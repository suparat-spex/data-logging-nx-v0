# NX Logger

PySide6 desktop data logger (Yokogawa GA10 style, SCADA-like connectivity like Genesis64 /
FUXA): devices (Simulation, Modbus TCP / RTU, MQTT, OPC UA; OPC DA planned) hold tags; the
app shows them in free-form views (snap grid), alarms, computes statistics and records them.
Formerly SX Logger / MQDAQ (MQTT waveform logger; last flat-file version: git tag `v6-mqdaq`).

## Layout (src/nxlogger)

| Path | Owns |
|---|---|
| `core/` | **No Qt.** `tags` (DeviceCfg, TagCfg, connector schemas `DRIVERS`, ScanBlock, tag CSV), `project` (model + file migration .sxl/.mqdaq -> .nxl), `view_model` (views: components with x / y / w / h on a snap grid; grid-layout views from 2.0 convert), `stat_buffer` (statistics windows s / min / h / samples, moving / block), `ring` (live buffers), `recording` (CSV / WPK writers, Recorder, zstd/gzip), `ml_log` (AI/ML data log), `alarm_rules` (AlarmEngine, Notifier), `channel_math` (scaling, safe formulas), `analysis` (statistics, FFT, STFT, histogram), `variables` (calculated tags), `wavepacket` (ESP32 packet format) |
| `comms/drivers/` | One driver thread per device: `base` (PollDriver, message protocol), `sim`, `modbus`, `mqtt`, `opcua` (+ Browser). `comms/manager.py` starts / stops them, one queue |
| `comms/mqtt_client.py` | paho-mqtt in Qt signals - only the waveform simulator (`sim/`) uses it |
| `ui/main_window.py` | `DaqWindow`: docks, documents, host API for pages, data path, recording (big - grep, don't read whole) |
| `ui/dialogs/` | One dialog per file. Dialogs never import each other; shared helpers (SchemaForm, field_editor, ChannelPicker ...) in `common.py`. `device_pages/`: the connector sections of the one-page device pop-up, one page per connector (`base.ConnectionPage`: sections, labels, helpers, summary, validate) |
| `ui/widgets/` | `form_tab` (editor tabs: a dialog embedded as a closable document with Apply / Refresh / Close / New... / Apply & Close / Apply & New...), `connectivity` (Connectivity document: device table, import / export JSON / CSV), `tags_page` (Tags document: device tree + tag table), `property_grid` (Properties panel), `properties` (what it shows per selection), `views` (free-form ViewPage + components), `gauge`, `alarm_panel`, `variables_panel`, `mqtt_panel` (simulator) |
| `ui/theme/` | `ui_kit` (tokens `T`, QSS, AppWindow shell), `app_style` (extra QSS, Fusion), `DESIGN.md` |
| `ui/icons.py` | All icons. Lucide SVGs in `resources/icons/ui/` |
| `paths.py`, `config.py` | File locations (dev vs EXE) and `config.ini` |
| `sim/publisher.py` | ESP32 waveform generator over MQTT |

Docs: `docs/ui-guidelines.md` (editor tabs vs pop-ups), `docs/connectivity.md` (connectors, addresses, tag CSV), `docs/recording-format.md`
(session files - the contract with the NX Viewer), `docs/packet-format.md`, `docs/config.md`.

## Commands

```
pip install -e ".[dev]"                 # once
python -m nxlogger                      # app        (python -m nxlogger.sim = waveform publisher)
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
- Tabs vs pop-ups: **docs/ui-guidelines.md**. Saved items open in editor tabs (`DaqWindow.open_editor(what, ident, **ctx)`, a `_spec_<what>`: tag, group, alarm, aprofile, variable, lprofile, view, tile, mllog); new items are drafts until Apply. Pop-ups only for actions: subclass `common.ActionDialog`, confirm with `common.confirm` / `ask_unsaved` - never `QMessageBox.question` / `QInputDialog` directly. Background events never open a pop-up.
- Connectivity document = device table only (no tags); device settings are a pop-up (`DaqWindow._device_popup`), not a tab, and read-only in Properties (`PropTarget.readonly`). Right side: Properties dock + Runtime dock (`runtime_dock`). Tags document = tag management.
- Toolbars never move: changing text -> `ui_kit.fixed_width(w, *texts)`; disable buttons, don't hide them.
- Project tree: Connectivity = devices only; Tags = device > groups > tags (tag management); Quick View is in the Project menu.
- Put scratch files in the session scratchpad, not the repo.
