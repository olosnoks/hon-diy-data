"""Expressions, filters, histogram, scatter, cursors, LOD, compare, replay session, report."""

import time

import numpy as np
import pytest

from hondalog.analysis import (Bins, Comparison, ExprError, Filter, FilterSet, LodPyramid,
                               ReplaySession, ScatterModel, TableHistogram, envelope, event_time,
                               first_crossing, span_stats, value_at)
from hondalog.analysis import cursors, filters, histogram
from hondalog.analysis.lod import decimate_points
from hondalog.analysis.report import capture_report, write_report
from hondalog.expr import compile_expr
from hondalog.analysis import compare_logs
from hondalog.data import Column, Log, Stream, StreamInfo, open_log, write_log
from hondalog.data.model import Event, Series, Table


def mem_log(columns, t, events=(), meta=None, name="ecu"):
    info = StreamInfo(name, tuple(Column(n, "f8") for n in columns), source="ecu")
    rows = np.zeros(len(t), info.dtype)
    rows["t"] = t
    for n, v in columns.items():
        rows[n] = v
    return Log(meta or {}, [Stream(info, data=rows)], events)


# ---- expressions (the one evaluator, hondalog.expr, as filters and triggers use it) ----

def test_expr_on_columns():
    t = np.arange(10.0)
    env = {"t": t, "ect": np.linspace(70, 97, 10), "closed_loop": np.array([1.0, 0] * 5), "tps": t * t}

    def mask(text):
        return compile_expr(text).mask(env, t).tolist()

    def col(text):
        return compile_expr(text).eval_array(env, t)

    assert mask("ect > 80 and closed_loop") == [False] * 4 + [True, False] * 3
    assert mask("[ect] > 80 && !closed_loop || t == 0") == [True] + [False] * 4 + [True, False, True, False, True]
    # rate() is the change since the previous row (the same number live and on
    # replay), so the first row has no rate and does not pass
    assert mask("abs(rate(tps)) < 5") == [False, True, True] + [False] * 7
    assert sum(mask("80 < ect <= 85")) == 2
    assert mask("between(ect, 73, 79)") == [False, True, True, True] + [False] * 6
    assert all(mask("1")) and len(mask("0")) == 10 and not any(mask("0"))
    np.testing.assert_allclose(col("ect * 2 - 1 if closed_loop else -ect")[:2], [139, -73])
    np.testing.assert_allclose(col("max(min(tps, 9), 4) ** 2 / 2 % 7")[:4], [1, 1, 1, 5.5])
    assert np.isnan(col("prev(tps)")[0]) and col("prev(tps, 2)")[2] == 0
    # smooth() looks back only, so it can run live: mean of the last 3 rows
    assert col("smooth(tps, 3)")[2] == pytest.approx((0 + 1 + 4) / 3)
    assert col("smooth(tps, 3)")[1] == pytest.approx((0 + 1) / 2)
    assert compile_expr("ect > 80 and closed_loop").deps == {"ect", "closed_loop"}
    with pytest.raises(ExprError, match="no column named 'egt'"):
        compile_expr("egt > 700").mask(env, t)


def test_expr_nan_and_live_values():
    t = np.arange(3.0)
    env = {"t": t, "x": np.array([1.0, np.nan, 3.0])}

    def mask(text):
        return compile_expr(text).mask(env, t).tolist()

    assert mask("x > 0") == [True, False, True]
    assert mask("x") == [True, False, True]                 # NaN is not "true"
    # unknown stays unknown through "not": a row without a value never passes
    assert mask("not (x > 2)") == [True, False, False]
    assert mask("!valid(x)") == [False, True, False]
    stall = compile_expr("rpm < 50")
    assert stall.test({"rpm": 0}) and not stall.test({"rpm": 800})
    assert not stall.test({"rpm": None}) and not stall.test({})        # unknown is never true
    assert stall.truth({"rpm": None}) is None
    link_lost = compile_expr("link == 0")
    assert link_lost.test({"link": 0}) and not link_lost.test({"link": 1})
    assert compile_expr("batt < 11 or ect > 105").test({"batt": 12.5, "ect": 110})
    # and/or go left to right and stop as soon as the answer is known; an
    # unknown value that has to be looked at makes the whole condition unknown
    assert compile_expr("ect > 105 or batt < 11").test({"batt": None, "ect": 110})
    assert compile_expr("batt < 11 or ect > 105").truth({"batt": None, "ect": 110}) is None
    assert compile_expr("true and not false").test({})
    # functions with history run on live values too, one reading at a time
    falling = compile_expr("rate(rpm) < -1000")
    assert falling.stateful and falling.deps == {"rpm"}
    assert [falling.test({"rpm": v}, ts) for ts, v in
            [(0.0, 900), (0.1, 880), (0.2, 600), (0.3, None), (0.4, 590)]] == [False, False, True, False, False]


@pytest.mark.parametrize("text", ["", "__import__('os').system('true')", "rpm.real", "rpm[0]",
                                  "lambda: 1", "'a' == 'a'", "open('x')", "rpm >", "min(1)",
                                  "[x for x in y]", "rpm @ 2", "f(x=1)", "rpm; 1", "exec(1)",
                                  "rpm if", "rpm = 2"])
def test_expr_rejects_everything_else(text):
    with pytest.raises(ExprError):
        compile_expr(text)


# ---- filters ----

def test_filterset_combines_and_caches(synth):
    table = synth.table(["rpm", "map", "tps", "ect", "closed_loop"], base="rpm")
    fs = FilterSet([Filter("warm", "ect > 80"), Filter("closed loop", "closed_loop")])
    fs.add("steady", "abs(rate(tps)) < 5", enabled=False)
    assert fs.channels() == {"ect", "closed_loop"}
    both = fs.mask(table)
    assert 0 < both.sum() < len(table)
    assert np.array_equal(both, (table["ect"] > 80) & (table["closed_loop"] > 0))
    fs.enable("steady")
    assert fs.mask(table).sum() < both.sum() and fs.channels() == {"ect", "closed_loop", "tps"}
    fs.enable("warm", False)
    fs.remove("closed loop")
    assert [f.name for f in fs.active] == ["steady"] and fs.get("warm").enabled is False
    again = FilterSet.from_json(fs.to_json())
    assert np.array_equal(again.mask(table), fs.mask(table))
    assert FilterSet().mask(table).all()
    with pytest.raises(ExprError):
        Filter("bad", "ect >")
    assert all(f.compiled for f in filters.PRESETS)


def test_filter_toggle_is_fast_on_an_hour(synth):
    t = np.arange(72000) / 20.0                           # one hour at 20 Hz
    rng = np.random.default_rng(0)
    table = Table(t, {"ect": 60 + t / 100, "closed_loop": (t > 300).astype(float),
                      "tps": rng.uniform(0, 100, len(t)), "rpm": rng.uniform(700, 6000, len(t)),
                      "map": rng.uniform(20, 100, len(t)), "stft": rng.normal(0, 3, len(t))})
    fs = FilterSet([Filter("warm", "ect > 80"), Filter("cl", "closed_loop"),
                    Filter("steady", "abs(rate(tps)) < 5")])
    fs.mask(table)
    start = time.perf_counter()
    fs.enable("warm", False)
    hist = TableHistogram.from_table(table, "rpm", "map", "stft", histogram.RPM_500,
                                     histogram.MAP_KPA_10, fs.mask(table))
    took = time.perf_counter() - start
    assert hist.samples > 0 and took < 0.2                # the feature list's budget


# ---- histogram ----

def test_bins():
    b = Bins.from_breakpoints([1000, 2000, 3000])
    assert len(b) == 3 and b.centres == (1000, 2000, 3000)
    assert b.index(np.array([0, 1499, 1500, 2600, 9000, np.nan])).tolist() == [0, 0, 1, 2, 2, -1]
    open_ = Bins.from_breakpoints([1000, 2000, 3000], clamp=False)
    assert open_.edges == (500, 1500, 2500, 3500)
    assert open_.index(np.array([499.0, 500, 3500, 3501])).tolist() == [-1, 0, 2, -1]
    lin = Bins.linear(0, 100, 4)
    assert lin.centres == (12.5, 37.5, 62.5, 87.5) and lin.index(np.array([100.0]))[0] == 3
    for bad in ([1], [1, 1], [2, 1]):
        with pytest.raises(ValueError):
            Bins.from_edges(bad)
        with pytest.raises(ValueError):
            Bins.from_breakpoints(bad)
    assert len(histogram.RPM_500) == 15 and len(histogram.PRESETS) == 5


def test_histogram_statistics_match_a_slow_loop():
    rng = np.random.default_rng(3)
    n = 5000
    x, y = rng.uniform(400, 7000, n), rng.uniform(15, 105, n)
    z = rng.normal(0, 5, n)
    z[::97] = np.nan
    xb, yb = Bins.from_breakpoints([1000, 2000, 3000, 4000, 5000, 6000]), Bins.linear(20, 100, 4)
    mask = rng.uniform(size=n) > 0.2
    h = TableHistogram(x, y, z, xb, yb, mask, t=np.arange(n) * 0.1)
    assert h.mean.shape == (4, 6)
    ix, iy = xb.index(x), yb.index(y)
    for cx in range(6):
        for cy in range(4):
            sel = (ix == cx) & (iy == cy) & mask & ~np.isnan(z)
            assert h.count[cy, cx] == sel.sum()
            assert np.array_equal(h.rows(cx, cy), np.flatnonzero(sel))
            if sel.any():
                assert h.mean[cy, cx] == pytest.approx(z[sel].mean())
                assert h.min[cy, cx] == z[sel].min() and h.max[cy, cx] == z[sel].max()
                assert h.std[cy, cx] == pytest.approx(z[sel].std(), abs=1e-9)
                assert h.sum[cy, cx] == pytest.approx(z[sel].sum())
    assert h.samples == h.count.sum()
    np.testing.assert_allclose(h.times(2, 1), h.rows(2, 1) * 0.1)
    hits = int(np.median(h.count))
    gated = h.stat("mean", min_hits=hits + 1)
    assert np.isnan(gated).sum() == (h.count <= hits).sum()
    assert h.stat("count", min_hits=hits + 1).min() == 0
    lo, hi = h.color_range("mean")
    assert lo == np.nanmin(h.mean) and hi == np.nanmax(h.mean) and h.color_range("count")[0] == 0
    with pytest.raises(ValueError):
        h.stat("median")
    with pytest.raises(ValueError):
        TableHistogram(x, y, z, xb, yb).times(0, 0)


def test_histogram_empty_and_delta():
    xb = yb = Bins.linear(0, 10, 2)
    empty = TableHistogram(np.zeros(0), np.zeros(0), np.zeros(0), xb, yb)
    assert empty.samples == 0 and np.isnan(empty.mean).all() and empty.color_range() == (0, 0)
    a = TableHistogram(np.array([1.0, 1, 6]), np.array([1.0, 1, 6]), np.array([10.0, 20, 5]), xb, yb)
    b = TableHistogram(np.array([1.0, 6]), np.array([1.0, 1]), np.array([12.0, 1]), xb, yb)
    d = a.delta(b)
    assert d[0, 0] == 3 and np.isnan(d[0, 1]) and np.isnan(d[1, 1])       # only shared cells
    assert np.isnan(a.delta(b, min_hits=2)).all()
    with pytest.raises(ValueError):
        a.delta(TableHistogram(np.zeros(0), np.zeros(0), np.zeros(0), xb, Bins.linear(0, 10, 3)))


def test_histogram_on_a_log_with_filter(synth):
    fs = FilterSet([Filter("warm", "ect > 80")])
    table = synth.table(["rpm", "map", "inj"] + sorted(fs.channels()), base="rpm")
    h = TableHistogram.from_table(table, "rpm", "map", "inj", histogram.RPM_500, histogram.MAP_KPA_10,
                                  fs.mask(table))
    assert 0 < h.samples < len(table)
    cy, cx = np.unravel_index(np.argmax(h.count), h.count.shape)
    when = h.times(int(cx), int(cy))
    assert len(when) == h.count[cy, cx] and np.all(np.diff(when) > 0)
    assert synth.table(["ect"], t=when)["ect"].min() > 80                 # jump targets obey the filter


# ---- scatter ----

def test_scatter_fit_and_brush():
    t = np.arange(200) * 0.1
    x = np.linspace(0, 10, 200)
    table = Table(t, {"x": x, "y": 3 * x + 2, "c": t, "q": 1 + 2 * x + 0.5 * x * x})
    table.columns["y"][5] = np.nan
    s = ScatterModel(table, "x", "y", "c", mask=x < 9)
    assert len(s) == np.sum(x < 9) - 1 and 5 not in s.rows
    fit = s.fit()
    assert fit.coeffs == pytest.approx((2, 3)) and fit.r2 == pytest.approx(1) and "y = 2 + 3 x" in fit.describe()
    assert fit(np.array([1.0]))[0] == pytest.approx(5)
    quad = ScatterModel(table, "x", "q").fit(2)
    assert quad.coeffs == pytest.approx((1, 2, 0.5))
    picked = s.brush(2, 1, 100, 0)                       # corners in any order
    assert len(picked) and s.x[picked].min() >= 1 and s.x[picked].max() <= 2
    assert np.array_equal(s.brush_times(1, 2, 0, 100), s.t[picked])
    xs, ys, color, pick = s.points(limit=50)
    assert len(xs) == len(ys) == len(color) == len(pick) <= 50 and pick[0] == 0 and pick[-1] == len(s) - 1
    assert ScatterModel(table, "x", "y").points()[2] is None
    flat = Table(t, {"x": np.ones(200), "y": x})
    assert ScatterModel(flat, "x", "y").fit() is None
    spans = ScatterModel(table, "x", "q").brush_spans(0, 1, 0, 1000, gap=0.5)
    assert spans == [(0.0, pytest.approx(1.9))]
    two = ScatterModel(Table(t, {"x": np.sin(t), "y": t}), "x", "y").brush_spans(0.9, 1.1, 0, 100)
    assert len(two) >= 2 and all(a <= b for a, b in two)
    assert decimate_points(5, 10).tolist() == [0, 1, 2, 3, 4] and len(decimate_points(1000, 10)) == 10


# ---- cursors ----

def test_cursor_values_and_span_stats():
    s = Series("rpm", np.array([0.0, 1.0, 2.0, 4.0]), np.array([100.0, 200.0, 400.0, 0.0]), "rpm")
    assert value_at(s, 1.5) == 200 and value_at(s, 1.5, "linear") == 300 and value_at(s, 1.6, "nearest") == 400
    assert np.isnan(value_at(s, -1)) and value_at(s, -1, "nearest") == 100 and value_at(s, 99) == 0
    st = span_stats(s, 0.5, 3.0)
    assert (st.a, st.b, st.delta, st.n) == (100, 400, 300, 2)
    assert (st.min, st.max) == (200, 400) and st.rate == pytest.approx(120)
    assert st.mean == pytest.approx((200 * 1 + 400 * 1) / 2)             # time-weighted
    back = span_stats(s, 3.0, 0.5)
    assert back.delta == -300 and back.min == 200
    gap = span_stats(Series("x", s.t, np.array([np.nan, 1, np.nan, 3])), 0, 4)
    assert gap.n == 2 and gap.min == 1 and gap.max == 3
    none = span_stats(s, 2.2, 2.4)
    assert none.n == 0 and np.isnan(none.mean) and none.a == 400
    assert cursors.step(s, 1.0, +1) == 2 and cursors.step(s, 1.0, -1) == 0 and cursors.step(s, 1.5, -1) == 1
    assert cursors.step(s, 4.0, +1) == 4 and cursors.step(Series("e", np.zeros(0), np.zeros(0)), 3, 1) == 3
    assert np.isnan(value_at(Series("e", np.zeros(0), np.zeros(0)), 1))


# ---- level of detail ----

def test_envelope_keeps_spikes():
    t = np.arange(100_000) / 100.0
    v = np.sin(t)
    v[43_210] = 50.0                                     # one-sample spike
    v[60_000:60_050] = np.nan
    env = envelope(t, v, columns=800)
    assert env.reduced and len(env.t) <= 800
    assert env.hi.max() == 50.0 and env.lo.min() >= -1.0
    assert np.all(env.lo <= env.hi) and np.all(np.diff(env.t) > 0)
    x, y = env.polyline()
    assert len(x) == 2 * len(env.t) and y.max() == 50.0
    zoom = envelope(t, v, 432.0, 432.2, columns=800)     # few samples: raw points come back
    assert not zoom.reduced and zoom.hi.max() == 50.0 and len(zoom.t) <= 23
    assert zoom.polyline()[0] is zoom.t
    assert len(envelope(np.zeros(0), np.zeros(0)).t) == 0
    hole = envelope(t, np.where((t > 300) & (t < 500), np.nan, v), columns=100)
    assert np.isnan(hole.lo).any()                       # an all-NaN column stays a gap


def test_envelope_skips_empty_columns():
    t = np.concatenate([np.arange(0, 10, 0.001), np.arange(90, 100, 0.001)])
    env = envelope(t, t.copy(), columns=100)
    assert env.reduced and len(env.t) == 20              # nothing drawn across the gap
    assert env.lo[10] >= 90 and env.hi[9] < 10


def test_pyramid_matches_direct_envelope():
    rng = np.random.default_rng(5)
    n = 1_000_000
    t = np.arange(n) / 1000.0
    v = rng.normal(0, 1, n)
    v[777_777] = 99.0
    v[123_456] = -99.0
    pyramid = LodPyramid(t, v)
    assert len(pyramid.levels) >= 2
    full = pyramid.envelope(columns=1000)
    assert full.reduced and full.hi.max() == 99.0 and full.lo.min() == -99.0 and len(full.t) <= 1000
    direct = envelope(t, v, columns=1000)
    # a block that straddles a column edge is drawn in the column it starts in, so an
    # extreme can land one column early (a quarter pixel at most); it is never lost
    assert len(full.t) == len(direct.t)
    assert np.all(np.maximum(full.hi, np.roll(full.hi, 1)) >= direct.hi)
    assert np.all(np.minimum(full.lo, np.roll(full.lo, 1)) <= direct.lo)
    part = pyramid.envelope(700.0, 800.0, columns=500)
    assert part.hi.max() == 99.0 and part.t[0] >= 700 and part.t[-1] <= 800
    close = pyramid.envelope(777.7, 777.9, columns=500)   # deep zoom falls back to raw samples
    assert not close.reduced and close.hi.max() == 99.0
    start = time.perf_counter()
    for i in range(20):
        pyramid.envelope(i * 10.0, i * 10.0 + 600.0, columns=1366)
    assert (time.perf_counter() - start) / 20 < 0.02      # a pan step is far below one frame
    assert len(LodPyramid(np.zeros(0), np.zeros(0)).envelope().t) == 0


# ---- compare ----

def two_runs():
    t = np.arange(0, 60, 0.1)
    a = mem_log({"rpm": 800 + 50 * t, "stft": np.full(len(t), 2.0), "map": 30 + t},
                t, [Event(20.0, "marker", "pull start"), Event(40.0, "trigger", "STALL (rpm 0)")])
    tb = np.arange(0, 50, 0.1)
    b = mem_log({"rpm": 800 + 50 * (tb + 7), "stft": np.full(len(tb), -1.0), "map": 30 + tb + 7},
                tb, [Event(13.0, "marker", "pull start"), Event(33.0, "trigger", "STALL (link lost)")])
    return a, b


def test_compare_alignment_and_difference():
    a, b = two_runs()
    cmp = Comparison(a, b)
    assert cmp.common_channels() == ["rpm", "stft", "map"]
    assert abs(cmp.overlay("rpm").summary()["mean"]) == pytest.approx(350, abs=1)    # 7 s apart
    assert cmp.align("marker") == pytest.approx(7.0)
    over = cmp.overlay("rpm")
    assert over.summary()["max_abs"] < 1e-6 and np.isnan(over.diff[:60]).all()      # B starts 7 s in
    assert cmp.shifted("rpm").t[0] == pytest.approx(7.0)
    assert cmp.align("trigger") == pytest.approx(7.0)
    assert cmp.align("crossing", channel="rpm", level=2000) == pytest.approx(7.0, abs=1e-6)
    assert cmp.align("start") == 0
    assert cmp.overlay("stft").summary()["mean"] == pytest.approx(3.0)
    with pytest.raises(ValueError, match="neither log"):
        cmp.align("marker", text="no such marker")
    with pytest.raises(ValueError, match="second log"):
        cmp.align("crossing", channel="rpm", level=3700)    # B never gets that high

    with pytest.raises(ValueError):
        cmp.align("crossing", channel="rpm", level=99999)
    with pytest.raises(ValueError):
        event_time(a, "sideways")
    assert event_time(a, "marker", text="PULL") == 20.0
    assert event_time(mem_log({"x": [1.0]}, [0.0], meta={"stall_time_s": "12.5"}), "trigger") == 12.5
    empty = Comparison(a, mem_log({"rpm": []}, [])).overlay("rpm").summary()
    assert empty["n"] == 0 and np.isnan(empty["rms"])


def test_first_crossing():
    s = Series("v", np.arange(5.0), np.array([0.0, 2.0, 4.0, 2.0, 0.0]))
    assert first_crossing(s, 3.0) == pytest.approx(1.5)
    assert first_crossing(s, 3.0, rising=False) == pytest.approx(2.5)
    assert first_crossing(s, 1.0, after=2.0) is None and first_crossing(s, 9.0) is None
    assert first_crossing(Series("v", np.zeros(1), np.zeros(1)), 0.5) is None


def test_histogram_delta_between_logs():
    a, b = two_runs()
    xb, yb = Bins.linear(800, 3800, 3), Bins.linear(30, 90, 2)
    delta, ha, hb = Comparison(a, b).histogram_delta("rpm", "map", "stft", xb, yb)
    assert delta.shape == (2, 3) and np.nanmax(np.abs(delta - 3.0)) < 1e-9
    assert np.isnan(delta).sum() == np.isnan(ha.mean + hb.mean).sum()
    fs = FilterSet([Filter("low", "rpm < 1800")])
    filtered, fa, _ = Comparison(a, b).histogram_delta("rpm", "map", "stft", xb, yb, filters=fs)
    assert fa.samples < ha.samples and np.isnan(filtered[:, 1:]).all()


def test_compare_two_drives_of_one_database_and_of_two(tmp_path):
    """The same comparison on drives read back from SQLite: both from one
    database, then the second one from another file."""
    a, b = two_runs()
    home, other = tmp_path / "hondalog.db", tmp_path / "friend.db"
    ref_a = write_log(a, home, {"started_unix": 1794812400.0, "notes": "before the new IACV"})
    ref_b = write_log(b, home, {"started_unix": 1794898800.0})
    ref_c = write_log(b, other, {"started_unix": 1794898800.0})
    assert ref_a.db == ref_b.db and ref_a.name != ref_b.name and ref_c.db == other
    for second in (ref_b, ref_c, f"{other}#{ref_c.name}"):
        with open_log(ref_a) as one, open_log(second) as two:
            cmp = Comparison(one, two)
            assert cmp.common_channels() == ["rpm", "stft", "map"]
            assert cmp.align("marker") == pytest.approx(7.0) and cmp.align("trigger") == pytest.approx(7.0)
            assert cmp.overlay("rpm").summary()["max_abs"] < 1e-3            # values are 32-bit in the file
            assert cmp.overlay("stft").summary()["mean"] == pytest.approx(3.0)
            xb, yb = Bins.linear(800, 3800, 3), Bins.linear(30, 90, 2)
            delta, ha, hb = cmp.histogram_delta("rpm", "map", "stft", xb, yb)
            assert np.nanmax(np.abs(delta - 3.0)) < 1e-6 and ha.samples == 600 and hb.samples == 500

            result = compare_logs(one, two, align="marker", text="pull")
            assert result.offset == pytest.approx(7.0) and result.aligned_on == "marker"
            rows = {r.name: r for r in result.rows}
            assert list(rows) == ["rpm", "stft", "map"] and rows["rpm"].units == "" and rows["rpm"].n == 500
            assert rows["stft"].mean == pytest.approx(3.0) and rows["stft"].mean_a == 2.0 and rows["stft"].mean_b == -1.0
            assert rows["map"].max_abs < 1e-3 and result.only_a == [] and result.only_b == []
            fallback = compare_logs(one, two, ["stft", "nope"], align="crossing", channel="rpm", level=1e9)
            assert fallback.aligned_on == "start" and fallback.offset == 0 and [r.name for r in fallback.rows] == ["stft"]

            session = ReplaySession(one)                                     # Replay's overlay, same drives
            assert session.set_overlay(two, "marker") == pytest.approx(7.0)
            assert session.overlay_trace("rpm").t[0] >= 7.0 - 0.2
    extra = mem_log({"rpm": [800.0, 900.0], "knock": [0.0, 1.0]}, [0.0, 1.0])
    with open_log(ref_a) as one:
        result = compare_logs(one, extra)
        assert [r.name for r in result.rows] == ["rpm"] and result.only_a == ["stft", "map"] and result.only_b == ["knock"]


# ---- replay session ----

def test_replay_session(synth):
    session = ReplaySession(synth)
    assert session.view == (synth.t0, synth.t1)
    env = session.trace("rpm", columns=1366)
    assert env.reduced and len(env.t) <= 1366
    session.zoom(0.01, around=300.0)
    assert session.view[1] - session.view[0] == pytest.approx(synth.duration * 0.01)
    assert not session.trace("rpm").reduced              # few samples in view: raw points
    lo = session.view[0]
    session.pan(0.5)
    assert session.view[0] > lo
    session.set_view(5, 1)                               # nonsense is ignored
    session.show_all()
    assert session.view == (synth.t0, synth.t1)

    session.set_cursor(100.0)
    session.set_cursor(160.0, "b")
    assert session.cursor_delta == 60
    rows = {r.name: r for r in session.readouts(["rpm", "ect", "vbatt_mean"])}
    rpm = synth.series("rpm")
    assert rows["rpm"].value == value_at(rpm, 100.0) and rows["rpm"].units == "rpm"
    assert rows["rpm"].stats.n == np.sum((rpm.t >= 100) & (rpm.t <= 160))
    assert rows["ect"].stats.delta == pytest.approx(10, abs=1.5)         # warms 1 degree per 6 s
    session.clear_cursor_b()
    assert session.readouts(["rpm"])[0].stats is None and session.cursor_delta is None
    assert len(session.readouts()) == len(synth.channels)
    session.set_cursor(1e9)
    assert session.cursor_a == synth.t1

    session.set_cursor(100.0)
    one = session.step(+1, "ect")                        # next sample of a slow channel
    assert 0 < one - 100.0 < 1.0 and one in synth.series("ect").t
    assert session.step(-1, "ect") < one
    session.set_cursor(100.0)
    any_stream = session.step(+1)                        # next read of anything
    assert 100.0 < any_stream < 100.03
    assert session.step(-1) <= 100.0

    assert [e.text for e in session.event_list()][:2] == ["MARK 1", "MARK 2"]
    session.set_cursor(0.0)
    session.set_view(0, 10)
    assert session.jump_event(+1) == pytest.approx(25.0)
    assert session.view[0] <= 25.0 <= session.view[1]    # the view follows the cursor
    assert session.jump_event(-1) is None and session.jump_event(+1) == pytest.approx(75.0)


def test_replay_overlay_and_pyramid():
    a, b = two_runs()
    session = ReplaySession(a)
    assert session.overlay_trace("rpm") is None
    assert session.set_overlay(b, "marker") == pytest.approx(7.0)
    over = session.overlay_trace("rpm")
    assert over.t[0] >= 7.0 - 0.2 and session.overlay_trace("nope") is None
    assert session.set_overlay(b, "crossing", channel="rpm", level=1e9) == 0     # falls back to start
    assert session.set_overlay(None) == 0 and session.overlay is None
    t = np.arange(300_000) / 1000.0
    long = ReplaySession(mem_log({"rail": np.sin(t)}, t, name="blackbox/rails"))
    env = long.trace("rail", 1000)
    assert env.reduced and len(env.t) <= 1000 and long._pyramids


# ---- report ----

def test_capture_report(tmp_path, synth):
    cut = synth.window(100, 140)
    cut.meta.update(trigger_time_s="130.000", trigger_message="rpm dropped to 0", notes="<b>hot</b> day",
                    trigger_wall_time="2026-11-15T08:02:10")
    cut.events.append(Event(130.0, "trigger", "STALL (rpm 0)", {"message": "rpm dropped to 0"}))
    html = capture_report(cut, title="Stall on Lakeland Dr")
    assert html.startswith("<!DOCTYPE html>") and "Stall on Lakeland Dr" in html
    assert "rpm dropped to 0 at 130.00 s" in html and "+0 ms" in html
    assert "&lt;b&gt;hot&lt;/b&gt;" in html and "<b>hot</b>" not in html           # escaped
    assert html.count("<svg") >= 6 and "blackbox/rails" in html and "script" not in html
    plain = mem_log({"x": [np.nan, np.nan]}, [0.0, 1.0])
    assert "no data" in capture_report(plain) and "no markers" in capture_report(plain)
    out = write_report(synth, tmp_path / "r.html", channels=["rpm", "map"])
    assert out.read_text(encoding="utf-8").count("<svg") >= 2
