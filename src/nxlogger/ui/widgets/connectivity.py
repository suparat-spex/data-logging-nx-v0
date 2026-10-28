"""
connectivity.py - the Connectivity document (like FUXA Connections / Genesis64 Connectivity):
the devices only. Tags are managed in the Tags document (tags_page.py).

Devices come in three categories (core.tags.CONN_CATEGORIES, also the Project tree's
Connectivity nodes). The page shows one category at a time, like the Tags document:
  Protocols     Modbus TCP / RTU, OPC UA, MQTT, Simulation (OPC DA planned)
  Database      MySQL, PostgreSQL, InfluxDB                                   (planned)
  Web Services  REST / HTTP, Webhook, WebSocket, Firebase, MCP Server         (planned)

  bar    add (the connectors of the category), edit, duplicate, delete, import... (checked JSON /
         CSV file), export... (tick devices, JSON with tags / CSV device list), columns (show /
         hide, remembered) - fixed buttons, nothing appears or renames
  left   the category as a band, below it: All connections, one row per connector (count, icon in
         the worst state colour of its devices; muted when it has none or is planned)
  right  device table: name (state icon), connector, address, scan, tags, status, enabled
         (check box), description - multi-select, sortable, filter

Select a row: its properties in the Properties panel; double-click (or Enter): the device
settings pop-up (the Enabled check box is an indicator, not a control). Only device rows have a
right-click menu: Edit, Open tags, Connect / Disconnect, Enable / Disable, Duplicate, Export,
Delete (all but Edit / Open tags / Duplicate act on every selected row). The list and empty
table space have none - the toolbar has everything.

Host API (duck-typed, DaqWindow):
  host.project, host.reg_icon(target, name, token), host.recording (bool)
  host.device_state(did) -> (state, text)
  host.select_object(ref)            ref: ("device", did) | None
  host.add_device(driver) / host.edit_device(did) / host.duplicate_device(did) / host.open_tags(did)
  host.start_device(did) / host.stop_device(did)
  host.delete_devices([did]) / host.set_devices_enabled([did], on) -> bool
  host.import_devices() / host.export_devices_dialog([did])     (pop-ups)
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QSplitter,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.core.tags import (CONN_CATEGORIES, CONNECTORS_PLANNED, DRIVERS, PLANNED, category_drivers,
                                driver_category, driver_label)
from nxlogger.ui.theme.app_style import blend
from nxlogger.ui.theme.ui_kit import T, label, make_icon, vsep

COLS = ["Name", "Connector", "Address", "Scan", "Tags", "Status", "Enabled", "Description"]
C_STATUS, C_EN = COLS.index("Status"), COLS.index("Enabled")
HIDDEN_KEY = "connectivity/hidden_columns"        # QSettings: column names the user hid
STATE_TOKEN = {"online": "ok", "connecting": "warn", "error": "bad", "stopped": "muted"}
WORST = ("bad", "warn", "ok", "muted")            # the connector row shows the first of these its devices have
CCAT = {c[0]: c for c in CONN_CATEGORIES}
NOUN = {"protocols": ("device", "devices"), "database": ("database", "databases"),   # Properties / hints
        "web": ("web service", "web services")}


def dot(token: str) -> QIcon:
    pm = QPixmap(20, 20); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(T[token]))
    p.drawEllipse(5, 5, 10, 10); p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


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


class ConnectivityPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._cat = "protocols"                      # the category shown (Project tree > Connectivity > ...)
        self._sel: tuple = ("all",)                  # in it: ("all",) / ("driver", key) / ("planned", label)
        self._rows: dict[str, QTreeWidgetItem] = {}
        self._drv_items: dict[str, QTreeWidgetItem] = {}
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
        self.btn_imp = self._button("Import...", "download", lambda: host.import_devices(),
                                    "Import devices from JSON (with tags) or CSV")
        self.btn_exp = self._button("Export...", "upload", lambda: host.export_devices_dialog(self.selected()),
                                    "Export devices: tick them, JSON (with tags) or CSV")
        for w in (add, self.btn_edit, self.btn_dup, self.btn_del, None, self.btn_imp, self.btn_exp):
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

        self.band = Band()
        self.clist = QTreeWidget(); self.clist.setObjectName("navTree")
        self.clist.setHeaderHidden(True); self.clist.setIndentation(14)    # same left margin as the Tags list
        self.clist.setProperty("plainSel", True)        # neutral selection, like the Tags list
        self.clist.currentItemChanged.connect(lambda cur, _p: self._list_selected(cur))
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
        for c, w in enumerate((160, 120, 210, 64, 46, 130, 74)):
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
        self.hint = label("", muted=True, small=True)
        self.hint.setContentsMargins(8, 2, 8, 2)

        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        rv.addWidget(self.table, 1); rv.addWidget(self.hint)
        split = QSplitter(); split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(1, 1); split.setSizes([210, 820]); split.setChildrenCollapsible(False)
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
    def category(self) -> str:
        return self._cat

    # ---------------------------------------------------------------- connector list
    def show_category(self, cat: str):
        """Project tree > Connectivity > <category>: show it (all its devices)."""
        if cat in CCAT and (cat, ("all",)) != (self._cat, self._sel):
            self._cat, self._sel = cat, ("all",)
            self._switched()

    def _cat_devices(self):
        return [d for d in self.host.project.devices if driver_category(d.driver) == self._cat]

    def _fill_list(self):
        key, name, icon, tip = CCAT[self._cat]
        self.band.set(name, icon, tip)
        devs = self._cat_devices()
        self.clist.blockSignals(True)
        self.clist.clear(); self._drv_items.clear()
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
        add(f"All connections  ({len(devs)})", ("all",), make_icon("list", T["text"]), f"Every {name} connection")
        for k in category_drivers(self._cat):
            n = sum(1 for d in devs if d.driver == k)
            self._drv_items[k] = add(f"{driver_label(k)}  ({n})", ("driver", k),
                                     make_icon(DRIVERS[k]["icon"], T["muted"]), DRIVERS[k]["note"],
                                     dim=not n or k in PLANNED)
        for text, ic in CONNECTORS_PLANNED.get(self._cat, []):
            add(f"{text} (planned)", ("planned", text), make_icon(ic, T["muted"]),
                f"{text}: not available yet", dim=True)
        cur = items.get(self._sel) or items[("all",)]
        self._sel = tuple(cur.data(0, Qt.ItemDataRole.UserRole))
        self.clist.setCurrentItem(cur)
        self.clist.blockSignals(False)

    def _list_selected(self, it):
        if it is None:
            return
        sel = tuple(it.data(0, Qt.ItemDataRole.UserRole))
        if sel != self._sel:
            self._sel = sel
            QTimer.singleShot(0, self._switched)         # not inside the list's own signal

    def _switched(self):
        """Another category / connector: other rows, so no selection (Properties shows the empty
        state) - a hidden selection would edit what the table does not show."""
        self._last = []
        self.refresh([])
        self._selected()

    def _fill_add_menu(self):
        """Add ▾: the connectors of the shown category (planned ones disabled)."""
        m = self.add_menu
        m.clear()
        for k in category_drivers(self._cat):           # text only, like every menu (no icon column)
            m.addAction(DRIVERS[k]["label"] + "...", lambda kk=k: self.host.add_device(kk))
        for text, _ic in CONNECTORS_PLANNED.get(self._cat, []):
            m.addAction(f"{text} (planned)").setEnabled(False)

    # ---------------------------------------------------------------- table
    def refresh(self, select: list[str] | None = None):
        """Model changed: rebuild the list and the table (keeps the selection unless `select` is given)."""
        keep = self.selected() if select is None else list(select)
        self._fill_list()
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.clear(); self._rows.clear()
        muted = QBrush(QColor(T["muted"]))
        for d in self._cat_devices():
            if self._sel[0] == "planned" or (self._sel[0] == "driver" and d.driver != self._sel[1]):
                continue
            it = QTreeWidgetItem([d.name, driver_label(d.driver), d.topic,
                                  "-" if d.wave else f"{d.scan_ms} ms", str(len(d.tags)), "", "",
                                  d.description])
            it.setData(0, Qt.ItemDataRole.UserRole, d.id)
            it.setCheckState(C_EN, Qt.CheckState.Checked if d.enabled else Qt.CheckState.Unchecked)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)    # indicator only: set in the pop-up
            it.setText(C_EN, "Yes" if d.enabled else "No")
            it.setToolTip(0, DRIVERS[d.driver]["note"])
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
        self.update_states(force=True)

    def select(self, did: str):
        """Select one device (project tree, banners) and show its properties: switches to its
        category, and to All connections unless its connector is the one shown."""
        dev = self.host.project.device(did)
        if dev is None:
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
        """Status column, state icons and the connector icons (twice a second while visible,
        and on state changes)."""
        bad = QBrush(QColor(T["bad"]))
        worst: dict[str, str] = {}
        for d in self._cat_devices():
            state, text = self.host.device_state(d.id)
            tok = STATE_TOKEN.get(state, "muted") if d.enabled else "muted"
            worst[d.driver] = min(worst.get(d.driver, "muted"), tok, key=WORST.index)
            it = self._rows.get(d.id)
            if it is None:
                continue
            status = "Disabled" if not d.enabled else ("Planned" if d.driver in PLANNED else state.capitalize())
            key = (tok, status, text)
            if not force and it.data(1, Qt.ItemDataRole.UserRole) == key:
                continue
            it.setData(1, Qt.ItemDataRole.UserRole, key)
            it.setIcon(0, make_icon(DRIVERS[d.driver]["icon"], T[tok if tok != "muted" else "text"]))
            it.setIcon(C_STATUS, dot(tok))
            it.setText(C_STATUS, status + (f": {text}" if text and state == "error" else ""))
            it.setToolTip(C_STATUS, f"{state.capitalize()}" + (f": {text}" if text else ""))
            it.setForeground(C_STATUS, bad if tok == "bad" else it.foreground(1))
        for k, it in self._drv_items.items():
            has = k in worst
            tok = worst.get(k, "muted")
            if force or it.data(1, Qt.ItemDataRole.UserRole) != (has, tok):
                it.setData(1, Qt.ItemDataRole.UserRole, (has, tok))     # muted: none, or all stopped / off
                it.setIcon(0, make_icon(DRIVERS[k]["icon"], T[tok if tok != "muted" else ("text" if has else "muted")]))
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
                          "double-click a device to edit it, right-click for more")

    def _apply_filter(self):
        text = self.filter.text().strip().lower()
        for it in self._rows.values():
            it.setHidden(bool(text) and not any(text in it.text(c).lower() for c in (0, 1, 2, 7)))

    def selected(self) -> list[str]:
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
        if not self.selected():
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
        if not self._rows:
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
        items = [("Edit device...", self._edit, one),
                 ("Open tags", lambda: self.host.open_tags(devs[0].id), one),
                 None,
                 (("Disconnect" if run else "Connect") + cnt, lambda: self._connect(devs, not run), run or can_run),
                 (f"{'Disable' if off else 'Enable'} {what}", self._toggle_enabled, not rec),
                 None,
                 ("Duplicate", self._duplicate, one and not rec),
                 ("Export...", lambda: self.host.export_devices_dialog([x.id for x in devs]), True),
                 None,
                 (f"Delete {what}...", self._delete, not rec)]
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
            if on and self.host.device_state(d.id)[0] == "stopped":
                self.host.start_device(d.id)
            elif not on:
                self.host.stop_device(d.id)

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
