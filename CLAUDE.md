# NX Logger

PySide6 desktop data logger (Yokogawa GA10 style, SCADA-like connectivity like Genesis64 /
FUXA): devices (Simulation, Modbus TCP / RTU, MQTT, OPC UA; OPC DA planned) hold tags; the
app shows them in grid views, alarms, computes statistics and records them.
Formerly SX Logger / MQDAQ (MQTT waveform logger; last flat-file version: git tag `v6-mqdaq`).

## Layout (src/nxlogger)

| Path | Owns |
|---|---|
| `core/` | **No Qt.** `tags` (DeviceCfg, TagCfg, connector schemas `DRIVERS`, ScanBlock, tag CSV), `project` (model + file migration .sxl/.mqdaq -> .nxl), `view_model` (grid views: rows / cols / spans, editing helpers), `stat_buffer` (statistics windows s / min / h / samples, moving / block), `ring` (live buffers), `recording` (CSV / WPK writers, Recorder, zstd/gzip), `ml_log` (AI/ML data log), `alarm_rules` (AlarmEngine, Notifier), `channel_math` (scaling, safe formulas), `analysis` (statistics, FFT, STFT, histogram), `variables` (calculated tags), `wavepacket` (ESP32 packet format) |
| `comms/drivers/` | One driver thread per device: `base` (PollDriver, message protocol), `sim`, `modbus`, `mqtt`, `opcua` (+ Browser). `comms/manager.py` starts / stops them, one queue |
| `comms/mqtt_client.py` | paho-mqtt in Qt signals - only the waveform simulator (`sim/`) uses it |
| `ui/main_window.py` | `DaqWindow`: docks, documents, host API for pages, data path, recording (big - grep, don't read whole) |
| `ui/dialogs/` | One dialog per file. Dialogs never import each other; shared helpers (SchemaForm, ChannelPicker ...) in `common.py` |
| `ui/widgets/` | `connectivity` (devices + tag table), `property_grid` (Properties panel), `properties` (what it shows per selection), `views` (grid ViewPage + components), `gauge`, `alarm_panel`, `variables_panel`, `mqtt_panel` (simulator) |
| `ui/theme/` | `ui_kit` (tokens `T`, QSS, AppWindow shell), `app_style` (extra QSS, Fusion), `DESIGN.md` |
| `ui/icons.py` | All icons. Lucide SVGs in `resources/icons/ui/` |
| `paths.py`, `config.py` | File locations (dev vs EXE) and `config.ini` |
| `sim/publisher.py` | ESP32 waveform generator over MQTT |

Docs: `docs/connectivity.md` (connectors, addresses, tag CSV), `docs/recording-format.md`
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
- New connector: schema in `core/tags.DRIVERS`, driver in `comms/drivers/` (subclass `PollDriver`: open / read / close / do_write), case in `make_driver`, test against a local server in `tests/test_drivers.py`.
- Colours only from theme tokens (`T["bad"]`, `@border@` in QSS). Must look right in dark + light (Fusion style only).
- Formulas go through `channel_math.compile_expr` / `compile_formula` (AST whitelist) - never `eval` user text.
- Threads: drivers and paho callbacks only queue; decode / scale / alarms / statistics on the GUI timer; disk + REST in workers.
- Views never drop a component: `view_model.clamp_tiles` moves it to a free cell (adds a row).
- Put scratch files in the session scratchpad, not the repo.
