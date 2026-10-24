"""
opcua.py - OPC UA client (asyncua sync API, in the driver thread).

Each scan reads all node values in one Read request. Booleans read as 0 / 1, numbers as
they are; anything else (strings, structures) is bad quality. Browser lists the address
space for the "Browse..." dialog (also off the GUI thread).
"""
from __future__ import annotations

from nxlogger.comms.drivers.base import PollDriver


def _client(settings):
    from asyncua.sync import Client
    c = Client(settings.get("endpoint", "opc.tcp://localhost:4840"),
               timeout=float(settings.get("timeout_s", 4.0)))
    if settings.get("username"):
        c.set_user(settings["username"])
        c.set_password(settings.get("password", ""))
    return c


def to_float(v) -> float | None:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    return None


class OpcUaDriver(PollDriver):
    def __init__(self, dev, out):
        super().__init__(dev, out)
        self.client = None

    def open(self):
        self.client = _client(self.dev.settings)
        self.client.connect()
        self._nodes = {}
        for t in self.number_tags():
            self._nodes[t.index] = self.client.get_node(t.address.get("node", ""))

    def close(self):
        if self.client is not None:
            try:
                self.client.disconnect()
            finally:
                self.client = None

    def read(self, tags):
        nodes = [self._nodes[t.index] for t in tags]
        try:
            vals = self.client.read_values(nodes)
        except Exception as e:
            if "Bad" not in str(e):                       # session / socket gone: reconnect
                raise
            vals = []                                     # one bad node fails the batch: read singly
            for n in nodes:
                try:
                    vals.append(n.read_value())
                except Exception:
                    vals.append(None)
        return [to_float(v) for v in vals]

    def do_write(self, tag, value):
        from asyncua import ua
        node = self._nodes[tag.index]
        vt = node.read_data_type_as_variant_type()
        if vt == ua.VariantType.Boolean:
            v = bool(value)
        elif vt in (ua.VariantType.Float, ua.VariantType.Double):
            v = float(value)
        else:
            v = int(round(value))
        node.write_value(ua.DataValue(ua.Variant(v, vt)))


class Browser:
    """Address space browsing for the tag dialog: children(node id) -> rows."""

    def __init__(self, settings: dict):
        self.client = _client(settings)
        self.client.connect()

    def children(self, node_id: str | None = None) -> list[dict]:
        from asyncua import ua
        node = self.client.get_node(node_id) if node_id else self.client.get_objects_node()
        out = []
        for ch in node.get_children():
            try:
                cls = ch.read_node_class()
                row = {"node": ch.nodeid.to_string(), "name": ch.read_browse_name().Name,
                       "variable": cls == ua.NodeClass.Variable, "type": "", "value": ""}
                if row["variable"]:
                    try:
                        row["type"] = ch.read_data_type_as_variant_type().name
                        v = ch.read_value()
                        row["value"] = f"{v:g}" if isinstance(v, float) else str(v)[:40]
                    except Exception:
                        pass
                out.append(row)
            except Exception:
                continue
        return sorted(out, key=lambda r: (r["variable"], r["name"].lower()))

    def close(self):
        try:
            self.client.disconnect()
        except Exception:
            pass
