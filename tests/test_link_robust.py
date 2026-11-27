"""Link timing, resync, retries, fault injection, the read-only guard and
poller recovery. Everything runs on virtual time at true 9600-baud byte
timing, so it is fast and repeats exactly."""

import time

import pytest

from hondalog import addressmap, protocol
from hondalog.link import VirtualClock
from hondalog.link.timing import AdaptiveTiming
from hondalog.poller import NO_REPLY, OK, PORT_LOST, Poller
from hondalog.sim import EcuModel, Faults
from hondalog.transport import (CommandBlocked, Link, LoopbackTransport, Transport,
                                TransportError, make_transport)

BYTE = 10 / 9600


def rig(framing=protocol.DEFAULT, usb_latency_s=0.001, **model_kw):
    """Link mechanics at 9600 baud: the simulated ECU is kerpz's P2T (Honda
    reads at 9600, no wake frame), so the byte arithmetic stays simple."""
    clock = VirtualClock()
    model = EcuModel(framing=framing, clock=clock.now, rev_every_s=0, **model_kw)
    transport = LoopbackTransport(model, clock, byte_timing=True, usb_latency_s=usb_latency_s)
    return clock, model, Link(transport, framing, clock)


# ---- byte timing and timestamps ----

def test_true_byte_timing_of_one_exchange():
    clock, model, link = rig(usb_latency_s=0.0)
    r = link.read_block(0x00, 16)
    # 5 echo + 19 reply bytes at 1.0417 ms each, plus the ECU's 2 ms think time
    assert r.t_done - r.t_request == pytest.approx(24 * BYTE + 0.002, abs=1e-6)
    assert r.turnaround_s == pytest.approx(0.002, abs=1e-6)
    assert r.latency_s == pytest.approx(19 * BYTE)           # age of the data at t_done
    r2 = link.read_block(0x00, 2)
    assert r2.t_request - r.t_done == pytest.approx(0.002)   # the inter-request gap
    assert r2.t_done - r2.t_request == pytest.approx(10 * BYTE + 0.002, abs=1e-6)


def test_bytes_arrive_one_at_a_time_and_echo_first():
    clock, model, link = rig(usb_latency_s=0.0)
    req = protocol.build_read_request(0x76, 4)
    link.transport.write(req)
    seen = []
    while len(seen) < 12:
        chunk = link.transport.read_some(64, 0.1)
        assert len(chunk) == 1
        seen.append((clock.now(), chunk[0]))
    assert bytes(b for _, b in seen[:5]) == req
    assert seen[0][0] == pytest.approx(BYTE) and seen[4][0] == pytest.approx(5 * BYTE)
    assert seen[5][0] == pytest.approx(5 * BYTE + 0.002 + BYTE)
    assert seen[-1][0] == pytest.approx(12 * BYTE + 0.002)


def test_usb_latency_timer_quantises_delivery():
    for latency, slack in ((0.001, 0.001), (0.016, 0.016)):
        clock, model, link = rig(usb_latency_s=latency)
        r = link.read_block(0, 16)
        wire = 24 * BYTE + 0.002
        assert wire - 1e-9 <= r.t_done - r.t_request <= wire + slack + 1e-9
        assert r.latency_s == pytest.approx(19 * BYTE + latency / 2)


# ---- adaptive timing ----

def test_timeouts_adapt_to_what_is_measured():
    clock, model, link = rig()
    assert link.timing.response_timeout == 0.2               # starts at the ceiling
    for _ in range(40):
        link.read_block(0, 16)
    tm = link.timing
    assert tm.overhead_s == pytest.approx(0.0025, abs=0.0006)
    assert tm.response_timeout == pytest.approx(0.02)        # floor
    assert tm.inter_byte_timeout < 0.012
    # a silent ECU now costs ~45 ms per read instead of 225 ms
    model.power_cut()
    t0 = clock.now()
    with pytest.raises(protocol.NoReply):
        link.read_block(0, 16, retries=0)
    assert clock.now() - t0 < 0.06                           # the caller hears at once...
    assert tm.response_timeout == pytest.approx(0.04)        # doubled after the miss
    with pytest.raises(protocol.NoReply):
        link.read_block(0, 16, retries=0)
    # ...but the next request waited out the late-reply watch (reply_timeout_s)
    assert clock.now() - t0 == pytest.approx(0.2 + 5 * BYTE + 0.002 + 0.04 + 6 * BYTE, abs=0.02)


def test_slow_ecu_is_measured_not_assumed():
    clock, model, link = rig(turnaround_s=0.03)
    for _ in range(40):
        link.read_block(0, 8)
    assert link.timing.overhead_s == pytest.approx(0.0305, abs=0.001)
    assert 0.06 < link.timing.response_timeout < 0.09
    assert link.stats.errors == 0


def test_gap_backs_off_on_errors_and_recovers():
    tm = AdaptiveTiming(protocol.DEFAULT)
    assert tm.gap == 0.002
    for _ in range(200):                                     # a noisy line: 1 in 20 fails
        tm.on_failure() if _ % 20 == 0 else tm.on_success(0.003, 0.001)
    assert tm.gap == 0.002                                   # scattered errors cost nothing
    for _ in range(3):                                       # a cluster does
        tm.on_failure()
    assert tm.gap == 0.008
    for _ in range(10):
        tm.on_failure()
    assert tm.gap == 0.05                                    # ceiling
    for _ in range(100):
        tm.on_success(0.003, 0.001)
    assert tm.gap == 0.002                                   # back at the floor


def test_fixed_timing_when_adaptive_is_off():
    f = protocol.DEFAULT.with_overrides({"adaptive_timing": False})
    clock, model, link = rig(f)
    for _ in range(20):
        link.read_block(0, 4)
    assert link.timing.response_timeout == 0.2 and link.timing.inter_byte_timeout == 0.05


# ---- resync, retries, counters ----

def test_late_reply_is_drained_and_the_retry_succeeds():
    clock, model, link = rig()
    for _ in range(30):
        link.read_block(0, 4)                                # timeout adapts down to 20 ms
    model.faults = Faults(slow_reply=1.0, slow_reply_s=0.06) # replies now 60 ms late
    first = model.requests
    with pytest.raises(protocol.NoReply):
        link.read_block(0x10, 4, retries=0)
    t_fail = clock.now()
    assert link.stats.timeouts == 1 and model.requests == first + 1
    model.faults = Faults()
    r = link.read_block(0x76, 3)                             # right data, not the late frame
    assert r.data == b"SIM" and r.attempts == 1
    s = link.stats
    assert s.resyncs == 1 and s.discarded_bytes == 7         # the late frame, thrown away whole
    # the watch ended as soon as the late frame had passed, not after 200 ms
    assert r.t_request - t_fail < 0.08


def test_retry_with_backoff_hides_a_single_bad_frame():
    clock, model, link = rig()
    model.faults = Faults(bad_checksum=1.0)
    hits = []
    real = model._damage

    def damage_first_only(frame):
        if not hits:
            hits.append(1)
            return real(frame)
        return frame, 0.0
    model._damage = damage_first_only
    t0 = clock.now()
    r = link.read_block(0x76, 10)
    assert r.data == b"SIM-P2P\x00\x00\x01" and r.attempts == 2
    s = link.stats
    assert (s.checksum_failures, s.retries, s.resyncs, s.ok) == (1, 1, 1, 1)
    assert clock.now() - t0 > 2 * 18 * BYTE + link.BACKOFF_BASE_S


def test_retries_exhausted_raises_the_last_error():
    clock, model, link = rig()
    model.faults = Faults(bad_checksum=1.0)
    with pytest.raises(protocol.BadChecksum):
        link.read_block(0, 4, retries=2)
    assert link.stats.requests == 3 and link.stats.retries == 2 and link.stats.checksum_failures == 3


def test_junk_before_the_reply_is_skipped_and_counted():
    clock, model, link = rig()
    model.faults = Faults(junk=1.0, seed=3)
    for addr in range(0, 64, 4):
        assert link.read_block(addr, 4).data == bytes(model.ram[addr:addr + 4])
    assert link.stats.junk_bytes > 0 and link.stats.ok == 16


def test_dropped_reply_byte_is_a_short_reply_then_recovers():
    clock, model, link = rig()
    model.faults = Faults(drop_byte=1.0)
    with pytest.raises(protocol.ProtocolError):
        link.read_block(0, 8, retries=0)
    assert link.stats.timeouts + link.stats.bad_frames + link.stats.checksum_failures == 1
    model.faults = Faults()
    assert len(link.read_block(0, 8).data) == 8


def test_damaged_echo_is_counted_but_the_frame_still_counts():
    clock, model, link = rig()
    link.read_block(0, 4)
    assert link.echo_seen
    model.faults = Faults(drop_echo=1.0)
    assert link.read_block(0x76, 3).data == b"SIM"
    assert link.stats.echo_mismatches == 1


def test_no_echo_cable_is_detected_and_costs_no_timeout():
    clock, model, link = rig(echo=False)
    link.read_block(0, 16)
    assert not link.echo_seen
    t0 = clock.now()
    r = link.read_block(0, 16)
    assert r.t_done - t0 < 24 * BYTE + 0.008 and link.stats.echo_mismatches == 0


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_fault_soup_never_returns_wrong_data(seed):
    clock, model, link = rig()
    model.faults = Faults(drop_byte=0.05, bad_checksum=0.05, junk=0.05, no_reply=0.03,
                          slow_reply=0.03, drop_echo=0.03, seed=seed)
    import random
    model.fault_rng = random.Random(seed)
    model.ram[0x40:0x80] = bytes(range(0x40, 0x80))          # static, known contents
    good = bad = 0
    for i in range(600):
        addr = 0x40 + (i * 7) % 48
        try:
            data = link.read_block(addr, 8).data
        except protocol.ProtocolError:
            bad += 1
            continue
        assert data == bytes(range(addr, addr + 8))
        good += 1
    s = link.stats
    assert good > 540 and bad < 60
    assert s.retries > 20 and s.resyncs >= s.retries
    assert s.checksum_failures and s.timeouts and s.junk_bytes
    assert s.ok == good


def test_faults_are_deterministic_for_a_seed():
    def run(seed):
        clock, model, link = rig(faults=Faults.flaky(seed))
        out = []
        for i in range(400):
            try:
                out.append(link.read_block(i % 64, 4).data)
            except protocol.ProtocolError as exc:
                out.append(type(exc).__name__)
        return out, link.stats.as_dict(), dict(model.fault_counts), clock.now()
    assert run(5) == run(5)
    assert run(5) != run(6)


def test_faults_parse():
    f = Faults.parse("drop=0.01, cksum=0.02,junk=0.03,slow=0.04,silent=0.05,echo=0.06,seed=9")
    assert (f.drop_byte, f.bad_checksum, f.junk, f.slow_reply, f.no_reply, f.drop_echo, f.seed) == \
        (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 9)
    assert not Faults.parse("").any()
    with pytest.raises(ValueError):
        Faults.parse("gremlins=1")


# ---- read-only guard ----

def test_raw_blocks_everything_but_reads_until_unlocked():
    clock, model, link = rig()
    echo, rest = link.raw(protocol.build_read_request(0x76, 3))
    assert echo and protocol.find_any_frame(rest)[2:5] == b"SIM"
    link.raw(protocol.DEFAULT.init_sequence)                 # the wake bytes are allowed
    reset = protocol.with_checksum(bytes([0x21, 0x04, 0x01]))
    before = model.requests
    for frame in (reset, b"\x20\x05\x00\x10", b"\x20\x05\x00\x10\x00", b"\x27\x05\x00\x01\xd3"):
        with pytest.raises(CommandBlocked):
            link.raw(frame)
    assert model.requests == before and link.unlocked_frames == []   # nothing reached the wire
    link.unlock()
    link.raw(reset)
    assert link.unlocked_frames == [reset]                   # kept for the log header
    link.unlock(False)
    with pytest.raises(CommandBlocked):
        link.raw(reset)


# ---- poller: schedule, timestamps, recovery ----

def start_poller(clock=None, **model_kw):
    clock = clock or VirtualClock()
    model = EcuModel(clock=clock.now, rev_every_s=0, **model_kw)
    amap = addressmap.load_default()
    poller = Poller(LoopbackTransport(model, clock, byte_timing=True), amap, clock=clock)
    poller.start()
    return clock, model, poller


def run_until(clock, t, wall=20.0):
    end = time.monotonic() + wall
    while clock.now() < t and time.monotonic() < end:
        time.sleep(0.002)
    assert clock.now() >= t, "virtual time did not advance (poller thread stuck?)"


def test_poller_rates_per_channel_and_shortfall():
    clock, model, poller = start_poller()
    try:
        run_until(clock, 20)
    finally:
        poller.stop()
    rates = poller.channel_rates()
    assert set(rates) == set(poller.amap.names)
    for name in ("rpm", "map", "tps", "inj", "ign", "o2"):
        assert rates[name].achieved_hz > 15, name            # research target: 15-20 Hz
        assert rates[name].worst_gap_s < 0.12
    for name in ("stft", "ltft"):
        assert rates[name].achieved_hz == pytest.approx(5, rel=0.15)
    for name in ("ect", "iat", "batt", "mil", "knock"):
        assert rates[name].achieved_hz == pytest.approx(1, rel=0.15)
    # 20 Hz on the fast class does not fit on a 9600 baud wire with this map,
    # and the report says so instead of hiding it.
    assert poller.bus_load > 1
    short = poller.shortfalls()
    assert "rpm" in short and 0 < short["rpm"].shortfall_hz < 5
    assert "ect" not in short
    classes = poller.class_rates()
    assert classes["fast"][0] == 20 and 15 < classes["fast"][1] < 20
    predicted = poller.predicted_rates()
    assert rates["rpm"].achieved_hz == pytest.approx(predicted["rpm"], rel=0.08)
    assert poller.errors == 0 and poller.hz > 15
    report = poller.link_report()
    assert report["timeouts"] == 0 and report["ok"] == poller.reads


def test_samples_carry_per_channel_timestamps_and_latency():
    clock, model, poller = start_poller()
    blocks = []
    poller.on_block = blocks.append
    try:
        run_until(clock, 5)
    finally:
        poller.stop()
    samples = [s for s in poller.samples if s.link]
    assert len(samples) > 60
    first = samples[0]
    assert all(v is not None for v in first.values.values())  # no sample before every block was read
    s = samples[-1]
    assert s.mono - poller.t0 == pytest.approx(s.t)
    assert 0.003 < s.latency < 0.012
    ages = {n: s.t - s.stamps[n] for n in s.values}
    assert ages["rpm"] < 0.12 and ages["map"] < 0.12
    assert 0.0 < max(ages.values()) < 1.2                    # slow channels: up to a second old
    assert ages["rpm"] != ages["map"]                        # different reads, different instants
    assert s.fresh >= {"rpm", "map", "inj"}                  # every sample is a full fast cycle
    assert sum("ect" in x.fresh for x in samples) < len(samples) / 5
    # time moves forward and per-read records line up with the samples
    assert all(b.t > a.t for a, b in zip(samples, samples[1:]))
    rpm_reads = [b for b in blocks if "rpm" in b.values]
    assert len(rpm_reads) > 60 and rpm_reads[-1].addr == 0x00
    last = [b for b in rpm_reads if b.t <= s.t][-1]
    assert s.stamps["rpm"] == pytest.approx(last.t - last.latency_s)
    assert 600 < s.values["rpm"] < 1700


def test_key_off_then_key_on_recovers_without_restart():
    # Scripted on the model's own (virtual) clock so the run repeats exactly:
    # key off at 3 s (ECU silent), key on again at 7 s.
    clock, model, poller = start_poller(stall_after_s=3.0, stall_mode="power",
                                        restart_after_s=4.0)
    try:
        run_until(clock, 12)
    finally:
        poller.stop()
    samples = list(poller.samples)
    down = [s for s in samples if not s.link]
    assert down and all(s.values == {} for s in down)
    # noticed within ~1 s: two block reads must fail (each retried) and the line
    # stay silent for 0.6 s, so a P2P that skips a request or two at idle does
    # not drop the link (docs/analysis/2026-11-26/link-dropouts.md)
    assert 3.0 <= down[0].t < 4.1
    gaps = [b.t - a.t for a, b in zip(down, down[1:])]
    assert max(gaps) < 0.9                                   # link-down samples keep coming
    back = [s for s in samples if s.link and s.t > 7.0]
    assert back and back[0].t - 7.0 < 2.0                    # polling again within 2 s
    assert all(v is not None for v in back[0].values.values())
    assert not [s for s in samples if not s.link and s.t > back[0].t]
    assert poller.state in (OK, "stopped") and poller.link.stats.wakes >= 2
    assert poller.channel_rates()["rpm"].achieved_hz > 15
    assert NO_REPLY == "no reply"


def test_cable_unplug_and_replug_recovers_without_restart():
    clock = VirtualClock()
    model = EcuModel(clock=clock.now, rev_every_s=0)
    model.schedule_unplug(at=2.0, for_s=3.0)                 # out at 2 s, back in at 5 s
    amap = addressmap.load_default()
    poller = Poller(LoopbackTransport(model, clock, byte_timing=True), amap, clock=clock)
    states = []
    poller.on_sample = lambda s: states.append((s.t, poller.state, poller.last_error))
    poller.start()
    try:
        run_until(clock, 10)
        assert poller.submit(lambda link: link.read_mem(0x76, 3)).result(5) == b"SIM"
    finally:
        poller.stop()
    lost = [x for x in states if x[1] == PORT_LOST]
    assert lost and 2.0 <= lost[0][0] < 2.2 and "unplugged" in lost[0][2]
    samples = list(poller.samples)
    back = [s for s in samples if s.link and s.t > 5.0]
    assert back and back[0].t - 5.0 < 3.0                    # reopen backoff + wake + first reads
    assert not [s for s in samples if not s.link and s.t > back[0].t]
    assert poller.stats.reconnects == 1
    assert poller.channel_rates()["rpm"].achieved_hz > 15


def test_jobs_fail_fast_while_the_port_is_gone():
    clock, model, poller = start_poller()
    try:
        run_until(clock, 1)
        model.unplug()
        run_until(clock, clock.now() + 2)
        assert poller.state == PORT_LOST
        with pytest.raises(TransportError):
            poller.submit(lambda link: link.read_mem(0, 1)).result(5)
    finally:
        poller.stop()


def test_poller_survives_a_flaky_line():
    clock, model, poller = start_poller(faults=Faults.flaky(4))
    try:
        run_until(clock, 30)
    finally:
        poller.stop()
    s = poller.stats
    assert poller.state in (OK, "stopped") and s.retries > 5 and s.resyncs > 5
    assert s.ok > 1200 and s.errors < 0.06 * s.requests
    assert poller.channel_rates()["rpm"].achieved_hz > 13
    assert all(sample.link for sample in list(poller.samples)[5:])   # never dropped to NO_REPLY


def test_poller_replans_when_the_ecu_is_slower_than_assumed():
    clock, model, poller = start_poller(turnaround_s=0.012)
    before = poller.blocks
    try:
        run_until(clock, 15)
    finally:
        poller.stop()
    assert poller.cost.turnaround_s == pytest.approx(0.0125, abs=0.001)
    assert len(poller.blocks) < len(before)                  # dearer requests: fewer, longer reads
    assert all(v is not None for v in poller.samples[-1].values.values())


def test_burst_mode_reads_one_channel_fast():
    clock, model, poller = start_poller()
    blocks = []
    try:
        run_until(clock, 2)
        poller.on_block = blocks.append
        assert poller.burst(["rpm"], 2.0).result(5) == [(0, 2)]
        t0 = clock.now()
        run_until(clock, t0 + 1.9)
        n = len([b for b in blocks if b.addr == 0 and len(b.data) == 2])
        assert n / 1.9 > 45                                  # research target: 45 Hz or better
        run_until(clock, t0 + 5)
        assert poller.channel_rates()["ect"].achieved_hz > 0.7   # normal schedule is back
    finally:
        poller.stop()


def test_make_transport_builtin_sims():
    t = make_transport("sim:")
    assert isinstance(t, LoopbackTransport) and t.byte_timing and not t.model.faults.any()
    flaky = make_transport("sim:flaky")
    assert flaky.model.faults.any() and flaky.name == "sim:flaky"
    # sim: is a stock P2P whatever the map says; the port opens at the map's baud
    amap = addressmap.load_default()
    p2p = make_transport("sim:", amap.framing)
    assert p2p.model.framing.baud == 10400 and p2p.model.need_wake and p2p.baud == 10400
    link = Link(p2p, amap.framing.with_overrides({"retries": 0}))
    link.wake()
    assert link.read_mem(0x76, 3) == b"SIM"
    old = make_transport("sim:", protocol.DEFAULT)              # a map still at 9600
    assert old.baud == 9600
    link = Link(old, protocol.DEFAULT.with_overrides({"retries": 0}))
    link.wake()
    with pytest.raises(protocol.NoReply):
        link.read_mem(0x76, 3)                                  # like the car: no Honda at 9600
    p2t = make_transport("sim:p2t", protocol.DEFAULT)
    assert p2t.model.framing.baud == 9600 and not p2t.model.need_wake
    assert Link(p2t, protocol.DEFAULT).read_mem(0x76, 3) == b"SIM"


def test_transport_base_refuses_break_and_baud():
    with pytest.raises(TransportError):
        Transport().set_break(True)
    with pytest.raises(TransportError):
        Transport().set_baud(10400)


# ---- short ECU deafness and damaged requests (the P2P on the car, 2026-11-26) ----

def test_short_ecu_deafness_does_not_drop_the_link():
    # The stock P2P ignores everything for ~0.2-0.45 s about three times a
    # minute at idle. That used to drop the link (values blanked, wake bytes,
    # a "link 0" for the stall catcher); now the poller just reads on.
    clock, model, poller = start_poller(faults=Faults(deaf=1e-12, deaf_s=0.0))
    reads, deaf = [], {}

    def on_block(r):                                        # on the poller thread: exact timing
        reads.append(r.t)
        if r.t > 3.0 and not deaf:
            deaf["t"], deaf["wakes"] = r.t, poller.link.stats.wakes
            model._deaf_until = clock.now() + 0.4
    poller.on_block = on_block
    try:
        run_until(clock, 8)
    finally:
        poller.stop()
    t_deaf = deaf["t"]
    assert not [s for s in poller.samples if not s.link]
    assert poller.link.stats.wakes == deaf["wakes"] and poller.link.stats.timeouts >= 2
    back = min(t for t in reads if t > t_deaf)              # first answer after the deaf stretch
    assert 0.4 < back - t_deaf < 0.9 and poller.error_kinds["NoReply"] >= 2


def test_p2p_fault_preset_keeps_the_link_up():
    clock, model, poller = start_poller(faults=Faults.p2p(seed=3))
    try:
        run_until(clock, 120, wall=60)
    finally:
        poller.stop()
    s = poller.link.stats
    assert s.bad_frames > 20 and s.echo_mismatches >= s.bad_frames and s.checksum_failures <= 2
    assert model.fault_counts.get("deaf", 0) >= 3
    drops = sum(1 for a, b in zip(poller.samples, list(poller.samples)[1:]) if a.link and not b.link)
    assert drops <= 1


def test_every_failed_attempt_is_reported():
    clock, model, link = rig()
    link.read_block(0, 4)
    seen = []
    link.on_error = lambda addr, n, exc, attempt, t: seen.append((addr, n, type(exc).__name__, attempt))
    model.faults = Faults(no_reply=1.0)
    with pytest.raises(protocol.NoReply):
        link.read_block(0x10, 4)
    assert seen == [(0x10, 4, "NoReply", 1), (0x10, 4, "NoReply", 2)]


class _FakeSerial:
    """Enough of pyserial's Windows Serial: no fileno, and every change of
    ``timeout`` counted (on Windows each one reprograms the adapter)."""

    def __init__(self, chunks):
        self._timeout = 0.2
        self.sets = 0
        self.buf = bytearray()
        self.chunks = list(chunks)              # bytes that "arrive" on each read call

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        self.sets += 1
        self._timeout = value

    @property
    def in_waiting(self):
        return len(self.buf)

    def read(self, n):
        if not self.buf and self.chunks:
            self.buf += self.chunks.pop(0)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out


def test_windows_read_some_does_not_reprogram_the_port_per_byte():
    from hondalog.transport import SerialTransport
    tr = SerialTransport("COM99", 10400)
    reply = bytes.fromhex("20 05 00 03 d8 00 06 0e 94 00 58")
    tr._ser = _FakeSerial([reply[i:i + 1] for i in range(len(reply))] + [b""] * 5)
    tr._fd = -1
    tr._timeout = 0.2
    got = bytearray()
    for _ in range(40):
        got += tr.read_some(64, 0.05)
        if len(got) == len(reply):
            break
    assert bytes(got) == reply
    assert tr._ser.sets == 1                    # set once to the poll slice, never again
