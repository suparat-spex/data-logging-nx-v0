# Recording format (for the NX Viewer)

Every **Record** run writes one *session folder*; the AI/ML data log writes *period files*
in date folders. Both are plain files that any tool can read. This page is the contract
between NX Logger (writer) and the NX Viewer (reader).

## Session folder

```
<log folder>/<project>_<yyyymmdd_hhmmss>/
  project.json                 session index (below) - read this first
  <profile>_<device>.csv       data profile, number-tag or waveform device
  <profile>_<device>.wpk       data profile, raw waveform packets (WPK format / keep raw)
  <profile>_<device>.csv       statistic profile (one row per interval)
  <profile>_Variables_<device>[ stats].csv   calculated tags (variables)
```

Names go through `recording.safe_name` (letters, digits, `_`, `-`).

### project.json

| Key | Meaning |
|---|---|
| `app`, `session_format` | `"NX Logger"`, `2` |
| `project` | the whole project (`.nxl` content: devices, tags with scaling / units, alarms, views ...) |
| `start`, `start_unix`, `end`, `duration_s` | session time (local ISO / Unix seconds) |
| `running` | `true` while recording - a crashed session stays `true` |
| `devices` | `[{id, name, topic, driver, virtual, channels: [{index, name, unit}]}]`; `virtual` = no raw packets (number tags, variables) |
| `files` | `[{file, profile, type, device_id, device, format, interval_s}]` - which file holds what (`type`: `signal` or `statistic`; `format`: `csv` or `wpk`) |
| `profiles` | the logging profiles of the run |
| `packets`, `samples_per_channel`, `dropped`, `error` | written at Stop |

`topics` holds the same list as `devices` (name used by MQDAQ / SX Logger sessions,
`session_format` 1).

### Data CSV (`type: signal`)

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
