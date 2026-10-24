"""
Offscreen smoke test for NX Logger: real public broker, synthetic data, alarms,
all logging profile types, variables, a view with stats / alarm / FFT / gauge tiles, screenshots of every tab.

  QT_QPA_PLATFORM=offscreen python -u offscreen_test.py \
      --theme dark --out /path/to/scratch/nxlogger_test [--seconds 6]

Data: CH1 constant 2.0 °C, CH2 5 Hz sine ±1 g, CH3 constant 3.0 bar, 1000 S/s.
Alarms: CH2 L1 H 0.8 / L2 L -0.8, CH1 L3 H 1.5 with 1 s on-delay, CH3 L4 data loss
1 s (publishing stops near the end). Exit code 0 = all checks passed.
"""
from __future__ import annotations

import argparse
import faulthandler
import glob
import json
import os
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
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)
faulthandler.dump_traceback_later(args.seconds + 60, exit=True)      # never hang forever

from PySide6.QtCore import QCoreApplication, QSettings, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication                              # noqa: E402

QCoreApplication.setOrganizationName("SPEXTEST"); QCoreApplication.setApplicationName("nxlogger-test")
QSettings().clear()
app = QApplication(sys.argv)
from nxlogger.comms.mqtt_client import MqttClient                       # noqa: E402
from nxlogger.core import alarm_rules as da                             # noqa: E402
from nxlogger.core import project as L                                  # noqa: E402
from nxlogger.core import variables as vr                               # noqa: E402
from nxlogger.core import view_model as dv                              # noqa: E402
from nxlogger.core import wavepacket as wp                              # noqa: E402
from nxlogger.ui.main_window import DaqWindow                           # noqa: E402

failures: list[str] = []


def check(ok, what):
    print(("  OK   " if ok else "  FAIL ") + what)
    if not ok:
        failures.append(what)


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

# ---- project
base = f"spexth/daq/test{time.time_ns() % 10**9}"
top = L.TopicCfg("Engine bay", base + "/a", channels=[
    L.ChannelCfg(0, "Temperature", "°C"), L.ChannelCfg(1, "Vibration", "g"), L.ChannelCfg(2, "Pressure", "bar")])
pop = da.new_profile("popup", "Popup + sound")
rest = {**da.new_profile("rest", "Test REST"), "url": f"http://127.0.0.1:{srv.server_port}/alarms"}
a1 = da.new_alarm((top.id, 1), "Vibration limits")
a1["levels"][0] = {"enabled": True, "type": "H", "value": 0.8, "hyst": 0.1, "delay_s": 0}
a1["levels"][1] = {"enabled": True, "type": "L", "value": -0.8, "hyst": 0.1, "delay_s": 0}
a1["profiles"] = [pop["id"], rest["id"]]
a2 = da.new_alarm((top.id, 0), "Temperature high (delayed)")
a2["levels"][2] = {"enabled": True, "type": "H", "value": 1.5, "hyst": 0.2, "delay_s": 1.0}
a3 = da.new_alarm((top.id, 2), "Pressure data loss")
a3["levels"][0]["enabled"] = False
a3["levels"][3] = {"enabled": True, "type": "D", "value": 1.0, "hyst": 0, "delay_s": 0}
lp = [L.new_log_profile("signal"),
      {**L.new_log_profile("statistic", "Stats 1s"), "interval_s": 1.0, "stats": ["mean", "min", "max", "rms"]},
      {**L.new_log_profile("statistic", "Stats TDMS"), "format": "TDMS", "interval_s": 1.0}]
view = dv.new_view("Overview")
view["tiles"] = [
    {"id": "s1", "kind": "stats", "x": 10, "y": 10, "w": 620, "h": 170, "title": "",
     "channels": [[top.id, i] for i in range(3)], "columns": ["Value", "Mean", "Min", "Max"], "window_s": 0, "decimals": 3},
    {"id": "p1", "kind": "plot", "x": 650, "y": 10, "w": 600, "h": 170, "title": "Vibration", "channels": [[top.id, 1]]},
    {"id": "a1", "kind": "alarms", "x": 10, "y": 200, "w": 720, "h": 200, "title": "", "source": "active",
     "max_rows": 5, "columns": ["Time", "Level", "State", "Alarm", "Value"], "levels": [1, 2, 3, 4]}]
v_rms = {**vr.new_variable("statistic", "VibRMS"), "source": [top.id, 1], "stat": "rms", "window_s": 1.0}
v_sum = {**vr.new_variable("formula", "Sum"), "expr": "Engine_bay_Temperature + Engine_bay_Pressure"}
view["tiles"] += [dv.new_tile("plot", 740, 200, plot="fft", w=510, h=200, channels=[[top.id, 1]]),
                  dv.new_tile("gauge", 1260, 10, channel=list(vr.var_key(v_rms["id"])), g_min=0, g_max=1,
                              quantity="value", gauge_style="speed", h=200)]
log_dir = os.path.join(args.out, "rec")
p = L.Project(name="SXL Test", topics=[top], logging=L.LoggingCfg(folder=log_dir), logging_profiles=lp,
              alarms=[a1, a2, a3], alarm_profiles=[pop, rest], views=[view], variables=[v_rms, v_sum])

w = DaqWindow(); w.resize(1500, 920); w._set_project(p, None); w.set_theme(args.theme); w.show()
QTimer.singleShot(300, w.mqtt_panel.btn.click)
pub = MqttClient(); pub.connect_to("broker.emqx.io", 1883)
state = {"seq": 0, "idx": 0, "on": True}
T0 = time.time()


def send():
    if not pub.is_connected or not state["on"]:
        return
    n, fs = 200, 1000
    t = (state["idx"] + np.arange(n)) / fs
    y = np.vstack([np.full(n, 2.0), np.sin(2 * np.pi * 5 * t), np.full(n, 3.0)])
    pub.publish(base + "/a", wp.encode(state["seq"], state["idx"], T0 + state["idx"] / fs, 1 / fs,
                                       [0, 1, 2], y, [wp.scale_for_range(10)] * 3), 0)
    state["seq"] = (state["seq"] + 1) & 0xFFFF; state["idx"] += n


QTimer(w, interval=200, timeout=send).start()


def shot(name, widget=None):
    app.processEvents()
    (widget or w).grab().save(os.path.join(args.out, f"{name}_{args.theme}.png"))


def start():
    w._start_recording()
    check(w.recorder is not None, "recording started")


def screens():
    w._update_stats()
    for key in ("screen", "channels", "logging"):
        w._show_doc(key); w._update_stats(); shot(key)
    w._show_doc(f"view:{view['id']}"); w._update_stats(); shot("view_live")
    page = w.view_pages[view["id"]]
    page.btn_edit.setChecked(True); shot("view_edit"); page.btn_edit.setChecked(False)
    state["on"] = False                         # -> data-loss alarm


def finish():
    eng = w.alarm_engine
    w._show_doc("alarms")
    for i, name in enumerate(("alarms_active", "alarms_defs", "alarms_log", "alarms_profiles")):
        w.alarm_page.tabs.setCurrentIndex(i); w.alarm_page.update_live(force=True); shot(name)
    st = w.streams.get(top.id)
    check(st is not None and st.pkts > 5, f"packets received: {st.pkts if st else 0}")
    fired = {(e["name"], e["level"]) for e in eng.log if e["event"] == "ON"}
    for want in (("Vibration limits", 1), ("Vibration limits", 2), ("Temperature high (delayed)", 3),
                 ("Pressure data loss", 4)):
        check(want in fired, f"alarm ON {want[0]} L{want[1]}")
    check(not any(k.startswith("alarm") for k in w.banners.items), "no alarm banners")
    ve = w.variable_engine
    check(not ve.errors, f"variables compile: {ve.errors or 'ok'}")
    r = ve.values.get(v_rms["id"])
    check(r is not None and abs(r - 1 / 2 ** 0.5) < 0.05, f"variable RMS of a 1 V sine: {r}")
    sm = ve.values.get(v_sum["id"])
    check(sm is not None and abs(sm - 5.0) < 0.01, f"formula variable 2 °C + 3 bar: {sm}")
    w._show_doc("variables"); shot("variables")
    check(len(posts) > 0, f"REST webhook posts: {len(posts)}")
    w._stop_recording()
    sess = sorted(glob.glob(os.path.join(log_dir, "*/")))
    check(bool(sess), "session folder created")
    if sess:
        files = sorted(os.listdir(sess[-1]))
        print("  files:", files)
        for pat in ("Waveform_*.csv", "Stats_1s_*.csv", "*Stats_TDMS.tdms", "project.json",
                    "Waveform_Variables_*.csv"):
            check(bool(glob.glob(os.path.join(sess[-1], pat))), f"file {pat}")
        sc = glob.glob(os.path.join(sess[-1], "Stats_1s_*.csv"))
        if sc:
            rows = [r for r in open(sc[0], encoding="utf-8-sig").read().splitlines() if not r.startswith("#")]
            print("  stats header:", rows[0][:120])
            check(len(rows) >= 3, f"statistic rows: {len(rows) - 1}")
    path = os.path.join(args.out, "test" + L.PROJECT_EXT)
    w.project_path = path; w._save_project()
    q = L.Project.from_dict(json.load(open(path, encoding="utf-8")))
    check(len(q.alarms) == 3 and len(q.logging_profiles) == 3 and len(q.views) == 1, "project round trip")
    w.dirty = False; w.close()
    print(f"\n{'PASSED' if not failures else 'FAILED: ' + '; '.join(failures)}  (screenshots in {args.out})")
    app.exit(1 if failures else 0)


ms = int(args.seconds * 1000)
QTimer.singleShot(2500, start)
QTimer.singleShot(2500 + ms, screens)
QTimer.singleShot(2500 + ms + 2500, finish)
sys.exit(app.exec())
