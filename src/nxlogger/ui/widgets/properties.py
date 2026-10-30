"""
properties.py - what the Properties panel shows for each kind of selection.

Each function returns a PropTarget (property_grid.py) for one object: its fields, current
values, how an edit is applied to the project, action buttons and live (read-only)
values. `w` is the main window (DaqWindow); the functions use its small API:
  w.project, w.mark_dirty(), w.device_changed(did), w.tags_changed(did), w.views_changed(page)
  w.edit_tag / edit_device / delete_* / write_tag / start_device / stop_device ...
  w.channel_choices(), w.tag_live(did, idx), w.device_state(did)
"""
from __future__ import annotations

import time

from nxlogger.core import aggregation as ag
from nxlogger.core import analysis as an
from nxlogger.core import channel_math as cm
from nxlogger.core import system_tags as ss
from nxlogger.core import view_model as vm
from nxlogger.core import virtual_tags as vt
from nxlogger.core.tags import DRIVERS, F, describe_address, driver_label
from nxlogger.ui.widgets.property_grid import PropTarget

STATS = {k: an.STAT_LABEL[k] for k in an.STAT_KEYS}


def _set(obj, key, value, on_change) -> bool:
    """setattr / item assignment only if the value really changed; then on_change()."""
    cur = obj.get(key) if isinstance(obj, dict) else getattr(obj, key)
    if cur == value:
        return False
    if isinstance(obj, dict):
        obj[key] = value
    else:
        setattr(obj, key, value)
    on_change()
    return True


# ====================================================================== project
def project_target(w) -> PropTarget:
    """Information only, like a device - the project is changed in its pop-up (Edit...)."""
    p = w.project
    counts = w.project_counts()
    general = [F("name", "Name", "info", ""), F("operator", "Operator", "info", ""),
               F("description", "Description", "info", ""), F("file", "Project file", "info", ""),
               F("folder", "Log folder", "info", "")]
    content = [F(k, k, "info", "") for k in counts]
    vals = {"name": p.name, "operator": p.operator or "-", "description": p.description or "-",
            "file": w.project_path or "Not saved yet", "folder": p.logging.folder, **{k: str(n) for k, n in counts.items()}}
    return PropTarget(p.name + (" *" if w.dirty else ""), "folder",
                      "Project" + (f" · {w.project_path}" if w.project_path else " · not saved"),
                      [("Project", general), ("Content", content)], vals, None,
                      [("Edit...", "pencil", w.edit_project)], key=("project",), readonly=True)


# ====================================================================== devices
def device_target(w, did: str) -> PropTarget | None:
    """Information only - a device is configured in its settings pop-up (Edit...)."""
    d = w.project.device(did)
    if d is None:
        return None
    info = DRIVERS[d.driver]
    general = [F("name", "Name", "info", ""), F("driver", "Connector", "info", ""),
               F("enabled", "Enabled", "bool", True), F("scan_ms", "Scan interval", "info", ""),
               F("tags", "Tags", "info", ""), F("description", "Description", "info", "")]
    settings = [dict(f, key="set." + f["key"]) for f in info["settings"]]
    status = [F("state", "State", "info", ""), F("message", "Message", "info", ""),
              F("scans", "Scans", "info", ""), F("errors", "Errors", "info", "")]
    vals = {"name": d.name, "enabled": d.enabled, "driver": info["label"], "tags": str(len(d.tags)),
            "scan_ms": "-" if d.wave else f"{d.scan_ms} ms", "description": d.description,
            **{"set." + k: v for k, v in d.settings.items()}}

    def live():
        st, msg = w.device_state(did)
        drv = w.drivers.driver(did)
        return {"state": st.capitalize(), "message": msg or "-",
                "scans": f"{drv.scans:,}" if drv else "-", "errors": f"{drv.errors:,}" if drv else "-"}
    return PropTarget(d.name, info["icon"], f"{driver_label(d.driver)} · {d.topic}",
                      [("Device", general), ("Connection", settings), ("Status", status)],
                      {**vals, **live()}, None, [("Edit...", "pencil", lambda: w.edit_device(did))], live,
                      key=("device", did), readonly=True)


def group_target(w, did: str, path: str) -> PropTarget | None:
    """Information only - a group is changed in its pop-up (Edit...)."""
    d = w.project.device(did)
    if d is None:
        return None
    n = sum(1 for t in d.tags if t.group == path or t.group.startswith(path + "/"))
    return PropTarget(path.rsplit("/", 1)[-1], "folder", f"Group in {d.name} · {n} tag(s)",
                      [("Group", [F("name", "Name", "info", ""), F("path", "Path", "info", ""),
                                  F("device", "Device", "info", ""), F("tags", "Tags", "info", "")])],
                      {"name": path.rsplit("/", 1)[-1], "path": path, "device": d.name, "tags": str(n)}, None,
                      [("Edit...", "pencil", lambda: w.edit_group(did, path))], key=("group", did, path),
                      readonly=True)


# ====================================================================== tags
def tag_target(w, did: str, idx: int) -> PropTarget | None:
    """Information only, like a device - a tag is changed in its pop-up (Edit...)."""
    d = w.project.device(did)
    t = d.tag(idx) if d else None
    if t is None:
        return None
    general = [F("name", "Name", "info", ""), F("enabled", "Enabled", "bool", True),
               F("group", "Group", "info", ""), F("unit", "Unit", "info", ""),
               F("description", "Description", "info", ""), F("color", "Colour", "info", "")]
    if t.kind == "number":
        general.append(F("access", "Access", "info", ""))
    addr = [F("ch", "Channel", "info", "")] if t.kind == "array" else         [dict(f, key="addr." + f["key"]) for f in DRIVERS[d.driver]["address"]]
    display = [F("decimals", "Decimals", "info", ""), F("scaling", "Scaling", "info", "")]
    live = [F("value", "Value", "info", "", level_of=lambda v: None),
            F("quality", "Quality", "info", "", level_of=lambda q: "ok" if q == "Good" else
              ("bad" if q in ("Bad", "No data") else "")),
            F("time", "Updated", "info", ""), F("key", "Formula name", "info", "",
                                                  tip="Use this name in variables (formulas)")]
    vals = {"name": t.name, "enabled": t.enabled, "group": t.group or "-", "unit": t.unit or "-",
            "description": t.description or "-", "color": t.color or w.color_of(did, idx),
            "access": "Read / write" if t.access == "RW" else "Read only", "decimals": str(t.decimals),
            "scaling": cm.describe_scale(t.scale) or "None", "ch": f"CH{t.index + 1} of the waveform packets",
            "key": cm.identifier(f"{d.name}_{t.name}"),
            **{"addr." + k: v for k, v in (t.address or {}).items()}}

    def lv():
        val, qual, good, stamp = w.tag_live(did, idx)
        return {"value": f"{val} {t.unit}".strip() if val else "-", "quality": qual or "-", "time": stamp or "-"}
    sub = f"{'Array' if t.kind == 'array' else 'Number'} tag · {d.name}" +           (f" / {t.group}" if t.group else "") + f" · {describe_address(d.driver, '', t)}"
    return PropTarget(t.name, "tag", sub, [("Tag", general), ("Address", addr), ("Display", display),
                                           ("Live", live)],
                      {**vals, **lv()}, None, [("Edit...", "pencil", lambda: w.edit_tag(did, idx))], lv,
                      key=("tag", did, idx), readonly=True)


def tags_target(w, did: str, idxs: list[int]) -> PropTarget | None:
    """Several tags: a summary (information only - change them from the table's menu)."""
    d = w.project.device(did)
    if d is None:
        return None
    tags = [t for t in (d.tag(i) for i in idxs) if t is not None]
    groups = sorted({t.group or "(no group)" for t in tags})
    units = sorted({t.unit or "-" for t in tags})
    return PropTarget(f"{len(tags)} tags", "list", f"{d.name} · right-click the table to enable, move or delete",
                      [("Selected tags", [F("device", "Device", "info", ""), F("count", "Tags", "info", ""),
                                          F("enabled", "Enabled", "info", ""), F("groups", "Groups", "info", ""),
                                          F("units", "Units", "info", "")])],
                      {"device": d.name, "count": str(len(tags)),
                       "enabled": f"{sum(t.enabled for t in tags)} of {len(tags)}",
                       "groups": ", ".join(groups[:6]) + (" ..." if len(groups) > 6 else ""),
                       "units": ", ".join(units[:6]) + (" ..." if len(units) > 6 else "")},
                      None, [], key=("tags", did, tuple(idxs)), readonly=True)


# ====================================================================== views
def view_target(w, page) -> PropTarget:
    """Information only, like devices and tags - the view is changed in its pop-up (Edit...)."""
    v = page.view
    tags = sorted({tuple(k) for t in v["tiles"] for k in (t.get("channels") or []) + ([t["channel"]]
                                                                                     if t.get("channel") else [])})
    rows = {"Name": v["name"], "Description": v.get("description") or "-",
            "Size": f"{v.get('width', 1920)} × {v.get('height', 1080)} px" +
                    (" · centred" if v.get("align") == "center" else ""),
            "Components": f"{len(v['tiles'])} ({sum(1 for t in v['tiles'] if vm.is_graph(t))} graph(s))",
            "Tags used": _keys_text(w, [list(k) for k in tags]) if tags else "-",
            "Grid": f"{v.get('grid', 10)} px" + (", snap" if v.get("snap", True) else "")}
    return PropTarget(v["name"], "layout", f"View · {len(v['tiles'])} component(s) · double-click a component "
                                           "to edit it (Edit mode)",
                      [("View", [F(k, k, "info", "") for k in rows])], rows, None,
                      [("Edit...", "pencil", lambda: w.view_properties(page)),
                       ("Done" if page.editing else "Edit layout", "layout",
                        lambda: page.btn_edit.setChecked(not page.editing)),
                       ("Monitor", "maximize", lambda: w.enter_monitor(v["id"]))],
                      key=("view", v["id"]), readonly=True)


def _keys_text(w, keys) -> str:
    names = {(tid, idx): lbl for tid, idx, lbl, _, _ in w.channel_choices()}
    lst = [names.get(tuple(k), "?").split(" [")[0] for k in keys or []]
    return ", ".join(lst[:3]) + (f" +{len(lst) - 3}" if len(lst) > 3 else "") if lst else "(none)"


def tile_target(w, page, cfg: dict) -> PropTarget:
    """Information only - a component is changed in its pop-up (Edit...), or moved / resized with
    the mouse in Edit mode."""
    kind = cfg["kind"]
    tile = page.tile_of(cfg)
    rows = {"Type": vm.tile_title(cfg), "Title": cfg.get("title") or "(automatic)",
            "Position": f"{cfg['x']}, {cfg['y']} px", "Size": f"{cfg['w']} × {cfg['h']} px"}
    if kind == "text":
        rows["Text"] = (cfg.get("text") or "").replace("\n", " ")[:80] or "-"
    elif cfg.get("channel"):
        rows["Tag"] = _keys_text(w, [cfg["channel"]])
        rows["Show"] = STATS.get(cfg.get("quantity", "value"), "Value") + (
            f" · {vm.win_text(cfg)}" if cfg.get("quantity", "value") != "value" else "")
    elif kind in ("plot", "stats", "tags"):
        rows["Tags"] = _keys_text(w, cfg.get("channels"))
        if kind == "stats":
            rows["Window"] = vm.win_text(cfg)
    elif kind == "alarms":
        rows["Show"] = vm.ALARM_SOURCES.get(cfg.get("source", "active"), "-")
    actions = [("Edit...", "pencil", lambda: tile and page.edit_tile(tile)),
               ("Duplicate", "copy", lambda: tile and page.duplicate_tile(tile)),
               ("Delete", "trash", lambda: tile and page.delete_tile(tile))]
    return PropTarget(vm.tile_title(cfg), {"plot": "chart", "text": "heading", "value": "hash", "gauge": "gauge",
                                          "led": "circle-dot", "stats": "sigma", "alarms": "bell",
                                          "tags": "tag"}.get(kind, "layout"),
                      f"{page.name} · {cfg['w']}×{cfg['h']} px at {cfg['x']}, {cfg['y']}",
                      [("Component", [F(k, k, "info", "") for k in rows])], rows, None, actions,
                      key=("tile", cfg["id"]), readonly=True)


def selection_target(w, page, sel) -> PropTarget:
    if sel and sel[0] == "tile":
        return tile_target(w, page, sel[1])
    return view_target(w, page)


# ====================================================================== others
def simple_target(title: str, icon: str, subtitle: str, rows: dict, actions=(), key=()) -> PropTarget:
    """Read-only summary (alarms, variables, logging profiles: edited in their own dialogs)."""
    return PropTarget(title, icon, subtitle, [("Summary", [F(k, k, "info", "") for k in rows])], dict(rows),
                      None, list(actions), key=key)


def virtual_target(w, vid: str) -> PropTarget | None:
    """A Virtual tag: read-only here (changed in its pop-up, Edit...), with its live value."""
    v = w.virtual_tag(vid)
    if v is None:
        return None
    text = v.get("dtype") == "text"
    rows = {"Name": v["name"], "Type": vt.TYPES[v["kind"]][0], "Data type": vt.DTYPES[v.get("dtype", "number")],
            "Group": v.get("group") or "-", "Unit": v.get("unit") or "-",
            "Definition": w.vt_describe(v) or "-",
            "Inputs": ", ".join(f"{i['alias']} = {w.key_label(tuple(i['key']))}" for i in v.get("inputs", [])) or "-",
            "Update": "-" if v["kind"] in vt.NO_INPUT else vt.UPDATES.get(v.get("update"), "-"),
            "Formula name": "- (text)" if text else vt.formula_name(v), "Enabled": "Yes" if v["enabled"] else "No",
            "Description": v.get("description") or "-"}
    live = [F("value", "Value", "info", ""),
            F("quality", "Quality", "info", "", level_of=lambda q: "ok" if q.startswith("Good") else
              ("bad" if q in ("Bad", "Error") else ("warn" if q == "Held" else ""))),
            F("message", "Message", "info", ""), F("time", "Updated", "info", "")]
    key = vt.vt_key(vid)

    def values():
        val, qual, _g, stamp = w.tag_live(*key)
        r = w.vt_engine.results.get(vid)
        return {"value": f"{val} {v.get('unit', '')}".strip() if val else "-", "quality": qual or "-",
                "message": (r.msg if r is not None and r.msg else "-"), "time": stamp or "-"}
    actions = [("Edit...", "pencil", lambda: w.edit_virtual(vid))]
    if not text:
        actions.append(("Alarm", "bell", lambda: w.add_alarm_for(key)))
    return PropTarget(v["name"], vt.TYPES[v["kind"]][1], f"Virtual tag · {vt.TYPES[v['kind']][0]}",
                      [("Live", live), ("General", [F(k, k, "info", "") for k in rows])],
                      {**rows, **values()}, None, actions, live=values, key=("vt", vid), readonly=True)


def _span(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 86400} d {s % 86400 // 3600} h" if s >= 86400 else f"{s // 3600} h {s % 3600 // 60} min" \
        if s >= 3600 else f"{s // 60} min {s % 60} s"


def aggregation_target(w, aid: str) -> PropTarget | None:
    """An Aggregation tag: read-only here (changed in its pop-up, Edit...), live value, previous
    period, period start and the time to the next reset."""
    a = w.aggregation(aid)
    if a is None:
        return None
    period = ag.has_period(a)
    rows = {"Name": a["name"], "Type": ag.TYPES[a["kind"]][0], "Group": a.get("group") or "-",
            "Unit": a.get("unit") or "-", "Definition": w.agg_describe(a), "Period": ag.describe_period(a),
            "Formula name": ag.formula_name(a) + (f", {ag.formula_name(a, True)}" if period and a.get("keep_prev")
                                                   else ""),
            "Keep on restart": ("Yes" if a.get("retain") else "No") if period else "-",
            "Enabled": "Yes" if a["enabled"] else "No", "Description": a.get("description") or "-"}
    live = [F("value", "Value", "info", ""),
            F("quality", "Quality", "info", "", level_of=lambda q: "ok" if q == "Good" else
              ("bad" if q == "Error" else ("warn" if q == "Paused" else ""))),
            F("previous", "Previous period", "info", ""), F("since", "Since", "info", ""),
            F("next", "Next reset", "info", ""), F("message", "Message", "info", "")]
    key = ag.agg_key(aid)

    def values():
        val, qual, _g, _stamp = w.tag_live(*key)
        st = w.agg_engine.states.get(aid)
        unit = a.get("unit", "")
        prev = st.previous if st is not None else None
        nxt = st.next if st is not None else None
        held = (f"  (at {time.strftime('%H:%M:%S', time.localtime(st.hold_t))})"
                if st is not None and a["kind"] == "minmax" and st.hold_t else "")
        return {"value": (f"{val} {unit}".strip() + held) if val else "-", "quality": qual or "-",
                "previous": f"{prev:.{a.get('decimals', 2)}f} {unit}".strip() if prev is not None else "-",
                "since": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.start)) if st is not None and st.start
                else "-",
                "next": f"{time.strftime('%a %H:%M', time.localtime(nxt))}  (in {_span(nxt - time.time())})"
                if nxt else "-", "message": (st.msg if st is not None and st.msg else "-")}
    actions = [("Edit...", "pencil", lambda: w.edit_aggregation(aid))]
    if period:
        actions.append(("Reset now...", "refresh", lambda: w.reset_aggregation(aid)))
    actions.append(("Alarm", "bell", lambda: w.add_alarm_for(key)))
    return PropTarget(a["name"], ag.TYPES[a["kind"]][1], f"Aggregation tag · {ag.TYPES[a['kind']][0]}",
                      [("Live", live), ("General", [F(k, k, "info", "") for k in rows])],
                      {**rows, **values()}, None, actions, live=values, key=("agg", aid), readonly=True)


def system_target(w, tid: str) -> PropTarget | None:
    """A System Tag: read-only (computed by the app), with its live value."""
    t = w.sys_tag(tid)
    if t is None:
        return None
    rows = {"Name": t.name, "Group": t.group, "Type": ss.KINDS[t.kind],
            "Unit": t.unit or "-", "Formula name": f"System_{t.name}" if t.streamed else "- (text)",
            "Description": t.description}
    live = [F("value", "Value", "info", ""), F("time", "Updated", "info", "")]

    def values():
        val, _q, _g, stamp = w.tag_live(*t.key)
        return {"value": f"{val} {t.unit}".strip() if val else "-", "time": stamp or "-"}
    actions = [("Alarm", "bell", lambda: w.add_alarm_for(t.key))] if t.streamed else []
    return PropTarget(t.name, "cpu", "System tag · read-only",
                      [("Live", live), ("General", [F(k, k, "info", "") for k in rows])],
                      {**rows, **values()}, None, actions, live=values, key=("sys", tid), readonly=True)
