"""Math channels: ordering, cycles, live evaluation, replay."""

import numpy as np
import pytest

from hondalog import addressmap as am
from hondalog.mathch import MathChannel, MathEngine, MathError


def engine(*defs, inputs=("rpm", "inj", "map", "stft", "ltft"), **kw):
    return MathEngine([MathChannel(n, e) for n, e in defs], inputs, **kw)


def test_evaluates_and_orders_dependencies():
    e = engine(("flow", "[duty] * 0.576"), ("duty", "[inj] * [rpm] / 1200"),
               ("flow2", "flow * 2"))
    assert e.names.index("duty") < e.names.index("flow") < e.names.index("flow2")
    out = e.update({"inj": 2.4, "rpm": 3000.0}, 0.0)
    assert out == {"duty": pytest.approx(6.0), "flow": pytest.approx(3.456),
                   "flow2": pytest.approx(6.912)}
    assert e.base_inputs("flow2") == {"inj", "rpm"}
    assert len(e) == 3 and e.channel("duty").expr and e.channel("zz") is None


def test_invalid_input_makes_dependants_invalid_not_an_exception():
    e = engine(("duty", "[inj] * [rpm] / 1200"), ("flow", "[duty] * 2"), ("trim", "[stft] + 1"))
    out = e.update({"inj": None, "rpm": 3000.0, "stft": 2.0}, 0.0)
    assert out == {"duty": None, "flow": None, "trim": 3.0}
    assert e.update({}, 0.1) == {"duty": None, "flow": None, "trim": None}


def test_cycles_are_refused_with_the_loop_spelled_out():
    with pytest.raises(MathError, match="loop: a -> b -> c -> a"):
        engine(("a", "[b] + 1"), ("b", "[c] + 1"), ("c", "[a] + 1"))
    with pytest.raises(MathError, match="loop: a -> a"):
        engine(("a", "[a-1] + 1"))


@pytest.mark.parametrize("defs, kw, why", [
    ([("a", "[nope] + 1")], {}, "unknown name 'nope'"),
    ([("a", "1 +")], {}, "math channel 'a'"),
    ([("a", "1"), ("a", "2")], {}, "duplicate"),
    ([("rpm", "1")], {}, "same name as a measured channel"),
    ([("k", "1")], {"constants": {"k": 2.0}}, "same name as a constant"),
])
def test_bad_definitions(defs, kw, why):
    with pytest.raises(MathError, match=why):
        engine(*defs, **kw)


def test_bad_channel_fields():
    with pytest.raises(MathError):
        MathChannel("9lives", "1")
    with pytest.raises(MathError):
        MathChannel("a", "  ")
    with pytest.raises(MathError):
        MathChannel("a", "1", kind="gauge")
    flag = MathChannel("lean", "[o2] < 0.2", kind="flag")
    assert (flag.min, flag.max, flag.decimals, flag.label) == (0.0, 1.0, 0, "lean")
    assert MathChannel("a", "1", decimals=2).format(1.234) == "1.23"
    assert MathChannel("a", "1").format(None) == "--"


def test_constants_and_disabled_channels():
    e = MathEngine([MathChannel("flow", "[inj] * cc", units="cc"),
                    MathChannel("off", "[nope]", enabled=False)],
                   ["inj"], constants={"cc": 240})
    assert e.names == ["flow"] and e.update({"inj": 2.0}, 0) == {"flow": 480.0}


def test_unit_warnings_do_not_stop_anything():
    e = MathEngine([MathChannel("odd", "[ect] + [map]"),
                    MathChannel("dt", "[ect] - [iat]", units="kPa")],
                   ["ect", "map", "iat"], units={"ect": "°C", "map": "kPa", "iat": "°C"})
    assert any("odd" in w and "mixes" in w for w in e.warnings)
    assert any("dt" in w and "declared" in w for w in e.warnings)
    assert e.update({"ect": 80.0, "map": 30.0, "iat": 20.0}, 0)["odd"] == 110


def test_runs_at_the_rate_of_its_fastest_input():
    e = engine(("fast", "[rpm] * 2"), ("slow", "[map] + 1"), ("both", "[fast] + [slow]"),
               ("clock", "time()"))
    v = {"rpm": 1000.0, "map": 40.0}
    assert e.update(v, 0.0) == {"fast": 2000, "slow": 41, "both": 2041, "clock": 0.0}
    v = {"rpm": 1100.0, "map": 99.0}
    out = e.update(v, 0.1, changed={"rpm"})             # only RPM was re-read
    assert out == {"fast": 2200, "slow": 41, "both": 2241, "clock": 0.1}
    out = e.update(v, 0.2, changed={"map"})
    assert out == {"fast": 2200, "slow": 100, "both": 2300, "clock": 0.2}


def test_extend_and_reset():
    e = engine(("d", "[rpm] - [rpm-1]"))
    assert e.extend({"rpm": 100.0}, 0) == {"rpm": 100.0, "d": None}
    assert e.update({"rpm": 150.0}, 0.1) == {"d": 50}
    e.reset()
    assert e.update({"rpm": 170.0}, 0.2) == {"d": None}


def test_replay_matches_live():
    e = engine(("duty", "[inj] * [rpm] / 1200"), ("rate", "slope([rpm], 400)"),
               ("smooth", "smooth([duty], 3)"), ("flag", "[rate] > 100 && [duty] > 2"))
    rng = np.random.default_rng(1)
    n = 200
    t = np.cumsum(rng.uniform(0.04, 0.11, n))
    rpm = 800 + 2000 * np.abs(np.sin(t))
    inj = 2 + rng.uniform(0, 2, n)
    inj[[5, 50, 51]] = np.nan
    series = e.compute_series(t, {"rpm": rpm, "inj": inj})
    e.reset()
    for i in range(n):
        row = {"rpm": float(rpm[i]), "inj": None if np.isnan(inj[i]) else float(inj[i])}
        live = e.update(row, float(t[i]))
        for name, value in live.items():
            if value is None:
                assert np.isnan(series[name][i]), (name, i)
            else:
                assert series[name][i] == pytest.approx(value), (name, i)
    # a column missing from the log gives NaN, not an exception
    assert np.isnan(e.compute_series(t, {"rpm": rpm})["duty"]).all()


def test_shipped_math_channels_on_plausible_values():
    amap = am.load_default()
    e = amap.math_engine()
    assert e.warnings == []
    assert {"inj_duty", "fuel_flow", "total_trim", "rpm_rate", "load", "afr_est"} <= set(e.names)
    v = {c.name: 0.0 for c in amap.channels}
    v.update(rpm=3000.0, inj=4.0, map=60.0, baro=100.0, iat=27.0, stft=3.0, ltft=-1.0, batt=14.0)
    e.update(v, 0.0)
    v["rpm"] = 3100.0
    out = e.update(v, 0.5)
    assert out["inj_duty"] == pytest.approx(4 * 3100 / 1200)
    assert out["fuel_flow"] == pytest.approx(out["inj_duty"] / 100 * 240 * 4 * 60 / 1000)
    assert out["total_trim"] == 2.0
    assert out["rpm_rate"] == pytest.approx(200.0)
    assert out["load"] == 60.0
    assert 10 < out["afr_est"] < 20                     # petrol-ish, by construction
    assert out["batt_min_5s"] == 14.0 and out["rpm_smooth"] == 3050.0
    # engine stopped: no fuel, so AFR has no value rather than a silly number
    v.update(rpm=0.0, inj=0.0)
    assert e.update(v, 1.0)["afr_est"] is None
