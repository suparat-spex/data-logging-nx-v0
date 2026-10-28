"""Add / edit a tag group (folder): name and parent group - of a device (Tags > Device) or of the
project (Tags > Groups: one tree for all tags)."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLineEdit, QTabWidget, QVBoxLayout

from nxlogger.ui.dialogs.common import form_page
from nxlogger.ui.theme.ui_kit import label, set_prop


class GroupDialog(QDialog):
    """owner: where it lives ("PLC-01", "Project"); groups: the existing paths; n_tags: tags in it
    (shown when editing). path = the group's current path ("" for a new group).
    result_path() -> the new path."""

    def __init__(self, parent, owner: str, groups: list[str], path: str = "", parent_path: str = "",
                 n_tags: int = 0, root: str = "(device root)"):
        super().__init__(parent)
        self.groups, self.path = list(groups), path
        self.setWindowTitle("Group" if path else "Add group")
        self.setMinimumWidth(440)
        w, f = form_page()
        self.name = QLineEdit(path.rsplit("/", 1)[-1] if path else "")
        self.name.setPlaceholderText("e.g. Boiler")
        cur_parent = path.rsplit("/", 1)[0] if "/" in path else ("" if path else parent_path)
        self.parent_group = QComboBox()
        self.parent_group.addItem(root, "")
        for g in self.groups:
            if not path or not (g == path or g.startswith(path + "/")):    # not into itself
                self.parent_group.addItem(g, g)
        self.parent_group.setCurrentIndex(max(0, self.parent_group.findData(cur_parent)))
        f.addRow("Device" if root == "(device root)" else "In", label(owner, muted=True))
        f.addRow("Name", self.name)
        f.addRow("Parent group", self.parent_group)
        if path:
            f.addRow("Tags", label(f"{n_tags} tag(s) in this group and its sub-groups", muted=True))
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
        if new != self.path and new in self.groups:
            self.err.setText("This group already exists."); return
        self.err.setText("")
        self.accept()
