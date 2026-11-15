import numpy as np
import pytest

from hondalog.tuning import TableError, parse_clipboard
from hondalog.tuning.demo import DEMO_MAP_KPA, DEMO_RPM, build_demo_rom
from hondalog.tuning.table import format_number, smooth_kernel
from tuning_helpers import demo_session

ROM = build_demo_rom()


@pytest.fixture
def session(tmp_path):
    return demo_session(tmp_path)


@pytest.fixture
def fuel(session):
    return session.table("fuel")


def test_shape_axes_and_views(fuel):
    assert fuel.shape == (8, 10) and fuel.units == "ms" and fuel.decimals == 2
    x, y = fuel.axis("x"), fuel.axis("y")
    assert x.values.tolist() == DEMO_RPM and y.values.tolist() == DEMO_MAP_KPA
    assert x.units == "rpm" and x.source == "rom" and x.editable and len(y) == 8
    raw, real = fuel.values("raw"), fuel.values()
    assert raw.dtype == np.int64 and np.allclose(real, raw * 0.064)
    assert fuel.limits() == pytest.approx((0, 255 * 0.064)) and fuel.limits("raw") == (0, 255)
    assert fuel.format(3.14159) == "3.14" and fuel.format(7.0, "raw") == "7"
    assert format_number(1234.5678, None) == "1234.57"
    assert fuel.session.table("fuel") is fuel
    with pytest.raises(TableError, match="view must be"):
        fuel.values("metric")
    with pytest.raises(TableError):
        fuel.axis("z")


def test_selection_forms(fuel):
    assert fuel.mask(None).all()
    assert fuel.mask((1, 2, 3, 4)).sum() == 9 and fuel.mask((3, 4, 1, 2)).sum() == 9
    assert fuel.mask([(0, 0), (7, 9)]).sum() == 2
    m = np.zeros((8, 10), bool)
    m[0] = True
    assert fuel.mask(m).sum() == 10
    for bad in [(0, 0, 8, 0), [(0, 10)], np.zeros((2, 2), bool)]:
        with pytest.raises(TableError):
            fuel.mask(bad)


def test_set_cell_real_and_raw(fuel, session):
    before = fuel.values("raw").copy()
    r = fuel.set_cell(2, 3, 6.4)
    assert r.changed == 1 and bool(r) and fuel.values("raw")[2, 3] == 100
    assert fuel.values()[2, 3] == pytest.approx(6.4)
    fuel.set_cell(0, 0, 77, view="raw")
    assert fuel.values("raw")[0, 0] == 77
    after = fuel.values("raw")
    after[2, 3], after[0, 0] = before[2, 3], before[0, 0]
    assert np.array_equal(after, before)                 # nothing else moved
    assert fuel.changed_mask().sum() == 2
    assert np.array_equal(fuel.original_values("raw"), before)


def test_clipping_is_reported_not_wrapped(fuel):
    r = fuel.set_value((0, 0, 0, 2), 1000.0)
    assert r.clipped == 3 and fuel.values("raw")[0, :3].tolist() == [255, 255, 255]
    r = fuel.add((0, 0, 0, 0), -1000)
    assert r.clipped == 1 and fuel.values("raw")[0, 0] == 0
    assert fuel.set_cell(1, 1, 300, view="raw").clipped == 1
    with pytest.raises(TableError, match="not a number"):
        fuel.set_cell(0, 0, float("nan"))


def test_add_scale_percent(fuel):
    base = fuel.values().copy()
    sel = (2, 2, 4, 5)
    m = fuel.mask(sel)
    fuel.add(sel, 0.64)
    assert np.allclose(fuel.values()[m], base[m] + 0.64) and np.allclose(fuel.values()[~m], base[~m])
    fuel.undo()
    fuel.scale(sel, 1.1)
    assert np.allclose(fuel.values()[m], base[m] * 1.1, atol=0.033)
    fuel.undo()
    fuel.percent(sel, -10)
    assert np.allclose(fuel.values()[m], base[m] * 0.9, atol=0.033)
    fuel.undo()
    fuel.add(sel, 3, view="raw")
    assert np.array_equal(fuel.values("raw")[m], np.rint(base[m] / 0.064) + 3)
    fuel.undo()
    factors = np.ones((8, 10))
    factors[0, 0] = 2.0
    assert fuel.scale(None, factors).changed == 1
    assert fuel.session.history[-1] == "Fuel (low cam): multiply"


def test_interpolate_rows_cols_and_bilinear(session):
    ign = session.table("ignition")
    ign.set_value(None, 0.0)
    ign.set_cell(0, 0, 10)
    ign.set_cell(0, 4, 30)
    ign.interpolate((0, 0, 0, 4), "horizontal", by_axis=False)
    assert ign.values()[0, :5].tolist() == [10, 15, 20, 25, 30]
    ign.interpolate((0, 0, 0, 4), "horizontal")              # by RPM: 800..3000
    want = np.interp(DEMO_RPM[:5], [800, 3000], [10, 30])
    assert np.allclose(ign.values()[0, :5], want, atol=0.125)
    ign.set_cell(4, 9, 20)
    ign.interpolate((0, 9, 4, 9), "vertical", by_axis=False)
    assert ign.values()[:5, 9].tolist() == [0, 5, 10, 15, 20]
    ign.set_value(None, 0.0)
    for (r, c), v in {(0, 0): 0, (0, 2): 20, (2, 0): 10, (2, 2): 30}.items():
        ign.set_cell(r, c, v)
    ign.interpolate((0, 0, 2, 2), "both", by_axis=False)
    assert ign.values()[:3, :3].tolist() == [[0, 10, 20], [5, 15, 25], [10, 20, 30]]
    assert ign.values()[3:, :].sum() == 0
    with pytest.raises(TableError, match="direction"):
        ign.interpolate(None, "diagonal")
    assert not ign.interpolate(np.zeros((8, 10), bool))
    assert not ign.interpolate((1, 1, 1, 1), "both")         # single cell: no change


def test_interpolate_ragged_selection(session):
    ign = session.table("ignition")
    ign.set_value(None, 0.0)
    ign.set_cell(1, 1, 8)
    ign.set_cell(1, 5, 16)
    ign.interpolate([(1, 1), (1, 3), (1, 5)], "horizontal", by_axis=False)
    assert ign.values()[1, :6].tolist() == [0, 8, 0, 12, 0, 16]


def test_smooth(fuel):
    fuel.set_value(None, 2.56)
    fuel.set_cell(4, 4, 12.8)
    spike = fuel.values()[4, 4]
    assert not fuel.smooth(None, 0.0)
    fuel.smooth((3, 3, 5, 5), 0.5)
    v = fuel.values()
    assert 2.56 < v[4, 4] < spike and v[3, 3] > 2.56 and v[0, 0] == pytest.approx(2.56)
    assert v[2, 4] == pytest.approx(2.56)                    # outside the selection
    before = v[4, 4]
    fuel.smooth(None, 1.0, passes=3)
    assert fuel.values()[4, 4] < before
    with pytest.raises(TableError, match="between 0 and 1"):
        fuel.smooth(None, 1.5)
    flat = np.full((3, 4), 7.0)
    assert np.allclose(smooth_kernel(flat), flat)            # edges are renormalised


def test_copy_and_paste_round_trip(fuel, session):
    text = fuel.copy((1, 1, 2, 3))
    assert text.count("\n") == 2 and text.split("\n")[0].count("\t") == 2
    ign = session.table("target_lambda")
    r = fuel.paste(text, at=(5, 6))
    assert r.changed > 0 and np.allclose(fuel.values()[5:7, 6:9], fuel.values()[1:3, 1:4])
    with_axes = fuel.copy((0, 0, 1, 1), with_axes=True).split("\n")
    assert with_axes[0] == "\t800\t1200" and with_axes[1].startswith("20\t")
    assert fuel.copy(np.zeros((8, 10), bool)) == ""
    raw = fuel.copy((0, 0, 0, 1), view="raw").strip().split("\t")
    assert all(cell.isdigit() for cell in raw)
    assert ign.copy([(0, 0), (1, 1)]).split("\n")[0].endswith("\t")   # hole left empty


def test_paste_variants(fuel):
    assert fuel.paste("5.12", at=(0, 0, 1, 1)).changed == 4          # one value fills
    assert np.allclose(fuel.values()[:2, :2], 5.12)
    r = fuel.paste("1,28\t2,56\n\t3,84\n", at=(7, 9))                 # runs off the edge
    assert r.truncated and fuel.values()[7, 9] == pytest.approx(1.28)
    before = fuel.values()[3, 4]
    fuel.paste("6.4\t\t6.4", at=(3, 3))                               # gap is skipped
    assert fuel.values()[3, 4] == before and fuel.values()[3, 5] == pytest.approx(6.4)
    assert fuel.paste("100\t101", at=(0, 0), view="raw").changed == 2
    assert not fuel.paste("   \n", at=(0, 0))
    with pytest.raises(TableError, match="not a number"):
        fuel.paste("abc", at=(0, 0))


def test_parse_clipboard_separators():
    assert parse_clipboard("1\t2\r\n3\t4\r\n") == [[1, 2], [3, 4]]
    assert parse_clipboard("1;2,5") == [[1, 2.5]]
    assert parse_clipboard("1,2,3") == [[1, 2, 3]]
    assert parse_clipboard("1 2  3") == [[1, 2, 3]]
    assert parse_clipboard("-1e2\t.5") == [[-100, 0.5]]
    assert parse_clipboard("") == []


def test_locked_cells_are_never_touched(fuel):
    fuel.lock((0, 0, 0, 4))
    before = fuel.values("raw")[0].copy()
    r = fuel.percent(None, 10)
    assert r.skipped_locked == 5 and np.array_equal(fuel.values("raw")[0, :5], before[:5])
    assert fuel.values("raw")[0, 5] != before[5]
    assert fuel.set_cell(0, 0, 9).changed == 0
    fuel.unlock((0, 0, 0, 0))
    assert fuel.locked.sum() == 4 and fuel.set_cell(0, 0, 9).changed == 1


def test_revert_selection(fuel):
    fuel.percent(None, 10)
    fuel.revert((0, 0, 3, 9))
    assert not fuel.changed_mask()[:4].any() and fuel.changed_mask()[4:].all()


def test_axis_editing(fuel, session):
    fuel.set_axis_value("x", 0, 900)
    assert fuel.axis("x").values[0] == 900
    assert session.table("ignition").axis("x").values[0] == 900       # shared axis
    assert fuel.set_axis_value("y", 7, 105).changed == 1
    assert fuel.set_axis_value("y", 7, 500).clipped == 1
    fuel.set_axis_value("x", 1, 1300, view="raw")
    assert fuel.axis("x").values[1] == 1300
    assert session.history[0] == "Fuel (low cam): x axis"
    with pytest.raises(TableError, match="out of range"):
        fuel.set_axis_value("x", 10, 1)
    with pytest.raises(TableError):
        fuel.set_axis_value("x", 0, 70000, view="raw")


def test_curve_table(session):
    ect = session.table("ect_fuel")
    assert ect.shape == (1, 8) and ect.axis("y").source == "index"
    assert ect.axis("x").values.tolist() == [-20, 0, 20, 40, 60, 80, 100, 120]
    ect.interpolate((0, 0, 0, 7), "horizontal")
    assert ect.values()[0, 0] == pytest.approx(1.45, abs=0.01)
    with pytest.raises(TableError, match="not stored"):
        ect.set_axis_value("y", 0, 1)


def test_signed_table_negative_values(session):
    ign = session.table("ignition")
    ign.set_cell(0, 0, -5.0)
    assert ign.values()[0, 0] == -5.0 and session.data[0x4080] == 0xEC
    assert ign.limits() == (-32.0, 31.75)


def test_every_op_is_one_undo_step_and_bytes_outside_stay(fuel, session):
    ops = [lambda: fuel.set_cell(1, 1, 3), lambda: fuel.add(None, 0.5),
           lambda: fuel.scale((0, 0, 2, 2), 1.2), lambda: fuel.percent(None, -4),
           lambda: fuel.interpolate((0, 0, 7, 9), "both"), lambda: fuel.smooth(None, 0.4),
           lambda: fuel.paste("4\t4\n4\t4", at=(2, 2)),
           lambda: fuel.set_values(np.full((8, 10), 5.0), (4, 4, 5, 5))]
    for op in ops:
        op()
    assert len(session.history) == len(ops)
    outside = bytearray(session.data)
    outside[0x4020:0x4070] = ROM[0x4020:0x4070]
    assert bytes(outside) == ROM
    for _ in ops:
        fuel.undo()
    assert bytes(session.data) == ROM
    fuel.redo()
    assert fuel.values()[1, 1] == pytest.approx(3.0, abs=0.033)
    with pytest.raises(TableError, match="shape"):
        fuel.set_values(np.zeros((2, 2)))
