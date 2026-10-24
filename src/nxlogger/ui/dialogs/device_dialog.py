"""Add / edit a device: connector, name, scan interval, connection settings (schema form)."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
                               QPlainTextEdit, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from nxlogger.core.tags import DRIVERS, PLANNED, SCAN_CHOICES, DeviceCfg, defaults
from nxlogger.ui.dialogs.common import SchemaForm
from nxlogger.ui.theme.ui_kit import label, set_prop


class DeviceDialog(QDialog):
    def __init__(self, parent, dev: DeviceCfg, taken: set[str], new: bool = False):
        super().__init__(parent)
        self.dev, self.taken = dev, taken
        self.setWindowTitle("Add device" if new else f"Device - {dev.name}")
        self.setMinimumWidth(480)
        f = QFormLayout(); f.setSpacing(8)
        self.driver = QComboBox()
        for k, d in DRIVERS.items():
            self.driver.addItem(d["label"], k)
        self.driver.setCurrentIndex(max(0, self.driver.findData(dev.driver)))
        self.driver.setEnabled(new or not dev.tags)        # the tags' addresses depend on it
        self.driver.setToolTip("" if self.driver.isEnabled() else "Delete the tags to change the connector")
        self.name = QLineEdit(dev.name)
        self.scan = QSpinBox(minimum=20, maximum=3_600_000, singleStep=100, suffix=" ms")
        self.scan.setValue(int(dev.scan_ms))
        self.scan.setToolTip("How often number tags are read (" +
                             ", ".join(f"{x} ms" for x in SCAN_CHOICES[:5]) + " ...)")
        self.enabled = QCheckBox("Enabled (connect when communication starts)", checked=dev.enabled)
        self.desc = QPlainTextEdit(dev.description); self.desc.setMaximumHeight(54)
        f.addRow("Connector", self.driver)
        f.addRow("Name", self.name)
        f.addRow("Scan interval", self.scan)
        f.addRow("Description", self.desc)
        f.addRow("", self.enabled)
        self.note = label("", muted=True, small=True); self.note.setWordWrap(True)
        self.settings_box = QVBoxLayout()
        self.err = label("", small=True); self.err.setObjectName("statusMsg"); set_prop(self.err, "level", "bad")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        set_prop(bb.button(QDialogButtonBox.StandardButton.Ok), "primary", True)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        gen = QWidget(); gen.setLayout(f)
        con = QWidget(); cv = QVBoxLayout(con); cv.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self.note); cv.addLayout(self.settings_box)
        tabs = QTabWidget(); tabs.addTab(gen, "Device"); tabs.addTab(con, "Connection")
        v = QVBoxLayout(self)
        v.addWidget(tabs)
        v.addStretch(1)
        v.addWidget(self.err); v.addWidget(bb)
        self.form: SchemaForm | None = None
        self.driver.currentIndexChanged.connect(self._driver_changed)
        self._driver_changed()

    def _driver_changed(self):
        k = self.driver.currentData()
        if self.form is not None:
            self.form.deleteLater()
        vals = self.dev.settings if k == self.dev.driver else defaults(DRIVERS[k]["settings"])
        self.form = SchemaForm(DRIVERS[k]["settings"], vals)
        self.settings_box.addWidget(self.form)
        self.note.setText(DRIVERS[k]["note"])
        if not self.name.text().strip() or self.name.text() in {d["label"].split(" (")[0] for d in DRIVERS.values()}:
            self.name.setText(DRIVERS[k]["label"].split(" (")[0])
        if k == "mqtt":
            mode = self.form.w["mode"][1]
            mode.currentIndexChanged.connect(self._mqtt_mode)
            self._mqtt_mode()
        if k in PLANNED:
            self.err.setText(f"{DRIVERS[k]['label']}: can be configured, but cannot connect yet.")
        else:
            self.err.setText("")

    def _mqtt_mode(self):
        wave = self.form.values().get("mode") == "wavepacket"
        self.form.set_visible("topic", wave)
        self.form.set_visible("qos", True)
        self.scan.setEnabled(not wave)

    def _ok(self):
        name = self.name.text().strip()
        if not name:
            self.err.setText("Enter a name."); return
        if name in self.taken:
            self.err.setText("Another device already has this name."); return
        s = self.form.values()
        if self.driver.currentData() == "mqtt" and s.get("mode") == "wavepacket" and not s.get("topic"):
            self.err.setText("Enter the waveform topic."); return
        self.accept()

    def apply(self, dev: DeviceCfg) -> DeviceCfg:
        dev.driver = self.driver.currentData()
        dev.name = self.name.text().strip()
        dev.scan_ms = self.scan.value()
        dev.enabled = self.enabled.isChecked()
        dev.description = self.desc.toPlainText().strip()
        dev.settings = {**defaults(DRIVERS[dev.driver]["settings"]), **self.form.values()}
        return dev
