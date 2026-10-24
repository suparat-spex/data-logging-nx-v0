"""
views.py - user-built "views" (screens) for NX Logger: a grid of rows x columns.

A view is a grid (GA10 / FUXA style screen layout). Each cell holds at most one
component, which can span several cells:
  Header / text   heading H1 / H2 / H3 or body text
  Graph           time plot (trend), FFT spectrum, spectrogram (STFT) or histogram
  Indicator       numeric readout, gauge (6 styles) or LED of one tag: the value or a
                  statistic (mean, RMS, SD ...) over a window buffered by the app
                  (seconds / minutes / hours / samples, moving or block - stat_buffer.py)
  Table           statistics table, alarm table, tag table (value, quality, time)

Edit mode shows the grid: row / column handles (click = select, menu = insert / delete /
size), empty cells with a + button, mock-up data in every component (no live updates, so
arranging a big view stays fast). Click selects (the Properties panel edits the
selection), drag a component to another cell, double-click opens all its settings. Live
mode locks the layout. Only the visible view is drawn. Views are plain dicts
(core/view_model.py) so the project file stores them as JSON.

The page talks to the main window through a small `host` object:
  host.channel_choices() -> [(device_id, index, label, colour, unit)]   (tags + variables)
  host.series(device_id, index) -> (t, y) numpy arrays or None
  host.last_value(device_id, index) -> (value or None, unit)
  host.view_changed()          mark project dirty
  host.reg_icon(target, name, token="text")
  host.view_properties(page) / host.delete_view(page)
optional (offered only when the host has them):
  host.stat_values(device_id, index, window, keys=None) -> {"Value", "Mean", ...} or None
        window: a tile dict (win_n / win_unit / win_mode) or None (Quick View window)
  host.alarm_rows(source, max_rows, levels) -> [{column: text, "_level": n}]
  host.alarm_seq() -> int that changes whenever the alarm data changes
  host.tag_rows(keys) -> [{column: text, "_good": bool}]   (tag table)
  host.view_selected(page, selection)   selection: ("tile", cfg) | ("cell", r, c) |
                                        ("row", i) | ("col", i) | None
"""
from __future__ import annotations

import collections
import copy
import html
import math
import time

import numpy as np
from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QMenu, QMessageBox, QRubberBand, QScrollArea,
                               QSizePolicy, QSpacerItem, QTableWidget, QTableWidgetItem, QToolButton,
                               QVBoxLayout, QWidget)
import pyqtgraph as pg

from nxlogger.core import analysis as an
from nxlogger.core import view_model as vm
from nxlogger.core.view_model import (ALARM_SOURCES, ALARM_TILE_COLS, MAX_GRAPHS, MAX_TILES, MIN_CELL,
                                      PLOT_TYPES, STAT_TILE_COLS, TAG_TILE_COLS, axis_defaults, is_graph,
                                      new_tile, normalize_view, tile_title)
from nxlogger.ui.dialogs.tile_dialog import TileDialog
from nxlogger.ui.theme.ui_kit import T, label, set_prop
from nxlogger.ui.widgets.gauge import GaugeWidget


PLOT_BG = "#000000"          # plot area is black in both themes (data surface, not UI)
PLOT_FG = "#e6e6e6"
PLOT_LEGEND_BG = (20, 20, 20, 210)
LEVEL_TOKEN = {1: "bad", 2: "accent", 3: "warn", 4: "info"}
ANALYSIS_DT = 0.2            # s between spectrum / spectrogram / histogram updates
_COL_KEY = {an.STAT_LABEL[k]: k for k in an.STAT_KEYS}

# insert menus: (section, [(item text, icon, kind, preset)])
ADD_MENUS = [
    ("Text", [("Header", "heading", "text", {"style": "H1", "show_title": False}),
              ("Text", "type", "text", {"style": "Body", "text": "Text", "show_title": False})]),
    ("Graph", [("Trend (time plot)", "activity", "plot", {"plot": "time"}),
               ("FFT spectrum", "audio-waveform", "plot", {"plot": "fft"}),
               ("Spectrogram (STFT)", "grid", "plot", {"plot": "stft"}),
               ("Histogram", "chart-column", "plot", {"plot": "hist"})]),
    ("Indicator", [("Number", "hash", "value", {}),
                   ("Gauge", "gauge", "gauge", {}),
                   ("LED", "circle-dot", "led", {})]),
    ("Table", [("Tag table", "tag", "tags", {}),
               ("Statistics table", "sigma", "stats", {}),
               ("Alarm table", "bell", "alarms", {})]),
]


def _mock(i: int, k: int = 0) -> float:
    """Deterministic mock-up numbers for edit mode."""
    return round(10 * math.sin(1.3 * i + 0.7 * k) + 2.5 * k + 20, 3)


def style_plot(p: pg.PlotItem, rect_zoom: bool = True, area: str = "", grid: bool = True):
    """Shared look for every live plot: themed axes, black data area, box zoom."""
    for name in ("left", "bottom"):
        ax = p.getAxis(name)
        ax.setPen(pg.mkPen(T["border"]))
        ax.setTextPen(pg.mkPen(T["text"]))
    vb = p.getViewBox()
    vb.setBackgroundColor(area or PLOT_BG)
    p.showGrid(x=grid, y=grid, alpha=0.35)
    p.setDownsampling(auto=True, mode="peak")
    p.setClipToView(True)
    if rect_zoom:
        vb.setMouseMode(pg.ViewBox.RectMode)


def add_legend(p: pg.PlotItem):
    return p.addLegend(offset=(10, 5), labelTextColor=PLOT_FG,
                       brush=pg.mkBrush(*PLOT_LEGEND_BG), pen=pg.mkPen("#333333"))


def insert_menu(page, parent, row=None, col=None) -> QMenu:
    """Component menu: inserts at the cell (row, col) or the first free one."""
    m = QMenu(parent)
    for section, items in ADD_MENUS:
        if section == "Table" and not hasattr(page.host, "stat_values"):
            continue
        m.addSection(section)
        for text, icon, kind, preset in items:
            a = m.addAction(text, lambda k=kind, p=preset: page.add_tile(k, at=(row, col) if row is not None
                                                                         else None, **p))
            page.host.reg_icon(a, icon)
    return m


# ====================================================================== components
class Tile(QFrame):
    kind = "tile"

    def __init__(self, page: "ViewPage", canvas: "GridCanvas", cfg: dict):
        super().__init__(canvas)
        self.page, self.canvas, self.cfg = page, canvas, cfg
        self.setObjectName("viewTile")
        self.setProperty("kind", self.kind)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)   # the grid decides
        self.setMinimumSize(40, 30)
        self._press: QPoint | None = None
        self._dragging = False

        # edit-mode buttons float over the top-right corner, so they never take space
        self.header = QFrame(self); self.header.setObjectName("tileHeader")
        hl = QHBoxLayout(self.header); hl.setContentsMargins(2, 0, 2, 0); hl.setSpacing(0)
        self.title_lbl = QLabel()            # kept for tooltips only
        for icon, tip, slot in (("pencil", "All settings", lambda: page.edit_tile(self)),
                                ("x", "Delete", lambda: page.delete_tile(self))):
            b = QToolButton(); b.setAutoRaise(True); b.setToolTip(tip)
            page.host.reg_icon(b, icon, "muted")
            b.clicked.connect(slot)
            hl.addWidget(b)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(1, 1, 1, 1); self.body.setSpacing(0)
        self.caption = QLabel(); self.caption.setObjectName("cardTitle")
        self.caption.setContentsMargins(10, 6, 10, 2)
        self.body.addWidget(self.caption)
        self.content = self.build_content()
        self.body.addWidget(self.content, 1)
        self.apply_cfg()
        self.set_edit(page.editing)

    # ---- to override
    def build_content(self) -> QWidget: ...
    def apply_cfg(self): ...

    def restyle(self):
        self.apply_cfg()

    def default_caption(self) -> str:
        return ""

    def set_caption(self, extra: str = ""):
        text = (self.cfg.get("title") or self.default_caption()) + extra
        self.caption.setText(text)
        self.caption.setVisible(bool(self.cfg.get("show_title", True)) and bool(text))

    def channel_info(self, key) -> tuple[str, str, str]:
        """(label, colour, unit) of a tag / variable key, or placeholders."""
        for tid, idx, lbl, col, unit in self.page.host.channel_choices():
            if (tid, idx) == tuple(key or ()):
                return lbl, col, unit
        return "(no tag)", T["muted"], ""

    # ---- colours: component override > view default > theme
    def bg_color(self) -> QColor | None:
        c = self.cfg.get("card_bg") or self.page.view.get("tile_bg")
        if not c and self.kind == "text" and not self.page.editing:
            return None                               # text floats on the view background
        return QColor(c or T["panel"])

    def border_color(self) -> QColor | None:
        c = self.cfg.get("border_color") or self.page.view.get("tile_border")
        if not c and self.kind == "text" and not self.page.editing:
            return None
        return QColor(c or T["border"])

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.header.adjustSize()
        self.header.move(self.width() - self.header.width() - 4, 4)
        self.header.raise_()

    def set_edit(self, on: bool):
        self.header.setVisible(on)
        self.header.raise_()
        self.setToolTip(f"{tile_title(self.cfg)} - click to select, drag to another cell, "
                        "double-click for all settings" if on else "")
        self.content.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)
        set_prop(self, "edit", "true" if on else "false")
        if on:
            self.show_mock()
        self.update()

    def show_mock(self):
        """Edit mode: fill the component with mock-up data (live updates are paused)."""

    # ---- select / drag to another cell (edit mode only)
    def mousePressEvent(self, e):
        if not self.page.editing or e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        self._press = e.globalPosition().toPoint()
        self._dragging = False
        self.page.select(("tile", self.cfg))

    def mouseMoveEvent(self, e):
        if not self.page.editing or self._press is None:
            return super().mouseMoveEvent(e)
        g = e.globalPosition().toPoint()
        if not self._dragging and (g - self._press).manhattanLength() > 8:
            self._dragging = True
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        if self._dragging:
            self.canvas.show_drop(self, g)

    def mouseReleaseEvent(self, e):
        if self._press is not None:
            if self._dragging:
                self.canvas.ghost.hide()
                cell = self.canvas.cell_at(e.globalPosition().toPoint())
                if cell is not None:
                    self.page.move_tile(self, *cell)
            self._press, self._dragging = None, False
            self.unsetCursor()
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.page.editing and e.button() == Qt.MouseButton.LeftButton:
            self.page.edit_tile(self)
            return
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        if not self.page.editing:
            return super().contextMenuEvent(e)
        self.page.select(("tile", self.cfg))
        m = QMenu(self)
        m.addAction("All settings...", lambda: self.page.edit_tile(self))
        m.addSeparator()
        for text, dc, dr in (("Wider", 1, 0), ("Narrower", -1, 0), ("Taller", 0, 1), ("Shorter", 0, -1)):
            m.addAction(text, lambda a=dc, b=dr: self.page.resize_tile(self, a, b))
        m.addSeparator()
        m.addAction("Duplicate", lambda: self.page.duplicate_tile(self))
        m.addAction("Delete", lambda: self.page.delete_tile(self))
        m.exec(e.globalPos())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg, border = self.bg_color(), self.border_color()
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.page.editing and self.page.is_selected(self.cfg):
            pen = QPen(QColor(T["accent"]), 2)
            r = r.adjusted(0.5, 0.5, -0.5, -0.5)
        elif self.page.editing:
            pen = QPen(QColor(T["accent"]), 1, Qt.PenStyle.DashLine)
        else:
            pen = QPen(border, 1) if border is not None else Qt.PenStyle.NoPen
        p.setPen(pen)
        p.setBrush(QBrush(bg) if bg is not None else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 6, 6)
        p.end()


# ---------------------------------------------------------------------- graph
class PlotTile(Tile):
    kind = "plot"

    def build_content(self):
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(2, 0, 2, 2); v.setSpacing(0)
        self.glw = pg.GraphicsLayoutWidget()
        self.info = QLabel(); self.info.setTextFormat(Qt.TextFormat.RichText)
        self.info.setContentsMargins(8, 2, 8, 2); set_prop(self.info, "small", "true")
        v.addWidget(self.glw, 1); v.addWidget(self.info)
        self.plots: list[pg.PlotItem] = []
        self.curves: dict[tuple, pg.PlotDataItem] = {}
        self.fits: dict[tuple, pg.PlotDataItem] = {}
        self.image = self.cbar = None
        self.avg: dict[tuple, collections.deque] = {}
        self.zoomed = False
        self._t_an = 0.0
        self.glw.scene().sigMouseClicked.connect(lambda ev: ev.double() and self.reset_zoom())
        return w

    @property
    def ptype(self) -> str:
        return self.cfg.get("plot", "time")

    def default_caption(self) -> str:
        return "" if self.ptype == "time" else PLOT_TYPES[self.ptype][0]

    # settings that need new plot items; everything else is updated in place (no flicker)
    STRUCT = ("plot", "channels", "stack", "legend", "line_width", "stft_cmap", "hist_fit", "x_mode")

    def _signature(self):
        names = [(tid, idx, lbl, col) for tid, idx, lbl, col, _ in self.page.host.channel_choices()
                 if [tid, idx] in [list(k) for k in self.cfg.get("channels", [])]]
        return tuple(repr(self.cfg.get(k)) for k in self.STRUCT) + tuple(names)

    def apply_cfg(self):
        self.title_lbl.setText(PLOT_TYPES[self.ptype][0])
        sig = self._signature()
        if sig != getattr(self, "_sig", None):
            self._sig = sig
            self.rebuild()
        else:
            self.update_style()
        self.page.redraw_tile(self)

    def restyle(self):
        """Theme / names changed: rebuild (axes take the new theme colours)."""
        self._sig = self._signature()
        self.rebuild()

    def update_style(self):
        """Colours, labels, grid, ranges - on the existing plot items."""
        c = self.cfg
        self.set_caption()
        bg = self.bg_color()
        self.glw.setBackground(QColor(c.get("plot_bg")) if c.get("plot_bg") else (bg or QColor(T["panel"])))
        info = {(tid, idx): (lbl, col, unit) for tid, idx, lbl, col, unit in self.page.host.channel_choices()}
        keys = [tuple(k) for k in c.get("channels", []) if tuple(k) in info]
        unit = info[keys[0]][2] if keys else ""
        xd, yd = axis_defaults(c, unit)
        show = bool(c.get("axis_labels", True))
        stack = len(self.plots) > 1
        for gi, p in enumerate(self.plots):
            p.getViewBox().setBackgroundColor(c.get("area_bg") or PLOT_BG)
            p.showGrid(x=bool(c.get("grid", True)), y=bool(c.get("grid", True)), alpha=0.35)
            if not stack:
                p.setLabel("left", (c.get("y_label") or yd) if show else "", color=T["text"])
            elif c.get("y_label"):
                p.setLabel("left", c["y_label"] if show else "")
            if gi == len(self.plots) - 1:
                p.setLabel("bottom", (c.get("x_label") or xd) if show else "", color=T["text"])
            if self.ptype in ("time", "fft"):
                p.setLogMode(x=bool(c.get("log_x")) and self.ptype == "fft", y=bool(c.get("log_y")))
        self.info.setVisible(self.ptype == "hist" and bool(c.get("hist_stats", True)))
        self.avg.clear()
        self._t_an = 0.0
        self.reset_zoom()
        self.update()

    def rebuild(self):
        c = self.cfg
        self.set_caption()
        bg = self.bg_color()
        self.glw.setBackground(QColor(c.get("plot_bg")) if c.get("plot_bg") else (bg or QColor(T["panel"])))
        self.glw.clear()
        self.plots, self.image, self.cbar = [], None, None
        self.curves.clear(); self.fits.clear(); self.avg.clear()
        info = {(tid, idx): (lbl, col, unit) for tid, idx, lbl, col, unit in self.page.host.channel_choices()}
        keys = [tuple(k) for k in c.get("channels", []) if tuple(k) in info]
        kind = self.ptype
        if kind == "stft":
            keys = keys[:1]
        stack = bool(c.get("stack")) and kind != "stft" and len(keys) > 1
        groups = [[k] for k in keys] if stack else [keys]
        unit = info[keys[0]][2] if keys else ""
        xd, yd = axis_defaults(c, unit)
        lw = float(c.get("line_width", 1.5))
        for gi, grp in enumerate(groups or [[]]):
            p = self.glw.addPlot(row=gi, col=0)
            style_plot(p, area=c.get("area_bg", ""), grid=bool(c.get("grid", True)))
            if self.plots:
                p.setXLink(self.plots[0])
            p.getViewBox().sigRangeChangedManually.connect(lambda *_: setattr(self, "zoomed", True))
            self.plots.append(p)
            if c.get("legend", True) and kind != "stft" and not stack:
                add_legend(p)                       # stacked: the Y axis names the channel
            g_unit = info[grp[0]][2] if (stack and grp) else unit
            g_yd = axis_defaults(c, g_unit)[1] if stack else yd
            if stack and grp:
                lbl = info[grp[0]][0].split(" · ", 1)[-1].split(" [")[0]
                g_yd = f"{lbl} ({g_unit})" if g_unit else lbl
            show = bool(c.get("axis_labels", True))
            p.setLabel("left", (c.get("y_label") or g_yd) if show else "",
                       color=info[grp[0]][1] if stack and grp else T["text"])
            last = gi == len(groups) - 1
            p.setLabel("bottom", (c.get("x_label") or xd) if show and last else "", color=T["text"])
            if stack:
                p.getAxis("left").setWidth(72)          # same width: the stacked traces line up
            if stack and not last:
                p.getAxis("bottom").setStyle(showValues=False)
            if kind == "stft":
                self.image = pg.ImageItem(axisOrder="row-major")
                p.addItem(self.image)
                cmap = pg.colormap.get(c.get("stft_cmap", "viridis"))
                self.image.setColorMap(cmap)
                self.cbar = pg.ColorBarItem(values=(-100, 0), colorMap=cmap, interactive=False,
                                            label="dB", width=12)
                self.cbar.setImageItem(self.image)
                self.glw.addItem(self.cbar, row=0, col=1)
                continue
            for k in grp:
                lbl, col, _ = info[k]
                if kind == "hist":
                    qc = QColor(col); qc.setAlpha(110)
                    self.curves[k] = p.plot(stepMode="center", fillLevel=0, brush=qc,
                                            pen=pg.mkPen(col, width=1.2), name=lbl)
                    if c.get("hist_fit", True):
                        self.fits[k] = p.plot(pen=pg.mkPen(col, width=1.6, style=Qt.PenStyle.DashLine))
                else:
                    self.curves[k] = p.plot(pen=pg.mkPen(col, width=lw), name=lbl)
            if kind in ("time", "fft"):
                p.setLogMode(x=bool(c.get("log_x")) and kind == "fft", y=bool(c.get("log_y")))
        self.info.setVisible(kind == "hist" and bool(c.get("hist_stats", True)))
        self.info.setText("")
        self.reset_zoom()
        if self.page.editing:
            self.show_mock()

    @property
    def by_samples(self) -> bool:
        return self.ptype == "time" and self.cfg.get("x_mode") == "samples"

    def reset_zoom(self):
        self.zoomed = False
        for p in self.plots:
            if self.cfg.get("y_auto", True) or self.ptype in ("stft", "hist"):
                p.enableAutoRange(axis="y", enable=True)
            else:
                p.setYRange(float(self.cfg.get("y_min", -10)), float(self.cfg.get("y_max", 10)), padding=0)
            if self.by_samples:                       # static plot area: 0 … N samples
                p.setXRange(0, int(self.cfg.get("x_samples", 1000)), padding=0)
            elif self.ptype != "time":
                p.enableAutoRange(axis="x", enable=True)

    # ---- edit mode: mock-up data
    def show_mock(self):
        kind = self.ptype
        t = np.linspace(0, 5, 2000)
        for k, (key, curve) in enumerate(self.curves.items()):
            y = np.sin(2 * np.pi * (2 + k) * t + k) * (1 + 0.3 * k) + 0.5 * k
            if kind == "time":
                curve.setData(t, y)
            elif kind == "fft":
                f = np.linspace(0, 200, 400)
                a = 0.02 + sum(h / (1 + ((f - (50 + 30 * k) * h) / 2) ** 2) for h in (1, 0.4, 0.15))
                curve.setData(f, a)
            elif kind == "hist":
                y = np.random.default_rng(k).normal(k, 1 + 0.3 * k, 3000)
                self._hist(key, curve, y)
        if self.image is not None:
            tt, ff = np.meshgrid(np.linspace(0, 10, 120), np.linspace(0, 500, 120), indexing="ij")
            S = -80 + 70 * np.exp(-((ff - 100 - 30 * np.sin(tt)) / 12) ** 2) + 10 * np.random.default_rng(0).random(tt.shape)
            self.image.setImage(S.T, autoLevels=False)
            self.image.setRect(QRectF(0, 0, 10, 500))
            self.cbar.setLevels((-90, 0))
        if kind == "time" and self.plots and not self.by_samples:
            self.plots[0].setXRange(0, 5, padding=0)
        if self.by_samples:
            n = int(self.cfg.get("x_samples", 1000))
            for k, curve in enumerate(self.curves.values()):
                x = np.arange(n)
                curve.setData(x, np.sin(2 * np.pi * (2 + k) * x / max(n, 1) + k) * (1 + 0.3 * k) + 0.5 * k)
        if kind == "hist":
            self.info.setText("mock-up: mean 0.000 · SD 1.000 · skewness 0.00 · kurtosis 3.00")

    # ---- live
    def draw(self, t_end: float, win: float):
        kind = self.ptype
        host = self.page.host
        win = float(self.cfg.get("span_s") or 0) or win      # a trend can show its own time span
        if kind == "time":
            n = int(self.cfg.get("x_samples", 1000)) if self.by_samples else 0
            for (tid, idx), curve in self.curves.items():
                s = host.series(tid, idx)
                if s is None:
                    curve.setData([], []); continue
                t, y = s
                if n:                                  # newest N samples on a fixed 0 … N axis
                    y = y[-n:]
                    curve.setData(np.arange(n - y.size, n), y, connect="finite")
                else:
                    i = np.searchsorted(t, t_end - win, side="left")
                    curve.setData(t[i:], y[i:], connect="finite")
            if not self.zoomed and self.plots and not n:
                self.plots[0].setXRange(t_end - win, t_end, padding=0)
            return
        now = time.monotonic()
        if now - self._t_an < ANALYSIS_DT:
            return
        self._t_an = now
        c = self.cfg
        if kind == "fft":
            for key, curve in self.curves.items():
                s = host.series(*key)
                fs = an.sample_rate(s[0]) if s is not None else None
                if s is None or not fs:
                    curve.setData([], []); continue
                f, a = an.spectrum(s[1], fs, c.get("fft_window", "hann"), c.get("fft_scale", "rms"),
                                   int(c.get("fft_nfft", 4096)))
                n = max(1, int(c.get("fft_avg", 1)))
                if n > 1 and a.size:
                    dq = self.avg.get(key)
                    if dq is None or dq.maxlen != n or (dq and dq[0].shape != a.shape):
                        dq = self.avg[key] = collections.deque(maxlen=n)
                    dq.append(a)
                    a = np.mean(dq, axis=0)
                fmax = float(c.get("fft_fmax") or 0)
                if fmax > 0:
                    m = f <= fmax
                    f, a = f[m], a[m]
                if c.get("log_x"):
                    f, a = f[1:], a[1:]                 # no 0 Hz on a log axis
                curve.setData(f, a)
        elif kind == "hist":
            span = float(c.get("hist_span_s") or 0) or win
            parts = []
            for key, curve in self.curves.items():
                s = host.series(*key)
                if s is None:
                    curve.setData([], []); continue
                t, y = s
                y = y[np.searchsorted(t, t[-1] - span, side="left"):]
                self._hist(key, curve, y)
                st = an.stats(y, None, ("mean", "sd", "skew", "kurt"))
                if st["mean"] is not None:
                    col = curve.opts["pen"].color().name()
                    parts.append(f"<span style='color:{col}'>■</span> mean {st['mean']:.4g} · SD {st['sd']:.4g}"
                                 f" · skew {st['skew']:.2f} · kurt {st['kurt']:.2f}")
            self.info.setText("&nbsp;&nbsp;&nbsp;".join(parts))
        elif kind == "stft" and self.image is not None and self.curves == {} and c.get("channels"):
            key = tuple(c["channels"][0])
            s = host.series(*key)
            fs = an.sample_rate(s[0]) if s is not None else None
            if s is None or not fs:
                return
            t, y = s
            span = float(c.get("stft_span_s", 10))
            i = np.searchsorted(t, t[-1] - span, side="left")
            tt, ff, S = an.spectrogram(y[i:], fs, int(c.get("stft_nperseg", 512)),
                                       float(c.get("stft_overlap", 50)) / 100, c.get("fft_window", "hann"))
            if S.size == 0:
                return
            self.image.setImage(S.T, autoLevels=False)
            t0 = t[i]
            self.image.setRect(QRectF(t0, 0, max(t[-1] - t0, 1e-6), ff[-1]))
            hi = float(np.percentile(S, 99.5))
            self.cbar.setLevels((hi - 80, hi))

    def _hist(self, key, curve, y):
        c = self.cfg
        edges, h, fit = an.histogram(y, int(c.get("hist_bins", 50)), bool(c.get("hist_density", True)))
        if h.size == 0:
            curve.setData([], []); return
        curve.setData(edges, h)
        fc = self.fits.get(key)
        if fc is not None:
            if fit is None:
                fc.setData([], [])
            else:
                x = np.linspace(edges[0], edges[-1], 200)
                fc.setData(x, fit(x))


# ---------------------------------------------------------------------- text
class TextTile(Tile):
    kind = "text"
    ALIGN = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter,
             "right": Qt.AlignmentFlag.AlignRight}

    def build_content(self):
        self.lbl = QLabel()
        self.lbl.setWordWrap(True)
        self.lbl.setTextFormat(Qt.TextFormat.RichText)
        self.lbl.setContentsMargins(8, 4, 8, 4)
        return self.lbl

    def apply_cfg(self):
        style = self.cfg.get("style", "H1")
        self.caption.hide()
        self.title_lbl.setText(tile_title(self.cfg))
        self.lbl.setObjectName(f"view{style}")
        v = Qt.AlignmentFlag.AlignVCenter if style != "Body" else Qt.AlignmentFlag.AlignTop
        self.lbl.setAlignment(self.ALIGN.get(self.cfg.get("align", "left"), Qt.AlignmentFlag.AlignLeft) | v)
        text = html.escape(self.cfg.get("text", "")).replace("\n", "<br>")
        col = self.cfg.get("value_color")
        self.lbl.setText(f"<span style='color:{col}'>{text}</span>" if col else text)
        set_prop(self.lbl, "dummy", style)      # re-polish for the new objectName


# ---------------------------------------------------------------------- indicators
class _IndicatorTile(Tile):
    """Number / gauge / LED: one tag, the value or a statistic over a buffered window."""

    def quantity_text(self) -> str:
        q = self.cfg.get("quantity", "value")
        if q == "value":
            return ""
        return f"{an.STAT_LABEL.get(q, q)} · {vm.win_text(self.cfg)}"

    def default_caption(self) -> str:
        lbl = self.channel_info(self.cfg.get("channel"))[0]
        q = self.quantity_text()
        return lbl.split(" [")[0] + (f"  ·  {q}" if q else "")

    def unit(self) -> str:
        return self.cfg.get("unit") or self.channel_info(self.cfg.get("channel"))[2]

    def read(self) -> float | None:
        ch = self.cfg.get("channel")
        if not ch:
            return None
        host = self.page.host
        q = self.cfg.get("quantity", "value")
        if q == "value" or not hasattr(host, "stat_values"):
            return host.last_value(*ch)[0]
        st = host.stat_values(ch[0], ch[1], self.cfg, keys=[q]) or {}
        return st.get(an.STAT_LABEL[q])


class ValueTile(_IndicatorTile):
    kind = "value"

    def build_content(self):
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(12, 0, 12, 8); v.setSpacing(2)
        row = QHBoxLayout(); row.setSpacing(6)
        self.val_lbl = QLabel("-"); self.val_lbl.setObjectName("viewValue")
        self.val_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.unit_lbl = label("", muted=True)
        row.addWidget(self.val_lbl); row.addWidget(self.unit_lbl, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        v.addStretch(1); v.addLayout(row); v.addStretch(1)
        return w

    def apply_cfg(self):
        self.title_lbl.setText("Number")
        self.set_caption()
        self.unit_lbl.setText(self.unit())
        f = self.val_lbl.font(); f.setPixelSize(int(self.cfg.get("value_size", 30)))
        self.val_lbl.setFont(f)
        if self.page.editing:
            self.show_mock()

    def _show(self, val):
        dec = int(self.cfg.get("decimals", 3))
        text = "-" if val is None or val != val else f"{val:.{dec}f}"
        col = self.cfg.get("value_color")
        self.val_lbl.setText(f"<span style='color:{col}'>{text}</span>" if col else text)

    def show_mock(self):
        self._show(_mock(1))

    def update_value(self):
        self._show(self.read())


class GaugeTile(_IndicatorTile):
    kind = "gauge"

    def build_content(self):
        self.gauge = GaugeWidget()
        return self.gauge

    def apply_cfg(self):
        self.title_lbl.setText("Gauge")
        self.set_caption()
        self.gauge.configure(self.cfg, self.unit())
        if self.page.editing:
            self.show_mock()

    def show_mock(self):
        lo, hi = float(self.cfg.get("g_min", 0)), float(self.cfg.get("g_max", 100))
        self.gauge.set_value(lo + 0.68 * (hi - lo))

    def update_value(self):
        self.gauge.set_value(self.read())


class _Led(QWidget):
    def __init__(self):
        super().__init__()
        self.color, self.text = QColor(T["muted"]), ""

    def set(self, color: QColor, text: str):
        if (color.name(), text) != (self.color.name(), self.text):
            self.color, self.text = color, text
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = self.fontMetrics()
        th = fm.height() + 4 if self.text else 0
        d = max(10, min(self.width() - 16, self.height() - th - 12, 90))
        x, y = (self.width() - d) / 2, (self.height() - th - d) / 2
        glow = QColor(self.color); glow.setAlpha(60)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(glow)
        p.drawEllipse(QRectF(x - 4, y - 4, d + 8, d + 8))
        p.setBrush(self.color); p.setPen(QPen(QColor(T["border"]), 1))
        p.drawEllipse(QRectF(x, y, d, d))
        if self.text:
            p.setPen(QColor(T["text"]))
            p.drawText(QRectF(0, y + d + 4, self.width(), th), Qt.AlignmentFlag.AlignHCenter, self.text)
        p.end()


class LedTile(_IndicatorTile):
    """On / off lamp: on when value <op> threshold (alarm state, running, valve open ...)."""
    kind = "led"
    OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b,
           "<=": lambda a, b: a <= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}

    def build_content(self):
        self.led = _Led()
        return self.led

    def apply_cfg(self):
        self.title_lbl.setText("LED")
        self.set_caption()
        if self.page.editing:
            self.show_mock()

    def _show(self, val):
        c = self.cfg
        if val is None or val != val:
            self.led.set(QColor(T["muted"]), "no data")
            return
        on = self.OPS.get(c.get("led_op", ">"), self.OPS[">"])(val, float(c.get("led_value", 0.5)))
        col = (c.get("led_on") or T["ok"]) if on else (c.get("led_off") or T["panel2"])
        self.led.set(QColor(col), c.get("led_on_text" if on else "led_off_text", ""))

    def show_mock(self):
        self._show(float(self.cfg.get("led_value", 0.5)) + 1)

    def update_value(self):
        self._show(self.read())


# ---------------------------------------------------------------------- tables
class _TableTile(Tile):
    def build_content(self):
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(8, 2, 8, 8); v.setSpacing(4)
        self.table = QTableWidget(0, 0)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(22)
        v.addWidget(self.table, 1)
        return w

    def set_rows(self, rows: list[list[str]], right_from: int = 1, colors=None):
        tb = self.table
        if tb.rowCount() != len(rows):
            tb.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                it = tb.item(r, c)
                if it is None:
                    it = QTableWidgetItem()
                    if c >= right_from:
                        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    tb.setItem(r, c, it)
                if it.text() != text:
                    it.setText(text)
                fg, bg = colors(r, c) if colors else (None, None)
                it.setForeground(QBrush(QColor(fg)) if fg else QBrush())
                it.setBackground(QBrush(bg) if bg else QBrush())

    def _columns(self, cols: list[str], stretch=()):
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        hh = self.table.horizontalHeader()
        for c, name in enumerate(cols):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch if name in stretch
                                    else QHeaderView.ResizeMode.ResizeToContents)
        hh.setStretchLastSection(False)


class StatsTile(_TableTile):
    """Rows = tags, columns = the chosen statistics (Value, Mean, Min, Max, SD, RMS ...)."""
    kind = "stats"

    def default_caption(self) -> str:
        return "Statistics"

    def apply_cfg(self):
        self.title_lbl.setText("Statistics table")
        cols = [c for c in STAT_TILE_COLS if c in (self.cfg.get("columns") or STAT_TILE_COLS)]
        self.cols = cols
        self.set_caption(f"  ·  {vm.win_text(self.cfg)}")
        self._columns(["Tag"] + cols + ["Unit"], stretch=("Tag",))
        names = {(tid, idx): (lbl, unit) for tid, idx, lbl, _, unit in self.page.host.channel_choices()}
        self.rows = [(tuple(ch), *names[tuple(ch)]) for ch in self.cfg.get("channels", [])
                     if tuple(ch) in names]
        self.table.setRowCount(0)
        if self.page.editing:
            self.show_mock()
        else:
            self.update_value()

    def show_mock(self):
        if not hasattr(self, "rows"):
            return
        d = int(self.cfg.get("decimals", 3))
        self.set_rows([[lbl.split(" [")[0]] + [f"{_mock(i, k):.{d}f}" for k in range(len(self.cols))] + [unit]
                       for i, (_, lbl, unit) in enumerate(self.rows)] or [["(mock-up) choose tags"]
                                                                           + [""] * (len(self.cols) + 1)],
                      right_from=1)

    def update_value(self):
        d = int(self.cfg.get("decimals", 3))
        keys = [_COL_KEY.get(c, "value") for c in self.cols]
        out = []
        for (tid, idx), lbl, unit in self.rows:
            st = self.page.host.stat_values(tid, idx, self.cfg, keys=keys) or {}
            out.append([lbl.split(" [")[0]] +
                       ["-" if st.get(c) is None or st[c] != st[c] else f"{st[c]:.{d}f}" for c in self.cols]
                       + [unit])
        self.set_rows(out)


class TagTile(_TableTile):
    """Live tag list: value, unit, quality, time of the last value."""
    kind = "tags"

    def default_caption(self) -> str:
        return "Tags"

    def apply_cfg(self):
        self.title_lbl.setText("Tag table")
        self.cols = [c for c in TAG_TILE_COLS if c in (self.cfg.get("columns") or TAG_TILE_COLS)] or ["Tag"]
        self.set_caption()
        self._columns(self.cols, stretch=("Tag", "Description"))
        self.keys = [tuple(k) for k in self.cfg.get("channels", [])]
        self.table.setRowCount(0)
        if self.page.editing:
            self.show_mock()
        else:
            self.update_value()

    def show_mock(self):
        if not hasattr(self, "cols"):
            return
        rows = []
        for i, k in enumerate(self.keys or [None] * 3):
            lbl, _, unit = self.channel_info(k) if k else (f"Tag{i + 1}", "", "")
            rows.append({"Tag": lbl.split(" · ")[-1].split(" [")[0], "Value": f"{_mock(i):.2f}", "Unit": unit,
                         "Quality": "Good", "Time": "12:00:00", "Device": lbl.split(" · ")[0],
                         "Description": "", "_good": True})
        self._show(rows)

    def _show(self, rows):
        right = self.cols.index("Value") if "Value" in self.cols else 99
        self.set_rows([[r.get(c, "") for c in self.cols] for r in rows] or
                      [["No tags - choose them in Properties"] + [""] * (len(self.cols) - 1)],
                      right_from=99, colors=lambda r, c: (
                          T["bad"] if r < len(rows) and not rows[r].get("_good", True)
                          and self.cols[c] in ("Value", "Quality") else None, None))
        for r in range(self.table.rowCount()):
            it = self.table.item(r, right) if right < 99 else None
            if it is not None:
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def update_value(self):
        host = self.page.host
        if hasattr(host, "tag_rows"):
            self._show(host.tag_rows(self.keys, int(self.cfg.get("decimals", 3))))


class AlarmTile(_TableTile):
    """Active alarms or the alarm log; max rows, columns and levels configurable."""
    kind = "alarms"

    def default_caption(self) -> str:
        return ALARM_SOURCES.get(self.cfg.get("source", "active"), "Alarms")

    def apply_cfg(self):
        self.title_lbl.setText("Alarm table")
        self.cols = [c for c in ALARM_TILE_COLS if c in (self.cfg.get("columns") or ALARM_TILE_COLS)] or ["Alarm"]
        self.set_caption(f"  ·  max {int(self.cfg.get('max_rows', 10))} rows")
        self._columns(self.cols, stretch=("Alarm", "Tag"))
        self._seq = None
        self.table.setRowCount(0)
        if self.page.editing:
            self.show_mock()
        else:
            self.update_value()

    def show_mock(self):
        if not hasattr(self, "cols"):
            return
        n = min(int(self.cfg.get("max_rows", 10)), 6)
        levels = self.cfg.get("levels", [1, 2, 3, 4]) or [1]
        rows = []
        for i in range(n):
            lv = levels[i % len(levels)]
            rows.append({"Time": f"12:0{i}:15", "Level": f"Level {lv}",
                         "State": ("ON", "OFF")[i % 2] if self.cfg.get("source") == "log" else "ALARM",
                         "Alarm": f"Mock alarm {i + 1}", "Tag": f"Device · Tag{i + 1}",
                         "Type": ("H", "L", "RH", "D")[i % 4], "Value": f"{_mock(i):.3f}",
                         "Setpoint": f"{_mock(i) - 1:.1f}", "_level": lv})
        self._show(rows)

    def _show(self, rows):
        self.set_rows([[r.get(c, "") for c in self.cols] for r in rows] or
                      [["No alarms"] + [""] * (len(self.cols) - 1)], right_from=99,
                      colors=lambda r, c: self._color(rows, r, c))

    def _color(self, rows, r, c):
        if r >= len(rows):
            return T["muted"], None
        row, name = rows[r], self.cols[c]
        col = T.get(LEVEL_TOKEN.get(row.get("_level"), "muted"), T["muted"])
        if name in ("Level", "State"):
            return col, None
        return None, None

    def update_value(self, force: bool = False):
        host = self.page.host
        seq = host.alarm_seq()
        src = self.cfg.get("source", "active")
        if not force and src == "log" and seq == self._seq:
            return                                   # the log only changes on events
        self._seq = seq
        self._show(host.alarm_rows(src, int(self.cfg.get("max_rows", 10)),
                                   self.cfg.get("levels", [1, 2, 3, 4])))


TILE_TYPES = {"plot": PlotTile, "text": TextTile, "value": ValueTile, "gauge": GaugeTile, "led": LedTile,
              "stats": StatsTile, "alarms": AlarmTile, "tags": TagTile}
LIVE_TYPES = (ValueTile, GaugeTile, LedTile, StatsTile, AlarmTile, TagTile)


# ====================================================================== grid
class _Cell(QFrame):
    """An empty cell in edit mode: click selects it, + inserts a component."""

    def __init__(self, page, row: int, col: int):
        super().__init__()
        self.page, self.row, self.col = page, row, col
        self.setObjectName("gridCell")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        b = QToolButton(); b.setAutoRaise(True); b.setToolTip("Insert a component here")
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        page.host.reg_icon(b, "plus", "muted")
        b.setMenu(insert_menu(page, b, row, col))
        lay.addWidget(b, 0, Qt.AlignmentFlag.AlignCenter)
        self.setToolTip(f"Row {row + 1}, column {col + 1} - click to select, + to insert")

    def mousePressEvent(self, e):
        self.page.select(("cell", self.row, self.col))

    def contextMenuEvent(self, e):
        self.page.select(("cell", self.row, self.col))
        insert_menu(self.page, self, self.row, self.col).exec(e.globalPos())

    def paintEvent(self, _):
        p = QPainter(self)
        sel = self.page.is_selected(("cell", self.row, self.col))
        p.setPen(QPen(QColor(T["accent" if sel else "border"]), 2 if sel else 1,
                      Qt.PenStyle.SolidLine if sel else Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        p.end()


class _Head(QToolButton):
    """Row / column handle (edit mode): click selects the line, the menu edits the grid."""

    def __init__(self, page, axis: str, i: int):
        super().__init__()
        self.page, self.axis, self.i = page, axis, i
        self.setObjectName("gridHead")
        line = page.view[axis][i]
        size = f"{line['size']:g} px" if line.get("fixed") else f"{line['size']:g}×"
        self.setText(f"{i + 1}" if axis == "rows" else chr(65 + i) if i < 26 else str(i + 1))
        self.setToolTip(f"{'Row' if axis == 'rows' else 'Column'} {self.text()} · {size}"
                        " - click to select, right-click to insert / delete")
        self.setSizePolicy(QSizePolicy.Policy.Ignored if axis == "cols" else QSizePolicy.Policy.Fixed,
                           QSizePolicy.Policy.Fixed if axis == "cols" else QSizePolicy.Policy.Ignored)
        self.clicked.connect(lambda: page.select(("row" if axis == "rows" else "col", i)))
        set_prop(self, "selected", page.is_selected(("row" if axis == "rows" else "col", i)))

    def contextMenuEvent(self, e):
        self.page.line_menu(self.axis, self.i).exec(e.globalPos())


class GridCanvas(QWidget):
    """The grid of a view: components, empty cells and (edit mode) row / column handles."""
    HEAD = 22

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.tiles: list[Tile] = []
        self.setObjectName("viewCanvas")
        self.lay = QGridLayout(self)
        self.ghost = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._extra: list[QWidget] = []                   # cells + handles, rebuilt by relayout
        self._used = (0, 0)

    @property
    def off(self) -> int:
        return 1 if self.page.editing else 0

    def relayout(self):
        v, lay, off = self.page.view, self.lay, self.off
        while lay.count():
            lay.takeAt(0)
        for w in self._extra:
            w.deleteLater()
        self._extra = []
        ur, uc = self._used
        for r in range(ur):
            lay.setRowStretch(r, 0); lay.setRowMinimumHeight(r, 0)
        for c in range(uc):
            lay.setColumnStretch(c, 0); lay.setColumnMinimumWidth(c, 0)
        m = int(v.get("margin", 10))
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(int(v.get("gap", 8)))
        rows, cols = v["rows"], v["cols"]
        nr, nc = len(rows), len(cols)
        min_h = min_w = 0
        for i, line in enumerate(rows):
            px = int(line["size"]) if line.get("fixed") else MIN_CELL
            lay.setRowMinimumHeight(i + off, px)
            lay.setRowStretch(i + off, 0 if line.get("fixed") else max(1, int(float(line["size"]) * 100)))
            min_h += px
        for i, line in enumerate(cols):
            px = int(line["size"]) if line.get("fixed") else MIN_CELL
            lay.setColumnMinimumWidth(i + off, px)
            lay.setColumnStretch(i + off, 0 if line.get("fixed") else max(1, int(float(line["size"]) * 100)))
            min_w += px
        # all lines fixed: an empty stretch line takes the rest, so fixed sizes stay fixed
        if all(x.get("fixed") for x in rows):
            lay.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding), nr + off, 0)
            lay.setRowStretch(nr + off, 1)
        if all(x.get("fixed") for x in cols):
            lay.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum), 0, nc + off)
            lay.setColumnStretch(nc + off, 1)
        self._used = (nr + 2, nc + 2)
        if off:                                           # handles: row 0 / column 0
            lay.setRowMinimumHeight(0, self.HEAD); lay.setColumnMinimumWidth(0, self.HEAD + 6)
            for i in range(nc):
                h = _Head(self.page, "cols", i); lay.addWidget(h, 0, i + 1); self._extra.append(h)
            for i in range(nr):
                h = _Head(self.page, "rows", i); lay.addWidget(h, i + 1, 0); self._extra.append(h)
            min_h += self.HEAD; min_w += self.HEAD + 6
        taken = vm.occupied(v)
        for t in self.tiles:
            c = t.cfg
            lay.addWidget(t, c["row"] + off, c["col"] + off, c["rowspan"], c["colspan"])
            t.show()
        if self.page.editing:
            for r in range(nr):
                for c in range(nc):
                    if (r, c) not in taken:
                        cell = _Cell(self.page, r, c)
                        lay.addWidget(cell, r + off, c + off)
                        self._extra.append(cell)
        gap = int(v.get("gap", 8))
        self.setMinimumSize(min_w + gap * max(nc - 1, 0) + 2 * m, min_h + gap * max(nr - 1, 0) + 2 * m)
        self.update()

    def cell_at(self, gpos: QPoint) -> tuple[int, int] | None:
        p = self.mapFromGlobal(gpos)
        v, off = self.page.view, self.off
        row = next((r for r in range(len(v["rows"]))
                    if self.lay.cellRect(r + off, off).top() <= p.y() <= self.lay.cellRect(r + off, off).bottom()
                    + self.lay.spacing()), None)
        col = next((c for c in range(len(v["cols"]))
                    if self.lay.cellRect(off, c + off).left() <= p.x() <= self.lay.cellRect(off, c + off).right()
                    + self.lay.spacing()), None)
        return (row, col) if row is not None and col is not None else None

    def span_rect(self, r, c, rs, cs) -> QRect:
        off = self.off
        return self.lay.cellRect(r + off, c + off).united(self.lay.cellRect(r + rs - 1 + off, c + cs - 1 + off))

    def show_drop(self, tile: Tile, gpos: QPoint):
        cell = self.cell_at(gpos)
        span = vm.fit_span(self.page.view, tile.cfg, *cell, tile.cfg["colspan"], tile.cfg["rowspan"]) \
            if cell else None
        if not span:
            self.ghost.hide()
            return
        self.ghost.setGeometry(self.span_rect(cell[0], cell[1], span[1], span[0]))
        self.ghost.show(); self.ghost.raise_()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(e.rect(), QColor(self.page.view.get("bg") or T["bg"]))
        p.end()


class ViewPage(QWidget):
    """One view: toolbar + scrollable grid."""
    changed = Signal()

    def __init__(self, host, view: dict, editing: bool = False):
        super().__init__()
        self.host, self.view = host, normalize_view(view)
        self.editing = editing
        self.selected = None
        self._last: tuple | None = None                 # last (t_end, win) drawn

        bar = QFrame(); bar.setObjectName("docBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 3, 8, 3); bl.setSpacing(4)
        self.btn_edit = QToolButton(); self.btn_edit.setCheckable(True)
        self.btn_edit.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_edit.setToolTip("Edit the layout (Done = live data)")
        host.reg_icon(self.btn_edit, "pencil")
        self.btn_edit.toggled.connect(self.set_edit)
        bl.addWidget(self.btn_edit)
        self.edit_btns = []
        ins = QToolButton(); ins.setText("Insert")
        ins.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        ins.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        ins.setToolTip("Insert a component in the selected cell (or the first free one)")
        host.reg_icon(ins, "plus")
        ins.setMenu(QMenu(ins))
        ins.menu().aboutToShow.connect(lambda: self._fill_insert(ins.menu()))
        bl.addWidget(ins); self.edit_btns.append(ins)
        for text, icon, axis in (("Row", "rows", "rows"), ("Column", "columns", "cols")):
            b = QToolButton(); b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setToolTip(f"Add a {text.lower()} at the end (handles: right-click to insert / delete)")
            host.reg_icon(b, icon)
            b.clicked.connect(lambda _=False, a=axis: self.insert_line(a, len(self.view[a])))
            bl.addWidget(b); self.edit_btns.append(b)
        bl.addStretch(1)
        self.hint = label("Mock-up data while editing · click to select · drag to move · "
                          "double-click for all settings", muted=True, small=True)
        bl.addWidget(self.hint)
        self.count_lbl = label("", muted=True, small=True)
        bl.addWidget(self.count_lbl)
        for tip, icon, slot in (("View properties", "sliders", lambda: host.view_properties(self)),
                                ("Delete view", "trash", lambda: host.delete_view(self))):
            b = QToolButton(); b.setToolTip(tip); b.setAutoRaise(True)
            host.reg_icon(b, icon)
            b.clicked.connect(slot)
            bl.addWidget(b)
        self.bar = bar

        self.canvas = GridCanvas(self)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setWidget(self.canvas)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(bar); v.addWidget(self.scroll, 1)
        for cfg in self.view["tiles"]:
            self._make_tile(cfg)
        self.btn_edit.setChecked(editing)
        self.set_edit(editing)

    @property
    def name(self) -> str:
        return self.view["name"]

    @property
    def tiles(self) -> list[Tile]:
        return self.canvas.tiles

    def all_tiles(self) -> list[Tile]:
        return self.canvas.tiles

    def tile_of(self, cfg: dict) -> Tile | None:
        return next((t for t in self.tiles if t.cfg is cfg), None)

    # ---- selection (the Properties panel follows it)
    def select(self, sel):
        self.selected = sel
        for t in self.tiles:
            t.update()
        for w in self.canvas._extra:
            if isinstance(w, _Head):
                set_prop(w, "selected", self.is_selected(("row" if w.axis == "rows" else "col", w.i)))
            w.update()
        if hasattr(self.host, "view_selected"):
            self.host.view_selected(self, sel)

    def is_selected(self, what) -> bool:
        s = self.selected
        if s is None:
            return False
        if isinstance(what, dict):
            return s[0] == "tile" and s[1] is what
        return tuple(s) == tuple(what)

    def selected_cell(self) -> tuple[int, int] | None:
        s = self.selected
        if s and s[0] == "cell":
            return s[1], s[2]
        if s and s[0] == "tile":
            return s[1]["row"], s[1]["col"]
        return None

    def _fill_insert(self, m: QMenu):
        m.clear()
        cell = self.selected_cell() if self.selected and self.selected[0] == "cell" else None
        sub = insert_menu(self, m, *(cell or (None, None)))
        for a in sub.actions():
            m.addAction(a)

    # ---- components
    def _make_tile(self, cfg: dict) -> Tile:
        t = TILE_TYPES[cfg["kind"]](self, self.canvas, cfg)
        self.canvas.tiles.append(t)
        return t

    def _update_count(self):
        tiles = self.view["tiles"]
        g = sum(1 for t in tiles if is_graph(t))
        self.count_lbl.setText(f"  {len(self.view['rows'])}×{len(self.view['cols'])} grid · "
                               f"{len(tiles)}/{MAX_TILES} components · {g}/{MAX_GRAPHS} graphs")
        self.count_lbl.setVisible(self.editing)

    def _room(self, cfg: dict) -> bool:
        """Limits per view (speed): MAX_TILES components, MAX_GRAPHS graphs."""
        tiles = self.view["tiles"]
        if len(tiles) >= MAX_TILES:
            msg = f"A view holds at most {MAX_TILES} components - add another view for more."
        elif is_graph(cfg) and sum(1 for t in tiles if is_graph(t)) >= MAX_GRAPHS:
            msg = (f"A view holds at most {MAX_GRAPHS} graphs (they redraw live) - add another view, "
                   "or show several tags in one graph.")
        else:
            return True
        QMessageBox.information(self, "View is full", msg)
        return False

    def _changed(self, relayout: bool = True):
        if relayout:
            self.canvas.relayout()
        self._update_count()
        self.host.view_changed()

    def add_tile(self, kind: str, at=None, **preset):
        """Place a component with sensible defaults in a cell; refine it in Properties."""
        ch = self.host.channel_choices()
        cfg = new_tile(kind, 0, 0, show_title=bool(self.view.get("title_bar", True)), **preset)
        if not self._room(cfg):
            return
        if kind in ("plot", "stats", "tags") and ch:   # start with every tag of the first device
            first = ch[0][0]
            cfg["channels"] = [[tid, idx] for tid, idx, *_ in ch if tid == first]
            if kind == "plot" and cfg["plot"] == "stft":
                cfg["channels"] = cfg["channels"][:1]
        elif kind in ("value", "gauge", "led") and ch:
            cfg["channel"] = [ch[0][0], ch[0][1]]
        if kind == "gauge":
            cfg["decimals"] = 1
        if kind == "text" and cfg.get("style") != "Body":
            cfg["colspan"] = len(self.view["cols"])          # a header spans the row
        place = None
        if at is not None and at[0] is not None:
            span = vm.fit_span(self.view, None, at[0], at[1], cfg["colspan"], cfg["rowspan"])
            place = (at[0], at[1], *span) if span else None
        place = place or vm.free_cell(self.view, cfg["colspan"], cfg["rowspan"]) or vm.free_cell(self.view)
        if place is None:                                    # full: add a row for it
            vm.insert_line(self.view, "rows", len(self.view["rows"]))
            place = vm.free_cell(self.view, cfg["colspan"], 1)
        cfg.update(row=place[0], col=place[1], colspan=place[2], rowspan=place[3])
        self.view["tiles"].append(cfg)
        self._make_tile(cfg)
        self._changed()
        self.select(("tile", cfg))

    def edit_tile(self, tile: Tile):
        dlg = TileDialog(self, tile.cfg, self.host.channel_choices())
        if dlg.exec():
            tile.cfg.update(dlg.result_cfg())
            self.tile_changed(tile.cfg)

    def tile_changed(self, cfg: dict, relayout: bool = False):
        """A component's settings changed (dialog or Properties panel)."""
        tile = self.tile_of(cfg)
        if tile is None:
            return
        tile.apply_cfg()
        tile.update()
        if self.editing:
            tile.show_mock()
        self._changed(relayout)

    def move_tile(self, tile: Tile, row: int, col: int):
        c = tile.cfg
        span = vm.fit_span(self.view, c, row, col, c["colspan"], c["rowspan"])
        if span and (row, col, *span) != (c["row"], c["col"], c["colspan"], c["rowspan"]):
            c.update(row=row, col=col, colspan=span[0], rowspan=span[1])
            self._changed()
            self.select(("tile", c))

    def resize_tile(self, tile: Tile, dcs: int, drs: int):
        c = tile.cfg
        cs, rs = c["colspan"] + dcs, c["rowspan"] + drs
        if vm.fits(self.view, c, c["row"], c["col"], rs, cs):
            c.update(colspan=cs, rowspan=rs)
            self._changed()
            self.select(("tile", c))

    def redraw_tile(self, tile: Tile):
        """Settings changed in live mode: draw at once, no empty frame."""
        if not self.editing and self._last and isinstance(tile, PlotTile):
            tile._t_an = 0.0
            tile.draw(*self._last)

    def duplicate_tile(self, tile: Tile):
        if not self._room(tile.cfg):
            return
        cfg = copy.deepcopy(tile.cfg)
        place = vm.free_cell(self.view, cfg["colspan"], cfg["rowspan"]) or vm.free_cell(self.view)
        if place is None:
            vm.insert_line(self.view, "rows", len(self.view["rows"]))
            place = vm.free_cell(self.view, cfg["colspan"], 1)
        cfg.update(id=new_tile(cfg["kind"])["id"], row=place[0], col=place[1], colspan=place[2], rowspan=place[3])
        self.view["tiles"].append(cfg)
        self._make_tile(cfg)
        self._changed()
        self.select(("tile", cfg))

    def delete_tile(self, tile: Tile):
        self.canvas.tiles.remove(tile)
        self.view["tiles"] = [x for x in self.view["tiles"] if x is not tile.cfg]
        tile.deleteLater()
        if self.is_selected(tile.cfg):
            self.select(("cell", tile.cfg["row"], tile.cfg["col"]))
        self._changed()

    # ---- rows / columns
    def line_menu(self, axis: str, i: int) -> QMenu:
        word = "row" if axis == "rows" else "column"
        a, b = ("above", "below") if axis == "rows" else ("left", "right")
        m = QMenu(self)
        m.addAction(f"Insert {word} {a}", lambda: self.insert_line(axis, i))
        m.addAction(f"Insert {word} {b}", lambda: self.insert_line(axis, i + 1))
        m.addSeparator()
        line = self.view[axis][i]
        m.addAction("Size..." , lambda: self._ask_size(axis, i))
        act = m.addAction(f"Fixed size ({'px' if axis == 'rows' else 'px'})")
        act.setCheckable(True); act.setChecked(bool(line.get("fixed")))
        act.toggled.connect(lambda on: self.set_line(axis, i, fixed=on))
        m.addSeparator()
        d = m.addAction(f"Delete {word}", lambda: self.delete_line(axis, i))
        d.setEnabled(len(self.view[axis]) > 1)
        return m

    def _ask_size(self, axis, i):
        line = self.view[axis][i]
        if line.get("fixed"):
            val, ok = QInputDialog.getInt(self, "Size", "Size in pixels:", int(line["size"]), 20, 4000)
        else:
            val, ok = QInputDialog.getDouble(self, "Size", "Weight (share of the free space):",
                                             float(line["size"]), 0.1, 50, 1)
        if ok:
            self.set_line(axis, i, size=val)

    def set_line(self, axis: str, i: int, size=None, fixed=None):
        line = self.view[axis][i]
        if fixed is not None and bool(fixed) != bool(line.get("fixed")):
            line["fixed"] = bool(fixed)
            line["size"] = (120 if axis == "rows" else 200) if fixed else 1.0
        if size is not None:
            line["size"] = int(size) if line.get("fixed") else float(size)
        self._changed()
        self.select((("row" if axis == "rows" else "col"), i))

    def insert_line(self, axis: str, at: int):
        if len(self.view[axis]) >= (vm.MAX_ROWS if axis == "rows" else vm.MAX_COLS):
            return
        vm.insert_line(self.view, axis, at)
        self._changed()
        self.select((("row" if axis == "rows" else "col"), at))

    def delete_line(self, axis: str, i: int):
        pos, span = ("row", "rowspan") if axis == "rows" else ("col", "colspan")
        lost = [t for t in self.view["tiles"] if t[pos] == i and t[span] == 1]
        if lost and QMessageBox.question(
                self, "Delete", f"Delete the {'row' if axis == 'rows' else 'column'} and the "
                                f"{len(lost)} component(s) only in it?") != QMessageBox.StandardButton.Yes:
            return
        for cfg in vm.delete_line(self.view, axis, i):
            tile = self.tile_of(cfg)
            if tile is not None:
                self.canvas.tiles.remove(tile)
                tile.deleteLater()
        self.selected = None
        self._changed()
        self.select(None)

    # ---- modes / updates
    def set_edit(self, on: bool):
        self.editing = on
        self.btn_edit.setText("Done" if on else "Edit")
        for b in self.edit_btns:
            b.setVisible(on)
        self.hint.setVisible(on)
        self._update_count()
        for t in self.tiles:
            t.set_edit(on)
        self.canvas.relayout()
        if not on:
            self.selected = None
            for t in self.tiles:
                if isinstance(t, PlotTile):          # drop the mock curves, live data follows
                    for c in t.curves.values():
                        c.setData([], [])
                    for c in t.fits.values():
                        c.setData([], [])
                    if t.image is not None:
                        t.image.clear()
                    t.info.setText("")
                    t.reset_zoom()
            self.update_values()
            if hasattr(self.host, "view_selected"):
                self.host.view_selected(self, None)

    def apply_view(self):
        """View properties changed: grid, colours, component defaults."""
        for t in self.tiles:
            t.apply_cfg(); t.update()
        self.canvas.relayout()
        self._update_count()
        self.setToolTip(self.view.get("description", ""))

    def restyle(self):
        for t in self.tiles:
            t.restyle()
        self.canvas.update()

    def draw(self, t_end: float, win: float):
        self._last = (t_end, win)
        if self.editing:                            # mock-up data while editing
            return
        for t in self.tiles:
            if isinstance(t, PlotTile):
                t.draw(t_end, win)

    def update_values(self):
        if self.editing:
            return
        for t in self.tiles:
            if isinstance(t, LIVE_TYPES):
                t.update_value()
