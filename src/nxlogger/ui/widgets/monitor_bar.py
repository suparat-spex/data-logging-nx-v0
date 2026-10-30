"""
monitor_bar.py - the slim top bar of Monitor mode (full-screen views for watching a test or a
plant on a big screen; a data-logger display, not a control HMI).

  [▤ Overview ▾] ‹ ›  Water Plant A   ● 4/5 online  ⚠ 2 alarms  ● REC 01:12:05   10:42:05  [⛶] [Cycle: Off ▾] [✕ Exit]

View ▾ / ‹ › (Page Up / Page Down) switch views; the status chips follow the logger (twice a
second); ⛶ full screen; Cycle switches to the next view every N seconds; Exit (F11 / Esc)
brings the previous layout back. Clicking the alarm chip leaves Monitor mode on the Alarms page.

Host API (duck-typed, DaqWindow):
  host.project (views), host.reg_icon(target, name, token), host.start_status() -> {"devices",
  "online", "alarms", "recording", "elapsed"}, host.monitor_show(vid), host.exit_monitor(to=None),
  host.set_monitor_fullscreen(on), host.set_monitor_cycle(seconds)
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QToolButton

from nxlogger.ui.theme.ui_kit import label, set_prop, vsep

CYCLES = [(0, "Cycle: off"), (10, "Every 10 s"), (30, "Every 30 s"), (60, "Every 1 min"), (300, "Every 5 min")]


class _Chip(QLabel):
    """A status chip; click -> callback (or nothing)."""

    def __init__(self, on_click=None):
        super().__init__()
        self.setObjectName("monitorChip")
        self._click = on_click
        if on_click:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set(self, text: str, level: str):
        if self.text() != text:
            self.setText(text)
        if self.property("level") != level:
            set_prop(self, "level", level)

    def mouseReleaseEvent(self, e):
        if self._click and e.button() == Qt.MouseButton.LeftButton:
            self._click()
        super().mouseReleaseEvent(e)


class MonitorBar(QFrame):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.setObjectName("monitorBar")
        h = QHBoxLayout(self); h.setContentsMargins(8, 3, 8, 3); h.setSpacing(6)
        self.views = QComboBox(); self.views.setMinimumWidth(180)
        self.views.setToolTip("The view shown (Page Up / Page Down: previous / next)")
        self.views.activated.connect(lambda i: host.monitor_show(self.views.itemData(i)))
        self.prev_btn, self.next_btn = QToolButton(), QToolButton()
        for b, text, tip, d in ((self.prev_btn, "‹", "Previous view (Page Up)", -1),
                                (self.next_btn, "›", "Next view (Page Down)", 1)):
            b.setText(text); b.setToolTip(tip); b.setAutoRaise(True)
            b.clicked.connect(lambda _=False, dd=d: self.step(dd))
        self.project = label("", muted=True)
        self.online, self.alarms, self.rec = _Chip(), _Chip(lambda: host.exit_monitor("alarms")), _Chip()
        self.alarms.setToolTip("Leave Monitor mode and open the Alarms page")
        self.clock = label("")
        self.clock.setObjectName("monitorClock")
        self.full_btn = QToolButton(); self.full_btn.setCheckable(True); self.full_btn.setAutoRaise(True)
        self.full_btn.setToolTip("Full screen")
        host.reg_icon(self.full_btn, "maximize")
        self.full_btn.toggled.connect(host.set_monitor_fullscreen)
        self.cycle = QComboBox()
        for s, text in CYCLES:
            self.cycle.addItem(text, s)
        self.cycle.setToolTip("Show the next view every N seconds (wall displays)")
        self.cycle.currentIndexChanged.connect(lambda _i: host.set_monitor_cycle(self.cycle.currentData()))
        self.exit_btn = QToolButton(); self.exit_btn.setText("Exit")
        self.exit_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.exit_btn.setToolTip("Leave Monitor mode (F11 / Esc)")
        host.reg_icon(self.exit_btn, "x")
        self.exit_btn.clicked.connect(lambda: host.exit_monitor())
        for w in (self.views, self.prev_btn, self.next_btn, self.project):
            h.addWidget(w)
        h.addStretch(1)
        for w in (self.online, self.alarms, self.rec, vsep(), self.clock, vsep(), self.full_btn, self.cycle,
                  self.exit_btn):
            h.addWidget(w)

    # ------------------------------------------------------------------ views
    def fill(self, current: str | None):
        """The project's views in the drop-down, `current` selected."""
        self.views.blockSignals(True)
        self.views.clear()
        for vw in self.host.project.views:
            self.views.addItem(vw["name"], vw["id"])
        i = self.views.findData(current)
        self.views.setCurrentIndex(max(0, i))
        self.views.blockSignals(False)
        many = self.views.count() > 1
        self.prev_btn.setEnabled(many); self.next_btn.setEnabled(many)
        self.cycle.setEnabled(many)

    def step(self, d: int):
        n = self.views.count()
        if n:
            i = (self.views.currentIndex() + d) % n
            self.views.setCurrentIndex(i)
            self.host.monitor_show(self.views.itemData(i))

    def set_options(self, fullscreen: bool, cycle_s: int):
        for w, f in ((self.full_btn, lambda: self.full_btn.setChecked(fullscreen)),
                     (self.cycle, lambda: self.cycle.setCurrentIndex(max(0, self.cycle.findData(cycle_s))))):
            w.blockSignals(True); f(); w.blockSignals(False)

    # ------------------------------------------------------------------ status
    def update_live(self):
        s = self.host.start_status()
        p = self.host.project
        if self.project.text() != p.name:
            self.project.setText(p.name)
        self.online.set(f"● {s['online']} / {s['devices']} online",
                        "ok" if s["devices"] and s["online"] == s["devices"] else ("warn" if s["online"] else "bad"))
        self.alarms.set(f"⚠ {s['alarms']} alarm(s)" if s["alarms"] else "No alarms", "bad" if s["alarms"] else "muted")
        self.rec.set(f"● REC {s['elapsed']}" if s["recording"] else "Not recording",
                     "bad" if s["recording"] else "muted")
        self.clock.setText(time.strftime("%H:%M:%S"))
