"""View (custom screen) data model: plain dicts saved in the project file, no Qt."""
from __future__ import annotations

import uuid

from nxlogger.core.analysis import STAT_KEYS, STAT_LABEL


GRID = 10                                  # default snap grid (px); a view can change it
MIN_W, MIN_H = 120, 50
MAX_TILES = 24                             # per view: every tile redraws live, keep views fast
MAX_GRAPHS = 8                             # graphs (plots / spectra) per view - the costly tiles
TEXT_STYLES = ["H1", "H2", "H3", "Body"]
DEFAULT_SIZE = {"plot": (520, 300), "text": (420, 60), "value": (200, 110),
                "gauge": (240, 220), "stats": (620, 200), "alarms": (720, 240)}
STAT_TILE_COLS = [STAT_LABEL[k] for k in STAT_KEYS]          # Value, Mean, Min, Max, SD, RMS ...
STAT_TILE_DEFAULT = ["Value", "Mean", "Min", "Max"]
ALARM_TILE_COLS = ["Time", "Level", "State", "Alarm", "Tag", "Type", "Value", "Setpoint"]
ALARM_SOURCES = {"active": "Active alarms", "log": "Alarm log (newest first)"}
KIND_TITLE = {"plot": "Graph", "text": "Text", "value": "Numeric indicator", "gauge": "Gauge",
              "stats": "Statistics table", "alarms": "Alarm table"}

# graph types of a plot tile: (menu label, default X label, X unit)
PLOT_TYPES = {"time": ("Time plot (waveform)", "Time", "s"),
              "fft": ("FFT spectrum", "Frequency", "Hz"),
              "stft": ("Spectrogram (STFT)", "Time", "s"),
              "hist": ("Histogram (amplitude distribution)", "Value", "")}
GAUGE_STYLES = {"basic": "Basic needle", "speed": "Speed (arc + needle)", "progress": "Progress arc",
                "stage": "Stage (coloured zones)", "temperature": "Temperature (thick arc)",
                "ring": "Ring"}
COLORMAPS = ["viridis", "inferno", "plasma", "magma", "cividis", "turbo"]

VIEW_DEFAULTS = {"bg": "", "grid": GRID, "show_grid": True, "snap": True,
                 "tile_bg": "", "tile_border": "", "title_bar": True, "margin": 10}
TILE_DEFAULTS = {"title": "", "show_title": True, "card_bg": "", "border_color": "",
                 "channels": [], "channel": None, "decimals": 3, "text": "Heading", "style": "H1",
                 # graph
                 "plot": "time", "stack": False, "legend": True, "grid": True, "axis_labels": True,
                 "x_label": "", "y_label": "", "x_mode": "time", "x_samples": 1000, "y_auto": True, "y_min": -10.0, "y_max": 10.0,
                 "log_x": False, "log_y": False, "plot_bg": "", "area_bg": "", "line_width": 1.5,
                 "fft_window": "hann", "fft_scale": "rms", "fft_nfft": 4096, "fft_avg": 1,
                 "fft_fmax": 0.0, "stft_nperseg": 512, "stft_overlap": 50, "stft_cmap": "viridis",
                 "stft_span_s": 10.0, "hist_bins": 50, "hist_density": True, "hist_fit": True,
                 "hist_stats": True, "hist_span_s": 0.0,
                 # numeric / gauge
                 "quantity": "value", "window_s": 0.0, "value_size": 30, "value_color": "",
                 "unit": "", "gauge_style": "speed", "g_min": 0.0, "g_max": 100.0,
                 "g_warn": None, "g_alarm": None, "g_ticks": 10, "g_color": "",
                 # tables
                 "columns": None, "source": "active", "max_rows": 10, "levels": [1, 2, 3, 4]}


def new_view(name: str) -> dict:
    return {"id": uuid.uuid4().hex[:8], "name": name, "tiles": [], **VIEW_DEFAULTS}


def split_tabs(v: dict) -> list[dict]:
    """A view saved with tabs (early v7) -> one view per tab ("<view> - <tab>")."""
    pages = v.pop("pages", None)
    v.pop("page", None)
    if not pages:
        return [v]
    v["tiles"] = list(v.get("tiles", [])) + list(pages[0].get("tiles", []))
    out = [v]
    for pg in pages[1:]:
        extra = {k: x for k, x in v.items() if k not in ("id", "name", "tiles")}
        out.append({**extra, "id": uuid.uuid4().hex[:8], "name": f"{v['name']} - {pg.get('name', 'Tab')}",
                    "tiles": list(pg.get("tiles", []))})
    return out


def normalize_view(v: dict) -> dict:
    if "pages" in v:                           # tabs inside one view: merge (split_tabs keeps them apart)
        v["tiles"] = list(v.get("tiles", [])) + [t for pg in v.pop("pages") for t in pg.get("tiles", [])]
    v.pop("page", None)
    for k, d in VIEW_DEFAULTS.items():
        v.setdefault(k, d)
    for t in v.setdefault("tiles", []):
        normalize_tile(t)
    return v


def is_graph(t: dict) -> bool:
    return t.get("kind") == "plot"


def tile_title(t: dict) -> str:
    """Dialog / tooltip name of a tile: graphs by their type."""
    return PLOT_TYPES.get(t.get("plot", "time"), PLOT_TYPES["time"])[0] if is_graph(t)         else KIND_TITLE.get(t.get("kind"), "Tile")


def normalize_tile(t: dict) -> dict:
    for k, d in TILE_DEFAULTS.items():
        t.setdefault(k, list(d) if isinstance(d, list) else d)
    if t.get("columns") is None:
        t["columns"] = list(STAT_TILE_DEFAULT if t.get("kind") == "stats" else ALARM_TILE_COLS)
    return t


def new_tile(kind: str, x: int, y: int, **over) -> dict:
    w, h = DEFAULT_SIZE[kind]
    t = {"id": uuid.uuid4().hex[:8], "kind": kind, "x": x, "y": y, "w": w, "h": h}
    t.update(over)
    return normalize_tile(t)


def axis_defaults(cfg: dict, unit: str = "") -> tuple[str, str]:
    """Default (X label, Y label) of a graph from its type and the channel unit."""
    kind = cfg.get("plot", "time")
    x = PLOT_TYPES.get(kind, PLOT_TYPES["time"])
    xl = f"{x[1]} ({x[2]})" if x[2] else x[1]
    if kind == "time" and cfg.get("x_mode") == "samples":
        xl = "Sample"
    if kind == "fft":
        sc = cfg.get("fft_scale", "rms")
        y = {"peak": f"Amplitude, peak ({unit})" if unit else "Amplitude, peak",
             "rms": f"Amplitude, RMS ({unit})" if unit else "Amplitude, RMS",
             "db": "Level (dB)", "psd": f"PSD ({unit}²/Hz)" if unit else "PSD (1/Hz)"}[sc]
    elif kind == "stft":
        y = "Frequency (Hz)"
    elif kind == "hist":
        y = "Probability density" if cfg.get("hist_density", True) else "Count"
        xl = f"Value ({unit})" if unit else "Value"
    else:
        y = f"Value ({unit})" if unit else "Value"
    return xl, y
