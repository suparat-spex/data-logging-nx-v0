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
noise`). Unknown keys are ignored. Export a device to get a template.

## Files written before NX Logger

`.sxl` (SX Logger) and `.mqdaq` (MQDAQ) projects open and are converted: every MQTT topic
becomes an MQTT device in wavepacket mode with the same id (alarms, variables, views and
logging profiles keep working), using the broker last used in the old connection form
(or `config.ini [mqtt]`). Save as `.nxl` to keep them.
