"""sim.py - built-in signal generator: one value per tag and scan (no hardware needed)."""
from __future__ import annotations

import math
import random
import time

from nxlogger.comms.drivers.base import PollDriver


class SimDriver(PollDriver):
    def open(self):
        self._walk: dict[int, float] = {}
        self._count: dict[int, int] = {}
        self._written: dict[int, float] = {}

    def read(self, tags):
        t = time.time()
        return [self._value(tag, t) for tag in tags]

    def _value(self, tag, t: float) -> float:
        if tag.index in self._written:                # a written value holds (like a setpoint)
            return self._written[tag.index]
        a = tag.address
        amp, f, off = float(a.get("amp", 1)), float(a.get("freq", 0.1)), float(a.get("offset", 0))
        ph = (t * f) % 1.0
        w = a.get("wave", "sine")
        if w == "sine":
            v = math.sin(2 * math.pi * ph)
        elif w == "square":
            v = 1.0 if ph < 0.5 else -1.0
        elif w == "triangle":
            v = 4 * ph - 1 if ph < 0.5 else 3 - 4 * ph
        elif w == "sawtooth":
            v = 2 * ph - 1
        elif w == "ramp":
            v = ph
        elif w == "random":                            # bounded random walk: looks like a process value
            x = self._walk.get(tag.index, 0.0) * 0.97 + random.gauss(0, 0.15)
            self._walk[tag.index] = v = max(-1.0, min(1.0, x))
        elif w == "counter":
            self._count[tag.index] = n = self._count.get(tag.index, -1) + 1
            return off + amp * n
        else:                                          # constant: the offset (+ noise)
            v = 0.0
        noise = float(a.get("noise", 0))
        return off + amp * v + (random.gauss(0, noise) if noise > 0 else 0.0)

    def do_write(self, tag, value):
        self._written[tag.index] = float(value)
