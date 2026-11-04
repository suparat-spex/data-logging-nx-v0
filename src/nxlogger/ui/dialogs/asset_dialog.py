"""Add / edit an asset (Assets): the settings pop-up, tabs style like devices and tags.

  General  name, type (an area: Area / Zone / Production; a machine: Machine / Station / Unit),
           area (machines only), template (machines only; a new one adds its attributes as slots),
           icon (the app's own icons - assets.ICONS; Default = the type's), description
  Tags     its tags (any category) with a short name ("Power" for Comp1_kW) and KPI (Start Page,
           Monitor bar, the rollups); a template slot without a tag shows "Not mapped" (Map tags...
           in the Assets document fills them); Add tags... (the window's tag picker), Remove

[Add] / [Save] stays off until it is valid: a name, unique in its area (or among the areas).
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QGridLayout,
                               QHBoxLayout, QLineEdit, QPushButton, QTabWidget, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.core import assets as am
from nxlogger.ui.dialogs.common import form_page
from nxlogger.ui.theme.ui_kit import T, label, make_icon, set_prop

ICON_COLS = 10
NOT_MAPPED = "Not mapped"


class AssetDialog(QDialog):
    """a: the asset (a copy - apply(a) writes the fields); assets / templates: the project's;
    key_label(key) -> "<source> · <tag>"; pick(chosen keys) -> keys | None (the tag picker)."""

    def __init__(self, parent, a: dict, assets: list[dict], templates: list[dict], key_label, pick):
        super().__init__(parent)
        self.a, self.assets, self.templates, self.key_label, self.pick = dict(a), assets, templates, key_label, pick
        self._icon = a.get("icon") or ""
        self.lvl = am.level(a)
        self.setWindowTitle("Asset")
        tabs = self.tabs = QTabWidget()

        # General ---------------------------------------------------------------
        w, f = form_page()
        self.name = QLineEdit(a.get("name", ""))
        self.name.setPlaceholderText("e.g. Utilities, Line 1" if self.lvl == 2 else "e.g. Compressor 1, Press 01")
        self.type = QComboBox()
        for key, name, icon in am.types_of(self.lvl):
            self.type.addItem(make_icon(icon, T["text"]), name, key)
        self.type.setCurrentIndex(max(0, self.type.findData(a.get("type"))))
        self.area = QComboBox()
        self.template = QComboBox()
        self.template.addItem("None", "")
        for t in templates:
            self.template.addItem(make_icon(am.icon_of(t), T["text"]), t["name"], t["id"])
        self.template.setCurrentIndex(max(0, self.template.findData(a.get("template", ""))))
        icons = QWidget(); iv = QVBoxLayout(icons); iv.setContentsMargins(0, 0, 0, 0); iv.setSpacing(4)
        grid = QGridLayout(); grid.setSpacing(2)
        self.icon_group = QButtonGroup(self); self.icon_group.setExclusive(True)
        self.icon_btns: dict[str, QToolButton] = {}
        for i, name in enumerate(am.ICONS):
            b = QToolButton(); b.setCheckable(True); b.setAutoRaise(True); b.setToolTip(name)
            b.setIconSize(QSize(18, 18)); b.setFixedSize(30, 30)
            b.clicked.connect(lambda _=False, n=name: self._set_icon(n))
            self.icon_group.addButton(b)
            grid.addWidget(b, i // ICON_COLS, i % ICON_COLS)
            self.icon_btns[name] = b
        iv.addLayout(grid)
        irow = QHBoxLayout(); irow.setSpacing(6)
        self.icon_note = label("", muted=True, small=True)
        self.icon_default = QPushButton("Default"); self.icon_default.clicked.connect(lambda: self._set_icon(""))
        self.icon_default.setToolTip("The type's icon")
        irow.addWidget(self.icon_default); irow.addWidget(self.icon_note, 1)
        iv.addLayout(irow)
        self.desc = QLineEdit(a.get("description", "")); self.desc.setPlaceholderText("What it is ...")
        f.addRow("Name", self.name)
        f.addRow("Type", self.type)
        if self.lvl == 3:
            for x in am.areas(assets):
                self.area.addItem(make_icon(am.icon_of(x), T["text"]), x["name"], x["id"])
            self.area.setCurrentIndex(max(0, self.area.findData(a.get("parent"))))
            f.addRow("Area", self.area)
            f.addRow("Template", self.template)
        f.addRow("Icon", icons)
        f.addRow("Description", self.desc)
        f.addRow("", label("An asset references tags - a tag can be in several assets, and deleting an asset "
                           "never deletes a tag.", muted=True, small=True))
        tabs.addTab(w, "General")

        # Tags ------------------------------------------------------------------
        aw = QWidget(); av = QVBoxLayout(aw); av.setContentsMargins(0, 0, 0, 0); av.setSpacing(4)
        head = QHBoxLayout()
        head.addWidget(label("Double-click a name to give it a short one (Power, Speed ...) · KPI: Start Page, "
                             "Monitor bar, rollups", muted=True, small=True))
        head.addStretch(1)
        add = QPushButton("Add tags..."); add.clicked.connect(self._add)
        rm = QPushButton("Remove"); rm.clicked.connect(self._remove)
        head.addWidget(add); head.addWidget(rm)
        self.attrs = QTreeWidget(); self.attrs.setHeaderLabels(["Name", "Tag", "KPI"])
        self.attrs.setRootIsDecorated(False)
        self.attrs.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.attrs.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.attrs.header().resizeSection(0, 160); self.attrs.header().resizeSection(1, 260)
        for x in a.get("attributes", []):
            self._row(x["key"], x.get("name", ""), x.get("kpi", False))
        av.addLayout(head); av.addWidget(self.attrs, 1)
        tabs.addTab(aw, "Tags")

        self.err = label("", small=True); self.err.setObjectName("statusMsg")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addWidget(tabs, 1); lay.addWidget(self.err); lay.addWidget(bb)
        self._paint_icons()
        self.name.textChanged.connect(lambda _: self._check())
        self.type.currentIndexChanged.connect(lambda _: self._paint_icons())
        self.area.currentIndexChanged.connect(lambda _: self._check())
        self._check()

    # ---------------------------------------------------------------- general
    def _set_icon(self, name: str):
        self._icon = name if name in am.ICONS else ""
        self._paint_icons()

    def _paint_icons(self):
        shown = self._icon or am.TYPE[self.type.currentData()][2]
        for n, b in self.icon_btns.items():
            b.setIcon(make_icon(n, T["text"] if n == shown else T["muted"]))
            b.setChecked(n == shown)
        self.icon_default.setEnabled(bool(self._icon))
        self.icon_note.setText(f"Default: the {am.TYPE[self.type.currentData()][1]} icon" if not self._icon
                               else f"Icon: {self._icon}")

    # ---------------------------------------------------------------- tags
    def _row(self, key, name: str, kpi: bool):
        lbl = self.key_label(tuple(key)) if key else NOT_MAPPED
        it = QTreeWidgetItem([name or lbl.split(" · ")[-1], lbl, ""])
        it.setData(0, Qt.ItemDataRole.UserRole, list(key) if key else None)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(2, Qt.CheckState.Checked if kpi else Qt.CheckState.Unchecked)
        if not key:
            it.setForeground(1, QBrush(QColor(T["muted"])))
        self.attrs.addTopLevelItem(it)

    def _items(self):
        return [self.attrs.topLevelItem(i) for i in range(self.attrs.topLevelItemCount())]

    def _add(self):
        have = [it.data(0, Qt.ItemDataRole.UserRole) for it in self._items()]
        keys = self.pick([tuple(k) for k in have if k])
        if keys is None:
            return
        for k in keys:
            if list(k) not in have:
                self._row(k, "", False)
        self._check()

    def _remove(self):
        for it in self.attrs.selectedItems():
            self.attrs.takeTopLevelItem(self.attrs.indexOfTopLevelItem(it))
        self._check()

    # ---------------------------------------------------------------- result
    def collect(self) -> dict:
        a = dict(self.a)
        attrs = []
        for it in self._items():
            key = it.data(0, Qt.ItemDataRole.UserRole)
            own = it.text(0).strip()
            default = self.key_label(tuple(key)).split(" · ")[-1] if key else ""
            attrs.append({"key": list(key) if key else None, "name": "" if key and own == default else own,
                          "kpi": it.checkState(2) == Qt.CheckState.Checked})
        a.update(name=self.name.text().strip(), type=self.type.currentData(), icon=self._icon,
                 description=self.desc.text().strip(), attributes=attrs)
        if self.lvl == 3:
            a.update(parent=self.area.currentData(), template=self.template.currentData() or "")
        return a

    def apply(self, a: dict):
        """Write the fields; a template chosen now adds its attributes as slots."""
        new = self.collect()
        tid = new["template"] if self.lvl == 3 else ""
        old = a.get("template", "")
        a.update(new)
        t = next((x for x in self.templates if x["id"] == tid), None)
        if t is not None and tid != old:
            am.apply_template(a, t)

    def _problem(self) -> str:
        a = self.collect()
        if not a["name"]:
            return "General: enter a name."
        if self.lvl == 3 and not a["parent"]:
            return "General: add an area first - a machine goes into an area."
        if any(x["name"].lower() == a["name"].lower() and x.get("parent") == a.get("parent") and x["id"] != a["id"]
               for x in self.assets):
            return "General: another asset here already has this name."
        return ""

    def _check(self):
        if not hasattr(self, "ok_btn"):
            return
        msg = self._problem()
        set_prop(self.err, "level", "bad" if msg else "muted")
        n = self.attrs.topLevelItemCount()
        self.err.setText(f"✗ {msg}" if msg else f"✓ Ready · {n} tag(s)")
        self.ok_btn.setEnabled(not msg)

    def _ok(self):
        self._check()
        if self.ok_btn.isEnabled():
            self.accept()
