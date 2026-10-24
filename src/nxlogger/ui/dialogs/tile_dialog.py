"""All settings of one view component: graph, text, number, gauge, LED, statistics / tag / alarm table."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from nxlogger.core import analysis as an
from nxlogger.core.view_model import (ALARM_SOURCES, ALARM_TILE_COLS, COLORMAPS, GAUGE_STYLES,
                                      STAT_TILE_COLS, TAG_TILE_COLS, TEXT_STYLES, WIN_MODES, WIN_UNITS,
                                      axis_defaults, tile_title)
from nxlogger.ui.dialogs.common import ChannelPicker, ColorButton, form_page, spin
from nxlogger.ui.theme.ui_kit import label, set_prop
from nxlogger.ui.widgets.gauge import GaugeWidget


QUANTITIES = {k: an.STAT_LABEL[k] for k in an.STAT_KEYS}     # numeric / gauge: what to show


class TileDialog(QDialog):
    """
    Edit a copy of the tile config; result_cfg() returns it.
    choices = [(topic_id, index, label, colour, unit)] (channels and variables)
    """

    def __init__(self, parent, cfg: dict, choices):
        super().__init__(parent)
        self.cfg, self.choices = dict(cfg), choices
        self.units = {(tid, idx): unit for tid, idx, _, _, unit in choices}
        kind = self.kind = cfg["kind"]
        self.ptype = cfg.get("plot", "time")          # a graph's type is fixed: one dialog per type
        self.setWindowTitle(tile_title(cfg))
        self.setMinimumWidth(560)
        self.tabs = QTabWidget()
        build = {"plot": self._plot, "text": self._text, "value": self._value, "gauge": self._gauge,
                 "led": self._led, "stats": self._stats, "tags": self._tags, "alarms": self._alarms}[kind]
        build()
        if kind != "text":
            self.tabs.addTab(self._appearance(), "Appearance")
        else:
            self.tabs.addTab(self._appearance(text=True), "Appearance")
        self.err = QLabel(); self.err.setObjectName("statusMsg"); self.err.setWordWrap(True)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        set_prop(bb.button(QDialogButtonBox.StandardButton.Ok), "primary", True)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        v = QVBoxLayout(self)
        v.addWidget(self.tabs, 1); v.addWidget(self.err); v.addWidget(bb)

    # ================================================================ shared rows
    def _title_rows(self, f, placeholder="optional - default: the channel name"):
        self.title = QLineEdit(self.cfg.get("title", ""))
        self.title.setPlaceholderText(placeholder)
        self.show_title = QCheckBox("Show title bar", checked=bool(self.cfg.get("show_title", True)))
        r = QHBoxLayout(); r.addWidget(self.title, 1); r.addWidget(self.show_title)
        f.addRow("Title", r)

    def _quantity_rows(self, f):
        self.quantity = QComboBox()
        for k, text in QUANTITIES.items():
            self.quantity.addItem(text, k)
            if k in an.STAT_HELP:
                self.quantity.setItemData(self.quantity.count() - 1, an.STAT_HELP[k], 3)   # tooltip
        self.quantity.setCurrentIndex(max(self.quantity.findData(self.cfg.get("quantity", "value")), 0))
        f.addRow("Show", self.quantity)
        win = self._window_rows(f)
        self.quantity.currentIndexChanged.connect(
            lambda _: win.setEnabled(self.quantity.currentData() != "value"))
        win.setEnabled(self.quantity.currentData() != "value")

    def _window_rows(self, f) -> QWidget:
        """Statistics window: length + unit (s / min / h / samples) + mode (moving / block)."""
        c = self.cfg
        self.win_n = spin(float(c.get("win_n") or 0), 0, 1e7, 1)
        self.win_n.setSpecialValueText("Quick View time window")
        self.win_n.setToolTip("Window length; 0 = the Quick View time window. Longer windows are "
                              "buffered by NX Logger itself (independent of the plots).")
        self.win_unit = QComboBox()
        for k, (text, _) in WIN_UNITS.items():
            self.win_unit.addItem(text, k)
        self.win_unit.setCurrentIndex(max(0, self.win_unit.findData(c.get("win_unit", "s"))))
        self.win_mode = QComboBox()
        for k, text in WIN_MODES.items():
            self.win_mode.addItem(text, k)
        self.win_mode.setCurrentIndex(max(0, self.win_mode.findData(c.get("win_mode", "moving"))))
        box = QWidget(); r = QHBoxLayout(box); r.setContentsMargins(0, 0, 0, 0)
        r.addWidget(self.win_n, 1); r.addWidget(self.win_unit)
        f.addRow("Statistics over", box)
        f.addRow("Window", self.win_mode)
        return box

    def _window_cfg(self) -> dict:
        return {"win_n": self.win_n.value(), "win_unit": self.win_unit.currentData(),
                "win_mode": self.win_mode.currentData(), "window_s": 0.0}

    @staticmethod
    def _checks(names, chosen, per_row=5, tips=None):
        w = QWidget(); g = QGridLayout(w); g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(14); g.setVerticalSpacing(6)
        boxes = {}
        for i, n in enumerate(names):
            boxes[n] = QCheckBox(n, checked=n in chosen)
            if tips and tips.get(n):
                boxes[n].setToolTip(tips[n])
            g.addWidget(boxes[n], i // per_row, i % per_row)
        return w, boxes

    # ================================================================ graph
    def _plot(self):
        c = self.cfg
        w, f = form_page()
        self._title_rows(f, "optional")
        k = self.ptype
        self.legend = QCheckBox("Legend visible", checked=bool(c.get("legend", True)))
        self.stack = QCheckBox("Stack plots (one plot per tag, shared X axis)",
                               checked=bool(c.get("stack", False)))
        self.grid = QCheckBox("Grid lines", checked=bool(c.get("grid", True)))
        for x in (self.legend, self.stack, self.grid):
            if k != "stft" or x is self.grid:
                f.addRow("", x)
        self.lw = spin(c.get("line_width", 1.5), 0.5, 6, 1)
        if k != "stft":
            f.addRow("Line width", self.lw)
        self.span = spin(float(c.get("span_s") or 0), 0, 7200, 1)
        self.span.setSuffix(" s"); self.span.setSpecialValueText("Quick View time window")
        self.span.setToolTip("How much history the trend shows (number tags keep 2 h at a 1 s scan)")
        if k == "time":
            f.addRow("Time span", self.span)
        self.tabs.addTab(w, "General")

        chosen = c.get("channels", [])
        self.picker = ChannelPicker(self.choices, chosen[:1] if k == "stft" else chosen, multi=k != "stft")
        cw = QWidget(); cv = QVBoxLayout(cw); cv.addWidget(self.picker)
        cv.addWidget(label("Variables are listed with the devices - add them in Project Files → Variables.",
                           muted=True, small=True))
        self.tabs.addTab(cw, "Tag" if k == "stft" else "Tags")

        aw, af = form_page()
        self.axis_labels = QCheckBox("Axis labels visible", checked=bool(c.get("axis_labels", True)))
        af.addRow("", self.axis_labels)
        self.x_label, self.y_label = QLineEdit(), QLineEdit()
        rst = QPushButton("Defaults"); rst.setToolTip("Reset both labels to the defaults of the graph type")
        rst.clicked.connect(lambda: (self.x_label.setText(""), self.y_label.setText(""), self._axis_hint()))
        xr = QHBoxLayout(); xr.addWidget(self.x_label, 1); xr.addWidget(rst)
        af.addRow("X axis label", xr)
        af.addRow("Y axis label", self.y_label)
        self.x_label.setText(c.get("x_label", "")); self.y_label.setText(c.get("y_label", ""))
        self.x_mode = QComboBox()
        self.x_mode.addItem("Time - the Quick View time window, scrolling", "time")
        self.x_mode.addItem("Samples - fixed axis 0 … N, newest N samples", "samples")
        self.x_mode.setCurrentIndex(max(self.x_mode.findData(c.get("x_mode", "time")), 0))
        self.x_samples = QSpinBox(minimum=10, maximum=1_000_000, value=int(c.get("x_samples", 1000)),
                                  singleStep=100, suffix=" samples")
        self.x_samples.setToolTip("Plot period: the X axis stays 0 … N, so the plot area does not move")
        if k == "time":
            af.addRow("X axis", self.x_mode)
            af.addRow("Plot period", self.x_samples)
            self.x_mode.currentIndexChanged.connect(
                lambda _: (self.x_samples.setEnabled(self.x_mode.currentData() == "samples"), self._axis_hint()))
            self.x_samples.setEnabled(self.x_mode.currentData() == "samples")
        self.y_auto = QCheckBox("Auto scale Y", checked=bool(c.get("y_auto", True)))
        self.y_min, self.y_max = spin(c.get("y_min", -10)), spin(c.get("y_max", 10))
        yr = QHBoxLayout(); yr.addWidget(self.y_min); yr.addWidget(QLabel("to")); yr.addWidget(self.y_max)
        if k in ("time", "fft"):
            af.addRow("", self.y_auto)
            af.addRow("Y range", yr)
        self.y_auto.toggled.connect(lambda on: (self.y_min.setDisabled(on), self.y_max.setDisabled(on)))
        self.y_min.setDisabled(self.y_auto.isChecked()); self.y_max.setDisabled(self.y_auto.isChecked())
        self.log_x = QCheckBox("Logarithmic X", checked=bool(c.get("log_x")))
        self.log_y = QCheckBox("Logarithmic Y", checked=bool(c.get("log_y")))
        lr = QHBoxLayout(); lr.addWidget(self.log_x); lr.addWidget(self.log_y); lr.addStretch(1)
        if k == "fft":
            af.addRow("Scale", lr)
        elif k == "time":
            af.addRow("", self.log_y)
        if k == "time":
            af.addRow(label("Drag a box to zoom, double-click the plot to follow live data again.",
                            muted=True, small=True))
        self.tabs.addTab(aw, "Axes")

        fw, ff = form_page()
        self.fft_window = QComboBox()
        for key, text in an.WINDOWS.items():
            self.fft_window.addItem(text, key)
        self.fft_window.setCurrentIndex(max(self.fft_window.findData(c.get("fft_window", "hann")), 0))
        self.fft_window.setToolTip("Hann: general purpose · Flat top: accurate tone amplitudes · "
                                   "Rectangular: transients")
        self.fft_scale = QComboBox()
        for key, text in an.SPEC_SCALES.items():
            self.fft_scale.addItem(text, key)
        self.fft_scale.setCurrentIndex(max(self.fft_scale.findData(c.get("fft_scale", "rms")), 0))
        self.fft_nfft = QComboBox()
        for n in an.NFFT_CHOICES:
            self.fft_nfft.addItem(f"{n:,} samples", n)
        self.fft_nfft.setCurrentIndex(max(self.fft_nfft.findData(int(c.get("fft_nfft", 4096))), 0))
        self.fft_avg = QSpinBox(minimum=1, maximum=100, value=int(c.get("fft_avg", 1)))
        self.fft_avg.setToolTip("Linear average of the last N spectra (smoother noise floor)")
        self.fft_fmax = spin(c.get("fft_fmax", 0), 0, 1e6, 1); self.fft_fmax.setSuffix(" Hz")
        self.fft_fmax.setSpecialValueText("Nyquist (fs / 2)")
        ff.addRow("Window", self.fft_window)
        ff.addRow("Scaling", self.fft_scale)
        ff.addRow("FFT length", self.fft_nfft)
        ff.addRow("Averages", self.fft_avg)
        ff.addRow("Max frequency", self.fft_fmax)
        ff.addRow(label("Resolution Δf = fs / FFT length. The newest samples of the live buffer are used.",
                        muted=True, small=True))
        sw, sf = form_page()
        self.stft_n = QComboBox()
        for n in an.NFFT_CHOICES[:7]:
            self.stft_n.addItem(f"{n:,} samples", n)
        self.stft_n.setCurrentIndex(max(self.stft_n.findData(int(c.get("stft_nperseg", 512))), 0))
        self.stft_ov = QSpinBox(minimum=0, maximum=95, value=int(c.get("stft_overlap", 50)), suffix=" %")
        self.stft_cmap = QComboBox(); self.stft_cmap.addItems(COLORMAPS)
        self.stft_cmap.setCurrentText(c.get("stft_cmap", "viridis"))
        self.stft_span = spin(c.get("stft_span_s", 10), 1, 60, 1); self.stft_span.setSuffix(" s")
        self.stft_win = QComboBox()
        for key, text in an.WINDOWS.items():
            self.stft_win.addItem(text, key)
        self.stft_win.setCurrentIndex(max(self.stft_win.findData(c.get("fft_window", "hann")), 0))
        sf.addRow("Segment length", self.stft_n)
        sf.addRow("Overlap", self.stft_ov)
        sf.addRow("Window", self.stft_win)
        sf.addRow("Time span", self.stft_span)
        sf.addRow("Colour map", self.stft_cmap)
        sf.addRow(label("Short-time FFT of the channel, level in dB re 1 unit RMS.",
                        muted=True, small=True))
        hw, hf = form_page()
        self.hist_bins = QSpinBox(minimum=5, maximum=500, value=int(c.get("hist_bins", 50)))
        self.hist_density = QCheckBox("Probability density (area = 1) instead of counts",
                                      checked=bool(c.get("hist_density", True)))
        self.hist_fit = QCheckBox("Overlay fitted normal (Gaussian) curve", checked=bool(c.get("hist_fit", True)))
        self.hist_stats = QCheckBox("Show mean, SD, skewness, kurtosis", checked=bool(c.get("hist_stats", True)))
        self.hist_span = spin(float(c.get("hist_span_s") or 0), 0, 7200, 1)
        self.hist_span.setSuffix(" s"); self.hist_span.setSpecialValueText("Quick View time window")
        hf.addRow("Bins", self.hist_bins)
        hf.addRow("Data", self.hist_span)
        for x in (self.hist_density, self.hist_fit, self.hist_stats):
            hf.addRow("", x)
        hf.addRow(label("Amplitude distribution: Gaussian noise gives a bell, a sine two peaks at "
                        "±amplitude, shocks long tails (kurtosis > 3).", muted=True, small=True))
        pages = {"fft": (fw, "Spectrum"), "stft": (sw, "Spectrogram"), "hist": (hw, "Histogram")}
        for kk, (pw, title) in pages.items():
            if kk == self.ptype:                       # (k is reused by the loops above)
                self.tabs.addTab(pw, title)
            else:                                      # keep the widgets alive, never shown
                pw.setParent(self); pw.hide()
        self.fft_scale.currentIndexChanged.connect(lambda _: self._axis_hint())
        self.hist_density.toggled.connect(lambda _: self._axis_hint())
        self.picker.changed.connect(self._axis_hint)
        self._axis_hint()

    def _first_unit(self) -> str:
        sel = self.picker.checked() if hasattr(self, "picker") else []
        return self.units.get(tuple(sel[0]), "") if sel else ""

    def _axis_hint(self):
        tmp = {"plot": self.ptype, "fft_scale": self.fft_scale.currentData(),
               "hist_density": self.hist_density.isChecked(), "x_mode": self.x_mode.currentData()}
        xd, yd = axis_defaults(tmp, self._first_unit())
        self.x_label.setPlaceholderText(xd); self.y_label.setPlaceholderText(yd)
        self.x_label.setToolTip(f"Empty = default: {xd}"); self.y_label.setToolTip(f"Empty = default: {yd}")

    # ================================================================ text
    def _text(self):
        w, f = form_page()
        self.text_style = QComboBox(); self.text_style.addItems(TEXT_STYLES)
        self.text_style.setCurrentText(self.cfg.get("style", "H1"))
        self.text = QPlainTextEdit(self.cfg.get("text", ""))
        self.text.setMaximumHeight(140)
        self.align = QComboBox()
        for k, text in (("left", "Left"), ("center", "Centre"), ("right", "Right")):
            self.align.addItem(text, k)
        self.align.setCurrentIndex(max(0, self.align.findData(self.cfg.get("align", "left"))))
        f.addRow("Style", self.text_style)
        f.addRow("Text", self.text)
        f.addRow("Alignment", self.align)
        self.tabs.addTab(w, "General")

    # ================================================================ numeric
    def _value(self):
        w, f = form_page()
        self._title_rows(f)
        self.picker = ChannelPicker(self.choices, [self.cfg.get("channel")], multi=False)
        f.addRow("Tag", self.picker)
        self._quantity_rows(f)
        self.dec = QSpinBox(minimum=0, maximum=8, value=int(self.cfg.get("decimals", 3)))
        self.unit = QLineEdit(self.cfg.get("unit", "")); self.unit.setPlaceholderText("default: the tag's unit")
        f.addRow("Decimals", self.dec)
        f.addRow("Unit", self.unit)
        self.tabs.addTab(w, "General")

    # ================================================================ gauge
    def _gauge(self):
        c = self.cfg
        w, f = form_page()
        self._title_rows(f)
        self.picker = ChannelPicker(self.choices, [c.get("channel")], multi=False)
        self.picker.setMinimumHeight(150)
        f.addRow("Tag", self.picker)
        self._quantity_rows(f)
        self.tabs.addTab(w, "General")

        sw, sf = form_page()
        self.g_min, self.g_max = spin(c.get("g_min", 0)), spin(c.get("g_max", 100))
        r = QHBoxLayout(); r.addWidget(self.g_min); r.addWidget(QLabel("to")); r.addWidget(self.g_max)
        sf.addRow("Range", r)
        self.g_ticks = QSpinBox(minimum=2, maximum=20, value=int(c.get("g_ticks", 10)))
        sf.addRow("Major ticks", self.g_ticks)
        self.dec = QSpinBox(minimum=0, maximum=8, value=int(c.get("decimals", 1)))
        sf.addRow("Decimals", self.dec)
        self.unit = QLineEdit(c.get("unit", "")); self.unit.setPlaceholderText("default: the tag's unit")
        sf.addRow("Unit", self.unit)
        self.warn_on = QCheckBox("Warning from", checked=c.get("g_warn") is not None)
        self.warn = spin(c.get("g_warn") if c.get("g_warn") is not None else 70)
        self.alarm_on = QCheckBox("Alarm from", checked=c.get("g_alarm") is not None)
        self.alarm = spin(c.get("g_alarm") if c.get("g_alarm") is not None else 90)
        for on, sp in ((self.warn_on, self.warn), (self.alarm_on, self.alarm)):
            sp.setEnabled(on.isChecked()); on.toggled.connect(sp.setEnabled)
            sf.addRow(on, sp)
        sf.addRow(label("Zones colour the value (and the scale in the Stage style): warning = "
                        "warn colour, alarm = bad colour. Display only - real alarms are set in "
                        "the Alarms document.", muted=True, small=True))
        self.tabs.addTab(sw, "Scale")

        yw = QWidget(); yl = QHBoxLayout(yw)
        left, lf = form_page()
        self.g_style = QComboBox()
        for k, text in GAUGE_STYLES.items():
            self.g_style.addItem(text, k)
        self.g_style.setCurrentIndex(max(self.g_style.findData(c.get("gauge_style", "speed")), 0))
        self.g_color = ColorButton(c.get("g_color", ""), "Auto (accent)")
        lf.addRow("Style", self.g_style)
        lf.addRow("Value colour", self.g_color)
        self.preview = GaugeWidget(); self.preview.setMinimumSize(220, 200)
        yl.addWidget(left, 1); yl.addWidget(self.preview, 1)
        self.tabs.addTab(yw, "Style")
        for sig in (self.g_style.currentIndexChanged, self.g_min.valueChanged, self.g_max.valueChanged,
                    self.g_ticks.valueChanged, self.dec.valueChanged, self.warn.valueChanged,
                    self.alarm.valueChanged, self.warn_on.toggled, self.alarm_on.toggled,
                    self.unit.textChanged, self.g_color.changed):
            sig.connect(lambda *_: self._preview())
        self._preview()

    def _gauge_cfg(self) -> dict:
        return {"g_min": self.g_min.value(), "g_max": self.g_max.value(), "g_ticks": self.g_ticks.value(),
                "decimals": self.dec.value(), "unit": self.unit.text().strip(),
                "gauge_style": self.g_style.currentData(), "g_color": self.g_color.color(),
                "g_warn": self.warn.value() if self.warn_on.isChecked() else None,
                "g_alarm": self.alarm.value() if self.alarm_on.isChecked() else None}

    def _preview(self):
        g = self._gauge_cfg()
        self.preview.configure(g, "")
        self.preview.set_value(g["g_min"] + 0.68 * (g["g_max"] - g["g_min"]))

    # ================================================================ statistics table
    def _stats(self):
        c = self.cfg
        w, f = form_page()
        self._title_rows(f, "optional - default: Statistics")
        self._window_rows(f)
        self.dec = QSpinBox(minimum=0, maximum=8, value=int(c.get("decimals", 3)))
        f.addRow("Decimals", self.dec)
        tips = {an.STAT_LABEL[k]: t for k, t in an.STAT_HELP.items()}
        self.col_checks = self._checks(STAT_TILE_COLS, c.get("columns") or ["Value", "Mean", "Min", "Max"],
                                       per_row=4, tips=tips)
        f.addRow("Columns", self.col_checks[0])
        self.tabs.addTab(w, "General")
        self.picker = ChannelPicker(self.choices, c.get("channels", []))
        self.tabs.addTab(self.picker, "Tags")

    # ================================================================ tag table
    def _tags(self):
        c = self.cfg
        w, f = form_page()
        self._title_rows(f, "optional - default: Tags")
        self.dec = QSpinBox(minimum=0, maximum=8, value=int(c.get("decimals", 3)))
        f.addRow("Decimals", self.dec)
        self.col_checks = self._checks(TAG_TILE_COLS, c.get("columns") or TAG_TILE_COLS, per_row=4)
        f.addRow("Columns", self.col_checks[0])
        self.tabs.addTab(w, "General")
        self.picker = ChannelPicker(self.choices, c.get("channels", []))
        self.tabs.addTab(self.picker, "Tags")

    # ================================================================ LED
    def _led(self):
        c = self.cfg
        w, f = form_page()
        self._title_rows(f)
        self.picker = ChannelPicker(self.choices, [c.get("channel")], multi=False)
        f.addRow("Tag", self.picker)
        self._quantity_rows(f)
        self.led_op = QComboBox(); self.led_op.addItems([">", ">=", "<", "<=", "==", "!="])
        self.led_op.setCurrentText(c.get("led_op", ">"))
        self.led_value = spin(c.get("led_value", 0.5))
        r = QHBoxLayout(); r.addWidget(self.led_op); r.addWidget(self.led_value, 1)
        f.addRow("On when value", r)
        self.led_on, self.led_off = ColorButton(c.get("led_on", ""), "Auto (ok)"), \
            ColorButton(c.get("led_off", ""), "Auto (grey)")
        self.led_on_text, self.led_off_text = QLineEdit(c.get("led_on_text", "ON")), QLineEdit(c.get("led_off_text", "OFF"))
        f.addRow("On colour", self.led_on); f.addRow("On text", self.led_on_text)
        f.addRow("Off colour", self.led_off); f.addRow("Off text", self.led_off_text)
        self.dec = QSpinBox(minimum=0, maximum=8, value=int(c.get("decimals", 3)))
        self.unit = QLineEdit(c.get("unit", ""))
        self.tabs.addTab(w, "General")

    # ================================================================ alarm table
    def _alarms(self):
        c = self.cfg
        w, f = form_page()
        self._title_rows(f, "optional - default: the source")
        self.src = QComboBox()
        for k, text in ALARM_SOURCES.items():
            self.src.addItem(text, k)
        self.src.setCurrentIndex(max(self.src.findData(c.get("source", "active")), 0))
        self.rows = QSpinBox(minimum=1, maximum=1000, value=int(c.get("max_rows", 10)))
        self.rows.setToolTip("Maximum number of rows shown (newest / highest priority first)")
        f.addRow("Show", self.src)
        f.addRow("Max rows", self.rows)
        self.col_checks = self._checks(ALARM_TILE_COLS, c.get("columns") or ALARM_TILE_COLS, per_row=4)
        self.lv_checks = self._checks([f"L{i}" for i in range(1, 5)],
                                      [f"L{i}" for i in c.get("levels", [1, 2, 3, 4])])
        f.addRow("Columns", self.col_checks[0])
        f.addRow("Levels", self.lv_checks[0])
        self.tabs.addTab(w, "General")

    # ================================================================ appearance
    def _appearance(self, text: bool = False):
        c = self.cfg
        w, f = form_page()
        self.card_bg = ColorButton(c.get("card_bg", ""), "Auto (view default)")
        self.border = ColorButton(c.get("border_color", ""), "Auto (view default)")
        f.addRow("Card background", self.card_bg)
        f.addRow("Card border", self.border)
        if self.kind == "plot":
            self.plot_bg = ColorButton(c.get("plot_bg", ""), "Auto (card)")
            self.area_bg = ColorButton(c.get("area_bg", ""), "Auto (black)")
            f.addRow("Graph background", self.plot_bg)
            f.addRow("Plot area", self.area_bg)
        if self.kind in ("value", "text"):
            self.value_color = ColorButton(c.get("value_color", ""), "Auto (text)")
            f.addRow("Text colour" if text else "Value colour", self.value_color)
        if self.kind == "value":
            self.value_size = QSpinBox(minimum=10, maximum=120, value=int(c.get("value_size", 30)), suffix=" px")
            f.addRow("Value size", self.value_size)
        f.addRow(label("Auto colours follow the theme (dark / light) and the View properties.",
                       muted=True, small=True))
        return w

    # ================================================================ result
    def _ok(self):
        err = ""
        if self.kind == "plot" and not self.y_auto.isChecked() and self.y_min.value() >= self.y_max.value():
            err = "Y range: min must be below max."
        elif self.kind == "gauge" and self.g_min.value() >= self.g_max.value():
            err = "Gauge range: min must be below max."
        elif self.kind in ("value", "gauge", "led") and not self.picker.current():
            err = "Choose a tag."
        if err:
            set_prop(self.err, "level", "bad"); self.err.setText(err)
            self.tabs.setCurrentIndex(2 if err.startswith("Y") else 1 if err.startswith("Gauge") else 0)
            return
        self.accept()

    def result_cfg(self) -> dict:
        c, kind = self.cfg, self.kind
        c["card_bg"], c["border_color"] = self.card_bg.color(), self.border.color()
        if kind != "text":
            c["title"] = self.title.text().strip()
            c["show_title"] = self.show_title.isChecked()
        if kind == "plot":
            c.update(plot=self.ptype, legend=self.legend.isChecked(),
                     stack=self.stack.isChecked(), grid=self.grid.isChecked(), line_width=self.lw.value(),
                     span_s=self.span.value(),
                     channels=([self.picker.current()] if self.picker.current() else [])
                     if self.ptype == "stft" else self.picker.checked(),
                     x_mode=self.x_mode.currentData(), x_samples=self.x_samples.value(), axis_labels=self.axis_labels.isChecked(),
                     x_label=self.x_label.text().strip(), y_label=self.y_label.text().strip(),
                     y_auto=self.y_auto.isChecked(), y_min=self.y_min.value(), y_max=self.y_max.value(),
                     log_x=self.log_x.isChecked(), log_y=self.log_y.isChecked(),
                     plot_bg=self.plot_bg.color(), area_bg=self.area_bg.color(),
                     fft_window=(self.stft_win if self.ptype == "stft"
                                 else self.fft_window).currentData(),
                     fft_scale=self.fft_scale.currentData(), fft_nfft=self.fft_nfft.currentData(),
                     fft_avg=self.fft_avg.value(), fft_fmax=self.fft_fmax.value(),
                     stft_nperseg=self.stft_n.currentData(), stft_overlap=self.stft_ov.value(),
                     stft_cmap=self.stft_cmap.currentText(), stft_span_s=self.stft_span.value(),
                     hist_bins=self.hist_bins.value(), hist_density=self.hist_density.isChecked(),
                     hist_fit=self.hist_fit.isChecked(), hist_stats=self.hist_stats.isChecked(),
                     hist_span_s=self.hist_span.value())
        elif kind == "text":
            c.update(style=self.text_style.currentText(), text=self.text.toPlainText(),
                     value_color=self.value_color.color(), align=self.align.currentData())
        elif kind in ("value", "gauge", "led"):
            c.update(channel=self.picker.current(), quantity=self.quantity.currentData(),
                     decimals=self.dec.value(), unit=self.unit.text().strip(), **self._window_cfg())
            if kind == "value":
                c.update(value_color=self.value_color.color(), value_size=self.value_size.value())
            elif kind == "gauge":
                c.update(self._gauge_cfg())
            else:
                c.update(led_op=self.led_op.currentText(), led_value=self.led_value.value(),
                         led_on=self.led_on.color(), led_off=self.led_off.color(),
                         led_on_text=self.led_on_text.text(), led_off_text=self.led_off_text.text())
        elif kind == "stats":
            c.update(decimals=self.dec.value(), channels=self.picker.checked(), **self._window_cfg(),
                     columns=[n for n, b in self.col_checks[1].items() if b.isChecked()] or ["Value"])
        elif kind == "tags":
            c.update(decimals=self.dec.value(), channels=self.picker.checked(),
                     columns=[n for n, b in self.col_checks[1].items() if b.isChecked()] or ["Tag"])
        elif kind == "alarms":
            c.update(source=self.src.currentData(), max_rows=self.rows.value(),
                     columns=[n for n, b in self.col_checks[1].items() if b.isChecked()] or ["Alarm"],
                     levels=[int(n[1]) for n, b in self.lv_checks[1].items() if b.isChecked()] or [1, 2, 3, 4])
        return c
