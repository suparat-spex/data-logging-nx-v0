"""
start_page.py - the Start Page document (like the start pages of LabVIEW NXG, BenchVue,
UaExpert): where you begin - open a recent project, start a new one from a template, find a
recording, get started, read the docs. The first tab; closable, back from the toolbar (house),
View > Start Page or a click on the project in the Project tree.

  banner            app name, version, one line
  left column       Current project (name, file, status line, buttons) - only with a project
                    Recent projects (click to open) · Browse...
                    New project: template tiles (core/templates.py)
                    Recent sessions (click: open the folder)
  right column      Get started (steps 1/4 with < >, each with its button)
                    Documentation (docs/*.md) · Simulators (from the source checkout)
  footer            [x] Show the Start Page at startup

Two columns on a wide window, one (right under left) below 980 px.

Host API (duck-typed, DaqWindow):
  host.project, host.project_path, host.dirty, host.recording, host.reg_icon(target, name, token)
  host.recent_projects() -> [path] / host.open_recent(path) / host.browse_project()
  host.new_from_template(key)
  host.recent_sessions(n) -> [(start unix, project name, folder, bytes)] / host.open_path(path)
  host.start_status() -> {"devices", "online", "tags", "alarms", "recording", "elapsed"}
  host.show_doc(key) / host.new_view() / host.start_recording()
  host.doc_links() -> [(title, path)] / host.simulators() -> [(title, text, module, ok)]
  host.run_simulator(module)
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QTimer
from PySide6.QtWidgets import (QBoxLayout, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton,
                               QScrollArea, QSizePolicy, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from nxlogger import __version__
from nxlogger.core import templates as tpl
from nxlogger.ui.theme.ui_kit import T, label, make_icon, set_prop

SHOW_KEY = "start/show_at_startup"                # QSettings: open the Start Page when the app starts
NARROW = 980                                      # below: one column
RIGHT_W = 340                                     # the right column on a wide page (fixed)
STEPS = [   # title, text, button, what it does (host action)
    ("Connect a device", "Add a Modbus, MQTT, OPC UA or simulation device in Connectivity, then connect.",
     "Open Connectivity", "connectivity"),
    ("Add tags", "Pick the values of the device: addresses, topics or OPC UA nodes. Group them by area.",
     "Open Tags", "tags"),
    ("Create a view", "Lay out trends, numbers, gauges and tables on a snap grid.", "New view...", "view"),
    ("Start recording", "Log the tags to CSV or WPK files - open them later in the NX Viewer.",
     "Start recording", "record"),
]


def show_at_startup() -> bool:
    v = QSettings().value(SHOW_KEY, True)
    return v not in (False, "false", "0", 0)


class _Tile(QFrame):
    """A template card: icon, title, one or two lines; click = use it."""

    def __init__(self, icon: str, title: str, text: str, on_click):
        super().__init__()
        self.setObjectName("startTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(150, 96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)   # grows with its text
        self._click, self._icon = on_click, icon
        v = QVBoxLayout(self); v.setContentsMargins(12, 10, 12, 10); v.setSpacing(4)
        head = QHBoxLayout(); head.setSpacing(6)
        self.ic = QLabel()
        self.title = QLabel(title); self.title.setObjectName("startTileTitle")
        head.addWidget(self.ic); head.addWidget(self.title, 1)
        self.text = label(text, muted=True, small=True); self.text.setWordWrap(True)
        v.addLayout(head); v.addWidget(self.text, 1)
        self.restyle()

    def restyle(self):
        self.ic.setPixmap(make_icon(self._icon, T["accent"]).pixmap(QSize(18, 18)))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self._click()
        super().mouseReleaseEvent(e)


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame(); f.setObjectName("startCard")
    v = QVBoxLayout(f); v.setContentsMargins(16, 12, 16, 14); v.setSpacing(8)
    h = QLabel(title); h.setObjectName("startHeading")
    v.addWidget(h)
    return f, v


def _list(cols: list[str]) -> QTreeWidget:
    t = QTreeWidget(); t.setObjectName("startList")
    t.setHeaderLabels(cols); t.setHeaderHidden(True); t.setRootIsDecorated(False)
    t.setUniformRowHeights(True); t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    t.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    t.setCursor(Qt.CursorShape.PointingHandCursor)
    t.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)   # never wider than the page
    t.header().setStretchLastSection(True)
    return t


def _fit(t: QTreeWidget, rows: int):
    """No inner scrolling: as tall as its rows (the page scrolls)."""
    h = t.sizeHintForRow(0) if t.topLevelItemCount() else 22
    t.setFixedHeight(max(1, rows) * max(h, 22) + 4)


def _size(n: int | None) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class StartPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._step = 0
        self.tiles: list[_Tile] = []

        banner = QFrame(); banner.setObjectName("startBanner")
        bh = QHBoxLayout(banner); bh.setContentsMargins(24, 14, 24, 14); bh.setSpacing(14)
        self.logo = QLabel(); self.word = QLabel(); self.word.setObjectName("startWordmark")
        ver = label(f"Version {__version__}", muted=True, small=True)     # banner: logo, name, version
        col = QVBoxLayout(); col.setSpacing(1); col.addWidget(self.word); col.addWidget(ver)
        bh.addWidget(self.logo); bh.addLayout(col); bh.addStretch(1)

        # left column -------------------------------------------------------------
        self.cur_card, cv = _card("Current project")
        row = QHBoxLayout(); row.setSpacing(10)
        self.cur_name = QLabel(); self.cur_name.setObjectName("startTileTitle")
        self.cur_file = label("", muted=True, small=True); self.cur_file.setWordWrap(True)
        row.addWidget(self.cur_name); row.addWidget(self.cur_file, 1)
        cv.addLayout(row)
        self.cur_status = label("", small=True); self.cur_status.setObjectName("statusMsg")
        self.cur_status.setWordWrap(True)
        cv.addWidget(self.cur_status)
        btns = QGridLayout(); btns.setSpacing(6)
        for text, icon, slot in (("Connectivity", "network", lambda: host.show_doc("connectivity")),
                                 ("Tags", "tag", lambda: host.show_doc("tags")),
                                 ("Views", "layout", None),
                                 ("Alarms", "bell", lambda: host.show_doc("alarms")),
                                 ("Logging", "file-text", lambda: host.show_doc("logging"))):
            b = QPushButton(text); host.reg_icon(b, icon)
            if slot is None:                         # Views: a menu of the project's views + New view...
                self.views_menu = QMenu(b)
                self.views_menu.aboutToShow.connect(self._fill_views_menu)
                b.setMenu(self.views_menu)
                self.views_btn = b
            else:
                b.clicked.connect(slot)
            self._cur_btns = getattr(self, "_cur_btns", []) + [b]
        self._cur_grid = btns
        cv.addLayout(btns)

        self.recent_card, rv = _card("Recent projects")
        self.recent = _list(["Name", "Folder", "Changed"])
        self.recent.itemClicked.connect(lambda it, _c: it.data(0, Qt.ItemDataRole.UserRole) and
                                        host.open_recent(it.data(0, Qt.ItemDataRole.UserRole)))
        rv.addWidget(self.recent)
        browse = QToolButton(); browse.setText("Browse...")
        browse.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon); browse.setAutoRaise(True)
        host.reg_icon(browse, "folder-open"); browse.clicked.connect(host.browse_project)
        rv.addWidget(browse, 0, Qt.AlignmentFlag.AlignLeft)

        new_card, nv = _card("New project")
        self.grid = QGridLayout(); self.grid.setSpacing(8)
        for key, title, text, icon in tpl.TEMPLATES:
            t = _Tile(icon, title, text, lambda k=key: host.new_from_template(k))
            t.setToolTip(f"New project from the {title} template")
            self.tiles.append(t)
        self._tile_cols = 0
        self._place_tiles(3)
        nv.addLayout(self.grid)

        self.sess_card, sv = _card("Recent sessions")
        self.sessions = _list(["Started", "Project", "Size"])
        self.sessions.itemClicked.connect(lambda it, _c: it.data(0, Qt.ItemDataRole.UserRole) and
                                          host.open_path(it.data(0, Qt.ItemDataRole.UserRole)))
        sv.addWidget(self.sessions)

        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(12)
        for w in (self.cur_card, self.recent_card, new_card, self.sess_card):
            lv.addWidget(w)
        lv.addStretch(1)

        # right column ------------------------------------------------------------
        steps, stv = _card("Get started")
        heading = stv.takeAt(0).widget()             # the heading and < 1/4 > on one row
        nav = QHBoxLayout(); nav.setSpacing(4)
        self.step_n = label("", muted=True, small=True)
        self.prev_btn, self.next_btn = QToolButton(), QToolButton()
        self.prev_btn.setText("‹"); self.next_btn.setText("›")
        for b, d in ((self.prev_btn, -1), (self.next_btn, 1)):
            b.setAutoRaise(True); b.clicked.connect(lambda _=False, dd=d: self._go(dd))
        nav.addWidget(heading); nav.addStretch(1)
        nav.addWidget(self.prev_btn); nav.addWidget(self.step_n); nav.addWidget(self.next_btn)
        stv.insertLayout(0, nav)
        self.step_title = QLabel(); self.step_title.setObjectName("startTileTitle")
        self.step_text = label("", muted=True); self.step_text.setWordWrap(True)
        self.step_text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for wdg in (self.step_title, self.step_text):     # the column sets the width, never the step text
            wdg.setSizePolicy(QSizePolicy.Policy.Ignored, wdg.sizePolicy().verticalPolicy())
        self.steps_card = steps
        steps.installEventFilter(self)               # its width changed: measure the steps again
        self.step_btn = QPushButton(); self.step_btn.setProperty("primary", True)
        self.step_btn.clicked.connect(self._do_step)
        stv.addWidget(self.step_title); stv.addWidget(self.step_text)
        stv.addWidget(self.step_btn, 0, Qt.AlignmentFlag.AlignLeft)

        docs, dv = _card("Documentation")
        self.docs_box = QVBoxLayout(); self.docs_box.setSpacing(2)
        dv.addLayout(self.docs_box)
        sims, smv = _card("Simulators")
        self.sims_box = QVBoxLayout(); self.sims_box.setSpacing(2)
        smv.addLayout(self.sims_box)
        right = QWidget(); right.setFixedWidth(RIGHT_W)   # one width: stepping never resizes the cards
        rv2 = QVBoxLayout(right); rv2.setContentsMargins(0, 0, 0, 0); rv2.setSpacing(12)
        for w in (steps, docs, sims):
            rv2.addWidget(w)
        rv2.addStretch(1)
        self.right = right

        body = QWidget(); body.setObjectName("startBody")
        self.cols = QBoxLayout(QBoxLayout.Direction.LeftToRight); self.cols.setSpacing(16)
        self.cols.addWidget(left, 1); self.cols.addWidget(right)
        bv = QVBoxLayout(body); bv.setContentsMargins(24, 16, 24, 16); bv.setSpacing(12)
        bv.addLayout(self.cols, 1)
        self.show_box = QCheckBox("Show the Start Page at startup", checked=show_at_startup())
        self.show_box.toggled.connect(lambda on: QSettings().setValue(SHOW_KEY, on))
        self.show_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)   # no sideways scrolling
        bv.addWidget(self.show_box)
        scroll = QScrollArea(); scroll.setObjectName("startScroll")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # fits the width, never sideways
        scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame); scroll.setWidget(body)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(banner); v.addWidget(scroll, 1)
        self._fill_links()
        self.refresh()

    # ------------------------------------------------------------------ content
    def refresh(self):
        """Project opened / saved / changed: every section again."""
        self.restyle()
        self._fill_recent()
        self._fill_sessions()
        self.update_live()
        self._show_step()

    def restyle(self):
        self.logo.setPixmap(make_icon("activity", T["accent"]).pixmap(QSize(34, 34)))
        self.word.setText(f"<span style='color:{T['accent']}'>NX</span> Logger")
        for t in self.tiles:
            t.restyle()

    def update_live(self):
        """Current project: name, file and the status line (twice a second while visible)."""
        p, path = self.host.project, self.host.project_path
        self.cur_name.setText(p.name + (" *" if self.host.dirty else ""))
        self.cur_file.setText(f"{os.path.basename(path)} · {os.path.dirname(path)}" if path else "Not saved yet")
        s = self.host.start_status()
        parts = [f"● {s['online']} / {s['devices']} devices online", f"{s['tags']} tags",
                 (f"⚠ {s['alarms']} active alarm(s)" if s["alarms"] else "no active alarms"),
                 (f"● REC {s['elapsed']}" if s["recording"] else "not recording")]
        text = "  ·  ".join(parts)
        if self.cur_status.text() != text:
            self.cur_status.setText(text)
            set_prop(self.cur_status, "level", "bad" if s["alarms"] else ("ok" if s["online"] else "muted"))

    def _fill_recent(self):
        t = self.recent
        t.clear()
        rows = self.host.recent_projects()
        for path in rows:
            try:
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
            except OSError:
                when = "missing"
            it = QTreeWidgetItem([os.path.splitext(os.path.basename(path))[0], os.path.dirname(path), when])
            it.setData(0, Qt.ItemDataRole.UserRole, path)
            it.setIcon(0, make_icon("file-text", T["muted"]))
            it.setToolTip(0, path); it.setToolTip(1, path)
            if path == self.host.project_path:
                f = it.font(0); f.setBold(True); it.setFont(0, f)
            t.addTopLevelItem(it)
        if not rows:
            it = QTreeWidgetItem(["No recent projects - open one with Browse... or start a new one below"])
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            t.addTopLevelItem(it)
        for c, w in enumerate((200, 380)):
            t.header().resizeSection(c, w)
        _fit(t, len(rows) or 1)

    def _fill_sessions(self):
        t = self.sessions
        t.clear()
        rows = self.host.recent_sessions(5)
        for start, name, folder, size in rows:
            it = QTreeWidgetItem([time.strftime("%Y-%m-%d %H:%M", time.localtime(start)) if start else "-",
                                  name, _size(size)])
            it.setData(0, Qt.ItemDataRole.UserRole, folder)
            it.setIcon(0, make_icon("database", T["muted"]))
            it.setToolTip(0, f"{folder}\nClick to open the folder")
            t.addTopLevelItem(it)
        if not rows:
            it = QTreeWidgetItem(["No recordings yet - they appear here after the first recording"])
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            t.addTopLevelItem(it)
        for c, w in enumerate((150, 260)):
            t.header().resizeSection(c, w)
        _fit(t, len(rows) or 1)

    def _fill_links(self):
        for box, rows in ((self.docs_box, [(t, p, True) for t, p in self.host.doc_links()]),
                          (self.sims_box, [(t, m, ok) for t, _x, m, ok in self.host.simulators()])):
            while box.count():
                w = box.takeAt(0).widget()
                if w is not None:
                    w.deleteLater()
            if not rows:
                box.addWidget(label("Not available in this installation", muted=True, small=True))
            for title, target, ok in rows:            # wrapping text links: never wider than the column
                docs = box is self.docs_box
                tip = target if docs else next((x for t, x, m, _ok in self.host.simulators() if m == target), "")
                mark = "•" if docs else "▸"
                link = QLabel(f"<a href='go' style='color:{T['accent']}; text-decoration:none'>{mark}&nbsp;{title}</a>"
                              if ok else f"{mark} {title}")
                link.setObjectName("startLink"); link.setWordWrap(True)
                if not ok:
                    link.setStyleSheet(f"color: {T['muted']};")
                link.setToolTip(tip + ("" if ok else "\n(only when NX Logger runs from its source code)"))
                link.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
                if ok:
                    link.linkActivated.connect(lambda _h, t_=target, d_=docs:
                                               self.host.open_path(t_) if d_ else self.host.run_simulator(t_))
                box.addWidget(link)

    # ------------------------------------------------------------------ get started
    def _go(self, d: int):
        self._step = (self._step + d) % len(STEPS)
        self._show_step()

    def _show_step(self):
        title, text, btn, _what = STEPS[self._step]
        self.step_n.setText(f"{self._step + 1}/{len(STEPS)}")
        self.step_title.setText(title); self.step_text.setText(text); self.step_btn.setText(btn)
        self._fix_step_height()
        self.step_btn.setEnabled(not (STEPS[self._step][3] == "record" and self.host.recording))

    def _fix_step_height(self):
        """One height for every step (the longest text at this width), so < > never resizes the card."""
        m = self.steps_card.layout().contentsMargins()   # the card's text width, not the label's (it lags)
        w = max(120, self.steps_card.width() - m.left() - m.right() - 2)
        fm = self.step_text.fontMetrics()
        lines = max(fm.boundingRect(0, 0, w, 10_000, Qt.TextFlag.TextWordWrap, t).height() for _a, t, _b, _c in STEPS)
        self.step_text.setFixedHeight(lines + 2)
        self.step_btn.setFixedWidth(max(fm.horizontalAdvance(b) for _a, _t, b, _c in STEPS) + 36)

    def _fill_views_menu(self):
        m = self.views_menu
        m.clear()
        for vw in self.host.project.views:
            m.addAction(vw["name"], lambda vid=vw["id"]: self.host.show_doc(f"view:{vid}"))
        if not self.host.project.views:
            m.addAction("(no views yet)").setEnabled(False)
        m.addSeparator()
        m.addAction("New view...", self.host.new_view)

    def _do_step(self):
        what = STEPS[self._step][3]
        if what in ("connectivity", "tags"):
            self.host.show_doc(what)
        elif what == "view":
            self.host.new_view()
        else:
            self.host.start_recording()

    # ------------------------------------------------------------------ layout
    def _place_tiles(self, cols: int):
        """Template tiles (and the project buttons) in `cols` columns - reflowed on resize."""
        if cols == self._tile_cols:
            return
        self._tile_cols = cols
        for t in self.tiles:
            self.grid.removeWidget(t)
        for i, t in enumerate(self.tiles):
            self.grid.addWidget(t, i // cols, i % cols)
        for c in range(3):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)
        for b in self._cur_btns:
            self._cur_grid.removeWidget(b)
        per = {3: 5, 2: 3}.get(cols, 1)
        for i, b in enumerate(self._cur_btns):
            self._cur_grid.addWidget(b, i // per, i % per)

    def resizeEvent(self, e):
        w = self.width()
        self._place_tiles(3 if w >= 760 else 2 if w >= 470 else 1)
        narrow = w < NARROW
        want = QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
        if self.cols.direction() != want:
            self.cols.setDirection(want)
            if narrow:                               # under the left column: the full width
                self.right.setMinimumWidth(0); self.right.setMaximumWidth(16777215)
            else:
                self.right.setFixedWidth(RIGHT_W)
        super().resizeEvent(e)

    def eventFilter(self, obj, e):
        if obj is self.steps_card and e.type() == QEvent.Type.Resize and e.size().width() != e.oldSize().width():
            QTimer.singleShot(0, self._fix_step_height)
        return super().eventFilter(obj, e)

    def activate(self):
        """The tab came to the front: fresh lists, and Properties shows the project."""
        self.refresh()
