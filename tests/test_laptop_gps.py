"""The laptop's own location (hondalog.geo.laptop) when no handheld GPS is
connected, with a fake position source: no Windows needed."""

import threading
import time

import numpy as np
import pytest

from hondalog import addressmap
from hondalog.data import open_log
from hondalog.geo import laptop
from hondalog.geo.laptop import OFF_TEXT, Fix, LaptopGps, PowerShellBackend, laptop_columns
from hondalog.geo.track import GPS_STREAM, gps_columns
from hondalog.ui import adapters as ad
from hondalog.ui import wired
from hondalog.ui.session import build_session

PEARL = (32.2746, -90.1320)


class FakeBackend:
    """A new fix every read (moving north), or none with ``reason``."""

    name = "fake"

    def __init__(self, fixes=True, reason=""):
        self.fixes = fixes
        self.reason = reason
        self.n = 0
        self.closed = threading.Event()

    def open(self):
        pass

    def read(self, timeout):
        time.sleep(min(timeout, 0.05))
        if not self.fixes:
            return None
        self.n += 1
        return Fix(PEARL[0] + self.n * 1e-4, PEARL[1], 1.7e9 + self.n, speed_kmh=36.0,
                   heading=0.0, alt_m=90.0, acc_m=25.0)

    def close(self):
        self.closed.set()


def pump_until(session, cond, seconds=8.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        session.pump(session.bus.now())
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture()
def make_session(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    addressmap.load_user_map()
    made = []

    def make(backend):
        monkeypatch.setattr(laptop, "default_backend", lambda: backend)
        session = build_session(log_dir=tmp_path / "logs")
        made.append(session)
        return session

    yield make
    for s in made:
        s.close()


def gps_source(session):
    return next(s for s in session.extra_sources if isinstance(s, wired.LaptopGpsSource))


def test_fix_values_are_the_gps_streams_columns_plus_altitude_and_accuracy():
    names = [c.name for c in laptop_columns()]
    assert names[:len(gps_columns())] == [c.name for c in gps_columns()]   # views read by position
    assert names[-2:] == ["gps_alt", "gps_acc"]
    v = Fix(32.0, -90.0, 1.7e9, acc_m=12.0).values()
    assert set(v) == set(names) and v["gps_speed"] is None and v["gps_fix"] == 1.0
    assert not Fix(0.0, 0.0, 1.7e9).valid                                    # no fix yet, not a place


def test_idle_fixes_reach_the_bus_and_the_status_says_laptop(make_session):
    s = make_session(FakeBackend())
    s.start()
    assert pump_until(s, lambda: s.bus.channel("gps_lat") is not None and s.bus.channel("gps_lat").gen >= 2)
    lat = s.bus.channel("gps_lat")
    assert lat.spec.source == "GPS" and abs(lat.value - PEARL[0]) < 0.01
    assert s.bus.channel("gps_speed").value == pytest.approx(36.0)
    src = gps_source(s)
    assert src.info_text() == "GPS: laptop ±25 m"
    assert src.status().state == ad.OK


def test_connected_fixes_are_recorded_in_the_gps_stream_as_laptop(make_session):
    s = make_session(FakeBackend())
    s.start()
    s.connect("sim:")
    assert pump_until(s, lambda: s.ecu_status.state == ad.OK and s.recording.active)
    src = gps_source(s)
    start = src.gps.fixes
    assert pump_until(s, lambda: src.gps.fixes >= start + 3)
    path = s.recording.path
    s.recording.stop()
    with open_log(path) as log:
        gps = log.streams[GPS_STREAM]
        assert gps.info.source == "laptop"
        assert [c.name for c in gps.info.columns] == [c.name for c in laptop_columns()]
        assert gps.rows >= 2
        assert np.all(gps.column("gps_acc") == 25.0)
        assert np.all(np.abs(gps.column("gps_lat") - PEARL[0]) < 0.01)
        assert np.all(np.diff(gps.t) > 0)
    assert s.bus.channel("gps_lat").spec.source == "GPS"


def test_no_fix_records_nothing_and_says_why(make_session):
    s = make_session(FakeBackend(fixes=False, reason=OFF_TEXT))
    s.start()
    s.connect("sim:")
    assert pump_until(s, lambda: s.ecu_status.state == ad.OK and s.recording.active)
    time.sleep(0.4)
    src = gps_source(s)
    assert src.info_text() == "GPS: " + OFF_TEXT
    assert "Privacy & security > Location" in src.info_text()
    assert src.status().state == ad.CONNECTING and src.gps.fixes == 0
    assert s.bus.channel("gps_lat") is None
    path = s.recording.path
    s.recording.stop()
    with open_log(path) as log:
        assert GPS_STREAM not in log.streams


def test_cyd_ports_do_not_start_it(make_session):
    backend = FakeBackend()
    s = make_session(backend)
    s.start()
    src = gps_source(s)
    assert src.gps.running
    s.connect("cyd:sim")
    assert not src.gps.running and backend.closed.is_set()
    assert src.info_text() == ""
    s.disconnect()
    assert src.gps.running                          # idle again: back to the laptop


def test_no_backend_off_windows_explains_itself(monkeypatch):
    monkeypatch.setattr(laptop.sys, "platform", "linux")
    monkeypatch.delenv("HONDALOG_LAPTOP_GPS", raising=False)
    assert laptop.default_backend() is None
    gps = LaptopGps(backend=None)
    gps.start()
    assert not gps.running and "not available" in gps.status


def test_powershell_lines_parse_without_inventing_a_fix():
    ps = PowerShellBackend()
    assert ps.parse('{"status":"Disabled","permission":"Granted","unknown":true}') is None
    assert ps.reason == OFF_TEXT
    assert ps.parse('{"status":"Ready","permission":"Denied","unknown":true}') is None
    assert "desktop apps" in ps.reason
    assert ps.parse('{"status":"Initializing","permission":"Granted","unknown":true}') is None
    fix = ps.parse('{"status":"Ready","permission":"Granted","unknown":false,"lat":32.27,"lon":-90.13,'
                   '"speed":"NaN","course":"NaN","alt":"0","acc":"40","utc":1700000000.5}')
    assert fix is not None and fix.speed_kmh is None and fix.heading is None and fix.acc_m == 40.0
    assert ps.reason == ""
