"""App-level styling on top of ui_kit: extra QSS, dense mode, Fusion control style."""
from __future__ import annotations

import re

from PySide6.QtGui import QColor

from nxlogger.ui.theme import ui_kit as ui


_FUSION_DROP = re.compile(r"^\s*(QLineEdit|QComboBox|QPlainTextEdit|QPushButton(?!\[primary)|QSlider|"
                          r"QScrollBar|QTableView|QHeaderView|QProgressBar|QDoubleSpinBox|QSpinBox|"
                          r"QTreeWidget \{)")
# check boxes keep the flat indicator in both styles: Fusion's outline is near-invisible in dark


def fusion_qss(qss: str) -> str:
    return "\n".join(line for line in qss.splitlines() if not _FUSION_DROP.match(line))


def fusion_palette(t: dict):
    """Theme palette with the extra roles Fusion uses for bevels and outlines."""
    from PySide6.QtGui import QPalette
    p = ui.build_palette(t)
    dark = t.get("mode") == "dark"
    btn = QColor(t["hover"] if dark else t["panel"])
    R = QPalette.ColorRole
    for role, c in ((R.Button, btn), (R.Light, btn.lighter(135 if dark else 110)),
                    (R.Midlight, btn.lighter(115 if dark else 105)), (R.Mid, btn.darker(130)),
                    (R.Dark, btn.darker(170 if dark else 150)), (R.Shadow, QColor("#000000")),
                    (R.Link, QColor(t["accent"]))):
        p.setColor(role, c)
    return p


def blend(c1: str, c2: str, a: float) -> str:
    """a*c1 + (1-a)*c2 for hex colours (derived token, e.g. a soft selection)."""
    x, y = QColor(c1), QColor(c2)
    return QColor(int(x.red() * a + y.red() * (1 - a)), int(x.green() * a + y.green() * (1 - a)),
                  int(x.blue() * a + y.blue() * (1 - a))).name()

EXTRA_QSS = """
QGroupBox { border: none; margin: 0; padding: 0; }
QLabel#led { border-radius: 6px; background: @muted@; }
QLabel#led[level="ok"] { background: @ok@; }
QLabel#led[level="warn"] { background: @warn@; }
QLabel#led[level="bad"] { background: @bad@; }
QDoubleSpinBox, QSpinBox { background: @input@; border: 1px solid @border@; border-radius: 4px; padding: 4px 6px; }
QDoubleSpinBox:focus, QSpinBox:focus { border: 1px solid @accent@; }
QLabel#recLabel { color: @muted@; font-weight: 600; padding: 0 8px; }
QLabel#recLabel[level="rec"] { color: @bad@; }
QTreeWidget { background: @input@; alternate-background-color: @panel2@; border: 1px solid @border@; }
QAbstractItemView { outline: 0; }
QTreeWidget::item { padding: 3px 0; }
QTreeWidget::item:selected { background: @sel@; color: @text@; }
QFrame#docBar { background: @panel@; border-bottom: 1px solid @border@; }
QTreeWidget#navTree { background: @panel2@; border: none; }
QTreeWidget#navTree::item { padding: 4px 0; }
QFrame#navBar { background: transparent; border-bottom: 1px solid @border@; }
QFrame#navBar QToolButton { padding: 4px; }
QFrame#docBar QLabel { background: transparent; }
QMainWindow::separator { background: @hover@; width: 4px; height: 4px; border: none; }
QTabWidget#docTabs::pane { border: 1px solid @border@; top: -1px; background: @bg@; }
QTabWidget#docTabs QTabBar::tab:selected { border-bottom: 2px solid @accent@; }
QTabWidget#docTabs QTabBar::tab { padding: 4px 6px 4px 12px; margin-top: 0; }
QToolButton#tabClose { border: none; border-radius: 3px; padding: 0; margin: 0 0 0 4px; color: @muted@; font-size: 13px; }
QToolButton#tabClose:hover { background: @hover@; color: @text@; }
QTreeView::indicator, QListView::indicator, QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid @muted@; border-radius: 3px; background: @input@; }
QTreeView::indicator:checked, QListView::indicator:checked, QCheckBox::indicator:checked { background: @accent@; border: 1px solid @accent@; image: url(@check@); }
QTreeView::indicator:disabled, QCheckBox::indicator:disabled { border: 1px solid @border@; }
QPushButton[primary="true"] { padding: 6px 14px; border-radius: 4px; }
QLabel#brand { font-size: 15px; font-weight: 600; padding: 0 12px 0 8px; }
QToolBar#mainToolbar { padding: 2px 6px; spacing: 2px; }               /* top nav: outer padding, gap */
QToolBar#mainToolbar QToolButton { padding: 3px 6px; }                /* top nav: button padding */
QLabel#chip { qproperty-alignment: AlignCenter; }
QWidget#viewCanvas { background: @bg@; }
QFrame#viewTile { background: transparent; border: none; }   /* card painted by Tile (user colours) */
QFrame#tileHeader { background: @panel2@; border: 1px solid @border@; border-radius: 4px; }
QFrame#tileHeader QLabel { background: transparent; }
QLabel#viewH1 { font-size: 26px; font-weight: 600; background: transparent; }
QLabel#viewH2 { font-size: 20px; font-weight: 600; background: transparent; }
QLabel#viewH3 { font-size: 16px; font-weight: 600; background: transparent; }
QLabel#viewBody { font-size: 13px; background: transparent; }
QLabel#viewValue { font-size: 30px; font-weight: 600; background: transparent; }
QFrame#subBar { background: @panel2@; border-bottom: 1px solid @border@; }
QFrame#subBar QLabel { background: transparent; }
QTabWidget#subTabs::pane { border: none; }
QToolButton#alarmStatus { border: none; padding: 0 8px; color: @muted@; }
QToolButton#alarmStatus[level="bad"] { color: @bad@; font-weight: 600; }
QToolButton#alarmStatus[level="warn"] { color: @warn@; font-weight: 600; }
QFrame#viewTile QTableWidget { border: none; }
QFrame#gridCell { background: transparent; border: none; }   /* dashed outline painted by _Cell */
QToolButton#gridHead { background: @panel2@; border: 1px solid @border@; border-radius: 3px; color: @muted@; font-size: 11px; padding: 0; }
QToolButton#gridHead:hover { color: @text@; border: 1px solid @muted@; }
QToolButton#gridHead[selected="true"] { color: @accent_text@; background: @accent@; border: 1px solid @accent@; }
QTreeWidget#propGrid { background: @panel@; alternate-background-color: @panel@; border: none; }
QTreeWidget#propGrid::item { padding: 1px 0; }
QTreeWidget#propGrid QLineEdit, QTreeWidget#propGrid QComboBox, QTreeWidget#propGrid QSpinBox,
QTreeWidget#propGrid QDoubleSpinBox, QTreeWidget#propGrid QPlainTextEdit { padding: 1px 4px; border-radius: 3px;
  border: 1px solid @border@; background: @input@; }
QLabel#propTitle { font-size: 14px; font-weight: 600; background: transparent; }
QFrame#propHead { background: @panel2@; border-bottom: 1px solid @border@; }
QFrame#propHead QLabel { background: transparent; }
QLabel#quality[level="bad"] { color: @bad@; }
QLabel#quality[level="ok"] { color: @ok@; }
"""

# Small screens: same tokens, tighter spacing (appended after EXTRA_QSS so it wins)
DENSE_QSS = """
QToolBar { spacing: 2px; padding: 2px; }
QToolButton { padding: 3px 6px; }
QTabBar::tab { padding: 6px 10px; }
QMenuBar::item { padding: 4px 8px; }
QDockWidget::title { padding: 5px 8px; }
QLineEdit, QComboBox, QPlainTextEdit { padding: 3px 6px; }
QPushButton { padding: 4px 10px; }
QTreeWidget::item { padding: 1px 0; }
QTreeWidget#navTree::item { padding: 2px 0; }
QHeaderView::section { padding: 3px 6px; }
"""
