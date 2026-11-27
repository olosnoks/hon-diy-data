/**
 * Calculated channels for Replay: power and torque now, gear, accel, lateral g,
 * grade estimate, fuel flow, rpm − idle target. Each becomes a series like a
 * logged channel, so it can be a lane and is read at the cursor the same way.
 *
 * The numbers come from agent #7's src/core/analysis (vehicle-dynamics.md:
 * the Willans torque model, the gear clusters, longitudinal accel from vss,
 * lateral accel from the GPS heading rate, the modelled grade, the injector
 * fuel flow). All of it is modelled, not measured: the screen says "est.".
 */
import type { LoadedDrive } from '../../core/types';
import { G, lateralAccel, longitudinalAccel } from '../../core/analysis/dynamics';
import { estimateGear, GEAR_RPM_PER_KMH, gearOfRatio } from '../../core/analysis/gear';
import { estimateGrade } from '../../core/analysis/grade';
import { fuelFlowGs, gsToLph, torqueModel, torqueToHp } from '../../core/analysis/power';
import { valueAt } from '../../core/analysis/cursors';
import { driveSpan, searchSorted } from '../../core/analysis/series';

export interface Series { t: Float64Array; v: Float64Array }

export { GEAR_RPM_PER_KMH };

/** 1..5, or NaN (stopped, crawling, clutch in, coasting, shifting). */
export function gearOf(rpm: number, vssKmh: number): number {
  if (!(vssKmh >= 8) || !(rpm > 500)) return NaN;
  const g = gearOfRatio(rpm / vssKmh);
  return g > 0 ? g : NaN;
}

/** Crank torque (N·m) from the torque model, NaN when the engine is not running. */
export function torqueNow(rpm: number, mapKpa: number, iatC: number, injMs: number): number {
  if (!(rpm > 400) || !Number.isFinite(mapKpa)) return NaN;
  const fuelOn = !(injMs <= 0.05);
  return torqueModel(rpm, mapKpa, iatC, undefined, fuelOn);
}

function safe<T>(f: () => T): T | null {
  try { return f(); } catch { return null; }
}

function onTimes(t: Float64Array, f: (t: number, i: number) => number): Series {
  const v = new Float64Array(t.length);
  for (let i = 0; i < t.length; i++) v[i] = f(t[i]!, i);
  return { t, v };
}

/** All calculated channels of a drive, plus the GPS speed and heading as series. */
export function deriveChannels(drive: LoadedDrive): Record<string, Series> {
  const out: Record<string, Series> = {};
  const s = drive.series;
  const at = (name: string, tt: number) => (s[name] ? valueAt(s[name]!, tt) : NaN);
  if (drive.gps.t.length) {
    out.gps_speed = { t: drive.gps.t, v: drive.gps.speedKmh };
    out.gps_heading = { t: drive.gps.t, v: drive.gps.heading };
    const lat = safe(() => lateralAccel(drive));
    if (lat && lat.t.length) out.lat_g = { t: lat.t, v: lat.g };
  }
  const rpm = s.rpm;
  if (!rpm || rpm.t.length === 0) return out;
  if (s.vss?.t.length) {
    const long = safe(() => longitudinalAccel(drive));
    if (long && long.t.length) out.accel_g = { t: long.t, v: long.a.map((a) => a / G) };
    const gear = safe(() => estimateGear(drive));
    if (gear && gear.t.length) out.gear = { t: gear.t, v: Float64Array.from(gear.gear, (g) => (g > 0 ? g : NaN)) };
    if (s.map?.t.length && s.tps?.t.length) {
      const grade = safe(() => estimateGrade(drive));
      if (grade && grade.t.length) out.grade = { t: grade.t, v: grade.grade.map((x) => x * 100) };
    }
  }
  if (s.map?.t.length) {
    const torque = onTimes(rpm.t, (tt, i) => torqueNow(rpm.v[i]!, at('map', tt), at('iat', tt), at('inj', tt)));
    out.torque = torque;
    out.power = onTimes(rpm.t, (_tt, i) => torqueToHp(torque.v[i]!, rpm.v[i]!));
  }
  if (s.fuel_flow?.t.length) out.fuel_lph = s.fuel_flow;
  else if (s.inj?.t.length) out.fuel_lph = onTimes(rpm.t, (tt, i) => gsToLph(fuelFlowGs(at('inj', tt), rpm.v[i]!)));
  if (s.idle_target?.t.length) out.rpm_err = onTimes(rpm.t, (tt, i) => rpm.v[i]! - at('idle_target', tt));
  return out;
}

/** A drive with its calculated channels merged in (what Replay opens). The
 *  calculated ones are cut to the drive's own span: a smoothing grid or the
 *  GPS lag shift must not make the drive look longer. */
export function withDerived(drive: LoadedDrive): LoadedDrive {
  const [t0, t1] = driveSpan(drive);
  const derived = deriveChannels(drive);
  for (const [k, s] of Object.entries(derived)) {
    if (!s.t.length || (s.t[0]! >= t0 && s.t[s.t.length - 1]! <= t1)) continue;
    const a = searchSorted(s.t, t0, 'left'), b = searchSorted(s.t, t1, 'right');
    derived[k] = { t: s.t.slice(a, b), v: s.v.slice(a, b) };
  }
  return { ...drive, series: { ...drive.series, ...derived } };
}

/** The pull the cursor is in: back from t while the throttle stays open. */
export interface Pull { t0: number; t1: number; rpm0: number; rpm1: number; tpsMean: number; gear: number }

export function pullAt(all: Record<string, Series>, t: number): Pull | null {
  const tps = all.tps, rpm = all.rpm;
  if (!tps || !rpm || !(valueAt(tps, t) > 5)) return null;
  let i = 0;
  while (i < tps.t.length && tps.t[i]! <= t) i++;
  let j = i - 1;
  let sum = 0, n = 0;
  while (j >= 0 && tps.v[j]! > 5) { sum += tps.v[j]!; n++; j--; }
  const t0 = tps.t[Math.max(0, j + 1)]!;
  if (t - t0 < 0.3) return null;
  const gear = all.gear ? valueAt(all.gear, t) : NaN;
  return { t0, t1: t, rpm0: valueAt(rpm, t0), rpm1: valueAt(rpm, t), tpsMean: n ? sum / n : 0, gear };
}
