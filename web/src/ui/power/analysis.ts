/**
 * F5 Power over agent #7's analysis (src/core/analysis/power.ts): the screen's
 * editable inputs, the pull list with its quality (full throttle / part / too
 * short), the result card and the CSV. The physics (pull detection, torque
 * from acceleration, the band, the D16Y8 reference) live in core/analysis.
 *
 *   engine torque = [m_eff·a + F_road(v) + m·g·sin(grade)] · r / (ratio · η)
 */
import type { LoadedDrive } from '../../core/types';
import {
  DEFAULT_UNCERTAINTY, DEFAULT_VEHICLE, detectPulls, dynamicsGrid, hasSeries, nanMax, pullCurve, REFERENCE_D16Y8,
  referenceHp, referenceTorque, torqueToHp, type DynamicsGrid, type Pull as CorePull, type PullCurve, type Uncertainty,
  type VehicleParams,
} from '../../core/analysis';

export { referenceHp, referenceTorque, REFERENCE_D16Y8, torqueToHp };
export type { DynamicsGrid, PullCurve };

/** the D16Y8's VTEC changeover, roughly (rpm) */
export const VTEC_ZONE: [number, number] = [5000, 5600];

// ---- inputs ----------------------------------------------------------------------------

export interface PowerInputs {
  /** car + driver + kit */
  massKg: number;
  /** tyre size, e.g. "185/65R14" (the rolling radius is worked out from it) */
  tyre: string;
  /** drivetrain loss, % of engine torque */
  drivetrainLossPct: number;
  /** road load F = rollN + aeroK·v² (N, v in m/s); vehicle-dynamics.md §2 fit */
  rollN: number;
  aeroK: number;
  /** road grade %, null = unknown (0 assumed, ±2 % in the uncertainty) */
  gradePct: number | null;
}

export const DEFAULT_INPUTS: PowerInputs = {
  massKg: DEFAULT_VEHICLE.massKg, tyre: '185/65R14', drivetrainLossPct: Math.round((1 - DEFAULT_VEHICLE.eta) * 100),
  rollN: DEFAULT_VEHICLE.roadA, aeroK: DEFAULT_VEHICLE.roadB, gradePct: null,
};

/** Rolling radius (m) of a metric tyre size: (rim/2 + section × aspect) × 0.963
 *  (185/65R14 -> 0.287 m, the radius vehicle-dynamics.md uses). */
export function tyreRadius(size: string): number | null {
  const m = /(\d{3})\s*\/\s*(\d{2})\s*Z?R\s*(\d{2})/i.exec(size);
  if (!m) return null;
  const width = Number(m[1]), aspect = Number(m[2]) / 100, rim = Number(m[3]) * 25.4;
  return ((rim / 2 + width * aspect) * 0.963) / 1000;
}

export function vehicleOf(inputs: PowerInputs): VehicleParams {
  return {
    ...DEFAULT_VEHICLE,
    massKg: inputs.massKg,
    tyreRadiusM: tyreRadius(inputs.tyre) ?? DEFAULT_VEHICLE.tyreRadiusM,
    eta: 1 - inputs.drivetrainLossPct / 100,
    roadA: inputs.rollN,
    roadB: inputs.aeroK,
    grade: (inputs.gradePct ?? 0) / 100,
  };
}

export function uncertaintyOf(inputs: PowerInputs): Uncertainty {
  return { ...DEFAULT_UNCERTAINTY, gradeAbs: inputs.gradePct == null ? DEFAULT_UNCERTAINTY.gradeAbs : 0.005 };
}

export function validInputs(i: PowerInputs): string | null {
  if (!(i.massKg >= 500 && i.massKg <= 3000)) return 'mass 500-3,000 kg';
  if (tyreRadius(i.tyre) == null) return 'tyre like 185/65R14';
  if (!(i.drivetrainLossPct >= 0 && i.drivetrainLossPct <= 40)) return 'drivetrain loss 0-40 %';
  if (!(i.rollN >= 0 && i.rollN <= 1000) || !(i.aeroK >= 0 && i.aeroK <= 3)) return 'road load out of range';
  if (i.gradePct != null && !(Math.abs(i.gradePct) <= 15)) return 'grade within ±15 %';
  return null;
}

// ---- pulls ------------------------------------------------------------------------------

export type PullQuality = 'full' | 'part' | 'short';
export const QUALITY_LABEL: Record<PullQuality, string> = { full: 'FULL THROTTLE', part: 'PART', short: 'TOO SHORT' };

export interface PullItem {
  id: string;
  pull: CorePull;
  quality: PullQuality;
  tpsMean: number;
  mapMean: number;
  durationS: number;
  linkGap: boolean;
}

/** what makes a pull worth a curve */
export const PULL_RULES = {
  minTps: 20, minDurationS: 1,
  /** full throttle: held at 80 % or more on average, never below 60 % (the pedal settling at the start) */
  fullTps: 80, fullTpsMin: 60,
  /** shorter, or less rpm than this, is too short for a curve */
  minFullS: 2, minFullSpan: 1000,
};

function meanOf(a: Float64Array, i0: number, i1: number): number {
  let s = 0, n = 0;
  for (let i = i0; i < i1; i++) if (Number.isFinite(a[i])) { s += a[i]!; n++; }
  return n ? s / n : NaN;
}

export interface DriveAnalysis {
  grid: DynamicsGrid;
  pulls: PullItem[];
  /** the highest rpm logged in the drive */
  rpmMax: number;
}

/** The analysis grid of a drive and its pulls, best first. null: no rpm / vss / tps. */
export function analyseDrive(drive: LoadedDrive | null | undefined, inputs: PowerInputs = DEFAULT_INPUTS): DriveAnalysis | null {
  if (!drive || !hasSeries(drive, 'rpm') || !hasSeries(drive, 'vss') || !hasSeries(drive, 'tps')) return null;
  const grid = dynamicsGrid(drive, 10, vehicleOf(inputs));
  const raw = detectPulls(grid, { minTps: PULL_RULES.minTps, minDurationS: PULL_RULES.minDurationS });
  const rpm = drive.series.rpm!;
  const pulls = raw.map((pull): PullItem => {
    const durationS = pull.t1 - pull.t0;
    const tpsMean = meanOf(grid.tps, pull.i0, pull.i1);
    const span = pull.rpm1 - pull.rpm0;
    let linkGap = false;
    for (let i = 1; i < rpm.t.length; i++) {
      if (rpm.t[i]! >= pull.t0 && rpm.t[i - 1]! <= pull.t1 + 3 && rpm.t[i]! - rpm.t[i - 1]! > 1) { linkGap = true; break; }
    }
    const quality: PullQuality = durationS < PULL_RULES.minFullS || span < PULL_RULES.minFullSpan ? 'short'
      : pull.fullThrottle || pull.tpsMin >= PULL_RULES.fullTps || (tpsMean >= PULL_RULES.fullTps && pull.tpsMin >= PULL_RULES.fullTpsMin) ? 'full' : 'part';
    return { id: pull.t0.toFixed(1), pull, quality, durationS, linkGap, tpsMean, mapMean: meanOf(grid.map, pull.i0, pull.i1) };
  });
  const rank: Record<PullQuality, number> = { full: 0, part: 1, short: 2 };
  pulls.sort((a, b) => rank[a.quality] - rank[b.quality] || (b.pull.rpm1 - b.pull.rpm0) - (a.pull.rpm1 - a.pull.rpm0));
  return { grid, pulls, rpmMax: nanMax(rpm.v) };
}

// ---- the result --------------------------------------------------------------------------

export interface CurvePoint {
  rpm: number;
  nm: number; nmLo: number; nmHi: number;
  hp: number; hpLo: number; hpHi: number;
  refNm: number; refHp: number;
  n: number;
}

export interface PowerResult {
  item: PullItem;
  curve: PullCurve;
  points: CurvePoint[];
  peakNm: number; peakNmRpm: number;
  peakHp: number; peakHpRpm: number; peakHpAtEnd: boolean;
  vsRefPct: number;
  uncertaintyPct: number;
  gradeKnown: boolean;
  radiusM: number;
  iat: number | null;
  baro: number | null;
}

export function computeResult(drive: LoadedDrive, a: DriveAnalysis, item: PullItem, inputs: PowerInputs): PowerResult | null {
  const p = vehicleOf(inputs);
  const curve = pullCurve(a.grid, item.pull, p, uncertaintyOf(inputs));
  const points: CurvePoint[] = curve.bins.filter((b) => Number.isFinite(b.torque)).map((b) => ({
    rpm: b.rpm, nm: b.torque, nmLo: b.torqueLo, nmHi: b.torqueHi,
    hp: torqueToHp(b.torque, b.rpm), hpLo: torqueToHp(b.torqueLo, b.rpm), hpHi: torqueToHp(b.torqueHi, b.rpm),
    refNm: b.refTorque, refHp: torqueToHp(b.refTorque, b.rpm), n: b.n,
  }));
  if (!points.length) return null;
  let pT = points[0]!, pH = points[0]!;
  for (const q of points) { if (q.nm > pT.nm) pT = q; if (q.hp > pH.hp) pH = q; }
  const avg = (name: string) => {
    const s = drive.series[name];
    if (!s) return null;
    let sum = 0, n = 0;
    for (let i = 0; i < s.t.length; i++) {
      if (s.t[i]! >= item.pull.t0 - 5 && s.t[i]! <= item.pull.t1 + 5 && Number.isFinite(s.v[i])) { sum += s.v[i]!; n++; }
    }
    return n ? sum / n : null;
  };
  return {
    item, curve, points,
    peakNm: pT.nm, peakNmRpm: pT.rpm, peakHp: pH.hp, peakHpRpm: pH.rpm, peakHpAtEnd: pH === points[points.length - 1],
    vsRefPct: curve.ofReference * 100,
    uncertaintyPct: Math.abs(curve.uncertainty.relative) * 100,
    gradeKnown: inputs.gradePct != null,
    radiusM: p.tyreRadiusM,
    iat: avg('iat'), baro: avg('baro'),
  };
}

// ---- words --------------------------------------------------------------------------------

export const ordinal = (g: number) => (['', '1st', '2nd', '3rd', '4th', '5th'][g] ?? `${g}th`);
export const thousands = (v: number) => (Number.isFinite(v) ? Math.round(v).toLocaleString('en-US') : '---');

export function pullDetail(it: PullItem): string {
  const parts = [`${it.durationS.toFixed(1)} s`];
  if (Number.isFinite(it.tpsMean)) parts.push(`TPS ${Math.round(it.tpsMean)} %`);
  if (Number.isFinite(it.mapMean)) parts.push(`MAP ${Math.round(it.mapMean)} kPa`);
  if (it.linkGap) parts.push('link gap');
  return parts.join(' · ');
}

/** The CSV of a result: one row per 250 rpm bin, the inputs in # comments. */
export function curveCsv(res: PowerResult, inputs: PowerInputs, name = ''): string {
  const pl = res.item.pull;
  const L: string[] = [];
  L.push(`# hondalog power curve${name ? ` · ${name}` : ''} (measured + modelled)`);
  L.push(`# pull t=${pl.t0.toFixed(2)}-${pl.t1.toFixed(2)} s, ${ordinal(pl.gear)} gear, ${thousands(pl.rpm0)}-${thousands(pl.rpm1)} rpm, ${QUALITY_LABEL[res.item.quality].toLowerCase()}`);
  L.push(`# mass ${inputs.massKg} kg, tyre ${inputs.tyre} (r ${res.radiusM.toFixed(4)} m), drivetrain loss ${inputs.drivetrainLossPct} %, road load ${inputs.rollN} N + ${inputs.aeroK} N/(m/s)^2 v^2, grade ${inputs.gradePct == null ? 'unknown (0 +-2 %)' : `${inputs.gradePct} %`}`);
  L.push(`# reference: ${REFERENCE_D16Y8.name} (${REFERENCE_D16Y8.ratedHp} hp @ ${REFERENCE_D16Y8.ratedHpRpm}, ${REFERENCE_D16Y8.ratedNm} N m @ ${REFERENCE_D16Y8.ratedNmRpm})`);
  L.push('rpm,torque_nm,torque_lo_nm,torque_hi_nm,power_hp,power_lo_hp,power_hi_hp,ref_torque_nm,ref_power_hp,samples');
  for (const p of res.points) {
    L.push([p.rpm.toFixed(0), p.nm.toFixed(1), p.nmLo.toFixed(1), p.nmHi.toFixed(1), p.hp.toFixed(1), p.hpLo.toFixed(1),
      p.hpHi.toFixed(1), p.refNm.toFixed(1), p.refHp.toFixed(1), String(p.n)].join(','));
  }
  return L.join('\n') + '\n';
}

// ---- baseline ------------------------------------------------------------------------------

export interface Baseline { label: string; savedUnix: number; points: Array<{ rpm: number; nm: number; hp: number }> }
const BASELINE_KEY = 'hondalog.power.baseline';

export function loadBaseline(): Baseline | null {
  try {
    const raw = localStorage.getItem(BASELINE_KEY);
    const b = raw ? (JSON.parse(raw) as Baseline) : null;
    return b && Array.isArray(b.points) ? b : null;
  } catch {
    return null;
  }
}

export function saveBaseline(res: PowerResult, label: string): Baseline {
  const b: Baseline = { label, savedUnix: Date.now() / 1000, points: res.points.map((p) => ({ rpm: p.rpm, nm: p.nm, hp: p.hp })) };
  try { localStorage.setItem(BASELINE_KEY, JSON.stringify(b)); } catch { /* storage blocked */ }
  return b;
}
