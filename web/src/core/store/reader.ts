/**
 * Reading logs (port of hondalog/data/reader.py): a ``Log`` is one drive in
 * the database, one capture (the drive cut to the capture's window), or a log
 * built in memory by an importer. Streams are read whole when the log is
 * opened (sql.js holds the file in memory anyway).
 */

import type { Conn } from './conn';
import { readChunks, streamInfos } from './db';
import { type Column, type LogEvent, MARKER_KINDS, type StreamData, castTo, emptyStreamData, isFloatDtype, searchsorted, takeRows, type StreamInfo } from './model';
import { pyParse } from './pyfmt';
import { SLOTS } from './schema';
import type { DriveRef } from './writer';

export interface Series {
  name: string;
  t: Float64Array;
  v: Float64Array;
  units: string;
}

export function rateHz(s: { t: ArrayLike<number> }): number {
  const n = s.t.length;
  if (n < 2) return 0;
  const d = Array.from({ length: n - 1 }, (_, i) => s.t[i + 1]! - s.t[i]!);
  const m = median(d);
  return m > 0 ? 1 / m : 0;
}

export function median(values: number[]): number {
  if (!values.length) return NaN;
  const s = [...values].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid]! : (s[mid - 1]! + s[mid]!) / 2;
}

/** Several channels on one shared time grid; NaN marks "no value here". */
export interface Table {
  t: Float64Array;
  columns: Record<string, Float64Array>;
  units: Record<string, string>;
  names: string[];
}

export class Log {
  meta: Record<string, any>;
  streams = new Map<string, StreamData>();
  events: LogEvent[];
  /** "recording", "complete", "crashed", "imported", or "memory" */
  status = 'memory';
  driveId: number | null = null;
  ref: DriveRef | null = null;
  private cache = new Map<string, Series>();

  constructor(meta: Record<string, any> = {}, streams: StreamData[] = [], events: LogEvent[] = []) {
    this.meta = { ...meta };
    for (const s of streams) this.streams.set(s.info.name, s);
    this.events = [...events].sort((a, b) => a.t - b.t);
  }

  get name(): string {
    return this.ref ? this.ref.capture || this.ref.drive : '';
  }

  /** Every channel name, in definition order, each listed once. */
  get channels(): string[] {
    const seen = new Set<string>();
    for (const s of this.streams.values()) for (const c of s.info.columns) seen.add(c.name);
    return [...seen];
  }

  column(name: string): Column | undefined {
    for (const s of this.streams.values()) {
      const c = s.info.column(name);
      if (c) return c;
    }
    return undefined;
  }

  units(name: string): string {
    return this.column(name)?.units ?? '';
  }

  streamsWith(name: string): StreamData[] {
    return [...this.streams.values()].filter((s) => s.info.column(name));
  }

  get t0(): number {
    const starts = [...this.streams.values()].filter((s) => s.t.length).map((s) => s.t[0]!);
    if (!starts.length && this.events.length) starts.push(this.events[0]!.t);
    return starts.length ? Math.min(...starts) : 0;
  }

  get t1(): number {
    const ends = [...this.streams.values()].filter((s) => s.t.length).map((s) => s.t[s.t.length - 1]!);
    if (!ends.length && this.events.length) ends.push(this.events[this.events.length - 1]!.t);
    return ends.length ? Math.max(...ends) : 0;
  }

  get duration(): number {
    return Math.max(0, this.t1 - this.t0);
  }

  get rows(): number {
    let n = 0;
    for (const s of this.streams.values()) n += s.t.length;
    return n;
  }

  /** (time, text) of everything the replay view draws as a marker line. */
  get markers(): [number, string][] {
    return this.events.filter((e) => MARKER_KINDS.includes(e.kind)).map((e) => [e.t, e.text || e.kind]);
  }

  eventsOf(...kinds: string[]): LogEvent[] {
    return this.events.filter((e) => kinds.includes(e.kind));
  }

  /** One channel at its own timestamps; a channel read in more than one stream is merged in time order. */
  series(name: string): Series {
    const hit = this.cache.get(name);
    if (hit) return hit;
    const parts = this.streamsWith(name).filter((s) => s.t.length);
    let out: Series;
    if (!parts.length) {
      if (!this.column(name)) throw new Error(`no channel ${JSON.stringify(name)} in this log`);
      out = { name, t: new Float64Array(0), v: new Float64Array(0), units: this.units(name) };
    } else if (parts.length === 1) {
      out = { name, t: parts[0]!.t.slice(), v: parts[0]!.columns[name]!.slice(), units: this.units(name) };
    } else {
      const pairs: [number, number][] = [];
      for (const p of parts) for (let i = 0; i < p.t.length; i++) pairs.push([p.t[i]!, p.columns[name]![i]!]);
      const order = pairs.map((_, i) => i).sort((a, b) => pairs[a]![0] - pairs[b]![0] || a - b);
      out = { name, t: Float64Array.from(order, (i) => pairs[i]![0]), v: Float64Array.from(order, (i) => pairs[i]![1]), units: this.units(name) };
    }
    this.cache.set(name, out);
    return out;
  }

  /**
   * Put channels on one time grid: ``t`` if given, else a uniform grid at
   * ``hz``, else the times of ``base``, else those of the channel with the
   * most samples. ``how``: "hold" or "linear"; values older than ``maxAge``
   * become NaN (default three sample periods of that channel, at least 0.5 s).
   */
  table(names?: string[], opts: { base?: string; hz?: number; t?: Float64Array; how?: 'hold' | 'linear'; maxAge?: number } = {}): Table {
    const list = names ?? this.channels;
    const series = list.map((n) => this.series(n));
    let grid = opts.t;
    if (!grid) {
      if (opts.hz) {
        const out: number[] = [];
        const step = 1 / opts.hz;
        const end = this.t1 + 0.5 / opts.hz;
        for (let k = 0; this.t0 + k * step < end; k++) out.push(this.t0 + k * step);
        grid = Float64Array.from(out);
      } else if (opts.base) grid = this.series(opts.base).t;
      else if (series.length) grid = series.reduce((a, b) => (b.t.length > a.t.length ? b : a)).t;
      else grid = new Float64Array(0);
    }
    const columns: Record<string, Float64Array> = {};
    const units: Record<string, string> = {};
    for (const s of series) {
      columns[s.name] = align(s, grid, opts.how ?? 'hold', opts.maxAge);
      units[s.name] = this.units(s.name);
    }
    return { t: grid, columns, units, names: [...list] };
  }

  /** An in-memory copy holding only rows and events with t0 <= t <= t1. */
  window(t0: number, t1: number): Log {
    const streams: StreamData[] = [];
    for (const s of this.streams.values()) {
      const lo = searchsorted(s.t, t0, 'left');
      const hi = searchsorted(s.t, t1, 'right');
      streams.push(takeRows(s, Array.from({ length: Math.max(0, hi - lo) }, (_, i) => lo + i)));
    }
    return new Log(this.meta, streams, this.events.filter((e) => e.t >= t0 && e.t <= t1));
  }
}

/** Sample a series at the times in ``grid``. */
export function align(series: { t: Float64Array; v: Float64Array }, grid: Float64Array, how: 'hold' | 'linear' = 'hold', maxAge?: number): Float64Array {
  const out = new Float64Array(grid.length).fill(NaN);
  const st = series.t;
  const sv = series.v;
  if (!st.length || !grid.length) return out;
  if (st === grid || (st.length === grid.length && st.every((x, i) => x === grid[i]))) return sv.slice();
  let age = maxAge;
  if (age === undefined) {
    age = st.length > 1 ? Math.max(3 * median(Array.from({ length: st.length - 1 }, (_, i) => st[i + 1]! - st[i]!)), 0.5) : Infinity;
  }
  const last = st.length - 1;
  for (let k = 0; k < grid.length; k++) {
    const g = grid[k]!;
    let idx = searchsorted(st, g, 'right') - 1;
    let ok = idx >= 0;
    idx = Math.min(Math.max(idx, 0), last);
    if (how === 'linear') {
      const nxt = Math.min(idx + 1, last);
      const span = st[nxt]! - st[idx]!;
      let frac = span > 0 ? (g - st[idx]!) / span : 0;
      frac = Math.min(Math.max(frac, 0), 1);
      const value = sv[idx]! + (sv[nxt]! - sv[idx]!) * frac;
      ok = ok && (span <= age || span === 0) && g - st[idx]! <= age && g <= st[last]!;
      if (ok) out[k] = value;
    } else {
      if (ok && g - st[idx]! <= age) out[k] = sv[idx]!;
    }
  }
  return out;
}

// ---- drives in the database --------------------------------------------------

/** drives columns that are shown under another name in the meta */
const META_COLUMNS: Record<string, string> = {
  ecu: 'ecu', ecu_id: 'ecu_id', port: 'port', vehicle: 'vehicle', map_version: 'address_map_version',
  map_tag: 'address_map_tag', map_hash: 'address_map_hash', started_iso: 'started', started_at: 'started_unix',
  epoch_unix: 'epoch_unix', notes: 'notes',
};
const JSON_META: Record<string, string> = { link_stats: 'link', unlocked_frames: 'unlocked_frames', read_plan: 'read_plan', tags: 'tags' };

/** A drive's row as the header dictionary the recorder was given. */
export function driveMeta(conn: Conn, driveId: number): Record<string, any> {
  const row = conn.objects('SELECT d.*, m.text AS map_text, m.file AS map_file FROM drives d ' +
    'LEFT JOIN address_maps m ON m.id = d.map_id WHERE d.id=?', [driveId])[0]!;
  const meta: Record<string, any> = pyParse(row.meta as string) ?? {};
  for (const [col, key] of Object.entries(META_COLUMNS)) {
    const v = row[col];
    if (v !== null && v !== undefined && v !== '') meta[key] = v;
  }
  for (const [col, key] of Object.entries(JSON_META)) {
    if (row[col]) {
      const value = pyParse(row[col] as string);
      const empty = Array.isArray(value) ? value.length === 0 : value === null || value === '' ||
        (typeof value === 'object' && !Object.keys(value).length);
      if (!empty || key !== 'tags') meta[key] = value;
    }
  }
  if (row.expert_unlock || 'unlocked_frames' in meta) meta.expert_unlock = !!row.expert_unlock;
  if (row.keep) meta.keep = true;
  if (row.map_text !== null && row.map_text !== undefined) {
    meta.address_map_text = row.map_text;
    if (row.map_file) meta.address_map_file = row.map_file;
  }
  meta.drive = row.name;
  meta.status = row.status;
  if (row.end_reason) meta.end_reason = row.end_reason;
  if (row.ended_iso) meta.ended = row.ended_iso;
  return meta;
}

function between(t0: number | null, t1: number | null, args: unknown[]): string {
  let sql = '';
  if (t0 !== null) { sql += ' AND t >= ?'; args.push(t0); }
  if (t1 !== null) { sql += ' AND t <= ?'; args.push(t1); }
  return sql;
}

/** The rows of one ``samples`` stream. */
export function loadRows(conn: Conn, driveId: number, streamId: number, info: StreamInfo, t0: number | null = null, t1: number | null = null): StreamData {
  const names = info.columnNames;
  const parts = Math.max(1, Math.ceil(names.length / SLOTS));
  let out = emptyStreamData(info);
  for (let part = 0; part < parts; part++) {
    const mine = names.slice(part * SLOTS, (part + 1) * SLOTS);
    const head = part === 0 ? 't, raw, latency_us' : 't';
    const cols = mine.map((_, k) => `, v${k}`).join('');
    const args: unknown[] = [driveId, streamId, part];
    const fetched = conn.all(`SELECT ${head}${cols} FROM samples WHERE drive_id=? AND stream_id=? AND part=?` + between(t0, t1, args), args);
    let first: number;
    if (part === 0) {
      out = emptyStreamData(info, fetched.length);
      if (!fetched.length) return out;
      const w = info.rawLen;
      fetched.forEach((r, i) => {
        out.t[i] = Number(r[0]);
        out.latency[i] = r[2] === null ? NaN : Number(r[2]) / 1e6;
        const blob = r[1] as Uint8Array | null;
        if (w && blob && blob.length === w) out.raw.set(blob, i * w);
      });
      first = 3;
    } else {
      if (fetched.length !== out.t.length) break;          // a torn multi-part row: keep what lines up
      first = 1;
    }
    mine.forEach((name, k) => {
      const c = info.column(name)!;
      const target = out.columns[name]!;
      fetched.forEach((r, i) => {
        const v = r[first + k];
        let x = v === null || v === undefined ? NaN : Number(v);
        if (!isFloatDtype(c.dtype)) x = x !== x ? 0 : castTo(c.dtype, x);
        else x = castTo(c.dtype, x);
        target[i] = x;
      });
    });
  }
  return out;
}

export interface FoundRef { ref: DriveRef; driveId: number }

/** A drive name, drive id, capture name, or "last" / "" for the newest drive. */
export function findRef(conn: Conn, name: string | number): FoundRef | null {
  const key = String(name).trim();
  if (key === '' || key === 'last' || key === 'latest') {
    const row = conn.get('SELECT name, id FROM drives ORDER BY coalesce(started_at, 0) DESC, id DESC LIMIT 1');
    return row ? { ref: { drive: String(row[0]), capture: '' }, driveId: Number(row[1]) } : null;
  }
  let row = conn.get('SELECT name, id FROM drives WHERE name=?', [key]);
  if (!row && /^\d+$/.test(key)) row = conn.get('SELECT name, id FROM drives WHERE id=?', [Number(key)]);
  if (row) return { ref: { drive: String(row[0]), capture: '' }, driveId: Number(row[1]) };
  const cap = conn.get('SELECT d.name, c.name, d.id FROM captures c JOIN drives d ON d.id = c.drive_id WHERE c.name=?', [key]);
  return cap ? { ref: { drive: String(cap[0]), capture: String(cap[1]) }, driveId: Number(cap[2]) } : null;
}

/** Open one drive (default: the newest) or one capture. Works while the drive is being recorded. */
export function readDrive(conn: Conn, name: string | number = '', range: { t0?: number; t1?: number } = {}): Log {
  const found = findRef(conn, name);
  if (!found) {
    throw new Error(name !== '' ? `no drive or capture ${JSON.stringify(String(name))}` : 'no drives recorded yet');
  }
  const { ref, driveId } = found;
  const status = String(conn.get('SELECT status FROM drives WHERE id=?', [driveId])![0]);
  const meta = driveMeta(conn, driveId);
  let t0: number | null = range.t0 ?? null;
  let t1: number | null = range.t1 ?? null;
  if (ref.capture) {
    const cap = conn.objects('SELECT * FROM captures WHERE name=?', [ref.capture])[0]!;
    t0 = t0 === null ? Number(cap.t_start) : Math.max(t0, Number(cap.t_start));
    t1 = t1 === null ? Number(cap.t_end) : Math.min(t1, Number(cap.t_end));
    Object.assign(meta, pyParse(cap.meta as string) ?? {});
    meta.capture = cap.name;
    meta.capture_of = ref.drive;
  }
  const log = new Log(meta);
  log.status = status;
  log.driveId = driveId;
  log.ref = ref;
  for (const [sid, { info, storage }] of streamInfos(conn, driveId)) {
    const data = storage === 'chunks' ? readChunks(conn, driveId, sid, info, t0, t1) : loadRows(conn, driveId, sid, info, t0, t1);
    log.streams.set(info.name, data);
  }
  const args: unknown[] = [driveId];
  const span = between(t0, t1, args);
  const events: LogEvent[] = conn.all('SELECT t, kind, text, data FROM events WHERE drive_id=?' + span + ' ORDER BY t, id', args)
    .map(([t, kind, text, data]) => ({ t: Number(t), kind: String(kind), text: String(text ?? ''), data: data ? pyParse(data as string) ?? {} : {} }));
  for (const [t, text, data] of conn.all('SELECT t, text, data FROM alarms WHERE drive_id=?' + span + ' ORDER BY t, id', args)) {
    events.push({ t: Number(t), kind: 'alarm', text: String(text ?? ''), data: data ? pyParse(data as string) ?? {} : {} });
  }
  log.events = events.map((e, i) => [e, i] as const).sort((a, b) => a[0].t - b[0].t || a[1] - b[1]).map(([e]) => e);
  return log;
}
