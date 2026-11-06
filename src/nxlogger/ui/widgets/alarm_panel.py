"""
alarm_panel.py - the Alarms document (core/alarm_rules.py), the same layout as Connectivity,
Assets and Logging: a summary strip under the toolbar, one flat left list, the selection right.

  Alarms (Project tree > Alarms)               live
    left   Active, the five types (Limit H / L, Rate of change, Deviation, State, Data loss),
           Notifications - a dot (the worst state) + an icon + a count; a click opens it
    right  tabs Active (level, alarm, tag, type, setpoint, value, since, duration) · Alarm log
           (every ON / OFF; filters, Export CSV, Clear)
  A type (Project tree > Limit ...)
    left   band = the type (count), its alarms: a dot (active level / ok / none) + its icon + name
    right  the alarm: header (name, state, Edit, ⋯), its tag, tabs Overview (a sentence, its levels,
           where it notifies) · History (its ON / OFF from the log)
  Notifications (Project tree > Notifications)
    left   its profiles: a dot (last send ok / failed) + icon + name
    right  the profile: header (name, state, Edit, ⋯ Send test), Overview (type, target, levels,
           events, last result, used by)
  bar    Add ▾ (the types / the profile kinds), Edit, Duplicate, Delete | Disable | Export log, Clear
         log | Options ▾ (sound, bring to front) | search

Alarms and profiles are edited in pop-ups (DaqWindow._alarm_popup / _aprofile_popup); an alarm sits
in the group of its Level 1 type (alarm_rules.group_of).

Host API (DaqWindow):
  host.project (alarms, alarm_profiles, alarm_settings), host.alarm_engine, host.profile_results
  host.reg_icon(target, name), host.key_label(key), host.select_object(ref), host.open_tag(key)
  host.add_alarm(group, channel) / host.edit_alarm(id) / host.duplicate_alarm(id) / host.delete_alarm(id)
  host.toggle_alarm(id) / host.add_aprofile(kind) / host.edit_aprofile(id) / host.delete_aprofile(id)
  host.toggle_aprofile(id) / host.test_profile(profile) / host.alarm_settings_changed()
"""
from __future__ import annotations

import csv
import datetime as _dt
import math
import time

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMenu, QMessageBox, QSplitter, QStackedWidget,
                               QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.core import alarm_rules as ar
from nxlogger.core.alarm_rules import (ACTIVE_COLS, EVENTS, LEVELS, LOG_COLS, MAX_LOG, PROFILE_KINDS,
                                       describe_level)
from nxlogger.ui.dialogs.common import confirm
from nxlogger.ui.icons import STATUS_SIZE, level_color, level_icon, status_icon
from nxlogger.ui.theme.ui_kit import T, label, make_icon, vsep
from nxlogger.ui.widgets.connectivity import Band
from nxlogger.ui.widgets.summary_strip import SummaryStrip, plural

LIVE, NOTIFY = "live", "notify"
PROFILE_ICON = {"log": "file-text", "popup": "bell", "rest": "globe", "mqtt": "radio", "email": "send"}


def fmt_val(v, unit="", dec=3) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{v:.{dec}f} {unit}".strip()


def fmt_dur(s: float) -> str:
    s = int(max(s, 0))
    return f"{s // 3600:d}:{s // 60 % 60:02d}:{s % 60:02d}"


def target_text(p: dict) -> str:
    k = p.get("kind")
    return {"rest": f"{p.get('method', 'POST')} {p.get('url', '')}", "mqtt": p.get("topic", ""),
            "email": p.get("to", ""), "popup": "this window", "log": "alarm log"}.get(k, "")


def _tree(cols) -> QTreeWidget:
    t = QTreeWidget(); t.setColumnCount(len(cols)); t.setHeaderLabels(cols)
    t.setRootIsDecorated(False); t.setAlternatingRowColors(True); t.setUniformRowHeights(True)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.header().setStretchLastSection(True)
    return t


class _Head(QFrame):
    """The header of a selected alarm / profile: icon, name, state, Edit, ⋯; a second line."""

    def __init__(self, page, more_fill, edit):
        super().__init__()
        self.setObjectName("subBar")
        hv = QVBoxLayout(self); hv.setContentsMargins(12, 8, 12, 8); hv.setSpacing(4)
        h1 = QHBoxLayout(); h1.setSpacing(8)
        self.icon = QLabel()
        self.title = label("", name="sectionTitle")
        self.state = QLabel()
        self.btn_edit = page._button("Edit", "pencil", edit, "Edit (its pop-up)")
        self.btn_more = page._button("", "ellipsis", None, "More")
        self.btn_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more = QMenu(self.btn_more); self.more.aboutToShow.connect(lambda: more_fill(self.more))
        self.btn_more.setMenu(self.more)
        for b in (self.btn_edit, self.btn_more):
            b.setAutoRaise(True)
        h1.addWidget(self.icon); h1.addWidget(self.title); h1.addStretch(1)
        h1.addWidget(self.state); h1.addWidget(self.btn_edit); h1.addWidget(self.btn_more)
        self.line = label("", muted=True)
        self.line.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.line_icon = QLabel()                         # an app icon before the second line (no emoji)
        h2 = QHBoxLayout(); h2.setSpacing(6)
        h2.addWidget(self.line_icon); h2.addWidget(self.line, 1)
        hv.addLayout(h1); hv.addLayout(h2)

    def set(self, icon: str, title: str, state: str, token: str, line: str, line_icon: str = ""):
        self.icon.setPixmap(make_icon(icon, T["text"]).pixmap(QSize(18, 18)))
        self.title.setText(title)
        self.state.setText(state)
        self.state.setStyleSheet(f"color: {T[token] if token in T else token};")
        self.line.setText(line)
        self.line_icon.setVisible(bool(line_icon))
        if line_icon:
            self.line_icon.setPixmap(make_icon(line_icon, T["muted"]).pixmap(QSize(14, 14)))


class AlarmPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._mode = LIVE                                # live / a group key / notify
        self._sel: str | None = None                     # the alarm / profile selected
        self._items: dict = {}
        self._active_items: dict[tuple, QTreeWidgetItem] = {}
        self._log_seq = -1
        self._level_filter: int | None = None            # a summary chip: only this level (Active)

        bar = QFrame(); bar.setObjectName("docBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(2)
        self.btn_add = self._button("Add", "plus", None, "Add an alarm (by type) or a notification profile")
        self.add_menu = QMenu(self.btn_add)
        self.add_menu.aboutToShow.connect(self._fill_add)
        self.btn_add.setMenu(self.add_menu); self.btn_add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_edit = self._button("Edit", "pencil", self._edit, "Edit the selected alarm / profile")
        self.btn_dup = self._button("", "copy", self._dup, "Duplicate the selected alarm")
        self.btn_del = self._button("", "trash", self._delete, "Delete the selected alarm / profile")
        self.btn_on = self._button("Disable", "power", self._toggle, "Enable / disable the selected alarm / profile")
        self.btn_exp = self._button("Export log...", "download", self.export_log, "Export the alarm log (CSV)")
        self.btn_clear = self._button("Clear log", "trash", self.clear_log, "Clear the alarm log")
        self.btn_opts = self._button("Options", "sliders", None, "Sound and window on a new alarm")
        self.btn_opts.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        om = QMenu(self.btn_opts)
        self.opt = {}
        for key, text in (("sound", "Sound on new alarm"), ("front", "Bring window to front on new alarm")):
            a = om.addAction(text); a.setCheckable(True)
            a.toggled.connect(lambda on, k=key: self._set_opt(k, on))
            self.opt[key] = a
        self.btn_opts.setMenu(om)
        for w in (self.btn_add, self.btn_edit, self.btn_dup, self.btn_del, None, self.btn_on, None, self.btn_exp,
                  self.btn_clear, None, self.btn_opts):
            bl.addWidget(vsep() if w is None else w)
        bl.addStretch(1)
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Search")
        self.filter.setClearButtonEnabled(True); self.filter.setMaximumWidth(220); self.filter.setMinimumWidth(120)
        self.filter.textChanged.connect(lambda _: self._apply_filter())
        bl.addWidget(self.filter)
        self.summary = SummaryStrip()
        self.summary.chip_clicked.connect(self._chip)

        self.band = Band()
        self.tree = QTreeWidget(); self.tree.setObjectName("navTree")
        self.tree.setHeaderHidden(True); self.tree.setIndentation(6); self.tree.setRootIsDecorated(False)
        self.tree.setProperty("plainSel", True)
        self.tree.setIconSize(QSize(*STATUS_SIZE))
        self.tree.currentItemChanged.connect(lambda cur, _p: self._left_selected(cur))
        self.tree.itemDoubleClicked.connect(self._left_double)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._left_menu)
        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(0)
        lv.addWidget(self.band); lv.addWidget(self.tree, 1)

        # live: Active · Alarm log
        self.live = QTabWidget(); self.live.setDocumentMode(True)
        self.active = _tree(ACTIVE_COLS)
        for c, wd in enumerate((80, 70, 180, 200, 50, 100, 110, 80)):
            self.active.header().resizeSection(c, wd)
        self.active.itemDoubleClicked.connect(lambda it, _c: self._open_alarm(it.data(0, Qt.ItemDataRole.UserRole)[0]))
        self.active_empty = label("  No active alarms - they appear here while a level is tripped.", muted=True, small=True)
        self.active_empty.setContentsMargins(4, 6, 4, 6)
        aw = QWidget(); av = QVBoxLayout(aw); av.setContentsMargins(0, 0, 0, 0); av.setSpacing(0)
        av.addWidget(self.active_empty); av.addWidget(self.active, 1)
        self.live.addTab(aw, "Active")
        self.f_event = QComboBox(); self.f_event.addItems(["All events", *EVENTS])
        self.f_level = QComboBox(); self.f_level.addItems(["All levels"] + [f"Level {i + 1}" for i in range(LEVELS)])
        self.log_count = label("", muted=True, small=True)
        fbar = QFrame(); fbar.setObjectName("docBar")
        fb = QHBoxLayout(fbar); fb.setContentsMargins(8, 4, 8, 4); fb.setSpacing(6)
        fb.addWidget(self.log_count); fb.addStretch(1); fb.addWidget(self.f_event); fb.addWidget(self.f_level)
        for w in (self.f_event, self.f_level):
            w.currentIndexChanged.connect(lambda _: self.rebuild_log())
        self.logt = _tree(LOG_COLS)
        for c, wd in enumerate((90, 100, 70, 60, 180, 200, 50, 110)):
            self.logt.header().resizeSection(c, wd)
        lw = QWidget(); lw_l = QVBoxLayout(lw); lw_l.setContentsMargins(0, 0, 0, 0); lw_l.setSpacing(0)
        lw_l.addWidget(fbar); lw_l.addWidget(self.logt, 1)
        self.live.addTab(lw, "Alarm log")
        self.live.currentChanged.connect(lambda _i: self.update_live(force=True))

        # an alarm
        self.aview = QWidget(); avl = QVBoxLayout(self.aview); avl.setContentsMargins(0, 0, 0, 0); avl.setSpacing(0)
        self.ahead = _Head(self, self._alarm_more, lambda: self._sel and self.host.edit_alarm(self._sel))
        self.atabs = QTabWidget(); self.atabs.setDocumentMode(True)
        ov = QWidget(); ovl = QVBoxLayout(ov); ovl.setContentsMargins(12, 10, 12, 10); ovl.setSpacing(8)
        self.a_sentence = QLabel(); self.a_sentence.setWordWrap(True)
        self.a_levels = _tree(["Level", "Type", "Setpoint", "Hysteresis", "On-delay", "State"])
        self.a_levels.setMaximumHeight(140)
        for c, wd in enumerate((110, 70, 150, 90, 80)):
            self.a_levels.header().resizeSection(c, wd)
        self.a_notify = label("", muted=True); self.a_notify.setWordWrap(True)
        ovl.addWidget(self.a_sentence); ovl.addWidget(self.a_levels); ovl.addWidget(self.a_notify); ovl.addStretch(1)
        self.atabs.addTab(ov, "Overview")
        self.a_hist = _tree(["Time", "Level", "Event", "Value", "Setpoint"])
        self.atabs.addTab(self.a_hist, "History")
        avl.addWidget(self.ahead); avl.addWidget(self.atabs, 1)

        # a notification profile
        self.pview = QWidget(); pvl = QVBoxLayout(self.pview); pvl.setContentsMargins(0, 0, 0, 0); pvl.setSpacing(0)
        self.phead = _Head(self, self._profile_more, lambda: self._sel and self.host.edit_aprofile(self._sel))
        pw = QWidget(); self.pgrid = QGridLayout(pw); self.pgrid.setContentsMargins(12, 10, 12, 10)
        self.pgrid.setHorizontalSpacing(14); self.pgrid.setVerticalSpacing(4)
        self._pcells: list[QLabel] = []
        pvl.addWidget(self.phead); pvl.addWidget(pw); pvl.addStretch(1)

        from nxlogger.ui.widgets.assets_page import EmptyState
        self.empty = EmptyState()
        self.stack = QStackedWidget()
        for w in (self.live, self.aview, self.pview, self.empty):
            self.stack.addWidget(w)
        split = QSplitter(); split.addWidget(left); split.addWidget(self.stack)
        split.setStretchFactor(1, 1); split.setSizes([250, 790]); split.setChildrenCollapsible(False)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(bar); v.addWidget(self.summary); v.addWidget(split, 1)

    def _button(self, text, icon, slot, tip) -> QToolButton:
        b = QToolButton(); b.setToolTip(tip)
        if text:
            b.setText(text); b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.host.reg_icon(b, icon)
        if slot:
            b.clicked.connect(slot)
        return b

    # ---------------------------------------------------------------- state
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def alarms(self) -> list[dict]:
        return self.host.project.alarms

    @property
    def profiles(self) -> list[dict]:
        return self.host.project.alarm_profiles

    def _alarm(self, aid):
        return next((a for a in self.alarms if a["id"] == aid), None)

    def _profile(self, pid):
        return next((p for p in self.profiles if p["id"] == pid), None)

    def _alarm_dot(self, a) -> str:
        if not a["enabled"]:
            return "none"
        return "alarm" if self.host.alarm_engine.alarm_state(a["id"]) else "ok"

    def _group_dot(self, alarms) -> str:
        dots = {self._alarm_dot(a) for a in alarms if a["enabled"]}
        return "alarm" if "alarm" in dots else ("ok" if dots else "none")

    def show_node(self, mode: str = LIVE, select: str | None = None):
        """Project tree > Alarms (live) / a type (its key) / Notifications."""
        self._mode = mode if mode in (LIVE, NOTIFY) or mode in ar.GROUP else LIVE
        self._sel = select
        self.refresh()

    def show_live(self, tab: int = 0):
        self.show_node(LIVE)
        self.live.setCurrentIndex(tab)

    def select_alarm(self, aid: str):
        a = self._alarm(aid)
        if a is not None:
            self.show_node(ar.group_of(a), aid)

    def select_profile(self, pid: str):
        self.show_node(NOTIFY, pid)

    def _open_alarm(self, aid):
        QTimer.singleShot(0, lambda: self.select_alarm(aid))

    # ---------------------------------------------------------------- left list
    def refresh(self):
        """Structure changed (alarms, profiles, options) or another node: rebuild."""
        s = self.host.project.alarm_settings
        for k, a in self.opt.items():
            a.blockSignals(True); a.setChecked(bool(s.get(k))); a.blockSignals(False)
        self.tree.blockSignals(True)
        self.tree.clear(); self._items.clear()
        if self._mode == LIVE:
            self.band.set("Alarms", "bell", "Active alarms and the log - a type: its alarms")
            n = self.host.alarm_engine.count()
            it = QTreeWidgetItem([f"Active  ({n})"])
            it.setIcon(0, status_icon("bell", "alarm" if n else "none"))
            it.setData(0, Qt.ItemDataRole.UserRole, ("live", None))
            self.tree.addTopLevelItem(it); self._items[("live", None)] = it
            for key, name, icon, _types, what in ar.GROUPS:
                mine = [a for a in self.alarms if ar.group_of(a) == key]
                gi = QTreeWidgetItem([f"{name}  ({len(mine)})"])
                gi.setIcon(0, status_icon(icon, self._group_dot(mine) if mine else "blank",
                                          T["text"] if mine else T["muted"]))
                if not mine:
                    gi.setForeground(0, QBrush(QColor(T["muted"])))
                gi.setToolTip(0, what + " - click to open")
                gi.setData(0, Qt.ItemDataRole.UserRole, ("group", key))
                self.tree.addTopLevelItem(gi); self._items[("group", key)] = gi
            ni = QTreeWidgetItem([f"Notifications  ({len(self.profiles)})"])
            ni.setIcon(0, status_icon("send", "blank"))
            ni.setData(0, Qt.ItemDataRole.UserRole, ("notify", None))
            self.tree.addTopLevelItem(ni); self._items[("notify", None)] = ni
            self.tree.setCurrentItem(self._items[("live", None)])
        elif self._mode == NOTIFY:
            self.band.set(f"Notifications  ({len(self.profiles)})", "send",
                          "Where alarm events go: log, popup, REST, MQTT, e-mail")
            res = self.host.profile_results
            for p in self.profiles:
                r = res.get(p["id"])
                dot = "none" if not p["enabled"] else ("ok" if r is None or r[0] else "alarm")
                it = QTreeWidgetItem([p["name"]])
                it.setIcon(0, status_icon(PROFILE_ICON.get(p["kind"], "send"), dot,
                                          T["text"] if p["enabled"] else T["muted"]))
                if not p["enabled"]:
                    it.setForeground(0, QBrush(QColor(T["muted"])))
                it.setData(0, Qt.ItemDataRole.UserRole, ("profile", p["id"]))
                self.tree.addTopLevelItem(it); self._items[p["id"]] = it
        else:
            key, name, icon, _types, what = ar.GROUP[self._mode]
            mine = [a for a in self.alarms if ar.group_of(a) == key]
            self.band.set(f"{name}  ({len(mine)})", icon, what + " - the Project tree: Alarms")
            for a in mine:
                it = QTreeWidgetItem([a["name"]])
                it.setIcon(0, status_icon(ar.icon_of(a), self._alarm_dot(a), T["text"] if a["enabled"] else T["muted"]))
                if not a["enabled"]:
                    it.setForeground(0, QBrush(QColor(T["muted"])))
                it.setToolTip(0, a.get("description") or a["name"])
                it.setData(0, Qt.ItemDataRole.UserRole, ("alarm", a["id"]))
                self.tree.addTopLevelItem(it); self._items[a["id"]] = it
        ids = [k for k in self._items if isinstance(k, str)]
        if self._mode != LIVE:
            if self._sel not in ids:
                self._sel = ids[0] if ids else None
            if self._sel:
                self.tree.setCurrentItem(self._items[self._sel])
        self.tree.blockSignals(False)
        self._fill_right()
        self._apply_filter()

    def _left_selected(self, it):
        if it is None:
            return
        kind, ident = it.data(0, Qt.ItemDataRole.UserRole)
        if kind == "group":
            QTimer.singleShot(0, lambda: self.show_node(ident))
        elif kind == "notify":
            QTimer.singleShot(0, lambda: self.show_node(NOTIFY))
        elif kind in ("alarm", "profile") and ident != self._sel:
            self._sel = ident
            QTimer.singleShot(0, self._fill_right)

    def _left_double(self, it, _c):
        kind, ident = it.data(0, Qt.ItemDataRole.UserRole)
        if kind == "alarm":
            QTimer.singleShot(0, lambda: self.host.edit_alarm(ident))
        elif kind == "profile":
            QTimer.singleShot(0, lambda: self.host.edit_aprofile(ident))

    def _left_menu(self, pos):
        it = self.tree.itemAt(pos)
        if it is None:
            return
        kind, ident = it.data(0, Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        if kind == "alarm":
            self.tree.setCurrentItem(it)
            self._alarm_more(m)
        elif kind == "profile":
            self.tree.setCurrentItem(it)
            self._profile_more(m)
        else:
            return
        m.exec(self.tree.viewport().mapToGlobal(pos))

    # ---------------------------------------------------------------- right side
    def _fill_right(self):
        if self._mode == LIVE:
            self.stack.setCurrentWidget(self.live)
            self.update_live(force=True)
        elif self._sel is None:
            if self._mode == NOTIFY:
                self.empty.set("send", "No notification profiles yet",
                               "Profiles decide where alarm events go: a popup, a REST webhook, MQTT, e-mail.",
                               [("Add profile", self._menu_kinds(), True)])
            else:
                key, name, icon, _t, what = ar.GROUP[self._mode]
                self.empty.set(icon, f"No {name.split('  ')[0].lower()} alarms", what + ".",
                               [(f"Add {name.split('  ')[0].lower()} alarm", lambda: self.host.add_alarm(key, None), True)])
            self.stack.setCurrentWidget(self.empty)
        elif self._mode == NOTIFY:
            self._fill_profile()
            self.stack.setCurrentWidget(self.pview)
        else:
            self._fill_alarm()
            self.stack.setCurrentWidget(self.aview)
        self._update_buttons()
        self.refresh_summary()

    def _menu_kinds(self) -> QMenu:
        m = QMenu(self)
        for k, text in PROFILE_KINDS.items():
            m.addAction(text + "...", lambda kk=k: self.host.add_aprofile(kk))
        self._keep = m
        return m

    def _fill_alarm(self):
        a = self._alarm(self._sel)
        if a is None:
            return
        eng = self.host.alarm_engine
        lvl = eng.alarm_state(a["id"])
        state = (f"● ALARM L{lvl}", level_color(lvl)) if lvl else \
            (("● Normal", "ok") if a["enabled"] else ("○ Disabled", "muted"))
        tag, unit = eng.describe(*a["channel"]) if a.get("channel") else ("(no tag)", "")
        ref = f" · reference {eng.describe(*a['ref'])[0]}" if a.get("ref") else ""
        self.ahead.set(ar.icon_of(a), a["name"], state[0], state[1], f"{tag}{ref}", "tag")
        on = [(i, lv) for i, lv in enumerate(a["levels"]) if lv["enabled"]]
        parts = [f"L{i + 1} {describe_level(lv, unit)}" for i, lv in on]
        self.a_sentence.setText(f"Alarms on {tag} at " + ("; ".join(parts) if parts else "no level") +
                                (f". {a['description']}" if a.get("description") else "."))
        self.a_levels.clear()
        for i, lv in enumerate(a["levels"]):
            if not lv["enabled"]:
                continue
            st = eng.states.get((a["id"], i + 1))
            it = QTreeWidgetItem([f"Level {i + 1}", lv["type"], describe_level(lv, unit, extras=False),
                                  f"{lv.get('hyst', 0):g}" if lv.get("hyst") else "-",
                                  f"{lv.get('delay_s', 0):g} s" if lv.get("delay_s") else "-",
                                  "ALARM" if st and st.active else "normal"])
            it.setIcon(0, level_icon(i + 1))
            if st and st.active:
                it.setForeground(5, QBrush(QColor(level_color(i + 1))))
            self.a_levels.addTopLevelItem(it)
        names = {p["id"]: p["name"] for p in self.profiles}
        self.a_notify.setText("Notifies  " + (", ".join(names[x] for x in a["profiles"] if x in names) or "nobody "
                                              "(Edit > Notify)"))
        self.a_hist.clear()
        for ev in reversed(eng.log):
            if ev["alarm_id"] != a["id"]:
                continue
            u = "s" if ev["type"] == "D" else ev["unit"]
            it = QTreeWidgetItem([time.strftime("%m-%d %H:%M:%S", time.localtime(ev["t"])), f"L{ev['level']}",
                                  ev["event"], fmt_val(ev["value"], u), fmt_val(ev["setpoint"], u)])
            it.setIcon(1, level_icon(ev["level"], 8))
            it.setForeground(2, QBrush(QColor(level_color(ev["level"]) if ev["event"] == "ON" else T["ok"])))
            self.a_hist.addTopLevelItem(it)
        self.atabs.setTabText(1, f"History ({self.a_hist.topLevelItemCount()})")

    def _fill_profile(self):
        p = self._profile(self._sel)
        if p is None:
            return
        r = self.host.profile_results.get(p["id"])
        state = ("○ Disabled", "muted") if not p["enabled"] else \
            (("● Ready", "ok") if r is None else (("● Last send OK", "ok") if r[0] else ("● Last send failed", "bad")))
        self.phead.set(PROFILE_ICON.get(p["kind"], "send"), p["name"], state[0], state[1],
                       f"{PROFILE_KINDS.get(p['kind'], p['kind'])} · {target_text(p)}")
        used = [a["name"] for a in self.alarms if p["id"] in a.get("profiles", [])]
        rows = [("Type", PROFILE_KINDS.get(p["kind"], p["kind"])), ("Target", target_text(p) or "-"),
                ("Levels", " ".join(f"L{x}" for x in p.get("levels", [])) or "-"),
                ("Events", " ".join(e for e in EVENTS if p.get(e.lower())) or "-"),
                ("Last result", (f"{time.strftime('%H:%M:%S', time.localtime(r[2]))}  {r[1]}" if r else "-")),
                ("Used by", (f"{len(used)} alarm(s): " + ", ".join(used[:6]) + (" …" if len(used) > 6 else ""))
                 if used else "no alarm (an alarm's pop-up > Notify)")]
        for c in self._pcells:
            c.setParent(None); c.deleteLater()
        self._pcells = []
        for i, (k, val) in enumerate(rows):
            a_, b_ = label(k, muted=True, small=True), QLabel(str(val))
            b_.setWordWrap(True); b_.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if k == "Last result" and r:
                b_.setStyleSheet(f"color: {T['ok'] if r[0] else T['bad']};")
            self.pgrid.addWidget(a_, i, 0); self.pgrid.addWidget(b_, i, 1)
            self._pcells += [a_, b_]
        self.pgrid.setColumnStretch(1, 1)

    # ---------------------------------------------------------------- live
    def update_live(self, force: bool = False):
        """2 Hz: the summary, the active list / log, the dots."""
        eng = self.host.alarm_engine
        self.refresh_summary()
        if self._mode == LIVE:
            it = self._items.get(("live", None))
            if it is not None:
                n = eng.count()
                it.setText(0, f"Active  ({n})")
                it.setIcon(0, status_icon("bell", "alarm" if n else "none"))
            for key, _n, icon, _t, _w in ar.GROUPS:
                gi = self._items.get(("group", key))
                mine = [a for a in self.alarms if ar.group_of(a) == key]
                if gi is not None and mine:
                    gi.setIcon(0, status_icon(icon, self._group_dot(mine)))
            if not (force or self.isVisible()):
                return
            if self.live.currentIndex() == 0:
                self._fill_active(eng)
            elif self._log_seq != eng.seq:
                self.rebuild_log()
        elif self._mode not in (LIVE, NOTIFY):
            for aid, it in self._items.items():
                a = self._alarm(aid)
                if a is not None:
                    it.setIcon(0, status_icon(ar.icon_of(a), self._alarm_dot(a),
                                              T["text"] if a["enabled"] else T["muted"]))
            if self.stack.currentWidget() is self.aview and (force or self._log_seq != eng.seq):
                self._fill_alarm()
                self._log_seq = eng.seq

    def _fill_active(self, eng):
        rows = [r for r in eng.active_rows() if self._level_filter in (None, r["level"])]
        now = time.time()
        keys = {r["key"] for r in rows}
        for k in [k for k in self._active_items if k not in keys]:
            it = self._active_items.pop(k)
            self.active.takeTopLevelItem(self.active.indexOfTopLevelItem(it))
        for pos, r in enumerate(rows):
            it = self._active_items.get(r["key"])
            if it is None:
                it = self._active_items[r["key"]] = QTreeWidgetItem()
                it.setData(0, Qt.ItemDataRole.UserRole, r["key"])
                self.active.insertTopLevelItem(pos, it)
            elif self.active.indexOfTopLevelItem(it) != pos:
                self.active.takeTopLevelItem(self.active.indexOfTopLevelItem(it))
                self.active.insertTopLevelItem(pos, it)
            u = r["unit"] if r["type"] != "D" else "s"
            vals = [f"Level {r['level']}", "ALARM", r["name"], r["tag"], r["type"], fmt_val(r["setpoint"], u, 3),
                    fmt_val(r["value"], u, 3), time.strftime("%H:%M:%S", time.localtime(r["since"])),
                    fmt_dur(now - r["since"])]
            for c, v in enumerate(vals):
                if it.text(c) != v:
                    it.setText(c, v)
            it.setIcon(0, level_icon(r["level"]))
            col = QBrush(QColor(level_color(r["level"])))
            it.setForeground(0, col); it.setForeground(1, col)
        self.active_empty.setVisible(not rows)
        self.live.setTabText(0, f"Active ({len(rows)})")

    def add_event(self, ev: dict):
        """A new engine event: on top of the log (when it passes the filter)."""
        if self._passes(ev):
            self.logt.insertTopLevelItem(0, self._log_item(ev))
            while self.logt.topLevelItemCount() > MAX_LOG:
                self.logt.takeTopLevelItem(self.logt.topLevelItemCount() - 1)
        self._log_seq = self.host.alarm_engine.seq
        self._count()

    def _passes(self, ev) -> bool:
        e, lv_, txt = self.f_event.currentIndex(), self.f_level.currentIndex(), self.filter.text().strip().lower()
        return (e == 0 or ev["event"] == EVENTS[e - 1]) and (lv_ == 0 or ev["level"] == lv_) and \
            (not txt or txt in ev["name"].lower() or txt in ev["tag"].lower())

    def _log_item(self, ev) -> QTreeWidgetItem:
        t = time.localtime(ev["t"])
        unit = "s" if ev["type"] == "D" else ev["unit"]
        it = QTreeWidgetItem([time.strftime("%Y-%m-%d", t), time.strftime("%H:%M:%S", t) + f".{int(ev['t'] % 1 * 1000):03d}",
                              f"L{ev['level']}", ev["event"], ev["name"], ev["tag"], ev["type"],
                              fmt_val(ev["value"], unit), fmt_val(ev["setpoint"], unit)])
        it.setIcon(2, level_icon(ev["level"], 8))
        it.setForeground(3, QBrush(QColor(level_color(ev["level"]) if ev["event"] == "ON" else T["ok"])))
        return it

    def rebuild_log(self):
        eng = self.host.alarm_engine
        self.logt.clear()
        self.logt.addTopLevelItems([self._log_item(ev) for ev in reversed(eng.log) if self._passes(ev)])
        self._log_seq = eng.seq
        self._count()

    def _count(self):
        self.log_count.setText(f"{self.logt.topLevelItemCount()} shown · {len(self.host.alarm_engine.log)} / {MAX_LOG}")

    # ---------------------------------------------------------------- summary strip
    def refresh_summary(self):
        eng = self.host.alarm_engine
        rows = eng.active_rows()
        today0 = _dt.datetime.combine(_dt.date.today(), _dt.time()).timestamp()
        today = sum(1 for ev in eng.log if ev["event"] == "ON" and ev["t"] >= today0)
        off = sum(1 for a in self.alarms if not a["enabled"])
        chips = []
        for lv in range(1, LEVELS + 1):
            n = sum(1 for r in rows if r["level"] == lv)
            if n:
                chips.append((lv, f"{n} L{lv}", "bad" if lv == 1 else ("accent" if lv == 2 else "warn")))
        chips += [("|", "", ""), ("today", f"{today:,} today", ""),
                  ("|", "", ""), ("defs", f"{plural(len(self.alarms), 'alarm')}" + (f" · {off} disabled" if off else ""), "")]
        if rows:
            title, state = f"● {plural(len(rows), 'active alarm')}", "bad"
            r = rows[0]
            u = "s" if r["type"] == "D" else r["unit"]
            issue = (f"{r['name']} L{r['level']} {r['type']} - {fmt_val(r['value'], u)} (setpoint {fmt_val(r['setpoint'], u)}) "
                     f"since {time.strftime('%H:%M:%S', time.localtime(r['since']))}"
                     + (f"  (+{len(rows) - 1} more)" if len(rows) > 1 else ""), "bad",
                     [("Open alarm ›", lambda aid=r["key"][0]: self.select_alarm(aid))])
        else:
            title, state, issue = "● No active alarms", "ok", None
        self.summary.set(state, title, chips, issue)

    def _chip(self, key):
        """A level chip: only that level in Active; the others: figures."""
        if isinstance(key, int):
            self._level_filter = key
            if self._mode != LIVE:
                self.show_live(0)
        else:
            self._level_filter = None
            self.summary.clear_filter()
            if key == "today":
                self.show_live(1)
        self._fill_active(self.host.alarm_engine)
        self.refresh_summary()

    def _apply_filter(self):
        text = self.filter.text().strip().lower()
        for k, it in self._items.items():
            if isinstance(k, str):
                it.setHidden(bool(text) and text not in it.text(0).lower())
        if self._mode == LIVE and self.live.currentIndex() == 1:
            self.rebuild_log()

    # ---------------------------------------------------------------- actions
    def _fill_add(self):
        m = self.add_menu
        m.clear()
        for key, name, icon, _t, what in ar.GROUPS:
            if self._mode in (LIVE, key):
                a = m.addAction(make_icon(icon, T["text"]), f"{name.split('  ')[0]} alarm...",
                                lambda k=key: self.host.add_alarm(k, None))
                a.setToolTip(what)
        if self._mode in (LIVE, NOTIFY):
            if self._mode == LIVE:
                m.addSeparator()
            sub = m if self._mode == NOTIFY else m.addMenu("Notification profile")
            for k, text in PROFILE_KINDS.items():
                sub.addAction(make_icon(PROFILE_ICON.get(k, "send"), T["text"]), text + "...",
                              lambda kk=k: self.host.add_aprofile(kk))

    def _alarm_more(self, m: QMenu):
        m.clear()
        a = self._alarm(self._sel)
        if a is None:
            return
        m.addAction("Edit...", lambda: self.host.edit_alarm(a["id"]))
        m.addAction("Disable" if a["enabled"] else "Enable", lambda: self.host.toggle_alarm(a["id"]))
        m.addAction("Duplicate", lambda: self.host.duplicate_alarm(a["id"]))
        if a.get("channel"):
            m.addAction("Open tag...", lambda: self.host.open_tag(tuple(a["channel"])))
        m.addSeparator()
        m.addAction("Delete...", lambda: self.host.delete_alarm(a["id"]))

    def _profile_more(self, m: QMenu):
        m.clear()
        p = self._profile(self._sel)
        if p is None:
            return
        m.addAction("Edit...", lambda: self.host.edit_aprofile(p["id"]))
        m.addAction("Send test", lambda: self.host.test_profile(p))
        m.addAction("Disable" if p["enabled"] else "Enable", lambda: self.host.toggle_aprofile(p["id"]))
        m.addSeparator()
        m.addAction("Delete...", lambda: self.host.delete_aprofile(p["id"]))

    def _edit(self):
        if self._sel and self._mode == NOTIFY:
            self.host.edit_aprofile(self._sel)
        elif self._sel and self._mode != LIVE:
            self.host.edit_alarm(self._sel)

    def _dup(self):
        if self._sel and self._mode not in (LIVE, NOTIFY):
            self.host.duplicate_alarm(self._sel)

    def _delete(self):
        if self._sel and self._mode == NOTIFY:
            self.host.delete_aprofile(self._sel)
        elif self._sel and self._mode != LIVE:
            self.host.delete_alarm(self._sel)

    def _toggle(self):
        if self._sel and self._mode == NOTIFY:
            self.host.toggle_aprofile(self._sel)
        elif self._sel and self._mode != LIVE:
            self.host.toggle_alarm(self._sel)

    def _update_buttons(self):
        item = self._profile(self._sel) if self._mode == NOTIFY else (self._alarm(self._sel) if self._mode != LIVE else None)
        for b in (self.btn_edit, self.btn_del, self.btn_on):
            b.setEnabled(item is not None)
        self.btn_dup.setEnabled(item is not None and self._mode not in (LIVE, NOTIFY))
        self.btn_on.setText("Disable" if item is None or item["enabled"] else "Enable")

    def _set_opt(self, key, on):
        self.host.project.alarm_settings[key] = on
        self.host.alarm_settings_changed()

    def export_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export alarm log",
                                              f"alarm_log_{time.strftime('%Y%m%d_%H%M%S')}.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["time_iso", "time_unix_s", "level", "event", "alarm", "tag", "type",
                            "value", "setpoint", "unit"])
                for ev in self.host.alarm_engine.log:
                    if self._passes(ev):
                        w.writerow([_dt.datetime.fromtimestamp(ev["t"]).isoformat(timespec="milliseconds"),
                                    f"{ev['t']:.3f}", ev["level"], ev["event"], ev["name"], ev["tag"],
                                    ev["type"], "" if math.isnan(ev["value"]) else f"{ev['value']:.7g}",
                                    f"{ev['setpoint']:g}", "s" if ev["type"] == "D" else ev["unit"]])
        except OSError as e:
            QMessageBox.warning(self, "Export alarm log", str(e))

    def clear_log(self):
        if confirm(self, "Clear alarm log", "Clear all entries of the alarm log?", "Clear log",
                   "Active alarms are not affected. Export the log first to keep it."):
            self.host.alarm_engine.clear_log()
            self.rebuild_log()
