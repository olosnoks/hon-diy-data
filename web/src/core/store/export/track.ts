/**
 * A drive's GPS track as the exports need it (the parts of
 * hondalog/geo/track.py that GPX and LoadedDrive.gps use): only rows with a
 * fix, sorted by time, a break wherever no fix came for GAP_S; the pins of a
 * drive (markers, raised alarms, captures).
 */

import type { Column } from '../model';
import { column } from '../model';
import type { Log } from '../reader';
import { median } from '../reader';
import { searchsorted } from '../model';

export const GPS_STREAM = 'gps';
/** no fix for longer than this and the line is broken */
export const GAP_S = 5.0;
/** a fix this close to (0, 0) is a receiver that has not got one yet */
export const NULL_ISLAND_DEG = 0.01;
const MAX_LAT = 85.0511287798066;

/** The "gps" stream's columns (the gps_track SQL view reads them by position: keep the order). */
export function gpsColumns(): Column[] {
  return [
    column('gps_lat', { dtype: 'f8', units: '°', label: 'Latitude', decimals: 6, lo: -90, hi: 90 }),
    column('gps_lon', { dtype: 'f8', units: '°', label: 'Longitude', decimals: 6, lo: -180, hi: 180 }),
    column('gps_speed', { dtype: 'f4', units: 'km/h', label: 'GPS speed', decimals: 1, lo: 0, hi: 200 }),
    column('gps_heading', { dtype: 'f4', units: '°', label: 'Heading', decimals: 0, lo: 0, hi: 360 }),
    column('gps_sats', { dtype: 'u1', units: '', label: 'Satellites', decimals: 0, lo: 0, hi: 16 }),
    column('gps_fix', { dtype: 'u1', units: '', label: 'GPS fix', kind: 'flag', decimals: 0 }),
    column('gps_hdop', { dtype: 'f4', units: '', label: 'HDOP', decimals: 1, lo: 0, hi: 20 }),
    column('gps_utc', { dtype: 'f8', units: 's', label: 'GPS time (unix)', decimals: 0 }),
  ];
}

export const GPS_CHANNELS = gpsColumns().map((c) => c.name);

export interface Waypoint {
  t: number;
  kind: string;              // "marker", "alarm", "stall", "capture"
  name: string;
  text: string;
}

export class Track {
  readonly t: Float64Array;
  readonly lat: Float64Array;
  readonly lon: Float64Array;
  readonly speed: Float64Array;
  readonly heading: Float64Array;
  readonly sats: Float64Array;
  /** breaks[i]: no line from fix i-1 to fix i */
  readonly breaks: boolean[];

  constructor(t: ArrayLike<number>, lat: ArrayLike<number>, lon: ArrayLike<number>, speed?: ArrayLike<number> | null,
    heading?: ArrayLike<number> | null, sats?: ArrayLike<number> | null) {
    const n = Math.min(t.length, lat.length, lon.length);
    const col = (v: ArrayLike<number> | null | undefined) => (v && v.length >= n ? v : null);
    const sp = col(speed), hd = col(heading), st = col(sats);
    const ok: number[] = [];
    for (let i = 0; i < n; i++) {
      const a = lat[i]!, o = lon[i]!, tt = t[i]!;
      if (!Number.isFinite(tt) || !Number.isFinite(a) || !Number.isFinite(o)) continue;
      if (Math.abs(a) > MAX_LAT || Math.abs(o) > 180) continue;
      if (Math.abs(a) < NULL_ISLAND_DEG && Math.abs(o) < NULL_ISLAND_DEG) continue;
      ok.push(i);
    }
    ok.sort((a, b) => t[a]! - t[b]! || a - b);
    this.t = Float64Array.from(ok, (i) => t[i]!);
    this.lat = Float64Array.from(ok, (i) => lat[i]!);
    this.lon = Float64Array.from(ok, (i) => lon[i]!);
    this.speed = Float64Array.from(ok, (i) => (sp ? sp[i]! : NaN));
    this.heading = Float64Array.from(ok, (i) => (hd ? hd[i]! : NaN));
    this.sats = Float64Array.from(ok, (i) => (st ? st[i]! : NaN));
    this.breaks = Array.from(this.t, (x, i) => i === 0 || x - this.t[i - 1]! > GAP_S);
  }

  get length(): number {
    return this.t.length;
  }

  get empty(): boolean {
    return this.t.length === 0;
  }

  /** The track of a log (latitude's times are the track's; the rest matched to them). */
  static fromLog(log: Log): Track {
    if (!log.column('gps_lat') || !log.column('gps_lon')) return new Track([], [], []);
    const lat = log.series('gps_lat');
    const t = lat.t;
    const matched = (name: string): Float64Array | null => {
      if (!log.column(name)) return null;
      const s = log.series(name);
      if (s.t.length === t.length && (t.length === 0 || (s.t[0] === t[0] && s.t[s.t.length - 1] === t[t.length - 1]))) return s.v;
      return hold(s.t, s.v, t, 0.5);
    };
    return new Track(t, lat.v, matched('gps_lon') ?? new Float64Array(t.length).fill(NaN), matched('gps_speed'), matched('gps_heading'), matched('gps_sats'));
  }

  /** (lat, lon) at t, linear between fixes; null outside the track and inside a gap. */
  at(t: number): [number, number] | null {
    const n = this.t.length;
    if (!n || !Number.isFinite(t) || t < this.t[0]! || t > this.t[n - 1]!) return null;
    let i = searchsorted(this.t, t, 'right') - 1;
    let f: number;
    if (i >= n - 1) {
      i = n - 1;
      f = 0;
    } else if (this.breaks[i + 1]) {
      if (t - this.t[i]! > GAP_S) return null;
      f = 0;
    } else {
      const span = this.t[i + 1]! - this.t[i]!;
      f = span > 0 ? (t - this.t[i]!) / span : 0;
    }
    const j = Math.min(i + 1, n - 1);
    const lerp = (a: Float64Array) => (f ? a[i]! + (a[j]! - a[i]!) * f : a[i]!);
    return [lerp(this.lat), lerp(this.lon)];
  }

  /** A channel's value at every fix (sample and hold). */
  sample(tSrc: Float64Array, vSrc: Float64Array, maxAge?: number): Float64Array {
    return hold(tSrc, vSrc, this.t, maxAge);
  }

  cut(t0?: number | null, t1?: number | null): Track {
    if ((t0 === null || t0 === undefined) && (t1 === null || t1 === undefined)) return this;
    const lo = t0 ?? -Infinity, hi = t1 ?? Infinity;
    const keep = Array.from(this.t.keys()).filter((i) => this.t[i]! >= lo && this.t[i]! <= hi);
    const pickA = (a: Float64Array) => Float64Array.from(keep, (i) => a[i]!);
    return new Track(pickA(this.t), pickA(this.lat), pickA(this.lon), pickA(this.speed), pickA(this.heading), pickA(this.sats));
  }
}

/** Sample-and-hold of (tSrc, vSrc) at the times ``grid``. */
export function hold(tSrc: Float64Array, vSrc: Float64Array, grid: Float64Array, maxAge?: number): Float64Array {
  const out = new Float64Array(grid.length).fill(NaN);
  if (!tSrc.length || !grid.length) return out;
  let age = maxAge;
  if (age === undefined) {
    const dt = tSrc.length > 1 ? median(Array.from({ length: tSrc.length - 1 }, (_, i) => tSrc[i + 1]! - tSrc[i]!)) : 1.0;
    age = Math.max(1.0, 3.0 * dt);
  }
  for (let k = 0; k < grid.length; k++) {
    const i = searchsorted(tSrc, grid[k]!, 'right') - 1;
    if (i < 0) continue;
    if (grid[k]! - tSrc[i]! <= age) out[k] = vSrc[i]!;
  }
  return out;
}

export interface CaptureRow { name: string; kind: string; reason: string; message: string; tTrigger: number }

/** Markers, raised alarms and captures of a log, in time order. */
export function logWaypoints(log: Log, captures: CaptureRow[] | null = null): Waypoint[] {
  const out: Waypoint[] = [];
  for (const e of log.events) {
    if (e.kind === 'marker') out.push({ t: e.t, kind: 'marker', name: e.text || 'MARK', text: '' });
    else if (e.kind === 'alarm' && String(e.data?.state ?? 'raised') === 'raised') out.push({ t: e.t, kind: 'alarm', name: e.text || 'ALARM', text: '' });
  }
  if (captures && captures.length) {
    const isCapture = !!log.ref?.capture;
    const lo = isCapture ? log.t0 : -Infinity, hi = isCapture ? log.t1 : Infinity;
    for (const c of captures) {
      if (!(lo - 1e-6 <= c.tTrigger && c.tTrigger <= hi + 1e-6)) continue;
      out.push({ t: c.tTrigger, kind: c.kind === 'stall' ? 'stall' : 'capture', name: c.name, text: c.message || c.reason || c.kind });
    }
  } else {
    for (const e of log.events) {
      if (e.kind === 'trigger') {
        const kind = (e.text || '').toUpperCase().includes('STALL') ? 'stall' : 'capture';
        out.push({ t: e.t, kind, name: e.text || kind.toUpperCase(), text: '' });
      }
    }
  }
  return out.map((w, i) => [w, i] as const).sort((a, b) => a[0].t - b[0].t || a[1] - b[1]).map(([w]) => w);
}
