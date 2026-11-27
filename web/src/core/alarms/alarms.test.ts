/** Alarm engine: limits, rate of change, combinations, hysteresis, latching
 *  (port of tests/test_alarms.py, plus a golden run of the shipped rules). */

import { describe, expect, it } from 'vitest';
import shipped from '../expr/__golden__/shipped.json';
import { MathChannel, MathEngine } from '../expr';
import { CRITICAL, WARN, AlarmEngine, AlarmError, AlarmEvent, AlarmRule, type AlarmRuleInit } from './index';

const NAMES = ['ect', 'batt', 'rpm', 'tps', 'o2'];
const rule = (init: AlarmRuleInit) => new AlarmRule(init);

/** Feed a list of values; returns [t, kind, name][]. */
function run(engine: AlarmEngine, channel: string, values: (number | null)[], opts: { dt?: number; t0?: number; extra?: Record<string, number> } = {}) {
  const { dt = 0.5, t0 = 0.0, extra = {} } = opts;
  const out: [number, string, string][] = [];
  values.forEach((v, i) => {
    const t = t0 + i * dt;
    for (const ev of engine.update({ [channel]: v, ...extra }, t)) out.push([t, ev.kind, ev.name]);
  });
  return out;
}

const kinds = (out: [number, string, string][]) => out.map(([, k]) => k);

describe('alarms', () => {
  it('acceptance: coolant over 105 for 2 s raises critical within one cycle', () => {
    const r = rule({ name: 'ect_high', channel: 'ect', high: 105, hold_s: 2, severity: 'critical',
      actions: ['tile', 'banner', 'beep', 'marker'] });
    const e = new AlarmEngine([r], NAMES, null, null, { ect: ['Coolant', '°C'] });
    const seen: AlarmEvent[] = [];
    e.subscribe((ev) => seen.push(ev));
    const dt = 0.05;
    let t = 0.0;
    let crossed: number | null = null;
    let raised: number | null = null;
    let events: AlarmEvent[] = [];
    while (raised === null && t < 10) {
      const ect = 100.0 + t * 4;
      if (crossed === null && ect > 105) crossed = t;
      events = e.update({ ect }, t);
      if (events.length) raised = t;
      else t += dt;
    }
    expect(raised! - crossed!).toBeGreaterThanOrEqual(2.0);
    expect(raised! - crossed!).toBeLessThan(2.0 + dt + 1e-9);
    expect(events.length).toBe(1);
    const ev = events[0]!;
    expect(ev).toBeInstanceOf(AlarmEvent);
    expect(ev.kind).toBe('raised');
    expect(ev.severity).toBe(CRITICAL);
    expect(ev.actions).toContain('banner');
    expect(ev.actions).toContain('marker');
    expect(seen).toEqual([ev]);
    expect(ev.markerText.startsWith('ALARM ect_high: ')).toBe(true);
    expect(ev.markerText).toContain('Coolant');
    expect(e.top()!.name).toBe('ect_high');
    expect(e.severityFor('ect')).toBe(CRITICAL);
    expect(e.severityFor('rpm')).toBeNull();
  });

  it('the hold time restarts if the condition breaks', () => {
    const e = new AlarmEngine([rule({ name: 'hot', channel: 'ect', high: 105, hold_s: 1 })], NAMES);
    expect(run(e, 'ect', [106, 106, 100, 106, 106, 106])).toEqual([[2.5, 'raised', 'hot']]);
  });

  it('hysteresis stops chatter at the limit', () => {
    const plain = new AlarmEngine([rule({ name: 'hot', channel: 'ect', high: 105 })], NAMES);
    const banded = new AlarmEngine([rule({ name: 'hot', channel: 'ect', high: 105, hysteresis: 3 })], NAMES);
    const wobble = [104, 106, 104.5, 106, 104.5, 106, 101, 100];
    expect(kinds(run(plain, 'ect', wobble))).toEqual(['raised', 'cleared', 'raised', 'cleared', 'raised', 'cleared']);
    expect(run(banded, 'ect', wobble)).toEqual([[0.5, 'raised', 'hot'], [3.0, 'cleared', 'hot']]);
  });

  it('low limit and both limits', () => {
    const e = new AlarmEngine([rule({ name: 'batt', channel: 'batt', low: 11.5, high: 15.2, hysteresis: 0.3 })], NAMES);
    expect(kinds(run(e, 'batt', [12.5, 11.4, 11.7, 11.9, 15.3, 15.0, 14.8]))).toEqual(['raised', 'cleared', 'raised', 'cleared']);
    expect(e.status('batt').count).toBe(2);
  });

  it('clear hold time', () => {
    const e = new AlarmEngine([rule({ name: 'hot', channel: 'ect', high: 105, clear_hold_s: 1 })], NAMES);
    expect(run(e, 'ect', [106, 100, 106, 100, 100, 100])).toEqual([[0.0, 'raised', 'hot'], [2.5, 'cleared', 'hot']]);
  });

  it('rate of change', () => {
    const e = new AlarmEngine([rule({ name: 'dying', channel: 'rpm', rate_low: -400, rate_window_ms: 400, gate: '[rpm] < 600' })], NAMES);
    expect(run(e, 'rpm', [750, 760, 745, 755, 750, 748, 752], { dt: 0.1 })).toEqual([]);
    const out = run(e, 'rpm', [750, 700, 590, 480, 380, 250, 100, 0, 0, 0, 0, 0, 0, 0], { dt: 0.1, t0: 1.0 });
    expect(kinds(out)).toEqual(['raised', 'cleared']);
    expect(Math.abs(out[0]![0] - 1.3)).toBeLessThan(1e-9);
    expect(Math.abs(e.status('dying').rate!)).toBeLessThan(1);
    const e2 = new AlarmEngine([rule({ name: 'surge', channel: 'rpm', rate_high: 2000 })], NAMES);
    expect(kinds(run(e2, 'rpm', [800, 900, 1500, 2400, 2500, 2500, 2500, 2500, 2500, 2500], { dt: 0.1 }))).toEqual(['raised', 'cleared']);
  });

  it('combination with an expression and a clear expression', () => {
    const e = new AlarmEngine([rule({ name: 'lean', when: '[o2] < 0.2 && [tps] > 60 && [rpm] > 3000', hold_s: 0.5, severity: 'critical' })], NAMES);
    const rows = [[0.1, 80, 4000], [0.1, 80, 4000], [0.1, 30, 4000], [0.1, 80, 4000], [0.1, 80, 4000], [0.8, 80, 4000]];
    const out: [number, string][] = [];
    rows.forEach(([o2, tps, rpm], i) => {
      for (const ev of e.update({ o2: o2!, tps: tps!, rpm: rpm! }, i * 0.5)) out.push([i, ev.kind]);
    });
    expect(out).toEqual([[1, 'raised'], [2, 'cleared'], [4, 'raised'], [5, 'cleared']]);
    const sticky = new AlarmEngine([rule({ name: 'hot', when: '[ect] > 105', clear_when: '[ect] < 95' })], NAMES);
    expect(run(sticky, 'ect', [106, 100, 96, 94, 100])).toEqual([[0.0, 'raised', 'hot'], [1.5, 'cleared', 'hot']]);
  });

  it('a gate suppresses and clears', () => {
    const e = new AlarmEngine([rule({ name: 'batt_low', channel: 'batt', low: 12.0, gate: '[rpm] > 400' })], NAMES);
    expect(e.update({ batt: 11.0, rpm: 0.0 }, 0)).toEqual([]);
    expect(e.update({ batt: 11.0, rpm: 800.0 }, 1).map((x) => x.kind)).toEqual(['raised']);
    expect(e.update({ batt: 11.0, rpm: 0.0 }, 2).map((x) => x.kind)).toEqual(['cleared']);
  });

  it('missing values hold the state', () => {
    const e = new AlarmEngine([rule({ name: 'hot', channel: 'ect', high: 105, hold_s: 1 }),
      rule({ name: 'combo', when: '[ect] > 105 && [rpm] > 400' })], NAMES);
    expect(run(e, 'ect', [null, null], { extra: { rpm: 800.0 } })).toEqual([]);
    expect(run(e, 'ect', [106, 106, 106, null, null, 100], { extra: { rpm: 800.0 }, t0: 5 })).toEqual([
      [5.0, 'raised', 'combo'], [6.0, 'raised', 'hot'], [7.5, 'cleared', 'hot'], [7.5, 'cleared', 'combo']]);
    e.update({ ect: 110.0, rpm: 800.0 }, 20);
    e.update({ ect: 110.0, rpm: 800.0 }, 21.5);
    expect(e.active().length).toBe(2);
    expect(e.update({}, 22)).toEqual([]);
    expect(e.update({ ect: null, rpm: null }, 23)).toEqual([]);
    expect(e.active().length).toBe(2);
  });

  it('latching and acknowledge', () => {
    const e = new AlarmEngine([rule({ name: 'hot', channel: 'ect', high: 105, latching: true })], NAMES);
    expect(run(e, 'ect', [106, 100, 100])).toEqual([[0.0, 'raised', 'hot']]);
    const st = e.status('hot');
    expect(st.state).toBe('latched');
    expect(st.active).toBe(true);
    expect(e.top()).toBe(st);
    expect(e.acknowledge('hot', 9.0).map((x) => x.kind)).toEqual(['acknowledged', 'cleared']);
    expect(st.active).toBe(false);
    expect(e.acknowledge()).toEqual([]);
    expect(kinds(run(e, 'ect', [106], { t0: 10 }))).toEqual(['raised']);
    expect(e.acknowledge().map((x) => x.kind)).toEqual(['acknowledged']);
    expect(e.status('hot').acknowledged && e.status('hot').active).toBe(true);
    expect(e.acknowledge()).toEqual([]);
    expect(run(e, 'ect', [100], { t0: 11 })).toEqual([[11.0, 'cleared', 'hot']]);
    run(e, 'ect', [106, 100], { t0: 12 });
    expect(e.status('hot').state).toBe('latched');
    expect(run(e, 'ect', [106], { t0: 13 })).toEqual([]);
    expect(e.status('hot').state).toBe('active');
  });

  it('priority ordering', () => {
    const e = new AlarmEngine([rule({ name: 'w1', channel: 'batt', low: 12, severity: WARN }),
      rule({ name: 'c1', channel: 'ect', high: 105, severity: CRITICAL }),
      rule({ name: 'w2', channel: 'rpm', high: 6800, severity: WARN, priority: 200 })], NAMES);
    expect(e.active()).toEqual([]);
    expect(e.top()).toBeNull();
    e.update({ batt: 11.0, ect: 80.0, rpm: 800.0 }, 0);
    e.update({ batt: 11.0, ect: 110.0, rpm: 7000.0 }, 1);
    expect(e.active().map((s) => s.name)).toEqual(['w2', 'c1', 'w1']);
    expect(e.top()!.name).toBe('w2');
    expect(e.rules.map((r) => r.name)).toEqual(['w1', 'c1', 'w2']);
    expect(e.length).toBe(3);
    expect([...e.inputs('c1')]).toEqual(['ect']);
    expect(e.statuses().find((s) => s.name === 'c1')!.state).toBe('active');
  });

  it('tile colour only for rules with the tile action', () => {
    const e = new AlarmEngine([rule({ name: 'a', channel: 'ect', high: 100, actions: ['banner'] }),
      rule({ name: 'b', channel: 'ect', high: 90, actions: ['tile'] })], NAMES);
    e.update({ ect: 95.0 }, 0);
    expect(e.severityFor('ect')).toBe(WARN);
    e.update({ ect: 85.0 }, 1);
    expect(e.severityFor('ect')).toBeNull();
  });

  it('messages', () => {
    const labels: Record<string, [string, string]> = { batt: ['Battery', 'V'], rpm: ['RPM', 'rpm'] };
    const e = new AlarmEngine([
      rule({ name: 'custom', channel: 'batt', low: 12, message: '{label}: {value:.1f} {units} at {rpm:.0f} rpm ({nope}) {value.real}' }),
      rule({ name: 'auto_low', channel: 'batt', low: 12 }),
      rule({ name: 'auto_rate', channel: 'rpm', rate_low: -100, label: 'Falling' }),
      rule({ name: 'expr_only', when: '[rpm] > 100', label: 'Running' }),
    ], NAMES, null, null, labels);
    e.update({ batt: 13.0, rpm: 900.0 }, 0);
    const msgs = Object.fromEntries(e.update({ batt: 11.456, rpm: 800.0 }, 0.1).map((ev) => [ev.name, ev.message]));
    expect(msgs.custom).toBe('custom: 11.5 V at 800 rpm ({nope}) {value.real}');
    expect(msgs.auto_low).toBe('auto low: Battery 11.46 V (limit 12)');
    expect(msgs.auto_rate!.startsWith('Falling: RPM 800 rpm, changing -1000')).toBe(true);
    expect(e.status('expr_only').message).toBe('Running');
  });

  it('unverified inputs are flagged', () => {
    const e = new AlarmEngine([rule({ name: 'a', channel: 'ect', high: 100 }), rule({ name: 'b', channel: 'batt', low: 12 }),
      rule({ name: 'c', channel: 'ect', high: 100, gate: '[rpm] > 400' })], NAMES, null, { ect: true, batt: false, rpm: false });
    const events = e.update({ ect: 110.0, batt: 11.0, rpm: 800.0 }, 0);
    expect(Object.fromEntries(events.map((ev) => [ev.name, ev.unverified]))).toEqual({ a: false, b: true, c: true });
  });

  it('subscribers and a broken subscriber', () => {
    const e = new AlarmEngine([rule({ name: 'hot', channel: 'ect', high: 105 })], NAMES);
    const got: AlarmEvent[] = [];
    e.subscribe(() => { throw new Error('boom'); });
    const off = e.subscribe((ev) => got.push(ev));
    e.update({ ect: 110.0 }, 0);
    expect(got.map((ev) => ev.kind)).toEqual(['raised']);
    off();
    off();
    e.update({ ect: 90.0 }, 1);
    expect(got.length).toBe(1);
  });

  it('reset and replay', () => {
    const e = new AlarmEngine([rule({ name: 'hot', channel: 'ect', high: 105, hold_s: 1, hysteresis: 2 }),
      rule({ name: 'combo', when: '[ect] > 105 && [rpm] > 400' })], NAMES);
    const t = Array.from({ length: 10 }, (_, i) => i * 0.5);
    const ect = [100, 106, 106, 106, NaN, 106, 104, 102, 100, 100.0];
    const rpm = Array(10).fill(800.0);
    e.update({ ect: 120.0, rpm: 900.0 }, 0);
    const before = e.statuses().map((s) => s.state);
    expect(e.replay(t, { ect, rpm }).map((ev) => [ev.t, ev.kind, ev.name])).toEqual([
      [0.5, 'raised', 'combo'], [1.5, 'raised', 'hot'], [3.0, 'cleared', 'combo'], [3.5, 'cleared', 'hot']]);
    expect(e.statuses().map((s) => s.state)).toEqual(before);
    e.reset();
    expect(e.active()).toEqual([]);
    expect(e.status('hot').count).toBe(0);
  });

  it.each([
    [{ name: 'bad name' }, 'bad alarm name'],
    [{ name: 'a' }, 'needs a limit'],
    [{ name: 'a', high: 5 }, "need a 'channel'"],
    [{ name: 'a', channel: 'ect', low: 5, high: 1 }, 'low must be below high'],
    [{ name: 'a', channel: 'ect', high: 5, severity: 'panic' }, 'severity'],
    [{ name: 'a', channel: 'ect', high: 5, actions: ['explode'] }, 'unknown action'],
    [{ name: 'a', channel: 'ect', high: 5, hold_s: -1 }, 'negative'],
    [{ name: 'a', channel: 'ect', high: 'hot' as unknown as number }, 'numbers'],
    [{ name: 'a', channel: 'ect', rate_high: 5, rate_window_ms: 0 }, 'rate_window_ms'],
    [{ name: 'a', channel: 'ect', high: 5, clear_when: '[ect] < 1' }, 'clear_when'],
  ] as [AlarmRuleInit, string][])('bad rule %j', (init, why) => {
    expect(() => rule(init)).toThrow(AlarmError);
    expect(() => rule(init)).toThrow(why);
  });

  it('bad engine setup', () => {
    expect(() => new AlarmEngine([rule({ name: 'a', channel: 'oil', high: 5 })], NAMES)).toThrow("unknown channel 'oil'");
    expect(() => new AlarmEngine([rule({ name: 'a', when: '[oil] > 5' })], NAMES)).toThrow(/when: unknown name 'oil'/);
    expect(() => new AlarmEngine([rule({ name: 'a', when: '1' }), rule({ name: 'a', when: '1' })], NAMES)).toThrow('duplicate');
    expect(new AlarmEngine([rule({ name: 'a', channel: 'oil', high: 5, enabled: false })], NAMES).length).toBe(0);
  });
});

describe('shipped alarm rules (golden against Python)', () => {
  type Golden = {
    constants: Record<string, number>; names: string[]; units: Record<string, string>;
    verified: Record<string, boolean>; labels: Record<string, [string, string]>;
    math: ConstructorParameters<typeof MathChannel>[0][]; alarms: AlarmRuleInit[]; all_names: string[];
    t: number[]; rows: Record<string, number | null>[];
    events: { t: number; kind: string; name: string; severity: string; priority: number; value: number | null;
      message: string; actions: string[]; channel: string; unverified: boolean }[];
    acks: { kind: string; name: string; t: number }[];
  };
  const s = shipped as unknown as Golden;

  function build() {
    const math = new MathEngine(s.math.map((m) => new MathChannel(m)), s.names, s.constants, s.units);
    const verified: Record<string, boolean> = { ...s.verified };
    for (const name of math.names) verified[name] = [...math.baseInputs(name)].every((n) => verified[n] ?? false);
    const alarms = new AlarmEngine(s.alarms.map((a) => new AlarmRule(a)), s.all_names, s.constants, verified, s.labels);
    return { math, alarms };
  }

  it('include the expected rules', () => {
    const names = build().alarms.rules.map((r) => r.name);
    for (const n of ['ect_high', 'batt_low', 'batt_high', 'over_rev']) expect(names).toContain(n);
  });

  it('raise and clear exactly as the Python engine does over a synthetic drive', () => {
    const { math, alarms } = build();
    const events = [];
    for (let i = 0; i < s.t.length; i++) {
      const row = s.rows[i]!;
      const m = math.update(row, s.t[i]!);
      for (const ev of alarms.update({ ...row, ...m }, s.t[i]!)) {
        events.push({ t: ev.t, kind: ev.kind, name: ev.name, severity: ev.severity, priority: ev.priority,
          value: ev.value, message: ev.message, actions: [...ev.actions], channel: ev.channel, unverified: ev.unverified });
      }
    }
    expect(events).toEqual(s.events);
    expect(alarms.acknowledge(null, 999.0).map((e) => ({ kind: e.kind, name: e.name, t: e.t }))).toEqual(s.acks);
  });

  it('replay gives the same events', () => {
    const { math, alarms } = build();
    const cols: Record<string, (number | null)[]> = {};
    for (const key of Object.keys(s.rows[0]!)) cols[key] = s.rows.map((r) => r[key] ?? null);
    Object.assign(cols, math.computeSeries(s.t, cols));
    expect(alarms.replay(s.t, cols).map((e) => [e.t, e.kind, e.name, e.message])).toEqual(
      s.events.map((e) => [e.t, e.kind, e.name, e.message]));
  });
});
