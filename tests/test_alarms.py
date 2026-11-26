"""Alarm engine: limits, rate of change, combinations, hysteresis, latching."""

import numpy as np
import pytest

from hondalog import addressmap as am
from hondalog.alarms import (CRITICAL, WARN, AlarmEngine, AlarmError, AlarmEvent, AlarmRule)

NAMES = ["ect", "batt", "rpm", "tps", "o2"]


def run(engine, channel, values, dt=0.5, t0=0.0, **extra):
    """Feed a list of values; returns [(t, kind, name), ...]."""
    out = []
    for i, v in enumerate(values):
        t = t0 + i * dt
        for ev in engine.update({channel: v, **extra}, t):
            out.append((t, ev.kind, ev.name))
    return out


def test_acceptance_coolant_over_105_for_2s_raises_critical_within_one_cycle():
    """W3-05: ECT above 105 °C for 2 s -> critical banner within one poll cycle + marker."""
    rule = AlarmRule("ect_high", channel="ect", high=105, hold_s=2, severity="critical",
                     actions=("tile", "banner", "beep", "marker"))
    e = AlarmEngine([rule], NAMES, labels={"ect": ("Coolant", "°C")})
    seen = []
    e.subscribe(seen.append)
    dt = 0.05                                           # 20 Hz poll
    t, crossed, raised = 0.0, None, None
    while raised is None and t < 10:
        ect = 100.0 + t * 4                             # crosses 105 at t = 1.25
        if crossed is None and ect > 105:
            crossed = t
        events = e.update({"ect": ect}, t)
        if events:
            raised = t
        else:
            t += dt
    assert crossed is not None and raised is not None
    assert 2.0 <= raised - crossed < 2.0 + dt + 1e-9    # within one poll cycle of the 2 s
    (ev,) = events
    assert isinstance(ev, AlarmEvent) and ev.kind == "raised" and ev.severity == CRITICAL
    assert {"banner", "marker"} <= set(ev.actions) and seen == [ev]
    assert ev.marker_text.startswith("ALARM ect_high: ") and "Coolant" in ev.marker_text
    assert e.top().name == "ect_high" and e.severity_for("ect") == CRITICAL
    assert e.severity_for("rpm") is None


def test_hold_time_restarts_if_the_condition_breaks():
    e = AlarmEngine([AlarmRule("hot", channel="ect", high=105, hold_s=1)], NAMES)
    assert run(e, "ect", [106, 106, 100, 106, 106, 106]) == [(2.5, "raised", "hot")]


def test_hysteresis_stops_chatter_at_the_limit():
    plain = AlarmEngine([AlarmRule("hot", channel="ect", high=105)], NAMES)
    banded = AlarmEngine([AlarmRule("hot", channel="ect", high=105, hysteresis=3)], NAMES)
    wobble = [104, 106, 104.5, 106, 104.5, 106, 101, 100]
    assert [k for _, k, _ in run(plain, "ect", wobble)] == ["raised", "cleared"] * 3
    assert run(banded, "ect", wobble) == [(0.5, "raised", "hot"), (3.0, "cleared", "hot")]


def test_low_limit_and_both_limits():
    e = AlarmEngine([AlarmRule("batt", channel="batt", low=11.5, high=15.2, hysteresis=0.3)],
                    NAMES)
    seq = [12.5, 11.4, 11.7, 11.9, 15.3, 15.0, 14.8]
    assert [(k) for _, k, _ in run(e, "batt", seq)] == ["raised", "cleared", "raised", "cleared"]
    assert e.status("batt").count == 2


def test_clear_hold_time():
    e = AlarmEngine([AlarmRule("hot", channel="ect", high=105, clear_hold_s=1)], NAMES)
    out = run(e, "ect", [106, 100, 106, 100, 100, 100])
    assert out == [(0.0, "raised", "hot"), (2.5, "cleared", "hot")]


def test_rate_of_change():
    rule = AlarmRule("dying", channel="rpm", rate_low=-400, rate_window_ms=400,
                     gate="[rpm] < 600")
    e = AlarmEngine([rule], NAMES)
    steady = [750, 760, 745, 755, 750, 748, 752]
    assert run(e, "rpm", steady, dt=0.1) == []
    falling = [750, 700, 590, 480, 380, 250, 100, 0, 0, 0, 0, 0, 0, 0]
    out = run(e, "rpm", falling, dt=0.1, t0=1.0)
    assert [k for _, k, _ in out] == ["raised", "cleared"]
    assert out[0][0] == pytest.approx(1.3)              # second sample below the gate
    assert e.status("dying").rate == pytest.approx(0, abs=1)
    # rising fast
    e2 = AlarmEngine([AlarmRule("surge", channel="rpm", rate_high=2000)], NAMES)
    assert [k for _, k, _ in run(e2, "rpm", [800, 900, 1500, 2400, 2500, 2500, 2500, 2500,
                                             2500, 2500], dt=0.1)] == ["raised", "cleared"]


def test_combination_with_expression_and_clear_expression():
    rule = AlarmRule("lean", when="[o2] < 0.2 && [tps] > 60 && [rpm] > 3000", hold_s=0.5,
                     severity="critical")
    e = AlarmEngine([rule], NAMES)
    rows = [(0.1, 80, 4000), (0.1, 80, 4000), (0.1, 30, 4000), (0.1, 80, 4000),
            (0.1, 80, 4000), (0.8, 80, 4000)]
    out = []
    for i, (o2, tps, rpm) in enumerate(rows):
        out += [(i, ev.kind) for ev in e.update({"o2": o2, "tps": tps, "rpm": rpm}, i * 0.5)]
    assert out == [(1, "raised"), (2, "cleared"), (4, "raised"), (5, "cleared")]

    sticky = AlarmEngine([AlarmRule("hot", when="[ect] > 105", clear_when="[ect] < 95")], NAMES)
    assert run(sticky, "ect", [106, 100, 96, 94, 100]) == [(0.0, "raised", "hot"),
                                                           (1.5, "cleared", "hot")]


def test_gate_suppresses_and_clears():
    rule = AlarmRule("batt_low", channel="batt", low=12.0, gate="[rpm] > 400")
    e = AlarmEngine([rule], NAMES)
    assert e.update({"batt": 11.0, "rpm": 0.0}, 0) == []            # engine off: no alarm
    assert [x.kind for x in e.update({"batt": 11.0, "rpm": 800.0}, 1)] == ["raised"]
    assert [x.kind for x in e.update({"batt": 11.0, "rpm": 0.0}, 2)] == ["cleared"]


def test_missing_values_hold_the_state():
    e = AlarmEngine([AlarmRule("hot", channel="ect", high=105, hold_s=1),
                     AlarmRule("combo", when="[ect] > 105 && [rpm] > 400")], NAMES)
    assert run(e, "ect", [None, None], rpm=800.0) == []
    out = run(e, "ect", [106, 106, 106, None, None, 100], rpm=800.0, t0=5)
    assert out == [(5.0, "raised", "combo"), (6.0, "raised", "hot"),
                   (7.5, "cleared", "hot"), (7.5, "cleared", "combo")]
    # a dropped link (everything missing) neither raises nor clears
    e.update({"ect": 110.0, "rpm": 800.0}, 20)
    e.update({"ect": 110.0, "rpm": 800.0}, 21.5)
    assert len(e.active()) == 2
    assert e.update({}, 22) == [] and e.update({"ect": None, "rpm": None}, 23) == []
    assert len(e.active()) == 2


def test_latching_and_acknowledge():
    e = AlarmEngine([AlarmRule("hot", channel="ect", high=105, latching=True)], NAMES)
    assert run(e, "ect", [106, 100, 100]) == [(0.0, "raised", "hot")]
    st = e.status("hot")
    assert st.state == "latched" and st.active and e.top() is st
    events = e.acknowledge("hot", t=9.0)
    assert [x.kind for x in events] == ["acknowledged", "cleared"] and not st.active
    assert e.acknowledge() == []                        # nothing left to acknowledge
    # acknowledged while still tripped: stays up, clears by itself later
    assert [k for _, k, _ in run(e, "ect", [106], t0=10)] == ["raised"]
    assert [x.kind for x in e.acknowledge()] == ["acknowledged"]
    assert e.status("hot").acknowledged and e.status("hot").active
    assert e.acknowledge() == []
    assert run(e, "ect", [100], t0=11) == [(11.0, "cleared", "hot")]
    # condition returns while latched: active again, no second 'raised'
    run(e, "ect", [106, 100], t0=12)
    assert e.status("hot").state == "latched"
    assert run(e, "ect", [106], t0=13) == [] and e.status("hot").state == "active"


def test_priority_ordering():
    rules = [AlarmRule("w1", channel="batt", low=12, severity=WARN),
             AlarmRule("c1", channel="ect", high=105, severity=CRITICAL),
             AlarmRule("w2", channel="rpm", high=6800, severity=WARN, priority=200)]
    e = AlarmEngine(rules, NAMES)
    assert e.active() == [] and e.top() is None
    e.update({"batt": 11.0, "ect": 80.0, "rpm": 800.0}, 0)
    e.update({"batt": 11.0, "ect": 110.0, "rpm": 7000.0}, 1)
    assert [s.name for s in e.active()] == ["w2", "c1", "w1"]
    assert e.top().name == "w2"
    assert [r.name for r in e.rules] == ["w1", "c1", "w2"] and len(e) == 3
    assert e.inputs("c1") == {"ect"}
    assert {s.name: s.state for s in e.statuses()}["c1"] == "active"


def test_tile_colour_only_for_rules_with_the_tile_action():
    e = AlarmEngine([AlarmRule("a", channel="ect", high=100, actions=("banner",)),
                     AlarmRule("b", channel="ect", high=90, actions=("tile",))], NAMES)
    e.update({"ect": 95.0}, 0)
    assert e.severity_for("ect") == WARN
    e.update({"ect": 85.0}, 1)
    assert e.severity_for("ect") is None


def test_messages():
    labels = {"batt": ("Battery", "V"), "rpm": ("RPM", "rpm")}
    rules = [AlarmRule("custom", channel="batt", low=12,
                       message="{label}: {value:.1f} {units} at {rpm:.0f} rpm ({nope}) {value.real}"),
             AlarmRule("auto_low", channel="batt", low=12),
             AlarmRule("auto_rate", channel="rpm", rate_low=-100, label="Falling"),
             AlarmRule("expr_only", when="[rpm] > 100", label="Running")]
    e = AlarmEngine(rules, NAMES, labels=labels)
    e.update({"batt": 13.0, "rpm": 900.0}, 0)
    msgs = {ev.name: ev.message for ev in e.update({"batt": 11.456, "rpm": 800.0}, 0.1)}
    assert msgs["custom"] == "custom: 11.5 V at 800 rpm ({nope}) {value.real}"
    assert msgs["auto_low"] == "auto low: Battery 11.46 V (limit 12)"
    assert msgs["auto_rate"].startswith("Falling: RPM 800 rpm, changing -1000")
    assert e.status("expr_only").message == "Running"


def test_unverified_inputs_are_flagged():
    verified = {"ect": True, "batt": False, "rpm": False}
    e = AlarmEngine([AlarmRule("a", channel="ect", high=100),
                     AlarmRule("b", channel="batt", low=12),
                     AlarmRule("c", channel="ect", high=100, gate="[rpm] > 400")],
                    NAMES, verified=verified)
    events = e.update({"ect": 110.0, "batt": 11.0, "rpm": 800.0}, 0)
    assert {ev.name: ev.unverified for ev in events} == {"a": False, "b": True, "c": True}


def test_subscribers_and_a_broken_subscriber():
    e = AlarmEngine([AlarmRule("hot", channel="ect", high=105)], NAMES)
    got = []
    e.subscribe(lambda ev: 1 / 0)
    off = e.subscribe(got.append)
    e.update({"ect": 110.0}, 0)
    assert [ev.kind for ev in got] == ["raised"]
    off()
    off()
    e.update({"ect": 90.0}, 1)
    assert len(got) == 1


def test_reset_and_replay():
    rule = AlarmRule("hot", channel="ect", high=105, hold_s=1, hysteresis=2)
    e = AlarmEngine([rule, AlarmRule("combo", when="[ect] > 105 && [rpm] > 400")], NAMES)
    t = np.arange(10) * 0.5
    ect = np.array([100, 106, 106, 106, np.nan, 106, 104, 102, 100, 100.0])
    rpm = np.full(10, 800.0)
    e.update({"ect": 120.0, "rpm": 900.0}, 0)           # live state: combo already up
    live_before = [s.state for s in e.statuses()]
    events = e.replay(t, {"ect": ect, "rpm": rpm})
    assert [(ev.t, ev.kind, ev.name) for ev in events] == [
        (0.5, "raised", "combo"), (1.5, "raised", "hot"), (3.0, "cleared", "combo"),
        (3.5, "cleared", "hot")]
    assert [s.state for s in e.statuses()] == live_before       # live state untouched
    e.reset()
    assert e.active() == [] and e.status("hot").count == 0


@pytest.mark.parametrize("kwargs, why", [
    (dict(name="bad name"), "bad alarm name"),
    (dict(name="a"), "needs a limit"),
    (dict(name="a", high=5), "need a 'channel'"),
    (dict(name="a", channel="ect", low=5, high=1), "low must be below high"),
    (dict(name="a", channel="ect", high=5, severity="panic"), "severity"),
    (dict(name="a", channel="ect", high=5, actions=("explode",)), "unknown action"),
    (dict(name="a", channel="ect", high=5, hold_s=-1), "negative"),
    (dict(name="a", channel="ect", high="hot"), "numbers"),
    (dict(name="a", channel="ect", rate_high=5, rate_window_ms=0), "rate_window_ms"),
    (dict(name="a", channel="ect", high=5, clear_when="[ect] < 1"), "clear_when"),
])
def test_bad_rules(kwargs, why):
    with pytest.raises(AlarmError, match=why):
        AlarmRule(**kwargs)


def test_bad_engine_setup():
    with pytest.raises(AlarmError, match="unknown channel 'oil'"):
        AlarmEngine([AlarmRule("a", channel="oil", high=5)], NAMES)
    with pytest.raises(AlarmError, match="when: unknown name 'oil'"):
        AlarmEngine([AlarmRule("a", when="[oil] > 5")], NAMES)
    with pytest.raises(AlarmError, match="duplicate"):
        AlarmEngine([AlarmRule("a", when="1"), AlarmRule("a", when="1")], NAMES)
    assert len(AlarmEngine([AlarmRule("a", channel="oil", high=5, enabled=False)], NAMES)) == 0


def test_shipped_alarms_against_the_simulator_values():
    amap = am.load_default()
    alarms, math = amap.alarm_engine(), amap.math_engine()
    assert {r.name for r in alarms.rules} >= {"ect_high", "batt_low", "batt_high", "over_rev"}
    base = {c.name: 0.0 for c in amap.channels}
    base.update(rpm=780.0, ect=88.0, batt=14.2, tps=0.0, o2=0.5, map=32.0, baro=101.0, inj=2.4)
    kinds = []
    for i in range(100):                                # 10 s of healthy idle
        v = dict(base, rpm=780.0 + (i % 3) * 8)
        kinds += alarms.update({**v, **math.update(v, i * 0.1)}, i * 0.1)
    assert kinds == []
    for i in range(100, 140):                           # coolant climbs past 105
        v = dict(base, ect=88.0 + (i - 100))
        kinds += alarms.update({**v, **math.update(v, i * 0.1)}, i * 0.1)
    assert [(e.name, e.kind, e.severity) for e in kinds] == [("ect_high", "raised", "critical")]
    assert amap.channel("ect").verified and not kinds[0].unverified   # ect is verified on the P2P
    assert kinds[0].message == "Coolant 126 °C" or kinds[0].message.startswith("Coolant 1")
