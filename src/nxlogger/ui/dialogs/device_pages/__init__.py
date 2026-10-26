"""Connector sections of the device settings pop-up: one page per connector (see base.py)."""
from __future__ import annotations

from nxlogger.ui.dialogs.device_pages.base import ConnectionPage
from nxlogger.ui.dialogs.device_pages.modbus_rtu import ModbusRtuPage
from nxlogger.ui.dialogs.device_pages.modbus_tcp import ModbusTcpPage
from nxlogger.ui.dialogs.device_pages.mqtt import MqttPage
from nxlogger.ui.dialogs.device_pages.opcda import OpcDaPage
from nxlogger.ui.dialogs.device_pages.opcua import OpcUaPage
from nxlogger.ui.dialogs.device_pages.sim import SimPage

PAGES: dict[str, type[ConnectionPage]] = {p.DRIVER: p for p in (SimPage, ModbusTcpPage, ModbusRtuPage, MqttPage,
                                                                 OpcUaPage, OpcDaPage)}


def connection_page(driver: str, values: dict, tabs: bool = False) -> ConnectionPage:
    return PAGES[driver](values, tabs=tabs)
