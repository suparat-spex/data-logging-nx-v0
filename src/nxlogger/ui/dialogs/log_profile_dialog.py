"""Add / edit a logging profile: the settings pop-up, tabs style like devices, tags and assets.
The type (core/log_types.py) is chosen by Add ▾ and fixed here; the When tab shows its settings.

  General  name, enabled, description (the type is shown, not changed)
  When     interval      every N s (0 = every value)
           on change     deadband (absolute / %), heartbeat, min gap
           trigger       start condition, stop (when the start is false / another condition),
                         debounce, pre- / post-trigger time, interval while active
           event         on: alarm (any / one) / condition / time of day / marker; debounce
           batch/shift   by shift times / start-stop condition, batch id tag, summary statistics,
                         interval while it runs
           statistics    period (presets), statistics
  Tags     all enabled tags, or By tag / By asset (whole assets kept live); event: the snapshot columns
  Output   folder (the project default / its own), date folders (session, year, month, day, custom
           pattern), new file (session / hour / day / week / month / N MB / run), file name
           (automatic / pattern with tokens), compress closed files, keep N days, format - a preview
           of the file now and the next one (core/log_files.py)

[Add profile] / [Save] stays off until it is valid (log_types.problem + name + tags).
"""
from __future__ import annotations

import copy
import os
import time

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QGridLayout, QMenu, QPushButton, QRadioButton, QSpinBox, QToolButton,
                               QHBoxLayout, QLineEdit, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from nxlogger.core import log_files as lf
from nxlogger.core import log_types as lt
from nxlogger.core import recording as dl
from nxlogger.core.project import STAT_INTERVALS
from nxlogger.ui.dialogs.common import ChannelPicker, form_page
from nxlogger.ui.theme.ui_kit import T, label, make_icon, set_prop

ICON_COLS = 10


def _secs(value: float, maximum: float = 86400.0, special: str = "") -> QDoubleSpinBox:
    b = QDoubleSpinBox(minimum=0.0, maximum=maximum, decimals=1, suffix=" s", value=float(value or 0))
    if special:
        b.setSpecialValueText(special)
    return b


class ConditionEdit(QWidget):
    """[tag ▾] [op ▾] [value] - a condition of log_types (key, op, value)."""

    def __init__(self, choices, cond: dict | None, empty: str = ""):
        super().__init__()
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(4)
        self.tag = QComboBox(); self.tag.setMinimumWidth(220)
        if empty:
            self.tag.addItem(empty, None)
        for c in choices:
            self.tag.addItem(c[2], [c[0], c[1]])
        self.op = QComboBox(); self.op.addItems(lt.OPS)
        self.value = QDoubleSpinBox(minimum=-1e12, maximum=1e12, decimals=3)
        cond = cond or {}
        i = self.tag.findData(cond.get("key")) if cond.get("key") else (0 if empty else -1)
        self.tag.setCurrentIndex(max(i, 0) if self.tag.count() else -1)
        self.op.setCurrentText(cond.get("op", ">"))
        self.value.setValue(float(cond.get("value", 0.0) or 0.0))
        h.addWidget(self.tag, 1); h.addWidget(self.op); h.addWidget(self.value)
        self.tag.currentIndexChanged.connect(lambda _: self._sync())
        self._sync()

    def _sync(self):
        on = self.tag.currentData() is not None
        self.op.setEnabled(on); self.value.setEnabled(on)

    def result(self) -> dict | None:
        key = self.tag.currentData()
        return lt.new_condition(key, self.op.currentText(), self.value.value()) if key else None


class LogProfileDialog(QDialog):
    """prof: the profile (a copy is edited - result_profile()); choices: [(tid, idx, label, colour,
    unit)] for the tags; taken: file-safe names of the other profiles; alarms: [(id, name)]."""

    def __init__(self, parent, prof: dict, choices, taken: set[str], alarms=(), default_folder: str = "",
                 project: str = "Project", device: str = "Device"):
        super().__init__(parent)
        self.p, self.taken = copy.deepcopy(prof), taken
        self.default_folder, self.project, self.device = default_folder or os.getcwd(), project, device
        self.out = lf.normalize_output(self.p)
        self.kind = lt.type_of(self.p)
        self.w = {**lt.default_when(self.kind), **(self.p.get("when") or {})}
        _k, tname, _icon, what = lt.TYPE[self.kind]
        self.setWindowTitle(f"{tname} logging profile")
        self.setMinimumWidth(600)
        tabs = self.tabs = QTabWidget()

        # General ---------------------------------------------------------------
        g, gf = form_page()
        self.name = QLineEdit(self.p["name"]); self.name.setToolTip("Used in the file names: <profile>_<device>.csv")
        self.enabled = QCheckBox("Enabled - logs on Record", checked=self.p.get("enabled", True))
        self.desc = QLineEdit(self.p.get("description", "")); self.desc.setPlaceholderText("What it is for ...")
        gf.addRow("Name", self.name)
        gf.addRow("", self.enabled)
        gf.addRow("Type", label(f"{tname} - {what}", muted=True))
        self._icon = self.p.get("icon") if self.p.get("icon") in lt.ICONS else ""
        icons = QWidget(); iv = QVBoxLayout(icons); iv.setContentsMargins(0, 0, 0, 0); iv.setSpacing(4)
        grid = QGridLayout(); grid.setSpacing(2)
        self.icon_btns: dict[str, QToolButton] = {}
        for i, name_ in enumerate(lt.ICONS):
            b = QToolButton(); b.setCheckable(True); b.setAutoRaise(True); b.setToolTip(name_)
            b.setIconSize(QSize(18, 18)); b.setFixedSize(30, 30)
            b.clicked.connect(lambda _=False, n=name_: self._set_icon(n))
            grid.addWidget(b, i // ICON_COLS, i % ICON_COLS)
            self.icon_btns[name_] = b
        iv.addLayout(grid)
        irow = QHBoxLayout(); irow.setSpacing(6)
        self.icon_default = QPushButton("Default"); self.icon_default.clicked.connect(lambda: self._set_icon(""))
        self.icon_default.setToolTip(f"The {tname} icon")
        self.icon_note = label("", muted=True, small=True)
        irow.addWidget(self.icon_default); irow.addWidget(self.icon_note, 1)
        iv.addLayout(irow)
        gf.addRow("Icon", icons)
        gf.addRow("Description", self.desc)
        gf.addRow("", label("Every enabled profile runs on Record and writes into the same session folder.",
                            muted=True, small=True))
        tabs.addTab(g, "General")

        # When ------------------------------------------------------------------
        wp, wf = form_page()
        nums = [c for c in choices if not str(c[2]).endswith("(text)")]     # conditions: numbers
        w = self.w
        if self.kind == "interval":
            self.every = _secs(w["every_s"], special="every value")
            wf.addRow("Every", self.every)
            wf.addRow("", label("Every value = one row per scan (number tags) or per sample (waveforms); "
                                "N s = the first value of each N-second period.", muted=True, small=True))
        elif self.kind == "change":
            self.deadband = QDoubleSpinBox(minimum=0.0, maximum=1e9, decimals=3, value=float(w["deadband"]))
            self.mode = QComboBox(); self.mode.addItem("% of the value", "pct"); self.mode.addItem("absolute", "abs")
            self.mode.setCurrentIndex(max(0, self.mode.findData(w["mode"])))
            row = QHBoxLayout(); row.addWidget(self.deadband); row.addWidget(self.mode); row.addStretch(1)
            self.heartbeat = _secs(w["heartbeat_s"], special="no heartbeat")
            self.min_gap = _secs(w["min_gap_s"], 3600, special="none")
            wf.addRow("Deadband", row)
            wf.addRow("Heartbeat", self.heartbeat)
            wf.addRow("Min. gap", self.min_gap)
            wf.addRow("", label("A row when any tag moves more than the deadband from its last logged value, and "
                                "at least once per heartbeat - small files for slow signals and states.",
                                muted=True, small=True))
        elif self.kind == "trigger":
            self.start = ConditionEdit(nums, w["start"])
            self.stop_mode = QComboBox()
            self.stop_mode.addItem("When the start condition is false", "")
            self.stop_mode.addItem("When another condition is true", "cond")
            self.stop = ConditionEdit(nums, w["stop"] or w["start"])
            self.stop_mode.setCurrentIndex(1 if w.get("stop") else 0)
            self.debounce = _secs(w["debounce_s"], 3600, special="none")
            self.pre, self.post = _secs(w["pre_s"], 3600, special="none"), _secs(w["post_s"], 3600, special="none")
            self.every = _secs(w["every_s"], special="every value")
            wf.addRow("Start when", self.start)
            wf.addRow("Stop", self.stop_mode)
            wf.addRow("", self.stop)
            wf.addRow("Debounce", self.debounce)
            wf.addRow("Pre-trigger", self.pre)
            wf.addRow("Post-trigger", self.post)
            wf.addRow("While active", self.every)
            wf.addRow("", label("Only the runs are logged, with a run column. Pre-trigger: the seconds before the "
                                "start come from a buffer. Tip: a Virtual logic tag makes any condition.",
                                muted=True, small=True))
            self.stop_mode.currentIndexChanged.connect(lambda _: self.stop.setVisible(bool(self.stop_mode.currentData())))
            self.stop.setVisible(bool(w.get("stop")))
        elif self.kind == "event":
            self.source = QComboBox()
            for k, text in lt.EVENT_SOURCES:
                self.source.addItem(text, k)
            self.source.setCurrentIndex(max(0, self.source.findData(w["source"])))
            self.src_stack = QStackedWidget()
            self.alarm = QComboBox(); self.alarm.addItem("Any alarm", "")
            for aid, aname in alarms:
                self.alarm.addItem(aname, aid)
            self.alarm.setCurrentIndex(max(0, self.alarm.findData(w["alarm"])))
            self.cond = ConditionEdit(nums, w["cond"])
            self.times = QLineEdit(w["times"]); self.times.setPlaceholderText("06:00, 14:00, 22:00")
            self.src_stack.addWidget(self.alarm); self.src_stack.addWidget(self.cond)
            self.src_stack.addWidget(self.times); self.src_stack.addWidget(label("The Marker button / Ctrl+Shift+E "
                                                                                 "while recording", muted=True))
            self.debounce = _secs(w["debounce_s"], 3600, special="none")
            wf.addRow("On", self.source)
            wf.addRow("", self.src_stack)
            wf.addRow("Debounce", self.debounce)
            wf.addRow("", label("One row per event: time, what happened and the values of the tags (Tags tab - any "
                                "category, text too).", muted=True, small=True))
            self.source.currentIndexChanged.connect(lambda i: (self.src_stack.setCurrentIndex(i), self._check()))
            self.src_stack.setCurrentIndex(self.source.currentIndex())
        elif self.kind == "batch":
            self.by = QComboBox()
            for k, text in lt.BATCH_BY:
                self.by.addItem(text, k)
            self.by.setCurrentIndex(max(0, self.by.findData(w["by"])))
            self.by_stack = QStackedWidget()
            self.shifts = QLineEdit(w["shifts"]); self.shifts.setPlaceholderText("06:00, 18:00")
            self.start = ConditionEdit(nums, w["start"])
            self.by_stack.addWidget(self.start); self.by_stack.addWidget(self.shifts)
            self.by_stack.setCurrentIndex(0 if w["by"] == "tag" else 1)
            self.id_key = QComboBox(); self.id_key.addItem("Batch 1, 2 ... / the shift start", None)
            for c in choices:
                self.id_key.addItem(c[2], [c[0], c[1]])
            self.id_key.setCurrentIndex(max(0, self.id_key.findData(w["id_key"])) if w.get("id_key") else 0)
            self.bstats = {}
            sr = QHBoxLayout()
            for k in lt.SUMMARY_STATS:
                self.bstats[k] = QCheckBox(dl.STAT_LABELS[k], checked=k in w["stats"]); sr.addWidget(self.bstats[k])
            sr.addStretch(1)
            self.every = _secs(w["every_s"], special="every value")
            wf.addRow("Batches by", self.by)
            wf.addRow("", self.by_stack)
            wf.addRow("Batch name", self.id_key)
            wf.addRow("Summary", sr)
            wf.addRow("While it runs", self.every)
            wf.addRow("", label("Rows while a batch / shift runs (a batch column) and <file>_summary.csv: one row "
                                "per batch with start, end and the statistics.", muted=True, small=True))
            self.by.currentIndexChanged.connect(lambda _: (self.by_stack.setCurrentIndex(
                0 if self.by.currentData() == "tag" else 1), self._check()))
        else:                                        # statistic
            ir = QHBoxLayout()
            self.preset = QComboBox()
            for text, sec in STAT_INTERVALS:
                self.preset.addItem(text, sec)
            self.preset.addItem("Custom", None)
            self.interval = QDoubleSpinBox(minimum=0.1, maximum=86400, decimals=1, suffix=" s",
                                           value=float(self.p.get("interval_s", 60)))
            ir.addWidget(self.preset); ir.addWidget(self.interval); ir.addStretch(1)
            sg = QGridLayout(); sg.setHorizontalSpacing(14)
            self.stats = {}
            for i, k in enumerate(dl.STAT_KEYS):
                self.stats[k] = QCheckBox(dl.STAT_LABELS[k], checked=k in self.p.get("stats", []))
                sg.addWidget(self.stats[k], i // 4, i % 4)
            self.stats["inst"].setToolTip("Instantaneous: the last value of the period")
            sw = QWidget(); sw.setLayout(sg); sg.setContentsMargins(0, 0, 0, 0)
            wf.addRow("Period", ir)
            wf.addRow("Statistics", sw)
            wf.addRow("", label("Periods are aligned to the clock (1 min starts at hh:mm:00); one row per period "
                                "with the sample count - reports and long trends.", muted=True, small=True))
            self.preset.currentIndexChanged.connect(self._preset)
            self.interval.valueChanged.connect(self._sync_preset)
            self._sync_preset()
        tabs.addTab(wp, "When")

        # Tags ------------------------------------------------------------------
        tw = QWidget(); tv = QVBoxLayout(tw); tv.setContentsMargins(0, 0, 0, 0); tv.setSpacing(4)
        self.all = QCheckBox("All enabled tags (new ones are included automatically)",
                             checked=self.p.get("channels") is None and self.kind != "event")
        self.picker = ChannelPicker(choices, self.p.get("channels") or [], assets=True,
                                    chosen_assets=self.p.get("assets") or [])
        self.picker.setMinimumHeight(220)
        self.picker.setDisabled(self.all.isChecked())
        self.all.toggled.connect(lambda on: (self.picker.setDisabled(on), self._check()))
        self.picker.changed.connect(self._check)
        tv.addWidget(self.all); tv.addWidget(self.picker, 1)
        if self.kind == "event":
            self.all.setText("All enabled device tags")
        tabs.addTab(tw, "Tags")

        # Output ----------------------------------------------------------------
        op, of = form_page()
        o = self.out
        self.f_default = QRadioButton(f"Project default   {self.default_folder}")
        self.f_own = QRadioButton("This folder")
        grp = QButtonGroup(self); grp.addButton(self.f_default); grp.addButton(self.f_own)
        (self.f_own if o["folder"] else self.f_default).setChecked(True)
        self.f_path = QLineEdit(o["folder"]); self.f_path.setPlaceholderText("E:\\Trend  or  \\\\server\\share\\Logs")
        browse = QPushButton("Browse..."); browse.clicked.connect(self._browse)
        fr = QHBoxLayout(); fr.setSpacing(4); fr.addWidget(self.f_own); fr.addWidget(self.f_path, 1); fr.addWidget(browse)
        self.f_state = label("", small=True); self.f_state.setObjectName("statusMsg")
        of.addRow("Folder", self.f_default)
        of.addRow("", fr)
        of.addRow("", self.f_state)
        self.folders = QComboBox()
        for k, text, _pat in lf.FOLDERS:
            self.folders.addItem(text, k)
        self.folders.setCurrentIndex(max(0, self.folders.findData(o["folders"])))
        self.pattern = QLineEdit(o.get("pattern") or "{yyyy}\\{MM}\\{dd}")
        self.pattern.setToolTip("Tokens: " + " ".join("{" + t + "}" for t in lf.TOKENS) + "  - \\ or / = a sub-folder")
        dr = QHBoxLayout(); dr.setSpacing(4); dr.addWidget(self.folders); dr.addWidget(self.pattern, 1)
        of.addRow("Folders", dr)
        self.new_file = QComboBox()
        for k, text, _suf in lf.NEW_FILE:
            if k != "run" or self.kind in ("trigger", "batch"):
                self.new_file.addItem(text, k)
        self.new_file.setCurrentIndex(max(0, self.new_file.findData(o["new_file"])))
        self.day_at = QLineEdit(o.get("day_at", "00:00")); self.day_at.setMaximumWidth(70)
        self.day_at.setToolTip("The time the new file starts (a shift start, 06:00)")
        self.week_day = QComboBox(); self.week_day.addItems(lf.WEEKDAYS)
        self.week_day.setCurrentIndex(int(o.get("week_day", 0)) % 7)
        self.size_mb = QDoubleSpinBox(minimum=0.1, maximum=100000, decimals=1, suffix=" MB",
                                      value=float(o.get("size_mb") or 100))
        self.at_lbl = label("at", muted=True)
        nr = QHBoxLayout(); nr.setSpacing(4)
        for w_ in (self.new_file, self.week_day, self.at_lbl, self.day_at, self.size_mb):
            nr.addWidget(w_)
        nr.addStretch(1)
        of.addRow("New file", nr)
        self.fname = QLineEdit(o.get("name", ""))
        tok = QToolButton(); tok.setText("Tokens"); tok.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tm = QMenu(tok)
        for t_ in lf.TOKENS:
            tm.addAction("{" + t_ + "}", lambda t_=t_: (self.fname.insert("{" + t_ + "}"), self.fname.setFocus()))
        tok.setMenu(tm)
        nmr = QHBoxLayout(); nmr.setSpacing(4); nmr.addWidget(self.fname, 1); nmr.addWidget(tok)
        of.addRow("File name", nmr)
        self.preview = label("", muted=True, small=True); self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        of.addRow("Preview", self.preview)
        self.compress = QCheckBox("Compress closed files (.zst)", checked=bool(o.get("compress")))
        self.keep = QSpinBox(minimum=0, maximum=36500, suffix=" days", value=int(o.get("keep_days") or 0))
        self.keep.setSpecialValueText("forever")
        self.keep.setToolTip("Delete this profile's own files older than this (its file name prefix) - checked hourly")
        kr = QHBoxLayout(); kr.setSpacing(8); kr.addWidget(self.compress); kr.addStretch(1)
        kr.addWidget(label("Keep", muted=True)); kr.addWidget(self.keep)
        of.addRow("Closed files", kr)
        self.fmt = QComboBox()
        for key, (text, _) in (dl.FORMATS if self.kind == "interval" else dl.STAT_FORMATS).items():
            self.fmt.addItem(text, key)
        self.fmt.setCurrentIndex(max(self.fmt.findData(self.p.get("format", "CSV")), 0))
        self.keep_raw = QCheckBox("Also raw packets (.wpk) - waveform devices", checked=bool(self.p.get("keep_raw")))
        fmr = QHBoxLayout(); fmr.setSpacing(8); fmr.addWidget(self.fmt, 1)
        if self.kind == "interval":
            fmr.addWidget(self.keep_raw)
        else:
            self.keep_raw.setParent(op); self.keep_raw.hide()
        of.addRow("Format", fmr)
        tabs.addTab(op, "Output")
        for sig in (self.f_default.toggled, self.f_path.textChanged, self.folders.currentIndexChanged,
                    self.pattern.textChanged, self.new_file.currentIndexChanged, self.day_at.textChanged,
                    self.week_day.currentIndexChanged, self.size_mb.valueChanged, self.fname.textChanged,
                    self.keep.valueChanged, self.name.textChanged):
            sig.connect(lambda *_: self._output_changed())

        self.err = label("", small=True); self.err.setObjectName("statusMsg")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self); lay.addWidget(tabs, 1); lay.addWidget(self.err); lay.addWidget(bb)
        self.name.textChanged.connect(lambda _: self._check())
        for cb in self.findChildren(QComboBox):
            cb.currentIndexChanged.connect(lambda _: self._check())
        for le in (getattr(self, "times", None), getattr(self, "shifts", None)):
            if le is not None:
                le.textChanged.connect(lambda _: self._check())
        self._paint_icons()
        self._output_changed()

    # ---------------------------------------------------------------- icon
    def _set_icon(self, name: str):
        self._icon = name if name in lt.ICONS else ""
        self._paint_icons()

    def _paint_icons(self):
        shown = self._icon or lt.TYPE[self.kind][2]
        for n, b in self.icon_btns.items():
            b.setIcon(make_icon(n, T["text"] if n == shown else T["muted"]))
            b.setChecked(n == shown)
        self.icon_default.setEnabled(bool(self._icon))
        self.icon_note.setText(f"Default: the {lt.TYPE[self.kind][1]} icon" if not self._icon else f"Icon: {self._icon}")
        head = getattr(self, "header_icon", None)            # the pop-up's header (common.popup_form)
        if head is not None:
            head.setPixmap(make_icon(shown, T["accent"]).pixmap(32, 32))

    # ---------------------------------------------------------------- output
    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Logging folder", self.f_path.text() or self.default_folder)
        if d:
            self.f_path.setText(os.path.normpath(d)); self.f_own.setChecked(True)

    def _output(self) -> dict:
        out = dict(self.out)
        out.update(folder=self.f_path.text().strip() if self.f_own.isChecked() else "",
                   folders=self.folders.currentData(), pattern=self.pattern.text().strip(),
                   new_file=self.new_file.currentData(), day_at=self.day_at.text().strip() or "00:00",
                   week_day=self.week_day.currentIndex(), size_mb=self.size_mb.value(), name=self.fname.text().strip(),
                   compress=self.compress.isChecked(), keep_days=self.keep.value())
        return out

    def _folder_state(self) -> tuple[str, str]:
        """(text, ok / new / bad) of the chosen folder."""
        path = self.f_path.text().strip() if self.f_own.isChecked() else self.default_folder
        if os.path.isdir(path):
            return ("✓ exists", "ok") if os.access(path, os.W_OK) else ("✗ no access (read-only)", "bad")
        q = os.path.abspath(path)
        while q and not os.path.isdir(q):
            parent = os.path.dirname(q)
            if parent == q:
                return "✗ not reachable", "bad"
            q = parent
        return ("will be created", "new") if os.access(q, os.W_OK) else ("✗ no access", "bad")

    def _output_changed(self):
        out = self._output()
        m = out["new_file"]
        self.pattern.setVisible(out["folders"] == "custom")
        self.week_day.setVisible(m == "week")
        self.at_lbl.setVisible(m in ("day", "week")); self.day_at.setVisible(m in ("day", "week"))
        self.size_mb.setVisible(m == "size")
        self.f_path.setEnabled(self.f_own.isChecked())
        text, level = self._folder_state()
        self.f_state.setText(text)
        set_prop(self.f_state, "level", {"ok": "ok", "new": "muted", "bad": "bad"}[level])
        name, note = lf.name_pattern(out, self.kind)
        self.fname.setPlaceholderText(f"automatic: {lf.auto_name(out, self.kind)}")
        if lf.problem(out):
            self.preview.setText("")
        else:
            base = out["folder"] or self.default_folder
            ctx = {"project": dl.safe_name(self.project), "profile": dl.safe_name(self.name.text() or "profile"),
                   "device": dl.safe_name(self.device), "session": time.strftime("%Y%m%d_%H%M%S")}
            now = time.time()
            starts = [lf.period_start(out, now) or now]
            nxt = lf.next_start(out, now)
            if nxt:
                starts.append(nxt)
            lines = []
            for i, t in enumerate(starts):
                c = {**ctx, "run": "Run_1" if i == 0 else "Run_2"} if m == "run" else ctx
                folder = os.path.join(base, *(lf.folder_parts(out, t, c) if out["folders"] != "session"
                                              else [f"{c['project']}_{c['session']}"]))
                lines.append(os.path.join(folder, lf.clean(lf.render(name, t, c))) + ".csv" + ("   (next)" if i else ""))
            if m == "run":
                c = {**ctx, "run": "Run_2"}
                lines.append(os.path.join(base, *lf.folder_parts(out, now, c),
                                          lf.clean(lf.render(name, now, c))) + ".csv   (next run)")
            if m == "size":
                lines.append(lines[0].replace(".csv", "_001.csv") + "   (after the size)")
            self.preview.setText("\n".join(lines) + (f"\n{note}" if note else ""))
        self._check()

    # ---------------------------------------------------------------- statistics presets
    def _preset(self, i):
        sec = self.preset.itemData(i)
        if sec:
            self.interval.blockSignals(True); self.interval.setValue(sec); self.interval.blockSignals(False)

    def _sync_preset(self, *_):
        i = self.preset.findData(int(self.interval.value())) if self.interval.value() % 1 == 0 else -1
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(i if i >= 0 else self.preset.count() - 1)
        self.preset.blockSignals(False)

    # ---------------------------------------------------------------- result
    def _when(self) -> dict:
        w = dict(self.w)
        k = self.kind
        if k == "interval":
            w.update(every_s=self.every.value())
        elif k == "change":
            w.update(deadband=self.deadband.value(), mode=self.mode.currentData(), heartbeat_s=self.heartbeat.value(),
                     min_gap_s=self.min_gap.value())
        elif k == "trigger":
            w.update(start=self.start.result() or lt.new_condition(),
                     stop=self.stop.result() if self.stop_mode.currentData() else None,
                     debounce_s=self.debounce.value(), pre_s=self.pre.value(), post_s=self.post.value(),
                     every_s=self.every.value())
        elif k == "event":
            w.update(source=self.source.currentData(), alarm=self.alarm.currentData() or "",
                     cond=self.cond.result() or lt.new_condition(), times=self.times.text().strip(),
                     debounce_s=self.debounce.value())
        elif k == "batch":
            w.update(by=self.by.currentData(), start=self.start.result() or lt.new_condition(),
                     shifts=self.shifts.text().strip(), id_key=self.id_key.currentData(),
                     stats=[s for s, b in self.bstats.items() if b.isChecked()], every_s=self.every.value())
        return w

    def result_profile(self) -> dict:
        p = self.p
        p.update(name=self.name.text().strip(), enabled=self.enabled.isChecked(), description=self.desc.text().strip(),
                 icon=self._icon,
                 format=self.fmt.currentData(), type=self.kind, when=self._when(),
                 channels=None if self.all.isChecked() else self.picker.checked_outside_assets(),
                 assets=[] if self.all.isChecked() else self.picker.checked_assets(),
                 keep_raw=self.kind == "interval" and self.keep_raw.isChecked(), output=self._output())
        if self.kind == "statistic":
            p.update(interval_s=self.interval.value(), stats=[k for k in dl.STAT_KEYS if self.stats[k].isChecked()])
        return p

    def _problem(self) -> str:
        name = self.name.text().strip()
        if not name:
            return "General: enter a profile name (used in the file names)."
        if dl.safe_name(name).lower() in self.taken:
            return "General: another profile already uses this name."
        msg = lt.problem({"type": self.kind, "when": self._when()})
        if msg:
            return msg
        if self.kind == "statistic" and not any(b.isChecked() for b in self.stats.values()):
            return "When: choose at least one statistic."
        msg = lf.problem(self._output())
        if msg:
            return msg
        if self.f_own.isChecked():
            if not self.f_path.text().strip():
                return "Output: choose the folder (or the project default)."
            if self._folder_state()[1] == "bad":
                return "Output: no access to that folder."
        if not self.all.isChecked() and not self.picker.checked() and not self.picker.checked_assets():
            return "Tags: choose at least one tag."
        return ""

    def _check(self, *_):
        if not hasattr(self, "ok_btn"):
            return
        msg = self._problem()
        set_prop(self.err, "level", "bad" if msg else "muted")
        self.err.setText(f"✗ {msg}" if msg else "✓ Ready")
        self.ok_btn.setEnabled(not msg)

    def _ok(self):
        self._check()
        if self.ok_btn.isEnabled():
            self.accept()
