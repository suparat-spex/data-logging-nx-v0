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


def test_virtual_tags_popup_page_and_data_path(win, monkeypatch):
    from PySide6.QtCore import Qt
    from nxlogger.core import virtual_tags as vt
    from nxlogger.ui.dialogs import virtual_tag_dialog as vd
    dev = win.project.devices[0]
    a, b = (dev.id, dev.tags[0].index), (dev.id, dev.tags[1].index)
    monkeypatch.setattr(win, "_vt_pick", lambda *_a, **_k: [list(a), list(b)])
    front = win.doc_tabs.currentIndex()                 # put back for the next tests

    def add_exec(dlg):                                   # the pop-up: tabs, live check, then Add tag
        assert [dlg.tabs.tabText(i).split("  ")[0] for i in range(dlg.tabs.count())] == \
            ["General", "Expression", "Calculation", "Display"]
        assert not dlg.ok_btn.isEnabled() and "●" in dlg.tabs.tabText(1)      # no formula yet
        dlg._add_inputs()
        assert [dlg.in_tree.topLevelItem(i).text(0) for i in range(2)] == ["A", "B"]
        dlg.expr.setText("A +")
        assert not dlg.ok_btn.isEnabled() and "Syntax" in dlg.err.text()
        dlg.expr.setText("(A + B) * 2")
        dlg.name.setText("Sum2")
        assert dlg.ok_btn.isEnabled() and dlg.err.text() == "✓ Ready"
        dlg._ok()
        return True
    monkeypatch.setattr(vd.VirtualTagDialog, "exec", add_exec)
    win._vt_popup(None, "formula")
    v = next(x for x in win.project.virtual_tags if x["name"] == "Sum2")
    assert v["inputs"][1] == {"alias": "B", "key": list(b)} and not win.vt_engine.errors

    def edit_exec(dlg):                                  # editing: its own Virtual_<name> is not "taken"
        assert dlg.ok_btn.isEnabled() and dlg.ok_btn.text() == "Save", dlg.err.text()
        assert dlg.color_btn.text() == win.vt_color(v)      # General shows the real colour, like device tags
        dlg._set_color("#123456")
        assert dlg.collect()["color"] == "#123456"
        dlg._set_color(dlg.default_color)                    # Default: saved as "" (palette colour)
        assert dlg.collect()["color"] == ""
        return False
    monkeypatch.setattr(vd.VirtualTagDialog, "exec", edit_exec)
    win._vt_popup(v["id"])

    def const_exec(dlg):
        assert dlg.tabs.count() == 3                     # a constant has no Calculation tab
        dlg.value.setText("0.5"); dlg.name.setText("Half")
        dlg._ok()
        return True
    monkeypatch.setattr(vd.VirtualTagDialog, "exec", const_exec)
    win._vt_popup(None, "constant")
    half = next(x for x in win.project.virtual_tags if x["name"] == "Half")

    def map_exec(dlg):
        dlg.source.setCurrentIndex(dlg.source.findData(list(a)))
        dlg.mode_ranges.setChecked(True)
        dlg.map.setRowCount(0)
        dlg._map_row({"lo": None, "hi": 0, "text": "Low", "color": "warn"})
        dlg._map_row({"lo": 0, "hi": None, "text": "High", "color": "ok"})
        dlg.name.setText("Side")
        dlg._ok()
        return True
    monkeypatch.setattr(vd.VirtualTagDialog, "exec", map_exec)
    win._vt_popup(None, "textmap")
    side = next(x for x in win.project.virtual_tags if x["name"] == "Side")
    assert side["map_mode"] == "ranges" and side["map"][1]["color"] == "ok" and side["dtype"] == "text"

    dev.scan_ms = 50                                     # live data: simulator -> virtual tags
    win._start_all(); _pump(1.2)
    val, qual, good, _t = win.tag_live(*vt.vt_key(v["id"]))
    assert val and qual == "Good" and good is True
    expect = (win.last_value(*a)[0] + win.last_value(*b)[0]) * 2
    assert abs(float(val) - expect) < 1.0                # same scan, give or take a tick
    assert win.tag_live(*vt.vt_key(side["id"]))[0] in ("Low", "High")
    assert win.tag_live(*vt.vt_key(half["id"]))[0] == "0.50"
    keys = {(c[0], c[1]) for c in win.channel_choices()}
    assert vt.vt_key(v["id"]) in keys and vt.vt_key(side["id"]) not in keys    # text: not a stream
    assert win.last_value(*vt.vt_key(v["id"]))[0] is not None                 # it has a stream
    assert "Virtual_Sum2" in win._channel_ids()
    win._stop_all()

    page = win.tags_page                                 # Tags > Virtual: list by type, table, menu
    win._show_doc("tags"); page.refresh(("cat", "virtual"))
    data = [page.dtree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(page.dtree.topLevelItemCount())]
    assert data[:7] == [("all",)] + [("vtype", k) for k in vt.TYPES]
    assert vt.vt_key(v["id"]) in page._rows and page.btn_add.menu() is page.vt_menu
    page.select_tag(*vt.vt_key(v["id"]))
    assert page.selected_virtual() == [v["id"]] and win._prop_key == ("vt", v["id"])
    assert page.btn_edit.isEnabled() and not page.btn_imp.isEnabled()
    page.refresh(("vtype", "constant"))
    assert set(page._rows) == {vt.vt_key(half["id"])}
    win.duplicate_virtual(half["id"])
    assert any(x["name"] == "Half copy" for x in win.project.virtual_tags)
    monkeypatch.setattr("nxlogger.ui.main_window.confirm", lambda *a_, **k_: True)
    win.delete_virtuals([x["id"] for x in win.project.virtual_tags])
    assert win.project.virtual_tags == [] and not any(vt.is_vt(k) for k in win.streams)
    page.refresh(("cat", "device"))
    assert page.btn_add.menu() is page.dev_menu
    win.doc_tabs.setCurrentIndex(front)


def test_aggregation_tags_and_project_groups(win, monkeypatch, tmp_path):
    from PySide6.QtCore import Qt
    from nxlogger.core import aggregation as ag
    from nxlogger.ui.dialogs import aggregation_dialog as ad
    monkeypatch.setattr("nxlogger.ui.main_window.paths.data_dir", lambda: tmp_path)
    monkeypatch.setattr("nxlogger.ui.main_window.confirm", lambda *a_, **k_: True)
    front = win.doc_tabs.currentIndex()
    dev = win.project.devices[0]
    src = (dev.id, dev.tags[0].index)

    def add_exec(dlg):                                   # tabs, then Add tag
        assert [dlg.tabs.tabText(i).split("  ")[0] for i in range(dlg.tabs.count())] == \
            ["General", "Totalizer", "Period", "Display"]
        assert not dlg.ok_btn.isEnabled() and "source" in dlg.err.text()
        dlg.source.setCurrentIndex(dlg.source.findData(list(src)))
        dlg.rate_per.setCurrentIndex(dlg.rate_per.findData("s")); dlg.skip_neg.setChecked(False)
        dlg.reset.setCurrentIndex(dlg.reset.findData("day")); dlg.reset_at.setText("6:7x")
        assert not dlg.ok_btn.isEnabled() and "●" in dlg.tabs.tabText(2)      # bad time: Period tab
        dlg.reset_at.setText("06:00"); dlg.name.setText("Total"); dlg.group.setCurrentText("Plant/Area1")
        assert dlg.ok_btn.isEnabled() and "Next reset" in dlg.next_reset.text()
        dlg._ok()
        return True
    monkeypatch.setattr(ad.AggregationDialog, "exec", add_exec)
    win._agg_popup(None, "totalizer")
    a = next(x for x in win.project.aggregations if x["name"] == "Total")
    assert a["source"] == list(src) and a["reset"] == "day" and not win.agg_engine.errors

    dev.scan_ms = 50
    win._start_all(); _pump(1.0)
    st = win.agg_engine.states[a["id"]]
    assert st.value != 0 and win.tag_live(*ag.agg_key(a["id"]))[1] == "Good"
    keys = {(c[0], c[1]) for c in win.channel_choices()}
    assert ag.agg_key(a["id"]) in keys and ag.agg_key(a["id"], 1) in keys       # value and previous
    assert "Aggregation_Total" in win._channel_ids() and "Aggregation_Total_Previous" in win._channel_ids()
    was = st.value
    win.reset_aggregation(a["id"])                       # Reset now: the value becomes Previous
    assert st.previous == was and abs(st.value) < abs(was) + 1e-9
    win.select_object(("agg", a["id"]))
    assert win._prop_key == ("agg", a["id"])
    win._stop_all()
    win._agg_save(wait=True)                             # retain: written to the data folder ...
    assert (tmp_path / "aggregation_state.json").exists()
    kept = st.value
    win.agg_engine.reset()
    win.agg_engine.restore(win._agg_load())              # ... and back after a restart
    assert win.agg_engine.states[a["id"]].value == pytest.approx(kept)

    page = win.tags_page                                 # Tags > Aggregation
    win._show_doc("tags"); page.refresh(("cat", "aggregation"))
    assert ag.agg_key(a["id"]) in page._rows and page.btn_add.menu() is page.agg_menu
    page.select_tag(*ag.agg_key(a["id"]))
    assert page.selected_agg() == [a["id"]] and page.btn_edit.isEnabled() and not page.btn_imp.isEnabled()
    menu_texts = []

    def grab():                                          # read the open row menu, then close it
        m = QtWidgets.QApplication.activePopupWidget()
        if m is not None:
            menu_texts.extend(x.text() for x in m.actions())
            m.close()
    from PySide6.QtCore import QTimer
    QTimer.singleShot(150, grab)
    page._tag_menu(page.table.visualItemRect(page._rows[ag.agg_key(a["id"])]).center())
    assert "Reset now..." in menu_texts and menu_texts[-1] == "Delete tag..."

    # Groups: one tree for all tags - a device tag and the aggregation tag in the same group
    win.move_to_group([src], "Plant/Area1")
    page.refresh(("cat", "groups"))
    data = [page.dtree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(page.dtree.topLevelItemCount())]
    assert data[0] == ("all",) and ("pgroup", "Plant") in data and data[-1] == ("pnone",)
    page.refresh(("pgroup", "Plant"))                    # a parent shows its sub-groups' tags too
    assert set(page._rows) == {src, ag.agg_key(a["id"])} and win.group_count("Plant") == 2
    assert page.btn_add.menu() is page.grp_menu and page.btn_del.isEnabled()
    monkeypatch.setattr(win, "_recording_guard", lambda: True)
    win._regroup(lambda g: "Site" + g[len("Plant"):] if g == "Plant" or g.startswith("Plant/") else g)  # rename
    assert dev.tags[0].group == "Site/Area1" and a["group"] == "Site/Area1"
    win.delete_project_group("Site/Area1")               # its tags move up, none is deleted
    assert dev.tags[0].group == "Site" and a["group"] == "Site" and dev.tags[0] in dev.tags
    win.move_to_group([src, ag.agg_key(a["id"])], "")
    assert "Site" not in win.project.all_groups()        # a group nobody uses (and not kept empty) is gone
    win.delete_aggregations([a["id"]])
    assert win.project.aggregations == [] and a["id"] not in win.agg_engine.states
    page.refresh(("cat", "device"))
    win.doc_tabs.setCurrentIndex(front)


def test_start_page(win, monkeypatch, tmp_path):
    import json
    from PySide6.QtCore import Qt
    from nxlogger.core import templates as tpl
    page = win.start_page
    assert win.doc_tabs.widget(0) is page and win.docs["start"][1] == "Start Page"   # the first tab
    win._rebuild_project_tree()
    win._nav_open(win.ptree.topLevelItem(0))                       # the project root opens it
    assert win.doc_tabs.currentWidget() is page and win._prop_key == ("project",)
    i = win.doc_tabs.indexOf(page)                                 # closable, and back from the house
    win.doc_tabs.removeTab(i)
    assert win.doc_tabs.indexOf(page) == -1
    win._show_doc("start")
    assert win.doc_tabs.currentWidget() is page
    saved = win.project
    monkeypatch.setattr(win, "_confirm_discard", lambda: True)
    for key, *_ in tpl.TEMPLATES:                                  # every template makes a project
        win.new_from_template(key)
        assert win.project is not saved and win.project_path is None
        if key not in ("blank", "opcua"):
            assert win.project.devices and win.project.views
    assert win.project.name == "Water plant demo" and len(win.project.devices[0].tags) == 49
    f = tmp_path / "demo.nxl"                                      # recent projects: save, then listed
    monkeypatch.setattr("nxlogger.ui.main_window.QFileDialog.getSaveFileName", lambda *a, **k: (str(f), ""))
    assert win._save_project_as()
    assert win.recent_projects()[0] == os.path.abspath(str(f))
    it = page.recent.topLevelItem(0)
    assert it.data(0, Qt.ItemDataRole.UserRole) == os.path.abspath(str(f)) and it.font(0).bold()
    sess = tmp_path / "logs" / "2026-10-29_060000"                 # recent sessions: the log folder
    sess.mkdir(parents=True)
    (sess / "project.json").write_text(json.dumps({"start_unix": 1_790_000_000, "project": {"name": "Demo"}}),
                                       encoding="utf-8")
    (sess / "data.csv").write_bytes(b"x" * 2048)
    win.project.logging.folder = str(tmp_path / "logs")
    assert win.recent_sessions(5)[0][1:] == ("Demo", str(sess), 2048 + len((sess / "project.json").read_bytes()))
    page.refresh()
    assert page.sessions.topLevelItem(0).text(1) == "Demo" and "KB" in page.sessions.topLevelItem(0).text(2)
    assert "devices online" in page.cur_status.text() and page.cur_name.text().startswith("Water plant demo")
    assert win.doc_links() and all(os.path.isfile(p) for _t, p in win.doc_links())   # from the source checkout
    started = []
    monkeypatch.setattr("nxlogger.ui.main_window.subprocess.Popen", lambda args, **k: started.append(args))
    win.run_simulator("nxlogger.sim.water_plant_gui")
    assert started and started[0][-1] == "nxlogger.sim.water_plant_gui"
    steps = [page.step_title.text()]
    for _ in range(3):
        page._go(1); steps.append(page.step_title.text())
    assert steps == ["Connect a device", "Add tags", "Create a view", "Start recording"]
    page._go(1)
    assert page.step_n.text() == "1/4"
    page.show_box.setChecked(False)
    from nxlogger.ui.widgets.start_page import show_at_startup
    assert not show_at_startup()
    from nxlogger.ui.main_window import DaqWindow
    for on in (False, True):                                       # a fresh start: one tab only
        page.show_box.setChecked(on)
        w2 = DaqWindow()
        assert w2.doc_tabs.count() == 1
        assert w2.doc_tabs.widget(0) is (w2.start_page if on else w2.conn_page)
        w2.drivers.stop_all(); w2.dirty = False; w2.close(); w2.deleteLater()
    win.open_recent(str(f))                                        # the open project: no reload
    assert win.project_path == str(f)
    sizes = set()                                                  # Get started: < > never resizes it
    for _ in range(4):
        page._go(1)
        _pump(0.05)
        sizes.add(page.steps_card.size().toTuple())
    assert len(sizes) == 1
    page._fill_views_menu()                                        # Views: the project's views + New view...
    names = [a.text() for a in page.views_menu.actions() if a.text()]
    assert names[:-1] == [v["name"] for v in win.project.views] and names[-1] == "New view..."
    win.select_object(("project",))                                # Properties: read-only + Edit...
    t = win.props.target
    assert t.readonly and t.apply is None and [a[0] for a in t.actions] == ["Edit..."]
    from nxlogger.ui.dialogs import project_dialog as pd

    def proj_exec(dlg):
        assert [dlg.findChild(QtWidgets.QTabWidget).tabText(i) for i in range(2)] == ["General", "Files"]
        dlg.name.setText(""); dlg._ok()
        assert "name" in dlg.err.text()                            # a name is needed
        dlg.name.setText("Plant B"); dlg.operator.setText("Ann")
        return True
    monkeypatch.setattr(pd.ProjectDialog, "exec", proj_exec)
    win._project_popup()
    assert win.project.name == "Plant B" and win.project.operator == "Ann" and win.dirty
    assert win.props.target.values["name"] == "Plant B" and win.p_name.text() == "Plant B"
    win._set_project(saved, None)                                  # back to the shared test project
    win.dirty = False
    vp = next(iter(win.view_pages.values()))                       # and its view in front, as before
    win._show_doc(f"view:{vp.view['id']}")


def test_monitor_mode(win):
    from nxlogger.core import view_model as vmod
    vp = next(iter(win.view_pages.values()))
    extra = vmod.new_view("Energy"); win.project.views.append(extra)
    win._open_view_page(extra, editing=False, show=False)
    win._show_doc(f"view:{vp.view['id']}")
    left_was = win.left_dock.isVisible()
    win.enter_monitor(vp.view["id"])                               # the Monitor button of the view
    bar = win.monitor_bar
    assert win._monitor is not None and bar.isVisible() or not win.isVisible()
    assert not win.menuBar().isVisible() and not win.toolbar.isVisible() and not win.statusBar().isVisible()
    assert win.doc_tabs.tabBar().isHidden() and win.left_dock.isHidden() and win.right_dock.isHidden()
    assert vp.bar.isHidden() and not vp.editing                    # watching, not editing
    assert [bar.views.itemText(i) for i in range(bar.views.count())] == [v["name"] for v in win.project.views]
    assert bar.views.currentData() == vp.view["id"]
    bar.step(1)                                                    # next view (Page Down)
    assert win.doc_tabs.currentWidget() is win.view_pages[extra["id"]] and bar.views.currentText() == "Energy"
    bar.step(-1)
    assert win.doc_tabs.currentWidget() is vp
    bar.cycle.setCurrentIndex(bar.cycle.findData(10))              # auto-cycle
    assert win._mon_cycle.isActive() and win._mon_cycle.interval() == 10_000
    bar.update_live()
    assert "online" in bar.online.text() and bar.clock.text()
    win.exit_monitor("alarms")                                     # the alarm chip: out, on the Alarms page
    assert win._monitor is None and not win._mon_cycle.isActive() and bar.isHidden()
    assert win.menuBar().isVisible() == True or not win.isVisible()   # noqa: E712
    assert not win.doc_tabs.tabBar().isHidden() and not vp.bar.isHidden()
    assert win.left_dock.isVisible() == left_was
    assert win.doc_tabs.currentWidget() is win.docs["alarms"][0]
    bar.cycle.setCurrentIndex(0)
    win.toggle_monitor(); assert win._monitor is not None            # F11 in, F11 out
    win.toggle_monitor(); assert win._monitor is None
    win.project.views.remove(extra)
    win._load_views()
    win._show_doc(f"view:{next(iter(win.view_pages.values())).view['id']}")


def test_properties_panel_targets(win, monkeypatch):
    from nxlogger.ui.dialogs import tile_dialog as td
    from nxlogger.ui.dialogs import view_dialog as vd
    dev = win.project.devices[0]
    for ref in (("device", dev.id), ("tag", dev.id, 0), ("project",)):
        win.select_object(ref)
        assert win.props.target is not None and win.props.grid.topLevelItemCount() > 0
    page = next(iter(win.view_pages.values()))
    win._show_doc(f"view:{page.view['id']}")             # views open as tabs when used
    page.btn_edit.setChecked(True)
    tile = page.view["tiles"][1]
    page.select(("tile", tile))                          # Properties: read-only + Edit...
    t = win.props.target
    assert t.key == ("tile", tile["id"]) and t.readonly and t.apply is None
    assert [a[0] for a in t.actions] == ["Edit...", "Duplicate", "Delete"]

    def tile_exec(dlg):                                  # the component pop-up: Position tab, snapped, inside
        tabs = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
        assert tabs[-1] == "Position"
        dlg.dec.setValue(1); dlg.pos["w"].setValue(333)
        dlg.pos["x"].setValue(1900)                      # does not fit on 1920 px: refused
        dlg._ok()
        assert "Position" in dlg.err.text()
        dlg.pos["x"].setValue(20)
        return True
    monkeypatch.setattr(td.TileDialog, "exec", tile_exec)
    win._tile_popup(page, tile)
    assert tile["decimals"] == 1 and tile["w"] == 330 and page.tile_of(tile).width() == 330   # 10 px grid

    def add_exec(dlg):                                   # Add: the pop-up first
        assert dlg.ok_btn.text() == "Add"
        return True
    monkeypatch.setattr(td.TileDialog, "exec", add_exec)
    n = len(page.view["tiles"])
    win.new_component(page, __import__("nxlogger.core.view_model", fromlist=["x"]).new_tile("led", 10, 10))
    _pump(0.05)
    assert len(page.view["tiles"]) == n + 1 and page.view["tiles"][-1]["kind"] == "led"
    monkeypatch.setattr(td.TileDialog, "exec", lambda dlg: False)        # Cancel: nothing added
    page.add_tile("value"); _pump(0.05)
    assert len(page.view["tiles"]) == n + 1

    page.select(None)                                    # the view: read-only, its pop-up has Size
    vt = win.props.target
    assert vt.key == ("view", page.view["id"]) and vt.readonly and vt.actions[0][0] == "Edit..."
    assert page.canvas.size().toTuple() == (1920, 1080)  # the fixed size, whatever the window

    def view_exec(dlg):
        assert dlg.preset.currentText() == "1920 × 1080"
        dlg.w_px.setValue(320); dlg.h_px.setValue(240)   # smaller than the components
        dlg._ok()
        assert "Size" in dlg.err.text()
        dlg.preset.setCurrentIndex(dlg.preset.findData("1366x768"))
        dlg.align.setCurrentIndex(dlg.align.findData("center"))
        return True
    monkeypatch.setattr(vd.ViewDialog, "exec", view_exec)
    win._view_popup(page)
    assert (page.view["width"], page.view["height"], page.view["align"]) == (1366, 768, "center")
    assert page.canvas.size().toTuple() == (1366, 768)
    page.view.update(width=1920, height=1080, align="left"); page.apply_view()
    page.btn_edit.setChecked(False)
    for a in win._icon_only:                             # Home / New / Open / Save: icons only, always
        assert win.toolbar.widgetForAction(a).toolButtonStyle() == QtCore_ToolButtonIconOnly()


def QtCore_ToolButtonIconOnly():
    from PySide6.QtCore import Qt
    return Qt.ToolButtonStyle.ToolButtonIconOnly


def test_tree_layout(win):
    from PySide6.QtCore import Qt
    win._rebuild_project_tree()
    root = win.ptree.topLevelItem(0)
    kids = {root.child(i).text(0).split("  ")[0]: root.child(i) for i in range(root.childCount())}
    assert "Quick View" not in kids and "Tags" in kids and "Connectivity" in kids
    conn = kids["Connectivity"]                             # three categories, flat (no devices)
    cc = [conn.child(i).data(0, Qt.ItemDataRole.UserRole) for i in range(conn.childCount())]
    assert cc == [("conncat", "protocols"), ("conncat", "database"), ("conncat", "web")]
    assert all(conn.child(i).childCount() == 0 for i in range(3))
    assert conn.child(0).text(0) == f"Protocols  ({len(win.project.devices)})"
    tags = kids["Tags"]                                     # six categories + Groups, flat
    cats = [tags.child(i).data(0, Qt.ItemDataRole.UserRole) for i in range(tags.childCount())]
    assert [c[1] for c in cats] == ["device", "virtual", "action", "aggregation", "memory", "system", "groups"]
    assert all(c[0] == "tagcat" for c in cats) and all(tags.child(i).childCount() == 0 for i in range(7))


def test_system_tags(win):
    from nxlogger.core import system_tags as ss
    win._update_stats()                                     # computes the System Tags
    keys = {k for k in ((c[0], c[1]) for c in win.channel_choices()) if ss.is_sys(k[0])}
    assert ss.sys_key("Hour") in keys and ss.sys_key("DateTime") not in keys   # text is not a stream
    val, unit = win.last_value(*ss.sys_key("Uptime"))
    assert val is not None and unit == "s"
    text, qual, good, _ = win.tag_live(*ss.sys_key("DateTime"))
    assert len(text) == 19 and qual == "Good" and good
    did = win.project.devices[0].id
    assert win.tag_live(f"sys:dev.{did}.Connected", 0)[0] in ("0", "1")
    from PySide6.QtCore import Qt
    page = win.tags_page
    top = lambda: [page.dtree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)       # noqa: E731
                   for i in range(page.dtree.topLevelItemCount())]
    page.refresh(("cat", "system"))                         # only this category: All tags + its groups
    assert top()[:5] == [("all",), ("sysgroup", "Clock"), ("sysgroup", "App"), ("sysgroup", "Recording"),
                         ("sysgroup", "Alarms")] and page.band.text.text() == "System"
    assert len(page._rows) == len(win.sys_tags())
    assert not page.btn_add.isEnabled() and not page.btn_edit.isEnabled() and not page.btn_del.isEnabled()
    page.refresh(("sysgroup", "Clock"))
    assert {r for r in page._rows} == {t.key for t in win.sys_tags() if t.group == "Clock"}
    page.refresh(("sysdev", did))
    assert len(page._rows) == len(ss.DEVICE_FIELDS)
    page.select_tag(*ss.sys_key("Hour"))
    assert page.selected_system() == [ss.sys_key("Hour")] and page.selected_tags() == []
    assert win._prop_key == ("sys", "sys:Hour")
    page.refresh(("cat", "device"))                         # All tags + the Connectivity devices, nothing else
    assert top() == [("all",)] + [("device", d.id) for d in win.project.devices]
    assert len(page._rows) == sum(len(d.tags) for d in win.project.devices) and not page.btn_add.isEnabled()
    page.refresh(("device", did))
    assert page.current_device_id() == did and page.btn_add.isEnabled() and page.btn_imp.isEnabled()
    page.refresh(("cat", "memory"))
    assert not page._rows and not top() and page.band.text.text().startswith("Memory")


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
    # tags and groups: pop-ups like the device (Add group / Add tag / Save; Cancel changes nothing)
    from nxlogger.ui.dialogs.group_dialog import GroupDialog
    from nxlogger.ui.dialogs.tag_dialog import TagDialog
    seen = []

    def popup_exec(dlg):
        seen.append(next(b.text() for b in dlg.findChildren(QtWidgets.QPushButton) if b.property("primary")))
        answer["edit"](dlg)
        return answer["ok"]
    monkeypatch.setattr(GroupDialog, "exec", popup_exec)
    monkeypatch.setattr(TagDialog, "exec", popup_exec)
    answer["edit"] = lambda d: d.name.setText("Boiler")
    win.add_group(dev.id, "")
    assert "Boiler" in dev.all_groups() and seen[-1] == "Add group" and win.doc_tabs.count() == tabs
    answer["edit"] = lambda d: d.name.setText("Steam")
    win.add_tags(dev.id, "Boiler", False)
    assert len(dev.tags) == n_tags + 1 and dev.tags[-1].group == "Boiler" and seen[-1] == "Add tag"
    answer["edit"] = lambda d: d.name.setText("Steam 2")
    answer["ok"] = False
    win.edit_tag(dev.id, dev.tags[-1].index)
    assert dev.tags[-1].name == "Steam" and seen[-1] == "Save"           # cancelled
    answer["ok"] = True
    win.edit_tag(dev.id, dev.tags[-1].index)
    assert dev.tags[-1].name == "Steam 2" and win.doc_tabs.count() == tabs
    # a selected tag's properties are information only (like a device), Edit... opens the pop-up
    win.select_object(("tag", dev.id, 0))
    assert win.props.target.readonly and win.props.target.apply is None
    assert [a[0] for a in win.props.target.actions] == ["Edit..."]
    # every other kind opens as a tab (new and existing)
    for kind, ident, ctx in (("alarm", None, {}), ("aprofile", None, {"kind": "rest"}), ("variable", None, {}),
                             ("lprofile", None, {"kind": "statistic"}),
                             ("lprofile", win.project.logging_profiles[0]["id"], {})):
        e = win.open_editor(kind, ident, **ctx)
        assert isinstance(e, FormTab) and e.dlg is not None, kind
    # closing a modified editor asks (Apply / Don't apply / Cancel)
    import nxlogger.ui.widgets.form_tab as ft
    e = win.open_editor("lprofile", win.project.logging_profiles[0]["id"])
    assert not e.btn_apply.isEnabled() and not e.btn_refresh.isEnabled()      # nothing to apply yet
    e._set_modified(True)
    assert e.btn_apply.isEnabled() and e.btn_refresh.isEnabled()
    monkeypatch.setattr(ft, "ask_unsaved", lambda *a, **k: "cancel")
    assert not win.close_editor(e) and win.doc_tabs.indexOf(e) >= 0
    monkeypatch.setattr(ft, "ask_unsaved", lambda *a, **k: "discard")
    assert win.close_editor(e)
    # the AI/ML log settings are an editor tab; views and components are pop-ups (like devices / tags)
    e = win.open_editor("mllog", "settings")
    assert isinstance(e, FormTab) and e.dlg is not None
    page = next(iter(win.view_pages.values()))
    from nxlogger.ui.dialogs import tile_dialog as td
    from nxlogger.ui.dialogs import view_dialog as vd
    seen = []
    monkeypatch.setattr(td.TileDialog, "exec", lambda dlg: seen.append(dlg.ok_btn.text()) or False)
    monkeypatch.setattr(vd.ViewDialog, "exec", lambda dlg: seen.append(dlg.ok_btn.text()) or False)
    tabs = win.doc_tabs.count()
    win.edit_component(page, page.view["tiles"][1]); win.view_properties(page)
    _pump(0.05)
    assert seen == ["Save", "Save"] and win.doc_tabs.count() == tabs        # pop-ups, no new tab
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


def test_connectivity_categories(win):
    from PySide6.QtCore import Qt
    page = win.conn_page
    win._show_doc("connectivity")
    data = lambda: [page.clist.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)     # noqa: E731
                    for i in range(page.clist.topLevelItemCount())]
    page.show_category("protocols")
    assert data()[0] == ("all",) and ("driver", "opcua") in data() and data()[-1] == ("driver", "opcda")
    assert page.band.text.text() == "Protocols"
    dev = win.project.devices[0]
    page._list_selected(page.clist.topLevelItem(data().index(("driver", dev.driver))))
    QtWidgets.QApplication.processEvents()                  # the refresh is deferred
    assert set(page._rows) == {d.id for d in win.project.devices if d.driver == dev.driver}
    assert page.selected() == []                            # switching clears the selection: Properties empty
    assert page.empty_hint()[0] == "No device selected" and win._empty_hint()[0] == "No device selected"
    page.select(dev.id); page.activate()
    assert page.selected() == [dev.id]
    page.show_category("database")                          # planned connectors only: empty, Add has no live item
    assert page.table.topLevelItemCount() == 0 and ("planned", "MySQL") in data()
    assert page.selected() == [] and win._prop_key is None
    assert page.empty_hint()[0] == "No database selected" and "planned" in page.empty_hint()[1]
    page.activate()                                         # coming back does not pick a row by itself
    page.show_category("web")
    assert page.empty_hint()[0] == "No web service selected"
    page._fill_add_menu()
    assert page.add_menu.actions() and not any(a.isEnabled() for a in page.add_menu.actions())
    page.select(dev.id)                                     # a device elsewhere: its category, All connections
    assert page.category == "protocols" and page.selected() == [dev.id]
    texts = [a.text() for a in page.build_menu([dev]).actions() if a.text()]
    assert texts[:3] == ["Edit device...", "Open tags", "Connect"] and texts[-1] == "Delete device..."
    assert page.build_menu([]).actions() == []               # empty space: no menu


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


def test_device_dialog_styles(win):
    from PySide6.QtWidgets import QTabWidget
    from nxlogger.core.tags import DeviceCfg
    from nxlogger.ui.dialogs.device_dialog import DeviceDialog
    s = DeviceDialog(win, DeviceCfg("PLC", "modbus_tcp"), {"Other"}, style="sections")
    assert s.findChild(QTabWidget) is None                     # the one-page style (kept, see the skill)
    t = DeviceDialog(win, DeviceCfg("Broker", "mqtt"), set())   # default: tabs, one per section
    assert [t.tabs.tabText(i) for i in range(t.tabs.count())] == ["General", "Broker", "Authentication",
                                                                   "Session", "Payload"]
    assert t.btn_test.isVisibleTo(t) and t.tabs.isAncestorOf(t.form.w["host"][1])   # test row under the tabs
    d = DeviceDialog(win, DeviceCfg("PLC", "modbus_tcp"), {"Other"})
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
    assert [a.text() for a in acts] == ["Edit device...", "Open tags", "Connect", "Disable device", "Duplicate",
                                        "Export...", "Delete device..."]
    from PySide6.QtCore import Qt
    assert QtWidgets.QApplication.testAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus)   # text-only menus
    acts[3].trigger()                                          # Disable device
    assert not dev.enabled
    assert [a.text() for a in page.build_menu([dev]).actions() if not a.isSeparator()][3] == "Enable device"
    page._toggle_enabled()
    assert dev.enabled
    opened = []
    monkeypatch.setattr(win, "edit_device", lambda did: opened.append(did))
    acts[0].trigger(); _pump(0.05)
    assert opened == [dev.id]
    two = [dev, dev]                                           # several rows: no Edit / Open tags / Duplicate
    multi = {a.text(): a.isEnabled() for a in page.build_menu(two).actions() if not a.isSeparator()}
    assert multi == {"Edit device...": False, "Open tags": False, "Connect (2)": True, "Disable 2 devices": True,
                     "Duplicate": False, "Export...": True, "Delete 2 devices...": True}


def test_menus_text_only_same_indent(win):
    page = win.conn_page
    add = page.btn_add.menu()
    assert all(a.icon().isNull() for a in add.actions())      # menu-only items carry no icon: no empty column
    row = page.build_menu([win.project.devices[0]])
    assert all(a.icon().isNull() for a in row.actions())
    assert "QMenu { icon-size: 0px; }" in QtWidgets.QApplication.instance().styleSheet()   # shared actions too


def test_tag_import_export_popups(win, monkeypatch, tmp_path):
    from nxlogger.ui.dialogs import export_tags_dialog as etd
    from nxlogger.ui.dialogs import import_tags_dialog as itd
    from nxlogger.ui.widgets.tags_page import COLS
    dev = win.project.devices[0]
    page = win.tags_page
    page.refresh(("device", dev.id))
    assert COLS[1] == "Device" and all(it.text(1) == dev.name for it in page._rows.values())
    assert page.btn_exp.isEnabled() and page.btn_imp.isEnabled()
    page.refresh(("cat", "device"))
    assert page.btn_exp.isEnabled() and not page.btn_imp.isEnabled()      # import needs one device
    out = tmp_path / "tags.csv"
    monkeypatch.setattr("nxlogger.ui.main_window.QFileDialog.getSaveFileName", lambda *a, **k: (str(out), ""))

    def export_exec(dlg):                        # untick all but the first tag, CSV
        dlg._tick_all(False)
        assert not dlg.ok.isEnabled()
        next(dlg._items()).setCheckState(0, Qt.CheckState.Checked)
        return dlg.ok.text() == "Export 1 tag..." and dlg.format() == "csv"
    from PySide6.QtCore import Qt
    monkeypatch.setattr(etd.ExportTagsDialog, "exec", export_exec)
    win.export_tags(dev.id)
    assert out.exists() and dev.tags[0].name in out.read_text(encoding="utf-8-sig")
    n = len(dev.tags)

    def import_exec(dlg):                        # the same tag back: replace keeps its index
        dlg.load(str(out))
        assert dlg.ok.isEnabled() and dlg.ok.text() == "Import 1 tag" and dlg.conflict.isEnabled()
        dlg.conflict.setCurrentIndex(dlg.conflict.findData("replace"))
        return True
    monkeypatch.setattr(itd.ImportTagsDialog, "exec", import_exec)
    idx0 = dev.tags[0].index
    win.import_tags(dev.id)
    assert len(dev.tags) == n and dev.tags[0].index == idx0

    def import_copy(dlg):
        dlg.load(str(out))
        return True                              # default: add as a copy (new name)
    monkeypatch.setattr(itd.ImportTagsDialog, "exec", import_copy)
    win.import_tags(dev.id)
    assert len(dev.tags) == n + 1 and dev.tags[-1].name != dev.tags[0].name
