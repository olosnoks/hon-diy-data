"""hondalog.analysis motion / power / events / derived / eventnotes on
synthetic drives (no real data, no GPS coordinates: headings and speeds only)."""

from datetime import datetime

import numpy as np
import pytest

from hondalog.analysis import derived, eventnotes, events, motion, power
from hondalog.analysis.motion import G, Car
from hondalog.data import Column, new_drive

CAR = Car()


# ---- motion ----------------------------------------------------------------

def test_car_constants_follow_the_analysis():
    assert CAR.mass_kg == 1195 and CAR.tyre_m == 0.287 and CAR.eta == 0.90
    assert CAR.road_load(0.0) == 0.0 and CAR.road_load(100 / 3.6) == pytest.approx(179 + 0.384 * (100 / 3.6) ** 2)
    assert CAR.road_load(100 / 3.6) == pytest.approx(475, abs=2)            # vehicle-dynamics.md: 475 N at 100 km/h
    assert CAR.ratio(2) == pytest.approx(8.40, abs=0.02) and np.isnan(CAR.ratio(0))
    assert CAR.m_eff(0) == pytest.approx(1195 + 2.6 / 0.287 ** 2)
    assert CAR.m_eff(1) - CAR.m_eff(0) == pytest.approx(333, rel=0.03)    # engine inertia through 1st
    assert CAR.m_eff(5) - CAR.m_eff(0) == pytest.approx(14, rel=0.1)
    assert CAR.with_(mass_kg=1250).mass_kg == 1250 and CAR.mass_kg == 1195


def test_resample_leaves_gaps_empty():
    t = np.array([0.0, 1.0, 2.0, 6.0, 7.0])
    v = np.array([0.0, 10.0, 20.0, 60.0, 70.0])
    out = motion.resample(t, v, np.array([0.5, 1.5, 4.0, 6.5, 8.0]))
    assert out[0] == 5.0 and out[1] == 15.0 and np.isnan(out[2]) and out[3] == 65.0 and np.isnan(out[4])
    assert list(motion.hold(t, v, np.array([1.2, 4.0]), max_age=1.5)) == [10.0, pytest.approx(np.nan, nan_ok=True)]


def test_derivative_matches_a_known_slope():
    dt = 0.1
    t = np.arange(0, 10, dt)
    v = 3.0 * t + 0.2 * t ** 2
    d = motion.derivative(v, dt, 1.5)
    assert d[50] == pytest.approx(3.0 + 0.4 * t[50], rel=1e-6) and np.isfinite(d).all()


def test_gear_from_the_coupling_test():
    dt = 0.1
    t = np.arange(0, 12, dt)
    vss = np.full(len(t), 40.0)
    rpm = np.where(t < 4, 40 * 76.8, np.where(t < 4.6, 2500.0, 40 * 49.7))      # 2nd, clutch in, 3rd
    rpm[t >= 9] = 40 * 49.7 * 1.08                                              # slipping: off the line by 8 %
    g = motion.gear(rpm, vss, dt)
    assert set(g[(t > 0.5) & (t < 3.5)]) == {2}
    assert set(g[(t > 5.0) & (t < 8.5)]) == {3}
    assert set(g[(t > 3.95) & (t < 4.7)]) == {0} and set(g[t > 9.5]) == {0}
    assert motion.gear(rpm, np.full(len(t), 5.0), dt).max() == 0                # below 8 km/h: never coupled
    assert motion.gear_name(2) == "2nd" and motion.gear_name(0) == "—" and motion.gear_name(4) == "4th"
    assert motion.decoupled(np.array([800.0]), np.array([60.0]))[0]


def test_longitudinal_accel_from_whole_km_per_h():
    dt = 0.1
    t = np.arange(0, 20, dt)
    true = 2.0 * t                                      # m/s, 2 m/s²
    vss = np.floor(true * 3.6 / CAR.vss_scale)          # the ECU counts whole km/h
    a = motion.longitudinal_accel(vss, dt)
    assert np.nanmedian(a[20:-20]) == pytest.approx(2.0, abs=0.05)


def test_lateral_accel_on_a_circle_with_the_gps_lag():
    t = np.arange(0, 60, 1.0)                            # GPS at 1 Hz
    speed_kmh = np.full(len(t), 36.0)                    # 10 m/s
    heading = (t * 6.0) % 360                            # 6 °/s clockwise, wrapping through north
    tl, lat = motion.lateral_accel(t, heading, speed_kmh)
    assert np.allclose(tl, t - 1.7)
    assert np.nanmedian(lat) == pytest.approx(10.0 * np.radians(6.0), rel=0.01)   # v · ω, right turn positive
    _, left = motion.lateral_accel(t, (360 - t * 6.0) % 360, speed_kmh)
    assert np.nanmedian(left) < 0
    _, slow = motion.lateral_accel(t, heading, np.full(len(t), 3.0))
    assert np.isnan(slow).all()
    x, y = motion.gg_trail(t, lat / G, np.zeros(len(t)), now=30.0, trail_s=5.0)
    assert len(x) == 6 and np.allclose(y, 0)


def test_fuel_flow_from_the_injector_pulse():
    gs, lph = motion.fuel_flow(np.array([2.0, 0.0, 1.0]), np.array([3000.0, 3000.0, 800.0]))
    cc_s = 4 * 240 / 60000 * 1.0 * 3000 / 120
    assert lph[0] == pytest.approx(cc_s * 3.6) and gs[0] == pytest.approx(cc_s * 0.745)
    assert lph[1] == 0.0 and lph[2] == 0.0                # fuel cut, and a pulse no longer than the dead time


def test_grade_estimate_is_zero_on_the_flat_and_sees_a_climb():
    n = 200
    v = np.full(n, 20.0)
    a = np.zeros(n)
    g = np.full(n, 4)
    flat_force = np.full(n, CAR.road_load(20.0))         # the engine just holds the speed
    k = 0.91
    flat = motion.grade_estimate(a, v, g, flat_force / k, None, CAR, k=k)
    assert np.nanmedian(flat) == pytest.approx(0.0, abs=0.01)
    climb_force = flat_force + CAR.mass_kg * G * np.sin(np.arctan(0.03))
    up = motion.grade_estimate(a, v, g, climb_force / k, None, CAR, k=k)
    assert np.nanmedian(up) == pytest.approx(3.0, abs=0.05)
    braking = motion.grade_estimate(a, v, g, flat_force / k, np.ones(n), CAR, k=k)
    assert np.isnan(braking).all()


# ---- power -----------------------------------------------------------------

def test_reference_curve_is_the_d16y8_rating():
    assert power.D16Y8.approximate and "approximate" in power.D16Y8.name
    assert power.reference_torque(5500) == pytest.approx(145.0)
    assert power.reference_power_hp(6600) == pytest.approx(127.0, abs=0.2)
    assert np.isnan(power.reference_torque(500))
    assert power.hp(145.0, 5500) == pytest.approx(112.0, abs=0.5)


def test_crank_torque_reproduces_the_wot_pull_of_the_analysis():
    # vehicle-dynamics.md §1.3: 2nd gear, 2.25 m/s² at about 3,100 rpm -> about 123 N·m
    rpm = 3100.0
    v = rpm / 76.8 * CAR.vss_scale / 3.6
    tq = float(power.crank_torque(2.25, v, 2, CAR))
    assert tq == pytest.approx(123, abs=4)
    lo, hi = power.torque_band(2.25, v, 2, CAR)
    assert lo < tq < hi and (hi - lo) / 2 / tq == pytest.approx(0.087, abs=0.02)
    assert np.isnan(power.crank_torque(2.25, v, 0, CAR))
    heavier = float(power.crank_torque(2.25, v, 2, CAR.with_(mass_kg=1295)))
    assert heavier > tq
    assert power.power_now(2.25, rpm / 76.8, 2, rpm, CAR)[0] == pytest.approx(tq, rel=1e-6)
    assert np.isnan(power.power_now(-1.0, 50.0, 2, 2000.0)[0])          # engine braking: no power reading


def synthetic_pull(tps_level=90.0, accel=2.2, gear_=2, seconds=4.0, lift=True, v0=10.0):
    """rpm, vss and tps at 12 Hz: cruise, a pull at constant acceleration in one gear, a lift."""
    hz = 12.0
    t = np.arange(0, 20, 1 / hz)
    per = CAR.gears[gear_]
    v_true = np.where(t < 5, v0, np.where(t < 5 + seconds, v0 + accel * (t - 5), v0 + accel * seconds))
    kmh = v_true * 3.6 / CAR.vss_scale
    rpm = kmh * per
    tps = np.where((t >= 5) & (t < 5 + seconds), tps_level, 15.0)
    if lift:
        tps[t >= 5 + seconds] = 8.0
    return t, rpm, np.floor(kmh), tps, np.full(len(t), 98.0)


def test_find_pulls_measures_torque_from_acceleration():
    t, rpm, vss, tps, mp = synthetic_pull()
    pulls = power.find_pulls(t, rpm, vss, tps, mp)
    assert len(pulls) == 1
    p = pulls[0]
    assert p.gear == 2 and p.wot and p.duration == pytest.approx(4.0, abs=0.35)     # nothing cut at the top
    assert p.key == f"pull@{p.t0:.1f}"
    v_mid = float(np.mean(p.rpm)) / 76.8 * CAR.vss_scale / 3.6
    expect = float(power.crank_torque(2.2, v_mid, 2, CAR))
    assert np.nanmedian(p.torque) == pytest.approx(expect, rel=0.03)
    assert p.peak_power_hp == pytest.approx(float(np.nanmax(power.hp(p.torque, p.rpm))))
    assert p.curve and all(row[3] < row[1] < row[4] for row in p.curve)
    assert "2nd gear" in p.label and "full throttle" in p.label
    assert p.at(p.t0 + 1.0)[0] == pytest.approx(float(np.interp(p.t0 + 1.0, t, rpm)), rel=0.02)
    assert 0.5 < p.ratio_to() < 1.5
    check = power.airflow_check(p)
    assert check is not None and check.air_gs > 0 and check.hp_lo < check.hp_hi


def test_find_pulls_thresholds_full_and_part_throttle():
    assert (power.MIN_TPS, power.MIN_PULL_S, power.WOT_TPS, power.WOT_S) == (50.0, 1.5, 80.0, 1.0)
    t, rpm, vss, tps, mp = synthetic_pull(tps_level=60.0)
    (p,) = power.find_pulls(t, rpm, vss, tps, mp)
    assert not p.wot and "part throttle 60 %" in p.label
    t, rpm, vss, tps, mp = synthetic_pull(tps_level=45.0)
    assert power.find_pulls(t, rpm, vss, tps, mp) == []                          # below 50 %: not a pull
    assert len(power.find_pulls(t, rpm, vss, tps, mp, min_tps=40)) == 1          # unless asked
    t, rpm, vss, tps, mp = synthetic_pull(seconds=1.2)
    assert power.find_pulls(t, rpm, vss, tps, mp) == []                          # shorter than 1.5 s
    t, rpm, vss, tps, mp = synthetic_pull(tps_level=60.0, seconds=3.0)
    tps[(t >= 6.0) & (t < 6.6)] = 85.0                                           # 0.6 s at full: still part
    assert not power.find_pulls(t, rpm, vss, tps, mp)[0].wot
    tps[(t >= 6.0) & (t < 7.2)] = 85.0                                           # 1.2 s at full: full throttle
    assert power.find_pulls(t, rpm, vss, tps, mp)[0].wot


def test_find_pulls_keeps_the_top_of_the_pull():
    # 2nd gear from 2,800 to 5,400 rpm at 2.2 m/s², then the throttle lifts
    per = CAR.gears[2]
    v0 = 2800 / per * CAR.vss_scale / 3.6
    v1 = 5400 / per * CAR.vss_scale / 3.6
    t, rpm, vss, tps, mp = synthetic_pull(accel=2.2, v0=v0, seconds=(v1 - v0) / 2.2)
    (p,) = power.find_pulls(t, rpm, vss, tps, mp)
    assert p.rpm1 == pytest.approx(5400, abs=150) and p.rpm0 == pytest.approx(2800, abs=150)
    top = p.curve[-1]
    assert top[0] > 5100 and top[1] == pytest.approx(float(power.crank_torque(2.2, v1, 2, CAR)), rel=0.05)
    assert np.nanmax(p.power) == pytest.approx(float(power.hp(power.crank_torque(2.2, v1, 2, CAR), 5400)), rel=0.06)


def test_find_pulls_lists_a_first_gear_launch_from_5_km_h():
    per = CAR.gears[1]
    v0 = 5.5 * CAR.vss_scale / 3.6                       # 5.5 km/h on the ECU
    t, rpm, vss, tps, mp = synthetic_pull(gear_=1, accel=2.0, v0=v0, seconds=2.5)
    vss = np.round(rpm / per)                            # whole km/h, as the ECU reads it
    (p,) = power.find_pulls(t, rpm, vss, tps, mp)
    assert p.gear == 1 and p.kmh0 < 9.0 and p.rpm1 == pytest.approx(float(rpm.max()), abs=150)


def test_airflow_and_engine_model_are_in_the_right_place():
    air = float(power.airflow_gs(5370, 98, 20))
    assert air == pytest.approx(71, abs=4)                # vehicle-dynamics.md §1.1: 68.8-71.3 g/s
    lo, hi = power.airflow_hp(air)
    assert lo == pytest.approx(air * 1.25) and hi == pytest.approx(air * 1.35)
    assert float(power.model_torque(4600, 98)) == pytest.approx(140, abs=10)    # calibrated on the rated point
    assert float(power.engine_force(3000, 60, 0)) == 0.0


# ---- events ----------------------------------------------------------------

def drive_channels():
    """A synthetic drive (session seconds), 12 Hz fast reads, 1 Hz slow ones:
    a near-stall at a launch (throttle closed, 470 rpm), a WOT pull with
    VTEC and a knock retard, a dropout while moving, a mark, a key-off at the end."""
    hz = 12.0
    t = np.arange(0, 300, 1 / hz)
    rpm = np.full(len(t), 850.0)
    vss = np.zeros(len(t))
    tps = np.full(len(t), 7.8)
    mp = np.full(len(t), 28.0)
    # near-stall: clutch bites at 30 s, rpm sags to 470 and recovers, the car creeps off at 33 s
    dip = (t >= 30) & (t < 32)
    rpm[dip] = 850 - 380 * np.sin(np.pi * (t[dip] - 30) / 2)
    tps[(t >= 32.3) & (t < 34)] = 18.0
    # driving from 34 s: 2nd gear, accelerating, WOT 60-64 s
    drive = t >= 34
    vss[drive] = np.minimum(80, 10 + (t[drive] - 34) * 1.2)
    slow = (t >= 170) & (t < 200)                        # a gentle stop: 80 -> 10 km/h in 30 s
    vss[slow] = 80 - (t[slow] - 170) * 70 / 30
    rpm[drive] = vss[drive] * 76.8
    tps[drive] = 30.0
    tps[(t >= 60) & (t < 64)] = 90.0
    mp[drive] = 60.0
    mp[(t >= 60) & (t < 64)] = 98.0
    # silence 100.0-106.0 s while moving: no rpm reads
    keep = ~((t > 100.0) & (t < 106.0))
    # stop at 200 s, idle to the end; the reads end at 290 s (key-off), recording ends at 300 s
    stop = t >= 200
    vss[stop], rpm[stop], tps[stop], mp[stop] = 0.0, 860.0, 7.8, 28.0
    keep &= t < 290
    ts = np.arange(0, 300, 1.0)
    vtec = ((ts >= 62) & (ts < 63)).astype(float)
    ign = np.full(len(ts), 20.0)
    ign[ts >= 61] = 12.0                                 # 8 counts pulled at 61 s, under load
    ign[ts >= 63] = 20.0
    ign[(ts >= 150) & (ts < 151)] = 10.0                 # a drop at light load: not knock
    target = np.full(len(ts), 860.0)
    return {
        "rpm": (t[keep], rpm[keep]), "vss": (t[keep], vss[keep]), "tps": (t[keep], tps[keep]),
        "map": (t[keep], mp[keep]), "vtec": (ts, vtec), "ign_limit": (ts, ign), "idle_target": (ts, target),
        "batt": (ts, np.full(len(ts), 14.2)), "ect": (ts, np.full(len(ts), 70.0)), "eld": (ts, np.full(len(ts), 20.0)),
    }


def test_detect_finds_every_kind_with_a_verdict():
    ch = drive_channels()
    ch["map"] = (ch["map"][0], np.where((ch["map"][0] > 149) & (ch["map"][0] < 152), 30.0, ch["map"][1]))
    found = events.detect(ch, markers=[(120.0, "MARK 1"), (100.5, "STALL (link lost)")], t_end=300.0)
    by = {k: [e for e in found if e.kind == k] for k in events.KINDS}
    assert len(by["near_stall"]) == 1 and by["near_stall"][0].value == pytest.approx(470, abs=6)
    ns = by["near_stall"][0]
    assert ns.t == pytest.approx(31.0, abs=0.1) and ns.severity == "alarm" and ns.data["closest"]
    assert ns.verdict == "throttle closed at clutch bite" or ns.verdict.startswith("throttle")
    assert ns.summary.startswith("470 rpm · ") and "RPM fell" in ns.why
    assert len(by["dropout"]) == 1 and by["dropout"][0].value == pytest.approx(6.0, abs=0.2)
    assert "moving" in by["dropout"][0].summary and "engine ran" in by["dropout"][0].summary
    assert len(by["key_off"]) == 1 and by["key_off"][0].t == pytest.approx(290.0, abs=0.1)
    assert "860 rpm" in by["key_off"][0].summary and "14.2 V" in by["key_off"][0].summary
    assert len(by["vtec"]) == 1 and "WOT" in by["vtec"][0].summary and "2nd" in by["vtec"][0].summary
    assert len(by["knock"]) == 1 and by["knock"][0].value == pytest.approx(2.0)      # 8 counts = 2°
    assert len(by["mark"]) == 1 and by["mark"][0].summary == "MARK 1"                 # the recorder's own markers are not marks
    assert by["stall"] == [] and by["harsh_brake"] == []
    assert [e.t for e in found] == sorted(e.t for e in found)
    assert events.counts(found)["near_stall"] == 1 and events.closest_call(found) is ns
    assert ns.key == "near_stall@31.0" and ns.label == "NEAR-STALL" and ns.title == "Near-stall · 470 rpm"


def test_near_stall_verdicts():
    def dip(tps_fn, vss_after=0.0, peak=850.0):
        hz = 12.0
        t = np.arange(0, 20, 1 / hz)
        rpm = np.full(len(t), 850.0)
        d = (t >= 10) & (t < 12)
        rpm[d] = peak - (peak - 480) * np.sin(np.pi * (t[d] - 10) / 2)
        rise = (t >= 9) & (t < 10)
        rpm[rise] = 850 + (peak - 850) * (t[rise] - 9)
        tps = np.array([tps_fn(x) for x in t])
        vss = np.where(t >= 11, vss_after, 0.0)
        ts = np.arange(0, 20, 1.0)
        return {"rpm": (t, rpm), "tps": (t, tps), "vss": (t, vss), "idle_target": (ts, np.full(len(ts), 850.0))}

    closed = events.near_stalls(dip(lambda x: 7.8))[0]
    assert closed.verdict == "throttle closed at clutch bite"
    late = events.near_stalls(dip(lambda x: 15.0 if x > 10.6 else 7.8))[0]
    assert late.verdict.startswith("throttle 0.") and late.verdict.endswith("s late")
    blip = events.near_stalls(dip(lambda x: 14.0 if 8.5 < x < 10.2 else 7.8, peak=1400.0))[0]
    assert blip.verdict == "blip lifted at the bite"
    hang = events.near_stalls(dip(lambda x: 7.8, peak=1250.0))[0]
    assert hang.verdict == "bite during rev-hang"
    rolled = events.near_stalls(dip(lambda x: 7.8, vss_after=3.0))[0]
    assert rolled.verdict == "no throttle, saved by rolling"
    moving = dip(lambda x: 7.8)
    moving["vss"] = (moving["vss"][0], np.full(len(moving["vss"][0]), 30.0))
    assert events.near_stalls(moving) == []                 # only at a standstill


def test_silences_tell_dropout_key_off_and_stall_apart():
    hz = 10.0
    t = np.arange(0, 100, 1 / hz)
    rpm = np.full(len(t), 820.0)
    vss = np.zeros(len(t))
    keep = ~((t > 20) & (t < 75))                          # 55 s silent at idle, the same rpm after: cable
    ch = {"rpm": (t[keep], rpm[keep]), "vss": (t[keep], vss[keep])}
    (e,) = events.silences(ch, t_end=100.0)
    assert e.kind == "dropout" and "55 s silent at idle" in e.summary and "820→820" in e.summary
    restart = rpm.copy()
    restart[t >= 75] = 1450.0                              # an after-start flare: the engine was off
    ch = {"rpm": (t[keep], restart[keep]), "vss": (t[keep], vss[keep])}
    (e,) = events.silences(ch, t_end=100.0)
    assert e.kind == "key_off" and e.data["restart"]
    moving = np.full(len(t), 50.0)
    restart2 = rpm.copy()
    restart2[t >= 75] = 250.0                              # moving before, a restart after: off while moving
    ch = {"rpm": (t[keep], restart2[keep]), "vss": (t[keep], moving[keep])}
    (e,) = events.silences(ch, t_end=100.0)
    assert e.kind == "stall" and e.severity == "alarm"
    died = rpm.copy()
    died[t >= 50] = 0.0                                    # the ECU answers while the rpm falls to 0
    deaths = events.engine_deaths({"rpm": (t, died), "vss": (t, vss)})
    assert len(deaths) == 1 and deaths[0].t == pytest.approx(50.0)


def test_harsh_braking_is_found():
    hz = 12.0
    t = np.arange(0, 30, 1 / hz)
    v = np.where(t < 10, 60.0, np.maximum(0.0, 60.0 - (t - 10) * 3.6 * 4.0))      # 4 m/s² = 0.41 g
    found = events.harsh({"vss": (t, np.floor(v / CAR.vss_scale))})
    assert [e.kind for e in found] == ["harsh_brake"] and found[0].value == pytest.approx(0.41, abs=0.04)


# ---- derived ---------------------------------------------------------------

def test_derive_gives_every_readout():
    ch = drive_channels()
    ch["inj"] = (ch["rpm"][0], np.full(len(ch["rpm"][0]), 3.0))
    tg = np.arange(0, 300, 1.0)
    ch["gps_speed"] = (tg, np.interp(tg, ch["vss"][0], ch["vss"][1]))
    ch["gps_heading"] = (tg, (tg * 3.0) % 360)
    d = derived.derive(ch)
    assert set(derived.CHANNELS) <= set(d.values)
    at = d.at(50.0)
    assert at["gear"] == 2 and at["long_g"] == pytest.approx(1.2 / 3.6 * CAR.vss_scale / G, abs=0.03)
    assert at["torque"] > 0 and at["power"] > 0 and at["fuel_lph"] > 0
    assert d.at(250.0)["rpm_err"] == pytest.approx(0.0)
    assert np.isfinite(d.values["lat_g"]).any() and d.has("grade")
    assert np.isnan(d.at(1e6)["gear"])
    assert derived.derive({}).t.size == 0


# ---- notes and verdicts in the database ------------------------------------

def test_notes_and_verdicts_live_in_the_events_table(tmp_path):
    database = tmp_path / "hondalog.db"
    started = datetime(2026, 11, 10, 7, 12).timestamp()
    with new_drive(database, {"started_unix": started, "epoch_unix": started}) as w:
        w.add_stream("ecu/00+2", [Column("rpm", units="rpm")], source="ecu", raw_len=2)
        for i in range(50):
            w.append("ecu/00+2", i / 10, [800.0], b"\x00\x00")
        w.event(1.0, "marker", "MARK 1")
    name = w.ref.drive
    assert eventnotes.load(database, name) == {}
    eventnotes.save(database, name, "near_stall@2.0", note="clutch bit early", kind="near_stall", t=2.0)
    eventnotes.save(database, name, "near_stall@2.0", verdict="throttle late")
    stored = eventnotes.load(database, name)
    assert stored["near_stall@2.0"]["note"] == "clutch bit early" and stored["near_stall@2.0"]["verdict"] == "throttle late"
    e = events.DriveEvent("near_stall", 2.0, 2.0, "x", "throttle closed at clutch bite")
    eventnotes.apply([e], stored)
    assert e.note == "clutch bit early" and e.shown_verdict == "throttle late"
    # the reader does not take them for markers
    from hondalog.data import open_log
    with open_log(w.ref) as log:
        assert log.markers == [(1.0, "MARK 1")]
    eventnotes.save(database, name, "near_stall@2.0", note="", verdict="")
    assert eventnotes.load(database, name) == {}
    with pytest.raises(ValueError):
        eventnotes.save(database, "drive_nope", "x", note="y")
