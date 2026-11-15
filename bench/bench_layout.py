#!/usr/bin/env python3
"""Which table layout for the decoded ECU values? Run from the repo root:

    python3 bench/bench_layout.py

Writes one hour of K-line reads (three fast blocks at 16 Hz, one slow block
at 1.3 Hz, 11 channels, raw bytes kept) into two candidate layouts and
measures file size, write time, and the reads the app does.

  long   ecu_reads(drive, stream, t, raw, latency) for the raw bytes, plus
         one row per decoded value: samples(drive, channel, t, value),
         clustered on (drive, channel, t)
  wide   one row per read: samples(drive, stream, part, t, raw, latency,
         v0..v3), clustered on (drive, stream, part, t); a channel is a
         (stream, part, slot) triple. This is hondalog/data/schema.py.

The numbers this printed on the development machine are in
docs/ARCHITECTURE.md ("Storage layout of the decoded values").
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hondalog.data.schema import SLOTS            # noqa: E402
from hondalog.data.synth import FAST, SLOW, drive  # noqa: E402

DURATION = 3600.0
COMMIT_S = 0.5

LONG = """
CREATE TABLE ecu_reads(drive_id INTEGER, stream_id INTEGER, t REAL, n INTEGER DEFAULT 0, raw BLOB,
                       latency_us INTEGER, PRIMARY KEY(drive_id, stream_id, t, n)) WITHOUT ROWID;
CREATE TABLE samples(drive_id INTEGER, channel_id INTEGER, t REAL, n INTEGER DEFAULT 0, value REAL,
                     PRIMARY KEY(drive_id, channel_id, t, n)) WITHOUT ROWID;
"""
WIDE = f"""
CREATE TABLE samples(drive_id INTEGER, stream_id INTEGER, part INTEGER DEFAULT 0, t REAL,
                     n INTEGER DEFAULT 0, raw BLOB, latency_us INTEGER,
                     {", ".join(f"v{i} REAL" for i in range(SLOTS))},
                     PRIMARY KEY(drive_id, stream_id, part, t, n)) WITHOUT ROWID;
"""


def reads(seed: int = 1):
    """Every read of the hour in time order: (t, stream index, raw bytes, values)."""
    rng = np.random.default_rng(seed)
    out = []
    n_fast = int(DURATION * 16)
    base = np.arange(n_fast) / 16.0 + rng.uniform(0, 0.004, n_fast)
    blocks = [*FAST, SLOW]
    for k, (_name, _addr, length, channels) in enumerate(blocks):
        t = base + k * 0.018 if k < len(FAST) else np.arange(int(DURATION * 1.3)) / 1.3 + 0.05
        values = drive(t, rng)
        # what a decoded 8-bit or 16-bit register looks like: a few thousand distinct values
        cols = np.stack([np.round(values[c].astype(np.float32), 2).astype(np.float64) for c in channels], axis=1)
        raw = rng.integers(0, 256, (len(t), length), dtype=np.uint8)
        out += [(float(t[i]), k, raw[i].tobytes(), cols[i].tolist()) for i in range(len(t))]
    out.sort(key=lambda r: r[0])
    return out, blocks


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def batches(rows):
    """Split the hour into the 0.5 s commits the recorder makes."""
    start, edge = 0, COMMIT_S
    for i, row in enumerate(rows):
        if row[0] >= edge:
            yield rows[start:i]
            start = i
            edge = row[0] - row[0] % COMMIT_S + COMMIT_S
    yield rows[start:]


def write_long(path: Path, rows, blocks) -> float:
    conn = connect(path)
    conn.executescript(LONG)
    first = np.cumsum([0] + [len(b[3]) for b in blocks])
    start = time.perf_counter()
    for batch in batches(rows):
        conn.executemany("INSERT INTO ecu_reads(drive_id, stream_id, t, raw, latency_us) VALUES(1,?,?,?,4370)",
                         [(k, t, raw) for t, k, raw, _ in batch])
        conn.executemany("INSERT INTO samples(drive_id, channel_id, t, value) VALUES(1,?,?,?)",
                         [(int(first[k]) + j, t, v) for t, k, _, vals in batch for j, v in enumerate(vals)])
        conn.commit()
    took = time.perf_counter() - start
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return took


def write_wide(path: Path, rows, blocks) -> float:
    conn = connect(path)
    conn.executescript(WIDE)
    assert all(len(b[3]) <= SLOTS for b in blocks)
    sql = {k: "INSERT INTO samples(drive_id, stream_id, t, raw, latency_us, "
              + ", ".join(f"v{j}" for j in range(len(b[3]))) + ") VALUES(1,?,?,?,4370,"
              + ",".join("?" * len(b[3])) + ")" for k, b in enumerate(blocks)}
    start = time.perf_counter()
    for batch in batches(rows):
        by: dict[int, list] = {}
        for t, k, raw, vals in batch:
            by.setdefault(k, []).append((k, t, raw, *vals))
        for k, part in by.items():
            conn.executemany(sql[k], part)
        conn.commit()
    took = time.perf_counter() - start
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return took


def best(fn, repeat: int = 5):
    out, result = float("inf"), None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        out = min(out, time.perf_counter() - start)
    return out, result


def main() -> None:
    rows, blocks = reads()
    n_channels = sum(len(b[3]) for b in blocks)
    n_values = sum(len(r[3]) for r in rows)
    print(f"{len(rows)} reads, {n_values} decoded values, {n_channels} channels, 1 hour; "
          f"sqlite {sqlite3.sqlite_version}")
    with tempfile.TemporaryDirectory(prefix="hondalog-layout-") as tmp:
        folder = Path(tmp)
        results = {}
        for name, writer in (("long", write_long), ("wide", write_wide)):
            path = folder / f"{name}.db"
            wrote = writer(path, rows, blocks)
            conn = sqlite3.connect(path)
            if name == "long":
                def load_all(conn=conn):
                    out = [np.array(conn.execute(
                        "SELECT t, value FROM samples WHERE drive_id=1 AND channel_id=?", (c,)).fetchall(),
                        dtype=np.float64) for c in range(n_channels)]
                    raw = conn.execute("SELECT t, raw FROM ecu_reads WHERE drive_id=1").fetchall()
                    return out, raw

                def one(conn=conn):
                    return np.array(conn.execute(
                        "SELECT t, value FROM samples WHERE drive_id=1 AND channel_id=0").fetchall())

                def window(conn=conn):
                    return [conn.execute("SELECT t, value FROM samples WHERE drive_id=1 AND channel_id=? "
                                         "AND t BETWEEN 1800 AND 1860", (c,)).fetchall()
                            for c in range(n_channels)]
            else:
                def load_all(conn=conn):
                    out = []
                    for k, b in enumerate(blocks):
                        cols = ", ".join(f"v{j}" for j in range(len(b[3])))
                        got = conn.execute(f"SELECT t, {cols}, raw FROM samples WHERE drive_id=1 "
                                           "AND stream_id=? AND part=0", (k,)).fetchall()
                        out.append(np.array([r[:-1] for r in got], dtype=np.float64))
                    return out

                def one(conn=conn):
                    return np.array(conn.execute(
                        "SELECT t, v0 FROM samples WHERE drive_id=1 AND stream_id=0 AND part=0").fetchall())

                def window(conn=conn):
                    return [conn.execute("SELECT * FROM samples WHERE drive_id=1 AND stream_id=? AND part=0 "
                                         "AND t BETWEEN 1800 AND 1860", (k,)).fetchall()
                            for k in range(len(blocks))]
            results[name] = (path.stat().st_size, wrote, best(load_all, 3)[0], best(one)[0], best(window)[0])
            conn.close()
        print(f"{'':<34}{'long/narrow':>14}{'wide per read':>16}")
        labels = ("file size, MB", "write the hour (0.5 s commits), s", "load every channel + raw, ms",
                  "load one fast channel, ms", "all channels, one 60 s window, ms")
        scale = (1e-6, 1.0, 1e3, 1e3, 1e3)
        for i, label in enumerate(labels):
            a, b = results["long"][i] * scale[i], results["wide"][i] * scale[i]
            print(f"{label:<34}{a:>14.2f}{b:>16.2f}")
        cpu = [results[k][1] / DURATION * 100 for k in ("long", "wide")]
        print(f"{'writer CPU while recording, %':<34}{cpu[0]:>14.3f}{cpu[1]:>16.3f}")


if __name__ == "__main__":
    main()
