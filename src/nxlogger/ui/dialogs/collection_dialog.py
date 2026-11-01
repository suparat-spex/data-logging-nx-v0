"""Add / edit a collection (Collections): the settings pop-up, tabs style like devices and tags.

  General  name, purpose (Report / KPI / Monitoring / Equipment), parent (Equipment only),
           description, colour (swatch + Default)
  Tags     its tags (any category) with an optional short name ("Speed" for StationA_P1_Speed);
           Add tags... (the window's tag picker), Remove

[Add collection] / [Save] stays off until it is valid: a name, unique among its siblings, a
parent only for Equipment, not inside itself and not deeper than tag_collections.MAX_DEPTH.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                               QLineEdit, QPushButton, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from nxlogger.core import tag_collections as tc
from nxlogger.ui.dialogs.common import form_page
from nxlogger.ui.icons import swatch
from nxlogger.ui.theme.ui_kit import label, set_prop

PURPOSE_NOTE = {"report": "Gets a statistics logging profile (15 min) - its tags as they change.",
                "kpi": "Its values are shown on the Start Page and in the Monitor bar.",
                "monitoring": "New view / Monitor show it in one click.",
                "equipment": "Can have sub-collections: site > area > unit > equipment."}


class CollectionDialog(QDialog):
    """c: the collection (a copy - apply(c) writes the fields); cols: the project's collections;
    key_label(key) -> "<source> · <tag>"; pick(chosen keys) -> keys | None (the tag picker);
    default_color: the theme's swatch colour for "Default"."""

    def __init__(self, parent, c: dict, cols: list[dict], key_label, pick, default_color: str = "#808080"):
        super().__init__(parent)
        self.c, self.cols, self.key_label, self.pick = dict(c), cols, key_label, pick
        self.default_color = default_color
        self._color = c.get("color") or ""
        self.existing = any(x["id"] == c["id"] for x in cols)
        self.setWindowTitle("Collection")
        tabs = self.tabs = QTabWidget()
        w, f = form_page()
        self.name = QLineEdit(c.get("name", "")); self.name.setPlaceholderText("e.g. Plant KPIs, Daily energy, Pump 01")
        self.purpose = QComboBox()
        for key, name, _icon, _what in tc.PURPOSES:
            self.purpose.addItem(name, key)
        self.purpose.setCurrentIndex(max(0, self.purpose.findData(c.get("purpose", "monitoring"))))
        self.note = label("", muted=True, small=True); self.note.setWordWrap(True)
        self.parent_box = QComboBox()
        self.desc = QLineEdit(c.get("description", "")); self.desc.setPlaceholderText("What it is for ...")
        self.color_btn = QPushButton(); self.color_btn.clicked.connect(self._pick_color)
        reset = QPushButton("Default"); reset.clicked.connect(lambda: self._set_color(""))
        colour = QHBoxLayout(); colour.addWidget(self.color_btn); colour.addWidget(reset); colour.addStretch(1)
        self._set_color(self._color)
        f.addRow("Name", self.name)
        f.addRow("Purpose", self.purpose)
        f.addRow("", self.note)
        f.addRow("Parent", self.parent_box)
        f.addRow("Description", self.desc)
        f.addRow("Colour", colour)
        f.addRow("", label("A collection references tags - a tag can be in several collections, and deleting a "
                           "collection never deletes a tag.", muted=True, small=True))
        tabs.addTab(w, "General")
        aw = QWidget(); av = QVBoxLayout(aw); av.setContentsMargins(0, 0, 0, 0); av.setSpacing(4)
        head = QHBoxLayout()
        head.addWidget(label("Double-click a name to give it a short one (Speed, Status ...)", muted=True, small=True))
        head.addStretch(1)
        add = QPushButton("Add tags..."); add.clicked.connect(self._add)
        rm = QPushButton("Remove"); rm.clicked.connect(self._remove)
        head.addWidget(add); head.addWidget(rm)
        self.attrs = QTreeWidget(); self.attrs.setHeaderLabels(["Name", "Tag"]); self.attrs.setRootIsDecorated(False)
        self.attrs.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.attrs.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.attrs.header().resizeSection(0, 160)
        for x in c.get("attributes", []):
            self._row(x["key"], x.get("name", ""))
        av.addLayout(head); av.addWidget(self.attrs, 1)
        tabs.addTab(aw, "Tags")
        self.err = label("", small=True); self.err.setObjectName("statusMsg")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addWidget(tabs, 1); lay.addWidget(self.err); lay.addWidget(bb)
        self._fill_parents(c.get("parent"))
        self.name.textChanged.connect(lambda _: self._check())
        self.purpose.currentIndexChanged.connect(lambda _: (self._fill_parents(self.parent_box.currentData()),
                                                            self._check()))
        self.parent_box.currentIndexChanged.connect(lambda _: self._check())
        self._check()

    def _fill_parents(self, current):
        """Parents: Equipment collections only (the other purposes are flat lists)."""
        nested = self.purpose.currentData() in tc.NESTED
        self.parent_box.blockSignals(True)
        self.parent_box.clear()
        self.parent_box.addItem("(top level)", None)
        if nested:
            for x in tc.ordered(self.cols, "equipment"):
                if x["id"] != self.c["id"]:
                    self.parent_box.addItem(tc.path(self.cols, x["id"]), x["id"])
        self.parent_box.setCurrentIndex(max(0, self.parent_box.findData(current)) if nested else 0)
        self.parent_box.setEnabled(nested)
        self.parent_box.blockSignals(False)
        self.note.setText(PURPOSE_NOTE[self.purpose.currentData()])

    def _row(self, key, name: str):
        lbl = self.key_label(tuple(key))
        it = QTreeWidgetItem([name or lbl.split(" · ")[-1], lbl])
        it.setData(0, Qt.ItemDataRole.UserRole, list(key))
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
        self.attrs.addTopLevelItem(it)

    def _items(self):
        return [self.attrs.topLevelItem(i) for i in range(self.attrs.topLevelItemCount())]

    def _add(self):
        have = [it.data(0, Qt.ItemDataRole.UserRole) for it in self._items()]
        keys = self.pick([tuple(k) for k in have])
        if keys is None:
            return
        for k in keys:
            if list(k) not in have:
                self._row(k, "")
        self._check()

    def _remove(self):
        for it in self.attrs.selectedItems():
            self.attrs.takeTopLevelItem(self.attrs.indexOfTopLevelItem(it))
        self._check()

    def _set_color(self, c: str):
        self._color = c
        self.color_btn.setIcon(swatch(c or self.default_color, 14))
        self.color_btn.setText(c or "Default (theme)")

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self._color or self.default_color), self, "Collection colour")
        if c.isValid():
            self._set_color(c.name())

    def collect(self) -> dict:
        c = dict(self.c)
        attrs = []
        for it in self._items():
            key = it.data(0, Qt.ItemDataRole.UserRole)
            own = it.text(0).strip()
            default = self.key_label(tuple(key)).split(" · ")[-1]
            attrs.append({"key": list(key), "name": "" if own == default else own})
        purpose = self.purpose.currentData()
        c.update(name=self.name.text().strip(), purpose=purpose,
                 parent=self.parent_box.currentData() if purpose in tc.NESTED else None,
                 description=self.desc.text().strip(), color=self._color, attributes=attrs)
        return c

    def apply(self, c: dict):
        c.update(self.collect())

    def _problem(self) -> str:
        c = self.collect()
        if not c["name"]:
            return "General: enter a name."
        if any(x["name"].lower() == c["name"].lower() and x.get("parent") == c["parent"] and
               x["purpose"] == c["purpose"] and x["id"] != c["id"] for x in self.cols):
            return "General: another collection here already has this name."
        if self.existing:
            if c["purpose"] not in tc.NESTED and tc.children(self.cols, c["id"]):
                return "General: it has sub-collections - only Equipment can; move them first."
            if c["purpose"] in tc.NESTED:
                msg = tc.can_move([{**x, "purpose": c["purpose"]} if x["id"] == c["id"] else x for x in self.cols],
                                  c["id"], c["parent"])
                if msg:
                    return f"General: {msg}"
        elif tc.depth(self.cols, c["parent"]) >= tc.MAX_DEPTH:
            return f"General: at most {tc.MAX_DEPTH} levels of equipment."
        return ""

    def _check(self):
        msg = self._problem()
        set_prop(self.err, "level", "bad" if msg else "muted")
        n = self.attrs.topLevelItemCount()
        self.err.setText(f"✗ {msg}" if msg else f"✓ Ready · {n} tag(s)")
        self.ok_btn.setEnabled(not msg)

    def _ok(self):
        self._check()
        if self.ok_btn.isEnabled():
            self.accept()
