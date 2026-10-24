"""Live buffers: per-tag ring buffer and per-device stream state (continuity, rates, quality).

Waveform devices keep MAX_WINDOW_S of samples (that is a lot of points at kS/s); scanned
number tags keep at least TREND_POINTS scans, e.g. 2 h of a 1 s scan for the trends.
Longer statistics windows are buffered separately (stat_buffer.py)."""
from __future__ import annotations

import numpy as np

from nxlogger.core import wavepacket as wp


MAX_WINDOW_S = 60                    # waveform history (s); sizes the ring buffers
TREND_POINTS = 7200                  # scans kept per number tag (2 h at 1 s)
MAX_PLOT_S = 7200                    # longest Quick View / graph window (s)


class ChannelRing:
    """Fixed-size ring buffer of (time, value) for one channel."""

    def __init__(self, cap: int):
        self.t = np.full(cap, np.nan)
        self.y = np.full(cap, np.nan)
        self.cap, self.w, self.count = cap, 0, 0

    def append(self, t: np.ndarray, y: np.ndarray):
        n = len(t)
        if n >= self.cap:
            t, y, n = t[-self.cap:], y[-self.cap:], self.cap
        end = self.w + n
        if end <= self.cap:
            self.t[self.w:end], self.y[self.w:end] = t, y
        else:
            k = self.cap - self.w
            self.t[self.w:], self.y[self.w:] = t[:k], y[:k]
            self.t[:n - k], self.y[:n - k] = t[k:], y[k:]
        self.w = end % self.cap
        self.count = min(self.count + n, self.cap)

    def break_line(self, t: float):
        """Insert a NaN point so the plot shows a gap (lost packets)."""
        self.append(np.array([t]), np.array([np.nan]))

    def ordered(self):
        if self.count < self.cap:
            return self.t[:self.count], self.y[:self.count]
        return (np.concatenate((self.t[self.w:], self.t[:self.w])),
                np.concatenate((self.y[self.w:], self.y[:self.w])))


class TopicStream:
    """Live state for one device (or variable): ring buffers, continuity check, rates,
    quality and time of the last value per tag."""

    def __init__(self):
        self.rings: dict[int, ChannelRing] = {}
        self.next_idx = self.last_seq = self.fs = self.last = None
        self.pkts = self.lost = self.crc = self.bad = 0
        self.win_pkts, self.pps = 0, 0.0
        self.last_vals: dict[int, float] = {}
        self.raw_mean: dict[int, float] = {}     # unscaled mean of the last block (for Tare / preview)
        self.scales: dict[int, float] = {}
        self.good: dict[int, bool] = {}          # quality of the last value (False = bad / missing)
        self.stamp: dict[int, float] = {}        # Unix time of the last value

    def feed(self, blk: wp.WaveBlock, t_ref: float):
        self.pkts += 1; self.win_pkts += 1
        self.fs, self.last = 1.0 / blk.dt, blk
        if self.next_idx is not None and blk.sample_idx < self.next_idx - blk.n:
            self.rings.clear(); self.next_idx = blk.sample_idx; self.last_seq = None
        gap = False
        if self.last_seq is not None:
            missed = (blk.seq - self.last_seq - 1) & 0xFFFF
            if missed:
                self.lost += missed; gap = True
        if self.next_idx is not None and blk.sample_idx != self.next_idx:
            gap = True
        self.last_seq, self.next_idx = blk.seq, blk.sample_idx + blk.n
        t = blk.t0 - t_ref + np.arange(blk.n) * blk.dt
        cap = max(int(MAX_WINDOW_S * self.fs), TREND_POINTS if blk.n == 1 else 0) + 2 * blk.n
        for row, ch in enumerate(blk.channels):
            ring = self.rings.get(ch)
            if ring is None or ring.cap < cap:
                ring = self.rings[ch] = ChannelRing(cap)
            if gap:
                ring.break_line(t[0] - blk.dt / 2)
            ring.append(t, blk.y[row])
            v = float(blk.y[row, -1])
            self.last_vals[ch] = v
            self.good[ch] = v == v                # NaN = bad quality
            self.stamp[ch] = blk.t0 + (blk.n - 1) * blk.dt
            self.scales[ch] = float(blk.scales[row])
