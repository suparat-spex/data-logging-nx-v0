# Alarm model (from Yokogawa GA10, IM04L65B01-01EN) and how MQDAQ maps it

## GA10 behaviour
- **4 alarm levels per tag** (Level 1-4), each with its own type, setpoint and hysteresis.
- Types: **H** high, **L** low, **dH / dL** difference high/low (vs another channel),
  **RH / RL** rate-of-change rise/fall over an interval, **tH / tL** delay high/low
  (condition must hold for a time), **D** data loss / communication error.
- **Alarm ACK**: new alarms blink until acknowledged; ACK all / per group / per tag.
- **Hold (latch)**: a cleared alarm stays shown until acknowledged.
- Optional **sound**, **popup**, **bring window to front**.
- **Alarm log / summary**: Date, Time, Tag, Level, Type, Status ON/OFF (and ACK),
  up to 1000 entries, clear log.
- Tag overview with 4 level indicators; group overview.
- **Recording triggered** by alarm (start/stop on alarm or level).
- **Mail on alarm** per level, ON / OFF / ON-OFF.
- **Report data**: ave, max, min, sum, inst per hourly / daily / monthly interval.

## MQDAQ mapping (daq_alarms.py, v6)
| GA10 | MQDAQ |
|---|---|
| Level 1-4 | `alarm["levels"][0..3]`; colours L1 bad (red), L2 accent (orange), L3 warn (yellow), L4 info (blue) |
| H / L | tested on block max / min (spikes inside a packet still trip) |
| dH / dL | `alarm["ref"]` reference channel, value = last − ref last |
| RH / RL | block-mean slope in units/s |
| tH / tL | any type + `delay_s` (on-delay) |
| D | `AlarmEngine.tick()`: no data for setpoint seconds |
| ACK / blink / hold | `AlarmEngine.ack()`, `latch`, `AlarmPage` blink timer |
| Alarm log 1000 | `AlarmEngine.log` deque, Alarm log tab, CSV export |
| Mail / notification | Notification profiles: log, popup, REST webhook, MQTT, e-mail (planned) |
| Report data | Statistic logging profiles (mean/min/max/SD/RMS/P-P/sum/inst per interval) |

## Not yet implemented (candidates)
Recording triggered by alarm (with pre-trigger), alarm shelving, groups / group
overview, SMTP e-mail, alarm-flood statistics (ISA-18.2), audit trail of ACKs by user.
