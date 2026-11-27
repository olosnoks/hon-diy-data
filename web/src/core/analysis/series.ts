/**
 * Shared helpers over the typed arrays of a LoadedDrive: binary search,
 * resampling onto a grid (hondalog/data/reader.py `align`), the Table of
 * channels on one time grid (hondalog/data/model.py `Table`), smoothing.
 *
 * NaN marks "no value here" throughout, as in the Python app.
 */
import type { LoadedDrive } from '../types';

export interface Series { name: string; t: Float64Array; v: Float64Array; units?: string }

/** Several channels on one shared time grid (hondalog Table). */
export interface Table {
  t: Float64Array;
  columns: Record<string, Float64Array>;
  units?: Record<string, string>;
}

export function tableCol(table: Table, name: string): Float64Array {
  if (name === 't') return table.t;
  const c = table.columns[name];
  if (!c) throw new Error(`no column ${name}`);
  return c;
}

/** Names for an expression evaluator: every column plus `t`. */
export function tableEnv(table: Table): Record<string, Float64Array> {
  return { ...table.columns, t: table.t };
}

/** numpy searchsorted(a, x, side): first index i with a[i] >= x ("left") / > x ("right"). */
export function searchSorted(a: ArrayLike<number>, x: number, side: 'left' | 'right' = 'left'): number {
  let lo = 0;
  let hi = a.length;
  if (side === 'left') {
    while (lo < hi) { const m = (lo + hi) >>> 1; if ((a[m] as number) < x) lo = m + 1; else hi = m; }
  } else {
    while (lo < hi) { const m = (lo + hi) >>> 1; if ((a[m] as number) <= x) lo = m + 1; else hi = m; }
  }
  return lo;
}

export function median(values: ArrayLike<number>): number {
  const a = Array.from(values).filter((x) => !Number.isNaN(x)).sort((p, q) => p - q);
  if (!a.length) return NaN;
  const m = a.length >> 1;
  return a.length % 2 ? a[m]! : (a[m - 1]! + a[m]!) / 2;
}

export function nanMin(a: ArrayLike<number>): number {
  let m = Infinity; let any = false;
  for (let i = 0; i < a.length; i++) { const x = a[i]!; if (!Number.isNaN(x)) { any = true; if (x < m) m = x; } }
  return any ? m : NaN;
}

export function nanMax(a: ArrayLike<number>): number {
  let m = -Infinity; let any = false;
  for (let i = 0; i < a.length; i++) { const x = a[i]!; if (!Number.isNaN(x)) { any = true; if (x > m) m = x; } }
  return any ? m : NaN;
}

export function nanMean(a: ArrayLike<number>): number {
  let s = 0; let n = 0;
  for (let i = 0; i < a.length; i++) { const x = a[i]!; if (!Number.isNaN(x)) { s += x; n++; } }
  return n ? s / n : NaN;
}

/** Sample `series` at the times in `grid` ("hold" = last value at or before,
 *  "linear" = interpolate, never extrapolate past the last read). A value
 *  older than `maxAge` becomes NaN (default three median sample periods, at
 *  least 0.5 s) so a link dropout shows as a gap, not a frozen number. */
export function align(series: { t: Float64Array; v: Float64Array }, grid: Float64Array,
  how: 'hold' | 'linear' = 'hold', maxAge?: number): Float64Array {
  const out = new Float64Array(grid.length).fill(NaN);
  const st = series.t; const sv = series.v;
  const n = st.length;
  if (n === 0 || grid.length === 0) return out;
  if (st === grid || (n === grid.length && st.every((x, i) => x === grid[i]))) return Float64Array.from(sv);
  let age = maxAge;
  if (age === undefined) {
    if (n > 1) {
      const d = new Float64Array(n - 1);
      for (let i = 0; i < n - 1; i++) d[i] = st[i + 1]! - st[i]!;
      age = Math.max(3 * median(d), 0.5);
    } else age = Infinity;
  }
  for (let g = 0; g < grid.length; g++) {
    const x = grid[g]!;
    const i = searchSorted(st, x, 'right') - 1;
    if (i < 0) continue;
    if (how === 'linear') {
      const j = Math.min(i + 1, n - 1);
      const span = st[j]! - st[i]!;
      let frac = span > 0 ? (x - st[i]!) / span : 0;
      frac = Math.min(Math.max(frac, 0), 1);
      if (!(span <= age || span === 0)) continue;
      if (x - st[i]! > age) continue;
      if (x > st[n - 1]!) continue;
      out[g] = sv[i]! + (sv[j]! - sv[i]!) * frac;
    } else {
      if (x - st[i]! > age) continue;
      out[g] = sv[i]!;
    }
  }
  return out;
}

/** First and last timestamp over every channel (and the GPS track). */
export function driveSpan(drive: LoadedDrive): [number, number] {
  let lo = Infinity; let hi = -Infinity;
  for (const s of Object.values(drive.series)) {
    if (s.t.length) { lo = Math.min(lo, s.t[0]!); hi = Math.max(hi, s.t[s.t.length - 1]!); }
  }
  if (!Number.isFinite(lo)) return [0, 0];
  return [lo, hi];
}

export function getSeries(drive: LoadedDrive, name: string): Series {
  const s = drive.series[name];
  const units = drive.map?.channels.find((c) => c.name === name)?.units
    ?? drive.map?.math.find((m) => m.name === name)?.units ?? '';
  return s ? { name, t: s.t, v: s.v, units } : { name, t: new Float64Array(0), v: new Float64Array(0), units };
}

export function hasSeries(drive: LoadedDrive, name: string): boolean {
  return (drive.series[name]?.t.length ?? 0) > 0;
}

export function uniformGrid(t0: number, t1: number, hz: number): Float64Array {
  const n = Math.max(0, Math.floor((t1 - t0) * hz + 1e-9) + 1);
  const g = new Float64Array(n);
  for (let i = 0; i < n; i++) g[i] = t0 + i / hz;
  return g;
}

export interface TableOptions { base?: string; hz?: number; t?: Float64Array; how?: 'hold' | 'linear'; maxAge?: number }

/** Put channels on one time grid (hondalog Log.table). Grid preference: `t`,
 *  a uniform grid at `hz`, channel `base`'s timestamps, the longest channel's. */
export function driveTable(drive: LoadedDrive, names?: string[], opts: TableOptions = {}): Table {
  const list = names ?? Object.keys(drive.series);
  const series = list.map((n) => getSeries(drive, n));
  let grid = opts.t;
  if (!grid) {
    if (opts.hz) { const [a, b] = driveSpan(drive); grid = uniformGrid(a, b, opts.hz); }
    else if (opts.base) grid = getSeries(drive, opts.base).t;
    else if (series.length) grid = series.reduce((m, s) => (s.t.length > m.t.length ? s : m)).t;
    else grid = new Float64Array(0);
  }
  const columns: Record<string, Float64Array> = {};
  const units: Record<string, string> = {};
  for (const s of series) { columns[s.name] = align(s, grid, opts.how ?? 'hold', opts.maxAge); units[s.name] = s.units ?? ''; }
  return { t: grid, columns, units };
}

/** Linear interpolation of finite samples over NaN gaps no longer than
 *  `maxGapS` seconds (longer gaps stay NaN). Returns a new array. */
export function fillGaps(t: Float64Array, v: Float64Array, maxGapS = Infinity): Float64Array {
  const out = Float64Array.from(v);
  let last = -1;
  for (let i = 0; i < v.length; i++) {
    if (Number.isNaN(v[i]!)) continue;
    if (last >= 0 && i - last > 1 && t[i]! - t[last]! <= maxGapS) {
      for (let k = last + 1; k < i; k++) {
        out[k] = v[last]! + (v[i]! - v[last]!) * (t[k]! - t[last]!) / (t[i]! - t[last]!);
      }
    }
    last = i;
  }
  return out;
}

/** Savitzky-Golay over a uniform grid (scipy savgol_filter, mode "interp"):
 *  a least-squares polynomial of `order` through `window` samples. `deriv` 0
 *  smooths, 1 gives the first derivative (per second with `delta` the sample
 *  period). The edges use the polynomial fitted to the first / last window.
 *  NaN in the input poisons only the windows that contain it. */
export function savgol(v: Float64Array, window: number, order: number, deriv: 0 | 1 = 0, delta = 1): Float64Array {
  const n = v.length;
  const out = new Float64Array(n).fill(NaN);
  if (window % 2 === 0) window += 1;
  const half = (window - 1) >> 1;
  if (n < window) {
    if (n <= order) return out;
    return polyEdge(v, 0, n, 0, n, order, deriv, delta, out);
  }
  // interior: fixed convolution weights, from the fit at offset 0
  const w = sgWeights(half, order, deriv, 0);
  for (let i = half; i < n - half; i++) {
    let s = 0;
    for (let k = -half; k <= half; k++) s += w[k + half]! * v[i + k]!;
    out[i] = s / (deriv ? delta : 1);
  }
  polyEdge(v, 0, window, 0, half, order, deriv, delta, out);
  polyEdge(v, n - window, n, n - half, n, order, deriv, delta, out);
  return out;
}

/** Weights that give the fitted value (or slope) at offset `at` from the
 *  window centre, for a window of 2*half+1 samples. */
function sgWeights(half: number, order: number, deriv: 0 | 1, at: number): Float64Array {
  const m = 2 * half + 1;
  const p = order + 1;
  // normal matrix A^T A with A[k][j] = x_k^j
  const ata: number[][] = Array.from({ length: p }, () => new Array<number>(p).fill(0));
  for (let k = -half; k <= half; k++) {
    for (let a = 0; a < p; a++) for (let b = 0; b < p; b++) ata[a]![b]! += k ** (a + b);
  }
  const inv = invert(ata);
  // row vector e = d/dx^deriv of [1, x, x^2...] at `at`
  const e = new Array<number>(p).fill(0);
  for (let j = 0; j < p; j++) {
    if (deriv === 0) e[j] = at ** j;
    else e[j] = j === 0 ? 0 : j * at ** (j - 1);
  }
  const w = new Float64Array(m);
  for (let k = -half; k <= half; k++) {
    let s = 0;
    for (let a = 0; a < p; a++) {
      let c = 0;
      for (let b = 0; b < p; b++) c += inv[a]![b]! * k ** b;
      s += e[a]! * c;
    }
    w[k + half] = s;
  }
  return w;
}

function polyEdge(v: Float64Array, w0: number, w1: number, o0: number, o1: number, order: number,
  deriv: 0 | 1, delta: number, out: Float64Array): Float64Array {
  const m = w1 - w0;
  const half = (m - 1) / 2;
  const centre = w0 + half;
  for (let i = o0; i < o1; i++) {
    // weights for an even-length window too: generic least squares on positions k - centre
    const p = order + 1;
    const ata: number[][] = Array.from({ length: p }, () => new Array<number>(p).fill(0));
    for (let k = w0; k < w1; k++) {
      const x = k - centre;
      for (let a = 0; a < p; a++) for (let b = 0; b < p; b++) ata[a]![b]! += x ** (a + b);
    }
    const inv = invert(ata);
    const at = i - centre;
    let s = 0;
    for (let k = w0; k < w1; k++) {
      const x = k - centre;
      let wk = 0;
      for (let a = 0; a < p; a++) {
        const ea = deriv === 0 ? at ** a : a === 0 ? 0 : a * at ** (a - 1);
        let c = 0;
        for (let b = 0; b < p; b++) c += inv[a]![b]! * x ** b;
        wk += ea * c;
      }
      s += wk * v[k]!;
    }
    out[i] = s / (deriv ? delta : 1);
  }
  return out;
}

/** Gauss-Jordan inverse of a small square matrix. */
export function invert(m: number[][]): number[][] {
  const n = m.length;
  const a = m.map((row, i) => [...row, ...Array.from({ length: n }, (_, j) => (i === j ? 1 : 0))]);
  for (let c = 0; c < n; c++) {
    let piv = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(a[r]![c]!) > Math.abs(a[piv]![c]!)) piv = r;
    [a[c], a[piv]] = [a[piv]!, a[c]!];
    const d = a[c]![c]!;
    if (d === 0) throw new Error('singular matrix');
    for (let j = 0; j < 2 * n; j++) a[c]![j]! /= d;
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = a[r]![c]!;
      if (f !== 0) for (let j = 0; j < 2 * n; j++) a[r]![j]! -= f * a[c]![j]!;
    }
  }
  return a.map((row) => row.slice(n));
}

/** Contiguous runs where `pred(i)` holds: [start, end) index pairs. */
export function runs(n: number, pred: (i: number) => boolean): [number, number][] {
  const out: [number, number][] = [];
  let start = -1;
  for (let i = 0; i < n; i++) {
    if (pred(i)) { if (start < 0) start = i; }
    else if (start >= 0) { out.push([start, i]); start = -1; }
  }
  if (start >= 0) out.push([start, n]);
  return out;
}
