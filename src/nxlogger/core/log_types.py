"""
log_types.py - the logging types of a logging profile (Project tree > Logging). No Qt.

  interval   every N s (0 = every value: one row per scan / per sample)          when.every_s
  change     a row when a tag moves more than its deadband, plus a heartbeat     when.deadband, mode (abs / pct),
                                                                                 heartbeat_s, min_gap_s
  trigger    while a condition is true, with pre- and post-trigger time          when.start, stop, debounce_s,
                                                                                 pre_s, post_s, every_s
  event      one row of the chosen tags when something happens                   when.source (alarm / condition /
                                                                                 schedule / marker), alarm, cond,
                                                                                 times, debounce_s
  batch      per lot / shift: rows while it runs + a summary row at its end      when.by (tag / shift), start,
                                                                                 shifts, id_key, stats, every_s
  statistic  one row per period with the chosen statistics (interval_s, stats - top level, as before)

A condition: {"key": [tid, idx] | None, "op": ">", "value": 0.0} - a Virtual logic tag makes any
other condition ("Virtual_On_peak == 1").

Gate (per trigger / batch / event profile) runs on the GUI timer while recording: update(now,
value_of) -> [("on", t, label) | ("off", t, "") | ("fire", t, label)]; the window passes them to
the Recorder (gate / event), which writes in its thread.
"""
from __future__ import annotations

import copy
import datetime as _dt
import math

TYPES = [   # key, name, icon, what
    ("interval", "Interval", "clock", "Every N seconds (or every value)"),
    ("change", "On change", "waves", "A row when a tag moves more than its deadband, plus a heartbeat"),
    ("trigger", "Trigger", "zap", "While a condition is true, with pre- and post-trigger time"),
    ("event", "Event", "flag", "One row of the chosen tags when an alarm, a condition, a time or a marker happens"),
    ("batch", "Batch / Shift", "rows", "Per lot or shift: its rows and a summary row at its end"),
    ("statistic", "Statistics", "sigma", "One row per period: mean, min, max ..."),
]
TYPE = {t[0]: t for t in TYPES}
ICONS = [      # the icons a profile can have (resources/icons/ui, Lucide) - the six types first
    "clock", "waves", "zap", "flag", "rows", "sigma", "chart", "chart-column", "gauge", "activity",
    "audio-waveform", "power", "bell", "star", "tag", "database", "folder", "file-text", "house", "cpu",
    "grid", "factory", "boxes", "server", "network", "plug", "sliders", "hash", "list", "circle-dot"]


def icon_of(pr: dict) -> str:
    """A profile's icon: its own (one of ICONS), else its type's."""
    return pr["icon"] if pr.get("icon") in ICONS else TYPE[type_of(pr)][2]
OPS = [">", ">=", "<", "<=", "==", "!="]
EVENT_SOURCES = [("alarm", "Alarm comes in"), ("condition", "Condition becomes true"),
                 ("schedule", "Time of day"), ("marker", "Marker (toolbar)")]
BATCH_BY = [("tag", "Start / stop condition"), ("shift", "Shift times")]
SUMMARY_STATS = ["mean", "min", "max", "sum"]


def new_condition(key=None, op: str = ">", value: float = 0.0) -> dict:
    return {"key": list(key) if key else None, "op": op, "value": float(value)}


DEFAULT_WHEN = {
    "interval": {"every_s": 0.0},
    "change": {"deadband": 0.5, "mode": "pct", "heartbeat_s": 600.0, "min_gap_s": 0.0},
    "trigger": {"start": new_condition(), "stop": None, "debounce_s": 0.0, "pre_s": 30.0, "post_s": 60.0,
                "every_s": 0.0},
    "event": {"source": "alarm", "alarm": "", "cond": new_condition(), "times": "06:00", "debounce_s": 0.0},
    "batch": {"by": "shift", "start": new_condition(), "shifts": "06:00, 18:00", "id_key": None,
              "stats": ["mean", "min", "max"], "every_s": 0.0},
    "statistic": {},
}


def type_of(pr: dict) -> str:
    """The profile's type ("signal" of older files = interval, every value)."""
    t = pr.get("type", "interval")
    return "interval" if t == "signal" else (t if t in TYPE else "interval")


def default_when(kind: str) -> dict:
    return copy.deepcopy(DEFAULT_WHEN.get(kind, {}))


def normalize_when(pr: dict) -> dict:
    """The profile's `when` with every field (older files: none)."""
    kind = type_of(pr)
    pr["type"] = kind
    w = pr.get("when") if isinstance(pr.get("when"), dict) else {}
    out = default_when(kind)
    for k, v in w.items():
        if k in out:
            out[k] = v
    for k in ("start", "cond"):
        if k in out and not isinstance(out[k], dict):
            out[k] = new_condition()
    if "stop" in out and out["stop"] is not None and not isinstance(out["stop"], dict):
        out["stop"] = None
    pr["when"] = out
    return out


def parse_times(text: str) -> list[int]:
    """'06:00, 18:00' -> minutes of the day, sorted (bad parts skipped)."""
    out = []
    for part in str(text or "").replace(";", ",").split(","):
        part = part.strip()
        try:
            h, m = part.split(":") if ":" in part else (part, "0")
            v = int(h) * 60 + int(m)
        except ValueError:
            continue
        if 0 <= v < 24 * 60:
            out.append(v)
    return sorted(set(out))


def test(cond: dict | None, value) -> bool | None:
    """The condition on a value: True / False, None = no value (or no tag)."""
    if not cond or not cond.get("key") or value is None:
        return None
    try:
        v, ref = float(value), float(cond.get("value", 0.0))
    except (TypeError, ValueError):
        return None
    if math.isnan(v):
        return None
    op = cond.get("op", ">")
    return {">": v > ref, ">=": v >= ref, "<": v < ref, "<=": v <= ref,
            "==": abs(v - ref) < 1e-9, "!=": abs(v - ref) >= 1e-9}.get(op, False)


def describe_cond(cond: dict | None, key_name) -> str:
    """'Comp1_kW > 5' - key_name(key) -> the tag's short name."""
    if not cond or not cond.get("key"):
        return "(no tag)"
    return f"{key_name(tuple(cond['key']))} {cond.get('op', '>')} {cond.get('value', 0):g}"


def fmt_s(s) -> str:
    s = float(s or 0)
    if s <= 0:
        return "0 s"
    for unit, n in (("h", 3600), ("min", 60)):
        if s >= n and s % n == 0:
            return f"{s / n:g} {unit}"
    return f"{s:g} s"


def describe(pr: dict, key_name, alarm_name=lambda aid: aid) -> str:
    """The When column: 'every 1 s', '±0.5 % · 10 min', 'Comp1_kW > 5' ..."""
    kind = type_of(pr)
    w = normalize_when(pr) if "when" not in pr or not isinstance(pr["when"], dict) else pr["when"]
    w = {**default_when(kind), **w}
    if kind == "interval":
        return "every value" if not w["every_s"] else f"every {fmt_s(w['every_s'])}"
    if kind == "change":
        db = f"±{w['deadband']:g}" + (" %" if w["mode"] == "pct" else "")
        return db + (f" · heartbeat {fmt_s(w['heartbeat_s'])}" if w["heartbeat_s"] else "")
    if kind == "trigger":
        stop = describe_cond(w["stop"], key_name) if w.get("stop") else "until false"
        return f"{describe_cond(w['start'], key_name)} → {stop}"
    if kind == "event":
        src = w["source"]
        if src == "alarm":
            return "any alarm" if not w["alarm"] else f"alarm {alarm_name(w['alarm'])}"
        if src == "condition":
            return f"when {describe_cond(w['cond'], key_name)}"
        if src == "schedule":
            return f"at {w['times']}"
        return "Marker"
    if kind == "batch":
        return f"shifts {w['shifts']}" if w["by"] == "shift" else f"while {describe_cond(w['start'], key_name)}"
    return f"every {fmt_s(pr.get('interval_s', 60))}"


def problem(pr: dict) -> str:
    """'' if the profile's When settings can run, else what is missing."""
    kind = type_of(pr)
    w = pr.get("when") or {}
    if kind == "trigger" and not (w.get("start") or {}).get("key"):
        return "When: choose the start tag."
    if kind == "event":
        if w.get("source") == "condition" and not (w.get("cond") or {}).get("key"):
            return "When: choose the condition's tag."
        if w.get("source") == "schedule" and not parse_times(w.get("times")):
            return "When: enter times of day (06:00, 18:00)."
    if kind == "batch":
        if w.get("by") == "tag" and not (w.get("start") or {}).get("key"):
            return "When: choose the start / stop tag."
        if w.get("by") == "shift" and not parse_times(w.get("shifts")):
            return "When: enter shift start times (06:00, 18:00)."
    if kind == "change" and float(w.get("deadband", 0) or 0) < 0:
        return "When: the deadband cannot be negative."
    return ""


# ====================================================================== the gate (GUI timer)
class Gate:
    """When a trigger / batch is active, when an event fires - from live values. value_of(key) ->
    float | None; text_of(key) -> str (the batch id); now = unix time."""

    def __init__(self, pr: dict, text_of=None):
        self.kind = type_of(pr)
        self.w = {**default_when(self.kind), **(pr.get("when") or {})}
        self.text_of = text_of or (lambda key: "")
        self.active = False
        self.runs = 0                            # trigger runs / batches / events so far
        self._since = None                       # when the pending change was first seen (debounce)
        self._last_cond = None                   # event: the condition's last state (edges)
        self._shift = None                       # batch by shift: the current shift (start minute, date)
        self._last_minute = None                 # event schedule

    def _debounced(self, want: bool, now: float) -> bool:
        """True once `want` has held for debounce_s."""
        if not want:
            self._since = None
            return False
        if self._since is None:
            self._since = now
        return now - self._since >= float(self.w.get("debounce_s") or 0)

    def update(self, now: float, value_of) -> list[tuple]:
        out = []
        if self.kind == "trigger" or (self.kind == "batch" and self.w["by"] == "tag"):
            on = test(self.w["start"], value_of(tuple(self.w["start"]["key"])) if self.w["start"].get("key") else None)
            stop = self.w.get("stop") if self.kind == "trigger" else None
            if not self.active:
                if self._debounced(bool(on), now):
                    self.active, self._since = True, None
                    self.runs += 1
                    out.append(("on", now, self._label(now)))
            else:
                if stop and stop.get("key"):
                    off = test(stop, value_of(tuple(stop["key"])))
                else:
                    off = on is False
                if self._debounced(bool(off), now):
                    self.active, self._since = False, None
                    out.append(("off", now, ""))
        elif self.kind == "batch":                  # shifts
            cur = self.shift_at(now)
            if cur != self._shift:
                if self.active:
                    out.append(("off", now, ""))
                self._shift, self.active = cur, cur is not None
                if cur is not None:
                    self.runs += 1
                    out.append(("on", now, self._label(now)))
        elif self.kind == "event":
            src = self.w["source"]
            if src == "condition":
                c = test(self.w["cond"], value_of(tuple(self.w["cond"]["key"])) if self.w["cond"].get("key") else None)
                held = self._debounced(bool(c), now)
                if held and self._last_cond is False:
                    out.append(self._fire(now, describe_cond(self.w["cond"], lambda k: "condition")))
                if c is not None and (held or not c):
                    self._last_cond = bool(held)
            elif src == "schedule":
                lt = _dt.datetime.fromtimestamp(now)
                minute = lt.hour * 60 + lt.minute
                if minute != self._last_minute:
                    if self._last_minute is not None and minute in parse_times(self.w["times"]):
                        out.append(self._fire(now, f"Schedule {lt:%H:%M}"))
                    self._last_minute = minute
        return out

    def _fire(self, now: float, label: str) -> tuple:
        self.runs += 1
        return ("fire", now, label)

    def alarm(self, alarm_id: str, text: str, now: float) -> list[tuple]:
        """An alarm came in (event profiles with source alarm)."""
        if self.kind == "event" and self.w["source"] == "alarm" and self.w["alarm"] in ("", alarm_id):
            return [self._fire(now, text)]
        return []

    def marker(self, text: str, now: float) -> list[tuple]:
        if self.kind == "event" and self.w["source"] == "marker":
            return [self._fire(now, text)]
        return []

    def shift_at(self, now: float):
        """(date, start minute) of the shift running at `now` (the last start time before it)."""
        times = parse_times(self.w.get("shifts"))
        if not times:
            return None
        lt = _dt.datetime.fromtimestamp(now)
        minute = lt.hour * 60 + lt.minute
        past = [t for t in times if t <= minute]
        if past:
            return (lt.date().isoformat(), past[-1])
        return ((lt.date() - _dt.timedelta(days=1)).isoformat(), times[-1])     # yesterday's last shift

    def _label(self, now: float) -> str:
        if self.kind == "trigger":
            return f"Run {self.runs}"
        idk = self.w.get("id_key")
        txt = self.text_of(tuple(idk)) if idk else ""
        if txt:
            return txt
        if self.w.get("by") == "shift" and self._shift:
            d, m = self._shift
            return f"{d} {m // 60:02d}:{m % 60:02d}"
        return f"Batch {self.runs}"
