---
name: nxlogger-dev
description: Use when developing NX Logger (formerly SX Logger / MQDAQ), the PySide6 tag data-logger in this project (src/nxlogger - core / comms drivers / ui / dialogs / widgets / icons / config / packaging) - adding connectors (Modbus, MQTT, OPC UA ...), tags, the Connectivity document, Properties panel, views and components, statistics windows, alarms, logging profiles, theming, icons, config.ini, building the EXE/installer, or running and testing the app.
---

# NX Logger development

NX Logger is a Yokogawa GA10 style data logger with SCADA-like connectivity (Genesis64 /
FUXA): devices (Simulation, Modbus TCP / RTU, MQTT, OPC UA) hold tags; views, alarms,
statistics and recording work on tags. ESP32 waveform nodes still connect as MQTT devices in
wavepacket mode (array tags). Aim for professional data-logger behaviour (see
`references/alarms-ga10.md`).
`CLAUDE.md` has the layout; this skill has the how-to.

## Where things go

| Task | Where |
|---|---|
| Logic without UI (packet, scaling, alarm rule, statistic) | `core/` - no Qt, add a test in `tests/` |
| New connector / protocol | schema in `core/tags.DRIVERS`, `comms/drivers/<name>.py` (PollDriver), `make_driver`, `ui/dialogs/device_pages/<name>.py` (ConnectionPage: SECTIONS, LABELS / CHOICES, summary, validate), test with a local server in `tests/test_drivers.py`, docs/connectivity.md |
| New view component | `TILE_DEFAULTS` / `KIND_TITLE` / `DEFAULT_SIZE` in `core/view_model.py`, Tile subclass + `ADD_MENUS` in `ui/widgets/views.py`, fields in `ui/widgets/properties.tile_target`, tab in `tile_dialog.py` |
| Something the Properties panel edits | a `*_target()` in `ui/widgets/properties.py` (fields = `core/tags.F` dicts) |
| New pop-up (an action) | subclass `common.ActionDialog` (verb button), confirm with `common.confirm` - see docs/ui-guidelines.md |
| Settings pop-up (device, tag, group ...) | a tabbed QDialog + `common.popup_form()`; style "tabs" (default) or "sections" via `common.POPUP_STYLE` - **references/popup-styles.md** (both styles, layout rules, how to switch) |
| New editable item in the Project tree | a form in `ui/dialogs/` (QDialog with `_ok()` + result / apply; tab pages become sections), a `_spec_<what>()` in `main_window.py`, open it with `open_editor(what, ident)` - no `exec()` pop-up |
| New dialog | new file `ui/dialogs/<name>_dialog.py`; open it from the page / window that needs it |
| New document page or panel | `ui/widgets/` |
| New icon | save the Lucide SVG to `src/nxlogger/resources/icons/ui/<name>.svg`, use `make_icon("<name>", T[...])` or `self.reg_icon(widget, name, token)` |
| New setting a person edits by hand | `resources/config.default.ini` + `docs/config.md`, read via `config.get()` |
| New file location | `paths.py` (works from source and in the EXE) |
| Project file field | `core/project.py` dataclass default + migration in `Project.from_dict` |

Pages talk to the window through a small **host API** (duck-typed methods on
`DaqWindow`, listed in each widget module's docstring). Keep modules decoupled: add a
host method rather than importing `main_window`.

## UI design

The look comes from the shared **spex-ui** skill (`~/.claude/skills/spex-ui`, the
master copy of the design system). Before UI work, read `src/nxlogger/ui/theme/DESIGN.md`
and the spex-ui `references/app-patterns.md`. This project's `ui_kit.py` loads icons
through `nxlogger.ui.icons` instead of an inline `SVG_BODY` dict - keep that when
syncing reusable improvements back to spex-ui.

## Rules

- **Versions = git**, not file copies. Work on a branch; bump `__version__` in
  `src/nxlogger/__init__.py` for a release (EXE properties, installer name and About read it).
- **Project files are JSON** (`.nxl`, version 2; `.sxl` / `.mqdaq` still open). New fields need
  defaults and a migration in `Project.from_dict` so older files open (v1 topics -> MQTT
  wavepacket devices with the same id; grid-layout views -> free-form; `window_s` -> `win_n`;
  TDMS profiles -> CSV; v5 `alarm_lo/hi` -> Level 1/2).
- **Live data path:** driver thread -> `DriverManager.q` -> `_drain_and_draw` -> `_ingest`
  (scale, ring, alarms, StatHub, variables, recorder, AI log). A scan is a one-sample
  `ScanBlock`; never touch widgets from a driver.
- **Settings:** QSettings under SPEX / "NX Logger" (window, last project, theme); connection
  settings live in the project's devices; `config.ini [mqtt]` is the default broker for new
  MQTT devices. `ui/app.py` copies MQDAQ v6 settings once.
- **Theming:** colours only from tokens (`T["bad"]`, `@border@` in QSS) - no hex in
  widgets. Channel colours (`COLORS`) and the black plot area are data, not UI.
  The app always uses the Fusion style; both themes must look right.
  `app_style._FUSION_DROP` strips QSS rules Fusion draws itself.
- **Qt pitfalls:** never rebuild a tree/list inside its `itemChanged` / click handler -
  defer with `QTimer.singleShot(0, ...)` (it segfaults otherwise). Block signals while
  filling trees. Do heavy work only for the visible document.
- **Threads:** paho callback thread only queues payloads; decoding, scaling, alarms run
  on the GUI timer (`_drain_and_draw`, 33 ms; `_update_stats`, 500 ms). Disk writes
  and REST calls run in worker threads - acquisition must never block.
- **Alarms** go to the Alarms tab, status bar / toolbar count and the Log panel - not
  to the top banners (banners are for connection / recording problems only).
- **Formulas** use the AST whitelist in `core/channel_math.compile_formula` - never `eval`
  user text. Project files may come from other people.
- **Edit mode in views** shows mock-up data (no live updates) for speed.
- **EXE:** never locate files with `__file__` outside `paths.py`; never write next to
  the program (Program Files is read-only) - user data goes to `paths.data_dir()`.
- Match the surrounding code: dense, comment the *why*, `label()`, `set_prop()`, docBar /
  subBar toolbars, `QTreeWidget` tables with column chooser where useful.
- Responsive: must work at 1280x800 laptops (compact / medium / wide modes).

## Run and test

Windows, Python 3.14 (`python`), installed with `pip install -e ".[dev]"`.

- App: `python -m nxlogger`; publisher: `python -m nxlogger.sim`.
- Fast checks (seconds, no broker): `python -m pytest -q` and `python -m pyflakes src tests`.
- **Offscreen end-to-end test** (local Modbus TCP + OPC UA servers, simulator, alarms, logging
  profiles, statistics windows, Modbus write, screenshots of every tab; `--mqtt` adds an ESP32
  wavepacket device on the public broker):

  ```bash
  QT_QPA_PLATFORM=offscreen PYTHONIOENCODING=utf-8 python -u .claude/skills/nxlogger-dev/scripts/offscreen_test.py \
      --theme dark --out <scratchpad>/sxl_test        # also: --theme light
  ```

  It prints a summary and exits non-zero on failure. Then **Read the PNGs** in `--out`
  to check the layout (text renders as boxes offscreen - no fonts - so check layout,
  colours and icons). For UI changes, always look before reporting done.
- Build: `.\packaging\build.ps1` (PowerShell) -> `dist\NX Logger\NX Logger.exe` and,
  with Inno Setup 6 installed, `dist\NX-Logger-Setup-<version>.exe`.
- Put test output and throwaway scripts in the session scratchpad, not the project.

Known test pitfalls:
- With `--mqtt`, the public broker (`broker.emqx.io`) is slow at times: "no data" on the ESP
  device. Re-run once before suspecting the code. The default run needs no internet.
- `PYTHONIOENCODING=utf-8` is needed on Windows (°C in the output).
- Close modal dialogs in tests (`QMessageBox` blocks the offscreen event loop).

## Report

Tell the user what changed per file, what the tests showed (numbers, files written),
anything not verified, and suggestions that would make it more professional.
