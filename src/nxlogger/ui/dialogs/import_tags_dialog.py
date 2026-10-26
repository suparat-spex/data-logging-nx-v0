"""Import tags into one device (CSV tag list / device JSON) - an action pop-up like Import
devices: choose the file, it is checked at once (core/tags.read_tags_file - any problem lists
every reason and Import stays disabled), tick the tags and what to do when a name exists
(add a copy, replace it, skip it)."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QPushButton,
                               QTreeWidget, QTreeWidgetItem)

from nxlogger.core.tags import DeviceCfg, DeviceFileError, describe_address, driver_label, read_tags_file
from nxlogger.ui.dialogs.common import ActionDialog
from nxlogger.ui.theme.ui_kit import label

CONFLICT = {"copy": "Add as a copy (new name)",
            "replace": "Replace the existing tag (keeps alarms, views, logging)",
            "skip": "Skip it"}
FILTER = "Tag files (*.csv *.json *.nxl);;CSV tag list (*.csv);;Device export (*.json *.nxl)"


class ImportTagsDialog(ActionDialog):
    def __init__(self, parent, dev: DeviceCfg, start_dir: str = "", path: str = ""):
        super().__init__(parent, "Import tags",
                         f"Into {dev.name} ({driver_label(dev.driver)}): a CSV tag list or a device JSON export. "
                         "The file is checked first.", "Import", 682, icon="download")
        self.dev, self.start_dir = dev, start_dir
        self.taken = {t.name for t in dev.tags}
        self.tags: list = []
        self.path = ""
        row = QHBoxLayout(); row.setSpacing(4)
        self.file = QLineEdit(); self.file.setReadOnly(True); self.file.setPlaceholderText("Choose a .csv or .json file")
        browse = QPushButton("Browse..."); browse.clicked.connect(self._browse)
        row.addWidget(self.file, 1); row.addWidget(browse)
        self.form.addRow("Device", label(f"{dev.name} · {driver_label(dev.driver)} · {dev.topic}", muted=True))
        self.form.addRow("File", row)
        self.check = label("", small=True); self.check.setObjectName("statusMsg"); self.check.setWordWrap(True)
        self.form.addRow("Check", self.check)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Group", "Address", "Unit", ""])
        self.tree.setRootIsDecorated(False); self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for c, w in enumerate((150, 100, 200, 50)):
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
        path, _ = QFileDialog.getOpenFileName(self, "Import tags", self.path or self.start_dir, FILTER)
        if path:
            self.load(path)

    def load(self, path: str):
        """Check the file; fill the list only when it passes."""
        self.path, self.tags = path, []
        self.file.setText(path)
        self.tree.clear()
        if not path:
            self._set_check("", "")
        else:
            try:
                self.tags = read_tags_file(path, self.dev)
            except DeviceFileError as e:
                self._set_check("✗ Not imported - fix the file:\n" + "\n".join(f"• {p}" for p in e.problems[:12])
                                + (f"\n• ... {len(e.problems) - 12} more" if len(e.problems) > 12 else ""), "bad")
            else:
                kind = "CSV tag list" if path.lower().endswith(".csv") else "device export"
                self._set_check(f"✓ {len(self.tags)} tag(s) · {kind} · {os.path.basename(path)}", "ok")
        dup = 0
        self.tree.blockSignals(True)
        for i, t in enumerate(self.tags):
            exists = t.name in self.taken
            dup += exists
            it = QTreeWidgetItem([t.name, t.group, describe_address(self.dev.driver, "", t), t.unit,
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
        self.ok.setText(f"Import {n} tag{'s' if n != 1 else ''}" if self.tags else "Import")
        self.ok.setEnabled(n > 0)

    def validate(self) -> str:
        return "" if self.chosen() else "Choose a valid file and at least one tag."

    def chosen(self) -> list:
        return [self.tags[it.data(0, Qt.ItemDataRole.UserRole)]
                for it in (self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount()))
                if it.checkState(0) == Qt.CheckState.Checked]

    def mode(self) -> str:
        return self.conflict.currentData()
