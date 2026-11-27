/**
 * Python number semantics the expression engine and its messages rely on:
 * floor division and modulo, math.pow, math.fsum, round() (half-even on the
 * exact binary value), repr() of floats and strings, and format() specs.
 *
 * Exactness matters: a formula from the address map must give the same
 * number here as in the Python app, and a formatted alarm message the same
 * text.
 */

/** A Python exception raised while evaluating (caught by the evaluator). */
export class PyError extends Error {
  constructor(public readonly pyType: string, message: string) {
    super(message);
    this.name = pyType;
  }
}

export const zeroDiv = (what = 'float division by zero'): PyError => new PyError('ZeroDivisionError', what);
export const domain = (): PyError => new PyError('ValueError', 'math domain error');
export const range = (): PyError => new PyError('OverflowError', 'math range error');

// ---------------------------------------------------------------- arithmetic

/** Python ``a % b`` on floats (result takes the sign of b). */
export function pyMod(a: number, b: number): number {
  if (b === 0) throw zeroDiv('float modulo');
  let mod = a % b;                             // C fmod
  if (mod) {
    if ((b < 0) !== (mod < 0)) mod += b;
  } else {
    mod = b < 0 ? -0 : 0;
  }
  return mod;
}

/** Python ``a // b`` on floats (CPython float_floor_div). */
export function pyFloorDiv(a: number, b: number): number {
  if (b === 0) throw zeroDiv('float floor division by zero');
  let mod = a % b;
  let div = (a - mod) / b;
  if (mod) {
    if ((b < 0) !== (mod < 0)) {
      mod += b;
      div -= 1.0;
    }
  }
  let floordiv: number;
  if (div) {
    floordiv = Math.floor(div);
    if (div - floordiv > 0.5) floordiv += 1.0;
  } else {
    floordiv = (a / b) < 0 || Object.is(a / b, -0) ? -0 : 0;
  }
  return floordiv;
}

/** Python ``a / b``. */
export function pyDiv(a: number, b: number): number {
  if (b === 0) throw zeroDiv();
  return a / b;
}

function isOddInteger(y: number): boolean {
  return Number.isFinite(y) && Math.abs(y) % 2 === 1;
}

/** CPython math.pow, special cases and errors included. */
export function pyPow(x: number, y: number): number {
  if (!Number.isFinite(x) || !Number.isFinite(y)) {
    if (Number.isNaN(x)) return y === 0 ? 1 : x;
    if (Number.isNaN(y)) return x === 1 ? 1 : y;
    if (!Number.isFinite(x)) {
      const odd = isOddInteger(y);
      if (y > 0) return odd ? x : Math.abs(x);
      if (y === 0) return 1;
      return odd ? (x < 0 ? -0 : 0) : 0;
    }
    // y infinite
    if (Math.abs(x) === 1) return 1;
    if (y > 0 && Math.abs(x) > 1) return y;
    if (y < 0 && Math.abs(x) < 1) return -y;
    return 0;
  }
  const r = Math.pow(x, y);
  if (!Number.isFinite(r)) {
    if (Number.isNaN(r)) throw domain();
    if (x === 0) throw domain();
    throw range();
  }
  return r;
}

/** Python int(x) of a float: truncate; inf and nan raise. */
export function pyTrunc(x: number): number {
  if (Number.isNaN(x)) throw new PyError('ValueError', 'cannot convert float NaN to integer');
  if (!Number.isFinite(x)) throw new PyError('OverflowError', 'cannot convert float infinity to integer');
  return Math.trunc(x) + 0;                    // no -0 from an int
}

export function toBig(x: number): bigint {
  return BigInt(pyTrunc(x));
}

// ---------------------------------------------------------------- fsum

/** math.fsum: the correctly rounded sum (Shewchuk / CPython msum). */
export function fsum(values: Iterable<number>): number {
  const partials: number[] = [];
  let special = 0;
  let infSum = 0;
  let sawSpecial = false;
  for (let x of values) {
    if (!Number.isFinite(x)) {
      sawSpecial = true;
      if (Number.isNaN(x)) special = NaN;
      else infSum += x;
      continue;
    }
    let i = 0;
    for (let j = 0; j < partials.length; j++) {
      let y = partials[j]!;
      if (Math.abs(x) < Math.abs(y)) {
        const tmp = x; x = y; y = tmp;
      }
      const hi = x + y;
      const lo = y - (hi - x);
      if (lo) partials[i++] = lo;
      x = hi;
    }
    partials.length = i;
    partials.push(x);
  }
  if (sawSpecial) return Number.isNaN(special) ? NaN : infSum;  // inf - inf: NaN (Python raises)
  let hi = 0.0;
  let n = partials.length;
  if (n > 0) {
    hi = partials[--n]!;
    let lo = 0;
    while (n > 0) {
      const x = hi;
      const y = partials[--n]!;
      hi = x + y;
      const yr = hi - x;
      lo = y - yr;
      if (lo !== 0.0) break;
    }
    if (n > 0 && ((lo < 0.0 && partials[n - 1]! < 0.0) || (lo > 0.0 && partials[n - 1]! > 0.0))) {
      const y = lo * 2.0;
      const x = hi + y;
      const yr = x - hi;
      if (y === yr) hi = x;
    }
  }
  return hi;
}

// ---------------------------------------------------------------- exact decimals

const _buf = new DataView(new ArrayBuffer(8));

/** |x| (finite) as an exact decimal: int(digits) / 10**k. */
function exactDecimal(x: number): { d: bigint; k: number } {
  _buf.setFloat64(0, Math.abs(x));
  const hi = _buf.getUint32(0);
  const lo = _buf.getUint32(4);
  const bexp = (hi >>> 20) & 0x7ff;
  let mant = (BigInt(hi & 0xfffff) << 32n) | BigInt(lo);
  let e2: number;
  if (bexp === 0) {
    e2 = -1074;
  } else {
    mant |= 1n << 52n;
    e2 = bexp - 1075;
  }
  if (mant === 0n) return { d: 0n, k: 0 };
  if (e2 >= 0) return { d: mant << BigInt(e2), k: 0 };
  const k = -e2;
  return { d: mant * 5n ** BigInt(k), k };
}

/** Round |x| to n decimals (n may be negative), half-even on the exact value.
 *  Returns q with |x| ~ q / 10**n. */
function roundDecimals(x: number, n: number): bigint {
  const { d, k } = exactDecimal(x);
  if (n >= k) return d * 10n ** BigInt(n - k);
  const p = 10n ** BigInt(k - n);
  let q = d / p;
  const twice = (d % p) * 2n;
  if (twice > p || (twice === p && (q & 1n) === 1n)) q += 1n;
  return q;
}

function placeDecimals(q: bigint, n: number): string {
  if (n <= 0) return (q * 10n ** BigInt(-n)).toString();
  let s = q.toString();
  if (s.length <= n) s = '0'.repeat(n - s.length + 1) + s;
  return `${s.slice(0, s.length - n)}.${s.slice(s.length - n)}`;
}

function negative(x: number): boolean {
  return x < 0 || Object.is(x, -0);
}

/** f"{x:.{n}f}" without sign handling of nan/inf. */
function fixedAbs(x: number, n: number): string {
  return placeDecimals(roundDecimals(x, n), n);
}

/** Python format(x, f".{n}f"). */
export function pyFixed(x: number, n: number): string {
  if (Number.isNaN(x)) return 'nan';
  if (!Number.isFinite(x)) return x > 0 ? 'inf' : '-inf';
  return (negative(x) ? '-' : '') + fixedAbs(x, n);
}

/** Python round(x, n) for a float (n = null: round(x) -> int, as a float). */
export function pyRound(x: number, n: number | null = null): number {
  if (n === null) {
    if (Number.isNaN(x)) throw new PyError('ValueError', 'cannot convert float NaN to integer');
    if (!Number.isFinite(x)) throw new PyError('OverflowError', 'cannot convert float infinity to integer');
    const q = roundDecimals(x, 0);
    return (negative(x) ? -Number(q) : Number(q)) + 0;   // an int: no -0
  }
  if (!Number.isFinite(x) || x === 0) return x;
  if (n > 323) return x;
  if (n < -308) return 0.0 * x;
  const q = roundDecimals(x, n);
  const v = Number.parseFloat(n > 0 ? placeDecimals(q, n) : `${q.toString()}e${-n}`);
  if (!Number.isFinite(v)) throw new PyError('OverflowError', 'rounded value too large to represent');
  return negative(x) ? -v : v;
}

/** Significant-digit rounding: digits (no leading zeros, length p) and the
 *  decimal exponent of the first digit. */
function sigDigits(x: number, p: number): { digits: string; exp: number } {
  if (x === 0) return { digits: '0'.repeat(p), exp: 0 };
  const { d, k } = exactDecimal(x);
  const e10 = d.toString().length - 1 - k;
  const n = p - 1 - e10;
  const q = roundDecimals(x, n);
  let s = q.toString();
  let exp = e10;
  if (s.length > p) {                          // 9.99 -> 10.0
    exp += 1;
    s = s.slice(0, p);
  }
  return { digits: s, exp };
}

function expText(exp: number): string {
  const a = Math.abs(exp);
  return `e${exp < 0 ? '-' : '+'}${a < 10 ? '0' : ''}${a}`;
}

function stripZeros(s: string): string {
  if (!s.includes('.')) return s;
  s = s.replace(/0+$/, '');
  return s.endsWith('.') ? s.slice(0, -1) : s;
}

/** Python 'g' (and the no-type float format with a precision when dot0). */
function generalAbs(x: number, precision: number, dot0: boolean): string {
  const p = precision === 0 ? 1 : precision;
  const { digits, exp } = sigDigits(x, p);
  const decpt = exp + 1;
  const useExp = decpt <= -4 || decpt > (dot0 ? p - 1 : p);
  if (x === 0 && !useExp) return dot0 ? '0.0' : '0';
  if (useExp) {
    let mant = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits;
    mant = stripZeros(mant);
    return mant + expText(exp);
  }
  let s: string;
  if (decpt <= 0) s = `0.${'0'.repeat(-decpt)}${digits}`;
  else if (decpt >= digits.length) s = digits + '0'.repeat(decpt - digits.length);
  else s = `${digits.slice(0, decpt)}.${digits.slice(decpt)}`;
  s = stripZeros(s);
  if (dot0 && !s.includes('.')) s += '.0';
  return s;
}

/** repr() of a float: shortest round-trip digits, Python's layout. */
export function reprFloat(x: number): string {
  if (Number.isNaN(x)) return 'nan';
  if (!Number.isFinite(x)) return x > 0 ? 'inf' : '-inf';
  const sign = negative(x) ? '-' : '';
  if (x === 0) return `${sign}0.0`;
  const [m, e] = Math.abs(x).toExponential().split('e') as [string, string];
  const digits = m.replace('.', '');
  const exp = Number(e);
  if (exp < -4 || exp >= 16) {
    const mant = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits;
    return sign + mant + expText(exp);
  }
  const decpt = exp + 1;
  let s: string;
  if (decpt <= 0) s = `0.${'0'.repeat(-decpt)}${digits}`;
  else if (decpt >= digits.length) s = `${digits}${'0'.repeat(decpt - digits.length)}.0`;
  else s = `${digits.slice(0, decpt)}.${digits.slice(decpt)}`;
  return sign + s;
}

/** f"{x:g}". */
export function pyG(x: number, precision = 6): string {
  return formatNumber(x, { precision, type: 'g' });
}

// ---------------------------------------------------------------- format()

interface Spec {
  align?: string;
  sign?: string;
  zero?: boolean;
  width?: number;
  precision?: number;
  type?: string;
}

const SPEC = /^(?:([<>^=])?)([+\- ])?(0)?(\d+)?(?:\.(\d+))?([bcdeEfFgGnosxX%])?$/;

function parseSpec(spec: string): Spec | null {
  // A fill character (any char followed by an align char) is not used by the
  // alarm messages' restricted specs; plain align / sign / width / precision / type.
  const m = SPEC.exec(spec);
  if (!m) return null;
  return {
    align: m[1], sign: m[2], zero: m[3] === '0',
    width: m[4] !== undefined ? Number(m[4]) : undefined,
    precision: m[5] !== undefined ? Number(m[5]) : undefined,
    type: m[6],
  };
}

function pad(body: string, signText: string, spec: Spec, numeric: boolean): string {
  const width = spec.width ?? 0;
  const len = [...signText].length + [...body].length;
  if (len >= width) return signText + body;
  const fill = width - len;
  const align = spec.align ?? (spec.zero && numeric ? '=' : numeric ? '>' : '<');
  if (align === '=') {
    const ch = spec.zero ? '0' : ' ';
    return signText + ch.repeat(fill) + body;
  }
  const text = signText + body;
  const ch = spec.zero && !spec.align ? '0' : ' ';
  if (align === '<') return text + ch.repeat(fill);
  if (align === '^') {
    const left = Math.floor(fill / 2);
    return ch.repeat(left) + text + ch.repeat(fill - left);
  }
  return ch.repeat(fill) + text;
}

function formatNumber(x: number, spec: Spec, asInt = false): string {
  const type = spec.type;
  const neg = negative(x) && !Number.isNaN(x);
  const ax = Math.abs(x);
  let body: string;
  if (!Number.isFinite(x)) {
    body = Number.isNaN(x) ? 'nan' : 'inf';
    if (type === '%') body += '%';
  } else if (asInt && (type === undefined || type === 'd')) {
    if (spec.precision !== undefined) throw new PyError('ValueError', 'Precision not allowed in integer format specifier');
    body = BigInt(ax).toString();
  } else if (type === 'd') {
    throw new PyError('ValueError', "Unknown format code 'd' for object of type 'float'");
  } else if (type === 'f' || type === 'F') {
    body = fixedAbs(ax, spec.precision ?? 6);
  } else if (type === '%') {
    body = `${fixedAbs(ax * 100, spec.precision ?? 6)}%`;
  } else if (type === 'g' || type === 'G') {
    body = generalAbs(ax, spec.precision ?? 6, false);
  } else if (type === 'e' || type === 'E') {
    const p = spec.precision ?? 6;
    const { digits, exp } = sigDigits(ax, p + 1);
    body = (p > 0 ? `${digits[0]}.${digits.slice(1)}` : digits) + expText(ax === 0 ? 0 : exp);
  } else if (type === undefined) {
    body = spec.precision === undefined ? reprFloat(ax) : generalAbs(ax, spec.precision, true);
  } else {
    throw new PyError('ValueError', `Unknown format code '${type}' for object of type 'float'`);
  }
  if (type === 'G' || type === 'E' || type === 'F') body = body.toUpperCase();
  const signText = neg ? '-' : spec.sign === '+' ? '+' : spec.sign === ' ' ? ' ' : '';
  return pad(body, signText, spec, true);
}

/**
 * Python ``format(value, spec)`` for numbers and strings. Numbers are taken as
 * floats; pass ``asInt`` for a Python int. Raises PyError where Python raises
 * ValueError.
 */
export function pyFormat(value: number | string, spec: string, asInt = false): string {
  const s = parseSpec(spec);
  if (!s) throw new PyError('ValueError', 'Invalid format specifier');
  if (typeof value === 'string') {
    if (s.type !== undefined && s.type !== 's') {
      throw new PyError('ValueError', `Unknown format code '${s.type}' for object of type 'str'`);
    }
    if (s.sign) throw new PyError('ValueError', 'Sign not allowed in string format specifier');
    const chars = [...value];
    const body = s.precision !== undefined ? chars.slice(0, s.precision).join('') : value;
    return pad(body, '', s, false);
  }
  return formatNumber(value, s, asInt);
}

/** str() of a value as Python would print it. */
export function pyStr(value: unknown): string {
  if (typeof value === 'number') return reprFloat(value);
  if (typeof value === 'boolean') return value ? 'True' : 'False';
  if (value === null || value === undefined) return 'None';
  return String(value);
}

// ---------------------------------------------------------------- repr of text

/** repr() of a str. */
export function pyRepr(text: string): string {
  const quote = text.includes("'") && !text.includes('"') ? '"' : "'";
  let out = quote;
  for (const ch of text) {
    const c = ch.codePointAt(0)!;
    if (ch === quote || ch === '\\') out += `\\${ch}`;
    else if (ch === '\n') out += '\\n';
    else if (ch === '\r') out += '\\r';
    else if (ch === '\t') out += '\\t';
    else if (c < 0x20 || c === 0x7f) out += `\\x${c.toString(16).padStart(2, '0')}`;
    else if (c >= 0x80 && c < 0xa0) out += `\\x${c.toString(16).padStart(2, '0')}`;
    else out += ch;
  }
  return out + quote;
}
