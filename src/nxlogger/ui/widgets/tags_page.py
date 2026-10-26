"""
tags_page.py - the Tags document (tag management, like Genesis64 / FUXA tag lists). The
devices themselves are in the Connectivity document (connectivity.py).

  left   devices (connector icon, state dot) with their groups (folders)
  right  tag table of the selected device / group: name, group, address, type, access,
         unit, live value, quality, time of the last value, description
  bar    add tag, add tags (bulk), add group, browse (OPC UA), start / stop,
         import / export tag list (CSV), delete

Select anything to edit it in the Properties panel; double-click a tag for all its settings
(scaling, display). Tags can be multi-selected (delete, enable / disable, move to a group).

Host API (duck-typed, DaqWindow):
  host.project, host.reg_icon(target, name, token), host.recording (bool), host.color_of(did, idx)
  host.device_state(did) -> (state, text)
  host.tag_live(did, idx) -> (value text, quality text, good: bool | None, time text)
  host.select_object(ref)            ref: ("device", did) | ("group", did, path) | ("tag", did, idx) | None
  host.add_device(driver) / host.edit_device(did) / host.delete_device(did)   (device tree menu)
  host.add_tags(did, group, bulk) / host.edit_tag(did, idx) / host.delete_tags(did, [idx])
  host.add_group(did, parent) / host.delete_group(did, path) / host.move_tags(did, [idx], group)
  host.set_tags_enabled(did, [idx], on) / host.write_tag(did, idx) / host.browse_opcua(did, group)
  host.start_device(did) / host.stop_device(did) / host.import_tags(did) / host.export_tags(did)
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLineEdit, QMenu,
                               QSplitter, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.core.tags import DRIVERS, PLANNED, describe_address, driver_label
from nxlogger.ui.icons import swatch
from nxlogger.ui.theme.ui_kit import T, label, make_icon, vsep
from nxlogger.ui.widgets.connectivity import STATE_TOKEN

COLS = ["Name", "Group", "Address", "Type", "Access", "Unit", "Value", "Quality", "Updated", "Description"]
C_VALUE, C_QUAL, C_TIME = COLS.index("Value"), COLS.index("Quality"), COLS.index("Updated")


class TagsPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._sel: tuple = ()                        # ("device", did) / ("group", did, path)
        self._rows: dict[tuple[str, int], QTreeWidgetItem] = {}
        self._dev_items: dict[str, QTreeWidgetItem] = {}

        bar = QFrame(); bar.setObjectName("docBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(2)
        self.btn_tag = self._button("Add tag", "tag", lambda: self._add_tags(False), "Add one tag to the device")
        self.btn_tags = self._button("Add tags...", "list", lambda: self._add_tags(True),
                                     "Add several tags with consecutive addresses")
        self.btn_group = self._button("Group", "folder-plus", self._add_group, "Add a group (folder)")
        self.btn_browse = self._button("Browse...", "search", self._browse, "Browse the OPC UA server (OPC UA devices)")
        self.btn_start = self._button("Start", "play", self._start, "Connect this device")
        self.btn_stop = self._button("Stop", "square", self._stop, "Disconnect this device")
        self.btn_imp = self._button("", "download", self._import, "Import tags from CSV")
        self.btn_exp = self._button("", "upload", self._export, "Export the tag list to CSV")
        self.btn_del = self._button("", "trash", self._delete, "Delete the selected tags / group / device")
        for w in (self.btn_tag, self.btn_tags, self.btn_group, self.btn_browse, None,
                  self.btn_start, self.btn_stop, None, self.btn_imp, self.btn_exp, self.btn_del):
            if w is None:
                bl.addWidget(vsep())
            else:
                bl.addWidget(w)
        bl.addStretch(1)
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Filter tags")
        self.filter.setClearButtonEnabled(True); self.filter.setMaximumWidth(220)
        self.filter.textChanged.connect(lambda _: self._apply_filter())
        bl.addWidget(self.filter)

        self.dtree = QTreeWidget(); self.dtree.setObjectName("navTree")
        self.dtree.setHeaderHidden(True); self.dtree.setIndentation(14)
        self.dtree.currentItemChanged.connect(lambda cur, _p: self._device_selected(cur))
        self.dtree.itemClicked.connect(lambda it, _c: self._device_selected(it, force=True))
        self.dtree.itemDoubleClicked.connect(self._device_double)
        self.dtree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.dtree.customContextMenuRequested.connect(self._device_menu)

        self.table = QTreeWidget()
        self.table.setHeaderLabels(COLS)
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        for c, w in enumerate((160, 110, 170, 60, 52, 60, 100, 70, 80)):
            self.table.header().resizeSection(c, w)
        self.table.header().setSectionResizeMode(len(COLS) - 1, QHeaderView.ResizeMode.Stretch)
        for c in (C_VALUE,):
            self.table.headerItem().setTextAlignment(c, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.itemSelectionChanged.connect(self._tags_selected)
        self.table.itemDoubleClicked.connect(lambda it, _c: QTimer.singleShot(
            0, lambda: host.edit_tag(*it.data(0, Qt.ItemDataRole.UserRole))))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._tag_menu)
        self.hint = label("", muted=True, small=True)
        self.hint.setContentsMargins(8, 2, 8, 2)

        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        rv.addWidget(self.table, 1); rv.addWidget(self.hint)
        split = QSplitter(); split.addWidget(self.dtree); split.addWidget(right)
        split.setStretchFactor(1, 1); split.setSizes([230, 800]); split.setChildrenCollapsible(False)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(bar); v.addWidget(split, 1)

    def _button(self, text, icon, slot, tip) -> QToolButton:
        b = QToolButton(); b.setToolTip(tip)
        if text:
            b.setText(text); b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.host.reg_icon(b, icon)
        if slot:
            b.clicked.connect(slot)
        return b

    # ---------------------------------------------------------------- devices
    def refresh(self, select: tuple | None = None):
        """Model changed: rebuild the device tree and the tag table (keeps the selection)."""
        if select is not None:
            self._sel = select
        self.dtree.blockSignals(True)
        self.dtree.clear(); self._dev_items.clear()
        cur = None
        for d in self.host.project.devices:
            it = QTreeWidgetItem([d.name])
            it.setData(0, Qt.ItemDataRole.UserRole, ("device", d.id))
            it.setToolTip(0, f"{driver_label(d.driver)} · {d.topic}" + ("" if d.enabled else " · disabled"))
            if not d.enabled:
                it.setForeground(0, QBrush(QColor(T["muted"])))
            self.dtree.addTopLevelItem(it)
            self._dev_items[d.id] = it
            folders = {"": it}
            for g in d.all_groups():
                parent = folders.get(g.rsplit("/", 1)[0] if "/" in g else "", it)
                gi = QTreeWidgetItem([g.rsplit("/", 1)[-1]])
                gi.setIcon(0, make_icon("folder", T["muted"]))
                gi.setData(0, Qt.ItemDataRole.UserRole, ("group", d.id, g))
                n = sum(1 for t in d.tags if t.group == g or t.group.startswith(g + "/"))
                gi.setToolTip(0, f"{g} · {n} tag(s)")
                parent.addChild(gi)
                folders[g] = gi
                if self._sel == ("group", d.id, g):
                    cur = gi
            it.setExpanded(True)
            if self._sel[:2] == ("device", d.id) or (cur is None and self._sel[1:2] == (d.id,)):
                cur = cur or it
        if cur is None and self.dtree.topLevelItemCount():
            cur = self.dtree.topLevelItem(0)
        if cur is not None:
            self.dtree.setCurrentItem(cur)
            self._sel = tuple(cur.data(0, Qt.ItemDataRole.UserRole))
        else:
            self._sel = ()
        self.dtree.blockSignals(False)
        self.update_states(force=True)
        self._fill_tags()

    def update_states(self, force: bool = False):
        for d in self.host.project.devices:
            it = self._dev_items.get(d.id)
            if it is None:
                continue
            state, text = self.host.device_state(d.id)
            tok = STATE_TOKEN.get(state, "muted") if d.enabled else "muted"
            key = (tok, text)
            if force or it.data(1, Qt.ItemDataRole.UserRole) != key:
                it.setData(1, Qt.ItemDataRole.UserRole, key)
                it.setIcon(0, self._device_icon(d.driver, tok))
                it.setToolTip(0, f"{driver_label(d.driver)} · {d.topic}\n{state.capitalize()}"
                                 + (f": {text}" if text else ""))
        self._update_buttons()

    @staticmethod
    def _device_icon(driver: str, tok: str) -> QIcon:
        """Connector icon in the state colour (green online, amber connecting, red error)."""
        return make_icon(DRIVERS.get(driver, {}).get("icon", "server"), T[tok if tok != "muted" else "text"])

    def _device(self):
        return self.host.project.device(self._sel[1]) if len(self._sel) > 1 else None

    def _device_selected(self, it, force: bool = False):
        if it is None:
            return
        sel = tuple(it.data(0, Qt.ItemDataRole.UserRole))
        if sel != self._sel or force:
            self._sel = sel
            self._fill_tags()
            self.host.select_object(sel)

    def _device_double(self, it, _c):
        d = it.data(0, Qt.ItemDataRole.UserRole)
        if d[0] == "device":
            QTimer.singleShot(0, lambda: self.host.edit_device(d[1]))

    def _device_menu(self, pos):
        it = self.dtree.itemAt(pos)
        m = QMenu(self)
        if it is not None:
            self.dtree.setCurrentItem(it)
            d = tuple(it.data(0, Qt.ItemDataRole.UserRole))
            dev = self.host.project.device(d[1])
            if d[0] == "device":
                m.addAction("Edit device...", lambda: QTimer.singleShot(0, lambda: self.host.edit_device(d[1])))
                run = self.host.device_state(d[1])[0] != "stopped"
                m.addAction("Stop" if run else "Start", lambda: (self.host.stop_device if run
                                                                 else self.host.start_device)(d[1]))
            m.addAction("Add tag", lambda: self._add_tags(False))
            m.addAction("Add tags...", lambda: self._add_tags(True))
            m.addAction("Add group...", self._add_group)
            if dev is not None and dev.driver == "opcua":
                m.addAction("Browse server...", self._browse)
            m.addSeparator()
            if d[0] == "device":
                m.addAction("Import tags (CSV)...", self._import)
                m.addAction("Export tags (CSV)...", self._export)
                m.addSeparator()
                m.addAction("Delete device", self._delete)
            else:
                m.addAction("Delete group", self._delete)
        else:
            for k, d in DRIVERS.items():
                m.addAction(f"Add {d['label']} device...", lambda kk=k: self.host.add_device(kk))
        self._lock_menu(m)
        m.exec(self.dtree.viewport().mapToGlobal(pos))

    # ---------------------------------------------------------------- tags
    def _fill_tags(self):
        dev = self._device()
        self.table.setSortingEnabled(False)
        self.table.clear(); self._rows.clear()
        if dev is None:
            self.hint.setText("No devices - add one in Connectivity (Simulation, Modbus TCP / RTU, MQTT or OPC UA).")
            self._update_buttons()
            return
        group = self._sel[2] if self._sel[0] == "group" else None
        muted = QBrush(QColor(T["muted"]))
        for t in dev.tags:
            if group is not None and not (t.group == group or t.group.startswith(group + "/")):
                continue
            typ = "Array" if t.kind == "array" else (t.address.get("dtype", "Number")
                                                     if dev.driver.startswith("modbus") else "Number")
            it = QTreeWidgetItem([t.name, t.group, describe_address(dev.driver, "", t), typ,
                                  t.access, t.unit, "", "", "", t.description])
            it.setData(0, Qt.ItemDataRole.UserRole, (dev.id, t.index))
            it.setIcon(0, swatch(self.host.color_of(dev.id, t.index)))
            it.setTextAlignment(C_VALUE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if not t.enabled:
                for c in range(len(COLS)):
                    it.setForeground(c, muted)
            self.table.addTopLevelItem(it)
            self._rows[(dev.id, t.index)] = it
        self.table.setSortingEnabled(True)
        n = len(self._rows)
        where = f"group {group}" if group else dev.name
        self.hint.setText(f"{n} tag(s) in {where} · {driver_label(dev.driver)} · {dev.topic} · "
                          "double-click a tag for all settings, right-click for more")
        self._apply_filter()
        self.update_live()
        self._update_buttons()

    def update_live(self):
        """Value / quality / time columns (called twice a second while the page is visible)."""
        bad, ok, muted = QBrush(QColor(T["bad"])), QBrush(QColor(T["ok"])), QBrush(QColor(T["muted"]))
        for (did, idx), it in self._rows.items():
            val, qual, good, stamp = self.host.tag_live(did, idx)
            if it.text(C_VALUE) != val:
                it.setText(C_VALUE, val)
            if it.text(C_QUAL) != qual:
                it.setText(C_QUAL, qual)
                it.setForeground(C_QUAL, ok if good else (bad if good is False else muted))
                it.setForeground(C_VALUE, bad if good is False else QBrush())
            if it.text(C_TIME) != stamp:
                it.setText(C_TIME, stamp)

    def _apply_filter(self):
        text = self.filter.text().strip().lower()
        for it in self._rows.values():
            it.setHidden(bool(text) and not any(text in it.text(c).lower() for c in (0, 1, 2, 9)))

    def selected_tags(self) -> list[tuple[str, int]]:
        return [tuple(it.data(0, Qt.ItemDataRole.UserRole)) for it in self.table.selectedItems()]

    def select_tag(self, did: str, idx: int):
        if self._sel[1:2] != (did,):
            self.refresh(("device", did))
        it = self._rows.get((did, idx))
        if it is None:                                   # filtered by group: show the whole device
            self.refresh(("device", did))
            it = self._rows.get((did, idx))
        if it is not None:
            self.table.clearSelection()
            it.setSelected(True)
            self.table.scrollToItem(it)

    def _tags_selected(self):
        sel = self.selected_tags()
        if len(sel) == 1:
            self.host.select_object(("tag", *sel[0]))
        elif len(sel) > 1:
            self.host.select_object(("tags", sel[0][0], [i for _, i in sel]))
        self._update_buttons()

    def activate(self):
        """The tab came to the front: show the properties of what is selected here again."""
        if self.selected_tags():
            self._tags_selected()
        elif self._sel:
            self.host.select_object(self._sel)

    def _tag_menu(self, pos):
        it = self.table.itemAt(pos)
        if it is not None and not it.isSelected():
            self.table.clearSelection(); it.setSelected(True)
        sel = self.selected_tags()
        dev = self._device()
        m = QMenu(self)
        if sel and dev is not None:
            did, ids = sel[0][0], [i for _, i in sel]
            if len(sel) == 1:
                m.addAction("Edit tag...", lambda: QTimer.singleShot(0, lambda: self.host.edit_tag(did, ids[0])))
                tag = dev.tag(ids[0])
                w = m.addAction("Write value...", lambda: self.host.write_tag(did, ids[0]))
                w.setEnabled(tag is not None and tag.access == "RW" and self.host.device_state(did)[0] == "online")
                m.addSeparator()
            m.addAction("Enable", lambda: self.host.set_tags_enabled(did, ids, True))
            m.addAction("Disable", lambda: self.host.set_tags_enabled(did, ids, False))
            mv = m.addMenu("Move to group")
            mv.addAction("(no group)", lambda: self.host.move_tags(did, ids, ""))
            for g in dev.all_groups():
                mv.addAction(g, lambda gg=g: self.host.move_tags(did, ids, gg))
            m.addSeparator()
            if len(sel) == 1:
                m.addAction("Add alarm for this tag...", lambda: self.host.add_alarm_for((did, ids[0])))
            m.addAction(f"Delete {len(sel)} tag(s)", self._delete)
            m.addSeparator()
        m.addAction("Add tag", lambda: self._add_tags(False))
        m.addAction("Add tags...", lambda: self._add_tags(True))
        self._lock_menu(m, keep=("Write value...", "Add alarm for this tag..."))
        m.exec(self.table.viewport().mapToGlobal(pos))

    # ---------------------------------------------------------------- actions
    def _lock_menu(self, m: QMenu, keep=()):
        """While recording the tag list is locked (files have fixed columns)."""
        if self.host.recording:
            for a in m.actions():
                if a.text() not in keep and not a.text().startswith(("Start", "Stop")):
                    a.setEnabled(False)

    def _group(self) -> str:
        return self._sel[2] if self._sel and self._sel[0] == "group" else ""

    def _add_tags(self, bulk: bool):
        dev = self._device()
        if dev is not None:
            self.host.add_tags(dev.id, self._group(), bulk)

    def _add_group(self):
        dev = self._device()
        if dev is not None:
            self.host.add_group(dev.id, self._group())

    def _browse(self):
        dev = self._device()
        if dev is not None and dev.driver == "opcua":
            self.host.browse_opcua(dev.id, self._group())

    def _start(self):
        dev = self._device()
        if dev is not None:
            self.host.start_device(dev.id)

    def _stop(self):
        dev = self._device()
        if dev is not None:
            self.host.stop_device(dev.id)

    def _import(self):
        dev = self._device()
        if dev is not None:
            self.host.import_tags(dev.id)

    def _export(self):
        dev = self._device()
        if dev is not None:
            self.host.export_tags(dev.id)

    def _delete(self):
        sel = self.selected_tags()            # tags selected: delete them, else the group / device
        if sel:
            self.host.delete_tags(sel[0][0], [i for _, i in sel])
        elif self._sel and self._sel[0] == "group":
            self.host.delete_group(self._sel[1], self._sel[2])
        elif self._sel and self._sel[0] == "device":
            self.host.delete_device(self._sel[1])

    def _update_buttons(self):
        dev = self._device()
        rec = self.host.recording
        has = dev is not None
        for b in (self.btn_tag, self.btn_tags, self.btn_group, self.btn_imp):
            b.setEnabled(has and not rec)
        self.btn_tags.setEnabled(has and not rec)
        self.btn_browse.setEnabled(has and dev.driver == "opcua" and not rec)   # never hidden: the bar stays put
        self.btn_exp.setEnabled(has)
        self.btn_del.setEnabled(has and not rec)
        state = self.host.device_state(dev.id)[0] if has else "stopped"
        self.btn_start.setEnabled(has and dev.enabled and state == "stopped" and dev.driver not in PLANNED)
        self.btn_stop.setEnabled(has and state != "stopped")
