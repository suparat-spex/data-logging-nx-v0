"""Map tags... (Assets): fill a machine's empty template slots with tags of one source, by name.

  Source   a device / Virtual / Aggregation / System / All tags
  Preview  slot -> the tag it gets (assets.map_tags: the template's match pattern, else the slot
           name inside the tag name; each tag once) - untick a row to leave that slot empty

[Map n tags] -> result(): [(slot name, key)].
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QTreeWidget, QTreeWidgetItem

from nxlogger.core import assets as am
from nxlogger.ui.dialogs.common import ActionDialog


class MapTagsDialog(ActionDialog):
    """a: the machine; t: its template (or None); sources: [(name, [(key, tag name)])];
    key_label(key) -> "<source> · <tag>"; source: the one to start with."""

    def __init__(self, parent, a: dict, t: dict | None, sources: list[tuple], key_label, source: str = ""):
        super().__init__(parent, "Map tags", f"Fill the empty attributes of {a['name']} with tags of one source, "
                         "by name", "Map tags", width=560, icon="tag")
        self.a, self.t, self.sources, self.key_label = a, t, sources, key_label
        self.source = QComboBox()
        for name, _c in sources:
            self.source.addItem(name)
        self.source.setCurrentIndex(max(0, self.source.findText(source)))
        self.preview = QTreeWidget(); self.preview.setHeaderLabels(["Attribute", "Tag"])
        self.preview.setRootIsDecorated(False); self.preview.setMinimumHeight(220)
        self.preview.header().resizeSection(0, 170)
        self.form.addRow("Source", self.source)
        self.form.addRow(self.preview)
        self.source.currentIndexChanged.connect(lambda _: self._fill())
        self.preview.itemChanged.connect(lambda *_: self._count())
        self._fill()

    def _fill(self):
        cands = self.sources[self.source.currentIndex()][1] if self.sources else []
        self.preview.blockSignals(True)
        self.preview.clear()
        for name, key in am.map_tags(self.a, self.t, cands):
            it = QTreeWidgetItem([name, self.key_label(key)])
            it.setData(0, Qt.ItemDataRole.UserRole, list(key))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Checked)
            self.preview.addTopLevelItem(it)
        empty = sum(1 for x in self.a["attributes"] if not x["key"])
        if not self.preview.topLevelItemCount():
            it = QTreeWidgetItem(["", "No matching tags in this source" if empty else "Every attribute has a tag"])
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.preview.addTopLevelItem(it)
        self.preview.blockSignals(False)
        self._count()

    def result(self) -> list[tuple]:
        out = []
        for i in range(self.preview.topLevelItemCount()):
            it = self.preview.topLevelItem(i)
            key = it.data(0, Qt.ItemDataRole.UserRole)
            if key and it.checkState(0) == Qt.CheckState.Checked:
                out.append((it.text(0), tuple(key)))
        return out

    def _count(self):
        n = len(self.result())
        self.ok.setText(f"Map {n} tag(s)" if n else "Map tags")
        self.ok.setEnabled(bool(n))

    def validate(self) -> str:
        return "" if self.result() else "Nothing to map."
