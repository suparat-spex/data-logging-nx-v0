# Recording format (for the NX Viewer)

Every **Record** run writes one *session folder*; the AI/ML data log writes *period files*
in date folders. Both are plain files that any tool can read. This page is the contract
between NX Logger (writer) and the NX Viewer (reader).

## Session folder

```
<log folder>/<project>_<yyyymmdd_hhmmss>/
  project.json                 session index (below) - read this first
  <profile>_<device>.csv       interval profile, number-tag or waveform device
  <profile>_<device>.wpk       interval profile, raw waveform packets (WPK format / keep raw)
  <profile>_<device>.csv       on change / trigger / batch / statistic profile
  <profile>_<device>_summary.csv   batch profile: one row per batch
  <profile>.csv                event profile: one row per event
  <profile>_Variables_<device>[ stats].csv   calculated tags (variables of older projects)
```

Each logging profile chooses its own folder ("" = the project's default) and layout
(core/log_files.py): the session folder above (older profiles, and the default for none), or
date folders (`{yyyy}`, `{yyyy}/{MM}`, `{yyyy}/{MM}/{dd}`, `{yyyy}-{MM}-{dd}` or a custom pattern)
with a new file per hour / day / week / month / N MB / run or batch:

```
<profile folder>/2026/11/05/
  Data_1_s_Energy_meters_20261105.csv     one file a day (periods are local time, [start, end))
  Data_1_s_Energy_meters_20261105_2.csv   the same name with other columns
  index.json                              the files written here (merged over sessions)
```

- A file of the same name and the same columns gets the rows **appended** (Record stopped and
  started on the same day); other columns: `_2`, `_3` ... Every N MB: `_001`, `_002` ...
- A statistics row belongs to its interval start (the 23:45 row of a daily file stays in that day).
- `index.json`: `{"app", "index_format": 1, "files": [{file, profile, type, device, format,
  interval_s, period_start, session_id}]}`.
- The session folder `<default folder>/<project>_<yyyymmdd_hhmmss>/project.json` is always
  written: `session_id`, every profile with its `folder`, and every file of the run (`file`:
  relative to the session folder, or an absolute path for files elsewhere).
- Closed files can be compressed (`.zst`, or `.gz` without zstd): read with `recording.open_any`.

Names go through `recording.safe_name` (letters, digits, `_`, `-`).

### project.json

| Key | Meaning |
|---|---|
| `app`, `session_format` | `"NX Logger"`, `2` |
| `project` | the whole project (`.nxl` content: devices, tags with scaling / units, alarms, views ...) |
| `start`, `start_unix`, `end`, `duration_s` | session time (local ISO / Unix seconds) |
| `running` | `true` while recording - a crashed session stays `true` |
| `devices` | `[{id, name, topic, driver, virtual, channels: [{index, name, unit}]}]`; `virtual` = no raw packets (number tags, variables) |
| `files` | `[{file, profile, type, device_id, device, format, interval_s}]` - which file holds what (`type`: `interval`, `change`, `trigger`, `batch`, `event` or `statistic` - `signal` in sessions before 2.3 = `interval`; `format`: `csv` or `wpk`) |
| `profiles` | the logging profiles of the run (`type`, `when`: core/log_types.py) |
| `packets`, `samples_per_channel`, `dropped`, `error` | written at Stop |

`topics` holds the same list as `devices` (name used by MQDAQ / SX Logger sessions,
`session_format` 1).

### Data CSV (`type: interval`, `change`)

```
# project: Plant A
# device: PLC (192.168.1.10:502 unit 1)
# start: 2026-10-24T17:48:07
time_unix_s,t_rel_s,Temperature [°C],Pressure [bar],Setpoint
1792838887.044801,0.000000,21.5,3,42
```

- UTF-8 with BOM (Excel shows `°C`), comma separated, `#` lines first.
- `time_unix_s`: Unix time of the value (scan time for number tags, sample time for
  waveforms); `t_rel_s`: seconds since the first row.
- One column per enabled tag, header `name [unit]` (no brackets without a unit).
  Values are **scaled engineering values**.
- Number-tag devices: one row per scan. Waveform devices: one row per sample.
- `nan` = bad quality (read error, not received, not a number).

Interval with `every_s`: the first value of each N-second period. On change: a row when any
column moved more than the deadband (absolute or % of the last written value), at least
every heartbeat - every column is written in that row.

### Trigger / batch CSV (`type: trigger`, `batch`)

```
time_unix_s,t_rel_s,run,Comp1_kW [kW],Comp1_PF
1792838887.000000,0.000000,Run 1,54.2,0.86
```

Rows only while the trigger / batch is on (`run`: Run 1, 2 ... / `batch`: the batch name, the
batch-name tag's value or the shift start "2026-11-05 06:00"). Trigger: the pre-trigger seconds
before the start and the post-trigger seconds after the stop. Batch: `<file>_summary.csv` with
`batch,start,end,duration_s,samples,<tag> Mean [unit],...` - one row per batch.

### Event CSV (`type: event`)

```
time,time_unix_s,event,Energy meters · Main_kW [kW],Virtual · Cost_THB_h [THB/h]
2026-11-05T09:14:02.120,1793873642.120,L1 H Peak demand (Demand_15min),812.4,3398.1
```

One row per event (an alarm coming in, a condition becoming true, a time of day, a Marker);
the values as shown in the app (text tags as text).

### Statistic CSV (`type: statistic`)

```
# statistics every 60 s: Mean, Min, Max
interval_start,time_unix_s,samples,Temperature Mean [°C],Temperature Min [°C],...
2026-10-24T17:48:00.000,1792838880.000,60,21.4,21.1,21.9
```

Intervals start on the clock (a 1 min interval at hh:mm:00). `samples` = values in the
interval. Statistics: Mean, Min, Max, SD, RMS, P-P, Sum, Inst (last value).

### WPK (raw waveform packets)

Binary, lossless, only for waveform devices (MQTT wavepacket mode):

```
"WPKLOG\0\1"  uint32 header length  JSON header (project, topic, channels, packet_format)
repeated:     float64 rx_unix  uint32 n  n bytes wavepacket (docs/packet-format.md)
```

Values in the packets are **before** the tag scaling - apply `scale` from `project.json`.
Read with `nxlogger.core.recording.read_wpk(path)` / `iter_wpk(path)` (also `.wpk.zst`,
`.wpk.gz`).

## AI/ML data log

```
<AI_Data>/YYYY-MM-DD/<device>/YYYY-MM-DD_HHMMSS.wpk.zst   waveform devices
<AI_Data>/YYYY-MM-DD/<device>/YYYY-MM-DD_HHMMSS.csv.zst   number-tag devices (CSV as above)
<AI_Data>/YYYY-MM-DD/events.csv                           alarms, markers, start / stop
<AI_Data>/YYYY-MM-DD/meta.json                            tags, units, scaling per device
```

Files start on the clock (period 10 s ... 1 h), no gap and no overlap; compression zstd
(or gzip). See `core/ml_log.py` for the rules.

## Compatibility

- New columns may be added at the end of a header; readers should look columns up by name.
- `session_format` changes only when a reader would misread a file.
