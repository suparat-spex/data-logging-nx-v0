"""Modbus TCP: PLC, meter or gateway over Ethernet."""
from __future__ import annotations

import ipaddress

from nxlogger.ui.dialogs.device_pages.base import ConnectionPage

MODBUS_SECTION = ("Modbus", ["unit", "timeout_s", "max_block", "one_based"])   # shared with RTU


class ModbusTcpPage(ConnectionPage):
    DRIVER = "modbus_tcp"
    SECTIONS = [("Network", ["host", "port"]), MODBUS_SECTION]

    def build_extra(self, v):
        self.w["port"][1].setToolTip("502 is the Modbus TCP standard port")

    def summary(self, values):
        return f"{values['host']}:{values['port']} · unit {values['unit']}"

    def validate(self, values):
        host = values["host"]
        if not host:
            return "Enter the IP address or host name of the device."
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not all(p.replace("-", "").isalnum() for p in host.split(".")):
                return f"'{host}' is not an IP address or host name."
        return ""
