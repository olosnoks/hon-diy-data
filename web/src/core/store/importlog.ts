/**
 * Old log files become drives (port of hondalog/data/store.py write_log and
 * the desktop drive picker's import_file): CSV / MSL (export/textlog.ts
 * parseTextLog) and MLG (export/mlg.ts readMlg) are read into a ``Log`` and
 * written as a new drive with status "imported" (transfer.ts writeLog), like
 * the Python app does.
 */

import type { HondalogDb } from './db';
import { readMlg } from './export/mlg';
import { parseTextLog } from './export/textlog';
import type { Log } from './reader';
import { writeLog } from './transfer';

/** The file kinds the import takes (the desktop picker's filter, without .bbx). */
export const IMPORT_EXTENSIONS = ['.csv', '.msl', '.mlg', '.txt', '.db', '.sqlite'];

function extension(name: string): string {
  const i = name.lastIndexOf('.');
  return i < 0 ? '' : name.slice(i).toLowerCase();
}

/** Read a CSV, MSL or MLG file into a Log. Throws for anything else or a file it cannot read. */
export function readLogFile(fileName: string, bytes: Uint8Array): Log {
  const ext = extension(fileName);
  if (ext === '.mlg') return readMlg(bytes, fileName);
  if (ext === '.csv' || ext === '.msl' || ext === '.txt' || ext === '.log') {
    return parseTextLog(new TextDecoder('utf-8', { fatal: false }).decode(bytes), fileName);
  }
  // no telling extension: MLG has a magic header, everything else is tried as text
  if (bytes.length >= 6 && String.fromCharCode(...bytes.subarray(0, 5)) === 'MLVLG') return readMlg(bytes, fileName);
  return parseTextLog(new TextDecoder('utf-8', { fatal: false }).decode(bytes), fileName);
}

/** A CSV / MSL / MLG file as a new drive; returns its name. */
export function importLogFile(hdb: HondalogDb, fileName: string, bytes: Uint8Array): string {
  const log = readLogFile(fileName, bytes);
  if (!log.rows) throw new Error(`${fileName}: no rows to import`);
  return writeLog(hdb, log, { imported_from: fileName }).drive;
}
