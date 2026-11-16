"""The handheld's GPS: the simulator's car on the Pearl loop, and live fixes
over cyd: reaching the app's bus (what the Map screen draws)."""

import time

import numpy as np

from hondalog import addressmap
from hondalog.geo import proj, route
from hondalog.handheld import format as F
from hondalog.handheld.cydsim import CydSim


def fixes(sim, t0, t1):
    out = []
    for t in np.arange(t0, t1, 1.0):
        t_us, lat, lon, utc, speed, course, fix, sats, hdop = F.REC_GPS.unpack(sim.gps_fix(float(t)))
        out.append((t_us / 1e6, lat / 1e7, lon / 1e7, speed * 0.036, course / 100.0, fix, sats))
    return np.array(out)


def test_the_simulators_car_drives_the_pearl_loop_and_stops_at_a_stall():
    sim = CydSim(unix_time=1794729600.0)
    a = fixes(sim, 1.0, 400.0)
    loop = route.PEARL_LOOP
    assert (a[:, 5] == 1).all() and (a[:, 6] >= 4).all()
    assert loop.lat.min() - 0.001 < a[:, 1].min() and a[:, 1].max() < loop.lat.max() + 0.001
    assert loop.lon.min() - 0.001 < a[:, 2].min() and a[:, 2].max() < loop.lon.max() + 0.001
    step = proj.haversine_m(a[:-1, 1], a[:-1, 2], a[1:, 1], a[1:, 2])
    assert step.max() < 30.0 and step.sum() > 2500.0                        # it drives, it does not jump
    assert 20 < a[:, 3].mean() < 70
    sim.model.stall()
    b = fixes(sim, 400.0, 460.0)
    assert b[-20:, 3].max() < 0.5                                           # rolled to a halt
    moved = proj.haversine_m(b[-20, 1], b[-20, 2], b[-1, 1], b[-1, 2])
    assert moved < 8.0                                                      # and stays (GPS noise only)


def test_live_fixes_reach_the_bus(tmp_path, monkeypatch):
    from hondalog.ui import adapters as ad
    from hondalog.ui.session import build_session
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    addressmap.load_user_map()
    session = build_session(log_dir=tmp_path / "logs")
    session.start()
    session.connect("cyd:sim")
    try:
        end = time.monotonic() + 10
        lat = None
        while time.monotonic() < end:
            session.pump(session.bus.now())
            lat = session.bus.channel("gps_lat")
            if session.ecu_status.state == ad.OK and lat is not None and lat.gen >= 2:
                break
            time.sleep(0.05)
        assert lat is not None and lat.gen >= 2, "no GPS fix on the bus"
        lon = session.bus.channel("gps_lon")
        assert lat.spec.source == "GPS" and not lat.stale(session.bus.now())
        loop = route.PEARL_LOOP
        assert loop.lat.min() - 0.001 < lat.value < loop.lat.max() + 0.001
        assert loop.lon.min() - 0.001 < lon.value < loop.lon.max() + 0.001
        assert session.bus.channel("gps_fix").value == 1.0
        t, v = session.bus.history("gps_lat")
        assert len(t) >= 2 and np.all(np.diff(t) > 0.5)                     # about once a second
    finally:
        session.close()
