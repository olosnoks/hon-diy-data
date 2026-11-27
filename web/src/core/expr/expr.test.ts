/** The safe expression evaluator: grammar, safety, state, column back end
 *  (port of tests/test_expr.py). */

import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { EvalError, ExprError, checkUnits, compileExpr, functionHelp, parse, references } from './index';
import { PURE, STATEFUL } from './funcs';

const ev = (text: string, values: Record<string, number | null> = {}, t = 0.0) => compileExpr(text).eval(values, t);
const approx = (a: number | null | undefined, b: number, tol = 1e-9) => {
  expect(a).not.toBeNull();
  expect(Math.abs((a as number) - b)).toBeLessThanOrEqual(tol * Math.max(1, Math.abs(b)));
};

// ---- grammar and arithmetic -------------------------------------------------

const CONSTANTS: [string, number][] = [
  ['1 + 2 * 3', 7], ['(1 + 2) * 3', 9], ['7 // 2', 3], ['7 % 3', 1], ['-7 % 3', 2],
  ['2 ** 3 ** 2', 512], ['-2 ** 2', -4], ['2 ** -1', 0.5], ['10 / 4', 2.5],
  ['0x10 + 0b101 + 1e1 + .5', 31.5], ['1 << 4 | 1', 17], ['6 & 3 ^ 1', 3], ['~5', -6],
  ['255 >> 4', 15], ['3 > 2', 1], ['3 > 2 > 1', 1], ['3 > 2 > 5', 0], ['1 == 1.0', 1],
  ['2 != 2', 0], ['!0', 1], ['not 3', 0], ['1 && 0', 0], ['1 and 2', 1], ['0 || 0', 0],
  ['0 or 7', 1], ['1 ? 2 : 3', 2], ['0 ? 2 : 3', 3], ['2 if 1 else 3', 2],
  ['0 ? 1 : 0 ? 2 : 3', 3], ['1 if 0 else 2 if 0 else 3', 3], ['true + false', 1],
  ['abs(-3)', 3], ['min(3, 1, 2)', 1], ['max(3, 1, 2)', 3], ['round(2.567, 1)', 2.6],
  ['round(2.5)', 2], ['int(-2.7)', -2], ['floor(-2.5)', -3], ['ceil(2.1)', 3],
  ['sqrt(16)', 4], ['pow(2, 10)', 1024], ['log10(1000)', 3], ['exp(0)', 1], ['ln(1)', 0],
  ['sign(-4)', -1], ['clamp(12, 0, 10)', 10], ['between(5, 1, 9)', 1],
  ['bit(0b1000, 3)', 1], ['signed(0xFF)', -1], ['signed(0x8000, 16)', -32768],
  ['poly(2, 1, 2, 3)', 17], ['table(5, 0, 0, 10, 100)', 50], ['table(-1, 0, 3, 10, 100)', 3],
  ['table(99, 0, 3, 10, 100)', 100],
];

describe('grammar and arithmetic', () => {
  it.each(CONSTANTS)('%s = %d (folded)', (text, want) => {
    const e = compileExpr(text);
    approx(e.eval({}), want);
    approx(e.constant, want);
    expect([...e.evalArray({}, new Float64Array(3))].every((x) => Math.abs(x - want) < 1e-9)).toBe(true);
  });

  it('valid and coalesce', () => {
    for (const [text, want] of [['coalesce(1/0, 4)', 4], ['valid(1/0)', 0], ['valid(3)', 1],
      ['coalesce(x, 1/0, 9)', 9]] as const) {
      const e = compileExpr(text);
      expect(e.eval({ x: null })).toBe(want);
      expect([...e.evalArray({ x: [NaN] }, [0.0])]).toEqual([want]);
    }
    expect(compileExpr('coalesce(x, x)').eval({ x: null })).toBeNull();
  });

  it('channel references and names', () => {
    expect(ev('[rpm] / 4', { rpm: 3000 })).toBe(750);
    expect(ev('rpm / 4', { rpm: 3000 })).toBe(750);
    const e = compileExpr('[inj] * [rpm] / 1200 + k', { constants: { k: 1 } });
    expect([...e.deps].sort()).toEqual(['inj', 'rpm']);
    approx(e.eval({ inj: 2.4, rpm: 3000 }), 7.0);
    expect([...references('[a] + b[0] + avg(c, 5)')].sort()).toEqual(['a', 'b', 'c']);
  });

  it('a variable shadows a constant of the same name', () => {
    const e = compileExpr('k * 2', { names: ['k'], constants: { k: 100 } });
    expect(e.eval({ k: 3 })).toBe(6);
    expect([...e.evalArray({ k: [3.0] }, [0.0])]).toEqual([6]);
  });

  it('unknown names are rejected with a hint', () => {
    expect(() => compileExpr('[rmp] + 1', { names: ['rpm', 'map'] })).toThrow(/unknown name 'rmp'.*did you mean rpm/);
    expect(() => compileExpr('averge(rpm, 5)', { names: ['rpm'] })).toThrow(/unknown function 'averge'.*avg/);
    expect(() => compileExpr('[k-1]', { names: [], constants: { k: 1 } })).toThrow(/constant/);
  });

  it('any name is allowed when names is null', () => {
    expect(ev('anything + 1', { anything: 2 })).toBe(3);
    expect(ev('anything + 1')).toBeNull();
  });

  it.each([
    ['', 'empty'], ['1 +', 'expected a value'], ['(1', "expected '\\)'"], ['1 1', 'operator'],
    ['a = 1', '=='], ["'x'", 'text is not allowed'], ['a.b', 'attribute'], ['a @ b', 'unexpected'],
    ['avg(x)', 'takes 2'], ['avg(x, y)', 'must be a constant'], ['avg(x, 0)', 'more than 0'],
    ['smooth(x, 0)', '1 to'], ['min(1)', '2 or more'], ['round(1, x)', 'constant'],
    ['table(x, 0, 1, 2, 3, 4)', 'pairs'], ['table(x, 5, 1, 2, 3)', 'go up'], ['[x-]', 'whole number'],
    ['[x-1.5]', 'whole number'], ['[3]', 'channel name'], ['x[0]', 'cannot be indexed'],
    ['if', 'expected a value'], ['time(1)', 'takes 0'], ['[x-99999]', '10000'],
    ['x ? 1', "expected ':'"], ['1 if x', "expected 'else'"],
  ])('syntax error %j names the problem', (bad, why) => {
    expect(() => compileExpr(bad)).toThrow(new RegExp(why));
    expect(() => compileExpr(bad)).toThrow(ExprError);
  });

  it('the error carries its position', () => {
    try {
      compileExpr('1 + $');
      expect.unreachable();
    } catch (exc) {
      expect(exc).toBeInstanceOf(ExprError);
      expect((exc as ExprError).pos).toBe(4);
      expect(String((exc as ExprError).message)).toContain('column 5');
    }
  });
});

// ---- safety -----------------------------------------------------------------

describe('safety', () => {
  it.each([
    "__import__('os').system('true')", '().__class__.__bases__[0]', "open('/etc/passwd')",
    "eval('1')", "exec('x=1')", 'lambda: 1', '[c for c in x]', 'x.__class__', 'globals()',
    "getattr(x, 'real')", 'print(1)', "compile('1','','eval')", '{1: 2}', 'x; y', 'x\ny',
    "f'{x}'", "b'\\x00'", '1 if x else __builtins__', 'type(x)', 'breakpoint()',
    'x := 5', '*x', '**x', 'x[::1]', 'yield', 'await x', 'import os',
    'constructor', 'x.constructor', '__proto__', 'toString()', 'hasOwnProperty(x)', 'Function(1)',
  ])('code injection %j is a syntax error', (attack) => {
    expect(() => compileExpr(attack, { names: ['x', 'y'] })).toThrow(ExprError);
  });

  it('only whitelisted functions exist', () => {
    for (const name of ['eval', 'exec', 'open', 'getattr', 'type', 'len', 'str', 'map', 'print',
      'constructor', 'toString', '__proto__', 'valueOf', 'hasOwnProperty']) {
      expect(Object.prototype.hasOwnProperty.call(PURE, name)).toBe(false);
      expect(Object.prototype.hasOwnProperty.call(STATEFUL, name)).toBe(false);
      expect(() => compileExpr(`${name}(1)`)).toThrow(ExprError);
    }
  });

  it('no eval or Function in the evaluator source', () => {
    const dir = resolve(process.cwd(), 'src/core/expr');
    for (const file of ['compiler.ts', 'parser.ts', 'funcs.ts', 'unitcheck.ts', 'nodes.ts', 'pynum.ts',
      'mathch.ts', 'difflib.ts', '../alarms/engine.ts']) {
      const src = readFileSync(resolve(dir, file), 'utf-8').replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '');
      expect(src, file).not.toMatch(/(?<![.\w])eval\s*\(|new\s+Function\b|\bFunction\s*\(|\bimport\s*\(/);
    }
  });

  it('resource limits', () => {
    expect(() => compileExpr('1+'.repeat(2000) + '1')).toThrow(/longer/);
    expect(() => compileExpr('('.repeat(200) + '1' + ')'.repeat(200))).toThrow(/nested too deeply/);
    expect(() => compileExpr('-'.repeat(500) + '1')).toThrow(/nested too deeply|chain/);
    expect(() => compileExpr('2**'.repeat(300) + '2')).toThrow(/nested too deeply|chain/);
    expect(() => compileExpr(Array(400).fill('x').join('+'))).toThrow(/chain/);
    expect(compileExpr(Array(150).fill('1').join('+')).eval({})).toBe(150);
    expect(compileExpr('1 << 4000').eval({})).toBeNull();
    expect(compileExpr('9 ** 9 ** 9').eval({})).toBeNull();
    expect(compileExpr('1e308 * 10').eval({})).toBeNull();
  });
});

// ---- invalid values never throw ---------------------------------------------

describe('invalid values', () => {
  it.each([
    '1 / x', 'x / 0', '7 % 0', '7 // 0', 'sqrt(-1)', 'ln(0)', '(-8) ** 0.5', '0 ** -1',
    'exp(9999)', 'y + 1', 'x > y', 'y && x', '!y', 'y ? 1 : 2', 'bit(1, 99)', '1 << -1',
    'min(x, y)', 'thermistor(0, 1, 1, 1)', 'int(1e400)', 'missing * 2',
  ])('%s gives null', (text) => {
    const e = compileExpr(text);
    expect(e.eval({ x: 0, y: null })).toBeNull();
    expect(e.truth({ x: 0, y: null })).toBeNull();
    expect(() => e.evalStrict({ x: 0, y: null })).toThrow(EvalError);
  });

  it('a NaN input counts as missing', () => {
    expect(ev('x > 1', { x: NaN })).toBeNull();
    expect(ev('x + 1', { x: NaN })).toBeNull();
  });

  it('short circuits skip invalid branches', () => {
    expect(ev('x > 0 && y > 0', { x: 0, y: null })).toBe(0);
    expect(ev('x > 0 || y > 0', { x: 1, y: null })).toBe(1);
    expect(ev('x > 0 ? y : 5', { x: 0, y: null })).toBe(5);
    expect(ev('coalesce(y, x)', { x: 7, y: null })).toBe(7);
    expect(ev('valid(y) ? y : -1', { y: null })).toBe(-1);
  });

  it('truth', () => {
    const e = compileExpr('[ect] > 105 && [rpm] > 400');
    expect(e.truth({ ect: 110, rpm: 800 })).toBe(true);
    expect(e.truth({ ect: 90, rpm: 800 })).toBe(false);
    expect(e.truth({ ect: null, rpm: 800 })).toBeNull();
    expect(e.test({ ect: null, rpm: 800 })).toBe(false);
  });

  it('evalStrict names the problem like Python', () => {
    expect(() => compileExpr('1 / x').evalStrict({ x: 0 })).toThrow('1 / x: ZeroDivisionError: float division by zero');
    expect(() => compileExpr('y + 1').evalStrict({ y: null })).toThrow('y + 1: Invalid: y');
  });
});

// ---- stateful helpers -------------------------------------------------------

function feed(text: string, xs: (number | null)[], dt = 0.1): (number | null)[] {
  const e = compileExpr(text);
  return xs.map((x, i) => e.eval({ x }, i * dt));
}

function close(got: (number | null)[], want: (number | null)[], tol = 1e-9): void {
  expect(got.length).toBe(want.length);
  got.forEach((g, i) => {
    if (want[i] === null) expect(g, `#${i}`).toBeNull();
    else approx(g, want[i]!, tol);
  });
}

describe('stateful helpers', () => {
  it('previous sample', () => {
    expect(feed('[x-1]', [1, 2, 3])).toEqual([null, 1, 2]);
    expect(feed('[x-2]', [1, 2, 3, 4])).toEqual([null, null, 1, 2]);
    expect(feed('prev(x)', [1, 2, 3])).toEqual([null, 1, 2]);
    expect(feed('prev(x, 2)', [1, 2, 3])).toEqual([null, null, 1]);
    expect(feed('x - [x-1]', [1, 4, 9])).toEqual([null, 3, 5]);
    expect(feed('delta(x)', [1, 4, 9])).toEqual([null, 3, 5]);
  });

  it('rate and slope are per second', () => {
    close(feed('rate(x)', [0, 10, 30]), [null, 100, 200]);
    const out = feed('slope(x, 1000)', Array.from({ length: 20 }, (_, i) => 5 * i));
    expect(out[0]).toBeNull();
    close(out.slice(1), Array(19).fill(50.0), 1e-6);
    const noisy = feed('slope(x, 1000)', [0, 1, 2, 3, 40, 5, 6, 7, 8, 9, 10]);
    const rate = feed('rate(x)', [0, 1, 2, 3, 40, 5]);
    expect(Math.abs(noisy[noisy.length - 1]! - 10)).toBeLessThan(Math.abs(rate[rate.length - 1]! - 10));
  });

  it('time windows', () => {
    const xs = [1, 2, 3, 4, 5, 6];
    close(feed('avg(x, 250)', xs), [1, 1.5, 2, 3, 4, 5]);
    expect(feed('minw(x, 250)', [5, 3, 4, 6, 7, 8])).toEqual([5, 3, 3, 3, 4, 6]);
    expect(feed('maxw(x, 250)', [5, 8, 4, 3, 2, 1])).toEqual([5, 8, 8, 8, 4, 3]);
    close(feed('stdev(x, 150)', [1, 3, 3, 7]), [0, 1, 0, 2]);
    close(feed('smooth(x, 3)', xs), [1, 1.5, 2, 3, 4, 5]);
    expect(feed('median(x, 3)', [1, 100, 2, 3, 3])).toEqual([1, 50.5, 2, 3, 3]);
    close(feed('integ(x)', [2, 2, 4]), [0, 0.2, 0.5]);
  });

  it('ema settles on a step', () => {
    const out = feed('ema(x, 200)', [0, ...Array(30).fill(10)]) as number[];
    approx(out[1], 10 * (1 - Math.exp(-0.5)));
    expect(Math.abs(out[out.length - 1]! - 10)).toBeLessThan(1e-3);
    out.slice(1).forEach((b, i) => expect(b).toBeGreaterThanOrEqual(out[i]!));
  });

  it('held needs an unbroken run', () => {
    expect(feed('held(x > 5, 200)', [9, 9, 9, 0, 9, 9, 9])).toEqual([0, 0, 1, 0, 0, 0, 1]);
  });

  it('windows skip invalid samples and advance in untaken branches', () => {
    expect(feed('avg(x, 1000)', [2, null, 4])).toEqual([2, null, 3]);
    const e = compileExpr('go ? avg(x, 1000) : -1');
    const seq = [[0, 10], [0, 20], [1, 30]];
    expect(seq.map(([g, x], i) => e.eval({ go: g!, x: x! }, i * 0.1))).toEqual([-1, -1, 20]);
  });

  it('reset and time going backwards', () => {
    const e = compileExpr('avg(x, 1000) + [x-1]');
    expect(e.eval({ x: 4 }, 0.0)).toBeNull();
    expect(e.eval({ x: 6 }, 0.1)).toBe(9);
    e.reset();
    expect(e.eval({ x: 100 }, 0.2)).toBeNull();
    expect(e.eval({ x: 100 }, 0.3)).toBe(200);
    const w = compileExpr('maxw(x, 1000) + minw(x, 1000) + avg(x, 1000)');
    w.eval({ x: 50 }, 10.0);
    expect(w.eval({ x: 1 }, 1.0)).toBe(3);
  });

  it('long runs do not drift', () => {
    // Python's random.Random is not available: a fixed LCG gives the same kind of noise.
    let seed = 3;
    const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
    const e = compileExpr('avg(x, 300) + slope(x, 300) * 0 + smooth(x, 4) * 0 + stdev(x, 300) * 0');
    let last: number[] = [];
    let got: number | null = null;
    for (let i = 0; i < 20000; i++) {
      const x = 1e6 + rnd();
      last = [...last, x].slice(-4);
      got = e.eval({ x }, i * 0.1);
    }
    approx(got, last.reduce((a, b) => a + b) / 4, 1e-12);
    const s = compileExpr('slope(x, 500)');
    for (let i = 0; i < 20000; i++) got = s.eval({ x: 3.0 * (i * 0.05) + 7 }, i * 0.05);
    approx(got, 3.0, 1e-6);
  });

  it('flags and time', () => {
    const e = compileExpr('time() > 1 ? 1 : 0');
    expect(e.usesTime && !e.stateful).toBe(true);
    expect(e.eval({}, 0.5)).toBe(0);
    expect(e.eval({}, 2)).toBe(1);
    expect(compileExpr('avg(x, 5)').stateful && compileExpr('[x-1]').stateful).toBe(true);
    expect(compileExpr('x + 1').stateful).toBe(false);
    expect([...compileExpr('avg(x, 5) + bit(x, 1)').functions].sort()).toEqual(['avg', 'bit']);
    expect(functionHelp().some((line) => line.includes('avg(x, ms)'))).toBe(true);
  });
});

// ---- column back end --------------------------------------------------------

describe('evalArray', () => {
  it('basics', () => {
    const t = [0, 0.1, 0.2, 0.3];
    const cols = { rpm: [800.0, 3000, NaN, 6000], inj: [2.0, 4, 6, 0] };
    const out = compileExpr('[inj] * [rpm] / 1200').evalArray(cols, t);
    approx(out[0], 800 * 2 / 1200);
    approx(out[1], 10);
    expect(Number.isNaN(out[2])).toBe(true);
    expect(out[3]).toBe(0);
    expect(Number.isNaN(compileExpr('1 / [inj]').evalArray(cols, t)[3])).toBe(true);
    const flags = compileExpr('[rpm] > 2000 && [inj] > 3').evalArray(cols, t);
    expect(flags[0]).toBe(0);
    expect(flags[1]).toBe(1);
    expect(Number.isNaN(flags[2])).toBe(true);
    expect(flags[3]).toBe(0);
    expect(compileExpr('[rpm] > 0').evalArray(cols).length).toBe(4);
    expect(() => compileExpr('avg([rpm], 100)').evalArray(cols)).toThrow(/time column/);
    expect(() => compileExpr('[nope] + 1').evalArray(cols, t)).toThrow(/no column/);
    expect(() => compileExpr('[rpm] + 1').evalArray({ rpm: [1.0, 2.0] }, t)).toThrow(/rows/);
    expect(() => compileExpr('b[0]', { arrays: ['b'] }).evalArray({}, t)).toThrow(/formulas/);
    expect([...compileExpr('[rpm] > 2000').mask(cols, t)]).toEqual([0, 1, 0, 1]);
  });

  it('does not touch live state', () => {
    const e = compileExpr('avg(x, 1000)');
    expect(e.eval({ x: 10 }, 0.0)).toBe(10);
    e.evalArray({ x: [1000.0, 2000.0] }, [0.0, 0.1]);
    expect(e.eval({ x: 20 }, 0.1)).toBe(15);
  });
});

// ---- units ------------------------------------------------------------------

const UNITS = { ect: '°C', iat: '°C', map: 'kPa', baro: 'kPa', batt: 'V', rpm: 'rpm' };

describe('unit check', () => {
  it.each([
    ['[ect] - [iat]', '°C', null],
    ['[map] / [baro] * 100', '%', null],
    ['[ect] * 1.8 + 32', null, null],
    ['avg([map], 500) - [baro]', 'kPa', null],
    ['[ect] + [map]', null, 'mixes °C and kPa'],
    ['[ect] > [batt]', null, 'mixes °C and V'],
    ['max([map], [batt])', null, 'mixes kPa and V'],
    ['[rpm] > 1000 ? [ect] : [batt]', null, 'mixes °C and V'],
    ['[ect] - [iat]', 'kPa', 'declared as kPa'],
    ['slope([rpm], 500)', 'rpm/s', null],
    ['slope([rpm], 500)', 'rpm', 'declared as rpm'],
    ['minw([batt], 5000) < 12', null, null],
  ])('%s declared %s', (text, declared, problem) => {
    const warnings = checkUnits(parse(text), UNITS, declared);
    if (problem === null) expect(warnings).toEqual([]);
    else expect(warnings.some((w) => w.includes(problem))).toBe(true);
  });
});
