"""Project settings pop-up (Properties > Edit... on the project, like the device and tag pop-ups):
name, operator, description; the project file and the log folder. Tabs style via
common.popup_form. The project changes only on Save (apply)."""
from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLineEdit, QPlainTextEdit,
                               QPushButton, QTabWidget, QVBoxLayout)

from nxlogger.ui.dialogs.common import form_page
from nxlogger.ui.theme.ui_kit import label, set_prop


class ProjectDialog(QDialog):
    """project: the Project; path: its file (or None); counts: {"Devices": n, ...} shown as info;
    folder_locked: recording - the log folder cannot change."""

    def __init__(self, parent, project, path: str | None, counts: dict, folder_locked: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Project")
        self.setMinimumWidth(480)
        tabs = QTabWidget()
        w, f = form_page()
        self.name = QLineEdit(project.name); self.name.setPlaceholderText("e.g. Water Plant A")
        self.operator = QLineEdit(project.operator); self.operator.setPlaceholderText("optional")
        self.desc = QPlainTextEdit(project.description); self.desc.setMaximumHeight(90)
        self.desc.setPlaceholderText("What is tested or logged (saved in the session files)")
        f.addRow("Name", self.name)
        f.addRow("Operator", self.operator)
        f.addRow("Description", self.desc)
        f.addRow("", label("Name, operator and description are written into every recording (project.json).",
                           muted=True, small=True))
        tabs.addTab(w, "General")
        w2, f2 = form_page()
        f2.addRow("Project file", label(path or "Not saved yet - File > Save as...", muted=True))
        self.folder = QLineEdit(project.logging.folder)
        browse = QPushButton("Browse..."); browse.clicked.connect(self._browse)
        row = QHBoxLayout(); row.addWidget(self.folder, 1); row.addWidget(browse)
        f2.addRow("Log folder", row)
        if folder_locked:
            self.folder.setEnabled(False); browse.setEnabled(False)
            f2.addRow("", label("Stop recording to change the log folder.", muted=True, small=True))
        f2.addRow("", label("Each recording is a new session folder inside the log folder.", muted=True, small=True))
        for k, n in counts.items():
            f2.addRow(k, label(str(n), muted=True))
        tabs.addTab(w2, "Files")
        self.err = label("", small=True); self.err.setObjectName("statusMsg")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        v = QVBoxLayout(self); v.addWidget(tabs, 1); v.addWidget(self.err); v.addWidget(bb)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Log folder", self.folder.text())
        if d:
            self.folder.setText(d)

    def result(self) -> dict:
        return {"name": self.name.text().strip(), "operator": self.operator.text().strip(),
                "description": self.desc.toPlainText().strip(), "folder": self.folder.text().strip()}

    def _ok(self):
        r = self.result()
        if not r["name"]:
            set_prop(self.err, "level", "bad"); self.err.setText("Enter a project name."); return
        if not r["folder"]:
            set_prop(self.err, "level", "bad"); self.err.setText("Choose a log folder."); return
        self.accept()
