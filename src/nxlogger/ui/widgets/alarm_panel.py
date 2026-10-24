"""The Alarms document: active alarms, definitions, alarm log, notification profiles."""
from __future__ import annotations

import copy
import csv
import datetime as _dt
import math
import time
import uuid

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QBrush, QColor, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QMenu, QMessageBox, QTabWidget, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.core.alarm_rules import (ACTIVE_COLS, DEF_COLS, EVENTS, LEVELS, LOG_COLS, MAX_LOG,
                                       PROFILE_KINDS, PROF_COLS, describe_level, new_alarm,
                                       new_profile)
from nxlogger.ui.dialogs.alarm_dialog import AlarmDialog
from nxlogger.ui.dialogs.alarm_profile_dialog import AlarmProfileDialog
from nxlogger.ui.icons import level_color, level_icon
from nxlogger.ui.theme.ui_kit import T, label


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


class AlarmPage(QWidget):
    """
    The closable "Alarms" document: Active alarms, Definitions (add / edit /
    delete / enable), Alarm log (filter, export, clear) and Notification profiles.

    host API:
      host.project.alarms / .alarm_profiles / .alarm_settings
      host.alarm_engine, host.channel_choices(), host.reg_icon(target, name, token)
      host.alarms_changed()        definitions or profiles changed (dirty, engine, tree)
      host.alarm_settings_changed()
      host.test_profile(profile)   send a test event
      host.profile_results         {profile id: (ok, text, time)}
    """

    def __init__(self, host):
        super().__init__()
        self.host = host
        self._log_seq = -1
        self._items: dict[tuple, QTreeWidgetItem] = {}

        bar = QFrame(); bar.setObjectName("docBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(4)
        self.summary = QLabel(); self.summary.setTextFormat(Qt.TextFormat.RichText)
        bl.addWidget(self.summary)
        bl.addStretch(1)
        self.btn_opts = self._tool("Options", "sliders", None, bl)
        self.btn_opts.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(self)
        self.opt = {}
        for key, text, tip in (("sound", "Sound on new alarm", "System beep on every new alarm"),
                               ("front", "Bring window to front on new alarm", "")):
            a = m.addAction(text); a.setCheckable(True); a.setToolTip(tip)
            a.toggled.connect(lambda on, k=key: self._set_opt(k, on))
            self.opt[key] = a
        self.btn_opts.setMenu(m)

        self.tabs = QTabWidget(); self.tabs.setObjectName("subTabs"); self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_active(), "Active alarms")
        self.tabs.addTab(self._build_defs(), "Definitions")
        self.tabs.addTab(self._build_log(), "Alarm log")
        self.tabs.addTab(self._build_profiles(), "Notifications")
        self.tabs.currentChanged.connect(lambda _: self.update_live(force=True))

        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(bar); v.addWidget(self.tabs, 1)

    # ---- helpers
    def _tool(self, text, icon, slot, layout, tip=""):
        b = QToolButton(); b.setText(text); b.setToolTip(tip or text)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.host.reg_icon(b, icon)
        if slot:
            b.clicked.connect(slot)
        layout.addWidget(b)
        return b

    def _subbar(self, buttons, extra=None):
        bar = QFrame(); bar.setObjectName("subBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(4)
        out = []
        for text, icon, slot in buttons:
            if text is None:
                bl.addStretch(1); continue
            out.append(self._tool(text, icon, slot, bl))
        for w in extra or []:
            bl.addWidget(w)
        return bar, out

    @staticmethod
    def _tree(cols, check=False) -> QTreeWidget:
        t = QTreeWidget(); t.setColumnCount(len(cols)); t.setHeaderLabels(cols)
        t.setRootIsDecorated(False); t.setAlternatingRowColors(True); t.setUniformRowHeights(True)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        t.header().setStretchLastSection(True)
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        return t

    @staticmethod
    def _page(*widgets) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        for x in widgets:
            v.addWidget(x, 1 if isinstance(x, QTreeWidget) else 0)
        return w

    # ---- pages
    def _build_active(self):
        self.active = self._tree(ACTIVE_COLS)
        for c, wd in enumerate((80, 130, 180, 200, 50, 100, 110, 80)):
            self.active.header().resizeSection(c, wd)
        self.active.customContextMenuRequested.connect(self._active_menu)
        self.active_empty = label("  No active alarms. Alarms appear here while a level is tripped.",
                                  muted=True, small=True)
        self.active_empty.setContentsMargins(4, 6, 4, 6)
        return self._page(self.active_empty, self.active)

    def _build_defs(self):
        bar, (self.d_add, self.d_edit, self.d_del, self.d_dup, self.d_on, self.d_off) = self._subbar([
            ("Add alarm", "plus", self.add_alarm), ("Edit", "pencil", self.edit_alarm),
            ("Delete", "trash", self.delete_alarm), ("Duplicate", "copy", self.duplicate_alarm),
            (None, None, None), ("Enable all", "power", lambda: self._enable_all(True)),
            ("Disable all", "power", lambda: self._enable_all(False))])
        self.defs = self._tree(DEF_COLS)
        for c, wd in enumerate((200, 200, 110, 110, 110, 110, 140)):
            self.defs.header().resizeSection(c, wd)
        self.defs.itemDoubleClicked.connect(lambda *_: QTimer.singleShot(0, self.edit_alarm))
        self.defs.itemChanged.connect(self._def_toggled)
        self.defs.customContextMenuRequested.connect(self._defs_menu)
        return self._page(bar, self.defs)

    def _build_log(self):
        self.f_event = QComboBox(); self.f_event.addItems(["All events", *EVENTS])
        self.f_level = QComboBox(); self.f_level.addItems(["All levels"] + [f"Level {i + 1}" for i in range(LEVELS)])
        self.f_text = QLineEdit(); self.f_text.setPlaceholderText("Filter alarm / tag"); self.f_text.setClearButtonEnabled(True)
        self.f_text.setMaximumWidth(220)
        self.log_count = label("", muted=True, small=True)
        bar, _ = self._subbar([("Export CSV...", "download", self.export_log),
                               ("Clear log", "trash", self.clear_log), (None, None, None)],
                              [self.log_count, self.f_event, self.f_level, self.f_text])
        for w in (self.f_event, self.f_level):
            w.currentIndexChanged.connect(lambda _: self.rebuild_log())
        self.f_text.textChanged.connect(lambda _: self.rebuild_log())
        self.logt = self._tree(LOG_COLS)
        for c, wd in enumerate((90, 100, 70, 60, 180, 200, 50, 110)):
            self.logt.header().resizeSection(c, wd)
        return self._page(bar, self.logt)

    def _build_profiles(self):
        bar, _ = self._subbar([("Add profile", "plus", self.add_profile), ("Edit", "pencil", self.edit_profile),
                               ("Delete", "trash", self.delete_profile), ("Send test", "send", self.test_profile),
                               (None, None, None)])
        self.profs = self._tree(PROF_COLS)
        for c, wd in enumerate((180, 140, 260, 110, 120)):
            self.profs.header().resizeSection(c, wd)
        self.profs.itemDoubleClicked.connect(lambda *_: QTimer.singleShot(0, self.edit_profile))
        self.profs.itemChanged.connect(self._prof_toggled)
        self.profs.customContextMenuRequested.connect(self._prof_menu)
        hint = label("  Profiles decide where alarm events go. Each alarm picks its profiles in its "
                     "definition (Notify).", muted=True, small=True)
        hint.setContentsMargins(4, 6, 4, 6)
        return self._page(bar, hint, self.profs)

    # ---- refresh
    def refresh(self):
        """Structure changed: definitions, profiles, options."""
        s = self.host.project.alarm_settings
        for k, a in self.opt.items():
            a.blockSignals(True); a.setChecked(bool(s.get(k))); a.blockSignals(False)
        self._fill_defs()
        self._fill_profiles()
        self.rebuild_log()
        self.update_live(force=True)

    def _choices(self):
        return {(tid, idx): (lbl, col, unit) for tid, idx, lbl, col, unit in self.host.channel_choices()}

    def _fill_defs(self):
        ch = self._choices()
        profs = {p["id"]: p["name"] for p in self.host.project.alarm_profiles}
        sel = {it.data(0, Qt.ItemDataRole.UserRole) for it in self.defs.selectedItems()}
        self.defs.blockSignals(True); self.defs.clear()
        for a in self.host.project.alarms:
            lbl, col, unit = ch.get(tuple(a["channel"]) if a.get("channel") else None, ("(no channel)", T["muted"], ""))
            it = QTreeWidgetItem([a["name"], lbl] + [describe_level(lv, unit) for lv in a["levels"]] +
                                 [", ".join(profs[p] for p in a["profiles"] if p in profs) or "-", ""])
            it.setData(0, Qt.ItemDataRole.UserRole, a["id"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Checked if a["enabled"] else Qt.CheckState.Unchecked)
            for i, lv in enumerate(a["levels"]):
                if lv["enabled"]:
                    it.setIcon(2 + i, level_icon(i + 1, 8))
            if a.get("description"):
                it.setToolTip(0, a["description"])
            if not a["enabled"]:
                for c in range(len(DEF_COLS)):
                    it.setForeground(c, QBrush(QColor(T["muted"])))
            self.defs.addTopLevelItem(it)
            it.setSelected(a["id"] in sel)
        self.defs.blockSignals(False)
        if not self.host.project.alarms:
            it = QTreeWidgetItem(["No alarms yet - click Add alarm"]); it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setForeground(0, QBrush(QColor(T["muted"])))
            self.defs.addTopLevelItem(it)

    def _fill_profiles(self):
        res = self.host.profile_results
        self.profs.blockSignals(True); self.profs.clear()
        for p in self.host.project.alarm_profiles:
            r = res.get(p["id"])
            it = QTreeWidgetItem([p["name"], PROFILE_KINDS.get(p["kind"], p["kind"]), target_text(p),
                                  " ".join(f"L{x}" for x in p["levels"]),
                                  " ".join(e for e in EVENTS if p.get(e.lower())),
                                  (f"{time.strftime('%H:%M:%S', time.localtime(r[2]))}  {r[1]}" if r else "-")])
            it.setData(0, Qt.ItemDataRole.UserRole, p["id"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Checked if p["enabled"] else Qt.CheckState.Unchecked)
            if r:
                it.setForeground(5, QBrush(QColor(T["ok" if r[0] else "bad"])))
            if not p["enabled"]:
                for c in range(5):
                    it.setForeground(c, QBrush(QColor(T["muted"])))
            self.profs.addTopLevelItem(it)
        self.profs.blockSignals(False)

    def update_live(self, force: bool = False):
        """2 Hz: summary, active list, definition states."""
        eng = self.host.alarm_engine
        act = eng.count()
        if act:
            self.summary.setText(f"<b style='color:{T['bad']}'>● {act} active</b>")
        else:
            self.summary.setText(f"<span style='color:{T['ok']}'>●</span> No active alarms"
                                 f"&nbsp;&nbsp;·&nbsp;&nbsp;{sum(a['enabled'] for a in self.host.project.alarms)}"
                                 f" of {len(self.host.project.alarms)} alarms enabled")
        if not (force or self.isVisible()):
            return
        tab = self.tabs.currentIndex()
        if tab == 0 or force:
            self._fill_active(eng)
        if tab == 1 or force:
            for i in range(self.defs.topLevelItemCount()):
                it = self.defs.topLevelItem(i)
                aid = it.data(0, Qt.ItemDataRole.UserRole)
                if aid is None:
                    continue
                lvl = eng.alarm_state(aid)
                a = eng.by_id.get(aid)
                txt = f"ALARM L{lvl}" if lvl else ("Normal" if a and a["enabled"] else "Disabled")
                if it.text(len(DEF_COLS) - 1) != txt:
                    self.defs.blockSignals(True)
                    it.setText(len(DEF_COLS) - 1, txt)
                    it.setIcon(len(DEF_COLS) - 1, level_icon(lvl, 8) if lvl else QIcon())
                    it.setForeground(len(DEF_COLS) - 1, QBrush(QColor(level_color(lvl) if lvl else T["muted"])))
                    self.defs.blockSignals(False)
        if (tab == 2 or force) and self._log_seq != eng.seq:
            self.rebuild_log()

    def _fill_active(self, eng):
        rows = eng.active_rows()
        now = time.time()
        keys = {r["key"] for r in rows}
        for k in [k for k in self._items if k not in keys]:
            it = self._items.pop(k)
            self.active.takeTopLevelItem(self.active.indexOfTopLevelItem(it))
        for pos, r in enumerate(rows):
            it = self._items.get(r["key"])
            if it is None:
                it = self._items[r["key"]] = QTreeWidgetItem()
                it.setData(0, Qt.ItemDataRole.UserRole, r["key"])
                self.active.insertTopLevelItem(pos, it)
            elif self.active.indexOfTopLevelItem(it) != pos:
                sel = it.isSelected()
                self.active.takeTopLevelItem(self.active.indexOfTopLevelItem(it))
                self.active.insertTopLevelItem(pos, it); it.setSelected(sel)
            vals = [f"Level {r['level']}", "ALARM", r["name"], r["tag"], r["type"],
                    fmt_val(r["setpoint"], r["unit"] if r["type"] != "D" else "s", 3),
                    fmt_val(r["value"], r["unit"] if r["type"] != "D" else "s", 3),
                    time.strftime("%H:%M:%S", time.localtime(r["since"])), fmt_dur(now - r["since"])]
            for c, v in enumerate(vals):
                if it.text(c) != v:
                    it.setText(c, v)
            it.setIcon(0, level_icon(r["level"]))
            col = QBrush(QColor(level_color(r["level"])))
            it.setForeground(0, col); it.setForeground(1, col)
        self.active_empty.setVisible(not rows)

    def add_event(self, ev: dict):
        """New engine event: put it on top of the log (if it passes the filter)."""
        if self._passes(ev):
            self.logt.insertTopLevelItem(0, self._log_item(ev))
            while self.logt.topLevelItemCount() > MAX_LOG:
                self.logt.takeTopLevelItem(self.logt.topLevelItemCount() - 1)
        self._log_seq = self.host.alarm_engine.seq
        self._count()

    def _passes(self, ev) -> bool:
        e, l, txt = self.f_event.currentIndex(), self.f_level.currentIndex(), self.f_text.text().strip().lower()
        return (e == 0 or ev["event"] == EVENTS[e - 1]) and (l == 0 or ev["level"] == l) and \
            (not txt or txt in ev["name"].lower() or txt in ev["tag"].lower())

    def _log_item(self, ev) -> QTreeWidgetItem:
        t = time.localtime(ev["t"])
        unit = "s" if ev["type"] == "D" else ev["unit"]
        it = QTreeWidgetItem([time.strftime("%Y-%m-%d", t),
                              time.strftime("%H:%M:%S", t) + f".{int(ev['t'] % 1 * 1000):03d}",
                              f"L{ev['level']}", ev["event"], ev["name"], ev["tag"], ev["type"],
                              fmt_val(ev["value"], unit), fmt_val(ev["setpoint"], unit)])
        it.setIcon(2, level_icon(ev["level"], 8))
        tok = level_color(ev["level"]) if ev["event"] == "ON" else T["ok"]
        it.setForeground(3, QBrush(QColor(tok)))
        return it

    def rebuild_log(self):
        eng = self.host.alarm_engine
        self.logt.clear()
        self.logt.addTopLevelItems([self._log_item(ev) for ev in reversed(eng.log) if self._passes(ev)])
        self._log_seq = eng.seq
        self._count()

    def _count(self):
        self.log_count.setText(f"{self.logt.topLevelItemCount()} shown · "
                               f"{len(self.host.alarm_engine.log)} / {MAX_LOG}  ")

    # ---- active
    def _active_menu(self, pos):
        it = self.active.itemAt(pos)
        if it is not None:
            aid = it.data(0, Qt.ItemDataRole.UserRole)[0]
            m = QMenu(self)
            m.addAction("Edit alarm definition...", lambda: (self.select_alarm(aid),
                                                             QTimer.singleShot(0, self.edit_alarm)))
            m.exec(self.active.viewport().mapToGlobal(pos))

    def _set_opt(self, key, on):
        self.host.project.alarm_settings[key] = on
        self.host.alarm_settings_changed()

    # ---- definitions
    def _sel_alarm(self) -> dict | None:
        it = self.defs.currentItem()
        aid = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        return next((a for a in self.host.project.alarms if a["id"] == aid), None)

    def select_alarm(self, aid: str):
        self.tabs.setCurrentIndex(1)
        for i in range(self.defs.topLevelItemCount()):
            it = self.defs.topLevelItem(i)
            if it.data(0, Qt.ItemDataRole.UserRole) == aid:
                self.defs.setCurrentItem(it); self.defs.scrollToItem(it)

    def select_profile(self, pid: str):
        self.tabs.setCurrentIndex(3)
        for i in range(self.profs.topLevelItemCount()):
            it = self.profs.topLevelItem(i)
            if it.data(0, Qt.ItemDataRole.UserRole) == pid:
                self.profs.setCurrentItem(it)

    def add_alarm(self, channel=None):
        ch = self.host.channel_choices()
        if not ch:
            QMessageBox.information(self, "Add alarm", "Add a topic with channels first.")
            return
        a = new_alarm(channel or ch[0][:2])
        a["profiles"] = [p["id"] for p in self.host.project.alarm_profiles if p["enabled"]]
        a["levels"][0]["enabled"] = True
        dlg = AlarmDialog(self, a, ch, self.host.project.alarm_profiles)
        dlg.setWindowTitle("Add alarm")
        if dlg.name.text() == "New alarm":
            dlg.name.clear(); dlg.name.setPlaceholderText("default: the channel name")
        if dlg.exec():
            self.host.project.alarms.append(dlg.result_alarm())
            self.host.alarms_changed()
            self.select_alarm(self.host.project.alarms[-1]["id"])

    def edit_alarm(self, aid: str | None = None):
        a = next((x for x in self.host.project.alarms if x["id"] == aid), None) if aid else self._sel_alarm()
        if a is None:
            return
        dlg = AlarmDialog(self, a, self.host.channel_choices(), self.host.project.alarm_profiles)
        if dlg.exec():
            a.clear(); a.update(dlg.result_alarm())
            self.host.alarms_changed()

    def delete_alarm(self):
        items = [it for it in self.defs.selectedItems() if it.data(0, Qt.ItemDataRole.UserRole)]
        if not items:
            return
        names = ", ".join(it.text(0) for it in items[:5]) + (" ..." if len(items) > 5 else "")
        if QMessageBox.question(self, "Delete alarm", f"Delete {len(items)} alarm(s): {names}?") \
                != QMessageBox.StandardButton.Yes:
            return
        ids = {it.data(0, Qt.ItemDataRole.UserRole) for it in items}
        self.host.project.alarms[:] = [a for a in self.host.project.alarms if a["id"] not in ids]
        self.host.alarms_changed()

    def duplicate_alarm(self):
        a = self._sel_alarm()
        if a is None:
            return
        b = copy.deepcopy(a); b["id"] = uuid.uuid4().hex[:8]; b["name"] = a["name"] + " (copy)"
        self.host.project.alarms.insert(self.host.project.alarms.index(a) + 1, b)
        self.host.alarms_changed()
        self.select_alarm(b["id"])

    def toggle_alarm(self, aid: str):
        a = next((x for x in self.host.project.alarms if x["id"] == aid), None)
        if a:
            a["enabled"] = not a["enabled"]
            self.host.alarms_changed()

    def _enable_all(self, on: bool):
        for a in self.host.project.alarms:
            a["enabled"] = on
        self.host.alarms_changed()

    def _def_toggled(self, it, col):
        aid = it.data(0, Qt.ItemDataRole.UserRole)
        a = next((x for x in self.host.project.alarms if x["id"] == aid), None)
        if a is None or col != 0:
            return
        on = it.checkState(0) == Qt.CheckState.Checked
        if on != a["enabled"]:
            a["enabled"] = on
            QTimer.singleShot(0, self.host.alarms_changed)   # never rebuild inside itemChanged

    def _defs_menu(self, pos):
        it = self.defs.itemAt(pos)
        a = None
        if it is not None and it.data(0, Qt.ItemDataRole.UserRole):
            self.defs.setCurrentItem(it); a = self._sel_alarm()
        m = QMenu(self)
        m.addAction("Add alarm...", self.add_alarm)
        if a:
            m.addAction("Edit...", lambda: QTimer.singleShot(0, self.edit_alarm))
            m.addAction("Disable" if a["enabled"] else "Enable", lambda: self.toggle_alarm(a["id"]))
            m.addAction("Duplicate", self.duplicate_alarm)
            m.addSeparator()
            m.addAction("Delete", self.delete_alarm)
        m.exec(self.defs.viewport().mapToGlobal(pos))

    # ---- log
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
        if QMessageBox.question(self, "Clear alarm log", "Clear all entries of the alarm log?\n"
                                "Active alarms are not affected.") == QMessageBox.StandardButton.Yes:
            self.host.alarm_engine.clear_log()
            self.rebuild_log()

    # ---- profiles
    def _sel_profile(self) -> dict | None:
        it = self.profs.currentItem()
        pid = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        return next((p for p in self.host.project.alarm_profiles if p["id"] == pid), None)

    def add_profile(self, kind: str = "popup"):
        dlg = AlarmProfileDialog(self, new_profile(kind))
        if dlg.exec():
            self.host.project.alarm_profiles.append(dlg.result_profile())
            self.host.alarms_changed()

    def edit_profile(self, pid: str | None = None):
        p = next((x for x in self.host.project.alarm_profiles if x["id"] == pid), None) if pid \
            else self._sel_profile()
        if p is None:
            return
        dlg = AlarmProfileDialog(self, p)
        if dlg.exec():
            p.clear(); p.update(dlg.result_profile())
            self.host.alarms_changed()

    def delete_profile(self):
        p = self._sel_profile()
        if p is None or QMessageBox.question(self, "Delete profile", f"Delete notification profile "
                                             f"'{p['name']}'?") != QMessageBox.StandardButton.Yes:
            return
        self.host.project.alarm_profiles.remove(p)
        for a in self.host.project.alarms:
            a["profiles"] = [x for x in a["profiles"] if x != p["id"]]
        self.host.alarms_changed()

    def test_profile(self):
        p = self._sel_profile()
        if p is not None:
            self.host.test_profile(p)

    def toggle_profile(self, pid: str):
        p = next((x for x in self.host.project.alarm_profiles if x["id"] == pid), None)
        if p:
            p["enabled"] = not p["enabled"]
            self.host.alarms_changed()

    def _prof_toggled(self, it, col):
        p = next((x for x in self.host.project.alarm_profiles
                  if x["id"] == it.data(0, Qt.ItemDataRole.UserRole)), None)
        if p is None or col != 0:
            return
        on = it.checkState(0) == Qt.CheckState.Checked
        if on != p["enabled"]:
            p["enabled"] = on
            QTimer.singleShot(0, self.host.alarms_changed)

    def _prof_menu(self, pos):
        it = self.profs.itemAt(pos)
        p = None
        if it is not None:
            self.profs.setCurrentItem(it); p = self._sel_profile()
        m = QMenu(self)
        add = m.addMenu("Add profile")
        for k, text in PROFILE_KINDS.items():
            add.addAction(text, lambda kk=k: self.add_profile(kk))
        if p:
            m.addAction("Edit...", lambda: QTimer.singleShot(0, self.edit_profile))
            m.addAction("Disable" if p["enabled"] else "Enable", lambda: self.toggle_profile(p["id"]))
            m.addAction("Send test", self.test_profile)
            m.addSeparator()
            m.addAction("Delete", self.delete_profile)
        m.exec(self.profs.viewport().mapToGlobal(pos))
