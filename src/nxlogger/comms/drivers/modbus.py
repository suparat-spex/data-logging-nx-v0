"""
modbus.py - Modbus TCP and RTU master (pymodbus, sync client in the driver thread).

Tags are grouped into block reads: tags of the same unit and area whose registers are
close (gap <= MAX_GAP) are read in one request of at most `max_block` registers (2000 bits
for coils / discrete inputs). A Modbus exception (illegal address ...) marks only that
block's tags bad; a connection error reconnects.

Values: int16 / uint16 / int32 / uint32 / int64 / uint64 / float32 / float64 / bool,
word order ABCD (big endian, Modbus standard), CDAB (word swap), BADC (byte swap),
DCBA (little endian). bit >= 0 picks one bit of the value (status words).
"""
from __future__ import annotations

import inspect
import struct

from nxlogger.comms.drivers.base import PollDriver
from nxlogger.core.tags import MODBUS_TYPES

MAX_GAP = 4
_FMT = {"int16": "h", "uint16": "H", "int32": "i", "uint32": "I", "float32": "f",
        "int64": "q", "uint64": "Q", "float64": "d", "bool": "H"}


def regs_to_value(regs: list[int], dtype: str, order: str = "ABCD") -> float:
    """Registers (16-bit, as read) -> number, with the device's word / byte order."""
    raw = b"".join(struct.pack(">H", r & 0xFFFF) for r in regs)
    if len(regs) > 1 or order in ("BADC", "DCBA"):
        raw = reorder(raw, order)
    return float(struct.unpack(">" + _FMT[dtype], raw)[0])


def value_to_regs(value: float, dtype: str, order: str = "ABCD") -> list[int]:
    code = _FMT[dtype]
    v = float(value) if code in "fd" else int(round(value))
    raw = struct.pack(">" + code, v)
    if len(raw) > 2 or order in ("BADC", "DCBA"):
        raw = reorder(raw, order, inverse=True)
    return [struct.unpack(">H", raw[i:i + 2])[0] for i in range(0, len(raw), 2)]


def reorder(raw: bytes, order: str, inverse: bool = False) -> bytes:
    """Wire bytes in `order` -> big endian ABCD (inverse: the other way; all four are involutions)."""
    words = [raw[i:i + 2] for i in range(0, len(raw), 2)]
    if order == "CDAB":                       # words reversed, bytes in a word big endian
        words = words[::-1]
    elif order == "BADC":                     # word order kept, bytes swapped
        words = [w[::-1] for w in words]
    elif order == "DCBA":                     # fully little endian
        words = [w[::-1] for w in words[::-1]]
    return b"".join(words)


def plan_blocks(tags, unit: int, one_based: bool, max_block: int):
    """[(unit, area, start, count, [(tag, offset)])] - neighbouring tags in one request."""
    items = []
    for t in tags:
        a = t.address
        area = a.get("area", "holding")
        addr = int(a.get("addr", 0)) - (1 if one_based else 0)
        n = 1 if area in ("coil", "discrete") else MODBUS_TYPES.get(a.get("dtype", "uint16"), 1)
        items.append((int(a.get("unit") or 0) or unit, area, max(0, addr), n, t))
    items.sort(key=lambda x: (x[0], x[1], x[2]))
    blocks = []
    for u, area, addr, n, t in items:
        limit = 2000 if area in ("coil", "discrete") else max_block
        b = blocks[-1] if blocks else None
        if b and b[0] == u and b[1] == area and addr - (b[2] + b[3]) <= MAX_GAP \
                and addr + n - b[2] <= limit:
            b[3] = max(b[3], addr + n - b[2])
            b[4].append((t, addr - b[2]))
        else:
            blocks.append([u, area, addr, n, [(t, 0)]])
    return [tuple(b) for b in blocks]


class ModbusDriver(PollDriver):
    def __init__(self, dev, out):
        super().__init__(dev, out)
        self.client = None
        self._unit_kw = "slave"

    def open(self):
        from pymodbus.client import ModbusSerialClient, ModbusTcpClient
        s = self.dev.settings
        timeout = float(s.get("timeout_s", 1.0))
        if self.dev.driver == "modbus_tcp":
            self.client = ModbusTcpClient(s.get("host", "127.0.0.1"), port=int(s.get("port", 502)),
                                          timeout=timeout, retries=1)
        else:
            self.client = ModbusSerialClient(s.get("port", "COM1"), baudrate=int(s.get("baudrate", 9600)),
                                             parity=s.get("parity", "N"), stopbits=int(s.get("stopbits", 1)),
                                             bytesize=int(s.get("bytesize", 8)), timeout=timeout, retries=1)
        params = inspect.signature(self.client.read_holding_registers).parameters
        self._unit_kw = "device_id" if "device_id" in params else "slave"   # pymodbus >= 3.10
        if not self.client.connect():
            raise ConnectionError(f"cannot connect to {self.dev.topic}")
        s = self.dev.settings
        self._blocks = plan_blocks(self.number_tags(), int(s.get("unit", 1)), bool(s.get("one_based")),
                                   int(s.get("max_block", 100)))

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None

    def interrupt(self):
        try:
            if self.client is not None:
                self.client.close()
        except Exception:
            pass

    def _call(self, name, *args, unit, **kw):
        return getattr(self.client, name)(*args, **{**kw, self._unit_kw: unit})

    def read(self, tags):
        out = {}
        for unit, area, start, count, members in self._blocks:
            fn = {"holding": "read_holding_registers", "input": "read_input_registers",
                  "coil": "read_coils", "discrete": "read_discrete_inputs"}[area]
            rr = self._call(fn, start, count=count, unit=unit)
            if rr.isError():
                if not hasattr(rr, "exception_code"):        # no reply / framing: the link is gone
                    raise ConnectionError(str(rr))
                for t, _ in members:                         # Modbus exception: only these tags bad
                    out[t.index] = None
                continue
            for t, off in members:
                a = t.address
                if area in ("coil", "discrete"):
                    out[t.index] = float(bool(rr.bits[off]))
                    continue
                n = MODBUS_TYPES.get(a.get("dtype", "uint16"), 1)
                v = regs_to_value(rr.registers[off:off + n], a.get("dtype", "uint16"), a.get("order", "ABCD"))
                bit = int(a.get("bit", -1))
                out[t.index] = float((int(v) >> bit) & 1) if bit >= 0 else v
        return [out.get(t.index) for t in tags]

    def do_write(self, tag, value):
        a = tag.address
        s = self.dev.settings
        unit = int(a.get("unit") or 0) or int(s.get("unit", 1))
        addr = int(a.get("addr", 0)) - (1 if s.get("one_based") else 0)
        area = a.get("area", "holding")
        if area == "coil":
            rr = self._call("write_coil", addr, bool(value), unit=unit)
        elif area == "holding":
            dtype, order = a.get("dtype", "uint16"), a.get("order", "ABCD")
            bit = int(a.get("bit", -1))
            if bit >= 0:                                    # read-modify-write one bit
                n = MODBUS_TYPES.get(dtype, 1)
                cur = self._call("read_holding_registers", addr, count=n, unit=unit)
                if cur.isError():
                    raise RuntimeError(str(cur))
                word = int(regs_to_value(cur.registers, dtype, order))
                value = (word | (1 << bit)) if value else (word & ~(1 << bit))
            rr = self._call("write_registers", addr, value_to_regs(value, dtype, order), unit=unit)
        else:
            raise RuntimeError(f"{area} is read-only in Modbus")
        if rr.isError():
            raise RuntimeError(str(rr))
