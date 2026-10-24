"""
wavepacket.py - Lightweight binary waveform packet (LabVIEW-waveform style)

One packet = one block of N samples for up to 8 channels.
Like the STIM318, values travel as two's-complement integers and the
receiver converts them back with a known scale:

    value = raw_int16 * scale[ch]          (raw = round(value / scale))

LabVIEW waveform mapping:   t0 -> t0_unix,  dt -> dt_ns * 1e-9,  Y -> data[ch]

All fields little-endian.

 Offset Size  Field
 0      2     sync          0xA5 0x5A
 2      1     version       1
 3      1     ch_mask       bit k = channel k present (CH1 = bit 0)
 4      2     seq           packet counter (uint16, wraps) -> drop detection
 6      8     sample_idx    index of first sample since stream start (uint64)
 14     8     t0_unix       time of first sample, float64 seconds (Unix epoch)
 22     4     dt_ns         sample period in nanoseconds (uint32)
 26     2     n             samples per channel in this block (uint16)
 28     4*C   scale[C]      float32 per present channel (engineering units / LSB)
 ..     2*N*C data          int16, channel-major: all N of first ch, then next ...
 end    2     crc16         CRC-16/XMODEM over everything before it

Size for 4 ch x 100 samples: 28 + 16 + 800 + 2 = 846 bytes
(the same data as float64 would be 3200 bytes).
"""
from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass, field

import numpy as np


SYNC = b"\xA5\x5A"
VERSION = 1
MAX_CH = 8
_HDR = struct.Struct("<2sBBHQdIH")   # 28 bytes
HEADER_SIZE = _HDR.size
CRC_SIZE = 2


class PacketError(ValueError):
    """Raised when a packet is malformed or fails its CRC."""


def crc16(data: bytes) -> int:
    """CRC-16/XMODEM (poly 0x1021, init 0). Same algorithm is easy on ESP32."""
    return binascii.crc_hqx(data, 0)


@dataclass
class WaveBlock:
    """Decoded block - one LabVIEW-style waveform per channel."""
    seq: int
    sample_idx: int
    t0: float                       # seconds, Unix epoch
    dt: float                       # seconds
    channels: list[int]             # 0-based channel numbers present
    scales: np.ndarray              # float32, one per present channel
    raw: np.ndarray                 # int16, shape (C, N)
    y: np.ndarray = field(init=False)   # float64, shape (C, N), engineering units

    def __post_init__(self):
        self.y = self.raw.astype(np.float64) * self.scales[:, None].astype(np.float64)

    @property
    def n(self) -> int:
        return self.raw.shape[1]

    def time_axis(self) -> np.ndarray:
        """Absolute timestamps of each sample (s)."""
        return self.t0 + np.arange(self.n) * self.dt


def scale_for_range(full_scale: float) -> float:
    """Pick the LSB size so +/-full_scale maps onto int16 (-32767..32767)."""
    return float(full_scale) / 32767.0


def quantize(values: np.ndarray, scale: float) -> np.ndarray:
    """float -> int16 with rounding and clamping (saturates like a real ADC)."""
    raw = np.rint(np.asarray(values, dtype=np.float64) / scale)
    return np.clip(raw, -32768, 32767).astype("<i2")


def encode(seq: int, sample_idx: int, t0: float, dt: float,
           channels: list[int], data: np.ndarray, scales: list[float]) -> bytes:
    """
    Build one packet.
    data   : float array shape (C, N) in engineering units, C == len(channels)
    scales : LSB size per channel (use scale_for_range)
    """
    data = np.atleast_2d(np.asarray(data, dtype=np.float64))
    if data.shape[0] != len(channels) or len(scales) != len(channels):
        raise ValueError("channels, scales and data rows must match")
    n = data.shape[1]
    if not 0 < n <= 0xFFFF:
        raise ValueError("block size must be 1..65535 samples")
    mask = 0
    for ch in channels:
        if not 0 <= ch < MAX_CH:
            raise ValueError(f"channel {ch} out of range")
        mask |= 1 << ch
    order = sorted(range(len(channels)), key=lambda i: channels[i])  # ascending ch
    dt_ns = int(round(dt * 1e9))

    parts = [_HDR.pack(SYNC, VERSION, mask, seq & 0xFFFF, sample_idx, t0, dt_ns, n),
             np.asarray([scales[i] for i in order], dtype="<f4").tobytes()]
    for i in order:
        parts.append(quantize(data[i], scales[i]).tobytes())
    body = b"".join(parts)
    return body + struct.pack("<H", crc16(body))


def decode(buf: bytes) -> WaveBlock:
    """Parse and verify one packet. Raises PacketError on any problem."""
    if len(buf) < HEADER_SIZE + CRC_SIZE:
        raise PacketError(f"too short ({len(buf)} B)")
    sync, ver, mask, seq, idx, t0, dt_ns, n = _HDR.unpack_from(buf, 0)
    if sync != SYNC:
        raise PacketError("bad sync")
    if ver != VERSION:
        raise PacketError(f"unsupported version {ver}")
    channels = [k for k in range(MAX_CH) if mask >> k & 1]
    c = len(channels)
    expected = HEADER_SIZE + 4 * c + 2 * n * c + CRC_SIZE
    if len(buf) != expected:
        raise PacketError(f"length {len(buf)} != expected {expected}")
    (crc_rx,) = struct.unpack_from("<H", buf, expected - CRC_SIZE)
    if crc16(buf[:expected - CRC_SIZE]) != crc_rx:
        raise PacketError("CRC mismatch")
    scales = np.frombuffer(buf, dtype="<f4", count=c, offset=HEADER_SIZE)
    raw = np.frombuffer(buf, dtype="<i2", count=n * c,
                        offset=HEADER_SIZE + 4 * c).reshape(c, n)
    return WaveBlock(seq, idx, t0, dt_ns * 1e-9, channels, scales.copy(), raw.copy())
