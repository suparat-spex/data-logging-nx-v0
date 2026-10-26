"""
system_tags.py - System Tags: read-only values the app produces about itself (clock, app,
recording, alarms, one set per device). No Qt.

They are fixed (not stored in the project) and behave like any other tag: key
("sys:<id>", 0), shared streams, alarms, statistics, views and formulas (System_<name>).
Number / bool tags are fed as one-sample blocks on the GUI timer; text tags (date, file
name ...) are shown only - streams hold numbers.

Tag ids are stable: per-device tags use the device id (dev.<device id>.<field>), their
display name the device name (<device>.<field>), so renaming a device keeps alarms and views.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

SYS_PREFIX = "sys:"
KINDS = {"number": "Number", "bool": "Boolean", "text": "Text"}
CATEGORY = "System"                                   # name of the Tags category


@dataclass(frozen=True)
class SysTag:
    id: str                                           # stable: key = ("sys:" + id, 0)
    name: str                                         # shown, and the formula name System_<name>
    group: str                                        # Clock / App / Recording / Alarms / <device>
    kind: str = "number"                              # number / bool / text
    unit: str = ""
    decimals: int = 0
    description: str = ""

    @property
    def key(self) -> tuple[str, int]:
        return sys_key(self.id)

    @property
    def streamed(self) -> bool:
        """Numbers and booleans go into streams (plots, alarms, formulas); text is shown only."""
        return self.kind != "text"


FIXED = [
    SysTag("DateTime", "DateTime", "Clock", "text", description="Local date and time (YYYY-MM-DD HH:MM:SS)"),
    SysTag("Date", "Date", "Clock", "text", description="Local date (YYYY-MM-DD)"),
    SysTag("Time", "Time", "Clock", "text", description="Local time (HH:MM:SS)"),
    SysTag("Year", "Year", "Clock"),
    SysTag("Month", "Month", "Clock", description="1 - 12"),
    SysTag("Day", "Day", "Clock", description="Day of the month, 1 - 31"),
    SysTag("Hour", "Hour", "Clock", description="0 - 23"),
    SysTag("Minute", "Minute", "Clock", description="0 - 59"),
    SysTag("Second", "Second", "Clock", description="0 - 59"),
    SysTag("Weekday", "Weekday", "Clock", description="ISO weekday: 1 = Monday ... 7 = Sunday"),
    SysTag("UnixTime", "UnixTime", "Clock", unit="s", description="Seconds since 1970-01-01 UTC"),
    SysTag("Uptime", "Uptime", "App", unit="s", description="Seconds since the app started"),
    SysTag("ProjectName", "ProjectName", "App", "text", description="Name of the open project"),
    SysTag("AppVersion", "AppVersion", "App", "text", description="NX Logger version"),
    SysTag("DiskFree", "DiskFree", "App", unit="GB", decimals=1, description="Free space on the log folder drive"),
    SysTag("DevicesOnline", "DevicesOnline", "App", description="Number of devices online"),
    SysTag("Recording", "Recording", "Recording", "bool", description="1 while recording"),
    SysTag("RecFolder", "RecFolder", "Recording", "text", description="Folder of the current recording session"),
    SysTag("RecSize", "RecSize", "Recording", unit="MB", decimals=2, description="Size of the current session"),
    SysTag("RecDuration", "RecDuration", "Recording", unit="s", description="Seconds since recording started"),
    SysTag("AlarmsActive", "AlarmsActive", "Alarms", description="Number of active alarm levels"),
]

DEVICE_FIELDS = [   # (field, kind, unit, decimals, description)
    ("Connected", "bool", "", 0, "1 while the device is online"),
    ("State", "text", "", 0, "Stopped / Connecting / Online / Error"),
    ("Quality", "text", "", 0, "Good / Stale / Bad / Stopped"),
    ("LastUpdate", "text", "", 0, "Time of the last value"),
    ("Age", "number", "s", 1, "Seconds since the last value (stale data)"),
    ("Errors", "number", "", 0, "Communication errors since the device started"),
]


def sys_key(tid: str) -> tuple[str, int]:
    return (SYS_PREFIX + tid, 0)


def is_sys(tid) -> bool:
    return isinstance(tid, str) and tid.startswith(SYS_PREFIX)


def catalog(devices) -> list[SysTag]:
    """All System Tags of a project. devices: [(device id, device name)]."""
    out = list(FIXED)
    for did, name in devices:
        out += [SysTag(f"dev.{did}.{f}", f"{name}.{f}", name, kind, unit, dec, desc)
                for f, kind, unit, dec, desc in DEVICE_FIELDS]
    return out


def compute(snap: dict) -> dict[str, object]:
    """App state -> {tag id: value} (float for number / bool, str for text).

    snap: now (Unix s), started, project, version, disk_free (bytes or None),
          recording (bool), rec_dir, rec_bytes, rec_elapsed, alarms_active,
          devices: {id: {"state": stopped / connecting / online / error, "last": Unix s or None,
                         "stale_s": seconds after which a value is stale, "errors": int}}
    """
    now = snap["now"]
    lt = time.localtime(now)
    rec = bool(snap.get("recording"))
    free = snap.get("disk_free")
    devs = snap.get("devices", {})
    v: dict[str, object] = {
        "DateTime": time.strftime("%Y-%m-%d %H:%M:%S", lt), "Date": time.strftime("%Y-%m-%d", lt),
        "Time": time.strftime("%H:%M:%S", lt),
        "Year": float(lt.tm_year), "Month": float(lt.tm_mon), "Day": float(lt.tm_mday),
        "Hour": float(lt.tm_hour), "Minute": float(lt.tm_min), "Second": float(min(lt.tm_sec, 59)),
        "Weekday": float(lt.tm_wday + 1), "UnixTime": float(int(now)),
        "Uptime": float(int(now - snap.get("started", now))),
        "ProjectName": snap.get("project", ""), "AppVersion": snap.get("version", ""),
        "DiskFree": float("nan") if free is None else free / 1e9,
        "DevicesOnline": float(sum(1 for d in devs.values() if d.get("state") == "online")),
        "Recording": 1.0 if rec else 0.0,
        "RecFolder": snap.get("rec_dir", "") if rec else "",
        "RecSize": snap.get("rec_bytes", 0) / 1e6 if rec else 0.0,
        "RecDuration": float(int(snap.get("rec_elapsed", 0))) if rec else 0.0,
        "AlarmsActive": float(snap.get("alarms_active", 0)),
    }
    for did, d in devs.items():
        state, last = d.get("state", "stopped"), d.get("last")
        age = now - last if last else float("nan")
        if state == "stopped":
            qual = "Stopped"
        elif state != "online" or not last:
            qual = "Bad"
        else:
            qual = "Stale" if age > d.get("stale_s", 5.0) else "Good"
        p = f"dev.{did}."
        v[p + "Connected"] = 1.0 if state == "online" else 0.0
        v[p + "State"] = state.capitalize()
        v[p + "Quality"] = qual
        v[p + "LastUpdate"] = time.strftime("%H:%M:%S", time.localtime(last)) if last else ""
        v[p + "Age"] = age
        v[p + "Errors"] = float(d.get("errors", 0))
    return v


def format_value(tag: SysTag, val) -> str:
    if val is None:
        return ""
    if tag.kind == "text":
        return str(val)
    if val != val:                                    # NaN: not available
        return "-"
    if tag.kind == "bool":
        return "1" if val else "0"
    return f"{val:.{tag.decimals}f}"
