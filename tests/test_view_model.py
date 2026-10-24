"""View model (no Qt): grid views, editing rows / columns, migration of free-form views."""
from nxlogger.core import view_model as vm
from nxlogger.core.project import Project


def test_old_view_becomes_grid():
    old = {"id": "v1", "name": "Old", "tiles": [
        {"id": "h", "kind": "text", "x": 10, "y": 10, "w": 600, "h": 50},
        {"id": "a", "kind": "value", "x": 10, "y": 80, "w": 200, "h": 110},
        {"id": "b", "kind": "gauge", "x": 230, "y": 80, "w": 200, "h": 110, "window_s": 5},
        {"id": "c", "kind": "plot", "x": 10, "y": 210, "w": 600, "h": 300}]}
    v = vm.normalize_view(old)
    pos = {t["id"]: (t["row"], t["col"], t["colspan"]) for t in v["tiles"]}
    assert v["layout"] == "grid" and len(v["rows"]) == 3 and len(v["cols"]) == 2
    assert pos == {"h": (0, 0, 2), "a": (1, 0, 1), "b": (1, 1, 1), "c": (2, 0, 2)}
    assert v["rows"][0]["fixed"] and not v["rows"][2]["fixed"]
    b = next(t for t in v["tiles"] if t["id"] == "b")
    assert (b["win_n"], b["win_unit"]) == (5.0, "s") and "x" not in b


def test_insert_and_delete_lines_move_components():
    v = vm.new_view("G", rows=2, cols=3)
    wide = vm.new_tile("plot", 0, 0, colspan=3, rowspan=1)
    cell = vm.new_tile("value", 1, 1)
    v["tiles"] = [wide, cell]
    vm.insert_line(v, "cols", 1)                     # crossing component grows, right one moves
    assert (wide["col"], wide["colspan"], cell["col"]) == (0, 4, 2)
    assert vm.delete_line(v, "rows", 1) == [cell] and len(v["rows"]) == 1
    vm.delete_line(v, "cols", 0)
    assert wide["colspan"] == 3 and vm.tile_at(v, 0, 2) is wide
    assert vm.free_cell(v) is None                    # full


def test_fits_and_free_cell():
    v = vm.new_view("G", rows=2, cols=2)
    v["tiles"] = [vm.new_tile("value", 0, 0)]
    assert not vm.fits(v, None, 0, 0, 1, 1) and vm.fits(v, None, 0, 1, 2, 1)
    assert vm.free_cell(v, 2, 2) == (0, 1, 1, 2)
    v["cols"].pop(); vm.clamp_tiles(v)
    assert len(v["tiles"]) == 1


def test_tabbed_view_splits_into_views():
    tile = lambda: vm.new_tile("gauge")                   # noqa: E731
    d = Project.default().to_dict()
    d["views"] = [{"id": "v1", "name": "Plant", "pages": [{"id": "a", "name": "Main", "tiles": [tile()]},
                                                          {"id": "b", "name": "Pumps", "tiles": [tile(), tile()]}]}]
    p = Project.from_dict(d)
    assert [v["name"] for v in p.views] == ["Plant", "Plant - Pumps"]
    assert all("pages" not in v for v in p.views)


def test_axis_defaults_follow_type():
    assert vm.axis_defaults({"plot": "fft", "fft_scale": "rms"}, "g") == ("Frequency (Hz)", "Amplitude, RMS (g)")
    assert vm.axis_defaults({"plot": "time", "x_mode": "samples"}, "V")[0] == "Sample"
    assert vm.tile_title({"kind": "plot", "plot": "hist"}).startswith("Histogram")
    assert vm.tile_title({"kind": "text", "style": "H1"}) == "Header"
