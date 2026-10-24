"""Add / edit an MQTT topic (one device) in the project."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QLineEdit, QSpinBox)

from nxlogger.core import wavepacket as wp
from nxlogger.core.project import ChannelCfg, TopicCfg, new_channels
from nxlogger.ui.theme.ui_kit import label, set_prop


class TopicDialog(QDialog):
    def __init__(self, parent, cfg: TopicCfg | None, taken: set[str]):
        super().__init__(parent)
        self.setWindowTitle("Edit topic" if cfg else "Add topic")
        self.setMinimumWidth(460)
        self.taken = taken
        self.name = QLineEdit(cfg.name if cfg else "")
        self.name.setPlaceholderText("e.g. Engine bay DAQ")
        self.topic = QLineEdit(cfg.topic if cfg else "spexth/daq/")
        self.topic.setPlaceholderText("e.g. spexth/daq/node1/wave   (+ and # allowed)")
        self.qos = QComboBox(); self.qos.addItems(["0", "1", "2"])
        self.qos.setCurrentText(str(cfg.qos if cfg else 0))
        self.nch = QSpinBox(minimum=1, maximum=wp.MAX_CH,
                            value=len(cfg.channels) if cfg and cfg.channels else 4)
        self.nch.setToolTip("Channels expected in the packets. New channels that arrive "
                            "are added automatically.")
        self.enabled = QCheckBox("Enabled (subscribe and log)", checked=cfg.enabled if cfg else True)
        self.err = label("", small=True)
        self.err.setObjectName("statusMsg"); self.err.setProperty("level", "bad")
        f = QFormLayout(self)
        f.addRow("Name", self.name)
        f.addRow("MQTT topic", self.topic)
        f.addRow("QoS", self.qos)
        f.addRow("Channels", self.nch)
        f.addRow("", self.enabled)
        f.addRow(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        set_prop(bb.button(QDialogButtonBox.StandardButton.Ok), "primary", True)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _ok(self):
        t = self.topic.text().strip()
        if not t or t.endswith("/") or " " in t:
            set_prop(self.topic, "invalid", True)
            self.err.setText("Enter a valid MQTT topic (no spaces, not ending with /).")
            return
        if t in self.taken:
            set_prop(self.topic, "invalid", True)
            self.err.setText("This topic is already in the project.")
            return
        self.accept()

    def apply(self, cfg: TopicCfg | None) -> TopicCfg:
        t = self.topic.text().strip()
        name = self.name.text().strip() or t.rsplit("/", 2)[-2 if t.count("/") else -1]
        n = self.nch.value()
        if cfg is None:
            cfg = TopicCfg(name, t, channels=new_channels(n))
        cfg.name, cfg.topic, cfg.qos = name, t, int(self.qos.currentText())
        cfg.enabled = self.enabled.isChecked()
        have = {c.index for c in cfg.channels}
        cfg.channels += [ChannelCfg(i, f"CH{i + 1}") for i in range(n) if i not in have]
        cfg.channels = sorted([c for c in cfg.channels if c.index < n], key=lambda c: c.index)
        return cfg
