/**
 * The correlation finder: which address behaves like this signal?
 * Port of hondalog/mapping/correlate.py (candidates, rank, rank_shape,
 * rank_against_channel, find_value, changed_bits, bit_history).
 */

import { compileFormula } from './formula';
import type { ParsedMap } from './mapdoc';
import { decodeWith } from './mapdoc';
import { inMark, type SweepRecording } from './sweeps';

export const MIN_SAMPLES = 5;

export interface Candidate { addr: number; width: 1 | 2; endian: 'big' | 'little'; inverse: boolean }

export function candLabel(c: Candidate): string {
  const kind = c.width === 1 ? 'u8' : `u16${c.endian === 'big' ? 'be' : 'le'}`;
  return `0x${c.addr.toString(16).toUpperCase().padStart(2, '0')} ${kind}${c.inverse ? ' 1/x' : ''}`;
}

export interface Match {
  candidate: Candidate;
  score: number;
  r: number;
  rho: number;
  slope: number;
  offset: number;
  rmse: number;
  n: number;
  span: number;
  notes: string[];
}

const g7 = (v: number) => Number(v.toPrecision(7)).toString();
const g6 = (v: number) => Number(v.toPrecision(6)).toString();

/** The least-squares fit as map formula text. */
export function matchFormula(m: Match): string {
  if (Number.isNaN(m.slope)) return '';
  const s = m.candidate.inverse ? `${g7(m.slope)} / x + ${g6(m.offset)}` : `x * ${g7(m.slope)} + ${g6(m.offset)}`;
  return s.replace('+ -', '- ');
}

// ---- candidate series -----------------------------------------------------

export function candidates(rec: SweepRecording, widths: number[] = [1, 2], inverse = false):
  { cands: Candidate[]; values: number[][]; times: number[][] } {
  const read = new Set(rec.readAddresses());
  const cands: Candidate[] = [];
  const values: number[][] = [];
  const times: number[][] = [];
  const col = (a: number) => rec.mem.map((row) => (row[a]! >= 0 ? row[a]! : NaN));
  const tcol = (a: number) => rec.times.map((row) => row[a]!);
  for (const addr of [...read].sort((a, b) => a - b)) {
    if (widths.includes(1)) {
      cands.push({ addr, width: 1, endian: 'big', inverse: false });
      values.push(col(addr));
      times.push(tcol(addr));
    }
    if (widths.includes(2) && read.has(addr + 1)) {
      for (const endian of ['big', 'little'] as const) {
        const [hi, lo] = endian === 'big' ? [addr, addr + 1] : [addr + 1, addr];
        const h = col(hi), l = col(lo);
        cands.push({ addr, width: 2, endian, inverse: false });
        values.push(h.map((v, i) => v * 256 + l[i]!));
        times.push(tcol(addr));
      }
    }
  }
  if (inverse) {
    const n = cands.length;
    for (let j = 0; j < n; j++) {
      cands.push({ ...cands[j]!, inverse: true });
      values.push(values[j]!.map((v) => (v > 0 ? 1 / v : NaN)));
      times.push(times[j]!);
    }
  }
  return { cands, values, times };
}

/** Average ranks (ties share a rank). */
export function ranks(xs: number[]): number[] {
  const order = xs.map((_, i) => i).sort((a, b) => xs[a]! - xs[b]!);
  const out = new Array<number>(xs.length);
  let i = 0;
  while (i < order.length) {
    let j = i;
    while (j + 1 < order.length && xs[order[j + 1]!] === xs[order[i]!]) j++;
    const r = (i + j) / 2 + 1;
    for (let k = i; k <= j; k++) out[order[k]!] = r;
    i = j + 1;
  }
  return out;
}

export function pearson(x: number[], y: number[]): number {
  const n = x.length;
  const mx = x.reduce((a, b) => a + b, 0) / n, my = y.reduce((a, b) => a + b, 0) / n;
  let sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < n; i++) {
    const dx = x[i]! - mx, dy = y[i]! - my;
    sxy += dx * dy; sxx += dx * dx; syy += dy * dy;
  }
  const den = Math.sqrt(sxx * syy);
  return den > 0 ? sxy / den : 0;
}

export function interp(t: number, xs: number[], ys: number[]): number {
  if (t <= xs[0]!) return ys[0]!;
  if (t >= xs[xs.length - 1]!) return ys[ys.length - 1]!;
  let lo = 0, hi = xs.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xs[mid]! <= t) lo = mid; else hi = mid;
  }
  const x0 = xs[lo]!, x1 = xs[hi]!;
  return x1 === x0 ? ys[hi]! : ys[lo]! + ((t - x0) / (x1 - x0)) * (ys[hi]! - ys[lo]!);
}

export function polyfit1(x: number[], y: number[]): [number, number] {
  const n = x.length;
  const mx = x.reduce((a, b) => a + b, 0) / n, my = y.reduce((a, b) => a + b, 0) / n;
  let sxy = 0, sxx = 0;
  for (let i = 0; i < n; i++) { sxy += (x[i]! - mx) * (y[i]! - my); sxx += (x[i]! - mx) ** 2; }
  const slope = sxx > 0 ? sxy / sxx : 0;
  return [slope, my - slope * mx];
}

export interface RankOptions {
  widths?: number[];
  inverse?: boolean;
  method?: 'pearson' | 'spearman' | 'both';
  top?: number;
  minSpan?: number;
  exclude?: Iterable<number>;
}

const key = (c: Candidate) => `${c.addr}|${c.width}|${c.endian}|${c.inverse}`;

/** Rank every candidate address against a reference signal. */
export function rank(rec: SweepRecording, refT: number[], refV: number[], opts: RankOptions = {}): Match[] {
  const { widths = [1, 2], inverse = false, method = 'both', top = 20, minSpan = 3.0 } = opts;
  let pairs = refT.map((t, i) => [t, refV[i]!] as [number, number]).filter(([t, v]) => Number.isFinite(t) && Number.isFinite(v));
  if (pairs.length < 2) return [];
  pairs = pairs.map((p, i) => [p, i] as const).sort((a, b) => a[0][0] - b[0][0] || a[1] - b[1]).map(([p]) => p);
  const rt = pairs.map((p) => p[0]), rv = pairs.map((p) => p[1]);
  const { cands, values, times } = candidates(rec, widths, inverse);
  const skip = new Set(opts.exclude ?? []);
  const out: Match[] = [];
  const byKey = new Map<string, Match>();
  cands.forEach((cand, j) => {
    if (skip.has(cand.addr) || (cand.width === 2 && skip.has(cand.addr + 1))) return;
    const xs: number[] = [], ts: number[] = [];
    values[j]!.forEach((v, i) => {
      const t = times[j]![i]!;
      if (Number.isFinite(v) && t >= rt[0]! && t <= rt[rt.length - 1]!) { xs.push(v); ts.push(t); }
    });
    if (xs.length < MIN_SAMPLES) return;
    const ys = ts.map((t) => interp(t, rt, rv));
    const span = Math.max(...xs) - Math.min(...xs);
    if (span === 0 || Math.max(...ys) - Math.min(...ys) === 0) return;
    const r = pearson(xs, ys);
    const rho = pearson(ranks(xs), ranks(ys));
    const strength = method === 'pearson' ? Math.abs(r) : method === 'spearman' ? Math.abs(rho) : Math.max(Math.abs(r), Math.abs(rho));
    const inv = xs.map((v) => 1 / v);
    const rawSpan = cand.inverse ? Math.max(...inv) - Math.min(...inv) : span;
    const weight = minSpan > 0 ? Math.min(1, rawSpan / minSpan) : 1;
    const [slope, offset] = polyfit1(xs, ys);
    const rmse = Math.sqrt(xs.reduce((a, x, i) => a + (slope * x + offset - ys[i]!) ** 2, 0) / xs.length);
    const m: Match = { candidate: cand, score: strength * weight, r, rho, slope, offset, rmse, n: xs.length, span: rawSpan, notes: [] };
    if (weight < 1) m.notes.push(`barely moves (${Number(rawSpan.toPrecision(6))} counts)`);
    out.push(m);
    byKey.set(key(cand), m);
  });
  preferSimple(out, byKey);
  const r6 = (v: number) => Math.round(v * 1e6) / 1e6;
  out.sort((a, b) => (r6(b.score) - r6(a.score)) || (r6(Math.abs(b.r)) - r6(Math.abs(a.r)))
    || (a.candidate.width - b.candidate.width) || (a.candidate.addr - b.candidate.addr));
  return top ? out.slice(0, top) : out;
}

function preferSimple(matches: Match[], byKey: Map<string, Match>): void {
  const hex = (a: number) => `0x${a.toString(16).toUpperCase().padStart(2, '0')}`;
  for (const m of matches) {
    const c = m.candidate;
    if (c.width !== 2) continue;
    const highAddr = c.endian === 'big' ? c.addr : c.addr + 1;
    const high = byKey.get(key({ addr: highAddr, width: 1, endian: 'big', inverse: c.inverse }));
    if (!high) {
      const lowAddr = c.endian === 'big' ? c.addr + 1 : c.addr;
      const low = byKey.get(key({ addr: lowAddr, width: 1, endian: 'big', inverse: c.inverse }));
      if (low && low.score >= m.score - 1e-9) {
        m.notes.push(`high byte never changes: same as u8 at ${hex(low.candidate.addr)}`);
        m.score *= 0.99;
      }
      continue;
    }
    const un16 = 1 - Math.min(1, m.score) ** 2;
    const un8 = 1 - Math.min(1, high.score) ** 2;
    if (un16 >= 0.5 * un8) {
      m.notes.push(`no better than u8 at ${hex(highAddr)}`);
      m.score = Math.min(m.score, high.score) * 0.995;
    }
  }
}

/** Rank against a channel decoded from the same sweeps; its own bytes are left out. */
export function rankAgainstChannel(rec: SweepRecording, map: ParsedMap, name: string, opts: RankOptions = {}): Match[] {
  const ch = map.channels.find((c) => c.name === name);
  if (!ch) throw new Error(`no channel named ${JSON.stringify(name)}`);
  const t: number[] = [], v: number[] = [];
  for (let i = 0; i < rec.length; i++) {
    const d = decodeWith(map, rec.memAt(i))[name];
    t.push(rec.times[i]![ch.addr]!);
    v.push(d === null || d === undefined ? NaN : d);
  }
  const exclude = opts.exclude ?? Array.from({ length: ch.length }, (_, k) => ch.addr + k);
  return rank(rec, t, v, { ...opts, exclude });
}

/** Rank against "rises / falls steadily between t0 and t1". */
export function rankShape(rec: SweepRecording, shape: 'rise' | 'fall', t0: number, t1: number, opts: RankOptions = {}): Match[] {
  if (shape !== 'rise' && shape !== 'fall') throw new Error("shape must be 'rise' or 'fall'");
  return rank(rec, [t0, t1], shape === 'rise' ? [0, 1] : [1, 0], { method: 'spearman', ...opts });
}

// ---- typed targets -----------------------------------------------------------

export interface ValueMatch { candidate: Candidate; error: number; decoded: number; spread: number; n: number }

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const n = s.length;
  return n % 2 ? s[(n - 1) / 2]! : (s[n / 2 - 1]! + s[n / 2]!) / 2;
}

/** Which addresses read `target` when decoded with `formula`? */
export function findValue(rec: SweepRecording, target: number, formula = 'x', tol = 0, relTol = 0.05,
  widths: number[] = [1, 2], top = 20): ValueMatch[] {
  const fn = compileFormula(formula);
  const limit = Math.max(tol, relTol * Math.abs(target));
  const { cands, values } = candidates(rec, widths);
  const out: ValueMatch[] = [];
  cands.forEach((cand, j) => {
    const xs = values[j]!.filter(Number.isFinite);
    if (!xs.length) return;
    const decoded = xs.map((x) => {
      const raw: number[] = [];
      for (let k = cand.width - 1; k >= 0; k--) raw.push((x >> (8 * k)) & 0xff);
      try { return fn(x, raw); } catch { return NaN; }
    }).filter(Number.isFinite);
    if (!decoded.length) return;
    const err = median(decoded.map((d) => Math.abs(d - target)));
    if (err <= limit) out.push({ candidate: cand, error: err, decoded: median(decoded), spread: Math.max(...decoded) - Math.min(...decoded), n: decoded.length });
  });
  out.sort((a, b) => (a.error + a.spread) - (b.error + b.spread) || a.candidate.width - b.candidate.width || a.candidate.addr - b.candidate.addr);
  return top ? out.slice(0, top) : out;
}

// ---- bits -------------------------------------------------------------------

export interface BitChange { addr: number; bit: number; inside: number; outside: number; purity: number; nInside: number; nOutside: number }

export const bitFormula = (c: BitChange) => (c.inside ? `bit(x, ${c.bit})` : `1 - bit(x, ${c.bit})`);

/** (times, rows of 8 bits; column 0 is bit 0) for one byte, unread sweeps dropped. */
export function bitHistory(rec: SweepRecording, addr: number): { t: number[]; bits: number[][] } {
  const t: number[] = [], bits: number[][] = [];
  rec.mem.forEach((row, i) => {
    const v = row[addr]!;
    if (v < 0) return;
    t.push(rec.times[i]![addr]!);
    bits.push(Array.from({ length: 8 }, (_, b) => (v >> b) & 1));
  });
  return { t, bits };
}

/** Bits that are one way inside the marks named `label` and the other way the rest of the time. */
export function changedBits(rec: SweepRecording, label: string, minPurity = 0.9, guardS = 0, minEach = 2): BitChange[] {
  if (!rec.length) return [];
  const inside = rec.markMask(label);
  const out: BitChange[] = [];
  const near = (i: number, a: number) => guardS > 0 && rec.marks.some((m) => m.label === label
    && (Math.abs(rec.times[i]![a]! - m.t0) < guardS || (m.t1 !== null && Math.abs(rec.times[i]![a]! - m.t1) < guardS)));
  for (const addr of rec.readAddresses()) {
    const a: number[] = [], b: number[] = [];
    rec.mem.forEach((row, i) => {
      const v = row[addr]!;
      if (v < 0 || near(i, addr)) return;
      (inside[i]![addr] ? a : b).push(v);
    });
    if (a.length < minEach || b.length < minEach) continue;
    for (let bit = 0; bit < 8; bit++) {
      const inOnes = a.reduce((s, v) => s + ((v >> bit) & 1), 0) / a.length;
      const outOnes = b.reduce((s, v) => s + ((v >> bit) & 1), 0) / b.length;
      const inVal = inOnes >= 0.5 ? 1 : 0, outVal = outOnes >= 0.5 ? 1 : 0;
      if (inVal === outVal) continue;
      const fitIn = inVal ? inOnes : 1 - inOnes;
      const fitOut = outVal ? outOnes : 1 - outOnes;
      const purity = (fitIn * a.length + fitOut * b.length) / (a.length + b.length);
      if (Math.min(fitIn, fitOut) >= minPurity) out.push({ addr, bit, inside: inVal, outside: outVal, purity, nInside: a.length, nOutside: b.length });
    }
  }
  out.sort((x, y) => y.purity - x.purity || x.addr - y.addr || x.bit - y.bit);
  return out;
}

export { inMark };
