/**
 * Exports to other tools' formats (port of hondalog/export/__init__.py):
 *
 *     exportLog(log, 'mlg')                 // MegaLogViewer binary, v2
 *     exportLog(log, 'csv', { channels: ['rpm', 'map'], t0: 10, t1: 50 })
 *
 * Formats: ``mlg`` (MLG v2 by default), ``msl``, ``csv``, ``gpx`` (the GPS
 * track) and ``db`` (the drive, or a cut of it, as a small hondalog database
 * with every stream and raw byte). The result is bytes for the platform layer
 * to save or share; nothing is sent anywhere.
 */

import type { Log } from '../reader';
import { extractToNewDb } from '../transfer';
import { type CaptureRow } from './track';
import { exportGpx } from './gpx';
import { writeMlg } from './mlg';
import { type ExportOptions, exportColumns, exportTable, smallMeta } from './table';
import { writeCsv, writeMsl } from './textlog';

export const FORMATS: Record<string, string> = {
  mlg: 'MegaLogViewer binary (MLG v2)',
  msl: 'MegaLogViewer text',
  csv: 'CSV',
  db: 'hondalog database (SQLite)',
  gpx: 'GPX 1.1 (GPS track with times, waypoints for markers and stalls)',
};

export const MIME: Record<string, string> = {
  mlg: 'application/octet-stream', msl: 'text/plain', csv: 'text/csv', db: 'application/vnd.sqlite3', gpx: 'application/gpx+xml',
};

export interface ExportLogOptions extends ExportOptions {
  mlgVersion?: 1 | 2;
  compact?: boolean;
  /** captures of the drive (GPX waypoints); from SqliteDriveStore.captures */
  captures?: CaptureRow[] | null;
}

const enc = new TextEncoder();

/** Write ``log`` in another format. ``fmt`` without the dot ("mlg", ".csv" also works). */
export async function exportLog(log: Log, fmt: string, opts: ExportLogOptions = {}): Promise<Uint8Array> {
  const ext = fmt.replace(/^\./, '').toLowerCase();
  if (!(ext in FORMATS)) throw new Error(`unknown export format .${ext}; choose from ${Object.keys(FORMATS).map((k) => '.' + k).join(', ')}`);
  if (ext === 'gpx') {
    return enc.encode(exportGpx(log, { channel: opts.channels?.[0] ?? null, t0: opts.t0, t1: opts.t1, captures: opts.captures }));
  }
  if (ext === 'db') {
    const lo = opts.t0 ?? log.t0;
    const hi = opts.t1 ?? log.t1;
    const from = log.ref ? log.ref.capture || log.ref.drive : '';
    return (await extractToNewDb([log], lo, hi, { exported_from: from })).bytes;
  }
  const { table, markers } = exportTable(log, opts);
  const columns = exportColumns(log, table.names);
  const meta = smallMeta(log);
  if (ext === 'mlg') {
    const info = ['ecu', 'ecu_id', 'address_map_version', 'started'].filter((k) => k in meta).map((k) => `${k}=${meta[k]}`).join('; ');
    const ts = meta.started_unix;
    return writeMlg(table, markers, { version: opts.mlgVersion ?? 2, columns, info, timestamp: typeof ts === 'number' ? ts : null, compact: opts.compact });
  }
  if (ext === 'msl') {
    const st = meta.started_unix;
    return enc.encode(writeMsl(table, markers, { title: String(meta.ecu ?? ''), started: typeof st === 'number' ? st : null }));
  }
  return enc.encode(writeCsv(table, markers, meta, columns));
}

export { exportTable, writeCsv, writeMsl, writeMlg, exportGpx };
export { parseMlg, readMlg, MlgError } from './mlg';
export { parseTextLog } from './textlog';
export { writeGpx, epochOf, speedChannel } from './gpx';
export { Track, gpsColumns, GPS_CHANNELS, GPS_STREAM, logWaypoints, type Waypoint, type CaptureRow } from './track';
