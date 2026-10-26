# Connectivity: devices and tags

A **device** is one connection (one driver thread). A **tag** is one value of a device.
Tags can be put in **groups** (folders, `Boiler/Pumps`) and have every channel property:
name, unit, colour, description, scaling (linear, two-point, formula), decimals, Y range.
In formulas a tag is `<device>_<tag>` (e.g. `PLC_Temperature`).

Number tags are read every **scan interval** of the device (100 ms ... 1 min). One scan
is one row in the data CSV. Writable tags (`Access = RW`) can be written from the tag
table (right-click → Write value) or the Properties panel; the value is sent as is
(before scaling).

## Connectors

| Connector | Settings | Tag address |
|---|---|---|
| **Simulation** | - | signal (sine, square, triangle, sawtooth, ramp, random, constant, counter), amplitude, frequency, offset, noise |
| **Modbus TCP** | IP address, port (502), unit ID, timeout, max registers per read, 1-based addresses | area, address, data type, word order, bit, unit ID override |
| **Modbus RTU** | serial port (COM3, /dev/ttyUSB0), baud rate, parity, stop bits, data bits, unit ID ... | as Modbus TCP |
| **MQTT** | broker, port, transport (TCP / TLS / WebSocket), login, client ID, payload mode, QoS | number tags: topic + JSON path |
| **OPC UA client** | endpoint URL, user / password, timeout | node ID (`ns=2;s=Tank.Level`, `ns=3;i=1001`) - or Browse... |
| OPC DA | planned - classic OPC DA needs Windows COM; use an OPC UA server or wrapper meanwhile | item ID |

### Modbus

- Areas: holding register (4x), input register (3x), coil (0x), discrete input (1x).
- Data types: int16, uint16, int32, uint32, int64, uint64, float32, float64, bool.
- Word order of 32 / 64-bit values: `ABCD` (big endian, Modbus standard), `CDAB` (word
  swap - common on PLCs), `BADC` (byte swap), `DCBA` (little endian).
- `bit` ≥ 0 reads one bit of a status word (writing a bit is read-modify-write).
- Neighbouring tags of the same unit and area are read in one request (up to *max
  registers per read*, gaps ≤ 4 registers). A Modbus exception (illegal address) marks only
  those tags bad; a timeout reconnects the device (2 s, 4 s ... 10 s).
- *1-based addresses*: on = address 1 is the first register (`40001` style lists).

### MQTT

- **Number tags** (payload mode *JSON / plain value*): each tag subscribes its topic
  (wildcards `+` / `#` allowed). The payload is a number (`21.5`) or JSON with the value at
  the JSON path: `data.temp`, `values[2]`, `a.b[0].c`; `{"value": 1.2, ...}` works with an
  empty path. The latest value is sampled every scan; a tag that never received a value
  is bad quality.
- **Waveform packets** (payload mode *wavepacket*): one topic of ESP32 DAQ blocks
  ([packet-format.md](packet-format.md)); tags are the packet channels CH1..CH8 (array
  tags) - FFT, spectrogram and raw `.wpk` recording work on them.
- Alarm notifications of kind *MQTT publish* go out through the first connected MQTT
  device.

### OPC UA

All tag nodes are read in one Read request per scan. Booleans read as 0 / 1, numbers as
they are; strings and structures are bad quality. **Browse...** lists the server's
address space (Objects folder down); tick variables to add them as tags.

## Quality

| Quality | Meaning |
|---|---|
| Good | the last value is valid |
| Stale | no new value for 3 scan intervals (at least 5 s) |
| Bad | read error / not a number - logged as `nan`, alarms ignore it |
| Waiting | the device runs but sent nothing yet |

## Tag list CSV (import / export)

```
device,group,name,unit,description,enabled,access,decimals,address
PLC,Boiler,Temperature,°C,TT-101,1,R,1,area=holding;addr=0;dtype=float32;order=CDAB
PLC,Boiler,Setpoint,°C,,1,RW,1,area=holding;addr=10;dtype=int16
```

`address` holds the connector's address fields as `key=value;key=value` (keys as in the
table above: `area addr dtype order bit unit`, `topic path`, `node`, `wave amp freq offset
noise`). Export a device to get a template.

Tags > *Import...* (pop-up, like Import devices) imports into the selected device: a CSV tag
list or a device JSON export (Connectivity's or Tags' JSON - the device with the same name or
the only one; same connector). The file is checked first (`core/tags.read_tags_file`): an
unknown column or address key, a value that does not parse, a missing name, access other than
R / RW or a name used twice lists every problem with its line and Import stays disabled. In a
CSV naming several devices only this device's rows count. Tick the tags; a name that exists is
added as a copy, replaced (keeps its index: alarms, views and logging stay) or skipped.
*Export...* (pop-up, like Export devices): tick tags (grouped by device; from *All tags* every
device, the selected rows start ticked), CSV tag list or JSON (the devices with those tags).

## Connectivity and Tags documents

**Connectivity** (Ctrl+2) is the device table: name, connector, address, scan, tag count,
status and an *Enabled* indicator (read-only; set in the device settings pop-up - a disabled
device is not connected or logged). Toolbar:
Add ▾ (per connector), Edit, Duplicate, Delete, Import..., Export..., Columns ▾ (show / hide
columns, remembered; also right-click the header). Select a row: the Properties panel shows
the device read-only with one *Edit...* button; double-click (or Enter, Edit) opens the **device settings pop-up** (Save / Cancel);
Del deletes. Rows can be multi-selected. Right-click a row: Edit device, Enable / Disable
device, Duplicate, Export..., Delete device (Enable / Disable, Export and Delete act on every
selected row; Edit and Duplicate on one).
Other device settings change only in the pop-up.
The device settings pop-up is one page: General (name, description, scan interval, enabled)
and the connector's sections. The connector is chosen in Add ▾ and cannot change later (the
tags' addresses depend on it). **Test connection** connects once with the values as typed -
the real driver, in the background - and shows the time and how many tags returned a value,
or the error. Nothing is saved or started by a test.
**Tags** (Ctrl+6) is tag management for one category at a time - the one clicked in the Project
tree (Tags > Device, Virtual, Action, Aggregation, Memory, System; flat). Left: the category as a
band (like a Properties section) and its list - Device: *All tags* and the Connectivity devices
with their groups (devices are only picked here; add / edit them in Connectivity); System:
*All tags*, Clock, App, Recording, Alarms, Devices > one group per device. Right: the tag table.
Toolbar (like Connectivity, fixed buttons, disabled when they do not apply; Add and Edit open
settings pop-ups, Cancel · *Add tag* / *Add group* / *Save*; tags are read-only in Properties): Add ▾ (Tag...,
Tags..., Group..., Browse OPC UA server...), Edit, Delete | Import..., Export... | Columns ▾
(show / hide, remembered; also right-click the header), filter. Del / Enter on the table
delete / edit.

## Tag categories

| Category | What it does | State |
|---|---|---|
| **Device** | acquires the data: the tags of the devices | ready |
| **Virtual** | calculates from it | planned - calculated tags are in Variables for now |
| **Action** | responds to it: logic, outputs, triggers | planned |
| **Aggregation** | summarizes it statistically | planned |
| **Memory** | values set by the user: setpoints, constants | planned |
| **System** | read-only values of the app | ready |

**System Tags** (`core/system_tags.py`) are fixed: every project has them and they cannot be
added, edited or deleted (the Properties panel shows them read-only). They are computed twice a
second. Number / boolean ones work like any tag in views, alarms, statistics and formulas
(`System_<name>`, e.g. `System_Hour`); text ones (date, folder) are shown only. Not recorded yet.

- Clock: `DateTime`, `Date`, `Time` (text), `Year`, `Month`, `Day`, `Hour`, `Minute`, `Second`,
  `Weekday` (1 = Monday), `UnixTime`
- App: `Uptime` (s), `ProjectName`, `AppVersion` (text), `DiskFree` (GB, log folder drive), `DevicesOnline`
- Recording: `Recording` (1 / 0), `RecFolder` (text), `RecSize` (MB), `RecDuration` (s)
- Alarms: `AlarmsActive`
- Per device, `<device>.<field>`: `Connected` (1 / 0), `State`, `Quality`, `LastUpdate` (text),
  `Age` (s since the last value), `Errors`

Keys are `("sys:<id>", 0)`; per-device ids use the device id (`dev.<id>.Connected`), so renaming
a device keeps its alarms and views. A formula that mixes device tags and System Tags runs at
the device rate.

## Device list (import / export)

**Export...** opens a pop-up: tick the devices (the selected rows are ticked; none selected =
all) and choose the format, then where to save:

- **JSON** - devices with settings, groups and tags (`{"format": "nxlogger-devices",
  "version": 1, "devices": [...]}`). Import also reads a `.nxl` project's devices.
- **CSV** - one row per device, no tags:

```
name,driver,enabled,scan_ms,description,settings
PLC,modbus_tcp,1,500,Line 1,host=10.0.0.5;port=502;unit=1;timeout_s=1.0;max_block=100;one_based=False
```

`driver` is the connector key (`sim modbus_tcp modbus_rtu mqtt opcua opcda`) or its label
(`Modbus TCP`); `settings` uses the keys of the device form, unknown keys are ignored.
**Passwords are never exported** - enter them after the import.

**Import...** opens a pop-up: choose the file and it is **checked first** - wrong format or
header, unknown connector or setting, a value that is not a number, an empty or duplicate
name. Every problem is listed with its line and *Import* stays disabled: nothing is imported
from a file that fails. A valid file lists its devices to tick; when a name exists: *Add as a copy*,
*Replace* (keeps the device id, so alarms, views and logging keep working; a CSV replace
keeps the old tags and passwords) or *Skip*. Imported devices are not started.

## Files written before NX Logger

`.sxl` (SX Logger) and `.mqdaq` (MQDAQ) projects open and are converted: every MQTT topic
becomes an MQTT device in wavepacket mode with the same id (alarms, variables, views and
logging profiles keep working), using the broker last used in the old connection form
(or `config.ini [mqtt]`). Save as `.nxl` to keep them.
