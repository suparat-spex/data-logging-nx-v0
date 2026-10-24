# Packet format (wavepacket v1)

One MQTT message = one block of N samples for up to 16 channels, int16 + a scale per
channel (STIM318 style). Implemented in `src/nxlogger/core/wavepacket.py`; the tests in
`tests/test_wavepacket.py` pin this contract.

All fields little-endian.

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | sync `A5 5A` |
| 2 | 1 | version = 1 |
| 3 | 1 | channel mask (bit 0 = CH1) |
| 4 | 2 | seq (uint16, wraps) |
| 6 | 8 | sample_idx of first sample (uint64) |
| 14 | 8 | t0, Unix seconds (float64) |
| 22 | 4 | dt in ns (uint32) |
| 26 | 2 | N samples per channel (uint16) |
| 28 | 4×C | scale per present channel (float32, units per LSB) |
| … | 2×N×C | data int16, channel-major (all CH1, then CH2 …) |
| end | 2 | CRC-16/XMODEM of all preceding bytes |

`value = raw × scale`. 4 ch × 100 samples = 846 B (as float64: 3200 B).

The receiver detects lost packets (sequence gaps → line breaks in the plot), CRC errors,
malformed packets and publisher restarts (sample_idx going backwards).

## Matching implementations

- **ESP32 (C):** CRC-16/XMODEM = poly 0x1021, init 0x0000, no reflection
  (check value: `"123456789"` → `0x31C3`). If the ESP32 has no real-time clock, send
  t0 = 0 and let the PC derive time from `sample_idx × dt`.
- **LabVIEW:** Unflatten From String with byte order = little-endian; read the 28-byte
  header as a cluster, then the scale array (SGL) and data (I16); multiply and
  Build Waveform (t0, dt, Y).
