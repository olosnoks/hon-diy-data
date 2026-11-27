/**
 * A drive's GPS track: positions in time order, and what the map asks of it
 * (port of hondalog/geo/track.py).
 *
 * Only the rows with a fix, sorted by time, projected to Web Mercator unit
 * coordinates, with a break wherever no fix came for GAP_S.
 */
import type { LoadedDrive } from '../../../core/types';
import { haversineM, MAX_LAT, toUnit, worldPx } from './proj';

/** no fix for longer than this and the line is broken */
export const GAP_S = 5.0;
/** a fix this close to (0, 0) is a receiver that has not got one yet */
export const NULL_ISLAND_DEG = 0.01;

export interface Position {
  t: number; x: number; y: number; lat: number; lon: number;
  speedKmh: number; heading: number; index: number;
}

type Num = ArrayLike<number>;

export class Track {
  readonly t: Float64Array;
  readonly lat: Float64Array;
  readonly lon: Float64Array;
  readonly speed: Float64Array;
  readonly heading: Float64Array;
  readonly sats: Float64Array;
  readonly x: Float64Array;
  readonly y: Float64Array;
  /** breaks[i]: no line from fix i-1 to fix i */
  readonly breaks: Uint8Array;
  /** distance along the track, metres (gaps add nothing) */
  readonly dist: Float64Array;
  private lodCache = new Map<number, Int32Array>();

  constructor(t: Num, lat: Num, lon: Num, speed?: Num | null, heading?: Num | null, sats?: Num | null) {
    const n = Math.min(t.length, lat.length, lon.length);
    const col = (v: Num | null | undefined, i: number) => (v && v.length >= n ? Number(v[i]) : NaN);
    const idx: number[] = [];
    for (let i = 0; i < n; i++) {
      const ti = Number(t[i]), la = Number(lat[i]), lo = Number(lon[i]);
      if (!Number.isFinite(ti) || !Number.isFinite(la) || !Number.isFinite(lo)) continue;
      if (Math.abs(la) > MAX_LAT || Math.abs(lo) > 180) continue;
      if (Math.abs(la) < NULL_ISLAND_DEG && Math.abs(lo) < NULL_ISLAND_DEG) continue;
      idx.push(i);
    }
    idx.sort((a, b) => Number(t[a]) - Number(t[b]) || a - b); // stable
    const m = idx.length;
    this.t = new Float64Array(m); this.lat = new Float64Array(m); this.lon = new Float64Array(m);
    this.speed = new Float64Array(m); this.heading = new Float64Array(m); this.sats = new Float64Array(m);
    this.x = new Float64Array(m); this.y = new Float64Array(m);
    this.breaks = new Uint8Array(m); this.dist = new Float64Array(m);
    idx.forEach((src, k) => {
      this.t[k] = Number(t[src]); this.lat[k] = Number(lat[src]); this.lon[k] = Number(lon[src]);
      this.speed[k] = col(speed, src); this.heading[k] = col(heading, src); this.sats[k] = col(sats, src);
      const [x, y] = toUnit(this.lon[k]!, this.lat[k]!);
      this.x[k] = x; this.y[k] = y;
    });
    for (let k = 0; k < m; k++) {
      this.breaks[k] = k === 0 || this.t[k]! - this.t[k - 1]! > GAP_S ? 1 : 0;
      if (k > 0) {
        const step = this.breaks[k] ? 0 : haversineM(this.lat[k - 1]!, this.lon[k - 1]!, this.lat[k]!, this.lon[k]!);
        this.dist[k] = this.dist[k - 1]! + step;
      }
    }
  }

  static fromDrive(drive: Pick<LoadedDrive, 'gps'> | null | undefined): Track {
    const g = drive?.gps;
    if (!g) return new Track([], [], []);
    return new Track(g.t, g.lat, g.lon, g.speedKmh, g.heading);
  }

  get length(): number { return this.t.length; }
  get empty(): boolean { return this.t.length === 0; }
  get duration(): number { return this.t.length > 1 ? this.t[this.t.length - 1]! - this.t[0]! : 0; }
  get lengthM(): number { return this.dist.length ? this.dist[this.dist.length - 1]! : 0; }
  get gaps(): number { let n = 0; for (let i = 1; i < this.breaks.length; i++) n += this.breaks[i]!; return n; }

  /** [x0, y0, x1, y1] in unit coordinates */
  bbox(): [number, number, number, number] {
    if (this.empty) return [0, 0, 1, 1];
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let i = 0; i < this.x.length; i++) {
      x0 = Math.min(x0, this.x[i]!); x1 = Math.max(x1, this.x[i]!);
      y0 = Math.min(y0, this.y[i]!); y1 = Math.max(y1, this.y[i]!);
    }
    return [x0, y0, x1, y1];
  }

  /** Indices of the fixes to draw at zoom: a fix is kept when it is at least
   *  px screen pixels from the last fix kept. Both ends of every gap are kept,
   *  and the last fix. */
  lod(zoom: number, px = 1.5): Int32Array {
    const key = Math.trunc(zoom) * 64 + Math.round(px * 8);
    const hit = this.lodCache.get(key);
    if (hit) return hit;
    const n = this.t.length;
    let out: Int32Array;
    if (n <= 2) {
      out = Int32Array.from({ length: n }, (_, i) => i);
    } else {
      const scale = worldPx(zoom);
      const must = new Uint8Array(n);
      for (let i = 0; i < n; i++) must[i] = this.breaks[i]! || (i + 1 < n && this.breaks[i + 1]) ? 1 : 0;
      must[n - 1] = 1;
      const keep: number[] = [0];
      let lx = this.x[0]! * scale, ly = this.y[0]! * scale;
      let gx = Math.floor(lx / px), gy = Math.floor(ly / px);
      const tol2 = px * px;
      for (let i = 1; i < n; i++) {
        const x = this.x[i]! * scale, y = this.y[i]! * scale;
        const cx = Math.floor(x / px), cy = Math.floor(y / px);
        const cand = cx !== gx || cy !== gy;
        gx = cx; gy = cy;
        if (!cand && !must[i]) continue;
        if (must[i] || (x - lx) * (x - lx) + (y - ly) * (y - ly) >= tol2) {
          keep.push(i); lx = x; ly = y;
        }
      }
      out = Int32Array.from(keep);
    }
    this.lodCache.set(key, out);
    return out;
  }

  /** Where the car was at t (linear between fixes). null before the first
   *  fix, after the last, and inside a gap. */
  at(t: number): Position | null {
    const n = this.t.length;
    if (n === 0 || !Number.isFinite(t) || t < this.t[0]! || t > this.t[n - 1]!) return null;
    let i = upperBound(this.t, t) - 1;
    let f = 0;
    if (i >= n - 1) {
      i = n - 1;
    } else if (this.breaks[i + 1]) {
      if (t - this.t[i]! > GAP_S) return null;
    } else {
      const span = this.t[i + 1]! - this.t[i]!;
      f = span > 0 ? (t - this.t[i]!) / span : 0;
    }
    const j = Math.min(i + 1, n - 1);
    const lerp = (a: Float64Array) => (f ? a[i]! + (a[j]! - a[i]!) * f : a[i]!);
    return {
      t, x: lerp(this.x), y: lerp(this.y), lat: lerp(this.lat), lon: lerp(this.lon),
      speedKmh: lerp(this.speed), heading: this.heading[i]!, index: i + f,
    };
  }

  /** [time, distance] of the point on the drawn line nearest to unit point
   *  (x, y); null when nothing is within maxD. */
  nearest(x: number, y: number, maxD = Infinity): [number, number] | null {
    const n = this.t.length;
    if (n === 0) return null;
    let bestT = this.t[0]!, bestD = Infinity;
    for (let k = 0; k < n; k++) {
      const d = Math.hypot(this.x[k]! - x, this.y[k]! - y);
      if (d < bestD) { bestD = d; bestT = this.t[k]!; }
    }
    for (let s = 0; s + 1 < n; s++) {
      if (this.breaks[s + 1]) continue;
      const ax = this.x[s]!, ay = this.y[s]!;
      const vx = this.x[s + 1]! - ax, vy = this.y[s + 1]! - ay;
      const seg2 = vx * vx + vy * vy;
      const f = seg2 > 0 ? Math.max(0, Math.min(1, ((x - ax) * vx + (y - ay) * vy) / seg2)) : 0;
      const d = Math.hypot(ax + f * vx - x, ay + f * vy - y);
      if (d < bestD) { bestD = d; bestT = this.t[s]! + f * (this.t[s + 1]! - this.t[s]!); }
    }
    return bestD <= maxD ? [bestT, bestD] : null;
  }

  /** A channel's value at every fix (sample-and-hold, NaN when too old). */
  sample(tSrc: Num, vSrc: Num, maxAge?: number): Float64Array {
    return hold(tSrc, vSrc, this.t, maxAge);
  }

  /** Index runs of the track without a gap. */
  segments(): Array<[number, number]> {
    const out: Array<[number, number]> = [];
    let start = 0;
    for (let i = 1; i <= this.t.length; i++) {
      if (i === this.t.length || this.breaks[i]) {
        if (i > start) out.push([start, i]);
        start = i;
      }
    }
    return out;
  }

  /** Same track restricted to t0 <= t <= t1. */
  cut(t0?: number | null, t1?: number | null): Track {
    if (t0 == null && t1 == null) return this;
    const lo = t0 ?? -Infinity, hi = t1 ?? Infinity;
    const keep: number[] = [];
    for (let i = 0; i < this.t.length; i++) if (this.t[i]! >= lo && this.t[i]! <= hi) keep.push(i);
    const pick = (a: Float64Array) => keep.map((i) => a[i]!);
    return new Track(pick(this.t), pick(this.lat), pick(this.lon), pick(this.speed), pick(this.heading), pick(this.sats));
  }
}

/** First index with a[i] > v (searchsorted side="right"). */
export function upperBound(a: Num, v: number): number {
  let lo = 0, hi = a.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (Number(a[mid]) <= v) lo = mid + 1; else hi = mid;
  }
  return lo;
}

function median(a: number[]): number {
  if (!a.length) return NaN;
  const s = [...a].sort((p, q) => p - q);
  const m = s.length >> 1;
  return s.length % 2 ? s[m]! : (s[m - 1]! + s[m]!) / 2;
}

/** Sample-and-hold of (tSrc, vSrc) at the times grid. */
export function hold(tSrc: Num, vSrc: Num, grid: Num, maxAge?: number): Float64Array {
  const out = new Float64Array(grid.length).fill(NaN);
  if (tSrc.length === 0 || grid.length === 0) return out;
  let age = maxAge;
  if (age == null) {
    const diffs: number[] = [];
    for (let i = 1; i < tSrc.length; i++) diffs.push(Number(tSrc[i]) - Number(tSrc[i - 1]));
    const dt = diffs.length ? median(diffs) : 1;
    age = Math.max(1, 3 * dt);
  }
  for (let k = 0; k < grid.length; k++) {
    const i = upperBound(tSrc, Number(grid[k])) - 1;
    if (i < 0) continue;
    if (Number(grid[k]) - Number(tSrc[i]) <= age) out[k] = Number(vSrc[i]);
  }
  return out;
}

/** How far in time a waypoint may be from a fix and still be placed on it */
export const PLACE_WITHIN_S = 60.0;

/** [lat, lon] of a moment: on the track, else the nearest fix in time. */
export function place(track: Track, t: number): [number, number] | null {
  const pos = track.at(t);
  if (pos) return [pos.lat, pos.lon];
  if (track.empty) return null;
  let best = 0, bd = Infinity;
  for (let i = 0; i < track.length; i++) {
    const d = Math.abs(track.t[i]! - t);
    if (d < bd) { bd = d; best = i; }
  }
  return bd <= PLACE_WITHIN_S ? [track.lat[best]!, track.lon[best]!] : null;
}
