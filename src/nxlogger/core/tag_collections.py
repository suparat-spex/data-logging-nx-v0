"""
tag_collections.py - Collections (Project tree > Collections): sets of tags for a purpose. No Qt.
(Not `collections`: that name is Python's own module.)

A collection belongs to one purpose and references tags of any category (device, virtual,
aggregation, system) by key, so renaming a tag or a device never breaks it. A tag can be in any
number of collections; collections never own tags.

  Report      what goes into a report: each gets a statistics logging profile (15 min)
  KPI         key values: shown on the Start Page and in the Monitor bar
  Monitoring  what to watch: a view in one click, Monitor mode, alarms for the set
  Equipment   the equipment tree (site > area > unit > equipment): the only purpose that nests

  {"id", "name", "purpose", "parent": id | None (Equipment only), "description", "color",
   "attributes": [{"key": [tid, idx], "name": ""}]}      name "" = the tag's own name

The project keeps a flat list (each collection knows its parent). Tags groups and the Assets of
older projects become Equipment collections (migrate).
"""
from __future__ import annotations

import uuid

MAX_DEPTH = 4
PURPOSES = [   # key, name, icon, what it is for
    ("report", "Report", "file-text", "What goes into a report: each gets a statistics logging profile (15 min)"),
    ("kpi", "KPI", "gauge", "Key values: on the Start Page and in the Monitor bar"),
    ("monitoring", "Monitoring", "activity", "What to watch: a view in one click, Monitor mode, alarms for the set"),
    ("equipment", "Equipment", "boxes", "The equipment tree: site > area > unit > equipment (nested)"),
]
PURPOSE = {p[0]: p for p in PURPOSES}
NESTED = {"equipment"}                            # the purposes whose collections can have sub-collections
REPORT_INTERVAL_S = 900.0                         # the statistics profile of a new Report collection


def new_collection(name: str = "", purpose: str = "monitoring", parent: str | None = None) -> dict:
    return {"id": uuid.uuid4().hex[:8], "name": name or "Collection1", "purpose": purpose,
            "parent": parent if purpose in NESTED else None, "description": "", "color": "", "attributes": []}


def normalize_collection(c: dict) -> dict:
    for k, d in new_collection().items():
        if k != "id":
            c.setdefault(k, d)
    c.setdefault("id", uuid.uuid4().hex[:8])
    if c["purpose"] not in PURPOSE:
        c["purpose"] = "monitoring"
    if c["purpose"] not in NESTED:
        c["parent"] = None
    c["attributes"] = [{"key": list(x["key"]), "name": str(x.get("name", ""))}
                       for x in c.get("attributes", []) if isinstance(x, dict) and x.get("key")]
    return c


def of_purpose(cols: list[dict], purpose: str | None) -> list[dict]:
    return [c for c in cols if purpose is None or c["purpose"] == purpose]


def by_id(cols: list[dict]) -> dict[str, dict]:
    return {c["id"]: c for c in cols}


def children(cols: list[dict], parent: str | None, purpose: str | None = None) -> list[dict]:
    return [c for c in cols if c.get("parent") == parent and (purpose is None or c["purpose"] == purpose)]


def descendants(cols: list[dict], cid: str) -> list[dict]:
    """Every collection below `cid` (Equipment), not itself."""
    out, todo = [], [cid]
    while todo:
        cur = todo.pop(0)
        kids = children(cols, cur)
        out += kids
        todo += [k["id"] for k in kids]
    return out


def ordered(cols: list[dict], purpose: str) -> list[dict]:
    """The collections of a purpose in list order: a parent, then its sub-collections."""
    out = []

    def walk(parent):
        for c in children(cols, parent, purpose):
            out.append(c)
            walk(c["id"])
    walk(None)
    return out


def depth(cols: list[dict], cid: str | None) -> int:
    idx, n = by_id(cols), 0
    while cid is not None and cid in idx and n <= 64:
        n += 1
        cid = idx[cid].get("parent")
    return n


def subtree_height(cols: list[dict], cid: str) -> int:
    return 1 + max((subtree_height(cols, k["id"]) for k in children(cols, cid)), default=0)


def path(cols: list[dict], cid: str, sep: str = " / ") -> str:
    idx, names = by_id(cols), []
    while cid is not None and cid in idx and len(names) <= 64:
        names.append(idx[cid]["name"])
        cid = idx[cid].get("parent")
    return sep.join(reversed(names))


def label(cols: list[dict], cid: str) -> str:
    """'KPI: Plant KPIs', 'Equipment: Station A / Pump 01' - for tables and menus."""
    c = by_id(cols).get(cid)
    return f"{PURPOSE[c['purpose']][1]}: {path(cols, cid)}" if c else "(deleted collection)"


def keys(cols: list[dict], cid: str, deep: bool = True) -> list[tuple]:
    """The tag keys of a collection (and of its sub-collections), each once, in order."""
    idx = by_id(cols)
    nodes = ([idx[cid]] + (descendants(cols, cid) if deep else [])) if cid in idx else []
    out, seen = [], set()
    for c in nodes:
        for x in c["attributes"]:
            k = tuple(x["key"])
            if k not in seen:
                seen.add(k); out.append(k)
    return out


def rows(cols: list[dict], cid: str | None, purpose: str | None = None, deep: bool = True) -> list[tuple]:
    """[(collection id, attribute)] of a collection and its subs (None = every collection of the purpose)."""
    if cid is None:
        nodes = of_purpose(cols, purpose)
    else:
        nodes = [c for c in cols if c["id"] == cid] + (descendants(cols, cid) if deep else [])
    return [(c["id"], x) for c in nodes for x in c["attributes"]]


def can_move(cols: list[dict], cid: str, parent: str | None) -> str:
    """'' if `cid` may go under `parent`, else why not."""
    idx = by_id(cols)
    if parent is not None:
        if idx.get(cid, {}).get("purpose") not in NESTED or idx.get(parent, {}).get("purpose") not in NESTED:
            return "Only Equipment collections can have sub-collections."
        if parent == cid or parent in {d["id"] for d in descendants(cols, cid)}:
            return "A collection cannot go inside itself."
    if depth(cols, parent) + subtree_height(cols, cid) > MAX_DEPTH:
        return f"At most {MAX_DEPTH} levels of equipment."
    return ""


def delete(cols: list[dict], cid: str) -> list[dict]:
    """The list without `cid` and its sub-collections (the tags stay)."""
    gone = {cid} | {d["id"] for d in descendants(cols, cid)}
    return [c for c in cols if c["id"] not in gone]


def collections_of(cols: list[dict], key) -> list[str]:
    """Labels of the collections a tag is in (for the Tags table's Collections column)."""
    k = list(key)
    return [label(cols, c["id"]) for c in cols if any(x["key"] == k for x in c["attributes"])]


def find_or_make(cols: list[dict], names: list[str], purpose: str = "equipment") -> str:
    """The Equipment collection at a path of names (made where missing) -> its id."""
    parent = None
    for n in names[:MAX_DEPTH]:
        hit = next((c for c in cols if c.get("parent") == parent and c["name"] == n and c["purpose"] == purpose),
                   None)
        if hit is None:
            hit = new_collection(n, purpose, parent)
            cols.append(hit)
        parent = hit["id"]
    return parent


def add_attributes(c: dict, keys_) -> int:
    """Add tag keys that are not there yet (once per collection) -> how many were added."""
    have = {tuple(x["key"]) for x in c["attributes"]}
    n = 0
    for k in keys_:
        if tuple(k) not in have:
            c["attributes"].append({"key": list(k), "name": ""})
            have.add(tuple(k)); n += 1
    return n


def migrate(p, assets: list | None = None) -> int:
    """Older projects -> Equipment collections, with the same tags: the Assets of 2.x
    (`assets`), Tags groups (device tags, virtual / aggregation tags, the project's group list)
    and logging profiles' "assets". Idempotent. -> how many tags were placed."""
    n = 0
    for a in assets or []:                              # Assets (Equipment, same ids)
        if isinstance(a, dict) and a.get("id") and not any(c["id"] == a["id"] for c in p.collections):
            c = normalize_collection({**a, "purpose": "equipment"})
            p.collections.append(c)
    for pr in getattr(p, "logging_profiles", []):
        if "assets" in pr:
            pr.setdefault("collections", list(pr.pop("assets") or []))

    def place(group: str, key):
        nonlocal n
        names = [x.strip() for x in (group or "").split("/") if x.strip()]
        if names:
            cid = find_or_make(p.collections, names)
            n += add_attributes(next(c for c in p.collections if c["id"] == cid), [key])
    for d in p.devices:
        for g in list(getattr(d, "groups", [])):          # empty groups: the collection, without tags
            names = [x.strip() for x in g.split("/") if x.strip()]
            if names:
                find_or_make(p.collections, names)
        d.groups = []
        for t in d.tags:
            if t.group:
                place(t.group, (d.id, t.index))
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
            find_or_make(p.collections, names)
    p.groups = []
    return n
