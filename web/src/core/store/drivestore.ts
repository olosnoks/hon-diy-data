/**
 * ``DriveStore`` (src/core/types.ts) on one hondalog.db held by sql.js and
 * saved through a ``FileStore``. The recorder writes into the same database
 * (``new Recorder(driveStore.hdb, map)``), so a drive being recorded is listed
 * and can be opened (what has been committed so far).
 */

import type { AddressMap, DriveInfo, DriveStore, EcuRead, LoadedDrive } from '../types';
import { DB_NAME, HondalogDb } from './db';
import type { CaptureRow } from './export/track';
import { Track } from './export/track';
import type { FileStore } from './filestore';
import { type DriveEntry, deleteEntry, listEntries } from './library';
import { type Log, readDrive } from './reader';
import { importDatabase } from './transfer';
import { Store } from './writer';

export interface DriveStoreOptions {
  fileName?: string;
  /** Parse a drive's stored address map text (agent #2's parser); default: no map. */
  parseMap?: (text: string) => AddressMap | null;
}

const CAPTURE_KINDS = new Set(['stall', 'dropout', 'alarm', 'capture']);

/** A library entry as the contract's DriveInfo. */
export function driveInfo(e: DriveEntry, mapVersion = e.mapVersion): DriveInfo {
  const kind = (e.kind === 'drive' ? 'drive' : CAPTURE_KINDS.has(e.kind) ? e.kind : 'capture') as DriveInfo['kind'];
  const status: DriveInfo['status'] = e.status === 'recording' ? 'recording' : e.status === 'crashed' ? 'crashed' : 'ok';
  const reason = e.kind === 'drive' ? e.endReason : e.trigger || e.endReason;
  return {
    id: e.driveId, name: e.name, kind, startedUnix: e.startedUnix, durationS: e.duration, rows: e.rows, status,
    mapVersion, ecuId: e.ecuId, ...(reason ? { reason } : {}), ...(e.kind !== 'drive' ? { captureKind: e.kind } : {}),
  };
}

export class SqliteDriveStore implements DriveStore {
  constructor(readonly hdb: HondalogDb, readonly opts: DriveStoreOptions = {}) {}

  /** Open (or make) hondalog.db in a file store. */
  static async open(fileStore: FileStore, opts: DriveStoreOptions = {}): Promise<SqliteDriveStore> {
    const hdb = await HondalogDb.openFile(fileStore, opts.fileName ?? DB_NAME);
    return new SqliteDriveStore(hdb, opts);
  }

  /** Every drive and capture, newest first, with notes, tags, markers and thumbnails. */
  entries(): DriveEntry[] {
    return listEntries(this.hdb.conn);
  }

  async listDrives(): Promise<DriveInfo[]> {
    return this.entries().map((e) => driveInfo(e));
  }

  /** A drive or capture as a ``Log`` (what replay, analysis and the exports use). */
  readLog(name: string | number = ''): Log {
    return readDrive(this.hdb.conn, name);
  }

  /** Captures of the drive a log belongs to (for GPX waypoints and the map). */
  captures(log: Log): CaptureRow[] {
    if (log.driveId === null) return [];
    return this.hdb.conn.all('SELECT name, kind, reason, message, t_trigger FROM captures WHERE drive_id=? ORDER BY t_trigger', [log.driveId])
      .map(([name, kind, reason, message, t]) => ({ name: String(name), kind: String(kind), reason: String(reason ?? ''), message: String(message ?? ''), tTrigger: Number(t) }));
  }

  async loadDrive(name: string): Promise<LoadedDrive> {
    const log = this.readLog(name);
    const entry = this.entries().find((e) => e.name === log.name);
    const info = entry ? driveInfo(entry) : {
      id: log.driveId ?? 0, name: log.name, kind: 'drive' as const, startedUnix: Number(log.meta.started_unix ?? 0),
      durationS: log.duration, rows: log.rows, status: 'ok' as const, mapVersion: String(log.meta.address_map_version ?? ''), ecuId: String(log.meta.ecu_id ?? ''),
    };
    if (log.ref?.capture) {
      info.rows = log.rows;
      info.durationS = log.duration;
    }
    return toLoaded(log, info, this.opts.parseMap);
  }

  async deleteDrive(name: string, opts: { allowKeep?: boolean } = {}): Promise<void> {
    deleteEntry(this.hdb, name, opts);
    await this.hdb.persist();
  }

  async importDb(file: Uint8Array): Promise<number> {
    const n = await importDatabase(this.hdb, file);
    if (n) await this.hdb.persist();
    return n;
  }

  async exportDb(): Promise<Uint8Array> {
    return this.hdb.export();
  }

  /** Write the file now. */
  persist(): Promise<void> {
    return this.hdb.persist();
  }

  async close(): Promise<void> {
    for (const w of [...this.hdb.writers]) if (w instanceof Store) await w.close().catch(() => undefined);
    await this.hdb.persist();
    await this.hdb.settled();
    this.hdb.close();
  }
}

/** A log as the contract's LoadedDrive. */
export function toLoaded(log: Log, info: DriveInfo, parseMap?: (text: string) => AddressMap | null): LoadedDrive {
  const series: LoadedDrive['series'] = {};
  for (const name of log.channels) {
    const s = log.series(name);
    series[name] = { t: s.t, v: s.v };
  }
  const reads: EcuRead[] = [];
  for (const s of log.streams.values()) {
    const addr = s.info.attrs.addr;
    const w = s.info.rawLen;
    if (typeof addr !== 'number' || !w) continue;
    for (let i = 0; i < s.t.length; i++) {
      reads.push({ t: s.t[i]!, addr, data: s.raw.slice(i * w, (i + 1) * w), latencyS: s.latency[i]! });
    }
  }
  reads.sort((a, b) => a.t - b.t);
  const track = Track.fromLog(log);
  let map: AddressMap | null = null;
  const text = log.meta.address_map_text;
  if (parseMap && typeof text === 'string' && text) {
    try {
      map = parseMap(text);
    } catch {
      map = null;
    }
  }
  return {
    info, map, series, reads,
    events: log.events.map((e) => ({ t: e.t, kind: e.kind, text: e.text })),
    gps: { t: track.t, lat: track.lat, lon: track.lon, speedKmh: track.speed, heading: track.heading },
  };
}
