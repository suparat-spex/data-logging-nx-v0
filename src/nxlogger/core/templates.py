"""
templates.py - the "New project" templates of the Start Page. No Qt.

Each template is a ready Project: a device with tags (where it makes sense), a logging profile,
a popup alarm profile and an Overview view of its tags, so the first connect shows data.
`broker` = the default MQTT broker ({"host", "port", ...} from config.ini [mqtt]), given by the
window - core does not read files.
"""
from __future__ import annotations

from nxlogger.core import aggregation as ag
from nxlogger.core import alarm_rules as ar
from nxlogger.core import system_tags as ss
from nxlogger.core import virtual_tags as vt
from nxlogger.core.project import Project, new_log_profile
from nxlogger.core.tags import new_device, new_tag
from nxlogger.core.view_model import default_view, new_tile, new_view

TEMPLATES = [   # key, title, description, icon
    ("blank", "Blank", "An empty project - add your own devices and tags.", "file-plus"),
    ("simulation", "Simulation", "Demo signals, an Overview view and logging - try the app without hardware.",
     "waves"),
    ("modbus_tcp", "Modbus TCP", "A PLC or meter over Ethernet with four holding-register tags to adjust.",
     "ethernet-port"),
    ("mqtt", "MQTT / IoT", "A broker and two JSON topic tags - for gateways, ESP32 and IoT sensors.", "radio"),
    ("opcua", "OPC UA", "An OPC UA client - connect, then browse the server to add its nodes as tags.",
     "network"),
    ("water_plant", "Water plant demo", "The water treatment plant simulator: 49 tags in plant areas, "
     "with a view. Start the simulator first.", "factory"),
    ("factory_energy", "Factory energy demo", "11 power meters over Modbus TCP: cost (TOU), demand, kWh today, "
     "solar, alarms, 3 views. Start the simulator first.", "gauge"),
]
TEMPLATE = {t[0]: t for t in TEMPLATES}
WATER_PLANT_BASE = "nxlogger/demo/wtp1"             # the simulator's default topic prefix


def _base(name: str) -> Project:
    return Project(name=name, logging_profiles=[new_log_profile("signal")],
                   alarm_profiles=[ar.new_profile("popup", "Popup + sound")])


def _with_view(p: Project, max_tags: int = 8) -> Project:
    keys = [(d.id, t.index) for d in p.devices for t in d.tags][:max_tags]
    if keys:
        p.views = [default_view("Overview", keys)]
    return p


def make(key: str, broker: dict | None = None) -> Project:
    """A new project from a template (KeyError for an unknown key)."""
    broker = broker or {}
    host, port = broker.get("host") or "broker.emqx.io", int(broker.get("port") or 1883)
    title = TEMPLATE[key][1]
    if key == "blank":
        return _base("Untitled project")
    if key == "simulation":
        p = Project.default()
        p.name = "Simulation demo"
        return p
    p = _base(f"{title} project")
    if key == "modbus_tcp":
        dev = new_device("modbus_tcp", "PLC-01")
        for i, name in enumerate(("Temperature", "Pressure", "Flow", "Level")):
            t = new_tag(dev, name, addr=i * 2)                 # float32 = two registers each
            dev.tags.append(t)
    elif key == "mqtt":
        dev = new_device("mqtt", "Broker")
        dev.settings.update(host=host, port=port, mode="json")
        for name, path in (("Temperature", "temp"), ("Humidity", "hum")):
            dev.tags.append(new_tag(dev, name, topic="plant/line1/env", path=path))
    elif key == "opcua":
        dev = new_device("opcua", "OPC UA server")
    elif key == "water_plant":
        from nxlogger.sim import water_plant as wtp            # no Qt: the signal list and the device
        dev = wtp.nx_device(WATER_PLANT_BASE, host, port)
        p.name = "Water plant demo"
    elif key == "factory_energy":
        return _factory_energy()
    else:
        raise KeyError(key)
    p.devices = [dev]
    return _with_view(p)


# ====================================================================== factory energy demo
TOU = {"on": 4.1839, "off": 2.6037}                  # THB/kWh (Thai TOU, editable Constant tags)
CONTRACT_KW = 850.0                                  # demand limit of the demo factory
CO2_KG_KWH = 0.4999                                  # grid emission factor (TGO, editable)


def _factory_energy() -> Project:
    """11 meters (sim/factory_energy.py, Modbus TCP 127.0.0.1:5020) + what makes them useful:
    Virtual tags (load, solar share, TOU rate, cost, CO2), Aggregation tags (15-min demand, peak
    today, kWh / solar / cost today, compressor run hours), 3 views, 4 alarms, 2 logging profiles."""
    from nxlogger.sim import factory_energy as fe                   # no Qt: the meters and the device
    p = _base("Factory energy demo")
    p.description = "Factory energy monitoring demo - start Simulators > Factory energy, then Connect."
    dev = fe.nx_device()
    p.devices = [dev]
    tag = {t.name: (dev.id, t.index) for t in dev.tags}
    k = lambda meter, field: list(tag[fe.tag_name(meter, field)])          # noqa: E731
    vts, aggs = p.virtual_tags, p.aggregations

    def v(kind, name, group="Energy", **kw):
        x = vt.new_virtual(kind, name)
        x.update(group=group, **kw)
        vts.append(x)
        return x

    def inp(*pairs):
        return [{"alias": a, "key": list(key)} for a, key in pairs]
    rate_on = v("constant", "Rate_on_peak", "Energy/Tariff", value=TOU["on"], unit="THB/kWh", decimals=4,
                description="TOU on-peak rate (Mon-Fri 09:00-22:00) - edit to your tariff")
    rate_off = v("constant", "Rate_off_peak", "Energy/Tariff", value=TOU["off"], unit="THB/kWh", decimals=4,
                 description="TOU off-peak rate - edit to your tariff")
    contract = v("constant", "Contract_kW", "Energy/Tariff", value=CONTRACT_KW, unit="kW", decimals=0,
                 description="Contracted maximum demand")
    co2f = v("constant", "CO2_factor", "Energy/Tariff", value=CO2_KG_KWH, unit="kg/kWh", decimals=4,
             description="Grid emission factor")
    feeders = [m for _u, m, *_r in fe.METERS if m not in ("main", "solar")]
    total = v("formula", "Total_load_kW", unit="kW", decimals=1, description="Sum of the 9 feeders",
              inputs=inp(*[(chr(65 + i), k(m, "kw")) for i, m in enumerate(feeders)]),
              expr=" + ".join(chr(65 + i) for i in range(len(feeders))))
    v("formula", "Solar_share", unit="%", decimals=1, description="Solar share of the consumption",
      inputs=inp(("S", k("solar", "kw")), ("M", k("main", "kw"))), expr="if(S + M > 1, S / (S + M) * 100, 0)")
    on_peak = v("logic", "On_peak", "Energy/Tariff", description="TOU on-peak now (Mon-Fri 09:00-22:00)",
                inputs=inp(("W", ss.sys_key("Weekday")), ("H", ss.sys_key("Hour"))),
                expr="W <= 5 and H >= 9 and H < 22")
    rate = v("formula", "Rate_now", "Energy/Tariff", unit="THB/kWh", decimals=4, description="The rate now",
             inputs=inp(("P", vt.vt_key(on_peak["id"])), ("R1", vt.vt_key(rate_on["id"])),
                        ("R2", vt.vt_key(rate_off["id"]))), expr="if(P, R1, R2)")
    cost = v("formula", "Cost_THB_h", unit="THB/h", decimals=1, description="Incomer power x the rate now",
             inputs=inp(("A", k("main", "kw")), ("R", vt.vt_key(rate["id"]))), expr="A * R")
    v("formula", "CO2_kg_h", unit="kg/h", decimals=1, description="Incomer power x the emission factor",
      inputs=inp(("A", k("main", "kw")), ("F", vt.vt_key(co2f["id"]))), expr="A * F")
    comp_run = v("logic", "Comp1_running", "Utilities", description="Air compressor 1 runs (above 5 kW)",
                 inputs=inp(("A", k("comp1", "kw"))), expr="A > 5")
    comp_trip = v("logic", "Comp1_trip", "Utilities", description="Production runs but compressor 1 is off",
                  inputs=inp(("T", vt.vt_key(total["id"])), ("A", k("comp1", "kw"))), expr="T > 300 and A < 5")

    def a(kind, name, group="Energy", **kw):
        x = ag.new_aggregation(kind, name)
        x.update(group=group, **kw)
        aggs.append(x)
        return x
    demand = a("statistic", "Demand_15min", source=k("main", "kw"), stat="mean", win_n=15, win_unit="min",
               win_mode="block", unit="kW", decimals=1, description="15-minute average demand (billing interval)")
    peak = a("minmax", "Peak_demand_today", source=list(ag.agg_key(demand["id"])), hold="max", reset="day",
             reset_at="00:00", unit="kW", decimals=1, description="Highest 15-minute demand today")
    energy = a("totalizer", "Energy_today", source=k("main", "kw"), rate_per="h", reset="day", reset_at="00:00",
               unit="kWh", decimals=1, description="Energy from the grid today")
    a("totalizer", "Solar_today", "Solar", source=k("solar", "kw"), rate_per="h", reset="day", reset_at="00:00",
      unit="kWh", decimals=1, description="Solar energy today")
    cost_day = a("totalizer", "Cost_today", source=list(vt.vt_key(cost["id"])), rate_per="h", reset="day",
                 reset_at="00:00", unit="THB", decimals=0, description="Electricity cost today (TOU)")
    a("runtime", "Comp1_run_hours", "Utilities", source=list(vt.vt_key(comp_run["id"])), time_unit="h",
      reset="never", unit="h", decimals=2, description="Air compressor 1 running hours")

    # views
    main_kw, main_pf, solar_kw = k("main", "kw"), k("main", "pf"), k("solar", "kw")
    ov = new_view("Energy overview")
    ov["tiles"] = [new_tile("text", 10, 10, w=1180, h=60, text="Factory energy", style="H1", show_title=False)]
    for i, (key, title, dec) in enumerate(((main_kw, "Incomer", 1), (main_pf, "Power factor", 3),
                                           (list(vt.vt_key(cost["id"])), "Cost", 0), (solar_kw, "Solar", 1),
                                           (list(ag.agg_key(energy["id"])), "Energy today", 0),
                                           (list(ag.agg_key(cost_day["id"])), "Cost today", 0))):
        ov["tiles"].append(new_tile("value", 10 + i * 200, 80, w=190, h=110, channel=key, title=title, decimals=dec))
    ov["tiles"].append(new_tile("plot", 10, 210, w=1180, h=320, title="Power",
                                channels=[main_kw, solar_kw, list(vt.vt_key(total["id"]))]))
    ov["tiles"].append(new_tile("gauge", 1210, 80, w=300, h=260, channel=list(ag.agg_key(demand["id"])),
                                title="Demand (15 min)", g_min=0, g_max=1000, g_warn=CONTRACT_KW * 0.94,
                                g_alarm=CONTRACT_KW, decimals=0))
    fd = new_view("Feeders")
    fd["tiles"] = [new_tile("text", 10, 10, w=1180, h=60, text="Feeders", style="H1", show_title=False),
                   new_tile("tags", 10, 80, w=580, h=420, title="Active power",
                            channels=[k(m, "kw") for _u, m, *_r in fe.METERS]),
                   new_tile("tags", 610, 80, w=580, h=420, title="Power factor",
                            channels=[k(m, "pf") for _u, m, *_r in fe.METERS if m != "solar"]),
                   new_tile("plot", 10, 520, w=1180, h=300, title="Production",
                            channels=[k(m, "kw") for m in ("line1", "line2", "molding")]),
                   new_tile("plot", 1210, 80, w=500, h=420, title="Utilities",
                            channels=[k(m, "kw") for m in ("comp1", "comp2", "chiller", "tower")])]
    dc = new_view("Demand & cost")
    dc["tiles"] = [new_tile("text", 10, 10, w=1180, h=60, text="Demand & cost", style="H1", show_title=False)]
    for i, (key, title, dec) in enumerate(((list(ag.agg_key(demand["id"])), "Demand (15 min)", 1),
                                           (list(ag.agg_key(peak["id"])), "Peak today", 1),
                                           (list(ag.agg_key(peak["id"], 1)), "Peak yesterday", 1),
                                           (list(vt.vt_key(contract["id"])), "Contract", 0),
                                           (list(vt.vt_key(rate["id"])), "Rate now", 4),
                                           (list(ag.agg_key(cost_day["id"], 1)), "Cost yesterday", 0))):
        dc["tiles"].append(new_tile("value", 10 + i * 200, 80, w=190, h=110, channel=key, title=title, decimals=dec))
    dc["tiles"].append(new_tile("plot", 10, 210, w=1180, h=320, title="Demand",
                                channels=[main_kw, list(ag.agg_key(demand["id"])), list(vt.vt_key(contract["id"]))]))
    p.views = [ov, fd, dc]

    # alarms (popup profile of _base)
    prof = [p.alarm_profiles[0]["id"]]

    def alarm(name, key, levels, desc):
        x = ar.new_alarm(key, name)
        x.update(description=desc, profiles=prof)
        for i, (typ, val, delay) in enumerate(levels):
            x["levels"][i].update(enabled=True, type=typ, value=val, delay_s=delay)
        p.alarms.append(x)
    alarm("Peak demand", list(ag.agg_key(demand["id"])), [("H", CONTRACT_KW, 0), ("H", CONTRACT_KW * 0.94, 0)],
          "15-minute demand above the contract (level 1) or 94 % of it (level 2)")
    alarm("Low power factor", main_pf, [("L", 0.85, 60)], "Incomer power factor below 0.85 for a minute")
    alarm("Compressor 1 trip", list(vt.vt_key(comp_trip["id"])), [("H", 0.5, 10)],
          "Production runs but air compressor 1 is off")
    alarm("Incomer overload", main_kw, [("H", 950, 30)], "Incomer above 950 kW for 30 s")

    p.logging_profiles = [new_log_profile("signal", "Data 1 s"),
                          {**new_log_profile("statistic", "Energy 15 min"), "interval_s": 900.0,
                           "stats": ["mean", "min", "max"]}]
    return p
