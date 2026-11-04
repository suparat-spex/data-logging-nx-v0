"""
assets.py - Assets (Project tree > Assets): the plant in three levels. No Qt.

  Enterprise                       the project (`project.enterprise`, "" = the project's name)
    Area / Zone / Production       level 2 (LEVEL2): parent None
      Machine / Station / Unit     level 3 (LEVEL3): parent = an area, `template` = a template id
        tags                       attributes

Types are labels and icons only. An asset references tags of any category (device, virtual,
aggregation, system) by key - a tag can be in several assets, assets never own tags. An area can
hold tags of its own too (a meter of the area, plant KPIs).

  asset     {"id", "name", "type", "parent": area id | None, "icon": "" (= the type's),
             "description", "template": "" | template id,
             "attributes": [{"key": [tid, idx] | None, "name": "", "kpi": False}]}
            key None = a template slot not mapped to a tag yet; name "" = the tag's own name
  template  {"id", "name", "type", "icon", "description",
             "attributes": [{"name", "unit", "kpi", "match"}]}   match: tag name pattern ("*_kW")

KPI attributes are the Start Page's Key values and the Monitor bar's chips. The rollup of an area
(or of the Enterprise) is the sum of each machine's first KPI value below it (same unit as the
first one); its status: alarm / ok / none (no data) from the tags below it.

The project keeps flat lists (project.assets, project.asset_templates). Collections, the Assets
of 2.x and Tags groups of older files become areas and machines (migrate).
"""
from __future__ import annotations

import fnmatch
import uuid

LEVEL2 = [("area", "Area", "grid"), ("zone", "Zone", "layout"), ("production", "Production", "factory")]
LEVEL3 = [("machine", "Machine", "cpu"), ("station", "Station", "server"), ("unit", "Unit", "boxes")]
TYPE = {t[0]: t for t in LEVEL2 + LEVEL3}
ICONS = [      # the icons an asset can have (resources/icons/ui, Lucide)
    "grid", "layout", "factory", "house", "boxes", "cpu", "server", "gauge", "activity", "power",
    "plug", "waves", "audio-waveform", "sliders", "database", "network", "globe", "radio", "usb", "ethernet-port",
    "chart", "chart-column", "sigma", "hash", "star", "flag", "bell", "tag", "folder", "circle-dot"]
STATUS = ("alarm", "ok", "none")
STARTERS = [   # Use a starter ▾: an area and its templates (no tags) - key, area name, type, templates
    ("utilities", "Utilities", "area", [
        ("Compressor", "machine", [("Power", "kW", True, "*kW*"), ("Pressure", "bar", False, "*bar*"),
                                   ("Running", "", False, "*run*")]),
        ("Chiller", "unit", [("Power", "kW", True, "*kW*"), ("Supply temperature", "°C", False, "*temp*")])]),
    ("production", "Production line", "production", [
        ("Machine", "machine", [("Power", "kW", True, "*kW*"), ("Speed", "", False, "*speed*"),
                                ("Status", "", False, "*status*"), ("Count", "", False, "*count*")])]),
    ("building", "Building", "zone", [
        ("Power meter", "unit", [("Power", "kW", True, "*kW*"), ("Energy", "kWh", False, "*kWh*"),
                                 ("Power factor", "", False, "*PF*")])]),
]


def _id() -> str:
    return uuid.uuid4().hex[:8]


def new_asset(name: str = "", typ: str = "area", parent: str | None = None) -> dict:
    return {"id": _id(), "name": name or TYPE.get(typ, LEVEL2[0])[1], "type": typ, "parent": parent, "icon": "",
            "description": "", "template": "", "attributes": []}


def new_template(name: str = "", typ: str = "machine") -> dict:
    return {"id": _id(), "name": name or "Template", "type": typ, "icon": "", "description": "", "attributes": []}


def new_slot(name: str, unit: str = "", kpi: bool = False, match: str = "") -> dict:
    """A template attribute."""
    return {"name": name, "unit": unit, "kpi": bool(kpi), "match": match or f"*{name}*"}


def level(a: dict) -> int:
    return 2 if a.get("parent") is None else 3


def types_of(lvl: int) -> list[tuple]:
    return LEVEL2 if lvl == 2 else LEVEL3


def type_name(a: dict) -> str:
    return TYPE.get(a.get("type"), ("", "Asset", ""))[1]


def icon_of(a: dict, _assets=None) -> str:
    """The asset's icon: its own, else its type's (`_assets`: the same call as tag pickers use)."""
    if a.get("icon") in ICONS:
        return a["icon"]
    return TYPE.get(a.get("type"), LEVEL2[0])[2]


def normalize_attribute(x: dict) -> dict | None:
    if not isinstance(x, dict) or "key" not in x:
        return None
    key = list(x["key"]) if x.get("key") else None
    if key is None and not x.get("name"):
        return None
    return {"key": key, "name": str(x.get("name", "")), "kpi": bool(x.get("kpi", False))}


def normalize_asset(a: dict) -> dict:
    for k, d in new_asset().items():
        if k != "id":
            a.setdefault(k, d)
    a.setdefault("id", _id())
    lvl = level(a)
    if a["type"] not in {t[0] for t in types_of(lvl)}:
        a["type"] = types_of(lvl)[0][0]
    if a["icon"] and a["icon"] not in ICONS:
        a["icon"] = ""
    a["attributes"] = [y for y in (normalize_attribute(x) for x in a.get("attributes", [])) if y]
    return a


def normalize_template(t: dict) -> dict:
    for k, d in new_template().items():
        if k != "id":
            t.setdefault(k, d)
    t.setdefault("id", _id())
    if t["type"] not in {x[0] for x in LEVEL3}:
        t["type"] = LEVEL3[0][0]
    if t["icon"] and t["icon"] not in ICONS:
        t["icon"] = ""
    t["attributes"] = [new_slot(str(x.get("name", "")), str(x.get("unit", "")), x.get("kpi", False),
                                str(x.get("match", ""))) for x in t.get("attributes", [])
                       if isinstance(x, dict) and x.get("name")]
    return t


# ---------------------------------------------------------------- the tree
def by_id(assets: list[dict]) -> dict[str, dict]:
    return {a["id"]: a for a in assets}


def children(assets: list[dict], parent: str | None) -> list[dict]:
    return [a for a in assets if a.get("parent") == parent]


def areas(assets: list[dict]) -> list[dict]:
    return children(assets, None)


def ordered(assets: list[dict]) -> list[dict]:
    """Every asset in list order: each area followed by its machines."""
    return [x for a in areas(assets) for x in [a] + children(assets, a["id"])]


def path(assets: list[dict], aid: str | None, sep: str = " / ") -> str:
    idx = by_id(assets)
    a = idx.get(aid)
    if a is None:
        return ""
    p = idx.get(a.get("parent"))
    return f"{p['name']}{sep}{a['name']}" if p else a["name"]


def label(assets: list[dict], aid: str) -> str:
    """'Utilities', 'Utilities / Compressor 1' - for tables and menus."""
    return path(assets, aid) if aid in by_id(assets) else "(deleted asset)"


def nodes(assets: list[dict], aid: str | None, deep: bool = True) -> list[dict]:
    """The asset and (deep) its machines; None = every asset."""
    if aid is None:
        return ordered(assets)
    a = by_id(assets).get(aid)
    if a is None:
        return []
    return [a] + (children(assets, aid) if deep else [])


def rows(assets: list[dict], aid: str | None = None, deep: bool = True) -> list[tuple]:
    """[(asset id, attribute)] of an asset (and its machines; None = every asset), mapped or not."""
    return [(a["id"], x) for a in nodes(assets, aid, deep) for x in a["attributes"]]


def keys(assets: list[dict], aid: str | None = None, deep: bool = True) -> list[tuple]:
    """The tag keys of an asset (and of its machines), each once, in order."""
    out, seen = [], set()
    for _aid, x in rows(assets, aid, deep):
        if x["key"]:
            k = tuple(x["key"])
            if k not in seen:
                seen.add(k); out.append(k)
    return out


def machines(assets: list[dict], aid: str | None) -> list[dict]:
    """The machines below the Enterprise (None) or an area; a machine: itself."""
    if aid is None:
        return [a for a in assets if a.get("parent") is not None]
    a = by_id(assets).get(aid)
    if a is None:
        return []
    return [a] if a.get("parent") is not None else children(assets, aid)


def can_move(assets: list[dict], aid: str, parent: str | None) -> str:
    """'' if the machine `aid` may go into the area `parent`, else why not."""
    idx = by_id(assets)
    a = idx.get(aid)
    if a is None:
        return "That asset does not exist."
    if a.get("parent") is None:
        return "An area stays on the top level."
    p = idx.get(parent)
    if p is None or p.get("parent") is not None:
        return "A machine goes into an area."
    return ""


def delete(assets: list[dict], aid: str) -> list[dict]:
    """The list without `aid` and its machines (the tags stay)."""
    return [a for a in assets if a["id"] != aid and a.get("parent") != aid]


def assets_of(assets: list[dict], key) -> list[str]:
    """Labels of the assets a tag is in (for the Tags table's Assets column)."""
    k = list(key)
    return [label(assets, a["id"]) for a in assets if any(x["key"] == k for x in a["attributes"])]


def add_attributes(a: dict, keys_, kpi: bool = False) -> int:
    """Add tag keys that are not there yet (once per asset) -> how many were added."""
    have = {tuple(x["key"]) for x in a["attributes"] if x["key"]}
    n = 0
    for k in keys_:
        if tuple(k) not in have:
            a["attributes"].append({"key": list(k), "name": "", "kpi": kpi})
            have.add(tuple(k)); n += 1
    return n


def find_or_make(assets: list[dict], names: list[str]) -> str:
    """The asset at [area] or [area, machine...] (machine names joined with " / "; made where
    missing) -> its id."""
    names = [n for n in names if n]
    area = next((a for a in areas(assets) if a["name"] == names[0]), None)
    if area is None:
        area = new_asset(names[0], "area")
        assets.append(area)
    if len(names) == 1:
        return area["id"]
    mname = " / ".join(names[1:])
    m = next((a for a in children(assets, area["id"]) if a["name"] == mname), None)
    if m is None:
        m = new_asset(mname, "machine", area["id"])
        assets.append(m)
    return m["id"]


# ---------------------------------------------------------------- templates
def template_used(assets: list[dict], tid: str) -> int:
    return sum(1 for a in assets if a.get("template") == tid)


def apply_template(a: dict, t: dict) -> int:
    """The template's attributes as slots (names the asset has not got yet) -> how many were added.
    The asset keeps its type and its tags (the template's type is the one new machines get)."""
    a["template"] = t["id"]
    have = {x["name"].lower() for x in a["attributes"] if x["name"]}
    n = 0
    for s in t["attributes"]:
        if s["name"].lower() not in have:
            a["attributes"].append({"key": None, "name": s["name"], "kpi": bool(s["kpi"])})
            have.add(s["name"].lower()); n += 1
        else:
            for x in a["attributes"]:
                if x["name"].lower() == s["name"].lower():
                    x["kpi"] = bool(s["kpi"])
    return n


def template_from(a: dict, name_of, unit_of) -> dict:
    """A template with the asset's attributes: name_of(key) -> tag name, unit_of(key) -> unit."""
    t = new_template(f"{a['name']} template", a["type"] if a["type"] in {x[0] for x in LEVEL3} else "machine")
    t["icon"] = a.get("icon", "")
    for x in a["attributes"]:
        nm = x["name"] or (name_of(tuple(x["key"])) if x["key"] else "")
        if nm and nm.lower() not in {s["name"].lower() for s in t["attributes"]}:
            t["attributes"].append(new_slot(nm, unit_of(tuple(x["key"])) if x["key"] else "", x["kpi"]))
    return t


def map_tags(a: dict, t: dict | None, candidates: list[tuple]) -> list[tuple]:
    """Fill the asset's empty slots: candidates [(key, tag name)] -> [(slot name, key)], each tag at
    most once. A slot matches by its template pattern (`match`, case-insensitive), else by its name
    inside the tag name."""
    pats = {s["name"].lower(): s.get("match") or f"*{s['name']}*" for s in (t or {}).get("attributes", [])}
    used = {tuple(x["key"]) for x in a["attributes"] if x["key"]}
    out = []
    for x in a["attributes"]:
        if x["key"] or not x["name"]:
            continue
        pat = pats.get(x["name"].lower(), f"*{x['name']}*").lower()
        for key, name in candidates:
            if tuple(key) not in used and any(fnmatch.fnmatchcase(name.lower(), p.strip())
                                              for p in pat.split("|") if p.strip()):
                out.append((x["name"], tuple(key)))
                used.add(tuple(key))
                break
    return out


def apply_mapping(a: dict, mapping: list[tuple]) -> int:
    """Write map_tags' result into the slots -> how many were filled."""
    want = {n.lower(): k for n, k in mapping}
    n = 0
    for x in a["attributes"]:
        if not x["key"] and x["name"].lower() in want:
            x["key"] = list(want.pop(x["name"].lower())); n += 1
    return n


# ---------------------------------------------------------------- live
def kpi_rows(assets: list[dict]) -> list[tuple]:
    """[(asset id, attribute)] of the KPI attributes, each tag once, in tree order."""
    out, seen = [], set()
    for aid, x in rows(assets):
        if x["kpi"] and x["key"] and tuple(x["key"]) not in seen:
            seen.add(tuple(x["key"])); out.append((aid, x))
    return out


def rollup(assets: list[dict], aid: str | None, value_of) -> tuple | None:
    """The sum of each machine's first KPI value below the Enterprise (None) or an area, in the
    unit of the first one: value_of(key) -> (float | None, unit) -> (sum, unit) | None."""
    total, unit, n = 0.0, None, 0
    for m in machines(assets, aid):
        x = next((x for x in m["attributes"] if x["kpi"] and x["key"]), None)
        if x is None:
            continue
        v, u = value_of(tuple(x["key"]))
        if unit is None:
            unit = u or ""
        if v is not None and (u or "") == unit:
            total += v; n += 1
    return (total, unit) if n else None


def status(assets: list[dict], aid: str | None, state_of) -> str:
    """alarm / ok / none for an asset (None = the Enterprise) from its tags (machines' too):
    state_of(key) -> "alarm" | "ok" | "none"."""
    seen = {state_of(k) for k in keys(assets, aid)}
    return "alarm" if "alarm" in seen else ("ok" if "ok" in seen else "none")


def starter(key: str) -> tuple[dict, list[dict]]:
    """Use a starter ▾: (the area, its templates)."""
    _k, name, typ, temps = next(s for s in STARTERS if s[0] == key)
    area = new_asset(name, typ)
    out = []
    for tname, ttyp, slots in temps:
        t = new_template(tname, ttyp)
        t["attributes"] = [new_slot(n, u, k, m) for n, u, k, m in slots]
        out.append(t)
    return area, out


# ---------------------------------------------------------------- older projects
def _from_tree(p, nodes_: list[dict], kpi_roots: set[str]) -> dict[str, list[str]]:
    """Collections / 2.x Assets (a tree: id, name, parent, attributes, [purpose]) -> areas (top) and
    machines (below: their path under the area). Empty top nodes without children go; an "Equipment"
    top node without tags lifts its children. -> {dropped id: [ids that replace it]}."""
    idx = {n["id"]: n for n in nodes_ if isinstance(n, dict) and n.get("id")}
    kids: dict = {}
    for n in idx.values():
        kids.setdefault(n.get("parent") if n.get("parent") in idx else None, []).append(n)
    have = by_id(p.assets)
    gone: dict[str, list[str]] = {}

    def attrs(n, kpi):
        return [{"key": list(x["key"]), "name": str(x.get("name", "")), "kpi": kpi}
                for x in n.get("attributes", []) if isinstance(x, dict) and x.get("key")]

    def below(n, prefix):
        for k in kids.get(n["id"], []):
            yield k, f"{prefix}{k['name']}"
            yield from below(k, f"{prefix}{k['name']} / ")
    tops = []
    for n in kids.get(None, []):
        if not kids.get(n["id"]) and not n.get("attributes"):
            gone[n["id"]] = []                                   # an empty starter collection
        elif n.get("purpose") == "equipment" and n["name"] == "Equipment" and not n.get("attributes"):
            gone[n["id"]] = [k["id"] for k in kids.get(n["id"], [])]
            tops += kids.get(n["id"], [])                       # its areas
        else:
            tops.append(n)
    for n in tops:
        kpi = n["id"] in kpi_roots or n.get("purpose") == "kpi"
        if n["id"] in have:
            continue
        area = {**new_asset(n["name"], "area"), "id": n["id"], "description": str(n.get("description", "")),
                "attributes": attrs(n, kpi)}
        p.assets.append(area)
        for k, name in below(n, ""):
            if k["id"] not in have:
                p.assets.append({**new_asset(name, "machine", area["id"]), "id": k["id"],
                                 "description": str(k.get("description", "")), "attributes": attrs(k, kpi)})
    return gone


def migrate(p, d: dict | None = None) -> int:
    """Older projects -> areas and machines with the same tags: Collections (`collections`), the
    Assets of 2.x (`assets` without types), Tags groups (device tags, virtual / aggregation tags, the
    project's group list); logging profiles' "collections" / 2.x "assets" -> "assets". Idempotent.
    -> how many tags were placed by groups."""
    d = d or {}
    gone: dict[str, list[str]] = {}
    old = [x for x in d.get("assets") or [] if isinstance(x, dict) and "type" not in x]
    if old:
        gone.update(_from_tree(p, old, set()))
    if d.get("collections"):
        gone.update(_from_tree(p, list(d["collections"]), set()))
    for pr in getattr(p, "logging_profiles", []):
        ids = list(pr.pop("collections", None) or [])
        if ids:
            pr["assets"] = list(dict.fromkeys(list(pr.get("assets") or []) + ids))
        if pr.get("assets"):
            out = []
            for x in pr["assets"]:
                out += gone.get(x, [x])
            pr["assets"] = [x for x in dict.fromkeys(out) if x in by_id(p.assets)]
    n = 0

    def place(group: str, key):
        nonlocal n
        names = [x.strip() for x in (group or "").split("/") if x.strip()]
        if names:
            aid = find_or_make(p.assets, names)
            n += add_attributes(by_id(p.assets)[aid], [key])
    for dev in p.devices:
        for g in list(getattr(dev, "groups", [])):              # empty groups: the asset, without tags
            names = [x.strip() for x in g.split("/") if x.strip()]
            if names:
                find_or_make(p.assets, names)
        dev.groups = []
        for t in dev.tags:
            if t.group:
                place(t.group, (dev.id, t.index))
                t.group = ""
    from nxlogger.core.aggregation import agg_key
    from nxlogger.core.virtual_tags import vt_key
    for x in p.virtual_tags:
        if x.get("group"):
            place(x["group"], vt_key(x["id"]))
        x["group"] = ""
    for x in p.aggregations:
        if x.get("group"):
            place(x["group"], agg_key(x["id"]))
        x["group"] = ""
    for g in list(getattr(p, "groups", [])):
        names = [x.strip() for x in g.split("/") if x.strip()]
        if names:
            find_or_make(p.assets, names)
    p.groups = []
    return n
