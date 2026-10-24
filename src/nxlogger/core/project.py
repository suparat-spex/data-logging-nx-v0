"""Project model (.nxl JSON; .sxl / .mqdaq open too): devices + tags, logging profiles, alarms,
variables, views. No Qt.

File versions
  1  MQDAQ / SX Logger (.mqdaq, .sxl): "topics" = MQTT wavepacket streams with channels
  2  NX Logger (.nxl): "devices" with tags (core/tags.py), grid views (core/view_model.py)
Older files are converted when opened: every topic becomes an MQTT device in wavepacket
mode with the same id, so alarms, variables, views and logging profiles still point at it.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

from nxlogger import paths
from nxlogger.core import alarm_rules as ar
from nxlogger.core import ml_log as mlg
from nxlogger.core import variables as vr
from nxlogger.core.tags import (COLORS, ChannelCfg, DeviceCfg, TagCfg, device_from_dict,  # noqa: F401
                                device_from_topic, new_device, tag_color)
from nxlogger.core.view_model import default_view, normalize_view, split_tabs


FILE_FORMAT, FILE_VERSION = "nxlogger", 2
PROJECT_EXT = ".nxl"
OLD_PROJECT_EXTS = (".sxl", ".mqdaq")      # SX Logger / MQDAQ v6 files still open
ch_color = tag_color


@dataclass
class TopicCfg:
    """v1 file format only (an MQTT wavepacket topic) - read, then turned into a device."""
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
    keep_raw: bool = False        # v6+ uses Project.logging_profiles


STAT_INTERVALS = [("1 s", 1), ("10 s", 10), ("1 min", 60), ("10 min", 600), ("1 h", 3600),
                  ("1 day", 86400)]


def new_log_profile(kind: str = "signal", name: str = "") -> dict:
    """Logging profile: what to write each Record run (see recording.Recorder)."""
    return {"id": uuid.uuid4().hex[:8], "name": name or ("Data" if kind == "signal" else "Statistics"),
            "enabled": True, "type": kind, "format": "CSV", "keep_raw": False,
            "channels": None,                   # None = all enabled tags, else [[device_id, index]]
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
    devices: list[DeviceCfg] = field(default_factory=list)
    logging: LoggingCfg = field(default_factory=LoggingCfg)
    views: list[dict] = field(default_factory=list)      # grid views (view_model.py)
    logging_profiles: list[dict] = field(default_factory=list)
    alarms: list[dict] = field(default_factory=list)            # alarm_rules.py
    alarm_profiles: list[dict] = field(default_factory=list)    # notification targets
    alarm_settings: dict = field(default_factory=lambda: {"sound": True, "front": False})
    variables: list[dict] = field(default_factory=list)         # calculated channels (variables.py)
    quick_hidden: list = field(default_factory=list)            # [[device_id, index]] not in Quick View
    ml_log: dict = field(default_factory=mlg.new_settings)   # AI/ML data log (core/ml_log.py)
    migrated_from: str = ""                                  # "" or the old format (not saved)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("migrated_from")
        return {"format": FILE_FORMAT, "version": FILE_VERSION, **d}

    def device(self, did: str) -> DeviceCfg | None:
        return next((d for d in self.devices if d.id == did), None)

    def tag(self, did: str, idx: int) -> TagCfg | None:
        d = self.device(did)
        return d.tag(idx) if d else None

    @classmethod
    def from_dict(cls, d: dict, broker: dict | None = None) -> "Project":
        """`broker`: settings for MQTT devices made from v1 topics (host, port, ...)."""
        migrated = ""
        if "devices" in d:
            devices = [device_from_dict(x) for x in d["devices"]]
        else:                                    # v1: topics -> MQTT wavepacket devices
            topics = [TopicCfg(**{**t, "channels": [ChannelCfg(**c) for c in t.get("channels", [])]})
                      for t in d.get("topics", [])]
            migrated = "v1"
            if "alarms" not in d:                # v5 file: channel limits -> Level 1 / 2
                d = {**d, "alarms": ar.migrate_channel_limits(topics), "_v5": True}
            devices = [device_from_topic(t, broker or {}) for t in topics]
        p = cls(name=d.get("name", "Untitled project"), operator=d.get("operator", ""),
                description=d.get("description", ""), devices=devices,
                logging=LoggingCfg(**{k: v for k, v in d.get("logging", {}).items()
                                      if k in LoggingCfg.__dataclass_fields__}),
                views=d.get("views", []),
                logging_profiles=d.get("logging_profiles", []), alarms=d.get("alarms", []),
                alarm_profiles=d.get("alarm_profiles", []),
                alarm_settings={**cls().alarm_settings, **d.get("alarm_settings", {})},
                variables=d.get("variables", []), quick_hidden=d.get("quick_hidden", []),
                ml_log={**mlg.new_settings(), **d.get("ml_log", {})}, migrated_from=migrated)
        if "logging_profiles" not in d:          # v5 file: its one format becomes a profile
            p.logging_profiles = [{**new_log_profile("signal", "Waveform"), "format": p.logging.format,
                                   "keep_raw": p.logging.keep_raw}]
        if d.get("_v5"):
            p.alarm_profiles = p.alarm_profiles or [ar.new_profile("popup", "Popup + sound")]
            for a in p.alarms:
                a["profiles"] = [x["id"] for x in p.alarm_profiles]
        for a in p.alarms:
            ar.normalize_alarm(a)
        for x in p.alarm_profiles:
            ar.normalize_profile(x)
        for x in p.logging_profiles:
            for k, v in new_log_profile(x.get("type", "signal")).items():
                x.setdefault(k, v)
            if x["format"] not in ("CSV", "WPK"):    # TDMS is no longer written
                x["format"] = "CSV"
        p.logging.format = "CSV" if p.logging.format == "TDMS" else p.logging.format
        for x in p.variables:
            vr.normalize_variable(x)
        p.views = [y for x in p.views for y in split_tabs(x)]   # early v7 tabs -> separate views
        for x in p.views:                        # free-form views -> grid, new tile properties
            normalize_view(x)
        return p

    @classmethod
    def default(cls) -> "Project":
        sim = new_device("sim", "Simulator")
        p = cls(name="Demo project", devices=[sim],
                logging_profiles=[new_log_profile("signal"),
                                  {**new_log_profile("statistic", "Statistics 1 min"), "enabled": False}],
                alarm_profiles=[ar.new_profile("popup", "Popup + sound")])
        p.views = [default_view("Overview", [(sim.id, t.index) for t in sim.tags])]
        return p
