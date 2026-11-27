/**
 * A synthetic drive for tests and the screens' demo: idle with a near-stall,
 * a short 1st-gear launch, a full-throttle pull in 2nd (engine torque =
 * 0.91 × the D16Y8 reference of core/analysis, so the dyno must find that), a cruise with a
 * GPS gap and a link dropout, a part-throttle pull in 3rd, then a stall.
 *
 * Positions are made up around an arbitrary origin: no real coordinates.
 */
import type { DriveInfo, LoadedDrive } from '../../core/types';
import { DEFAULT_VEHICLE, effectiveMass, G as GRAV, GEAR_RPM_PER_KMH, overallRatio, referenceTorque as refTorque } from '../../core/analysis';
import { offset } from './geo/proj';

export const SYNTH_ORIGIN: [number, number] = [40.0, -100.0];
export const SYNTH_TORQUE_K = 0.91;

export interface SynthDrive extends LoadedDrive {
  /** where the interesting things are, for tests */
  marks: { nearStallT: number; wotT0: number; wotT1: number; partT0: number; stallT: number; gpsGap: [number, number]; dropoutT: number };
}

export function synthDrive(name = 'drive_2026_11_26_2251'): SynthDrive {
  const dt = 0.1;
  const P = DEFAULT_VEHICLE;
  const inp = { vssScale: P.vssScale, massKg: P.massKg, rollN: P.roadA, aeroK: P.roadB };
  const r = P.tyreRadiusM;
  const eta = P.eta;
  const ratioOf = (g: number) => overallRatio(g, P);
  const mEff = (g: number) => effectiveMass(g, P);

  const T: number[] = [], RPM: number[] = [], VSS: number[] = [], TPS: number[] = [], MAP: number[] = [];
  let t = 0, kmh = 0; // ECU km/h
  const push = (rpm: number, tps: number, map: number) => {
    T.push(+t.toFixed(3)); RPM.push(rpm); VSS.push(kmh); TPS.push(tps); MAP.push(map);
    t += dt;
  };
  const marks = { nearStallT: 0, wotT0: 0, wotT1: 0, partT0: 0, stallT: 0, gpsGap: [0, 0] as [number, number], dropoutT: 0 };

  // idle, a near-stall dip at 10 s
  while (t < 30 - 1e-9) {
    const dip = t >= 10 && t < 11.2 ? 280 * Math.sin(((t - 10) / 1.2) * Math.PI) : 0;
    if (dip > 279) marks.nearStallT = t;
    push(800 - dip + 5 * Math.sin(t * 7), 8, 30);
  }
  // a short launch in 1st, part throttle
  kmh = 7.5;
  for (let k = 0; k < 16; k++) { kmh += (1.4 * 3.6 * dt) / inp.vssScale; push(kmh * GEAR_RPM_PER_KMH[0]!, 35, 60); }
  // the shift, then easy throttle in 2nd up to 2,770 rpm
  for (let k = 0; k < 6; k++) push(2100 - k * 150, 5, 35);
  const accel = (g: number, k: number, frac: number) => {
    const rpm = kmh * GEAR_RPM_PER_KMH[g - 1]!;
    const v = (kmh * inp.vssScale) / 3.6;
    const F = (frac * k * refTorque(rpm) * ratioOf(g) * eta) / r - inp.rollN - inp.aeroK * v * v - inp.massKg * GRAV * 0;
    return F / mEff(g);
  };
  while (kmh * GEAR_RPM_PER_KMH[1]! < 2770) {
    push(kmh * GEAR_RPM_PER_KMH[1]!, 12, 50);
    kmh += (accel(2, SYNTH_TORQUE_K, 0.5) * 3.6 * dt) / inp.vssScale;
  }
  // the WOT pull in 2nd from 2,770 rpm
  marks.wotT0 = t;
  while (kmh * GEAR_RPM_PER_KMH[1]! < 5411) {
    push(kmh * GEAR_RPM_PER_KMH[1]!, 84, 98);
    kmh += (accel(2, SYNTH_TORQUE_K, 1) * 3.6 * dt) / inp.vssScale;
  }
  marks.wotT1 = t;
  // lift, shift to 3rd, cruise
  for (let k = 0; k < 8; k++) push(5411 - k * 300, 3, 25);
  const cruise0 = t;
  while (t < cruise0 + 20) push(kmh * GEAR_RPM_PER_KMH[2]! + 10 * Math.sin(t), 14, 45);
  marks.gpsGap = [cruise0 + 5, cruise0 + 17];
  marks.dropoutT = cruise0 + 10;
  // a part-throttle pull in 3rd from 3,500 rpm
  marks.partT0 = t;
  while (kmh * GEAR_RPM_PER_KMH[2]! < 4700) {
    push(kmh * GEAR_RPM_PER_KMH[2]!, 50, 72);
    kmh += (accel(3, SYNTH_TORQUE_K, 0.7) * 3.6 * dt) / inp.vssScale;
  }
  // coast down and stall
  while (kmh > 0) {
    kmh = Math.max(0, kmh - 2.2 * 3.6 * dt);
    push(Math.max(800, kmh * GEAR_RPM_PER_KMH[2]!), 0, 28);
  }
  for (let k = 0; k < 30; k++) push(800, 8, 30);
  marks.stallT = t;
  for (let k = 0; k < 10; k++) push(Math.max(0, 800 - k * 200), 8, 100);
  for (let k = 0; k < 20; k++) push(0, 8, 100);

  // GPS at 1 Hz along a gently curving road, a gap during the cruise
  const g = { t: [] as number[], lat: [] as number[], lon: [] as number[], speedKmh: [] as number[], heading: [] as number[] };
  let lat = SYNTH_ORIGIN[0], lon = SYNTH_ORIGIN[1], heading = 60, k = 0;
  for (let s = 0; s <= T[T.length - 1]!; s += 1) {
    while (k + 1 < T.length && T[k + 1]! <= s) k++;
    const v = (VSS[k]! * inp.vssScale) / 3.6;
    heading = (heading + (s > 40 ? 1.5 : 0.3)) % 360;
    [lat, lon] = offset(lat, lon, v * Math.sin((heading * Math.PI) / 180), v * Math.cos((heading * Math.PI) / 180));
    if (s >= marks.gpsGap[0] && s < marks.gpsGap[1]) continue;
    g.t.push(s); g.lat.push(lat); g.lon.push(lon); g.speedKmh.push(VSS[k]! * inp.vssScale); g.heading.push(heading);
  }

  const f = (a: number[]) => Float64Array.from(a);
  const tt = f(T);
  const slow = (fn: (i: number) => number) => {
    const ts: number[] = [], vs: number[] = [];
    for (let i = 0; i < T.length; i += 10) { ts.push(T[i]!); vs.push(fn(i)); }
    return { t: f(ts), v: f(vs) };
  };
  const info: DriveInfo = {
    id: 1, name, kind: 'drive', startedUnix: 1795753477, durationS: T[T.length - 1]!, rows: T.length,
    status: 'ok', mapVersion: 'test', ecuId: 'synthetic',
  };
  return {
    info, map: null, reads: [],
    series: {
      rpm: { t: tt, v: f(RPM) },
      vss: { t: tt, v: f(VSS) },
      tps: { t: tt, v: f(TPS) },
      map: { t: tt, v: f(MAP) },
      inj: { t: tt, v: f(MAP.map((m) => 0.6 + m * 0.07)) },
      ign: { t: tt, v: f(MAP.map((m, i) => 40 - m * 0.25 + RPM[i]! * 0.001)) },
      stft: slow((i) => (MAP[i]! > 90 ? 0 : 2 * Math.sin(i / 50))),
      ltft: slow(() => 1.5),
      iat: slow(() => 22),
      baro: slow(() => 100.3),
    },
    events: [
      { t: 5, kind: 'marker', text: 'MARK 1' },
      { t: marks.dropoutT, kind: 'dropout', text: 'LINK DROPOUT' },
      { t: marks.stallT + 0.5, kind: 'stall', text: 'rpm dropped to 0' },
    ],
    gps: { t: f(g.t), lat: f(g.lat), lon: f(g.lon), speedKmh: f(g.speedKmh), heading: f(g.heading) },
    marks,
  };
}

export function synthDriveList(): DriveInfo[] {
  const d = synthDrive().info;
  return [
    d,
    { ...d, id: 2, name: 'drive_2026_11_26_2224', startedUnix: d.startedUnix - 1700, durationS: 1500 },
    { ...d, id: 3, name: 'stall_20261126_223941', kind: 'stall', durationS: 40, reason: 'rpm' },
  ];
}
