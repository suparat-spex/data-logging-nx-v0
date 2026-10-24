"""Broker connection dialog: hosts the main window's MQTT panel (kept alive, shown on demand)."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout


class ConnectionDialog(QDialog):
    def __init__(self, parent, panel):
        super().__init__(parent)
        self.setWindowTitle("Connection")
        self.setMinimumWidth(440)
        v = QVBoxLayout(self)
        v.addWidget(panel)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.hide)
        v.addWidget(close)
