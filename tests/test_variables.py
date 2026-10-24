"""Variables (no Qt): formulas over channels, statistics, signal processing, errors."""
import numpy as np
import pytest

from nxlogger.core import variables as vr
from nxlogger.core.variables import VarBlock

DT = 0.001


def blk(y, seq=0, idx=0):
    y = np.atleast_2d(np.asarray(y, dtype=float))
    return VarBlock(seq, idx, 100.0 + idx * DT, DT, y, channels=list(range(y.shape[0])))


def engine(*defs):
    e = vr.VariableEngine()
    e.channel_ids = lambda: {"Sim_CH1": ("t1", 0), "Sim_CH2": ("t1", 1), "Other_CH1": ("t2", 0)}
    e.latest = lambda key: {("t2", 0): 10.0}.get(key)
    vs = []
    for kind, extra in defs:
        v = vr.new_variable(kind, extra.pop("name"))
        v.update(extra)
        vs.append(v)
    e.set_variables(vs)
    return e, vs


def test_formula_and_chained_variable():
    e, (d, s) = engine(("formula", {"name": "Diff", "expr": "Sim_CH1 - Sim_CH2"}),
                       ("formula", {"name": "Scaled", "expr": "Diff * 2 + Other_CH1"}))
    assert not e.errors and e.base[d["id"]] == "t1" and e.base[s["id"]] == "t1"
    out = e.compute("t1", blk([[5, 6], [1, 1]]))
    assert [v["name"] for v, _ in out] == ["Diff", "Scaled"]       # dependency order
    np.testing.assert_allclose(out[1][1].y[0], [18, 20])           # other topic = latest value
    assert e.compute("t2", blk([[1, 2]])) == []


def test_errors_cycle_unknown_and_name_clash():
    e, (a, b, c, d) = engine(("formula", {"name": "A", "expr": "B + 1"}),
                             ("formula", {"name": "B", "expr": "A + 1"}),
                             ("formula", {"name": "C", "expr": "nope * 2"}),
                             ("formula", {"name": "Sim_CH1", "expr": "Sim_CH2"}))
    assert "Circular" in e.errors[a["id"]] or "Circular" in e.errors[b["id"]]
    assert "Unknown name" in e.errors[c["id"]]
    assert "channel identifier" in e.errors[d["id"]]


def test_statistic_per_block():
    e, (v,) = engine(("statistic", {"name": "Rms", "source": ["t1", 0], "stat": "rms", "window_s": 0}))
    out = e.compute("t1", blk([[3, -3, 3, -3]], seq=7))
    (_, vb), = out
    assert vb.n == 1 and vb.y[0, 0] == pytest.approx(3.0) and vb.dt == pytest.approx(4 * DT)


def test_integrate_and_differentiate_continue_across_blocks():
    t = np.arange(2000) * DT
    x = np.cos(2 * np.pi * 5 * t)
    for op, expect in (("integrate", np.sin(2 * np.pi * 5 * t) / (2 * np.pi * 5)),
                       ("differentiate", -2 * np.pi * 5 * np.sin(2 * np.pi * 5 * t))):
        p = vr.Processor(op, 0.0)
        y = np.concatenate([p(x[i:i + 100], DT) for i in range(0, x.size, 100)])
        tail = slice(500, None)
        assert np.max(np.abs(y[tail] - expect[tail])) < 0.02 * np.max(np.abs(expect))


def test_moving_average_and_filters():
    x = np.ones(1000)
    p = vr.Processor("movavg", 10)
    y = np.concatenate([p(x[i:i + 37], DT) for i in range(0, 1000, 37)])
    np.testing.assert_allclose(y, 1.0)
    hp = vr.Processor("highpass", 5.0)
    assert abs(hp(np.full(500, 3.0), DT)[-1]) < 1e-9                # DC removed, no start step
    lp = vr.Processor("lowpass", 1.0)
    fast = np.sin(2 * np.pi * 200 * np.arange(2000) * DT)
    assert np.std(lp(fast, DT)[500:]) < 0.01


def test_iir1_matches_loop():
    x = np.random.default_rng(0).normal(size=3000)
    a, b, y0 = 0.3, 0.7, 2.0
    ref, y = [], y0
    for v in x:
        y = a * y + b * v
        ref.append(y)
    np.testing.assert_allclose(vr.iir1(x, a, b, y0), ref, rtol=1e-9, atol=1e-9)
