"""Sweep recording, the correlation finder, 'what changed', typed targets."""

import numpy as np
import pytest

from hondalog import addressmap as am
from hondalog.mapping import correlate, sweeps
from hondalog.mapping.sweeps import Mark, SweepRecorder, SweepRecording
from hondalog.poller import Poller
from hondalog.sim import EcuModel
from hondalog.transport import Link, LoopbackTransport

from mapping_helpers import ram, recording

AMAP = am.load_default()


def driving(n=60):
    """Revs up and down with the throttle; coolant warms; battery wanders."""
    rows = []
    for i in range(n):
        tps = 40 * abs(np.sin(i / 7.0))
        rpm = 800 + 2500 * abs(np.sin(i / 4.3 + 1))
        rows.append(ram(rpm=rpm, tps=tps, map_kpa=30 + rpm / 200, ect=20 + i,
                        batt=14.0 + 0.3 * np.sin(i / 3.0), inj=2.4 + rpm / 1000, noise=i))
    return recording(rows)


# ---- recording --------------------------------------------------------------

def test_recording_decode_window_marks_and_files(tmp_path):
    rec = driving(20)
    rec.marks.append(Mark("blip", 1.0, 2.0))
    assert len(rec) == 20 and rec.t[3] == pytest.approx(0.6)
    assert list(rec.read_addresses()) == list(range(256))
    d = rec.decode(AMAP)
    assert d["rpm"][0] == pytest.approx(800 + 2500 * np.sin(1), abs=0.3) and d["tps"][0] == 0 and d["rpm:t"][5] == pytest.approx(1.0)
    assert rec.mem_at(0)[0x13] == round(106 / 0.716)
    assert len(rec.window(1.0, 2.0)) == 6 and len(rec.marked("blip")) == 6
    assert rec.mark_mask("blip").sum() == 6 * 256
    back = SweepRecording.load(rec.save(tmp_path / "scan"))
    assert np.array_equal(back.mem, rec.mem) and back.marks == rec.marks
    assert back.times == pytest.approx(rec.times)


def test_reads_the_raw_tools_csv_export(tmp_path):
    f = tmp_path / "scan.csv"
    cells = ",".join(str(v) for v in ram(rpm=3000))
    f.write_text("wall_time," + ",".join(f"{a:02X}" for a in range(256)) + "\n"
                 f"2026-11-15T10:00:00.000,{cells}\n2026-11-15T10:00:01.500,{cells}\n"
                 "2026-11-15T10:00:02.000," + "," * 255 + "\n")
    rec = SweepRecording.from_scan_csv(f)
    assert len(rec) == 3 and rec.t.tolist() == [0.0, 1.5, 2.0]
    assert rec.decode(AMAP)["rpm"][:2].tolist() == [3000, 3000]
    assert rec.mem[2].max() == -1
    f.write_text("nope\n")
    with pytest.raises(ValueError):
        SweepRecording.from_scan_csv(f)


def test_recorder_through_the_poller_and_the_simulator():
    """The real path: poller thread -> simulated ECU -> SweepRecorder."""
    model = EcuModel(rev_every_s=0.5, warmup_s=5)
    poller = Poller(LoopbackTransport(model), AMAP)
    poller.start()
    try:
        assert poller.ready.wait(5)
        recorder = SweepRecorder(poller.submit, lo=0x00, hi=0x2F)
        recorder.begin_mark("first")
        rec = recorder.run(sweeps=6)
    finally:
        poller.stop()
    assert len(rec) >= 6 and recorder.error == "" and not recorder.active
    assert list(rec.read_addresses()) == list(range(0x30))
    assert rec.marks[0].label == "first" and rec.marks[0].t1 is not None
    mem, times = rec.data
    assert (np.diff(times[0, ::16][:3]) > 0).all()        # chunks stamped one after another
    assert np.nanmax(rec.decode(AMAP)["rpm"]) > 500
    with pytest.raises(ValueError):
        recorder.run()


def test_recorder_survives_bad_reads_and_stops_on_a_dead_port():
    from concurrent.futures import Future
    from hondalog.protocol import NoReply

    calls = []

    def submit(fn):
        fut = Future()
        calls.append(fn)
        n = len(calls)
        if n == 2:
            fut.set_exception(NoReply("quiet"))
        elif n >= 5:
            fut.set_exception(OSError("unplugged"))
        else:
            class L:
                def read_mem(self, a, k):
                    return bytes([a] * k)
            fut.set_result(fn(L()))
        return fut

    r = SweepRecorder(submit, lo=0, hi=31, chunk=16)
    r.start()
    assert r.pump() == 2                       # this fake answers at once: two sweeps, then dead
    mem, _ = r.recording.data
    assert mem[0, 0] == 0 and mem[0, 16] == -1 and r.failed_reads == 1     # bad chunk kept as unread
    assert mem[1, 16] == 16
    assert not r.active and "unplugged" in r.error and r.pump() == 0


def test_record_link_direct():
    clk = [0.0]
    model = EcuModel(clock=lambda: clk[0])
    tr = LoopbackTransport(model)
    tr.open()
    link = Link(tr, AMAP.framing)
    link.wake()                                 # the P2P answers only after the wake frame
    rec = sweeps.record_link(link, 2, lo=0, hi=0x1F,
                             between=lambda i: clk.__setitem__(0, clk[0] + 1))
    assert len(rec) == 2 and list(rec.read_addresses()) == list(range(32))
    assert sweeps.chunks(0x10, 0x00, 7) == [(0, 7), (7, 7), (14, 3)]
    assert len(sweeps.from_rows([{0: 1}, [1, 2, 3]], [0.0, 1.0])) == 2


# ---- correlation ------------------------------------------------------------

def test_finds_the_hidden_copies_and_gets_width_and_byte_order_right():
    rec = driving()
    top = correlate.rank_against_channel(rec, AMAP, "rpm", top=3)
    best = top[0]
    assert best.candidate == correlate.Candidate(0x90, 2, "little")
    assert best.score > 0.999 and best.candidate.label == "0x90 u16le"
    # fit maps the raw 16-bit value back to rpm: x / 4
    assert best.slope == pytest.approx(0.25, rel=1e-3) and abs(best.offset) < 2
    assert best.formula.startswith("x * 0.25")
    assert best.candidate.channel_fields() == {"addr": 0x90, "length": 2, "endian": "little"}
    assert "score" in str(best)

    tps = correlate.rank_against_channel(rec, AMAP, "tps", top=5)
    assert tps[0].candidate == correlate.Candidate(0xA0)             # a byte, not a word
    assert tps[0].slope == pytest.approx(1 / 2, rel=2e-2)          # (x - 24) / 2
    assert tps[0].offset == pytest.approx(-12, abs=1)
    wide = [m for m in tps if m.candidate.addr in (0x9F, 0xA0) and m.candidate.width == 2]
    assert wide and all(m.score < tps[0].score and m.notes for m in wide)


def test_own_bytes_are_excluded_and_dead_bytes_ignored():
    rec = driving()
    for m in correlate.rank_against_channel(rec, AMAP, "rpm", top=0):
        assert m.candidate.addr not in (0x00, 0x01)
        assert not (m.candidate.width == 2 and m.candidate.addr == 0xFF)
        assert m.span > 0
    with pytest.raises(am.MapError):
        correlate.rank_against_channel(rec, AMAP, "nope")


def test_external_reference_at_its_own_sample_rate():
    """A reference sampled faster than the sweeps (Track B, a typed series)."""
    rec = driving()
    t = np.linspace(0, rec.t[-1], 500)
    volts = 14.0 + 0.3 * np.sin(t / 0.2 / 3.0)
    top = correlate.rank(rec, t, volts, top=1)[0]
    assert top.candidate == correlate.Candidate(0x17)
    assert top.slope == pytest.approx(1 / 10.45, rel=0.05) and top.rmse < 0.1
    assert correlate.rank(rec, [0.0], [1.0]) == []
    assert correlate.rank(SweepRecording(), t, volts) == []


def test_spearman_follows_a_curved_sensor():
    rec = driving()                                       # ECT rises 20 -> 79 through a polynomial
    ref_t, ref_v = rec.t, 20.0 + np.arange(len(rec))
    found = {m.candidate.label: m for m in correlate.rank(rec, ref_t, ref_v, method="spearman",
                                                          top=3)}
    assert abs(found["0x10 u8"].rho) > 0.99 and abs(found["0x10 u8"].r) < 0.995  # curved, still found
    shape = {m.candidate.addr: m for m in correlate.rank_shape(rec, "rise", 0.0, rec.t[-1], top=2)}
    assert set(shape) == {0x10, 0x80}                     # coolant, and a free-running counter
    assert shape[0x10].rho < -0.99 < 0.99 < shape[0x80].rho    # the thermistor count runs down
    assert correlate.rank_shape(rec, "fall", 0.0, rec.t[-1], top=2)[0].candidate.addr in shape
    with pytest.raises(ValueError):
        correlate.rank_shape(rec, "wobble", 0, 1)


def test_inverse_candidates_for_period_style_values():
    rows, rpm = [], []
    for i in range(40):
        speed = 1000 + 100 * i
        period = int(1875000 / speed)                      # OBD1 style: the ECU stores a period
        r = ram(noise=i * 37)
        r[0xC0], r[0xC1] = period >> 8, period & 0xFF
        rows.append(r)
        rpm.append(speed)
    rec = recording(rows)
    best = correlate.rank(rec, rec.t, rpm, inverse=True, top=1)[0]
    assert best.candidate == correlate.Candidate(0xC0, 2, "big", True)
    assert best.slope == pytest.approx(1875000, rel=0.01) and " / x" in best.formula
    plain = correlate.rank(rec, rec.t, rpm, top=1)[0]
    assert abs(plain.r) < abs(best.r)


def test_jitter_is_scored_down():
    rows = []
    for i in range(40):
        r = ram(tps=i, noise=0)
        r[0xD0] = 100 + (i > 20)                           # one count of movement
        rows.append(r)
    rec = recording(rows)
    ms = {m.candidate.label: m for m in correlate.rank(rec, rec.t, np.arange(40.0), top=0)}
    assert ms["0xD0 u8"].score < 0.5 and "barely moves" in ms["0xD0 u8"].notes[0]
    assert ms["0x14 u8"].score > 0.99


# ---- typed targets ----------------------------------------------------------

def test_find_value_for_a_typed_reading():
    rec = recording([ram(rpm=3000, batt=13.97, noise=i) for i in range(8)])
    hits = correlate.find_value(rec, 3000, "x / 4", rel_tol=0.01)
    assert {(h.candidate.addr, h.candidate.width, h.candidate.endian) for h in hits} == \
        {(0x00, 2, "big"), (0x90, 2, "little")}
    assert hits[0].error == 0 and "reads 3000" in str(hits[0])
    volts = correlate.find_value(rec, 13.9, "x / 10.45", tol=0.1, rel_tol=0, widths=(1,))
    assert [h.candidate.addr for h in volts] == [0x17]
    with pytest.raises(am.MapError):
        correlate.find_value(rec, 1, "x +")


# ---- bits -------------------------------------------------------------------

def ac_recording():
    rows = [ram(rpm=800, ac=int(10 <= i < 20), noise=i % 4) for i in range(30)]
    return recording(rows, marks=[Mark("ac_on", 2.0, 3.9)])


def test_what_changed_finds_the_bits_that_follow_the_action():
    rec = ac_recording()
    found = correlate.changed_bits(rec, "ac_on")
    assert [(c.addr, c.bit, c.outside, c.inside) for c in found] == [(0x08, 1, 0, 1),
                                                                     (0x0B, 1, 0, 1),
                                                                     (0xB0, 4, 0, 1)]
    assert found[0].purity == 1.0 and found[0].formula == "bit(x, 1)"
    assert "0x08 bit 1: 0 -> 1" in str(found[0])
    assert correlate.changed_bytes(rec, "ac_on") == [0x08, 0x0B, 0xB0]    # switch, clutch, copy
    table = correlate.flag_channel_table("ac2", found[2], "A/C (copy)")
    assert am.Channel(**table).convert(bytes([0x90])) == 1
    assert correlate.changed_bits(rec, "no such mark") == []
    assert correlate.changed_bits(SweepRecording(), "x") == []


def test_what_changed_ignores_free_running_bits_and_tolerates_sloppy_timing():
    rows = [ram(ac=int(11 <= i < 21), noise=i) for i in range(30)]     # switch lags the mark
    rec = recording(rows, marks=[Mark("ac_on", 2.0, 3.9)])
    assert correlate.changed_bits(rec, "ac_on", min_purity=0.99) == []
    assert [(c.addr, c.bit) for c in correlate.changed_bits(rec, "ac_on", guard_s=0.25)] == \
        [(0x08, 1), (0xB0, 4)]
    inverted = recording([ram(mil=int(not 10 <= i < 20)) for i in range(30)],
                         marks=[Mark("m", 2.0, 3.9)])
    (c,) = correlate.changed_bits(inverted, "m")
    assert (c.inside, c.formula) == (0, "1 - bit(x, 5)")
    steps = correlate.rank_steps(ac_recording(), "ac_on", min_span=0, top=3)
    assert {m.candidate.addr for m in steps} == {0x08, 0x0B, 0xB0}


def test_bit_history():
    t, bits = correlate.bit_history(ac_recording(), 0x08)
    assert bits.shape == (30, 8) and bits[:, 1].tolist() == [0] * 10 + [1] * 10 + [0] * 10
    assert bits[:, 2].tolist() == [1] * 30                   # the PSP switch, at rest
    assert t[0] == 0.0 and bits[:, 0].sum() == 0
