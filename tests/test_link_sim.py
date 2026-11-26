"""Link + poller + recorder against the in-process simulator."""

import time

import pytest

from hondalog import addressmap, protocol
from hondalog.logger import Recorder, read_log
from hondalog.poller import OK, Poller
from hondalog.sim import EcuModel
from hondalog.transport import Link, LoopbackTransport, Transport


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Scripted(Transport):
    """Replays canned bytes after each write."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.rx = bytearray()
        self.sent = []

    def write(self, data):
        self.sent.append(bytes(data))
        if self.answers:
            self.rx += self.answers.pop(0)

    def read(self, size, timeout):
        out = bytes(self.rx[:size])
        del self.rx[:size]
        return out

    def flush_input(self):
        self.rx.clear()


FAST = protocol.DEFAULT.with_overrides({"reply_timeout_s": 0.03, "init_sequence": "",
                                        "inter_request_delay_s": 0.0, "retries": 0,
                                        "inter_byte_timeout_s": 0.03})


def test_sim_echoes_then_replies():
    t = LoopbackTransport(EcuModel(rev_every_s=0))      # a stock P2P: 10400 baud
    assert t.baud == 10400
    req = protocol.build_read_request(0x00, 2)
    t.write(req)
    assert t.read(64, 0.1) == req                       # not woken: the echo only
    t.write(protocol.DEFAULT.init_sequence)
    assert t.read(64, 0.1) == protocol.DEFAULT.init_sequence
    t.write(req)
    got = t.read(64, 0.1)
    assert got[:5] == req                               # echo first
    assert protocol.parse_reply(got, req, 2)


def test_p2p_answers_honda_only_at_10400_after_the_wake_frame():
    from hondalog.sim import P2P_FRAMING
    m = EcuModel(rev_every_s=0)
    t = LoopbackTransport(m)
    req = protocol.build_read_request(0x76, 3)
    wake = P2P_FRAMING.init_sequence
    for baud in (9600, 10400):                          # no wake frame: silent at either speed
        t.set_baud(baud)
        t.write(req)
        assert t.read(64, 0.1) == req
    t.set_baud(9600)
    t.write(wake)                                       # a wake frame at 9600 is noise to it
    t.write(req)
    t.read(64, 0.1)
    assert not m.woken and m.honda_ignored == 1         # only the read at 10400 got that far
    t.set_baud(10400)
    t.write(wake)
    t.read(64, 0.1)
    assert m.woken and m.wakes == 1
    t.write(req)
    assert t.read(64, 0.1)[5:10] == b"\x00\x06SIM"
    t.set_baud(9600)
    t.write(req)
    assert t.read(64, 0.1) == req                       # woken, still deaf at 9600
    m.power_cut()
    m.restart()
    t.set_baud(10400)
    t.write(req)
    assert t.read(64, 0.1) == req                       # key cycled: wake it again
    link = Link(t, P2P_FRAMING)
    link.wake()
    assert link.read_mem(0x76, 3) == b"SIM"
    p2t = LoopbackTransport(EcuModel(framing=protocol.DEFAULT, rev_every_s=0))
    p2t.write(req)                                      # kerpz's P2T: 9600, no wake needed
    assert p2t.baud == 9600 and p2t.read(64, 0.1)[5:10] == b"\x00\x06SIM"


def test_sim_ignores_bad_checksum_and_resyncs():
    m = EcuModel(rev_every_s=0)
    assert m.feed(bytearray(protocol.build_read_request(0x10, 1))) == b""   # asleep
    assert m.feed(bytearray(protocol.DEFAULT.init_sequence)) == b"" and m.woken
    buf = bytearray(b"\x20\x05\x00\x02\x00")            # wrong checksum
    assert m.feed(buf) == b""
    buf += protocol.build_read_request(0x10, 1)
    assert protocol.verify(m.feed(buf))
    wake = bytearray(protocol.DEFAULT.init_sequence)
    assert m.feed(wake) == b""


def test_sim_idle_rev_and_stall():
    clock = FakeClock()
    m = EcuModel(clock=clock, warmup_s=1, rev_every_s=0, restart_after_s=None)
    amap = addressmap.load_default()

    def rpm():
        m.step()
        return amap.decode(dict(enumerate(m.ram)))["rpm"]

    for _ in range(100):
        clock.now += 0.1
        idle = rpm()
    assert 700 < idle < 800
    m.rev()
    peak = 0
    for _ in range(30):
        clock.now += 0.1
        peak = max(peak, rpm())
    assert peak > 2500
    m.stall()
    for _ in range(20):
        clock.now += 0.1
        dead = rpm()
    assert dead == 0
    m.power_cut()
    assert m.feed(bytearray(protocol.build_read_request(0, 2))) == b""


# FAST is a 9600 baud framing without wake bytes: kerpz's P2T, not the P2P

def test_link_reads_through_echo():
    link = Link(LoopbackTransport(EcuModel(framing=FAST, rev_every_s=0)), FAST)
    data = link.read_mem(0x76, 10)
    assert data == b"SIM-P2P\x00\x00\x01" and link.echo_seen


def test_link_works_without_echo():
    link = Link(LoopbackTransport(EcuModel(framing=FAST, rev_every_s=0, echo=False)), FAST)
    assert len(link.read_mem(0x00, 16)) == 16 and not link.echo_seen


@pytest.mark.parametrize("answer,exc", [
    (b"", protocol.NoReply),
    (protocol.build_read_request(0, 2), protocol.NoReply),
    (protocol.build_read_request(0, 2) + b"\x00\x05\x01", protocol.ShortReply),
    (protocol.build_read_request(0, 2) + b"\x00\x05\x01\x02\x03", protocol.BadChecksum),
    (b"\xff" * 12, protocol.BadFrame),
])
def test_link_malformed_replies(answer, exc):
    with pytest.raises(exc):
        Link(Scripted([answer]), FAST).read_mem(0, 2)


def test_link_raw_separates_echo():
    link = Link(LoopbackTransport(EcuModel(framing=FAST, rev_every_s=0)), FAST)
    req = protocol.build_read_request(0x76, 3)
    echo, rest = link.raw(req, quiet_s=0.03, max_s=0.3)
    assert echo and protocol.find_any_frame(rest)[2:5] == b"SIM"


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_poller_end_to_end_with_stall(tmp_path):
    amap = addressmap.load_default()
    model = EcuModel(warmup_s=0.5, rev_every_s=0, restart_after_s=None)
    poller = Poller(LoopbackTransport(model), amap)
    poller.start()
    assert poller.ready.wait(3)
    rec = Recorder(amap, poller.ecu_id_text, "sim:", out_dir=tmp_path, post_s=0.5)
    poller.on_sample = rec.feed
    try:
        assert _wait(lambda: poller.sweeps > 20)
        assert poller.state == OK and poller.errors == 0 and poller.hz > 1
        assert b"SIM-P2P" in poller.ecu_id
        rec.mark("it stumbled")
        fut = poller.submit(lambda link: link.read_mem(0x76, 3))
        assert fut.result(2) == b"SIM"
        assert _wait(lambda: model.rpm > 400 and model.rpm < 1000)
        time.sleep(0.2)
        model.stall()
        assert _wait(lambda: not rec.events.empty())
    finally:
        poller.stop()
        rec.stop()
    event = rec.events.get_nowait()
    assert event.reason == "rpm" and event.path.name.startswith("stall_")

    log = read_log(rec.path)
    assert log.meta["address_map_version"] == amap.version
    assert "SIM-P2P" in log.meta["ecu_id"]
    assert len(log.t) > 20 and 600 < log.data["rpm"][5] < 1700
    texts = [text for _, text in log.markers]
    assert "it stumbled" in texts and any("STALL" in t for t in texts)

    stall = read_log(event.path)
    assert "rpm" in stall.meta["stall_trigger"]
    assert stall.data["rpm"][0] > 400 and stall.data["rpm"][-1] == 0
    assert stall.t[-1] - float(stall.meta["stall_time_s"]) >= 0.5


def test_poller_reports_silent_ecu():
    amap = addressmap.load_default()
    amap.framing = FAST
    model = EcuModel(framing=FAST, rev_every_s=0)
    model.power_cut()
    model.restart_after_s = None
    poller = Poller(LoopbackTransport(model), amap)
    poller.start()
    try:
        assert _wait(lambda: poller.state == "no reply", 5)
        assert poller.ecu_id is None and "echo only" in poller.last_error
        assert not poller.samples[-1].link
        model.restart()
        assert _wait(lambda: poller.state == OK, 5)
    finally:
        poller.stop()
