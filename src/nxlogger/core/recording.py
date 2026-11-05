"""
recording.py - write live data blocks to disk, and read them back.

Formats (one recording session = one folder with project.json + data files; the layout
is documented for the NX Viewer in docs/recording-format.md):
  CSV   one file per device: time_unix_s, t_rel_s, <tag [unit]>...  (Excel friendly).
        Number tags: one row per scan. Array tags (waveforms): one row per sample.
  WPK   custom lossless format: the raw MQTT wavepackets exactly as received, with a
        JSON header (device, channel names/units). Smallest, and re-decodable with
        wavepacket.decode(). Read with read_wpk(). Only for wavepacket devices - number
        tags have no raw packets and fall back to CSV.

Logging profiles: one Record run can feed several profiles at once, each with its own
tags and format, written to the same session folder (types: core/log_types.py):
  interval   every value (CSV / WPK as above; "signal" in older files), or one row per
             `every_s` seconds (the first sample of each period)
  change     a row when a tag moves more than its deadband (absolute or % of the last
             written value), at least every heartbeat_s, at most every min_gap_s
  trigger    only while the window says so (Recorder.gate), with pre-trigger time (a buffer)
             and post-trigger time; a `run` column
  batch      like trigger (no pre / post) with a `batch` column, plus <file>_summary.csv:
             one row per batch with start, end, duration and the chosen statistics
  event      one file per profile: one row per Recorder.event() with the values the window
             passes (any tag category)
  statistic  one row per interval (e.g. every 1 s, 1 min, 1 h) with the chosen
             statistics per channel - mean, min, max, SD, RMS, P-P, sum, inst
             (last value) - like the hourly / daily report data of a paperless
             recorder. Much smaller than the waveform, good for long-term trends.

Where: each profile has its own folder ("" = the project's default), date folders (session /
year / month / day / custom pattern) and a new file per session / hour / day / week / month /
N MB / run (core/log_files.py, RotFile) - a file of the same name and columns is appended to.

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

from nxlogger.core import log_files as lf
from nxlogger.core import wavepacket as wp


FORMATS = {   # key: (label, extension)
    "CSV": ("CSV - one file per device (Excel, pandas, NX Viewer)", ".csv"),
    "WPK": ("Raw packets .wpk - lossless, waveform devices only", ".wpk"),
}


STAT_FORMATS = {"CSV": FORMATS["CSV"]}
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


# ====================================================================== files of a profile
def _same_columns(path: str, header: str) -> bool:
    """The file's column line is the header's (rows can be appended)."""
    want = [ln for ln in header.splitlines() if ln and not ln.startswith("#")]
    try:
        with open(path, encoding="utf-8-sig") as f:
            for ln in f:
                if ln.strip() and not ln.startswith("#"):
                    return bool(want) and ln.rstrip("\r\n") == want[0]
    except OSError:
        return False
    return False


class RotFile:
    """One output of a sink: the file of each period (core/log_files.py) - opened when the first
    row of a period comes, in the profile's folder and date folders, closed (and compressed) when
    the next period starts. header(t) -> the text written at the top of a new file (bytes when
    binary). A file of the same name with the same columns gets the rows appended; otherwise
    _2, _3 ... (and size rotation _001, _002 ...)."""

    def __init__(self, rec: "Recorder", pi: int, out: dict, kind: str, ctx: dict, ext: str, header,
                 binary: bool = False, suffix: str = "", by_time_only: bool = False):
        self.rec, self.pi, self.kind, self.ctx, self.ext = rec, pi, kind, ctx, ext
        self.out = dict(out)
        if by_time_only and self.out["new_file"] == "run":           # a batch summary: one file
            self.out["new_file"] = "session"
        self.header, self.binary, self.suffix = header, binary, suffix
        self.f = None
        self.key = None
        self.path = ""
        self.part = 0

    def key_of(self, t: float, run: str = ""):
        m = self.out["new_file"]
        if m in ("hour", "day", "week", "month"):
            return lf.period_start(self.out, t)
        return run if m == "run" else 0

    def get(self, t: float, run: str = ""):
        k = self.key_of(t, run)
        if self.f is None or k != self.key:
            self._open(t, k, run)
        self.rec.last_write[self.pi] = time.time()       # a row is written now (Last write / Stalled)
        return self.f

    def _open(self, t: float, k, run: str):
        self.close()
        self.key = k
        start = k if isinstance(k, float) and k else t
        ctx = {**self.ctx, "run": safe_name(run) if run else ""}        # "Run 1" -> Run_1
        d = self.rec.folder_of(self.pi, start, ctx)
        os.makedirs(d, exist_ok=True)
        name = lf.clean(lf.render(lf.name_pattern(self.out, self.kind)[0], start, ctx)) + self.suffix
        if self.part:
            name += f"_{self.part:03d}"
        head = self.header(start)
        path, n, append = os.path.join(d, name + self.ext), 1, False
        while True:
            busy = path in self.rec.open_paths
            if not busy and not os.path.exists(path):
                break
            if not busy and not self.binary and _same_columns(path, head):
                append = True
                break
            n += 1
            path = os.path.join(d, f"{name}_{n}{self.ext}")
        if self.binary:
            self.f = open(path, "ab" if append else "wb")
            if not append:
                self.f.write(head)
        else:
            self.f = open(path, "a" if append else "w", encoding="utf-8-sig", newline="")
            if not append:
                self.f.write(head)
        self.path = path
        self.rec.opened(self, path, d, start)

    def check_size(self):
        """Every N MB: the next row goes into _001, _002 ..."""
        if self.out["new_file"] == "size" and self.f is not None and \
                self.f.tell() > float(self.out.get("size_mb") or 100) * 1e6:
            self.part += 1
            self.key = None                              # the next get() opens the next part

    def flush(self):
        if self.f is not None:
            self.f.flush()

    def close(self):
        if self.f is not None:
            self.f.close()
            self.f = None
            self.rec.closed(self, self.path)


class FixedFile:
    """A sink's one file at a given path, opened now (the AI/ML log rotates by itself)."""

    def __init__(self, path: str, header, binary: bool = False):
        self.path = path
        self.f = open(path, "wb") if binary else open(path, "w", encoding="utf-8-sig", newline="")
        self.f.write(header(time.time()))

    def key_of(self, t, run=""):
        return 0

    def get(self, t, run=""):
        return self.f

    def check_size(self):
        pass

    def flush(self):
        self.f.flush()

    def close(self):
        if not self.f.closed:
            self.f.close()


def _maker_of(target):
    """A sink's file maker: the Recorder's (RotFile per period), or a path (one FixedFile)."""
    if callable(target):
        return target
    return lambda ext, header, binary=False, **_k: FixedFile(target, header, binary)


# ====================================================================== sinks
class _Sink:
    """Writer for one topic of one profile. `channels` = [(index, name, unit)] of the enabled channels."""

    def __init__(self, topic: dict, channels: list[tuple[int, str, str]], t_start: float):
        self.topic, self.channels, self.t_start = topic, channels, t_start
        self.samples = 0
        self.files: list[RotFile] = []

    def select(self, blk: wp.WaveBlock) -> np.ndarray:
        """Rows for the enabled channels, NaN where a channel is missing in the block."""
        out = np.full((len(self.channels), blk.n), np.nan)
        for r, (idx, _, _) in enumerate(self.channels):
            if idx in blk.channels:
                out[r] = blk.y[blk.channels.index(idx)]
        return out

    def _csv_head(self, project: str, extra: str = "") -> str:
        start = _dt.datetime.fromtimestamp(self.t_start).isoformat(timespec="seconds")
        return (f"# project: {project}\n# device: {self.topic['name']} ({self.topic['topic']})\n"
                f"# start: {start}\n" + (f"# {extra}\n" if extra else ""))

    @staticmethod
    def _cols(cols) -> str:
        return ",".join(c.replace(",", ";") for c in cols) + "\n"

    @property
    def f(self):
        """The file written now (the AI/ML log reads its size)."""
        return self.files[0].f if self.files else None

    def write(self, rx_time: float, payload: bytes, blk: wp.WaveBlock): ...

    def flush(self):
        for f in self.files:
            f.flush()

    def close(self):
        for f in self.files:
            f.close()


def every_mask(t: np.ndarray, every: float, last: float | None) -> tuple[np.ndarray, float | None]:
    """Interval logging: the first sample of each `every` period -> (mask, last period)."""
    if not every or every <= 0:
        return np.ones(t.size, dtype=bool), last
    b = np.floor(t / every)
    m = np.zeros(t.size, dtype=bool)
    for i, bk in enumerate(b):
        if bk != last:
            m[i] = True
            last = bk
    return m, last


def _segments(rf: RotFile, t: np.ndarray, run: str = ""):
    """Split sample times by the file period they belong to -> (mask, file) one at a time (a
    generator: each part is written before the next period's file opens and closes this one)."""
    if not t.size:
        return
    k0, k1 = rf.key_of(float(t[0]), run), rf.key_of(float(t[-1]), run)
    if k0 == k1:
        yield np.ones(t.size, dtype=bool), rf.get(float(t[0]), run)
        return
    keys = np.array([rf.key_of(float(x), run) for x in t])
    for k in dict.fromkeys(keys.tolist()):
        m = keys == k
        yield m, rf.get(float(t[m][0]), run)


class CsvSink(_Sink):
    def __init__(self, rf_maker, topic, channels, t_start, project, every_s: float = 0.0):
        super().__init__(topic, channels, t_start)
        cols = ["time_unix_s", "t_rel_s"] + [f"{n} [{u}]" if u else n for _, n, u in channels]
        head = self._csv_head(project) + self._cols(cols)
        self.rf = _maker_of(rf_maker)(".csv", lambda t: head)
        self.files = [self.rf]
        self.fmt = ["%.6f", "%.6f"] + ["%.7g"] * len(channels)
        self.every, self._period = float(every_s or 0), None
        self.t_first = None                     # t_rel_s = 0 at the first logged sample

    def write(self, rx_time, payload, blk):
        if self.t_first is None:
            self.t_first = blk.t0
        t = blk.t0 + np.arange(blk.n) * blk.dt
        y = self.select(blk)
        if self.every:
            m, self._period = every_mask(t, self.every, self._period)
            t, y = t[m], y[:, m]
            if not t.size:
                return
        for m, f in _segments(self.rf, t):
            np.savetxt(f, np.column_stack([t[m], t[m] - self.t_first, y[:, m].T]), delimiter=",", fmt=self.fmt)
        self.rf.check_size()
        self.samples += t.size


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
    def __init__(self, rf_maker, topic, channels, t_start, project, interval, keys):
        super().__init__(topic, channels, t_start, interval, keys)
        cols = ["interval_start", "time_unix_s", "samples"] + [f"{n} [{u}]" if u else n for n, u in self.col_names()]
        head = self._csv_head(project, f"statistics every {self.interval:g} s: "
                                       f"{', '.join(STAT_LABELS[k] for k in self.keys)}") + self._cols(cols)
        self.rf = rf_maker(".csv", lambda t: head)
        self.files = [self.rf]

    def emit_row(self, t0, n, vals):
        """t0 = the interval start: a 23:45 row of a daily file stays in its day."""
        stamp = _dt.datetime.fromtimestamp(t0).isoformat(timespec="milliseconds")
        self.rf.get(t0).write(f"{stamp},{t0:.3f},{n}," + ",".join("" if math.isnan(v) else f"{v:.7g}"
                                                                for v in vals) + "\n")
        self.rf.check_size()

    def close(self):
        self._emit()                             # last (partial) interval
        super().close()


# ====================================================================== on change, trigger, batch, event
def _cell(v: float) -> str:
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.7g}"


class _RowsCsv(_Sink):
    """A CSV of rows: time_unix_s, t_rel_s, [label column], the channels."""

    def __init__(self, rf_maker, topic, channels, t_start, project, label: str = "", note: str = ""):
        super().__init__(topic, channels, t_start)
        cols = ["time_unix_s", "t_rel_s"] + ([label] if label else []) + \
               [f"{n} [{u}]" if u else n for _, n, u in channels]
        head = self._csv_head(project, note) + self._cols(cols)
        self.rf = rf_maker(".csv", lambda t: head)
        self.files = [self.rf]
        self.label = label
        self.t_first = None

    def rows(self, t: np.ndarray, y: np.ndarray, tag: str = ""):
        """Write samples t (n) with values y (channels x n)."""
        if not t.size:
            return
        if self.t_first is None:
            self.t_first = float(t[0])
        lab = [tag.replace(",", ";")] if self.label else []
        for m, f in _segments(self.rf, t, tag):
            for i in np.nonzero(m)[0]:
                f.write(",".join([f"{t[i]:.6f}", f"{t[i] - self.t_first:.6f}"] + lab +
                                 [_cell(float(v)) for v in y[:, i]]) + "\n")
        self.rf.check_size()
        self.samples += t.size


class ChangeCsvSink(_RowsCsv):
    """On change: a row when any channel moves more than the deadband from its last written
    value (mode "abs" or "pct" of it), at least every heartbeat_s, at most every min_gap_s."""

    def __init__(self, rf_maker, topic, channels, t_start, project, deadband=0.5, mode="pct", heartbeat_s=600.0,
                 min_gap_s=0.0):
        super().__init__(rf_maker, topic, channels, t_start, project,
                         note=f"on change: deadband {deadband:g}{' %' if mode == 'pct' else ''}, "
                              f"heartbeat {heartbeat_s:g} s")
        self.db, self.pct = abs(float(deadband or 0)), mode == "pct"
        self.hb, self.gap = float(heartbeat_s or 0), float(min_gap_s or 0)
        self.last = None                          # the last written row
        self.t_last = -math.inf

    def _moved(self, row: np.ndarray) -> bool:
        if self.last is None:
            return True
        for v, w in zip(row, self.last):
            if math.isnan(v) != math.isnan(w):
                return True
            if math.isnan(v):
                continue
            lim = self.db * abs(w) / 100.0 if self.pct else self.db
            if abs(v - w) > lim or (lim == 0 and v != w):
                return True
        return False

    def write(self, rx_time, payload, blk):
        t = blk.t0 + np.arange(blk.n) * blk.dt
        y = self.select(blk)
        keep = []
        for i in range(blk.n):
            row = y[:, i]
            due = (self.hb and t[i] - self.t_last >= self.hb) or self._moved(row)
            if due and t[i] - self.t_last >= self.gap:
                keep.append(i)
                self.last, self.t_last = row.copy(), t[i]
        if keep:
            self.rows(t[keep], y[:, keep])


class GateCsvSink(_RowsCsv):
    """Trigger / batch: rows only while the gate is on (Recorder.gate), every_s apart (0 = every
    value); trigger: the last pre_s seconds before it (a buffer) and post_s after it; batch:
    a summary row per batch in <file>_summary.csv."""

    def __init__(self, rf_maker, topic, channels, t_start, project, kind="trigger", pre_s=0.0, post_s=0.0,
                 every_s=0.0, stats=()):
        super().__init__(rf_maker, topic, channels, t_start, project, label="run" if kind == "trigger" else "batch")
        self.kind = kind
        self.pre, self.post = (float(pre_s or 0), float(post_s or 0)) if kind == "trigger" else (0.0, 0.0)
        self.every, self._period = float(every_s or 0), None
        self.on, self.tag, self.until = False, "", None     # until: the end of the post-trigger time
        self.buf: list[tuple[np.ndarray, np.ndarray]] = []  # pre-trigger blocks (t, y)
        self.keys = [k for k in STAT_KEYS if k in (stats or [])] or ["mean"]
        self.summary = None
        if kind == "batch":
            cols = ["batch", "start", "end", "duration_s", "samples"] + \
                [f"{n} {STAT_LABELS[k]}" + (f" [{u}]" if u and k != "sum" else "")
                 for _, n, u in channels for k in self.keys]
            head = self._cols(cols)
            self.summary = rf_maker(".csv", lambda t: head, suffix="_summary", by_time_only=True)
            self.files.append(self.summary)
        self.accs, self.t_on, self.n = None, None, 0

    def gate(self, on: bool, t: float, tag: str):
        if on and not self.on:
            self.on, self.tag, self.until, self.t_on, self.n = True, tag, None, t, 0
            self.accs = [_Acc() for _ in self.channels]
            for bt, by in self.buf:                    # the pre-trigger time
                m = bt >= t - self.pre
                self._out(bt[m], by[:, m])
            self.buf = []
        elif not on and self.on:
            self.on, self.until = False, t + self.post
            self._summary(t)

    def _out(self, t, y):
        if self.every:
            m, self._period = every_mask(t, self.every, self._period)
            t, y = t[m], y[:, m]
        self.rows(t, y, self.tag)

    def write(self, rx_time, payload, blk):
        t = blk.t0 + np.arange(blk.n) * blk.dt
        y = self.select(blk)
        if self.on:
            m = t >= self.t_on - self.pre                  # a block from before the start: its later part
            t, y = t[m], y[:, m]
            self._out(t, y)
            if self.accs is not None:
                for r, acc in enumerate(self.accs):
                    acc.add(y[r])
                self.n += t.size
        elif self.until is not None:                   # the post-trigger time
            m = t <= self.until
            self._out(t[m], y[:, m])
            if not m.all() or t.size and t[-1] >= self.until:
                self.until = None
        if not self.on and self.pre > 0 and t.size:    # keep the last pre_s seconds
            self.buf.append((t, y))
            while self.buf and self.buf[0][0].size and self.buf[0][0][-1] < t[-1] - self.pre:
                self.buf.pop(0)

    def _summary(self, t_end: float):
        if self.summary is None or self.accs is None:
            return
        vals = [v for acc in self.accs for v in acc.result(self.keys)]
        iso = lambda x: _dt.datetime.fromtimestamp(x).isoformat(timespec="seconds")   # noqa: E731
        self.summary.get(self.t_on).write(",".join([self.tag.replace(",", ";"), iso(self.t_on), iso(t_end),
                                                    f"{t_end - self.t_on:.1f}", str(self.n)] +
                                                   [_cell(v) for v in vals]) + "\n")
        self.accs = None

    def close(self):
        if self.on:
            self._summary(time.time())
        super().close()


class EventCsvSink(_Sink):
    """Event: one row per event - time, event, the chosen tags' values (any category)."""

    def __init__(self, rf_maker, tags: list[tuple[str, str]], t_start: float, project: str):
        super().__init__({"name": "", "topic": ""}, [], t_start)
        start = _dt.datetime.fromtimestamp(t_start).isoformat(timespec="seconds")
        cols = ["time", "time_unix_s", "event"] + [f"{n} [{u}]" if u else n for n, u in tags]
        head = f"# project: {project}\n# events\n# start: {start}\n" + self._cols(cols)
        self.rf = rf_maker(".csv", lambda t: head)
        self.files = [self.rf]

    def event(self, t: float, text: str, values: list):
        stamp = _dt.datetime.fromtimestamp(t).isoformat(timespec="milliseconds")
        self.rf.get(t).write(",".join([stamp, f"{t:.3f}", text.replace(",", ";")] +
                                      [v.replace(",", ";") if isinstance(v, str) else _cell(v) for v in values]) + "\n")
        self.rf.check_size()
        self.samples += 1


WPK_MAGIC = b"WPKLOG\x00\x01"
_WPK_REC = struct.Struct("<dI")   # rx_time (Unix s), payload length


class WpkSink(_Sink):
    def __init__(self, rf_maker, topic, channels, t_start, project):
        super().__init__(topic, channels, t_start)

        def head(t):
            meta = {"project": project, "topic": topic, "t_start": t_start,
                    "channels": [{"index": i, "name": n, "unit": u} for i, n, u in channels],
                    "packet_format": "wavepacket v1 (see wavepacket.py)"}
            mb = json.dumps(meta, ensure_ascii=False).encode()
            return WPK_MAGIC + struct.pack("<I", len(mb)) + mb
        self.rf = _maker_of(rf_maker)(".wpk", head, binary=True)
        self.files = [self.rf]

    def write(self, rx_time, payload, blk):
        self.rf.get(blk.t0).write(_WPK_REC.pack(rx_time, len(payload)) + payload)
        self.rf.check_size()
        self.samples += blk.n


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

    folder  : the project's default log folder
    project : dict (the project config, saved as project.json in the session folder)
    topics  : [{"id", "name", "topic", "channels": [(index, name, unit), ...]}]
    profiles: optional list of logging profiles, each
              {"name", "type": "interval" ("signal") | "change" | "trigger" | "batch" | "event" |
               "statistic", "format", "keep_raw", "interval_s", "stats": [STAT_KEYS],
               "when": {...} (core/log_types.py), "output": {...} (core/log_files.py: its folder,
               date folders, new file per period / size / run, names, compress, keep days),
               "topics": [as above], "tags": [(name, unit)] (event: the snapshot columns)}
              Without profiles, topics / fmt / keep_raw make one unnamed signal profile
              (the v5 behaviour and file names).

    The session folder <folder>/<project>_<yyyymmdd_hhmmss>/ always holds project.json (every file
    of the run, paths relative to it or absolute); profiles writing into date folders also leave an
    index.json in each folder they write to.
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
        self.folder = folder
        self.project, self.topics, self.fmt, self.keep_raw = project, topics, fmt, keep_raw
        self.t_start = time.time()
        self.stamp = _dt.datetime.fromtimestamp(self.t_start).strftime("%Y%m%d_%H%M%S")
        self.session_id = f"{safe_name(project['name'])}_{self.stamp}"
        self.dir = os.path.join(folder, self.session_id)
        self.q: "queue.Queue" = queue.Queue(maxsize=20000)
        self.dropped = self.packets = 0
        self.error: str | None = None
        self.errors: dict[int, str] = {}         # profile index -> its write error
        self.last_write: dict[int, float] = {}   # profile index -> when it last wrote a row
        self._sinks: dict[str, list[_Sink]] = {}
        self._by_profile: dict[int, list] = {}  # profile index -> its sinks
        self.files: list[dict] = []              # index for readers (NX Viewer): who wrote which file
        self.open_paths: set[str] = set()
        self._session_dirs: dict[str, str] = {}  # base folder -> its session folder
        self._outs = [lf.normalize_output(dict(pr)) for pr in profiles]
        self._last_prune = 0.0
        self._thread = threading.Thread(target=self._run, name="recorder", daemon=True)

    # ------------------------------------------------------------ where files go
    def base_of(self, pi: int) -> str:
        return self._outs[pi].get("folder") or self.folder

    def folder_of(self, pi: int, t: float, ctx: dict) -> str:
        """The folder of profile pi's file for the period starting at t."""
        out, base = self._outs[pi], self.base_of(pi)
        if out["folders"] == "session":
            d = self._session_dirs.get(base)
            if d is None:
                d = self._session_dirs[base] = os.path.join(base, self.session_id)
                os.makedirs(d, exist_ok=True)
            return d
        return os.path.join(base, *lf.folder_parts(out, t, ctx))

    def _maker(self, pi: int, pr: dict, device: str):
        kind = "interval" if pr.get("type", "signal") == "signal" else pr.get("type")
        ctx = {"project": safe_name(self.project["name"]), "profile": safe_name(pr["name"]) if pr.get("name") else "",
               "device": safe_name(device) if device else "", "session": self.stamp}

        def make(ext, header, binary=False, suffix="", by_time_only=False):
            return RotFile(self, pi, self._outs[pi], kind, ctx, ext, header, binary, suffix, by_time_only)
        return make

    def opened(self, rf: RotFile, path: str, d: str, start: float):
        """A file was opened (the recorder thread): index it."""
        self.open_paths.add(path)
        pr = self.profiles[rf.pi]
        entry = {"file": self._rel(path), "path": path, "profile": pr.get("name", ""),
                 "type": "interval" if pr.get("type", "signal") == "signal" else pr.get("type"),
                 "device_id": rf.ctx.get("device_id", ""), "device": rf.ctx.get("device", ""),
                 "format": "wpk" if path.endswith(".wpk") else "csv",
                 "interval_s": pr.get("interval_s") if pr.get("type") == "statistic" else None,
                 "period_start": _dt.datetime.fromtimestamp(start).isoformat(timespec="seconds"),
                 "session_id": self.session_id}
        if not any(x["path"] == path for x in self.files):
            self.files.append(entry)
            if self._thread.is_alive():          # a crash still leaves the list of files
                self._write_session_json(running=True)
        if self._outs[rf.pi]["folders"] != "session":
            self._index(d, entry)

    def closed(self, rf: RotFile, path: str):
        """A file was closed: compressed when its profile wants it."""
        self.open_paths.discard(path)
        if not self._outs[rf.pi].get("compress") or not os.path.isfile(path):
            return
        try:
            new = compress_file(path, "zstd" if zstd_available() else "gzip")
        except Exception as e:                   # keep the plain file
            self.errors[rf.pi] = f"compress: {e}"
            return
        for x in self.files:
            if x["path"] == path:
                x.update(path=new, file=self._rel(new))
        if self._outs[rf.pi]["folders"] != "session":
            self._index(os.path.dirname(path), None, rename=(os.path.basename(path), os.path.basename(new)))

    def _rel(self, path: str) -> str:
        try:
            rel = os.path.relpath(path, self.dir)
        except ValueError:                        # another drive
            return path
        return path if rel.startswith("..") else rel

    @staticmethod
    def _index(d: str, entry: dict | None, rename=None):
        """index.json of a date folder: the files written there (merged over sessions)."""
        p = os.path.join(d, "index.json")
        try:
            with open(p, encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            info = {"app": "NX Logger", "index_format": 1, "files": []}
        files = info.setdefault("files", [])
        if entry is not None:
            e = {k: v for k, v in entry.items() if k != "path"}
            e["file"] = os.path.basename(entry["path"])
            if not any(x.get("file") == e["file"] and x.get("session_id") == e["session_id"] for x in files):
                files.append(e)
        if rename:
            for x in files:
                if x.get("file") == rename[0]:
                    x["file"] = rename[1]
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(info, f, indent=2, ensure_ascii=False)
        except OSError:
            pass

    # ------------------------------------------------------------ control
    def start(self):
        os.makedirs(self.dir, exist_ok=False)
        self._session_dirs[self.folder] = self.dir
        name = self.project["name"]
        for pi, pr in enumerate(self.profiles):
            kind = "interval" if pr.get("type", "signal") == "signal" else pr.get("type")
            fmt, w = pr.get("format", "CSV"), pr.get("when") or {}
            mine = self._by_profile.setdefault(pi, [])
            try:
                os.makedirs(self.base_of(pi), exist_ok=True)
            except OSError as e:
                self.errors[pi] = f"{self.base_of(pi)}: {e}"
                continue
            if kind == "event":
                mine.append(EventCsvSink(self._maker(pi, pr, ""), pr.get("tags", []), self.t_start, name))
                continue
            for tp in pr["topics"]:
                mk = self._maker(pi, pr, tp["name"])
                sinks = self._sinks.setdefault(tp["id"], [])
                new: list[_Sink] = []
                if kind == "statistic":
                    new.append(StatCsvSink(mk, tp, tp["channels"], self.t_start, name, pr.get("interval_s", 1.0),
                                           pr.get("stats", ["mean"])))
                elif kind == "change":
                    new.append(ChangeCsvSink(mk, tp, tp["channels"], self.t_start, name, w.get("deadband", 0.5),
                                             w.get("mode", "pct"), w.get("heartbeat_s", 600.0), w.get("min_gap_s", 0.0)))
                elif kind in ("trigger", "batch"):
                    new.append(GateCsvSink(mk, tp, tp["channels"], self.t_start, name, kind, w.get("pre_s", 0),
                                           w.get("post_s", 0), w.get("every_s", 0), w.get("stats", ())))
                else:
                    virtual = tp.get("virtual", False)      # number tags / variables: no raw packets
                    every = float(w.get("every_s") or 0)
                    if fmt != "WPK" or virtual or every:
                        new.append(CsvSink(mk, tp, tp["channels"], self.t_start, name, every))
                    if (fmt == "WPK" or pr.get("keep_raw")) and not virtual and not every:
                        new.append(WpkSink(mk, tp, tp["channels"], self.t_start, name))
                for sk in new:
                    sk.pi = pi
                    for rf in sk.files:
                        rf.ctx["device_id"] = tp["id"]
                sinks += new
                mine += new
        self._write_session_json(running=True)
        self._thread.start()

    def put(self, topic_id: str, rx_time: float, payload: bytes, blk: wp.WaveBlock):
        if topic_id not in self._sinks:
            return
        try:
            self.q.put_nowait((topic_id, rx_time, payload, blk))
        except queue.Full:
            self.dropped += 1

    def gate(self, profile: int, on: bool, t: float, tag: str = ""):
        """Trigger / batch profile `profile` (index) on or off at t (its rows start / stop)."""
        if self._by_profile.get(profile):
            self.q.put(("__gate__", profile, on, t, tag))

    def event(self, profile: int, t: float, text: str, values: list):
        """Event profile `profile`: one row (values in its `tags` order)."""
        if self._by_profile.get(profile):
            self.q.put(("__event__", profile, t, text, values))

    def stop(self):
        self.q.put(None)
        self._thread.join(timeout=30)
        for sinks in self._by_profile.values():
            for s in sinks:
                try:
                    s.close()
                except Exception as e:  # keep closing the others
                    self.error = self.error or str(e)
        self._write_session_json(running=False)

    # ------------------------------------------------------------ stats
    @property
    def elapsed(self) -> float:
        return time.time() - self.t_start

    @property
    def samples(self) -> int:
        return max((s.samples for sinks in self._sinks.values() for s in sinks), default=0)

    def written(self, profile: int) -> int:
        """Rows / events a profile has written so far (the Logging table)."""
        return sum(s.samples for s in self._by_profile.get(profile, []))

    def profile_files(self, profile: int) -> tuple[str, int, int]:
        """(the file written now - the newest, files, bytes) of a profile."""
        name = self.profiles[profile].get("name", "")
        mine = [x for x in self.files if x["profile"] == name]
        size = 0
        for x in mine:
            try:
                size += os.path.getsize(x["path"])
            except OSError:
                pass
        now = next((s.files[0].path for s in self._by_profile.get(profile, []) if s.files and s.files[0].f), "")
        return now or (mine[-1]["path"] if mine else ""), len(mine), size

    def disk_bytes(self) -> int:
        total = 0
        for x in self.files:
            try:
                total += os.path.getsize(x["path"])
            except OSError:
                pass
        return total

    # ------------------------------------------------------------ internals
    def _run(self):
        last_flush = time.monotonic()
        self._prune()
        while True:
            try:
                item = self.q.get(timeout=0.5)
            except queue.Empty:
                item = False
            if item is None:
                return
            if item and item[0] in ("__gate__", "__event__"):
                try:
                    for sk in self._by_profile.get(item[1], []):
                        if item[0] == "__gate__":
                            sk.gate(item[2], item[3], item[4])
                        else:
                            sk.event(item[2], item[3], item[4])
                    self.packets += item[0] == "__event__"
                except Exception as e:
                    self.error = self.errors[item[1]] = str(e)
                item = False
            if item:
                tid, rx, payload, blk = item
                for s in self._sinks[tid]:
                    try:
                        s.write(rx, payload, blk)
                    except Exception as e:      # disk full, folder gone - this profile, keep running
                        self.error = self.errors[getattr(s, "pi", 0)] = str(e)
                self.packets += 1
            if time.monotonic() - last_flush > 1.0:
                last_flush = time.monotonic()
                for sinks in self._by_profile.values():
                    for s in sinks:
                        try:
                            s.flush()
                        except Exception as e:
                            self.error = str(e)
                if time.time() - self._last_prune > 3600:
                    self._prune()

    def _prune(self):
        """Keep N days: delete each such profile's own old files (its name prefix) and empty folders."""
        self._last_prune = time.time()
        for pi, pr in enumerate(self.profiles):
            out = self._outs[pi]
            days = int(out.get("keep_days") or 0)
            if days <= 0 or lf.problem(out):
                continue
            base = self.base_of(pi)
            prefix = lf.keep_prefix(out, {"project": safe_name(self.project["name"]),
                                          "profile": safe_name(pr["name"]) if pr.get("name") else ""})
            cutoff = time.time() - days * 86400
            for root, dirs, files in os.walk(base, topdown=False):
                for fn in files:
                    p = os.path.join(root, fn)
                    if fn.startswith(prefix) and fn.endswith((".csv", ".wpk", ".zst", ".gz")) and \
                            p not in self.open_paths:
                        try:
                            if os.path.getmtime(p) < cutoff:
                                os.remove(p)
                        except OSError:
                            pass
                if root != base and root not in self._session_dirs.values():
                    try:
                        left = os.listdir(root)
                        if not left or left == ["index.json"]:
                            for x in left:
                                os.remove(os.path.join(root, x))
                            os.rmdir(root)
                    except OSError:
                        pass

    def _write_session_json(self, running: bool):
        info = {"app": "NX Logger", "session_format": 2,          # docs/recording-format.md
                "session_id": self.session_id,
                "project": self.project, "format": self.fmt, "keep_raw": self.keep_raw,
                "profiles": [{k: v for k, v in p.items() if k != "topics"} |
                             {"topics": [t["name"] for t in p["topics"]],
                              "folder": self.base_of(i)} for i, p in enumerate(self.profiles)],
                "start": _dt.datetime.fromtimestamp(self.t_start).isoformat(),
                "start_unix": self.t_start, "running": running,
                "topics": [{**t, "channels": [{"index": i, "name": n, "unit": u}
                                              for i, n, u in t["channels"]]}
                           for t in self.topics],
                "files": [{k: v for k, v in x.items() if k != "path"} for x in self.files]}
        info["devices"] = info["topics"]         # same list; "topics" kept for older readers
        if not running:
            info.update(end=_dt.datetime.now().isoformat(), duration_s=self.elapsed,
                        packets=self.packets, samples_per_channel=self.samples,
                        dropped=self.dropped, error=self.error)
        for d in dict.fromkeys(self._session_dirs.values()):
            try:
                with open(os.path.join(d, "project.json"), "w", encoding="utf-8") as f:
                    json.dump(info, f, indent=2, ensure_ascii=False)
            except OSError as e:
                self.error = self.error or str(e)
