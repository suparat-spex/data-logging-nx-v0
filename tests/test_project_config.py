"""Project model round trip, .sxl / .mqdaq (v1) migration to devices, tag CSV, config.ini."""
import json

from nxlogger import config, paths
from nxlogger.core.project import PROJECT_EXT, Project
from nxlogger.core.tags import DeviceCfg, new_device, new_tag, tag_from_row, tags_to_rows


def test_round_trip():
    p = Project.default()
    dev = DeviceCfg("PLC", "modbus_tcp")
    dev.tags.append(new_tag(dev, "T", "Boiler", area="input", addr=3, dtype="int16"))
    p.devices.append(dev)
    d = json.loads(json.dumps(p.to_dict()))
    assert d["format"] == "nxlogger" and d["version"] == 2 and PROJECT_EXT == ".nxl"
    q = Project.from_dict(d)
    assert q.to_dict() == p.to_dict()
    assert q.tag(dev.id, 0).address["addr"] == 3 and q.device(dev.id).all_groups() == ["Boiler"]


def test_v1_topics_become_wavepacket_devices():
    old = {"name": "Old", "topics": [{"name": "D", "topic": "x/y", "id": "t1", "qos": 1,
                                      "channels": [{"index": 0, "name": "CH1", "alarm_hi": 5.0},
                                                   {"index": 2, "name": "Vib", "unit": "g"}]}],
           "logging": {"folder": "C:/rec", "format": "TDMS", "keep_raw": True}}
    p = Project.from_dict(old, broker={"host": "my.broker", "port": 1884})
    dev = p.devices[0]
    assert (dev.id, dev.driver, dev.wave, dev.settings["topic"], dev.settings["host"]) == \
           ("t1", "mqtt", True, "x/y", "my.broker")
    assert [(t.index, t.kind) for t in dev.tags] == [(0, "array"), (2, "array")] and dev.tag(2).unit == "g"
    assert p.logging_profiles[0]["format"] == "CSV" and p.logging_profiles[0]["keep_raw"]   # TDMS -> CSV
    lv1 = p.alarms[0]["levels"][0]                            # alarm_hi -> Level 1 (H)
    assert len(p.alarms) == 1 and (lv1["type"], lv1["enabled"], lv1["value"]) == ("H", True, 5.0)
    assert p.alarms[0]["channel"] == ["t1", 0] and p.migrated_from == "v1"


def test_tag_csv_round_trip():
    dev = DeviceCfg("PLC", "modbus_tcp")
    dev.tags.append(new_tag(dev, "Flow", "Line 1", area="holding", addr=10, dtype="float32", order="CDAB"))
    dev.tags[0].unit, dev.tags[0].access = "m3/h", "RW"
    row = tags_to_rows(dev)[0]
    t = tag_from_row(DeviceCfg("Other", "modbus_tcp"), row)
    assert (t.name, t.group, t.unit, t.access) == ("Flow", "Line 1", "m3/h", "RW")
    assert t.address["addr"] == 10 and t.address["order"] == "CDAB" and t.address["dtype"] == "float32"


def test_default_project_has_simulator_and_view():
    p = Project.default()
    assert p.devices[0].driver == "sim" and len(p.devices[0].tags) == 4
    assert p.views and p.views[0]["layout"] == "grid" and new_device("modbus_rtu").settings["baudrate"] == "9600"


def test_config_defaults_and_override(tmp_path, monkeypatch):
    f = tmp_path / "config.ini"
    f.write_text("[mqtt]\nhost = my.broker ; comment\nport = 8883\n[app]\ntheme = light\n", encoding="utf-8")
    monkeypatch.setattr(paths, "config_file", lambda: f)
    c = config.load()
    assert c.mqtt["host"] == "my.broker" and c.mqtt_int("port", 0) == 8883
    assert c.mqtt["transport"] == "TCP"                       # from config.default.ini
    assert c.app["theme"] == "light" and c.source == str(f)


def test_resources_present():
    assert paths.resource("config.default.ini").exists()
    assert len(list(paths.resource("icons", "ui").glob("*.svg"))) >= 60
