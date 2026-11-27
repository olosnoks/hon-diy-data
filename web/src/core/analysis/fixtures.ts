/** Synthetic drives for the analysis tests (no real data, no GPS of anyone). */
import type { DriveInfo, LoadedDrive } from '../types';

export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function normal(r: () => number): number {
  return Math.sqrt(-2 * Math.log(r() || 1e-12)) * Math.cos(2 * Math.PI * r());
}

export function arange(a: number, b: number, step: number): Float64Array {
  const n = Math.max(0, Math.round((b - a) / step));
  return Float64Array.from({ length: n }, (_, i) => a + i * step);
}

export function info(name = 'drive_2026_01_01_0000', kind: DriveInfo['kind'] = 'drive'): DriveInfo {
  return { id: 1, name, kind, startedUnix: 0, durationS: 0, rows: 0, status: 'ok', mapVersion: 'test', ecuId: '' };
}

export function makeDrive(series: Record<string, { t: ArrayLike<number>; v: ArrayLike<number> }>,
  events: { t: number; kind: string; text: string }[] = [], opts: { name?: string; kind?: DriveInfo['kind']; gps?: Partial<LoadedDrive['gps']> } = {}): LoadedDrive {
  const s: LoadedDrive['series'] = {};
  for (const [k, v] of Object.entries(series)) s[k] = { t: Float64Array.from(v.t), v: Float64Array.from(v.v) };
  const e = new Float64Array(0);
  const g = opts.gps ?? {};
  return {
    info: info(opts.name, opts.kind), map: null, series: s, reads: [], events,
    gps: { t: g.t ?? e, lat: g.lat ?? e, lon: g.lon ?? e, speedKmh: g.speedKmh ?? e, heading: g.heading ?? e },
  };
}

/** One channel from a function of time. */
export function ch(t: Float64Array, f: (t: number, i: number) => number): { t: Float64Array; v: Float64Array } {
  return { t, v: t.map((x, i) => f(x, i)) };
}
