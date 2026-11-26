"""The handheld on USB next to the laptop's own KKL cable (hondalog.handheld.companion):
its GPS on the laptop's GPS path, recorded as source "handheld", winning over the
laptop's location; discovery that never takes the ECU's port. A fake handheld
(tests/hhfake.py) stands in for the CYD with firmware 0.1.0."""

from __future__ import annotations

import time

import numpy as np
import pytest

from hhfake import PEARL, FakeHandheld
from hondalog import addressmap
from hondalog.data import open_log
from hondalog.geo.laptop import Fix
from hondalog.geo.track import GPS_STREAM, gps_columns
from hondalog.handheld import companion as C
from hondalog.handheld import serial_link as SL
from hondalog.handheld import serial_proto as P
from hondalog.live import LiveSession


def wait(cond, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture(scope="module")
def amap():
    return addressmap.load_default()


def test_same_port_and_spec():
    assert C.same_port("cyd:COM22", "com22") and C.same_port("COM22@460800", "COM22")
    assert not C.same_port("COM22", "COM21") and not C.same_port("", "")
    assert C.spec_port("COM22") == "cyd:COM22" and C.spec_port("cyd:sim") == "cyd:sim"


def test_auto_never_takes_the_ecus_port(monkeypatch):
    found = [("cyd:COM21", "CYD (serial)  COM21  (CH340 1a86:7523)"),
             ("cyd:COM22", "CYD (serial)  COM22  (CH340 1a86:7523)")]
    monkeypatch.setattr(SL, "list_ports", lambda: list(found))
    assert C.find_handheld("COM21") == "cyd:COM22"
    assert C.find_handheld("COM5") == "cyd:COM21"
    found.pop()
    assert C.find_handheld("COM21") is None


def test_named_port_that_is_the_ecus_is_refused(amap):
    hh = C.HandheldCompanion("COM21", amap, exclude="COM21")
    hh.start()
    assert hh.ready.wait(2)
    hh.stop()
    assert hh.hello is None and "ECU" in hh.status_text()


def test_auto_finds_the_ch340_and_reads_its_gps(amap, monkeypatch):
    fake = FakeHandheld()
    monkeypatch.setattr(SL, "list_ports", lambda: [("cyd:COM22", "CYD (serial)  COM22")])
    monkeypatch.setattr(SL, "make_port", lambda name: fake if name == "cyd:COM22" else None)
    got = []
    hh = C.HandheldCompanion("auto", amap, exclude="COM21", on_fix=lambda t, f: got.append((t, f)))
    hh.start()
    try:
        assert wait(lambda: hh.hello is not None and len(got) >= 3)
        assert hh.found == "cyd:COM22" and hh.hello.fw == "0.1.0" and hh.two_way
        assert hh.gps_ok and hh.gps_text() == "GPS: handheld (9 sats)"
        t, fix = got[-1]
        assert abs(fix["gps_lat"] - PEARL[0]) < 0.01 and fix["gps_fix"] == 1 and fix["gps_sats"] == 9
        # the heartbeat that shows PC on the handheld, and the clock exchange
        assert wait(lambda: len(fake.sent(P.SC_TIME_REQ)) >= 2)
        assert {t for t, _, _ in fake.frames} <= {P.SC_TIME_REQ, P.SC_GET_HELLO}
    finally:
        hh.stop()


def test_no_fix_is_not_passed_on(amap):
    fake = FakeHandheld(fix=False, sats=3)
    got = []
    hh = C.HandheldCompanion(fake, amap, on_fix=lambda t, f: got.append(f))
    hh.start()
    try:
        assert wait(lambda: fake.gps_sent >= 5 and hh.sats == 3)
        assert not got and not hh.gps_ok and hh.gps_text() == ""
    finally:
        hh.stop()


def test_live_session_records_handheld_gps_and_drops_laptop_fixes(amap, tmp_path):
    fake = FakeHandheld()
    live = LiveSession(amap, out_dir=tmp_path, handheld=fake)
    seen = []
    live.on_gps = lambda t, fix: seen.append(fix)
    live.connect("sim:")
    try:
        assert live.handheld is not None and live.handheld.exclude == "sim:"
        assert wait(lambda: live.poller.state == "ok")
        live.start_recording()
        assert wait(lambda: live.handheld.fixes >= 10)
        assert live.handheld_gps
        n = len(seen)
        live.laptop_fix(Fix(40.0, -80.0, 1.7e9, acc_m=25.0))       # the handheld has a fix: ignored
        assert all(abs(f["gps_lat"] - 40.0) > 1 for f in seen[n:])
        time.sleep(0.3)
        path = live.stop_recording().path
    finally:
        live.close()
    with open_log(path) as log:
        gps = log.streams[GPS_STREAM]
        assert gps.info.source == "handheld"
        assert [c.name for c in gps.info.columns] == [c.name for c in gps_columns()]
        assert gps.rows >= 5
        assert np.all(np.abs(gps.column("gps_lat") - PEARL[0]) < 0.01)
        assert np.all(np.diff(gps.t) >= 0)
        assert log.meta.get("handheld", {}).get("firmware") == "0.1.0"


def test_laptop_location_wins_without_a_handheld_fix(amap, tmp_path):
    fake = FakeHandheld(fix=False)
    live = LiveSession(amap, out_dir=tmp_path, handheld=fake)
    seen = []
    live.on_gps = lambda t, fix: seen.append(fix)
    live.connect("sim:")
    try:
        assert wait(lambda: live.handheld.hello is not None and not live.handheld.starting)
        assert not live.handheld_gps
        live.laptop_fix(Fix(40.0, -80.0, 1.7e9, acc_m=25.0))
        assert seen and seen[-1]["gps_lat"] == pytest.approx(40.0)
    finally:
        live.close()


def test_cyd_port_opens_no_second_link(amap, tmp_path):
    live = LiveSession(amap, out_dir=tmp_path, handheld="auto")
    live.connect("cyd:sim", start=False)
    assert live.handheld is None
    live.close()


def test_off_opens_nothing(amap, tmp_path):
    live = LiveSession(amap, out_dir=tmp_path, handheld="off")
    live.connect("sim:")
    try:
        assert live.handheld is None
    finally:
        live.close()


def test_app_shows_handheld_gps_in_the_info_bar(tmp_path, monkeypatch):
    from hondalog.ui import wired
    from hondalog.ui.session import build_session

    class NoBackend:
        name = "none"
        reason = ""

        def open(self):
            pass

        def read(self, timeout):
            time.sleep(min(timeout, 0.05))
            return None

        def close(self):
            pass

    from hondalog.geo import laptop
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(laptop, "default_backend", lambda: NoBackend())
    addressmap.load_user_map()
    fake = FakeHandheld()
    s = build_session(log_dir=tmp_path / "logs", handheld=fake)
    try:
        s.start()
        s.connect("sim:")
        src = next(x for x in s.extra_sources if isinstance(x, wired.LaptopGpsSource))

        def ready():
            s.pump(s.bus.now())
            ch = s.bus.channel("gps_lat")
            return ch is not None and ch.value is not None and src.info_text() == "GPS: handheld (9 sats)"
        assert wait(ready, 8)
        assert abs(s.bus.channel("gps_lat").value - PEARL[0]) < 0.01
        assert "handheld HH-1373A0 fw 0.1.0" in s.ecu.link_text()
        assert "handheld: firmware too old for relay" in s.ecu.link_text()     # fw 0.1.0 has no capability 64
        assert s.ecu.take_notice() == "Handheld: firmware too old for relay"
        assert not s.ecu.handheld_map_differs()
        assert src.status().detail == "GPS: handheld (9 sats)"
    finally:
        s.close()


def test_main_has_the_handheld_option():
    from hondalog import __main__ as M
    src = open(M.__file__, encoding="utf-8").read()
    assert "--handheld" in src and "handheld=args.handheld" in src
