"""
collections_page.py - the Collections document (Project tree > Collections): sets of tags for a
purpose (core/tag_collections.py). Like the Tags document it shows one purpose at a time - the
one chosen in the Project tree:

  Report      each collection has a statistics logging profile (15 min)
  KPI         its values are on the Start Page and in the Monitor bar
  Monitoring  a view in one click (New view / Monitor), alarms for the set
  Equipment   the equipment tree (sub-collections; the list shows their paths)

  bar    Add ▾ (Collection..., Sub-collection... for Equipment), Edit, Delete | Add tags..., Remove
         | New view, Monitor | search - fixed buttons, disabled when they do not apply
  left   the purpose as a band, below it: All, then one row per collection, flat (Equipment:
         "Station A / Pump 01", a parent before its subs); counts include sub-collections
  right  the tags of the selected collection (and its subs): attribute, tag, source, collection,
         live value, unit, quality, time

A tag can be in any number of collections. Select a collection: its properties (read-only,
Edit...); double-click: its pop-up. Select a tag row: its tag's properties.

Host API (duck-typed, DaqWindow):
  host.project (collections), host.reg_icon(target, name, token), host.recording (bool)
  host.tag_live(tid, idx) / host.last_value(tid, idx) / host.color_of(tid, idx)
  host.key_label(key) / host.key_source(key) / host.select_object(ref) / host.select_key(key) / host.open_tag(key)
  host.add_collection(purpose, parent) / host.edit_collection(cid) / host.delete_collection(cid)
  host.duplicate_collection(cid) / host.move_collection(cid, parent) / host.collection_add_tags(cid)
  host.remove_attributes([(cid, key)]) / host.new_view_from([key], name) / host.monitor_collection(cid)
  host.alarms_for_collection(cid)
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLineEdit, QMenu, QSplitter,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.core import tag_collections as tc
from nxlogger.ui.icons import swatch
from nxlogger.ui.theme.ui_kit import T, label, make_icon, vsep
from nxlogger.ui.widgets.connectivity import Band

COLS = ["Attribute", "Tag", "Source", "Collection", "Value", "Unit", "Quality", "Updated"]
C_VALUE, C_UNIT, C_QUAL, C_TIME = (COLS.index(c) for c in ("Value", "Unit", "Quality", "Updated"))
HIDDEN_KEY = "collections/hidden_columns"
MAX_PATH = 36                                     # longer paths: "… / Pump 01" (the tooltip has the whole path)
HINTS = {"report": "each Report collection has a statistics logging profile (Logging)",
         "kpi": "KPI values are on the Start Page and in the Monitor bar",
         "monitoring": "New view or Monitor shows a collection in one click",
         "equipment": "Equipment collections can have sub-collections (Add > Sub-collection...)"}


def short_path(path: str) -> str:
    if len(path) <= MAX_PATH:
        return path
    tail = path.split(" / ")[-1]
    return f"… / {tail}" if len(tail) < MAX_PATH - 4 else tail[:MAX_PATH - 1] + "…"


class CollectionsPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._purpose = "kpi"                        # the purpose shown (Project tree > Collections > ...)
        self._cid: str | None = None                 # the collection shown (None = every one of the purpose)
        self._items: dict[str, QTreeWidgetItem] = {}
        self._rows: dict[tuple, QTreeWidgetItem] = {}   # (collection id, tid, idx) -> row

        bar = QFrame(); bar.setObjectName("docBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(2)
        self.btn_add = self._button("Add", "plus", None, "Add a collection of this purpose")
        m = QMenu(self.btn_add)
        m.addAction("Collection...", lambda: self.host.add_collection(self._purpose, None))
        self.act_sub = m.addAction("Sub-collection...",
                                   lambda: self._cid and self.host.add_collection(self._purpose, self._cid))
        self.btn_add.setMenu(m); self.btn_add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_edit = self._button("Edit", "pencil", self._edit, "Edit the selected collection (double-click it)")
        self.btn_del = self._button("", "trash", self._delete, "Delete the selected collection (the tags stay)")
        self.btn_tags = self._button("Add tags...", "tag", lambda: self._cid and self.host.collection_add_tags(self._cid),
                                     "Add tags of any category to the selected collection")
        self.btn_rm = self._button("Remove", "x", self._remove, "Remove the selected tags from their collection")
        self.btn_view = self._button("New view", "layout", self._new_view,
                                     "A view with a trend and a table of the selected collection (or rows)")
        self.btn_mon = self._button("Monitor", "maximize", lambda: self._cid and self.host.monitor_collection(self._cid),
                                    "Show the selected collection full screen (Monitor mode)")
        for w in (self.btn_add, self.btn_edit, self.btn_del, None, self.btn_tags, self.btn_rm, None,
                  self.btn_view, self.btn_mon):
            bl.addWidget(vsep() if w is None else w)
        bl.addStretch(1)
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Search tags")
        self.filter.setClearButtonEnabled(True); self.filter.setMaximumWidth(220); self.filter.setMinimumWidth(120)
        self.filter.textChanged.connect(lambda _: self._apply_filter())
        bl.addWidget(self.filter)

        self.band = Band()
        self.tree = QTreeWidget(); self.tree.setObjectName("navTree")
        self.tree.setHeaderHidden(True); self.tree.setIndentation(14)
        self.tree.setProperty("plainSel", True)
        self.tree.currentItemChanged.connect(lambda cur, _p: self._collection_selected(cur))
        self.tree.itemDoubleClicked.connect(lambda it, _c: self._edit())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._collection_menu)
        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(0)
        lv.addWidget(self.band); lv.addWidget(self.tree, 1)

        self.table = QTreeWidget(); self.table.setHeaderLabels(COLS)
        self.table.setRootIsDecorated(False); self.table.setAlternatingRowColors(True)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True); self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        for c, w in enumerate((120, 170, 120, 180, 90, 60, 70)):
            self.table.header().resizeSection(c, w)
        self.table.header().setSectionResizeMode(len(COLS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.headerItem().setTextAlignment(C_VALUE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hidden = QSettings().value(HIDDEN_KEY, [])
        for c in range(1, len(COLS)):
            self.table.setColumnHidden(c, COLS[c] in (hidden if isinstance(hidden, list) else [hidden]))
        self.table.itemSelectionChanged.connect(self._rows_selected)
        self.table.itemDoubleClicked.connect(lambda it, _c: self.host.open_tag(self._key(it)))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._row_menu)
        sc = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.table)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut); sc.activated.connect(self._remove)
        self.hint = label("", muted=True, small=True); self.hint.setContentsMargins(8, 2, 8, 2)
        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        rv.addWidget(self.table, 1); rv.addWidget(self.hint)
        split = QSplitter(); split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(1, 1); split.setSizes([250, 780]); split.setChildrenCollapsible(False)
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

    @property
    def purpose(self) -> str:
        return self._purpose

    @property
    def current(self) -> str | None:
        return self._cid

    # ---------------------------------------------------------------- the list
    def show_purpose(self, purpose: str, cid: str | None = None):
        """Project tree > Collections > <purpose> (or one of its collections)."""
        self._purpose = purpose if purpose in tc.PURPOSE else self._purpose
        self.refresh(cid)

    def refresh(self, select: str | None | bool = False):
        """Model changed: rebuild. select: a collection id to show (its purpose too), None = all of
        the purpose, False = keep."""
        cols = self.host.project.collections
        if select is not False:
            self._cid = select
            c = tc.by_id(cols).get(select) if select else None
            if c is not None:
                self._purpose = c["purpose"]
        if self._cid is not None and not any(c["id"] == self._cid and c["purpose"] == self._purpose for c in cols):
            self._cid = None
        key, name, icon, what = tc.PURPOSE[self._purpose]
        self.band.set(name, icon, what)
        self.tree.blockSignals(True)
        self.tree.clear(); self._items.clear()
        mine = tc.of_purpose(cols, self._purpose)
        root = QTreeWidgetItem([f"All {name}  ({len(mine)})"])
        root.setIcon(0, make_icon("list", T["text"]))
        root.setData(0, Qt.ItemDataRole.UserRole, None)
        root.setToolTip(0, f"Every {name} collection")
        self.tree.addTopLevelItem(root)
        for c in tc.ordered(cols, self._purpose):    # flat, like Tags / Connectivity: one row per collection
            n = len(tc.keys(cols, c["id"]))
            path = tc.path(cols, c["id"])
            it = QTreeWidgetItem([f"{short_path(path)}  ({n})"])
            it.setIcon(0, swatch(c["color"]) if c.get("color") else make_icon(icon, T["muted"]))
            it.setData(0, Qt.ItemDataRole.UserRole, c["id"])
            it.setToolTip(0, path + (f"\n{c['description']}" if c.get("description") else ""))
            self.tree.addTopLevelItem(it)
            self._items[c["id"]] = it
        self.tree.setCurrentItem(self._items.get(self._cid) if self._cid else root)
        self.tree.blockSignals(False)
        self._fill()

    def _collection_selected(self, it):
        if it is None:
            return
        cid = it.data(0, Qt.ItemDataRole.UserRole)
        if cid != self._cid:
            self._cid = cid
            QTimer.singleShot(0, self._fill)           # not inside the list's own signal
        self.host.select_object(("collection", cid) if cid else None)

    def activate(self):
        self.host.select_object(("collection", self._cid) if self._cid else None)

    # ---------------------------------------------------------------- the table
    def _fill(self):
        cols = self.host.project.collections
        self.table.setSortingEnabled(False)
        self.table.clear(); self._rows.clear()
        for cid, x in tc.rows(cols, self._cid, self._purpose):
            key = tuple(x["key"])
            tag = self.host.key_label(key)
            it = QTreeWidgetItem([x.get("name") or tag.split(" · ")[-1], tag, self.host.key_source(key),
                                  tc.path(cols, cid), "", "", "", ""])
            it.setData(0, Qt.ItemDataRole.UserRole, (cid, *key))
            it.setIcon(0, swatch(self.host.color_of(*key)))
            it.setTextAlignment(C_VALUE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if tag.startswith("(deleted"):
                it.setForeground(1, QBrush(QColor(T["bad"])))
            self.table.addTopLevelItem(it)
            self._rows[(cid, *key)] = it
        self.table.setSortingEnabled(True)
        self._apply_filter()
        self.update_live()
        name = tc.PURPOSE[self._purpose][1]
        where = tc.path(cols, self._cid) if self._cid else f"every {name} collection"
        self.hint.setText(f"No {name} collections - Add > Collection..., or right-click tags in Tags > Add to "
                          "collection" if not tc.of_purpose(cols, self._purpose) else
                          f"{len(self._rows)} tag(s) in {where} · {HINTS[self._purpose]} · a tag can be in "
                          "several collections")
        self._update_buttons()

    def update_live(self):
        bad, ok, muted = QBrush(QColor(T["bad"])), QBrush(QColor(T["ok"])), QBrush(QColor(T["muted"]))
        for (cid, tid, idx), it in self._rows.items():
            val, qual, good, stamp = self.host.tag_live(tid, idx)
            unit = self.host.last_value(tid, idx)[1]
            for c, text in ((C_VALUE, val), (C_UNIT, unit), (C_QUAL, qual), (C_TIME, stamp)):
                if it.text(c) != (text or ""):
                    it.setText(c, text or "")
            it.setForeground(C_QUAL, ok if good else (bad if good is False else muted))

    def _apply_filter(self):
        text = self.filter.text().strip().lower()
        for it in self._rows.values():
            it.setHidden(bool(text) and not any(text in it.text(c).lower() for c in range(4)))

    @staticmethod
    def _key(it) -> tuple:
        d = it.data(0, Qt.ItemDataRole.UserRole)
        return (d[1], d[2])

    def _selected_rows(self) -> list[tuple]:
        return [tuple(it.data(0, Qt.ItemDataRole.UserRole)) for it in self.table.selectedItems()]

    def _rows_selected(self):
        rows = self._selected_rows()
        if len(rows) == 1:
            self.host.select_key(rows[0][1:])
        self._update_buttons()

    # ---------------------------------------------------------------- menus / actions
    def _collection_menu(self, pos):
        it = self.tree.itemAt(pos)
        cid = it.data(0, Qt.ItemDataRole.UserRole) if it is not None else None
        if not cid:
            return
        self.tree.setCurrentItem(it)
        cols = self.host.project.collections
        nested = self._purpose in tc.NESTED
        m = QMenu(self)
        m.addAction("Edit...", lambda: self.host.edit_collection(cid))
        if nested:
            m.addAction("Add sub-collection...", lambda: self.host.add_collection(self._purpose, cid)).setEnabled(
                tc.depth(cols, cid) < tc.MAX_DEPTH)
        m.addAction("Add tags...", lambda: self.host.collection_add_tags(cid))
        if nested:
            mv = m.addMenu("Move to")
            me = tc.by_id(cols)[cid]
            mv.addAction("(top level)", lambda: self.host.move_collection(cid, None)).setEnabled(
                me.get("parent") is not None)
            for c in tc.ordered(cols, self._purpose):
                if c["id"] != me.get("parent"):
                    act = mv.addAction(tc.path(cols, c["id"]), lambda p=c["id"]: self.host.move_collection(cid, p))
                    act.setEnabled(tc.can_move(cols, cid, c["id"]) == "")
        m.addAction("Duplicate", lambda: self.host.duplicate_collection(cid))
        m.addSeparator()
        m.addAction("New view", lambda: self.host.new_view_from(tc.keys(cols, cid), tc.path(cols, cid)))
        m.addAction("Monitor", lambda: self.host.monitor_collection(cid))
        m.addAction("Add alarms for its tags...", lambda: self.host.alarms_for_collection(cid))
        m.addSeparator()
        m.addAction("Delete...", lambda: self.host.delete_collection(cid))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _row_menu(self, pos):
        it = self.table.itemAt(pos)
        if it is None:
            return
        if not it.isSelected():
            self.table.clearSelection(); it.setSelected(True)
        rows = self._selected_rows()
        m = QMenu(self)
        m.addAction("Open tag...", lambda: self.host.open_tag(rows[0][1:])).setEnabled(len(rows) == 1)
        m.addAction("New view from selection", lambda: self.host.new_view_from([r[1:] for r in rows]))
        m.addSeparator()
        m.addAction(f"Remove from collection{'' if len(rows) == 1 else f' ({len(rows)})'}", self._remove)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _edit(self):
        if self._cid:
            self.host.edit_collection(self._cid)

    def _delete(self):
        if self._cid:
            self.host.delete_collection(self._cid)

    def _remove(self):
        rows = self._selected_rows()
        if rows:
            self.host.remove_attributes([(r[0], r[1:]) for r in rows])

    def _new_view(self):
        rows = self._selected_rows()
        cols = self.host.project.collections
        if rows:
            self.host.new_view_from([r[1:] for r in rows])
        elif self._cid:
            self.host.new_view_from(tc.keys(cols, self._cid), tc.path(cols, self._cid))

    def _update_buttons(self):
        has = self._cid is not None
        rows = bool(self._selected_rows())
        cols = self.host.project.collections
        nested = self._purpose in tc.NESTED
        self.act_sub.setEnabled(has and nested and tc.depth(cols, self._cid) < tc.MAX_DEPTH)
        self.act_sub.setVisible(nested)               # only Equipment nests (a menu item, not a toolbar button)
        self.btn_edit.setEnabled(has)
        self.btn_del.setEnabled(has)
        self.btn_tags.setEnabled(has)
        self.btn_rm.setEnabled(rows)
        some = rows or (has and bool(tc.keys(cols, self._cid)))
        self.btn_view.setEnabled(some)
        self.btn_mon.setEnabled(has and bool(tc.keys(cols, self._cid)))
