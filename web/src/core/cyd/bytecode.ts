/**
 * Compile an expression tree into the handheld's postfix byte code (firmware hh_expr.c),
 * ported from hondalog/handheld/bytecode.py. Only what can be evaluated from the present
 * sample is accepted: a function with history raises ``Unsupported``.
 */

import * as F from './format';
import { type Node, parse } from './expr';

export class Unsupported extends Error {}

/** A name the handheld never has a value for (an OBD2-only channel). */
class Absent extends Unsupported {}

/** hondalog/expr/funcs.py PURE: (min_args, max_args or null = any) */
const PURE: Record<string, [number, number | null]> = {
  abs: [1, 1], min: [2, null], max: [2, null], round: [1, 2], int: [1, 1], float: [1, 1], floor: [1, 1],
  ceil: [1, 1], sqrt: [1, 1], exp: [1, 1], ln: [1, 1], log: [1, 1], log10: [1, 1], pow: [2, 2], sin: [1, 1],
  cos: [1, 1], sign: [1, 1], clamp: [3, 3], between: [3, 3], bit: [2, 2], signed: [1, 2], poly: [2, null],
  table: [5, null], thermistor: [4, 5],
};
/** hondalog/expr/funcs.py STATEFUL */
export const STATEFUL = new Set(['avg', 'slope', 'minw', 'maxw', 'stdev', 'smooth', 'median', 'ema', 'prev',
  'delta', 'rate', 'integ', 'held']);

/** Constants shared by every program of a map file. */
export class Pool {
  readonly values: number[] = [];
  private readonly index = new Map<string, number>();
  private readonly scratch = new DataView(new ArrayBuffer(8));

  add(value: number): number {
    this.scratch.setFloat64(0, value, true);
    const key = this.scratch.getBigUint64(0, true).toString(16);
    let i = this.index.get(key);
    if (i === undefined) {
      if (this.values.length >= F.MAX_CONST) throw new Unsupported(`more than ${F.MAX_CONST} different constants`);
      i = this.values.length;
      this.index.set(key, i);
      this.values.push(value);
    }
    return i;
  }
}

class Out {
  bytes: number[] = [];
  push(...b: number[]): void { this.bytes.push(...b); }
}

export class Compiler {
  readonly deps = new Set<string>();
  private readonly absent: Set<string>;

  constructor(private readonly pool: Pool, private readonly names: Map<string, number> = new Map(),
    private readonly constants: Map<string, number> = new Map(), private readonly formula = false,
    private readonly rawLen = 0, absent: Iterable<string> = []) {
    this.absent = new Set(absent);
  }

  compile(text: string): Uint8Array {
    const out = new Out();
    const depth = this.emit(parse(text), out, 0);
    if (depth !== 1) throw new Error('stack depth is not 1');
    out.push(F.OP_END);
    return Uint8Array.from(out.bytes);
  }

  private pushed(depth: number): number {
    if (depth + 1 > F.EXPR_STACK) throw new Unsupported(`expression needs more than ${F.EXPR_STACK} stack slots`);
    return depth + 1;
  }

  private constant(value: number, isInt: boolean, out: Out, depth: number): number {
    if (isInt && value >= 0 && value <= 255) {
      out.push(F.OP_IMM, value);
    } else {
      const i = this.pool.add(value);
      out.push(F.OP_CONST, i & 0xff, i >> 8);
    }
    return this.pushed(depth);
  }

  private emit(node: Node, out: Out, depth: number): number {
    switch (node.t) {
      case 'num': return this.constant(node.value, node.isInt, out, depth);
      case 'var': return this.variable(node, out, depth);
      case 'index': return this.indexed(node, out, depth);
      case 'unary': {
        const d = this.emit(node.operand, out, depth);
        if (node.op !== '+') out.push(node.op === '-' ? F.OP_NEG : node.op === '~' ? F.OP_BITNOT : F.OP_NOT);
        return d;
      }
      case 'binary': {
        let d = this.emit(node.left, out, depth);
        d = this.emit(node.right, out, d);
        out.push(F.OP_BINARY[node.op]!);
        return d - 1;
      }
      case 'compare': {
        // a < b <= c  is  (a < b) and (b <= c); the middle terms are pure, so twice is fine
        let left = node.left;
        for (let i = 0; i < node.ops.length; i++) {
          const right = node.rights[i]!;
          let d = this.emit(left, out, depth);
          d = this.emit(right, out, d);
          out.push(F.OP_COMPARE[node.ops[i]!]!);
          depth = d - 1;
          if (i) {
            out.push(F.OP_AND);
            depth -= 1;
          }
          left = right;
        }
        return depth;
      }
      case 'bool': {
        depth = this.emit(node.values[0]!, out, depth);
        for (const v of node.values.slice(1)) {
          depth = this.emit(v, out, depth);
          out.push(node.op === 'and' ? F.OP_AND : F.OP_OR);
          depth -= 1;
        }
        if (node.values.length === 1) throw new Unsupported('empty boolean');
        return depth;
      }
      case 'cond': {
        depth = this.emit(node.test, out, depth);
        depth = this.emit(node.then, out, depth);
        depth = this.emit(node.other, out, depth);
        out.push(F.OP_SELECT);
        return depth - 2;
      }
      case 'call': return this.call(node, out, depth);
    }
  }

  private variable(node: Extract<Node, { t: 'var' }>, out: Out, depth: number): number {
    const name = node.name;
    if (node.lag) throw new Unsupported(`[${name}-${node.lag}] needs history`);
    if (this.formula && name === 'x') {
      out.push(F.OP_X);
      return this.pushed(depth);
    }
    if (this.formula && name === 'b') throw new Unsupported("'b' is a list of bytes: write b[0], b[1] ...");
    const ch = this.names.get(name);
    if (ch !== undefined) {
      this.deps.add(name);
      out.push(F.OP_CH, ch);
      return this.pushed(depth);
    }
    const c = this.constants.get(name);
    if (c !== undefined) return this.constant(c, false, out, depth);
    if (this.absent.has(name)) throw new Absent(`'${name}' is OBD2-only: the handheld has no value for it`);
    throw new Unsupported(`unknown name '${name}'`);
  }

  private indexed(node: Extract<Node, { t: 'index' }>, out: Out, depth: number): number {
    if (!(this.formula && node.name === 'b')) throw new Unsupported(`'${node.name}' cannot be indexed`);
    let index = node.index;
    const negative = index.t === 'unary' && index.op === '-';
    if (negative && index.t === 'unary') index = index.operand;
    if (index.t !== 'num') throw new Unsupported('b[...] needs a constant index');
    let i = negative ? -Math.trunc(index.value) : Math.trunc(index.value);
    if (i < 0) i += this.rawLen;
    if (!(i >= 0 && i < 255)) throw new Unsupported('b[...] index out of range');
    out.push(F.OP_BYTE, i);
    return this.pushed(depth);
  }

  private call(node: Extract<Node, { t: 'call' }>, out: Out, depth: number): number {
    const name = node.func;
    let args = node.args;
    if (name === 'time') {
      out.push(F.OP_TIME);
      return this.pushed(depth);
    }
    if (name === 'valid') {
      depth = this.emit(args[0]!, out, depth);
      out.push(F.OP_VALID);
      return depth;
    }
    if (name === 'coalesce') {
      const kept: Node[] = [];
      for (const arg of args) { // an argument that can never have a value adds nothing
        try {
          this.emit(arg, new Out(), depth);
        } catch (e) {
          if (e instanceof Absent) continue;
          throw e;
        }
        kept.push(arg);
      }
      if (!kept.length) throw new Unsupported('coalesce(): no argument has a value on the handheld');
      args = kept;
      depth = this.emit(args[0]!, out, depth);
      for (const arg of args.slice(1)) {
        depth = this.emit(arg, out, depth);
        out.push(F.OP_COALESCE);
        depth -= 1;
      }
      return depth;
    }
    if (STATEFUL.has(name)) throw new Unsupported(`${name}() needs history`);
    if (!(name in F.FN)) throw new Unsupported(`${name}() is not available on the handheld`);
    const [lo, hi] = PURE[name]!;
    const n = args.length;
    if (n < lo || (hi !== null && n > hi) || n > 255) throw new Unsupported(`${name}() takes another number of arguments`);
    const start = depth;
    for (const arg of args) depth = this.emit(arg, out, depth);
    out.push(F.OP_CALL, F.FN[name]!, n);
    return start + 1;
  }
}

/** A channel formula: ``x`` is the raw number, ``b[i]`` its bytes. */
export function compileFormula(text: string, pool: Pool, constants: Map<string, number>, rawLen: number): Uint8Array {
  return new Compiler(pool, new Map(), constants, true, rawLen).compile(text);
}

/** A math channel or alarm condition over channel values: the program and the names it reads. */
export function compileCondition(text: string, pool: Pool, names: Map<string, number>, constants: Map<string, number>,
  absent: Iterable<string> = []): [Uint8Array, Set<string>] {
  const comp = new Compiler(pool, names, constants, false, 0, absent);
  return [comp.compile(text), comp.deps];
}
