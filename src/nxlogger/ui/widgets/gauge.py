"""
gauge.py - painted dial gauge for view tiles (and the live preview in the tile dialog).

Styles (view_model.GAUGE_STYLES):
  basic        thin scale, ticks + labels, needle
  speed        thick value arc over the scale, ticks, needle, big value
  progress     thick rounded value arc, big value in the middle (no needle)
  stage        coloured zones normal / warning / alarm, needle
  temperature  very thick value arc, scale labels outside, big value + unit
  ring         full 360° ring, value in the middle
Colours: "" = theme tokens (accent for the value, ok / warn / bad for zones).
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from nxlogger.ui.theme.ui_kit import T


START, SPAN = 225.0, 270.0               # degrees (Qt: counter-clockwise from 3 o'clock)


class GaugeWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.value: float | None = None
        self.vmin, self.vmax = 0.0, 100.0
        self.unit, self.decimals, self.style_ = "", 1, "speed"
        self.color, self.warn, self.alarm, self.ticks = "", None, None, 10
        self.setMinimumSize(80, 80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def configure(self, cfg: dict, unit: str = ""):
        self.vmin, self.vmax = float(cfg.get("g_min", 0)), float(cfg.get("g_max", 100))
        if self.vmax <= self.vmin:
            self.vmax = self.vmin + 1
        self.unit = cfg.get("unit") or unit
        self.decimals = int(cfg.get("decimals", 1))
        self.style_ = cfg.get("gauge_style", "speed")
        self.color = cfg.get("g_color", "")
        self.warn, self.alarm = cfg.get("g_warn"), cfg.get("g_alarm")
        self.ticks = max(2, int(cfg.get("g_ticks", 10)))
        self.update()

    def set_value(self, v: float | None):
        if v != self.value:
            self.value = v
            self.update()

    # ---- helpers
    def _frac(self, v: float) -> float:
        return min(max((v - self.vmin) / (self.vmax - self.vmin), 0.0), 1.0)

    def _value_color(self) -> QColor:
        v = self.value
        if v is not None and self.alarm is not None and v >= self.alarm:
            return QColor(T["bad"])
        if v is not None and self.warn is not None and v >= self.warn:
            return QColor(T["warn"])
        return QColor(self.color or T["accent"])

    @staticmethod
    def _font(px: float, bold=False) -> QFont:
        f = QFont()
        f.setPixelSize(max(7, int(px)))
        f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
        return f

    def _arc(self, p, rect, a0, span, color, width, cap=Qt.PenCapStyle.FlatCap):
        pen = QPen(color, width); pen.setCapStyle(cap)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(rect, int(a0 * 16), int(span * 16))

    # ---- paint
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        ring = self.style_ == "ring"
        side = min(w, h * (1.0 if ring else 1.18))
        r = side / 2 * 0.86
        c = QPointF(w / 2, h / 2 + (0 if ring else r * 0.12))
        track, txt, muted = QColor(T["border"]), QColor(T["text"]), QColor(T["muted"])
        vcol = self._value_color()
        f = self._frac(self.value) if self.value is not None else 0.0
        st = self.style_
        thick = {"basic": 0.035, "speed": 0.09, "progress": 0.14, "stage": 0.10,
                 "temperature": 0.20, "ring": 0.16}.get(st, 0.09) * r
        rr = r - thick / 2
        arc_rect = QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr)
        round_cap = Qt.PenCapStyle.RoundCap

        if ring:
            self._arc(p, arc_rect, 90, -360, track, thick)
            if f > 0:
                self._arc(p, arc_rect, 90, -360 * f, vcol, thick, round_cap)
        else:
            if st == "stage":
                zones = [(self.vmin, self.warn if self.warn is not None else self.vmax, T["ok"])]
                if self.warn is not None:
                    zones.append((self.warn, self.alarm if self.alarm is not None else self.vmax, T["warn"]))
                if self.alarm is not None:
                    zones.append((self.alarm, self.vmax, T["bad"]))
                for lo, hi, col in zones:
                    a, b = self._frac(lo), self._frac(hi)
                    if b > a:
                        self._arc(p, arc_rect, START - SPAN * a, -SPAN * (b - a), QColor(col), thick)
            else:
                cap = round_cap if st in ("progress", "temperature") else Qt.PenCapStyle.FlatCap
                self._arc(p, arc_rect, START, -SPAN, track, thick, cap)
                if st != "basic" and f > 0:
                    self._arc(p, arc_rect, START, -SPAN * f, vcol, thick, cap)
            if st in ("basic", "speed", "stage", "temperature"):
                self._ticks(p, c, r, thick, txt, muted, outside=st == "temperature")

        # value text
        val = "-" if self.value is None else f"{self.value:.{self.decimals}f}"
        needle = st in ("basic", "speed", "stage")
        if needle and self.value is not None:
            ang = math.radians(START - SPAN * f)
            ln = r - thick - r * 0.06
            tip = QPointF(c.x() + ln * math.cos(ang), c.y() - ln * math.sin(ang))
            p.setPen(QPen(QColor(T["text"]) if st == "basic" else vcol, max(2.0, r * 0.03),
                          Qt.PenStyle.SolidLine, round_cap))
            p.drawLine(c, tip)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(T["text"]) if st == "basic" else vcol)
            p.drawEllipse(c, r * 0.06, r * 0.06)
        big = r * (0.30 if not needle else 0.24)
        p.setPen(vcol if st != "basic" else txt)
        p.setFont(self._font(big, True))
        y_val = c.y() + (r * 0.50 if needle else -big * 0.05)
        p.drawText(QRectF(c.x() - r, y_val - big * 0.6, 2 * r, big * 1.25),
                   Qt.AlignmentFlag.AlignCenter, val)
        if self.unit:
            p.setPen(muted); p.setFont(self._font(r * 0.13))
            p.drawText(QRectF(c.x() - r, y_val + big * 0.62, 2 * r, r * 0.2),
                       Qt.AlignmentFlag.AlignCenter, self.unit)
        p.end()

    def _ticks(self, p, c, r, thick, txt, muted, outside=False):
        n = self.ticks
        minor = 5 if n <= 12 else 2
        rin = r - thick - r * 0.03
        p.setFont(self._font(r * 0.11))
        dec = 0 if all(abs(self.vmin + (self.vmax - self.vmin) * i / n
                           - round(self.vmin + (self.vmax - self.vmin) * i / n)) < 1e-9
                       for i in range(n + 1)) else 1
        for i in range(n * minor + 1):
            frac = i / (n * minor)
            ang = math.radians(START - SPAN * frac)
            ca, sa = math.cos(ang), -math.sin(ang)
            major = i % minor == 0
            ln = r * (0.09 if major else 0.045)
            r0 = (r + r * 0.02) if outside else rin
            r1 = r0 + ln if outside else r0 - ln
            p.setPen(QPen(txt if major else muted, 1.4 if major else 1.0))
            p.drawLine(QPointF(c.x() + r0 * ca, c.y() + r0 * sa), QPointF(c.x() + r1 * ca, c.y() + r1 * sa))
            if major:
                v = self.vmin + (self.vmax - self.vmin) * frac
                rl = (r1 + r * 0.10) if outside else (r1 - r * 0.12)
                p.setPen(muted)
                p.drawText(QRectF(c.x() + rl * ca - r * 0.2, c.y() + rl * sa - r * 0.07, r * 0.4, r * 0.14),
                           Qt.AlignmentFlag.AlignCenter, f"{v:.{dec}f}")
