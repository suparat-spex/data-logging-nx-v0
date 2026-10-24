"""AI/ML data log: clock-aligned period files in date folders, no gap / overlap, compression,
events.csv + meta.json, retention and crash recovery."""
import csv
import datetime as _dt
import glob
import json
import os

import numpy as np
import pytest

from nxlogger.core import ml_log as ml
from nxlogger.core import recording as dl
from nxlogger.core import wavepacket as wp

TOPICS = [{"id": "t1", "name": "pump01 vib", "topic": "x/y", "channels": [(0, "A", "g"), (1, "B", "")]}]
PROJECT = {"name": "Unit test", "topics": [{"id": "t1", "channels": [
    {"index": 0, "scale": {"type": "linear", "gain": 2.0}, "description": "DE bearing"}]}]}


def _feed(lg, base, seconds, block=0.1):
    """Blocks of 100 samples (1 kHz) from base for `seconds`; returns the total samples."""
    n = int(round(seconds / block))
    for k in range(n):
        y = np.vstack([np.full(100, float(k)), np.arange(100) / 100])
        buf = wp.encode(k, k * 100, base + k * block, 1e-3, [0, 1], y, [wp.scale_for_range(1000)] * 2)
        lg.put("t1", base + k * block, buf, wp.decode(buf))
    return n * 100


@pytest.mark.parametrize("comp", ["gzip", "none"] + (["zstd"] if dl.zstd_available() else []))
def test_rotation_layout_and_readback(tmp_path, comp):
    base = ml.period_start(1_700_000_000.0, 10) + 3          # starts 3 s into a 10 s period
    lg = ml.MlLogger(str(tmp_path), PROJECT, TOPICS, period_s=10, compression=comp)
    lg.start()
    total = _feed(lg, base, 25)                              # covers 3 periods (7 + 10 + 8 s)
    lg.event("marker", "operator", "bearing noise, check", base + 5)
    lg.stop()
    assert lg.error is None and lg.dropped == 0 and lg.late == 0

    day = _dt.datetime.fromtimestamp(base).strftime("%Y-%m-%d")
    ext = dl.COMPRESSIONS[comp][1]
    files = sorted(glob.glob(os.path.join(tmp_path, day, "pump01_vib", "*.wpk" + ext)))
    assert len(files) == 3 and not glob.glob(os.path.join(tmp_path, "**", "*.part"), recursive=True)
    starts = [_dt.datetime.strptime(os.path.basename(f)[:17], "%Y-%m-%d_%H%M%S") for f in files]
    assert all(s.second % 10 == 0 for s in starts)           # aligned to the clock
    assert [(b - a).total_seconds() for a, b in zip(starts, starts[1:])] == [10, 10]

    seen = []                                                # every packet in exactly one file
    for f, s in zip(files, starts):
        meta, t, cols = dl.read_wpk(f)
        assert meta["topic"]["name"] == "pump01 vib"
        lo = s.timestamp()
        firsts = t[::100]                                    # first sample of each packet
        assert np.all((firsts >= lo - 1e-6) & (firsts < lo + 10 - 1e-6))
        seen.append(t)
    t_all = np.concatenate(seen)
    assert len(t_all) == total and np.all(np.diff(t_all) > 0)   # no gap, no overlap, in order

    meta = json.load(open(os.path.join(tmp_path, day, "meta.json"), encoding="utf-8"))
    assert meta["format_version"] == ml.FORMAT_VERSION and meta["period_s"] == 10
    ch = meta["topics"][0]["channels"]
    assert ch[0]["scale"]["gain"] == 2.0 and ch[0]["description"] == "DE bearing"
    rows = list(csv.reader(open(os.path.join(tmp_path, day, "events.csv"), encoding="utf-8-sig")))
    assert rows[0] == ml.EVENT_COLS and rows[1][2:] == ["marker", "operator", "bearing noise, check"]
    now = list(csv.reader(open(os.path.join(tmp_path, _dt.date.today().isoformat(), "events.csv"),
                               encoding="utf-8-sig")))       # log start / stop are stamped "now"
    assert [r[2] for r in now[1:]] == ["log_start", "log_stop"]


def test_period_start_is_local_clock():
    for p in (10, 60, 300, 3600):
        d = _dt.datetime.fromtimestamp(ml.period_start(1_700_000_123.4, p))
        assert (d.hour * 3600 + d.minute * 60 + d.second) % p == 0 and d.microsecond == 0


def test_retention_and_recovery(tmp_path):
    old = tmp_path / "2000-01-01" / "dev"
    old.mkdir(parents=True)
    (old / "a.wpk").write_bytes(b"x")
    today = tmp_path / _dt.date.today().isoformat() / "dev"
    today.mkdir(parents=True)
    (today / "left_by_crash.wpk").write_bytes(b"0123456789" * 100)
    (today / "half.wpk.gz.part").write_bytes(b"broken")
    lg = ml.MlLogger(str(tmp_path), PROJECT, TOPICS, compression="gzip", keep_days=30)
    lg.start()
    lg.stop()
    assert not (tmp_path / "2000-01-01").exists()            # older than keep_days
    assert (today / "left_by_crash.wpk.gz").exists() and not (today / "left_by_crash.wpk").exists()
    assert not (today / "half.wpk.gz.part").exists()
