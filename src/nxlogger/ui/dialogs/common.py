"""Small helpers shared by the dialogs: spin box, channel tree, colour button, form pages."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QDoubleSpinBox, QFormLayout, QLineEdit, QMenu,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger.ui.icons import swatch


def spin(val=0.0, lo=-1e9, hi=1e9, dec=4) -> QDoubleSpinBox:
    s = QDoubleSpinBox(minimum=lo, maximum=hi, decimals=dec)
    s.setValue(float(val))
    s.setAlignment(Qt.AlignmentFlag.AlignRight)
    return s


def form_page() -> tuple[QWidget, QFormLayout]:
    """A tab page with a form layout (same spacing as the Channel dialog)."""
    w = QWidget()
    f = QFormLayout(w)
    f.setSpacing(8)
    return w, f


def group_of(label: str) -> str:
    """'Simulator · CH1 [V]' -> 'Simulator' (channel choices are labelled '<group> · <name>')."""
    return label.split(" · ", 1)[0] if " · " in label else ""


class ChannelPicker(QWidget):
    """
    Filter box + tree of the channel choices, grouped by topic (and "Variables").
    multi=True: check boxes (groups are tri-state); multi=False: pick one row.
    choices = [(topic_id, index, label, colour, unit)]
    """
    changed = Signal()

    def __init__(self, choices, chosen=(), multi: bool = True, parent=None):
        super().__init__(parent)
        self.multi = multi
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Filter channels")
        self.filter.setClearButtonEnabled(True)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumHeight(150)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(4)
        v.addWidget(self.filter); v.addWidget(self.tree, 1)
        chosen = {tuple(c) for c in chosen if c}
        groups: dict[str, QTreeWidgetItem] = {}
        self.leaves: list[QTreeWidgetItem] = []
        self.tree.blockSignals(True)
        for tid, idx, lbl, color, _ in choices:
            g = group_of(lbl)
            parent_it = groups.get(g)
            if parent_it is None:
                parent_it = groups[g] = QTreeWidgetItem([g or "Channels"])
                if multi:
                    parent_it.setFlags(parent_it.flags() | Qt.ItemFlag.ItemIsAutoTristate
                                       | Qt.ItemFlag.ItemIsUserCheckable)
                else:
                    parent_it.setFlags(parent_it.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.tree.addTopLevelItem(parent_it)
            it = QTreeWidgetItem([lbl.split(" · ", 1)[-1]])
            it.setIcon(0, swatch(color))
            it.setToolTip(0, lbl)
            it.setData(0, Qt.ItemDataRole.UserRole, [tid, idx])
            if multi:
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Checked if (tid, idx) in chosen else Qt.CheckState.Unchecked)
            parent_it.addChild(it)
            self.leaves.append(it)
            if not multi and (tid, idx) in chosen:
                self.tree.setCurrentItem(it)
        self.tree.blockSignals(False)
        self.tree.expandAll()
        self.filter.textChanged.connect(self._filter)
        self.tree.itemChanged.connect(lambda *_: self.changed.emit())
        self.tree.currentItemChanged.connect(lambda *_: self.changed.emit())

    def _filter(self, text: str):
        text = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            any_shown = False
            for k in range(g.childCount()):
                c = g.child(k)
                show = not text or text in c.toolTip(0).lower()
                c.setHidden(not show)
                any_shown |= show
            g.setHidden(not any_shown)

    def checked(self) -> list[list]:
        return [it.data(0, Qt.ItemDataRole.UserRole) for it in self.leaves
                if it.checkState(0) == Qt.CheckState.Checked]

    def current(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it is not None else None


class ColorButton(QToolButton):
    """Colour choice with "Auto (theme)" = "" so the default follows dark / light mode."""
    changed = Signal(str)

    def __init__(self, color: str = "", auto_text: str = "Auto (theme)", parent=None):
        super().__init__(parent)
        self.auto_text = auto_text
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.setMinimumWidth(170)
        m = QMenu(self)
        m.addAction(auto_text, lambda: self.set_color(""))
        m.addAction("Choose colour...", self._pick)
        self.setMenu(m)
        self.clicked.connect(self._pick)
        self.set_color(color or "")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color or "#808080"), self, "Colour",
                                  QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            self.set_color(c.name(QColor.NameFormat.HexArgb) if c.alpha() < 255 else c.name())

    def set_color(self, c: str):
        self._color = c
        if c:
            self.setIcon(swatch(c, 14)); self.setText(c)
        else:
            self.setIcon(swatch("#00000000", 14)); self.setText(self.auto_text)
        self.changed.emit(c)

    def color(self) -> str:
        return self._color
