/**
 * Engine torque and power from a pull, the D16Y8 reference curve, the
 * speed-density airflow cross-check and fuel flow from the injectors
 * (docs/analysis/2026-11-26 vehicle-dynamics.md §1, §7; work-dynamics/
 * common.py, power.py).
 *
 *   torque = (m_eff·a + F_road(v) + m·g·sinθ) · r / (overall ratio · η)
 *   hp     = torque · rpm / 7121
 *
 * m_eff adds the wheel inertia and the engine inertia reflected through the
 * gear; the overall ratio (gearbox × final drive) comes from the measured rpm
 * per km/h. Everything here is "measured + modelled": the acceleration is
 * measured, mass, η, road load and grade are assumptions the user can edit.
 */
import type { LoadedDrive } from '../types';
import { G, longAccelFromArrays, type LongAccel } from './dynamics';
import { GEAR_RPM_PER_KMH, gearFromArrays, type GearResult } from './gear';
import { align, driveSpan, fillGaps, getSeries, hasSeries, nanMean, runs, savgol, uniformGrid } from './series';

export interface VehicleParams {
  massKg: number;            // car + driver + kit
  tyreRadiusM: number;       // rolling radius
  eta: number;               // drivetrain efficiency
  roadA: number;             // road load F = roadA + roadB·v² (N, v in m/s)
  roadB: number;
  /** rpm per km/h of ECU vss, gears 1..5 */
  ratios: readonly number[];
  vssScale: number;          // true speed / vss
  wheelInertia: number;      // kg m², all four wheels
  engineInertia: number;     // kg m², crank + flywheel + clutch + gearbox input
  /** grade as a fraction (0.02 = 2 %) assumed during the pull */
  grade: number;
}

/** 2000 Civic EX defaults (vehicle-dynamics.md §7; road load fitted in §2). */
export const DEFAULT_VEHICLE: VehicleParams = {
  massKg: 1195, tyreRadiusM: 0.287, eta: 0.9, roadA: 179, roadB: 0.384,
  ratios: GEAR_RPM_PER_KMH, vssScale: 1.011, wheelInertia: 2.6, engineInertia: 0.13, grade: 0,
};

/** Spread of each assumption for the uncertainty band (vehicle-dynamics.md §6). */
export interface Uncertainty { gradeAbs: number; massKg: number; etaLo: number; etaHi: number; radiusRel: number }
export const DEFAULT_UNCERTAINTY: Uncertainty = { gradeAbs: 0.02, massKg: 50, etaLo: 0.86, etaHi: 0.92, radiusRel: 0.01 };

export const HP_PER_NM_RPM = 1 / 7121;
export const torqueToHp = (nm: number, rpm: number): number => (nm * rpm) / 7121;

/** Engine revolutions per wheel revolution in gear k (1-based). */
export function overallRatio(gear: number, p: VehicleParams = DEFAULT_VEHICLE): number {
  const wheelRpmPerKmh = 1000 / 60 / (2 * Math.PI * p.tyreRadiusM) / p.vssScale;
  return p.ratios[gear - 1]! / wheelRpmPerKmh;
}

/** Effective translational mass: wheel inertia, plus the engine's when coupled (gear > 0). */
export function effectiveMass(gear: number, p: VehicleParams = DEFAULT_VEHICLE): number {
  let m = p.massKg + p.wheelInertia / p.tyreRadiusM ** 2;
  if (gear > 0) m += (p.engineInertia * overallRatio(gear, p) ** 2) / p.tyreRadiusM ** 2;
  return m;
}

export function roadLoad(v: number, p: VehicleParams = DEFAULT_VEHICLE): number {
  return p.roadA + p.roadB * v * v;
}

/** Crank torque (N·m) from acceleration a (m/s²) at true speed v (m/s) in gear. */
export function torqueFromAccel(a: number, v: number, gear: number, p: VehicleParams = DEFAULT_VEHICLE): number {
  const sin = Math.sin(Math.atan(p.grade));
  const f = effectiveMass(gear, p) * a + roadLoad(v, p) + p.massKg * G * sin;
  return (f * p.tyreRadiusM) / (overallRatio(gear, p) * p.eta);
}

/** Torque with an uncertainty band: each assumption is moved to both ends of
 *  its spread, and the shifts are added in quadrature. */
export function torqueWithBand(a: number, v: number, gear: number, p: VehicleParams = DEFAULT_VEHICLE,
  u: Uncertainty = DEFAULT_UNCERTAINTY): { torque: number; lo: number; hi: number; parts: Record<string, number> } {
  const t = torqueFromAccel(a, v, gear, p);
  const variants: Record<string, [VehicleParams, VehicleParams]> = {
    grade: [{ ...p, grade: p.grade - u.gradeAbs }, { ...p, grade: p.grade + u.gradeAbs }],
    mass: [{ ...p, massKg: p.massKg - u.massKg }, { ...p, massKg: p.massKg + u.massKg }],
    eta: [{ ...p, eta: u.etaLo }, { ...p, eta: u.etaHi }],
    radius: [{ ...p, tyreRadiusM: p.tyreRadiusM * (1 - u.radiusRel) }, { ...p, tyreRadiusM: p.tyreRadiusM * (1 + u.radiusRel) }],
  };
  let up = 0; let down = 0;
  const parts: Record<string, number> = {};
  for (const [k, [pa, pb]] of Object.entries(variants)) {
    const ta = torqueFromAccel(a, v, gear, pa) - t; const tb = torqueFromAccel(a, v, gear, pb) - t;
    const du = Math.max(ta, tb, 0); const dd = Math.max(-ta, -tb, 0);
    up += du * du; down += dd * dd;
    parts[k] = Math.max(du, dd);
  }
  return { torque: t, lo: t - Math.sqrt(down), hi: t + Math.sqrt(up), parts };
}

// ---------------------------------------------------------------- reference curve

/** D16Y8 (2000 Civic EX) rated 127 hp at 6,600 rpm and 145 N·m (107 lb·ft) at
 *  5,500 rpm. Only those two points are published; the shape between is
 *  APPROXIMATE (drawn through them, low cam below ~5,000 rpm, VTEC above). */
export const REFERENCE_D16Y8 = {
  name: 'D16Y8 rated (approximate shape)',
  approximate: true,
  note: 'Only 127 hp @ 6,600 rpm and 145 N·m @ 5,500 rpm are published; the curve between them is drawn by hand.',
  ratedHp: 127, ratedHpRpm: 6600, ratedNm: 145, ratedNmRpm: 5500,
  rpm: [1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000, 6600, 7000, 7200],
  nm: [98, 108, 117, 122, 127, 131, 134, 136, 139, 145, 142, 137, 130, 126],
} as const;

export function interp(x: number, xs: readonly number[], ys: readonly number[]): number {
  if (!xs.length || Number.isNaN(x)) return NaN;
  if (x <= xs[0]!) return ys[0]!;
  if (x >= xs[xs.length - 1]!) return ys[ys.length - 1]!;
  let i = 1;
  while (xs[i]! < x) i++;
  const f = (x - xs[i - 1]!) / (xs[i]! - xs[i - 1]!);
  return ys[i - 1]! + f * (ys[i]! - ys[i - 1]!);
}

export function referenceTorque(rpm: number): number { return interp(rpm, REFERENCE_D16Y8.rpm, REFERENCE_D16Y8.nm); }
export function referenceHp(rpm: number): number { return torqueToHp(referenceTorque(rpm), rpm); }

// ---------------------------------------------------------------- airflow and fuel

export const DISPLACEMENT_M3 = 1.59e-3;
export const R_AIR = 287.05;

/** Assumed speed-density VE, MAP-referenced (vehicle-dynamics.md §1.1). */
export function veAssumed(rpm: number, mapKpa: number): number {
  const wot = interp(rpm / 1000, [0.7, 1.5, 2.5, 3.5, 4.6, 5.5, 6.2, 7.0], [0.62, 0.74, 0.83, 0.88, 0.9, 0.89, 0.86, 0.8]);
  const m = Math.min(Math.max(mapKpa, 15), 101);
  return wot * (0.8 + (0.2 * (m - 15)) / 86);
}

/** Air mass flow, g/s: VE · MAP · Vd / (R · T) · rpm / 120. IAT in °C (25 if unknown). */
export function airflowGs(rpm: number, mapKpa: number, iatC = 25, ve = veAssumed(rpm, mapKpa)): number {
  const tk = (Number.isFinite(iatC) ? iatC : 25) + 273.15;
  return ((ve * mapKpa * 1e3 * DISPLACEMENT_M3) / (R_AIR * tk)) * (rpm / 120) * 1000;
}

/** Crank hp from airflow by the rule of thumb 1.25-1.35 hp per g/s (healthy NA petrol engine). */
export function airflowHp(airGs: number): { lo: number; hi: number; mid: number } {
  return { lo: airGs * 1.25, hi: airGs * 1.35, mid: airGs * 1.3 };
}

/** Willans-line brake torque model (N·m) from MAP (stoich-equivalent fuel);
 *  constants from vehicle-dynamics.md §1.1. Modelled, not measured. */
export function torqueModel(rpm: number, mapKpa: number, iatC = 25, baroKpa = 100.25, fuelOn = true): number {
  const tk = (Number.isFinite(iatC) ? iatC : 25) + 273.15;
  const airCyc = (veAssumed(rpm, mapKpa) * mapKpa * 1e3 * DISPLACEMENT_M3) / (R_AIR * tk);   // kg per 2 rev
  const fuel = fuelOn ? airCyc / 14.7 : 0;
  const imep = (0.42 * fuel * 43e6) / DISPLACEMENT_M3 / 1e3;
  const n = rpm / 1000;
  const fmep = 60 + 12 * n + 2.6 * n * n;
  const flow = (rpm / 6000) * (mapKpa / 100);
  const pmep = baroKpa + 8 * flow * flow - mapKpa;
  return ((imep - fmep - pmep) * 1e3 * DISPLACEMENT_M3) / (4 * Math.PI);
}

export interface InjectorParams { ccPerMin: number; deadMs: number; cylinders: number; densityGcc: number }
/** 240 cc/min and 1.0 ms dead time are unverified. */
export const DEFAULT_INJECTOR: InjectorParams = { ccPerMin: 240, deadMs: 1.0, cylinders: 4, densityGcc: 0.745 };

/** Fuel mass flow, g/s, from the injector pulse (ms) and rpm. 0 in fuel cut (pw <= 0.05 ms). */
export function fuelFlowGs(injMs: number, rpm: number, p: InjectorParams = DEFAULT_INJECTOR): number {
  if (Number.isNaN(injMs) || Number.isNaN(rpm)) return NaN;
  if (injMs <= 0.05) return 0;
  const pw = Math.max(injMs - p.deadMs, 0);
  return p.cylinders * (p.ccPerMin / 60000) * pw * (rpm / 120) * p.densityGcc;
}

export const gsToLph = (gs: number, p: InjectorParams = DEFAULT_INJECTOR): number => (gs * 3600) / (p.densityGcc * 1000);

export interface FuelSummary {
  grams: number; litres: number; km: number; lPer100km: number; mpgUs: number;
  idleLitres: number; cutSeconds: number; movingSeconds: number;
  t: Float64Array; gs: Float64Array;
}

/** Fuel over a drive (or [t0, t1]) on a 10 Hz grid. */
export function fuelUsed(drive: LoadedDrive, p: InjectorParams = DEFAULT_INJECTOR, t0?: number, t1?: number,
  vssScale = 1.011): FuelSummary {
  const [a, b] = driveSpan(drive);
  const t = uniformGrid(t0 ?? a, t1 ?? b, 10);
  const inj = align(getSeries(drive, 'inj'), t); const rpm = align(getSeries(drive, 'rpm'), t);
  const vss = align(getSeries(drive, 'vss'), t);
  const gs = new Float64Array(t.length);
  let grams = 0; let idle = 0; let km = 0; let cut = 0; let moving = 0;
  for (let i = 0; i < t.length; i++) {
    gs[i] = fuelFlowGs(inj[i]!, rpm[i]!, p);
    const g = Number.isNaN(gs[i]!) ? 0 : gs[i]! * 0.1;
    grams += g;
    const v = vss[i]!;
    if (!Number.isNaN(v)) {
      km += (v * vssScale * 0.1) / 3600;
      if (v < 1) idle += g; else { moving += 0.1; if (inj[i]! <= 0.05 && rpm[i]! > 1000) cut += 0.1; }
    }
  }
  const litres = grams / (p.densityGcc * 1000);
  const l100 = km > 0 ? (litres / km) * 100 : NaN;
  return { grams, litres, km, lPer100km: l100, mpgUs: km > 0 && litres > 0 ? 235.215 / l100 : NaN,
    idleLitres: idle / (p.densityGcc * 1000), cutSeconds: cut, movingSeconds: moving, t, gs };
}

// ---------------------------------------------------------------- pulls

export interface PullOptions {
  /** throttle held at or above this (%); 80 = full throttle */
  minTps?: number;
  minDurationS?: number;     // default 2
  /** rpm must rise at least this fast throughout (rpm/s), default 50 */
  minRpmRate?: number;
  /** drop this much from the end: the 1.5 s derivative window smears the lift (default 0.8) */
  trimEndS?: number;
  hz?: number;
}

export interface Pull {
  i0: number; i1: number;        // [i0, i1) on the analysis grid, after trimming
  t0: number; t1: number;
  gear: number;
  rpm0: number; rpm1: number;
  tpsMin: number;
  fullThrottle: boolean;
}

export interface PullPoint {
  t: number; rpm: number; v: number; a: number; map: number;
  torque: number; torqueLo: number; torqueHi: number;
  hp: number; hpLo: number; hpHi: number;
  refTorque: number; refHp: number;
  /** speed-density cross-check, NaN without MAP */
  airGs: number; airHpLo: number; airHpHi: number;
}

export interface PullBin {
  rpmLo: number; rpmHi: number; n: number; rpm: number; a: number; map: number;
  torque: number; torqueLo: number; torqueHi: number; hp: number; refTorque: number;
}

export interface PullCurve {
  pull: Pull;
  points: PullPoint[];
  bins: PullBin[];
  /** measured torque / reference torque, mean over the pull */
  ofReference: number;
  peakHp: { hp: number; rpm: number };
  peakTorque: { nm: number; rpm: number };
  /** relative half-width of the band at mid-pull, and per source (N·m) */
  uncertainty: { relative: number; parts: Record<string, number> };
  params: VehicleParams;
  label: 'measured + modelled';
}

/** Arrays of a drive on the analysis grid: what pulls, power and grade need. */
export interface DynamicsGrid {
  t: Float64Array; rpm: Float64Array; vss: Float64Array; tps: Float64Array;
  map: Float64Array; iat: Float64Array; brake: Float64Array; inj: Float64Array;
  gear: GearResult; long: LongAccel;
}

export function dynamicsGrid(drive: LoadedDrive, hz = 10, p: VehicleParams = DEFAULT_VEHICLE): DynamicsGrid {
  const [a, b] = driveSpan(drive);
  const t = uniformGrid(a, b, hz);
  const ch = (n: string) => align(getSeries(drive, n), t);
  const rpm = ch('rpm'); const vss = ch('vss');
  return {
    t, rpm, vss, tps: ch('tps'), map: ch('map'), iat: ch('iat'), brake: ch('brake'), inj: ch('inj'),
    gear: gearFromArrays(t, rpm, vss, { ratios: p.ratios }),
    long: longAccelFromArrays(t, vss, { hz, vssScale: p.vssScale }),
  };
}

/** Pulls: one gear, throttle held >= minTps, rpm rising, at least minDurationS. */
export function detectPulls(g: DynamicsGrid, opts: PullOptions = {}): Pull[] {
  const minTps = opts.minTps ?? 80;
  const hz = opts.hz ?? (g.t.length > 1 ? 1 / (g.t[1]! - g.t[0]!) : 10);
  const minRate = opts.minRpmRate ?? 50;
  const rpmF = fillGaps(g.t, g.rpm, 0.5);
  const rate = savgol(rpmF, Math.max(3, Math.round(0.5 * hz) | 1), 2, 1, 1 / hz);
  const out: Pull[] = [];
  const ok = (i: number) => g.gear.gear[i]! > 0 && g.tps[i]! >= minTps && rate[i]! >= minRate;
  for (const [s, e] of runs(g.t.length, ok)) {
    // split where the gear changes
    let a = s;
    for (let i = s + 1; i <= e; i++) {
      if (i === e || g.gear.gear[i] !== g.gear.gear[a]) {
        const dur = (i - a) / hz;
        if (dur >= (opts.minDurationS ?? 2)) {
          const trim = Math.round((opts.trimEndS ?? 0.8) * hz);
          const i1 = Math.max(a + 2, i - trim);
          let tpsMin = Infinity;
          for (let k = a; k < i; k++) tpsMin = Math.min(tpsMin, g.tps[k]!);
          out.push({ i0: a, i1, t0: g.t[a]!, t1: g.t[i1 - 1]!, gear: g.gear.gear[a]!, rpm0: g.rpm[a]!, rpm1: g.rpm[i1 - 1]!,
            tpsMin, fullThrottle: tpsMin >= 80 });
        }
        a = i;
      }
    }
  }
  return out;
}

/** Torque and power through one pull, with the band, 250 rpm bins and the reference. */
export function pullCurve(g: DynamicsGrid, pull: Pull, p: VehicleParams = DEFAULT_VEHICLE,
  u: Uncertainty = DEFAULT_UNCERTAINTY, binRpm = 250): PullCurve {
  const points: PullPoint[] = [];
  let mid: ReturnType<typeof torqueWithBand> | null = null;
  for (let i = pull.i0; i < pull.i1; i++) {
    const a = g.long.a[i]!; const v = g.long.v[i]!; const rpm = g.rpm[i]!;
    if (Number.isNaN(a) || Number.isNaN(v) || Number.isNaN(rpm)) continue;
    const band = torqueWithBand(a, v, pull.gear, p, u);
    if (i >= (pull.i0 + pull.i1) >> 1 && !mid) mid = band;
    const map = g.map[i]!;
    const air = Number.isNaN(map) ? NaN : airflowGs(rpm, map, g.iat[i]);
    const ah = airflowHp(air);
    points.push({ t: g.t[i]!, rpm, v, a, map, torque: band.torque, torqueLo: band.lo, torqueHi: band.hi,
      hp: torqueToHp(band.torque, rpm), hpLo: torqueToHp(band.lo, rpm), hpHi: torqueToHp(band.hi, rpm),
      refTorque: referenceTorque(rpm), refHp: referenceHp(rpm), airGs: air, airHpLo: ah.lo, airHpHi: ah.hi });
  }
  const bins: PullBin[] = [];
  if (points.length) {
    const lo = Math.floor(Math.min(...points.map((q) => q.rpm)) / binRpm) * binRpm;
    const hi = Math.max(...points.map((q) => q.rpm));
    for (let b = lo; b <= hi; b += binRpm) {
      const sel = points.filter((q) => q.rpm >= b && q.rpm < b + binRpm);
      if (!sel.length) continue;
      const m = (f: (q: PullPoint) => number) => nanMean(sel.map(f));
      const rpm = m((q) => q.rpm);
      bins.push({ rpmLo: b, rpmHi: b + binRpm, n: sel.length, rpm, a: m((q) => q.a), map: m((q) => q.map),
        torque: m((q) => q.torque), torqueLo: m((q) => q.torqueLo), torqueHi: m((q) => q.torqueHi),
        hp: torqueToHp(m((q) => q.torque), rpm), refTorque: referenceTorque(rpm) });
    }
  }
  const ofRef = points.length ? nanMean(points.map((q) => q.torque)) / nanMean(points.map((q) => q.refTorque)) : NaN;
  const ph = points.reduce((best, q) => (!(q.hp <= best.hp) ? { hp: q.hp, rpm: q.rpm } : best), { hp: NaN, rpm: NaN });
  const pt = points.reduce((best, q) => (!(q.torque <= best.nm) ? { nm: q.torque, rpm: q.rpm } : best), { nm: NaN, rpm: NaN });
  const m0 = mid ?? (points.length ? torqueWithBand(points[0]!.a, points[0]!.v, pull.gear, p, u) : null);
  return {
    pull, points, bins, ofReference: ofRef,
    peakHp: ph,
    peakTorque: pt,
    uncertainty: m0 ? { relative: (m0.hi - m0.lo) / 2 / m0.torque, parts: m0.parts } : { relative: NaN, parts: {} },
    params: p, label: 'measured + modelled',
  };
}

/** Every pull of a drive with its curve. */
export function drivePulls(drive: LoadedDrive, p: VehicleParams = DEFAULT_VEHICLE, opts: PullOptions = {},
  u: Uncertainty = DEFAULT_UNCERTAINTY): PullCurve[] {
  if (!hasSeries(drive, 'rpm') || !hasSeries(drive, 'vss') || !hasSeries(drive, 'tps')) return [];
  const g = dynamicsGrid(drive, opts.hz ?? 10, p);
  return detectPulls(g, opts).map((pl) => pullCurve(g, pl, p, u));
}
