"""tools/poke_test.py: the step parser, the stimulus analysis (synthetic runs
with a planted responding bit), the cable guard, and a short end-to-end run
against the built-in simulator."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import poke_test as pt  # noqa: E402

# ------------------------------------------------------------ step parser --


def test_default_steps_are_valid_and_cover_the_flag_tests():
    names = [s.name for s in pt.DEFAULT_STEPS]
    assert len(names) == len(set(names))
    for s in pt.DEFAULT_STEPS:
        pt._check_step(pt.asdict(s), "default")          # every default passes its own checks
    by = {s.name: s for s in pt.DEFAULT_STEPS}
    assert by["baseline"].baseline
    assert by["steering"].repeat == 6 and len(by["steering"].prompts) == 2
    assert by["steering"].hold_s == 5 and by["steering"].rest_s == 10
    assert by["brake"].hold_s == 10 and by["brake"].rest_s == 10 and by["brake"].repeat == 2
    assert by["elec_load"].fast == "0x00-0x1F" and by["elec_load"].repeat == 5
    assert by["elec_load"].hold_s == 10 and by["elec_load"].rest_s == 20
    assert by["keyon"].engine == "off" and by["keyon"].hold_s == 30 and by["keyon"].mark
    assert by["engine_stop"].optional and by["engine_stop"].trans == "manual"
    assert by["scs"].optional and by["scs"].engine == "off"
    assert by["gear_auto"].trans == "auto" and by["gear_manual"].trans == "manual"
    for s in pt.DEFAULT_STEPS:                           # engine-off steps say so, and restart
        if s.engine == "off":
            assert s.undo and "START" in s.undo
    assert "vtec" not in " ".join(names).lower()


def test_parse_replaces_list_and_inherits_defaults_by_name():
    steps = pt.parse_steps("""
[[step]]
name = "ac"
hold_s = 30

[[step]]
name = "horn"
prompt = "Sound the horn."
hold_s = 3
undo = "Stop."
""")
    assert [s.name for s in steps] == ["ac", "horn"]
    ac = steps[0]
    assert ac.hold_s == 30.0 and ac.settle_s == 5 and ac.sim == "ac_on"   # inherited
    assert steps[1].undo == "Stop." and steps[1].rest_s == 10.0           # dataclass default


def test_parse_include_defaults_overrides_in_place():
    steps = pt.parse_steps("""
include_defaults = true
[[step]]
name = "brake"
repeat = 4
[[step]]
name = "wipers"
prompt = "Wipers on."
""")
    names = [s.name for s in steps]
    assert names[: len(pt.DEFAULT_STEPS)] == [s.name for s in pt.DEFAULT_STEPS]
    assert names[-1] == "wipers"
    assert next(s for s in steps if s.name == "brake").repeat == 4


@pytest.mark.parametrize("text, msg", [
    ('[[step]]\nname = "x"\n', "prompt is required"),
    ('[[step]]\nname = "x"\nprompt = "p"\nhold_s = -1\n', "hold_s"),
    ('[[step]]\nname = "x"\nprompt = "p"\nhold_s = 0\n', "hold_s must be > 0"),
    ('[[step]]\nname = "x"\nprompt = "p"\nfoo = 1\n', "unknown key"),
    ('[[step]]\nname = "x"\nprompt = "p"\nengine = "off"\n', "needs an undo"),
    ('[[step]]\nname = "x"\nprompt = "p"\nrepeat = 0\n', "repeat"),
    ('[[step]]\nname = "x"\nprompt = "p"\nsim = "explode"\n', "sim must be"),
    ('[[step]]\nname = "x"\nprompt = "p"\nfast = "all"\n', "fast must look like"),
    ('[[step]]\nname = "x"\nprompt = "p"\ntrans = "cvt"\n', "trans"),
    ('[[step]]\nname = "x"\nprompt = "p"\nprompts = []\nundos = [1]\n', "undos"),
    ('[[step]]\nname = "x y"\nprompt = "p"\n', "name must be"),
    ('[[step]]\nname = "x"\nprompt = "p"\n[[step]]\nname = "x"\nprompt = "q"\n', "used twice"),
    ('steps = 1\n', "unknown top-level"),
    ('[[step]\n', "not valid TOML"),
    ('', r"no \[\[step"),
])
def test_parse_rejects(text, msg):
    with pytest.raises(pt.StepError, match=msg):
        pt.parse_steps(text)


def test_select_applicable_and_durations():
    steps = pt.select_steps(pt.DEFAULT_STEPS, "baseline, brake")
    assert [s.name for s in steps] == ["baseline", "brake"]
    with pytest.raises(pt.StepError, match="unknown step"):
        pt.select_steps(pt.DEFAULT_STEPS, "nope")
    auto = [s.name for s in pt.applicable(pt.DEFAULT_STEPS, "auto")]
    assert "gear_auto" in auto and "gear_manual" not in auto and "engine_stop" not in auto
    brake = next(s for s in pt.DEFAULT_STEPS if s.name == "brake")
    assert brake.duration_s() == 2 * (0 + 10 + 0 + 10)
    assert brake.scaled(0.5).hold_s == 5
    text = pt.describe_steps(pt.applicable(pt.DEFAULT_STEPS, "manual"))
    assert "timed total" in text and "OFF" in text
    st = next(s for s in pt.DEFAULT_STEPS if s.name == "steering")
    assert "LEFT" in st.prompt_for(1) and "RIGHT" in st.prompt_for(2) and "LEFT" in st.prompt_for(5)


def test_fast_blocks():
    assert pt.fast_blocks("0x00-0x1F") == ((0x00, 16), (0x10, 16))
    assert pt.fast_blocks("0x08-0x0F") == ((0x08, 8),)
    assert pt.fast_blocks("") == pt.FULL_BLOCKS


# --------------------------------------------------------------- statistics --


def test_fisher_and_mann_whitney():
    assert pt.fisher_p(5, 10, 5, 10) == pytest.approx(1.0)
    assert pt.fisher_p(10, 10, 0, 10) < 1e-4
    assert pt.fisher_p(3, 3, 0, 3) == pytest.approx(0.1, abs=1e-9)     # 2 / C(6,3)
    assert pt.mann_whitney_p([1, 2, 3], [1, 2, 3]) > 0.5
    assert pt.mann_whitney_p(list(range(20, 40)), list(range(0, 20))) < 1e-5
    assert pt.mann_whitney_p([5] * 10, [5] * 10) == 1.0


# ------------------------------------------------------- synthetic analysis --


def _synth_run(seed: int = 3, planted=(0x5D, 2), byte_addr=0x6A, rpm_driven=0x9C):
    """A run: baseline, then 'lamp' (x3: the planted bit goes 1 in the hold, and 0x6A
    rises by 20), then 'rev' (rpm up; 0x9C follows rpm everywhere)."""
    rng = random.Random(seed)
    steps = [
        {"name": "baseline", "prompt": "rest", "baseline": True, "engine": "running"},
        {"name": "lamp", "prompt": "lamp on", "engine": "running", "repeat": 3},
        {"name": "rev", "prompt": "rev it", "engine": "running", "repeat": 3},
    ]
    phases, sweeps = [], []
    t = 0.0
    state = {"lamp": False, "rev": False}

    def emit(n, step, rep, phase):
        nonlocal t
        t0 = t
        for _ in range(n):
            vals = [rng.choice((7, 8)) for _ in range(256)]          # mild noise everywhere
            vals[0x20] = rng.randrange(256)                          # pure noise byte
            rpm = 3000 if state["rev"] else 800 + rng.randrange(-20, 21)
            vals[0], vals[1] = divmod(rpm * 4, 256)
            vals[rpm_driven] = min(255, rpm // 20 + rng.randrange(-1, 2))
            a, b = planted
            vals[a] = (vals[a] & ~(1 << b)) | ((1 << b) if state["lamp"] else 0)
            vals[byte_addr] = 100 + (20 if state["lamp"] else 0) + rng.randrange(-2, 3)
            sweeps.append(pt.Sweep(t + 0.01, t + 0.6, vals))
            t += 1.0
        phases.append({"step": step, "rep": rep, "phase": phase, "t0": t0, "t1": t})

    emit(20, "baseline", 1, "hold")
    for rep in (1, 2, 3):
        emit(1, "lamp", rep, "wait")
        state["lamp"] = True
        emit(2, "lamp", rep, "settle")
        emit(8, "lamp", rep, "hold")
        state["lamp"] = False
        emit(1, "lamp", rep, "undo_wait")
        emit(2, "lamp", rep, "undo_settle")
        emit(8, "lamp", rep, "rest")
    for rep in (1, 2, 3):
        emit(1, "rev", rep, "wait")
        state["rev"] = True
        emit(6, "rev", rep, "hold")
        state["rev"] = False
        emit(8, "rev", rep, "rest")
    return sweeps, {"steps": steps, "phases": phases, "events": []}


def test_planted_bit_ranks_first_and_noise_is_quiet():
    sweeps, markers = _synth_run()
    res = {r.name: r for r in pt.analyse(sweeps, markers)}
    lamp = res["lamp"]
    assert lamp.n_hold == 24 and lamp.n_base >= 15
    direct = [f for f in lamp.findings if not f.indirect]
    assert (direct[0].addr, direct[0].bit) == (0x5D, 2)
    assert direct[0].kind in ("shift", "excursion") and direct[0].effect == "+100%"
    hit = {(f.addr, f.bit) for f in lamp.findings}
    assert (0x6A, None) in hit                        # the byte that rose by 20
    assert not any(f.addr == 0x20 for f in lamp.findings)   # the noise byte stays out
    # nothing else: only the planted bit, the shifted byte (and its bits)
    assert {f.addr for f in lamp.findings} <= {0x5D, 0x6A}


def test_rpm_driven_bytes_are_flagged_indirect():
    sweeps, markers = _synth_run()
    rev = next(r for r in pt.analyse(sweeps, markers) if r.name == "rev")
    assert rev.driver_moved["rpm"]
    by = {(f.addr, f.bit): f for f in rev.findings}
    assert "is rpm" in by[(0x00, None)].indirect
    assert "rpm" in by[(0x9C, None)].indirect
    assert not any(f.addr == 0x5D for f in rev.findings)
    # in the lamp step rpm did not move, so nothing there is called indirect
    lamp = next(r for r in pt.analyse(sweeps, markers) if r.name == "lamp")
    assert not any(f.indirect for f in lamp.findings)


def test_report_and_saved_run_roundtrip(tmp_path):
    sweeps, markers = _synth_run()
    folder = tmp_path / "run"
    folder.mkdir()
    with open(folder / "wide.csv", "w", newline="", encoding="utf-8") as f:
        import csv
        w = csv.writer(f)
        w.writerow(["wall", "t", "t_end", "sweep", "mode"] + [f"b{i:02X}" for i in range(256)])
        for i, sw in enumerate(sweeps):
            w.writerow(["", sw.t0, sw.t1, i, "full"] + [("" if v is None else v) for v in sw.vals])
    (folder / "markers.json").write_text(json.dumps(markers), encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps({"port": "sim:", "labels": pt.DEFAULT_LABELS}),
                                      encoding="utf-8")
    assert pt.main(["analyse", str(folder)]) == 0
    text = (folder / "report.md").read_text(encoding="utf-8")
    assert "## lamp" in text and "0x5D b2" in text and "## Per address" in text
    assert "Flag bytes 0x08-0x0F" in text
    assert "0x5D b2: lamp" in text                     # an unnamed address that responded
    data = json.loads((folder / "findings.json").read_text(encoding="utf-8"))
    assert data[0]["step"] == "lamp"


def test_brief_blink_at_the_switch_is_an_edge():
    """0x0C b0 style: always 1, drops to 0 only for the sweeps right at the switching."""
    rng = random.Random(1)
    phases, sweeps = [], []
    t = 0.0

    def emit(n, step, rep, phase, blink=False):
        nonlocal t
        t0 = t
        for i in range(n):
            vals = [7] * 256
            vals[0], vals[1] = divmod(800 * 4, 256)
            vals[0x0C] = 0x00 if (blink and i < 2) else 0x01
            vals[0x30] = rng.randrange(5)
            sweeps.append(pt.Sweep(t + 0.001, t + 0.06, vals))
            t += 0.07
        phases.append({"step": step, "rep": rep, "phase": phase, "t0": t0, "t1": t})

    emit(200, "load", 0, "rest")
    for rep in range(1, 6):
        emit(10, "load", rep, "wait", blink=True)
        emit(140, "load", rep, "hold")
        emit(10, "load", rep, "undo_wait")
        emit(280, "load", rep, "rest")
    markers = {"steps": [{"name": "load", "prompt": "all on", "engine": "running",
                          "repeat": 5, "fast": "0x00-0x1F", "setup": "all off"}],
               "phases": phases}
    r = pt.analyse(sweeps, markers)[0]
    assert not any(f.addr == 0x0C for f in r.findings)      # nothing in the hold
    edge = [f for f in r.edges if f.addr == 0x0C]
    assert edge and edge[0].bit == 0 and edge[0].kind == "edge"
    assert r.bitfrac["0x0C.0"] == (1.0, 1.0)


def test_engine_off_timeline_and_mark():
    phases, sweeps = [], []
    t = 0.0

    def emit(n, phase, fn):
        nonlocal t
        t0 = t
        for i in range(n):
            sweeps.append(pt.Sweep(t + 0.01, t + 0.6, fn(i)))
            t += 1.0
        phases.append({"step": "keyon", "rep": 1 if phase != "rest0" else 0,
                       "phase": "rest" if phase.startswith("rest") else phase, "t0": t0, "t1": t})

    def running(i):
        v = [0] * 256
        v[0], v[1] = divmod(800 * 4, 256)
        v[0x0B] = 0x41
        return v

    def silent(i):
        return [None] * 256

    def keyon(i):
        v = [0] * 256
        v[0x0B] = (0x01 if i < 2 else 0) | (0x20 if i < 5 else 0)   # pump prime 2 s, MIL 5 s
        return v

    emit(20, "rest0", running)
    emit(1, "wait_pre", running)
    emit(5, "pre", silent)
    emit(1, "wait", silent)
    emit(30, "hold", keyon)
    emit(2, "undo_wait", keyon)
    emit(10, "undo_settle", running)
    emit(20, "rest", running)
    hold_t0 = next(p["t0"] for p in phases if p["phase"] == "hold")
    markers = {"steps": [{"name": "keyon", "prompt": "key on", "engine": "off", "repeat": 1,
                          "pre": "key off", "undo": "start"}],
               "phases": phases,
               "events": [{"step": "keyon", "rep": 1, "label": "MIL out", "t": hold_t0 + 5.0}]}
    r = pt.analyse(sweeps, markers)[0]
    text = "\n".join(r.timeline)
    assert "ECU silent" in text
    assert "0x0B b0 1->0" in text and "0x0B b5 1->0" in text
    assert r.marks and "0x0B b5 1->0" in r.marks[0] and "+5.0 s" in r.marks[0]
    assert any(f.addr == 0x0B and f.bit == 6 for f in r.findings)    # engine-turning bit off


# ------------------------------------------------------------- cable guard --


def test_take_cable_refuses_when_paused(tmp_path):
    pause = tmp_path / ".watch-pause"
    pause.write_text("x")
    with pytest.raises(SystemExit, match="already exists"):
        with pt.take_cable("COM21", print, pause=pause, procs=lambda: [],
                           closer=lambda say: None, guard_console=False):
            pass
    assert pause.exists()                    # someone else's pause file is left alone


def test_take_cable_refuses_other_port_users(tmp_path):
    pause = tmp_path / ".watch-pause"
    procs = [{"ProcessId": 999999, "Name": "python.exe",
              "CommandLine": r"python.exe C:\x\Scripts\hondalog-log.exe --port COM21"},
             {"ProcessId": 999998, "Name": "hondalog.exe", "CommandLine": "hondalog.exe --port COM21"}]
    with pytest.raises(SystemExit, match="another program"):
        with pt.take_cable("COM21", print, pause=pause, procs=lambda: procs,
                           closer=lambda say: None, guard_console=False):
            pass
    assert not pause.exists()


def test_take_cable_always_gives_back(tmp_path):
    pause = tmp_path / ".watch-pause"
    closed = []
    app = [{"ProcessId": 999998, "Name": "hondalog.exe", "CommandLine": "hondalog.exe --port COM21"}]
    with pytest.raises(KeyboardInterrupt):
        with pt.take_cable("COM21", print, pause=pause, procs=lambda: app,
                           closer=lambda say: closed.append(1), guard_console=False) as times:
            assert pause.exists() and closed == [1]
            raise KeyboardInterrupt
    assert not pause.exists() and "give" in times


def test_run_refuses_the_handheld_port():
    with pytest.raises(SystemExit, match="handheld"):
        pt.main(["run", "--port", "COM23", "--auto"])


# --------------------------------------------------------- end to end, sim --


def test_end_to_end_on_the_simulator(tmp_path, monkeypatch):
    """A short run on sim: with the A/C and brake played by the simulated owner: the
    A/C switch and brake bits must come out as direct responses. The pause file is
    never touched on sim:."""
    pause = tmp_path / "never"
    monkeypatch.setattr(pt, "PAUSE_FILE", pause)
    out = tmp_path / "run"
    steps = tmp_path / "steps.toml"
    steps.write_text("""
[[step]]
name = "baseline"
hold_s = 6
[[step]]
name = "ac"
settle_s = 1
hold_s = 5
undo_settle_s = 1
rest_s = 5
[[step]]
name = "brake"
settle_s = 0
hold_s = 4
rest_s = 5
""", encoding="utf-8")
    rc = pt.main(["run", "--dry-run", "--auto", "--no-beep", "--steps-file", str(steps),
                  "--period", "0", "--out", str(out)])
    assert rc == 0 and not pause.exists()
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["port"] == "sim:" and meta["reads_ok"] > 0 and meta["cable"] == {}
    findings = {f["step"]: f for f in json.loads((out / "findings.json").read_text(encoding="utf-8"))}
    ac = {(x["addr"], x["bit"]) for x in findings["ac"]["findings"] if not x["indirect"]}
    assert (0x08, 1) in ac and (0x0B, 1) in ac           # A/C switch and clutch
    brake = {(x["addr"], x["bit"]) for x in findings["brake"]["findings"] if not x["indirect"]}
    assert (0x08, 3) in brake
    reads = (out / "reads.csv").read_text(encoding="utf-8")
    assert ",0x00,16," in reads and ",0xF0,16," in reads
