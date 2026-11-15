"""Performance budgets from docs/research/FEATURES.md, with wide margins so a
slow CI box does not flake. bench/bench_data.py prints the real numbers."""

import time

from hondalog.analysis import Filter, FilterSet, ReplaySession, TableHistogram, histogram
from hondalog.data import open_log
from hondalog.data.synth import write_synthetic


def test_one_hour_log_budgets(tmp_path):
    path = write_synthetic(tmp_path / "hour.hlog", duration_s=3600)
    assert path.stat().st_size < 20e6                       # 3 fast blocks, 1 slow, 50 Hz black box

    start = time.perf_counter()
    log = open_log(path)
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
