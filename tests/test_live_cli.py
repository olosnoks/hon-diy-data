"""LivePipeline, the hondalog-map command line, and the minimal UI wiring."""

import os
import types

import pytest

from hondalog import addressmap as am
from hondalog.live import LivePipeline
from hondalog.mapping import __main__ as cli


def sample(t, link=True, **values):
    return types.SimpleNamespace(t=t, link=link, values=values)


def healthy(amap, **kw):
    v = {c.name: 0.0 for c in amap.channels}
    v.update(rpm=800.0, ect=88.0, batt=14.1, map=32.0, baro=101.0, inj=2.4, iat=30.0, o2=0.5)
    v.update(kw)
    return v


def test_pipeline_adds_math_and_raises_alarms():
    amap = am.load_default()
    p = LivePipeline(amap)
    assert [c.name for c in p.channels] == amap.names + p.math.names
    frame = p.feed(sample(0.0, **healthy(amap)))
    assert frame.values["inj_duty"] == pytest.approx(2.4 * 800 / 1200) and frame.events == []
    assert set(frame.values) == set(amap.all_names)
    events = []
    for i in range(1, 40):
        events += p.feed(sample(i * 0.1, **healthy(amap, ect=110.0))).events
    assert [(e.name, e.kind) for e in events] == [("ect_high", "raised")]
    assert p.alarms.top().name == "ect_high" and p.last.t == pytest.approx(3.9)
    # link lost: math goes blank, the alarm holds
    lost = p.feed(sample(4.0, link=False))
    assert lost.values["inj_duty"] is None and lost.events == []
    assert p.alarms.top() is not None
    p.reset()
    assert p.alarms.top() is None and p.last is None
    # tiered polling: only the re-read channels' math is recomputed
    p.process(healthy(amap), 0.0)
    f = p.process(healthy(amap, rpm=1600.0, map=99.0), 0.1, changed={"rpm"})
    assert f.values["inj_duty"] == pytest.approx(3.2) and f.values["load"] == pytest.approx(31.68, abs=0.01)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HONDALOG_DIR", str(tmp_path / "logs"))
    return tmp_path


def run(*argv, answers=()):
    out, replies = [], list(answers)
    code = cli.main(list(argv), say=out.append, ask=lambda prompt: replies.pop(0) if replies else "")
    return code, "\n".join(out)


def test_cli_status_scripts_hash_diff_upgrade(env, tmp_path):
    code, text = run("status")
    assert code == 0 and "0 of 22 channels verified" in text and "tps_sweep" in text
    code, text = run("scripts")
    assert code == 0 and "koeo" in text and "Throttle sweep" in text
    code, text = run("hash")
    assert code == 0 and text.split()[0] == am.load_default().version_hash
    new = tmp_path / "new.toml"
    new.write_text(am.DEFAULT_MAP.read_text(encoding="utf-8").replace('"x / 4"', '"x / 8"'),
                   encoding="utf-8")
    code, text = run("diff", str(am.DEFAULT_MAP), str(new))
    assert code == 1 and "decoding changed" in text and "channel rpm: formula" in text
    assert run("diff", str(new), str(new))[0] == 0
    old = tmp_path / "v1.toml"
    old.write_text("version = 'mine'\n[[channel]]\nname = 'rpm'\naddr = 0\nlength = 2\n"
                   "formula = 'x / 4'\n[[channel]]\nname = 'inj'\naddr = 0x24\nlength = 2\n",
                   encoding="utf-8")
    code, text = run("--map", str(old), "upgrade")
    assert code == 0 and "schema 2" in text and "left out" in text
    assert "inj_duty" in am.load(old).math_names
    code, text = run("--map", str(tmp_path / "missing.toml"), "status")
    assert code == 2 and text.startswith("error:")
    assert run("verify", "nope", "--port", "sim:")[0] == 2


def test_cli_verify_against_the_built_in_simulator(env, tmp_path):
    """Whole path: poller -> simulated ECU -> sweeps -> session -> evidence -> map file."""
    code, text = run("verify", "steady_3000", "--port", "sim:", "--auto", "--speed", "0.08",
                     "--ref", "tach_rpm=3000", "--confirm", "vss", "--force")
    assert "== Hold 3000 rpm ==" in text and "evidence saved:" in text
    assert "rpm: FAIL" in text                      # the simulator idles; nobody held 3000
    assert code == 1
    saved = list((tmp_path / "verification").glob("verify_*_steady_3000.json"))
    assert len(saved) == 1 and saved[0].with_suffix(".npz").exists()
    amap = am.load_user_map()
    vss = amap.channel("vss")                       # plausibility-only, confirmed with --force
    assert vss.verified and vss.evidence.method == "guided:steady_3000"
    assert vss.evidence.result == "plausible" and vss.evidence.log == saved[0].name
    assert "vss: marked verified" in text
    code, text = run("status")
    assert "1 of 22 channels verified" in text and "guided:steady_3000" in text


def test_cli_verify_interactive_prompts(env):
    answers = ["12.3", "", "n", "", ]               # meter, ambient skipped, not cold, Enter
    code, text = run("verify", "koeo", "--port", "sim:", "--hi", "0x2F", answers=answers)
    assert "batt: FAIL" in text and "vs typed in 12.3" in text      # sim engine is running
    assert "It may live here instead" in text


def test_cli_find(env):
    code, text = run("find", "--port", "sim:", "--value", "101", "--formula", "x * 0.716 - 5",
                     "--seconds", "1.5", "--hi", "0x1F", "--tol", "0.02")
    assert code == 0 and "0x13 u8" in text          # the simulator's baro byte
    code, text = run("find", "--port", "sim:", "--seconds", "0.5", "--hi", "0x0F")
    assert code == 2


def test_ui_wiring_alarm_readout_and_finder(env):
    """The same wiring through the 1.0 shell: the alarm engine's state reaches
    the window through the AlarmSource adapter, and the raw tools' sweep
    history feeds the correlation finder."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from hondalog import ui  # noqa: F401
    from PySide6.QtWidgets import QApplication
    from hondalog.ui import scene3d
    from hondalog.ui.main_window import ALARMS, Shell
    from hondalog.ui.session import build_session
    if QApplication.instance() is None:
        scene3d.default_surface_format()
    app = QApplication.instance() or QApplication([])
    session = build_session()
    win = Shell(session)
    try:
        ecu, pipeline, bus = session.ecu, session.ecu.live.pipeline, session.bus
        assert len(pipeline.alarms.rules) >= 4
        amap = ecu.amap
        kinds = []

        def feed(i, **kw):
            frame = pipeline.feed(sample(i * 0.1, **healthy(amap, **kw)))
            bus.publish(frame.values, t=bus.now())
            if frame.events:
                ecu.frames.append(frame)
            kinds.extend(e.kind for e in session.pump(bus.now()))
            win.strip.sync(session.alarms, bus.now())

        for i in range(40):
            feed(i, ect=112.0)
        assert kinds == ["alarm"]
        top = session.alarms.active()[0]
        assert top.severity == "alarm" and "Coolant 112" in top.limit_text and top.label == "COOLANT"
        assert session.alarms.state("ect") == "alarm" and session.alarms.unacked("ect")
        win.show_screen(ALARMS)
        win.frame()
        session.acknowledge()                       # what Esc does
        assert not session.alarms.unacked("ect")
        for i in range(40, 120):
            feed(i)
        assert session.alarms.active() == [] and session.alarms.history()[0].channel_id == "ect"

        # scanner history -> correlation finder
        import numpy as np
        from mapping_helpers import ram
        mapper = session.mapper
        assert mapper.rank_against("rpm") == [] and "sweep continuously" in mapper.console()
        for i in range(30):
            mapper._hist[:-1] = mapper._hist[1:]
            mapper._hist[-1] = ram(rpm=800 + 150 * (i % 9), noise=i)
            mapper._sweep_t.append(float(i))
        mapper.minv[:], mapper.maxv[:] = 0, 255
        assert len(mapper.sweep_recording()) == 30
        found = mapper.rank_against("rpm")
        assert "0x90 u16le" in mapper.console() and 0x90 in [c.addr for c in found]
        assert isinstance(np.asarray(mapper.history(0x90)), np.ndarray)
    finally:
        win.close()
        app.processEvents()
