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
    assert up and up[0].t > 6                                       # only after Honda stayed silent
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
    clock, model, poller = rig(honda_protocol=False, stall_after_s=14.0, restart_after_s=None)
    poller.start()
    try:
        run_until(clock, 22)
    finally:
        poller.stop()
    samples = list(poller.samples)
    assert any(s.link and s.t < 14 and s.values["rpm"] > 500 for s in samples)
    after = [s for s in samples if s.t > 17]
    assert after and all(s.link for s in after)                     # the ECU kept answering
    assert after[-1].values["rpm"] == 0


def test_key_off_drops_the_session_and_key_on_brings_it_back():
    clock, model, poller = rig(honda_protocol=False, stall_after_s=14.0, stall_mode="power",
                               restart_after_s=6.0)
    errors = []
    poller.on_sample = lambda s: errors.append(poller.last_error)
    poller.start()
    try:
        run_until(clock, 45)
    finally:
        poller.stop()
    samples = list(poller.samples)
    before = [s for s in samples if s.link and s.t < 14]
    down = [s for s in samples if not s.link and 14 < s.t < 20]
    back = [s for s in samples if s.link and s.t > 20]
    assert before and down and back                                 # up, lost, up again
    assert any("OBD2 session lost" in e for e in errors)
    assert back[-1].values["rpm"] > 0 and poller.mode == OBD2
    assert model.iso_inits >= 2


def test_jobs_in_obd2_mode():
    clock, model, poller = rig(honda_protocol=False, dtcs=("P0135",))
    poller.start()
    try:
        run_until(clock, 14)
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
    model = EcuModel(clock=clock.now, honda_protocol=False, rev_every_s=0, stall_after_s=16.0,
                     restart_after_s=None)
    session = LiveSession(amap, out_dir=tmp_path, sync_captures=True, fsync=False, pre_s=3, post_s=2)
    poller = Poller(LoopbackTransport(model, clock, byte_timing=True), amap, clock=clock)
    session.attach(poller)
    session.start_recording()
    poller.start()
    try:
        run_until(clock, 24)
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
    assert event.rule == "stall" and 16.0 <= event.t < 19.0
