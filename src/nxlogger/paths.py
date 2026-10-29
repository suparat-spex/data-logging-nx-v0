"""
paths.py - where NX Logger finds its files, from source and as a packaged EXE.

                 from source (dev)                 installed EXE (PyInstaller)
  resources      src/nxlogger/resources/           <_MEIPASS>/nxlogger/resources/
  config.ini     <repo>/config.ini                 %APPDATA%/NX Logger/config.ini
  user data      Documents/NX Logger/              Documents/NX Logger/

Program Files is read-only for the app, so nothing is ever written next to the EXE.
The rest of the code asks this module for locations instead of using __file__.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from nxlogger import APP_NAME

FROZEN = getattr(sys, "frozen", False)


def resource_dir() -> Path:
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "nxlogger" / "resources"
    return Path(__file__).resolve().parent / "resources"


def resource(*parts: str) -> Path:
    return resource_dir().joinpath(*parts)


def repo_root() -> Path | None:
    """The source checkout (has pyproject.toml), or None when frozen / installed as a package."""
    if FROZEN:
        return None
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").exists() else None


def user_config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME


def config_file() -> Path:
    """The editable config.ini; created from resources/config.default.ini on first use."""
    root = repo_root()
    f = (root / "config.ini") if root else user_config_dir() / "config.ini"
    if not f.exists():
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(resource("config.default.ini"), f)
        except OSError:
            pass                              # read-only location: defaults still apply
    return f


def data_dir() -> Path:
    return Path.home() / "Documents" / APP_NAME


def _configured(key: str, default: Path) -> Path:
    from nxlogger import config               # lazy: config imports paths
    v = config.get().paths.get(key, "").strip()
    if not v:
        return default
    p = Path(os.path.expandvars(os.path.expanduser(v)))
    return p if p.is_absolute() else data_dir() / p


def recordings_dir() -> Path:
    return _configured("recordings", data_dir() / "Recordings")


def projects_dir() -> Path:
    return _configured("projects", data_dir() / "Projects")


def docs_dir() -> Path | None:
    """The user documentation (docs/*.md): the source checkout's docs/, or docs/ next to the
    program when packaged with it; None when there is none."""
    root = repo_root()
    for d in ((root / "docs") if root else None, Path(sys.executable).parent / "docs" if FROZEN else None):
        if d is not None and d.is_dir():
            return d
    return None
