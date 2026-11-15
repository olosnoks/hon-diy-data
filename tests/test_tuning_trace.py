import numpy as np
import pytest

from hondalog.tuning import AxisError, TableTracer, cell_stat, knock_map
from hondalog.tuning.binning import bilinear, fractional_index, nearest_cell, spread
from tuning_helpers import demo_session

RPM = [1000, 2000, 3000, 4000]
MAP = [20, 40, 60]


def test_fractional_index_rising_falling_and_clamped():
    idx, clamped = fractional_index(np.array(RPM), [1000, 1500, 4000, 500, 9000])
    assert idx.tolist() == [0, 0.5, 3, 0, 3] and clamped.tolist() == [0, 0, 0, 1, 1]
    idx, _ = fractional_index(np.array(RPM[::-1]), [4000, 3500, 1000])
    assert idx.tolist() == [0, 0.5, 3]
    idx, _ = fractional_index(np.array(RPM), [np.nan])
    assert np.isnan(idx[0])
    assert fractional_index(np.array([5.0]), [5.0, 6.0])[1].tolist() == [False, True]
    for bad in ([], [1, 1], [1, 3, 2], [1, np.nan]):
        with pytest.raises(AxisError):
            fractional_index(np.array(bad, dtype=float), [1])


def test_cursor_weights_sum_to_one():
    t = TableTracer(RPM, MAP)
    pos = t.locate(1250, 25)
    assert pos.valid and (pos.col, pos.row) == (0.25, 0.25)
    assert pos.cells[0] == (0, 0, pytest.approx(0.5625)) and pos.nearest == (0, 0)
    assert sum(w for _, _, w in pos.cells) == pytest.approx(1.0) and len(pos.cells) == 4
    assert {(r, c) for r, c, _ in pos.cells} == {(0, 0), (0, 1), (1, 0), (1, 1)}
    on_point = t.locate(2000, 40)
    assert on_point.cells == [(1, 1, 1.0)]
    edge = t.locate(4000, 60)
    assert edge.cells == [(2, 3, 1.0)] and edge.nearest == (2, 3) and not edge.clamped
    off = t.locate(9000, 5)
    assert off.clamped_x and off.clamped_y and off.cells == [(0, 3, 1.0)]
    assert not t.locate(float("nan"), 30).valid and not t.locate(None, 30).valid


def test_trail_fades_and_expires():
    t = TableTracer(RPM, MAP, trail_seconds=2.0)
    for i in range(5):
        t.update(i * 0.5, 1000 + i * 500, 20)
    trail = t.trail()
    assert trail.shape == (5, 4) and trail[-1].tolist() == [2.0, 0.0, 0.0, 1.0]
    assert trail[0, 3] == 0.0 and np.all(np.diff(trail[:, 3]) > 0)
    t.update(3.0, 4000, 60)
    assert len(t.trail()) == 4                          # the t=0 and t=0.5 samples are gone
    assert len(t.trail(now=4.9)) == 1 and len(t.trail(now=100)) == 0
    heat = t.heat()
    assert heat.shape == (3, 4) and heat[2, 3] == 1.0 and 0 < heat[0, 2] < 1
    assert heat[1, 0] == 0
    assert t.cursor.nearest == (2, 3)


def test_hits_and_dwell():
    t = TableTracer(RPM, MAP)
    for i in range(11):
        t.update(i * 0.1, 2000, 40)
    assert t.hits[1, 1] == 11 and t.hits.sum() == 11
    assert t.dwell[1, 1] == pytest.approx(1.0)
    t.update(1.1, None, 40)                            # link lost
    assert not t.cursor.valid
    t.update(60.0, 2000, 40)                           # no dwell across the gap
    assert t.dwell[1, 1] == pytest.approx(1.0) and t.hits[1, 1] == 12
    t.update(70.0, 1500, 30)                           # long step is capped at 1 s
    assert t.dwell.sum() == pytest.approx(2.0)
    t.reset()
    assert t.hits.sum() == 0 and len(t.trail()) == 0 and not t.cursor.valid


def test_max_points_bound():
    t = TableTracer(RPM, MAP, trail_seconds=1e9, max_points=50)
    for i in range(500):
        t.update(i, 2000, 40)
    assert len(t.trail()) == 50


def test_tracer_for_a_rom_table(tmp_path):
    s = demo_session(tmp_path)
    t = TableTracer.for_table(s.table("fuel"), trail_seconds=3)
    assert t.shape == (8, 10)
    pos = t.update(0.0, 3300, 55)
    assert pos.nearest in ((3, 4), (4, 4), (3, 5), (4, 5)) and len(pos.cells) == 4
    assert pos.col == pytest.approx(4.5) and pos.row == pytest.approx(3.5)
    with pytest.raises(AxisError, match="no numeric"):
        TableTracer.for_table(s.table("ect_fuel"))


def test_bilinear_spread_conserves_samples():
    x = np.array([1000, 1500, 2500, 4000, np.nan])
    y = np.array([20, 30, 60, 60, 20])
    r0, c0, wr, wc, valid = bilinear(RPM, MAP, x, y)
    assert valid.tolist() == [True, True, True, True, False]
    hits = spread((3, 4), r0, c0, wr, wc, keep=valid)
    assert hits.sum() == pytest.approx(4.0) and hits[0, 0] == pytest.approx(1.25)
    r, c, ok = nearest_cell(RPM, MAP, x, y)
    assert list(zip(r[ok], c[ok])) == [(0, 0), (1, 1), (2, 2), (2, 3)]


def test_cell_stat():
    x = np.array([1000, 1000, 1100, 3000, 3000])
    y = np.array([20, 20, 22, 60, 60])
    z = np.array([1.0, 3.0, 5.0, 10.0, np.nan])
    assert cell_stat(x, y, z, RPM, MAP, "count")[0, 0] == 3
    assert cell_stat(x, y, z, RPM, MAP, "mean")[0, 0] == 3
    assert cell_stat(x, y, z, RPM, MAP, "max")[0, 0] == 5
    assert cell_stat(x, y, z, RPM, MAP, "min")[0, 0] == 1
    assert cell_stat(x, y, z, RPM, MAP, "sum")[2, 2] == 10
    assert np.isnan(cell_stat(x, y, z, RPM, MAP, "mean")[1, 1])
    keep = np.array([True, False, False, True, True])
    assert cell_stat(x, y, z, RPM, MAP, "count", keep)[0, 0] == 1
    with pytest.raises(ValueError):
        cell_stat(x, y, z, RPM, MAP, "median")


def test_knock_map_modes():
    x = np.array([2000] * 4 + [4000] * 4)
    y = np.array([40] * 8)
    counter = np.array([0, 0, 2, 2, 3, 0, 1, 1])        # +2, +1, reset, +1
    k = knock_map(x, y, counter, RPM, MAP)
    assert k[1, 1] == 2 and k[1, 3] == 2 and k.sum() == 4
    flag = np.array([0, 1, 1, 0, 1, 0, 0, 1])
    assert knock_map(x, y, flag, RPM, MAP, "flag").sum() == 3
    assert knock_map(x, y, flag, RPM, MAP, "level")[1, 1] == 2
    with pytest.raises(ValueError):
        knock_map(x, y, flag, RPM, MAP, "loud")
