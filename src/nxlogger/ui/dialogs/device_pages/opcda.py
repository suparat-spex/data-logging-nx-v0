"""OPC DA (planned): classic COM servers - configurable, cannot connect yet."""
from __future__ import annotations

from nxlogger.ui.dialogs.device_pages.base import ConnectionPage


class OpcDaPage(ConnectionPage):
    DRIVER = "opcda"
    SECTIONS = [("Server", ["host", "server"])]
    LABELS = {"server": "Server ProgID"}

    def summary(self, values):
        return f"{values['host']} / {values['server']} (not available yet)"
