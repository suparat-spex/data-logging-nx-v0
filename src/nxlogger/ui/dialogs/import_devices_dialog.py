"""Import devices (JSON / CSV) - an action pop-up: choose the file, it is checked at once
(core/tags.read_devices_file - any problem lists every reason and Import stays disabled),
pick the devices and what to do when a name exists (add a copy, replace it, skip it)."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QPushButton,
                               QTreeWidget, QTreeWidgetItem)

from nxlogger.core.tags import DeviceFileError, driver_label, read_devices_file
from nxlogger.ui.dialogs.common import ActionDialog
from nxlogger.ui.theme.ui_kit import label

CONFLICT = {"copy": "Add as a copy (new name)",
            "replace": "Replace the existing device (keeps alarms, views, logging)",
            "skip": "Skip it"}
FILTER = "Device files (*.json *.csv *.nxl);;JSON (*.json *.nxl);;CSV (*.csv)"


class ImportDevicesDialog(ActionDialog):
    def __init__(self, parent, taken: set[str], start_dir: str = "", path: str = ""):
        super().__init__(parent, "Import devices",
                         "JSON (devices with tags) or CSV (device list). The file is checked first; "
                         "passwords are not in export files.", "Import", 682, icon="download")
        self.taken, self.start_dir = taken, start_dir
        self.devices: list = []
        self.path = ""
        row = QHBoxLayout(); row.setSpacing(4)
        self.file = QLineEdit(); self.file.setReadOnly(True); self.file.setPlaceholderText("Choose a .json, .nxl or .csv file")
        browse = QPushButton("Browse..."); browse.clicked.connect(self._browse)
        row.addWidget(self.file, 1); row.addWidget(browse)
        self.form.addRow("File", row)
        self.check = label("", small=True); self.check.setObjectName("statusMsg"); self.check.setWordWrap(True)
        self.form.addRow("Check", self.check)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Connector", "Address", "Tags", ""])
        self.tree.setRootIsDecorated(False); self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for c, w in enumerate((150, 110, 200, 44)):
            self.tree.header().resizeSection(c, w)
        self.tree.setMinimumHeight(180)
        self.tree.itemChanged.connect(lambda *_: self._count())
        self.form.addRow(self.tree)
        self.conflict = QComboBox()
        for k, text in CONFLICT.items():
            self.conflict.addItem(text, k)
        self.conflict_lbl = label("Name exists")
        self.form.addRow(self.conflict_lbl, self.conflict)
        self.load(path)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import devices", self.path or self.start_dir, FILTER)
        if path:
            self.load(path)

    def load(self, path: str):
        """Check the file; fill the list only when it passes."""
        self.path, self.devices = path, []
        self.file.setText(path)
        self.tree.clear()
        if not path:
            self._set_check("", "")
        else:
            try:
                self.devices = read_devices_file(path)
            except DeviceFileError as e:
                self._set_check("✗ Not imported - fix the file:\n" + "\n".join(f"• {p}" for p in e.problems[:12])
                                + (f"\n• ... {len(e.problems) - 12} more" if len(e.problems) > 12 else ""), "bad")
            else:
                kind = "CSV device list (no tags)" if path.lower().endswith(".csv") else "JSON with tags"
                self._set_check(f"✓ {len(self.devices)} device(s) · {kind} · {os.path.basename(path)}", "ok")
        dup = 0
        self.tree.blockSignals(True)
        for i, d in enumerate(self.devices):
            exists = d.name in self.taken
            dup += exists
            it = QTreeWidgetItem([d.name, driver_label(d.driver), d.topic, str(len(d.tags)),
                                  "exists" if exists else ""])
            it.setData(0, Qt.ItemDataRole.UserRole, i)
            it.setCheckState(0, Qt.CheckState.Checked)
            self.tree.addTopLevelItem(it)
        self.tree.blockSignals(False)
        self.conflict_lbl.setText(f"Name exists ({dup})")
        self.conflict.setEnabled(dup > 0)
        self._count()

    def _set_check(self, text: str, level: str):
        self.check.setText(text)
        self.check.setProperty("level", level or "muted")
        self.check.style().unpolish(self.check); self.check.style().polish(self.check)

    def _count(self):
        n = len(self.chosen())
        self.ok.setText(f"Import {n} device{'s' if n != 1 else ''}" if self.devices else "Import")
        self.ok.setEnabled(n > 0)

    def validate(self) -> str:
        return "" if self.chosen() else "Choose a valid file and at least one device."

    def chosen(self) -> list:
        return [self.devices[it.data(0, Qt.ItemDataRole.UserRole)]
                for it in (self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount()))
                if it.checkState(0) == Qt.CheckState.Checked]

    def mode(self) -> str:
        return self.conflict.currentData()

    @property
    def is_csv(self) -> bool:
        return self.path.lower().endswith(".csv")
