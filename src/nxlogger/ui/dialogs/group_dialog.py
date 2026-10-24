"""Add / edit a tag group (folder) of a device: name and parent group."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLineEdit, QTabWidget, QVBoxLayout

from nxlogger.core.tags import DeviceCfg
from nxlogger.ui.dialogs.common import form_page
from nxlogger.ui.theme.ui_kit import label, set_prop


class GroupDialog(QDialog):
    """path = the group's current path ("" for a new group). result_path() -> the new path."""

    def __init__(self, parent, dev: DeviceCfg, path: str = "", parent_path: str = ""):
        super().__init__(parent)
        self.dev, self.path = dev, path
        self.setWindowTitle("Group" if path else "Add group")
        self.setMinimumWidth(440)
        w, f = form_page()
        self.name = QLineEdit(path.rsplit("/", 1)[-1] if path else "")
        self.name.setPlaceholderText("e.g. Boiler")
        cur_parent = path.rsplit("/", 1)[0] if "/" in path else ("" if path else parent_path)
        self.parent_group = QComboBox()
        self.parent_group.addItem("(device root)", "")
        for g in dev.all_groups():
            if not path or not (g == path or g.startswith(path + "/")):    # not into itself
                self.parent_group.addItem(g, g)
        self.parent_group.setCurrentIndex(max(0, self.parent_group.findData(cur_parent)))
        n = sum(1 for t in dev.tags if path and (t.group == path or t.group.startswith(path + "/")))
        f.addRow("Device", label(dev.name, muted=True))
        f.addRow("Name", self.name)
        f.addRow("Parent group", self.parent_group)
        if path:
            f.addRow("Tags", label(f"{n} tag(s) in this group and its sub-groups", muted=True))
        tabs = QTabWidget(); tabs.addTab(w, "Group")
        self.err = label("", small=True); self.err.setObjectName("statusMsg"); set_prop(self.err, "level", "bad")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        set_prop(bb.button(QDialogButtonBox.StandardButton.Ok), "primary", True)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        v = QVBoxLayout(self); v.addWidget(tabs); v.addWidget(self.err); v.addWidget(bb)

    def result_path(self) -> str:
        name = self.name.text().strip().replace("/", "-")
        parent = self.parent_group.currentData() or ""
        return f"{parent}/{name}" if parent else name

    def _ok(self):
        if not self.name.text().strip():
            self.err.setText("Enter a name."); return
        new = self.result_path()
        if new != self.path and new in self.dev.all_groups():
            self.err.setText("This group already exists."); return
        self.err.setText("")
        self.accept()
