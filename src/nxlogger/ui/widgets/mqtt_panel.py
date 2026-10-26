"""Broker settings form (MqttPanel) and its themed variant used in the main window.

Defaults come from config.ini [mqtt]; what the user last entered (QSettings) wins."""
from __future__ import annotations

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QWidget)

from nxlogger import config
from nxlogger.comms.mqtt_client import MqttClient, TRANSPORTS
from nxlogger.ui.theme.ui_kit import fixed_width, set_prop


class MqttPanel(QGroupBox):
    """Broker settings + Connect/Disconnect button. Settings are remembered."""

    def __init__(self, client: MqttClient, settings_key: str,
                 default_topic: str, show_retain: bool = False, parent=None):
        super().__init__("MQTT", parent)
        self.client = client
        self._key = settings_key
        s = QSettings()
        d = config.get()

        self.host = QComboBox(editable=True)
        self.host.addItems(["broker.emqx.io", "test.mosquitto.org", "localhost"])
        self.host.setCurrentText(s.value(f"{self._key}/host", d.mqtt.get("host") or "broker.emqx.io"))
        self.transport = QComboBox()
        self.transport.addItems(TRANSPORTS.keys())
        self.port = QSpinBox(maximum=65535)
        self.ws_path = QLineEdit(s.value(f"{self._key}/ws_path", d.mqtt.get("ws_path") or "/mqtt"))
        self.client_id = QLineEdit(s.value(f"{self._key}/client_id", d.mqtt.get("client_id", "")))
        self.client_id.setPlaceholderText("auto (random)")
        self.user = QLineEdit(s.value(f"{self._key}/user", d.mqtt.get("username", "")))
        self.pwd = QLineEdit(d.mqtt.get("password", ""), echoMode=QLineEdit.Password)
        self.ca_file = QLineEdit(s.value(f"{self._key}/ca", ""))
        self.ca_file.setPlaceholderText("system CAs (optional .crt)")
        ca_btn = QPushButton("...", maximumWidth=32, clicked=self._browse_ca)
        self.insecure = QCheckBox("Skip certificate check")
        self.keepalive = QSpinBox(minimum=5, maximum=600, value=d.mqtt_int("keepalive", 30), suffix=" s")
        self.topic = QLineEdit(s.value(f"{self._key}/topic", default_topic))
        self.qos = QComboBox()
        self.qos.addItems(["0", "1", "2"])
        self.qos.setCurrentText(str(s.value(f"{self._key}/qos", "0")))
        self.retain = QCheckBox("Retain")
        self.btn = QPushButton("Connect", clicked=self._toggle)
        self.btn.setMinimumHeight(30)
        fixed_width(self.btn, "Connect", "Disconnect")
        self.led = QLabel()
        self.state = QLabel("Disconnected")
        self._set_led("#9aa0a6")

        self.transport.currentTextChanged.connect(self._transport_changed)
        self.transport.setCurrentText(s.value(f"{self._key}/transport", d.mqtt.get("transport") or "TCP"))
        self._transport_changed(self.transport.currentText())
        self.port.setValue(int(s.value(f"{self._key}/port", d.mqtt_int("port", self.port.value()))))

        f = QFormLayout(self)
        f.addRow("Broker", self.host)
        row = QHBoxLayout(); row.addWidget(self.transport, 2); row.addWidget(self.port, 1)
        f.addRow("Transport", _wrap(row))
        f.addRow("WS path", self.ws_path)
        f.addRow("Client ID", self.client_id)
        f.addRow("Username", self.user)
        f.addRow("Password", self.pwd)
        row = QHBoxLayout(); row.addWidget(self.ca_file); row.addWidget(ca_btn)
        f.addRow("CA file", _wrap(row))
        f.addRow("", self.insecure)
        f.addRow("Keepalive", self.keepalive)
        f.addRow("Topic", self.topic)
        row = QHBoxLayout(); row.addWidget(self.qos)
        if show_retain:
            row.addWidget(self.retain)
        row.addStretch()
        f.addRow("QoS", _wrap(row))
        row = QHBoxLayout(); row.addWidget(self.led); row.addWidget(self.state, 1)
        f.addRow(self.btn)
        f.addRow(_wrap(row))

        client.connected.connect(self._on_connected)
        client.disconnected.connect(self._on_disconnected)
        self._want = False

    # ----------------------------------------------------------- helpers
    @property
    def qos_value(self) -> int:
        return int(self.qos.currentText())

    def _set_led(self, color: str):
        self.led.setFixedSize(12, 12)
        self.led.setStyleSheet(f"background:{color}; border-radius:6px;")

    def _browse_ca(self):
        p, _ = QFileDialog.getOpenFileName(self, "CA certificate", "",
                                           "Certificates (*.crt *.pem);;All (*)")
        if p:
            self.ca_file.setText(p)

    def _transport_changed(self, name: str):
        ptransport, tls, port = TRANSPORTS[name]
        self.port.setValue(port)
        self.ws_path.setEnabled(ptransport == "websockets")
        for w in (self.ca_file, self.insecure):
            w.setEnabled(tls)

    def _editable(self, on: bool):
        for w in (self.host, self.transport, self.port, self.ws_path, self.client_id,
                  self.user, self.pwd, self.ca_file, self.insecure, self.keepalive):
            w.setEnabled(on)
        if on:
            self._transport_changed_keep_port()

    def _transport_changed_keep_port(self):
        ptransport, tls, _ = TRANSPORTS[self.transport.currentText()]
        self.ws_path.setEnabled(ptransport == "websockets")
        self.ca_file.setEnabled(tls); self.insecure.setEnabled(tls)

    def _save(self):
        s = QSettings()
        for k, v in {"host": self.host.currentText(), "transport": self.transport.currentText(),
                     "port": self.port.value(), "ws_path": self.ws_path.text(),
                     "client_id": self.client_id.text(), "user": self.user.text(),
                     "ca": self.ca_file.text(), "topic": self.topic.text(),
                     "qos": self.qos.currentText()}.items():
            s.setValue(f"{self._key}/{k}", v)

    def _toggle(self):
        if self._want:
            self._want = False
            self.client.disconnect_from()
            self._on_disconnected("user")
            return
        self._save()
        self._want = True
        self._editable(False)
        self.btn.setText("Disconnect")
        self.state.setText("Connecting...")
        self._set_led("#f2a600")
        self.client.connect_to(
            self.host.currentText().strip(), self.port.value(), self.transport.currentText(),
            self.client_id.text().strip(), self.user.text(), self.pwd.text(),
            self.ca_file.text().strip(), self.insecure.isChecked(),
            self.ws_path.text().strip(), self.keepalive.value())
        self._attempt = getattr(self, "_attempt", 0) + 1
        token = self._attempt
        QTimer.singleShot(10000, lambda: self._watchdog(token))

    def _watchdog(self, token: int):
        """Still not connected 10 s after clicking Connect -> say so plainly."""
        if token == self._attempt and self._want and not self.client.is_connected:
            self.state.setText("Not connected after 10 s - see Log")
            self._set_led("#e5484d")

    def _on_connected(self):
        self.state.setText(f"Connected  {self.host.currentText()}:{self.port.value()}")
        self._set_led("#1e9e5a")

    def _on_disconnected(self, why: str):
        if self._want:                      # dropped, paho is reconnecting
            self.state.setText("Reconnecting...")
            self._set_led("#f2a600")
            return
        self.state.setText("Disconnected")
        self._set_led("#9aa0a6")
        self.btn.setText("Connect")
        self._editable(True)


def _wrap(layout) -> QWidget:
    w = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    w.setLayout(layout)
    return w


class ThemedMqttPanel(MqttPanel):
    """MqttPanel with token colours: the status LED is styled by role, not by hex."""
    LED_LEVEL = {"#9aa0a6": "muted", "#f2a600": "warn", "#e5484d": "bad", "#1e9e5a": "ok"}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.setTitle("")
        set_prop(self.btn, "primary", True)

    def _set_led(self, color: str):
        self.led.setObjectName("led")
        self.led.setFixedSize(12, 12)
        set_prop(self.led, "level", self.LED_LEVEL.get(color, "muted"))
