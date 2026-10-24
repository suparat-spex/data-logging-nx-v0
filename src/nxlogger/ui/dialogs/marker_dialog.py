"""AI/ML data log event marker: type and note - an action pop-up. The time is the click."""
from __future__ import annotations

import time

from PySide6.QtWidgets import QComboBox, QLineEdit

from nxlogger.ui.dialogs.common import ActionDialog
from nxlogger.ui.theme.ui_kit import label


class MarkerDialog(ActionDialog):
    """types = [(events.csv type, text)]; result() -> (type, note)."""

    def __init__(self, parent, types, kind: str, t: float):
        super().__init__(parent, "Event marker", "Marks this moment in events.csv of the AI/ML data log - "
                                                 "the AI app turns markers into labels.", "Add marker", 420)
        self.kind = QComboBox()
        for k, text in types:
            self.kind.addItem(text, k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        self.note = QLineEdit(); self.note.setPlaceholderText("optional - what happened")
        self.form.addRow("Time", label(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), muted=True))
        self.form.addRow("Type", self.kind)
        self.form.addRow("Note", self.note)
        self.note.setFocus()

    def result(self) -> tuple[str, str]:
        return self.kind.currentData(), self.note.text().strip()
