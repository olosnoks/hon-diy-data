/** Unit conversion and display units (port of tests/test_units.py and the
 *  Qt-free parts of tests/test_ui_units.py), with a golden table from Python. */

import { beforeEach, describe, expect, it } from 'vitest';
import golden from '../expr/__golden__/units.json';
import shipped from '../expr/__golden__/shipped.json';
import {
  DisplayUnits, METRIC, TABLE, US, UnitPrefs, choices, convert, convertSpan, factors, quantity, type UnitSpec,
} from './index';

const near = (a: number | null, b: number, tol = 1e-9) => expect(Math.abs((a as number) - b)).toBeLessThanOrEqual(tol);

describe('conversions', () => {
  it('convert', () => {
    near(convert(100, '°C', '°F'), 212);
    near(convert(32, 'degF', 'C'), 0);
    near(convert(0, '°C', 'K'), 273.15);
    near(convert(101.325, 'kPa', 'psi'), 14.696, 1e-3);
    near(convert(101.325, 'kpa', 'inHg'), 29.92, 1e-2);
    near(convert(14.696, 'psi', 'bar'), 1.01325, 1e-4);
    near(convert(100, 'km/h', 'mph'), 62.137, 1e-3);
    near(convert(60, 'mph', 'kph'), 96.56, 1e-2);
    near(convert(1.0, 'lambda', 'AFR'), 14.7);
    expect(convert(null, '°C', '°F')).toBeNull();
    near(convertSpan(5, '°C', '°F'), 9);
    for (const [a, b] of [['°C', 'kPa'], ['rpm', 'rpm'], ['V', '°C']] as const) {
      expect(() => convert(1, a, b)).toThrow(RangeError);
      expect(() => convertSpan(1, a, b)).toThrow(RangeError);
    }
  });

  it('round trip every pair', () => {
    for (const table of Object.values(TABLE)) {
      for (const a of Object.keys(table)) for (const b of Object.keys(table)) near(convert(convert(37.5, a, b), b, a), 37.5, 1e-9);
    }
  });

  it('match Python bit for bit', () => {
    for (const [v, a, b, want, span, fac] of golden.convert as [number, string, string, number, number, [number, number]][]) {
      expect(convert(v, a, b)).toBe(want);
      expect(convertSpan(v, a, b)).toBe(span);
      expect(factors(a, b)).toEqual(fac);
    }
  });

  it('prefs', () => {
    const p = new UnitPrefs({ temperature: 'f', pressure: 'PSI', speed: 'mph', mixture: 'afr' });
    expect(p.temperature).toBe('°F');
    expect(p.displayUnit('kPa')).toBe('psi');
    const [v, u] = p.convert(88.0, '°C');
    near(v, 190.4);
    expect(u).toBe('°F');
    expect(p.convert(13.9, 'V')).toEqual([13.9, 'V']);
    expect(p.format(88.0, '°C')).toBe('190 °F');
    expect(p.format(null, '°C')).toBe('--');
    expect(p.format(3.14159, '', 2)).toBe('3.14');
    expect(new UnitPrefs().format(88.0, '°C', 1)).toBe('88.0 °C');
    expect(UnitPrefs.us().speed).toBe('mph');
    expect(UnitPrefs.fromMapping({ pressure: 'inHg', junk: 'x' }).pressure).toBe('inHg');
    expect(choices('°C')).toEqual(['°C', '°F', 'K']);
    expect(choices('rpm')).toEqual([]);
    expect(quantity('km/h')).toBe('speed');
    expect(quantity('%')).toBeNull();
    expect(() => new UnitPrefs({ temperature: 'psi' })).toThrow(RangeError);
  });

  it('the shipped map base units are understood', () => {
    const s = shipped as unknown as { units: Record<string, string>; math: { name: string; units: string }[] };
    expect(quantity(s.units.ect!)).toBe('temperature');
    expect(quantity(s.units.map!)).toBe('pressure');
    expect(quantity(s.units.vss!)).toBe('speed');
    expect(quantity(s.math.find((m) => m.name === 'afr_est')!.units)).toBe('mixture');
  });
});

const spec = (units: string, decimals: number, min: number, max: number, extra: Partial<UnitSpec> = {}): UnitSpec =>
  ({ units, decimals, min, max, kind: 'value', ...extra });

describe('display units', () => {
  let U: DisplayUnits;
  beforeEach(() => {
    U = new DisplayUnits();
  });

  it('nothing is converted until the app loads its settings', () => {
    const ect = spec('°C', 0, -40, 140, { warn_hi: 100, alarm_hi: 108 });
    expect(U.prefs).toBeNull();
    expect(U.spec(ect)).toBe(ect);
    expect(U.withUnit(ect, 88.0)).toBe('88 °C');
    expect(U.convertText('Coolant 110 °C')).toBe('Coolant 110 °C');
    expect(U.limit(ect, 108)).toBe('108 °C');
  });

  it('US units convert what is shown and keep MAP in kPa', () => {
    U.preset('US');
    const ect = spec('°C', 0, -40, 140, { warn_hi: 100, alarm_hi: 108 });
    const shown = U.spec(ect);
    expect([shown.units, shown.min, shown.max]).toEqual(['°F', -40.0, 284.0]);
    expect(shown.warn_hi).toBe(212.0);
    near(shown.alarm_hi!, 226.4);
    expect(U.withUnit(ect, 88.0)).toBe('190 °F');
    expect(U.value(ect, 100.0)).toBe(212.0);
    expect(U.limit(ect, 108)).toBe('226.4 °F');
    expect(U.spec(ect)).toBe(shown);
    const vss = spec('km/h', 0, 0, 200);
    expect(U.withUnit(vss, 100.0)).toBe('62 mph');
    near(U.spec(vss).max, 124.27, 0.01);
    const kpa = spec('kPa', 0, 0, 110);
    expect(U.spec(kpa)).toBe(kpa);
    expect(U.withUnit(kpa, 32.0)).toBe('32 kPa');
    for (const s of [spec('V', 1, 8, 16), spec('V', 2, 0, 1), spec('rpm', 0, 0, 8000),
      spec('', 0, 0, 1, { kind: 'flag' }), spec('AFR', 1, 10, 20)]) expect(U.spec(s)).toBe(s);
    expect(U.withUnit(spec('L/h', 1, 0, 40), 10.0)).toBe('2.64 gal/h');
    expect(U.presetName()).toBe('US');
    expect(U.asDict()).toEqual(US);

    U.setUnit('pressure', 'psi');
    expect(U.withUnit(kpa, 101.0)).toBe('14.6 psi');
    expect(U.presetName()).toBe('');
    U.preset('Metric');
    expect(U.asDict()).toEqual(METRIC);
    expect(U.withUnit(ect, 88.0)).toBe('88 °C');
    expect(U.withUnit(kpa, 101.0)).toBe('101 kPa');
  });

  it('free text from alarm rules is converted', () => {
    U.preset('US');
    expect(U.convertText('Coolant 110 °C')).toBe('Coolant 230 °F');
    expect(U.convertText('Coolant 104.5 °C rising 2.0 °C/s')).toBe('Coolant 220.1 °F rising 3.6 °F/s');
    expect(U.convertText('Speed 100 km/h at 3000 rpm, 13.8 V, MAP 32 kPa')).toBe('Speed 62 mph at 3000 rpm, 13.8 V, MAP 32 kPa');
    expect(U.convertText('IAT −5 °C')).toBe('IAT 23 °F');
    expect(U.convertText('RPM 800 falling 900 rpm/s')).toBe('RPM 800 falling 900 rpm/s');
  });

  it('starts in US units and remembers a change', () => {
    const store = new Map<string, string>();
    const settings = { get: (k: string) => store.get(k), set: (k: string, v: string) => void store.set(k, v) };
    let changes = 0;
    U.onChange(() => changes++);
    U.load(settings);
    expect(U.presetName()).toBe('US');
    expect(changes).toBe(1);
    U.setUnit('temperature', '°C');
    U.save(settings);
    expect(store.get('units/temperature')).toBe('°C');
    expect(store.get('units/speed')).toBe('mph');
    const again = new DisplayUnits();
    again.load(settings);
    expect(again.asDict()).toEqual({ ...US, temperature: '°C' });
    store.set('units/speed', 'furlongs');
    store.set('units/temperature', 'kPa');
    again.load(settings);
    expect(again.asDict()).toEqual(US);
    const rev = again.revision;
    again.preset('US');
    expect(again.revision).toBe(rev);          // no change, no bump
  });

  it('formats like the Python tiles', () => {
    U.preset('US');
    const trim = spec('%', 1, -25, 25, { signed: true });
    expect(U.text(trim, 2.25)).toBe('+2.2');
    expect(U.text(trim, -3.0)).toBe('−3.0');
    expect(U.text(trim, -0.01)).toBe('0.0');
    expect(U.text(trim, null)).toBe('---');
    expect(U.text(spec('', 0, 0, 1, { kind: 'flag' }), 1)).toBe('ON');
    expect(U.limit(spec('°C', 0, -40, 140), -10)).toBe('14 °F');
    expect(U.limit(spec('°C', 0, -40, 140), -30)).toBe('−22 °F');
  });
});
