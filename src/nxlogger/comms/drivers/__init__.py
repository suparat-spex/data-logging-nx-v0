"""Connectors: one driver thread per device (see base.py for the message protocol)."""
from __future__ import annotations

import copy

from nxlogger.comms.drivers.base import STATES, Driver, PollDriver  # noqa: F401


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
