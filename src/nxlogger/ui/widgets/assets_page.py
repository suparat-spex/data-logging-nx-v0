"""
assets_page.py - the Assets document (Project tree > Assets): how tags are used together - areas,
equipment, KPI or report sets (core/assets.py). Like AWS SiteWise / Cognite / Seeq asset trees.
The tags themselves are in the Tags document.

  bar    Add ▾ (Asset..., Sub-asset...), Edit, Delete | Add tags..., Remove | New view
         | filter - fixed buttons, disabled when they do not apply
  left   the asset tree (up to 4 levels; counts include the sub-assets)
  right  the attributes of the selected asset and of its sub-assets: attribute, tag, source,
         asset (path), live value, unit, quality, time

Select an asset: its properties (read-only, Edit...); double-click: its pop-up. Select an
attribute: the properties of its tag. Right-click an asset: Edit, Add sub-asset, Move to,
Duplicate, New view, Add alarms, Delete; an attribute: Open tag, Remove from asset, New view.

Host API (duck-typed, DaqWindow):
  host.project (assets), host.reg_icon(target, name, token), host.recording (bool)
  host.tag_live(tid, idx) / host.color_of(tid, idx) / host.key_label(key) / host.key_source(key)
  host.select_object(ref) / host.select_key(key) / host.open_tag(key)
  host.add_asset(parent) / host.edit_asset(aid) / host.delete_asset(aid) / host.duplicate_asset(aid)
  host.move_asset(aid, parent) / host.asset_add_tags(aid) / host.remove_attributes([(aid, key)])
  host.new_view_from([key], name) / host.alarms_for_asset(aid)
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLineEdit, QMenu, QSplitter,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.core import assets as asm
from nxlogger.ui.icons import swatch
from nxlogger.ui.theme.ui_kit import T, label, make_icon, vsep
from nxlogger.ui.widgets.connectivity import Band

COLS = ["Attribute", "Tag", "Source", "Asset", "Value", "Unit", "Quality", "Updated"]
C_VALUE, C_UNIT, C_QUAL, C_TIME = (COLS.index(c) for c in ("Value", "Unit", "Quality", "Updated"))
HIDDEN_KEY = "assets/hidden_columns"


class AssetsPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._aid: str | None = None                 # the asset shown (None = every asset)
        self._items: dict[str, QTreeWidgetItem] = {}
        self._rows: dict[tuple, QTreeWidgetItem] = {}   # (asset id, tid, idx) -> row
        self._expanded: set[str] = set()

        bar = QFrame(); bar.setObjectName("docBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(2)
        self.btn_add = self._button("Add", "plus", None, "Add an asset, or a sub-asset of the selected one")
        m = QMenu(self.btn_add)
        m.addAction("Asset...", lambda: self.host.add_asset(None))
        self.act_sub = m.addAction("Sub-asset...", lambda: self._aid and self.host.add_asset(self._aid))
        self.btn_add.setMenu(m); self.btn_add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_edit = self._button("Edit", "pencil", self._edit, "Edit the selected asset (double-click it)")
        self.btn_del = self._button("", "trash", self._delete, "Delete the selected asset and its sub-assets "
                                                                "(the tags stay)")
        self.btn_tags = self._button("Add tags...", "tag", lambda: self._aid and self.host.asset_add_tags(self._aid),
                                     "Add tags of any category to the selected asset")
        self.btn_rm = self._button("Remove", "x", self._remove, "Remove the selected attributes from their asset")
        self.btn_view = self._button("New view", "layout", self._new_view,
                                     "A view with a trend and a table of the selected asset (or the selected rows)")
        for w in (self.btn_add, self.btn_edit, self.btn_del, None, self.btn_tags, self.btn_rm, None, self.btn_view):
            bl.addWidget(vsep() if w is None else w)
        bl.addStretch(1)
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Search attributes")
        self.filter.setClearButtonEnabled(True); self.filter.setMaximumWidth(220); self.filter.setMinimumWidth(120)
        self.filter.textChanged.connect(lambda _: self._apply_filter())
        bl.addWidget(self.filter)

        self.band = Band()
        self.band.set("Assets", "boxes", "How tags are used together: areas, equipment, KPI and report sets")
        self.tree = QTreeWidget(); self.tree.setObjectName("navTree")
        self.tree.setHeaderHidden(True); self.tree.setIndentation(14)
        self.tree.setProperty("plainSel", True)
        self.tree.currentItemChanged.connect(lambda cur, _p: self._asset_selected(cur))
        self.tree.itemDoubleClicked.connect(lambda it, _c: self._edit())
        self.tree.itemExpanded.connect(lambda it: self._expanded.add(it.data(0, Qt.ItemDataRole.UserRole)))
        self.tree.itemCollapsed.connect(lambda it: self._expanded.discard(it.data(0, Qt.ItemDataRole.UserRole)))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._asset_menu)
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
    def current(self) -> str | None:
        return self._aid

    # ---------------------------------------------------------------- the tree
    def refresh(self, select: str | None | bool = False):
        """Model changed: rebuild. select: an asset id to show (None = every asset), False = keep."""
        assets = self.host.project.assets
        if select is not False:
            self._aid = select
        if self._aid is not None and not any(a["id"] == self._aid for a in assets):
            self._aid = None
        if self._aid:                               # its parents open
            idx = asm.by_id(assets)
            p = idx[self._aid].get("parent")
            while p and p in idx:
                self._expanded.add(p); p = idx[p].get("parent")
            self._expanded.add(self._aid)
        self.tree.blockSignals(True)
        self.tree.clear(); self._items.clear()
        root = QTreeWidgetItem([f"All assets  ({len(asm.rows(assets, None))})"])
        root.setIcon(0, make_icon("list", T["text"]))
        root.setData(0, Qt.ItemDataRole.UserRole, None)
        root.setToolTip(0, "Every attribute of every asset")
        self.tree.addTopLevelItem(root)

        def build(parent_item, parent_id):
            for a in asm.children(assets, parent_id):
                n = len(asm.keys(assets, a["id"]))
                it = QTreeWidgetItem([f"{a['name']}  ({n})"])
                it.setIcon(0, swatch(a["color"]) if a.get("color") else make_icon("boxes", T["muted"]))
                it.setData(0, Qt.ItemDataRole.UserRole, a["id"])
                it.setToolTip(0, asm.path(assets, a["id"]) + (f"\n{a['description']}" if a.get("description") else ""))
                (parent_item.addChild if parent_item is not None else self.tree.addTopLevelItem)(it)
                self._items[a["id"]] = it
                build(it, a["id"])
                it.setExpanded(a["id"] in self._expanded)
        build(None, None)
        cur = self._items.get(self._aid) if self._aid else root
        self.tree.setCurrentItem(cur)
        self.tree.blockSignals(False)
        self._fill()

    def _asset_selected(self, it):
        if it is None:
            return
        aid = it.data(0, Qt.ItemDataRole.UserRole)
        if aid != self._aid:
            self._aid = aid
            QTimer.singleShot(0, self._fill)           # not inside the tree's own signal
        self.host.select_object(("asset", aid) if aid else None)

    def activate(self):
        self.host.select_object(("asset", self._aid) if self._aid else None)

    # ---------------------------------------------------------------- the table
    def _fill(self):
        assets = self.host.project.assets
        self.table.setSortingEnabled(False)
        self.table.clear(); self._rows.clear()
        for aid, x in asm.rows(assets, self._aid):
            key = tuple(x["key"])
            tag = self.host.key_label(key)
            it = QTreeWidgetItem([x.get("name") or tag.split(" · ")[-1], tag, self.host.key_source(key),
                                  asm.path(assets, aid), "", "", "", ""])
            it.setData(0, Qt.ItemDataRole.UserRole, (aid, *key))
            it.setIcon(0, swatch(self.host.color_of(*key)))
            it.setTextAlignment(C_VALUE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if tag.startswith("(deleted"):
                it.setForeground(1, QBrush(QColor(T["bad"])))
            self.table.addTopLevelItem(it)
            self._rows[(aid, *key)] = it
        self.table.setSortingEnabled(True)
        self._apply_filter()
        self.update_live()
        where = asm.path(assets, self._aid) if self._aid else "every asset"
        self.hint.setText("No assets - Add > Asset..., then Add tags... (or right-click tags in Tags > Add to asset)"
                          if not assets else
                          f"{len(self._rows)} attribute(s) in {where} · a tag can be in several assets · "
                          "double-click an attribute to open its tag")
        self._update_buttons()

    def update_live(self):
        bad, ok, muted = QBrush(QColor(T["bad"])), QBrush(QColor(T["ok"])), QBrush(QColor(T["muted"]))
        for (aid, tid, idx), it in self._rows.items():
            val, qual, good, stamp = self.host.tag_live(tid, idx)
            unit = self.host.last_value(tid, idx)[1] if hasattr(self.host, "last_value") else ""
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
    def _asset_menu(self, pos):
        it = self.tree.itemAt(pos)
        aid = it.data(0, Qt.ItemDataRole.UserRole) if it is not None else None
        if not aid:
            return
        self.tree.setCurrentItem(it)
        assets = self.host.project.assets
        m = QMenu(self)
        m.addAction("Edit...", lambda: self.host.edit_asset(aid))
        m.addAction("Add sub-asset...", lambda: self.host.add_asset(aid)).setEnabled(
            asm.depth(assets, aid) < asm.MAX_DEPTH)
        m.addAction("Add tags...", lambda: self.host.asset_add_tags(aid))
        mv = m.addMenu("Move to")
        mv.addAction("(top level)", lambda: self.host.move_asset(aid, None)).setEnabled(
            asm.by_id(assets)[aid].get("parent") is not None)
        for a in sorted(assets, key=lambda x: asm.path(assets, x["id"]).lower()):
            if a["id"] != asm.by_id(assets)[aid].get("parent"):
                act = mv.addAction(asm.path(assets, a["id"]), lambda p=a["id"]: self.host.move_asset(aid, p))
                act.setEnabled(asm.can_move(assets, aid, a["id"]) == "")
        m.addAction("Duplicate", lambda: self.host.duplicate_asset(aid))
        m.addSeparator()
        m.addAction("New view", lambda: self.host.new_view_from(asm.keys(assets, aid), asm.path(assets, aid)))
        m.addAction("Add alarms for its attributes...", lambda: self.host.alarms_for_asset(aid))
        m.addSeparator()
        m.addAction("Delete...", lambda: self.host.delete_asset(aid))
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
        m.addAction(f"Remove from asset{'' if len(rows) == 1 else f' ({len(rows)})'}", self._remove)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _edit(self):
        if self._aid:
            self.host.edit_asset(self._aid)

    def _delete(self):
        if self._aid:
            self.host.delete_asset(self._aid)

    def _remove(self):
        rows = self._selected_rows()
        if rows:
            self.host.remove_attributes([(r[0], r[1:]) for r in rows])

    def _new_view(self):
        rows = self._selected_rows()
        assets = self.host.project.assets
        if rows:
            self.host.new_view_from([r[1:] for r in rows])
        elif self._aid:
            self.host.new_view_from(asm.keys(assets, self._aid), asm.path(assets, self._aid))

    def _update_buttons(self):
        has = self._aid is not None
        rows = bool(self._selected_rows())
        assets = self.host.project.assets
        self.act_sub.setEnabled(has and asm.depth(assets, self._aid) < asm.MAX_DEPTH)
        self.btn_edit.setEnabled(has)
        self.btn_del.setEnabled(has)
        self.btn_tags.setEnabled(has)
        self.btn_rm.setEnabled(rows)
        self.btn_view.setEnabled(rows or (has and bool(asm.keys(assets, self._aid))))
