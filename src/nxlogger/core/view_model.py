"""
View (custom screen) data model: plain dicts saved in the project file, no Qt.

A view is a free-form canvas: every component has x / y / w / h in pixels and snaps to the
view's grid (default 10 px) while it is moved or resized. Components: header / text, graph
(trend, FFT, STFT, histogram), number, gauge, LED, statistics / alarm / tag table.
Views saved as grids (NX Logger 2.0 grid layout: rows / cols / row / col / spans) are
converted to pixel positions in normalize_view.

Statistics windows (number / gauge / LED / statistics table): win_n + win_unit ("s", "min",
"h", "samples") + win_mode ("moving" | "block"); win_n = 0 uses the Quick View time window.
The window is buffered by the app itself (core/stat_buffer.py), independent of the plots.
"""
from __future__ import annotations

import uuid

from nxlogger.core.analysis import STAT_KEYS, STAT_LABEL


GRID = 10                                  # default snap grid (px); a view can change it
MIN_W, MIN_H = 120, 50
MAX_TILES = 36                             # per view: every tile redraws live, keep views fast
MAX_GRAPHS = 8                             # graphs (plots / spectra) per view - the costly tiles
DEFAULT_SIZE = {"plot": (520, 300), "text": (420, 60), "value": (200, 110), "gauge": (240, 220),
                "led": (160, 140), "stats": (620, 200), "alarms": (720, 240), "tags": (420, 200)}
TEXT_STYLES = ["H1", "H2", "H3", "Body"]
STAT_TILE_COLS = [STAT_LABEL[k] for k in STAT_KEYS]          # Value, Mean, Min, Max, SD, RMS ...
STAT_TILE_DEFAULT = ["Value", "Mean", "Min", "Max"]
ALARM_TILE_COLS = ["Time", "Level", "State", "Alarm", "Tag", "Type", "Value", "Setpoint"]
TAG_TILE_COLS = ["Tag", "Value", "Unit", "Quality", "Time", "Device", "Description"]
TAG_TILE_DEFAULT = ["Tag", "Value", "Unit", "Quality"]
ALARM_SOURCES = {"active": "Active alarms", "log": "Alarm log (newest first)"}
KIND_TITLE = {"plot": "Graph", "text": "Text", "value": "Numeric indicator", "gauge": "Gauge",
              "stats": "Statistics table", "alarms": "Alarm table", "tags": "Tag table",
              "led": "LED indicator"}
WIN_UNITS = {"s": ("seconds", 1.0), "min": ("minutes", 60.0), "h": ("hours", 3600.0),
             "samples": ("samples", None)}
WIN_MODES = {"moving": "Moving window (updates every scan)",
             "block": "Block (holds the result of the last full window)"}

# graph types of a plot tile: (menu label, default X label, X unit)
PLOT_TYPES = {"time": ("Time plot (trend)", "Time", "s"),
              "fft": ("FFT spectrum", "Frequency", "Hz"),
              "stft": ("Spectrogram (STFT)", "Time", "s"),
              "hist": ("Histogram (amplitude distribution)", "Value", "")}
GAUGE_STYLES = {"basic": "Basic needle", "speed": "Speed (arc + needle)", "progress": "Progress arc",
                "stage": "Stage (coloured zones)", "temperature": "Temperature (thick arc)",
                "ring": "Ring"}
COLORMAPS = ["viridis", "inferno", "plasma", "magma", "cividis", "turbo"]

VIEW_DEFAULTS = {"bg": "", "grid": GRID, "show_grid": True, "snap": True, "layout": "free",
                 "tile_bg": "", "tile_border": "", "title_bar": True, "margin": 10}
TILE_DEFAULTS = {"title": "", "show_title": True, "card_bg": "", "border_color": "",
                 "x": 10, "y": 10, "w": 200, "h": 110,
                 "channels": [], "channel": None, "decimals": 3, "text": "Heading", "style": "H1",
                 "align": "left",
                 # graph
                 "plot": "time", "stack": False, "legend": True, "grid": True, "axis_labels": True,
                 "x_label": "", "y_label": "", "x_mode": "time", "x_samples": 1000, "y_auto": True, "y_min": -10.0, "y_max": 10.0,
                 "log_x": False, "log_y": False, "plot_bg": "", "area_bg": "", "line_width": 1.5,
                 "span_s": 0.0,
                 "fft_window": "hann", "fft_scale": "rms", "fft_nfft": 4096, "fft_avg": 1,
                 "fft_fmax": 0.0, "stft_nperseg": 512, "stft_overlap": 50, "stft_cmap": "viridis",
                 "stft_span_s": 10.0, "hist_bins": 50, "hist_density": True, "hist_fit": True,
                 "hist_stats": True, "hist_span_s": 0.0,
                 # numeric / gauge / tables: statistics window (stat_buffer.py)
                 "quantity": "value", "window_s": 0.0, "win_n": 0.0, "win_unit": "s", "win_mode": "moving",
                 "value_size": 30, "value_color": "",
                 "unit": "", "gauge_style": "speed", "g_min": 0.0, "g_max": 100.0,
                 "g_warn": None, "g_alarm": None, "g_ticks": 10, "g_color": "",
                 # LED: on when value <op> threshold
                 "led_op": ">", "led_value": 0.5, "led_on": "", "led_off": "", "led_on_text": "ON",
                 "led_off_text": "OFF",
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


def _grid_to_freeform(v: dict, width: int = 1200):
    """A view saved as a grid (rows / cols, row / col / spans) -> pixel positions."""
    rows, cols = v.pop("rows", None) or [], v.pop("cols", None) or []
    gap, m = int(v.pop("gap", 8)), int(v.get("margin", 10))
    if not rows or not cols:
        return

    def sizes(lines, total):
        fixed = sum(int(x["size"]) for x in lines if x.get("fixed"))
        weights = sum(float(x["size"]) for x in lines if not x.get("fixed")) or 1.0
        free = max(0, total - fixed - gap * (len(lines) - 1))
        return [int(x["size"]) if x.get("fixed") else max(60, int(free * float(x["size"]) / weights))
                for x in lines]
    cw = sizes(cols, width - 2 * m)
    rh = sizes(rows, max(600, 160 * len(rows)))
    cx = [m + sum(cw[:i]) + gap * i for i in range(len(cw))]
    ry = [m + sum(rh[:i]) + gap * i for i in range(len(rh))]
    g = int(v.get("grid", GRID)) or GRID
    snap = lambda n: int(round(n / g) * g)                     # noqa: E731
    for t in v.get("tiles", []):
        r, c = min(int(t.pop("row", 0)), len(rows) - 1), min(int(t.pop("col", 0)), len(cols) - 1)
        rs = max(1, min(int(t.pop("rowspan", 1)), len(rows) - r))
        cs = max(1, min(int(t.pop("colspan", 1)), len(cols) - c))
        t.update(x=snap(cx[c]), y=snap(ry[r]), w=snap(sum(cw[c:c + cs]) + gap * (cs - 1)),
                 h=snap(sum(rh[r:r + rs]) + gap * (rs - 1)))


def normalize_view(v: dict) -> dict:
    if "pages" in v:                           # tabs inside one view: merge (split_tabs keeps them apart)
        v["tiles"] = list(v.get("tiles", [])) + [t for pg in v.pop("pages") for t in pg.get("tiles", [])]
    v.pop("page", None)
    if v.get("layout") == "grid":
        _grid_to_freeform(v)
    v["layout"] = "free"
    for k in ("rows", "cols", "gap"):
        v.pop(k, None)
    for k, d in VIEW_DEFAULTS.items():
        v.setdefault(k, d)
    for t in v.setdefault("tiles", []):
        normalize_tile(t)
    return v


def is_graph(t: dict) -> bool:
    return t.get("kind") == "plot"


def tile_title(t: dict) -> str:
    """Dialog / tooltip name of a tile: graphs by their type."""
    if is_graph(t):
        return PLOT_TYPES.get(t.get("plot", "time"), PLOT_TYPES["time"])[0]
    if t.get("kind") == "text" and t.get("style", "H1") != "Body":
        return "Header"
    return KIND_TITLE.get(t.get("kind"), "Component")


def normalize_tile(t: dict) -> dict:
    for k, d in TILE_DEFAULTS.items():
        t.setdefault(k, list(d) if isinstance(d, list) else d)
    if t.get("columns") is None:
        t["columns"] = list({"stats": STAT_TILE_DEFAULT, "tags": TAG_TILE_DEFAULT}.get(t.get("kind"),
                                                                                     ALARM_TILE_COLS))
    if t.get("window_s") and not t.get("win_n"):           # v6 / v7: seconds only
        t["win_n"], t["win_unit"] = float(t["window_s"]), "s"
        t["window_s"] = 0.0
    return t


def new_tile(kind: str, x: int = 10, y: int = 10, **over) -> dict:
    w, h = DEFAULT_SIZE.get(kind, (200, 110))
    t = {"id": uuid.uuid4().hex[:8], "kind": kind, "x": x, "y": y, "w": w, "h": h}
    t.update(over)
    return normalize_tile(t)


def win_text(t: dict) -> str:
    """'mean · 10 min (block)' style description of a statistics window."""
    n = float(t.get("win_n") or 0)
    if not n:
        return "Quick View window"
    unit = t.get("win_unit", "s")
    txt = f"{n:g} {unit}" if unit != "samples" else f"{int(n)} samples"
    return txt + (" block" if t.get("win_mode") == "block" else "")


def default_view(name: str, keys: list[tuple[str, int]]) -> dict:
    """A starter view: header, a number per tag, a trend of all of them, a statistics table."""
    v = new_view(name)
    n = max(1, min(4, len(keys)))
    v["tiles"] = [new_tile("text", 10, 10, w=n * 220 - 20, h=60, text=name, style="H1", show_title=False)]
    for i, k in enumerate(keys[:n]):
        v["tiles"].append(new_tile("value", 10 + i * 220, 80, w=200, h=110, channel=list(k), decimals=2))
    v["tiles"].append(new_tile("plot", 10, 210, w=n * 220 - 20, h=300, channels=[list(k) for k in keys],
                               title="Trend"))
    v["tiles"].append(new_tile("stats", 10, 530, w=n * 220 - 20, h=190, channels=[list(k) for k in keys],
                               win_n=1.0, win_unit="min"))
    return v


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
