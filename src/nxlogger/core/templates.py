"""
templates.py - the "New project" templates of the Start Page. No Qt.

Each template is a ready Project: a device with tags (where it makes sense), a logging profile,
a popup alarm profile and an Overview view of its tags, so the first connect shows data.
`broker` = the default MQTT broker ({"host", "port", ...} from config.ini [mqtt]), given by the
window - core does not read files.
"""
from __future__ import annotations

from nxlogger.core import alarm_rules as ar
from nxlogger.core.project import Project, new_log_profile
from nxlogger.core.tags import new_device, new_tag
from nxlogger.core.view_model import default_view

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
    else:
        raise KeyError(key)
    p.devices = [dev]
    return _with_view(p)
