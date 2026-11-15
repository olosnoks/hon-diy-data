#!/usr/bin/env python3
"""Benchmarks for the data layer. Run from the repo root:

    python3 bench/bench_data.py            # 1-hour synthetic log
    python3 bench/bench_data.py --hours 3

Prints one line per measurement. The budgets from docs/research/FEATURES.md:
open a 1-hour log in under 2 s, filter toggle under 200 ms.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hondalog.analysis import Filter, FilterSet, ReplaySession, TableHistogram, histogram  # noqa: E402
from hondalog.data import Column, LogWriter, open_log, read_hlog                            # noqa: E402
from hondalog.data.synth import write_synthetic                                            # noqa: E402
from hondalog.export import export_log                                                     # noqa: E402


def timed(fn, repeat: int = 5):
    """(best seconds, last result)."""
    best, result = float("inf"), None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - start)
    return best, result


def report(name: str, value: str, note: str = "") -> None:
    print(f"{name:<46} {value:>16}   {note}")


def bench_write(folder: Path) -> None:
    cols = [Column(f"c{i}") for i in range(12)]
    values = [float(i) for i in range(12)]
    raw = bytes(range(16))
    for label, fsync in (("fsync every 0.5 s", True), ("no fsync", False)):
        path = folder / f"write_{fsync}.hlog"
        writer = LogWriter(path, {"bench": True}, fsync=fsync)
        writer.add_stream("ecu/00+16", cols, raw_len=16)
        n = 200_000
        gaps = []
        start = time.perf_counter()
        last = start
        for i in range(n):
            writer.append("ecu/00+16", i * 0.001, values, raw)
            if i % 64 == 0:
                now = time.perf_counter()
                gaps.append((now - last) / 64)
                last = now
        took = time.perf_counter() - start
        writer.close()
        size = path.stat().st_size
        report(f"append, 12 values + 16 raw bytes ({label})", f"{n / took / 1000:.0f} k rows/s",
               f"{size / took / 1e6:.1f} MB/s; per call median {statistics.median(gaps) * 1e6:.1f} us, "
               f"worst 64-call mean {max(gaps) * 1e6:.0f} us")
    # the real load: ~50 K-line reads/s plus 50 Hz black box frames
    need = 50 + 50
    report("rows/s a car session actually produces", f"{need} rows/s", "K-line reads + 50 Hz black box frames")

    path = folder / "bulk.hlog"
    rows = np.zeros(1_000_000, dtype=[("t", "<f8")] + [(f"c{i}", "<f4") for i in range(12)])
    rows["t"] = np.arange(len(rows)) / 1000.0
    start = time.perf_counter()
    with LogWriter(path, background=False) as writer:
        writer.add_stream("blackbox/rails", cols)
        for chunk in np.array_split(rows, 1000):            # 1 s of 1 kHz frames per call
            writer.append_array("blackbox/rails", chunk)
            writer.flush()
    took = time.perf_counter() - start
    report("append_array, 1 kHz x 12 columns, 1000 s", f"{len(rows) / took / 1e6:.1f} M rows/s",
           f"{path.stat().st_size / took / 1e6:.0f} MB/s")


def bench_hour(folder: Path, hours: float) -> None:
    path = folder / "hour.hlog"
    start = time.perf_counter()
    write_synthetic(path, duration_s=3600 * hours)
    made = time.perf_counter() - start
    size = path.stat().st_size
    with read_hlog(path) as log:
        rows = log.rows
        ecu = sum(s.rows * s.info.dtype.itemsize for s in log.streams.values() if s.info.source == "ecu")
    report(f"{hours:g} h synthetic log", f"{size / 1e6:.1f} MB",
           f"{rows} rows in 5 streams, written in {made:.2f} s; K-line part {ecu / 1e6:.1f} MB")

    took, _ = timed(lambda: read_hlog(path).close())
    report("open (index, metadata, events)", f"{took * 1000:.1f} ms", "budget 2000 ms")

    def open_and_load():
        with open_log(path) as log:
            for name in log.channels:
                log.series(name)
            return log.rows
    took, _ = timed(open_and_load, 3)
    report("open + load every channel of every stream", f"{took * 1000:.0f} ms", "budget 2000 ms")

    def open_and_plot():
        with open_log(path) as log:
            session = ReplaySession(log)
            return [session.trace(n, 1366) for n in ("rpm", "map", "tps", "o2", "inj", "ign", "ect", "batt")]
    took, _ = timed(open_and_plot, 3)
    report("open + 8 traces ready to draw at 1366 px", f"{took * 1000:.0f} ms", "what the replay tab needs")

    crashed = folder / "crashed.hlog"
    blob = path.read_bytes()
    crashed.write_bytes(blob[:-5000])                       # no index, torn tail
    took, _ = timed(lambda: read_hlog(crashed).close(), 3)
    report("open after a crash (no index: full scan)", f"{took * 1000:.0f} ms", "checks every CRC")

    log = open_log(path).load_all()
    took, table = timed(lambda: log.table(["rpm", "map", "inj", "ect", "closed_loop", "tps"], base="rpm"))
    report("align 6 channels on the rpm grid", f"{took * 1000:.1f} ms", f"{len(table)} rows")
    took, hist = timed(lambda: TableHistogram.from_table(table, "rpm", "map", "inj", histogram.RPM_250,
                                                         histogram.MAP_KPA_5))
    report("histogram RPM x MAP of inj (29 x 19 cells)", f"{took * 1000:.2f} ms", f"{hist.samples} samples")

    filters = FilterSet([Filter("warm", "ect > 80"), Filter("closed loop", "closed_loop"),
                         Filter("steady", "abs(rate(tps)) < 5")])
    filters.mask(table)

    def toggle():
        filters.enable("warm", not filters.get("warm").enabled)
        return TableHistogram.from_table(table, "rpm", "map", "inj", histogram.RPM_250, histogram.MAP_KPA_5,
                                         filters.mask(table))
    took, _ = timed(toggle, 10)
    report("toggle a filter and rebuild the histogram", f"{took * 1000:.2f} ms", "budget 200 ms")

    def cold_filter():
        fresh = FilterSet(filters.filters)
        return fresh.mask(table)
    took, _ = timed(cold_filter)
    report("evaluate 3 filter expressions from scratch", f"{took * 1000:.2f} ms", "")

    session = ReplaySession(log)
    took, _ = timed(lambda: [session.trace(n, 1366) for n in ("rpm", "map", "tps", "o2", "inj", "ign", "ect", "batt")], 20)
    report("8 traces, whole log, 1366 px (min/max envelope)", f"{took * 1000:.2f} ms", "one frame at 30 fps is 33 ms")
    session.set_cursor(log.t1 * 0.25)
    session.set_cursor(log.t1 * 0.75, "b")
    took, _ = timed(lambda: session.readouts(), 10)
    report("dual-cursor statistics, all channels", f"{took * 1000:.2f} ms", f"{len(log.channels)} channels")

    for ext in ("mlg", "msl", "csv"):
        out = folder / f"out.{ext}"
        took, _ = timed(lambda: export_log(log, out, channels=["rpm", "map", "baro", "tps", "o2", "inj", "ign",
                                                               "ect", "iat", "batt", "closed_loop"], base="rpm"), 1)
        report(f"export 11 channels to .{ext}", f"{took * 1000:.0f} ms", f"{out.stat().st_size / 1e6:.1f} MB")
    took, back = timed(lambda: open_log(folder / "out.csv"), 1)
    report("import that CSV again", f"{took * 1000:.0f} ms", f"{back.rows} rows")
    took, back = timed(lambda: open_log(folder / "out.mlg"), 1)
    report("import that MLG again", f"{took * 1000:.0f} ms", f"{back.rows} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=float, default=1.0)
    args = parser.parse_args()
    import platform
    print(f"python {platform.python_version()}, numpy {np.__version__}, {platform.processor() or platform.machine()}")
    with tempfile.TemporaryDirectory(prefix="hondalog-bench-") as tmp:
        bench_write(Path(tmp))
        bench_hour(Path(tmp), args.hours)


if __name__ == "__main__":
    main()
