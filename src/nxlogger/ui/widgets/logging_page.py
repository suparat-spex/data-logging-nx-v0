"""
logging_page.py - the Logging document: logging profiles by type (core/log_types.py), where they
write (core/log_files.py), and whether the data is safe right now. One flat left list like Tags
and Assets (the Project tree goes back - no "All profiles" row inside a type):

  health strip (always on top)  ● RECORDING 02:14:36 · profiles writing / armed / issues · disk free
                                and days left at the measured / estimated size per day · rows/s · dropped
  Logging (Project tree > Logging)
    left   band "Logging", All profiles, the six types (counts; muted while empty) - click a type: open it
    right  every profile: Name, Type, When, Folder, Last write, Rate, Size/day, Status
           (double-click: its type, it selected)
  A type (Project tree > Interval ...)
    left   band = the type (count), its profiles as three-line rows: name / when · tags / rate · size a day
    right  the selected profile: a header (name, status · last row, Edit…), its folder (Open / Copy) and
           tabs Overview (its settings in a sentence, key values, rows per minute, problems) · Tags
           (last logged value, quality, asset; Add tags..., Remove) · Files (its files: Open, Copy path,
           period) · History (interval / on change / statistics: size per day; trigger / batch: runs;
           event: the newest events) - an empty type: one message + Add
  bar    Add ▾, Edit, Duplicate, Delete | Disable, Test | Open folder | search - fixed, disabled when
         they do not apply (and while recording)

Status: ● writing · ● armed (amber: a trigger / batch waiting, an event profile listening) · ● stalled
(amber: no row for 3 intervals) · ● error (red) · ○ off.

Host API (duck-typed, DaqWindow):
  host.project (logging_profiles), host.reg_icon(target, name), host.recording, host.log_health()
  host.log_status(pr) -> (state, count text) / host.key_name(key) / host.alarm_name(id)
  host.profile_tags_text(pr) / host.profile_live(pr) -> dict / host.profile_tag_rows(pr) / host.session_files_text()
  host.profile_files_list(pr, days) / host.profile_runs(pr) / host.profile_events(pr) / host.profile_problems(pr)
  host.tag_live(tid, idx) / host.last_value(tid, idx) / host.color_of(tid, idx) / host.select_object(ref) / host.open_tag(key)
  host.add_log_profile(kind) / host.edit_log_profile(id) / host.dup_log_profile(id) / host.delete_log_profile(id)
  host.toggle_log_profile(id) / host.test_log_profile(id) / host.open_profile_folder(id) / host.open_log_folder()
  host.log_profile_add_tags(id) / host.log_profile_remove(id, keys, asset ids) / host.add_alarm_for(key)
"""
from __future__ import annotations

import datetime as _dt
import os
import time

from PySide6.QtCore import QRect, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMenu, QPushButton, QSplitter, QStackedWidget,
                               QStyle, QStyledItemDelegate, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from nxlogger.core import log_files as lf
from nxlogger.core import log_types as lt
from nxlogger.core import recording as dl
from nxlogger.ui.icons import STATUS_SIZE, status_icon, swatch
from nxlogger.ui.theme.ui_kit import T, fixed_width, label, make_icon, vsep
from nxlogger.ui.widgets.assets_page import EmptyState
from nxlogger.ui.widgets.connectivity import Band

ALL_COLS = ["Name", "Type", "When", "Folder", "Last write", "Rate", "Size/day", "Status"]
TAG_COLS = ["Name", "Source", "Unit", "Last logged", "Quality", "Asset"]
FILE_COLS = ["File", "Modified", "Size", "State"]
STATE_TEXT = {"writing": "Writing", "armed": "Armed", "stalled": "Stalled", "error": "Error", "off": "Off"}
STATE_DOT = {"writing": "ok", "armed": "armed", "stalled": "armed", "error": "alarm", "off": "none"}
STATE_TOKEN = {"writing": "ok", "armed": "warn", "stalled": "warn", "error": "bad", "off": "muted"}
SUB = Qt.ItemDataRole.UserRole + 1                   # the second (and third) line of a list row
EMPTY_TEXT = {
    "interval": "Every value, or one row every N seconds - trends and general history. Typical: 1 s for "
                "trends, 1 min for days.",
    "change": "A row only when a tag moves more than its deadband - slow signals and states, small files.",
    "trigger": "Only while a condition is true (a machine runs, a test), with the seconds before and after it.",
    "event": "One row of chosen values when an alarm comes in, a condition becomes true, at a time or a Marker.",
    "batch": "Per lot or shift: its rows with a batch column, and a summary row when it ends.",
    "statistic": "One row per period with mean, min, max ... - reports and long-term trends.",
}
PERIODS = [("Today", 1), ("Last 7 days", 7), ("Last 30 days", 30), ("All", None)]


def fmt_bytes(n) -> str:
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_span(s: float) -> str:
    s = max(0, int(s))
    d, rest = divmod(s, 86400)
    h, m = divmod(rest // 60, 60)
    if d:
        return f"{d} d {h} h"
    return f"{h} h {m} min" if h else (f"{m} min" if m else f"{s} s")


def fmt_ago(age) -> str:
    if age is None:
        return "-"
    return f"{age:.1f} s ago" if age < 10 else f"{fmt_span(age)} ago"


def fmt_rate(r) -> str:
    if r is None:
        return "-"
    return f"{r:,.0f}/s" if r >= 10 else (f"{r:.1f}/s" if r >= 1 else
                                         (f"{r * 60:.1f}/min" if r * 60 >= 1 else f"{r * 3600:.1f}/h"))


def fmt_time(t) -> str:
    if not t:
        return "-"
    d = _dt.datetime.fromtimestamp(t)
    return d.strftime("%H:%M:%S") if d.date() == _dt.date.today() else d.strftime("%d %b %H:%M")


class TwoLineDelegate(QStyledItemDelegate):
    """A list row with a name and muted lines below it (SUB, "\\n" between lines)."""

    def sizeHint(self, option, index):
        sub = index.data(SUB)
        if not sub:
            return super().sizeHint(option, index)
        fm = QFontMetrics(option.font)
        return QSize(super().sizeHint(option, index).width(), fm.height() * (1 + len(sub.split("\n"))) + 10)

    def paint(self, p, option, index):
        sub = index.data(SUB)
        if not sub:
            return super().paint(p, option, index)
        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else QApplication.style()
        name = option.text
        option.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, option, p, option.widget)
        r = option.rect.adjusted(6, 4, -6, -4)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        x = r.left()
        fm = QFontMetrics(option.font)
        if icon is not None:
            icon.paint(p, QRect(x, r.top() + (fm.height() - 16) // 2, 30, 16))
            x += 30
        p.save()
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        fg = index.data(Qt.ItemDataRole.ForegroundRole)
        p.setPen(QColor(T["text"]) if sel or fg is None else fg.color())
        p.drawText(QRect(x, r.top(), r.right() - x, fm.height()), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(name, Qt.TextElideMode.ElideRight, r.right() - x))
        f = QFont(option.font)                       # the lines below: 90 % (pt or px fonts)
        if f.pointSizeF() > 0:
            f.setPointSizeF(f.pointSizeF() * 0.9)
        else:
            f.setPixelSize(max(9, int(f.pixelSize() * 0.9)))
        p.setFont(f)
        p.setPen(QColor(T["muted"]))
        fm2 = QFontMetrics(f)
        y = r.top() + fm.height() + 1
        for line in sub.split("\n"):
            p.drawText(QRect(x, y, r.right() - x, fm2.height()), Qt.AlignmentFlag.AlignVCenter,
                       fm2.elidedText(line, Qt.TextElideMode.ElideRight, r.right() - x))
            y += fm.height()
        p.restore()


class Bars(QWidget):
    """A small bar chart (theme colours read at paint time): values, labels under the first, middle
    and last bar, the largest value on the left."""

    def __init__(self, height: int = 110):
        super().__init__()
        self.setMinimumHeight(height)
        self.values: list[float] = []
        self.labels: list[str] = []
        self.unit = ""
        self.marks: set[int] = set()                 # bars drawn in the warn colour (gaps, short runs)

    def set(self, values, labels, unit: str = "", marks=()):
        self.values, self.labels, self.unit, self.marks = list(values), list(labels), unit, set(marks)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = QFontMetrics(self.font())
        r = self.rect().adjusted(4, 4, -4, -(fm.height() + 4))
        p.setPen(QColor(T["border"]))
        p.drawLine(r.left() + 46, r.bottom(), r.right(), r.bottom())
        if not self.values or max(self.values) <= 0:
            p.setPen(QColor(T["muted"]))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No data yet")
            p.end()
            return
        top = max(self.values)
        p.setPen(QColor(T["muted"]))
        p.drawText(QRect(r.left(), r.top(), 44, fm.height()), Qt.AlignmentFlag.AlignRight,
                   self._fmt(top))
        left = r.left() + 50
        n = len(self.values)
        w = max(1.0, (r.right() - left) / n)
        for i, v in enumerate(self.values):
            h = int((r.height() - 4) * v / top) if v > 0 else 0
            p.fillRect(int(left + i * w + w * 0.15), r.bottom() - h, max(1, int(w * 0.7)), h,
                       QColor(T["warn"] if i in self.marks else T["accent"]))
        p.setPen(QColor(T["muted"]))
        for i in sorted({0, n // 2, n - 1}):
            if i < len(self.labels) and self.labels[i]:
                x = int(left + i * w + w / 2)
                tw = fm.horizontalAdvance(self.labels[i])
                p.drawText(max(left, min(x - tw // 2, r.right() - tw)), self.rect().bottom() - 2, self.labels[i])
        p.end()

    def _fmt(self, v: float) -> str:
        if self.unit == "B":
            return fmt_bytes(v)
        return f"{v / 1e6:.1f}M" if v >= 1e6 else (f"{v / 1e3:.1f}k" if v >= 1e3 else f"{v:g}")


class HealthStrip(QFrame):
    """The top of the Logging document: is the data safe right now."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setObjectName("subBar")
        v = QVBoxLayout(self); v.setContentsMargins(12, 6, 12, 6); v.setSpacing(2)
        r1 = QHBoxLayout(); r1.setSpacing(14)
        self.state = QLabel(); f = self.state.font(); f.setBold(True); self.state.setFont(f)
        self.counts = QLabel()
        self.issue = QPushButton(); self.issue.setFlat(True)
        self.issue.clicked.connect(self._goto_issue)
        r1.addWidget(self.state); r1.addWidget(self.counts); r1.addStretch(1); r1.addWidget(self.issue)
        r2 = QHBoxLayout(); r2.setSpacing(14)
        self.disk = label("", muted=True, small=True)
        self.rate = label("", muted=True, small=True)
        r2.addWidget(self.disk); r2.addStretch(1); r2.addWidget(self.rate)
        v.addLayout(r1); v.addLayout(r2)
        self._issue_pid = None

    def refresh(self, kind: str | None = None):
        h = self.page.host.log_health()
        if h["recording"]:
            self.state.setText(f"● RECORDING  {time.strftime('%H:%M:%S', time.gmtime(h['elapsed']))}")
            self.state.setStyleSheet(f"color: {T['bad']};")
        else:
            self.state.setText("○ Ready - not recording")
            self.state.setStyleSheet(f"color: {T['muted']};")
        st = h["states"]
        parts = [f"{h['profiles']} profile(s)"] + [f"{st[k]} {STATE_TEXT[k].lower()}" for k in
                                                   ("writing", "armed", "stalled", "error") if st.get(k)]
        if kind:
            mine = [p for p in self.page.profiles if lt.type_of(p) == kind]
            parts.insert(0, f"{lt.TYPE[kind][1]}: {len(mine)} profile(s)")
        self.counts.setText("  ·  ".join(parts))
        issues = h["issues"]
        self._issue_pid = issues[0][0] if issues else None
        self.issue.setVisible(bool(issues))
        if issues:
            self.issue.setText(f"⚠ {len(issues)} issue(s) - {issues[0][1]}  ›")
            self.issue.setStyleSheet(f"color: {T['warn']}; text-align: right;")
            self.issue.setToolTip("\n".join(t for _p, t in issues))
        free = h["free"]
        if free is None:
            self.disk.setText(f"Disk {h['drive']}: not reachable")
            self.disk.setStyleSheet(f"color: {T['bad']};")
        else:
            days = h["days"]
            low = days is not None and days < 7
            self.disk.setText(f"Disk {h['drive']}  {fmt_bytes(free)} free" +
                              (f"  ·  ~{days:,.0f} days at {fmt_bytes(h['per_day'])}/day" if days else ""))
            self.disk.setStyleSheet(f"color: {T['bad'] if low else T['muted']};")
        self.rate.setText(f"{h['rate']:,.0f} rows/s  ·  dropped {h['dropped']}" if h["recording"] else
                          "Estimates from the tags and intervals - measured while recording")

    def _goto_issue(self):
        pr = self.page._profile(self._issue_pid)
        if pr is not None:
            self.page.show_type(lt.type_of(pr), pr["id"])


class ProfileView(QWidget):
    """The right side of a type: the selected profile - header, folder, tabs."""

    def __init__(self, page):
        super().__init__()
        self.page, self.host = page, page.host
        self.pid: str | None = None
        self._rows: dict[tuple, QTreeWidgetItem] = {}
        head = QFrame(); head.setObjectName("subBar")
        hv = QVBoxLayout(head); hv.setContentsMargins(12, 8, 12, 8); hv.setSpacing(4)
        h1 = QHBoxLayout(); h1.setSpacing(8)
        self.icon = QLabel()
        self.title = label("", name="sectionTitle")
        self.state = QLabel()
        self.btn_edit = page._button("Edit", "pencil", lambda: self.pid and self.host.edit_log_profile(self.pid),
                                     "Edit the profile (its pop-up)")
        self.btn_more = page._button("", "ellipsis", None, "More: enable / disable, test, duplicate, delete")
        self.btn_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more = QMenu(self.btn_more); self.more.aboutToShow.connect(self._fill_more)
        self.btn_more.setMenu(self.more)
        for b in (self.btn_edit, self.btn_more):
            b.setAutoRaise(True)
        h1.addWidget(self.icon); h1.addWidget(self.title); h1.addStretch(1)
        h1.addWidget(self.state); h1.addWidget(self.btn_edit); h1.addWidget(self.btn_more)
        h2 = QHBoxLayout(); h2.setSpacing(6)
        self.ficon = QLabel()
        self.folder = QLabel(); self.folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.folder.setMinimumWidth(120)
        self.btn_open = page._button("", "folder-open", lambda: self.pid and self.host.open_profile_folder(self.pid),
                                     "Open the folder")
        self.btn_copy = page._button("", "copy", self._copy_folder, "Copy the folder's path")
        for b in (self.btn_open, self.btn_copy):
            b.setAutoRaise(True)
        h2.addWidget(self.ficon); h2.addWidget(self.folder, 1); h2.addWidget(self.btn_open); h2.addWidget(self.btn_copy)
        hv.addLayout(h1); hv.addLayout(h2)

        self.tabs = QTabWidget(); self.tabs.setDocumentMode(True)
        # Overview
        ov = QWidget(); ovl = QVBoxLayout(ov); ovl.setContentsMargins(12, 10, 12, 10); ovl.setSpacing(8)
        self.sentence = QLabel(); self.sentence.setWordWrap(True)
        self.grid = QGridLayout(); self.grid.setHorizontalSpacing(14); self.grid.setVerticalSpacing(3)
        self._cells: list[QLabel] = []
        self.chart_title = label("Rows per minute - since the app started", muted=True, small=True)
        self.chart = Bars(120)
        self.problems = label("", small=True); self.problems.setWordWrap(True)
        ovl.addWidget(self.sentence); ovl.addLayout(self.grid); ovl.addWidget(self.chart_title)
        ovl.addWidget(self.chart); ovl.addWidget(self.problems); ovl.addStretch(1)
        self.tabs.addTab(ov, "Overview")
        # Tags
        tw = QWidget(); tv = QVBoxLayout(tw); tv.setContentsMargins(0, 0, 0, 0); tv.setSpacing(0)
        tbar = QFrame(); tbar.setObjectName("docBar")
        tb = QHBoxLayout(tbar); tb.setContentsMargins(8, 4, 8, 4); tb.setSpacing(2)
        self.tags_note = label("", muted=True, small=True)
        self.btn_add = page._button("Add tags...", "tag", lambda: self.pid and self.host.log_profile_add_tags(self.pid),
                                    "Add tags of any category, or whole assets (kept live)")
        self.btn_rm = page._button("Remove", "x", self._remove, "Remove the selected tags / assets from the profile")
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Search tags"); self.filter.setClearButtonEnabled(True)
        self.filter.setMaximumWidth(200)
        self.filter.textChanged.connect(lambda _: self._apply_filter())
        tb.addWidget(self.tags_note); tb.addSpacing(10); tb.addWidget(self.btn_add); tb.addWidget(self.btn_rm)
        tb.addStretch(1); tb.addWidget(self.filter)
        self.tags = QTreeWidget(); self.tags.setHeaderLabels(TAG_COLS)
        self.tags.setAlternatingRowColors(True); self.tags.setUniformRowHeights(True)
        self.tags.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for c, wd in enumerate((200, 150, 55, 130, 70)):
            self.tags.header().resizeSection(c, wd)
        self.tags.header().setSectionResizeMode(len(TAG_COLS) - 1, QHeaderView.ResizeMode.Stretch)
        self.tags.itemSelectionChanged.connect(self._update_buttons)
        self.tags.itemDoubleClicked.connect(self._tag_open)
        self.tags.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tags.customContextMenuRequested.connect(self._tag_menu)
        tv.addWidget(tbar); tv.addWidget(self.tags, 1)
        self.tabs.addTab(tw, "Tags")
        # Files
        fw = QWidget(); fv = QVBoxLayout(fw); fv.setContentsMargins(0, 0, 0, 0); fv.setSpacing(0)
        fbar = QFrame(); fbar.setObjectName("docBar")
        fb = QHBoxLayout(fbar); fb.setContentsMargins(8, 4, 8, 4); fb.setSpacing(2)
        self.btn_fopen = page._button("Open", "folder-open", lambda: self._file_act("open"), "Open the file")
        self.btn_fdir = page._button("Show in folder", "folder", lambda: self._file_act("dir"), "Open its folder")
        self.btn_fcopy = page._button("Copy path", "copy", lambda: self._file_act("copy"), "Copy the file's path")
        self.period = QComboBox()
        for text, days in PERIODS:
            self.period.addItem(text, days)
        self.period.setCurrentIndex(1)
        self.period.currentIndexChanged.connect(lambda _i: self._fill_files())
        self.files_note = label("", muted=True, small=True)
        for w_ in (self.btn_fopen, self.btn_fdir, self.btn_fcopy):
            fb.addWidget(w_)
        fb.addStretch(1); fb.addWidget(self.files_note); fb.addWidget(label("Period", muted=True)); fb.addWidget(self.period)
        self.files = QTreeWidget(); self.files.setHeaderLabels(FILE_COLS)
        self.files.setRootIsDecorated(False); self.files.setAlternatingRowColors(True); self.files.setUniformRowHeights(True)
        for c, wd in enumerate((380, 130, 90)):
            self.files.header().resizeSection(c, wd)
        self.files.header().setSectionResizeMode(len(FILE_COLS) - 1, QHeaderView.ResizeMode.Stretch)
        self.files.itemDoubleClicked.connect(lambda *_: self._file_act("open"))
        self.files.itemSelectionChanged.connect(self._update_buttons)
        fv.addWidget(fbar); fv.addWidget(self.files, 1)
        self.tabs.addTab(fw, "Files")
        # History
        hw = QWidget(); hl = QVBoxLayout(hw); hl.setContentsMargins(12, 10, 12, 10); hl.setSpacing(6)
        self.hist_title = label("", muted=True, small=True)
        self.hist_chart = Bars(110)
        self.hist = QTreeWidget(); self.hist.setRootIsDecorated(False); self.hist.setAlternatingRowColors(True)
        self.hist.setUniformRowHeights(True)
        self.hist_note = label("", muted=True, small=True); self.hist_note.setWordWrap(True)
        hl.addWidget(self.hist_title); hl.addWidget(self.hist_chart); hl.addWidget(self.hist, 1); hl.addWidget(self.hist_note)
        self.tabs.addTab(hw, "History")
        self.tabs.currentChanged.connect(lambda _i: self._fill_tab())   # after every tab exists

        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(head); v.addWidget(self.tabs, 1)

    def profile(self) -> dict | None:
        return next((x for x in self.host.project.logging_profiles if x["id"] == self.pid), None)

    def _copy_folder(self):
        QApplication.clipboard().setText(self.folder.toolTip())
        self.btn_copy.setToolTip("Copied ✓")
        QTimer.singleShot(1500, lambda: self.btn_copy.setToolTip("Copy the folder's path"))

    def show_profile(self, pid: str):
        changed = pid != self.pid
        self.pid = pid
        pr = self.profile()
        if pr is None:
            return
        kind = lt.type_of(pr)
        self.icon.setPixmap(make_icon(lt.TYPE[kind][2], T["text"]).pixmap(QSize(18, 18)))
        self.title.setText(pr["name"])
        self.ficon.setPixmap(make_icon("folder", T["muted"]).pixmap(QSize(16, 16)))
        self.tabs.setTabText(1, f"Tags ({sum(len(r[3]) if r[0] == 'asset' else 1 for r in self.host.profile_tag_rows(pr))})")
        if changed:
            self.filter.clear()
        self._fill_head()
        self._fill_tab(force=True)

    # ---------------------------------------------------------------- header + Overview
    def _fill_head(self):
        pr = self.profile()
        if pr is None:
            return
        live = self.host.profile_live(pr)
        st, _count = self.host.log_status(pr)
        last = f" · last row {fmt_ago(live['last'])}" if live["last"] is not None else ""
        self.state.setText(("○ Off" + ("" if pr["enabled"] else " (disabled)") + " · writes on Record")
                           if st == "off" else f"● {STATE_TEXT[st]}{last}")
        self.state.setStyleSheet(f"color: {T[STATE_TOKEN[st]]};")
        folder, note, bad = live["folder"], live["folder_note"], live["folder_bad"]
        fm = QFontMetrics(self.folder.font())
        self.folder.setText(fm.elidedText(folder, Qt.TextElideMode.ElideMiddle, max(160, self.folder.width() - 10))
                            + (f"  {note}" if note else ""))
        self.folder.setToolTip(folder)
        self.folder.setStyleSheet(f"color: {T['bad']};" if bad else "")
        self.btn_edit.setEnabled(not self.host.recording)

    def _sentence(self, pr: dict) -> str:
        kind, out = lt.type_of(pr), lf.normalize_output(dict(pr))
        when = lt.describe(pr, self.host.key_name, self.host.alarm_name)
        what = {"interval": f"Writes {when}", "change": f"Writes a row when a tag moves {when}",
                "trigger": f"Writes while {when}", "event": f"Writes one row on {when}",
                "batch": f"Writes per batch - {when}", "statistic": f"Writes statistics {when}"}[kind]
        folders = "the session folder" if out["folders"] == "session" else \
            (f"{lf.FOLDER[out['folders']][1]} folders" if out["folders"] != "custom" else f"folders {out['pattern']}")
        keep = f", keeps {out['keep_days']} days" if out.get("keep_days") else ""
        return f"{what} into {lf.describe(out).replace('every', 'a new file every')}, in {folders}{keep}, " \
               f"{pr.get('format', 'CSV')}" + (" (compressed when closed)" if out.get("compress") else "") + "."

    def _card_rows(self, pr: dict, live: dict) -> list[tuple[str, str]]:
        kind, w, out = lt.type_of(pr), pr.get("when") or {}, lf.normalize_output(dict(pr))
        name = self.host.key_name
        rows: list[tuple[str, str]] = []
        if kind == "interval":
            rows += [("Every", "every value" if not w.get("every_s") else lt.fmt_s(w["every_s"])),
                     ("Raw .wpk", "yes" if pr.get("keep_raw") else "no")]
        elif kind == "change":
            rows += [("Deadband", f"±{w.get('deadband', 0):g}" + (" %" if w.get("mode") == "pct" else "")),
                     ("Heartbeat", lt.fmt_s(w.get("heartbeat_s")) if w.get("heartbeat_s") else "none")]
        elif kind == "trigger":
            rows += [("Start when", lt.describe_cond(w.get("start"), name) +
                      (f" ({lt.fmt_s(w['debounce_s'])})" if w.get("debounce_s") else "")),
                     ("Stop", lt.describe_cond(w.get("stop"), name) if w.get("stop") else "start is false"),
                     ("Pre / post", f"{lt.fmt_s(w.get('pre_s'))} / {lt.fmt_s(w.get('post_s'))}")]
        elif kind == "event":
            rows += [("On", lt.describe(pr, name, self.host.alarm_name)),
                     ("Debounce", lt.fmt_s(w.get("debounce_s")) if w.get("debounce_s") else "none")]
        elif kind == "batch":
            rows += [("Batches by", lt.describe(pr, name)),
                     ("Summary", ", ".join(dl.STAT_LABELS[s] for s in w.get("stats", [])) or "-")]
        else:
            rows += [("Period", lt.fmt_s(pr.get("interval_s", 60))),
                     ("Statistics", ", ".join(dl.STAT_LABELS[s] for s in pr.get("stats", [])))]
        est = "" if live["measured"] else " (est.)"
        rows += [("Format", pr.get("format", "CSV")),
                 ("New file", lf.describe(out)),
                 ("Rate", fmt_rate(live["rate"]) + est if live["rate"] is not None else "depends on the process"),
                 ("Size/day", fmt_bytes(live["per_day"]) + est if live["per_day"] is not None else "-"),
                 ("Keep", (f"{out['keep_days']} days" + (f" → {fmt_bytes(live['per_day'] * out['keep_days'])}"
                                                        if live["per_day"] else "")) if out.get("keep_days") else "forever")]
        if live.get("next"):
            rows.append(("Next file", f"in {fmt_span(live['next'])}"))
        st, count = self.host.log_status(pr)
        if count:
            rows.append(("Count", count))
        if live.get("rows") is not None:
            rows.append(("Rows", f"{live['rows']:,}"))
        if live.get("files"):
            rows.append(("Files", f"{live['files']} · {fmt_bytes(live.get('bytes', 0))}"))
        if live.get("now"):
            rows.append(("Now", os.path.basename(live["now"])))
        if pr.get("description"):
            rows.append(("Description", pr["description"]))
        return rows

    def _fill_overview(self):
        pr = self.profile()
        if pr is None:
            return
        live = self.host.profile_live(pr)
        self.sentence.setText(self._sentence(pr))
        rows = self._card_rows(pr, live)
        for c in self._cells:
            c.setParent(None); c.deleteLater()
        self._cells = []
        half = (len(rows) + 1) // 2
        for i, (k, val) in enumerate(rows):
            col, row = (0, i) if i < half else (2, i - half)
            a, b = label(k, muted=True, small=True), QLabel(str(val))
            b.setToolTip(str(val)); b.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.grid.addWidget(a, row, col); self.grid.addWidget(b, row, col + 1)
            self._cells += [a, b]
        self.grid.setColumnStretch(1, 1); self.grid.setColumnStretch(3, 1)
        hist = self.page.activity.get(pr["id"], [])
        vals = [n for _t, n in hist]
        self.chart.set(vals, [time.strftime("%H:%M", time.localtime(t)) for t, _n in hist])
        probs = self.host.profile_problems(pr)
        self.problems.setText("Problems  " + ("none" if not probs else
                                              "  ·  ".join(f"{fmt_time(t)} {txt}" for t, txt in probs[:4])))
        self.problems.setStyleSheet(f"color: {T['warn'] if probs else T['muted']};")

    # ---------------------------------------------------------------- tabs
    def _fill_tab(self, force: bool = False):
        i = self.tabs.currentIndex()
        if i == 0:
            self._fill_overview()
        elif i == 1:
            self._fill_tags()
        elif i == 2:
            self._fill_files()
        else:
            self._fill_history()
        self._update_buttons()

    def _fill_tags(self):
        pr = self.profile()
        t = self.tags
        t.clear(); self._rows.clear()
        if pr is None:
            return
        for r in self.host.profile_tag_rows(pr):
            if r[0] == "asset":
                _k, aid, text, keys = r
                it = QTreeWidgetItem([f"{text}  (asset · {len(keys)} tags · kept live)", "", "", "", "", ""])
                it.setIcon(0, make_icon("boxes", T["text"]))
                it.setData(0, Qt.ItemDataRole.UserRole, ("asset", aid))
                f = it.font(0); f.setBold(True); it.setFont(0, f)
                for key in keys:
                    ch = self._tag_item(key, text)
                    ch.setFlags(ch.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    it.addChild(ch)
                t.addTopLevelItem(it)
            else:
                t.addTopLevelItem(self._tag_item(r[1], r[2]))
        self.tags_note.setText("All enabled - new tags join" if pr.get("channels") is None else
                               "Whole assets are kept live")
        self._apply_filter()
        self.update_live()

    def _tag_item(self, key, asset: str) -> QTreeWidgetItem:
        lbl = self.host.key_label(key)
        src, _sep, name = lbl.rpartition(" · ")
        it = QTreeWidgetItem([name or lbl, src or "-", self.host.last_value(*key)[1], "", "", asset])
        it.setIcon(0, swatch(self.host.color_of(*key)))
        it.setData(0, Qt.ItemDataRole.UserRole, ("tag", tuple(key)))
        it.setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._rows[(id(it), tuple(key))] = it
        return it

    def update_live(self):
        bad, ok, muted = QBrush(QColor(T["bad"])), QBrush(QColor(T["ok"])), QBrush(QColor(T["muted"]))
        for (_i, key), it in self._rows.items():
            val, qual, good, stamp = self.host.tag_live(*key)
            text = f"{val}   {stamp}" if val and stamp else (val or "")
            if it.text(3) != text:
                it.setText(3, text)
            if it.text(4) != (qual or ""):
                it.setText(4, qual or "")
            it.setForeground(4, ok if good else (bad if good is False else muted))

    def _fill_files(self):
        pr = self.profile()
        self.files.clear()
        if pr is None:
            return
        rows = self.host.profile_files_list(pr, self.period.currentData())
        for r in rows:
            it = QTreeWidgetItem([r["rel"], fmt_time(r["mtime"]), fmt_bytes(r["size"]),
                                  "● writing" if r["state"] == "writing" else "✓ complete"])
            it.setData(0, Qt.ItemDataRole.UserRole, r["path"])
            it.setToolTip(0, r["path"])
            it.setForeground(3, QBrush(QColor(T["ok"] if r["state"] == "writing" else T["muted"])))
            it.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.files.addTopLevelItem(it)
        total = sum(r["size"] for r in rows)
        self.files_note.setText(f"{len(rows)} file(s) · {fmt_bytes(total)}" if rows else "No files in this period")
        self.tabs.setTabText(2, f"Files ({len(rows)})")

    def _file_act(self, what: str):
        sel = self.files.selectedItems()
        if not sel:
            return
        path = sel[0].data(0, Qt.ItemDataRole.UserRole)
        if what == "copy":
            QApplication.clipboard().setText(path)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path if what == "open" else os.path.dirname(path)))

    def _fill_history(self):
        pr = self.profile()
        h = self.hist
        h.clear()
        if pr is None:
            return
        kind = lt.type_of(pr)
        if kind in ("trigger", "batch"):
            runs = self.host.profile_runs(pr)
            h.setHeaderLabels(["Run" if kind == "trigger" else "Batch", "Start", "End", "Duration"])
            now = time.time()
            for r in runs:
                dur = (r["end"] or now) - r["start"]
                h.addTopLevelItem(QTreeWidgetItem([r["label"], fmt_time(r["start"]),
                                                   "running" if r["end"] is None else fmt_time(r["end"]), fmt_span(dur)]))
            total = sum(((r["end"] or now) - r["start"]) for r in runs)
            self.hist_title.setText(f"{'Runs' if kind == 'trigger' else 'Batches'} since the app started: "
                                    f"{len(runs)} · {fmt_span(total)}" if runs else "No runs yet - they start on Record")
            hours = [0.0] * 24                           # minutes active per hour today
            day0 = _dt.datetime.combine(_dt.date.today(), _dt.time()).timestamp()
            for r in runs:
                a, b = max(r["start"], day0), min(r["end"] or now, day0 + 86400)
                while a < b:
                    hr = int((a - day0) // 3600)
                    nxt = min(b, day0 + (hr + 1) * 3600)
                    hours[hr] += (nxt - a) / 60
                    a = nxt
            self.hist_chart.set(hours, [f"{i:02d}:00" for i in range(24)])
            self.hist_note.setText("Minutes running per hour today · the files of each run: Files tab")
        elif kind == "event":
            cols, rows = self.host.profile_events(pr)
            h.setHeaderLabels(cols[:1] + cols[2:] if cols else ["Time", "Event"])
            for r in rows:
                h.addTopLevelItem(QTreeWidgetItem(r[:1] + r[2:]))
            self.hist_title.setText(f"The newest events ({len(rows)})" if rows else "No events yet")
            per = {}
            for r in rows:
                per[r[0][:13]] = per.get(r[0][:13], 0) + 1
            keys = sorted(per)[-24:]
            self.hist_chart.set([per[k] for k in keys], [k[11:13] + ":00" for k in keys])
            self.hist_note.setText("Events per hour (the newest file)")
        else:
            files = self.host.profile_files_list(pr, 30)
            per: dict[str, int] = {}
            for f in files:
                d = time.strftime("%Y-%m-%d", time.localtime(f["mtime"]))
                per[d] = per.get(d, 0) + f["size"]
            days = [(_dt.date.today() - _dt.timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
            vals = [per.get(d, 0) for d in days]
            gaps = {i for i, v in enumerate(vals) if not v and any(vals[:i])}
            self.hist_chart.set(vals, [d[5:] for d in days], "B", gaps)
            h.setHeaderLabels(["Day", "Size", "Files"])
            for d in reversed(days):
                if d in per:
                    n = sum(1 for f in files if time.strftime("%Y-%m-%d", time.localtime(f["mtime"])) == d)
                    h.addTopLevelItem(QTreeWidgetItem([d, fmt_bytes(per[d]), str(n)]))
            self.hist_title.setText("Size written per day - last 30 days")
            self.hist_note.setText("Amber: a day without data between days with data (not recording, device offline)"
                                   if gaps else "")

    def refresh_live(self):
        self._fill_head()
        i = self.tabs.currentIndex()
        if i == 0:
            self._fill_overview()
        elif i == 1:
            self.update_live()

    # ---------------------------------------------------------------- tags actions
    def _apply_filter(self):
        text = self.filter.text().strip().lower()
        for i in range(self.tags.topLevelItemCount()):
            it = self.tags.topLevelItem(i)
            kids = [it.child(k) for k in range(it.childCount())]
            hit = not text or any(text in it.text(c).lower() for c in range(2))
            any_kid = False
            for k in kids:
                show = not text or hit or any(text in k.text(c).lower() for c in range(2))
                k.setHidden(not show); any_kid |= show
            it.setHidden(not (hit or any_kid))

    def _selected(self):
        keys, aids = [], []
        for it in self.tags.selectedItems():
            kind, ident = it.data(0, Qt.ItemDataRole.UserRole)
            (aids if kind == "asset" else keys).append(ident)
        return keys, aids

    def _remove(self):
        keys, aids = self._selected()
        if self.pid and (keys or aids):
            self.host.log_profile_remove(self.pid, keys, aids)

    def _tag_open(self, it, _c):
        kind, ident = it.data(0, Qt.ItemDataRole.UserRole)
        if kind == "tag":
            self.host.open_tag(ident)

    def _tag_menu(self, pos):
        it = self.tags.itemAt(pos)
        if it is None:
            return
        kind, ident = it.data(0, Qt.ItemDataRole.UserRole)
        rec = self.host.recording
        m = QMenu(self)
        if kind == "tag":
            m.addAction("Open tag...", lambda: self.host.open_tag(ident))
            m.addAction("Add alarm for this tag...", lambda: self.host.add_alarm_for(ident))
        if it.flags() & Qt.ItemFlag.ItemIsSelectable:
            if not it.isSelected():
                self.tags.clearSelection(); it.setSelected(True)
            m.addSeparator()
            m.addAction("Remove from profile", self._remove).setEnabled(not rec)
        m.exec(self.tags.viewport().mapToGlobal(pos))

    def _fill_more(self):
        m = self.more
        m.clear()
        pr = self.profile()
        if pr is None:
            return
        rec = self.host.recording
        m.addAction("Enable" if not pr["enabled"] else "Disable", lambda: self.host.toggle_log_profile(self.pid)).setEnabled(not rec)
        if lt.type_of(pr) in ("trigger", "event"):
            m.addAction("Test (fire once now)", lambda: self.host.test_log_profile(self.pid)).setEnabled(rec)
        m.addAction("Duplicate", lambda: self.host.dup_log_profile(self.pid)).setEnabled(not rec)
        m.addSeparator()
        m.addAction("Delete...", lambda: self.host.delete_log_profile(self.pid)).setEnabled(not rec)

    def _update_buttons(self):
        rec = self.host.recording
        keys, aids = self._selected()
        self.btn_add.setEnabled(not rec and self.pid is not None)
        self.btn_rm.setEnabled(not rec and bool(keys or aids))
        on = bool(self.files.selectedItems())
        for b in (self.btn_fopen, self.btn_fdir, self.btn_fcopy):
            b.setEnabled(on)


class LoggingPage(QWidget):
    ACTIVITY_N = 24 * 60                             # rows per minute: the last 24 h (in this app session)

    def __init__(self, host):
        super().__init__()
        self.host = host
        self._kind: str | None = None                # None = All profiles
        self._pid: str | None = None                 # the profile selected (a type)
        self._only: str | None = None                # the overview: only this type (a click on the left)
        self._rows: dict[str, QTreeWidgetItem] = {}
        self._items: dict = {}
        self.activity: dict[str, list[tuple[float, int]]] = {}   # pid -> [(minute, rows)]
        self._act_last: dict[str, tuple[float, int]] = {}

        bar = QFrame(); bar.setObjectName("docBar")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(2)
        self.btn_add = self._button("Add", "plus", None, "Add a logging profile")
        self.add_menu = QMenu(self.btn_add)
        self.add_menu.aboutToShow.connect(lambda: self._fill_add(self.add_menu))
        self.btn_add.setMenu(self.add_menu); self.btn_add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_edit = self._button("Edit", "pencil", lambda: self._act(self.host.edit_log_profile),
                                     "Edit the selected profile")
        self.btn_dup = self._button("", "copy", lambda: self._act(self.host.dup_log_profile), "Duplicate the profile")
        self.btn_del = self._button("", "trash", lambda: self._act(self.host.delete_log_profile), "Delete the profile")
        self.btn_on = self._button("Disable", "power", lambda: self._act(self.host.toggle_log_profile),
                                   "Enable / disable the selected profile")
        fixed_width(self.btn_on, "Enable", "Disable")
        self.btn_test = self._button("Test", "zap", lambda: self._act(self.host.test_log_profile),
                                     "Trigger / event: fire it once now (while recording)")
        self.btn_open = self._button("Open folder", "folder-open", self._open_folder,
                                     "Open the selected profile's folder (today's / the running one)")
        for w in (self.btn_add, self.btn_edit, self.btn_dup, self.btn_del, None, self.btn_on, self.btn_test, None,
                  self.btn_open):
            bl.addWidget(vsep() if w is None else w)
        bl.addStretch(1)
        self.filter = QLineEdit(); self.filter.setPlaceholderText("Search")
        self.filter.setClearButtonEnabled(True); self.filter.setMaximumWidth(220); self.filter.setMinimumWidth(120)
        self.filter.textChanged.connect(lambda _: self._apply_filter())
        bl.addWidget(self.filter)
        self.health = HealthStrip(self)

        self.band = Band()
        self.tree = QTreeWidget(); self.tree.setObjectName("navTree")
        self.tree.setHeaderHidden(True); self.tree.setIndentation(6); self.tree.setRootIsDecorated(False)
        self.tree.setProperty("plainSel", True)
        self.tree.setItemDelegate(TwoLineDelegate(self.tree))
        self.tree.currentItemChanged.connect(lambda cur, _p: self._left_selected(cur))
        self.tree.itemDoubleClicked.connect(self._left_double)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._left_menu)
        self.tree.installEventFilter(self)
        left = QWidget(); lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(0)
        lv.addWidget(self.band); lv.addWidget(self.tree, 1)

        self.table = QTreeWidget()
        self.table.setRootIsDecorated(False); self.table.setAlternatingRowColors(True)
        self.table.setUniformRowHeights(True); self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        self.table.setIconSize(QSize(*STATUS_SIZE))
        self.table.setHeaderLabels(ALL_COLS)
        for c, wd in enumerate((160, 85, 150, 150, 80, 65, 90)):
            self.table.header().resizeSection(c, wd)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)      # long folders: D:\Lo…
        self.table.header().setSectionResizeMode(len(ALL_COLS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._row_selected)
        self.table.itemDoubleClicked.connect(lambda it, _c: self.show_type(
            lt.type_of(self._profile(it.data(0, Qt.ItemDataRole.UserRole)) or {}), it.data(0, Qt.ItemDataRole.UserRole)))
        self.table.itemChanged.connect(self._ticked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(
            lambda pos: (it := self.table.itemAt(pos)) and self._profile_menu(
                it.data(0, Qt.ItemDataRole.UserRole), self.table.viewport().mapToGlobal(pos)))
        self.view = ProfileView(self)
        self.empty = EmptyState()
        self.stack = QStackedWidget()
        for w in (self.table, self.view, self.empty):
            self.stack.addWidget(w)
        self.hint = label("", muted=True, small=True); self.hint.setContentsMargins(8, 2, 8, 2)
        self.hint.setWordWrap(True)
        self.hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        rv.addWidget(self.stack, 1); rv.addWidget(self.hint)
        split = QSplitter(); split.addWidget(left); split.addWidget(right)
        split.setStretchFactor(1, 1); split.setSizes([260, 780]); split.setChildrenCollapsible(False)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        v.addWidget(bar); v.addWidget(self.health); v.addWidget(split, 1)

    def _button(self, text, icon, slot, tip) -> QToolButton:
        b = QToolButton(); b.setToolTip(tip)
        if text:
            b.setText(text); b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.host.reg_icon(b, icon)
        if slot:
            b.clicked.connect(slot)
        return b

    def eventFilter(self, obj, ev):
        """Backspace in a type's list: back to every profile (the Project tree does the same)."""
        if obj is self.tree and ev.type() == ev.Type.KeyPress and ev.key() == Qt.Key.Key_Backspace \
                and self._kind is not None:
            self.show_type(None)
            return True
        return False

    # ---------------------------------------------------------------- state
    @property
    def kind(self) -> str | None:
        return self._kind

    @property
    def profiles(self) -> list[dict]:
        return self.host.project.logging_profiles

    def _profile(self, pid) -> dict | None:
        return next((x for x in self.profiles if x["id"] == pid), None)

    def current(self) -> str | None:
        if self._kind is not None:
            return self._pid
        sel = self.table.selectedItems()
        return sel[0].data(0, Qt.ItemDataRole.UserRole) if sel and self.stack.currentWidget() is self.table else None

    def _act(self, fn):
        pid = self.current()
        if pid:
            fn(pid)

    def _open_folder(self):
        pid = self.current()
        if pid:
            self.host.open_profile_folder(pid)
        else:
            self.host.open_log_folder()

    def show_type(self, kind: str | None = None, select: str | None = None):
        """Project tree > Logging (None: every profile) / a type (select: that profile)."""
        self._kind = kind if kind in lt.TYPE else None
        if self._kind is None:
            self._only = None
        self._pid = select
        self.refresh(select)
        self.activate()

    def _sub(self, pr: dict) -> str:
        """The lines under a profile's name in a type's list."""
        live = self.host.profile_live(pr)
        line1 = lt.describe(pr, self.host.key_name, self.host.alarm_name) + " · " + self.host.profile_tags_text(pr)
        if not pr["enabled"]:
            return line1 + "\noff"
        bits = [b for b in (fmt_rate(live["rate"]) if live["rate"] is not None else "",
                            f"{fmt_bytes(live['per_day'])}/day" if live["per_day"] else "") if b]
        return line1 + ("\n" + " · ".join(bits) if bits else "")

    def refresh(self, select: str | None = None):
        cur = select or self.current()
        mine = [pr for pr in self.profiles if self._kind is None or lt.type_of(pr) == self._kind]
        if self._kind is not None:
            ids = [pr["id"] for pr in mine]
            self._pid = cur if cur in ids else (ids[0] if ids else None)
        self.tree.blockSignals(True)
        self.tree.clear(); self._items.clear()
        if self._kind is None:
            self.band.set("Logging", "file-text", "Logging profiles by type - every enabled one runs on Record")
            counts = {k: 0 for k in lt.TYPE}
            for pr in self.profiles:
                counts[lt.type_of(pr)] += 1
            root = QTreeWidgetItem([f"All profiles  ({len(self.profiles)})"])
            root.setIcon(0, make_icon("list", T["text"]))
            root.setData(0, Qt.ItemDataRole.UserRole, ("all", None))
            self.tree.addTopLevelItem(root)
            for k, name, icon, what in lt.TYPES:
                it = QTreeWidgetItem([f"{name}  ({counts[k]})"])
                it.setIcon(0, make_icon(icon, T["text"] if counts[k] else T["muted"]))
                if not counts[k]:
                    it.setForeground(0, QBrush(QColor(T["muted"])))
                it.setData(0, Qt.ItemDataRole.UserRole, ("type", k))
                it.setToolTip(0, what + " - click: only these · double-click: open the type")
                self.tree.addTopLevelItem(it)
                self._items[("type", k)] = it
            self.tree.setCurrentItem(self._items.get(("type", self._only), root))
        else:
            k, name, icon, what = lt.TYPE[self._kind]
            self.band.set(f"{name}  ({len(mine)})", icon, what + " - Backspace / the Project tree: every profile")
            for pr in mine:
                st, _c = self.host.log_status(pr)
                it = QTreeWidgetItem([pr["name"]])
                it.setData(0, SUB, self._sub(pr))
                it.setIcon(0, status_icon("", STATE_DOT[st]) if pr["enabled"] else status_icon("", "none"))
                it.setData(0, Qt.ItemDataRole.UserRole, ("profile", pr["id"]))
                if not pr["enabled"]:
                    it.setForeground(0, QBrush(QColor(T["muted"])))
                self.tree.addTopLevelItem(it)
                self._items[pr["id"]] = it
            if self._pid in self._items:
                self.tree.setCurrentItem(self._items[self._pid])
        self.tree.blockSignals(False)
        self.health.refresh(self._kind)
        self._fill_right(cur)

    def _left_selected(self, it):
        if it is None:
            return
        kind, ident = it.data(0, Qt.ItemDataRole.UserRole)
        if kind in ("type", "all") and self._kind is None:            # the overview: filter the table
            if ident != self._only:
                self._only = ident
                QTimer.singleShot(0, lambda: self._fill_right(None))
            self.host.select_object(("logtype", ident))
        elif kind == "profile" and ident != self._pid:
            self._pid = ident
            QTimer.singleShot(0, lambda: self._fill_right(ident))
            self.activate()

    def _left_double(self, it, _c):
        kind, ident = it.data(0, Qt.ItemDataRole.UserRole)
        if kind == "profile":
            QTimer.singleShot(0, lambda: self.host.edit_log_profile(ident))
        elif kind == "type":                                           # open the type page
            QTimer.singleShot(0, lambda: self.show_type(ident))

    def _left_menu(self, pos):
        it = self.tree.itemAt(pos)
        if it is not None and it.data(0, Qt.ItemDataRole.UserRole)[0] == "profile":
            self.tree.setCurrentItem(it)
            self._profile_menu(it.data(0, Qt.ItemDataRole.UserRole)[1], self.tree.viewport().mapToGlobal(pos))

    def activate(self):
        pid = self.current()
        self.host.select_object(("lprof", pid) if pid else ("logtype", self._kind or self._only))

    # ---------------------------------------------------------------- the right side
    def _fill_right(self, select: str | None = None):
        if self._kind is None:
            self._fill_table(select)
        elif self._pid is None:
            k = self._kind
            name = lt.TYPE[k][1]
            self.empty.set(lt.TYPE[k][2], f"No {name.lower()} logging yet", EMPTY_TEXT[k],
                           [(f"Add {name.lower()} profile", lambda: self.host.add_log_profile(k), True)])
            self.stack.setCurrentWidget(self.empty)
        else:
            self.view.show_profile(self._pid)
            self.stack.setCurrentWidget(self.view)
        self.hint.setText(self.host.session_files_text() if self._kind is None else "")
        self.hint.setVisible(self._kind is None)
        self._update_buttons()

    def _table_values(self, pr: dict) -> list[str]:
        k = lt.type_of(pr)
        st, count = self.host.log_status(pr)
        live = self.host.profile_live(pr)
        est = "" if live["measured"] else " (est.)"
        return [pr["name"], lt.TYPE[k][1], lt.describe(pr, self.host.key_name, self.host.alarm_name),
                live["folder"] + (f"  {live['folder_note']}" if live["folder_note"] else ""),
                fmt_ago(live["last"]) if self.host.recording else "-",
                fmt_rate(live["rate"]) if live["rate"] is not None else "-",
                fmt_bytes(live["per_day"]) + est if live["per_day"] is not None else "-",
                STATE_TEXT[st] + (f" · {count}" if count else "")]

    def _fill_table(self, select: str | None = None):
        t = self.table
        t.blockSignals(True); t.setSortingEnabled(False)
        t.clear(); self._rows.clear()
        locked = self.host.recording
        s_col = ALL_COLS.index("Status")
        for pr in self.profiles:
            k = lt.type_of(pr)
            if self._only not in (None, k):
                continue
            st, _count = self.host.log_status(pr)
            live = self.host.profile_live(pr)
            it = QTreeWidgetItem(self._table_values(pr))
            it.setToolTip(3, live["folder"])
            it.setData(0, Qt.ItemDataRole.UserRole, pr["id"])
            it.setIcon(0, make_icon(lt.TYPE[k][2], T["text"] if pr["enabled"] else T["muted"]))
            it.setIcon(s_col, status_icon("", STATE_DOT[st]))
            for c in (4, 5, 6):
                it.setTextAlignment(c, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if not locked:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            it.setFlags(flags)
            it.setCheckState(0, Qt.CheckState.Checked if pr["enabled"] else Qt.CheckState.Unchecked)
            if not pr["enabled"]:
                for c in range(len(ALL_COLS)):
                    it.setForeground(c, QBrush(QColor(T["muted"])))
            if live["folder_bad"]:
                it.setForeground(3, QBrush(QColor(T["bad"])))
            if st != "off":
                it.setForeground(s_col, QBrush(QColor(T[STATE_TOKEN[st]])))
            t.addTopLevelItem(it)
            self._rows[pr["id"]] = it
        t.setSortingEnabled(True)
        if select in self._rows:
            self._rows[select].setSelected(True)
            t.scrollToItem(self._rows[select])
        t.blockSignals(False)
        self._apply_filter()
        if not self._rows and self._only:
            k, name = self._only, lt.TYPE[self._only][1]
            self.empty.set(lt.TYPE[k][2], f"No {name.lower()} profiles", EMPTY_TEXT[k],
                           [(f"Add {name.lower()} profile", lambda: self.host.add_log_profile(k), True)])
            self.stack.setCurrentWidget(self.empty)
        elif not self._rows:
            m = QMenu(self); self._fill_add(m, every=True); self._keep = m
            self.empty.set("file-text", "No logging profiles yet",
                           "A profile says when to write, which tags and where - add one of the six types.",
                           [("Add profile", m, True)])
            self.stack.setCurrentWidget(self.empty)
        else:
            self.stack.setCurrentWidget(self.table)

    def sample_activity(self):
        """Once a minute while recording: rows written per profile (the Overview chart)."""
        rec = self.host.recorder if hasattr(self.host, "recorder") else None
        if rec is None:
            self._act_last.clear()
            return
        now = time.time()
        for i, pr in enumerate(rec.profiles):
            pid = pr.get("id")
            if not pid:
                continue
            n = rec.written(i)
            t0, n0 = self._act_last.get(pid, (now, n))
            if now - t0 >= 60 or pid not in self._act_last:
                if pid in self._act_last:
                    hist = self.activity.setdefault(pid, [])
                    hist.append((now, max(0, n - n0)))
                    del hist[:-self.ACTIVITY_N]
                self._act_last[pid] = (now, n)

    def update_live(self):
        """Health, status, counts and values (the GUI timer, while the page is shown)."""
        self.sample_activity()
        self.health.refresh(self._kind)
        if self.stack.currentWidget() is self.view:
            self.view.refresh_live()
            pr = self._profile(self._pid)
            it = self._items.get(self._pid)
            if pr is not None and it is not None:
                st, _c = self.host.log_status(pr)
                it.setIcon(0, status_icon("", STATE_DOT[st]) if pr["enabled"] else status_icon("", "none"))
                it.setData(0, SUB, self._sub(pr))
        elif self.stack.currentWidget() is self.table:
            self.table.blockSignals(True)
            for pid, it in self._rows.items():
                pr = self._profile(pid)
                if pr is None:
                    continue
                st, _count = self.host.log_status(pr)
                for c, text in enumerate(self._table_values(pr)):
                    if c >= 4 and it.text(c) != text:
                        it.setText(c, text)
                it.setIcon(7, status_icon("", STATE_DOT[st]))
                it.setForeground(7, QBrush(QColor(T[STATE_TOKEN[st]])) if st != "off" else QBrush())
            self.table.blockSignals(False)

    def _apply_filter(self):
        text = self.filter.text().strip().lower()
        for it in self._rows.values():
            it.setHidden(bool(text) and not any(text in it.text(c).lower() for c in range(4)))
        for pid, it in self._items.items():
            if isinstance(pid, tuple):                   # the overview's type rows stay
                continue
            it.setHidden(bool(text) and text not in it.text(0).lower() and text not in str(it.data(0, SUB)).lower())

    def _row_selected(self):
        self.activate()
        self._update_buttons()

    def _ticked(self, it, col):
        pid = it.data(0, Qt.ItemDataRole.UserRole)
        pr = self._profile(pid)
        if pr is None or col != 0 or self.host.recording:
            return
        if (it.checkState(0) == Qt.CheckState.Checked) != pr["enabled"]:
            QTimer.singleShot(0, lambda: self.host.toggle_log_profile(pid))     # not inside itemChanged

    # ---------------------------------------------------------------- menus
    def _fill_add(self, m: QMenu, every: bool = False):
        m.clear()
        for k, name, icon, what in lt.TYPES:
            if every or (self._kind or self._only) in (None, k):
                a = m.addAction(make_icon(icon, T["text"]), f"{name}...", lambda kk=k: self.host.add_log_profile(kk))
                a.setToolTip(what)
                a.setEnabled(not self.host.recording)

    def _profile_menu(self, pid: str, at):
        pr = self._profile(pid)
        if pr is None:
            return
        rec = self.host.recording
        m = QMenu(self)
        m.addAction("Edit...", lambda: self.host.edit_log_profile(pid)).setEnabled(not rec)
        m.addAction("Disable" if pr["enabled"] else "Enable", lambda: self.host.toggle_log_profile(pid)).setEnabled(not rec)
        if lt.type_of(pr) in ("trigger", "event"):
            m.addAction("Test (fire once now)", lambda: self.host.test_log_profile(pid)).setEnabled(rec)
        m.addAction("Duplicate", lambda: self.host.dup_log_profile(pid)).setEnabled(not rec)
        m.addAction("Open folder", lambda: self.host.open_profile_folder(pid))
        m.addSeparator()
        m.addAction("Delete...", lambda: self.host.delete_log_profile(pid)).setEnabled(not rec)
        m.exec(at)

    def _update_buttons(self):
        pid = self.current()
        pr = self._profile(pid)
        rec = self.host.recording
        self.btn_add.setEnabled(not rec)
        for b in (self.btn_edit, self.btn_dup, self.btn_del, self.btn_on):
            b.setEnabled(pr is not None and not rec)
        self.btn_on.setText("Disable" if pr is None or pr["enabled"] else "Enable")
        self.btn_test.setEnabled(pr is not None and rec and lt.type_of(pr) in ("trigger", "event"))
