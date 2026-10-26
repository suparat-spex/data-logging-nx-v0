"""Export devices - an action pop-up: tick the devices, choose JSON (with tags) or CSV (device
list), then [Export N devices...] asks where to save."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QHBoxLayout, QPushButton, QRadioButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from nxlogger.core.tags import driver_label
from nxlogger.ui.dialogs.common import ActionDialog

FORMATS = {"json": "JSON - devices with settings, groups and tags (re-import, backup, other projects)",
           "csv": "CSV - device list without tags (spreadsheets)"}


class ExportDevicesDialog(ActionDialog):
    def __init__(self, parent, devices: list, chosen: list[str] = (), fmt: str = "json"):
        super().__init__(parent, "Export devices",
                         "Tick the devices and choose the format, then where to save. Passwords are "
                         "never written to export files.",
                         "Export", 682, icon="upload")
        self.devices = devices
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Connector", "Address", "Tags"])
        self.tree.setRootIsDecorated(False); self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for c, w in enumerate((150, 110, 190)):
            self.tree.header().resizeSection(c, w)
        self.tree.setMinimumHeight(200)
        some = bool(chosen)                       # nothing selected in the table: all ticked
        for d in devices:
            it = QTreeWidgetItem([d.name, driver_label(d.driver), d.topic, str(len(d.tags))])
            it.setData(0, Qt.ItemDataRole.UserRole, d.id)
            it.setCheckState(0, Qt.CheckState.Checked if (d.id in chosen or not some) else Qt.CheckState.Unchecked)
            self.tree.addTopLevelItem(it)
        self.tree.itemChanged.connect(lambda *_: self._count())
        tick = QHBoxLayout(); tick.setSpacing(4)
        for text, on in (("Select all", True), ("Select none", False)):
            b = QPushButton(text); b.clicked.connect(lambda _=False, v=on: self._tick_all(v))
            tick.addWidget(b)
        tick.addStretch(1)
        box = QVBoxLayout(); box.setSpacing(4)
        box.addWidget(self.tree); box.addLayout(tick)
        self.form.addRow("Devices", box)
        self.fmt = QButtonGroup(self)
        fv = QVBoxLayout(); fv.setSpacing(2)
        for k, text in FORMATS.items():
            r = QRadioButton(text); r.setProperty("fmt", k); r.setChecked(k == fmt)
            self.fmt.addButton(r); fv.addWidget(r)
        self.form.addRow("Format", fv)
        self._count()

    def _tick_all(self, on: bool):
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _count(self):
        n = len(self.chosen())
        self.ok.setText(f"Export {n} device{'s' if n != 1 else ''}...")
        self.ok.setEnabled(n > 0)

    def validate(self) -> str:
        return "" if self.chosen() else "Tick at least one device."

    def chosen(self) -> list[str]:
        items = (self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount()))
        return [it.data(0, Qt.ItemDataRole.UserRole) for it in items if it.checkState(0) == Qt.CheckState.Checked]

    def format(self) -> str:
        return self.fmt.checkedButton().property("fmt")
