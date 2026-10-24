"""Add / edit a variable: formula, statistic or signal processing of channels (tabbed)."""
from __future__ import annotations

import copy

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget,
                               QTabWidget, QVBoxLayout)

from nxlogger.core import analysis as an
from nxlogger.core import variables as vr
from nxlogger.core.channel_math import FUNCS, UNITS, identifier
from nxlogger.ui.dialogs.common import ChannelPicker, ColorButton, form_page, spin
from nxlogger.ui.theme.ui_kit import label, set_prop


class VariableDialog(QDialog):
    """
    engine  : VariableEngine (validation, formula names, latest values)
    choices : channel choices [(topic_id, index, label, colour, unit)] incl. other variables
    """

    def __init__(self, parent, var: dict, engine, choices):
        super().__init__(parent)
        self.v, self.engine = copy.deepcopy(var), engine
        own = vr.var_key(self.v["id"])
        self.choices = [c for c in choices if (c[0], c[1]) != own]
        self.labels = {(c[0], c[1]): c[2] for c in self.choices}
        self.setWindowTitle(f"Variable - {self.v['name']}")
        self.setMinimumWidth(600)
        tabs = QTabWidget()
        tabs.addTab(self._general(), "General")
        tabs.addTab(self._definition(), "Definition")
        self.tabs = tabs
        self.err = QLabel(); self.err.setObjectName("statusMsg"); self.err.setWordWrap(True)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        set_prop(bb.button(QDialogButtonBox.StandardButton.Ok), "primary", True)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        v = QVBoxLayout(self)
        v.addWidget(tabs, 1); v.addWidget(self.err); v.addWidget(bb)
        if self.v.get("expr") or self.v.get("source"):
            tabs.setCurrentIndex(1)
        self._timer = QTimer(self, interval=500, timeout=self._validate)
        self._timer.start()
        self._validate()

    # ---- pages
    def _general(self):
        w, f = form_page()
        self.name = QLineEdit(self.v["name"])
        self.ident = label("", muted=True, small=True)
        self.name.textChanged.connect(lambda _: self._validate())
        self.unit = QComboBox(); self.unit.setEditable(True); self.unit.addItems(UNITS)
        self.unit.setCurrentText(self.v.get("unit", ""))
        self.color = ColorButton(self.v.get("color", ""), "Auto (palette)")
        self.dec = QSpinBox(minimum=0, maximum=8, value=int(self.v.get("decimals", 3)))
        self.desc = QPlainTextEdit(self.v.get("description", "")); self.desc.setMaximumHeight(60)
        self.desc.setPlaceholderText("What it means, how it is used ... (saved in the files)")
        self.enabled = QCheckBox("Enabled - calculate, show in views and alarms", checked=self.v["enabled"])
        self.log = QCheckBox("Logged by logging profiles set to 'All enabled channels'",
                             checked=bool(self.v.get("log", True)))
        f.addRow("Name", self.name)
        f.addRow("", self.ident)
        f.addRow("Unit", self.unit)
        f.addRow("Colour", self.color)
        f.addRow("Decimals", self.dec)
        f.addRow("Description", self.desc)
        f.addRow("", self.enabled)
        f.addRow("", self.log)
        return w

    def _definition(self):
        w, f = form_page()
        self.kind = QComboBox()
        for k, text in vr.KINDS.items():
            self.kind.addItem(text, k)
        self.kind.setCurrentIndex(max(self.kind.findData(self.v.get("kind", "formula")), 0))
        f.addRow("Type", self.kind)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._formula_page())
        self.pages.addWidget(self._stat_page())
        self.pages.addWidget(self._process_page())
        f.addRow(self.pages)
        self.preview = QLabel(); self.preview.setObjectName("sectionTitle")
        f.addRow("Value now", self.preview)
        self.kind.currentIndexChanged.connect(lambda i: (self.pages.setCurrentIndex(i), self._validate()))
        self.pages.setCurrentIndex(self.kind.currentIndex())
        return w

    def _formula_page(self):
        w, f = form_page(); f.setContentsMargins(0, 0, 0, 0)
        self.expr = QLineEdit(self.v.get("expr", ""))
        mono = QFont("Consolas"); mono.setStyleHint(QFont.StyleHint.Monospace)
        self.expr.setFont(mono)
        self.expr.setPlaceholderText("Simulator_CH1 - Simulator_CH2")
        self.expr.textChanged.connect(lambda _: self._validate())
        f.addRow("Formula", self.expr)
        names = self.engine.names(self.v["id"])
        self.ins = QComboBox()
        for ident, key in names.items():
            self.ins.addItem(f"{ident}    —  {self.labels.get(key, '')}", ident)
        b = QPushButton("Insert"); b.clicked.connect(lambda: self._insert(self.ins.currentData()))
        r = QHBoxLayout(); r.addWidget(self.ins, 1); r.addWidget(b)
        f.addRow("Channel", r)
        # quick signal operations: pick A / B and a template
        self.op_a, self.op_b, self.op_t = QComboBox(), QComboBox(), QComboBox()
        for c in (self.op_a, self.op_b):
            for ident in names:
                c.addItem(ident, ident)
        if self.op_b.count() > 1:
            self.op_b.setCurrentIndex(1)
        for text, tpl in vr.TEMPLATES:
            self.op_t.addItem(text, tpl)
        build = QPushButton("Build"); build.setToolTip("Replace the formula with the operation")
        build.clicked.connect(self._build)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("A")); r2.addWidget(self.op_a, 1); r2.addWidget(QLabel("B")); r2.addWidget(self.op_b, 1)
        r2.addWidget(self.op_t); r2.addWidget(build)
        f.addRow("Operation", r2)
        h = label("Sample by sample on the block of the first channel used; a channel of another topic "
                  "enters as its latest value.  Operators  + - * / ** %  and comparisons.  Functions: "
                  + ", ".join(sorted(FUNCS)) + ".  Constants: pi, e.", muted=True, small=True)
        h.setWordWrap(True)
        f.addRow(h)
        return w

    def _stat_page(self):
        w, f = form_page(); f.setContentsMargins(0, 0, 0, 0)
        self.st_src = ChannelPicker(self.choices, [self.v.get("source")], multi=False)
        self.st_src.setMinimumHeight(160)
        self.st_src.changed.connect(self._validate)
        self.stat = QComboBox()
        for k in an.STAT_KEYS:
            self.stat.addItem(an.STAT_LABEL[k], k)
            if k in an.STAT_HELP:
                self.stat.setItemData(self.stat.count() - 1, an.STAT_HELP[k], 3)
        self.stat.setCurrentIndex(max(self.stat.findData(self.v.get("stat", "rms")), 0))
        self.win = spin(float(self.v.get("window_s", 1.0)), 0, 3600, 2)
        self.win.setSuffix(" s"); self.win.setSpecialValueText("Each block")
        f.addRow("Source", self.st_src)
        f.addRow("Statistic", self.stat)
        f.addRow("Window", self.win)
        f.addRow(label("One value per received block, computed over the last window (sliding). "
                       "Useful for trends: RMS level, crest factor, kurtosis, dominant frequency.",
                       muted=True, small=True))
        return w

    def _process_page(self):
        w, f = form_page(); f.setContentsMargins(0, 0, 0, 0)
        self.pr_src = ChannelPicker(self.choices, [self.v.get("source")], multi=False)
        self.pr_src.setMinimumHeight(160)
        self.pr_src.changed.connect(self._validate)
        self.op = QComboBox()
        for k, (text, *_ ) in vr.OPS.items():
            self.op.addItem(text, k)
        self.op.setCurrentIndex(max(self.op.findData(self.v.get("op", "integrate")), 0))
        self.param = spin(float(self.v.get("param") or 0), 0, 1e6, 3)
        self.param_lbl = QLabel()
        f.addRow("Source", self.pr_src)
        f.addRow("Operation", self.op)
        f.addRow(self.param_lbl, self.param)
        f.addRow(label("Runs on every sample with memory between blocks. Integrate turns acceleration "
                       "into velocity (set the drift high-pass a little below the lowest frequency of "
                       "interest); a second variable integrating that gives displacement.",
                       muted=True, small=True))
        self.op.currentIndexChanged.connect(lambda _: self._op_changed(True))
        self._op_changed(False)
        return w

    def _op_changed(self, set_default: bool):
        _, plabel, default, unit = vr.OPS[self.op.currentData()]
        self.param_lbl.setText(plabel or "Parameter")
        self.param.setEnabled(bool(plabel))
        self.param.setSuffix(f" {unit}" if unit else "")
        self.param.setDecimals(0 if unit == "samples" else 3)
        if set_default:
            self.param.setValue(default)
        self._validate()

    # ---- helpers
    def _insert(self, ident):
        if ident:
            self.expr.insert(ident)
            self.expr.setFocus()

    def _build(self):
        tpl = self.op_t.currentData()
        a, b = self.op_a.currentData() or "A", self.op_b.currentData() or "B"
        self.expr.setText(tpl.replace("A", "\0").replace("B", b).replace("\0", a))

    def result_variable(self) -> dict:
        v = self.v
        kind = self.kind.currentData()
        v.update(name=self.name.text().strip(), unit=self.unit.currentText().strip(),
                 color=self.color.color(), decimals=self.dec.value(),
                 description=self.desc.toPlainText().strip(), enabled=self.enabled.isChecked(),
                 log=self.log.isChecked(), kind=kind, expr=self.expr.text().strip(),
                 stat=self.stat.currentData(), window_s=self.win.value(),
                 op=self.op.currentData(), param=self.param.value())
        if kind == "statistic":
            v["source"] = self.st_src.current()
        elif kind == "process":
            v["source"] = self.pr_src.current()
        return v

    def _validate(self):
        if not hasattr(self, "preview"):
            return ""
        self.ident.setText(f"Formula name: {identifier(self.name.text() or 'v')}")
        v = self.result_variable()
        msg = self.engine.check(v)
        set_prop(self.err, "level", "bad" if msg else "muted")
        self.err.setText(msg)
        val = self.engine.values.get(v["id"])
        self.preview.setText("-" if val is None else f"{val:.{self.dec.value()}f} {v['unit']}".strip()
                             + "   (saved definition)")
        return msg

    def _ok(self):
        if not self._validate():
            self.accept()
