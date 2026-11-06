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
  * Centre: closable document tabs - Start Page, Connectivity, Tags, Assets, Logging, Alarms,
            (Quick View), Views
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
import re
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager

import numpy as np
from PySide6.QtCore import QEvent, QObject, QSettings, QSize, QTimer, QUrl, Qt
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDockWidget,
                               QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPlainTextEdit, QSizePolicy, QStackedWidget,
                               QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
import pyqtgraph as pg

from nxlogger import APP_NAME, __version__, config, paths
from nxlogger.comms.manager import DriverManager
from nxlogger.core import aggregation as ag
from nxlogger.core import assets as am
from nxlogger.core import log_files as lf
from nxlogger.core import log_types as lt
from nxlogger.core import alarm_rules as ar
from nxlogger.core import analysis as an
from nxlogger.core import channel_math as cm
from nxlogger.core import ml_log as mlg
from nxlogger.core import recording as dl
from nxlogger.core import stat_buffer as sb
from nxlogger.core import system_tags as ss
from nxlogger.core import templates as tpl
from nxlogger.core import variables as vr
from nxlogger.core import virtual_tags as vt
from nxlogger.core import view_model as vmod
from nxlogger.core import wavepacket as wp
from nxlogger.core.project import OLD_PROJECT_EXTS, PROJECT_EXT, Project, fmt_interval, new_log_profile
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
from nxlogger.ui.dialogs.asset_dialog import AssetDialog
from nxlogger.ui.dialogs.map_tags_dialog import MapTagsDialog
from nxlogger.ui.dialogs.template_dialog import TemplateDialog
from nxlogger.ui.dialogs.export_devices_dialog import ExportDevicesDialog
from nxlogger.ui.dialogs.export_tags_dialog import ExportTagsDialog
from nxlogger.ui.dialogs.import_devices_dialog import ImportDevicesDialog
from nxlogger.ui.dialogs.import_tags_dialog import ImportTagsDialog
from nxlogger.ui.dialogs.common import ChannelPicker, ask_unsaved, confirm, popup_form
from nxlogger.ui.dialogs.log_profile_dialog import LogProfileDialog
from nxlogger.ui.dialogs.marker_dialog import MarkerDialog
from nxlogger.ui.dialogs.ml_log_dialog import MlLogDialog
from nxlogger.ui.dialogs.new_view_dialog import NewViewDialog
from nxlogger.ui.dialogs.opcua_browse_dialog import OpcUaBrowseDialog
from nxlogger.ui.dialogs.pick_dialog import PickDialog
from nxlogger.ui.dialogs.project_dialog import ProjectDialog
from nxlogger.ui.dialogs.tag_dialog import TagDialog
from nxlogger.ui.dialogs.tile_dialog import TileDialog
from nxlogger.ui.dialogs.virtual_tag_dialog import VirtualTagDialog
from nxlogger.ui.dialogs.view_dialog import ViewDialog
from nxlogger.ui.dialogs.write_value_dialog import WriteValueDialog
from nxlogger.ui.icons import check_icon_file, grip_icon_file, swatch
from nxlogger.ui.theme import ui_kit as ui
from nxlogger.ui.theme.app_style import DENSE_QSS, EXTRA_QSS, blend, fusion_palette, fusion_qss
from nxlogger.ui.theme.ui_kit import (AppWindow, Card, Chip, EmptyState, T, fixed_width, label, make_icon, refit,
                                      set_prop)
from nxlogger.ui.widgets import alarm_panel as ap
from nxlogger.ui.widgets import properties as props
from nxlogger.ui.widgets import views as dv
from nxlogger.ui.widgets.assets_page import AssetsPage
from nxlogger.ui.widgets.connectivity import STATE_TOKEN, ConnectivityPage
from nxlogger.ui.widgets.monitor_bar import MonitorBar
from nxlogger.ui.widgets.start_page import StartPage, show_at_startup
from nxlogger.ui.widgets.form_tab import FormTab
from nxlogger.ui.widgets.property_grid import PropertiesPanel
from nxlogger.ui.widgets.tags_page import CATEGORIES as TAG_CATEGORIES, TagsPage
from nxlogger.ui.widgets.logging_page import LoggingPage


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
HEADER_NODES = ("project", "doc", "conncat", "asset", "templates", "tagsroot", "logtype", "alarms", "alarmgrp",
                "aprofs", "views")   # Project tree sections: no right-click menu


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
                                             self.logging_page.refresh(), self.tags_page.refresh(),
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
        self.monitor_bar = MonitorBar(self)          # Monitor mode (full-screen views)
        self.monitor_bar.hide()
        self.centralWidget().layout().insertWidget(0, self.monitor_bar)
        self._monitor: dict | None = None            # the layout to bring back while in Monitor mode
        self._mon_cycle = QTimer(self, timeout=lambda: self.monitor_bar.step(1))
        for key, slot in (("F11", self.toggle_monitor), ("Esc", lambda: self._monitor and self.exit_monitor()),
                          ("PgUp", lambda: self._monitor and self.monitor_bar.step(-1)),
                          ("PgDown", lambda: self._monitor and self.monitor_bar.step(1))):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut if key == "F11" else Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)
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
        self._icon_only = []                         # Home / New / Open / Save: icons only, always
        for text, slot, key, icon in (("Start Page", lambda: self._show_doc("start"), "Ctrl+0", "house"),
                                      ("New project", self._new_project, "Ctrl+N", "file-plus"),
                                      ("Open project...", self._open_project, "Ctrl+O", "folder-open"),
                                      ("Save project", self._save_project, "Ctrl+S", "save")):
            a = self.act(text, slot, key, icon)
            a.setToolTip(f"{text}  ({key})")
            tb.addAction(a)
            tb.widgetForAction(a).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            self._icon_only.append(a)
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
        self.view_menu.insertAction(self.view_menu.actions()[0] if self.view_menu.actions() else None,
                                    self.act("Monitor mode  (F11)", self.toggle_monitor, None, "maximize"))
        for a in (self.act("Start Page", lambda: self._show_doc("start"), None, "house"),
                  self.act("Quick View", lambda: self._show_doc("screen"), "Ctrl+1", "chart"),
                  self.act("Connectivity", lambda: self._show_doc("connectivity"), "Ctrl+2", "network"),
                  self.act("Tags", lambda: self._show_doc("tags"), "Ctrl+6", "tag"),
                  self.act("Logging", lambda: self._show_doc("logging"), "Ctrl+3", "file-text"),
                  self.act_alarms,
                  None,
                  add_dev,
                  self.act("Add tag", self._add_tag_current, "Ctrl+T", "tag"),
                  self.act("Add alarm...", lambda: self._alarm_action("add"), "Ctrl+Shift+M", "bell"),
                  self.act("Add logging profile...", lambda: self.add_log_profile("interval"), None, "file-plus"),
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
                ("start", self._build_start(), "Start Page", "Start", "house"),
                ("screen", self._build_screen(), "Quick View", "Quick", "chart"),
                ("connectivity", self._build_connectivity(), "Connectivity", "Devices", "network"),
                ("tags", self._build_tags(), "Tags", "Tags", "tag"),
                ("assets", self._build_assets(), "Assets", "Assets", "boxes"),
                ("logging", self._build_logging(), "Logging", "Logging", "file-text"),
                ("alarms", self._build_alarms(), "Alarms", "Alarms", "bell")):
            self.docs[key] = (w, title, short, icon)
        # at startup only one tab: the Start Page (or Connectivity when it is switched off) - the
        # other documents open when used (Project tree, menus, buttons: _show_doc adds the tab)
        self._add_doc_tab("start" if show_at_startup() else "connectivity")
        self.doc_tabs.setCurrentIndex(0)
        return self.doc_tabs

    def _build_assets(self):
        self.assets_page = AssetsPage(self)
        ChannelPicker.asset_source = lambda: self.project.assets      # every tag picker: By asset
        return self.assets_page

    def _build_start(self):
        self.start_page = StartPage(self)
        return self.start_page

    def _build_screen(self):
        pg.setConfigOptions(antialias=False)
        self.glw = pg.GraphicsLayoutWidget()
        self.screen_stack = QStackedWidget()
        self.screen_stack.addWidget(EmptyState(
            "tag", "No tags enabled", "Add a device and its tags in Connectivity, or enable one, "
            "to see them here.", "Open Connectivity", lambda: self._show_doc("connectivity")))
        self.screen_stack.addWidget(EmptyState(
            "radio", "Waiting for data", "Connect: every enabled device starts and its tags appear "
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

    def _build_logging(self):
        self.logging_page = LoggingPage(self)
        return self.logging_page

    def _build_alarms(self):
        self.alarm_page = ap.AlarmPage(self)
        return self.alarm_page

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
            elif kind == "lprof":
                t = props.log_profile_target(self, ref[1])
            elif kind == "logtype":
                t = props.log_type_target(self, ref[1])
            elif kind == "asset":
                t = props.asset_target(self, ref[1])
            elif kind == "template":
                t = props.template_target(self, ref[1])
            elif kind == "enterprise":
                t = props.enterprise_target(self)
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
            return "No tag selected", "Select a device or a tag to view its settings"
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
        if k[0] in ("device", "asset", "template", "enterprise", "lprof", "logtype", "tag", "tags", "project",
                    "vt", "agg"):
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
        elif page in (self.conn_page, self.tags_page, getattr(self, "assets_page", None)):
            page.activate()                      # its selected row and properties come back
        elif page is getattr(self, "start_page", None):
            page.activate()
            self.show_properties(props.project_target(self))
        self._draw(force=True)
        if page is self.tags_page:
            self.tags_page.update_live()
        elif page is self.conn_page:
            self.conn_page.update_states()

    # ================================================================ project
    def _set_project(self, p: Project, path: str | None):
        if getattr(self, "_monitor", None) is not None:
            self.exit_monitor()
        self.drivers.stop_all()
        if hasattr(self, "doc_tabs"):
            self._close_editors()                # editors of the old project
        self.project, self.project_path = p, path
        self._loading = True
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
        self.logging_page.show_type(None)
        self.tags_page.refresh(())
        self.conn_page.refresh([])
        if hasattr(self, "assets_page"):
            self.assets_page.show_enterprise()
        if hasattr(self, "start_page"):
            self.start_page.refresh()
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

    def project_counts(self) -> dict:
        p = self.project
        return {"Devices": len(p.devices), "Tags": sum(len(d.tags) for d in p.devices),
                "Virtual tags": len(p.virtual_tags), "Aggregation tags": len(p.aggregations),
                "Assets": len(p.assets), "Views": len(p.views), "Alarms": len(p.alarms)}

    def edit_project(self):
        QTimer.singleShot(0, self._project_popup)

    def _project_popup(self):
        """Project settings pop-up (tabs): name, operator, description, log folder - changes on Save."""
        p = self.project
        dlg = ProjectDialog(self, p, self.project_path, self.project_counts(), folder_locked=self.recording)
        dlg.ok_btn, title = popup_form(dlg, "folder", p.name, "Project · name, description and log folder", "Save",
                                       width=520)
        dlg.name.textChanged.connect(lambda s_: title.setText(s_.strip() or p.name))
        if not dlg.exec():
            return
        r = dlg.result()
        changed = False
        for k in ("name", "operator", "enterprise", "description"):
            if getattr(p, k) != r[k]:
                setattr(p, k, r[k]); changed = True
                if k in ("name", "enterprise"):
                    self._rebuild_project_tree()
                    self.assets_page.refresh()
        if r["folder"] != p.logging.folder and not self.recording:
            self._edit_logging("folder", r["folder"])
            changed = True
        if changed:
            self._mark_dirty()
            self.project_props_changed("description")
            self._refresh_sessions()
            if hasattr(self, "start_page"):
                self.start_page.refresh()
            self.set_status("Project saved in memory - File > Save to keep it", "ok")
        self.show_properties(props.project_target(self))

    def project_props_changed(self, key):
        """The project's name / description changed: the title, the tree, the session file names."""
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
            self._remember_recent(path)
        self._log(f"Project opened: {path or '(converted, not saved yet)'}")

    # ---- Start Page (host API of start_page.py)
    RECENT_KEY = "recent_projects"                   # QSettings: newest first
    RECENT_MAX = 8

    def recent_projects(self) -> list[str]:
        v = QSettings().value(self.RECENT_KEY, [])
        v = v if isinstance(v, list) else [v] if v else []
        return [x for x in v if isinstance(x, str) and os.path.isfile(x)][:self.RECENT_MAX]

    def _remember_recent(self, path: str):
        path = os.path.abspath(path)
        old = [x for x in self.recent_projects() if os.path.abspath(x) != path]
        QSettings().setValue(self.RECENT_KEY, [path] + old[:self.RECENT_MAX - 1])
        if hasattr(self, "start_page"):
            self.start_page.refresh()

    def open_recent(self, path: str):
        if not os.path.isfile(path):
            self.toast.popup("That project file no longer exists")
            return
        if os.path.abspath(path) == os.path.abspath(self.project_path or ""):
            self._show_doc("connectivity")           # already open
            return
        if not (self._recording_guard() and self._confirm_discard()):
            return
        try:
            self._load_project(path)
            self.toast.popup(f"Opened {os.path.basename(path)}")
        except Exception as e:
            self.banners.post("proj", f"Could not open project: {e}", "bad")

    def browse_project(self):
        self._open_project()

    def new_from_template(self, key: str):
        """Start Page > New project: a template, after the unsaved-changes question."""
        if not (self._recording_guard() and self._confirm_discard()):
            return
        p = tpl.make(key, dict(config.get().mqtt))
        self._set_project(p, None)
        title = tpl.TEMPLATE[key][1]
        self._log(f"New project from the {title} template")
        if key == "blank":
            self.toast.popup("New project - add a device in Connectivity")
            self._show_doc("connectivity")
        elif key == "water_plant":
            self.toast.popup("Water plant demo - start the simulator (Start Page > Simulators), then Connect")
            self._show_doc("tags")
        else:
            self.toast.popup(f"New {title} project - check the device settings, then Connect")
            self._show_doc("connectivity")

    def recent_sessions(self, n: int = 5) -> list[tuple]:
        """[(start unix, project name, folder, bytes)] of the newest recordings in the log folder."""
        folder, rows = self.project.logging.folder, []
        try:
            entries = [e for e in os.scandir(folder) if e.is_dir()]
        except OSError:
            return []
        for e in entries:
            pj = os.path.join(e.path, "project.json")
            try:
                with open(pj, encoding="utf-8") as f:
                    info = json.load(f)
            except (OSError, ValueError):
                continue
            rows.append((info.get("start_unix", 0), info.get("project", {}).get("name", e.name), e.path))
        out = []
        for start, name, path in sorted(rows, key=lambda r: r[0], reverse=True)[:n]:
            try:
                size = sum(x.stat().st_size for x in os.scandir(path) if x.is_file())
            except OSError:
                size = None
            out.append((start, name, path, size))
        return out

    def open_path(self, path: str):
        self._open_path(path)

    def show_doc(self, key: str):
        self._show_doc(key)

    def new_view(self):
        self._new_view()

    def start_recording(self):
        if self.recorder is None:
            self._start_recording()

    def start_status(self) -> dict:
        devs = self.project.devices
        rec = self.recorder
        return {"devices": len(devs), "online": sum(1 for d in devs if self.drivers.state(d.id)[0] == "online"),
                "tags": sum(len(d.tags) for d in devs) + len(self.project.virtual_tags) + len(self.project.aggregations),
                "alarms": self.alarm_engine.count(), "recording": rec is not None,
                "elapsed": time.strftime("%H:%M:%S", time.gmtime(rec.elapsed)) if rec else ""}

    DOC_LINKS = [("connectivity.md", "Connectivity and tag addresses"),
                 ("layout-and-menus.md", "Layout and menus"),
                 ("recording-format.md", "Recording file format (NX Viewer)"),
                 ("config.md", "Settings file (config.ini)"),
                 ("packet-format.md", "ESP32 waveform packet format")]

    def doc_links(self) -> list[tuple[str, str]]:
        d = paths.docs_dir()
        return [(title, str(d / f)) for f, title in self.DOC_LINKS if d is not None and (d / f).is_file()]

    SIMULATORS = [("Water treatment plant", "MQTT simulator: 49 signals in plant areas, with its own window - "
                   "use the Water plant demo template", "nxlogger.sim.water_plant_gui"),
                  ("Factory energy", "Modbus TCP server with 11 power meters (127.0.0.1:5020) - use the Factory "
                   "energy demo template", "nxlogger.sim.factory_energy_gui"),
                  ("ESP32 waveform publisher", "MQTT waveform packets like an ESP32 DAQ node", "nxlogger.sim")]

    def simulators(self) -> list[tuple[str, str, str, bool]]:
        ok = paths.repo_root() is not None           # the EXE has no Python to start them with
        return [(t, x, m, ok) for t, x, m in self.SIMULATORS]

    def run_simulator(self, module: str):
        if paths.repo_root() is None:
            self.toast.popup("Simulators start from the source code only")
            return
        try:
            subprocess.Popen([sys.executable, "-m", module], cwd=str(paths.repo_root()))
        except OSError as e:
            self.banners.post("sim", f"Could not start the simulator: {e}", "bad")
            return
        self._log(f"Simulator started: python -m {module}")
        self.toast.popup("Simulator starting - its window opens in a moment")

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
        self._remember_recent(self.project_path)
        self.dirty = False
        self.project.migrated_from = ""
        self.banners.dismiss("proj")
        self._update_title()
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

    # ---- tags: pop-ups like the device settings (the Tags page works like Connectivity)
    def _tag_popup(self, did: str, idx: int | None = None):
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
            t = new_tag(dev, "")
            t.name = self._unique_name(t.name, {x.name for x in dev.tags})
        else:
            t = dev.tag(idx)
            if t is None:
                return
        st = self.streams.get(dev.id)
        k = sum(len(d.tags) for d in self.project.devices[:self.project.devices.index(dev)]) + \
            (dev.tags.index(t) if t in dev.tags else len(dev.tags))
        dlg = TagDialog(self, dev, t, COLORS[k % len(COLORS)], lambda: st.raw_mean.get(t.index) if st else None)
        dlg.setWindowTitle("Add tag" if new else f"Tag - {t.name}")
        where = " · ".join(x for x in (dev.name, driver_label(dev.driver)) if x)
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

    # ---- alarms and notification profiles: settings pop-ups (like devices, tags, assets, logging)
    def _alarm(self, aid: str) -> dict | None:
        return next((x for x in self.project.alarms if x["id"] == aid), None)

    def _aprofile(self, pid: str) -> dict | None:
        return next((x for x in self.project.alarm_profiles if x["id"] == pid), None)

    def add_alarm(self, group: str | None = "limit", channel=None):
        QTimer.singleShot(0, lambda: self._alarm_popup(None, group or "limit", channel))

    def edit_alarm(self, aid: str):
        QTimer.singleShot(0, lambda: self._alarm_popup(aid))

    def _alarm_popup(self, aid: str | None = None, group: str = "limit", channel=None):
        """Add / edit an alarm (tabs General / Levels / Notify) - the project changes on Add / Save."""
        new = aid is None
        cur = None if new else self._alarm(aid)
        if not new and cur is None:
            return
        ch = self.channel_choices()
        if not ch:
            self.toast.popup("Add a device with tags first")
            return
        if new:
            a = ar.preset_alarm(group, tuple(channel) if channel else ch[0][:2])
            a["profiles"] = [p["id"] for p in self.project.alarm_profiles if p["enabled"]]
        else:
            a = copy.deepcopy(cur)
            group = ar.group_of(a)
        dlg = AlarmDialog(self, a, ch, self.project.alarm_profiles)
        if new and dlg.name.text() == "New alarm":
            dlg.name.clear(); dlg.name.setPlaceholderText("default: the tag name")
        gname = ar.GROUP[group][1].split("  ")[0]
        dlg.ok_btn, title = popup_form(dlg, ar.icon_of(a), f"New {gname.lower()} alarm" if new else a["name"],
                                       f"Alarm · {gname}", "Add alarm" if new else "Save", width=760)
        dlg.name.textChanged.connect(lambda s_: title.setText(s_.strip() or (f"New {gname.lower()} alarm" if new
                                                                             else a["name"])))
        dlg._paint_icons()                           # the header follows the chosen icon from now on
        dlg._check()
        if not dlg.exec():
            return
        res = dlg.result_alarm()
        if new:
            self.project.alarms.append(res)
        else:
            cur.clear(); cur.update(res)
        self.alarms_changed()
        self.alarm_page.select_alarm(res["id"])
        self.set_status(f"Alarm '{res['name']}' " + ("added" if new else "saved"), "ok")

    def delete_alarm(self, aid: str):
        a = self._alarm(aid)
        if a is None or not confirm(self, "Delete alarm", f"Delete alarm '{a['name']}'?", "Delete alarm"):
            return
        self.project.alarms[:] = [x for x in self.project.alarms if x is not a]
        self.alarms_changed()

    def duplicate_alarm(self, aid: str):
        a = self._alarm(aid)
        if a is None:
            return
        b = copy.deepcopy(a); b["id"] = uuid.uuid4().hex[:8]
        b["name"] = self._unique_name(f"{a['name']} copy", {x["name"] for x in self.project.alarms})
        self.project.alarms.insert(self.project.alarms.index(a) + 1, b)
        self.alarms_changed()
        self.alarm_page.select_alarm(b["id"])

    def toggle_alarm(self, aid: str):
        a = self._alarm(aid)
        if a is not None:
            a["enabled"] = not a["enabled"]
            self.alarms_changed()

    def add_aprofile(self, kind: str = "popup"):
        QTimer.singleShot(0, lambda: self._aprofile_popup(None, kind))

    def edit_aprofile(self, pid: str):
        QTimer.singleShot(0, lambda: self._aprofile_popup(pid))

    def _aprofile_popup(self, pid: str | None = None, kind: str = "popup"):
        """Add / edit a notification profile (tabs General / Target)."""
        new = pid is None
        cur = None if new else self._aprofile(pid)
        if not new and cur is None:
            return
        p = ar.new_profile(kind) if new else copy.deepcopy(cur)
        dlg = AlarmProfileDialog(self, p)
        dlg.ok_btn, title = popup_form(dlg, ap.PROFILE_ICON.get(p["kind"], "send"),
                                       "New notification profile" if new else p["name"],
                                       "Notification profile · where alarm events go",
                                       "Add profile" if new else "Save", width=560)
        dlg.name.textChanged.connect(lambda s_: title.setText(s_.strip() or p["name"]))
        if not dlg.exec():
            return
        res = dlg.result_profile()
        if new:
            self.project.alarm_profiles.append(res)
        else:
            cur.clear(); cur.update(res)
        self.alarms_changed()
        self.alarm_page.select_profile(res["id"])

    def delete_aprofile(self, pid: str):
        p = self._aprofile(pid)
        if p is None or not confirm(self, "Delete notification profile", f"Delete notification profile '{p['name']}'?",
                                    "Delete profile", "Alarms that use it stop sending to it."):
            return
        self.project.alarm_profiles.remove(p)
        for x in self.project.alarms:
            x["profiles"] = [i for i in x["profiles"] if i != p["id"]]
        self.alarms_changed()

    def toggle_aprofile(self, pid: str):
        p = self._aprofile(pid)
        if p is not None:
            p["enabled"] = not p["enabled"]
            self.alarms_changed()

    # ---- views and their components
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
        if am.migrate(self.project):                # groups of the imported tags -> assets
            self.assets_changed()
        self.toast.popup(f"{len(added)} device(s) added" + (f", {replaced} replaced" if replaced else ""))

    def _add_tag_current(self):
        """Ctrl+T: add a tag to the device selected in Connectivity (or the first one)."""
        page = self.tags_page
        did = page.current_device_id() or (self.project.devices[0].id if self.project.devices else None)
        if did is None:
            self._show_doc("connectivity")
            self.toast.popup("Add a device first")
            return
        self.add_tags(did, False)

    def add_tags(self, did: str, bulk: bool):
        """bulk: Add tags... pop-up; one tag: the Add tag pop-up."""
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        if not bulk:
            self._tag_popup(did, None)
            return
        if dev.wave and len(dev.tags) >= wp.MAX_CH:
            self.toast.popup("A waveform packet has at most 8 channels")
            return
        if bulk:
            dlg = AddTagsDialog(self, dev)
            if not dlg.exec():
                return
            new = dlg.tags()
        else:
            t = new_tag(dev, "")
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

    def browse_opcua(self, did: str):
        dev = self.project.device(did)
        if dev is None or not self._recording_guard():
            return
        dlg = OpcUaBrowseDialog(self, dict(dev.settings))
        if not dlg.exec():
            return
        picked = dlg.selected()
        have = {t.address.get("node") for t in dev.tags}
        new = []
        for node, name in picked:
            if node in have:
                continue
            t = new_tag(dev, self._unique_name(name, {x.name for x in dev.tags}), node=node)
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
        placed = am.migrate(self.project)           # a "group" column of the CSV -> assets
        if not added and not replaced:
            return
        self.tags_changed(did, restart=True)
        self._log(f"Imported tags into {dev.name} from {dlg.path}: {added} added, {replaced} replaced"
                  + (f", {placed} placed in assets" if placed else ""))
        if placed:
            self.assets_changed()
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
        # Tags: All tags + the six tag categories, flat - the Tags document lists them
        tags = node(root, "Tags", make_icon("tag", T["text"]), ("tagsroot",),
                    "Tag management: Device, Virtual, Action, Aggregation, Memory, System")
        counts = {"device": sum(len(d.tags) for d in self.project.devices), "system": len(self.sys_tags()),
                  "virtual": len(self.project.virtual_tags), "aggregation": len(self.project.aggregations)}
        counts["all"] = sum(counts.values())
        for key, name, icon, tip, ready in TAG_CATEGORIES:
            ci = node(tags, name + (f"  ({counts.get(key, 0)})" if ready else ""),
                      make_icon(icon, T["text" if ready else "muted"]), ("tagcat", key), tip)
            if not ready:
                ci.setForeground(0, muted)
        # Assets: the Enterprise, its areas (one level), Templates last - machines only in the document
        A = self.project.assets
        an = node(root, f"Assets · {self.enterprise_name()}", make_icon("boxes", T["text"]), ("doc", "assets"),
                  "The plant: Enterprise > areas > machines > tags")
        self._asset_nodes = {}
        for a in am.areas(A):
            st = am.status(A, a["id"], self.asset_state)
            ai = node(an, f"{a['name']}  ({len(am.children(A, a['id']))})",
                      make_icon(am.icon_of(a), T["bad"] if st == "alarm" else T["text"]), ("asset", a["id"]),
                      f"{am.type_name(a)}" + (f"\n{a['description']}" if a.get("description") else ""))
            self._asset_nodes[a["id"]] = ai
        ti = node(an, f"Templates  ({len(self.project.asset_templates)})", make_icon("copy", T["muted"]),
                  ("templates", None), "What a kind of machine has: attributes, units, KPIs")
        ti.setForeground(0, muted)
        logn = node(root, "Logging", make_icon("file-text", T["text"]), ("doc", "logging"),
                    "Logging profiles by type and the log folder")
        counts = {k: 0 for k in lt.TYPE}
        for pr in self.project.logging_profiles:
            counts[lt.type_of(pr)] += 1
        self.nav_logtypes = {}
        for k, name, icon, what in lt.TYPES:            # the types, one level - profiles in the document
            li = node(logn, f"{name}  ({counts[k]})", make_icon(icon, T["text" if counts[k] else "muted"]),
                      ("logtype", k), what)
            if not counts[k]:
                li.setForeground(0, muted)
            self.nav_logtypes[k] = li
        self.nav_alarms = node(root, "Alarms", make_icon("bell", T["text"]), ("alarms",),
                               "Active alarms and the alarm log - the types: their alarms")
        self.nav_alarm_groups = {}
        for key, name, icon, _types, what in ar.GROUPS:     # the alarm types, one level - alarms: the document
            n = sum(1 for a in self.project.alarms if ar.group_of(a) == key)
            gi = node(self.nav_alarms, f"{name}  ({n})", make_icon(icon, T["text" if n else "muted"]),
                      ("alarmgrp", key), what)
            if not n:
                gi.setForeground(0, muted)
            self.nav_alarm_groups[key] = gi
        node(self.nav_alarms, f"Notifications  ({len(self.project.alarm_profiles)})", make_icon("send", T["text"]),
             ("aprofs",), "Where alarm events go: log, popup, REST API, MQTT, e-mail")
        views = node(root, "Views", make_icon("layout", T["text"]), ("views",),
                     "Screens: headers, trends, numbers, gauges, tables on a snap grid - right-click for New view")
        for vw in self.project.views:
            node(views, vw["name"], make_icon("layout", T["muted"]), ("view", vw["id"]))

        def expand(it):
            d = tuple(it.data(0, Qt.ItemDataRole.UserRole) or ())
            if d in expanded or (first and d and d[0] in ("project", "doc", "tagsroot", "alarms",
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
        for key, it in getattr(self, "nav_alarm_groups", {}).items():     # a type in alarm: its icon red
            mine = [a for a in self.project.alarms if ar.group_of(a) == key]
            hot = any(eng.alarm_state(a["id"]) for a in mine)
            tok = "bad" if hot else ("text" if mine else "muted")
            if force or self._dot_cache.get(f"__g{key}") != tok:
                self._dot_cache[f"__g{key}"] = tok
                it.setIcon(0, make_icon(ar.GROUP[key][2], T[tok]))
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
            if d[1] == "logging":                    # the node itself: every profile
                self.logging_page.show_type(None)
                self.logging_page.activate()
            if d[1] == "assets":                     # the node itself: the Enterprise
                self.assets_page.show_enterprise()
                self.assets_page.activate()
        elif d[0] == "project":
            self._show_doc("start")
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
        elif d[0] == "asset":                        # an area: its machines and tags
            self._show_doc("assets")
            self.assets_page.open(d[1])
        elif d[0] == "templates":
            self._show_doc("assets")
            self.assets_page.show_templates()
        elif d[0] == "tag":
            self.tags_page.select_tag(d[1], d[2])
            self.select_object(("tag", d[1], d[2]))
        elif d[0] == "logtype":                      # one logging type: its profiles
            self._show_doc("logging")
            self.logging_page.show_type(d[1])
            self.logging_page.activate()
        elif d[0] == "alarms":                       # the live view: Active, Alarm log
            self._show_doc("alarms")
            self.alarm_page.show_node(ap.LIVE)
        elif d[0] == "alarmgrp":                     # one alarm type: its alarms
            self._show_doc("alarms")
            self.alarm_page.show_node(d[1])
        elif d[0] == "aprofs":
            self._show_doc("alarms")
            self.alarm_page.show_node(ap.NOTIFY)

    def _nav_double_clicked(self, it, _col):
        """Double-click opens the item's editor tab (no pop-up)."""
        d = self._nav_data(it)
        if not d:
            return
        go = {"device": lambda: self.edit_device(d[1]),
              "tag": lambda: self.edit_tag(d[1], d[2]),
              "asset": lambda: self.edit_asset(d[1]),
}.get(d[0])
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
                m.addAction("Browse server...", lambda: self.browse_opcua(d[1]))
            m.addSeparator()
            m.addAction("Delete device", lambda: self.delete_device(d[1]))
        elif tuple(d[:2]) == ("doc", "connectivity"):
            add = m.addMenu("Add device")
            for k, dd in DRIVERS.items():
                add.addAction(dd["label"] + "...", lambda kk=k: self.add_device(kk))
        elif tuple(d[:2]) == ("tagcat", "system"):
            m.addAction("Open system tags", lambda: (self._show_doc("tags"), self.tags_page.refresh(("cat", "system"))))
        elif d[0] in ("tagsroot", "tag") or tuple(d[:2]) == ("tagcat", "device"):
            first = self.project.devices[0].id if self.project.devices else None
            did = d[1] if d[0] == "tag" else (self.tags_page.current_device_id() or first)
            if d[0] == "tag":
                t = self.project.tag(d[1], d[2])
                m.addAction("Edit tag...", later(lambda: self.edit_tag(d[1], d[2])))
                if t is not None:
                    m.addAction("Disable" if t.enabled else "Enable",
                                lambda: self.set_tags_enabled(d[1], [d[2]], not t.enabled))
                    if t.access == "RW":
                        m.addAction("Write value...", lambda: self.write_tag(d[1], d[2]))
                m.addAction("Add alarm for this tag...", lambda: self.add_alarm_for((d[1], d[2])))
                m.addAction("Delete tag", lambda: self.delete_tags(d[1], [d[2]]))
                m.addSeparator()
            if did is not None:
                m.addAction("New tag...", later(lambda: self.add_tags(did, False)))
                m.addAction("Add tags...", lambda: self.add_tags(did, True))
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
        else:
            add = m.addMenu("Add device")
            for k, dd in DRIVERS.items():
                add.addAction(dd["label"] + "...", lambda kk=k: self.add_device(kk))
            m.addAction("New view...", self._new_view)
        free = ("views", "view")
        for a in m.actions():
            a.setEnabled(a.isEnabled() and (self.recorder is None or d[0] in free or a.text().startswith(
                ("Add alarm", "Connect", "Disconnect", "Write", "Export", "Edit"))))
        m.exec(self.ptree.viewport().mapToGlobal(pos))

    def _log_bases(self) -> list[str]:
        """Every folder the profiles write to (the default first)."""
        out = [self.project.logging.folder]
        for pr in self.project.logging_profiles:
            f = lf.normalize_output(copy.deepcopy(pr))["folder"]
            if f and f not in out:
                out.append(f)
        return out

    def _refresh_sessions(self):
        tree = getattr(self, "stree", None)
        if tree is None:
            return
        tree.clear()
        rows = []
        for folder in self._log_bases():
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
        for start, e, info in sorted(rows, key=lambda r: r[0], reverse=True)[:200]:
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
        dated = 0
        for pr in self.project.logging_profiles:     # profiles writing into date folders: per profile
            out = lf.normalize_output(copy.deepcopy(pr))
            if out["folders"] == "session":
                continue
            d = self.profile_folder(pr)
            base = out["folder"] or self.project.logging.folder
            it = QTreeWidgetItem([pr["name"], "today" if os.path.isdir(d) else "-", lf.describe(out)])
            it.setData(0, Qt.ItemDataRole.UserRole, d if os.path.isdir(d) else base)
            it.setToolTip(0, f"{d}\n{lt.TYPE[lt.type_of(pr)][1]} · {lf.describe(out)} - double-click: today's folder")
            it.setIcon(0, make_icon("folder", T["muted"]))
            tree.addTopLevelItem(it)
            dated += 1
        if not rows and not dated:
            it = QTreeWidgetItem(["No recordings yet", "", ""])
            it.setForeground(0, QBrush(QColor(T["muted"])))
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setToolTip(0, self.project.logging.folder)
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
        if sel is not None:                      # whole assets: their tags as they are now
            for aid in pr.get("assets") or []:
                sel |= set(am.keys(self.project.assets, aid))
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

    def session_files_text(self) -> str:
        """The Logging footer: what the next Record run writes, and where."""
        on = [pr for pr in self.project.logging_profiles if pr["enabled"]]
        if not on:
            return "No enabled logging profile - Record is not possible"
        bases = {lf.normalize_output(copy.deepcopy(pr))["folder"] or self.project.logging.folder for pr in on}
        return (f"Record writes {len(on)} profile(s) into {len(bases)} folder(s) · default folder "
                f"{self.project.logging.folder} (Project > Edit...) · each profile's folder, date folders and new "
                "files: its pop-up > Output")

    def _update_logging_help(self):
        if hasattr(self, "logging_page"):
            self.logging_page.refresh()

    def _log_profile(self, pid: str | None) -> dict | None:
        return next((x for x in self.project.logging_profiles if x["id"] == pid), None)

    def _log_profiles_changed(self, select: str | None = None):
        self._mark_dirty()
        if hasattr(self, "logging_page"):
            self.logging_page.refresh(select)
        self._rebuild_project_tree()
        self._refresh_properties()

    def key_name(self, key) -> str:
        """A tag's short name (Logging: conditions)."""
        return self.key_label(tuple(key)).split(" · ")[-1]

    def alarm_name(self, aid: str) -> str:
        return next((a["name"] for a in self.project.alarms if a["id"] == aid), "(deleted alarm)")

    def profile_tags_text(self, pr: dict) -> str:
        if pr.get("channels") is None:
            return "All enabled"
        n, m = len(pr["channels"]), len(pr.get("assets") or [])
        return f"{n} tag(s)" + (f" + {m} asset(s)" if m else "")

    def log_status(self, pr: dict) -> tuple[str, str]:
        """(writing / armed / off, a count: '3 runs', '12 events') of a profile."""
        kind = lt.type_of(pr)
        unit = {"trigger": "run", "event": "event", "batch": "batch"}.get(kind, "")
        g = getattr(self, "_gates", {}).get(pr["id"])
        rec = self.recorder
        runs = g[1].runs if g else getattr(self, "_gates_done", {}).get(pr["id"], 0)
        count = f"{runs} {unit}{'' if runs == 1 else ('es' if unit == 'batch' else 's')}" if unit and runs else ""
        if rec is None or not pr["enabled"]:
            return "off", count
        i = self._rec_index(pr)
        if i is not None and rec.errors.get(i):
            return "error", count
        if g is not None:
            return ("writing" if g[1].active else "armed"), count
        if i is None:
            return "off", count
        return ("stalled" if self.profile_live(pr)["stalled"] else "writing"), count

    def add_log_profile(self, kind: str = "interval"):
        if self._recording_guard():
            QTimer.singleShot(0, lambda: self._log_profile_popup(None, kind))

    def edit_log_profile(self, pid: str):
        if self._recording_guard():
            QTimer.singleShot(0, lambda: self._log_profile_popup(pid))

    def _log_profile_popup(self, pid: str | None = None, kind: str = "interval"):
        """Add / edit a logging profile: the settings pop-up (tabs General / When / Tags / Output)."""
        profs = self.project.logging_profiles
        new = pid is None
        cur = None if new else self._log_profile(pid)
        if not new and cur is None:
            return
        if new:
            base = "Data" if kind == "interval" else lt.TYPE[lt.type_of({"type": kind})][1].replace(" / ", " ")
            pr = new_log_profile(kind, self._unique_name(base, {x["name"] for x in profs}))
        else:
            pr = cur
        taken = {dl.safe_name(x["name"]).lower() for x in profs if x["id"] != pr["id"]}
        kind = lt.type_of(pr)
        choices = self.vt_choices() if kind == "event" else self.channel_choices()
        dev = next((d.name for d in self.project.devices if d.enabled), "Device")
        dlg = LogProfileDialog(self, pr, choices, taken, [(a["id"], a["name"]) for a in self.project.alarms],
                               self.project.logging.folder, self.project.name, dev)
        dlg.ok_btn, title = popup_form(dlg, lt.icon_of(pr), f"New {lt.TYPE[kind][1].lower()} profile" if new
                                       else pr["name"], f"Logging profile · {lt.TYPE[kind][1]}",
                                       "Add profile" if new else "Save", width=640)
        dlg.name.textChanged.connect(lambda s_: title.setText(s_.strip() or pr["name"]))
        dlg._paint_icons()                           # the header shows the chosen icon from now on
        dlg.resize(dlg.width(), 560)
        dlg._check()
        if not dlg.exec():
            return
        res = dlg.result_profile()
        if new:
            profs.append(res)
        else:
            cur.clear(); cur.update(res)
        self._log_profiles_changed(res["id"])
        self.set_status(f"Logging profile '{res['name']}' " + ("added" if new else "saved"), "ok")

    def dup_log_profile(self, pid: str):
        pr = self._log_profile(pid)
        if pr is None or not self._recording_guard():
            return
        b = {**copy.deepcopy(pr), "id": uuid.uuid4().hex[:8]}
        b["name"] = self._next_name(pr["name"] if pr["name"][-1:].isdigit() else pr["name"] + " 1",
                                    {x["name"] for x in self.project.logging_profiles})
        self.project.logging_profiles.insert(self.project.logging_profiles.index(pr) + 1, b)
        self._log_profiles_changed(b["id"])

    def delete_log_profile(self, pid: str):
        pr = self._log_profile(pid)
        if pr is None or not self._recording_guard():
            return
        if not confirm(self, "Delete logging profile", f"Delete logging profile '{pr['name']}'?",
                       "Delete profile", "Recordings already made are not touched."):
            return
        self.project.logging_profiles.remove(pr)
        self.select_object(None)
        self._log_profiles_changed()

    def toggle_log_profile(self, pid: str):
        pr = self._log_profile(pid)
        if pr is not None and self._recording_guard():
            pr["enabled"] = not pr["enabled"]
            self._log_profiles_changed(pid)

    def test_log_profile(self, pid: str):
        """Trigger: a 5 s run now; event: one row now (while recording)."""
        g = getattr(self, "_gates", {}).get(pid)
        rec = self.recorder
        if g is None or rec is None:
            self.toast.popup("Record first - a test writes into the running session")
            return
        idx, gate, keys = g
        now = time.time()
        if gate.kind == "event":
            gate.runs += 1
            rec.event(idx, now, "Test", [self.tag_live(*k)[0] for k in keys])
            self.toast.popup("Test event written")
        elif gate.kind == "trigger" and not gate.active:
            gate.active = True; gate.runs += 1
            self.__dict__.setdefault("_runs", {}).setdefault(pid, []).append({"label": "Test", "start": now, "end": None})
            rec.gate(idx, True, now, "Test")

            def off():
                if self.recorder is rec and gate.active:
                    gate.active = False
                    rec.gate(idx, False, time.time(), "")
                    runs = self.__dict__.get("_runs", {}).get(pid) or []
                    if runs and runs[-1]["end"] is None:
                        runs[-1]["end"] = time.time()
            QTimer.singleShot(5000, off)
            self.toast.popup("Test run: 5 s")
        self.logging_page.update_live()

    # ---- where a profile writes (core/log_files.py) and its tags
    def _reachable(self, path: str) -> bool:
        """The folder exists, or the nearest existing parent can be written (cached 10 s - shares)."""
        cache = self.__dict__.setdefault("_reach_cache", {})
        hit = cache.get(path)
        if hit and time.time() - hit[1] < 10:
            return hit[0]
        p = os.path.abspath(path or ".")
        while p and not os.path.isdir(p):
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        ok = bool(p) and os.path.isdir(p) and os.access(p, os.W_OK)
        cache[path] = (ok, time.time())
        return ok

    def profile_folder(self, pr: dict) -> str:
        """Today's folder of a profile (its base folder + the date folders; session: the base)."""
        out = lf.normalize_output(copy.deepcopy(pr))
        base = out["folder"] or self.project.logging.folder
        if out["folders"] == "session":
            return base
        ctx = {"project": dl.safe_name(self.project.name), "profile": dl.safe_name(pr["name"])}
        return os.path.join(base, *lf.folder_parts(out, time.time(), ctx))

    def profile_live(self, pr: dict) -> dict:
        """The card of a profile: folder (+ note, bad), the file written now, files, bytes, rows,
        seconds to the next file."""
        out = lf.normalize_output(copy.deepcopy(pr))
        base = out["folder"] or self.project.logging.folder
        rec = self.recorder
        i = next((k for k, x in enumerate(rec.profiles) if x.get("id") == pr["id"]), None) if rec else None
        rate, per_day = self.profile_estimate(pr)
        info = {"folder": self.profile_folder(pr), "folder_note": "" if out["folder"] else "(default)",
                "folder_bad": False, "now": "", "files": 0, "bytes": 0, "rows": None, "next": None,
                "last": None, "rate": rate, "per_day": per_day, "measured": False, "stalled": False}
        if i is not None:
            now, files, size = rec.profile_files(i)
            info.update(now=now, files=files, bytes=size, rows=rec.written(i))
            lw = rec.last_write.get(i)
            info["last"] = time.time() - lw if lw else None
            if rec.elapsed > 60:                     # measured, once there is a minute of it
                info.update(rate=info["rows"] / rec.elapsed, per_day=size / rec.elapsed * 86400, measured=True)
            limit = self._stall_limit(pr) if pr["enabled"] else None
            quiet = info["last"] if info["last"] is not None else rec.elapsed
            info["stalled"] = bool(limit and quiet > limit and lt.type_of(pr) not in ("trigger", "batch", "event"))
            if now:
                info["folder"] = os.path.dirname(now)
            err = rec.errors.get(i)
            if err:
                info.update(folder_note=f"⚠ {err}", folder_bad=True)
        elif not self._reachable(base):
            info.update(folder_note="⚠ not reachable", folder_bad=True)
        nxt = lf.next_start(out, time.time())
        if nxt:
            info["next"] = nxt - time.time()
        return info

    def open_profile_folder(self, pid: str):
        pr = self._log_profile(pid)
        if pr is None:
            return
        d = self.profile_live(pr)["folder"]
        base = lf.normalize_output(copy.deepcopy(pr))["folder"] or self.project.logging.folder
        try:
            os.makedirs(d if os.path.isdir(os.path.dirname(d)) or d == base else base, exist_ok=True)
        except OSError:
            pass
        self._open_path(d if os.path.isdir(d) else base)

    def profile_tag_rows(self, pr: dict) -> list[tuple]:
        """The Logging card's tags: [("tag", key, its assets)] and [("asset", id, path, [keys])]."""
        A = self.project.assets
        if pr.get("channels") is None:
            keys = self._event_keys(pr) if lt.type_of(pr) == "event" else \
                [(d.id, t.index) for d in self.project.devices if d.enabled for t in d.tags if t.enabled]
            return [("tag", k, ", ".join(am.assets_of(A, k))) for k in keys]
        rows = [("tag", tuple(k), ", ".join(am.assets_of(A, k))) for k in pr["channels"]]
        for aid in pr.get("assets") or []:
            rows.append(("asset", aid, am.label(A, aid), am.keys(A, aid)))
        return rows

    def log_profile_add_tags(self, pid: str):
        """Add tags... of a profile: the picker with By tag / By asset (whole assets kept live)."""
        pr = self._log_profile(pid)
        if pr is None or not self._recording_guard():
            return
        kind = lt.type_of(pr)
        choices = self.vt_choices() if kind == "event" else self.channel_choices()
        chosen = [list(r[1]) for r in self.profile_tag_rows(pr) if r[0] == "tag"]
        dlg = PickDialog(self, choices, chosen, True, f"Tags of {pr['name']}", assets=True,
                         chosen_assets=pr.get("assets") or [])
        if not dlg.exec():
            return
        keys, aids = dlg.result_assets()
        if not keys and not aids:
            self.toast.popup("A profile needs at least one tag")
            return
        pr["channels"], pr["assets"] = [list(k) for k in keys], list(aids)
        self._log_profiles_changed(pid)

    def log_profile_remove(self, pid: str, keys, aids):
        """Remove tags / whole assets from a profile ("All enabled" becomes the list without them)."""
        pr = self._log_profile(pid)
        if pr is None or not self._recording_guard():
            return
        gone = {tuple(k) for k in keys}
        left = [list(r[1]) for r in self.profile_tag_rows(pr) if r[0] == "tag" and tuple(r[1]) not in gone]
        assets = [a for a in pr.get("assets") or [] if a not in set(aids)]
        if not left and not assets:
            self.toast.popup("A profile needs at least one tag - delete the profile instead")
            return
        pr["channels"], pr["assets"] = left, assets
        self._log_profiles_changed(pid)

    # ---- Logging health: rates, sizes, last write / stalled, files, runs, events
    def _rec_index(self, pr: dict):
        rec = self.recorder
        return next((k for k, x in enumerate(rec.profiles) if x.get("id") == pr["id"]), None) if rec else None

    def profile_estimate(self, pr: dict) -> tuple:
        """(rows / s, bytes / day) a profile is expected to write - None where it depends on the
        process (on change, trigger, event, batch)."""
        kind, w = lt.type_of(pr), pr.get("when") or {}
        topics = self._profile_topics(pr) if kind != "event" else []
        if kind == "interval":
            rows = 0.0
            for tp in topics:
                d = self.project.device(tp["id"])
                scan = 1000.0 / max(1, d.scan_ms) if d is not None else 1.0
                rows += 1.0 / w["every_s"] if w.get("every_s") else scan
            size = sum((24 + 10 * len(tp["channels"])) * (1.0 / w["every_s"] if w.get("every_s") else
                                                          1000.0 / max(1, getattr(self.project.device(tp["id"]),
                                                                                  "scan_ms", 1000)))
                       for tp in topics)
            return rows, size * 86400
        if kind == "statistic":
            iv = max(0.1, float(pr.get("interval_s") or 60))
            size = sum(40 + 10 * len(tp["channels"]) * len(pr.get("stats") or [1]) for tp in topics)
            return len(topics) / iv, size * 86400 / iv
        return None, None

    def _stall_limit(self, pr: dict) -> float | None:
        """Seconds without a row after which a writing profile is Stalled (None: may be silent)."""
        kind, w = lt.type_of(pr), pr.get("when") or {}
        if kind == "interval":
            scans = [self.project.device(tp["id"]).scan_ms / 1000.0 for tp in self._profile_topics(pr)
                     if self.project.device(tp["id"]) is not None]
            return max(10.0, 3 * (w.get("every_s") or max(scans or [1.0])))
        if kind == "statistic":
            return max(10.0, 3 * float(pr.get("interval_s") or 60))
        if kind == "change" and w.get("heartbeat_s"):
            return max(10.0, 3 * float(w["heartbeat_s"]))
        return None

    def log_health(self) -> dict:
        """The Logging health strip: recording, states, issues, disk, rows / s, dropped."""
        rec = self.recorder
        states, issues, per_day, rate = {}, [], 0.0, 0.0
        default = self.project.logging.folder
        drive = os.path.splitdrive(os.path.abspath(default))[0].lower()
        for pr in self.project.logging_profiles:
            st, _c = self.log_status(pr)
            states[st] = states.get(st, 0) + 1
            live = self.profile_live(pr)
            if st in ("stalled", "error") or (live["folder_bad"] and pr["enabled"]):
                issues.append((pr["id"], f"{pr['name']}: " + (live["folder_note"].lstrip("⚠ ") if live["folder_bad"]
                                                               else f"no row for {lt.fmt_s(live['last'] or 0)}")))
            if pr["enabled"]:
                base = lf.normalize_output(copy.deepcopy(pr))["folder"] or default
                if os.path.splitdrive(os.path.abspath(base))[0].lower() == drive and live["per_day"]:
                    per_day += live["per_day"]
                if rec is not None:
                    rate += live["rate"] or 0.0
        p = os.path.abspath(default)
        while p and not os.path.isdir(p) and os.path.dirname(p) != p:
            p = os.path.dirname(p)
        try:
            free = shutil.disk_usage(p).free
        except OSError:
            free = None
        return {"recording": rec is not None, "elapsed": rec.elapsed if rec else 0.0, "states": states,
                "issues": issues, "free": free, "drive": drive.upper() or p, "per_day": per_day,
                "days": (free / per_day) if free and per_day else None, "rate": rate,
                "dropped": rec.dropped if rec else 0, "profiles": len(self.project.logging_profiles)}

    def profile_files_list(self, pr: dict, days: int | None = 7) -> list[dict]:
        """This profile's files (its folder, newest first, at most 500): path, rel, mtime, size,
        state writing / complete (cached 10 s)."""
        cache = self.__dict__.setdefault("_files_cache", {})
        key = (pr["id"], days, json.dumps(pr.get("output"), sort_keys=True))
        hit = cache.get(key)
        if hit and time.time() - hit[0] < 10 and self.recorder is None:
            return hit[1]
        out = lf.normalize_output(copy.deepcopy(pr))
        base = out["folder"] or self.project.logging.folder
        ctx = {"project": dl.safe_name(self.project.name), "profile": dl.safe_name(pr["name"])}
        name = lf.name_pattern(out, lt.type_of(pr))[0]
        prefix = lf.keep_prefix(out, ctx) if "{profile}" in name else dl.safe_name(pr["name"])
        since = time.time() - days * 86400 if days else 0
        busy = self.recorder.open_paths if self.recorder else set()
        rows = []
        for root, dirs, files in os.walk(base):
            if root[len(base):].count(os.sep) >= 4:
                dirs[:] = []
            for fn in files:
                if not fn.startswith(prefix) or not fn.endswith((".csv", ".wpk", ".zst", ".gz")):
                    continue
                pth = os.path.join(root, fn)
                try:
                    st = os.stat(pth)
                except OSError:
                    continue
                if st.st_mtime >= since:
                    rows.append({"path": pth, "rel": os.path.relpath(pth, base), "mtime": st.st_mtime,
                                 "size": st.st_size, "state": "writing" if pth in busy else "complete"})
        rows.sort(key=lambda r: r["mtime"], reverse=True)
        rows = rows[:500]
        cache[key] = (time.time(), rows)
        return rows

    def profile_runs(self, pr: dict) -> list[dict]:
        """Trigger runs / batches of this app session: label, start, end (None = running), newest first."""
        return list(reversed(self.__dict__.get("_runs", {}).get(pr["id"], [])))

    def profile_events(self, pr: dict, limit: int = 200) -> tuple[list[str], list[list[str]]]:
        """(columns, the newest event rows) of an event profile's newest file."""
        files = [f for f in self.profile_files_list(pr, None) if f["path"].endswith(".csv")]
        if not files:
            return [], []
        try:
            with open(files[0]["path"], encoding="utf-8-sig") as f:
                lines = [ln for ln in f if not ln.startswith("#")]
        except OSError:
            return [], []
        rows = list(csv.reader(lines))
        return (rows[0], list(reversed(rows[1:]))[:limit]) if rows else ([], [])

    def profile_problems(self, pr: dict) -> list[tuple]:
        return list(reversed(self.__dict__.get("_log_problems", {}).get(pr["id"], [])))[:20]

    def _check_log_problems(self):
        """A profile that became stalled / failed: remembered for its Overview (newest 50)."""
        probs = self.__dict__.setdefault("_log_problems", {})
        last = self.__dict__.setdefault("_log_state", {})
        for pr in self.project.logging_profiles:
            st, _c = self.log_status(pr)
            if st in ("stalled", "error") and last.get(pr["id"]) != st:
                live = self.profile_live(pr)
                probs.setdefault(pr["id"], []).append(
                    (time.time(), live["folder_note"].lstrip("⚠ ") if st == "error" else
                     f"stalled - no row for {lt.fmt_s(live['last'] or 0)}"))
                del probs[pr["id"]][:-50]
            last[pr["id"]] = st

    # ---- Connectivity health and device diagnostics (the Connectivity document)
    def sample_devices(self):
        """Every stats tick: values per second of each device (from its driver's counter)."""
        rates = self.__dict__.setdefault("_dev_rates", {})
        now = time.time()
        for d in self.project.devices:
            drv = self.drivers.driver(d.id)
            if drv is None:
                rates.pop(d.id, None)
                continue
            t0, v0, r = rates.get(d.id, (now, drv.values, None))
            if now - t0 >= 2.0:
                rates[d.id] = (now, drv.values, (drv.values - v0) / (now - t0))
            elif d.id not in rates:
                rates[d.id] = (now, drv.values, None)

    def device_live(self, did: str) -> dict:
        """A device's health: state, last poll age, values / s, errors last hour, scan time, load,
        online since, stalled."""
        d = self.project.device(did)
        state, text = self.device_state(did)
        info = {"state": state, "text": text, "last": None, "rate": None, "err_h": 0, "scan_avg": None,
                "scan_max": None, "load": None, "since": None, "stalled": False, "scans": 0, "errors": 0}
        drv = self.drivers.driver(did)
        if d is None or drv is None:
            return info
        now = time.time()
        info.update(scans=drv.scans, errors=drv.errors,
                    err_h=sum(1 for t in list(drv.error_times) if now - t < 3600),
                    rate=self.__dict__.get("_dev_rates", {}).get(did, (0, 0, None))[2])
        if drv.last_ok:
            info["last"] = now - drv.last_ok
        recent = [ms for t, ms in list(drv.scan_times) if now - t < 60]
        if recent:
            info["scan_avg"], info["scan_max"] = sum(recent) / len(recent), max(recent)
            if not d.wave and d.scan_ms:
                info["load"] = info["scan_avg"] / d.scan_ms * 100.0
        if state == "online":
            info["since"] = next((t for t, st, _x in reversed(list(drv.events)) if st == "online"), None)
            limit = 30.0 if d.wave else max(10.0, 3 * d.scan_ms / 1000.0)
            quiet = info["last"] if info["last"] is not None else (now - info["since"] if info["since"] else 0)
            info["stalled"] = d.enabled and quiet > limit and bool(d.tags)
        return info

    def device_status(self, did: str) -> str:
        """online / stalled / connecting / error / stopped / disabled."""
        d = self.project.device(did)
        if d is None or not d.enabled:
            return "disabled"
        live = self.device_live(did)
        return "stalled" if live["stalled"] else live["state"]

    def conn_health(self) -> dict:
        """The Connectivity health strip."""
        states, issues, rate, err_h, load = {}, [], 0.0, 0, 0.0
        since = []
        for d in self.project.devices:
            st = self.device_status(d.id)
            states[st] = states.get(st, 0) + 1
            live = self.device_live(d.id)
            if st in ("error", "stalled"):
                issues.append((d.id, f"{d.name}: " + ((live["text"] or "error") if st == "error" else
                                                      f"no data for {int(live['last'] or 0)} s")))
            rate += live["rate"] or 0.0
            err_h += live["err_h"]
            load = max(load, live["load"] or 0.0)
            if live["since"]:
                since.append(live["since"])
        return {"running": self.drivers.any_running, "since": min(since) if since else None, "states": states,
                "issues": issues, "rate": rate, "err_h": err_h, "load": load, "devices": len(self.project.devices)}

    def device_diag(self, did: str, comm: bool | None = None) -> dict:
        """Diagnostics of a running device: blocks of the last scan, state events, the comm log
        (comm True / False: switch the log on / off)."""
        drv = self.drivers.driver(did)
        if drv is None:
            return {"blocks": [], "events": [], "comm": [], "running": False}
        if comm is not None:
            drv.comm_on = comm
        return {"blocks": list(drv.blocks), "events": list(drv.events), "comm": list(drv.comm), "running": True,
                "scan_times": list(drv.scan_times)}

    def clear_comm_log(self, did: str):
        drv = self.drivers.driver(did)
        if drv is not None:
            drv.comm.clear()

    def device_tag_rows(self, did: str) -> list[dict]:
        """The device page's tags: name, address, type, unit, assets, alarm level."""
        d = self.project.device(did)
        if d is None:
            return []
        out = []
        for t in d.tags:
            lvl, typ = self.alarm_engine.channel_state(d.id, t.index)
            out.append({"key": (d.id, t.index), "name": t.name, "enabled": t.enabled,
                        "address": describe_address(d.driver, "", t), "type": t.address.get("dtype", t.kind) if t.kind == "number"
                        else t.kind, "unit": t.unit, "assets": ", ".join(am.assets_of(self.project.assets, (d.id, t.index))),
                        "alarm": f"L{lvl} {typ}" if lvl else ""})
        return out

    # ---- summaries under the toolbars (ui/widgets/summary_strip.py)
    def open_device_page(self, did: str):
        """Connectivity > the device's connector page, the device selected."""
        d = self.project.device(did)
        if d is not None:
            self._show_doc("connectivity")
            self.conn_page.open_connector(d.driver, did)

    def tags_summary(self) -> dict:
        """Every tag of the project: good / bad / no data / disabled / in alarm / formula errors,
        values per second, and the first bad device tags (for the issue line)."""
        out = {"total": 0, "good": 0, "bad": 0, "nodata": 0, "disabled": 0, "alarm": 0, "formula": 0,
               "rate": sum((r[2] or 0.0) for r in self.__dict__.get("_dev_rates", {}).values()),
               "running": self.drivers.any_running, "bad_keys": [], "bad_device": None}
        keys = []
        for d in self.project.devices:
            for t in d.tags:
                out["total"] += 1
                if not (d.enabled and t.enabled):
                    out["disabled"] += 1
                else:
                    keys.append((d.id, t.index))
        for v in self.project.virtual_tags:
            out["total"] += 1
            if not v.get("enabled", True):
                out["disabled"] += 1
            else:
                keys.append(vt.vt_key(v["id"]))
            if v["id"] in self.vt_engine.errors:
                out["formula"] += 1
        for a in self.project.aggregations:
            out["total"] += 1
            if not a.get("enabled", True):
                out["disabled"] += 1
            else:
                keys.append(ag.agg_key(a["id"]))
            if a["id"] in self.agg_engine.errors:
                out["formula"] += 1
        for t in self.sys_tags():
            out["total"] += 1
            keys.append(t.key)
        for k in keys:
            good = self.tag_live(*k)[2]
            if good:
                out["good"] += 1
            elif good is False:
                out["bad"] += 1
                if not vt.is_vt(k[0]) and not ag.is_agg(k[0]) and not ss.is_sys(k[0]):
                    out["bad_keys"].append(k)
            else:
                out["nodata"] += 1
            if self.alarm_engine.channel_state(*k)[0]:
                out["alarm"] += 1
        if out["bad_keys"]:
            out["bad_device"] = out["bad_keys"][0][0]
        return out

    def tag_quality(self, key) -> str:
        """good / bad / nodata / disabled - the Tags summary's filters."""
        tid, idx = key
        d = self.project.device(tid)
        if d is not None:
            t = d.tag(idx)
            if not d.enabled or t is None or not t.enabled:
                return "disabled"
        elif vt.is_vt(tid):
            v = self.virtual_tag(tid)
            if v is None or not v.get("enabled", True):
                return "disabled"
        elif ag.is_agg(tid):
            a = self.aggregation(tid)
            if a is None or not a.get("enabled", True):
                return "disabled"
        good = self.tag_live(tid, idx)[2]
        return "good" if good else ("bad" if good is False else "nodata")

    def assets_summary(self) -> dict:
        """The plant: areas, machines, their tags good / no data, template slots not mapped,
        machines in alarm and without tags - and the first machine of each (Open machine)."""
        A = self.project.assets
        out = {"areas": len(am.areas(A)), "machines": 0, "tags": 0, "good": 0, "nodata": 0, "not_mapped": 0,
               "alarm": 0, "empty": 0, "first": {}}
        for m in am.machines(A, None):
            out["machines"] += 1
            if not m["attributes"]:
                out["empty"] += 1
                out["first"].setdefault("empty", m["id"])
            if any(not x["key"] for x in m["attributes"]):
                out["not_mapped"] += sum(1 for x in m["attributes"] if not x["key"])
                out["first"].setdefault("not_mapped", m["id"])
            st = am.status(A, m["id"], self.asset_state)
            if st == "alarm":
                out["alarm"] += 1
                out["first"].setdefault("alarm", m["id"])
        for k in am.keys(A, None):
            out["tags"] += 1
            if self.tag_live(*k)[2]:
                out["good"] += 1
            else:
                out["nodata"] += 1
                out["first"].setdefault("nodata", next((a["id"] for a in A if any(
                    x["key"] and tuple(x["key"]) == tuple(k) for x in a["attributes"])), None))
        return out

    def browse_log_folder(self):
        self._browse_log_folder()

    def open_log_folder(self):
        self._open_log_folder()

    # ---- trigger / batch / event gates (core/log_types.Gate) while recording
    def _event_keys(self, pr: dict) -> list[tuple]:
        """The tags an event profile snapshots: its tags and assets, or every enabled device tag."""
        if pr.get("channels") is None:
            return [(d.id, t.index) for d in self.project.devices if d.enabled for t in d.tags if t.enabled]
        keys = [tuple(k) for k in pr["channels"]]
        for aid in pr.get("assets") or []:
            keys += [k for k in am.keys(self.project.assets, aid) if k not in keys]
        return keys

    def _key_text(self, key) -> str:
        return self.tag_live(*key)[0] or ""

    def _update_gates(self):
        """Every stats tick while recording: trigger / batch windows, event conditions and times."""
        rec = self.recorder
        if rec is None:
            return
        now = time.time()
        value_of = lambda k: self.last_value(*k)[0]                      # noqa: E731
        for pid, (idx, gate, keys) in getattr(self, "_gates", {}).items():
            for ev in gate.update(now, value_of):
                self._gate_out(rec, idx, keys, ev)
        self._check_log_problems()

    def _gate_out(self, rec, idx: int, keys, ev):
        what, t, label_ = ev
        pid = rec.profiles[idx].get("id")
        runs = self.__dict__.setdefault("_runs", {}).setdefault(pid, [])
        if what == "on":
            runs.append({"label": label_, "start": t, "end": None})
        elif what == "off" and runs and runs[-1]["end"] is None:
            runs[-1]["end"] = t
        del runs[:-500]
        if what == "fire":
            rec.event(idx, t, label_, [self.tag_live(*k)[0] for k in keys])
        else:
            rec.gate(idx, what == "on", t, label_)
            self._log(f"Logging {'started' if what == 'on' else 'stopped'}: "
                      f"{rec.profiles[idx]['name']}" + (f" ({label_})" if label_ else ""))

    def _gate_events(self, source: str, ident: str, text: str, t: float):
        """An alarm came in / a Marker: the event profiles listening to it write a row."""
        rec = self.recorder
        if rec is None:
            return
        for pid, (idx, gate, keys) in getattr(self, "_gates", {}).items():
            evs = gate.alarm(ident, text, t) if source == "alarm" else gate.marker(text, t)
            for ev in evs:
                self._gate_out(rec, idx, keys, ev)

    def _marker_profiles(self) -> list[str]:
        return [pid for pid, (_i, g, _k) in getattr(self, "_gates", {}).items()
                if g.kind == "event" and g.w.get("source") == "marker"]

    def _update_lock(self):
        rec = self.recorder is not None
        self.act_rec.setEnabled(not rec)
        self.act_stop.setEnabled(rec)
        self.act_marker.setEnabled(self.ml_logger is not None or bool(self._marker_profiles()))
        if hasattr(self, "logging_page"):
            self.logging_page.refresh()
        if hasattr(self, "tags_page"):
            self.tags_page.update_states()
            self.conn_page.update_states()

    # ================================================================ recording
    def _start_recording(self):
        if self.recorder is not None:
            return
        profiles, self._gates = [], {}
        for pr in self.project.logging_profiles:
            if not pr["enabled"] or lt.problem(pr):
                continue
            if lt.type_of(pr) == "event":
                keys = self._event_keys(pr)
                if keys:
                    self._gates[pr["id"]] = (len(profiles), lt.Gate(pr, self._key_text), keys)
                    profiles.append({**copy.deepcopy(pr), "topics": [],
                                     "tags": [(self.key_label(k), self.last_value(*k)[1]) for k in keys]})
                continue
            tps = self._profile_topics(pr)
            if tps:
                if lt.type_of(pr) in ("trigger", "batch"):
                    self._gates[pr["id"]] = (len(profiles), lt.Gate(pr, self._key_text), [])
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
        if rec.errors:                               # a folder that cannot be made: that profile only
            self.banners.post("rec", "Not logged: " + "; ".join(f"{rec.profiles[i]['name']} ({e})"
                                                              for i, e in rec.errors.items()), "warn")
        self._update_gates()                         # a trigger already true / the running shift
        self.banners.dismiss("rec")
        if self.ml_logger is not None:
            self.ml_logger.event("record_start", "Record", rec.dir)
        self._update_lock(); self.logging_page.refresh()
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
        self._gates_done = {pid: g.runs for pid, (_i, g, _k) in self._gates.items()}   # the counts stay
        self._gates = {}
        self.busy(True)
        with self._waiting():
            rec.stop()
        self.busy(False)
        if self.ml_logger is not None:
            self.ml_logger.event("record_stop", "Record", rec.dir)
        self._update_lock(); self.logging_page.refresh()
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
        self.act_marker.setEnabled(on or bool(self._marker_profiles()))
        self.ml_label.setVisible(on)

    def _ml_settings(self):
        self.open_editor("mllog", "settings")

    def _ml_marker(self, kind: str):
        """Event marker: events.csv of the AI/ML log (the AI app turns these into draft labels) and
        the event logging profiles listening to the Marker."""
        if self.ml_logger is None and not self._marker_profiles():
            return
        t = time.time()                          # the moment of the click, not of the OK
        dlg = MarkerDialog(self, MARKER_TYPES, kind, t)
        if not dlg.exec():
            return
        kind, text = dlg.result()
        title = dict(MARKER_TYPES)[kind].split(" (")[0]
        self._gate_events("marker", "", f"{title}: {text}" if text else title, t)
        if self.ml_logger is not None:
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
        self._update_gates()
        if hasattr(self, "logging_page"):            # rows per minute: sampled on every page
            self.logging_page.sample_activity()
        self.sample_devices()
        self._update_nav_dots()
        page = self.doc_tabs.currentWidget()
        if isinstance(page, dv.ViewPage):
            page.update_values()
        elif page is self.tags_page:
            self.tags_page.update_live()
            self.tags_page.update_states()
        elif page is self.conn_page:
            self.conn_page.update_states()
        elif page is getattr(self, "start_page", None):
            self.start_page.update_live()
        elif page is getattr(self, "assets_page", None):
            self.assets_page.update_live()
        elif page is getattr(self, "logging_page", None) and self.recorder is not None:
            self.logging_page.update_live()
        self._asset_tick = (getattr(self, "_asset_tick", 0) + 1) % 4
        if not self._asset_tick:                     # the status dots and rollups: every 4th update
            self._update_asset_status(page)
        if self._monitor is not None:
            self.monitor_bar.update_live()
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
            self.add_alarm("limit", channel)

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
            self._gate_events("alarm", ev["alarm_id"], f"{lvl} {ev['type']} {name}", ev.get("t") or time.time())
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

    def add_virtual(self, kind: str):
        if self._recording_guard():
            QTimer.singleShot(0, lambda: self._vt_popup(None, kind))

    def edit_virtual(self, vid: str):
        QTimer.singleShot(0, lambda: self._vt_popup(vid))

    def _vt_pick(self, choices, chosen, multi: bool, title: str):
        dlg = PickDialog(self, choices, chosen, multi, title)
        return dlg.result_keys() if dlg.exec() else None

    def _vt_popup(self, vid: str | None = None, kind: str = "formula"):
        """Add / Edit virtual tag pop-up (tabs): the tag joins the project or changes only on Add tag / Save."""
        new = vid is None
        cur = None if new else self.virtual_tag(vid)
        if not new and cur is None:
            return
        if new:
            v = vt.new_virtual(kind)
            v["name"] = self._unique_name(v["name"], {x["name"] for x in self.project.virtual_tags})
        else:
            v = copy.deepcopy(cur)
        taken = {x["name"].strip().lower() for x in self.project.virtual_tags if x["id"] != v["id"]}
        dlg = VirtualTagDialog(self, v, self.vt_engine, self.vt_choices(), self._vt_names(),
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

    def add_aggregation(self, kind: str):
        if self._recording_guard():
            QTimer.singleShot(0, lambda: self._agg_popup(None, kind))

    def edit_aggregation(self, aid: str):
        QTimer.singleShot(0, lambda: self._agg_popup(aid))

    def _agg_popup(self, aid: str | None = None, kind: str = "totalizer"):
        """Add / Edit aggregation tag pop-up (tabs): joins the project or changes only on Add tag / Save."""
        new = aid is None
        cur = None if new else self.aggregation(aid)
        if not new and cur is None:
            return
        if new:
            a = ag.new_aggregation(kind)
            a["name"] = self._unique_name(a["name"], {x["name"] for x in self.project.aggregations})
        else:
            a = copy.deepcopy(cur)
        taken = {x["name"].strip().lower() for x in self.project.aggregations if x["id"] != a["id"]}
        choices = [c for c in self.channel_choices() if not (ag.is_agg(c[0]) and c[0] == ag.AGG_PREFIX + a["id"])]
        dlg = AggregationDialog(self, a, self.agg_engine, choices, self._vt_names(),
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

    # ================================================================ Monitor mode (full-screen views)
    MON_KEY = "monitor"                              # QSettings: last view, full screen, cycle (per PC)

    def toggle_monitor(self):
        if self._monitor is None:
            self.enter_monitor()
        else:
            self.exit_monitor()

    def enter_monitor(self, vid: str | None = None):
        """Only the views, full size: menu, toolbar, panels, tabs and status bar hidden, the slim
        Monitor bar on top. The layout before comes back on exit."""
        views = self.project.views
        if not views:
            self.toast.popup("Add a view first (Start Page > Views > New view...)")
            return
        if self._monitor is not None:
            if vid:
                self.monitor_show(vid)
            return
        page = self.doc_tabs.currentWidget()
        if vid is None:                              # the view in front, else the last one, else the first
            vid = next((k for k, p in self.view_pages.items() if p is page), None) or \
                str(QSettings().value(f"{self.MON_KEY}/view", "")) or views[0]["id"]
        if not any(v["id"] == vid for v in views):
            vid = views[0]["id"]
        self._monitor = {"state": self.saveState(), "current": page, "max": self.isMaximized(),
                         "full": self.isFullScreen(), "menu": self.menuBar().isVisible(),
                         "tool": self.toolbar.isVisible(), "status": self.statusBar().isVisible()}
        for p in self.view_pages.values():           # watching, not editing
            if p.editing:
                p.btn_edit.setChecked(False)
            p.bar.hide()
        for d in self.findChildren(QDockWidget):
            d.hide()
        self.menuBar().hide(); self.toolbar.hide(); self.statusBar().hide()
        self.doc_tabs.tabBar().hide()
        self.monitor_bar.show()
        s = QSettings()
        full = s.value(f"{self.MON_KEY}/fullscreen", False) in (True, "true", 1, "1")
        cycle = int(s.value(f"{self.MON_KEY}/cycle_s", 0) or 0)
        self.monitor_bar.set_options(full, cycle)
        self.monitor_show(vid)
        self.set_monitor_fullscreen(full)
        self.set_monitor_cycle(cycle)
        self.monitor_bar.update_live()
        self._log("Monitor mode on")

    def monitor_show(self, vid: str):
        if f"view:{vid}" not in self.docs:
            return
        self._show_doc(f"view:{vid}")
        self.monitor_bar.fill(vid)
        QSettings().setValue(f"{self.MON_KEY}/view", vid)

    def set_monitor_fullscreen(self, on: bool):
        if self._monitor is None:
            return
        QSettings().setValue(f"{self.MON_KEY}/fullscreen", bool(on))
        if on and not self.isFullScreen():
            self.showFullScreen()
        elif not on and self.isFullScreen():
            self.showMaximized() if self._monitor["max"] else self.showNormal()

    def set_monitor_cycle(self, seconds: int):
        QSettings().setValue(f"{self.MON_KEY}/cycle_s", int(seconds or 0))
        self._mon_cycle.stop()
        if seconds and self._monitor is not None and len(self.project.views) > 1:
            self._mon_cycle.start(int(seconds) * 1000)

    def exit_monitor(self, to: str | None = None):
        """Back to the layout before: panels, tabs, menu, toolbar, status bar, window size."""
        m = self._monitor
        if m is None:
            return
        self._monitor = None
        self._mon_cycle.stop()
        self.monitor_bar.hide()
        for p in self.view_pages.values():
            p.bar.show()
        self.doc_tabs.tabBar().show()
        self.menuBar().setVisible(m["menu"]); self.toolbar.setVisible(m["tool"])
        self.statusBar().setVisible(m["status"])
        if self.isFullScreen() and not m["full"]:
            self.showMaximized() if m["max"] else self.showNormal()
        self.restoreState(m["state"])
        if to:
            self._show_doc(to)
        self._log("Monitor mode off")

    # ================================================================ Assets (core/assets.py)
    def assets_of(self, key) -> list[str]:
        return am.assets_of(self.project.assets, key)

    def key_source(self, key) -> str:
        tid = key[0]
        if vt.is_vt(tid):
            return "Virtual"
        if ag.is_agg(tid):
            return "Aggregation"
        if ss.is_sys(tid):
            return "System"
        if vr.is_var(tid):
            return "Variables"
        d = self.project.device(tid)
        return d.name if d else "(deleted)"

    def select_key(self, key):
        """Properties of any tag key (device, virtual, aggregation, system)."""
        tid, idx = key
        self.select_object(("agg", tid[len(ag.AGG_PREFIX):]) if ag.is_agg(tid) else
                           ("vt", tid[len(vt.VT_PREFIX):]) if vt.is_vt(tid) else
                           ("sys", tid) if ss.is_sys(tid) else
                           None if vr.is_var(tid) else ("tag", tid, idx))

    def open_tag(self, key):
        """A tag's own pop-up (System Tags: the Tags page - they are read-only)."""
        tid, idx = key
        if ag.is_agg(tid):
            self.edit_aggregation(tid[len(ag.AGG_PREFIX):])
        elif vt.is_vt(tid):
            self.edit_virtual(tid[len(vt.VT_PREFIX):])
        elif ss.is_sys(tid):
            self._show_doc("tags"); self.tags_page.select_tag(tid, idx)
        elif self.project.tag(tid, idx) is not None:
            QTimer.singleShot(0, lambda: self.edit_tag(tid, idx))

    def enterprise_name(self) -> str:
        return self.project.enterprise or self.project.name

    def edit_enterprise(self):
        """The Enterprise is the project: its pop-up (Enterprise field)."""
        QTimer.singleShot(0, self._project_popup)

    def asset_state(self, key) -> str:
        """alarm (an active alarm level) / ok (good data) / none (no data yet, bad quality)."""
        if self.alarm_engine.channel_state(*key)[0]:
            return "alarm"
        return "ok" if self.tag_live(*key)[2] else "none"

    def asset_value(self, key):
        """(value or None, unit) - the rollups."""
        return self.last_value(*key)

    def _update_asset_status(self, page):
        """The status dots of the Project tree's areas and (shown) of the Assets document."""
        A = self.project.assets
        for aid, it in list(getattr(self, "_asset_nodes", {}).items()):
            a = am.by_id(A).get(aid)
            if a is not None:
                st = am.status(A, aid, self.asset_state)
                it.setIcon(0, make_icon(am.icon_of(a), T["bad"] if st == "alarm" else T["text"]))
        if page is getattr(self, "assets_page", None):
            self.assets_page.refresh_status()

    def assets_changed(self, select=False):
        self._mark_dirty()
        if hasattr(self, "assets_page"):
            self.assets_page.refresh(select)
        self.tags_page.refresh_assets()
        self._rebuild_project_tree()
        self._refresh_properties()
        if hasattr(self, "start_page"):
            self.start_page.refresh()

    def asset(self, aid: str) -> dict | None:
        return am.by_id(self.project.assets).get(aid)

    def asset_template(self, tid: str) -> dict | None:
        return next((t for t in self.project.asset_templates if t["id"] == tid), None)

    def _asset_pick(self, chosen):
        return self._vt_pick(self.vt_choices(), [list(k) for k in chosen], True, "Add tags to the asset")

    @staticmethod
    def _next_name(name: str, taken: set) -> str:
        """'Compressor 2' -> 'Compressor 3' (the next free number), else 'Name 2'."""
        m = re.match(r"^(.*?)(\d+)$", name)
        base, n = (m.group(1), int(m.group(2))) if m else (name + " ", 1)
        while True:
            n += 1
            if f"{base}{n}" not in taken:
                return f"{base}{n}"

    def add_asset(self, typ: str = "area", parent: str | None = None, template: str | None = None, keys=()):
        """A new area (parent None) or machine (in `parent`, optionally from a template)."""
        QTimer.singleShot(0, lambda: self._asset_popup(None, typ, parent, template, keys))

    def edit_asset(self, aid: str):
        QTimer.singleShot(0, lambda: self._asset_popup(aid))

    def _asset_popup(self, aid: str | None = None, typ: str = "area", parent: str | None = None,
                     template: str | None = None, keys=()):
        """Add / Edit asset pop-up (tabs General / Tags): changes only on Add / Save."""
        A = self.project.assets
        new = aid is None
        cur = None if new else self.asset(aid)
        if not new and cur is None:
            return
        if new:
            if typ in {t[0] for t in am.LEVEL3} and self.asset(parent) is None:
                self.toast.popup("Add an area first - a machine goes into an area")
                return
            if typ in {t[0] for t in am.LEVEL2}:
                parent = None
            t = self.asset_template(template) if template else None
            base = t["name"] if t else am.TYPE[typ][1]
            taken = {x["name"] for x in am.children(A, parent)}
            first = f"{base} 1"
            a = am.new_asset(first if first not in taken else self._next_name(first, taken), typ, parent)
            if t is not None:
                am.apply_template(a, t)
            am.add_attributes(a, keys)
        else:
            a = copy.deepcopy(cur)
        dlg = AssetDialog(self, a, A, self.project.asset_templates, self.key_label, self._asset_pick)
        kind = am.type_name(a)
        where = am.path(A, a["parent"]) if a.get("parent") else self.enterprise_name()
        dlg.ok_btn, title = popup_form(dlg, am.icon_of(a), f"New {kind.lower()}" if new else a["name"],
                                       f"{kind} · {where}", "Add" if new else "Save", width=620)
        dlg.name.textChanged.connect(lambda s_: title.setText(s_.strip() or (f"New {kind.lower()}" if new
                                                                             else a["name"])))
        dlg.resize(dlg.width(), 540)
        dlg._check()
        if not dlg.exec():
            return
        if new:
            dlg.apply(a)
            A.append(a)
        else:
            dlg.apply(cur)
            a = cur
        self.assets_changed(a["id"])
        self.set_status(f"{am.type_name(a)} '{a['name']}' " + ("added" if new else "saved"), "ok")

    def delete_asset(self, aid: str):
        a = self.asset(aid)
        if a is None:
            return
        A = self.project.assets
        subs = am.children(A, aid)
        n = len(am.keys(A, aid))
        if not confirm(self, f"Delete {am.type_name(a).lower()}", f"Delete {am.type_name(a).lower()} '{a['name']}'" +
                       (f" and its {len(subs)} machine(s)?" if subs else "?"), "Delete",
                       f"{n} tag reference(s) go with it - the tags themselves stay. Logging profiles that use it "
                       "log its tags no longer."):
            return
        self.project.assets = am.delete(A, aid)
        gone = {aid} | {x["id"] for x in subs}
        for pr in self.project.logging_profiles:
            if pr.get("assets"):
                pr["assets"] = [x for x in pr["assets"] if x not in gone]
        self.select_object(None)
        self.assets_changed(a.get("parent"))

    def duplicate_asset(self, aid: str):
        """A copy next to it ('Compressor 2' -> 'Compressor 3'); an area with its machines."""
        A = self.project.assets
        a = self.asset(aid)
        if a is None:
            return
        y = copy.deepcopy(a)
        y["id"] = am.new_asset()["id"]
        y["name"] = self._next_name(a["name"], {x["name"] for x in am.children(A, a.get("parent"))})
        A.insert(A.index(a) + 1, y)
        for m in am.children(A, aid) if a.get("parent") is None else []:
            z = copy.deepcopy(m)
            z["id"], z["parent"] = am.new_asset()["id"], y["id"]
            A.append(z)
        self.assets_changed(y["id"])

    def move_asset(self, aid: str, parent: str):
        msg = am.can_move(self.project.assets, aid, parent)
        if msg:
            self.toast.popup(msg)
            return
        self.asset(aid)["parent"] = parent
        self.assets_changed(aid)

    def asset_add_tags(self, aid: str | None):
        a = self.asset(aid) if aid else None
        if a is None:
            self.toast.popup("Select an area or a machine first")
            return
        keys = self._asset_pick([tuple(x["key"]) for x in a["attributes"] if x["key"]])
        if keys is None:
            return
        chosen = {tuple(k) for k in keys}
        a["attributes"] = [x for x in a["attributes"] if not x["key"] or tuple(x["key"]) in chosen]  # unticked: out
        am.add_attributes(a, keys)
        self.assets_changed(aid)

    def add_to_asset(self, keys, target: str | None):
        """Tags > Add to asset ▸: an asset id, or None = a new area (its pop-up, with the tags)."""
        if target is None:
            self.add_asset("area", None, None, [tuple(k) for k in keys])
            return
        a = self.asset(target)
        if a is not None:
            n = am.add_attributes(a, keys)
            self.assets_changed()
            self.toast.popup(f"{n} tag(s) added to {am.label(self.project.assets, target)}" if n else
                             "Already in the asset")

    def remove_asset_rows(self, rows):
        """[(asset id, key | None, slot name)] -> out of their assets (the tags stay)."""
        for aid, key, name in rows:
            a = self.asset(aid)
            if a is None:
                continue
            if key is not None:
                a["attributes"] = [x for x in a["attributes"] if not x["key"] or tuple(x["key"]) != tuple(key)]
            else:
                a["attributes"] = [x for x in a["attributes"] if x["key"] or x["name"] != name]
        self.assets_changed()

    def _map_sources(self) -> list[tuple]:
        """Map tags... sources: each device, Virtual, Aggregation, System, All tags - [(name, [(key, tag name)])]."""
        by: dict[str, list] = {}
        for c in self.vt_choices():
            k = (c[0], c[1])
            by.setdefault(self.key_source(k), []).append((k, self.key_label(k).split(" · ")[-1]))
        out = [(d.name, by.pop(d.name)) for d in self.project.devices if d.name in by]
        out += [(n, by.pop(n)) for n in ("Virtual", "Aggregation", "System", "Variables") if n in by]
        out += list(by.items())
        out.append(("All tags", [x for _n, xs in out for x in xs]))
        return out

    def map_asset_tags(self, aid: str):
        """Fill a machine's empty attributes (template slots) with tags of one source, by name."""
        a = self.asset(aid)
        if a is None:
            return
        if not any(not x["key"] for x in a["attributes"]):
            self.toast.popup(f"Every attribute of {a['name']} has a tag - Apply template adds more")
            return
        t = self.asset_template(a.get("template"))
        mapped = [self.key_source(tuple(x["key"])) for x in a["attributes"] if x["key"]]
        src = max(set(mapped), key=mapped.count) if mapped else ""
        dlg = MapTagsDialog(self, a, t, self._map_sources(), self.key_label, src)
        if not dlg.exec():
            return
        n = am.apply_mapping(a, dlg.result())
        self.assets_changed(aid)
        self.set_status(f"{n} tag(s) mapped in {a['name']}", "ok")

    def apply_template(self, aid: str, tid: str):
        a, t = self.asset(aid), self.asset_template(tid)
        if a is None or t is None or a.get("parent") is None:
            return
        n = am.apply_template(a, t)
        self.assets_changed(aid)
        self.toast.popup(f"{t['name']}: {n} attribute(s) added - Map tags... fills them" if n else
                         f"{a['name']} now uses {t['name']}")

    def add_template(self, from_aid: str | None = None):
        """A new template - or one with a machine's attributes (that machine then uses it)."""
        a = self.asset(from_aid) if from_aid else None
        if a is not None:
            t = am.template_from(a, lambda k: self.key_label(k).split(" · ")[-1], lambda k: self.last_value(*k)[1])
        else:
            t = am.new_template(f"Template {len(self.project.asset_templates) + 1}")
        t["name"] = self._unique_name(t["name"], {x["name"] for x in self.project.asset_templates})
        QTimer.singleShot(0, lambda: self._template_popup(None, t, from_aid))

    def edit_template(self, tid: str):
        QTimer.singleShot(0, lambda: self._template_popup(tid))

    def _template_popup(self, tid: str | None = None, t: dict | None = None, from_aid: str | None = None):
        temps = self.project.asset_templates
        new = tid is None
        cur = None if new else self.asset_template(tid)
        if not new and cur is None:
            return
        t = t if new else copy.deepcopy(cur)
        dlg = TemplateDialog(self, t, temps, am.template_used(self.project.assets, t["id"]))
        dlg.ok_btn, title = popup_form(dlg, am.icon_of(t), "New template" if new else t["name"],
                                       "Template · what a kind of machine has", "Add template" if new else "Save",
                                       width=600)
        dlg.name.textChanged.connect(lambda s_: title.setText(s_.strip() or ("New template" if new else t["name"])))
        dlg.resize(dlg.width(), 520)
        dlg._check()
        if not dlg.exec():
            return
        if new:
            dlg.apply(t)
            temps.append(t)
            a = self.asset(from_aid) if from_aid else None
            if a is not None:
                a["template"] = t["id"]
        else:
            dlg.apply(cur)
            t = cur
            for m in self.project.assets:            # its machines get new attributes as slots
                if m.get("template") == t["id"]:
                    am.apply_template(m, t)
        self.assets_changed(t["id"])
        self.set_status(f"Template '{t['name']}' " + ("added" if new else "saved"), "ok")

    def delete_template(self, tid: str):
        t = self.asset_template(tid)
        if t is None:
            return
        n = am.template_used(self.project.assets, tid)
        if not confirm(self, "Delete template", f"Delete template '{t['name']}'?", "Delete template",
                       f"{n} machine(s) use it - they keep their tags." if n else "No machine uses it."):
            return
        self.project.asset_templates.remove(t)
        for m in self.project.assets:
            if m.get("template") == tid:
                m["template"] = ""
        self.select_object(None)
        self.assets_changed(None)

    def duplicate_template(self, tid: str):
        t = self.asset_template(tid)
        if t is None:
            return
        y = copy.deepcopy(t)
        y["id"] = am.new_template()["id"]
        y["name"] = self._unique_name(f"{t['name']} copy", {x["name"] for x in self.project.asset_templates})
        self.project.asset_templates.append(y)
        self.assets_changed(y["id"])

    def add_starter(self, key: str):
        """Use a starter ▾: an area and its templates (templates with the same name are reused)."""
        area, temps = am.starter(key)
        A = self.project.assets
        area["name"] = self._unique_name(area["name"], {x["name"] for x in am.areas(A)})
        A.append(area)
        have = {x["name"].lower() for x in self.project.asset_templates}
        added = [t for t in temps if t["name"].lower() not in have]
        self.project.asset_templates += added
        self.assets_changed(area["id"])
        self.set_status(f"Area '{area['name']}' added" + (f" with {len(added)} template(s): "
                                                          + ", ".join(t["name"] for t in added) if added else ""), "ok")

    def monitor_asset(self, aid: str):
        """Monitor in one click: its view (made once, then reused by name) full screen."""
        a = self.asset(aid)
        if a is None:
            return
        name = am.path(self.project.assets, aid)
        vw = next((v for v in self.project.views if v["name"] == name), None)
        if vw is None:
            self.new_view_from(am.keys(self.project.assets, aid), name)
            vw = next((v for v in self.project.views if v["name"] == name), None)
            if vw is None:                           # no number tags
                return
        QTimer.singleShot(0, lambda: self.enter_monitor(vw["id"]))

    def kpi_values(self, limit: int | None = None, where: str | None = None) -> list[tuple]:
        """The KPI attributes of the assets, each tag once, in tree order: [(name, value text, unit,
        good, key)] (Start Page "start", Monitor bar "monitor": the same). good: True / False / None."""
        out = []
        for _aid, x in am.kpi_rows(self.project.assets):
            k = tuple(x["key"])
            text, _q, good, _t = self.tag_live(*k)
            out.append((x.get("name") or self.key_label(k).split(" · ")[-1], text, self.last_value(*k)[1], good, k))
            if limit and len(out) >= limit:
                break
        return out

    def new_view_from(self, keys, name: str | None = None):
        """A view with a trend (number tags, at most 8) and a tag table of the keys."""
        keys = [tuple(k) for k in keys]
        numeric = {(c[0], c[1]) for c in self.channel_choices()}
        nums = [list(k) for k in keys if k in numeric]
        if not nums:
            self.toast.popup("No number tags to show")
            return
        taken = {v["name"] for v in self.project.views}
        vw = vmod.new_view(self._unique_name(name or f"View {len(self.project.views) + 1}", taken))
        vw["tiles"] = [vmod.new_tile("text", 10, 10, w=1180, h=60, text=vw["name"], style="H1", show_title=False),
                       vmod.new_tile("plot", 10, 80, w=1180, h=320, channels=nums[:8], title="Trend"),
                       vmod.new_tile("tags", 10, 420, w=1180, h=min(600, 60 + 26 * len(nums)), channels=nums,
                                     title="Values")]
        self.project.views.append(vw)
        self._open_view_page(vw, editing=False)
        self._mark_dirty()
        self._stat_dirty = True
        self._rebuild_project_tree()
        self.set_status(f"View '{vw['name']}' made from {len(nums)} tag(s)", "ok")

    def alarms_for_asset(self, aid: str):
        """One alarm per number tag: data loss after 10 s (edit the limits afterwards)."""
        A = self.project.assets
        numeric = {(c[0], c[1]) for c in self.channel_choices()}
        have = {tuple(a["channel"]) for a in self.project.alarms if a.get("channel")}
        keys = [k for k in am.keys(A, aid) if k in numeric and k not in have]
        if not keys:
            self.toast.popup("Every number tag already has an alarm")
            return
        path = am.path(A, aid)
        if not confirm(self, "Add alarms", f"Add {len(keys)} alarm(s) for {path}?", f"Add {len(keys)} alarms",
                       "Each starts with a data-loss level (no data for 10 s) - set the limits in each alarm.",
                       danger=False):
            return
        prof = [p["id"] for p in self.project.alarm_profiles[:1]]
        for k in keys:
            a = ar.new_alarm(k, f"{path} · {self.key_label(k).split(' · ')[-1]}")
            a["levels"][0].update(enabled=True, type="D", value=10.0)
            a["profiles"] = prof
            self.project.alarms.append(a)
        self._mark_dirty()
        self._alarms_reload()
        self.set_status(f"{len(keys)} alarm(s) added for {path}", "ok")

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
        if show:                                 # a loaded project's views open as tabs when used
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
            QTimer.singleShot(0, lambda: self._view_popup(page))

    def _view_popup(self, page):
        """View pop-up (tabs, like devices and tags): name, size, background, grid, cards - on Save."""
        v = page.view
        dlg = ViewDialog(self, v, {x["name"] for x in self.project.views if x is not v}, vmod.view_extent(v))
        dlg.ok_btn, title = popup_form(dlg, "layout", v["name"],
                                       f"View · {v.get('width', 1920)} × {v.get('height', 1080)} px · "
                                       f"{len(v['tiles'])} component(s)", "Save", width=520)
        dlg.name.textChanged.connect(lambda s_: title.setText(s_.strip() or v["name"]))
        if not dlg.exec():
            return
        dlg.apply(v)
        self.view_props_changed(page, "name")
        self._refresh_properties()
        self.set_status(f"View '{v['name']}' saved", "ok")

    def edit_component(self, page, cfg: dict):
        """A view component's Edit... (host API of views.py): its pop-up."""
        QTimer.singleShot(0, lambda: self._tile_popup(page, cfg))

    def new_component(self, page, cfg: dict):
        """Add > ... on a view: the component's pop-up first ("New component", Add)."""
        QTimer.singleShot(0, lambda: self._tile_popup(page, cfg, new=True))

    def _tile_popup(self, page, cfg: dict, new: bool = False):
        """Component pop-up (tabs per kind + Position): changes only on Save / Add."""
        if not new and page.tile_of(cfg) is None:
            return
        v = page.view
        dlg = TileDialog(self, cfg, self.channel_choices(), (int(v.get("width", 1920)), int(v.get("height", 1080))),
                         int(v.get("grid", vmod.GRID)) if v.get("snap", True) else 1)
        kind = vmod.tile_title(cfg)
        icon = {"plot": "chart", "text": "heading", "value": "hash", "gauge": "gauge", "led": "circle-dot",
                "stats": "sigma", "alarms": "bell", "tags": "tag"}.get(cfg["kind"], "layout")
        dlg.ok_btn, title = popup_form(dlg, icon, "New component" if new else (cfg.get("title") or kind),
                                       f"{page.name} · {kind}", "Add" if new else "Save", width=600)
        if not dlg.exec():
            return
        cfg.update(dlg.result_cfg())
        if new:
            page.insert_tile(cfg)
            page.tile_changed(cfg, geometry=True)
        else:
            page.tile_changed(cfg, geometry=True)
        self._stat_dirty = True
        self._refresh_properties()
        self.set_status(f"{kind} " + ("added" if new else "saved"), "ok")

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
            if isinstance(btn, QToolButton) and a not in (self.act_left, self.act_panel, self.act_log,
                                                          *self._icon_only):
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
