"""Connectors: one driver thread per device (see base.py for the message protocol)."""
from __future__ import annotations

import copy
import queue
import time

from nxlogger.comms.drivers.base import Driver, PollDriver

__all__ = ["Driver", "PollDriver", "make_driver", "test_connection"]


class OpcDaDriver(PollDriver):
    """Placeholder: classic OPC DA needs Windows COM (OpenOPC / pywin32)."""

    def open(self):
        raise RuntimeError("OPC DA is not available yet - use an OPC UA server or wrapper")


def make_driver(dev, out) -> Driver:
    """Driver for a DeviceCfg (copied: editing the project never changes a running driver)."""
    dev = copy.deepcopy(dev)
    if dev.driver == "sim":
        from nxlogger.comms.drivers.sim import SimDriver
        return SimDriver(dev, out)
    if dev.driver in ("modbus_tcp", "modbus_rtu"):
        from nxlogger.comms.drivers.modbus import ModbusDriver
        return ModbusDriver(dev, out)
    if dev.driver == "mqtt":
        from nxlogger.comms.drivers.mqtt import MqttDriver
        return MqttDriver(dev, out)
    if dev.driver == "opcua":
        from nxlogger.comms.drivers.opcua import OpcUaDriver
        return OpcUaDriver(dev, out)
    return OpcDaDriver(dev, out)


def test_connection(dev, limit: float | None = None) -> tuple[bool, str]:
    """Connect once with the real driver (on a copy of `dev`) and say how it went - the device
    pop-up's Test connection. Waits for online / error, then the first scan of the tags.
    Blocks up to `limit` seconds: run it in a worker thread, never on the GUI thread."""
    q: queue.Queue = queue.Queue()
    drv = make_driver(dev, q)
    if limit is None:                            # the connector's own timeout, with room for retries
        limit = 3.0 + 2.0 * float(dev.settings.get("timeout_s", 2.5))
    t0 = time.monotonic()
    drv.start()
    try:
        took = None
        while took is None and time.monotonic() - t0 < limit:
            try:
                msg = q.get(timeout=0.1)
            except queue.Empty:
                continue
            if msg[0] == "status" and msg[2] == "error":
                return False, msg[3] or "connection failed"
            if msg[0] == "status" and msg[2] == "online":
                took = (time.monotonic() - t0) * 1000
        if took is None:
            return False, f"no answer from {dev.topic} within {limit:g} s"
        text = f"connected in {took:.0f} ms"
        if dev.wave or not drv.number_tags():
            return True, text
        end = time.monotonic() + max(1.0, dev.scan_ms / 1000) + 1.0          # first scan of the tags
        while time.monotonic() < end:
            try:
                msg = q.get(timeout=0.1)
            except queue.Empty:
                continue
            if msg[0] == "scan":
                good = sum(v is not None for v in msg[5])
                return True, f"{text} · {good}/{len(msg[5])} tag(s) with a value"
            if msg[0] == "status" and msg[2] == "error":
                return False, f"connected, but reading the tags failed: {msg[3]}"
        return True, text
    finally:
        drv.stop(wait=2.0)
