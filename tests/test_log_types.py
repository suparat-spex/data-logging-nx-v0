"""Logging types (core/log_types.py) and their recorder files: interval decimation, on change,
trigger (pre / post), batch (summary), event snapshots; older "signal" profiles."""
import csv
import json
import datetime as dt
import os
import time

import numpy as np

from nxlogger.core import log_types as lt
from nxlogger.core import recording as dl
from nxlogger.core.project import Project, new_log_profile
from nxlogger.core.variables import VarBlock

TOPIC = {"id": "d1", "name": "PLC", "topic": "plc", "channels": [(0, "Flow", "m3/h"), (1, "Speed", "")],
         "virtual": True}


def _blk(seq, t0, ys, dt_=1.0):
    y = np.array(ys, dtype=float)
    return VarBlock(seq, seq * y.shape[1], t0, dt_, y, [0, 1])


def _rows(path):
    with open(path, encoding="utf-8-sig") as f:
        return [r for r in csv.reader(x for x in f if not x.startswith("#"))]


def test_conditions_and_describe():
    c = lt.new_condition(("d", 0), ">=", 5)
    assert lt.test(c, 5) is True and lt.test(c, 4.9) is False and lt.test(c, None) is None
    assert lt.test(lt.new_condition(None), 1) is None and lt.test(c, float("nan")) is None
    assert lt.parse_times("18:00, 6:00, bad, 25:00") == [360, 1080]
    name = lambda k: "Comp1_kW"                                          # noqa: E731
    pr = new_log_profile("trigger")
    pr["when"]["start"] = c
    assert lt.describe(pr, name) == "Comp1_kW >= 5 → until false"
    assert lt.problem(new_log_profile("trigger")) and not lt.problem(pr)
    assert lt.describe(new_log_profile("change"), name) == "±0.5 % · heartbeat 10 min"
    old = {"type": "signal"}                                             # older files
    assert lt.normalize_when(old) == {"every_s": 0.0} and old["type"] == "interval"
    q = Project.from_dict({"name": "x", "devices": [], "logging_profiles": [
        {"id": "a", "name": "D", "type": "signal", "format": "CSV", "channels": None, "enabled": True}]})
    assert q.logging_profiles[0]["type"] == "interval" and q.logging_profiles[0]["when"] == {"every_s": 0.0}


def test_gates():
    v = {"k": 0.0}
    value_of = lambda key: v["k"]                                        # noqa: E731
    pr = new_log_profile("trigger")
    pr["when"].update(start=lt.new_condition(("d", 0), ">", 5), debounce_s=2)
    g = lt.Gate(pr)
    assert g.update(100, value_of) == []
    v["k"] = 9
    assert g.update(101, value_of) == [] and g.update(103, value_of) == [("on", 103, "Run 1")]   # debounced
    v["k"] = 1
    assert g.update(104, value_of) == [] and g.update(106, value_of) == [("off", 106, "")]
    ev = new_log_profile("event")
    ev["when"].update(source="condition", cond=lt.new_condition(("d", 0), ">", 5))
    e = lt.Gate(ev)
    v["k"] = 9
    assert e.update(1, value_of) == []                                   # already true at the start: no event
    v["k"] = 1; e.update(2, value_of); v["k"] = 9
    assert e.update(3, value_of) == [("fire", 3, "condition > 5")] and e.update(4, value_of) == []
    al = lt.Gate({**new_log_profile("event"), "when": {"source": "alarm", "alarm": "a1"}})
    assert al.alarm("a2", "x", 5) == [] and al.alarm("a1", "L1 H Flow", 5) == [("fire", 5, "L1 H Flow")]
    sh = new_log_profile("batch")
    sh["when"].update(by="shift", shifts="06:00, 18:00")
    b = lt.Gate(sh)
    t = dt.datetime(2026, 11, 5, 7, 0).timestamp()
    assert b.update(t, value_of) == [("on", t, "2026-11-05 06:00")]
    t2 = dt.datetime(2026, 11, 5, 18, 1).timestamp()
    assert b.update(t2, value_of) == [("off", t2, ""), ("on", t2, "2026-11-05 18:00")]
    assert b.shift_at(dt.datetime(2026, 11, 6, 3, 0).timestamp()) == ("2026-11-05", 1080)   # overnight


def test_recorder_types(tmp_path):
    t0 = float(int(time.time()) // 2 * 2 - 100)                          # whole, even seconds
    profs = [
        {"name": "Every", "type": "interval", "when": {"every_s": 2.0}, "topics": [TOPIC]},
        {"name": "Change", "type": "change", "when": {"deadband": 1.0, "mode": "abs", "heartbeat_s": 0},
         "topics": [TOPIC]},
        {"name": "Run", "type": "trigger", "when": {"pre_s": 2.0, "post_s": 1.0}, "topics": [TOPIC]},
        {"name": "Shift", "type": "batch", "when": {"stats": ["mean", "max"]}, "topics": [TOPIC]},
        {"name": "Snap", "type": "event", "topics": [], "tags": [("Flow", "m3/h"), ("Text", "")]},
    ]
    rec = dl.Recorder(str(tmp_path), {"name": "T"}, profiles=profs)
    rec.start()
    rec.put("d1", t0, b"", _blk(0, t0, [[1, 1.5, 3, 3.2, 5, 5.1], [0] * 6]))     # t0 .. t0+5
    rec.gate(2, True, t0 + 7, "Run 1"); rec.gate(3, True, t0 + 7, "Lot A")
    rec.put("d1", t0, b"", _blk(1, t0 + 6, [[6, 7, 8], [1, 1, 1]]))            # t0+6 .. t0+8
    rec.gate(2, False, t0 + 8.5, ""); rec.gate(3, False, t0 + 8.5, "")
    rec.put("d1", t0, b"", _blk(2, t0 + 9, [[9, 10, 11], [1, 1, 1]]))          # t0+9 .. t0+11
    rec.event(4, t0 + 10, "L1 H Flow", ["10.0", "Run"])
    time.sleep(0.3)
    assert rec.written(4) == 1
    rec.stop()
    d = rec.dir
    every = _rows(os.path.join(d, "Every_PLC.csv"))[1:]
    assert len(every) == 6                                               # one per 2-s period of 12 samples
    change = [float(r[2]) for r in _rows(os.path.join(d, "Change_PLC.csv"))[1:]]
    assert change == [1, 3, 5, 7, 9, 11]                                 # moved more than 1.0
    run = _rows(os.path.join(d, "Run_PLC.csv"))
    assert run[0][:3] == ["time_unix_s", "t_rel_s", "run"]
    assert [float(r[3]) for r in run[1:]] == [5.1, 6, 7, 8, 9]          # 2 s before, 1 s after
    assert {r[2] for r in run[1:]} == {"Run 1"}
    shift = _rows(os.path.join(d, "Shift_PLC.csv"))[1:]
    assert [float(r[3]) for r in shift] == [7, 8] and shift[0][2] == "Lot A"   # only while on
    summ = _rows(os.path.join(d, "Shift_PLC_summary.csv"))
    assert summ[0][:5] == ["batch", "start", "end", "duration_s", "samples"] and summ[1][0] == "Lot A"
    assert summ[1][4] == "2" and float(summ[1][5]) == 7.5 and float(summ[1][6]) == 8
    snap = _rows(os.path.join(d, "Snap.csv"))
    assert snap[0] == ["time", "time_unix_s", "event", "Flow [m3/h]", "Text"] and snap[1][2:] == ["L1 H Flow", "10.0", "Run"]


def test_file_names_and_periods():
    from nxlogger.core import log_files as lf
    out = lf.default_output("interval")
    assert (out["folders"], out["new_file"]) == ("ymd", "day") and lf.default_output()["folders"] == "session"
    t = dt.datetime(2026, 11, 5, 9, 30).timestamp()
    ctx = {"profile": "Data_1_s", "device": "PLC", "project": "Plant"}
    assert lf.folder_parts(out, t, ctx) == ["2026", "11", "05"]
    assert lf.render(lf.name_pattern(out)[0], t, ctx) == "Data_1_s_PLC_20261105"
    assert lf.name_pattern({**out, "name": "{profile}"})[1]                   # every day needs {dd}: added
    assert lf.period_start({**out, "day_at": "06:00"}, dt.datetime(2026, 11, 5, 5, 0).timestamp()) == \
        dt.datetime(2026, 11, 4, 6, 0).timestamp()                           # before the shift: yesterday's
    wk = {**out, "new_file": "week", "week_day": 0}
    assert dt.datetime.fromtimestamp(lf.period_start(wk, t)).weekday() == 0
    mo = {**out, "new_file": "month"}
    assert lf.next_start(mo, t) == dt.datetime(2026, 12, 1).timestamp()
    cus = {**out, "folders": "custom", "pattern": "{yyyy}/{MM}-{MMM}"}
    assert lf.folder_parts(cus, t, ctx) == ["2026", "11-Nov"] and not lf.problem(cus)
    assert lf.problem({**out, "folders": "custom", "pattern": "{yyyy}/{bad}"})
    assert lf.problem({**out, "name": "a|b"}) and lf.problem({**out, "name": "x_{dd}", "keep_days": 3})
    assert lf.describe(out) == "every day at 00:00"


def test_rotation_append_size_compress_keep(tmp_path):
    from nxlogger.core import log_files as lf
    base = tmp_path / "logs"
    mid = dt.datetime(2026, 11, 6, 0, 0, 0).timestamp()
    out = {**lf.default_output("interval"), "folder": str(tmp_path / "own")}
    prof = {"name": "Data", "type": "interval", "when": {"every_s": 0}, "output": out, "topics": [TOPIC]}
    rec = dl.Recorder(str(base), {"name": "T"}, profiles=[prof])
    rec.start()
    rec.put("d1", mid, b"", _blk(0, mid - 2, [[1, 2, 3, 4], [0] * 4]))     # 23:59:58 .. 00:00:01
    time.sleep(0.3)
    rec.stop()
    day1 = tmp_path / "own" / "2026" / "11" / "05" / "Data_PLC_20261105.csv"
    day2 = tmp_path / "own" / "2026" / "11" / "06" / "Data_PLC_20261106.csv"
    assert [float(r[2]) for r in _rows(day1)[1:]] == [1, 2] and [float(r[2]) for r in _rows(day2)[1:]] == [3, 4]
    idx = json.loads((day2.parent / "index.json").read_text(encoding="utf-8"))
    assert idx["files"][0]["file"] == day2.name and idx["files"][0]["profile"] == "Data"
    sess = json.loads((base / rec.session_id / "project.json").read_text(encoding="utf-8"))
    assert {os.path.basename(f["file"]) for f in sess["files"]} == {day1.name, day2.name}
    rec2 = dl.Recorder(str(base), {"name": "T"}, profiles=[prof])          # the same day again: appended
    rec2.session_id += "b"; rec2.dir += "b"
    rec2.start()
    rec2.put("d1", mid, b"", _blk(1, mid + 5, [[9], [0]]))
    time.sleep(0.3)
    rec2.stop()
    assert [float(r[2]) for r in _rows(day2)[1:]] == [3, 4, 9]

    small = {**out, "folders": "none", "new_file": "size", "size_mb": 0.0001, "compress": True,
             "folder": str(tmp_path / "small")}                              # ~100 bytes a file
    rec3 = dl.Recorder(str(base), {"name": "T"}, profiles=[{**prof, "output": small}])
    rec3.session_id += "c"; rec3.dir += "c"
    rec3.start()
    for k in range(3):
        rec3.put("d1", mid, b"", _blk(k, mid + 10 * k, [[1, 2, 3], [0] * 3]))
    time.sleep(0.3)
    rec3.stop()
    names = sorted(os.listdir(tmp_path / "small"))
    assert names[:3] == ["Data_PLC.csv.zst", "Data_PLC_001.csv.zst", "Data_PLC_002.csv.zst"] or \
        names[:3] == ["Data_PLC.csv.gz", "Data_PLC_001.csv.gz", "Data_PLC_002.csv.gz"]

    old = tmp_path / "own" / "2025" / "01" / "01"                           # Keep 30 days
    old.mkdir(parents=True)
    for n in ("Data_PLC_20250101.csv", "Other_PLC_20250101.csv"):
        (old / n).write_text("x")
        os.utime(old / n, (time.time() - 90 * 86400,) * 2)
    rec4 = dl.Recorder(str(base), {"name": "T"}, profiles=[{**prof, "output": {**out, "keep_days": 30}}])
    rec4.session_id += "d"; rec4.dir += "d"
    rec4.start(); time.sleep(0.2); rec4.stop()
    assert not (old / "Data_PLC_20250101.csv").exists() and (old / "Other_PLC_20250101.csv").exists()
    assert day1.exists()                                                     # younger: kept
