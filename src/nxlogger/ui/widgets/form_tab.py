"""
form_tab.py - configuration editors as closable document tabs (Genesis64 Workbench style).

Double-clicking a device, tag, group, alarm, variable ... in the Project tree opens its
editor here instead of a pop-up:

  header   Full Path: Project / Tags / PLC / Boiler / Temperature            [TAG]
  body     the form, one collapsible section per page (a ▲ / ▼ title bar each)
  bar      [extra actions ...]      Apply  Refresh  Close  New...  Apply & Close  Apply & New...

Keys: Ctrl+S Apply, Ctrl+Enter Apply & Close, Ctrl+W Close (the window's tab shortcut).
Apply / Apply & Close are enabled only when something changed (or for a new item);
Refresh only when there are edits to discard. Closing with changes asks Apply / Don't
apply / Cancel. Rules for tabs vs pop-ups: docs/ui-guidelines.md.

The form itself is one of the ui/dialogs classes, embedded as a widget: its OK / Cancel
row is hidden, its QTabWidget pages become the sections, and its own validation (_ok)
runs on Apply. So every editor exists once and can still be used as a pop-up where a
pop-up is right (an action inside a tab: pick tags, browse a server, add tags in bulk).

Spec (dict) given by the window:
  key, title, icon, path, kind        identity and header text ("[MODBUS TCP DEVICE]")
  build()  -> QDialog                 a fresh form from the model (also Refresh)
  apply(dlg) -> bool                  write the form into the model (False = not applied)
  new()                               optional: open an editor for a new item of this kind
  actions  [(text, icon, callable)]   optional extra buttons on the left of the bar
  locked() -> str                     optional: reason why Apply is not possible now ("" = ok)
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea, QTabWidget,
                               QTreeWidget, QVBoxLayout, QWidget)

from nxlogger.ui.dialogs.common import ask_unsaved, confirm
from nxlogger.ui.theme.ui_kit import T, label, make_icon, set_prop


class _Bar(QFrame):
    """Section title bar: click anywhere on it to fold / unfold."""
    clicked = Signal()

    def mousePressEvent(self, e):
        self.clicked.emit()


class Section(QFrame):
    """Collapsible block with a title bar (click the bar to fold)."""

    def __init__(self, title: str, body: QWidget):
        super().__init__()
        self.setObjectName("formSection")
        bar = _Bar(); bar.setObjectName("formSectionBar")
        bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title = QLabel(title.replace("&&", "&")); self.title.setObjectName("formSectionHead")
        self.arrow = QLabel("▲"); self.arrow.setObjectName("formSectionArrow")
        hl = QHBoxLayout(bar); hl.setContentsMargins(10, 5, 10, 5)
        hl.addWidget(self.title, 1); hl.addWidget(self.arrow)
        bar.clicked.connect(self.toggle)
        self.body = body
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(bar)
        wrap = QFrame(); wrap.setObjectName("formSectionBody")
        wl = QVBoxLayout(wrap); wl.setContentsMargins(10, 8, 10, 10); wl.addWidget(body)
        self.wrap = wrap
        v.addWidget(wrap)

    def toggle(self):
        on = not self.wrap.isVisible()
        self.wrap.setVisible(on)
        self.arrow.setText("▲" if on else "▼")


class FormTab(QWidget):
    changed = Signal()                       # modified flag or identity changed (tab text)

    def __init__(self, host, spec: dict):
        super().__init__()
        self.host, self.spec = host, spec
        self.modified = False
        self.dlg: QDialog | None = None
        self._ok = False

        head = QFrame(); head.setObjectName("formHead")
        hl = QVBoxLayout(head); hl.setContentsMargins(12, 6, 12, 6); hl.setSpacing(2)
        top = QHBoxLayout()
        self.path_lbl = QLabel(); self.path_lbl.setObjectName("formPath")
        self.kind_lbl = QLabel(); self.kind_lbl.setObjectName("formPath")
        top.addWidget(self.path_lbl, 1); top.addWidget(self.kind_lbl)
        hl.addLayout(top)
        self.msg = label("", small=True); self.msg.setObjectName("statusMsg"); self.msg.hide()
        hl.addWidget(self.msg)

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        bar = QFrame(); bar.setObjectName("formBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(10, 6, 10, 6); bl.setSpacing(6)
        for text, icon, slot in spec.get("actions", []):
            b = QPushButton(text)
            if icon:
                host.reg_icon(b, icon)
            b.clicked.connect(slot)
            bl.addWidget(b)
        bl.addStretch(1)
        self.btn_apply = self._btn(bl, "Apply", self.apply, "Ctrl+S")
        self.btn_refresh = self._btn(bl, "Refresh", self.refresh, tip="Discard the edits: back to the project")
        self._btn(bl, "Close", self.close_tab, tip="Close  (Ctrl+W)")
        has_new = spec.get("new") is not None
        if has_new:
            self._btn(bl, "New...", self._new, tip="Another new item of this kind")
        self.btn_apply_close = self._btn(bl, "Apply && Close", self._apply_close, tip="Apply && Close  (Ctrl+Enter)")
        self.btn_apply_new = self._btn(bl, "Apply && New...", self._apply_new) if has_new else None
        set_prop(self.btn_apply, "primary", True)
        for keys, slot in (("Ctrl+S", self.apply), ("Ctrl+Return", self._apply_close),
                           ("Ctrl+Enter", self._apply_close)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(head); v.addWidget(self.scroll, 1); v.addWidget(bar)
        self.set_identity(spec["key"], spec["title"], spec["path"])
        self._load()

    def _btn(self, layout, text, slot, shortcut=None, tip="") -> QPushButton:
        b = QPushButton(text); b.setMinimumWidth(90)
        b.clicked.connect(slot)
        b.setToolTip(tip or (f"{text.replace('&&', '&')}  ({shortcut})" if shortcut else ""))
        layout.addWidget(b)
        return b

    # ---- identity (a new item gets its real key / name on the first Apply)
    @property
    def key(self) -> str:
        return self.spec["key"]

    @property
    def title(self) -> str:
        return self.spec["title"]

    def set_identity(self, key: str, title: str, path: str):
        self.spec.update(key=key, title=title, path=path)
        self.path_lbl.setText(f"Full Path:&nbsp;&nbsp;<b>{path}</b>")
        self.kind_lbl.setText(f"[{self.spec.get('kind', '').upper()}]")
        self.changed.emit()

    # ---- form
    def _load(self):
        old = self.dlg
        dlg = self.spec["build"]()
        dlg.setWindowFlags(Qt.WindowType.Widget)
        dlg.accept = lambda: setattr(self, "_ok", True)   # the form's own validation calls accept()
        dlg.reject = lambda: None
        for bb in dlg.findChildren(QDialogButtonBox):
            bb.hide()
        self._sections(dlg)
        box = QWidget(); bv = QVBoxLayout(box); bv.setContentsMargins(12, 10, 12, 12)
        dlg.setMaximumWidth(max(760, dlg.minimumWidth()))
        bv.addWidget(dlg, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        bv.addStretch(1)
        self.scroll.setWidget(box)
        self.dlg = dlg
        if old is not None:
            old.deleteLater()
        self._watch(dlg)
        self._set_modified(bool(self.spec.get("is_new")))
        self._message("")

    def _sections(self, dlg: QDialog):
        """The dialog's tab pages -> collapsible sections, one under the other. A form without
        pages becomes one section (titled spec["section"])."""
        lay = dlg.layout()
        if not any(tw.parent() is dlg for tw in dlg.findChildren(QTabWidget)):
            inner = QWidget(); il = QVBoxLayout(inner)          # move the form into a section
            il.setContentsMargins(0, 0, 0, 0); il.setSpacing(lay.spacing())
            while lay.count():
                it = lay.takeAt(0)
                if it.widget() is not None:
                    il.addWidget(it.widget())
                elif it.layout() is not None:
                    sub = it.layout(); sub.setParent(None); il.addLayout(sub)
                else:
                    il.addItem(it)
            lay.addWidget(Section(self.spec.get("section", self.spec.get("kind", "General").capitalize()), inner))
            return
        for tw in dlg.findChildren(QTabWidget):
            if tw.parent() is not dlg:
                continue                          # nested tabs (inside a page) stay tabs
            box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
            pages = [(tw.tabText(i), tw.widget(i)) for i in range(tw.count())]
            for i in reversed(range(tw.count())):
                tw.removeTab(i)                   # the pages live on in the sections
            for title, page in pages:
                v.addWidget(Section(title, page))
                page.show()                       # a tab widget hides its other pages
            lay.replaceWidget(tw, box)
            tw.hide()

    def _watch(self, root: QWidget):
        """Any edit in the form marks the tab modified (*)."""
        mark = lambda *_: self._set_modified(True)        # noqa: E731
        for w in root.findChildren(QWidget):
            if isinstance(w, QLineEdit):
                w.textEdited.connect(mark)
            elif isinstance(w, QPlainTextEdit):
                w.textChanged.connect(mark)
            elif isinstance(w, QComboBox):
                w.activated.connect(mark)
                if w.isEditable() and w.lineEdit():
                    w.lineEdit().textEdited.connect(mark)
            elif isinstance(w, QAbstractSpinBox):
                w.valueChanged.connect(mark)
            elif isinstance(w, QCheckBox):
                w.clicked.connect(mark)
            elif isinstance(w, QTreeWidget):
                w.itemChanged.connect(mark)
            elif isinstance(w, QAbstractButton) and hasattr(w, "color") and hasattr(w, "changed"):
                w.changed.connect(mark)              # ColorButton

    def _set_modified(self, on: bool):
        if on != self.modified:
            self.modified = on
            self.changed.emit()
        dirty = self.modified or bool(self.spec.get("is_new"))
        for b in (self.btn_apply, self.btn_apply_close, self.btn_apply_new):
            if b is not None:
                b.setEnabled(dirty)
        self.btn_refresh.setEnabled(self.modified and not self.spec.get("is_new"))

    def _message(self, text: str, level: str = "bad"):
        self.msg.setText(text); self.msg.setVisible(bool(text))
        set_prop(self.msg, "level", level)
        if text and level == "ok":                   # confirmations fade, errors stay
            QTimer.singleShot(3000, lambda: self.msg.text() == text and self._message(""))

    # ---- buttons
    def apply(self) -> bool:
        why = self.spec.get("locked", lambda: "")()
        if why:
            self._message(why)
            return False
        if not (self.modified or self.spec.get("is_new")):
            return True                              # nothing to apply
        self._ok = False
        if hasattr(self.dlg, "_ok"):
            self.dlg._ok()                       # validation: calls accept() when fine
        else:
            self._ok = True
        if not self._ok:
            self._message("Not applied - see the message in the form.")
            return False
        if not self.spec["apply"](self.dlg):
            return False
        self.spec["is_new"] = False
        self._set_modified(False)
        self._message(f"Applied  ·  {time.strftime('%H:%M:%S')}", "ok")
        return True

    def refresh(self):
        """Back to the values in the project (discard the edits)."""
        if self.modified and not self.spec.get("is_new") and not confirm(
                self, "Refresh", f"Discard the changes to '{self.title}'?", "Discard changes",
                "The editor shows the values in the project again."):
            return
        self._load()

    def can_close(self) -> bool:
        if not self.modified:
            return True
        self.host.show_editor(self)                  # the question is about the tab in view
        r = ask_unsaved(self, "Unapplied changes", f"Apply the changes to '{self.title}'?", keep="Apply")
        if r == "keep":
            return self.apply()
        return r == "discard"

    def close_tab(self):
        self.host.close_editor(self)

    def _new(self):
        self.spec["new"]()

    def _apply_close(self):
        if self.apply():
            self.modified = False
            self.host.close_editor(self)

    def _apply_new(self):
        if self.apply():
            self.spec["new"]()

    def tab_icon(self):
        return make_icon(self.spec.get("icon", "pencil"), T["accent" if self.modified else "muted"])
