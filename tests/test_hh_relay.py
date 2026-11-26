"""Relay: the laptop's own ECU reads to the handheld (SERIAL.md "Relay"). Exact
frame bytes, the gates (capability 64, the map hash, force), the laptop's
poller on the simulated ECU (Honda and OBD2-only) relayed to a fake handheld
that decodes the COBS frames it gets, and a stalled USB link that must never
hold up the poller thread."""

from __future__ import annotations

import struct
import time
from types import SimpleNamespace

import pytest

from hhfake import FakeHandheld
from hondalog import addressmap
from hondalog import poller as pl
from hondalog.handheld import relay as R
from hondalog.handheld import serial_proto as P
from hondalog.link import VirtualClock
from hondalog.live import LiveSession
from hondalog.poller import BlockRead, Poller
from hondalog.sim import EcuModel
from hondalog.transport import LoopbackTransport

RELAY_CAPS = P.CAP_RX | P.CAP_LOG | P.CAP_HL | P.CAP_RELAY


def wait(cond, seconds=8.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture(scope="module")
def amap():
    return addressmap.load_default()


# ---- the bytes ----

def test_relay_frames_are_the_spec_bytes():
    assert (P.SR_RELAY_READ, P.SR_RELAY_ISO, P.SR_RELAY_TICK, P.SR_RELAY_LINK) == (0x50, 0x51, 0x52, 0x53)
    assert P.CAP_RELAY == 64 and P.CAP_HL == 32 and P.LINK_RELAY == 5
    # u64 host_us, u8 addr, u8 len, u16 latency_us + bytes
    assert P.pack_relay_read(0x0102030405060708, 0x10, b"\xAA\xBB\xCC", 1234) == \
        bytes.fromhex("0807060504030201" "10" "03" "d204" "aabbcc")
    # u64 host_us, u8 mode, u8 pid, u8 len, u8 flags + bytes
    assert P.pack_relay_iso(1_000_000, 0x0C, b"\x1A\xF8") == \
        bytes.fromhex("40420f0000000000" "41" "0c" "02" "00" "1af8")
    # u64 host_us, u16 latency_us, u16 hz_c, u32 n
    assert P.pack_relay_tick(5, 2500.4, 19.37, 77) == struct.pack("<QHHI", 5, 2500, 1937, 77)
    assert len(P.pack_relay_tick(5, 1e9, 1e9, 2**40)) == 16          # clamped, never an error
    # u64 host_us, u8 state, u8 up, u8 iso, u8 ecu_id_len + id (at most 10)
    assert P.pack_relay_link(9, P.LINK_ISO_ONLY, True, True, b"0123456789AB") == \
        struct.pack("<QBBBB", 9, 3, 1, 1, 10) + b"0123456789"
    assert P.pack_relay_link(9, P.LINK_OFF, False, False) == struct.pack("<QBBBB", 9, 0, 0, 0, 0)
    # in a frame: COBS(ver type seq payload crc16) 0x00, as the handheld's deframer takes it
    frames = list(P.Deframer().feed(P.frame_encode(P.SR_RELAY_TICK, P.pack_relay_tick(5, 1, 2, 3), 7)))
    assert frames == [(P.SR_RELAY_TICK, 7, struct.pack("<QHHI", 5, 1, 200, 3))]


def test_link_fields():
    def p(state, mode=pl.HONDA):
        return SimpleNamespace(state=state, mode=mode)
    assert R.link_fields(p(pl.OK)) == (P.LINK_OK, True, False)
    assert R.link_fields(p(pl.OK, pl.OBD2)) == (P.LINK_ISO_ONLY, True, True)
    assert R.link_fields(p(pl.CONNECTING)) == (P.LINK_CONNECTING, False, False)
    assert R.link_fields(p(pl.NO_REPLY)) == (P.LINK_OFF, False, False)
    assert R.link_fields(p(pl.PORT_LOST)) == (P.LINK_OFF, False, False)


# ---- the laptop's poller on the simulated ECU ----

def rig(amap, honda=True):
    clock = VirtualClock()
    model = EcuModel(clock=clock.now, rev_every_s=0, honda_protocol=honda)
    poller = Poller(LoopbackTransport(model, clock, byte_timing=True), amap, clock=clock)
    poller.port_name = "sim:" if honda else "sim:iso"
    return clock, poller


def run_until(clock, t, wall=40.0):
    end = time.monotonic() + wall
    while clock.now() < t and time.monotonic() < end:
        time.sleep(0.002)
    assert clock.now() >= t


def relayed(fake, ftype):
    return fake.sent(ftype)


def session_with(amap, fake, honda=True, **opts):
    clock, poller = rig(amap, honda)
    live = LiveSession(amap, **opts)
    live.attach(poller)
    live.open_handheld(fake, exclude=poller.port_name)
    assert wait(lambda: live.handheld.hello is not None)
    return clock, poller, live


def test_honda_reads_are_relayed_byte_for_byte(amap):
    fake = FakeHandheld(caps=RELAY_CAPS, map_hash=amap.version_hash)
    clock, poller, live = session_with(amap, fake)
    reads = []
    live.add_listener(on_block=reads.append)
    poller.start()
    try:
        run_until(clock, 3.0)
        assert wait(lambda: len(relayed(fake, P.SR_RELAY_TICK)) >= 5)
    finally:
        live.close()
    assert fake.bad == 0
    got = []
    for p in relayed(fake, P.SR_RELAY_READ):
        host_us, addr, n, lat = P.RELAY_READ.unpack(p[:12])
        assert len(p) == 12 + n
        got.append((host_us, addr, p[12:], lat))
    assert len(got) >= 10
    want = {(int((r.mono - r.latency_s) * 1e6), r.addr, r.data) for r in reads}
    assert all((h, a, d) in want for h, a, d, _ in got)            # the poller's own bytes and times
    assert [h for h, *_ in got] == sorted(h for h, *_ in got)      # in order
    assert all(0 < lat < 65535 for *_, lat in got)
    ticks = [P.RELAY_TICK.unpack(p) for p in relayed(fake, P.SR_RELAY_TICK)]
    assert [n for *_, n in ticks] == sorted(n for *_, n in ticks)
    assert ticks[-1][2] > 0                                          # hz_c
    links = [P.RELAY_LINK.unpack(p[:12]) for p in relayed(fake, P.SR_RELAY_LINK)]
    assert links and links[-1][1:4] == (P.LINK_OK, 1, 0)
    assert not relayed(fake, P.SR_RELAY_ISO)


def test_obd2_only_replies_are_relayed_as_mode_41(amap):
    fake = FakeHandheld(caps=RELAY_CAPS, map_hash=amap.version_hash)
    clock, poller, live = session_with(amap, fake, honda=False)
    pids = []
    live.add_listener(on_pid=lambda t, pid, data: pids.append((pid, data)))
    poller.start()
    try:
        run_until(clock, 20.0)
        assert wait(lambda: len(relayed(fake, P.SR_RELAY_ISO)) >= 5 and poller.mode == pl.OBD2)
        assert wait(lambda: any(P.RELAY_LINK.unpack(p[:12])[1] == P.LINK_ISO_ONLY
                                for p in relayed(fake, P.SR_RELAY_LINK)))
    finally:
        live.close()
    for p in relayed(fake, P.SR_RELAY_ISO):
        _, mode, pid, n, flags = P.RELAY_ISO.unpack(p[:12])
        assert mode == 0x41 and flags == 0 and (pid, p[12:12 + n]) in pids
    assert not relayed(fake, P.SR_RELAY_READ)
    assert relayed(fake, P.SR_RELAY_TICK)
    link = [P.RELAY_LINK.unpack(p[:12]) for p in relayed(fake, P.SR_RELAY_LINK)][-1]
    assert link[1:4] == (P.LINK_ISO_ONLY, 1, 1)


# ---- the gates ----

def test_firmware_0_1_0_gets_nothing_and_says_why(amap):
    fake = FakeHandheld(map_hash=amap.version_hash)            # caps without 64 (HL's 32 is not relay)
    clock, poller, live = session_with(amap, fake)
    poller.start()
    try:
        run_until(clock, 2.0)
        time.sleep(0.3)
        assert live.relay.status == R.TOO_OLD == "handheld: firmware too old for relay"
        assert "firmware too old for relay" in live.handheld_text()
    finally:
        live.close()
    assert not [t for t, _, _ in fake.frames if P.SR_RELAY_READ <= t <= P.SR_RELAY_LINK]
    assert fake.sent(P.SC_TIME_REQ)                             # the heartbeat still goes


def test_force_relays_to_firmware_0_1_0(amap):
    fake = FakeHandheld(map_hash="000000000000")
    clock, poller, live = session_with(amap, fake, relay_force=True)
    poller.start()
    try:
        run_until(clock, 2.0)
        assert wait(lambda: relayed(fake, P.SR_RELAY_READ))
    finally:
        live.close()


def test_map_differs_is_said_and_the_map_can_be_pushed(amap):
    fake = FakeHandheld(caps=RELAY_CAPS, map_hash="ffffffffffff")
    clock, poller, live = session_with(amap, fake)
    poller.start()
    pushed = []

    class Client:
        def __init__(self, port):
            self.port = port

        def open(self):
            pushed.append("open")

        def push(self, kind, data):
            pushed.append((kind, len(data)))

        def close(self):
            pushed.append("close")
    try:
        run_until(clock, 2.0)
        time.sleep(0.3)
        assert live.relay.status == R.MAP_DIFFERS == "handheld map differs: hondalog-handheld push-map"
        assert not relayed(fake, P.SR_RELAY_READ)
        text = live.push_handheld_map(client_factory=Client)
        assert pushed[0] == "open" and pushed[1][0] == P.F_MAP and pushed[-1] == "close"
        assert "restart the handheld" in text and amap.version_hash in text
        assert live.handheld is not None and live.relay is not None      # opened again
    finally:
        live.close()


def test_tx_only_handheld_is_never_written_to(amap):
    fake = FakeHandheld(caps=P.CAP_LOG, map_hash=amap.version_hash)
    clock, poller, live = session_with(amap, fake, relay_force=True)
    poller.start()
    try:
        run_until(clock, 2.0)
        time.sleep(0.3)
        assert live.relay.status == R.TX_ONLY
    finally:
        live.close()
    assert fake.frames == []


# ---- never block the poller ----

def test_a_stalled_usb_link_never_holds_up_the_poller_thread(amap):
    fake = FakeHandheld(caps=RELAY_CAPS, map_hash=amap.version_hash, write_delay=0.5)
    poller = SimpleNamespace(state=pl.OK, mode=pl.HONDA, t0=0.0, hz=20.0, sweeps=0, ecu_id=b"",
                             clock=SimpleNamespace(now=time.monotonic))
    from hondalog.handheld.companion import HandheldCompanion
    companion = HandheldCompanion(fake, amap)
    companion.start()
    relay = R.Relay(companion, poller, amap, max_queue=256).start()
    try:
        assert wait(lambda: relay.active)
        read = BlockRead(1.0, time.monotonic(), 0x10, bytes(16), 0.01, {})
        t0 = time.perf_counter()
        for _ in range(5000):
            relay.on_block(read)
        spent = time.perf_counter() - t0
        assert spent < 0.5, f"5000 relayed reads took {spent:.3f} s on the poller thread"
        assert relay.dropped >= 5000 - 256 - 5 and len(relay._q) <= 256
        assert relay.sent <= 5                          # the writer is stuck in write()
    finally:
        relay.stop(0.1)
        companion.stop(0.1)


# ---- the session chains, never replaces ----

def test_callbacks_already_on_the_poller_are_kept(amap):
    clock, poller = rig(amap)
    mine = []
    poller.on_block = lambda r: mine.append(r)
    live = LiveSession(amap)
    live.attach(poller)
    extra = []
    remove = live.add_listener(on_block=extra.append)
    poller.start()
    try:
        run_until(clock, 1.0)
        assert wait(lambda: len(mine) >= 3 and len(extra) >= 3)
        remove()
        n = len(extra)
        run_until(clock, 2.0)
        assert wait(lambda: len(mine) > n + 3)
        assert len(extra) <= n + 1
    finally:
        live.close()


# ---- the simulator takes relay frames ----

def test_cydsim_says_relay_and_takes_the_frames(amap, tmp_path):
    live = LiveSession(amap, out_dir=tmp_path, handheld="cyd:sim")
    live.connect("sim:")
    try:
        hh = live.handheld
        assert wait(lambda: hh.hello is not None)
        assert hh.hello.caps & P.CAP_RELAY and live.relay.status == ""
        sim = hh.port.sim
        assert wait(lambda: {t for t, _ in sim.relayed} >= {P.SR_RELAY_READ, P.SR_RELAY_TICK, P.SR_RELAY_LINK})
        assert sim.relay_bad == 0
    finally:
        live.close()
