"""
alarm_rules.py - alarm engine for NX Logger, modelled on paperless recorders / data loggers
(Yokogawa GA10 / DX / GX: 4 alarm levels per tag, hysteresis, alarm log).

Alarm definition (plain dict, saved in the project file):
  one alarm = one channel ("tag") with up to 4 levels (Level 1 ... Level 4). Each level:
    type       H   high limit          value > setpoint
               L   low limit           value < setpoint
               dH  difference high     (channel - reference channel) > setpoint
               dL  difference low      (channel - reference channel) < setpoint
               RH  rate-of-change rise mean rises faster than setpoint units/s
               RL  rate-of-change fall mean falls faster than setpoint units/s
               D   data loss           no data from the channel for setpoint seconds
    setpoint, hysteresis (the alarm clears only when the value is back by this much),
    on-delay   the condition must hold this long before the alarm turns ON
               (GA10's delay alarms tH / tL = H / L with an on-delay).
  H / L are tested on the block min / max, so a short spike inside a packet still
  trips the alarm (the live value only shows the last sample).

Level colours (priority): Level 1 red, Level 2 orange, Level 3 yellow, Level 4 blue.

An alarm is in the active list while its condition holds and leaves it when it clears.
Every ON / OFF goes to the alarm log (newest 1000).

Notification profiles route events to targets: log only, popup + sound, REST API
(webhook, JSON POST/PUT, sent off the GUI thread), MQTT publish, e-mail (planned).
Each profile filters by level and by event (ON / OFF); each alarm picks the
profiles it notifies.
The engine (AlarmEngine, Notifier) has no Qt. The UI is in ui/dialogs/alarm_dialog.py,
ui/dialogs/alarm_profile_dialog.py and ui/widgets/alarm_panel.py (the Alarms document).
"""
from __future__ import annotations

import collections
import datetime as _dt
import json
import math
import queue
import threading
import urllib.request
import uuid
from dataclasses import dataclass

import numpy as np


LEVELS = 4
TYPES = {
    "H": "High limit",
    "L": "Low limit",
    "dH": "Difference high (channel − reference)",
    "dL": "Difference low (channel − reference)",
    "RH": "Rate of change, rise (units/s)",
    "RL": "Rate of change, fall (units/s)",
    "D": "Data loss (no data for N s)",
}
REF_TYPES = {"dH", "dL"}
UP_TYPES, DOWN_TYPES = {"H", "dH", "RH"}, {"L", "dL", "RL"}
LEVEL_TOKEN = {1: "bad", 2: "accent", 3: "warn", 4: "info"}
PROFILE_KINDS = {"log": "Log only", "popup": "Popup + sound", "rest": "REST API (webhook)",
                 "mqtt": "MQTT publish", "email": "E-mail (planned)"}
EVENTS = ("ON", "OFF")
ACTIVE_COLS = ["Level", "State", "Alarm", "Tag", "Type", "Setpoint", "Value", "Since", "Duration"]
LOG_COLS = ["Date", "Time", "Level", "Event", "Alarm", "Tag", "Type", "Value", "Setpoint"]
DEF_COLS = ["Alarm", "Channel", "Level 1", "Level 2", "Level 3", "Level 4", "Notify", "State"]
PROF_COLS = ["Profile", "Type", "Target", "Levels", "Events", "Last result"]
MAX_LOG = 1000


# ====================================================================== model helpers
def new_level(kind: str = "H") -> dict:
    return {"enabled": False, "type": kind, "value": 0.0, "hyst": 0.0, "delay_s": 0.0}


def new_alarm(channel=None, name: str = "") -> dict:
    return {"id": uuid.uuid4().hex[:8], "name": name or "New alarm", "enabled": True,
            "channel": list(channel) if channel else None, "ref": None, "description": "",
            "levels": [new_level(k) for k in ("H", "L", "H", "L")], "profiles": []}


def new_profile(kind: str = "popup", name: str = "") -> dict:
    return {"id": uuid.uuid4().hex[:8], "name": name or PROFILE_KINDS[kind], "enabled": True,
            "kind": kind, "levels": [1, 2, 3, 4], "on": True, "off": True,
            "url": "", "method": "POST", "headers": "", "timeout_s": 5.0,
            "topic": "", "qos": 0, "retain": False, "to": ""}


def normalize_alarm(a: dict) -> dict:
    base = new_alarm()
    for k, v in base.items():
        a.setdefault(k, v)
    lv = list(a.get("levels") or [])[:LEVELS]
    lv += [new_level() for _ in range(LEVELS - len(lv))]
    a["levels"] = [{**new_level(), **x} for x in lv]
    return a


def normalize_profile(p: dict) -> dict:
    for k, v in new_profile(p.get("kind", "log")).items():
        p.setdefault(k, v)
    return p


def describe_level(lv: dict, unit: str = "") -> str:
    if not lv.get("enabled"):
        return ""
    t, v = lv["type"], lv["value"]
    unit = " s" if t == "D" else (f" {unit}/s" if t in ("RH", "RL") and unit else
                                  ("/s" if t in ("RH", "RL") else (f" {unit}" if unit else "")))
    s = f"{t} {v:g}{unit}"
    if lv.get("hyst"):
        s += f" ±{lv['hyst']:g}"
    if lv.get("delay_s"):
        s += f" ⏱{lv['delay_s']:g}s"
    return s


def migrate_channel_limits(topics) -> list[dict]:
    """v5 channels had alarm_lo / alarm_hi: turn them into Level 1 (H) / Level 2 (L)."""
    out = []
    for t in topics:
        for c in t.channels:
            if c.alarm_hi is None and c.alarm_lo is None:
                continue
            a = new_alarm((t.id, c.index), f"{t.name} · {c.name}")
            if c.alarm_hi is not None:
                a["levels"][0] = {**new_level("H"), "enabled": True, "value": float(c.alarm_hi)}
            if c.alarm_lo is not None:
                a["levels"][1] = {**new_level("L"), "enabled": True, "value": float(c.alarm_lo)}
            out.append(a)
            c.alarm_hi = c.alarm_lo = None
    return out


# ====================================================================== engine (no Qt)
@dataclass
class LevelState:
    active: bool = False
    pending: float | None = None
    on_t: float = 0.0
    off_t: float | None = None
    value: float = math.nan


class AlarmEngine:
    """
    Evaluates the alarm definitions on every scaled block (feed) and on a timer (tick,
    data-loss alarms). Emits event dicts to `listeners`:
      {"t", "alarm_id", "name", "tag", "unit", "level", "type", "event", "value", "setpoint"}
    `describe(tid, idx) -> (tag text, unit)` and `ref_value(tid, idx) -> float | None`
    are set by the host.
    """

    def __init__(self, max_log: int = MAX_LOG):
        self.alarms: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.by_ch: dict[tuple, list[dict]] = {}
        self.states: dict[tuple[str, int], LevelState] = {}
        self.log: "collections.deque[dict]" = collections.deque(maxlen=max_log)
        self.listeners: list = []
        self.last_seen: dict[tuple, float] = {}
        self._rate: dict[tuple, tuple[float, float]] = {}
        self.describe = lambda tid, idx: (f"{tid}:{idx}", "")
        self.ref_value = lambda tid, idx: None
        self.seq = 0                                 # bumps on every event (cheap "changed?")

    # ---- configuration
    def set_alarms(self, alarms: list[dict]):
        self.alarms = alarms
        self.by_id, self.by_ch = {}, {}
        valid = set()
        for a in alarms:
            normalize_alarm(a)
            self.by_id[a["id"]] = a
            if a["enabled"] and a.get("channel"):
                self.by_ch.setdefault(tuple(a["channel"]), []).append(a)
                valid |= {(a["id"], i + 1) for i, lv in enumerate(a["levels"]) if lv["enabled"]}
        for k in list(self.states):                  # removed / disabled levels: forget
            if k not in valid:
                del self.states[k]
        self.seq += 1

    # ---- evaluation
    def feed(self, tid: str, idx: int, now: float, y: np.ndarray):
        key = (tid, idx)
        self.last_seen[key] = now
        alarms = self.by_ch.get(key)
        if not alarms:
            return
        y = y[np.isfinite(y)]
        if y.size == 0:
            return
        vmin, vmax, vlast, vmean = float(y.min()), float(y.max()), float(y[-1]), float(y.mean())
        prev = self._rate.get(key)
        self._rate[key] = (now, vmean)
        rate = (vmean - prev[1]) / (now - prev[0]) if prev and now - prev[0] > 1e-6 else None
        for a in alarms:
            for i, lv in enumerate(a["levels"]):
                if not lv["enabled"]:
                    continue
                t, sp, h = lv["type"], float(lv["value"]), abs(float(lv.get("hyst") or 0))
                if t == "H":
                    on, off, v = vmax > sp, vmax < sp - h, vmax
                elif t == "L":
                    on, off, v = vmin < sp, vmin > sp + h, vmin
                elif t in REF_TYPES:
                    ref = self.ref_value(*a["ref"]) if a.get("ref") else None
                    if ref is None:
                        continue
                    d = vlast - ref
                    on, off, v = (d > sp, d < sp - h, d) if t == "dH" else (d < sp, d > sp + h, d)
                elif t in ("RH", "RL"):
                    if rate is None:
                        continue
                    r = rate if t == "RH" else -rate
                    on, off, v = r > sp, r < sp - h, rate
                else:
                    continue                          # D: see tick()
                self._step(a, i + 1, lv, now, on, off, v)

    def tick(self, now: float):
        """Timer: data-loss levels (no data from the channel for `setpoint` seconds)."""
        for key, alarms in self.by_ch.items():
            seen = self.last_seen.get(key)
            if seen is None:                          # never received: nothing to lose yet
                continue
            gap = now - seen
            for a in alarms:
                for i, lv in enumerate(a["levels"]):
                    if lv["enabled"] and lv["type"] == "D":
                        sp = float(lv["value"])
                        self._step(a, i + 1, lv, now, gap > sp, gap <= sp, gap)

    def reset_data_loss(self):
        """Forget when channels were last seen (e.g. after a deliberate disconnect)."""
        self.last_seen.clear()

    def _step(self, a, level, lv, now, on, off, value):
        st = self.states.setdefault((a["id"], level), LevelState())
        if not st.active:
            if not on:
                st.pending = None
                return
            if st.pending is None:
                st.pending = now
            if now - st.pending < float(lv.get("delay_s") or 0):
                return
            st.active, st.on_t, st.off_t, st.pending = True, now, None, None
            st.value = value
            self._emit(a, level, lv, "ON", value, now)
        else:
            st.value = value
            if off:
                st.active, st.off_t = False, now
                self._emit(a, level, lv, "OFF", value, now)

    def _emit(self, a, level, lv, event, value, now):
        tag, unit = self.describe(*a["channel"]) if a.get("channel") else ("-", "")
        ev = {"t": now, "alarm_id": a["id"], "name": a["name"], "tag": tag, "unit": unit,
              "level": level, "type": lv["type"], "event": event, "value": value,
              "setpoint": float(lv["value"])}
        self.log.append(ev)
        self.seq += 1
        for fn in self.listeners:
            fn(ev)

    def clear_log(self):
        self.log.clear()
        self.seq += 1

    # ---- queries
    def active_rows(self) -> list[dict]:
        rows = []
        for (aid, level), st in self.states.items():
            a = self.by_id.get(aid)
            if a is None or not st.active:
                continue
            lv = a["levels"][level - 1]
            tag, unit = self.describe(*a["channel"]) if a.get("channel") else ("-", "")
            rows.append({"key": (aid, level), "level": level, "active": st.active,
                         "name": a["name"], "tag": tag, "unit": unit,
                         "type": lv["type"], "setpoint": float(lv["value"]), "value": st.value,
                         "since": st.on_t, "off": st.off_t})
        rows.sort(key=lambda r: (r["level"], -r["since"]))
        return rows

    def count(self) -> int:
        return sum(1 for st in self.states.values() if st.active)

    def channel_state(self, tid: str, idx: int):
        """(highest active level, type) for a channel, or (0, "")."""
        best = (0, "")
        for a in self.by_ch.get((tid, idx), []):
            for i in range(LEVELS):
                st = self.states.get((a["id"], i + 1))
                if st and st.active and (best[0] == 0 or i + 1 < best[0]):
                    best = (i + 1, a["levels"][i]["type"])
        return best

    def alarm_state(self, aid: str) -> int:
        lv = [i + 1 for i in range(LEVELS)
              if (s := self.states.get((aid, i + 1))) is not None and s.active]
        return min(lv) if lv else 0


def event_payload(ev: dict, project: str) -> dict:
    """JSON body sent to REST / MQTT targets."""
    return {"source": "NX Logger", "project": project, "event": ev["event"],
            "level": ev["level"], "type": ev["type"], "alarm": ev["name"], "alarm_id": ev["alarm_id"],
            "tag": ev["tag"], "value": None if math.isnan(ev["value"]) else ev["value"],
            "setpoint": ev["setpoint"], "unit": ev["unit"],
            "time": _dt.datetime.fromtimestamp(ev["t"]).astimezone().isoformat(timespec="milliseconds"),
            "time_unix": ev["t"]}


def profile_wants(p: dict, ev: dict) -> bool:
    return bool(p.get("enabled")) and ev["level"] in p.get("levels", []) and \
        bool(p.get({"ON": "on", "OFF": "off"}.get(ev["event"], "")))


class Notifier:
    """REST calls run in a worker thread; results come back through `results`
    (profile id, ok, text) for the GUI to show. MQTT goes through `mqtt_publish`
    (topic, bytes, qos, retain) -> bool, called on the GUI thread."""

    def __init__(self, mqtt_publish=None):
        self.q: "queue.Queue" = queue.Queue(maxsize=1000)
        self.results: "queue.Queue[tuple[str, bool, str]]" = queue.Queue()
        self.mqtt_publish = mqtt_publish
        self.dropped = 0
        threading.Thread(target=self._run, name="alarm-notifier", daemon=True).start()

    def send(self, p: dict, payload: dict):
        kind = p.get("kind")
        if kind == "rest":
            try:
                self.q.put_nowait((p, payload))
            except queue.Full:
                self.dropped += 1
                self.results.put((p["id"], False, "queue full - event dropped"))
        elif kind == "mqtt":
            topic = p.get("topic", "").strip()
            if not topic or self.mqtt_publish is None:
                self.results.put((p["id"], False, "no MQTT topic"))
                return
            ok = self.mqtt_publish(topic, json.dumps(payload).encode(), int(p.get("qos", 0)),
                                   bool(p.get("retain")))
            self.results.put((p["id"], bool(ok), f"published to {topic}" if ok else "not connected"))
        elif kind == "email":
            self.results.put((p["id"], False, "e-mail is not available yet (planned)"))

    def _run(self):
        while True:
            p, payload = self.q.get()
            url = p.get("url", "").strip()
            try:
                if not url.lower().startswith(("http://", "https://")):
                    raise ValueError("URL must start with http:// or https://")
                headers = {"Content-Type": "application/json", "User-Agent": "NX-Logger"}
                for line in p.get("headers", "").splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        headers[k.strip()] = v.strip()
                req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                             headers=headers, method=p.get("method", "POST"))
                with urllib.request.urlopen(req, timeout=float(p.get("timeout_s", 5))) as r:
                    self.results.put((p["id"], True, f"HTTP {r.status}"))
            except Exception as e:                    # report, never crash the worker
                self.results.put((p["id"], False, str(e)[:200]))
