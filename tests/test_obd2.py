"""ISO 9141-2: init, PIDs, DTCs, freeze frame, clear, protocol switching.
All against the simulated ECU on virtual time."""

import time

import pytest

from hondalog import addressmap, obd2, protocol
from hondalog.diag import dtc
from hondalog.link import VirtualClock
from hondalog.obd2 import (ConfirmationRequired, InitError, Iso9141Error, Iso9141Link,
                           NoResponse, Obd2Session)
from hondalog.poller import HONDA, OBD2, OK, Poller
from hondalog.sim import EcuModel, Faults, dtc_to_bytes
from hondalog.transport import Link, LoopbackTransport, Transport


def rig(**kw):
    clock = VirtualClock()
    model = EcuModel(clock=clock.now, rev_every_s=0, warmup_s=1, **kw)
    transport = LoopbackTransport(model, clock, byte_timing=True)
    return clock, model, transport, Iso9141Link(transport, clock)


class Recorder(Transport):
    """Wraps a transport and notes break edges and baud changes."""

    def __init__(self, inner, clock):
        self.inner, self.clock = inner, clock
        self.events = []
        self.usb_latency_s = inner.usb_latency_s

    def set_break(self, on):
        self.events.append((self.clock.now(), "break", on))
        self.inner.set_break(on)

    def set_baud(self, baud):
        self.events.append((self.clock.now(), "baud", baud))
        self.inner.set_baud(baud)

    def write(self, data):
        self.events.append((self.clock.now(), "tx", bytes(data)))
        self.inner.write(data)

    def read_some(self, size, timeout):
        return self.inner.read_some(size, timeout)

    def flush_input(self):
        self.inner.flush_input()


# ---- init ----

def test_5_baud_init_waveform_and_handshake():
    clock, model, transport, _ = rig()
    rec = Recorder(transport, clock)
    link = Iso9141Link(rec, clock)
    result = link.slow_init()
    assert result.method == "5-baud" and result.protocol == "ISO 9141-2"
    assert result.key_bytes == (0x08, 0x08) and result.w1_s == pytest.approx(0.1, abs=0.005)
    edges = [(t, on) for t, kind, on in rec.events if kind == "break"]
    t0 = next(t for t, on in edges if on)
    # 0x33 LSB first is 1 1 0 0 1 1 0 0: low for the start bit, bits 2-3 and bits 6-7
    lows = [round(t - t0, 6) for t, on in edges if on]
    highs = [round(t - t0, 6) for t, on in edges if not on and t >= t0]
    assert lows == [0.0, 0.6, 1.4] and highs == [0.2, 1.0, 1.8]
    assert rec.events[1][1:] == ("baud", 10400)
    assert t0 - rec.events[1][0] >= 0.3                       # W5: idle before the address
    tx = [(t, d) for t, kind, d in rec.events if kind == "tx"]
    assert tx[0][1] == b"\xf7"                                # inverted key byte 2
    sync_t = t0 + 2.0 + 0.1
    kb2_t = sync_t + 3 * 10 / 10400 + 0.02
    assert 0.025 <= tx[0][0] - kb2_t <= 0.050                 # W4
    assert model.iso_inits == 1 and link.connected


def test_init_fails_cleanly_when_ecu_is_off_and_fast_init_is_tried():
    clock, model, transport, link = rig()
    model.power_cut()
    model.restart_after_s = None
    with pytest.raises(InitError, match="5-baud init.*fast init"):
        link.connect()
    assert not link.connected
    with pytest.raises(Iso9141Error):
        link.request(b"\x01\x00")


def test_fast_init_fallback_when_the_ecu_only_does_kwp():
    clock, model, transport, link = rig(iso_fast_init=True)
    model.init_tolerance_s = -1.0                             # make every 5-baud init fail
    result = link.connect()
    assert result.method == "fast" and "KWP2000" in result.protocol and link.kwp
    session = Obd2Session(link)
    assert session.read_pid(0x0C).value == pytest.approx(model.rpm, abs=30)
    assert 0x0C in session.supported_pids()


def test_fast_init_does_not_work_on_a_9141_only_ecu():
    clock, model, transport, link = rig()                     # like the real car
    with pytest.raises(InitError, match="StartCommunication"):
        link.fast_init()


def test_sloppy_bit_timing_is_rejected_by_the_sim():
    clock, model, transport, _ = rig()
    clock.sleep(1.0)
    for start, end in ((0.0, 0.23), (0.6, 1.0), (1.4, 1.8)):  # start bit 30 ms too long
        clock.advance_to(1.0 + start)
        transport.set_break(True)
        clock.advance_to(1.0 + end)
        transport.set_break(False)
    assert model.iso_inits == 0


# ---- messages ----

def test_split_9141_messages_without_length_bytes():
    def msg(payload):
        body = b"\x48\x6b\x10" + payload
        return body + bytes([sum(body) & 0xFF])
    a, b, c = msg(b"\x41\x0c\x1a\xf8"), msg(b"\x43\x01\x35\x04\x20\x00\x00"), msg(b"\x44")
    assert obd2.split_9141(a + b + c) == [b"\x41\x0c\x1a\xf8", b"\x43\x01\x35\x04\x20\x00\x00", b"\x44"]
    assert obd2.split_9141(a[:-1] + b"\x00" + b) == [b"\x43\x01\x35\x04\x20\x00\x00"]   # bad sum skipped
    assert obd2.split_9141(b"") == [] and obd2.split_9141(b"\x48\x6b") == []
    # data that looks like a header inside a message
    tricky = msg(b"\x41\x00\x48\x6b\x10\x41")
    assert obd2.split_9141(tricky + a) == [b"\x41\x00\x48\x6b\x10\x41", b"\x41\x0c\x1a\xf8"]


def test_request_timing_p3min_and_reply_parsing():
    clock, model, transport, link = rig()
    rec = Recorder(transport, clock)
    link = Iso9141Link(rec, clock)
    link.connect()
    session = Obd2Session(link)
    for _ in range(5):
        session.read_pid(0x0C)
    assert model.iso_requests == 5                            # none ignored as "too soon"
    tx = [t for t, kind, d in rec.events if kind == "tx" and d[:3] == b"\x68\x6a\xf1"]
    gaps = [b - a for a, b in zip(tx, tx[1:])]
    # request 6 bytes + P2 30 ms + reply 7 bytes + the quiet wait that doubles as P3min
    assert all(0.09 < g < 0.13 for g in gaps)


def test_session_reinitialises_after_five_idle_seconds_and_keepalive_prevents_it():
    clock, model, transport, link = rig()
    link.connect()
    session = Obd2Session(link)
    session.read_pid(0x05)
    for _ in range(4):
        clock.sleep(3.0)
        assert not link.keepalive()                           # only 3 s idle: nothing sent
        clock.sleep(1.2)
        assert link.keepalive()                               # 4.2 s idle: PID 00 sent
    assert model.iso_inits == 1 and link.reinits == 0
    clock.sleep(6.0)                                          # the ECU has dropped us
    assert not link.connected
    assert session.read_pid(0x05).value == pytest.approx(model.ect, abs=1)
    assert model.iso_inits == 2 and link.reinits == 1


# ---- services ----

def test_supported_pid_discovery_and_live_values():
    clock, model, transport, link = rig()
    link.connect()
    session = Obd2Session(link)
    pids = session.supported_pids()
    assert pids == [0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,
                    0x11, 0x13, 0x14, 0x15, 0x1C]
    before = model.iso_requests
    assert session.supported_pids() == pids and model.iso_requests == before   # cached
    values = session.read_pids()
    assert set(values) == set(pids) - {0x01}
    assert values[0x0C].value == pytest.approx(model.rpm, abs=30) and values[0x0C].units == "rpm"
    assert values[0x05].value == pytest.approx(model.ect, abs=1)
    assert values[0x0B].value == pytest.approx(model.live.map_kpa, abs=1)
    assert values[0x11].value == pytest.approx(model.tps, abs=1)
    assert values[0x0E].value == pytest.approx(model.live.ign, abs=0.5)
    assert values[0x03].text.startswith(("closed loop", "open loop"))
    assert values[0x13].text == "B1S1 B1S2" and values[0x1C].text == "OBD-II (CARB)"
    assert "rpm" in str(values[0x0C])
    with pytest.raises(NoResponse):
        session.read_pid(0x10)                                # no MAF on this engine


def test_dtcs_freeze_frame_and_readiness():
    codes = ("P0135", "P1259", "P0505", "P1456")              # 4 codes: two reply messages
    clock, model, transport, link = rig(dtcs=codes, pending_dtcs=("P0420",))
    link.connect()
    session = Obd2Session(link)
    stored = session.read_dtcs()
    assert [d.code for d in stored] == list(codes)
    assert "heater" in stored[0].description and "VTEC" in stored[1].description
    assert [d.code for d in session.read_pending_dtcs()] == ["P0420"]
    assert session.read_pending_dtcs()[0].status == "pending"
    ready = session.readiness()
    assert ready.mil_on and ready.dtc_count == 4 and ready.ready
    assert set(ready.monitors) == {"misfire", "fuel system", "comprehensive components",
                                   "catalyst", "evaporative system", "O2 sensor", "O2 sensor heater"}
    ff = session.read_freeze_frame()
    assert ff.dtc.code == "P0135"
    assert ff.values[0x0C].value == 812 and ff.values[0x05].value == 84
    assert ff.values[0x06].value == pytest.approx(1.6, abs=0.5)
    snap = session.snapshot()
    text = snap.to_text()
    assert "P1259" in text and "freeze frame for P0135" in text and "Engine speed" in text
    assert not snap.errors


def test_no_codes_and_no_freeze_frame():
    clock, model, transport, link = rig()
    link.connect()
    session = Obd2Session(link)
    assert session.read_dtcs() == [] and session.read_pending_dtcs() == []
    assert session.read_freeze_frame() is None
    ready = session.readiness()
    assert not ready.mil_on and ready.dtc_count == 0 and "all monitors ready" in str(ready)


def test_clear_needs_confirmation_and_a_stopped_engine():
    clock, model, transport, link = rig(dtcs=("P0135",))
    link.connect()
    session = Obd2Session(link)
    for bad in ("", "yes", "clear"):
        with pytest.raises(ConfirmationRequired, match="readiness"):
            session.clear_dtcs(bad)
    with pytest.raises(Iso9141Error, match="engine is running"):
        session.clear_dtcs(Obd2Session.CLEAR_CONFIRM)
    assert model.dtcs == ["P0135"]                            # nothing was sent
    model.stall()                                             # key on, engine off
    clock.sleep(1.0)
    session.read_pid(0x0C)
    clock.sleep(1.0)
    assert session.clear_dtcs(Obd2Session.CLEAR_CONFIRM) is True
    assert model.dtcs == [] and session.read_dtcs() == []
    ready = session.readiness()
    assert not ready.mil_on and not ready.ready
    assert "catalyst" in ready.incomplete                     # monitors reset, as warned
    assert session.read_freeze_frame() is None
    assert "readiness" in Obd2Session.CLEAR_WARNING


def test_obd2_survives_line_faults():
    clock, model, transport, link = rig(dtcs=("P0135", "P0171"))
    link.connect()
    model.faults = Faults(bad_checksum=0.15, drop_byte=0.1, no_reply=0.05, seed=2)
    session = Obd2Session(link)
    ok = 0
    for _ in range(60):
        try:
            v = session.read_pid(0x05)
        except NoResponse:
            continue
        assert v.value == pytest.approx(model.ect, abs=1)     # never a wrong value
        ok += 1
    assert ok > 45


def test_decoders():
    assert obd2.decode_dtc(*dtc_to_bytes("P0135")) == "P0135"
    assert obd2.decode_dtc(*dtc_to_bytes("P1456")) == "P1456"
    assert obd2.decode_dtc(0xC1, 0x00) == "U0100" and obd2.decode_dtc(0x41, 0x23) == "C0123"
    assert obd2.decode_pid(0x0C, b"\x1a\xf8").value == 1726
    assert obd2.decode_pid(0x05, b"\x7b").value == 83
    assert obd2.decode_pid(0x06, b"\x80").value == 0 and obd2.decode_pid(0x07, b"\x90").value == 12.5
    assert obd2.decode_pid(0x0E, b"\xa0").value == 16
    assert obd2.decode_pid(0x14, b"\x5a\x80").value == 0.45
    assert obd2.decode_pid(0x77, b"\x01").value is None
    assert obd2.decode_pid(0x0C, b"\x1a").text == "short reply"
    r = obd2.decode_readiness(bytes([0x82, 0x07, 0x65, 0x04]))
    assert r.mil_on and r.dtc_count == 2 and r.incomplete == ["evaporative system"]
    assert obd2.checksum(bytes.fromhex("686af1010c")) == 0xD0


def test_dtc_table():
    assert "VTEC" in dtc.describe("P1259") and "ELD" in dtc.describe("p1298 ")
    assert "Idle" in dtc.describe("P0505") and "misfire" in dtc.describe("P0301")
    assert "Honda-specific" in dtc.describe("P1999") and "generic" in dtc.describe("P0999")
    assert dtc.describe("XYZ") == "(not a valid OBD2 code)"
    assert dtc.is_stall_related("P1381") and not dtc.is_stall_related("P0420")
    for code in list(dtc.HONDA) + list(dtc.GENERIC):
        assert len(code) == 5 and code[0] == "P" and obd2.decode_dtc(*dtc_to_bytes(code)) == code
    assert all(c[1] == "1" for c in dtc.HONDA) and all(c[1] == "0" for c in dtc.GENERIC)


# ---- sharing the line with the Honda protocol ----

def test_protocols_exclude_each_other_and_switch_back_without_power_cycle():
    clock, model, transport, iso = rig()
    honda = Link(transport, protocol.DEFAULT.with_overrides({"retries": 0}), clock)
    assert honda.read_mem(0x76, 3) == b"SIM"
    iso.connect()
    assert Obd2Session(iso).read_pid(0x0C).value > 0
    iso.close()                                               # back to 9600 baud
    assert transport.baud == 9600
    with pytest.raises(protocol.NoReply):
        honda.read_mem(0x76, 3)                               # ECU still holds the ISO session
    clock.sleep(5.1)                                          # P3max: it lets go
    assert honda.read_mem(0x76, 3) == b"SIM"
    # and the other way: Honda traffic, then straight into another ISO init
    iso.connect()
    assert Obd2Session(iso).read_pid(0x05).value == pytest.approx(model.ect, abs=1)
    assert model.iso_inits == 2


def test_wrong_baud_is_garbage_to_the_ecu():
    clock, model, transport, iso = rig()
    transport.set_baud(10400)
    transport.write(protocol.build_read_request(0, 2))        # Honda frame at the ISO speed
    assert transport.read(64, 0.3) == protocol.build_read_request(0, 2)   # echo only
    transport.set_baud(9600)
    transport.write(bytes.fromhex("686af1010cd0"))            # ISO frame, no session
    assert len(transport.read(64, 0.3)) == 6


def _run_until(clock, t, wall=20.0):
    end = time.monotonic() + wall
    while clock.now() < t and time.monotonic() < end:
        time.sleep(0.002)
    assert clock.now() >= t


def test_poller_obd2_job_pauses_polling_and_resumes_by_itself():
    clock = VirtualClock()
    model = EcuModel(clock=clock.now, rev_every_s=0, dtcs=("P0135",))
    poller = Poller(LoopbackTransport(model, clock, byte_timing=True),
                    addressmap.load_default(), clock=clock)
    modes = []
    poller.start()
    try:
        _run_until(clock, 2)
        assert poller.state == OK and poller.mode == HONDA

        def job(session):
            modes.append(poller.mode)
            return session.snapshot(live=False)

        snap = poller.obd2(job).result(20)
        t_done = clock.now()
        assert modes == [OBD2] and [d.code for d in snap.stored] == ["P0135"]
        assert snap.readiness.mil_on and snap.freeze_frame.dtc.code == "P0135"
        _run_until(clock, t_done + 9)
        assert poller.state == OK and poller.mode == HONDA
    finally:
        poller.stop()
    samples = list(poller.samples)
    assert all(s.link for s in samples)                       # the pause is not a "link lost"
    gaps = [(a.t, b.t - a.t) for a, b in zip(samples, samples[1:]) if b.t - a.t > 1]
    assert len(gaps) == 1 and 3 < gaps[0][1] < 12             # init + session + ISO timeout
    assert poller.channel_rates()["rpm"].achieved_hz > 15     # full speed again afterwards


def test_poller_obd2_job_reports_init_failure_and_carries_on():
    clock = VirtualClock()
    model = EcuModel(clock=clock.now, rev_every_s=0, iso=False)
    poller = Poller(LoopbackTransport(model, clock, byte_timing=True),
                    addressmap.load_default(), clock=clock)
    poller.start()
    try:
        _run_until(clock, 1)
        fut = poller.obd2(lambda session: session.read_dtcs())
        with pytest.raises(InitError):
            fut.result(20)
        t = clock.now()
        _run_until(clock, t + 3)
        assert poller.state == OK and all(s.link for s in poller.samples)
    finally:
        poller.stop()
