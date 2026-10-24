"""Recorder writes CSV + raw .wpk in a session folder with a file index; .wpk reads back losslessly."""
import csv
import glob
import json
import os
import time

import numpy as np

from nxlogger.core import recording as dl
from nxlogger.core import wavepacket as wp


def test_csv_and_wpk(tmp_path):
    topics = [{"id": "t1", "name": "Dev", "topic": "x/y", "channels": [(0, "A", "V"), (1, "B", "")]}]
    rec = dl.Recorder(str(tmp_path), {"name": "Unit test"}, topics, fmt="CSV", keep_raw=True)
    rec.start()
    for k in range(5):
        y = np.vstack([np.full(100, 1.0 + k), np.arange(100) / 100])
        buf = wp.encode(k, k * 100, 1_700_000_000 + k * 0.1, 1e-3, [0, 1], y, [wp.scale_for_range(10)] * 2)
        rec.put("t1", time.time(), buf, wp.decode(buf))
    rec.stop()

    files = sorted(os.listdir(rec.dir))
    assert "project.json" in files
    info = json.load(open(os.path.join(rec.dir, "project.json"), encoding="utf-8"))
    assert {f["format"] for f in info["files"]} == {"csv", "wpk"} and info["devices"][0]["name"] == "Dev"
    rows = [r for r in csv.reader(open(glob.glob(os.path.join(rec.dir, "*.csv"))[0], encoding="utf-8-sig"))
            if r and not r[0].startswith("#")]
    assert len(rows) == 1 + 500                               # header + every sample
    meta, t, cols = dl.read_wpk(glob.glob(os.path.join(rec.dir, "*.wpk"))[0])
    assert len(t) == 500 and np.allclose(cols["A"][-100:], 5.0, atol=1e-3)


def test_safe_name():
    assert dl.safe_name("a/b:c d") not in ("", "a/b:c d")
