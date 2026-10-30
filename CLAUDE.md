# NX Logger

PySide6 desktop data logger (Yokogawa GA10 style, SCADA-like connectivity like Genesis64 /
FUXA): devices (Simulation, Modbus TCP / RTU, MQTT, OPC UA; OPC DA planned) hold tags; the
app shows them in free-form views (snap grid), alarms, computes statistics and records them.
Formerly SX Logger / MQDAQ (MQTT waveform logger; last flat-file version: git tag `v6-mqdaq`).

## Layout (src/nxlogger)

| Path | Owns |
|---|---|
| `core/` | **No Qt.** `tags` (DeviceCfg, TagCfg, connector schemas `DRIVERS`, ScanBlock, tag CSV), `project` (model + file migration .sxl/.mqdaq -> .nxl), `view_model` (views: components with x / y / w / h on a snap grid; grid-layout views from 2.0 convert), `stat_buffer` (statistics windows s / min / h / samples, moving / block), `ring` (live buffers), `recording` (CSV / WPK writers, Recorder, zstd/gzip), `ml_log` (AI/ML data log), `alarm_rules` (AlarmEngine, Notifier), `channel_math` (scaling, safe formulas), `analysis` (statistics, FFT, STFT, histogram), `variables` (calculated tags), `templates` (Start Page > New project), `virtual_tags` (Tags > Virtual: constants, formulas, conversions, logic, selectors, text maps), `aggregation` (Tags > Aggregation: statistic, totalizer, counter, runtime, min / max hold, change; periods / resets, retain), `system_tags` (System Tags: clock, app, recording, device status), `wavepacket` (ESP32 packet format) |
| `comms/drivers/` | One driver thread per device: `base` (PollDriver, message protocol), `sim`, `modbus`, `mqtt`, `opcua` (+ Browser). `comms/manager.py` starts / stops them, one queue |
| `comms/mqtt_client.py` | paho-mqtt in Qt signals - only the waveform simulator (`sim/`) uses it |
| `ui/main_window.py` | `DaqWindow`: docks, documents, host API for pages, data path, recording (big - grep, don't read whole) |
| `ui/dialogs/` | One dialog per file. Dialogs never import each other; shared helpers (SchemaForm, field_editor, ChannelPicker ...) in `common.py`. `device_pages/`: the connector sections of the one-page device pop-up, one page per connector (`base.ConnectionPage`: sections, labels, helpers, summary, validate) |
| `ui/widgets/` | `form_tab` (editor tabs: a dialog embedded as a closable document with Apply / Refresh / Close / New... / Apply & Close / Apply & New...), `connectivity` (Connectivity document: device table, import / export JSON / CSV), `start_page` (Start Page: recent projects, templates, sessions, get started, docs), `tags_page` (Tags document: device tree + tag table), `property_grid` (Properties panel), `properties` (what it shows per selection), `views` (free-form ViewPage + components), `gauge`, `alarm_panel`, `variables_panel`, `mqtt_panel` (simulator) |
| `ui/theme/` | `ui_kit` (tokens `T`, QSS, AppWindow shell), `app_style` (extra QSS, Fusion), `DESIGN.md` |
| `ui/icons.py` | All icons. Lucide SVGs in `resources/icons/ui/` |
| `paths.py`, `config.py` | File locations (dev vs EXE) and `config.ini` |
| `sim/publisher.py` | ESP32 waveform generator over MQTT |
| `sim/water_plant.py` | Water treatment plant simulator over MQTT (JSON per area: raw water, pumps, dosing, filters, treated, power meter, environment, KPI); exports the NX Logger device / tag CSV; `water_plant_gui.py` = its window |

Docs: `docs/ui-guidelines.md` (editor tabs vs pop-ups), `docs/layout-and-menus.md` (Connectivity / Tags layout, lists, menus, pop-up tabs), `docs/connectivity.md` (connectors, addresses, tag CSV), `docs/recording-format.md`
(session files - the contract with the NX Viewer), `docs/packet-format.md`, `docs/config.md`.

## Commands

```
pip install -e ".[dev]"                 # once
python -m nxlogger                      # app        (python -m nxlogger.sim = waveform publisher)
python -m nxlogger.sim.water_plant      # water treatment plant over MQTT (--help; --export-device wtp.json)
python -m nxlogger.sim.water_plant_gui  # the same with a window (live values, copy topic / JSON path)
python -m pytest -q                     # fast tests (Modbus / OPC UA tests start local servers)
python -m pyflakes src tests            # lint
.\packaging\build.ps1                   # EXE + installer
```

Full end-to-end check (local Modbus + OPC UA servers, alarms, recording, screenshots): see the `nxlogger-dev` skill.

## Rules

- `core/` never imports Qt or `ui/`; `comms/drivers` never import Qt. Only `paths.py` knows where files are; only `icons.py` loads icons.
- Use git branches for new versions - never copy files to `_2`, `_backup`.
- Project files are JSON (`.nxl`, `"version": 2`; `.sxl` / `.mqdaq` still open). New fields need defaults + migration in `Project.from_dict` / `normalize_view` / `normalize_tile`.
- Live data is keyed `(device_id, tag_index)`; a tag's index never changes. Variables are `("var:<id>", 0)`: they share streams, alarms, views and logging with tags.
  In formulas a tag is `<device>_<tag>` (`channel_math.identifier`).
- New connector: schema in `core/tags.DRIVERS`, driver in `comms/drivers/` (subclass `PollDriver`: open / read / close / do_write), case in `make_driver`, a Connection page in `ui/dialogs/device_pages/<name>.py` (+ `PAGES`), test against a local server in `tests/test_drivers.py`.
- Colours only from theme tokens (`T["bad"]`, `@border@` in QSS). Must look right in dark + light (Fusion style only).
- Formulas go through `channel_math.compile_expr` / `compile_formula` (AST whitelist) - never `eval` user text.
- Threads: drivers and paho callbacks only queue; decode / scale / alarms / statistics on the GUI timer; disk + REST in workers.
- The grid-layout view version is kept on branch `nx-views-grid` (not merged).
- Tabs vs pop-ups: **docs/ui-guidelines.md**. Saved items open in editor tabs (`DaqWindow.open_editor(what, ident, **ctx)`, a `_spec_<what>`: alarm, aprofile, variable, lprofile, mllog); new items are drafts until Apply. Pop-ups only for actions: subclass `common.ActionDialog`, confirm with `common.confirm` / `ask_unsaved` - never `QMessageBox.question` / `QInputDialog` directly. Background events never open a pop-up. The project, devices, tags, groups, virtual and aggregation tags, views and view components are the exception: settings pop-ups (`_project_popup`, `_device_popup`, `_tag_popup`, `_group_popup`, `_vt_popup`, `_agg_popup`, `_view_popup`, `_tile_popup`; `common.popup_form`, tabs style) and read-only in Properties.
- Connectivity document = connector list + device table only (no tags; right-click only on device rows); device settings are a pop-up (`DaqWindow._device_popup`), not a tab, and read-only in Properties (`PropTarget.readonly`). Right side: Properties dock + Runtime dock (`runtime_dock`). Tags document = tag management.
- Toolbars never move: changing text -> `ui_kit.fixed_width(w, *texts)`; disable buttons, don't hide them.
- Project tree: Connectivity = three flat categories (Protocols, Database, Web Services - `core/tags.CONN_CATEGORIES`, a driver's `category`; the Connectivity document lists connectors > devices, like Tags); Tags = six flat categories + Groups (Device, Virtual, Action, Aggregation, Memory, System, Groups - `tags_page.CATEGORIES`; the Tags document lists devices > groups > tags); at startup the Start Page is the only tab (other documents and views open as tabs when used); the project root opens the Start Page (closable, back from the house / Project menu / Ctrl+0); Quick View (live plots) is in the Project menu and no longer opens at startup.
- Views have a fixed canvas (`width` x `height`, default 1920 x 1080, `align` left / center; View pop-up > Size): it never stretches, the scroll area shows scroll bars, components stay inside it (drag, resize, Position tab). Home / New / Open / Save on the toolbar are icons only, always (`_icon_only`).
- Monitor mode (`DaqWindow.enter_monitor` / `exit_monitor`, `ui/widgets/monitor_bar.py`): full-screen views for watching - everything but the view and the slim Monitor bar hidden, the layout restored on exit (`saveState`); F11 / Esc / Page Up / Page Down; options per PC in QSettings `monitor/...`. A logger display, not a control HMI.
- System Tags (`core/system_tags.py`): fixed, read-only, key `("sys:<id>", 0)`, computed on the 500 ms stats timer and fed like a device block (streams, alarms, statistics, formulas `System_<name>`).
- Virtual tags (`core/virtual_tags.py`, `project.virtual_tags`): derived values - inputs in, value out, no state, not written at runtime. Key `("vt:<id>", 0)`, formulas `Virtual_<name>`; scalar formulas go through `channel_math.compile_scalar` (AST interpreter, text allowed). Calculated on the GUI timer after the queue drains; numbers / booleans fed like System Tags, text shown only. Signal processing stays in Variables.
- Aggregation tags (`core/aggregation.py`, `project.aggregations`): values with state over time. Key `("agg:<id>", 0)` = value, `1` = previous period (`keep_prev`); formulas `Aggregation_<name>` / `_Previous`. Updated on the GUI timer after Virtual tags; a bad / stale source pauses them. Running values (retain) go to `paths.data_dir()/aggregation_state.json` (a worker writes, every 30 s and on exit) - never into the project file.
- Groups are one project-wide tree (`Project.all_groups()`: `project.groups` + device groups + every tag's / virtual / aggregation `group`); the same path on two devices is one group. Tags > Groups lists every category's tags; renaming / deleting there changes every category (`DaqWindow._regroup`), deleting moves tags up, never deletes them.
- Put scratch files in the session scratchpad, not the repo.
