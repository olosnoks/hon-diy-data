"""The handheld's portable C core (firmware/handheld/components/hhcore)
against the Python it was ported from: framing, planner, scheduler, formulas,
alarms, and the whole engine on the Python ECU simulator."""

import ctypes as C
import math
import random
import struct

import pytest

import hhhost
from hondalog import addressmap, protocol
from hondalog.alarms import AlarmEngine
from hondalog.expr import compile_expr
from hondalog.handheld import format as F
from hondalog.handheld.bytecode import Pool, Unsupported, compile_condition, compile_formula
from hondalog.handheld.mapfile import build_map, read_map
from hondalog.link.planner import CostModel, plan_blocks, spans_from_channels
from hondalog.link.scheduler import Scheduler, target_hz
from hondalog.poller import Poller
from hondalog.sim import Faults
from hondalog.transport import LoopbackTransport
from hondalog.link.clock import VirtualClock


@pytest.fixture(scope="session")
def L(tmp_path_factory):
    lib = hhhost.lib(tmp_path_factory.mktemp("hhhost"))
    if lib is None:
        pytest.skip("no C compiler")
    return lib


@pytest.fixture(scope="session")
def amap():
    return addressmap.load_default()


@pytest.fixture(scope="session")
def map_bytes(amap):
    return build_map(amap)


@pytest.fixture
def handheld(L, tmp_path, map_bytes):
    made = []

    def make(cfg="", **kw):
        kw.setdefault("map_bytes", map_bytes)
        h = hhhost.Handheld(L, tmp_path / f"sd{len(made)}", cfg=cfg, **kw)
        made.append(h)
        return h
    yield make
    for h in made:
        h.close()


# ---- framing ----------------------------------------------------------------

def test_checksum_and_guard_match_python(L):
    rng = random.Random(1)
    framing = protocol.DEFAULT
    for _ in range(300):
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 20)))
        assert L.hh_checksum(data, len(data)) == protocol.checksum(data)
    # the read-only guard: the same verdict as Link.allowed on anything
    from hondalog.transport import Link
    link = Link(LoopbackTransport())
    buf = (C.c_uint8 * 256)()
    f = C.create_string_buffer(256)
    L.hh_framing_default.argtypes = [C.c_void_p]
    L.hh_framing_default(f)
    del buf
    frames = [protocol.build_read_request(a, n) for a in (0, 0x76, 0xFF) for n in (1, 16, 255)]
    frames += [framing.init_sequence, bytes.fromhex("2005001000"), bytes.fromhex("21050010CA"),
               bytes.fromhex("200600100000"), b"\x20", bytes.fromhex("686af1040000")]
    frames += [bytes(rng.randrange(256) for _ in range(5)) for _ in range(500)]
    for frame in frames:
        assert bool(L.hh_frame_allowed(f, frame, len(frame))) == link.allowed(frame), frame.hex()
    # ISO: only Mode 01 <pid> and Mode 03 can be framed
    def iso(payload):
        body = b"\x68\x6a\xf1" + payload
        return body + bytes([sum(body) & 0xFF])
    assert L.hh_iso_allowed(iso(b"\x01\x0c"), 6) and L.hh_iso_allowed(iso(b"\x03"), 5)
    for bad in (iso(b"\x04"), iso(b"\x02\x0c\x00"), iso(b"\x07"), iso(b"\x01"), iso(b"\x03\x00"),
                iso(b"\x01\x0c")[:-1] + b"\x00", b"\xc1\x33\xf1\x81\x66"):
        assert not L.hh_iso_allowed(bad, len(bad)), bad.hex()


# ---- planner parity ----------------------------------------------------------

class _Ch:
    def __init__(self, name, addr, length, rate):
        self.name, self.addr, self.length, self.rate_hz = name, addr, length, rate


def _python_plan(chans, max_len, cost):
    spans = spans_from_channels(chans, lambda c: c.rate_hz)
    return plan_blocks(spans, max_len, cost)


def _same_plan(L, chans, max_len=16, turnaround=0.002, gap=0.002, baud=9600):
    cost = CostModel(byte_s=10.0 / baud, turnaround_s=turnaround, gap_s=gap)
    want = _python_plan(chans, max_len, cost)
    got, load = hhhost.plan(L, [(c.addr, c.length, c.rate_hz) for c in chans], max_len, baud, turnaround, gap)
    assert [(b.addr, b.length, b.rate_hz, b.channels) for b in want] == \
           [(a, n, r, tuple(chans[i].name for i in idx)) for a, n, r, idx in got]
    # (Python's sum() of floats is compensated since 3.12, so the last bit may differ)
    assert load == pytest.approx(sum(b.rate_hz * cost.request_s(b.length) for b in want), rel=1e-13)


def test_planner_matches_python_on_the_default_map(L, amap):
    chans = [_Ch(c.name, c.addr, c.length, target_hz(c.rate, amap.framing)) for c in amap.channels]
    _same_plan(L, chans, amap.framing.max_read_len)
    # and for the overheads a real cable gives (the engine re-plans from what it measures)
    for turnaround, gap in ((0.0, 0.002), (0.004, 0.002), (0.010, 0.002), (0.018, 0.05), (0.001, 0.0)):
        _same_plan(L, chans, amap.framing.max_read_len, turnaround, gap)


def test_planner_matches_python_on_random_maps(L):
    rng = random.Random(7)
    for trial in range(400):
        n = rng.randrange(1, 40)
        chans = []
        for i in range(n):
            length = rng.choice((1, 1, 1, 2, 2, 4))
            addr = rng.randrange(0, 256 - length) if rng.random() < 0.7 else rng.randrange(0, 40)
            chans.append(_Ch(f"c{i}", addr, length, rng.choice((20.0, 5.0, 1.0))))
        _same_plan(L, chans, rng.choice((4, 8, 16, 16, 16, 32, 64)), rng.choice((0.0, 0.002, 0.006, 0.02)),
                   rng.choice((0.0, 0.002, 0.01)), rng.choice((9600, 10400)))


def test_the_engine_plans_what_the_poller_plans(L, amap, handheld):
    clock = VirtualClock()
    poller = Poller(LoopbackTransport(clock=clock), amap, clock=clock)
    h = handheld()
    assert [(int(h.get("plan.addr", i)), int(h.get("plan.length", i))) for i in range(int(h.get("plan.n")))] \
        == poller.blocks


# ---- scheduler parity --------------------------------------------------------

def test_scheduler_matches_python_step_for_step(L):
    from hondalog.link.planner import Block
    rng = random.Random(3)
    for trial in range(60):
        n = rng.randrange(1, 12)
        rates = [rng.choice((20.0, 20.0, 5.0, 1.0)) for _ in range(n)]
        py = Scheduler([Block(i, 1, r) for i, r in enumerate(rates)])
        cs = L.hhhost_sched_new(n, (C.c_double * n)(*rates))
        t = rng.random()
        try:
            for step in range(600):
                want = py.next(t)
                got = L.hh_sched_next(cs, t)
                assert got == want, (trial, step)
                t += rng.choice((0.008, 0.0125, 0.021, 0.03)) + rng.random() * 0.004
                roll = rng.random()
                if roll < 0.07:
                    py.failed(want, t); L.hh_sched_failed(cs, want, t)
                else:
                    py.done(want, t); L.hh_sched_done(cs, want, t)
                if roll > 0.995:
                    py.restart(); L.hh_sched_restart(cs)
                if rng.random() < 0.01:
                    t += rng.choice((0.3, 2.0, 30.0))          # the link was down for a while
        finally:
            L.hhhost_sched_free(cs)


# ---- formulas ----------------------------------------------------------------

def _float32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def test_every_channel_decodes_as_on_the_laptop(L, amap, map_bytes):
    """All 256 (or 65536) raw values of every channel of the default map."""
    assert L.hhhost_map_check(map_bytes, len(map_bytes)) == 0
    for index, ch in enumerate(amap.channels):
        for x in range(256 ** ch.length) if ch.length <= 2 else random.Random(index).sample(range(256 ** ch.length), 4000):
            raw = x.to_bytes(ch.length, ch.endian)
            want = ch.convert(raw)
            got = L.hhhost_map_decode(map_bytes, len(map_bytes), index, raw)
            if want is None:
                assert math.isnan(got), (ch.name, x)
            else:
                assert got == _float32(want), (ch.name, x, got, want)


FORMULAS = [
    "x", "x / 4", "1875000 / (x + 1)", "x * 0.716 - 5", "(x - 24) / 2", "(x / 128 - 1) * 100", "77.06 - x / 2.5371",
    "bit(x, 2)", "bit(x, 0) + bit(x, 7) * 2", "signed(x)", "signed(x, 4)", "x & 0x0F", "x | 0x80", "x ^ 0xFF", "~x",
    "x << 2", "x >> 3", "x // 7", "x % 7", "-x % 7", "x ** 2", "x ** 0.5", "2 ** x", "abs(x - 100)", "-x", "+x", "not x",
    "!x", "x > 100", "x >= 100", "x == 100", "x != 100", "50 < x < 100", "50 <= x <= 100 < 200", "x < 50 or x > 200",
    "x > 50 and x < 200", "x > 50 && x < 200 || x == 3", "x > 100 ? 1 : 2", "5 if x > 100 else 6",
    "x == 0 ? 0 : 1000 / x", "1000 / x", "min(x, 100)", "max(x, 100, 150)", "clamp(x, 50, 200)", "between(x, 50, 200)",
    "round(x / 7)", "round(x / 2)", "round(x / 7, 2)", "int(x / 7)", "floor(x / 7)", "ceil(x / 7)", "float(x)",
    "sqrt(x)", "sqrt(x - 100)", "exp(x / 50)", "exp(x * 10)", "ln(x)", "log(x + 1)", "log10(x)", "pow(x, 1.5)",
    "sin(x)", "cos(x / 10)", "sign(x - 100)",
    "poly(x, 155.04149, -3.0414878, 0.03952185, -0.00029383913, 0.0000010792568, -0.0000000015618437)",
    "table(x, 0, -40, 50, 0, 100, 25, 200, 80, 255, 140)", "thermistor(x, 0.0013, 0.00026, 0.00000016)",
    "thermistor(x, 0.0013, 0.00026, 0.00000016, 1023)", "b[0]", "b[0] * 256 + b[1]", "b[1] - b[0]", "b[-1]",
    "valid(1 / x)", "coalesce(1 / x, 7)", "coalesce(sqrt(x - 300), ln(x - 300), 9)", "x * k + off", "true + false",
    "0x10 + 0b101 + 1e2 + .5", "(x + 1) * (x - 1) / (x + 2) - x // 3 % 5", "x / 0", "x % 0", "x // 0", "x << 70",
    "x >> 64", "bit(x, 64)", "1e308 * x * 10", "0 and 1 / 0", "1 or 1 / 0", "1 / 0 or 1", "x > 5 and 1 / (x - 10)",
]


def test_formulas_evaluate_as_hondalog_expr_does(L):
    """The byte code of every construct against the laptop's evaluator."""
    constants = {"k": 0.716, "off": -5.0}
    for text in FORMULAS:
        expr = compile_expr(text, names=("x",), constants=constants, arrays=("b",))
        pool = Pool()
        prog = compile_formula(text, pool, constants, 2)
        assert L.hh_expr_check(prog, len(prog), len(pool.values), 0) == 0, text
        consts = (C.c_double * max(1, len(pool.values)))(*pool.values)
        for x in list(range(0, 300)) + [511, 1000, 4095, 65535]:
            raw = (x & 0xFFFF).to_bytes(2, "big")
            want = expr.eval({"x": x, "b": raw})
            got = L.hhhost_eval(prog, len(prog), consts, len(pool.values), None, 0, raw, 2, float(x), 0.0)
            if want is None:
                assert math.isnan(got), (text, x, got)
            else:
                assert got == pytest.approx(want, rel=1e-12, abs=1e-12), (text, x)


CONDITIONS = [
    "[inj] * [rpm] / 1200", "[stft] + [ltft]", "[map] / [baro] * 100", "[rpm] > 400", "[rpm] < 600",
    "[o2] < 0.2 && [tps] > 60 && [rpm] > 3000", "[o2] < 0.2 or [tps] > 60", "rpm > 400 and not batt < 12",
    "clamp(([map] * displacement_l / cylinders * ve / (0.287 * ([iat] + 273.15))) / ([inj] / 60000 * 240 * 0.745), 5, 30)",
    "valid([rpm]) + valid([o2])", "coalesce([o2], [batt], -1)", "[rpm] > 3000 ? [tps] : [map]",
    "max([rpm], [tps], [map]) - min([rpm], [tps])", "time() * 2 + [rpm]", "[tps] > 50 if [rpm] > 1000 else [map] > 90",
    "100 < [rpm] < 3000 < [rpm] * 2",
]
NAMES = ["rpm", "tps", "map", "baro", "o2", "batt", "stft", "ltft", "inj", "iat"]


def test_conditions_with_missing_values_follow_python(L):
    """Math channels and alarm conditions: any input may have no value."""
    rng = random.Random(11)
    constants = {"displacement_l": 1.59, "cylinders": 4, "ve": 0.85}
    index = {name: i for i, name in enumerate(NAMES)}
    for text in CONDITIONS:
        expr = compile_expr(text, names=NAMES, constants=constants)
        pool = Pool()
        prog, deps = compile_condition(text, pool, index, constants)
        assert deps == expr.deps
        assert L.hh_expr_check(prog, len(prog), len(pool.values), len(NAMES)) == 0
        consts = (C.c_double * max(1, len(pool.values)))(*pool.values)
        for _ in range(400):
            values = {n: (None if rng.random() < 0.15 else _float32(rng.choice((0, 0.1, 13.8, 50, 399, 401, 3500))
                                                                    * rng.choice((1, 1, 0.5, 2)))) for n in NAMES}
            t = rng.random() * 100
            want = expr.eval(values, t)
            arr = (C.c_float * len(NAMES))(*[float("nan") if values[n] is None else values[n] for n in NAMES])
            got = L.hhhost_eval(prog, len(prog), consts, len(pool.values), arr, len(NAMES), None, 0, float("nan"), t)
            if want is None:
                assert math.isnan(got), (text, values)
            else:
                assert got == pytest.approx(want, rel=1e-12, abs=1e-12), (text, values)


def test_what_needs_history_is_refused_by_the_compiler():
    pool = Pool()
    for text in ("slope([rpm], 500)", "avg([rpm], 500) > 3", "[rpm-1]", "delta([rpm])", "smooth([rpm], 5)",
                 "prev([rpm])", "minw([rpm], 100)", "[nope] + 1"):
        with pytest.raises(Unsupported):
            compile_condition(text, pool, {"rpm": 0}, {})
    for text in ("b", "b[x]", "y + 1", "[rpm]"):
        with pytest.raises(Unsupported):
            compile_formula(text, pool, {}, 1)


def test_math_channels_of_the_default_map(L, amap, map_bytes):
    mf = read_map(map_bytes)
    names = [c["name"] for c in mf.channels]
    engine = amap.math_engine()
    rng = random.Random(5)
    for _ in range(200):
        values = {c.name: _float32(rng.uniform(c.min, c.max)) for c in amap.channels}
        if rng.random() < 0.3:
            values[rng.choice(amap.names)] = None
        want = engine.update(dict(values), 1.0)
        arr = (C.c_float * len(names))(*[float("nan")] * len(names))
        for i, name in enumerate(names):
            if values.get(name) is not None:
                arr[i] = values[name]
        for i, c in enumerate(mf.channels):
            if c["kind"] != F.KIND_MATH:
                continue
            got = L.hhhost_map_math(map_bytes, len(map_bytes), i, arr, 1.0)
            arr[i] = got                        # later math channels may use it, as on the laptop
            if want[c["name"]] is None:
                assert math.isnan(got), c["name"]
            else:
                assert got == pytest.approx(want[c["name"]], rel=2e-6, abs=1e-6), c["name"]


# ---- alarms ------------------------------------------------------------------

def test_alarms_follow_the_python_engine(L, amap, map_bytes):
    """Random walks through every limit, with dropouts and acknowledgements:
    the same alarms raise and clear at the same samples."""
    mf = read_map(map_bytes)
    names = [c["name"] for c in mf.channels]
    exported = [a["name"] for a in mf.alarms]
    rules = [r for r in amap.alarms if r.name in exported]
    assert [r.name for r in rules] == exported
    kinds = {1: "raised", 2: "cleared", 3: "acknowledged"}
    rng = random.Random(2)
    for trial in range(30):
        py = AlarmEngine(rules, names, amap.constants)
        ca = L.hhhost_alarms_new(map_bytes, len(map_bytes))
        events = (C.c_int * 16)()
        state = {"ect": 90.0, "batt": 13.8, "rpm": 800.0, "o2": 0.5, "tps": 10.0}
        t = 0.0
        try:
            for step in range(1500):
                t += rng.choice((0.05, 0.05, 0.1, 0.25))
                state["ect"] += rng.uniform(-1.5, 1.6)
                state["ect"] = min(115.0, max(85.0, state["ect"]))
                state["batt"] = min(16.0, max(10.5, state["batt"] + rng.uniform(-0.25, 0.25)))
                state["rpm"] = min(7200.0, max(0.0, state["rpm"] + rng.uniform(-600, 620)))
                state["o2"] = rng.choice((0.05, 0.1, 0.5, 0.8))
                state["tps"] = min(100.0, max(0.0, state["tps"] + rng.uniform(-30, 32)))
                values = {n: None for n in names}
                for key, value in state.items():
                    values[key] = None if rng.random() < 0.05 else _float32(value)
                want = [(e.kind, e.name) for e in py.update(values, t)]
                arr = (C.c_float * len(names))(*[float("nan") if values[n] is None else values[n] for n in names])
                n = L.hhhost_alarms_update(ca, arr, len(names), t, events, 16)
                got = [(kinds[events[i] >> 8], exported[events[i] & 0xFF]) for i in range(n)]
                assert got == want, (trial, step, t)
                if rng.random() < 0.02:
                    want = [(e.kind, e.name) for e in py.acknowledge(t=t)]
                    n = L.hhhost_alarms_ack(ca, t, events, 16)
                    assert [(kinds[events[i] >> 8], exported[events[i] & 0xFF]) for i in range(n)] == want
                top = py.top()
                assert L.hhhost_alarms_top(ca) == (exported.index(top.name) if top else -1)
        finally:
            L.hhhost_alarms_free(ca)


# ---- the engine on the Python simulator ---------------------------------------

def test_engine_reads_what_the_poller_reads(handheld, amap):
    """Same simulator, same map, same virtual time: the C engine and the
    Python poller send the same requests and get the same sample rate."""
    h = handheld()
    h.run_until(30.0)
    clock = VirtualClock()
    from hondalog.sim import EcuModel
    model = EcuModel(clock=clock.now, restart_after_s=None, rev_every_s=1e9)
    sent = []
    transport = LoopbackTransport(model, clock, byte_timing=True, usb_latency_s=0.001)
    write = transport.write
    transport.write = lambda data: (sent.append(bytes(data)), write(data))[1]
    poller = Poller(transport, amap, clock=clock)
    poller._connect()
    while clock.now() < 30.0:
        poller._step()
    mine = [frame for _, frame in h.sent]
    assert len(mine) > 1500
    assert mine == sent[:len(mine)] or sent == mine[:len(sent)]
    assert abs(len(mine) - len(sent)) <= 2
    assert h.get("link.hz_fast") == pytest.approx(poller.hz, rel=0.02)
    assert h.get("link.reads") == pytest.approx(poller.reads, abs=2)
    # and every value on the screen is what the laptop would show
    last = poller.samples[-1]
    for i, ch in enumerate(amap.channels):
        if ch.name in ("rpm", "map", "tps", "inj", "ign", "o2", "vss"):
            continue                            # the simulated engine moves between the two reads
        assert h.get("value", i) == pytest.approx(last.values[ch.name], rel=1e-6, abs=1e-6), ch.name


def test_engine_only_ever_sends_reads(handheld):
    """Read-only guard, end to end: through faults, a stall, a link loss, an
    unplug and the ISO fallback, every frame on the wire is a read request,
    the wake-up, or a Mode 01 / Mode 03 request."""
    h = handheld(faults=Faults.flaky(seed=4), dtcs=("P0301",))
    h.run_until(20.0)
    h.request_dtc()
    h.run_until(40.0)
    h.model.power_cut()
    h.run_until(50.0)
    h.model.restart()
    h.run_until(60.0)
    h.model.unplug(3.0)
    h.run_until(70.0)
    # On the P2P both protocols run at 10400 baud: tell them apart by content
    framing = protocol.DEFAULT

    def is_honda(frame):
        return frame == framing.init_sequence or (
            len(frame) == 5 and frame[0] == 0x20 and frame[1] == 5 and protocol.verify(frame))
    honda = [(baud, f) for baud, f in h.sent if is_honda(f)]
    iso = [f for baud, f in h.sent if not is_honda(f)]
    assert len(honda) > 1000 and iso
    assert {baud for baud, _ in honda} == {10400}                  # the map's baud, never 9600
    for frame in iso:
        assert len(frame) == 1 or (frame[:3] == b"\x68\x6a\xf1" and frame[3] in (0x01, 0x03)), frame.hex()
    assert h.get("stats.blocked") == 0
    assert h.get("link.state") == 2 and h.get("stats.reconnects") == 1
    assert int(h.get("dtc.n")) == 1 and F.dtc_text(int(h.get("dtc.code", 0))) == "P0301"


def test_engine_survives_a_flaky_line(handheld):
    h = handheld(faults=Faults.flaky(seed=9))
    h.run_until(120.0)
    assert h.get("link.state") == 2
    assert h.get("stats.retries") > 20 and h.get("stats.ok") > 3000
    assert h.get("stall.count") == 0            # a bad line is not a stall
    assert h.get("link.hz_fast") > 10


def test_engine_stall_with_ecu_alive_and_with_power_cut(handheld):
    h = handheld()
    h.run_until(10.0)
    assert h.get("stall.armed") == 1
    h.model.stall()
    h.run_until(11.0)
    assert h.get("stall.count") == 1 and h.text("stall.last_reason") == "rpm dropped to 0"
    h.run_until(25.0)
    assert h.get("stall.triggered") == 0
    h.model.restart()
    h.run_until(40.0)
    assert h.get("stall.armed") == 1
    h.model.power_cut()
    h.run_until(40.9)
    assert h.get("stall.count") == 1
    h.run_until(42.0)
    assert h.get("stall.count") == 2 and h.text("stall.last_reason").startswith("link lost")
    assert h.get("link.state") == 0 and math.isnan(h.get("value", 0))


def test_engine_falls_back_to_iso(handheld):
    h = handheld("iso_after_s = 4\n", honda_protocol=False, dtcs=("P0135", "P1456"))
    h.run_until(3.0)
    assert h.get("link.state") in (0, 1) and h.model.iso_inits == 0
    h.run_until(20.0)
    assert h.model.iso_inits == 1 and h.get("link.state") == 3
    assert h.get("value", 0) == pytest.approx(h.model.rpm, rel=0.05)
    assert int(h.get("dtc.n")) == 2
    assert {F.dtc_text(int(h.get("dtc.code", i))) for i in range(2)} == {"P0135", "P1456"}
    assert h.get("log.recording") == 1
