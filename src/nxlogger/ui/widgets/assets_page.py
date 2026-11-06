"""
assets_page.py - the Assets document: Enterprise > Area / Zone / Production > Machine / Station /
Unit > tags (core/assets.py). The left list is always one level (a band, an "All ..." row, the
items) like Tags and Connectivity; selecting a row filters the table, double-clicking a row goes one
level down:

  Enterprise (Project tree > Assets)          left: the areas      right: their machines
  An area (Project tree > Utilities)          left: its machines   right: their tags (+ the area's own)
  Templates (Project tree > Templates)        left: the templates  right: their attributes

  bar  Add ▾, Edit, Duplicate, Delete | Map tags..., Remove | New view, Monitor | search - the same
       on every level (Add ▾ offers the next level down; buttons that do not apply are disabled)

Each area / machine shows a status dot (alarm / ok / ring = no data) and the area rows their rollup
(the sum of each machine's first KPI value). An empty level shows one centred message with its
action buttons - never a pop-up.

Host API (duck-typed, DaqWindow):
  host.project (enterprise, assets, asset_templates), host.reg_icon(target, name)
  host.tag_live(tid, idx) / host.last_value(tid, idx) / host.color_of(tid, idx) / host.key_label(key)
  host.key_source(key) / host.select_object(ref) / host.select_key(key) / host.open_tag(key)
  host.enterprise_name() / host.asset_state(key) -> alarm | ok | none / host.asset_value(key) -> (v, unit)
  host.add_asset(type, parent, template id) / host.edit_asset(id) / host.delete_asset(id)
  host.duplicate_asset(id) / host.move_asset(id, area) / host.asset_add_tags(id) / host.map_asset_tags(id)
  host.remove_asset_rows([(asset id, key | None, name)]) / host.add_starter(key) / host.edit_enterprise()
  host.add_template(from asset id | None) / host.edit_template(id) / host.delete_template(id)
  host.duplicate_template(id) / host.apply_template(asset id, template id)
  host.new_view_from([key], name) / host.monitor_asset(id) / host.alarms_for_asset(id)
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
                               QPushButton, QSplitter, QStackedWidget, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from nxlogger.core import assets as am
from nxlogger.ui.icons import STATUS_SIZE, status_icon, swatch
from nxlogger.ui.theme.ui_kit import T, label, make_icon, vsep
from nxlogger.ui.widgets.connectivity import Band
from nxlogger.ui.widgets.summary_strip import SummaryStrip, plural

MCOLS = ["Name", "Type", "Area", "Template", "Tags", "Value", "Status", "Description"]
TCOLS = ["Name", "Tag", "Source", "Machine", "Value", "Unit", "Quality", "Updated"]
ACOLS = ["Attribute", "Template", "Unit", "KPI", "Match", "Used"]
C_VALUE, C_UNIT, C_QUAL, C_TIME = (TCOLS.index(c) for c in ("Value", "Unit", "Quality", "Updated"))
M_VALUE, M_STATUS = MCOLS.index("Value"), MCOLS.index("Status")
HIDDEN_KEY = "assets/hidden_columns"
STATUS_TEXT = {"alarm": "Alarm", "ok": "OK", "none": "No data"}
NOT_MAPPED = "Not mapped"
ENT, AREA, TPL = "enterprise", "area", "templates"


def fmt_rollup(r) -> str:
    if r is None:
        return ""
    v, unit = r
    return f"{v:,.1f} {unit}".strip()


class EmptyState(QWidget):
    """One centred message with an icon and up to three action buttons."""

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self); v.setContentsMargins(24, 24, 24, 24); v.setSpacing(8)
        self.icon, self.title = QLabel(), label("", name="sectionTitle")
        self.text = label("", muted=True); self.text.setWordWrap(True)
        for w in (self.icon, self.title, self.text):
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.row = QHBoxLayout(); self.row.setSpacing(8)
        v.addStretch(1)
        v.addWidget(self.icon); v.addWidget(self.title); v.addWidget(self.text)
        v.addLayout(self.row)
        v.addStretch(2)
        self.buttons: list[QPushButton] = []

    def set(self, icon: str, title: str, text: str, actions):
        """actions: [(text, slot | QMenu, primary)]."""
        self.icon.setPixmap(make_icon(icon, T["muted"]).pixmap(QSize(40, 40)))
        self.title.setText(title); self.text.setText(text)
        for b in self.buttons:
            b.setParent(None); b.deleteLater()
        self.buttons = []
        while self.row.count():
            self.row.takeAt(0)
        self.row.addStretch(1)
        for text_, slot, primary in actions:
            b = QPushButton(text_)
            if primary:
                b.setProperty("primary", True)
            if isinstance(slot, QMenu):
                b.setMenu(slot)
            else:
                b.clicked.connect(slot)
            self.row.addWidget(b); self.buttons.append(b)
        self.row.addStretch(1)


class AssetsPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._mode = ENT
        self._area: str | None = None               # the area opened (AREA)
        self._sel: str | None = None                # the left row (area / machine / template id; None = All)
        self._items: dict[str, QTreeWidgetItem] = {}
        self._rows: dict[tuple, QTreeWidgetItem] = {}   # right table rows by their data
        self._menus: list[QMenu] = []

        bar = QFrame(); bar.setObjectName("docBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(2)
        self.btn_add = self._button("Add", "plus", None, "Add an area, a machine, tags or a template")
        self.add_menu = QMenu(self.btn_add)
        self.add_menu.aboutToShow.connect(lambda: self._fill_add_menu(self.add_menu))
        self.btn_add.setMenu(self.add_menu); self.btn_add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_edit = self._button("Edit", "pencil", self._edit, "Edit the selected asset (or the Enterprise)")
        self.btn_dup = self._button("", "copy", self._duplicate, "Duplicate the selected asset / template")
        self.btn_del = self._button("", "trash", self._delete, "Delete the selected asset / template (the tags stay)")
        self.btn_map = self._button("Map tags...", "tag", self._map, "Fill the machine's empty attributes by name")
        self.btn_rm = self._button("Remove", "x", self._remove, "Remove the selected tags from their asset")
        self.btn_view = self._button("New view", "layout", self._new_view,
                                     "A view with a trend and a table of the selected asset (or rows)")
        self.btn_mon = self._button("Monitor", "maximize", lambda: self._target() and self.host.monitor_asset(
            self._target()), "Show the selected asset full screen (Monitor mode)")
        for w in (self.btn_add, self.btn_edit, self.btn_dup, self.btn_del, None, self.btn_map, self.btn_rm, None,
                  self.btn_view, self.btn_mon):
            bl.addWidget(vsep() if w is None else w)
        bl.addStretch(1)
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Search")
        self.filter.setClearButtonEnabled(True); self.filter.setMaximumWidth(220); self.filter.setMinimumWidth(120)
        self.filter.textChanged.connect(lambda _: self._apply_filter())
        bl.addWidget(self.filter)

        self.band = Band()
        self.tree = QTreeWidget(); self.tree.setObjectName("navTree")
        self.tree.setHeaderHidden(True); self.tree.setIndentation(6); self.tree.setRootIsDecorated(False)
        self.tree.setIconSize(QSize(*STATUS_SIZE))
        self.tree.setProperty("plainSel", True)
        self.tree.currentItemChanged.connect(lambda cur, _p: self._left_selected(cur))
        self.tree.itemDoubleClicked.connect(lambda it, _c: self._left_open(it))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(
            lambda pos: self._item_menu(self.tree, pos, self.tree.itemAt(pos)))
        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(0)
        lv.addWidget(self.band); lv.addWidget(self.tree, 1)

        self.mtable = self._table(MCOLS, (190, 90, 120, 120, 50, 110, 80))      # machines (Enterprise)
        self.mtable.setIconSize(QSize(*STATUS_SIZE))
        self.mtable.itemDoubleClicked.connect(lambda it, _c: self._machine_open(it))
        self.ttable = self._table(TCOLS, (130, 190, 120, 150, 90, 60, 70))      # tags (an area)
        self.ttable.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.ttable.itemDoubleClicked.connect(lambda it, _c: self._tag_open(it))
        self.ttable.headerItem().setTextAlignment(C_VALUE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hidden = QSettings().value(HIDDEN_KEY, [])
        for c in range(1, len(TCOLS)):
            self.ttable.setColumnHidden(c, TCOLS[c] in (hidden if isinstance(hidden, list) else [hidden]))
        sc = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.ttable)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut); sc.activated.connect(self._remove)
        self.atable = self._table(ACOLS, (170, 130, 70, 50, 150))              # template attributes
        self.atable.itemDoubleClicked.connect(lambda it, _c: self._template_open(it))
        self.empty = EmptyState()
        self.stack = QStackedWidget()
        for w in (self.mtable, self.ttable, self.atable, self.empty):
            self.stack.addWidget(w)
        self.hint = label("", muted=True, small=True); self.hint.setContentsMargins(8, 2, 8, 2)
        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        rv.addWidget(self.stack, 1); rv.addWidget(self.hint)
        split = QSplitter(); split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(1, 1); split.setSizes([260, 780]); split.setChildrenCollapsible(False)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        self.summary = SummaryStrip()                # the summary under the toolbar (no rollup)
        self.summary.chip_clicked.connect(self._summary_chip)
        v.addWidget(bar); v.addWidget(self.summary); v.addWidget(split, 1)

    def _button(self, text, icon, slot, tip) -> QToolButton:
        b = QToolButton(); b.setToolTip(tip)
        if text:
            b.setText(text); b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.host.reg_icon(b, icon)
        if slot:
            b.clicked.connect(slot)
        return b

    def _table(self, cols, widths) -> QTreeWidget:
        t = QTreeWidget(); t.setHeaderLabels(cols)
        t.setRootIsDecorated(False); t.setAlternatingRowColors(True); t.setUniformRowHeights(True)
        t.setSortingEnabled(True); t.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        for c, w in enumerate(widths):
            t.header().resizeSection(c, w)
        t.header().setSectionResizeMode(len(cols) - 1, QHeaderView.ResizeMode.Stretch)
        t.itemSelectionChanged.connect(self._rows_selected)
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        t.customContextMenuRequested.connect(lambda pos, t=t: self._table_menu(t, pos))
        return t

    # ---------------------------------------------------------------- state
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def area(self) -> str | None:
        return self._area

    @property
    def current(self) -> str | None:
        return self._sel

    @property
    def assets(self) -> list[dict]:
        return self.host.project.assets

    @property
    def templates(self) -> list[dict]:
        return self.host.project.asset_templates

    def _table_sel(self) -> list:
        t = {ENT: self.mtable, AREA: self.ttable, TPL: self.atable}[self._mode]
        return [it.data(0, Qt.ItemDataRole.UserRole) for it in t.selectedItems()] \
            if self.stack.currentWidget() is t else []

    def _target(self) -> str | None:
        """The asset Edit / Duplicate / Delete / Map / Monitor act on (None = the Enterprise)."""
        if self._mode == ENT:
            sel = [d for d in self._table_sel() if d]
            return sel[0] if len(sel) == 1 else self._sel
        if self._mode == AREA:
            return self._sel or self._area
        return None

    def _template(self) -> str | None:
        if self._mode != TPL:
            return None
        if self._sel:
            return self._sel
        sel = self._table_sel()
        return sel[0][0] if len(sel) == 1 and sel[0] else None

    # ---------------------------------------------------------------- levels
    def show_enterprise(self, select: str | None = None):
        """Project tree > Assets: the areas and their machines."""
        self._mode, self._area, self._sel = ENT, None, select
        self._rebuild()

    def open(self, aid: str | None, select: str | None = None):
        """Project tree > an area (a machine: its area, that row selected)."""
        a = am.by_id(self.assets).get(aid)
        if a is None:
            return self.show_enterprise()
        if a.get("parent") is not None:
            aid, select = a["parent"], a["id"]
        self._mode, self._area, self._sel = AREA, aid, select
        self._rebuild()
        self.activate()

    def show_templates(self, select: str | None = None):
        self._mode, self._area, self._sel = TPL, None, select
        self._rebuild()
        self.activate()

    def refresh(self, select: str | None | bool = False):
        """Model changed: rebuild. select: an asset / template to show (False = keep, None = All)."""
        if select is None:
            self._sel = None
        elif select is not False:
            a = am.by_id(self.assets).get(select)
            if any(t["id"] == select for t in self.templates):
                self._mode, self._area, self._sel = TPL, None, select
            elif a is not None and a.get("parent") is None:
                if self._mode == AREA:
                    self._area, self._sel = select, None
                else:
                    self._mode, self._sel = ENT, select
            elif a is not None:
                self._mode, self._area, self._sel = AREA, a["parent"], select
        self._rebuild()

    def _rebuild(self):
        idx = am.by_id(self.assets)
        if self._mode == AREA and (self._area not in idx or idx[self._area].get("parent") is not None):
            self._mode, self._area = ENT, None
        if self._mode == TPL:
            if self._sel not in {t["id"] for t in self.templates}:
                self._sel = None
        elif self._sel is not None and (self._sel not in idx or (
                self._mode == ENT and idx[self._sel].get("parent") is not None) or (
                self._mode == AREA and idx[self._sel].get("parent") != self._area)):
            self._sel = None
        self.tree.blockSignals(True)
        self.tree.clear(); self._items.clear()
        rows = []
        if self._mode == ENT:
            ar = am.areas(self.assets)
            r = fmt_rollup(am.rollup(self.assets, None, self.host.asset_value))
            self.band.set(self.host.enterprise_name() + (f"   {r}" if r else ""), "house",
                          "The Enterprise: its areas, zones and production - double-click one to open it")
            root = QTreeWidgetItem([f"All areas  ({len(ar)})"])
            for a in ar:
                r = fmt_rollup(am.rollup(self.assets, a["id"], self.host.asset_value))
                n = len(am.children(self.assets, a["id"]))
                rows.append((a, f"{a['name']}{f'   {r}' if r else ''}  ({n})"))
        elif self._mode == AREA:
            a = idx[self._area]
            self.band.set(f"{a['name']} · {am.type_name(a)}", am.icon_of(a),
                          a.get("description") or "Its machines - select one to see its tags")
            root = QTreeWidgetItem([f"All {a['name']}  ({len(am.keys(self.assets, self._area))})"])
            tnames = {t["id"]: t["name"] for t in self.templates}
            for m in am.children(self.assets, self._area):
                t = tnames.get(m.get("template"))
                rows.append((m, f"{m['name']}{f'  [{t}]' if t else ''}  ({len(m['attributes'])})"))
        else:
            self.band.set("Templates", "copy", "What a kind of machine has: its attributes, units, KPIs and the "
                          "tag names Map tags... looks for")
            root = QTreeWidgetItem([f"All templates  ({len(self.templates)})"])
            for t in self.templates:
                rows.append((t, f"{t['name']}  ({am.template_used(self.assets, t['id'])} used)"))
        root.setIcon(0, self._all_icon())                # the dot column: the worst state of the rows below
        root.setData(0, Qt.ItemDataRole.UserRole, None)
        self.tree.addTopLevelItem(root)
        self._root = root
        for x, text in rows:
            it = QTreeWidgetItem([text])
            if self._mode == TPL:                        # templates: no state - an empty dot slot
                it.setIcon(0, status_icon(am.icon_of(x), "blank", T["muted"]))
            else:
                it.setIcon(0, status_icon(am.icon_of(x), am.status(self.assets, x["id"], self.host.asset_state),
                                          T["muted"]))
            it.setData(0, Qt.ItemDataRole.UserRole, x["id"])
            it.setToolTip(0, (am.path(self.assets, x["id"]) if self._mode != TPL else x["name"])
                          + (f"\n{x['description']}" if x.get("description") else ""))
            self.tree.addTopLevelItem(it)
            self._items[x["id"]] = it
        self.tree.setCurrentItem(self._items.get(self._sel) if self._sel else root)
        self.tree.blockSignals(False)
        self._fill()

    def _left_selected(self, it):
        if it is None:
            return
        sid = it.data(0, Qt.ItemDataRole.UserRole)
        if sid != self._sel:
            self._sel = sid
            QTimer.singleShot(0, self._fill)           # not inside the list's own signal
        self.activate()

    def _left_open(self, it):
        sid = it.data(0, Qt.ItemDataRole.UserRole)
        if not sid:
            return
        if self._mode == ENT:
            self.open(sid)
        else:
            (self.host.edit_template if self._mode == TPL else self.host.edit_asset)(sid)

    def activate(self):
        if self._mode == TPL:
            t = self._template()
            self.host.select_object(("template", t) if t else ("templates", None))
        else:
            t = self._target()
            self.host.select_object(("asset", t) if t else ("enterprise", None))

    # ---------------------------------------------------------------- the tables
    def _fill(self):
        {ENT: self._fill_machines, AREA: self._fill_tags, TPL: self._fill_attributes}[self._mode]()
        self._update_buttons()
        self.refresh_summary()

    def _show(self, table, empty):
        """The table, or (empty = (icon, title, text, actions)) the empty state."""
        if empty:
            self.empty.set(*empty)
            self.stack.setCurrentWidget(self.empty)
        else:
            self.stack.setCurrentWidget(table)

    def _menu(self, fill) -> QMenu:
        m = QMenu(self); self._menus.append(m)
        m.aboutToShow.connect(lambda: fill(m))
        if len(self._menus) > 8:
            self._menus.pop(0).deleteLater()
        return m

    def _fill_machines(self):
        t = self.mtable
        t.blockSignals(True); t.setSortingEnabled(False)
        t.clear(); self._rows.clear()
        areas = [a for a in am.areas(self.assets) if self._sel in (None, a["id"])]
        tnames = {x["id"]: x["name"] for x in self.templates}
        for a in areas:
            for m in am.children(self.assets, a["id"]):
                st = am.status(self.assets, m["id"], self.host.asset_state)
                it = QTreeWidgetItem([m["name"], am.type_name(m), a["name"], tnames.get(m.get("template"), "-"),
                                      str(len(m["attributes"])), fmt_rollup(am.rollup(self.assets, m["id"],
                                                                                      self.host.asset_value)),
                                      STATUS_TEXT[st], m.get("description", "")])
                it.setIcon(0, status_icon(am.icon_of(m), st, T["muted"]))
                it.setData(0, Qt.ItemDataRole.UserRole, m["id"])
                for c in (4, M_VALUE):
                    it.setTextAlignment(c, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                t.addTopLevelItem(it)
                self._rows[(m["id"],)] = it
        t.setSortingEnabled(True); t.blockSignals(False)
        self._apply_filter()
        empty = None
        if not self.assets:
            empty = ("grid", "No areas yet", "Areas group your machines: a plant area, a production line or a "
                     "building zone.", [("Add area", lambda: self.host.add_asset("area", None), True),
                                        ("Use a starter", self._menu(self._fill_starters), False)])
        elif not self._rows:
            a = am.by_id(self.assets).get(self._sel)
            where = a["name"] if a else "any area"
            own = len(a["attributes"]) if a else 0
            empty = ("cpu", f"No machines in {where}",
                     (f"{a['name']} has {own} tag(s) of its own - double-click it to open it. " if own else "")
                     + "Machines, stations and units hold the tags of one piece of equipment.",
                     [("Add machine", lambda: self._add_machine("machine"), True),
                      ("From template", self._menu(self._fill_from_template), False)]
                     if a else [])
        self._show(t, empty)
        n = len(self._rows)
        self.hint.setText(f"{n} machine(s) · select an area to filter, double-click a machine to open its area"
                          if self.assets else "Add > Area... (or Use a starter) · then machines, then tags")

    def _fill_tags(self):
        t = self.ttable
        t.blockSignals(True); t.setSortingEnabled(False)
        t.clear(); self._rows.clear()
        for aid, x in am.rows(self.assets, self._sel or self._area):
            key = tuple(x["key"]) if x["key"] else None
            tag = self.host.key_label(key) if key else NOT_MAPPED
            m = am.by_id(self.assets)[aid]
            it = QTreeWidgetItem([x.get("name") or tag.split(" · ")[-1], tag,
                                  self.host.key_source(key) if key else "-",
                                  m["name"] if m.get("parent") else "-", "", "", "", ""])
            data = (aid, *(key or (None, None)), x.get("name", ""))
            it.setData(0, Qt.ItemDataRole.UserRole, data)
            it.setIcon(0, swatch(self.host.color_of(*key)) if key else make_icon("tag", T["muted"]))
            if x.get("kpi"):
                it.setToolTip(0, "KPI: on the Start Page, in the Monitor bar and the rollups")
                f = it.font(0); f.setBold(True); it.setFont(0, f)
            it.setTextAlignment(C_VALUE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if not key:
                it.setForeground(1, QBrush(QColor(T["muted"])))
            elif tag.startswith("(deleted"):
                it.setForeground(1, QBrush(QColor(T["bad"])))
            t.addTopLevelItem(it)
            self._rows[data] = it
        t.setSortingEnabled(True); t.blockSignals(False)
        self._apply_filter()
        self.update_live()
        idx = am.by_id(self.assets)
        empty = None
        if not self._rows:
            if self._sel:
                m = idx[self._sel]
                empty = ("tag", f"{m['name']} has no tags", "Add tags of any category, or give it a template and "
                         "map its tags by name.",
                         [("Add tags...", lambda: self.host.asset_add_tags(self._sel), True),
                          ("Map tags...", lambda: self.host.map_asset_tags(self._sel), False),
                          ("Apply template", self._menu(self._fill_apply_template), False)])
            elif not am.children(self.assets, self._area):
                empty = ("cpu", f"No machines in {idx[self._area]['name']}", "Machines, stations and units hold "
                         "the tags of one piece of equipment.",
                         [("Add machine", lambda: self._add_machine("machine"), True),
                          ("From template", self._menu(self._fill_from_template), False),
                          ("Add tags...", lambda: self.host.asset_add_tags(self._area), False)])
        self._show(t, empty)
        where = idx[self._sel]["name"] if self._sel else f"{idx[self._area]['name']} and its machines"
        unmapped = sum(1 for d in self._rows if d[1] is None)
        self.hint.setText(f"{len(self._rows)} tag(s) in {where}" + (f" · {unmapped} not mapped - Map tags..."
                                                                     if unmapped else "")
                          + " · bold = KPI · a tag can be in several assets")

    def _fill_attributes(self):
        t = self.atable
        t.blockSignals(True); t.setSortingEnabled(False)
        t.clear(); self._rows.clear()
        for x in self.templates:
            if self._sel not in (None, x["id"]):
                continue
            used = str(am.template_used(self.assets, x["id"]))
            for s in x["attributes"]:
                it = QTreeWidgetItem([s["name"], x["name"], s["unit"], "✓" if s["kpi"] else "", s["match"], used])
                it.setIcon(0, make_icon(am.icon_of(x), T["muted"]))
                it.setData(0, Qt.ItemDataRole.UserRole, (x["id"], s["name"]))
                t.addTopLevelItem(it)
                self._rows[(x["id"], s["name"])] = it
        t.setColumnHidden(1, self._sel is not None)
        t.setSortingEnabled(True); t.blockSignals(False)
        self._apply_filter()
        empty = None
        if not self.templates:
            empty = ("copy", "No templates yet", "A template is what a kind of machine has (Power kW, Pressure "
                     "bar ...): give it to machines, then Map tags... fills them by name.",
                     [("Add template", lambda: self.host.add_template(None), True),
                      ("From machine", self._menu(self._fill_from_machine), False)])
        elif not self._rows:
            empty = ("copy", "No attributes", "Edit the template to add its attributes.",
                     [("Edit...", lambda: self.host.edit_template(self._sel), True)])
        self._show(t, empty)
        self.hint.setText(f"{len(self.templates)} template(s) · double-click to edit · Match: the tag names "
                          "Map tags... looks for")

    # ---------------------------------------------------------------- the summary strip
    def refresh_summary(self):
        """Enterprise │ areas · machines │ tags good · no data │ not mapped · in alarm · empty - a chip
        opens the first machine it counts; the first machine in alarm on a second line."""
        h = self.host.assets_summary()
        name = self.host.enterprise_name()
        if not self.assets:
            self.summary.set("muted", "○ No assets yet - group your tags by area and machine", (), None,
                             [("+  Add area", lambda: self.host.add_asset("area", None))])
            return
        state = "bad" if h["alarm"] else ("warn" if h["not_mapped"] or h["nodata"] else "ok")
        chips = [("areas", plural(h["areas"], "area"), ""), ("machines", plural(h["machines"], "machine"), ""),
                 ("|", "", ""), ("good", f"{h['tags']:,} tags · {h['good']:,} good", "ok")]
        if h["nodata"]:
            chips.append(("nodata", f"{h['nodata']:,} no data", "warn"))
        extra = [(k, text, tok) for k, text, tok in
                 (("not_mapped", f"{h['not_mapped']:,} not mapped", "warn"),
                  ("alarm", f"{plural(h['alarm'], 'machine')} in alarm", "bad"),
                  ("empty", f"{plural(h['empty'], 'empty machine')}", "muted")) if h[k]]
        if extra:
            chips += [("|", "", "")] + extra
        issue = None
        first = h["first"].get("alarm")
        if first:
            m = am.by_id(self.assets).get(first)
            issue = (f"{am.path(self.assets, first)} in alarm" + (f"  (+{h['alarm'] - 1} more)" if h["alarm"] > 1 else ""),
                     "bad", [("Open machine ›", lambda i=first: self.open(i))]) if m else None
        self.summary.set(state, f"● {name}", chips, issue)

    def _summary_chip(self, key):
        """A chip: open the first machine it counts (areas / machines: the Enterprise)."""
        self.summary.clear_filter()
        target = self.host.assets_summary()["first"].get(key)
        if key in ("areas", "machines", "good"):
            self.show_enterprise()
        elif target:
            self.open(target)
        self.refresh_summary()

    def update_live(self):
        """The tags' values (an area) - the dots and rollups follow on refresh_status."""
        if self._mode != AREA:
            return
        bad, ok, muted = QBrush(QColor(T["bad"])), QBrush(QColor(T["ok"])), QBrush(QColor(T["muted"]))
        for d, it in self._rows.items():
            if d[1] is None:
                continue
            val, qual, good, stamp = self.host.tag_live(d[1], d[2])
            unit = self.host.last_value(d[1], d[2])[1]
            for c, text in ((C_VALUE, val), (C_UNIT, unit), (C_QUAL, qual), (C_TIME, stamp)):
                if it.text(c) != (text or ""):
                    it.setText(c, text or "")
            it.setForeground(C_QUAL, ok if good else (bad if good is False else muted))

    def _all_icon(self):
        """The "All ..." row: the list icon after the worst state of the area / plant (no dot: templates)."""
        if self._mode == TPL:
            return status_icon("list", "blank")
        return status_icon("list", am.status(self.assets, self._area if self._mode == AREA else None,
                                              self.host.asset_state))

    def refresh_status(self):
        """The status dots and rollups (every few seconds) - and the summary strip."""
        self.refresh_summary()
        if self._mode == TPL:
            return
        if getattr(self, "_root", None) is not None:
            try:
                self._root.setIcon(0, self._all_icon())
            except RuntimeError:                         # the list was rebuilt
                self._root = None
        for aid, it in self._items.items():
            if aid in am.by_id(self.assets):
                a = am.by_id(self.assets)[aid]
                it.setIcon(0, status_icon(am.icon_of(a), am.status(self.assets, aid, self.host.asset_state),
                                          T["muted"]))
                if self._mode == ENT:
                    r = fmt_rollup(am.rollup(self.assets, aid, self.host.asset_value))
                    it.setText(0, f"{a['name']}{f'   {r}' if r else ''}  ({len(am.children(self.assets, aid))})")
        if self._mode == ENT:
            r = fmt_rollup(am.rollup(self.assets, None, self.host.asset_value))
            self.band.text.setText(self.host.enterprise_name() + (f"   {r}" if r else ""))
            for (mid,), it in self._rows.items():
                st = am.status(self.assets, mid, self.host.asset_state)
                m = am.by_id(self.assets).get(mid)
                if m is None:
                    continue
                it.setIcon(0, status_icon(am.icon_of(m), st, T["muted"]))
                it.setText(M_STATUS, STATUS_TEXT[st])
                it.setText(M_VALUE, fmt_rollup(am.rollup(self.assets, mid, self.host.asset_value)))

    def _apply_filter(self):
        text = self.filter.text().strip().lower()
        for it in self._rows.values():
            it.setHidden(bool(text) and not any(text in it.text(c).lower() for c in range(5)))

    def _rows_selected(self):
        sel = self._table_sel()
        if self._mode == AREA and len(sel) == 1 and sel[0][1] is not None:
            self.host.select_key((sel[0][1], sel[0][2]))
        elif self._mode == ENT and len(sel) == 1:
            self.host.select_object(("asset", sel[0]))
        elif self._mode == TPL and len(sel) == 1:
            self.host.select_object(("template", sel[0][0]))
        self._update_buttons()

    def _machine_open(self, it):
        mid = it.data(0, Qt.ItemDataRole.UserRole)
        if mid:
            self.open(mid)

    def _tag_open(self, it):
        d = it.data(0, Qt.ItemDataRole.UserRole)
        if d[1] is not None:
            self.host.open_tag((d[1], d[2]))
        else:
            self.host.map_asset_tags(d[0])

    def _template_open(self, it):
        self.host.edit_template(it.data(0, Qt.ItemDataRole.UserRole)[0])

    # ---------------------------------------------------------------- menus / actions
    def _area_for_add(self) -> str | None:
        if self._mode == AREA:
            return self._area
        return self._sel if self._mode == ENT else None

    def _add_machine(self, typ: str, template: str | None = None):
        area = self._area_for_add()
        if area:
            self.host.add_asset(typ, area, template)

    def _fill_add_menu(self, m: QMenu):
        m.clear()
        if self._mode == TPL:
            m.addAction(make_icon("copy", T["text"]), "Template...", lambda: self.host.add_template(None))
            self._fill_from_machine(m.addMenu("From machine"))
            return
        area = self._area_for_add()
        if self._mode == AREA:
            for key, name, icon in am.LEVEL3:
                m.addAction(make_icon(icon, T["text"]), f"{name}...", lambda k=key: self._add_machine(k))
            self._fill_from_template(m.addMenu("From template"))
            t = self._target()
            m.addAction(make_icon("tag", T["text"]), "Tags...", lambda: self.host.asset_add_tags(t))
            m.addSeparator()
        for key, name, icon in am.LEVEL2:
            m.addAction(make_icon(icon, T["text"]), f"{name}...", lambda k=key: self.host.add_asset(k, None))
        if self._mode == ENT:
            m.addSeparator()
            where = am.by_id(self.assets)[area]["name"] if area else "select an area"
            for key, name, icon in am.LEVEL3:
                m.addAction(make_icon(icon, T["text"]), f"{name}... (in {where})",
                            lambda k=key: self._add_machine(k)).setEnabled(area is not None)
            sub = m.addMenu("From template")
            sub.setEnabled(area is not None)
            self._fill_from_template(sub)
            m.addSeparator()
            self._fill_starters(m.addMenu("Use a starter"))

    def _fill_from_template(self, m: QMenu):
        m.clear()
        for t in self.templates:
            m.addAction(make_icon(am.icon_of(t), T["text"]), t["name"],
                        lambda tid=t["id"]: self._add_machine(next(x["type"] for x in self.templates
                                                                   if x["id"] == tid), tid))
        if not self.templates:
            m.addAction("No templates yet").setEnabled(False)

    def _fill_apply_template(self, m: QMenu):
        m.clear()
        aid = self._target()
        for t in self.templates:
            m.addAction(make_icon(am.icon_of(t), T["text"]), t["name"],
                        lambda tid=t["id"]: self.host.apply_template(aid, tid))
        if not self.templates:
            m.addAction("No templates yet").setEnabled(False)

    def _fill_from_machine(self, m: QMenu):
        m.clear()
        ms = am.machines(self.assets, None)
        for x in ms:
            m.addAction(make_icon(am.icon_of(x), T["text"]), am.path(self.assets, x["id"]),
                        lambda aid=x["id"]: self.host.add_template(aid))
        if not ms:
            m.addAction("No machines yet").setEnabled(False)

    def _fill_starters(self, m: QMenu):
        m.clear()
        for key, name, typ, temps in am.STARTERS:
            m.addAction(make_icon(am.TYPE[typ][2], T["text"]),
                        f"{name}  ({', '.join(t[0] for t in temps)})", lambda k=key: self.host.add_starter(k))

    def _item_menu(self, view, pos, it):
        sid = it.data(0, Qt.ItemDataRole.UserRole) if it is not None else None
        if not sid:
            return
        view.setCurrentItem(it)
        if self._mode == TPL:
            m = QMenu(self)
            m.addAction("Edit...", lambda: self.host.edit_template(sid))
            m.addAction("Duplicate", lambda: self.host.duplicate_template(sid))
            m.addSeparator()
            m.addAction("Delete...", lambda: self.host.delete_template(sid))
            m.exec(view.viewport().mapToGlobal(pos))
            return
        self._asset_menu(sid).exec(view.viewport().mapToGlobal(pos))

    def _asset_menu(self, aid: str) -> QMenu:
        a = am.by_id(self.assets)[aid]
        m = QMenu(self)
        m.addAction("Edit...", lambda: self.host.edit_asset(aid))
        if a.get("parent") is None:
            if self._mode == ENT:
                m.addAction("Open", lambda: self.open(aid))
            add = m.addMenu("Add machine")
            for key, name, icon in am.LEVEL3:
                add.addAction(make_icon(icon, T["text"]), f"{name}...", lambda k=key: self.host.add_asset(k, aid))
            if self.templates:
                add.addSeparator()
                for t in self.templates:
                    add.addAction(make_icon(am.icon_of(t), T["text"]), f"From {t['name']}",
                                  lambda tid=t["id"], typ=t["type"]: self.host.add_asset(typ, aid, tid))
        m.addAction("Add tags...", lambda: self.host.asset_add_tags(aid))
        if a.get("parent") is not None:
            m.addAction("Map tags...", lambda: self.host.map_asset_tags(aid))
            ap = m.addMenu("Apply template")
            for t in self.templates:
                ap.addAction(make_icon(am.icon_of(t), T["text"]), t["name"],
                             lambda tid=t["id"]: self.host.apply_template(aid, tid))
            ap.setEnabled(bool(self.templates))
            mv = m.addMenu("Move to")
            for x in am.areas(self.assets):
                if x["id"] != a["parent"]:
                    mv.addAction(make_icon(am.icon_of(x), T["text"]), x["name"],
                                 lambda p=x["id"]: self.host.move_asset(aid, p))
            mv.setEnabled(len(am.areas(self.assets)) > 1)
            m.addAction("Make a template from it", lambda: self.host.add_template(aid))
        m.addAction("Duplicate", lambda: self.host.duplicate_asset(aid))
        m.addSeparator()
        m.addAction("New view", lambda: self.host.new_view_from(am.keys(self.assets, aid),
                                                                am.path(self.assets, aid)))
        m.addAction("Monitor", lambda: self.host.monitor_asset(aid))
        m.addAction("Add alarms for its tags...", lambda: self.host.alarms_for_asset(aid))
        m.addSeparator()
        m.addAction("Delete...", lambda: self.host.delete_asset(aid))
        return m

    def _table_menu(self, t, pos):
        it = t.itemAt(pos)
        if it is None:
            return
        if not it.isSelected():
            t.clearSelection(); it.setSelected(True)
        sel = self._table_sel()
        if self._mode == ENT:
            self._asset_menu(sel[0]).exec(t.viewport().mapToGlobal(pos))
            return
        if self._mode == TPL:
            tid = sel[0][0]
            m = QMenu(self)
            m.addAction("Edit template...", lambda: self.host.edit_template(tid))
            m.exec(t.viewport().mapToGlobal(pos))
            return
        keys = [(d[1], d[2]) for d in sel if d[1] is not None]
        m = QMenu(self)
        m.addAction("Open tag...", lambda: self.host.open_tag(keys[0])).setEnabled(len(keys) == 1)
        m.addAction("Map tags...", lambda: self.host.map_asset_tags(sel[0][0])).setEnabled(
            am.by_id(self.assets)[sel[0][0]].get("parent") is not None)
        m.addAction("New view from selection", lambda: self.host.new_view_from(keys)).setEnabled(bool(keys))
        m.addSeparator()
        m.addAction(f"Remove from asset{'' if len(sel) == 1 else f' ({len(sel)})'}", self._remove)
        m.exec(t.viewport().mapToGlobal(pos))

    def _edit(self):
        if self._mode == TPL:
            if self._template():
                self.host.edit_template(self._template())
        elif self._target():
            self.host.edit_asset(self._target())
        elif self._mode == ENT:
            self.host.edit_enterprise()

    def _duplicate(self):
        if self._mode == TPL:
            if self._template():
                self.host.duplicate_template(self._template())
        elif self._target():
            self.host.duplicate_asset(self._target())

    def _delete(self):
        if self._mode == TPL:
            if self._template():
                self.host.delete_template(self._template())
        elif self._target():
            self.host.delete_asset(self._target())

    def _map(self):
        t = self._target()
        if t:
            self.host.map_asset_tags(t)

    def _remove(self):
        if self._mode != AREA:
            return
        rows = [(d[0], (d[1], d[2]) if d[1] is not None else None, d[3]) for d in self._table_sel()]
        if rows:
            self.host.remove_asset_rows(rows)

    def _new_view(self):
        if self._mode == AREA:
            keys = [(d[1], d[2]) for d in self._table_sel() if d[1] is not None]
            if keys:
                self.host.new_view_from(keys)
                return
        t = self._target()
        if t:
            self.host.new_view_from(am.keys(self.assets, t), am.path(self.assets, t))
        elif self._mode == ENT and self.assets:
            self.host.new_view_from(am.keys(self.assets, None), self.host.enterprise_name())

    def _update_buttons(self):
        t = self._target()
        a = am.by_id(self.assets).get(t)
        tpl = self._mode == TPL
        self.btn_edit.setEnabled(bool(self._template()) if tpl else (t is not None or self._mode == ENT))
        for b in (self.btn_dup, self.btn_del):
            b.setEnabled(bool(self._template()) if tpl else t is not None)
        self.btn_map.setEnabled(a is not None and a.get("parent") is not None)
        self.btn_rm.setEnabled(self._mode == AREA and bool(self._table_sel()))
        has = bool(am.keys(self.assets, t)) if t else (self._mode == ENT and bool(am.keys(self.assets, None)))
        self.btn_view.setEnabled(not tpl and has)
        self.btn_mon.setEnabled(not tpl and t is not None and bool(am.keys(self.assets, t)))
