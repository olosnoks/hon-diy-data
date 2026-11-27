/**
 * Small helpers that make text the way the Python app makes it, so the
 * database rows look the same whichever app wrote them: JSON with Python's
 * separators, float repr, local ISO times, `%g`.
 */

/** Python's ``repr(float)`` for a finite number ("5.0", "0.1", "1795765862.73675"). */
export function pyFloat(x: number): string {
  if (!Number.isFinite(x)) return x !== x ? 'nan' : x > 0 ? 'inf' : '-inf';
  if (Number.isInteger(x) && Math.abs(x) < 1e16) return x.toFixed(1);
  const s = String(x);
  // JS: 1e+21, Python: 1e+21; JS: 1e-7, Python: 1e-07
  return s.replace(/e([+-])(\d)$/, 'e$10$2');
}

/** Python's ``format(x, ".{p}g")`` (default precision 6, like ``"g"``). */
export function pyG(x: number, precision = 6): string {
  if (!Number.isFinite(x)) return x !== x ? 'nan' : x > 0 ? 'inf' : '-inf';
  if (x === 0) return '0';
  const p = Math.max(1, precision);
  const [, e0] = x.toExponential(p - 1).split('e') as [string, string];
  const exp = Number(e0);
  if (exp < -4 || exp >= p) {
    let [m, e] = x.toExponential(p - 1).split('e') as [string, string];
    if (m.includes('.')) m = m.replace(/0+$/, '').replace(/\.$/, '');
    const n = Number(e);
    return `${m}e${n < 0 ? '-' : '+'}${String(Math.abs(n)).padStart(2, '0')}`;
  }
  let s = pyFixed(x, Math.max(0, p - 1 - exp));
  if (s.includes('.')) s = s.replace(/0+$/, '').replace(/\.$/, '');
  return s;
}

/** Python's ``f"{x:.{d}f}"``: like toFixed, but an exact tie rounds to even (11.25 -> "11.2"). */
export function pyFixed(x: number, d: number): string {
  if (!Number.isFinite(x)) return x !== x ? 'nan' : x > 0 ? 'inf' : '-inf';
  const plain = x.toFixed(d);
  const full = Math.abs(x).toFixed(Math.min(100, d + 60));
  const [ip, fp = ''] = full.split('.') as [string, string?];
  const rest = fp.slice(d);
  if (rest[0] !== '5' || /[1-9]/.test(rest.slice(1))) return plain;
  const keep = fp.slice(0, d);
  const lastDigit = Number((d ? keep : ip).slice(-1));
  if (lastDigit % 2 === 1) return plain;                 // odd: rounding up makes it even
  const down = d ? `${ip}.${keep}` : ip;
  return (x < 0 && Number(down) !== 0 ? '-' : x < 0 ? '-' : '') + down;
}

/** ``json.dumps(value, ensure_ascii=False, default=str)``: ", " and ": " separators. */
export function pyJson(value: unknown): string {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') {
    if (value !== value) return 'NaN';
    if (!Number.isFinite(value)) return value > 0 ? 'Infinity' : '-Infinity';
    return String(value);
  }
  if (typeof value === 'bigint') return value.toString();
  if (typeof value === 'string') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(pyJson).join(', ') + ']';
  if (ArrayBuffer.isView(value)) return pyJson(Array.from(value as unknown as ArrayLike<number>));
  if (value instanceof Date) return JSON.stringify(localIso(value.getTime() / 1000));
  if (typeof value === 'object') {
    const parts = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .map(([k, v]) => JSON.stringify(k) + ': ' + pyJson(v));
    return '{' + parts.join(', ') + '}';
  }
  return JSON.stringify(String(value));
}

/** ``json.loads`` that tolerates Python's NaN / Infinity. */
export function pyParse(text: string | null | undefined): any {
  if (text === null || text === undefined || text === '') return null;
  try {
    return JSON.parse(text);
  } catch {
    const fixed = text.replace(/\bNaN\b/g, 'null').replace(/-?\bInfinity\b/g, 'null');
    return JSON.parse(fixed);
  }
}

const p2 = (n: number) => String(n).padStart(2, '0');

/** ``datetime.fromtimestamp(unix).isoformat(timespec=...)`` in local time. */
export function localIso(unix: number, timespec: 'seconds' | 'milliseconds' = 'seconds'): string {
  const d = new Date(Math.floor(unix * 1000));
  let s = `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}T${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
  if (timespec === 'milliseconds') s += '.' + String(d.getMilliseconds()).padStart(3, '0');
  return s;
}

/** ``datetime.fromisoformat(text).timestamp()`` for a local time without a zone; NaN if not one. */
export function parseLocalIso(text: string): number {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?)?$/.exec(text.trim());
  if (!m) return NaN;
  const ms = m[7] ? Number(m[7].padEnd(3, '0').slice(0, 3)) : 0;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4] ?? 0), Number(m[5] ?? 0),
    Number(m[6] ?? 0), ms).getTime() / 1000;
}

/** ``f"{dt:%Y_%m_%d_%H%M}"`` and friends: local time pieces. */
export function localParts(unix: number) {
  const d = new Date(Math.floor(unix * 1000));
  return { Y: String(d.getFullYear()), m: p2(d.getMonth() + 1), d: p2(d.getDate()), H: p2(d.getHours()),
    M: p2(d.getMinutes()), S: p2(d.getSeconds()) };
}

/** Python's ``round(x, n)`` closely enough for display values (half to even at the last digit is ignored). */
export function pyRound(x: number, n = 0): number {
  const f = 10 ** n;
  return Math.round(x * f) / f;
}
