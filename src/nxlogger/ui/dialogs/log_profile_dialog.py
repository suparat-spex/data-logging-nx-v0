"""Add / edit a logging profile. Signal (waveform) and Statistic (interval summary) each have
their own dialog layout - the type is chosen by the Add button, not inside the dialog."""
from __future__ import annotations

import copy

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from nxlogger.core import recording as dl
from nxlogger.core.project import STAT_INTERVALS
from nxlogger.ui.dialogs.common import ChannelPicker
from nxlogger.ui.theme.ui_kit import label, set_prop


class LogProfileDialog(QDialog):
    """
    prof["type"] decides the layout ("signal" / "statistic").
    add_variable() -> (new channel key, new choices) or None: optional "Add variable..." button.
    """

    def __init__(self, parent, prof: dict, choices, taken: set[str], add_variable=None):
        super().__init__(parent)
        self.p, self.taken, self.add_variable = copy.deepcopy(prof), taken, add_variable
        self.stat = self.p["type"] == "statistic"
        self.setWindowTitle("Statistic logging profile" if self.stat else "Data logging profile")
        self.setMinimumWidth(580)
        v = QVBoxLayout(self)
        v.addWidget(label(
            "Statistic: the stream is cut into intervals aligned to the clock (a 1 min interval starts "
            "at hh:mm:00); each interval gives one row with the sample count and the chosen statistics "
            "per tag - ideal for long-term trends and reports (a day at 1 min = 1440 rows)."
            if self.stat else
            "Data: every value of the chosen tags - one row per scan (number tags) or per sample "
            "(waveforms), scaled engineering values in CSV; untouched waveform packets in .wpk.", muted=True, small=True))
        f = QFormLayout(); f.setSpacing(8)
        self.name = QLineEdit(self.p["name"])
        self.name.setToolTip("Used in the file names: <profile>_<device>.csv")
        self.enabled = QCheckBox("Enabled", checked=self.p["enabled"])
        r = QHBoxLayout(); r.addWidget(self.name, 1); r.addWidget(self.enabled)
        f.addRow("Name", r)
        self.fmt = QComboBox()
        for key, (text, _) in (dl.STAT_FORMATS if self.stat else dl.FORMATS).items():
            self.fmt.addItem(text, key)
        self.fmt.setCurrentIndex(max(self.fmt.findData(self.p.get("format", "CSV")), 0))
        f.addRow("File format", self.fmt)
        if self.stat:
            ir = QHBoxLayout()
            self.preset = QComboBox()
            for text, sec in STAT_INTERVALS:
                self.preset.addItem(text, sec)
            self.preset.addItem("Custom", None)
            self.interval = QDoubleSpinBox(minimum=0.1, maximum=86400, decimals=1, suffix=" s",
                                           value=float(self.p.get("interval_s", 60)))
            ir.addWidget(self.preset); ir.addWidget(self.interval); ir.addStretch(1)
            f.addRow("Interval", ir)
            sr = QGridLayout(); sr.setHorizontalSpacing(14)
            self.stats = {}
            for i, k in enumerate(dl.STAT_KEYS):
                self.stats[k] = QCheckBox(dl.STAT_LABELS[k], checked=k in self.p.get("stats", []))
                sr.addWidget(self.stats[k], i // 4, i % 4)
            self.stats["inst"].setToolTip("Instantaneous: the last value of the interval")
            sw = QWidget(); sw.setLayout(sr); sr.setContentsMargins(0, 0, 0, 0)
            f.addRow("Statistics", sw)
            self.preset.currentIndexChanged.connect(self._preset)
            self.interval.valueChanged.connect(self._sync_preset)
            self._sync_preset()
        else:
            self.keep_raw = QCheckBox("Also save raw packets (.wpk) - lossless, can be re-decoded later",
                                      checked=bool(self.p.get("keep_raw")))
            f.addRow("", self.keep_raw)
        v.addLayout(f)

        hr = QHBoxLayout()
        hr.addWidget(label("Channels", name="sectionTitle")); hr.addStretch(1)
        if add_variable is not None:
            b = QPushButton("Add variable...")
            b.setToolTip("Create a calculated tag (formula / statistic / processing) and log it here")
            b.clicked.connect(self._add_variable)
            hr.addWidget(b)
        v.addLayout(hr)
        self.all = QCheckBox("All enabled tags and the variables marked Logged "
                             "(new ones are included automatically)",
                             checked=self.p.get("channels") is None)
        v.addWidget(self.all)
        self.picker_box = QVBoxLayout()
        v.addLayout(self.picker_box, 1)
        self._set_picker(choices, self.p.get("channels") or [])
        self.all.toggled.connect(lambda on: self.picker.setDisabled(on))
        self.err = QLabel(); self.err.setObjectName("statusMsg")
        v.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        set_prop(bb.button(QDialogButtonBox.StandardButton.Ok), "primary", True)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _set_picker(self, choices, chosen):
        if getattr(self, "picker", None) is not None:
            self.picker.setParent(None); self.picker.deleteLater()
        self.picker = ChannelPicker(choices, chosen)
        self.picker.setMinimumHeight(170)
        self.picker.setDisabled(self.all.isChecked())
        self.picker_box.addWidget(self.picker)

    def _add_variable(self):
        res = self.add_variable()
        if res is None:
            return
        key, choices = res
        chosen = self.picker.checked() + [list(key)]
        self._set_picker(choices, chosen)
        if self.all.isChecked():                     # it is logged anyway (Logged is on by default)
            set_prop(self.err, "level", "muted")
            self.err.setText("Variable added - logged with all enabled tags.")

    def _preset(self, i):
        sec = self.preset.itemData(i)
        if sec:
            self.interval.blockSignals(True); self.interval.setValue(sec); self.interval.blockSignals(False)

    def _sync_preset(self, *_):
        i = self.preset.findData(int(self.interval.value())) if self.interval.value() % 1 == 0 else -1
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(i if i >= 0 else self.preset.count() - 1)
        self.preset.blockSignals(False)

    def _ok(self):
        name = self.name.text().strip()
        err = ""
        if not name:
            err = "Enter a profile name (used in the file names)."
        elif dl.safe_name(name).lower() in self.taken:
            err = "Another profile already uses this name."
        elif self.stat and not any(b.isChecked() for b in self.stats.values()):
            err = "Choose at least one statistic."
        elif not self.all.isChecked() and not self.picker.checked():
            err = "Choose at least one tag."
        if err:
            set_prop(self.err, "level", "bad"); self.err.setText(err)
            return
        self.accept()

    def result_profile(self) -> dict:
        p = self.p
        p.update(name=self.name.text().strip(), enabled=self.enabled.isChecked(),
                 format=self.fmt.currentData(),
                 channels=None if self.all.isChecked() else self.picker.checked())
        if self.stat:
            p.update(interval_s=self.interval.value(),
                     stats=[k for k in dl.STAT_KEYS if self.stats[k].isChecked()], keep_raw=False)
        else:
            p.update(keep_raw=self.keep_raw.isChecked())
        return p
