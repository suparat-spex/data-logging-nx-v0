"""
sim_publisher.py - 4-channel signal simulator that publishes binary waveform
blocks over MQTT (stand-in for the ESP32-S3 DAQ).

Run:  python -m nxlogger.sim
"""
from __future__ import annotations

import sys
import time

import numpy as np
from PySide6.QtCore import QCoreApplication, QTimer, Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                               QGridLayout, QGroupBox, QLabel, QMainWindow, QPlainTextEdit,
                               QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget)
import pyqtgraph as pg

from nxlogger import APP_NAME, ORG_NAME
from nxlogger.comms.mqtt_client import MqttClient
from nxlogger.core import wavepacket as wp
from nxlogger.ui.widgets.mqtt_panel import MqttPanel


N_CH = 4
COLORS = ["#e8b100", "#2f9bf0", "#e5484d", "#30a46c"]   # CH1..CH4
WAVEFORMS = ["Sine", "Square", "Triangle", "Sawtooth", "Pulse", "DC", "Noise only"]
NOISES = ["None", "Gaussian", "Uniform", "Impulse", "Mains hum 50 Hz"]
DEFAULTS = [  # waveform, amp, freq, offset
    ("Sine", 1.0, 5.0, 0.0),
    ("Sawtooth", 2.0, 2.0, 0.0),
    ("Square", 1.5, 1.0, 0.5),
    ("Triangle", 3.0, 0.5, -1.0),
]


class ChannelGen:
    """Phase-continuous generator for one channel (frequency changes don't jump)."""

    def __init__(self, seed: int):
        self.phase = 0.0                         # cycles
        self.rng = np.random.default_rng(seed)

    def block(self, n: int, fs: float, idx0: int, cfg: dict) -> np.ndarray:
        f = cfg["freq"]
        cyc = self.phase + f * np.arange(n) / fs + cfg["phase"] / 360.0
        self.phase = (self.phase + f * n / fs) % 1.0
        x = np.mod(cyc, 1.0)                     # position inside the period, 0..1
        a = cfg["amp"]
        w = cfg["wave"]
        if w == "Sine":
            y = a * np.sin(2 * np.pi * cyc)
        elif w == "Square":
            y = np.where(x < 0.5, a, -a)
        elif w == "Triangle":
            y = a * (4 * np.abs(x - 0.5) - 1)
        elif w == "Sawtooth":
            y = a * (2 * x - 1)
        elif w == "Pulse":
            y = np.where(x < cfg["duty"] / 100.0, a, 0.0)
        else:                                    # DC / Noise only
            y = np.full(n, a if w == "DC" else 0.0)
        y = y + cfg["offset"]

        lvl, nz = cfg["noise_lvl"], cfg["noise"]
        if lvl > 0 and nz != "None":
            if nz == "Gaussian":
                y += self.rng.normal(0, lvl, n)
            elif nz == "Uniform":
                y += self.rng.uniform(-lvl, lvl, n)
            elif nz == "Impulse":
                hits = self.rng.random(n) < 0.005
                y += hits * self.rng.choice([-lvl, lvl], n)
            elif nz.startswith("Mains"):
                t = (idx0 + np.arange(n)) / fs
                y += lvl * np.sin(2 * np.pi * 50 * t)
        return y


class ChannelRow:
    """Widgets for one channel in the grid."""

    def __init__(self, grid: QGridLayout, row: int, ch: int):
        wave, amp, freq, off = DEFAULTS[ch]
        self.enable = QCheckBox(f"CH{ch + 1}", checked=True)
        self.enable.setStyleSheet(f"QCheckBox {{ color:{COLORS[ch]}; font-weight:600; }}")
        self.wave = QComboBox(); self.wave.addItems(WAVEFORMS); self.wave.setCurrentText(wave)
        self.amp = _dspin(0, 1000, amp, 0.1)
        self.freq = _dspin(0, 500, freq, 0.5, " Hz")
        self.offset = _dspin(-1000, 1000, off, 0.1)
        self.phase = _dspin(-360, 360, 0, 15, "°")
        self.duty = _dspin(1, 99, 25, 5, " %")
        self.noise = QComboBox(); self.noise.addItems(NOISES)
        self.noise_lvl = _dspin(0, 100, 0.05, 0.05)
        self.range = _dspin(0.01, 10000, 10, 1)
        self.range.setPrefix("±")
        self.range.setToolTip("Full-scale range. Values beyond it saturate (clip) like an ADC.\n"
                              "Resolution = range / 32767")
        for col, w in enumerate([self.enable, self.wave, self.amp, self.freq, self.offset,
                                 self.phase, self.duty, self.noise, self.noise_lvl, self.range]):
            grid.addWidget(w, row, col)
        self.wave.currentTextChanged.connect(self._sync)
        self._sync(wave)

    def _sync(self, w):
        periodic = w not in ("DC", "Noise only")
        self.freq.setEnabled(periodic); self.phase.setEnabled(periodic)
        self.duty.setEnabled(w == "Pulse")

    def config(self) -> dict:
        return dict(wave=self.wave.currentText(), amp=self.amp.value(),
                    freq=self.freq.value(), offset=self.offset.value(),
                    phase=self.phase.value(), duty=self.duty.value(),
                    noise=self.noise.currentText(), noise_lvl=self.noise_lvl.value(),
                    range=self.range.value())


class PublisherWindow(QMainWindow):
    PREVIEW_S = 2.0

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Waveform Simulator → MQTT")
        self.resize(1300, 820)

        self.mqtt = MqttClient(self)
        self.mqtt_panel = MqttPanel(self.mqtt, "publisher", "spexth/daq/demo/wave",
                                    show_retain=False)

        # ---- acquisition settings
        self.fs = QSpinBox(minimum=10, maximum=20000, value=1000, singleStep=100, suffix=" S/s")
        self.block = QSpinBox(minimum=10, maximum=5000, value=100, singleStep=10,
                              suffix=" samples")
        self.block.setToolTip("Samples per channel in each MQTT message.\n"
                              "1000 S/s with 100 samples = 10 messages/s")
        self.rate_lbl = QLabel()
        self.run_btn = QPushButton("▶  Start", clicked=self.toggle_run)
        self.run_btn.setMinimumHeight(34)
        self.run_btn.setStyleSheet("font-weight:600;")
        acq = QGroupBox("Acquisition")
        af = QFormLayout(acq)
        af.addRow("Sample rate", self.fs)
        af.addRow("Block size", self.block)
        af.addRow("", self.rate_lbl)
        af.addRow(self.run_btn)
        self.fs.valueChanged.connect(self._update_rate_lbl)
        self.block.valueChanged.connect(self._update_rate_lbl)
        self._update_rate_lbl()

        # ---- stats
        self.stats = QLabel()
        self.stats.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.stats.setStyleSheet("font-family: Consolas, Menlo, monospace;")
        sg = QGroupBox("Statistics"); QVBoxLayout(sg).addWidget(self.stats)

        self.log = QPlainTextEdit(readOnly=True, maximumBlockCount=300)
        lg = QGroupBox("Log"); QVBoxLayout(lg).addWidget(self.log)
        self.mqtt.status.connect(self._log)

        left = QWidget(); lv = QVBoxLayout(left)
        lv.addWidget(self.mqtt_panel); lv.addWidget(acq); lv.addWidget(sg); lv.addWidget(lg, 1)
        left.setMaximumWidth(380)

        # ---- channel grid
        cg = QGroupBox("Channels")
        grid = QGridLayout(cg)
        for col, h in enumerate(["Enable", "Waveform", "Amplitude", "Frequency", "Offset",
                                 "Phase", "Duty", "Noise", "Noise level", "Range (FS)"]):
            lbl = QLabel(f"<b>{h}</b>"); grid.addWidget(lbl, 0, col)
        self.rows = [ChannelRow(grid, ch + 1, ch) for ch in range(N_CH)]
        self.gens = [ChannelGen(seed=ch) for ch in range(N_CH)]

        # ---- preview plot
        pg.setConfigOptions(antialias=False)
        self.plot = pg.PlotWidget(title="Local preview (what is being sent, after quantization)")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "Time", "s")
        self.plot.addLegend(offset=(10, 10))
        self.curves = [self.plot.plot(pen=pg.mkPen(COLORS[ch], width=1.5), name=f"CH{ch + 1}")
                       for ch in range(N_CH)]

        right = QWidget(); rv = QVBoxLayout(right)
        rv.addWidget(cg); rv.addWidget(self.plot, 1)
        split = QSplitter(); split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)

        # ---- runtime state
        self.timer = QTimer(self, interval=10, timerType=Qt.PreciseTimer, timeout=self._tick)
        self.stat_timer = QTimer(self, interval=500, timeout=self._update_stats)
        self.running = False
        self._reset_counters()
        self._update_stats()

    # ------------------------------------------------------------- control
    def _update_rate_lbl(self):
        mps = self.fs.value() / self.block.value()
        dur = self.block.value() / self.fs.value() * 1000
        self.rate_lbl.setText(f"→ {mps:.1f} msg/s, {dur:.0f} ms per block")

    def _reset_counters(self):
        self.seq = 0
        self.sample_idx = 0
        self.sent = self.failed = self.bytes_sent = 0
        self.last_size = 0
        self._bytes_win = 0
        self._win_t = time.perf_counter()
        self.bps = 0.0
        npts = max(int(self.PREVIEW_S * self.fs.value()), 2 * self.block.value())
        self.prev_y = np.full((N_CH, npts), np.nan)
        self.prev_t = (np.arange(npts) - npts) / self.fs.value()

    def toggle_run(self):
        if self.running:
            self.running = False
            self.timer.stop(); self.stat_timer.stop()
            self.run_btn.setText("▶  Start")
            self.fs.setEnabled(True); self.block.setEnabled(True)
            self._log("Stopped")
            return
        self._reset_counters()
        for g in self.gens:
            g.phase = 0.0
        self.running = True
        self.fs.setEnabled(False); self.block.setEnabled(False)
        self.run_btn.setText("■  Stop")
        self.t_start_perf = time.perf_counter()
        self.t_start_unix = time.time()
        self.timer.start(); self.stat_timer.start()
        if not self.mqtt.is_connected:
            self._log("Generating (preview only) - connect MQTT to publish")
        else:
            self._log(f"Publishing to '{self.mqtt_panel.topic.text()}'")

    # ---------------------------------------------------------- generation
    def _tick(self):
        """Emit as many whole blocks as real time allows (keeps exactly fs on average)."""
        fs, n = self.fs.value(), self.block.value()
        target = int((time.perf_counter() - self.t_start_perf) * fs)
        if target - self.sample_idx > 5 * fs:           # we were suspended: skip ahead
            self.sample_idx = target - n
        emitted = False
        while target - self.sample_idx >= n:
            self._emit_block(fs, n)
            emitted = True
        if emitted:                                     # redraw once per tick, not per block
            for ch in range(N_CH):
                self.curves[ch].setData(self.prev_t, self.prev_y[ch], connect="finite")

    def _emit_block(self, fs: int, n: int):
        idx0 = self.sample_idx
        t0 = self.t_start_unix + idx0 / fs
        chans, data, scales = [], [], []
        all_y = np.full((N_CH, n), np.nan)
        for ch, (row, gen) in enumerate(zip(self.rows, self.gens)):
            cfg = row.config()
            y = gen.block(n, fs, idx0, cfg)                 # keep generators running
            if not row.enable.isChecked():
                continue
            sc = wp.scale_for_range(cfg["range"])
            chans.append(ch); data.append(y); scales.append(sc)
            all_y[ch] = wp.quantize(y, sc) * sc               # preview = what receiver sees

        if chans and self.mqtt.is_connected:
            pkt = wp.encode(self.seq, idx0, t0, 1.0 / fs, chans, np.vstack(data), scales)
            if self.mqtt.publish(self.mqtt_panel.topic.text(), pkt, self.mqtt_panel.qos_value):
                self.sent += 1
                self.bytes_sent += len(pkt); self._bytes_win += len(pkt)
                self.last_size = len(pkt)
            else:
                self.failed += 1
            self.seq = (self.seq + 1) & 0xFFFF
        self.sample_idx += n

        # scroll preview buffer
        self.prev_y = np.roll(self.prev_y, -n, axis=1)
        self.prev_y[:, -n:] = all_y

    # -------------------------------------------------------------- stats
    def _update_stats(self):
        now = time.perf_counter()
        dt = now - self._win_t
        if dt > 0 and self.running:
            self.bps = self._bytes_win / dt
        self._bytes_win, self._win_t = 0, now
        n_on = sum(r.enable.isChecked() for r in self.rows)
        n = self.block.value()
        dbl = n_on * n * 8
        ratio = f"{dbl / self.last_size:.1f}×" if self.last_size else "-"
        self.stats.setText(
            f"Packets sent : {self.sent}\n"
            f"Failed       : {self.failed}\n"
            f"Seq          : {self.seq}\n"
            f"Sample index : {self.sample_idx}\n"
            f"Packet size  : {self.last_size} B\n"
            f"Same as DBL  : {dbl} B  (saving {ratio})\n"
            f"Throughput   : {self.bps / 1024:.2f} kB/s\n"
            f"Total        : {self.bytes_sent / 1024:.1f} kB")

    def _log(self, s: str):
        self.log.appendPlainText(time.strftime("%H:%M:%S  ") + s)

    def closeEvent(self, e):
        self.timer.stop()
        self.mqtt.disconnect_from()
        super().closeEvent(e)


def _dspin(lo, hi, val, step, suffix="") -> QDoubleSpinBox:
    s = QDoubleSpinBox(minimum=lo, maximum=hi, value=val, singleStep=step, decimals=3)
    if suffix:
        s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


def main() -> int:
    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setApplicationName(APP_NAME)   # shares the broker settings with the logger
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = PublisherWindow(); w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
