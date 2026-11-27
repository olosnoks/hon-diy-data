import { describe, expect, it } from 'vitest';
import { DemoMemLink, FULL_BLOCKS, MemoryScanner } from '../model/scanner';
import { analyse, renderReport, type Markers, type Sweep } from './analyse';
import { autoUi, Recorder, runSteps, withLinePaused } from './runner';
import { fisherP, mannWhitneyP } from './stats';
import {
  applicable, checkStep, DEFAULT_STEPS, durationS, fastBlocks, parseSteps, promptFor, scaled, selectSteps, StepError, totals,
} from './steps';

function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

describe('poke test steps (tests/test_poke_test.py)', () => {
  it('default steps are valid and cover the flag tests', () => {
    const names = DEFAULT_STEPS.map((s) => s.name);
    expect(new Set(names).size).toBe(names.length);
    for (const s of DEFAULT_STEPS) checkStep({ ...s }, 'default');
    const by = Object.fromEntries(DEFAULT_STEPS.map((s) => [s.name, s]));
    expect(by.baseline!.baseline).toBe(true);
    expect(by.steering!.repeat).toBe(6);
    expect(by.steering!.prompts).toHaveLength(2);
    expect(by.elec_load!.fast).toBe('0x00-0x1F');
    expect(by.keyon!.engine).toBe('off');
    expect(by.keyon!.mark).toBeTruthy();
    expect(by.engine_stop!.optional && by.engine_stop!.trans === 'manual').toBe(true);
    for (const s of DEFAULT_STEPS) if (s.engine === 'off') expect(s.undo).toContain('START');
  });

  it('parse replaces the list and inherits defaults by name', () => {
    const steps = parseSteps('[[step]]\nname = "ac"\nhold_s = 30\n\n[[step]]\nname = "horn"\nprompt = "Sound the horn."\nhold_s = 3\nundo = "Stop."\n');
    expect(steps.map((s) => s.name)).toEqual(['ac', 'horn']);
    expect(steps[0]!.hold_s).toBe(30);
    expect(steps[0]!.settle_s).toBe(5);
    expect(steps[0]!.sim).toBe('ac_on');
    expect(steps[1]!.rest_s).toBe(10);
  });

  it('include_defaults overrides in place', () => {
    const steps = parseSteps('include_defaults = true\n[[step]]\nname = "brake"\nrepeat = 4\n[[step]]\nname = "wipers"\nprompt = "Wipers on."\n');
    expect(steps.slice(0, DEFAULT_STEPS.length).map((s) => s.name)).toEqual(DEFAULT_STEPS.map((s) => s.name));
    expect(steps[steps.length - 1]!.name).toBe('wipers');
    expect(steps.find((s) => s.name === 'brake')!.repeat).toBe(4);
  });

  it.each([
    ['[[step]]\nname = "x"\n', /prompt is required/],
    ['[[step]]\nname = "x"\nprompt = "p"\nhold_s = -1\n', /hold_s/],
    ['[[step]]\nname = "x"\nprompt = "p"\nhold_s = 0\n', /hold_s must be > 0/],
    ['[[step]]\nname = "x"\nprompt = "p"\nfoo = 1\n', /unknown key/],
    ['[[step]]\nname = "x"\nprompt = "p"\nengine = "off"\n', /needs an undo/],
    ['[[step]]\nname = "x"\nprompt = "p"\nrepeat = 0\n', /repeat/],
    ['[[step]]\nname = "x"\nprompt = "p"\nsim = "explode"\n', /sim must be/],
    ['[[step]]\nname = "x"\nprompt = "p"\nfast = "all"\n', /fast must look like/],
    ['[[step]]\nname = "x"\nprompt = "p"\ntrans = "cvt"\n', /trans/],
    ['[[step]]\nname = "x"\nprompt = "p"\nprompts = []\nundos = [1]\n', /undos/],
    ['[[step]]\nname = "x y"\nprompt = "p"\n', /name must be/],
    ['[[step]]\nname = "x"\nprompt = "p"\n[[step]]\nname = "x"\nprompt = "q"\n', /used twice/],
    ['steps = 1\n', /unknown top-level/],
    ['[[step]\n', /not valid TOML/],
    ['', /no \[\[step/],
  ])('rejects %j', (text, msg) => {
    expect(() => parseSteps(text)).toThrow(StepError);
    expect(() => parseSteps(text)).toThrow(msg);
  });

  it('selects, filters by gearbox and times', () => {
    expect(selectSteps(DEFAULT_STEPS, 'baseline, brake').map((s) => s.name)).toEqual(['baseline', 'brake']);
    expect(() => selectSteps(DEFAULT_STEPS, 'nope')).toThrow(/unknown step/);
    const auto = applicable(DEFAULT_STEPS, 'auto').map((s) => s.name);
    expect(auto).toContain('gear_auto');
    expect(auto).not.toContain('gear_manual');
    expect(auto).not.toContain('engine_stop');
    const brake = DEFAULT_STEPS.find((s) => s.name === 'brake')!;
    expect(durationS(brake)).toBe(40);
    expect(scaled(brake, 0.5).hold_s).toBe(5);
    const st = DEFAULT_STEPS.find((s) => s.name === 'steering')!;
    expect(promptFor(st, 1)).toContain('LEFT');
    expect(promptFor(st, 2)).toContain('RIGHT');
    expect(promptFor(st, 5)).toContain('LEFT');
    expect(totals(applicable(DEFAULT_STEPS, 'manual')).required).toBeGreaterThan(0);
  });

  it('fast blocks', () => {
    expect(fastBlocks('0x00-0x1F')).toEqual([[0x00, 16], [0x10, 16]]);
    expect(fastBlocks('0x08-0x0F')).toEqual([[0x08, 8]]);
    expect(fastBlocks('')).toBe(FULL_BLOCKS);
  });

  it('Fisher and Mann-Whitney', () => {
    expect(fisherP(5, 10, 5, 10)).toBeCloseTo(1, 9);
    expect(fisherP(10, 10, 0, 10)).toBeLessThan(1e-4);
    expect(fisherP(3, 3, 0, 3)).toBeCloseTo(0.1, 9);
    expect(mannWhitneyP([1, 2, 3], [1, 2, 3])).toBeGreaterThan(0.5);
    expect(mannWhitneyP(Array.from({ length: 20 }, (_, i) => i + 20), Array.from({ length: 20 }, (_, i) => i))).toBeLessThan(1e-5);
    expect(mannWhitneyP(Array(10).fill(5), Array(10).fill(5))).toBe(1);
  });
});

function synthRun(seed = 3): { sweeps: Sweep[]; markers: Markers } {
  const r = rng(seed);
  const pick = <T,>(xs: T[]) => xs[Math.floor(r() * xs.length)]!;
  const steps = [
    { name: 'baseline', prompt: 'rest', baseline: true, engine: 'running' },
    { name: 'lamp', prompt: 'lamp on', engine: 'running', repeat: 3 },
    { name: 'rev', prompt: 'rev it', engine: 'running', repeat: 3 },
  ];
  const phases: Markers['phases'] = [];
  const sweeps: Sweep[] = [];
  let t = 0;
  const state = { lamp: false, rev: false };
  const emit = (n: number, step: string, rep: number, phase: string) => {
    const t0 = t;
    for (let i = 0; i < n; i++) {
      const vals: number[] = Array.from({ length: 256 }, () => pick([7, 8]));
      vals[0x20] = Math.floor(r() * 256);
      const rpm = state.rev ? 3000 : 800 + Math.floor(r() * 41) - 20;
      vals[0] = Math.floor((rpm * 4) / 256); vals[1] = (rpm * 4) % 256;
      vals[0x9c] = Math.min(255, Math.floor(rpm / 20) + Math.floor(r() * 3) - 1);
      vals[0x5d] = (vals[0x5d]! & ~(1 << 2)) | (state.lamp ? 1 << 2 : 0);
      vals[0x6a] = 100 + (state.lamp ? 20 : 0) + Math.floor(r() * 5) - 2;
      sweeps.push({ t0: t + 0.01, t1: t + 0.6, vals });
      t += 1;
    }
    phases.push({ step, rep, phase, t0, t1: t });
  };
  emit(20, 'baseline', 1, 'hold');
  for (const rep of [1, 2, 3]) {
    emit(1, 'lamp', rep, 'wait'); state.lamp = true; emit(2, 'lamp', rep, 'settle'); emit(8, 'lamp', rep, 'hold');
    state.lamp = false; emit(1, 'lamp', rep, 'undo_wait'); emit(2, 'lamp', rep, 'undo_settle'); emit(8, 'lamp', rep, 'rest');
  }
  for (const rep of [1, 2, 3]) {
    emit(1, 'rev', rep, 'wait'); state.rev = true; emit(6, 'rev', rep, 'hold'); state.rev = false; emit(8, 'rev', rep, 'rest');
  }
  return { sweeps, markers: { steps, phases, events: [] } };
}

describe('poke test analysis', () => {
  it('ranks the planted bit first and keeps noise quiet', () => {
    const { sweeps, markers } = synthRun();
    const res = Object.fromEntries(analyse(sweeps, markers).map((r) => [r.name, r]));
    const lamp = res.lamp!;
    expect(lamp.nHold).toBe(24);
    expect(lamp.nBase).toBeGreaterThanOrEqual(15);
    const direct = lamp.findings.filter((f) => !f.indirect);
    expect([direct[0]!.addr, direct[0]!.bit]).toEqual([0x5d, 2]);
    expect(direct[0]!.effect).toBe('+100%');
    expect(lamp.findings.some((f) => f.addr === 0x6a && f.bit === null)).toBe(true);
    expect(lamp.findings.some((f) => f.addr === 0x20)).toBe(false);
    expect(lamp.findings.every((f) => [0x5d, 0x6a].includes(f.addr))).toBe(true);
  });

  it('flags rpm-driven bytes as indirect', () => {
    const { sweeps, markers } = synthRun();
    const results = analyse(sweeps, markers);
    const rev = results.find((r) => r.name === 'rev')!;
    expect(rev.driverMoved.rpm).toBe(true);
    const by = new Map(rev.findings.map((f) => [`${f.addr}/${f.bit}`, f]));
    expect(by.get('0/null')!.indirect).toContain('is rpm');
    expect(by.get(`${0x9c}/null`)!.indirect).toContain('rpm');
    expect(rev.findings.some((f) => f.addr === 0x5d)).toBe(false);
    expect(results.find((r) => r.name === 'lamp')!.findings.some((f) => f.indirect)).toBe(false);
  });

  it('renders the report', () => {
    const { sweeps, markers } = synthRun();
    const text = renderReport(analyse(sweeps, markers), { port: 'sim:' }, sweeps.length);
    expect(text).toContain('## lamp');
    expect(text).toContain('0x5D b2');
    expect(text).toContain('## Per address');
    expect(text).toContain('Flag bytes 0x08-0x0F');
    expect(text).toContain('0x5D b2: lamp');
  });

  it('a brief blink at the switch is an edge', () => {
    const r = rng(1);
    const phases: Markers['phases'] = [];
    const sweeps: Sweep[] = [];
    let t = 0;
    const emit = (n: number, step: string, rep: number, phase: string, blink = false) => {
      const t0 = t;
      for (let i = 0; i < n; i++) {
        const vals = Array<number>(256).fill(7);
        vals[0] = Math.floor(3200 / 256); vals[1] = 3200 % 256;
        vals[0x0c] = blink && i < 2 ? 0 : 1;
        vals[0x30] = Math.floor(r() * 5);
        sweeps.push({ t0: t + 0.001, t1: t + 0.06, vals });
        t += 0.07;
      }
      phases.push({ step, rep, phase, t0, t1: t });
    };
    emit(200, 'load', 0, 'rest');
    for (let rep = 1; rep <= 5; rep++) {
      emit(10, 'load', rep, 'wait', true); emit(140, 'load', rep, 'hold'); emit(10, 'load', rep, 'undo_wait'); emit(280, 'load', rep, 'rest');
    }
    const res = analyse(sweeps, { steps: [{ name: 'load', prompt: 'all on', engine: 'running', repeat: 5, fast: '0x00-0x1F', setup: 'all off' }], phases })[0]!;
    expect(res.findings.some((f) => f.addr === 0x0c)).toBe(false);
    const edge = res.edges.filter((f) => f.addr === 0x0c);
    expect(edge[0]!.bit).toBe(0);
    expect(edge[0]!.kind).toBe('edge');
    expect(res.bitfrac['0x0C.0']).toEqual([1, 1]);
  });

  it('builds the engine-off timeline and reads owner marks', () => {
    const phases: Markers['phases'] = [];
    const sweeps: Sweep[] = [];
    let t = 0;
    const emit = (n: number, phase: string, fn: (i: number) => (number | null)[]) => {
      const t0 = t;
      for (let i = 0; i < n; i++) { sweeps.push({ t0: t + 0.01, t1: t + 0.6, vals: fn(i) }); t += 1; }
      phases.push({ step: 'keyon', rep: phase !== 'rest0' ? 1 : 0, phase: phase.startsWith('rest') ? 'rest' : phase, t0, t1: t });
    };
    const running = () => { const v = Array<number>(256).fill(0); v[0] = 12; v[1] = 128; v[0x0b] = 0x41; return v; };
    const silent = () => Array<null>(256).fill(null);
    const keyon = (i: number) => { const v = Array<number>(256).fill(0); v[0x0b] = (i < 2 ? 1 : 0) | (i < 5 ? 0x20 : 0); return v; };
    emit(20, 'rest0', running); emit(1, 'wait_pre', running); emit(5, 'pre', silent); emit(1, 'wait', silent);
    emit(30, 'hold', keyon); emit(2, 'undo_wait', keyon); emit(10, 'undo_settle', running); emit(20, 'rest', running);
    const holdT0 = phases.find((p) => p.phase === 'hold')!.t0;
    const res = analyse(sweeps, {
      steps: [{ name: 'keyon', prompt: 'key on', engine: 'off', repeat: 1, pre: 'key off', undo: 'start' }],
      phases, events: [{ step: 'keyon', rep: 1, label: 'MIL out', t: holdT0 + 5 }],
    })[0]!;
    const text = res.timeline.join('\n');
    expect(text).toContain('ECU silent');
    expect(text).toContain('0x0B b0 1->0');
    expect(text).toContain('0x0B b5 1->0');
    expect(res.marks[0]).toContain('0x0B b5 1->0');
    expect(res.marks[0]).toContain('+5.0 s');
    expect(res.findings.some((f) => f.addr === 0x0b && f.bit === 6)).toBe(true);
  });
});

describe('poke test run', () => {
  it('runs end to end on the simulator, pausing the live session and giving it back', async () => {
    let now = 1000;
    const clock = () => now;
    const link = new DemoMemLink(clock);
    const scanner = new MemoryScanner(link, clock);
    const rec = new Recorder(clock);
    scanner.onSweep((mem, times) => rec.addSweep(mem, times));
    const live = { paused: 0, resumed: 0, pause: async () => { live.paused++; }, resume: async () => { live.resumed++; } };
    const steps = selectSteps(DEFAULT_STEPS, 'baseline,ac,brake').map((s) => scaled(s, 0.5));
    const ui = autoUi(async (secs) => {
      for (let k = 0; k < Math.round(secs * 2); k++) { now += 0.5; await scanner.sweepOnce(); }
    });
    const result = await withLinePaused(live, () => runSteps(steps, rec, ui, { sim: (a) => link.act(a), setFast: (b) => scanner.setBlocks(b) }));
    expect(result).toBe('ok');
    expect([live.paused, live.resumed]).toEqual([1, 1]);
    const results = analyse(rec.sweeps, rec.markers());
    const ac = results.find((r) => r.name === 'ac')!;
    expect(ac.findings.some((f) => f.addr === 0x08 && f.bit === 1 && !f.indirect)).toBe(true);
    const brake = results.find((r) => r.name === 'brake')!;
    expect(brake.findings.some((f) => f.addr === 0x08 && f.bit === 3)).toBe(true);

    // an error inside still gives the line back
    await expect(withLinePaused(live, async () => { throw new Error('boom'); })).rejects.toThrow('boom');
    expect(live.resumed).toBe(2);
  });
});
