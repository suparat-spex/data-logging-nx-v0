"""
main_window.py - NX Logger main window: devices + tags, live views, alarms, recording.

NX Logger (tag-based data logger, Yokogawa GA10 / Genesis64 / FUXA style):
  * Connectivity document: devices (Simulation, Modbus TCP, Modbus RTU, MQTT, OPC UA client;
    OPC DA planned) with groups and tags. Each device runs in its own driver thread
    (comms/drivers); number tags are read every scan interval, MQTT devices in wavepacket
    mode stream ESP32 waveforms as array tags. Start / stop per device or all at once.
  * Tags have every channel property (unit, colour, scaling, decimals, Y range) and are
    used everywhere a channel was: alarms, variables, views, logging, AI/ML log.
  * Views are grids of rows x columns; each cell holds a component (header, trend, FFT,
    number, gauge, LED, tag / statistics / alarm table). Statistics windows are seconds,
    minutes, hours or samples, buffered by the app (core/stat_buffer.py).
  * Right: Properties panel - whatever is selected (device, tag, group, view, cell,
    component ...) is edited there, like the Genesis64 / FUXA property inspectors. A small
    Runtime section under it shows recording / communication state.
  * Recording: CSV (and raw .wpk for waveform devices); session folders keep project.json
    (docs/recording-format.md - read by the NX Viewer). TDMS is no longer written.
  * Project files: .nxl (JSON); .sxl and .mqdaq open and are converted (topics -> MQTT
    wavepacket devices).

Layout:
  * Left:   "Project" dock (navigator tree) and "Data" dock (recorded sessions)
  * Centre: closable document tabs - Quick View, Connectivity, Logging, Alarms, Variables, Views
  * Right:  "Properties" dock; bottom: "Log" dock
Responsive (by window width): wide >= 1500 px, medium 1180-1500, compact < 1180 (the
Properties panel auto-hides, icon-only toolbar, short tab names).

Threads: driver threads only queue (comms/drivers/base.py); decoding, scaling, alarms,
variables and statistics run on the GUI timer (_drain_and_draw 33 ms, _update_stats
500 ms); disk writes in the Recorder / MlLogger threads.

Run:  python -m nxlogger
"""
from __future__ import annotations

import copy
import csv
import json
import os
import queue
import time
import uuid
from contextlib import contextmanager

import numpy as np
from PySide6.QtCore import QEvent, QObject, QSettings, QSize, QTimer, QUrl, Qt
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDockWidget,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
                               QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
import pyqtgraph as pg

from nxlogger import APP_NAME, config, paths
from nxlogger.comms.manager import DriverManager
from nxlogger.core import alarm_rules as ar
from nxlogger.core import analysis as an
from nxlogger.core import channel_math as cm
from nxlogger.core import ml_log as mlg
from nxlogger.core import recording as dl
from nxlogger.core import stat_buffer as sb
from nxlogger.core import variables as vr
from nxlogger.core import view_model as vmod
from nxlogger.core import wavepacket as wp
from nxlogger.core.project import (OLD_PROJECT_EXTS, PROJECT_EXT, Project, fmt_interval, new_log_profile)
from nxlogger.core.ring import MAX_PLOT_S, TopicStream
from nxlogger.core.tags import (COLORS, DRIVERS, PLANNED, DeviceCfg, ScanBlock, TagCfg, driver_label, new_device,
                                new_tag, tag_from_row, tags_to_rows)
from nxlogger.ui.dialogs.about_dialog import show_about
from nxlogger.ui.dialogs.add_tags_dialog import AddTagsDialog
from nxlogger.ui.dialogs.device_dialog import DeviceDialog
from nxlogger.ui.dialogs.log_profile_dialog import LogProfileDialog
from nxlogger.ui.dialogs.ml_log_dialog import MlLogDialog
from nxlogger.ui.dialogs.opcua_browse_dialog import OpcUaBrowseDialog
from nxlogger.ui.dialogs.pick_dialog import PickDialog
from nxlogger.ui.dialogs.tag_dialog import TagDialog
from nxlogger.ui.dialogs.view_dialog import ViewDialog
from nxlogger.ui.icons import check_icon_file, grip_icon_file, level_icon, swatch
from nxlogger.ui.theme import ui_kit as ui
from nxlogger.ui.theme.app_style import DENSE_QSS, EXTRA_QSS, blend, fusion_palette, fusion_qss
from nxlogger.ui.theme.ui_kit import AppWindow, Card, Chip, EmptyState, T, label, make_icon, set_prop
from nxlogger.ui.widgets import alarm_panel as ap
from nxlogger.ui.widgets import properties as props
from nxlogger.ui.widgets import views as dv
from nxlogger.ui.widgets.connectivity import STATE_TOKEN, ConnectivityPage
from nxlogger.ui.widgets.property_grid import PropertiesPanel
from nxlogger.ui.widgets.variables_panel import VariablesPage


# AI/ML data log event markers: (events.csv type, menu text)
MARKER_TYPES = [("note", "Note"), ("normal", "Normal operation"), ("startup", "Startup"),
                ("maintenance", "Maintenance (re-baseline after this)"), ("fault", "Fault"),
                ("bad_data", "Bad data (exclude from training)")]
LEGACY_BROKER = "daq3"            # QSettings group of the MQDAQ / SX Logger broker form


def _brk(path: str) -> str:
    """Let long paths wrap after each separator (a path has no spaces, so a QLabel
    would otherwise force the window wider than a laptop screen)."""
    return path.replace("/", "/​").replace("\\", "\\​")


# window-width breakpoints (px)
BP_COMPACT, BP_WIDE = 1180, 1500
DOCK_W = {"compact": (230, 300), "medium": (260, 320), "wide": (290, 360)}   # (left, right)
CHIP_W = 132                    # status chips keep one width whatever the text


# ======================================================================= window
class _NoFlash(QObject):
    """
    Dialogs open fully painted: a new top-level dialog starts transparent and becomes
    visible after its first paint (otherwise Windows shows a white frame for a moment).
    """

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.Show and isinstance(obj, QDialog) and obj.isWindow() \
                and not obj.property("_noflash"):
            obj.setProperty("_noflash", 1)
            obj.setWindowOpacity(0.0)
            QTimer.singleShot(300, lambda o=obj: _reveal(o))      # fallback
        elif t == QEvent.Type.Paint and isinstance(obj, QDialog) and obj.property("_noflash") == 1:
            obj.setProperty("_noflash", 2)
            QTimer.singleShot(0, lambda o=obj: _reveal(o))        # painted: show it now
        return False


def _reveal(w):
    try:
        w.setWindowOpacity(1.0)
    except RuntimeError:                       # closed before it was revealed
        pass


class DaqWindow(AppWindow):
    TITLE = APP_NAME
    LEFT_TITLE, RIGHT_TITLE = "Project", "Properties"
    LEFT_W, RIGHT_W = 300, 340

    # ---------------------------------------------------------------- init
    def __init__(self):
        self.drivers = DriverManager()
        self.overflow = 0
        self.project = Project.default()
        self.project_path: str | None = None
        self.dirty = False
        self.recorder: dl.Recorder | None = None
        self.ml_logger: mlg.MlLogger | None = None    # AI/ML data log, independent of Record
        self.streams: dict[str, TopicStream] = {}
        self.stats = sb.StatHub()
        self._scan_seq: dict[str, int] = {}           # per device: continuous across driver restarts
        self.t_ref: float | None = None
        self._loading = False
        self.plots: list[pg.PlotItem] = []
        self.curves: dict[tuple[str, int], pg.PlotDataItem] = {}
        self._mode: str | None = None          # compact | medium | wide
        self._dense = self._screen_is_small()  # read by apply_theme() during super().__init__
        self._auto_hidden_right = False
        self._status_full = ""
        self._zoomed = False                   # Quick View plots hold their range after a box zoom
        self._grid = False                     # Quick View plots: stack (False) or 2-column grid
        self.view_pages: dict[str, dv.ViewPage] = {}
        self._scalers: dict[tuple[str, int], object] = {}
        self.docs: dict[str, tuple] = {}       # key -> (widget, title, short title, icon)
        self.alarm_engine = ar.AlarmEngine()
        self.alarm_engine.describe = self._alarm_describe
        self.alarm_engine.ref_value = lambda tid, idx: self.last_value(tid, idx)[0]
        self.alarm_engine.listeners.append(self._on_alarm_event)
        self.notifier = ar.Notifier(lambda *a: self.drivers.publish(*a))
        self.profile_results: dict[str, tuple[bool, str, float]] = {}
        self.variable_engine = vr.VariableEngine()
        self.variable_engine.channel_ids = self._channel_ids
        self.variable_engine.history = self._history
        self.variable_engine.latest = lambda key: self.last_value(*key)[0]
        self.variable_engine.window_stat = self._window_stat
        self._var_groups: dict[str, list[str]] = {}   # recorder group id -> variable ids (rows)
        self._noflash = _NoFlash()                    # no parent: created before the Qt base
        self._alarm_level = None               # last indicator state (repaint only on change)
        self._prop_key: tuple | None = None    # what the Properties panel shows
        self._stat_dirty = True                # statistics windows to (re)collect
        super().__init__()
        self.theme_listeners.append(self._rebuild_plots)
        self.left_dock.setMinimumWidth(210)
        self.left_dock.widget().setMinimumWidth(200)   # ui_kit's scroll area asks for 300
        self.right_dock.setMinimumWidth(260)
        self.setMinimumSize(960, 600)
        self.status_msg.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._resp_timer = QTimer(self, singleShot=True, interval=60, timeout=self._apply_responsive)
        self._fit_to_screen()
        self._apply_responsive()
        self.theme_listeners.append(self._rebuild_project_tree)
        self.banners.lay.setContentsMargins(8, 0, 8, 0)   # no empty gap above the tabs
        self.theme_listeners.append(self._refresh_chrome)
        self._refresh_chrome()
        self.doc_tabs.currentChanged.connect(self._doc_changed)
        self._build_help_menu()
        self.alarm_btn = QToolButton(); self.alarm_btn.setObjectName("alarmStatus")
        self.alarm_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.alarm_btn.setToolTip("Alarms - click to open the Alarms document")
        self.alarm_btn.clicked.connect(lambda: self._show_doc("alarms"))
        self.statusBar().insertPermanentWidget(1, self.alarm_btn)   # 0 = the message label
        self.ml_label = QLabel(); self.ml_label.setObjectName("statusMsg")
        set_prop(self.ml_label, "level", "ok")
        self.ml_label.hide()
        self.statusBar().insertPermanentWidget(1, self.ml_label)
        self.theme_listeners.append(self._ml_update_ui)              # running icon colour
        self._ml_update_ui()
        self.theme_listeners.append(lambda: (setattr(self, "_alarm_level", None),
                                             self._update_alarm_indicator(), self.alarm_page.refresh(),
                                             self.var_page.refresh(), self.conn_page.refresh(),
                                             self.props.refresh_icons()))
        QApplication.instance().installEventFilter(self._noflash)   # dialogs: no white flash

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
        T.clear(); T.update(t)
        app = QApplication.instance()
        app.setStyle("Fusion")
        app.setPalette(fusion_palette(t))
        raw = fusion_qss(ui.QSS + qss)
        for k, v in t.items():
            raw = raw.replace(f"@{k}@", str(v))
        app.setStyleSheet(raw)
        self.refresh_icons()
        for fn in self.theme_listeners:
            fn()

    # ---------------------------------------------------------------- toolbar / menus
    def build_toolbar_items(self, tb):
        self.brand = QLabel(); self.brand.setObjectName("brand")
        self.brand.setToolTip(f"{APP_NAME} - data logger: Modbus, MQTT, OPC UA, simulation")
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
        tb.addAction(self.act_ml)
        tb.addAction(self.act_marker)
        tb.widgetForAction(self.act_marker).setPopupMode(   # click -> choose the marker type
            QToolButton.ToolButtonPopupMode.InstantPopup)
        tb.addSeparator()
        self.act_pause = self.act("Pause view", lambda: self._draw(force=True), "Ctrl+P", "pause")
        self.act_pause.setCheckable(True)
        tb.addAction(self.act_pause)
        tb.addAction(self.act("Clear view", self._clear_view, "Ctrl+L", "trash"))
        tb.addSeparator()
        tb.addAction(self.act_alarms)
        self.add_spacer(tb)
        self.act_left = self.act("Side panel", self._toggle_left_docks, "Ctrl+Shift+B", "panel-left")
        self.act_left.setCheckable(True)
        self._left_shown = [self.left_dock, self.data_dock]   # docks to bring back on show
        for d in self._left_shown:
            d.toggleViewAction().toggled.connect(lambda _: self.act_left.setChecked(
                not (self.left_dock.isHidden() and self.data_dock.isHidden())))
        self.act_left.setChecked(True)
        tb.addAction(self.act_left)
        self.act_log = self.log_dock.toggleViewAction()
        self.act_log.setText("Log panel")
        self.act_log.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self.reg_icon(self.act_log, "panel-bottom")
        tb.addAction(self.act_log)
        self.act_panel = self.right_dock.toggleViewAction()
        self.act_panel.setText("Properties panel")
        self.act_panel.setShortcut(QKeySequence("Ctrl+Shift+P"))
        self.act_panel.triggered.connect(lambda on: setattr(self, "_auto_hidden_right", False))
        self.reg_icon(self.act_panel, "panel-right")
        tb.addAction(self.act_panel)
        for a in (self.act_left, self.act_log, self.act_panel):   # panel toggles: icon only, always
            tb.widgetForAction(a).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        tb.setIconSize(QSize(16, 16) if self._dense else QSize(18, 18))   # top nav icon size
        for a in tb.actions():          # icon-only mode needs the names in tooltips
            self._set_tip(a)

    def _toggle_left_docks(self):
        """Hide / show the left side (Project + Data) as one panel."""
        shown = [d for d in (self.left_dock, self.data_dock) if not d.isHidden()]
        if shown:
            self._left_shown = shown
            for d in shown:
                d.hide()
        else:
            for d in self._left_shown:
                d.show()

    @staticmethod
    def _set_tip(a):
        sc = a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        a.setToolTip(f"{a.text()}  ({sc})" if sc else a.text())

    def build_extra_menus(self, mb):
        # actions shared by the Acquisition menu and the toolbar (menus are built first)
        self.act_connect = self.act("Connect", self._toggle_comm, "Ctrl+K", "play")
        self.act_rec = self.act("Record", self._start_recording, "Ctrl+R", None)
        self.reg_icon(self.act_rec, "circle", "bad")
        self.act_stop = self.act("Stop", self._stop_recording, "Ctrl+Shift+R", "square")
        self.act_stop.setEnabled(False)
        self.act_alarms = self.act("Alarms", lambda: self._show_doc("alarms"), "Ctrl+4", None)
        self.act_alarms.setIcon(make_icon("bell", T["text"]))
        self.act_vars = self.act("Variables", lambda: self._show_doc("variables"), "Ctrl+5", "variable")
        self.act_ml = self.act("AI/ML data log", self._ml_toggle, "Ctrl+Shift+A", "brain-circuit")
        self.act_ml.setCheckable(True)
        self.act_ml_settings = self.act("AI/ML data log settings...", self._ml_settings, None, "sliders")
        self.act_marker = self.act("Marker", lambda: self._ml_marker("note"), "Ctrl+Shift+E", "flag")
        self.act_marker.setEnabled(False)
        mm = QMenu(self)
        for key, text in MARKER_TYPES:
            mm.addAction(text + "...", lambda k=key: self._ml_marker(k))
        self.act_marker.setMenu(mm)
        proj = QMenu("&Project", self)
        mb.insertMenu(self.view_menu.menuAction(), proj)
        add_dev = QMenu("Add device", self)
        add_dev.setIcon(make_icon("server", T["text"]))
        for k, d in DRIVERS.items():
            a = add_dev.addAction(d["label"] + "...", lambda kk=k: self.add_device(kk))
            self.reg_icon(a, d["icon"])
        for a in (self.act("Quick View", lambda: self._show_doc("screen"), "Ctrl+1", "chart"),
                  self.act("Connectivity", lambda: self._show_doc("connectivity"), "Ctrl+2", "network"),
                  self.act("Logging", lambda: self._show_doc("logging"), "Ctrl+3", "file-text"),
                  self.act_alarms,
                  self.act_vars,
                  None,
                  add_dev,
                  self.act("Add tag", self._add_tag_current, "Ctrl+T", "tag"),
                  self.act("Add variable...", lambda: self._var_action("add"), None, "function-square"),
                  self.act("Add alarm...", lambda: self._alarm_action("add"), "Ctrl+Shift+M", "bell"),
                  self.act("Add logging profile...", lambda: self._add_log_profile("signal"), None, "file-plus"),
                  self.act("New view...", self._new_view, "Ctrl+Shift+N", "layout"),
                  None,
                  self.act("Open recordings folder", self._open_log_folder, None, "folder-open")):
            if a is None:
                proj.addSeparator()
            elif isinstance(a, QMenu):
                proj.addMenu(a)
            else:
                proj.addAction(a)
        v = self.view_menu
        first = v.actions()[0]
        v.insertSeparator(first)
        for a in (self.data_dock.toggleViewAction(), self.log_dock.toggleViewAction()):
            v.insertAction(first, a)
        v.insertSeparator(first)
        for a in (self.act_ml, self.act_marker, self.act_ml_settings,
                  self.act("Open AI/ML data folder", self._ml_open_folder, None, "folder-open")):
            v.insertAction(first, a)
        acq = mb.addMenu("&Acquisition")
        for a in (self.act_connect, None, self.act_rec, self.act_stop,
                  None, self.act_ml, self.act_marker, self.act_ml_settings):
            acq.addSeparator() if a is None else acq.addAction(a)
        f = self.file_menu
        first = f.actions()[0]
        for a in (self.act("Save as...", self._save_project_as, "Ctrl+Shift+S"),):
            f.insertAction(first, a)
        f.insertSeparator(first)

    # ---------------------------------------------------------------- left dock
    def build_left(self):
        """Navigator: project tree (the Data dock with recorded sessions sits below it)."""
        return self._build_project_files()

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
        add = QMenu(self)
        for k, d in DRIVERS.items():
            a = add.addAction(d["label"] + "...", lambda kk=k: self.add_device(kk))
            self.reg_icon(a, d["icon"])
        bar = self._nav_bar([("Add device", "server", lambda: add.exec(self.cursor().pos())),
                             ("Add tag (Ctrl+T)", "tag", self._add_tag_current),
                             ("New view", "layout", self._new_view),
                             (None, None, None),
                             ("Expand all", "expand", lambda: self.ptree.expandAll()),
                             ("Collapse", "collapse", self._collapse_tree)])
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
        self.ptree_devices: dict[str, QTreeWidgetItem] = {}
        self._dot_cache: dict[str, object] = {}

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
                ("screen", self._build_screen(), "Quick View", "Quick", "chart"),
                ("connectivity", self._build_connectivity(), "Connectivity", "Tags", "network"),
                ("logging", self._build_logging_spec(), "Logging", "Logging", "file-text"),
                ("alarms", self._build_alarms(), "Alarms", "Alarms", "bell"),
                ("variables", self._build_variables(), "Variables", "Vars", "variable")):
            self.docs[key] = (w, title, short, icon)
            self._add_doc_tab(key)
        self.doc_tabs.setCurrentIndex(0)
        return self.doc_tabs

    def _build_screen(self):
        pg.setConfigOptions(antialias=False)
        self.glw = pg.GraphicsLayoutWidget()
        self.screen_stack = QStackedWidget()
        self.screen_stack.addWidget(EmptyState(
            "🧩", "No tags enabled", "Add a device and its tags in Connectivity, or enable one, "
            "to see them here.", "Open Connectivity", lambda: self._show_doc("connectivity")))
        self.screen_stack.addWidget(EmptyState(
            "📡", "Waiting for data", "Connect: every enabled device starts and its tags appear "
            "here.", "Connect", self._start_all))
        self.screen_stack.addWidget(self.glw)

        bar = QFrame(); bar.setObjectName("bottomBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(10, 6, 10, 6); bl.setSpacing(10)
        self.window_s = QDoubleSpinBox(minimum=0.2, maximum=MAX_PLOT_S, value=60,
                                       singleStep=10, suffix=" s")
        self.window_s.setToolTip("Time window of the plots (and of statistics set to 'Quick View window')")
        self.layout_mode = QComboBox(); self.layout_mode.addItems(["Per device", "Per tag"])
        self.autoscale = QCheckBox("Auto-scale Y", checked=True)
        self.lay_group = QButtonGroup(self)
        lay_btns = []
        for i, (icon, tip) in enumerate((("rows", "Stack plots"), ("grid", "Grid (2 columns)"))):
            b = QToolButton(); b.setCheckable(True); b.setAutoRaise(True); b.setToolTip(tip)
            self.reg_icon(b, icon)
            self.lay_group.addButton(b, i); lay_btns.append(b)
        lay_btns[0].setChecked(True)
        self.lay_group.idClicked.connect(lambda i: (setattr(self, "_grid", i == 1), self._rebuild_plots()))
        self.qv_btn = QToolButton(); self.qv_btn.setText("Tags")
        self.qv_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.qv_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.qv_btn.setToolTip("Choose the tags plotted in Quick View (logging is not affected)")
        self.reg_icon(self.qv_btn, "columns")
        self.qv_menu = QMenu(self.qv_btn)
        self.qv_menu.aboutToShow.connect(self._fill_quick_menu)
        self.qv_btn.setMenu(self.qv_menu)
        for w in (label("Time window", muted=True), self.window_s,
                  label("Plots", muted=True), self.layout_mode, *lay_btns, self.autoscale, self.qv_btn):
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

    def _build_connectivity(self):
        self.conn_page = ConnectivityPage(self)
        return self.conn_page

    def _build_logging_spec(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 14, 16, 14); v.setSpacing(10)
        v.addWidget(label("Logging", name="h1"))
        intro = label("Each Record run creates a new session folder inside the log folder, "
                      "containing the data files and project.json (configuration, start/end "
                      "time, value counts). Only enabled devices and tags are logged.",
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
        for text, icon, slot in (("Add data", "activity", lambda: self._add_log_profile("signal")),
                                 ("Add statistic", "sigma", lambda: self._add_log_profile("statistic")),
                                 ("Add variable", "function-square", None),
                                 ("Edit", "pencil", lambda: self._edit_log_profile()),
                                 ("Duplicate", "copy", lambda: self._dup_log_profile()),
                                 ("Delete", "trash", lambda: self._delete_log_profile())):
            b = QToolButton(); b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.reg_icon(b, icon)
            if slot:
                b.clicked.connect(slot)
            else:                                    # Add variable: logged by "All tags" profiles
                b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                b.setToolTip("Add a calculated tag (variable) - it is logged by profiles set to "
                             "'All enabled tags', or tick it in a profile")
                vm_ = QMenu(b)
                for k, txt in vr.KINDS.items():
                    vm_.addAction(txt + "...", lambda kk=k: self.var_page.add_variable(kk))
                b.setMenu(vm_)
            bl.addWidget(b); self.lp_btns.append(b)
        bl.addStretch(1)
        bl.addWidget(label("tick = enabled · double-click to edit", muted=True, small=True))
        self.lp_tree = QTreeWidget()
        self.lp_tree.setHeaderLabels(["Profile", "Type", "Format", "Tags", "Details"])
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

    def _build_variables(self):
        self.var_page = VariablesPage(self)
        return self.var_page

    # ---------------------------------------------------------------- right dock: Properties
    def build_right(self):
        self._build_side_docks()
        rt = QFrame(); rt.setObjectName("propHead")
        v = QVBoxLayout(rt); v.setContentsMargins(10, 8, 10, 8); v.setSpacing(6)
        v.addWidget(label("Runtime", name="sectionTitle"))
        chips = QHBoxLayout()
        self.chip_conn, self.chip_rec = Chip(), Chip()
        for c in (self.chip_conn, self.chip_rec):
            c.setFixedWidth(CHIP_W)
            chips.addWidget(c)
        chips.addStretch(1)
        v.addLayout(chips)
        grid = QGridLayout(); grid.setSpacing(6)
        self.cards: dict[str, Card] = {}
        for i, (k, t) in enumerate([("elapsed", "Recording time"), ("size", "Data on disk"),
                                    ("rx", "Values received / s"), ("lost", "Errors / lost")]):
            self.cards[k] = Card(t)
            grid.addWidget(self.cards[k], i // 2, i % 2)
        v.addLayout(grid)
        self.last_session = label("", muted=True, small=True)
        self.last_session.setWordWrap(True)
        self.last_session.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.last_session)
        self.props = PropertiesPanel(self.reg_icon, footer=rt)
        self.lbl_topics = self.add_status_label("")
        self.lbl_rx = self.add_status_label("")
        return self.props

    def _build_side_docks(self):
        """Data dock under the Project tree, Log dock under the documents."""
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

    # ---------------------------------------------------------------- Properties: selection
    def select_object(self, ref):
        """Something was selected (project tree, Connectivity, view): show its properties."""
        t = None
        if ref:
            kind = ref[0]
            if kind == "device":
                t = props.device_target(self, ref[1])
            elif kind == "group":
                t = props.group_target(self, ref[1], ref[2])
            elif kind == "tag":
                t = props.tag_target(self, ref[1], ref[2])
            elif kind == "tags":
                t = props.tags_target(self, ref[1], ref[2])
            elif kind == "project":
                t = props.project_target(self)
        self.show_properties(t)

    def show_properties(self, t):
        self._prop_key = t.key if t is not None else None
        self.props.show_target(t)

    def _refresh_properties(self):
        """The model changed under the panel: rebuild what it shows (same object)."""
        k = self._prop_key
        if not k:
            return
        if k[0] in ("device", "group", "tag", "tags", "project"):
            ref = k if k[0] != "tags" else ("tags", k[1], list(k[2]))
            self.select_object(ref)
        elif k[0] in ("view", "tile", "cell", "line"):
            page = self.doc_tabs.currentWidget()
            if isinstance(page, dv.ViewPage):
                self.view_selected(page, page.selected)

    def view_selected(self, page, sel):
        """A view's selection changed (host API of views.py)."""
        if page is self.doc_tabs.currentWidget():
            self.show_properties(props.selection_target(self, page, sel))

    def _doc_changed(self, _i):
        page = self.doc_tabs.currentWidget()
        if isinstance(page, dv.ViewPage):
            self.view_selected(page, page.selected)
        elif page is self.docs.get("connectivity", (None,))[0] and self._prop_key is None:
            pass
        self._draw(force=True)
        if page is self.docs.get("connectivity", (None,))[0]:
            self.conn_page.update_live()

    # ================================================================ project
    def _set_project(self, p: Project, path: str | None):
        self.drivers.stop_all()
        self.project, self.project_path = p, path
        self._loading = True
        self.p_name.setText(p.name)
        self.p_operator.setText(p.operator)
        self.p_desc.setPlainText(p.description)
        self.p_file.setText(_brk(path) if path else "Not saved yet")
        self.log_folder.setText(p.logging.folder)
        self._loading = False
        self.streams.clear(); self.t_ref = None; self._scan_seq.clear()
        self.variable_engine.reset()
        self.dirty = False
        self.alarm_engine.states.clear()
        self.alarm_engine.reset_data_loss()
        self.alarm_engine.clear_log()
        self._structure_changed()
        self._update_title()
        self._refresh_sessions()
        self._load_views()
        self.var_page.refresh()
        self.conn_page.refresh(())
        self.show_properties(props.project_target(self))
        self._sync_comm()
        if p.ml_log.get("autostart") and self.ml_logger is None:   # unattended 24/7 logging
            QTimer.singleShot(0, self._ml_start)
        if p.migrated_from:
            self.banners.post("proj", f"Opened a {p.migrated_from} project: every MQTT topic is now an MQTT "
                                      f"device (wavepacket mode). Save it as {PROJECT_EXT} to keep the new "
                                      "format.", "info", "Save as...", self._save_project_as)

    def mark_dirty(self):
        self._mark_dirty()

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

    def project_props_changed(self, key):
        """Edited in the Properties panel: mirror into the Logging page."""
        self._loading = True
        self.p_name.setText(self.project.name); self.p_operator.setText(self.project.operator)
        if key == "description":
            self.p_desc.setPlainText(self.project.description)
        self._loading = False
        self._update_logging_help()
        self._update_title()
        self._rebuild_project_tree()

    def _edit_logging(self, key, value):
        if self._loading:
            return
        setattr(self.project.logging, key, value)
        self._mark_dirty()
        self._update_logging_help()

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
            p = Project(name="Untitled project", logging_profiles=[new_log_profile("signal")],
                        alarm_profiles=[ar.new_profile("popup", "Popup + sound")])
            self._set_project(p, None)
            self.toast.popup("New project - add a device in Connectivity")
            self._show_doc("connectivity")

    def _open_project(self):
        if not (self._recording_guard() and self._confirm_discard()):
            return
        exts = " ".join(f"*{e}" for e in (PROJECT_EXT, *OLD_PROJECT_EXTS))
        path, _ = QFileDialog.getOpenFileName(self, "Open project", self._project_dir(),
                                              f"{APP_NAME} project ({exts});;All files (*)")
        if path:
            try:
                self._load_project(path)
                self.toast.popup(f"Opened {os.path.basename(path)}")
            except Exception as e:
                self.banners.post("proj", f"Could not open project: {e}", "bad")

    @staticmethod
    def _legacy_broker() -> dict:
        """Broker of the old global connection form (QSettings, else config.ini) - for v1 files."""
        s, c = QSettings(), config.get().mqtt
        g = lambda k, d: s.value(f"{LEGACY_BROKER}/{k}", d)    # noqa: E731
        try:
            port = int(g("port", c.get("port") or 1883))
        except (TypeError, ValueError):
            port = 1883
        return {"host": g("host", c.get("host") or "broker.emqx.io"), "port": port,
                "transport": g("transport", c.get("transport") or "TCP"),
                "username": g("user", c.get("username", "")), "password": c.get("password", ""),
                "client_id": g("client_id", c.get("client_id", ""))}

    def _load_project(self, path: str):
        with self._waiting(), open(path, encoding="utf-8") as f:
            p = Project.from_dict(json.load(f), broker=self._legacy_broker())
        if p.migrated_from:                      # an old file is never overwritten in the new format
            path = None
        self._set_project(p, path)
        if path:
            QSettings().setValue("last_project", path)
        self._log(f"Project opened: {path or '(converted, not saved yet)'}")

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
        self.project.migrated_from = ""
        self.banners.dismiss("proj")
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

    # ================================================================ devices + tags (host API)
    @property
    def recording(self) -> bool:
        return self.recorder is not None

    def _recording_guard(self) -> bool:
        if self.recorder is not None:
            self.toast.popup("Stop recording to change the project")
            return False
        return True

    def _structure_changed(self):
        """Devices / tags changed: scaling, formula names, alarms, plots, logging, statistics."""
        self._rebuild_colors()
        self._rebuild_scalers()
        self._variables_reload()                 # tag names are formula names
        self._rebuild_plots()
        self._alarms_reload()
        self._update_logging_help()
        self._update_lock()
        self._ml_restart()                       # new device folders / names in meta.json
        self._stat_dirty = True
        self._rebuild_project_tree()

    def device_changed(self, did: str, restart: bool = True, structure: bool = False):
        """A device's properties changed: restart its driver if it runs, refresh views."""
        self._mark_dirty()
        dev = self.project.device(did)
        if structure and dev is not None:        # MQTT mode switch: tags change kind
            for t in dev.tags:
                t.kind = "array" if dev.wave else "number"
            self.streams.pop(did, None)
        if restart and self.drivers.running(did):
            self.drivers.stop(did)
            if dev is not None and dev.enabled:
                self.drivers.start(dev)
        QTimer.singleShot(0, lambda: (self._structure_changed(), self.conn_page.refresh(),
                                      self._refresh_properties()))

    def tags_changed(self, did: str, restart: bool = False):
        self._mark_dirty()
        if restart and self.drivers.running(did):
            self.drivers.start(self.project.device(did))
        QTimer.singleShot(0, lambda: (self._structure_changed(), self.conn_page.refresh(),
                                      self._refresh_properties()))

    def _rebuild_colors(self):
        """Default tag colours run through the palette across the whole project (the first tag
        of every device would otherwise get the same colour); a tag's own colour wins."""
        self._colors = {}
        k = 0
        for d in self.project.devices:
            for t in d.tags:
                self._colors[(d.id, t.index)] = t.color or COLORS[k % len(COLORS)]
                k += 1

    def color_of(self, did: str, idx: int) -> str:
        return getattr(self, "_colors", {}).get((did, idx)) or COLORS[idx % len(COLORS)]

    def device_state(self, did: str) -> tuple[str, str]:
        return self.drivers.state(did)

    def tag_live(self, did: str, idx: int):
        """(value text, quality text, good True / False / None, time text) for tables."""
        st = self.streams.get(did)
        t = self.project.tag(did, idx)
        if st is None or idx not in st.last_vals or t is None:
            run = self.drivers.running(did)
            return "", ("Waiting" if run else ""), None, ""
        good = st.good.get(idx, True)
        val = st.last_vals[idx]
        age = time.time() - st.stamp.get(idx, 0)
        dev = self.project.device(did)
        stale = dev is not None and not dev.wave and age > max(5.0, 3 * dev.scan_ms / 1000)
        text = f"{val:.{int(t.decimals)}f}" if good else "-"
        qual = "Bad" if not good else ("Stale" if stale else "Good")
        return text, qual, (False if not good else (None if stale else True)), \
            time.strftime("%H:%M:%S", time.localtime(st.stamp.get(idx, 0)))

    def _unique_name(self, base: str, taken: set[str]) -> str:
        return base if base not in taken else next(f"{base} {i}" for i in range(2, 999) if f"{base} {i}" not in taken)

    def add_device(self, driver: str):
        if not self._recording_guard():
            return
        dev = new_device(driver, self._unique_name(driver_label(driver).split(" (")[0],
                                                    {d.name for d in self.project.devices}))
        if driver == "mqtt":                     # start from the broker used so far
            dev.settings.update({k: v for k, v in self._legacy_broker().items() if v not in ("", None)})
        dlg = DeviceDialog(self, dev, {d.name for d in self.project.devices}, new=True)
        if not dlg.exec():
            return
        dlg.apply(dev)
        if dev.driver != driver and dev.driver != "sim":
            dev.tags = []                        # the starter tags of the simulator don't fit
        if dev.wave and not dev.tags:
            dev.tags = [TagCfg(i, f"CH{i + 1}", kind="array") for i in range(4)]
        self.project.devices.append(dev)
        self._mark_dirty()
        self._structure_changed()
        self.conn_page.refresh(("device", dev.id))
        self._show_doc("connectivity")
        self.select_object(("device", dev.id))
        self.set_status(f"Device '{dev.name}' added - add its tags, then Start", "ok")
        if self.drivers.any_running and dev.enabled and dev.driver not in PLANNED:
            self.drivers.start(dev)

    def edit_device(self, did: str):
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        dlg = DeviceDialog(self, dev, {d.name for d in self.project.devices if d is not dev})
        if dlg.exec():
            was_wave = dev.wave
            dlg.apply(dev)
            self.device_changed(did, structure=was_wave != dev.wave)

    def delete_device(self, did: str):
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        if QMessageBox.question(self, "Delete device", f"Delete device '{dev.name}' and its "
                                                       f"{len(dev.tags)} tag(s)?") != QMessageBox.StandardButton.Yes:
            return
        self.drivers.stop(did)
        self.project.devices.remove(dev)
        self.streams.pop(did, None)
        self._mark_dirty()
        self._structure_changed()
        self.conn_page.refresh(())
        self.show_properties(None)
        self.toast.popup(f"Deleted '{dev.name}'")

    def _add_tag_current(self):
        """Ctrl+T: add a tag to the device selected in Connectivity (or the first one)."""
        sel = self.conn_page._sel
        did = sel[1] if len(sel) > 1 else (self.project.devices[0].id if self.project.devices else None)
        if did is None:
            self._show_doc("connectivity")
            self.toast.popup("Add a device first")
            return
        self.add_tags(did, sel[2] if sel and sel[0] == "group" else "", False)

    def add_tags(self, did: str, group: str, bulk: bool):
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        if dev.wave and len(dev.tags) >= wp.MAX_CH:
            self.toast.popup("A waveform packet has at most 8 channels")
            return
        if bulk:
            dlg = AddTagsDialog(self, dev, group)
            if not dlg.exec():
                return
            new = dlg.tags()
        else:
            t = new_tag(dev, "", group)
            t.name = self._unique_name(t.name, {x.name for x in dev.tags})
            new = [t]
        dev.tags.extend(new)
        dev.tags.sort(key=lambda t: t.index) if dev.wave else None
        self._show_doc("connectivity")
        self.tags_changed(did, restart=True)
        QTimer.singleShot(0, lambda: (self.conn_page.select_tag(did, new[-1].index),
                                      self.select_object(("tag", did, new[-1].index))))

    def edit_tag(self, did: str, idx: int):
        dev = self.project.device(did)
        t = dev.tag(idx) if dev else None
        if t is None or not self._recording_guard():
            return
        st = self.streams.get(did)
        k = next(i for i, x in enumerate(x for d in self.project.devices for x in d.tags) if x is t)
        dlg = TagDialog(self, dev, t, COLORS[k % len(COLORS)],
                        lambda: st.raw_mean.get(idx) if st else None)
        if dlg.exec():
            dlg.apply(t)
            self.tags_changed(did, restart=True)
            self.set_status(f"Tag '{t.name}' updated", "ok")

    def delete_tags(self, did: str, idxs: list[int]):
        dev = self.project.device(did)
        if dev is None or not idxs or not self._recording_guard():
            return
        names = [t.name for t in dev.tags if t.index in idxs]
        if QMessageBox.question(self, "Delete tags", f"Delete {len(names)} tag(s): "
                                f"{', '.join(names[:5])}{' ...' if len(names) > 5 else ''}?") \
                != QMessageBox.StandardButton.Yes:
            return
        dev.tags = [t for t in dev.tags if t.index not in idxs]
        st = self.streams.get(did)
        for i in idxs:
            if st:
                st.rings.pop(i, None); st.last_vals.pop(i, None)
        self.show_properties(None)
        self.tags_changed(did, restart=True)

    def set_tags_enabled(self, did: str, idxs: list[int], on: bool):
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        for t in dev.tags:
            if t.index in idxs:
                t.enabled = on
        self.tags_changed(did, restart=True)

    def move_tags(self, did: str, idxs: list[int], group: str):
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        for t in dev.tags:
            if t.index in idxs:
                t.group = group
        self.tags_changed(did)

    def add_group(self, did: str, parent: str):
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        name, ok = QInputDialog.getText(self, "Add group", f"Group name{' in ' + parent if parent else ''}:")
        name = "/".join(p.strip() for p in name.split("/") if p.strip())
        if ok and name:
            path = f"{parent}/{name}" if parent else name
            if path not in dev.groups:
                dev.groups.append(path)
            self._mark_dirty()
            self.conn_page.refresh(("group", did, path))
            self.select_object(("group", did, path))

    def rename_group(self, did: str, path: str, name: str):
        dev = self.project.device(did)
        name = name.strip().replace("/", "-")
        if dev is None or not name or not self._recording_guard():
            return
        new = (path.rsplit("/", 1)[0] + "/" + name) if "/" in path else name
        fix = lambda g: new + g[len(path):] if g == path or g.startswith(path + "/") else g   # noqa: E731
        dev.groups = sorted({fix(g) for g in dev.groups})
        for t in dev.tags:
            t.group = fix(t.group)
        self._mark_dirty()
        self.conn_page.refresh(("group", did, new))
        self.select_object(("group", did, new))

    def delete_group(self, did: str, path: str):
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        inside = [t for t in dev.tags if t.group == path or t.group.startswith(path + "/")]
        if inside:
            r = QMessageBox.question(self, "Delete group", f"Group '{path}' has {len(inside)} tag(s). "
                                     "Delete the tags too? (No = keep them, without a group)",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                                     QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return
            if r == QMessageBox.StandardButton.Yes:
                dev.tags = [t for t in dev.tags if t not in inside]
            else:
                parent = path.rsplit("/", 1)[0] if "/" in path else ""
                for t in inside:
                    t.group = parent
        dev.groups = [g for g in dev.groups if not (g == path or g.startswith(path + "/"))]
        self.show_properties(None)
        self.conn_page.refresh(("device", did))
        self.tags_changed(did, restart=bool(inside))

    def write_tag(self, did: str, idx: int):
        t = self.project.tag(did, idx)
        if t is None:
            return
        if self.drivers.state(did)[0] != "online":
            self.toast.popup("The device is not online")
            return
        cur = self.last_value(did, idx)[0]
        val, ok = QInputDialog.getDouble(self, f"Write {t.name}", "Value (sent as is - before scaling):",
                                         cur if cur is not None else 0.0, -1e12, 1e12, 6)
        if ok:
            self.drivers.write(did, idx, val)
            self._log(f"Write {t.name} ← {val:g}")

    def browse_opcua(self, did: str, group: str):
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        dlg = OpcUaBrowseDialog(self, dict(dev.settings), dev.all_groups(), group)
        if not dlg.exec():
            return
        picked = dlg.selected()
        have = {t.address.get("node") for t in dev.tags}
        new = []
        for node, name in picked:
            if node in have:
                continue
            t = new_tag(dev, self._unique_name(name, {x.name for x in dev.tags}), dlg.group_path(), node=node)
            dev.tags.append(t); new.append(t)
        if new:
            self.tags_changed(did, restart=True)
            self.toast.popup(f"{len(new)} tag(s) added")

    def import_tags(self, did: str):
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import tags", self._project_dir(), "CSV (*.csv);;All files (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
        except (OSError, csv.Error) as e:
            self.banners.post("proj", f"Could not read {path}: {e}", "bad")
            return
        added = 0
        for r in rows:
            if not (r.get("name") or "").strip() or (r.get("device") and r["device"] != dev.name
                                                    and any(x.get("device") == dev.name for x in rows)):
                continue
            t = tag_from_row(dev, r)
            t.name = self._unique_name(t.name, {x.name for x in dev.tags})
            dev.tags.append(t); added += 1
        self.tags_changed(did, restart=True)
        self.toast.popup(f"{added} tag(s) imported")

    def export_tags(self, did: str):
        dev = self.project.device(did)
        if dev is None:
            return
        start = os.path.join(self._project_dir(), dl.safe_name(dev.name) + "_tags.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export tags", start, "CSV (*.csv)")
        if not path:
            return
        from nxlogger.core.tags import CSV_COLS
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, CSV_COLS)
            w.writeheader(); w.writerows(tags_to_rows(dev))
        self.toast.popup(f"{len(dev.tags)} tag(s) exported")

    # ---- communication
    def start_device(self, did: str):
        dev = self.project.device(did)
        if dev is None:
            return
        if dev.driver in PLANNED:
            self.toast.popup(f"{driver_label(dev.driver)} cannot connect yet")
            return
        self.drivers.start(dev)
        self._log(f"{dev.name}: starting ({driver_label(dev.driver)}, {dev.topic})")
        self._sync_comm(); self.conn_page.update_states(); self._refresh_properties()

    def stop_device(self, did: str):
        self.drivers.stop(did)
        self._sync_comm(); self.conn_page.update_states(); self._refresh_properties()

    def _start_all(self):
        devs = [d for d in self.project.devices if d.enabled and d.driver not in PLANNED]
        if not devs:
            self._show_doc("connectivity")
            self.toast.popup("No enabled device - add one in Connectivity")
            return
        for d in devs:
            if not self.drivers.running(d.id):
                self.drivers.start(d)
        self._log(f"Communication started: {len(devs)} device(s)")
        self._sync_comm(); self.conn_page.update_states(); self._refresh_properties()

    def _stop_all(self):
        with self._waiting():
            self.drivers.stop_all()
        self._log("Communication stopped")
        self._sync_comm(); self.conn_page.update_states(); self._refresh_properties()

    def _toggle_comm(self):
        self._stop_all() if self.drivers.any_running else self._start_all()

    def _sync_comm(self):
        on = self.drivers.any_running
        text = "Disconnect" if on else "Connect"
        if self.act_connect.text() != text:
            self.act_connect.setText(text)
            self.act_connect.setIcon(make_icon("square" if on else "play", T["bad" if on else "ok"]))
            self.act_connect.setToolTip(("Stop communication with all devices" if on else
                                         "Start communication: connect all enabled devices") + "  (Ctrl+K)")

    # ================================================================ navigator (left dock)
    @staticmethod
    def _dot(token: str) -> QIcon:
        pm = QPixmap(20, 20); pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(T[token]))
        p.drawEllipse(5, 5, 10, 10); p.end()
        pm.setDevicePixelRatio(2)
        return QIcon(pm)

    def _device_token(self, d: DeviceCfg) -> str:
        if not d.enabled:
            return "muted"
        return STATE_TOKEN.get(self.drivers.state(d.id)[0], "muted")

    def _rebuild_project_tree(self, *_):
        tree = getattr(self, "ptree", None)
        if tree is None:
            return
        expanded = {did for did, it in self.ptree_devices.items() if it.isExpanded()}
        tree.clear(); self.ptree_devices.clear(); self._dot_cache.clear()
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

        node(root, "Quick View", make_icon("chart", T["text"]), ("doc", "screen"),
             "Live plots of every enabled tag - choose them with the Tags button")
        self.nav_conn = node(root, "Connectivity", make_icon("network", T["text"]), ("doc", "connectivity"),
                             "Devices and tags: Simulation, Modbus TCP / RTU, MQTT, OPC UA")
        for d in self.project.devices:
            tok = self._device_token(d)
            di = node(self.nav_conn, f"{d.name}  ({len(d.tags)})", self._dot(tok), ("device", d.id),
                      f"{driver_label(d.driver)} · {d.topic}" + ("" if d.enabled else "  ·  disabled"))
            self._dot_cache[d.id] = tok
            if not d.enabled:
                di.setForeground(0, muted)
            for t in d.tags[:200]:                   # the Connectivity table shows them all
                ti = node(di, t.name + (f"  ({t.unit})" if t.unit else ""), swatch(self.color_of(d.id, t.index)),
                          ("tag", d.id, t.index), (t.group + " · " if t.group else "") + t.description)
                if not (t.enabled and d.enabled):
                    ti.setForeground(0, muted)
            di.setExpanded(d.id in expanded)
            self.ptree_devices[d.id] = di
        self.nav_conn.setExpanded(True)
        logn = node(root, "Logging", make_icon("file-text", T["text"]), ("doc", "logging"),
                    "Test properties, log folder and logging profiles")
        for pr in self.project.logging_profiles:
            stat = pr["type"] == "statistic"
            li = node(logn, pr["name"], make_icon("sigma" if stat else "activity", T["muted"]), ("lprof", pr["id"]),
                      (f"Statistic · every {fmt_interval(pr['interval_s'])}" if stat else "Data")
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
        varn = node(root, "Variables", make_icon("variable", T["text"]), ("vars",),
                    "Calculated tags: formulas, statistics, signal processing - right-click to add")
        for v in self.project.variables:
            err = self.variable_engine.errors.get(v["id"])
            vi = node(varn, v["name"], swatch(self.var_color(v)), ("var", v["id"]),
                      vr.describe(v) + (f"\n{err}" if err else "") + ("" if v["enabled"] else "  ·  disabled"))
            if not v["enabled"]:
                vi.setForeground(0, muted)
            elif err:
                vi.setForeground(0, QBrush(QColor(T["bad"])))
        varn.setExpanded(True)
        views = node(root, "Views", make_icon("layout", T["text"]), ("views",),
                     "Screens: grids of headers, trends, numbers, gauges, tables - right-click for New view")
        for vw in self.project.views:
            node(views, vw["name"], make_icon("layout", T["muted"]), ("view", vw["id"]))
        views.setExpanded(True)
        root.setExpanded(True)
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
            act = eng.count()
            text = "Alarms" + (f"  ·  {act} active" if act else "")
            if self.nav_alarms.text(0) != text:
                self.nav_alarms.setText(0, text)
                self.nav_alarms.setForeground(0, QBrush(QColor(T["bad"])) if act else QBrush())
        for d in self.project.devices:
            it = self.ptree_devices.get(d.id)
            tok = self._device_token(d)
            if it is not None and (force or self._dot_cache.get(d.id) != tok):
                it.setIcon(0, self._dot(tok)); self._dot_cache[d.id] = tok
        if getattr(self, "nav_conn", None) is not None:
            n = sum(1 for d in self.project.devices if self.drivers.state(d.id)[0] == "online")
            text = "Connectivity" + (f"  ·  {n} online" if self.drivers.any_running else "")
            if self.nav_conn.text(0) != text:
                self.nav_conn.setText(0, text)

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

    def _collapse_tree(self):
        for it in self.ptree_devices.values():
            it.setExpanded(False)

    def _nav_data(self, it):
        return it.data(0, Qt.ItemDataRole.UserRole) if it is not None else None

    def _nav_open(self, it):
        d = self._nav_data(it)
        if not d:
            return
        if d[0] == "doc":
            self._show_doc(d[1])
        elif d[0] == "project":
            self.show_properties(props.project_target(self))
        elif d[0] == "view":
            self._show_doc(f"view:{d[1]}")
            page = self.view_pages.get(d[1])
            if page is not None:
                self.show_properties(props.view_target(self, page))
        elif d[0] == "device":
            self._show_doc("connectivity")
            self.conn_page.refresh(("device", d[1]))
            self.select_object(("device", d[1]))
        elif d[0] == "tag":
            self._show_doc("connectivity")
            self.conn_page.select_tag(d[1], d[2])
            self.select_object(("tag", d[1], d[2]))
        elif d[0] == "lprof":
            self._show_doc("logging")
            self._select_log_profile(d[1])
            pr = self._log_profile(d[1])
            if pr:
                self.show_properties(props.simple_target(
                    pr["name"], "file-text", "Logging profile - edit in its dialog",
                    {"Type": "Statistic" if pr["type"] == "statistic" else "Data", "Format": pr["format"],
                     "Enabled": "Yes" if pr["enabled"] else "No"},
                    [("Edit...", "pencil", lambda: self._edit_log_profile(d[1]))], key=("lprof", d[1])))
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
        elif d[0] in ("vars", "var"):
            self._show_doc("variables")
            if d[0] == "var":
                self.var_page.select(d[1])

    def _nav_double_clicked(self, it, _col):
        d = self._nav_data(it)
        if d and d[0] == "device":
            QTimer.singleShot(0, lambda: self.edit_device(d[1]))     # dialogs rebuild trees
        elif d and d[0] == "tag":
            QTimer.singleShot(0, lambda: self.edit_tag(d[1], d[2]))
        elif d and d[0] == "lprof":
            QTimer.singleShot(0, lambda: self._edit_log_profile(d[1]))
        elif d and d[0] == "alarm":
            QTimer.singleShot(0, lambda: self.alarm_page.edit_alarm(d[1]))
        elif d and d[0] == "aprof":
            QTimer.singleShot(0, lambda: self.alarm_page.edit_profile(d[1]))
        elif d and d[0] == "var":
            QTimer.singleShot(0, lambda: self.var_page.edit_variable(d[1]))

    def _nav_menu(self, pos):
        it = self.ptree.itemAt(pos)
        d = self._nav_data(it) or ("none",)
        m = QMenu(self)
        if d[0] == "device":
            dev = self.project.device(d[1])
            run = self.drivers.running(d[1])
            m.addAction("Edit device...", lambda: QTimer.singleShot(0, lambda: self.edit_device(d[1])))
            m.addAction("Stop" if run else "Start", lambda: (self.stop_device if run else self.start_device)(d[1]))
            m.addAction("Add tag", lambda: self.add_tags(d[1], "", False))
            m.addAction("Add tags...", lambda: self.add_tags(d[1], "", True))
            if dev and dev.driver == "opcua":
                m.addAction("Browse server...", lambda: self.browse_opcua(d[1], ""))
            m.addSeparator()
            m.addAction("Delete device", lambda: self.delete_device(d[1]))
        elif d[0] == "tag":
            m.addAction("Edit tag...", lambda: QTimer.singleShot(0, lambda: self.edit_tag(d[1], d[2])))
            t = self.project.tag(d[1], d[2])
            if t is not None:
                m.addAction("Disable" if t.enabled else "Enable",
                            lambda: self.set_tags_enabled(d[1], [d[2]], not t.enabled))
            m.addAction("Add alarm for this tag...", lambda: self.add_alarm_for((d[1], d[2])))
            m.addAction("Add statistic variable of this tag...", lambda: self._var_action("add", [d[1], d[2]]))
            m.addSeparator()
            m.addAction("Delete tag", lambda: self.delete_tags(d[1], [d[2]]))
        elif d[0] in ("views", "view"):
            if d[0] == "view":
                page = self.view_pages.get(d[1])
                m.addAction("Open", lambda: self._show_doc(f"view:{d[1]}"))
                m.addAction("View properties...", lambda: self.view_properties(page))
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
        elif d[0] in ("vars", "var"):
            vp = self.var_page
            if d[0] == "var":
                v = next((x for x in self.project.variables if x["id"] == d[1]), None)
                m.addAction("Edit variable...", lambda: QTimer.singleShot(0, lambda: vp.edit_variable(d[1])))
                m.addAction("Disable" if v and v["enabled"] else "Enable", lambda: vp.toggle(d[1]))
                m.addAction("Delete variable", lambda: (self._show_doc("variables"), vp.select(d[1]),
                                                        vp.delete_variable()))
                m.addAction("Add alarm for this variable...",
                            lambda: self._alarm_action("add", list(vr.var_key(d[1]))))
                m.addSeparator()
            add = m.addMenu("Add variable")
            for k, text in vr.KINDS.items():
                add.addAction(text + "...", lambda kk=k: (self._show_doc("variables"), vp.add_variable(kk)))
        else:
            add = m.addMenu("Add device")
            for k, dd in DRIVERS.items():
                add.addAction(dd["label"] + "...", lambda kk=k: self.add_device(kk))
            m.addAction("New view...", self._new_view)
        free = ("views", "view", "alarms", "alarm", "aprofs", "aprof")
        for a in m.actions():
            a.setEnabled(a.isEnabled() and (self.recorder is None or d[0] in free
                                            or a.text().startswith(("Add alarm", "Start", "Stop"))))
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

    # ================================================================ logging profiles
    def _profile_topics(self, pr: dict) -> list[dict]:
        """Recorder device list for one logging profile (enabled devices / tags only)."""
        sel = None if pr.get("channels") is None else {tuple(x) for x in pr["channels"]}
        out = []
        for d in self.project.devices:
            if not d.enabled:
                continue
            chans = [(t.index, t.name, t.unit) for t in d.tags
                     if t.enabled and (sel is None or (d.id, t.index) in sel)]
            if chans:
                out.append({"id": d.id, "name": d.name, "topic": d.topic, "channels": chans,
                            "virtual": not d.wave, "driver": d.driver})
        # variables: one group per base device and rate (waveform / one value per block)
        names = {d.id: d.name for d in self.project.devices}
        byid = {v["id"]: v for v in self.project.variables}
        for gid, vids in self._var_groups.items():
            chans = [(i, byid[vid]["name"], byid[vid].get("unit", "")) for i, vid in enumerate(vids)
                     if vid in byid and (byid[vid].get("log", True) if sel is None else vr.var_key(vid) in sel)]
            if chans:
                _, base, rate = gid.split(":")
                out.append({"id": gid, "name": f"Variables {names.get(base, base)}" +
                            (" stats" if rate == "s" else ""), "topic": "calculated",
                            "channels": chans, "virtual": True})
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
                files.append(f"({pr['name']}: no enabled tags)")
                continue
            for t in topics:
                wpk = fmt == "WPK" and not t["virtual"] and pr["type"] == "signal"
                files.append(f"{pn}_{dl.safe_name(t['name'])}{'.wpk' if wpk else '.csv'}")
                if pr["type"] == "signal" and pr.get("keep_raw") and fmt != "WPK" and not t["virtual"]:
                    files.append(f"{pn}_{dl.safe_name(t['name'])}.wpk")
        if len(files) == 1:
            files.append("(no enabled logging profile - Record is not possible)")
        self.session_preview.setText(
            _brk(f"{os.path.join(lg.folder, name + '_<yyyymmdd_hhmmss>')}{os.sep}") + "\n    " +
            "\n    ".join(files))
        self.fmt_help.setText(
            "Data profiles log every value: CSV with one row per scan for number tags (Excel, pandas, "
            "NX Viewer), one row per sample for waveforms - or raw .wpk packets for waveform devices "
            "(smallest, lossless). Statistic profiles log one row per interval with the chosen statistics "
            "per tag - small files for long-term trends and reports. All enabled profiles run together on "
            "Record and write into the same session folder; each can use its own tags. The layout is "
            "described in docs/recording-format.md.")
        self._fill_log_profiles()

    def _fill_log_profiles(self):
        tree = getattr(self, "lp_tree", None)
        if tree is None:
            return
        names = {(d.id, t.index): t.name for d in self.project.devices for t in d.tags}
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
            it = QTreeWidgetItem([pr["name"], "Statistic" if stat else "Data", pr["format"], chans, details])
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
        dlg = LogProfileDialog(self, pr, self.channel_choices(), taken, add_variable=self._new_variable_key)
        return dlg.result_profile() if dlg.exec() else None

    def _add_log_profile(self, kind: str):
        if not self._recording_guard():
            return
        base = "Data" if kind == "signal" else "Statistics"
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
        m.addAction("Add data profile...", lambda: self._add_log_profile("signal"))
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

    def _update_lock(self):
        rec = self.recorder is not None
        for w in (self.log_folder, self.p_name, self.p_operator, self.p_desc, *self.lp_btns):
            w.setEnabled(not rec)
        self.act_rec.setEnabled(not rec)
        self.act_stop.setEnabled(rec)
        if hasattr(self, "var_page"):                 # variables are locked while recording too
            self.var_page.refresh()
        if hasattr(self, "conn_page"):
            self.conn_page.update_states()

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
                                     "device with an enabled tag.", "warn",
                              "Open", lambda: self._show_doc("logging"))
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
        if self.ml_logger is not None:
            self.ml_logger.event("record_start", "Record", rec.dir)
        self._update_lock(); self._fill_log_profiles()
        self._refresh_sessions()
        self._log(f"Recording started -> {rec.dir}  ({len(profiles)} profile(s): " +
                  ", ".join(f"{p['name']} [{p['type']}, {p['format']}]" for p in profiles) + ")")
        self.set_status(f"Recording to {rec.dir}", "ok")
        if not self.drivers.any_running:
            self.banners.post("rec", "Recording, but the devices are not connected - nothing is being "
                                     "received.", "warn", "Connect", self._start_all)
        self._update_stats()

    def _stop_recording(self):
        rec, self.recorder = self.recorder, None
        if rec is None:
            return
        self.busy(True)
        with self._waiting():
            rec.stop()
        self.busy(False)
        if self.ml_logger is not None:
            self.ml_logger.event("record_stop", "Record", rec.dir)
        self._update_lock(); self._fill_log_profiles()
        dur = time.strftime("%H:%M:%S", time.gmtime(rec.elapsed))
        msg = (f"Saved {rec.packets:,} blocks / scans in {dur} ({_fmt_bytes(rec.disk_bytes())})")
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

    # ================================================================ AI/ML data log
    def _ml_folder(self) -> str:
        return self.project.ml_log.get("folder") or os.path.join(self.project.logging.folder, "AI_Data")

    def _ml_toggle(self, on: bool):
        self._ml_start() if on else self._ml_stop()

    def _ml_start(self):
        if self.ml_logger is not None:
            return
        topics = [{"id": d.id, "name": d.name, "topic": d.topic, "virtual": not d.wave,
                   "channels": [(t.index, t.name, t.unit) for t in d.tags if t.enabled]}
                  for d in self.project.devices if d.enabled]
        topics = [t for t in topics if t["channels"]]
        s = self.project.ml_log
        if not topics:
            self.banners.post("ml", "AI/ML data log: nothing to log - enable a device with at least one "
                                    "enabled tag.", "warn", "Open", lambda: self._show_doc("connectivity"))
            self._ml_update_ui()
            return
        try:
            lg = mlg.MlLogger(self._ml_folder(), self.project.to_dict(), topics, s["period_s"],
                              s["compression"], s["keep_days"])
            lg.start()
        except Exception as e:
            self.banners.post("ml", f"Could not start the AI/ML data log: {e}", "bad")
            self._ml_update_ui()
            return
        self.ml_logger = lg
        self.banners.dismiss("ml")
        self._log(f"AI/ML data log started -> {lg.root}  ({len(topics)} device(s), "
                  f"{fmt_interval(lg.period)} files, {lg.compression})")
        if lg.compression != s["compression"]:
            self._log("zstd is not available (needs Python 3.14+ or 'pip install zstandard') - using gzip")
        self._ml_update_ui()
        self._update_stats()

    def _ml_stop(self):
        lg, self.ml_logger = self.ml_logger, None
        if lg is not None:
            with self._waiting():
                lg.stop()                        # closes and compresses the open files
            self._log(f"AI/ML data log stopped. {lg.files} files, {_fmt_bytes(lg.disk_bytes)}, "
                      f"{lg.packets:,} blocks -> {lg.root}")
            if lg.error or lg.dropped:
                self.banners.post("ml", f"AI/ML data log finished with problems: {lg.dropped} blocks "
                                        f"dropped. {lg.error or ''}", "bad")
        self._ml_update_ui()

    def _ml_restart(self):
        """Project, devices or settings changed while logging: new files, new meta.json."""
        if self.ml_logger is not None:
            self._ml_stop()
            self._ml_start()

    def _ml_update_ui(self):
        on = self.ml_logger is not None
        self.act_ml.setChecked(on)
        self.act_ml.setIcon(make_icon("brain-circuit", T["ok" if on else "text"]))
        self.act_ml.setToolTip(("Stop" if on else "Start") + " the AI/ML data log  (" +
                               self.act_ml.shortcut().toString(QKeySequence.SequenceFormat.NativeText) + ")")
        self.act_marker.setEnabled(on)
        self.ml_label.setVisible(on)

    def _ml_settings(self):
        dlg = MlLogDialog(self, self.project.ml_log, os.path.join(self.project.logging.folder, "AI_Data"))
        if dlg.exec():
            self.project.ml_log = dlg.result_settings()
            self._mark_dirty()
            self._ml_restart()
            self.set_status("AI/ML data log settings saved" +
                            (" - logging restarted" if self.ml_logger else ""), "ok")

    def _ml_marker(self, kind: str):
        """Event marker in events.csv - the AI app turns these into draft labels."""
        if self.ml_logger is None:
            return
        t = time.time()                          # the moment of the click, not of the OK
        title = dict(MARKER_TYPES)[kind].split(" (")[0]
        text, ok = QInputDialog.getText(self, f"Marker - {title}", "Note (optional):")
        if not ok or self.ml_logger is None:
            return
        self.ml_logger.event(kind, self.project.operator or "operator", text.strip(), t)
        self._log(f"Marker {kind}: {text.strip()}")
        self.toast.popup(f"Marker saved: {title}")

    def _ml_open_folder(self):
        d = self._ml_folder()
        os.makedirs(d, exist_ok=True)
        self._open_path(d)

    # ================================================================ processing
    def _drain_and_draw(self):
        """GUI timer: drain the driver queue - scans, waveform packets, status, write results."""
        n, new_ch = 0, False
        q = self.drivers.q
        while n < 4000:
            try:
                msg = q.get_nowait()
            except queue.Empty:
                break
            n += 1
            kind, did = msg[0], msg[1]
            dev = self.project.device(did)
            if kind == "status":
                self._on_device_status(dev, msg[2], msg[3])
                continue
            if kind == "write":
                self._log(f"{dev.name if dev else did}: write {'OK' if msg[2] else 'FAILED'} - {msg[3]}")
                if not msg[2]:
                    self.toast.popup(f"Write failed: {msg[3]}")
                continue
            if dev is None or not dev.enabled:
                continue
            if kind == "scan":
                _, _, t, dt, idx, vals = msg
                if not idx:
                    continue
                seq = self._scan_seq[did] = self._scan_seq.get(did, -1) + 1
                raw = np.array([np.nan if v is None else v for v in vals], dtype=float)
                blk = ScanBlock(seq, t, dt, idx, raw)
                self._ingest(dev, t, b"", blk)
            elif kind == "wave":
                rx, buf = msg[2], msg[3]
                try:
                    blk = wp.decode(buf)
                except wp.PacketError as e:
                    st = self.streams.setdefault(did, TopicStream())
                    if "CRC" in str(e):
                        st.crc += 1
                    else:
                        st.bad += 1
                    continue
                self._ingest(dev, rx, buf, blk)
                for ch in blk.channels:                  # auto-add unseen packet channels
                    if dev.tag(ch) is None:
                        dev.tags.append(TagCfg(ch, f"CH{ch + 1}", kind="array"))
                        dev.tags.sort(key=lambda c: c.index)
                        new_ch = True
        if new_ch:
            self._mark_dirty()
            if self.recorder is None:
                self._structure_changed(); self.conn_page.refresh()
        self._draw()

    def _ingest(self, dev: DeviceCfg, rx: float, payload: bytes, blk):
        """One block of a device: scale, buffer, alarms, variables, statistics, files."""
        if self.t_ref is None:
            self.t_ref = blk.t0
        st = self.streams.setdefault(dev.id, TopicStream())
        for row, ch in enumerate(blk.channels):
            yr = blk.y[row]
            m = yr[np.isfinite(yr)]
            if m.size:
                st.raw_mean[ch] = float(m.mean())
        sblk = self._scaled(dev, blk)                    # engineering values (plots, files)
        st.feed(sblk, self.t_ref)
        t_abs = None
        for row, ch in enumerate(sblk.channels):         # alarms see every sample of the block
            self.alarm_engine.feed(dev.id, ch, rx, sblk.y[row])
            if self.stats.wants((dev.id, ch)):
                if t_abs is None:
                    t_abs = sblk.t0 + np.arange(sblk.n) * sblk.dt
                self.stats.feed((dev.id, ch), t_abs, sblk.y[row])
        self._compute_variables(dev.id, rx, sblk)
        if self.recorder is not None:
            self.recorder.put(dev.id, rx, payload, sblk)
        if self.ml_logger is not None:
            self.ml_logger.put(dev.id, rx, payload, sblk)

    def _on_device_status(self, dev, state: str, text: str):
        if dev is None:
            return
        self._log(f"{dev.name}: {state}" + (f" - {text}" if text else ""))
        if state == "error":
            self.banners.post(f"dev:{dev.id}", f"{dev.name}: {text}", "bad", "Device",
                              lambda: (self._show_doc("connectivity"), self.conn_page.refresh(("device", dev.id))))
        elif state == "online":
            self.banners.dismiss(f"dev:{dev.id}")
            self.set_status(f"{dev.name} online - {text}", "ok")
        self.conn_page.update_states()
        self._update_nav_dots()
        if self._prop_key == ("device", dev.id):
            self._refresh_properties()

    def _draw(self, force: bool = False):
        enabled = [(d, t) for d in self.project.devices if d.enabled for t in d.tags if t.enabled]
        has_data = any(self.streams.get(d.id) and self.streams[d.id].rings for d, _ in enabled)
        want = 0 if not enabled else (2 if has_data else 1)
        if self.screen_stack.currentIndex() != want:
            self.screen_stack.setCurrentIndex(want)
        if not has_data or (self.act_pause.isChecked() and not force):
            return
        win = self.window_s.value()
        t_end = max((r.ordered()[0][-1] for st in self.streams.values()
                     for r in st.rings.values() if r.count), default=0)
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
        """User zoomed / panned a Quick View plot: hold the range until Reset zoom."""
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
        per_dev = self.layout_mode.currentText() == "Per device"
        groups = []          # [(title, [(device, tag)])]
        hidden = {tuple(x) for x in self.project.quick_hidden}
        for d in self.project.devices:
            tags = [t for t in d.tags if t.enabled and (d.id, t.index) not in hidden] if d.enabled else []
            if not tags:
                continue
            if per_dev:
                groups.append((d.name, [(d, t) for t in tags]))
            else:
                groups += [(t.name, [(d, t)]) for t in tags]
        if not per_dev:   # prefix the device only where tag names clash
            names = [g[0] for g in groups]
            groups = [((f"{m[0][0].name}·{n}" if names.count(n) > 1 else n), m)
                      for n, m in groups]
        first = None
        cols = 2 if self._grid and len(groups) > 1 else 1
        for i, (title, members) in enumerate(groups):
            row = i // cols
            p = self.glw.addPlot(row=row, col=i % cols)
            self._style_plot(p)
            if per_dev:
                p.setTitle(title, color=T["text"], size="10pt")
                dv.add_legend(p)
                units = {t.unit for _, t in members if t.unit}
                if len(units) == 1:
                    p.setLabel("left", units.pop(), color=T["muted"])
            else:
                d, t = members[0]
                p.setLabel("left", f"{title}" + (f" ({t.unit})" if t.unit else ""), color=self.color_of(d.id, t.index))
            if first is None:
                first = p
            else:
                p.setXLink(first)
            if i >= len(groups) - cols:                 # x axis only on the last row
                p.setLabel("bottom", "Time since first value", "s", color=T["text"])
            else:
                p.hideAxis("bottom")
            fixed = [(t.y_min, t.y_max) for _, t in members if t.y_min is not None]
            if len(fixed) == len(members):               # every tag fixed -> union range
                self._fixed_y[id(p)] = (min(a for a, _ in fixed), max(b for _, b in fixed))
            for d, t in members:
                label_txt = t.name + (f" [{t.unit}]" if t.unit else "")
                self.curves[(d.id, t.index)] = p.plot(
                    pen=pg.mkPen(self.color_of(d.id, t.index), width=1.5), name=label_txt,
                    symbol="o" if t.kind == "number" and d.scan_ms >= 1000 else None, symbolSize=3,
                    symbolPen=None, symbolBrush=self.color_of(d.id, t.index))
            self.plots.append(p)
        self._zoomed = False
        self.zoom_btn.setVisible(False)
        self._apply_autoscale()
        for page in self.view_pages.values():          # names / colours / theme changed
            page.restyle()
        self._draw(force=True)

    def _fill_quick_menu(self):
        """Quick View > Tags: one check per enabled tag, plus show / hide all."""
        m = self.qv_menu
        m.clear()
        hidden = {tuple(x) for x in self.project.quick_hidden}
        m.addAction("Show all", lambda: self._set_quick_hidden([]))
        m.addAction("Hide all", lambda: self._set_quick_hidden(
            [[d.id, t.index] for d in self.project.devices for t in d.tags]))
        for d in self.project.devices:
            if not d.enabled:
                continue
            m.addSection(d.name)
            for t in d.tags:
                if not t.enabled:
                    continue
                a = m.addAction(swatch(self.color_of(d.id, t.index)), t.name + (f"  [{t.unit}]" if t.unit else ""))
                a.setCheckable(True); a.setChecked((d.id, t.index) not in hidden)
                a.toggled.connect(lambda on, k=(d.id, t.index): self._toggle_quick(k, on))

    def _toggle_quick(self, key, on: bool):
        hidden = [x for x in self.project.quick_hidden if tuple(x) != key]
        if not on:
            hidden.append(list(key))
        self._set_quick_hidden(hidden)

    def _set_quick_hidden(self, hidden: list):
        self.project.quick_hidden = hidden
        self._mark_dirty()
        QTimer.singleShot(0, self._rebuild_plots)

    def _apply_autoscale(self, *_):
        for p in self.plots:
            fy = getattr(self, "_fixed_y", {}).get(id(p))
            if fy:
                p.setYRange(*fy, padding=0.02)
            else:
                p.enableAutoRange(axis="y", enable=self.autoscale.isChecked())

    # ================================================================ stats (twice a second)
    def _update_stats(self):
        now = time.perf_counter()
        dt = now - getattr(self, "_stat_t", now - 0.5) or 0.5
        self._stat_t = now
        if self._stat_dirty:
            self._stat_require()
        rate = errs = lost = 0
        for st in self.streams.values():
            st.pps, st.win_pkts = st.win_pkts / dt, 0
            rate += st.pps * max(1, len(st.rings))
            lost += st.lost; errs += st.crc + st.bad
        for d in self.project.devices:
            drv = self.drivers.driver(d.id)
            if drv is not None:
                errs += drv.errors
        rec = self.recorder
        c = self.cards
        if rec:
            c["elapsed"].set_value(time.strftime("%H:%M:%S", time.gmtime(rec.elapsed)))
            c["size"].set_value(_fmt_bytes(rec.disk_bytes()))
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
            c["elapsed"].set_value("-")
            c["size"].set_value("-")
        lg = self.ml_logger
        if lg:
            self.ml_label.setText(f"AI log  {lg.files} file{'' if lg.files == 1 else 's'} · {_fmt_bytes(lg.disk_bytes)}")
            self.ml_label.setToolTip(f"AI/ML data log -> {lg.root}\n{lg.packets:,} blocks, "
                                     f"{lg.dropped} dropped, {lg.late} late" +
                                     (f"\nLast file: {lg.last_file}" if lg.last_file else ""))
            if lg.error:
                self.banners.post("ml", f"AI/ML data log write error: {lg.error}", "bad")
        c["rx"].set_value(f"{rate:,.0f}")
        c["lost"].set_value(f"{errs} / {lost}")
        states = [self.drivers.state(d.id)[0] for d in self.project.devices if self.drivers.running(d.id)]
        if not states:
            self.chip_conn.set("Stopped", "info")
        elif all(s == "online" for s in states):
            self.chip_conn.set(f"Online ×{len(states)}", "ok")
        elif any(s == "error" for s in states):
            self.chip_conn.set(f"{states.count('error')} error(s)", "bad")
        else:
            self.chip_conn.set("Connecting", "warn")
        n_tags = sum(1 for d in self.project.devices if d.enabled for t in d.tags if t.enabled)
        self.lbl_topics.setText(f"{len(states)}/{len(self.project.devices)} devices · {n_tags} tags")
        self.lbl_rx.setText(f"{rate:,.0f} values/s" + (f" · {self.drivers.q.qsize()} queued" if
                                                      self.drivers.q.qsize() > 1000 else ""))
        self.alarm_engine.tick(time.time())
        self._drain_notifier()
        self._update_alarm_indicator()
        self.alarm_page.update_live()
        self.var_page.update_live()
        self._update_nav_dots()
        page = self.doc_tabs.currentWidget()
        if isinstance(page, dv.ViewPage):
            page.update_values()
        elif page is self.conn_page:
            self.conn_page.update_live()
            self.conn_page.update_states()
        self._sync_comm()

    def _log(self, s: str):
        self.log.appendPlainText(time.strftime("%H:%M:%S  ") + s)

    # ================================================================ scaling
    def _rebuild_scalers(self):
        self._scalers = {}
        for d in self.project.devices:
            for t in d.tags:
                try:
                    f = cm.make_scaler(t.scale)
                except ValueError as e:
                    self._log(f"{d.name} · {t.name}: scaling ignored - {e}")
                    f = None
                if f is not None:
                    self._scalers[(d.id, t.index)] = f

    def _scaled(self, dev, blk):
        rows = [(r, self._scalers[(dev.id, ch)]) for r, ch in enumerate(blk.channels)
                if (dev.id, ch) in self._scalers]
        if not rows:
            return blk
        out = copy.copy(blk)
        out.y = blk.y.copy()
        for r, f in rows:
            try:
                out.y[r] = f(out.y[r])
            except Exception as e:                       # never let a formula stop acquisition
                out.y[r] = np.nan
                self._log(f"{dev.name} tag {blk.channels[r]}: formula error - {e}")
        return out

    def _stats_ring(self, st, idx: int, win: float, keys):
        ring = st.rings.get(idx)
        if ring is None or ring.count == 0:
            return None
        t, y = ring.ordered()
        i = np.searchsorted(t, t[-1] - win, side="left")
        return {an.STAT_LABEL[k]: v for k, v in an.stats(y[i:], an.sample_rate(t[i:]), keys).items()}

    # ================================================================ alarms (alarm_rules.py)
    def _alarm_describe(self, tid: str, idx: int):
        if vr.is_var(tid):
            v = self._var(tid)
            return (f"Variables · {v['name']}" if v else "(deleted variable)"), (v.get("unit", "") if v else "")
        d = self.project.device(tid)
        t = d.tag(idx) if d else None
        return (f"{d.name} · {t.name}" if t else "(deleted tag)"), (t.unit if t else "")

    def _alarms_reload(self):
        """Definitions / profiles / settings changed (or the project): engine, page, tree."""
        self.alarm_engine.set_alarms(self.project.alarms)
        if hasattr(self, "alarm_page"):
            self.alarm_page.refresh()
        self._update_alarm_indicator()

    def alarms_changed(self):
        self._mark_dirty()
        self._alarms_reload()
        self._rebuild_project_tree()

    def alarm_settings_changed(self):
        self._mark_dirty()

    def _alarm_action(self, what: str, channel=None):
        self._show_doc("alarms")
        if what == "add":
            QTimer.singleShot(0, lambda: self.alarm_page.add_alarm(channel))

    def add_alarm_for(self, key):
        self._alarm_action("add", tuple(key))

    def _on_alarm_event(self, ev: dict):
        unit = "s" if ev["type"] == "D" else ev["unit"]
        lvl, name = f"L{ev['level']}", f"{ev['name']} ({ev['tag']})"
        if self.ml_logger is not None:           # alarms are weak labels for the AI app
            self.ml_logger.event(f"alarm_{ev['event'].lower()}", f"{lvl} {ev['type']} {name}",
                                 f"{ap.fmt_val(ev['value'], unit)} (setpoint {ev['setpoint']:g})", ev.get("t"))
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
        else:
            self._log(f"Alarm OFF  {lvl} {ev['type']}: {name} = {ap.fmt_val(ev['value'], unit)}")
        a = self.alarm_engine.by_id.get(ev["alarm_id"])
        payload = None
        for p in self.project.alarm_profiles:
            if not (a and p["id"] in a.get("profiles", []) and ar.profile_wants(p, ev)):
                continue
            if p["kind"] == "popup":
                self.toast.popup(f"{'⚠ ' if ev['event'] == 'ON' else ''}{ev['event']} {lvl} · {ev['name']}"
                                 + f" = {ap.fmt_val(ev['value'], unit)}")
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
        act = self.alarm_engine.count()
        level = "bad" if act else ""
        if act == self._alarm_level:
            return
        self._alarm_level = act
        text = f"{act} active" if act else "No alarms"
        self.alarm_btn.setText(text)
        self.alarm_btn.setIcon(make_icon("bell", T[level or "muted"]))
        set_prop(self.alarm_btn, "level", level)
        self.act_alarms.setText(f"Alarms ({act})" if act else "Alarms")
        self.act_alarms.setIcon(make_icon("bell", T[level or "text"]))
        self._set_tip(self.act_alarms)

    # ================================================================ documents (closable tabs)
    def _add_doc_tab(self, key: str, index: int | None = None):
        w, title, short, icon = self.docs[key]
        if self.doc_tabs.indexOf(w) >= 0:
            return
        text = short if self._mode == "compact" else title
        i = self.doc_tabs.addTab(w, make_icon(icon, T["muted"]), text) if index is None \
            else self.doc_tabs.insertTab(index, w, make_icon(icon, T["muted"]), text)
        close = QToolButton(); close.setObjectName("tabClose"); close.setText("×")
        close.setToolTip("Close tab (reopen from the Project tree or the Project menu)")
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
        if hasattr(self, "act_connect"):
            self.act_connect.setText("")             # re-tint the Start / Stop icon
            self._sync_comm()

    @contextmanager
    def _waiting(self):
        """Wait cursor around anything that blocks the GUI (file I/O, start/stop)."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()

    # ================================================================ views (host API of views.py)
    def channel_choices(self):
        out = []
        for d in self.project.devices:
            for t in d.tags:
                out.append((d.id, t.index, f"{d.name} · {t.name}" + (f" [{t.unit}]" if t.unit else ""),
                            self.color_of(d.id, t.index), t.unit))
        for v in self.project.variables:             # calculated tags
            if v["enabled"]:
                u = v.get("unit", "")
                out.append((*vr.var_key(v["id"]), f"Variables · {v['name']}" + (f" [{u}]" if u else ""),
                            self.var_color(v), u))
        return out

    def series(self, tid: str, idx: int):
        st = self.streams.get(tid)
        ring = st.rings.get(idx) if st else None
        return ring.ordered() if ring is not None and ring.count else None

    def last_value(self, tid: str, idx: int):
        """(latest engineering value or None when missing / bad quality, unit)."""
        st = self.streams.get(tid)
        val = st.last_vals.get(idx) if st else None
        if val is not None and val != val:
            val = None
        if vr.is_var(tid):
            v = self._var(tid)
            return val, (v.get("unit", "") if v else "")
        t = self.project.tag(tid, idx)
        return val, (t.unit if t else "")

    def view_changed(self):
        self._mark_dirty()
        self._stat_dirty = True
        QTimer.singleShot(0, self._refresh_properties)

    def stat_values(self, tid: str, idx: int, win=None, keys=None):
        """{column label: value} of analysis.STATS. win: a tile / variable dict with a statistics
        window (buffered by StatHub), seconds, or None = the Quick View window (plot ring)."""
        st = self.streams.get(tid)
        if st is None or idx not in st.last_vals:
            return None
        keys = [k for k in (keys or an.STAT_KEYS) if k != "value"]
        spec = sb.spec_of(win) if isinstance(win, dict) else None
        if spec is not None:
            vals = self.stats.values((tid, idx), spec, keys) if keys else {}
            if vals is None:
                vals = {}
                if (( tid, idx), spec) not in self.stats.buffers:
                    self._stat_dirty = True              # a new window: collect it from now on
        else:
            secs = float(win) if isinstance(win, (int, float)) and win else self.window_s.value()
            vals = (self._stats_ring(st, idx, secs, keys) if keys else {}) or {}
        vals["Value"] = self.last_value(tid, idx)[0]
        return vals

    def _stat_require(self):
        """Collect every statistics window in use (views, variables) for the StatHub."""
        self._stat_dirty = False
        want = set()
        for vw in self.project.views:
            for t in vw.get("tiles", []):
                spec = sb.spec_of(t)
                if spec is None:
                    continue
                if t["kind"] in ("value", "gauge", "led") and t.get("quantity", "value") != "value" and t.get("channel"):
                    want.add((tuple(t["channel"]), spec))
                elif t["kind"] == "stats":
                    want |= {(tuple(k), spec) for k in t.get("channels", [])}
        for v in self.project.variables:
            spec = sb.spec_of(v)
            if v.get("kind") == "statistic" and v.get("enabled") and spec and v.get("source"):
                want.add((tuple(v["source"]), spec))
        self.stats.require(want)

    def _window_stat(self, key, spec, stat: str):
        if (tuple(key), spec) not in self.stats.buffers:
            self._stat_dirty = True
            return None
        vals = self.stats.values(tuple(key), spec, [stat]) or {}
        return vals.get(an.STAT_LABEL.get(stat, stat))

    def tag_rows(self, keys, decimals: int = 3) -> list[dict]:
        """Rows of a tag table component."""
        out = []
        names = {(tid, idx): lbl for tid, idx, lbl, _, _ in self.channel_choices()}
        for key in keys:
            tid, idx = key
            lbl = names.get((tid, idx), "(deleted)")
            dev, _, name = lbl.partition(" · ")
            if vr.is_var(tid):
                val, unit = self.last_value(tid, idx)
                out.append({"Tag": name.split(" [")[0], "Value": "-" if val is None else f"{val:.{decimals}f}",
                            "Unit": unit, "Quality": "Good" if val is not None else "No data", "Time": "",
                            "Device": "Variables", "Description": "", "_good": val is not None})
                continue
            t = self.project.tag(tid, idx)
            text, qual, good, stamp = self.tag_live(tid, idx)
            val = self.last_value(tid, idx)[0]
            out.append({"Tag": t.name if t else name, "Value": "-" if val is None else f"{val:.{decimals}f}",
                        "Unit": t.unit if t else "", "Quality": qual or "No data", "Time": stamp,
                        "Device": dev, "Description": t.description if t else "", "_good": good is not False})
        return out

    def _history(self, key, win: float):
        """(t, y) of the newest `win` seconds of a tag / variable, or None."""
        s = self.series(*key)
        if s is None:
            return None
        t, y = s
        i = np.searchsorted(t, t[-1] - win, side="left") if win else 0
        return t[i:], y[i:]

    def pick_keys(self, cfg: dict, field: str, multi: bool, page):
        """Properties panel: choose the tag(s) of a component."""
        chosen = cfg.get(field) or []
        dlg = PickDialog(self, self.channel_choices(), chosen if multi else [chosen], multi,
                         "Choose tags" if multi else "Choose a tag")
        if dlg.exec():
            keys = dlg.result_keys()
            if keys is None:
                return
            if cfg.get("kind") == "plot" and cfg.get("plot") == "stft" and multi:
                keys = keys[:1]
            cfg[field] = keys
            page.tile_changed(cfg)
            self.show_properties(props.tile_target(self, page, cfg))

    def view_props_changed(self, page, key: str):
        if key == "name":
            key_ = f"view:{page.view['id']}"
            self.docs[key_] = (page, page.name, page.name, "layout")
            i = self.doc_tabs.indexOf(page)
            if i >= 0:
                self.doc_tabs.setTabText(i, page.name)
            self._rebuild_project_tree()
        page.apply_view()
        self._mark_dirty()

    # ================================================================ variables (core/variables.py)
    def _var(self, tid: str) -> dict | None:
        vid = tid[len(vr.VAR_PREFIX):]
        return next((v for v in self.project.variables if v["id"] == vid), None)

    def var_color(self, v: dict) -> str:
        if v.get("color"):
            return v["color"]
        i = next((k for k, x in enumerate(self.project.variables) if x["id"] == v["id"]), 0)
        return COLORS[(i + 4) % len(COLORS)]

    def _channel_ids(self) -> dict:
        """Formula names of the tags: <device>_<tag>."""
        return {cm.identifier(f"{d.name}_{t.name}"): (d.id, t.index)
                for d in self.project.devices for t in d.tags}

    def _variables_reload(self):
        """Definitions or tag names changed: recompile, regroup for logging."""
        eng = self.variable_engine
        eng.set_variables(self.project.variables)
        groups: dict[str, list[str]] = {}
        for v in eng.order:
            rate = "s" if eng.is_scalar(v) else "w"
            groups.setdefault(f"vars:{eng.base[v['id']]}:{rate}", []).append(v["id"])
        self._var_groups = groups
        for msg in {f"Variable '{v['name']}': {eng.errors[v['id']]}" for v in self.project.variables
                    if v["enabled"] and v["id"] in eng.errors}:
            self._log(msg)

    def variables_changed(self):
        self._mark_dirty()
        for tid in [k for k in self.streams if vr.is_var(k)]:   # new definitions: fresh buffers
            del self.streams[tid]
        self.variable_engine.reset()
        self._variables_reload()
        self.var_page.refresh()
        self._rebuild_project_tree()
        self._alarms_reload()
        self._stat_dirty = True
        for page in self.view_pages.values():
            page.restyle()

    def _compute_variables(self, tid: str, rx: float, sblk):
        out = self.variable_engine.compute(tid, sblk)
        if not out:
            return
        rows = {}
        for v, vb in out:
            key = vr.var_key(v["id"])
            self.streams.setdefault(key[0], TopicStream()).feed(vb, self.t_ref)
            self.alarm_engine.feed(key[0], 0, rx, vb.y[0])
            if self.stats.wants(key):
                self.stats.feed(key, vb.t0 + np.arange(vb.n) * vb.dt, vb.y[0])
            rows[v["id"]] = vb
        if self.recorder is None:
            return
        for gid, vids in self._var_groups.items():
            if not gid.startswith(f"vars:{tid}:"):
                continue
            first = next((rows[x] for x in vids if x in rows), None)
            if first is None:
                continue
            y = np.vstack([rows[x].y[0] if x in rows and rows[x].n == first.n else np.full(first.n, np.nan)
                           for x in vids])
            self.recorder.put(gid, rx, b"", vr.VarBlock(first.seq, first.sample_idx, first.t0, first.dt, y,
                                                        channels=list(range(len(vids)))))

    def _new_variable_key(self):
        """Add a variable from another dialog -> (its key, new choices) or None."""
        before = {v["id"] for v in self.project.variables}
        self.var_page.add_variable("formula")
        new = [v for v in self.project.variables if v["id"] not in before]
        return (vr.var_key(new[0]["id"]), self.channel_choices()) if new else None

    def _var_action(self, what: str, source=None):
        self._show_doc("variables")
        if what == "add":
            QTimer.singleShot(0, lambda: self.var_page.add_variable("formula" if source is None else "statistic",
                                                                    source))

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
                            "Setpoint": ap.fmt_val(ev["setpoint"], unit),
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
                        "State": "ALARM",
                        "Alarm": r["name"], "Tag": r["tag"], "Type": r["type"],
                        "Value": ap.fmt_val(r["value"], unit), "Setpoint": ap.fmt_val(r["setpoint"], unit),
                        "_level": r["level"]})
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
        self._stat_dirty = True
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
        vw = vmod.new_view(name.strip())
        self.project.views.append(vw)
        page = self._open_view_page(vw, editing=True)
        self._mark_dirty()
        self._rebuild_project_tree()
        self.show_properties(props.view_target(self, page))
        self.toast.popup("Edit mode - select a cell and insert a component")

    def view_properties(self, page):
        if page is None:
            return
        taken = {v["name"] for v in self.project.views if v["id"] != page.view["id"]}
        dlg = ViewDialog(self, page.view, taken)
        if not dlg.exec():
            return
        dlg.apply(page.view)
        self.view_props_changed(page, "name")
        self._refresh_properties()

    def delete_view(self, page):
        if page is None:
            return
        if QMessageBox.question(self, "Delete view", f"Delete view '{page.name}'?") \
                != QMessageBox.StandardButton.Yes:
            return
        vid = page.view["id"]
        self.project.views = [v for v in self.project.views if v["id"] != vid]
        key = f"view:{vid}"
        self._close_doc(page)
        self.docs.pop(key, None)
        self.view_pages.pop(vid, None)
        page.deleteLater()
        self._mark_dirty()
        self._stat_dirty = True
        self._rebuild_project_tree()
        self.show_properties(None)

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
            if isinstance(btn, QToolButton) and a not in (self.act_left, self.act_panel, self.act_log):
                text = mode == "wide" or (mode == "medium" and a in keep)
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text
                                       else Qt.ToolButtonStyle.ToolButtonIconOnly)

        short = mode == "compact"
        for key, (w, full, abbr, _) in self.docs.items():
            i = self.doc_tabs.indexOf(w)
            if i >= 0:
                self.doc_tabs.setTabText(i, abbr if short else full)

        # Properties panel: auto-hide when narrow, bring back when there is room again
        if short:
            if self.right_dock.isVisible() and not self.right_dock.isFloating():
                self.right_dock.hide()
                self._auto_hidden_right = True
                if prev is not None:
                    self.toast.popup("Properties panel hidden - Ctrl+Shift+P shows it")
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
        if self.recorder is not None or self.ml_logger is not None:
            run = [x for x, on in (("Recording", self.recorder), ("the AI/ML data log", self.ml_logger))
                   if on is not None]
            msg = " and ".join(run) + (" are" if len(run) > 1 else " is") + " running. Stop and exit?"
            if QMessageBox.question(self, APP_NAME, msg[0].upper() + msg[1:]) \
                    != QMessageBox.StandardButton.Yes:
                e.ignore(); return
            self._stop_recording()
        if not self._confirm_discard():
            e.ignore(); return
        self._ml_stop()
        self.drivers.stop_all()
        super().closeEvent(e)


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
