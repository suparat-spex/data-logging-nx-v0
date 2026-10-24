"""
base.py - driver thread shared by every connector. No Qt.

A driver owns one device connection in its own thread and only queues results; the GUI
timer drains the queue (CLAUDE.md: threads never touch widgets). Messages:

  ("scan",   device_id, t_unix, dt_s, [tag index], [value or None])   one scan of number tags
  ("wave",   device_id, rx_unix, payload bytes)                        one wavepacket (MQTT)
  ("status", device_id, state, text)        state: connecting | online | error | stopped
  ("write",  device_id, ok, text)           result of a write request

A value of None means bad quality (read error, not received yet, not a number).
PollDriver reconnects by itself (2 s, 4 s ... 10 s back-off) until stopped.
"""
from __future__ import annotations

import queue
import threading
import time

STATES = {"stopped": "Stopped", "connecting": "Connecting", "online": "Online", "error": "Error"}


class Driver:
    def __init__(self, dev, out: "queue.Queue"):
        self.dev, self.out = dev, out             # dev: a private copy of the DeviceCfg
        self.id = dev.id
        self.state, self.message = "stopped", ""
        self.scans = self.errors = self.dropped = 0
        self.last_ok = 0.0
        self._stop = threading.Event()
        self._writes: "queue.Queue[tuple[int, float]]" = queue.Queue()
        self._thread = threading.Thread(target=self._main, name=f"drv-{dev.name}", daemon=True)

    # ---- control (GUI thread)
    def start(self):
        self._thread.start()

    def stop(self, wait: float = 3.0):
        self._stop.set()
        self.interrupt()
        if self._thread.is_alive():
            self._thread.join(timeout=wait)

    def interrupt(self):
        """Unblock a pending network call (stop requested)."""

    def write(self, idx: int, value: float):
        self._writes.put((idx, value))

    # ---- helpers (driver thread)
    def put(self, msg: tuple):
        try:
            self.out.put_nowait(msg)
        except queue.Full:
            self.dropped += 1

    def status(self, state: str, text: str = ""):
        if (state, text) != (self.state, self.message):
            self.state, self.message = state, text
            self.put(("status", self.id, state, text))

    def sleep(self, s: float) -> bool:
        """Wait, but wake at once on stop. True = keep running."""
        return not self._stop.wait(max(0.0, s))

    @property
    def running(self) -> bool:
        return not self._stop.is_set()

    def number_tags(self):
        return [t for t in self.dev.tags if t.enabled and t.kind == "number"]

    def _main(self):
        try:
            self.run()
        except Exception as e:                    # a driver bug must never kill the app
            self.status("error", f"driver stopped: {e}")
            return
        self.status("stopped", "")

    def run(self): ...


class PollDriver(Driver):
    """Connect, then read every number tag each scan interval."""

    def open(self): ...
    def close(self): ...

    def read(self, tags) -> list[float | None]:
        return [None] * len(tags)

    def do_write(self, tag, value: float):
        raise RuntimeError("this connector cannot write")

    def run(self):
        period = max(0.02, self.dev.scan_ms / 1000.0)
        tags = self.number_tags()
        index = [t.index for t in tags]
        fails, connected = 0, False
        next_t = time.monotonic()
        while self.running:
            try:
                if not connected:
                    self.status("connecting", self.dev.topic)
                    self.open()
                    connected, fails = True, 0
                    self.status("online", f"{len(tags)} tag(s) every {period:g} s")
                self._do_writes()
                t = time.time()
                vals = self.read(tags) if tags else []
                self.scans += 1
                if any(v is not None for v in vals) or not tags:
                    self.last_ok = t
                self.put(("scan", self.id, t, period, index, vals))
            except Exception as e:
                self.errors += 1
                fails += 1
                connected = False
                self.status("error", str(e)[:200] or type(e).__name__)
                try:
                    self.close()
                except Exception:
                    pass
                if not self.sleep(min(2.0 * fails, 10.0)):
                    break
                next_t = time.monotonic()
                continue
            next_t += period
            now = time.monotonic()
            if next_t < now:                          # slow device: skip, don't pile up
                next_t = now
            if not self.sleep(next_t - now):
                break
        try:
            self.close()
        except Exception:
            pass

    def _do_writes(self):
        while True:
            try:
                idx, value = self._writes.get_nowait()
            except queue.Empty:
                return
            tag = self.dev.tag(idx)
            try:
                if tag is None:
                    raise RuntimeError("unknown tag")
                if tag.access != "RW":
                    raise RuntimeError(f"'{tag.name}' is read-only")
                self.do_write(tag, value)
                self.put(("write", self.id, True, f"{tag.name} ← {value:g}"))
            except Exception as e:
                self.put(("write", self.id, False, f"{tag.name if tag else idx}: {e}"))
