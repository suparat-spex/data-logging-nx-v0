"""Add / edit an asset (Assets): the settings pop-up, tabs style like devices and tags.

  General     name, parent (an asset, or the top level), description, colour (swatch + Default)
  Attributes  its tags (any category) with an optional short attribute name ("Speed" for
              StationA_P1_Speed); Add tags... (the window's tag picker), Remove

[Add asset] / [Save] stays off until it is valid: a name, unique among its siblings, a parent
that is not inside itself and not deeper than core.assets.MAX_DEPTH levels.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                               QLineEdit, QPushButton, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from nxlogger.core import assets as asm
from nxlogger.ui.dialogs.common import form_page
from nxlogger.ui.icons import swatch
from nxlogger.ui.theme.ui_kit import label, set_prop


class AssetDialog(QDialog):
    """a: the asset (a copy - apply(a) writes the fields); assets: the project's assets;
    key_label(key) -> "<source> · <tag>"; pick(chosen keys) -> keys | None (the tag picker);
    default_color: the theme's swatch colour for "Default"."""

    def __init__(self, parent, a: dict, assets: list[dict], key_label, pick, default_color: str = "#808080"):
        super().__init__(parent)
        self.a, self.assets, self.key_label, self.pick = dict(a), assets, key_label, pick
        self.default_color = default_color
        self._color = a.get("color") or ""
        self.setWindowTitle("Asset")
        tabs = self.tabs = QTabWidget()
        w, f = form_page()
        self.name = QLineEdit(a.get("name", "")); self.name.setPlaceholderText("e.g. Pump 01, Station A, Energy KPIs")
        self.parent_box = QComboBox()
        self.parent_box.addItem("(top level)", None)
        for x in sorted(assets, key=lambda y: asm.path(assets, y["id"]).lower()):
            if x["id"] != a["id"]:
                self.parent_box.addItem(asm.path(assets, x["id"]), x["id"])
        self.parent_box.setCurrentIndex(max(0, self.parent_box.findData(a.get("parent"))))
        self.desc = QLineEdit(a.get("description", "")); self.desc.setPlaceholderText("What it is, where ...")
        self.color_btn = QPushButton(); self.color_btn.clicked.connect(self._pick_color)
        reset = QPushButton("Default"); reset.clicked.connect(lambda: self._set_color(""))
        colour = QHBoxLayout(); colour.addWidget(self.color_btn); colour.addWidget(reset); colour.addStretch(1)
        self._set_color(self._color)
        f.addRow("Name", self.name)
        f.addRow("Parent", self.parent_box)
        f.addRow("Description", self.desc)
        f.addRow("Colour", colour)
        f.addRow("", label(f"Up to {asm.MAX_DEPTH} levels (site > area > unit > equipment). An asset references "
                           "tags - deleting it never deletes a tag.", muted=True, small=True))
        tabs.addTab(w, "General")
        aw = QWidget(); av = QVBoxLayout(aw); av.setContentsMargins(0, 0, 0, 0); av.setSpacing(4)
        head = QHBoxLayout()
        head.addWidget(label("Attributes - double-click a name to give it a short one", muted=True, small=True))
        head.addStretch(1)
        add = QPushButton("Add tags..."); add.clicked.connect(self._add)
        rm = QPushButton("Remove"); rm.clicked.connect(self._remove)
        head.addWidget(add); head.addWidget(rm)
        self.attrs = QTreeWidget(); self.attrs.setHeaderLabels(["Attribute", "Tag"]); self.attrs.setRootIsDecorated(False)
        self.attrs.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.attrs.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.attrs.header().resizeSection(0, 160)
        for x in a.get("attributes", []):
            self._row(x["key"], x.get("name", ""))
        av.addLayout(head); av.addWidget(self.attrs, 1)
        tabs.addTab(aw, "Attributes")
        self.err = label("", small=True); self.err.setObjectName("statusMsg")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addWidget(tabs, 1); lay.addWidget(self.err); lay.addWidget(bb)
        self.name.textChanged.connect(lambda _: self._check())
        self.parent_box.currentIndexChanged.connect(lambda _: self._check())
        self._check()

    def _row(self, key, name: str):
        lbl = self.key_label(tuple(key))
        it = QTreeWidgetItem([name or lbl.split(" · ")[-1], lbl])
        it.setData(0, Qt.ItemDataRole.UserRole, list(key))
        it.setData(1, Qt.ItemDataRole.UserRole, bool(name))          # a name of its own
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
        c = QColorDialog.getColor(QColor(self._color or self.default_color), self, "Asset colour")
        if c.isValid():
            self._set_color(c.name())

    def collect(self) -> dict:
        a = dict(self.a)
        attrs = []
        for it in self._items():
            key = it.data(0, Qt.ItemDataRole.UserRole)
            own = it.text(0).strip()
            default = self.key_label(tuple(key)).split(" · ")[-1]
            attrs.append({"key": list(key), "name": "" if own == default else own})
        a.update(name=self.name.text().strip(), parent=self.parent_box.currentData(),
                 description=self.desc.text().strip(), color=self._color, attributes=attrs)
        return a

    def apply(self, a: dict):
        a.update(self.collect())

    def _problem(self) -> str:
        a = self.collect()
        if not a["name"]:
            return "General: enter a name."
        if any(x["name"].lower() == a["name"].lower() and x.get("parent") == a["parent"] and x["id"] != a["id"]
               for x in self.assets):
            return "General: another asset here already has this name."
        if any(x["id"] == a["id"] for x in self.assets):           # an existing asset: not inside itself
            msg = asm.can_move(self.assets, a["id"], a["parent"])
            if msg:
                return f"General: {msg}"
        elif asm.depth(self.assets, a["parent"]) >= asm.MAX_DEPTH:
            return f"General: at most {asm.MAX_DEPTH} levels of assets."
        return ""

    def _check(self):
        msg = self._problem()
        set_prop(self.err, "level", "bad" if msg else "muted")
        n = self.attrs.topLevelItemCount()
        self.err.setText(f"✗ {msg}" if msg else f"✓ Ready · {n} attribute(s)")
        self.ok_btn.setEnabled(not msg)

    def _ok(self):
        self._check()
        if self.ok_btn.isEnabled():
            self.accept()
