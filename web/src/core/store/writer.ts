/**
 * Writing to the database (port of hondalog/data/store.py): the ``Store``
 * and the ``DriveWriter`` it hands out per drive.
 *
 *     const store = new Store(hdb);
 *     const drive = store.beginDrive({ ecu_id: '...' });
 *     drive.addStream('ecu/00+2', [column('rpm')], { source: 'ecu', rawLen: 2, attrs: { addr: 0 } });
 *     drive.append('ecu/00+2', t, [812], raw, 0.004);
 *     drive.event(t, 'marker', 'MARK 1');
 *     drive.end();
 *     store.close();
 *
 * ``append`` and ``event`` only touch memory. A timer commits everything
 * buffered in one transaction every ``flushIntervalS`` (0.5 s); the file is
 * written to the FileStore every ``checkpointS`` (and when a drive ends), the
 * most a power cut can take. ``background: false`` commits only on ``flush``
 * and ``close`` (importers, tests).
 *
 * High-rate streams (``storage: 'chunks'``, the default for black box
 * streams) are appended as ``raw`` BLOBs and packed into ``d1`` blocks with
 * their one-second statistics once a minute and at the end of the drive.
 */

import { sha256 } from '@noble/hashes/sha256';
import { bytesToHex } from '@noble/hashes/utils';
import * as codec from './codec';
import type { Conn } from './conn';
import { HondalogDb, SESSION_PID, compactStream, driveStem, finishDrive, readChunks, storageFor, streamInfos, uniqueName, writeBlock } from './db';
import { type Column, type LogEvent, type StreamData, StreamInfo, packRow, sortByTime } from './model';
import { localIso, localParts, parseLocalIso, pyJson, pyParse } from './pyfmt';
import { SLOTS, makeDriveView } from './schema';

export const HEARTBEAT_S = 5.0;

/** One drive, or one capture (a time range of a drive). */
export interface DriveRef {
  drive: string;
  capture: string;
}

export const refName = (ref: DriveRef): string => ref.capture || ref.drive;

const TEXT_COLUMNS: Record<string, string> = {
  ecu: 'ecu', ecu_id: 'ecu_id', port: 'port', vehicle: 'vehicle', notes: 'notes',
  address_map_version: 'map_version', address_map_tag: 'map_tag', address_map_hash: 'map_hash', started: 'started_iso',
};
const REAL_COLUMNS: Record<string, string> = { started_unix: 'started_at', epoch_unix: 'epoch_unix' };
const JSON_COLUMNS: Record<string, string> = { link: 'link_stats', unlocked_frames: 'unlocked_frames', read_plan: 'read_plan' };
const BOOL_COLUMNS: Record<string, string> = { expert_unlock: 'expert_unlock', keep: 'keep' };
/** keys the reader adds to the meta; they describe the row, they are not stored in it */
const DERIVED = new Set(['drive', 'status', 'end_reason', 'ended', 'address_map_file']);

export function sha256Text(text: string): string {
  return bytesToHex(sha256(new TextEncoder().encode(text)));
}

/** Merge header facts into a drive's row. Call inside a transaction. */
export function applyMeta(conn: Conn, driveId: number, items: Record<string, unknown>): void {
  const sets: Record<string, unknown> = {};
  const rest: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(items)) {
    if (value === undefined) continue;
    if (key in TEXT_COLUMNS) sets[TEXT_COLUMNS[key]!] = value === null ? '' : String(value);
    else if (key in REAL_COLUMNS) sets[REAL_COLUMNS[key]!] = value === null ? null : Number(value);
    else if (key in JSON_COLUMNS) sets[JSON_COLUMNS[key]!] = pyJson(value);
    else if (key in BOOL_COLUMNS) sets[BOOL_COLUMNS[key]!] = value ? 1 : 0;
    else if (key === 'tags') {
      const tags = Array.isArray(value) ? value : String(value ?? '').split(/\s+/).filter(Boolean);
      sets.tags = pyJson(tags.map(String));
    } else if (key === 'address_map_text') {
      const text = String(value ?? '');
      if (text) {
        const digest = sha256Text(text);
        conn.run('INSERT OR IGNORE INTO address_maps(sha256, version, tag, hash, file, text) VALUES(?,?,?,?,?,?)',
          [digest, String(items.address_map_version ?? ''), String(items.address_map_tag ?? ''),
            String(items.address_map_hash ?? ''), String(items.address_map_file ?? ''), text]);
        sets.map_id = Number(conn.get('SELECT id FROM address_maps WHERE sha256=?', [digest])![0]);
      }
    } else if (!DERIVED.has(key)) rest[key] = value;
  }
  if ('address_map_file' in items && !('address_map_text' in items)) rest.address_map_file = items.address_map_file;
  if (Object.keys(rest).length) {
    const old = conn.get('SELECT meta FROM drives WHERE id=?', [driveId]);
    const merged = old ? { ...(pyParse(old[0] as string) ?? {}), ...rest } : rest;
    sets.meta = pyJson(merged);
  }
  const keys = Object.keys(sets);
  if (keys.length) conn.run(`UPDATE drives SET ${keys.map((k) => `${k}=?`).join(', ')} WHERE id=?`, [...Object.values(sets), driveId]);
}

class RowStream {
  readonly chunked = false;
  readonly ncols: number;
  readonly parts: number;
  readonly sql: string[] = [];
  buf: unknown[][][];
  maxT = -Infinity;
  dup = 0;
  rows = 0;

  constructor(readonly info: StreamInfo, readonly sid: number) {
    this.ncols = info.columns.length;
    this.parts = Math.max(1, Math.ceil(this.ncols / SLOTS));
    for (let part = 0; part < this.parts; part++) {
      const n = Math.min(SLOTS, this.ncols - part * SLOTS);
      const names = Array.from({ length: n }, (_, k) => `, v${k}`).join('');
      this.sql.push('INSERT INTO samples(drive_id, stream_id, part, t, n, raw, latency_us' + names +
        ') VALUES(?,?,?,?,?,?,?' + ',?'.repeat(n) + ')');
    }
    this.buf = Array.from({ length: this.parts }, () => []);
  }
}

class ChunkStream {
  readonly chunked = true;
  readonly ncols: number;
  buf: Uint8Array[] = [];
  t0 = 0;
  t1 = 0;
  rows = 0;
  /** time of the oldest row still in a "raw" chunk */
  rawSince: number | null = null;
  /** whole blocks waiting to be packed */
  blocks: StreamData[] = [];

  constructor(readonly info: StreamInfo, readonly sid: number) {
    this.ncols = info.columns.length;
  }
}

interface Batch {
  defs: [number, number, StreamInfo, string][];
  meta: [number, Record<string, unknown>][];
  many: [string, unknown[][]][];
  raw: [DriveWriter, ChunkStream, Uint8Array, number, number, number][];
  blocks: [number, ChunkStream, StreamData][];
  events: [number, LogEvent][];
  views: number[];
  rows: number;
}

const emptyBatch = (): Batch => ({ defs: [], meta: [], many: [], raw: [], blocks: [], events: [], views: [], rows: 0 });
const batchHasData = (b: Batch) => !!(b.defs.length || b.meta.length || b.many.length || b.raw.length || b.blocks.length || b.events.length || b.views.length);

export interface AddStreamOptions {
  source?: string;
  rawLen?: number;
  attrs?: Record<string, unknown>;
  storage?: string;
}

export interface CaptureOptions {
  rule?: string;
  reason?: string;
  label?: string;
  message?: string;
  severity?: string;
  /** wall time of the trigger, unix seconds (default: now) */
  wallUnix?: number;
  complete?: boolean;
  fired?: unknown[];
  meta?: Record<string, unknown>;
}

export class DriveWriter {
  rows = 0;
  closed = false;
  readonly streams = new Map<string, RowStream | ChunkStream>();
  pendingEvents: LogEvent[] = [];
  pendingMeta: Record<string, unknown> = {};
  defs: [number, StreamInfo, string][] = [];
  viewDirty = false;

  constructor(readonly store: Store, readonly id: number, readonly name: string) {}

  get ref(): DriveRef {
    return { drive: this.name, capture: '' };
  }

  stream(name: string): StreamInfo | undefined {
    return this.streams.get(name)?.info;
  }

  /** Add or replace header facts. */
  setMeta(items: Record<string, unknown>): void {
    this.checkOpen();
    Object.assign(this.pendingMeta, items);
  }

  /** Define a stream; defining the same stream twice is fine. */
  addStream(name: string, columns: readonly Column[], opts: AddStreamOptions = {}): string {
    const info = new StreamInfo(name, columns, opts.source ?? '', opts.rawLen ?? 0, { ...(opts.attrs ?? {}) });
    this.checkOpen();
    const old = this.streams.get(name);
    if (old) {
      if (!old.info.sameLayout(info)) throw new Error(`stream ${JSON.stringify(name)} already exists with other columns`);
      return name;
    }
    const sid = this.streams.size + 1;
    const kind = opts.storage ?? storageFor(info);
    if (kind !== 'rows' && kind !== 'chunks') throw new Error(`stream ${JSON.stringify(name)}: storage must be 'rows' or 'chunks'`);
    this.streams.set(name, kind === 'chunks' ? new ChunkStream(info, sid) : new RowStream(info, sid));
    this.defs.push([sid, info, kind]);
    this.viewDirty = true;
    return name;
  }

  /** Add one row. null / NaN is "no value". ``latency``: estimated age of a K-line reply, seconds. */
  append(stream: string, t: number, values: readonly (number | null | undefined)[], raw?: Uint8Array | null, latency?: number | null): void {
    const state = this.streams.get(stream);
    if (!state) throw new Error(`no stream ${JSON.stringify(stream)}`);
    const rawLen = raw ? raw.length : 0;
    if (rawLen !== state.info.rawLen) throw new Error(`stream ${JSON.stringify(stream)} takes ${state.info.rawLen} raw bytes, got ${rawLen}`);
    if (values.length !== state.ncols) throw new Error(`stream ${JSON.stringify(stream)} takes ${state.ncols} values, got ${values.length}`);
    this.checkOpen();
    if (state.chunked) {
      const row = packRow(state.info, t, values, raw ?? undefined);
      if (!state.buf.length) state.t0 = t;
      state.t1 = t;
      state.buf.push(row);
      state.rows++;
      this.rows++;
      return;
    }
    const blob = raw && raw.length ? raw.slice() : null;
    const micros = latency === null || latency === undefined || latency !== latency ? null : Math.trunc(latency * 1e6);
    let n: number;
    if (t > state.maxT) {
      state.maxT = t;
      n = 0;
    } else {                                  // equal or out-of-order time: keep the key unique
      state.dup++;
      n = state.dup;
    }
    const vals = values.map((v) => (v === undefined ? null : v));
    if (state.parts === 1) state.buf[0]!.push([this.id, state.sid, 0, t, n, blob, micros, ...vals]);
    else {
      for (let part = 0; part < state.parts; part++) {
        state.buf[part]!.push([this.id, state.sid, part, t, n, part === 0 ? blob : null, part === 0 ? micros : null,
          ...vals.slice(part * SLOTS, (part + 1) * SLOTS)]);
      }
    }
    state.rows++;
    this.rows++;
  }

  /** Add many rows at once (column-wise data in the stream's layout). */
  appendData(stream: string, data: StreamData): void {
    const state = this.streams.get(stream);
    if (!state) throw new Error(`no stream ${JSON.stringify(stream)}`);
    const n = data.t.length;
    if (!n) return;
    this.checkOpen();
    if (state.chunked) {
      const rows = sortByTime(data);
      const BLOCK = 65536;
      for (let i = 0; i < n; i += BLOCK) {
        const idx = Array.from({ length: Math.min(BLOCK, n - i) }, (_, k) => i + k);
        state.blocks.push(i === 0 && n <= BLOCK ? rows : { ...pick(rows, idx) });
      }
      state.rows += n;
      this.rows += n;
      return;
    }
    const names = state.info.columnNames;
    const w = state.info.rawLen;
    for (let i = 0; i < n; i++) {
      const t = data.t[i]!;
      let tie: number;
      if (t > state.maxT) {
        state.maxT = t;
        tie = 0;
      } else {
        state.dup++;
        tie = state.dup;
      }
      const blob = w ? data.raw.slice(i * w, (i + 1) * w) : null;
      const lat = data.latency[i]!;
      const micros = lat === lat ? Math.trunc(lat * 1e6) : null;
      const vals = names.map((c) => data.columns[c]![i]!);
      for (let part = 0; part < state.parts; part++) {
        state.buf[part]!.push([this.id, state.sid, part, t, tie, part === 0 ? blob : null, part === 0 ? micros : null,
          ...vals.slice(part * SLOTS, (part + 1) * SLOTS)]);
      }
    }
    state.rows += n;
    this.rows += n;
  }

  event(t: number, kind: string, text = '', data: Record<string, unknown> | null = null): void {
    this.checkOpen();
    this.pendingEvents.push({ t: Number(t), kind, text, data: { ...(data ?? {}) } });
  }

  /** Record a capture: the window [tStart, tEnd] of this drive and what fired. No data is copied. */
  capture(kind: string, tTrigger: number, tStart: number, tEnd: number, opts: CaptureOptions = {}): { ref: DriveRef; rows: number } {
    const store = this.store;
    const conn = store.hdb.conn;
    store.write();
    const wall = opts.wallUnix ?? store.hdb.now();
    const p = localParts(wall);
    let name = '';
    let rows = 0;
    conn.tx(() => {
      name = uniqueName(conn, 'captures', `${kind}_${p.Y}${p.m}${p.d}_${p.H}${p.M}${p.S}`);
      rows = countRows(conn, this.id, tStart, tEnd);
      conn.run('INSERT INTO captures(drive_id, name, kind, rule, reason, label, message, severity, ' +
        't_trigger, t_start, t_end, wall_unix, wall_iso, complete, rows, fired, meta) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
      [this.id, name, kind, opts.rule ?? '', opts.reason ?? '', opts.label ?? '', opts.message ?? '', opts.severity ?? '',
        tTrigger, tStart, tEnd, wall, localIso(wall, 'milliseconds'), opts.complete === false ? 0 : 1, rows,
        pyJson(opts.fired ?? []), pyJson(opts.meta ?? {})]);
      conn.run('UPDATE drives SET capture_count = capture_count + 1, stall_count = stall_count + ? WHERE id=?',
        [kind === 'stall' ? 1 : 0, this.id]);
    });
    store.hdb.dirty = true;
    return { ref: { drive: this.name, capture: name }, rows };
  }

  /** Re-file a saved capture (found by rule and trigger time) under another kind. Works after the drive ended. */
  relabelCapture(tTrigger: number, rule: string, opts: { kind: string; reason?: string; label?: string; message?: string; severity?: string; meta?: Record<string, unknown> }): void {
    const store = this.store;
    const conn = store.hdb.conn;
    store.write();
    const row = conn.get('SELECT id, kind, meta FROM captures WHERE drive_id=? AND rule=? AND abs(t_trigger - ?) < 0.0005 ORDER BY id DESC LIMIT 1',
      [this.id, rule, tTrigger]);
    if (!row) return;
    const [cid, oldKind, oldMeta] = row;
    let items: Record<string, unknown>;
    try {
      items = pyParse(oldMeta as string) ?? {};
    } catch {
      items = {};
    }
    Object.assign(items, opts.meta ?? {});
    conn.tx(() => {
      conn.run('UPDATE captures SET kind=?, reason=?, label=?, message=?, severity=?, meta=? WHERE id=?',
        [opts.kind, opts.reason ?? '', opts.label ?? '', opts.message ?? '', opts.severity ?? '', pyJson(items), cid]);
      const delta = (opts.kind === 'stall' ? 1 : 0) - (oldKind === 'stall' ? 1 : 0);
      if (delta) conn.run('UPDATE drives SET stall_count = max(0, stall_count + ?) WHERE id=?', [delta, this.id]);
    });
    store.hdb.dirty = true;
  }

  /** Flush, pack the black box tail, set the end time and the status. */
  end(status = 'complete', reason = 'stopped'): void {
    if (this.closed) return;
    this.closed = true;
    const store = this.store;
    try {
      store.write();
      finishDrive(store.hdb.conn, this.id, status, reason);
      store.hdb.dirty = true;
    } finally {
      store.hdb.active.delete(this.id);
      const i = store.drives.indexOf(this);
      if (i >= 0) store.drives.splice(i, 1);
    }
    store.checkpoint();
  }

  checkOpen(): void {
    if (this.closed) throw new Error(`${this.name}: the drive is closed`);
  }

  hasData(): boolean {
    if (this.defs.length || this.pendingEvents.length || Object.keys(this.pendingMeta).length || this.viewDirty) return true;
    for (const s of this.streams.values()) {
      if (s.chunked ? s.buf.length || s.blocks.length : s.buf.some((b) => b.length)) return true;
    }
    return false;
  }
}

function pick(data: StreamData, idx: number[]): StreamData {
  const out: StreamData = { info: data.info, t: new Float64Array(idx.length), columns: {}, raw: new Uint8Array(idx.length * data.info.rawLen), latency: new Float64Array(idx.length) };
  for (const c of data.info.columns) out.columns[c.name] = new Float64Array(idx.length);
  idx.forEach((i, j) => {
    out.t[j] = data.t[i]!;
    out.latency[j] = data.latency[i]!;
    for (const c of data.info.columns) out.columns[c.name]![j] = data.columns[c.name]![i]!;
    if (data.info.rawLen) out.raw.set(data.raw.subarray(i * data.info.rawLen, (i + 1) * data.info.rawLen), j * data.info.rawLen);
  });
  return out;
}

export function countRows(conn: Conn, driveId: number, t0: number, t1: number): number {
  let total = 0;
  for (const [sid, { info, storage }] of streamInfos(conn, driveId)) {
    if (storage === 'chunks') total += readChunks(conn, driveId, sid, info, t0, t1).t.length;
    else {
      total += Number(conn.get('SELECT count(*) FROM samples WHERE drive_id=? AND stream_id=? AND part=0 AND t >= ? AND t <= ?',
        [driveId, sid, t0, t1])![0]);
    }
  }
  return total;
}

export interface StoreOptions {
  /** commit interval, seconds (0.5) */
  flushIntervalS?: number;
  /** commit on a timer (default true); false: only on flush / close */
  background?: boolean;
  /** pack a black box stream's raw chunks after this many seconds (60) */
  compactS?: number;
  /** write the file to the FileStore at most this often while recording, seconds (10); 0: only when a drive ends */
  checkpointS?: number;
}

export class Store {
  readonly drives: DriveWriter[] = [];
  /** last write error, if any */
  error: Error | null = null;
  closed = false;
  readonly flushIntervalS: number;
  readonly compactS: number;
  readonly checkpointS: number;
  private heartbeat: number;
  private lastCheckpoint: number;
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(readonly hdb: HondalogDb, opts: StoreOptions = {}) {
    this.flushIntervalS = opts.flushIntervalS ?? 0.5;
    this.compactS = opts.compactS ?? 60;
    this.checkpointS = opts.checkpointS ?? 10;
    this.heartbeat = hdb.now();
    this.lastCheckpoint = hdb.now();
    hdb.writers.add(this);
    if (opts.background ?? true) {
      this.timer = setInterval(() => this.tick(), Math.max(10, this.flushIntervalS * 1000));
    }
  }

  /** Start a drive. Its name (``drive_2026_11_16_0712``, made unique) is also the name of its view. */
  beginDrive(meta: Record<string, unknown> = {}, opts: { startedUnix?: number; name?: string } = {}): DriveWriter {
    this.checkOpen();
    const header: Record<string, unknown> = { ...meta };
    let started = opts.startedUnix;
    if (started === undefined) {
      const unix = header.started_unix;
      if (typeof unix === 'number') started = unix;
      else {
        const parsed = parseLocalIso(String(header.started ?? ''));
        started = Number.isFinite(parsed) ? parsed : this.hdb.now();
      }
    }
    if (!('started' in header)) header.started = localIso(started);
    if (!('started_unix' in header)) header.started_unix = started;
    const conn = this.hdb.conn;
    let id = 0;
    let unique = '';
    conn.tx(() => {
      unique = uniqueName(conn, 'drives', opts.name ?? driveStem(started!));
      conn.run('INSERT INTO drives(name, status, writer_pid, heartbeat_at) VALUES(?,?,?,?)', [unique, 'recording', SESSION_PID, this.hdb.now()]);
      id = conn.lastId();
      applyMeta(conn, id, header);
      makeDriveView(conn, id);
      this.hdb.active.add(id);
    });
    this.hdb.dirty = true;
    const drive = new DriveWriter(this, id, unique);
    this.drives.push(drive);
    return drive;
  }

  /** Write everything buffered so far. ``persist``: also write the file now. */
  flush(persist = false): Promise<void> | void {
    this.write();
    if (this.error) throw this.error;
    if (persist) return this.hdb.persist();
  }

  /** End any drive still open, write everything, write the file. */
  async close(): Promise<void> {
    if (this.closed) return;
    for (const drive of [...this.drives]) {
      try {
        drive.end('complete', 'closed');
      } catch (err) {
        this.error = err as Error;
      }
    }
    this.closed = true;
    this.hdb.writers.delete(this);
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    try {
      this.write();
      this.hdb.conn.all('PRAGMA incremental_vacuum(2000)');
    } catch (err) {
      this.error = this.error ?? (err as Error);
    }
    try {
      await this.hdb.persist();
    } catch (err) {
      this.error = this.error ?? (err as Error);
    }
    if (this.error) throw this.error;
  }

  /** One timer tick: commit, and write the file when it is due. */
  tick(): void {
    if (this.closed) return;
    this.write();
    const now = this.hdb.now();
    if (this.checkpointS > 0 && now - this.lastCheckpoint >= this.checkpointS && this.hdb.dirty) this.checkpoint();
  }

  /** Write the file to the FileStore now (errors land in ``error``). */
  checkpoint(): void {
    this.lastCheckpoint = this.hdb.now();
    if (!this.hdb.fileStore) return;
    this.hdb.persist().catch((err: unknown) => {
      this.error = err instanceof Error ? err : new Error(String(err));
    });
  }

  private checkOpen(): void {
    if (this.closed) throw new Error('the store is closed');
  }

  private take(): Batch {
    const batch = emptyBatch();
    for (const drive of this.drives) {
      if (!drive.hasData()) continue;
      for (const [sid, info, kind] of drive.defs) batch.defs.push([drive.id, sid, info, kind]);
      drive.defs = [];
      if (Object.keys(drive.pendingMeta).length) {
        batch.meta.push([drive.id, drive.pendingMeta]);
        drive.pendingMeta = {};
      }
      for (const state of drive.streams.values()) {
        if (state.chunked) {
          if (state.buf.length) {
            const n = state.buf.length;
            const bytes = new Uint8Array(n * state.info.itemsize);
            state.buf.forEach((row, i) => bytes.set(row, i * state.info.itemsize));
            batch.raw.push([drive, state, bytes, n, state.t0, state.t1]);
            batch.rows += n;
            state.buf = [];
          }
          if (state.blocks.length) {
            for (const block of state.blocks) {
              batch.blocks.push([drive.id, state, block]);
              batch.rows += block.t.length;
            }
            state.blocks = [];
          }
        } else {
          state.buf.forEach((rows, part) => {
            if (rows.length) {
              batch.many.push([state.sql[part]!, rows]);
              batch.rows += rows.length;
              state.buf[part] = [];
            }
          });
        }
      }
      if (drive.pendingEvents.length) {
        for (const e of drive.pendingEvents) batch.events.push([drive.id, e]);
        drive.pendingEvents = [];
      }
      if (drive.viewDirty) {
        batch.views.push(drive.id);
        drive.viewDirty = false;
      }
    }
    return batch;
  }

  /** One transaction with everything buffered. Never throws for a write error: ``error`` says what happened. */
  write(): void {
    if (this.hdb.closed) return;
    const batch = this.take();
    const now = this.hdb.now();
    const beat = now - this.heartbeat >= HEARTBEAT_S && this.drives.length > 0;
    if (!batchHasData(batch) && !beat) return;
    try {
      this.hdb.conn.tx(() => {
        this.writeBatch(batch);
        if (beat) {
          this.heartbeat = now;
          this.hdb.conn.many('UPDATE drives SET heartbeat_at=? WHERE id=?', this.drives.map((d) => [now, d.id]));
        }
      });
      this.hdb.dirty = true;
    } catch (err) {
      this.error = err instanceof Error ? err : new Error(String(err));
    }
  }

  private writeBatch(batch: Batch): void {
    const conn = this.hdb.conn;
    for (const [driveId, sid, info, kind] of batch.defs) {
      const addr = info.attrs.addr;
      conn.run('INSERT INTO streams(drive_id, id, name, source, storage, raw_len, addr, attrs) VALUES(?,?,?,?,?,?,?,?)',
        [driveId, sid, info.name, info.source, kind, info.rawLen, typeof addr === 'number' && Number.isInteger(addr) ? addr : null, pyJson(info.attrs)]);
      conn.many('INSERT INTO channels(drive_id, stream_id, position, part, slot, name, units, label, kind, ' +
        'dtype, decimals, lo, hi, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
      info.columns.map((c, i) => [driveId, sid, i, Math.floor(i / SLOTS), i % SLOTS, c.name, c.units, c.label, c.kind, c.dtype,
        c.decimals, c.lo, c.hi, info.source]));
    }
    for (const [driveId, items] of batch.meta) applyMeta(conn, driveId, items);
    for (const [sql, rows] of batch.many) conn.many(sql, rows);
    for (const [drive, state, data, n, t0, t1] of batch.raw) {
      conn.run('INSERT INTO chunks(drive_id, stream_id, t_start, t_end, n_rows, codec, data) VALUES(?,?,?,?,?,?,?)',
        [drive.id, state.sid, t0, t1, n, codec.RAW, data]);
      if (state.rawSince === null) state.rawSince = t0;
      if (t1 - state.rawSince >= this.compactS) {
        compactStream(conn, drive.id, state.sid, state.info);
        state.rawSince = null;
      }
    }
    for (const [driveId, state, block] of batch.blocks) writeBlock(conn, driveId, state.sid, block);
    const plain = batch.events.filter(([, e]) => e.kind !== 'alarm')
      .map(([d, e]) => [d, e.t, e.kind, e.text, Object.keys(e.data).length ? pyJson(e.data) : null]);
    conn.many('INSERT INTO events(drive_id, t, kind, text, data) VALUES(?,?,?,?,?)', plain);
    const alarms = batch.events.filter(([, e]) => e.kind === 'alarm').map(([d, e]) => {
      const v = e.data.value;
      return [d, e.t, String(e.data.alarm ?? ''), String(e.data.kind ?? ''), String(e.data.severity ?? ''),
        String(e.data.channel ?? '') || '', typeof v === 'number' ? v : null, e.data.unverified ? 1 : 0, e.text,
        Object.keys(e.data).length ? pyJson(e.data) : null];
    });
    conn.many('INSERT INTO alarms(drive_id, t, name, state, severity, channel, value, unverified, text, data) VALUES(?,?,?,?,?,?,?,?,?,?)', alarms);
    for (const driveId of batch.views) makeDriveView(conn, driveId);
  }
}

/** Write one drive on the calling thread (converters, importers, tests). */
export function newDrive(hdb: HondalogDb, meta: Record<string, unknown>, fill: (drive: DriveWriter) => void,
  opts: { status?: string; name?: string } = {}): DriveRef {
  const store = new Store(hdb, { background: false, checkpointS: 0 });
  const drive = store.beginDrive(meta, { name: opts.name });
  try {
    fill(drive);
    drive.end(opts.status ?? 'complete', opts.status === 'imported' ? 'import' : 'stopped');
  } finally {
    store.closed = true;
    hdb.writers.delete(store);
  }
  return drive.ref;
}

/** Add or change header facts (notes, tags, keep, ...) of a drive. */
export function annotate(hdb: HondalogDb, drive: string, meta: Record<string, unknown>): void {
  const row = hdb.conn.get('SELECT id FROM drives WHERE name=?', [drive]);
  if (!row) throw new Error(`${drive}: no such drive`);
  hdb.conn.tx(() => applyMeta(hdb.conn, Number(row[0]), meta));
  hdb.dirty = true;
}
