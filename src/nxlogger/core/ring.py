"""Live buffers: per-channel ring buffer and per-topic stream state (continuity, rates)."""
from __future__ import annotations

import numpy as np

from nxlogger.core import wavepacket as wp


MAX_WINDOW_S = 60                    # longest plot window (s); sizes the ring buffers


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
    """Live state for one topic: ring buffers, continuity check, rates."""

    def __init__(self):
        self.rings: dict[int, ChannelRing] = {}
        self.next_idx = self.last_seq = self.fs = self.last = None
        self.pkts = self.lost = self.crc = self.bad = 0
        self.win_pkts, self.pps = 0, 0.0
        self.last_vals: dict[int, float] = {}
        self.raw_mean: dict[int, float] = {}     # unscaled mean of the last block (for Tare / preview)
        self.scales: dict[int, float] = {}

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
        cap = int(MAX_WINDOW_S * self.fs) + 2 * blk.n
        for row, ch in enumerate(blk.channels):
            ring = self.rings.get(ch)
            if ring is None or ring.cap < cap:
                ring = self.rings[ch] = ChannelRing(cap)
            if gap:
                ring.break_line(t[0] - blk.dt / 2)
            ring.append(t, blk.y[row])
            self.last_vals[ch] = float(blk.y[row, -1])
            self.scales[ch] = float(blk.scales[row])
