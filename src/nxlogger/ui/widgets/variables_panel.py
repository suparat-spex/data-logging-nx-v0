"""The Variables document: calculated channels (formula, statistic, signal processing)."""
from __future__ import annotations

import copy
import uuid

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.core import variables as vr
from nxlogger.core.channel_math import identifier
from nxlogger.ui.dialogs.variable_dialog import VariableDialog
from nxlogger.ui.icons import swatch
from nxlogger.ui.theme.ui_kit import T, label

COLS = ["Variable", "Type", "Definition", "Unit", "Value", "Logged", "Status"]
C_NAME, C_TYPE, C_DEF, C_UNIT, C_VAL, C_LOG, C_STATE = range(len(COLS))


class VariablesPage(QWidget):
    """
    host API:
      host.project.variables, host.variable_engine, host.channel_choices()
      host.var_color(var) -> colour, host.variables_changed(), host.reg_icon(target, name, token)
      host.recorder (None when not recording: definitions are locked while recording)
    """

    def __init__(self, host):
        super().__init__()
        self.host = host
        bar = QFrame(); bar.setObjectName("docBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(4)
        self.summary = QLabel(); self.summary.setTextFormat(Qt.TextFormat.RichText)
        bl.addWidget(self.summary); bl.addStretch(1)

        sub = QFrame(); sub.setObjectName("subBar")
        sl = QHBoxLayout(sub); sl.setContentsMargins(8, 4, 8, 4); sl.setSpacing(4)
        self.btn_add = self._tool("Add variable", "plus", None, sl)
        self.btn_add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(self)
        for k, text in vr.KINDS.items():
            m.addAction(text + "...", lambda kk=k: self.add_variable(kk))
        self.btn_add.setMenu(m)
        self.btns = [self.btn_add,
                     self._tool("Edit", "pencil", lambda: self.edit_variable(), sl),
                     self._tool("Delete", "trash", self.delete_variable, sl),
                     self._tool("Duplicate", "copy", self.duplicate_variable, sl)]
        sl.addStretch(1)
        self.btns += [self._tool("Enable all", "power", lambda: self._enable_all(True), sl),
                      self._tool("Disable all", "power", lambda: self._enable_all(False), sl)]

        self.tree = QTreeWidget(); self.tree.setColumnCount(len(COLS)); self.tree.setHeaderLabels(COLS)
        self.tree.setRootIsDecorated(False); self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        for c, w in enumerate((170, 120, 330, 60, 110, 70)):
            self.tree.header().resizeSection(c, w)
        self.tree.itemDoubleClicked.connect(lambda *_: QTimer.singleShot(0, self.edit_variable))
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.customContextMenuRequested.connect(self._menu)
        hint = label("  Variables are calculated channels: use them in views, alarms and logging like any "
                     "tag. In formulas, tags are named <device>_<tag> (e.g. Simulator_Temperature).",
                     muted=True, small=True)
        hint.setContentsMargins(4, 6, 4, 6); hint.setWordWrap(True)

        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(bar); v.addWidget(sub); v.addWidget(hint); v.addWidget(self.tree, 1)

    def _tool(self, text, icon, slot, layout):
        b = QToolButton(); b.setText(text); b.setToolTip(text)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.host.reg_icon(b, icon)
        if slot:
            b.clicked.connect(slot)
        layout.addWidget(b)
        return b

    # ---- refresh
    def refresh(self):
        labels = {(tid, idx): lbl for tid, idx, lbl, *_ in self.host.channel_choices()}
        sel = {it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()}
        self.tree.blockSignals(True); self.tree.clear()
        muted = QBrush(QColor(T["muted"]))
        for var in self.host.project.variables:
            it = QTreeWidgetItem([var["name"], vr.KINDS.get(var["kind"], var["kind"]),
                                  vr.describe(var, lambda k: labels.get(k, "?").split(" [")[0]),
                                  var.get("unit", ""), "", "", ""])
            it.setData(0, Qt.ItemDataRole.UserRole, var["id"])
            it.setIcon(0, swatch(self.host.var_color(var)))
            it.setToolTip(0, f"Formula name: {identifier(var['name'])}" +
                          (f"\n{var['description']}" if var.get("description") else ""))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(C_NAME, Qt.CheckState.Checked if var["enabled"] else Qt.CheckState.Unchecked)
            it.setCheckState(C_LOG, Qt.CheckState.Checked if var.get("log", True) else Qt.CheckState.Unchecked)
            it.setTextAlignment(C_VAL, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if not var["enabled"]:
                for c in range(len(COLS)):
                    it.setForeground(c, muted)
            self.tree.addTopLevelItem(it)
            it.setSelected(var["id"] in sel)
        if not self.host.project.variables:
            it = QTreeWidgetItem(["No variables yet - click Add variable"])
            it.setFlags(Qt.ItemFlag.NoItemFlags); it.setForeground(0, muted)
            self.tree.addTopLevelItem(it)
        self.tree.blockSignals(False)
        self.update_live(force=True)
        locked = self.host.recorder is not None
        for b in self.btns:
            b.setEnabled(not locked)
        self.btn_add.setToolTip("Stop recording to change variables" if locked else "Add variable")

    def update_live(self, force: bool = False):
        eng = self.host.variable_engine
        vs = self.host.project.variables
        n_on = sum(v["enabled"] for v in vs)
        n_err = sum(1 for v in vs if v["enabled"] and v["id"] in eng.errors)
        self.summary.setText(f"{n_on} of {len(vs)} variables enabled" +
                             (f"&nbsp;&nbsp;·&nbsp;&nbsp;<b style='color:{T['bad']}'>{n_err} with errors</b>"
                              if n_err else ""))
        if not (force or self.isVisible()):
            return
        by_id = {v["id"]: v for v in vs}
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            v = by_id.get(it.data(0, Qt.ItemDataRole.UserRole))
            if v is None:
                continue
            val = eng.values.get(v["id"])
            it.setText(C_VAL, "-" if val is None or val != val else f"{val:.{int(v.get('decimals', 3))}f}")
            if not v["enabled"]:
                state, tok = "Disabled", "muted"
            elif v["id"] in eng.errors:
                state, tok = eng.errors[v["id"]], "bad"
            else:
                state, tok = ("Running" if val is not None else "Waiting for data"), \
                    ("ok" if val is not None else "muted")
            if it.text(C_STATE) != state:
                it.setText(C_STATE, state); it.setToolTip(C_STATE, state)
                it.setForeground(C_STATE, QBrush(QColor(T[tok])))
        self.tree.blockSignals(False)

    # ---- actions
    def _sel(self) -> dict | None:
        it = self.tree.currentItem()
        vid = it.data(0, Qt.ItemDataRole.UserRole) if it else None
        return next((v for v in self.host.project.variables if v["id"] == vid), None)

    def select(self, vid: str):
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, Qt.ItemDataRole.UserRole) == vid:
                self.tree.setCurrentItem(it); self.tree.scrollToItem(it)

    def _unique_name(self, base: str) -> str:
        taken = {identifier(v["name"]).lower() for v in self.host.project.variables}
        n = 1
        while identifier(f"{base}{n}").lower() in taken:
            n += 1
        return f"{base}{n}"

    def add_variable(self, kind: str = "formula", source=None, popup: bool = False):
        """New variable: an editor tab; popup=True for actions inside another form."""
        if self.host.recorder is not None:
            return
        if not popup and hasattr(self.host, "open_editor"):
            self.host.open_editor("variable", None, kind=kind, source=source)
            return
        base = {"formula": "Calc", "statistic": "Stat", "process": "Proc"}[kind]
        v = vr.new_variable(kind, self._unique_name(base))
        ch = self.host.channel_choices()
        if kind != "formula" and ch:
            v["source"] = list(source or ch[0][:2])
        dlg = VariableDialog(self, v, self.host.variable_engine, ch)
        dlg.setWindowTitle(f"Add variable - {vr.KINDS[kind]}")
        if dlg.exec():
            self.host.project.variables.append(dlg.result_variable())
            self.host.variables_changed()
            self.select(v["id"])

    def edit_variable(self, vid: str | None = None):
        v = next((x for x in self.host.project.variables if x["id"] == vid), None) if vid else self._sel()
        if v is None or self.host.recorder is not None:
            return
        if hasattr(self.host, "open_editor"):
            self.host.open_editor("variable", v["id"])
            return
        dlg = VariableDialog(self, v, self.host.variable_engine, self.host.channel_choices())
        if dlg.exec():
            v.clear(); v.update(dlg.result_variable())
            self.host.variables_changed()

    def delete_variable(self):
        ids = {it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()} - {None}
        if not ids or self.host.recorder is not None:
            return
        names = [v["name"] for v in self.host.project.variables if v["id"] in ids]
        if QMessageBox.question(self, "Delete variable", f"Delete {len(names)} variable(s): "
                                f"{', '.join(names[:5])}{' ...' if len(names) > 5 else ''}?\n"
                                "Views, alarms and formulas that use them stop showing data.") \
                != QMessageBox.StandardButton.Yes:
            return
        self.host.project.variables[:] = [v for v in self.host.project.variables if v["id"] not in ids]
        self.host.variables_changed()

    def duplicate_variable(self):
        v = self._sel()
        if v is None or self.host.recorder is not None:
            return
        b = copy.deepcopy(v); b["id"] = uuid.uuid4().hex[:8]; b["name"] = self._unique_name(v["name"] + "_")
        vs = self.host.project.variables
        vs.insert(vs.index(v) + 1, b)
        self.host.variables_changed()
        self.select(b["id"])

    def toggle(self, vid: str):
        v = next((x for x in self.host.project.variables if x["id"] == vid), None)
        if v and self.host.recorder is None:
            v["enabled"] = not v["enabled"]
            self.host.variables_changed()

    def _enable_all(self, on: bool):
        for v in self.host.project.variables:
            v["enabled"] = on
        self.host.variables_changed()

    def _item_changed(self, it, col):
        v = next((x for x in self.host.project.variables
                  if x["id"] == it.data(0, Qt.ItemDataRole.UserRole)), None)
        if v is None or col not in (C_NAME, C_LOG):
            return
        key = "enabled" if col == C_NAME else "log"
        on = it.checkState(col) == Qt.CheckState.Checked
        if self.host.recorder is not None:            # locked while recording: undo the click
            QTimer.singleShot(0, self.refresh)
            return
        if on != bool(v.get(key, True)):
            v[key] = on
            QTimer.singleShot(0, self.host.variables_changed)   # never rebuild inside itemChanged

    def _menu(self, pos):
        it = self.tree.itemAt(pos)
        v = None
        if it is not None and it.data(0, Qt.ItemDataRole.UserRole):
            self.tree.setCurrentItem(it); v = self._sel()
        m = QMenu(self)
        add = m.addMenu("Add variable")
        for k, text in vr.KINDS.items():
            add.addAction(text + "...", lambda kk=k: self.add_variable(kk))
        if v:
            m.addAction("Edit...", lambda: QTimer.singleShot(0, self.edit_variable))
            m.addAction("Disable" if v["enabled"] else "Enable", lambda: self.toggle(v["id"]))
            m.addAction("Duplicate", self.duplicate_variable)
            m.addSeparator()
            m.addAction("Delete", self.delete_variable)
        for a in m.actions():
            a.setEnabled(self.host.recorder is None)
        m.exec(self.tree.viewport().mapToGlobal(pos))
