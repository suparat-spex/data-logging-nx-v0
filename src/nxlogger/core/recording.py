"""
daq_logging.py - write live waveform blocks to disk, and read them back.

Formats (one recording session = one folder with project.json + data files):
  CSV   one file per topic: time_unix_s, t_rel_s, <channel [unit]>...  (Excel friendly)
  TDMS  one file for the session, one group per topic, one channel per DAQ channel
        plus a "Time" channel (NI DIAdem / LabVIEW / Excel TDM add-in, npTDMS)
  WPK   custom lossless format: the raw MQTT packets exactly as received, with a
        JSON header (topic, channel names/units). Smallest, and re-decodable with
        wavepacket.decode(). Read with read_wpk().

Logging profiles (NX Logger): one Record run can feed several profiles at once, each
with its own channels and format, written to the same session folder:
  signal     the waveform itself (CSV / TDMS / WPK as above)
  statistic  one row per interval (e.g. every 1 s, 1 min, 1 h) with the chosen
             statistics per channel - mean, min, max, SD, RMS, P-P, sum, inst
             (last value) - like the hourly / daily report data of a paperless
             recorder. Much smaller than the waveform, good for long-term trends.

Recorder runs the writers in a background thread so disk I/O never blocks the GUI.

Nothing here depends on Qt, so the future viewer / analysis / ML code can import it.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import io
import json
import math
import os
import queue
import re
import shutil
import struct
import threading
import time
from typing import Iterator

import numpy as np

from nxlogger.core import wavepacket as wp


FORMATS = {   # key: (label, extension)
    "CSV": ("CSV - one file per topic (Excel, pandas)", ".csv"),
    "TDMS": ("TDMS - one file, a group per topic (NI DIAdem, LabVIEW, npTDMS)", ".tdms"),
    "WPK": ("Raw packets .wpk - lossless custom format", ".wpk"),
}


STAT_FORMATS = {"CSV": FORMATS["CSV"], "TDMS": FORMATS["TDMS"]}
STAT_KEYS = ["mean", "min", "max", "sd", "rms", "pp", "sum", "inst"]
STAT_LABELS = {"mean": "Mean", "min": "Min", "max": "Max", "sd": "SD", "rms": "RMS",
               "pp": "P-P", "sum": "Sum", "inst": "Inst"}


def safe_name(s: str) -> str:
    return re.sub(r"[^\w\-]+", "_", s.strip()).strip("_") or "untitled"


# ====================================================================== compression
COMPRESSIONS = {   # key: (label, extension) - lossless only, ML needs every sample
    "zstd": ("zstd - small and fast (recommended)", ".zst"),
    "gzip": ("gzip - opens everywhere", ".gz"),
    "none": ("None - plain .wpk", ""),
}


def _zstd():
    """zstd module: stdlib compression.zstd (Python 3.14+) or the zstandard package, else None.
    Both have open(path, mode) returning a file object."""
    try:
        from compression import zstd
        return zstd
    except ImportError:
        pass
    try:
        import zstandard
        return zstandard
    except ImportError:
        return None


def zstd_available() -> bool:
    return _zstd() is not None


def open_any(path: str):
    """Open a data file for binary reading - plain, .gz or .zst."""
    if path.endswith(".gz"):
        return gzip.open(path, "rb")
    if path.endswith(".zst"):
        z = _zstd()
        if z is None:
            raise ValueError(f"{path}: reading .zst needs Python 3.14+ or 'pip install zstandard'")
        return io.BufferedReader(z.open(path, "rb"))   # read(n) returns n bytes (zstandard may not)
    return open(path, "rb")


def compress_file(src: str, method: str) -> str:
    """Compress src next to itself (src + .zst / .gz), then delete src. Returns the new path.
    Written to a .part file first, so a crash never leaves a broken file under the real name."""
    ext = COMPRESSIONS[method][1]
    if not ext:
        return src
    dst = src + ext
    opener = gzip.open if method == "gzip" else _zstd().open
    with open(src, "rb") as fi, opener(dst + ".part", "wb") as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
    os.replace(dst + ".part", dst)
    os.remove(src)
    return dst


# ====================================================================== TDMS
# Minimal TDMS 2.0 writer (little-endian, non-interleaved, float64 data).
# Spec: https://www.ni.com/en/support/documentation/supplemental/07/tdms-file-format-internal-structure.html
_TOC_META, _TOC_NEW_OBJ, _TOC_RAW = 1 << 1, 1 << 2, 1 << 3
_T_I32, _T_DOUBLE, _T_STRING, _T_TIME = 0x03, 0x0A, 0x20, 0x44
_EPOCH_1904 = 2082844800          # seconds from 1904-01-01 to 1970-01-01 (UTC)


def _tstr(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<I", len(b)) + b


def _tpath(*names: str) -> str:
    return "/" + "/".join("'" + n.replace("'", "''") + "'" for n in names) if names else "/"


class TdmsTime(float):
    """Marks a float (Unix seconds) to be stored as a TDMS timestamp property."""


def _tprop(v) -> bytes:
    if isinstance(v, TdmsTime):
        secs = math.floor(v)
        frac = int((v - secs) * 2 ** 64) & 0xFFFFFFFFFFFFFFFF
        return struct.pack("<IQq", _T_TIME, frac, secs + _EPOCH_1904)
    if isinstance(v, bool) or isinstance(v, int):
        return struct.pack("<Ii", _T_I32, int(v))
    if isinstance(v, float):
        return struct.pack("<Id", _T_DOUBLE, v)
    return struct.pack("<I", _T_STRING) + _tstr(str(v))


class TdmsFile:
    def __init__(self, path: str, props: dict):
        self.f = open(path, "wb")
        self._segment([(_tpath(), None, props)])

    def _segment(self, objects):
        """objects: [(path, float64 array or None, {prop: value})]"""
        meta = [struct.pack("<I", len(objects))]
        raw = []
        for path, data, props in objects:
            meta.append(_tstr(path))
            if data is None:
                meta.append(struct.pack("<I", 0xFFFFFFFF))
            else:
                meta.append(struct.pack("<IIIQ", 20, _T_DOUBLE, 1, len(data)))
                raw.append(np.ascontiguousarray(data, dtype="<f8").tobytes())
            meta.append(struct.pack("<I", len(props)))
            for k, v in props.items():
                meta.append(_tstr(k) + _tprop(v))
        meta_b, raw_b = b"".join(meta), b"".join(raw)
        toc = _TOC_META | _TOC_NEW_OBJ | (_TOC_RAW if raw_b else 0)
        self.f.write(b"TDSm" + struct.pack("<IIQQ", toc, 4713,
                                           len(meta_b) + len(raw_b), len(meta_b)))
        self.f.write(meta_b)
        self.f.write(raw_b)

    def write(self, group: str, group_props: dict, channels: list[tuple[str, dict]],
              t: np.ndarray, y: np.ndarray):
        """Append one segment: group -> 'Time' + one data channel per row of y.
        Pass props only the first time (later segments with props would overwrite)."""
        objs = [(_tpath(group), None, group_props),
                (_tpath(group, channels[0][0]), t, channels[0][1])]     # Time channel
        for (name, props), row in zip(channels[1:], y):
            objs.append((_tpath(group, name), row, props))
        self._segment(objs)

    def close(self):
        self.f.close()


# ====================================================================== sinks
class _Sink:
    """Writer for one topic. `channels` = [(index, name, unit)] of the enabled channels."""

    def __init__(self, topic: dict, channels: list[tuple[int, str, str]], t_start: float):
        self.topic, self.channels, self.t_start = topic, channels, t_start
        self.samples = 0

    def select(self, blk: wp.WaveBlock) -> np.ndarray:
        """Rows for the enabled channels, NaN where a channel is missing in the block."""
        out = np.full((len(self.channels), blk.n), np.nan)
        for r, (idx, _, _) in enumerate(self.channels):
            if idx in blk.channels:
                out[r] = blk.y[blk.channels.index(idx)]
        return out

    def write(self, rx_time: float, payload: bytes, blk: wp.WaveBlock): ...
    def flush(self): ...
    def close(self): ...


class CsvSink(_Sink):
    def __init__(self, path, topic, channels, t_start, project):
        super().__init__(topic, channels, t_start)
        self.f = open(path, "w", encoding="utf-8-sig", newline="")   # BOM: Excel shows °C
        start = _dt.datetime.fromtimestamp(t_start).isoformat(timespec="seconds")
        self.f.write(f"# project: {project}\n# topic: {topic['name']} ({topic['topic']})\n"
                     f"# start: {start}\n")
        cols = ["time_unix_s", "t_rel_s"] + [f"{n} [{u}]" if u else n for _, n, u in channels]
        self.f.write(",".join(c.replace(",", ";") for c in cols) + "\n")
        self.fmt = ["%.6f", "%.6f"] + ["%.7g"] * len(channels)
        self.t_first = None                     # t_rel_s = 0 at the first logged sample

    def write(self, rx_time, payload, blk):
        if self.t_first is None:
            self.t_first = blk.t0
        t = blk.t0 + np.arange(blk.n) * blk.dt
        np.savetxt(self.f, np.column_stack([t, t - self.t_first, self.select(blk).T]),
                   delimiter=",", fmt=self.fmt)
        self.samples += blk.n

    def flush(self):
        self.f.flush()

    def close(self):
        self.f.close()


class TdmsSink(_Sink):
    """Buffers ~1 s per topic so the TDMS file doesn't get one segment per packet."""

    def __init__(self, tdms: TdmsFile, topic, channels, t_start):
        super().__init__(topic, channels, t_start)
        self.tdms, self.buf_t, self.buf_y, self.first = tdms, [], [], True
        self.wf_start = self.dt = None

    def write(self, rx_time, payload, blk):
        if self.wf_start is None:
            self.wf_start, self.dt = blk.t0, blk.dt
        self.buf_t.append(blk.t0 + np.arange(blk.n) * blk.dt)
        self.buf_y.append(self.select(blk))
        self.samples += blk.n
        if sum(len(b) for b in self.buf_t) * self.dt >= 1.0:
            self.flush()

    def flush(self):
        if not self.buf_t:
            return
        t, y = np.concatenate(self.buf_t), np.hstack(self.buf_y)
        self.buf_t, self.buf_y = [], []
        if self.first:
            gprops = {"mqtt_topic": self.topic["topic"], "sample_rate_hz": 1.0 / self.dt}
            chans = [("Time", {"unit_string": "s", "description": "Unix time"})]
            chans += [(n, {"unit_string": u, "NI_ChannelName": n, "channel_index": i + 1,
                           "wf_start_time": TdmsTime(self.wf_start),
                           "wf_increment": float(self.dt), "wf_start_offset": 0.0})
                      for i, n, u in self.channels]
        else:
            gprops = {}
            chans = [("Time", {})] + [(n, {}) for _, n, _ in self.channels]
        self.tdms.write(self.topic["name"], gprops, chans, t, y)
        self.tdms.f.flush()
        self.first = False

    def close(self):
        self.flush()


# ====================================================================== statistic sinks
class _Acc:
    """Running statistics of one channel over one interval (finite samples only)."""
    __slots__ = ("n", "s", "ss", "mn", "mx", "last")

    def __init__(self):
        self.n, self.s, self.ss = 0, 0.0, 0.0
        self.mn, self.mx, self.last = math.inf, -math.inf, math.nan

    def add(self, y: np.ndarray):
        y = y[np.isfinite(y)]
        if y.size:
            self.n += y.size
            self.s += float(y.sum()); self.ss += float(np.dot(y, y))
            self.mn, self.mx = min(self.mn, float(y.min())), max(self.mx, float(y.max()))
            self.last = float(y[-1])

    def result(self, keys) -> list[float]:
        if not self.n:
            return [math.nan] * len(keys)
        mean = self.s / self.n
        out = {"mean": mean, "min": self.mn, "max": self.mx, "pp": self.mx - self.mn,
               "sd": math.sqrt(max(self.ss / self.n - mean * mean, 0.0)),
               "rms": math.sqrt(self.ss / self.n), "sum": self.s, "inst": self.last}
        return [out[k] for k in keys]


class _StatSink(_Sink):
    """Cuts the stream into intervals aligned to the clock (a 1 min interval starts at
    hh:mm:00) and emits one row of statistics per interval."""

    def __init__(self, topic, channels, t_start, interval: float, keys: list[str]):
        super().__init__(topic, channels, t_start)
        self.interval = max(float(interval), 0.1)
        self.keys = [k for k in STAT_KEYS if k in keys] or ["mean"]
        self.bucket = None
        self._reset()

    def _reset(self):
        self.accs = [_Acc() for _ in self.channels]
        self.nsamp = 0

    def write(self, rx_time, payload, blk):
        t = blk.t0 + np.arange(blk.n) * blk.dt
        b = np.floor(t / self.interval)
        y = self.select(blk)
        for bk in np.unique(b):                  # usually one bucket per block
            m = b == bk
            if self.bucket is not None and bk != self.bucket:
                self._emit()
            self.bucket = bk
            for r, acc in enumerate(self.accs):
                acc.add(y[r, m])
            self.nsamp += int(m.sum())
        self.samples += blk.n

    def _emit(self):
        if self.bucket is None or not self.nsamp:
            return
        vals = [v for acc in self.accs for v in acc.result(self.keys)]
        self.emit_row(self.bucket * self.interval, self.nsamp, vals)
        self._reset()

    def col_names(self) -> list[tuple[str, str]]:
        """[(column name, unit)] for the statistic columns."""
        return [(f"{n} {STAT_LABELS[k]}", "" if k == "sum" and u else u)
                for _, n, u in self.channels for k in self.keys]

    def emit_row(self, t0: float, n: int, vals: list[float]): ...


class StatCsvSink(_StatSink):
    def __init__(self, path, topic, channels, t_start, project, interval, keys):
        super().__init__(topic, channels, t_start, interval, keys)
        self.f = open(path, "w", encoding="utf-8-sig", newline="")
        start = _dt.datetime.fromtimestamp(t_start).isoformat(timespec="seconds")
        self.f.write(f"# project: {project}\n# topic: {topic['name']} ({topic['topic']})\n"
                     f"# start: {start}\n# statistics every {self.interval:g} s: "
                     f"{', '.join(STAT_LABELS[k] for k in self.keys)}\n")
        cols = ["interval_start", "time_unix_s", "samples"] + \
               [f"{n} [{u}]" if u else n for n, u in self.col_names()]
        self.f.write(",".join(c.replace(",", ";") for c in cols) + "\n")

    def emit_row(self, t0, n, vals):
        stamp = _dt.datetime.fromtimestamp(t0).isoformat(timespec="milliseconds")
        self.f.write(f"{stamp},{t0:.3f},{n}," + ",".join("" if math.isnan(v) else f"{v:.7g}"
                                                      for v in vals) + "\n")

    def flush(self):
        self.f.flush()

    def close(self):
        self._emit()                             # last (partial) interval
        self.f.close()


class StatTdmsSink(_StatSink):
    """Group per topic: Time (interval start), Samples, then '<channel> <stat>'."""

    def __init__(self, tdms: TdmsFile, topic, channels, t_start, interval, keys):
        super().__init__(topic, channels, t_start, interval, keys)
        self.tdms, self.rows, self.first = tdms, [], True

    def emit_row(self, t0, n, vals):
        self.rows.append([t0, float(n)] + vals)

    def flush(self):
        if not self.rows:
            return
        a = np.array(self.rows, dtype=float).T
        self.rows = []
        if self.first:
            gprops = {"mqtt_topic": self.topic["topic"], "interval_s": self.interval,
                      "statistics": ", ".join(STAT_LABELS[k] for k in self.keys)}
            chans = [("Time", {"unit_string": "s", "description": "Interval start, Unix time"}),
                     ("Samples", {"description": "samples in the interval"})]
            chans += [(n, {"unit_string": u}) for n, u in self.col_names()]
        else:
            gprops = {}
            chans = [("Time", {}), ("Samples", {})] + [(n, {}) for n, _ in self.col_names()]
        self.tdms.write(self.topic["name"], gprops, chans, a[0], a[1:])
        self.tdms.f.flush()
        self.first = False

    def close(self):
        self._emit()
        self.flush()


WPK_MAGIC = b"WPKLOG\x00\x01"
_WPK_REC = struct.Struct("<dI")   # rx_time (Unix s), payload length


class WpkSink(_Sink):
    def __init__(self, path, topic, channels, t_start, project):
        super().__init__(topic, channels, t_start)
        self.f = open(path, "wb")
        meta = {"project": project, "topic": topic, "t_start": t_start,
                "channels": [{"index": i, "name": n, "unit": u} for i, n, u in channels],
                "packet_format": "wavepacket v1 (see wavepacket.py)"}
        mb = json.dumps(meta, ensure_ascii=False).encode()
        self.f.write(WPK_MAGIC + struct.pack("<I", len(mb)) + mb)

    def write(self, rx_time, payload, blk):
        self.f.write(_WPK_REC.pack(rx_time, len(payload)) + payload)
        self.samples += blk.n

    def flush(self):
        self.f.flush()

    def close(self):
        self.f.close()


# ====================================================================== readers
def iter_wpk(path: str) -> tuple[dict, Iterator[tuple[float, wp.WaveBlock]]]:
    """Return (header dict, iterator of (rx_time, WaveBlock)). Also reads .wpk.zst / .wpk.gz."""
    f = open_any(path)
    if f.read(len(WPK_MAGIC)) != WPK_MAGIC:
        f.close()
        raise ValueError(f"{path}: not a .wpk file")
    (n,) = struct.unpack("<I", f.read(4))
    meta = json.loads(f.read(n))

    def gen():
        with f:
            while hdr := f.read(_WPK_REC.size):
                if len(hdr) < _WPK_REC.size:
                    return
                rx, ln = _WPK_REC.unpack(hdr)
                buf = f.read(ln)
                if len(buf) < ln:
                    return                       # truncated last record (crash)
                try:
                    yield rx, wp.decode(buf)
                except wp.PacketError:
                    continue
    return meta, gen()


def read_wpk(path: str) -> tuple[dict, np.ndarray, dict[str, np.ndarray]]:
    """Load a whole .wpk: (header, time array [Unix s], {channel name: values})."""
    meta, it = iter_wpk(path)
    ts, cols = [], {c["name"]: [] for c in meta["channels"]}
    for _, blk in it:
        ts.append(blk.t0 + np.arange(blk.n) * blk.dt)
        for c in meta["channels"]:
            i = c["index"]
            cols[c["name"]].append(blk.y[blk.channels.index(i)] if i in blk.channels
                                   else np.full(blk.n, np.nan))
    cat = lambda a: np.concatenate(a) if a else np.empty(0)
    return meta, cat(ts), {k: cat(v) for k, v in cols.items()}


# ====================================================================== recorder
class Recorder:
    """
    One recording session. Call put() from the GUI thread for every decoded block;
    the writes happen in a background thread.

    project : dict (the project config, saved as project.json in the session folder)
    topics  : [{"id", "name", "topic", "channels": [(index, name, unit), ...]}]
    profiles: optional list of logging profiles, each
              {"name", "type": "signal" | "statistic", "format", "keep_raw",
               "interval_s", "stats": [STAT_KEYS], "topics": [as above]}
              Without profiles, topics / fmt / keep_raw make one unnamed signal profile
              (the v5 behaviour and file names).
    """

    def __init__(self, folder: str, project: dict, topics: list[dict] | None = None,
                 fmt: str = "CSV", keep_raw: bool = False, profiles: list[dict] | None = None):
        if profiles is None:
            profiles = [{"name": "", "type": "signal", "format": fmt, "keep_raw": keep_raw,
                         "topics": topics or []}]
        self.profiles = profiles
        merged: dict[str, dict] = {}             # union of topics / channels (session info)
        for pr in profiles:
            for tp in pr["topics"]:
                m = merged.setdefault(tp["id"], {**tp, "channels": []})
                have = {c[0] for c in m["channels"]}
                m["channels"] += [c for c in tp["channels"] if c[0] not in have]
        topics = list(merged.values())
        self.project, self.topics, self.fmt, self.keep_raw = project, topics, fmt, keep_raw
        self.t_start = time.time()
        stamp = _dt.datetime.fromtimestamp(self.t_start).strftime("%Y%m%d_%H%M%S")
        self.dir = os.path.join(folder, f"{safe_name(project['name'])}_{stamp}")
        self.q: "queue.Queue" = queue.Queue(maxsize=20000)
        self.dropped = self.packets = 0
        self.error: str | None = None
        self._sinks: dict[str, list[_Sink]] = {}
        self._tdms: list[TdmsFile] = []
        self._thread = threading.Thread(target=self._run, name="recorder", daemon=True)

    # ------------------------------------------------------------ control
    def start(self):
        os.makedirs(self.dir, exist_ok=False)
        self._write_session_json(running=True)
        name = self.project["name"]
        used = set()
        for pr in self.profiles:
            pname = safe_name(pr["name"]) if pr.get("name") else ""
            fmt, stat = pr.get("format", "CSV"), pr.get("type") == "statistic"
            tdms = None
            if fmt == "TDMS":
                tdms = TdmsFile(os.path.join(self.dir, f"{safe_name(name)}{'_' + pname if pname else ''}.tdms"),
                                {"name": name, "description": self.project.get("description", ""),
                                 "author": self.project.get("operator", ""),
                                 "datetime": TdmsTime(self.t_start),
                                 "logging_profile": pr.get("name", ""),
                                 "profile_type": pr.get("type", "signal")})
                self._tdms.append(tdms)
            for tp in pr["topics"]:
                base = (pname + "_" if pname else "") + safe_name(tp["name"])
                while base in used:
                    base += "_"
                used.add(base)
                sinks = self._sinks.setdefault(tp["id"], [])
                p = lambda ext: os.path.join(self.dir, base + ext)
                if stat:
                    iv, keys = pr.get("interval_s", 1.0), pr.get("stats", ["mean"])
                    sinks.append(StatCsvSink(p(".csv"), tp, tp["channels"], self.t_start, name, iv, keys)
                                 if fmt == "CSV" else
                                 StatTdmsSink(tdms, tp, tp["channels"], self.t_start, iv, keys))
                    continue
                virtual = tp.get("virtual", False)      # calculated variables: no raw packets
                if fmt == "CSV" or (fmt == "WPK" and virtual):
                    sinks.append(CsvSink(p(".csv"), tp, tp["channels"], self.t_start, name))
                elif fmt == "TDMS":
                    sinks.append(TdmsSink(tdms, tp, tp["channels"], self.t_start))
                if (fmt == "WPK" or pr.get("keep_raw")) and not virtual:
                    sinks.append(WpkSink(p(".wpk"), tp, tp["channels"], self.t_start, name))
        self._thread.start()

    def put(self, topic_id: str, rx_time: float, payload: bytes, blk: wp.WaveBlock):
        if topic_id not in self._sinks:
            return
        try:
            self.q.put_nowait((topic_id, rx_time, payload, blk))
        except queue.Full:
            self.dropped += 1

    def stop(self):
        self.q.put(None)
        self._thread.join(timeout=30)
        for sinks in self._sinks.values():
            for s in sinks:
                try:
                    s.close()
                except Exception as e:  # keep closing the others
                    self.error = self.error or str(e)
        for f in self._tdms:
            f.close()
        self._write_session_json(running=False)

    # ------------------------------------------------------------ stats
    @property
    def elapsed(self) -> float:
        return time.time() - self.t_start

    @property
    def samples(self) -> int:
        return max((s.samples for sinks in self._sinks.values() for s in sinks), default=0)

    def disk_bytes(self) -> int:
        try:
            return sum(e.stat().st_size for e in os.scandir(self.dir) if e.is_file())
        except OSError:
            return 0

    # ------------------------------------------------------------ internals
    def _run(self):
        last_flush = time.monotonic()
        while True:
            try:
                item = self.q.get(timeout=0.5)
            except queue.Empty:
                item = False
            if item is None:
                return
            if item:
                tid, rx, payload, blk = item
                try:
                    for s in self._sinks[tid]:
                        s.write(rx, payload, blk)
                    self.packets += 1
                except Exception as e:      # disk full etc. - report, keep running
                    self.error = str(e)
            if time.monotonic() - last_flush > 1.0:
                last_flush = time.monotonic()
                for sinks in self._sinks.values():
                    for s in sinks:
                        try:
                            s.flush()
                        except Exception as e:
                            self.error = str(e)

    def _write_session_json(self, running: bool):
        info = {"project": self.project, "format": self.fmt, "keep_raw": self.keep_raw,
                "profiles": [{k: v for k, v in p.items() if k != "topics"} |
                             {"topics": [t["name"] for t in p["topics"]]} for p in self.profiles],
                "start": _dt.datetime.fromtimestamp(self.t_start).isoformat(),
                "start_unix": self.t_start, "running": running,
                "topics": [{**t, "channels": [{"index": i, "name": n, "unit": u}
                                              for i, n, u in t["channels"]]}
                           for t in self.topics]}
        if not running:
            info.update(end=_dt.datetime.now().isoformat(), duration_s=self.elapsed,
                        packets=self.packets, samples_per_channel=self.samples,
                        dropped=self.dropped, error=self.error)
        with open(os.path.join(self.dir, "project.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2, ensure_ascii=False)
