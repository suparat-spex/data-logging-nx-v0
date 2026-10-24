"""Alarm engine (no Qt): levels, hysteresis, on-delay, data loss, active list."""
import numpy as np

from nxlogger.core import alarm_rules as ar


def engine(*levels):
    a = ar.new_alarm(("t1", 0), "Test")
    for i, lv in enumerate(levels):
        a["levels"][i] = {"enabled": True, "hyst": 0.0, "delay_s": 0.0, **lv}
    e = ar.AlarmEngine()
    e.set_alarms([a])
    events = []
    e.listeners.append(events.append)
    return e, events


def on(events):
    return [(ev["level"], ev["type"]) for ev in events if ev["event"] == "ON"]


def test_high_trips_on_block_max():
    e, ev = engine({"type": "H", "value": 1.0})
    e.feed("t1", 0, 0.0, np.array([0.0, 1.5, 0.2]))     # spike inside the block, last value low
    assert on(ev) == [(1, "H")]


def test_hysteresis_holds_alarm():
    e, ev = engine({"type": "H", "value": 1.0, "hyst": 0.5})
    e.feed("t1", 0, 0.0, np.array([1.2]))
    e.feed("t1", 0, 1.0, np.array([0.8]))               # below setpoint, inside hysteresis
    assert [x["event"] for x in ev] == ["ON"]
    e.feed("t1", 0, 2.0, np.array([0.4]))
    assert [x["event"] for x in ev] == ["ON", "OFF"]


def test_on_delay():
    e, ev = engine({"type": "L", "value": 0.0, "delay_s": 1.0})
    e.feed("t1", 0, 0.0, np.array([-1.0]))
    assert on(ev) == []
    e.feed("t1", 0, 1.5, np.array([-1.0]))
    assert on(ev) == [(1, "L")]


def test_data_loss_via_tick():
    e, ev = engine({"type": "D", "value": 2.0})
    e.tick(10.0)
    assert on(ev) == []                                  # never received: nothing lost yet
    e.feed("t1", 0, 10.0, np.array([0.0]))
    e.tick(13.0)
    assert on(ev) == [(1, "D")]


def test_cleared_alarm_leaves_active_list():
    e, ev = engine({"type": "H", "value": 1.0})
    e.feed("t1", 0, 0.0, np.array([2.0]))
    assert e.count() == 1 and len(e.active_rows()) == 1
    e.feed("t1", 0, 1.0, np.array([0.0]))
    assert e.count() == 0 and e.active_rows() == []
    assert [x["event"] for x in ev] == ["ON", "OFF"] and len(e.log) == 2


def test_profile_filter():
    p = ar.new_profile("rest")
    p.update(enabled=True, levels=[1], on=True, off=False)
    assert ar.profile_wants(p, {"level": 1, "event": "ON"})
    assert not ar.profile_wants(p, {"level": 2, "event": "ON"})
    assert not ar.profile_wants(p, {"level": 1, "event": "OFF"})
