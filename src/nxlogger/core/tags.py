"""
tags.py - Connectivity model: devices (one connection each) holding tags. No Qt.

Like the Connectivity part of a SCADA (Genesis64, FUXA) and the GA10 "channel" list:

  Device   one connection to a source - Simulation, Modbus TCP, Modbus RTU, MQTT broker,
           OPC UA server (OPC DA planned). Driver settings, scan interval, enabled.
  Group    folder inside a device ("Boiler/Pumps"); only a path on the tag, so tags can be
           moved between groups freely. Empty groups are kept in DeviceCfg.groups.
  Tag      one value. A Number tag is read every scan; an Array tag is one channel of an
           ESP32 waveform packet (MQTT device in wavepacket mode). Tags have every Channel
           property: name, unit, colour, description, scaling, decimals, Y range.

Live data is keyed (device id, tag index), exactly like the old (topic id, channel)
pairs, so alarms, variables, views and logging work on tags unchanged. One scan of a
device becomes a one-sample block of all its number tags (see ScanBlock).

Schemas (SETTINGS / ADDRESS / FIELD types) describe the driver forms; the Properties panel
and the device / tag dialogs build their editors from them.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

import numpy as np

from nxlogger.core import wavepacket as wp


# ====================================================================== schemas
# field: (key, label, type, default, extra)   type: str | int | float | bool | choice | password
#        extra: choices list for "choice", (min, max) for numbers; more: UI hints (button text ...)
def F(key, label, kind, default, extra=None, tip="", **more):
    return {"key": key, "label": label, "type": kind, "default": default, "extra": extra, "tip": tip, **more}


MODBUS_AREAS = {"holding": "Holding register (4x)", "input": "Input register (3x)",
                "coil": "Coil (0x)", "discrete": "Discrete input (1x)"}
MODBUS_TYPES = {"int16": 1, "uint16": 1, "int32": 2, "uint32": 2, "float32": 2,
                "int64": 4, "uint64": 4, "float64": 4, "bool": 1}       # registers per value
WORD_ORDERS = {"ABCD": "ABCD (big endian)", "CDAB": "CDAB (word swap)",
               "BADC": "BADC (byte swap)", "DCBA": "DCBA (little endian)"}
SIM_WAVES = ["sine", "square", "triangle", "sawtooth", "ramp", "random", "constant", "counter"]
MQTT_MODES = {"json": "Number tags (JSON / plain value per topic)",
              "wavepacket": "Waveform packets (ESP32 DAQ, array tags)"}
MQTT_TRANSPORTS = ["TCP", "TLS/SSL", "WebSocket", "Secure WebSocket"]

_MODBUS_COMMON = [F("unit", "Unit ID (slave)", "int", 1, (0, 247)),
                  F("timeout_s", "Timeout", "float", 1.0, (0.1, 30), "seconds"),
                  F("max_block", "Max registers per read", "int", 100, (1, 125),
                    "Neighbouring tags are read in one request up to this size"),
                  F("one_based", "1-based addresses", "bool", False,
                    "On: address 1 is the first register (40001 style). Off: protocol address 0")]
_MQTT_BROKER = [F("host", "Broker", "str", "broker.emqx.io"),
                F("port", "Port", "int", 1883, (1, 65535)),
                F("transport", "Transport", "choice", "TCP", MQTT_TRANSPORTS),
                F("username", "Username", "str", ""),
                F("password", "Password", "password", ""),
                F("client_id", "Client ID", "str", "", tip="empty = random"),
                F("keepalive", "Keep-alive", "int", 30, (5, 600), "seconds"),
                F("mode", "Payload", "choice", "json", list(MQTT_MODES)),
                F("topic", "Waveform topic", "str", "spexth/daq/demo/wave",
                  tip="Wavepacket mode: the topic the DAQ node publishes (+ and # allowed)"),
                F("qos", "QoS", "choice", "0", ["0", "1", "2"])]

DRIVERS = {
    # key: label, icon, settings schema, address schema (number tags), note
    "sim": {"label": "Simulation", "icon": "waves", "settings": [],
            "address": [F("wave", "Signal", "choice", "sine", SIM_WAVES),
                        F("amp", "Amplitude", "float", 1.0),
                        F("freq", "Frequency", "float", 0.1, (0, 1e4), "Hz"),
                        F("offset", "Offset", "float", 0.0),
                        F("noise", "Noise (SD)", "float", 0.0, (0, 1e9))],
            "note": "Built-in signal generator - for testing views, alarms and logging."},
    "modbus_tcp": {"label": "Modbus TCP", "icon": "ethernet-port",
                   "settings": [F("host", "IP address", "str", "127.0.0.1"),
                                F("port", "Port", "int", 502, (1, 65535))] + _MODBUS_COMMON,
                   "address": None, "note": "PLC, meter or gateway over Ethernet (pymodbus)."},
    "modbus_rtu": {"label": "Modbus RTU", "icon": "usb",
                   "settings": [F("port", "Serial port", "str", "COM1", tip="COM3, /dev/ttyUSB0 ..."),
                                F("baudrate", "Baud rate", "choice", "9600",
                                  ["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"]),
                                F("parity", "Parity", "choice", "N", ["N", "E", "O"]),
                                F("stopbits", "Stop bits", "choice", "1", ["1", "2"]),
                                F("bytesize", "Data bits", "choice", "8", ["7", "8"])] + _MODBUS_COMMON,
                   "address": None, "note": "RS-485 / RS-232 serial devices (pymodbus + pyserial)."},
    "mqtt": {"label": "MQTT", "icon": "radio", "settings": _MQTT_BROKER,
             "address": [F("topic", "Topic", "str", "plant/line1/temp"),
                         F("path", "JSON path", "str", "",
                           tip="e.g. data.temp or values[2]; empty = the payload is the number")],
             "note": "Subscribe to a broker. Number tags read a topic (JSON field or plain value); "
                     "wavepacket mode receives ESP32 DAQ waveform blocks as array tags."},
    "opcua": {"label": "OPC UA client", "icon": "network",
              "settings": [F("endpoint", "Endpoint URL", "str", "opc.tcp://localhost:4840"),
                           F("username", "Username", "str", "", tip="empty = anonymous"),
                           F("password", "Password", "password", ""),
                           F("timeout_s", "Timeout", "float", 4.0, (0.5, 60), "seconds")],
              "address": [F("node", "Node ID", "str", "ns=2;s=Demo.Temperature",
                            tip="ns=<n>;s=<name> or ns=<n>;i=<number>; Browse... picks it from the server")],
              "note": "Read nodes of an OPC UA server (asyncua). Browse the address space to add tags."},
    "opcda": {"label": "OPC DA (planned)", "icon": "factory",
              "settings": [F("server", "Server ProgID", "str", "Matrikon.OPC.Simulation"),
                           F("host", "Host", "str", "localhost")],
              "address": [F("item", "Item ID", "str", "Random.Real8")],
              "note": "Classic OPC DA needs Windows COM - not available yet. Use an OPC UA wrapper "
                      "or gateway meanwhile."},
}
MODBUS_ADDRESS = [F("area", "Area", "choice", "holding", list(MODBUS_AREAS)),
                  F("addr", "Address", "int", 0, (0, 65535)),
                  F("dtype", "Data type", "choice", "float32", list(MODBUS_TYPES)),
                  F("order", "Word order", "choice", "ABCD", list(WORD_ORDERS)),
                  F("bit", "Bit (-1 = whole value)", "int", -1, (-1, 63)),
                  F("unit", "Unit ID (0 = device)", "int", 0, (0, 247))]
for _k in ("modbus_tcp", "modbus_rtu"):
    DRIVERS[_k]["address"] = MODBUS_ADDRESS
PLANNED = {"opcda"}                          # listed, cannot be started yet
for _d in DRIVERS.values():                  # every driver so far is a field / IoT protocol
    _d.setdefault("category", "protocols")

# Connectivity categories: the Project tree's Connectivity nodes and the left list of the
# Connectivity document. A device's category comes from its driver - nothing is stored.
CONN_CATEGORIES = [   # (key, name, icon, what it is)
    ("protocols", "Protocols", "factory", "Industrial and IoT protocols: Modbus TCP / RTU, OPC UA, MQTT, Simulation"),
    ("database", "Database", "database", "SQL and time-series databases: read tags from a query or log to them"),
    ("web", "Web Services", "globe", "HTTP APIs, webhooks, WebSocket, Firebase and the MCP server for AI clients"),
]
CONNECTORS_PLANNED = {   # connectors listed (muted) before they have a driver: category -> [(label, icon)]
    "database": [("MySQL", "database"), ("PostgreSQL", "database"), ("InfluxDB", "database")],
    "web": [("REST / HTTP", "globe"), ("Webhook (in)", "globe"), ("WebSocket", "globe"),
            ("Firebase", "globe"), ("MCP Server", "brain-circuit")],
}


def driver_category(d: str) -> str:
    return DRIVERS.get(d, {}).get("category", "protocols")


def category_drivers(cat: str) -> list[str]:
    """The drivers of a category in list order: ready ones, Simulation, then planned ones."""
    return sorted((k for k in DRIVERS if driver_category(k) == cat), key=lambda k: (k in PLANNED, k == "sim"))
SCAN_CHOICES = [100, 200, 250, 500, 1000, 2000, 5000, 10000, 60000]     # ms


def defaults(schema) -> dict:
    return {f["key"]: f["default"] for f in schema or []}


def driver_label(d: str) -> str:
    return DRIVERS.get(d, {}).get("label", d)


def describe_address(driver: str, mode: str, tag) -> str:
    """Short address text for tables: '40001 float32 ABCD', 'plant/t1 → data.temp', 'CH3'."""
    a = tag.address or {}
    if tag.kind == "array":
        return f"CH{tag.index + 1}"
    if driver in ("modbus_tcp", "modbus_rtu"):
        area = {"holding": "HR", "input": "IR", "coil": "CO", "discrete": "DI"}.get(a.get("area"), "?")
        bit = f".{a['bit']}" if int(a.get("bit", -1)) >= 0 else ""
        unit = f" @{a['unit']}" if int(a.get("unit", 0)) else ""
        dt = "" if a.get("area") in ("coil", "discrete") else f" {a.get('dtype', '')}"
        order = f" {a.get('order')}" if MODBUS_TYPES.get(a.get("dtype"), 1) > 1 and dt else ""
        return f"{area} {a.get('addr', 0)}{bit}{dt}{order}{unit}"
    if driver == "mqtt":
        return a.get("topic", "") + (f" → {a['path']}" if a.get("path") else "")
    if driver == "opcua":
        return a.get("node", "")
    if driver == "opcda":
        return a.get("item", "")
    if driver == "sim":
        return f"{a.get('wave', 'sine')} {a.get('amp', 1):g} @ {a.get('freq', 0.1):g} Hz"
    return ""


# ====================================================================== model
# Channel colours are a data ramp, separate from the UI tokens (DESIGN.md §7)
COLORS = ["#e8b100", "#2f9bf0", "#e5484d", "#30a46c",
          "#8e4ec6", "#f76b15", "#12a594", "#d6409f"]


@dataclass
class ChannelCfg:
    index: int                    # waveform: channel number in the packet; number tag: id in the device
    name: str
    unit: str = ""
    enabled: bool = True
    color: str = ""               # "" = default palette colour for the index
    description: str = ""
    scale: dict = field(default_factory=lambda: {"type": "none"})   # channel_math.make_scaler
    decimals: int = 3
    y_min: float | None = None    # fixed Y range on plots (both set) or auto
    y_max: float | None = None
    alarm_lo: float | None = None   # v5 only - migrated to the Alarms document
    alarm_hi: float | None = None


@dataclass
class TagCfg(ChannelCfg):
    """A tag: every Channel property plus where it comes from."""
    group: str = ""                     # "Boiler/Pumps" - folder path inside the device
    address: dict = field(default_factory=dict)
    access: str = "R"                   # R | RW (write from the tag table / properties)
    kind: str = "number"                # number | array (waveform channel)


@dataclass
class DeviceCfg:
    name: str
    driver: str = "sim"
    enabled: bool = True
    settings: dict = field(default_factory=dict)
    scan_ms: int = 1000
    tags: list[TagCfg] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)     # group paths (also the empty ones)
    description: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def __post_init__(self):
        self.settings = {**defaults(DRIVERS.get(self.driver, {}).get("settings")), **self.settings}

    # ---- the old TopicCfg API, so alarms / migration code reads devices like topics
    @property
    def channels(self) -> list[TagCfg]:
        return self.tags

    def channel(self, idx: int) -> TagCfg | None:
        return self.tag(idx)

    def tag(self, idx: int) -> TagCfg | None:
        return next((t for t in self.tags if t.index == idx), None)

    @property
    def wave(self) -> bool:
        """MQTT in wavepacket mode: tags are the packet channels (array tags)."""
        return self.driver == "mqtt" and self.settings.get("mode") == "wavepacket"

    @property
    def topic(self) -> str:
        """Connection text for tables and file headers."""
        s = self.settings
        if self.driver == "mqtt":
            return f"{s.get('host')}:{s.get('port')}" + (f" {s.get('topic')}" if self.wave else "")
        if self.driver == "modbus_tcp":
            return f"{s.get('host')}:{s.get('port')} unit {s.get('unit')}"
        if self.driver == "modbus_rtu":
            return f"{s.get('port')} {s.get('baudrate')} {s.get('bytesize')}{s.get('parity')}{s.get('stopbits')}"
        if self.driver == "opcua":
            return s.get("endpoint", "")
        if self.driver == "opcda":
            return f"{s.get('host')}/{s.get('server')}"
        return "built-in"

    def all_groups(self) -> list[str]:
        """Every group path, parents included, sorted."""
        out = set()
        for g in list(self.groups) + [t.group for t in self.tags]:
            parts = [p for p in (g or "").split("/") if p]
            for i in range(1, len(parts) + 1):
                out.add("/".join(parts[:i]))
        return sorted(out, key=str.lower)

    def next_index(self) -> int:
        return max((t.index for t in self.tags), default=-1) + 1


def tag_color(t: ChannelCfg) -> str:
    return t.color or COLORS[t.index % len(COLORS)]


def new_device(driver: str = "sim", name: str = "") -> DeviceCfg:
    d = DeviceCfg(name or driver_label(driver).split(" (")[0], driver)
    if driver == "sim":
        d.tags = [TagCfg(i, n, u, address={**defaults(DRIVERS["sim"]["address"]), **a})
                  for i, (n, u, a) in enumerate([
                      ("Temperature", "°C", {"wave": "sine", "amp": 5, "freq": 0.02, "offset": 25, "noise": 0.05}),
                      ("Pressure", "bar", {"wave": "triangle", "amp": 0.5, "freq": 0.01, "offset": 3.0}),
                      ("Flow", "m³/h", {"wave": "random", "amp": 2, "offset": 12}),
                      ("Level", "%", {"wave": "sawtooth", "amp": 40, "freq": 0.005, "offset": 50})])]
    return d


def new_tag(dev: DeviceCfg, name: str = "", group: str = "", **address) -> TagCfg:
    idx = dev.next_index()
    if dev.wave:
        idx = next(i for i in range(wp.MAX_CH + 64) if dev.tag(i) is None)
    addr = {} if dev.wave else {**defaults(DRIVERS[dev.driver]["address"]), **address}
    return TagCfg(idx, name or f"Tag{idx + 1}", group=group, address=addr,
                  kind="array" if dev.wave else "number")


def device_from_dict(d: dict) -> DeviceCfg:
    d = dict(d)
    tags = [TagCfg(**{k: v for k, v in t.items() if k in TagCfg.__dataclass_fields__})
            for t in d.pop("tags", [])]
    return DeviceCfg(**{k: v for k, v in d.items() if k in DeviceCfg.__dataclass_fields__}, tags=tags)


def device_from_topic(t, broker: dict) -> DeviceCfg:
    """An MQDAQ / SX Logger topic (wavepacket stream) -> MQTT device in wavepacket mode.
    Keeps the topic id, so alarms, variables, views and logging profiles keep pointing at it."""
    dev = DeviceCfg(t.name, "mqtt", t.enabled, {**broker, "mode": "wavepacket", "topic": t.topic,
                                                  "qos": str(t.qos)}, id=t.id)
    dev.tags = [TagCfg(**{**c.__dict__, "kind": "array"}) for c in t.channels]
    return dev


# ====================================================================== live block
class ScanBlock:
    """One scan of a device: a one-sample block of its number tags. Looks like
    wavepacket.WaveBlock to TopicStream, alarms, variables and the writers."""
    __slots__ = ("seq", "sample_idx", "t0", "dt", "channels", "y", "scales")

    def __init__(self, seq: int, t0: float, dt: float, channels: list[int], values):
        self.seq, self.sample_idx, self.t0, self.dt = seq & 0xFFFF, seq, t0, dt
        self.channels = list(channels)
        self.y = np.asarray(values, dtype=float).reshape(len(self.channels), 1)
        self.scales = np.ones(len(self.channels), dtype=np.float32)

    @property
    def n(self) -> int:
        return 1


# ====================================================================== tag list CSV
CSV_COLS = ["device", "group", "name", "unit", "description", "enabled", "access", "decimals",
            "address"]


def tags_to_rows(dev: DeviceCfg) -> list[dict]:
    """Rows for a tag list export (address as key=value;key=value)."""
    return [{"device": dev.name, "group": t.group, "name": t.name, "unit": t.unit,
             "description": t.description, "enabled": int(t.enabled), "access": t.access,
             "decimals": t.decimals,
             "address": ";".join(f"{k}={v}" for k, v in (t.address or {}).items())} for t in dev.tags]


def tag_from_row(dev: DeviceCfg, row: dict) -> TagCfg:
    """One imported row -> new tag of `dev` (unknown address keys are ignored)."""
    t = new_tag(dev, row.get("name", "").strip(), row.get("group", "").strip())
    schema = {f["key"]: f for f in DRIVERS[dev.driver]["address"] or []}
    for part in (row.get("address") or "").split(";"):
        if "=" not in part:
            continue
        k, v = (x.strip() for x in part.split("=", 1))
        f = schema.get(k)
        if f is None:
            continue
        try:
            t.address[k] = _typed(f, v)
        except ValueError:
            pass
    t.unit = row.get("unit", "") or ""
    t.description = row.get("description", "") or ""
    t.enabled = str(row.get("enabled", "1")).strip().lower() not in ("0", "false", "no")
    t.access = "RW" if str(row.get("access", "R")).upper() == "RW" else "R"
    try:
        t.decimals = int(row.get("decimals") or 3)
    except ValueError:
        pass
    return t


# ====================================================================== device list (JSON / CSV)
# Export / import of the Connectivity table. JSON is the full device (settings + groups + tags),
# CSV is one row per device (no tags) for spreadsheets. Passwords are never written: export
# files get mailed around; the project file keeps them.
DEVICES_FORMAT = "nxlogger-devices"
DEVICE_COLS = ["name", "driver", "enabled", "scan_ms", "description", "settings"]


def secret_keys(driver: str) -> set[str]:
    return {f["key"] for f in DRIVERS.get(driver, {}).get("settings") or [] if f["type"] == "password"}


def _typed(f: dict, v: str):
    """Text from a CSV cell -> the schema field's type (ValueError if it does not parse)."""
    return {"int": int, "float": float, "bool": lambda s: s.strip().lower() in ("1", "true", "yes", "on")}\
        .get(f["type"], str)(v)


def devices_to_json(devs: list[DeviceCfg], tags: bool = True) -> dict:
    out = []
    for d in devs:
        x = asdict(d)
        x["settings"] = {k: v for k, v in d.settings.items() if k not in secret_keys(d.driver)}
        if not tags:
            x["tags"], x["groups"] = [], []
        out.append(x)
    return {"format": DEVICES_FORMAT, "version": 1, "devices": out}


def devices_from_json(data) -> list[DeviceCfg]:
    """An export file, a project file (.nxl, its "devices") or a bare list of devices."""
    items = data.get("devices") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("no device list in the file")
    out = []
    for x in items:
        if not isinstance(x, dict) or x.get("driver") not in DRIVERS or not str(x.get("name", "")).strip():
            raise ValueError(f"not a device: {str(x)[:60]}")
        out.append(device_from_dict(x))
    return out


def devices_to_rows(devs: list[DeviceCfg]) -> list[dict]:
    """CSV rows (settings as key=value;key=value, like the tag list's address)."""
    return [{"name": d.name, "driver": d.driver, "enabled": int(d.enabled), "scan_ms": d.scan_ms,
             "description": d.description,
             "settings": ";".join(f"{k}={v}" for k, v in d.settings.items() if k not in secret_keys(d.driver))}
            for d in devs]


def device_from_row(row: dict) -> DeviceCfg:
    """One CSV row -> device without tags. The connector is its key or its label
    ("modbus_tcp" / "Modbus TCP"); unknown settings are ignored, bad numbers keep the default."""
    drv = (row.get("driver") or "").strip()
    drv = drv if drv in DRIVERS else next((k for k, v in DRIVERS.items() if v["label"].lower() == drv.lower()), "")
    name = (row.get("name") or "").strip()
    if not drv or not name:
        raise ValueError(f"row needs a name and a connector: {name or '?'} / {drv or row.get('driver') or '?'}")
    d = DeviceCfg(name, drv)
    schema = {f["key"]: f for f in DRIVERS[drv]["settings"]}
    for part in (row.get("settings") or "").split(";"):
        k, _, v = part.partition("=")
        f = schema.get(k.strip())
        if f is not None:
            try:
                d.settings[f["key"]] = _typed(f, v.strip())
            except ValueError:
                pass
    d.enabled = str(row.get("enabled", "1")).strip().lower() not in ("0", "false", "no", "off")
    try:
        d.scan_ms = max(20, int(float(row.get("scan_ms") or 1000)))
    except ValueError:
        pass
    d.description = row.get("description", "") or ""
    return d


class DeviceFileError(ValueError):
    """A device file that must not be imported; `problems` lists every reason (line numbers)."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems[:3]) + (f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""))
        self.problems = problems


def read_devices_file(path: str) -> list[DeviceCfg]:
    """Strict import check: JSON (device export, .nxl project, list) or CSV device list.
    Any problem - wrong format, unknown connector or setting, bad number, duplicate name -
    raises DeviceFileError with all of them; nothing is imported from a file that fails."""
    import csv
    import json
    low = path.lower()
    if not low.endswith((".json", ".nxl", ".csv")):
        raise DeviceFileError(["not a device file: use .json, .nxl or .csv"])
    problems, devs = [], []
    try:
        if low.endswith(".csv"):
            with open(path, encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                cols = [c.strip() for c in reader.fieldnames or []]
                missing = [c for c in ("name", "driver") if c not in cols]
                unknown = [c for c in cols if c not in DEVICE_COLS]
                if missing or unknown:
                    raise DeviceFileError(
                        ([f"missing column(s): {', '.join(missing)}"] if missing else [])
                        + ([f"unknown column(s): {', '.join(unknown)}"] if unknown else [])
                        + [f"expected header: {','.join(DEVICE_COLS)}"])
                for n, row in enumerate(reader, start=2):
                    row = {(k or "").strip(): v for k, v in row.items()}
                    if not any(isinstance(v, str) and v.strip() for v in row.values()):
                        continue                                     # blank line
                    problems += [f"line {n}: {p}" for p in _row_problems(row)]
                    if not problems:
                        devs.append(device_from_row(row))
        else:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("format") not in (DEVICES_FORMAT, "nxlogger"):
                raise DeviceFileError([f"not an NX Logger device or project file (format: {data.get('format')!r})"])
            items = data.get("devices") if isinstance(data, dict) else data
            if not isinstance(items, list):
                raise DeviceFileError(["no device list in the file"])
            for n, x in enumerate(items, start=1):
                p = _json_problems(x)
                problems += [f"device {n}: {q}" for q in p]
                if not p:
                    try:
                        devs.append(device_from_dict(x))
                    except (TypeError, ValueError) as e:
                        problems.append(f"device {n}: {e}")
    except DeviceFileError:
        raise
    except UnicodeDecodeError:
        raise DeviceFileError(["not a text file (UTF-8)"])
    except json.JSONDecodeError as e:
        raise DeviceFileError([f"not valid JSON: line {e.lineno}, column {e.colno}: {e.msg}"])
    except (OSError, csv.Error) as e:
        raise DeviceFileError([str(e)])
    names = [d.name for d in devs]
    problems += [f"device name used twice: {x}" for x in sorted({x for x in names if names.count(x) > 1})]
    if not problems and not devs:
        problems.append("no devices in the file")
    if problems:
        raise DeviceFileError(problems)
    return devs


def _tag_row_problems(dev: DeviceCfg, row: dict) -> list[str]:
    out = []
    if not (row.get("name") or "").strip():
        out.append("no name")
    schema = {f["key"]: f for f in DRIVERS[dev.driver]["address"] or []}
    for part in (row.get("address") or "").split(";"):
        if not part.strip():
            continue
        k, eq, v = (x.strip() for x in part.partition("="))
        if not eq:
            out.append(f"address part '{part.strip()}' is not key=value")
        elif k not in schema:
            out.append(f"unknown address key '{k}' for {driver_label(dev.driver)}"
                       + (f" (use {', '.join(schema)})" if schema else " (this connector has no tag address)"))
        else:
            try:
                _typed(schema[k], v)
            except ValueError:
                out.append(f"address {k}: '{v}' is not a{'n' if schema[k]['type'] == 'int' else ''} {schema[k]['type']}")
    if str(row.get("access") or "R").strip().upper() not in ("R", "RW"):
        out.append(f"access must be R or RW, not '{row.get('access')}'")
    try:
        int(row.get("decimals") or 3)
    except ValueError:
        out.append(f"decimals: '{row.get('decimals')}' is not a whole number")
    return out


def read_tags_file(path: str, dev: DeviceCfg) -> list[TagCfg]:
    """Strict check of a tag file for device `dev` (nothing is imported from a file that fails):
    a tag list CSV (CSV_COLS; rows of other devices are left out when the device column names
    this one) or a device JSON export (the tags of the device with this name, or of the only
    device - same connector, addresses must fit). New tags, indices not assigned yet."""
    import csv
    low = path.lower()
    if not low.endswith((".csv", ".json", ".nxl")):
        raise DeviceFileError(["not a tag file: use .csv (tag list) or .json (device export)"])
    problems, tags = [], []
    try:
        if low.endswith(".csv"):
            with open(path, encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                cols = [c.strip() for c in reader.fieldnames or []]
                unknown = [c for c in cols if c not in CSV_COLS]
                if "name" not in cols or unknown:
                    raise DeviceFileError((["missing column: name"] if "name" not in cols else [])
                                          + ([f"unknown column(s): {', '.join(unknown)}"] if unknown else [])
                                          + [f"expected header: {','.join(CSV_COLS)}"])
                rows = [(n, {(k or "").strip(): v for k, v in r.items()}) for n, r in enumerate(reader, start=2)]
            rows = [(n, r) for n, r in rows if any(isinstance(v, str) and v.strip() for v in r.values())]
            if any((r.get("device") or "").strip() == dev.name for _, r in rows):     # a multi-device list
                rows = [(n, r) for n, r in rows if (r.get("device") or "").strip() == dev.name]
            for n, r in rows:
                p = _tag_row_problems(dev, r)
                problems += [f"line {n}: {q}" for q in p]
                if not p:
                    tags.append(tag_from_row(dev, r))
        else:
            devs = read_devices_file(path)
            src = next((d for d in devs if d.name == dev.name), devs[0] if len(devs) == 1 else None)
            if src is None:
                raise DeviceFileError([f"no device named '{dev.name}' in the file "
                                       f"({', '.join(d.name for d in devs[:6])})"])
            if src.driver != dev.driver:
                raise DeviceFileError([f"the tags are for a {driver_label(src.driver)} device; "
                                       f"{dev.name} is {driver_label(dev.driver)} - the addresses do not fit"])
            tags = list(src.tags)
    except DeviceFileError:
        raise
    except UnicodeDecodeError:
        raise DeviceFileError(["not a text file (UTF-8)"])
    except (OSError, csv.Error) as e:
        raise DeviceFileError([str(e)])
    names = [t.name for t in tags]
    problems += [f"tag name used twice: {x}" for x in sorted({x for x in names if names.count(x) > 1})]
    if not problems and not tags:
        problems.append(f"no tags for {dev.name} in the file")
    if problems:
        raise DeviceFileError(problems)
    return tags


def _row_problems(row: dict) -> list[str]:
    out = []
    drv = (row.get("driver") or "").strip()
    key = drv if drv in DRIVERS else next((k for k, v in DRIVERS.items() if v["label"].lower() == drv.lower()), "")
    if not (row.get("name") or "").strip():
        out.append("empty name")
    if not key:
        out.append(f"unknown connector {drv!r} (use {', '.join(DRIVERS)})")
    if str(row.get("enabled", "1")).strip().lower() not in ("", "0", "1", "true", "false", "yes", "no", "on", "off"):
        out.append(f"enabled must be 1 or 0, not {row.get('enabled')!r}")
    try:
        if int(float(row.get("scan_ms") or 1000)) < 20:
            out.append("scan_ms below 20")
    except ValueError:
        out.append(f"scan_ms is not a number: {row.get('scan_ms')!r}")
    if key:
        schema = {f["key"]: f for f in DRIVERS[key]["settings"]}
        for part in (row.get("settings") or "").split(";"):
            if not part.strip():
                continue
            k, eq, v = part.partition("=")
            f = schema.get(k.strip())
            if not eq or f is None:
                out.append(f"unknown setting {k.strip()!r} for {DRIVERS[key]['label']}")
                continue
            try:
                _typed(f, v.strip())
            except ValueError:
                kind = {"int": "whole number", "float": "number", "bool": "yes / no"}.get(f["type"], f["type"])
                out.append(f"setting {k.strip()}: {v.strip()!r} is not a {kind}")
    return out


def _json_problems(x) -> list[str]:
    if not isinstance(x, dict):
        return ["not a device object"]
    out = []
    if not str(x.get("name") or "").strip():
        out.append("empty name")
    if x.get("driver") not in DRIVERS:
        out.append(f"unknown connector {x.get('driver')!r}")
    if not isinstance(x.get("settings", {}), dict):
        out.append("settings is not an object")
    tags = x.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, dict) and isinstance(t.get("index"), int)
                                             and str(t.get("name") or "").strip() for t in tags):
        out.append("tags must be a list of {index, name, ...}")
    return out
