/**
 * Opening the database and the housekeeping every opener shares (port of
 * hondalog/data/db.py): crash recovery, finishing a drive, compacting
 * high-rate streams, naming, deleting, vacuum.
 *
 * The whole file lives in memory (sql.js). ``HondalogDb.persist`` writes it
 * back through a ``FileStore`` (OPFS on the web, the Tauri file system on
 * desktop and Android).
 */

import * as codec from './codec';
import { Conn } from './conn';
import type { FileStore } from './filestore';
import { type Column, type Dtype, type StreamData, StreamInfo, column, concatStreamData, emptyStreamData, searchsorted, sortByTime, takeRows } from './model';
import { localIso, localParts, pyJson, pyParse } from './pyfmt';
import { APPLICATION_ID, LogFormatError, SHARED_VIEWS, SLOTS, SQLITE_MAGIC, dropDriveView, makeDriveView, migrate, type Migration, MIGRATIONS } from './schema';
import { type Database, loadSqlJs } from './sqljs';

export const DB_NAME = 'hondalog.db';
/** a drive still marked "recording" whose writer has not been heard from for this long is dead */
export const HEARTBEAT_STALE_S = 30.0;
export const THUMB_POINTS = 64;
/** rough bytes per ``samples`` row, for the size estimate retention works with */
export const ROW_BYTES = 56;

/** This page's stand-in for a process id (``drives.writer_pid``). */
export const SESSION_PID = 100000 + Math.floor(Math.random() * 2_000_000_000);

export function isDatabase(bytes: Uint8Array | null | undefined): boolean {
  if (!bytes || bytes.length < SQLITE_MAGIC.length) return false;
  for (let i = 0; i < SQLITE_MAGIC.length; i++) if (bytes[i] !== SQLITE_MAGIC[i]) return false;
  return true;
}

/** A WAL-mode file read as a plain one: the Python app closes with a full checkpoint. */
function rollbackHeader(bytes: Uint8Array): Uint8Array {
  if (bytes.length > 19 && (bytes[18] === 2 || bytes[19] === 2)) {
    const copy = bytes.slice();
    copy[18] = 1;
    copy[19] = 1;
    return copy;
  }
  return bytes;
}

export interface OpenOptions {
  /** where ``persist`` writes the file */
  fileStore?: FileStore;
  fileName?: string;
  migrations?: Migration[];
  /** for tests: "now" in unix seconds */
  now?: () => number;
}

export class HondalogDb {
  readonly conn: Conn;
  fileStore?: FileStore;
  fileName: string;
  /** (drive id) being written by this page */
  readonly active = new Set<number>();
  /** the writers on this database (flushed before an export) */
  readonly writers = new Set<{ write(): void }>();
  /** something changed since the last persist */
  dirty = false;
  private saving: Promise<void> = Promise.resolve();
  closed = false;
  now: () => number;

  private constructor(readonly db: Database, opts: OpenOptions) {
    this.conn = new Conn(db);
    this.fileStore = opts.fileStore;
    this.fileName = opts.fileName ?? DB_NAME;
    this.now = opts.now ?? (() => Date.now() / 1000);
  }

  /**
   * Open a database file (its bytes), or make a new one (``null``): the
   * newest schema, dead drives marked "crashed". Raises LogFormatError for a
   * file that is not a hondalog database.
   */
  static async open(bytes: Uint8Array | null, opts: OpenOptions = {}): Promise<HondalogDb> {
    const SQL = await loadSqlJs();
    const exists = !!bytes && bytes.length > 0;
    if (exists && !isDatabase(bytes)) throw new LogFormatError(`${opts.fileName ?? 'file'}: not a hondalog database`);
    let db: Database;
    try {
      db = new SQL.Database(exists ? rollbackHeader(bytes!) : undefined);
    } catch (err) {
      throw new LogFormatError(`${opts.fileName ?? 'file'}: ${(err as Error).message}`);
    }
    const out = new HondalogDb(db, opts);
    try {
      const conn = out.conn;
      if (!exists) {
        conn.run('PRAGMA auto_vacuum = INCREMENTAL');
        conn.run(`PRAGMA application_id = ${APPLICATION_ID}`);
      } else {
        const app = Number(conn.get('PRAGMA application_id')![0]);
        if (app !== APPLICATION_ID) throw new LogFormatError(`${opts.fileName ?? 'file'}: an SQLite file, but not a hondalog database`);
      }
      conn.run('PRAGMA journal_mode = DELETE');
      if (migrate(conn, opts.migrations ?? MIGRATIONS).length) out.dirty = true;
      if (conn.get("SELECT 1 FROM drives WHERE status='recording' LIMIT 1")) {
        if (out.recover().length) out.dirty = true;
      }
    } catch (err) {
      db.close();
      if (err instanceof LogFormatError) throw err;
      const msg = (err as Error).message ?? String(err);
      throw new LogFormatError(`${opts.fileName ?? 'file'}: ${msg}`);
    }
    if (!exists) out.dirty = true;
    return out;
  }

  /** Open ``fileName`` from a file store, or make it if it is not there. */
  static async openFile(fileStore: FileStore, fileName = DB_NAME, opts: Omit<OpenOptions, 'fileStore' | 'fileName'> = {}): Promise<HondalogDb> {
    const bytes = await fileStore.read(fileName);
    const out = await HondalogDb.open(bytes, { ...opts, fileStore, fileName });
    if (!bytes) await out.persist();
    return out;
  }

  /** Commit what every writer has buffered. */
  flushWriters(): void {
    for (const w of this.writers) w.write();
  }

  /** The whole file as it is now (everything buffered is committed first). */
  export(): Uint8Array {
    this.flushWriters();
    return this.db.export();
  }

  /** Write the file to the file store (if there is one). Serialised; never two at a time. */
  persist(): Promise<void> {
    const store = this.fileStore;
    if (!store || this.closed) return this.saving;
    this.dirty = false;
    const bytes = this.export();
    this.saving = this.saving.then(() => store.write(this.fileName, bytes)).catch((err: unknown) => {
      this.dirty = true;
      throw err;
    });
    return this.saving;
  }

  /** Wait for the last persist. */
  async settled(): Promise<void> {
    try {
      await this.saving;
    } catch {
      /* reported to whoever started it */
    }
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.db.close();
  }

  // ---- crash recovery ----

  private writerAlive(driveId: number, pid: number | null, heartbeat: number | null, now: number): boolean {
    if (pid === null) return false;
    if (pid === SESSION_PID) return this.active.has(driveId);
    return heartbeat !== null && now - heartbeat < HEARTBEAT_STALE_S;
  }

  /** Close every drive whose recorder is gone: status "crashed". Returns their ids.
   *  ``foreign``: this page is the only one with the file open (the app holds a
   *  Web Lock), so a drive another session left "recording" is dead however
   *  fresh its heartbeat (a reload, a killed tab). */
  recover(now?: number, opts: { foreign?: boolean } = {}): number[] {
    const t = now ?? this.now();
    const done: number[] = [];
    for (const [id, pid, hb] of this.conn.all("SELECT id, writer_pid, heartbeat_at FROM drives WHERE status='recording'")) {
      const driveId = Number(id);
      const owner = pid === null ? null : Number(pid);
      const foreignDead = !!opts.foreign && owner !== SESSION_PID;
      if (!foreignDead && this.writerAlive(driveId, owner, hb === null ? null : Number(hb), t)) continue;
      finishDrive(this.conn, driveId, 'crashed', 'crash');
      done.push(driveId);
    }
    if (done.length) this.dirty = true;
    return done;
  }
}

// ---- stream definitions ------------------------------------------------------

/** "chunks" for high-rate streams (the black box), "rows" for the rest. */
export function storageFor(info: StreamInfo): 'rows' | 'chunks' {
  const wanted = info.attrs?.storage;
  if (wanted === 'rows' || wanted === 'chunks') return wanted;
  return info.source === 'blackbox' ? 'chunks' : 'rows';
}

/** stream id -> (definition, storage) for one drive. */
export function streamInfos(conn: Conn, driveId: number): Map<number, { info: StreamInfo; storage: string }> {
  const cols = new Map<number, Column[]>();
  for (const [sid, name, units, label, kind, dtype, decimals, lo, hi] of conn.all(
    'SELECT stream_id, name, units, label, kind, dtype, decimals, lo, hi FROM channels WHERE drive_id=? ORDER BY stream_id, position', [driveId])) {
    const list = cols.get(Number(sid)) ?? [];
    list.push(column(String(name), {
      dtype: String(dtype) as Dtype, units: String(units ?? ''), label: String(label ?? ''), kind: String(kind ?? 'value'),
      decimals: Number(decimals), lo: lo === null ? null : Number(lo), hi: hi === null ? null : Number(hi),
    }));
    cols.set(Number(sid), list);
  }
  const out = new Map<number, { info: StreamInfo; storage: string }>();
  for (const [sid, name, source, storage, rawLen, attrs] of conn.all(
    'SELECT id, name, source, storage, raw_len, attrs FROM streams WHERE drive_id=? ORDER BY id', [driveId])) {
    const info = new StreamInfo(String(name), cols.get(Number(sid)) ?? [], String(source ?? ''), Number(rawLen), pyParse(attrs as string) ?? {});
    out.set(Number(sid), { info, storage: String(storage) });
  }
  return out;
}

// ---- chunked streams ---------------------------------------------------------

/** Every row of a chunked stream (or those with t0 <= t <= t1), in time order. */
export function readChunks(conn: Conn, driveId: number, streamId: number, info: StreamInfo, t0: number | null = null, t1: number | null = null): StreamData {
  let sql = 'SELECT codec, data, n_rows FROM chunks WHERE drive_id=? AND stream_id=?';
  const args: unknown[] = [driveId, streamId];
  if (t0 !== null) { sql += ' AND t_end >= ?'; args.push(t0); }
  if (t1 !== null) { sql += ' AND t_start <= ?'; args.push(t1); }
  const parts = conn.all(sql + ' ORDER BY t_start, id', args)
    .map(([name, data, n]) => codec.decode(String(name), data as Uint8Array, info, Number(n)));
  if (!parts.length) return emptyStreamData(info);
  let rows = sortByTime(concatStreamData(parts));
  if (t0 !== null || t1 !== null) {
    const lo = t0 === null ? 0 : searchsorted(rows.t, t0, 'left');
    const hi = t1 === null ? rows.t.length : searchsorted(rows.t, t1, 'right');
    rows = takeRows(rows, Array.from({ length: hi - lo }, (_, i) => lo + i));
  }
  return rows;
}

/** (position, second, n, min, mean, max) for every channel and whole second with a finite value. */
export function secondStats(rows: StreamData): [number, number, number, number, number, number][] {
  const out: [number, number, number, number, number, number][] = [];
  const n = rows.t.length;
  if (!n) return out;
  const starts: number[] = [];
  const seconds: number[] = [];
  let prev = NaN;
  for (let i = 0; i < n; i++) {
    const s = Math.floor(rows.t[i]!);
    if (i === 0 || s !== prev) { starts.push(i); seconds.push(s); }
    prev = s;
  }
  rows.info.columns.forEach((col, position) => {
    const values = rows.columns[col.name]!;
    for (let g = 0; g < starts.length; g++) {
      const a = starts[g]!;
      const b = g + 1 < starts.length ? starts[g + 1]! : n;
      let count = 0, total = 0, lo = Infinity, hi = -Infinity;
      for (let i = a; i < b; i++) {
        const v = values[i]!;
        if (!Number.isFinite(v)) continue;
        count++;
        total += v;
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
      if (count) out.push([position, seconds[g]!, count, lo, total / count, hi]);
    }
  });
  return out;
}

export const STATS_UPSERT = 'INSERT INTO chunk_stats(drive_id, stream_id, position, t, n, lo, mean, hi) ' +
  'VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(drive_id, stream_id, position, t) DO UPDATE SET ' +
  'lo = min(lo, excluded.lo), hi = max(hi, excluded.hi), ' +
  'mean = (mean * n + excluded.mean * excluded.n) / (n + excluded.n), n = n + excluded.n';

/** Store sorted rows as one packed chunk plus their one-second statistics. Inside a transaction. */
export function writeBlock(conn: Conn, driveId: number, streamId: number, rows: StreamData): number {
  const n = rows.t.length;
  if (!n) return 0;
  const blob = codec.encode(rows, codec.PACKED);
  conn.run('INSERT INTO chunks(drive_id, stream_id, t_start, t_end, n_rows, codec, data) VALUES(?,?,?,?,?,?,?)',
    [driveId, streamId, rows.t[0]!, rows.t[n - 1]!, n, codec.PACKED, blob]);
  conn.many(STATS_UPSERT, secondStats(rows).map((r) => [driveId, streamId, ...r]));
  return blob.length;
}

/** Turn the stream's ``raw`` chunks into one packed block. Returns the rows packed. Inside a transaction. */
export function compactStream(conn: Conn, driveId: number, streamId: number, info: StreamInfo): number {
  const found = conn.all('SELECT id, data, n_rows FROM chunks WHERE drive_id=? AND stream_id=? AND codec=? ORDER BY t_start, id',
    [driveId, streamId, codec.RAW]);
  if (!found.length) return 0;
  const rows = sortByTime(concatStreamData(found.map(([, data, n]) => codec.decode(codec.RAW, data as Uint8Array, info, Number(n)))));
  conn.many('DELETE FROM chunks WHERE id=?', found.map((r) => [r[0]]));
  writeBlock(conn, driveId, streamId, rows);
  return rows.t.length;
}

// ---- finishing a drive -------------------------------------------------------

/** Min/max of ``v`` in each of ``columns`` equal time slices (analysis/lod.py envelope). */
export function envelope(t: ArrayLike<number>, v: ArrayLike<number>, columns: number) {
  const n = t.length;
  if (!n) return { t: [] as number[], lo: [] as number[], hi: [] as number[], reduced: false };
  const t0 = t[0]!;
  const t1 = t[n - 1]!;
  if (n <= 2 * columns || t1 <= t0) {
    return { t: Array.from(t), lo: Array.from(v), hi: Array.from(v), reduced: false };
  }
  const tl: number[] = [], lo: number[] = [], hi: number[] = [];
  const edges = Array.from({ length: columns + 1 }, (_, i) => t0 + (t1 - t0) * (i / columns));
  edges[columns] = t1;
  const starts = edges.slice(0, -1).map((e) => searchsorted(t, e, 'left'));
  for (let c = 0; c < columns; c++) {
    const a = starts[c]!;
    const b = c + 1 < columns ? starts[c + 1]! : n;
    if (b <= a) continue;
    let mn = NaN, mx = NaN;
    for (let i = a; i < b; i++) {
      const x = v[i]!;
      if (x !== x) continue;
      if (mn !== mn || x < mn) mn = x;
      if (mx !== mx || x > mx) mx = x;
    }
    tl.push((edges[c]! + edges[c + 1]!) / 2);
    lo.push(mn);
    hi.push(mx);
  }
  return { t: tl, lo, hi, reduced: true };
}

const round1 = (x: number) => Math.round(x * 10) / 10;

/** The RPM envelope for a sparkline, as JSON. */
function thumb(conn: Conn, driveId: number, channel = 'rpm'): string | null {
  const row = conn.get(
    'SELECT c.stream_id, c.part, c.slot FROM channels c JOIN streams s ON s.drive_id = c.drive_id ' +
    "AND s.id = c.stream_id WHERE c.drive_id=? AND c.name=? AND s.storage='rows' ORDER BY s.rows DESC LIMIT 1", [driveId, channel]);
  if (!row) return null;
  const data = conn.all(`SELECT t, v${Number(row[2])} FROM samples WHERE drive_id=? AND stream_id=? AND part=?`, [driveId, row[0], row[1]]);
  if (!data.length) return null;
  const t = data.map((r) => Number(r[0]));
  const v = data.map((r) => (r[1] === null ? NaN : Number(r[1])));
  const env = envelope(t, v, THUMB_POINTS);
  let lo = env.lo.map((x) => (x === x ? x : 0));
  let hi = env.hi.map((x) => (x === x ? x : 0));
  if (!env.reduced && lo.length > THUMB_POINTS) {
    const pick = Array.from({ length: THUMB_POINTS }, (_, i) => Math.trunc((i * (lo.length - 1)) / (THUMB_POINTS - 1)));
    lo = pick.map((i) => lo[i]!);
    hi = pick.map((i) => hi[i]!);
  }
  return pyJson({ lo: lo.map(round1), hi: hi.map(round1) });
}

/**
 * Everything done once per drive, at its end (or when a crashed drive is
 * found): pack the black box tail, count rows per stream, set the end time,
 * make the thumbnail and the view. One transaction.
 */
export function finishDrive(conn: Conn, driveId: number, status: string, reason = ''): void {
  conn.tx(() => {
    const infos = streamInfos(conn, driveId);
    let total = 0, size = 0;
    let first: number | null = null, last: number | null = null;
    for (const [sid, { info, storage }] of infos) {
      let rows: number, lo: number | null, hi: number | null;
      if (storage === 'chunks') {
        compactStream(conn, driveId, sid, info);
        const r = conn.get('SELECT coalesce(sum(n_rows), 0), min(t_start), max(t_end), coalesce(sum(length(data)), 0) ' +
          'FROM chunks WHERE drive_id=? AND stream_id=?', [driveId, sid])!;
        rows = Number(r[0]);
        lo = r[1] === null ? null : Number(r[1]);
        hi = r[2] === null ? null : Number(r[2]);
        const stats = Number(conn.get('SELECT count(*) FROM chunk_stats WHERE drive_id=? AND stream_id=?', [driveId, sid])![0]);
        size += Number(r[3]) + ROW_BYTES * stats;
      } else {
        const r = conn.get('SELECT count(*), min(t), max(t) FROM samples WHERE drive_id=? AND stream_id=? AND part=0', [driveId, sid])!;
        rows = Number(r[0]);
        lo = r[1] === null ? null : Number(r[1]);
        hi = r[2] === null ? null : Number(r[2]);
        const parts = Math.ceil(info.columns.length / SLOTS) || 1;
        size += rows * parts * (ROW_BYTES + info.rawLen);
      }
      conn.run('UPDATE streams SET rows=?, t_first=?, t_last=? WHERE drive_id=? AND id=?', [rows, lo, hi, driveId, sid]);
      total += rows;
      if (rows) {
        first = first === null ? lo : Math.min(first, lo!);
        last = last === null ? hi : Math.max(last, hi!);
      }
    }
    const ev = conn.get('SELECT min(t), max(t) FROM events WHERE drive_id=?', [driveId])!;
    if (first === null && ev[0] !== null) {
      first = Number(ev[0]);
      last = Number(ev[1]);
    }
    const [epoch, started] = conn.get('SELECT epoch_unix, started_at FROM drives WHERE id=?', [driveId])!;
    let ended: number | null = null;
    if (last !== null && epoch !== null) ended = Number(epoch) + last;
    else if (started !== null) ended = Number(started) + (last !== null && first !== null ? last - first : 0);
    const duration = last !== null && first !== null ? last - first : 0;
    conn.run('UPDATE drives SET status=?, end_reason=?, t_first=?, t_last=?, rows=?, size_bytes=?, duration_s=?, ' +
      'ended_at=?, ended_iso=?, thumb=?, writer_pid=NULL WHERE id=?',
    [status, reason, first, last, total, size, duration, ended, ended !== null ? localIso(ended) : null, thumb(conn, driveId), driveId]);
    makeDriveView(conn, driveId);
  });
}

// ---- names -------------------------------------------------------------------

/** Letters, digits and underscores only, starting with a letter. */
export function safeName(text: string, fallback = 'drive'): string {
  let name = text.replace(/[^A-Za-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
  if (!name || !/^[A-Za-z]/.test(name)) name = name ? `${fallback}_${name}` : fallback;
  return name;
}

/** ``stem``, or ``stem_2``, ``stem_3`` ... : a name not yet in ``table`` (nor a table or shared view, for a drive). */
export function uniqueName(conn: Conn, table: 'drives' | 'captures', stem: string): string {
  const base = safeName(stem, table === 'drives' ? 'drive' : 'capture');
  const taken = new Set<string>();
  if (table === 'drives') {
    for (const [n] of conn.all("SELECT name FROM sqlite_master WHERE type != 'view'")) taken.add(String(n));
    for (const n of Object.keys(SHARED_VIEWS)) taken.add(n);
  }
  let name = base;
  let k = 2;
  while (taken.has(name) || conn.get(`SELECT 1 FROM ${table} WHERE name=?`, [name])) name = `${base}_${k++}`;
  return name;
}

export function driveStem(startedUnix: number): string {
  const p = localParts(startedUnix);
  return `drive_${p.Y}_${p.m}_${p.d}_${p.H}${p.M}`;
}

// ---- deleting and vacuum -----------------------------------------------------

/** Remove drives and everything recorded in them. */
export function deleteDrives(conn: Conn, names: Iterable<string>): string[] {
  const removed: string[] = [];
  for (const name of names) {
    const row = conn.get('SELECT id FROM drives WHERE name=?', [name]);
    if (!row) continue;
    const id = Number(row[0]);
    conn.tx(() => {
      for (const table of ['samples', 'chunks', 'chunk_stats', 'events', 'alarms', 'captures', 'channels', 'streams']) {
        conn.run(`DELETE FROM ${table} WHERE drive_id=?`, [id]);
      }
      conn.run('DELETE FROM drives WHERE id=?', [id]);
      conn.run('DELETE FROM address_maps WHERE id NOT IN (SELECT map_id FROM drives WHERE map_id IS NOT NULL)');
      dropDriveView(conn, name);
    });
    removed.push(name);
  }
  return removed;
}

/** Remove captures by name (the drive and its rows stay; its counts follow). */
export function deleteCaptures(conn: Conn, names: Iterable<string>): string[] {
  const removed: string[] = [];
  for (const name of names) {
    const row = conn.get('SELECT id, drive_id, kind FROM captures WHERE name=?', [name]);
    if (!row) continue;
    conn.tx(() => {
      conn.run('DELETE FROM captures WHERE id=?', [row[0]]);
      conn.run('UPDATE drives SET capture_count = max(0, capture_count - 1), stall_count = max(0, stall_count - ?) WHERE id=?',
        [row[2] === 'stall' ? 1 : 0, row[1]]);
    });
    removed.push(name);
  }
  return removed;
}

/** Give free pages back (incremental vacuum, or ``full`` rewrite). */
export function vacuum(conn: Conn, full = false): { freePages: number; check: string; full: boolean } {
  const free = Number(conn.get('PRAGMA freelist_count')![0]);
  const check = String(conn.get('PRAGMA quick_check')![0]);
  if (full) conn.run('VACUUM');
  else conn.all('PRAGMA incremental_vacuum');
  return { freePages: free, check, full };
}

/** ``drives`` row id, or null. */
export function driveId(conn: Conn, name: string): number | null {
  const row = conn.get('SELECT id FROM drives WHERE name=?', [name]);
  return row ? Number(row[0]) : null;
}

export { LogFormatError };
