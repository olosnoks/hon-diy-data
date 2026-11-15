"""Benchmarks for the data layer. Run from the repo root:

    python3 bench/bench_data.py            # 1-hour synthetic drive
    python3 bench/bench_data.py --hours 3

Prints one line per measurement. The budgets from docs/research/FEATURES.md:
open a 1-hour drive in under 2 s, filter toggle under 200 ms, recording
under 5 % of one core, and a database no bigger per hour than the 13.8 MB
the .hlog container took for the same streams.
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hondalog import addressmap                                                            # noqa: E402
from hondalog.analysis import Filter, FilterSet, ReplaySession, TableHistogram, histogram  # noqa: E402
from hondalog.blackbox.channels import LIVE_NAMES, bb_channel                              # noqa: E402
from hondalog.data import Column, Store, db, open_log                                      # noqa: E402
from hondalog.data.synth import write_synthetic                                            # noqa: E402
from hondalog.export import export_log                                                     # noqa: E402
from hondalog.logger import Recorder, column_for                                           # noqa: E402

OLD_HOUR_MB = 13.8          # the same hour in the .hlog container


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
    cols = [Column(f"c{i}") for i in range(4)]
    values = [float(i) + 0.25 for i in range(4)]
    raw = bytes(range(4))
    for label, fsync in (("synchronous=NORMAL", True), ("synchronous=OFF", False)):
        path = folder / f"write_{fsync}.db"
        store = Store(path, fsync=fsync)
        drive = store.begin_drive({"bench": True})
        drive.add_stream("ecu/12+4", cols, source="ecu", raw_len=4)
        n = 200_000
        gaps = []
        start = time.perf_counter()
        last = start
        for i in range(n):
            drive.append("ecu/12+4", i * 0.001, values, raw, 0.004)
            if i % 64 == 0:
                now = time.perf_counter()
                gaps.append((now - last) / 64)
                last = now
        took = time.perf_counter() - start
        store.close()
        size = db.file_size(path)
        report(f"append, one K-line read ({label})", f"{n / took / 1000:.0f} k rows/s",
               f"{size / n:.0f} bytes/row; per call median {statistics.median(gaps) * 1e6:.1f} us, "
               f"worst 64-call mean {max(gaps) * 1e6:.0f} us")

    path = folder / "bulk.db"
    info_cols = [Column(f"c{i}") for i in range(12)]
    rows = np.zeros(1_000_000, dtype=[("t", "<f8")] + [(f"c{i}", "<f4") for i in range(12)])
    rows["t"] = np.arange(len(rows)) / 1000.0
    rng = np.random.default_rng(0)
    for i in range(12):
        rows[f"c{i}"] = np.round(13.8 + rng.normal(0, 0.03, len(rows)), 3)
    start = time.perf_counter()
    store = Store(path, background=False, fsync=False)
    drive = store.begin_drive()
    drive.add_stream("blackbox/rails", info_cols, source="blackbox")
    for chunk in np.array_split(rows, 1000):                # 1 s of 1 kHz frames per call
        drive.append_array("blackbox/rails", chunk)
        store.flush()
    store.close()
    took = time.perf_counter() - start
    size = db.file_size(path)
    report("append_array, 1 kHz x 12 columns, 1000 s", f"{len(rows) / took / 1e6:.2f} M rows/s",
           f"{size / 1e6:.1f} MB for {rows.nbytes / 1e6:.0f} MB of rows "
           f"({size / len(rows):.1f} bytes/row, 1 Hz statistics included)")


def feed_drive(rec: Recorder, seconds: float, bb_hz: int) -> int:
    """What a car session feeds: the read plan's blocks in three tiers
    (about 55 reads/s), the math stream, black box frames."""
    blocks = {0x00: bytes([0x2e, 0xe0, 40]), 0x12: bytes([60, 141, 30, 23]), 0x24: bytes([20, 140, 0]),
              0x08: bytes(10), 0x17: bytes([145, 128, 0]), 0x20: bytes([128, 128, 0])}
    bb_cols = [column_for(bb_channel(n)) for n in LIVE_NAMES]
    bb_values = {n: 13.8 for n in LIVE_NAMES}
    fed = 0
    cycle = 1 / 16.0
    per_cycle = max(1, int(bb_hz * cycle + 0.5))
    for i in range(int(seconds / cycle)):
        t = i * cycle
        for k, addr in enumerate((0x00, 0x12, 0x24)):
            rec.feed_read(t + k * 0.018, addr, blocks[addr], 0.004)
        if i % 8 == 0:
            for k, addr in enumerate((0x08, 0x17, 0x20)):
                rec.feed_read(t + 0.055 + k * 0.002, addr, blocks[addr], 0.004)
            fed += 3
        rec.feed_values("math", t + 0.06, {"afr": 14.7, "duty": 31.0}, source="math")
        for j in range(per_cycle):
            bb_values["bb_ecub"] = 13.8 + (i + j) % 7 / 1000.0
            rec.feed_values("blackbox/live", t + j * cycle / per_cycle, bb_values, source="blackbox",
                            columns=bb_cols)
        fed += 4 + per_cycle
        if i % 8 == 7:
            rec.flush()                 # the commit the writer thread makes every 0.5 s of a real drive
    return fed


def bench_recording(folder: Path) -> None:
    amap = addressmap.load_default()
    for bb_hz, seconds in ((50, 600.0), (1000, 120.0)):
        out = folder / f"rec{bb_hz}"
        rec = Recorder(amap, "BENCH", "sim:", out_dir=out)
        cpu = time.process_time()
        fed = feed_drive(rec, seconds, bb_hz)
        rec.flush()
        used = time.process_time() - cpu
        rec.stop()
        size = db.file_size(rec.db)
        report(f"recording, K-line + {bb_hz} Hz black box", f"{used / seconds * 100:.2f} % of a core",
               f"budget 5 %; {fed / seconds:.0f} rows/s through Recorder.feed_*, "
               f"{size / seconds * 3600 / 1e6:.1f} MB per hour")


def table_sizes(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT name, sum(pgsize) FROM dbstat GROUP BY name ORDER BY 2 DESC LIMIT 3").fetchall()
    except sqlite3.Error:
        return ""
    finally:
        conn.close()
    return ", ".join(f"{name} {size / 1e6:.1f}" for name, size in rows)


def bench_hour(folder: Path, hours: float) -> None:
    path = folder / "hour.db"
    start = time.perf_counter()
    ref = write_synthetic(path, duration_s=3600 * hours)
    made = time.perf_counter() - start
    size = db.file_size(path)
    with open_log(ref) as log:
        rows = log.rows
    report(f"{hours:g} h synthetic drive", f"{size / 1e6:.1f} MB",
           f"{rows} rows in 5 streams, written in {made:.2f} s; .hlog took {OLD_HOUR_MB * hours:.1f} MB "
           f"({size / (OLD_HOUR_MB * hours * 1e6) * 100:.0f} %)")
    report("  of which (MB)", "", table_sizes(path))

    took, _ = timed(lambda: open_log(ref).close())
    report("open (drive row, streams, events)", f"{took * 1000:.1f} ms", "budget 2000 ms")

    def open_and_load():
        with open_log(ref) as log:
            for name in log.channels:
                log.series(name)
            return log.rows
    took, _ = timed(open_and_load, 3)
    report("open + load every channel of every stream", f"{took * 1000:.0f} ms", "budget 2000 ms")

    def open_and_plot():
        with open_log(ref) as log:
            session = ReplaySession(log)
            return [session.trace(n, 1366) for n in ("rpm", "map", "tps", "o2", "inj", "ign", "ect", "batt")]
    took, _ = timed(open_and_plot, 3)
    report("open + 8 traces ready to draw at 1366 px", f"{took * 1000:.0f} ms", "what the replay tab needs")

    def one_window():
        with open_log(ref) as log:
            return log.window(1800.0, 1860.0).rows
    took, n = timed(one_window, 3)
    report("open + every stream, one 60 s window", f"{took * 1000:.0f} ms", f"{n} rows")

    conn = sqlite3.connect(path)
    took, got = timed(lambda: conn.execute(f"SELECT count(*), avg(map), avg(batt), avg(vbatt_mean) FROM {ref.name}"
                                           ).fetchone(), 3)
    report("SQL: aggregate over the whole drive view", f"{took * 1000:.0f} ms",
           f"{got[0]} rows, 11 ECU + 12 black box columns held on the rpm grid")
    took, got = timed(lambda: conn.execute(f"SELECT * FROM {ref.name} WHERE t BETWEEN 1800 AND 1805").fetchall(), 3)
    report("SQL: 5 s of the drive view, all columns", f"{took * 1000:.1f} ms", f"{len(got)} rows")
    took, got = timed(lambda: conn.execute("SELECT t, value FROM readings WHERE drive = ? AND channel = 'rpm' "
                                           "AND t BETWEEN 1800 AND 1860", (ref.name,)).fetchall(), 3)
    report("SQL: one channel, 60 s, from 'readings'", f"{took * 1000:.1f} ms", f"{len(got)} rows")
    conn.close()

    crashed = folder / "crashed.db"
    store = Store(crashed, background=False, fsync=False)
    drive = store.begin_drive()
    with open_log(ref) as log:
        for stream in log.streams.values():
            info = stream.info
            drive.add_stream(info.name, info.columns, source=info.source, raw_len=info.raw_len, attrs=info.attrs)
            if info.source == "blackbox":                   # row by row with a commit every 0.5 s, as when live
                names = info.column_names
                for part in np.array_split(stream.data, int(7200 * hours)):
                    for row in part.tolist():
                        drive.append(info.name, row[0], row[1:1 + len(names)])
                    store.flush()
            else:
                drive.append_array(info.name, stream.data)
    store.flush()
    db.mark_active(crashed, drive.id, False)                # the recorder dies here: no end, no close
    store._conn.close()
    start = time.perf_counter()
    with open_log(crashed) as log:
        status = log.status
    report("first open after a crash at the end of the hour", f"{(time.perf_counter() - start) * 1000:.0f} ms",
           f"marks the drive '{status}', packs the last minute of black box rows, builds the view")

    log = open_log(ref).load_all()
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
    print(f"python {platform.python_version()}, numpy {np.__version__}, sqlite {sqlite3.sqlite_version}, "
          f"{platform.processor() or platform.machine()}")
    with tempfile.TemporaryDirectory(prefix="hondalog-bench-") as tmp:
        bench_write(Path(tmp))
        bench_recording(Path(tmp))
        bench_hour(Path(tmp), args.hours)


if __name__ == "__main__":
    main()
