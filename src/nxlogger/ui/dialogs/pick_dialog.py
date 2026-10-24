"""Pick one tag or several tags (and variables) - used by the Properties panel."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout

from nxlogger.ui.dialogs.common import ChannelPicker
from nxlogger.ui.theme.ui_kit import set_prop


class PickDialog(QDialog):
    def __init__(self, parent, choices, chosen, multi: bool, title: str = "Choose tags"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 480)
        self.multi = multi
        self.picker = ChannelPicker(choices, chosen, multi=multi)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        set_prop(bb.button(QDialogButtonBox.StandardButton.Ok), "primary", True)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        self.picker.tree.itemDoubleClicked.connect(lambda *_: not multi and self.accept())
        v = QVBoxLayout(self); v.addWidget(self.picker, 1); v.addWidget(bb)

    def result_keys(self):
        """[[device_id, index], ...] (multi) or [device_id, index] / None (single)."""
        return self.picker.checked() if self.multi else self.picker.current()
