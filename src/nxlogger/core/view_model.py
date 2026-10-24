"""
View (custom screen) data model: plain dicts saved in the project file, no Qt.

A view is a grid of rows x columns (like a GA10 / FUXA screen layout):
  rows / cols   [{"size": n, "fixed": bool}]  fixed = n pixels, otherwise a weight (n parts of
                the free space, at least MIN_CELL px)
  tiles         components placed in a cell: {"row", "col", "rowspan", "colspan", "kind", ...}
                kinds: header / text, graph (time, FFT, STFT, histogram), numeric, gauge,
                statistics table, alarm table, tag table, LED indicator
Rows and columns can be added, inserted and deleted; a cell holds at most one component,
which can span several cells. Views of older versions (free-form x / y / w / h canvas)
become grids in normalize_view.

Statistics windows (numeric / gauge / tables): win_n + win_unit ("s", "min", "h",
"samples") + win_mode ("moving" | "block"); win_n = 0 uses the Quick View time window.
The window is buffered by the app itself (core/stat_buffer.py), independent of the plots.
"""
from __future__ import annotations

import uuid

from nxlogger.core.analysis import STAT_KEYS, STAT_LABEL


MAX_TILES = 36                             # per view: every tile redraws live, keep views fast
MAX_GRAPHS = 8                             # graphs (plots / spectra) per view - the costly tiles
MAX_ROWS = MAX_COLS = 24
MIN_CELL = 60                              # px: smallest weighted row / column
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

VIEW_DEFAULTS = {"bg": "", "tile_bg": "", "tile_border": "", "title_bar": True, "margin": 10,
                 "gap": 8, "layout": "grid", "rows": None, "cols": None}
TILE_DEFAULTS = {"title": "", "show_title": True, "card_bg": "", "border_color": "",
                 "row": 0, "col": 0, "rowspan": 1, "colspan": 1,
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
# grid cells a new component takes by default (colspan, rowspan)
DEFAULT_SPAN = {"plot": (2, 2), "stats": (2, 1), "alarms": (2, 1), "tags": (2, 1)}


def new_axis(n: int, size: float = 1.0, fixed: bool = False) -> list[dict]:
    return [{"size": size, "fixed": fixed} for _ in range(n)]


def new_view(name: str, rows: int = 3, cols: int = 4) -> dict:
    return {"id": uuid.uuid4().hex[:8], "name": name, "tiles": [], **VIEW_DEFAULTS,
            "rows": new_axis(rows), "cols": new_axis(cols)}


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


def _bands(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping (start, end) intervals -> sorted bands."""
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a < out[-1][1] - 10:                    # overlaps the band (10 px slack)
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _freeform_to_grid(v: dict):
    """v6 / v7 canvas (x, y, w, h per tile) -> rows by vertical band, columns per row."""
    tiles = v.get("tiles", [])
    if not tiles:
        v["rows"], v["cols"] = new_axis(3), new_axis(4)
        return
    rows = _bands([(t.get("y", 0), t.get("y", 0) + t.get("h", 100)) for t in tiles])
    per_row = [[t for t in tiles if a - 10 <= t.get("y", 0) < b] for a, b in rows]
    ncols = max(1, max(len(r) for r in per_row))
    v["rows"] = [{"size": max(MIN_CELL, int(b - a)), "fixed": all(t.get("kind") == "text" for t in r)}
                 for (a, b), r in zip(rows, per_row)]
    for row in v["rows"]:
        if not row["fixed"]:
            row["size"] = round(row["size"] / 100, 2)       # px -> weight
    v["cols"] = new_axis(ncols)
    for r, members in enumerate(per_row):
        members.sort(key=lambda t: t.get("x", 0))
        for c, t in enumerate(members):
            t.update(row=r, col=c, rowspan=1,
                     colspan=(ncols - c) if c == len(members) - 1 else 1)   # last one fills the row
    for t in tiles:
        for k in ("x", "y", "w", "h"):
            t.pop(k, None)
    for k in ("grid", "show_grid", "snap"):
        v.pop(k, None)


def normalize_view(v: dict) -> dict:
    if "pages" in v:                           # tabs inside one view: merge (split_tabs keeps them apart)
        v["tiles"] = list(v.get("tiles", [])) + [t for pg in v.pop("pages") for t in pg.get("tiles", [])]
    v.pop("page", None)
    if v.get("layout") != "grid":
        _freeform_to_grid(v)
        v["layout"] = "grid"
    for k, d in VIEW_DEFAULTS.items():
        v.setdefault(k, d)
    v["rows"] = v["rows"] or new_axis(3)
    v["cols"] = v["cols"] or new_axis(4)
    for t in v.setdefault("tiles", []):
        normalize_tile(t)
    clamp_tiles(v)
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


def new_tile(kind: str, row: int = 0, col: int = 0, **over) -> dict:
    cs, rs = DEFAULT_SPAN.get(kind, (1, 1))
    t = {"id": uuid.uuid4().hex[:8], "kind": kind, "row": row, "col": col, "rowspan": rs, "colspan": cs}
    t.update(over)
    return normalize_tile(t)


# ====================================================================== grid editing
def cells_of(t: dict) -> set[tuple[int, int]]:
    return {(r, c) for r in range(t["row"], t["row"] + t["rowspan"])
            for c in range(t["col"], t["col"] + t["colspan"])}


def occupied(v: dict, skip: dict | None = None) -> dict[tuple[int, int], dict]:
    out = {}
    for t in v["tiles"]:
        if t is not skip:
            for cell in cells_of(t):
                out[cell] = t
    return out


def tile_at(v: dict, row: int, col: int) -> dict | None:
    return occupied(v).get((row, col))


def fits(v: dict, t: dict, row: int, col: int, rowspan: int, colspan: int) -> bool:
    """Would `t` fit at row/col with this span (inside the grid, no other component)?"""
    if row < 0 or col < 0 or rowspan < 1 or colspan < 1:
        return False
    if row + rowspan > len(v["rows"]) or col + colspan > len(v["cols"]):
        return False
    taken = occupied(v, skip=t)
    return not any((r, c) in taken for r in range(row, row + rowspan) for c in range(col, col + colspan))


def fit_span(v: dict, t: dict, row: int, col: int, want_cs: int, want_rs: int) -> tuple[int, int] | None:
    """Largest span <= wanted that fits at row/col, or None if not even one cell is free."""
    for rs in range(want_rs, 0, -1):
        for cs in range(want_cs, 0, -1):
            if fits(v, t, row, col, rs, cs):
                return cs, rs
    return None


def free_cell(v: dict, want_cs: int = 1, want_rs: int = 1) -> tuple[int, int, int, int] | None:
    """First free cell (row-major) -> (row, col, colspan, rowspan), or None if the grid is full."""
    taken = occupied(v)
    for r in range(len(v["rows"])):
        for c in range(len(v["cols"])):
            if (r, c) not in taken:
                span = fit_span(v, None, r, c, want_cs, want_rs)
                if span:
                    return r, c, *span
    return None


def clamp_tiles(v: dict):
    """Make the components fit the grid (file edited by hand, older version): shrink spans
    at the edge; a component without room moves to a free cell - a row is added if the grid
    is full, so nothing is ever dropped."""
    nr, nc = len(v["rows"]), len(v["cols"])
    keep = list(v["tiles"])
    for t in keep:
        t["row"], t["col"] = max(0, min(int(t["row"]), nr - 1)), max(0, min(int(t["col"]), nc - 1))
        t["rowspan"] = max(1, min(int(t["rowspan"]), nr - t["row"]))
        t["colspan"] = max(1, min(int(t["colspan"]), nc - t["col"]))
    v["tiles"] = []                                       # re-add: a later one may overlap now
    for t in keep:
        span = fit_span(v, t, t["row"], t["col"], t["colspan"], t["rowspan"])
        if span is None:
            place = free_cell(v, t["colspan"], 1)
            if place is None:
                v["rows"].append({"size": 1.0, "fixed": False})
                place = free_cell(v, t["colspan"], 1)
            t["row"], t["col"] = place[0], place[1]
            span = place[2], place[3]
        t["colspan"], t["rowspan"] = span
        v["tiles"].append(t)


def insert_line(v: dict, axis: str, at: int):
    """Insert a row (axis 'rows') or column ('cols') before index `at`; components move along
    and a component crossing the new line gets wider / taller."""
    lines = v[axis]
    at = max(0, min(at, len(lines)))
    lines.insert(at, {"size": 1.0, "fixed": False})
    pos, span = ("row", "rowspan") if axis == "rows" else ("col", "colspan")
    for t in v["tiles"]:
        if t[pos] >= at:
            t[pos] += 1
        elif t[pos] + t[span] > at:
            t[span] += 1


def delete_line(v: dict, axis: str, at: int) -> list[dict]:
    """Delete a row / column -> the components that were only in it (removed)."""
    lines = v[axis]
    if len(lines) <= 1 or not 0 <= at < len(lines):
        return []
    pos, span = ("row", "rowspan") if axis == "rows" else ("col", "colspan")
    gone = [t for t in v["tiles"] if t[pos] == at and t[span] == 1]
    v["tiles"] = [t for t in v["tiles"] if t not in gone]
    for t in v["tiles"]:
        if t[pos] > at:
            t[pos] -= 1
        elif t[pos] <= at < t[pos] + t[span]:
            t[span] -= 1
    del lines[at]
    return gone


def win_text(t: dict) -> str:
    """'mean · 10 min (block)' style description of a statistics window."""
    n = float(t.get("win_n") or 0)
    if not n:
        return "Quick View window"
    unit = t.get("win_unit", "s")
    txt = f"{n:g} {unit}" if unit != "samples" else f"{int(n)} samples"
    return txt + (" block" if t.get("win_mode") == "block" else "")


def default_view(name: str, keys: list[tuple[str, int]]) -> dict:
    """A starter view: header, a numeric per tag, a trend of all of them, a statistics table."""
    n = max(1, min(4, len(keys)))
    v = new_view(name, rows=4, cols=n)
    v["rows"] = [{"size": 56, "fixed": True}, {"size": 120, "fixed": True}, {"size": 3.0, "fixed": False},
                 {"size": 1.6, "fixed": False}]
    v["tiles"] = [new_tile("text", 0, 0, colspan=n, text=name, style="H1", show_title=False)]
    for i, k in enumerate(keys[:n]):
        v["tiles"].append(new_tile("value", 1, i, channel=list(k), decimals=2))
    v["tiles"].append(new_tile("plot", 2, 0, colspan=n, rowspan=1, channels=[list(k) for k in keys],
                               title="Trend"))
    v["tiles"].append(new_tile("stats", 3, 0, colspan=n, rowspan=1, channels=[list(k) for k in keys],
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
