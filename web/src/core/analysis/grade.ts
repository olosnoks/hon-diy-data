/**
 * Road grade estimate from engine force against acceleration
 * (docs/analysis/2026-11-26 vehicle-dynamics.md §2.2, work-dynamics/grade.py).
 *
 *   sinθ = (k·F_engine − m_eff·a − F_road(v)) / (m·g)
 *
 * F_engine comes from the Willans torque MODEL (MAP, rpm, IAT) through the
 * gear, scaled by k (0.91 fitted on the 2026-11-26 route). Used only in gear
 * with the brake off, or decoupled with the brake off (engine force 0); under
 * braking and during shifts the grade is unknown (NaN) and the smoothed curve
 * bridges it over distance. No altitude is logged: this is MODELLED, a check
 * for self-consistency only, good to a few metres over a few hundred.
 */
import type { LoadedDrive } from '../types';
import { G } from './dynamics';
import { GearState } from './gear';
import { DEFAULT_VEHICLE, dynamicsGrid, effectiveMass, overallRatio, roadLoad, torqueModel, type DynamicsGrid, type VehicleParams } from './power';
import { searchSorted } from './series';

export interface GradeOptions {
  /** engine-model scale (fitted k), default 0.91 */
  k?: number;
  /** distance window for the smoothed grade, ±m, default 50 (a 100 m grade) */
  halfWindowM?: number;
  baroKpa?: number;
}

export interface GradeResult {
  t: Float64Array;
  /** distance along the drive, m */
  s: Float64Array;
  /** per sample, fraction (0.01 = 1 %), NaN where not estimable */
  raw: Float64Array;
  /** mean of raw over ±halfWindowM of distance */
  grade: Float64Array;
  /** relative elevation, m, integral of grade over distance (starts at 0) */
  elevation: Float64Array;
  modelled: true;
  /** false when no brake channel was logged: braking could not be excluded */
  brakeKnown: boolean;
  note: string;
}

export function gradeFromGrid(g: DynamicsGrid, p: VehicleParams = DEFAULT_VEHICLE, opts: GradeOptions = {}, brakeKnown = true): GradeResult {
  const k = opts.k ?? 0.91;
  const n = g.t.length;
  const raw = new Float64Array(n).fill(NaN);
  for (let i = 0; i < n; i++) {
    const a = g.long.a[i]!; const v = g.long.v[i]!;
    if (Number.isNaN(a) || Number.isNaN(v) || v < 3) continue;
    const brake = g.brake[i]!;
    if (brakeKnown && !(brake < 0.5)) continue;
    const st = g.gear.state[i]!;
    let fEng: number; let gear: number;
    if (st === GearState.inGear) {
      gear = g.gear.gear[i]!;
      const rpm = g.rpm[i]!; const map = g.map[i]!;
      if (Number.isNaN(rpm) || Number.isNaN(map)) continue;
      const fuelOn = !(g.inj[i]! <= 0.05);
      const torque = torqueModel(rpm, map, g.iat[i], opts.baroKpa ?? 100.25, fuelOn);
      fEng = (k * torque * overallRatio(gear, p) * p.eta) / p.tyreRadiusM;
    } else if (st === GearState.decoupled) { fEng = 0; gear = 0; }
    else continue;
    const sin = (fEng - effectiveMass(gear, p) * a - roadLoad(v, p)) / (p.massKg * G);
    if (Math.abs(sin) < 0.3) raw[i] = Math.tan(Math.asin(sin));
  }
  const s = g.long.s;
  const half = opts.halfWindowM ?? 50;
  // prefix sums over samples for a distance-window mean
  const cs = new Float64Array(n + 1); const cn = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) {
    const ok = !Number.isNaN(raw[i]!);
    cs[i + 1] = cs[i]! + (ok ? raw[i]! : 0); cn[i + 1] = cn[i]! + (ok ? 1 : 0);
  }
  const grade = new Float64Array(n).fill(NaN);
  for (let i = 0; i < n; i++) {
    const a = searchSorted(s, s[i]! - half, 'left'); const b = searchSorted(s, s[i]! + half, 'right');
    const cnt = cn[b]! - cn[a]!;
    if (cnt > 0) grade[i] = (cs[b]! - cs[a]!) / cnt;
  }
  const elevation = new Float64Array(n);
  for (let i = 1; i < n; i++) {
    const ds = s[i]! - s[i - 1]!;
    const gr = Number.isNaN(grade[i]!) ? 0 : grade[i]!;
    elevation[i] = elevation[i - 1]! + gr * ds;
  }
  return { t: g.t, s, raw, grade, elevation, modelled: true, brakeKnown,
    note: 'Modelled from engine force (torque model × k) against acceleration; no altitude was logged.' };
}

export function estimateGrade(drive: LoadedDrive, p: VehicleParams = DEFAULT_VEHICLE, opts: GradeOptions = {}): GradeResult {
  const g = dynamicsGrid(drive, 10, p);
  const brakeKnown = (drive.series.brake?.t.length ?? 0) > 0;
  return gradeFromGrid(g, p, opts, brakeKnown);
}
