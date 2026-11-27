/**
 * Compile an AST into nested closures. No ``eval``, no ``Function``, no codegen.
 *
 * Every node becomes one small function ``f(values, t)``. Constant sub-trees
 * are folded at compile time. Stateful helpers (``avg``, ``[x-1]`` ...) are
 * hoisted: they run once per ``Expr.eval`` call, before the main tree, so
 * their windows advance on every sample even inside an untaken branch.
 *
 * An evaluation problem (missing value, divide by zero, math domain error)
 * never escapes ``Expr.eval``: the result is ``null``, shown as invalid.
 *
 * Numbers follow Python (hondalog/expr/compiler.py): floor division and
 * modulo, math.pow errors, bit operators on whole numbers below 2**62, NaN
 * counting as true in a condition.
 */

import { getCloseMatches } from './difflib';
import { PURE, SPECIAL, STATEFUL, Lag, pyCompare, type Pure, type State, type Stateful } from './funcs';
import { ExprError, walk, type Binary, type Call, type Compare, type Cond, type Index, type Node, type Unary, type Var, type Bool } from './nodes';
import { parse } from './parser';
import { PyError, pyDiv, pyFloorDiv, pyMod, pyPow, pyRepr, pyTrunc, toBig } from './pynum';

/** What an expression reads: names to numbers, ``null``/``undefined``/NaN = no value.
 *  Array variables (formula bytes ``b``) are sequences of numbers. */
export type Value = number | boolean | null | undefined | ArrayLike<number>;
export type Values = Readonly<Record<string, Value>>;
type Fn = (v: Values, t: number) => number;

const NC: unique symbol = Symbol('not a constant');
type Const = number | typeof NC;

const INT_LIMIT = 1n << 62n;

/** Raised inside a closure when an input has no value right now. */
class Invalid extends PyError {
  constructor(what: string) {
    super('Invalid', what);
  }
}

/** Raised by ``Expr.evalStrict`` when the expression has no value. */
export class EvalError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'EvalError';
  }
}

/** Python truthiness of a number: NaN is true, 0 and -0 are false. */
const truthy = (x: number): boolean => x !== 0;

// ---- operator helpers -------------------------------------------------------

function intResult(x: bigint): bigint {
  if (!(-INT_LIMIT < x && x < INT_LIMIT)) throw new Invalid('integer overflow');
  return x;
}

/** Whole-number view of a value for the bit operators. */
function int(a: number): bigint {
  return intResult(toBig(a));
}

function lshift(a: number, b: number): number {
  const n = pyTrunc(b);
  if (!(n >= 0 && n < 64)) throw new Invalid('shift count out of range');
  return Number(intResult(int(a) << BigInt(n)));
}

function rshift(a: number, b: number): number {
  const n = pyTrunc(b);
  if (!(n >= 0 && n < 64)) throw new Invalid('shift count out of range');
  return Number(int(a) >> BigInt(n));
}

const BINARY: Record<string, (a: number, b: number) => number> = {
  '+': (a, b) => a + b,
  '-': (a, b) => a - b,
  '*': (a, b) => a * b,
  '/': pyDiv,
  '//': pyFloorDiv,
  '%': pyMod,
  '**': pyPow,
  '&': (a, b) => Number(int(a) & int(b)),
  '|': (a, b) => Number(int(a) | int(b)),
  '^': (a, b) => Number(int(a) ^ int(b)),
  '<<': lshift,
  '>>': rshift,
};

const COMPARE: Record<string, (a: number, b: number) => boolean> = {
  '<': (a, b) => a < b,
  '<=': (a, b) => a <= b,
  '>': (a, b) => a > b,
  '>=': (a, b) => a >= b,
  '==': (a, b) => a === b,
  '!=': (a, b) => a !== b,
};

function binaryFn(op: string, lf: Fn, lc: Const, rf: Fn, rc: Const): Fn {
  // Constant operands are bound once (the common "x * 0.25" formula shape).
  if (op === '+') {
    if (rc !== NC) return (v, t) => lf(v, t) + rc;
    if (lc !== NC) return (v, t) => lc + rf(v, t);
    return (v, t) => lf(v, t) + rf(v, t);
  }
  if (op === '-') {
    if (rc !== NC) return (v, t) => lf(v, t) - rc;
    if (lc !== NC) return (v, t) => lc - rf(v, t);
    return (v, t) => lf(v, t) - rf(v, t);
  }
  if (op === '*') {
    if (rc !== NC) return (v, t) => lf(v, t) * rc;
    if (lc !== NC) return (v, t) => lc * rf(v, t);
    return (v, t) => lf(v, t) * rf(v, t);
  }
  if (op === '/' && rc !== NC && rc !== 0) return (v, t) => lf(v, t) / rc;
  const fn = BINARY[op]!;
  return (v, t) => fn(lf(v, t), rf(v, t));
}

function cmp1(op: string, lf: Fn, rf: Fn, rc: Const): Fn {
  if (rc !== NC) {
    if (op === '>') return (v, t) => (lf(v, t) > rc ? 1 : 0);
    if (op === '<') return (v, t) => (lf(v, t) < rc ? 1 : 0);
    if (op === '>=') return (v, t) => (lf(v, t) >= rc ? 1 : 0);
    if (op === '<=') return (v, t) => (lf(v, t) <= rc ? 1 : 0);
  }
  const fn = COMPARE[op]!;
  return (v, t) => (fn(lf(v, t), rf(v, t)) ? 1 : 0);
}

const hasOwn = (o: object, k: string): boolean => Object.prototype.hasOwnProperty.call(o, k);

// ---- the compiler -----------------------------------------------------------

interface Slot { value: number | null }

class Compiler {
  readonly deps = new Set<string>();
  readonly functions = new Set<string>();
  readonly steps: Fn[] = [];
  readonly states: State[] = [];
  readonly slots: Slot[] = [];
  usesTime = false;

  constructor(
    readonly text: string,
    readonly names: ReadonlySet<string> | null,
    readonly constants: Readonly<Record<string, number>>,
    readonly arrays: ReadonlySet<string>,
  ) {}

  error(message: string, node: Node): ExprError {
    return new ExprError(message, this.text, node.pos);
  }

  compile(node: Node): [Fn, Const] {
    switch (node.type) {
      case 'num': return Compiler.konst(node.value);
      case 'var': return this.variable(node);
      case 'index': return this.index(node);
      case 'unary': return this.unary(node);
      case 'binary': return this.binary(node);
      case 'compare': return this.compare(node);
      case 'bool': return this.bool(node);
      case 'cond': return this.cond(node);
      case 'call': return this.call(node);
    }
  }

  static konst(value: number): [Fn, Const] {
    return [() => value, value];
  }

  /** If every input was constant, run the node now and keep the answer. */
  fold(fn: Fn, ...consts: Const[]): [Fn, Const] {
    if (consts.some((c) => c === NC)) return [fn, NC];
    let value: number;
    try {
      value = fn({}, 0.0);
    } catch {
      return [fn, NC];          // e.g. 1/0: stays a runtime 'invalid'
    }
    return Compiler.konst(value);
  }

  // -- leaves --

  private isConstant(name: string): boolean {
    return hasOwn(this.constants, name);
  }

  unknown(node: Var | Index): ExprError {
    const known = new Set<string>([...(this.names ?? []), ...Object.keys(this.constants)]);
    const near = getCloseMatches(node.name, [...known].sort(pyCompare), 3);
    const hint = near.length ? ` (did you mean ${near.join(', ')}?)` : '';
    return this.error(`unknown name ${pyRepr(node.name)}${hint}`, node);
  }

  variable(node: Var): [Fn, Const] {
    const name = node.name;
    if (this.arrays.has(name)) {
      throw this.error(`${pyRepr(name)} is a list of bytes: write ${name}[0], ${name}[1] ...`, node);
    }
    if (this.isConstant(name) && (this.names === null || !this.names.has(name))) {
      if (node.lag) throw this.error(`${pyRepr(name)} is a constant, it has no previous sample`, node);
      return Compiler.konst(this.constants[name]!);
    }
    if (this.names !== null && !this.names.has(name)) throw this.unknown(node);
    this.deps.add(name);
    const read: Fn = (v) => {
      const x = v[name];
      if (x === undefined && !hasOwn(v, name)) throw new PyError('KeyError', pyRepr(name));
      if (x === null || x === undefined) throw new Invalid(name);
      if (typeof x === 'boolean') return x ? 1 : 0;
      if (typeof x !== 'number') throw new PyError('TypeError', `${pyRepr(name)} is not a number`);
      if (x !== x) throw new Invalid(name);
      return x;
    };
    if (node.lag) return [this.hoist(new Lag(node.lag), read), NC];
    return [read, NC];
  }

  index(node: Index): [Fn, Const] {
    const name = node.name;
    if (!this.arrays.has(name)) {
      if (this.names !== null && !this.names.has(name) && !this.isConstant(name)) throw this.unknown(node);
      throw this.error(`${pyRepr(name)} cannot be indexed`, node);
    }
    const [idx, idxC] = this.compile(node.index);
    const at = (v: Values, i: number): number => {
      const arr = v[name];
      if (arr === null || arr === undefined || typeof arr !== 'object') {
        throw new PyError('KeyError', pyRepr(name));
      }
      const n = arr.length;
      const j = i < 0 ? i + n : i;
      if (j < 0 || j >= n) throw new PyError('IndexError', 'index out of range');
      return arr[j]!;
    };
    if (idxC !== NC && Number.isFinite(idxC)) {
      const i = pyTrunc(idxC);
      return [(v) => at(v, i), NC];
    }
    return [(v, t) => at(v, pyTrunc(idx(v, t))), NC];
  }

  // -- operators --

  unary(node: Unary): [Fn, Const] {
    const [f, c] = this.compile(node.operand);
    let fn: Fn;
    if (node.op === '-') fn = (v, t) => -f(v, t);
    else if (node.op === '+') fn = f;
    else if (node.op === '~') fn = (v, t) => Number(~int(f(v, t)));
    else fn = (v, t) => (truthy(f(v, t)) ? 0 : 1);
    return this.fold(fn, c);
  }

  binary(node: Binary): [Fn, Const] {
    const [lf, lc] = this.compile(node.left);
    const [rf, rc] = this.compile(node.right);
    return this.fold(binaryFn(node.op, lf, lc, rf, rc), lc, rc);
  }

  compare(node: Compare): [Fn, Const] {
    const [lf, lc] = this.compile(node.left);
    const rights = node.rights.map((r) => this.compile(r));
    let fn: Fn;
    if (rights.length === 1) {
      const [rf, rc] = rights[0]!;
      fn = cmp1(node.ops[0]!, lf, rf, rc);
    } else {
      const links = node.ops.map((op, i) => [COMPARE[op]!, rights[i]![0]] as const);
      fn = (v, t) => {
        let a = lf(v, t);
        for (const [op, rf] of links) {
          const b = rf(v, t);
          if (!op(a, b)) return 0;
          a = b;
        }
        return 1;
      };
    }
    return this.fold(fn, lc, ...rights.map(([, c]) => c));
  }

  bool(node: Bool): [Fn, Const] {
    const parts = node.values.map((v) => this.compile(v));
    const fs = parts.map(([f]) => f);
    let fn: Fn;
    if (fs.length === 2) {
      const [a, b] = fs as [Fn, Fn];
      fn = node.op === 'and'
        ? (v, t) => (truthy(a(v, t)) && truthy(b(v, t)) ? 1 : 0)
        : (v, t) => (truthy(a(v, t)) || truthy(b(v, t)) ? 1 : 0);
    } else if (node.op === 'and') {
      fn = (v, t) => {
        for (const f of fs) if (!truthy(f(v, t))) return 0;
        return 1;
      };
    } else {
      fn = (v, t) => {
        for (const f of fs) if (truthy(f(v, t))) return 1;
        return 0;
      };
    }
    return this.fold(fn, ...parts.map(([, c]) => c));
  }

  cond(node: Cond): [Fn, Const] {
    const [test, tc] = this.compile(node.test);
    const then = this.compile(node.then);
    const other = this.compile(node.other);
    if (tc !== NC) return truthy(tc) ? then : other;      // constant test: keep one branch
    const [hf] = then;
    const [of] = other;
    return [(v, t) => (truthy(test(v, t)) ? hf(v, t) : of(v, t)), NC];
  }

  // -- calls --

  call(node: Call): [Fn, Const] {
    const name = node.func;
    this.functions.add(name);
    if (hasOwn(PURE, name)) return this.pure(node, PURE[name]!);
    if (hasOwn(STATEFUL, name)) return this.stateful(node, STATEFUL[name]!);
    if (hasOwn(SPECIAL, name)) return this.special(node);
    const known = [...Object.keys(PURE), ...Object.keys(STATEFUL), ...Object.keys(SPECIAL)].sort(pyCompare);
    const near = getCloseMatches(name, known, 3);
    const hint = near.length ? ` (did you mean ${near.join(', ')}?)` : '';
    throw this.error(`unknown function ${pyRepr(name)}${hint}`, node);
  }

  arity(node: Call, lo: number, hi: number | null): void {
    const n = node.args.length;
    if (n < lo || (hi !== null && n > hi)) {
      const want = hi === lo ? `${lo}` : hi === null ? `${lo} or more` : `${lo} to ${hi}`;
      throw this.error(`${node.func}() takes ${want} arguments, got ${n}`, node);
    }
  }

  constantArgs(node: Call, start: number): number[] {
    const out: number[] = [];
    node.args.slice(start).forEach((arg, k) => {
      const [, c] = this.compile(arg);
      if (c === NC) throw this.error(`argument ${start + k + 1} of ${node.func}() must be a constant`, arg);
      out.push(c);
    });
    return out;
  }

  pure(node: Call, spec: Pure): [Fn, Const] {
    this.arity(node, spec.minArgs, spec.maxArgs);
    const scalar = spec.scalar;
    const nDyn = spec.constFrom === null ? node.args.length : spec.constFrom;
    let tail: number[] = spec.constFrom === null ? [] : this.constantArgs(node, nDyn);
    if (spec.pairs) checkPairs(this, node, tail);
    const parts = node.args.slice(0, nDyn).map((a) => this.compile(a));
    const consts = parts.map(([, c]) => c);
    // Trailing constants (poly coefficients, clamp limits) are bound once.
    const dyn = [...parts];
    while (dyn.length > 1 && dyn[dyn.length - 1]![1] !== NC) tail = [dyn.pop()![1] as number, ...tail];
    const fs = dyn.map(([f]) => f);
    let fn: Fn;
    if (fs.length === 1) {
      const a = fs[0]!;
      fn = tail.length ? (v, t) => scalar(a(v, t), ...tail) : (v, t) => scalar(a(v, t));
    } else if (fs.length === 2 && !tail.length) {
      const [a, b] = fs as [Fn, Fn];
      fn = (v, t) => scalar(a(v, t), b(v, t));
    } else {
      fn = (v, t) => scalar(...fs.map((f) => f(v, t)), ...tail);
    }
    return this.fold(fn, ...consts);
  }

  stateful(node: Call, spec: Stateful): [Fn, Const] {
    this.arity(node, 1 + spec.params - spec.optional, 1 + spec.params);
    const params = this.constantArgs(node, 1);
    let state: State;
    try {
      state = spec.factory(...params);
    } catch (exc) {
      if (exc instanceof PyError) throw this.error(`${node.func}(): ${exc.message}`, node);
      throw exc;
    }
    const [arg] = this.compile(node.args[0]!);
    this.usesTime = true;
    return [this.hoist(state, arg), NC];
  }

  hoist(state: State, arg: Fn): Fn {
    const slot: Slot = { value: null };
    const step: Fn = (v, t) => {
      try {
        slot.value = state.update(t, arg(v, t));
      } catch {
        slot.value = null;
      }
      return 0;
    };
    const read: Fn = () => {
      const x = slot.value;
      if (x === null) throw new Invalid('no value yet');
      return x;
    };
    this.steps.push(step);
    this.states.push(state);
    this.slots.push(slot);
    return read;
  }

  special(node: Call): [Fn, Const] {
    if (node.func === 'time') {
      this.arity(node, 0, 0);
      this.usesTime = true;
      return [(_v, t) => t, NC];
    }
    if (node.func === 'valid') {
      this.arity(node, 1, 1);
      const [f] = this.compile(node.args[0]!);
      return [(v, t) => {
        let r: number;
        try {
          r = f(v, t);
        } catch {
          return 0;
        }
        return r === r ? 1 : 0;
      }, NC];
    }
    this.arity(node, 2, null);
    const fs = node.args.map((a) => this.compile(a)[0]);
    return [(v, t) => {
      for (const f of fs) {
        let r: number;
        try {
          r = f(v, t);
        } catch {
          continue;
        }
        if (r === r) return r;
      }
      throw new Invalid('nothing has a value');
    }, NC];
  }
}

function checkPairs(comp: Compiler, node: Call, tail: number[]): void {
  if (tail.length % 2) throw comp.error(`${node.func}() needs x, y pairs after the input`, node);
  const xs = tail.filter((_, i) => i % 2 === 0);
  for (let i = 1; i < xs.length; i++) {
    if (xs[i]! <= xs[i - 1]!) throw comp.error(`${node.func}(): the x values must go up`, node);
  }
}

// ---- public -----------------------------------------------------------------

export interface CompileOptions {
  /** The variables the expression may use; null/undefined allows any name
   *  (it is then looked up at evaluation time). */
  names?: Iterable<string> | null;
  /** Named numbers folded in at compile time. A variable of the same name wins. */
  constants?: Readonly<Record<string, number>>;
  /** Variables that are sequences and must be indexed (``b[0]``). */
  arrays?: Iterable<string>;
}

/** A column for ``evalArray``: numbers, NaN or null = no value. */
export type Column = ArrayLike<number | null>;

/**
 * A compiled expression. Build with {@link compileExpr}.
 *
 * ``eval(values, t)`` is the hot path: ``values`` maps names to numbers (or
 * null for "no value"), ``t`` is the sample time in seconds. It returns a
 * number, or null when the expression has no value right now.
 */
export class Expr {
  readonly text: string;
  readonly node: Node;
  readonly deps: ReadonlySet<string>;
  readonly functions: ReadonlySet<string>;
  readonly stateful: boolean;
  readonly usesTime: boolean;
  readonly constant: number | null;
  /** Evaluate one sample: a finite number, or null. */
  readonly eval: (values: Values, t?: number) => number | null;
  private readonly fn: Fn;
  private readonly steps: Fn[];
  private readonly states: State[];
  private readonly slots: Slot[];
  private readonly opts: CompileOptions;
  private shadow: Expr | null = null;

  constructor(text: string, node: Node, comp: Compiler, fn: Fn, konst: Const, opts: CompileOptions) {
    this.text = text;
    this.node = node;
    this.deps = new Set(comp.deps);
    this.functions = new Set(comp.functions);
    this.stateful = comp.steps.length > 0;
    this.usesTime = comp.usesTime;
    this.constant = konst === NC ? null : Number(konst);
    this.fn = fn;
    this.steps = comp.steps;
    this.states = comp.states;
    this.slots = comp.slots;
    this.opts = opts;
    this.eval = makeEval(fn, this.steps);
  }

  toString(): string {
    return `Expr(${pyRepr(this.text)})`;
  }

  /** Like ``eval`` but throws EvalError instead of returning null. */
  evalStrict(values: Values, t = 0.0): number {
    try {
      for (const step of this.steps) step(values, t);
      return Number(this.fn(values, t));
    } catch (exc) {
      const type = exc instanceof PyError ? exc.pyType : exc instanceof Error ? exc.name : 'Error';
      const msg = exc instanceof Error ? exc.message : String(exc);
      throw new EvalError(`${this.text}: ${type}: ${msg}`);
    }
  }

  /** The expression as a condition: true, false, or null for 'unknown'. */
  truth(values: Values, t = 0.0): boolean | null {
    const r = this.eval(values, t);
    return r === null ? null : r !== 0.0;
  }

  /** ``truth`` for callers that act on it: unknown counts as false, so
   *  ``rpm < 50`` cannot fire a trigger while the ECU is silent. */
  test(values: Values, t = 0.0): boolean {
    const r = this.eval(values, t);
    return r !== null && r !== 0.0;
  }

  /** ``evalArray`` as a 0/1 column for filters: 1 where the expression is
   *  non-zero, 0 where it is zero or has no value. */
  mask(columns: Readonly<Record<string, Column>>, t?: ArrayLike<number> | null): Uint8Array {
    const out = this.evalArray(columns, t);
    const m = new Uint8Array(out.length);
    for (let i = 0; i < out.length; i++) m[i] = out[i] === out[i] && out[i] !== 0 ? 1 : 0;
    return m;
  }

  /** Forget all window history (new session, new log, seek in replay). */
  reset(): void {
    for (const state of this.states) state.reset();
    for (const slot of this.slots) slot.value = null;
  }

  /**
   * Evaluate over whole columns at once. NaN means 'no value'.
   *
   * ``columns`` maps names to equal-length arrays (NaN or null = missing),
   * ``t`` is the time column in seconds. Gives the same numbers as calling
   * ``eval`` once per row on a fresh expression, without touching live state.
   * Throws ExprError like the Python numpy back end: an indexed name, a
   * missing column, a column of the wrong length, or no time column for an
   * expression that needs one.
   */
  evalArray(columns: Readonly<Record<string, Column>>, t?: ArrayLike<number> | null): Float64Array {
    const consts = this.opts.constants ?? {};
    const names = this.opts.names ? new Set(this.opts.names) : null;
    const isConst = (n: string): boolean => hasOwn(consts, n) && (names === null || !names.has(n));
    const nodes = [...walk(this.node)];
    for (const node of nodes) {
      if (node.type === 'index') {
        throw new ExprError(`${node.name}[...] only works in channel formulas`, this.text, node.pos);
      }
    }
    let tArr: ArrayLike<number>;
    if (t === null || t === undefined) {
      if (this.usesTime) throw new ExprError('this expression looks at time: pass the time column', this.text);
      let n = 1;
      for (const name of this.deps) {
        if (hasOwn(columns, name)) {
          n = columns[name]!.length;
          break;
        }
      }
      tArr = new Float64Array(n);
    } else {
      tArr = t;
    }
    const rows = tArr.length;
    const used: string[] = [];
    for (const node of nodes) {
      if (node.type !== 'var' || isConst(node.name) || used.includes(node.name)) continue;
      if (!hasOwn(columns, node.name)) {
        throw new ExprError(`no column named ${pyRepr(node.name)}`, this.text, node.pos);
      }
      const len = columns[node.name]!.length;
      if (len !== rows) {
        throw new ExprError(`column ${pyRepr(node.name)} has ${len} rows, expected ${rows}`, this.text, node.pos);
      }
      used.push(node.name);
    }
    if (this.shadow === null) this.shadow = compileExpr(this.text, this.opts);
    const shadow = this.shadow;
    shadow.reset();
    const out = new Float64Array(rows);
    const row: Record<string, number | null> = {};
    const cols = used.map((n) => columns[n]!);
    for (let i = 0; i < rows; i++) {
      for (let k = 0; k < used.length; k++) {
        const x = cols[k]![i];
        row[used[k]!] = x === undefined || x === null || x !== x ? null : x;
      }
      const r = shadow.eval(row, tArr[i]!);
      out[i] = r === null ? NaN : r;
    }
    shadow.reset();
    return out;
  }
}

function makeEval(fn: Fn, steps: Fn[]): (values: Values, t?: number) => number | null {
  if (!steps.length) {
    return (values, t = 0.0) => {
      let r: number;
      try {
        r = Number(fn(values, t));
      } catch {
        return null;
      }
      return Number.isFinite(r) ? r : null;
    };
  }
  return (values, t = 0.0) => {
    for (const step of steps) step(values, t);
    let r: number;
    try {
      r = Number(fn(values, t));
    } catch {
      return null;
    }
    return Number.isFinite(r) ? r : null;
  };
}

/** Parse, check and compile ``text``. Throws ExprError on any problem. */
export function compileExpr(text: string, opts: CompileOptions = {}): Expr {
  const node = parse(text);
  const names = opts.names === null || opts.names === undefined ? null : new Set(opts.names);
  const stored: CompileOptions = {
    names: names === null ? null : [...names],
    constants: { ...(opts.constants ?? {}) },
    arrays: [...(opts.arrays ?? [])],
  };
  const comp = new Compiler(text.trim(), names, stored.constants!, new Set(stored.arrays));
  const [fn, konst] = comp.compile(node);
  return new Expr(text.trim(), node, comp, fn, konst, stored);
}

/** Names an expression mentions, without compiling it. */
export function references(text: string): Set<string> {
  const out = new Set<string>();
  for (const n of walk(parse(text))) if (n.type === 'var' || n.type === 'index') out.add(n.name);
  return out;
}
