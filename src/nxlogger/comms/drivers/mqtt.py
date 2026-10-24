"""
mqtt.py - MQTT connector (paho-mqtt; its network thread only stores / queues).

json mode        every number tag subscribes its topic (wildcards allowed). The payload is
                 a number ("21.5"), or JSON with the value at the tag's path
                 ("data.temp", "values[2]", "a.b[0].c"). The latest value of every tag
                 is sampled each scan interval, like a recorder scanning its inputs.
wavepacket mode  the device subscribes one topic of ESP32 waveform packets; each packet is
                 queued as it is ("wave") and decoded on the GUI timer.

Writing a RW tag publishes the value (as text) to the tag's topic.
"""
from __future__ import annotations

import json
import re
import ssl
import threading
import time
import uuid

import paho.mqtt.client as mqtt

from nxlogger.comms.drivers.base import Driver

PAHO_V2 = hasattr(mqtt, "CallbackAPIVersion")
TRANSPORTS = {"TCP": ("tcp", False), "TLS/SSL": ("tcp", True),
              "WebSocket": ("websockets", False), "Secure WebSocket": ("websockets", True)}
_TOKEN = re.compile(r"[^.\[\]]+|\[\d+\]")


def json_value(payload: bytes, path: str) -> float | None:
    """Number at `path` in a JSON payload ("" = the payload itself). None if missing."""
    try:
        text = payload.decode("utf-8").strip()
        if not path:
            try:
                return float(text)
            except ValueError:
                obj = json.loads(text)
        else:
            obj = json.loads(text)
        for tok in _TOKEN.findall(path):
            obj = obj[int(tok[1:-1])] if tok.startswith("[") else obj[tok]
        if isinstance(obj, bool):
            return float(obj)
        if isinstance(obj, (int, float)):
            return float(obj)
        if isinstance(obj, str):
            return float(obj)
        if isinstance(obj, dict) and "value" in obj:      # {"value": 1.2, "ts": ...}
            return float(obj["value"])
    except (ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError):
        pass
    return None


class MqttDriver(Driver):
    def __init__(self, dev, out):
        super().__init__(dev, out)
        self.client = None
        self.connected = False
        self.latest: dict[int, float] = {}
        self._lock = threading.Lock()

    def _subs(self) -> list[tuple[str, int]]:
        qos = int(self.dev.settings.get("qos", 0))
        if self.dev.wave:
            return [(self.dev.settings.get("topic", ""), qos)]
        return sorted({(t.address.get("topic", ""), qos) for t in self.number_tags() if t.address.get("topic")})

    def run(self):
        s = self.dev.settings
        transport, tls = TRANSPORTS.get(s.get("transport", "TCP"), ("tcp", False))
        kw = dict(client_id=s.get("client_id") or f"nxlogger-{uuid.uuid4().hex[:8]}",
                  transport=transport, protocol=mqtt.MQTTv311)
        if PAHO_V2:
            kw["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
        c = mqtt.Client(**kw)
        if transport == "websockets":
            c.ws_set_options(path="/mqtt")
        if tls:
            c.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        if s.get("username"):
            c.username_pw_set(s["username"], s.get("password") or None)
        c.reconnect_delay_set(min_delay=1, max_delay=10)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        self.client = c
        host, port = s.get("host", "localhost"), int(s.get("port", 1883))
        self.status("connecting", f"{host}:{port}")
        try:
            c.connect_async(host, port, int(s.get("keepalive", 30)))
            c.loop_start()
        except Exception as e:
            self.status("error", str(e))
            return
        tags = self.number_tags()
        index = [t.index for t in tags]
        period = max(0.05, self.dev.scan_ms / 1000.0)
        next_t = time.monotonic()
        while self.running:
            if not self.dev.wave and self.connected:          # sample the latest values
                t = time.time()
                with self._lock:
                    vals = [self.latest.get(i) for i in index]
                self.scans += 1
                self.put(("scan", self.id, t, period, index, vals))
            self._do_writes()
            next_t = max(next_t + period, time.monotonic())
            if not self.sleep(next_t - time.monotonic()):
                break
        c.loop_stop()
        try:
            c.disconnect()
        except Exception:
            pass

    # ---- paho thread
    def _on_connect(self, client, userdata, flags, rc, props=None):
        ok = (rc == 0) if not hasattr(rc, "is_failure") else not rc.is_failure
        if not ok:
            self.status("error", f"connection refused: {rc}")
            return
        self.connected = True
        subs = self._subs()
        if subs:
            client.subscribe(subs)
        self.status("online", f"{len(subs)} topic(s) subscribed")

    def _on_disconnect(self, client, userdata, *args):
        self.connected = False
        if self.running:
            self.status("connecting", "connection lost - reconnecting")

    def _on_message(self, client, userdata, msg):
        rx = time.time()
        if self.dev.wave:
            self.put(("wave", self.id, rx, bytes(msg.payload)))
            return
        for t in self.number_tags():
            sub = t.address.get("topic", "")
            if sub == msg.topic or (("+" in sub or "#" in sub) and mqtt.topic_matches_sub(sub, msg.topic)):
                v = json_value(msg.payload, t.address.get("path", ""))
                with self._lock:
                    self.latest[t.index] = v
                self.last_ok = rx

    # ---- writing / publishing
    def _do_writes(self):
        while not self._writes.empty():
            idx, value = self._writes.get_nowait()
            tag = self.dev.tag(idx)
            if tag is None or tag.access != "RW" or self.dev.wave:
                self.put(("write", self.id, False, f"{tag.name if tag else idx}: read-only"))
                continue
            ok = self.publish(tag.address.get("topic", ""), f"{value:g}".encode(),
                              int(self.dev.settings.get("qos", 0)), False)
            self.put(("write", self.id, ok, f"{tag.name} ← {value:g}" if ok else f"{tag.name}: not connected"))

    def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> bool:
        if not (self.client and self.connected and topic):
            return False
        return self.client.publish(topic, payload, qos, retain).rc == mqtt.MQTT_ERR_SUCCESS
