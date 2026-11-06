"""
summary_strip.py - the summary under the toolbar of Connectivity, Tags, Assets and Logging: is
everything all right, and what to do when not. One line when healthy, a second line only for the
worst problem:

  ● Connected · 1 h 42 min  │ [5 online] [1 error] [1 disabled] │ 1,240 values/s │ 0 errors /h   [actions]
  ⚠ PLC-02: timeout (192.168.1.12)                                           [Open device ›] [Retry]

  state   ok / warn / bad / muted token of the dot and the title
  chips   (key, text, token): a count in its state colour - a click filters the page by it (the
          page decides), a second click clears it (`chip_clicked(key | None)`); token "" = plain
          text (a figure, not a filter)
  issue   (text, token, [(button text, slot)]) or None - the second line
  actions [(button text, slot)] at the end of the first line (Connect all, Record ...)

Colours only from theme tokens, read at every set() (a theme switch repaints on the next tick).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from nxlogger.ui.theme.ui_kit import T


def plural(n: int, one: str, many: str | None = None) -> str:
    """'1 device', '3 devices' - never 'device(s)'."""
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


class SummaryStrip(QFrame):
    chip_clicked = Signal(object)                    # the chip's key, or None (cleared)

    def __init__(self):
        super().__init__()
        self.setObjectName("subBar")
        v = QVBoxLayout(self); v.setContentsMargins(12, 4, 12, 4); v.setSpacing(2)
        self.row1 = QHBoxLayout(); self.row1.setSpacing(8)
        self.title = QLabel(); f = self.title.font(); f.setBold(True); self.title.setFont(f)
        self.chips_box = QHBoxLayout(); self.chips_box.setSpacing(4)
        self.actions_box = QHBoxLayout(); self.actions_box.setSpacing(4)
        self.row1.addWidget(self.title); self.row1.addSpacing(6)
        self.row1.addLayout(self.chips_box); self.row1.addStretch(1); self.row1.addLayout(self.actions_box)
        self.issue_w = QWidget()
        r2 = QHBoxLayout(self.issue_w); r2.setContentsMargins(0, 0, 0, 0); r2.setSpacing(6)
        self.issue = QLabel(); self.issue.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.issue_btns = QHBoxLayout(); self.issue_btns.setSpacing(4)
        r2.addWidget(self.issue, 1); r2.addLayout(self.issue_btns)
        v.addLayout(self.row1); v.addWidget(self.issue_w)
        self.issue_w.hide()
        self.active = None                           # the chip filtering the page
        self._sig = None                             # what is shown (no rebuild when nothing changed)
        self._chips: list[QWidget] = []
        self._buttons: list[QWidget] = []
        self._issue_buttons: list[QWidget] = []

    def set(self, state: str, title: str, chips=(), issue=None, actions=()):
        sig = (state, title, tuple(chips), (issue[0], issue[1], tuple(t for t, _s in issue[2])) if issue else None,
               tuple(t for t, _s in actions), self.active, T["text"])
        if sig == self._sig:
            return
        self._sig = sig
        self.title.setText(title)
        self.title.setStyleSheet(f"color: {T[state] if state in T else T['text']};")
        for w in self._chips:
            self._drop(self.chips_box, w)
        self._chips = []
        for key, text, token in chips:
            if key == "|":                           # a group separator
                sep = QLabel("│"); sep.setStyleSheet(f"color: {T['border']};")
                self.chips_box.addWidget(sep); self._chips.append(sep)
                continue
            if not token:                            # a figure: plain muted text
                lb = QLabel(text); lb.setStyleSheet(f"color: {T['muted']};")
                self.chips_box.addWidget(lb); self._chips.append(lb)
                continue
            b = QToolButton(); b.setText(text); b.setAutoRaise(True); b.setCheckable(True); b.setFixedHeight(22)
            b.setChecked(key == self.active)
            b.setToolTip("Click: only these · click again: all")
            col = T[token] if token in T else T["text"]
            b.setStyleSheet(f"QToolButton {{ color: {col}; padding: 1px 6px; border-radius: 3px; }}"
                            f"QToolButton:checked {{ border: 1px solid {col}; }}")
            b.clicked.connect(lambda _=False, k=key: self._chip(k))
            self.chips_box.addWidget(b); self._chips.append(b)
        for w in self._buttons:
            self._drop(self.actions_box, w)
        self._buttons = []
        for text, slot in actions:
            b = QPushButton(text); b.setProperty("primary", True); b.clicked.connect(slot)
            b.setFixedHeight(24); b.setStyleSheet("padding: 0 10px;")              # one compact line
            self.actions_box.addWidget(b); self._buttons.append(b)
        for w in self._issue_buttons:
            self._drop(self.issue_btns, w)
        self._issue_buttons = []
        if issue:
            text, token, buttons = issue
            self.issue.setText(f"⚠ {text}")
            self.issue.setStyleSheet(f"color: {T[token] if token in T else T['warn']};")
            for bt, slot in buttons:
                b = QPushButton(bt); b.setFlat(True); b.clicked.connect(slot)
                b.setFixedHeight(22); b.setStyleSheet("padding: 0 8px;")
                self.issue_btns.addWidget(b); self._issue_buttons.append(b)
        self.issue_w.setVisible(bool(issue))

    @staticmethod
    def _drop(box, w):
        """Out of the strip now: hidden and detached (deleteLater alone leaves it painted, full size)."""
        box.removeWidget(w)
        w.hide()
        w.setParent(None)
        w.deleteLater()

    def _chip(self, key):
        self.active = None if key == self.active else key
        self._sig = None                             # repaint the checked chip
        self.chip_clicked.emit(self.active)

    def clear_filter(self):
        if self.active is not None:
            self.active = None
            self._sig = None
