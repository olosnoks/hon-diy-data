// @vitest-environment node
/** The trigger engine and the stall presets (port of tests/test_triggers.py). */
import { describe, expect, it } from 'vitest';
import { ExprError, compileCondition } from './condition';
import { type StallOptions, MANUAL, defaultRules, extraRules, loadRules, mergeRules, stallRules } from './presets';
import { TriggerEngine, ruleFromDict, ruleToDict, scan, triggerRule } from './engine';

const engine = (o: StallOptions = {}) => new TriggerEngine(stallRules(o));

/** One sweep, the way the recorder presents it: values unknown when the link is down. */
function feed(eng: TriggerEngine, t: number, rpm: number | null, link = true) {
  const [fired, done] = eng.feed(t, link ? { rpm, link: 1 } : { rpm: null, link: 0 });
  return [fired.length ? fired[0]!.reason : null, done] as const;
}

function feedV(eng: TriggerEngine, t: number, rpm: number | null, vss: number | null, link = true) {
  const [fired, done] = eng.feed(t, link ? { rpm, vss, link: 1 } : { rpm: null, vss: null, link: 0 });
  return [fired.length ? fired[0]!.reason : null, done] as const;
}

const fail = (msg: string) => () => { throw new Error(msg); };

describe('the stall catcher from 0.1.0, on the engine', () => {
  it('windows', () => {
    const eng = engine({ preS: 30, postS: 10 });
    let t = 0;
    while (t < 50) {
      expect(feed(eng, t, 800)).toEqual([null, null]);
      t += 0.1;
    }
    const [reason] = feed(eng, t, 0);
    expect(reason).toBe('rpm');
    expect(eng.capturing).toBe(true);
    const stallT = t;
    let done = null;
    while (done === null) {
      t += 0.1;
      done = feed(eng, t, 0)[1];
    }
    expect(done.complete && done.t === stallT && done.first.rule.label === 'STALL (rpm 0)').toBe(true);
    expect(done.tStart).toBeCloseTo(stallT - 30, 9);
    expect(Math.abs(done.tEnd - stallT - 10)).toBeLessThan(0.11);
    expect(done.tEnd - stallT).toBeGreaterThanOrEqual(10);
    expect(eng.capturing).toBe(false);
  });

  it('needs a running engine first', () => {
    const eng = engine();
    for (let i = 0; i < 50; i++) expect(feed(eng, i * 0.1, 0)[0]).toBeNull();     // key on, engine off
    for (let i = 50; i < 80; i++) expect(feed(eng, i * 0.1, 250)[0]).toBeNull();  // cranking does not arm it
    expect(feed(eng, 8.0, 0)[0]).toBeNull();
    expect(eng.armed('stall')).toBe(false);
  });

  it('link loss', () => {
    const eng = engine({ postS: 1, linkDownS: 1.0 });
    for (let i = 0; i < 20; i++) feed(eng, i * 0.1, 900);
    expect(feed(eng, 2.0, null, false)[0]).toBeNull();      // one bad sweep is not a stall
    expect(feed(eng, 2.1, 900)[0]).toBeNull();
    expect(feed(eng, 2.2, null, false)[0]).toBeNull();
    expect(feed(eng, 2.8, null, false)[0]).toBeNull();
    expect(feed(eng, 3.3, null, false)[0]).toBe('link');
    const done = eng.finish()!;                              // recording stopped early
    expect(done.first.reason).toBe('link');
    expect(done.complete).toBe(false);
    expect(done.first.rule.message).toContain('link lost');
    expect(eng.finish()).toBeNull();
  });

  it('re-arms', () => {
    const eng = engine({ postS: 0.2 });
    feed(eng, 0, 800);
    expect(feed(eng, 1, 0)[0]).toBe('rpm');
    expect(feed(eng, 1.3, 0)[1]).not.toBeNull();
    expect(feed(eng, 2, 0)[0]).toBeNull();                   // still dead: no second capture
    feed(eng, 3, 900);
    expect(feed(eng, 4, 0)[0]).toBe('rpm');
    expect(eng.firedCount('stall')).toBe(2);
  });

  it('key off after a stall is not a second capture', () => {
    const eng = engine({ postS: 0.5 });
    feed(eng, 0, 800);
    expect(feed(eng, 1, 0)[0]).toBe('rpm');
    const seen = Array.from({ length: 29 }, (_, k) => feed(eng, 1 + (k + 1) * 0.2, null, false)[0]);
    expect(seen).toEqual(Array(29).fill(null));
    expect(eng.armed('link_loss')).toBe(false);
    feed(eng, 10, 900, true);
    expect(eng.armed('link_loss') && eng.armed('stall')).toBe(true);
  });

  it('a silent ECU cannot look like rpm zero', () => {
    const eng = engine({ linkDownS: 5 });
    feed(eng, 0, 800);
    expect(feed(eng, 0.1, null, false)[0]).toBeNull();       // rpm unknown, not "below 50"
    expect(feed(eng, 0.2, null, true)[0]).toBeNull();
  });
});

describe('the general engine', () => {
  it('hold and rearm condition', () => {
    const rule = triggerRule({ name: 'overtemp', when: 'ect > 105', rearmWhen: 'ect < 100', holdS: 2, postS: 1, preS: 5 });
    const eng = new TriggerEngine([rule]);
    expect([rule.label, rule.kind, rule.reason]).toEqual(['OVERTEMP', 'overtemp', 'overtemp']);
    expect(eng.feed(0, { ect: 106 })[0]).toEqual([]);
    expect(eng.feed(1.5, { ect: 104 })[0]).toEqual([]);     // dipped: the hold starts again
    expect(eng.feed(2.0, { ect: 107 })[0]).toEqual([]);
    expect(eng.feed(3.9, { ect: 107 })[0]).toEqual([]);
    const [fired] = eng.feed(4.0, { ect: 108 });
    expect(fired.map((f) => f.rule.name)).toEqual(['overtemp']);
    expect(fired[0]!.values.ect).toBe(108);
    expect(eng.feed(4.5, { ect: 103 })[0]).toEqual([]);
    expect(eng.armed('overtemp')).toBe(false);               // not below 100 yet
    expect(eng.feed(9.0, { ect: 107 })[0]).toEqual([]);
    eng.feed(10, { ect: 99 });
    expect(eng.armed('overtemp')).toBe(true);
    eng.feed(11, { ect: 106 });
    expect(eng.feed(13, { ect: 106 })[0]).toHaveLength(1);
  });

  it('the default rearm is the condition going false', () => {
    const eng = new TriggerEngine([triggerRule({ name: 'low', when: 'batt < 11', postS: 0, preS: 0 })]);
    expect(eng.feed(0, { batt: 10 })[0]).toHaveLength(1);
    expect(eng.feed(1, { batt: 10 })[0]).toEqual([]);
    eng.feed(2, { batt: 12 });
    expect(eng.feed(3, { batt: 10.5 })[0]).toHaveLength(1);
  });

  it('partial updates keep the latest values', () => {
    const eng = new TriggerEngine([triggerRule({ name: 'lean', when: 'o2 < 0.1 and tps > 50', postS: 0 })]);
    eng.feed(0.0, { tps: 80 });
    expect(eng.feed(0.02, { o2: 0.5 })[0]).toEqual([]);
    expect(eng.feed(0.04, { o2: 0.05 })[0]).toHaveLength(1);  // tps came from an earlier read
    expect(eng.values).toEqual({ tps: 80, o2: 0.05 });
  });

  it('overlapping triggers make one bundle', () => {
    const a = triggerRule({ name: 'a', when: 'x > 1', preS: 5, postS: 2 });
    const b = triggerRule({ name: 'b', when: 'y > 1', preS: 20, postS: 10 });
    const quiet = triggerRule({ name: 'quiet', when: 'z > 1', actions: ['mark'] });
    const eng = new TriggerEngine([a, b, quiet], 11);
    const seen: string[] = [];
    const captures: unknown[] = [];
    eng.onFired = (f) => seen.push(f.rule.name);
    eng.onCapture = (c) => captures.push(c);
    eng.feed(100, { x: 2 });
    eng.feed(101, { z: 2 });                                 // marks only: no window change
    expect(eng.capture!.tEnd).toBe(102);
    eng.feed(101.5, { y: 2 });
    expect(eng.capture!.tStart).toBe(81.5);
    expect(eng.capture!.tEnd).toBe(111);                     // capped at maxCaptureS
    expect(eng.feed(110.9, {})[1]).toBeNull();
    const [, done] = eng.feed(111.2, {});
    expect(done!.fired.map((f) => f.rule.name)).toEqual(['a', 'b']);
    expect(done!.t).toBe(100);
    expect(done!.tEnd).toBe(111.2);
    expect(seen).toEqual(['a', 'quiet', 'b']);
    expect(captures).toEqual([done]);
  });

  it('manual fire, enable, remove', () => {
    const eng = new TriggerEngine(defaultRules());
    expect(eng.rules.map((r) => r.name)).toEqual(['stall', 'link_loss', 'low_battery', 'lean_spike', 'overtemp']);
    expect(eng.rules.map((r) => r.enabled)).toEqual([true, true, false, false, false]);
    expect(eng.feed(0, { batt: 9, ect: 120 })[0]).toEqual([]);   // disabled rules stay quiet
    eng.enable('low_battery');
    eng.feed(1, { batt: 9 });
    expect(eng.feed(1.6, { batt: 9 })[0]).toHaveLength(1);
    eng.finish();
    const event = eng.fire(MANUAL, 5.0);
    expect(event.rule.kind).toBe('capture');
    expect(eng.capture!.tStart).toBe(-25);
    expect(eng.capture!.tEnd).toBe(15);
    eng.finish();
    expect(eng.fire('overtemp', 6.0).rule.name).toBe('overtemp');      // by name, even if disabled
    eng.remove('overtemp');
    eng.remove('overtemp');
    expect(eng.rules.map((r) => r.name)).not.toContain('overtemp');
    eng.add(triggerRule({ name: 'stall', when: 'rpm < 10' }));          // replaces by name
    expect(eng.rules.filter((r) => r.name === 'stall').map((r) => r.when)).toEqual(['rpm < 10']);
  });

  it('rule validation and dicts', () => {
    const bad = [
      { name: '', when: 'x' }, { name: 'a', when: '' }, { name: 'a', when: 'x >' }, { name: 'a', when: 'x', armWhen: 'import os' },
      { name: 'a', when: 'x', actions: ['explode'] }, { name: 'a', when: 'x', holdS: -1 },
    ];
    for (const b of bad) expect(() => triggerRule(b)).toThrow();
    for (const rule of defaultRules()) expect(ruleFromDict(ruleToDict(rule))).toEqual(rule);
    expect(ruleFromDict({ name: 'n', when: 'x', bogus: 1, actions: ['mark'] }).actions).toEqual(['mark']);
    expect(extraRules().every((r) => !r.enabled)).toBe(true);
  });

  it('rules from TOML, merged with the defaults', () => {
    const rules = loadRules('[[trigger]]\nname = "stall"\nwhen = "rpm < 100"\narm_when = "rpm > 500"\n\n' +
      '[[trigger]]\nname = "cold"\nwhen = "ect < 0"\nhold_s = 3\nactions = ["mark"]\n');
    expect(rules.map((r) => r.name)).toEqual(['stall', 'cold']);
    expect(rules[1]!.holdS).toBe(3);
    const merged = mergeRules(defaultRules(), rules);
    expect(merged).toHaveLength(6);
    expect(merged[0]!.when).toBe('rpm < 100');
    expect(merged[merged.length - 1]!.name).toBe('cold');
    expect(() => loadRules('[[trigger]]\nname = "x"\n')).toThrow(/'x'/);
    expect(() => loadRules('[[trigger]]\nname = "x"\nwhen = "a b"\n')).toThrow(ExprError);
  });

  it('scans a finished log', () => {
    const t = Array.from({ length: 600 }, (_, i) => Math.round(i * 0.1 * 10) / 10);
    const rpm: number[] = t.map((x) => (x > 20 && x < 25 ? 0 : 850));
    for (let i = 400; i < 420; i++) rpm[i] = NaN;           // 2 s link gap: a dropout, not a stall
    for (let i = 450; i < 500; i++) rpm[i] = NaN;           // 5 s link gap: unknown, not zero
    const fired = scan(stallRules(), t, { rpm, link: rpm.map((v) => (v !== v ? 0 : 1)) });
    expect(fired.map((f) => [f.reason, Math.round(f.t * 10) / 10])).toEqual([['rpm', 20.1], ['link', 48.0]]);
  });
});

describe('link dropouts with the engine running (2026-11-26)', () => {
  it('a short dropout is not a stall', () => {
    const eng = engine();
    for (let i = 0; i < 100; i++) feed(eng, i * 0.1, 700);
    for (let t = 10.0; t < 11.5; t += 0.1) expect(feed(eng, t, null, false)[0]).toBeNull();
    expect(feed(eng, 11.6, 690)[0]).toBeNull();
    expect(eng.capturing).toBe(false);
  });

  it('link loss with the engine running when it answers is revoked', () => {
    const eng = engine({ postS: 10, linkDownS: 3.0 });
    const revoked: [string, number][] = [];
    eng.onRevoked = (f, t) => revoked.push([f.reason, t]);
    for (let i = 0; i < 100; i++) feed(eng, i * 0.1, 700);
    const reasons = Array.from({ length: 33 }, (_, i) => feed(eng, 10.0 + i * 0.1, null, false)[0]);
    expect(reasons.slice(-3).filter((r) => r === 'link')).toHaveLength(1);
    expect(eng.capturing && eng.reviewing()).toBe(true);
    expect(Math.abs(eng.capture!.tStart - (13.0 - 33.0))).toBeLessThan(0.11);   // 30 s before the silence
    feed(eng, 13.6, 650);                                    // back, engine still at idle
    expect(revoked).toEqual([['link', 13.6]]);
    expect(eng.reviewing()).toBe(false);
    let done = null;
    let t = 13.7;
    while (done === null) {
      done = feed(eng, t, 650)[1];
      t += 0.1;
    }
    expect(done.cancelled).toBe(true);
    expect(done.first.rule.cancelKind).toBe('dropout');
    expect(eng.armed('stall') && eng.armed('link_loss')).toBe(true);   // the stall hunt goes on
  });

  it('link loss then key on at rpm zero stands', () => {
    const eng = engine({ postS: 10, linkDownS: 3.0 });
    eng.onRevoked = fail('a dead ECU must stay a stall');
    for (let i = 0; i < 100; i++) feed(eng, i * 0.1, 800);
    for (let t = 10.0; t < 30.0; t += 0.1) feed(eng, t, null, false);
    feed(eng, 30.0, 0);                                      // first answer: key on, engine stopped
    feed(eng, 31.0, 250);
    feed(eng, 32.0, 1400);                                   // running again: too late to revoke
    expect(eng.reviewing()).toBe(false);
  });

  it('no verdict in time: it stands', () => {
    const eng = engine({ postS: 1, linkDownS: 3.0, reviewS: 5.0 });
    eng.onRevoked = fail('no verdict in time');
    for (let i = 0; i < 100; i++) feed(eng, i * 0.1, 800);
    for (let t = 10.0; t < 20.0; t += 0.1) feed(eng, t, null, false);
    feed(eng, 20.0, 800);                                    // 7 s after the firing
  });

  it('link loss while driving and revving is a dropout (the moving-car rule)', () => {
    // 2026-11-26 23:16: 8 s silent while accelerating 41 -> 59 km/h; back at 2,887 rpm against 2,066
    const eng = engine({ postS: 10, linkDownS: 3.0 });
    const revoked: number[] = [];
    eng.onRevoked = (_f, t) => revoked.push(t);
    for (let i = 0; i < 100; i++) feedV(eng, i * 0.1, 2066, 41);
    for (let t = 10.0; t < 18.0; t += 0.1) feedV(eng, t, null, null, false);
    feedV(eng, 18.1, 2887, 59);
    expect(revoked).toHaveLength(1);
    expect(revoked[0]).toBeCloseTo(18.1, 9);
    expect(eng.reviewing()).toBe(false);
  });

  it('stopped, then a flare with speed: the stall stands', () => {
    const eng = engine({ postS: 10, linkDownS: 3.0 });
    eng.onRevoked = fail('a restart flare at a standstill is a stall');
    for (let i = 0; i < 100; i++) feedV(eng, i * 0.1, 670, 0);
    for (let t = 10.0; t < 30.0; t += 0.1) feedV(eng, t, null, null, false);
    feedV(eng, 30.0, 1446, 0);
    expect(eng.reviewing()).toBe(false);
  });

  it('driving, answered at key-on: the stall stands', () => {
    const eng = engine({ postS: 10, linkDownS: 3.0 });
    eng.onRevoked = fail('rpm 0 on return is a stall');
    for (let i = 0; i < 100; i++) feedV(eng, i * 0.1, 2500, 60);
    for (let t = 10.0; t < 20.0; t += 0.1) feedV(eng, t, null, null, false);
    feedV(eng, 20.0, 0, 30);
    expect(eng.reviewing()).toBe(false);
  });

  it('answered in an after-start flare: the stall stands', () => {
    const eng = engine({ postS: 10, linkDownS: 3.0 });
    eng.onRevoked = fail('a restart flare is not a running engine');
    for (let i = 0; i < 100; i++) feed(eng, i * 0.1, 670);
    for (let t = 10.0; t < 30.0; t += 0.1) feed(eng, t, null, false);
    feed(eng, 30.0, 1446);
    expect(eng.reviewing()).toBe(false);
  });

  it('a speed channel never read (not in the map) is no value: the rpm test decides', () => {
    // revving back above 1.25x: without a speed channel this stands; with the idle test it is revoked
    const eng = engine({ postS: 10, linkDownS: 3.0 });
    eng.onRevoked = fail('no speed channel: the rpm test decides');
    for (let i = 0; i < 100; i++) feed(eng, i * 0.1, 2066);
    for (let t = 10.0; t < 18.0; t += 0.1) feed(eng, t, null, false);
    feed(eng, 18.1, 2887);
    expect(eng.reviewing()).toBe(false);
    const idle = engine({ postS: 10, linkDownS: 3.0 });
    const seen: number[] = [];
    idle.onRevoked = (_f, t) => seen.push(t);
    for (let i = 0; i < 100; i++) feed(idle, i * 0.1, 700);
    for (let t = 10.0; t < 14.0; t += 0.1) feed(idle, t, null, false);
    feed(idle, 14.1, 720);
    expect(seen).toHaveLength(1);
  });

  it('vssChannel "" leaves the moving-car test out', () => {
    expect(stallRules({ vssChannel: '' })[1]!.cancelWhen).toBe('rpm > 400 and (rpm < before_rpm * 1.25 + 100)');
    expect(stallRules()[1]!.cancelWhen).toBe('rpm > 400 and (rpm < before_rpm * 1.25 + 100 or (before_vss > 5 and vss > 5))');
    expect(stallRules()[1]!.holdS).toBe(3);
    expect(stallRules()[1]!.preS).toBe(33);
  });
});

describe('conditions with history (the expression engine)', () => {
  it('a stateful condition steps only on feeds that bring its channels', () => {
    // smooth(batt, 4): the mean of the last 4 batt readings, whatever else is fed in between
    const eng = new TriggerEngine([triggerRule({ name: 'sag', when: 'smooth(batt, 4) < 11', postS: 0, preS: 0 })]);
    for (const v of [12, 12, 12, 12]) eng.feed(0, { batt: v });
    for (let i = 0; i < 20; i++) expect(eng.feed(1 + i * 0.01, { rpm: 800 })[0]).toEqual([]);   // other reads: no steps
    expect(eng.feed(2, { batt: 10 })[0]).toEqual([]);          // (12 * 3 + 10) / 4 = 11.5
    expect(eng.feed(2.1, { batt: 10 })[0]).toEqual([]);        // 11.0: not below
    expect(eng.feed(2.2, { rpm: 900 })[0]).toEqual([]);        // keeps its answer
    expect(eng.feed(2.3, { batt: 10 })[0]).toHaveLength(1);    // 10.5
  });
});

describe('the built-in condition compiler', () => {
  it('has hondalog.expr semantics', () => {
    const c = compileCondition('rpm > 400 and (rpm < before_rpm * 1.25 + 100 or (before_vss > 5 and vss > 5))');
    expect([...c.deps].sort()).toEqual(['before_rpm', 'before_vss', 'rpm', 'vss']);
    expect(c.test({ rpm: 700, before_rpm: 700 }, 0)).toBe(true);           // short-circuit: vss never needed
    expect(c.test({ rpm: 2887, before_rpm: 2066, before_vss: NaN, vss: 59 }, 0)).toBe(false);
    expect(c.test({ rpm: 2887, before_rpm: 2066, before_vss: 41, vss: 59 }, 0)).toBe(true);
    expect(compileCondition('x / 0 > 1').test({ x: 1 }, 0)).toBe(false);
    expect(compileCondition('-2 ** 2 == -4').test({}, 0)).toBe(true);
    expect(compileCondition('1 < x < 3').test({ x: 2 }, 0)).toBe(true);
    expect(compileCondition('1 < x < 3').test({ x: 4 }, 0)).toBe(false);
    expect(compileCondition('not x').test({ x: 0 }, 0)).toBe(true);
    expect(compileCondition('bit(x, 1)').test({ x: 2 }, 0)).toBe(true);
    expect(compileCondition('7 // 2 == 3 and -7 % 3 == 2').test({}, 0)).toBe(true);
    expect(compileCondition('[rpm] > 5 if link else 0').test({ rpm: 10, link: 1 }, 0)).toBe(true);
    expect(() => compileCondition('rate(rpm) < -1500')).toThrow(ExprError);
    expect(() => compileCondition('a b')).toThrow(ExprError);
  });
});
