"""
mqtt_client.py - paho-mqtt wrapped for Qt.

paho runs its own network thread (loop_start). Status changes are sent to the
GUI through Qt signals (queued across threads). For high-rate data the
subscriber can set `client.on_payload`, which is called directly in the
network thread so decoding never waits for the GUI.
"""
from __future__ import annotations

import socket
import ssl
import threading
import uuid
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal
import paho.mqtt.client as mqtt


PAHO_V2 = hasattr(mqtt, "CallbackAPIVersion")   # support paho-mqtt 1.x and 2.x

TRANSPORTS = {
    # label: (paho transport, use TLS, default port)
    "TCP": ("tcp", False, 1883),
    "TLS/SSL": ("tcp", True, 8883),
    "WebSocket": ("websockets", False, 8083),
    "Secure WebSocket": ("websockets", True, 8084),
}


class MqttClient(QObject):
    connected = Signal()
    disconnected = Signal(str)
    status = Signal(str)            # human-readable log lines
    message = Signal(str, bytes)    # topic, payload (only if no on_payload hook)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client: Optional[mqtt.Client] = None
        self._subs: list[tuple[str, int]] = []
        self.on_payload: Optional[Callable[[str, bytes], None]] = None
        self.is_connected = False

    # ---------------------------------------------------------------- public
    def connect_to(self, host: str, port: int, transport: str = "TCP",
                   client_id: str = "", username: str = "", password: str = "",
                   ca_file: str = "", insecure: bool = False, ws_path: str = "/mqtt",
                   keepalive: int = 30):
        self.disconnect_from()
        ptransport, use_tls, _ = TRANSPORTS[transport]
        kw = dict(client_id=client_id or f"qtdaq-{uuid.uuid4().hex[:8]}",
                  transport=ptransport, protocol=mqtt.MQTTv311)
        if PAHO_V2:
            kw["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
        c = mqtt.Client(**kw)
        if ptransport == "websockets":
            c.ws_set_options(path=ws_path or "/mqtt")
        if use_tls:
            c.tls_set(ca_certs=ca_file or None,
                      cert_reqs=ssl.CERT_NONE if insecure else ssl.CERT_REQUIRED)
            if insecure:
                c.tls_insecure_set(True)
        if username:
            c.username_pw_set(username, password or None)
        c.reconnect_delay_set(min_delay=1, max_delay=10)
        c.max_queued_messages_set(200)        # don't grow without bound if link is slow
        c.on_connect = self._on_connect
        c.on_connect_fail = self._on_connect_fail
        c.on_disconnect = self._on_disconnect if PAHO_V2 else self._on_disconnect_v1
        c.on_message = self._on_message
        self._client = c
        self._fails = 0
        self._target = (host, port)
        self.status.emit(f"Connecting to {host}:{port} ({transport})...")
        # paho never reports *why* a background connect fails, so probe the
        # socket ourselves (off the GUI thread) and log the real reason.
        threading.Thread(target=self._probe, args=(host, port), daemon=True).start()
        try:
            c.connect_async(host, port, keepalive)
            c.loop_start()
        except Exception as e:  # DNS errors etc.
            self.status.emit(f"Connect error: {e}")
            self._client = None
            self.disconnected.emit(str(e))

    def disconnect_from(self):
        c, self._client = self._client, None
        if c is not None:
            try:
                c.disconnect()
                c.loop_stop()
            except Exception:
                pass
        if self.is_connected:
            self.is_connected = False
            self.disconnected.emit("user")

    def subscribe(self, topic: str, qos: int = 0):
        self._subs = [(topic, qos)]
        if self._client and self.is_connected:
            self._client.subscribe(topic, qos)
            self.status.emit(f"Subscribed to '{topic}' (QoS {qos})")

    def set_subscriptions(self, subs: list[tuple[str, int]]):
        """Subscribe to several topics at once (replaces the current set).
        Kept across auto-reconnects, like subscribe()."""
        subs = list(dict(subs).items())            # de-duplicate, last QoS wins
        if self._client and self.is_connected:
            new = dict(subs)
            for t, _ in self._subs:
                if t not in new:
                    self._client.unsubscribe(t)
                    self.status.emit(f"Unsubscribed from '{t}'")
            old = dict(self._subs)
            for t, q in subs:
                if old.get(t) != q:
                    self._client.subscribe(t, q)
                    self.status.emit(f"Subscribed to '{t}' (QoS {q})")
        self._subs = subs

    def unsubscribe_all(self):
        if self._client and self.is_connected:
            for t, _ in self._subs:
                self._client.unsubscribe(t)
        self._subs = []

    def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> bool:
        if not (self._client and self.is_connected):
            return False
        info = self._client.publish(topic, payload, qos=qos, retain=retain)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    # ------------------------------------------------------ diagnostics
    def _probe(self, host: str, port: int):
        """Plain TCP connect test, so the Log can say timeout vs refused vs DNS."""
        try:
            socket.getaddrinfo(host, port)
        except socket.gaierror as e:
            self.status.emit(f"Cannot resolve '{host}' (DNS): {e.strerror or e}. "
                             "Check the broker name and your internet connection.")
            return
        try:
            with socket.create_connection((host, port), timeout=6):
                pass
        except socket.timeout:
            self.status.emit(f"No reply from {host}:{port} after 6 s - the port is "
                             "probably blocked by your network/firewall. Try "
                             "WebSocket (8083) / Secure WebSocket (8084) or another network.")
        except ConnectionRefusedError:
            self.status.emit(f"{host}:{port} refused the connection - wrong port, "
                             "or no broker listening there.")
        except OSError as e:
            self.status.emit(f"Network error to {host}:{port}: {e.strerror or e}")

    def _on_connect_fail(self, client, userdata):
        if client is not self._client:
            return
        self._fails += 1
        if self._fails <= 3 or self._fails % 10 == 0:   # don't flood the Log
            h, p = self._target
            self.status.emit(f"Connect attempt {self._fails} to {h}:{p} failed - retrying...")

    # ------------------------------------------------------ paho callbacks
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        failed = reason_code.is_failure if PAHO_V2 else reason_code != 0
        if failed:
            msg = reason_code if PAHO_V2 else mqtt.connack_string(reason_code)
            self.status.emit(f"Connection refused: {msg}")
            return
        self.is_connected = True
        self.status.emit("Connected")
        for t, q in self._subs:          # re-subscribe after auto-reconnect
            client.subscribe(t, q)
            self.status.emit(f"Subscribed to '{t}' (QoS {q})")
        self.connected.emit()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if client is not self._client:   # old client after user disconnect
            return
        was = self.is_connected
        self.is_connected = False
        if was:
            self.status.emit(f"Disconnected ({reason_code}) - retrying...")
            self.disconnected.emit(str(reason_code))

    def _on_disconnect_v1(self, client, userdata, rc):   # paho 1.x signature
        self._on_disconnect(client, userdata, None, mqtt.error_string(rc), None)

    def _on_message(self, client, userdata, msg):
        if self.on_payload is not None:
            self.on_payload(msg.topic, msg.payload)
        else:
            self.message.emit(msg.topic, bytes(msg.payload))
