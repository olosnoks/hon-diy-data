"""Benchmark for the expression evaluator, math channels and alarms.

Budget (W3 brief): 30 math channels evaluated at 20 Hz must cost under 1% of
one CPU core, i.e. under 500 microseconds per sample for the whole set.

    python3 -m hondalog.bench.expr_bench            # prints a table
    python3 -m hondalog.bench.expr_bench --json     # machine-readable

``measure()`` is what the test suite calls.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from dataclasses import asdict, dataclass

import numpy as np

from .. import addressmap
from ..alarms import AlarmEngine, AlarmRule
from ..expr import compile_expr
from ..mathch import MathChannel, MathEngine

POLL_HZ = 20.0
BUDGET_CPU = 0.01                    # 1% of one core
N_CHANNELS = 30

# 30 expressions in the style people actually write: the shipped ones, some
# filters, a few conditionals and a couple that chain other math channels.
EXPRESSIONS = [
    "[inj] * [rpm] / 1200",
    "[inj_duty] / 100 * 240 * 4 * 60 / 1000",
    "[stft] + [ltft]",
    "slope([rpm], 500)",
    "[map] / [baro] * 100",
    "clamp(([map] * 1.59 / 4 * 0.85 / (0.287 * ([iat] + 273.15))) / ([inj] / 60000 * 240 * 0.745), 5, 30)",
    "smooth([rpm], 5)",
    "avg([map], 500)",
    "minw([batt], 5000)",
    "maxw([ect], 10000)",
    "ema([o2], 300)",
    "median([tps], 5)",
    "[rpm] - [rpm-1]",
    "rate([tps])",
    "[ect] * 1.8 + 32",
    "[map] * 0.1450377",
    "[vss] * 0.621371",
    "[rpm] > 400 ? [batt] : 0",
    "[tps] > 60 && [rpm] > 3000 ? 1 : 0",
    "abs([m03]) > 15",
    "poly([ect], 1.0, 0.01, 0.0002)",
    "table([rpm], 0, 0, 1000, 12, 3000, 28, 7000, 34)",
    "stdev([rpm], 1000)",
    "held([o2] < 0.2, 500)",
    "integ([m01]) / 3600",
    "[m04] / 60",
    "sqrt([map] * [rpm]) / 10",
    "max([ign], [ign_limit]) - min([ign], [ign_limit])",
    "valid([knock]) ? [knock] * 51 : -1",
    "avg([m04], 1000) - [m12]",
]
assert len(EXPRESSIONS) == N_CHANNELS


@dataclass
class Result:
    name: str
    us_per_sample: float             # microseconds for the whole set, one sample
    cpu_at_20hz: float               # fraction of one core at POLL_HZ
    budget_us: float

    @property
    def ok(self) -> bool:
        return self.us_per_sample <= self.budget_us


def _engine() -> tuple[MathEngine, list[str]]:
    amap = addressmap.load_default()
    names = [f"m{i:02d}" for i in range(N_CHANNELS)]
    channels = []
    for i, text in enumerate(EXPRESSIONS):
        text = text.replace("[inj_duty]", "[m00]")
        channels.append(MathChannel(names[i], text))
    return MathEngine(channels, amap.names), amap.names


def _samples(names: list[str], n: int, seed: int = 7) -> list[dict[str, float | None]]:
    rnd = random.Random(seed)
    base = {"rpm": 2500.0, "map": 45.0, "baro": 101.0, "iat": 28.0, "ect": 88.0, "inj": 3.1,
            "batt": 14.1, "tps": 12.0, "o2": 0.45, "stft": 2.0, "ltft": -1.5, "vss": 40.0,
            "ign": 24.0, "ign_limit": 30.0, "knock": 0.1}
    out: list[dict[str, float | None]] = []
    for i in range(n):
        row: dict[str, float | None] = {}
        for name in names:
            centre = base.get(name, 1.0)
            row[name] = centre * (1 + 0.2 * math.sin(i * 0.05 + len(name)) + rnd.uniform(-.02, .02))
        out.append(row)
    return out


def _best(fn, samples, repeats: int) -> float:
    """Best-of-N microseconds per sample (least disturbed by other processes)."""
    best = float("inf")
    for _ in range(repeats):
        start = time.perf_counter()
        fn(samples)
        best = min(best, time.perf_counter() - start)
    return best / len(samples) * 1e6


def measure(n: int = 4000, repeats: int = 5) -> list[Result]:
    budget_us = BUDGET_CPU / POLL_HZ * 1e6
    engine, names = _engine()
    samples = _samples(names, n)
    dt = 1.0 / POLL_HZ
    results: list[Result] = []

    def add(name: str, us: float) -> None:
        results.append(Result(name, us, us * 1e-6 * POLL_HZ, budget_us))

    def run_math(rows):
        update = engine.update
        for i, row in enumerate(rows):
            update(row, i * dt)
    add(f"math: {N_CHANNELS} channels, one sample", _best(run_math, samples, repeats))

    amap = addressmap.load_default()
    shipped_math, shipped_alarms = amap.math_engine(), amap.alarm_engine()

    def run_shipped(rows):
        for i, row in enumerate(rows):
            t = i * dt
            shipped_alarms.update({**row, **shipped_math.update(row, t)}, t)
    add(f"shipped map: {len(shipped_math)} math + {len(shipped_alarms)} alarms",
        _best(run_shipped, samples, repeats))

    rules = [AlarmRule(f"a{i}", channel=name, high=1e9, low=-1e9, hold_s=1, hysteresis=1)
             for i, name in enumerate(names[:20])]
    rules += [AlarmRule(f"r{i}", channel=name, rate_high=1e9) for i, name in enumerate(names[:5])]
    rules += [AlarmRule(f"w{i}", when=f"[{name}] > 1e9 && [rpm] > 400", gate="[rpm] > 100")
              for i, name in enumerate(names[:5])]
    alarms = AlarmEngine(rules, names)

    def run_alarms(rows):
        update = alarms.update
        for i, row in enumerate(rows):
            update(row, i * dt)
    add(f"alarms: {len(rules)} rules, one sample", _best(run_alarms, samples, repeats))

    def run_full(rows):
        for i, row in enumerate(rows):
            t = i * dt
            alarms.update(row, t)
            engine.update(row, t)
    add(f"math {N_CHANNELS} + alarms {len(rules)} together", _best(run_full, samples, repeats))

    def run_decode(rows):
        mem = {a: (a * 37 + 11) & 0xFF for a in range(256)}
        decode = amap.decode
        for _ in rows:
            decode(mem)
    add(f"decode: {len(amap.channels)} formulas, one sweep", _best(run_decode, samples, repeats))

    simple = compile_expr("[inj] * [rpm] / 1200")
    add("one simple expression", _best(lambda rows: [simple.eval(r, 0.0) for r in rows],
                                       samples, repeats))
    return results


def measure_vector(rows: int = 72000, repeats: int = 3) -> dict[str, float]:
    """Whole-log evaluation: a one-hour log at 20 Hz. Returns milliseconds."""
    rnd = np.random.default_rng(5)
    t = np.arange(rows) / POLL_HZ
    cols = {name: rnd.uniform(0, 100, rows) for name in ("ect", "tps", "rpm", "map", "inj")}
    out: dict[str, float] = {}
    cases = {
        "filter: ECT > 80 and steady throttle": "[ect] > 80 && abs([tps] - [tps-1]) < 5 && [rpm] > 400",
        "pure arithmetic": "[inj] * [rpm] / 1200",
        "windowed: avg over 500 ms": "avg([map], 500)",
    }
    for label, text in cases.items():
        expr = compile_expr(text)
        best = float("inf")
        for _ in range(repeats):
            start = time.perf_counter()
            expr.eval_array(cols, t)
            best = min(best, time.perf_counter() - start)
        out[label] = best * 1e3
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-n", type=int, default=4000, help="samples per run")
    args = ap.parse_args(argv)
    results = measure(args.n)
    vec = measure_vector()
    if args.json:
        print(json.dumps({"live": [asdict(r) for r in results], "whole_log_ms": vec}, indent=2))
    else:
        print(f"{'live, per sample':48} {'us':>9} {'CPU @ 20 Hz':>12}   budget {results[0].budget_us:.0f} us")
        for r in results:
            print(f"{r.name:48} {r.us_per_sample:9.1f} {r.cpu_at_20hz * 100:11.3f}%   "
                  f"{'ok' if r.ok else 'OVER BUDGET'}")
        print(f"\n{'whole log, 1 hour at 20 Hz (72000 rows)':48} {'ms':>9}")
        for label, ms in vec.items():
            print(f"{label:48} {ms:9.1f}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
