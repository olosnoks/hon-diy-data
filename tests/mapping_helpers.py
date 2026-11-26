"""A scriptable fake ECU RAM image for the mapping tests.

It uses the same encodings as the default address map (and the simulator), plus
a few decoys, so a recording made from it decodes to the numbers put in.
"""

import numpy as np

from hondalog.mapping.sweeps import SweepRecording
from hondalog.sim import _temp_raw


def ram(rpm=0.0, tps=0.0, map_kpa=101.0, baro=101.0, ect=20.0, iat=20.0, batt=12.4, o2=0.05,
        inj=0.0, ign=0.0, vss=0.0, stft=0.0, ltft=0.0, iacv=0.0, mil=0, closed_loop=0, ac=0,
        noise=0, brake=0, starter=0, ac_clutch=None, fuel_pump=None):
    r = np.full(256, 0x55, dtype=np.int16)
    u8 = lambda v: max(0, min(255, int(round(v))))                       # noqa: E731
    x = max(0, min(0xFFFF, int(round(rpm * 4))))
    r[0x00], r[0x01] = x >> 8, x & 0xFF
    r[0x02] = u8(vss)
    # flag bytes as seen on the 2000 Civic EX P2P: 08 b7 and b2 (PSP switch) set at
    # rest, 0A b4 always and b7 running, 0B b6 running
    running = rpm > 100
    if ac_clutch is None:
        ac_clutch = ac and running
    if fuel_pump is None:
        fuel_pump = running
    r[0x08] = 0x84 | (0x02 if ac else 0) | (0x08 if brake else 0) | (0x01 if starter else 0)
    r[0x0A] = 0x10 | (0x80 if running else 0)
    r[0x0B] = ((0x40 if running else 0) | (0x20 if mil else 0) | (0x02 if ac_clutch else 0)
               | (0x01 if fuel_pump else 0))
    r[0x0F] = 0x01 if closed_loop else 0x00
    r[0x10], r[0x11] = _temp_raw(ect), _temp_raw(iat)
    r[0x12], r[0x13] = u8((map_kpa + 5) / 0.716), u8((baro + 5) / 0.716)
    r[0x14] = u8(tps * 2.55)                      # the OBD2 PID 11 byte on the P2P
    r[0x15] = u8(o2 * 51.3)
    r[0x17] = u8(batt * 10.45)
    r[0x18], r[0x19] = u8(40 * 2.55), u8((77.06 - 22.0) * 2.5371)
    r[0x20] = u8((stft / 100 + 1) * 128)
    r[0x21] = r[0x22] = u8((ltft / 100 + 1) * 128)
    y = int(round(inj * 250))
    r[0x24], r[0x25] = y >> 8, y & 0xFF
    r[0x26] = r[0x27] = u8(ign * 4 + 24)
    r[0x28] = u8(iacv * 2.55)
    r[0x3C] = 0
    # decoys and hidden copies for the finder
    r[0x80] = noise & 0xFF                        # a free-running counter
    r[0x90], r[0x91] = x & 0xFF, x >> 8           # RPM again, little-endian
    r[0xA0] = u8(tps * 2 + 24)                    # throttle on another scale (kerpz's)
    r[0xB0] = 0x80 | (0x10 if ac else 0)          # A/C again, bit 4, other bits set
    return r


def recording(rows, dt=0.2, t0=0.0, marks=()):
    rec = SweepRecording()
    for i, row in enumerate(rows):
        rec.append(row, np.full(256, t0 + i * dt))
    rec.marks.extend(marks)
    return rec


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def feed(session, clock, rows, dt=0.2):
    """Push RAM rows into a session as sweeps, advancing its clock."""
    for row in rows:
        clock.t += dt
        session.add_sweep(row)
