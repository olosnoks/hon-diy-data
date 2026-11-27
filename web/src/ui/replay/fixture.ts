/**
 * Synthetic drives shaped like the first real drives of 2026-11-26 (Civic EX,
 * D16Y8, manual): idle, clutch-bite near-stalls at a standstill, city and
 * arterial driving, a hard stop, a WOT 2nd-gear pull with VTEC, K-line
 * dropouts while moving, a mark, and a key-off at home.
 *
 * Nothing in here is real: the GPS track is integrated from the made-up speed
 * and heading and starts at a made-up point in the middle of the ocean
 * (FAKE_ORIGIN). Used by the screens until the drive store (#4) is wired in,
 * and by the tests.
 */
import type { DriveInfo, DriveStore, LoadedDrive } from '../../core/types';
import { GEAR_RPM_PER_KMH } from '../../core/analysis/gear';
import { VSS_SCALE as VSS_TRUE } from '../../core/analysis/dynamics';

/** Deliberately not a real place: open water, no roads. */
export const FAKE_ORIGIN = { lat: 0.5, lon: -25.0 };

type Turn = [number, number, number];          // start, end (s, relative to the segment), deg/s (+ = right)
export type Seg =
  | { k: 'idle'; d: number }
  | { k: 'dip'; d: number; at: number; min: number; tps: 'none' | 'late' | 'blip' }
  | { k: 'accel'; d: number; to: number; tps: number }
  | { k: 'cruise'; d: number; tps?: number; turns?: Turn[] }
  | { k: 'brake'; to: number; g: number }
  | { k: 'creep'; d: number; to: number };

export interface Script {
  name: string;
  started: Date;
  segs: Seg[];
  /** [t0, t1): no ECU reads (K-line silent) */
  gaps: [number, number][];
  /** [t0, t1): no GPS fixes */
  gpsGaps: [number, number][];
  marks: [number, string][];
  heading0: number;
  ect0: number;
  origin?: { lat: number; lon: number };
}

const IDLE = 830;

function rng(seed: number) {
  let s = seed >>> 0 || 1;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
}

const bump = (x: number, w: number) => Math.exp(-(x * x) / (2 * w * w));

interface Tick { t: number; rpm: number; vss: number; tps: number; map: number; brake: number; turn: number; psp: number; target: number }

/** Run a script at 100 Hz of simulated time. */
function simulate(sc: Script): Tick[] {
  const out: Tick[] = [];
  const dt = 0.01;
  let t = 0, vss = 0, rpm = IDLE, gear = 0;
  const rnd = rng(sc.name.length * 977);
  for (const seg of sc.segs) {
    const t0 = t;
    const v0 = vss;
    const end = seg.k === 'brake' ? Infinity : t0 + seg.d;
    while (t < end) {
      const x = t - t0;
      let tps = 0, map = 30, brake = 0, turn = 0, psp = 0;
      let target = 864;
      const idleRpm = IDLE + 12 * Math.sin(t * 1.7) + 6 * (rnd() - 0.5);
      if (seg.k === 'idle') {
        rpm = idleRpm + (rpm - idleRpm) * Math.exp(-dt / 1.2);     // rev-hang after a stop
        vss = 0; gear = 0;
      } else if (seg.k === 'dip') {
        const rel = x - seg.at;
        let dip = (IDLE - seg.min) * bump(rel, 0.35);
        if (seg.tps === 'blip') {
          const blip = x >= seg.at - 2.6 && x < seg.at - 1.9;
          tps = blip ? 12.9 : rel > -0.2 ? 10.6 : 0;
          dip -= 590 * bump(rel + 2.0, 0.35);                         // the blip's rev-up
        } else if (seg.tps === 'late') {
          tps = rel > 0.6 ? 10.6 : 0;
        }
        rpm = idleRpm - dip;
        vss = rel > 0 ? Math.min(6, 6 * rel / Math.max(0.5, seg.d - seg.at)) : 0;
        map = 26 + 28 * bump(rel, 0.5) + tps * 0.4;
        gear = rel > 0 ? 1 : 0;
      } else if (seg.k === 'accel') {
        vss = v0 + (seg.to - v0) * Math.min(1, x / seg.d);
        tps = seg.tps;
        map = 30 + 0.68 * tps;
        const ups = tps >= 80 ? [37, 71, 108, 140] : [18, 34, 52, 75];
        gear = 1 + ups.filter((u) => vss >= u).length;
        rpm = Math.max(1100, vss * GEAR_RPM_PER_KMH[gear - 1]!);
      } else if (seg.k === 'cruise') {
        tps = seg.tps ?? 12;
        map = 34 + 0.5 * tps + 2 * Math.sin(t * 0.3);
        vss = v0 + 1.5 * Math.sin(x * 0.15);
        gear = 1 + [18, 34, 52, 75].filter((u) => vss >= u).length;
        rpm = vss * GEAR_RPM_PER_KMH[gear - 1]!;
        for (const [a, b, r] of seg.turns ?? []) if (x >= a && x < b) turn = r;
      } else if (seg.k === 'brake') {
        brake = 1; tps = 0; map = 22;
        vss = Math.max(seg.to, vss - seg.g * 9.81 * 3.6 * dt);
        if (vss < 20) gear = 0;                                        // clutch in
        rpm = gear ? vss * GEAR_RPM_PER_KMH[gear - 1]! : Math.max(idleRpm + 250, rpm - 900 * dt);
        if (vss <= seg.to + 1e-9) { t += dt; out.push({ t, rpm, vss, tps, map, brake, turn, psp, target }); break; }
      } else if (seg.k === 'creep') {
        vss = v0 + (seg.to - v0) * Math.min(1, x / seg.d);
        tps = vss > 0.5 ? 3 : 0; map = 33; psp = 1;
        rpm = 900 + 20 * Math.sin(t);
        gear = 1;
      }
      out.push({ t, rpm, vss, tps, map, brake, turn, psp, target });
      t += dt;
    }
  }
  return out;
}

function at(ticks: Tick[], t: number): Tick {
  const i = Math.min(ticks.length - 1, Math.max(0, Math.round(t / 0.01)));
  return ticks[i]!;
}

export function buildDrive(sc: Script): LoadedDrive {
  const ticks = simulate(sc);
  const dur = ticks[ticks.length - 1]!.t;
  const rnd = rng(sc.name.length * 31 + 7);
  const inGap = (t: number, gaps: [number, number][]) => gaps.some(([a, b]) => t >= a && t < b);
  const fast: Record<string, number[]> = {};
  const fastT: number[] = [];
  const slow: Record<string, number[]> = {};
  const slowT: number[] = [];
  const push = (rec: Record<string, number[]>, k: string, v: number) => { (rec[k] ??= []).push(v); };
  let iac = 95, n = 0;
  for (let t = 0.05; t < dur; t += 0.1) {
    const tt = t + (rnd() - 0.5) * 0.02;
    if (inGap(tt, sc.gaps)) continue;
    const k = at(ticks, tt);
    const ect = Math.min(71, sc.ect0 + tt / 40);
    // idle air: slow feedback, a kick when MAP rises, taken away as rpm falls (drive-idle-control.md)
    iac += k.vss < 1 && k.rpm < k.target - 50 ? 0.25 : k.rpm > k.target + 100 ? -0.2 : 0;
    iac = Math.max(70, Math.min(140, iac + (k.map > 45 && k.vss < 3 ? 1.5 : 0)));
    fastT.push(tt);
    push(fast, 'rpm', Math.round(k.rpm));
    push(fast, 'vss', Math.max(0, Math.round(k.vss / VSS_TRUE)));
    push(fast, 'tps', Math.round(k.tps * 10) / 10);
    push(fast, 'map', Math.round(k.map * 10) / 10);
    push(fast, 'idle_target', k.target);
    push(fast, 'iac_cmd', Math.round(iac));
    push(fast, 'iacv', Math.round(iac * 0.14 * 10) / 10);
    const wot = k.tps > 80;
    push(fast, 'o2', wot ? 0.88 : 0.45 + 0.35 * Math.sin(tt * 6.3));
    push(fast, 'inj', Math.round((0.6 + 0.065 * k.map) * 100) / 100);
    push(fast, 'ign', Math.round((12 + k.rpm / 280 - k.map / 12) * 2) / 2);
    push(fast, 'stft', Math.round((wot ? 0 : 4 * Math.sin(tt * 0.9) + (rnd() - 0.5) * 2) * 10) / 10);
    push(fast, 'vtec', k.rpm > 5200 && wot ? 1 : 0);
    push(fast, 'brake', k.brake);
    push(fast, 'psp', k.psp);
    if (n++ % 10 === 0) {
      slowT.push(tt);
      push(slow, 'ect', Math.round(ect));
      push(slow, 'iat', 27);
      push(slow, 'batt', Math.round((k.rpm > 1500 ? 14.2 : 14.0) * 10) / 10);
      push(slow, 'alt_fr', k.rpm > 1500 ? 41 : 35);
      push(slow, 'eld', k.vss < 1 ? 24 : 17);
      push(slow, 'ltft', 8.6);
      push(slow, 'ac_sw', 0);
      push(slow, 'fuel_pump', 1);
      push(slow, 'closed_loop', wot ? 0 : 1);
    }
  }
  const series: LoadedDrive['series'] = {};
  const ft = Float64Array.from(fastT), st = Float64Array.from(slowT);
  for (const [k, v] of Object.entries(fast)) series[k] = { t: ft, v: Float64Array.from(v) };
  for (const [k, v] of Object.entries(slow)) series[k] = { t: st, v: Float64Array.from(v) };

  // GPS: 1 Hz, position integrated from true speed and heading
  const origin = sc.origin ?? FAKE_ORIGIN;
  const g = { t: [] as number[], lat: [] as number[], lon: [] as number[], speed: [] as number[], heading: [] as number[] };
  let lat = origin.lat, lon = origin.lon, heading = sc.heading0, last = 0;
  for (const k of ticks) {
    heading = (heading + k.turn * 0.01 + 360) % 360;
    const d = (k.vss / 3.6) * 0.01;
    lat += (d * Math.cos(heading * Math.PI / 180)) / 111_320;
    lon += (d * Math.sin(heading * Math.PI / 180)) / (111_320 * Math.cos(lat * Math.PI / 180));
    if (k.t - last >= 1 - 1e-9) {
      last = k.t;
      if (inGap(k.t, sc.gpsGaps)) continue;
      g.t.push(k.t + 0.3); g.lat.push(lat); g.lon.push(lon); g.speed.push(k.vss); g.heading.push(heading);
    }
  }
  const events: LoadedDrive['events'] = sc.marks.map(([t, text]) => ({ t, kind: 'marker', text }));
  for (const [a] of sc.gaps) events.push({ t: a, kind: 'link_error', text: 'no reply' });
  events.sort((a, b) => a.t - b.t);
  const startedUnix = sc.started.getTime() / 1000;
  return {
    info: {
      id: 0, name: sc.name, kind: 'drive', startedUnix, durationS: Math.round(dur * 10) / 10,
      rows: ft.length, status: 'ok', mapVersion: 'p2p-guess7', ecuId: '37820-P2P-A01',
    },
    map: null,
    series,
    reads: [],
    events,
    gps: {
      t: Float64Array.from(g.t), lat: Float64Array.from(g.lat), lon: Float64Array.from(g.lon),
      speedKmh: Float64Array.from(g.speed), heading: Float64Array.from(g.heading),
    },
  };
}

/** Drive B of the night: 2251. */
export const SCRIPT_B: Script = {
  name: 'drive_2026_11_26_2251',
  started: new Date(2026, 10, 26, 22, 51, 0),
  heading0: 30, ect0: 64,
  segs: [
    { k: 'idle', d: 18 },
    { k: 'dip', d: 6, at: 3.5, min: 554, tps: 'late' },                 // throttle 0.6 s late
    { k: 'accel', d: 16, to: 50, tps: 35 },
    { k: 'cruise', d: 50, turns: [[26, 32, 15], [44, 48, -20]] },
    { k: 'brake', to: 0, g: 0.5 },                                     // a hard stop
    { k: 'idle', d: 12 },
    { k: 'accel', d: 3, to: 14, tps: 30 },
    { k: 'accel', d: 15, to: 72, tps: 100 },                           // WOT 2nd, VTEC
    { k: 'cruise', d: 70, tps: 15, turns: [[30, 36, -9], [50, 55, 10]] },
    { k: 'brake', to: 0, g: 0.14 },
    { k: 'idle', d: 18 },
    { k: 'dip', d: 6, at: 4, min: 440, tps: 'blip' },                  // blip lifted at the bite
    { k: 'creep', d: 8, to: 4 },
    { k: 'brake', to: 0, g: 0.05 },
    { k: 'idle', d: 12 },
  ],
  gaps: [[150, 155.7], [172, 180.1]],
  gpsGaps: [[118, 124]],
  marks: [[190, 'MARK 1']],
};

/** Drive A of the night: 2224, shorter, ends at the gas station. */
export const SCRIPT_A: Script = {
  name: 'drive_2026_11_26_2224',
  started: new Date(2026, 10, 26, 22, 24, 37),
  heading0: 80, ect0: 66,
  segs: [
    { k: 'idle', d: 20 },
    { k: 'idle', d: 25 },
    { k: 'dip', d: 6, at: 2, min: 546, tps: 'none' },                  // never on the gas
    { k: 'accel', d: 14, to: 44, tps: 30 },
    { k: 'cruise', d: 40, turns: [[18, 24, -15]] },
    { k: 'accel', d: 8, to: 66, tps: 40 },
    { k: 'cruise', d: 30, turns: [[10, 15, 11]] },
    { k: 'brake', to: 0, g: 0.2 },
    { k: 'idle', d: 15 },
  ],
  gaps: [[20, 35]],                                                    // cable knocked out, engine ran
  gpsGaps: [],
  marks: [],
};

let cache: LoadedDrive[] | null = null;

/** The two synthetic drives, newest first. */
export function fixtureDrives(): LoadedDrive[] {
  cache ??= [buildDrive(SCRIPT_B), buildDrive(SCRIPT_A)].map((d, i) => ({ ...d, info: { ...d.info, id: 39 - i } }));
  return cache;
}

/** A capture inside a drive: the same samples, cut to [t0, t1], times kept. */
export function sliceDrive(d: LoadedDrive, t0: number, t1: number, info: DriveInfo): LoadedDrive {
  const cut = (t: Float64Array, ...cols: Float64Array[]) => {
    let a = 0; while (a < t.length && t[a]! < t0) a++;
    let b = a; while (b < t.length && t[b]! <= t1) b++;
    return [t.slice(a, b), ...cols.map((c) => c.slice(a, b))];
  };
  const series: LoadedDrive['series'] = {};
  for (const [k, s] of Object.entries(d.series)) { const [t, v] = cut(s.t, s.v); series[k] = { t: t!, v: v! }; }
  const [t, lat, lon, sp, hd] = cut(d.gps.t, d.gps.lat, d.gps.lon, d.gps.speedKmh, d.gps.heading);
  return {
    ...d, info, series,
    events: d.events.filter((e) => e.t >= t0 && e.t <= t1),
    gps: { t: t!, lat: lat!, lon: lon!, speedKmh: sp!, heading: hd! },
  };
}

/** A DriveStore over the synthetic drives (plus one dropout capture in B). */
export class FixtureStore implements DriveStore {
  private deleted = new Set<string>();
  private captures(): { info: DriveInfo; parent: string; t0: number; t1: number }[] {
    const b = fixtureDrives()[0]!;
    return [{
      info: { id: 101, name: 'dropout_2026_11_26_2253', kind: 'dropout', startedUnix: b.info.startedUnix + 140,
        durationS: 25, rows: 0, status: 'ok', mapVersion: b.info.mapVersion, ecuId: b.info.ecuId, reason: 'K-line silent 5.7 s' },
      parent: b.info.name, t0: 140, t1: 165,
    }];
  }
  async listDrives(): Promise<DriveInfo[]> {
    const all = [...fixtureDrives().map((d) => d.info), ...this.captures().map((c) => c.info)];
    return all.filter((d) => !this.deleted.has(d.name)).sort((a, b) => b.startedUnix - a.startedUnix);
  }
  async loadDrive(name: string): Promise<LoadedDrive> {
    if (this.deleted.has(name)) throw new Error(`no drive ${name}`);
    const d = fixtureDrives().find((x) => x.info.name === name);
    if (d) return d;
    const c = this.captures().find((x) => x.info.name === name);
    if (c) return sliceDrive(fixtureDrives().find((x) => x.info.name === c.parent)!, c.t0, c.t1, c.info);
    throw new Error(`no drive ${name}`);
  }
  async deleteDrive(name: string): Promise<void> { this.deleted.add(name); }
  async importDb(): Promise<number> { throw new Error('the demo drives cannot take an import'); }
  async exportDb(): Promise<Uint8Array> { return new Uint8Array(0); }
}
