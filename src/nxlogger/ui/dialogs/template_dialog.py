"""Add / edit an asset template (Assets > Templates): the settings pop-up, tabs style.

  General     name, type (Machine / Station / Unit), icon (assets.ICONS; Default = the type's),
              description, how many machines use it
  Attributes  name, unit, KPI, match (the tag name pattern Map tags... uses: "*_kW", "*speed*|*Hz*")
              - Add, Remove; double-click to edit

A machine with the template gets its attributes as slots; Map tags... fills them by the patterns.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QGridLayout,
                               QHBoxLayout, QLineEdit, QPushButton, QTabWidget, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.core import assets as am
from nxlogger.ui.dialogs.common import form_page
from nxlogger.ui.theme.ui_kit import T, label, make_icon, set_prop

ICON_COLS = 10


class TemplateDialog(QDialog):
    """t: the template (a copy - apply(t) writes the fields); templates: the project's; used: how
    many machines use it."""

    def __init__(self, parent, t: dict, templates: list[dict], used: int = 0):
        super().__init__(parent)
        self.t, self.templates = dict(t), templates
        self._icon = t.get("icon") or ""
        self.setWindowTitle("Template")
        tabs = QTabWidget()

        w, f = form_page()
        self.name = QLineEdit(t.get("name", "")); self.name.setPlaceholderText("e.g. Compressor, Power meter")
        self.type = QComboBox()
        for key, name, icon in am.LEVEL3:
            self.type.addItem(make_icon(icon, T["text"]), name, key)
        self.type.setCurrentIndex(max(0, self.type.findData(t.get("type"))))
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
        self.icon_default = QPushButton("Default"); self.icon_default.clicked.connect(lambda: self._set_icon(""))
        irow = QHBoxLayout(); irow.addWidget(self.icon_default); irow.addStretch(1)
        iv.addLayout(irow)
        self.desc = QLineEdit(t.get("description", "")); self.desc.setPlaceholderText("What it models ...")
        f.addRow("Name", self.name)
        f.addRow("Type", self.type)
        f.addRow("Icon", icons)
        f.addRow("Description", self.desc)
        f.addRow("Used by", label(f"{used} machine(s)", muted=True))
        tabs.addTab(w, "General")

        aw = QWidget(); av = QVBoxLayout(aw); av.setContentsMargins(0, 0, 0, 0); av.setSpacing(4)
        head = QHBoxLayout()
        head.addWidget(label("Match: the tag names Map tags... looks for (* = anything, | = or)", muted=True,
                             small=True))
        head.addStretch(1)
        add = QPushButton("Add"); add.clicked.connect(lambda: self._row({"name": "", "unit": "", "kpi": False,
                                                                         "match": ""}, edit=True))
        rm = QPushButton("Remove"); rm.clicked.connect(self._remove)
        head.addWidget(add); head.addWidget(rm)
        self.attrs = QTreeWidget(); self.attrs.setHeaderLabels(["Attribute", "Unit", "KPI", "Match"])
        self.attrs.setRootIsDecorated(False)
        self.attrs.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.attrs.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        for c, wd in enumerate((160, 70, 50)):
            self.attrs.header().resizeSection(c, wd)
        for x in t.get("attributes", []):
            self._row(x)
        self.attrs.itemChanged.connect(lambda *_: self._check())
        av.addLayout(head); av.addWidget(self.attrs, 1)
        tabs.addTab(aw, "Attributes")

        self.err = label("", small=True); self.err.setObjectName("statusMsg")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addWidget(tabs, 1); lay.addWidget(self.err); lay.addWidget(bb)
        self._paint_icons()
        self.name.textChanged.connect(lambda _: self._check())
        self.type.currentIndexChanged.connect(lambda _: self._paint_icons())
        self._check()

    def _set_icon(self, name: str):
        self._icon = name if name in am.ICONS else ""
        self._paint_icons()

    def _paint_icons(self):
        shown = self._icon or am.TYPE[self.type.currentData()][2]
        for n, b in self.icon_btns.items():
            b.setIcon(make_icon(n, T["text"] if n == shown else T["muted"]))
            b.setChecked(n == shown)
        self.icon_default.setEnabled(bool(self._icon))

    def _row(self, x: dict, edit: bool = False):
        it = QTreeWidgetItem([x.get("name", ""), x.get("unit", ""), "", x.get("match", "")])
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(2, Qt.CheckState.Checked if x.get("kpi") else Qt.CheckState.Unchecked)
        self.attrs.addTopLevelItem(it)
        if edit:
            self.attrs.setCurrentItem(it); self.attrs.editItem(it, 0)
        self._check()

    def _items(self):
        return [self.attrs.topLevelItem(i) for i in range(self.attrs.topLevelItemCount())]

    def _remove(self):
        for it in self.attrs.selectedItems():
            self.attrs.takeTopLevelItem(self.attrs.indexOfTopLevelItem(it))
        self._check()

    def collect(self) -> dict:
        t = dict(self.t)
        t.update(name=self.name.text().strip(), type=self.type.currentData(), icon=self._icon,
                 description=self.desc.text().strip(),
                 attributes=[am.new_slot(it.text(0).strip(), it.text(1).strip(),
                                         it.checkState(2) == Qt.CheckState.Checked, it.text(3).strip())
                             for it in self._items() if it.text(0).strip()])
        return t

    def apply(self, t: dict):
        t.update(self.collect())

    def _problem(self) -> str:
        t = self.collect()
        if not t["name"]:
            return "General: enter a name."
        if any(x["name"].lower() == t["name"].lower() and x["id"] != t["id"] for x in self.templates):
            return "General: another template already has this name."
        names = [s["name"].lower() for s in t["attributes"]]
        if len(names) != len(set(names)):
            return "Attributes: each name once."
        return ""

    def _check(self):
        if not hasattr(self, "ok_btn"):
            return
        msg = self._problem()
        set_prop(self.err, "level", "bad" if msg else "muted")
        self.err.setText(f"✗ {msg}" if msg else f"✓ Ready · {len(self.collect()['attributes'])} attribute(s)")
        self.ok_btn.setEnabled(not msg)

    def _ok(self):
        self._check()
        if self.ok_btn.isEnabled():
            self.accept()
