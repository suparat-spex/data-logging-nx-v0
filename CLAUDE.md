# NX Logger

PySide6 desktop data logger: ESP32 DAQ nodes publish binary waveform blocks over MQTT;
the app plots, alarms and records them (FlexLogger / Yokogawa GA10 style).
Formerly MQDAQ (last flat-file version: git tag `v6-mqdaq`).

## Layout (src/nxlogger)

| Path | Owns |
|---|---|
| `core/` | **No Qt.** `wavepacket` (packet format), `project` (model + file migration), `ring` (live buffers), `recording` (CSV/TDMS/WPK writers, Recorder, zstd/gzip), `ml_log` (AI/ML data log: 24/7 clock-aligned period files in date folders), `alarm_rules` (AlarmEngine, Notifier), `channel_math` (scaling, safe formulas), `analysis` (statistics, FFT, STFT, histogram), `variables` (calculated channels, VariableEngine), `view_model` (view / tile dicts) |
| `comms/mqtt_client.py` | paho-mqtt wrapped in Qt signals |
| `ui/main_window.py` | `DaqWindow`: docks, documents, MQTT routing, recording, status (big - grep, don't read whole) |
| `ui/dialogs/` | One dialog per file. Dialogs never import each other; shared helpers in `common.py` |
| `ui/widgets/` | `views` (view tiles + ViewPage), `gauge` (painted dial), `alarm_panel` (Alarms document), `variables_panel` (Variables document), `mqtt_panel` |
| `ui/theme/` | `ui_kit` (tokens `T`, QSS, AppWindow shell), `app_style` (extra QSS, Fusion), `DESIGN.md` |
| `ui/icons.py` | All icons. Lucide SVGs in `resources/icons/ui/` |
| `paths.py`, `config.py` | File locations (dev vs EXE) and `config.ini` |
| `sim/publisher.py` | Signal generator (stand-in for the ESP32) |

## Commands

```
pip install -e ".[dev]"                 # once
python -m nxlogger                      # app        (python -m nxlogger.sim = publisher)
python -m pytest -q                     # fast tests, no broker
python -m pyflakes src tests            # lint
.\packaging\build.ps1                   # EXE + installer
```

Full end-to-end check (broker, alarms, recording, screenshots): see the `nxlogger-dev` skill.

## Rules

- `core/` never imports Qt or `ui/`. Only `paths.py` knows where files are; only `icons.py` loads icons.
- Use git branches for new versions - never copy files to `_2`, `_backup`.
- Project files are JSON (`.sxl`; `.mqdaq` still opens). New fields need defaults + migration in `Project.from_dict`.
- Colours only from theme tokens (`T["bad"]`, `@border@` in QSS). Must look right in dark + light (Fusion style only).
- Formulas go through `channel_math.compile_expr` / `compile_formula` (AST whitelist) - never `eval` user text.
- Variables are channels keyed `("var:<id>", 0)`: they share streams, alarms, views and logging with real channels.
  In formulas a channel is `<topic>_<channel>` (`channel_math.identifier`).
- Threads: paho callback only queues; decode/scale/alarms on the GUI timer; disk + REST in workers.
- Put scratch files in the session scratchpad, not the repo.
