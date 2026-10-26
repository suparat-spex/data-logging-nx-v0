"""MQTT: subscribe to a broker - number tags (JSON / plain value per topic) or ESP32 waveform
packets (wavepacket mode, array tags)."""
from __future__ import annotations

from nxlogger.core.tags import MQTT_MODES
from nxlogger.ui.dialogs.device_pages.base import ConnectionPage

DEFAULT_PORTS = {"TCP": 1883, "TLS/SSL": 8883, "WebSocket": 8083, "Secure WebSocket": 8084}
SCHEME = {"TCP": "mqtt", "TLS/SSL": "mqtts", "WebSocket": "ws", "Secure WebSocket": "wss"}


class MqttPage(ConnectionPage):
    DRIVER = "mqtt"
    SECTIONS = [("Broker", ["host", "port", "transport"]),
                ("Authentication", ["username", "password", "client_id"]),
                ("Session", ["keepalive", "qos"]),
                ("Payload", ["mode", "topic"])]
    LABELS = {"host": "Address", "mode": "Tags", "qos": "QoS (quality of service)"}
    CHOICES = {"mode": MQTT_MODES, "qos": {"0": "0 - at most once", "1": "1 - at least once",
                                          "2": "2 - exactly once"}}

    def build_extra(self, v):
        self._transport = self.values()["transport"]
        self.w["transport"][1].currentIndexChanged.connect(self._transport_changed)

    def _transport_changed(self):
        """A new transport moves the port to its usual one - unless a custom port was typed."""
        new = self.values()["transport"]
        if self.values()["port"] == DEFAULT_PORTS.get(self._transport):
            self.set_value("port", DEFAULT_PORTS[new])
        self._transport = new

    def on_change(self, values):
        self.set_visible("topic", values["mode"] == "wavepacket")

    def summary(self, values):
        url = f"{SCHEME.get(values['transport'], 'mqtt')}://{values['host']}:{values['port']}"
        who = f" as {values['username']}" if values["username"] else ""
        what = f" · topic {values['topic']}" if values["mode"] == "wavepacket" else " · topic per tag"
        return url + who + what

    def validate(self, values):
        if not values["host"]:
            return "Enter the broker address."
        if values["mode"] == "wavepacket" and not values["topic"]:
            return "Enter the waveform topic."
        return ""
