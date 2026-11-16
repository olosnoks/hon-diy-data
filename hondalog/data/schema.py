"""The database schema, its version history, and the views.

Everything hondalog records lives in one SQLite file (``hondalog.db``):

    drives        one row per drive: when, which ECU, which address map, how it ended
    address_maps  the map text each drive was decoded with (stored once per map)
    streams       per drive: one K-line read block, the math channels, a black box stream
    channels      per drive: every channel of every stream (units, range, where it is stored)
    samples       one row per read / per sample: t, raw bytes, latency, decoded values v0..v3
    chunks        high-rate streams (the black box): compressed blocks of rows
    chunk_stats   the same streams at 1 Hz (min / mean / max), so plain SQL can see them
    events        markers, trigger firings, notes, link and black box events
    alarms        alarm state changes
    captures      stall and trigger captures: a time range of a drive plus what fired

``samples`` is "wide": a channel is a (stream, part, slot) triple, and slot k
is column ``vk``. bench/bench_layout.py compares this with one row per value.

Views: one per drive (``drive_2026_11_16_0712``: every channel on the time
grid of the fastest stream), ``readings`` (every value of every drive in long
form: K-line reads and math channels), ``blackbox_1s`` (the black box streams,
one row per channel per second), ``ecu_reads`` (the raw K-line bytes),
``markers``, ``stalls``, ``drive_summary``, ``gps_track`` (every GPS fix of
every drive) and ``stall_locations`` (every capture with the GPS fix nearest
its trigger). The shared views are plain joins,
so a condition on drive, channel and time in the query that uses them
becomes an index range search (CROSS JOIN pins the join order: the channel
is looked up first, then its rows by time).

A schema change is a new ``Migration`` at the end of ``MIGRATIONS``. Views
are derived data: they are dropped and made again after any migration.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Callable, Sequence

#: decoded values per ``samples`` row; a wider stream continues in the next part
SLOTS = 4
#: "HDLG": PRAGMA application_id of a hondalog database
APPLICATION_ID = 0x48444C47
SQLITE_MAGIC = b"SQLite format 3\x00"

#: how long a per-drive view holds a value of a slower stream, seconds
VIEW_HOLD_S = 5.0


class LogFormatError(ValueError):
    """Not a hondalog database (or log file), or one this program cannot read."""


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    apply: Callable[[sqlite3.Connection], None]


def run_script(conn: sqlite3.Connection, script: str) -> None:
    """Execute several statements without ``executescript``'s implicit commit."""
    statement = ""
    for line in script.splitlines():
        statement += line + "\n"
        if sqlite3.complete_statement(statement):
            if statement.strip():
                conn.execute(statement)
            statement = ""
    if statement.strip():
        conn.execute(statement)


_V1 = f"""
CREATE TABLE address_maps(
    id       INTEGER PRIMARY KEY,
    sha256   TEXT NOT NULL UNIQUE,
    version  TEXT NOT NULL DEFAULT '',
    tag      TEXT NOT NULL DEFAULT '',
    hash     TEXT NOT NULL DEFAULT '',
    file     TEXT NOT NULL DEFAULT '',
    text     TEXT NOT NULL
);
CREATE TABLE drives(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,              -- also the name of the drive's view
    status          TEXT NOT NULL DEFAULT 'recording', -- recording, complete, crashed, imported
    end_reason      TEXT NOT NULL DEFAULT '',          -- stopped, link_lost, crash, ...
    started_at      REAL,                              -- unix seconds
    started_iso     TEXT,
    ended_at        REAL,
    ended_iso       TEXT,
    duration_s      REAL,
    epoch_unix      REAL,                              -- wall time of session second 0
    t_first         REAL,                              -- session seconds of the first and last row
    t_last          REAL,
    rows            INTEGER NOT NULL DEFAULT 0,
    size_bytes      INTEGER NOT NULL DEFAULT 0,        -- estimate, for retention
    vehicle         TEXT NOT NULL DEFAULT '',
    ecu             TEXT NOT NULL DEFAULT '',
    ecu_id          TEXT NOT NULL DEFAULT '',
    port            TEXT NOT NULL DEFAULT '',
    map_id          INTEGER REFERENCES address_maps(id),
    map_version     TEXT NOT NULL DEFAULT '',
    map_tag         TEXT NOT NULL DEFAULT '',
    map_hash        TEXT NOT NULL DEFAULT '',
    link_stats      TEXT,                              -- JSON: counters and measured timing
    expert_unlock   INTEGER NOT NULL DEFAULT 0,
    unlocked_frames TEXT,                              -- JSON list of hex frames
    read_plan       TEXT,                              -- JSON list of "AA+N" blocks
    notes           TEXT NOT NULL DEFAULT '',
    tags            TEXT NOT NULL DEFAULT '[]',        -- JSON list
    keep            INTEGER NOT NULL DEFAULT 0,
    stall_count     INTEGER NOT NULL DEFAULT 0,
    capture_count   INTEGER NOT NULL DEFAULT 0,
    thumb           TEXT,                              -- JSON: RPM envelope for a sparkline
    meta            TEXT NOT NULL DEFAULT '{{}}',        -- JSON: every other header fact
    writer_pid      INTEGER,
    heartbeat_at    REAL
);
CREATE INDEX drives_by_start ON drives(started_at);
CREATE TABLE streams(
    drive_id  INTEGER NOT NULL,
    id        INTEGER NOT NULL,
    name      TEXT NOT NULL,
    source    TEXT NOT NULL DEFAULT '',                -- ecu, math, blackbox, import, ...
    storage   TEXT NOT NULL DEFAULT 'rows',            -- rows (samples) or chunks
    raw_len   INTEGER NOT NULL DEFAULT 0,
    addr      INTEGER,                                 -- K-line block address, if it is one
    attrs     TEXT NOT NULL DEFAULT '{{}}',
    rows      INTEGER NOT NULL DEFAULT 0,
    t_first   REAL,
    t_last    REAL,
    PRIMARY KEY(drive_id, id),
    UNIQUE(drive_id, name)
) WITHOUT ROWID;
CREATE TABLE channels(
    id        INTEGER PRIMARY KEY,
    drive_id  INTEGER NOT NULL,
    stream_id INTEGER NOT NULL,
    position  INTEGER NOT NULL,                        -- column number in its stream
    part      INTEGER NOT NULL,                        -- samples.part = position / {SLOTS}
    slot      INTEGER NOT NULL,                        -- samples.v<slot> = position % {SLOTS}
    name      TEXT NOT NULL,
    units     TEXT NOT NULL DEFAULT '',
    label     TEXT NOT NULL DEFAULT '',
    kind      TEXT NOT NULL DEFAULT 'value',
    dtype     TEXT NOT NULL DEFAULT 'f4',
    decimals  INTEGER NOT NULL DEFAULT 2,
    lo        REAL,
    hi        REAL,
    source    TEXT NOT NULL DEFAULT '',
    UNIQUE(drive_id, stream_id, position)
);
CREATE INDEX channels_by_name ON channels(drive_id, name);
CREATE TABLE samples(
    drive_id   INTEGER NOT NULL,
    stream_id  INTEGER NOT NULL,
    part       INTEGER NOT NULL DEFAULT 0,
    t          REAL NOT NULL,                          -- session seconds
    n          INTEGER NOT NULL DEFAULT 0,             -- tie-break for equal times
    raw        BLOB,                                   -- the bytes the ECU sent (K-line reads)
    latency_us INTEGER,                                -- estimated age of the reply
    {", ".join(f"v{i} REAL" for i in range(SLOTS))},
    PRIMARY KEY(drive_id, stream_id, part, t, n)
) WITHOUT ROWID;
CREATE TABLE chunks(
    id        INTEGER PRIMARY KEY,
    drive_id  INTEGER NOT NULL,
    stream_id INTEGER NOT NULL,
    t_start   REAL NOT NULL,
    t_end     REAL NOT NULL,
    n_rows    INTEGER NOT NULL,
    codec     TEXT NOT NULL,                           -- see hondalog/data/codec.py
    data      BLOB NOT NULL
);
CREATE INDEX chunks_by_time ON chunks(drive_id, stream_id, t_start);
CREATE TABLE chunk_stats(
    drive_id  INTEGER NOT NULL,
    stream_id INTEGER NOT NULL,
    position  INTEGER NOT NULL,                        -- channels.position
    t         REAL NOT NULL,                           -- start of the second
    n         INTEGER NOT NULL,
    lo        REAL,
    mean      REAL,
    hi        REAL,
    PRIMARY KEY(drive_id, stream_id, position, t)
) WITHOUT ROWID;
CREATE TABLE events(
    id        INTEGER PRIMARY KEY,
    drive_id  INTEGER NOT NULL,
    t         REAL NOT NULL,
    kind      TEXT NOT NULL,                           -- marker, trigger, note, link, blackbox, ...
    text      TEXT NOT NULL DEFAULT '',
    data      TEXT                                     -- JSON
);
CREATE INDEX events_by_time ON events(drive_id, t);
CREATE TABLE alarms(
    id         INTEGER PRIMARY KEY,
    drive_id   INTEGER NOT NULL,
    t          REAL NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    state      TEXT NOT NULL DEFAULT '',               -- raised, cleared, acknowledged
    severity   TEXT NOT NULL DEFAULT '',
    channel    TEXT NOT NULL DEFAULT '',
    value      REAL,
    unverified INTEGER NOT NULL DEFAULT 0,
    text       TEXT NOT NULL DEFAULT '',
    data       TEXT                                    -- JSON: the event as it was recorded
);
CREATE INDEX alarms_by_time ON alarms(drive_id, t);
CREATE TABLE captures(
    id         INTEGER PRIMARY KEY,
    drive_id   INTEGER NOT NULL,
    name       TEXT NOT NULL UNIQUE,                   -- stall_20261116_071455
    kind       TEXT NOT NULL DEFAULT 'capture',        -- stall, capture, alarm, ...
    rule       TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    label      TEXT NOT NULL DEFAULT '',
    message    TEXT NOT NULL DEFAULT '',
    severity   TEXT NOT NULL DEFAULT '',
    t_trigger  REAL NOT NULL,                          -- session seconds, like samples.t
    t_start    REAL NOT NULL,
    t_end      REAL NOT NULL,
    wall_unix  REAL,
    wall_iso   TEXT,
    complete   INTEGER NOT NULL DEFAULT 1,             -- 0: recording stopped inside the window
    rows       INTEGER NOT NULL DEFAULT 0,
    fired      TEXT,                                   -- JSON: every rule that fired in the window
    meta       TEXT NOT NULL DEFAULT '{{}}'
);
CREATE INDEX captures_by_drive ON captures(drive_id, t_trigger);
"""


def _v1(conn: sqlite3.Connection) -> None:
    run_script(conn, _V1)


def _views_only(conn: sqlite3.Connection) -> None:
    """Nothing to change in the tables: the views are made again after every migration."""


MIGRATIONS: list[Migration] = [
    Migration(1, "drives, streams, channels, samples, chunks, events, alarms, captures", _v1),
    Migration(2, "views gps_track and stall_locations", _views_only),
]

#: the GPS stream and where its columns are (``hondalog.geo.gps_columns``:
#: lat, lon, speed, heading in part 0; sats, fix, hdop, utc in part 1)
GPS_STREAM_NAME = "gps"
#: how far from a capture's trigger the nearest fix may be for ``stall_locations``
NEAREST_FIX_S = 60.0

_SLOT_CASE = "CASE c.slot " + " ".join(f"WHEN {i} THEN s.v{i}" for i in range(SLOTS)) + " END"

SHARED_VIEWS: dict[str, str] = {
    "readings": f"""
        SELECT c.drive_id AS drive_id, d.name AS drive, c.name AS channel, c.units AS units,
               s.t AS t, coalesce(d.epoch_unix, d.started_at) + s.t AS unix_time, {_SLOT_CASE} AS value
        FROM channels c
        CROSS JOIN samples s ON s.drive_id = c.drive_id AND s.stream_id = c.stream_id AND s.part = c.part
        JOIN drives d ON d.id = c.drive_id""",
    "ecu_reads": """
        SELECT s.drive_id AS drive_id, d.name AS drive, s.t AS t, coalesce(d.epoch_unix, d.started_at) + s.t AS unix_time,
               st.addr AS addr, st.name AS block, s.raw AS raw, hex(s.raw) AS raw_hex,
               s.latency_us / 1000.0 AS latency_ms
        FROM samples s
        JOIN streams st ON st.drive_id = s.drive_id AND st.id = s.stream_id
        JOIN drives d ON d.id = s.drive_id
        WHERE s.part = 0 AND s.raw IS NOT NULL""",
    "blackbox_1s": """
        SELECT k.drive_id AS drive_id, d.name AS drive, c.name AS channel, c.units AS units,
               k.t AS t, coalesce(d.epoch_unix, d.started_at) + k.t AS unix_time, k.n AS samples,
               k.lo AS min, k.mean AS mean, k.hi AS max
        FROM channels c
        CROSS JOIN chunk_stats k ON k.drive_id = c.drive_id AND k.stream_id = c.stream_id
                                    AND k.position = c.position
        JOIN drives d ON d.id = c.drive_id""",
    "markers": """
        SELECT e.drive_id AS drive_id, d.name AS drive, e.t AS t,
               datetime(coalesce(d.epoch_unix, d.started_at) + e.t, 'unixepoch', 'localtime') AS wall_time,
               e.kind AS kind, e.text AS text
        FROM events e JOIN drives d ON d.id = e.drive_id
        WHERE e.kind IN ('marker', 'trigger')
        UNION ALL
        SELECT a.drive_id, d.name, a.t, datetime(coalesce(d.epoch_unix, d.started_at) + a.t, 'unixepoch', 'localtime'),
               'alarm', a.text
        FROM alarms a JOIN drives d ON d.id = a.drive_id""",
    "stalls": """
        SELECT c.id AS capture_id, c.name AS name, c.drive_id AS drive_id, d.name AS drive,
               c.wall_iso AS wall_time, c.reason AS reason, c.message AS message,
               c.t_trigger AS t_trigger, c.t_start AS t_start, c.t_end AS t_end,
               c.complete AS complete, c.rows AS rows, d.ecu_id AS ecu_id,
               d.map_version AS map_version, d.notes AS drive_notes
        FROM captures c JOIN drives d ON d.id = c.drive_id
        WHERE c.kind = 'stall'""",
    "gps_track": f"""
        SELECT d.id AS drive_id, d.name AS drive, g.t AS t, coalesce(d.epoch_unix, d.started_at) + g.t AS unix_time,
               g.v0 AS lat, g.v1 AS lon, g.v2 AS speed, g.v2 * 0.621371192 AS speed_mph, g.v3 AS heading,
               h.v1 AS fix, h.v0 AS sats, h.v2 AS hdop
        FROM drives d
        JOIN streams st ON st.drive_id = d.id AND st.name = '{GPS_STREAM_NAME}'
        CROSS JOIN samples g ON g.drive_id = st.drive_id AND g.stream_id = st.id AND g.part = 0
        LEFT JOIN samples h ON h.drive_id = g.drive_id AND h.stream_id = g.stream_id AND h.part = 1
                               AND h.t = g.t AND h.n = g.n""",
    "stall_locations": f"""
        SELECT c.id AS capture_id, c.name AS name, c.kind AS kind, c.reason AS reason, c.rule AS rule,
               c.message AS message, c.drive_id AS drive_id, d.name AS drive, c.wall_iso AS wall_time,
               c.wall_unix AS wall_unix, c.t_trigger AS t_trigger,
               g.t AS gps_t, g.t - c.t_trigger AS gps_dt, g.v0 AS lat, g.v1 AS lon, g.v2 AS speed,
               g.v2 * 0.621371192 AS speed_mph, g.v3 AS heading,
               (SELECT max(s.v2) FROM samples s WHERE s.drive_id = c.drive_id AND s.stream_id = st.id
                AND s.part = 0 AND s.t BETWEEN c.t_trigger - 30 AND c.t_trigger) AS max_speed_30s
        FROM captures c
        JOIN drives d ON d.id = c.drive_id
        LEFT JOIN streams st ON st.drive_id = c.drive_id AND st.name = '{GPS_STREAM_NAME}'
        LEFT JOIN samples g ON g.drive_id = c.drive_id AND g.stream_id = st.id AND g.part = 0
             AND (g.t, g.n) = (SELECT s.t, s.n FROM samples s
                               WHERE s.drive_id = c.drive_id AND s.stream_id = st.id AND s.part = 0
                                 AND s.v0 IS NOT NULL
                                 AND s.t BETWEEN c.t_trigger - {NEAREST_FIX_S} AND c.t_trigger + {NEAREST_FIX_S}
                               ORDER BY abs(s.t - c.t_trigger) LIMIT 1)""",
    "drive_summary": """
        SELECT d.id AS drive_id, d.name AS drive, d.status AS status, d.end_reason AS end_reason,
               d.started_iso AS started, d.ended_iso AS ended,
               round(d.duration_s / 60.0, 1) AS minutes, d.rows AS rows,
               d.stall_count AS stalls, d.capture_count AS captures,
               (SELECT count(*) FROM events e WHERE e.drive_id = d.id AND e.kind = 'marker') AS markers,
               (SELECT count(*) FROM alarms a WHERE a.drive_id = d.id AND a.state = 'raised') AS alarms,
               (SELECT count(DISTINCT c.name) FROM channels c WHERE c.drive_id = d.id) AS channels,
               d.vehicle AS vehicle, d.ecu_id AS ecu_id, d.map_version AS map_version,
               d.notes AS notes, d.tags AS tags, round(d.size_bytes / 1e6, 2) AS approx_mb
        FROM drives d""",
}


def quote(name: str) -> str:
    """An SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def schema_version(conn: sqlite3.Connection) -> int:
    """The version of the schema in this database; 0 for an empty file."""
    if not has_table(conn, "schema_version"):
        return 0
    row = conn.execute("SELECT max(version) FROM schema_version").fetchone()
    return int(row[0] or 0)


def migrate(conn: sqlite3.Connection, migrations: Sequence[Migration] | None = None) -> list[int]:
    """Bring the database up to the newest schema. Returns the versions applied.

    Each migration runs in its own transaction together with its row in
    ``schema_version``: a crash leaves the database at a whole version.
    """
    todo = MIGRATIONS if migrations is None else migrations
    latest = todo[-1].version if todo else 0
    current = schema_version(conn)
    if current > latest:
        raise LogFormatError(f"database schema version {current} is newer than this program "
                             f"understands ({latest}); update hondalog")
    applied: list[int] = []
    for migration in todo:
        if migration.version <= current:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            if schema_version(conn) >= migration.version:      # another process got there first
                conn.execute("ROLLBACK")
                continue
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, "
                         "applied_at TEXT NOT NULL, description TEXT NOT NULL)")
            migration.apply(conn)
            conn.execute("INSERT INTO schema_version(version, applied_at, description) VALUES(?,?,?)",
                         (migration.version, time.strftime("%Y-%m-%dT%H:%M:%S"), migration.description))
            conn.execute(f"PRAGMA user_version = {int(migration.version)}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        applied.append(migration.version)
    if applied:
        rebuild_views(conn)
    return applied


def rebuild_views(conn: sqlite3.Connection) -> None:
    """Drop every view and make them again: the shared ones and one per drive."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='view'").fetchall():
            conn.execute(f"DROP VIEW IF EXISTS {quote(name)}")
        for name, body in SHARED_VIEWS.items():
            conn.execute(f"CREATE VIEW {quote(name)} AS {body}")
        for (drive_id,) in conn.execute("SELECT id FROM drives").fetchall():
            make_drive_view(conn, int(drive_id))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def make_drive_view(conn: sqlite3.Connection, drive_id: int) -> str:
    """(Re)create the view named after the drive. Call inside a transaction.

    One row per sample of the drive's base stream (the one that reads RPM,
    else the one with the most rows), a ``wall_time`` column, and one column
    per channel. Channels of slower streams show the last value read at or
    before the row's time, if it is younger than VIEW_HOLD_S; black box
    channels show their one-second mean. ``<name>_blackbox`` has the black
    box streams alone, one row per second with min, mean and max.
    """
    row = conn.execute("SELECT name, coalesce(epoch_unix, started_at) FROM drives WHERE id=?", (drive_id,)).fetchone()
    if row is None:
        return ""
    name, epoch = str(row[0]), float(row[1] or 0.0)
    conn.execute(f"DROP VIEW IF EXISTS {quote(name)}")
    conn.execute(f"DROP VIEW IF EXISTS {quote(name + '_blackbox')}")
    channels = conn.execute(
        "SELECT c.stream_id, c.position, c.part, c.slot, c.name, s.storage, s.rows "
        "FROM channels c JOIN streams s ON s.drive_id = c.drive_id AND s.id = c.stream_id "
        "WHERE c.drive_id=? ORDER BY c.stream_id, c.position", (drive_id,)).fetchall()
    rows = [c for c in channels if c[5] == "rows"]
    chunked = [c for c in channels if c[5] == "chunks"]
    wall = f"strftime('%Y-%m-%d %H:%M:%f', {epoch!r} + g.t, 'unixepoch', 'localtime')"
    used = {"t", "wall_time"}

    def unique(label: str) -> str:
        out, k = label, 2
        while out.lower() in used:
            out = f"{label}_{k}"
            k += 1
        used.add(out.lower())
        return out

    columns: list[str] = []
    if rows:
        with_rpm = [c[0] for c in rows if c[4] == "rpm"]
        base = with_rpm[0] if with_rpm else max(rows, key=lambda c: (c[6], -c[0]))[0]
        seen: set[str] = set()
        for stream_id, _pos, part, slot, ch, _storage, _n in rows:
            if ch in seen:
                continue                    # read in two streams: the first one is shown
            seen.add(ch)
            if stream_id == base and part == 0:
                columns.append(f"g.v{slot} AS {quote(unique(ch))}")
            elif stream_id == base:             # the same row, continued in another part
                columns.append(
                    f"(SELECT s.v{slot} FROM samples s WHERE s.drive_id={drive_id} AND s.stream_id={stream_id} "
                    f"AND s.part={part} AND s.t = g.t AND s.n = g.n) AS {quote(unique(ch))}")
            else:
                columns.append(
                    f"(SELECT s.v{slot} FROM samples s WHERE s.drive_id={drive_id} AND s.stream_id={stream_id} "
                    f"AND s.part={part} AND s.t <= g.t AND s.t > g.t - {VIEW_HOLD_S} "
                    f"ORDER BY s.t DESC LIMIT 1) AS {quote(unique(ch))}")
        for stream_id, pos, _part, _slot, ch, _storage, _n in chunked:
            if ch in seen:
                continue
            seen.add(ch)
            columns.append(
                f"(SELECT k.mean FROM chunk_stats k WHERE k.drive_id={drive_id} AND k.stream_id={stream_id} "
                f"AND k.position={pos} AND k.t <= g.t AND k.t > g.t - 2.0 "
                f"ORDER BY k.t DESC LIMIT 1) AS {quote(unique(ch))}")
        body = (f"SELECT g.t AS t, {wall} AS wall_time, {', '.join(columns)} FROM samples g "
                f"WHERE g.drive_id={drive_id} AND g.stream_id={base} AND g.part=0")
    elif chunked:
        first = chunked[0]
        for stream_id, pos, _part, _slot, ch, _storage, _n in chunked:
            columns.append(
                f"(SELECT k.mean FROM chunk_stats k WHERE k.drive_id={drive_id} AND k.stream_id={stream_id} "
                f"AND k.position={pos} AND k.t = g.t) AS {quote(unique(ch))}")
        body = (f"SELECT g.t AS t, {wall} AS wall_time, {', '.join(columns)} FROM chunk_stats g "
                f"WHERE g.drive_id={drive_id} AND g.stream_id={first[0]} AND g.position={first[1]}")
    else:
        body = "SELECT NULL AS t, NULL AS wall_time WHERE 0"
    conn.execute(f"CREATE VIEW {quote(name)} AS {body}")
    if chunked:
        first = chunked[0]
        used.clear()
        used.update({"t", "wall_time"})
        cols = []
        for stream_id, pos, _part, _slot, ch, _storage, _n in chunked:
            for stat, col in (("min", "lo"), ("mean", "mean"), ("max", "hi")):
                cols.append(
                    f"(SELECT k.{col} FROM chunk_stats k WHERE k.drive_id={drive_id} AND k.stream_id={stream_id} "
                    f"AND k.position={pos} AND k.t = g.t) AS {quote(unique(f'{ch}_{stat}'))}")
        conn.execute(
            f"CREATE VIEW {quote(name + '_blackbox')} AS SELECT g.t AS t, {wall} AS wall_time, "
            f"{', '.join(cols)} FROM chunk_stats g WHERE g.drive_id={drive_id} "
            f"AND g.stream_id={first[0]} AND g.position={first[1]}")
    return name


def drop_drive_view(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(f"DROP VIEW IF EXISTS {quote(name)}")
    conn.execute(f"DROP VIEW IF EXISTS {quote(name + '_blackbox')}")
