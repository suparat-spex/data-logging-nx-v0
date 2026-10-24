"""Offscreen: the main window builds and every dialog opens (no broker needed).
The full end-to-end run (broker, alarms, recording, screenshots) is
.claude/skills/nxlogger-dev/scripts/offscreen_test.py."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtCore import QCoreApplication, QSettings
    QCoreApplication.setOrganizationName("SPEXTEST")
    QCoreApplication.setApplicationName("nxlogger-pytest")
    QSettings().clear()
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def win(app):
    from nxlogger.ui.main_window import DaqWindow
    w = DaqWindow()
    yield w
    w.dirty = False
    w.close()


def test_window_and_icons(win):
    from nxlogger.ui.icons import app_icon, icon
    assert "NX Logger" in win.windowTitle()
    assert not icon("bell").isNull() and not app_icon().isNull()


def test_dialogs_open(win):
    from nxlogger.core import alarm_rules as ar
    from nxlogger.core.project import new_log_profile
    from nxlogger.ui.dialogs.about_dialog import show_about
    assert callable(show_about)                               # modal: import check only
    from nxlogger.ui.dialogs.alarm_dialog import AlarmDialog
    from nxlogger.ui.dialogs.alarm_profile_dialog import AlarmProfileDialog
    from nxlogger.ui.dialogs.channel_dialog import ChannelDialog
    from nxlogger.ui.dialogs.log_profile_dialog import LogProfileDialog
    from nxlogger.ui.dialogs.ml_log_dialog import MlLogDialog
    from nxlogger.ui.dialogs.tile_dialog import TileDialog
    from nxlogger.ui.dialogs.topic_dialog import TopicDialog

    t = win.project.topics[0]
    ch = win.channel_choices()
    dialogs = [
        TopicDialog(win, t, set()),
        LogProfileDialog(win, new_log_profile("statistic"), ch, set()),
        AlarmDialog(win, ar.new_alarm(), ch, win.project.alarm_profiles),
        AlarmProfileDialog(win, ar.new_profile("rest")),
        ChannelDialog(win, t.name, t.channels[0], "#e8b100", lambda: None),
        TileDialog(win, {"kind": "plot", "title": "", "channels": []}, ch),
        MlLogDialog(win, win.project.ml_log, "/tmp/AI_Data"),
        win.conn_dlg,
    ]
    for d in dialogs:
        d.show()
        QtWidgets.QApplication.processEvents()
        assert d.isVisible(), type(d).__name__
        d.hide()
