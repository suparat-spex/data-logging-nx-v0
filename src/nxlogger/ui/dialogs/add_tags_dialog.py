"""Add several tags at once: count, name pattern, group, first address and address step."""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QLineEdit, QSpinBox, QVBoxLayout)

from nxlogger.core.tags import DRIVERS, MODBUS_TYPES, DeviceCfg, TagCfg, new_tag
from nxlogger.ui.dialogs.common import SchemaForm, form_page
from nxlogger.ui.theme.ui_kit import label, set_prop


class AddTagsDialog(QDialog):
    def __init__(self, parent, dev: DeviceCfg, group: str = "", groups=None):
        super().__init__(parent)
        self.dev = dev
        self.setWindowTitle(f"Add tags - {dev.name}")
        self.setMinimumWidth(440)
        w, f = form_page()
        self.count = QSpinBox(minimum=1, maximum=500, value=1)
        self.name = QLineEdit("Tag{n}")
        self.name.setToolTip("{n} = running number (1, 2, 3 ...)")
        self.first = QSpinBox(minimum=1, maximum=100000, value=dev.next_index() + 1)
        self.first.setToolTip("Number used for {n} in the first tag")
        self.group = QComboBox(); self.group.setEditable(True)
        self.group.addItems([""] + (dev.all_groups() if groups is None else list(groups)))
        self.group.setCurrentText(group)
        f.addRow("How many", self.count)
        f.addRow("Name", self.name)
        f.addRow("First number", self.first)
        f.addRow("Group", self.group)
        self.addr = None
        self.step = QSpinBox(minimum=0, maximum=1000)
        if not dev.wave:
            f.addRow(label("Address of the first tag", name="sectionTitle"))
            self.addr = SchemaForm(DRIVERS[dev.driver]["address"])
            f.addRow(self.addr)
            if dev.driver in ("modbus_tcp", "modbus_rtu"):
                f.addRow("Address step", self.step)
                self.step.setToolTip("0 = by the data type (float32 = 2 registers, int16 = 1 ...)")
                self.step.setSpecialValueText("auto (data type size)")
        self.err = label("", small=True); self.err.setObjectName("statusMsg"); set_prop(self.err, "level", "bad")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        set_prop(bb.button(QDialogButtonBox.StandardButton.Ok), "primary", True)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        v = QVBoxLayout(self); v.addWidget(w); v.addWidget(self.err); v.addWidget(bb)

    def _ok(self):
        if self.dev.wave and self.count.value() + len(self.dev.tags) > 8:
            self.err.setText("A waveform packet has at most 8 channels."); return
        self.accept()

    def tags(self) -> list[TagCfg]:
        """The new tags (not yet added to the device)."""
        out = []
        base = self.addr.values() if self.addr else {}
        group = "/".join(p.strip() for p in self.group.currentText().split("/") if p.strip())
        tmp = DeviceCfg(self.dev.name, self.dev.driver, settings=dict(self.dev.settings),
                        tags=list(self.dev.tags))
        step = self.step.value() or (1 if base.get("area") in ("coil", "discrete")
                                      else MODBUS_TYPES.get(base.get("dtype", "uint16"), 1))
        for i in range(self.count.value()):
            addr = dict(base)
            if "addr" in addr:
                addr["addr"] = int(base["addr"]) + i * step
            name = self.name.text().replace("{n}", str(self.first.value() + i)) or f"Tag{tmp.next_index() + 1}"
            t = new_tag(tmp, name, group, **addr)
            tmp.tags.append(t)
            out.append(t)
        return out
