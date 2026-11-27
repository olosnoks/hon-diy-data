/**
 * Longitudinal acceleration from vss, lateral g from the GPS heading rate,
 * and a g-g trail (docs/analysis/2026-11-26 vehicle-dynamics.md §5,
 * drive-timeline.md §4.4-4.5, work-dynamics/common.py `load`).
 *
 * vss: 1 km/h steps at ~11 Hz; true speed = 1.011 x vss. Resampled to 10 Hz,
 * short gaps bridged, a Savitzky-Golay window of 1.5 s (15 samples, order 2)
 * gives the smoothed speed and its derivative. Short peaks read 10-20 % low.
 *
 * GPS: ~1 Hz, fixes arrive 1.7 s late against the ECU (shift them earlier),
 * gaps of 3-13 s every ~2 min, now and then a corrupt position. Lateral
 * acceleration = v x heading rate; it is NaN across gaps, below `minKmh`
 * (heading is noise when slow) and where the turn rate is implausible.
 */
import type { LoadedDrive } from '../types';
import { align, driveSpan, fillGaps, getSeries, savgol, searchSorted, uniformGrid } from './series';

export const G = 9.81;
export const VSS_SCALE = 1.011;
export const GPS_LAG_S = 1.7;

export interface LongAccelOptions {
  hz?: number;            // default 10
  windowS?: number;       // default 1.5
  vssScale?: number;      // default 1.011
  maxGapS?: number;       // bridge vss gaps up to this (default 3 s); longer stay NaN
  vss?: string;
}

export interface LongAccel {
  t: Float64Array;
  /** true speed, m/s, smoothed */
  v: Float64Array;
  /** m/s^2 (positive = speeding up) */
  a: Float64Array;
  /** distance, m (NaN-safe: gaps add nothing) */
  s: Float64Array;
}

/** Smoothed speed and acceleration on arrays already on a uniform grid. */
export function longAccelFromArrays(t: Float64Array, vssKmh: Float64Array, opts: LongAccelOptions = {}): LongAccel {
  const hz = opts.hz ?? (t.length > 1 ? 1 / (t[1]! - t[0]!) : 10);
  const scale = opts.vssScale ?? VSS_SCALE;
  const filled = fillGaps(t, vssKmh, opts.maxGapS ?? 3);
  const vms = filled.map((x) => (x * scale) / 3.6);
  const win = Math.max(3, Math.round((opts.windowS ?? 1.5) * hz) | 1);
  // smooth each finite run on its own so a long gap does not poison neighbours
  const v = new Float64Array(t.length).fill(NaN);
  const a = new Float64Array(t.length).fill(NaN);
  let i = 0;
  while (i < t.length) {
    if (Number.isNaN(vms[i]!)) { i++; continue; }
    let j = i;
    while (j < t.length && !Number.isNaN(vms[j]!)) j++;
    const seg = vms.subarray(i, j);
    if (seg.length >= 3) {
      const w = Math.min(win, seg.length % 2 ? seg.length : seg.length - 1);
      v.set(savgol(seg, w, 2, 0, 1 / hz), i);
      a.set(savgol(seg, w, 2, 1, 1 / hz), i);
    } else { v.set(seg, i); }
    i = j;
  }
  const s = new Float64Array(t.length);
  for (let k = 1; k < t.length; k++) {
    const step = 0.5 * (v[k]! + v[k - 1]!) * (t[k]! - t[k - 1]!);
    s[k] = s[k - 1]! + (Number.isNaN(step) ? 0 : step);
  }
  return { t, v, a, s };
}

export function longitudinalAccel(drive: LoadedDrive, opts: LongAccelOptions = {}): LongAccel {
  const [t0, t1] = driveSpan(drive);
  const t = uniformGrid(t0, t1, opts.hz ?? 10);
  const vss = align(getSeries(drive, opts.vss ?? 'vss'), t);
  return longAccelFromArrays(t, vss, { ...opts, hz: opts.hz ?? 10 });
}

export interface LateralOptions {
  lagS?: number;          // GPS behind the ECU, default 1.7 s
  maxGapS?: number;       // no heading rate across a gap longer than this, default 3 s
  minKmh?: number;        // default 10
  maxTurnDegS?: number;   // implausible above this, default 60 deg/s
  /** speed source: 'vss' (true speed from the ECU, default when present) or 'gps' */
  speed?: 'vss' | 'gps';
  vssScale?: number;
}

export interface LateralAccel {
  /** GPS fix times, already shifted earlier by the lag (ECU time base) */
  t: Float64Array;
  /** heading rate, deg/s (positive = turning right / clockwise) */
  yawDegS: Float64Array;
  /** m/s^2, positive = to the right */
  a: Float64Array;
  g: Float64Array;
}

const R_EARTH = 6371008.8;

/** Bearing (deg, clockwise from north) from fix i to fix j. */
function bearing(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = (lat1 * Math.PI) / 180; const p2 = (lat2 * Math.PI) / 180;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const y = Math.sin(dl) * Math.cos(p2);
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

function distance(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = (lat1 * Math.PI) / 180; const p2 = (lat2 * Math.PI) / 180;
  const dp = p2 - p1; const dl = ((lon2 - lon1) * Math.PI) / 180;
  const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * R_EARTH * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Smallest signed difference b - a of two headings, degrees. */
export function headingDiff(a: number, b: number): number {
  return ((b - a + 540) % 360) - 180;
}

/** Lateral acceleration from the GPS heading rate times speed. */
export function lateralAccel(drive: LoadedDrive, opts: LateralOptions = {}): LateralAccel {
  const lag = opts.lagS ?? GPS_LAG_S;
  const maxGap = opts.maxGapS ?? 3;
  const minKmh = opts.minKmh ?? 10;
  const maxTurn = opts.maxTurnDegS ?? 60;
  const gps = drive.gps;
  const n = gps.t.length;
  const t = gps.t.map((x) => x - lag);
  // drop corrupt fixes: a jump implying more than 100 m/s from the previous good fix
  const good = new Uint8Array(n);
  let last = -1;
  for (let i = 0; i < n; i++) {
    const la = gps.lat[i]!; const lo = gps.lon[i]!;
    if (!Number.isFinite(la) || !Number.isFinite(lo) || (la === 0 && lo === 0)) continue;
    if (last >= 0) {
      const dt = Math.max(t[i]! - t[last]!, 1e-3);
      if (distance(gps.lat[last]!, gps.lon[last]!, la, lo) / dt > 100) continue;
    }
    good[i] = 1; last = i;
  }
  // heading: the receiver's when given, else the bearing between neighbouring fixes
  const heading = new Float64Array(n).fill(NaN);
  for (let i = 0; i < n; i++) {
    if (!good[i]) continue;
    const h = gps.heading[i]!;
    if (Number.isFinite(h)) { heading[i] = h; continue; }
    let j = i + 1; while (j < n && !good[j]) j++;
    let k = i - 1; while (k >= 0 && !good[k]) k--;
    if (j < n && k >= 0 && t[j]! - t[k]! <= 2 * maxGap) heading[i] = bearing(gps.lat[k]!, gps.lon[k]!, gps.lat[j]!, gps.lon[j]!);
    else if (j < n && t[j]! - t[i]! <= maxGap) heading[i] = bearing(gps.lat[i]!, gps.lon[i]!, gps.lat[j]!, gps.lon[j]!);
  }
  // speed at each (shifted) fix
  let speed: Float64Array;
  const vss = getSeries(drive, 'vss');
  if ((opts.speed ?? (vss.t.length ? 'vss' : 'gps')) === 'vss' && vss.t.length) {
    speed = align(vss, t, 'linear').map((x) => (x * (opts.vssScale ?? VSS_SCALE)) / 3.6);
  } else speed = gps.speedKmh.map((x) => x / 3.6);
  // central difference of the heading over neighbours within maxGap
  const yaw = new Float64Array(n).fill(NaN);
  for (let i = 0; i < n; i++) {
    if (Number.isNaN(heading[i]!) || !(speed[i]! * 3.6 >= minKmh)) continue;
    let k = i - 1; while (k >= 0 && Number.isNaN(heading[k]!)) k--;
    let j = i + 1; while (j < n && Number.isNaN(heading[j]!)) j++;
    const okK = k >= 0 && t[i]! - t[k]! <= maxGap && t[i]! > t[k]!;
    const okJ = j < n && t[j]! - t[i]! <= maxGap && t[j]! > t[i]!;
    let rate = NaN;
    if (okK && okJ) rate = headingDiff(heading[k]!, heading[j]!) / (t[j]! - t[k]!);
    else if (okJ) rate = headingDiff(heading[i]!, heading[j]!) / (t[j]! - t[i]!);
    else if (okK) rate = headingDiff(heading[k]!, heading[i]!) / (t[i]! - t[k]!);
    if (Math.abs(rate) <= maxTurn) yaw[i] = rate;
  }
  const a = yaw.map((w, i) => ((w * Math.PI) / 180) * speed[i]!);
  return { t, yawDegS: yaw, a, g: a.map((x) => x / G) };
}

export interface GgPoint { t: number; lon: number; lat: number }

/** g-g trail: longitudinal and lateral acceleration in g on the 10 Hz grid,
 *  lateral linearly interpolated between valid GPS-derived points (never
 *  across a gap). Only points where both exist; optionally limited to [t0, t1]. */
export function ggTrail(drive: LoadedDrive, t0?: number, t1?: number,
  opts: { long?: LongAccelOptions; lat?: LateralOptions; long0?: LongAccel; lat0?: LateralAccel } = {}): GgPoint[] {
  const lon = opts.long0 ?? longitudinalAccel(drive, opts.long);
  const lat = opts.lat0 ?? lateralAccel(drive, opts.lat);
  const maxGap = opts.lat?.maxGapS ?? 3;
  const out: GgPoint[] = [];
  const i0 = t0 === undefined ? 0 : searchSorted(lon.t, t0, 'left');
  const i1 = t1 === undefined ? lon.t.length : searchSorted(lon.t, t1, 'right');
  for (let i = i0; i < i1; i++) {
    const tt = lon.t[i]!; const al = lon.a[i]!;
    if (Number.isNaN(al)) continue;
    const j = searchSorted(lat.t, tt, 'right') - 1;
    if (j < 0 || j + 1 >= lat.t.length) continue;
    const ta = lat.t[j]!; const tb = lat.t[j + 1]!;
    const ya = lat.a[j]!; const yb = lat.a[j + 1]!;
    if (Number.isNaN(ya) || Number.isNaN(yb) || tb - ta > maxGap || tb <= ta) continue;
    const ay = ya + ((yb - ya) * (tt - ta)) / (tb - ta);
    out.push({ t: tt, lon: al / G, lat: ay / G });
  }
  return out;
}
