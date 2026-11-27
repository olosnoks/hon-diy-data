/**
 * The correlation finder: which address behaves like this signal?
 * (hondalog/mapping/correlate.py and the SweepRecording of sweeps.py.)
 *
 * Every address is tried as an unsigned byte and as a 16-bit value in both
 * byte orders (optionally as 1/x, for ECUs that store a period). Each
 * candidate is compared with a reference and the list comes back ranked,
 * with a least-squares formula suggestion. Also: findValue (which address
 * reads 3000 rpm right now through a formula?) and changedBits (which bits
 * flipped only while the A/C was on?). Pure functions, no I/O.
 */

import { compileFormula, MapError, type AddressMap } from '../addressmap';

export const UNREAD = -1;
export const MIN_SAMPLES = 5;

// ---------------------------------------------------------------- recording

/** A labelled stretch of time: a session phase, or "A/C on". */
export interface Mark {
  label: string;
  t0: number;
  t1: number | null;
}

export function markContains(mark: Mark, t: number): boolean {
  const end = mark.t1 === null ? Infinity : mark.t1;
  return t >= mark.t0 && t <= end;
}

/**
 * N sweeps of the 256-byte address space. mem[i][a] is -1 where a byte was
 * not read; times[i][a] is when the chunk holding that byte arrived.
 */
export class SweepRecording {
  mem: Int16Array[] = [];
  times: Float64Array[] = [];
  marks: Mark[] = [];
  meta: Record<string, unknown> = {};

  constructor(mem: Int16Array[] = [], times: Float64Array[] = [], marks: Mark[] = [], meta: Record<string, unknown> = {}) {
    this.mem = mem;
    this.times = times;
    this.marks = marks;
    this.meta = meta;
  }

  get length(): number {
    return this.mem.length;
  }

  append(mem: ArrayLike<number>, times: ArrayLike<number>): void {
    this.mem.push(Int16Array.from(mem));
    this.times.push(Float64Array.from(times));
  }

  /** One time per sweep: when its last chunk arrived. */
  get t(): number[] {
    return this.times.map((row) => row.reduce((a, b) => Math.max(a, b), -Infinity));
  }

  /** Addresses that were read at least once. */
  readAddresses(): number[] {
    const out: number[] = [];
    for (let a = 0; a < 256; a++) if (this.mem.some((row) => row[a]! >= 0)) out.push(a);
    return out;
  }

  /** Sweep i as {address: byte}, the form AddressMap.decode takes. */
  memAt(i: number): Map<number, number> {
    const row = this.mem[i]!;
    const out = new Map<number, number>();
    for (let a = 0; a < 256; a++) if (row[a]! >= 0) out.set(a, row[a]!);
    return out;
  }

  /**
   * Decode every sweep with a map: {t, channel...}, NaN = no value. Each
   * channel also gets "<name>:t", the time its own bytes were read.
   */
  decode(amap: AddressMap): Record<string, number[]> {
    const out: Record<string, number[]> = { t: this.t };
    const rows = this.mem.map((_, i) => amap.decode(this.memAt(i)));
    for (const ch of amap.channels) {
      out[ch.name] = rows.map((r) => r[ch.name] ?? NaN);
      out[`${ch.name}:t`] = this.times.map((row) => row[ch.addr]!);
    }
    return out;
  }

  private keep(mask: boolean[]): SweepRecording {
    return new SweepRecording(this.mem.filter((_, i) => mask[i]), this.times.filter((_, i) => mask[i]),
      [...this.marks], { ...this.meta });
  }

  /** The sweeps that finished between t0 and t1. */
  window(t0: number, t1: number): SweepRecording {
    return this.keep(this.t.map((t) => t >= t0 && t <= t1));
  }

  /** The sweeps inside every mark with this label. */
  marked(label: string): SweepRecording {
    const ts = this.t;
    return this.keep(ts.map((t) => this.marks.some((m) => m.label === label && markContains(m, t))));
  }

  /** mask[i][a]: was reading (i, a) taken inside a mark with this label? */
  markMask(label: string): boolean[][] {
    const marks = this.marks.filter((m) => m.label === label);
    return this.times.map((row) => Array.from(row, (t) => marks.some((m) => markContains(m, t))));
  }
}

/** Build a recording from plain data: one {addr: byte} map or 256-list per sweep. */
export function fromRows(rows: (Map<number, number> | Record<number, number> | ArrayLike<number>)[], t: number[]): SweepRecording {
  const rec = new SweepRecording();
  rows.forEach((row, i) => {
    if (i >= t.length) return;
    const mem = new Int16Array(256).fill(UNREAD);
    if (row instanceof Map) for (const [a, v] of row) mem[a] = v;
    else if (typeof (row as ArrayLike<number>).length === 'number') {
      const arr = row as ArrayLike<number>;
      for (let a = 0; a < Math.min(256, arr.length); a++) mem[a] = arr[a]!;
    } else for (const [a, v] of Object.entries(row)) mem[Number(a)] = v as number;
    rec.append(mem, new Float64Array(256).fill(t[i]!));
  });
  return rec;
}

// ---------------------------------------------------------------- formatting

/** Python's format(x, '.Ng'). */
export function formatG(x: number, precision = 6, plus = false): string {
  const sign = plus && x >= 0 ? '+' : '';
  if (Number.isNaN(x)) return 'nan';
  if (!Number.isFinite(x)) return sign + (x > 0 ? 'inf' : '-inf');
  if (x === 0) return sign + (Object.is(x, -0) ? '-0' : '0');
  const p = Math.max(1, precision);
  const [mant = '0', expText = '0'] = x.toExponential(p - 1).split('e');
  const exp = parseInt(expText, 10);
  const strip = (s: string): string => (s.includes('.') ? s.replace(/0+$/, '').replace(/\.$/, '') : s);
  if (exp >= -4 && exp < p) return sign + strip(x.toFixed(Math.max(0, p - 1 - exp)));
  const e = `${exp < 0 ? '-' : '+'}${String(Math.abs(exp)).padStart(2, '0')}`;
  return `${sign}${strip(mant)}e${e}`;
}

function formatF(x: number, decimals: number, plus = false): string {
  if (Number.isNaN(x)) return 'nan';
  const s = x.toFixed(decimals);
  return plus && x >= 0 ? '+' + s : s;
}

function hex2(a: number): string {
  return a.toString(16).toUpperCase().padStart(2, '0');
}

// ---------------------------------------------------------------- candidates

export interface Candidate {
  addr: number;
  /** bytes: 1 or 2 */
  width: number;
  endian: 'big' | 'little';
  /** compared as 1/x */
  inverse: boolean;
}

export function candidate(addr: number, width = 1, endian: 'big' | 'little' = 'big', inverse = false): Candidate {
  return { addr, width, endian, inverse };
}

export function candidateLabel(c: Candidate): string {
  const kind = c.width === 1 ? 'u8' : `u16${c.endian === 'big' ? 'be' : 'le'}`;
  return `0x${hex2(c.addr)} ${kind}` + (c.inverse ? ' 1/x' : '');
}

/** addr/length/endian in the form a channel update takes. */
export function candidateFields(c: Candidate): { addr: number; length: number; endian: 'big' | 'little' } {
  return { addr: c.addr, length: c.width, endian: c.endian };
}

export interface Match {
  candidate: Candidate;
  /** 0..1, higher is better */
  score: number;
  /** Pearson correlation with the reference */
  r: number;
  /** Spearman (rank) correlation */
  rho: number;
  /** reference ~= slope * x + offset  (x or 1/x) */
  slope: number;
  offset: number;
  rmse: number;
  n: number;
  /** raw range seen (max - min) */
  span: number;
  notes: string[];
}

/** The least-squares fit as map formula text. */
export function matchFormula(m: Match): string {
  if (Number.isNaN(m.slope)) return '';
  if (m.candidate.inverse) return `${formatG(m.slope, 7)} / x + ${formatG(m.offset, 6)}`.replace('+ -', '- ');
  return `x * ${formatG(m.slope, 7)} + ${formatG(m.offset, 6)}`.replace('+ -', '- ');
}

export function matchText(m: Match): string {
  const formula = matchFormula(m);
  const fit = formula ? `  fit: ${formula} (rmse ${formatG(m.rmse, 3)})` : '';
  const note = m.notes.length ? `  [${m.notes.join('; ')}]` : '';
  return `${candidateLabel(m.candidate).padEnd(14)} score ${formatF(m.score, 3)}  r ${formatF(m.r, 3, true)}  `
    + `rho ${formatF(m.rho, 3, true)}  n ${m.n}${fit}${note}`;
}

interface CandidateSeries {
  cands: Candidate[];
  /** values[j][i]: candidate j at sweep i, NaN for unread */
  values: Float64Array[];
  times: Float64Array[];
}

/** All candidates with their raw series. A 16-bit value takes the read time of its first byte. */
export function candidates(rec: SweepRecording, widths: readonly number[] = [1, 2], inverse = false): CandidateSeries {
  const n = rec.length;
  const raw = (a: number): Float64Array => Float64Array.from(rec.mem, (row) => (row[a]! >= 0 ? row[a]! : NaN));
  const tcol = (a: number): Float64Array => Float64Array.from(rec.times, (row) => row[a]!);
  const cands: Candidate[] = [];
  const values: Float64Array[] = [];
  const times: Float64Array[] = [];
  const read = new Set(rec.readAddresses());
  for (const addr of [...read].sort((a, b) => a - b)) {
    if (widths.includes(1)) {
      cands.push(candidate(addr));
      values.push(raw(addr));
      times.push(tcol(addr));
    }
    if (widths.includes(2) && read.has(addr + 1)) {
      for (const endian of ['big', 'little'] as const) {
        const [hi, lo] = endian === 'big' ? [addr, addr + 1] : [addr + 1, addr];
        const h = raw(hi), l = raw(lo);
        cands.push(candidate(addr, 2, endian));
        values.push(Float64Array.from({ length: n }, (_, i) => h[i]! * 256 + l[i]!));
        times.push(tcol(addr));
      }
    }
  }
  if (inverse && cands.length) {
    const count = cands.length;
    for (let j = 0; j < count; j++) {
      const c = cands[j]!;
      cands.push(candidate(c.addr, c.width, c.endian, true));
      values.push(values[j]!.map((v) => (v > 0 ? 1 / v : NaN)));
      times.push(times[j]!);
    }
  }
  return { cands, values, times };
}

/** Average ranks (ties share a rank), as Spearman needs. */
function ranks(col: number[]): number[] {
  const order = col.map((v, i) => [v, i] as const).sort((a, b) => a[0] - b[0]);
  const out = new Array<number>(col.length);
  let i = 0;
  while (i < order.length) {
    let j = i;
    while (j + 1 < order.length && order[j + 1]![0] === order[i]![0]) j++;
    const rank = (i + 1 + j + 1) / 2;
    for (let k = i; k <= j; k++) out[order[k]![1]] = rank;
    i = j + 1;
  }
  return out;
}

function mean(x: number[]): number {
  return x.reduce((a, b) => a + b, 0) / x.length;
}

function pearson(x: number[], y: number[]): number {
  const mx = mean(x), my = mean(y);
  let sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < x.length; i++) {
    const dx = x[i]! - mx, dy = y[i]! - my;
    sxy += dx * dy;
    sxx += dx * dx;
    syy += dy * dy;
  }
  const den = Math.sqrt(sxx * syy);
  return den > 0 ? sxy / den : 0;
}

/** np.polyfit(x, y, 1) -> [slope, offset] */
function linearFit(x: number[], y: number[]): [number, number] {
  const mx = mean(x), my = mean(y);
  let sxy = 0, sxx = 0;
  for (let i = 0; i < x.length; i++) {
    sxy += (x[i]! - mx) * (y[i]! - my);
    sxx += (x[i]! - mx) ** 2;
  }
  const slope = sxx > 0 ? sxy / sxx : NaN;
  return [slope, my - slope * mx];
}

/** np.interp with sorted xp. */
function interp(x: number, xp: number[], fp: number[]): number {
  const n = xp.length;
  if (x <= xp[0]!) return fp[0]!;
  if (x >= xp[n - 1]!) return fp[n - 1]!;
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xp[mid]! <= x) lo = mid;
    else hi = mid;
  }
  const x0 = xp[lo]!, x1 = xp[hi]!;
  return x1 === x0 ? fp[hi]! : fp[lo]! + ((fp[hi]! - fp[lo]!) * (x - x0)) / (x1 - x0);
}

function ptp(x: number[]): number {
  let lo = Infinity, hi = -Infinity;
  for (const v of x) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return x.length ? hi - lo : 0;
}

function round6(x: number): number {
  return Math.round(x * 1e6) / 1e6;
}

export interface RankOptions {
  widths?: readonly number[];
  /** also try 1/x (OBD1 RPM is a period) */
  inverse?: boolean;
  /** "pearson" (straight line), "spearman" (any monotonic relation) or "both" */
  method?: 'pearson' | 'spearman' | 'both';
  top?: number;
  /** candidates whose raw value moved less than this are scored down */
  minSpan?: number;
  /** addresses to leave out (the reference's own bytes) */
  exclude?: Iterable<number>;
}

/**
 * Rank every candidate address against a reference signal (refT, refV in the
 * recording's clock). The reference is interpolated onto each candidate's
 * own read times.
 */
export function rank(rec: SweepRecording, refT: ArrayLike<number>, refV: ArrayLike<number>, opts: RankOptions = {}): Match[] {
  const { widths = [1, 2], inverse = false, method = 'both', top = 20, minSpan = 3.0 } = opts;
  let pairs: [number, number][] = [];
  for (let i = 0; i < Math.min(refT.length, refV.length); i++) {
    const t = refT[i]!, v = refV[i]!;
    if (Number.isFinite(t) && Number.isFinite(v)) pairs.push([t, v]);
  }
  if (pairs.length < 2) return [];
  pairs = pairs.map((p, i) => [p, i] as const).sort((a, b) => (a[0][0] - b[0][0]) || (a[1] - b[1])).map((p) => p[0]);
  const rt = pairs.map((p) => p[0]);
  const rv = pairs.map((p) => p[1]);
  const t0 = rt[0]!, t1 = rt[rt.length - 1]!;
  const { cands, values, times } = candidates(rec, widths, inverse);
  const skip = new Set(opts.exclude ?? []);
  const out: Match[] = [];
  const byKey = new Map<string, Match>();
  cands.forEach((cand, j) => {
    if (skip.has(cand.addr) || (cand.width === 2 && skip.has(cand.addr + 1))) return;
    const xs: number[] = [];
    const ts: number[] = [];
    const col = values[j]!, tcol = times[j]!;
    for (let i = 0; i < col.length; i++) {
      const v = col[i]!, t = tcol[i]!;
      if (Number.isFinite(v) && t >= t0 && t <= t1) {
        xs.push(v);
        ts.push(t);
      }
    }
    if (xs.length < MIN_SAMPLES) return;
    const ys = ts.map((t) => interp(t, rt, rv));
    const span = ptp(xs);
    if (span === 0 || ptp(ys) === 0) return;
    const r = pearson(xs, ys);
    const rho = pearson(ranks(xs), ranks(ys));
    const strength = method === 'pearson' ? Math.abs(r) : method === 'spearman' ? Math.abs(rho) : Math.max(Math.abs(r), Math.abs(rho));
    const rawSpan = cand.inverse ? ptp(xs.map((v) => 1 / v)) : span;
    const weight = minSpan > 0 ? Math.min(1, rawSpan / minSpan) : 1;
    const [slope, offset] = linearFit(xs, ys);
    const rmse = Math.sqrt(mean(xs.map((x, i) => (slope * x + offset - ys[i]!) ** 2)));
    const match: Match = { candidate: cand, score: strength * weight, r, rho, slope, offset, rmse, n: xs.length, span: rawSpan, notes: [] };
    if (weight < 1) match.notes.push(`barely moves (${formatG(rawSpan)} counts)`);
    out.push(match);
    byKey.set(key(cand.addr, cand.width, cand.endian, cand.inverse), match);
  });
  preferSimple(out, byKey);
  out.sort((a, b) => (round6(b.score) - round6(a.score)) || (round6(Math.abs(b.r)) - round6(Math.abs(a.r)))
    || (a.candidate.width - b.candidate.width) || (a.candidate.addr - b.candidate.addr));
  return top ? out.slice(0, top) : out;
}

function key(addr: number, width: number, endian: string, inverse: boolean): string {
  return `${addr},${width},${endian},${inverse}`;
}

/**
 * A 16-bit candidate must earn its second byte: unless it explains clearly
 * more than its own high byte alone does, the single byte is listed first.
 */
function preferSimple(matches: Match[], byKey: Map<string, Match>): void {
  for (const m of matches) {
    const c = m.candidate;
    if (c.width !== 2) continue;
    const highAddr = c.endian === 'big' ? c.addr : c.addr + 1;
    const high = byKey.get(key(highAddr, 1, 'big', c.inverse));
    if (!high) {
      const low = byKey.get(key(c.endian === 'big' ? c.addr + 1 : c.addr, 1, 'big', c.inverse));
      if (low && low.score >= m.score - 1e-9) {
        m.notes.push(`high byte never changes: same as u8 at 0x${hex2(low.candidate.addr)}`);
        m.score *= 0.99;
      }
      continue;
    }
    const unexplained16 = 1 - Math.min(1, m.score) ** 2;
    const unexplained8 = 1 - Math.min(1, high.score) ** 2;
    if (unexplained16 >= 0.5 * unexplained8) {
      m.notes.push(`no better than u8 at 0x${hex2(highAddr)}`);
      m.score = Math.min(m.score, high.score) * 0.995;
    }
  }
}

/**
 * Rank against a channel decoded from the same sweeps (ideally a verified
 * one). The channel's own bytes are left out of the result.
 */
export function rankAgainstChannel(rec: SweepRecording, amap: AddressMap, name: string, opts: RankOptions = {}): Match[] {
  const ch = amap.channel(name);
  if (!ch) throw new MapError(`no channel named '${name}'`);
  const decoded = rec.decode(amap);
  const exclude = opts.exclude ?? Array.from({ length: ch.length }, (_, i) => ch.addr + i);
  return rank(rec, decoded[`${name}:t`]!, decoded[name]!, { ...opts, exclude });
}

/**
 * Rank against an expected behaviour between t0 and t1 instead of a signal:
 * "rise" or "fall". Only the order matters, so the method defaults to Spearman.
 */
export function rankShape(rec: SweepRecording, shape: 'rise' | 'fall', t0: number, t1: number, opts: RankOptions = {}): Match[] {
  if (shape !== 'rise' && shape !== 'fall') throw new Error("shape must be 'rise' or 'fall'");
  const refV = shape === 'rise' ? [0, 1] : [1, 0];
  return rank(rec, [t0, t1], refV, { method: 'spearman', ...opts });
}

/** Rank against "different inside the marks with this label than outside". */
export function rankSteps(rec: SweepRecording, label: string, opts: RankOptions = {}): Match[] {
  if (!rec.length) return [];
  const all = new Set<number>();
  rec.mem.forEach((row, i) => row.forEach((v, a) => { if (v >= 0) all.add(rec.times[i]![a]!); }));
  const grid = [...all].sort((a, b) => a - b);
  const inside = grid.map((t) => (rec.marks.some((m) => m.label === label && markContains(m, t)) ? 1 : 0));
  return rank(rec, grid, inside, opts);
}

// ---------------------------------------------------------------- typed targets

export interface ValueMatch {
  candidate: Candidate;
  /** median |decoded - target| */
  error: number;
  /** median decoded value */
  decoded: number;
  /** how much the decoded value moved meanwhile */
  spread: number;
  n: number;
}

export function valueMatchText(m: ValueMatch): string {
  return `${candidateLabel(m.candidate).padEnd(14)} reads ${formatG(m.decoded, 4)} `
    + `(off by ${formatG(m.error, 3)}, moves ${formatG(m.spread, 3)}, n ${m.n})`;
}

function median(x: number[]): number {
  const s = [...x].sort((a, b) => a - b);
  const n = s.length;
  return n % 2 ? s[(n - 1) / 2]! : (s[n / 2 - 1]! + s[n / 2]!) / 2;
}

export interface FindValueOptions {
  formula?: string;
  tol?: number;
  relTol?: number;
  widths?: readonly number[];
  top?: number;
}

/**
 * Which addresses read `target` when decoded with `formula`? For a typed-in
 * reading (tach says 3000) taken while the real value was not changing.
 */
export function findValue(rec: SweepRecording, target: number, opts: FindValueOptions = {}): ValueMatch[] {
  const { formula = 'x', tol = 0, relTol = 0.05, widths = [1, 2], top = 20 } = opts;
  const fn = compileFormula(formula);
  const limit = Math.max(tol, relTol * Math.abs(target));
  const { cands, values } = candidates(rec, widths);
  const out: ValueMatch[] = [];
  cands.forEach((cand, j) => {
    const xs = Array.from(values[j]!).filter((v) => Number.isFinite(v));
    if (!xs.length) return;
    const decoded = xs.map((v) => decodeWith(fn, Math.trunc(v), cand)).filter((d) => Number.isFinite(d));
    if (!decoded.length) return;
    const mid = median(decoded);
    const err = median(decoded.map((d) => Math.abs(d - target)));
    if (err <= limit) out.push({ candidate: cand, error: err, decoded: mid, spread: ptp(decoded), n: decoded.length });
  });
  out.sort((a, b) => (a.error + a.spread - (b.error + b.spread)) || (a.candidate.width - b.candidate.width) || (a.candidate.addr - b.candidate.addr));
  return top ? out.slice(0, top) : out;
}

function decodeWith(fn: (x: number, b: ArrayLike<number>) => number, x: number, cand: Candidate): number {
  const raw = cand.width === 1 ? [x & 0xff] : [(x >> 8) & 0xff, x & 0xff];   // x is already in the right order
  try {
    return Number(fn(x, raw));
  } catch {
    return NaN;
  }
}

// ---------------------------------------------------------------- bits

export interface BitChange {
  addr: number;
  bit: number;
  /** the bit's usual value during the action */
  inside: number;
  /** and the rest of the time */
  outside: number;
  /** 0..1: share of readings that fit that story */
  purity: number;
  nInside: number;
  nOutside: number;
}

export function bitFormula(c: BitChange): string {
  return c.inside ? `bit(x, ${c.bit})` : `1 - bit(x, ${c.bit})`;
}

export function bitChangeText(c: BitChange): string {
  return `0x${hex2(c.addr)} bit ${c.bit}: ${c.outside} -> ${c.inside} during the `
    + `action (${Math.round(c.purity * 100)}% of ${c.nInside}+${c.nOutside} readings)`;
}

/** (times, bits[i][k]) for one byte; bit 0 first. Unread sweeps are dropped. */
export function bitHistory(rec: SweepRecording, addr: number): { t: number[]; bits: number[][] } {
  const t: number[] = [];
  const bits: number[][] = [];
  rec.mem.forEach((row, i) => {
    const v = row[addr]!;
    if (v < 0) return;
    t.push(rec.times[i]![addr]!);
    bits.push(Array.from({ length: 8 }, (_, k) => ((v & 0xff) >> k) & 1));
  });
  return { t, bits };
}

/**
 * The "what changed" filter: bits that are one way inside the marks named
 * `label` and the other way the rest of the time. guardS ignores readings
 * this close to a mark's start or end.
 */
export function changedBits(rec: SweepRecording, label: string, minPurity = 0.9, guardS = 0, minEach = 2): BitChange[] {
  if (!rec.length) return [];
  const inside = rec.markMask(label);
  const marks = rec.marks.filter((m) => m.label === label);
  const near = (t: number): boolean => guardS > 0 && marks.some((m) => Math.abs(t - m.t0) < guardS || (m.t1 !== null && Math.abs(t - m.t1) < guardS));
  const out: BitChange[] = [];
  for (const addr of rec.readAddresses()) {
    const a: number[] = [];
    const b: number[] = [];
    rec.mem.forEach((row, i) => {
      const v = row[addr]!;
      if (v < 0 || near(rec.times[i]![addr]!)) return;
      (inside[i]![addr] ? a : b).push(v);
    });
    if (a.length < minEach || b.length < minEach) continue;
    for (let bit = 0; bit < 8; bit++) {
      const inOnes = mean(a.map((v) => (v >> bit) & 1));
      const outOnes = mean(b.map((v) => (v >> bit) & 1));
      const inVal = inOnes >= 0.5 ? 1 : 0;
      const outVal = outOnes >= 0.5 ? 1 : 0;
      if (inVal === outVal) continue;
      const fitIn = inVal ? inOnes : 1 - inOnes;
      const fitOut = outVal ? outOnes : 1 - outOnes;
      const purity = (fitIn * a.length + fitOut * b.length) / (a.length + b.length);
      if (Math.min(fitIn, fitOut) >= minPurity) {
        out.push({ addr, bit, inside: inVal, outside: outVal, purity, nInside: a.length, nOutside: b.length });
      }
    }
  }
  out.sort((p, q) => (q.purity - p.purity) || (p.addr - q.addr) || (p.bit - q.bit));
  return out;
}

/** Addresses whose values inside the marked stretches never occur outside. */
export function changedBytes(rec: SweepRecording, label: string): number[] {
  const inside = rec.markMask(label);
  const out: number[] = [];
  for (const addr of rec.readAddresses()) {
    const a = new Set<number>();
    const b = new Set<number>();
    rec.mem.forEach((row, i) => {
      const v = row[addr]!;
      if (v >= 0) (inside[i]![addr] ? a : b).add(v);
    });
    if (a.size && b.size && ![...a].some((v) => b.has(v))) out.push(addr);
  }
  return out;
}

/** A [[channel]] table that promotes a found bit to a flag channel. */
export function flagChannelTable(name: string, change: BitChange, label = ''): Record<string, unknown> {
  return {
    name, label: label || name, addr: change.addr, length: 1, formula: bitFormula(change), kind: 'flag',
    status: 'unverified', source: 'memory scanner: bit that follows the marked action',
  };
}
