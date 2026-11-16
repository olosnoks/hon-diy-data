"""The handheld's clock from the GPS pulse, laptop side: the compiled engine (hh_pps.c in hh_core.c) is fed a
simulated pulse and sentences, and what comes out is read with the laptop's code: the drive file's TIME records,
the serial link's hello and stats (new and older-firmware shapes), and the wording on screen. The filter itself is
tested in C (firmware/handheld/test/host/hhpps.c)."""

from __future__ import annotations

import struct

import pytest

import hhhost
from hondalog import addressmap
from hondalog.handheld import format as F
from hondalog.handheld import serial_proto as P
from hondalog.handheld.drivefile import read_drive_file
from hondalog.handheld.mapfile import build_map

U0 = 1794746119                    # 2026-11-15 12:35:19 UTC


@pytest.fixture(scope="session")
def amap():
    return addressmap.load_default()


@pytest.fixture(scope="session")
def L(tmp_path_factory):
    lib = hhhost.lib(tmp_path_factory.mktemp("hhhost"))
    if lib is None:
        pytest.skip("no C compiler")
    return lib


def _second(h, k, t0=5.0, pulse=True):
    """Second k of the run: the pulse at device time t0 + k, GGA at +120 ms, RMC at +650 ms."""
    t = t0 + k
    h.run_until(t)
    if pulse:
        h.pps_edge(round(t * 1e6) + (k * 7) % 9)        # a few microseconds of interrupt jitter
    sec = (U0 + k) % 60
    h.run_until(t + 0.12)
    h.gps_at(f"GPGGA,1235{sec:02d},3217.100,N,09007.200,W,1,08,0.9,95.4,M,-29.0,M,,", round((t + 0.12) * 1e6))
    h.run_until(t + 0.65)
    h.gps_at(f"GPRMC,1235{sec:02d},A,3217.100,N,09007.200,W,022.4,084.4,151126,003.1,W", round((t + 0.65) * 1e6))
    h.run_until(t + 0.9)


def test_drive_file_has_pps_time_records(L, tmp_path, amap):
    h = hhhost.Handheld(L, tmp_path / "sd", map_bytes=build_map(amap), cfg="prealloc_mb = 0\n")
    h.serial()
    h.run_until(3.0)
    for k in range(3):                                   # sentences alone first: the old behaviour, a TIME 1
        _second(h, k, pulse=False)
    for k in range(3, 20):
        _second(h, k)
    h.run_until(30.0)
    h.close()
    df = read_drive_file(tmp_path / "sd" / "DRIVES" / "D000001.HHD")
    sources = [src for _, _, src in df.times]
    assert sources[0] == F.TIME_GPS and F.TIME_PPS in sources
    first_pps = next(t for t in df.times if t[2] == F.TIME_PPS)
    # a PPS record is exact: the wall time of its device time, to tens of microseconds
    t_us, unix_us, _ = first_pps
    assert unix_us == pytest.approx(U0 * 1e6 + (t_us - 5.0e6), abs=60)
    # and the epoch of the file (what the importer uses) is built from it, not from an older sentence time
    assert df.epoch_unix == pytest.approx(U0 + (df.t0_us / 1e6 - 5.0), abs=1e-4)
    # the record's reserved bytes now carry the error estimate and the drift
    raw = open(tmp_path / "sd" / "DRIVES" / "D000001.HHD", "rb").read()
    assert struct.pack("<QqB", t_us, unix_us, F.TIME_PPS) in raw
    at = raw.index(struct.pack("<QqB", t_us, unix_us, F.TIME_PPS))
    _, _, err_us, drift_ppb = struct.unpack_from("<BBHi", raw, at + 16)
    assert 1 <= err_us < 100 and abs(drift_ppb) < 20000


def test_old_readers_ignore_what_they_do_not_know():
    """A TIME record of source 4 read the old way (source, 7 opaque bytes) still parses, and a PPS record counts
    as a GPS time for the file's epoch."""
    rec = struct.pack("<QqBBHi", 5_000_000, U0 * 1_000_000, F.TIME_PPS, 0, 12, 3000)
    assert len(rec) == 24
    t, unix, source, rest = F.REC_TIME.unpack(rec)
    assert (t, unix, source) == (5_000_000, U0 * 1_000_000, 4) and len(rest) == 7
    from hondalog.handheld.drivefile import DriveFile
    base = dict(path="x.HHD", kind=F.FK_DRIVE, file_no=1, boot_no=1, flags=0, backend=0, link_mode=0, nonce=1, t0_us=2_000_000,
                unix_us_at_t0=0, map_hash="", map_sha=b"", fw="", device="", ecu_id=b"", drive_no=0, reason=0,
                trigger_t_us=0, prealloc=0)
    # newest GPS-class record wins over an older one, whichever kind it is
    df = DriveFile(**base, times=[(3_000_000, (U0 - 5) * 1_000_000, 1), (5_000_000, U0 * 1_000_000, 4)])
    assert df.epoch_unix == pytest.approx(U0 - 3.0)
    # ... and a clock-as-it-was record (2) or a laptop one (3) does not displace a GPS one
    df = DriveFile(**base, times=[(5_000_000, U0 * 1_000_000, 4), (6_000_000, (U0 + 9) * 1_000_000, 2)])
    assert df.epoch_unix == pytest.approx(U0 - 3.0)
    df = DriveFile(**base, times=[(5_000_000, U0 * 1_000_000, 1), (6_000_000, (U0 + 1) * 1_000_000, 4)])
    assert df.epoch_unix == pytest.approx(U0 + 1 - 4.0)


def test_stats_old_and_new_shapes():
    old = P.pack_stats(t_us=1, unix_us=2, reads=3)
    assert len(old) == P.STATS.size == 96
    st = P.parse_stats(old)
    assert st["reads"] == 3 and st["time_source"] == P.TS_NONE and st["time_err_us"] == 0
    new = P.pack_stats(t_us=1, unix_us=2, reads=3, time_source=P.TS_PPS, time_err_us=18)
    assert len(new) == 104 and new[:96] == old
    st = P.parse_stats(new)
    assert st["time_source"] == P.TS_PPS and st["time_err_us"] == 18 and st["reads"] == 3
    # a reader that only knows the first 96 bytes (the unpack of the old code) sees the same fields
    assert dict(zip(P.STATS_FIELDS, P.STATS.unpack(new[:96])))["unix_us"] == 2
    assert P.parse_stats(new[:95]) is None


def test_time_text():
    assert P.time_text(P.TS_PPS, 20) == "PPS ±20µs"
    assert P.time_text(P.TS_PPS, 2500) == "PPS ±2.5ms"
    assert P.time_text(P.TS_NMEA, 1_000_000) == "GPS ±1s"
    assert P.time_text(P.TS_HOST, 0) == "PC" and P.time_text(P.TS_NONE, 0) == "none"


def test_link_carries_the_time_source(L, tmp_path, amap):
    h = hhhost.Handheld(L, tmp_path / "sd", map_bytes=build_map(amap), cfg="prealloc_mb = 0\n")
    h.serial()
    data = bytearray()
    h.run_until(3.0)
    data += h.serial_poll()                              # a stats frame before any GPS: no clock
    for k in range(14):
        _second(h, k)
        data += h.serial_poll()
    h.run_until(20.5)
    data += h.serial_poll()
    d = P.Deframer()
    frames = d.feed(bytes(data))
    assert d.bad == 0
    stats = [P.parse_stats(p) for t, _, p in frames if t == P.SF_STATS]
    hellos = [P.parse_hello(p) for t, _, p in frames if t == P.SF_HELLO]
    assert stats[0]["time_source"] == P.TS_NONE and stats[-1]["time_source"] == P.TS_PPS
    assert 1 <= stats[-1]["time_err_us"] < 100 and stats[-1]["flags"] & P.ST_TIME
    assert hellos[-1].clock_from_pps and not hellos[0].clock_from_pps
    # (t_us, unix_us) of a frame is one instant: unix - t is the same offset in every STATS frame once locked
    offsets = {s["unix_us"] - s["t_us"] for s in stats[-4:]}
    assert max(offsets) - min(offsets) < 40
    assert offsets.pop() == pytest.approx(U0 * 1e6 - 5.0e6, abs=60)
    h.close()
