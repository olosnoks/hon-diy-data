import numpy as np
import pytest

from hondalog.tuning import (AutoTuneSettings, afr_to_lambda, analyze, analyze_log,
                             lambda_to_afr, trim_ratio)
from hondalog.tuning.demo import DEMO_MAP_KPA, DEMO_RPM
from tuning_helpers import demo_session

RPM = np.array([1000.0, 2000, 3000, 4000])
MAP = np.array([20.0, 40, 60])


def on_cells(cells, n=20):
    """n samples sitting exactly on each (row, col) cell."""
    x = np.repeat([RPM[c] for _, c in cells], n)
    y = np.repeat([MAP[r] for r, _ in cells], n)
    return x, y


def test_conversions():
    assert afr_to_lambda(14.7) == pytest.approx(1.0)
    assert lambda_to_afr(0.85) == pytest.approx(12.495)
    assert trim_ratio(5, 10) == pytest.approx(1.155) and trim_ratio(-4) == pytest.approx(0.96)


def test_lean_cell_gets_fuel_rich_cell_loses_it():
    x, y = on_cells([(0, 0), (1, 1), (2, 3)])
    lam = np.repeat([1.05, 0.95, 1.0], 20)
    res = analyze(x, y, RPM, MAP, lambda_measured=lam, lambda_target=1.0)
    assert res.hits[0, 0] == 20 and res.hits.sum() == 60
    assert res.error_pct[0, 0] == pytest.approx(5) and res.error_pct[1, 1] == pytest.approx(-5)
    assert res.correction_pct[0, 0] == pytest.approx(5)
    assert res.correction_pct[1, 1] == pytest.approx(-5)
    assert res.correction_pct[2, 3] == 0                 # on target: inside the deadband
    assert res.multiplier[0, 0] == pytest.approx(1.05) and res.cells_to_change == 2
    assert np.isnan(res.error_pct[0, 1]) and np.isnan(res.lambda_mean[0, 1])
    assert res.lambda_mean[0, 0] == pytest.approx(1.05) and res.lambda_target[1, 1] == 1.0
    assert res.samples_total == 60 and res.samples_used == 60
    assert "2 cells to change" in res.summary()
    assert set(res.overlays()) == {"lambda", "target", "error_pct", "hits", "correction_pct"}


def test_min_samples_and_max_correction():
    x, y = on_cells([(0, 0)], n=5)
    x2, y2 = on_cells([(1, 2)], n=30)
    x, y = np.concatenate([x, x2]), np.concatenate([y, y2])
    lam = np.concatenate([np.full(5, 1.1), np.full(30, 1.3)])
    res = analyze(x, y, RPM, MAP, lambda_measured=lam,
                  settings=AutoTuneSettings(min_samples=10, max_correction_pct=8))
    assert not res.eligible[0, 0] and res.correction_pct[0, 0] == 0
    assert res.error_pct[0, 0] == pytest.approx(10)       # still shown as an overlay
    assert res.correction_pct[1, 2] == 8 and res.limited[1, 2]
    assert res.error_pct[1, 2] == pytest.approx(30) and "1 at the limit" in res.summary()


def test_bilinear_sharing_between_cells():
    n = 40
    x, y = np.full(n, 1500.0), np.full(n, 30.0)           # dead centre of four cells
    res = analyze(x, y, RPM, MAP, lambda_measured=np.full(n, 1.04),
                  settings=AutoTuneSettings(min_samples=10))
    assert np.allclose(res.hits[:2, :2], 10) and res.hits.sum() == pytest.approx(n)
    assert np.allclose(res.correction_pct[:2, :2], 4)
    near = analyze(x, y, RPM, MAP, lambda_measured=np.full(n, 1.04),
                   settings=AutoTuneSettings(weighting="nearest"))
    assert near.hits[1, 1] == n and near.cells_to_change == 1


def test_target_forms():
    x, y = on_cells([(0, 0), (2, 3)])
    lam = np.repeat([0.9, 0.9], 20)
    table = np.ones((3, 4))
    table[2, 3] = 0.85
    res = analyze(x, y, RPM, MAP, lambda_measured=lam, lambda_target=table)
    assert res.correction_pct[0, 0] == pytest.approx(-10)
    assert res.correction_pct[2, 3] == pytest.approx((0.9 / 0.85 - 1) * 100)
    per_sample = np.repeat([0.9, 1.0], 20)
    res = analyze(x, y, RPM, MAP, lambda_measured=lam, lambda_target=per_sample)
    assert res.correction_pct[0, 0] == 0 and res.correction_pct[2, 3] == pytest.approx(-10)
    with pytest.raises(ValueError, match="lambda_target"):
        analyze(x, y, RPM, MAP, lambda_measured=lam, lambda_target=np.ones(3))


def test_bad_samples_are_dropped():
    x, y = on_cells([(1, 1)], n=40)
    lam = np.full(40, 1.02)
    lam[:5] = 1.9            # fuel cut
    lam[5:8] = np.nan        # sensor dropout
    lam[8:10] = 0.2
    res = analyze(x, y, RPM, MAP, lambda_measured=lam)
    assert res.samples_used == 30 and res.correction_pct[1, 1] == pytest.approx(2)
    mask = np.zeros(40, bool)
    mask[20:] = True
    res = analyze(x, y, RPM, MAP, lambda_measured=lam, mask=mask)
    assert res.samples_used == 20 and res.hits[1, 1] == 20
    x[10] = np.nan
    assert analyze(x, y, RPM, MAP, lambda_measured=lam).samples_used == 29
    empty = analyze(x, y, RPM, MAP, lambda_measured=np.full(40, np.nan))
    assert empty.cells_to_change == 0 and "no cell needs" in empty.summary()


def test_lag_compensation():
    t = np.arange(0, 20, 0.1)
    in_second = t >= 10
    x = np.where(in_second, 3000.0, 1000.0)
    y = np.full(t.size, 40.0)
    true_lam = np.where(in_second, 1.08, 0.94)
    seen = np.interp(t - 0.5, t, true_lam)                # sensor reads 0.5 s late
    plain = analyze(x, y, RPM, MAP, lambda_measured=seen, time=t)
    fixed = analyze(x, y, RPM, MAP, lambda_measured=seen, time=t,
                    settings=AutoTuneSettings(lag_s=0.5))
    assert abs(fixed.error_pct[1, 2] - 8) < abs(plain.error_pct[1, 2] - 8)
    assert fixed.error_pct[1, 2] == pytest.approx(8, abs=0.05)
    assert fixed.error_pct[1, 0] == pytest.approx(-6, abs=0.05)
    assert fixed.samples_used == t.size - 5               # the tail has no reading yet
    with pytest.raises(ValueError, match="time"):
        analyze(x, y, RPM, MAP, lambda_measured=seen, settings=AutoTuneSettings(lag_s=0.5))


def test_smoothing_and_neighbour_blend():
    x, y = on_cells([(1, 1), (1, 2)], n=30)
    lam = np.repeat([1.08, 1.0], 30)
    plain = analyze(x, y, RPM, MAP, lambda_measured=lam)
    smooth = analyze(x, y, RPM, MAP, lambda_measured=lam,
                     settings=AutoTuneSettings(smoothing=1.0))
    assert plain.correction_pct[1, 1] == pytest.approx(8) and plain.correction_pct[1, 2] == 0
    assert 0 < smooth.correction_pct[1, 2] < smooth.correction_pct[1, 1] < 8
    assert smooth.correction_pct[0, 0] == 0               # untuned cells stay out of it
    blend = analyze(x, y, RPM, MAP, lambda_measured=lam,
                    settings=AutoTuneSettings(neighbour_blend=0.5))
    assert 0 < blend.correction_pct[0, 1] < 8 and not blend.eligible[0, 1]
    assert blend.correction_pct[1, 1] == pytest.approx(8)
    locked = np.zeros((3, 4), bool)
    locked[0, 1] = locked[1, 1] = True
    held = analyze(x, y, RPM, MAP, lambda_measured=lam, locked=locked,
                   settings=AutoTuneSettings(neighbour_blend=0.5))
    assert held.correction_pct[0, 1] == 0 and held.correction_pct[1, 1] == 0


def test_fuel_trims_as_the_error_signal():
    x, y = on_cells([(0, 1)], n=25)
    res = analyze(x, y, RPM, MAP, ratio=trim_ratio(np.full(25, 3.0), np.full(25, 4.0)))
    assert res.correction_pct[0, 1] == pytest.approx(7.12)
    with pytest.raises(ValueError, match="either"):
        analyze(x, y, RPM, MAP)
    with pytest.raises(ValueError, match="either"):
        analyze(x, y, RPM, MAP, ratio=np.ones(25), lambda_measured=np.ones(25))


@pytest.mark.parametrize("kwargs", [
    dict(min_samples=-1), dict(smoothing=2), dict(neighbour_blend=-0.1),
    dict(weighting="cubic"), dict(max_correction_pct=-1),
])
def test_settings_validation(kwargs):
    with pytest.raises(ValueError):
        analyze(np.ones(3), np.ones(3), RPM, MAP, lambda_measured=np.ones(3),
                settings=AutoTuneSettings(**kwargs))


def test_input_shape_errors():
    with pytest.raises(ValueError, match="same length"):
        analyze(np.ones(3), np.ones(4), RPM, MAP, lambda_measured=np.ones(3))
    with pytest.raises(ValueError, match="one value per sample"):
        analyze(np.ones(3), np.ones(3), RPM, MAP, lambda_measured=np.ones(2))
    with pytest.raises(ValueError, match="mask"):
        analyze(np.ones(3), np.ones(3), RPM, MAP, lambda_measured=np.ones(3), mask=[True])


def test_apply_to_the_fuel_table_and_converge(tmp_path):
    """Closing the loop: a table that is 6% lean everywhere it was driven is
    corrected over passes, never by more than the per-pass limit."""
    s = demo_session(tmp_path)
    fuel = s.table("fuel")
    truth = fuel.values() * 1.06                        # the fuel the engine wants
    rng = np.random.default_rng(1)
    n = 6000
    rpm = rng.uniform(1200, 5000, n)
    kpa = rng.uniform(30, 85, n)
    xi = np.interp(rpm, DEMO_RPM, np.arange(10))
    yi = np.interp(kpa, DEMO_MAP_KPA, np.arange(8))

    def lookup(table):
        from scipy_free import bilinear_lookup
        return bilinear_lookup(table, yi, xi)

    fuel.lock((3, 3, 3, 3))
    locked_before = fuel.values("raw")[3, 3]
    settings = AutoTuneSettings(max_correction_pct=4, min_samples=5)
    errors = []
    for _ in range(4):
        lam = lookup(truth) / lookup(fuel.values())     # lean where the table is low
        res = analyze_log({"rpm": rpm, "map": kpa, "lambda": lam}, fuel, settings=settings)
        assert np.abs(res.correction_pct).max() <= 4 + 1e-9
        assert res.correction_pct[3, 3] == 0            # locked
        errors.append(np.nanmean(np.abs(res.error_pct[res.eligible])))
        done = res.apply(fuel)
        assert done.skipped_locked == 0
    assert errors[0] == pytest.approx(6, abs=0.5) and errors[-1] < 1.0
    assert errors == sorted(errors, reverse=True)
    assert fuel.values("raw")[3, 3] == locked_before
    assert not fuel.changed_mask()[0].any()             # never driven there
    assert s.history.count("Fuel (low cam): auto-tune correction") == 2   # then nothing left to do
    with pytest.raises(ValueError, match="the table is"):
        res.apply(s.table("ect_fuel"))


def test_analyze_log_channel_choices(tmp_path):
    s = demo_session(tmp_path)
    fuel = s.table("fuel")
    log = {"rpm": np.full(30, 3000.0), "map": np.full(30, 60.0),
           "afr": np.full(30, 15.435), "stft": np.full(30, 2.0), "ltft": np.full(30, 3.0),
           "time_s": np.arange(30) * 0.1, "lambda": np.full(30, 0.97)}
    assert analyze_log(log, fuel, afr="afr").correction_pct[4, 4] == pytest.approx(5)
    assert analyze_log(log, fuel).correction_pct[4, 4] == pytest.approx(-3)
    assert analyze_log(log, fuel, stft="stft", ltft="ltft").correction_pct[4, 4] == \
        pytest.approx(5.06)
    target = s.table("target_lambda").values()
    res = analyze_log(log, fuel, lambda_target=target)
    assert res.lambda_target[4, 4] == pytest.approx(target[4, 4])
    with pytest.raises(ValueError, match="name a lambda"):
        analyze_log(log, fuel, lam=None)
    with pytest.raises(ValueError, match="no numeric"):
        analyze_log(log, s.table("ect_fuel"))
