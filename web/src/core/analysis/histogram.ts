/**
 * Table histogram: bin two channels (say rpm and MAP) and summarise a third
 * per cell; plus time-in-cell (port of hondalog/analysis/histogram.py).
 *
 * Grids are Float64Array in row-major order, shape (ny, nx): row = Y cell,
 * column = X cell; index with `iy * nx + ix` (or `cell()`).
 */
import { median, searchSorted, tableCol, type Table } from './series';

export const STATS = ['mean', 'min', 'max', 'count', 'std', 'sum', 'time'] as const;
export type Stat = (typeof STATS)[number];

export class Bins {
  private constructor(readonly edges: readonly number[], readonly centres: readonly number[]) {}

  get length(): number { return this.centres.length; }

  static fromEdges(edges: readonly number[]): Bins {
    const e = edges.map(Number);
    if (e.length < 2 || e.some((x, i) => i > 0 && !(x > e[i - 1]!))) throw new Error('bin edges must be at least two rising numbers');
    return new Bins(e, e.slice(1).map((b, i) => (e[i]! + b) / 2));
  }

  /** Cells centred on table breakpoints (an ECU table's rpm or MAP axis).
   *  Boundaries are halfway between neighbours. With `clamp` the outer cells
   *  take everything beyond them, the way an ECU clamps its lookup. */
  static fromBreakpoints(points: readonly number[], clamp = true): Bins {
    const p = points.map(Number);
    if (p.length < 2 || p.some((x, i) => i > 0 && !(x > p[i - 1]!))) throw new Error('breakpoints must be at least two rising numbers');
    const mids = p.slice(1).map((b, i) => (p[i]! + b) / 2);
    const edges = clamp
      ? [-Infinity, ...mids, Infinity]
      : [p[0]! - (mids[0]! - p[0]!), ...mids, p[p.length - 1]! + (p[p.length - 1]! - mids[mids.length - 1]!)];
    return new Bins(edges, p);
  }

  /** Cells for a ROM table axis: anything with numeric `values` (or an array).
   *  Throws when the axis has no numeric breakpoints (`numeric === false`). */
  static fromAxis(axis: { values: readonly number[]; numeric?: boolean } | readonly number[], clamp = true): Bins {
    if (!Array.isArray(axis) && (axis as { numeric?: boolean }).numeric === false) {
      throw new Error('this axis has no numeric breakpoints in the definition');
    }
    const values = Array.isArray(axis) ? (axis as readonly number[]) : (axis as { values: readonly number[] }).values;
    return Bins.fromBreakpoints(values, clamp);
  }

  static linear(lo: number, hi: number, n: number): Bins {
    return Bins.fromEdges(Array.from({ length: n + 1 }, (_, i) => lo + ((hi - lo) * i) / n));
  }

  /** Cell number per value; -1 for NaN and values outside every cell. */
  index(values: ArrayLike<number>): Int32Array {
    const out = new Int32Array(values.length);
    const n = this.length;
    const top = this.edges[this.edges.length - 1]!;
    for (let k = 0; k < values.length; k++) {
      const x = values[k]!;
      let i = searchSorted(this.edges, x, 'right') - 1;
      if (x === top) i = n - 1;
      out[k] = Number.isNaN(x) || i < 0 || i >= n ? -1 : i;
    }
    return out;
  }

  equals(other: Bins): boolean {
    return this.edges.length === other.edges.length && this.edges.every((e, i) => e === other.edges[i])
      && this.centres.every((c, i) => c === other.centres[i]);
  }
}

const range = (a: number, b: number, s: number) => Array.from({ length: Math.floor((b - a) / s) + 1 }, (_, i) => a + i * s);
/** Generic axes, for when no ROM definition is loaded. */
export const RPM_500 = Bins.fromBreakpoints(range(500, 7500, 500));
export const RPM_250 = Bins.fromBreakpoints(range(500, 7500, 250));
export const MAP_KPA_10 = Bins.fromBreakpoints(range(20, 100, 10));
export const MAP_KPA_5 = Bins.fromBreakpoints(range(15, 105, 5));
export const TPS_10 = Bins.fromBreakpoints(range(0, 100, 10));
export const PRESETS: Record<string, Bins> = {
  'rpm 500': RPM_500, 'rpm 250': RPM_250, 'map 10 kPa': MAP_KPA_10, 'map 5 kPa': MAP_KPA_5, 'tps 10 %': TPS_10,
};

/** (x bins, y bins) of a ROM table: anything with axis("x") / axis("y"). */
export function tableBins(editor: { axis(which: 'x' | 'y'): { values: readonly number[]; numeric?: boolean } }, clamp = true): [Bins, Bins] {
  return [Bins.fromAxis(editor.axis('x'), clamp), Bins.fromAxis(editor.axis('y'), clamp)];
}

/** Seconds each row stands for (zero-order hold to the next row), capped at
 *  `cap` (default three median periods) so a gap in the log is not counted. */
export function holdWeights(t: Float64Array, cap?: number): Float64Array {
  const n = t.length;
  const w = new Float64Array(n);
  if (n < 2) return w;
  const d = new Float64Array(n - 1);
  for (let i = 0; i < n - 1; i++) d[i] = t[i + 1]! - t[i]!;
  const lim = cap ?? 3 * median(d);
  for (let i = 0; i < n - 1; i++) w[i] = Math.min(d[i]!, lim);
  w[n - 1] = Math.min(median(d), lim);
  return w;
}

export interface HistogramOptions {
  mask?: ArrayLike<boolean | number>;
  t?: Float64Array;
  /** seconds per row for the "time" statistic; default holdWeights(t) */
  weights?: Float64Array;
}

/** Per-cell statistics of z binned by x and y. `rows(ix, iy)` gives the
 *  source row indices of a cell, in time order, so the UI can jump to them. */
export class TableHistogram {
  readonly nx: number;
  readonly ny: number;
  readonly count: Float64Array;
  readonly sum: Float64Array;
  readonly mean: Float64Array;
  readonly min: Float64Array;
  readonly max: Float64Array;
  readonly std: Float64Array;
  /** seconds spent in each cell (time-in-cell), 0 when no time base */
  readonly time: Float64Array;
  readonly samples: number;
  readonly t?: Float64Array;
  private readonly cellRows: number[][];

  constructor(x: ArrayLike<number>, y: ArrayLike<number>, z: ArrayLike<number>,
    readonly xBins: Bins, readonly yBins: Bins, opts: HistogramOptions = {}) {
    this.nx = xBins.length; this.ny = yBins.length;
    this.t = opts.t;
    const ncell = this.nx * this.ny;
    const ix = xBins.index(x); const iy = yBins.index(y);
    const weights = opts.weights ?? (opts.t ? holdWeights(opts.t) : undefined);
    this.cellRows = Array.from({ length: ncell }, () => []);
    const total = new Float64Array(ncell); const sq = new Float64Array(ncell);
    const lo = new Float64Array(ncell).fill(NaN); const hi = new Float64Array(ncell).fill(NaN);
    const time = new Float64Array(ncell);
    let samples = 0;
    for (let r = 0; r < z.length; r++) {
      const zx = z[r]!;
      if (ix[r]! < 0 || iy[r]! < 0 || Number.isNaN(zx)) continue;
      if (opts.mask && !opts.mask[r]) continue;
      const c = iy[r]! * this.nx + ix[r]!;
      this.cellRows[c]!.push(r);
      total[c]! += zx; sq[c]! += zx * zx;
      lo[c] = Number.isNaN(lo[c]!) ? zx : Math.min(lo[c]!, zx);
      hi[c] = Number.isNaN(hi[c]!) ? zx : Math.max(hi[c]!, zx);
      if (weights) time[c]! += weights[r]!;
      samples++;
    }
    this.count = new Float64Array(ncell); this.sum = new Float64Array(ncell).fill(NaN);
    this.mean = new Float64Array(ncell).fill(NaN); this.std = new Float64Array(ncell).fill(NaN);
    for (let c = 0; c < ncell; c++) {
      const n = this.cellRows[c]!.length;
      this.count[c] = n;
      if (n) {
        const m = total[c]! / n;
        this.sum[c] = total[c]!; this.mean[c] = m;
        this.std[c] = Math.sqrt(Math.max(sq[c]! / n - m * m, 0));
      }
    }
    this.min = lo; this.max = hi; this.time = time; this.samples = samples;
  }

  static fromTable(table: Table, x: string, y: string, z: string, xBins: Bins, yBins: Bins,
    mask?: ArrayLike<boolean | number>): TableHistogram {
    return new TableHistogram(tableCol(table, x), tableCol(table, y), tableCol(table, z), xBins, yBins, { mask, t: table.t });
  }

  /** z binned on a ROM table's own axes; `fallback` bins when it has none. */
  static forTable(table: Table, editor: Parameters<typeof tableBins>[0], z: string, x = 'rpm', y = 'map',
    mask?: ArrayLike<boolean | number>, fallback?: [Bins, Bins]): TableHistogram {
    let bins: [Bins, Bins];
    try { bins = tableBins(editor); } catch (e) { if (!fallback) throw e; bins = fallback; }
    return TableHistogram.fromTable(table, x, y, z, bins[0], bins[1], mask);
  }

  cell(ix: number, iy: number): number { return iy * this.nx + ix; }

  /** One statistic as a grid. Cells with fewer than `minHits` samples are NaN
   *  (0 for "count" and "time"). */
  stat(name: Stat = 'mean', minHits = 1): Float64Array {
    if (!(STATS as readonly string[]).includes(name)) {
      throw new Error(`unknown statistic '${name}'; choose from ${STATS.join(', ')}`);
    }
    const grid = this[name];
    const out = new Float64Array(grid.length);
    for (let c = 0; c < grid.length; c++) {
      if (name === 'count' || name === 'time') out[c] = this.count[c]! >= minHits ? grid[c]! : 0;
      else out[c] = this.count[c]! >= Math.max(1, minHits) ? grid[c]! : NaN;
    }
    return out;
  }

  rows(ix: number, iy: number): number[] { return this.cellRows[this.cell(ix, iy)] ?? []; }

  times(ix: number, iy: number): Float64Array {
    if (!this.t) throw new Error('histogram was built without a time column');
    const t = this.t;
    return Float64Array.from(this.rows(ix, iy), (r) => t[r]!);
  }

  /** This minus `other` per cell; NaN where either side has too few samples. */
  delta(other: TableHistogram, stat: Stat = 'mean', minHits = 1): Float64Array {
    if (!this.xBins.equals(other.xBins) || !this.yBins.equals(other.yBins)) {
      throw new Error('histograms must use the same bins to be compared');
    }
    const a = this.stat(stat, minHits); const b = other.stat(stat, minHits);
    return a.map((v, i) => v - b[i]!);
  }

  /** [low, high] of the visible cells, for scaling the colour map. */
  colorRange(stat: Stat = 'mean', minHits = 1): [number, number] {
    const g = this.stat(stat, minHits);
    if (stat === 'count' || stat === 'time') return [0, g.length ? Math.max(...g) : 0];
    let lo = Infinity; let hi = -Infinity;
    for (const v of g) if (!Number.isNaN(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    return Number.isFinite(lo) ? [lo, hi] : [0, 0];
  }
}
