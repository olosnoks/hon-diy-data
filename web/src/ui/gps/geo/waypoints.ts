/**
 * Pins for a drive: markers, alarms, stalls, near-stalls, link dropouts and
 * other captures (port of log_waypoints in hondalog/geo/track.py; near-stalls
 * and unrecorded stalls / dropouts from core/analysis/events.ts).
 */
import type { LoadedDrive } from '../../../core/types';
import { findLinkEvents, findNearStalls, type AnalysisEvent } from '../../../core/analysis';

export type WaypointKind = 'stall' | 'nearstall' | 'dropout' | 'capture' | 'alarm' | 'marker';

export interface Waypoint {
  t: number;
  kind: WaypointKind;
  name: string;
  text: string;
}

/** What an event of the store is, as a pin. null: not a pin (connect, note...). */
export function classify(kind: string, text: string): WaypointKind | null {
  const k = kind.toLowerCase();
  const up = (text || '').toUpperCase();
  if (k === 'marker' || k === 'mark') return 'marker';
  if (k.includes('near')) return 'nearstall';
  if (k === 'dropout' || k.includes('dropout') || up.includes('DROPOUT')) return 'dropout';
  if (k === 'alarm') return up.includes('CLEARED') ? null : 'alarm';
  if (k === 'stall' || k.startsWith('stall')) return 'stall';
  if (k === 'trigger') return up.includes('STALL') ? 'stall' : 'capture';
  if (k === 'capture') return up.includes('STALL') ? 'stall' : 'capture';
  return null;
}

/** Every pin of a drive in time order: the store's events (markers, alarms,
 *  captures, stalls, dropouts) plus what core/analysis finds in the data
 *  (near-stalls, stalls and link dropouts the store did not record). One
 *  found within 2 s of a recorded one of the same family is the same one; a
 *  "near-stall" in the 3 s before a stall is the stall itself. */
export function driveWaypoints(drive: LoadedDrive | null | undefined): Waypoint[] {
  if (!drive) return [];
  const out: Waypoint[] = [];
  let marks = 0;
  for (const e of drive.events ?? []) {
    const kind = classify(e.kind, e.text);
    if (!kind) continue;
    const name = kind === 'marker' ? e.text || `MARK ${++marks}` : (e.text || kind.toUpperCase()).split(/\r?\n/)[0]!;
    out.push({ t: e.t, kind, name, text: e.text || '' });
  }
  let found: AnalysisEvent[] = [];
  try {
    found = drive.series ? [...findNearStalls(drive), ...findLinkEvents(drive)] : [];
  } catch {
    found = [];
  }
  const family = (k: WaypointKind) => (k === 'nearstall' || k === 'stall' ? 'stall' : k);
  const extra: Waypoint[] = [];
  for (const e of found) {
    const kind: WaypointKind | null = e.kind === 'near_stall' ? 'nearstall' : e.kind === 'stall' ? 'stall' : e.kind === 'dropout' ? 'dropout' : null;
    if (!kind) continue;
    const w: Waypoint = { t: e.t, kind, name: e.label, text: e.verdict };
    if ([...out, ...extra].some((o) => o.kind !== 'marker' && family(o.kind) === family(kind) && Math.abs(o.t - w.t) < 2)) continue;
    extra.push(w);
  }
  const all = [...out, ...extra];
  const stalls = all.filter((w) => w.kind === 'stall');
  return all
    .filter((w) => w.kind !== 'nearstall' || !stalls.some((s) => s.t >= w.t && s.t - w.t <= 3))
    .sort((a, b) => a.t - b.t);
}

/** "MARK 3" -> "3", other markers "M" */
export function markLabel(text: string): string {
  const m = /MARK\s+(\d+)/.exec(text || '');
  return m ? m[1]! : 'M';
}
