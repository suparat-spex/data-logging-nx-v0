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


def test_tree_layout(win):
    from PySide6.QtCore import Qt
    win._rebuild_project_tree()
    root = win.ptree.topLevelItem(0)
    kids = {root.child(i).text(0).split("  ")[0]: root.child(i) for i in range(root.childCount())}
    assert "Quick View" not in kids and "Tags" in kids and "Connectivity" in kids
    conn = kids["Connectivity"]
    assert conn.childCount() == len(win.project.devices)
    assert all(conn.child(i).childCount() == 0 for i in range(conn.childCount()))      # devices only
    dev_node = kids["Tags"].child(0)
    assert dev_node.data(0, Qt.ItemDataRole.UserRole)[0] == "tagdev" and dev_node.childCount() >= 4


def test_editor_tabs(win, monkeypatch):
    from nxlogger.ui.widgets.form_tab import FormTab
    dev = win.project.devices[0]
    n_dev, n_tags = len(win.project.devices), len(dev.tags)
    # devices: a settings pop-up (no editor tab); Cancel changes nothing, Save / Add device applies
    from nxlogger.ui.dialogs.device_dialog import DeviceDialog
    tabs = win.doc_tabs.count()
    answer = {"ok": False, "edit": lambda d: None}

    def fake_exec(dlg):
        assert dlg.ok.text() == ("Add device" if dlg.windowTitle() == "Add device" else "Save")
        answer["edit"](dlg)
        return answer["ok"]
    monkeypatch.setattr(DeviceDialog, "exec", fake_exec)
    answer["edit"] = lambda d: d.name.setText("Sim A")
    win.edit_device(dev.id)
    assert dev.name != "Sim A" and win.doc_tabs.count() == tabs        # cancelled
    answer["ok"] = True
    win.edit_device(dev.id)
    assert dev.name == "Sim A" and win.doc_tabs.count() == tabs
    answer["edit"] = lambda d: d.form.w["host"][1].setText("10.0.0.5")
    win.add_device("modbus_tcp")
    assert len(win.project.devices) == n_dev + 1
    plc = win.project.devices[-1]
    assert plc.driver == "modbus_tcp" and plc.settings["host"] == "10.0.0.5" and win.conn_page.selected() == [plc.id]
    # new group + new tag in it
    g = win.open_editor("group", None, did=dev.id)
    g.dlg.name.setText("Boiler")
    assert g.apply() and "Boiler" in dev.all_groups()
    t = win.open_editor("tag", None, did=dev.id, group="Boiler")
    t.dlg.name.setText("Steam")
    assert t.apply() and len(dev.tags) == n_tags + 1 and dev.tags[-1].group == "Boiler"
    # every other kind opens as a tab (new and existing)
    for kind, ident, ctx in (("alarm", None, {}), ("aprofile", None, {"kind": "rest"}), ("variable", None, {}),
                             ("lprofile", None, {"kind": "statistic"}),
                             ("lprofile", win.project.logging_profiles[0]["id"], {}),
                             ("tag", (dev.id, 0), {})):
        e = win.open_editor(kind, ident, **ctx)
        assert isinstance(e, FormTab) and e.dlg is not None, kind
    # closing a modified editor asks (Apply / Don't apply / Cancel)
    import nxlogger.ui.widgets.form_tab as ft
    e = win.open_editor("tag", (dev.id, 0))
    assert not e.btn_apply.isEnabled() and not e.btn_refresh.isEnabled()      # nothing to apply yet
    e._set_modified(True)
    assert e.btn_apply.isEnabled() and e.btn_refresh.isEnabled()
    monkeypatch.setattr(ft, "ask_unsaved", lambda *a, **k: "cancel")
    assert not win.close_editor(e) and win.doc_tabs.indexOf(e) >= 0
    monkeypatch.setattr(ft, "ask_unsaved", lambda *a, **k: "discard")
    assert win.close_editor(e)
    # saved settings that used to be pop-ups are editor tabs too
    page = next(iter(win.view_pages.values()))
    for what, ident in (("view", page.view["id"]), ("tile", (page.view["id"], page.view["tiles"][1]["id"])),
                        ("mllog", "settings")):
        e = win.open_editor(what, ident)
        assert isinstance(e, FormTab) and e.dlg is not None, what
    win.edit_component(page, page.view["tiles"][1])
    assert win.doc_tabs.currentWidget().key == f"edit:tile:{page.view['id']}:{page.view['tiles'][1]['id']}"
    win._close_editors()
    assert not any(k.startswith("edit:") for k in win.docs)


def test_action_popups(win):
    from nxlogger.ui.dialogs.marker_dialog import MarkerDialog
    from nxlogger.ui.dialogs.new_view_dialog import NewViewDialog
    from nxlogger.ui.dialogs.write_value_dialog import WriteValueDialog
    dev = win.project.devices[0]
    w = WriteValueDialog(win, dev.name, dev.tags[0], 21.5, "HR 0 float32")
    assert w.ok.text() == "Write" and w.result_value() == 21.5
    n = NewViewDialog(win, "Overview", {"Overview"}, [(dev.id, dev.name)])
    assert n.validate() == "Another view already has this name."
    n.name.setText("Plant"); n.start.setCurrentIndex(1)
    assert n.validate() == "" and n.result() == ("Plant", dev.id)
    m = MarkerDialog(win, [("note", "Note"), ("fault", "Fault")], "fault", 0.0)
    assert m.result() == ("fault", "")


def test_connectivity_device_table(win, monkeypatch, tmp_path):
    from PySide6.QtCore import Qt
    from nxlogger.ui.dialogs import import_devices_dialog as idd
    from nxlogger.ui.widgets.connectivity import C_EN
    page = win.conn_page
    win._show_doc("connectivity")
    assert page.table.topLevelItemCount() == len(win.project.devices)
    assert page.table.columnCount() == 8                         # devices only, no tag columns
    dev = win.project.devices[0]
    page.select(dev.id)
    assert page.selected() == [dev.id] and win._prop_key == ("device", dev.id)
    # the Enabled check box is an indicator: it follows the device, a click cannot change it
    row = page._rows[dev.id]
    assert row.checkState(C_EN) == Qt.CheckState.Checked
    assert not row.flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert not hasattr(page, "btn_en") and not hasattr(page, "btn_start")       # not on the toolbar
    win.set_devices_enabled([dev.id], False)                                  # (as the pop-up does)
    assert page._rows[dev.id].checkState(C_EN) == Qt.CheckState.Unchecked
    win.set_devices_enabled([dev.id], True)
    assert dev.enabled and page._rows[dev.id].checkState(C_EN) == Qt.CheckState.Checked
    # duplicate, export JSON, import it back as a copy, delete
    n = len(win.project.devices)
    win.duplicate_device(dev.id)
    copy = win.project.devices[1]
    assert len(win.project.devices) == n + 1 and copy.name == f"{dev.name} copy" and copy.id != dev.id
    out = tmp_path / "dev.json"
    monkeypatch.setattr("nxlogger.ui.main_window.QFileDialog.getSaveFileName", lambda *a, **k: (str(out), ""))
    from nxlogger.ui.dialogs import export_devices_dialog as edd

    def export_exec(dlg):                        # tick only `dev`, JSON
        dlg._tick_all(False)
        assert not dlg.ok.isEnabled()
        next(dlg.tree.topLevelItem(i) for i in range(dlg.tree.topLevelItemCount())
             if dlg.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == dev.id).setCheckState(
            0, Qt.CheckState.Checked)
        return dlg.ok.text() == "Export 1 device..." and dlg.format() == "json"
    monkeypatch.setattr(edd.ExportDevicesDialog, "exec", export_exec)
    win.export_devices_dialog()
    assert out.exists()
    bad = tmp_path / "bad.csv"
    bad.write_text("name,driver,settings\nX,nope,\nY,modbus_tcp,port=abc\n", encoding="utf-8")

    def import_exec(dlg):
        dlg.load(str(bad))                       # invalid: listed problems, Import disabled
        assert not dlg.ok.isEnabled() and "unknown connector" in dlg.check.text() and "port" in dlg.check.text()
        dlg.load(str(out))
        return dlg.ok.isEnabled() and dlg.ok.text() == "Import 1 device"
    monkeypatch.setattr(idd.ImportDevicesDialog, "exec", import_exec)
    win.import_devices()
    assert len(win.project.devices) == n + 2 and win.project.devices[-1].name == f"{dev.name} 2"
    assert page.table.topLevelItemCount() == n + 2
    monkeypatch.setattr("nxlogger.ui.main_window.confirm", lambda *a, **k: True)
    win.delete_devices([copy.id, win.project.devices[-1].id])
    assert len(win.project.devices) == n and win.project.devices[0] is dev


def test_fixed_width_buttons(win):
    from PySide6.QtCore import Qt
    from nxlogger.ui.theme.ui_kit import refit
    btn = win.toolbar.widgetForAction(win.act_connect)
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon); refit(btn)   # wide window
    win.act_connect.setText("Connect"); QtWidgets.QApplication.processEvents()
    w0 = btn.minimumWidth()
    assert w0 > 0 and btn.sizeHint().width() <= w0
    win.act_connect.setText("Disconnect"); QtWidgets.QApplication.processEvents()
    assert btn.minimumWidth() == w0 and btn.sizeHint().width() <= w0     # the longer text fits: no jump
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly); refit(btn)
    assert btn.minimumWidth() == 0                                       # icon only: no reserved text
    win._sync_comm()


def test_device_properties_columns_and_runtime_dock(win, monkeypatch):
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QLabel, QToolButton
    from nxlogger.ui.widgets import connectivity as cn
    dev = win.project.devices[0]
    # Properties: information only, a single Edit... button that opens the settings pop-up
    win.select_object(("device", dev.id))
    assert win.props.target.readonly and [a[0] for a in win.props.target.actions] == ["Edit..."]
    assert all(isinstance(w, QLabel) for w in win.props.grid.editors.values())         # no editors
    opened = []
    monkeypatch.setattr(win, "edit_device", lambda did: opened.append(did))
    btns = [b for b in win.props.findChildren(QToolButton) if b.toolTip() == "Edit" and b.isVisibleTo(win.props)]
    btns[-1].click()
    assert opened == [dev.id]
    # Runtime is its own dock in the right area, next to Properties
    assert win.runtime_dock.widget() is win._runtime
    assert win.dockWidgetArea(win.runtime_dock) == Qt.DockWidgetArea.RightDockWidgetArea
    # Connectivity: hide / show columns, remembered
    page = win.conn_page
    c = cn.COLS.index("Scan")
    page._show_column(c, False)
    assert page.table.isColumnHidden(c) and "Scan" in QSettings().value(cn.HIDDEN_KEY)
    page._fill_columns_menu(page.cols_menu)
    acts = {a.text(): a for a in page.cols_menu.actions()}
    assert "Name" not in acts and not acts["Scan"].isChecked()
    acts["Show all columns"].trigger()
    assert not page.table.isColumnHidden(c) and not QSettings().value(cn.HIDDEN_KEY)


def test_column_menu_clicks_and_right_panel_toggle(win):
    from nxlogger.ui.widgets import connectivity as cn
    page, c = win.conn_page, cn.COLS.index("Address")
    for want_hidden in (True, False):                # click the menu entry like a user does
        page._fill_columns_menu(page.cols_menu)
        next(a for a in page.cols_menu.actions() if a.text() == "Address").trigger()
        QtWidgets.QApplication.processEvents()
        assert page.table.isColumnHidden(c) is want_hidden
    btn = win.toolbar.widgetForAction(win.act_panel)
    for width in (1000, 1700):                       # compact (auto-hidden) and wide windows
        win.resize(width, 900); win._mode = None; win._apply_responsive(); _pump(0.1)
        for _ in range(2):
            btn.click(); _pump(0.05)
            on = win.act_panel.isChecked()
            assert win.right_dock.isHidden() is (not on) and win.runtime_dock.isHidden() is (not on)
    if not win.act_panel.isChecked():
        btn.click()


def test_tab_switch_keeps_selection(win):
    page = win.conn_page
    dev = win.project.devices[0]
    win._show_doc("connectivity"); page.select(dev.id)
    win.select_object(("project",))                       # another tab / the tree took the panel
    page.table.clearSelection()
    win._show_doc("logging"); win._show_doc("connectivity")
    assert page.selected() == [dev.id] and win._prop_key == ("device", dev.id)


def test_property_dock_bar(win):
    from PySide6.QtWidgets import QToolButton
    dev = win.project.devices[0]
    win.select_object(("device", dev.id))
    from PySide6.QtWidgets import QFrame

    def in_order(lay):                           # widgets of the bar as laid out, left to right
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item.widget() is not None:
                yield item.widget()
            elif item.layout() is not None:
                yield from in_order(item.layout())
    bar = win.props.findChild(QFrame, "navBar")
    tips = [w.toolTip() for w in in_order(bar.layout()) if isinstance(w, QToolButton)]
    assert tips == ["Refresh (read the item again)", "Edit", "Expand all", "Collapse"]
    win.props.grid.collapseAll()
    assert not win.props.grid.topLevelItem(0).isExpanded()
    win.props.grid.expandAll()
    assert win.props.grid.topLevelItem(0).isExpanded()


def test_properties_empty_state(win):
    win._show_doc("connectivity")
    page = win.conn_page
    page.table.clearSelection()                               # nothing selected on Connectivity
    p = win.props
    assert p.target is None and p.empty.isVisibleTo(p)
    assert not p.bar.isVisibleTo(p) and not p.head.isVisibleTo(p) and not p.grid.isVisibleTo(p)
    assert p.empty_title.text() == "No device selected" and p.empty_text.text() == "Select a device to view its settings"
    page.select(win.project.devices[0].id)                    # a device: bar, header, grid again
    assert p.bar.isVisibleTo(p) and p.grid.isVisibleTo(p) and not p.empty.isVisibleTo(p)


def test_device_connection_pages(win):
    from nxlogger.core.tags import DRIVERS, defaults
    from nxlogger.ui.dialogs.device_pages import PAGES, connection_page
    assert set(PAGES) == set(DRIVERS)                          # one page per connector
    for k in DRIVERS:                                          # saved values: same keys and types as before
        vals = connection_page(k, {}).values()
        assert vals == defaults(DRIVERS[k]["settings"]), k
    tcp = connection_page("modbus_tcp", {"host": "10.0.0.5"})
    assert tcp.validate(tcp.values()) == "" and "10.0.0.5:502" in tcp.summary(tcp.values())
    tcp.set_value("host", "bad host!")
    assert tcp.validate(tcp.values())
    mq = connection_page("mqtt", {})
    mq.set_value("transport", "TLS/SSL")                       # default port follows the transport
    assert mq.values()["port"] == 8883 and mq.summary(mq.values()).startswith("mqtts://")
    mq.set_value("port", 9999); mq.set_value("transport", "TCP")
    assert mq.values()["port"] == 9999                         # a typed port is kept
    assert not mq._row_of["topic"].isVisibleTo(mq)             # topic only for waveform packets
    ua = connection_page("opcua", {"username": "op", "password": "x"})
    assert not ua.anon.isChecked() and ua.values()["username"] == "op"
    ua.anon.setChecked(True)
    assert ua.values()["username"] == "" and ua.values()["password"] == ""
    ua.set_value("endpoint", "http://x")
    assert ua.validate(ua.values())
    rtu = connection_page("modbus_rtu", {"port": "COM7", "parity": "E"})
    assert rtu.values()["port"] == "COM7" and rtu.values()["parity"] == "E" and "9600 8E1" in rtu.summary(rtu.values())


def test_device_dialog_one_page(win):
    from PySide6.QtWidgets import QTabWidget
    from nxlogger.core.tags import DeviceCfg
    from nxlogger.ui.dialogs.device_dialog import DeviceDialog
    d = DeviceDialog(win, DeviceCfg("PLC", "modbus_tcp"), {"Other"})
    assert d.findChild(QTabWidget) is None                     # no tabs: one page
    assert d.title.text() == "PLC" and "Modbus TCP" in d.subtitle.text()
    assert d.summary.text().startswith("Connects to") and "127.0.0.1:502" in d.summary.text()
    widths = {lb.minimumWidth() for lb in d.form.labels()}
    assert len(widths) == 1                                    # one label column
    assert not hasattr(d, "driver")                            # the connector is fixed (chosen in Add ▾)
    m = DeviceDialog(win, DeviceCfg("Broker", "mqtt"), set())
    assert "transport" in m.form.w and "Connects to  mqtt://" in m.summary.text()
    d.name.setText("Other"); d._ok()
    assert d.err.text() == "Another device already has this name." and d.result() == 0
    n = DeviceDialog(win, DeviceCfg("", "opcua"), set(), new=True)
    n.name.setText("")
    assert n.title.text() == "New OPC UA client device" and n.ok.text() == "Add device"


def test_device_dialog_test_connection(win):
    from nxlogger.core.tags import new_device
    from nxlogger.ui.dialogs.device_dialog import DeviceDialog
    dev = new_device("sim", "Sim test")
    d = DeviceDialog(win, dev, set(), new=True)
    d._test()
    assert not d.btn_test.isEnabled() and d.btn_test.text() == "Testing..."
    end = time.time() + 10
    while d._testing and time.time() < end:
        _pump(0.05)
    assert d.btn_test.isEnabled() and d.test_result.property("level") == "ok"
    assert d.test_result.text().startswith("✓ connected") and not d.err.isVisible()   # not in the footer
    assert d.btn_test.parent() is not d.ok.parent()            # under the form, apart from Cancel / Save
    assert dev.name == "Sim test" and dev.id not in [x.id for x in win.project.devices]   # nothing saved
    d.form.changed.emit()                                      # an edit: the old result goes
    assert d.test_result.text() == "Settings changed - test again."
    assert d.minimumWidth() == 682


def test_device_row_menu(win, monkeypatch):
    page = win.conn_page
    dev = win.project.devices[0]
    page.select(dev.id)
    m = page.build_menu([dev])
    acts = [a for a in m.actions() if not a.isSeparator()]
    assert [a.text() for a in acts] == ["Edit device...", "Disable device", "Duplicate", "Export...", "Delete device"]
    from PySide6.QtCore import Qt
    assert QtWidgets.QApplication.testAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus)   # text-only menus
    acts[1].trigger()                                          # Disable device
    assert not dev.enabled
    assert [a.text() for a in page.build_menu([dev]).actions() if not a.isSeparator()][1] == "Enable device"
    page._toggle_enabled()
    assert dev.enabled
    opened = []
    monkeypatch.setattr(win, "edit_device", lambda did: opened.append(did))
    acts[0].trigger(); _pump(0.05)
    assert opened == [dev.id]
    two = [dev, dev]                                           # several rows: no Edit / Duplicate
    multi = {a.text(): a.isEnabled() for a in page.build_menu(two).actions() if not a.isSeparator()}
    assert multi == {"Edit device...": False, "Disable 2 devices": True, "Duplicate": False,
                     "Export...": True, "Delete 2 devices": True}


def test_menus_text_only_same_indent(win):
    page = win.conn_page
    add = page.btn_add.menu()
    assert all(a.icon().isNull() for a in add.actions())      # menu-only items carry no icon: no empty column
    row = page.build_menu([win.project.devices[0]])
    assert all(a.icon().isNull() for a in row.actions())
    assert "QMenu { icon-size: 0px; }" in QtWidgets.QApplication.instance().styleSheet()   # shared actions too
