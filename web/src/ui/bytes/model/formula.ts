/**
 * A small, safe evaluator for address-map formulas (the "x * 0.5 - 40",
 * "bit(x, 2)", "poly(x, c0, c1)" language of hondalog/addressmap.py).
 *
 * Local fallback only: when agent #3's expression engine (src/core/expr) is
 * present the integrator can pass its compiler through BytesDeps.compile.
 * Supported: numbers (incl. 0x..), x, b[i], named constants, + - * / // % **,
 * unary -, & | ^ << >>, comparisons (-> 1/0), and/or/not, parentheses and
 * bit, signed, poly, abs, min, max, round.
 */

export type Formula = (x: number, b: ArrayLike<number>) => number;

type Tok = { k: 'num'; v: number } | { k: 'id'; v: string } | { k: 'op'; v: string };

const OPS = ['**', '//', '<<', '>>', '<=', '>=', '==', '!=', '+', '-', '*', '/', '%', '(', ')', '[', ']', ',', '&', '|', '^', '<', '>', '~'];

function lex(text: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < text.length) {
    const c = text[i]!;
    if (/\s/.test(c)) { i++; continue; }
    const hexm = /^0[xX][0-9a-fA-F]+/.exec(text.slice(i));
    if (hexm) { out.push({ k: 'num', v: parseInt(hexm[0], 16) }); i += hexm[0].length; continue; }
    const num = /^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?/.exec(text.slice(i));
    if (num) { out.push({ k: 'num', v: parseFloat(num[0]) }); i += num[0].length; continue; }
    const id = /^[A-Za-z_][A-Za-z0-9_]*/.exec(text.slice(i));
    if (id) { out.push({ k: 'id', v: id[0] }); i += id[0].length; continue; }
    const op = OPS.find((o) => text.startsWith(o, i));
    if (!op) throw new Error(`bad formula ${JSON.stringify(text)}: unexpected ${JSON.stringify(c)}`);
    out.push({ k: 'op', v: op });
    i += op.length;
  }
  return out;
}

type Node = (env: Env) => number;
interface Env { x: number; b: ArrayLike<number> }

const FUNCS: Record<string, (...a: number[]) => number> = {
  bit: (x, n) => (Math.trunc(x) >> Math.trunc(n)) & 1,
  signed: (x, bits = 8) => {
    const m = 2 ** bits;
    const v = ((Math.trunc(x) % m) + m) % m;
    return v >= m / 2 ? v - m : v;
  },
  poly: (x, ...c) => c.reduce((acc, k, i) => acc + k * x ** i, 0),
  abs: Math.abs,
  min: Math.min,
  max: Math.max,
  round: (x, n = 0) => {
    const k = 10 ** n;
    // Python rounds half to even
    const y = x * k;
    const r = Math.round(y);
    return (Math.abs(y % 1) === 0.5 ? (r % 2 === 0 ? r : r - 1) : r) / k;
  },
  int: Math.trunc,
  float: (x) => x,
};

export function compileFormula(text: string, constants: Record<string, number> = {}): Formula {
  const toks = lex(text);
  let p = 0;
  const peek = (): Tok | undefined => toks[p];
  const isOp = (v: string) => { const t = peek(); return t?.k === 'op' && t.v === v; };
  const isId = (v: string) => { const t = peek(); return t?.k === 'id' && t.v === v; };
  const expect = (v: string) => {
    if (!isOp(v)) throw new Error(`bad formula ${JSON.stringify(text)}: expected ${v}`);
    p++;
  };

  function primary(): Node {
    const t = peek();
    if (!t) throw new Error(`bad formula ${JSON.stringify(text)}: ends too soon`);
    if (t.k === 'num') { p++; const v = t.v; return () => v; }
    if (t.k === 'op' && t.v === '(') { p++; const e = orExpr(); expect(')'); return e; }
    if (t.k === 'id') {
      p++;
      const name = t.v;
      if (isOp('(')) {
        p++;
        const args: Node[] = [];
        if (!isOp(')')) {
          args.push(orExpr());
          while (isOp(',')) { p++; args.push(orExpr()); }
        }
        expect(')');
        const fn = FUNCS[name];
        if (!fn) throw new Error(`bad formula ${JSON.stringify(text)}: unknown function ${name}`);
        return (env) => fn(...args.map((a) => a(env)));
      }
      if (name === 'x') return (env) => env.x;
      if (name === 'b') {
        expect('[');
        const idx = orExpr();
        expect(']');
        return (env) => {
          const i = Math.trunc(idx(env));
          const v = env.b[i];
          if (v === undefined) throw new Error(`b[${i}] out of range`);
          return v;
        };
      }
      if (name === 'True') return () => 1;
      if (name === 'False') return () => 0;
      if (name in constants) { const v = constants[name]!; return () => v; }
      throw new Error(`bad formula ${JSON.stringify(text)}: unknown name ${name}`);
    }
    throw new Error(`bad formula ${JSON.stringify(text)}: unexpected ${t.v}`);
  }

  function unary(): Node {
    if (isOp('-')) { p++; const e = unary(); return (env) => -e(env); }
    if (isOp('+')) { p++; return unary(); }
    if (isOp('~')) { p++; const e = unary(); return (env) => ~Math.trunc(e(env)); }
    return power();
  }
  function power(): Node {
    const base = primary();
    if (isOp('**')) { p++; const ex = unary(); return (env) => base(env) ** ex(env); }
    return base;
  }
  function term(): Node {
    let l = unary();
    for (;;) {
      const t = peek();
      if (t?.k !== 'op' || !['*', '/', '//', '%'].includes(t.v)) return l;
      p++;
      const r = unary();
      const a = l;
      if (t.v === '*') l = (env) => a(env) * r(env);
      else if (t.v === '/') l = (env) => { const d = r(env); if (d === 0) throw new Error('division by zero'); return a(env) / d; };
      else if (t.v === '//') l = (env) => { const d = r(env); if (d === 0) throw new Error('division by zero'); return Math.floor(a(env) / d); };
      else l = (env) => { const d = r(env); if (d === 0) throw new Error('division by zero'); const v = a(env); return v - d * Math.floor(v / d); };
    }
  }
  function arith(): Node {
    let l = term();
    while (isOp('+') || isOp('-')) {
      const op = (peek() as Tok).v; p++;
      const r = term(); const a = l;
      l = op === '+' ? (env) => a(env) + r(env) : (env) => a(env) - r(env);
    }
    return l;
  }
  function shift(): Node {
    let l = arith();
    while (isOp('<<') || isOp('>>')) {
      const op = (peek() as Tok).v; p++;
      const r = arith(); const a = l;
      l = op === '<<' ? (env) => Math.trunc(a(env)) << Math.trunc(r(env)) : (env) => Math.trunc(a(env)) >> Math.trunc(r(env));
    }
    return l;
  }
  function bitand(): Node {
    let l = shift();
    while (isOp('&')) { p++; const r = shift(); const a = l; l = (env) => Math.trunc(a(env)) & Math.trunc(r(env)); }
    return l;
  }
  function bitxor(): Node {
    let l = bitand();
    while (isOp('^')) { p++; const r = bitand(); const a = l; l = (env) => Math.trunc(a(env)) ^ Math.trunc(r(env)); }
    return l;
  }
  function bitor(): Node {
    let l = bitxor();
    while (isOp('|')) { p++; const r = bitxor(); const a = l; l = (env) => Math.trunc(a(env)) | Math.trunc(r(env)); }
    return l;
  }
  function compare(): Node {
    let l = bitor();
    for (;;) {
      const t = peek();
      if (t?.k !== 'op' || !['<', '>', '<=', '>=', '==', '!='].includes(t.v)) return l;
      p++;
      const r = bitor(); const a = l; const op = t.v;
      l = (env) => {
        const x = a(env), y = r(env);
        const ok = op === '<' ? x < y : op === '>' ? x > y : op === '<=' ? x <= y : op === '>=' ? x >= y : op === '==' ? x === y : x !== y;
        return ok ? 1 : 0;
      };
    }
  }
  function notExpr(): Node {
    if (isId('not')) { p++; const e = notExpr(); return (env) => (e(env) ? 0 : 1); }
    return compare();
  }
  function andExpr(): Node {
    let l = notExpr();
    while (isId('and')) { p++; const r = notExpr(); const a = l; l = (env) => { const v = a(env); return v ? r(env) : v; }; }
    return l;
  }
  function orExpr(): Node {
    let l = andExpr();
    while (isId('or')) { p++; const r = andExpr(); const a = l; l = (env) => { const v = a(env); return v ? v : r(env); }; }
    return l;
  }

  const root = orExpr();
  if (p !== toks.length) throw new Error(`bad formula ${JSON.stringify(text)}: unexpected ${toks[p]!.v}`);
  return (x, b) => root({ x, b });
}
