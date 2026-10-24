"""Help -> About NX Logger."""
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from nxlogger import APP_NAME, __version__, config


def show_about(parent):
    src = config.get().source or "built-in defaults"
    QMessageBox.about(parent, f"About {APP_NAME}",
                      f"<h3>{APP_NAME} {__version__}</h3><p>Data logger for tags from Modbus TCP / RTU, MQTT, "
                      "OPC UA and simulated devices: live trends and grid views, 4-level alarms, "
                      "statistics, recording to CSV / raw waveform packets, AI/ML data log.</p>"
                      f"<p>Settings: {src}</p>")
