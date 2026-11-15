"""The native log container: round trips, crash safety, the index, multi-rate reads."""

import os
import signal
import struct
import subprocess
import sys
import textwrap
import time

import numpy as np
import pytest

from hondalog.data import (ClockSync, Column, Log, LogFormatError, LogWriter, Stream, StreamInfo,
                           annotate, extract_window, is_hlog, open_log, read_hlog, repair, write_log)
from hondalog.data import container
from hondalog.data.model import Event, Series
from hondalog.data.reader import align


def make(path, rows=200, close=True, **opts):
    w = LogWriter(path, {"ecu": "P2P", "address_map_version": "v1"}, background=False, **opts)
    w.add_stream("ecu/00+2", [Column("rpm", units="rpm", decimals=0)], source="ecu", raw_len=2,
                 attrs={"addr": 0, "length": 2})
    w.add_stream("ecu/10+3", [Column("ect", units="°C"), Column("flag", "u1", kind="flag")], source="ecu")
    for i in range(rows):
        w.append("ecu/00+2", i * 0.05, [800 + i], struct.pack(">H", (800 + i) * 4))
        if i % 10 == 0:
            w.append("ecu/10+3", i * 0.05 + 0.02, [80 + i / 100, i % 2])
        if i % 50 == 49:
            w.flush()
    w.event(1.0, "marker", "MARK 1")
    w.event(2.5, "trigger", "STALL (rpm 0)", {"rule": "stall"})
    w.event(3.0, "note", "smelled fuel")
    if close:
        w.close()
    return w


def test_round_trip(tmp_path):
    path = tmp_path / "a.hlog"
    make(path)
    assert is_hlog(path) and not is_hlog(__file__)
    with open_log(path) as log:
        assert log.indexed and log.junk_bytes == 0
        assert log.meta["ecu"] == "P2P"
        assert list(log.streams) == ["ecu/00+2", "ecu/10+3"]
        assert log.channels == ["rpm", "ect", "flag"]
        assert log.rows == 220 and log.streams["ecu/00+2"].rows == 200
        rpm = log.series("rpm")
        assert rpm.units == "rpm" and rpm.rate_hz == pytest.approx(20)
        np.testing.assert_allclose(rpm.v, 800 + np.arange(200))
        np.testing.assert_allclose(rpm.t, np.arange(200) * 0.05)
        raw = log.streams["ecu/00+2"].raw
        assert raw.shape == (200, 2) and bytes(raw[3]) == struct.pack(">H", 803 * 4)
        assert log.streams["ecu/10+3"].raw.shape == (20, 0)
        assert log.streams["ecu/00+2"].info.attrs == {"addr": 0, "length": 2}
        assert log.column("ect").units == "°C" and log.column("nope") is None
        assert log.series("flag").v[:3].tolist() == [0, 0, 0] and log.series("flag").v[1] == 0
        assert log.markers == [(1.0, "MARK 1"), (2.5, "STALL (rpm 0)")]
        assert [e.kind for e in log.events] == ["marker", "trigger", "note"]
        assert log.events[1].data == {"rule": "stall"}
        assert log.t0 == 0 and log.t1 == pytest.approx(9.95) and log.duration == pytest.approx(9.95)
        with pytest.raises(KeyError):
            log.series("nope")


def test_none_becomes_nan_and_bad_rows_are_rejected(tmp_path):
    with LogWriter(tmp_path / "a.hlog", background=False) as w:
        w.add_stream("s", [Column("a"), Column("b")], raw_len=2)
        w.append("s", 0.0, [None, 2.0], b"ab")
        with pytest.raises(ValueError):
            w.append("s", 0.1, [1.0], b"ab")
        with pytest.raises(ValueError):
            w.append("s", 0.1, [1.0, 2.0], b"abc")
        w.add_stream("s", [Column("a"), Column("b")], raw_len=2)      # same again is fine
        with pytest.raises(ValueError):
            w.add_stream("s", [Column("a")])
    log = read_hlog(tmp_path / "a.hlog")
    assert np.isnan(log.series("a").v[0]) and log.series("b").v[0] == 2 and log.rows == 1
    with pytest.raises(ValueError):
        w.append("s", 1, [1, 2], b"ab")                               # closed
    with pytest.raises(ValueError):
        Column("t")
    with pytest.raises(ValueError):
        Column("x", "f3")
    with pytest.raises(ValueError):
        StreamInfo("s", (Column("a"), Column("a")))


def test_unclosed_file_reads_everything_flushed(tmp_path):
    path = tmp_path / "a.hlog"
    w = make(path, close=False)
    w.flush()
    with read_hlog(path) as log:
        assert not log.indexed and log.rows == 220 and len(log.events) == 3
    w.close()
    assert read_hlog(path).indexed


@pytest.mark.parametrize("cut", [17, 40, 300, 1234, 2500, -30, -5, -1])
def test_truncated_file_opens_and_repairs(tmp_path, cut):
    path = tmp_path / "a.hlog"
    make(path)
    whole = path.read_bytes()
    path.write_bytes(whole[:cut])
    with read_hlog(path) as log:
        assert not log.indexed
        kept = log.rows
        for name in log.channels:
            log.series(name)                    # every surviving stream loads
    assert kept <= 220
    info = repair(path)
    assert info["rows"] == kept and not info["was_indexed"]
    with read_hlog(path) as log:
        assert log.indexed and log.junk_bytes == 0 and log.rows == kept
    assert repair(path)["was_indexed"]


def test_damage_in_the_middle_is_skipped(tmp_path):
    path = tmp_path / "a.hlog"
    make(path)
    blob = bytearray(path.read_bytes())
    table = container.scan_chunks(bytes(blob), len(blob))
    victim = table.entries[table.entries["type"] == container.DATA][1]
    blob[int(victim["offset"]) + 20] ^= 0xFF                # one flipped bit in a payload
    path.write_bytes(blob[:-container.TRAILER.size])        # and no trailer, so it must scan
    with read_hlog(path) as log:
        assert not log.indexed and log.junk_bytes > 0
        assert 0 < log.rows < 220
        assert len(log.events) == 3                         # chunks after the damage survive


def test_not_a_log(tmp_path):
    bad = tmp_path / "x.hlog"
    bad.write_bytes(b"HONDALOG")
    with pytest.raises(LogFormatError):
        read_hlog(bad)
    bad.write_bytes(b"NOTALOG!" + bytes(32))
    with pytest.raises(LogFormatError):
        read_hlog(bad)
    bad.write_bytes(container.FILE_HEADER.pack(b"HONDALOG", 99, 16, 0))
    with pytest.raises(LogFormatError, match="newer"):
        read_hlog(bad)


def test_append_and_annotate_keep_old_data(tmp_path):
    path = tmp_path / "a.hlog"
    make(path)
    annotate(path, notes="idle hunting", tags=["idle"])
    annotate(path, notes="idle hunting, fixed by cleaning IACV")
    with LogWriter(path, append=True, background=False) as w:
        w.append("ecu/00+2", 10.0, [1234], b"\x00\x01")
        w.add_stream("late", [Column("x")])
        w.append("late", 10.1, [5])
    with read_hlog(path) as log:
        assert log.indexed and log.rows == 222
        assert log.meta["notes"].endswith("IACV") and log.meta["tags"] == ["idle"]
        assert log.series("rpm").v[-1] == 1234 and log.series("x").v.tolist() == [5]


def test_background_writer_flushes_on_its_own(tmp_path):
    path = tmp_path / "a.hlog"
    w = LogWriter(path, flush_interval=0.05)
    w.add_stream("s", [Column("a")])
    for i in range(50):
        w.append("s", i * 0.01, [i])
    deadline = time.monotonic() + 3
    rows = 0
    while rows < 50 and time.monotonic() < deadline:
        time.sleep(0.05)
        with read_hlog(path) as log:
            rows = log.rows
    assert rows == 50                           # on disk without flush() or close()
    w.close()
    w.close()                                   # twice is harmless
    assert w.error is None and w.closed and w.rows == 50 and w.bytes_written == path.stat().st_size


def test_hard_kill_loses_at_most_one_second(tmp_path):
    """SIGKILL a process that is logging at 100 Hz; the file must open and
    hold everything up to within a second of the kill."""
    path = tmp_path / "killed.hlog"
    script = textwrap.dedent(f"""
        import sys, time
        from hondalog.data import LogWriter, Column
        w = LogWriter({str(path)!r}, {{"who": "child"}})
        w.add_stream("s", [Column("a")], raw_len=4)
        t0 = time.monotonic()
        i = 0
        while True:
            t = time.monotonic() - t0
            w.append("s", t, [i], b"abcd")
            i += 1
            if i == 250:
                print("go", flush=True)
            time.sleep(0.01)
    """)
    env = dict(os.environ, PYTHONPATH=os.getcwd())
    child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, env=env, text=True)
    try:
        assert child.stdout.readline().strip() == "go"
        started = time.monotonic()
        time.sleep(1.2)
        child.send_signal(signal.SIGKILL)
        child.wait()
        ran = 2.5 + (time.monotonic() - started)        # 250 rows at 10 ms, then the sleep
    finally:
        child.kill()
    with read_hlog(path) as log:
        assert not log.indexed and log.meta["who"] == "child"
        s = log.series("a")
        assert np.array_equal(s.v, np.arange(len(s)))   # no holes, no reordering
        assert s.t[-1] > ran - 1.0 - 0.6                # 1 s budget (+ process start slack)
    assert not repair(path)["was_indexed"]


def test_series_merges_a_channel_read_in_two_streams(tmp_path):
    with LogWriter(tmp_path / "a.hlog", background=False) as w:
        w.add_stream("fast", [Column("rpm")])
        w.add_stream("slow", [Column("rpm"), Column("ect")])
        for i in range(10):
            w.append("fast", i * 0.1, [i])
        w.append("slow", 0.25, [100, 80])
        w.append("slow", 0.75, [200, 81])
    log = read_hlog(tmp_path / "a.hlog")
    s = log.series("rpm")
    assert len(s) == 12 and np.all(np.diff(s.t) >= 0)
    assert s.v[3] == 100 and s.v[9] == 200
    assert len(log.streams_with("rpm")) == 2 and log.channels == ["rpm", "ect"]


def test_table_puts_rates_on_one_grid(synth):
    table = synth.table(["rpm", "ect", "vbatt_mean"])
    assert len(table) == len(synth.series("vbatt_mean"))          # densest channel is the grid
    table = synth.table(["rpm", "map", "ect"], base="rpm")
    rpm = synth.series("rpm")
    assert len(table) == len(rpm) and np.array_equal(table["rpm"], rpm.v) and "t" in table
    assert not np.isnan(table["map"][5:]).any()
    # hold: every map value on the grid is a real sample taken at or before that time
    m = synth.series("map")
    i = 5000
    j = np.searchsorted(m.t, table.t[i], side="right") - 1
    assert table["map"][i] == m.v[j] and m.t[j] <= table.t[i]
    assert table.units["ect"] == "°C" and table.names == ["rpm", "map", "ect"]
    uniform = synth.table(["rpm"], hz=10)
    assert np.allclose(np.diff(uniform.t), 0.1) and len(uniform) == pytest.approx(6000, abs=2)
    sub = table.select(table["rpm"] > 3000)
    assert 0 < len(sub) < len(table) and sub["rpm"].min() > 3000


def test_align_gaps_and_modes():
    s = Series("x", np.array([0.0, 1.0, 2.0, 10.0]), np.array([0.0, 10.0, 20.0, 100.0]))
    grid = np.array([-1.0, 0.5, 1.0, 2.9, 6.0, 10.0])
    held = align(s, grid, "hold", max_age=1.5)
    assert np.isnan(held[0]) and held[1:4].tolist() == [0, 10, 20] and np.isnan(held[4]) and held[5] == 100
    lin = align(s, grid, "linear", max_age=1.5)
    assert lin[1] == 5 and np.isnan(lin[3]) and np.isnan(lin[4]) and lin[5] == 100
    assert align(s, grid).tolist()[1:4] == [0, 10, 20]            # automatic max age
    assert np.isnan(align(Series("e", np.zeros(0), np.zeros(0)), grid)).all()
    with pytest.raises(ValueError):
        align(s, grid, "cubic")


def test_window_and_extract(tmp_path, synth_path):
    out, rows = extract_window([synth_path], tmp_path / "cut.hlog", 100.0, 140.0, {"trigger": "manual"})
    with read_hlog(out) as cut, read_hlog(synth_path) as full:
        assert set(cut.streams) == set(full.streams)             # ECU and black box in one bundle
        assert cut.rows == rows and cut.t0 >= 100 and cut.t1 <= 140
        assert cut.meta["trigger"] == "manual" and cut.meta["ecu"] == full.meta["ecu"]
        assert cut.streams["ecu/00+2"].raw.shape[1] == 2
        assert cut.streams["blackbox/rails"].rows == pytest.approx(40 * 50, abs=2)
        assert [e.text for e in cut.events] == [e.text for e in full.events if 100 <= e.t <= 140]


def test_extract_joins_rotated_parts(tmp_path):
    for part, t0 in (("p1", 0.0), ("p2", 10.0)):
        with LogWriter(tmp_path / f"{part}.hlog", {"part": part}, background=False) as w:
            w.add_stream("s", [Column("a")])
            for i in range(100):
                w.append("s", t0 + i * 0.1, [t0 + i * 0.1])
    out, rows = extract_window([tmp_path / "p1.hlog", tmp_path / "p2.hlog"], tmp_path / "j.hlog", 8.0, 12.0)
    s = read_hlog(out).series("a")
    assert rows == len(s) == 41 and s.t[0] == pytest.approx(8.0) and np.all(np.diff(s.t) > 0)


def test_in_memory_log_and_write_log(tmp_path):
    info = StreamInfo("m", (Column("x", "f8"),), source="math")
    rows = np.zeros(5, info.dtype)
    rows["t"] = np.arange(5.0)
    rows["x"] = np.arange(5.0) ** 2
    log = Log({"k": 1}, [Stream(info, data=rows)], [Event(2.0, "marker", "m")])
    assert log.rows == 5 and log.series("x").v[3] == 9 and log.path is None
    back = read_hlog(write_log(log, tmp_path / "m.hlog", {"extra": True})).load_all()
    assert back.meta == {"k": 1, "extra": True} and back.series("x").v.tolist() == [0, 1, 4, 9, 16]
    assert back.streams["m"].info.source == "math" and back.markers == [(2.0, "m")]
    empty = Log()
    assert empty.t0 == 0 and empty.duration == 0 and empty.channels == [] and len(empty.table()) == 0


def test_stream_loads_after_close(synth_path):
    log = read_hlog(synth_path)
    log.close()
    assert len(log.series("rpm")) > 9000          # reopens the file for the load


def test_clock_sync_offset_and_drift():
    sync = ClockSync()
    assert not sync.synced and sync.residual_ms() == 0
    sync.add(10.0, 1000.0)
    assert sync.to_session(1001.0) == pytest.approx(11.0)
    drift = 40e-6                                   # a 40 ppm crystal
    for i in range(60):
        box = 1000.0 + i
        sync.add(10.0 + i * (1 + drift) + (0.0004 if i % 2 else -0.0004), box)
    assert sync.rate == pytest.approx(1 + drift, abs=5e-6)
    assert sync.to_session(1030.0) == pytest.approx(10.0 + 30 * (1 + drift), abs=0.001)
    assert sync.residual_ms() < 1.0
    np.testing.assert_allclose(sync.to_session(np.array([1000.0, 1059.0])),
                               [10.0, 10.0 + 59 * (1 + drift)], atol=0.001)
