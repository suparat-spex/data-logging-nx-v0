"""
views.py - user-built "views" (screens) for NX Logger: a free-form canvas with a snap grid.

Components are placed anywhere and snap to the view's grid (default 10 px):
  Header / text   heading H1 / H2 / H3 or body text
  Graph           time plot (trend), FFT spectrum, spectrogram (STFT) or histogram
  Indicator       number, gauge (6 styles) or LED of one tag: the value or a statistic
                  (mean, RMS, SD ...) over a window buffered by the app (seconds / minutes /
                  hours / samples, moving or block - stat_buffer.py)
  Table           statistics table, alarm table, tag table (value, quality, time)

Edit mode: Add places a component with defaults; click selects it (the Properties panel
edits the selection, click the empty canvas for the view's properties), drag moves it,
the grip in the bottom-right corner resizes it; while dragging only an outline moves,
the component follows on release. Double-click opens all its settings, right-click for
duplicate / bring to front / delete. Edit mode shows mock-up data (no live updates), so
arranging a big view stays fast; Live mode locks the layout. Only the visible view is
drawn. Views are plain dicts (core/view_model.py) so the project file stores them as JSON.

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
  host.view_selected(page, selection)   selection: ("tile", cfg) or None (the view)
"""
from __future__ import annotations

import collections
import copy
import html
import math
import time

import numpy as np
from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu,
                               QMessageBox, QRubberBand, QScrollArea, QTableWidget, QTableWidgetItem,
                               QToolButton, QVBoxLayout, QWidget)
import pyqtgraph as pg

from nxlogger.core import analysis as an
from nxlogger.core import view_model as vm
from nxlogger.core.view_model import (ALARM_SOURCES, ALARM_TILE_COLS, GRID, MAX_GRAPHS, MAX_TILES, MIN_H,
                                      MIN_W, PLOT_TYPES, STAT_TILE_COLS, TAG_TILE_COLS, axis_defaults,
                                      is_graph, new_tile, normalize_view, tile_title)
from nxlogger.ui.dialogs.tile_dialog import TileDialog
from nxlogger.ui.theme.ui_kit import T, fixed_width, label, set_prop
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


class _Grip(QWidget):
    """Resize grip drawn above the component (bottom-right corner, edit mode)."""
    SIZE = 16

    def __init__(self, tile):
        super().__init__(tile)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(T["accent"]), 1.6))
        s = self.SIZE
        for k in (4, 8, 12):
            p.drawLine(s - 3 - k, s - 3, s - 3, s - 3 - k)
        p.end()


# ====================================================================== components
class Tile(QFrame):
    kind = "tile"

    def __init__(self, page: "ViewPage", canvas: "Canvas", cfg: dict):
        super().__init__(canvas)
        self.page, self.canvas, self.cfg = page, canvas, cfg
        self.setObjectName("viewTile")
        self.setProperty("kind", self.kind)
        self.setMouseTracking(True)
        self._press: QPoint | None = None
        self._start = QRect()
        self._target = QRect()
        self._drag = "move"

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
        self.grip = _Grip(self)
        self.setGeometry(cfg["x"], cfg["y"], cfg["w"], cfg["h"])
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
        self.grip.move(self.width() - _Grip.SIZE - 1, self.height() - _Grip.SIZE - 1)
        self.grip.raise_()

    def set_edit(self, on: bool):
        self.header.setVisible(on)
        self.header.raise_()
        self.grip.setVisible(on)
        self.grip.raise_()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setToolTip(f"{tile_title(self.cfg)} - click to select, drag to move, corner grip to resize, "
                        "double-click for all settings" if on else "")
        self.content.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)
        set_prop(self, "edit", "true" if on else "false")
        if on:
            self.show_mock()
        self.update()

    def show_mock(self):
        """Edit mode: fill the component with mock-up data (live updates are paused)."""

    # ---- select, drag to move / resize (edit mode only)
    def _in_corner(self, pos) -> bool:
        return pos.x() > self.width() - 20 and pos.y() > self.height() - 20

    def mousePressEvent(self, e):
        if not self.page.editing or e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        self.page.select(("tile", self.cfg))
        self._press = e.globalPosition().toPoint()
        self._start = QRect(self.geometry())
        self._target = QRect(self._start)
        self._drag = "resize" if self._in_corner(e.position()) else "move"
        self.raise_()
        self.canvas.ghost.setGeometry(self._start)    # only this outline moves while dragging
        self.canvas.ghost.show(); self.canvas.ghost.raise_()

    def mouseMoveEvent(self, e):
        if not self.page.editing:
            return super().mouseMoveEvent(e)
        if self._press is None:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if self._in_corner(e.position())
                           else Qt.CursorShape.SizeAllCursor)
            return
        d = e.globalPosition().toPoint() - self._press
        g = QRect(self._start)
        snap = self.page.snap
        cw, ch = self.canvas.width(), self.canvas.height()     # the view's fixed size: stay inside it
        if self._drag == "move":
            g.moveTo(min(max(0, snap(g.x() + d.x())), max(0, cw - g.width())),
                     min(max(0, snap(g.y() + d.y())), max(0, ch - g.height())))
        else:
            g.setWidth(min(max(MIN_W, snap(g.width() + d.x())), max(MIN_W, cw - g.x())))
            g.setHeight(min(max(MIN_H, snap(g.height() + d.y())), max(MIN_H, ch - g.y())))
        if g != self._target:
            self._target = g
            self.canvas.ghost.setGeometry(g)

    def mouseReleaseEvent(self, e):
        if self._press is not None:
            self._press = None
            self.canvas.ghost.hide()
            g = self._target
            if g != self.geometry():
                self.setGeometry(g)
                self.cfg.update(x=g.x(), y=g.y(), w=g.width(), h=g.height())
                self.canvas.fit()
                self.page.geometry_changed(self.cfg)
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e):
        if self._press is None:
            self.unsetCursor()
        super().leaveEvent(e)

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
        m.addAction("Edit...", lambda: self.page.edit_tile(self))
        m.addAction("Duplicate", lambda: self.page.duplicate_tile(self))
        m.addAction("Bring to front", self.raise_)
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


# ====================================================================== page
class Canvas(QWidget):
    """The free-form area of a view, holding its components."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.tiles: list[Tile] = []
        self.setObjectName("viewCanvas")
        self.ghost = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.empty = label("Empty view - click Edit, then add a header, graph, indicator or table.",
                           muted=True)
        self.empty.setParent(self); self.empty.move(20, 20); self.empty.adjustSize()
        self._grid_px: tuple | None = None

    def fit(self):
        """The view's fixed size (width x height, View pop-up > Size): the same layout with any panel
        open; the scroll area shows scroll bars when the window is smaller."""
        v = self.page.view
        self.setFixedSize(int(v.get("width", 1920)), int(v.get("height", 1080)))
        self.empty.setVisible(not self.tiles)

    def _grid_brush(self, step: int) -> QBrush:
        key = (step, T["border"])
        if self._grid_px is None or self._grid_px[0] != key:
            pm = QPixmap(step, step); pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm); p.setPen(QColor(T["border"])); p.drawPoint(0, 0); p.end()
            self._grid_px = (key, QBrush(pm))
        return self._grid_px[1]

    def mousePressEvent(self, e):
        if self.page.editing:                         # empty canvas: the view itself
            self.page.select(None)
        super().mousePressEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        v = self.page.view
        p.fillRect(e.rect(), QColor(v.get("bg") or T["bg"]))
        if self.page.editing and v.get("show_grid", True):      # dot grid while editing (one brush)
            step = max(4, int(v.get("grid", GRID)))
            step *= 2 if step < 10 else 1
            p.fillRect(e.rect(), self._grid_brush(step))
        if self.page.editing:                                    # where the screen ends
            p.setPen(QColor(T["accent"]))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()


class ViewPage(QWidget):
    """One view: toolbar + scrollable free-form canvas."""
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
        fixed_width(self.btn_edit, "Edit", "Done")
        self.add_btns = []
        icons = {"Text": "heading", "Graph": "chart", "Indicator": "gauge", "Table": "table"}
        for section, items in ADD_MENUS:
            if section == "Table" and not hasattr(host, "stat_values"):
                continue
            b = QToolButton(); b.setText(section)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            b.setToolTip(f"Add a {section.lower()} component")
            host.reg_icon(b, icons.get(section, "plus"))
            m = QMenu(b)
            for text, _icon, kind, preset in items:      # menus are text only (no icon column)
                m.addAction(text, lambda k=kind, p=preset: self.add_tile(k, **p))
            b.setMenu(m)
            bl.addWidget(b); self.add_btns.append(b)
        bl.addStretch(1)
        self.hint = label("Mock-up data while editing · click to select · drag to move · "
                          "corner grip to resize", muted=True, small=True)
        bl.addWidget(self.hint)
        self.count_lbl = label("", muted=True, small=True)
        bl.addWidget(self.count_lbl)
        if hasattr(host, "enter_monitor"):          # full-screen watching (Monitor mode, F11)
            mon = QToolButton(); mon.setText("Monitor")
            mon.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            mon.setToolTip("Monitor mode: only the views, full size (F11)")
            host.reg_icon(mon, "maximize")
            mon.clicked.connect(lambda: host.enter_monitor(self.view["id"]))
            bl.addWidget(mon)
            self.mon_btn = mon
        for tip, icon, slot in (("View properties", "sliders", lambda: host.view_properties(self)),
                                ("Delete view", "trash", lambda: host.delete_view(self))):
            b = QToolButton(); b.setToolTip(tip); b.setAutoRaise(True)
            host.reg_icon(b, icon)
            b.clicked.connect(slot)
            bl.addWidget(b)
        self.bar = bar

        self.canvas = Canvas(self)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(False)   # fixed canvas: scroll bars, no stretch
        self.scroll.setObjectName("viewScroll")
        self.scroll.setWidget(self.canvas)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._align()
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

    def snap(self, v: int) -> int:
        g = max(1, int(self.view.get("grid", GRID)))
        return int(round(v / g) * g) if self.view.get("snap", True) else int(v)

    # ---- selection (the Properties panel follows it)
    def select(self, sel):
        self.selected = sel
        for t in self.tiles:
            t.update()
        if hasattr(self.host, "view_selected"):
            self.host.view_selected(self, sel)

    def is_selected(self, cfg: dict) -> bool:
        s = self.selected
        return bool(s) and s[0] == "tile" and s[1] is cfg

    # ---- components
    def _make_tile(self, cfg: dict) -> Tile:
        t = TILE_TYPES[cfg["kind"]](self, self.canvas, cfg)
        t.show()
        self.canvas.tiles.append(t)
        self.canvas.fit()
        return t

    def _update_count(self):
        tiles = self.view["tiles"]
        g = sum(1 for t in tiles if is_graph(t))
        self.count_lbl.setText(f"  {len(tiles)}/{MAX_TILES} components · {g}/{MAX_GRAPHS} graphs")
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

    def _free_y(self) -> int:
        y = max((t.geometry().bottom() for t in self.tiles), default=0)
        return self.snap(y + 20) if self.tiles else self.snap(10)

    def add_tile(self, kind: str, **preset):
        """A new component below the others with sensible defaults - the host's pop-up first
        ("New component", Add), else at once."""
        ch = self.host.channel_choices()
        cfg = new_tile(kind, self.snap(10), self._free_y(), show_title=bool(self.view.get("title_bar", True)),
                       **preset)
        if not self._room(cfg):
            return
        cfg["y"] = max(0, min(cfg["y"], self.snap(int(self.view.get("height", 1080)) - cfg["h"])))
        if kind in ("plot", "stats", "tags") and ch:   # start with every tag of the first device
            first = ch[0][0]
            cfg["channels"] = [[tid, idx] for tid, idx, *_ in ch if tid == first]
            if kind == "plot" and cfg["plot"] == "stft":
                cfg["channels"] = cfg["channels"][:1]
        elif kind in ("value", "gauge", "led") and ch:
            cfg["channel"] = [ch[0][0], ch[0][1]]
        if kind == "gauge":
            cfg["decimals"] = 1
        if hasattr(self.host, "new_component"):
            self.host.new_component(self, cfg)       # Add in its pop-up -> insert_tile
            return
        self.insert_tile(cfg)

    def insert_tile(self, cfg: dict):
        self.view["tiles"].append(cfg)
        t = self._make_tile(cfg)
        t.raise_()
        self.scroll.ensureWidgetVisible(t, 20, 20)
        self._update_count()
        self.host.view_changed()
        self.select(("tile", cfg))

    def edit_tile(self, tile: Tile):
        """All settings of a component: an editor tab when the host has them, else a pop-up."""
        if hasattr(self.host, "edit_component"):
            self.host.edit_component(self, tile.cfg)
            return
        dlg = TileDialog(self, tile.cfg, self.host.channel_choices())
        if dlg.exec():
            tile.cfg.update(dlg.result_cfg())
            self.tile_changed(tile.cfg)

    def tile_changed(self, cfg: dict, geometry: bool = False):
        """A component's settings changed (dialog or Properties panel)."""
        tile = self.tile_of(cfg)
        if tile is None:
            return
        if geometry:                                 # inside the view's fixed size
            vw, vh = int(self.view.get("width", 1920)), int(self.view.get("height", 1080))
            cfg["w"] = min(max(MIN_W, int(cfg["w"])), vw)
            cfg["h"] = min(max(MIN_H, int(cfg["h"])), vh)
            cfg["x"] = min(max(0, int(cfg["x"])), vw - cfg["w"])
            cfg["y"] = min(max(0, int(cfg["y"])), vh - cfg["h"])
            tile.setGeometry(cfg["x"], cfg["y"], cfg["w"], cfg["h"])
            self.canvas.fit()
        tile.apply_cfg()
        tile.update()
        if self.editing:
            tile.show_mock()
        self.host.view_changed()

    def geometry_changed(self, cfg: dict):
        """Moved / resized with the mouse: the Properties panel shows the new position."""
        self.host.view_changed()
        self.select(("tile", cfg))

    def redraw_tile(self, tile: Tile):
        """Settings changed in live mode: draw at once, no empty frame."""
        if not self.editing and self._last and isinstance(tile, PlotTile):
            tile._t_an = 0.0
            tile.draw(*self._last)

    def duplicate_tile(self, tile: Tile):
        if not self._room(tile.cfg):
            return
        cfg = copy.deepcopy(tile.cfg)
        cfg.update(id=new_tile(cfg["kind"])["id"], x=self.snap(cfg["x"] + 20), y=self.snap(cfg["y"] + 20))
        self.view["tiles"].append(cfg)
        self._make_tile(cfg).raise_()
        self._update_count()
        self.host.view_changed()
        self.select(("tile", cfg))

    def delete_tile(self, tile: Tile):
        if tile not in self.canvas.tiles:
            return
        self.canvas.tiles.remove(tile)
        self.view["tiles"] = [x for x in self.view["tiles"] if x is not tile.cfg]
        tile.deleteLater()
        self.canvas.fit()
        self._update_count()
        self.host.view_changed()
        if self.is_selected(tile.cfg):
            self.select(None)

    # ---- modes / updates
    def set_edit(self, on: bool):
        self.editing = on
        self.btn_edit.setText("Done" if on else "Edit")
        for b in self.add_btns:
            b.setVisible(on)
        self.hint.setVisible(on)
        self._update_count()
        for t in self.tiles:
            t.set_edit(on)
        self.canvas.update()
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

    def _align(self):
        a = Qt.AlignmentFlag.AlignTop | (Qt.AlignmentFlag.AlignHCenter if self.view.get("align") == "center"
                                         else Qt.AlignmentFlag.AlignLeft)
        self.scroll.setAlignment(a)

    def apply_view(self):
        """View properties changed: size, alignment, canvas, grid, component defaults."""
        self.canvas.fit()
        self._align()
        self.canvas.update()
        for t in self.tiles:
            t.apply_cfg(); t.update()
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
