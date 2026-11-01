"""
tags_page.py - the Tags document (tag management, like Genesis64 / FUXA tag lists): what each tag
is. How tags are used together (areas, equipment, KPI sets) is the Assets document (assets_page.py);
the devices themselves are in Connectivity (connectivity.py).

The categories (CATEGORIES, also the Project tree's Tags nodes); the page shows one at a time:
  All tags     every tag of every category in one table (filter by category on the left)
  Device       acquires the data: the tags of the devices. The devices come from Connectivity
               (picked here, never added or edited here)
  Virtual      derives from it: constants, formulas, conversions, logic, selectors, text maps
               (core/virtual_tags.py; added / edited in a pop-up like device tags)
  Action       responds to it: logic, outputs, triggers                       (planned)
  Aggregation  remembers it over time: statistic, totalizer, counter, runtime, min / max hold,
               change - reset by a period (core/aggregation.py; pop-up like virtual tags)
  Memory       values set by the user: setpoints, constants                   (planned)
  System       read-only values of the app (core/system_tags.py; fixed, not edited)

  bar    Add ▾ (per category), Edit, Delete | Import..., Export... | Columns ▾ (remembered),
         filter - fixed buttons, disabled when they do not apply (like Connectivity)
  left   the category as a band (like a Properties section), below it its list:
         All tags: All, Device, Virtual, Aggregation, System
         Device: All tags, devices (connector icon in the state colour)
         Virtual / Aggregation: All tags, one row per type
         System: All tags, Clock, App, Recording, Alarms, Devices > one row per device
  right  tag table: name, source, assets, address, type, access, unit, live value, quality,
         time of the last value, description - search (filter) and sort by any column

Select a row: its properties (read-only); double-click: its pop-up. Right-click rows: the
category's actions plus Add to asset ▸ and New view from selection (any category).

Host API (duck-typed, DaqWindow):
  host.project, host.reg_icon(target, name, token), host.recording (bool), host.color_of(did, idx)
  host.device_state(did) -> (state, text)
  host.tag_live(did, idx) -> (value text, quality text, good: bool | None, time text)
  host.select_object(ref)            ref: ("device", did) | ("tag", did, idx) | ("tags", did, [idx])
                                          | ("sys", "sys:<id>") | ("vt", id) | ("agg", id) | None
  host.sys_tags() -> [system_tags.SysTag] / host.sys_tag("sys:<id>")
  host.add_tags(did, bulk) / host.edit_tag(did, idx) / host.delete_tags(did, [idx])
  host.set_tags_enabled(did, [idx], on) / host.write_tag(did, idx) / host.browse_opcua(did)
  host.add_alarm_for(key) / host.import_tags(did) / host.export_tags(did | None, [(did, idx)])
  host.project.virtual_tags / host.vt_engine / host.vt_describe(v) / host.virtual_tag(vid)
  host.add_virtual(kind) / host.edit_virtual(vid) / host.duplicate_virtual(vid)
  host.delete_virtuals([vid]) / host.set_virtuals_enabled([vid], on)      (pop-ups; key ("vt:<id>", 0))
  host.project.aggregations / host.agg_engine / host.agg_describe(a) / host.aggregation(aid)
  host.add_aggregation(kind) / host.edit_aggregation(aid) / host.duplicate_aggregation(aid)
  host.delete_aggregations([aid]) / host.set_aggregations_enabled([aid], on) / host.reset_aggregation(aid)
  host.assets_of(key) -> [path] / host.add_to_asset([key], asset id | None = a new asset)
  host.new_view_from([key])
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLineEdit, QMenu,
                               QSplitter, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.core import aggregation as ag
from nxlogger.core import assets as asm
from nxlogger.core import system_tags as ss
from nxlogger.core import virtual_tags as vt
from nxlogger.core.tags import DRIVERS, describe_address, driver_label
from nxlogger.ui.icons import swatch
from nxlogger.ui.theme.ui_kit import T, label, make_icon, vsep
from nxlogger.ui.widgets.connectivity import STATE_TOKEN, Band

COLS = ["Name", "Source", "Assets", "Address", "Type", "Access", "Unit", "Value", "Quality", "Updated",
        "Description"]
C_ASSETS, C_VALUE, C_QUAL, C_TIME = (COLS.index(c) for c in ("Assets", "Value", "Quality", "Updated"))
C_FILTER = [COLS.index(c) for c in ("Name", "Source", "Assets", "Address", "Description")]
HIDDEN_KEY = "tags/hidden_columns"                # QSettings: column names the user hid
CATEGORIES = [   # (key, name, icon, what it is, ready)
    ("all", "All tags", "list", "Every tag of every category in one table", True),
    ("device", "Device", "plug", "Acquires the data: the tags of the devices", True),
    ("virtual", "Virtual", "function-square",
     "Derives from it: constants, formulas, conversions, logic, selectors, text maps", True),
    ("action", "Action", "power", "Responds to it: logic, outputs, triggers - planned", False),
    ("aggregation", "Aggregation", "sigma",
     "Remembers it over time: statistics, totals, counters, run hours, min / max, change", True),
    ("memory", "Memory", "database", "Values set by the user: setpoints, constants - planned", False),
    ("system", "System", "cpu", "Read-only values of the app: clock, recording, alarms, device status", True),
]
CAT = {c[0]: c for c in CATEGORIES}
SYS_GROUPS = ["Clock", "App", "Recording", "Alarms"]     # then "Devices" > one row per device
ALL_PARTS = [("device", "Device", "plug"), ("virtual", "Virtual", "function-square"),
             ("aggregation", "Aggregation", "sigma"), ("system", "System", "cpu")]


class TagsPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._cat = "device"                         # the category shown (Project tree > Tags > ...)
        self._sel: tuple = ("all",)                  # in it: ("all",) / ("device", did) / ("vtype", k)
        #                                              / ("atype", k) / ("part", cat) / ("sysgroup", name) / ("sysdev", did)
        self._rows: dict[tuple[str, int], QTreeWidgetItem] = {}
        self._dev_items: dict[str, QTreeWidgetItem] = {}

        bar = QFrame(); bar.setObjectName("docBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(2)
        self.btn_add = self._button("Add", "plus", None, "Add tags to the selected device")
        m = QMenu(self.btn_add)
        self.act_tag = m.addAction("Tag...", lambda: self._add_tags(False))
        self.act_tags = m.addAction("Tags (consecutive addresses)...", lambda: self._add_tags(True))
        m.addSeparator()
        self.act_browse = m.addAction("Browse OPC UA server...", self._browse)
        self.dev_menu = m
        self.vt_menu = QMenu(self.btn_add)             # Virtual: one item per type
        for k, (text, _ic, _what) in vt.TYPES.items():
            self.vt_menu.addAction(text + "...", lambda kk=k: self.host.add_virtual(kk))
        self.agg_menu = QMenu(self.btn_add)            # Aggregation: one item per type
        for k, (text, _ic, _what) in ag.TYPES.items():
            self.agg_menu.addAction(text + "...", lambda kk=k: self.host.add_aggregation(kk))
        self.btn_add.setMenu(m); self.btn_add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_edit = self._button("Edit", "pencil", self._edit, "Edit the selected tag (double-click a row)")
        self.btn_del = self._button("", "trash", self._delete, "Delete the selected tags (Del)")
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
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Search tags")
        self.filter.setClearButtonEnabled(True); self.filter.setMaximumWidth(220)
        self.filter.setMinimumWidth(120)
        self.filter.textChanged.connect(lambda _: self._apply_filter())
        bl.addWidget(self.filter)

        self.band = Band()
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
        for c, w in enumerate((160, 110, 120, 170, 60, 52, 60, 100, 70, 80)):
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
        select: ("cat", key) shows a category (all its tags); ("device", did) the Device category with
        that device; () / None keeps what is shown."""
        if select:
            if select[0] == "cat":
                self._cat, self._sel = select[1], ("all",)
            else:
                self._cat = ("system" if select[0] in ("sysgroup", "sysdev") else
                             "virtual" if select[0] == "vtype" else
                             "aggregation" if select[0] == "atype" else
                             "all" if select[0] == "part" else "device")
                self._sel = tuple(select)
        if self._cat not in CAT:                     # an older selection (Groups): All tags
            self._cat, self._sel = "all", ("all",)
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
        p = self.host.project
        if self._cat == "all":
            counts = {"device": sum(len(d.tags) for d in p.devices), "virtual": len(p.virtual_tags),
                      "aggregation": len(p.aggregations), "system": len(self.host.sys_tags())}
            add(None, f"All tags  ({sum(counts.values())})", ("all",), make_icon("list", T["text"]),
                "Every tag of every category")
            for part, text, ic in ALL_PARTS:
                it = add(None, f"{text}  ({counts[part]})", ("part", part), make_icon(ic, T["text"]))
                if not counts[part]:
                    it.setForeground(0, muted)
        elif self._cat == "device":
            add(None, f"All tags  ({sum(len(d.tags) for d in p.devices)})", ("all",),
                make_icon("list", T["text"]), "Every tag of every device")
            for d in p.devices:
                it = add(None, f"{d.name}  ({len(d.tags)})", ("device", d.id), None,
                         f"{driver_label(d.driver)} · {d.topic}" + ("" if d.enabled else " · disabled"))
                if not d.enabled:
                    it.setForeground(0, muted)
                self._dev_items[d.id] = it
        elif self._cat == "system":
            tags = self.host.sys_tags()
            add(None, f"All tags  ({len(tags)})", ("all",), make_icon("list", T["text"]), "Every system tag")
            for g in SYS_GROUPS:
                add(None, f"{g}  ({sum(1 for t in tags if t.group == g)})", ("sysgroup", g),
                    make_icon("folder", T["muted"]))
            if p.devices:
                devs = add(None, "Devices", ("sysgroup", "Devices"), make_icon("folder", T["muted"]),
                           "Status of every device: connected, state, quality, last value, errors")
                for d in p.devices:
                    add(devs, d.name, ("sysdev", d.id), make_icon(DRIVERS.get(d.driver, {}).get("icon", "server"),
                                                                  T["muted"]))
                devs.setExpanded(True)
        elif self._cat in ("virtual", "aggregation"):
            virt = self._cat == "virtual"
            xs, types = (p.virtual_tags, vt.TYPES) if virt else (p.aggregations, ag.TYPES)
            errors = self.host.vt_engine.errors if virt else self.host.agg_engine.errors
            add(None, f"All tags  ({len(xs)})", ("all",), make_icon("list", T["text"]),
                f"Every {'virtual' if virt else 'aggregation'} tag")
            for k, (text, ic, what) in types.items():
                n = sum(1 for x in xs if x["kind"] == k)
                it = add(None, f"{text}  ({n})", ("vtype" if virt else "atype", k),
                         make_icon(ic, T["text" if n else "muted"]), what)
                if not n:
                    it.setForeground(0, muted)
                if any(x["kind"] == k and x["id"] in errors for x in xs):
                    it.setForeground(0, QBrush(QColor(T["bad"])))      # one of them has an error
        cur = items.get(self._sel) or items.get(("all",))
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
        return self._sel[1] if self._cat == "device" and self._sel[:1] == ("device",) else None

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
            self.host.select_object(sel if sel[0] == "device" else None)

    def _list_menu(self, pos):
        """Device rows only: their tags (devices are added / edited in Connectivity)."""
        it = self.dtree.itemAt(pos)
        if it is None or self._cat != "device":
            return
        self.dtree.setCurrentItem(it)
        d = tuple(it.data(0, Qt.ItemDataRole.UserRole))
        if d[0] != "device":
            return
        dev = self.host.project.device(d[1])
        m = QMenu(self)
        m.addAction("Add tag...", lambda: self._add_tags(False))
        m.addAction("Add tags...", lambda: self._add_tags(True))
        if dev is not None and dev.driver == "opcua":
            m.addAction("Browse server...", self._browse)
        m.addSeparator()
        m.addAction("Import tags...", self._import)
        m.addAction("Export tags...", self._export)
        self._lock_menu(m, keep=("Export tags...",))
        m.exec(self.dtree.viewport().mapToGlobal(pos))

    # ---------------------------------------------------------------- rows
    def _assets_text(self, key) -> str:
        return ", ".join(self.host.assets_of(key))

    def _device_rows(self, devices):
        muted = QBrush(QColor(T["muted"]))
        for d in devices:
            for t in d.tags:
                typ = "Array" if t.kind == "array" else (t.address.get("dtype", "Number")
                                                         if d.driver.startswith("modbus") else "Number")
                key = (d.id, t.index)
                self._add_row(key, [t.name, d.name, self._assets_text(key), describe_address(d.driver, "", t),
                                    typ, t.access, t.unit, "", "", "", t.description],
                              self.host.color_of(*key), None if t.enabled and d.enabled else muted)

    def _virtual_rows(self, kind=None):
        muted = QBrush(QColor(T["muted"]))
        for v in self.host.project.virtual_tags:
            if kind and v["kind"] != kind:
                continue
            typ = vt.TYPES[v["kind"]][0] + ("" if v.get("dtype") == "number" else f" · {vt.DTYPES[v['dtype']]}")
            key = vt.vt_key(v["id"])
            self._add_row(key, [v["name"], "Virtual", self._assets_text(key), self.host.vt_describe(v), typ, "R",
                                v.get("unit", ""), "", "", "", v.get("description", "")],
                          self.host.color_of(*key), None if v["enabled"] else muted)
            err = self.host.vt_engine.errors.get(v["id"])
            if err:
                self._rows[key].setToolTip(0, err)

    def _aggregation_rows(self, kind=None):
        muted = QBrush(QColor(T["muted"]))
        for a in self.host.project.aggregations:
            if kind and a["kind"] != kind:
                continue
            key = ag.agg_key(a["id"])
            period = ag.describe_period(a)
            self._add_row(key, [a["name"], "Aggregation", self._assets_text(key),
                                self.host.agg_describe(a) + (f" · {period}" if period != "-" else ""),
                                ag.TYPES[a["kind"]][0], "R", a.get("unit", ""), "", "", "", a.get("description", "")],
                          self.host.color_of(*key), None if a["enabled"] else muted)
            err = self.host.agg_engine.errors.get(a["id"])
            if err:
                self._rows[key].setToolTip(0, err)

    def _system_rows(self, sel=("all",)):
        devs = {d.id: d.name for d in self.host.project.devices}
        for t in self.host.sys_tags():
            if sel[0] == "sysgroup" and not (t.group == sel[1] or (sel[1] == "Devices" and t.group not in SYS_GROUPS)):
                continue
            if sel[0] == "sysdev" and not t.id.startswith(f"dev.{sel[1]}."):
                continue
            own = devs.get(t.id.split(".")[1]) if t.id.startswith("dev.") else None    # a device's status tag
            self._add_row(t.key, [t.name, f"System · {own}" if own else f"System · {t.group}",
                                  self._assets_text(t.key), f"System_{t.name}" if t.streamed else "",
                                  ss.KINDS[t.kind], "R", t.unit, "", "", "", t.description],
                          self.host.color_of(*t.key))

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
        p, sel = self.host.project, self._sel
        if self._cat == "all":
            part = sel[1] if sel[0] == "part" else None
            if part in (None, "device"):
                self._device_rows(p.devices)
            if part in (None, "virtual"):
                self._virtual_rows()
            if part in (None, "aggregation"):
                self._aggregation_rows()
            if part in (None, "system"):
                self._system_rows()
            self.hint.setText(f"{len(self._rows)} tag(s)" + (f" · {CAT[part][1]}" if part else " · every category")
                              + " · search and sort to find one · right-click: Add to asset, New view from selection")
        elif self._cat == "device":
            dev = self._device()
            if not p.devices:
                self.hint.setText("No devices - add one in Connectivity, then select it here to add its tags.")
            else:
                self._device_rows([dev] if dev is not None else p.devices)
                if dev is None:
                    self.hint.setText(f"{len(self._rows)} tag(s) in {len(p.devices)} device(s) · select a device "
                                      "to add tags · double-click a tag for all settings")
                else:
                    self.hint.setText(f"{len(self._rows)} tag(s) in {dev.name} · {driver_label(dev.driver)} · "
                                      f"{dev.topic} · double-click a tag for all settings, right-click for more")
        elif self._cat == "virtual":
            self._virtual_rows(sel[1] if sel[0] == "vtype" else None)
            errs = sum(1 for v in p.virtual_tags if v["id"] in self.host.vt_engine.errors)
            self.hint.setText("No virtual tags - Add > Constant, Formula, Conversion, Logic, Selector or Text map"
                              if not p.virtual_tags else
                              f"{len(self._rows)} virtual tag(s)" + (f" · {errs} with an error" if errs else "") +
                              " · numbers work in views, alarms and formulas (Virtual_<name>) · "
                              "double-click to edit, right-click for more")
        elif self._cat == "aggregation":
            self._aggregation_rows(sel[1] if sel[0] == "atype" else None)
            errs = sum(1 for a in p.aggregations if a["id"] in self.host.agg_engine.errors)
            self.hint.setText("No aggregation tags - Add > Statistic, Totalizer, Counter, Runtime, Min / Max hold "
                              "or Change" if not p.aggregations else
                              f"{len(self._rows)} aggregation tag(s)" + (f" · {errs} with an error" if errs else "") +
                              " · in formulas Aggregation_<name> (and _Previous) · double-click to edit, "
                              "right-click for more")
        elif self._cat == "system":
            self._system_rows(sel)
            devs = {d.id: d.name for d in p.devices}
            where = (sel[1] if sel[0] == "sysgroup" else devs.get(sel[1], "") if sel[0] == "sysdev" else "System")
            self.hint.setText(f"{len(self._rows)} system tag(s) in {where} · read-only, updated twice a second · "
                              "numbers work in views, alarms and formulas (System_<name>)")
        else:
            self.hint.setText(f"{CAT[self._cat][1]} tags: {CAT[self._cat][3]}.")
        self.table.setSortingEnabled(True)
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
            if vt.is_vt(did):                            # a text map's row colour (theme token)
                r = self.host.vt_engine.results.get(did[len(vt.VT_PREFIX):])
                tok = r.color if r is not None and r.color in T else ""
                it.setForeground(C_VALUE, QBrush(QColor(T[tok])) if tok else
                                 (QBrush(QColor(T["bad"])) if good is False else QBrush()))

    def refresh_assets(self):
        """Assets changed: only the Assets column."""
        for key, it in self._rows.items():
            it.setText(C_ASSETS, self._assets_text(key))

    def _apply_filter(self):
        text = self.filter.text().strip().lower()
        for it in self._rows.values():
            it.setHidden(bool(text) and not any(text in it.text(c).lower() for c in C_FILTER))

    # ---------------------------------------------------------------- selection
    def selected_tags(self) -> list[tuple[str, int]]:
        """Selected device tags (System Tags are not edited: see selected_system)."""
        return [k for k in self._selected_keys() if not ss.is_sys(k[0]) and not vt.is_vt(k[0]) and not ag.is_agg(k[0])]

    def selected_virtual(self) -> list[str]:
        return [k[0][len(vt.VT_PREFIX):] for k in self._selected_keys() if vt.is_vt(k[0])]

    def selected_agg(self) -> list[str]:
        return [k[0][len(ag.AGG_PREFIX):] for k in self._selected_keys() if ag.is_agg(k[0])]

    def selected_system(self) -> list[tuple[str, int]]:
        return [k for k in self._selected_keys() if ss.is_sys(k[0])]

    def _selected_keys(self) -> list[tuple[str, int]]:
        return [tuple(it.data(0, Qt.ItemDataRole.UserRole)) for it in self.table.selectedItems()]

    def _tag_double(self, it, _c):
        key = tuple(it.data(0, Qt.ItemDataRole.UserRole))
        if ag.is_agg(key[0]):
            self.host.edit_aggregation(key[0][len(ag.AGG_PREFIX):])
        elif vt.is_vt(key[0]):
            self.host.edit_virtual(key[0][len(vt.VT_PREFIX):])
        elif not ss.is_sys(key[0]):                      # System Tags are read-only: Properties only
            QTimer.singleShot(0, lambda: self.host.edit_tag(*key))

    def select_tag(self, did: str, idx: int):
        if (did, idx) not in self._rows:
            self.refresh(("cat", "system") if ss.is_sys(did) else ("cat", "virtual") if vt.is_vt(did)
                         else ("cat", "aggregation") if ag.is_agg(did) else ("device", did))
        it = self._rows.get((did, idx))
        if it is not None:
            self.table.clearSelection()
            it.setSelected(True)
            self.table.scrollToItem(it)

    def _tags_selected(self):
        sel, vsel, asel = self.selected_tags(), self.selected_virtual(), self.selected_agg()
        keys = self._selected_keys()
        kinds = {("agg" if ag.is_agg(k[0]) else "vt" if vt.is_vt(k[0]) else "sys" if ss.is_sys(k[0]) else "tag")
                 for k in keys}
        if len(kinds) > 1:                               # mixed categories (All tags): no single target
            self.host.select_object(None)
        elif asel:
            self.host.select_object(("agg", asel[0]) if len(asel) == 1 else None)
        elif vsel:
            self.host.select_object(("vt", vsel[0]) if len(vsel) == 1 else None)
        elif not sel and self.selected_system():
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
            self.host.select_object(self._sel if self._sel[0] == "device" else None)

    # ---------------------------------------------------------------- row menu
    def _use_menu(self, m: QMenu, keys):
        """Add to asset ▸ / New view from selection - for rows of any category."""
        sub = m.addMenu(f"Add to asset{'' if len(keys) == 1 else f' ({len(keys)})'}")
        assets = self.host.project.assets
        for a in sorted(assets, key=lambda x: asm.path(assets, x["id"]).lower()):
            sub.addAction(asm.path(assets, a["id"]), lambda aid=a["id"]: self.host.add_to_asset(keys, aid))
        if assets:
            sub.addSeparator()
        sub.addAction("New asset...", lambda: self.host.add_to_asset(keys, None))
        m.addAction("New view from selection", lambda: self.host.new_view_from(keys))

    def _tag_menu(self, pos):
        it = self.table.itemAt(pos)
        if it is None:
            return
        if not it.isSelected():
            self.table.clearSelection(); it.setSelected(True)
        keys = self._selected_keys()
        sel = self.selected_tags()
        m = QMenu(self)
        kinds = {("agg" if ag.is_agg(k[0]) else "vt" if vt.is_vt(k[0]) else "sys" if ss.is_sys(k[0]) else "tag")
                 for k in keys}
        if len(kinds) > 1:                               # mixed (All tags): only what fits every row
            self._use_menu(m, keys)
        elif self.selected_agg():
            asel = self.selected_agg()
            xs = [a for a in (self.host.aggregation(i) for i in asel) if a is not None]
            one, n = len(xs) == 1, len(xs)
            on = all(a["enabled"] for a in xs)
            self._items(m, [("Edit...", lambda: self.host.edit_aggregation(xs[0]["id"]), one),
                            ("Duplicate", lambda: self.host.duplicate_aggregation(xs[0]["id"]), one),
                            (("Disable" if on else "Enable") + ("" if one else f" ({n})"),
                             lambda: self.host.set_aggregations_enabled([a["id"] for a in xs], not on), True),
                            ("Reset now...", lambda: self.host.reset_aggregation(xs[0]["id"]),
                             one and ag.has_period(xs[0])),
                            None,
                            ("Add alarm...", lambda: self.host.add_alarm_for(ag.agg_key(xs[0]["id"])), one)])
            m.addSeparator(); self._use_menu(m, keys); m.addSeparator()
            m.addAction(f"Delete {'tag' if one else f'{n} tags'}...", lambda: self.host.delete_aggregations(asel))
        elif self.selected_virtual():
            vsel = self.selected_virtual()
            vs = [v for v in (self.host.virtual_tag(i) for i in vsel) if v is not None]
            one, n = len(vs) == 1, len(vs)
            on = all(v["enabled"] for v in vs)
            self._items(m, [("Edit...", lambda: self.host.edit_virtual(vs[0]["id"]), one),
                            ("Duplicate", lambda: self.host.duplicate_virtual(vs[0]["id"]), one),
                            (("Disable" if on else "Enable") + ("" if one else f" ({n})"),
                             lambda: self.host.set_virtuals_enabled([v["id"] for v in vs], not on), True),
                            None,
                            ("Add alarm...", lambda: self.host.add_alarm_for(vt.vt_key(vs[0]["id"])),
                             one and vs[0].get("dtype") != "text")])
            m.addSeparator(); self._use_menu(m, keys); m.addSeparator()
            m.addAction(f"Delete {'tag' if one else f'{n} tags'}...", lambda: self.host.delete_virtuals(vsel))
        elif self.selected_system():
            key = self.selected_system()[0]
            t = self.host.sys_tag(key[0])
            m.addAction("Add alarm for this tag...", lambda: self.host.add_alarm_for(key)).setEnabled(
                t is not None and t.streamed)
            m.addSeparator(); self._use_menu(m, keys)
        elif sel:
            did = sel[0][0]
            dev = self.host.project.device(did)
            ids = [i for d, i in sel if d == did]           # enable / delete: one device at a time
            if len(ids) == 1 and dev is not None:
                m.addAction("Edit tag...", lambda: QTimer.singleShot(0, lambda: self.host.edit_tag(did, ids[0])))
                tag = dev.tag(ids[0])
                w = m.addAction("Write value...", lambda: self.host.write_tag(did, ids[0]))
                w.setEnabled(tag is not None and tag.access == "RW" and self.host.device_state(did)[0] == "online")
                m.addSeparator()
            m.addAction("Enable", lambda: self.host.set_tags_enabled(did, ids, True))
            m.addAction("Disable", lambda: self.host.set_tags_enabled(did, ids, False))
            m.addSeparator()
            if len(ids) == 1:
                m.addAction("Add alarm for this tag...", lambda: self.host.add_alarm_for((did, ids[0])))
            self._use_menu(m, keys)
            m.addSeparator()
            m.addAction(f"Delete {len(ids)} tag(s)", self._delete)
        if not m.actions():
            return
        self._lock_menu(m, keep=("Write value...", "Add alarm for this tag...", "Add alarm...", "Reset now...",
                                 "New view from selection") + tuple(a.text() for a in m.actions() if a.menu()))
        m.exec(self.table.viewport().mapToGlobal(pos))

    @staticmethod
    def _items(m: QMenu, items):
        for item in items:
            if item is None:
                m.addSeparator(); continue
            text, slot, ok = item
            m.addAction(text, slot).setEnabled(ok)

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
            self.host.add_tags(dev.id, bulk)

    def _browse(self):
        dev = self._device()
        if dev is not None and dev.driver == "opcua":
            self.host.browse_opcua(dev.id)

    def _edit(self):
        sel = self.selected_tags()
        if self.selected_agg():
            self.host.edit_aggregation(self.selected_agg()[0])
        elif self.selected_virtual():
            self.host.edit_virtual(self.selected_virtual()[0])
        elif sel:
            QTimer.singleShot(0, lambda: self.host.edit_tag(*sel[0]))

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
        sel = self.selected_tags()
        if self.selected_agg() and not sel and not self.selected_virtual():
            self.host.delete_aggregations(self.selected_agg())
        elif self.selected_virtual() and not sel and not self.selected_agg():
            self.host.delete_virtuals(self.selected_virtual())
        elif sel and not self.selected_virtual() and not self.selected_agg():
            self.host.delete_tags(sel[0][0], [i for d, i in sel if d == sel[0][0]])

    def _update_buttons(self):
        """Fixed buttons: disabled, never hidden, when they do not apply."""
        dev = self._device()
        rec = self.host.recording
        keys = self._selected_keys()
        want = {"virtual": self.vt_menu, "aggregation": self.agg_menu}.get(self._cat, self.dev_menu)
        if self.btn_add.menu() is not want:
            self.btn_add.setMenu(want)
        one_kind = len({("agg" if ag.is_agg(k[0]) else "vt" if vt.is_vt(k[0]) else "sys" if ss.is_sys(k[0])
                         else "tag") for k in keys}) == 1
        editable = one_kind and len(keys) >= 1 and not self.selected_system()
        if self._cat in ("virtual", "aggregation"):
            self.btn_add.setEnabled(not rec)
        else:
            self.btn_add.setEnabled(dev is not None and not rec)
        self.act_browse.setEnabled(dev is not None and dev.driver == "opcua")
        self.btn_edit.setEnabled(editable)
        self.btn_del.setEnabled(editable and not rec)
        self.btn_imp.setEnabled(dev is not None and not rec)
        self.btn_exp.setEnabled(self._cat == "device" and any(d.tags for d in self.host.project.devices))
