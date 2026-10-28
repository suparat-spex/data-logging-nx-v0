"""Virtual tags (core/virtual_tags.py) and the scalar formulas they use (channel_math.compile_scalar)."""
import pytest

from nxlogger.core import channel_math as cm
from nxlogger.core import virtual_tags as vt


def test_scalar_formulas_numbers_text_and_logic():
    f = cm.compile_scalar("(A + B + C) * 3.6", {"A", "B", "C"})
    assert f({"A": 1.0, "B": 2.0, "C": 3.0}) == pytest.approx(21.6)
    g = cm.compile_scalar('if(Level > 80, "High", "OK")', {"Level"})
    assert g({"Level": 90.0}) == "High" and g({"Level": 10.0}) == "OK"
    h = cm.compile_scalar('Mode == "Auto" and Flow > 5', {"Mode", "Flow"})
    assert h({"Mode": "Auto", "Flow": 6.0}) is True and h({"Mode": "Hand", "Flow": 6.0}) is False
    assert cm.compile_scalar("S in (1, 2)", {"S"})({"S": 2.0}) is True
    assert cm.compile_scalar("0 < x < 1", {"x"})({"x": 0.5}) is True             # chained is fine here
    assert cm.compile_scalar('num("12.5") + 1', set())({}) == 13.5
    assert cm.compile_scalar('"Line " + text(N)', {"N"})({"N": 3.0}) == "Line 3"
    assert cm.compile_scalar("bit(W, 3)", {"W"})({"W": 8.0}) == 1.0
    with pytest.raises(cm.ScalarError):                                         # no silent conversion
        cm.compile_scalar('S == "1"', {"S"})({"S": 1.0})
    with pytest.raises(cm.ScalarError):
        cm.compile_scalar('"Auto" + 1', set())({})
    with pytest.raises(cm.ScalarError):
        cm.compile_scalar("A / B", {"A", "B"})({"A": 1.0, "B": 0.0})


@pytest.mark.parametrize("bad", ["__import__('os')", "A.real", "[A]", "open('x')", "lambda: 1", "A[0]", "Q + 1"])
def test_scalar_formulas_are_whitelisted(bad):
    with pytest.raises(ValueError):
        cm.compile_scalar(bad, {"A"})


def _engine(values):
    """Engine over device tags dev_T0.. with the values (and stamps) in `values`: {idx: (value, stamp)}."""
    e = vt.VirtualEngine()
    e.names = lambda: {f"dev_T{i}": ("dev", i) for i in values}
    e.latest = lambda key: values.get(key[1], (None, None))
    return e


def _tag(kind, name, **kw):
    v = vt.new_virtual(kind, name)
    v.update(kw)
    return v


def test_types_and_update_on_input_change():
    vals = {0: (12.1, 1.0), 1: (8.4, 1.0), 2: (3.05, 1.0)}
    e = _engine(vals)
    ins = [{"alias": a, "key": ["dev", i]} for i, a in enumerate("ABC")]
    total = _tag("formula", "Total", inputs=ins, expr="(A + B + C) * 3.6")
    area = _tag("constant", "Area", value=0.5)
    uses = _tag("formula", "Flow", inputs=[], expr="dev_T0 * Virtual_Area")      # full names
    lvl = _tag("conversion", "Level", inputs=[{"alias": "A", "key": ["dev", 0]}],
               in_lo=4, in_hi=20, out_lo=0, out_hi=100)
    run = _tag("logic", "Both", inputs=ins[:2], expr="A > 10 and B > 10")
    best = _tag("selector", "Best", inputs=ins, rule="max")
    state = _tag("textmap", "State", inputs=[{"alias": "A", "key": ["dev", 2]}], map_mode="ranges",
                 map=[{"lo": None, "hi": 3, "text": "Low"}, {"lo": 3, "hi": None, "text": "OK", "color": "#0f0"}])
    e.set_tags([total, uses, area, lvl, run, best, state])
    assert e.errors == {}
    assert [v["name"] for v in e.order].index("Area") < [v["name"] for v in e.order].index("Flow")
    done = {v["name"]: r for v, r in e.evaluate(now=10.0)}
    assert done["Total"].value == pytest.approx(84.78) and done["Flow"].value == pytest.approx(6.05)
    assert done["Level"].value == pytest.approx(50.625) and done["Both"].value == 0.0
    assert done["Best"].value == 12.1 and done["State"].value == "OK" and done["State"].color == "#0f0"
    assert {v["name"] for v, _ in e.evaluate(now=10.1)} == set()                # nothing new: nothing due
    vals[1] = (11.0, 2.0)                                                       # B changed
    assert {v["name"] for v, _ in e.evaluate(now=10.2)} >= {"Total", "Both", "Best"}
    assert e.results[run["id"]].value == 1.0


def test_bad_inputs_hold_selector_and_errors():
    vals = {0: (5.0, 1.0), 1: (None, 1.0)}
    e = _engine(vals)
    a, b = {"alias": "A", "key": ["dev", 0]}, {"alias": "B", "key": ["dev", 1]}
    f = _tag("formula", "Sum", inputs=[a, b], expr="A + B")
    first = _tag("selector", "First", inputs=[b, a])                            # B bad: skipped
    e.set_tags([f, first])
    e.evaluate(now=1.0)
    assert not e.results[f["id"]].good and "B" in e.results[f["id"]].msg
    assert e.results[first["id"]].value == 5.0 and e.results[first["id"]].quality == "Good (A)"
    vals[1] = (1.0, 2.0); e.evaluate(now=2.0)
    assert e.results[f["id"]].value == 6.0
    f["on_bad"] = "hold"; e.set_tags([f, first])
    vals[1] = (None, 3.0); e.evaluate(now=3.0)
    assert e.results[f["id"]].value == 6.0 and e.results[f["id"]].quality == "Held"
    loop1 = _tag("formula", "L1", expr="Virtual_L2 + 1")
    loop2 = _tag("formula", "L2", expr="Virtual_L1 + 1")
    e.set_tags([loop1, loop2])
    assert "Circular" in e.errors[loop1["id"]] and "Circular" in e.errors[loop2["id"]]
    assert "Unknown name" in e.check(_tag("formula", "X", expr="Nope + 1"))
    assert e.check(_tag("constant", "N", value="abc")) == "The value must be a number."


def test_text_results_and_text_inputs():
    vals = {0: ("Auto", 1.0), 1: (2.0, 1.0)}
    e = _engine(vals)
    m = _tag("logic", "IsAuto", inputs=[{"alias": "M", "key": ["dev", 0]}], expr='M == "Auto"')
    t = _tag("textmap", "Pump", inputs=[{"alias": "S", "key": ["dev", 1]}],
             map=[{"value": "1", "text": "Running"}, {"value": "2", "text": "Fault"}], other="Unknown")
    txt = _tag("formula", "Msg", dtype="text", inputs=[{"alias": "S", "key": ["dev", 1]}],
               expr='if(S == 2, "Check pump", "OK")')
    wrong = _tag("formula", "Wrong", inputs=[{"alias": "S", "key": ["dev", 1]}], expr='"x"')   # text in a Number
    e.set_tags([m, t, txt, wrong])
    e.evaluate(now=1.0)
    r = {v["name"]: e.results[v["id"]] for v in (m, t, txt, wrong)}
    assert r["IsAuto"].value == 1.0 and r["Pump"].value == "Fault" and r["Msg"].value == "Check pump"
    assert r["Wrong"].quality == "Error" and "Text" in r["Wrong"].msg
    assert vt.format_value(m, 1.0) == "1" and vt.format_value(t, "Fault") == "Fault"
