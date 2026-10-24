"""
config.py - settings from config.ini (see docs/config.md).

Order: built-in defaults (resources/config.default.ini) -> config.ini -> what the user
changes in the app (QSettings remembers that, and wins). config.ini is for defaults a
person or an installer sets by hand: broker, theme, folders.
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field

from nxlogger import paths


@dataclass
class Config:
    app: dict = field(default_factory=dict)
    mqtt: dict = field(default_factory=dict)
    paths: dict = field(default_factory=dict)
    source: str = ""                          # file the values came from (for the log / About)

    def mqtt_int(self, key: str, default: int) -> int:
        try:
            return int(self.mqtt.get(key, default))
        except ValueError:
            return default


_cfg: Config | None = None


def load() -> Config:
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"), interpolation=None)
    cp.read(paths.resource("config.default.ini"), encoding="utf-8")
    f = paths.config_file()
    read = cp.read(f, encoding="utf-8")
    sec = lambda s: dict(cp[s]) if cp.has_section(s) else {}
    return Config(app=sec("app"), mqtt=sec("mqtt"), paths=sec("paths"), source=str(f) if read else "")


def get() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = load()
    return _cfg
