/**
 * The simulator's recorded demo drive (parity with the desktop's
 * demo_drive_view in hondalog/ui/replaymodel.py): a bit over three minutes of
 * a simulated drive, so F4 Replay, F5 Power and F6 GPS have something to
 * show without a car or a recorded drive.
 *
 *   0-20 s     warm idle, 860 rpm
 *   20-23 s    the clutch bites with the throttle closed: rpm sags to ~470 (near-stall),
 *              throttle 0.6 s late
 *   23-41 s    1st, then 2nd at part throttle
 *   44-60.5 s  a 3rd-gear WOT pull, 2,000 -> 6,500 rpm, VTEC from ~5,500, a knock retard;
 *              the speed integrated from a D16Y8-like torque curve and the Power
 *              screen's default car (1,195 kg, 185/65 R14, 10 % loss, road load)
 *   62-150 s   4th / 5th cruise with corners; the K-line silent 72-78 s while moving
 *   150-166 s  braking to a stop (a hard stop at the end)
 *   176 s      a mark;  186 s  key off
 *
 * Plausible, not measured. The GPS track is integrated from the made-up speed
 * and heading from a made-up point in open water (FAKE_ORIGIN): no real place.
 */
import type { DriveInfo, EventNote, LoadedDrive } from '../../core/types';
import { GEAR_RPM_PER_KMH } from '../../core/analysis/gear';
import { VSS_SCALE } from '../../core/analysis/dynamics';
import { FAKE_ORIGIN } from './fixture';
import type { DriveSource } from './source';

export const DEMO_DRIVE = 'demo_drive';
const DT = 0.1;
const END = 195;

/** The WOT pull: 3rd gear, from 2,000 to 6,500 rpm (16.5 s with the car below). */
export const DEMO_PULL = { t0: 44, t1: 60.5, gear: 3, rpm0: 2000, rpm1: 6500 };
// engine torque at the flywheel (N·m), roughly a D16Y8's; VTEC lifts it from 5,500
const TORQUE: [number, number][] = [[2000, 118], [2500, 122], [3000, 127], [3500, 131], [4000, 134], [4500, 136], [5000, 137], [5500, 143], [6000, 141], [6500, 132]];
const MASS = 1195, LOSS = 0.9, ROAD_A = 179, ROAD_B = 0.384;

function rng(seed: number) {
  let s = seed >>> 0 || 1;
  const u = () => { s = (s * 1664525 + 1013904223) >>> 0; return (s + 0.5) / 4294967296; };
  return { u, normal: (sd: number) => sd * Math.sqrt(-2 * Math.log(u())) * Math.cos(2 * Math.PI * u()) };
}

/** Linear interpolation through (xs, ys). */
function interp(x: number, xs: readonly number[], ys: readonly number[]): number {
  if (x <= xs[0]!) return ys[0]!;
  for (let i = 1; i < xs.length; i++) {
    if (x <= xs[i]!) {
      const f = (x - xs[i - 1]!) / (xs[i]! - xs[i - 1]! || 1);
      return ys[i - 1]! + f * (ys[i]! - ys[i - 1]!);
    }
  }
  return ys[ys.length - 1]!;
}

const per = (gear: number) => (gear > 0 ? GEAR_RPM_PER_KMH[gear - 1]! : 0);
const KMH_PULL0 = DEMO_PULL.rpm0 / per(3);
const KMH_PULL1 = DEMO_PULL.rpm1 / per(3);

// true speed (km/h) and gear over the drive
const SPEED_T = [0, 22.4, 26, 31, 33, 41, 44, DEMO_PULL.t1, 62.5, 74, 90, 100, 104, 130, 150, 160, 163, 166, END];
const SPEED_V = [0, 0, 12, 24, 22, 40, KMH_PULL0, KMH_PULL1, 128, 112, 96, 94, 92, 95, 90, 60, 20, 0, 0];
function gearAt(t: number): number {
  if (t < 22.4) return 0;
  if (t < 31) return 1;
  if (t < 33) return 0;          // shifting
  if (t < 41) return 2;
  if (t < 44) return 0;
  if (t < DEMO_PULL.t1) return 3;  // the pull
  if (t < 62.5) return 0;
  if (t < 100) return 4;
  if (t < 104) return 0;
  if (t < 160) return 5;
  return 0;                      // clutch in to the stop
}
// rpm across the shifts: [t0, t1, rpm at t1]
const SHIFTS: [number, number, number][] = [[31, 33, 1700], [41, 44, DEMO_PULL.rpm0], [DEMO_PULL.t1, 62.5, 128 * per(4)], [100, 104, 92 * per(5)], [160, 166, 900]];
// steering: [t0, t1, deg/s] (+ = right); a hard left at 138 s
const TURNS: [number, number, number][] = [[64, 71, -4], [84, 92, 5], [120, 126, 3], [138, 141, -11]];

let cache: LoadedDrive | null = null;

/** The demo drive (built once). */
export function demoDrive(): LoadedDrive {
  if (cache) return cache;
  const r = rng(7);
  const n = Math.round(END / DT);
  const t = Array.from({ length: n }, (_, i) => Math.round(i * DT * 1000) / 1000);
  const kmh = t.map((x) => interp(x, SPEED_T, SPEED_V));
  // the pull: speed from the torque curve against the car's mass and road load
  {
    const radPerM = per(DEMO_PULL.gear) * (2 * Math.PI / 60) * 3.6;
    let v = KMH_PULL0 / 3.6;
    t.forEach((x, i) => {
      if (x < DEMO_PULL.t0 || x >= DEMO_PULL.t1) return;
      kmh[i] = Math.min(v * 3.6, KMH_PULL1);
      const force = interp(v * 3.6 * per(DEMO_PULL.gear), TORQUE.map((p) => p[0]), TORQUE.map((p) => p[1])) * radPerM * LOSS - (ROAD_A + ROAD_B * v * v);
      v += (force / MASS) * DT;
    });
  }
  const gear = t.map(gearAt);
  const rpm = t.map((x, i) => (gear[i]! > 0 ? kmh[i]! * per(gear[i]!) : 860));
  for (const [a, b, next] of SHIFTS) {
    const i0 = t.findIndex((x) => x >= a);
    const i1 = t.findIndex((x) => x >= b);
    const start = rpm[Math.max(0, i0 - 1)]!;
    for (let i = i0; i < i1; i++) rpm[i] = start + ((next - start) * (i - i0)) / Math.max(1, i1 - i0 - 1);
  }
  t.forEach((x, i) => {
    if (x >= 20 && x < 23) rpm[i] = 860 - 390 * Math.sin(Math.PI * Math.min(1, Math.max(0, (x - 20) / 2.6)));   // the clutch bites, throttle closed
    else if (x >= 23 && x < 26) rpm[i] = Math.max(rpm[i]!, 1500);
    else if (x >= 166) rpm[i] = 860;
    rpm[i] = rpm[i]! + r.normal(6);
  });
  const tps = t.map((x) => {
    if (x >= 20.6 && x < 31) return 22;                              // 0.6 s after the fall began
    if (x >= 33 && x < 41) return 35;
    if (x >= DEMO_PULL.t0 && x < DEMO_PULL.t1) return 100;           // WOT
    if (x >= 62.5 && x < 150) return x >= 104 && x < 112 ? 45 : 18 + 6 * Math.sin(x / 7);
    return 0;
  });
  const map = t.map((x, i) => (x >= 150 && x < 168 ? 22 : Math.min(99, Math.max(18, 22 + 0.78 * tps[i]! + r.normal(0.6)))));
  const vtec = t.map((x, i) => (x >= DEMO_PULL.t0 && x < DEMO_PULL.t1 && rpm[i]! > 5500 ? 1 : 0));
  const inj = t.map((x, i) => (x >= 150 && x < 160 ? 0 : (map[i]! > 25 ? 1.2 + map[i]! * 0.09 : 2.6) + r.normal(0.03)));
  const brake = t.map((x) => (x >= 150 && x < 168 ? 1 : 0));
  const ignLimit = t.map((x, i) => {
    let v = map[i]! > 44 ? Math.min(4 + 1.3 * (map[i]! - 41), 28) : 4;
    if (x >= 50 && x < 52) v -= 9;                                   // timing pulled under load
    return Math.round(v);
  });
  const ecuUp = t.map((x) => !(x > 72 && x < 78) && x < 186);          // a dropout while moving; key off at 186 s

  // ECU fast block every 0.1 s, slow block once a second
  const series: LoadedDrive['series'] = {};
  const put = (name: string, idx: number[], v: (i: number) => number) => {
    series[name] = { t: Float64Array.from(idx.map((i) => t[i]!)), v: Float64Array.from(idx.map(v)) };
  };
  const fast = t.map((_, i) => i).filter((i) => ecuUp[i]);
  const slow = fast.filter((i) => i % 10 === 0);
  put('rpm', fast, (i) => Math.round(rpm[i]!));
  put('vss', fast, (i) => Math.max(0, Math.round(kmh[i]! / VSS_SCALE)));
  put('tps', fast, (i) => Math.round(tps[i]! * 10) / 10);
  put('map', fast, (i) => Math.round(map[i]! * 10) / 10);
  put('inj', fast, (i) => Math.round(Math.max(0, inj[i]!) * 100) / 100);
  put('ign', fast, (i) => Math.round((12 + rpm[i]! / 300 - map[i]! * 0.05) * 2) / 2);
  put('stft', fast, (i) => (tps[i]! > 80 ? 0 : Math.round(r.normal(2) * 10) / 10 + 2));
  put('ltft', fast, () => 6.2);
  put('o2', fast, (i) => (tps[i]! > 80 ? 0.88 : 0.45 + 0.4 * Math.sign(Math.sin(t[i]! * 2.7))));
  put('vtec', fast, (i) => vtec[i]!);
  put('brake', fast, (i) => brake[i]!);
  put('idle_target', slow, (i) => (t[i]! < 120 ? 864 : 840));
  const iac = (i: number) => 90 + 8 * Math.sin(t[i]! / 9) + (t[i]! >= 21 && t[i]! < 24 ? 14 : 0);
  put('iac_cmd', slow, (i) => Math.round(iac(i)));
  put('iacv', slow, (i) => Math.round((iac(i) - 70) * 0.9 * 10) / 10);
  put('ect', slow, (i) => Math.round(Math.min(70 + t[i]! / 30, 82)));
  put('iat', slow, () => 24);
  put('batt', slow, () => Math.round((14.2 + r.normal(0.03)) * 100) / 100);
  put('eld', slow, (i) => (Math.sin(t[i]! / 13) > 0.6 ? 24 : 18));
  put('alt_fr', slow, () => 38);
  put('ign_limit', slow, (i) => ignLimit[i]!);
  put('knock', slow, (i) => Math.round(1 + 0.104 * (rpm[i]! / 1000) ** 2.9));
  put('closed_loop', slow, (i) => (tps[i]! < 50 && inj[i]! > 0 ? 1 : 0));
  put('ac_sw', slow, () => 0);
  put('fuel_pump', slow, () => 1);

  // GPS once a second, integrated from the true speed and the heading
  const g = { t: [] as number[], lat: [] as number[], lon: [] as number[], speed: [] as number[], heading: [] as number[] };
  let heading = 40, east = 0, north = 0;
  t.forEach((x, i) => {
    for (const [a, b, rate] of TURNS) if (x >= a && x < b) heading = (heading + rate * DT + 360) % 360;
    const d = (kmh[i]! / 3.6) * DT;
    east += d * Math.sin((heading * Math.PI) / 180);
    north += d * Math.cos((heading * Math.PI) / 180);
    if (i % 10 === 0) {
      g.t.push(x);
      g.lat.push(FAKE_ORIGIN.lat + north / 111_320);
      g.lon.push(FAKE_ORIGIN.lon + east / (111_320 * Math.cos((FAKE_ORIGIN.lat * Math.PI) / 180)));
      g.speed.push(Math.round(kmh[i]! * 10) / 10);
      g.heading.push(Math.round(heading * 10) / 10);
    }
  });
  series.gps_speed = { t: Float64Array.from(g.t), v: Float64Array.from(g.speed) };
  series.gps_sats = { t: Float64Array.from(g.t), v: Float64Array.from(g.t.map(() => 11)) };

  const info: DriveInfo = {
    id: -1, name: DEMO_DRIVE, kind: 'drive', startedUnix: new Date(2026, 10, 26, 23, 0, 0).getTime() / 1000,
    durationS: END, rows: fast.length, status: 'ok', mapVersion: 'p2p-guess7', ecuId: '37820-P2P-A01',
    reason: 'built-in demo drive (simulated)',
  };
  cache = {
    info, map: null, series, reads: [],
    events: [{ t: 72, kind: 'link_error', text: 'no reply' }, { t: 176, kind: 'marker', text: 'MARK 1' }],
    gps: {
      t: Float64Array.from(g.t), lat: Float64Array.from(g.lat), lon: Float64Array.from(g.lon),
      speedKmh: Float64Array.from(g.speed), heading: Float64Array.from(g.heading),
    },
  };
  return cache;
}

/**
 * The drive store with the demo drive in its list (the simulator's source).
 * The demo drive cannot be deleted or kept; its notes live in memory only.
 */
export function withDemoDrive(store: DriveSource): DriveSource {
  const notes: Record<string, EventNote> = {};
  const isDemo = (name: string) => name === DEMO_DRIVE;
  const src: DriveSource = {
    listDrives: async () => [...(await store.listDrives()).filter((d) => !isDemo(d.name)), demoDrive().info],
    loadDrive: (name) => (isDemo(name) ? Promise.resolve(demoDrive()) : store.loadDrive(name)),
  };
  if (store.deleteDrive) {
    const del = store.deleteDrive.bind(store);
    src.deleteDrive = async (name, opts) => {
      if (isDemo(name)) throw new Error('the demo drive is built in');
      await del(name, opts);
    };
  }
  if (store.setKeep) {
    const keep = store.setKeep.bind(store);
    src.setKeep = async (name, on) => {
      if (isDemo(name)) throw new Error('the demo drive is built in');
      await keep(name, on);
    };
  }
  if (store.importDb) src.importDb = store.importDb.bind(store);
  if (store.importFile) src.importFile = store.importFile.bind(store);
  if (store.loadEventNotes && store.saveEventNote) {
    const load = store.loadEventNotes.bind(store);
    const save = store.saveEventNote.bind(store);
    src.loadEventNotes = async (drive) => (isDemo(drive) ? { ...notes } : load(drive));
    src.saveEventNote = async (drive, key, change) => {
      if (!isDemo(drive)) return save(drive, key, change);
      const old = notes[key] ?? { note: '', verdict: '', kind: change.kind ?? '', t: change.t ?? null };
      const next = { ...old, ...(change.note !== undefined ? { note: change.note } : {}), ...(change.verdict !== undefined ? { verdict: change.verdict } : {}) };
      if (!next.note && !next.verdict) delete notes[key]; else notes[key] = next;
    };
  }
  return src;
}
