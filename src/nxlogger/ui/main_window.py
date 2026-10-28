"""
main_window.py - NX Logger main window: devices + tags, live views, alarms, recording.

NX Logger (tag-based data logger, Yokogawa GA10 / Genesis64 / FUXA style):
  * Connectivity document: the device table (Simulation, Modbus TCP, Modbus RTU, MQTT, OPC UA
    client; OPC DA planned) - add / edit / enable / import / export (JSON, CSV). Tags document:
    devices with groups and tags. Each device runs in its own driver thread
    (comms/drivers); number tags are read every scan interval, MQTT devices in wavepacket
    mode stream ESP32 waveforms as array tags. Start / stop per device or all at once.
  * Tags have every channel property (unit, colour, scaling, decimals, Y range) and are
    used everywhere a channel was: alarms, variables, views, logging, AI/ML log.
  * Views are free-form canvases with a snap grid holding components (header, trend, FFT,
    number, gauge, LED, tag / statistics / alarm table). Statistics windows are seconds,
    minutes, hours or samples, buffered by the app (core/stat_buffer.py).
  * Right: Properties panel - whatever is selected (device, tag, group, view,
    component ...) is shown there, like the Genesis64 / FUXA property inspectors (devices
    read-only: Edit... opens their settings pop-up). The Runtime dock under it shows
    recording / communication state.
  * Recording: CSV (and raw .wpk for waveform devices); session folders keep project.json
    (docs/recording-format.md - read by the NX Viewer). TDMS is no longer written.
  * Project files: .nxl (JSON); .sxl and .mqdaq open and are converted (topics -> MQTT
    wavepacket devices).

Layout:
  * Left:   "Project" dock (navigator tree) and "Data" dock (recorded sessions)
  * Centre: closable document tabs - Quick View, Connectivity, Tags, Logging, Alarms,
            Variables, Views
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
import threading
import os
import queue
import shutil
import time
import uuid
from contextlib import contextmanager

import numpy as np
from PySide6.QtCore import QEvent, QObject, QSettings, QSize, QTimer, QUrl, Qt
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDockWidget,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
                               QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
import pyqtgraph as pg

from nxlogger import APP_NAME, __version__, config, paths
from nxlogger.comms.manager import DriverManager
from nxlogger.core import aggregation as ag
from nxlogger.core import alarm_rules as ar
from nxlogger.core import analysis as an
from nxlogger.core import channel_math as cm
from nxlogger.core import ml_log as mlg
from nxlogger.core import recording as dl
from nxlogger.core import stat_buffer as sb
from nxlogger.core import system_tags as ss
from nxlogger.core import variables as vr
from nxlogger.core import virtual_tags as vt
from nxlogger.core import view_model as vmod
from nxlogger.core import wavepacket as wp
from nxlogger.core.project import (OLD_PROJECT_EXTS, PROJECT_EXT, Project, fmt_interval, new_log_profile)
from nxlogger.core.ring import MAX_PLOT_S, TopicStream
from nxlogger.core.tags import (COLORS, CONN_CATEGORIES, DRIVERS, PLANNED, DeviceCfg, ScanBlock, TagCfg, describe_address,
                                DEVICE_COLS, devices_to_json,
                                devices_to_rows, driver_category, driver_label, new_device, new_tag, secret_keys,
                                tags_to_rows)
from nxlogger.ui.dialogs.about_dialog import show_about
from nxlogger.ui.dialogs.add_tags_dialog import AddTagsDialog
from nxlogger.ui.dialogs.aggregation_dialog import AggregationDialog
from nxlogger.ui.dialogs.alarm_dialog import AlarmDialog
from nxlogger.ui.dialogs.alarm_profile_dialog import AlarmProfileDialog
from nxlogger.ui.dialogs.device_dialog import DeviceDialog
from nxlogger.ui.dialogs.group_dialog import GroupDialog
from nxlogger.ui.dialogs.export_devices_dialog import ExportDevicesDialog
from nxlogger.ui.dialogs.export_tags_dialog import ExportTagsDialog
from nxlogger.ui.dialogs.import_devices_dialog import ImportDevicesDialog
from nxlogger.ui.dialogs.import_tags_dialog import ImportTagsDialog
from nxlogger.ui.dialogs.common import ask_unsaved, confirm, popup_form
from nxlogger.ui.dialogs.log_profile_dialog import LogProfileDialog
from nxlogger.ui.dialogs.marker_dialog import MarkerDialog
from nxlogger.ui.dialogs.ml_log_dialog import MlLogDialog
from nxlogger.ui.dialogs.new_view_dialog import NewViewDialog
from nxlogger.ui.dialogs.opcua_browse_dialog import OpcUaBrowseDialog
from nxlogger.ui.dialogs.pick_dialog import PickDialog
from nxlogger.ui.dialogs.tag_dialog import TagDialog
from nxlogger.ui.dialogs.tile_dialog import TileDialog
from nxlogger.ui.dialogs.variable_dialog import VariableDialog
from nxlogger.ui.dialogs.virtual_tag_dialog import VirtualTagDialog
from nxlogger.ui.dialogs.view_dialog import ViewDialog
from nxlogger.ui.dialogs.write_value_dialog import WriteValueDialog
from nxlogger.ui.icons import check_icon_file, grip_icon_file, level_icon, swatch
from nxlogger.ui.theme import ui_kit as ui
from nxlogger.ui.theme.app_style import DENSE_QSS, EXTRA_QSS, blend, fusion_palette, fusion_qss
from nxlogger.ui.theme.ui_kit import (AppWindow, Card, Chip, EmptyState, T, fixed_width, label, make_icon, refit,
                                      set_prop)
from nxlogger.ui.widgets import alarm_panel as ap
from nxlogger.ui.widgets import properties as props
from nxlogger.ui.widgets import views as dv
from nxlogger.ui.widgets.connectivity import STATE_TOKEN, ConnectivityPage
from nxlogger.ui.widgets.form_tab import FormTab
from nxlogger.ui.widgets.property_grid import PropertiesPanel
from nxlogger.ui.widgets.tags_page import CATEGORIES as TAG_CATEGORIES, TagsPage
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
HEADER_NODES = ("project", "doc", "conncat", "tagsroot", "alarms", "vars", "views")   # Project tree sections: no right-click menu


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
        self._t_started = time.time()
        self.sys_values: dict[str, object] = {}        # System Tags: tag id -> value (text tags too)
        self._sys_n = 0                                # sample counter of the System Tag blocks
        self._sys_t = time.time()                      # time of the last System Tag values
        self._sys_tags: dict[str, ss.SysTag] = {}
        self._loading = False
        self.plots: list[pg.PlotItem] = []
        self.curves: dict[tuple[str, int], pg.PlotDataItem] = {}
        self._mode: str | None = None          # compact | medium | wide
        self._dense = self._screen_is_small()  # read by apply_theme() during super().__init__
        self._auto_hidden_right = self._auto_hidden_rt = False   # hidden by a narrow window (Properties, Runtime)
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
        self.vt_engine = vt.VirtualEngine()          # Tags > Virtual (core/virtual_tags.py)
        self.vt_engine.names = self._vt_names
        self.vt_engine.latest = self._vt_latest
        self._vt_seq: dict[str, int] = {}
        self.agg_engine = ag.AggregationEngine()     # Tags > Aggregation (core/aggregation.py)
        self.agg_engine.names = self._vt_names
        self.agg_engine.latest = self._agg_latest
        self._agg_seq: dict[str, int] = {}
        self._var_groups: dict[str, list[str]] = {}   # recorder group id -> variable ids (rows)
        self._noflash = _NoFlash()                    # no parent: created before the Qt base
        self._alarm_level = None               # last indicator state (repaint only on change)
        self._prop_key: tuple | None = None    # what the Properties panel shows
        self._stat_dirty = True                # statistics windows to (re)collect
        # menus are text only on every platform (macOS hides menu icons natively; Windows matches)
        QApplication.instance().setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, True)
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
        # text that changes at run time: fixed widths, so the toolbar / status bar never move
        fixed_width(self.alarm_btn, "No alarms", "99 active")
        fixed_width(self.toolbar.widgetForAction(self.act_connect), "Connect", "Disconnect")
        fixed_width(self.toolbar.widgetForAction(self.act_alarms), "Alarms", "Alarms (99)")
        fixed_width(self.rec_label, "Ready", "● REC  00:00:00")
        self.ml_label = QLabel(); self.ml_label.setObjectName("statusMsg")
        set_prop(self.ml_label, "level", "ok")
        self.ml_label.hide()
        self.statusBar().insertPermanentWidget(1, self.ml_label)
        self.theme_listeners.append(self._ml_update_ui)              # running icon colour
        self._ml_update_ui()
        self.theme_listeners.append(lambda: (setattr(self, "_alarm_level", None),
                                             self._update_alarm_indicator(), self.alarm_page.refresh(),
                                             self.var_page.refresh(), self.tags_page.refresh(),
                                             self.conn_page.refresh(),
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
        self._agg_saver = QTimer(self, interval=30_000, timeout=self._agg_save)   # running values (retain)
        self._agg_saver.start()
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
        # right side (Properties + Runtime) as one panel, like the left side
        self.act_panel = self.act("Properties panel", self._toggle_right_docks, "Ctrl+Shift+P", None)
        self.act_panel.setCheckable(True)
        for d in (self.right_dock, self.runtime_dock):
            d.toggleViewAction().toggled.connect(lambda _: self.act_panel.setChecked(
                not (self.right_dock.isHidden() and self.runtime_dock.isHidden())))
        self.act_panel.setChecked(True)
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

    def _toggle_right_docks(self):
        """Hide / show the right side (Properties + Runtime) as one panel."""
        self._auto_hidden_right = self._auto_hidden_rt = False
        show = self.right_dock.isHidden()        # Properties decides: both follow it
        for d in (self.right_dock, self.runtime_dock):
            d.setVisible(show)
        self.act_panel.setChecked(show)

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
        add_dev = QMenu("Add device", self)               # menus are text only (no icon column)
        for k, d in DRIVERS.items():
            add_dev.addAction(d["label"] + "...", lambda kk=k: self.add_device(kk))
        for a in (self.act("Quick View", lambda: self._show_doc("screen"), "Ctrl+1", "chart"),
                  self.act("Connectivity", lambda: self._show_doc("connectivity"), "Ctrl+2", "network"),
                  self.act("Tags", lambda: self._show_doc("tags"), "Ctrl+6", "tag"),
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
        for a in (self.data_dock.toggleViewAction(), self.log_dock.toggleViewAction(),
                  self.runtime_dock.toggleViewAction()):
            v.insertAction(first, a)
        v.insertSeparator(first)
        for a in (self.act_ml, self.act_marker, self.act_ml_settings,
                  self.act("Open AI/ML data folder", self._ml_open_folder, None, "folder-open")):
            v.insertAction(first, a)
        acq = mb.addMenu("&Acquisition")
        for a in (self.act_connect, None, self.act_rec, self.act_stop,
                  None, self.act_ml, self.act_marker, self.act_ml_settings):
            acq.addSeparator() if a is None else acq.addAction(a)
        self.addAction(self.act("Close tab", self._close_current_tab, "Ctrl+W"))
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
            add.addAction(d["label"] + "...", lambda kk=k: self.add_device(kk))
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
        bar = self.doc_tabs.tabBar()
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._tab_menu)
        bar.installEventFilter(self)                  # middle click closes a tab
        for key, w, title, short, icon in (
                ("screen", self._build_screen(), "Quick View", "Quick", "chart"),
                ("connectivity", self._build_connectivity(), "Connectivity", "Devices", "network"),
                ("tags", self._build_tags(), "Tags", "Tags", "tag"),
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

    def _build_tags(self):
        self.tags_page = TagsPage(self)
        return self.tags_page

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

    # ---------------------------------------------------------------- right docks: Properties, Runtime
    def build_docks(self):
        super().build_docks()
        # Runtime: its own dock, stacked under Properties (move / close / tabify like any dock)
        self.runtime_dock = QDockWidget("Runtime", self)
        self.runtime_dock.setObjectName("dockRuntime")
        self.runtime_dock.setWidget(self._runtime)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.runtime_dock)
        self.splitDockWidget(self.right_dock, self.runtime_dock, Qt.Orientation.Vertical)
        self.resizeDocks([self.right_dock, self.runtime_dock], [560, 240], Qt.Orientation.Vertical)

    def build_right(self):
        self._build_side_docks()
        rt = QFrame(); rt.setObjectName("runtimePanel")
        v = QVBoxLayout(rt); v.setContentsMargins(10, 8, 10, 8); v.setSpacing(6)
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
        v.addStretch(1)
        self._runtime = rt                       # docked in build_docks (after the Properties dock)
        self.props = PropertiesPanel(self.reg_icon, on_refresh=self._refresh_properties,
                                     empty_hint=self._empty_hint)
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
            elif kind == "sys":
                t = props.system_target(self, ref[1])
            elif kind == "vt":
                t = props.virtual_target(self, ref[1])
            elif kind == "agg":
                t = props.aggregation_target(self, ref[1])
            elif kind == "project":
                t = props.project_target(self)
        self.show_properties(t)

    def _empty_hint(self) -> tuple[str, str]:
        """Properties with nothing selected: say what to select on the tab in front."""
        page = self.doc_tabs.currentWidget() if hasattr(self, "doc_tabs") else None
        if page is getattr(self, "conn_page", None):
            return self.conn_page.empty_hint()          # in the category's words (device / database / web service)
        if page is getattr(self, "tags_page", None):
            return "No tag selected", "Select a device, group or tag to view its settings"
        if isinstance(page, dv.ViewPage):
            return "No component selected", "Select a component to view its settings"
        return "No selection", "Select an object to view its settings"

    def show_properties(self, t):
        self._prop_key = t.key if t is not None else None
        self.props.show_target(t)

    def _refresh_properties(self):
        """The model changed under the panel: rebuild what it shows (same object)."""
        k = self._prop_key
        if not k:
            return
        if k[0] in ("device", "group", "tag", "tags", "project", "vt", "agg"):
            ref = k if k[0] != "tags" else ("tags", k[1], list(k[2]))
            self.select_object(ref)
        elif k[0] in ("view", "tile"):
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
        elif page in (self.conn_page, self.tags_page):
            page.activate()                      # its selected row and properties come back
        self._draw(force=True)
        if page is self.tags_page:
            self.tags_page.update_live()
        elif page is self.conn_page:
            self.conn_page.update_states()

    # ================================================================ project
    def _set_project(self, p: Project, path: str | None):
        self.drivers.stop_all()
        if hasattr(self, "doc_tabs"):
            self._close_editors()                # editors of the old project
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
        self.vt_engine.reset(); self._vt_seq.clear()
        self.agg_engine.reset(); self._agg_seq.clear()
        self.dirty = False
        self.alarm_engine.states.clear()
        self.alarm_engine.reset_data_loss()
        self.alarm_engine.clear_log()
        self._structure_changed()
        self.agg_engine.restore(self._agg_load())    # running totals / counters kept from last time
        self._update_title()
        self._refresh_sessions()
        self._load_views()
        self.var_page.refresh()
        self.tags_page.refresh(())
        self.conn_page.refresh([])
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
        open_ = [w for w, *_ in self.docs.values() if isinstance(w, FormTab) and w.modified]
        if open_:                                # unapplied editor tabs first
            self.show_editor(open_[0])
            r = ask_unsaved(self, APP_NAME, f"{len(open_)} editor(s) have changes that are not applied: "
                            + ", ".join(w.title for w in open_[:4]) + ("..." if len(open_) > 4 else ""),
                            keep="Apply")
            if r == "cancel":
                return False
            if r == "keep" and not all(w.apply() for w in open_):
                return False
            for w in open_:
                w.modified = False
        if not self.dirty:
            return True
        r = ask_unsaved(self, APP_NAME, f"Save changes to '{self.project.name}'?")
        if r == "keep":
            return self._save_project()
        return r == "discard"

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

    # ================================================================ editor tabs (Genesis64 style)
    def open_editor(self, what: str, ident=None, /, **ctx):
        """Open (or focus) the editor tab of a device, tag, group, alarm, notification profile,
        variable or logging profile. ident None = a new item (a draft until the first Apply).
        ctx: what a new item starts from (driver, did, group, kind, channel ...)."""
        spec = getattr(self, f"_spec_{what}")(ident, **ctx)
        if spec is None:
            return None
        key = spec["key"]
        old = self.docs.get(key, (None,))[0]
        if isinstance(old, FormTab):
            self._show_doc(key)
            return old
        tab = FormTab(self, spec)
        spec["tab"] = tab
        tab.changed.connect(lambda t=tab: self._editor_changed(t))
        self.docs[key] = (tab, spec["title"], spec["title"], spec["icon"])
        self._show_doc(key)
        self._editor_changed(tab)
        return tab

    def _editor_changed(self, tab):
        """Modified flag, or a new item got its real key / name: tab text, docs key."""
        old = next((k for k, v in self.docs.items() if v[0] is tab), None)
        if old is not None and old != tab.key:
            self.docs[tab.key] = self.docs.pop(old)
        self.docs[tab.key] = (tab, tab.title, tab.title, tab.spec["icon"])
        i = self.doc_tabs.indexOf(tab)
        if i >= 0:
            self.doc_tabs.setTabText(i, tab.title + (" *" if tab.modified else ""))
            self.doc_tabs.setTabIcon(i, tab.tab_icon())
            self.doc_tabs.setTabToolTip(i, tab.spec["path"])

    def close_editor(self, tab, force: bool = False) -> bool:
        if not force and not tab.can_close():
            return False
        key = next((k for k, v in self.docs.items() if v[0] is tab), None)
        i = self.doc_tabs.indexOf(tab)
        if i >= 0:
            self.doc_tabs.removeTab(i)
        if key:
            self.docs.pop(key, None)
        tab.deleteLater()
        return True

    def show_editor(self, tab):
        if self.doc_tabs.indexOf(tab) >= 0:
            self.doc_tabs.setCurrentWidget(tab)

    def _close_tab_widget(self, w) -> bool:
        """Close one document tab: editors may ask (unapplied changes), pages just hide."""
        if isinstance(w, FormTab):
            return self.close_editor(w)
        self._close_doc(w)
        return True

    def _close_current_tab(self):
        w = self.doc_tabs.currentWidget()
        if w is not None:
            self._close_tab_widget(w)

    def _tab_menu(self, pos):
        bar = self.doc_tabs.tabBar()
        i = bar.tabAt(pos)
        if i < 0:
            return
        w = self.doc_tabs.widget(i)
        tabs = [self.doc_tabs.widget(k) for k in range(self.doc_tabs.count())]
        editors = [x for x in tabs if isinstance(x, FormTab)]
        m = QMenu(self)
        m.addAction("Close", lambda: self._close_tab_widget(w)).setShortcut(QKeySequence("Ctrl+W"))
        m.addAction("Close others", lambda: [self._close_tab_widget(x) for x in tabs if x is not w])
        m.addAction("Close tabs to the right", lambda: [self._close_tab_widget(x) for x in tabs[i + 1:]])
        m.addSeparator()
        a = m.addAction(f"Close all editors ({len(editors)})",
                        lambda: [self._close_tab_widget(x) for x in editors])
        a.setEnabled(bool(editors))
        if isinstance(w, FormTab):
            m.addSeparator()
            m.addAction("Apply", w.apply).setEnabled(w.modified or bool(w.spec.get("is_new")))
        m.exec(bar.mapToGlobal(pos))

    def eventFilter(self, obj, ev):
        if obj is self.doc_tabs.tabBar() and ev.type() == QEvent.Type.MouseButtonRelease \
                and ev.button() == Qt.MouseButton.MiddleButton:
            i = obj.tabAt(ev.position().toPoint())
            if i >= 0:
                self._close_tab_widget(self.doc_tabs.widget(i))
                return True
        return super().eventFilter(obj, ev)

    def _close_editors(self, prefix: str = "edit:"):
        """Model gone (project changed, item deleted): close its editors without asking."""
        for k in [k for k, v in self.docs.items() if k.startswith(prefix) and isinstance(v[0], FormTab)]:
            self.close_editor(self.docs[k][0], force=True)

    def _locked_reason(self) -> str:
        return "Stop recording to change the project." if self.recorder is not None else ""

    def _delete_action(self, spec, fn, exists):
        """Delete button of an editor: ask (fn does), then close the tab if the item is gone."""
        def run():
            if spec.get("is_new"):
                self.close_editor(spec["tab"], force=True)
                return
            fn()
            if not exists():
                self.close_editor(spec["tab"], force=True)
        return ("Delete", "trash", run)

    # ---- devices
    def _device_popup(self, did, driver: str | None = None):
        """Device settings pop-up (Add / Edit / double-click): the device joins the project or
        changes only on Add device / Save."""
        new = did is None
        if new:
            if not self._recording_guard():
                return
            dev = new_device(driver or "sim", self._unique_name(driver_label(driver or "sim").split(" (")[0],
                                                                  {d.name for d in self.project.devices}))
            if dev.driver == "mqtt":                 # start from the broker used so far
                dev.settings.update({k: v for k, v in self._legacy_broker().items() if v not in ("", None)})
        else:
            dev = self.project.device(did)
            if dev is None:
                return
        dlg = DeviceDialog(self, dev, {d.name for d in self.project.devices if d is not dev}, new=new,
                           locked="Stop recording to change the device." if self.recording else "")
        if not dlg.exec() or self.recording:
            return
        if new:
            driver0 = dev.driver
            dlg.apply(dev)
            if dev.driver != driver0 and dev.driver != "sim":
                dev.tags = []                        # the simulator's starter tags don't fit
            if dev.wave and not dev.tags:
                dev.tags = [TagCfg(i, f"CH{i + 1}", kind="array") for i in range(4)]
            self.project.devices.append(dev)
            self._mark_dirty()
            self._structure_changed()
            self.tags_page.refresh(("device", dev.id))
            self.conn_page.select(dev.id)
            if self.drivers.any_running and dev.enabled and dev.driver not in PLANNED:
                self.drivers.start(dev)
        else:
            was_wave = dev.wave
            dlg.apply(dev)
            self.device_changed(dev.id, structure=was_wave != dev.wave)
        self.set_status(f"Device '{dev.name}' " + ("added" if new else "saved"), "ok")

    # ---- tags and groups: pop-ups like the device settings (the Tags page works like Connectivity)
    def _tag_popup(self, did: str, idx: int | None = None, group: str = ""):
        """Add tag / Edit tag pop-up: the tag joins the project or changes only on Add tag / Save."""
        dev = self.project.device(did)
        if dev is None:
            return
        new = idx is None
        if new:
            if not self._recording_guard():
                return
            if dev.wave and len(dev.tags) >= wp.MAX_CH:
                self.toast.popup("A waveform packet has at most 8 channels")
                return
            t = new_tag(dev, "", group)
            t.name = self._unique_name(t.name, {x.name for x in dev.tags})
        else:
            t = dev.tag(idx)
            if t is None:
                return
        st = self.streams.get(dev.id)
        k = sum(len(d.tags) for d in self.project.devices[:self.project.devices.index(dev)]) + \
            (dev.tags.index(t) if t in dev.tags else len(dev.tags))
        dlg = TagDialog(self, dev, t, COLORS[k % len(COLORS)], lambda: st.raw_mean.get(t.index) if st else None,
                        self.project.all_groups())
        dlg.setWindowTitle("Add tag" if new else f"Tag - {t.name}")
        where = " · ".join(x for x in (dev.name, driver_label(dev.driver), t.group) if x)
        dlg.ok_btn, title = popup_form(dlg, "tag", "New tag" if new else t.name,
                                       f"{where} · {'array (waveform channel)' if t.kind == 'array' else 'tag'}",
                                       "Add tag" if new else "Save")
        dlg.name.textChanged.connect(lambda s_: title.setText(s_.strip() or ("New tag" if new else t.name)))
        dlg._preview()
        if self.recording:                           # recording: look, don't change
            dlg.ok_btn.setEnabled(False)
            dlg._timer.stop()
            set_prop(dlg.err, "level", "bad"); dlg.err.setText("Stop recording to change tags.")
        if not dlg.exec() or self.recording:
            return
        if new:
            if dev.tag(t.index) is not None:         # the index was taken meanwhile
                free = [i for i in range(wp.MAX_CH) if dev.tag(i) is None] if dev.wave else [dev.next_index()]
                if not free:
                    self.toast.popup("A waveform packet has at most 8 channels")
                    return
                t.index = free[0]
            dlg.apply(t)
            dev.tags.append(t)
            if dev.wave:
                dev.tags.sort(key=lambda x: x.index)
        else:
            dlg.apply(t)
        self.tags_changed(dev.id, restart=True)
        if new:
            QTimer.singleShot(0, lambda: self.tags_page.select_tag(dev.id, t.index))
        self.set_status(f"Tag '{t.name}' " + ("added" if new else "saved"), "ok")

    def _group_popup(self, did: str, path: str = "", parent: str = ""):
        """Add group / Edit group pop-up (renaming moves its tags and sub-groups along)."""
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        new = not path
        n = sum(1 for t in dev.tags if path and (t.group == path or t.group.startswith(path + "/")))
        dlg = GroupDialog(self, dev.name, dev.all_groups(), path, parent, n)
        dlg.setWindowTitle("Add group" if new else f"Group - {path}")
        _ok, title = popup_form(dlg, "folder", "New group" if new else path.rsplit("/", 1)[-1],
                                f"{dev.name} · folder of tags (use sub-groups for areas, machines ...)",
                                "Add group" if new else "Save", width=480)
        dlg.name.textChanged.connect(lambda s_: title.setText(s_.strip() or "New group"))
        if not dlg.exec() or self.recording:
            return
        newp = dlg.result_path()
        if new:
            dev.groups.append(newp)
        elif newp != path:
            fix = lambda g: newp + g[len(path):] if g == path or g.startswith(path + "/") else g   # noqa: E731
            dev.groups = sorted({fix(g) for g in dev.groups} | {newp})
            for x in dev.tags:
                x.group = fix(x.group)
        self._mark_dirty()
        self.tags_page.refresh(("group", dev.id, newp))
        self._rebuild_project_tree()
        self.set_status(f"Group '{newp}' " + ("added" if new else "saved"), "ok")

    # ---- alarms and notification profiles
    def _spec_alarm(self, aid, channel=None):
        new = aid is None
        if new:
            ch = self.channel_choices()
            if not ch:
                self.toast.popup("Add a device with tags first")
                return None
            a = ar.new_alarm(tuple(channel) if channel else ch[0][:2])
            a["profiles"] = [p["id"] for p in self.project.alarm_profiles if p["enabled"]]
            a["levels"][0]["enabled"] = True
        else:
            a = next((x for x in self.project.alarms if x["id"] == aid), None)
            if a is None:
                return None
        spec = {"key": f"edit:alarm:{a['id']}", "title": a["name"], "icon": "bell",
                "path": f"Project / Alarms / {a['name']}", "kind": "alarm", "is_new": new}

        def build():
            dlg = AlarmDialog(self, a, self.channel_choices(), self.project.alarm_profiles)
            if spec["is_new"] and dlg.name.text() == "New alarm":
                dlg.name.clear(); dlg.name.setPlaceholderText("default: the tag name")
            return dlg
        spec["build"] = build

        def apply(dlg):
            res = dlg.result_alarm()
            a.clear(); a.update(res)
            if spec["is_new"]:
                self.project.alarms.append(a)
            self.alarms_changed()
            spec["tab"].set_identity(f"edit:alarm:{a['id']}", a["name"], f"Project / Alarms / {a['name']}")
            return True
        spec["apply"] = apply
        spec["new"] = lambda: self.open_editor("alarm", None)
        exists = lambda: any(x is a for x in self.project.alarms)                  # noqa: E731

        def delete():
            if confirm(self, "Delete alarm", f"Delete alarm '{a['name']}'?", "Delete alarm"):
                self.project.alarms[:] = [x for x in self.project.alarms if x is not a]
                self.alarms_changed()
        spec["actions"] = [self._delete_action(spec, delete, exists)]
        return spec

    def _spec_aprofile(self, pid, kind: str = "popup"):
        new = pid is None
        p = ar.new_profile(kind) if new else next((x for x in self.project.alarm_profiles if x["id"] == pid), None)
        if p is None:
            return None
        spec = {"key": f"edit:aprofile:{p['id']}", "title": p["name"], "icon": "send",
                "path": f"Project / Alarms / Notification profiles / {p['name']}", "kind": "notification profile",
                "is_new": new, "section": "Notification profile"}
        spec["build"] = lambda: AlarmProfileDialog(self, p)

        def apply(dlg):
            res = dlg.result_profile()
            p.clear(); p.update(res)
            if spec["is_new"]:
                self.project.alarm_profiles.append(p)
            self.alarms_changed()
            spec["tab"].set_identity(f"edit:aprofile:{p['id']}", p["name"],
                                     f"Project / Alarms / Notification profiles / {p['name']}")
            return True
        spec["apply"] = apply
        spec["new"] = lambda: self.open_editor("aprofile", None, kind=p["kind"])
        exists = lambda: any(x is p for x in self.project.alarm_profiles)          # noqa: E731

        def delete():
            if confirm(self, "Delete notification profile", f"Delete notification profile '{p['name']}'?",
                       "Delete profile", "Alarms that use it stop sending to it."):
                self.project.alarm_profiles.remove(p)
                for x in self.project.alarms:
                    x["profiles"] = [i for i in x["profiles"] if i != p["id"]]
                self.alarms_changed()
        spec["actions"] = [("Send test", "send", lambda: self.test_profile(p) if exists() else None),
                           self._delete_action(spec, delete, exists)]
        return spec

    # ---- variables
    def _spec_variable(self, vid, kind: str = "formula", source=None):
        new = vid is None
        if new:
            base = {"formula": "Calc", "statistic": "Stat", "process": "Proc"}[kind]
            v = vr.new_variable(kind, self.var_page._unique_name(base))
            ch = self.channel_choices()
            if kind != "formula" and ch:
                v["source"] = list(source or ch[0][:2])
        else:
            v = next((x for x in self.project.variables if x["id"] == vid), None)
            if v is None:
                return None
        spec = {"key": f"edit:variable:{v['id']}", "title": v["name"], "icon": "variable",
                "path": f"Project / Variables / {v['name']}", "kind": f"{vr.KINDS.get(v['kind'], '')} variable",
                "is_new": new, "locked": self._locked_reason}
        spec["build"] = lambda: VariableDialog(self, v, self.variable_engine, self.channel_choices())

        def apply(dlg):
            res = dlg.result_variable()
            v.clear(); v.update(res)
            if spec["is_new"]:
                self.project.variables.append(v)
            self.variables_changed()
            spec["tab"].set_identity(f"edit:variable:{v['id']}", v["name"], f"Project / Variables / {v['name']}")
            return True
        spec["apply"] = apply
        spec["new"] = lambda: self.open_editor("variable", None, kind=v["kind"])
        exists = lambda: any(x is v for x in self.project.variables)               # noqa: E731
        spec["actions"] = [("Add alarm", "bell", lambda: self.add_alarm_for(vr.var_key(v["id"])) if exists() else None),
                           self._delete_action(spec, lambda: (self.var_page.select(v["id"]),
                                                              self.var_page.delete_variable()), exists)]
        return spec

    # ---- views and their components
    def _spec_view(self, vid):
        page = self.view_pages.get(vid)
        if page is None:
            return None
        v = page.view
        spec = {"key": f"edit:view:{vid}", "title": v["name"], "icon": "layout",
                "path": f"Project / Views / {v['name']}", "kind": "view", "section": "View"}
        spec["build"] = lambda: ViewDialog(self, v, {x["name"] for x in self.project.views if x is not v})

        def apply(dlg):
            dlg.apply(v)
            self.view_props_changed(page, "name")
            self._refresh_properties()
            spec["tab"].set_identity(spec["key"], v["name"], f"Project / Views / {v['name']}")
            return True
        spec["apply"] = apply
        spec["actions"] = [("Open view", "layout", lambda: self._show_doc(f"view:{vid}")),
                           self._delete_action(spec, lambda: self.delete_view(page),
                                               lambda: vid in self.view_pages)]
        return spec

    def _spec_tile(self, ident):
        vid, tid = ident
        page = self.view_pages.get(vid)
        cfg = next((t for t in page.view["tiles"] if t["id"] == tid), None) if page else None
        if cfg is None:
            return None
        name = lambda: cfg.get("title") or vmod.tile_title(cfg)                     # noqa: E731
        spec = {"key": f"edit:tile:{vid}:{tid}", "title": name(), "icon": "sliders",
                "path": f"Project / Views / {page.name} / {name()}", "kind": vmod.tile_title(cfg)}
        spec["build"] = lambda: TileDialog(self, cfg, self.channel_choices())

        def apply(dlg):
            cfg.update(dlg.result_cfg())
            page.tile_changed(cfg)
            self._stat_dirty = True
            self._refresh_properties()
            spec["tab"].set_identity(spec["key"], name(), f"Project / Views / {page.name} / {name()}")
            return True
        spec["apply"] = apply
        exists = lambda: vid in self.view_pages and any(t is cfg for t in page.view["tiles"])   # noqa: E731
        spec["actions"] = [("Open view", "layout", lambda: self._show_doc(f"view:{vid}")),
                           self._delete_action(spec, lambda: page.tile_of(cfg) and page.delete_tile(page.tile_of(cfg)),
                                               exists)]
        return spec

    def _spec_mllog(self, _ident):
        spec = {"key": "edit:mllog", "title": "AI/ML data log", "icon": "brain-circuit",
                "path": "Project / Logging / AI/ML data log", "kind": "settings", "section": "AI/ML data log"}
        spec["build"] = lambda: MlLogDialog(self, self.project.ml_log,
                                            os.path.join(self.project.logging.folder, "AI_Data"))

        def apply(dlg):
            self.project.ml_log = dlg.result_settings()
            self._mark_dirty()
            self._ml_restart()
            self.set_status("AI/ML data log settings applied" +
                            (" - logging restarted" if self.ml_logger else ""), "ok")
            return True
        spec["apply"] = apply
        spec["actions"] = [("Start / stop log", "brain-circuit", lambda: self._ml_toggle(self.ml_logger is None)),
                           ("Open data folder", "folder-open", self._ml_open_folder)]
        return spec

    # ---- logging profiles
    def _spec_lprofile(self, pid, kind: str = "signal"):
        new = pid is None
        if new:
            base = "Data" if kind == "signal" else "Statistics"
            taken = {x["name"] for x in self.project.logging_profiles}
            pr = new_log_profile(kind, next(n for n in [base] + [f"{base} {i}" for i in range(2, 99)]
                                            if n not in taken))
        else:
            pr = self._log_profile(pid)
            if pr is None:
                return None
        spec = {"key": f"edit:lprofile:{pr['id']}", "title": pr["name"], "icon": "file-text",
                "path": f"Project / Logging / {pr['name']}",
                "kind": "statistic logging profile" if pr["type"] == "statistic" else "data logging profile",
                "is_new": new, "locked": self._locked_reason, "section": "Logging profile"}

        def build():
            taken = {dl.safe_name(x["name"]).lower() for x in self.project.logging_profiles if x["id"] != pr["id"]}
            return LogProfileDialog(self, pr, self.channel_choices(), taken, add_variable=self._new_variable_key)
        spec["build"] = build

        def apply(dlg):
            res = dlg.result_profile()
            pr.clear(); pr.update(res)
            if spec["is_new"]:
                self.project.logging_profiles.append(pr)
            self._log_profiles_changed()
            self._select_log_profile(pr["id"])
            spec["tab"].set_identity(f"edit:lprofile:{pr['id']}", pr["name"], f"Project / Logging / {pr['name']}")
            return True
        spec["apply"] = apply
        spec["new"] = lambda: self.open_editor("lprofile", None, kind=pr["type"])
        exists = lambda: any(x is pr for x in self.project.logging_profiles)      # noqa: E731
        spec["actions"] = [self._delete_action(spec, lambda: self._delete_log_profile(pr["id"]), exists)]
        return spec

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
        self._virtual_reload()                   # tag names are formula names (inputs may be gone)
        self._agg_reload()
        self._variables_reload()
        self._rebuild_plots()
        self._alarms_reload()
        self._update_logging_help()
        self._update_lock()
        self._ml_restart()                       # new device folders / names in meta.json
        self._stat_dirty = True
        self._rebuild_project_tree()
        if hasattr(self, "conn_page"):
            self.conn_page.refresh()             # tag counts, names, enabled

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
        QTimer.singleShot(0, lambda: (self._structure_changed(), self.tags_page.refresh(),
                                      self._refresh_properties()))

    def tags_changed(self, did: str, restart: bool = False):
        self._mark_dirty()
        if restart and self.drivers.running(did):
            self.drivers.start(self.project.device(did))
        QTimer.singleShot(0, lambda: (self._structure_changed(), self.tags_page.refresh(),
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
        if ag.is_agg(did):
            a = self.aggregation(did)
            return self.agg_color(a) if a else COLORS[0]
        if vt.is_vt(did):
            v = self.virtual_tag(did)
            return self.vt_color(v) if v else COLORS[0]
        if ss.is_sys(did):
            t = self.sys_tag(did)
            return self.sys_color(t) if t else COLORS[0]
        return getattr(self, "_colors", {}).get((did, idx)) or COLORS[idx % len(COLORS)]

    def device_state(self, did: str) -> tuple[str, str]:
        return self.drivers.state(did)

    def tag_live(self, did: str, idx: int):
        """(value text, quality text, good True / False / None, time text) for tables."""
        if ag.is_agg(did):
            a = self.aggregation(did)
            if a is None:
                return "", "", None, ""
            if not a["enabled"]:
                return "", "Disabled", None, ""
            if a["id"] in self.agg_engine.errors:
                return "", "Error", False, ""
            st = self.agg_engine.states.get(a["id"])
            if st is None:
                return "", "Waiting", None, ""
            val = st.previous if idx == 1 else (st.value if st.has else None)
            text = "" if val is None else f"{val:.{int(a.get('decimals', 2))}f}"
            good = True if st.quality in ("Good", "Restored") else (False if st.quality == "Error" else None)
            return (text, st.quality, good,
                    time.strftime("%H:%M:%S", time.localtime(st.sent)) if st.sent else "")
        if vt.is_vt(did):
            v = self.virtual_tag(did)
            if v is None:
                return "", "", None, ""
            if not v["enabled"]:
                return "", "Disabled", None, ""
            r = self.vt_engine.results.get(v["id"])
            if r is None:
                return "", "Waiting", None, ""
            good = True if r.good and r.quality.startswith("Good") else (None if r.quality in ("Held", "Waiting")
                                                                          else False)
            return (vt.format_value(v, r.value), r.quality, good,
                    time.strftime("%H:%M:%S", time.localtime(r.stamp)) if r.stamp else "")
        if ss.is_sys(did):
            t = self.sys_tag(did)
            val = self.sys_values.get(t.id) if t else None
            if val is None:
                return "", "", None, ""
            na = not isinstance(val, str) and val != val     # NaN: not available (no data yet)
            return (ss.format_value(t, val), "N/A" if na else "Good", None if na else True,
                    time.strftime("%H:%M:%S", time.localtime(self._sys_t)))
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
        if self._recording_guard():
            self._device_popup(None, driver)

    def edit_device(self, did: str):
        self._device_popup(did)

    def delete_device(self, did: str):
        self.delete_devices([did])

    def delete_devices(self, dids: list[str]):
        devs = [d for d in (self.project.device(i) for i in dids) if d is not None]
        if not devs or not self._recording_guard():
            return
        n = sum(len(d.tags) for d in devs)
        what = f"device '{devs[0].name}'" if len(devs) == 1 else f"{len(devs)} devices"
        if not confirm(self, "Delete devices" if len(devs) > 1 else "Delete device",
                       f"Delete {what} and {n} tag(s)?",
                       f"Delete {len(devs)} devices" if len(devs) > 1 else "Delete device",
                       "Alarms, views and logging profiles that use their tags stop showing data."):
            return
        for dev in devs:
            did = dev.id
            self.drivers.stop(did)
            self.project.devices.remove(dev)
            self._close_editors(f"edit:tag:{did}:")
            self._close_editors(f"edit:group:{did}:")
            self.streams.pop(did, None)
        self._mark_dirty()
        self._structure_changed()
        self.tags_page.refresh(())
        self.show_properties(None)
        self._sync_comm()
        self.toast.popup(f"Deleted '{devs[0].name}'" if len(devs) == 1 else f"Deleted {len(devs)} devices")

    def duplicate_device(self, did: str):
        """Copy of a device with its settings, groups and tags (new id, not started)."""
        src = self.project.device(did)
        if src is None or not self._recording_guard():
            return
        dev = copy.deepcopy(src)
        dev.id = uuid.uuid4().hex[:8]
        dev.name = self._unique_name(f"{src.name} copy", {d.name for d in self.project.devices})
        self.project.devices.insert(self.project.devices.index(src) + 1, dev)
        self._mark_dirty()
        self._structure_changed()
        self.tags_page.refresh()
        self.conn_page.select(dev.id)
        self.toast.popup(f"Duplicated as '{dev.name}'")

    def set_devices_enabled(self, dids: list[str], on: bool) -> bool:
        """Enable / disable devices: a disabled device stops and is not logged; an enabled one
        starts if communication runs."""
        if not self._recording_guard():
            return False
        for did in dids:
            dev = self.project.device(did)
            if dev is None or dev.enabled == on:
                continue
            dev.enabled = on
            if not on:
                self.drivers.stop(did)
            elif self.drivers.any_running and dev.driver not in PLANNED:
                self.drivers.start(dev)
        self._mark_dirty()
        self._structure_changed()
        self.tags_page.refresh()
        self._update_device_states()
        self._refresh_properties()
        return True

    def open_tags(self, did: str):
        self._show_doc("tags")
        self.tags_page.refresh(("device", did))

    def _update_device_states(self):
        self._sync_comm()
        self.conn_page.update_states()
        self.tags_page.update_states()

    def export_devices_dialog(self, preselect: list[str] = ()):
        """Export pop-up: tick devices, choose JSON / CSV, then where to save."""
        if not self.project.devices:
            self.toast.popup("No devices to export")
            return
        dlg = ExportDevicesDialog(self, self.project.devices, list(preselect))
        if dlg.exec():
            self.export_devices(dlg.chosen(), dlg.format())

    def export_devices(self, dids: list[str], fmt: str):
        """JSON: devices with groups and tags; CSV: the device list. No passwords."""
        devs = [d for d in self.project.devices if d.id in dids]
        if not devs:
            return
        base = dl.safe_name(devs[0].name if len(devs) == 1 else self.project.name) + "_devices"
        ext = {"json": "JSON (*.json)", "csv": "CSV (*.csv)"}[fmt]
        path, _ = QFileDialog.getSaveFileName(self, "Export devices",
                                              os.path.join(self._project_dir(), f"{base}.{fmt}"), ext)
        if not path:
            return
        try:
            if fmt == "json":
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(devices_to_json(devs), f, indent=2, ensure_ascii=False)
            else:
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    w = csv.DictWriter(f, DEVICE_COLS)
                    w.writeheader(); w.writerows(devices_to_rows(devs))
        except OSError as e:
            self.banners.post("proj", f"Could not write {path}: {e}", "bad")
            return
        self._log(f"Exported {len(devs)} device(s) to {path}")
        self.toast.popup(f"{len(devs)} device(s) exported" + (" with tags" if fmt == "json" else ""))

    def import_devices(self):
        """JSON export (or a .nxl project: its devices) with tags, or a CSV device list."""
        if not self._recording_guard():
            return
        dlg = ImportDevicesDialog(self, {d.name for d in self.project.devices}, self._project_dir())
        if not dlg.exec():                       # the dialog checks the file: only valid files get here
            return
        path, csv_file = dlg.path, dlg.is_csv
        mode, ids, added, replaced = dlg.mode(), {d.id for d in self.project.devices}, [], 0
        for dev in dlg.chosen():
            old = next((d for d in self.project.devices if d.name == dev.name), None)
            if old is not None and mode == "skip":
                continue
            if old is not None and mode == "replace":
                self.drivers.stop(old.id)
                self.streams.pop(old.id, None)
                dev.id = old.id                  # alarms, views, logging keep pointing at it
                if csv_file:                     # a CSV row has no tags: keep the old ones
                    dev.tags, dev.groups = old.tags, old.groups
                if old.driver == dev.driver:     # passwords are not exported: keep the old ones
                    dev.settings.update({k: old.settings[k] for k in secret_keys(dev.driver) if old.settings.get(k)})
                self.project.devices[self.project.devices.index(old)] = dev
                replaced += 1
            else:
                if old is not None:
                    dev.name = self._unique_name(dev.name, {d.name for d in self.project.devices})
                if dev.id in ids:
                    dev.id = uuid.uuid4().hex[:8]
                self.project.devices.append(dev)
                added.append(dev.id)
            ids.add(dev.id)
        if not added and not replaced:
            return
        self._mark_dirty()
        self._structure_changed()
        self.tags_page.refresh()
        self.conn_page.refresh(added)
        self._show_doc("connectivity")
        self._log(f"Imported devices from {path}: {len(added)} added, {replaced} replaced")
        self.toast.popup(f"{len(added)} device(s) added" + (f", {replaced} replaced" if replaced else ""))

    def _add_tag_current(self):
        """Ctrl+T: add a tag to the device selected in Connectivity (or the first one)."""
        page = self.tags_page
        did = page.current_device_id() or (self.project.devices[0].id if self.project.devices else None)
        if did is None:
            self._show_doc("connectivity")
            self.toast.popup("Add a device first")
            return
        self.add_tags(did, page.current_group() if page.current_device_id() == did else "", False)

    def add_tags(self, did: str, group: str, bulk: bool):
        """bulk: Add tags... pop-up; one tag: the Add tag pop-up."""
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        if not bulk:
            self._tag_popup(did, None, group)
            return
        if dev.wave and len(dev.tags) >= wp.MAX_CH:
            self.toast.popup("A waveform packet has at most 8 channels")
            return
        if bulk:
            dlg = AddTagsDialog(self, dev, group, self.project.all_groups())
            if not dlg.exec():
                return
            new = dlg.tags()
        else:
            t = new_tag(dev, "", group)
            t.name = self._unique_name(t.name, {x.name for x in dev.tags})
            new = [t]
        dev.tags.extend(new)
        dev.tags.sort(key=lambda t: t.index) if dev.wave else None
        self._show_doc("tags")
        self.tags_changed(did, restart=True)
        QTimer.singleShot(0, lambda: (self.tags_page.select_tag(did, new[-1].index),
                                      self.select_object(("tag", did, new[-1].index))))

    def edit_tag(self, did: str, idx: int):
        self._tag_popup(did, idx)

    def delete_tags(self, did: str, idxs: list[int]):
        dev = self.project.device(did)
        if dev is None or not idxs or not self._recording_guard():
            return
        names = [t.name for t in dev.tags if t.index in idxs]
        if not confirm(self, "Delete tags", f"Delete {len(names)} tag(s) of {dev.name}?",
                       f"Delete {len(names)} tag{'s' if len(names) > 1 else ''}",
                       ", ".join(names[:8]) + (" ..." if len(names) > 8 else "")):
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
        self._group_popup(did, "", parent)

    def edit_group(self, did: str, path: str):
        self._group_popup(did, path)

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
        self.tags_page.refresh(("group", did, new))
        self.select_object(("group", did, new))

    def delete_group(self, did: str, path: str):
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        inside = [t for t in dev.tags if t.group == path or t.group.startswith(path + "/")]
        if inside:
            box = QMessageBox(self)
            box.setWindowTitle("Delete group"); box.setIcon(QMessageBox.Icon.Warning)
            box.setText(f"Group '{path}' holds {len(inside)} tag(s).")
            box.setInformativeText("Keep the tags (they move up one level) or delete them with the group?")
            keep = box.addButton("Keep tags", QMessageBox.ButtonRole.AcceptRole)
            kill = box.addButton("Delete group and tags", QMessageBox.ButtonRole.DestructiveRole)
            cancel = box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(keep); box.setEscapeButton(cancel)
            box.exec()
            if box.clickedButton() not in (keep, kill):
                return
            if box.clickedButton() is kill:
                dev.tags = [t for t in dev.tags if t not in inside]
            else:
                parent = path.rsplit("/", 1)[0] if "/" in path else ""
                for t in inside:
                    t.group = parent
        dev.groups = [g for g in dev.groups if not (g == path or g.startswith(path + "/"))]
        self.show_properties(None)
        self.tags_page.refresh(("device", did))
        self.tags_changed(did, restart=bool(inside))

    def write_tag(self, did: str, idx: int):
        t = self.project.tag(did, idx)
        if t is None:
            return
        if self.drivers.state(did)[0] != "online":
            self.toast.popup("The device is not online")
            return
        dev = self.project.device(did)
        dlg = WriteValueDialog(self, dev.name, t, self.last_value(did, idx)[0],
                               describe_address(dev.driver, "", t))
        if dlg.exec():
            val = dlg.result_value()
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
        """Import pop-up (like Import devices): a checked CSV tag list or device JSON, ticked tags,
        what to do with names that exist. Replaced tags keep their index (alarms, views, logging)."""
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        dlg = ImportTagsDialog(self, dev, self._project_dir())
        if not dlg.exec():                       # the dialog checks the file: only valid files get here
            return
        mode, added, replaced = dlg.mode(), 0, 0
        for t in dlg.chosen():
            old = next((x for x in dev.tags if x.name == t.name), None)
            if old is not None and mode == "skip":
                continue
            if old is not None and mode == "replace":
                t.index = old.index
                dev.tags[dev.tags.index(old)] = t
                replaced += 1
                continue
            free = [i for i in range(wp.MAX_CH) if dev.tag(i) is None] if dev.wave else [dev.next_index()]
            if not free:
                self.toast.popup("A waveform packet has at most 8 channels")
                break
            t.index = free[0]
            if old is not None:
                t.name = self._unique_name(t.name, {x.name for x in dev.tags})
            dev.tags.append(t)
            added += 1
        if dev.wave:
            dev.tags.sort(key=lambda x: x.index)
        for g in {t.group for t in dev.tags if t.group} - set(dev.all_groups()):
            dev.groups.append(g)
        if not added and not replaced:
            return
        self.tags_changed(did, restart=True)
        self._log(f"Imported tags into {dev.name} from {dlg.path}: {added} added, {replaced} replaced")
        self.toast.popup(f"{added} tag(s) added" + (f", {replaced} replaced" if replaced else ""))

    def export_tags(self, did: str | None = None, keys: list = ()):
        """Export pop-up (like Export devices): tick tags of one device (did) or of all, CSV / JSON."""
        devs = [d for d in self.project.devices if did is None or d.id == did]
        if not any(d.tags for d in devs):
            self.toast.popup("No tags to export")
            return
        dlg = ExportTagsDialog(self, devs, list(keys))
        if not dlg.exec():
            return
        chosen, fmt = set(dlg.chosen()), dlg.format()
        picked = [(d, [t for t in d.tags if (d.id, t.index) in chosen]) for d in devs]
        picked = [(d, ts) for d, ts in picked if ts]
        n = sum(len(ts) for _, ts in picked)
        base = dl.safe_name(picked[0][0].name if len(picked) == 1 else self.project.name) + "_tags"
        ext = {"json": "JSON (*.json)", "csv": "CSV (*.csv)"}[fmt]
        path, _ = QFileDialog.getSaveFileName(self, "Export tags", os.path.join(self._project_dir(), f"{base}.{fmt}"), ext)
        if not path:
            return
        try:
            if fmt == "json":
                devs_out = [copy.copy(d) for d, _ in picked]
                for c, (_, ts) in zip(devs_out, picked):
                    c.tags = ts
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(devices_to_json(devs_out), f, indent=2, ensure_ascii=False)
            else:
                from nxlogger.core.tags import CSV_COLS
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    w = csv.DictWriter(f, CSV_COLS)
                    w.writeheader()
                    for d, ts in picked:
                        names = {t.name for t in ts}
                        w.writerows(r for r in tags_to_rows(d) if r["name"] in names)
        except OSError as e:
            self.banners.post("proj", f"Could not write {path}: {e}", "bad")
            return
        self._log(f"Exported {n} tag(s) to {path}")
        self.toast.popup(f"{n} tag(s) exported")

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
        self._update_device_states(); self._refresh_properties()

    def stop_device(self, did: str):
        self.drivers.stop(did)
        self._update_device_states(); self._refresh_properties()

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
        self._update_device_states(); self._refresh_properties()

    def _stop_all(self):
        with self._waiting():
            self.drivers.stop_all()
        self._log("Communication stopped")
        self._update_device_states(); self._refresh_properties()

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
        expanded, first = set(), tree.topLevelItemCount() == 0

        def walk(it):
            if it.isExpanded():
                expanded.add(tuple(it.data(0, Qt.ItemDataRole.UserRole) or ()))
            for i in range(it.childCount()):
                walk(it.child(i))
        for i in range(tree.topLevelItemCount()):
            walk(tree.topLevelItem(i))
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

        # Connectivity: the three connector categories, flat - the Connectivity document lists
        # their connectors and devices (like Tags)
        self.nav_conn = node(root, "Connectivity", make_icon("network", T["text"]), ("doc", "connectivity"),
                             "Devices: Protocols, Database, Web Services")
        for key, name, icon, tip in CONN_CATEGORIES:
            n = sum(1 for d in self.project.devices if driver_category(d.driver) == key)
            ci = node(self.nav_conn, f"{name}  ({n})", make_icon(icon, T["text" if n else "muted"]), ("conncat", key), tip)
            if not n:
                ci.setForeground(0, muted)
        # Tags: the six tag categories, flat - the Tags document lists devices, groups and tags
        tags = node(root, "Tags", make_icon("tag", T["text"]), ("tagsroot",),
                    "Tag management: Device, Virtual, Action, Aggregation, Memory, System")
        counts = {"device": sum(len(d.tags) for d in self.project.devices), "system": len(self.sys_tags()),
                  "virtual": len(self.project.virtual_tags), "aggregation": len(self.project.aggregations),
                  "groups": len(self.project.all_groups())}
        for key, name, icon, tip, ready in TAG_CATEGORIES:
            ci = node(tags, name + (f"  ({counts.get(key, 0)})" if ready else ""),
                      make_icon(icon, T["text" if ready else "muted"]), ("tagcat", key), tip)
            if not ready:
                ci.setForeground(0, muted)
        logn = node(root, "Logging", make_icon("file-text", T["text"]), ("doc", "logging"),
                    "Test properties, log folder and logging profiles")
        for pr in self.project.logging_profiles:
            stat = pr["type"] == "statistic"
            li = node(logn, pr["name"], make_icon("sigma" if stat else "activity", T["muted"]), ("lprof", pr["id"]),
                      (f"Statistic · every {fmt_interval(pr['interval_s'])}" if stat else "Data")
                      + f" · {pr['format']}" + ("" if pr["enabled"] else " · disabled"))
            if not pr["enabled"]:
                li.setForeground(0, muted)
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
        views = node(root, "Views", make_icon("layout", T["text"]), ("views",),
                     "Screens: headers, trends, numbers, gauges, tables on a snap grid - right-click for New view")
        for vw in self.project.views:
            node(views, vw["name"], make_icon("layout", T["muted"]), ("view", vw["id"]))

        def expand(it):
            d = tuple(it.data(0, Qt.ItemDataRole.UserRole) or ())
            if d in expanded or (first and d and d[0] in ("project", "doc", "tagsroot", "alarms", "vars",
                                                          "views")):
                it.setExpanded(True)
            for i in range(it.childCount()):
                expand(it.child(i))
        expand(root)
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
        root = self.ptree.topLevelItem(0)
        for i in range(root.childCount() if root else 0):
            it = root.child(i)
            for k in range(it.childCount()):
                it.child(k).setExpanded(False)

    def _nav_data(self, it):
        return it.data(0, Qt.ItemDataRole.UserRole) if it is not None else None

    def _nav_open(self, it):
        """Single click: show it (document + Properties panel). Double-click edits (tab)."""
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
            self.tags_page.refresh(("device", d[1]))
            self.conn_page.select(d[1])
            self.select_object(("device", d[1]))
        elif d[0] == "conncat":
            self._show_doc("connectivity")
            self.conn_page.show_category(d[1])
            self.conn_page.activate()
        elif d[0] == "tagsroot":
            self._show_doc("tags")
        elif d[0] == "tagcat":
            self._show_doc("tags")
            self.tags_page.refresh(("cat", d[1]))
            self.select_object(None)
        elif d[0] == "group":
            self.tags_page.refresh(("group", d[1], d[2]))
            self.select_object(("group", d[1], d[2]))
        elif d[0] == "tag":
            self.tags_page.select_tag(d[1], d[2])
            self.select_object(("tag", d[1], d[2]))
        elif d[0] == "lprof":
            self._select_log_profile(d[1])
            pr = self._log_profile(d[1])
            if pr:
                self.show_properties(props.simple_target(
                    pr["name"], "file-text", "Logging profile - double-click to edit",
                    {"Type": "Statistic" if pr["type"] == "statistic" else "Data", "Format": pr["format"],
                     "Enabled": "Yes" if pr["enabled"] else "No"},
                    [("Edit...", "pencil", lambda: self._edit_log_profile(d[1]))], key=("lprof", d[1])))
        elif d[0] == "alarms":
            self._show_doc("alarms")
            self.alarm_page.tabs.setCurrentIndex(0)
        elif d[0] == "alarm":
            self.alarm_page.select_alarm(d[1])
        elif d[0] in ("aprofs", "aprof"):
            if d[0] == "aprof":
                self.alarm_page.select_profile(d[1])
            else:
                self._show_doc("alarms"); self.alarm_page.tabs.setCurrentIndex(3)
        elif d[0] == "vars":
            self._show_doc("variables")
        elif d[0] == "var":
            self.var_page.select(d[1])

    def _nav_double_clicked(self, it, _col):
        """Double-click opens the item's editor tab (no pop-up)."""
        d = self._nav_data(it)
        if not d:
            return
        go = {"device": lambda: self.edit_device(d[1]),
              "tag": lambda: self.edit_tag(d[1], d[2]),
              "group": lambda: self.edit_group(d[1], d[2]),
              "lprof": lambda: self.open_editor("lprofile", d[1]),
              "alarm": lambda: self.open_editor("alarm", d[1]),
              "aprof": lambda: self.open_editor("aprofile", d[1]),
              "var": lambda: self.open_editor("variable", d[1])}.get(d[0])
        if go:
            QTimer.singleShot(0, go)             # editors rebuild trees: not inside the click

    def _nav_menu(self, pos):
        it = self.ptree.itemAt(pos)
        d = self._nav_data(it) or ("none",)
        if d[0] in HEADER_NODES:                     # section headers (and the project root): no menu
            return
        m = QMenu(self)
        later = lambda fn: (lambda: QTimer.singleShot(0, fn))                    # noqa: E731
        if d[0] == "device":
            dev = self.project.device(d[1])
            run = self.drivers.running(d[1])
            m.addAction("Edit device...", later(lambda: self.edit_device(d[1])))
            m.addAction("Disconnect" if run else "Connect",
                        lambda: (self.stop_device if run else self.start_device)(d[1]))
            if dev and dev.driver == "opcua":
                m.addAction("Browse server...", lambda: self.browse_opcua(d[1], ""))
            m.addSeparator()
            m.addAction("Delete device", lambda: self.delete_device(d[1]))
        elif tuple(d[:2]) == ("doc", "connectivity"):
            add = m.addMenu("Add device")
            for k, dd in DRIVERS.items():
                add.addAction(dd["label"] + "...", lambda kk=k: self.add_device(kk))
        elif tuple(d[:2]) == ("tagcat", "system"):
            m.addAction("Open system tags", lambda: (self._show_doc("tags"), self.tags_page.refresh(("cat", "system"))))
        elif d[0] in ("tagsroot", "group", "tag") or tuple(d[:2]) == ("tagcat", "device"):
            first = self.project.devices[0].id if self.project.devices else None
            did = d[1] if d[0] in ("group", "tag") else (self.tags_page.current_device_id() or first)
            group = d[2] if d[0] == "group" else (self.project.tag(d[1], d[2]).group
                                                   if d[0] == "tag" and self.project.tag(d[1], d[2]) else "")
            if d[0] == "tag":
                t = self.project.tag(d[1], d[2])
                m.addAction("Edit tag...", later(lambda: self.edit_tag(d[1], d[2])))
                if t is not None:
                    m.addAction("Disable" if t.enabled else "Enable",
                                lambda: self.set_tags_enabled(d[1], [d[2]], not t.enabled))
                    if t.access == "RW":
                        m.addAction("Write value...", lambda: self.write_tag(d[1], d[2]))
                    mv = m.addMenu("Move to group")
                    mv.addAction("(no group)", lambda: self.move_tags(d[1], [d[2]], ""))
                    for g in self.project.device(d[1]).all_groups():
                        mv.addAction(g, lambda gg=g: self.move_tags(d[1], [d[2]], gg))
                m.addAction("Add alarm for this tag...", lambda: self.add_alarm_for((d[1], d[2])))
                m.addAction("Add statistic variable of this tag...", lambda: self._var_action("add", [d[1], d[2]]))
                m.addAction("Delete tag", lambda: self.delete_tags(d[1], [d[2]]))
                m.addSeparator()
            elif d[0] == "group":
                m.addAction("Edit group...", later(lambda: self.edit_group(d[1], d[2])))
                m.addAction("Delete group", lambda: self.delete_group(d[1], d[2]))
                m.addSeparator()
            if did is not None:
                where = f" ({group})" if group else ""
                m.addAction(f"New tag{where}...", later(lambda: self.add_tags(did, group, False)))
                m.addAction(f"Add tags{where}...", lambda: self.add_tags(did, group, True))
                m.addAction(f"New group{where}...", later(lambda: self.add_group(did, group)))
                if d[0] in ("tagcat", "tagsroot"):
                    m.addSeparator()
                    m.addAction("Import tags...", lambda: self.import_tags(did))
                    m.addAction("Export tags...", lambda: self.export_tags(did))
            else:
                m.addAction("Add a device first (Connectivity)", lambda: self._show_doc("connectivity"))
        elif d[0] in ("views", "view"):
            if d[0] == "view":
                page = self.view_pages.get(d[1])
                m.addAction("Open", lambda: self._show_doc(f"view:{d[1]}"))
                m.addAction("View properties...", lambda: self.view_properties(page))
                m.addAction("Delete view", lambda: self.delete_view(page))
                m.addSeparator()
            m.addAction("New view...", self._new_view)
        elif d[0] == "lprof" or tuple(d[:2]) == ("doc", "logging"):
            if d[0] == "lprof":
                pr = self._log_profile(d[1])
                m.addAction("Edit profile...", later(lambda: self.open_editor("lprofile", d[1])))
                if pr:
                    m.addAction("Disable" if pr["enabled"] else "Enable", lambda: self._toggle_log_profile(d[1]))
                m.addAction("Duplicate", lambda: self._dup_log_profile(d[1]))
                m.addAction("Delete profile", lambda: self._delete_log_profile(d[1]))
                m.addSeparator()
            m.addAction("New data profile...", lambda: self._add_log_profile("signal"))
            m.addAction("New statistic profile...", lambda: self._add_log_profile("statistic"))
        elif d[0] in ("alarms", "alarm", "aprofs", "aprof"):
            pg_ = self.alarm_page
            if d[0] == "alarm":
                a = next((x for x in self.project.alarms if x["id"] == d[1]), None)
                m.addAction("Edit alarm...", later(lambda: self.open_editor("alarm", d[1])))
                m.addAction("Disable" if a and a["enabled"] else "Enable", lambda: pg_.toggle_alarm(d[1]))
                m.addAction("Delete alarm", lambda: (pg_.select_alarm(d[1]), pg_.delete_alarm()))
                m.addSeparator()
            elif d[0] == "aprof":
                pf = next((x for x in self.project.alarm_profiles if x["id"] == d[1]), None)
                m.addAction("Edit profile...", later(lambda: self.open_editor("aprofile", d[1])))
                m.addAction("Disable" if pf and pf["enabled"] else "Enable", lambda: pg_.toggle_profile(d[1]))
                m.addAction("Send test", lambda: pf and self.test_profile(pf))
                m.addAction("Delete profile", lambda: (pg_.select_profile(d[1]), pg_.delete_profile()))
                m.addSeparator()
            if d[0] in ("aprofs", "aprof"):
                add = m.addMenu("New notification profile")
                for k, text in ar.PROFILE_KINDS.items():
                    add.addAction(text, lambda kk=k: self.open_editor("aprofile", None, kind=kk))
            else:
                m.addAction("New alarm...", later(lambda: self.open_editor("alarm", None)))
        elif d[0] in ("vars", "var"):
            vp = self.var_page
            if d[0] == "var":
                v = next((x for x in self.project.variables if x["id"] == d[1]), None)
                m.addAction("Edit variable...", later(lambda: self.open_editor("variable", d[1])))
                m.addAction("Disable" if v and v["enabled"] else "Enable", lambda: vp.toggle(d[1]))
                m.addAction("Delete variable", lambda: (vp.select(d[1]), vp.delete_variable()))
                m.addAction("Add alarm for this variable...",
                            lambda: self._alarm_action("add", list(vr.var_key(d[1]))))
                m.addSeparator()
            add = m.addMenu("New variable")
            for k, text in vr.KINDS.items():
                add.addAction(text + "...", lambda kk=k: self.open_editor("variable", None, kind=kk))
        else:
            add = m.addMenu("Add device")
            for k, dd in DRIVERS.items():
                add.addAction(dd["label"] + "...", lambda kk=k: self.add_device(kk))
            m.addAction("New view...", self._new_view)
        free = ("views", "view", "alarms", "alarm", "aprofs", "aprof")
        for a in m.actions():
            a.setEnabled(a.isEnabled() and (self.recorder is None or d[0] in free or a.text().startswith(
                ("Add alarm", "Connect", "Disconnect", "Write", "Export", "Edit"))))
        m.exec(self.ptree.viewport().mapToGlobal(pos))

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
        """Pop-up form of a logging profile (used by actions; the tree opens an editor tab)."""
        taken = {dl.safe_name(x["name"]).lower() for x in self.project.logging_profiles if x["id"] != pr["id"]}
        dlg = LogProfileDialog(self, pr, self.channel_choices(), taken, add_variable=self._new_variable_key)
        return dlg.result_profile() if dlg.exec() else None

    def _add_log_profile(self, kind: str):
        if self._recording_guard():
            self.open_editor("lprofile", None, kind=kind)

    def _edit_log_profile(self, pid: str | None = None):
        pr = self._log_profile(pid)
        if pr is not None:
            self.open_editor("lprofile", pr["id"])

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
        if not confirm(self, "Delete logging profile", f"Delete logging profile '{pr['name']}'?",
                       "Delete profile", "Recordings already made are not touched."):
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
        if hasattr(self, "tags_page"):
            self.tags_page.update_states()
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
        self.open_editor("mllog", "settings")

    def _ml_marker(self, kind: str):
        """Event marker in events.csv - the AI app turns these into draft labels."""
        if self.ml_logger is None:
            return
        t = time.time()                          # the moment of the click, not of the OK
        dlg = MarkerDialog(self, MARKER_TYPES, kind, t)
        if not dlg.exec() or self.ml_logger is None:
            return
        kind, text = dlg.result()
        title = dict(MARKER_TYPES)[kind].split(" (")[0]
        self.ml_logger.event(kind, self.project.operator or "operator", text, t)
        self._log(f"Marker {kind}: {text}")
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
                self._structure_changed(); self.tags_page.refresh()
        if self.vt_engine.order:
            self._update_virtual_tags()
        if self.agg_engine.order:
            self._update_aggregations()
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
                              lambda: (self._show_doc("connectivity"), self.conn_page.select(dev.id)))
        elif state == "online":
            self.banners.dismiss(f"dev:{dev.id}")
            self.set_status(f"{dev.name} online - {text}", "ok")
        self.tags_page.update_states()
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
        self._update_system_tags()
        rate = errs = lost = 0
        for tid, st in self.streams.items():
            st.pps, st.win_pkts = st.win_pkts / dt, 0
            if ss.is_sys(tid):
                continue
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
        elif page is self.tags_page:
            self.tags_page.update_live()
            self.tags_page.update_states()
        elif page is self.conn_page:
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
        if ss.is_sys(tid):
            t = self.sys_tag(tid)
            return (f"System · {t.name}" if t else "(removed system tag)"), (t.unit if t else "")
        if vr.is_var(tid):
            v = self._var(tid)
            return (f"Variables · {v['name']}" if v else "(deleted variable)"), (v.get("unit", "") if v else "")
        if vt.is_vt(tid):
            v = self.virtual_tag(tid)
            return (f"Virtual · {v['name']}" if v else "(deleted virtual tag)"), (v.get("unit", "") if v else "")
        if ag.is_agg(tid):
            a = self.aggregation(tid)
            return ((f"Aggregation · {a['name']}" + (" (previous)" if idx == 1 else "")) if a
                    else "(deleted aggregation tag)"), (a.get("unit", "") if a else "")
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
        if isinstance(w, FormTab):
            self.close_editor(w)                 # editors are closed for good (ask if modified)
            return
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
        for v in self.project.virtual_tags:          # Virtual tags with a number / boolean
            if v["enabled"] and v.get("dtype") != "text":
                u = v.get("unit", "")
                out.append((*vt.vt_key(v["id"]), f"Virtual · {v['name']}" + (f" [{u}]" if u else ""),
                            self.vt_color(v), u))
        for a in self.project.aggregations:          # Aggregation tags (+ the previous period's value)
            if a["enabled"]:
                u = a.get("unit", "")
                out.append((*ag.agg_key(a["id"]), f"Aggregation · {a['name']}" + (f" [{u}]" if u else ""),
                            self.agg_color(a), u))
                if a.get("keep_prev") and ag.has_period(a):
                    out.append((*ag.agg_key(a["id"], 1), f"Aggregation · {a['name']} (previous)"
                                + (f" [{u}]" if u else ""), self.agg_color(a), u))
        for t in self.sys_tags():                    # System Tags with a number (text is not a stream)
            if t.streamed:
                out.append((*t.key, f"System · {t.name}" + (f" [{t.unit}]" if t.unit else ""),
                            self.sys_color(t), t.unit))
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
        if vt.is_vt(tid):
            v = self.virtual_tag(tid)
            return val, (v.get("unit", "") if v else "")
        if ag.is_agg(tid):
            a = self.aggregation(tid)
            return val, (a.get("unit", "") if a else "")
        if ss.is_sys(tid):
            t = self.sys_tag(tid)
            return val, (t.unit if t else "")
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
            if vr.is_var(tid) or ss.is_sys(tid) or vt.is_vt(tid) or ag.is_agg(tid):
                val, unit = self.last_value(tid, idx)
                st = self.sys_tag(tid) if ss.is_sys(tid) else None
                if vt.is_vt(tid) or ag.is_agg(tid):
                    text, qual, good, stamp = self.tag_live(tid, idx)
                    v = self.virtual_tag(tid) if vt.is_vt(tid) else self.aggregation(tid)
                    out.append({"Tag": name.split(" [")[0], "Value": "-" if val is None else f"{val:.{decimals}f}",
                                "Unit": unit, "Quality": qual or "No data", "Time": stamp,
                                "Device": "Virtual" if vt.is_vt(tid) else "Aggregation",
                                "Description": v.get("description", "") if v else "", "_good": good is not False})
                    continue
                out.append({"Tag": name.split(" [")[0], "Value": "-" if val is None else f"{val:.{decimals}f}",
                            "Unit": unit, "Quality": "Good" if val is not None else "No data", "Time": "",
                            "Device": "System" if st else "Variables", "Description": st.description if st else "",
                            "_good": val is not None})
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
        ids = {cm.identifier(f"{d.name}_{t.name}"): (d.id, t.index)
               for d in self.project.devices for t in d.tags}
        ids.update({cm.identifier(f"System_{t.name}"): t.key for t in self.sys_tags() if t.streamed})
        ids.update({vt.formula_name(v): vt.vt_key(v["id"]) for v in self.project.virtual_tags
                    if v.get("dtype") != "text"})
        for a in self.project.aggregations:
            ids[ag.formula_name(a)] = ag.agg_key(a["id"])
            if a.get("keep_prev") and ag.has_period(a):
                ids[ag.formula_name(a, True)] = ag.agg_key(a["id"], 1)
        return ids

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

    # ================================================================ Virtual tags (core/virtual_tags.py)
    def virtual_tag(self, vid: str) -> dict | None:
        """By key topic ("vt:<id>") or id."""
        vid = vid[len(vt.VT_PREFIX):] if vt.is_vt(vid) else vid
        return next((v for v in self.project.virtual_tags if v["id"] == vid), None)

    def vt_color(self, v: dict) -> str:
        if v.get("color"):
            return v["color"]
        i = next((k for k, x in enumerate(self.project.virtual_tags) if x["id"] == v["id"]),
                 len(self.project.virtual_tags))         # a new one: the next palette colour
        return COLORS[(i + 12) % len(COLORS)]

    def key_label(self, key) -> str:
        """'<device> · <tag>' of any key (device, variable, System, Virtual) for texts."""
        tid, idx = key
        if vt.is_vt(tid):
            v = self.virtual_tag(tid)
            return f"Virtual · {v['name']}" if v else "(deleted virtual tag)"
        if ag.is_agg(tid):
            a = self.aggregation(tid)
            return (f"Aggregation · {a['name']}" + (" (previous)" if idx == 1 else "")) if a else "(deleted)"
        if ss.is_sys(tid):
            t = self.sys_tag(tid)
            return f"System · {t.name}" if t else "(removed system tag)"
        if vr.is_var(tid):
            v = self._var(tid)
            return f"Variables · {v['name']}" if v else "(deleted variable)"
        d = self.project.device(tid)
        t = d.tag(idx) if d else None
        return f"{d.name} · {t.name}" if t else "(deleted tag)"

    def vt_describe(self, v: dict) -> str:
        return vt.describe(v, self.key_label)

    def vt_choices(self) -> list:
        """What a virtual tag can read: every tag with a value - text ones too (System, Virtual)."""
        out = list(self.channel_choices())
        out += [(*vt.vt_key(v["id"]), f"Virtual · {v['name']} (text)", self.vt_color(v), "")
                for v in self.project.virtual_tags if v["enabled"] and v.get("dtype") == "text"]
        out += [(*t.key, f"System · {t.name} (text)", self.sys_color(t), "") for t in self.sys_tags() if not t.streamed]
        return out

    def _vt_names(self) -> dict:
        """Formula names a virtual tag can use: the tags' (numbers) plus the text System Tags."""
        ids = self._channel_ids()
        ids.update({cm.identifier(f"System_{t.name}"): t.key for t in self.sys_tags() if not t.streamed})
        return ids

    def _vt_latest(self, key):
        """(value or None when missing / bad / stale / disabled, stamp) of an input."""
        tid, idx = key
        if ag.is_agg(tid):
            return self.agg_engine.value(tid[len(ag.AGG_PREFIX):], idx)
        if ss.is_sys(tid):
            t = self.sys_tag(tid)
            val = self.sys_values.get(t.id) if t else None
            stamp = getattr(self, "_sys_t", None)
            if val is None or (not isinstance(val, str) and val != val):
                return None, stamp
            return (val if isinstance(val, str) else float(val)), stamp
        st = self.streams.get(tid)
        if st is None or idx not in st.last_vals:
            return None, None
        val, stamp = st.last_vals[idx], st.stamp.get(idx)
        if not st.good.get(idx, True) or val != val:
            return None, stamp
        dev = self.project.device(tid)
        if dev is not None:
            t = dev.tag(idx)
            if not dev.enabled or t is None or not t.enabled:
                return None, stamp
            if not dev.wave and time.time() - (stamp or 0) > max(5.0, 3 * dev.scan_ms / 1000):
                return None, stamp                   # stale
        return float(val), stamp

    def _virtual_reload(self):
        """Definitions or tag names changed: recompile (errors go to the Log)."""
        eng = self.vt_engine
        eng.set_tags(self.project.virtual_tags)
        for msg in {f"Virtual tag '{v['name']}': {eng.errors[v['id']]}" for v in self.project.virtual_tags
                    if v["enabled"] and v["id"] in eng.errors}:
            self._log(msg)

    def virtual_changed(self):
        """A virtual tag was added / edited / deleted: fresh values and buffers, everything that lists tags."""
        self._mark_dirty()
        for tid in [k for k in self.streams if vt.is_vt(k)]:
            del self.streams[tid]
        self.vt_engine.reset(); self._vt_seq.clear()
        self._virtual_reload()
        self._variables_reload()                 # Virtual_<name> in variable formulas
        self._rebuild_project_tree()
        self._alarms_reload()
        self._stat_dirty = True
        self.tags_page.refresh()
        self._refresh_properties()
        for page in self.view_pages.values():
            page.restyle()

    def _update_virtual_tags(self):
        """GUI timer: calculate what is due; numbers / booleans go through the data path like a
        one-sample device block (streams, alarms, statistics, variables) - text is shown only."""
        done = self.vt_engine.evaluate()
        if not done:
            return
        if self.t_ref is None:
            self.t_ref = time.time()
        for v, r in done:
            if v.get("dtype") == "text":
                continue
            tid = vt.VT_PREFIX + v["id"]
            n = self._vt_seq[tid] = self._vt_seq.get(tid, 0) + 1
            val = r.value if r.good and r.value is not None else np.nan
            vb = vr.VarBlock(n & 0xFFFF, n, r.stamp, 0.5, np.array([[val]], dtype=float))
            self.streams.setdefault(tid, TopicStream()).feed(vb, self.t_ref)
            self.alarm_engine.feed(tid, 0, r.stamp, vb.y[0])
            if self.stats.wants((tid, 0)):
                self.stats.feed((tid, 0), np.array([r.stamp]), vb.y[0])
            self._compute_variables(tid, r.stamp, vb)

    def add_virtual(self, kind: str, group: str = ""):
        if self._recording_guard():
            QTimer.singleShot(0, lambda: self._vt_popup(None, kind, group))

    def edit_virtual(self, vid: str):
        QTimer.singleShot(0, lambda: self._vt_popup(vid))

    def _vt_pick(self, choices, chosen, multi: bool, title: str):
        dlg = PickDialog(self, choices, chosen, multi, title)
        return dlg.result_keys() if dlg.exec() else None

    def _vt_popup(self, vid: str | None = None, kind: str = "formula", group: str = ""):
        """Add / Edit virtual tag pop-up (tabs): the tag joins the project or changes only on Add tag / Save."""
        new = vid is None
        cur = None if new else self.virtual_tag(vid)
        if not new and cur is None:
            return
        if new:
            v = vt.new_virtual(kind)
            v["group"] = group
            v["name"] = self._unique_name(v["name"], {x["name"] for x in self.project.virtual_tags})
        else:
            v = copy.deepcopy(cur)
        taken = {x["name"].strip().lower() for x in self.project.virtual_tags if x["id"] != v["id"]}
        groups = self.project.all_groups()
        dlg = VirtualTagDialog(self, v, self.vt_engine, self.vt_choices(), self._vt_names(), groups,
                               self.tag_live, self._vt_pick, taken, self.vt_color({**v, "color": ""}))
        label_, icon, what = vt.TYPES[v["kind"]]
        dlg.setWindowTitle("Add virtual tag" if new else f"Virtual tag - {v['name']}")
        dlg.ok_btn, title = popup_form(dlg, icon, "New virtual tag" if new else v["name"],
                                       f"Virtual tag · {label_} · {what}", "Add tag" if new else "Save", width=560)
        dlg.name.textChanged.connect(lambda s_: title.setText(s_.strip() or ("New virtual tag" if new else v["name"])))
        dlg.tabs.setCurrentIndex(1 if new else 0)    # new: straight to what defines it
        dlg.resize(dlg.width(), 520)                 # one fixed size: every tab fits, none scrolls
        dlg._preview()                               # the verb button exists now
        if self.recording:                           # recording: look, don't change
            dlg.locked = True
            dlg.ok_btn.setEnabled(False)
            set_prop(dlg.err, "level", "bad"); dlg.err.setText("Stop recording to change virtual tags.")
            dlg._timer.stop()
        if not dlg.exec() or self.recording:
            return
        if new:
            dlg.apply(v)
            self.project.virtual_tags.append(v)
        else:
            dlg.apply(cur)
            v = cur
        self.virtual_changed()
        QTimer.singleShot(0, lambda: self.tags_page.select_tag(*vt.vt_key(v["id"])))
        self.set_status(f"Virtual tag '{v['name']}' " + ("added" if new else "saved"), "ok")

    def duplicate_virtual(self, vid: str):
        cur = self.virtual_tag(vid)
        if cur is None or not self._recording_guard():
            return
        v = copy.deepcopy(cur)
        v["id"] = vt.new_virtual()["id"]
        v["name"] = self._unique_name(f"{cur['name']} copy", {x["name"] for x in self.project.virtual_tags})
        tags = self.project.virtual_tags
        tags.insert(tags.index(cur) + 1, v)
        self.virtual_changed()
        QTimer.singleShot(0, lambda: self.tags_page.select_tag(*vt.vt_key(v["id"])))

    def delete_virtuals(self, vids: list[str]):
        vs = [v for v in (self.virtual_tag(i) for i in vids) if v is not None]
        if not vs or not self._recording_guard():
            return
        keys = {vt.vt_key(v["id"]) for v in vs}
        users = [o["name"] for o in self.project.virtual_tags if o not in vs and set(self.vt_engine.deps(o)) & keys]
        what = f"virtual tag '{vs[0]['name']}'" if len(vs) == 1 else f"{len(vs)} virtual tags"
        detail = (f"Used by {', '.join(users[:4])}{' ...' if len(users) > 4 else ''} - they will show an error."
                  if users else "Alarms and views that show them keep a '(deleted)' entry.")
        if not confirm(self, "Delete virtual tags" if len(vs) > 1 else "Delete virtual tag", f"Delete {what}?",
                       f"Delete {len(vs)} tags" if len(vs) > 1 else "Delete tag", detail):
            return
        self.project.virtual_tags[:] = [v for v in self.project.virtual_tags if v not in vs]
        self.select_object(None)
        self.virtual_changed()

    def set_virtuals_enabled(self, vids: list[str], on: bool):
        vs = [v for v in (self.virtual_tag(i) for i in vids) if v is not None]
        if not vs or not self._recording_guard():
            return
        for v in vs:
            v["enabled"] = on
        self.virtual_changed()

    # ================================================================ Aggregation tags (core/aggregation.py)
    def aggregation(self, aid: str) -> dict | None:
        """By key topic ("agg:<id>") or id."""
        aid = aid[len(ag.AGG_PREFIX):] if ag.is_agg(aid) else aid
        return next((a for a in self.project.aggregations if a["id"] == aid), None)

    def agg_color(self, a: dict) -> str:
        if a.get("color"):
            return a["color"]
        i = next((k for k, x in enumerate(self.project.aggregations) if x["id"] == a["id"]),
                 len(self.project.aggregations))
        return COLORS[(i + 6) % len(COLORS)]

    def agg_describe(self, a: dict) -> str:
        return ag.describe(a, self.key_label)

    def _agg_latest(self, key):
        """Inputs of an aggregation: any number tag, virtual ones from their engine."""
        tid, idx = key
        if vt.is_vt(tid):
            r = self.vt_engine.results.get(tid[len(vt.VT_PREFIX):])
            if r is None:
                return None, None
            return (r.value if r.good and not isinstance(r.value, str) else None), r.stamp
        return self._vt_latest(key)

    def _agg_reload(self):
        eng = self.agg_engine
        eng.set_tags(self.project.aggregations)
        for msg in {f"Aggregation tag '{a['name']}': {eng.errors[a['id']]}" for a in self.project.aggregations
                    if a["enabled"] and a["id"] in eng.errors}:
            self._log(msg)

    def _agg_file(self):
        return paths.data_dir() / "aggregation_state.json"

    def _agg_project_key(self) -> str:
        return os.path.abspath(self.project_path) if self.project_path else f"(unsaved) {self.project.name}"

    def _agg_load(self) -> dict:
        """The running values kept for this project (retain), or {}."""
        try:
            data = json.loads(self._agg_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data.get(self._agg_project_key(), {}) if isinstance(data, dict) else {}

    def _agg_save(self, wait: bool = False):
        """Every 30 s and on exit: the running values to the user data folder (a worker writes)."""
        snap = self.agg_engine.snapshot()
        if not snap:
            return
        f, key = self._agg_file(), self._agg_project_key()

        def write():
            try:
                data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
            except (OSError, ValueError):
                data = {}
            data = data if isinstance(data, dict) else {}
            data[key] = snap
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                tmp = f.with_suffix(".tmp")
                tmp.write_text(json.dumps(data), encoding="utf-8")
                os.replace(tmp, f)
            except OSError:
                pass                                 # not worth a message: it is tried again in 30 s
        th = threading.Thread(target=write, daemon=True)
        th.start()
        if wait:
            th.join(2.0)

    def aggregations_changed(self):
        """Added / edited / deleted: recompile, fresh buffers, everything that lists tags. Running
        values of unchanged tags stay (a totalizer does not restart because another was added)."""
        self._mark_dirty()
        keep = {a["id"] for a in self.project.aggregations}
        for tid in [k for k in self.streams if ag.is_agg(k) and k[len(ag.AGG_PREFIX):] not in keep]:
            del self.streams[tid]
        self._agg_reload()
        self._virtual_reload()                   # Aggregation_<name> in virtual formulas
        self._variables_reload()
        self._rebuild_project_tree()
        self._alarms_reload()
        self._stat_dirty = True
        self.tags_page.refresh()
        self._refresh_properties()
        for page in self.view_pages.values():
            page.restyle()

    def _update_aggregations(self):
        """GUI timer: update every aggregation; changed values (or once a second) go through the
        data path like a device block: channel 0 the value, 1 the previous period's."""
        done = self.agg_engine.evaluate()
        if not done:
            return
        if self.t_ref is None:
            self.t_ref = time.time()
        now = time.time()
        for a, st in done:
            tid = ag.AGG_PREFIX + a["id"]
            n = self._agg_seq[tid] = self._agg_seq.get(tid, 0) + 1
            prev = a.get("keep_prev") and ag.has_period(a)
            ys = [st.value if st.has and st.quality != "Error" else np.nan]
            if prev:
                ys.append(np.nan if st.previous is None else st.previous)
            vb = vr.VarBlock(n & 0xFFFF, n, now, 0.5, np.array(ys, dtype=float).reshape(-1, 1),
                             channels=list(range(len(ys))))
            self.streams.setdefault(tid, TopicStream()).feed(vb, self.t_ref)
            for i in range(len(ys)):
                self.alarm_engine.feed(tid, i, now, vb.y[i])
                if self.stats.wants((tid, i)):
                    self.stats.feed((tid, i), np.array([now]), vb.y[i])
            self._compute_variables(tid, now, vb)

    def add_aggregation(self, kind: str, group: str = ""):
        if self._recording_guard():
            QTimer.singleShot(0, lambda: self._agg_popup(None, kind, group))

    def edit_aggregation(self, aid: str):
        QTimer.singleShot(0, lambda: self._agg_popup(aid))

    def _agg_popup(self, aid: str | None = None, kind: str = "totalizer", group: str = ""):
        """Add / Edit aggregation tag pop-up (tabs): joins the project or changes only on Add tag / Save."""
        new = aid is None
        cur = None if new else self.aggregation(aid)
        if not new and cur is None:
            return
        if new:
            a = ag.new_aggregation(kind)
            a["group"] = group
            a["name"] = self._unique_name(a["name"], {x["name"] for x in self.project.aggregations})
        else:
            a = copy.deepcopy(cur)
        taken = {x["name"].strip().lower() for x in self.project.aggregations if x["id"] != a["id"]}
        choices = [c for c in self.channel_choices() if not (ag.is_agg(c[0]) and c[0] == ag.AGG_PREFIX + a["id"])]
        dlg = AggregationDialog(self, a, self.agg_engine, choices, self._vt_names(), self.project.all_groups(),
                                self.tag_live, taken, self.agg_color({**a, "color": ""}))
        label_, icon, what = ag.TYPES[a["kind"]]
        dlg.setWindowTitle("Add aggregation tag" if new else f"Aggregation tag - {a['name']}")
        dlg.ok_btn, title = popup_form(dlg, icon, "New aggregation tag" if new else a["name"],
                                       f"Aggregation tag · {label_} · {what}", "Add tag" if new else "Save", width=560)
        dlg.name.textChanged.connect(lambda s_: title.setText(s_.strip() or ("New aggregation tag" if new else a["name"])))
        dlg.tabs.setCurrentIndex(1 if new else 0)
        dlg.resize(dlg.width(), 520)
        dlg._check()
        if self.recording:
            dlg.locked = True
            dlg.ok_btn.setEnabled(False)
            set_prop(dlg.err, "level", "bad"); dlg.err.setText("Stop recording to change aggregation tags.")
            dlg._timer.stop()
        if not dlg.exec() or self.recording:
            return
        if new:
            dlg.apply(a)
            self.project.aggregations.append(a)
        else:
            rule = {k: cur.get(k) for k in ("kind", "source", "rate_per", "factor", "edge", "time_unit", "hold",
                                            "change", "change_per", "stat", "win_n", "win_unit", "win_mode")}
            dlg.apply(cur)
            a = cur
            if rule != {k: cur.get(k) for k in rule}:    # what it adds up changed: start again
                self.agg_engine.states.pop(cur["id"], None)
        self.aggregations_changed()
        QTimer.singleShot(0, lambda: self.tags_page.select_tag(*ag.agg_key(a["id"])))
        self.set_status(f"Aggregation tag '{a['name']}' " + ("added" if new else "saved"), "ok")

    def duplicate_aggregation(self, aid: str):
        cur = self.aggregation(aid)
        if cur is None or not self._recording_guard():
            return
        a = copy.deepcopy(cur)
        a["id"] = ag.new_aggregation()["id"]
        a["name"] = self._unique_name(f"{cur['name']}_copy", {x["name"] for x in self.project.aggregations})
        lst = self.project.aggregations
        lst.insert(lst.index(cur) + 1, a)
        self.aggregations_changed()
        QTimer.singleShot(0, lambda: self.tags_page.select_tag(*ag.agg_key(a["id"])))

    def delete_aggregations(self, aids: list[str]):
        xs = [a for a in (self.aggregation(i) for i in aids) if a is not None]
        if not xs or not self._recording_guard():
            return
        what = f"aggregation tag '{xs[0]['name']}'" if len(xs) == 1 else f"{len(xs)} aggregation tags"
        if not confirm(self, "Delete aggregation tags" if len(xs) > 1 else "Delete aggregation tag",
                       f"Delete {what}?", f"Delete {len(xs)} tags" if len(xs) > 1 else "Delete tag",
                       "Their running values are lost. Alarms and views that show them keep a '(deleted)' entry."):
            return
        self.project.aggregations[:] = [a for a in self.project.aggregations if a not in xs]
        for a in xs:
            self.agg_engine.states.pop(a["id"], None)
        self.select_object(None)
        self.aggregations_changed()

    def set_aggregations_enabled(self, aids: list[str], on: bool):
        xs = [a for a in (self.aggregation(i) for i in aids) if a is not None]
        if not xs or not self._recording_guard():
            return
        for a in xs:
            a["enabled"] = on
        self.aggregations_changed()

    def reset_aggregation(self, aid: str):
        a = self.aggregation(aid)
        if a is None or not ag.has_period(a):
            return
        st = self.agg_engine.states.get(aid)
        now = f"{st.value:.{a.get('decimals', 2)}f} {a.get('unit', '')}".strip() if st is not None else "-"
        keep = " It becomes the previous period's value." if a.get("keep_prev") else ""
        if not confirm(self, "Reset aggregation tag", f"Reset '{a['name']}' to 0 now?", "Reset",
                       f"Now: {now}.{keep}"):
            return
        self.agg_engine.reset_now(aid)
        self._log(f"Aggregation tag '{a['name']}' reset by hand (was {now})")
        self._refresh_properties()

    # ================================================================ Groups (Tags > Groups: one tree for all tags)
    def _regroup(self, fix):
        """Apply fix(path) -> path to every group path of the project (devices, tags, virtual,
        aggregation, the project's own list)."""
        self.project.groups = sorted({g for g in (fix(x) for x in self.project.groups) if g})
        for d in self.project.devices:
            d.groups = sorted({g for g in (fix(x) for x in d.groups) if g})
            for t in d.tags:
                t.group = fix(t.group)
        for x in self.project.virtual_tags + self.project.aggregations:
            x["group"] = fix(x.get("group", ""))

    def group_count(self, path: str) -> int:
        inside = lambda g: bool(g) and (g == path or g.startswith(path + "/"))    # noqa: E731
        return (sum(1 for d in self.project.devices for t in d.tags if inside(t.group))
                + sum(1 for x in self.project.virtual_tags + self.project.aggregations if inside(x.get("group", ""))))

    def add_project_group(self, parent: str = ""):
        if not self._recording_guard():
            return
        dlg = GroupDialog(self, "Project (all tags)", self.project.all_groups(), "", parent, root="(top level)")
        dlg.setWindowTitle("Add group")
        _ok, title = popup_form(dlg, "folder", "New group", "Tags > Groups · one tree for all tags (areas, machines ...)",
                                "Add group", width=480)
        dlg.name.textChanged.connect(lambda s_: title.setText(s_.strip() or "New group"))
        if not dlg.exec() or self.recording:
            return
        newp = dlg.result_path()
        self.project.groups = sorted(set(self.project.groups) | {newp})
        self._groups_changed(newp)

    def edit_project_group(self, path: str):
        if not path or not self._recording_guard():
            return
        dlg = GroupDialog(self, "Project (all tags)", self.project.all_groups(), path, "", self.group_count(path),
                          root="(top level)")
        dlg.setWindowTitle(f"Group - {path}")
        _ok, title = popup_form(dlg, "folder", path.rsplit("/", 1)[-1],
                                "Tags > Groups · renaming moves its tags and sub-groups along", "Save", width=480)
        dlg.name.textChanged.connect(lambda s_: title.setText(s_.strip() or path))
        if not dlg.exec() or self.recording:
            return
        newp = dlg.result_path()
        if newp != path:
            self._regroup(lambda g: newp + g[len(path):] if g == path or g.startswith(path + "/") else g)
        self._groups_changed(newp)

    def delete_project_group(self, path: str):
        if not path or not self._recording_guard():
            return
        n = self.group_count(path)
        up = path.rsplit("/", 1)[0] if "/" in path else ""
        if not confirm(self, "Delete group", f"Delete group '{path}'?", "Delete group",
                       f"Its {n} tag(s) and sub-groups move up to {up or 'the top level'} - no tag is deleted."
                       if n else "It holds no tags."):
            return
        self._regroup(lambda g: (up + "/" + g[len(path) + 1:]).strip("/") if g.startswith(path + "/")
                      else (up if g == path else g))
        self._groups_changed(up or None)

    def move_to_group(self, keys, group: str):
        """Put tags of any category (device, virtual, aggregation keys) into a project group."""
        if not self._recording_guard():
            return
        for tid, idx in keys:
            if vt.is_vt(tid):
                x = self.virtual_tag(tid)
            elif ag.is_agg(tid):
                x = self.aggregation(tid)
            else:
                t = self.project.tag(tid, idx)
                if t is not None:
                    t.group = group
                continue
            if x is not None:
                x["group"] = group
        self._groups_changed(group or None)

    def _groups_changed(self, select: str | None):
        self._mark_dirty()
        self._rebuild_project_tree()
        self.tags_page.refresh(("pgroup", select) if select else ("cat", "groups"))
        self._refresh_properties()

    # ================================================================ System Tags (core/system_tags.py)
    def sys_tags(self) -> list[ss.SysTag]:
        """All System Tags: the fixed ones plus one set per device (rebuilt when devices change)."""
        devs = [(d.id, d.name) for d in self.project.devices]
        if getattr(self, "_sys_sig", None) != devs:
            self._sys_sig = devs
            self._sys_tags = {t.id: t for t in ss.catalog(devs)}
        return list(self._sys_tags.values())

    def sys_tag(self, tid: str) -> ss.SysTag | None:
        """By key topic ("sys:<id>") or id."""
        self.sys_tags()
        return self._sys_tags.get(tid[len(ss.SYS_PREFIX):] if ss.is_sys(tid) else tid)

    def sys_color(self, t: ss.SysTag) -> str:
        i = next((k for k, x in enumerate(self._sys_tags) if x == t.id), 0)
        return COLORS[(i + 8) % len(COLORS)]

    def _disk_free(self) -> int | None:
        folder = self.project.logging.folder
        for p in (folder, os.path.splitdrive(os.path.abspath(folder))[0] + os.sep):
            try:
                return shutil.disk_usage(p).free
            except OSError:
                continue
        return None

    def _update_system_tags(self):
        """Stats timer (500 ms): compute the System Tags; numbers / booleans go through the data
        path like a one-sample device block (streams, alarms, statistics, formulas)."""
        now = self._sys_t = time.time()
        rec = self.recorder
        devs = {}
        for d in self.project.devices:
            st, drv = self.streams.get(d.id), self.drivers.driver(d.id)
            devs[d.id] = {"state": self.drivers.state(d.id)[0],
                          "last": max(st.stamp.values(), default=None) if st else None,
                          "stale_s": max(5.0, 3 * d.scan_ms / 1000), "errors": drv.errors if drv else 0}
        self.sys_values = vals = ss.compute({
            "now": now, "started": self._t_started, "project": self.project.name, "version": __version__,
            "disk_free": self._disk_free(), "recording": rec is not None,
            "rec_dir": rec.dir if rec else "", "rec_bytes": rec.disk_bytes() if rec else 0,
            "rec_elapsed": rec.elapsed if rec else 0, "alarms_active": self.alarm_engine.count(),
            "devices": devs})
        if self.t_ref is None:
            self.t_ref = now
        self._sys_n += 1
        n = self._sys_n
        for t in self.sys_tags():
            v = vals.get(t.id)
            if not t.streamed or v is None:
                continue
            tid = ss.SYS_PREFIX + t.id
            vb = vr.VarBlock(n & 0xFFFF, n, now, 0.5, np.array([[v]], dtype=float))
            self.streams.setdefault(tid, TopicStream()).feed(vb, self.t_ref)
            self.alarm_engine.feed(tid, 0, now, vb.y[0])
            if self.stats.wants(t.key):
                self.stats.feed(t.key, np.array([now]), vb.y[0])
            self._compute_variables(tid, now, vb)

    def _new_variable_key(self):
        """Add a variable from another form (pop-up action) -> (its key, new choices) or None."""
        before = {v["id"] for v in self.project.variables}
        self.var_page.add_variable("formula", popup=True)
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
        dlg = NewViewDialog(self, f"View {n}", {v["name"] for v in self.project.views},
                            [(d.id, d.name) for d in self.project.devices if d.tags])
        if not dlg.exec():
            return
        name, did = dlg.result()
        dev = self.project.device(did) if did else None
        vw = vmod.default_view(name, [(dev.id, t.index) for t in dev.tags if t.enabled][:8]) if dev \
            else vmod.new_view(name)
        self.project.views.append(vw)
        page = self._open_view_page(vw, editing=True)
        self._mark_dirty()
        self._rebuild_project_tree()
        self.show_properties(props.view_target(self, page))
        self.toast.popup("Edit mode - add a header, graph, indicator or table")

    def view_properties(self, page):
        if page is not None:
            self.open_editor("view", page.view["id"])

    def edit_component(self, page, cfg: dict):
        """A view component's "All settings..." (host API of views.py): an editor tab."""
        self.open_editor("tile", (page.view["id"], cfg["id"]))

    def delete_view(self, page):
        if page is None:
            return
        if not confirm(self, "Delete view", f"Delete view '{page.name}' and its {len(page.view['tiles'])} "
                       "component(s)?", "Delete view"):
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
                refit(btn)

        short = mode == "compact"
        for key, (w, full, abbr, _) in self.docs.items():
            i = self.doc_tabs.indexOf(w)
            if i >= 0:
                self.doc_tabs.setTabText(i, abbr if short else full)

        # Properties panel: auto-hide when narrow, bring back when there is room again
        if short:
            if not self.right_dock.isHidden() and not self.right_dock.isFloating():
                self.right_dock.hide()
                self._auto_hidden_right = True
                self._auto_hidden_rt = not self.runtime_dock.isHidden() and not self.runtime_dock.isFloating()
                if self._auto_hidden_rt:
                    self.runtime_dock.hide()
                if prev is not None:
                    self.toast.popup("Properties panel hidden - Ctrl+Shift+P shows it")
        elif self._auto_hidden_right:
            self.right_dock.show()
            if self._auto_hidden_rt:
                self.runtime_dock.show()
            self._auto_hidden_right = self._auto_hidden_rt = False

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
            if not confirm(self, APP_NAME, msg[0].upper() + msg[1:], "Stop and exit",
                           "The files are closed properly before the program ends."):
                e.ignore(); return
            self._stop_recording()
        if not self._confirm_discard():
            e.ignore(); return
        self._ml_stop()
        self.drivers.stop_all()
        self._agg_save(wait=True)
        super().closeEvent(e)


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
