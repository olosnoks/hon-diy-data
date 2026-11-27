#!/usr/bin/env python3
"""Link benchmark: per-channel update rate, before and after the planner and
scheduler, against the simulated ECU at true 9600-baud byte timing.

    python3 tools/bench_link.py                    # 30 s of simulated time, instant
    python3 tools/bench_link.py --realtime -s 10   # same on the wall clock
    python3 tools/bench_link.py --turnaround-ms 8 --usb-latency-ms 16
    python3 tools/bench_link.py --faults flaky
    python3 tools/bench_link.py --faults p2p --usb-latency-ms 16   # the car on 2026-11-26

"before" is the v0.1.0 strategy, reproduced here line for line: sweep every
block of the greedy plan in turn, flush, write, read in 20 ms slices, sleep
2 ms, one row per sweep. "after" is the real Poller. Both talk to the same
wire model: every byte (the echo too) takes 10 bit times, the ECU answers
``--turnaround-ms`` after the last request byte, and the adapter hands bytes
over on ``--usb-latency-ms`` ticks like an FTDI latency timer.

By default the run is on virtual time: nothing sleeps, the numbers are exact
for the wire model and identical on every run. ``--realtime`` runs the same
thing on the wall clock, which adds this machine's scheduling jitter.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from hondalog import addressmap, protocol                     # noqa: E402
from hondalog.link.clock import REAL, Clock, VirtualClock     # noqa: E402
from hondalog.link.planner import CostModel                   # noqa: E402
from hondalog.link.scheduler import rate_class_of             # noqa: E402
from hondalog.poller import BlockRead, Poller                 # noqa: E402
from hondalog.sim import EcuModel, Faults                     # noqa: E402
from hondalog.transport import Link, LoopbackTransport        # noqa: E402


def make_rig(args: argparse.Namespace, amap: addressmap.AddressMap) -> tuple[Clock, EcuModel, LoopbackTransport]:
    clock: Clock = REAL if args.realtime else VirtualClock()
    faults = Faults.named(args.faults, args.seed)
    faults.seed = args.seed
    model = EcuModel(framing=amap.framing, clock=clock.now, rev_every_s=5, seed=args.seed,
                     faults=faults, turnaround_s=args.turnaround_ms / 1000,
                     need_wake=False)   # the P2P map runs at 10400; the wake frame is not what is measured
    transport = LoopbackTransport(model, clock, byte_timing=True,
                                  usb_latency_s=args.usb_latency_ms / 1000)
    return clock, model, transport


# ---- v0.1.0, as it was ------------------------------------------------------

def legacy_read_mem(transport: LoopbackTransport, clock: Clock, f: protocol.Framing,
                    addr: int, length: int) -> bytes:
    request = protocol.build_read_request(addr, length, f)
    want = len(request) + protocol.reply_size(length, f)
    transport.flush_input()
    transport.write(request)
    deadline = clock.now() + want * 10.0 / f.baud + f.reply_timeout_s
    buf = bytearray()
    while True:
        remaining = deadline - clock.now()
        if remaining <= 0:
            break
        buf += transport.read(max(1, want - len(buf)), min(remaining, 0.02))
        if len(buf) >= protocol.reply_size(length, f):
            rest, _ = protocol.strip_echo(buf, request)
            if protocol.find_reply(rest, length, f) is not None:
                break
    return protocol.parse_reply(bytes(buf), request, length, f)


def run_before(args: argparse.Namespace, amap: addressmap.AddressMap) -> dict:
    clock, model, transport = make_rig(args, amap)
    f = amap.framing
    blocks = addressmap.plan_blocks(amap.channels, f.max_read_len)
    counts = {name: 0 for name in amap.names}
    reads = errors = sweeps = 0
    t0 = clock.now()
    while clock.now() - t0 < args.seconds:
        for addr, length in blocks:
            try:
                legacy_read_mem(transport, clock, f, addr, length)
            except protocol.ProtocolError:
                errors += 1
                continue
            reads += 1
            clock.sleep(f.inter_request_delay_s)
            for ch in amap.channels:
                if addr <= ch.addr and ch.addr + ch.length <= addr + length:
                    counts[ch.name] += 1
        sweeps += 1
    elapsed = clock.now() - t0
    return {"blocks": blocks, "reads": reads, "errors": errors, "elapsed": elapsed,
            "hz": {n: c / elapsed for n, c in counts.items()}, "sample_hz": sweeps / elapsed,
            "worst_gap": {}}


# ---- now ---------------------------------------------------------------------

def run_after(args: argparse.Namespace, amap: addressmap.AddressMap) -> dict:
    clock, model, transport = make_rig(args, amap)
    poller = Poller(transport, amap, clock=clock)
    stamps: dict[str, list[float]] = {name: [] for name in amap.names}

    def on_block(read: BlockRead) -> None:
        for name in read.values:
            stamps[name].append(read.t)

    poller.on_block = on_block
    t0 = clock.now()
    poller.start()
    while clock.now() - t0 < args.seconds:
        time.sleep(0.002)
    poller.stop()
    elapsed = clock.now() - t0
    samples = [s for s in poller.samples if s.link]
    drops = sum(1 for a, b in zip(poller.samples, list(poller.samples)[1:]) if a.link and not b.link)
    return {"drops": drops, "blocks": poller.blocks, "reads": poller.reads, "errors": poller.errors,
            "elapsed": elapsed,
            "hz": {n: len(ts) / elapsed for n, ts in stamps.items()},
            # worst gap once running (the first second is the priming pass)
            "worst_gap": {n: max((b - a for a, b in zip(ts, ts[1:]) if a > 1.0), default=0.0)
                          for n, ts in stamps.items()},
            "sample_hz": len(samples) / elapsed, "stats": poller.link.stats.as_dict(),
            "timing": poller.link.timing.as_dict(), "bus_load": poller.bus_load,
            "predicted": poller.predicted_rates(),
            "targets": {n: r.target_hz for n, r in poller.channel_rates().items()},
            "cost": poller.cost}


def run_burst(args: argparse.Namespace, amap: addressmap.AddressMap, name: str = "rpm") -> float:
    clock, model, transport = make_rig(args, amap)
    link = Link(transport, amap.framing, clock)
    ch = amap.channel(name)
    assert ch is not None
    n, t0 = 0, clock.now()
    while clock.now() - t0 < min(args.seconds, 5.0):
        try:
            link.read_block(ch.addr, ch.length)
            n += 1
        except protocol.ProtocolError:
            pass
    return n / (clock.now() - t0)


def measure_cost(args: argparse.Namespace, amap: addressmap.AddressMap) -> dict:
    """Time requests of every length and fit  t = overhead + length * byte."""
    clock, model, transport = make_rig(args, amap)
    model.faults = Faults()
    link = Link(transport, amap.framing, clock)
    for _ in range(20):
        link.read_block(0, 8)                       # let the timeouts settle
    xs, ys = [], []
    for length in range(1, amap.framing.max_read_len + 1):
        times = []
        last_done = link.read_block(0, length).t_done
        for _ in range(40):
            r = link.read_block(0, length)
            times.append(r.t_done - last_done)      # full period, gap included
            last_done = r.t_done
        xs.append(length)
        ys.append(statistics.median(times))
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    intercept = my - slope * mx
    model_cost = CostModel.from_framing(amap.framing, turnaround_s=link.timing.overhead_s,
                                        gap_s=link.timing.gap)
    worst = max(abs(model_cost.request_s(x) - y) for x, y in zip(xs, ys))
    return {"byte_ms": slope * 1000, "overhead_ms": intercept * 1000,
            "model_byte_ms": model_cost.byte_s * 1000, "model_overhead_ms": model_cost.overhead_s * 1000,
            "overhead_bytes": intercept / slope, "worst_error_ms": worst * 1000,
            "turnaround_ms": link.timing.overhead_s * 1000}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-s", "--seconds", type=float, default=30.0, help="length of each run (default 30)")
    ap.add_argument("--realtime", action="store_true", help="run on the wall clock instead of virtual time")
    ap.add_argument("--turnaround-ms", type=float, default=2.0, help="ECU think time per request (default 2)")
    ap.add_argument("--usb-latency-ms", type=float, default=1.0, help="FTDI latency timer (default 1)")
    ap.add_argument("--faults", default="", help="fault spec for the simulator, or 'flaky' / 'p2p'")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--map", help="address map to use (default: the shipped one)")
    ap.add_argument("--json", action="store_true", help="print the results as JSON")
    args = ap.parse_args(argv)

    amap = addressmap.load(args.map) if args.map else addressmap.load_default()
    cost = measure_cost(args, amap)
    before = run_before(args, amap)
    after = run_after(args, amap)
    burst = run_burst(args, amap)
    if args.json:
        after_json = {k: v for k, v in after.items() if k != "cost"}
        print(json.dumps({"cost": cost, "before": before, "after": after_json, "burst_rpm_hz": burst},
                         indent=1, default=str))
        return 0

    mode = "wall clock" if args.realtime else "virtual time, exact for the wire model"
    print(f"hondalog link benchmark: {args.seconds:g} s per run, {mode}")
    print(f"wire: 9600 baud 8N1, ECU turnaround {args.turnaround_ms:g} ms, "
          f"USB latency timer {args.usb_latency_ms:g} ms, faults: {args.faults or 'none'}\n")
    print("Per-request cost (measured on the sim, fitted over lengths 1-16):")
    print(f"  measured  {cost['overhead_ms']:.2f} ms + {cost['byte_ms']:.3f} ms per data byte"
          f"   (overhead is worth {cost['overhead_bytes']:.1f} data bytes)")
    print(f"  model     {cost['model_overhead_ms']:.2f} ms + {cost['model_byte_ms']:.3f} ms per data byte"
          f"   (worst error {cost['worst_error_ms']:.2f} ms)")
    print("  model = 5 echo bytes + 3 header/checksum bytes + measured turnaround "
          f"({cost['turnaround_ms']:.2f} ms) + inter-request gap\n")

    def fmt_blocks(blocks: list) -> str:
        return " ".join(f"{a:02X}+{n}" for a, n in blocks)

    print(f"before: {len(before['blocks'])} requests per sweep: {fmt_blocks(before['blocks'])}")
    print(f"after:  {len(after['blocks'])} blocks, scheduled by rate class: {fmt_blocks(after['blocks'])}")
    print(f"        bus load at target rates: {after['bus_load']:.2f} "
          f"({'targets fit' if after['bus_load'] <= 1 else 'more than the wire carries: fast class falls short'})\n")
    by_name = {c.name: c for c in amap.channels}
    print(f"{'channel':<12} {'class':<7} {'target':>7} {'before':>8} {'after':>8} {'change':>7} "
          f"{'worst gap':>10}  {'vs target'}")
    order = {"fast": 0, "medium": 1, "slow": 2}
    for name in sorted(amap.names, key=lambda n: (order[rate_class_of(by_name[n])], by_name[n].addr)):
        klass = rate_class_of(by_name[name])
        target = after["targets"].get(name, 0.0)
        b, a = before["hz"][name], after["hz"][name]
        verdict = "ok" if a >= 0.95 * target else f"short by {target - a:.1f} Hz"
        print(f"{name:<12} {klass:<7} {target:>5.0f}Hz {b:>6.1f}Hz {a:>6.1f}Hz {a / b if b else 0:>6.2f}x "
              f"{after['worst_gap'][name] * 1000:>7.0f} ms  {verdict}")
    print(f"\nrows per second   before {before['sample_hz']:.1f} (one per sweep)   "
          f"after {after['sample_hz']:.1f} (one per fast cycle, per-channel timestamps)")
    print(f"requests          before {before['reads']} ok, {before['errors']} failed   "
          f"after {after['reads']} ok, {after['errors']} failed")
    s = after["stats"]
    print(f"after, counters   timeouts {s['timeouts']}  checksum {s['checksum_failures']}  "
          f"bad frames {s['bad_frames']}  echo mismatches {s['echo_mismatches']}  "
          f"resyncs {s['resyncs']}  retries {s['retries']}  wakes {s['wakes']}")
    print(f"after, link drops {after['drops']} (link reported down), "
          f"worst rpm gap {after['worst_gap'].get('rpm', 0) * 1000:.0f} ms")
    t = after["timing"]
    print(f"after, timing     response timeout {t['response_timeout_ms']:.0f} ms  "
          f"inter-byte timeout {t['inter_byte_timeout_ms']:.1f} ms  gap {t['gap_ms']:.1f} ms")
    print(f"burst, rpm only   {burst:.1f} Hz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
