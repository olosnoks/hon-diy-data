"""Performance budgets from docs/research/FEATURES.md, with wide margins so a
slow CI box does not flake. bench/bench_data.py prints the real numbers."""

import threading
import time

import numpy as np

from hondalog import addressmap
from hondalog.analysis import Filter, FilterSet, ReplaySession, TableHistogram, histogram
from hondalog.blackbox.channels import LIVE_NAMES, bb_channel
from hondalog.data import db, open_log
from hondalog.data.synth import write_synthetic
from hondalog.logger import Recorder, column_for

#: what one hour of the same streams took in the .hlog container this replaced
OLD_HOUR_MB = 13.8


def test_one_hour_drive_budgets(tmp_path):
    ref = write_synthetic(tmp_path / "hondalog.db", duration_s=3600)
    size = db.file_size(ref.db)
    assert size < OLD_HOUR_MB * 1.05e6                      # 3 fast blocks, 1 slow, 50 Hz black box

    start = time.perf_counter()
    log = open_log(ref)
    for name in log.channels:
        log.series(name)
    session = ReplaySession(log)
    traces = [session.trace(n, 1366) for n in ("rpm", "map", "tps", "o2", "inj", "ign", "ect", "batt")]
    opened = time.perf_counter() - start
    assert opened < 2.0 and log.rows > 300_000 and all(len(e.t) <= 1366 for e in traces)

    table = log.table(["rpm", "map", "inj", "ect", "closed_loop", "tps"], base="rpm")
    filters = FilterSet([Filter("warm", "ect > 80"), Filter("cl", "closed_loop"),
                         Filter("steady", "abs(rate(tps)) < 5")])
    filters.mask(table)
    start = time.perf_counter()
    filters.enable("warm", False)
    hist = TableHistogram.from_table(table, "rpm", "map", "inj", histogram.RPM_250, histogram.MAP_KPA_5,
                                     filters.mask(table))
    assert time.perf_counter() - start < 0.2 and hist.samples > 1000

    start = time.perf_counter()
    for i in range(30):                                     # pan across the hour
        session.set_view(i * 100.0, i * 100.0 + 600.0)
        for name in ("rpm", "map", "tps", "o2", "inj", "ign", "ect", "batt"):
            session.trace(name, 1366)
    assert (time.perf_counter() - start) / 30 < 0.033       # one 30 fps frame
    log.close()

    # the same hour as the tenth drive of a database opens no slower
    for k in range(3):
        write_synthetic(ref.db, duration_s=600, seed=k + 2, started_unix=1794729600.0 + 4000 * (k + 1))
    start = time.perf_counter()
    with open_log(ref) as again:
        for name in again.channels:
            again.series(name)
    assert time.perf_counter() - start < 2.0


def feed_real_rates(rec, seconds, bb_hz=50):
    """What a car session feeds: the read plan's blocks (about 50 reads/s in
    three tiers), the math stream once per fast cycle, black box frames."""
    blocks = {0x00: bytes([0x2e, 0xe0, 40]), 0x12: bytes([60, 141, 30, 23]), 0x24: bytes([20, 140, 0]),
              0x08: bytes(10), 0x17: bytes([145, 128, 0]), 0x20: bytes([128, 128, 0])}
    bb_cols = [column_for(bb_channel(n)) for n in LIVE_NAMES]
    bb_values = {n: 13.8 for n in LIVE_NAMES}
    math_cols = None
    fed = 0
    cycle = 1 / 16.0
    for i in range(int(seconds / cycle)):
        t = i * cycle
        for k, addr in enumerate((0x00, 0x12, 0x24)):       # fast tier: every cycle
            rec.feed_read(t + k * 0.018, addr, blocks[addr], 0.004)
            fed += 1
        if i % 8 == 0:                                      # slower tiers
            for k, addr in enumerate((0x08, 0x17, 0x20)):
                rec.feed_read(t + 0.055 + k * 0.002, addr, blocks[addr], 0.004)
                fed += 1
        rec.feed_values("math", t + 0.06, {"afr": 14.7, "duty": 31.0}, source="math", columns=math_cols)
        for j in range(int(bb_hz * cycle + 0.5) or 1):
            bb_values["bb_ecub"] = 13.8 + (i + j) % 7 / 1000.0
            rec.feed_values("blackbox/live", t + j / bb_hz, bb_values, source="blackbox", columns=bb_cols)
            fed += 1
        fed += 1
        if i % 8 == 7:
            rec.flush()                 # the commit the writer thread makes every 0.5 s of a real drive
    return fed


def test_recording_at_real_rates_stays_under_five_percent_of_a_core(tmp_path):
    """Feed ten minutes of a drive as fast as it goes and charge all of the
    process's CPU (feeding thread and writer thread) to those ten minutes."""
    amap = addressmap.load_default()
    seconds = 600.0
    rec = Recorder(amap, "SIM", "sim:", out_dir=tmp_path)
    threads = threading.active_count()
    cpu = time.process_time()
    fed = feed_real_rates(rec, seconds)
    rec.flush()
    used = time.process_time() - cpu
    rec.stop()
    assert threading.active_count() <= threads
    assert fed / seconds > 90                               # about 55 reads/s + 50 black box frames/s
    assert used / seconds < 0.05, f"{used / seconds:.1%} of one core"
    assert rec.write_error is None
    with open_log(rec.path) as log:
        assert log.status == "complete" and log.rows - log.streams["ecu/link"].rows == fed
        assert log.series("bb_ecub").rate_hz > 40 and np.isfinite(log.series("rpm").v).all()
