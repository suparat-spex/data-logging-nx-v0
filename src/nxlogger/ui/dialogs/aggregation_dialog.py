"""Add / edit an Aggregation tag (Tags > Aggregation): the settings pop-up, tabs style like the
Virtual tag pop-up. The type is chosen before it opens and is fixed.

  General   name, group, unit, colour (swatch + Default), description, enabled
  <type>    Statistic · Totalizer · Counter · Runtime · Hold · Change: the source and its rule
  Period    reset (never / hour / day / week / month / shifts / a tag), keep the previous
            period's value, keep the running value on restart (not for a statistic)
  Display   decimals

[Add tag] / [Save] stays off until the definition is valid (the problem in the footer, its tab
marked with ●). The window passes the choices and helpers - dialogs never import each other.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QHBoxLayout, QLineEdit, QPushButton, QRadioButton, QSpinBox, QTabWidget, QVBoxLayout)

from nxlogger.core import aggregation as ag
from nxlogger.core import channel_math as cm
from nxlogger.ui.dialogs.common import form_page, spin
from nxlogger.ui.icons import swatch
from nxlogger.ui.theme.ui_kit import label, set_prop

TAB_OF = {"statistic": "Statistic", "totalizer": "Totalizer", "counter": "Counter", "runtime": "Runtime",
          "minmax": "Hold", "change": "Change"}
UNIT_HINT = {("totalizer", "m³/h"): "m³", ("totalizer", "L/s"): "L", ("totalizer", "kW"): "kWh",
             ("totalizer", "W"): "Wh", ("totalizer", "L/min"): "L"}


class AggregationDialog(QDialog):
    """a: the definition (a copy - apply(a) writes the fields). engine: the AggregationEngine;
    choices: [(tid, idx, label, colour, unit)] of number tags; names: {formula name: key};
    groups: the project's group paths; live(key) -> (value text, quality, good, time);
    taken: other aggregation names (lower case); default_color: the palette colour."""

    def __init__(self, parent, a: dict, engine, choices, names: dict, groups, live, taken: set,
                 default_color: str = ""):
        super().__init__(parent)
        self.a, self.engine, self.names, self.live, self.taken = dict(a), engine, names, live, taken
        self.kind = a["kind"]
        self.choices = [c for c in choices if not (ag.is_agg(c[0]) and c[0] == ag.AGG_PREFIX + a["id"])]
        self.units = {(c[0], c[1]): c[4] for c in self.choices}
        self.default_color = default_color or "#808080"
        self._color = a.get("color") or self.default_color
        self.setWindowTitle(ag.TYPES[self.kind][0])
        tabs = self.tabs = QTabWidget()
        tabs.addTab(self._general(groups), "General")
        tabs.addTab(self._type_page(), TAB_OF[self.kind])
        if ag.has_period(self.a):
            tabs.addTab(self._period(), "Period")
        tabs.addTab(self._display(), "Display")
        self._titles = [tabs.tabText(i) for i in range(tabs.count())]
        self.err = label("", small=True); self.err.setObjectName("statusMsg"); self.err.setWordWrap(True)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addWidget(tabs, 1); lay.addWidget(self.err); lay.addWidget(bb)
        self._ready = True                           # signals during construction: no check yet
        self._timer = QTimer(self, interval=500, timeout=self._check)
        self._timer.start()
        self._check()

    # ------------------------------------------------------------------ pages
    def _general(self, groups):
        w, f = form_page()
        self.name = QLineEdit(self.a["name"]); self.name.setPlaceholderText("e.g. Volume_Today")
        self.group = QComboBox(); self.group.setEditable(True); self.group.addItem("")
        self.group.addItems([g for g in groups if g]); self.group.setCurrentText(self.a.get("group", ""))
        self.group.lineEdit().setPlaceholderText("(no group) - pick one or type a new one")
        self.unit = QComboBox(); self.unit.setEditable(True); self.unit.addItems(cm.UNITS)
        self.unit.setCurrentText(self.a.get("unit", ""))
        self.color_btn = QPushButton(); self.color_btn.clicked.connect(self._pick_color)
        self.color_btn.setToolTip("The colour of its line in trends and its swatch in tables")
        reset = QPushButton("Default"); reset.clicked.connect(lambda: self._set_color(self.default_color))
        reset.setToolTip("Back to the automatic palette colour")
        colour = QHBoxLayout(); colour.addWidget(self.color_btn); colour.addWidget(reset); colour.addStretch(1)
        self._set_color(self._color)
        self.desc = QLineEdit(self.a.get("description", ""))
        self.desc.setPlaceholderText("What it counts, where ...")
        self.enabled = QCheckBox("Enabled - update it and show it in views", checked=self.a.get("enabled", True))
        f.addRow("Type", label(f"{ag.TYPES[self.kind][0]} - {ag.TYPES[self.kind][2]}", muted=True))
        f.addRow("Name", self.name)
        f.addRow("Group", self.group)
        f.addRow("Unit", self.unit)
        f.addRow("Colour", colour)
        f.addRow("Description", self.desc)
        f.addRow("", self.enabled)
        self.name.textChanged.connect(lambda _: self._check())
        return w

    def _combo(self, items: dict, cur):
        c = QComboBox()
        for k, t in items.items():
            c.addItem(t[0] if isinstance(t, tuple) else t, k)
        c.setCurrentIndex(max(0, c.findData(cur)))
        c.currentIndexChanged.connect(lambda _: self._check())
        return c

    def _type_page(self):
        w, f = form_page()
        a, k = self.a, self.kind
        self.source = QComboBox(); self.source.addItem("(choose the source tag)", None)
        for tid, idx, lbl, _c, _u in self.choices:
            self.source.addItem(lbl, [tid, idx])
        if a.get("source"):
            self.source.setCurrentIndex(max(0, self.source.findData(list(a["source"]))))
        self.source.currentIndexChanged.connect(lambda _: (self._unit_follow(), self._check()))
        f.addRow("Source", self.source)
        self.src_value = label("", muted=True, small=True); f.addRow("", self.src_value)
        if k == "statistic":
            self.stat = self._combo(ag.STATS, a.get("stat", "mean"))
            self.win_n = spin(a.get("win_n", 1.0), 0, 1e9, 2)
            self.win_unit = self._combo(ag.WIN_UNITS, a.get("win_unit", "h"))
            self.win_n.valueChanged.connect(lambda _: self._check())
            r = QHBoxLayout(); r.addWidget(self.win_n); r.addWidget(self.win_unit); r.addStretch(1)
            self.mode_moving, self.mode_block = QRadioButton("Moving (every sample)"), QRadioButton(
                "Block (one value per full window)")
            g = QButtonGroup(w); g.addButton(self.mode_moving); g.addButton(self.mode_block)
            (self.mode_block if a.get("win_mode") == "block" else self.mode_moving).setChecked(True)
            m = QVBoxLayout(); m.setSpacing(4); m.addWidget(self.mode_moving); m.addWidget(self.mode_block)
            f.addRow("Function", self.stat)
            f.addRow("Window", r)
            f.addRow("Mode", m)
        elif k == "totalizer":
            self.rate_per = self._combo(ag.RATE_PER, a.get("rate_per", "h"))
            self.factor = spin(a.get("factor", 1.0), -1e9, 1e9, 6)
            self.factor.valueChanged.connect(lambda _: self._check())
            self.skip_neg = QCheckBox("Ignore negative values (reverse flow)", checked=bool(a.get("skip_negative", True)))
            f.addRow("Source rate", self.rate_per)
            f.addRow("Factor", self.factor)
            f.addRow("", label("Result = ∫ source dt × factor. A bad or stale source pauses it (the gap is not "
                               "added).", muted=True, small=True))
            f.addRow("", self.skip_neg)
        elif k == "counter":
            self.edge = self._combo(ag.EDGES, a.get("edge", "rising"))
            f.addRow("Count", self.edge)
            f.addRow("", label("A tag counts as on when it is not 0 (1, true, a running motor ...).",
                               muted=True, small=True))
        elif k == "runtime":
            self.time_unit = self._combo(ag.TIME_UNITS, a.get("time_unit", "h"))
            self.time_unit.currentIndexChanged.connect(lambda _: self._unit_follow())
            f.addRow("In", self.time_unit)
            f.addRow("", label("Counts the time while the source is not 0 and good.", muted=True, small=True))
        elif k == "minmax":
            self.hold_max, self.hold_min = QRadioButton("Highest value"), QRadioButton("Lowest value")
            g = QButtonGroup(w); g.addButton(self.hold_max); g.addButton(self.hold_min)
            (self.hold_min if a.get("hold") == "min" else self.hold_max).setChecked(True)
            m = QVBoxLayout(); m.setSpacing(4); m.addWidget(self.hold_max); m.addWidget(self.hold_min)
            f.addRow("Hold", m)
            f.addRow("", label("Since the last reset; Properties shows when it happened.", muted=True, small=True))
        else:
            self.change = self._combo(ag.CHANGE, a.get("change", "diff"))
            self.change_per = self._combo(ag.RATE_PER, a.get("change_per", "h"))
            f.addRow("Result", self.change)
            f.addRow("Rate", self.change_per)
            f.addRow("", label("E.g. an energy meter reading -> this hour's consumption.", muted=True, small=True))
        self.result = label("", small=True); self.result.setObjectName("statusMsg")
        f.addRow("Now", self.result)
        return w

    def _unit_follow(self):
        """Suggest the result unit from the source (m³/h -> m³, kW -> kWh; runtime h / min / s)."""
        if self.unit.currentText() and self.unit.currentText() != getattr(self, "_suggested", None):
            return                                   # the user typed one
        key = self.source.currentData()
        u = self.units.get(tuple(key), "") if key else ""
        if self.kind == "runtime":
            sug = self.time_unit.currentData()
        elif self.kind == "counter":
            sug = ""
        else:
            sug = UNIT_HINT.get((self.kind, u), u if self.kind != "totalizer" else "")
        self._suggested = sug
        self.unit.setCurrentText(sug)

    def _period(self):
        w, f = form_page()
        a = self.a
        self.reset = self._combo(ag.RESETS, a.get("reset", "day"))
        self.reset_at = QLineEdit(a.get("reset_at", "06:00")); self.reset_at.setPlaceholderText("HH:MM")
        self.reset_at.setMaximumWidth(90)
        self.shifts = QLineEdit(a.get("shifts", ag.DEFAULT_SHIFTS))
        self.reset_tag = QComboBox(); self.reset_tag.addItem("(choose)", None)
        for tid, idx, lbl, _c, _u in self.choices:
            self.reset_tag.addItem(lbl, [tid, idx])
        if a.get("reset_tag"):
            self.reset_tag.setCurrentIndex(max(0, self.reset_tag.findData(list(a["reset_tag"]))))
        self.keep_prev = QCheckBox("Keep the last period's value as <name>.Previous (e.g. yesterday's total)",
                                   checked=bool(a.get("keep_prev", True)))
        self.retain = QCheckBox("Keep the running value when the app restarts", checked=bool(a.get("retain", True)))
        for wdg in (self.reset_at, self.shifts):
            wdg.textChanged.connect(lambda _: self._check())
        self.reset_tag.currentIndexChanged.connect(lambda _: self._check())
        self.reset.currentIndexChanged.connect(lambda _: self._period_rows())
        f.addRow("Reset", self.reset)
        f.addRow("At", self.reset_at)
        f.addRow("Shifts", self.shifts)
        f.addRow("Reset tag", self.reset_tag)
        f.addRow("", self.keep_prev)
        f.addRow("", self.retain)
        self.next_reset = label("", muted=True, small=True); f.addRow("", self.next_reset)
        self._period_rows()
        return w

    def _period_rows(self):
        m = self.reset.currentData()
        self.reset_at.setEnabled(m in ("hour", "day", "week", "month"))
        self.reset_at.setToolTip("Minute of the hour (HH:MM - only MM counts)" if m == "hour" else "HH:MM")
        self.shifts.setEnabled(m == "shifts")
        self.reset_tag.setEnabled(m == "tag")
        self.keep_prev.setEnabled(m != "never")
        self._check()

    def _display(self):
        w, f = form_page()
        self.decimals = QSpinBox(minimum=0, maximum=9); self.decimals.setValue(int(self.a.get("decimals", 2)))
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
        a = dict(self.a)
        a.update(name=self.name.text().strip(), group=self.group.currentText().strip().strip("/"),
                 unit=self.unit.currentText().strip(), description=self.desc.text().strip(),
                 enabled=self.enabled.isChecked(), decimals=self.decimals.value(),
                 color="" if self._color.lower() == self.default_color.lower() else self._color,
                 source=self.source.currentData())
        k = self.kind
        if k == "statistic":
            a.update(stat=self.stat.currentData(), win_n=self.win_n.value(), win_unit=self.win_unit.currentData(),
                     win_mode="block" if self.mode_block.isChecked() else "moving")
        elif k == "totalizer":
            a.update(rate_per=self.rate_per.currentData(), factor=self.factor.value(),
                     skip_negative=self.skip_neg.isChecked())
        elif k == "counter":
            a["edge"] = self.edge.currentData()
        elif k == "runtime":
            a["time_unit"] = self.time_unit.currentData()
        elif k == "minmax":
            a["hold"] = "min" if self.hold_min.isChecked() else "max"
        else:
            a.update(change=self.change.currentData(), change_per=self.change_per.currentData())
        if ag.has_period(a):
            a.update(reset=self.reset.currentData(), reset_at=self.reset_at.text().strip(),
                     shifts=self.shifts.text().strip(), reset_tag=self.reset_tag.currentData(),
                     keep_prev=self.keep_prev.isChecked(), retain=self.retain.isChecked())
        return a

    def apply(self, a: dict):
        a.update(self.collect())

    def _check(self):
        if not getattr(self, "_ready", False):
            return
        a = self.collect()
        key = a.get("source")
        val, qual, _g, _t = self.live(*key) if key else ("", "", None, "")
        self.src_value.setText(f"Now: {val or '-'}  {qual}".strip() if key else "")
        if hasattr(self, "change_per"):
            self.change_per.setEnabled(self.change.currentData() == "rate")
        msg = self.engine.check(a, self.names, self.taken)
        tab = self._tab_of(msg) if msg else -1
        for i, t in enumerate(self._titles):
            self.tabs.setTabText(i, t + ("  ●" if i == tab else ""))
        st = self.engine.states.get(a["id"])
        if st is not None and st.has:
            set_prop(self.result, "level", "ok")
            self.result.setText(f"{st.value:.{a['decimals']}f} {a.get('unit', '')}".strip() + f"   ({st.quality})")
        else:
            set_prop(self.result, "level", "muted")
            self.result.setText("Starts at 0 when added" if a["kind"] in ("totalizer", "counter", "runtime")
                                else "Shows a value after the first samples")
        if hasattr(self, "next_reset"):
            nxt = ag.period_bounds(a, time.time())[1] if not msg else None
            self.next_reset.setText(f"Next reset: {_when(nxt)}" if nxt else "")
        if msg:
            set_prop(self.err, "level", "bad"); self.err.setText(f"✗ {self._titles[tab]}: {msg}")
            self.ok_btn.setEnabled(False)
            return
        set_prop(self.err, "level", "muted"); self.err.setText("✓ Ready")
        self.ok_btn.setEnabled(not getattr(self, "locked", False))

    def _tab_of(self, msg: str) -> int:
        m = msg.lower()
        if any(w in m for w in ("name", "called")):
            return 0
        if any(w in m for w in ("reset", "shift")) and "Period" in self._titles:
            return self._titles.index("Period")
        return 1

    def _ok(self):
        self._check()
        if not self.ok_btn.isEnabled():
            return
        self._timer.stop()
        self.accept()


def _when(t: float) -> str:
    return time.strftime("%a %Y-%m-%d %H:%M", time.localtime(t))
