"""
connectivity.py - the Connectivity document (like FUXA Connections / Genesis64 Connectivity):
the devices and whether they talk right now. Tags are managed in the Tags document (tags_page.py).

Devices come in three categories (core.tags.CONN_CATEGORIES, also the Project tree's
Connectivity nodes):
  Protocols     Modbus TCP / RTU, OPC UA, MQTT, Simulation (OPC DA planned)
  Database      MySQL, PostgreSQL, InfluxDB                                   (planned)
  Web Services  REST / HTTP, Webhook, WebSocket, Firebase, MCP Server         (planned)

  health strip  ● CONNECTED since … / ○ Not connected · devices online / connecting / stalled /
                error, the first issue as a link (opens that device), values/s, errors in the last
                hour, the highest poll load
  bar    add (the connectors of the category), edit, duplicate, delete | connect, disconnect |
         import..., export... | columns, filter - fixed buttons, nothing appears or renames
  The category (Project tree > Protocols ...)
    left   the category as a band, All connections, one row per connector (count, icon in the
           worst state colour; muted when none / planned) - a click filters the table, a
           double-click opens the connector
    right  the devices: name (state icon), connector, address, scan, tags, last poll, values/s,
           errors/h, status (Stalled: online but no data for 3 scans), enabled, description
  A connector (double-click it)
    left   band = the connector (count), its devices as three-line rows (name / address · tags /
           scan · values/s, or the error) - Backspace / the Project tree goes back
    right  the device: a header (name, status · last poll, Edit, ⋯), its address (connect /
           disconnect, copy), tabs Overview (a sentence, key values: values/s, scan time, load,
           errors, online since; scan time per minute; problems) · Tags (address, type, live
           value, quality, assets, alarm) · Diagnostics (the requests of the last scan, the comm
           log while the tab is open) · History (online share per hour today, state changes)

Select a row: its properties in the Properties panel; double-click (or Enter): the device
settings pop-up. Device rows have a right-click menu: Edit, Open tags, Connect / Disconnect,
Enable / Disable, Duplicate, Export, Delete.

Host API (duck-typed, DaqWindow):
  host.project, host.reg_icon(target, name, token), host.recording (bool)
  host.device_state(did) -> (state, text) / host.device_status(did) / host.device_live(did) / host.conn_health()
  host.device_diag(did, comm) / host.clear_comm_log(did) / host.device_tag_rows(did)
  host.tag_live(tid, idx) / host.select_object(ref) / host.select_key(key) / host.open_tag(key)
  host.add_device(driver) / host.edit_device(did) / host.duplicate_device(did) / host.open_tags(did)
  host.add_tags(did, bulk) / host.start_device(did) / host.stop_device(did)
  host.delete_devices([did]) / host.set_devices_enabled([did], on) -> bool
  host.import_devices() / host.export_devices_dialog([did])     (pop-ups)
"""
from __future__ import annotations

import datetime as _dt
import time

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMenu, QSplitter, QStackedWidget, QTabWidget,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.core.tags import (CONN_CATEGORIES, CONNECTORS_PLANNED, DRIVERS, PLANNED, category_drivers,
                                driver_category, driver_label)
from nxlogger.ui.theme.app_style import blend
from nxlogger.ui.icons import STATUS_SIZE, status_icon
from nxlogger.ui.theme.ui_kit import T, label, make_icon, vsep
from nxlogger.ui.widgets.summary_strip import SummaryStrip, plural

COLS = ["Name", "Connector", "Address", "Scan", "Tags", "Last poll", "Values/s", "Errors/h", "Status", "Enabled",
        "Description"]
C_LAST, C_RATE, C_ERR, C_STATUS, C_EN = (COLS.index(c) for c in ("Last poll", "Values/s", "Errors/h", "Status",
                                                                  "Enabled"))
HIDDEN_KEY = "connectivity/hidden_columns"        # QSettings: column names the user hid
STATE_TOKEN = {"online": "ok", "connecting": "warn", "stalled": "warn", "error": "bad", "stopped": "muted",
               "disabled": "muted"}
STATE_TEXT = {"online": "Online", "connecting": "Connecting", "stalled": "Stalled", "error": "Error",
              "stopped": "Stopped", "disabled": "Disabled"}
WORST = ("bad", "warn", "ok", "muted")
DOT_STATE = {"bad": "alarm", "warn": "armed", "ok": "ok", "muted": "none"}   # token -> icons.status_icon state            # the connector row shows the first of these its devices have
CCAT = {c[0]: c for c in CONN_CATEGORIES}
NOUN = {"protocols": ("device", "devices"), "database": ("database", "databases"),   # Properties / hints
        "web": ("web service", "web services")}
TAG_COLS = ["Name", "Address", "Type", "Value", "Quality", "Updated", "Assets", "Alarm"]
BLOCK_COLS = ["#", "Unit", "Area", "Start", "Count", "Tags", "Time", "Error"]
SUB = Qt.ItemDataRole.UserRole + 1


def dot(token: str) -> QIcon:
    pm = QPixmap(20, 20); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(T[token]))
    p.drawEllipse(5, 5, 10, 10); p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def fmt_age(s) -> str:
    if s is None:
        return "-"
    if s < 10:
        return f"{s:.1f} s"
    return f"{int(s)} s" if s < 120 else (f"{int(s // 60)} min" if s < 7200 else f"{s / 3600:.0f} h")


def fmt_rate(r) -> str:
    if r is None:
        return "-"
    return f"{r:,.0f}" if r >= 10 else f"{r:.1f}"


def fmt_clock(t) -> str:
    if not t:
        return "-"
    d = _dt.datetime.fromtimestamp(t)
    return d.strftime("%H:%M:%S") if d.date() == _dt.date.today() else d.strftime("%d %b %H:%M")


def fmt_dur(s: float) -> str:
    s = max(0, int(s))
    h, m = divmod(s // 60, 60)
    return f"{h} h {m} min" if h else (f"{m} min {s % 60} s" if m else f"{s} s")


class Band(QFrame):
    """A category heading (Connectivity, Tags): the same full-width band as a Properties section
    (neutral theme colours read from T at paint time, so a theme switch needs no restyle)."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(26)
        h = QHBoxLayout(self); h.setContentsMargins(8, 0, 8, 0); h.setSpacing(6)
        self.icon, self.text = QLabel(), QLabel()
        f = self.text.font(); f.setWeight(f.Weight.DemiBold); self.text.setFont(f)
        self.text.setStyleSheet("background: transparent;"); self.icon.setStyleSheet("background: transparent;")
        h.addWidget(self.icon); h.addWidget(self.text, 1)

    def set(self, name: str, icon: str, tip: str):
        self.text.setText(name)
        self.icon.setPixmap(make_icon(icon, T["text"]).pixmap(QSize(16, 16)))
        self.setToolTip(tip)

    def paintEvent(self, e):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, QColor(blend(T["hover"], T["panel"], 0.7)))
        p.setPen(QColor(T["border"]))
        p.drawLine(r.left(), r.top(), r.right(), r.top())
        p.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
        p.end()


class DeviceView(QWidget):
    """A connector's right side: the selected device - header, address, tabs."""

    def __init__(self, page):
        super().__init__()
        from nxlogger.ui.widgets.logging_page import Bars               # (logging_page imports Band from here)
        self.page, self.host = page, page.host
        self.did: str | None = None
        self._rows: dict[tuple, QTreeWidgetItem] = {}
        head = QFrame(); head.setObjectName("subBar")
        hv = QVBoxLayout(head); hv.setContentsMargins(12, 8, 12, 8); hv.setSpacing(4)
        h1 = QHBoxLayout(); h1.setSpacing(8)
        self.icon = QLabel()
        self.title = label("", name="sectionTitle")
        self.state = QLabel()
        self.btn_edit = page._button("Edit", "pencil", lambda: self.did and self.host.edit_device(self.did),
                                     "Edit the device settings (its pop-up)")
        self.btn_more = page._button("", "ellipsis", None, "More: tags, enable / disable, duplicate, export, delete")
        self.btn_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more = QMenu(self.btn_more); self.more.aboutToShow.connect(self._fill_more)
        self.btn_more.setMenu(self.more)
        h1.addWidget(self.icon); h1.addWidget(self.title); h1.addStretch(1)
        h1.addWidget(self.state); h1.addWidget(self.btn_edit); h1.addWidget(self.btn_more)
        h2 = QHBoxLayout(); h2.setSpacing(6)
        self.aicon = QLabel()
        self.address = QLabel(); self.address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_conn = page._button("", "play", self._toggle_conn, "Connect / disconnect this device")
        self.btn_copy = page._button("", "copy", self._copy, "Copy the address")
        h2.addWidget(self.aicon); h2.addWidget(self.address, 1); h2.addWidget(self.btn_conn); h2.addWidget(self.btn_copy)
        for b in (self.btn_edit, self.btn_more, self.btn_conn, self.btn_copy):
            b.setAutoRaise(True)
        hv.addLayout(h1); hv.addLayout(h2)

        self.tabs = QTabWidget(); self.tabs.setDocumentMode(True)
        # Overview
        ov = QWidget(); ovl = QVBoxLayout(ov); ovl.setContentsMargins(12, 10, 12, 10); ovl.setSpacing(8)
        self.sentence = QLabel(); self.sentence.setWordWrap(True)
        self.grid = QGridLayout(); self.grid.setHorizontalSpacing(14); self.grid.setVerticalSpacing(3)
        self._cells: list[QLabel] = []
        self.chart_title = label("Scan time (ms) per minute - the last hour", muted=True, small=True)
        self.chart = Bars(110)
        self.problems = label("", small=True); self.problems.setWordWrap(True)
        ovl.addWidget(self.sentence); ovl.addLayout(self.grid); ovl.addWidget(self.chart_title)
        ovl.addWidget(self.chart); ovl.addWidget(self.problems); ovl.addStretch(1)
        self.tabs.addTab(ov, "Overview")
        # Tags
        tw = QWidget(); tv = QVBoxLayout(tw); tv.setContentsMargins(0, 0, 0, 0); tv.setSpacing(0)
        tbar = QFrame(); tbar.setObjectName("docBar")
        tb = QHBoxLayout(tbar); tb.setContentsMargins(8, 4, 8, 4); tb.setSpacing(2)
        self.btn_tadd = page._button("Add tag...", "plus", lambda: self.did and self.host.add_tags(self.did, False),
                                     "Add a tag to this device (its pop-up)")
        self.btn_topen = page._button("Open in Tags", "tag", lambda: self.did and self.host.open_tags(self.did),
                                      "Manage these tags in the Tags document (import, export, bulk edit)")
        self.tfilter = QLineEdit(); self.tfilter.setPlaceholderText("Search tags"); self.tfilter.setClearButtonEnabled(True)
        self.tfilter.setMaximumWidth(200)
        self.tfilter.textChanged.connect(lambda _: self._apply_tag_filter())
        tb.addWidget(self.btn_tadd); tb.addWidget(self.btn_topen); tb.addStretch(1); tb.addWidget(self.tfilter)
        self.tags = QTreeWidget(); self.tags.setHeaderLabels(TAG_COLS)
        self.tags.setRootIsDecorated(False); self.tags.setAlternatingRowColors(True); self.tags.setUniformRowHeights(True)
        for c, wd in enumerate((150, 150, 70, 100, 70, 70, 140)):
            self.tags.header().resizeSection(c, wd)
        self.tags.header().setSectionResizeMode(len(TAG_COLS) - 1, QHeaderView.ResizeMode.Stretch)
        self.tags.headerItem().setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.tags.itemDoubleClicked.connect(lambda it, _c: self.host.open_tag(it.data(0, Qt.ItemDataRole.UserRole)))
        self.tags.itemSelectionChanged.connect(self._tag_selected)
        tv.addWidget(tbar); tv.addWidget(self.tags, 1)
        self.tabs.addTab(tw, "Tags")
        # Diagnostics
        dw = QWidget(); dl = QVBoxLayout(dw); dl.setContentsMargins(0, 0, 0, 0); dl.setSpacing(0)
        self.blocks_title = label("", muted=True, small=True); self.blocks_title.setContentsMargins(10, 6, 10, 2)
        self.blocks = QTreeWidget(); self.blocks.setHeaderLabels(BLOCK_COLS)
        self.blocks.setRootIsDecorated(False); self.blocks.setAlternatingRowColors(True); self.blocks.setUniformRowHeights(True)
        self.blocks.setMaximumHeight(170)
        for c, wd in enumerate((30, 45, 70, 60, 55, 45, 70)):
            self.blocks.header().resizeSection(c, wd)
        self.blocks.header().setSectionResizeMode(len(BLOCK_COLS) - 1, QHeaderView.ResizeMode.Stretch)
        cbar = QFrame(); cbar.setObjectName("docBar")
        cb = QHBoxLayout(cbar); cb.setContentsMargins(8, 4, 8, 4); cb.setSpacing(2)
        self.comm_title = label("Comm log", name="sectionTitle")
        self.btn_pause = page._button("Pause", "pause", None, "Pause / resume the comm log")
        self.btn_pause.setCheckable(True)
        self.btn_clear = page._button("Clear", "trash", lambda: self.did and (self.host.clear_comm_log(self.did),
                                                                              self.comm.clear()), "Clear the comm log")
        self.btn_ccopy = page._button("Copy", "copy", self._copy_log, "Copy the comm log")
        cb.addWidget(self.comm_title); cb.addSpacing(10)
        for b in (self.btn_pause, self.btn_clear, self.btn_ccopy):
            cb.addWidget(b)
        cb.addStretch(1)
        self.comm_note = label("", muted=True, small=True)
        cb.addWidget(self.comm_note)
        self.comm = QTreeWidget(); self.comm.setHeaderLabels(["Time", "Dir", "Message"])
        self.comm.setRootIsDecorated(False); self.comm.setUniformRowHeights(True)
        self.comm.header().resizeSection(0, 100); self.comm.header().resizeSection(1, 45)
        dl.addWidget(self.blocks_title); dl.addWidget(self.blocks); dl.addWidget(cbar); dl.addWidget(self.comm, 1)
        self.tabs.addTab(dw, "Diagnostics")
        # History
        hw = QWidget(); hl = QVBoxLayout(hw); hl.setContentsMargins(12, 10, 12, 10); hl.setSpacing(6)
        self.hist_title = label("", muted=True, small=True)
        self.hist_chart = Bars(100)
        self.hist = QTreeWidget(); self.hist.setHeaderLabels(["Time", "State", "Duration", "Detail"])
        self.hist.setRootIsDecorated(False); self.hist.setAlternatingRowColors(True); self.hist.setUniformRowHeights(True)
        for c, wd in enumerate((110, 100, 100)):
            self.hist.header().resizeSection(c, wd)
        hl.addWidget(self.hist_title); hl.addWidget(self.hist_chart); hl.addWidget(self.hist, 1)
        self.tabs.addTab(hw, "History")
        self.tabs.currentChanged.connect(lambda _i: self._tab_changed())     # after every tab exists

        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(head); v.addWidget(self.tabs, 1)

    def device(self):
        return self.host.project.device(self.did) if self.did else None

    def show_device(self, did: str):
        if did != self.did and self.did:
            self.host.device_diag(self.did, comm=False)
        changed = did != self.did
        self.did = did
        d = self.device()
        if d is None:
            return
        self.icon.setPixmap(make_icon(DRIVERS[d.driver]["icon"], T["text"]).pixmap(QSize(18, 18)))
        self.title.setText(d.name)
        self.aicon.setPixmap(make_icon("plug", T["muted"]).pixmap(QSize(16, 16)))
        self.address.setText(d.topic or "-")
        self.address.setToolTip(d.topic or "")
        self.tabs.setTabText(1, f"Tags ({len(d.tags)})")
        if changed:
            self.tfilter.clear(); self.comm.clear()
        self._fill_head()
        self._tab_changed()

    def _tab_changed(self):
        if not self.did:
            return
        self.host.device_diag(self.did, comm=self.tabs.currentIndex() == 2 and self.page.stack.currentWidget() is self)
        i = self.tabs.currentIndex()
        if i == 0:
            self._fill_overview()
        elif i == 1:
            self._fill_tags()
        elif i == 2:
            self._fill_diag()
        else:
            self._fill_history()

    def hideEvent(self, e):
        if self.did:
            self.host.device_diag(self.did, comm=False)              # the comm log only while it is shown
        super().hideEvent(e)

    # ---------------------------------------------------------------- header + Overview
    def _fill_head(self):
        d = self.device()
        if d is None:
            return
        st = self.host.device_status(d.id)
        live = self.host.device_live(d.id)
        last = f" · last poll {fmt_age(live['last'])} ago" if live["last"] is not None and st in ("online", "stalled") else ""
        err = f": {live['text']}" if st == "error" and live["text"] else ""
        self.state.setText(f"{'○' if st in ('stopped', 'disabled') else '●'} {STATE_TEXT[st]}{last}{err}")
        self.state.setToolTip(live["text"] or "")
        self.state.setStyleSheet(f"color: {T[STATE_TOKEN[st]]};")
        running = live["state"] != "stopped"
        self.btn_conn.setIcon(make_icon("square" if running else "play", T["text"]))   # each tick: follows the theme
        self.btn_conn.setToolTip("Disconnect this device" if running else "Connect this device")
        self.btn_conn.setEnabled(running or (d.enabled and d.driver not in PLANNED))
        self.btn_edit.setEnabled(True)

    def _sentence(self, d) -> str:
        n = sum(1 for t in d.tags if t.enabled)
        what = {"modbus_tcp": f"Reads {n} tag(s) over Modbus TCP", "modbus_rtu": f"Reads {n} tag(s) over Modbus RTU",
                "opcua": f"Reads {n} OPC UA node(s)", "mqtt": f"Subscribes {n} tag(s) over MQTT",
                "sim": f"Simulates {n} tag(s)"}.get(d.driver, f"Reads {n} tag(s)")
        how = "as waveform packets" if d.wave else f"every {d.scan_ms / 1000:g} s"
        return f"{what} from {d.topic or 'the device'} {how}." + (" Disabled - Connect skips it." if not d.enabled else "")

    def _fill_overview(self):
        d = self.device()
        if d is None:
            return
        self.sentence.setText(self._sentence(d))
        live = self.host.device_live(d.id)
        rows = [("Connector", driver_label(d.driver)), ("Address", d.topic or "-"),
                ("Scan", "-" if d.wave else f"{d.scan_ms} ms"), ("Tags", f"{sum(t.enabled for t in d.tags)} of {len(d.tags)} enabled"),
                ("Values/s", fmt_rate(live["rate"])),
                ("Scan time", f"{live['scan_avg']:.0f} ms avg · {live['scan_max']:.0f} ms max" if live["scan_avg"] is not None else "-"),
                ("Load", f"{live['load']:.0f} % of the scan" + ("  - slow down the scan or read fewer blocks"
                                                                  if live["load"] >= 80 else "") if live["load"] is not None else "-"),
                ("Errors", f"{live['err_h']} in the last hour · {live['errors']} since connect"),
                ("Online since", f"{fmt_clock(live['since'])} ({fmt_dur(time.time() - live['since'])})" if live["since"] else "-"),
                ("Last poll", f"{fmt_age(live['last'])} ago" if live["last"] is not None else "-"),
                ("Scans", f"{live['scans']:,}")]
        if d.description:
            rows.append(("Description", d.description))
        for c in self._cells:
            c.setParent(None); c.deleteLater()
        self._cells = []
        half = (len(rows) + 1) // 2
        for i, (k, val) in enumerate(rows):
            col, row = (0, i) if i < half else (2, i - half)
            a, b = label(k, muted=True, small=True), QLabel(str(val))
            b.setToolTip(str(val)); b.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if k == "Load" and live["load"] is not None and live["load"] >= 80:
                b.setStyleSheet(f"color: {T['warn']};")
            self.grid.addWidget(a, row, col); self.grid.addWidget(b, row, col + 1)
            self._cells += [a, b]
        self.grid.setColumnStretch(1, 1); self.grid.setColumnStretch(3, 1)
        diag = self.host.device_diag(d.id)
        per: dict[int, list] = {}
        now = time.time()
        for t, ms in diag.get("scan_times", []):
            if now - t <= 3600:
                per.setdefault(int((now - t) // 60), []).append(ms)
        mins = list(range(59, -1, -1))
        self.chart.set([sum(per[m]) / len(per[m]) if m in per else 0 for m in mins],
                       [time.strftime("%H:%M", time.localtime(now - m * 60)) for m in mins])
        errs = [(t, x) for t, st, x in diag["events"] if st == "error"][-4:]
        self.problems.setText("Problems  " + ("none" if not errs else
                                              "  ·  ".join(f"{fmt_clock(t)} {x}" for t, x in reversed(errs))))
        self.problems.setStyleSheet(f"color: {T['warn'] if errs else T['muted']};")

    # ---------------------------------------------------------------- Tags
    def _fill_tags(self):
        self.tags.clear(); self._rows.clear()
        if not self.did:
            return
        muted = QBrush(QColor(T["muted"]))
        for r in self.host.device_tag_rows(self.did):
            it = QTreeWidgetItem([r["name"], r["address"], str(r["type"]), "", "", "", r["assets"], r["alarm"]])
            it.setData(0, Qt.ItemDataRole.UserRole, r["key"])
            it.setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if not r["enabled"]:
                for c in range(len(TAG_COLS)):
                    it.setForeground(c, muted)
            if r["alarm"]:
                it.setForeground(7, QBrush(QColor(T["bad"])))
            self.tags.addTopLevelItem(it)
            self._rows[r["key"]] = it
        self._apply_tag_filter()
        self.update_tags()

    def update_tags(self):
        bad, ok, muted = QBrush(QColor(T["bad"])), QBrush(QColor(T["ok"])), QBrush(QColor(T["muted"]))
        for key, it in self._rows.items():
            val, qual, good, stamp = self.host.tag_live(*key)
            for c, text in ((3, val), (4, qual), (5, stamp)):
                if it.text(c) != (text or ""):
                    it.setText(c, text or "")
            it.setForeground(4, ok if good else (bad if good is False else muted))

    def _apply_tag_filter(self):
        text = self.tfilter.text().strip().lower()
        for it in self._rows.values():
            it.setHidden(bool(text) and not any(text in it.text(c).lower() for c in (0, 1, 6)))

    def _tag_selected(self):
        sel = self.tags.selectedItems()
        if len(sel) == 1:
            self.host.select_key(sel[0].data(0, Qt.ItemDataRole.UserRole))

    # ---------------------------------------------------------------- Diagnostics
    def _fill_diag(self):
        d = self.device()
        if d is None:
            return
        diag = self.host.device_diag(d.id)
        self.blocks.clear()
        for i, b in enumerate(diag["blocks"]):
            it = QTreeWidgetItem([str(i + 1), str(b["unit"]), b["area"], str(b["start"]), str(b["count"]), str(b["tags"]),
                                  "-" if b["ms"] is None else f"{b['ms']:.0f} ms", b["error"] or ""])
            if b["error"]:
                it.setForeground(7, QBrush(QColor(T["bad"])))
                it.setForeground(6, QBrush(QColor(T["bad"])))
            self.blocks.addTopLevelItem(it)
        self.blocks.setVisible(bool(diag["blocks"]))
        self.blocks_title.setText(f"Requests of the last scan: {len(diag['blocks'])}" if diag["blocks"] else
                                  ("Requests per scan: Modbus devices list them here" if diag["running"] else
                                   "Not connected - connect the device to see its requests and comm log"))
        if not self.btn_pause.isChecked():
            have = self.comm.topLevelItemCount()
            rows = diag["comm"]
            if have > len(rows):
                self.comm.clear(); have = 0
            for t, direction, text in rows[have:]:
                it = QTreeWidgetItem([time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}",
                                      direction, text])
                it.setForeground(1, QBrush(QColor(T["bad"] if direction == "ERR" else
                                                  (T["info"] if direction == "TX" else T["ok"]))))
                if direction == "ERR":
                    it.setForeground(2, QBrush(QColor(T["bad"])))
                self.comm.addTopLevelItem(it)
            if rows[have:]:
                self.comm.scrollToBottom()
        self.comm_note.setText("logging while this tab is open · last 200" if diag["running"] else "")

    def _copy_log(self):
        lines = ["\t".join(self.comm.topLevelItem(i).text(c) for c in range(3)) for i in range(self.comm.topLevelItemCount())]
        QApplication.clipboard().setText("\n".join(lines))

    # ---------------------------------------------------------------- History
    def _fill_history(self):
        d = self.device()
        self.hist.clear()
        if d is None:
            return
        events = self.host.device_diag(d.id)["events"]
        now = time.time()
        for i in range(len(events) - 1, -1, -1):
            t, st, text = events[i]
            end = events[i + 1][0] if i + 1 < len(events) else now
            it = QTreeWidgetItem([fmt_clock(t), STATE_TEXT.get(st, st), fmt_dur(end - t), text])
            it.setForeground(1, QBrush(QColor(T[STATE_TOKEN.get(st, "muted")])))
            self.hist.addTopLevelItem(it)
        day0 = _dt.datetime.combine(_dt.date.today(), _dt.time()).timestamp()
        online = [0.0] * 24                               # minutes online per hour today
        for i, (t, st, _x) in enumerate(events):
            if st != "online":
                continue
            a, b = max(t, day0), min(events[i + 1][0] if i + 1 < len(events) else now, now)
            while a < b:
                hr = int((a - day0) // 3600)
                if hr >= 24:
                    break
                nxt = min(b, day0 + (hr + 1) * 3600)
                online[hr] += (nxt - a) / 60
                a = nxt
        self.hist_chart.set(online, [f"{h:02d}:00" for h in range(24)])
        span = max(1.0, (now - max(day0, events[0][0])) / 60) if events else 0
        share = sum(online) / span * 100 if events else 0
        self.hist_title.setText(f"Minutes online per hour today · {share:.1f} % online since the first connect today"
                                if events else "No state changes yet - Connect to start the device")

    def refresh_live(self):
        self._fill_head()
        i = self.tabs.currentIndex()
        if i == 0:
            self._fill_overview()
        elif i == 1:
            self.update_tags()
        elif i == 2:
            self.host.device_diag(self.did, comm=self.page.stack.currentWidget() is self)
            self._fill_diag()

    # ---------------------------------------------------------------- actions
    def _toggle_conn(self):
        d = self.device()
        if d is None:
            return
        if self.host.device_state(d.id)[0] != "stopped":
            self.host.stop_device(d.id)
        else:
            self.host.start_device(d.id)
        QTimer.singleShot(300, self._fill_head)

    def _copy(self):
        QApplication.clipboard().setText(self.address.toolTip())
        self.btn_copy.setToolTip("Copied ✓")
        QTimer.singleShot(1500, lambda: self.btn_copy.setToolTip("Copy the address"))

    def _fill_more(self):
        self.more.clear()
        d = self.device()
        if d is not None:
            for a in self.page.build_menu([d]).actions():
                self.more.addAction(a)


class ConnectivityPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._cat = "protocols"                      # the category shown (Project tree > Connectivity > ...)
        self._sel: tuple = ("all",)                  # in it: ("all",) / ("driver", key) / ("planned", label)
        self._conn: str | None = None                # a connector opened (its devices left, one device right)
        self._did: str | None = None                 # the device shown in a connector
        self._rows: dict[str, QTreeWidgetItem] = {}
        self._drv_items: dict[str, QTreeWidgetItem] = {}
        self._dev_items: dict[str, QTreeWidgetItem] = {}
        self._last: list[str] | None = None          # selection to bring back when the tab returns;
        #                                              [] = cleared on purpose (connector switched): none

        bar = QFrame(); bar.setObjectName("docBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(2)
        add = self._button("Add", "plus", None, "Add a device: choose the connector")
        self.add_menu = QMenu(add)
        self.add_menu.aboutToShow.connect(self._fill_add_menu)
        add.setMenu(self.add_menu); add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_add = add
        self.btn_edit = self._button("Edit", "pencil", self._edit, "Edit the device settings (double-click a row)")
        self.btn_dup = self._button("", "copy", self._duplicate, "Duplicate the device (settings and tags)")
        self.btn_del = self._button("", "trash", self._delete, "Delete the selected devices (Del)")
        self.btn_on = self._button("Connect", "play", lambda: self._connect(self._devices(), True),
                                   "Connect the selected devices")
        self.btn_off = self._button("Disconnect", "square", lambda: self._connect(self._devices(), False),
                                    "Disconnect the selected devices")
        self.btn_imp = self._button("Import...", "download", lambda: host.import_devices(),
                                    "Import devices from JSON (with tags) or CSV")
        self.btn_exp = self._button("Export...", "upload", lambda: host.export_devices_dialog(self.selected()),
                                    "Export devices: tick them, JSON (with tags) or CSV")
        for w in (add, self.btn_edit, self.btn_dup, self.btn_del, None, self.btn_on, self.btn_off, None,
                  self.btn_imp, self.btn_exp):
            if w is None:
                bl.addWidget(vsep())
            else:
                bl.addWidget(w)
        bl.addStretch(1)
        self.btn_cols = self._button("Columns", "columns", None, "Show / hide table columns")
        self.cols_menu = QMenu(self.btn_cols)
        self.cols_menu.aboutToShow.connect(lambda: self._fill_columns_menu(self.cols_menu))
        self.btn_cols.setMenu(self.cols_menu)
        self.btn_cols.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        bl.addWidget(self.btn_cols)
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Filter devices")
        self.filter.setClearButtonEnabled(True); self.filter.setMaximumWidth(220)
        self.filter.setMinimumWidth(120)
        self.filter.textChanged.connect(lambda _: self._apply_filter())
        bl.addWidget(self.filter)
        self.health = SummaryStrip()                 # the summary under the toolbar
        self.health.chip_clicked.connect(self._status_chip)
        self._status: str | None = None              # a chip's filter: online / error / ... / load

        from nxlogger.ui.widgets.logging_page import TwoLineDelegate
        self.band = Band()
        self.clist = QTreeWidget(); self.clist.setObjectName("navTree")
        self.clist.setHeaderHidden(True); self.clist.setIndentation(6)     # the same left margin as Assets / Logging
        self.clist.setRootIsDecorated(False)
        self.clist.setProperty("plainSel", True)        # neutral selection, like the Tags list
        self.clist.setIconSize(QSize(*STATUS_SIZE))      # every row: a dot column, then the icon
        self.clist.setItemDelegate(TwoLineDelegate(self.clist))
        self.clist.currentItemChanged.connect(lambda cur, _p: self._list_selected(cur))
        self.clist.itemDoubleClicked.connect(self._list_double)
        self.clist.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.clist.customContextMenuRequested.connect(self._list_menu)
        self.clist.installEventFilter(self)
        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(0)
        lv.addWidget(self.band); lv.addWidget(self.clist, 1)

        self.table = QTreeWidget()
        self.table.setHeaderLabels(COLS)
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        for c, w in enumerate((150, 100, 180, 60, 44, 70, 70, 62, 110, 64)):
            self.table.header().resizeSection(c, w)
        self.table.header().setStretchLastSection(True)
        self.table.header().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.header().customContextMenuRequested.connect(self._header_menu)
        hidden = QSettings().value(HIDDEN_KEY, [])
        for c in range(1, len(COLS)):                 # Name always shows
            self.table.setColumnHidden(c, COLS[c] in (hidden if isinstance(hidden, list) else [hidden]))
        self.table.itemSelectionChanged.connect(self._selected)
        self.table.itemDoubleClicked.connect(lambda it, _c: QTimer.singleShot(
            0, lambda: host.edit_device(it.data(0, Qt.ItemDataRole.UserRole))))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        for key, slot in ((QKeySequence.StandardKey.Delete, self._delete), (Qt.Key.Key_Return, self._edit),
                          (Qt.Key.Key_Enter, self._edit)):
            sc = QShortcut(QKeySequence(key), self.table)
            sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(slot)
        from nxlogger.ui.widgets.assets_page import EmptyState
        self.view = DeviceView(self)
        self.empty = EmptyState()
        self.stack = QStackedWidget()
        for w in (self.table, self.view, self.empty):
            self.stack.addWidget(w)
        self.hint = label("", muted=True, small=True)
        self.hint.setContentsMargins(8, 2, 8, 2)

        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        rv.addWidget(self.stack, 1); rv.addWidget(self.hint)
        split = QSplitter(); split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(1, 1); split.setSizes([230, 820]); split.setChildrenCollapsible(False)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(bar); v.addWidget(self.health); v.addWidget(split, 1)

    def _button(self, text, icon, slot, tip) -> QToolButton:
        b = QToolButton(); b.setToolTip(tip)
        if text:
            b.setText(text); b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.host.reg_icon(b, icon)
        if slot:
            b.clicked.connect(slot)
        return b

    def eventFilter(self, obj, ev):
        """Backspace in a connector's device list: back to the category."""
        if obj is self.clist and ev.type() == ev.Type.KeyPress and ev.key() == Qt.Key.Key_Backspace \
                and self._conn is not None:
            self.show_category(self._cat, force=True)
            return True
        return False

    @property
    def category(self) -> str:
        return self._cat

    @property
    def connector(self) -> str | None:
        return self._conn

    # ---------------------------------------------------------------- connector list
    def show_category(self, cat: str, force: bool = False):
        """Project tree > Connectivity > <category>: show it (all its devices)."""
        if cat in CCAT and (force or self._conn is not None or (cat, ("all",)) != (self._cat, self._sel)):
            self._cat, self._sel, self._conn = cat, ("all",), None
            self._last = []
            self.refresh([])                         # another category / back from a connector: the list too
            self._selected()

    def open_connector(self, driver: str, did: str | None = None):
        """A connector's page: its devices left, the selected one right (did: that one)."""
        if driver not in DRIVERS:
            return
        self._cat, self._conn = driver_category(driver), driver
        devs = [d.id for d in self._conn_devices()]
        self._did = did if did in devs else (self._did if self._did in devs else (devs[0] if devs else None))
        self.refresh()
        self.activate()

    def _cat_devices(self):
        return [d for d in self.host.project.devices if driver_category(d.driver) == self._cat]

    def _conn_devices(self):
        return [d for d in self.host.project.devices if d.driver == self._conn]

    def _dev_sub(self, d) -> str:
        st = self.host.device_status(d.id)
        live = self.host.device_live(d.id)
        line1 = f"{d.topic or '-'} · {len(d.tags)} tags"
        if st == "error":
            return line1 + f"\n{(live['text'] or 'error')[:60]}"
        if st == "disabled":
            return line1 + "\ndisabled"
        bits = ["waveform" if d.wave else f"{d.scan_ms} ms"]
        if live["rate"] is not None:
            bits.append(f"{fmt_rate(live['rate'])} values/s")
        if st == "stalled":
            bits.append(f"no data {fmt_age(live['last'])}")
        return line1 + "\n" + " · ".join(bits)

    def _fill_list(self):
        self.clist.blockSignals(True)
        self.clist.clear(); self._drv_items.clear(); self._dev_items.clear()
        if self._conn is not None:                   # a connector: its devices
            devs = self._conn_devices()
            self.band.set(f"{driver_label(self._conn)}  ({len(devs)})", DRIVERS[self._conn]["icon"],
                          DRIVERS[self._conn]["note"] + " - Backspace / the Project tree: the category")
            for d in devs:
                it = QTreeWidgetItem([d.name])
                it.setData(0, Qt.ItemDataRole.UserRole, ("device", d.id))
                it.setData(0, SUB, self._dev_sub(d))
                self._dev_icon(it, d)
                self.clist.addTopLevelItem(it)
                self._dev_items[d.id] = it
            if self._did in self._dev_items:
                self.clist.setCurrentItem(self._dev_items[self._did])
            self.clist.blockSignals(False)
            return
        key, name, icon, tip = CCAT[self._cat]
        self.band.set(name, icon, tip)
        devs = self._cat_devices()
        items: dict[tuple, QTreeWidgetItem] = {}
        muted = QBrush(QColor(T["muted"]))

        def add(text, data, icon_, tip_, dim=False):
            it = QTreeWidgetItem([text]); it.setIcon(0, icon_); it.setToolTip(0, tip_)
            it.setData(0, Qt.ItemDataRole.UserRole, data)
            if dim:
                it.setForeground(0, muted)
            self.clist.addTopLevelItem(it)
            items[data] = it
            return it
        self._all_item = add(f"All connections  ({len(devs)})", ("all",), status_icon("list", "blank"),
                             f"Every {name} connection")
        for k in category_drivers(self._cat):
            n = sum(1 for d in devs if d.driver == k)
            self._drv_items[k] = add(f"{driver_label(k)}  ({n})", ("driver", k),
                                     status_icon(DRIVERS[k]["icon"], "blank", T["muted"]),
                                     DRIVERS[k]["note"] + " - click: only these · double-click: open the connector",
                                     dim=not n or k in PLANNED)
        for text, ic in CONNECTORS_PLANNED.get(self._cat, []):
            add(f"{text} (planned)", ("planned", text), status_icon(ic, "blank", T["muted"]),
                f"{text}: not available yet", dim=True)
        cur = items.get(self._sel) or items[("all",)]
        self._sel = tuple(cur.data(0, Qt.ItemDataRole.UserRole))
        self.clist.setCurrentItem(cur)
        self.clist.blockSignals(False)

    def _dev_icon(self, it, d):
        st = self.host.device_status(d.id)
        it.setIcon(0, status_icon(DRIVERS[d.driver]["icon"], DOT_STATE[STATE_TOKEN[st]],
                                  T["muted"] if st == "disabled" else T["text"]))
        it.setForeground(0, QBrush(QColor(T["muted"])) if st == "disabled" else QBrush())

    def _list_selected(self, it):
        if it is None:
            return
        sel = tuple(it.data(0, Qt.ItemDataRole.UserRole))
        if sel[0] == "device":
            if sel[1] != self._did:
                self._did = sel[1]
                QTimer.singleShot(0, self._fill_right)
            self.activate()
            return
        if sel != self._sel:
            self._sel = sel
            QTimer.singleShot(0, self._switched)         # not inside the list's own signal

    def _list_double(self, it, _c):
        sel = tuple(it.data(0, Qt.ItemDataRole.UserRole))
        if sel[0] == "driver" and sel[1] not in PLANNED:
            QTimer.singleShot(0, lambda: self.open_connector(sel[1]))
        elif sel[0] == "device":
            QTimer.singleShot(0, lambda: self.host.edit_device(sel[1]))

    def _list_menu(self, pos):
        it = self.clist.itemAt(pos)
        if it is None:
            return
        sel = tuple(it.data(0, Qt.ItemDataRole.UserRole))
        if sel[0] == "device":
            d = self.host.project.device(sel[1])
            if d is not None:
                self.clist.setCurrentItem(it)
                self.build_menu([d]).exec(self.clist.viewport().mapToGlobal(pos))

    def _switched(self):
        """Another category / connector: other rows, so no selection (Properties shows the empty
        state) - a hidden selection would edit what the table does not show."""
        self._last = []
        self.refresh([], keep_list=self._conn is None and bool(self._drv_items))
        self._selected()

    def _fill_add_menu(self):
        """Add ▾: the connectors of the shown category (a connector page: that one first)."""
        m = self.add_menu
        m.clear()
        for k in category_drivers(self._cat):           # text only, like every menu (no icon column)
            if self._conn in (None, k):
                m.addAction(DRIVERS[k]["label"] + "...", lambda kk=k: self.host.add_device(kk))
        for text, _ic in CONNECTORS_PLANNED.get(self._cat, []) if self._conn is None else []:
            m.addAction(f"{text} (planned)").setEnabled(False)

    # ---------------------------------------------------------------- the right side
    def _fill_right(self):
        if self._conn is None:
            self.stack.setCurrentWidget(self.table)
        elif self._did is None:
            lab = driver_label(self._conn)
            k = self._conn
            self.empty.set(DRIVERS[k]["icon"], f"No {lab} devices", DRIVERS[k]["note"],
                           [(f"Add {lab} device", lambda: self.host.add_device(k), True)])
            self.stack.setCurrentWidget(self.empty)
        else:
            self.view.show_device(self._did)
            self.stack.setCurrentWidget(self.view)
        self.hint.setVisible(self._conn is None)
        self._update_buttons()

    def refresh(self, select: list[str] | None = None, keep_list: bool = False):
        """Model changed: rebuild the list and the table (keeps the selection unless `select` is given;
        keep_list: only the table - a click on a connector filters it, the list stays)."""
        keep = self.selected() if select is None else list(select)
        if self._conn is not None:
            ids = [d.id for d in self._conn_devices()]
            if self._did not in ids:
                self._did = ids[0] if ids else None
        if not keep_list:
            self._fill_list()
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.clear(); self._rows.clear()
        muted = QBrush(QColor(T["muted"]))
        for d in self._cat_devices() if self._conn is None else []:
            if self._sel[0] == "planned" or (self._sel[0] == "driver" and d.driver != self._sel[1]):
                continue
            it = QTreeWidgetItem([d.name, driver_label(d.driver), d.topic,
                                  "-" if d.wave else f"{d.scan_ms} ms", str(len(d.tags)), "", "", "", "", "",
                                  d.description])
            it.setData(0, Qt.ItemDataRole.UserRole, d.id)
            it.setCheckState(C_EN, Qt.CheckState.Checked if d.enabled else Qt.CheckState.Unchecked)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)    # indicator only: set in the pop-up
            it.setText(C_EN, "Yes" if d.enabled else "No")
            it.setToolTip(0, DRIVERS[d.driver]["note"])
            it.setToolTip(2, d.topic)
            for c in (C_LAST, C_RATE, C_ERR):
                it.setTextAlignment(c, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if not d.enabled:
                for c in range(len(COLS)):
                    it.setForeground(c, muted)
            self.table.addTopLevelItem(it)
            self._rows[d.id] = it
            it.setSelected(d.id in keep)
        self.table.setSortingEnabled(True)
        self.table.blockSignals(False)
        cur = next((self._rows[k] for k in keep if k in self._rows), None)
        if cur is not None:
            self.table.setCurrentItem(cur, 0, self.table.selectionModel().SelectionFlag.NoUpdate)
        self._apply_filter()
        self._fill_right()
        self.update_states(force=True)

    def select(self, did: str):
        """Select one device (project tree, banners) and show its properties: switches to its
        category, and to All connections unless its connector is the one shown."""
        dev = self.host.project.device(did)
        if dev is None:
            return
        if self._conn is not None:                   # a connector page: show it there
            self.open_connector(dev.driver, did)
            return
        cat = driver_category(dev.driver)
        if cat != self._cat or self._sel not in (("all",), ("driver", dev.driver)):
            self._cat, self._sel = cat, ("all",)
        if did not in self._rows:
            self.refresh([did])
        it = self._rows.get(did)
        if it is not None:
            self.table.clearSelection()
            it.setSelected(True)
            self.table.setCurrentItem(it)
            self.table.scrollToItem(it)

    def update_states(self, force: bool = False):
        """Status and live columns, state icons, the connector icons, the health strip (twice a
        second while visible, and on state changes)."""
        bad = QBrush(QColor(T["bad"]))
        worst: dict[str, str] = {}
        for d in self._cat_devices():
            st = self.host.device_status(d.id)
            state, text = self.host.device_state(d.id)
            tok = STATE_TOKEN.get(st, "muted")
            worst[d.driver] = min(worst.get(d.driver, "muted"), tok, key=WORST.index)
            it = self._rows.get(d.id)
            if it is not None:
                live = self.host.device_live(d.id)
                running = state not in ("stopped",)
                for c, val in ((C_LAST, fmt_age(live["last"]) if running and live["last"] is not None else "-"),
                               (C_RATE, fmt_rate(live["rate"]) if running else "-"),
                               (C_ERR, str(live["err_h"]) if running else "-")):
                    if it.text(c) != val:
                        it.setText(c, val)
                it.setForeground(C_ERR, bad if live["err_h"] else it.foreground(1))
                it.setForeground(C_LAST, QBrush(QColor(T["warn"])) if st == "stalled" else it.foreground(1))
                status = "Planned" if d.driver in PLANNED and d.enabled else STATE_TEXT[st]
                key = (tok, status, text)
                if force or it.data(1, Qt.ItemDataRole.UserRole) != key:
                    it.setData(1, Qt.ItemDataRole.UserRole, key)
                    it.setIcon(0, make_icon(DRIVERS[d.driver]["icon"], T[tok if tok != "muted" else "text"]))
                    it.setIcon(C_STATUS, dot(tok))
                    it.setText(C_STATUS, status + (f": {text}" if text and st == "error" else ""))
                    it.setToolTip(C_STATUS, f"{STATE_TEXT[st]}" + (f": {text}" if text else ""))
                    it.setForeground(C_STATUS, bad if tok == "bad" else (QBrush(QColor(T["warn"])) if tok == "warn"
                                                                         else it.foreground(1)))
            dit = self._dev_items.get(d.id)
            if dit is not None:
                self._dev_icon(dit, d)
                sub = self._dev_sub(d)
                if dit.data(0, SUB) != sub:
                    dit.setData(0, SUB, sub)
        for k, it in self._drv_items.items():
            has = k in worst
            tok = worst.get(k, "muted")
            if force or it.data(1, Qt.ItemDataRole.UserRole) != (has, tok):
                it.setData(1, Qt.ItemDataRole.UserRole, (has, tok))     # muted: none, or all stopped / off
                it.setIcon(0, status_icon(DRIVERS[k]["icon"], DOT_STATE[tok] if has else "blank",
                                          T["text"] if has else T["muted"]))
        all_tok = min(worst.values(), key=WORST.index) if worst else None     # All: the worst of the category
        if getattr(self, "_all_item", None) is not None and self._conn is None:
            try:
                self._all_item.setIcon(0, status_icon("list", DOT_STATE[all_tok] if all_tok else "blank"))
            except RuntimeError:                         # the list was rebuilt
                self._all_item = None
        self.refresh_health()
        if self._status is not None:
            self._apply_filter()
        if self._conn is not None and self.stack.currentWidget() is self.view:
            self.view.refresh_live()
        self._update_hint()
        self._update_buttons()

    def _update_hint(self):
        name = CCAT[self._cat][1]
        one, many = NOUN[self._cat]
        if self._sel[0] == "planned":
            self.hint.setText(f"{self._sel[1]} is planned - not available yet. Use MQTT, Modbus or OPC UA meanwhile.")
            return
        if self._sel[0] == "driver" and self._sel[1] in PLANNED:
            self.hint.setText(DRIVERS[self._sel[1]]["note"])
            return
        p = self.host.project                        # rows can be older than the model (refresh follows)
        devs = [d for d in (p.device(i) for i in self._rows) if d is not None]
        if not devs:                                 # nothing to select: say how to add one
            if self._sel[0] == "driver":
                lab = driver_label(self._sel[1])
                self.hint.setText(f"No {lab} {many} - Add > {lab}..., or Import...")
            elif any(k not in PLANNED for k in category_drivers(self._cat)):
                self.hint.setText(f"No {many} - Add > a connector, or Import...")
            else:
                self.hint.setText(f"No {many} yet - the {name} connectors are planned.")
            return
        on = sum(1 for d in devs if self.host.device_state(d.id)[0] == "online")
        self.hint.setText(f"{len(devs)} device(s) · {sum(d.enabled for d in devs)} enabled · {on} online · "
                          "double-click a device to edit it · double-click a connector (left) to open it")

    def _apply_filter(self):
        text = self.filter.text().strip().lower()
        for did, it in self._rows.items():
            hide = bool(text) and not any(text in it.text(c).lower() for c in (0, 1, 2, len(COLS) - 1))
            if self._status == "load":
                hide = hide or (self.host.device_live(did)["load"] or 0) < 50
            elif self._status is not None:
                hide = hide or self.host.device_status(did) != self._status
            it.setHidden(hide)
        for it in self._dev_items.values():
            it.setHidden(bool(text) and text not in it.text(0).lower() and text not in str(it.data(0, SUB)).lower())

    # ---------------------------------------------------------------- the summary strip
    def refresh_health(self):
        """Connected · how long │ the devices by state (chips filter the table) │ values/s │ errors /h,
        the highest poll load above 50 %; the worst problem on a second line."""
        h = self.host.conn_health()
        devs = self.host.project.devices
        st = h["states"]
        chips = [(k, f"{st[k]} {STATE_TEXT[k].lower()}", STATE_TOKEN[k]) for k in
                 ("online", "connecting", "stalled", "error", "stopped", "disabled") if st.get(k)]
        loads = [(self.host.device_live(d.id)["load"] or 0, d.name) for d in devs]
        top = max(loads, default=(0, ""))
        issue, actions = None, []
        if h["running"]:
            since = f" · {fmt_dur(time.time() - h['since'])}" if h["since"] else ""
            state = "bad" if st.get("error") else ("warn" if st.get("stalled") or st.get("connecting") else "ok")
            title = f"● Connected{since}"
            chips += [("|", "", ""), ("rate", f"{h['rate']:,.0f} values/s", ""),
                      ("|", "", ""), ("errors", f"{h['err_h']:,} errors /h", "")]
            if top[0] >= 50:
                chips += [("|", "", ""), ("load", f"Poll load {top[0]:.0f} % ({top[1]})", "warn")]
        else:
            ready = sum(1 for d in devs if d.enabled and d.driver not in PLANNED)
            off = sum(1 for d in devs if not d.enabled)
            state = "muted"
            title = "○ Not connected · " + (f"{plural(ready, 'device')} ready" + (f" ({off} disabled)" if off else "")
                                             if devs else "no devices yet")
            chips = [c for c in chips if c[0] == "disabled"]      # Connect: the main toolbar
        if h["issues"]:
            did, text = h["issues"][0]
            more = f"  (+{len(h['issues']) - 1} more)" if len(h["issues"]) > 1 else ""
            dev = self.host.project.device(did)
            issue = (text + more, "bad" if self.host.device_status(did) == "error" else "warn",
                     [("Open device ›", lambda d=dev: d and self.open_connector(d.driver, d.id)),
                      ("Retry", lambda i=did: (self.host.stop_device(i), self.host.start_device(i)))])
        self.health.set(state, title, chips, issue, actions)

    def _status_chip(self, key):
        """A chip of the summary: only the devices in that state (the category's table)."""
        self._status = key
        if key is not None and self._conn is not None:
            self.show_category(self._cat, force=True)
        self._apply_filter()
        self.refresh_health()

    def selected(self) -> list[str]:
        if self._conn is not None:
            return [self._did] if self._did else []
        return [it.data(0, Qt.ItemDataRole.UserRole) for it in self.table.selectedItems()]

    def _devices(self):
        p = self.host.project
        return [d for d in (p.device(i) for i in self.selected()) if d is not None]

    def _selected(self):
        sel = self.selected()
        if sel:
            self._last = sel
        self.host.select_object(("device", sel[0]) if len(sel) == 1 else None)
        self._update_buttons()

    def activate(self):
        """The tab came to the front (other tabs used the Properties panel meanwhile): keep or
        bring back the selected rows - else the first device - and show its properties again."""
        if self._conn is None and not self.selected():
            keep = [d for d in self._last or [] if d in self._rows]
            first = self.table.topLevelItem(0)
            if not keep and first is not None and self._last is None:    # first visit only
                keep = [first.data(0, Qt.ItemDataRole.UserRole)]
            self.table.blockSignals(True)
            for did in keep:
                self._rows[did].setSelected(True)
            if keep:
                self.table.setCurrentItem(self._rows[keep[0]], 0,
                                          self.table.selectionModel().SelectionFlag.NoUpdate)
            self.table.blockSignals(False)
        self._selected()

    def empty_hint(self) -> tuple[str, str]:
        """Properties with no single row selected: title and line, in the category's words."""
        one, many = NOUN[self._cat]
        n = len(self.selected())
        if n > 1:
            return f"{n} {many} selected", f"Select one {one} to view its settings"
        if not self._rows and self._conn is None:
            return f"No {one} selected", self.hint.text()
        return f"No {one} selected", f"Select a {one} to view its settings"

    def _menu(self, pos):
        it = self.table.itemAt(pos)
        if it is None:                               # empty space: no menu (the toolbar has it all)
            return
        if not it.isSelected():
            self.table.clearSelection(); it.setSelected(True)
        self.build_menu(self._devices()).exec(self.table.viewport().mapToGlobal(pos))

    def build_menu(self, devs) -> QMenu:
        """Right-click menu of device rows - the same for every connector (connector actions such
        as Browse... or Test are in the device pop-up)."""
        rec = self.host.recording
        m = QMenu(self)
        if not devs:
            return m
        one, n = len(devs) == 1, len(devs)
        what = "device" if one else f"{n} devices"
        cnt = "" if one else f" ({n})"
        off = all(x.enabled for x in devs)               # all on: Disable, else Enable
        run = all(self.host.device_state(x.id)[0] != "stopped" for x in devs)   # all running: Disconnect
        can_run = all(x.enabled and x.driver not in PLANNED for x in devs)
        ids = [x.id for x in devs]
        items = [("Edit device...", lambda: QTimer.singleShot(0, lambda: self.host.edit_device(ids[0])), one),
                 ("Open tags", lambda: self.host.open_tags(ids[0]), one),
                 None,
                 (("Disconnect" if run else "Connect") + cnt, lambda: self._connect(devs, not run), run or can_run),
                 (f"{'Disable' if off else 'Enable'} {what}",
                  lambda: self.host.set_devices_enabled(ids, not off), not rec),
                 None,
                 ("Duplicate", lambda: self.host.duplicate_device(ids[0]), one and not rec),
                 ("Export...", lambda: self.host.export_devices_dialog(ids), True),
                 None,
                 (f"Delete {what}...", lambda: self.host.delete_devices(ids), not rec)]
        for item in items:
            if item is None:
                m.addSeparator(); continue
            text, slot, on = item
            m.addAction(text, slot).setEnabled(on)
        return m

    # ---------------------------------------------------------------- columns
    def _fill_columns_menu(self, m: QMenu):
        m.clear()
        for c in range(1, len(COLS)):
            a = m.addAction(COLS[c])
            a.setCheckable(True); a.setChecked(not self.table.isColumnHidden(c))
            a.toggled.connect(lambda on, cc=c: self._show_column(cc, on))   # toggled passes the state
        m.addSeparator()
        m.addAction("Show all columns", lambda: [self._show_column(c, True) for c in range(1, len(COLS))])

    def _header_menu(self, pos):
        m = QMenu(self)
        self._fill_columns_menu(m)
        m.exec(self.table.header().mapToGlobal(pos))

    def _show_column(self, c: int, on: bool):
        self.table.setColumnHidden(c, not on)
        QSettings().setValue(HIDDEN_KEY, [COLS[i] for i in range(1, len(COLS)) if self.table.isColumnHidden(i)])

    # ---------------------------------------------------------------- actions
    def _edit(self):
        sel = self.selected()
        if sel:
            QTimer.singleShot(0, lambda: self.host.edit_device(sel[0]))

    def _duplicate(self):
        sel = self.selected()
        if len(sel) == 1:
            self.host.duplicate_device(sel[0])

    def _delete(self):
        sel = self.selected()
        if sel:
            self.host.delete_devices(sel)

    def _connect(self, devs, on: bool):
        for d in devs:
            if on and self.host.device_state(d.id)[0] == "stopped" and d.enabled and d.driver not in PLANNED:
                self.host.start_device(d.id)
            elif not on and self.host.device_state(d.id)[0] != "stopped":
                self.host.stop_device(d.id)
        QTimer.singleShot(300, lambda: self.update_states(force=True))

    def _toggle_enabled(self):
        devs = self._devices()
        if devs:
            self.host.set_devices_enabled([d.id for d in devs], not all(d.enabled for d in devs))

    def _update_buttons(self):
        devs = self._devices()
        rec = self.host.recording
        self.btn_add.setEnabled(not rec)
        self.btn_imp.setEnabled(not rec)
        self.btn_edit.setEnabled(len(devs) == 1)
        self.btn_dup.setEnabled(len(devs) == 1 and not rec)
        self.btn_del.setEnabled(bool(devs) and not rec)
        states = [self.host.device_state(d.id)[0] for d in devs]
        self.btn_on.setEnabled(any(s == "stopped" and d.enabled and d.driver not in PLANNED
                                   for s, d in zip(states, devs)))
        self.btn_off.setEnabled(any(s != "stopped" for s in states))
