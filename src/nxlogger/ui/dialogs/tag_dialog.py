"""Edit one tag: name, unit, colour, group, address, scaling (linear / two-point / formula),
display range. ChannelDialog is the common part (also every waveform channel); TagDialog adds
the Address tab of the device's connector."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget, QTabWidget,
                               QVBoxLayout, QWidget)

from nxlogger.core.channel_math import FUNCS, SCALE_TYPES, UNITS, make_scaler
from nxlogger.core.tags import DRIVERS, DeviceCfg, TagCfg, describe_address
from nxlogger.ui.dialogs.common import SchemaForm, form_page
from nxlogger.ui.icons import swatch
from nxlogger.ui.theme.ui_kit import label, set_prop


def _spin(val=0.0, lo=-1e9, hi=1e9, dec=6) -> QDoubleSpinBox:
    s = QDoubleSpinBox(minimum=lo, maximum=hi, decimals=dec, value=float(val))
    s.setKeyboardTracking(False)
    return s


# ====================================================================== dialog
class ChannelDialog(QDialog):
    """Edit one channel. `raw_value()` returns the latest mean of the unscaled values (or None)."""

    def __init__(self, parent, topic_name: str, ch, default_color: str, raw_value, mqtt_topic="",
                 alarms: bool = True):
        super().__init__(parent)
        self.ch, self.default_color, self.raw_value = ch, default_color, raw_value
        self.alarms = alarms           # False: alarms live in their own document (MQDAQ v6)
        self.setWindowTitle(f"Tag - {topic_name} · {ch.name}")
        self.setMinimumWidth(520)
        self._color = ch.color or default_color

        tabs = self.tabs = QTabWidget()
        tabs.addTab(self._general(topic_name, mqtt_topic), "General")
        tabs.addTab(self._scaling(), "Scaling")
        tabs.addTab(self._display(), "Display && alarms" if alarms else "Display")

        self.err = QLabel(); self.err.setObjectName("statusMsg"); self.err.setWordWrap(True)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        set_prop(self.ok_btn, "primary", True)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        v = QVBoxLayout(self)
        v.addWidget(tabs, 1); v.addWidget(self.err); v.addWidget(bb)

        self._timer = QTimer(self, interval=500, timeout=self._preview)
        self._timer.start()
        self._preview()

    # ---- pages
    def _general(self, topic_name, mqtt_topic):
        w = QWidget(); f = QFormLayout(w)
        f.addRow("Device", label(f"{topic_name}   ({mqtt_topic})" if mqtt_topic else topic_name, muted=True))
        if getattr(self.ch, "kind", "array") == "array":
            f.addRow("Channel", label(f"CH{self.ch.index + 1}  (index {self.ch.index} in the packet)", muted=True))
        self.name = QLineEdit(self.ch.name)
        self.unit = QComboBox(); self.unit.setEditable(True); self.unit.addItems(UNITS)
        self.unit.setCurrentText(self.ch.unit)
        self.unit.currentTextChanged.connect(lambda _: self._preview())
        self.color_btn = QPushButton(); self.color_btn.clicked.connect(self._pick_color)
        reset = QPushButton("Default"); reset.clicked.connect(lambda: self._set_color(self.default_color))
        row = QHBoxLayout(); row.addWidget(self.color_btn); row.addWidget(reset); row.addStretch(1)
        self.desc = QPlainTextEdit(self.ch.description); self.desc.setMaximumHeight(70)
        self.desc.setPlaceholderText("Sensor, location, calibration date... (saved in the files)")
        self.enabled = QCheckBox("Enabled - show in plots and log to file", checked=self.ch.enabled)
        f.addRow("Name", self.name)
        f.addRow("Unit", self.unit)
        f.addRow("Colour", row)
        f.addRow("Description", self.desc)
        f.addRow("", self.enabled)
        self._set_color(self._color)
        return w

    def _scaling(self):
        s = self.ch.scale or {"type": "none"}
        w = QWidget(); v = QVBoxLayout(w)
        f = QFormLayout()
        self.kind = QComboBox()
        for key, text in SCALE_TYPES:
            self.kind.addItem(text, key)
        self.kind.setCurrentIndex(max(0, self.kind.findData(s.get("type", "none"))))
        f.addRow("Scaling", self.kind)
        v.addLayout(f)

        self.pages = QStackedWidget()
        self.pages.addWidget(label("Values are used as received from the device.", muted=True))
        # linear
        lw = QWidget(); lf = QFormLayout(lw)
        self.m = _spin(s.get("m", 1.0)); self.b = _spin(s.get("b", 0.0))
        tare = QPushButton("Tare (zero now)")
        tare.setToolTip("Set the offset so the current value reads 0")
        tare.clicked.connect(self._tare)
        lf.addRow("Slope m", self.m)
        brow = QHBoxLayout(); brow.addWidget(self.b, 1); brow.addWidget(tare)
        lf.addRow("Offset b", brow)
        self.pages.addWidget(lw)
        # two-point
        tw = QWidget(); tf = QFormLayout(tw)
        self.x1, self.y1 = _spin(s.get("x1", 0.0)), _spin(s.get("y1", 0.0))
        self.x2, self.y2 = _spin(s.get("x2", 1.0)), _spin(s.get("y2", 1.0))
        for xs, ys, n in ((self.x1, self.y1, 1), (self.x2, self.y2, 2)):
            use = QPushButton("Use current"); use.setToolTip(f"x{n} = current raw value")
            use.clicked.connect(lambda _=False, s_=xs: self._use_current(s_))
            r = QHBoxLayout(); r.addWidget(QLabel(f"raw x{n}")); r.addWidget(xs, 1); r.addWidget(use)
            r.addSpacing(10); r.addWidget(QLabel(f"→  y{n}")); r.addWidget(ys, 1)
            tf.addRow(f"Point {n}", r)
        self.pages.addWidget(tw)
        # formula
        fw = QWidget(); ff = QVBoxLayout(fw); ff.setContentsMargins(0, 0, 0, 0)
        self.expr = QLineEdit(s.get("expr", ""))
        self.expr.setPlaceholderText("x * 9/5 + 32")
        self.expr.textChanged.connect(lambda _: self._preview())
        ff.addWidget(self.expr)
        help_ = label("x = value from the device.  Operators  + - * / ** %  and comparisons.  "
                      "Functions: " + ", ".join(sorted(FUNCS)) + ".  Constants: pi, e.  "
                      "Examples:  (x - 4) * 6.25   ·   sqrt(abs(x))   ·   where(x > 100, 100, x)",
                      muted=True, small=True)
        help_.setWordWrap(True)
        ff.addWidget(help_)
        self.pages.addWidget(fw)
        v.addWidget(self.pages)

        self.preview = QLabel(); self.preview.setObjectName("sectionTitle")
        v.addWidget(self.preview)
        v.addStretch(1)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        for sp in (self.m, self.b, self.x1, self.y1, self.x2, self.y2):
            sp.valueChanged.connect(lambda _: self._preview())
        self._kind_changed()
        return w

    def _display(self):
        w = QWidget(); f = QFormLayout(w)
        self.dec = QSpinBox(minimum=0, maximum=8, value=int(self.ch.decimals))
        f.addRow("Decimals", self.dec)
        self.yfix = QCheckBox("Fixed Y range on plots", checked=self.ch.y_min is not None)
        self.ymin = _spin(self.ch.y_min if self.ch.y_min is not None else -10, dec=4)
        self.ymax = _spin(self.ch.y_max if self.ch.y_max is not None else 10, dec=4)
        r = QHBoxLayout(); r.addWidget(self.ymin); r.addWidget(QLabel("to")); r.addWidget(self.ymax)
        f.addRow(self.yfix)
        f.addRow("Y range", r)
        self.yfix.toggled.connect(lambda on: (self.ymin.setEnabled(on), self.ymax.setEnabled(on)))
        self.ymin.setEnabled(self.yfix.isChecked()); self.ymax.setEnabled(self.yfix.isChecked())

        f.addRow(label("Alarms", name="sectionTitle"))
        if not self.alarms:
            note = label("Alarms (4 levels with hysteresis and delay) are set in "
                         "the Alarms document: Project Files → Alarms.", muted=True, small=True)
            note.setWordWrap(True)
            f.addRow(note)
            return w
        self.lo_on = QCheckBox("Low alarm below", checked=self.ch.alarm_lo is not None)
        self.lo = _spin(self.ch.alarm_lo if self.ch.alarm_lo is not None else 0, dec=4)
        self.hi_on = QCheckBox("High alarm above", checked=self.ch.alarm_hi is not None)
        self.hi = _spin(self.ch.alarm_hi if self.ch.alarm_hi is not None else 0, dec=4)
        for on, sp in ((self.lo_on, self.lo), (self.hi_on, self.hi)):
            sp.setEnabled(on.isChecked())
            on.toggled.connect(sp.setEnabled)
            f.addRow(on, sp)
        f.addRow(label("Alarms use the latest scaled value: the live value turns red and "
                       "the event is written to the Log.", muted=True, small=True))
        return w

    # ---- helpers
    def _set_color(self, c: str):
        self._color = c
        self.color_btn.setIcon(swatch(c, 14))
        self.color_btn.setText(c)

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Channel colour")
        if c.isValid():
            self._set_color(c.name())

    def _kind_changed(self):
        self.pages.setCurrentIndex(self.kind.currentIndex())
        self._preview()

    def _scale_dict(self) -> dict:
        k = self.kind.currentData()
        if k == "linear":
            return {"type": "linear", "m": self.m.value(), "b": self.b.value()}
        if k == "twopoint":
            return {"type": "twopoint", "x1": self.x1.value(), "y1": self.y1.value(),
                    "x2": self.x2.value(), "y2": self.y2.value()}
        if k == "formula":
            return {"type": "formula", "expr": self.expr.text().strip()}
        return {"type": "none"}

    def _use_current(self, spin):
        x = self.raw_value()
        if x is not None:
            spin.setValue(x)

    def _tare(self):
        x = self.raw_value()
        if x is not None:
            self.b.setValue(-self.m.value() * x)

    def _preview(self):
        if not hasattr(self, "ok_btn"):                 # still building the dialog
            return
        x = self.raw_value()
        unit = self.unit.currentText()
        try:
            f = make_scaler(self._scale_dict())
            ok = True
            msg = ""
        except ValueError as e:
            f, ok, msg = None, False, str(e)
        set_prop(self.err, "level", "bad" if not ok else "muted")
        self.err.setText(msg)
        self.ok_btn.setEnabled(ok)
        if x is None:
            self.preview.setText("Preview: no data yet - connect to see raw → scaled values")
        elif ok:
            y = float(f(np.array([x]))[0]) if f else x
            self.preview.setText(f"Preview:  raw {x:.6g}   →   {y:.6g} {unit}".rstrip())
        else:
            self.preview.setText(f"Preview:  raw {x:.6g}   →   ?")

    def _ok(self):
        if self.yfix.isChecked() and self.ymin.value() >= self.ymax.value():
            set_prop(self.err, "level", "bad")
            self.err.setText("Y range: min must be below max.")
            return
        if self.alarms and self.lo_on.isChecked() and self.hi_on.isChecked() and self.lo.value() >= self.hi.value():
            set_prop(self.err, "level", "bad")
            self.err.setText("Low alarm must be below the high alarm.")
            return
        self.accept()

    def apply(self, ch):
        """Write the dialog values into the ChannelCfg."""
        ch.name = self.name.text().strip() or ch.name
        ch.unit = self.unit.currentText().strip()
        ch.color = "" if self._color.lower() == self.default_color.lower() else self._color
        ch.description = self.desc.toPlainText().strip()
        ch.enabled = self.enabled.isChecked()
        ch.scale = self._scale_dict()
        ch.decimals = self.dec.value()
        ch.y_min = self.ymin.value() if self.yfix.isChecked() else None
        ch.y_max = self.ymax.value() if self.yfix.isChecked() else None
        if not self.alarms:
            return
        ch.alarm_lo = self.lo.value() if self.lo_on.isChecked() else None
        ch.alarm_hi = self.hi.value() if self.hi_on.isChecked() else None


# ====================================================================== tag
class TagDialog(ChannelDialog):
    def __init__(self, parent, dev: DeviceCfg, tag: TagCfg, default_color: str, raw_value, groups=None):
        super().__init__(parent, dev.name, tag, default_color, raw_value, dev.topic, alarms=False)
        self.dev, self.tag = dev, tag
        self.setWindowTitle(f"Tag - {dev.name} · {tag.name}")
        tabs = self.tabs
        w, f = form_page()
        self.group = QComboBox(); self.group.setEditable(True)
        self.group.addItems([""] + (dev.all_groups() if groups is None else list(groups)))   # the project's groups
        self.group.setCurrentText(tag.group)
        self.group.setToolTip("Group (Tags > Groups, one tree for all tags); use / for sub-groups (Boiler/Pumps)")
        f.addRow("Group", self.group)
        self.access = QCheckBox("Writable (RW) - values can be written from the tag table",
                                checked=tag.access == "RW")
        if tag.kind == "array":
            f.addRow("Address", label(f"{describe_address(dev.driver, '', tag)} of the waveform packets on "
                                      f"{dev.settings.get('topic', '')}", muted=True))
            self.addr = None
        else:
            f.addRow("", self.access)
            self.addr = SchemaForm(DRIVERS[dev.driver]["address"], tag.address)
            box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(0, 0, 0, 0)
            v.addWidget(self.addr)
            f.addRow(label("Address", name="sectionTitle"))
            f.addRow(box)
        tabs.insertTab(1, w, "Address")
        tabs.setCurrentIndex(0)

    def apply(self, tag: TagCfg):
        super().apply(tag)
        tag.group = "/".join(p.strip() for p in self.group.currentText().split("/") if p.strip())
        if self.addr is not None:
            tag.address = {**tag.address, **self.addr.values()}
            tag.access = "RW" if self.access.isChecked() else "R"
