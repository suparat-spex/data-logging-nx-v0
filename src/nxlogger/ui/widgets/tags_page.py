"""
tags_page.py - the Tags document (tag management, like Genesis64 / FUXA tag lists). The
devices themselves are in the Connectivity document (connectivity.py).

Tags come in six categories (CATEGORIES, also the Project tree's Tags nodes). The page shows
one category at a time - the one chosen in the Project tree:
  Device       acquires the data: the tags of the devices. The devices come from Connectivity
               (picked here, never added or edited here)
  Virtual      calculates from it            (planned - calculated tags are Variables for now)
  Action       responds to it: logic, outputs, triggers                       (planned)
  Aggregation  summarizes it statistically                                    (planned)
  Memory       values set by the user: setpoints, constants                   (planned)
  System       read-only values of the app (core/system_tags.py; fixed, not edited)

  bar    Add ▾ (tag, tags..., group..., browse OPC UA...), Edit, Delete | Import..., Export...
         | Columns ▾ (show / hide, remembered), filter - fixed buttons, disabled when they do
         not apply (like Connectivity)
  left   the category as a band (like a Properties section), below it its list:
         Device: All tags, devices (connector icon in the state colour) > groups
         System: All tags, Clock, App, Recording, Alarms, Devices > one group per device
  right  tag table: name, group, address, type, access, unit, live value, quality, time of
         the last value, description

Select anything to edit it in the Properties panel; double-click a tag for all its settings
(scaling, display). Tags can be multi-selected (delete, enable / disable, move to a group).

Host API (duck-typed, DaqWindow):
  host.project, host.reg_icon(target, name, token), host.recording (bool), host.color_of(did, idx)
  host.device_state(did) -> (state, text)
  host.tag_live(did, idx) -> (value text, quality text, good: bool | None, time text)
  host.select_object(ref)            ref: ("device", did) | ("group", did, path) | ("tag", did, idx)
                                          | ("sys", "sys:<id>") | None
  host.sys_tags() -> [system_tags.SysTag] / host.sys_tag("sys:<id>")
                                     (tag_live / color_of take the key ("sys:<id>", 0) too)
  host.add_tags(did, group, bulk) / host.edit_tag(did, idx) / host.delete_tags(did, [idx])
  host.edit_group(did, path) / host.add_group(did, parent) / host.delete_group(did, path)   (pop-ups)
  host.move_tags(did, [idx], group) / host.set_tags_enabled(did, [idx], on) / host.write_tag(did, idx)
  host.browse_opcua(did, group) / host.add_alarm_for(key)
  host.import_tags(did) / host.export_tags(did | None, [(did, idx)])    (pop-ups like Connectivity's)
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
                               QSplitter, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.core import system_tags as ss
from nxlogger.core.tags import DRIVERS, describe_address, driver_label
from nxlogger.ui.icons import swatch
from nxlogger.ui.theme.app_style import blend
from nxlogger.ui.theme.ui_kit import T, label, make_icon, vsep
from nxlogger.ui.widgets.connectivity import STATE_TOKEN

COLS = ["Name", "Device", "Group", "Address", "Type", "Access", "Unit", "Value", "Quality", "Updated", "Description"]
C_VALUE, C_QUAL, C_TIME = COLS.index("Value"), COLS.index("Quality"), COLS.index("Updated")
C_FILTER = [COLS.index(c) for c in ("Name", "Device", "Group", "Address", "Description")]
HIDDEN_KEY = "tags/hidden_columns"                # QSettings: column names the user hid
CATEGORIES = [   # (key, name, icon, what it is, ready)
    ("device", "Device", "plug", "Acquires the data: the tags of the devices", True),
    ("virtual", "Virtual", "function-square", "Calculates from it - planned (calculated tags are in Variables)", False),
    ("action", "Action", "power", "Responds to it: logic, outputs, triggers - planned", False),
    ("aggregation", "Aggregation", "sigma", "Summarizes it statistically - planned", False),
    ("memory", "Memory", "database", "Values set by the user: setpoints, constants - planned", False),
    ("system", "System", "cpu", "Read-only values of the app: clock, recording, alarms, device status", True),
]
CAT = {c[0]: c for c in CATEGORIES}
SYS_GROUPS = ["Clock", "App", "Recording", "Alarms"]     # then "Devices" > one group per device


class _Band(QFrame):
    """The category heading: the same full-width band as a Properties section (neutral theme
    colours read from T at paint time, so a theme switch needs no restyle)."""

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


class TagsPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._cat = "device"                         # the category shown (Project tree > Tags > ...)
        self._sel: tuple = ("all",)                  # in it: ("all",) / ("device", did) / ("group", did, path)
        #                                              / ("sysgroup", name) / ("sysdev", did)
        self._rows: dict[tuple[str, int], QTreeWidgetItem] = {}
        self._dev_items: dict[str, QTreeWidgetItem] = {}

        bar = QFrame(); bar.setObjectName("docBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(2)
        self.btn_add = self._button("Add", "plus", None, "Add tags or a group to the selected device")
        m = QMenu(self.btn_add)
        self.act_tag = m.addAction("Tag...", lambda: self._add_tags(False))
        self.act_tags = m.addAction("Tags (consecutive addresses)...", lambda: self._add_tags(True))
        self.act_group = m.addAction("Group...", self._add_group)
        m.addSeparator()
        self.act_browse = m.addAction("Browse OPC UA server...", self._browse)
        self.btn_add.setMenu(m); self.btn_add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_edit = self._button("Edit", "pencil", self._edit, "Edit the selected tag or group (double-click a row)")
        self.btn_del = self._button("", "trash", self._delete, "Delete the selected tags or group (Del)")
        self.btn_imp = self._button("Import...", "download", self._import,
                                    "Import tags into the selected device: CSV tag list or device JSON (checked first)")
        self.btn_exp = self._button("Export...", "upload", self._export, "Export tags: tick them, CSV or JSON")
        for w in (self.btn_add, self.btn_edit, self.btn_del, None, self.btn_imp, self.btn_exp):
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
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Filter tags")
        self.filter.setClearButtonEnabled(True); self.filter.setMaximumWidth(220)
        self.filter.setMinimumWidth(120)
        self.filter.textChanged.connect(lambda _: self._apply_filter())
        bl.addWidget(self.filter)

        self.band = _Band()
        self.dtree = QTreeWidget(); self.dtree.setObjectName("navTree")
        self.dtree.setHeaderHidden(True); self.dtree.setIndentation(14)
        self.dtree.setProperty("plainSel", True)        # neutral selection, like the Properties bands
        self.dtree.currentItemChanged.connect(lambda cur, _p: self._list_selected(cur))
        self.dtree.itemClicked.connect(lambda it, _c: self._list_selected(it, force=True))
        self.dtree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.dtree.customContextMenuRequested.connect(self._list_menu)
        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(0)
        lv.addWidget(self.band); lv.addWidget(self.dtree, 1)

        self.table = QTreeWidget()
        self.table.setHeaderLabels(COLS)
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        for c, w in enumerate((160, 110, 100, 170, 60, 52, 60, 100, 70, 80)):
            self.table.header().resizeSection(c, w)
        self.table.header().setSectionResizeMode(len(COLS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.headerItem().setTextAlignment(C_VALUE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.header().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.header().customContextMenuRequested.connect(self._header_menu)
        hidden = QSettings().value(HIDDEN_KEY, [])
        hidden = hidden if isinstance(hidden, list) else [hidden]
        for c in range(1, len(COLS)):                 # Name always shows
            self.table.setColumnHidden(c, COLS[c] in hidden)
        self.table.itemSelectionChanged.connect(self._tags_selected)
        self.table.itemDoubleClicked.connect(self._tag_double)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._tag_menu)
        for key, slot in ((QKeySequence.StandardKey.Delete, self._delete), (Qt.Key.Key_Return, self._edit),
                          (Qt.Key.Key_Enter, self._edit)):
            sc = QShortcut(QKeySequence(key), self.table)
            sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(slot)
        self.hint = label("", muted=True, small=True)
        self.hint.setContentsMargins(8, 2, 8, 2)

        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        rv.addWidget(self.table, 1); rv.addWidget(self.hint)
        split = QSplitter(); split.addWidget(left); split.addWidget(right)
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

    # ---------------------------------------------------------------- category list
    def refresh(self, select: tuple | None = None):
        """Model changed: rebuild the list and the tag table (keeps the selection).
        select: ("cat", key) shows a category (all its tags); ("device", did) / ("group", did, path)
        show the Device category with that selected; () / None keeps what is shown."""
        if select:
            if select[0] == "cat":
                self._cat, self._sel = select[1], ("all",)
            else:
                self._cat = "system" if select[0] in ("sysgroup", "sysdev") else "device"
                self._sel = tuple(select)
        key, name, icon, tip, ready = CAT[self._cat]
        self.band.set(name + ("" if ready else "  (planned)"), icon, tip)
        self.dtree.blockSignals(True)
        self.dtree.clear(); self._dev_items.clear()
        items: dict[tuple, QTreeWidgetItem] = {}

        def add(parent, text, data, icon_=None, tip_=""):
            it = QTreeWidgetItem([text])
            it.setData(0, Qt.ItemDataRole.UserRole, data)
            if icon_ is not None:
                it.setIcon(0, icon_)
            if tip_:
                it.setToolTip(0, tip_)
            (parent.addChild if parent is not None else self.dtree.addTopLevelItem)(it)
            items[data] = it
            return it
        muted = QBrush(QColor(T["muted"]))
        if self._cat == "device":
            add(None, f"All tags  ({sum(len(d.tags) for d in self.host.project.devices)})", ("all",),
                make_icon("list", T["text"]), "Every tag of every device")
            for d in self.host.project.devices:
                it = add(None, f"{d.name}  ({len(d.tags)})", ("device", d.id), None,
                         f"{driver_label(d.driver)} · {d.topic}" + ("" if d.enabled else " · disabled"))
                if not d.enabled:
                    it.setForeground(0, muted)
                self._dev_items[d.id] = it
                folders = {"": it}
                for g in d.all_groups():
                    parent = folders.get(g.rsplit("/", 1)[0] if "/" in g else "", it)
                    n = sum(1 for t in d.tags if t.group == g or t.group.startswith(g + "/"))
                    folders[g] = add(parent, g.rsplit("/", 1)[-1], ("group", d.id, g),
                                     make_icon("folder", T["muted"]), f"{g} · {n} tag(s)")
                it.setExpanded(True)
        elif self._cat == "system":
            tags = self.host.sys_tags()
            add(None, f"All tags  ({len(tags)})", ("all",), make_icon("list", T["text"]), "Every system tag")
            for g in SYS_GROUPS:
                add(None, f"{g}  ({sum(1 for t in tags if t.group == g)})", ("sysgroup", g),
                    make_icon("folder", T["muted"]))
            if self.host.project.devices:
                devs = add(None, "Devices", ("sysgroup", "Devices"), make_icon("folder", T["muted"]),
                           "Status of every device: connected, state, quality, last value, errors")
                for d in self.host.project.devices:
                    add(devs, d.name, ("sysdev", d.id), make_icon(DRIVERS.get(d.driver, {}).get("icon", "server"),
                                                                  T["muted"]))
                devs.setExpanded(True)
        cur = items.get(self._sel) or items.get(("all",))
        if cur is None and self._sel[:1] == ("group",):    # group gone: its device
            cur = items.get(("device", self._sel[1]))
        if cur is not None:
            self.dtree.setCurrentItem(cur)
            self._sel = tuple(cur.data(0, Qt.ItemDataRole.UserRole))
        else:
            self._sel = ("all",)
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

    def current_device_id(self) -> str | None:
        """The device selected in the list (Device category), else None."""
        return self._sel[1] if self._cat == "device" and self._sel[:1] in (("device",), ("group",)) else None

    def current_group(self) -> str:
        return self._sel[2] if self._cat == "device" and self._sel[:1] == ("group",) else ""

    def _device(self):
        did = self.current_device_id()
        return self.host.project.device(did) if did else None

    def _list_selected(self, it, force: bool = False):
        if it is None:
            return
        sel = tuple(it.data(0, Qt.ItemDataRole.UserRole))
        if sel != self._sel or force:
            self._sel = sel
            self._fill_tags()
            self.host.select_object(sel if sel[0] in ("device", "group") else None)

    def _list_menu(self, pos):
        """Tags only: devices are added / edited in Connectivity."""
        it = self.dtree.itemAt(pos)
        if it is None or self._cat != "device":
            return
        self.dtree.setCurrentItem(it)
        d = tuple(it.data(0, Qt.ItemDataRole.UserRole))
        if d[0] == "all":
            return
        dev = self.host.project.device(d[1])
        m = QMenu(self)
        m.addAction("Add tag...", lambda: self._add_tags(False))
        m.addAction("Add tags...", lambda: self._add_tags(True))
        m.addAction("Add group...", self._add_group)
        if dev is not None and dev.driver == "opcua":
            m.addAction("Browse server...", self._browse)
        m.addSeparator()
        if d[0] == "device":
            m.addAction("Import tags...", self._import)
            m.addAction("Export tags...", self._export)
        else:
            m.addAction("Edit group...", lambda: QTimer.singleShot(0, lambda: self.host.edit_group(*d[1:])))
            m.addAction("Delete group", self._delete)
        self._lock_menu(m, keep=("Export tags...",))
        m.exec(self.dtree.viewport().mapToGlobal(pos))

    # ---------------------------------------------------------------- tags
    def _add_row(self, key, cols, color, muted=None):
        it = QTreeWidgetItem(cols)
        it.setData(0, Qt.ItemDataRole.UserRole, key)
        it.setIcon(0, swatch(color))
        it.setTextAlignment(C_VALUE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if muted is not None:
            for c in range(len(COLS)):
                it.setForeground(c, muted)
        self.table.addTopLevelItem(it)
        self._rows[key] = it

    def _fill_tags(self):
        self.table.setSortingEnabled(False)
        self.table.clear(); self._rows.clear()
        if self._cat == "system":
            self._fill_system()
        elif self._cat == "device":
            self._fill_device()
        else:
            self.hint.setText(f"{CAT[self._cat][1]} tags: {CAT[self._cat][3]}.")
        self.table.setSortingEnabled(True)
        self._apply_filter()
        self.update_live()
        self._update_buttons()

    def _fill_device(self):
        devices = self.host.project.devices
        if not devices:
            self.hint.setText("No devices - add one in Connectivity, then select it here to add its tags.")
            return
        dev = self._device()
        group = self.current_group() or None
        muted = QBrush(QColor(T["muted"]))
        for d in [dev] if dev is not None else devices:
            for t in d.tags:
                if group is not None and not (t.group == group or t.group.startswith(group + "/")):
                    continue
                typ = "Array" if t.kind == "array" else (t.address.get("dtype", "Number")
                                                         if d.driver.startswith("modbus") else "Number")
                self._add_row((d.id, t.index), [t.name, d.name, t.group, describe_address(d.driver, "", t), typ,
                                                t.access, t.unit, "", "", "", t.description],
                              self.host.color_of(d.id, t.index), None if t.enabled and d.enabled else muted)
        n = len(self._rows)
        if dev is None:
            self.hint.setText(f"{n} tag(s) in {len(devices)} device(s) · select a device to add tags · "
                              "double-click a tag for all settings")
        else:
            where = f"group {group}" if group else dev.name
            self.hint.setText(f"{n} tag(s) in {where} · {driver_label(dev.driver)} · {dev.topic} · "
                              "double-click a tag for all settings, right-click for more")

    def _fill_system(self):
        sel = self._sel
        devs = {d.id: d.name for d in self.host.project.devices}
        for t in self.host.sys_tags():
            if sel[0] == "sysgroup" and not (t.group == sel[1] or (sel[1] == "Devices" and t.group not in SYS_GROUPS)):
                continue
            if sel[0] == "sysdev" and not t.id.startswith(f"dev.{sel[1]}."):
                continue
            own = devs.get(t.id.split(".")[1]) if t.id.startswith("dev.") else None    # a device's status tag
            self._add_row(t.key, [t.name, own or "System", "Status" if own else t.group,
                                  f"System_{t.name}" if t.streamed else "", ss.KINDS[t.kind],
                                  "R", t.unit, "", "", "", t.description], self.host.color_of(*t.key))
        where = (sel[1] if sel[0] == "sysgroup" else devs.get(sel[1], "") if sel[0] == "sysdev" else "System")
        self.hint.setText(f"{len(self._rows)} system tag(s) in {where} · read-only, updated twice a second · "
                          "numbers work in views, alarms and formulas (System_<name>)")

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
            it.setHidden(bool(text) and not any(text in it.text(c).lower() for c in C_FILTER))

    def selected_tags(self) -> list[tuple[str, int]]:
        """Selected device tags (System Tags are not edited: see selected_system)."""
        return [k for k in self._selected_keys() if not ss.is_sys(k[0])]

    def selected_system(self) -> list[tuple[str, int]]:
        return [k for k in self._selected_keys() if ss.is_sys(k[0])]

    def _selected_keys(self) -> list[tuple[str, int]]:
        return [tuple(it.data(0, Qt.ItemDataRole.UserRole)) for it in self.table.selectedItems()]

    def _tag_double(self, it, _c):
        key = tuple(it.data(0, Qt.ItemDataRole.UserRole))
        if not ss.is_sys(key[0]):                        # System Tags are read-only: Properties only
            QTimer.singleShot(0, lambda: self.host.edit_tag(*key))

    def select_tag(self, did: str, idx: int):
        if ss.is_sys(did):
            if self._cat != "system":
                self.refresh(("cat", "system"))
        elif self._cat != "device" or self._sel[1:2] != (did,):
            self.refresh(("device", did))
        it = self._rows.get((did, idx))
        if it is None:                                   # filtered by a group: show the whole device / all
            self.refresh(("cat", "system") if ss.is_sys(did) else ("device", did))
            it = self._rows.get((did, idx))
        if it is not None:
            self.table.clearSelection()
            it.setSelected(True)
            self.table.scrollToItem(it)

    def _tags_selected(self):
        sel = self.selected_tags()
        if not sel and self.selected_system():
            self.host.select_object(("sys", self.selected_system()[0][0]))
        elif len(sel) == 1:
            self.host.select_object(("tag", *sel[0]))
        elif len(sel) > 1:
            self.host.select_object(("tags", sel[0][0], [i for d, i in sel if d == sel[0][0]]))
        self._update_buttons()

    def activate(self):
        """The tab came to the front: show the properties of what is selected here again."""
        if self._selected_keys():
            self._tags_selected()
        else:
            self.host.select_object(self._sel if self._sel[0] in ("device", "group") else None)

    def _tag_menu(self, pos):
        it = self.table.itemAt(pos)
        if it is not None and not it.isSelected():
            self.table.clearSelection(); it.setSelected(True)
        sel = self.selected_tags()
        dev = self.host.project.device(sel[0][0]) if sel else None
        m = QMenu(self)
        if self.selected_system():
            key = self.selected_system()[0]
            t = self.host.sys_tag(key[0])
            a = m.addAction("Add alarm for this tag...", lambda: self.host.add_alarm_for(key))
            a.setEnabled(t is not None and t.streamed)
            m.exec(self.table.viewport().mapToGlobal(pos))
            return
        if sel and dev is not None:
            did = sel[0][0]
            ids = [i for d, i in sel if d == did]           # one device at a time (All tags lists every device)
            if len(ids) == 1:
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
            if len(ids) == 1:
                m.addAction("Add alarm for this tag...", lambda: self.host.add_alarm_for((did, ids[0])))
            m.addAction(f"Delete {len(ids)} tag(s)", self._delete)
            m.addSeparator()
        if self._device() is not None:
            m.addAction("Add tag...", lambda: self._add_tags(False))
            m.addAction("Add tags...", lambda: self._add_tags(True))
        if not m.actions():
            return
        self._lock_menu(m, keep=("Write value...", "Add alarm for this tag..."))
        m.exec(self.table.viewport().mapToGlobal(pos))

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
    def _lock_menu(self, m: QMenu, keep=()):
        """While recording the tag list is locked (files have fixed columns)."""
        if self.host.recording:
            for a in m.actions():
                if a.text() not in keep:
                    a.setEnabled(False)

    def _add_tags(self, bulk: bool):
        dev = self._device()
        if dev is not None:
            self.host.add_tags(dev.id, self.current_group(), bulk)

    def _add_group(self):
        dev = self._device()
        if dev is not None:
            self.host.add_group(dev.id, self.current_group())

    def _browse(self):
        dev = self._device()
        if dev is not None and dev.driver == "opcua":
            self.host.browse_opcua(dev.id, self.current_group())

    def _edit(self):
        sel = self.selected_tags()
        if sel:
            QTimer.singleShot(0, lambda: self.host.edit_tag(*sel[0]))
        elif not self.selected_system() and self._sel[0] == "group":
            g = self._sel[1:]
            QTimer.singleShot(0, lambda: self.host.edit_group(*g))

    def _import(self):
        dev = self._device()
        if dev is not None:
            self.host.import_tags(dev.id)

    def _export(self):
        """One device selected: its tags; All tags: every device. Selected rows start ticked."""
        if self._cat == "device":
            dev = self._device()
            self.host.export_tags(dev.id if dev else None, self.selected_tags())

    def _delete(self):
        if self.host.recording:
            return
        sel = self.selected_tags()            # tags selected: delete them, else the selected group
        if sel:
            self.host.delete_tags(sel[0][0], [i for d, i in sel if d == sel[0][0]])
        elif not self.selected_system() and self._sel[0] == "group":
            self.host.delete_group(self._sel[1], self._sel[2])

    def _update_buttons(self):
        """Fixed buttons: disabled, never hidden, when they do not apply."""
        dev = self._device()
        rec = self.host.recording
        has = dev is not None
        tags = bool(self.selected_tags())
        group = self._cat == "device" and self._sel[0] == "group"
        self.btn_add.setEnabled(has and not rec)
        self.act_browse.setEnabled(has and dev.driver == "opcua")
        self.btn_edit.setEnabled(tags or group)
        self.btn_del.setEnabled((tags or group) and not rec)
        self.btn_imp.setEnabled(has and not rec)
        self.btn_exp.setEnabled(self._cat == "device" and any(d.tags for d in self.host.project.devices))
