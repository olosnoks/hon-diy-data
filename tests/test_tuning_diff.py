import numpy as np
import pytest

from hondalog.tuning import RomError, byte_runs, compare_many, diff_roms, table_stack
from hondalog.tuning.demo import build_demo_rom, demo_definition
from tuning_helpers import demo_session

ROM = build_demo_rom()


def edited(tmp_path):
    s = demo_session(tmp_path)
    s.table("fuel").percent((0, 0, 1, 1), 10)
    s.table("ignition").add((7, 9, 7, 9), -2)
    s.set_scalar("rev_limit", 7200)
    s.set_flag("closed_loop", False)
    s.apply("code patch", [(0x200, b"\x00\x01\x02"), (0x7000, b"\xAA")])
    return s


def test_byte_runs():
    a = bytearray(32)
    b = bytearray(32)
    assert byte_runs(a, b) == []
    for i in (3, 4, 5, 9, 31):
        b[i] = 1
    assert byte_runs(a, b) == [(3, 6), (9, 10), (31, 32)]
    assert byte_runs(a, b, join_gap=3) == [(3, 10), (31, 32)]
    with pytest.raises(RomError, match="cannot compare"):
        byte_runs(b"a", b"ab")


def test_identical():
    d = diff_roms(ROM, ROM, demo_definition())
    assert d.identical and d.summary() == ["The ROMs are identical."]
    assert d.regions == [] and d.tables == []


def test_diff_by_definition(tmp_path):
    s = edited(tmp_path)
    d = s.diff()
    assert not d.identical and d.bytes_changed == int(s.changed_bytes().sum())
    kinds = {(r.item_id, r.kind) for r in d.regions}
    assert {("fuel", "table"), ("ignition", "table"), ("rev_limit", "scalar"),
            ("closed_loop", "flag"), (None, "undefined")} <= kinds
    assert sum(r.bytes_changed for r in d.regions) == d.bytes_changed
    assert [(r.start, r.end) for r in d.undefined] == [(0x200, 0x203), (0x7000, 0x7001)]
    assert d.undefined_bytes == 4

    fuel = d.table("fuel")
    assert fuel.cells_changed == 4 and fuel.view == "real" and not fuel.axes_changed
    assert fuel.changed[:2, :2].all() and fuel.changed.sum() == 4
    assert np.allclose(fuel.percent[fuel.changed], 10, atol=1.5)
    assert np.allclose(fuel.delta, fuel.b - fuel.a) and (fuel.delta[~fuel.changed] == 0).all()
    assert fuel.max_abs_percent == pytest.approx(10, abs=1.5) and fuel.mean_delta > 0
    ign = d.table("Ignition (low cam)")
    assert ign.cells_changed == 1 and ign.delta[7, 9] == -2 and ign.max_abs_delta == 2
    assert d.table("target_lambda") is None

    assert [(x.id, x.a, x.b, x.delta) for x in d.scalars] == [("rev_limit", 6800, 7200, 400)]
    assert d.scalars[0].percent == pytest.approx(5.88, abs=0.01)
    assert [(f.id, f.a, f.b) for f in d.flags] == [("closed_loop", True, False)]
    text = "\n".join(d.summary())
    assert "Fuel (low cam): 4 of 80 cells" in text and "Rev limit: 6800 -> 7200 rpm" in text
    assert "Closed loop enabled: on -> off" in text and "2 undefined regions" in text


def test_axis_change_and_zero_base(tmp_path):
    s = demo_session(tmp_path)
    s.table("fuel").set_axis_value("x", 0, 900)
    s.table("ignition").set_value(None, 0.0)
    base = bytes(s.data)
    s.table("ignition").set_cell(0, 0, 5)
    d = diff_roms(ROM, bytes(s.data), s.definition)
    assert d.table("fuel").axes_changed and d.table("fuel").cells_changed == 0
    assert "axis changed" in d.table("fuel").summary()
    d2 = diff_roms(base, bytes(s.data), s.definition)
    assert np.isnan(d2.table("ignition").percent[0, 0])
    assert d2.table("ignition").max_abs_percent == 0.0


def test_raw_bytes_only(tmp_path):
    s = edited(tmp_path)
    d = diff_roms(ROM, bytes(s.data))
    assert d.tables == [] and all(r.item_id is None for r in d.regions)
    assert len(d.regions) == len(d.runs)


def test_compare_many_and_table_stack(tmp_path):
    s = edited(tmp_path)
    b = bytes(s.data)
    s.table("fuel").percent(None, 5)
    c = bytes(s.data)
    result = compare_many(ROM, {"b": b, "c": c, "same": ROM, "d": c}, s.definition)
    assert list(result) == ["b", "c", "same", "d"] and result["same"].identical
    assert result["c"].table("fuel").cells_changed > result["b"].table("fuel").cells_changed
    stack = table_stack(ROM, {"b": b, "c": c}, s.definition, "fuel")
    assert list(stack) == ["base", "b", "c"] and stack["c"].shape == (8, 10)
    assert stack["c"][7, 9] > stack["base"][7, 9]


def test_unsupported_math_falls_back_to_raw():
    from hondalog.tuning import load_xdf
    from tuning_helpers import SAMPLE_XDF
    d = load_xdf(SAMPLE_XDF)
    a = bytearray(0x8000)
    b = bytearray(0x8000)
    b[0x10A0] = 9
    b[0x1090] = 2            # the K variable of "column_major"
    b[0x1080] = 4
    result = diff_roms(a, b, d)
    assert result.table("needs_cell_function").view == "raw"
    assert result.table("needs_cell_function").delta[0, 0] == 9
    assert result.table("column_major").b[0, 0] == 8 and result.table("column_major").a[0, 0] == 0
