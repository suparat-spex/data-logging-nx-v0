"""
water_plant.py - water treatment plant simulator: publishes realistic, linked process values
over MQTT as JSON (one message per plant area), for demos and testing NX Logger's MQTT
connector, alarms, statistics and views. No Qt.

  raw water    intake flow, turbidity, pH, water temperature, raw tank level
  pumps        2 high-lift pumps: run, speed, motor current, discharge pressure, vibration
               (pump 2 starts at high demand; now and then pump 1 trips for a few minutes)
  dosing       alum (coagulant) and chlorine dose, following raw turbidity / residual
  filters      2 sand filters: differential pressure rises until a backwash resets it
  treated      outlet flow, turbidity, pH, free chlorine, conductivity, clear-well level, pressure
  power        3-phase power meter: V / A per phase, kW, kvar, kVA, PF, Hz, THD, kWh counter
  environment  pump house and control room temperature / humidity, MCC panel temperature
  kpi          specific energy (kWh/m3), energy per day, chemical cost rate

Demand follows a day profile (morning and evening peaks); --speed runs the plant clock faster
(e.g. 60 = one plant hour per real minute) so day profiles and backwashes show up quickly.

Run:
  python -m nxlogger.sim.water_plant_gui                   window: live values, copy topic / JSON path
  python -m nxlogger.sim.water_plant                       broker from config.ini [mqtt]
  python -m nxlogger.sim.water_plant --base demo/wtp1 --interval 1 --speed 60
  python -m nxlogger.sim.water_plant --export-device wtp.json   NX Logger device + tags (Connectivity > Import...)
  python -m nxlogger.sim.water_plant --export-tags wtp.csv      tag list (Tags > Import...)
  python -m nxlogger.sim.water_plant --flat                one topic per value (plain numbers)

Topics: <base>/<area>  payload {"ts": <unix s>, "<key>": value, ...}   (--flat: <base>/<area>/<key>)
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import uuid

# (area, key, name, unit, decimals, description) - the model's outputs and the exported tags
SIGNALS = [
    ("raw", "flow", "Intake flow", "m³/h", 1, "Raw water intake flow"),
    ("raw", "turbidity", "Raw turbidity", "NTU", 2, "Raw water turbidity (rain events raise it)"),
    ("raw", "ph", "Raw pH", "pH", 2, "Raw water pH"),
    ("raw", "temp", "Water temperature", "°C", 1, "Raw water temperature"),
    ("raw", "level", "Raw tank level", "m", 2, "Raw water tank level (0 - 5 m)"),
    ("pumps", "p1_run", "Pump 1 run", "", 0, "High-lift pump 1 running (1 / 0) - trips now and then"),
    ("pumps", "p1_speed", "Pump 1 speed", "Hz", 1, "Pump 1 VFD output frequency"),
    ("pumps", "p1_current", "Pump 1 current", "A", 1, "Pump 1 motor current"),
    ("pumps", "p1_vibration", "Pump 1 vibration", "mm/s", 2, "Pump 1 bearing vibration velocity (RMS)"),
    ("pumps", "p2_run", "Pump 2 run", "", 0, "High-lift pump 2 running (1 / 0) - duty assist at high demand"),
    ("pumps", "p2_speed", "Pump 2 speed", "Hz", 1, "Pump 2 VFD output frequency"),
    ("pumps", "p2_current", "Pump 2 current", "A", 1, "Pump 2 motor current"),
    ("pumps", "p2_vibration", "Pump 2 vibration", "mm/s", 2, "Pump 2 bearing vibration velocity (RMS)"),
    ("pumps", "discharge_pressure", "Discharge pressure", "bar", 2, "Common pump discharge pressure"),
    ("dosing", "alum_dose", "Alum dose", "mg/L", 1, "Coagulant (alum) dose"),
    ("dosing", "chlorine_dose", "Chlorine dose", "mg/L", 2, "Chlorine dose (keeps the free chlorine near 0.8 mg/L)"),
    ("dosing", "alum_tank", "Alum tank level", "%", 1, "Coagulant day tank level (refilled at 15 %)"),
    ("filters", "f1_dp", "Filter 1 diff. pressure", "bar", 3, "Filter 1 differential pressure (backwash at 1.8 bar)"),
    ("filters", "f1_backwash", "Filter 1 backwash", "", 0, "Filter 1 in backwash (1 / 0)"),
    ("filters", "f2_dp", "Filter 2 diff. pressure", "bar", 3, "Filter 2 differential pressure (backwash at 1.8 bar)"),
    ("filters", "f2_backwash", "Filter 2 backwash", "", 0, "Filter 2 in backwash (1 / 0)"),
    ("treated", "flow", "Outlet flow", "m³/h", 1, "Treated water flow to the network"),
    ("treated", "turbidity", "Treated turbidity", "NTU", 3, "Treated water turbidity (limit 1 NTU)"),
    ("treated", "ph", "Treated pH", "pH", 2, "Treated water pH (6.5 - 8.5)"),
    ("treated", "chlorine", "Free chlorine", "mg/L", 2, "Free chlorine residual (0.2 - 1.0 mg/L)"),
    ("treated", "conductivity", "Conductivity", "µS/cm", 0, "Treated water conductivity"),
    ("treated", "level", "Clear-well level", "m", 2, "Clear-well (treated water) level (0 - 6 m)"),
    ("treated", "pressure", "Outlet pressure", "bar", 2, "Network outlet pressure"),
    ("power", "v_l1", "Voltage L1", "V", 1, "Phase voltage L1-N"),
    ("power", "v_l2", "Voltage L2", "V", 1, "Phase voltage L2-N"),
    ("power", "v_l3", "Voltage L3", "V", 1, "Phase voltage L3-N"),
    ("power", "i_l1", "Current L1", "A", 1, "Phase current L1"),
    ("power", "i_l2", "Current L2", "A", 1, "Phase current L2"),
    ("power", "i_l3", "Current L3", "A", 1, "Phase current L3"),
    ("power", "kw", "Active power", "kW", 1, "Total active power"),
    ("power", "kvar", "Reactive power", "kvar", 1, "Total reactive power"),
    ("power", "kva", "Apparent power", "kVA", 1, "Total apparent power"),
    ("power", "pf", "Power factor", "", 3, "Total power factor"),
    ("power", "hz", "Frequency", "Hz", 2, "Grid frequency"),
    ("power", "thd_i", "Current THD", "%", 1, "Current total harmonic distortion (VFDs)"),
    ("power", "kwh", "Energy", "kWh", 1, "Active energy counter (import)"),
    ("environment", "pumphouse_temp", "Pump house temperature", "°C", 1, "Pump house air temperature"),
    ("environment", "pumphouse_rh", "Pump house humidity", "%RH", 1, "Pump house relative humidity"),
    ("environment", "control_temp", "Control room temperature", "°C", 1, "Control room temperature (air-conditioned)"),
    ("environment", "control_rh", "Control room humidity", "%RH", 1, "Control room relative humidity"),
    ("environment", "mcc_temp", "MCC panel temperature", "°C", 1, "Motor control centre panel temperature"),
    ("kpi", "specific_energy", "Specific energy", "kWh/m³", 3, "Energy per cubic metre treated"),
    ("kpi", "energy_today", "Energy today", "kWh", 1, "Energy since local midnight (plant clock)"),
    ("kpi", "chem_cost", "Chemical cost rate", "THB/h", 1, "Alum + chlorine cost per hour"),
]
AREAS = {"raw": "Raw water", "pumps": "Pumps", "dosing": "Dosing", "filters": "Filters", "treated": "Treated water",
         "power": "Power meter", "environment": "Environment", "kpi": "KPI"}


def _bump(h: float, centre: float, width: float) -> float:
    d = (h - centre + 12) % 24 - 12                  # distance on the 24 h circle
    return math.exp(-(d / width) ** 2)


class WaterPlant:
    """The process model. step(dt, t) advances the plant by dt plant-seconds to plant time t
    (Unix s) and returns {area: {key: value}}; values are linked (flow -> pumps -> power ...)."""

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.raw_level, self.cw_level = 3.8, 4.2
        self.raw_turb_event = 0.0                    # extra turbidity from a rain event, decays
        self.f_dp = [0.35, 1.05]                     # filters start at different points of the run
        self.f_bw = [0.0, 0.0]                       # backwash time left (s)
        self.p1_trip = 0.0                           # trip time left (s)
        self.p2_on = False
        self.vib_wear = [0.0, 0.0]
        self.alum_tank = 72.0
        self.kwh = 152_340.0
        self.kwh_day, self.day = 0.0, None
        self.chlorine = 0.8
        self.drift = {}                              # slow random walks

    # ---- helpers
    def _walk(self, key: str, sigma: float, lim: float, dt: float) -> float:
        """A bounded random walk (slow sensor / process drift), mean-reverting to 0."""
        v = self.drift.get(key, 0.0)
        v += -v * 0.002 * dt + self.rng.gauss(0, sigma) * math.sqrt(max(dt, 0.01))
        v = max(-lim, min(lim, v))
        self.drift[key] = v
        return v

    def _n(self, sigma: float) -> float:
        return self.rng.gauss(0, sigma)

    # ---- one step
    def step(self, dt: float, t: float) -> dict[str, dict[str, float]]:
        lt = time.localtime(t)
        h = lt.tm_hour + lt.tm_min / 60 + lt.tm_sec / 3600
        if self.day != lt.tm_yday:
            self.day, self.kwh_day = lt.tm_yday, 0.0
        demand = 0.5 + 0.35 * _bump(h, 7.5, 2.0) + 0.3 * _bump(h, 19.0, 2.5) - 0.15 * _bump(h, 3.0, 2.5)
        demand *= 1 + self._walk("demand", 0.004, 0.08, dt)
        day = math.sin((h - 9) / 24 * 2 * math.pi)  # -1 at 03:00, +1 at 15:00

        # raw water: rain events raise turbidity, which decays over hours
        if self.rng.random() < dt / (3600 * 18):     # about once per 18 plant hours
            self.raw_turb_event += self.rng.uniform(15, 60)
        self.raw_turb_event *= math.exp(-dt / 5400)
        raw_turb = max(1.0, 6.5 + self._walk("turb", 0.05, 3, dt) + self.raw_turb_event + self._n(0.15))
        intake = max(0.0, 320 * demand + self._n(3))
        out_flow = max(0.0, 300 * demand + self._n(2.5))

        # pumps: P1 duty (trips now and then), P2 assists at high demand
        if self.p1_trip <= 0 and self.rng.random() < dt / (3600 * 30):
            self.p1_trip = self.rng.uniform(120, 420)
        self.p1_trip = max(0.0, self.p1_trip - dt)
        p1 = self.p1_trip <= 0
        if demand > 0.8 or not p1:
            self.p2_on = True
        elif demand < 0.7:
            self.p2_on = False
        running = [p1, self.p2_on]
        n_run = max(1, sum(running))
        hz = min(50.0, 32 + 18 * (out_flow / 170 / n_run))
        pumps = {}
        for i, on in enumerate(running):
            k = i + 1
            self.vib_wear[i] += dt / (3600 * 24 * 60) if on else 0.0     # slow wear
            pumps[f"p{k}_run"] = 1.0 if on else 0.0
            pumps[f"p{k}_speed"] = round(hz + self._n(0.1), 3) if on else 0.0
            pumps[f"p{k}_current"] = max(0.0, 22 + 1.35 * (hz - 32) + self._n(0.3)) if on else 0.0
            pumps[f"p{k}_vibration"] = max(0.0, 1.8 + 0.4 * i + self.vib_wear[i] + 0.02 * (hz - 32)
                                           + self._n(0.08)) if on else max(0.0, 0.05 + self._n(0.02))
        pumps["discharge_pressure"] = max(0.0, 2.2 + 0.0065 * out_flow * (1 if any(running) else 0)
                                          + self._n(0.02))

        # tanks
        self.raw_level = min(5.0, max(0.5, self.raw_level + (intake - out_flow * 1.04) * dt / (3600 * 600)))
        self.cw_level = min(6.0, max(0.5, self.cw_level + (out_flow * 1.0 - 300 * demand) * dt / (3600 * 800)
                                 + self._walk("cw", 0.0004, 0.3, dt) * 0.01))

        # dosing
        alum = 12 + 1.1 * raw_turb + self._n(0.3)
        self.alum_tank -= alum * intake * dt / 3.6e9 * 100 / 2.5         # 2.5 t day tank, %
        if self.alum_tank < 15:
            self.alum_tank = 95.0
        cl_dose = max(0.5, 1.4 + 0.6 * (0.8 - self.chlorine) + 0.01 * raw_turb + self._n(0.02))
        self.chlorine += ((cl_dose - 1.4) * 0.5 - (self.chlorine - 0.8) * 0.3) * dt / 600 + self._n(0.004)
        self.chlorine = max(0.05, min(2.0, self.chlorine))

        # filters: dp rises with flow and turbidity load; backwash at 1.8 bar takes 12 min
        filters, backwash = {}, False
        for i in range(2):
            if self.f_bw[i] > 0:
                self.f_bw[i] = max(0.0, self.f_bw[i] - dt)
                self.f_dp[i] = 0.25 + self._n(0.01)
                backwash = True
            else:
                self.f_dp[i] += (0.00006 + 0.000004 * raw_turb) * (intake / 320) * dt
                if self.f_dp[i] >= 1.8 and not any(b > 0 for b in self.f_bw):   # one filter at a time
                    self.f_bw[i] = 720.0
            filters[f"f{i + 1}_dp"] = max(0.0, self.f_dp[i] + self._n(0.004))
            filters[f"f{i + 1}_backwash"] = 1.0 if self.f_bw[i] > 0 else 0.0

        treated_turb = max(0.02, 0.08 + 0.012 * raw_turb + (0.25 if backwash else 0.0) + self._n(0.01))

        # power meter: pumps + backwash pump + base load (blowers, dosing, lighting, HVAC)
        pf = min(0.98, 0.87 + 0.03 * (hz - 32) / 18 + self._n(0.003))
        motor_kw = sum(math.sqrt(3) * 400 * pumps[f"p{k}_current"] * pf / 1000 for k in (1, 2))
        kw = max(0.0, motor_kw + (37.0 if backwash else 0.0) + 28 + 6 * max(day, 0) + self._n(0.6))
        kva = kw / pf
        kvar = math.sqrt(max(kva ** 2 - kw ** 2, 0.0))
        volts = [230.5 + self._walk("v1", 0.05, 4, dt) + self._n(0.3), 229.8 + self._walk("v2", 0.05, 4, dt) + self._n(0.3),
                 231.0 + self._walk("v3", 0.05, 4, dt) + self._n(0.3)]
        unbal = [1.02, 0.97, 1.01]
        amps = [kva * 1000 / (3 * v) * u + self._n(0.4) for v, u in zip(volts, unbal)]
        e = kw * dt / 3600
        self.kwh += e
        self.kwh_day += e
        power = {"v_l1": volts[0], "v_l2": volts[1], "v_l3": volts[2],
                 "i_l1": max(0.0, amps[0]), "i_l2": max(0.0, amps[1]), "i_l3": max(0.0, amps[2]),
                 "kw": kw, "kvar": kvar, "kva": kva, "pf": pf, "hz": 50 + self._walk("hz", 0.002, 0.08, dt) + self._n(0.005),
                 "thd_i": max(0.5, 3.5 + 2.5 * sum(running) / 2 + self._n(0.15)), "kwh": self.kwh}

        # environment (tropical day: hot afternoons, humid nights)
        ph_temp = 29.5 + 4.5 * day + 0.03 * motor_kw + self._walk("pht", 0.01, 1, dt) + self._n(0.1)
        env = {"pumphouse_temp": ph_temp,
               "pumphouse_rh": min(98.0, max(35.0, 72 - 14 * day + self._walk("phrh", 0.03, 5, dt) + self._n(0.3))),
               "control_temp": 24.0 + 0.6 * day + self._n(0.08),
               "control_rh": 52 + 3 * -day + self._n(0.3),
               "mcc_temp": ph_temp + 6 + 0.04 * motor_kw + self._n(0.1)}

        return {
            "raw": {"flow": intake, "turbidity": raw_turb, "ph": 7.35 + self._walk("rph", 0.002, 0.25, dt) + self._n(0.01),
                    "temp": 27.5 + 1.5 * day + self._walk("rt", 0.004, 1, dt), "level": self.raw_level},
            "pumps": pumps,
            "dosing": {"alum_dose": alum, "chlorine_dose": cl_dose, "alum_tank": self.alum_tank},
            "filters": filters,
            "treated": {"flow": out_flow, "turbidity": treated_turb,
                        "ph": 7.2 + self._walk("tph", 0.002, 0.2, dt) + self._n(0.01),
                        "chlorine": self.chlorine + self._n(0.01),
                        "conductivity": 430 + 2.5 * raw_turb + self._walk("ec", 0.2, 30, dt) + self._n(2),
                        "level": self.cw_level, "pressure": max(0.0, pumps["discharge_pressure"] - 0.6 + self._n(0.02))},
            "power": power,
            "environment": env,
            "kpi": {"specific_energy": kw / out_flow if out_flow > 1 else 0.0, "energy_today": self.kwh_day,
                    "chem_cost": (alum * 9.5 + cl_dose * 38) * intake / 1000},   # g/m³ x m³/h -> kg/h x THB/kg
        }


# ====================================================================== NX Logger files
def nx_device(base: str, host: str, port: int, flat: bool = False, name: str = "Water plant"):
    """The MQTT device with one tag per signal (core/tags.DeviceCfg) - groups = plant areas."""
    from nxlogger.core.tags import DeviceCfg, new_tag
    dev = DeviceCfg(name, "mqtt", settings={"host": host, "port": port, "mode": "json"}, scan_ms=1000,
                    description=f"Water treatment plant simulator ({base}/#)")
    for area, key, label, unit, dec, desc in SIGNALS:
        t = new_tag(dev, label, AREAS[area], topic=f"{base}/{area}/{key}" if flat else f"{base}/{area}",
                    path="" if flat else key)
        t.unit, t.decimals, t.description = unit, dec, desc
        dev.tags.append(t)
    return dev


def export_device(path: str, dev):
    from nxlogger.core.tags import devices_to_json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(devices_to_json([dev]), f, indent=2, ensure_ascii=False)


def export_tags(path: str, dev):
    import csv
    from nxlogger.core.tags import CSV_COLS, tags_to_rows
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, CSV_COLS)
        w.writeheader(); w.writerows(tags_to_rows(dev))


# ====================================================================== MQTT
def messages(base: str, values: dict, ts: float, flat: bool) -> list[tuple[str, str]]:
    """(topic, payload) of one step: JSON per area, or (flat) one plain number per topic."""
    dec = {(a, k): d for a, k, _, _, d, _ in SIGNALS}
    out = []
    for area, vals in values.items():
        rounded = {k: round(v, max(dec.get((area, k), 2), 0) + 1) for k, v in vals.items()}
        if flat:
            out += [(f"{base}/{area}/{k}", repr(v)) for k, v in rounded.items()]
        else:
            out.append((f"{base}/{area}", json.dumps({"ts": round(ts, 3), **rounded})))
    return out


def main(argv=None) -> int:
    from nxlogger import config
    cfg = config.get().mqtt
    ap = argparse.ArgumentParser(prog="python -m nxlogger.sim.water_plant",
                                 description="Water treatment plant simulator over MQTT (JSON per area).")
    ap.add_argument("--host", default=cfg.get("host") or "broker.emqx.io", help="broker (default: config.ini [mqtt])")
    ap.add_argument("--port", type=int, default=int(cfg.get("port") or 1883))
    ap.add_argument("--username", default=cfg.get("username") or "")
    ap.add_argument("--password", default=cfg.get("password") or "")
    ap.add_argument("--base", default="nxlogger/demo/wtp1", help="topic prefix (make it unique on a public broker)")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between messages (default 1)")
    ap.add_argument("--speed", type=float, default=1.0, help="plant clock speed: 60 = one plant hour per minute")
    ap.add_argument("--flat", action="store_true", help="one topic per value, plain number payloads")
    ap.add_argument("--qos", type=int, choices=(0, 1), default=0)
    ap.add_argument("--retain", action="store_true", help="retain the last message of each topic")
    ap.add_argument("--seed", type=int, default=None, help="random seed (repeatable runs)")
    ap.add_argument("--export-device", metavar="FILE.json", help="write the NX Logger device with its tags and exit")
    ap.add_argument("--export-tags", metavar="FILE.csv", help="write the tag list CSV and exit")
    ap.add_argument("--count", type=int, default=0, help="stop after N messages per area (0 = run until Ctrl+C)")
    a = ap.parse_args(argv)
    base = a.base.strip("/")
    if a.export_device or a.export_tags:
        dev = nx_device(base, a.host, a.port, a.flat)
        if a.export_device:
            export_device(a.export_device, dev)
            print(f"Device '{dev.name}' with {len(dev.tags)} tags -> {a.export_device}  (Connectivity > Import...)")
        if a.export_tags:
            export_tags(a.export_tags, dev)
            print(f"{len(dev.tags)} tags -> {a.export_tags}  (Tags > select an MQTT device > Import...)")
        return 0

    import paho.mqtt.client as mqtt
    kw = dict(client_id=f"nxlogger-wtp-{uuid.uuid4().hex[:6]}", protocol=mqtt.MQTTv311)
    if hasattr(mqtt, "CallbackAPIVersion"):
        kw["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
    client = mqtt.Client(**kw)
    if a.username:
        client.username_pw_set(a.username, a.password or None)
    client.reconnect_delay_set(1, 10)
    try:
        client.connect(a.host, a.port, 30)
    except OSError as e:
        print(f"Cannot connect to {a.host}:{a.port} - {e}", file=sys.stderr)
        return 1
    client.loop_start()
    plant = WaterPlant(a.seed)
    print(f"Publishing to {a.host}:{a.port}  {base}/{'<area>/<key>' if a.flat else '<area>'}  every {a.interval:g} s"
          f"  (plant clock x{a.speed:g}) - Ctrl+C to stop")
    t0, sim_t, n = time.time(), time.time(), 0
    try:
        while not a.count or n < a.count:
            now = time.time()
            values = plant.step(a.interval * a.speed if n else 0.0, sim_t)
            for topic, payload in messages(base, values, now, a.flat):
                client.publish(topic, payload, qos=a.qos, retain=a.retain)
            n += 1
            if n % 10 == 1:
                p, tw = values["power"], values["treated"]
                print(f"{time.strftime('%H:%M:%S')}  plant {time.strftime('%H:%M', time.localtime(sim_t))}  "
                      f"flow {tw['flow']:6.1f} m³/h  power {p['kw']:6.1f} kW  turbidity {tw['turbidity']:.3f} NTU  "
                      f"pumps {int(values['pumps']['p1_run'])}{int(values['pumps']['p2_run'])}  "
                      f"backwash {int(values['filters']['f1_backwash'])}{int(values['filters']['f2_backwash'])}")
            sim_t += a.interval * a.speed
            time.sleep(max(0.0, t0 + n * a.interval - time.time()))
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop(); client.disconnect()
    print(f"Stopped after {n} step(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
