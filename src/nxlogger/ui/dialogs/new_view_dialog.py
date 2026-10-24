"""Create a view: name and what it starts with - an action pop-up."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLineEdit

from nxlogger.ui.dialogs.common import ActionDialog


class NewViewDialog(ActionDialog):
    """devices = [(device id, name)]; result() -> (name, None | device id for an overview)."""

    def __init__(self, parent, default_name: str, taken: set[str], devices: list[tuple[str, str]]):
        super().__init__(parent, "New view", "A view is a screen of headers, trends, numbers, gauges and "
                                             "tables. It opens in edit mode.", "Create view", 420)
        self.taken = taken
        self.name = QLineEdit(default_name); self.name.selectAll()
        self.start = QComboBox()
        self.start.addItem("Empty view", None)
        for did, name in devices:
            self.start.addItem(f"Overview of {name} (header, numbers, trend, statistics)", did)
        self.form.addRow("Name", self.name)
        self.form.addRow("Start with", self.start)

    def validate(self) -> str:
        n = self.name.text().strip()
        if not n:
            return "Enter a name."
        return "Another view already has this name." if n in self.taken else ""

    def result(self) -> tuple[str, str | None]:
        return self.name.text().strip(), self.start.currentData()
