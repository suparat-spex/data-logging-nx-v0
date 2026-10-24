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
from nxlogger.core.tags import (DRIVERS, PLANNED, SCAN_CHOICES, F, describe_address, driver_label)
from nxlogger.ui.widgets.property_grid import PropTarget
from nxlogger.ui.widgets.views import ADD_MENUS

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
    d = w.project.device(did)
    if d is None:
        return None
    locked = w.recording
    info = DRIVERS[d.driver]
    general = [F("name", "Name", "str", ""), F("enabled", "Enabled", "bool", True),
               F("driver", "Connector", "info", ""),
               F("scan_ms", "Scan interval", "combo", 1000, {x: f"{x} ms" for x in SCAN_CHOICES},
                 "How often the number tags are read"),
               F("description", "Description", "text", "")]
    settings = [dict(f, key="set." + f["key"]) for f in info["settings"]]
    status = [F("state", "State", "info", ""), F("message", "Message", "info", ""),
              F("scans", "Scans", "info", ""), F("errors", "Errors", "info", "")]
    vals = {"name": d.name, "enabled": d.enabled, "driver": info["label"], "scan_ms": d.scan_ms,
            "description": d.description, **{"set." + k: v for k, v in d.settings.items()}}

    def apply(k, v):
        if locked and k not in ("description",):
            w.toast.popup("Stop recording to change the device")
            return
        if k.startswith("set."):
            if k == "set.port" and d.driver == "modbus_rtu":
                v = str(v)
            _set(d.settings, k[4:], v, lambda: w.device_changed(did, structure=k == "set.mode"))
        elif k == "scan_ms":
            try:
                v = max(20, int(str(v).split()[0]))
            except ValueError:
                return
            _set(d, k, v, lambda: w.device_changed(did))
        elif k == "name":
            v = v.strip()
            if v and v not in {x.name for x in w.project.devices if x is not d}:
                _set(d, k, v, lambda: w.device_changed(did, restart=False))
        else:
            _set(d, k, v, lambda: w.device_changed(did, restart=k == "enabled"))

    def live():
        st, msg = w.device_state(did)
        drv = w.drivers.driver(did)
        return {"state": st.capitalize(), "message": msg or "-",
                "scans": f"{drv.scans:,}" if drv else "-", "errors": f"{drv.errors:,}" if drv else "-"}
    running = w.device_state(did)[0] != "stopped"
    actions = [("Stop" if running else "Start", "square" if running else "play",
                (lambda: w.stop_device(did)) if running else (lambda: w.start_device(did))),
               ("Add tag", "tag", lambda: w.add_tags(did, "", False))]
    if d.driver == "opcua":
        actions.append(("Browse", "search", lambda: w.browse_opcua(did, "")))
    actions.append(("", "trash", lambda: w.delete_device(did)))
    if d.driver in PLANNED:
        actions = actions[1:]
    return PropTarget(d.name, info["icon"], f"{driver_label(d.driver)} · {d.topic} · {len(d.tags)} tag(s)",
                      [("Device", general), ("Connection", settings), ("Status", status)],
                      {**vals, **live()}, apply, actions, live, key=("device", did))


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
        if k in ("n_rows", "n_cols"):
            axis = "rows" if k == "n_rows" else "cols"
            n = int(val)
            while len(v[axis]) < n:
                page.insert_line(axis, len(v[axis]))
            while len(v[axis]) > max(1, n):
                page.delete_line(axis, len(v[axis]) - 1)
                if len(v[axis]) > n:                    # cancelled
                    break
            w.show_properties(view_target(w, page))
            return
        if k == "name":
            val = val.strip()
            if not val or val in {x["name"] for x in w.project.views if x is not v}:
                return
        _set(v, k, val, lambda: w.view_props_changed(page, k))
    return PropTarget(v["name"], "layout", f"View · {len(v['rows'])} × {len(v['cols'])} grid · "
                                           f"{len(v['tiles'])} component(s)",
                      [("View", [F("name", "Name", "str", ""), F("description", "Description", "text", "")]),
                       ("Grid", [F("n_rows", "Rows", "int", 3, (1, vm.MAX_ROWS)),
                                 F("n_cols", "Columns", "int", 4, (1, vm.MAX_COLS)),
                                 F("gap", "Gap (px)", "int", 8, (0, 60)),
                                 F("margin", "Margin (px)", "int", 10, (0, 100))]),
                       ("Appearance", [F("bg", "Background", "color", ""),
                                       F("tile_bg", "Card background", "color", ""),
                                       F("tile_border", "Card border", "color", ""),
                                       F("title_bar", "Title bars (new)", "bool", True)])],
                      {**v, "n_rows": len(v["rows"]), "n_cols": len(v["cols"]),
                       "description": v.get("description", "")}, apply,
                      [("Done" if page.editing else "Edit layout", "pencil",
                        lambda: page.btn_edit.setChecked(not page.editing)),
                       ("", "trash", lambda: w.delete_view(page))], key=("view", v["id"]))


def _line_fields(axis: str, i: int, line: dict) -> list:
    return [F(f"{axis}.{i}.fixed", "Fixed size", "bool", False,
              tip="Fixed: size in pixels. Off: a share (weight) of the free space"),
            F(f"{axis}.{i}.size", "Size (px)" if line.get("fixed") else "Weight", "float",
              1.0, (20, 4000) if line.get("fixed") else (0.1, 50))]


def _apply_line(w, page, k, v) -> bool:
    if not k.startswith(("rows.", "cols.")):
        return False
    axis, i, what = k.split(".")
    i = int(i)
    if what == "fixed":
        page.set_line(axis, i, fixed=bool(v))
    else:
        page.set_line(axis, i, size=v)
    return True


def line_target(w, page, axis: str, i: int) -> PropTarget:
    line = page.view[axis][i]
    word = "Row" if axis == "rows" else "Column"
    a, b = ("above", "below") if axis == "rows" else ("left", "right")
    name = str(i + 1) if axis == "rows" else (chr(65 + i) if i < 26 else str(i + 1))
    n = sum(1 for t in page.view["tiles"] if i in range(t["row" if axis == "rows" else "col"],
                                                       t["row" if axis == "rows" else "col"]
                                                       + t["rowspan" if axis == "rows" else "colspan"]))
    return PropTarget(f"{word} {name}", "rows" if axis == "rows" else "columns",
                      f"{page.name} · {n} component(s) in this {word.lower()}",
                      [(word, _line_fields(axis, i, line))],
                      {f"{axis}.{i}.fixed": bool(line.get("fixed")), f"{axis}.{i}.size": float(line["size"])},
                      lambda k, v: _apply_line(w, page, k, v),
                      [(f"Insert {a}", "plus", lambda: page.insert_line(axis, i)),
                       (f"Insert {b}", "plus", lambda: page.insert_line(axis, i + 1)),
                       ("", "trash", lambda: page.delete_line(axis, i))], key=("line", page.view["id"], axis, i))


def cell_target(w, page, r: int, c: int) -> PropTarget:
    v = page.view
    items = [(text, lambda k=kind, p=preset: page.add_tile(k, at=(r, c), **p))
             for _, menu in ADD_MENUS for text, _i, kind, preset in menu]
    return PropTarget(f"Cell {chr(65 + c) if c < 26 else c + 1}{r + 1}", "grid",
                      f"{page.name} · empty - insert a component",
                      [("Row", _line_fields("rows", r, v["rows"][r])), ("Column", _line_fields("cols", c, v["cols"][c]))],
                      {f"rows.{r}.fixed": bool(v["rows"][r].get("fixed")), f"rows.{r}.size": float(v["rows"][r]["size"]),
                       f"cols.{c}.fixed": bool(v["cols"][c].get("fixed")), f"cols.{c}.size": float(v["cols"][c]["size"])},
                      lambda k, val: _apply_line(w, page, k, val),
                      [("Insert", "plus", items)], key=("cell", v["id"], r, c))


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
            F("show_title", "Show title", "bool", True),
            F("colspan", "Column span", "int", 1, (1, vm.MAX_COLS)),
            F("rowspan", "Row span", "int", 1, (1, vm.MAX_ROWS))]
    data, look = [], [F("card_bg", "Background", "color", ""), F("border_color", "Border", "color", "")]
    if kind == "text":
        comp = [comp[0]] + comp[3:]
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
        if k in ("colspan", "rowspan"):
            cs = int(v) if k == "colspan" else cfg["colspan"]
            rs = int(v) if k == "rowspan" else cfg["rowspan"]
            if vm.fits(page.view, cfg, cfg["row"], cfg["col"], rs, cs):
                cfg.update(colspan=cs, rowspan=rs)
                page.tile_changed(cfg, relayout=True)
            else:
                w.toast.popup("No room - the neighbouring cells are taken")
                w.show_properties(tile_target(w, page, cfg))
            return
        if k == "win_n":
            v = float(v)
        _set(cfg, k, v, lambda: page.tile_changed(cfg))
    actions = [("All settings...", "sliders", lambda: tile and page.edit_tile(tile)),
               ("", "copy", lambda: tile and page.duplicate_tile(tile)),
               ("", "trash", lambda: tile and page.delete_tile(tile))]
    pos = f"{chr(65 + cfg['col']) if cfg['col'] < 26 else cfg['col'] + 1}{cfg['row'] + 1}"
    return PropTarget(vm.tile_title(cfg), {"plot": "chart", "text": "heading", "value": "hash", "gauge": "gauge",
                                          "led": "circle-dot", "stats": "sigma", "alarms": "bell",
                                          "tags": "tag"}.get(kind, "layout"),
                      f"{page.name} · cell {pos}" + (f" · {cfg['colspan']}×{cfg['rowspan']} cells"
                                                    if (cfg['colspan'], cfg['rowspan']) != (1, 1) else ""),
                      [("Component", comp), ("Data", data), ("Appearance", look)], vals, apply, actions,
                      key=("tile", cfg["id"]))


def selection_target(w, page, sel) -> PropTarget:
    if sel is None:
        return view_target(w, page)
    if sel[0] == "tile":
        return tile_target(w, page, sel[1])
    if sel[0] == "cell":
        return cell_target(w, page, sel[1], sel[2])
    return line_target(w, page, "rows" if sel[0] == "row" else "cols", sel[1])


# ====================================================================== others
def simple_target(title: str, icon: str, subtitle: str, rows: dict, actions=(), key=()) -> PropTarget:
    """Read-only summary (alarms, variables, logging profiles: edited in their own dialogs)."""
    return PropTarget(title, icon, subtitle, [("Summary", [F(k, k, "info", "") for k in rows])], dict(rows),
                      None, list(actions), key=key)
