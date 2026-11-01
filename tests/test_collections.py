"""Collections (core/tag_collections.py): purposes, the Equipment tree, references by key, moves,
deletes, and Assets / Tags groups of older projects becoming Equipment collections."""
from nxlogger.core import tag_collections as tc
from nxlogger.core.project import Project, report_profile
from nxlogger.core.tags import DeviceCfg, new_tag


def _pumping():
    """Equipment: Pumping stations > Station A > Pump 01 / Pump 02; Station B > Pump 01."""
    xs = []
    ps = tc.new_collection("Pumping stations", "equipment"); xs.append(ps)
    a = tc.new_collection("Station A", "equipment", ps["id"])
    b = tc.new_collection("Station B", "equipment", ps["id"]); xs += [a, b]
    a1, a2, b1 = (tc.new_collection("Pump 01", "equipment", a["id"]),
                  tc.new_collection("Pump 02", "equipment", a["id"]),
                  tc.new_collection("Pump 01", "equipment", b["id"]))
    xs += [a1, a2, b1]
    tc.add_attributes(a1, [("plcA", 0), ("plcA", 1), ("plcA", 2)])
    tc.add_attributes(a2, [("plcA", 3), ("plcA", 4)])
    tc.add_attributes(b1, [("plcB", 0), ("plcB", 1)])
    return xs, ps, a, b, a1, a2, b1


def test_equipment_tree():
    xs, ps, a, b, a1, a2, b1 = _pumping()
    assert tc.path(xs, a1["id"]) == "Pumping stations / Station A / Pump 01"
    assert len(tc.keys(xs, ps["id"])) == 7 and len(tc.keys(xs, a["id"])) == 5
    assert tc.keys(xs, a["id"], deep=False) == []
    assert tc.depth(xs, a1["id"]) == 3 and tc.subtree_height(xs, ps["id"]) == 3
    assert tc.add_attributes(a1, [("plcA", 0)]) == 0                       # once per collection
    assert [c["name"] for c in tc.ordered(xs, "equipment")][:3] == ["Pumping stations", "Station A", "Pump 01"]
    assert "inside itself" in tc.can_move(xs, ps["id"], a1["id"])          # no cycles
    assert tc.can_move(xs, a1["id"], b["id"]) == ""
    deep = tc.new_collection("Motor", "equipment", a1["id"]); xs.append(deep)   # level 4
    assert tc.depth(xs, deep["id"]) == tc.MAX_DEPTH
    assert "levels" in tc.can_move(xs, a["id"], b["id"])                    # would make 5 levels
    left = tc.delete(xs, a["id"])                                          # with its subs, not the tags
    assert {c["name"] for c in left} == {"Pumping stations", "Station B", "Pump 01"}
    assert tc.keys(left, b["id"]) == [("plcB", 0), ("plcB", 1)]


def test_purposes_and_many_to_many():
    xs, ps, a, *_ = _pumping()
    kpi = tc.new_collection("Energy KPIs", "kpi"); xs.append(kpi)
    rep = tc.new_collection("Daily", "report"); xs.append(rep)
    tc.add_attributes(kpi, [("plcA", 0)]); tc.add_attributes(rep, [("plcA", 0), ("plcB", 0)])
    assert "parent" not in kpi or kpi["parent"] is None                    # only Equipment nests
    assert tc.can_move(xs, kpi["id"], ps["id"])                            # refused
    assert set(tc.collections_of(xs, ("plcA", 0))) == {"Equipment: Pumping stations / Station A / Pump 01",
                                                         "KPI: Energy KPIs", "Report: Daily"}
    assert [c["id"] for c in tc.of_purpose(xs, "kpi")] == [kpi["id"]]
    pr = report_profile(rep)
    assert pr["type"] == "statistic" and pr["interval_s"] == tc.REPORT_INTERVAL_S and pr["collections"] == [rep["id"]]


def test_assets_and_groups_become_equipment():
    p = Project(name="Old")
    dev = DeviceCfg("PLC", "modbus_tcp")
    dev.tags = [new_tag(dev, "Flow", "Raw water/Pumps"), new_tag(dev, "Level", "Raw water")]
    dev.groups = ["Raw water", "Empty one"]
    p.devices = [dev]
    p.virtual_tags = [{"id": "v1", "name": "Total", "group": "Raw water", "kind": "formula"}]
    p.groups = ["Plant"]
    n = tc.migrate(p)
    assert n == 3 and not dev.tags[0].group and not dev.groups and not p.groups
    assert all(c["purpose"] == "equipment" for c in p.collections)
    names = {tc.path(p.collections, x["id"]) for x in p.collections}
    assert names == {"Raw water", "Raw water / Pumps", "Empty one", "Plant"}
    raw = next(x for x in p.collections if x["name"] == "Raw water")
    assert tc.keys(p.collections, raw["id"], deep=False) == [(dev.id, dev.tags[1].index), ("vt:v1", 0)]
    assert tc.migrate(p) == 0                                              # idempotent
    q = Project.from_dict(p.to_dict())
    assert [x["name"] for x in q.collections] == [x["name"] for x in p.collections]

    d = Project(name="2.x").to_dict()                                      # a project with Assets
    d["assets"] = [{"id": "a1", "name": "Boiler", "parent": None, "attributes": [{"key": ["d", 0], "name": "T"}]}]
    d["logging_profiles"] = [{**d["logging_profiles"][0], "assets": ["a1"]}] if d["logging_profiles"] else []
    q = Project.from_dict(d)
    assert [(c["id"], c["purpose"], c["attributes"]) for c in q.collections] == \
        [("a1", "equipment", [{"key": ["d", 0], "name": "T"}])]
    assert all("assets" not in pr for pr in q.logging_profiles)
    assert all(pr["collections"] == ["a1"] for pr in q.logging_profiles)
