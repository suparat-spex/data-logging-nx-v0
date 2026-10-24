"""Add / edit one alarm: tag, 4 levels, notification profiles."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QVBoxLayout)

from nxlogger.core.alarm_rules import PROFILE_KINDS, REF_TYPES, TYPES, normalize_alarm
from nxlogger.ui.dialogs.common import spin
from nxlogger.ui.icons import level_icon
from nxlogger.ui.theme.ui_kit import label, set_prop


class AlarmDialog(QDialog):
    """Add / edit one alarm: tag, 4 levels, notification profiles."""

    def __init__(self, parent, alarm: dict, choices, profiles: list[dict]):
        super().__init__(parent)
        self.a = copy.deepcopy(normalize_alarm(alarm))
        self.choices = choices                       # [(tid, idx, label, color, unit)]
        self.setWindowTitle("Edit alarm" if alarm.get("channel") else "Add alarm")
        self.setMinimumWidth(720)
        v = QVBoxLayout(self); v.setSpacing(10)

        f = QFormLayout(); f.setSpacing(8)
        self.name = QLineEdit(self.a["name"])
        self.enabled = QCheckBox("Enabled", checked=self.a["enabled"])
        r = QHBoxLayout(); r.addWidget(self.name, 1); r.addWidget(self.enabled)
        f.addRow("Name", r)
        self.chan = QComboBox(); self.ref = QComboBox()
        self.ref.addItem("(none)", None)
        for tid, idx, lbl, _, _ in choices:
            self.chan.addItem(lbl, [tid, idx]); self.ref.addItem(lbl, [tid, idx])
        if self.a["channel"]:
            self.chan.setCurrentIndex(max(self.chan.findData(list(self.a["channel"])), 0))
        if self.a.get("ref"):
            self.ref.setCurrentIndex(max(self.ref.findData(list(self.a["ref"])), 0))
        f.addRow("Tag", self.chan)
        f.addRow("Reference tag", self.ref)
        self.ref.setToolTip("Used by the difference types dH / dL: value = tag − reference")
        self.desc = QLineEdit(self.a.get("description", ""))
        self.desc.setPlaceholderText("optional - shown in the alarm list and sent to targets")
        f.addRow("Description", self.desc)
        v.addLayout(f)

        v.addWidget(label("Alarm levels", name="sectionTitle"))
        g = QGridLayout(); g.setHorizontalSpacing(10); g.setVerticalSpacing(6)
        for col, text in enumerate(("Level", "Type", "Setpoint", "Hysteresis", "On-delay")):
            g.addWidget(label(text, muted=True, small=True), 0, col)
        self.rows = []
        for i, lv in enumerate(self.a["levels"]):
            on = QCheckBox(f"Level {i + 1}", checked=lv["enabled"])
            on.setIcon(level_icon(i + 1))
            typ = QComboBox()
            for code, text in TYPES.items():
                typ.addItem(f"{code}  ·  {text}", code)
            typ.setCurrentIndex(max(typ.findData(lv["type"]), 0))
            sp = spin(lv["value"]); hy = spin(lv.get("hyst", 0), 0); dl = spin(lv.get("delay_s", 0), 0, 86400, 1)
            dl.setSuffix(" s")
            hy.setToolTip("Deadband: the alarm clears only when the value is back by this much")
            dl.setToolTip("The condition must hold this long before the alarm turns ON "
                          "(delay alarm, GA10 tH / tL)")
            for c, w in enumerate((on, typ, sp, hy, dl)):
                g.addWidget(w, i + 1, c)
            row = (on, typ, sp, hy, dl)
            self.rows.append(row)
            on.toggled.connect(lambda _=False, rw=row: self._row_enabled(rw))
            typ.currentIndexChanged.connect(lambda _=0: self._units())
            self._row_enabled(row)
        g.setColumnStretch(1, 1)
        v.addLayout(g)
        v.addWidget(label("H / L test the block max / min (a spike inside a packet still trips). "
                          "Level 1 has the highest priority.", muted=True, small=True))

        v.addWidget(label("Notify", name="sectionTitle"))
        self.plist = QListWidget(); self.plist.setMaximumHeight(110)
        for p in profiles:
            it = QListWidgetItem(f"{p['name']}  ·  {PROFILE_KINDS.get(p['kind'], p['kind'])}"
                                 + ("" if p.get("enabled") else "  (disabled)"))
            it.setData(Qt.ItemDataRole.UserRole, p["id"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if p["id"] in self.a["profiles"] else Qt.CheckState.Unchecked)
            self.plist.addItem(it)
        if not profiles:
            self.plist.addItem("No notification profiles yet - add them on the Notifications tab")
        v.addWidget(self.plist)

        self.err = QLabel(); self.err.setObjectName("statusMsg"); self.err.setWordWrap(True)
        v.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        set_prop(bb.button(QDialogButtonBox.StandardButton.Ok), "primary", True)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.chan.currentIndexChanged.connect(lambda _: self._units())
        self._units()

    def _row_enabled(self, row):
        for w in row[1:]:
            w.setEnabled(row[0].isChecked())

    def _unit(self) -> str:
        i = self.chan.currentIndex()
        return self.choices[i][4] if 0 <= i < len(self.choices) else ""

    def _units(self):
        u = self._unit()
        for on, typ, sp, hy, dl in self.rows:
            t = typ.currentData()
            suf = " s" if t == "D" else (f" {u}/s" if t in ("RH", "RL") else (f" {u}" if u else ""))
            sp.setSuffix(suf); hy.setSuffix(suf)

    def _ok(self):
        err = ""
        if self.chan.currentData() is None:
            err = "Choose a tag."
        elif not any(r[0].isChecked() for r in self.rows):
            err = "Enable at least one level."
        elif any(r[0].isChecked() and r[1].currentData() in REF_TYPES for r in self.rows) \
                and self.ref.currentData() is None:
            err = "Difference alarms (dH / dL) need a reference tag."
        elif any(r[0].isChecked() and r[1].currentData() == "D" and r[2].value() <= 0 for r in self.rows):
            err = "Data loss: the setpoint is a time in seconds and must be above 0."
        if err:
            set_prop(self.err, "level", "bad"); self.err.setText(err)
            return
        self.accept()

    def result_alarm(self) -> dict:
        a = self.a
        a["name"] = self.name.text().strip() or self.chan.currentText()
        a["enabled"] = self.enabled.isChecked()
        a["channel"] = self.chan.currentData()
        a["ref"] = self.ref.currentData()
        a["description"] = self.desc.text().strip()
        a["levels"] = [{"enabled": on.isChecked(), "type": typ.currentData(), "value": sp.value(),
                        "hyst": hy.value(), "delay_s": dl.value()}
                       for on, typ, sp, hy, dl in self.rows]
        a["profiles"] = [self.plist.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.plist.count())
                         if self.plist.item(i).data(Qt.ItemDataRole.UserRole)
                         and self.plist.item(i).checkState() == Qt.CheckState.Checked]
        return a
