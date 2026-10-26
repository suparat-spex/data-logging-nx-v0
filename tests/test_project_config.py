"""Project model round trip, .sxl / .mqdaq (v1) migration to devices, tag CSV, config.ini."""
import json

from nxlogger import config, paths
from nxlogger.core.project import PROJECT_EXT, Project
from nxlogger.core.tags import (DeviceCfg, DeviceFileError, read_devices_file, device_from_row, devices_from_json, devices_to_json, devices_to_rows,
                                new_device, new_tag, tag_from_row, tags_to_rows)


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


def test_device_export_import():
    plc = DeviceCfg("PLC", "modbus_tcp", settings={"host": "10.0.0.5", "port": 1502}, scan_ms=500)
    plc.tags.append(new_tag(plc, "Flow", "Line 1", addr=10))
    mq = DeviceCfg("Broker", "mqtt", enabled=False, settings={"password": "s3cret", "username": "u"})
    data = json.loads(json.dumps(devices_to_json([plc, mq])))       # through a file
    assert "password" not in data["devices"][1]["settings"]          # never exported
    a, b = devices_from_json(data)
    assert (a.name, a.settings["host"], a.scan_ms, a.tags[0].address["addr"]) == ("PLC", "10.0.0.5", 500, 10)
    assert not b.enabled and b.settings["password"] == "" and b.settings["username"] == "u"
    assert [d.name for d in devices_from_json(Project("x", devices=[plc]).to_dict())] == ["PLC"]  # a .nxl
    rows = devices_to_rows([plc, mq])
    assert "password" not in rows[1]["settings"]
    c = device_from_row({**rows[0], "driver": "Modbus TCP"})                     # label works too
    assert (c.driver, c.settings["host"], c.settings["port"], c.scan_ms, c.tags) == \
        ("modbus_tcp", "10.0.0.5", 1502, 500, [])
    assert device_from_row({"name": "S", "driver": "sim", "enabled": "0"}).enabled is False
    for bad in ({"name": "", "driver": "sim"}, {"name": "X", "driver": "nope"}):
        try:
            device_from_row(bad)
            raise AssertionError("bad row accepted")
        except ValueError:
            pass


def test_device_file_validation(tmp_path):
    good = tmp_path / "d.csv"
    good.write_text("name,driver,enabled,scan_ms,description,settings\nPLC,Modbus TCP,1,500,,host=1.2.3.4;port=502\n",
                    encoding="utf-8")
    assert [d.settings["host"] for d in read_devices_file(str(good))] == ["1.2.3.4"]
    cases = {"tags.csv": "device,group,name,unit\nPLC,,T,C\n",                     # a tag list, not devices
             "dup.csv": "name,driver\nA,sim\nA,sim\n",
             "num.csv": "name,driver,scan_ms\nA,sim,fast\n",
             "set.csv": "name,driver,settings\nA,sim,host=x\n",
             "x.json": '{"format": "other", "devices": []}',
             "broken.json": "{not json",
             "empty.json": '{"format": "nxlogger-devices", "devices": []}',
             "d.txt": "name,driver\nA,sim\n"}
    for name, text in cases.items():
        f = tmp_path / name
        f.write_text(text, encoding="utf-8")
        try:
            read_devices_file(str(f))
            raise AssertionError(f"{name} accepted")
        except DeviceFileError as e:
            assert e.problems, name


def test_default_project_has_simulator_and_view():
    p = Project.default()
    assert p.devices[0].driver == "sim" and len(p.devices[0].tags) == 4
    assert p.views and p.views[0]["layout"] == "free" and new_device("modbus_rtu").settings["baudrate"] == "9600"


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


def test_read_tags_file(tmp_path):
    import csv as _csv
    from nxlogger.core.tags import CSV_COLS, DeviceFileError, read_tags_file
    plc = DeviceCfg("PLC", "modbus_tcp")
    plc.tags.append(new_tag(plc, "Flow", "Line 1", addr=10))
    plc.tags.append(new_tag(plc, "Level", "", addr=12))
    other = DeviceCfg("Boiler", "modbus_tcp")
    other.tags.append(new_tag(other, "Steam", "", addr=1))
    good = tmp_path / "tags.csv"
    with open(good, "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.DictWriter(f, CSV_COLS); w.writeheader()
        w.writerows(tags_to_rows(plc) + tags_to_rows(other))
    tags = read_tags_file(str(good), plc)                     # only this device's rows of a multi-device list
    assert [t.name for t in tags] == ["Flow", "Level"] and tags[0].address["addr"] == 10
    bad = tmp_path / "bad.csv"
    bad.write_text("name,address,access\nA,addr=x,R\n,addr=1,R\nB,nope=1,W\nB,addr=2,R\n", encoding="utf-8")
    try:
        read_tags_file(str(bad), plc)
        raise AssertionError("bad file accepted")
    except DeviceFileError as e:
        text = "\n".join(e.problems)
        assert "line 2: address addr" in text and "line 3: no name" in text
        assert "unknown address key 'nope'" in text and "access must be R or RW" in text
    js = tmp_path / "dev.json"
    js.write_text(json.dumps(devices_to_json([plc])), encoding="utf-8")
    assert [t.name for t in read_tags_file(str(js), DeviceCfg("PLC", "modbus_tcp"))] == ["Flow", "Level"]
    try:
        read_tags_file(str(js), DeviceCfg("PLC", "opcua"))
        raise AssertionError("wrong connector accepted")
    except DeviceFileError as e:
        assert "addresses do not fit" in e.problems[0]
