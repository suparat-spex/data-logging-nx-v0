"""
test-mqtt.py - command-line check that an MQTT broker is reachable.

Connects, subscribes to a test topic, publishes one message to it and waits for
it to come back (round trip). No GUI needed.

Run:  python test-mqtt.py
      python test-mqtt.py --host broker.emqx.io --transport TLS/SSL
      python test-mqtt.py --host 192.168.1.10 -u user -P pass
Exit code 0 = OK, 1 = failed.
"""
from __future__ import annotations

import argparse
import ssl
import sys
import threading
import time
import uuid

import paho.mqtt.client as mqtt

PAHO_V2 = hasattr(mqtt, "CallbackAPIVersion")   # works with paho-mqtt 1.x and 2.x

TRANSPORTS = {
    # label: (paho transport, use TLS, default port)  - same as mqtt_qt.py
    "TCP": ("tcp", False, 1883),
    "TLS/SSL": ("tcp", True, 8883),
    "WebSocket": ("websockets", False, 8083),
    "Secure WebSocket": ("websockets", True, 8084),
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Test connection to an MQTT broker")
    ap.add_argument("--host", default="broker.emqx.io")
    ap.add_argument("--port", type=int, help="default depends on transport")
    ap.add_argument("--transport", choices=TRANSPORTS, default="TCP")
    ap.add_argument("-u", "--username", default="")
    ap.add_argument("-P", "--password", default="")
    ap.add_argument("--ca-file", default="", help="CA cert for TLS (empty = system CAs)")
    ap.add_argument("--insecure", action="store_true", help="skip TLS cert verification")
    ap.add_argument("--ws-path", default="/mqtt")
    ap.add_argument("--topic", default=f"spexth/daq/test/{uuid.uuid4().hex[:8]}")
    ap.add_argument("--qos", type=int, choices=(0, 1, 2), default=1)
    ap.add_argument("--timeout", type=float, default=10.0, help="seconds per step")
    a = ap.parse_args()

    ptransport, use_tls, default_port = TRANSPORTS[a.transport]
    port = a.port or default_port
    connected, received = threading.Event(), threading.Event()
    result: dict = {}

    kw = dict(client_id=f"test-mqtt-{uuid.uuid4().hex[:8]}",
              transport=ptransport, protocol=mqtt.MQTTv311)
    if PAHO_V2:
        kw["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
    c = mqtt.Client(**kw)
    if ptransport == "websockets":
        c.ws_set_options(path=a.ws_path)
    if use_tls:
        c.tls_set(ca_certs=a.ca_file or None,
                  cert_reqs=ssl.CERT_NONE if a.insecure else ssl.CERT_REQUIRED)
        if a.insecure:
            c.tls_insecure_set(True)
    if a.username:
        c.username_pw_set(a.username, a.password or None)

    # paho 1.x passes rc as an int and has no `properties` arg; 2.x passes ReasonCode
    def on_connect(client, userdata, flags, reason_code, properties=None):
        ok = not reason_code.is_failure if PAHO_V2 else reason_code == 0
        result["ok"] = ok
        result["rc"] = reason_code if PAHO_V2 else mqtt.connack_string(reason_code)
        if ok:
            client.subscribe(a.topic, a.qos)
        connected.set()

    def on_subscribe(client, userdata, mid, reason_codes, properties=None):
        result["sub_rc"] = reason_codes
        result["t_pub"] = time.perf_counter()
        client.publish(a.topic, b"hello from test-mqtt.py", qos=a.qos)

    def on_message(client, userdata, msg):
        result["t_rx"] = time.perf_counter()
        result["payload"] = msg.payload
        received.set()

    c.on_connect, c.on_subscribe, c.on_message = on_connect, on_subscribe, on_message

    print(f"Broker    : {a.host}:{port} ({a.transport})")
    print(f"Topic     : {a.topic}")
    t0 = time.perf_counter()
    try:
        c.connect(a.host, port, keepalive=30)
    except Exception as e:  # DNS, refused, TLS handshake...
        print(f"FAIL      : cannot connect - {e}")
        return 1
    c.loop_start()
    try:
        if not connected.wait(a.timeout):
            print("FAIL      : no CONNACK from broker (timeout)")
            return 1
        if not result["ok"]:
            print(f"FAIL      : connection refused - {result['rc']}")
            return 1
        print(f"Connected : OK in {(time.perf_counter() - t0) * 1000:.0f} ms")

        if not received.wait(a.timeout):
            sub = result.get("sub_rc")
            why = f"subscribe result {sub}" if sub else "no SUBACK"
            print(f"FAIL      : published message not received ({why})")
            return 1
        rtt = (result["t_rx"] - result["t_pub"]) * 1000
        print(f"Round trip: OK in {rtt:.0f} ms, payload={result['payload']!r}")
        print("PASS")
        return 0
    finally:
        c.disconnect()
        c.loop_stop()


if __name__ == "__main__":
    sys.exit(main())
