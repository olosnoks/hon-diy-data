"""Adapter implementations: protocols are satisfied, and the stopgap logic
(alarms, order of loss, wizard ranking, histogram, tables) does what the
handoff says."""

import time

import numpy as np
import pytest

from hondalog.ui import adapters as ad
from hondalog.ui import backends as be
from hondalog.ui.bus import ChannelBus, ChannelSpec
from hondalog.ui.session import build_session


def test_implementations_satisfy_the_protocols():
    ecu = be.PollerSource()
    assert isinstance(ecu, ad.ChannelSource) and isinstance(ecu, ad.Connectable)
    assert isinstance(be.SimBlackBox(ecu), ad.ChannelSource)
    assert isinstance(be.DemoSource("ECU"), ad.ChannelSource)
    assert isinstance(be.LimitAlarms(), ad.AlarmSource)
    assert isinstance(be.CsvRecording(ecu), ad.Recording)
    assert isinstance(be.NullRecording(), ad.Recording)
    assert isinstance(be.CsvLogReader(), ad.LogReader)
    assert isinstance(be.HistogramMap(), ad.ObservedMap)
    assert isinstance(be.PollerMapper(ecu), ad.MapperSession)
    assert isinstance(be.DemoTableModel(), ad.TableModel)


def test_poller_source_specs_cover_the_32_slots():
    ecu = be.PollerSource()
    ids = [s.id for s in ecu.specs()]
    assert len(ids) == 24 and {"fan", "fuel_pump"} <= set(ids)
    by = {s.id: s for s in ecu.specs()}
    assert not by["fan"].mapped and by["rpm"].mapped and not by["rpm"].verified
    assert by["alt_fr"].label == "Alt load" and by["ign"].units == "°"
    assert by["ect"].warn_hi == 100 and by["batt"].alarm_lo == 11.5 and by["stft"].signed
    assert len(be.BLACKBOX_SPECS) == 8


def test_simulator_reaches_the_bus_and_recording_works(tmp_path):
    s = build_session(log_dir=tmp_path)
    s.start()
    try:
        s.connect("sim:")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and s.bus.channel("rpm").gen < 5:
            s.pump(s.bus.now())
            time.sleep(0.02)
        rpm = s.bus.channel("rpm")
        assert rpm.gen >= 5 and rpm.value > 500 and not rpm.stale(s.bus.now())
        assert s.ecu_status.state == ad.OK and s.ecu_status.simulated
        assert s.recording.active, "record-on-connect"
        assert s.bus.channel("fan").stale(s.bus.now()), "placeholder never gets data"
        time.sleep(0.3)
        s.pump(s.bus.now() + 1)
        assert s.bb_status.state == ad.OK and s.bus.channel("tach").value > 10
        assert s.mark() == "Marker 1 dropped" and s.recording.marks == 1
        time.sleep(0.4)                     # the logger tags the next sample's row
        assert s.stall.state in ("armed", "standby")
        path = s.recording.path
    finally:
        s.close()
    view = s.logs.read(path)
    assert "rpm" in view.data and view.trigger_t is None and view.markers[0][1] == "MARK 1"
    assert s.logs.recent()[0] == path


def _bus(*specs):
    bus = ChannelBus()
    for spec in specs:
        bus.register(spec)
    return bus


def test_alarm_latches_2s_and_ack_stops_the_blink():
    bus = _bus(ChannelSpec("ect", "Coolant", "°C", 0, -20, 130, warn_hi=100, alarm_hi=108))
    alarms = be.LimitAlarms()

    def at(t, value):
        bus.publish({"ect": value}, t=t)
        return alarms.evaluate(bus, t)
    assert at(1.0, 90) == [] and alarms.state("ect") == ""
    assert at(2.0, 103) == [] and alarms.state("ect") == "warn"
    started = at(3.0, 110)
    assert [e.severity for e in started] == ["alarm"] and started[0].limit_text == "above 108 °C"
    assert alarms.unacked("ect")
    alarms.acknowledge()
    assert alarms.state("ect") == "alarm" and not alarms.unacked("ect")
    at(4.0, 95)
    assert alarms.state("ect") == "alarm", "latched: back in range for less than 2 s"
    at(5.5, 95)
    assert alarms.state("ect") == "alarm"
    at(6.1, 95)
    assert alarms.state("ect") == "" and alarms.history()[0].severity == "alarm"
    assert alarms.limits()[0] == ad.LimitRow("Coolant", ">100", ">108")


def test_stale_channel_is_not_in_alarm():
    bus = _bus(ChannelSpec("batt", "Battery", "V", 1, 8, 16, alarm_lo=11.5))
    alarms = be.LimitAlarms()
    bus.publish({"batt": 10.0}, t=1.0)
    alarms.evaluate(bus, 1.0)
    assert alarms.state("batt") == "alarm"
    alarms.evaluate(bus, 10.0)
    alarms.evaluate(bus, 12.5)
    assert alarms.state("batt") == ""


def test_order_of_loss_is_computed_from_the_data():
    view = be.demo_stall_view()
    f = be.order_of_loss(view)
    first = f.loss[0]
    assert first.channel_id == "fpv" and -2.2 <= first.t <= -1.9 and "fell below" in first.what
    assert [r.t for r in f.loss] == sorted(r.t for r in f.loss)
    assert f.loss[-1].signal == "STALL" and f.loss[-1].t == 0
    ids = [r.channel_id for r in f.loss]
    assert "closed_loop" in ids and "ecub" not in ids and "ig1" not in ids
    assert "ECU +B" in f.held and "IG1" in f.held
    assert f.summary.startswith("FP FEED fell first")
    # a channel that never moves is not a loss; a file without a trigger has none
    view.trigger_t = None
    assert be.order_of_loss(view).loss == []


def test_wizard_ranking_finds_the_byte_that_follows_the_presses():
    rng = np.random.default_rng(1)
    t0, phase = 100.0, 3.0
    trace = []
    for k in range(20):
        t = t0 + k * 0.9
        press = int((t - t0) / phase) % 2 == 0
        mem = np.zeros(256, dtype=np.int16)
        mem[0x14] = 20 + (70 if press else 0) + rng.integers(0, 3)
        mem[0x5A] = 25 + (40 if press else 0) + rng.integers(0, 25)
        mem[0x30] = k * 3                                   # a counter
        mem[0x80] = rng.integers(0, 255)                    # noise, seen in the baseline
        trace.append((t, mem))
    noise = np.zeros(256, dtype=bool)
    noise[0x80] = True
    cands = be.rank_candidates(trace, t0, phase, noise)
    assert cands[0].addr == 0x14 and cands[0].score > 0.95
    assert cands[1].addr == 0x5A and (cands[0].lo, cands[0].hi) == (20, 92)
    assert 0x80 not in [c.addr for c in cands]
    assert be.rank_candidates(trace[:2], t0, phase, noise) == []


def test_demo_mapper_sweep_and_change_tracking():
    s = build_session(demo=True, demo_clock=lambda: 50.0)
    s.start()
    m = s.mapper
    try:
        assert m.available() and m.mapped(0x14) == ("tps", True) and m.mapped(0x99) is None
        m.fill(10.0)
        m.fill(12.0)
        assert m.sweeps == 2 and m.fresh[0x14] and m.changes[0x14] == 1 and m.cur[0x14] >= 0
        assert len(m.history(0x14)) == 2
        m.clear_changes()
        assert m.changes.sum() == 0 and m.sweeps == 0
        w = m.wizard()
        assert w.step == 0 and len(w.steps) == 5 and w.targets
    finally:
        s.close()


def test_histogram_map_accumulates_means():
    bus = _bus(*(ChannelSpec(c) for c in ("rpm", "map", "inj", "ign", "stft", "ltft")))
    obs = be.HistogramMap()
    for inj in (2.0, 4.0):
        bus.publish({"rpm": 3000, "map": 55, "inj": inj, "ign": 20, "stft": 3, "ltft": 1})
        obs.update(bus)
        obs.update(bus)                                     # same sample twice: counted once
    i, j = obs.cell(3000, 55)
    assert obs.hits()[j, i] == 2 and obs.hits().sum() == 2 and obs.revision == 2
    assert obs.mean("inj")[j, i] == pytest.approx(3.0) and obs.mean("trim")[j, i] == pytest.approx(4.0)
    assert np.isnan(obs.mean("inj")[0, 0])


def test_table_model_edit_undo_and_read_only():
    ro = be.DemoTableModel()
    before = ro.values("fl").copy()
    ro.adjust("fl", [(0, 0)], 1)
    assert not ro.writable and ro.read_only_reason and (ro.values("fl") == before).all()

    tm = be.DemoTableModel(writable=True)
    assert tm.values("fl").shape == (20, 10) == (len(tm.rpm_axis), len(tm.map_axis))
    tm.adjust("fl", [(3, 4), (3, 5)], 2)
    assert tm.values("fl")[3, 4] == pytest.approx(before[3, 4] + 0.10) and tm.revision == 1
    tm.adjust("il", [(0, 0)], -1)
    assert tm.values("il")[0, 0] == pytest.approx(tm.base("il")[0, 0] - 0.5)
    tm.values("fl")[5, 5] = 99.0
    tm.interpolate("fl", [(r, c) for r in (4, 5, 6) for c in (4, 5, 6)])
    assert tm.values("fl")[5, 5] < 20
    assert tm.undo() and tm.undo() and tm.undo() and not tm.undo()
    assert tm.values("fl")[3, 4] == pytest.approx(before[3, 4])
