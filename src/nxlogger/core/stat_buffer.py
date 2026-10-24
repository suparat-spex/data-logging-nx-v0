"""
stat_buffer.py - statistics over a window the app collects itself (GA10 style). No Qt.

The plot rings only hold what the screen needs (60 s of a waveform, a few hours of a
1 s scan). A statistic can ask for more - "mean of the last 10 min", "max of the last
500 samples" - so each (channel, window) pair gets its own buffer, fed with every
block from the moment the window is used anywhere (view, variable), whether its view is
on screen or not.

Window spec (dict, also stored on tiles / variables):
  win_n     length (0 = not buffered: the caller uses the Quick View window instead)
  win_unit  "s" | "min" | "h" | "samples"
  win_mode  "moving"  statistics of the newest window, recomputed on request
            "block"   the window is filled, its statistics are held until the next one
                      is complete (a clock-aligned report value: e.g. the mean of each
                      full minute)

Memory: a buffer keeps at most MAX_POINTS samples; a longer time window of a fast
waveform is decimated (every k-th block sample) and says so in `decimated`.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from nxlogger.core import analysis as an

UNIT_S = {"s": 1.0, "min": 60.0, "h": 3600.0}
MAX_POINTS = 2_000_000


def spec_of(d: dict) -> tuple[float, str, str] | None:
    """Hashable window spec from a tile / variable dict, or None if not buffered."""
    n = float(d.get("win_n") or 0)
    if n <= 0:
        return None
    unit = d.get("win_unit", "s")
    unit = unit if unit in UNIT_S or unit == "samples" else "s"
    return (n if unit != "samples" else float(int(n)), unit, "block" if d.get("win_mode") == "block" else "moving")


def describe(spec) -> str:
    if spec is None:
        return "Quick View window"
    n, unit, mode = spec
    return (f"{int(n)} samples" if unit == "samples" else f"{n:g} {unit}") + (" block" if mode == "block" else "")


class StatBuffer:
    """Samples of one channel for one window."""

    def __init__(self, spec: tuple[float, str, str]):
        self.n, self.unit, self.mode = spec
        self.span = None if self.unit == "samples" else self.n * UNIT_S[self.unit]
        self.chunks: deque[tuple[np.ndarray, np.ndarray]] = deque()
        self.count = 0
        self.step = 1                          # decimation (1 = every sample)
        self.decimated = False
        self.held: tuple[np.ndarray, np.ndarray] | None = None   # block mode: last full window
        self.block_start: float | None = None
        self.fs: float | None = None

    def _limit(self) -> int:
        return int(self.n) if self.span is None else MAX_POINTS

    def feed(self, t: np.ndarray, y: np.ndarray):
        if t.size == 0:
            return
        if t.size > 1:
            self.fs = 1.0 / float(t[1] - t[0]) if t[1] > t[0] else self.fs
        if self.span is not None and self.fs and self.span * self.fs / self.step > MAX_POINTS:
            self.step = int(math.ceil(self.span * self.fs / MAX_POINTS))    # long window: decimate
            self.decimated = True
        if self.step > 1:
            t, y = t[::self.step], y[::self.step]
        if self.mode == "block":
            self._feed_block(t, y)
            return
        self.chunks.append((t, y))
        self.count += t.size
        self._trim()

    def _trim(self):
        if self.span is not None:
            t_last = self.chunks[-1][0][-1]
            while self.chunks and self.chunks[0][0][-1] < t_last - self.span:
                self.count -= self.chunks.popleft()[0].size
        while self.chunks and self.count - self.chunks[0][0].size >= self._limit():
            self.count -= self.chunks.popleft()[0].size

    def _feed_block(self, t, y):
        """Cut into windows: time windows align to the clock (a 1 min block starts at hh:mm:00)."""
        if self.span is not None:
            b = np.floor(t / self.span)
            for bk in np.unique(b):
                m = b == bk
                if self.block_start is not None and bk != self.block_start:
                    self._close_block()
                self.block_start = bk
                self.chunks.append((t[m], y[m])); self.count += int(m.sum())
            return
        i = 0
        while i < t.size:                           # sample blocks: exactly n samples each
            take = min(int(self.n) - self.count, t.size - i)
            self.chunks.append((t[i:i + take], y[i:i + take])); self.count += take
            i += take
            if self.count >= int(self.n):
                self._close_block()

    def _close_block(self):
        if self.chunks:
            self.held = (np.concatenate([c[0] for c in self.chunks]),
                         np.concatenate([c[1] for c in self.chunks]))
        self.chunks.clear(); self.count = 0

    def data(self) -> tuple[np.ndarray, np.ndarray] | None:
        if self.mode == "block":
            return self.held
        if not self.chunks:
            return None
        t = np.concatenate([c[0] for c in self.chunks])
        y = np.concatenate([c[1] for c in self.chunks])
        if self.span is None:                       # samples: exactly the newest n
            t, y = t[-int(self.n):], y[-int(self.n):]
        return t, y

    def fill(self) -> float:
        """0..1: how full the current window is (for 'collecting...' hints)."""
        if self.mode == "block" and self.held is not None:
            return 1.0
        if self.span is None:
            return min(1.0, self.count / max(self.n, 1))
        if not self.chunks:
            return 0.0
        t0, t1 = self.chunks[0][0][0], self.chunks[-1][0][-1]
        return min(1.0, (t1 - t0) / self.span)


class StatHub:
    """All buffered windows. `require()` with the set of (key, spec) in use; `feed()` every block."""

    def __init__(self):
        self.buffers: dict[tuple, StatBuffer] = {}      # (key, spec) -> buffer
        self._by_key: dict[tuple, list[StatBuffer]] = {}

    def require(self, wanted):
        wanted = set(wanted)
        for k in list(self.buffers):
            if k not in wanted:
                del self.buffers[k]
        for k in wanted:
            if k not in self.buffers:
                self.buffers[k] = StatBuffer(k[1])
        self._by_key = {}
        for (key, _), b in self.buffers.items():
            self._by_key.setdefault(key, []).append(b)

    def wants(self, key) -> bool:
        return key in self._by_key

    def feed(self, key, t: np.ndarray, y: np.ndarray):
        for b in self._by_key.get(key, ()):
            b.feed(t, y)

    def clear(self):
        keys = list(self.buffers)                   # same windows, empty buffers
        self.require([])
        self.require(keys)

    def values(self, key, spec, keys) -> dict[str, float | None] | None:
        """{STAT_LABEL: value} over the window, or None (not buffered / no data yet)."""
        b = self.buffers.get((key, spec))
        d = b.data() if b else None
        if d is None or d[1].size == 0:
            return None
        fs = an.sample_rate(d[0]) or b.fs
        return {an.STAT_LABEL[k]: v for k, v in an.stats(d[1], fs, keys).items()}

    def fill(self, key, spec) -> float:
        b = self.buffers.get((key, spec))
        return b.fill() if b else 0.0
