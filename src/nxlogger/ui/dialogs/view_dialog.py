"""View properties pop-up: name, size (fixed canvas, default 1920 x 1080), background, grid, default
card colours. Tabs style via common.popup_form; the view changes only on Save (apply)."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
                               QSpinBox, QTabWidget, QVBoxLayout)

from nxlogger.core.view_model import MAX_VIEW, VIEW_ALIGN, VIEW_SIZES

from nxlogger.ui.dialogs.common import ColorButton, form_page
from nxlogger.ui.theme.ui_kit import label, set_prop


class ViewDialog(QDialog):
    def __init__(self, parent, view: dict, taken: set[str] = frozenset(), extent: tuple[int, int] = (0, 0)):
        super().__init__(parent)
        self.v, self.taken, self.extent = view, {t.lower() for t in taken}, extent
        self.setWindowTitle(f"View properties - {view.get('name', '')}")
        self.setMinimumWidth(460)
        tabs = QTabWidget()

        w, f = form_page()
        self.name = QLineEdit(view.get("name", ""))
        self.desc = QLineEdit(view.get("description", ""))
        self.desc.setPlaceholderText("optional - shown as the tab tooltip")
        f.addRow("Name", self.name)
        f.addRow("Description", self.desc)
        tabs.addTab(w, "General")

        w, f = form_page()                          # the fixed canvas: same layout with any panel open
        self.preset = QComboBox()
        for wd, ht in VIEW_SIZES:                   # data "1920x1080": Qt hands tuples back as lists
            self.preset.addItem(f"{wd} × {ht}", f"{wd}x{ht}")
        self.preset.addItem("Custom", "")
        cur = (int(view.get("width", 1920)), int(view.get("height", 1080)))
        i = self.preset.findData(f"{cur[0]}x{cur[1]}")
        self.preset.setCurrentIndex(i if i >= 0 else self.preset.count() - 1)
        self.w_px = QSpinBox(minimum=320, maximum=MAX_VIEW, value=cur[0], suffix=" px", singleStep=10)
        self.h_px = QSpinBox(minimum=240, maximum=MAX_VIEW, value=cur[1], suffix=" px", singleStep=10)
        row = QHBoxLayout(); row.addWidget(self.w_px); row.addWidget(QLabel("×")); row.addWidget(self.h_px)
        row.addStretch(1)
        self.align = QComboBox()
        for k, text in VIEW_ALIGN.items():
            self.align.addItem(text, k)
        self.align.setCurrentIndex(max(0, self.align.findData(view.get("align", "left"))))
        self.preset.currentIndexChanged.connect(self._preset)
        for sb in (self.w_px, self.h_px):
            sb.valueChanged.connect(self._custom)
        need = f" Its components need at least {extent[0]} × {extent[1]} px." if any(extent) else ""
        f.addRow("Size", self.preset)
        f.addRow("Width × height", row)
        f.addRow("On a bigger window", self.align)
        f.addRow("", label("The view keeps this size: a smaller window shows scroll bars, so the layout "
                           "looks the same with any panel open." + need, muted=True, small=True))
        tabs.addTab(w, "Size")

        w, f = form_page()
        self.bg = ColorButton(view.get("bg", ""), "Auto (theme)")
        self.grid = QSpinBox(minimum=4, maximum=80, value=int(view.get("grid", 10)), suffix=" px")
        self.grid.setToolTip("Tiles snap to this grid when moved or resized")
        self.show_grid = QCheckBox("Show the grid while editing", checked=bool(view.get("show_grid", True)))
        self.snap = QCheckBox("Snap tiles to the grid", checked=bool(view.get("snap", True)))
        f.addRow("Background", self.bg)
        f.addRow("Grid size", self.grid)
        f.addRow("", self.show_grid)
        f.addRow("", self.snap)
        tabs.addTab(w, "Canvas")

        w, f = form_page()
        self.tile_bg = ColorButton(view.get("tile_bg", ""), "Auto (theme)")
        self.tile_border = ColorButton(view.get("tile_border", ""), "Auto (theme)")
        self.title_bar = QCheckBox("New tiles show a title bar", checked=bool(view.get("title_bar", True)))
        f.addRow("Card background", self.tile_bg)
        f.addRow("Card border", self.tile_border)
        f.addRow("", self.title_bar)
        f.addRow(label("Defaults for every tile of this view; a tile can override them on its "
                       "Appearance tab.", muted=True, small=True))
        tabs.addTab(w, "Cards")

        self.err = QLabel(); self.err.setObjectName("statusMsg")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        set_prop(bb.button(QDialogButtonBox.StandardButton.Ok), "primary", True)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        v = QVBoxLayout(self)
        v.addWidget(tabs, 1); v.addWidget(self.err); v.addWidget(bb)

    def _preset(self):
        d = self.preset.currentData()
        if d:
            d = [int(x) for x in d.split("x")]
            for sb, x in ((self.w_px, d[0]), (self.h_px, d[1])):
                sb.blockSignals(True); sb.setValue(x); sb.blockSignals(False)

    def _custom(self):
        i = self.preset.findData(f"{self.w_px.value()}x{self.h_px.value()}")
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(i if i >= 0 else self.preset.count() - 1)
        self.preset.blockSignals(False)

    def _ok(self):
        name = self.name.text().strip()
        ew, eh = self.extent
        small = self.w_px.value() < ew or self.h_px.value() < eh
        err = ("Enter a view name." if not name else
               "Another view already has this name." if name.lower() in self.taken else
               f"Size: the components need at least {ew} × {eh} px - move them first or choose a bigger size."
               if small else "")
        if err:
            set_prop(self.err, "level", "bad"); self.err.setText(err)
            return
        self.accept()

    def apply(self, view: dict):
        view.update(name=self.name.text().strip(), description=self.desc.text().strip(),
                    bg=self.bg.color(), grid=self.grid.value(), show_grid=self.show_grid.isChecked(),
                    snap=self.snap.isChecked(), tile_bg=self.tile_bg.color(),
                    tile_border=self.tile_border.color(), title_bar=self.title_bar.isChecked(),
                    width=self.w_px.value(), height=self.h_px.value(), align=self.align.currentData())
