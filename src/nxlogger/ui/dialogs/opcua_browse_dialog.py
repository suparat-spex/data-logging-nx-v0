"""Browse an OPC UA server and pick variables to add as tags (network calls in a thread)."""
from __future__ import annotations

import queue
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from nxlogger.ui.theme.ui_kit import label, set_prop


class OpcUaBrowseDialog(QDialog):
    """selected() -> [(node id, browse name)] of the ticked variables."""

    def __init__(self, parent, settings: dict, groups: list[str], group: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"Browse OPC UA - {settings.get('endpoint', '')}")
        self.resize(640, 520)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Type", "Value", "Node ID"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for c, w in enumerate((220, 80, 90)):
            self.tree.header().resizeSection(c, w)
        self.tree.itemExpanded.connect(self._expand)
        self.group = QComboBox(); self.group.setEditable(True)
        self.group.addItems([""] + groups); self.group.setCurrentText(group)
        f = QFormLayout(); f.addRow("Add to group", self.group)
        self.status = label("Connecting...", muted=True, small=True)
        set_prop(self.status, "level", "muted"); self.status.setObjectName("statusMsg")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Add ticked variables")
        set_prop(bb.button(QDialogButtonBox.StandardButton.Ok), "primary", True)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        v = QVBoxLayout(self)
        v.addWidget(label("Tick the variables to add as tags. Folders and objects load when expanded.",
                          muted=True, small=True))
        v.addWidget(self.tree, 1); v.addLayout(f); v.addWidget(self.status); v.addWidget(bb)
        self._jobs: "queue.Queue" = queue.Queue()
        self._results: "queue.Queue" = queue.Queue()
        self._items: dict[str, QTreeWidgetItem] = {}
        self._browser = None
        threading.Thread(target=self._worker, args=(settings,), daemon=True).start()
        self._timer = QTimer(self, interval=100, timeout=self._poll)
        self._timer.start()
        self._jobs.put(None)                          # the Objects folder

    # ---- worker thread: one connection, browse requests one by one
    def _worker(self, settings):
        try:
            from nxlogger.comms.drivers.opcua import Browser
            self._browser = Browser(settings)
        except Exception as e:
            self._results.put(("error", None, str(e)))
            return
        while True:
            nid = self._jobs.get()
            if nid == "__stop__":
                self._browser.close()
                return
            try:
                self._results.put(("rows", nid, self._browser.children(nid)))
            except Exception as e:
                self._results.put(("error", nid, str(e)))

    def _poll(self):
        while True:
            try:
                kind, nid, data = self._results.get_nowait()
            except queue.Empty:
                return
            if kind == "error":
                self.status.setText(f"OPC UA: {data}")
                set_prop(self.status, "level", "bad")
                continue
            parent = self._items.get(nid) if nid else None
            if parent is not None:
                parent.takeChildren()
            for r in data:
                it = QTreeWidgetItem([r["name"], r["type"], r["value"], r["node"]])
                it.setData(0, Qt.ItemDataRole.UserRole, r)
                if r["variable"]:
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    it.setCheckState(0, Qt.CheckState.Unchecked)
                else:                                    # folder / object: children on expand
                    it.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                self._items[r["node"]] = it
                (parent.addChild(it) if parent is not None else self.tree.addTopLevelItem(it))
            self.status.setText(f"{len(self._items)} node(s) loaded")
            set_prop(self.status, "level", "muted")

    def _expand(self, it: QTreeWidgetItem):
        r = it.data(0, Qt.ItemDataRole.UserRole)
        if r and not r["variable"] and it.childCount() == 0:
            self.status.setText(f"Browsing {r['name']}...")
            self._jobs.put(r["node"])

    def selected(self) -> list[tuple[str, str]]:
        return [(it.data(0, Qt.ItemDataRole.UserRole)["node"], it.text(0)) for it in self._items.values()
                if it.flags() & Qt.ItemFlag.ItemIsUserCheckable and it.checkState(0) == Qt.CheckState.Checked]

    def group_path(self) -> str:
        return "/".join(p.strip() for p in self.group.currentText().split("/") if p.strip())

    def done(self, r):
        self._timer.stop()
        self._jobs.put("__stop__")
        super().done(r)
