"""Small helpers shared by the dialogs: spin box, channel tree, colour button, form pages,
schema forms (driver settings / tag addresses), and the pop-up standard: confirm(),
ask_unsaved(), ActionDialog (see docs/ui-guidelines.md)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QMessageBox, QSpinBox, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from nxlogger.ui.icons import swatch
from nxlogger.ui.theme.ui_kit import T, label, make_icon


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
    """'Simulator · Temp [V]' -> 'Simulator' (choices are labelled '<device> · <tag>')."""
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


def field_editor(f: dict, v, on_change) -> QWidget:
    """Editor for one schema field (core/tags.F): str, password, int, float, bool, choice;
    on_change() runs on every edit. Read it back with field_value(kind, widget)."""
    kind, extra = f["type"], f.get("extra")
    if kind in ("str", "password"):
        w = QLineEdit("" if v is None else str(v))
        if kind == "password":
            w.setEchoMode(QLineEdit.EchoMode.Password)
        w.textChanged.connect(lambda _: on_change())
    elif kind == "int":
        lo, hi = extra if isinstance(extra, tuple) else (-2_000_000_000, 2_000_000_000)
        w = QSpinBox(minimum=int(lo), maximum=int(hi)); w.setValue(int(v or 0))
        w.valueChanged.connect(lambda _: on_change())
    elif kind == "float":
        lo, hi = extra if isinstance(extra, tuple) else (-1e12, 1e12)
        w = spin(float(v or 0), lo, hi, 4)
        w.valueChanged.connect(lambda _: on_change())
    elif kind == "bool":
        w = QCheckBox(); w.setChecked(bool(v))
        w.toggled.connect(lambda _: on_change())
    else:                                            # choice
        w = QComboBox()
        items = list(extra.items()) if isinstance(extra, dict) else [(x, str(x)) for x in extra or []]
        for val, text in items:
            w.addItem(text, val)
        w.setCurrentIndex(max(0, next((i for i, (val, _) in enumerate(items) if str(val) == str(v)), 0)))
        w.currentIndexChanged.connect(lambda _: on_change())
    if f.get("tip"):
        w.setToolTip(f["tip"])
    return w


def field_value(kind: str, w: QWidget):
    if kind in ("str", "password"):
        return w.text().strip()
    if kind in ("int", "float"):
        return w.value()
    if kind == "bool":
        return w.isChecked()
    if kind == "editable":                           # editable combo holding free text (serial port)
        return w.currentText().strip()
    return w.currentData()


class SchemaForm(QWidget):
    """Form built from a field schema (core/tags.F): str, password, int, float, bool, choice.
    values() -> {key: value} typed like the defaults."""
    changed = Signal()

    def __init__(self, schema, values: dict | None = None, parent=None):
        super().__init__(parent)
        values = values or {}
        self.form = QFormLayout(self); self.form.setContentsMargins(0, 0, 0, 0); self.form.setSpacing(8)
        self.w: dict[str, tuple[str, QWidget]] = {}
        for f in schema or []:
            w = field_editor(f, values.get(f["key"], f["default"]), self.changed.emit)
            self.form.addRow(f["label"], w)
            self.w[f["key"]] = (f["type"], w)

    def values(self) -> dict:
        return {k: field_value(kind, w) for k, (kind, w) in self.w.items()}

    def set_visible(self, key: str, on: bool):
        if key in self.w:
            self.form.setRowVisible(self.w[key][1], on)


# ====================================================================== pop-up standard
# Pop-ups are for actions, never for editing a saved item (that is an editor tab, see
# docs/ui-guidelines.md). Every pop-up looks the same: title, one line saying what happens,
# the form, a message line, and [Cancel] [<Verb>] - the primary button names the action.
def confirm(parent, title: str, text: str, verb: str = "Delete", detail: str = "",
            danger: bool = True) -> bool:
    """Yes / no question with an explicit verb button ("Delete 3 tags"). Destructive ones
    default to Cancel, so Enter never deletes by accident."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    ok = box.addButton(verb, QMessageBox.ButtonRole.DestructiveRole if danger else QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(cancel if danger else ok)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is ok


def ask_unsaved(parent, title: str, text: str, keep: str = "Save") -> str:
    """Unsaved changes: 'keep' (Save / Apply), 'discard' or 'cancel'."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(text)
    k = box.addButton(keep, QMessageBox.ButtonRole.AcceptRole)
    d = box.addButton("Don't " + keep.lower(), QMessageBox.ButtonRole.DestructiveRole)
    c = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(k); box.setEscapeButton(c)
    box.exec()
    return {k: "keep", d: "discard"}.get(box.clickedButton(), "cancel")


def dialog_header(icon: str, title: str, subtitle: str = ""):
    """The header band of the larger pop-ups (device settings, import / export): a 32 px icon
    in the accent colour, a bold title and a muted line. -> (frame, icon label, title, subtitle)."""
    head = QFrame(); head.setObjectName("dlgHeader")
    hh = QHBoxLayout(head); hh.setContentsMargins(20, 14, 20, 14); hh.setSpacing(14)
    ic = QLabel(); ic.setFixedSize(32, 32)
    ic.setPixmap(make_icon(icon, T["accent"]).pixmap(32, 32))
    hh.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
    hv = QVBoxLayout(); hv.setSpacing(2)
    t = QLabel(title); t.setObjectName("dlgTitle")
    sub = label(subtitle, muted=True, small=True); sub.setWordWrap(True)
    hv.addWidget(t); hv.addWidget(sub)
    hh.addLayout(hv, 1)
    return head, ic, t, sub


class ActionDialog(QDialog):
    """Base of the action pop-ups: a one-line intro, a form (self.form), a message line and
    [Cancel] [verb]. Subclasses fill self.form and override validate() -> "" or a message.
    With `icon` it gets the header / footer bands of the device dialog (intro = header line)."""

    def __init__(self, parent, title: str, intro: str, verb: str, width: int = 440, icon: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.buttons = QDialogButtonBox()
        self.ok = self.buttons.addButton(verb, QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.ok.setProperty("primary", True); self.ok.setDefault(True)
        self.buttons.accepted.connect(self._ok); self.buttons.rejected.connect(self.reject)
        self.err = QLabel(); self.err.setObjectName("statusMsg"); self.err.setWordWrap(True)
        self.err.setProperty("level", "bad"); self.err.hide()
        self.form = QFormLayout(); self.form.setSpacing(8)
        if icon:                                     # header | body | footer, like the device dialog
            v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
            v.addWidget(dialog_header(icon, title, intro)[0])
            body = QVBoxLayout(); body.setContentsMargins(20, 14, 20, 14); body.setSpacing(10)
            body.addLayout(self.form); body.addWidget(self.err); body.addStretch(1)
            v.addLayout(body, 1)
            foot = QFrame(); foot.setObjectName("dlgFooter")
            fh = QHBoxLayout(foot); fh.setContentsMargins(20, 10, 20, 10)
            fh.addWidget(self.buttons)
            v.addWidget(foot)
            return
        v = QVBoxLayout(self); v.setSpacing(10)
        if intro:
            lbl = label(intro, muted=True, small=True); lbl.setWordWrap(True)
            v.addWidget(lbl)
        v.addLayout(self.form)
        v.addWidget(self.err)
        v.addStretch(1)
        v.addWidget(self.buttons)

    def validate(self) -> str:
        return ""

    def show_error(self, text: str):
        self.err.setText(text); self.err.setVisible(bool(text))

    def _ok(self):
        msg = self.validate()
        self.show_error(msg)
        if not msg:
            self.accept()
