"""Simulation: no connection - a built-in signal generator (each tag picks its signal)."""
from __future__ import annotations

from nxlogger.core.tags import SIM_WAVES
from nxlogger.ui.dialogs.device_pages.base import ConnectionPage
from nxlogger.ui.theme.ui_kit import label


class SimPage(ConnectionPage):
    DRIVER = "sim"

    def build_extra(self, v):
        form = self.section(v, "Signals")
        text = label("Nothing to connect. Each tag chooses its signal - " + ", ".join(SIM_WAVES)
                     + " - with amplitude, frequency, offset and noise (Tags tab, tag settings).", muted=True)
        text.setWordWrap(True)
        form.addRow(text)

    def summary(self, values):
        return "built-in generator (no network)"
