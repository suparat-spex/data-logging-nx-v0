"""Factory energy simulator (sim/factory_energy.py): the model, the Modbus server read by NX
Logger's own Modbus driver, the device export and the Factory energy demo template."""
import queue
import socket
import struct
import time

import pytest

from nxlogger.core.tags import read_devices_file
from nxlogger.sim import factory_energy as fe


def _day(seed=1, day=(2026, 10, 27)):                       # a Tuesday
    f = fe.Factory(seed)
    t = time.mktime((*day, 0, 0, 0, 0, 0, -1))
    return [f.step(60 if i else 0, t + i * 60) for i in range(24 * 60)]


def test_model_day_profile_and_physics():
    rows = _day()
    assert set(rows[0]) == {m[1] for m in fe.METERS}
    assert set(rows[0]["main"]) == {f[0] for f in fe.FIELDS}
    main = [r["main"]["kw"] for r in rows]
    assert sum(main[600:660]) / 60 > 1.4 * sum(main[180:240]) / 60      # 10:00 (day shift) above 03:00
    for r in rows[::97]:                                    # the incomer = the feeders - solar
        feeders = sum(r[m]["kw"] for m in r if m not in ("main", "solar"))
        assert r["main"]["kw"] == pytest.approx(max(feeders - r["solar"]["kw"], 0), abs=0.01)
        assert 0 <= r["main"]["pf"] <= 1 and 49.8 < r["main"]["hz"] < 50.2
    assert rows[180]["solar"]["kw"] == 0 and rows[720]["solar"]["kw"] > 50       # night / noon
    kwh = [r["main"]["kwh"] for r in rows]
    assert all(b >= a for a, b in zip(kwh, kwh[1:]))        # counters only count up
    sunday = _day(day=(2026, 11, 1))
    assert max(r["line2"]["kw"] for r in sunday) < 20       # standby on Sunday


def test_registers_and_device_export(tmp_path):
    regs = fe.registers({"v": 230.5, "kw": 12.25, "pf": 0.9})
    assert len(regs) == fe.REGS
    f32 = lambda off: struct.unpack(">f", struct.pack(">HH", regs[off], regs[off + 1]))[0]   # noqa: E731
    assert f32(0) == 230.5 and f32(4) == 12.25 and f32(10) == pytest.approx(0.9)
    dev = fe.nx_device(port=5020)
    assert len(dev.tags) == len(fe.METERS) * len(fe.FIELDS) == 99
    assert len({t.name for t in dev.tags}) == 99 and {t.address["unit"] for t in dev.tags} == set(range(1, 12))
    assert {t.group for t in dev.tags} == {"Main", "Production", "Utilities", "Building", "Solar"}
    js = tmp_path / "energy.json"
    fe.export_device(str(js), dev)
    (imported,) = read_devices_file(str(js))                # Connectivity > Import...
    assert imported.driver == "modbus_tcp" and imported.settings["port"] == 5020 and len(imported.tags) == 99


def test_server_read_by_the_modbus_driver():
    pytest.importorskip("pymodbus")
    from nxlogger.comms.drivers import make_driver
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    values = fe.Factory(3).step(0, time.mktime((2026, 10, 27, 10, 0, 0, 0, 0, -1)))
    srv = fe.Server("127.0.0.1", port)
    srv.update(values)
    srv.start()
    try:
        time.sleep(0.8)
        dev = fe.nx_device(port=port)
        dev.scan_ms = 200
        q = queue.Queue()
        d = make_driver(dev, q)
        d.start()
        scan, end = None, time.time() + 8
        while scan is None and time.time() < end:
            try:
                m = q.get(timeout=0.2)
            except queue.Empty:
                continue
            if m[0] == "scan":
                scan = m
        d.stop()
        assert scan is not None, "no scan from the simulator"
        got = dict(zip(scan[4], scan[5]))                   # tag index -> value
        by_name = {t.name: got.get(t.index) for t in dev.tags}
        assert by_name["Comp1_kW"] == pytest.approx(values["comp1"]["kw"], abs=0.01)
        assert by_name["Main_PF"] == pytest.approx(values["main"]["pf"], abs=1e-4)
        assert by_name["Solar_kWh"] == pytest.approx(values["solar"]["kwh"], rel=1e-6)
    finally:
        srv.stop()


def test_template_definitions_are_valid():
    from nxlogger.core import aggregation as ag
    from nxlogger.core import templates as tpl
    from nxlogger.core import virtual_tags as vt
    p = tpl.make("factory_energy")
    names = {t.name for t in p.devices[0].tags}
    assert {"Main_kW", "Solar_kW", "Comp1_kW"} <= names
    assert {v["name"] for v in p.virtual_tags} >= {"Cost_THB_h", "On_peak", "Rate_on_peak", "Total_load_kW"}
    assert {a["name"] for a in p.aggregations} >= {"Demand_15min", "Peak_demand_today", "Energy_today"}
    assert [v["name"] for v in p.views] == ["Energy overview", "Feeders", "Demand & cost"]
    assert {a["name"] for a in p.alarms} == {"Peak demand", "Low power factor", "Compressor 1 trip",
                                             "Incomer overload"}
    keys = {(p.devices[0].id, t.index) for t in p.devices[0].tags}
    keys |= {vt.vt_key(v["id"]) for v in p.virtual_tags} | {ag.agg_key(a["id"], i) for a in p.aggregations
                                                           for i in (0, 1)}
    for view in p.views:                                     # every component points at a real tag
        for t in view["tiles"]:
            for k in (t.get("channels") or []) + ([t["channel"]] if t.get("channel") else []):
                assert tuple(k) in keys, (view["name"], k)
