"""Add / edit a Virtual tag (Tags > Virtual): the settings pop-up, tabs style like the device and
tag pop-ups. The type is chosen before it opens and is fixed (like a device's connector).

  General      name, group, data type, unit, colour (swatch + Default, like device tags), description, enabled
  <type>       Value (constant) · Expression (formula) · Condition (logic) · Inputs (selector)
               · Conversion · Text map
  Calculation  update (any input changes / trigger tag / every N ms), bad input (not constants)
  Display      decimals

The result line and the inputs' values refresh twice a second; [Add tag] / [Save] stays off
until the definition is valid (the problem is named in the footer, its tab marked with ●).
The window passes the choices and helpers - dialogs never import each other.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDialogButtonBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPushButton,
                               QRadioButton, QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.core import channel_math as cm
from nxlogger.core import virtual_tags as vt
from nxlogger.ui.dialogs.common import form_page, spin
from nxlogger.ui.icons import swatch
from nxlogger.ui.theme.ui_kit import label, set_prop

TAB_OF = {"constant": "Value", "formula": "Expression", "logic": "Condition", "selector": "Inputs",
          "conversion": "Conversion", "textmap": "Text map"}
TOKENS = {"": "(none)", "ok": "Green (ok)", "warn": "Amber (warn)", "bad": "Red (bad)", "muted": "Grey (muted)"}
TEMPLATES = [("A + B", "A + B"), ("A − B", "A - B"), ("A × B", "A * B"), ("A ÷ B", "A / B"),
             ("(A + B) / 2", "(A + B) / 2"), ("√(A² + B²)", "sqrt(A**2 + B**2)"), ("Gain · offset", "A * 1.0 + 0.0"),
             ("Clip 0..100", "clip(A, 0, 100)"), ("If … then … else", 'if(A > 80, "High", "OK")')]
LOGIC_TEMPLATES = [("A and B", "A and B"), ("A or B", "A or B"), ("not A", "not A"), ("A > limit", "A > 50"),
                   ("Between", "10 <= A <= 90"), ("Text equals", 'A == "Auto"'), ("One of", "A in (1, 2)"),
                   ("Bit of a word", "bit(A, 0)")]


class VirtualTagDialog(QDialog):
    """v: the definition (a copy - apply(v) writes the fields). engine: the VirtualEngine (check /
    preview); choices: [(tid, idx, label, colour, unit)] it can read; names: {formula name: key};
    groups: existing group names; live(key) -> (value text, quality text, good, time);
    pick(choices, chosen, multi, title) -> keys | None (the window's tag picker)."""

    def __init__(self, parent, v: dict, engine, choices, names: dict, groups, live, pick, taken: set,
                 default_color: str = ""):
        super().__init__(parent)
        self.v, self.engine, self.names, self.live, self.pick, self.taken = dict(v), engine, names, live, pick, taken
        self.default_color = default_color or "#808080"    # the palette colour (Default)
        self._color = v.get("color") or self.default_color
        self.kind = v["kind"]
        self.choices = [c for c in choices if not (vt.is_vt(c[0]) and c[0] == vt.VT_PREFIX + v["id"])]
        self.labels = {(c[0], c[1]): c[2] for c in self.choices}
        self.setWindowTitle(vt.TYPES[self.kind][0])
        tabs = self.tabs = QTabWidget()
        tabs.addTab(self._general(groups), "General")
        tabs.addTab(self._type_page(), TAB_OF[self.kind])
        if self.kind not in vt.NO_INPUT:
            tabs.addTab(self._calculation(), "Calculation")
        tabs.addTab(self._display(), "Display")
        self._titles = [tabs.tabText(i) for i in range(tabs.count())]
        self.err = label("", small=True); self.err.setObjectName("statusMsg"); self.err.setWordWrap(True)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addWidget(tabs, 1); lay.addWidget(self.err); lay.addWidget(bb)
        self._ready = True                           # signals during construction: no preview yet
        self._timer = QTimer(self, interval=500, timeout=self._preview)
        self._timer.start()
        self._preview()

    # ------------------------------------------------------------------ pages
    def _general(self, groups):
        w, f = form_page()
        self.name = QLineEdit(self.v["name"]); self.name.setPlaceholderText("e.g. Total_Flow")
        self.group = QComboBox(); self.group.setEditable(True); self.group.addItem("")
        self.group.addItems(sorted(g for g in groups if g)); self.group.setCurrentText(self.v.get("group", ""))
        self.group.lineEdit().setPlaceholderText("(no group) - type a new one or pick")
        self.dtype = QComboBox()
        for k, t in vt.DTYPES.items():
            self.dtype.addItem(t, k)
        self.dtype.setCurrentIndex(max(0, self.dtype.findData(self.v.get("dtype", "number"))))
        fixed = {"logic": "bool", "textmap": "text", "conversion": "number", "selector": "number"}.get(self.kind)
        if fixed:                                    # the type decides it
            self.dtype.setCurrentIndex(self.dtype.findData(fixed)); self.dtype.setEnabled(False)
        self.unit = QComboBox(); self.unit.setEditable(True); self.unit.addItems(cm.UNITS)
        self.unit.setCurrentText(self.v.get("unit", ""))
        self.color_btn = QPushButton(); self.color_btn.clicked.connect(self._pick_color)
        self.color_btn.setToolTip("The colour of its line in trends and its swatch in tables")
        reset = QPushButton("Default"); reset.clicked.connect(lambda: self._set_color(self.default_color))
        reset.setToolTip("Back to the automatic palette colour")
        colour = QHBoxLayout(); colour.addWidget(self.color_btn); colour.addWidget(reset); colour.addStretch(1)
        self._set_color(self._color)
        self.desc = QLineEdit(self.v.get("description", ""))
        self.desc.setPlaceholderText("What it is, where it comes from ...")
        self.enabled = QCheckBox("Enabled - calculate it and show it in views", checked=self.v.get("enabled", True))
        f.addRow("Type", label(f"{vt.TYPES[self.kind][0]} - {vt.TYPES[self.kind][2]}", muted=True))
        f.addRow("Name", self.name)
        f.addRow("Group", self.group)
        f.addRow("Data type", self.dtype)
        f.addRow("Unit", self.unit)
        f.addRow("Colour", colour)
        f.addRow("Description", self.desc)
        f.addRow("", self.enabled)
        for sig in (self.name.textChanged, self.dtype.currentIndexChanged):
            sig.connect(lambda *_: self._preview())
        return w

    def _type_page(self):
        k = self.kind
        if k == "constant":
            w, f = form_page()
            val = self.v.get("value", 0.0)
            self.value = QLineEdit(str(val) if not isinstance(val, float) else f"{val:g}")
            self.value.textChanged.connect(lambda _: self._preview())
            f.addRow("Value", self.value)
            f.addRow("", label("Number: 0.0314 · Text: any text · Boolean: 1 or 0. The operator cannot change "
                               "it at runtime - use a Memory tag for that.", muted=True, small=True))
            self.result = label("", small=True); self.result.setObjectName("statusMsg")
            f.addRow("Result", self.result)
            return w
        page = QWidget(); v = QVBoxLayout(page); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
        if k in vt.ONE_INPUT:
            w, f = form_page()
            self.source = QComboBox()
            self.source.addItem("(choose the source tag)", None)
            for tid, idx, lbl, _c, _u in self.choices:
                self.source.addItem(lbl, [tid, idx])
            ins = self.v.get("inputs", [])
            if ins:
                self.source.setCurrentIndex(max(0, self.source.findData(list(ins[0]["key"]))))
            self.source.currentIndexChanged.connect(lambda _: self._preview())
            f.addRow("Source", self.source)
            self.src_value = label("", muted=True, small=True); f.addRow("", self.src_value)
            v.addWidget(w)
            v.addWidget(self._conversion() if k == "conversion" else self._textmap(), 1)
        else:
            v.addWidget(self._inputs(), 1)
            if k == "selector":
                w, f = form_page()
                self.rule = QComboBox()
                for key, t in vt.RULES.items():
                    self.rule.addItem(t, key)
                self.rule.setCurrentIndex(max(0, self.rule.findData(self.v.get("rule", "first"))))
                self.rule.currentIndexChanged.connect(lambda _: self._preview())
                f.addRow("Rule", self.rule)
                f.addRow("", label("Bad inputs are skipped; the result is bad only when none is good.",
                                   muted=True, small=True))
                v.addWidget(w)
            else:
                row = QHBoxLayout(); row.setSpacing(4)
                self.expr = QLineEdit(self.v.get("expr", ""))
                self.expr.setPlaceholderText("A and B > 20" if k == "logic" else "(A + B + C) * 3.6")
                self.expr.textChanged.connect(lambda _: self._preview())
                row.addWidget(QLabel("Condition" if k == "logic" else "Expression")); row.addWidget(self.expr, 1)
                v.addLayout(row)
                tools = QHBoxLayout(); tools.setSpacing(4)
                for text, items in (("Templates", LOGIC_TEMPLATES if k == "logic" else TEMPLATES),
                                    ("Functions", [(f"{n}  -  {h}", n + "()") for n, (_f, h) in
                                                   sorted(cm.SCALAR_FUNCS.items())] + [("if  -  if(c, then, else)", "if(, , )")])):
                    b = QToolButton(); b.setText(text)
                    b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                    m = QMenu(b)
                    for t_, ins_ in items:
                        m.addAction(t_, lambda s=ins_, tpl=(text == "Templates"): self._insert(s, tpl))
                    b.setMenu(m); tools.addWidget(b)
                tools.addStretch(1)
                v.addLayout(tools)
        self.result = label("", small=True); self.result.setObjectName("statusMsg")
        v.addWidget(self.result)
        return page

    def _inputs(self):
        box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(4)
        head = QHBoxLayout(); head.setSpacing(4)
        head.addWidget(QLabel("Inputs")); head.addStretch(1)
        add = QPushButton("Add tag..."); add.clicked.connect(self._add_inputs)
        self.rm = QPushButton("Remove"); self.rm.clicked.connect(self._remove_inputs)
        head.addWidget(add); head.addWidget(self.rm)
        v.addLayout(head)
        t = self.in_tree = QTreeWidget()
        t.setHeaderLabels(["Alias", "Tag", "Value", "Quality"]); t.setRootIsDecorated(False)
        t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        for c, wd in enumerate((52, 220, 80)):
            t.header().resizeSection(c, wd)
        t.setMinimumHeight(110)
        for i in self.v.get("inputs", []):
            self._input_row(i["alias"], list(i["key"]))
        t.itemChanged.connect(lambda *_: self._preview())
        t.itemSelectionChanged.connect(lambda: self.rm.setEnabled(bool(t.selectedItems())))
        self.rm.setEnabled(False)
        v.addWidget(t, 1)
        v.addWidget(label("Use the aliases in the formula (double-click to rename one), or full names such as "
                          "PLC01_Flow.", muted=True, small=True))
        return box

    def _input_row(self, alias: str, key: list):
        it = QTreeWidgetItem([alias, self.labels.get(tuple(key), "(deleted tag)"), "", ""])
        it.setData(0, Qt.ItemDataRole.UserRole, key)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
        self.in_tree.addTopLevelItem(it)

    def _add_inputs(self):
        have = [it.data(0, Qt.ItemDataRole.UserRole) for it in self._input_items()]
        keys = self.pick(self.choices, [], True, "Add inputs")
        if not keys:
            return
        used = {it.text(0) for it in self._input_items()}
        self.in_tree.blockSignals(True)
        for key in keys:
            if list(key) in have:
                continue
            alias = next((a for a in vt.ALIASES if a not in used), f"X{len(used)}")
            used.add(alias)
            self._input_row(alias, list(key))
        self.in_tree.blockSignals(False)
        self._preview()

    def _remove_inputs(self):
        for it in self.in_tree.selectedItems():
            self.in_tree.takeTopLevelItem(self.in_tree.indexOfTopLevelItem(it))
        self._preview()

    def _input_items(self):
        t = getattr(self, "in_tree", None)
        return [t.topLevelItem(i) for i in range(t.topLevelItemCount())] if t is not None else []

    def _insert(self, text: str, template: bool):
        if template and not self.expr.text().strip():
            self.expr.setText(text)
        else:
            self.expr.insert(text)
        self.expr.setFocus()

    def _conversion(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0)
        top, f = form_page()
        self.conv = QComboBox()
        for k, t in vt.CONVS.items():
            self.conv.addItem(t, k)
        self.conv.setCurrentIndex(max(0, self.conv.findData(self.v.get("conv", "linear"))))
        f.addRow("Method", self.conv)
        v.addWidget(top)
        self.conv_stack = QStackedWidget()
        lin, lf = form_page()
        self.in_lo, self.in_hi = spin(self.v["in_lo"]), spin(self.v["in_hi"])
        self.out_lo, self.out_hi = spin(self.v["out_lo"]), spin(self.v["out_hi"])
        for a, b, text in ((self.in_lo, self.in_hi, "Input range"), (self.out_lo, self.out_hi, "Output range")):
            r = QHBoxLayout(); r.addWidget(a); r.addWidget(QLabel("to")); r.addWidget(b)
            lf.addRow(text, r)
        self.clamp = QCheckBox("Keep the result inside the output range", checked=bool(self.v.get("clamp", True)))
        lf.addRow("", self.clamp)
        tab = QWidget(); tv = QVBoxLayout(tab); tv.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 2); self.table.setHorizontalHeaderLabels(["Input", "Output"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide(); self.table.setMinimumHeight(110)
        for x, y in self.v.get("table", []):
            self._table_row(self.table, [f"{float(x):g}", f"{float(y):g}"])
        tb = QHBoxLayout(); tb.addWidget(label("Points between are interpolated; outside, the end value.",
                                               muted=True, small=True), 1)
        for text, slot in (("Add point", lambda: self._table_row(self.table, ["", ""])),
                           ("Remove", lambda: self._table_remove(self.table))):
            b = QPushButton(text); b.clicked.connect(slot); tb.addWidget(b)
        tv.addWidget(self.table, 1); tv.addLayout(tb)
        un, uf = form_page()
        self.unit_conv = QComboBox()
        for k, (t, *_r) in vt.UNIT_CONV.items():
            self.unit_conv.addItem(t, k)
        self.unit_conv.setCurrentIndex(max(0, self.unit_conv.findData(self.v.get("unit_conv", "c_f"))))
        self.unit_conv.currentIndexChanged.connect(self._unit_follow)
        uf.addRow("Conversion", self.unit_conv)
        for p in (lin, tab, un):
            self.conv_stack.addWidget(p)
        self.conv.currentIndexChanged.connect(lambda i: (self.conv_stack.setCurrentIndex(i), self._preview()))
        self.conv_stack.setCurrentIndex(self.conv.currentIndex())
        for s in (self.in_lo, self.in_hi, self.out_lo, self.out_hi):
            s.valueChanged.connect(lambda _: self._preview())
        self.clamp.toggled.connect(lambda _: self._preview())
        self.table.itemChanged.connect(lambda _: self._preview())
        v.addWidget(self.conv_stack, 1)
        return w

    def _unit_follow(self):
        u = vt.UNIT_CONV.get(self.unit_conv.currentData(), ("", 1, 0, ""))[3]
        if u and (not self.unit.currentText() or self.unit.currentText() in {x[3] for x in vt.UNIT_CONV.values()}):
            self.unit.setCurrentText(u)              # follow the conversion unless the user typed a unit
        self._preview()

    def _textmap(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(4)
        row = QHBoxLayout()
        self.mode_values, self.mode_ranges = QRadioButton("Values (0, 1, 2 ... or text)"), QRadioButton("Ranges")
        grp = QButtonGroup(w); grp.addButton(self.mode_values); grp.addButton(self.mode_ranges)
        (self.mode_ranges if self.v.get("map_mode") == "ranges" else self.mode_values).setChecked(True)
        row.addWidget(QLabel("Match")); row.addWidget(self.mode_values); row.addWidget(self.mode_ranges); row.addStretch(1)
        v.addLayout(row)
        self.map = QTableWidget(0, 5)
        self.map.setHorizontalHeaderLabels(["Value", "From", "To (below)", "Text", "Colour"])
        self.map.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.map.verticalHeader().hide(); self.map.setMinimumHeight(110)
        for r in self.v.get("map", []):
            self._map_row(r)
        tb = QHBoxLayout()
        self.other = QLineEdit(self.v.get("other", "")); self.other.setPlaceholderText("Unknown")
        tb.addWidget(QLabel("Other values")); tb.addWidget(self.other, 1)
        for text, slot in (("Add row", lambda: self._map_row({"value": "", "text": ""})),
                           ("Remove", lambda: self._table_remove(self.map))):
            b = QPushButton(text); b.clicked.connect(slot); tb.addWidget(b)
        v.addWidget(self.map, 1); v.addLayout(tb)
        self.mode_values.toggled.connect(lambda _: (self._map_columns(), self._preview()))
        self.map.itemChanged.connect(lambda _: self._preview())
        self.other.textChanged.connect(lambda _: self._preview())
        self._map_columns()
        return w

    def _map_row(self, r: dict):
        n = self.map.rowCount(); self.map.insertRow(n)
        fmt = lambda x: "" if x is None or x == "" else f"{float(x):g}"     # noqa: E731
        for c, text in enumerate((str(r.get("value", "")), fmt(r.get("lo")), fmt(r.get("hi")), str(r.get("text", "")))):
            self.map.setItem(n, c, QTableWidgetItem(text))
        col = QComboBox()
        for k, t in TOKENS.items():
            col.addItem(t, k)
        col.setCurrentIndex(max(0, col.findData(r.get("color", "") if r.get("color", "") in TOKENS else "")))
        col.currentIndexChanged.connect(lambda _: self._preview())
        self.map.setCellWidget(n, 4, col)

    def _map_columns(self):
        rng = self.mode_ranges.isChecked()
        self.map.setColumnHidden(0, rng); self.map.setColumnHidden(1, not rng); self.map.setColumnHidden(2, not rng)

    @staticmethod
    def _table_row(t: QTableWidget, texts):
        n = t.rowCount(); t.insertRow(n)
        for c, text in enumerate(texts):
            t.setItem(n, c, QTableWidgetItem(text))

    def _table_remove(self, t: QTableWidget):
        rows = sorted({i.row() for i in t.selectedIndexes()} or ({t.rowCount() - 1} if t.rowCount() else set()),
                      reverse=True)
        for r in rows:
            t.removeRow(r)
        self._preview()

    def _calculation(self):
        w, f = form_page()
        self.upd = {}
        grp = QButtonGroup(w)
        box = QVBoxLayout(); box.setSpacing(4)
        for k, t in vt.UPDATES.items():
            rb = self.upd[k] = QRadioButton(t); grp.addButton(rb)
            if k == "trigger":
                r = QHBoxLayout(); r.addWidget(rb)
                self.trigger = QComboBox(); self.trigger.addItem("(choose)", None)
                for tid, idx, lbl, _c, _u in self.choices:
                    self.trigger.addItem(lbl, [tid, idx])
                if self.v.get("trigger"):
                    self.trigger.setCurrentIndex(max(0, self.trigger.findData(list(self.v["trigger"]))))
                self.trigger.currentIndexChanged.connect(lambda _: self._preview())
                r.addWidget(self.trigger, 1); box.addLayout(r)
            elif k == "every":
                r = QHBoxLayout(); r.addWidget(rb)
                self.every = QSpinBox(minimum=100, maximum=3_600_000, singleStep=100, suffix=" ms")
                self.every.setValue(int(self.v.get("every_ms", 1000)))
                self.every.valueChanged.connect(lambda _: self._preview())
                r.addWidget(self.every); r.addStretch(1); box.addLayout(r)
            else:
                box.addWidget(rb)
            rb.toggled.connect(lambda _: self._preview())
        self.upd.get(self.v.get("update", "change"), self.upd["change"]).setChecked(True)
        f.addRow("Update", box)
        self.bad = {}
        g2 = QButtonGroup(w); b2 = QVBoxLayout(); b2.setSpacing(4)
        for k, t in vt.ON_BAD.items():
            rb = self.bad[k] = QRadioButton(t); g2.addButton(rb); b2.addWidget(rb)
        self.bad.get(self.v.get("on_bad", "bad"), self.bad["bad"]).setChecked(True)
        if self.kind == "selector":
            for rb in self.bad.values():
                rb.setEnabled(False)
            b2.addWidget(label("A selector skips bad inputs.", muted=True, small=True))
        f.addRow("Bad input", b2)
        f.addRow("", label("An input is bad when it has no data, bad quality, or is stale (no new value for 3 "
                           "scan times).", muted=True, small=True))
        return w

    def _display(self):
        w, f = form_page()
        self.decimals = QSpinBox(minimum=0, maximum=9); self.decimals.setValue(int(self.v.get("decimals", 2)))
        self.decimals.valueChanged.connect(lambda _: self._preview())
        f.addRow("Decimals", self.decimals)
        f.addRow("", label("Digits after the decimal point in tables, numbers and Properties.", muted=True, small=True))
        return w

    def _set_color(self, c: str):
        self._color = c
        self.color_btn.setIcon(swatch(c, 14))
        self.color_btn.setText(c)

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Tag colour")
        if c.isValid():
            self._set_color(c.name())

    # ------------------------------------------------------------------ data
    def collect(self) -> dict:
        """The definition as the widgets say (does not change self.v)."""
        v = dict(self.v)
        v.update(name=self.name.text().strip(), group=self.group.currentText().strip().strip("/"),
                 dtype=self.dtype.currentData(), unit=self.unit.currentText().strip(),
                 description=self.desc.text().strip(), enabled=self.enabled.isChecked(),
                 decimals=self.decimals.value(),
                 color="" if self._color.lower() == self.default_color.lower() else self._color)
        k = self.kind
        if k == "constant":
            text = self.value.text().strip()
            v["value"] = text if v["dtype"] == "text" else (_float(text) if _float(text) is not None else text)
        elif k in vt.ONE_INPUT:
            key = self.source.currentData()
            v["inputs"] = [{"alias": "A", "key": list(key)}] if key else []
        else:
            v["inputs"] = [{"alias": it.text(0).strip(), "key": list(it.data(0, Qt.ItemDataRole.UserRole))}
                           for it in self._input_items()]
        if k in ("formula", "logic"):
            v["expr"] = self.expr.text().strip()
        elif k == "selector":
            v["rule"] = self.rule.currentData()
        elif k == "conversion":
            v.update(conv=self.conv.currentData(), in_lo=self.in_lo.value(), in_hi=self.in_hi.value(),
                     out_lo=self.out_lo.value(), out_hi=self.out_hi.value(), clamp=self.clamp.isChecked(),
                     unit_conv=self.unit_conv.currentData(), table=self._table_points())
        elif k == "textmap":
            rows = []
            for r in range(self.map.rowCount()):
                cell = lambda c: (self.map.item(r, c).text().strip() if self.map.item(r, c) else "")   # noqa: E731
                if not cell(3) and not cell(0) and not cell(1) and not cell(2):
                    continue
                rows.append({"value": cell(0), "lo": _float(cell(1)), "hi": _float(cell(2)), "text": cell(3),
                             "color": self.map.cellWidget(r, 4).currentData() or ""})
            v.update(map_mode="ranges" if self.mode_ranges.isChecked() else "values", map=rows,
                     other=self.other.text())
        if k not in vt.NO_INPUT:
            v["update"] = next(key for key, rb in self.upd.items() if rb.isChecked())
            v["trigger"] = self.trigger.currentData()
            v["every_ms"] = self.every.value()
            v["on_bad"] = next(key for key, rb in self.bad.items() if rb.isChecked())
        return v

    def _table_points(self):
        pts = []
        for r in range(self.table.rowCount()):
            x, y = (_float(self.table.item(r, c).text()) if self.table.item(r, c) else None for c in (0, 1))
            if x is not None and y is not None:
                pts.append([x, y])
        return pts

    def apply(self, v: dict):
        v.update(self.collect())

    # ------------------------------------------------------------------ live check
    def _preview(self):
        if not getattr(self, "_ready", False):
            return
        v = self.collect()
        msg = self.engine.check(v, self.names, self.taken)
        for it in self._input_items():               # live values of the inputs
            val, qual, _good, _t = self.live(*it.data(0, Qt.ItemDataRole.UserRole))
            if it.text(2) != val or it.text(3) != qual:
                self.in_tree.blockSignals(True)
                it.setText(2, val); it.setText(3, qual)
                self.in_tree.blockSignals(False)
        if hasattr(self, "src_value"):
            key = self.source.currentData()
            val, qual, _g, _t = self.live(*key) if key else ("", "", None, "")
            self.src_value.setText(f"Now: {val or '-'}  {qual}".strip() if key else "")
        tab = self._tab_of(msg) if msg else -1
        for i, t in enumerate(self._titles):
            self.tabs.setTabText(i, t + ("  ●" if i == tab else ""))
        if msg:
            set_prop(self.err, "level", "bad"); self.err.setText(f"✗ {self._titles[tab]}: {msg}")
            self.result.setText("")
            self.ok_btn.setEnabled(False)
            return
        val, run = self.engine.preview(v, self.names)
        unit = v.get("unit", "")
        if run:
            set_prop(self.result, "level", "warn"); self.result.setText(f"= {val or '-'}   ({run})")
        else:
            set_prop(self.result, "level", "ok"); self.result.setText(f"= {val} {unit}  ✓".replace("  ✓", " ✓"))
        set_prop(self.err, "level", "muted"); self.err.setText("✓ Ready")
        self.ok_btn.setEnabled(not getattr(self, "locked", False))

    def _tab_of(self, msg: str) -> int:
        """The tab a problem belongs to (marked with ●)."""
        m = msg.lower()
        if any(w in m for w in ("name", "called", "/")) and "unknown name" not in m:
            return 0
        if ("trigger" in m or "every" in m) and "Calculation" in self._titles:
            return self._titles.index("Calculation")
        return 1

    def _ok(self):
        self._preview()
        if not self.ok_btn.isEnabled():
            return
        self._timer.stop()
        self.accept()


def _float(text):
    try:
        return float(str(text).strip()) if str(text).strip() != "" else None
    except ValueError:
        return None
