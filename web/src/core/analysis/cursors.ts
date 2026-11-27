/**
 * Cursor readouts: the value at a cursor and statistics between two cursors
 * (port of hondalog/analysis/cursors.py).
 */
import { searchSorted, type Series } from './series';

export interface SpanStats {
  name: string;
  units: string;
  a: number;        // value at cursor A
  b: number;        // value at cursor B
  delta: number;    // b - a
  min: number;
  max: number;
  /** time-weighted (zero-order hold), so uneven sampling does not skew it */
  mean: number;
  n: number;        // samples between the cursors
  rate: number;     // delta per second
}

/** Value at time t: last sample at or before t ("hold"), the nearest sample
 *  ("nearest") or a straight line between neighbours ("linear"). */
export function valueAt(series: { t: Float64Array; v: Float64Array }, t: number,
  how: 'hold' | 'nearest' | 'linear' = 'hold'): number {
  const n = series.t.length;
  if (n === 0) return NaN;
  const i = searchSorted(series.t, t, 'right') - 1;
  if (how === 'nearest') {
    let j = Math.min(Math.max(i, 0), n - 1);
    if (j + 1 < n && Math.abs(series.t[j + 1]! - t) < Math.abs(series.t[j]! - t)) j += 1;
    return series.v[j]!;
  }
  if (i < 0) return NaN;
  if (how === 'linear' && i + 1 < n) {
    const t0 = series.t[i]!; const t1 = series.t[i + 1]!;
    if (t1 > t0) return series.v[i]! + ((series.v[i + 1]! - series.v[i]!) * (t - t0)) / (t1 - t0);
  }
  return series.v[i]!;
}

/** Statistics of one channel between two cursor times (either order).
 *  `full = false` gives only the two values, their difference and the rate
 *  (min, max, mean NaN, n 0): what a readout following a dragged cursor needs. */
export function spanStats(series: Series, ta: number, tb: number, full = true): SpanStats {
  const lo = Math.min(ta, tb); const hi = Math.max(ta, tb);
  const a = valueAt(series, ta); const b = valueAt(series, tb);
  const span = tb - ta;
  const rate = span ? (b - a) / span : NaN;
  const units = series.units ?? '';
  if (!full) return { name: series.name, units, a, b, delta: b - a, min: NaN, max: NaN, mean: NaN, n: 0, rate };
  const i0 = searchSorted(series.t, lo, 'left');
  const i1 = searchSorted(series.t, hi, 'right');
  let n = 0; let vmin = Infinity; let vmax = -Infinity; let sum = 0;
  for (let i = i0; i < i1; i++) {
    const v = series.v[i]!;
    if (Number.isNaN(v)) continue;
    n++; sum += v; if (v < vmin) vmin = v; if (v > vmax) vmax = v;
  }
  let mean = NaN;
  if (n === 0) { vmin = NaN; vmax = NaN; }
  else {
    mean = sum / n;
    if (n > 1) {
      let ws = 0; let wv = 0;
      for (let i = i0; i < i1; i++) {
        const v = series.v[i]!;
        const next = i + 1 < i1 ? series.t[i + 1]! : hi;
        const dt = next - series.t[i]!;
        if (Number.isNaN(v)) continue;
        ws += dt; wv += v * dt;
      }
      if (ws > 0) mean = wv / ws;
    }
  }
  return { name: series.name, units, a, b, delta: b - a, min: vmin, max: vmax, mean, n, rate };
}

/** Time of the next (+1) or previous (-1) sample of this channel from t. */
export function stepSample(series: { t: Float64Array }, t: number, direction: number): number {
  const n = series.t.length;
  if (n === 0) return t;
  const i = direction > 0 ? searchSorted(series.t, t, 'right') : searchSorted(series.t, t, 'left') - 1;
  return series.t[Math.min(Math.max(i, 0), n - 1)]!;
}
