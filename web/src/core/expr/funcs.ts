/**
 * The function whitelist: everything an expression is allowed to call.
 *
 * * pure functions (``abs``, ``poly``, ``table`` ...): a scalar implementation;
 * * stateful helpers (``avg``, ``slope``, ``smooth`` ...): one small state
 *   object per call site, fed once per sample with ``update(t, x)``.
 *
 * Nothing here does I/O and nothing reaches JavaScript globals by name.
 * Semantics follow hondalog/expr/funcs.py (Python numbers), errors included.
 */

import {
  PyError, domain, fsum, pyDiv, pyPow, pyRound, pyTrunc, range, toBig,
} from './pynum';

type Scalar = (...args: number[]) => number;

// ---- pure, scalar -----------------------------------------------------------

function bit(x: number, n: number): number {
  const ni = pyTrunc(n);
  if (!(ni >= 0 && ni < 64)) throw new PyError('ValueError', 'bit index out of range');
  return Number((toBig(x) >> BigInt(ni)) & 1n);
}

function signed(x: number, bits = 8): number {
  let xi = toBig(x);
  const nb = pyTrunc(bits);
  if (!(nb >= 1 && nb <= 64)) throw new PyError('ValueError', 'bits out of range');
  const big = BigInt(nb);
  xi &= (1n << big) - 1n;
  return Number(xi & (1n << (big - 1n)) ? xi - (1n << big) : xi);
}

/** c0 + c1*x + c2*x^2 ... (Horner). */
function poly(x: number, ...coeffs: number[]): number {
  let acc = 0.0;
  for (let i = coeffs.length - 1; i >= 0; i--) acc = acc * x + coeffs[i]!;
  return acc;
}

function clamp(x: number, lo: number, hi: number): number {
  return x < lo ? lo : x > hi ? hi : x;
}

function sign(x: number): number {
  return (x > 0 ? 1 : 0) - (x < 0 ? 1 : 0);
}

/** Piecewise-linear lookup: table(x, x0, y0, x1, y1, ...), ends held. */
function table(x: number, ...pairs: number[]): number {
  const n = Math.floor(pairs.length / 2);
  if (x <= pairs[0]!) return pairs[1]!;
  for (let i = 1; i < n; i++) {
    const x1 = pairs[2 * i]!;
    if (x <= x1) {
      const x0 = pairs[2 * i - 2]!;
      const y0 = pairs[2 * i - 1]!;
      const y1 = pairs[2 * i + 1]!;
      return x1 === x0 ? y1 : y0 + pyDiv((y1 - y0) * (x - x0), x1 - x0);
    }
  }
  return pairs[pairs.length - 1]!;
}

function log(x: number): number {
  if (Number.isNaN(x)) return x;
  if (x <= 0) throw domain();
  return Math.log(x);
}

function log10(x: number): number {
  if (Number.isNaN(x)) return x;
  if (x <= 0) throw domain();
  return Math.log10(x);
}

/** Steinhart-Hart on an ADC count: degrees C from a pulled-up NTC divider. */
function thermistor(x: number, a: number, b: number, c: number, full = 255.0): number {
  const lnR = log(pyDiv(x, full - x));
  return pyDiv(1.0, a + b * lnR + c * pyPow(lnR, 3)) - 273.15;
}

function between(x: number, lo: number, hi: number): number {
  return lo <= x && x <= hi ? 1 : 0;
}

function round(x: number, digits = 0): number {
  const d = pyTrunc(digits);
  return digits ? pyRound(x, d) : pyRound(x);
}

function sqrt(x: number): number {
  if (x < 0) throw domain();
  return Math.sqrt(x);
}

function exp(x: number): number {
  const r = Math.exp(x);
  if (r === Infinity && Number.isFinite(x)) throw range();
  return r;
}

function trig(fn: (x: number) => number): Scalar {
  return (x: number) => {
    if (x === Infinity || x === -Infinity) throw domain();
    return fn(x);
  };
}

function floorCeil(fn: (x: number) => number): Scalar {
  return (x: number) => {
    pyTrunc(x);                        // math.floor(inf) / floor(nan) raise, as int() does
    return fn(x) + 0;                  // an int: no -0
  };
}

/** Python min()/max(): the first extreme argument wins; NaN as Python orders it. */
function minOf(...args: number[]): number {
  let acc = args[0]!;
  for (let i = 1; i < args.length; i++) if (args[i]! < acc) acc = args[i]!;
  return acc;
}

function maxOf(...args: number[]): number {
  let acc = args[0]!;
  for (let i = 1; i < args.length; i++) if (args[i]! > acc) acc = args[i]!;
  return acc;
}

export interface Pure {
  scalar: Scalar;
  minArgs: number;
  maxArgs: number | null;            // null = any number
  doc: string;
  constFrom: number | null;          // arguments from this position on must be constants
  pairs: boolean;                    // table(): the constants are ascending x, y pairs
}

function pure(scalar: Scalar, minArgs: number, maxArgs: number | null, doc: string,
  constFrom: number | null = null, pairs = false): Pure {
  return { scalar, minArgs, maxArgs, doc, constFrom, pairs };
}

export const PURE: Record<string, Pure> = {
  abs: pure(Math.abs, 1, 1, 'abs(x)'),
  min: pure(minOf, 2, null, 'min(a, b, ...) smallest argument'),
  max: pure(maxOf, 2, null, 'max(a, b, ...) largest argument'),
  round: pure(round, 1, 2, 'round(x[, digits])', 1),
  int: pure(pyTrunc, 1, 1, 'int(x) drops the fraction'),
  float: pure((x) => x, 1, 1, 'float(x)'),
  floor: pure(floorCeil(Math.floor), 1, 1, 'floor(x)'),
  ceil: pure(floorCeil(Math.ceil), 1, 1, 'ceil(x)'),
  sqrt: pure(sqrt, 1, 1, 'sqrt(x)'),
  exp: pure(exp, 1, 1, 'exp(x)'),
  ln: pure(log, 1, 1, 'ln(x) natural logarithm'),
  log: pure(log, 1, 1, 'log(x) natural logarithm'),
  log10: pure(log10, 1, 1, 'log10(x)'),
  pow: pure(pyPow, 2, 2, 'pow(x, y)'),
  sin: pure(trig(Math.sin), 1, 1, 'sin(x), radians'),
  cos: pure(trig(Math.cos), 1, 1, 'cos(x), radians'),
  sign: pure(sign, 1, 1, 'sign(x): -1, 0 or 1'),
  clamp: pure(clamp, 3, 3, 'clamp(x, lo, hi)'),
  between: pure(between, 3, 3, 'between(x, lo, hi): 1 when lo <= x <= hi'),
  bit: pure(bit, 2, 2, 'bit(x, n): bit n of x, 0 = least significant'),
  signed: pure(signed, 1, 2, "signed(x[, bits]) two's complement", 1),
  poly: pure(poly, 2, null, 'poly(x, c0, c1, ...) = c0 + c1*x + c2*x^2 ...'),
  table: pure(table, 5, null, 'table(x, x0, y0, x1, y1, ...) straight lines between points', 1, true),
  thermistor: pure(thermistor, 4, 5, 'thermistor(x, a, b, c[, full]) Steinhart-Hart on an ADC count'),
};

// ---- stateful ---------------------------------------------------------------

const RESYNC = 1024;       // recompute running sums this often so rounding cannot build up

/** One call site's memory. ``update`` returns null for 'not enough data'. */
export interface State {
  update(t: number, x: number): number | null;
  reset(): void;
}

/** A FIFO of numbers with O(1) push / shift (a deque without the rotation). */
class Queue {
  private buf: number[] = [];
  private head = 0;

  get length(): number {
    return this.buf.length - this.head;
  }

  push(x: number): void {
    this.buf.push(x);
  }

  first(): number {
    return this.buf[this.head]!;
  }

  last(): number {
    return this.buf[this.buf.length - 1]!;
  }

  at(i: number): number {
    return this.buf[this.head + i]!;
  }

  shift(): number {
    const x = this.buf[this.head++]!;
    if (this.head > 1024 && this.head * 2 > this.buf.length) {
      this.buf = this.buf.slice(this.head);
      this.head = 0;
    }
    return x;
  }

  pop(): number {
    const x = this.buf.pop()!;
    if (this.buf.length === this.head) this.clear();
    return x;
  }

  clear(): void {
    this.buf = [];
    this.head = 0;
  }

  *values(): Generator<number> {
    for (let i = this.head; i < this.buf.length; i++) yield this.buf[i]!;
  }
}

/** Samples from the last ``ms`` milliseconds as (t, x) pairs. */
abstract class Window implements State {
  protected readonly w: number;
  protected tq = new Queue();
  protected xq = new Queue();
  protected count = 0;

  constructor(ms: number) {
    if (!(ms > 0)) throw new PyError('ValueError', 'the time window must be more than 0 ms');
    this.w = ms / 1000.0;
  }

  reset(): void {
    this.tq.clear();
    this.xq.clear();
    this.count = 0;
    this.clearSums();
  }

  protected clearSums(): void {}

  /** Append; returns true when running sums must be rebuilt from scratch. */
  protected push(t: number, x: number): boolean {
    if (this.tq.length && t < this.tq.last()) this.reset();    // a seek or a new log
    this.tq.push(t);
    this.xq.push(x);
    this.count += 1;
    return this.count % RESYNC === 0;
  }

  abstract update(t: number, x: number): number | null;
}

/** avg(x, ms): mean of the samples in the window. */
export class Avg extends Window {
  private s = 0.0;

  protected override clearSums(): void {
    this.s = 0.0;
  }

  update(t: number, x: number): number {
    const resync = this.push(t, x);
    this.s += x;
    const lim = t - this.w;
    while (this.tq.first() < lim) {
      this.tq.shift();
      this.s -= this.xq.shift();
    }
    if (resync || this.xq.length === 1) this.s = fsum(this.xq.values());
    return this.s / this.xq.length;
  }
}

/** stdev(x, ms): population standard deviation over the window. */
export class Stdev extends Window {
  update(t: number, x: number): number {
    this.push(t, x);
    const lim = t - this.w;
    while (this.tq.first() < lim) {
      this.tq.shift();
      this.xq.shift();
    }
    const n = this.xq.length;
    const mean = fsum(this.xq.values()) / n;
    const sq: number[] = [];
    for (const v of this.xq.values()) sq.push((v - mean) ** 2);
    return Math.sqrt(fsum(sq) / n);
  }
}

/** slope(x, ms): least-squares slope over the window, in units per second. */
export class Slope extends Window {
  private t0 = 0.0;
  private st = 0.0;
  private sx = 0.0;
  private stt = 0.0;
  private stx = 0.0;

  protected override clearSums(): void {
    this.t0 = 0.0;
    this.st = this.sx = this.stt = this.stx = 0.0;
  }

  private rebuild(): void {
    const ts = [...this.tq.values()];
    const xs = [...this.xq.values()];
    const t0 = ts[0]!;
    this.t0 = t0;
    this.st = fsum(ts.map((t) => t - t0));
    this.sx = fsum(xs);
    this.stt = fsum(ts.map((t) => (t - t0) ** 2));
    this.stx = fsum(ts.map((t, i) => (t - t0) * xs[i]!));
  }

  update(t: number, x: number): number | null {
    const resync = this.push(t, x);
    if (this.xq.length === 1) {
      this.rebuild();
      return null;
    }
    let r = t - this.t0;
    this.st += r;
    this.sx += x;
    this.stt += r * r;
    this.stx += r * x;
    const lim = t - this.w;
    while (this.tq.first() < lim) {
      const ot = this.tq.shift();
      const ox = this.xq.shift();
      r = ot - this.t0;
      this.st -= r;
      this.sx -= ox;
      this.stt -= r * r;
      this.stx -= r * ox;
    }
    const n = this.xq.length;
    if (resync || n <= 2) this.rebuild();
    if (n < 2) return null;
    const den = n * this.stt - this.st * this.st;
    if (den <= 1e-12) return null;
    return (n * this.stx - this.st * this.sx) / den;
  }
}

/** Sliding minimum or maximum with a monotonic queue: O(1) per sample. */
abstract class Extreme extends Window {
  protected abstract readonly sign: number;

  update(t: number, x: number): number {
    const tq = this.tq;
    const xq = this.xq;
    if (tq.length && t < tq.last()) {
      tq.clear();
      xq.clear();
    }
    const key = x * this.sign;
    while (xq.length && xq.last() * this.sign >= key) {
      xq.pop();
      tq.pop();
    }
    tq.push(t);
    xq.push(x);
    const lim = t - this.w;
    while (tq.first() < lim) {
      tq.shift();
      xq.shift();
    }
    return xq.first();
  }
}

/** minw(x, ms): smallest value in the window. */
export class MinW extends Extreme {
  protected readonly sign = 1.0;
}

/** maxw(x, ms): largest value in the window. */
export class MaxW extends Extreme {
  protected readonly sign = -1.0;
}

function sampleCount(n: number, hi: number): number {
  const c = pyTrunc(n);
  if (c < 1 || c > hi) throw new PyError('ValueError', `the sample count must be 1 to ${hi}`);
  return c;
}

/** smooth(x, n): mean of the last n samples. */
export class Smooth implements State {
  private readonly maxlen: number;
  private q = new Queue();
  private s = 0.0;
  private count = 0;

  constructor(n: number) {
    this.maxlen = sampleCount(n, 100000);
  }

  reset(): void {
    this.q.clear();
    this.s = 0.0;
    this.count = 0;
  }

  update(_t: number, x: number): number {
    const q = this.q;
    if (q.length === this.maxlen) this.s -= q.shift();
    q.push(x);
    this.s += x;
    this.count += 1;
    if (this.count % RESYNC === 0) this.s = fsum(q.values());
    return this.s / q.length;
  }
}

/** median(x, n): median of the last n samples (kills single-sample spikes). */
export class Median implements State {
  private readonly maxlen: number;
  private q = new Queue();

  constructor(n: number) {
    this.maxlen = sampleCount(n, 1001);
  }

  reset(): void {
    this.q.clear();
  }

  update(_t: number, x: number): number {
    if (this.q.length === this.maxlen) this.q.shift();
    this.q.push(x);
    const s = [...this.q.values()].sort((a, b) => a - b);
    const mid = Math.floor(s.length / 2);
    return s.length % 2 ? s[mid]! : 0.5 * (s[mid - 1]! + s[mid]!);
  }
}

/** ema(x, ms): first-order low-pass with time constant ms. */
export class Ema implements State {
  private readonly tau: number;
  private y: number | null = null;
  private t = 0.0;

  constructor(ms: number) {
    if (!(ms > 0)) throw new PyError('ValueError', 'the time constant must be more than 0 ms');
    this.tau = ms / 1000.0;
  }

  reset(): void {
    this.y = null;
  }

  update(t: number, x: number): number {
    if (this.y === null || t < this.t) this.y = x;
    else this.y += (x - this.y) * (1.0 - Math.exp(-(t - this.t) / this.tau));
    this.t = t;
    return this.y;
  }
}

/** prev(x[, n]) and [x-n]: the value n valid samples ago. */
export class Lag implements State {
  private readonly maxlen: number;
  private q = new Queue();

  constructor(n = 1) {
    const c = pyTrunc(n);
    if (c < 1 || c > 10000) throw new PyError('ValueError', 'can look back 1 to 10000 samples');
    this.maxlen = c + 1;
  }

  reset(): void {
    this.q.clear();
  }

  update(_t: number, x: number): number | null {
    if (this.q.length === this.maxlen) this.q.shift();
    this.q.push(x);
    return this.q.length === this.maxlen ? this.q.first() : null;
  }
}

/** delta(x): change since the previous sample. */
export class Delta implements State {
  private last: number | null = null;

  reset(): void {
    this.last = null;
  }

  update(_t: number, x: number): number | null {
    const last = this.last;
    this.last = x;
    return last === null ? null : x - last;
  }
}

/** rate(x): change per second since the previous sample. */
export class Rate implements State {
  private last: [number, number] | null = null;

  reset(): void {
    this.last = null;
  }

  update(t: number, x: number): number | null {
    const last = this.last;
    this.last = [t, x];
    if (last === null || t <= last[0]) return null;
    return (x - last[1]) / (t - last[0]);
  }
}

/** integ(x): running integral of x over time in seconds (trapezoid). */
export class Integ implements State {
  private last: [number, number] | null = null;
  private acc = 0.0;

  reset(): void {
    this.last = null;
    this.acc = 0.0;
  }

  update(t: number, x: number): number {
    let last = this.last;
    if (last !== null && t < last[0]) {
      this.acc = 0.0;
      last = null;
    }
    if (last !== null) this.acc += 0.5 * (x + last[1]) * (t - last[0]);
    this.last = [t, x];
    return this.acc;
  }
}

/** held(cond, ms): 1 once cond has been true for ms without a break. */
export class Held implements State {
  private readonly w: number;
  private since: number | null = null;

  constructor(ms: number) {
    if (ms < 0) throw new PyError('ValueError', 'the hold time cannot be negative');
    this.w = ms / 1000.0;
  }

  reset(): void {
    this.since = null;
  }

  update(t: number, x: number): number {
    if (x === 0) {                     // Python truthiness: NaN counts as true
      this.since = null;
      return 0.0;
    }
    if (this.since === null || t < this.since) this.since = t;
    return t - this.since >= this.w ? 1.0 : 0.0;
  }
}

export interface Stateful {
  factory: (...params: number[]) => State;
  params: number;            // constant parameters after x: avg(x, ms) has 1
  optional: number;          // how many of those may be left out
  doc: string;
}

export const STATEFUL: Record<string, Stateful> = {
  avg: { factory: (ms) => new Avg(ms!), params: 1, optional: 0, doc: 'avg(x, ms) mean over the last ms milliseconds' },
  slope: { factory: (ms) => new Slope(ms!), params: 1, optional: 0, doc: 'slope(x, ms) rate of change per second over the last ms' },
  minw: { factory: (ms) => new MinW(ms!), params: 1, optional: 0, doc: 'minw(x, ms) smallest value in the last ms' },
  maxw: { factory: (ms) => new MaxW(ms!), params: 1, optional: 0, doc: 'maxw(x, ms) largest value in the last ms' },
  stdev: { factory: (ms) => new Stdev(ms!), params: 1, optional: 0, doc: 'stdev(x, ms) standard deviation over the last ms' },
  smooth: { factory: (n) => new Smooth(n!), params: 1, optional: 0, doc: 'smooth(x, n) mean of the last n samples' },
  median: { factory: (n) => new Median(n!), params: 1, optional: 0, doc: 'median(x, n) median of the last n samples' },
  ema: { factory: (ms) => new Ema(ms!), params: 1, optional: 0, doc: 'ema(x, ms) low-pass filter, time constant ms' },
  prev: { factory: (n) => new Lag(n ?? 1), params: 1, optional: 1, doc: 'prev(x[, n]) value n samples ago, same as [x-n]' },
  delta: { factory: () => new Delta(), params: 0, optional: 0, doc: 'delta(x) change since the previous sample' },
  rate: { factory: () => new Rate(), params: 0, optional: 0, doc: 'rate(x) change per second since the previous sample' },
  integ: { factory: () => new Integ(), params: 0, optional: 0, doc: 'integ(x) running integral over seconds' },
  held: { factory: (ms) => new Held(ms!), params: 1, optional: 0, doc: 'held(cond, ms) 1 once cond has been true for ms' },
};

/** Handled by the compiler itself (it needs to catch 'invalid'). */
export const SPECIAL: Record<string, string> = {
  valid: 'valid(x) 1 when x has a value right now, else 0',
  coalesce: 'coalesce(a, b, ...) first argument that has a value',
  time: 'time() seconds since the start of the session or log',
};

/** One line per callable, for the UI's expression editor. */
export function functionHelp(): string[] {
  const lines = [...Object.values(PURE).map((p) => p.doc), ...Object.values(STATEFUL).map((s) => s.doc),
    ...Object.values(SPECIAL)];
  return lines.sort(pyCompare);
}

/** Python string ordering (by code point). */
export function pyCompare(a: string, b: string): number {
  const aa = Array.from(a);
  const bb = Array.from(b);
  const n = Math.min(aa.length, bb.length);
  for (let i = 0; i < n; i++) {
    const d = aa[i]!.codePointAt(0)! - bb[i]!.codePointAt(0)!;
    if (d) return d;
  }
  return aa.length - bb.length;
}

/** Feed a whole column through a state object. NaN/null in = skipped, NaN out. */
export function runState(state: State, t: ArrayLike<number>, x: ArrayLike<number | null>): Float64Array {
  const out = new Float64Array(x.length).fill(NaN);
  for (let i = 0; i < x.length; i++) {
    const xi = x[i];
    if (xi !== null && xi !== undefined && xi === xi) {
      const r = state.update(t[i]!, xi);
      if (r !== null) out[i] = r;
    }
  }
  return out;
}
