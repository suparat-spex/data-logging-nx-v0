"""
ml_log.py - AI / ML data log: unattended, continuous raw logging for anomaly detection and
model training. Unlike a Record session (one folder per run, files grow until Stop) it is
meant to run 24/7 and cuts the stream into short files:

  <root>/
    2026-10-23/
      pump01_vib/
        2026-10-23_103200.wpk.zst      one file per period, aligned to the clock
        2026-10-23_103300.wpk.zst
      events.csv                       alarms + markers + log start / stop of the day
      meta.json                        sensors, units, scaling, sample rate
    2026-10-24/ ...

Rules (the AI app relies on them):
  * Files start on the clock in local time: a 1 min file starts at hh:mm:00, a 1 h file at
    hh:00:00, so file names line up across devices.
  * No gap, no overlap: each packet goes into exactly one file, chosen by the time of its
    first sample (t0). Packets are stored whole (lossless .wpk, read with
    recording.read_wpk), so the last packet of a file may run a few ms past the period end.
  * A file is written plain (.wpk) and compressed after it closes, in a background thread:
    a crash or power cut can only hit the file being written, and that one stays readable.
  * keep_days > 0 deletes date folders older than that; 0 keeps everything.

No Qt. Calculated variables are not logged here (no raw packets) - the AI app can
recompute them from meta.json + the raw data.
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import os
import queue
import re
import shutil
import threading
import time

from nxlogger.core import recording as dl
from nxlogger.core import wavepacket as wp

FORMAT_VERSION = 1
PERIODS = [("10 s", 10), ("30 s", 30), ("1 min", 60), ("5 min", 300), ("10 min", 600), ("1 h", 3600)]
EVENT_COLS = ["time_local", "time_unix_s", "type", "source", "text"]
IDLE_CLOSE_S = 5.0          # close a file this long after its period ended and data stopped
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def new_settings() -> dict:
    """Project.ml_log defaults. folder "" = <recordings folder>/AI_Data."""
    return {"folder": "", "period_s": 60, "compression": "zstd", "keep_days": 0, "autostart": False}


def period_start(t: float, period: float) -> float:
    """Start of the clock-aligned period holding t, in local time (+05:30 zones too)."""
    off = _dt.datetime.fromtimestamp(t).astimezone().utcoffset().total_seconds()
    return (t + off) // period * period - off


class _File:
    __slots__ = ("sink", "path", "start", "end", "last")

    def __init__(self, sink, path, start, end):
        self.sink, self.path, self.start, self.end = sink, path, start, end
        self.last = time.monotonic()


class MlLogger:
    """
    Call put() from the GUI thread for every decoded packet and event() for alarms /
    markers; all disk work happens in two background threads (writer, compressor).

    project : the project dict (name, topics with channel scaling -> meta.json)
    topics  : [{"id", "name", "topic", "channels": [(index, name, unit), ...]}] - real topics
    """

    def __init__(self, root: str, project: dict, topics: list[dict], period_s: float = 60,
                 compression: str = "zstd", keep_days: int = 0):
        if compression == "zstd" and not dl.zstd_available():
            compression = "gzip"                 # Python < 3.14 without the zstandard package
        self.root, self.project = root, project
        self.period = max(float(period_s), 1.0)
        self.compression = compression if compression in dl.COMPRESSIONS else "none"
        self.keep_days = max(int(keep_days), 0)
        self.topics: dict[str, dict] = {}
        used = set()
        for tp in topics:                        # one sub-folder per topic, unique names
            folder = dl.safe_name(tp["name"])
            while folder.lower() in used:
                folder += "_"
            used.add(folder.lower())
            self.topics[tp["id"]] = {**tp, "folder": folder}
        self.t_start = time.time()
        self.q: "queue.Queue" = queue.Queue(maxsize=20000)
        self.cq: "queue.Queue" = queue.Queue()
        self.packets = self.dropped = self.late = self.files = 0
        self.bytes_done = self.bytes_open = 0
        self.error: str | None = None
        self.last_file = ""
        self._open: dict[str, _File] = {}
        self._days: set[str] = set()
        self._events = None                      # (day, file, csv writer)
        self._thread = threading.Thread(target=self._run, name="ml-log", daemon=True)
        self._cthread = threading.Thread(target=self._compress_loop, name="ml-compress", daemon=True)

    # ------------------------------------------------------------ control
    def start(self):
        os.makedirs(self.root, exist_ok=True)
        self._recover()
        self._retention()
        self._cthread.start()
        self._thread.start()
        self.event("log_start", "AI/ML data log",
                   f"period {self.period:g} s, compression {self.compression}, "
                   f"{len(self.topics)} topic(s)", self.t_start)

    def put(self, topic_id: str, rx_time: float, payload: bytes, blk: wp.WaveBlock):
        if topic_id not in self.topics or not payload:
            return
        try:
            self.q.put_nowait(("p", topic_id, rx_time, payload, blk))
        except queue.Full:
            self.dropped += 1

    def event(self, etype: str, source: str = "", text: str = "", t: float | None = None):
        try:
            self.q.put_nowait(("e", time.time() if t is None else t, etype, source, text))
        except queue.Full:
            self.dropped += 1

    def stop(self):
        self.event("log_stop", "AI/ML data log",
                   f"{self.packets} packets, {self.dropped} dropped" + (f", error: {self.error}" if self.error else ""))
        self.q.put(None)
        self._thread.join(timeout=30)
        self.cq.put(None)
        self._cthread.join(timeout=120)          # finish compressing the last files

    # ------------------------------------------------------------ stats
    @property
    def elapsed(self) -> float:
        return time.time() - self.t_start

    @property
    def disk_bytes(self) -> int:
        return self.bytes_done + self.bytes_open

    # ------------------------------------------------------------ writer thread
    def _run(self):
        last_tick = time.monotonic()
        while True:
            try:
                item = self.q.get(timeout=0.5)
            except queue.Empty:
                item = False
            if item is None:
                break
            try:
                if item and item[0] == "p":
                    self._write(*item[1:])
                elif item:
                    self._write_event(*item[1:])
                if time.monotonic() - last_tick > 1.0:
                    last_tick = time.monotonic()
                    self._tick()
            except Exception as e:               # disk full etc. - report, keep running
                self.error = str(e)
        for tid in list(self._open):
            self._close(tid)
        if self._events:
            self._events[1].close()

    def _write(self, tid: str, rx: float, payload: bytes, blk):
        f = self._open.get(tid)
        if f is None or blk.t0 >= f.end:         # first packet or next period: new file
            if f is not None:
                self._close(tid)
            start = period_start(blk.t0, self.period)
            f = self._open[tid] = self._new_file(tid, start)
        elif blk.t0 < f.start:                   # out-of-order packet from an earlier period:
            self.late += 1                       # kept in the open file (closed files never reopen)
        f.sink.write(rx, payload, blk)
        f.last = time.monotonic()
        self.packets += 1
        self.bytes_open = sum(x.sink.f.tell() for x in self._open.values())

    def _new_file(self, tid: str, start: float) -> _File:
        tp = self.topics[tid]
        d = _dt.datetime.fromtimestamp(start)
        day_dir = os.path.join(self.root, d.strftime("%Y-%m-%d"))
        if day_dir not in self._days:
            self._new_day(day_dir)
        tdir = os.path.join(day_dir, tp["folder"])
        os.makedirs(tdir, exist_ok=True)
        base, n = d.strftime("%Y-%m-%d_%H%M%S"), 0
        ext = dl.COMPRESSIONS[self.compression][1]
        while True:                              # restart in the same period / DST hour: _1, _2 ...
            path = os.path.join(tdir, f"{base}{'_' + str(n) if n else ''}.wpk")
            if not (os.path.exists(path) or os.path.exists(path + ext)):
                break
            n += 1
        sink = dl.WpkSink(path, tp, tp["channels"], start, self.project.get("name", ""))
        return _File(sink, path, start, start + self.period)

    def _close(self, tid: str):
        f = self._open.pop(tid)
        f.sink.close()
        self.files += 1
        self.last_file = f.path
        self.bytes_open = sum(x.sink.f.tell() for x in self._open.values())
        if self.compression == "none":
            self.bytes_done += os.path.getsize(f.path)
        else:
            self.cq.put(f.path)

    def _tick(self):
        """Once a second: flush, and close files whose period is over when data stopped
        (otherwise a silent device would keep its file open)."""
        now, mono = time.time(), time.monotonic()
        for tid, f in list(self._open.items()):
            if now > f.end + IDLE_CLOSE_S and mono - f.last > IDLE_CLOSE_S:
                self._close(tid)
            else:
                f.sink.flush()

    def _new_day(self, day_dir: str):
        os.makedirs(day_dir, exist_ok=True)
        self._days.add(day_dir)
        self._write_meta(day_dir)
        self._retention()

    def _write_meta(self, day_dir: str):
        """meta.json: everything needed to read and scale the day's data without the project."""
        cfg = {t.get("id"): t for t in self.project.get("topics", [])}
        meta = {
            "format_version": FORMAT_VERSION,
            "app": "NX Logger", "project": self.project.get("name", ""),
            "written": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "utc_offset": _dt.datetime.now().astimezone().strftime("%z"),
            "period_s": self.period, "compression": self.compression,
            "files": "<topic folder>/<YYYY-MM-DD_HHMMSS>.wpk[.zst|.gz], local time of the period "
                     "start; a file holds the packets whose first sample is in [start, start + period)",
            "packet_format": "wavepacket v1 - read with nxlogger.core.recording.read_wpk; values are "
                             "the device's engineering units before the channel scale below",
            "events": "events.csv: " + ", ".join(EVENT_COLS),
            "topics": [{"folder": tp["folder"], "name": tp["name"], "mqtt_topic": tp["topic"],
                        "channels": [{"index": i, "name": n, "unit": u,
                                      **{k: c.get(k) for k in ("scale", "description")
                                         for c in cfg.get(tid, {}).get("channels", []) if c.get("index") == i}}
                                     for i, n, u in tp["channels"]]}
                       for tid, tp in self.topics.items()],
        }
        tmp = os.path.join(day_dir, "meta.json.part")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        os.replace(tmp, os.path.join(day_dir, "meta.json"))

    def _write_event(self, t: float, etype: str, source: str, text: str):
        d = _dt.datetime.fromtimestamp(t)
        day = d.strftime("%Y-%m-%d")
        if self._events is None or self._events[0] != day:
            if self._events:
                self._events[1].close()
            day_dir = os.path.join(self.root, day)
            if day_dir not in self._days:
                self._new_day(day_dir)
            path = os.path.join(day_dir, "events.csv")
            new = not os.path.exists(path)
            fh = open(path, "a", encoding="utf-8-sig" if new else "utf-8", newline="")
            w = csv.writer(fh)
            if new:
                w.writerow(EVENT_COLS)
            self._events = (day, fh, w)
        self._events[2].writerow([d.isoformat(timespec="milliseconds"), f"{t:.3f}", etype, source, text])
        self._events[1].flush()                  # events are rare and precious

    # ------------------------------------------------------------ housekeeping
    def _recover(self):
        """Plain .wpk files left by a crash in the last two days: compress them now."""
        if self.compression == "none":
            return
        days = sorted(e.path for e in os.scandir(self.root) if e.is_dir() and _DAY_RE.match(e.name))[-2:]
        for day in days:
            for dirpath, _, names in os.walk(day):
                for n in names:
                    p = os.path.join(dirpath, n)
                    if n.endswith(".part"):
                        os.remove(p)             # half-written compressed file; the .wpk is still there
                    elif n.endswith(".wpk"):
                        self.cq.put(p)

    def _retention(self):
        if not self.keep_days:
            return
        cutoff = (_dt.date.today() - _dt.timedelta(days=self.keep_days)).isoformat()
        for e in os.scandir(self.root):
            if e.is_dir() and _DAY_RE.match(e.name) and e.name < cutoff:
                shutil.rmtree(e.path, ignore_errors=True)
                self._days.discard(e.path)

    def _compress_loop(self):
        while (path := self.cq.get()) is not None:
            try:
                if os.path.exists(path):
                    self.bytes_done += os.path.getsize(dl.compress_file(path, self.compression))
            except Exception as e:
                self.error = f"compress {os.path.basename(path)}: {e}"
