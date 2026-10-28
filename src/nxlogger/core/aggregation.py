"""
aggregation.py - Aggregation tags (Tags > Aggregation): values that remember over time. No Qt.

Unlike a Virtual tag (inputs now -> value now), an Aggregation tag keeps state: a sum, a count,
a time, a window of samples. It is reset by its period (hour / day / week / month at a time,
shifts, a tag becoming 1, or manually) and can keep the last period's value as <name>.Previous.

Types
  statistic   mean / min / max / RMS / SD / range / count / sum of one tag over a window
              (s / min / h / samples; moving, or block = one value per full window)
  totalizer   ∫ x dt of a rate (flow m³/h -> m³, power kW -> kWh), trapezoid between samples;
              a bad / stale input pauses it (the gap is not added)
  counter     events of a 0 / 1 tag: rising, falling or both edges, or every value change
  runtime     time while a tag is true (non-zero), in s / min / h
  minmax      lowest or highest value since the reset, and when it happened
  change      now minus the value at the start of the period, or that as a rate per min / h

Keys: ("agg:<id>", 0) the value, ("agg:<id>", 1) the previous period's value (keep_prev).
In formulas: Aggregation_<name> and Aggregation_<name>_Previous. Numbers only.
The running state can be kept across restarts (retain): snapshot() / restore() - the window
writes it to the user data folder, never into the project file.
"""
from __future__ import annotations

import datetime as _dt
import math
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from nxlogger.core import channel_math as cm

AGG_PREFIX = "agg:"
FORMULA_PREFIX = "Aggregation_"
TYPES = {   # key: (label, icon, what it is)
    "statistic": ("Statistic", "sigma", "Mean, min, max, RMS ... of a tag over a window"),
    "totalizer": ("Totalizer", "chart-column", "Adds up a rate over time: volume from flow, energy from power"),
    "counter": ("Counter", "hash", "Counts events: starts, trips, changes of a tag"),
    "runtime": ("Runtime", "activity", "Time while a tag is true: run hours"),
    "minmax": ("Min / Max hold", "gauge", "Lowest or highest value since the reset"),
    "change": ("Change", "chart", "Difference since the start of the period, or its rate"),
}
STATS = {"mean": "Mean", "min": "Minimum", "max": "Maximum", "rms": "RMS", "sd": "Standard deviation",
         "range": "Range (max - min)", "count": "Number of samples", "sum": "Sum"}
WIN_UNITS = {"s": ("seconds", 1.0), "min": ("minutes", 60.0), "h": ("hours", 3600.0), "samples": ("samples", 0.0)}
RATE_PER = {"s": ("per second", 1.0), "min": ("per minute", 60.0), "h": ("per hour", 3600.0),
            "day": ("per day", 86400.0)}
TIME_UNITS = {"s": ("seconds", 1.0), "min": ("minutes", 60.0), "h": ("hours", 3600.0)}
EDGES = {"rising": "Rising edge (0 → 1)", "falling": "Falling edge (1 → 0)", "both": "Both edges",
         "change": "Every value change"}
RESETS = {"never": "Never (manual only)", "hour": "Every hour", "day": "Every day", "week": "Every week (Monday)",
          "month": "Every month (1st)", "shifts": "At shift changes", "tag": "When a tag becomes 1"}
CHANGE = {"diff": "Difference since the period start", "rate": "Rate of change"}
DEFAULT_SHIFTS = "06:00, 14:00, 22:00"
SEND_EVERY_S = 1.0                                   # unchanged values are re-sent so trends draw a line


def agg_key(aid: str, idx: int = 0) -> tuple[str, int]:
    return (AGG_PREFIX + aid, idx)


def is_agg(tid) -> bool:
    return isinstance(tid, str) and tid.startswith(AGG_PREFIX)


def formula_name(a: dict, previous: bool = False) -> str:
    return cm.identifier(FORMULA_PREFIX + a["name"] + ("_Previous" if previous else ""))


def new_aggregation(kind: str = "totalizer", name: str = "") -> dict:
    return {"id": uuid.uuid4().hex[:8], "name": name or TYPES.get(kind, ("Agg",))[0].split()[0] + "1",
            "kind": kind, "enabled": True, "group": "", "unit": "", "description": "", "decimals": 2, "color": "",
            "source": None,
            "rate_per": "h", "factor": 1.0, "skip_negative": True,
            "edge": "rising", "time_unit": "h", "hold": "max", "change": "diff", "change_per": "h",
            "stat": "mean", "win_n": 1.0, "win_unit": "h", "win_mode": "moving",
            "reset": "never" if kind in ("runtime", "statistic") else "day", "reset_at": "06:00",
            "shifts": DEFAULT_SHIFTS, "reset_tag": None, "keep_prev": True, "retain": True}


def normalize_aggregation(a: dict) -> dict:
    for k, d in new_aggregation(a.get("kind", "totalizer")).items():
        a.setdefault(k, d)
    return a


def parse_hhmm(text: str) -> tuple[int, int] | None:
    try:
        h, m = (int(x) for x in str(text).strip().split(":"))
    except (TypeError, ValueError):
        return None
    return (h, m) if 0 <= h < 24 and 0 <= m < 60 else None


def parse_shifts(text: str) -> list[tuple[int, int]] | None:
    out = [parse_hhmm(x) for x in str(text).replace(";", ",").split(",") if x.strip()]
    return sorted(set(out)) if out and None not in out else None


def has_period(a: dict) -> bool:
    return a["kind"] != "statistic"


def period_bounds(a: dict, now: float) -> tuple[float | None, float | None]:
    """(start of the current period, next reset) in Unix time; None where there is none."""
    mode = a.get("reset", "never")
    if not has_period(a) or mode in ("never", "tag"):
        return None, None
    t = _dt.datetime.fromtimestamp(now)
    hm = parse_hhmm(a.get("reset_at", "00:00")) or (0, 0)
    if mode == "hour":
        start = t.replace(minute=hm[1], second=0, microsecond=0)
        start = start if start <= t else start - _dt.timedelta(hours=1)
        return start.timestamp(), (start + _dt.timedelta(hours=1)).timestamp()
    if mode == "shifts":
        times = parse_shifts(a.get("shifts", DEFAULT_SHIFTS)) or [(0, 0)]
        day = t.replace(hour=0, minute=0, second=0, microsecond=0)
        marks = [day + _dt.timedelta(days=d, hours=h, minutes=m) for d in (-1, 0, 1) for h, m in times]
        start = max(x for x in marks if x <= t)
        return start.timestamp(), min(x for x in marks if x > t).timestamp()
    at = t.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    if mode == "day":
        start = at if at <= t else at - _dt.timedelta(days=1)
        return start.timestamp(), (start + _dt.timedelta(days=1)).timestamp()
    if mode == "week":
        start = at - _dt.timedelta(days=t.weekday())
        start = start if start <= t else start - _dt.timedelta(days=7)
        return start.timestamp(), (start + _dt.timedelta(days=7)).timestamp()
    if mode == "month":
        start = at.replace(day=1)
        if start > t:
            start = (start - _dt.timedelta(days=1)).replace(day=1)
        nxt = (start.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
        return start.timestamp(), nxt.timestamp()
    return None, None


def describe(a: dict, label_of=lambda key: str(key)) -> str:
    """Short definition text for tables."""
    src = label_of(tuple(a["source"])) if a.get("source") else "(no source)"
    k = a["kind"]
    if k == "statistic":
        n, u = a.get("win_n", 1), a.get("win_unit", "h")
        return f"{STATS.get(a.get('stat'), '?').lower()} of {src}, {float(n):g} {WIN_UNITS[u][0]}" + \
            (" block" if a.get("win_mode") == "block" else "")
    if k == "totalizer":
        return f"∫ {src} dt ({RATE_PER[a.get('rate_per', 'h')][0]})" + \
            (f" × {float(a.get('factor', 1)):g}" if float(a.get("factor", 1)) != 1 else "")
    if k == "counter":
        return f"{EDGES[a.get('edge', 'rising')].split(' (')[0].lower()} of {src}"
    if k == "runtime":
        return f"while {src} is true"
    if k == "minmax":
        return f"{'max' if a.get('hold') == 'max' else 'min'} of {src}"
    return f"{'rate' if a.get('change') == 'rate' else 'change'} of {src}"


def describe_period(a: dict) -> str:
    if not has_period(a):
        return "-"
    m = a.get("reset", "never")
    if m in ("day", "week", "month"):
        return f"{RESETS[m].split(' (')[0].replace('Every ', '').capitalize()} {a.get('reset_at', '')}"
    if m == "hour":
        hm = parse_hhmm(a.get("reset_at", "00:00")) or (0, 0)
        return f"Hour :{hm[1]:02d}"
    if m == "shifts":
        return "Shifts " + a.get("shifts", "")
    return {"never": "Never", "tag": "On tag"}[m]


@dataclass
class AggState:
    value: float = 0.0
    previous: float | None = None
    start: float = 0.0                               # period start (or last reset)
    next: float | None = None                        # next reset
    last_t: float = float("-inf")                  # stamp of the last sample used (none yet)
    last_x: float | None = None                      # last good sample (None after a bad one)
    last_tick: float = 0.0
    on: bool = False                                 # runtime / counter: last state
    hold_t: float = 0.0                              # min / max: when
    x0: float | None = None                          # change: the value at the period start
    trig: bool = False                               # reset tag: last state
    buf: deque = field(default_factory=deque)        # statistic: (stamp, value)
    has: bool = False                                # min / max / statistic / change: a value exists
    sent: float = 0.0                                # last time it was handed to the data path
    sent_v: float | None = None
    quality: str = "Waiting"
    msg: str = ""

    def snapshot(self) -> dict:
        return {k: getattr(self, k) for k in ("value", "previous", "start", "hold_t", "x0", "has")}


class AggregationEngine:
    """
    Compiles the definitions and updates them on the GUI timer. Host callbacks:
      names()        -> {formula name: key} of the tags it may read
      latest(key)    -> (value or None when missing / bad / stale, stamp or None)
    """

    def __init__(self):
        self.tags: list[dict] = []
        self.errors: dict[str, str] = {}
        self.states: dict[str, AggState] = {}
        self.order: list[dict] = []
        self.names = lambda: {}
        self.latest = lambda key: (None, None)

    def by_id(self, aid: str) -> dict | None:
        return next((a for a in self.tags if a["id"] == aid), None)

    def own_names(self) -> dict[str, tuple]:
        out = {}
        for a in self.tags:
            out[formula_name(a)] = agg_key(a["id"])
            if a.get("keep_prev") and has_period(a):
                out[formula_name(a, True)] = agg_key(a["id"], 1)
        return out

    def set_tags(self, tags: list[dict]):
        self.tags = tags
        for a in tags:
            normalize_aggregation(a)
        for k in [k for k in self.states if k not in {a["id"] for a in tags}]:
            del self.states[k]
        self.errors.clear()
        known = set(self.names().values())
        for a in tags:
            if a["enabled"]:
                msg = self.check(a, known=known)
                if msg:
                    self.errors[a["id"]] = msg
        self.order = [a for a in tags if a["enabled"] and a["id"] not in self.errors]
        now = time.time()
        for a in self.order:                           # a changed period: the next reset follows it
            st = self.states.get(a["id"])
            if st is not None:
                st.next = period_bounds(a, now)[1]

    def check(self, a: dict, names: dict | None = None, taken: set | None = None, known: set | None = None) -> str:
        """'' if the definition is valid, else the message (also used live by the pop-up)."""
        names = self.names() if names is None else names
        known = set(names.values()) if known is None else known
        if not a.get("name", "").strip():
            return "Enter a name."
        if any(c in a["name"] for c in "/\\."):
            return "A name cannot contain / \\ or a dot."
        others = taken if taken is not None else {o["name"].strip().lower() for o in self.tags if o["id"] != a["id"]}
        if a["name"].strip().lower() in others:
            return f"Another aggregation tag is already called '{a['name'].strip()}'."
        own = {formula_name(o) for o in self.tags if o["id"] != a["id"]}
        if formula_name(a) in own or formula_name(a) in {k for k, v in names.items() if not is_agg(v[0])}:
            return f"'{formula_name(a)}' is already a formula name - choose another name."
        src = a.get("source")
        if not src:
            return "Choose the source tag."
        if tuple(src) not in known:
            return "The source tag no longer exists."
        if is_agg(src[0]) and src[0] == AGG_PREFIX + a["id"]:
            return "A tag cannot use itself."
        k = a["kind"]
        if k == "statistic":
            try:
                n = float(a.get("win_n", 0))
            except (TypeError, ValueError):
                n = 0
            if n <= 0:
                return "The window must be longer than 0."
        if k == "totalizer" and float(a.get("factor", 1)) == 0:
            return "The factor cannot be 0."
        if has_period(a):
            m = a.get("reset", "never")
            if m in ("hour", "day", "week", "month") and parse_hhmm(a.get("reset_at", "")) is None:
                return "Reset time: HH:MM, e.g. 06:00."
            if m == "shifts" and parse_shifts(a.get("shifts", "")) is None:
                return "Shifts: times as HH:MM, e.g. 06:00, 14:00, 22:00."
            if m == "tag" and (not a.get("reset_tag") or tuple(a["reset_tag"]) not in known):
                return "Choose the reset tag."
        return ""

    # ---- state
    def reset(self):
        self.states.clear()

    def snapshot(self) -> dict:
        """The running values to keep across a restart (retain)."""
        return {a["id"]: {"kind": a["kind"], **self.states[a["id"]].snapshot()}
                for a in self.tags if a.get("retain") and a["id"] in self.states}

    def restore(self, data: dict):
        for a in self.tags:
            s = (data or {}).get(a["id"])
            if not a.get("retain") or not isinstance(s, dict) or s.get("kind") != a["kind"]:
                continue
            st = self.states[a["id"]] = AggState()
            for k in ("value", "previous", "start", "hold_t", "x0", "has"):
                if k in s:
                    setattr(st, k, s[k])
            st.next = period_bounds(a, st.start or time.time())[1]   # passed while closed: resets at once
            st.quality = "Restored"

    def reset_now(self, aid: str, now: float | None = None):
        a, st = self.by_id(aid), self.states.get(aid)
        if a is not None and st is not None:
            self._reset(a, st, time.time() if now is None else now)

    def _reset(self, a: dict, st: AggState, now: float):
        if a.get("keep_prev") and has_period(a):
            st.previous = st.value if (st.has or a["kind"] in ("totalizer", "counter", "runtime")) else None
        st.value, st.has, st.start, st.hold_t = 0.0, False, now, 0.0
        st.x0 = st.last_x if a["kind"] == "change" else None    # change: counts from the value now
        if a["kind"] == "change" and st.x0 is not None:
            st.has = True
        st.buf.clear()
        st.next = period_bounds(a, now + 0.001)[1]
        st.sent = 0.0                                    # hand the new value on at once

    # ---- evaluation
    def evaluate(self, now: float | None = None) -> list[tuple[dict, AggState]]:
        """Update every tag; -> [(definition, state)] of those to hand on (changed, or 1 s passed)."""
        now = time.time() if now is None else now
        out = []
        for a in self.order:
            st = self.states.get(a["id"])
            if st is None:
                st = self.states[a["id"]] = AggState(start=now, last_tick=now)
                st.next = period_bounds(a, now)[1]
                if a["kind"] in ("totalizer", "counter", "runtime"):
                    st.has = True
            try:                                     # the sample first: up to the reset it is the old period's
                self._step(a, st, now)
            except (TypeError, ValueError, OverflowError) as e:
                st.quality, st.msg = "Error", f"Error: {e}"
            st.last_tick = now
            if st.next is not None and now >= st.next:
                self._reset(a, st, now)
            if has_period(a) and a.get("reset") == "tag" and a.get("reset_tag"):
                tv = self.latest(tuple(a["reset_tag"]))[0]
                on = isinstance(tv, (int, float)) and tv > 0.5
                if on and not st.trig:
                    self._reset(a, st, now)
                st.trig = on
            v = st.value if st.has else None
            if v != st.sent_v or now - st.sent >= SEND_EVERY_S:
                st.sent, st.sent_v = now, v
                out.append((a, st))
        return out

    def _step(self, a: dict, st: AggState, now: float):
        val, stamp = self.latest(tuple(a["source"]))
        if isinstance(val, str):
            st.quality, st.msg = "Error", "The source is text - an aggregation needs a number"
            return
        good = val is not None and val == val
        new = stamp is not None and stamp > st.last_t
        k = a["kind"]
        if not good:
            st.quality = "Paused" if st.has or k in ("totalizer", "counter", "runtime") else "Waiting"
            st.msg = "The source has no good value - paused"
            if k == "totalizer":
                st.last_x = None                         # the gap is not added
            if k == "runtime":
                st.on = False
            if new:
                st.last_t = stamp
            return
        x = float(val)
        st.quality, st.msg = "Good", ""
        if k == "runtime":                             # wall time while it was on
            if st.on:
                st.value += (now - st.last_tick) / TIME_UNITS[a.get("time_unit", "h")][1]
            st.on = x > 0.5 or x < -0.5
            return
        if not new:
            return
        if k == "totalizer":
            if st.last_x is not None and stamp > st.last_t:
                rate = (x + st.last_x) / 2
                if a.get("skip_negative", True):
                    rate = max(rate, 0.0)
                st.value += rate * (stamp - st.last_t) / RATE_PER[a.get("rate_per", "h")][1] * float(a.get("factor", 1))
            st.last_x = x
        elif k == "counter":
            on = x > 0.5 or x < -0.5
            e = a.get("edge", "rising")
            if st.last_x is not None:
                hit = {"rising": on and not st.on, "falling": st.on and not on, "both": on != st.on,
                       "change": x != st.last_x}[e]
                st.value += 1 if hit else 0
            st.on, st.last_x = on, x
        elif k == "minmax":
            better = x > st.value if a.get("hold", "max") == "max" else x < st.value
            if not st.has or better:
                st.value, st.hold_t, st.has = x, stamp, True
            st.last_x = x
        elif k == "change":
            if st.x0 is None:
                st.x0 = x
            diff = x - st.x0
            if a.get("change") == "rate":
                span = max(stamp - st.start, 1e-9)
                st.value = diff / (span / RATE_PER[a.get("change_per", "h")][1])
            else:
                st.value = diff
            st.has, st.last_x = True, x
        elif k == "statistic":
            st.buf.append((stamp, x))
            unit, n = a.get("win_unit", "h"), float(a.get("win_n", 1))
            if unit == "samples":
                full = len(st.buf) >= int(n)
                while len(st.buf) > max(1, int(n)):
                    st.buf.popleft()
            else:
                span = n * WIN_UNITS[unit][1]
                full = st.buf[-1][0] - st.buf[0][0] >= span
                while st.buf and st.buf[0][0] < stamp - span:
                    st.buf.popleft()
            if a.get("win_mode") == "block":
                if full:
                    st.value, st.has = _stat(a.get("stat", "mean"), [v for _t, v in st.buf]), True
                    st.buf.clear()
            else:
                st.value, st.has = _stat(a.get("stat", "mean"), [v for _t, v in st.buf]), True
            st.last_x = x
        st.last_t = stamp

    def value(self, aid: str, idx: int = 0):
        """(value or None, stamp) of a key - for tables and other tags."""
        st = self.states.get(aid)
        if st is None:
            return None, None
        if idx == 1:
            return st.previous, st.sent or None
        return (st.value if st.has else None), (st.sent or None)


def _stat(name: str, xs: list[float]) -> float:
    n = len(xs)
    if not n:
        return math.nan
    if name == "mean":
        return sum(xs) / n
    if name == "min":
        return min(xs)
    if name == "max":
        return max(xs)
    if name == "rms":
        return math.sqrt(sum(x * x for x in xs) / n)
    if name == "sd":
        m = sum(xs) / n
        return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    if name == "range":
        return max(xs) - min(xs)
    if name == "count":
        return float(n)
    return sum(xs)
