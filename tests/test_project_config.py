"""Project model round trip + MQDAQ v5/v6 file migration, and config.ini loading."""
import json

from nxlogger import config, paths
from nxlogger.core.project import ChannelCfg, Project, TopicCfg


def test_round_trip():
    p = Project.default()
    p.topics.append(TopicCfg("Dev", "a/b", channels=[ChannelCfg(0, "T", "°C")]))
    q = Project.from_dict(json.loads(json.dumps(p.to_dict())))
    assert q.to_dict() == p.to_dict()


def test_v5_file_migrates_limits_and_format():
    old = {"name": "Old", "topics": [{"name": "D", "topic": "x", "id": "t1",
                                      "channels": [{"index": 0, "name": "CH1", "alarm_hi": 5.0}]}],
           "logging": {"folder": "C:/rec", "format": "TDMS", "keep_raw": True}}
    p = Project.from_dict(old)
    assert p.logging_profiles[0]["format"] == "TDMS" and p.logging_profiles[0]["keep_raw"]
    lv1 = p.alarms[0]["levels"][0]                            # alarm_hi -> Level 1 (H)
    assert len(p.alarms) == 1 and (lv1["type"], lv1["enabled"], lv1["value"]) == ("H", True, 5.0)


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
    assert len(list(paths.resource("icons", "ui").glob("*.svg"))) >= 40
