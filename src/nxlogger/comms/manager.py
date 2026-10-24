"""
manager.py - runs the device drivers and collects their messages in one queue. No Qt.

The window starts / stops drivers and drains `q` on its GUI timer (see drivers/base.py
for the messages). Editing a device restarts only that device.
"""
from __future__ import annotations

import queue

from nxlogger.comms.drivers import make_driver


class DriverManager:
    def __init__(self):
        self.q: "queue.Queue[tuple]" = queue.Queue(maxsize=50000)
        self.drivers: dict = {}

    def start(self, dev):
        self.stop(dev.id)
        d = self.drivers[dev.id] = make_driver(dev, self.q)
        d.start()

    def stop(self, did: str):
        d = self.drivers.pop(did, None)
        if d is not None:
            d.stop()

    def stop_all(self):
        drivers, self.drivers = list(self.drivers.values()), {}
        for d in drivers:                        # signal all first: the waits overlap
            d._stop.set()
            d.interrupt()
        for d in drivers:
            d.stop()

    def running(self, did: str) -> bool:
        return did in self.drivers

    @property
    def any_running(self) -> bool:
        return bool(self.drivers)

    def state(self, did: str) -> tuple[str, str]:
        d = self.drivers.get(did)
        return (d.state, d.message) if d else ("stopped", "")

    def driver(self, did: str):
        return self.drivers.get(did)

    def write(self, did: str, idx: int, value: float) -> bool:
        d = self.drivers.get(did)
        if d is None:
            return False
        d.write(idx, value)
        return True

    def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> bool:
        """Alarm notifications: publish through the first connected MQTT device."""
        for d in self.drivers.values():
            if hasattr(d, "publish") and d.publish(topic, payload, qos, retain):
                return True
        return False
