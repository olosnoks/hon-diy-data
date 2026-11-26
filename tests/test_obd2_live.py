"""OBD2-only live mode: the poller falls back to ISO 9141-2 PIDs when the ECU
answers standard OBD2 but not Honda reads. All on the simulated ECU, on
virtual time, at true byte timing."""

import time

import numpy as np
import pytest

from hondalog import addressmap
from hondalog.data.reader import open_log
from hondalog.link import VirtualClock
from hondalog.live import LiveSession
from hondalog.obd2 import PID_CHANNEL
from hondalog.poller import HONDA, OBD2, OK, Poller
from hondalog.protocol import ProtocolError
from hondalog.sim import EcuModel
from hondalog.transport import LoopbackTransport, make_transport


def run_until(clock, t, wall=30.0):
    end = time.monotonic() + wall
    while clock.now() < t and time.monotonic() < end:
        time.sleep(0.002)
    assert clock.now() >= t, "virtual time did not advance (poller thread stuck?)"


def rig(iso_fallback=True, **model_kw):
    clock = VirtualClock()
    model_kw.setdefault("rev_every_s", 0)
    model = EcuModel(clock=clock.now, **model_kw)
    poller = Poller(LoopbackTransport(model, clock, byte_timing=True), addressmap.load_default(),
                    clock=clock, iso_fallback=iso_fallback)
    return clock, model, poller


def test_iso_only_ecu_gets_live_values_through_obd2_pids():
    clock, model, poller = rig(honda_protocol=False)
    pids = []
    poller.on_pid = lambda t, pid, data: pids.append((t, pid, data))
    poller.start()
    try:
        run_until(clock, 20)
    finally:
        poller.stop()
    assert poller.mode == OBD2 and "5-baud" in poller.iso_link
    up = [s for s in poller.samples if s.link]
    assert up and up[0].t > 12                                       # only after Honda stayed silent
    last = up[-1]
    assert last.values["rpm"] == pytest.approx(model.rpm, abs=80)
    for name in ("map", "tps", "ect", "iat", "stft", "ltft", "o2", "ign", "vss"):
        assert last.values[name] is not None, name
    assert last.values["batt"] is None                              # no PID for it: unknown, not made up
    span = up[-1].t - up[0].t
    assert len(up) / span > 3                                       # a Sample after every RPM pair
    rpm_reads = [p for p in pids if p[1] == 0x0C]
    assert len(rpm_reads) >= len(pids) // 2 - 1                     # RPM is every other request
    assert {PID_CHANNEL[p[1]] for p in pids} >= {"rpm", "map", "tps", "ect"}
    assert all(isinstance(p[2], bytes) and p[2] for p in pids)


def test_honda_ecu_never_switches_to_obd2():
    clock, model, poller = rig()
    poller.start()
    try:
        run_until(clock, 20)
        assert poller.mode == HONDA and poller.state == OK
    finally:
        poller.stop()
    assert poller.iso_link == ""


def test_fallback_can_be_switched_off():
    clock, model, poller = rig(iso_fallback=False, honda_protocol=False)
    poller.start()
    try:
        run_until(clock, 20)
        assert poller.mode == HONDA and poller.state != OK
    finally:
        poller.stop()
    assert not any(s.link for s in poller.samples)


def test_stall_reads_zero_rpm_with_the_link_up():
    clock, model, poller = rig(honda_protocol=False, stall_after_s=22.0, restart_after_s=None)
    poller.start()
    try:
        run_until(clock, 30)
    finally:
        poller.stop()
    samples = list(poller.samples)
    assert any(s.link and s.t < 22 and s.values["rpm"] > 500 for s in samples)
    after = [s for s in samples if s.t > 25]
    assert after and all(s.link for s in after)                     # the ECU kept answering
    assert after[-1].values["rpm"] == 0


def test_key_off_drops_the_session_and_key_on_brings_it_back():
    clock, model, poller = rig(honda_protocol=False, stall_after_s=22.0, stall_mode="power",
                               restart_after_s=6.0)
    errors = []
    poller.on_sample = lambda s: errors.append(poller.last_error)
    poller.start()
    try:
        run_until(clock, 55)
    finally:
        poller.stop()
    samples = list(poller.samples)
    before = [s for s in samples if s.link and s.t < 22]
    down = [s for s in samples if not s.link and 22 < s.t < 28]
    back = [s for s in samples if s.link and s.t > 28]
    assert before and down and back                                 # up, lost, up again
    assert any("OBD2 session lost" in e for e in errors)
    assert back[-1].values["rpm"] > 0 and poller.mode == OBD2
    assert model.iso_inits >= 2


def test_jobs_in_obd2_mode():
    clock, model, poller = rig(honda_protocol=False, dtcs=("P0135",))
    poller.start()
    try:
        run_until(clock, 20)
        assert poller.mode == OBD2
        inits = model.iso_inits
        codes = poller.obd2(lambda session: [d.code for d in session.read_dtcs()]).result(20)
        assert codes == ["P0135"] and model.iso_inits == inits     # the running session, no new init
        with pytest.raises(ProtocolError, match="OBD2 only"):
            poller.submit(lambda link: link.read_block(0, 1)).result(20)
        run_until(clock, clock.now() + 2)
        assert poller.mode == OBD2 and poller.state == OK
    finally:
        poller.stop()


def test_sim_iso_port():
    assert make_transport("sim:iso").model.honda_protocol is False
    assert make_transport("sim:").model.honda_protocol is True


def test_recording_and_stall_capture_in_obd2_mode(tmp_path):
    clock = VirtualClock()
    amap = addressmap.load_default()
    model = EcuModel(clock=clock.now, honda_protocol=False, rev_every_s=0, stall_after_s=22.0,
                     restart_after_s=None)
    session = LiveSession(amap, out_dir=tmp_path, sync_captures=True, fsync=False, pre_s=3, post_s=2)
    poller = Poller(LoopbackTransport(model, clock, byte_timing=True), amap, clock=clock)
    session.attach(poller)
    session.start_recording()
    poller.start()
    try:
        run_until(clock, 30)
    finally:
        recorder = session.recorder
        session.close()
    assert recorder.write_error is None
    with open_log(recorder.path) as log:
        assert {"obd2/0C", "obd2/0B", "obd2/11", "ecu/link"} <= set(log.streams)
        rpm_stream = log.streams["obd2/0C"]
        assert rpm_stream.info.source == "obd2" and rpm_stream.info.raw_len == 2
        rpm = log.series("rpm")
        assert rpm.rate_hz > 3 and np.nanmax(rpm.v) > 500 and rpm.v[-1] == 0
        assert log.series("ect").units == "°C"
    event = recorder.events.get_nowait()
    assert event.rule == "stall" and 22.0 <= event.t < 25.0


def test_honda_ecu_keyed_off_is_probed_not_switched_to_obd2():
    clock, model, poller = rig(stall_after_s=5.0, stall_mode="power", restart_after_s=12.0)
    poller.start()
    try:
        run_until(clock, 30)
    finally:
        poller.stop()
    samples = list(poller.samples)
    assert any(not s.link for s in samples) and samples[-1].link      # lost at key off, back at key on
    assert model.iso_inits == 0 and poller.mode == HONDA


# ---- flags, load and the downstream O2 from standard PIDs ----

REAL_CAR_PIDS = [0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x11,
                 0x13, 0x14, 0x15, 0x1C]


def test_channel_value_turns_status_bytes_into_flags():
    from hondalog.obd2 import channel_value
    # PID 03 fuel system 1 status: closed loop, and closed loop with an O2 fault
    for status, flag in ((0x01, 0), (0x02, 1), (0x04, 0), (0x08, 0), (0x10, 1), (0x00, 0)):
        assert channel_value(0x03, bytes([status, 0])) == flag, hex(status)
    # PID 01 byte A bit 7 is the MIL; the low bits are the DTC count
    assert channel_value(0x01, bytes([0x81, 7, 0x65, 0])) == 1
    assert channel_value(0x01, bytes([0x7F, 7, 0x65, 0])) == 0
    assert channel_value(0x04, b"\xff") == 100.0
    assert channel_value(0x15, bytes([120, 0xFF])) == pytest.approx(0.6)
    assert channel_value(0x03, b"") is None                          # short reply: unknown


def test_plan_on_the_real_cars_pids_weights_flags_slow():
    _clock, _model, poller = rig(honda_protocol=False)
    plan = poller._plan_iso(REAL_CAR_PIDS)
    count = {}
    for pid, _name, _emit in plan:
        count[pid] = count.get(pid, 0) + 1
    assert set(count) == {0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,
                          0x11, 0x14, 0x15}                          # 13 and 1C fill no channel
    assert count[0x01] == count[0x03] == 1                           # MIL and loop state: slow
    assert count[0x04] == count[0x15] == 2                           # load and O2 post: medium
    assert count[0x0B] == 3                                          # MAP: fast


def test_obd2_mode_fills_mil_closed_loop_load_and_o2_post():
    clock, model, poller = rig(honda_protocol=False, warmup_s=5.0, dtcs=("P0135",))
    session = LiveSession(poller.amap)
    session.attach(poller)
    frames = []
    session.on_frame = frames.append
    poller.start()
    try:
        run_until(clock, 40)
    finally:
        poller.stop()
    assert poller.mode == OBD2
    last = [s for s in poller.samples if s.link][-1]
    assert last.values["mil"] == 1                                   # a stored code lights it
    assert last.values["closed_loop"] == (1 if model.live.closed_loop else 0)
    assert any(s.values.get("closed_loop") == 1 for s in poller.samples if s.link)
    assert last.values["o2_post"] == pytest.approx(0.6)
    assert last.values["calc_load"] is not None
    frame = [f for f in frames if f.link][-1]
    assert frame.values["load"] == pytest.approx(frame.values["calc_load"])   # PID 04, not map/baro
    for name in ("batt", "iacv", "knock", "baro", "eld", "alt_fr", "ign_limit", "vtec", "ac_sw",
                 "inj"):
        assert last.values[name] is None, name                       # no PID: stays blank
    for name in ("inj_duty", "fuel_flow", "afr_est", "batt_min_5s"):
        assert frame.values[name] is None, name                      # built on blank inputs


def test_no_mil_and_open_loop_read_as_zero():
    clock, model, poller = rig(honda_protocol=False)                 # cold, no codes
    poller.start()
    try:
        run_until(clock, 20)
    finally:
        poller.stop()
    last = [s for s in poller.samples if s.link][-1]
    assert last.values["mil"] == 0 and last.values["closed_loop"] == 0


def test_honda_mode_keeps_load_from_map_and_baro():
    amap = addressmap.load_default()
    assert "o2_post" not in amap.names and amap.obd2_channel("o2_post").pid == 0x15
    out = amap.math_engine().update({"map": 50.0, "baro": 100.0}, 0.0)
    assert out["load"] == pytest.approx(50.0)
    out = amap.math_engine().update({"map": 50.0, "baro": 100.0, "calc_load": 21.0}, 0.0)
    assert out["load"] == pytest.approx(21.0)
    clock, model, poller = rig()
    poller.start()
    try:
        run_until(clock, 10)
    finally:
        poller.stop()
    assert poller.mode == HONDA
    assert all("o2_post" not in s.values for s in poller.samples)   # never read over Honda


def test_flag_and_o2_post_streams_are_recorded_with_raw_bytes(tmp_path):
    clock = VirtualClock()
    amap = addressmap.load_default()
    model = EcuModel(clock=clock.now, honda_protocol=False, rev_every_s=0, warmup_s=5.0,
                     dtcs=("P0135",))
    session = LiveSession(amap, out_dir=tmp_path, sync_captures=True, fsync=False)
    poller = Poller(LoopbackTransport(model, clock, byte_timing=True), amap, clock=clock)
    session.attach(poller)
    session.start_recording()
    poller.start()
    try:
        run_until(clock, 40)
    finally:
        recorder = session.recorder
        session.close()
    assert recorder.write_error is None
    with open_log(recorder.path) as log:
        assert {"obd2/01", "obd2/03", "obd2/04", "obd2/15"} <= set(log.streams)
        assert log.streams["obd2/03"].info.raw_len == 2 and log.streams["obd2/01"].info.raw_len == 4
        assert set(np.unique(log.series("closed_loop").v)) <= {0.0, 1.0}
        assert np.nanmax(log.series("closed_loop").v) == 1
        assert np.all(log.series("mil").v == 1)
        assert log.series("o2_post").v[-1] == pytest.approx(0.6, abs=1e-3)
        assert log.series("o2_post").units == "V"
        assert log.series("calc_load").units == "%"
