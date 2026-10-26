"""
water_plant_gui.py - window for the water treatment plant simulator (water_plant.py): broker,
start / stop, live values of all 49 signals with their MQTT topic and JSON path ready to copy,
the payload of each area, and the NX Logger device / tag exports.

  left   broker (shared form, remembered), simulation (interval, plant clock, payload mode),
         start / stop, export device JSON / tag CSV, log
  right  bar: Copy topic, Copy JSON path, Copy topic + path, Copy subscription (<base>/#), filter
         table: area > signals - value, unit, topic, JSON path, description
         payload: the last message of the selected area (Copy)

Copy: double-click a cell (topic / path / value ...), Ctrl+C (topics of the selected rows),
right-click for every choice. Runs without a broker too (values only, nothing published).

Run:  python -m nxlogger.sim.water_plant_gui        (or: nxlogger-wtp-gui)
"""
from __future__ import annotations

import json
import os
import sys
import time

from PySide6.QtCore import QCoreApplication, QSettings, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLineEdit, QMainWindow, QMenu,
                               QPlainTextEdit, QPushButton, QSplitter, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from nxlogger import APP_NAME, ORG_NAME
from nxlogger.comms.mqtt_client import MqttClient
from nxlogger.sim import water_plant as wtp
from nxlogger.ui.icons import check_icon_file, grip_icon_file
from nxlogger.ui.theme import ui_kit as ui
from nxlogger.ui.theme.app_style import EXTRA_QSS, blend, fusion_palette, fusion_qss
from nxlogger.ui.theme.ui_kit import T, Toast, fixed_width, label, make_icon, set_prop, vsep
from nxlogger.ui.widgets.mqtt_panel import ThemedMqttPanel

COLS = ["Signal", "Value", "Unit", "Topic", "JSON path", "Description"]
C_VAL, C_UNIT, C_TOPIC, C_PATH = 1, 2, 3, 4
SPEEDS = {"Real time (x1)": 1.0, "x10": 10.0, "1 plant hour / minute (x60)": 60.0,
          "1 plant day / 4 min (x360)": 360.0, "1 plant day / minute (x1440)": 1440.0}
MODES = {"json": "JSON per area  (<base>/<area>, JSON path = key)",
         "flat": "One topic per value  (<base>/<area>/<key>, plain number)"}


def apply_app_theme(mode: str):
    """The logger's look (Fusion + tokens + its extra QSS), so both windows match."""
    t = ui.THEMES[ui.resolve_theme(mode)]
    qss = EXTRA_QSS.replace("@sel@", blend(t["accent"], t["panel2"], 0.28)) \
        .replace("@check@", check_icon_file(t["accent_text"])) \
        .replace("@grip_v@", grip_icon_file(t["muted"], True)).replace("@grip_h@", grip_icon_file(t["muted"], False))
    T.clear(); T.update(t)
    app = QApplication.instance()
    app.setStyle("Fusion")
    app.setPalette(fusion_palette(t))
    raw = fusion_qss(ui.QSS + qss)
    for k, v in t.items():
        raw = raw.replace(f"@{k}@", str(v))
    app.setStyleSheet(raw)


class WaterPlantWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Water Treatment Plant Simulator → MQTT")
        self.resize(1360, 820)
        self.plant = wtp.WaterPlant()
        self.values: dict = {}
        self.payloads: dict[str, str] = {}           # area -> last payload (JSON mode) / topic lines (flat)
        self.sim_t = time.time()
        self.sent = self.steps = 0
        self.timer = QTimer(self, timeout=self._tick)

        # ---- left: broker, simulation, export, log
        self.mqtt = MqttClient(self)
        self.panel = ThemedMqttPanel(self.mqtt, "water_plant", "nxlogger/demo/wtp1", show_retain=True)
        self.panel.topic.setToolTip("Topic prefix (base): the areas publish to <base>/<area> - "
                                    "make it unique on a public broker")
        self.panel.topic.textChanged.connect(lambda _: self._topics_changed())
        broker = QGroupBox("Broker"); bv = QVBoxLayout(broker); bv.setContentsMargins(4, 4, 4, 4)
        bv.addWidget(self.panel)
        s = QSettings()
        self.interval = QDoubleSpinBox(minimum=0.2, maximum=60, singleStep=0.5, suffix=" s", decimals=1)
        self.interval.setValue(float(s.value("water_plant/interval", 1.0)))
        self.interval.valueChanged.connect(self._interval_changed)
        self.speed = QComboBox(); self.speed.addItems(SPEEDS)
        self.speed.setCurrentText(str(s.value("water_plant/speed", "1 plant hour / minute (x60)")))
        self.mode = QComboBox()
        for k, text in MODES.items():
            self.mode.addItem(text, k)
        self.mode.setCurrentIndex(max(0, self.mode.findData(s.value("water_plant/mode", "json"))))
        self.mode.currentIndexChanged.connect(lambda _: self._topics_changed())
        self.run_btn = QPushButton("Start"); set_prop(self.run_btn, "primary", True)
        self.run_btn.setMinimumHeight(30); fixed_width(self.run_btn, "Start", "Stop")
        self.run_btn.clicked.connect(self._toggle_run)
        self.stats = label("Stopped", muted=True, small=True); self.stats.setWordWrap(True)
        sim = QGroupBox("Simulation"); sf = QFormLayout(sim)
        sf.addRow("Interval", self.interval); sf.addRow("Plant clock", self.speed); sf.addRow("Payload", self.mode)
        sf.addRow(self.run_btn); sf.addRow(self.stats)
        exp = QGroupBox("NX Logger"); ev = QVBoxLayout(exp)
        b1 = QPushButton("Export device (JSON)..."); b1.clicked.connect(self._export_device)
        b1.setToolTip("MQTT device with all 49 tags and this broker: NX Logger > Connectivity > Import...")
        b2 = QPushButton("Export tag list (CSV)..."); b2.clicked.connect(self._export_tags)
        b2.setToolTip("Tag list for an existing MQTT device: NX Logger > Tags > Import...")
        ev.addWidget(b1); ev.addWidget(b2)
        self.log = QPlainTextEdit(readOnly=True, maximumBlockCount=300)
        lg = QGroupBox("Log"); QVBoxLayout(lg).addWidget(self.log)
        self.mqtt.status.connect(self._log)
        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(6, 6, 6, 6)
        lv.addWidget(broker); lv.addWidget(sim); lv.addWidget(exp); lv.addWidget(lg, 1)
        left.setMinimumWidth(330); left.setMaximumWidth(400)

        # ---- right: copy bar, signal table, payload
        bar = QFrame(); bar.setObjectName("docBar")
        hl = QHBoxLayout(bar); hl.setContentsMargins(8, 4, 8, 4); hl.setSpacing(2)
        self.btn_topic = self._tool("Copy topic", lambda: self._copy_col(C_TOPIC), "Topics of the selected rows (Ctrl+C)")
        self.btn_path = self._tool("Copy JSON path", lambda: self._copy_col(C_PATH), "JSON paths of the selected rows")
        self.btn_both = self._tool("Copy topic + path", self._copy_both, "topic<TAB>path of the selected rows")
        self.btn_sub = self._tool("Copy subscription", lambda: self._copy(self._base() + "/#"),
                                  "<base>/# - subscribe to every signal (MQTT Explorer, mosquitto_sub ...)")
        for w in (self.btn_topic, self.btn_path, self.btn_both, None, self.btn_sub):
            hl.addWidget(vsep() if w is None else w)
        hl.addStretch(1)
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Filter signals")
        self.filter.setClearButtonEnabled(True); self.filter.setMaximumWidth(220)
        self.filter.textChanged.connect(lambda _: self._apply_filter())
        hl.addWidget(self.filter)
        self.table = QTreeWidget(); self.table.setHeaderLabels(COLS)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setUniformRowHeights(True); self.table.setAlternatingRowColors(True)
        for c, w in enumerate((210, 90, 60, 230, 130)):
            self.table.header().resizeSection(c, w)
        self.table.header().setSectionResizeMode(len(COLS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.headerItem().setTextAlignment(C_VAL, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.itemDoubleClicked.connect(self._double)
        self.table.itemSelectionChanged.connect(self._selected)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        sc = QShortcut(QKeySequence.StandardKey.Copy, self.table)
        sc.setContext(Qt.ShortcutContext.WidgetShortcut); sc.activated.connect(lambda: self._copy_col(C_TOPIC))
        self.rows: dict[tuple[str, str], QTreeWidgetItem] = {}
        self.area_items: dict[str, QTreeWidgetItem] = {}
        for area, title in wtp.AREAS.items():
            ai = QTreeWidgetItem([title]); ai.setData(0, Qt.ItemDataRole.UserRole, (area, ""))
            f = ai.font(0); f.setWeight(f.Weight.DemiBold); ai.setFont(0, f)
            self.table.addTopLevelItem(ai); ai.setExpanded(True)
            self.area_items[area] = ai
        for area, key, name, unit, dec, desc in wtp.SIGNALS:
            it = QTreeWidgetItem([name, "", unit, "", "", desc])
            it.setData(0, Qt.ItemDataRole.UserRole, (area, key))
            it.setTextAlignment(C_VAL, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it.setToolTip(C_TOPIC, "Double-click to copy"); it.setToolTip(C_PATH, "Double-click to copy")
            self.area_items[area].addChild(it)
            self.rows[(area, key)] = it
        self.hint = label("Double-click a cell to copy it · Ctrl+C copies the topics of the selected rows · "
                          "right-click for more", muted=True, small=True)
        self.hint.setContentsMargins(8, 2, 8, 2)
        self.payload = QPlainTextEdit(readOnly=True); self.payload.setMaximumBlockCount(200)
        self.payload.setStyleSheet("font-family: Consolas, Menlo, monospace;")
        self.payload_lbl = label("Payload - select a signal or area", small=True)
        copy_pl = QPushButton("Copy payload"); copy_pl.clicked.connect(lambda: self._copy(self.payload.toPlainText()))
        ph = QHBoxLayout(); ph.setContentsMargins(8, 4, 8, 0); ph.addWidget(self.payload_lbl, 1); ph.addWidget(copy_pl)
        pw = QWidget(); pv = QVBoxLayout(pw); pv.setContentsMargins(0, 0, 0, 0); pv.addLayout(ph); pv.addWidget(self.payload)
        top = QWidget(); tv = QVBoxLayout(top); tv.setContentsMargins(0, 0, 0, 0); tv.setSpacing(0)
        tv.addWidget(bar); tv.addWidget(self.table, 1); tv.addWidget(self.hint)
        rsplit = QSplitter(Qt.Orientation.Vertical); rsplit.addWidget(top); rsplit.addWidget(pw)
        rsplit.setSizes([600, 170]); rsplit.setChildrenCollapsible(False)
        split = QSplitter(); split.addWidget(left); split.addWidget(rsplit)
        split.setStretchFactor(1, 1); split.setChildrenCollapsible(False)
        self.setCentralWidget(split)
        self.toast = Toast(self)
        self._topics_changed()
        self._show_values(self.plant.step(0, self.sim_t))     # values before Start (preview)

    def _tool(self, text, slot, tip) -> QToolButton:
        b = QToolButton(); b.setText(text); b.setToolTip(tip)
        b.setIcon(make_icon("copy", T["text"])); b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        b.clicked.connect(slot)
        return b

    # ---- topics
    def _base(self) -> str:
        return self.panel.topic.text().strip().strip("/") or "nxlogger/demo/wtp1"

    def _flat(self) -> bool:
        return self.mode.currentData() == "flat"

    def _topics_changed(self):
        base, flat = self._base(), self._flat()
        for (area, key), it in self.rows.items():
            it.setText(C_TOPIC, f"{base}/{area}/{key}" if flat else f"{base}/{area}")
            it.setText(C_PATH, "" if flat else key)
        for area, ai in self.area_items.items():
            ai.setText(C_TOPIC, f"{base}/{area}" + ("/#" if flat else ""))
        s = QSettings(); s.setValue("water_plant/mode", self.mode.currentData())

    # ---- run
    def _interval_changed(self, v: float):
        QSettings().setValue("water_plant/interval", v)
        if self.timer.isActive():
            self.timer.setInterval(int(v * 1000))

    def _toggle_run(self):
        if self.timer.isActive():
            self.timer.stop()
            self.run_btn.setText("Start")
            self._log(f"Stopped after {self.steps} step(s), {self.sent} message(s) sent")
            self.stats.setText(f"Stopped · {self.sent} message(s) sent")
            return
        QSettings().setValue("water_plant/speed", self.speed.currentText())
        self.timer.start(int(self.interval.value() * 1000))
        self.run_btn.setText("Stop")
        self._log(f"Started: {self._base()}/{'<area>/<key>' if self._flat() else '<area>'} every "
                  f"{self.interval.value():g} s" + ("" if self.mqtt.is_connected else " - not connected: values only"))
        self._tick()

    def _tick(self):
        dt = self.interval.value() * SPEEDS[self.speed.currentText()]
        self.sim_t += dt
        values = self.plant.step(dt, self.sim_t)
        self.steps += 1
        msgs = wtp.messages(self._base(), values, time.time(), self._flat())
        connected = self.mqtt.is_connected
        if connected:
            qos, retain = self.panel.qos_value, self.panel.retain.isChecked()
            for topic, payload in msgs:
                self.sent += bool(self.mqtt.publish(topic, payload.encode(), qos, retain))
        self.payloads = {}
        for topic, payload in msgs:
            area = topic[len(self._base()) + 1:].split("/")[0]
            self.payloads[area] = (self.payloads.get(area, "") + f"{topic}  {payload}\n") if self._flat() else payload
        self._show_values(values)
        self.stats.setText(f"Plant clock {time.strftime('%Y-%m-%d %H:%M', time.localtime(self.sim_t))} · "
                           f"step {self.steps} · {self.sent} message(s) sent" +
                           ("" if connected else " · not connected (values only)"))

    def _show_values(self, values: dict):
        self.values = values
        dec = {(a, k): d for a, k, _, _, d, _ in wtp.SIGNALS}
        bad = QBrush(QColor(T["bad"]))
        for (area, key), it in self.rows.items():
            v = values.get(area, {}).get(key)
            if v is None:
                continue
            it.setText(C_VAL, f"{v:.{dec[(area, key)]}f}")
            alarm = (area, key) in (("treated", "turbidity"),) and v > 1 or \
                (area, key) == ("treated", "chlorine") and v < 0.2 or (area, key) == ("pumps", "p1_run") and v == 0
            it.setForeground(C_VAL, bad if alarm else QBrush())
        self._selected()

    # ---- selection, copy
    def _keys(self) -> list[tuple[str, str]]:
        return [tuple(it.data(0, Qt.ItemDataRole.UserRole)) for it in self.table.selectedItems()]

    def _selected(self):
        keys = self._keys()
        area = keys[0][0] if keys else None
        if area is None:
            return
        self.payload_lbl.setText(f"Payload of {wtp.AREAS[area]}  ·  {self.area_items[area].text(C_TOPIC)}")
        text = self.payloads.get(area, "")
        if text and not self._flat():
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        if self.payload.toPlainText() != text:
            self.payload.setPlainText(text or "(no message yet - press Start)")

    def _copy(self, text: str):
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        first = text.splitlines()[0]
        n = len(text.splitlines())
        self.toast.popup(f"Copied: {first}" + (f"  (+{n - 1} more)" if n > 1 else ""))

    def _copy_col(self, col: int):
        items = [it for it in self.table.selectedItems() if it.text(col)]
        self._copy("\n".join(dict.fromkeys(it.text(col) for it in items)))     # unique, in order

    def _copy_both(self):
        self._copy("\n".join(f"{it.text(C_TOPIC)}\t{it.text(C_PATH)}" for it in self.table.selectedItems()
                             if it.data(0, Qt.ItemDataRole.UserRole)[1]))

    def _double(self, it, col):
        self._copy(it.text(col if it.text(col) else C_TOPIC))

    def _menu(self, pos):
        it = self.table.itemAt(pos)
        if it is not None and not it.isSelected():
            self.table.clearSelection(); it.setSelected(True)
        m = QMenu(self)
        m.addAction("Copy topic", lambda: self._copy_col(C_TOPIC))
        m.addAction("Copy JSON path", lambda: self._copy_col(C_PATH)).setEnabled(not self._flat())
        m.addAction("Copy topic + path", self._copy_both)
        m.addAction("Copy value", lambda: self._copy_col(C_VAL))
        m.addAction("Copy row", lambda: self._copy("\n".join("\t".join(x.text(c) for c in range(len(COLS)))
                                                             for x in self.table.selectedItems())))
        m.addSeparator()
        m.addAction("Copy subscription (<base>/#)", lambda: self._copy(self._base() + "/#"))
        m.addAction("Copy payload of the area", lambda: self._copy(self.payload.toPlainText()))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _apply_filter(self):
        text = self.filter.text().strip().lower()
        for area, ai in self.area_items.items():
            shown = 0
            for i in range(ai.childCount()):
                it = ai.child(i)
                hide = bool(text) and not any(text in it.text(c).lower() for c in (0, C_TOPIC, C_PATH, 5)) \
                    and text not in ai.text(0).lower()
                it.setHidden(hide); shown += not hide
            ai.setHidden(shown == 0)

    # ---- export
    def _device(self):
        return wtp.nx_device(self._base(), self.panel.host.currentText().strip(), self.panel.port.value(), self._flat())

    def _export_device(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export NX Logger device", os.path.join(
            os.path.expanduser("~"), "water_plant_device.json"), "JSON (*.json)")
        if path:
            wtp.export_device(path, self._device())
            self._log(f"Device with {len(wtp.SIGNALS)} tags -> {path} (NX Logger: Connectivity > Import...)")
            self.toast.popup("Device exported - NX Logger: Connectivity > Import...")

    def _export_tags(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export tag list", os.path.join(
            os.path.expanduser("~"), "water_plant_tags.csv"), "CSV (*.csv)")
        if path:
            wtp.export_tags(path, self._device())
            self._log(f"{len(wtp.SIGNALS)} tags -> {path} (NX Logger: Tags > MQTT device > Import...)")
            self.toast.popup("Tag list exported - NX Logger: Tags > Import...")

    def _log(self, s: str):
        self.log.appendPlainText(time.strftime("%H:%M:%S  ") + s)

    def closeEvent(self, e):
        self.timer.stop()
        self.mqtt.disconnect_from()
        super().closeEvent(e)


def main() -> int:
    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setApplicationName(APP_NAME)    # shares broker settings and theme with the logger
    app = QApplication(sys.argv)
    apply_app_theme(str(QSettings().value("theme", "dark")))
    w = WaterPlantWindow(); w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
