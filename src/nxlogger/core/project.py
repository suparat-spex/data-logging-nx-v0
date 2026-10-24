"""Project model (.sxl / .mqdaq JSON): topics, channels, logging profiles, alarms, views. No Qt."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

from nxlogger import paths
from nxlogger.core import alarm_rules as ar
from nxlogger.core import ml_log as mlg
from nxlogger.core import variables as vr
from nxlogger.core.view_model import normalize_view, split_tabs


# Channel colours are a data ramp, separate from the UI tokens (DESIGN.md §7)
COLORS = ["#e8b100", "#2f9bf0", "#e5484d", "#30a46c",
          "#8e4ec6", "#f76b15", "#12a594", "#d6409f"]
PROJECT_EXT = ".sxl"
OLD_PROJECT_EXT = ".mqdaq"               # MQDAQ v6 files still open


@dataclass
class ChannelCfg:
    index: int                    # 0-based channel number in the packet
    name: str
    unit: str = ""
    enabled: bool = True
    color: str = ""               # "" = default palette colour for the index
    description: str = ""
    scale: dict = field(default_factory=lambda: {"type": "none"})   # see daq_channels.py
    decimals: int = 3
    y_min: float | None = None    # fixed Y range on plots (both set) or auto
    y_max: float | None = None
    alarm_lo: float | None = None
    alarm_hi: float | None = None


def ch_color(c: ChannelCfg) -> str:
    return c.color or COLORS[c.index % len(COLORS)]


@dataclass
class TopicCfg:
    name: str
    topic: str
    qos: int = 0
    enabled: bool = True
    channels: list[ChannelCfg] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def channel(self, idx: int) -> ChannelCfg | None:
        return next((c for c in self.channels if c.index == idx), None)


@dataclass
class LoggingCfg:
    folder: str = field(default_factory=lambda: str(paths.recordings_dir()))
    format: str = "CSV"           # v5 single format (kept so v5 can still open the file);
    keep_raw: bool = False        # v6 uses Project.logging_profiles


STAT_INTERVALS = [("1 s", 1), ("10 s", 10), ("1 min", 60), ("10 min", 600), ("1 h", 3600),
                  ("1 day", 86400)]


def new_log_profile(kind: str = "signal", name: str = "") -> dict:
    """Logging profile: what to write each Record run (see daq_logging.Recorder)."""
    return {"id": uuid.uuid4().hex[:8], "name": name or ("Waveform" if kind == "signal" else "Statistics"),
            "enabled": True, "type": kind, "format": "CSV", "keep_raw": False,
            "channels": None,                   # None = all enabled channels, else [[topic_id, index]]
            "interval_s": 1.0 if kind == "signal" else 60.0, "stats": ["mean", "min", "max"]}


def fmt_interval(s: float) -> str:
    s = float(s)
    for text, v in reversed(STAT_INTERVALS):
        if s >= v and s % v == 0:
            n = int(s // v)
            return text if n == 1 else f"{n} × {text}"
    return f"{s:g} s"


@dataclass
class Project:
    name: str = "Untitled project"
    operator: str = ""
    description: str = ""
    topics: list[TopicCfg] = field(default_factory=list)
    logging: LoggingCfg = field(default_factory=LoggingCfg)
    views: list[dict] = field(default_factory=list)      # custom views (daq_views.py)
    logging_profiles: list[dict] = field(default_factory=list)
    alarms: list[dict] = field(default_factory=list)            # daq_alarms.py
    alarm_profiles: list[dict] = field(default_factory=list)    # notification targets
    alarm_settings: dict = field(default_factory=lambda: {"sound": True, "front": False})
    variables: list[dict] = field(default_factory=list)         # calculated channels (variables.py)
    quick_hidden: list = field(default_factory=list)            # [[topic_id, index]] not plotted in Quick View
    ml_log: dict = field(default_factory=mlg.new_settings)   # AI/ML data log (core/ml_log.py)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        topics = [TopicCfg(**{**t, "channels": [ChannelCfg(**c) for c in t.get("channels", [])]})
                  for t in d.get("topics", [])]
        p = cls(name=d.get("name", "Untitled project"), operator=d.get("operator", ""),
                description=d.get("description", ""), topics=topics,
                logging=LoggingCfg(**d.get("logging", {})), views=d.get("views", []),
                logging_profiles=d.get("logging_profiles", []), alarms=d.get("alarms", []),
                alarm_profiles=d.get("alarm_profiles", []),
                alarm_settings={**cls().alarm_settings, **d.get("alarm_settings", {})},
                variables=d.get("variables", []), quick_hidden=d.get("quick_hidden", []),
                ml_log={**mlg.new_settings(), **d.get("ml_log", {})})
        if "logging_profiles" not in d:          # v5 file: its one format becomes a profile
            p.logging_profiles = [{**new_log_profile("signal"), "format": p.logging.format,
                                   "keep_raw": p.logging.keep_raw}]
        if "alarms" not in d:                    # v5 file: channel limits -> Level 1 / 2
            p.alarm_profiles = p.alarm_profiles or [ar.new_profile("popup", "Popup + sound")]
            p.alarms = ar.migrate_channel_limits(p.topics)
            for a in p.alarms:
                a["profiles"] = [x["id"] for x in p.alarm_profiles]
        for a in p.alarms:
            ar.normalize_alarm(a)
        for x in p.alarm_profiles:
            ar.normalize_profile(x)
        for x in p.logging_profiles:
            for k, v in new_log_profile(x.get("type", "signal")).items():
                x.setdefault(k, v)
        for x in p.variables:
            vr.normalize_variable(x)
        p.views = [y for x in p.views for y in split_tabs(x)]   # early v7 tabs -> separate views
        for x in p.views:                        # v6 views: add the view / tile properties
            normalize_view(x)
        return p

    @classmethod
    def default(cls) -> "Project":
        return cls(name="Demo project", topics=[TopicCfg(
            "Simulator", "spexth/daq/demo/wave",
            channels=[ChannelCfg(i, f"CH{i + 1}") for i in range(4)])],
            logging_profiles=[new_log_profile("signal"),
                              {**new_log_profile("statistic", "Statistics 1 min"), "enabled": False}],
            alarm_profiles=[ar.new_profile("popup", "Popup + sound")])


def new_channels(n: int) -> list[ChannelCfg]:
    return [ChannelCfg(i, f"CH{i + 1}") for i in range(n)]
