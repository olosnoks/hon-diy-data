/**
 * Level of detail for plotting: a min/max envelope per pixel column
 * (port of hondalog/analysis/lod.py).
 *
 * A naive "every Nth sample" hides spikes; the envelope keeps the lowest and
 * highest value in each pixel column, so a one-sample dropout is still drawn.
 * `envelope` is enough for K-line logs; for kHz black-box streams build an
 * `LodPyramid` once and query any zoom from pre-reduced levels.
 */
import { searchSorted } from './series';

export interface Envelope {
  /** Centre of each non-empty pixel column (or the raw sample times). */
  t: Float64Array;
  lo: Float64Array;
  hi: Float64Array;
  /** false: these are the raw samples (lo === hi). */
  reduced: boolean;
}

/** x, y for one zig-zag line that covers the envelope (lo, hi, lo, hi...). */
export function polyline(env: Envelope): [Float64Array, Float64Array] {
  if (!env.reduced) return [env.t, env.lo];
  const n = env.t.length;
  const x = new Float64Array(2 * n);
  const y = new Float64Array(2 * n);
  for (let i = 0; i < n; i++) {
    x[2 * i] = x[2 * i + 1] = env.t[i]!;
    y[2 * i] = env.lo[i]!;
    y[2 * i + 1] = env.hi[i]!;
  }
  return [x, y];
}

const fmin = (a: number, b: number) => (Number.isNaN(a) ? b : Number.isNaN(b) ? a : Math.min(a, b));
const fmax = (a: number, b: number) => (Number.isNaN(a) ? b : Number.isNaN(b) ? a : Math.max(a, b));

function linspace(a: number, b: number, n: number): Float64Array {
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) out[i] = n === 1 ? a : a + ((b - a) * i) / (n - 1);
  return out;
}

/** Min/max of `v` in each of `columns` equal time slices of [t0, t1]. If the
 *  range holds no more than two samples per column the raw samples come back
 *  (with one sample of context each side). NaN samples are ignored; an
 *  all-NaN column stays NaN, which a plot draws as a gap. */
export function envelope(t: Float64Array, v: Float64Array, t0?: number, t1?: number, columns = 1000): Envelope {
  if (t.length === 0) {
    const e = new Float64Array(0);
    return { t: e, lo: e, hi: e, reduced: false };
  }
  const a0 = t0 ?? t[0]!;
  const a1 = t1 ?? t[t.length - 1]!;
  const i0 = searchSorted(t, a0, 'left');
  const i1 = searchSorted(t, a1, 'right');
  const cols = Math.max(1, Math.floor(columns));
  if (i1 - i0 <= 2 * cols || a1 <= a0) {
    const a = Math.max(0, i0 - 1);
    const b = Math.min(t.length, i1 + 1);
    const vs = v.slice(a, b);
    return { t: t.slice(a, b), lo: vs, hi: vs, reduced: false };
  }
  const edges = linspace(a0, a1, cols + 1);
  const ts = t.subarray(i0, i1);
  const ct: number[] = []; const lo: number[] = []; const hi: number[] = [];
  const total = i1 - i0;
  let start = searchSorted(ts, edges[0]!, 'left');
  for (let c = 0; c < cols; c++) {
    const end = c + 1 < cols ? searchSorted(ts, edges[c + 1]!, 'left') : total;
    if (end > start) {
      let l = NaN; let h = NaN;
      for (let k = start; k < end; k++) { const x = v[i0 + k]!; l = fmin(l, x); h = fmax(h, x); }
      ct.push((edges[c]! + edges[c + 1]!) / 2); lo.push(l); hi.push(h);
    }
    start = Math.max(start, end);
  }
  return { t: Float64Array.from(ct), lo: Float64Array.from(lo), hi: Float64Array.from(hi), reduced: true };
}

/** Pre-reduced min/max levels of one long series. Level k holds the min and
 *  max of blocks of factor**k samples; a query picks the coarsest level that
 *  still has `oversample` blocks per pixel column. */
export class LodPyramid {
  readonly t: Float64Array;
  readonly v: Float64Array;
  readonly levels: { t: Float64Array; lo: Float64Array; hi: Float64Array }[] = [];

  constructor(t: Float64Array, v: Float64Array, readonly factor = 8, minLen = 4096) {
    this.t = t; this.v = v;
    let lo = v; let hi = v; let tt = t;
    while (tt.length > minLen) {
      const nb = Math.ceil(tt.length / factor);
      const nlo = new Float64Array(nb); const nhi = new Float64Array(nb); const nt = new Float64Array(nb);
      for (let b = 0; b < nb; b++) {
        const s = b * factor; const e = Math.min(s + factor, tt.length);
        let l = NaN; let h = NaN;
        for (let k = s; k < e; k++) { l = fmin(l, lo[k]!); h = fmax(h, hi[k]!); }
        nlo[b] = l; nhi[b] = h; nt[b] = tt[s]!;
      }
      lo = nlo; hi = nhi; tt = nt;
      this.levels.push({ t: tt, lo, hi });
    }
  }

  envelope(t0?: number, t1?: number, columns = 1000, oversample = 4): Envelope {
    if (this.t.length === 0) return envelope(this.t, this.v, t0, t1, columns);
    const a0 = t0 ?? this.t[0]!;
    const a1 = t1 ?? this.t[this.t.length - 1]!;
    const cols = Math.max(1, Math.floor(columns));
    const need = cols * oversample;
    let chosen: { t: Float64Array; lo: Float64Array; hi: Float64Array } | undefined;
    for (let k = this.levels.length - 1; k >= 0; k--) {
      const L = this.levels[k]!;
      if (searchSorted(L.t, a1, 'right') - searchSorted(L.t, a0, 'left') >= need) { chosen = L; break; }
    }
    if (!chosen) return envelope(this.t, this.v, a0, a1, cols);
    const { t: tt, lo, hi } = chosen;
    const edges = linspace(a0, a1, cols + 1);
    const lastT = this.t[this.t.length - 1]!;
    const ct: number[] = []; const ol: number[] = []; const oh: number[] = [];
    for (let c = 0; c < cols; c++) {
      // every block that starts inside the column, plus the one straddling its left edge
      const first = searchSorted(tt, edges[c]!, 'left');
      const stop = c === cols - 1 ? searchSorted(tt, a1, 'right') : searchSorted(tt, edges[c + 1]!, 'left');
      let l = NaN; let h = NaN; let has = false;
      for (let b = first; b < stop; b++) { l = fmin(l, lo[b]!); h = fmax(h, hi[b]!); has = true; }
      const s = first - 1;
      const straddles = s >= 0 && s < tt.length && tt[s]! < edges[c]! && (s < tt.length - 1 || lastT >= edges[c]!);
      if (straddles) { l = fmin(l, lo[s]!); h = fmax(h, hi[s]!); }
      if (has || straddles) { ct.push((edges[c]! + edges[c + 1]!) / 2); ol.push(l); oh.push(h); }
    }
    return { t: Float64Array.from(ct), lo: Float64Array.from(ol), hi: Float64Array.from(oh), reduced: true };
  }
}

/** Indices of at most `limit` evenly spread points out of `n` (scatter plots). */
export function decimatePoints(n: number, limit: number): Int32Array {
  if (n <= limit) return Int32Array.from({ length: n }, (_, i) => i);
  const out: number[] = [];
  let prev = -1;
  for (let k = 0; k < limit; k++) {
    const i = Math.floor(limit === 1 ? 0 : ((n - 1) * k) / (limit - 1));
    if (i !== prev) out.push(i);
    prev = i;
  }
  return Int32Array.from(out);
}
