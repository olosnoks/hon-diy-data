/** Math channels: ordering, cycles, live evaluation, replay (port of tests/test_mathch.py). */

import { describe, expect, it } from 'vitest';
import shipped from './__golden__/shipped.json';
import { MathChannel, MathEngine, MathError } from './index';

const INPUTS = ['rpm', 'inj', 'map', 'stft', 'ltft'];

function engine(defs: [string, string][], opts: { inputs?: string[]; constants?: Record<string, number> } = {}) {
  return new MathEngine(defs.map(([name, expr]) => new MathChannel({ name, expr })), opts.inputs ?? INPUTS, opts.constants);
}

const near = (a: number | null | undefined, b: number) => expect(Math.abs((a as number) - b)).toBeLessThan(1e-9 * Math.max(1, Math.abs(b)));

describe('math channels', () => {
  it('evaluates and orders dependencies', () => {
    const e = engine([['flow', '[duty] * 0.576'], ['duty', '[inj] * [rpm] / 1200'], ['flow2', 'flow * 2']]);
    expect(e.names.indexOf('duty')).toBeLessThan(e.names.indexOf('flow'));
    expect(e.names.indexOf('flow')).toBeLessThan(e.names.indexOf('flow2'));
    const out = e.update({ inj: 2.4, rpm: 3000.0 }, 0.0);
    near(out.duty, 6.0);
    near(out.flow, 3.456);
    near(out.flow2, 6.912);
    expect([...e.baseInputs('flow2')].sort()).toEqual(['inj', 'rpm']);
    expect(e.length).toBe(3);
    expect(e.channel('duty')!.expr).toBeTruthy();
    expect(e.channel('zz')).toBeNull();
  });

  it('an invalid input makes dependants invalid, not an exception', () => {
    const e = engine([['duty', '[inj] * [rpm] / 1200'], ['flow', '[duty] * 2'], ['trim', '[stft] + 1']]);
    expect(e.update({ inj: null, rpm: 3000.0, stft: 2.0 }, 0.0)).toEqual({ duty: null, flow: null, trim: 3.0 });
    expect(e.update({}, 0.1)).toEqual({ duty: null, flow: null, trim: null });
  });

  it('cycles are refused with the loop spelled out', () => {
    expect(() => engine([['a', '[b] + 1'], ['b', '[c] + 1'], ['c', '[a] + 1']])).toThrow(/loop: a -> b -> c -> a/);
    expect(() => engine([['a', '[a-1] + 1']])).toThrow(/loop: a -> a/);
  });

  it.each([
    [[['a', '[nope] + 1']], {}, "unknown name 'nope'"],
    [[['a', '1 +']], {}, "math channel 'a'"],
    [[['a', '1'], ['a', '2']], {}, 'duplicate'],
    [[['rpm', '1']], {}, 'same name as a measured channel'],
    [[['k', '1']], { constants: { k: 2.0 } }, 'same name as a constant'],
  ] as [[string, string][], { constants?: Record<string, number> }, string][])('bad definition %j', (defs, kw, why) => {
    expect(() => engine(defs, kw)).toThrow(MathError);
    expect(() => engine(defs, kw)).toThrow(why);
  });

  it('bad channel fields', () => {
    expect(() => new MathChannel({ name: '9lives', expr: '1' })).toThrow(MathError);
    expect(() => new MathChannel({ name: 'a', expr: '  ' })).toThrow(MathError);
    expect(() => new MathChannel({ name: 'a', expr: '1', kind: 'gauge' })).toThrow(MathError);
    const flag = new MathChannel({ name: 'lean', expr: '[o2] < 0.2', kind: 'flag' });
    expect([flag.min, flag.max, flag.decimals, flag.label]).toEqual([0.0, 1.0, 0, 'lean']);
    expect(new MathChannel({ name: 'a', expr: '1', decimals: 2 }).format(1.234)).toBe('1.23');
    expect(new MathChannel({ name: 'a', expr: '1' }).format(null)).toBe('--');
    expect(new MathChannel({ name: 'a', expr: '1', decimals: 2 }).format(0.125)).toBe('0.12');   // Python rounds half to even
  });

  it('constants and disabled channels', () => {
    const e = new MathEngine([new MathChannel({ name: 'flow', expr: '[inj] * cc', units: 'cc' }),
      new MathChannel({ name: 'off', expr: '[nope]', enabled: false })], ['inj'], { cc: 240 });
    expect(e.names).toEqual(['flow']);
    expect(e.update({ inj: 2.0 }, 0)).toEqual({ flow: 480.0 });
  });

  it('unit warnings do not stop anything', () => {
    const e = new MathEngine([new MathChannel({ name: 'odd', expr: '[ect] + [map]' }),
      new MathChannel({ name: 'dt', expr: '[ect] - [iat]', units: 'kPa' })],
    ['ect', 'map', 'iat'], null, { ect: '°C', map: 'kPa', iat: '°C' });
    expect(e.warnings.some((w) => w.includes('odd') && w.includes('mixes'))).toBe(true);
    expect(e.warnings.some((w) => w.includes('dt') && w.includes('declared'))).toBe(true);
    expect(e.update({ ect: 80.0, map: 30.0, iat: 20.0 }, 0).odd).toBe(110);
  });

  it('runs at the rate of its fastest input', () => {
    const e = engine([['fast', '[rpm] * 2'], ['slow', '[map] + 1'], ['both', '[fast] + [slow]'], ['clock', 'time()']]);
    let v = { rpm: 1000.0, map: 40.0 };
    expect(e.update(v, 0.0)).toEqual({ fast: 2000, slow: 41, both: 2041, clock: 0.0 });
    v = { rpm: 1100.0, map: 99.0 };
    expect(e.update(v, 0.1, ['rpm'])).toEqual({ fast: 2200, slow: 41, both: 2241, clock: 0.1 });
    expect(e.update(v, 0.2, ['map'])).toEqual({ fast: 2200, slow: 100, both: 2300, clock: 0.2 });
  });

  it('extend and reset', () => {
    const e = engine([['d', '[rpm] - [rpm-1]']]);
    expect(e.extend({ rpm: 100.0 }, 0)).toEqual({ rpm: 100.0, d: null });
    expect(e.update({ rpm: 150.0 }, 0.1)).toEqual({ d: 50 });
    e.reset();
    expect(e.update({ rpm: 170.0 }, 0.2)).toEqual({ d: null });
  });

  it('replay matches live', () => {
    const e = engine([['duty', '[inj] * [rpm] / 1200'], ['rate', 'slope([rpm], 400)'],
      ['smooth', 'smooth([duty], 3)'], ['flag', '[rate] > 100 && [duty] > 2']]);
    let seed = 1;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    const n = 200;
    const t: number[] = [];
    let acc = 0;
    for (let i = 0; i < n; i++) t.push((acc += 0.04 + 0.07 * rnd()));
    const rpm = t.map((x) => 800 + 2000 * Math.abs(Math.sin(x)));
    const inj = t.map(() => 2 + 2 * rnd());
    for (const i of [5, 50, 51]) inj[i] = NaN;
    const series = e.computeSeries(t, { rpm, inj });
    e.reset();
    for (let i = 0; i < n; i++) {
      const live = e.update({ rpm: rpm[i]!, inj: Number.isNaN(inj[i]) ? null : inj[i]! }, t[i]!);
      for (const [name, value] of Object.entries(live)) {
        if (value === null) expect(Number.isNaN(series[name]![i])).toBe(true);
        else near(series[name]![i], value);
      }
    }
    expect([...e.computeSeries(t, { rpm }).duty!].every(Number.isNaN)).toBe(true);
  });

  it('shipped math channels on plausible values', () => {
    const s = shipped as unknown as { constants: Record<string, number>; names: string[]; units: Record<string, string>;
      math: ConstructorParameters<typeof MathChannel>[0][] };
    const e = new MathEngine(s.math.map((m) => new MathChannel(m)), s.names, s.constants, s.units);
    expect(e.warnings).toEqual([]);
    for (const n of ['inj_duty', 'fuel_flow', 'total_trim', 'rpm_rate', 'load', 'afr_est']) expect(e.names).toContain(n);
    const v: Record<string, number> = Object.fromEntries(s.names.filter((n) => !n.startsWith('calc_')).map((n) => [n, 0.0]));
    delete v.calc_load;
    Object.assign(v, { rpm: 3000.0, inj: 4.0, map: 60.0, baro: 100.0, iat: 27.0, stft: 3.0, ltft: -1.0, batt: 14.0 });
    e.update(v, 0.0);
    v.rpm = 3100.0;
    const out = e.update(v, 0.5);
    near(out.inj_duty, 4 * 3100 / 1200);
    near(out.fuel_flow, out.inj_duty! / 100 * 240 * 4 * 60 / 1000);
    expect(out.total_trim).toBe(2.0);
    near(out.rpm_rate, 200.0);
    expect(out.load).toBe(60.0);
    expect(out.afr_est! > 10 && out.afr_est! < 20).toBe(true);
    expect(out.batt_min_5s).toBe(14.0);
    expect(out.rpm_smooth).toBe(3050.0);
    Object.assign(v, { rpm: 0.0, inj: 0.0 });
    expect(e.update(v, 1.0).afr_est).toBeNull();
  });
});
