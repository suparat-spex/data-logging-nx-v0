"""Connectors without the GUI: value coding, block planning, and real round trips against
local Modbus TCP and OPC UA servers (skipped when pymodbus / asyncua are not installed)."""
import queue
import socket
import threading
import time

import pytest

from nxlogger.comms.drivers import make_driver, test_connection as try_connection
from nxlogger.comms.drivers.mqtt import json_value
from nxlogger.core.tags import DeviceCfg, TagCfg, new_device, new_tag


def _drain(q, kind, n=1, timeout=8.0):
    out, end = [], time.time() + timeout
    while len(out) < n and time.time() < end:
        try:
            m = q.get(timeout=0.1)
        except queue.Empty:
            continue
        if m[0] == kind:
            out.append(m)
    return out


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def test_json_paths():
    assert json_value(b"21.5", "") == 21.5
    assert json_value(b'{"data": {"temp": 3}}', "data.temp") == 3.0
    assert json_value(b'{"v": [1, 2, {"x": true}]}', "v[2].x") == 1.0
    assert json_value(b'{"value": 7, "ts": 1}', "") == 7.0
    assert json_value(b"not a number", "") is None and json_value(b"{}", "a.b") is None


def test_modbus_value_coding():
    md = pytest.importorskip("nxlogger.comms.drivers.modbus")
    for order in ("ABCD", "CDAB", "BADC", "DCBA"):
        for dtype, v in (("float32", -12.5), ("int32", -70000), ("uint16", 65000), ("float64", 1e-3)):
            assert md.regs_to_value(md.value_to_regs(v, dtype, order), dtype, order) == pytest.approx(v)
    assert md.value_to_regs(1.0, "float32", "ABCD") == [0x3F80, 0x0000]
    assert md.value_to_regs(1.0, "float32", "CDAB") == [0x0000, 0x3F80]


def test_modbus_block_plan_merges_neighbours():
    md = pytest.importorskip("nxlogger.comms.drivers.modbus")
    dev = DeviceCfg("plc", "modbus_tcp")
    for addr, dtype in ((0, "float32"), (2, "int16"), (40, "uint16")):
        dev.tags.append(new_tag(dev, area="holding", addr=addr, dtype=dtype))
    blocks = md.plan_blocks(dev.tags, 1, False, 100)
    assert [(b[2], b[3], len(b[4])) for b in blocks] == [(0, 3, 2), (40, 1, 1)]


def test_sim_driver_scans():
    dev = new_device("sim")
    dev.scan_ms = 50
    q = queue.Queue()
    d = make_driver(dev, q)
    d.start()
    scans = _drain(q, "scan", 3)
    d.stop()
    assert len(scans) == 3 and len(scans[0][4]) == 4
    assert all(v is not None for v in scans[-1][5])


def test_modbus_tcp_round_trip():
    pytest.importorskip("pymodbus")
    from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext
    try:
        from pymodbus.datastore import ModbusSlaveContext as Ctx
    except ImportError:                                              # pymodbus >= 3.10
        from pymodbus.datastore import ModbusDeviceContext as Ctx
    from pymodbus.server import ServerStop, StartTcpServer
    from nxlogger.comms.drivers.modbus import value_to_regs

    regs = [0] * 100
    regs[10:12] = value_to_regs(21.5, "float32", "ABCD")
    regs[12] = 0xFFFE                                                # int16 -2
    ctx = ModbusServerContext(Ctx(hr=ModbusSequentialDataBlock(0, regs),
                                  co=ModbusSequentialDataBlock(0, [False] * 16)), single=True)
    port = _free_port()
    threading.Thread(target=StartTcpServer, kwargs={"context": ctx, "address": ("127.0.0.1", port)},
                     daemon=True).start()
    time.sleep(0.8)
    dev = DeviceCfg("plc", "modbus_tcp", settings={"host": "127.0.0.1", "port": port}, scan_ms=100)
    # ModbusSequentialDataBlock(0, ...) is 1-based on the wire in pymodbus: address n -> regs[n + 1]
    dev.tags.append(new_tag(dev, "T", area="holding", addr=9, dtype="float32"))
    dev.tags.append(new_tag(dev, "S", area="holding", addr=11, dtype="int16"))
    dev.tags[1].access = "RW"
    q = queue.Queue()
    d = make_driver(dev, q)
    d.start()
    try:
        scan = _drain(q, "scan", 1)[0]
        assert scan[5] == [pytest.approx(21.5), -2.0]
        d.write(dev.tags[1].index, 123)
        assert _drain(q, "write", 1)[0][2] is True
        time.sleep(0.3)
        assert _drain(q, "scan", 1)[0][5][1] == 123.0
        ok, text = try_connection(dev)                  # the device pop-up's Test connection
        assert ok and text.startswith("connected in ") and "2/2 tag(s)" in text, text
    finally:
        d.stop()
        ServerStop()


def test_connection_test_reports_failures():
    ok, text = try_connection(new_device("sim"))
    assert ok and "4/4" in text
    port = _free_port()                                 # nothing listens there
    ok, text = try_connection(DeviceCfg("plc", "modbus_tcp", settings={"host": "127.0.0.1", "port": port,
                                                                       "timeout_s": 0.5}))
    assert not ok and text, text
    ok, text = try_connection(DeviceCfg("da", "opcda"))
    assert not ok and "not available" in text


def test_opcua_round_trip():
    pytest.importorskip("asyncua")
    from asyncua.sync import Server
    port = _free_port()
    srv = Server()
    srv.set_endpoint(f"opc.tcp://127.0.0.1:{port}/nx")
    ns = srv.register_namespace("urn:nx-test")
    obj = srv.nodes.objects.add_object(ns, "Plant")
    var = obj.add_variable(ns, "Temperature", 21.25)
    flag = obj.add_variable(ns, "Running", True)
    var.set_writable()
    srv.start()
    try:
        dev = DeviceCfg("ua", "opcua", settings={"endpoint": f"opc.tcp://127.0.0.1:{port}/nx"}, scan_ms=100)
        dev.tags.append(new_tag(dev, "T", node=var.nodeid.to_string()))
        dev.tags.append(new_tag(dev, "R", node=flag.nodeid.to_string()))
        dev.tags[0].access = "RW"
        q = queue.Queue()
        d = make_driver(dev, q)
        d.start()
        try:
            assert _drain(q, "scan", 1)[0][5] == [21.25, 1.0]
            d.write(dev.tags[0].index, 30.5)
            assert _drain(q, "write", 1)[0][2] is True
            assert var.read_value() == 30.5
        finally:
            d.stop()
        from nxlogger.comms.drivers.opcua import Browser
        b = Browser(dev.settings)
        try:
            plant = next(r for r in b.children() if r["name"] == "Plant")
            names = {r["name"]: r for r in b.children(plant["node"])}
            assert names["Temperature"]["variable"] and names["Temperature"]["type"] == "Double"
        finally:
            b.close()
    finally:
        srv.stop()


def test_tag_kind_follows_device():
    wave = DeviceCfg("esp", "mqtt", settings={"mode": "wavepacket"})
    assert new_tag(wave).kind == "array" and new_tag(wave).address == {}
    assert new_tag(DeviceCfg("m", "mqtt")).address == {"topic": "plant/line1/temp", "path": ""}
    assert isinstance(new_tag(DeviceCfg("s", "sim")), TagCfg)
