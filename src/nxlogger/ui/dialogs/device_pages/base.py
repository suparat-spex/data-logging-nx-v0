"""ConnectionPage - the connector part of the device settings pop-up, one subclass per connector
(sim.py, modbus_tcp.py, modbus_rtu.py, mqtt.py, opcua.py, opcda.py).

The fields stay the connector schema in core/tags.DRIVERS[...]["settings"] (saved, imported,
shown in Properties the same way); a page only lays them out in titled sections and adds
connector helpers (port list, default ports, anonymous login ...). The dialog shows the
connector header, the General section above the page and summary() in its footer.

Subclass API:
  DRIVER                  key in DRIVERS
  SECTIONS                [(title, [setting keys])] - keys not listed land in "More"
  LABELS / CHOICES        {key: label} / {key: {value: text}} - friendlier wording on this page
  editor(f, value)        -> (kind, widget) for one field (default: common.field_editor); a widget
                          with a `row_widget` attribute shows that in the form (combo + button)
  build_extra(layout)     extra widgets under the sections (self.section(v, title) adds one)
  on_change(values)       show / hide / enable fields after an edit
  summary(values)         one line for the dialog footer, e.g. "mqtt://broker.emqx.io:1883"
  validate(values)        "" or an error message (Save stays on the dialog)

Two layouts (common.POPUP_STYLE, see the nxlogger-dev skill, references/popup-styles.md):
  tabs=False   sections one under the other in this widget (titled blocks, one page)
  tabs=True    every section is its own page in `self.pages` [(title, widget)] for the dialog's
               tab widget; this widget only keeps the fields' API (values, signals)
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFormLayout, QLabel, QVBoxLayout, QWidget

from nxlogger.core.tags import DRIVERS
from nxlogger.ui.dialogs.common import field_editor, field_value


def section(v: QVBoxLayout, title: str) -> QFormLayout:
    """A titled block (bold title, line under it) holding a form - the dialog's General section
    and every connector section look the same."""
    v.addSpacing(10)
    t = QLabel(title); t.setObjectName("pageSection")
    v.addWidget(t)
    form = QFormLayout(); form.setContentsMargins(8, 4, 0, 0)
    form.setHorizontalSpacing(12); form.setVerticalSpacing(6)
    v.addLayout(form)
    return form


def form_labels(forms) -> list[QWidget]:
    return [it.widget() for form in forms for i in range(form.rowCount())
            if (it := form.itemAt(i, QFormLayout.ItemRole.LabelRole)) is not None and it.widget() is not None]


class ConnectionPage(QWidget):
    DRIVER = ""
    SECTIONS: list[tuple[str, list[str]]] = []
    LABELS: dict[str, str] = {}
    CHOICES: dict[str, dict] = {}
    changed = Signal()

    def __init__(self, values: dict, parent=None, tabs: bool = False):
        super().__init__(parent)
        self.tabs = tabs
        self.pages: list[tuple[str, QWidget]] = []           # tabs=True: one page per section
        info = DRIVERS[self.DRIVER]
        self.schema = {f["key"]: f for f in info["settings"]}
        self.w: dict[str, tuple[str, QWidget]] = {}          # key -> (kind, editor), like SchemaForm
        self._form_of: dict[str, QFormLayout] = {}
        self._row_of: dict[str, QWidget] = {}
        self._forms: list[QFormLayout] = []
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(4)
        used = set()
        for title, keys in self.SECTIONS:
            form = self.section(v, title)
            for k in keys:
                self._add(form, k, values); used.add(k)
        rest = [k for k in self.schema if k not in used]
        if rest:
            form = self.section(v, "More")
            for k in rest:
                self._add(form, k, values)
        self.build_extra(v)
        self.changed.connect(self._update)
        self._update()

    # ---- building
    def section(self, v: QVBoxLayout, title: str) -> QFormLayout:
        if self.tabs:                                # a page of its own (the tab names it)
            page = QWidget()
            pv = QVBoxLayout(page); pv.setContentsMargins(12, 12, 12, 12)
            form = QFormLayout(); form.setHorizontalSpacing(12); form.setVerticalSpacing(6)
            pv.addLayout(form); pv.addStretch(1)
            self.pages.append((title, page))
        else:
            form = section(v, title)
        self._forms.append(form)
        return form

    def labels(self) -> list[QWidget]:
        """Label widgets of every section (the dialog gives all one column width)."""
        return form_labels(self._forms)

    def _add(self, form: QFormLayout, key: str, values: dict):
        f = dict(self.schema[key])
        if key in self.CHOICES:                      # same values, readable text
            f["extra"] = {k: self.CHOICES[key].get(k, k) for k in (f["extra"] or [])}
        kind, w = self.editor(f, values.get(key, f["default"]))
        if f.get("tip") == "seconds" and hasattr(w, "setSuffix"):      # 1.0 s, not 1.0000
            w.setSuffix(" s")
            if hasattr(w, "setDecimals"):
                w.setDecimals(1)
        if hasattr(w, "setAlignment") and hasattr(w, "setSuffix"):     # numbers line up with text fields
            w.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        row = getattr(w, "row_widget", w)
        form.addRow(self.LABELS.get(key, f["label"]), row)
        self.w[key] = (kind, w)
        self._form_of[key], self._row_of[key] = form, row

    def editor(self, f: dict, value) -> tuple[str, QWidget]:
        return f["type"], field_editor(f, value, self.changed.emit)

    def build_extra(self, v: QVBoxLayout):
        pass

    # ---- behaviour
    def on_change(self, values: dict):
        pass

    def summary(self, values: dict) -> str:
        return ""

    def validate(self, values: dict) -> str:
        return ""

    def _update(self):
        self.on_change(self.values())

    # ---- the SchemaForm API the dialog uses
    def values(self) -> dict:
        return {k: field_value(kind, w) for k, (kind, w) in self.w.items()}

    def set_visible(self, key: str, on: bool):
        if key in self.w:
            self._form_of[key].setRowVisible(self._row_of[key], on)

    def set_value(self, key: str, value):
        kind, w = self.w[key]
        if kind in ("int", "float"):
            w.setValue(value)
        elif kind in ("str", "password"):
            w.setText(str(value))
        elif kind == "bool":
            w.setChecked(bool(value))
        elif kind == "editable":
            w.setEditText(str(value))
        else:
            w.setCurrentIndex(max(0, w.findData(value)))
