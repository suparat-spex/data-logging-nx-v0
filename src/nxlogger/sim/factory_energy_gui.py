"""
factory_energy_gui.py - window for the factory energy simulator (factory_energy.py): Modbus TCP
port, factory clock, start / stop, the live values of the 11 meters, the NX Logger device export.

  bar     Port, Factory clock, Start / Stop, Export device..., status
  table   meter (unit id, group) - kW, PF, V, A, kWh, THD
  log

The NX Logger side: Start Page > New project > Factory energy demo (Modbus TCP 127.0.0.1:5020),
then Connect.

Run:  python -m nxlogger.sim.factory_energy_gui        (or: nxlogger-energy-gui)
"""
from __future__ import annotations

import sys
import time

from PySide6.QtCore import QCoreApplication, QSettings, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                               QMainWindow, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from nxlogger import APP_NAME, ORG_NAME
from nxlogger.sim import factory_energy as fe
from nxlogger.sim.water_plant_gui import apply_app_theme
from nxlogger.ui.theme.ui_kit import label, set_prop

COLS = ["Meter", "Unit", "Group", "kW", "PF", "V", "A", "kWh", "THD %"]
SPEEDS = {"Real time (x1)": 1.0, "x10": 10.0, "1 factory hour / minute (x60)": 60.0,
          "1 factory day / 4 min (x360)": 360.0}


class FactoryEnergyWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Factory energy simulator - NX Logger")
        self.resize(900, 620)
        self.factory, self.server = fe.Factory(), None
        self.sim_t = self.last = time.time()
        s = QSettings()
        bar = QFrame(); bar.setObjectName("docBar")
        h = QHBoxLayout(bar); h.setContentsMargins(8, 4, 8, 4); h.setSpacing(6)
        self.port = QSpinBox(minimum=1, maximum=65535, value=int(s.value("energy_sim/port", fe.PORT)))
        self.speed = QComboBox(); self.speed.addItems(list(SPEEDS))
        self.speed.setCurrentText(str(s.value("energy_sim/speed", "Real time (x1)")))
        self.speed.currentTextChanged.connect(lambda t_: QSettings().setValue("energy_sim/speed", t_))
        self.run_btn = QPushButton("Start"); self.run_btn.setProperty("primary", True)
        self.run_btn.clicked.connect(self._toggle)
        exp = QPushButton("Export device..."); exp.clicked.connect(self._export)
        self.status = label("Stopped", small=True); self.status.setObjectName("statusMsg")
        for w in (label("Port", muted=True), self.port, label("Factory clock", muted=True), self.speed,
                  self.run_btn, exp):
            h.addWidget(w)
        h.addStretch(1); h.addWidget(self.status)
        self.table = QTreeWidget(); self.table.setHeaderLabels(COLS); self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.rows = {}
        for unit, key, name, group, *_ in fe.METERS:
            it = QTreeWidgetItem([name, str(unit), group] + [""] * 6)
            for c in range(3, len(COLS)):
                it.setTextAlignment(c, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.addTopLevelItem(it)
            self.rows[key] = it
        self.clock = label("", muted=True, small=True)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(500)
        top = QWidget(); tv = QVBoxLayout(top); tv.setContentsMargins(0, 0, 0, 0)
        tv.addWidget(self.table, 1); tv.addWidget(self.clock)
        split = QSplitter(Qt.Orientation.Vertical); split.addWidget(top); split.addWidget(self.log)
        split.setSizes([460, 120])
        c = QWidget(); v = QVBoxLayout(c); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(bar); v.addWidget(split, 1)
        self.setCentralWidget(c)
        self.timer = QTimer(self, interval=1000, timeout=self._tick)
        self.timer.start()                       # values move even when stopped (nothing served)
        self._log("NX Logger: Start Page > New project > Factory energy demo, then Connect.")
        self._tick()

    def _log(self, text: str):
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {text}")

    def _toggle(self):
        if self.server is None:
            self.server = fe.Server("127.0.0.1", self.port.value())
            self.server.update(self.factory.step(0, self.sim_t))
            try:
                self.server.start()
            except Exception as e:                   # port in use, pymodbus missing ...
                self.server = None
                self._log(f"Cannot start the server: {e}")
                return
            QSettings().setValue("energy_sim/port", self.port.value())
            self._log(f"Serving 11 meters on Modbus TCP 127.0.0.1:{self.port.value()} (units 1-11)")
        else:
            self.server.stop(); self.server = None
            self._log("Stopped")
        running = self.server is not None
        self.run_btn.setText("Stop" if running else "Start")
        self.port.setEnabled(not running)
        set_prop(self.status, "level", "ok" if running else "muted")
        self.status.setText(f"● Serving 127.0.0.1:{self.port.value()}" if running else "Stopped")

    def _tick(self):
        now = time.time()
        speed = SPEEDS.get(self.speed.currentText(), 1.0)
        dt = (now - self.last) * speed
        self.sim_t += dt
        self.last = now
        values = self.factory.step(dt, self.sim_t)
        if self.server is not None:
            self.server.update(values)
        for key, it in self.rows.items():
            m = values[key]
            for c, (f, dec) in enumerate((("kw", 1), ("pf", 3), ("v", 1), ("i", 1), ("kwh", 0), ("thd", 1)), 3):
                it.setText(c, f"{m[f]:,.{dec}f}")
        self.clock.setText(f"Factory clock {time.strftime('%a %Y-%m-%d %H:%M:%S', time.localtime(self.sim_t))}"
                           f" · incomer {values['main']['kw']:.0f} kW · solar {values['solar']['kw']:.0f} kW")

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export NX Logger device", "factory_energy.json",
                                              "Device JSON (*.json)")
        if path:
            dev = fe.nx_device("127.0.0.1", self.port.value())
            fe.export_device(path, dev)
            self._log(f"Device with {len(dev.tags)} tags -> {path}  (NX Logger: Connectivity > Import...)")

    def closeEvent(self, e):
        if self.server is not None:
            self.server.stop()
        super().closeEvent(e)


def main() -> int:
    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    app = QApplication.instance() or QApplication(sys.argv)
    apply_app_theme(str(QSettings().value("theme", "dark")))
    w = FactoryEnergyWindow(); w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
