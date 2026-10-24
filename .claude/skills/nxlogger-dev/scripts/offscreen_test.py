"""
Offscreen end-to-end test for NX Logger: real connectors against local servers, alarms,
variables, statistics windows, logging profiles, a grid view, screenshots of every tab.

  QT_QPA_PLATFORM=offscreen python -u offscreen_test.py \
      --theme dark --out /path/to/scratch/nxlogger_test [--seconds 6] [--mqtt]

Devices (no hardware, no internet unless --mqtt):
  PLC  Modbus TCP (local pymodbus server, 100 ms scan): Temperature float32 = 2.0 °C,
       Pressure int16 raw 300 scaled ×0.01 = 3.0 bar, Setpoint RW (written by the test)
  UA   OPC UA client (local asyncua server, 200 ms scan): Level ramps 0..100 %; the server
       stops near the end -> data-loss alarm
  Sim  Simulation (20 ms scan): Vibration = 1 Hz sine ±1 g
  ESP  (--mqtt) MQTT wavepacket device on broker.emqx.io, 3 channels at 1000 S/s
Alarms: Vibration L1 H 0.8 / L2 L -0.8, Temperature L3 H 1.5 (1 s on-delay), Level L4 data
loss 1 s. Exit code 0 = all checks passed.
"""
from __future__ import annotations

import argparse
import faulthandler
import glob
import json
import os
import socket
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

ap = argparse.ArgumentParser()
ap.add_argument("--theme", default="dark", choices=["dark", "light"])
ap.add_argument("--out", required=True)
ap.add_argument("--seconds", type=float, default=6.0, help="recording length")
ap.add_argument("--mqtt", action="store_true", help="also test an MQTT wavepacket device (public broker)")
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)
faulthandler.dump_traceback_later(args.seconds + 90, exit=True)      # never hang forever

from PySide6.QtCore import QCoreApplication, QSettings, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication                              # noqa: E402

QCoreApplication.setOrganizationName("SPEXTEST"); QCoreApplication.setApplicationName("nxlogger-test")
QSettings().clear()
app = QApplication(sys.argv)
from nxlogger.comms.drivers.modbus import regs_to_value, value_to_regs  # noqa: E402
from nxlogger.core import alarm_rules as da                             # noqa: E402
from nxlogger.core import project as L                                  # noqa: E402
from nxlogger.core import stat_buffer as sb                             # noqa: E402
from nxlogger.core import variables as vr                               # noqa: E402
from nxlogger.core import view_model as vm                              # noqa: E402
from nxlogger.core import wavepacket as wp                              # noqa: E402
from nxlogger.core.tags import DeviceCfg, new_tag                       # noqa: E402
from nxlogger.ui.main_window import DaqWindow                           # noqa: E402

failures: list[str] = []


def check(ok, what):
    print(("  OK   " if ok else "  FAIL ") + what)
    if not ok:
        failures.append(what)


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


# ---- local REST endpoint for the webhook profile
posts = []


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        posts.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200); self.end_headers()

    def log_message(self, *a):
        pass


class Srv(HTTPServer):
    def server_bind(self):                    # HTTPServer's getfqdn() hangs on macOS
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]


srv = Srv(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

# ---- Modbus TCP server (registers: 1-based in the pymodbus block, address n = regs[n + 1])
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext  # noqa: E402
try:
    from pymodbus.datastore import ModbusSlaveContext as _Ctx
except ImportError:                                                     # pymodbus >= 3.10
    from pymodbus.datastore import ModbusDeviceContext as _Ctx
from pymodbus.server import ServerStop, StartTcpServer                  # noqa: E402

regs = [0] * 64
regs[1:3] = value_to_regs(2.0, "float32", "ABCD")      # HR 0: Temperature
regs[3] = 300                                           # HR 2: Pressure raw (×0.01)
hr = ModbusSequentialDataBlock(0, regs)
mb_port = free_port()
threading.Thread(target=StartTcpServer, daemon=True,
                 kwargs={"context": ModbusServerContext(_Ctx(hr=hr), single=True),
                         "address": ("127.0.0.1", mb_port)}).start()

# ---- OPC UA server with a ramping Level
from asyncua.sync import Server as UaServer                            # noqa: E402

ua_port = free_port()
ua = UaServer()
ua.set_endpoint(f"opc.tcp://127.0.0.1:{ua_port}/nx")
ns = ua.register_namespace("urn:nx-test")
tank = ua.nodes.objects.add_object(ns, "Tank")
level = tank.add_variable(ns, "Level", 0.0)
ua.start()
ua_on = {"run": True}


def ua_ramp():
    k = 0
    while ua_on["run"]:
        k += 1
        try:
            level.write_value(float(k % 100))
        except Exception:
            return
        time.sleep(0.1)


threading.Thread(target=ua_ramp, daemon=True).start()
time.sleep(0.8)

# ---- project
plc = DeviceCfg("PLC", "modbus_tcp", settings={"host": "127.0.0.1", "port": mb_port}, scan_ms=100)
for name, unit, addr, dtype in (("Temperature", "°C", 0, "float32"), ("Pressure", "bar", 2, "int16"),
                                ("Setpoint", "", 10, "uint16")):
    t = new_tag(plc, name, "Boiler", area="holding", addr=addr, dtype=dtype)
    t.unit = unit
    plc.tags.append(t)
plc.tags[1].scale = {"type": "linear", "m": 0.01, "b": 0.0}
plc.tags[2].access = "RW"
uad = DeviceCfg("UA", "opcua", settings={"endpoint": f"opc.tcp://127.0.0.1:{ua_port}/nx"}, scan_ms=200)
uad.tags.append(new_tag(uad, "Level", node=level.nodeid.to_string()))
uad.tags[0].unit = "%"
sim = DeviceCfg("Sim", "sim", scan_ms=20)
sim.tags.append(new_tag(sim, "Vibration", wave="sine", amp=1.0, freq=1.0, offset=0.0))
sim.tags[0].unit = "g"
devices = [plc, uad, sim]
base = f"spexth/daq/test{time.time_ns() % 10**9}"
if args.mqtt:
    esp = DeviceCfg("ESP", "mqtt", settings={"host": "broker.emqx.io", "port": 1883, "mode": "wavepacket",
                                             "topic": base + "/a"})
    for i, n in enumerate(("A", "B", "C")):
        esp.tags.append(L.TagCfg(i, n, kind="array"))
    devices.append(esp)

pop = da.new_profile("popup", "Popup + sound")
rest = {**da.new_profile("rest", "Test REST"), "url": f"http://127.0.0.1:{srv.server_port}/alarms"}
a1 = da.new_alarm((sim.id, 0), "Vibration limits")
a1["levels"][0] = {"enabled": True, "type": "H", "value": 0.8, "hyst": 0.1, "delay_s": 0}
a1["levels"][1] = {"enabled": True, "type": "L", "value": -0.8, "hyst": 0.1, "delay_s": 0}
a1["profiles"] = [pop["id"], rest["id"]]
a2 = da.new_alarm((plc.id, 0), "Temperature high (delayed)")
a2["levels"][2] = {"enabled": True, "type": "H", "value": 1.5, "hyst": 0.2, "delay_s": 1.0}
a3 = da.new_alarm((uad.id, 0), "Level data loss")
a3["levels"][0]["enabled"] = False
a3["levels"][3] = {"enabled": True, "type": "D", "value": 1.0, "hyst": 0, "delay_s": 0}
lp = [L.new_log_profile("signal"),
      {**L.new_log_profile("statistic", "Stats 1s"), "interval_s": 1.0, "stats": ["mean", "min", "max", "rms"]}]
v_rms = {**vr.new_variable("statistic", "VibRMS"), "source": [sim.id, 0], "stat": "rms",
         "win_n": 2.0, "win_unit": "s"}
v_sum = {**vr.new_variable("formula", "Sum"), "expr": "PLC_Temperature + PLC_Pressure"}
view = vm.new_view("Overview", rows=4, cols=4)
view["rows"] = [{"size": 56, "fixed": True}, {"size": 120, "fixed": True}, {"size": 2.0, "fixed": False},
                {"size": 1.5, "fixed": False}]
keys = [[d.id, t.index] for d in (plc, uad, sim) for t in d.tags]
stats_tile = vm.new_tile("stats", 3, 0, colspan=2, channels=keys, win_n=25, win_unit="samples")
view["tiles"] = [
    vm.new_tile("text", 0, 0, colspan=4, text="Plant overview", style="H1", show_title=False),
    vm.new_tile("value", 1, 0, channel=[plc.id, 0], decimals=2),
    vm.new_tile("gauge", 1, 1, channel=list(vr.var_key(v_rms["id"])), g_min=0, g_max=1),
    vm.new_tile("led", 1, 2, channel=[plc.id, 0], led_op=">", led_value=1.5, title="Temperature high"),
    vm.new_tile("value", 1, 3, channel=[uad.id, 0], quantity="max", win_n=1, win_unit="min", decimals=1),
    vm.new_tile("plot", 2, 0, colspan=3, rowspan=1, channels=[[sim.id, 0], [uad.id, 0]], title="Trend",
                span_s=10),
    vm.new_tile("tags", 2, 3, colspan=1, channels=keys),
    stats_tile,
    vm.new_tile("alarms", 3, 2, colspan=2, source="active", max_rows=5)]
log_dir = os.path.join(args.out, "rec")
p = L.Project(name="NXL Test", devices=devices, logging=L.LoggingCfg(folder=log_dir), logging_profiles=lp,
              alarms=[a1, a2, a3], alarm_profiles=[pop, rest], views=[view], variables=[v_rms, v_sum])

w = DaqWindow(); w.resize(1500, 920); w._set_project(p, None); w.set_theme(args.theme); w.show()
QTimer.singleShot(300, w._start_all)
pub_state = {"seq": 0, "idx": 0, "on": True, "client": None}
T0 = time.time()
if args.mqtt:
    import paho.mqtt.client as mqtt
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2) if hasattr(mqtt, "CallbackAPIVersion") else mqtt.Client()
    c.connect_async("broker.emqx.io", 1883); c.loop_start()
    pub_state["client"] = c

    def send():
        if not c.is_connected():
            return
        n, fs = 200, 1000
        t = (pub_state["idx"] + np.arange(n)) / fs
        y = np.vstack([np.full(n, 2.0), np.sin(2 * np.pi * 5 * t), np.full(n, 3.0)])
        c.publish(base + "/a", wp.encode(pub_state["seq"], pub_state["idx"], T0 + pub_state["idx"] / fs, 1 / fs,
                                         [0, 1, 2], y, [wp.scale_for_range(10)] * 3), 0)
        pub_state["seq"] = (pub_state["seq"] + 1) & 0xFFFF; pub_state["idx"] += n

    QTimer(w, interval=200, timeout=send).start()


def shot(name, widget=None):
    app.processEvents()
    (widget or w).grab().save(os.path.join(args.out, f"{name}_{args.theme}.png"))


def start():
    states = {d.name: w.device_state(d.id)[0] for d in p.devices}
    check(all(s == "online" for n, s in states.items() if n != "ESP"), f"devices online: {states}")
    w._start_recording()
    check(w.recorder is not None, "recording started")
    w.drivers.write(plc.id, plc.tags[2].index, 42)


def screens():
    w._update_stats()
    for key in ("screen", "connectivity", "logging"):
        w._show_doc(key); w._update_stats(); shot(key)
    w._show_doc(f"view:{view['id']}"); w._update_stats(); shot("view_live")
    page = w.view_pages[view["id"]]
    page.btn_edit.setChecked(True); page.select(("tile", stats_tile)); shot("view_edit")
    page.btn_edit.setChecked(False)
    check(regs_to_value(hr.getValues(11, 1), "uint16") == 42, "Modbus write Setpoint = 42")
    ua_on["run"] = False
    ua.stop()                                   # -> Level data-loss alarm


def finish():
    eng = w.alarm_engine
    w._show_doc("alarms")
    for i, name in enumerate(("alarms_active", "alarms_defs", "alarms_log", "alarms_profiles")):
        w.alarm_page.tabs.setCurrentIndex(i); w.alarm_page.update_live(force=True); shot(name)
    for d in (plc, uad, sim):
        st = w.streams.get(d.id)
        check(st is not None and st.pkts > 5, f"{d.name}: scans received {st.pkts if st else 0}")
    v, q, good, _ = w.tag_live(plc.id, 1)
    check(v == "3.000" and good, f"Modbus int16 ×0.01 scaling: Pressure = {v} ({q})")
    fired = {(e["name"], e["level"]) for e in eng.log if e["event"] == "ON"}
    for want in (("Vibration limits", 1), ("Vibration limits", 2), ("Temperature high (delayed)", 3),
                 ("Level data loss", 4)):
        check(want in fired, f"alarm ON {want[0]} L{want[1]}")
    check(not any(k.startswith("alarm") for k in w.banners.items), "no alarm banners")
    ve = w.variable_engine
    check(not ve.errors, f"variables compile: {ve.errors or 'ok'}")
    r = ve.values.get(v_rms["id"])
    check(r is not None and abs(r - 1 / 2 ** 0.5) < 0.1, f"variable RMS of a 1 g sine over 2 s: {r}")
    sm = ve.values.get(v_sum["id"])
    check(sm is not None and abs(sm - 5.0) < 0.01, f"formula variable 2 °C + 3 bar: {sm}")
    spec = sb.spec_of(stats_tile)
    buf = w.stats.buffers.get(((sim.id, 0), spec))
    check(buf is not None and buf.count >= 25, f"statistics window 25 samples buffered: {buf.count if buf else 0}")
    if args.mqtt:
        st = w.streams.get(devices[-1].id)
        check(st is not None and st.pkts > 5, f"MQTT wavepackets: {st.pkts if st else 0}")
    w._show_doc("variables"); shot("variables")
    check(len(posts) > 0, f"REST webhook posts: {len(posts)}")
    w._stop_recording()
    sess = sorted(glob.glob(os.path.join(log_dir, "*/")))
    check(bool(sess), "session folder created")
    if sess:
        files = sorted(os.listdir(sess[-1]))
        print("  files:", files)
        for pat in ("Data_PLC.csv", "Data_UA.csv", "Data_Sim.csv", "Stats_1s_PLC.csv", "project.json",
                    "Data_Variables_*.csv"):
            check(bool(glob.glob(os.path.join(sess[-1], pat))), f"file {pat}")
        dc = glob.glob(os.path.join(sess[-1], "Data_PLC.csv"))
        if dc:
            rows = [r for r in open(dc[0], encoding="utf-8-sig").read().splitlines() if not r.startswith("#")]
            print("  PLC header:", rows[0][:120])
            check(len(rows) > 10 and rows[1].split(",")[3] == "3", f"PLC rows: {len(rows) - 1}, pressure column 3")
        info = json.load(open(os.path.join(sess[-1], "project.json"), encoding="utf-8"))
        check(info.get("session_format") == 2 and info["project"]["version"] == 2, "session project.json v2")
    path = os.path.join(args.out, "test" + L.PROJECT_EXT)
    w.project_path = path; w._save_project()
    q = L.Project.from_dict(json.load(open(path, encoding="utf-8")))
    check(len(q.devices) == len(devices) and len(q.alarms) == 3 and len(q.views) == 1 and
          len(q.views[0]["tiles"]) == 9 and q.views[0]["tiles"][1]["row"] == 1, "project round trip (.nxl)")
    w.drivers.stop_all()
    if pub_state["client"]:
        pub_state["client"].loop_stop()
    ServerStop()
    w.dirty = False; w.close()
    print(f"\n{'PASSED' if not failures else 'FAILED: ' + '; '.join(failures)}  (screenshots in {args.out})")
    app.exit(1 if failures else 0)


ms = int(args.seconds * 1000)
QTimer.singleShot(2500, start)
QTimer.singleShot(2500 + ms, screens)
QTimer.singleShot(2500 + ms + 3000, finish)
sys.exit(app.exec())
