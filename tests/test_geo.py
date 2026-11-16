"""GPS without Qt: the projection, tracks (gaps, decimation, time <-> place),
the Pearl test loop, the SQL views, stall locations and their grouping."""

import math
import sqlite3

import numpy as np
import pytest

from hondalog.data import open_log
from hondalog.data.synth import write_gps_drives, write_synthetic
from hondalog.geo import proj, route
from hondalog.geo import stalls as gs
from hondalog.geo.track import GAP_S, Track, hold, log_waypoints

PEARL = (32.2730, -90.1320)


def straight(n=600, hz=1.0, kmh=36.0, gap_at=None, gap_s=20.0):
    """Due east from Pearl at a steady speed; optionally a gap with no fixes."""
    t = np.arange(n) / hz
    if gap_at is not None:
        t = np.where(t >= gap_at, t + gap_s, t)
    east = t * kmh / 3.6
    lat = np.full(n, PEARL[0])
    lon = PEARL[1] + np.degrees(east / (proj.MEAN_R * math.cos(math.radians(PEARL[0]))))
    return Track(t, lat, lon, np.full(n, kmh), np.full(n, 90.0))


# ---- projection ---------------------------------------------------------------

def test_unit_coordinates_round_trip_and_tiles():
    x, y = proj.to_unit(PEARL[1], PEARL[0])
    lon, lat = proj.from_unit(x, y)
    assert float(lon) == pytest.approx(PEARL[1], abs=1e-9) and float(lat) == pytest.approx(PEARL[0], abs=1e-9)
    # the OSM tile that holds Pearl at zoom 15, by the slippy-map formula of the OSM wiki
    z = 15
    n = 2 ** z
    wiki = (int((PEARL[1] + 180) / 360 * n),
            int((1 - math.asinh(math.tan(math.radians(PEARL[0]))) / math.pi) / 2 * n))
    assert (int(x * n), int(y * n)) == wiki == (8179, 13277)
    assert proj.metres_per_px(0.0, 0) == pytest.approx(156543.03, rel=1e-6)


def test_distances_and_scale_bar():
    # a degree of latitude is about 111.2 km
    assert float(proj.haversine_m(32.0, -90.0, 33.0, -90.0)) == pytest.approx(111195, rel=1e-3)
    assert float(proj.bearing_deg(32.0, -90.0, 32.0, -89.0)) == pytest.approx(90.0, abs=0.5)
    px, label = proj.scale_bar(10.0, 150.0)                 # 10 m a pixel, 150 px room
    assert label == "1 km" and px == pytest.approx(100.0)
    px, label = proj.scale_bar(10.0, 150.0, miles=True)
    assert label == "0.5 mi" and px == pytest.approx(80.4672)
    assert proj.scale_bar(0.5, 150.0, miles=True)[1] == "200 ft"
    lat, lon = proj.offset(32.27, -90.13, 1000.0, 0.0)
    assert float(proj.haversine_m(32.27, -90.13, lat, lon)) == pytest.approx(1000.0, rel=1e-3)


def test_fit_zoom_puts_a_box_inside_the_view():
    t = straight(600)
    x0, y0, x1, y1 = t.bbox()
    z = proj.fit_zoom(x0, y0, x1, y1, 960, 630)
    assert (x1 - x0) * proj.world_px(z) <= 960 - 2 * 40
    assert (x1 - x0) * proj.world_px(z + 1) > 960 - 2 * 40


# ---- tracks -------------------------------------------------------------------

def test_track_drops_no_fix_rows_and_sorts():
    t = [3.0, 1.0, 2.0, 4.0, 5.0]
    lat = [32.27, 32.27, np.nan, 0.0, 32.28]
    lon = [-90.13, -90.13, -90.13, 0.0, -90.12]
    tr = Track(t, lat, lon)
    assert list(tr.t) == [1.0, 3.0, 5.0]                    # NaN and (0, 0) left out
    assert tr.lat[-1] == 32.28


def test_gaps_break_the_line_and_have_no_position():
    tr = straight(300, gap_at=100.0, gap_s=30.0)
    assert tr.breaks[0] and int(tr.breaks[1:].sum()) == 1
    i = int(np.flatnonzero(tr.breaks[1:])[0]) + 1
    assert tr.t[i] - tr.t[i - 1] > GAP_S
    assert tr.at(tr.t[i - 1] + 2.0) is not None             # just after the last fix: held there
    assert tr.at(tr.t[i - 1] + 15.0) is None                # inside the gap: nowhere
    assert tr.length_m == pytest.approx(10.0 * 298, rel=0.01)    # the gap adds no distance


def test_time_to_place_and_place_to_time():
    tr = straight(600)
    pos = tr.at(100.5)
    assert pos is not None and pos.index == pytest.approx(100.5)
    assert pos.lon == pytest.approx((tr.lon[100] + tr.lon[101]) / 2)
    assert tr.at(-1.0) is None and tr.at(1e6) is None
    # a click between two fixes gives the time between theirs (click-to-cursor)
    t, d = tr.nearest(pos.x, pos.y + 1e-9)
    assert t == pytest.approx(100.5, abs=1e-3) and d < 1e-8
    # a click off the line by more than max_d is nothing
    assert tr.nearest(pos.x, pos.y + 1e-3, max_d=1e-6) is None


def test_sample_holds_a_channel_at_every_fix():
    tr = straight(10)
    v = tr.sample(np.array([0.0, 2.0, 2.5, 8.0]), np.array([1.0, 2.0, 3.0, 4.0]), max_age=1.0)
    assert list(v[:4]) == [1.0, 1.0, 2.0, 3.0] and np.isnan(v[4]) and v[8] == 4.0
    assert np.isnan(hold(np.array([5.0]), np.array([1.0]), np.array([1.0]))[0])


def test_decimation_is_bounded_by_the_length_in_pixels():
    t, lat, lon, kmh, heading = route.drive(route.PEARL_LOOP, 3600, hz=10, seed=7)
    tr = Track(t, lat, lon, kmh, heading)
    assert len(tr) == 36000
    for z in (12, 14, 16):
        idx = tr.lod(z)
        assert idx[0] == 0 and idx[-1] == len(tr) - 1 and np.all(np.diff(idx) > 0)
        length_px = tr.length_m / proj.metres_per_px(32.27, z)
        assert len(idx) <= length_px / 1.5 + 10 * (1 + tr.breaks.sum())
        # no kept point is closer than 1.5 px to the one before (except forced ones)
        xy = np.column_stack((tr.x[idx], tr.y[idx])) * proj.world_px(z)
        assert np.all(np.hypot(*np.diff(xy, axis=0).T)[:-1] >= 1.5 - 1e-9)
        assert tr.lod(z) is idx                             # cached
    assert len(tr.lod(12)) < len(tr.lod(14)) < len(tr.lod(16)) < len(tr)
    assert len(tr.lod(14)) < 4000                           # an hour at the fit zoom


def test_decimation_keeps_both_ends_of_a_gap():
    tr = straight(400, hz=10, gap_at=20.0, gap_s=40.0)
    idx = set(tr.lod(8).tolist())                           # at zoom 8 the whole track is a few px
    i = int(np.flatnonzero(tr.breaks[1:])[0]) + 1
    assert {0, i - 1, i, len(tr) - 1} <= idx


# ---- the test loop --------------------------------------------------------------

def test_the_pearl_loop_is_a_plausible_drive():
    loop = route.PEARL_LOOP
    assert 8000 < loop.length_m < 14000
    assert abs(loop.lat[0] - loop.lat[-1]) < 1e-12           # closed
    lat0, lon0 = loop.origin
    assert abs(lat0 - PEARL[0]) < 0.03 and abs(lon0 - PEARL[1]) < 0.05
    t, lat, lon, kmh, heading = route.drive(loop, 1800, hz=1, seed=3)
    assert kmh.max() <= 72 * 1.03 and 30 < kmh.mean() < 65   # under the limits, moving on average
    assert 0.03 < (kmh < 1).mean() < 0.3                     # it waits at lights and stop signs
    step = proj.haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:])
    assert step.max() < 30.0                                 # no teleporting: 72 km/h and a little noise
    # a stall: the car rolls to a halt and stays there
    t, lat, lon, kmh, heading = route.drive(loop, 120, hz=1, seed=3, stops_at={60.0: 40.0})
    assert kmh[75:100].max() < 0.5


# ---- the views ------------------------------------------------------------------

@pytest.fixture(scope="module")
def gps_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("gps") / "hondalog.db"
    refs = write_gps_drives(path, drives=4, duration_s=900)
    # a drive with no GPS and a stall: in stall_locations with no place
    plain = write_synthetic(path, 300, fast_hz=4, slow_hz=1, blackbox_hz=0, markers=0, seed=99,
                            started_unix=1794729600.0 - 86400 * 30, stalls=(120.0,))
    return path, refs, plain


def test_gps_track_view(gps_db):
    path, refs, plain = gps_db
    conn = sqlite3.connect(path)
    cols = [c[1] for c in conn.execute("PRAGMA table_info(gps_track)")]
    assert cols[:12] == ["drive_id", "drive", "t", "unix_time", "lat", "lon", "speed", "speed_mph", "heading",
                         "fix", "sats", "hdop"]
    rows = conn.execute("SELECT drive, count(*), sum(fix), min(sats), max(speed) FROM gps_track GROUP BY drive "
                        "ORDER BY drive").fetchall()
    assert [r[0] for r in rows] == sorted(r.name for r in refs)            # the drive without GPS has no rows
    assert all(r[1] == 900 and r[2] == 900 and r[3] >= 4 and 40 < r[4] < 80 for r in rows)
    with open_log(refs[0]) as log:
        lat = log.series("gps_lat")
        first = conn.execute("SELECT t, lat, lon FROM gps_track WHERE drive = ? ORDER BY t LIMIT 1",
                             (refs[0].name,)).fetchone()
        assert first == (pytest.approx(lat.t[0]), pytest.approx(lat.v[0]), pytest.approx(log.series("gps_lon").v[0]))
    plan = " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN SELECT * FROM gps_track WHERE drive = 'x'"))
    assert "USING INDEX" in plan and "SCAN g" not in plan


def test_stall_locations_view(gps_db):
    path, refs, plain = gps_db
    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT name, kind, reason, drive, gps_dt, lat, lon, speed, max_speed_30s "
                        "FROM stall_locations ORDER BY wall_unix").fetchall()
    assert len(rows) == 6                                   # 5 stalls and one manual capture
    nofix = [r for r in rows if r[3] == plain.name]
    assert len(nofix) == 1 and nofix[0][5] is None and nofix[0][1] == "stall"
    placed = [r for r in rows if r[3] != plain.name]
    assert all(abs(r[4]) <= 1.0 and r[5] is not None for r in placed)       # 1 Hz fixes: within a second
    for name, kind, reason, drive, dt, lat, lon, speed, top in placed:
        t = conn.execute("SELECT t_trigger FROM captures WHERE name = ?", (name,)).fetchone()[0]
        best = conn.execute("SELECT lat, lon FROM gps_track WHERE drive = ? ORDER BY abs(t - ?) LIMIT 1",
                            (drive, t)).fetchone()
        assert (lat, lon) == best
        assert top is None or top >= speed


def test_stall_pins_filters_and_groups(gps_db):
    path, refs, plain = gps_db
    pins = gs.stall_pins(path)
    assert len(pins) == 5 and sum(p.located for p in pins) == 4
    assert len(gs.stall_pins(path, kinds=None)) == 6
    assert [p.reason for p in gs.stall_pins(path, kinds=None, reason="manual")] == ["manual"]
    late = gs.stall_pins(path, since=1794729600.0 + 86400 * 4)
    assert {p.drive for p in late} == {r.name for r in refs[2:]}
    groups = gs.clusters(pins)
    assert [len(g) for g in groups] == [3, 1]                # three on the rough stretch, one on US 80
    lat, lon = gs.centre(pins, groups[0])
    rlat, rlon = route.PEARL_LOOP.point("rough stretch", 0.5)
    assert float(proj.haversine_m(lat, lon, rlat, rlon)) < 300
    text = gs.summary(pins)
    assert text.startswith("5 stalls, 4 with a place. 3 of them within") and "km/h" in text
    assert gs.reasons(path)[0] == ("stall", "rpm", 5)
    assert gs.stall_pins(path.parent / "nothing.db") == []


def test_waypoints_of_a_drive(gps_db):
    path, refs, plain = gps_db
    with open_log(refs[1]) as log:
        pins = log_waypoints(log)
    kinds = [w.kind for w in pins]
    assert kinds.count("marker") == 2 and kinds.count("stall") == 1 and kinds.count("capture") == 1
    stall = next(w for w in pins if w.kind == "stall")
    assert stall.ref.capture == stall.name and stall.name.startswith("stall_")
    assert not any(w.kind == "trigger" for w in pins)        # the captures stand for the trigger events
