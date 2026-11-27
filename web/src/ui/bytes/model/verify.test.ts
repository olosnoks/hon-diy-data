import { describe, expect, it } from 'vitest';
import { parseMapText, setChannel } from './mapdoc';
import { ram, TEST_MAP } from './testram';
import { FAIL, g, PASS, PLAUSIBLE, SCRIPTS, scriptsFor, SKIP, VerificationSession } from './verify';

class Clock { t = 100; now = () => this.t; }

function session(script: string, text = TEST_MAP) {
  const clock = new Clock();
  return { s: new VerificationSession(SCRIPTS[script]!, parseMapText(text), clock.now), clock };
}

function feed(s: VerificationSession, clock: Clock, rows: Int16Array[], dt = 0.2) {
  for (const row of rows) { clock.t += dt; s.addSweep(row); }
}

function runTpsSweep(s: VerificationSession, clock: Clock, floor = 100) {
  s.start();
  feed(s, clock, Array(25).fill(ram({ tps: 0 })));
  expect(s.canAdvance).toBe(true);
  expect(s.nextPhase()!.id).toBe('sweep');
  feed(s, clock, Array.from({ length: 51 }, (_, i) => ram({ tps: (floor * i) / 50 })));
  s.nextPhase();
  feed(s, clock, Array(20).fill(ram({ tps: floor })));
  s.nextPhase();
  feed(s, clock, Array(20).fill(ram({ tps: 0 })));
  expect(s.nextPhase()).toBeNull();
  return s.finish();
}

describe('guided verification (tests/test_mapping_verify.py)', () => {
  it('a throttle sweep passes and confirm writes evidence', () => {
    let { s, clock } = session('tps_sweep');
    expect(s.phase).toBeNull();
    expect(s.start().id).toBe('released');
    expect(s.canAdvance).toBe(false);
    ({ s, clock } = session('tps_sweep'));
    const report = runTpsSweep(s, clock);
    const tps = report.verdicts.tps!;
    expect(tps.status).toBe(PASS);
    expect(tps.results.map((r) => r.status)).toEqual([PASS, PASS, PASS, PASS]);
    expect(tps.results[1]!.measured).toContain('went 0.9804 -> 99.02');
    expect(report.verdicts.rpm!.status).toBe(PLAUSIBLE);
    expect(report.sweeps).toBe(116);
    expect(report.samples).toBe(116);
    expect(report.durationS).toBeGreaterThan(20);

    const text = s.confirmEdit('tps', TEST_MAP, 'pedal by hand');
    const after = parseMapText(text);
    const ch = after.channels.find((c) => c.name === 'tps')!;
    expect(ch.status).toBe('verified');
    expect(after.hash).toBe(parseMapText(TEST_MAP).hash);
    expect(ch.evidence!.method).toBe('guided:tps_sweep');
    expect(ch.evidence!.result).toBe('pass');
    expect(ch.evidence!.summary).toContain('rises steadily');
    expect(ch.evidence!.summary).toContain('pedal by hand');

    expect(() => s.confirmEdit('rpm', TEST_MAP)).toThrow(/plausibility/);
    expect(parseMapText(s.confirmEdit('rpm', TEST_MAP, '', true)).channels[0]!.evidence!.result).toBe('plausible');
    expect(() => s.confirmEdit('ect', TEST_MAP)).toThrow(/does not check/);
  });

  it('a wrong address fails and the finder points at the right one', () => {
    const wrong = setChannel(TEST_MAP, 'tps', { addr: 0x18 });     // never moves
    const { s, clock } = session('tps_sweep', wrong);
    const report = runTpsSweep(s, clock);
    expect(report.verdicts.tps!.status).toBe(FAIL);
    expect(() => s.confirmEdit('tps', wrong)).toThrow(/failed/);
    const best = s.suggest('tps', 4);
    expect(best.slice(0, 2).map((x) => x.slice(0, 4)).sort()).toEqual(['0x14', '0xA0']);
    const suspect = parseMapText(s.rejectEdit('tps', wrong)).channels.find((c) => c.name === 'tps')!;
    expect(suspect.status).toBe('suspect');
    expect(suspect.evidence!.result).toBe('fail');
  });

  it('partial pedal travel fails', () => {
    const { s, clock } = session('tps_sweep');
    const tps = runTpsSweep(s, clock, 70).verdicts.tps!;
    expect(tps.status).toBe(FAIL);
    expect(tps.results.map((r) => r.status)).toEqual([PASS, FAIL, FAIL, PASS]);
  });

  it('pumping the pedal is not a steady rise', () => {
    const { s, clock } = session('tps_sweep');
    s.start();
    feed(s, clock, Array(25).fill(ram({ tps: 0 })));
    s.nextPhase();
    const vals = [...Array.from({ length: 20 }, (_, i) => i * 4), ...Array.from({ length: 14 }, (_, i) => 80 - i * 5),
      ...Array.from({ length: 19 }, (_, i) => 10 + i * 5)];
    feed(s, clock, vals.map((v) => ram({ tps: v })));
    const r = s.evaluate().tps!.results[1]!;
    expect(r.status).toBe(FAIL);
    expect(r.stats.reversal).toBeGreaterThan(0.5);
  });

  it('skips instead of guessing without references, and channels not in the map', () => {
    const { s, clock } = session('koeo');
    s.start();
    feed(s, clock, Array.from({ length: 50 }, (_, i) => ram({ batt: 12.4, noise: i })));
    const v = s.finish().verdicts;
    expect(v.batt!.status).toBe(PLAUSIBLE);
    expect(v.batt!.results[0]!.status).toBe(SKIP);
    expect(v.ect!.status).toBe(SKIP);           // not in this map
    expect(v.ect!.results[0]!.measured).toMatch(/no such channel/);
    expect(() => s.confirmEdit('ect', TEST_MAP)).toThrow(/not checked/);
  });

  it('formats like Python and orders scripts by strength', () => {
    expect(g(0.98039)).toBe('0.9804');
    expect(g(99.0196)).toBe('99.02');
    expect(g(12346)).toBe('1.235e+04');
    expect(scriptsFor('tps')[0]!.id).toBe('tps_sweep');
  });
});
