"""Assets (core/assets.py): Enterprise > areas > machines > tags, templates and Map tags, KPI
rollups and status, and Collections / 2.x Assets / Tags groups of older projects becoming assets."""
from nxlogger.core import assets as am
from nxlogger.core.project import Project
from nxlogger.core.tags import DeviceCfg, new_tag


def _plant():
    """Utilities > Compressor 1 / Compressor 2; Line 1 (no machines)."""
    xs = []
    ut = am.new_asset("Utilities", "area"); line = am.new_asset("Line 1", "production"); xs += [ut, line]
    c1, c2 = am.new_asset("Compressor 1", "machine", ut["id"]), am.new_asset("Compressor 2", "machine", ut["id"])
    xs += [c1, c2]
    am.add_attributes(c1, [("plc", 0), ("plc", 1)])
    am.add_attributes(c2, [("plc", 2), ("plc", 3)])
    am.add_attributes(ut, [("plc", 9)])                                   # an area's own tag
    return xs, ut, line, c1, c2


def test_tree():
    xs, ut, line, c1, c2 = _plant()
    assert am.path(xs, c1["id"]) == "Utilities / Compressor 1" and am.level(c1) == 3 and am.level(ut) == 2
    assert am.keys(xs, ut["id"]) == [("plc", 9), ("plc", 0), ("plc", 1), ("plc", 2), ("plc", 3)]
    assert am.keys(xs, ut["id"], deep=False) == [("plc", 9)]
    assert [a["name"] for a in am.ordered(xs)] == ["Utilities", "Compressor 1", "Compressor 2", "Line 1"]
    assert am.add_attributes(c1, [("plc", 0)]) == 0                       # once per asset
    assert am.can_move(xs, c1["id"], line["id"]) == "" and am.can_move(xs, ut["id"], line["id"])
    assert am.can_move(xs, c1["id"], c2["id"])                            # a machine goes into an area
    assert am.assets_of(xs, ("plc", 0)) == ["Utilities / Compressor 1"]
    assert {a["name"] for a in am.delete(xs, ut["id"])} == {"Line 1"}      # with its machines, not the tags
    assert am.icon_of(ut) == "grid" and am.icon_of({**c1, "icon": "star"}) == "star"
    assert am.icon_of({**c1, "icon": "no-such"}) == "cpu"                 # only the app's icons
    bad = am.normalize_asset({"name": "X", "parent": "p", "type": "area", "attributes": [{"key": None}]})
    assert bad["type"] == "machine" and bad["attributes"] == []           # a level-3 type, no empty rows


def test_templates_and_map_tags():
    xs, ut, line, c1, c2 = _plant()
    t = am.new_template("Compressor", "machine")
    t["attributes"] = [am.new_slot("Power", "kW", True, "*_kW"), am.new_slot("Pressure", "bar", False, "*_bar|*press*")]
    c3 = am.new_asset("Compressor 3", "station", ut["id"]); xs.append(c3)
    assert am.apply_template(c3, t) == 2 and c3["template"] == t["id"] and c3["type"] == "station"
    assert [(x["name"], x["key"], x["kpi"]) for x in c3["attributes"]] == [("Power", None, True),
                                                                           ("Pressure", None, False)]
    assert am.keys(xs, c3["id"]) == [] and am.template_used(xs, t["id"]) == 1
    cands = [(("plc", 5), "Comp3_kW"), (("plc", 6), "Comp3_pressure"), (("plc", 7), "Comp3_kW2")]
    m = am.map_tags(c3, t, cands)
    assert m == [("Power", ("plc", 5)), ("Pressure", ("plc", 6))]
    assert am.apply_mapping(c3, m) == 2 and am.keys(xs, c3["id"]) == [("plc", 5), ("plc", 6)]
    assert am.map_tags(c3, t, cands) == []                                # nothing left to fill
    assert am.apply_template(c3, t) == 0                                  # again: nothing new
    nt = am.template_from(c1, lambda k: f"T{k[1]}", lambda k: "kW")
    assert [s["name"] for s in nt["attributes"]] == ["T0", "T1"] and nt["attributes"][0]["unit"] == "kW"
    area, temps = am.starter("utilities")
    assert area["type"] == "area" and [x["name"] for x in temps] == ["Compressor", "Chiller"]


def test_kpi_rollup_and_status():
    xs, ut, line, c1, c2 = _plant()
    c1["attributes"][0]["kpi"] = True; c2["attributes"][0]["kpi"] = True
    vals = {("plc", 0): (10.0, "kW"), ("plc", 2): (5.5, "kW")}
    value_of = lambda k: vals.get(k, (None, ""))                         # noqa: E731
    assert am.rollup(xs, ut["id"], value_of) == (15.5, "kW")
    assert am.rollup(xs, None, value_of) == (15.5, "kW") and am.rollup(xs, line["id"], value_of) is None
    assert [tuple(x["key"]) for _a, x in am.kpi_rows(xs)] == [("plc", 0), ("plc", 2)]
    state = {("plc", 0): "ok", ("plc", 3): "alarm"}
    st = lambda k: state.get(k, "none")                                   # noqa: E731
    assert am.status(xs, c1["id"], st) == "ok" and am.status(xs, c2["id"], st) == "alarm"
    assert am.status(xs, ut["id"], st) == "alarm" and am.status(xs, line["id"], st) == "none"


def test_older_projects_become_assets():
    p = Project(name="Old")
    dev = DeviceCfg("PLC", "modbus_tcp")
    dev.tags = [new_tag(dev, "Flow", "Raw water/Pumps"), new_tag(dev, "Level", "Raw water")]
    dev.groups = ["Raw water", "Empty one"]
    p.devices = [dev]
    p.virtual_tags = [{"id": "v1", "name": "Total", "group": "Raw water", "kind": "formula"}]
    p.groups = ["Plant"]
    n = am.migrate(p)
    assert n == 3 and not dev.tags[0].group and not dev.groups and not p.groups
    assert {am.path(p.assets, a["id"]) for a in p.assets} == {"Raw water", "Raw water / Pumps", "Empty one", "Plant"}
    raw = next(a for a in p.assets if a["name"] == "Raw water")
    assert am.keys(p.assets, raw["id"], deep=False) == [(dev.id, dev.tags[1].index), ("vt:v1", 0)]
    assert am.migrate(p) == 0                                             # idempotent
    q = Project.from_dict(p.to_dict())
    assert [a["name"] for a in q.assets] == [a["name"] for a in p.assets]

    d = Project(name="Collections").to_dict()                             # a project with Collections
    d["collections"] = [
        {"id": "r", "name": "Report", "purpose": "report", "parent": None, "attributes": []},   # empty starter
        {"id": "eq", "name": "Equipment", "purpose": "equipment", "parent": None, "attributes": []},
        {"id": "st", "name": "Station A", "purpose": "equipment", "parent": "eq", "attributes": []},
        {"id": "p1", "name": "Pump 01", "purpose": "equipment", "parent": "st",
         "attributes": [{"key": ["d", 0], "name": "Speed"}]},
        {"id": "m1", "name": "Motor", "purpose": "equipment", "parent": "p1", "attributes": [{"key": ["d", 1]}]},
        {"id": "k", "name": "Plant KPIs", "purpose": "kpi", "parent": None, "attributes": [{"key": ["d", 2]}]}]
    d["logging_profiles"] = [{**d["logging_profiles"][0], "collections": ["eq", "k"]}] if d["logging_profiles"] else []
    q = Project.from_dict(d)
    assert [(am.path(q.assets, a["id"]), am.level(a)) for a in am.ordered(q.assets)] == [
        ("Station A", 2), ("Station A / Pump 01", 3), ("Station A / Pump 01 / Motor", 3), ("Plant KPIs", 2)]
    assert am.by_id(q.assets)["p1"]["attributes"] == [{"key": ["d", 0], "name": "Speed", "kpi": False}]
    assert am.by_id(q.assets)["k"]["attributes"][0]["kpi"] is True       # a KPI collection's tags: KPI
    assert all("collections" not in pr for pr in q.logging_profiles)
    assert all(pr["assets"] == ["st", "k"] for pr in q.logging_profiles)   # "Equipment" -> its areas

    d = Project(name="2.x").to_dict()                                     # a project with 2.x Assets
    d["assets"] = [{"id": "a1", "name": "Boiler", "parent": None, "attributes": [{"key": ["d", 0], "name": "T"}]}]
    q = Project.from_dict(d)
    assert [(a["id"], a["type"], a["attributes"]) for a in q.assets] == \
        [("a1", "area", [{"key": ["d", 0], "name": "T", "kpi": False}])]
    r = Project.from_dict(q.to_dict())
    assert r.to_dict() == q.to_dict()                                     # stable once migrated
