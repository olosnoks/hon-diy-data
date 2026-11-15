"""Guided verification sessions, ISO cross-check, calibration helpers."""

import json

import numpy as np
import pytest

from hondalog import addressmap as am
from hondalog.mapping import calibrate, crosscheck, verify
from hondalog.mapping.sweeps import SweepRecording
from hondalog.mapping.verify import FAIL, PASS, PLAUSIBLE, SCRIPTS, SKIP, VerificationSession
from hondalog.sim import _TEMP_TABLE

from mapping_helpers import Clock, feed, ram


@pytest.fixture
def mapfile(tmp_path):
    path = tmp_path / "addressmap.toml"
    path.write_text(am.DEFAULT_MAP.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def session(script, path=None, **kw):
    clock = Clock()
    s = VerificationSession(SCRIPTS[script], am.load(path) if path else am.load_default(),
                            clock=clock, **kw)
    return s, clock


def run_tps_sweep(s, clock, floor=100.0):
    s.start()
    feed(s, clock, [ram(tps=0)] * 25)
    assert s.can_advance and s.next_phase().id == "sweep"
    feed(s, clock, [ram(tps=floor * i / 50) for i in range(51)])
    s.next_phase()
    feed(s, clock, [ram(tps=floor)] * 20)
    s.next_phase()
    feed(s, clock, [ram(tps=0)] * 20)
    assert s.next_phase() is None
    return s.finish()


# ---- the W3-01 acceptance example -------------------------------------------

def test_tps_sweep_passes_and_confirm_writes_evidence(mapfile, tmp_path):
    s, clock = session("tps_sweep", mapfile)
    assert s.phase is None and not s.can_advance
    first = s.start()
    assert first.id == "released" and "foot off" in first.instruction.lower()
    assert not s.can_advance                              # minimum phase time not reached
    s.finish()                                            # (throwaway session, closed early)

    s, clock = session("tps_sweep", mapfile)
    report = run_tps_sweep(s, clock)
    tps = report.verdicts["tps"]
    assert tps.status == PASS and [r.status for r in tps.results] == [PASS] * 4
    assert "went 1 -> 99" in tps.results[1].measured
    assert report.verdicts["rpm"].status == PLAUSIBLE     # "reads 0" proves nothing
    assert report.sweeps == 116 and report.samples == 116 and report.duration_s > 20
    assert "tps: PASS" in report.text()

    before = am.load(mapfile)
    after = s.confirm("tps", mapfile, note="pedal by hand", evidence_dir=tmp_path / "ev")
    ch = after.channel("tps")
    assert ch.verified and after.version_hash == before.version_hash
    ev = ch.evidence
    assert ev.method == "guided:tps_sweep" and ev.result == "pass"
    assert ev.map_version == before.version and ev.map_hash == before.version_hash
    assert ev.date == report.started and "rises steadily" in ev.summary
    assert "pedal by hand" in ev.summary
    # date, evidence log name and map version are stored, and the log exists
    saved = tmp_path / "ev" / ev.log
    doc = json.loads(saved.read_text(encoding="utf-8"))
    assert doc["verdicts"]["tps"]["status"] == "pass" and doc["map_hash"] == ev.map_hash
    assert len(doc["samples_by_phase"]["sweep"]["values"]["tps"]) == 51
    raw = SweepRecording.load(saved.with_suffix(".npz"))
    assert len(raw) == 116 and [m.label for m in raw.marks] == ["released", "sweep", "floor",
                                                                 "release"]
    # the user's comments in the map survived, everything else is still unverified
    text = mapfile.read_text(encoding="utf-8")
    assert "# about 101 at key-on/WOT" in text and "[channel.evidence]" in text
    assert [c.name for c in after.channels if c.verified] == ["tps"]

    # plausible-only channels need an explicit override; failed ones cannot be confirmed
    with pytest.raises(am.MapError, match="plausibility"):
        s.confirm("rpm", mapfile)
    forced = s.confirm("rpm", mapfile, force=True)
    assert forced.channel("rpm").verified and forced.channel("rpm").evidence.result == "plausible"
    with pytest.raises(am.MapError, match="does not check"):
        s.confirm("ect", mapfile)


def test_wrong_address_fails_and_the_finder_points_at_the_right_one(mapfile, tmp_path):
    """The map has TPS at the wrong byte; the real one is the copy at 0xA0."""
    from hondalog.mapping import mapedit
    mapedit.update_channel(mapfile, "tps", addr=0x18)      # alternator load: never moves
    s, clock = session("tps_sweep", mapfile)
    report = run_tps_sweep(s, clock)
    assert report.verdicts["tps"].status == FAIL
    with pytest.raises(am.MapError, match="failed"):
        s.confirm("tps", mapfile)
    best = s.suggest("tps", top=4)
    assert {m.candidate.addr for m in best[:2]} == {0x14, 0xA0}      # both real throttle bytes
    assert all(m.rho > 0.99 for m in best[:2])
    suspect = s.reject("tps", mapfile, evidence_dir=tmp_path)
    ch = suspect.channel("tps")
    assert ch.status == "suspect" and not ch.verified and ch.evidence.result == "fail"
    # apply the suggestion; verification state resets and the map version moves on
    fixed = mapedit.update_channel(mapfile, "tps", **best[0].candidate.channel_fields(),
                                   formula="(x - 24) / 2" if best[0].candidate.addr == 0x14
                                   else "x / 2.55")
    assert fixed.channel("tps").status == "unverified" and fixed.channel("tps").evidence is None
    assert fixed.version != suspect.version
    s2, clock2 = session("tps_sweep", mapfile)
    assert run_tps_sweep(s2, clock2).verdicts["tps"].status == PASS


def test_partial_pedal_travel_fails():
    s, clock = session("tps_sweep")
    report = run_tps_sweep(s, clock, floor=70.0)          # formula scale is off: tops out at 70%
    tps = report.verdicts["tps"]
    assert tps.status == FAIL
    assert [r.status for r in tps.results] == [PASS, FAIL, FAIL, PASS]


def test_pumping_the_pedal_is_not_a_steady_rise():
    s, clock = session("tps_sweep")
    s.start()
    feed(s, clock, [ram(tps=0)] * 25)
    s.next_phase()
    feed(s, clock, [ram(tps=v) for v in list(range(0, 80, 4)) + list(range(80, 10, -5))
                    + list(range(10, 101, 5))])
    result = s.evaluate()["tps"].results[1]
    assert result.status == FAIL and result.stats["reversal"] > 0.5


# ---- key on, engine off -----------------------------------------------------

def koeo_rows(**kw):
    base = dict(rpm=0, tps=0, map_kpa=100, baro=100, ect=21, iat=22, batt=12.4, mil=1)
    base.update(kw)
    return [ram(**base, noise=i) for i in range(50)]


def test_koeo_with_references_and_a_cold_engine():
    s, clock = session("koeo")
    s.set_reference("batt_meter", 12.5, "multimeter")
    s.set_reference("ambient_c", 20)
    s.answer("cold_engine", True)
    s.start()
    feed(s, clock, koeo_rows())
    v = s.finish().verdicts
    assert {n: x.status for n, x in v.items()} == {
        "rpm": PLAUSIBLE, "vss": PLAUSIBLE, "tps": PLAUSIBLE, "map": PASS, "baro": PLAUSIBLE,
        "batt": PASS, "ect": PASS, "iat": PASS, "mil": PLAUSIBLE, "closed_loop": PLAUSIBLE}
    assert "vs multimeter 12.5" in v["batt"].results[0].measured
    assert s.evidence("batt").reference == "multimeter"


def test_koeo_without_references_skips_instead_of_guessing():
    s, clock = session("koeo")
    s.start()
    feed(s, clock, koeo_rows())
    v = s.finish().verdicts
    assert v["batt"].status == PLAUSIBLE                  # meter check skipped, range check only
    assert v["batt"].results[0].status == SKIP
    assert v["ect"].status == SKIP and v["iat"].status == SKIP       # engine not known cold
    assert "cold engine" in v["ect"].results[0].measured
    with pytest.raises(am.MapError, match="not checked"):
        s.confirm("ect", "unused.toml")


def test_koeo_catches_bad_battery_scale_and_warm_engine():
    s, clock = session("koeo")
    s.set_reference("batt_meter", 12.4)
    s.set_reference("ambient_c", 20)
    s.answer("cold_engine", True)
    s.start()
    feed(s, clock, koeo_rows(batt=11.2, ect=60, map_kpa=60))
    v = s.finish().verdicts
    assert v["batt"].status == FAIL and "apart 1.2" in v["batt"].results[0].measured
    assert v["ect"].status == FAIL and v["map"].status == FAIL and v["iat"].status == PASS


def test_iso_pid_values_as_references():
    s, clock = session("koeo")
    s.set_pid_references({"rpm": 0.0, "ect": 21.0})
    assert s.references["ect_pid"] == 21.0 and s.reference_sources["ect_pid"] == "ISO PID ect"
    s.set_reference("ect_pid", None)
    assert "ect_pid" not in s.references


# ---- idle, 3000 rpm, warm-up, drive, switches -------------------------------

def test_idle_script():
    s, clock = session("idle")
    s.set_reference("tach_rpm", 750)
    s.start()
    rows = [ram(rpm=760 + 15 * np.sin(i), map_kpa=32, ect=90, iat=30, batt=14.1,
                o2=0.45 + 0.4 * np.sin(i * 1.3), inj=2.5, ign=16, closed_loop=1, iacv=30,
                stft=3 * np.sin(i / 5), ltft=2) for i in range(160)]
    feed(s, clock, rows)
    v = s.finish().verdicts
    assert v["rpm"].status == PASS and v["o2"].status == PASS
    assert v["batt"].status == PLAUSIBLE and v["map"].status == PLAUSIBLE
    assert all(x.status in (PASS, PLAUSIBLE) for x in v.values()), s.report.text()
    # a dead O2 sensor (or the wrong byte) does not switch
    s2, clock2 = session("idle")
    s2.start()
    feed(s2, clock2, [ram(rpm=760, o2=0.45, closed_loop=1)] * 160)
    assert s2.finish().verdicts["o2"].status == FAIL


def test_steady_3000_uses_the_tachometer_and_compares_phases():
    s, clock = session("steady_3000")
    assert s.references["tach_rpm"] == 3000                # default target
    s.start()
    feed(s, clock, [ram(rpm=780, ign=16, batt=14.1, tps=0)] * 45)
    s.next_phase()
    feed(s, clock, [ram(rpm=3050 + 40 * np.sin(i), ign=30, batt=14.2, tps=9) for i in range(70)])
    s.next_phase()
    feed(s, clock, [ram(rpm=790, ign=16, batt=14.1)] * 30)
    v = s.finish().verdicts
    assert v["rpm"].status == PASS and v["ign"].status == PLAUSIBLE
    assert "vs tach_rpm 3000" in v["rpm"].results[0].measured
    # RPM formula off by a factor of two: the tach comparison catches it
    s2, clock2 = session("steady_3000")
    s2.start()
    feed(s2, clock2, [ram(rpm=390)] * 45)
    s2.next_phase()
    feed(s2, clock2, [ram(rpm=1500)] * 70)
    rpm = s2.finish().verdicts["rpm"]
    assert rpm.status == FAIL and [r.status for r in rpm.results] == [FAIL, FAIL, PASS]
    hits = s2.suggest("rpm")                               # nothing in RAM reads 3000 with x / 4
    assert hits == []


def test_warmup_script_and_finder_by_shape():
    s, clock = session("warmup")
    s.set_reference("ambient_c", 18)
    s.start()
    feed(s, clock, [ram(ect=19, iat=18)] * 45)
    s.next_phase()
    n = 300
    rows = [ram(rpm=1500 - 750 * min(1, i / 200), ect=19 + 70 * (1 - np.exp(-3 * i / n)),
                iat=18 + 6 * i / n, closed_loop=int(i > 150), noise=(i * 7) % 256)
            for i in range(n)]
    feed(s, clock, rows, dt=1.0)
    v = s.finish().verdicts
    assert v["ect"].status == PASS and v["iat"].status == PASS and v["closed_loop"].status == PASS
    assert v["rpm"].status == PLAUSIBLE
    top = s.suggest("ect", top=3)                          # "rises steadily" -> by shape
    assert top[0].candidate.addr == 0x10 and abs(top[0].rho) > 0.99
    # coolant that never warms (stuck sensor, wrong byte)
    s2, clock2 = session("warmup")
    s2.start()
    feed(s2, clock2, [ram(ect=19, iat=18)] * 45)
    s2.next_phase()
    feed(s2, clock2, [ram(ect=19, iat=18, rpm=800)] * 300, dt=1.0)
    assert s2.finish().verdicts["ect"].status == FAIL


def test_drive_and_switch_scripts_with_bit_suggestion():
    s, clock = session("drive")
    s.set_reference("speedo_kmh", 60)
    s.start()
    feed(s, clock, [ram(vss=62, rpm=2400)] * 60)
    assert s.finish().verdicts["vss"].status == PASS

    amap = am.loads(am.DEFAULT_MAP.read_text(encoding="utf-8").replace(
        'formula = "bit(x, 2)"', 'formula = "bit(x, 6)"'))             # wrong bit for the A/C
    clock = Clock()
    s = VerificationSession(SCRIPTS["ac_switch"], amap, clock=clock)
    s.start()
    feed(s, clock, [ram(rpm=800, ac=0)] * 30)
    s.next_phase()
    feed(s, clock, [ram(rpm=800, ac=1)] * 35)
    s.next_phase()
    feed(s, clock, [ram(rpm=800, ac=0)] * 30)
    assert s.finish().verdicts["ac_sw"].status == FAIL
    bits = s.suggest("ac_sw")
    assert [(b.addr, b.bit) for b in bits] == [(0x08, 2), (0xB0, 4)]

    brake = verify.switch_script("brake", "the brake pedal (press and hold)")
    assert brake.id == "switch_brake" and len(brake.phases) == 3
    missing = VerificationSession(brake, am.load_default(), clock=Clock())
    assert missing.missing_channels == ["brake"]
    missing.start()
    assert missing.finish().verdicts["brake"].status == SKIP


# ---- plumbing ---------------------------------------------------------------

def test_poller_samples_and_recorder_feed():
    from hondalog.poller import Poller
    from hondalog.sim import EcuModel
    from hondalog.transport import LoopbackTransport
    amap = am.load_default()
    poller = Poller(LoopbackTransport(EcuModel()), amap)
    s = VerificationSession(SCRIPTS["koeo"], amap)
    poller.on_sample = lambda sample: s.add_sample(sample.values)
    poller.start()
    try:
        assert poller.ready.wait(5)
        s.start()
        rec = s.recorder(poller.submit, hi=0x1F)
        rec.start()
        import time
        deadline = time.monotonic() + 5
        taken = 0
        while taken < 3 and time.monotonic() < deadline:
            taken += s.take(rec)
            time.sleep(0.01)
        rec.stop()
    finally:
        poller.stop()
    report = s.finish()
    assert taken >= 3 and report.sweeps == taken and report.samples >= 1
    assert report.verdicts["rpm"].status == FAIL          # the simulator's engine is running


def test_session_state_errors_and_coverage():
    s, clock = session("koeo")
    with pytest.raises(RuntimeError):
        s.next_phase()
    with pytest.raises(RuntimeError):
        s.finish()
    s.add_sample({"rpm": 1.0})                             # ignored before start
    s.add_sweep(ram())
    s.start()
    with pytest.raises(RuntimeError):
        s.start()
    assert s.next_phase() is None
    assert s.finish().samples == 0 and s.suggest("rpm") == []
    assert all(v.status == SKIP for v in s.report.verdicts.values())
    rows = {r["channel"]: r for r in verify.coverage(am.load_default())}
    assert rows["tps"]["strong"] == ["tps_sweep"] and "koeo" in rows["tps"]["weak"]
    assert rows["knock"]["strong"] == [] and rows["knock"]["weak"] == []
    assert verify.scripts_for("rpm")[0].id in ("idle", "steady_3000")
    assert SCRIPTS["koeo"].phase("rest").min_s == 8.0
    for script in SCRIPTS.values():                        # every shipped check is well formed
        for check in script.checks:
            assert check.kind in verify._CHECKS and script.phase(check.phase)
            if check.kind == "near_reference":
                assert check.params["ref"] in {r.name for r in script.references} or \
                    check.params["ref"].endswith("_pid")


# ---- ISO cross-check (W3-02) ------------------------------------------------

def test_pid_decoding():
    assert crosscheck.decode_pid(0x0C, [0x2E, 0xE0]) == 3000
    assert crosscheck.decode_pid(0x05, [130]) == 90 and crosscheck.decode_pid(0x0B, [35]) == 35
    assert crosscheck.decode_pid(0x11, [255]) == 100 and crosscheck.decode_pid(0x0D, [60]) == 60
    assert crosscheck.decode_pid(0x06, [128]) == 0 and crosscheck.decode_pid(0x07, [160]) == 25
    assert crosscheck.decode_pid(0x14, [90, 128]) == 0.45
    assert crosscheck.decode_pid(0x0E, [160]) == 16
    assert crosscheck.pid_values({0x0C: [0x0B, 0xB8], 0x05: [120], 0x99: [1], 0x14: [1]}) == \
        {"rpm": 750, "ect": 80}
    with pytest.raises(KeyError):
        crosscheck.decode_pid(0x99, [1])
    with pytest.raises(ValueError):
        crosscheck.decode_pid(0x0C, [1])
    assert {p.channel for p in crosscheck.PIDS} == {"rpm", "ect", "iat", "map", "tps", "vss",
                                                    "stft", "ltft", "o2", "ign"}


def test_crosscheck_agree_scaled_no_match():
    amap = am.load_default()
    cc = crosscheck.CrossCheck({c.name: c.formula for c in amap.channels})
    idle = dict(rpm=752, ect=88, iat=30, map=33, tps=0, vss=0, stft=2, ltft=-1, o2=0.44, ign=16)
    cc.add_snapshot(idle, dict(rpm=760, ect=87, iat=31, map=47.0, tps=9.8, vss=0, stft=2.3,
                               ltft=-0.8, o2=0.5, ign=15.5), "warm idle")
    fast = dict(rpm=3010, ect=91, iat=31, map=25, tps=12, vss=None, ign=31)
    cc.add_snapshot(fast, dict(rpm=2985, ect=91, iat=31, map=36.0, tps=20.4, ign=4.0), "3000")
    cc.add_snapshot(dict(rpm=1500, map=50, tps=30, ign=22),
                    dict(rpm=1490, map=71.0, tps=36.5, ign=40.0), "1500 under load")
    r = {c.channel: c for c in cc.report()}
    assert r["rpm"].verdict == "agree" and r["rpm"].n == 3 and r["rpm"].max_error == 25
    assert r["ect"].verdict == "agree" and r["stft"].verdict == "agree"
    assert r["vss"].verdict == "agree" and r["vss"].n == 1        # the None was skipped
    # map: PID = 1.40 * channel + 1 -> the stored formula's slope is off
    assert r["map"].verdict == "scaled"
    assert r["map"].slope == pytest.approx(1.40, abs=0.02)
    assert r["map"].formula.startswith("(x * 0.716 - 5) * 1.4")
    fixed = am.compile_formula(r["map"].formula)
    assert fixed(int((33 + 5) / 0.716), []) == pytest.approx(47, abs=1.5)
    # tps: relative vs absolute -> scaled, with the explanation
    assert r["tps"].verdict == "scaled" and "absolute position" in r["tps"].detail
    assert r["ign"].verdict == "no match" and "no straight line" in r["ign"].detail
    assert "try formula" in str(r["map"]) and len(r["map"].pairs) == 3
    assert crosscheck.CrossCheck().report(["knock", "rpm"])[0].verdict == "no data"
    assert crosscheck.CrossCheck().report(["rpm"])[0].verdict == "no data"


def test_crosscheck_one_point_cannot_fit():
    cc = crosscheck.CrossCheck()
    cc.add_snapshot({"rpm": 375.0, "map": 60.0}, {"rpm": 750.0, "map": 30.0})
    rpm, mp = cc.report(["rpm", "map"])
    assert rpm.verdict == "no match" and "factor of 2" in rpm.detail and rpm.formula == ""
    assert "different operating point" in mp.detail
    cc.add_snapshot({"rpm": 380.0, "map": 60.0}, {"rpm": 760.0, "map": 31.0})
    assert "too alike" in cc.report(["rpm"])[0].detail             # two idle snapshots
    cc.add_snapshot({"rpm": 1500.0}, {"rpm": 3000.0})
    cc.add_snapshot({"rpm": 1000.0}, {"rpm": 2000.0})
    rpm = cc.report(["rpm"])[0]
    assert rpm.verdict == "scaled" and rpm.slope == pytest.approx(2.0, abs=0.01)
    assert rpm.formula.startswith("x * 2")
    assert crosscheck.steady_mean([{"a": 1.0, "b": None}, {"a": 3.0}]) == {"a": 2.0}


# ---- calibration (W3-08) ----------------------------------------------------

def test_fit_linear_and_residual():
    raw = [100, 120, 146, 160]
    volts = [x / 10.45 + e for x, e in zip(raw, (0.01, -0.02, 0.0, 0.015))]
    fit = calibrate.fit_linear(raw, volts)
    assert fit.kind == "linear" and fit.coeffs[1] == pytest.approx(1 / 10.45, rel=0.01)
    assert fit.rmse < 0.02 and fit.max_error < 0.03 and fit.r2 > 0.999 and fit.n == 4
    assert am.compile_formula(fit.formula)(146, []) == pytest.approx(13.97, abs=0.03)
    assert len(fit.residuals) == 4 and "rmse" in str(fit)
    assert "two points" in calibrate.fit_linear([1, 2], [3, 5]).warnings[0]
    assert calibrate.fit_linear([1, 2, float("nan")], [3, 5, 9]).n == 2
    for bad in ([[1], [2]], [[5, 5], [1, 2]], [[1, 2], [1]]):
        with pytest.raises(ValueError):
            calibrate.fit_linear(*bad)


def test_fit_poly_recovers_the_honda_temperature_curve():
    xs = list(range(15, 245, 4))
    fit = calibrate.fit_poly(xs, [_TEMP_TABLE[x] for x in xs], 5)
    assert fit.rmse < 1e-6 and fit.formula.startswith("poly(x, 155.04")
    ch = am.Channel("t", 0x10, formula=fit.formula)
    assert ch.convert(bytes([100])) == pytest.approx(_TEMP_TABLE[100], abs=1e-6)
    with pytest.raises(ValueError):
        calibrate.fit_poly([1, 2, 3], [1, 2, 3], 3)
    with pytest.raises(ValueError):
        calibrate.fit_poly(xs, xs, 9)
    narrow = calibrate.fit_poly([10, 20, 30, 40], [1, 4, 9, 16], 2)
    assert any("only covers" in w for w in narrow.warnings)


def test_fit_best_prefers_a_line_unless_a_curve_is_clearly_better():
    xs = list(range(20, 240, 5))
    line = calibrate.fit_best(xs, [0.7 * x - 5 + 0.01 * (-1) ** x for x in xs])
    assert line.kind == "linear"
    curve = calibrate.fit_best(xs, [_TEMP_TABLE[x] for x in xs])
    assert curve.kind == "poly" and curve.rmse < calibrate.fit_linear(
        xs, [_TEMP_TABLE[x] for x in xs]).rmse / 2
    few = calibrate.fit_best([10, 50, 90, 130], [_TEMP_TABLE[x] for x in (10, 50, 90, 130)])
    assert few.kind == "linear"                            # too few points to justify a curve


def test_thermistor_from_three_points():
    points = [(214, 0.0), (128, 25.0), (25, 90.0)]
    fit = calibrate.fit_thermistor(points)
    assert fit.kind == "thermistor" and fit.max_error < 1e-6
    ch = am.Channel("ect", 0x10, formula=fit.formula)
    for raw, temp in points:
        assert ch.convert(bytes([raw])) == pytest.approx(temp, abs=1e-5)
    assert 25 < ch.convert(bytes([80])) < 90               # sane in between
    assert ch.convert(bytes([0])) is None and ch.convert(bytes([255])) is None
    assert any("span" in w for w in calibrate.fit_thermistor([(120, 20), (110, 25),
                                                              (100, 30)]).warnings)
    for bad in ([(1, 2)], [(0, 1), (5, 2), (9, 3)], [(5, 1), (5, 2), (9, 3)]):
        with pytest.raises(ValueError):
            calibrate.fit_thermistor(bad)


def test_collector():
    c = calibrate.Collector()
    for raw in (40, 80, 120, 160, 200):
        c.add(raw, raw * 0.5 + 3)
    assert len(c) == 5 and c.fit().formula.startswith("x * 0.5 + 3")
    assert c.fit("linear").kind == "linear" and c.fit("poly", 2).kind == "poly"
    c.clear()
    for p in [(214, 0.0), (128, 25.0), (25, 90.0)]:
        c.add(*p)
    assert c.fit("thermistor").kind == "thermistor"
