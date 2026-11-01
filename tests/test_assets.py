"""Assets (core/assets.py): the tree, references by key, moves, deletes, and groups becoming assets."""
from nxlogger.core import assets as asm
from nxlogger.core.project import Project
from nxlogger.core.tags import DeviceCfg, new_tag


def _pumping():
    """Pumping stations > Station A > Pump 01 / Pump 02; Station B > Pump 01 (the user's example)."""
    xs = []
    ps = asm.new_asset("Pumping stations"); xs.append(ps)
    a = asm.new_asset("Station A", ps["id"]); b = asm.new_asset("Station B", ps["id"]); xs += [a, b]
    a1, a2, b1 = (asm.new_asset("Pump 01", a["id"]), asm.new_asset("Pump 02", a["id"]),
                  asm.new_asset("Pump 01", b["id"]))
    xs += [a1, a2, b1]
    asm.add_attributes(a1, [("plcA", 0), ("plcA", 1), ("plcA", 2)])
    asm.add_attributes(a2, [("plcA", 3), ("plcA", 4)])
    asm.add_attributes(b1, [("plcB", 0), ("plcB", 1)])
    return xs, ps, a, b, a1, a2, b1


def test_tree_paths_keys_and_references():
    xs, ps, a, b, a1, a2, b1 = _pumping()
    assert asm.path(xs, a1["id"]) == "Pumping stations / Station A / Pump 01"
    assert len(asm.keys(xs, ps["id"])) == 7 and len(asm.keys(xs, a["id"])) == 5
    assert asm.keys(xs, a["id"], deep=False) == []
    assert asm.depth(xs, a1["id"]) == 3 and asm.subtree_height(xs, ps["id"]) == 3
    assert asm.add_attributes(a1, [("plcA", 0)]) == 0                     # once per asset
    asm.add_attributes(asm.new_asset("Maintenance"), [("plcA", 0)])
    kpi = asm.new_asset("Energy KPIs"); xs.append(kpi)
    asm.add_attributes(kpi, [("plcA", 0)])                                # a tag in several assets
    assert set(asm.assets_of(xs, ("plcA", 0))) == {"Pumping stations / Station A / Pump 01", "Energy KPIs"}
    rows = asm.rows(xs, a["id"])
    assert {(r[0], tuple(r[1]["key"])) for r in rows} >= {(a1["id"], ("plcA", 0)), (a2["id"], ("plcA", 3))}


def test_moves_depth_and_delete():
    xs, ps, a, b, a1, a2, b1 = _pumping()
    assert "inside itself" in asm.can_move(xs, ps["id"], a1["id"])      # no cycles
    assert asm.can_move(xs, a1["id"], b["id"]) == ""
    deep = asm.new_asset("Motor", a1["id"]); xs.append(deep)               # level 4
    assert asm.depth(xs, deep["id"]) == asm.MAX_DEPTH
    assert "levels" in asm.can_move(xs, a["id"], b["id"])                  # would make 5 levels
    left = asm.delete(xs, a["id"])                                         # with its sub-assets, not the tags
    assert {x["name"] for x in left} == {"Pumping stations", "Station B", "Pump 01"}
    assert asm.keys(left, b["id"]) == [("plcB", 0), ("plcB", 1)]


def test_groups_become_assets():
    p = Project(name="Old")
    dev = DeviceCfg("PLC", "modbus_tcp")
    dev.tags = [new_tag(dev, "Flow", "Raw water/Pumps"), new_tag(dev, "Level", "Raw water")]
    dev.groups = ["Raw water", "Empty one"]
    p.devices = [dev]
    p.virtual_tags = [{"id": "v1", "name": "Total", "group": "Raw water", "kind": "formula"}]
    p.groups = ["Plant"]
    n = asm.migrate_groups(p)
    assert n == 3 and not dev.tags[0].group and not dev.groups and not p.groups
    names = {asm.path(p.assets, x["id"]) for x in p.assets}
    assert names == {"Raw water", "Raw water / Pumps", "Empty one", "Plant"}
    raw = next(x for x in p.assets if x["name"] == "Raw water")
    assert asm.keys(p.assets, raw["id"], deep=False) == [(dev.id, dev.tags[1].index), ("vt:v1", 0)]
    assert asm.migrate_groups(p) == 0                                      # idempotent
    q = Project.from_dict(p.to_dict())
    assert [x["name"] for x in q.assets] == [x["name"] for x in p.assets]
