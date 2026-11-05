"""Pick one tag or several tags - used by the Properties panel, virtual tags and logging profiles
(assets=True: whole assets too, kept live - result_assets())."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout

from nxlogger.ui.dialogs.common import ChannelPicker
from nxlogger.ui.theme.ui_kit import set_prop


class PickDialog(QDialog):
    def __init__(self, parent, choices, chosen, multi: bool, title: str = "Choose tags", assets: bool = False,
                 chosen_assets=()):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 480)
        self.multi = multi
        self.picker = ChannelPicker(choices, chosen, multi=multi, assets=assets, chosen_assets=chosen_assets)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        set_prop(bb.button(QDialogButtonBox.StandardButton.Ok), "primary", True)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        self.picker.tree.itemDoubleClicked.connect(lambda *_: not multi and self.accept())
        v = QVBoxLayout(self); v.addWidget(self.picker, 1); v.addWidget(bb)

    def result_keys(self):
        """[[device_id, index], ...] (multi) or [device_id, index] / None (single)."""
        return self.picker.checked() if self.multi else self.picker.current()

    def result_assets(self) -> tuple[list, list]:
        """(tags outside the whole assets, the whole assets' ids) - assets=True."""
        return self.picker.checked_outside_assets(), self.picker.checked_assets()
