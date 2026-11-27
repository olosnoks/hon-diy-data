/**
 * A person's notes and verdicts on the events of a drive, kept in the drive's
 * own database without a schema change (port of the desktop app's
 * hondalog/analysis/eventnotes.py, same rows so notes move between apps):
 * one row per event in the existing ``events`` table, ``kind = 'event_note'``,
 * the event's key in ``text`` and ``{"note", "verdict", "kind", "t",
 * "updated_unix"}`` (Python json.dumps) in ``data``.
 *
 * The reader's markers only take the kinds marker / trigger / alarm, so these
 * rows never show up as markers; deleting the drive deletes them with it.
 */

import { pyFixed, reprFloat } from '../expr/pynum';
import type { Conn } from './conn';
import type { EventNote } from '../types';
import type { HondalogDb } from './db';
import { pyJson } from './pyfmt';

/** Python json.dumps of the note's dict (ensure_ascii, floats as floats: "t": 12.0). */
export function dumpsNote(value: Record<string, unknown>): string {
  const str = (s: string) => JSON.stringify(s).replace(/[^\x00-\x7f]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
  const parts = Object.entries(value).filter(([, v]) => v !== undefined).map(([k, v]) => {
    const text = typeof v === 'number' ? (Number.isFinite(v) ? reprFloat(v) : v !== v ? 'NaN' : v > 0 ? 'Infinity' : '-Infinity')
      : typeof v === 'string' ? str(v) : pyJson(v);
    return `${str(k)}: ${text}`;
  });
  return `{${parts.join(', ')}}`;
}

export const EVENT_NOTE_KIND = 'event_note';

export type { EventNote };

/** The desktop app's DriveEvent.key: kind + time to 0.1 s ("near_stall@3210.4"). */
export function eventKey(kind: string, t: number): string {
  return `${kind}@${pyFixed(t, 1)}`;
}

function driveId(conn: Conn, drive: string): number | null {
  const row = conn.get('SELECT id FROM drives WHERE name=?', [drive]);
  return row ? Number(row[0]) : null;
}

function parse(data: unknown): Record<string, unknown> {
  try {
    const v = JSON.parse(String(data ?? '') || '{}') as unknown;
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/** Every note and verdict of one drive, by event key. {} when there are none or no such drive. */
export function loadEventNotes(conn: Conn, drive: string): Record<string, EventNote> {
  const did = driveId(conn, drive);
  if (did === null) return {};
  const out: Record<string, EventNote> = {};
  for (const [key, data] of conn.all('SELECT text, data FROM events WHERE drive_id=? AND kind=? ORDER BY id', [did, EVENT_NOTE_KIND])) {
    const v = parse(data);
    out[String(key)] = {
      note: String(v.note ?? '') || '',
      verdict: String(v.verdict ?? '') || '',
      kind: typeof v.kind === 'string' ? v.kind : '',
      t: typeof v.t === 'number' ? v.t : null,
    };
  }
  return out;
}

export interface SaveNote {
  /** undefined leaves it as it is, '' clears it */
  note?: string;
  verdict?: string;
  kind?: string;
  t?: number | null;
}

/** Set the note and/or the verdict of one event. A row with neither left is
 *  removed. Returns what is stored. Throws for an unknown drive. */
export function saveEventNote(hdb: HondalogDb, drive: string, key: string, change: SaveNote,
  now: () => number = () => Date.now() / 1000): { note: string; verdict: string } {
  const conn = hdb.conn;
  const did = driveId(conn, drive);
  if (did === null) throw new Error(`no drive ${JSON.stringify(drive)}`);
  let value: Record<string, unknown> = {};
  conn.tx(() => {
    const row = conn.get('SELECT id, data FROM events WHERE drive_id=? AND kind=? AND text=?', [did, EVENT_NOTE_KIND, key]);
    value = row ? parse(row[1]) : {};
    if (change.note !== undefined) value.note = change.note;
    if (change.verdict !== undefined) value.verdict = change.verdict;
    if (!('kind' in value)) value.kind = change.kind ?? '';
    if (change.t !== undefined && change.t !== null) value.t = Number(change.t);
    value.updated_unix = now();
    const empty = !value.note && !value.verdict;
    if (row) {
      if (empty) conn.run('DELETE FROM events WHERE id=?', [row[0]]);
      else conn.run('UPDATE events SET data=? WHERE id=?', [dumpsNote(value), row[0]]);
    } else if (!empty) {
      conn.run('INSERT INTO events(drive_id, t, kind, text, data) VALUES (?, ?, ?, ?, ?)',
        [did, Number(change.t ?? 0) || 0, EVENT_NOTE_KIND, key, dumpsNote(value)]);
    }
  });
  hdb.dirty = true;
  return { note: String(value.note ?? ''), verdict: String(value.verdict ?? '') };
}
