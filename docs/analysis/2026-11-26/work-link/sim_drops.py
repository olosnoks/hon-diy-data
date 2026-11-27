"""Replay drive 2045's link faults on the simulator and count what the poller
and the stall rules make of them.

    python sim_drops.py <checkout> [seconds] [seed]

<checkout> is the source tree to test (the main checkout = "before", the
link-dropout-fix worktree = "after"). Virtual time, FTDI latency timer 16 ms,
10400 baud, the drive-2045 read plan. Fault rates are fitted to drive 2045:
  garble  1.7 % of requests: our request is damaged on the wire (bad echo, ECU silent)
  single  0.4 % of requests: the ECU ignores one good request
  deaf    0.15 % of requests: the ECU ignores everything for 0.2-0.45 s
          (15 % of those for 0.6-1.2 s)
The faults are injected here, not with the checkout's own sim faults, so both
trees see exactly the same wire.
"""
import sys, time, random, statistics as st
root = sys.argv[1]
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 1715
SEED = int(sys.argv[3]) if len(sys.argv) > 3 else 1
sys.path.insert(0, root)
from hondalog import addressmap                                   # noqa: E402
from hondalog.link.clock import VirtualClock                      # noqa: E402
from hondalog.poller import Poller                                # noqa: E402
from hondalog.sim import EcuModel                                 # noqa: E402
from hondalog.transport import LoopbackTransport                  # noqa: E402
from hondalog.triggers import TriggerEngine, stall_rules          # noqa: E402


class FieldEcu(EcuModel):
    def __init__(self, *a, garble=0.017, single=0.004, deaf=0.0015, **k):
        super().__init__(*a, **k)
        self.r = random.Random(SEED)
        self.garble, self.single, self.deaf = garble, single, deaf
        self.deaf_until = 0.0
        self._garbled = False
        self.n = {"garble": 0, "single": 0, "deaf": 0}

    def echo_of(self, data):
        self._garbled = False
        if len(data) == 5 and self.r.random() < self.garble:
            i = self.r.randrange(5)
            self._garbled = True
            self.n["garble"] += 1
            return data[:i] + bytes([data[i] ^ (1 << self.r.randrange(8))]) + data[i + 1:]
        return data

    def on_bytes(self, data, baud, now):
        if len(data) == 5 and data[0] == 0x20:
            if self._garbled:
                return []
            if now < self.deaf_until:
                return []
            if self.r.random() < self.deaf:
                long = self.r.random() < 0.15
                self.deaf_until = now + (self.r.uniform(0.6, 1.2) if long else self.r.uniform(0.2, 0.45))
                self.n["deaf"] += 1
                return []
            if self.r.random() < self.single:
                self.n["single"] += 1
                return []
        return super().on_bytes(data, baud, now)


amap = addressmap.load_default()
plan = ["00+3", "03+15", "12+4", "16+10", "20+7", "27+14", "35+8"]
clock = VirtualClock()
model = FieldEcu(framing=amap.framing, clock=clock.now, rev_every_s=1e9, seed=SEED,
                 turnaround_s=0.002, warmup_s=0)
transport = LoopbackTransport(model, clock, byte_timing=True, usb_latency_s=0.016)
poller = Poller(transport, amap, clock=clock)
try:                                    # read every address like drive 2045
    from hondalog.link.planner import Block
    poller.plan  # noqa
except Exception:
    pass
eng = TriggerEngine(stall_rules())
rpm_t, fired, down = [], [], []
state = {"link": None}


def on_block(read):
    if "rpm" in read.values:
        rpm_t.append(read.t)
    f, _ = eng.feed(read.t, {**read.values, "link": 1})
    fired.extend(f)
    state["link"] = True


def on_sample(s):
    if not s.link:
        if state["link"] is not False:
            down.append(s.t)
        state["link"] = False
        f, _ = eng.feed(s.t, {"link": 0, "rpm": None})
        fired.extend(f)


poller.on_block, poller.on_sample = on_block, on_sample
wall = time.time()
poller.start()
t0 = clock.now()
while clock.now() - t0 < SECONDS:
    time.sleep(0.01)
poller.stop()
el = clock.now() - t0
gaps = sorted(b - a for a, b in zip(rpm_t, rpm_t[1:]) if a > 2)
s = poller.link.stats
print(f"{root}: {el:.0f} s simulated in {time.time() - wall:.0f} s; injected {model.n}")
print(f"  requests {s.requests} ok {s.ok} timeouts {s.timeouts} bad_frames {s.bad_frames} "
      f"echo {s.echo_mismatches} retries {s.retries} wakes {s.wakes}")
print(f"  link-down episodes {len(down)} ({len(down) / el * 60:.1f}/min)")
print(f"  rpm reads {len(rpm_t)} = {len(rpm_t) / el:.2f} Hz; gaps >0.3 s: {sum(g > 0.3 for g in gaps)}, "
      f">1 s: {sum(g > 1 for g in gaps)}, worst {gaps[-1]:.2f} s; time in gaps >0.15 s: "
      f"{sum(g for g in gaps if g > 0.15):.1f} s")
print(f"  stall rule firings: {[(f.rule.name, round(f.t, 1)) for f in fired]}")
