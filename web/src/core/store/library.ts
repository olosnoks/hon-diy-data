/**
 * The drive library (port of hondalog/data/library.py and retention.py):
 * every drive and capture straight from SQL, retention, deleting by hand.
 */

import type { Conn } from './conn';
import { type HondalogDb, deleteCaptures, deleteDrives, vacuum } from './db';
import { pyParse } from './pyfmt';
import type { DriveRef } from './writer';

export interface DriveEntry {
  ref: DriveRef;
  name: string;
  /** "drive", or the capture's kind: "stall", "dropout", "capture", ... */
  kind: string;
  driveId: number;
  status: string;
  started: string;
  startedUnix: number;
  duration: number;
  rows: number;
  /** estimate in bytes (0 for a capture: it holds no data of its own) */
  size: number;
  ecuId: string;
  mapVersion: string;
  notes: string;
  tags: string[];
  keep: boolean;
  markers: string[];
  /** a capture's message ("rpm dropped to 0"); a drive: its end reason */
  trigger: string;
  endReason: string;
  stalls: number;
  captures: number;
  thumbLo: number[];
  thumbHi: number[];
}

const MARKS = "SELECT drive_id, t, text, kind FROM events WHERE kind IN ('marker', 'trigger') " +
  "UNION ALL SELECT drive_id, t, text, 'alarm' FROM alarms ORDER BY 1, 2";

function tagsOf(text: unknown): string[] {
  try {
    const tags = pyParse(String(text ?? '[]'));
    return Array.isArray(tags) ? tags.map(String) : String(tags ?? '').split(/\s+/).filter(Boolean);
  } catch {
    return String(text).split(/\s+/).filter(Boolean);
  }
}

const round3 = (x: number) => Math.round(x * 1000) / 1000;

/** Every drive and capture, newest first. */
export function listEntries(conn: Conn): DriveEntry[] {
  const marks = new Map<number, [number, string][]>();
  for (const [id, t, text, kind] of conn.all(MARKS)) {
    const list = marks.get(Number(id)) ?? [];
    list.push([Number(t), String(text || kind)]);
    marks.set(Number(id), list);
  }
  const entries: DriveEntry[] = [];
  const byId = new Map<number, DriveEntry>();
  for (const r of conn.objects('SELECT id, name, status, started_iso, started_at, duration_s, rows, size_bytes, ecu_id, ' +
    'map_version, notes, tags, keep, stall_count, capture_count, thumb, end_reason FROM drives')) {
    const id = Number(r.id);
    const entry: DriveEntry = {
      ref: { drive: String(r.name), capture: '' }, name: String(r.name), kind: 'drive', driveId: id, status: String(r.status),
      started: String(r.started_iso ?? ''), startedUnix: Number(r.started_at ?? 0), duration: round3(Number(r.duration_s ?? 0)),
      rows: Number(r.rows), size: Number(r.size_bytes), ecuId: String(r.ecu_id ?? ''), mapVersion: String(r.map_version ?? ''),
      notes: String(r.notes ?? ''), tags: tagsOf(r.tags), keep: !!r.keep, markers: (marks.get(id) ?? []).map(([, t]) => t),
      trigger: '', endReason: String(r.end_reason ?? ''), stalls: Number(r.stall_count), captures: Number(r.capture_count),
      thumbLo: [], thumbHi: [],
    };
    if (r.thumb) {
      const doc = pyParse(String(r.thumb)) ?? {};
      entry.thumbLo = doc.lo ?? [];
      entry.thumbHi = doc.hi ?? [];
    }
    entries.push(entry);
    byId.set(id, entry);
  }
  for (const [id, name, kind, message, t0, t1, wall, wallUnix, rows, reason] of conn.all(
    'SELECT drive_id, name, kind, message, t_start, t_end, wall_iso, wall_unix, rows, reason FROM captures')) {
    const owner = byId.get(Number(id));
    if (!owner) continue;
    entries.push({
      ...owner, ref: { drive: owner.name, capture: String(name) }, name: String(name), kind: String(kind),
      started: String(wall ?? ''), startedUnix: Number(wallUnix ?? 0), duration: round3(Number(t1) - Number(t0)), rows: Number(rows),
      size: 0, tags: [...owner.tags], markers: (marks.get(Number(id)) ?? []).filter(([t]) => Number(t0) <= t && t <= Number(t1)).map(([, x]) => x),
      trigger: String(message ?? ''), endReason: String(reason ?? ''), stalls: 0, captures: 0, thumbLo: [], thumbHi: [],
    });
  }
  entries.sort((a, b) => {
    if (a.startedUnix !== b.startedUnix) return b.startedUnix - a.startedUnix;
    const ak = a.kind !== 'drive' ? 1 : 0, bk = b.kind !== 'drive' ? 1 : 0;
    if (ak !== bk) return bk - ak;
    return a.name < b.name ? 1 : a.name > b.name ? -1 : 0;
  });
  return entries;
}

/** Every word of ``query`` appears in the name, notes, tags or markers. */
export function matches(e: DriveEntry, query: string): boolean {
  const hay = [e.name, e.kind, e.status, e.notes, e.trigger, e.ecuId, e.mapVersion, ...e.tags, ...e.markers].join(' ').toLowerCase();
  return query.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w));
}

export function search(entries: DriveEntry[], query = '', kinds: string[] = [], tag = ''): DriveEntry[] {
  return entries.filter((e) => (!kinds.length || kinds.includes(e.kind)) && (!tag || e.tags.includes(tag)) && (!query || matches(e, query)));
}

// ---- retention ---------------------------------------------------------------

/** True if the drive's own row says to keep it. */
export function isProtected(e: DriveEntry): boolean {
  return !!(e.keep || e.notes || e.tags.length);
}

export interface RetentionPolicy {
  maxAgeDays?: number | null;
  maxTotalMb?: number | null;
  maxDrives?: number | null;
  /** never delete a drive that has a capture (a stall) in it (default true) */
  keepCaptures?: boolean;
  /** always keep this many of the newest drives (default 1) */
  keepNewest?: number;
  fullVacuum?: boolean;
}

export function policyActive(p: RetentionPolicy): boolean {
  return [p.maxAgeDays, p.maxTotalMb, p.maxDrives].some((v) => v !== null && v !== undefined);
}

/** Drives this policy would delete, oldest first. */
export function retentionPlan(hdb: HondalogDb, p: RetentionPolicy, now?: number): DriveRef[] {
  if (!policyActive(p)) return [];
  const t = now ?? hdb.now();
  const conn = hdb.conn;
  const drives = listEntries(conn).filter((e) => e.kind === 'drive');
  const [pages, free, pageSize] = ['page_count', 'freelist_count', 'page_size'].map((n) => Number(conn.get(`PRAGMA ${n}`)![0]));
  drives.sort((a, b) => a.startedUnix - b.startedUnix || a.driveId - b.driveId);
  let candidates = drives.filter((e) => e.status !== 'recording' && !hdb.active.has(e.driveId));
  const keepNewest = p.keepNewest ?? 1;
  if (keepNewest > 0) {
    const spared = new Set(drives.slice(-keepNewest).map((e) => e.name));
    candidates = candidates.filter((e) => !spared.has(e.name));
  }
  const keepCaptures = p.keepCaptures ?? true;
  candidates = candidates.filter((e) => !isProtected(e) && !(keepCaptures && e.captures));
  const doomed: DriveEntry[] = [];
  if (p.maxAgeDays !== null && p.maxAgeDays !== undefined) {
    const limit = t - p.maxAgeDays * 86400;
    doomed.push(...candidates.filter((e) => e.startedUnix < limit));
  }
  const remaining = candidates.filter((e) => !doomed.includes(e));
  if (p.maxDrives !== null && p.maxDrives !== undefined) {
    let left = drives.length - doomed.length;
    while (remaining.length && left > p.maxDrives) {
      doomed.push(remaining.shift()!);
      left--;
    }
  }
  if (p.maxTotalMb !== null && p.maxTotalMb !== undefined) {
    const used = (pages! - free!) * pageSize!;
    const scale = used / (drives.reduce((s, e) => s + e.size, 0) || 1);
    let total = used - scale * doomed.reduce((s, e) => s + e.size, 0);
    while (remaining.length && total > p.maxTotalMb * 1e6) {
      const victim = remaining.shift()!;
      doomed.push(victim);
      total -= scale * victim.size;
    }
  }
  doomed.sort((a, b) => a.startedUnix - b.startedUnix || a.driveId - b.driveId);
  return doomed.map((e) => e.ref);
}

/** Delete what the plan lists. Returns the drives removed. */
export function applyRetention(hdb: HondalogDb, p: RetentionPolicy, now?: number): DriveRef[] {
  const doomed = retentionPlan(hdb, p, now);
  if (!doomed.length) return [];
  const gone = new Set(deleteDrives(hdb.conn, doomed.map((r) => r.drive)));
  vacuum(hdb.conn, !!p.fullVacuum);
  hdb.dirty = true;
  return doomed.filter((r) => gone.has(r.drive));
}

// ---- deleting one drive or capture by hand ------------------------------------

export class KeepProtected extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'KeepProtected';
  }
}

/** The library entry for a drive or capture name (or a drive id, or "last"). */
export function lookup(conn: Conn, target: string): DriveEntry {
  const entries = listEntries(conn);
  let name = target.trim();
  if (name === '' || name === 'last' || name === 'latest') {
    const row = conn.get('SELECT name FROM drives ORDER BY coalesce(started_at, 0) DESC, id DESC LIMIT 1');
    if (row) name = String(row[0]);
  } else if (/^\d+$/.test(name) && !entries.some((e) => e.name === name)) {
    const row = conn.get('SELECT name FROM drives WHERE id=?', [Number(name)]);
    if (row) name = String(row[0]);
  }
  const found = entries.find((e) => e.name === name);
  if (!found) throw new Error(`no drive or capture ${JSON.stringify(target)}`);
  return found;
}

/** One line that says what would go, for a confirmation prompt. */
export function describe(e: DriveEntry): string {
  const when = e.started.slice(0, 19).replace('T', ' ');
  if (e.kind !== 'drive') {
    return `${e.kind} capture ${e.name} (${when}, ${e.duration.toFixed(0)} s of ${e.ref.drive}` +
      (e.trigger ? `: ${e.trigger}` : '') + '). The drive and its data stay.';
  }
  const length = e.duration < 120 ? `${e.duration.toFixed(0)} s` : `${(e.duration / 60).toFixed(0)} min`;
  const parts = [`${e.rows} rows`, `about ${(e.size / 1e6).toFixed(1)} MB`];
  if (e.captures) parts.push(`${e.captures} capture${e.captures !== 1 ? 's' : ''} (${e.stalls} stall${e.stalls !== 1 ? 's' : ''})`);
  if (e.notes) parts.push(`notes: ${e.notes.slice(0, 60)}`);
  if (e.tags.length) parts.push('tags: ' + e.tags.join(' '));
  return `drive ${e.name} (${when}, ${length}; ${parts.join(', ')})`;
}

/**
 * Delete one drive (with everything recorded in it, its captures included)
 * or one capture (only its entry). Throws KeepProtected for a drive with
 * ``keep`` unless ``allowKeep``; an Error for a drive being recorded.
 */
export function deleteEntry(hdb: HondalogDb, target: string, opts: { allowKeep?: boolean; fullVacuum?: boolean } = {}): DriveEntry {
  const conn = hdb.conn;
  const entry = lookup(conn, target);
  if (entry.kind !== 'drive') {
    if (!deleteCaptures(conn, [entry.ref.capture]).length) throw new Error(`no capture ${JSON.stringify(entry.ref.capture)}`);
    hdb.dirty = true;
    return entry;
  }
  const row = conn.get('SELECT status, keep, id FROM drives WHERE name=?', [entry.ref.drive]);
  if (!row) throw new Error(`no drive ${JSON.stringify(entry.ref.drive)}`);
  if (row[0] === 'recording' || hdb.active.has(Number(row[2]))) {
    throw new Error(`${entry.ref.drive} is being recorded: stop the recording first`);
  }
  if (row[1] && !opts.allowKeep) throw new KeepProtected(`${entry.ref.drive} is marked keep`);
  deleteDrives(conn, [entry.ref.drive]);
  vacuum(conn, !!opts.fullVacuum);
  hdb.dirty = true;
  return entry;
}
