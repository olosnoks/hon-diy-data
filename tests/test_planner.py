"""Block-read planner and priority scheduler."""

import itertools
from dataclasses import dataclass

import pytest

from hondalog import addressmap, protocol
from hondalog.link.planner import (Block, CostModel, Span, bus_load, plan_blocks,
                                   predict_rates, spans_from_channels)
from hondalog.link.scheduler import Scheduler, rate_class_of, target_hz


@dataclass
class Ch:
    name: str
    addr: int
    length: int = 1
    rate: str = ""
    kind: str = "value"


COST = CostModel()          # 9600 baud, 2 ms turnaround, 2 ms gap


def total(blocks, cost=COST):
    return sum(b.rate_hz * cost.request_s(b.length) for b in blocks)


def test_cost_model_numbers():
    assert COST.byte_s == pytest.approx(1.0417e-3, rel=1e-3)
    # 5 echo + 3 frame bytes + 2 ms + 2 ms = 12.33 ms, worth 11.84 data bytes
    assert COST.overhead_s == pytest.approx(8 * COST.byte_s + 0.004)
    assert COST.overhead_bytes == pytest.approx(11.84, abs=0.01)
    assert COST.request_s(16) == pytest.approx(COST.overhead_s + 16 * COST.byte_s)


def test_spans_keep_multibyte_channels_whole_and_merge_overlaps():
    spans = spans_from_channels([Ch("rpm", 0, 2), Ch("rpm_lo", 1), Ch("ect", 0x10)])
    assert [(s.addr, s.length) for s in spans] == [(0, 2), (0x10, 1)]
    assert spans[0].names == ("rpm", "rpm_lo")


def test_gap_shorter_than_overhead_is_read_through():
    spans = [Span(0, 1), Span(8, 1)]                     # 6 unwanted bytes between
    assert [(b.addr, b.length) for b in plan_blocks(spans, 16, COST)] == [(0, 9)]


def test_gap_longer_than_overhead_splits():
    spans = [Span(0, 1), Span(14, 1)]                    # 13 unwanted bytes > 11.84
    assert [(b.addr, b.length) for b in plan_blocks(spans, 16, COST)] == [(0, 1), (14, 1)]


def test_break_even_moves_with_the_measured_overhead():
    spans = [Span(0, 1), Span(14, 1)]
    slow_ecu = CostModel(turnaround_s=0.010)             # overhead now worth ~19.5 bytes
    assert [(b.addr, b.length) for b in plan_blocks(spans, 16, slow_ecu)] == [(0, 15)]


def test_max_len_is_respected_and_channels_never_split():
    spans = spans_from_channels([Ch(f"c{i}", i * 2, 2) for i in range(20)])
    blocks = plan_blocks(spans, 16, COST)
    assert all(b.length <= 16 for b in blocks)
    covered = {a for b in blocks for a in range(b.addr, b.end)}
    assert covered >= set(range(40))
    for s in spans:                                      # each span inside one block
        assert any(b.addr <= s.addr and s.end <= b.end for b in blocks)


def test_oversize_span_is_chunked():
    blocks = plan_blocks([Span(0, 40)], 16, COST)
    assert [(b.addr, b.length) for b in blocks] == [(0, 16), (16, 16), (32, 8)]


def test_plan_is_optimal_against_brute_force():
    spans = [Span(a, n, r) for a, n, r in
             [(0, 2, 20), (2, 1, 5), (9, 1, 1), (16, 2, 1), (18, 1, 20), (21, 1, 5),
              (30, 1, 1), (33, 2, 20), (47, 1, 1)]]
    best = float("inf")
    for cuts in itertools.product((0, 1), repeat=len(spans) - 1):
        groups, cur = [], [spans[0]]
        for cut, s in zip(cuts, spans[1:]):
            if cut:
                groups.append(cur)
                cur = [s]
            else:
                cur.append(s)
        groups.append(cur)
        if any(g[-1].end - g[0].addr > 16 for g in groups):
            continue
        best = min(best, sum(max(s.rate_hz for s in g) * COST.request_s(g[-1].end - g[0].addr)
                             for g in groups))
    assert total(plan_blocks(spans, 16, COST)) == pytest.approx(best)


def test_riding_along_in_a_fast_block_depends_on_rate_and_distance():
    # A medium byte next to a fast one: 1 byte at 20 Hz beats a request at 5 Hz.
    near = plan_blocks([Span(0x14, 1, 20, ("tps",)), Span(0x15, 1, 5, ("o2",))], 16, COST)
    assert [(b.addr, b.length, b.rate_hz) for b in near] == [(0x14, 2, 20)]
    # A slow byte next to a fast one: its own request once a second is cheaper.
    slow = plan_blocks([Span(0x12, 1, 20, ("map",)), Span(0x13, 1, 1, ("baro",))], 16, COST)
    assert [(b.addr, b.length, b.rate_hz) for b in slow] == [(0x12, 1, 20), (0x13, 1, 1)]
    # ...unless it sits between two fast bytes and comes for free.
    mid = plan_blocks([Span(0x12, 1, 20), Span(0x13, 1, 1), Span(0x14, 1, 20)], 16, COST)
    assert [(b.addr, b.length) for b in mid] == [(0x12, 3)]
    # A medium byte far from the fast one is not worth dragging along at 20 Hz.
    far = plan_blocks([Span(0x12, 1, 20), Span(0x1A, 1, 5)], 16, COST)
    assert [(b.addr, b.length, b.rate_hz) for b in far] == [(0x12, 1, 20), (0x1A, 1, 5)]


def test_default_map_plan():
    amap = addressmap.load_default()
    f = amap.framing
    spans = spans_from_channels(amap.channels, lambda c: target_hz(rate_class_of(c), f))
    blocks = plan_blocks(spans, f.max_read_len, CostModel.from_framing(f))
    names = [n for b in blocks for n in b.channels]
    assert sorted(names) == sorted(amap.names)           # every channel exactly once
    fast = [b for b in blocks if b.rate_hz == f.rate_fast_hz]
    assert {n for b in fast for n in b.channels} >= {"rpm", "map", "tps", "inj", "ign"}
    assert len(fast) == 3 and sum(b.length for b in fast) <= 12
    # uniform rates: never worse than the old greedy sweep (4 requests, 36 bytes)
    flat = plan_blocks(spans_from_channels(amap.channels), f.max_read_len, COST)
    old = [Block(a, n) for a, n in addressmap.plan_blocks(amap.channels, f.max_read_len)]
    assert total(flat) <= total(old) + 1e-12


def test_rate_classes():
    assert rate_class_of(Ch("rpm", 0)) == "fast"
    assert rate_class_of(Ch("o2", 0)) == "medium"
    assert rate_class_of(Ch("ect", 0)) == "slow"
    assert rate_class_of(Ch("mystery", 0)) == "slow"
    assert rate_class_of(Ch("ect", 0, rate="FAST")) == "fast"       # map overrides the name
    with pytest.raises(ValueError):
        rate_class_of(Ch("ect", 0, rate="warp"))
    f = protocol.DEFAULT.with_overrides({"rate_fast_hz": 30})
    assert (target_hz("fast", f), target_hz("medium", f), target_hz("slow", f)) == (30, 5, 1)


def test_address_map_accepts_rate_field(tmp_path):
    path = tmp_path / "map.toml"
    path.write_text('version = "t"\n[[channel]]\nname = "x"\naddr = 0x30\nrate = "fast"\n'
                    '[[channel]]\nname = "y"\naddr = 0x50\n')
    amap = addressmap.load(path)
    assert [rate_class_of(c) for c in amap.channels] == ["fast", "slow"]


# ---- scheduler ----

def run_schedule(blocks, cost, seconds):
    sched = Scheduler(blocks, {n: "x" for b in blocks for n in b.channels})
    t, order = 0.0, []
    while t < seconds:
        i = sched.next(t)
        t += cost.request_s(blocks[i].length)
        sched.done(i, t)
        order.append(i)
    return sched, t, order


def test_scheduler_reads_every_block_first_fastest_first():
    blocks = [Block(0, 1, 1.0, ("slow",)), Block(8, 1, 20.0, ("fast",)), Block(16, 1, 5.0, ("med",))]
    _, _, order = run_schedule(blocks, COST, 0.2)
    assert order[:3] == [1, 2, 0]


def test_scheduler_meets_targets_when_there_is_room():
    blocks = [Block(0, 2, 20.0, ("rpm",)), Block(0x10, 2, 5.0, ("o2",)), Block(0x20, 8, 1.0, ("ect",))]
    assert bus_load(blocks, COST) < 1
    sched, t, _ = run_schedule(blocks, COST, 20)
    report = sched.report(t)
    assert all(r.ok and r.shortfall_hz == 0 for r in report.values())
    assert report["o2"].achieved_hz == pytest.approx(5, rel=0.1)
    assert report["ect"].achieved_hz == pytest.approx(1, rel=0.1)
    assert report["rpm"].achieved_hz > 40            # spare wire time goes to the fast class
    predicted = predict_rates(blocks, COST)
    assert report["rpm"].achieved_hz == pytest.approx(predicted[0], rel=0.05)


def test_scheduler_reports_shortfall_when_overloaded():
    blocks = [Block(i * 16, 16, 20.0, (f"f{i}",)) for i in range(3)] + [Block(0x80, 4, 1.0, ("s",))]
    assert bus_load(blocks, COST) > 1.5
    sched, t, _ = run_schedule(blocks, COST, 20)
    report = sched.report(t)
    assert not report["f0"].ok and report["f0"].shortfall_hz > 5
    assert set(sched.shortfalls(t)) >= {"f0", "f1", "f2"}
    assert report["s"].achieved_hz > 0.5             # the slow block is not starved
    rates = [report[f"f{i}"].achieved_hz for i in range(3)]
    assert max(rates) - min(rates) < 0.5             # the fast blocks share evenly
    assert report["f0"].achieved_hz == pytest.approx(predict_rates(blocks, COST)[0], rel=0.1)


def test_scheduler_failed_block_does_not_hog_the_wire():
    blocks = [Block(0, 1, 20.0, ("a",)), Block(8, 1, 20.0, ("b",))]
    sched = Scheduler(blocks)
    t = 0.0
    picks = []
    for _ in range(20):
        i = sched.next(t)
        picks.append(i)
        t += 0.02
        if i == 0:
            sched.failed(0, t)
        else:
            sched.done(1, t)
    assert picks.count(1) >= 9
    assert sched.report(t)["a"].achieved_hz == 0 and sched.failures[0] > 0
