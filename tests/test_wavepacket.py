"""Packet format: the contract with the ESP32 firmware and LabVIEW - keep these green."""
import numpy as np
import pytest

from nxlogger.core import wavepacket as wp


def make(seq=7, n=100, channels=(0, 1, 3)):
    t = np.arange(n) / 1000
    data = np.vstack([np.sin(2 * np.pi * (k + 1) * t) * (k + 1) for k in range(len(channels))])
    scales = [wp.scale_for_range(10)] * len(channels)
    return wp.encode(seq, 1234, 1_700_000_000.5, 1e-3, list(channels), data, scales), data, scales


def test_round_trip():
    buf, data, scales = make()
    blk = wp.decode(buf)
    assert (blk.seq, blk.sample_idx, blk.n, blk.channels) == (7, 1234, 100, [0, 1, 3])
    assert blk.t0 == 1_700_000_000.5 and blk.dt == pytest.approx(1e-3)
    assert np.allclose(blk.y, data, atol=scales[0])          # within one LSB


def test_size_matches_readme():
    buf, _, _ = make(channels=(0, 1, 2, 3))                   # README: 4 ch x 100 samples = 846 B
    assert len(buf) == 846


def test_seq_wraps():
    assert wp.decode(make(seq=0x1_0005)[0]).seq == 5


def test_crc_error_detected():
    buf = bytearray(make()[0])
    buf[40] ^= 0xFF
    with pytest.raises(wp.PacketError):
        wp.decode(bytes(buf))


@pytest.mark.parametrize("bad", [b"", b"\x00" * 40, b"\x5a\xa5" + b"\x00" * 60])
def test_malformed(bad):
    with pytest.raises(wp.PacketError):
        wp.decode(bad)


def test_quantize_saturates():
    s = wp.scale_for_range(1.0)
    assert list(wp.quantize(np.array([2.0, -2.0]), s)) == [32767, -32768]


def test_crc16_xmodem_check_value():
    assert wp.crc16(b"123456789") == 0x31C3                 # CRC-16/XMODEM reference value
