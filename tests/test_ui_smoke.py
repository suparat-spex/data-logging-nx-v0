"""Offscreen: the main window builds, every dialog opens, a simulated device delivers data
into tags, views and statistics (no broker or hardware needed). The full end-to-end run
(broker, alarms, recording, screenshots) is .claude/skills/nxlogger-dev/scripts/offscreen_test.py."""
import os
import time

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
    w.drivers.stop_all()
    w.dirty = False
    w.close()


def _pump(seconds: float):
    end = time.time() + seconds
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)


def test_window_and_icons(win):
    from nxlogger.ui.icons import app_icon, icon
    assert "NX Logger" in win.windowTitle()
    assert not icon("bell").isNull() and not app_icon().isNull() and not icon("server").isNull()
    assert win.project.devices and win.project.views


def test_dialogs_open(win):
    from nxlogger.core import alarm_rules as ar
    from nxlogger.core.project import new_log_profile
    from nxlogger.core.tags import DeviceCfg
    from nxlogger.ui.dialogs.about_dialog import show_about
    assert callable(show_about)                               # modal: import check only
    from nxlogger.ui.dialogs.add_tags_dialog import AddTagsDialog
    from nxlogger.ui.dialogs.alarm_dialog import AlarmDialog
    from nxlogger.ui.dialogs.alarm_profile_dialog import AlarmProfileDialog
    from nxlogger.ui.dialogs.device_dialog import DeviceDialog
    from nxlogger.ui.dialogs.log_profile_dialog import LogProfileDialog
    from nxlogger.ui.dialogs.ml_log_dialog import MlLogDialog
    from nxlogger.ui.dialogs.pick_dialog import PickDialog
    from nxlogger.ui.dialogs.tag_dialog import TagDialog
    from nxlogger.ui.dialogs.tile_dialog import TileDialog

    dev = win.project.devices[0]
    ch = win.channel_choices()
    dialogs = [
        DeviceDialog(win, DeviceCfg("x", "modbus_tcp"), set(), new=True),
        DeviceDialog(win, DeviceCfg("m", "mqtt", settings={"mode": "wavepacket"}), set()),
        TagDialog(win, dev, dev.tags[0], "#e8b100", lambda: None),
        AddTagsDialog(win, DeviceCfg("plc", "modbus_rtu")),
        PickDialog(win, ch, [], True),
        LogProfileDialog(win, new_log_profile("statistic"), ch, set()),
        AlarmDialog(win, ar.new_alarm(), ch, win.project.alarm_profiles),
        AlarmProfileDialog(win, ar.new_profile("rest")),
        MlLogDialog(win, win.project.ml_log, "/tmp/AI_Data"),
    ]
    for kind in ("plot", "value", "gauge", "led", "stats", "tags", "alarms", "text"):
        dialogs.append(TileDialog(win, {"kind": kind, "title": "", "channels": [], "channel": None}, ch))
    for d in dialogs:
        d.show()
        QtWidgets.QApplication.processEvents()
        assert d.isVisible(), type(d).__name__
        d.hide()


def test_simulator_feeds_tags_views_and_stats(win):
    from nxlogger.core import stat_buffer as sb
    dev = win.project.devices[0]
    dev.scan_ms = 50
    win._start_all()
    _pump(1.5)
    assert win.device_state(dev.id)[0] == "online"
    val, qual, good, stamp = win.tag_live(dev.id, 0)
    assert val and qual == "Good" and good is True
    page = next(iter(win.view_pages.values()))
    win._show_doc(f"view:{page.view['id']}")
    stats = next(t for t in page.view["tiles"] if t["kind"] == "stats")
    win._stat_require()
    assert ((dev.id, 0), sb.spec_of(stats)) in win.stats.buffers
    _pump(0.6)
    vals = win.stat_values(dev.id, 0, stats, ["mean", "max"])
    assert vals["Mean"] is not None and vals["Value"] is not None
    win._stop_all()
    assert not win.drivers.any_running


def test_properties_panel_targets(win):
    dev = win.project.devices[0]
    for ref in (("device", dev.id), ("tag", dev.id, 0), ("project",)):
        win.select_object(ref)
        assert win.props.target is not None and win.props.grid.topLevelItemCount() > 0
    page = next(iter(win.view_pages.values()))
    page.btn_edit.setChecked(True)
    tile = page.view["tiles"][1]
    page.select(("tile", tile))
    assert win.props.target.key == ("tile", tile["id"])
    win.props.target.apply("decimals", 1)
    assert tile["decimals"] == 1
    win.props.target.apply("w", 333)                     # snaps to the 10 px grid
    tile_w = page.tile_of(tile).width()
    assert tile["w"] == 330 and tile_w == 330
    page.add_tile("led")
    assert page.view["tiles"][-1]["kind"] == "led" and win.props.target.key[0] == "tile"
    page.select(None)
    assert win.props.target.key == ("view", page.view["id"])
    page.btn_edit.setChecked(False)
