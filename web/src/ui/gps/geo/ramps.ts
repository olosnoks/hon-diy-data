/**
 * The colour ramps of the design and how a channel's values are spread over
 * them (port of hondalog/ramps.py). The KML export colours a track with the
 * same ramp as the map, so it looks the same in Google Earth.
 */

/** sequential (cividis-like, colour-blind safe, monotonic lightness) */
export const SEQ_RAMP = ['#00224E', '#35456C', '#666970', '#948E77', '#C8B866', '#FEE838'] as const;
/** diverging: fuel trim (blue = rich, orange = lean) */
export const DIV_RAMP = ['#053061', '#2166AC', '#F7F7F7', '#E08214', '#7F3B08'] as const;

export type RGB = [number, number, number];

function stops(hexes: readonly string[]): RGB[] {
  return hexes.map((h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255) as RGB);
}
const SEQ = stops(SEQ_RAMP);
const DIV = stops(DIV_RAMP);

/** Piecewise-linear ramp, t in 0..1 -> rgb 0..1. (float32 like numpy, so
 *  the KML colours match the Python export to the byte.) */
export function ramp(table: RGB[], t: number): RGB {
  const tt = Math.fround(Math.fround(Math.max(0, Math.min(1, t))) * (table.length - 1));
  const i = Math.min(Math.trunc(tt), table.length - 2);
  // numpy: float32 t minus int32 i is float64, so only t and the table are float32
  const f = tt - i;
  const a = table[i]!, b = table[i + 1]!;
  return [0, 1, 2].map((k) => Math.fround(a[k]!) * (1 - f) + Math.fround(b[k]!) * f) as RGB;
}

export const seq = (t: number) => ramp(SEQ, t);
export const div = (t: number) => ramp(DIV, t);

export function isDiverging(channel: string): boolean {
  const name = channel.toLowerCase();
  return name.includes('trim') || name === 'stft' || name === 'ltft';
}

/** numpy.percentile (linear) of the finite values */
export function percentile(sorted: number[], p: number): number {
  if (!sorted.length) return NaN;
  const pos = ((sorted.length - 1) * p) / 100;
  const i = Math.floor(pos);
  const f = pos - i;
  return i + 1 < sorted.length ? sorted[i]! + (sorted[i + 1]! - sorted[i]!) * f : sorted[i]!;
}

/** [lo, hi, diverging]: the 2nd to 98th percentile, symmetric for a trim,
 *  at least a little wide; speed starts at 0. */
export function valueRange(channel: string, values: ArrayLike<number>, lo?: number | null, hi?: number | null): [number, number, boolean] {
  const v: number[] = [];
  for (let i = 0; i < values.length; i++) if (Number.isFinite(values[i])) v.push(values[i]!);
  v.sort((a, b) => a - b);
  const diverging = isDiverging(channel);
  let l = lo ?? null, h = hi ?? null;
  if (l == null || h == null) {
    const a = v.length ? percentile(v, 2) : 0;
    const b = v.length ? percentile(v, 98) : 1;
    l = l ?? a;
    h = h ?? b;
  }
  if (diverging) {
    const m = Math.max(Math.abs(l), Math.abs(h), 1);
    l = -m; h = m;
  }
  if (!(h > l)) h = l + Math.max(1, Math.abs(l) * 0.1);
  if (['gps_speed', 'vss', 'speed'].includes(channel.toLowerCase()) && l > 0) l = 0;
  return [l, h, diverging];
}

/** values -> bin numbers 0..n-1; NaN -> -1 */
export function bins(values: ArrayLike<number>, lo: number, hi: number, n: number): Int32Array {
  const out = new Int32Array(values.length).fill(-1);
  for (let i = 0; i < values.length; i++) {
    const x = values[i]!;
    if (Number.isFinite(x)) out[i] = Math.max(0, Math.min(n - 1, Math.trunc(((x - lo) / (hi - lo)) * n)));
  }
  return out;
}

/** the centre colour of each of n bins */
export function binColours(n: number, diverging: boolean): RGB[] {
  return Array.from({ length: n }, (_, k) => (diverging ? div : seq)((k + 0.5) / n));
}

export function rgbHex(c: RGB): string {
  return '#' + c.map((v) => Math.round(v * 255).toString(16).padStart(2, '0')).join('').toUpperCase();
}
