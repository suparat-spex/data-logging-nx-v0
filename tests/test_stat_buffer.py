"""Statistics windows buffered by the app: seconds / samples, moving / block."""
import numpy as np
import pytest

from nxlogger.core import stat_buffer as sb


def _feed(hub, key, t0, n, dt, values):
    t = t0 + np.arange(n) * dt
    hub.feed(key, t, np.asarray(values, dtype=float))


def test_spec_parsing():
    assert sb.spec_of({"win_n": 0}) is None
    assert sb.spec_of({"win_n": 10, "win_unit": "min"}) == (10.0, "min", "moving")
    assert sb.spec_of({"win_n": 7.9, "win_unit": "samples", "win_mode": "block"}) == (7.0, "samples", "block")
    assert sb.describe((5.0, "s", "block")) == "5 s block"


def test_moving_samples_window_keeps_newest_n():
    hub, key, spec = sb.StatHub(), ("d", 0), (5.0, "samples", "moving")
    hub.require([(key, spec)])
    for k in range(4):                                 # 12 scans: 0 .. 11
        _feed(hub, key, 1000 + 3 * k, 3, 1.0, [3 * k, 3 * k + 1, 3 * k + 2])
    v = hub.values(key, spec, ["mean", "min", "max"])
    assert (v["Mean"], v["Min"], v["Max"]) == (9.0, 7.0, 11.0)


def test_moving_time_window():
    hub, key, spec = sb.StatHub(), ("d", 0), (1.0, "min", "moving")
    hub.require([(key, spec)])
    for k in range(180):                               # 3 min of 1 s scans, value = k
        _feed(hub, key, 10_000 + k, 1, 1.0, [k])
    v = hub.values(key, spec, ["min", "max"])
    assert v["Min"] == pytest.approx(119) and v["Max"] == 179


def test_block_window_holds_last_full_block():
    hub, key, spec = sb.StatHub(), ("d", 0), (10.0, "s", "block")
    hub.require([(key, spec)])
    for k in range(25):                                # 1 s scans from a 10 s boundary
        _feed(hub, key, 100.0 + k, 1, 1.0, [k])
    v = hub.values(key, spec, ["mean", "max"])         # block 110..119 -> values 10..19
    assert v["Mean"] == pytest.approx(14.5) and v["Max"] == 19
    assert hub.fill(key, spec) == 1.0


def test_require_drops_unused_buffers():
    hub = sb.StatHub()
    a, b = (("d", 0), (5.0, "s", "moving")), (("d", 1), (5.0, "s", "moving"))
    hub.require([a, b])
    hub.require([a])
    assert hub.wants(("d", 0)) and not hub.wants(("d", 1))
    assert hub.values(("d", 1), (5.0, "s", "moving"), ["mean"]) is None
