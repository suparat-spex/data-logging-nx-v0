"""Add / edit an alarm notification profile (log, popup, REST, MQTT, e-mail)."""
from __future__ import annotations

import copy

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QStackedWidget,
                               QVBoxLayout, QWidget)

from nxlogger.core.alarm_rules import LEVELS, PROFILE_KINDS, normalize_profile
from nxlogger.ui.dialogs.common import spin
from nxlogger.ui.icons import level_icon
from nxlogger.ui.theme.ui_kit import label, set_prop


class AlarmProfileDialog(QDialog):
    """Add / edit a notification profile (where alarm events are sent)."""

    def __init__(self, parent, profile: dict):
        super().__init__(parent)
        self.p = copy.deepcopy(normalize_profile(profile))
        self.setWindowTitle("Notification profile")
        self.setMinimumWidth(520)
        v = QVBoxLayout(self)
        f = QFormLayout(); f.setSpacing(8)
        self.name = QLineEdit(self.p["name"])
        self.enabled = QCheckBox("Enabled", checked=self.p["enabled"])
        r = QHBoxLayout(); r.addWidget(self.name, 1); r.addWidget(self.enabled)
        f.addRow("Name", r)
        self.kind = QComboBox()
        for k, text in PROFILE_KINDS.items():
            self.kind.addItem(text, k)
        self.kind.setCurrentIndex(max(self.kind.findData(self.p["kind"]), 0))
        f.addRow("Type", self.kind)
        lr = QHBoxLayout()
        self.lv = []
        for i in range(LEVELS):
            c = QCheckBox(f"L{i + 1}", checked=(i + 1) in self.p["levels"]); c.setIcon(level_icon(i + 1))
            self.lv.append(c); lr.addWidget(c)
        lr.addStretch(1)
        f.addRow("Levels", lr)
        er = QHBoxLayout()
        self.ev = {k: QCheckBox({"on": "Alarm ON", "off": "Alarm OFF (cleared)"}[k],
                                checked=bool(self.p[k])) for k in ("on", "off")}
        for c in self.ev.values():
            er.addWidget(c)
        er.addStretch(1)
        f.addRow("Events", er)
        v.addLayout(f)

        self.stack = QStackedWidget()
        pages = {}
        for k in PROFILE_KINDS:
            w = QWidget(); pf = QFormLayout(w); pf.setContentsMargins(0, 6, 0, 0); pf.setSpacing(8)
            pages[k] = pf
            self.stack.addWidget(w)
        pages["log"].addRow(label("Events are written to the alarm log and the Log panel only.",
                                  muted=True, small=True))
        pages["popup"].addRow(label("Shows a popup message and plays the system sound for each event.",
                                    muted=True, small=True))
        self.url = QLineEdit(self.p["url"]); self.url.setPlaceholderText("https://example.com/api/alarms")
        self.method = QComboBox(); self.method.addItems(["POST", "PUT"])
        self.method.setCurrentText(self.p.get("method", "POST"))
        self.headers = QPlainTextEdit(self.p.get("headers", ""))
        self.headers.setPlaceholderText("Authorization: Bearer <token>\nX-Site: plant-1")
        self.headers.setMaximumHeight(64)
        self.timeout = spin(self.p.get("timeout_s", 5), 0.5, 60, 1); self.timeout.setSuffix(" s")
        for text, w in (("URL", self.url), ("Method", self.method), ("Headers", self.headers),
                        ("Timeout", self.timeout)):
            pages["rest"].addRow(text, w)
        pages["rest"].addRow(label("Body: JSON {event, level, type, alarm, tag, value, setpoint, unit, "
                                   "time, project}. Sent in the background - a slow server never "
                                   "stops acquisition.", muted=True, small=True))
        self.topic = QLineEdit(self.p.get("topic", "")); self.topic.setPlaceholderText("spexth/daq/alarms")
        self.qos = QComboBox(); self.qos.addItems(["0", "1", "2"]); self.qos.setCurrentText(str(self.p.get("qos", 0)))
        self.retain = QCheckBox("Retain", checked=bool(self.p.get("retain")))
        pages["mqtt"].addRow("Topic", self.topic)
        pages["mqtt"].addRow("QoS", self.qos)
        pages["mqtt"].addRow("", self.retain)
        pages["mqtt"].addRow(label("Published on the connected broker with the same JSON body.",
                                   muted=True, small=True))
        self.to = QLineEdit(self.p.get("to", "")); self.to.setPlaceholderText("operator@example.com")
        pages["email"].addRow("To", self.to)
        pages["email"].addRow(label("E-mail delivery (SMTP server, TLS) is planned; the profile can be "
                                    "prepared now.", muted=True, small=True))
        for pf in pages.values():
            for i in range(pf.rowCount()):
                it = pf.itemAt(i, QFormLayout.ItemRole.SpanningRole)
                if it and isinstance(it.widget(), QLabel):
                    it.widget().setWordWrap(True)
        v.addWidget(self.stack)
        self.kind.currentIndexChanged.connect(self._kind)
        self._kind()

        self.err = QLabel(); self.err.setObjectName("statusMsg")
        v.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        set_prop(bb.button(QDialogButtonBox.StandardButton.Ok), "primary", True)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _kind(self):
        self.stack.setCurrentIndex(self.kind.currentIndex())
        if self.name.text().strip() in PROFILE_KINDS.values() or not self.name.text().strip():
            self.name.setText(self.kind.currentText())

    def _ok(self):
        k = self.kind.currentData()
        err = ""
        if k == "rest" and not self.url.text().strip().lower().startswith(("http://", "https://")):
            err = "Enter a URL starting with http:// or https://"
        elif k == "mqtt" and (not self.topic.text().strip() or any(c in self.topic.text() for c in "+# ")):
            err = "Enter an MQTT topic to publish to (no spaces, no + or #)."
        elif not any(c.isChecked() for c in self.lv):
            err = "Choose at least one level."
        if err:
            set_prop(self.err, "level", "bad"); self.err.setText(err)
            return
        self.accept()

    def result_profile(self) -> dict:
        p = self.p
        p.update(name=self.name.text().strip() or self.kind.currentText(), enabled=self.enabled.isChecked(),
                 kind=self.kind.currentData(), levels=[i + 1 for i, c in enumerate(self.lv) if c.isChecked()],
                 url=self.url.text().strip(), method=self.method.currentText(),
                 headers=self.headers.toPlainText().strip(), timeout_s=self.timeout.value(),
                 topic=self.topic.text().strip(), qos=int(self.qos.currentText()),
                 retain=self.retain.isChecked(), to=self.to.text().strip(),
                 **{k: c.isChecked() for k, c in self.ev.items()})
        return p
