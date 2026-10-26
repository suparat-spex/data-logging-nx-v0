"""Export tags - an action pop-up like Export devices: tick the tags (grouped by device), choose
CSV (tag list) or JSON (the devices with the ticked tags), then [Export N tags...] asks where
to save."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QHBoxLayout, QPushButton, QRadioButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from nxlogger.core.tags import describe_address, driver_label
from nxlogger.ui.dialogs.common import ActionDialog

FORMATS = {"csv": "CSV - tag list (spreadsheets, Import tags)",
           "json": "JSON - the devices with these tags (Import tags or Import devices, other projects)"}


class ExportTagsDialog(ActionDialog):
    """devices: the devices to offer; chosen: [(device id, tag index)] ticked at the start
    (empty: every tag)."""

    def __init__(self, parent, devices: list, chosen: list[tuple[str, int]] = (), fmt: str = "csv"):
        super().__init__(parent, "Export tags",
                         "Tick the tags and choose the format, then where to save.", "Export", 682, icon="upload")
        self.devices = devices
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Group", "Address", "Unit"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for c, w in enumerate((180, 100, 200)):
            self.tree.header().resizeSection(c, w)
        self.tree.setMinimumHeight(220)
        some = {tuple(k) for k in chosen}
        self.tree.blockSignals(True)
        for d in devices:
            di = QTreeWidgetItem([f"{d.name}  ({len(d.tags)})", driver_label(d.driver), d.topic, ""])
            di.setFlags(di.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
            self.tree.addTopLevelItem(di)
            for t in d.tags:
                it = QTreeWidgetItem([t.name, t.group, describe_address(d.driver, "", t), t.unit])
                it.setData(0, Qt.ItemDataRole.UserRole, (d.id, t.index))
                on = not some or (d.id, t.index) in some
                it.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                di.addChild(it)
            di.setExpanded(len(devices) == 1 or any(k[0] == d.id for k in some))
        self.tree.blockSignals(False)
        self.tree.itemChanged.connect(lambda *_: self._count())
        tick = QHBoxLayout(); tick.setSpacing(4)
        for text, on in (("Select all", True), ("Select none", False)):
            b = QPushButton(text); b.clicked.connect(lambda _=False, v=on: self._tick_all(v))
            tick.addWidget(b)
        tick.addStretch(1)
        box = QVBoxLayout(); box.setSpacing(4)
        box.addWidget(self.tree); box.addLayout(tick)
        self.form.addRow("Tags", box)
        self.fmt = QButtonGroup(self)
        fv = QVBoxLayout(); fv.setSpacing(2)
        for k, text in FORMATS.items():
            r = QRadioButton(text); r.setProperty("fmt", k); r.setChecked(k == fmt)
            self.fmt.addButton(r); fv.addWidget(r)
        self.form.addRow("Format", fv)
        self._count()

    def _items(self):
        for i in range(self.tree.topLevelItemCount()):
            di = self.tree.topLevelItem(i)
            for k in range(di.childCount()):
                yield di.child(k)

    def _tick_all(self, on: bool):
        for it in self._items():
            it.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _count(self):
        n = len(self.chosen())
        self.ok.setText(f"Export {n} tag{'s' if n != 1 else ''}...")
        self.ok.setEnabled(n > 0)

    def validate(self) -> str:
        return "" if self.chosen() else "Tick at least one tag."

    def chosen(self) -> list[tuple[str, int]]:
        return [tuple(it.data(0, Qt.ItemDataRole.UserRole)) for it in self._items()
                if it.checkState(0) == Qt.CheckState.Checked]

    def format(self) -> str:
        return self.fmt.checkedButton().property("fmt")
