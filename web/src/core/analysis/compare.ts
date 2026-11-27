/**
 * Comparing two drives: line them up in time, overlay, difference, histogram
 * delta (port of hondalog/analysis/compare.py).
 */
import type { LoadedDrive } from '../types';
import type { FilterSet } from './filters';
import { Bins, TableHistogram, type Stat } from './histogram';
import { align, driveSpan, driveTable, getSeries, type Series } from './series';

/** Time at which the channel first crosses `level` (interpolated), or null. */
export function firstCrossing(series: { t: Float64Array; v: Float64Array }, level: number, rising = true, after?: number): number | null {
  const { t, v } = series;
  let i0 = 0;
  if (after !== undefined) while (i0 < t.length && t[i0]! < after) i0++;
  for (let i = i0; i < t.length - 1; i++) {
    const a = v[i]! >= level; const b = v[i + 1]! >= level;
    if (rising ? !a && b : a && !b) {
      const span = v[i + 1]! - v[i]!;
      const frac = span ? (level - v[i]!) / span : 0;
      return t[i]! + (t[i + 1]! - t[i]!) * frac;
    }
  }
  return null;
}

export type AlignHow = 'start' | 'marker' | 'trigger' | 'crossing';
export interface AlignOptions { channel?: string; level?: number; rising?: boolean; text?: string }

export function channelsOf(drive: LoadedDrive): string[] {
  return Object.keys(drive.series);
}

/** A reference moment in a drive: the first sample, the first marker (or one
 *  whose text contains `text`), the first trigger, or the first time
 *  `channel` crosses `level`. */
export function eventTime(drive: LoadedDrive, how: AlignHow = 'start', opts: AlignOptions = {}): number | null {
  if (how === 'start') return driveSpan(drive)[0];
  if (how === 'marker' || how === 'trigger') {
    const text = (opts.text ?? '').toLowerCase();
    const ev = [...drive.events].sort((a, b) => a.t - b.t)
      .find((e) => e.kind === how && e.text.toLowerCase().includes(text));
    return ev ? ev.t : null;
  }
  if (how === 'crossing') return firstCrossing(getSeries(drive, opts.channel ?? 'rpm'), opts.level ?? 0, opts.rising ?? true);
  throw new Error(`unknown alignment '${how as string}'`);
}

export interface Overlay { name: string; t: Float64Array; a: Float64Array; b: Float64Array }

export function overlayDiff(o: Overlay): Float64Array { return o.a.map((x, i) => x - o.b[i]!); }

export function overlaySummary(o: Overlay): { n: number; mean: number; rms: number; maxAbs: number } {
  let n = 0; let s = 0; let s2 = 0; let m = 0;
  for (let i = 0; i < o.a.length; i++) {
    const d = o.a[i]! - o.b[i]!;
    if (Number.isNaN(d)) continue;
    n++; s += d; s2 += d * d; m = Math.max(m, Math.abs(d));
  }
  return n ? { n, mean: s / n, rms: Math.sqrt(s2 / n), maxAbs: m } : { n: 0, mean: NaN, rms: NaN, maxAbs: NaN };
}

/** Drive B laid over drive A; `offset` is added to B's times. */
export class Comparison {
  constructor(readonly a: LoadedDrive, readonly b: LoadedDrive, public offset = 0) {}

  /** Set `offset` so the chosen event happens at the same time in both drives.
   *  Throws (changing nothing) if a drive lacks the event. */
  align(how: AlignHow = 'start', opts: AlignOptions = {}): number {
    const ta = eventTime(this.a, how, opts); const tb = eventTime(this.b, how, opts);
    if (ta === null || tb === null) {
      const which = ta === null && tb === null ? 'neither log has' : ta === null ? 'the first log has' : 'the second log has';
      throw new Error(`${which} no '${how}' event to align on`);
    }
    this.offset = ta - tb;
    return this.offset;
  }

  commonChannels(): string[] {
    const theirs = new Set(channelsOf(this.b));
    return channelsOf(this.a).filter((n) => theirs.has(n));
  }

  shifted(name: string): Series {
    const s = getSeries(this.b, name);
    return { ...s, t: s.t.map((x) => x + this.offset) };
  }

  overlay(name: string, how: 'hold' | 'linear' = 'linear'): Overlay {
    const a = getSeries(this.a, name);
    return { name, t: a.t, a: a.v, b: align(this.shifted(name), a.t, how) };
  }

  /** Per-cell A minus B, plus both histograms. */
  histogramDelta(x: string, y: string, z: string, xBins: Bins, yBins: Bins, stat: Stat = 'mean', minHits = 1,
    filters?: FilterSet): { delta: Float64Array; a: TableHistogram; b: TableHistogram } {
    const hists = [this.a, this.b].map((drive) => {
      const names = new Set([x, y, z, ...(filters ? filters.channels() : [])]);
      const table = driveTable(drive, [...names].sort());
      const mask = filters ? filters.mask(table) : undefined;
      return TableHistogram.fromTable(table, x, y, z, xBins, yBins, mask);
    }) as [TableHistogram, TableHistogram];
    return { delta: hists[0].delta(hists[1], stat, minHits), a: hists[0], b: hists[1] };
  }
}

export interface ChannelDiff { name: string; units: string; n: number; meanA: number; meanB: number; mean: number; rms: number; maxAbs: number }
export interface CompareResult { offset: number; alignedOn: AlignHow; rows: ChannelDiff[]; onlyA: string[]; onlyB: string[] }

/** Two drives side by side: per common channel, the difference after B is
 *  shifted so the chosen moment coincides. Falls back to the first sample. */
export function compareDrives(a: LoadedDrive, b: LoadedDrive, channels?: string[], how: AlignHow = 'start',
  opts: AlignOptions = {}): CompareResult {
  const cmp = new Comparison(a, b);
  let alignedOn = how;
  try { cmp.align(how, opts); } catch { cmp.align('start'); alignedOn = 'start'; }
  const common = cmp.commonChannels();
  const names = channels ? channels.filter((n) => common.includes(n)) : common;
  const rows = names.map((name): ChannelDiff => {
    const o = cmp.overlay(name);
    let n = 0; let sa = 0; let sb = 0;
    for (let i = 0; i < o.a.length; i++) {
      if (Number.isNaN(o.a[i]!) || Number.isNaN(o.b[i]!)) continue;
      n++; sa += o.a[i]!; sb += o.b[i]!;
    }
    const s = overlaySummary(o);
    return { name, units: getSeries(a, name).units ?? '', n, meanA: n ? sa / n : NaN, meanB: n ? sb / n : NaN, mean: s.mean, rms: s.rms, maxAbs: s.maxAbs };
  });
  const ca = channelsOf(a); const cb = channelsOf(b);
  return { offset: cmp.offset, alignedOn, rows, onlyA: ca.filter((n) => !cb.includes(n)), onlyB: cb.filter((n) => !ca.includes(n)) };
}
