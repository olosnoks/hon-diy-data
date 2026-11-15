"""The drive database: round trips, crash recovery, migrations, the views
(queried with nothing but sqlite3), black box chunks, reading while recording."""

import json
import os
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import textwrap
import threading
import time

import numpy as np
import pytest

from hondalog.data import (ClockSync, Column, DriveRef, Log, LogFormatError, Store, Stream, StreamInfo,
                           annotate, connect, db_path, extract_window, new_drive, open_log, parse_ref,
                           read_drive, schema_version, vacuum, write_log)
from hondalog.data import codec, db, schema
from hondalog.data.model import Event, Series
from hondalog.data.reader import align


def fill(drive, rows=200):
    drive.add_stream("ecu/00+2", [Column("rpm", units="rpm", decimals=0)], source="ecu", raw_len=2,
                     attrs={"addr": 0, "length": 2})
    drive.add_stream("ecu/10+3", [Column("ect", units="°C"), Column("flag", "u1", kind="flag")], source="ecu")
    for i in range(rows):
        drive.append("ecu/00+2", i * 0.05, [800 + i], struct.pack(">H", (800 + i) * 4), latency=0.004)
        if i % 10 == 0:
            drive.append("ecu/10+3", i * 0.05 + 0.02, [80 + i / 100, i % 2])
        if i % 50 == 49:
            drive.store.flush()
    drive.event(1.0, "marker", "MARK 1")
    drive.event(2.5, "trigger", "STALL (rpm 0)", {"rule": "stall"})
    drive.event(3.0, "note", "smelled fuel")
    drive.event(3.5, "alarm", "ECT high", {"alarm": "ect_high", "kind": "raised", "severity": "warn",
                                           "value": 106.0, "channel": "ect", "unverified": True})


def make(path, rows=200):
    with new_drive(path, {"ecu": "P2P", "address_map_version": "v1", "started_unix": 1794812400.0,
                          "epoch_unix": 1794812400.0, "address_map_text": "[[channel]]\nname = 'rpm'\n"}) as drive:
        fill(drive, rows)
    return drive.ref


def test_round_trip(tmp_path):
    ref = make(tmp_path / "a.db")
    assert isinstance(ref, DriveRef) and ref.db == tmp_path / "a.db" and ref.name.startswith("drive_2026_11_")
    assert str(ref) == f"{tmp_path / 'a.db'}#{ref.name}" and ref.exists() and ref.suffix == ""
    assert db.is_database(ref.db) and not db.is_database(__file__)
    with open_log(ref) as log:
        assert log.status == "complete" and log.ref == ref and log.name == ref.name
        assert log.meta["ecu"] == "P2P" and log.meta["address_map_version"] == "v1"
        assert log.meta["address_map_text"].startswith("[[channel]]") and log.meta["epoch_unix"] == 1794812400.0
        assert list(log.streams) == ["ecu/00+2", "ecu/10+3"]
        assert log.channels == ["rpm", "ect", "flag"]
        assert log.rows == 220 and log.streams["ecu/00+2"].rows == 200
        rpm = log.series("rpm")
        assert rpm.units == "rpm" and rpm.rate_hz == pytest.approx(20)
        np.testing.assert_allclose(rpm.v, 800 + np.arange(200))
        np.testing.assert_allclose(rpm.t, np.arange(200) * 0.05)
        raw = log.streams["ecu/00+2"].raw
        assert raw.shape == (200, 2) and bytes(raw[3]) == struct.pack(">H", 803 * 4)
        np.testing.assert_allclose(log.streams["ecu/00+2"].latency, 0.004)
        assert log.streams["ecu/10+3"].raw.shape == (20, 0) and np.isnan(log.streams["ecu/10+3"].latency).all()
        assert log.streams["ecu/00+2"].info.attrs == {"addr": 0, "length": 2}
        assert log.column("ect").units == "°C" and log.column("nope") is None
        assert log.series("flag").v[:3].tolist() == [0, 0, 0] and log.streams["ecu/10+3"].data["flag"].dtype == np.uint8
        assert log.markers == [(1.0, "MARK 1"), (2.5, "STALL (rpm 0)"), (3.5, "ECT high")]
        assert [e.kind for e in log.events] == ["marker", "trigger", "note", "alarm"]
        assert log.events[1].data == {"rule": "stall"} and log.events[3].data["value"] == 106.0
        assert log.t0 == 0 and log.t1 == pytest.approx(9.95) and log.duration == pytest.approx(9.95)
        with pytest.raises(KeyError):
            log.series("nope")
    # every way of naming the same drive
    for spec in (ref.db, str(ref), f"{ref.db}#1", f"{ref.db}#last"):
        with open_log(spec) as log:
            assert log.name == ref.name and log.rows == 220
    assert read_drive(ref.db, 1).name == ref.name and parse_ref(str(ref)) == ref
    assert parse_ref(ref.name, ref.db) == ref and parse_ref("nope", ref.db) is None
    with pytest.raises(KeyError):
        open_log(f"{ref.db}#drive_1999_01_01_0000")


def test_none_becomes_nan_and_bad_rows_are_rejected(tmp_path):
    with new_drive(tmp_path / "a.db") as w:
        w.add_stream("s", [Column("a"), Column("b")], raw_len=2)
        w.append("s", 0.0, [None, 2.0], b"ab")
        w.append("s", 0.5, [float("nan"), np.float32(3.5)], b"cd")       # numpy scalars are stored too
        with pytest.raises(ValueError):
            w.append("s", 0.1, [1.0], b"ab")
        with pytest.raises(ValueError):
            w.append("s", 0.1, [1.0, 2.0], b"abc")
        w.add_stream("s", [Column("a"), Column("b")], raw_len=2)      # same again is fine
        with pytest.raises(ValueError):
            w.add_stream("s", [Column("a")])
        with pytest.raises(ValueError):
            w.add_stream("x", [Column("a")], storage="tape")
    log = open_log(w.ref)
    assert np.isnan(log.series("a").v).all() and log.series("b").v.tolist() == [2, 3.5] and log.rows == 2
    with pytest.raises(ValueError):
        w.append("s", 1, [1, 2], b"ab")                               # closed
    with pytest.raises(ValueError):
        Column("t")
    with pytest.raises(ValueError):
        Column("x", "f3")
    with pytest.raises(ValueError):
        StreamInfo("s", (Column("a"), Column("a")))


def test_wide_streams_and_equal_timestamps(tmp_path):
    """More columns than a samples row has slots; rows with the same time."""
    names = [f"c{i}" for i in range(schema.SLOTS * 2 + 3)]
    with new_drive(tmp_path / "a.db") as w:
        w.add_stream("wide", [Column(n, "f8") for n in names], raw_len=1)
        for i, t in enumerate([0.0, 0.1, 0.1, 0.1, 0.05, 0.2]):
            w.append("wide", t, [i * 100 + k for k in range(len(names))], bytes([i]))
        rows = np.zeros(3, StreamInfo("wide", tuple(Column(n, "f8") for n in names), raw_len=1).dtype)
        rows["t"] = [0.2, 0.3, 0.3]
        rows["c0"], rows[names[-1]], rows["raw"] = [7, 8, 9], [70, 80, 90], [[7], [8], [9]]
        w.append_array("wide", rows)
    with open_log(w.ref) as log:
        data = log.streams["wide"].data
        assert len(data) == 9 and np.all(np.diff(data["t"]) >= 0)             # nothing lost, time order
        assert sorted(data["raw"][:, 0].tolist()) == [0, 1, 2, 3, 4, 5, 7, 8, 9]
        first = data[data["raw"][:, 0] == 3][0]
        assert first["c0"] == 300 and first[names[-1]] == 300 + len(names) - 1    # parts line up
        assert log.series(names[-1]).v[-1] == 90
    conn = sqlite3.connect(tmp_path / "a.db")
    assert conn.execute("SELECT count(DISTINCT part) FROM samples").fetchone()[0] == 3
    last = names[-1]
    assert conn.execute(f"SELECT count(*), max({last}) FROM {w.name}").fetchone() == (9, 510.0)
    assert conn.execute(f"SELECT {last} FROM {w.name} WHERE c0 IN (100, 200, 300) ORDER BY c0").fetchall() \
        == [(110.0,), (210.0,), (310.0,)]                     # equal times: each row keeps its own values


def test_a_drive_is_readable_while_it_is_recorded(tmp_path):
    store = Store(tmp_path / "a.db", background=False)
    drive = store.begin_drive({"ecu": "P2P"})
    fill(drive)
    drive.add_stream("blackbox/rails", [Column("ecu_b", units="V")], source="blackbox")
    for i in range(500):
        drive.append("blackbox/rails", i * 0.01, [13.8])
    store.flush()
    with open_log(drive.ref) as log:
        assert log.status == "recording" and log.rows == 720 and len(log.events) == 4
        assert log.streams["blackbox/rails"].rows == 500 and len(log.series("ecu_b")) == 500
        assert log.series("rpm").v[-1] == 999
        drive.append("ecu/00+2", 99.0, [1], b"\x00\x04")
        store.flush()
        assert log.series("rpm").v[-1] == 999                 # a loaded series is a snapshot
    assert open_log(drive.ref).series("rpm").v[-1] == 1
    store.close()                                             # ends the drive that was left open
    with open_log(drive.ref) as log:
        assert log.status == "complete" and log.meta["end_reason"] == "closed" and log.rows == 721


def test_unclosed_drive_is_marked_crashed_on_the_next_open(tmp_path):
    path = tmp_path / "a.db"
    store = Store(path, background=False)
    drive = store.begin_drive({"ecu": "P2P", "epoch_unix": 1794812400.0})
    fill(drive)
    drive.add_stream("blackbox/rails", [Column("ecu_b", units="V")], source="blackbox")
    for i in range(300):
        drive.append("blackbox/rails", i * 0.01, [13.8 - i / 1000])
    store.flush()
    drive.append("ecu/00+2", 50.0, [1], b"\x00\x04")          # never committed
    with open_log(drive.ref) as log:
        assert log.status == "recording"                      # its writer is alive: left alone
    db.mark_active(path, drive.id, False)                     # the process "dies" here
    store._conn.close()

    conn = sqlite3.connect(path)                              # plain sqlite3: the file itself is sound
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT status, ended_at FROM drives").fetchone() == ("recording", None)
    conn.close()

    with open_log(drive.ref) as log:                          # any hondalog open finishes the row
        assert log.status == "crashed" and log.meta["end_reason"] == "crash"
        assert log.rows == 520 and log.series("rpm").v[-1] == 999 and len(log.events) == 4
        assert log.series("ecu_b").v[-1] == pytest.approx(13.501)
    conn = sqlite3.connect(path)
    status, ended, duration, rows, pid = conn.execute(
        "SELECT status, ended_at, duration_s, rows, writer_pid FROM drives").fetchone()
    assert (status, rows, pid) == ("crashed", 520, None) and duration == pytest.approx(9.95)
    assert ended == pytest.approx(1794812400.0 + 9.95)
    assert conn.execute("SELECT DISTINCT codec FROM chunks").fetchall() == [("d1",)]    # the tail was packed
    assert conn.execute(f"SELECT count(*) FROM {drive.name}").fetchone()[0] == 200       # and its view works
    assert conn.execute("SELECT status FROM drive_summary").fetchone()[0] == "crashed"

    again = Store(path, background=False)                     # recording goes on in the same file
    with again.begin_drive() as second:
        second.add_stream("s", [Column("a")])
        second.append("s", 0.0, [1.0])
    again.close()
    assert [r[0] for r in conn.execute("SELECT status FROM drives ORDER BY id")] == ["crashed", "complete"]


def test_hard_kill_loses_at_most_one_second(tmp_path):
    """SIGKILL a process that is recording at 100 Hz (K-line rows and black
    box rows). The database must open cleanly, hold everything up to within
    a second of the kill, and show the drive as crashed with an end time."""
    path = tmp_path / "killed.db"
    script = textwrap.dedent(f"""
        import sys, time
        from hondalog.data import Store, Column
        store = Store({str(path)!r})
        d = store.begin_drive({{"who": "child", "epoch_unix": 1000.0}})
        d.add_stream("s", [Column("a")], raw_len=4)
        d.add_stream("blackbox/live", [Column("v")], source="blackbox")
        t0 = time.monotonic()
        i = 0
        while True:
            t = time.monotonic() - t0
            d.append("s", t, [i], b"abcd")
            d.append("blackbox/live", t, [i])
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
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert check.execute("SELECT status FROM drives").fetchone()[0] == "recording"
    check.close()
    with open_log(path) as log:
        assert log.status == "crashed" and log.meta["who"] == "child"
        for name in ("a", "v"):
            s = log.series(name)
            assert np.array_equal(s.v, np.arange(len(s)))   # no holes, no reordering
            assert s.t[-1] > ran - 1.0 - 0.6                # 1 s budget (+ process start slack)
        assert log.streams["s"].raw.shape == (len(log.series("a")), 4)
        last = log.t1
    ended, duration = sqlite3.connect(path).execute("SELECT ended_at, duration_s FROM drives").fetchone()
    assert ended == pytest.approx(1000.0 + last) and duration == pytest.approx(last, abs=0.05)


# ---- schema and migrations ----

def test_fresh_database_settings_and_schema_version(tmp_path):
    ref = make(tmp_path / "a.db")
    conn = sqlite3.connect(ref.db)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA application_id").fetchone()[0] == schema.APPLICATION_ID
    assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2            # incremental
    latest = schema.MIGRATIONS[-1].version
    assert conn.execute("PRAGMA user_version").fetchone()[0] == latest
    rows = conn.execute("SELECT version, description FROM schema_version ORDER BY version").fetchall()
    assert [r[0] for r in rows] == [m.version for m in schema.MIGRATIONS] and rows[0][1]
    assert schema_version(conn) == latest
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"drives", "channels", "streams", "samples", "chunks", "chunk_stats", "events", "alarms",
            "captures", "address_maps", "schema_version"} <= tables
    ours = connect(ref.db)
    assert ours.execute("PRAGMA synchronous").fetchone()[0] == 1            # NORMAL
    with pytest.raises(sqlite3.OperationalError):
        ours.execute("DELETE FROM drives")                                  # readers cannot write
    ours.close()


def test_migrations_run_once_in_order_and_keep_the_data(tmp_path, monkeypatch):
    ref = make(tmp_path / "a.db")
    base = schema.MIGRATIONS[-1].version
    calls = []

    def add_weather(conn):
        calls.append("weather")
        conn.execute("ALTER TABLE drives ADD COLUMN weather TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE drives SET weather = 'dry'")

    def add_fuel(conn):
        calls.append("fuel")
        conn.execute("CREATE TABLE fills(id INTEGER PRIMARY KEY, drive_id INTEGER, litres REAL)")

    def broken(conn):
        conn.execute("CREATE TABLE half_done(x)")
        raise RuntimeError("boom")

    grown = [*schema.MIGRATIONS, schema.Migration(base + 1, "weather per drive", add_weather),
             schema.Migration(base + 2, "fuel fills", add_fuel)]
    monkeypatch.setattr(schema, "MIGRATIONS", grown)
    with open_log(ref) as log:                                # the first open migrates
        assert log.rows == 220 and log.series("rpm").v[-1] == 999
    assert calls == ["weather", "fuel"]
    conn = sqlite3.connect(ref.db)
    assert conn.execute("SELECT weather FROM drives").fetchone()[0] == "dry"
    assert [r[0] for r in conn.execute("SELECT version FROM schema_version ORDER BY 1")][-2:] == [base + 1, base + 2]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == base + 2
    assert conn.execute(f"SELECT count(*) FROM {ref.name}").fetchone()[0] == 200       # views were rebuilt
    open_log(ref).close()
    assert calls == ["weather", "fuel"]                       # not again

    monkeypatch.setattr(schema, "MIGRATIONS", [*grown, schema.Migration(base + 3, "breaks", broken)])
    with pytest.raises(RuntimeError):
        open_log(ref)
    assert schema_version(conn) == base + 2                   # the failed step left nothing behind
    assert not schema.has_table(conn, "half_done")

    monkeypatch.setattr(schema, "MIGRATIONS", grown[:-2])     # an older program meets the newer file
    with pytest.raises(LogFormatError, match="newer"):
        open_log(ref)
    with pytest.raises(LogFormatError, match="newer"):
        Store(ref.db, background=False)


def test_not_a_database(tmp_path):
    bad = tmp_path / "x.db"
    bad.write_bytes(b"HONDALOG" + bytes(64))
    with pytest.raises(LogFormatError):
        open_log(bad)
    with pytest.raises(LogFormatError):
        Store(bad, background=False)
    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE drives(id)")
    conn.commit()
    conn.close()
    with pytest.raises(LogFormatError, match="not a hondalog database"):
        open_log(other)
    with pytest.raises(OSError):
        open_log(tmp_path / "missing.db")
    with pytest.raises(OSError):
        connect(tmp_path / "missing.db")
    empty = Store(tmp_path / "empty.db", background=False)
    empty.close()
    with pytest.raises(KeyError, match="no drives"):
        open_log(tmp_path / "empty.db")


# ---- the views, with nothing but sqlite3 ----

def test_views_are_queryable_with_plain_sqlite3(tmp_path):
    path = tmp_path / "hondalog.db"
    first = make(path)
    with new_drive(path, {"ecu_id": "SIM", "started_unix": 1794900000.0, "epoch_unix": 1794900000.0,
                          "notes": "second drive"}) as drive:
        drive.add_stream("ecu/00+2", [Column("rpm", units="rpm")], source="ecu", raw_len=2, attrs={"addr": 0})
        drive.add_stream("ecu/08+1", [Column("ect", units="°C")], source="ecu", raw_len=1, attrs={"addr": 8})
        drive.add_stream("blackbox/live", [Column("bb_ecub", units="V"), Column("bb_vbat", units="V")],
                         source="blackbox")
        for i in range(400):
            t = i * 0.05
            drive.append("ecu/00+2", t, [3000.0 if t < 15 else 0.0], b"\x2e\xe0", latency=0.0042)
            if i % 20 == 0:
                drive.append("ecu/08+1", t + 0.01, [80.0 + i / 100], b"\x50")
        for i in range(2000):
            t = i * 0.01
            drive.append("blackbox/live", t, [13.8 if t < 14.2 else 0.3, 13.9])
        drive.event(5.0, "marker", "MARK 1")
        drive.capture("stall", 15.0, 5.0, 20.0, rule="stall", reason="rpm", message="rpm dropped to 0")
    second = drive.ref

    conn = sqlite3.connect(path)            # no hondalog code from here on
    views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert {first.name, second.name, second.name + "_blackbox", "stalls", "drive_summary", "readings",
            "ecu_reads", "blackbox_1s", "markers"} <= views
    assert all(name.replace("_", "").isalnum() for name in views)             # SQL-safe without quoting
    assert first.name != second.name and second.name.startswith("drive_2026_11_")

    cols = [d[0] for d in conn.execute(f"SELECT * FROM {second.name} LIMIT 1").description]
    assert cols == ["t", "wall_time", "rpm", "ect", "bb_ecub", "bb_vbat"]
    rows = conn.execute(f"SELECT t, wall_time, rpm, ect, bb_ecub FROM {second.name}").fetchall()
    assert len(rows) == 400 and rows[0][2] == 3000.0 and rows[-1][2] == 0.0   # one row per rpm read
    assert rows[0][3] is None and rows[1][3] == 80.0                          # ect: read 10 ms later, then held
    assert rows[45][3] == pytest.approx(80.4)                                 # ... the last value read
    assert rows[100][4] == pytest.approx(13.8) and rows[-1][4] == pytest.approx(0.3)
    assert len(rows[0][1]) == 23 and rows[0][1][4] == "-"                     # 2026-11-16 07:20:00.000
    assert conn.execute(f"SELECT max(rpm), min(rpm), count(ect) FROM {second.name} WHERE t >= 1").fetchone() \
        == (3000.0, 0.0, 380)
    bb = conn.execute(f"SELECT t, bb_ecub_min, bb_ecub_mean, bb_ecub_max FROM {second.name}_blackbox "
                      "WHERE t = 14").fetchone()
    assert bb == (14.0, pytest.approx(0.3), pytest.approx(13.8 * 0.2 + 0.3 * 0.8), pytest.approx(13.8))

    assert conn.execute("SELECT drive, status, rows, stalls, captures, markers, channels, notes "
                        "FROM drive_summary ORDER BY drive_id").fetchall() == [
        (first.name, "complete", 220, 0, 0, 1, 3, ""), (second.name, "complete", 2420, 1, 1, 1, 4, "second drive")]
    stall = conn.execute("SELECT name, drive, reason, message, t_trigger, t_start, t_end, rows FROM stalls").fetchall()
    assert len(stall) == 1 and stall[0][1:7] == (second.name, "rpm", "rpm dropped to 0", 15.0, 5.0, 20.0)
    inside = sum(int(((t >= 5.0) & (t <= 20.0)).sum()) for t in (
        np.arange(400) * 0.05, np.arange(0, 400, 20) * 0.05 + 0.01, np.arange(2000) * 0.01))
    assert stall[0][0].startswith("stall_") and stall[0][7] == inside

    # the README's question: ECU +B and RPM in the last 5 s before every stall
    rpm = conn.execute("""
        SELECT s.name, round(r.t - s.t_trigger, 2), r.value
        FROM stalls s JOIN readings r ON r.drive_id = s.drive_id AND r.channel = 'rpm'
        WHERE r.t BETWEEN s.t_trigger - 5 AND s.t_trigger ORDER BY r.t""").fetchall()
    ecub = conn.execute("""
        SELECT s.name, b.t - s.t_trigger, b.min, b.mean, b.samples
        FROM stalls s JOIN blackbox_1s b ON b.drive_id = s.drive_id AND b.channel = 'bb_ecub'
        WHERE b.t BETWEEN s.t_trigger - 5 AND s.t_trigger ORDER BY b.t""").fetchall()
    assert [row[1] for row in ecub] == [-5, -4, -3, -2, -1, 0] and len(rpm) == 101
    assert ecub[3][2] == pytest.approx(13.8) and ecub[4][2] == pytest.approx(0.3) and ecub[4][4] == 100
    assert rpm[-2][2] == 3000.0 and rpm[-1][2] == 0.0         # +B fell in second 14, rpm a second later
    plan = " ".join(r[3] for r in conn.execute(
        "EXPLAIN QUERY PLAN SELECT r.value FROM stalls s JOIN readings r ON r.drive_id = s.drive_id "
        "AND r.channel = 'rpm' WHERE r.t BETWEEN s.t_trigger - 5 AND s.t_trigger"))
    assert "t>? AND t<?" in plan                               # a range search, not a scan of the drive
    assert conn.execute("SELECT count(*), min(t), max(value) FROM readings WHERE drive = ? AND channel = 'ect'",
                        (second.name,)).fetchone() == (20, 0.01, pytest.approx(83.8))

    reads = conn.execute("SELECT t, addr, block, raw, raw_hex, latency_ms FROM ecu_reads WHERE drive = ? "
                         "ORDER BY t LIMIT 2", (second.name,)).fetchall()
    assert reads == [(0.0, 0, "ecu/00+2", b"\x2e\xe0", "2EE0", 4.2), (0.01, 8, "ecu/08+1", b"\x50", "50", None)]
    assert conn.execute("SELECT count(*) FROM ecu_reads").fetchone()[0] == 200 + 400 + 20
    assert conn.execute("SELECT drive, t, kind, text FROM markers WHERE drive = ?", (first.name,)).fetchall() == [
        (first.name, 1.0, "marker", "MARK 1"), (first.name, 2.5, "trigger", "STALL (rpm 0)"),
        (first.name, 3.5, "alarm", "ECT high")]
    assert conn.execute("SELECT name, state, severity, channel, value, unverified FROM alarms").fetchall() == [
        ("ect_high", "raised", "warn", "ect", 106.0, 1)]
    assert conn.execute("SELECT channel, units, min, max FROM blackbox_1s WHERE t = 14 AND channel = 'bb_vbat'"
                        ).fetchone() == ("bb_vbat", "V", pytest.approx(13.9), pytest.approx(13.9))
    assert json.loads(conn.execute("SELECT attrs FROM streams WHERE drive_id = 1 AND id = 1").fetchone()[0]) \
        == {"addr": 0, "length": 2}
    assert conn.execute("SELECT count(*) FROM address_maps").fetchone()[0] == 1


def test_readme_sql_examples_run(tmp_path):
    """Every query in the README's SQL section runs against a recorded
    database and returns rows (the drive name is the only thing swapped in)."""
    import re
    from pathlib import Path
    from hondalog import addressmap
    from hondalog.logger import Recorder
    rec = Recorder(addressmap.load_default(), "SIM", "sim:", out_dir=tmp_path, sync_captures=True, fsync=False,
                   epoch=__import__("datetime").datetime(2026, 11, 16, 7, 12))
    for i in range(1200):
        t = i * 0.05
        rpm = 800 if t < 50 else 0
        rec.feed_read(t, 0x00, bytes([(rpm * 4) >> 8, (rpm * 4) & 255, 0]), 0.004)
        rec.feed_read(t + 0.01, 0x12, bytes([60, 141, 24, 2]))
        rec.feed_read(t + 0.02, 0x20, bytes([128, 128, 0]))
        if i % 10 == 0:
            rec.feed_read(t + 0.03, 0x08, bytes([0x20, 0x60, 0, 0, 0, 0, 0, 0, 0, 0]))
            rec.feed_read(t + 0.04, 0x17, bytes([145, 128, 0]))
        rec.feed_values("blackbox/live", t, {"bb_ecub": 13.8 if t < 49 else 0.2, "bb_vbat": 13.9}, source="blackbox")
        if i == 400:
            rec.mark("MARK 1")
    rec.stop()
    assert rec.events.get_nowait().reason == "rpm"
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## Querying your drives with SQL"):]
    block = re.search(r"```sql\n(.*?)```", section, re.S).group(1)
    queries = [q.strip() for q in block.split(";") if q.strip()]
    assert len(queries) >= 4
    conn = sqlite3.connect(rec.db)
    for query in queries:
        rows = conn.execute(query.replace("drive_2026_11_16_0712", rec.path.name)).fetchall()
        assert rows, query
    names = re.findall(r"^\| `([a-z_0-9.]+)` \|", section, re.M)       # the table of views
    views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert {"drive_summary", "stalls", "readings", "blackbox_1s", "ecu_reads", "markers"} <= set(names) <= views | {
        "drive_2026_11_16_0712", "drive_..._blackbox"}
    assert rec.path.name + "_blackbox" in views


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="the sqlite3 shell is not installed")
def test_the_sqlite3_shell_reads_a_drive(tmp_path):
    ref = make(tmp_path / "a.db")
    out = subprocess.run(["sqlite3", str(ref.db), f"SELECT count(*), max(rpm) FROM {ref.name}"],
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == "200|999.0"


def test_deleting_a_drive_removes_its_rows_and_views(tmp_path):
    path = tmp_path / "a.db"
    first, second = make(path), make(path)
    assert first.name != second.name and second.name == first.name + "_2"        # same minute: made unique
    conn = connect(path, write=True)
    assert db.delete_drives(conn, [first.name, "nope"]) == [first.name]
    conn.close()
    plain = sqlite3.connect(path)
    assert plain.execute("SELECT name FROM drives").fetchall() == [(second.name,)]
    assert plain.execute("SELECT count(*), count(DISTINCT drive_id) FROM samples").fetchone() == (220, 1)
    assert plain.execute("SELECT count(*) FROM events").fetchone()[0] == 3
    assert plain.execute("SELECT count(*) FROM address_maps").fetchone()[0] == 1   # still used by the other
    views = {r[0] for r in plain.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert first.name not in views and second.name in views and not first.exists() and second.exists()
    info = vacuum(path, full=True)
    assert info["check"] == "ok" and info["after"] <= info["before"]
    assert open_log(second).rows == 220


# ---- the black box in chunks ----

def bb_rows(n, rate=1000.0, seed=3):
    rng = np.random.default_rng(seed)
    info = StreamInfo("blackbox/live", (Column("bb_ecub", units="V"), Column("bb_rpm", units="rpm"),
                                        Column("bb_on", "u1", kind="flag"), Column("odd", "f8")),
                      source="blackbox", raw_len=2)
    rows = np.zeros(n, info.dtype)
    rows["t"] = 5.0 + np.arange(n) / rate + rng.uniform(0, 1e-5, n)
    rows["bb_ecub"] = np.round(13.8 + rng.normal(0, 0.05, n), 3)              # millivolts, like the box
    rows["bb_rpm"] = np.round(3000 + 500 * np.sin(np.arange(n) / 300))
    rows["bb_on"] = rng.integers(0, 2, n)
    rows["odd"] = rng.normal(0, 1, n)                                         # fits no decimal scale
    rows["odd"][::97] = np.nan
    rows["raw"] = rng.integers(0, 256, (n, 2))
    return info, rows


def test_codec_is_lossless_and_small():
    info, rows = bb_rows(20_000)
    blob = codec.encode(rows)
    back = codec.decode(codec.PACKED, blob, info.dtype, len(rows))
    assert back.tobytes() == rows.tobytes()                                   # bit for bit, NaN included
    assert codec.decode(codec.RAW, codec.encode(rows, codec.RAW), info.dtype, len(rows)).tobytes() == rows.tobytes()
    quiet = np.zeros(len(rows), [("t", "<f8"), ("bb_ecub", "<f4"), ("bb_rpm", "<f4"), ("bb_on", "u1")])
    for name in quiet.dtype.names:
        quiet[name] = rows[name]
    quiet["t"] = 5.0 + np.arange(len(rows)) / 1000.0          # the box's own clock: evenly spaced
    assert len(codec.encode(quiet)) < 0.25 * quiet.nbytes                     # 17 bytes a row -> about 3
    for part in (rows[:1], rows[:0], rows[::-1].copy()):
        assert codec.decode(codec.PACKED, codec.encode(part), info.dtype, len(part)).tobytes() == part.tobytes()
    with pytest.raises(ValueError):
        codec.decode("zstd9", blob, info.dtype, len(rows))
    with pytest.raises(ValueError):
        codec.encode(rows, "zstd9")


def test_black_box_chunks_round_trip(tmp_path):
    """Row by row (raw chunks per commit, packed once a minute), whole arrays, and windows."""
    info, rows = bb_rows(90_000)                              # 90 s at 1 kHz
    path = tmp_path / "a.db"
    store = Store(path, background=False, compact_s=30.0)
    drive = store.begin_drive({"epoch_unix": 1794812400.0})
    drive.add_stream(info.name, info.columns, source="blackbox", raw_len=2)
    names = info.column_names
    plain = sqlite3.connect(path)
    for start in range(0, len(rows), 500):                    # a commit every 0.5 s of data
        for row in rows[start:start + 500]:
            drive.append(info.name, float(row["t"]), [row[n].item() for n in names], row["raw"].tobytes())
        store.flush()
        if start == 20_000:
            assert plain.execute("SELECT DISTINCT codec FROM chunks").fetchall() == [("raw",)]
            assert plain.execute("SELECT count(*) FROM chunks").fetchone()[0] == 41
    kinds = dict(plain.execute("SELECT codec, count(*) FROM chunks GROUP BY codec").fetchall())
    assert kinds["d1"] == 2 and kinds["raw"] < 60             # two packed half-minutes, the tail still raw
    with open_log(drive.ref) as live:                         # both kinds read as one stream
        assert live.streams[info.name].data.tobytes() == rows.tobytes()
    drive.end()
    store.close()
    assert plain.execute("SELECT codec, count(*) FROM chunks GROUP BY codec").fetchall() == [("d1", 3)]
    assert plain.execute("SELECT sum(n_rows), min(t_start), max(t_end) FROM chunks").fetchone() == (
        90_000, pytest.approx(rows["t"][0]), pytest.approx(rows["t"][-1]))
    stored = plain.execute("SELECT sum(length(data)) FROM chunks").fetchone()[0]
    assert stored < 0.55 * rows.nbytes                        # with a column of pure noise in it

    with open_log(drive.ref) as log:
        stream = log.streams[info.name]
        assert stream.rows == 90_000 and stream.info.source == "blackbox" and stream.info.raw_len == 2
        assert stream.data.tobytes() == rows.tobytes()        # exactly what went in
        assert log.series("bb_ecub").rate_hz == pytest.approx(1000, rel=0.02)
        cut = log.window(40.0, 41.5)
        keep = (rows["t"] >= 40.0) & (rows["t"] <= 41.5)
        assert cut.streams[info.name].data.tobytes() == rows[keep].tobytes()

    # one row per channel per second, also for seconds that straddle two packed blocks
    sec = np.floor(rows["t"])
    for second in (5.0, 34.0, 35.0, 64.0, 94.0):
        mine = rows["bb_ecub"][sec == second].astype(np.float64)
        n, lo, mean, hi = plain.execute("SELECT samples, min, mean, max FROM blackbox_1s WHERE channel = 'bb_ecub' "
                                        "AND t = ?", (second,)).fetchone()
        assert (n, lo, hi) == (len(mine), mine.min(), mine.max()) and mean == pytest.approx(mine.mean())
    odd = rows["odd"][sec == 50.0]
    assert plain.execute("SELECT samples FROM blackbox_1s WHERE channel = 'odd' AND t = 50").fetchone()[0] \
        == int(np.isfinite(odd).sum())                        # NaN is not counted
    assert plain.execute("SELECT count(*) FROM blackbox_1s").fetchone()[0] == 4 * 90

    # whole arrays (imports, copies) are packed directly; a capture window counts chunk rows exactly
    copy = write_log(open_log(drive.ref).load_all(), tmp_path / "b.db")
    with open_log(copy) as log:
        assert log.status == "imported" and log.streams[info.name].data.tobytes() == rows.tobytes()
    with new_drive(tmp_path / "c.db") as w:
        w.add_stream(info.name, info.columns, source="blackbox", raw_len=2)
        w.append_array(info.name, rows[::-1])                 # out of order: sorted on the way in
        ref, n = w.capture("stall", 41.0, 40.0, 41.5)
    assert n == int(keep.sum()) and ref.capture.startswith("stall_")
    with open_log(ref) as cap:
        assert cap.rows == n and cap.streams[info.name].data.tobytes() == rows[keep].tobytes()
        assert cap.meta["capture_of"] == w.name and cap.t0 >= 40.0 and cap.t1 <= 41.5


def test_storage_choice_and_low_rate_streams_stay_rows(tmp_path):
    with new_drive(tmp_path / "a.db") as w:
        w.add_stream("math", [Column("afr")], source="math")
        w.add_stream("blackbox/rails", [Column("v")], source="blackbox")
        w.add_stream("blackbox/slow", [Column("temp")], source="blackbox", storage="rows")
        w.add_stream("ecu/fast", [Column("x")], source="ecu", storage="chunks")
        for name in ("math", "blackbox/rails", "blackbox/slow", "ecu/fast"):
            w.append(name, 1.0, [2.0])
    conn = sqlite3.connect(tmp_path / "a.db")
    assert dict(conn.execute("SELECT name, storage FROM streams").fetchall()) == {
        "math": "rows", "blackbox/rails": "chunks", "blackbox/slow": "rows", "ecu/fast": "chunks"}
    assert conn.execute("SELECT count(*) FROM samples").fetchone()[0] == 2
    with open_log(w.ref) as log:
        assert all(log.series(n).v.tolist() == [2.0] for n in ("afr", "v", "temp", "x"))


# ---- the writer thread ----

def test_background_writer_commits_on_its_own(tmp_path):
    path = tmp_path / "a.db"
    before = {t for t in threading.enumerate() if t.name == "hondalog-store"}
    store = Store(path, flush_interval=0.05)
    drive = store.begin_drive()
    drive.add_stream("s", [Column("a")])
    for i in range(50):
        drive.append("s", i * 0.01, [i])
    writer = [t for t in threading.enumerate() if t.name == "hondalog-store" and t not in before]
    assert len(writer) == 1 and writer[0] is not threading.current_thread()
    deadline = time.monotonic() + 3
    rows = 0
    while rows < 50 and time.monotonic() < deadline:
        time.sleep(0.05)
        with open_log(drive.ref) as log:
            rows = log.rows
    assert rows == 50                           # in the database without flush() or close()
    store.close()
    store.close()                               # twice is harmless
    assert store.error is None and store.closed and drive.closed and drive.rows == 50
    assert not writer[0].is_alive()
    with pytest.raises(ValueError):
        store.begin_drive()


def test_reading_an_old_drive_while_another_is_recorded(tmp_path):
    """What the app does all day: Replay opens an old drive (and the running
    one) from the UI thread while the recorder commits twice a second."""
    path = tmp_path / "hondalog.db"
    old = make(path, rows=2000)
    store = Store(path, flush_interval=0.02)
    drive = store.begin_drive({"ecu": "P2P"})
    drive.add_stream("ecu/00+2", [Column("rpm")], source="ecu", raw_len=2)
    drive.add_stream("blackbox/live", [Column("v")], source="blackbox")
    stop = threading.Event()
    fed = [0]

    def feed():
        i = 0
        while not stop.is_set():
            drive.append("ecu/00+2", i * 0.001, [float(i)], b"ab")
            drive.append("blackbox/live", i * 0.001, [float(i)])
            i += 1
            fed[0] = i
            if i % 200 == 0:
                time.sleep(0.001)
    feeder = threading.Thread(target=feed)
    feeder.start()
    try:
        seen = []
        deadline = time.monotonic() + 1.5
        opens = 0
        while time.monotonic() < deadline:
            with open_log(old) as log:                        # the old drive: always whole
                assert log.status == "complete" and len(log.series("rpm")) == 2000
                assert log.series("rpm").v[-1] == 2799 and len(log.events) == 4
            with open_log(drive.ref) as live:                 # the running one: a growing, gap-free prefix
                assert live.status == "recording"
                for name in live.channels:                    # none before the first commit
                    v = live.series(name).v
                    assert np.array_equal(v, np.arange(len(v)))
                seen.append(len(live.series("rpm")) if live.channels else 0)
            opens += 1
        assert opens > 10 and seen[-1] > seen[0] and seen == sorted(seen)
    finally:
        stop.set()
        feeder.join()
        store.close()
    assert store.error is None
    with open_log(drive.ref) as log:
        assert log.status == "complete" and len(log.series("rpm")) == fed[0] == len(log.series("v"))
    assert open_log(old).rows == 2200


def test_write_errors_are_kept_and_feeding_goes_on(tmp_path):
    store = Store(tmp_path / "a.db", background=False)
    drive = store.begin_drive()
    drive.add_stream("s", [Column("a")])
    drive.append("s", 0.0, [1.0])
    store.flush()
    real = store._conn

    class Full:
        def execute(self, sql, *args):
            if sql.startswith("BEGIN"):
                raise sqlite3.OperationalError("database or disk is full")
            return real.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(real, name)
    store._conn = Full()
    drive.append("s", 1.0, [2.0])
    with pytest.raises(OSError, match="disk is full"):
        store.flush()
    drive.append("s", 2.0, [3.0])                             # still accepted
    assert isinstance(store.error, OSError)
    store._conn = real
    store.error = None
    store.close()
    assert open_log(drive.ref).series("a").v.tolist() == [1.0, 3.0]    # the failed batch is gone, the rest is not


def test_rows_wait_while_another_process_has_the_database(tmp_path):
    path = tmp_path / "a.db"
    store = Store(path, background=False, timeout=0.05)
    drive = store.begin_drive()
    drive.add_stream("s", [Column("a")])
    drive.add_stream("blackbox/live", [Column("v")], source="blackbox")
    drive.append("s", 0.0, [1.0])
    store.flush()
    other = sqlite3.connect(path)
    other.isolation_level = None
    other.execute("BEGIN IMMEDIATE")                          # say, an import running in another terminal
    drive.append("s", 1.0, [2.0])
    drive.append("blackbox/live", 1.0, [5.0])
    drive.event(1.0, "marker", "MARK 1")
    with pytest.raises(OSError, match="locked"):
        store.flush()
    drive.append("s", 2.0, [3.0])
    other.execute("COMMIT")
    store.error = None
    store.close()                                             # the kept batch goes first, in order
    with open_log(drive.ref) as log:
        assert log.series("a").v.tolist() == [1.0, 2.0, 3.0] and log.series("v").v.tolist() == [5.0]
        assert log.markers == [(1.0, "MARK 1")]


def test_recovery_leaves_a_live_writer_alone(tmp_path):
    """Another process that is recording right now is not "crashed": its
    pid is alive and it has been heard from in the last 30 s."""
    path = tmp_path / "a.db"
    ref = make(path)
    conn = sqlite3.connect(path)

    def pretend(pid, heartbeat_age):
        conn.execute("UPDATE drives SET status='recording', writer_pid=?, heartbeat_at=?, ended_at=NULL",
                     (pid, time.time() - heartbeat_age))
        conn.commit()
        with open_log(ref) as log:
            return log.status
    assert pretend(os.getppid(), 2.0) == "recording"          # alive and fresh
    assert pretend(os.getppid(), 300.0) == "crashed"          # alive but silent: a recycled pid
    assert pretend(2 ** 22 - 3, 1.0) == "crashed"             # no such process
    assert pretend(None, 1.0) == "crashed"
    assert pretend(os.getpid(), 1.0) == "crashed"             # us, but no Store of ours is writing it
    assert conn.execute("SELECT end_reason, writer_pid FROM drives").fetchone() == ("crash", None)


# ---- the Log object ----

def test_annotate_keeps_the_data(tmp_path):
    ref = make(tmp_path / "a.db")
    annotate(ref, notes="idle hunting", tags=["idle"])
    annotate(str(ref), notes="idle hunting, fixed by cleaning IACV", keep=True, dtc=["P0505"])
    with open_log(ref) as log:
        assert log.rows == 220 and log.meta["notes"].endswith("IACV") and log.meta["tags"] == ["idle"]
        assert log.meta["keep"] is True and log.meta["dtc"] == ["P0505"] and log.meta["ecu"] == "P2P"
    with pytest.raises(ValueError):
        annotate(tmp_path / "nope.csv", notes="x")


def test_series_merges_a_channel_read_in_two_streams(tmp_path):
    with new_drive(tmp_path / "a.db") as w:
        w.add_stream("fast", [Column("rpm")])
        w.add_stream("slow", [Column("rpm"), Column("ect")])
        for i in range(10):
            w.append("fast", i * 0.1, [i])
        w.append("slow", 0.25, [100, 80])
        w.append("slow", 0.75, [200, 81])
    log = open_log(w.ref)
    s = log.series("rpm")
    assert len(s) == 12 and np.all(np.diff(s.t) >= 0)
    assert s.v[3] == 100 and s.v[9] == 200
    assert len(log.streams_with("rpm")) == 2 and log.channels == ["rpm", "ect"]
    cols = [d[0] for d in sqlite3.connect(w.ref.db).execute(f"SELECT * FROM {w.name}").description]
    assert cols == ["t", "wall_time", "rpm", "ect"]           # the view shows such a channel once


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
    out, rows = extract_window([synth_path], tmp_path / "cut.db", 100.0, 140.0, {"trigger": "manual"})
    assert out.db == tmp_path / "cut.db"
    with open_log(out) as cut, open_log(synth_path) as full:
        assert set(cut.streams) == set(full.streams)             # ECU and black box in one bundle
        assert cut.rows == rows and cut.t0 >= 100 and cut.t1 <= 140
        assert cut.meta["trigger"] == "manual" and cut.meta["ecu"] == full.meta["ecu"]
        assert cut.streams["ecu/00+2"].raw.shape[1] == 2
        assert cut.streams["blackbox/rails"].rows == pytest.approx(40 * 50, abs=2)
        assert [e.text for e in cut.events] == [e.text for e in full.events if 100 <= e.t <= 140]
        lo, hi = np.searchsorted(full.series("rpm").t, [100.0, 140.0])
        np.testing.assert_array_equal(cut.series("rpm").v, full.series("rpm").v[lo:hi])


def test_extract_joins_two_drives_of_one_session(tmp_path):
    refs = []
    for t0 in (0.0, 10.0):
        with new_drive(tmp_path / "a.db", {"session": "one"}) as w:
            w.add_stream("s", [Column("a")])
            for i in range(100):
                w.append("s", t0 + i * 0.1, [t0 + i * 0.1])
        refs.append(w.ref)
    out, rows = extract_window(refs, tmp_path / "j.db", 8.0, 12.0)
    s = open_log(out).series("a")
    assert rows == len(s) == 41 and s.t[0] == pytest.approx(8.0) and np.all(np.diff(s.t) > 0)


def test_in_memory_log_and_write_log(tmp_path):
    info = StreamInfo("m", (Column("x", "f8"),), source="math")
    rows = np.zeros(5, info.dtype)
    rows["t"] = np.arange(5.0)
    rows["x"] = np.arange(5.0) ** 2
    log = Log({"k": 1}, [Stream(info, data=rows)], [Event(2.0, "marker", "m")])
    assert log.rows == 5 and log.series("x").v[3] == 9 and log.path is None and log.status == "memory"
    ref = write_log(log, tmp_path / "m.db", {"extra": True}, name="drive_test")
    assert ref.name == "drive_test"
    # any name becomes one SQL can use unquoted, and never shadows a table or a shared view
    odd = [write_log(log, tmp_path / "m.db", name=n).name for n in ("samples", "stalls", "2nd try; DROP", "drive_test")]
    assert odd == ["samples_2", "stalls_2", "drive_2nd_try_DROP", "drive_test_2"]
    plain = sqlite3.connect(tmp_path / "m.db")
    assert all(plain.execute(f"SELECT count(*) FROM {n}").fetchone() == (5,) for n in odd)
    assert plain.execute("SELECT count(*) FROM samples").fetchone()[0] == 25
    back = open_log(ref).load_all()
    assert back.meta["k"] == 1 and back.meta["extra"] is True and back.status == "imported"
    assert back.series("x").v.tolist() == [0, 1, 4, 9, 16]
    assert back.streams["m"].info.source == "math" and back.markers == [(2.0, "m")]
    empty = Log()
    assert empty.t0 == 0 and empty.duration == 0 and empty.channels == [] and len(empty.table()) == 0


def test_stream_loads_after_close(synth_path):
    log = open_log(synth_path)
    log.close()
    assert len(log.series("rpm")) > 9000          # reopens the database for the load
    assert len(log.series("vbatt_mean")) == 30000


def test_default_database_location(tmp_path, monkeypatch):
    monkeypatch.delenv("HONDALOG_DB", raising=False)
    monkeypatch.setenv("HONDALOG_DIR", str(tmp_path / "data"))
    assert db_path() == tmp_path / "data" / "hondalog.db"
    assert db_path(tmp_path / "x") == tmp_path / "x" / "hondalog.db" and db_path(tmp_path / "y.db") == tmp_path / "y.db"
    monkeypatch.setenv("HONDALOG_DB", str(tmp_path / "elsewhere.db"))
    assert db_path() == tmp_path / "elsewhere.db"
    monkeypatch.delenv("HONDALOG_DB")
    monkeypatch.delenv("HONDALOG_DIR")
    home = os.path.expanduser("~/hondalog")
    assert str(db_path()) == f"{home}/hondalog.db" and str(db.files_dir()) == f"{home}/logs"
    assert db.files_dir(tmp_path / "y.db") == tmp_path and db.files_dir(tmp_path / "x") == tmp_path / "x"
    ref = make(db_path(tmp_path / "data"))
    monkeypatch.setenv("HONDALOG_DIR", str(tmp_path / "data"))
    assert open_log(ref.name).rows == 220          # a bare drive name: the default database


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
