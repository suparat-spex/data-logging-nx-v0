"""OPC UA client: read nodes of a server. Anonymous or user name / password login."""
from __future__ import annotations

from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QRadioButton, QWidget

from nxlogger.ui.dialogs.device_pages.base import ConnectionPage


class OpcUaPage(ConnectionPage):
    DRIVER = "opcua"
    SECTIONS = [("Server", ["endpoint", "timeout_s"]), ("Authentication", ["username", "password"])]

    def build_extra(self, v):
        form = self._form_of["username"]
        box = QWidget(); h = QHBoxLayout(box); h.setContentsMargins(0, 0, 0, 0)
        self.anon, user = QRadioButton("Anonymous"), QRadioButton("User name and password")
        grp = QButtonGroup(box); grp.addButton(self.anon); grp.addButton(user)
        h.addWidget(self.anon); h.addWidget(user); h.addStretch(1)
        form.insertRow(0, "Login", box)
        (user if self.w["username"][1].text() else self.anon).setChecked(True)
        self.anon.toggled.connect(lambda _: self.changed.emit())

    def on_change(self, values):
        for k in ("username", "password"):
            self.w[k][1].setEnabled(not self.anon.isChecked())

    def values(self):
        vals = super().values()
        if getattr(self, "anon", None) is not None and self.anon.isChecked():
            vals["username"] = vals["password"] = ""          # anonymous: no credentials saved
        return vals

    def summary(self, values):
        return values["endpoint"] + (f" as {values['username']}" if values["username"] else " (anonymous)")

    def validate(self, values):
        if not values["endpoint"].startswith("opc.tcp://"):
            return "The endpoint starts with opc.tcp:// (e.g. opc.tcp://192.168.1.10:4840)."
        if not self.anon.isChecked() and not values["username"]:
            return "Enter the user name, or choose Anonymous."
        return ""
