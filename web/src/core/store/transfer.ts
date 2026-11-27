/**
 * Moving drives between databases and files: save any ``Log`` as a new drive
 * (``write_log``), copy a window of a drive (``extract_window``), and merge
 * another hondalog.db into this one (``importDb``).
 */

import type { Conn } from './conn';
import { HondalogDb, uniqueName } from './db';
import { type StreamData, concatStreamData, sortByTime } from './model';
import { Log } from './reader';
import { makeDriveView } from './schema';
import { type DriveRef, newDrive } from './writer';

/**
 * Save a log (imported, cut, re-decoded) as a new drive. ``status``
 * "imported" (default) or "complete". Returns the new drive.
 */
export function writeLog(hdb: HondalogDb, log: Log, meta: Record<string, unknown> = {}, opts: { name?: string; status?: string } = {}): DriveRef {
  const header = { ...log.meta, ...meta };
  return newDrive(hdb, header, (drive) => {
    for (const s of log.streams.values()) {
      const info = s.info;
      drive.addStream(info.name, info.columns, { source: info.source, rawLen: info.rawLen, attrs: { ...info.attrs } });
      drive.appendData(info.name, s);
    }
    for (const e of log.events) drive.event(e.t, e.kind, e.text, e.data);
  }, { status: opts.status ?? 'imported', name: opts.name });
}

/** Rows and events between t0 and t1 from one or more logs on the same timebase, as one in-memory log. */
export function mergeWindow(sources: Log[], t0: number, t1: number): Log {
  const streams = new Map<string, StreamData>();
  const events = [];
  const meta: Record<string, unknown> = {};
  for (const source of sources) {
    const part = source.window(t0, t1);
    Object.assign(meta, part.meta);
    events.push(...part.events);
    for (const [name, s] of part.streams) {
      const old = streams.get(name);
      if (!old || !old.info.sameLayout(s.info)) streams.set(name, s);
      else streams.set(name, sortByTime(concatStreamData([old, s])));
    }
  }
  return new Log(meta, [...streams.values()], events);
}

/** A new database file holding [t0, t1] of the given logs as one drive (``export ... cut.db``). */
export async function extractToNewDb(sources: Log[], t0: number, t1: number, meta: Record<string, unknown> = {}): Promise<{ bytes: Uint8Array; rows: number }> {
  const cut = mergeWindow(sources, t0, t1);
  const out = await HondalogDb.open(null);
  try {
    writeLog(out, cut, meta, { status: 'imported' });
    return { bytes: out.export(), rows: cut.rows };
  } finally {
    out.close();
  }
}

function tableColumns(conn: Conn, table: string): string[] {
  return conn.all(`PRAGMA table_info(${table})`).map((r) => String(r[1]));
}

function copyRows(src: Conn, dst: Conn, table: string, where: string, args: unknown[], set: Record<string, unknown>, skip: string[] = []): void {
  const dstCols = new Set(tableColumns(dst, table));
  const cols = tableColumns(src, table).filter((c) => dstCols.has(c) && !skip.includes(c));
  const rows = src.all(`SELECT ${cols.join(', ')} FROM ${table} WHERE ${where}`, args);
  const names = [...cols, ...Object.keys(set).filter((k) => !cols.includes(k))];
  const fixed = Object.keys(set);
  const sql = `INSERT INTO ${table}(${names.join(', ')}) VALUES(${names.map(() => '?').join(',')})`;
  dst.many(sql, rows.map((r) => {
    const values: unknown[] = cols.map((c, i) => (fixed.includes(c) ? set[c] : r[i]));
    for (const k of fixed) if (!cols.includes(k)) values.push(set[k]);
    return values;
  }));
}

/**
 * Copy every drive of another hondalog database into this one (with its
 * streams, rows, black box chunks, events, alarms, captures and map). A drive
 * that is already here (same name and start time) is skipped; a name that is
 * taken gets ``_2``. Returns the number of drives copied.
 */
export async function importDatabase(hdb: HondalogDb, bytes: Uint8Array): Promise<number> {
  const other = await HondalogDb.open(bytes, { fileName: 'imported file' });
  const src = other.conn;
  const dst = hdb.conn;
  let copied = 0;
  try {
    const drives = src.objects('SELECT id, name, started_at, map_id FROM drives ORDER BY id');
    for (const d of drives) {
      const same = dst.get('SELECT 1 FROM drives WHERE name=? AND coalesce(started_at, 0) = coalesce(?, 0)', [d.name, d.started_at]);
      if (same) continue;
      dst.tx(() => {
        let mapId: number | null = null;
        if (d.map_id !== null) {
          const m = src.objects('SELECT sha256, version, tag, hash, file, text FROM address_maps WHERE id=?', [d.map_id])[0];
          if (m) {
            dst.run('INSERT OR IGNORE INTO address_maps(sha256, version, tag, hash, file, text) VALUES(?,?,?,?,?,?)',
              [m.sha256, m.version, m.tag, m.hash, m.file, m.text]);
            mapId = Number(dst.get('SELECT id FROM address_maps WHERE sha256=?', [m.sha256])![0]);
          }
        }
        const name = uniqueName(dst, 'drives', String(d.name));
        copyRows(src, dst, 'drives', 'id=?', [d.id], { name, map_id: mapId }, ['id']);
        const id = dst.lastId();
        const oldId = Number(d.id);
        copyRows(src, dst, 'streams', 'drive_id=?', [oldId], { drive_id: id });
        copyRows(src, dst, 'channels', 'drive_id=?', [oldId], { drive_id: id }, ['id']);
        copyRows(src, dst, 'samples', 'drive_id=?', [oldId], { drive_id: id });
        copyRows(src, dst, 'chunks', 'drive_id=?', [oldId], { drive_id: id }, ['id']);
        copyRows(src, dst, 'chunk_stats', 'drive_id=?', [oldId], { drive_id: id });
        copyRows(src, dst, 'events', 'drive_id=?', [oldId], { drive_id: id }, ['id']);
        copyRows(src, dst, 'alarms', 'drive_id=?', [oldId], { drive_id: id }, ['id']);
        for (const c of src.objects('SELECT id, name FROM captures WHERE drive_id=?', [oldId])) {
          copyRows(src, dst, 'captures', 'id=?', [c.id], { drive_id: id, name: uniqueName(dst, 'captures', String(c.name)) }, ['id']);
        }
        makeDriveView(dst, id);
      });
      copied++;
    }
  } finally {
    other.close();
  }
  if (copied) hdb.dirty = true;
  return copied;
}
