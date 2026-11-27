/**
 * Time in cell: where the engine actually lives on the RPM x MAP grid (port
 * of the observed map behind hondalog/ui/screens/map3d.py: the default
 * 20 x 10 grid of ObservedMap in hondalog/ui/wired.py, nearest break point,
 * rows with rpm >= 300).
 */
import type { LoadedDrive } from '../../core/types';

export const RPM_AXIS = Array.from({ length: 20 }, (_, i) => Math.round((500 + (i * 7000) / 19) / 50) * 50);
export const MAP_AXIS = Array.from({ length: 10 }, (_, j) => Math.round(17 + (j * 84) / 9));

export type CellKey = 'hits' | 'inj' | 'ign' | 'trim';
export const CELL_KEYS: Array<{ key: CellKey; label: string; units: string; range?: [number, number]; decimals: number }> = [
  { key: 'hits', label: 'Samples', units: '', decimals: 0 },
  { key: 'inj', label: 'Injector ms', units: 'ms', range: [0, 10], decimals: 2 },
  { key: 'ign', label: 'Ignition °', units: '°BTDC', range: [-5, 45], decimals: 1 },
  { key: 'trim', label: 'Fuel trim %', units: '%', range: [-12, 12], decimals: 1 },
];

export interface CellMap {
  rpmAxis: number[];
  mapAxis: number[];
  /** [j][i]: j = MAP row, i = rpm column */
  hits: number[][];
  sum: Record<'inj' | 'ign' | 'trim', number[][]>;
  /** seconds spent in each cell (sample spacing, capped) */
  seconds: number[][];
  total: number;
}

export function nearest(axis: readonly number[], v: number): number {
  let best = 0, bd = Infinity;
  for (let i = 0; i < axis.length; i++) {
    const d = Math.abs(axis[i]! - v);
    if (d < bd) { bd = d; best = i; }
  }
  return best;
}

const zeros = (ny: number, nx: number) => Array.from({ length: ny }, () => new Array<number>(nx).fill(0));

function holdAt(src: { t: ArrayLike<number>; v: ArrayLike<number> } | undefined, t: number, state: { j: number }): number {
  if (!src || !src.t.length) return NaN;
  while (state.j + 1 < src.t.length && src.t[state.j + 1]! <= t) state.j++;
  return src.t[state.j]! <= t && t - src.t[state.j]! <= 2 ? src.v[state.j]! : NaN;
}

export function buildCells(drive: Pick<LoadedDrive, 'series'> | null, t0 = -Infinity, t1 = Infinity,
  rpmAxis = RPM_AXIS, mapAxis = MAP_AXIS): CellMap {
  const ny = mapAxis.length, nx = rpmAxis.length;
  const out: CellMap = {
    rpmAxis, mapAxis, hits: zeros(ny, nx), seconds: zeros(ny, nx), total: 0,
    sum: { inj: zeros(ny, nx), ign: zeros(ny, nx), trim: zeros(ny, nx) },
  };
  const s = drive?.series;
  const rpm = s?.rpm;
  if (!s || !rpm) return out;
  const st = { map: { j: 0 }, inj: { j: 0 }, ign: { j: 0 }, stft: { j: 0 }, ltft: { j: 0 } };
  for (let k = 0; k < rpm.t.length; k++) {
    const t = rpm.t[k]!, r = rpm.v[k]!;
    if (t < t0 || t > t1) continue;
    const kpa = holdAt(s.map, t, st.map);
    if (!Number.isFinite(r) || !Number.isFinite(kpa) || r < 300) continue;
    const i = nearest(rpmAxis, r), j = nearest(mapAxis, kpa);
    out.hits[j]![i]!++;
    out.total++;
    const dt = k + 1 < rpm.t.length ? Math.min(0.5, rpm.t[k + 1]! - t) : 0;
    out.seconds[j]![i]! += dt;
    const val = (x: number) => (Number.isFinite(x) ? x : 0);
    out.sum.inj[j]![i]! += val(holdAt(s.inj, t, st.inj));
    out.sum.ign[j]![i]! += val(holdAt(s.ign, t, st.ign));
    out.sum.trim[j]![i]! += val(holdAt(s.stft, t, st.stft)) + val(holdAt(s.ltft, t, st.ltft));
  }
  return out;
}

/** The value a cell shows: samples, or the mean (NaN where no samples). */
export function cellValue(cells: CellMap, key: CellKey, j: number, i: number): number {
  const h = cells.hits[j]![i]!;
  if (key === 'hits') return h;
  return h ? cells.sum[key][j]![i]! / h : NaN;
}

export function coverage(cells: CellMap): { seen: number; total: number } {
  let seen = 0;
  for (const row of cells.hits) for (const h of row) if (h > 0) seen++;
  return { seen, total: cells.rpmAxis.length * cells.mapAxis.length };
}
