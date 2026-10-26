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
from nxlogger.core import view_model as vm
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
    d = w.project.device(did)
    if d is None:
        return None
    n = sum(1 for t in d.tags if t.group == path or t.group.startswith(path + "/"))

    def apply(k, v):
        if k == "name":
            w.rename_group(did, path, v)
    return PropTarget(path.rsplit("/", 1)[-1], "folder", f"Group in {d.name} · {n} tag(s)",
                      [("Group", [F("name", "Name", "str", ""), F("path", "Path", "info", ""),
                                  F("tags", "Tags", "info", "")])],
                      {"name": path.rsplit("/", 1)[-1], "path": path, "tags": n}, apply,
                      [("Add tag", "tag", lambda: w.add_tags(did, path, False)),
                       ("Add tags", "list", lambda: w.add_tags(did, path, True)),
                       ("", "trash", lambda: w.delete_group(did, path))], key=("group", did, path))


# ====================================================================== tags
def tag_target(w, did: str, idx: int) -> PropTarget | None:
    d = w.project.device(did)
    t = d.tag(idx) if d else None
    if t is None:
        return None
    general = [F("name", "Name", "str", ""), F("enabled", "Enabled", "bool", True),
               F("group", "Group", "combo", "", [""] + d.all_groups()),
               F("unit", "Unit", "combo", "", list(cm.UNITS)), F("description", "Description", "text", ""),
               F("color", "Colour", "color", "")]
    if t.kind == "number":
        general.append(F("access", "Access", "choice", "R", {"R": "Read only", "RW": "Read / write"}))
    addr = [] if t.kind == "array" else [dict(f, key="addr." + f["key"]) for f in DRIVERS[d.driver]["address"]]
    if t.kind == "array":
        addr = [F("ch", "Channel", "info", "")]
    display = [F("decimals", "Decimals", "int", 3, (0, 8)),
               F("scaling", "Scaling", "button", None, lambda: w.edit_tag(did, idx), button=cm.describe_scale(t.scale)
                 or "None - edit...", tip="Linear, two-point or formula scaling")]
    live = [F("value", "Value", "info", "", level_of=lambda v: None),
            F("quality", "Quality", "info", "", level_of=lambda q: "ok" if q == "Good" else
              ("bad" if q in ("Bad", "No data") else "")),
            F("time", "Updated", "info", ""), F("key", "Formula name", "info", "",
                                                  tip="Use this name in variables (formulas)")]
    vals = {"name": t.name, "enabled": t.enabled, "group": t.group, "unit": t.unit,
            "description": t.description, "color": t.color, "access": t.access, "decimals": t.decimals,
            "ch": f"CH{t.index + 1} of the waveform packets",
            "key": cm.identifier(f"{d.name}_{t.name}"),
            **{"addr." + k: v for k, v in (t.address or {}).items()}}

    def apply(k, v):
        if w.recording and k not in ("description", "color", "decimals"):
            w.toast.popup("Stop recording to change tags")
            return
        if k.startswith("addr."):
            _set(t.address, k[5:], v, lambda: w.tags_changed(did, restart=True))
        elif k == "group":
            v = "/".join(p.strip() for p in str(v).split("/") if p.strip())
            _set(t, k, v, lambda: w.tags_changed(did))
        elif k == "name":
            v = v.strip()
            if v:
                _set(t, k, v, lambda: w.tags_changed(did))
        else:
            _set(t, k, v, lambda: w.tags_changed(did, restart=k in ("enabled", "access")))

    def lv():
        val, qual, good, stamp = w.tag_live(did, idx)
        return {"value": f"{val} {t.unit}".strip() if val else "-", "quality": qual or "-", "time": stamp or "-"}
    actions = [("Edit...", "pencil", lambda: w.edit_tag(did, idx))]
    if t.access == "RW" and t.kind == "number":
        actions.append(("Write...", "pencil-line", lambda: w.write_tag(did, idx)))
    actions += [("Alarm", "bell", lambda: w.add_alarm_for((did, idx))),
                ("", "trash", lambda: w.delete_tags(did, [idx]))]
    sub = f"{'Array' if t.kind == 'array' else 'Number'} tag · {d.name}" + \
          (f" / {t.group}" if t.group else "") + f" · {describe_address(d.driver, '', t)}"
    return PropTarget(t.name, "tag", sub, [("Tag", general), ("Address", addr), ("Display", display),
                                           ("Live", live)],
                      {**vals, **lv()}, apply, actions, lv, key=("tag", did, idx))


def tags_target(w, did: str, idxs: list[int]) -> PropTarget | None:
    d = w.project.device(did)
    if d is None:
        return None
    tags = [t for t in (d.tag(i) for i in idxs) if t is not None]

    def apply(k, v):
        if w.recording:
            w.toast.popup("Stop recording to change tags")
            return
        for t in tags:
            if k == "group":
                t.group = "/".join(p.strip() for p in str(v).split("/") if p.strip())
            else:
                setattr(t, k, v)
        w.tags_changed(did, restart=k in ("enabled", "access"))
    return PropTarget(f"{len(tags)} tags", "list", f"{d.name} · edit all selected tags at once",
                      [("Selected tags", [F("enabled", "Enabled", "bool", True),
                                          F("group", "Group", "combo", "", [""] + d.all_groups()),
                                          F("unit", "Unit", "combo", "", list(cm.UNITS)),
                                          F("decimals", "Decimals", "int", 3, (0, 8))])],
                      {"enabled": all(t.enabled for t in tags), "group": tags[0].group if tags else "",
                       "unit": tags[0].unit if tags else "", "decimals": tags[0].decimals if tags else 3},
                      apply, [("", "trash", lambda: w.delete_tags(did, idxs))], key=("tags", did, tuple(idxs)))


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
