/**
 * The database schema, its version history, and the views: the same SQL as
 * hondalog/data/schema.py, so a hondalog.db made by either app opens in the
 * other. Read that module's text for what each table holds.
 *
 * A schema change is a new Migration at the end of MIGRATIONS (here AND in
 * the Python app). Views are derived data: dropped and made again after any
 * migration.
 */

import type { Conn } from './conn';
import { localIso, pyFloat } from './pyfmt';

/** decoded values per ``samples`` row; a wider stream continues in the next part */
export const SLOTS = 4;
/** "HDLG": PRAGMA application_id of a hondalog database */
export const APPLICATION_ID = 0x48444c47;
export const SQLITE_MAGIC = new TextEncoder().encode('SQLite format 3\u0000');
/** how long a per-drive view holds a value of a slower stream, seconds */
export const VIEW_HOLD_S = 5.0;

export class LogFormatError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'LogFormatError';
  }
}

export interface Migration {
  version: number;
  description: string;
  apply: (conn: Conn) => void;
}

const V1 = `
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
    meta            TEXT NOT NULL DEFAULT '{}',        -- JSON: every other header fact
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
    attrs     TEXT NOT NULL DEFAULT '{}',
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
    part      INTEGER NOT NULL,                        -- samples.part = position / ${SLOTS}
    slot      INTEGER NOT NULL,                        -- samples.v<slot> = position % ${SLOTS}
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
    ${Array.from({ length: SLOTS }, (_, i) => `v${i} REAL`).join(', ')},
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
    meta       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX captures_by_drive ON captures(drive_id, t_trigger);
`;

export const MIGRATIONS: Migration[] = [
  { version: 1, description: 'drives, streams, channels, samples, chunks, events, alarms, captures', apply: (c) => c.script(V1) },
  { version: 2, description: 'views gps_track and stall_locations', apply: () => {} },
];

/** the GPS stream (hondalog.geo.gps_columns: lat, lon, speed, heading in part 0; sats, fix, hdop, utc in part 1) */
export const GPS_STREAM_NAME = 'gps';
/** how far from a capture's trigger the nearest fix may be for ``stall_locations`` */
export const NEAREST_FIX_S = 60.0;

const SLOT_CASE = 'CASE c.slot ' + Array.from({ length: SLOTS }, (_, i) => `WHEN ${i} THEN s.v${i}`).join(' ') + ' END';
const NF = pyFloat(NEAREST_FIX_S);

export const SHARED_VIEWS: Record<string, string> = {
  readings: `
        SELECT c.drive_id AS drive_id, d.name AS drive, c.name AS channel, c.units AS units,
               s.t AS t, coalesce(d.epoch_unix, d.started_at) + s.t AS unix_time, ${SLOT_CASE} AS value
        FROM channels c
        CROSS JOIN samples s ON s.drive_id = c.drive_id AND s.stream_id = c.stream_id AND s.part = c.part
        JOIN drives d ON d.id = c.drive_id`,
  ecu_reads: `
        SELECT s.drive_id AS drive_id, d.name AS drive, s.t AS t, coalesce(d.epoch_unix, d.started_at) + s.t AS unix_time,
               st.addr AS addr, st.name AS block, s.raw AS raw, hex(s.raw) AS raw_hex,
               s.latency_us / 1000.0 AS latency_ms
        FROM samples s
        JOIN streams st ON st.drive_id = s.drive_id AND st.id = s.stream_id
        JOIN drives d ON d.id = s.drive_id
        WHERE s.part = 0 AND s.raw IS NOT NULL`,
  blackbox_1s: `
        SELECT k.drive_id AS drive_id, d.name AS drive, c.name AS channel, c.units AS units,
               k.t AS t, coalesce(d.epoch_unix, d.started_at) + k.t AS unix_time, k.n AS samples,
               k.lo AS min, k.mean AS mean, k.hi AS max
        FROM channels c
        CROSS JOIN chunk_stats k ON k.drive_id = c.drive_id AND k.stream_id = c.stream_id
                                    AND k.position = c.position
        JOIN drives d ON d.id = c.drive_id`,
  markers: `
        SELECT e.drive_id AS drive_id, d.name AS drive, e.t AS t,
               datetime(coalesce(d.epoch_unix, d.started_at) + e.t, 'unixepoch', 'localtime') AS wall_time,
               e.kind AS kind, e.text AS text
        FROM events e JOIN drives d ON d.id = e.drive_id
        WHERE e.kind IN ('marker', 'trigger')
        UNION ALL
        SELECT a.drive_id, d.name, a.t, datetime(coalesce(d.epoch_unix, d.started_at) + a.t, 'unixepoch', 'localtime'),
               'alarm', a.text
        FROM alarms a JOIN drives d ON d.id = a.drive_id`,
  stalls: `
        SELECT c.id AS capture_id, c.name AS name, c.drive_id AS drive_id, d.name AS drive,
               c.wall_iso AS wall_time, c.reason AS reason, c.message AS message,
               c.t_trigger AS t_trigger, c.t_start AS t_start, c.t_end AS t_end,
               c.complete AS complete, c.rows AS rows, d.ecu_id AS ecu_id,
               d.map_version AS map_version, d.notes AS drive_notes
        FROM captures c JOIN drives d ON d.id = c.drive_id
        WHERE c.kind = 'stall'`,
  gps_track: `
        SELECT d.id AS drive_id, d.name AS drive, g.t AS t, coalesce(d.epoch_unix, d.started_at) + g.t AS unix_time,
               g.v0 AS lat, g.v1 AS lon, g.v2 AS speed, g.v2 * 0.621371192 AS speed_mph, g.v3 AS heading,
               h.v1 AS fix, h.v0 AS sats, h.v2 AS hdop
        FROM drives d
        JOIN streams st ON st.drive_id = d.id AND st.name = '${GPS_STREAM_NAME}'
        CROSS JOIN samples g ON g.drive_id = st.drive_id AND g.stream_id = st.id AND g.part = 0
        LEFT JOIN samples h ON h.drive_id = g.drive_id AND h.stream_id = g.stream_id AND h.part = 1
                               AND h.t = g.t AND h.n = g.n`,
  stall_locations: `
        SELECT c.id AS capture_id, c.name AS name, c.kind AS kind, c.reason AS reason, c.rule AS rule,
               c.message AS message, c.drive_id AS drive_id, d.name AS drive, c.wall_iso AS wall_time,
               c.wall_unix AS wall_unix, c.t_trigger AS t_trigger,
               g.t AS gps_t, g.t - c.t_trigger AS gps_dt, g.v0 AS lat, g.v1 AS lon, g.v2 AS speed,
               g.v2 * 0.621371192 AS speed_mph, g.v3 AS heading,
               (SELECT max(s.v2) FROM samples s WHERE s.drive_id = c.drive_id AND s.stream_id = st.id
                AND s.part = 0 AND s.t BETWEEN c.t_trigger - 30 AND c.t_trigger) AS max_speed_30s
        FROM captures c
        JOIN drives d ON d.id = c.drive_id
        LEFT JOIN streams st ON st.drive_id = c.drive_id AND st.name = '${GPS_STREAM_NAME}'
        LEFT JOIN samples g ON g.drive_id = c.drive_id AND g.stream_id = st.id AND g.part = 0
             -- nearest fix by min(abs()), not ORDER BY abs(): SQLite 3.45.0/3.45.1 cannot see the
             -- outer c in a correlated subquery's ORDER BY ("no such column: c.t_trigger")
             AND g.t = (SELECT min(s.t) FROM samples s
                        WHERE s.drive_id = c.drive_id AND s.stream_id = st.id AND s.part = 0 AND s.v0 IS NOT NULL
                          AND s.t BETWEEN c.t_trigger - ${NF} AND c.t_trigger + ${NF}
                          AND abs(s.t - c.t_trigger) = (
                              SELECT min(abs(s2.t - c.t_trigger)) FROM samples s2
                              WHERE s2.drive_id = c.drive_id AND s2.stream_id = st.id AND s2.part = 0 AND s2.v0 IS NOT NULL
                                AND s2.t BETWEEN c.t_trigger - ${NF} AND c.t_trigger + ${NF}))
             AND g.n = (SELECT min(s.n) FROM samples s WHERE s.drive_id = c.drive_id AND s.stream_id = st.id AND s.part = 0 AND s.v0 IS NOT NULL AND s.t = g.t)`,
  drive_summary: `
        SELECT d.id AS drive_id, d.name AS drive, d.status AS status, d.end_reason AS end_reason,
               d.started_iso AS started, d.ended_iso AS ended,
               round(d.duration_s / 60.0, 1) AS minutes, d.rows AS rows,
               d.stall_count AS stalls, d.capture_count AS captures,
               (SELECT count(*) FROM events e WHERE e.drive_id = d.id AND e.kind = 'marker') AS markers,
               (SELECT count(*) FROM alarms a WHERE a.drive_id = d.id AND a.state = 'raised') AS alarms,
               (SELECT count(DISTINCT c.name) FROM channels c WHERE c.drive_id = d.id) AS channels,
               d.vehicle AS vehicle, d.ecu_id AS ecu_id, d.map_version AS map_version,
               d.notes AS notes, d.tags AS tags, round(d.size_bytes / 1e6, 2) AS approx_mb
        FROM drives d`,
};

/** An SQL identifier. */
export function quote(name: string): string {
  return '"' + name.replace(/"/g, '""') + '"';
}

export function hasTable(conn: Conn, name: string): boolean {
  return conn.get("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [name]) !== undefined;
}

/** The version of the schema in this database; 0 for an empty file. */
export function schemaVersion(conn: Conn): number {
  if (!hasTable(conn, 'schema_version')) return 0;
  return Number(conn.get('SELECT max(version) FROM schema_version')![0] ?? 0);
}

function nowText(): string {
  return localIso(Date.now() / 1000);
}

/** Bring the database up to the newest schema. Returns the versions applied. */
export function migrate(conn: Conn, migrations: Migration[] = MIGRATIONS): number[] {
  const latest = migrations.length ? migrations[migrations.length - 1]!.version : 0;
  const current = schemaVersion(conn);
  if (current > latest) {
    throw new LogFormatError(`database schema version ${current} is newer than this program understands (${latest}); update hondalog`);
  }
  const applied: number[] = [];
  for (const m of migrations) {
    if (m.version <= current) continue;
    conn.tx(() => {
      conn.run('CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT NOT NULL)');
      m.apply(conn);
      conn.run('INSERT INTO schema_version(version, applied_at, description) VALUES(?,?,?)', [m.version, nowText(), m.description]);
      conn.run(`PRAGMA user_version = ${Math.trunc(m.version)}`);
    });
    applied.push(m.version);
  }
  if (applied.length) rebuildViews(conn);
  return applied;
}

/** Drop every view and make them again: the shared ones and one per drive. */
export function rebuildViews(conn: Conn): void {
  conn.tx(() => {
    for (const [name] of conn.all("SELECT name FROM sqlite_master WHERE type='view'")) {
      conn.run(`DROP VIEW IF EXISTS ${quote(String(name))}`);
    }
    for (const [name, body] of Object.entries(SHARED_VIEWS)) conn.run(`CREATE VIEW ${quote(name)} AS ${body}`);
    for (const [id] of conn.all('SELECT id FROM drives')) makeDriveView(conn, Number(id));
  });
}

/**
 * (Re)create the view named after the drive. Call inside a transaction.
 * One row per sample of the base stream (the one that reads RPM, else the one
 * with the most rows), ``wall_time``, one column per channel; slower streams
 * hold their last value for VIEW_HOLD_S; black box channels show their
 * one-second mean. ``<name>_blackbox`` has the black box streams alone.
 */
export function makeDriveView(conn: Conn, driveId: number): string {
  const row = conn.get('SELECT name, coalesce(epoch_unix, started_at) FROM drives WHERE id=?', [driveId]);
  if (!row) return '';
  const name = String(row[0]);
  const epoch = Number(row[1] ?? 0) || 0;
  conn.run(`DROP VIEW IF EXISTS ${quote(name)}`);
  conn.run(`DROP VIEW IF EXISTS ${quote(name + '_blackbox')}`);
  type Ch = [number, number, number, number, string, string, number];
  const channels = conn.all(
    'SELECT c.stream_id, c.position, c.part, c.slot, c.name, s.storage, s.rows ' +
    'FROM channels c JOIN streams s ON s.drive_id = c.drive_id AND s.id = c.stream_id ' +
    'WHERE c.drive_id=? ORDER BY c.stream_id, c.position', [driveId],
  ).map((r) => [Number(r[0]), Number(r[1]), Number(r[2]), Number(r[3]), String(r[4]), String(r[5]), Number(r[6])] as Ch);
  const rows = channels.filter((c) => c[5] === 'rows');
  const chunked = channels.filter((c) => c[5] === 'chunks');
  const wall = `strftime('%Y-%m-%d %H:%M:%f', ${pyFloat(epoch)} + g.t, 'unixepoch', 'localtime')`;
  const used = new Set(['t', 'wall_time']);
  const unique = (label: string): string => {
    let out = label;
    let k = 2;
    while (used.has(out.toLowerCase())) out = `${label}_${k++}`;
    used.add(out.toLowerCase());
    return out;
  };
  const hold = pyFloat(VIEW_HOLD_S);
  const columns: string[] = [];
  let body: string;
  if (rows.length) {
    const withRpm = rows.filter((c) => c[4] === 'rpm').map((c) => c[0]);
    let base: number;
    if (withRpm.length) base = withRpm[0]!;
    else {
      // max by (rows, -stream_id): the first of the largest
      let best = rows[0]!;
      for (const c of rows) if (c[6] > best[6] || (c[6] === best[6] && c[0] < best[0])) best = c;
      base = best[0];
    }
    const seen = new Set<string>();
    for (const [sid, , part, slot, ch] of rows) {
      if (seen.has(ch)) continue;
      seen.add(ch);
      if (sid === base && part === 0) columns.push(`g.v${slot} AS ${quote(unique(ch))}`);
      else if (sid === base) {
        columns.push(`(SELECT s.v${slot} FROM samples s WHERE s.drive_id=${driveId} AND s.stream_id=${sid} ` +
          `AND s.part=${part} AND s.t = g.t AND s.n = g.n) AS ${quote(unique(ch))}`);
      } else {
        columns.push(`(SELECT s.v${slot} FROM samples s WHERE s.drive_id=${driveId} AND s.stream_id=${sid} ` +
          `AND s.part=${part} AND s.t <= g.t AND s.t > g.t - ${hold} ` +
          `ORDER BY s.t DESC LIMIT 1) AS ${quote(unique(ch))}`);
      }
    }
    for (const [sid, pos, , , ch] of chunked) {
      if (seen.has(ch)) continue;
      seen.add(ch);
      columns.push(`(SELECT k.mean FROM chunk_stats k WHERE k.drive_id=${driveId} AND k.stream_id=${sid} ` +
        `AND k.position=${pos} AND k.t <= g.t AND k.t > g.t - 2.0 ` +
        `ORDER BY k.t DESC LIMIT 1) AS ${quote(unique(ch))}`);
    }
    body = `SELECT g.t AS t, ${wall} AS wall_time, ${columns.join(', ')} FROM samples g ` +
      `WHERE g.drive_id=${driveId} AND g.stream_id=${base} AND g.part=0`;
  } else if (chunked.length) {
    const first = chunked[0]!;
    for (const [sid, pos, , , ch] of chunked) {
      columns.push(`(SELECT k.mean FROM chunk_stats k WHERE k.drive_id=${driveId} AND k.stream_id=${sid} ` +
        `AND k.position=${pos} AND k.t = g.t) AS ${quote(unique(ch))}`);
    }
    body = `SELECT g.t AS t, ${wall} AS wall_time, ${columns.join(', ')} FROM chunk_stats g ` +
      `WHERE g.drive_id=${driveId} AND g.stream_id=${first[0]} AND g.position=${first[1]}`;
  } else {
    body = 'SELECT NULL AS t, NULL AS wall_time WHERE 0';
  }
  conn.run(`CREATE VIEW ${quote(name)} AS ${body}`);
  if (chunked.length) {
    const first = chunked[0]!;
    used.clear();
    used.add('t');
    used.add('wall_time');
    const cols: string[] = [];
    for (const [sid, pos, , , ch] of chunked) {
      for (const [stat, col] of [['min', 'lo'], ['mean', 'mean'], ['max', 'hi']] as const) {
        cols.push(`(SELECT k.${col} FROM chunk_stats k WHERE k.drive_id=${driveId} AND k.stream_id=${sid} ` +
          `AND k.position=${pos} AND k.t = g.t) AS ${quote(unique(`${ch}_${stat}`))}`);
      }
    }
    conn.run(`CREATE VIEW ${quote(name + '_blackbox')} AS SELECT g.t AS t, ${wall} AS wall_time, ` +
      `${cols.join(', ')} FROM chunk_stats g WHERE g.drive_id=${driveId} ` +
      `AND g.stream_id=${first[0]} AND g.position=${first[1]}`);
  }
  return name;
}

export function dropDriveView(conn: Conn, name: string): void {
  conn.run(`DROP VIEW IF EXISTS ${quote(name)}`);
  conn.run(`DROP VIEW IF EXISTS ${quote(name + '_blackbox')}`);
}
