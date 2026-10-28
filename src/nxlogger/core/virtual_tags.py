"""
virtual_tags.py - Virtual tags (Tags > Virtual): values derived from other tags or set in the
project. No Qt.

A virtual tag is like a spreadsheet cell: inputs in, value out, recalculated each time - no
memory of the past (that is Aggregation), not written at runtime (that is Memory), no actions
(that is Action), no waveforms (that is Variables). Its key is ("vt:<id>", 0); numbers and
booleans go through the data path like System Tags (streams, alarms, statistics, views,
formulas as Virtual_<name>); text values are shown only (streams hold numbers).

Types
  constant    a fixed number / text / boolean (pipe area, plant name)
  formula     math on 1..n tags: inputs A, B, C ... (or full names), text allowed:
                (A + B + C) * 3.6        if(Level > 80, "High", "OK")
  conversion  one tag: linear scale (4..20 -> 0..100), table (curve), unit (°C -> °F ...)
  logic       a condition of 1..n tags -> 1 / 0:  A and B and C > 20   Mode == "Auto"
  selector    first good / average / min / max of 2..n tags (bad inputs are skipped)
  textmap     one tag -> text by values (0 Stopped, 1 Running) or ranges (< 10 Low ...)

Update: "change" (any input has a new value), "trigger" (one tag has a new value) or "every"
N ms. A bad input (no data, bad quality, stale) makes the result bad, or keeps the last good
value (on_bad = "hold") - except a selector, which skips bad inputs.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import numpy as np

from nxlogger.core import channel_math as cm

VT_PREFIX = "vt:"
FORMULA_PREFIX = "Virtual_"                         # in formulas: Virtual_<name>
TYPES = {   # key: (label, icon, what it is)
    "constant": ("Constant", "hash", "A fixed value set in the project"),
    "formula": ("Formula", "function-square", "Calculated from tags, recalculated each time"),
    "conversion": ("Conversion", "sliders", "Scale, table or unit conversion of one tag"),
    "logic": ("Logic", "check-check", "A condition of tags: 1 when true, 0 when false"),
    "selector": ("Selector", "rows", "First good, average, min or max of several tags"),
    "textmap": ("Text map", "type", "A number (or text) as state text: 0 Stopped, 1 Running ..."),
}
DTYPES = {"number": "Number", "bool": "Boolean", "text": "Text"}
UPDATES = {"change": "When any input changes", "trigger": "On a trigger tag", "every": "Every N ms"}
ON_BAD = {"bad": "Result is bad", "hold": "Keep the last good value"}
RULES = {"first": "First good input", "avg": "Average of good inputs", "min": "Minimum of good inputs",
         "max": "Maximum of good inputs"}
CONVS = {"linear": "Linear scale", "table": "Table (curve)", "unit": "Unit conversion"}
UNIT_CONV = {   # key: (label, factor, offset, unit of the result)
    "c_f": ("°C → °F", 1.8, 32.0, "°F"), "f_c": ("°F → °C", 5 / 9, -32 * 5 / 9, "°C"),
    "c_k": ("°C → K", 1.0, 273.15, "K"), "bar_psi": ("bar → psi", 14.503774, 0.0, "psi"),
    "psi_bar": ("psi → bar", 1 / 14.503774, 0.0, "bar"), "bar_kpa": ("bar → kPa", 100.0, 0.0, "kPa"),
    "kpa_bar": ("kPa → bar", 0.01, 0.0, "bar"), "m3h_ls": ("m³/h → L/s", 1 / 3.6, 0.0, "L/s"),
    "ls_m3h": ("L/s → m³/h", 3.6, 0.0, "m³/h"), "kw_hp": ("kW → hp", 1.341022, 0.0, "hp"),
    "w_kw": ("W → kW", 0.001, 0.0, "kW"), "mm_in": ("mm → in", 1 / 25.4, 0.0, "in"),
}
ALIASES = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
ONE_INPUT = {"conversion", "textmap"}                # the first input is the source
NO_INPUT = {"constant"}
CONST_EVERY_S = 1.0                                  # constants are re-sent so trends draw a line


def vt_key(vid: str) -> tuple[str, int]:
    return (VT_PREFIX + vid, 0)


def is_vt(tid) -> bool:
    return isinstance(tid, str) and tid.startswith(VT_PREFIX)


def formula_name(v: dict) -> str:
    return cm.identifier(FORMULA_PREFIX + v["name"])


def new_virtual(kind: str = "formula", name: str = "") -> dict:
    return {"id": uuid.uuid4().hex[:8], "name": name or TYPES.get(kind, ("Virtual",))[0].replace(" ", "") + "1",
            "kind": kind, "enabled": True, "group": "", "unit": "", "description": "",
            "dtype": {"logic": "bool", "textmap": "text"}.get(kind, "number"), "decimals": 2, "color": "",
            "value": 0.0, "inputs": [], "expr": "",
            "update": "change", "trigger": None, "every_ms": 1000, "on_bad": "bad",
            "conv": "linear", "in_lo": 4.0, "in_hi": 20.0, "out_lo": 0.0, "out_hi": 100.0, "clamp": True,
            "table": [[0.0, 0.0], [100.0, 100.0]], "unit_conv": "c_f",
            "rule": "first",
            "map_mode": "values", "map": [{"value": "0", "lo": None, "hi": None, "text": "Stopped", "color": ""},
                                          {"value": "1", "lo": None, "hi": None, "text": "Running", "color": ""}],
            "other": "Unknown"}


def normalize_virtual(v: dict) -> dict:
    for k, d in new_virtual(v.get("kind", "formula")).items():
        v.setdefault(k, d)
    v["inputs"] = [{"alias": str(i.get("alias", "")), "key": list(i["key"])} for i in v.get("inputs", [])
                   if isinstance(i, dict) and i.get("key")]
    return v


def describe(v: dict, label_of=lambda key: str(key)) -> str:
    """Short definition text for tables: the expression, source or value."""
    k = v.get("kind")
    ins = v.get("inputs", [])
    src = label_of(tuple(ins[0]["key"])) if ins else "(no source)"
    if k == "constant":
        return f'"{v["value"]}"' if v.get("dtype") == "text" else f"{v.get('value')}"
    if k in ("formula", "logic"):
        return v.get("expr", "")
    if k == "conversion":
        c = v.get("conv")
        if c == "linear":
            return f"{src}  {v['in_lo']:g}..{v['in_hi']:g} → {v['out_lo']:g}..{v['out_hi']:g}"
        if c == "unit":
            return f"{src}  {UNIT_CONV.get(v.get('unit_conv'), ('?',))[0]}"
        return f"{src}  table ({len(v.get('table', []))} points)"
    if k == "selector":
        return f"{RULES.get(v.get('rule'), '?').lower()}: " + ", ".join(i["alias"] for i in ins)
    if k == "textmap":
        return f"{src} → " + ", ".join(r.get("text", "") for r in v.get("map", [])[:4]) + \
            (" ..." if len(v.get("map", [])) > 4 else "")
    return ""


def format_value(v: dict, val) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if v.get("dtype") == "bool":
        return "1" if val else "0"
    if val != val:
        return ""
    return f"{val:.{int(v.get('decimals', 2))}f}"


@dataclass
class Result:
    value: object = None                             # float (number / bool 1-0), str (text) or None
    good: bool = False
    stamp: float = 0.0                               # time of the last calculation
    msg: str = ""                                    # why it is bad / held
    quality: str = "Waiting"                         # Good / Bad / Held / Error / Waiting
    color: str = ""                                  # text map: the colour of the chosen row
    picked: str = ""                                 # selector: the alias of the first good input


class VirtualEngine:
    """
    Compiles the definitions and calculates them on the GUI timer. Host callbacks:
      names()        -> {formula name: key} of the other tags (device, variables, System)
      latest(key)    -> (value or None when missing / bad / stale, stamp or None)
    Virtual inputs are read from the engine itself (in dependency order).
    """

    def __init__(self):
        self.tags: list[dict] = []
        self.errors: dict[str, str] = {}             # vid -> definition problem
        self.results: dict[str, Result] = {}
        self.order: list[dict] = []
        self._fn: dict[str, object] = {}
        self._env: dict[str, dict[str, tuple]] = {}  # vid -> {name in its formula: key}
        self._seen: dict[str, float] = {}            # vid -> newest input stamp used
        self.names = lambda: {}
        self.latest = lambda key: (None, None)

    # ---- configuration
    def own_names(self, exclude: str | None = None) -> dict[str, tuple]:
        return {formula_name(v): vt_key(v["id"]) for v in self.tags if v["id"] != exclude}

    def all_names(self, exclude: str | None = None, names: dict | None = None) -> dict[str, tuple]:
        """Formula names a definition may use: the host's (or `names`) without virtual tags, then
        the other virtual tags - never its own name."""
        out = {k: key for k, key in (self.names() if names is None else names).items() if not is_vt(key[0])}
        out.update(self.own_names(exclude))
        return out

    def by_id(self, vid: str) -> dict | None:
        return next((v for v in self.tags if v["id"] == vid), None)

    def set_tags(self, tags: list[dict]):
        self.tags = tags
        for v in tags:
            normalize_virtual(v)
        keep = {v["id"] for v in tags}
        for d in (self.results, self._seen):
            for k in [k for k in d if k not in keep]:
                del d[k]
        self._compile()

    def env_of(self, v: dict, names: dict | None = None) -> dict[str, tuple]:
        """{name: key} a definition reads: its aliases, then full names used in the formula."""
        names = self.all_names(v["id"]) if names is None else names
        env = {i["alias"]: tuple(i["key"]) for i in v.get("inputs", []) if i.get("alias")}
        if v["kind"] in ("formula", "logic"):
            for n in cm.scalar_names(v.get("expr", "")):
                if n not in env and n in names:
                    env[n] = names[n]
        return env

    def check(self, v: dict, names: dict | None = None, taken: set | None = None) -> str:
        """'' if the definition is valid, else the message (also used live by the pop-up)."""
        names = self.all_names(v["id"], names)
        if not v.get("name", "").strip():
            return "Enter a name."
        if any(c in v["name"] for c in "/\\"):
            return "A name cannot contain / or \\."
        others = taken if taken is not None else {o["name"].strip().lower() for o in self.tags if o["id"] != v["id"]}
        if v["name"].strip().lower() in others:
            return f"Another virtual tag is already called '{v['name'].strip()}'."
        if formula_name(v) in names:
            return f"'{formula_name(v)}' is already a formula name - choose another name."
        k, ins = v["kind"], v.get("inputs", [])
        aliases = [i.get("alias", "") for i in ins]
        if len(set(aliases)) != len(aliases) or any(not a.isidentifier() for a in aliases):
            return "Each input needs its own alias (A, B, C ...)."
        known = set(names.values()) | {vt_key(o["id"]) for o in self.tags}
        for i in ins:
            if tuple(i["key"]) not in known:
                return f"Input {i['alias']}: the tag no longer exists."
            if tuple(i["key"]) == vt_key(v["id"]):
                return f"Input {i['alias']}: a tag cannot use itself."
        if k == "constant":
            if v.get("dtype") != "text":
                try:
                    float(v.get("value"))
                except (TypeError, ValueError):
                    return "The value must be a number."
        elif k in ("formula", "logic"):
            env = self.env_of(v, names)
            try:
                cm.compile_scalar(v.get("expr", ""), set(env) | set(names),
                                  "A and B > 20" if k == "logic" else "(A + B) * 3.6")
            except ValueError as e:
                return str(e)
            if not env:
                return "Add an input, or use a tag by its full name."
            if vt_key(v["id"]) in env.values():
                return "A tag cannot use itself."
        elif k in ONE_INPUT:
            if len(ins) != 1:
                return "Choose the source tag."
            if k == "conversion":
                if v.get("conv") == "linear" and float(v.get("in_lo", 0)) == float(v.get("in_hi", 0)):
                    return "Input low and high must differ."
                if v.get("conv") == "table":
                    xs = [p[0] for p in v.get("table", [])]
                    if len(xs) < 2:
                        return "The table needs at least two points."
                    if len(set(xs)) != len(xs):
                        return "Each input value of the table can appear only once."
            elif not v.get("map"):
                return "Add at least one row to the text map."
        elif k == "selector" and len(ins) < 2:
            return "A selector needs at least two inputs."
        if v.get("update") == "trigger" and k not in NO_INPUT:
            if not v.get("trigger") or tuple(v["trigger"]) not in known:
                return "Choose the trigger tag."
        if v.get("update") == "every" and not 100 <= int(v.get("every_ms", 0)) <= 3_600_000:
            return "Every: 100 ms ... 1 h."
        return ""

    def deps(self, v: dict) -> list[tuple]:
        if v["kind"] in NO_INPUT:
            return []
        out = list(self.env_of(v).values())
        if v.get("update") == "trigger" and v.get("trigger"):
            out.append(tuple(v["trigger"]))
        return out

    def _compile(self):
        self.errors.clear(); self._fn.clear(); self._env.clear()
        by_key = {vt_key(v["id"]): v for v in self.tags}
        live = [v for v in self.tags if v["enabled"]]
        for v in live:
            msg = self.check(v)
            if msg:
                self.errors[v["id"]] = msg
                continue
            self._env[v["id"]] = self.env_of(v)
            if v["kind"] in ("formula", "logic"):
                names = self.all_names(v["id"])
                self._fn[v["id"]] = cm.compile_scalar(v["expr"], set(self._env[v["id"]]) | set(names))
        order, state = [], {}

        def visit(v) -> bool:                        # depth-first; False: v cannot be calculated
            vid = v["id"]
            if state.get(vid) == 2:
                return vid not in self.errors
            if state.get(vid) == 1:
                self.errors[vid] = "Circular reference between virtual tags."
                return False
            state[vid] = 1
            for d in self.deps(v):
                dv = by_key.get(d)
                if dv is None:
                    continue
                if not dv["enabled"]:
                    self.errors.setdefault(vid, f"Uses '{dv['name']}', which is disabled.")
                elif not visit(dv):
                    circ = "Circular" in self.errors.get(dv["id"], "")
                    self.errors.setdefault(vid, "Circular reference between virtual tags." if circ
                                           else f"Uses '{dv['name']}', which is invalid.")
            state[vid] = 2
            if vid in self.errors:
                return False
            order.append(v)
            return True

        for v in live:
            visit(v)
        self.order = [v for v in order if v["id"] not in self.errors]
        for v in self.tags:                           # invalid / disabled: no value
            if v["id"] in self.errors or not v["enabled"]:
                self.results[v["id"]] = Result(quality="Error" if v["enabled"] else "Disabled",
                                               msg=self.errors.get(v["id"], ""))

    def reset(self):
        self.results.clear(); self._seen.clear()

    # ---- evaluation
    def _input(self, key: tuple):
        if is_vt(key[0]):
            r = self.results.get(key[0][len(VT_PREFIX):])
            return (r.value if r is not None and r.good else None), (r.stamp if r is not None else None)
        return self.latest(key)

    def _due(self, v: dict, now: float, stamps: dict) -> bool:
        vid, last = v["id"], self.results.get(v["id"])
        if v["kind"] in NO_INPUT:
            return last is None or last.stamp == 0 or now - last.stamp >= CONST_EVERY_S
        upd = v.get("update", "change")
        if upd == "every":
            return last is None or last.stamp == 0 or now - last.stamp >= int(v.get("every_ms", 1000)) / 1000
        if upd == "trigger":
            s = stamps.get(tuple(v["trigger"]))
            return s is not None and s > self._seen.get(vid, 0.0)
        newest = max((s for s in stamps.values() if s is not None), default=None)
        if newest is not None and newest > self._seen.get(vid, 0.0):
            return True
        # nothing new: still look once a second, so an input that went stale makes the result bad
        return last is not None and last.stamp > 0 and now - last.stamp >= CONST_EVERY_S

    def evaluate(self, now: float | None = None) -> list[tuple[dict, Result]]:
        """Calculate what is due -> [(definition, result)] of the tags calculated now."""
        now = time.time() if now is None else now
        out = []
        for v in self.order:
            vid = v["id"]
            env = self._env.get(vid, {})
            vals, stamps = {}, {}
            for name, key in env.items():
                vals[name], stamps[key] = self._input(key)
            if v.get("update") == "trigger" and v.get("trigger"):
                stamps[tuple(v["trigger"])] = self._input(tuple(v["trigger"]))[1]
            if not self._due(v, now, stamps):
                continue
            self._seen[vid] = max([s for s in stamps.values() if s is not None] + [self._seen.get(vid, 0.0)])
            r = self.results[vid] = self._calc(v, vals, now)
            out.append((v, r))
        return out

    def _calc(self, v: dict, vals: dict, now: float, fn=None) -> Result:
        k = v["kind"]
        prev = self.results.get(v["id"])
        if k == "constant":
            return Result(self._cast(v, v.get("value")), True, now, quality="Good")
        if k != "selector":
            bad = [n for n, x in vals.items() if x is None]
            if bad:
                msg = f"Input {', '.join(bad)}: no good value"
                if v.get("on_bad") == "hold" and prev is not None and prev.value is not None:
                    return Result(prev.value, True, now, msg + " - last good value kept", "Held", prev.color)
                return Result(None, False, now, msg, "Bad")
        try:
            color = picked = ""
            if k in ("formula", "logic"):
                val = (fn or self._fn[v["id"]])(vals)
                if k == "logic":
                    val = 1.0 if cm.truthy(val) else 0.0
            elif k == "conversion":
                x = cm.as_number(vals[v["inputs"][0]["alias"]])
                c = v.get("conv")
                if c == "linear":
                    lo, hi, a, b = (float(v[n]) for n in ("in_lo", "in_hi", "out_lo", "out_hi"))
                    val = a + (x - lo) * (b - a) / (hi - lo)
                    if v.get("clamp"):
                        val = min(max(val, min(a, b)), max(a, b))
                elif c == "unit":
                    _l, f, o, _u = UNIT_CONV.get(v.get("unit_conv"), ("", 1.0, 0.0, ""))
                    val = x * f + o
                else:
                    pts = sorted((float(p[0]), float(p[1])) for p in v["table"])
                    val = float(np.interp(x, [p[0] for p in pts], [p[1] for p in pts]))
            elif k == "selector":
                good = [(i["alias"], vals[i["alias"]]) for i in v["inputs"] if vals.get(i["alias"]) is not None]
                if not good:
                    return Result(None, False, now, "No input has a good value", "Bad")
                nums = [cm.as_number(x) for _a, x in good]
                rule = v.get("rule", "first")
                if rule == "first":
                    picked, val = good[0][0], nums[0]
                else:
                    val = {"avg": sum(nums) / len(nums), "min": min(nums), "max": max(nums)}[rule]
            else:                                    # textmap
                x = vals[v["inputs"][0]["alias"]]
                val, color = self._map(v, x)
            val = self._cast(v, val)
        except cm.ScalarError as e:
            return Result(None, False, now, str(e), "Error")
        except (ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
            return Result(None, False, now, f"Error: {e}", "Error")
        if isinstance(val, float) and val != val:
            return Result(None, False, now, "Not a number (NaN)", "Bad")
        q = "Good" + (f" ({picked})" if picked else "")
        return Result(val, True, now, "", q, color, picked)

    @staticmethod
    def _map(v: dict, x) -> tuple[str, str]:
        for row in v.get("map", []):
            if v.get("map_mode") == "ranges":
                if isinstance(x, str):
                    raise cm.ScalarError("Ranges need a number input")
                lo, hi = row.get("lo"), row.get("hi")
                if (lo is None or x >= float(lo)) and (hi is None or x < float(hi)):
                    return str(row.get("text", "")), row.get("color", "")
            else:
                want = str(row.get("value", "")).strip()
                if isinstance(x, str):
                    hit = x == want
                else:
                    try:
                        hit = float(want) == float(x)
                    except ValueError:
                        hit = False
                if hit:
                    return str(row.get("text", "")), row.get("color", "")
        return str(v.get("other", "")), ""

    @staticmethod
    def _cast(v: dict, val):
        dt = v.get("dtype", "number")
        if dt == "text":
            return cm.as_text(val) if not isinstance(val, bool) else ("true" if val else "false")
        if isinstance(val, str):
            if v.get("kind") == "constant":
                return cm.as_number(val)
            raise cm.ScalarError("The result is text - set the data type to Text")
        if dt == "bool":
            return 1.0 if val else 0.0
        return float(val)

    def preview(self, v: dict, names: dict) -> tuple[str, str]:
        """(value text, message) of a definition being edited, with the live input values -
        for the pop-up. Does not touch the engine's results."""
        msg = self.check(v, names)
        if msg:
            return "", msg
        names = self.all_names(v["id"], names)
        env = self.env_of(v, names)
        vals = {n: self._input(key)[0] for n, key in env.items()}
        fn = cm.compile_scalar(v["expr"], set(env) | set(names)) if v["kind"] in ("formula", "logic") else None
        r = self._calc(v, vals, time.time(), fn)
        return format_value(v, r.value), ("" if r.good and r.quality.startswith("Good") else r.msg)
