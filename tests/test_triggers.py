"""The trigger engine, and the 0.1.0 stall behaviour as presets."""

import numpy as np
import pytest

from hondalog.analysis import ExprError
from hondalog.triggers import (MANUAL, TriggerEngine, TriggerRule, default_rules, extra_rules,
                               load_rules, merge_rules, scan, stall_rules)


def engine(**opts):
    return TriggerEngine(stall_rules(**opts))


def feed(eng, t, rpm, link=True):
    """One sweep, the way the recorder presents it: values unknown when the link is down."""
    fired, done = eng.feed(t, {"rpm": rpm, "link": 1} if link else {"rpm": None, "link": 0})
    return (fired[0].reason if fired else None), done


# ---- the four stall catcher tests from 0.1.0, on the engine ----

def test_stall_windows():
    eng = engine(pre_s=30, post_s=10)
    t = 0.0
    while t < 50:
        assert feed(eng, t, 800) == (None, None)
        t += 0.1
    reason, _ = feed(eng, t, 0)
    assert reason == "rpm" and eng.capturing
    stall_t, done = t, None
    while done is None:
        t += 0.1
        _, done = feed(eng, t, 0)
    assert done.complete and done.t == stall_t and done.first.rule.label == "STALL (rpm 0)"
    assert done.t_start == pytest.approx(stall_t - 30) and done.t_end - stall_t == pytest.approx(10, abs=0.11)
    assert done.t_end - stall_t >= 10 and not eng.capturing


def test_stall_needs_running_engine_first():
    eng = engine()
    for i in range(50):                 # key on, engine off: never triggers
        assert feed(eng, i * 0.1, 0)[0] is None
    for i in range(50, 80):             # cranking below 400 does not arm it
        assert feed(eng, i * 0.1, 250)[0] is None
    assert feed(eng, 8.0, 0)[0] is None and not eng.armed("stall")


def test_stall_link_loss():
    eng = engine(post_s=1, link_down_s=1.0)
    for i in range(20):
        feed(eng, i * 0.1, 900)
    assert feed(eng, 2.0, None, link=False)[0] is None      # one bad sweep is not a stall
    assert feed(eng, 2.1, 900)[0] is None
    assert feed(eng, 2.2, None, link=False)[0] is None
    assert feed(eng, 2.8, None, link=False)[0] is None
    assert feed(eng, 3.3, None, link=False)[0] == "link"
    done = eng.finish()                                     # recording stopped early
    assert done.first.reason == "link" and not done.complete
    assert "link lost" in done.first.rule.message and eng.finish() is None


def test_stall_rearms():
    eng = engine(post_s=0.2)
    feed(eng, 0, 800)
    assert feed(eng, 1, 0)[0] == "rpm"
    assert feed(eng, 1.3, 0)[1] is not None
    assert feed(eng, 2, 0)[0] is None                       # still dead: no second capture
    feed(eng, 3, 900)
    assert feed(eng, 4, 0)[0] == "rpm" and eng.fired_count("stall") == 2


def test_key_off_after_a_stall_is_not_a_second_capture():
    eng = engine(post_s=0.5)
    feed(eng, 0, 800)
    assert feed(eng, 1, 0)[0] == "rpm"
    seen = [feed(eng, 1 + i * 0.2, None, link=False)[0] for i in range(1, 30)]   # key off
    assert seen == [None] * 29 and not eng.armed("link_loss")
    feed(eng, 10, 900, link=True)
    assert eng.armed("link_loss") and eng.armed("stall")


def test_silent_ecu_cannot_look_like_rpm_zero():
    eng = engine(link_down_s=5)
    feed(eng, 0, 800)
    assert feed(eng, 0.1, None, link=False)[0] is None      # rpm unknown, not "below 50"
    assert feed(eng, 0.2, None, link=True)[0] is None


# ---- the general engine ----

def test_hold_and_rearm_condition():
    rule = TriggerRule("overtemp", when="ect > 105", rearm_when="ect < 100", hold_s=2, post_s=1, pre_s=5)
    eng = TriggerEngine([rule])
    assert rule.label == "OVERTEMP" and rule.kind == "overtemp" and rule.reason == "overtemp"
    assert eng.feed(0, {"ect": 106})[0] == []
    assert eng.feed(1.5, {"ect": 104})[0] == []             # dipped: the hold starts again
    assert eng.feed(2.0, {"ect": 107})[0] == []
    assert eng.feed(3.9, {"ect": 107})[0] == []
    fired, _ = eng.feed(4.0, {"ect": 108})
    assert [f.rule.name for f in fired] == ["overtemp"] and fired[0].values["ect"] == 108
    assert eng.feed(4.5, {"ect": 103})[0] == [] and not eng.armed("overtemp")   # not below 100 yet
    assert eng.feed(9.0, {"ect": 107})[0] == []
    eng.feed(10, {"ect": 99})
    assert eng.armed("overtemp")
    eng.feed(11, {"ect": 106})
    assert len(eng.feed(13, {"ect": 106})[0]) == 1


def test_default_rearm_is_condition_going_false():
    eng = TriggerEngine([TriggerRule("low", when="batt < 11", post_s=0, pre_s=0)])
    assert len(eng.feed(0, {"batt": 10})[0]) == 1
    assert eng.feed(1, {"batt": 10})[0] == []
    eng.feed(2, {"batt": 12})
    assert len(eng.feed(3, {"batt": 10.5})[0]) == 1


def test_partial_updates_keep_latest_values():
    """Reads arrive one block at a time; a rule can span blocks."""
    eng = TriggerEngine([TriggerRule("lean", when="o2 < 0.1 and tps > 50", post_s=0)])
    eng.feed(0.00, {"tps": 80})
    assert eng.feed(0.02, {"o2": 0.5})[0] == []
    assert len(eng.feed(0.04, {"o2": 0.05})[0]) == 1        # tps came from an earlier read
    assert eng.values == {"tps": 80, "o2": 0.05}


def test_overlapping_triggers_make_one_bundle():
    a = TriggerRule("a", when="x > 1", pre_s=5, post_s=2)
    b = TriggerRule("b", when="y > 1", pre_s=20, post_s=10)
    quiet = TriggerRule("quiet", when="z > 1", actions=("mark",))
    eng = TriggerEngine([a, b, quiet], max_capture_s=11)
    seen, captures = [], []
    eng.on_fired = seen.append
    eng.on_capture = captures.append
    eng.feed(100, {"x": 2})
    eng.feed(101, {"z": 2})                                 # marks only: no window change
    assert eng.capture.t_end == 102
    eng.feed(101.5, {"y": 2})
    assert eng.capture.t_start == 81.5 and eng.capture.t_end == 111    # capped at max_capture_s
    assert eng.feed(110.9, {})[1] is None
    _, done = eng.feed(111.2, {})
    assert [f.rule.name for f in done.fired] == ["a", "b"] and done.t == 100 and done.t_end == 111.2
    assert [f.rule.name for f in seen] == ["a", "quiet", "b"] and captures == [done]


def test_manual_fire_enable_remove():
    eng = TriggerEngine(default_rules())
    assert [r.name for r in eng.rules] == ["stall", "link_loss", "low_battery", "lean_spike", "overtemp"]
    assert [r.enabled for r in eng.rules] == [True, True, False, False, False]
    assert eng.feed(0, {"batt": 9, "ect": 120})[0] == []    # disabled rules stay quiet
    eng.enable("low_battery")
    eng.feed(1, {"batt": 9})
    assert len(eng.feed(1.6, {"batt": 9})[0]) == 1
    eng.finish()
    event = eng.fire(MANUAL, 5.0)
    assert event.rule.kind == "capture" and eng.capture.t_start == -25 and eng.capture.t_end == 15
    eng.finish()
    assert eng.fire("overtemp", 6.0).rule.name == "overtemp"            # by name, even if disabled
    eng.remove("overtemp")
    eng.remove("overtemp")
    assert "overtemp" not in [r.name for r in eng.rules]
    eng.add(TriggerRule("stall", when="rpm < 10"))                      # replaces by name
    assert [r.when for r in eng.rules if r.name == "stall"] == ["rpm < 10"]


def test_rule_validation_and_dicts():
    for bad in (dict(name="", when="x"), dict(name="a", when=""), dict(name="a", when="x >"),
                dict(name="a", when="x", arm_when="import os"), dict(name="a", when="x", actions=("explode",)),
                dict(name="a", when="x", hold_s=-1)):
        with pytest.raises((ValueError, ExprError)):
            TriggerRule(**bad)
    for rule in default_rules():
        assert TriggerRule.from_dict(rule.to_dict()) == rule
    assert TriggerRule.from_dict({"name": "n", "when": "x", "bogus": 1, "actions": ["mark"]}).actions == ("mark",)
    assert all(not r.enabled for r in extra_rules())


def test_load_rules_from_toml(tmp_path):
    path = tmp_path / "triggers.toml"
    path.write_text('[[trigger]]\nname = "stall"\nwhen = "rpm < 100"\narm_when = "rpm > 500"\n\n'
                    '[[trigger]]\nname = "cold"\nwhen = "ect < 0"\nhold_s = 3\nactions = ["mark"]\n')
    rules = load_rules(path)
    assert [r.name for r in rules] == ["stall", "cold"] and rules[1].hold_s == 3
    merged = merge_rules(default_rules(), rules)
    assert len(merged) == 6 and merged[0].when == "rpm < 100" and merged[-1].name == "cold"
    path.write_text('[[trigger]]\nname = "x"\n')
    with pytest.raises(ValueError, match="'x'"):
        load_rules(path)
    path.write_text('[[trigger]]\nname = "x"\nwhen = "a b"\n')
    with pytest.raises(ExprError):
        load_rules(path)


def test_scan_a_finished_log():
    t = np.arange(0, 60, 0.1)
    rpm = np.where((t > 20) & (t < 25), 0.0, 850.0)
    rpm[400:420] = np.nan                                   # 2 s link gap: a dropout, not a stall
    rpm[450:500] = np.nan                                   # 5 s link gap: unknown, not zero
    fired = scan(stall_rules(), t, {"rpm": rpm, "link": np.where(np.isnan(rpm), 0.0, 1.0)})
    assert [(f.reason, round(f.t, 1)) for f in fired] == [("rpm", 20.1), ("link", 48.0)]


# ---- link dropouts with the engine running (2026-11-26) ----

def test_short_link_dropout_is_not_a_stall():
    # drive 2026-11-26 20:45: 96 dropouts, the longest 1.5 s, engine idling throughout
    eng = engine()
    for i in range(100):
        feed(eng, i * 0.1, 700)
    t = 10.0
    while t < 11.5:
        assert feed(eng, t, None, link=False)[0] is None
        t += 0.1
    assert feed(eng, 11.6, 690)[0] is None and not eng.capturing


def test_link_loss_with_the_engine_running_when_it_answers_is_revoked():
    eng = engine(post_s=10, link_down_s=3.0)
    revoked = []
    eng.on_revoked = lambda f, t: revoked.append((f.reason, t))
    for i in range(100):
        feed(eng, i * 0.1, 700)
    reasons = [feed(eng, 10.0 + i * 0.1, None, link=False)[0] for i in range(33)]
    assert reasons[-3:].count("link") == 1 and eng.capturing and eng.reviewing()
    assert eng.capture.t_start == pytest.approx(13.0 - 33.0, abs=0.11)   # 30 s before the silence
    feed(eng, 13.6, 650)                                    # back, engine still at idle
    assert revoked == [("link", 13.6)] and not eng.reviewing()
    done = None
    t = 13.7
    while done is None:
        _, done = feed(eng, t, 650)
        t += 0.1
    assert done.cancelled and done.first.rule.cancel_kind == "dropout"
    assert eng.armed("stall") and eng.armed("link_loss")    # the stall hunt goes on


def test_link_loss_then_key_on_at_rpm_zero_stands():
    eng = engine(post_s=10, link_down_s=3.0)
    eng.on_revoked = lambda f, t: pytest.fail("a dead ECU must stay a stall")
    for i in range(100):
        feed(eng, i * 0.1, 800)
    t = 10.0
    while t < 30.0:                                          # ECU dead (power loss), then key on
        feed(eng, t, None, link=False)
        t += 0.1
    feed(eng, 30.0, 0)                                      # first answer: key on, engine stopped
    feed(eng, 31.0, 250)                                    # cranking
    feed(eng, 32.0, 1400)                                   # running again: too late to revoke
    assert not eng.reviewing()


def test_review_without_a_verdict_in_time_stands():
    eng = engine(post_s=1, link_down_s=3.0, review_s=5.0)
    eng.on_revoked = lambda f, t: pytest.fail("no verdict in time")
    for i in range(100):
        feed(eng, i * 0.1, 800)
    t = 10.0
    while t < 20.0:
        feed(eng, t, None, link=False)
        t += 0.1
    feed(eng, 20.0, 800)                                    # 7 s after the firing


def test_link_loss_answered_in_an_after_start_flare_stands():
    # 2026-11-25 18:17 (OBD2-only mode): key off at 670 rpm, the slow ISO init
    # reconnected only after the restart, at 1,446 rpm
    eng = engine(post_s=10, link_down_s=3.0)
    eng.on_revoked = lambda f, t: pytest.fail("a restart flare is not a running engine")
    for i in range(100):
        feed(eng, i * 0.1, 670)
    t = 10.0
    while t < 30.0:
        feed(eng, t, None, link=False)
        t += 0.1
    feed(eng, 30.0, 1446)
    assert not eng.reviewing()
