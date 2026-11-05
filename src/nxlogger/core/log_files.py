"""
log_files.py - where a logging profile writes: its folder, the date folders it makes, when it
starts a new file and how files are named. No Qt (the Recorder and the profile pop-up use it).

  pr["output"] = {
    "folder":    ""            "" = the project's default log folder, else a path
    "folders":   "ymd"         none / session / year / year_month / ymd / date / custom (FOLDERS)
    "pattern":   ""            the custom folder pattern: "{yyyy}/{MM}-{MMM}/{dd}"
    "new_file":  "day"         session / hour / day / week / month / size / run (NEW_FILE)
    "day_at":    "00:00"       every day: the time the new file starts (a shift start)
    "week_day":  0             every week: 0 = Monday
    "size_mb":   100.0         every N MB
    "name":      ""            the file name pattern, "" = automatic (auto_name)
    "compress":  False         closed files -> .zst
    "keep_days": 0             delete this profile's own files older than N days (0 = keep)
  }

Tokens (folders and names): {yyyy} {MM} {MMM} {dd} {HH} {ww} {project} {profile} {device} {run}
{session}. Times are local; a period is [start, end) - a row at 00:00:00 is the new day's.
Older profiles (no "output") keep the session folder and one file per session.
"""
from __future__ import annotations

import datetime as _dt
import re

FOLDERS = [   # key, label, pattern
    ("none", "None", ""),
    ("session", "Session", "{project}_{session}"),
    ("year", "Year", "{yyyy}"),
    ("year_month", "Year \\ Month", "{yyyy}/{MM}"),
    ("ymd", "Year \\ Month \\ Day", "{yyyy}/{MM}/{dd}"),
    ("date", "Date", "{yyyy}-{MM}-{dd}"),
    ("custom", "Custom...", None),
]
FOLDER = {f[0]: f for f in FOLDERS}
NEW_FILE = [   # key, label, the name suffix it needs
    ("session", "Per session", ""),
    ("hour", "Every hour", "_{yyyy}{MM}{dd}_{HH}"),
    ("day", "Every day", "_{yyyy}{MM}{dd}"),
    ("week", "Every week", "_{yyyy}W{ww}"),
    ("month", "Every month", "_{yyyy}{MM}"),
    ("size", "Every N MB", ""),
    ("run", "Per run / batch", "_{run}"),
]
NEW = {n[0]: n for n in NEW_FILE}
NEEDS = {"hour": "{HH}", "day": "{dd}", "week": "{ww}", "month": "{MM}", "run": "{run}"}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
TOKENS = ["yyyy", "MM", "MMM", "dd", "HH", "ww", "project", "profile", "device", "run", "session"]
BAD = re.compile(r'[<>:"|?*\x00-\x1f]')
TYPE_DEFAULTS = {   # new profiles: (folders, new file)
    "interval": ("ymd", "day"), "change": ("year_month", "month"), "trigger": ("ymd", "run"),
    "event": ("year", "month"), "batch": ("year_month", "run"), "statistic": ("year", "month"),
}


def default_output(kind: str | None = None) -> dict:
    """The output of a new profile of `kind` (None: one session folder, one file per session -
    older profiles)."""
    folders, new_file = TYPE_DEFAULTS.get(kind, ("session", "session"))
    return {"folder": "", "folders": folders, "pattern": "", "new_file": new_file, "day_at": "00:00",
            "week_day": 0, "size_mb": 100.0, "name": "", "compress": False, "keep_days": 0}


def normalize_output(pr: dict) -> dict:
    out = {**default_output(), **(pr.get("output") if isinstance(pr.get("output"), dict) else {})}
    if out["folders"] not in FOLDER:
        out["folders"] = "session"
    if out["new_file"] not in NEW:
        out["new_file"] = "session"
    if out["new_file"] == "run" and pr.get("type") not in ("trigger", "batch"):
        out["new_file"] = "session"
    pr["output"] = out
    return out


def folder_pattern(out: dict) -> str:
    return out.get("pattern", "") if out["folders"] == "custom" else FOLDER[out["folders"]][2]


def auto_name(out: dict, kind: str = "") -> str:
    base = "{profile}" if kind == "event" else "{profile}_{device}"
    return base + NEW[out["new_file"]][2]


def name_pattern(out: dict, kind: str = "") -> tuple[str, str]:
    """(the file name pattern used, a note when a needed token was added)."""
    name = (out.get("name") or "").strip() or auto_name(out, kind)
    need = NEEDS.get(out["new_file"])
    if need and need not in name and not (need == "{MM}" and "{MMM}" in name):
        add = NEW[out["new_file"]][2]
        return name + add, f"{add} added - every {out['new_file']} needs a new name"
    return name, ""


def day_minutes(text: str) -> int:
    try:
        h, m = str(text or "0:0").split(":")
        return max(0, min(24 * 60 - 1, int(h) * 60 + int(m)))
    except ValueError:
        return 0


def period_start(out: dict, t: float) -> float:
    """The start (Unix s) of the period holding t for hour / day / week / month (else 0)."""
    mode = out["new_file"]
    lt = _dt.datetime.fromtimestamp(t)
    if mode == "hour":
        s = lt.replace(minute=0, second=0, microsecond=0)
    elif mode in ("day", "week"):
        m = day_minutes(out.get("day_at"))
        s = lt.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)
        if s > lt:
            s -= _dt.timedelta(days=1)
        if mode == "week":
            s -= _dt.timedelta(days=(s.weekday() - int(out.get("week_day", 0))) % 7)
    elif mode == "month":
        s = lt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return 0.0
    return s.timestamp()


def next_start(out: dict, t: float) -> float | None:
    """When the next file starts (None: not by time)."""
    mode = out["new_file"]
    s = period_start(out, t)
    if mode not in ("hour", "day", "week", "month"):
        return None
    d = _dt.datetime.fromtimestamp(s)
    if mode == "hour":
        n = d + _dt.timedelta(hours=1)
    elif mode == "day":
        n = d + _dt.timedelta(days=1)
    elif mode == "week":
        n = d + _dt.timedelta(days=7)
    else:
        n = (d.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
    return n.timestamp()


def clean(text: str) -> str:
    """A path part without characters Windows refuses."""
    return BAD.sub("_", text).strip().strip(".") or "_"


def render(pattern: str, t: float, ctx: dict) -> str:
    """Fill the tokens (t: the period start; ctx: project, profile, device, run, session)."""
    lt = _dt.datetime.fromtimestamp(t)
    vals = {"yyyy": f"{lt.year:04d}", "MM": f"{lt.month:02d}", "MMM": MONTHS[lt.month - 1], "dd": f"{lt.day:02d}",
            "HH": f"{lt.hour:02d}", "ww": f"{lt.isocalendar()[1]:02d}"}
    for k in ("project", "profile", "device", "run", "session"):
        vals[k] = str(ctx.get(k, "") or "")
    out = re.sub(r"\{(\w+)\}", lambda m: vals.get(m.group(1), m.group(0)), pattern)
    return re.sub(r"_{2,}", "_", out).strip("_")


def folder_parts(out: dict, t: float, ctx: dict) -> list[str]:
    pat = folder_pattern(out)
    return [clean(p) for p in re.split(r"[\\/]+", render(pat, t, ctx)) if p.strip()] if pat else []


def problem(out: dict) -> str:
    """'' if the folder / name patterns can be used, else what is wrong."""
    if out["folders"] == "custom":
        pat = out.get("pattern", "").strip()
        if not pat:
            return "Output: enter a folder pattern ({yyyy}\\{MM})."
        if BAD.search(pat.replace(":", "")) or pat.startswith(("\\", "/")):
            return "Output: the folder pattern has characters a folder name cannot have."
    name = (out.get("name") or "").strip()
    if name and (BAD.search(name) or "/" in name or "\\" in name):
        return "Output: the file name has characters a file name cannot have."
    unknown = {m for m in re.findall(r"\{(\w+)\}", folder_pattern(out) + name)} - set(TOKENS)
    if unknown:
        return "Output: unknown token " + ", ".join("{" + u + "}" for u in sorted(unknown)) + "."
    if out["new_file"] == "size" and float(out.get("size_mb") or 0) <= 0:
        return "Output: the file size must be above 0 MB."
    if int(out.get("keep_days") or 0) > 0 and "{profile}" not in name_pattern(out)[0]:
        return "Output: Keep N days needs {profile} in the file name (it deletes only this profile's files)."
    return ""


def describe(out: dict) -> str:
    """'every day at 00:00', 'every 100 MB', 'per session' ..."""
    m = out["new_file"]
    if m == "day":
        return f"every day at {out.get('day_at', '00:00')}"
    if m == "week":
        return f"every week ({WEEKDAYS[int(out.get('week_day', 0)) % 7]} {out.get('day_at', '00:00')})"
    if m == "size":
        return f"every {float(out.get('size_mb', 100)):g} MB"
    return NEW[m][1].lower()


def keep_prefix(out: dict, ctx: dict) -> str:
    """The fixed start of this profile's file names (Keep N days deletes only these)."""
    name = name_pattern(out)[0]
    head = name.split("{profile}")[0] + "{profile}"
    return clean(render(head, 2 * 86400.0, ctx))         # no time tokens before {profile} in practice
