"""
assets.py - Assets (Project tree > Assets): how tags are used, separate from what they are. No Qt.

An asset is a named node of a tree (site > area > unit > equipment, or a working set such as
"Energy KPIs"); its attributes are references to tags of any category (device, virtual,
aggregation, system), stored by key, so renaming a tag or a device never breaks an asset.
A tag can be an attribute of any number of assets; assets never own tags.

  {"id", "name", "parent": id | None, "description", "color",
   "attributes": [{"key": [tid, idx], "name": ""}]}      name "" = the tag's own name

The project keeps a flat list (each asset knows its parent): moving an asset is one field.
Up to MAX_DEPTH levels. Tags groups of older projects become assets (migrate_groups).
"""
from __future__ import annotations

import uuid

MAX_DEPTH = 4


def new_asset(name: str = "", parent: str | None = None) -> dict:
    return {"id": uuid.uuid4().hex[:8], "name": name or "Asset1", "parent": parent, "description": "",
            "color": "", "attributes": []}


def normalize_asset(a: dict) -> dict:
    for k, d in new_asset().items():
        if k != "id":
            a.setdefault(k, d)
    a.setdefault("id", uuid.uuid4().hex[:8])
    a["attributes"] = [{"key": list(x["key"]), "name": str(x.get("name", ""))}
                       for x in a.get("attributes", []) if isinstance(x, dict) and x.get("key")]
    return a


def by_id(assets: list[dict]) -> dict[str, dict]:
    return {a["id"]: a for a in assets}


def children(assets: list[dict], parent: str | None) -> list[dict]:
    return [a for a in assets if a.get("parent") == parent]


def descendants(assets: list[dict], aid: str) -> list[dict]:
    """Every asset below `aid` (depth first, not itself)."""
    out, todo = [], [aid]
    while todo:
        cur = todo.pop(0)
        kids = children(assets, cur)
        out += kids
        todo += [k["id"] for k in kids]
    return out


def depth(assets: list[dict], aid: str | None) -> int:
    """1 for a top-level asset, 0 for None (the root)."""
    idx, n = by_id(assets), 0
    while aid is not None and aid in idx and n <= 64:
        n += 1
        aid = idx[aid].get("parent")
    return n


def subtree_height(assets: list[dict], aid: str) -> int:
    """1 for a leaf, 2 with children ..."""
    kids = children(assets, aid)
    return 1 + max((subtree_height(assets, k["id"]) for k in kids), default=0)


def path(assets: list[dict], aid: str, sep: str = " / ") -> str:
    idx, names = by_id(assets), []
    while aid is not None and aid in idx and len(names) <= 64:
        names.append(idx[aid]["name"])
        aid = idx[aid].get("parent")
    return sep.join(reversed(names))


def keys(assets: list[dict], aid: str, deep: bool = True) -> list[tuple]:
    """The tag keys of an asset (and of its sub-assets), each once, in tree order."""
    idx = by_id(assets)
    nodes = [idx[aid]] + (descendants(assets, aid) if deep else []) if aid in idx else []
    out, seen = [], set()
    for a in nodes:
        for x in a["attributes"]:
            k = tuple(x["key"])
            if k not in seen:
                seen.add(k); out.append(k)
    return out


def rows(assets: list[dict], aid: str | None, deep: bool = True) -> list[tuple[str, dict]]:
    """[(asset id, attribute)] of an asset and its sub-assets (None = every asset)."""
    nodes = assets if aid is None else ([a for a in assets if a["id"] == aid] +
                                         (descendants(assets, aid) if deep else []))
    return [(a["id"], x) for a in nodes for x in a["attributes"]]


def can_move(assets: list[dict], aid: str, parent: str | None) -> str:
    """'' if `aid` may go under `parent`, else why not (a cycle, too deep)."""
    if parent == aid or parent in {d["id"] for d in descendants(assets, aid)}:
        return "An asset cannot go inside itself."
    if depth(assets, parent) + subtree_height(assets, aid) > MAX_DEPTH:
        return f"At most {MAX_DEPTH} levels of assets."
    return ""


def delete(assets: list[dict], aid: str) -> list[dict]:
    """The list without `aid` and its sub-assets (the tags stay)."""
    gone = {aid} | {d["id"] for d in descendants(assets, aid)}
    return [a for a in assets if a["id"] not in gone]


def assets_of(assets: list[dict], key) -> list[str]:
    """Paths of the assets a tag belongs to (for the Tags table's Assets column)."""
    k = list(key)
    return [path(assets, a["id"]) for a in assets if any(x["key"] == k for x in a["attributes"])]


def find_or_make(assets: list[dict], names: list[str]) -> str:
    """The asset at a path of names (made where missing) -> its id."""
    parent = None
    for n in names[:MAX_DEPTH]:
        hit = next((a for a in assets if a.get("parent") == parent and a["name"] == n), None)
        if hit is None:
            hit = new_asset(n, parent)
            assets.append(hit)
        parent = hit["id"]
    return parent


def add_attributes(asset: dict, keys_) -> int:
    """Add tag keys that are not there yet -> how many were added."""
    have = {tuple(x["key"]) for x in asset["attributes"]}
    n = 0
    for k in keys_:
        if tuple(k) not in have:
            asset["attributes"].append({"key": list(k), "name": ""})
            have.add(tuple(k)); n += 1
    return n


def migrate_groups(p) -> int:
    """Tags groups (device tags, virtual / aggregation tags, the project's group list) become
    assets with the same tags; the group fields are cleared. -> how many tags were placed.
    Idempotent: nothing to do once the groups are gone."""
    n = 0

    def place(group: str, key):
        nonlocal n
        names = [x.strip() for x in (group or "").split("/") if x.strip()]
        if names:
            aid = find_or_make(p.assets, names)
            a = next(x for x in p.assets if x["id"] == aid)
            n += add_attributes(a, [key])
    for d in p.devices:
        for g in list(getattr(d, "groups", [])):          # empty groups: the asset, without tags
            names = [x.strip() for x in g.split("/") if x.strip()]
            if names:
                find_or_make(p.assets, names)
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
            find_or_make(p.assets, names)
    p.groups = []
    return n
