"""AI/ML data log settings: folder, file period, compression, retention, auto-start.
The logger itself is core/ml_log.py; this dialog only edits Project.ml_log."""
from __future__ import annotations

import copy
import os

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QSpinBox,
                               QVBoxLayout)

from nxlogger.core import ml_log as mlg
from nxlogger.core import recording as dl
from nxlogger.ui.theme.ui_kit import label, set_prop


class MlLogDialog(QDialog):
    """settings: Project.ml_log dict. default_folder: shown when folder is "" (recordings/AI_Data)."""

    def __init__(self, parent, settings: dict, default_folder: str):
        super().__init__(parent)
        self.s, self.default_folder = copy.deepcopy(settings), default_folder
        self.setWindowTitle("AI/ML data log")
        self.setMinimumWidth(560)
        v = QVBoxLayout(self)
        intro = label(
            "Continuous raw logging for anomaly detection and model training - runs on its own, "
            "independent of Record. Every topic is cut into files of one period, aligned to the clock, "
            "in one folder per day: <folder>/YYYY-MM-DD/<topic>/YYYY-MM-DD_HHMMSS.wpk.zst, with "
            "events.csv (alarms, markers) and meta.json (channels, units, scaling) per day. "
            "Lossless: every packet goes into exactly one file.", muted=True, small=True)
        intro.setWordWrap(True)
        v.addWidget(intro)
        f = QFormLayout(); f.setSpacing(8)
        r = QHBoxLayout()
        self.folder = QLineEdit(self.s.get("folder", ""))
        self.folder.setPlaceholderText(default_folder)
        self.folder.setToolTip("Empty = AI_Data inside the recordings folder")
        b = QPushButton("Browse...")
        b.clicked.connect(self._browse)
        r.addWidget(self.folder, 1); r.addWidget(b)
        f.addRow("Folder", r)
        self.period = QComboBox()
        for text, sec in mlg.PERIODS:
            self.period.addItem(text, sec)
        self.period.setCurrentIndex(max(self.period.findData(int(self.s.get("period_s", 60))), 0))
        self.period.setToolTip("Aim for files of about 10-100 MB: short periods for fast vibration "
                               "data, long periods for slow process data")
        f.addRow("File period", self.period)
        self.comp = QComboBox()
        for key, (text, _) in dl.COMPRESSIONS.items():
            self.comp.addItem(text, key)
        if not dl.zstd_available():              # Python < 3.14 without the zstandard package
            i = self.comp.findData("zstd")
            self.comp.model().item(i).setEnabled(False)
            self.comp.setItemText(i, "zstd - not available (Python 3.14+ or pip install zstandard)")
            if self.s.get("compression") == "zstd":
                self.s["compression"] = "gzip"
        self.comp.setCurrentIndex(max(self.comp.findData(self.s.get("compression", "zstd")), 0))
        self.comp.setToolTip("Lossless. Each file is compressed after it closes, so a power cut "
                             "only affects the file being written")
        f.addRow("Compression", self.comp)
        self.keep = QSpinBox(minimum=0, maximum=36500, suffix=" days", value=int(self.s.get("keep_days", 0)))
        self.keep.setSpecialValueText("Keep everything")
        self.keep.setToolTip("Delete day folders older than this, so the disk never fills up. "
                             "0 = keep everything")
        f.addRow("Keep data", self.keep)
        self.auto = QCheckBox("Start automatically when NX Logger opens this project",
                              checked=bool(self.s.get("autostart")))
        self.auto.setToolTip("For unattended 24/7 logging: after a restart or power cut, logging "
                             "continues without anyone pressing a button")
        f.addRow("", self.auto)
        v.addLayout(f)
        self.err = label("", small=True); self.err.setObjectName("statusMsg")
        v.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        set_prop(bb.button(QDialogButtonBox.StandardButton.Ok), "primary", True)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "AI/ML data folder",
                                             self.folder.text() or self.default_folder)
        if d:
            self.folder.setText(d)

    def _ok(self):
        d = self.folder.text().strip()
        if d and not os.path.isabs(d):
            set_prop(self.err, "level", "bad")
            self.err.setText("Enter a full folder path (or leave it empty for the default).")
            return
        self.accept()

    def result_settings(self) -> dict:
        self.s.update(folder=self.folder.text().strip(), period_s=self.period.currentData(),
                      compression=self.comp.currentData(), keep_days=self.keep.value(),
                      autostart=self.auto.isChecked())
        return self.s
