"""
main_window.py - NX Logger main window: multi-topic live waveforms + recording.

v6 (professional data-logger features):
  * Alarms document (closable tab, Project Files -> Alarms): alarm definitions with
    4 levels per channel (Level 1-4: H, L, dH, dL, RH, RL, D + hysteresis + on-delay),
    add / edit / delete / enable / disable, active alarm list with ACK (blinking until
    acknowledged, optional latch and sound), alarm log (1000 entries, filter, CSV
    export) and notification profiles (log, popup, REST API webhook, MQTT publish,
    e-mail planned). No alarm banners on top - status bar + toolbar show the count.
    See core/alarm_rules.py.
  * Logging profiles: add / edit / delete / enable several profiles, each Signal
    (waveform, CSV / TDMS / WPK) or Statistic (one row per interval with mean / min /
    max / SD / RMS / P-P / sum / inst, CSV / TDMS) and its own channel selection. All
    enabled profiles write into the same session folder.
  * Views: Statistics table and Alarm table tiles (columns, max rows, levels); edit
    mode shows mock-up data so big views stay fast.

v5 workspace (FlexLogger-like):
  * Left:   "Project Files" dock (top) and "Data" dock (bottom), stacked
  * Centre: closable document tabs - Screen, Channel Specification, Logging
            Specification and any number of custom Views (+ button / Project menu)
  * Right:  "Status" dock (toggle in the toolbar); bottom: "Log" dock (toggle too)
  * Plots:  black data area, drag a box to zoom (plot then holds), double-click or
            "Reset zoom" returns to live; Stack / Grid layout
  * Views:  free-form canvas of plot / heading-text / numeric tiles with Edit and
            Live modes, saved in the project (see ui/widgets/views.py)
Layout still adapts to laptop screens like v4.

Works like NI FlexLogger, on MQTT sources:
  * Project (.sxl, JSON; .mqdaq from MQDAQ v6 opens too): name, operator, description, topics, channels, logging.
  * Channel Specification: add / edit / delete / enable topics (each topic = one
    device publishing wavepacket blocks), rename channels (CH1 -> "Temperature"),
    set units, enable channels. Live value per channel.
  * Logging Specification: folder + file format (CSV, TDMS, raw .wpk) [+ raw copy].
  * Screen: live plots, per topic or per channel.
  * Record / Stop: each run makes a session folder <project>_<date_time>/ with the
    data files and project.json. Only enabled topics and channels are logged.
    The configuration is locked while recording.

Files are written by core/recording.py (no Qt), which also has the readers for the
next steps (viewer, statistics, ML).

Responsive layout (by window width):
  wide    >= 1500 px  project tree | plots | status panel, full toolbar text
  medium  1180-1500  narrower docks, toolbar text only on Connect / Record / Stop
  compact  < 1180    status panel auto-hidden (toolbar button brings it back),
                     icon-only toolbar, short tab names
  Small screens (< 1440 x 860) also get a denser style (smaller paddings and icons).
  The window always opens inside the screen, even if it was last used on a bigger one.

Run:  python -m nxlogger
"""
from __future__ import annotations

import copy
import json
import os
import queue
import time
import uuid
from contextlib import contextmanager

import numpy as np
from PySide6.QtCore import QSettings, QSize, QTimer, QUrl, Qt
from PySide6.QtGui import (QActionGroup, QBrush, QColor, QDesktopServices, QIcon, QKeySequence,
                           QPainter, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
                               QDockWidget, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QStackedWidget, QTabWidget, QTableWidget,
                               QTableWidgetItem, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)
import paho.mqtt.client as mqtt
import pyqtgraph as pg

from nxlogger import APP_NAME, paths
from nxlogger.comms.mqtt_client import MqttClient
from nxlogger.core import alarm_rules as ar
from nxlogger.core import channel_math as cm
from nxlogger.core import recording as dl
from nxlogger.core import wavepacket as wp
from nxlogger.core.project import (COLORS, ChannelCfg, OLD_PROJECT_EXT, PROJECT_EXT, Project, TopicCfg, ch_color,
                                   fmt_interval, new_log_profile)
from nxlogger.core.ring import MAX_WINDOW_S, TopicStream
from nxlogger.ui.dialogs.about_dialog import show_about
from nxlogger.ui.dialogs.channel_dialog import ChannelDialog
from nxlogger.ui.dialogs.connection_dialog import ConnectionDialog
from nxlogger.ui.dialogs.log_profile_dialog import LogProfileDialog
from nxlogger.ui.dialogs.topic_dialog import TopicDialog
from nxlogger.ui.icons import check_icon_file, grip_icon_file, level_color, level_icon, swatch
from nxlogger.ui.theme import ui_kit as ui
from nxlogger.ui.theme.app_style import DENSE_QSS, EXTRA_QSS, blend, fusion_palette, fusion_qss
from nxlogger.ui.theme.ui_kit import (AppWindow, Card, Chip, EmptyState, T, label, make_icon,
                                      set_prop)
from nxlogger.ui.widgets import alarm_panel as ap
from nxlogger.ui.widgets import views as dv
from nxlogger.ui.widgets.mqtt_panel import ThemedMqttPanel


def _brk(path: str) -> str:
    """Let long paths wrap after each separator (a path has no spaces, so a QLabel
    would otherwise force the window wider than a laptop screen)."""
    return path.replace("/", "/\u200b").replace("\\", "\\\u200b")


# window-width breakpoints (px)
BP_COMPACT, BP_WIDE = 1180, 1500
DOCK_W = {"compact": (230, 300), "medium": (260, 320), "wide": (290, 400)}   # (left, right)
CHIP_W = 132                    # status chips keep one width whatever the text

STAT_COLS = ["Mean", "Min", "Max", "SD", "RMS", "P-P"]
SPEC_COLS = ["Name", "Topic / Channel", "Unit", "Live value"] + STAT_COLS + ["Details"]
COL_DETAILS = len(SPEC_COLS) - 1
LOCKED_COLS = {"Name"}               # always visible


# ======================================================================= window
class DaqWindow(AppWindow):
    TITLE = APP_NAME
    LEFT_TITLE, RIGHT_TITLE = "Project Files", "Status"
    LEFT_W, RIGHT_W = 300, 470

    # ---------------------------------------------------------------- init
    def __init__(self):
        self.q: "queue.Queue[tuple[float, str, bytes]]" = queue.Queue(maxsize=20000)
        self.overflow = 0
        self.project = Project.default()
        self.project_path: str | None = None
        self.dirty = False
        self.recorder: dl.Recorder | None = None
        self.streams: dict[str, TopicStream] = {}
        self.t_ref: float | None = None
        self._routes: dict[str, list[str]] = {}
        self._busy_on = False
        self._loading = False
        self.plots: list[pg.PlotItem] = []
        self.curves: dict[tuple[str, int], pg.PlotDataItem] = {}
        self._mode: str | None = None          # compact | medium | wide
        self._dense = self._screen_is_small()  # read by apply_theme() during super().__init__
        self._auto_hidden_right = False
        self._status_full = ""
        self._zoomed = False                   # Screen plots hold their range after a box zoom
        self._grid = False                     # Screen plots: stack (False) or 2-column grid
        self._waitcur = False
        self.view_pages: dict[str, dv.ViewPage] = {}
        self.control_style = str(QSettings().value("control_style", "fusion"))
        self._scalers: dict[tuple[str, int], object] = {}
        self.docs: dict[str, tuple] = {}       # key -> (widget, title, short title, icon)
        self.alarm_engine = ar.AlarmEngine()
        self.alarm_engine.describe = self._alarm_describe
        self.alarm_engine.ref_value = lambda tid, idx: self.last_value(tid, idx)[0]
        self.alarm_engine.listeners.append(self._on_alarm_event)
        self.notifier = ar.Notifier(lambda *a: self.mqtt.publish(*a))
        self.profile_results: dict[str, tuple[bool, str, float]] = {}
        self._alarm_level = None               # last indicator state (repaint only on change)
        super().__init__()
        self.theme_listeners.append(self._rebuild_plots)
        self.left_dock.setMinimumWidth(210)
        self.left_dock.widget().setMinimumWidth(200)   # ui_kit's scroll area asks for 300
        self.right_dock.setMinimumWidth(280)       # ui_kit default (460) is too wide for laptops
        self.setMinimumSize(960, 600)
        self.status_msg.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._resp_timer = QTimer(self, singleShot=True, interval=60, timeout=self._apply_responsive)
        self._fit_to_screen()
        self._apply_responsive()
        self.theme_listeners.append(self._rebuild_project_tree)
        self.banners.lay.setContentsMargins(8, 0, 8, 0)   # no empty gap above the tabs
        self.theme_listeners.append(self._refresh_chrome)
        self._refresh_chrome()
        self.doc_tabs.currentChanged.connect(lambda _: self._draw(force=True))
        self._build_help_menu()
        self.alarm_btn = QToolButton(); self.alarm_btn.setObjectName("alarmStatus")
        self.alarm_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.alarm_btn.setToolTip("Alarms - click to open the Alarms document")
        self.alarm_btn.clicked.connect(lambda: self._show_doc("alarms"))
        self.statusBar().insertPermanentWidget(1, self.alarm_btn)   # 0 = the message label
        self.theme_listeners.append(lambda: (setattr(self, "_alarm_level", None),
                                             self._update_alarm_indicator(), self.alarm_page.refresh()))

        self.mqtt.on_payload = self._on_payload_net_thread
        self.mqtt.connected.connect(self._on_connected)
        self.mqtt.status.connect(self._on_mqtt_status)
        self.mqtt_panel.btn.clicked.connect(self._sync_conn)

        last = QSettings().value("last_project", "")
        if last and os.path.isfile(last):
            try:
                self._load_project(last)
            except Exception as e:
                self._log(f"Could not open last project {last}: {e}")
                self._set_project(Project.default(), None)
        else:
            self._set_project(Project.default(), None)

        self.draw_timer = QTimer(self, interval=33, timeout=self._drain_and_draw)
        self.draw_timer.start()
        self.stat_timer = QTimer(self, interval=500, timeout=self._update_stats)
        self.stat_timer.start()

    def apply_theme(self):
        t = ui.THEMES[ui.resolve_theme(self.theme_mode)]
        qss = (EXTRA_QSS + (DENSE_QSS if self._dense else "")) \
            .replace("@sel@", blend(t["accent"], t["panel2"], 0.28)) \
            .replace("@check@", check_icon_file(t["accent_text"])) \
            .replace("@grip_v@", grip_icon_file(t["muted"], True)) \
            .replace("@grip_h@", grip_icon_file(t["muted"], False))
        if self.control_style == "fusion":
            T.clear(); T.update(t)
            app = QApplication.instance()
            app.setStyle("Fusion")
            app.setPalette(fusion_palette(t))
            raw = fusion_qss(ui.QSS + qss)
            for k, v in t.items():
                raw = raw.replace(f"@{k}@", str(v))
            app.setStyleSheet(raw)
        else:
            ui.apply_theme(self.theme_mode, qss)
        self.refresh_icons()
        for fn in self.theme_listeners:
            fn()

    # ---------------------------------------------------------------- toolbar / menus
    def build_toolbar_items(self, tb):
        self.brand = QLabel(); self.brand.setObjectName("brand")
        self.brand.setToolTip(f"{APP_NAME} - MQTT data logger")
        tb.addWidget(self.brand)
        tb.addSeparator()
        tb.addAction(self.act("New", self._new_project, "Ctrl+N", "file-plus"))
        tb.addAction(self.act("Open...", self._open_project, "Ctrl+O", "folder-open"))
        tb.addAction(self.act("Save", self._save_project, "Ctrl+S", "save"))
        tb.addSeparator()
        tb.addAction(self.act_connect)
        tb.addSeparator()
        tb.addAction(self.act_rec); tb.addAction(self.act_stop)
        self.rec_label = QLabel("Ready")
        self.rec_label.setObjectName("recLabel")
        tb.addWidget(self.rec_label)
        tb.addSeparator()
        self.act_pause = self.act("Pause view", lambda: self._draw(force=True), "Ctrl+P", "pause")
        self.act_pause.setCheckable(True)
        tb.addAction(self.act_pause)
        tb.addAction(self.act("Clear view", self._clear_view, "Ctrl+L", "trash"))
        tb.addSeparator()
        tb.addAction(self.act_alarms); tb.addAction(self.act_ack)
        self.add_spacer(tb)
        self.act_log = self.log_dock.toggleViewAction()
        self.act_log.setText("Log panel")
        self.act_log.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self.reg_icon(self.act_log, "panel-bottom")
        tb.addAction(self.act_log)
        self.act_panel = self.right_dock.toggleViewAction()
        self.act_panel.setText("Status panel")
        self.act_panel.setShortcut(QKeySequence("Ctrl+Shift+P"))
        self.act_panel.triggered.connect(lambda on: setattr(self, "_auto_hidden_right", False))
        self.reg_icon(self.act_panel, "panel-right")
        tb.addAction(self.act_panel)
        for a in (self.act_log, self.act_panel):      # panel toggles: icon only, always
            tb.widgetForAction(a).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        tb.setIconSize(QSize(16, 16) if self._dense else QSize(18, 18))   # top nav icon size
        for a in tb.actions():          # icon-only mode needs the names in tooltips
            self._set_tip(a)

    @staticmethod
    def _set_tip(a):
        sc = a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        a.setToolTip(f"{a.text()}  ({sc})" if sc else a.text())

    def build_extra_menus(self, mb):
        # actions shared by the Acquisition menu and the toolbar (menus are built first)
        self.act_connect = self.act("Connect", lambda: self.mqtt_panel.btn.click(), "Ctrl+K", "plug")
        self.act_conn_settings = self.act("Connection settings...", self._show_connection, "Ctrl+,")
        self.act_rec = self.act("Record", self._start_recording, "Ctrl+R", None)
        self.reg_icon(self.act_rec, "circle", "bad")
        self.act_stop = self.act("Stop", self._stop_recording, "Ctrl+Shift+R", "square")
        self.act_stop.setEnabled(False)
        self.act_alarms = self.act("Alarms", lambda: self._show_doc("alarms"), "Ctrl+4", None)
        self.act_alarms.setIcon(make_icon("bell", T["text"]))
        self.act_ack = self.act("ACK all", self._ack_all, "Ctrl+Shift+A", "check-check")
        self.act_ack.setToolTip("Acknowledge all alarms")
        self.act_ack.setEnabled(False)
        proj = QMenu("&Project", self)
        mb.insertMenu(self.view_menu.menuAction(), proj)
        for a in (self.act("Screen", lambda: self._show_doc("screen"), "Ctrl+1", "chart"),
                  self.act("Channel Specification", lambda: self._show_doc("channels"), "Ctrl+2", "share"),
                  self.act("Logging Specification", lambda: self._show_doc("logging"), "Ctrl+3", "file-text"),
                  self.act_alarms,
                  None,
                  self.act("Add topic...", self._add_topic, "Ctrl+T", "plus"),
                  self.act("Add alarm...", lambda: self._alarm_action("add"), "Ctrl+Shift+M", "bell"),
                  self.act("Add logging profile...", lambda: self._add_log_profile("signal"), None, "file-plus"),
                  self.act("New view...", self._new_view, "Ctrl+Shift+N", "layout"),
                  None,
                  self.act("Open recordings folder", self._open_log_folder, None, "folder-open")):
            proj.addSeparator() if a is None else proj.addAction(a)
        v = self.view_menu
        cs = QMenu("Control style", self)
        grp = QActionGroup(self)
        for text, key in (("Fusion (classic)", "fusion"), ("Flat", "flat")):
            a = cs.addAction(text); a.setCheckable(True); a.setChecked(self.control_style == key)
            grp.addAction(a)
            a.triggered.connect(lambda _=False, k=key: self._set_control_style(k))
        v.insertMenu(v.actions()[1], cs)
        first = v.actions()[0]
        v.insertSeparator(first)
        for a in (self.data_dock.toggleViewAction(), self.log_dock.toggleViewAction()):
            v.insertAction(first, a)
        acq = mb.addMenu("&Acquisition")
        for a in (self.act_connect, self.act_conn_settings, None, self.act_rec, self.act_stop,
                  None, self.act_ack):
            acq.addSeparator() if a is None else acq.addAction(a)
        f = self.file_menu
        first = f.actions()[0]
        for a in (self.act("Save as...", self._save_project_as, "Ctrl+Shift+S"),):
            f.insertAction(first, a)
        f.insertSeparator(first)

    # ---------------------------------------------------------------- left dock
    def build_left(self):
        """FlexLogger-style navigator: Project Files tree + Data (recorded sessions)."""
        self.mqtt = MqttClient(self)
        self.mqtt_panel = ThemedMqttPanel(self.mqtt, "daq3", "")
        form = self.mqtt_panel.layout()
        form.setRowVisible(self.mqtt_panel.topic, False)            # topics live in
        form.setRowVisible(self.mqtt_panel.qos.parentWidget(), False)  # the project
        self.conn_dlg = ConnectionDialog(self, self.mqtt_panel)

        return self._build_project_files()      # "Data" is its own dock below (build_right)

    def _nav_bar(self, buttons) -> QFrame:
        bar = QFrame(); bar.setObjectName("navBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(4, 2, 4, 2); bl.setSpacing(0)
        for tip, icon, slot in buttons:
            if tip is None:
                bl.addStretch(1); continue
            b = QToolButton(); b.setToolTip(tip); b.setAutoRaise(True)
            self.reg_icon(b, icon)
            b.clicked.connect(slot)
            bl.addWidget(b)
        return bar

    def _build_project_files(self):
        bar = self._nav_bar([("Add topic (Ctrl+T)", "plus", self._add_topic),
                             ("Edit selected topic", "pencil", self._nav_edit),
                             ("Delete selected topic", "trash", self._nav_delete),
                             (None, None, None),
                             ("Expand all", "expand", lambda: self.ptree.expandAll()),
                             ("Collapse topics", "collapse", self._collapse_topics)])
        self.nav_filter = QLineEdit()
        self.nav_filter.setPlaceholderText("Filter")
        self.nav_filter.setClearButtonEnabled(True)
        fa = self.nav_filter.addAction(make_icon("search", T["muted"]), QLineEdit.ActionPosition.LeadingPosition)
        self.reg_icon(fa, "search", "muted")
        self.nav_filter.textChanged.connect(self._apply_nav_filter)

        self.ptree = QTreeWidget()
        self.ptree.setObjectName("navTree")
        self.ptree.setHeaderHidden(True)
        self.ptree.setIndentation(16)
        self.ptree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ptree.customContextMenuRequested.connect(self._nav_menu)
        self.ptree.itemClicked.connect(lambda it, _c: self._nav_open(it))
        self.ptree.itemActivated.connect(lambda it, _c: self._nav_open(it))
        self.ptree.itemDoubleClicked.connect(self._nav_double_clicked)
        self.ptree_topics: dict[str, QTreeWidgetItem] = {}
        self._dot_cache: dict[str, str] = {}

        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
        v.addWidget(bar)
        fl = QHBoxLayout(); fl.setContentsMargins(6, 0, 6, 0); fl.addWidget(self.nav_filter)
        v.addLayout(fl)
        v.addWidget(self.ptree, 1)
        return w

    def _build_data_tab(self):
        bar = self._nav_bar([("Refresh", "refresh", self._refresh_sessions),
                             ("Open log folder", "folder-open", self._open_log_folder),
                             (None, None, None)])
        self.stree = QTreeWidget()
        self.stree.setObjectName("navTree")
        self.stree.setHeaderLabels(["Session", "Start", "Length"])
        self.stree.setRootIsDecorated(False)
        self.stree.header().setStretchLastSection(False)
        self.stree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2):
            self.stree.header().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.stree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.stree.customContextMenuRequested.connect(self._session_menu)
        self.stree.itemDoubleClicked.connect(lambda it, _c: self._open_path(it.data(0, Qt.ItemDataRole.UserRole)))
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(bar); v.addWidget(self.stree, 1)
        v.addWidget(label("  Double-click a session to open its folder", muted=True, small=True))
        return w

    # ---------------------------------------------------------------- centre: document tabs
    def build_center(self):
        self.doc_tabs = QTabWidget()
        self.doc_tabs.setObjectName("docTabs")
        self.doc_tabs.setDocumentMode(False)          # draw the pane frame (border below)
        self.doc_tabs.setMovable(True)
        self.doc_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.doc_tabs.tabBar().setDrawBase(False)     # no line under / beside the tabs
        for key, w, title, short, icon in (
                ("screen", self._build_screen(), "Screen", "Screen", "chart"),
                ("channels", self._build_channel_spec(), "Channel Specification", "Channels", "share"),
                ("logging", self._build_logging_spec(), "Logging Specification", "Logging", "file-text"),
                ("alarms", self._build_alarms(), "Alarms", "Alarms", "bell")):
            self.docs[key] = (w, title, short, icon)
            self._add_doc_tab(key)
        self.doc_tabs.setCurrentIndex(0)
        return self.doc_tabs

    def _build_screen(self):
        pg.setConfigOptions(antialias=False)
        self.glw = pg.GraphicsLayoutWidget()
        self.screen_stack = QStackedWidget()
        self.screen_stack.addWidget(EmptyState(
            "🧩", "No topics enabled", "Add an MQTT topic in Channel Specification, "
            "or enable one, to see its waveforms here.", "Add topic", self._add_topic))
        self.screen_stack.addWidget(EmptyState(
            "📡", "Waiting for data", "Connect to the broker. Enabled topics are subscribed "
            "automatically and their waveforms appear here.", "Connect", self._connect_from_empty))
        self.screen_stack.addWidget(self.glw)

        bar = QFrame(); bar.setObjectName("bottomBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(10, 6, 10, 6); bl.setSpacing(10)
        self.window_s = QDoubleSpinBox(minimum=0.2, maximum=MAX_WINDOW_S, value=5,
                                       singleStep=1, suffix=" s")
        self.layout_mode = QComboBox(); self.layout_mode.addItems(["Per topic", "Per channel"])
        self.autoscale = QCheckBox("Auto-scale Y", checked=True)
        self.lay_group = QButtonGroup(self)
        lay_btns = []
        for i, (icon, tip) in enumerate((("rows", "Stack plots"), ("grid", "Grid (2 columns)"))):
            b = QToolButton(); b.setCheckable(True); b.setAutoRaise(True); b.setToolTip(tip)
            self.reg_icon(b, icon)
            self.lay_group.addButton(b, i); lay_btns.append(b)
        lay_btns[0].setChecked(True)
        self.lay_group.idClicked.connect(lambda i: (setattr(self, "_grid", i == 1), self._rebuild_plots()))
        for w in (label("Time window", muted=True), self.window_s,
                  label("Plots", muted=True), self.layout_mode, *lay_btns, self.autoscale):
            bl.addWidget(w)
        bl.addStretch(1)
        self.zoom_btn = QToolButton(); self.zoom_btn.setText("Reset zoom")
        self.zoom_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.zoom_btn.setToolTip("Back to live (or double-click a plot). Drag a box on a plot to zoom.")
        self.reg_icon(self.zoom_btn, "zoom-reset")
        self.zoom_btn.clicked.connect(self._reset_zoom)
        self.zoom_btn.setVisible(False)
        bl.addWidget(self.zoom_btn)
        self.glw.scene().sigMouseClicked.connect(lambda ev: ev.double() and self._reset_zoom())
        self.layout_mode.currentTextChanged.connect(self._rebuild_plots)
        self.autoscale.toggled.connect(self._apply_autoscale)

        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(self.screen_stack, 1); v.addWidget(bar)
        return w

    def _build_channel_spec(self):
        bar = QFrame(); bar.setObjectName("docBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(4)
        self.btn_add, self.btn_edit, self.btn_del = QToolButton(), QToolButton(), QToolButton()
        self.btn_edit_ch = QToolButton()
        for b, text, icon, slot in ((self.btn_add, "Add topic", "plus", self._add_topic),
                                    (self.btn_edit, "Edit topic", "pencil", self._edit_topic),
                                    (self.btn_del, "Delete topic", "trash", self._delete_topic),
                                    (self.btn_edit_ch, "Edit channel", "sliders", self._edit_channel)):
            b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.reg_icon(b, icon)
            b.clicked.connect(slot)
            bl.addWidget(b)
        bl.addStretch(1)
        self.spec_hint = label("", muted=True, small=True)
        bl.addWidget(self.spec_hint)
        self.window_s.valueChanged.connect(lambda _: self._update_spec_hint())
        self.btn_cols = QToolButton(); self.btn_cols.setText("Columns")
        self.btn_cols.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_cols.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.reg_icon(self.btn_cols, "columns")
        bl.addWidget(self.btn_cols)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(SPEC_COLS))
        self.tree.setHeaderLabels(SPEC_COLS)
        for col in range(len(SPEC_COLS)):
            if SPEC_COLS[col] in STAT_COLS or col == 3:
                self.tree.headerItem().setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.tree.setAlternatingRowColors(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)   # edit via dialogs
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._spec_menu)
        hh = self.tree.header()
        for col, w in enumerate((200, 220, 60, 110) + (78,) * len(STAT_COLS)):
            hh.resizeSection(col, w)
        hh.setStretchLastSection(True)
        hh.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hh.customContextMenuRequested.connect(lambda pos: self._columns_menu().exec(hh.mapToGlobal(pos)))
        hidden = QSettings().value("spec_hidden_cols", ["RMS", "P-P"])
        hidden = [hidden] if isinstance(hidden, str) else list(hidden or [])
        for col, name in enumerate(SPEC_COLS):
            self.tree.setColumnHidden(col, name in hidden)
        self.btn_cols.setMenu(self._columns_menu())
        self.tree.itemChanged.connect(self._tree_item_changed)
        self.tree.itemDoubleClicked.connect(self._tree_double_clicked)
        self.tree.currentItemChanged.connect(lambda *_: self._update_lock())
        self.tree_items: dict[str, tuple[QTreeWidgetItem, dict[int, QTreeWidgetItem]]] = {}

        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(bar); v.addWidget(self.tree, 1)
        return w

    def _build_logging_spec(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 14, 16, 14); v.setSpacing(10)
        v.addWidget(label("Logging Specification", name="h1"))
        intro = label("Each Record run creates a new session folder inside the log folder, "
                      "containing the data files and project.json (configuration, start/end "
                      "time, packet counts). Only enabled topics and channels are logged.",
                      muted=True)
        intro.setWordWrap(True)
        v.addWidget(intro)
        v.addWidget(label("Test properties", name="sectionTitle"))
        pf = QFormLayout(); pf.setSpacing(8)
        self.p_name = QLineEdit()
        self.p_operator = QLineEdit(); self.p_operator.setPlaceholderText("optional")
        self.p_desc = QPlainTextEdit(); self.p_desc.setPlaceholderText("Test description (saved in the files)")
        self.p_desc.setMaximumHeight(70)
        self.p_file = label("Not saved yet", muted=True, small=True)
        self.p_file.setWordWrap(True)
        pf.addRow("Project name", self.p_name)
        pf.addRow("Operator", self.p_operator)
        pf.addRow("Description", self.p_desc)
        pf.addRow("Project file", self.p_file)
        v.addLayout(pf)
        self.p_name.textEdited.connect(lambda s: self._edit_project("name", s))
        self.p_operator.textEdited.connect(lambda s: self._edit_project("operator", s))
        self.p_desc.textChanged.connect(lambda: self._edit_project("description", self.p_desc.toPlainText()))
        v.addSpacing(6)
        v.addWidget(label("Files", name="sectionTitle"))
        f = QFormLayout(); f.setSpacing(8)
        self.log_folder = QLineEdit()
        browse = QPushButton("Browse..."); browse.clicked.connect(self._browse_log_folder)
        row = QHBoxLayout(); row.addWidget(self.log_folder, 1); row.addWidget(browse)
        f.addRow("Log folder", row)
        v.addLayout(f)
        v.addSpacing(6)
        v.addWidget(label("Logging profiles", name="sectionTitle"))
        bar = QFrame(); bar.setObjectName("subBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(6, 3, 6, 3); bl.setSpacing(4)
        self.lp_btns = []
        for text, icon, slot in (("Add signal", "activity", lambda: self._add_log_profile("signal")),
                                 ("Add statistic", "sigma", lambda: self._add_log_profile("statistic")),
                                 ("Edit", "pencil", lambda: self._edit_log_profile()),
                                 ("Duplicate", "copy", lambda: self._dup_log_profile()),
                                 ("Delete", "trash", lambda: self._delete_log_profile())):
            b = QToolButton(); b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.reg_icon(b, icon); b.clicked.connect(slot)
            bl.addWidget(b); self.lp_btns.append(b)
        bl.addStretch(1)
        bl.addWidget(label("tick = enabled · double-click to edit", muted=True, small=True))
        self.lp_tree = QTreeWidget()
        self.lp_tree.setHeaderLabels(["Profile", "Type", "Format", "Channels", "Details"])
        self.lp_tree.setRootIsDecorated(False)
        self.lp_tree.setAlternatingRowColors(True)
        self.lp_tree.setMinimumHeight(120)
        for c, wd in enumerate((180, 90, 70, 150)):
            self.lp_tree.header().resizeSection(c, wd)
        self.lp_tree.itemDoubleClicked.connect(lambda *_: QTimer.singleShot(0, self._edit_log_profile))
        self.lp_tree.itemChanged.connect(self._lp_toggled)
        self.lp_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lp_tree.customContextMenuRequested.connect(self._lp_menu)
        box = QVBoxLayout(); box.setSpacing(0)
        box.addWidget(bar); box.addWidget(self.lp_tree, 1)
        v.addLayout(box, 1)
        f2 = QFormLayout(); f2.setSpacing(8)
        self.session_preview = label("", muted=True, small=True)
        self.session_preview.setWordWrap(True)
        self.session_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f2.addRow("Session files", self.session_preview)
        v.addLayout(f2)
        self.fmt_help = label("", muted=True, small=True)
        self.fmt_help.setWordWrap(True)
        v.addWidget(self.fmt_help)
        self.log_folder.textEdited.connect(lambda s: self._edit_logging("folder", s))
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(w)   # short laptop screens
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return sc

    def _build_alarms(self):
        self.alarm_page = ap.AlarmPage(self)
        return self.alarm_page

    # ---------------------------------------------------------------- right dock
    def build_right(self):
        self._build_side_docks()
        s = QWidget(); v = QVBoxLayout(s)
        v.setContentsMargins(16, 14, 16, 14); v.setSpacing(10)
        v.addWidget(label("Status", name="h1"))
        chips = QHBoxLayout()
        self.chip_conn, self.chip_rec = Chip(), Chip()
        for c in (self.chip_conn, self.chip_rec):
            c.setFixedWidth(CHIP_W)
            chips.addWidget(c)
        chips.addStretch(1)
        v.addLayout(chips)
        grid = QGridLayout(); grid.setSpacing(8)
        self.cards: dict[str, Card] = {}
        for i, (k, t) in enumerate([("elapsed", "Recording time"), ("size", "Data on disk"),
                                    ("samples", "Samples/ch logged"),
                                    ("packets", "Packets logged"),
                                    ("rx", "Packets received"), ("lost", "Lost / CRC / bad")]):
            self.cards[k] = Card(t)
            grid.addWidget(self.cards[k], i // 2, i % 2)
        v.addLayout(grid)
        v.addWidget(label("Topics", name="sectionTitle"))
        self.topic_table = QTableWidget(0, 5)
        self.topic_table.setHorizontalHeaderLabels(["Topic", "Packets/s", "Sample rate", "Lost", "CRC/bad"])
        self.topic_table.verticalHeader().hide()
        self.topic_table.setAlternatingRowColors(True)
        self.topic_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.topic_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.topic_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.topic_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.topic_table.setMinimumHeight(110)
        v.addWidget(self.topic_table, 1)
        self.last_session = label("", muted=True, small=True)
        self.last_session.setWordWrap(True)
        self.last_session.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.last_session)
        sc = QScrollArea()                       # short laptop screens: scroll, don't squash
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setWidget(s)
        self.lbl_topics = self.add_status_label("")
        self.lbl_rx = self.add_status_label("")
        return sc

    def _build_side_docks(self):
        """Data dock under Project Files, Log dock under the documents."""
        self.data_dock = QDockWidget("Data", self)
        self.data_dock.setObjectName("dockData")
        self.data_dock.setWidget(self._build_data_tab())
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.data_dock)
        self.splitDockWidget(self.left_dock, self.data_dock, Qt.Orientation.Vertical)
        self.resizeDocks([self.left_dock, self.data_dock], [560, 220], Qt.Orientation.Vertical)
        self.data_dock.visibilityChanged.connect(lambda on: on and self._refresh_sessions())

        self.log = QPlainTextEdit(readOnly=True, maximumBlockCount=1000)
        self.log.setFrameShape(QFrame.Shape.NoFrame)
        self.log_dock = QDockWidget("Log", self)
        self.log_dock.setObjectName("dockLog")
        self.log_dock.setWidget(self.log)
        # the bottom area spans only the documents, the side docks keep full height
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.resizeDocks([self.log_dock], [130], Qt.Orientation.Vertical)

    # ================================================================ project
    def _set_project(self, p: Project, path: str | None):
        self.project, self.project_path = p, path
        self._loading = True
        self.p_name.setText(p.name)
        self.p_operator.setText(p.operator)
        self.p_desc.setPlainText(p.description)
        self.p_file.setText(_brk(path) if path else "Not saved yet")
        self.log_folder.setText(p.logging.folder)
        self._loading = False
        self.streams.clear(); self.t_ref = None
        self.dirty = False
        self.alarm_engine.states.clear()
        self.alarm_engine.reset_data_loss()
        self.alarm_engine.clear_log()
        self._topics_changed()
        self._update_logging_help()
        self._update_title()
        self._refresh_sessions()
        self._load_views()

    def _mark_dirty(self):
        if not self._loading:
            self.dirty = True
            self._update_title()

    def _update_title(self):
        self.setWindowTitle(f"{self.project.name}{' *' if self.dirty else ''} - {APP_NAME}")
        root = self.ptree.topLevelItem(0) if hasattr(self, "ptree") else None
        if root is not None:
            root.setText(0, root.text(0).removesuffix(" *") + (" *" if self.dirty else ""))

    def _edit_project(self, key, value):
        if self._loading:
            return
        setattr(self.project, key, value)
        self._mark_dirty()
        self._update_logging_help()
        if key == "name":
            self._rebuild_project_tree()

    def _edit_logging(self, key, value):
        if self._loading:
            return
        setattr(self.project.logging, key, value)
        self._mark_dirty()
        self._update_logging_help()

    def _profile_topics(self, pr: dict) -> list[dict]:
        """Recorder topic list for one logging profile (enabled topics / channels only)."""
        sel = None if pr.get("channels") is None else {tuple(x) for x in pr["channels"]}
        out = []
        for t in self.project.topics:
            if not t.enabled:
                continue
            chans = [(c.index, c.name, c.unit) for c in t.channels
                     if c.enabled and (sel is None or (t.id, c.index) in sel)]
            if chans:
                out.append({"id": t.id, "name": t.name, "topic": t.topic, "channels": chans})
        return out

    def _update_logging_help(self):
        lg = self.project.logging
        name = dl.safe_name(self.project.name)
        files = ["project.json"]
        for pr in self.project.logging_profiles:
            if not pr["enabled"]:
                continue
            pn, fmt = dl.safe_name(pr["name"]), pr["format"]
            topics = self._profile_topics(pr)
            if not topics:
                files.append(f"({pr['name']}: no enabled channels)")
                continue
            if fmt == "TDMS":
                files.append(f"{name}_{pn}.tdms")
            else:
                files += [f"{pn}_{dl.safe_name(t['name'])}{dl.FORMATS[fmt][1]}" for t in topics]
            if pr["type"] == "signal" and pr.get("keep_raw") and fmt != "WPK":
                files += [f"{pn}_{dl.safe_name(t['name'])}.wpk" for t in topics]
        if len(files) == 1:
            files.append("(no enabled logging profile - Record is not possible)")
        self.session_preview.setText(
            _brk(f"{os.path.join(lg.folder, name + '_<yyyymmdd_hhmmss>')}{os.sep}") + "\n    " +
            "\n    ".join(files))
        self.fmt_help.setText(
            "Signal profiles log every sample: CSV (one row per sample, Excel / pandas, large), "
            "TDMS (NI DIAdem / LabVIEW / npTDMS, waveform properties) or raw .wpk packets (smallest, "
            "lossless, daq_logging.read_wpk()). Statistic profiles log one row per interval with the "
            "chosen statistics per channel (CSV or TDMS) - small files for long-term trends and "
            "reports. All enabled profiles run together on Record and write into the same session "
            "folder; each can use its own channels.")
        self._fill_log_profiles()

    def _fill_log_profiles(self):
        tree = getattr(self, "lp_tree", None)
        if tree is None:
            return
        names = {(t.id, c.index): c.name for t in self.project.topics for c in t.channels}
        cur = tree.currentItem().data(0, Qt.ItemDataRole.UserRole) if tree.currentItem() else None
        locked = self.recorder is not None
        tree.blockSignals(True); tree.clear()
        for pr in self.project.logging_profiles:
            stat = pr["type"] == "statistic"
            if pr.get("channels") is None:
                chans = "All enabled"
            else:
                ch = [names.get(tuple(x), "?") for x in pr["channels"]]
                chans = ", ".join(ch[:3]) + (f" +{len(ch) - 3}" if len(ch) > 3 else "")
            details = (f"every {fmt_interval(pr['interval_s'])} · "
                       + ", ".join(dl.STAT_LABELS[k] for k in pr.get("stats", [])) if stat
                       else ("+ raw .wpk" if pr.get("keep_raw") and pr["format"] != "WPK" else ""))
            it = QTreeWidgetItem([pr["name"], "Statistic" if stat else "Signal", pr["format"], chans, details])
            it.setIcon(0, make_icon("sigma" if stat else "activity", T["text" if pr["enabled"] else "muted"]))
            it.setData(0, Qt.ItemDataRole.UserRole, pr["id"])
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if not locked:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            it.setFlags(flags)
            it.setCheckState(0, Qt.CheckState.Checked if pr["enabled"] else Qt.CheckState.Unchecked)
            if not pr["enabled"]:
                for c in range(5):
                    it.setForeground(c, QBrush(QColor(T["muted"])))
            tree.addTopLevelItem(it)
            if pr["id"] == cur:
                tree.setCurrentItem(it)
        tree.blockSignals(False)

    # ---------------------------------------------------------------- logging profiles
    def _log_profile(self, pid: str | None = None) -> dict | None:
        if pid is None:
            it = self.lp_tree.currentItem()
            pid = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        return next((x for x in self.project.logging_profiles if x["id"] == pid), None)

    def _log_profiles_changed(self):
        self._mark_dirty()
        self._update_logging_help()
        self._rebuild_project_tree()

    def _profile_dialog(self, pr: dict) -> dict | None:
        taken = {dl.safe_name(x["name"]).lower() for x in self.project.logging_profiles if x["id"] != pr["id"]}
        dlg = LogProfileDialog(self, pr, self.channel_choices(), taken)
        return dlg.result_profile() if dlg.exec() else None

    def _add_log_profile(self, kind: str):
        if not self._recording_guard():
            return
        base = "Waveform" if kind == "signal" else "Statistics"
        taken = {x["name"] for x in self.project.logging_profiles}
        name = next(n for n in [base] + [f"{base} {i}" for i in range(2, 99)] if n not in taken)
        pr = self._profile_dialog(new_log_profile(kind, name))
        if pr:
            self.project.logging_profiles.append(pr)
            self._log_profiles_changed()
            self._show_doc("logging")
            self._select_log_profile(pr["id"])

    def _edit_log_profile(self, pid: str | None = None):
        pr = self._log_profile(pid)
        if pr is None or not self._recording_guard():
            return
        new = self._profile_dialog(pr)
        if new:
            pr.clear(); pr.update(new)
            self._log_profiles_changed()

    def _dup_log_profile(self, pid: str | None = None):
        pr = self._log_profile(pid)
        if pr is None or not self._recording_guard():
            return
        taken = {x["name"] for x in self.project.logging_profiles}
        b = {**copy.deepcopy(pr), "id": uuid.uuid4().hex[:8]}
        b["name"] = next(n for n in (f"{pr['name']} {i}" for i in range(2, 99)) if n not in taken)
        self.project.logging_profiles.insert(self.project.logging_profiles.index(pr) + 1, b)
        self._log_profiles_changed()

    def _delete_log_profile(self, pid: str | None = None):
        pr = self._log_profile(pid)
        if pr is None or not self._recording_guard():
            return
        if QMessageBox.question(self, "Delete logging profile",
                                f"Delete logging profile '{pr['name']}'?") != QMessageBox.StandardButton.Yes:
            return
        self.project.logging_profiles.remove(pr)
        self._log_profiles_changed()

    def _toggle_log_profile(self, pid: str):
        pr = self._log_profile(pid)
        if pr is not None and self._recording_guard():
            pr["enabled"] = not pr["enabled"]
            self._log_profiles_changed()

    def _lp_toggled(self, it, col):
        pr = self._log_profile(it.data(0, Qt.ItemDataRole.UserRole))
        if pr is None or col != 0 or self.recorder is not None:
            return
        on = it.checkState(0) == Qt.CheckState.Checked
        if on != pr["enabled"]:
            pr["enabled"] = on
            QTimer.singleShot(0, self._log_profiles_changed)     # not inside itemChanged

    def _select_log_profile(self, pid: str):
        for i in range(self.lp_tree.topLevelItemCount()):
            it = self.lp_tree.topLevelItem(i)
            if it.data(0, Qt.ItemDataRole.UserRole) == pid:
                self.lp_tree.setCurrentItem(it)

    def _log_profile_menu(self, m: QMenu, pr: dict | None):
        if pr is not None:
            m.addAction("Edit profile...", lambda: QTimer.singleShot(0, lambda: self._edit_log_profile(pr["id"])))
            m.addAction("Disable" if pr["enabled"] else "Enable", lambda: self._toggle_log_profile(pr["id"]))
            m.addAction("Duplicate", lambda: self._dup_log_profile(pr["id"]))
            m.addAction("Delete profile", lambda: self._delete_log_profile(pr["id"]))
            m.addSeparator()
        m.addAction("Add signal profile...", lambda: self._add_log_profile("signal"))
        m.addAction("Add statistic profile...", lambda: self._add_log_profile("statistic"))

    def _lp_menu(self, pos):
        it = self.lp_tree.itemAt(pos)
        if it is not None:
            self.lp_tree.setCurrentItem(it)
        m = QMenu(self)
        self._log_profile_menu(m, self._log_profile() if it else None)
        for a in m.actions():
            a.setEnabled(self.recorder is None)
        m.exec(self.lp_tree.viewport().mapToGlobal(pos))

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, APP_NAME, f"Save changes to '{self.project.name}'?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                 QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return self._save_project()
        return r == QMessageBox.StandardButton.Discard

    def _new_project(self):
        if self._recording_guard() and self._confirm_discard():
            self._set_project(Project(name="Untitled project", logging_profiles=[new_log_profile("signal")],
                                      alarm_profiles=[ar.new_profile("popup", "Popup + sound")]), None)
            self.toast.popup("New project")

    def _open_project(self):
        if not (self._recording_guard() and self._confirm_discard()):
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open project", self._project_dir(),
                                              f"{APP_NAME} project (*{PROJECT_EXT} *{OLD_PROJECT_EXT});;All files (*)")
        if path:
            try:
                self._load_project(path)
                self.toast.popup(f"Opened {os.path.basename(path)}")
            except Exception as e:
                self.banners.post("proj", f"Could not open project: {e}", "bad")

    def _load_project(self, path: str):
        with self._waiting(), open(path, encoding="utf-8") as f:
            self._set_project(Project.from_dict(json.load(f)), path)
        QSettings().setValue("last_project", path)
        self._log(f"Project opened: {path}")

    def _project_dir(self) -> str:
        if self.project_path:
            return os.path.dirname(self.project_path)
        d = paths.projects_dir()
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def _save_project(self) -> bool:
        if not self.project_path:
            return self._save_project_as()
        try:
            with self._waiting(), open(self.project_path, "w", encoding="utf-8") as f:
                json.dump(self.project.to_dict(), f, indent=2, ensure_ascii=False)
        except OSError as e:
            self.banners.post("proj", f"Could not save project: {e}", "bad")
            return False
        QSettings().setValue("last_project", self.project_path)
        self.dirty = False
        self._update_title()
        self.p_file.setText(_brk(self.project_path))
        self._rebuild_project_tree()
        self.toast.popup("Project saved")
        return True

    def _save_project_as(self) -> bool:
        os.makedirs(self.project.logging.folder, exist_ok=True)
        start = os.path.join(self._project_dir(), dl.safe_name(self.project.name) + PROJECT_EXT)
        path, _ = QFileDialog.getSaveFileName(self, "Save project", start,
                                              f"{APP_NAME} project (*{PROJECT_EXT})")
        if not path:
            return False
        if not path.endswith(PROJECT_EXT):
            path += PROJECT_EXT
        self.project_path = path
        return self._save_project()

    # ================================================================ topics
    def _selected_topic(self) -> TopicCfg | None:
        it = self.tree.currentItem()
        if it is None:
            return None
        tid = it.data(0, Qt.ItemDataRole.UserRole)[1]
        return next((t for t in self.project.topics if t.id == tid), None)

    def _add_topic(self):
        if not self._recording_guard():
            return
        dlg = TopicDialog(self, None, {t.topic for t in self.project.topics})
        if dlg.exec():
            cfg = dlg.apply(None)
            self.project.topics.append(cfg)
            self._mark_dirty(); self._topics_changed()
            self._show_doc("channels")
            self.set_status(f"Topic '{cfg.name}' added", "ok")

    def _edit_topic(self):
        cfg = self._selected_topic()
        if cfg is None or not self._recording_guard():
            return
        dlg = TopicDialog(self, cfg, {t.topic for t in self.project.topics if t is not cfg})
        if dlg.exec():
            dlg.apply(cfg)
            self.streams.pop(cfg.id, None)
            self._mark_dirty(); self._topics_changed()

    def _delete_topic(self):
        cfg = self._selected_topic()
        if cfg is None or not self._recording_guard():
            return
        if QMessageBox.question(self, "Delete topic",
                                f"Delete topic '{cfg.name}' ({cfg.topic}) from the project?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.project.topics.remove(cfg)
        self.streams.pop(cfg.id, None)
        self._mark_dirty(); self._topics_changed()
        self.toast.popup(f"Deleted '{cfg.name}'")

    def _topics_changed(self):
        """Structure changed: subscriptions, routes, tree, plots."""
        self._routes.clear()
        self.mqtt.set_subscriptions([(t.topic, t.qos) for t in self.project.topics if t.enabled])
        self._rebuild_tree()
        self._rebuild_plots()
        self._alarms_reload()
        self._update_logging_help()
        self._update_lock()

    def _rebuild_tree(self):
        locked = self.recorder is not None
        self.tree.blockSignals(True)
        sel = self._selected_topic()
        self.tree.clear(); self.tree_items.clear()
        for t in self.project.topics:
            top = QTreeWidgetItem([t.name, t.topic] + [""] * (len(SPEC_COLS) - 2))
            top.setData(0, Qt.ItemDataRole.UserRole, ("topic", t.id))
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if not locked:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            top.setFlags(flags)
            top.setCheckState(0, Qt.CheckState.Checked if t.enabled else Qt.CheckState.Unchecked)
            f = top.font(0); f.setBold(True)
            for c in range(len(SPEC_COLS)):
                top.setFont(c, f)
            kids = {}
            for c in t.channels:
                it = QTreeWidgetItem([c.name, f"CH{c.index + 1}", c.unit] + [""] * (len(SPEC_COLS) - 3))
                if c.description:
                    it.setToolTip(0, c.description)
                it.setData(0, Qt.ItemDataRole.UserRole, ("ch", t.id, c.index))
                it.setFlags(flags)
                it.setCheckState(0, Qt.CheckState.Checked if c.enabled else Qt.CheckState.Unchecked)
                it.setIcon(0, swatch(ch_color(c)))
                for col in range(3, COL_DETAILS):
                    it.setTextAlignment(col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                top.addChild(it)
                kids[c.index] = it
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
            self.tree_items[t.id] = (top, kids)
            if sel is not None and sel.id == t.id:
                self.tree.setCurrentItem(top)
        self.tree.blockSignals(False)
        self._rebuild_scalers()
        self._update_tree_values()
        self._rebuild_project_tree()

    def _tree_double_clicked(self, item, col):
        kind = item.data(0, Qt.ItemDataRole.UserRole)[0]
        # the dialogs rebuild the tree: never inside the click itself
        QTimer.singleShot(0, self._edit_topic if kind == "topic" else self._edit_channel)

    def _tree_item_changed(self, item, col):
        if self.recorder is not None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        cfg = next((t for t in self.project.topics if t.id == data[1]), None)
        if cfg is None:
            return
        target = cfg if data[0] == "topic" else cfg.channel(data[2])
        if col == 0:
            on = item.checkState(0) == Qt.CheckState.Checked
            name = item.text(0).strip() or target.name
            changed_enable = on != target.enabled
            target.enabled, target.name = on, name
            if data[0] == "topic" and changed_enable:
                # Never rebuild the tree inside itemChanged: Qt is still inside this
                # item's setData(), and deleting it there segfaults. Defer it.
                self._mark_dirty()
                QTimer.singleShot(0, self._topics_changed)
                return
        elif col == 2 and data[0] == "ch":
            target.unit = item.text(2).strip()
        elif col == 1 and data[0] == "ch":
            self.tree.blockSignals(True); item.setText(1, f"CH{target.index + 1}")
            self.tree.blockSignals(False)
            return
        self._mark_dirty()
        self._rebuild_plots()
        self._update_logging_help()
        self._rebuild_project_tree()

    def _update_tree_values(self):
        for t in self.project.topics:
            entry = self.tree_items.get(t.id)
            if entry is None:
                continue
            top, kids = entry
            st = self.streams.get(t.id)
            if not t.enabled:
                details = "disabled"
            elif st is None or st.fs is None:
                details = f"QoS {t.qos} · waiting for data" if self.mqtt.is_connected \
                    else f"QoS {t.qos} · not connected"
            else:
                details = (f"QoS {t.qos} · {st.fs:g} S/s · {st.pps:.1f} pkt/s · "
                           f"block {st.last.n} · lost {st.lost}")
            self.tree.blockSignals(True)
            top.setText(COL_DETAILS, details)
            win = self.window_s.value()
            for c in t.channels:
                it = kids.get(c.index)
                if it is None:
                    continue
                d = int(c.decimals)
                sc = cm.describe_scale(c.scale)
                if st and c.index in st.last_vals:
                    val = st.last_vals[c.index]
                    lvl, typ = self.alarm_engine.channel_state(t.id, c.index)
                    mark = ("▲ " if typ in ar.UP_TYPES else "▼ " if typ in ar.DOWN_TYPES else "⚠ ") if lvl else ""
                    it.setText(3, mark + f"{val:.{d}f} {c.unit}".strip())
                    it.setForeground(3, QBrush(QColor(level_color(lvl))) if lvl else QBrush())
                    it.setToolTip(3, f"Alarm Level {lvl} ({typ}) active" if lvl else "")
                    for col, v in zip(range(4, 4 + len(STAT_COLS)), self._stats(st, c.index, win)):
                        it.setText(col, "-" if v is None else f"{v:.{d}f}")
                    it.setText(COL_DETAILS, "  ·  ".join(x for x in (
                        sc, f"{st.scales[c.index]:.3g} {c.unit or 'units'}/LSB") if x))
                else:
                    it.setText(3, "-")
                    for col in range(4, 4 + len(STAT_COLS)):
                        it.setText(col, "")
                    it.setText(COL_DETAILS, sc)
            self.tree.blockSignals(False)

    def _update_lock(self):
        rec = self.recorder is not None
        has_sel = self._selected_topic() is not None
        self.btn_add.setEnabled(not rec)
        self.btn_edit.setEnabled(not rec and has_sel)
        self.btn_del.setEnabled(not rec and has_sel)
        self.btn_edit_ch.setEnabled(not rec and self._selected_channel() is not None)
        for w in (self.log_folder, self.p_name, self.p_operator, self.p_desc, *self.lp_btns):
            w.setEnabled(not rec)
        self.act_rec.setEnabled(not rec)
        self.act_stop.setEnabled(rec)

    def _recording_guard(self) -> bool:
        if self.recorder is not None:
            self.toast.popup("Stop recording to change the project")
            return False
        return True

    def _browse_log_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Log folder", self.project.logging.folder)
        if d:
            self.log_folder.setText(d)
            self._edit_logging("folder", d)

    def _open_log_folder(self):
        d = self.recorder.dir if self.recorder else self.project.logging.folder
        os.makedirs(d, exist_ok=True)
        self._open_path(d)

    @staticmethod
    def _open_path(path):
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ================================================================ navigator (left dock)
    def _show_connection(self):
        self.conn_dlg.show(); self.conn_dlg.raise_(); self.conn_dlg.activateWindow()

    @staticmethod
    def _dot(token: str) -> QIcon:
        pm = QPixmap(20, 20); pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(T[token]))
        p.drawEllipse(5, 5, 10, 10); p.end()
        pm.setDevicePixelRatio(2)
        return QIcon(pm)

    def _topic_state(self, t: TopicCfg) -> str:
        if not t.enabled or not self.mqtt.is_connected:
            return "muted"
        st = self.streams.get(t.id)
        return "ok" if st and st.pps > 0 else "warn"

    def _rebuild_project_tree(self, *_):
        tree = getattr(self, "ptree", None)
        if tree is None:
            return
        expanded = {tid for tid, it in self.ptree_topics.items() if it.isExpanded()}
        first = not self.ptree_topics and tree.topLevelItemCount() == 0
        tree.clear(); self.ptree_topics.clear(); self._dot_cache.clear()
        muted = QBrush(QColor(T["muted"]))
        fname = os.path.basename(self.project_path) if self.project_path else f"{self.project.name}{PROJECT_EXT}"
        root = QTreeWidgetItem([fname + (" *" if self.dirty else "")])
        root.setIcon(0, make_icon("folder", T["text"]))
        root.setToolTip(0, self.project_path or "Not saved yet")
        root.setData(0, Qt.ItemDataRole.UserRole, ("project",))
        tree.addTopLevelItem(root)

        def node(parent, text, icon, data, tip=""):
            it = QTreeWidgetItem([text]); it.setIcon(0, icon)
            it.setData(0, Qt.ItemDataRole.UserRole, data)
            if tip:
                it.setToolTip(0, tip)
            parent.addChild(it)
            return it

        node(root, "Screen", make_icon("chart", T["text"]), ("doc", "screen"), "Live plots")
        chans = node(root, "Channel Specification", make_icon("share", T["text"]), ("doc", "channels"),
                     "Topics and channels")
        for t in self.project.topics:
            state = self._topic_state(t)
            ti = node(chans, t.name, self._dot(state), ("topic", t.id),
                      f"{t.topic}  ·  QoS {t.qos}" + ("" if t.enabled else "  ·  disabled"))
            self._dot_cache[t.id] = state
            if not t.enabled:
                ti.setForeground(0, muted)
            for c in t.channels:
                ci = node(ti, c.name + (f"  ({c.unit})" if c.unit else ""),
                          swatch(ch_color(c)), ("ch", t.id, c.index),
                          f"CH{c.index + 1}")
                if not (c.enabled and t.enabled):
                    ci.setForeground(0, muted)
            ti.setExpanded(t.id in expanded or first)
            self.ptree_topics[t.id] = ti
        logn = node(root, "Logging Specification", make_icon("file-text", T["text"]), ("doc", "logging"),
                    "Test properties, log folder and logging profiles")
        for pr in self.project.logging_profiles:
            stat = pr["type"] == "statistic"
            li = node(logn, pr["name"], make_icon("sigma" if stat else "activity", T["muted"]), ("lprof", pr["id"]),
                      (f"Statistic · every {fmt_interval(pr['interval_s'])}" if stat else "Signal")
                      + f" · {pr['format']}" + ("" if pr["enabled"] else " · disabled"))
            if not pr["enabled"]:
                li.setForeground(0, muted)
        logn.setExpanded(True)
        self.nav_alarms = node(root, "Alarms", make_icon("bell", T["text"]), ("alarms",),
                               "4-level alarms, active list, alarm log - right-click to add")
        self.ptree_alarms = {}
        for a in self.project.alarms:
            ai = node(self.nav_alarms, a["name"], level_icon(0, 5), ("alarm", a["id"]),
                      ", ".join(x for x in (ar.describe_level(lv) for lv in a["levels"]) if x)
                      + ("" if a["enabled"] else "  ·  disabled"))
            if not a["enabled"]:
                ai.setForeground(0, muted)
            self.ptree_alarms[a["id"]] = ai
        profs = node(self.nav_alarms, "Notification profiles", make_icon("send", T["muted"]), ("aprofs",),
                     "Where alarm events are sent: log, popup, REST API, MQTT")
        for pf in self.project.alarm_profiles:
            pi = node(profs, pf["name"], make_icon("send", T["muted"]), ("aprof", pf["id"]),
                      f"{ar.PROFILE_KINDS.get(pf['kind'])} · {ap.target_text(pf)}"
                      + ("" if pf["enabled"] else "  ·  disabled"))
            if not pf["enabled"]:
                pi.setForeground(0, muted)
        self.nav_alarms.setExpanded(True)
        views = node(root, "Views", make_icon("layout", T["text"]), ("views",),
                     "Custom screens - right-click for New view")
        for vw in self.project.views:
            node(views, vw["name"], make_icon("layout", T["muted"]), ("view", vw["id"]))
        views.setExpanded(True)
        self.nav_conn = node(root, "Connection", self._dot("muted"), ("conn",), "Broker settings")
        root.setExpanded(True); chans.setExpanded(True)
        self._update_nav_dots(force=True)
        self._apply_nav_filter(self.nav_filter.text())

    def _update_nav_dots(self, force: bool = False):
        eng = self.alarm_engine
        for aid, it in getattr(self, "ptree_alarms", {}).items():
            lvl = eng.alarm_state(aid)
            a = eng.by_id.get(aid)
            key = f"__a{aid}"
            state = lvl if lvl else (-1 if a is None or not a["enabled"] else 0)
            if force or self._dot_cache.get(key) != state:
                self._dot_cache[key] = state
                it.setIcon(0, level_icon(lvl, 5) if lvl else self._dot("ok" if state == 0 else "muted"))
        if getattr(self, "nav_alarms", None) is not None:
            act, unack = eng.counts()
            text = "Alarms" + (f"  ·  {act} active" if act else "") + (f"  ·  {unack} unack" if unack else "")
            if self.nav_alarms.text(0) != text:
                self.nav_alarms.setText(0, text)
                self.nav_alarms.setForeground(0, QBrush(QColor(T["bad"])) if act or unack else QBrush())
        for t in self.project.topics:
            it = self.ptree_topics.get(t.id)
            state = self._topic_state(t)
            if it is not None and (force or self._dot_cache.get(t.id) != state):
                it.setIcon(0, self._dot(state)); self._dot_cache[t.id] = state
        if getattr(self, "nav_conn", None) is not None:
            want = self.mqtt_panel.btn.text() == "Disconnect"
            state = "ok" if self.mqtt.is_connected else ("warn" if want else "muted")
            text = (f"Connection · {self.mqtt_panel.host.currentText()}"
                    if self.mqtt.is_connected else "Connection · offline")
            if force or self._dot_cache.get("__conn") != state or self.nav_conn.text(0) != text:
                self.nav_conn.setIcon(0, self._dot(state)); self.nav_conn.setText(0, text)
                self._dot_cache["__conn"] = state

    def _apply_nav_filter(self, text: str):
        text = text.strip().lower()

        def walk(it) -> bool:
            kids = [walk(it.child(i)) for i in range(it.childCount())]
            show = not text or text in it.text(0).lower() or any(kids)
            it.setHidden(not show)
            if text and any(kids):
                it.setExpanded(True)
            return show
        for i in range(self.ptree.topLevelItemCount()):
            walk(self.ptree.topLevelItem(i))

    def _collapse_topics(self):
        for it in self.ptree_topics.values():
            it.setExpanded(False)

    def _nav_data(self, it):
        return it.data(0, Qt.ItemDataRole.UserRole) if it is not None else None

    def _select_in_spec(self, tid: str, idx: int | None = None):
        entry = self.tree_items.get(tid)
        if entry is None:
            return
        it = entry[0] if idx is None else entry[1].get(idx, entry[0])
        self.tree.setCurrentItem(it)
        self.tree.scrollToItem(it)

    def _nav_open(self, it):
        d = self._nav_data(it)
        if not d:
            return
        if d[0] == "doc":
            self._show_doc(d[1])
        elif d[0] == "project":
            self._show_doc("logging")
        elif d[0] == "view":
            self._show_doc(f"view:{d[1]}")
        elif d[0] in ("topic", "ch"):
            self._show_doc("channels")
            self._select_in_spec(d[1], d[2] if d[0] == "ch" else None)
        elif d[0] == "conn":
            self._show_connection()
        elif d[0] == "lprof":
            self._show_doc("logging")
            self._select_log_profile(d[1])
        elif d[0] == "alarms":
            self._show_doc("alarms")
            self.alarm_page.tabs.setCurrentIndex(0)
        elif d[0] == "alarm":
            self._show_doc("alarms")
            self.alarm_page.select_alarm(d[1])
        elif d[0] in ("aprofs", "aprof"):
            self._show_doc("alarms")
            self.alarm_page.tabs.setCurrentIndex(3)
            if d[0] == "aprof":
                self.alarm_page.select_profile(d[1])

    def _nav_double_clicked(self, it, _col):
        d = self._nav_data(it)
        if d and d[0] == "topic":
            self._select_in_spec(d[1])
            QTimer.singleShot(0, self._edit_topic)       # rebuilds trees: not inside the click
        elif d and d[0] == "ch":
            self._select_in_spec(d[1], d[2])
            QTimer.singleShot(0, self._edit_channel)
        elif d and d[0] == "lprof":
            QTimer.singleShot(0, lambda: self._edit_log_profile(d[1]))
        elif d and d[0] == "alarm":
            QTimer.singleShot(0, lambda: self.alarm_page.edit_alarm(d[1]))
        elif d and d[0] == "aprof":
            QTimer.singleShot(0, lambda: self.alarm_page.edit_profile(d[1]))

    def _nav_topic(self) -> TopicCfg | None:
        d = self._nav_data(self.ptree.currentItem())
        if not d or d[0] not in ("topic", "ch"):
            return None
        self._select_in_spec(d[1])
        return self._selected_topic()

    def _nav_edit(self):
        if self._nav_topic():
            QTimer.singleShot(0, self._edit_topic)

    def _nav_delete(self):
        if self._nav_topic():
            QTimer.singleShot(0, self._delete_topic)

    def _toggle_enabled(self, tid: str, idx: int | None):
        if not self._recording_guard():
            return
        t = next((x for x in self.project.topics if x.id == tid), None)
        if t is None:
            return
        target = t if idx is None else t.channel(idx)
        target.enabled = not target.enabled
        self._mark_dirty()
        QTimer.singleShot(0, self._topics_changed)

    def _nav_menu(self, pos):
        it = self.ptree.itemAt(pos)
        d = self._nav_data(it) or ("none",)
        m = QMenu(self)
        if d[0] == "topic":
            t = next(x for x in self.project.topics if x.id == d[1])
            self.ptree.setCurrentItem(it)
            m.addAction("Edit topic...", self._nav_edit)
            m.addAction("Disable" if t.enabled else "Enable", lambda: self._toggle_enabled(t.id, None))
            m.addSeparator()
            m.addAction("Delete topic", self._nav_delete)
        elif d[0] == "ch":
            t = next(x for x in self.project.topics if x.id == d[1])
            c = t.channel(d[2])
            m.addAction("Edit channel...", lambda: self._nav_double_clicked(it, 0))
            m.addAction("Disable channel" if c.enabled else "Enable channel",
                        lambda: self._toggle_enabled(t.id, c.index))
        elif d[0] == "conn":
            m.addAction(self.act_connect)
            m.addAction("Connection settings...", self._show_connection)
        elif d[0] in ("views", "view"):
            if d[0] == "view":
                page = self.view_pages.get(d[1])
                m.addAction("Open", lambda: self._show_doc(f"view:{d[1]}"))
                m.addAction("Rename...", lambda: self.rename_view(page))
                m.addAction("Delete view", lambda: self.delete_view(page))
                m.addSeparator()
            m.addAction("New view...", self._new_view)
        elif d[0] == "lprof" or tuple(d[:2]) == ("doc", "logging"):
            self._log_profile_menu(m, self._log_profile(d[1]) if d[0] == "lprof" else None)
        elif d[0] in ("alarms", "alarm", "aprofs", "aprof"):
            pg_ = self.alarm_page
            if d[0] == "alarm":
                a = next((x for x in self.project.alarms if x["id"] == d[1]), None)
                m.addAction("Edit alarm...", lambda: QTimer.singleShot(0, lambda: pg_.edit_alarm(d[1])))
                m.addAction("Disable" if a and a["enabled"] else "Enable", lambda: pg_.toggle_alarm(d[1]))
                m.addAction("Delete alarm", lambda: (pg_.select_alarm(d[1]), pg_.delete_alarm()))
                m.addSeparator()
            elif d[0] == "aprof":
                pf = next((x for x in self.project.alarm_profiles if x["id"] == d[1]), None)
                m.addAction("Edit profile...", lambda: QTimer.singleShot(0, lambda: pg_.edit_profile(d[1])))
                m.addAction("Disable" if pf and pf["enabled"] else "Enable", lambda: pg_.toggle_profile(d[1]))
                m.addAction("Send test", lambda: pf and self.test_profile(pf))
                m.addAction("Delete profile", lambda: (pg_.select_profile(d[1]), pg_.delete_profile()))
                m.addSeparator()
            if d[0] in ("aprofs", "aprof"):
                add = m.addMenu("Add notification profile")
                for k, text in ar.PROFILE_KINDS.items():
                    add.addAction(text, lambda kk=k: (self._show_doc("alarms"), pg_.add_profile(kk)))
            else:
                m.addAction("Add alarm...", lambda: self._alarm_action("add"))
                m.addAction("Acknowledge all", self._ack_all).setEnabled(self.alarm_engine.counts()[1] > 0)
        else:
            m.addAction("Add topic...", self._add_topic)
        if d[0] == "ch":
            m.addAction("Add alarm for this channel...", lambda: self._alarm_action("add", (d[1], d[2])))
        free = ("conn", "views", "view", "alarms", "alarm", "aprofs", "aprof")
        for a in m.actions():
            a.setEnabled(a.isEnabled() and (self.recorder is None or d[0] in free
                                            or a.text().startswith("Add alarm")))
        m.exec(self.ptree.viewport().mapToGlobal(pos))

    # ---------------------------------------------------------------- sessions (Data tab)
    def _refresh_sessions(self):
        tree = getattr(self, "stree", None)
        if tree is None:
            return
        tree.clear()
        folder = self.project.logging.folder
        rows = []
        try:
            entries = [e for e in os.scandir(folder) if e.is_dir()]
        except OSError:
            entries = []
        for e in entries:
            pj = os.path.join(e.path, "project.json")
            if not os.path.isfile(pj):
                continue
            try:
                with open(pj, encoding="utf-8") as f:
                    info = json.load(f)
            except (OSError, ValueError):
                continue
            rows.append((info.get("start_unix", 0), e, info))
        for start, e, info in sorted(rows, key=lambda r: r[0], reverse=True):
            running = info.get("running")
            dur = "recording..." if running else time.strftime("%H:%M:%S", time.gmtime(info.get("duration_s", 0)))
            it = QTreeWidgetItem([info.get("project", {}).get("name", e.name),
                                  time.strftime("%d %b %H:%M", time.localtime(start)) if start else "-",
                                  dur])
            it.setData(0, Qt.ItemDataRole.UserRole, e.path)
            tip = f"{e.path}\nFormat: {info.get('format', '-')}" + (" + raw .wpk" if info.get("keep_raw") else "")
            for c in range(3):
                it.setToolTip(c, tip)
            it.setIcon(0, make_icon("database", T["muted"]))
            if running:
                it.setIcon(0, self._dot("bad"))
            tree.addTopLevelItem(it)
        if not rows:
            it = QTreeWidgetItem(["No recordings yet", "", ""])
            it.setForeground(0, QBrush(QColor(T["muted"])))
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setToolTip(0, folder)
            tree.addTopLevelItem(it)

    def _session_menu(self, pos):
        it = self.stree.itemAt(pos)
        path = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        if not path:
            return
        m = QMenu(self)
        m.addAction("Open folder", lambda: self._open_path(path))
        m.addAction("Copy path", lambda: QApplication.clipboard().setText(path))
        m.exec(self.stree.viewport().mapToGlobal(pos))

    # ================================================================ recording
    def _start_recording(self):
        if self.recorder is not None:
            return
        profiles = []
        for pr in self.project.logging_profiles:
            tps = self._profile_topics(pr) if pr["enabled"] else []
            if tps:
                profiles.append({**copy.deepcopy(pr), "topics": tps})
        if not profiles:
            self.banners.post("rec", "Nothing to record: enable a logging profile and at least one "
                                     "topic with an enabled channel.", "warn",
                              "Open", lambda: self._show_doc("logging"))
            return
        for pr in profiles:
            if pr["format"] != "TDMS":
                continue
            for tp in pr["topics"]:
                ch = [c[1] for c in tp["channels"]]
                if len(set(ch)) != len(ch) or "Time" in ch or "Samples" in ch:
                    self.banners.post("rec", f"Profile '{pr['name']}', topic '{tp['name']}': channel names "
                                             "must be unique and not 'Time' / 'Samples' for TDMS.", "warn")
                    return
        lg = self.project.logging
        try:
            os.makedirs(lg.folder, exist_ok=True)
            with self._waiting():
                rec = dl.Recorder(lg.folder, self.project.to_dict(), profiles=profiles)
                rec.start()
        except Exception as e:
            self.banners.post("rec", f"Could not start recording: {e}", "bad")
            return
        self.recorder = rec
        self.banners.dismiss("rec")
        self._rebuild_tree(); self._update_lock(); self._fill_log_profiles()
        self._refresh_sessions()
        self._log(f"Recording started -> {rec.dir}  ({len(profiles)} profile(s): " +
                  ", ".join(f"{p['name']} [{p['type']}, {p['format']}]" for p in profiles) + ")")
        self.set_status(f"Recording to {rec.dir}", "ok")
        if not self.mqtt.is_connected:
            self.banners.post("rec", "Recording, but not connected to the broker - "
                                     "nothing is being received.", "warn",
                              "Connect", self._connect_from_empty)
        self._update_stats()

    def _stop_recording(self):
        rec, self.recorder = self.recorder, None
        if rec is None:
            return
        self.busy(True)
        with self._waiting():
            rec.stop()
        self.busy(False)
        self._rebuild_tree(); self._update_lock(); self._fill_log_profiles()
        dur = time.strftime("%H:%M:%S", time.gmtime(rec.elapsed))
        msg = (f"Saved {rec.packets} packets, {rec.samples:,} samples/channel in {dur} "
               f"({_fmt_bytes(rec.disk_bytes())})")
        self._log(f"Recording stopped. {msg} -> {rec.dir}")
        self.last_session.setText(f"Last recording: {_brk(rec.dir)}")
        self._refresh_sessions()
        self.banners.dismiss("rec")
        if rec.error or rec.dropped:
            self.banners.post("rec", f"Recording finished with problems: "
                                     f"{rec.dropped} blocks dropped. {rec.error or ''}", "bad")
        else:
            self.toast.popup("Recording saved")
        self.set_status(msg, "ok")
        self._update_stats()

    # ================================================================ MQTT
    def _connect_from_empty(self):
        if self.mqtt_panel.btn.text() != "Disconnect":
            self.mqtt_panel.btn.click()

    def _on_connected(self):
        self.banners.dismiss("conn")
        self.toast.popup(f"Connected to {self.mqtt_panel.host.currentText()}")
        self._sync_conn()

    def _on_mqtt_status(self, s: str):
        self._log(s)
        low = s.lower()
        if s.startswith(("Connected", "Subscribed")):
            self.set_status(s, "ok")
        elif any(k in low for k in ("refused", "cannot", "no reply", "error", "failed")):
            self.set_status(s, "bad")
            if not low.startswith("connect attempt"):
                self.banners.post("conn", s, "bad", "Connection settings", self._show_connection)
        else:
            self.set_status(s)
        self._sync_conn()

    def _sync_conn(self, *_):
        want = self.mqtt_panel.btn.text() == "Disconnect"
        if self.act_connect.text() != ("Disconnect" if want else "Connect"):
            self.act_connect.setText("Disconnect" if want else "Connect")
            self._set_tip(self.act_connect)
        busy = want and not self.mqtt.is_connected
        if busy != self._busy_on:
            self._busy_on = busy
            self.busy(busy)
            if busy:                                    # arrow + spinner while connecting
                QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
            else:
                QApplication.restoreOverrideCursor()
        if not want:
            self.banners.dismiss("conn")

    def _on_payload_net_thread(self, topic: str, payload: bytes):
        """paho thread: timestamp and queue only."""
        try:
            self.q.put_nowait((time.time(), topic, bytes(payload)))
        except queue.Full:
            self.overflow += 1

    def _route(self, topic: str) -> list[TopicCfg]:
        ids = self._routes.get(topic)
        if ids is None:
            ids = self._routes[topic] = [
                t.id for t in self.project.topics
                if t.enabled and (t.topic == topic or mqtt.topic_matches_sub(t.topic, topic))]
        return [t for t in self.project.topics if t.id in ids]

    # ================================================================ processing
    def _drain_and_draw(self):
        n, new_ch = 0, False
        while n < 3000:
            try:
                rx, topic, buf = self.q.get_nowait()
            except queue.Empty:
                break
            n += 1
            cfgs = self._route(topic)
            if not cfgs:
                continue
            try:
                blk = wp.decode(buf)
            except wp.PacketError as e:
                for cfg in cfgs:
                    st = self.streams.setdefault(cfg.id, TopicStream())
                    if "CRC" in str(e):
                        st.crc += 1
                    else:
                        st.bad += 1
                continue
            if self.t_ref is None:
                self.t_ref = blk.t0
            for cfg in cfgs:
                st = self.streams.setdefault(cfg.id, TopicStream())
                for row, ch in enumerate(blk.channels):
                    st.raw_mean[ch] = float(blk.y[row].mean())
                sblk = self._scaled(cfg, blk)            # engineering values (plots, files)
                st.feed(sblk, self.t_ref)
                for row, ch in enumerate(sblk.channels):  # alarms see every sample of the block
                    self.alarm_engine.feed(cfg.id, ch, rx, sblk.y[row])
                for ch in blk.channels:                  # auto-add unseen channels
                    if cfg.channel(ch) is None:
                        cfg.channels.append(ChannelCfg(ch, f"CH{ch + 1}"))
                        cfg.channels.sort(key=lambda c: c.index)
                        new_ch = True
                if self.recorder is not None:
                    self.recorder.put(cfg.id, rx, buf, sblk)
        if new_ch:
            self._mark_dirty()
            if self.recorder is None:
                self._rebuild_tree(); self._rebuild_plots()
        self._draw()

    def _draw(self, force: bool = False):
        enabled = [t for t in self.project.topics if t.enabled]
        has_data = any(self.streams.get(t.id) and self.streams[t.id].rings for t in enabled)
        want = 0 if not enabled else (2 if has_data else 1)
        if self.screen_stack.currentIndex() != want:
            self.screen_stack.setCurrentIndex(want)
        if not has_data or (self.act_pause.isChecked() and not force):
            return
        win = self.window_s.value()
        t_end = max((r.ordered()[0][-1] for t in enabled if t.id in self.streams
                     for r in self.streams[t.id].rings.values() if r.count), default=0)
        page = self.doc_tabs.currentWidget()
        if isinstance(page, dv.ViewPage):           # only the visible document is redrawn
            page.draw(t_end, win)
            return
        if page is not self.docs["screen"][0]:
            return
        for (tid, idx), curve in self.curves.items():
            st = self.streams.get(tid)
            ring = st.rings.get(idx) if st else None
            if ring is None or ring.count == 0:
                curve.setData([], []); continue
            t, y = ring.ordered()
            i = np.searchsorted(t, t_end - win, side="left")
            curve.setData(t[i:], y[i:], connect="finite")
        if not self._zoomed:
            for p in self.plots:
                p.setXRange(t_end - win, t_end, padding=0)

    def _on_manual_range(self, *_):
        """User zoomed / panned a Screen plot: hold the range until Reset zoom."""
        if not self._zoomed:
            self._zoomed = True
            self.zoom_btn.setVisible(True)
            self.set_status("Zoomed - plots hold this range. Double-click or Reset zoom for live.")

    def _reset_zoom(self):
        self._zoomed = False
        self.zoom_btn.setVisible(False)
        self.set_status("Live view", "ok")
        self._apply_autoscale()
        self._draw(force=True)

    def _clear_view(self):
        for st in self.streams.values():
            st.rings.clear()
        self.t_ref = None
        for c in self.curves.values():
            c.setData([], [])
        self.toast.popup("View cleared")

    # ================================================================ plots
    def _style_plot(self, p: pg.PlotItem):
        dv.style_plot(p)                             # black data area + box zoom
        p.getViewBox().sigRangeChangedManually.connect(self._on_manual_range)

    def _rebuild_plots(self, *_):
        self.glw.setBackground(T["panel"])
        self.glw.clear(); self.plots.clear(); self.curves.clear()
        self._fixed_y = {}
        per_topic = self.layout_mode.currentText() == "Per topic"
        groups = []          # [(title, [(topic, channel)])]
        for t in self.project.topics:
            chans = [c for c in t.channels if c.enabled] if t.enabled else []
            if not chans:
                continue
            if per_topic:
                groups.append((t.name, [(t, c) for c in chans]))
            else:
                groups += [(c.name, [(t, c)]) for c in chans]
        if not per_topic:   # prefix the topic only where channel names clash
            names = [g[0] for g in groups]
            groups = [((f"{m[0][0].name}·{n}" if names.count(n) > 1 else n), m)
                      for n, m in groups]
        first = None
        cols = 2 if self._grid and len(groups) > 1 else 1
        for i, (title, members) in enumerate(groups):
            row = i // cols
            p = self.glw.addPlot(row=row, col=i % cols)
            self._style_plot(p)
            if per_topic:
                p.setTitle(title, color=T["text"], size="10pt")
                dv.add_legend(p)
                units = {c.unit for _, c in members if c.unit}
                if len(units) == 1:
                    p.setLabel("left", units.pop(), color=T["muted"])
            else:
                t, c = members[0]
                p.setLabel("left", f"{title}" + (f" ({c.unit})" if c.unit else ""),
                           color=ch_color(c))
            if first is None:
                first = p
            else:
                p.setXLink(first)
            if i >= len(groups) - cols:                 # x axis only on the last row
                p.setLabel("bottom", "Time since first sample", "s", color=T["text"])
            else:
                p.hideAxis("bottom")
            fixed = [(c.y_min, c.y_max) for _, c in members if c.y_min is not None]
            if len(fixed) == len(members):               # every channel fixed -> union range
                self._fixed_y[id(p)] = (min(a for a, _ in fixed), max(b for _, b in fixed))
            for t, c in members:
                label_txt = c.name + (f" [{c.unit}]" if c.unit else "")
                self.curves[(t.id, c.index)] = p.plot(
                    pen=pg.mkPen(ch_color(c), width=1.5), name=label_txt)
            self.plots.append(p)
        self._zoomed = False
        self.zoom_btn.setVisible(False)
        self._apply_autoscale()
        for page in self.view_pages.values():          # names / colours / theme changed
            page.restyle()
        self._draw(force=True)

    def _apply_autoscale(self, *_):
        for p in self.plots:
            fy = getattr(self, "_fixed_y", {}).get(id(p))
            if fy:
                p.setYRange(*fy, padding=0.02)
            else:
                p.enableAutoRange(axis="y", enable=self.autoscale.isChecked())

    # ================================================================ stats
    def _update_stats(self):
        now = time.perf_counter()
        dt = now - getattr(self, "_stat_t", now - 0.5) or 0.5
        self._stat_t = now
        enabled = [t for t in self.project.topics if t.enabled]
        rx = lost = errs = 0
        self.topic_table.setRowCount(len(enabled))
        for row, t in enumerate(enabled):
            st = self.streams.get(t.id)
            if st:
                st.pps, st.win_pkts = st.win_pkts / dt, 0
                rx += st.pkts; lost += st.lost; errs += st.crc + st.bad
            vals = [t.name, f"{st.pps:.1f}" if st else "-",
                    f"{st.fs:g} S/s" if st and st.fs else "-",
                    str(st.lost) if st else "-", f"{st.crc}/{st.bad}" if st else "-"]
            for col, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if col:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.topic_table.setItem(row, col, it)

        rec = self.recorder
        c = self.cards
        if rec:
            c["elapsed"].set_value(time.strftime("%H:%M:%S", time.gmtime(rec.elapsed)))
            c["size"].set_value(_fmt_bytes(rec.disk_bytes()))
            c["samples"].set_value(f"{rec.samples:,}")
            c["packets"].set_value(f"{rec.packets:,}")
            self.rec_label.setText(f"● REC  {time.strftime('%H:%M:%S', time.gmtime(rec.elapsed))}")
            set_prop(self.rec_label, "level", "rec")
            self.chip_rec.set(f"Recording ×{len(rec.profiles)}" if len(rec.profiles) > 1 else "Recording", "bad")
            self.chip_rec.setToolTip("Logging profiles: " + ", ".join(p["name"] for p in rec.profiles))
            if rec.error:
                self.banners.post("rec", f"Write error: {rec.error}", "bad")
        else:
            if self.rec_label.text() != "Ready":
                self.rec_label.setText("Ready")
                set_prop(self.rec_label, "level", "")
            self.chip_rec.set("Not recording", "info")
        c["rx"].set_value(f"{rx:,}")
        c["lost"].set_value(f"{lost} / {sum(s.crc for s in self.streams.values())} / "
                            f"{sum(s.bad for s in self.streams.values())}")
        if not self.mqtt.is_connected:
            self.chip_conn.set("Disconnected", "info")
        elif any(self.streams.get(t.id) and self.streams[t.id].pps > 0 for t in enabled):
            self.chip_conn.set("Live", "ok")
        else:
            self.chip_conn.set("Waiting for data", "warn")
        narrow = self.topic_table.width() < 380          # laptop: keep the columns that matter
        for col in (2, 4):
            self.topic_table.setColumnHidden(col, narrow)
        self.lbl_topics.setText(f"{len(enabled)}/{len(self.project.topics)} topics enabled")
        self.lbl_rx.setText(f"{rx:,} packets" + (f" · {self.overflow} queue drops" if self.overflow else ""))
        self.alarm_engine.tick(time.time())
        self._drain_notifier()
        self._update_alarm_indicator()
        self.alarm_page.update_live()
        self._update_tree_values()
        self._update_nav_dots()
        page = self.doc_tabs.currentWidget()
        if isinstance(page, dv.ViewPage):
            page.update_values()
        self._sync_conn()

    def _log(self, s: str):
        self.log.appendPlainText(time.strftime("%H:%M:%S  ") + s)

    # ================================================================ channels: edit, scaling, stats, alarms
    def _selected_channel(self):
        it = self.tree.currentItem() if hasattr(self, "tree") else None
        d = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        if not d or d[0] != "ch":
            return None
        t = next((x for x in self.project.topics if x.id == d[1]), None)
        return (t, t.channel(d[2])) if t else None

    def _edit_channel(self):
        sel = self._selected_channel()
        if sel is None or not self._recording_guard():
            return
        t, c = sel
        st = self.streams.get(t.id)
        dlg = ChannelDialog(self, t.name, c, COLORS[c.index % len(COLORS)],
                               lambda: st.raw_mean.get(c.index) if st else None, t.topic, alarms=False)
        if dlg.exec():
            dlg.apply(c)
            self._mark_dirty()
            QTimer.singleShot(0, self._topics_changed)     # tree, plots, views, scalers
            self.set_status(f"Channel '{c.name}' updated", "ok")

    def _rebuild_scalers(self):
        self._scalers = {}
        for t in self.project.topics:
            for c in t.channels:
                try:
                    f = cm.make_scaler(c.scale)
                except ValueError as e:
                    self._log(f"{t.name} · {c.name}: scaling ignored - {e}")
                    f = None
                if f is not None:
                    self._scalers[(t.id, c.index)] = f

    def _scaled(self, cfg, blk):
        rows = [(r, self._scalers[(cfg.id, ch)]) for r, ch in enumerate(blk.channels)
                if (cfg.id, ch) in self._scalers]
        if not rows:
            return blk
        out = copy.copy(blk)
        out.y = blk.y.copy()
        for r, f in rows:
            try:
                out.y[r] = f(out.y[r])
            except Exception as e:                       # never let a formula stop acquisition
                out.y[r] = np.nan
                self._log(f"{cfg.name} CH{blk.channels[r] + 1}: formula error - {e}")
        return out

    def _stats(self, st, idx: int, win: float):
        ring = st.rings.get(idx)
        if ring is None or ring.count == 0:
            return [None] * len(STAT_COLS)
        t, y = ring.ordered()
        y = y[np.searchsorted(t, t[-1] - win, side="left"):]
        y = y[np.isfinite(y)]
        if y.size == 0:
            return [None] * len(STAT_COLS)
        mn, mx = float(y.min()), float(y.max())
        return [float(y.mean()), mn, mx, float(y.std()), float(np.sqrt(np.mean(y * y))), mx - mn]

    # ================================================================ alarms (daq_alarms.py)
    def _alarm_describe(self, tid: str, idx: int):
        t = next((x for x in self.project.topics if x.id == tid), None)
        c = t.channel(idx) if t else None
        return (f"{t.name} · {c.name}" if c else "(deleted channel)"), (c.unit if c else "")

    def _alarms_reload(self):
        """Definitions / profiles / settings changed (or the project): engine, page, tree."""
        eng = self.alarm_engine
        eng.latch = bool(self.project.alarm_settings.get("latch", True))
        eng.set_alarms(self.project.alarms)
        if hasattr(self, "alarm_page"):
            self.alarm_page.refresh()
        self._update_alarm_indicator()

    def alarms_changed(self):
        self._mark_dirty()
        self._alarms_reload()
        self._rebuild_project_tree()

    def alarm_settings_changed(self):
        self.alarm_engine.latch = bool(self.project.alarm_settings.get("latch", True))
        if not self.alarm_engine.latch:          # cleared + unacknowledged rows go away
            for st in self.alarm_engine.states.values():
                if not st.active:
                    st.acked = True
        self._mark_dirty()
        self.alarm_page.update_live(force=True)

    def _alarm_action(self, what: str, channel=None):
        self._show_doc("alarms")
        if what == "add":
            QTimer.singleShot(0, lambda: self.alarm_page.add_alarm(channel))

    def _ack_all(self):
        n = self.alarm_engine.ack()
        if n:
            self.set_status(f"{n} alarm(s) acknowledged", "ok")
        self.alarm_page.update_live(force=True)
        self._update_alarm_indicator()

    def _on_alarm_event(self, ev: dict):
        unit = "s" if ev["type"] == "D" else ev["unit"]
        lvl, name = f"L{ev['level']}", f"{ev['name']} ({ev['tag']})"
        s = self.project.alarm_settings
        if ev["event"] == "ON":
            cmp = "<" if ev["type"] in ar.DOWN_TYPES else ">"
            self._log(f"ALARM ON   {lvl} {ev['type']}: {name} = {ap.fmt_val(ev['value'], unit)}  "
                      f"({cmp} {ev['setpoint']:g})")
            if s.get("sound", True):
                QApplication.beep()
            if s.get("front"):
                self.showNormal() if self.isMinimized() else None
                self.raise_(); self.activateWindow()
        elif ev["event"] == "OFF":
            self._log(f"Alarm OFF  {lvl} {ev['type']}: {name} = {ap.fmt_val(ev['value'], unit)}")
        else:
            self._log(f"Alarm ACK  {lvl} {ev['type']}: {name}")
        a = self.alarm_engine.by_id.get(ev["alarm_id"])
        payload = None
        for p in self.project.alarm_profiles:
            if not (a and p["id"] in a.get("profiles", []) and ar.profile_wants(p, ev)):
                continue
            if p["kind"] == "popup":
                self.toast.popup(f"{'⚠ ' if ev['event'] == 'ON' else ''}{ev['event']} {lvl} · {ev['name']}"
                                 + (f" = {ap.fmt_val(ev['value'], unit)}" if ev["event"] != "ACK" else ""))
                if ev["event"] == "ON" and not s.get("sound", True):
                    QApplication.beep()
                self.profile_results[p["id"]] = (True, f"{ev['event']} shown", time.time())
            elif p["kind"] == "log":
                self.profile_results[p["id"]] = (True, f"{ev['event']} logged", time.time())
            else:
                payload = payload or ar.event_payload(ev, self.project.name)
                self.notifier.send(p, payload)
        self.alarm_page.add_event(ev)
        self._update_alarm_indicator()

    def test_profile(self, p: dict):
        ev = {"t": time.time(), "alarm_id": "test", "name": "Test alarm", "tag": f"{APP_NAME} · test",
              "unit": "", "level": (p.get("levels") or [1])[0], "type": "H", "event": "ON",
              "value": 1.0, "setpoint": 0.0}
        self._log(f"Notification test -> {p['name']} ({ar.PROFILE_KINDS.get(p['kind'])})")
        if p["kind"] in ("log", "popup"):
            if p["kind"] == "popup":
                self.toast.popup("Test alarm · Level 1"); QApplication.beep()
            self.profile_results[p["id"]] = (True, "test OK", time.time())
            self.alarm_page.refresh()
        else:
            self.notifier.send(p, {**ar.event_payload(ev, self.project.name), "test": True})
            self.set_status(f"Test sent to '{p['name']}' - result appears on the Notifications tab")

    def _drain_notifier(self):
        changed = False
        while True:
            try:
                pid, ok, text = self.notifier.results.get_nowait()
            except queue.Empty:
                break
            self.profile_results[pid] = (ok, text, time.time())
            name = next((p["name"] for p in self.project.alarm_profiles if p["id"] == pid), pid)
            self._log(f"Notification '{name}': {'OK' if ok else 'FAILED'} - {text}")
            changed = True
        if changed:
            self.alarm_page.refresh()

    def _update_alarm_indicator(self):
        if not hasattr(self, "alarm_btn"):
            return
        act, unack = self.alarm_engine.counts()
        level = "bad" if act else ("warn" if unack else "")
        state = (act, unack)
        if state == self._alarm_level:
            return
        self._alarm_level = state
        if act or unack:
            text = f"{act} active" + (f" · {unack} unack" if unack else "")
        else:
            text = "No alarms"
        self.alarm_btn.setText(text)
        self.alarm_btn.setIcon(make_icon("bell", T[level or "muted"]))
        set_prop(self.alarm_btn, "level", level)
        self.act_alarms.setText(f"Alarms ({act})" if act else "Alarms")
        self.act_alarms.setIcon(make_icon("bell", T[level or "text"]))
        self._set_tip(self.act_alarms)
        self.act_ack.setEnabled(unack > 0)

    def _columns_menu(self) -> QMenu:
        m = QMenu(self)
        for col, name in enumerate(SPEC_COLS):
            a = m.addAction(name); a.setCheckable(True)
            a.setChecked(not self.tree.isColumnHidden(col))
            a.setEnabled(name not in LOCKED_COLS)
            a.toggled.connect(lambda on, c=col: self._set_column(c, on))
        m.aboutToShow.connect(lambda: [a.setChecked(not self.tree.isColumnHidden(i))
                                       for i, a in enumerate(m.actions())])
        return m

    def _set_column(self, col: int, visible: bool):
        self.tree.setColumnHidden(col, not visible)
        QSettings().setValue("spec_hidden_cols",
                             [n for i, n in enumerate(SPEC_COLS) if self.tree.isColumnHidden(i)])

    def _spec_menu(self, pos):
        it = self.tree.itemAt(pos)
        if it is None:
            return
        self.tree.setCurrentItem(it)
        d = it.data(0, Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        if d[0] == "ch":
            c = self._selected_channel()[1]
            m.addAction("Edit channel...", lambda: QTimer.singleShot(0, self._edit_channel))
            m.addAction("Disable" if c.enabled else "Enable", lambda: self._toggle_enabled(d[1], d[2]))
        else:
            t = self._selected_topic()
            m.addAction("Edit topic...", lambda: QTimer.singleShot(0, self._edit_topic))
            m.addAction("Disable" if t.enabled else "Enable", lambda: self._toggle_enabled(d[1], None))
            m.addSeparator()
            m.addAction("Delete topic", lambda: QTimer.singleShot(0, self._delete_topic))
        m.addSeparator()
        m.addMenu(self._columns_menu()).setText("Columns")
        for a in m.actions()[:3]:
            a.setEnabled(self.recorder is None)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _update_spec_hint(self):
        self.spec_hint.setText(f"Statistics over the last {self.window_s.value():g} s · "
                               "double-click to edit · tick to enable")

    def _set_control_style(self, key: str):
        self.control_style = key
        QSettings().setValue("control_style", key)
        self.apply_theme()

    # ================================================================ documents (closable tabs)
    def _add_doc_tab(self, key: str, index: int | None = None):
        w, title, short, icon = self.docs[key]
        if self.doc_tabs.indexOf(w) >= 0:
            return
        text = short if self._mode == "compact" else title
        i = self.doc_tabs.addTab(w, make_icon(icon, T["muted"]), text) if index is None \
            else self.doc_tabs.insertTab(index, w, make_icon(icon, T["muted"]), text)
        close = QToolButton(); close.setObjectName("tabClose"); close.setText("×")
        close.setToolTip("Close tab (reopen from Project Files or the Project menu)")
        close.clicked.connect(lambda _=False, wid=w: self._close_doc(wid))
        self.doc_tabs.tabBar().setTabButton(i, self.doc_tabs.tabBar().ButtonPosition.RightSide, close)

    def _close_doc(self, w):
        i = self.doc_tabs.indexOf(w)
        if i >= 0:
            self.doc_tabs.removeTab(i)          # the page lives on; reopen it from the tree

    def _show_doc(self, key: str):
        if key not in self.docs:
            return
        self._add_doc_tab(key)
        self.doc_tabs.setCurrentWidget(self.docs[key][0])

    def _refresh_chrome(self):
        """Theme-dependent bits that are not plain QSS: wordmark, tab icons."""
        self.brand.setText(f"<span style='color:{T['accent']}'>NX</span> Logger")
        for key, (w, _, _, icon) in self.docs.items():
            i = self.doc_tabs.indexOf(w)
            if i >= 0:
                self.doc_tabs.setTabIcon(i, make_icon(icon, T["muted"]))

    @contextmanager
    def _waiting(self):
        """Wait cursor around anything that blocks the GUI (file I/O, start/stop)."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()

    # ================================================================ custom views (host API)
    def channel_choices(self):
        out = []
        for t in self.project.topics:
            for c in t.channels:
                out.append((t.id, c.index, f"{t.name} · {c.name}" + (f" [{c.unit}]" if c.unit else ""),
                            ch_color(c), c.unit))
        return out

    def series(self, tid: str, idx: int):
        st = self.streams.get(tid)
        ring = st.rings.get(idx) if st else None
        return ring.ordered() if ring is not None and ring.count else None

    def last_value(self, tid: str, idx: int):
        st = self.streams.get(tid)
        t = next((x for x in self.project.topics if x.id == tid), None)
        c = t.channel(idx) if t else None
        return (st.last_vals.get(idx) if st else None), (c.unit if c else "")

    def view_changed(self):
        self._mark_dirty()

    def stat_values(self, tid: str, idx: int, win: float | None = None):
        st = self.streams.get(tid)
        if st is None or idx not in st.last_vals:
            return None
        vals = dict(zip(STAT_COLS, self._stats(st, idx, win or self.window_s.value())))
        vals["Value"] = st.last_vals[idx]
        return vals

    def alarm_seq(self) -> int:
        return self.alarm_engine.seq

    def alarm_rows(self, source: str, max_rows: int, levels) -> list[dict]:
        eng, out = self.alarm_engine, []
        levels = set(levels or [1, 2, 3, 4])
        if source == "log":
            for ev in reversed(eng.log):
                if ev["level"] not in levels:
                    continue
                unit = "s" if ev["type"] == "D" else ev["unit"]
                out.append({"Time": time.strftime("%m-%d %H:%M:%S", time.localtime(ev["t"])),
                            "Level": f"Level {ev['level']}", "State": ev["event"], "Alarm": ev["name"],
                            "Tag": ev["tag"], "Type": ev["type"], "Value": ap.fmt_val(ev["value"], unit),
                            "Setpoint": ap.fmt_val(ev["setpoint"], unit), "Ack": "",
                            "_level": ev["level"] if ev["event"] == "ON" else 0})
                if len(out) >= max_rows:
                    break
            return out
        for r in eng.active_rows():
            if r["level"] not in levels:
                continue
            unit = "s" if r["type"] == "D" else r["unit"]
            out.append({"Time": time.strftime("%H:%M:%S", time.localtime(r["since"])),
                        "Level": f"Level {r['level']}",
                        "State": ("ALARM" if r["active"] else "CLEARED") + (" · ACK" if r["acked"] else " · UNACK"),
                        "Alarm": r["name"], "Tag": r["tag"], "Type": r["type"],
                        "Value": ap.fmt_val(r["value"], unit), "Setpoint": ap.fmt_val(r["setpoint"], unit),
                        "Ack": "yes" if r["acked"] else "no", "_level": r["level"] if r["active"] else 0,
                        "_flash": not r["acked"] and self.alarm_page.blink})
            if len(out) >= max_rows:
                break
        return out

    def _load_views(self):
        for vid in list(self.view_pages):
            key = f"view:{vid}"
            self._close_doc(self.docs[key][0])
            self.docs.pop(key)[0].deleteLater()
        self.view_pages.clear()
        for vw in self.project.views:
            self._open_view_page(vw, editing=False, show=False)
        self._rebuild_project_tree()

    def _open_view_page(self, vw: dict, editing: bool, show: bool = True):
        page = dv.ViewPage(self, vw, editing=editing)
        self.view_pages[vw["id"]] = page
        self.docs[f"view:{vw['id']}"] = (page, vw["name"], vw["name"], "layout")
        self._add_doc_tab(f"view:{vw['id']}")
        if show:
            self._show_doc(f"view:{vw['id']}")
        return page

    def _new_view(self):
        n = len(self.project.views) + 1
        name, ok = QInputDialog.getText(self, "New view", "View name:", text=f"View {n}")
        if not ok or not name.strip():
            return
        vw = dv.new_view(name.strip())
        self.project.views.append(vw)
        self._open_view_page(vw, editing=True)
        self._mark_dirty()
        self._rebuild_project_tree()
        self.toast.popup("Edit mode - add plots, text or numeric tiles")

    def rename_view(self, page):
        if page is None:
            return
        name, ok = QInputDialog.getText(self, "Rename view", "View name:", text=page.name)
        if ok and name.strip():
            page.view["name"] = name.strip()
            key = f"view:{page.view['id']}"
            self.docs[key] = (page, page.name, page.name, "layout")
            i = self.doc_tabs.indexOf(page)
            if i >= 0:
                self.doc_tabs.setTabText(i, page.name)
            self._mark_dirty()
            self._rebuild_project_tree()

    def delete_view(self, page):
        if page is None or QMessageBox.question(
                self, "Delete view", f"Delete view '{page.name}'?") != QMessageBox.StandardButton.Yes:
            return
        vid = page.view["id"]
        self.project.views = [v for v in self.project.views if v["id"] != vid]
        self._close_doc(page)
        self.docs.pop(f"view:{vid}", None)
        self.view_pages.pop(vid, None)
        page.deleteLater()
        self._mark_dirty()
        self._rebuild_project_tree()

    # ================================================================ help
    def _build_help_menu(self):
        h = self.help_menu
        h.addAction(self.act("Keyboard shortcuts", self._show_shortcuts, "F1"))
        h.addSeparator()
        h.addAction(self.act(f"About {APP_NAME}", self._show_about))

    def _show_shortcuts(self):
        rows = [(a.text().replace("&", ""), a.shortcut().toString(QKeySequence.SequenceFormat.NativeText))
                for m in self.menuBar().findChildren(QMenu) for a in m.actions()
                if not a.shortcut().isEmpty()]
        seen, html = set(), []
        for name, sc in rows:
            if name not in seen:
                seen.add(name)
                html.append(f"<tr><td>{name}</td><td style='padding-left:24px'><b>{sc}</b></td></tr>")
        QMessageBox.information(self, "Keyboard shortcuts", "<table>" + "".join(html) + "</table>")

    def _show_about(self):
        show_about(self)

    # ================================================================ responsive layout
    @staticmethod
    def _screen_is_small() -> bool:
        scr = QApplication.primaryScreen()
        if scr is None:
            return False
        g = scr.availableGeometry()
        return g.width() < 1440 or g.height() < 860

    def _fit_to_screen(self):
        """Open inside the screen: first run, or geometry saved on a bigger monitor."""
        scr = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        restored = self.qs.value("geometry") is not None
        if restored and scr.contains(self.frameGeometry()):
            return
        w = min(scr.width(), max(self.minimumWidth(), min(1640, int(scr.width() * 0.94))))
        h = min(scr.height(), max(self.minimumHeight(), min(940, int(scr.height() * 0.92))))
        self.resize(w, h)
        self.move(scr.x() + (scr.width() - w) // 2, scr.y() + (scr.height() - h) // 2)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "_resp_timer"):
            self._resp_timer.start()

    def _apply_responsive(self):
        self._elide_status()
        w = self.width()
        mode = "compact" if w < BP_COMPACT else ("medium" if w < BP_WIDE else "wide")
        if mode == self._mode:
            return
        prev, self._mode = self._mode, mode

        # toolbar: full text when there is room, otherwise icons (names are in tooltips)
        keep = {self.act_connect, self.act_rec, self.act_stop}
        for a in self.toolbar.actions():
            btn = self.toolbar.widgetForAction(a)
            if isinstance(btn, QToolButton) and a not in (self.act_panel, self.act_log):
                text = mode == "wide" or (mode == "medium" and a in keep)
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text
                                       else Qt.ToolButtonStyle.ToolButtonIconOnly)

        short = mode == "compact"
        for key, (w, full, abbr, _) in self.docs.items():
            i = self.doc_tabs.indexOf(w)
            if i >= 0:
                self.doc_tabs.setTabText(i, abbr if short else full)
        self.spec_hint.setVisible(not short)
        self._update_spec_hint()

        # status panel: auto-hide when narrow, bring back when there is room again
        if short:
            if self.right_dock.isVisible() and not self.right_dock.isFloating():
                self.right_dock.hide()
                self._auto_hidden_right = True
                if prev is not None:
                    self.toast.popup("Status panel hidden - Ctrl+Shift+P shows it")
        elif self._auto_hidden_right:
            self.right_dock.show()
            self._auto_hidden_right = False

        left, right = DOCK_W[mode]
        docks, sizes = [self.left_dock], [left]
        if self.right_dock.isVisible() and not self.right_dock.isFloating():
            docks.append(self.right_dock); sizes.append(right)
        self.resizeDocks(docks, sizes, Qt.Orientation.Horizontal)

    def set_status(self, text, level="muted"):
        self._status_full = text
        super().set_status(text, level)
        self.status_msg.setToolTip(text)
        self._elide_status()

    def _elide_status(self):
        lbl = getattr(self, "status_msg", None)
        if lbl is None or not self._status_full:
            return
        room = max(80, self.width() - 420)            # leave space for the permanent labels
        lbl.setText(lbl.fontMetrics().elidedText(self._status_full, Qt.TextElideMode.ElideMiddle, room))

    # ================================================================ close
    def closeEvent(self, e):
        if self.recorder is not None:
            if QMessageBox.question(self, APP_NAME, "Recording is running. Stop recording and exit?") \
                    != QMessageBox.StandardButton.Yes:
                e.ignore(); return
            self._stop_recording()
        if not self._confirm_discard():
            e.ignore(); return
        self.mqtt.disconnect_from()
        super().closeEvent(e)


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
