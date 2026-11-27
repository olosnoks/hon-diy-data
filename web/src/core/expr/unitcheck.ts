/**
 * Light unit checking: catch 'kPa + °C' and 'ect > batt', nothing clever.
 *
 * Units are the free-text strings channels declare. Adding, subtracting or
 * comparing two values whose declared units differ gives a warning. Scaling
 * by a plain number keeps the unit; multiplying two channels gives "unknown",
 * which is compatible with anything. Warnings never stop an expression.
 */

import type { Call, Node } from './nodes';

const SAME_AS_FIRST = new Set(['abs', 'avg', 'minw', 'maxw', 'smooth', 'median', 'ema', 'prev', 'round',
  'floor', 'ceil', 'int', 'float', 'delta', 'stdev']);
const ALL_ALIKE = new Set(['min', 'max', 'clamp', 'between', 'coalesce']);

/** Python str.casefold(), close enough for unit text. */
function casefold(s: string): string {
  return s.toLowerCase().replace(/ß/g, 'ss');
}

function norm(unit: string | null | undefined): string | null {
  const u = (unit ?? '').trim();
  return casefold(u).replace(/ /g, '') || null;
}

class Checker {
  readonly warnings: string[] = [];
  private readonly names = new Map<string, string>();     // normalised -> as first written

  constructor(private readonly units: Readonly<Record<string, string>>) {}

  known(unit: string | null | undefined): string | null {
    const key = norm(unit);
    if (key !== null && !this.names.has(key)) this.names.set(key, (unit ?? '').trim());
    return key;
  }

  show(key: string): string {
    return this.names.get(key) ?? key;
  }

  same(a: string | null, b: string | null, what: string): string | null {
    if (a !== null && b !== null && a !== b) {
      this.warnings.push(`${what} mixes ${this.show(a)} and ${this.show(b)}`);
      return null;
    }
    return a !== null ? a : b;
  }

  unit(node: Node): string | null {
    switch (node.type) {
      case 'num':
      case 'index':
        return null;
      case 'var':
        return this.known(Object.prototype.hasOwnProperty.call(this.units, node.name) ? this.units[node.name] : null);
      case 'unary': {
        const inner = this.unit(node.operand);
        return node.op === '+' || node.op === '-' ? inner : null;
      }
      case 'binary': {
        const left = this.unit(node.left);
        const right = this.unit(node.right);
        if (node.op === '+' || node.op === '-') return this.same(left, right, `'${node.op}'`);
        if (node.op === '*') {
          if (node.right.type === 'num') return left;
          return node.left.type === 'num' ? right : null;
        }
        if (node.op === '/' && node.right.type === 'num') return left;
        return null;
      }
      case 'compare': {
        let prev = this.unit(node.left);
        node.ops.forEach((op, i) => {
          const cur = this.unit(node.rights[i]!);
          this.same(prev, cur, `comparison '${op}'`);
          prev = cur;
        });
        return null;
      }
      case 'bool':
        for (const v of node.values) this.unit(v);
        return null;
      case 'cond':
        this.unit(node.test);
        return this.same(this.unit(node.then), this.unit(node.other), "'? :'");
      case 'call':
        return this.call(node);
    }
  }

  private call(node: Call): string | null {
    const units = node.args.map((a) => this.unit(a));
    const first = units.length ? units[0]! : null;
    if (SAME_AS_FIRST.has(node.func)) return first;
    if (ALL_ALIKE.has(node.func)) {
      let out = first;
      for (const other of units.slice(1)) out = this.same(out, other, `${node.func}()`);
      return node.func === 'between' ? null : out;
    }
    if ((node.func === 'slope' || node.func === 'rate') && first !== null) return this.known(`${this.show(first)}/s`);
    if (node.func === 'integ' && first !== null) return this.known(`${this.show(first)}·s`);
    return null;
  }
}

/**
 * Warnings about mixed units in an expression (an empty list is good).
 *
 * ``units`` maps names to unit text; ``declared`` is the unit the result is
 * supposed to have, checked when the expression's own unit can be worked out.
 */
export function checkUnits(node: Node, units: Readonly<Record<string, string>>, declared: string | null = null): string[] {
  const checker = new Checker(units);
  const result = checker.unit(node);
  const want = checker.known(declared);
  if (result !== null && want !== null && result !== want) {
    checker.warnings.push(`result is in ${checker.show(result)} but declared as ${checker.show(want)}`);
  }
  return checker.warnings;
}
