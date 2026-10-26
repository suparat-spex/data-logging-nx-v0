"""Device settings pop-up (Connectivity: Add / double-click / Edit) - one page, no tabs:

  header   connector icon, device name (or "New <connector> device"), what the connector is
  body     General (name, description, scan interval, enabled), the connector's own sections
           (device_pages/<connector>.py), Connection test ([Test connection] + result) - one
           label column, scrolls when tall
  footer   "Connects to ..." / a validation error · [Cancel] [Add device | Save]

The connector is chosen before the dialog opens (Add ▾) and is fixed: the tags' addresses
depend on it. Test connection runs the real driver once (comms.drivers.test_connection) in a
worker thread, with the values as typed - nothing is saved or started.
"""
from __future__ import annotations

import copy
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from nxlogger.comms.drivers import test_connection
from nxlogger.core.tags import DRIVERS, PLANNED, SCAN_CHOICES, DeviceCfg, defaults
from nxlogger.ui.dialogs.common import dialog_header
from nxlogger.ui.dialogs.device_pages import ConnectionPage, connection_page
from nxlogger.ui.dialogs.device_pages.base import form_labels, section
from nxlogger.ui.theme.ui_kit import T, label, make_icon, set_prop


WIDTH = 682                                      # px


class _Tester(QObject):
    """Carries the worker thread's result to the GUI thread (queued signal)."""
    done = Signal(bool, str)


class DeviceDialog(QDialog):
    def __init__(self, parent, dev: DeviceCfg, taken: set[str], new: bool = False, locked: str = ""):
        super().__init__(parent)
        self.dev, self.taken, self.new = dev, taken, new
        self.kind = dev.driver                       # fixed: chosen from Add ▾
        info = DRIVERS[self.kind]
        self.setWindowTitle("Add device" if new else f"Device - {dev.name}")
        self.setMinimumWidth(WIDTH)

        # ---- header
        head, self.icon, self.title, self.subtitle = dialog_header(info["icon"], "", f"{info['label']} · {info['note']}")

        # ---- body: General + the connector page
        self.body = QWidget(); self.body.setObjectName("dlgBody")
        self.bv = QVBoxLayout(self.body); self.bv.setContentsMargins(20, 6, 20, 16); self.bv.setSpacing(4)
        self.general = g = section(self.bv, "General")
        self.name = QLineEdit(dev.name)
        self.desc = QLineEdit(dev.description); self.desc.setPlaceholderText("optional - location, purpose ...")
        self.scan = QSpinBox(minimum=20, maximum=3_600_000, singleStep=100, suffix=" ms")
        self.scan.setValue(int(dev.scan_ms))
        self.scan.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.scan.setToolTip("How often number tags are read (" +
                             ", ".join(f"{x} ms" for x in SCAN_CHOICES[:5]) + " ...)")
        self.enabled = QCheckBox("Connect when communication starts", checked=dev.enabled)
        for text, w in (("Name", self.name), ("Description", self.desc),
                        ("Scan interval", self.scan), ("Enabled", self.enabled)):
            g.addRow(text, w)
        self.form: ConnectionPage = connection_page(self.kind, dev.settings)
        self.bv.addWidget(self.form)
        # Connection test: under the settings it checks, apart from Cancel / Save
        self.test_form = section(self.bv, "Connection test")
        self.btn_test = QPushButton("Test connection")
        self.btn_test.setIcon(make_icon("plug", T["text"]))
        self.btn_test.setToolTip("Connect once with these settings and read the tags - nothing is saved")
        self.btn_test.clicked.connect(self._test)
        self.btn_test.setEnabled(self.kind not in PLANNED)
        self.test_result = label("Not tested yet - connects once with the settings above, nothing is saved.",
                                 muted=True, small=True)
        self.test_result.setObjectName("statusMsg"); self.test_result.setWordWrap(True)
        set_prop(self.test_result, "level", "muted")
        row = QWidget(); rh = QHBoxLayout(row); rh.setContentsMargins(0, 0, 0, 0); rh.setSpacing(10)
        rh.addWidget(self.btn_test); rh.addWidget(self.test_result, 1)
        self.test_form.addRow(QLabel(""), row)          # a real (empty) label: the button lines up with the fields
        self.bv.addStretch(1)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(self.body)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ---- footer
        foot = QFrame(); foot.setObjectName("dlgFooter")
        fh = QHBoxLayout(foot); fh.setContentsMargins(20, 10, 20, 10); fh.setSpacing(8)
        msgs = QVBoxLayout(); msgs.setSpacing(0)
        self.summary = label("", muted=True, small=True); self.summary.setWordWrap(True)
        self.err = label("", small=True); self.err.setObjectName("statusMsg"); self.err.setWordWrap(True)
        set_prop(self.err, "level", "bad"); self.err.hide()
        msgs.addWidget(self.summary); msgs.addWidget(self.err)
        fh.addLayout(msgs, 1)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        self.ok = QPushButton("Add device" if new else "Save")       # the verb, never a bare OK
        set_prop(self.ok, "primary", True); self.ok.setDefault(True)
        self.ok.clicked.connect(self._ok)
        fh.addWidget(cancel); fh.addWidget(self.ok)

        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(head); v.addWidget(scroll, 1); v.addWidget(foot)

        self._tester = _Tester(self)
        self._tester.done.connect(self._tested)
        self._testing = False
        self.form.changed.connect(self._page_changed)
        self.name.textChanged.connect(lambda _: self._title())
        self._align(); self._title(); self._page_changed(); self._fit()
        if self.kind in PLANNED:
            self._message(f"{info['label']} can be configured, but cannot connect yet.", "warn")
        self.locked = locked
        if locked:                               # recording: look, don't change
            self.ok.setEnabled(False)
            self._message(locked)

    # ---- layout
    def _align(self):
        """General and every connector section share one label column."""
        labels = form_labels([self.general, self.test_form]) + self.form.labels()
        width = max(lb.sizeHint().width() for lb in labels)
        for lb in labels:
            lb.setMinimumWidth(width)

    def _title(self):
        name = self.name.text().strip()
        label_ = DRIVERS[self.kind]["label"]
        self.title.setText(f"New {label_} device" if self.new and not name else (name or label_))

    def _page_changed(self):
        vals = self.form.values()
        self.scan.setEnabled(vals.get("mode") != "wavepacket")      # wavepacket MQTT streams on its own
        text = self.form.summary(vals)
        self.summary.setText(f"Connects to  {text}" if text else "")
        if not self._testing and self.test_result.property("level") in ("ok", "bad"):
            self._result("Settings changed - test again.", "muted")   # the old result no longer applies

    def _fit(self):
        """Tall enough for the page (no scrolling for the usual connectors), at most 85 % of the screen."""
        scr = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        want = self.body.sizeHint().height() + 170
        self.resize(max(self.width(), WIDTH), min(want, int(scr.height() * 0.85)))

    # ---- Test connection
    def _test(self):
        msg = self.form.validate(self.form.values())
        if msg:
            self._result("✗ " + msg, "bad"); return
        trial = self.apply(copy.deepcopy(self.dev))  # the values as typed; the project is not touched
        self._testing = True
        self.btn_test.setEnabled(False); self.btn_test.setText("Testing...")
        self._result(f"Testing {trial.topic} ...", "info")

        def run():
            try:
                ok, text = test_connection(trial)
            except Exception as e:                   # a driver bug must not kill the dialog
                ok, text = False, str(e) or type(e).__name__
            try:
                self._tester.done.emit(ok, text)
            except RuntimeError:                     # the dialog is gone
                pass
        threading.Thread(target=run, name="device-test", daemon=True).start()

    def _tested(self, ok: bool, text: str):
        self._testing = False
        self.btn_test.setEnabled(True); self.btn_test.setText("Test connection")
        self._result(("✓ " if ok else "✗ ") + text[:300], "ok" if ok else "bad")

    def _result(self, text: str, level: str):
        set_prop(self.test_result, "level", level)
        self.test_result.setText(text)

    # ---- messages / OK
    def _message(self, text: str, level: str = "bad"):
        set_prop(self.err, "level", level)
        self.err.setText(text); self.err.setVisible(bool(text))

    def _ok(self):
        name = self.name.text().strip()
        if not name:
            self._message("Enter a name."); self.name.setFocus(); return
        if name in self.taken:
            self._message("Another device already has this name."); self.name.setFocus(); return
        msg = self.form.validate(self.form.values())      # the connector page checks its own fields
        if msg:
            self._message(msg); return
        self.accept()

    def apply(self, dev: DeviceCfg) -> DeviceCfg:
        dev.driver = self.kind
        dev.name = self.name.text().strip() or self.dev.name
        dev.scan_ms = self.scan.value()
        dev.enabled = self.enabled.isChecked()
        dev.description = self.desc.text().strip()
        dev.settings = {**defaults(DRIVERS[dev.driver]["settings"]), **self.form.values()}
        return dev
