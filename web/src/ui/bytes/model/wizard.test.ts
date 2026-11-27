import { describe, expect, it } from 'vitest';
import { parseMapText } from './mapdoc';
import { DemoMemLink, MemoryScanner } from './scanner';
import { TEST_MAP } from './testram';
import { MapWizard, WIZARD_STEPS } from './wizard';

function setup(text = TEST_MAP) {
  let now = 0;
  const clock = () => now;
  const link = new DemoMemLink(clock);
  const scanner = new MemoryScanner(link, clock);
  scanner.sweeping = true;            // the test feeds the sweeps itself (no sweep loop)
  const wizard = new MapWizard(scanner, clock);
  const map = parseMapText(text);
  // a sweep a second; the phase changes half a second after the last sweep (not on one)
  const sweeps = (n: number) => { for (let i = 0; i < n; i++) { now += 1; wizard.addSweep(link.ram()); } now += 0.5; };
  return { wizard, map, link, sweeps, text, scanner };
}

/** Run the A/C switch check: off, on, off again. */
function runAc(s: ReturnType<typeof setup>) {
  const { wizard, map, link, sweeps } = s;
  wizard.pickTarget(map.channels.findIndex((c) => c.name === 'ac_sw'));
  expect(wizard.view(map).primary).toBe('Next');
  expect(wizard.next(map, s.text)).toBeNull();
  expect(wizard.step).toBe(1);
  let v = wizard.view(map);
  expect(v.title).toBe('Off  (1 of 3)');
  expect(v.primary).toBe('Recording…');
  expect(v.target).toBe('ac_sw (ac_sw)');
  sweeps(6);
  expect(wizard.view(map).primary).toBe('Next step');
  wizard.next(map, s.text);
  expect(wizard.step).toBe(2);
  link.act('ac_on');
  sweeps(7);
  wizard.next(map, s.text);
  link.act('ac_off');
  sweeps(6);
  v = wizard.view(map);
  expect(v.primary).toBe('Finish');
  wizard.next(map, s.text);
  expect(wizard.step).toBe(3);
}

describe('Map a channel · guided', () => {
  it('has the desktop steps and starts on the channel list', () => {
    const { wizard, map } = setup();
    const v = wizard.view(map);
    expect(v.steps).toEqual(WIZARD_STEPS);
    expect(v.steps).toEqual(['Target', 'Baseline', 'Stimulus', 'Candidates', 'Confirm']);
    expect(v.title).toBe('Pick the channel to prove');
    expect(v.targets[3]).toMatch(/^ac_sw \(ac_sw\) {2}now 0x08/);
    expect(v.canBack).toBe(false);
  });

  it('a channel that passes is marked verified with the evidence', () => {
    const s = setup();
    runAc(s);
    const v = s.wizard.view(s.map);
    expect(v.title).toBe('ac_sw: PASS');
    expect(v.primary).toBe('Mark verified');
    const text = s.wizard.next(s.map, s.text);
    expect(text).not.toBeNull();
    expect(parseMapText(text!).channels.find((c) => c.name === 'ac_sw')!.status).toBe('verified');
    expect(s.wizard.step).toBe(4);
    expect(s.wizard.view(s.map).body).toMatch(/ac_sw is marked verified/);
    s.wizard.next(s.map, text!);
    expect(s.wizard.step).toBe(0);
  });

  it('a channel at the wrong address fails and offers where it lives', () => {
    const s = setup(TEST_MAP.replace('addr = 0x08', 'addr = 0x09'));
    runAc(s);
    const v = s.wizard.view(s.map);
    expect(v.title).toBe('ac_sw: FAIL');
    expect(v.candidates.map((c) => c.addr)).toContain(0x08);
    s.wizard.pickCandidate(0x08);
    expect(s.wizard.view(s.map).primary).toBe('Use 0x08');
    const text = s.wizard.next(s.map, s.text)!;
    const ch = parseMapText(text).channels.find((c) => c.name === 'ac_sw')!;
    expect(ch.addr).toBe(0x08);
    expect(ch.formula).toBe('bit(x, 1)');
    expect(ch.status).toBe('unverified');
    expect(s.wizard.view(s.map).body).toMatch(/now reads 0x08/);
  });

  it('Back starts over; a lost link stops the check', () => {
    const s = setup();
    s.wizard.pickTarget(3);
    s.wizard.next(s.map, s.text);
    expect(s.wizard.step).toBe(1);
    s.wizard.back();
    expect(s.wizard.step).toBe(0);
    s.wizard.next(s.map, s.text);
    s.link.state.key = false;
    (s.link as unknown as { available: () => boolean }).available = () => false;
    expect(s.wizard.tick()).toBe(true);
    expect(s.wizard.step).toBe(0);
    expect(s.wizard.view(s.map).body).toMatch(/link went away/);
    expect(s.wizard.view(s.map).primary).toBe('Connect first');
  });
});
