"""Aggregation tags (core/aggregation.py): state over time, periods, resets, retain."""
import datetime as dt

import pytest

from nxlogger.core import aggregation as ag


class Feed:
    """One source tag ("dev", 0) whose value / stamp the test sets."""

    def __init__(self):
        self.val, self.stamp = None, None

    def set(self, val, stamp):
        self.val, self.stamp = val, stamp


def _engine(*tags):
    f = Feed()
    e = ag.AggregationEngine()
    e.names = lambda: {"dev_T0": ("dev", 0), "dev_T1": ("dev", 1)}
    e.latest = lambda key: (f.val, f.stamp) if key == ("dev", 0) else (None, None)
    e.set_tags(list(tags))
    return e, f


def _tag(kind, name, **kw):
    a = ag.new_aggregation(kind, name)
    a.update({"source": ["dev", 0], "reset": "never", **kw})
    return a


def test_totalizer_integrates_and_pauses_on_bad_input():
    tot = _tag("totalizer", "Vol", rate_per="h")             # m³/h -> m³
    e, f = _engine(tot)
    for t, x in ((0.0, 3600.0), (1.0, 3600.0), (2.0, 3600.0)):  # 3600 m³/h for 2 s = 2 m³
        f.set(x, t); e.evaluate(now=t)
    assert e.states[tot["id"]].value == pytest.approx(2.0)
    f.set(None, 3.0); e.evaluate(now=3.0)                    # bad: paused, the gap is not added
    assert e.states[tot["id"]].quality == "Paused"
    f.set(3600.0, 10.0); e.evaluate(now=10.0)
    f.set(3600.0, 11.0); e.evaluate(now=11.0)
    assert e.states[tot["id"]].value == pytest.approx(3.0)
    neg = _tag("totalizer", "Neg", rate_per="s")
    e2, f2 = _engine(neg)
    for t in (0.0, 1.0):
        f2.set(-5.0, t); e2.evaluate(now=t)
    assert e2.states[neg["id"]].value == 0.0                 # reverse flow ignored


def test_counter_runtime_hold_change_and_statistic():
    cnt = _tag("counter", "Starts", edge="rising")
    run = _tag("runtime", "RunS", time_unit="s")
    mx = _tag("minmax", "Peak", hold="max")
    chg = _tag("change", "Delta")
    mean = _tag("statistic", "Mean3", stat="mean", win_n=3, win_unit="samples")
    blk = _tag("statistic", "Blk2", stat="max", win_n=2, win_unit="samples", win_mode="block")
    e, f = _engine(cnt, run, mx, chg, mean, blk)
    for t, x in enumerate([0, 1, 1, 0, 1, 0, 5, 2]):
        f.set(float(x), float(t)); e.evaluate(now=float(t))
    s = {a["name"]: e.states[a["id"]] for a in e.tags}
    assert s["Starts"].value == 3                            # 0->1 three times (5 counts as on)
    assert s["RunS"].value == pytest.approx(4.0)             # on during 1-3 and 4-5 and 6-7 s
    assert s["Peak"].value == 5.0 and s["Peak"].hold_t == 6.0
    assert s["Delta"].value == 2.0                           # 2 now - 0 at the start
    assert s["Mean3"].value == pytest.approx((0 + 5 + 2) / 3)
    assert s["Blk2"].value == 5.0                            # last full block: (0, 5)


def test_periods_reset_previous_and_tag_reset():
    assert ag.period_bounds({"kind": "totalizer", "reset": "never"}, 0) == (None, None)
    now = dt.datetime(2026, 10, 28, 10, 30).timestamp()
    start, nxt = ag.period_bounds({"kind": "totalizer", "reset": "day", "reset_at": "06:00"}, now)
    assert dt.datetime.fromtimestamp(start) == dt.datetime(2026, 10, 28, 6, 0)
    assert dt.datetime.fromtimestamp(nxt) == dt.datetime(2026, 10, 29, 6, 0)
    s2, n2 = ag.period_bounds({"kind": "counter", "reset": "shifts", "shifts": "22:00, 06:00, 14:00"}, now)
    assert (dt.datetime.fromtimestamp(s2).hour, dt.datetime.fromtimestamp(n2).hour) == (6, 14)
    s3, n3 = ag.period_bounds({"kind": "counter", "reset": "month", "reset_at": "00:00"}, now)
    assert dt.datetime.fromtimestamp(n3) == dt.datetime(2026, 11, 1)
    w, _ = ag.period_bounds({"kind": "counter", "reset": "week", "reset_at": "00:00"}, now)
    assert dt.datetime.fromtimestamp(w).weekday() == 0
    tot = _tag("totalizer", "Vol", rate_per="s", reset="hour", reset_at="00:00")
    e, f = _engine(tot)
    t0 = dt.datetime(2026, 10, 28, 9, 59, 58).timestamp()
    for i in range(4):                                       # crosses 10:00
        f.set(1.0, t0 + i); e.evaluate(now=t0 + i)
    st = e.states[tot["id"]]
    assert st.previous == pytest.approx(2.0) and st.value == pytest.approx(1.0)
    assert e.value(tot["id"], 1)[0] == pytest.approx(2.0)
    assert ag.formula_name(tot, True) in e.own_names()
    e.reset_now(tot["id"], now=t0 + 4)
    assert st.value == 0.0 and st.previous == pytest.approx(1.0)
    trig = _tag("counter", "C", reset="tag", reset_tag=["dev", 0])
    assert e.check(trig) == ""
    assert "reset tag" in e.check(_tag("counter", "C2", reset="tag"))
    assert "HH:MM" in e.check(_tag("counter", "C3", reset="day", reset_at="25:00"))
    assert "source" in e.check({**_tag("counter", "C4"), "source": None})


def test_retain_restores_running_values():
    tot = _tag("totalizer", "Vol", rate_per="s")
    e, f = _engine(tot)
    for t in (0.0, 1.0, 2.0):
        f.set(10.0, t); e.evaluate(now=t)
    snap = e.snapshot()
    e2, f2 = _engine(dict(tot))
    e2.restore(snap)
    assert e2.states[tot["id"]].value == pytest.approx(20.0)
    for t in (5.0, 6.0):
        f2.set(10.0, t); e2.evaluate(now=t)
    assert e2.states[tot["id"]].value == pytest.approx(30.0)
    off = dict(tot, retain=False)
    e3, _ = _engine(off)
    e3.restore(snap)
    assert tot["id"] not in e3.states
