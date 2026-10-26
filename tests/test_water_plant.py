import json
import time

from nxlogger.comms.drivers.mqtt import json_value
from nxlogger.core.tags import DeviceCfg, read_devices_file, read_tags_file
from nxlogger.sim import water_plant as wtp


def _day(seed=1):
    p = wtp.WaterPlant(seed)
    t = time.mktime((2026, 10, 26, 0, 0, 0, 0, 0, -1))
    return [p.step(60 if i else 0, t + i * 60) for i in range(24 * 60)]


def test_model_outputs_every_signal_in_range():
    rows = _day()
    assert {(a, k) for a, d in rows[0].items() for k in d} == {(a, k) for a, k, *_ in wtp.SIGNALS}
    col = lambda a, k: [r[a][k] for r in rows]                       # noqa: E731
    assert 80 < min(col("treated", "flow")) < max(col("treated", "flow")) < 400   # day profile
    assert max(col("treated", "turbidity")) < 1.0                    # within the drinking-water limit
    assert all(0.8 < pf < 1 for pf in col("power", "pf")) and all(49.8 < f < 50.2 for f in col("power", "hz"))
    kwh = col("power", "kwh")
    assert all(b >= a for a, b in zip(kwh, kwh[1:]))                 # the energy counter only counts up
    assert max(col("filters", "f1_dp")) < 1.9 and sum(col("filters", "f1_backwash")) > 0   # backwash resets dp
    assert sum(col("pumps", "p2_run")) > 0                           # pump 2 assists at peak demand


def test_messages_parse_with_the_mqtt_connector():
    v = wtp.WaterPlant(2).step(0, time.time())
    msgs = dict(wtp.messages("demo/wtp", v, 1.0, flat=False))
    assert set(msgs) == {f"demo/wtp/{a}" for a in wtp.AREAS}
    assert json_value(msgs["demo/wtp/power"].encode(), "kw") == round(v["power"]["kw"], 2)
    flat = dict(wtp.messages("demo/wtp", v, 1.0, flat=True))
    assert json_value(flat["demo/wtp/treated/ph"].encode(), "") == round(v["treated"]["ph"], 3)


def test_exports_pass_the_import_checks(tmp_path):
    dev = wtp.nx_device("demo/wtp", "localhost", 1883)
    assert len(dev.tags) == len(wtp.SIGNALS) and dev.tags[0].address == {"topic": "demo/wtp/raw", "path": "flow"}
    js, csv_ = tmp_path / "wtp.json", tmp_path / "wtp.csv"
    wtp.export_device(str(js), dev)
    wtp.export_tags(str(csv_), dev)
    (imported,) = read_devices_file(str(js))                         # Connectivity > Import...
    assert imported.driver == "mqtt" and imported.settings["host"] == "localhost" and len(imported.tags) == 49
    tags = read_tags_file(str(csv_), DeviceCfg("Any MQTT device", "mqtt"))   # Tags > Import...
    assert len(tags) == 49 and tags[34].unit == "kW" and json.loads(js.read_text(encoding="utf-8"))["devices"]


def test_window_copies_topics_without_a_broker():
    import os
    import pytest
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtGui import QGuiApplication
    from nxlogger.sim import water_plant_gui as g
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert app is not None
    w = g.WaterPlantWindow()
    w.panel.topic.setText("demo/wtp9")
    w._tick(); w._tick()                                          # not connected: values only, nothing sent
    assert w.sent == 0 and w.rows[("power", "kw")].text(g.C_VAL)
    w.table.clearSelection()
    for k in (("power", "kw"), ("power", "pf")):
        w.rows[k].setSelected(True)
    w._copy_col(g.C_TOPIC)
    assert QGuiApplication.clipboard().text() == "demo/wtp9/power"           # one topic per area (unique)
    w._copy_both()
    assert QGuiApplication.clipboard().text() == "demo/wtp9/power\tkw\ndemo/wtp9/power\tpf"
    w.mode.setCurrentIndex(w.mode.findData("flat"))
    w._copy_col(g.C_TOPIC)
    assert QGuiApplication.clipboard().text() == "demo/wtp9/power/kw\ndemo/wtp9/power/pf"
    assert '"kw"' in w.payloads["power"] or "power/kw" in w.payloads["power"]
    w.close()
