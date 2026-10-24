"""View model (no Qt): free-form views with a snap grid, migration of grid and tabbed views."""
from nxlogger.core import view_model as vm
from nxlogger.core.project import Project


def test_old_view_gets_tile_defaults():
    old = {"id": "v1", "name": "Old", "tiles": [{"id": "t", "kind": "plot", "x": 0, "y": 0, "w": 9, "h": 9,
                                                 "window_s": 5}]}
    v = vm.normalize_view(old)
    t = v["tiles"][0]
    assert t["plot"] == "time" and t["legend"] is True and v["grid"] == 10 and v["layout"] == "free"
    assert (t["x"], t["w"], t["win_n"], t["win_unit"]) == (0, 9, 5.0, "s")


def test_grid_view_becomes_free_form():
    grid = {"id": "g", "name": "Grid", "layout": "grid", "gap": 8, "margin": 10,
            "rows": [{"size": 60, "fixed": True}, {"size": 1.0, "fixed": False}],
            "cols": [{"size": 1.0, "fixed": False}, {"size": 1.0, "fixed": False}],
            "tiles": [{"id": "h", "kind": "text", "row": 0, "col": 0, "rowspan": 1, "colspan": 2},
                      {"id": "a", "kind": "value", "row": 1, "col": 1, "rowspan": 1, "colspan": 1}]}
    v = vm.normalize_view(grid)
    h, a = v["tiles"]
    assert v["layout"] == "free" and "rows" not in v and "row" not in a
    assert (h["x"], h["y"]) == (10, 10) and h["h"] == 60 and h["w"] > a["w"]
    assert a["x"] > h["x"] and a["y"] > h["y"] + 50 and all(x % 10 == 0 for x in (a["x"], a["y"], a["w"]))


def test_new_tile_sizes_and_default_view():
    assert (vm.new_tile("gauge", 20, 30)["w"], vm.new_tile("led")["h"]) == (240, 140)
    v = vm.default_view("Overview", [("d", 0), ("d", 1)])
    assert [t["kind"] for t in v["tiles"]] == ["text", "value", "value", "plot", "stats"]


def test_tabbed_view_splits_into_views():
    tile = lambda: vm.new_tile("gauge")                   # noqa: E731
    d = Project.default().to_dict()
    d["views"] = [{"id": "v1", "name": "Plant", "pages": [{"id": "a", "name": "Main", "tiles": [tile()]},
                                                          {"id": "b", "name": "Pumps", "tiles": [tile(), tile()]}]}]
    p = Project.from_dict(d)
    assert [v["name"] for v in p.views] == ["Plant", "Plant - Pumps"]
    assert [len(v["tiles"]) for v in p.views] == [1, 2]
    assert all("pages" not in v for v in p.views)


def test_axis_defaults_follow_type():
    assert vm.axis_defaults({"plot": "fft", "fft_scale": "rms"}, "g") == ("Frequency (Hz)", "Amplitude, RMS (g)")
    assert vm.axis_defaults({"plot": "time", "x_mode": "samples"}, "V")[0] == "Sample"
    assert vm.tile_title({"kind": "plot", "plot": "hist"}).startswith("Histogram")
    assert vm.tile_title({"kind": "text", "style": "H1"}) == "Header"
