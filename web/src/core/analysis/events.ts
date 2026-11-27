/**
 * Events found in a drive, each with a short machine verdict
 * (docs/analysis/2026-11-26 drive-idle-control.md, stall-forensics.md,
 * link-dropouts.md, vehicle-dynamics.md §5; hondalog/triggers/presets.py).
 *
 *  near_stall   rpm < 600 at a standstill (ECU answering, engine still running)
 *  stall        rpm read below 50 after running (> 400): the ECU kept answering
 *  dropout      ECU silent, and the first rpm after it is above 400 and close to
 *               the rpm before (< 1.25 × before + 100), or the car was moving
 *               (> 5 km/h) before and after: the engine never stopped
 *  keyoff       ECU silent and back at rpm 0 / cranking, or in an after-start
 *               flare, or the log ends: engine off by key-off or ECU power loss
 *               (bb_ig1 logged on the CYD tells which)
 *  vtec         VTEC solenoid on
 *  harsh_accel / harsh_brake   |a| from vss beyond the thresholds
 *  mark         a marker the driver set (Space)
 */
import type { LoadedDrive } from '../types';
import { valueAt } from './cursors';
import { G, longAccelFromArrays } from './dynamics';
import { GEAR_RPM_PER_KMH, gearFromArrays } from './gear';
import { align, driveSpan, getSeries, hasSeries, runs, searchSorted, uniformGrid, type Series } from './series';

export type AnalysisEventKind = 'near_stall' | 'stall' | 'dropout' | 'keyoff' | 'vtec' | 'harsh_accel' | 'harsh_brake' | 'mark';

export interface AnalysisEvent {
  t: number;
  /** end of the episode (same as t for a moment) */
  t1: number;
  kind: AnalysisEventKind;
  label: string;
  /** one short sentence, e.g. "throttle closed at clutch bite" */
  verdict: string;
  severity: 'info' | 'warn' | 'alarm';
  data: Record<string, number>;
}

export interface EventOptions {
  nearStallRpm?: number;     // 600
  standstillKmh?: number;    // 3
  rpmRun?: number;           // 400 (armed / engine running)
  rpmZero?: number;          // 50
  silenceS?: number;         // an ECU gap at least this long is a silence (1.0 s)
  movingKmh?: number;        // 5
  closedTps?: number;        // throttle "closed" at or below this % (3)
  biteMapRise?: number;      // kPa rise that marks a clutch bite (8)
  loadStepA?: number;        // ELD rise that marks an electrical load step (8 A)
  harshAccelMs2?: number;    // 2.5 m/s² (0.25 g)
  harshBrakeMs2?: number;    // 3.0 m/s² (0.31 g)
  harshMinS?: number;        // sustained at least this long (0.3 s)
  /** idle target when no idle_target channel is logged */
  idleTargetRpm?: number;    // 820
}

const D: Required<EventOptions> = {
  nearStallRpm: 600, standstillKmh: 3, rpmRun: 400, rpmZero: 50, silenceS: 1.0, movingKmh: 5, closedTps: 3,
  biteMapRise: 8, loadStepA: 8, harshAccelMs2: 2.5, harshBrakeMs2: 3.0, harshMinS: 0.3, idleTargetRpm: 820,
};

const fmt = (x: number) => Math.round(x).toLocaleString('en-US');

function windowStat(s: Series, t0: number, t1: number, f: 'min' | 'max'): { v: number; t: number } {
  const i0 = searchSorted(s.t, t0, 'left'); const i1 = searchSorted(s.t, t1, 'right');
  let best = NaN; let bt = NaN;
  for (let i = i0; i < i1; i++) {
    const v = s.v[i]!;
    if (Number.isNaN(v)) continue;
    if (Number.isNaN(best) || (f === 'min' ? v < best : v > best)) { best = v; bt = s.t[i]!; }
  }
  return { v: best, t: bt };
}

/** The verdict for a dip at a standstill, from what throttle, MAP, ELD and the
 *  idle target were doing (drive-idle-control.md §1). */
export function nearStallVerdict(ctx: {
  /** throttle at the start of the fall (the rpm peak before the minimum) */
  tpsAtFall: number;
  /** lowest throttle between the start of the fall and the minimum */
  tpsMinFall: number;
  mapRise: number; overshoot: number; eldStep: number;
}, o: Required<EventOptions> = D): string {
  const closed = (x: number) => !(x > o.closedTps);
  const bite = ctx.mapRise >= o.biteMapRise;
  const parts: string[] = [];
  if (bite && closed(ctx.tpsAtFall)) parts.push('throttle closed at clutch bite');
  else if (bite && closed(ctx.tpsMinFall)) parts.push('throttle blip released as the clutch bit');
  else if (bite) parts.push('clutch bite with the throttle open');
  else if (ctx.eldStep >= o.loadStepA) parts.push(`electrical load step (+${Math.round(ctx.eldStep)} A)`);
  else parts.push('rpm sag at standstill');
  if (ctx.overshoot >= 150) parts.push('rpm already falling from above target');
  if (bite && ctx.eldStep >= o.loadStepA) parts.push(`with a +${Math.round(ctx.eldStep)} A load step`);
  return parts.join(', ');
}

export function findNearStalls(drive: LoadedDrive, opts: EventOptions = {}): AnalysisEvent[] {
  const o = { ...D, ...opts };
  const rpm = getSeries(drive, 'rpm');
  if (!rpm.t.length) return [];
  const vssAt = align(getSeries(drive, 'vss'), rpm.t);
  const tps = getSeries(drive, 'tps'); const map = getSeries(drive, 'map'); const eld = getSeries(drive, 'eld');
  const target = getSeries(drive, 'idle_target');
  const low = (i: number) => {
    const r = rpm.v[i]!;
    return r < o.nearStallRpm && r >= o.rpmZero && !(vssAt[i]! > o.standstillKmh);
  };
  const out: AnalysisEvent[] = [];
  for (const [s, e] of runs(rpm.t.length, low)) {
    let im = s;
    for (let i = s; i < e; i++) if (rpm.v[i]! < rpm.v[im]!) im = i;
    const tm = rpm.t[im]!;
    const peak = windowStat(rpm, tm - 2.5, tm, 'max');
    const tgt = target.t.length ? valueAt(target, tm) : o.idleTargetRpm;
    const mapBefore = windowStat(map, tm - 2.5, tm, 'min').v;
    const mapAt = windowStat(map, tm - 0.3, tm + 0.3, 'max').v;
    const eldBefore = windowStat(eld, tm - 5, tm - 0.5, 'min').v;
    const eldAt = windowStat(eld, tm - 5, tm + 0.5, 'max').v;
    // steepest drop over ~0.35 s before the minimum
    let fall = 0;
    for (let i = searchSorted(rpm.t, tm - 2.5, 'left'); i <= im; i++) {
      const j = searchSorted(rpm.t, rpm.t[i]! + 0.35, 'left');
      if (j <= im && j > i) fall = Math.min(fall, (rpm.v[j]! - rpm.v[i]!) / (rpm.t[j]! - rpm.t[i]!));
    }
    const ctx = {
      tpsAtFall: Number.isNaN(peak.t) ? NaN : valueAt(tps, peak.t),
      tpsMinFall: windowStat(tps, Number.isNaN(peak.t) ? tm : peak.t, tm, 'min').v,
      mapRise: mapAt - mapBefore, overshoot: peak.v - tgt, eldStep: eldAt - eldBefore,
    };
    const minRpm = rpm.v[im]!;
    out.push({
      t: rpm.t[s]!, t1: rpm.t[e - 1]!, kind: 'near_stall', label: `NEAR STALL ${fmt(minRpm)} rpm`,
      verdict: nearStallVerdict({ ...ctx, mapRise: Number.isNaN(ctx.mapRise) ? 0 : ctx.mapRise,
        eldStep: Number.isNaN(ctx.eldStep) ? 0 : ctx.eldStep, overshoot: Number.isNaN(ctx.overshoot) ? 0 : ctx.overshoot }, o),
      severity: minRpm < 500 ? 'alarm' : 'warn',
      data: { tMin: tm, minRpm, peakRpm: peak.v, target: tgt, fallRpmS: fall, mapRise: ctx.mapRise, tpsAtFall: ctx.tpsAtFall,
        tpsAtMin: valueAt(tps, tm), eldStep: ctx.eldStep, below600S: rpm.t[e - 1]! - rpm.t[s]! },
    });
  }
  return out;
}

/** Stalls with the ECU answering, link dropouts, and key-off / power loss. */
export function findLinkEvents(drive: LoadedDrive, opts: EventOptions = {}): AnalysisEvent[] {
  const o = { ...D, ...opts };
  const rpm = getSeries(drive, 'rpm');
  if (!rpm.t.length) return [];
  const vss = getSeries(drive, 'vss');
  const ig1 = getSeries(drive, 'bb_ig1');
  const out: AnalysisEvent[] = [];
  let armed = false;
  for (let i = 0; i < rpm.t.length; i++) {
    const r = rpm.v[i]!;
    if (Number.isNaN(r)) continue;
    if (r > o.rpmRun) armed = true;
    // stall with the ECU still answering
    if (armed && r < o.rpmZero) {
      const before = windowStat(rpm, rpm.t[i]! - 3, rpm.t[i]! - 1e-6, 'max').v;
      out.push({ t: rpm.t[i]!, t1: rpm.t[i]!, kind: 'stall', label: 'STALL (rpm 0)',
        verdict: 'engine stopped with the ECU answering (rpm fell to 0)', severity: 'alarm',
        data: { rpmBefore: before } });
      armed = false;
      continue;
    }
    // a silence: no rpm read for silenceS
    const next = i + 1 < rpm.t.length ? rpm.t[i + 1]! : NaN;
    const gap = next - rpm.t[i]!;
    if (!armed || !(gap >= o.silenceS)) continue;
    const t0 = rpm.t[i]!; const before = r;
    let j = i + 1; while (j < rpm.t.length && Number.isNaN(rpm.v[j]!)) j++;
    if (j >= rpm.t.length) continue;
    const after = rpm.v[j]!;
    const vB = valueAt(vss, t0); const vA = valueAt(vss, rpm.t[j]!);
    const moving = vB > o.movingKmh && vA > o.movingKmh;
    const running = after > o.rpmRun && (after < before * 1.25 + 100 || moving);
    const data = { silenceS: gap, rpmBefore: before, rpmAfter: after, vssBefore: vB, vssAfter: vA };
    if (running) {
      out.push({ t: t0, t1: rpm.t[j]!, kind: 'dropout', label: 'LINK DROPOUT (engine running)',
        verdict: moving && !(after < before * 1.25 + 100)
          ? `ECU silent ${gap.toFixed(1)} s while driving; back at ${fmt(after)} rpm, ${Math.round(vA)} km/h`
          : `ECU silent ${gap.toFixed(1)} s; engine running when it answered (${fmt(before)} → ${fmt(after)} rpm)`,
        severity: gap >= 3 ? 'warn' : 'info', data });
    } else {
      const how = after <= o.rpmRun ? `back at ${fmt(after)} rpm (key-on / cranking)` : `back in an after-start flare at ${fmt(after)} rpm`;
      out.push({ t: t0, t1: rpm.t[j]!, kind: 'keyoff', label: 'ENGINE OFF (link lost)',
        verdict: `${ig1Verdict(ig1, t0, rpm.t[j]!)}; ${how} after ${gap.toFixed(1)} s`, severity: 'alarm', data });
      armed = after > o.rpmRun;
    }
  }
  // the log ends with the engine running
  const last = rpm.t.length - 1;
  if (armed && rpm.v[last]! > o.rpmRun) {
    const tEnd = rpm.t[last]!;
    out.push({ t: tEnd, t1: driveSpan(drive)[1], kind: 'keyoff', label: 'LOG ENDS (engine running)',
      verdict: `log ends at ${fmt(rpm.v[last]!)} rpm: ${ig1.t.length ? ig1Verdict(ig1, tEnd, Infinity) : 'key-off, cable pulled or ECU power loss'}`,
      severity: 'info', data: { rpmBefore: rpm.v[last]! } });
  }
  return out.sort((a, b) => a.t - b.t);
}

function ig1Verdict(ig1: Series, t0: number, t1: number): string {
  if (!ig1.t.length) return 'engine off: key-off or ECU power loss (no IG1 logged)';
  const lo = windowStat(ig1, t0, Math.min(t1, t0 + 3), 'min').v;
  if (Number.isNaN(lo)) return 'engine off: key-off or ECU power loss (no IG1 reading then)';
  return lo < 6 ? 'key-off (IG1 dropped)' : 'ECU power loss with IG1 on (main relay, IG1 feed or ECU ground)';
}

export function findVtec(drive: LoadedDrive): AnalysisEvent[] {
  const v = getSeries(drive, 'vtec');
  const rpm = getSeries(drive, 'rpm');
  const out: AnalysisEvent[] = [];
  for (const [s, e] of runs(v.t.length, (i) => v.v[i]! >= 0.5)) {
    const t0 = v.t[s]!; const t1 = e < v.t.length ? v.t[e]! : v.t[e - 1]!;
    const at = valueAt(rpm, t0);
    out.push({ t: t0, t1, kind: 'vtec', label: 'VTEC', severity: 'info',
      verdict: `VTEC on at ${fmt(at)} rpm for ${(t1 - t0).toFixed(1)} s`, data: { rpm: at, durationS: t1 - t0 } });
  }
  return out;
}

export function findHarsh(drive: LoadedDrive, opts: EventOptions = {}): AnalysisEvent[] {
  const o = { ...D, ...opts };
  if (!hasSeries(drive, 'vss')) return [];
  const [a0, a1] = driveSpan(drive);
  const t = uniformGrid(a0, a1, 10);
  const vss = align(getSeries(drive, 'vss'), t);
  const rpm = align(getSeries(drive, 'rpm'), t);
  const long = longAccelFromArrays(t, vss, { hz: 10 });
  const gear = gearFromArrays(t, rpm, vss, { ratios: GEAR_RPM_PER_KMH });
  const out: AnalysisEvent[] = [];
  const minN = Math.max(1, Math.round(o.harshMinS * 10));
  const scan = (pred: (i: number) => boolean, kind: 'harsh_accel' | 'harsh_brake') => {
    for (const [s, e] of runs(t.length, pred)) {
      if (e - s < minN) continue;
      let ip = s;
      for (let i = s; i < e; i++) if (Math.abs(long.a[i]!) > Math.abs(long.a[ip]!)) ip = i;
      const peak = long.a[ip]!;
      // the speed it started from: the extreme in the second before the threshold was crossed
      let kmh = long.v[s]! * 3.6;
      for (let i = Math.max(0, s - 10); i < s; i++) {
        const k = long.v[i]! * 3.6;
        if (!Number.isNaN(k) && (kind === 'harsh_brake' ? k > kmh : k < kmh)) kmh = k;
      }
      const g = gear.gear[ip]!;
      out.push({
        t: t[s]!, t1: t[e - 1]!, kind, severity: 'warn',
        label: kind === 'harsh_brake' ? 'HARSH BRAKE' : 'HARD ACCEL',
        verdict: kind === 'harsh_brake'
          ? `braking ${(-peak / G).toFixed(2)} g from ${Math.round(kmh)} km/h`
          : `accelerating ${(peak / G).toFixed(2)} g${g ? ` in ${['1st', '2nd', '3rd', '4th', '5th'][g - 1] ?? `gear ${g}`}` : ''} from ${Math.round(kmh)} km/h`,
        data: { peakMs2: peak, peakG: peak / G, kmh, gear: g },
      });
    }
  };
  scan((i) => long.a[i]! >= o.harshAccelMs2, 'harsh_accel');
  scan((i) => long.a[i]! <= -o.harshBrakeMs2, 'harsh_brake');
  return out;
}

export function findMarks(drive: LoadedDrive): AnalysisEvent[] {
  const rpm = getSeries(drive, 'rpm'); const vss = getSeries(drive, 'vss');
  return drive.events.filter((e) => e.kind === 'marker').map((e) => {
    const r = valueAt(rpm, e.t); const v = valueAt(vss, e.t);
    const ctx = [Number.isNaN(v) ? '' : `${Math.round(v)} km/h`, Number.isNaN(r) ? '' : `${fmt(r)} rpm`].filter(Boolean).join(', ');
    return { t: e.t, t1: e.t, kind: 'mark' as const, label: e.text || 'MARK', severity: 'info' as const,
      verdict: ctx ? `mark at ${ctx}` : 'mark', data: { rpm: r, vss: v } };
  });
}

/** Everything, in time order. */
export function findEvents(drive: LoadedDrive, opts: EventOptions = {}): AnalysisEvent[] {
  return [...findNearStalls(drive, opts), ...findLinkEvents(drive, opts), ...findVtec(drive), ...findHarsh(drive, opts),
    ...findMarks(drive)].sort((a, b) => a.t - b.t);
}
