"""
properties.py - what the Properties panel shows for each kind of selection.

Each function returns a PropTarget (property_grid.py) for one object: its fields, current
values, how an edit is applied to the project, action buttons and live (read-only)
values. `w` is the main window (DaqWindow); the functions use its small API:
  w.project, w.mark_dirty(), w.device_changed(did), w.tags_changed(did), w.views_changed(page)
  w.edit_tag / edit_device / delete_* / write_tag / start_device / stop_device ...
  w.channel_choices(), w.tag_live(did, idx), w.device_state(did), w.pick_keys(...)
"""
from __future__ import annotations

from nxlogger.core import analysis as an
from nxlogger.core import channel_math as cm
from nxlogger.core import system_tags as ss
from nxlogger.core import view_model as vm
from nxlogger.core import virtual_tags as vt
from nxlogger.core.tags import DRIVERS, F, describe_address, driver_label
from nxlogger.ui.widgets.property_grid import PropTarget

STATS = {k: an.STAT_LABEL[k] for k in an.STAT_KEYS}
WIN_UNITS = {k: v[0] for k, v in vm.WIN_UNITS.items()}


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
    p = w.project

    def apply(k, v):
        _set(p, k, v, lambda: (w.mark_dirty(), w.project_props_changed(k)))
    return PropTarget(p.name, "folder", "Project" + (f" · {w.project_path}" if w.project_path else " · not saved"),
                      [("Project", [F("name", "Name", "str", ""), F("operator", "Operator", "str", ""),
                                    F("description", "Description", "text", "")]),
                       ("Content", [F("n_dev", "Devices", "info", ""), F("n_tags", "Tags", "info", ""),
                                    F("n_views", "Views", "info", "")])],
                      {"name": p.name, "operator": p.operator, "description": p.description,
                       "n_dev": len(p.devices), "n_tags": sum(len(d.tags) for d in p.devices),
                       "n_views": len(p.views)}, apply, key=("project",))


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
    v = page.view

    def apply(k, val):
        if k == "name":
            val = val.strip()
            if not val or val in {x["name"] for x in w.project.views if x is not v}:
                return
        _set(v, k, val, lambda: w.view_props_changed(page, k))
    return PropTarget(v["name"], "layout", f"View · {len(v['tiles'])} component(s) · "
                                           "click a component to edit it",
                      [("View", [F("name", "Name", "str", ""), F("description", "Description", "text", "")]),
                       ("Snap grid", [F("grid", "Grid (px)", "int", 10, (2, 100)),
                                      F("snap", "Snap to grid", "bool", True),
                                      F("show_grid", "Show grid (edit)", "bool", True)]),
                       ("Appearance", [F("bg", "Background", "color", ""),
                                       F("tile_bg", "Card background", "color", ""),
                                       F("tile_border", "Card border", "color", ""),
                                       F("title_bar", "Title bars (new)", "bool", True)])],
                      {**v, "description": v.get("description", "")}, apply,
                      [("Done" if page.editing else "Edit layout", "pencil",
                        lambda: page.btn_edit.setChecked(not page.editing)),
                       ("", "trash", lambda: w.delete_view(page))], key=("view", v["id"]))


# ---------------------------------------------------------------------- components
def _keys_text(w, keys) -> str:
    names = {(tid, idx): lbl for tid, idx, lbl, _, _ in w.channel_choices()}
    lst = [names.get(tuple(k), "?").split(" [")[0] for k in keys or []]
    return ", ".join(lst[:3]) + (f" +{len(lst) - 3}" if len(lst) > 3 else "") if lst else "(none)"


def _window_fields() -> list:
    return [F("win_n", "Window length", "float", 0.0, (0, 1e7),
              "0 = the Quick View time window; otherwise buffered by the app"),
            F("win_unit", "Window unit", "choice", "s", WIN_UNITS),
            F("win_mode", "Window mode", "choice", "moving", vm.WIN_MODES)]


def tile_target(w, page, cfg: dict) -> PropTarget:
    kind = cfg["kind"]
    tile = page.tile_of(cfg)
    pick_many = lambda: w.pick_keys(cfg, "channels", True, page)        # noqa: E731
    pick_one = lambda: w.pick_keys(cfg, "channel", False, page)         # noqa: E731
    comp = [F("type", "Type", "info", ""), F("title", "Title", "str", "", tip="empty = automatic"),
            F("show_title", "Show title", "bool", True)]
    geo = [F("x", "X (px)", "int", 0, (0, 20000)), F("y", "Y (px)", "int", 0, (0, 20000)),
           F("w", "Width (px)", "int", 200, (vm.MIN_W, 20000)), F("h", "Height (px)", "int", 110, (vm.MIN_H, 20000))]
    data, look = [], [F("card_bg", "Background", "color", ""), F("border_color", "Border", "color", "")]
    if kind == "text":
        comp = [comp[0]]
        data = [F("text", "Text", "text", ""), F("style", "Style", "choice", "H1",
                                                 {"H1": "Header 1", "H2": "Header 2", "H3": "Header 3",
                                                  "Body": "Body text"}),
                F("align", "Alignment", "choice", "left", {"left": "Left", "center": "Centre", "right": "Right"}),
                F("value_color", "Text colour", "color", "")]
    elif kind == "plot":
        data = [F("channels", "Tags", "channels", None, pick_many, button="Choose..."),
                F("span_s", "Time span (s)", "float", 0.0, (0, 7200), "0 = the Quick View time window"),
                F("legend", "Legend", "bool", True), F("grid", "Grid lines", "bool", True),
                F("stack", "Stack (one plot per tag)", "bool", False),
                F("y_auto", "Auto Y range", "bool", True), F("y_min", "Y min", "float", -10.0),
                F("y_max", "Y max", "float", 10.0), F("line_width", "Line width", "float", 1.5, (0.5, 6))]
        if cfg.get("plot") != "time":
            data = data[:1] + data[2:5]
    elif kind in ("value", "gauge", "led"):
        data = [F("channel", "Tag", "channel", None, pick_one, button="Choose..."),
                F("quantity", "Show", "choice", "value", STATS)] + _window_fields() + \
               [F("decimals", "Decimals", "int", 3, (0, 8))]
        if kind == "value":
            data += [F("value_size", "Value size (px)", "int", 30, (10, 160)), F("value_color", "Value colour", "color", ""),
                     F("unit", "Unit text", "str", "", tip="empty = the tag's unit")]
        elif kind == "gauge":
            data += [F("gauge_style", "Style", "choice", "speed", vm.GAUGE_STYLES),
                     F("g_min", "Minimum", "float", 0.0), F("g_max", "Maximum", "float", 100.0),
                     F("unit", "Unit text", "str", "")]
        else:
            data = data[:5] + [F("led_op", "On when value", "choice", ">", [">", ">=", "<", "<=", "==", "!="]),
                               F("led_value", "Threshold", "float", 0.5),
                               F("led_on", "On colour", "color", ""), F("led_off", "Off colour", "color", ""),
                               F("led_on_text", "On text", "str", "ON"), F("led_off_text", "Off text", "str", "OFF")]
    elif kind in ("stats", "tags"):
        data = [F("channels", "Tags", "channels", None, pick_many, button="Choose...")]
        if kind == "stats":
            data += _window_fields()
        data.append(F("decimals", "Decimals", "int", 3, (0, 8)))
    elif kind == "alarms":
        data = [F("source", "Show", "choice", "active", vm.ALARM_SOURCES),
                F("max_rows", "Max rows", "int", 10, (1, 200))]
    vals = {**cfg, "type": vm.tile_title(cfg), "channels": _keys_text(w, cfg.get("channels")),
            "channel": _keys_text(w, [cfg["channel"]] if cfg.get("channel") else [])}

    def apply(k, v):
        if k in ("x", "y", "w", "h"):
            _set(cfg, k, page.snap(int(v)), lambda: page.tile_changed(cfg, geometry=True))
            return
        if k == "win_n":
            v = float(v)
        _set(cfg, k, v, lambda: page.tile_changed(cfg))
    actions = [("All settings...", "sliders", lambda: tile and page.edit_tile(tile)),
               ("", "copy", lambda: tile and page.duplicate_tile(tile)),
               ("", "trash", lambda: tile and page.delete_tile(tile))]
    return PropTarget(vm.tile_title(cfg), {"plot": "chart", "text": "heading", "value": "hash", "gauge": "gauge",
                                          "led": "circle-dot", "stats": "sigma", "alarms": "bell",
                                          "tags": "tag"}.get(kind, "layout"),
                      f"{page.name} · {cfg['w']}×{cfg['h']} px at {cfg['x']}, {cfg['y']}",
                      [("Component", comp), ("Data", data), ("Position", geo), ("Appearance", look)],
                      vals, apply, actions,
                      key=("tile", cfg["id"]))


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
