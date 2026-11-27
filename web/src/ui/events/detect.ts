/**
 * The events of a drive, as F3 Events lists them and F4 Replay ticks them:
 * engine died, near-stalls, link dropouts, key-offs, VTEC, harsh driving and
 * marks. Found by agent #7's src/core/analysis/events.ts (findEvents); this
 * file puts them in the screens' terms (title, one-line text, a "why"
 * paragraph from the signals around the moment) and adds hard cornering from
 * the GPS lateral g, which the core finder does not look for.
 */
import type { LoadedDrive } from '../../core/types';
import { findEvents, type AnalysisEvent } from '../../core/analysis/events';
import { lateralAccel } from '../../core/analysis/dynamics';
import { valueAt } from '../../core/analysis/cursors';
import { searchSorted } from '../../core/analysis/series';
import { num } from '../replay/channels';

export type EventKind = 'stall' | 'near_stall' | 'dropout' | 'key_off' | 'vtec' | 'harsh' | 'mark';

export interface DriveEvent {
  id: string;
  drive: string;
  kind: EventKind;
  /** drive time (s since connect) of the moment itself (the rpm minimum, the start of the silence) */
  t: number;
  unix: number;
  t0: number;
  t1: number;
  title: string;
  /** one line for the list */
  text: string;
  /** the main number: min rpm, silence s, peak g, rpm at VTEC */
  value: number;
  /** a few sentences: what led up to it */
  why: string;
  severity: 'info' | 'warn' | 'alarm';
}

export const KIND_LABEL: Record<EventKind, string> = {
  stall: 'ENGINE DIED', near_stall: 'NEAR-STALL', dropout: 'DROPOUT', key_off: 'KEY OFF',
  vtec: 'VTEC', harsh: 'HARSH', mark: 'MARK',
};
export const KIND_FILTER: Record<EventKind, string> = {
  stall: 'Engine died', near_stall: 'Near-stall', dropout: 'Link dropout', key_off: 'Key off',
  vtec: 'VTEC', harsh: 'Harsh', mark: 'Marks',
};
export const KIND_ORDER: EventKind[] = ['stall', 'near_stall', 'dropout', 'key_off', 'vtec', 'harsh', 'mark'];

/** |lateral g| that counts as hard cornering */
export const HARSH_LAT_G = 0.45;

const s1 = (x: number) => (Number.isFinite(x) ? x.toFixed(1) : '?');

type S = { t: Float64Array; v: Float64Array };
function maxIn(s: S | undefined, a: number, b: number): number {
  let best = -Infinity;
  if (!s) return best;
  for (let i = searchSorted(s.t, a); i < s.t.length && s.t[i]! <= b; i++) best = Math.max(best, s.v[i]!);
  return best;
}
function firstAbove(s: S | undefined, a: number, b: number, level: number): number | null {
  if (!s) return null;
  for (let i = searchSorted(s.t, a); i < s.t.length && s.t[i]! <= b; i++) if (s.v[i]! > level) return s.t[i]!;
  return null;
}

/** What led to a near-stall, in the words of drive-idle-control.md. */
function nearStallWhy(drive: LoadedDrive, t: number, minRpm: number, fall: number, target: number): string {
  const s = drive.series;
  const v = (ch: string, tt: number) => (s[ch] ? valueAt(s[ch]!, tt) : NaN);
  const blipTo = maxIn(s.tps, t - 3, t - 0.5);
  const blip = blipTo >= 5 && v('tps', t - 0.25) < 2;
  const anyTps = firstAbove(s.tps, t - 3, t + 3, 2);
  const back = firstAbove(s.tps, t, t + 3, 2);
  const iacA = v('iac_cmd', t - 2), iacB = v('iac_cmd', t);
  return [
    blip ? `Throttle blipped to ${num(blipTo)} %, then lifted as the clutch bit.` : anyTps === null ? 'No throttle at all.' : '',
    `RPM fell ~${num(Math.abs(fall))} rpm/s to ${num(minRpm)}${Number.isFinite(target) ? ` (${num(target - minRpm)} under the ${num(target)} target)` : ''}.`,
    Number.isFinite(iacA) && Number.isFinite(iacB) ? `IAC ${iacB - iacA >= 0 ? '+' : '−'}${num(Math.abs(iacB - iacA))} in the 2 s before.` : '',
    back === null ? '' : back - t < 0.1 ? 'Throttle back at the minimum.' : `Throttle back ${s1(back - t)} s later.`,
    v('vss', t + 0.5) >= 2 && anyTps === null ? 'The car started rolling, which saved it.' : '',
    `Steering load ${v('psp', t) >= 0.5 ? 'on' : 'off'}, A/C ${v('ac_sw', t) >= 0.5 || v('ac_clutch', t) >= 0.5 ? 'on' : 'off'}${Number.isFinite(v('ect', t)) ? `, coolant ${num(v('ect', t))} °C` : ''}.`,
  ].filter(Boolean).join(' ');
}

function fromCore(drive: LoadedDrive, e: AnalysisEvent): Omit<DriveEvent, 'id' | 'drive' | 'unix'> {
  const d = e.data;
  const s = drive.series;
  const v = (ch: string, tt: number) => (s[ch] ? valueAt(s[ch]!, tt) : NaN);
  const base = { t: e.t, t0: e.t, t1: e.t1, severity: e.severity, why: e.verdict.charAt(0).toUpperCase() + e.verdict.slice(1) + '.' };
  switch (e.kind) {
    case 'near_stall': {
      const t = d.tMin ?? e.t;
      const min = d.minRpm ?? v('rpm', t);
      return { ...base, kind: 'near_stall', t, value: min, title: `Near-stall · ${num(min)} rpm`, text: `${num(min)} rpm · ${e.verdict}`,
        why: nearStallWhy(drive, t, min, d.fallRpmS ?? NaN, d.target ?? v('idle_target', t)) };
    }
    case 'stall':
      return { ...base, kind: 'stall', value: 0, title: 'Engine died', text: e.verdict,
        why: `${base.why} The ECU kept answering with rpm 0: the engine stalled, the key was still on. Throttle ${num(v('tps', e.t))} %, MAP ${num(v('map', e.t))} kPa, coolant ${num(v('ect', e.t))} °C.` };
    case 'dropout': {
      const gap = d.silenceS ?? e.t1 - e.t;
      const va = d.vssBefore ?? v('vss', e.t), vb = d.vssAfter ?? v('vss', e.t1);
      const moving = va > 2 || vb > 2;
      const ran = /engine running/.test(e.label + e.verdict);
      return { ...base, kind: 'dropout', value: gap, title: `Dropout · ${s1(gap)} s silent`,
        text: `${s1(gap)} s silent · ${moving ? `moving ${num(va)}→${num(vb)} km/h` : 'standing'}${ran ? ', engine ran' : ''}`,
        why: `${base.why}${moving ? ' The car was moving: a jolted plug (K-line pin 7, ground 4/5) fits best.' : ''}` };
    }
    case 'keyoff': {
      const r = d.rpmBefore ?? NaN;
      const batt = v('batt', e.t);
      return { ...base, kind: 'key_off', value: r, title: 'Key off',
        text: r > 400 ? `steady ${num(r)} rpm${Number.isFinite(batt) ? `, ${s1(batt)} V` : ''}` : e.verdict };
    }
    case 'vtec':
      return { ...base, kind: 'vtec', value: d.rpm ?? NaN, title: `VTEC · ${num(Math.round((d.rpm ?? 0) / 100) * 100)} rpm`,
        text: `on ${s1(d.durationS ?? e.t1 - e.t)} s at ~${num(Math.round((d.rpm ?? 0) / 100) * 100)} rpm${v('tps', e.t) > 80 ? ', WOT' : ''}` };
    case 'harsh_accel':
    case 'harsh_brake': {
      const g = d.peakG ?? NaN;
      return { ...base, kind: 'harsh', value: g, title: `${e.kind === 'harsh_brake' ? 'Harsh brake' : 'Hard launch'} · ${num(Math.abs(g), 2)} g`, text: e.verdict };
    }
    case 'mark':
      return { ...base, kind: 'mark', value: NaN, title: e.label || 'Mark', text: e.verdict, why: '' };
  }
}

/** Hard cornering: |lateral g| from the GPS heading rate past HARSH_LAT_G. */
function corners(drive: LoadedDrive): Omit<DriveEvent, 'id' | 'drive' | 'unix'>[] {
  if (drive.gps.t.length < 3) return [];
  let lat;
  try { lat = lateralAccel(drive); } catch { return []; }
  const out: Omit<DriveEvent, 'id' | 'drive' | 'unix'>[] = [];
  let i = 0;
  while (i < lat.t.length) {
    if (!(Math.abs(lat.g[i]!) >= HARSH_LAT_G)) { i++; continue; }
    const sign = Math.sign(lat.g[i]!);
    let j = i, peak = i;
    while (j + 1 < lat.t.length && sign * lat.g[j + 1]! >= HARSH_LAT_G && lat.t[j + 1]! - lat.t[j]! < 3) {
      j++;
      if (Math.abs(lat.g[j]!) > Math.abs(lat.g[peak]!)) peak = j;
    }
    const g = lat.g[peak]!, t = lat.t[peak]!;
    const kmh = drive.series.vss ? valueAt(drive.series.vss, t) : NaN;
    const side = g > 0 ? 'right' : 'left';
    out.push({
      kind: 'harsh', t, t0: lat.t[i]!, t1: lat.t[j]!, value: g, severity: 'warn',
      title: `Hard ${side} · ${num(Math.abs(g), 2)} g`, text: `cornering ${num(Math.abs(g), 2)} g to the ${side} at ${num(kmh)} km/h`,
      why: `Lateral acceleration from the GPS heading rate peaked at ${num(Math.abs(g), 2)} g to the ${side} at ${num(kmh)} km/h. GPS-derived: a rough figure.`,
    });
    i = j + 1;
  }
  return out;
}

/** Every event of one drive, newest first. */
export function eventsOf(drive: LoadedDrive): DriveEvent[] {
  const name = drive.info.name;
  let core: AnalysisEvent[] = [];
  try { core = findEvents(drive); } catch (err) { console.error('findEvents failed', err); }
  const all = [...core.map((e) => fromCore(drive, e)), ...corners(drive)];
  return all
    .map((e) => ({ ...e, id: `${name}@${e.kind}@${e.t.toFixed(2)}`, drive: name, unix: drive.info.startedUnix + e.t }))
    .sort((a, b) => b.unix - a.unix);
}

/** The automatic verdict, before the owner changes it. `all` is every event
 *  in the list (for "closest call of the night"). */
export function autoVerdict(e: DriveEvent, all: DriveEvent[]): string {
  switch (e.kind) {
    case 'near_stall': {
      const lowest = Math.min(...all.filter((x) => x.kind === 'near_stall').map((x) => x.value));
      if (e.value === lowest && all.filter((x) => x.kind === 'near_stall').length > 1) return 'closest call of the night';
      return e.value < 500 ? 'close call' : 'dip, recovered';
    }
    case 'stall': return 'engine died';
    case 'dropout': return e.text.includes('engine ran') ? 'link only, engine ran' : 'link lost, check the engine';
    case 'key_off': return 'normal key-off';
    case 'vtec': return 'normal';
    case 'harsh': return 'driving';
    case 'mark': return 'marked by you';
  }
}

/** What "Change verdict" offers. */
export const VERDICTS = ['real problem', 'driver', 'link only', 'false alarm', 'not sure'];
