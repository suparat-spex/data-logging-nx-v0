import time

from nxlogger.core import system_tags as ss
from nxlogger.core.variables import VariableEngine, new_variable


def test_catalog_keys_are_stable_per_device():
    tags = ss.catalog([("d1", "PLC")])
    ids = [t.id for t in tags]
    assert len(ids) == len(set(ids))
    assert "dev.d1.Connected" in ids and "DateTime" in ids
    con = next(t for t in tags if t.id == "dev.d1.Connected")
    assert con.name == "PLC.Connected" and con.key == ("sys:dev.d1.Connected", 0)
    renamed = next(t for t in ss.catalog([("d1", "Boiler")]) if t.id == "dev.d1.Connected")
    assert renamed.key == con.key and renamed.name == "Boiler.Connected"
    assert ss.is_sys(con.key[0]) and not ss.is_sys("var:x") and not ss.is_sys(None)


def test_compute_clock_device_and_recording():
    now = time.mktime((2026, 10, 26, 14, 5, 9, 0, 0, -1))
    v = ss.compute({"now": now, "started": now - 90, "project": "P", "version": "2.0.0",
                    "disk_free": 5e9, "recording": True, "rec_dir": "C:/rec/P_1", "rec_bytes": 2.5e6,
                    "rec_elapsed": 12.7, "alarms_active": 2,
                    "devices": {"a": {"state": "online", "last": now - 1, "stale_s": 5, "errors": 3},
                                "b": {"state": "online", "last": now - 60, "stale_s": 5},
                                "c": {"state": "stopped", "last": None}}})
    assert v["DateTime"] == "2026-10-26 14:05:09" and v["Hour"] == 14.0 and v["Weekday"] == 1.0
    assert v["Uptime"] == 90 and v["DiskFree"] == 5.0 and v["RecSize"] == 2.5 and v["RecDuration"] == 12
    assert v["Recording"] == 1.0 and v["AlarmsActive"] == 2 and v["DevicesOnline"] == 2
    assert v["dev.a.Connected"] == 1.0 and v["dev.a.Quality"] == "Good" and v["dev.a.Errors"] == 3
    assert v["dev.b.Quality"] == "Stale" and v["dev.c.Quality"] == "Stopped" and v["dev.c.Connected"] == 0.0
    assert v["dev.c.Age"] != v["dev.c.Age"]                    # NaN: never received
    tags = {t.id: t for t in ss.catalog([("a", "A"), ("b", "B"), ("c", "C")])}
    assert set(v) == set(tags)                                 # every tag gets a value
    assert ss.format_value(tags["Recording"], 1.0) == "1"
    assert ss.format_value(tags["DiskFree"], 5.0) == "5.0"
    assert ss.format_value(tags["dev.c.Age"], float("nan")) == "-"


def test_variable_prefers_a_device_over_system_tags_as_base():
    eng = VariableEngine()
    eng.channel_ids = lambda: {"System_Hour": ss.sys_key("Hour"), "PLC_T": ("d1", 0)}
    v = new_variable("formula", "Shifted")
    v["expr"] = "System_Hour + PLC_T"
    eng.set_variables([v])
    assert eng.base[v["id"]] == "d1"
