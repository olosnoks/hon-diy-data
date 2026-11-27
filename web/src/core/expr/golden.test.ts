/**
 * Golden tests against the Python app (files written by __golden__/gen_golden.py):
 * every channel formula of the shipped address map over raw byte inputs,
 * hundreds of random expressions with gaps in the data, Python number edge
 * cases, error messages, number formatting and the shipped math channels.
 */

import { describe, expect, it } from 'vitest';
import formulas from './__golden__/formulas.json';
import randomExprs from './__golden__/random_exprs.json';
import errors from './__golden__/errors.json';
import formatting from './__golden__/formatting.json';
import shipped from './__golden__/shipped.json';
import unitsGolden from './__golden__/units.json';
import help from './__golden__/function_help.json';
import { ExprError, MathChannel, MathEngine, checkUnits, compileExpr, functionHelp, parse } from './index';
import { pyFormat, pyRound, reprFloat } from './pynum';
import { fillTemplate } from '../alarms/engine';

const REL = 1e-12;

function same(got: number | null, want: number | null, what: string): void {
  if (want === null) {
    expect(got, what).toBeNull();
    return;
  }
  expect(got, what).not.toBeNull();
  if (got === want) return;
  expect(Math.abs(got! - want), `${what}: ${got} vs ${want}`).toBeLessThanOrEqual(REL * Math.max(1e-300, Math.abs(want)));
}

describe('shipped address map formulas', () => {
  it('cover every channel', () => {
    expect(formulas.length).toBeGreaterThanOrEqual(15);
  });

  it.each(formulas.map((f) => [f.name, f] as const))('%s', (_name, f) => {
    const expr = compileExpr(f.formula, { names: ['x'], arrays: ['b'] });
    expect(expr.stateful || expr.usesTime).toBe(false);
    for (const [raw, want] of f.cases as [number[], number | null][]) {
      const bytes = f.endian === 'little' ? [...raw].reverse() : raw;
      let x = 0;
      for (const byte of bytes) x = x * 256 + byte;
      const got = expr.eval({ x, b: Uint8Array.from(raw) });
      same(got, want, `${f.formula} raw=${raw}`);
    }
  });
});

describe('random expressions match the Python evaluator', () => {
  const { t, cols, exprs, fixed } = randomExprs as {
    t: number[]; cols: Record<string, (number | null)[]>;
    exprs: { text: string; out: (number | null)[] }[];
    fixed: { text: string; xs: number[]; out: (number | null)[]; constant: number | null }[];
  };
  const rows = t.map((_, i) => Object.fromEntries(Object.entries(cols).map(([k, v]) => [k, v[i]!])));

  it('scalar, row by row', () => {
    let checked = 0;
    for (const { text, out } of exprs) {
      const e = compileExpr(text, { names: ['a', 'b', 'c'] });
      rows.forEach((row, i) => same(e.eval(row, t[i]), out[i]!, `${text} row ${i}`));
      checked += out.filter((x) => x !== null).length;
    }
    expect(checked).toBeGreaterThan(5000);
  });

  it('whole columns give the same numbers', () => {
    for (const { text, out } of exprs) {
      const col = compileExpr(text, { names: ['a', 'b', 'c'] }).evalArray(cols, t);
      out.forEach((want, i) => same(Number.isNaN(col[i]!) ? null : col[i]!, want, `${text} row ${i}`));
    }
  });

  it('Python number edge cases', () => {
    for (const { text, xs, out, constant } of fixed) {
      xs.forEach((x, i) => same(compileExpr(text).eval({ x }), out[i]!, `${text} x=${x}`));
      const k = compileExpr(text).constant;
      same(k !== null && !Number.isFinite(k) ? null : k, constant, `${text} constant`);
    }
  });
});

describe('error messages are word for word', () => {
  type Case = { text: string; error: string | null; names?: string[]; constants?: Record<string, number>; arrays?: string[]; pos?: number | null };
  it.each((errors as Case[]).map((c) => [c.text.length > 40 ? c.text.slice(0, 40) + '...' : c.text, c] as const))('%j', (_label, c) => {
    const opts = c.names !== undefined
      ? { names: c.names, constants: c.constants, arrays: c.arrays }
      : { names: c.text.includes('x') ? ['x', 'y', 'rpm'] : null };
    let got: string | null = null;
    let pos: number | null | undefined;
    try {
      compileExpr(c.text, opts);
    } catch (exc) {
      expect(exc).toBeInstanceOf(ExprError);
      got = (exc as ExprError).message;
      pos = (exc as ExprError).pos;
    }
    expect(got).toBe(c.error);
    if (c.error !== null && c.pos !== undefined) expect(pos).toBe(c.pos);
  });

  it('function help lists the same lines', () => {
    expect(functionHelp()).toEqual(help);
  });
});

describe('Python number formatting', () => {
  it('format()', () => {
    for (const [v, spec, want] of formatting.format as [number | string, string, string | null][]) {
      let got: string | null;
      try {
        got = pyFormat(v, spec);
      } catch {
        got = null;
      }
      expect(got, `format(${JSON.stringify(v)}, ${JSON.stringify(spec)})`).toBe(want);
    }
  });

  it('round()', () => {
    for (const [v, d, want] of formatting.round as [number, number, number][]) {
      const got = d === 0 ? pyRound(v) : pyRound(v, d);
      expect(Object.is(got, want) || got === want, `round(${v}, ${d}) = ${got}, want ${want}`).toBe(true);
    }
  });

  it('repr()', () => {
    for (const [v, want] of formatting.repr as [number, string][]) expect(reprFloat(v)).toBe(want);
  });

  it('alarm message templates', () => {
    const { fields, cases } = formatting.fill as unknown as { fields: Record<string, number | string | null>; cases: [string, string][] };
    for (const [tpl, want] of cases) expect(fillTemplate(tpl, fields), tpl).toBe(want);
  });
});

describe('unit checks match', () => {
  it('check_units', () => {
    for (const [text, declared, want] of unitsGolden.check as [string, string | null, string[]][]) {
      expect(checkUnits(parse(text), unitsGolden.units, declared), `${text} / ${declared}`).toEqual(want);
    }
  });
});

describe('shipped math channels', () => {
  const s = shipped as unknown as {
    constants: Record<string, number>; names: string[]; units: Record<string, string>;
    math: { name: string; expr: string; label: string; units: string; min: number; max: number; decimals: number; kind: string; enabled: boolean }[];
    math_order: string[]; math_warnings: string[]; base_inputs: Record<string, string[]>;
    t: number[]; rows: Record<string, number | null>[]; math_out: Record<string, number | null>[];
  };

  it('compile in the same order with the same warnings', () => {
    const engine = new MathEngine(s.math.map((m) => new MathChannel(m)), s.names, s.constants, s.units);
    expect(engine.names).toEqual(s.math_order);
    expect(engine.warnings).toEqual(s.math_warnings);
    for (const [name, inputs] of Object.entries(s.base_inputs)) expect([...engine.baseInputs(name)].sort()).toEqual(inputs);
  });

  it('give the same numbers over a synthetic drive, live and replayed', () => {
    const engine = new MathEngine(s.math.map((m) => new MathChannel(m)), s.names, s.constants, s.units);
    s.rows.forEach((row, i) => {
      const out = engine.update(row, s.t[i]!);
      for (const [name, want] of Object.entries(s.math_out[i]!)) same(out[name] ?? null, want, `${name} row ${i}`);
    });
    const cols: Record<string, (number | null)[]> = {};
    for (const key of Object.keys(s.rows[0]!)) cols[key] = s.rows.map((r) => r[key] ?? null);
    const series = engine.computeSeries(s.t, cols);
    s.math_out.forEach((outs, i) => {
      for (const [name, want] of Object.entries(outs)) {
        const v = series[name]![i]!;
        same(Number.isNaN(v) ? null : v, want, `replay ${name} row ${i}`);
      }
    });
  });
});
