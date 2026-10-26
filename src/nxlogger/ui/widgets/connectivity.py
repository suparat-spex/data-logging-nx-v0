"""
connectivity.py - the Connectivity document (like FUXA Connections / Genesis64 Connectivity):
the device table only. Tags are managed in the Tags document (tags_page.py).

  bar    add (per connector), edit, duplicate, delete, import... (checked JSON / CSV file),
         export... (tick devices, JSON with tags / CSV device list), columns (show / hide, remembered) -
         fixed buttons, nothing appears or renames
  table  name (state icon), connector, address, scan, tags, status, enabled (check box),
         description - multi-select, sortable, filter

Select a row: its properties in the Properties panel; double-click (or Enter): the device
settings pop-up (the Enabled check box is an indicator, not a control); right-click a row:
Edit device, Enable / Disable, Duplicate, Export, Delete (Enable / Disable, Export and Delete
act on every selected row).

Host API (duck-typed, DaqWindow):
  host.project, host.reg_icon(target, name, token), host.recording (bool)
  host.device_state(did) -> (state, text)
  host.select_object(ref)            ref: ("device", did) | None
  host.add_device(driver) / host.edit_device(did) / host.duplicate_device(did)
  host.delete_devices([did]) / host.set_devices_enabled([did], on) -> bool
  host.import_devices() / host.export_devices_dialog([did])     (pop-ups)
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QLineEdit, QMenu,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.core.tags import DRIVERS, PLANNED, driver_label
from nxlogger.ui.theme.ui_kit import T, label, make_icon, vsep

COLS = ["Name", "Connector", "Address", "Scan", "Tags", "Status", "Enabled", "Description"]
C_STATUS, C_EN = COLS.index("Status"), COLS.index("Enabled")
HIDDEN_KEY = "connectivity/hidden_columns"        # QSettings: column names the user hid
STATE_TOKEN = {"online": "ok", "connecting": "warn", "error": "bad", "stopped": "muted"}


def dot(token: str) -> QIcon:
    pm = QPixmap(20, 20); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(T[token]))
    p.drawEllipse(5, 5, 10, 10); p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


class ConnectivityPage(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self._rows: dict[str, QTreeWidgetItem] = {}
        self._last: list[str] = []                   # selection to bring back when the tab returns

        bar = QFrame(); bar.setObjectName("docBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(2)
        add = self._button("Add", "plus", None, "Add a device: choose the connector")
        m = QMenu(add)
        for k, d in DRIVERS.items():                   # text only, like every menu (no icon column)
            m.addAction(d["label"] + "...", lambda kk=k: host.add_device(kk))
        add.setMenu(m); add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
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

        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(bar); v.addWidget(self.table, 1); v.addWidget(self.hint)

    def _button(self, text, icon, slot, tip) -> QToolButton:
        b = QToolButton(); b.setToolTip(tip)
        if text:
            b.setText(text); b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.host.reg_icon(b, icon)
        if slot:
            b.clicked.connect(slot)
        return b

    # ---------------------------------------------------------------- table
    def refresh(self, select: list[str] | None = None):
        """Model changed: rebuild the table (keeps the selection unless `select` is given)."""
        keep = self.selected() if select is None else list(select)
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.clear(); self._rows.clear()
        muted = QBrush(QColor(T["muted"]))
        for d in self.host.project.devices:
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
        """Select one device (project tree, banners) and show its properties."""
        if did not in self._rows:
            self.refresh([did])
        it = self._rows.get(did)
        if it is not None:
            self.table.clearSelection()
            it.setSelected(True)
            self.table.setCurrentItem(it)
            self.table.scrollToItem(it)

    def update_states(self, force: bool = False):
        """Status column and state icon (twice a second while visible, and on state changes)."""
        bad = QBrush(QColor(T["bad"]))
        for d in self.host.project.devices:
            it = self._rows.get(d.id)
            if it is None:
                continue
            state, text = self.host.device_state(d.id)
            tok = STATE_TOKEN.get(state, "muted") if d.enabled else "muted"
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
        self._update_hint()
        self._update_buttons()

    def _update_hint(self):
        devs = self.host.project.devices
        if not devs:
            self.hint.setText("No devices - Add ▾: Simulation, Modbus TCP / RTU, MQTT or OPC UA, or Import...")
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
            keep = [d for d in self._last if d in self._rows]
            first = self.table.topLevelItem(0)
            if not keep and first is not None:
                keep = [first.data(0, Qt.ItemDataRole.UserRole)]
            self.table.blockSignals(True)
            for did in keep:
                self._rows[did].setSelected(True)
            if keep:
                self.table.setCurrentItem(self._rows[keep[0]], 0,
                                          self.table.selectionModel().SelectionFlag.NoUpdate)
            self.table.blockSignals(False)
        self._selected()

    def _menu(self, pos):
        it = self.table.itemAt(pos)
        if it is not None and not it.isSelected():
            self.table.clearSelection(); it.setSelected(True)
        self.build_menu(self._devices() if it is not None else []).exec(self.table.viewport().mapToGlobal(pos))

    def build_menu(self, devs) -> QMenu:
        """Right-click menu: on rows the device actions, on empty space add / import / export."""
        rec = self.host.recording
        m = QMenu(self)
        if devs:                                     # edit, enable / disable, duplicate, export, delete
            one, n = len(devs) == 1, len(devs)
            what = "device" if one else f"{n} devices"
            off = all(x.enabled for x in devs)           # all on: Disable, else Enable
            items = [("Edit device...", self._edit, one),
                     (f"{'Disable' if off else 'Enable'} {what}", self._toggle_enabled, not rec),
                     ("Duplicate", self._duplicate, one and not rec),
                     None,
                     ("Export...", lambda: self.host.export_devices_dialog([x.id for x in devs]), True),
                     None,
                     (f"Delete {what}", self._delete, not rec)]
            for item in items:
                if item is None:
                    m.addSeparator(); continue
                text, slot, on = item
                m.addAction(text, slot).setEnabled(on)
        else:
            add = m.addMenu("Add device")
            for k, dd in DRIVERS.items():
                add.addAction(dd["label"] + "...", lambda kk=k: self.host.add_device(kk))
            add.setEnabled(not rec)
            m.addSeparator()
            m.addAction("Import devices...", self.host.import_devices).setEnabled(not rec)
            m.addAction("Export devices...", lambda: self.host.export_devices_dialog([])).setEnabled(
                bool(self.host.project.devices))
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
