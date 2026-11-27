/**
 * Gear from rpm / vss with a coupling test (docs/analysis/2026-11-26
 * drive-timeline.md §1.3, vehicle-dynamics.md §7, work-dynamics/common.py).
 *
 * A sample is in gear k when vss >= minKmh and rpm / vss is within `tol` of
 * gear k's cluster centre (rpm per km/h of ECU vss), and it stays in that
 * band for every sample within ±couplingS: rpm follows vss, so the clutch is
 * engaged. Moving with the ratio matching no gear (or not holding) is
 * "decoupled": clutch in, neutral, coasting or shifting.
 */
import type { LoadedDrive } from '../types';
import { align, driveSpan, getSeries, uniformGrid } from './series';

/** Measured clusters, rpm per km/h of ECU vss, 2000 Civic EX D16Y8 manual. */
export const GEAR_RPM_PER_KMH: readonly number[] = [133.6, 76.8, 49.7, 36.0, 27.7];

export const GearState = {
  unknown: -1,   // no rpm or vss here (link dropout)
  stopped: 0,    // vss < 1 km/h
  crawl: 1,      // moving below minKmh: ratio meaningless
  inGear: 2,
  decoupled: 3,  // clutch in / neutral / coasting / shifting
} as const;
export type GearStateCode = (typeof GearState)[keyof typeof GearState];

export interface GearOptions {
  ratios?: readonly number[];
  tol?: number;          // fraction, default 0.05; widened by vssTolKmh / vss per sample
  /** vss resolution term (km/h): whole-km/h vss makes a plain 5 % test fail below ~10 km/h. Default 0.5 */
  vssTolKmh?: number;
  minKmh?: number;       // default 5 (1st gear counts from ~5 km/h when coupled)
  couplingS?: number;    // default 0.5
  hz?: number;           // grid rate, default 10
  rpm?: string;
  vss?: string;
}

export interface GearResult {
  t: Float64Array;
  rpm: Float64Array;
  vss: Float64Array;
  /** rpm per km/h, NaN below minKmh */
  ratio: Float64Array;
  /** 1-5 when in gear, 0 otherwise */
  gear: Int8Array;
  state: Int8Array;
}

/** Which cluster a ratio falls in (1-based), 0 for none. */
export function gearOfRatio(ratio: number, ratios: readonly number[] = GEAR_RPM_PER_KMH, tol = 0.05): number {
  if (!Number.isFinite(ratio)) return 0;
  for (let k = 0; k < ratios.length; k++) if (Math.abs(ratio / ratios[k]! - 1) < tol) return k + 1;
  return 0;
}

/** Gear and coupling on arrays already on one uniform grid. */
export function gearFromArrays(t: Float64Array, rpm: Float64Array, vss: Float64Array, opts: GearOptions = {}): GearResult {
  const ratios = opts.ratios ?? GEAR_RPM_PER_KMH;
  const tol = opts.tol ?? 0.05;
  const minKmh = opts.minKmh ?? 5;
  const vssTol = opts.vssTolKmh ?? 0.5;
  const cs = opts.couplingS ?? 0.5;
  const n = t.length;
  const ratio = new Float64Array(n).fill(NaN);
  const raw = new Int8Array(n);
  for (let i = 0; i < n; i++) {
    const v = vss[i]!; const r = rpm[i]!;
    if (Number.isNaN(v) || Number.isNaN(r) || v < minKmh) continue;
    ratio[i] = r / v;
    raw[i] = gearOfRatio(ratio[i]!, ratios, tol + vssTol / v);
  }
  const gear = new Int8Array(n);
  const state = new Int8Array(n);
  let lo = 0; let hi = 0;
  for (let i = 0; i < n; i++) {
    const v = vss[i]!; const r = rpm[i]!;
    if (Number.isNaN(v) || Number.isNaN(r)) { state[i] = GearState.unknown; continue; }
    if (v < 1) { state[i] = GearState.stopped; continue; }
    if (v < minKmh) { state[i] = GearState.crawl; continue; }
    while (t[lo]! < t[i]! - cs - 1e-9) lo++;
    while (hi < n && t[hi]! <= t[i]! + cs + 1e-9) hi++;
    const k = raw[i]!;
    let held = k > 0;
    for (let j = lo; held && j < hi; j++) {
      // a missing sample inside the window does not break coupling; a different ratio does
      if (Number.isNaN(rpm[j]!) || Number.isNaN(vss[j]!)) continue;
      if (raw[j] !== k) held = false;
    }
    if (held) { gear[i] = k; state[i] = GearState.inGear; } else state[i] = GearState.decoupled;
  }
  return { t, rpm, vss, ratio, gear, state };
}

/** Gear over a whole drive on a uniform grid (default 10 Hz). */
export function estimateGear(drive: LoadedDrive, opts: GearOptions = {}): GearResult {
  const [t0, t1] = driveSpan(drive);
  const t = uniformGrid(t0, t1, opts.hz ?? 10);
  const rpm = align(getSeries(drive, opts.rpm ?? 'rpm'), t);
  const vss = align(getSeries(drive, opts.vss ?? 'vss'), t);
  return gearFromArrays(t, rpm, vss, opts);
}

/** Seconds per state / gear, for a coverage table. */
export function gearShare(g: GearResult): { stopped: number; crawl: number; decoupled: number; unknown: number; gears: number[] } {
  const dt = g.t.length > 1 ? g.t[1]! - g.t[0]! : 0;
  const out = { stopped: 0, crawl: 0, decoupled: 0, unknown: 0, gears: [0, 0, 0, 0, 0] };
  for (let i = 0; i < g.t.length; i++) {
    const s = g.state[i]!;
    if (s === GearState.inGear) out.gears[g.gear[i]! - 1] = (out.gears[g.gear[i]! - 1] ?? 0) + dt;
    else if (s === GearState.stopped) out.stopped += dt;
    else if (s === GearState.crawl) out.crawl += dt;
    else if (s === GearState.decoupled) out.decoupled += dt;
    else out.unknown += dt;
  }
  return out;
}
