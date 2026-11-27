/**
 * Python-compatible text: repr() of strings, numbers and lists, and
 * json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",", ":")).
 *
 * The version hash is sha256 over that JSON, so it must come out byte for
 * byte as CPython writes it. JS has one number type; a value that Python
 * holds as a float is wrapped in PyFloat so it is written "1.0", not "1".
 */

/** A number that Python holds as a float (json writes repr(float)). */
export class PyFloat {
  constructor(readonly value: number) {}
}

/** repr(float): shortest round-trip digits, CPython's layout. */
export function pyFloatRepr(x: number): string {
  if (Number.isNaN(x)) return 'nan';
  if (x === Infinity) return 'inf';
  if (x === -Infinity) return '-inf';
  if (x === 0) return Object.is(x, -0) ? '-0.0' : '0.0';
  const [mant = '', expText = '0'] = x.toExponential().split('e');
  const exp = parseInt(expText, 10);
  const neg = mant.startsWith('-');
  const digits = mant.replace('-', '').replace('.', '');
  const sign = neg ? '-' : '';
  if (exp < -4 || exp >= 16) {
    const m = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits;
    const e = `${exp < 0 ? '-' : '+'}${String(Math.abs(exp)).padStart(2, '0')}`;
    return `${sign}${m}e${e}`;
  }
  let intPart: string;
  let frac: string;
  if (exp >= 0) {
    intPart = digits.slice(0, exp + 1).padEnd(exp + 1, '0');
    frac = digits.slice(exp + 1) || '0';
  } else {
    intPart = '0';
    frac = '0'.repeat(-exp - 1) + digits;
  }
  return `${sign}${intPart}.${frac}`;
}

/** json's float text: like repr, but NaN / Infinity spelled the JS way. */
function jsonFloat(x: number): string {
  if (Number.isNaN(x)) return 'NaN';
  if (x === Infinity) return 'Infinity';
  if (x === -Infinity) return '-Infinity';
  return pyFloatRepr(x);
}

/** repr(str): single quotes unless the text has ' and no ". */
export function pyStrRepr(s: string): string {
  const quote = s.includes("'") && !s.includes('"') ? '"' : "'";
  let out = quote;
  for (const ch of s) {
    const code = ch.codePointAt(0) ?? 0;
    if (ch === quote || ch === '\\') out += '\\' + ch;
    else if (ch === '\n') out += '\\n';
    else if (ch === '\r') out += '\\r';
    else if (ch === '\t') out += '\\t';
    else if (code < 0x20 || code === 0x7f) out += '\\x' + code.toString(16).padStart(2, '0');
    else out += ch;
  }
  return out + quote;
}

/** repr() of the plain values a TOML document can hold. */
export function pyRepr(v: unknown): string {
  if (v === null || v === undefined) return 'None';
  if (typeof v === 'string') return pyStrRepr(v);
  if (typeof v === 'boolean') return v ? 'True' : 'False';
  if (typeof v === 'bigint') return v.toString();
  if (v instanceof PyFloat) return pyFloatRepr(v.value);
  if (typeof v === 'number') return pyFloatRepr(v);   // TOML ints arrive as bigint
  if (Array.isArray(v)) return `[${v.map(pyRepr).join(', ')}]`;
  if (typeof v === 'object') {
    return `{${Object.entries(v as Record<string, unknown>).map(([k, x]) => `${pyStrRepr(k)}: ${pyRepr(x)}`).join(', ')}}`;
  }
  return String(v);
}

/** str() of a TOML value (evidence fields are stored as text). */
export function pyStr(v: unknown): string {
  if (typeof v === 'string') return v;
  if (typeof v === 'number') return pyFloatRepr(v);
  if (v instanceof Date) return v.toISOString();
  return pyRepr(v);
}

function jsonString(s: string): string {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const ch = s[i]!;
    const code = s.charCodeAt(i);
    if (ch === '"') out += '\\"';
    else if (ch === '\\') out += '\\\\';
    else if (ch === '\n') out += '\\n';
    else if (ch === '\r') out += '\\r';
    else if (ch === '\t') out += '\\t';
    else if (ch === '\b') out += '\\b';
    else if (ch === '\f') out += '\\f';
    else if (code < 0x20) out += '\\u' + code.toString(16).padStart(4, '0');
    else out += ch;
  }
  return out + '"';
}

/**
 * json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":")).
 * Plain JS numbers must be whole (Python ints); wrap floats in PyFloat.
 */
export function pyJsonDumps(v: unknown): string {
  if (v === null || v === undefined) return 'null';
  if (v === true) return 'true';
  if (v === false) return 'false';
  if (typeof v === 'string') return jsonString(v);
  if (typeof v === 'bigint') return v.toString();
  if (v instanceof PyFloat) return jsonFloat(v.value);
  if (typeof v === 'number') {
    if (!Number.isInteger(v)) return jsonFloat(v);
    return Math.abs(v) < 1e21 ? BigInt(v).toString() : jsonFloat(v);
  }
  if (Array.isArray(v)) return `[${v.map(pyJsonDumps).join(',')}]`;
  const obj = v as Record<string, unknown>;
  const keys = Object.keys(obj).sort(comparePy);
  return `{${keys.map((k) => `${jsonString(k)}:${pyJsonDumps(obj[k])}`).join(',')}}`;
}

/** Python's str ordering (by code point, not UTF-16 unit). */
export function comparePy(a: string, b: string): number {
  const ca = Array.from(a, (c) => c.codePointAt(0) ?? 0);
  const cb = Array.from(b, (c) => c.codePointAt(0) ?? 0);
  const n = Math.min(ca.length, cb.length);
  for (let i = 0; i < n; i++) if (ca[i] !== cb[i]) return ca[i]! - cb[i]!;
  return ca.length - cb.length;
}

/** sorted() for strings. */
export function pySorted(items: Iterable<string>): string[] {
  return Array.from(items).sort(comparePy);
}

/** Python's round(x): half to even. */
export function roundHalfEven(x: number): number {
  const f = Math.floor(x);
  const diff = x - f;
  if (diff > 0.5) return f + 1;
  if (diff < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

/** f"{value:.{n}f}" */
export function pyFixed(value: number, decimals: number): string {
  if (Number.isNaN(value)) return 'nan';
  if (!Number.isFinite(value)) return value > 0 ? 'inf' : '-inf';
  return value.toFixed(Math.max(0, Math.min(100, decimals)));
}
