"""View model (no Qt): one canvas per view, tile defaults, migration of tabbed views."""
from nxlogger.core import view_model as vm
from nxlogger.core.project import Project


def test_old_view_gets_tile_defaults():
    old = {"id": "v1", "name": "Old", "tiles": [{"id": "t", "kind": "plot", "x": 0, "y": 0, "w": 9, "h": 9}]}
    v = vm.normalize_view(old)
    t = v["tiles"][0]
    assert t["plot"] == "time" and t["x_mode"] == "time" and t["legend"] is True and v["grid"] == 10


def test_tabbed_view_splits_into_views():
    tile = lambda: vm.new_tile("gauge", 10, 10)                   # noqa: E731
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
