"""Add / edit one alarm: the settings pop-up, tabs style like devices, tags, assets and logging.

  General  name, enabled, tag, reference tag (deviation dH / dL, or DV's setpoint), description,
           icon (the app's icons - alarm_rules.ICONS; Default = its type's)
  Levels   up to 4 levels (Level 1 = the highest priority; Limit: HH / H / L / LL): type, setpoint,
           SP (DV without a reference) or = / ≠ (State), hysteresis, on-delay
  Notify   the notification profiles it sends to

[Add alarm] / [Save] stays off until it is valid (a tag, a level on, a reference / band / time
where its type needs one).
"""
from __future__ import annotations

import copy

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout,
                               QPushButton, QToolButton,
                               QLineEdit, QListWidget, QListWidgetItem, QStackedWidget, QTabWidget, QVBoxLayout,
                               QWidget)

from nxlogger.core.alarm_rules import GROUP, ICONS, PROFILE_KINDS, TYPE_GROUP, REF_TYPES, STATE_OPS, TYPES, normalize_alarm
from nxlogger.ui.dialogs.common import form_page, spin
from nxlogger.ui.icons import level_icon
from nxlogger.ui.theme.ui_kit import T, label, make_icon, set_prop

LIMIT_NAMES = ["HH", "H", "L", "LL"]                  # Limit alarms: the usual names of the 4 levels
ICON_COLS = 10


class AlarmDialog(QDialog):
    """alarm: the definition (a copy is edited - result_alarm()); choices: [(tid, idx, label, colour,
    unit)]; profiles: the project's notification profiles."""

    def __init__(self, parent, alarm: dict, choices, profiles: list[dict]):
        super().__init__(parent)
        self.a = copy.deepcopy(normalize_alarm(alarm))
        self.choices = choices
        self.setWindowTitle("Alarm")
        self.setMinimumWidth(720)
        tabs = self.tabs = QTabWidget()

        # General ---------------------------------------------------------------
        g, gf = form_page()
        self.name = QLineEdit(self.a["name"])
        self.enabled = QCheckBox("Enabled", checked=self.a["enabled"])
        self.chan = QComboBox(); self.ref = QComboBox()
        self.ref.addItem("(none)", None)
        for tid, idx, lbl, _, _ in choices:
            self.chan.addItem(lbl, [tid, idx]); self.ref.addItem(lbl, [tid, idx])
        if self.a["channel"]:
            self.chan.setCurrentIndex(max(self.chan.findData(list(self.a["channel"])), 0))
        if self.a.get("ref"):
            self.ref.setCurrentIndex(max(self.ref.findData(list(self.a["ref"])), 0))
        self.ref.setToolTip("Deviation: dH / dL = tag − reference; DV's setpoint follows the reference "
                            "(else its SP)")
        self.desc = QLineEdit(self.a.get("description", ""))
        self.desc.setPlaceholderText("optional - shown in the alarm lists and sent to targets")
        gf.addRow("Name", self.name)
        gf.addRow("", self.enabled)
        gf.addRow("Tag", self.chan)
        gf.addRow("Reference tag", self.ref)
        gf.addRow("Description", self.desc)
        self._icon = self.a.get("icon") if self.a.get("icon") in ICONS else ""
        icons = QWidget(); iv = QVBoxLayout(icons); iv.setContentsMargins(0, 0, 0, 0); iv.setSpacing(4)
        igrid = QGridLayout(); igrid.setSpacing(2)
        self.icon_btns: dict[str, QToolButton] = {}
        for i, name_ in enumerate(ICONS):
            b = QToolButton(); b.setCheckable(True); b.setAutoRaise(True); b.setToolTip(name_)
            b.setIconSize(QSize(18, 18)); b.setFixedSize(30, 30)
            b.clicked.connect(lambda _=False, n=name_: self._set_icon(n))
            igrid.addWidget(b, i // ICON_COLS, i % ICON_COLS)
            self.icon_btns[name_] = b
        iv.addLayout(igrid)
        irow = QHBoxLayout(); irow.setSpacing(6)
        self.icon_default = QPushButton("Default"); self.icon_default.clicked.connect(lambda: self._set_icon(""))
        self.icon_default.setToolTip("The icon of its type (Limit, Rate of change, Deviation, State, Data loss)")
        self.icon_note = label("", muted=True, small=True)
        irow.addWidget(self.icon_default); irow.addWidget(self.icon_note, 1)
        iv.addLayout(irow)
        gf.addRow("Icon", icons)
        tabs.addTab(g, "General")

        # Levels ----------------------------------------------------------------
        lw = QWidget(); lv = QVBoxLayout(lw); lv.setContentsMargins(4, 4, 4, 4); lv.setSpacing(6)
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(6)
        for col, text in enumerate(("Level", "Type", "Setpoint", "SP / state", "Hysteresis", "On-delay")):
            grid.addWidget(label(text, muted=True, small=True), 0, col)
        self.rows = []
        for i, lvl in enumerate(self.a["levels"]):
            on = QCheckBox(f"Level {i + 1}", checked=lvl["enabled"])
            on.setIcon(level_icon(i + 1))
            typ = QComboBox()
            for code, text in TYPES.items():
                typ.addItem(f"{code}  ·  {text}", code)
            typ.setCurrentIndex(max(typ.findData(lvl["type"]), 0))
            sp = spin(lvl["value"])
            extra = QStackedWidget()                   # DV: its setpoint · S: = / ≠ · others: nothing
            extra.addWidget(QWidget())
            cen = spin(lvl.get("sp", 0)); cen.setToolTip("Deviation (DV): the setpoint the band is around "
                                                       "(a reference tag on General replaces it)")
            extra.addWidget(cen)
            op = QComboBox()
            for k, text in STATE_OPS.items():
                op.addItem(text, k)
            op.setCurrentIndex(max(op.findData(lvl.get("op", "=")), 0))
            op.setToolTip("State: alarm while the value = this state, or while it is ≠ (not the normal state)")
            extra.addWidget(op)
            hy = spin(lvl.get("hyst", 0), 0)
            dl = spin(lvl.get("delay_s", 0), 0, 86400, 1); dl.setSuffix(" s")
            hy.setToolTip("Deadband: the alarm clears only when the value is back by this much")
            dl.setToolTip("The condition must hold this long before the alarm turns ON")
            for c, w in enumerate((on, typ, sp, extra, hy, dl)):
                grid.addWidget(w, i + 1, c)
            row = (on, typ, sp, extra, cen, op, hy, dl)
            self.rows.append(row)
            on.toggled.connect(lambda _=False, rw=row: (self._row_enabled(rw), self._paint_icons(), self._check()))
            typ.currentIndexChanged.connect(lambda _=0, rw=row: (self._row_type(rw), self._units(),
                                                                 self._paint_icons(), self._check()))
            for w in (sp, cen, hy, dl):
                w.valueChanged.connect(lambda *_: self._check())
            self._row_type(row)
            self._row_enabled(row)
        grid.setColumnStretch(1, 1)
        lv.addLayout(grid)
        self.levels_note = label("", muted=True, small=True); self.levels_note.setWordWrap(True)
        lv.addWidget(self.levels_note)
        lv.addStretch(1)
        tabs.addTab(lw, "Levels")

        # Notify ----------------------------------------------------------------
        nw = QWidget(); nv = QVBoxLayout(nw); nv.setContentsMargins(4, 4, 4, 4)
        nv.addWidget(label("Where its ON / OFF events go (profiles: Alarms > Notifications).", muted=True, small=True))
        self.plist = QListWidget()
        for p in profiles:
            it = QListWidgetItem(f"{p['name']}  ·  {PROFILE_KINDS.get(p['kind'], p['kind'])}"
                                 + ("" if p.get("enabled") else "  (disabled)"))
            it.setData(Qt.ItemDataRole.UserRole, p["id"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if p["id"] in self.a["profiles"] else Qt.CheckState.Unchecked)
            self.plist.addItem(it)
        if not profiles:
            self.plist.addItem("No notification profiles yet - Alarms > Notifications > Add")
        nv.addWidget(self.plist, 1)
        tabs.addTab(nw, "Notify")

        self.err = label("", small=True); self.err.setObjectName("statusMsg")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addWidget(tabs, 1); lay.addWidget(self.err); lay.addWidget(bb)
        self.chan.currentIndexChanged.connect(lambda _: (self._units(), self._check()))
        self.ref.currentIndexChanged.connect(lambda _: self._check())
        self._units()
        self._paint_icons()
        self._check()

    # ---------------------------------------------------------------- icon
    def _group(self) -> str:
        """Its type now: the group of the first level switched on (else Level 1's)."""
        on = [r for r in self.rows if r[0].isChecked()] or self.rows[:1]
        return TYPE_GROUP.get(on[0][1].currentData(), "limit")

    def _set_icon(self, name: str):
        self._icon = name if name in ICONS else ""
        self._paint_icons()

    def _paint_icons(self):
        if not hasattr(self, "icon_btns"):
            return
        g = GROUP[self._group()]
        shown = self._icon or g[2]
        for n, b in self.icon_btns.items():
            b.setIcon(make_icon(n, T["text"] if n == shown else T["muted"]))
            b.setChecked(n == shown)
        self.icon_default.setEnabled(bool(self._icon))
        self.icon_note.setText(f"Default: the {g[1].split('  ')[0]} icon" if not self._icon else f"Icon: {self._icon}")
        head = getattr(self, "header_icon", None)            # the pop-up's header (common.popup_form)
        if head is not None:
            head.setPixmap(make_icon(shown, T["accent"]).pixmap(32, 32))

    # ---------------------------------------------------------------- levels
    def _row_enabled(self, row):
        for w in (row[1], row[2], row[3], row[6], row[7]):
            w.setEnabled(row[0].isChecked())

    def _row_type(self, row):
        t = row[1].currentData()
        row[3].setCurrentIndex(1 if t == "DV" else (2 if t == "S" else 0))
        self._names()

    def _names(self):
        """Limit alarms laid out H, H, L, L (a new Limit alarm): Level 1-4 read HH / H / L / LL, the
        usual ISA-18.2 names; any other layout keeps Level 1-4 (a name must match its type)."""
        types = [r[1].currentData() for r in self.rows]
        limit = types == ["H", "H", "L", "L"]
        for i, r in enumerate(self.rows):
            r[0].setText(f"{LIMIT_NAMES[i]}  (L{i + 1})" if limit else f"Level {i + 1}")
        if hasattr(self, "levels_note"):
            self.levels_note.setText(
                "Level 1 has the highest priority. H / L test the block max / min (a spike inside a packet "
                "still trips). DV: |value − SP| > setpoint. S: alarm while the value = (or ≠) the state "
                "(1 = on / tripped). D: no data for the setpoint in seconds.")

    def _unit(self) -> str:
        i = self.chan.currentIndex()
        return self.choices[i][4] if 0 <= i < len(self.choices) else ""

    def _units(self):
        u = self._unit()
        for on, typ, sp, extra, cen, op, hy, dl in self.rows:
            t = typ.currentData()
            suf = " s" if t == "D" else ("" if t == "S" else (f" {u}/s" if t in ("RH", "RL") else (f" {u}" if u else "")))
            sp.setSuffix(suf); hy.setSuffix(suf); cen.setSuffix(f" {u}" if u else "")

    # ---------------------------------------------------------------- result
    def _problem(self) -> str:
        if self.chan.currentData() is None:
            return "General: choose a tag."
        on = [r for r in self.rows if r[0].isChecked()]
        if not on:
            return "Levels: switch on at least one level."
        types = {r[1].currentData() for r in on}
        if types & REF_TYPES and self.ref.currentData() is None:
            return "General: dH / dL need a reference tag."
        if any(r[1].currentData() == "D" and r[2].value() <= 0 for r in on):
            return "Levels: data loss - the setpoint is a time in seconds, above 0."
        if any(r[1].currentData() == "DV" and r[2].value() <= 0 for r in on):
            return "Levels: deviation - the setpoint is the band, above 0."
        return ""

    def _check(self, *_):
        if not hasattr(self, "ok_btn"):
            return
        msg = self._problem()
        set_prop(self.err, "level", "bad" if msg else "muted")
        n = sum(1 for r in self.rows if r[0].isChecked())
        self.err.setText(f"✗ {msg}" if msg else f"✓ Ready · {n} level(s)")
        self.ok_btn.setEnabled(not msg)

    def _ok(self):
        self._check()
        if self.ok_btn.isEnabled():
            self.accept()

    def result_alarm(self) -> dict:
        a = self.a
        a["name"] = self.name.text().strip() or self.chan.currentText()
        a["enabled"] = self.enabled.isChecked()
        a["channel"] = self.chan.currentData()
        a["ref"] = self.ref.currentData()
        a["description"] = self.desc.text().strip()
        a["icon"] = self._icon
        a["levels"] = [{"enabled": on.isChecked(), "type": typ.currentData(), "value": sp.value(),
                        "hyst": hy.value(), "delay_s": dl.value(), "sp": cen.value(), "op": op.currentData()}
                       for on, typ, sp, extra, cen, op, hy, dl in self.rows]
        a["profiles"] = [self.plist.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.plist.count())
                         if self.plist.item(i).data(Qt.ItemDataRole.UserRole)
                         and self.plist.item(i).checkState() == Qt.CheckState.Checked]
        return a

