"""Write a value to a writable (RW) tag - an action pop-up."""
from __future__ import annotations

from nxlogger.ui.dialogs.common import ActionDialog, spin
from nxlogger.ui.theme.ui_kit import label


class WriteValueDialog(ActionDialog):
    def __init__(self, parent, device: str, tag, current: float | None, address: str):
        super().__init__(parent, f"Write - {tag.name}",
                         "The value is sent to the device as typed (before the tag's scaling). "
                         "The next scan shows the result.", "Write", 400)
        unit = f" {tag.unit}" if tag.unit else ""
        self.form.addRow("Tag", label(f"{device} · {tag.name}", muted=True))
        self.form.addRow("Address", label(address, muted=True))
        self.form.addRow("Current value", label("-" if current is None else f"{current:.{tag.decimals}f}{unit}",
                                                muted=True))
        self.value = spin(current if current is not None else 0.0, -1e12, 1e12, max(int(tag.decimals), 3))
        self.form.addRow("New value", self.value)
        self.value.setFocus(); self.value.selectAll()

    def result_value(self) -> float:
        return self.value.value()
