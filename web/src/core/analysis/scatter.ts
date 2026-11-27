/**
 * Scatter plot data: X against Y, optional colour channel, polynomial fit,
 * brushing (port of hondalog/analysis/scatter.py).
 */
import { decimatePoints } from './lod';
import { invert, tableCol, type Table } from './series';

/** y = c[0] + c[1] x + c[2] x^2 ... through the points. */
export class Fit {
  constructor(readonly coeffs: number[], readonly r2: number, readonly n: number) {}

  at(x: number): number {
    let y = 0;
    for (let p = this.coeffs.length - 1; p >= 0; p--) y = y * x + this.coeffs[p]!;
    return y;
  }

  describe(decimals = 4): string {
    const g = (c: number) => Number(c.toPrecision(decimals)).toString();
    const terms = this.coeffs.map((c, p) => g(c) + (p === 0 ? '' : p === 1 ? ' x' : ` x^${p}`));
    return `y = ${terms.join(' + ').replace(/\+ -/g, '- ')}   (R² = ${this.r2.toFixed(3)}, n = ${this.n})`;
  }
}

/** Least-squares polynomial; null with too few points or no spread in x. */
export function polyfit(x: ArrayLike<number>, y: ArrayLike<number>, degree: number): number[] | null {
  const n = x.length;
  if (n <= degree) return null;
  let lo = Infinity; let hi = -Infinity; let mx = 0;
  for (let i = 0; i < n; i++) { lo = Math.min(lo, x[i]!); hi = Math.max(hi, x[i]!); mx += x[i]!; }
  if (hi - lo === 0) return null;
  // fit in a centred, scaled variable u = (x - m) / s for conditioning, then expand
  mx /= n;
  const s = (hi - lo) / 2;
  const p = degree + 1;
  const ata: number[][] = Array.from({ length: p }, () => new Array<number>(p).fill(0));
  const aty = new Array<number>(p).fill(0);
  for (let i = 0; i < n; i++) {
    const u = (x[i]! - mx) / s;
    const pw = [1];
    for (let k = 1; k < 2 * p; k++) pw.push(pw[k - 1]! * u);
    for (let a = 0; a < p; a++) { aty[a]! += pw[a]! * y[i]!; for (let b = 0; b < p; b++) ata[a]![b]! += pw[a + b]!; }
  }
  const inv = invert(ata);
  const cu = inv.map((row) => row.reduce((acc, v, j) => acc + v * aty[j]!, 0));
  // expand sum cu_k ((x - m)/s)^k into powers of x
  const out = new Array<number>(p).fill(0);
  for (let k = 0; k < p; k++) {
    for (let j = 0; j <= k; j++) {
      out[j]! += cu[k]! * binom(k, j) * (-mx) ** (k - j) / s ** k;
    }
  }
  return out;
}

function binom(n: number, k: number): number {
  let r = 1;
  for (let i = 1; i <= k; i++) r = (r * (n - k + i)) / i;
  return r;
}

/** Points for a scatter plot. Rows with NaN in X or Y, or rejected by `mask`,
 *  are left out; `rows` maps each point back to its table row. */
export class ScatterModel {
  readonly rows: Int32Array;
  readonly x: Float64Array;
  readonly y: Float64Array;
  readonly t: Float64Array;
  readonly color: Float64Array | null;

  constructor(table: Table, readonly xName: string, readonly yName: string, readonly colorName: string | null = null,
    mask?: ArrayLike<boolean | number>) {
    const xs = tableCol(table, xName); const ys = tableCol(table, yName);
    const rows: number[] = [];
    for (let i = 0; i < xs.length; i++) {
      if (Number.isNaN(xs[i]!) || Number.isNaN(ys[i]!)) continue;
      if (mask && !mask[i]) continue;
      rows.push(i);
    }
    this.rows = Int32Array.from(rows);
    this.x = Float64Array.from(rows, (r) => xs[r]!);
    this.y = Float64Array.from(rows, (r) => ys[r]!);
    this.t = Float64Array.from(rows, (r) => table.t[r]!);
    const cs = colorName ? tableCol(table, colorName) : null;
    this.color = cs ? Float64Array.from(rows, (r) => cs[r]!) : null;
  }

  get length(): number { return this.rows.length; }

  /** x, y, colour and point indices, thinned evenly to at most `limit` points. */
  points(limit = 20000): { x: Float64Array; y: Float64Array; color: Float64Array | null; pick: Int32Array } {
    const pick = decimatePoints(this.length, limit);
    const color = this.color;
    return {
      x: Float64Array.from(pick, (i) => this.x[i]!),
      y: Float64Array.from(pick, (i) => this.y[i]!),
      color: color ? Float64Array.from(pick, (i) => color[i]!) : null,
      pick,
    };
  }

  fit(degree = 1): Fit | null {
    const c = polyfit(this.x, this.y, degree);
    if (!c) return null;
    const f = new Fit(c, NaN, this.length);
    let my = 0;
    for (const v of this.y) my += v;
    my /= this.length;
    let ss = 0; let sr = 0;
    for (let i = 0; i < this.length; i++) { const r = this.y[i]! - f.at(this.x[i]!); sr += r * r; ss += (this.y[i]! - my) ** 2; }
    return new Fit(c, ss > 0 ? 1 - sr / ss : 1, this.length);
  }

  /** Point indices inside a rectangle dragged on the plot (corners in any order). */
  brush(x0: number, x1: number, y0: number, y1: number): Int32Array {
    const lx = Math.min(x0, x1); const hx = Math.max(x0, x1); const ly = Math.min(y0, y1); const hy = Math.max(y0, y1);
    const out: number[] = [];
    for (let i = 0; i < this.length; i++) {
      const x = this.x[i]!; const y = this.y[i]!;
      if (x >= lx && x <= hx && y >= ly && y <= hy) out.push(i);
    }
    return Int32Array.from(out);
  }

  brushTimes(x0: number, x1: number, y0: number, y1: number): Float64Array {
    return Float64Array.from(this.brush(x0, x1, y0, y1), (i) => this.t[i]!);
  }

  /** The brushed points as time spans; points closer than `gap` seconds join. */
  brushSpans(x0: number, x1: number, y0: number, y1: number, gap = 1.0): [number, number][] {
    const times = this.brushTimes(x0, x1, y0, y1);
    if (!times.length) return [];
    const out: [number, number][] = [];
    let a = times[0]!;
    for (let i = 1; i < times.length; i++) {
      if (times[i]! - times[i - 1]! > gap) { out.push([a, times[i - 1]!]); a = times[i]!; }
    }
    out.push([a, times[times.length - 1]!]);
    return out;
  }
}
