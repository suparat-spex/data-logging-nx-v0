"""View properties: name, canvas background, grid, default card colours."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLabel, QLineEdit, QSpinBox,
                               QTabWidget, QVBoxLayout)

from nxlogger.ui.dialogs.common import ColorButton, form_page
from nxlogger.ui.theme.ui_kit import label, set_prop


class ViewDialog(QDialog):
    def __init__(self, parent, view: dict, taken: set[str] = frozenset()):
        super().__init__(parent)
        self.v, self.taken = view, {t.lower() for t in taken}
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

    def _ok(self):
        name = self.name.text().strip()
        err = "Enter a view name." if not name else \
            "Another view already has this name." if name.lower() in self.taken else ""
        if err:
            set_prop(self.err, "level", "bad"); self.err.setText(err)
            return
        self.accept()

    def apply(self, view: dict):
        view.update(name=self.name.text().strip(), description=self.desc.text().strip(),
                    bg=self.bg.color(), grid=self.grid.value(), show_grid=self.show_grid.isChecked(),
                    snap=self.snap.isChecked(), tile_bg=self.tile_bg.color(),
                    tile_border=self.tile_border.color(), title_bar=self.title_bar.isChecked())
