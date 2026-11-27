/**
 * Trigger conditions. The trigger engine needs three things of a compiled
 * condition: the channels it names (``deps``), whether it has history
 * (``stateful``: rate(), avg(), held() ...) and ``test(values, t)`` with the
 * hondalog.expr rule "unknown counts as false".
 *
 * Conditions compile with agent #3's expression engine (src/core/expr,
 * ``compileExpr``: the whole hondalog.expr language, rate() / avg() / held()
 * included). ``setConditionCompiler`` swaps it; ``compileCondition`` below is a
 * small built-in compiler (no stateful functions) kept as a fallback and for
 * decoding formulas in tests: numbers, names (``[name]`` too),
 * + - * / // % **, comparisons (chained), and / or / not, ``a if c else b``,
 * bit operators, and the pure functions abs, min, max, round, floor, ceil,
 * sqrt, clamp, bit, poly. Its semantics are hondalog.expr's: a name that is
 * missing, None or NaN makes the whole expression "no value" (so its test is
 * false), ``and`` / ``or`` short-circuit, division by zero is "no value".
 */

import { ExprError, compileExpr } from '../expr';

export { ExprError };

export type Values = Readonly<Record<string, number | null | undefined>>;

export interface Condition {
  readonly text: string;
  readonly deps: ReadonlySet<string>;
  readonly stateful: boolean;
  /** true / false; an expression without a value right now is false */
  test(values: Values, t: number): boolean;
}

export type ConditionCompiler = (text: string) => Condition;


// the engine's Expr has text, deps, stateful and test(values, t): it is a Condition
const engineCompiler: ConditionCompiler = (text) => compileExpr(text);
let current: ConditionCompiler = engineCompiler;

/** Use another compiler for every rule compiled from now on (null: back to the expression engine). */
export function setConditionCompiler(fn: ConditionCompiler | null): void {
  current = fn ?? engineCompiler;
}

export function compile(text: string): Condition {
  return current(text);
}

// ---- the built-in compiler ----------------------------------------------------

class Invalid extends Error {}

type Tok = { k: 'num' | 'name' | 'op' | 'end'; v: string; pos: number };

const OPS = ['**', '//', '<<', '>>', '<=', '>=', '==', '!=', '<', '>', '+', '-', '*', '/', '%', '&', '|', '^', '~', '(', ')', ',', '[', ']'];
const KEYWORDS = new Set(['and', 'or', 'not', 'if', 'else']);

function tokenize(text: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < text.length) {
    const ch = text[i]!;
    if (/\s/.test(ch)) { i++; continue; }
    const num = /^(0[xX][0-9a-fA-F]+|0[bB][01]+|(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?)/.exec(text.slice(i));
    if (num) {
      out.push({ k: 'num', v: num[0], pos: i });
      i += num[0].length;
      if (/^[A-Za-z_]/.test(text.slice(i))) throw new ExprError('unexpected name after a number', text, i);
      continue;
    }
    const name = /^[A-Za-z_][A-Za-z0-9_]*/.exec(text.slice(i));
    if (name) {
      out.push({ k: 'name', v: name[0], pos: i });
      i += name[0].length;
      continue;
    }
    const op = OPS.find((o) => text.startsWith(o, i));
    if (!op) throw new ExprError(`unexpected character ${JSON.stringify(ch)}`, text, i);
    out.push({ k: 'op', v: op, pos: i });
    i += op.length;
  }
  out.push({ k: 'end', v: '', pos: text.length });
  return out;
}

type Fn = (v: Values) => number | boolean;

const truthy = (x: number | boolean) => x !== 0 && x !== false;
const num = (x: number | boolean): number => (typeof x === 'boolean' ? (x ? 1 : 0) : x);

function checkInt(x: number): number {
  if (!Number.isFinite(x)) throw new Invalid('not a whole number');
  const n = Math.trunc(x);
  if (Math.abs(n) >= 2 ** 62) throw new Invalid('integer overflow');
  return n;
}

const PURE: Record<string, [number, number | null, (...a: number[]) => number]> = {
  abs: [1, 1, Math.abs],
  min: [1, null, (...a) => Math.min(...a)],
  max: [1, null, (...a) => Math.max(...a)],
  round: [1, 2, (x, n = 0) => { const f = 10 ** n; const y = x * f; const r = Math.round(y); return (Math.abs(y % 1) === 0.5 ? 2 * Math.round(y / 2) : r) / f; }],
  floor: [1, 1, Math.floor],
  ceil: [1, 1, Math.ceil],
  sqrt: [1, 1, (x) => { if (x < 0) throw new Invalid('math domain error'); return Math.sqrt(x); }],
  clamp: [3, 3, (x, lo, hi) => Math.min(Math.max(x, lo), hi)],
  bit: [2, 2, (x, n) => (Number(BigInt(checkInt(x)) >> BigInt(checkInt(n))) & 1)],
  poly: [2, null, (x, ...c) => c.reduce((acc, k, i) => acc + k * x ** i, 0)],
};

const STATEFUL = new Set(['rate', 'avg', 'held', 'delta', 'smooth', 'mean', 'minw', 'maxw', 'since', 'count']);

class Parser {
  i = 0;
  deps = new Set<string>();
  constructor(readonly toks: Tok[], readonly text: string) {}

  peek(): Tok {
    return this.toks[this.i]!;
  }
  next(): Tok {
    return this.toks[this.i++]!;
  }
  isOp(v: string): boolean {
    const t = this.peek();
    return t.k === 'op' && t.v === v;
  }
  isKw(v: string): boolean {
    const t = this.peek();
    return t.k === 'name' && t.v === v;
  }
  expect(v: string): void {
    const t = this.next();
    if (!(t.k === 'op' && t.v === v)) throw new ExprError(`expected ${JSON.stringify(v)}`, this.text, t.pos);
  }

  parse(): Fn {
    const t = this.peek();
    if (t.k === 'end') throw new ExprError('empty expression', this.text, 0);
    const fn = this.ternary();
    const end = this.peek();
    if (end.k !== 'end') throw new ExprError(`unexpected ${JSON.stringify(end.v)}`, this.text, end.pos);
    return fn;
  }

  ternary(): Fn {
    const then = this.or();
    if (this.isKw('if')) {
      this.next();
      const test = this.or();
      if (!this.isKw('else')) throw new ExprError("expected 'else'", this.text, this.peek().pos);
      this.next();
      const other = this.ternary();
      return (v) => (truthy(test(v)) ? then(v) : other(v));
    }
    return then;
  }

  or(): Fn {
    let left = this.and();
    while (this.isKw('or')) {
      this.next();
      const a = left, b = this.and();
      left = (v) => (truthy(a(v)) || truthy(b(v)) ? 1 : 0);
    }
    return left;
  }

  and(): Fn {
    let left = this.not();
    while (this.isKw('and')) {
      this.next();
      const a = left, b = this.not();
      left = (v) => (truthy(a(v)) && truthy(b(v)) ? 1 : 0);
    }
    return left;
  }

  not(): Fn {
    if (this.isKw('not')) {
      this.next();
      const f = this.not();
      return (v) => (truthy(f(v)) ? 0 : 1);
    }
    return this.compare();
  }

  compare(): Fn {
    const first = this.bitor();
    const ops: string[] = [];
    const rights: Fn[] = [];
    while (['<', '>', '<=', '>=', '==', '!='].some((o) => this.isOp(o))) {
      ops.push(this.next().v);
      rights.push(this.bitor());
    }
    if (!ops.length) return first;
    const cmp = (op: string, a: number, b: number): boolean => {
      switch (op) {
        case '<': return a < b;
        case '>': return a > b;
        case '<=': return a <= b;
        case '>=': return a >= b;
        case '==': return a === b;
        default: return a !== b;
      }
    };
    return (v) => {
      let a = num(first(v));
      for (let k = 0; k < ops.length; k++) {
        const b = num(rights[k]!(v));
        if (!cmp(ops[k]!, a, b)) return false;
        a = b;
      }
      return true;
    };
  }

  binary(next: () => Fn, ops: string[], apply: (op: string, a: number, b: number) => number): Fn {
    let left = next();
    while (ops.some((o) => this.isOp(o))) {
      const op = this.next().v;
      const a = left, b = next();
      left = (v) => apply(op, num(a(v)), num(b(v)));
    }
    return left;
  }

  bitor(): Fn {
    return this.binary(() => this.bitxor(), ['|'], (_, a, b) => Number(BigInt(checkInt(a)) | BigInt(checkInt(b))));
  }
  bitxor(): Fn {
    return this.binary(() => this.bitand(), ['^'], (_, a, b) => Number(BigInt(checkInt(a)) ^ BigInt(checkInt(b))));
  }
  bitand(): Fn {
    return this.binary(() => this.shift(), ['&'], (_, a, b) => Number(BigInt(checkInt(a)) & BigInt(checkInt(b))));
  }
  shift(): Fn {
    return this.binary(() => this.sum(), ['<<', '>>'], (op, a, b) => {
      const n = Math.trunc(b);
      if (!(n >= 0 && n < 64)) throw new Invalid('shift count out of range');
      const r = op === '<<' ? BigInt(checkInt(a)) << BigInt(n) : BigInt(checkInt(a)) >> BigInt(n);
      return checkInt(Number(r));
    });
  }
  sum(): Fn {
    return this.binary(() => this.product(), ['+', '-'], (op, a, b) => (op === '+' ? a + b : a - b));
  }
  product(): Fn {
    return this.binary(() => this.unary(), ['*', '/', '//', '%'], (op, a, b) => {
      if (op === '*') return a * b;
      if (b === 0) throw new Invalid('division by zero');
      if (op === '/') return a / b;
      if (op === '//') return Math.floor(a / b);
      return a - b * Math.floor(a / b);
    });
  }

  unary(): Fn {
    if (this.isOp('-') || this.isOp('+') || this.isOp('~')) {
      const op = this.next().v;
      const f = this.unary();
      if (op === '-') return (v) => -num(f(v));
      if (op === '+') return f;
      return (v) => ~checkInt(num(f(v)));
    }
    return this.power();
  }

  power(): Fn {
    const base = this.atom();
    if (this.isOp('**')) {
      this.next();
      const exp = this.unary();
      return (v) => {
        const r = Math.pow(num(base(v)), num(exp(v)));
        if (r !== r) throw new Invalid('math domain error');
        return r;
      };
    }
    return base;
  }

  variable(name: string): Fn {
    this.deps.add(name);
    return (v) => {
      const x = v[name];
      if (x === null || x === undefined || x !== x) throw new Invalid(name);
      return x;
    };
  }

  atom(): Fn {
    const t = this.next();
    if (t.k === 'num') {
      const s = t.v.toLowerCase();
      const value = s.startsWith('0x') ? parseInt(s.slice(2), 16) : s.startsWith('0b') ? parseInt(s.slice(2), 2) : Number(t.v);
      return () => value;
    }
    if (t.k === 'op' && t.v === '(') {
      const f = this.ternary();
      this.expect(')');
      return f;
    }
    if (t.k === 'op' && t.v === '[') {
      const n = this.next();
      if (n.k !== 'name') throw new ExprError('expected a channel name', this.text, n.pos);
      this.expect(']');
      return this.variable(n.v);
    }
    if (t.k === 'name' && !KEYWORDS.has(t.v)) {
      if (this.isOp('(')) {
        this.next();
        const args: Fn[] = [];
        if (!this.isOp(')')) {
          args.push(this.ternary());
          while (this.isOp(',')) {
            this.next();
            args.push(this.ternary());
          }
        }
        this.expect(')');
        if (STATEFUL.has(t.v)) {
          throw new ExprError(`${t.v}() needs the full expression engine (src/core/expr)`, this.text, t.pos);
        }
        const spec = PURE[t.v];
        if (!spec) throw new ExprError(`unknown function ${JSON.stringify(t.v)}`, this.text, t.pos);
        const [lo, hi, fn] = spec;
        if (args.length < lo || (hi !== null && args.length > hi)) {
          throw new ExprError(`${t.v}() takes ${hi === lo ? lo : hi === null ? `${lo} or more` : `${lo} to ${hi}`} arguments, got ${args.length}`, this.text, t.pos);
        }
        return (v) => fn(...args.map((a) => num(a(v))));
      }
      return this.variable(t.v);
    }
    throw new ExprError(t.k === 'end' ? 'unexpected end of expression' : `unexpected ${JSON.stringify(t.v)}`, this.text, t.pos);
  }
}

/** Compile ``text`` with the built-in compiler. Throws ExprError on any problem. */
export function compileCondition(text: string): Condition {
  const src = text.trim();
  const p = new Parser(tokenize(src), src);
  const fn = p.parse();
  const deps: ReadonlySet<string> = p.deps;
  return {
    text: src,
    deps,
    stateful: false,
    test(values: Values): boolean {
      let r: number;
      try {
        r = num(fn(values));
      } catch (err) {
        if (err instanceof Invalid || err instanceof RangeError || err instanceof TypeError) return false;
        throw err;
      }
      return Number.isFinite(r) && r !== 0;
    },
  };
}

/**
 * A formula as a value (the built-in compiler): ``eval(values)`` is the number,
 * or null when it has no value. Used to decode address map channels until
 * agent #3's engine is wired in, and by the tests.
 */
export function compileValue(text: string): { deps: ReadonlySet<string>; eval(values: Values): number | null } {
  const src = text.trim();
  const p = new Parser(tokenize(src), src);
  const fn = p.parse();
  return {
    deps: p.deps,
    eval(values: Values): number | null {
      try {
        const r = num(fn(values));
        return Number.isFinite(r) ? r : null;
      } catch (err) {
        if (err instanceof Invalid || err instanceof RangeError || err instanceof TypeError) return null;
        throw err;
      }
    },
  };
}
