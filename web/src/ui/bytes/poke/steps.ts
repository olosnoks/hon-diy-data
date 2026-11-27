/**
 * The guided poke test's step list (tools/poke_test.py Step, DEFAULT_STEPS,
 * parse_steps, fast_blocks, select_steps, applicable, describe_steps).
 */

import { parse as parseToml } from 'smol-toml';
import { FULL_BLOCKS, type Block } from '../model/scanner';

export interface Step {
  name: string;
  prompt: string;
  settle_s: number;
  hold_s: number;
  undo: string;
  undo_settle_s: number;
  rest_s: number;
  repeat: number;
  engine: 'running' | 'off';
  baseline: boolean;
  custom: boolean;
  optional: boolean;
  trans: '' | 'auto' | 'manual';
  note: string;
  prompts: string[];
  undos: string[];
  setup: string;
  setup_rest_s: number;
  teardown: string;
  pre: string;
  pre_s: number;
  mark: string;
  fast: string;
  sim: string;
  sim_undo: string;
  sim_pre: string;
}

export const STEP_DEFAULTS: Omit<Step, 'name' | 'prompt'> = {
  settle_s: 3, hold_s: 15, undo: '', undo_settle_s: 3, rest_s: 10, repeat: 1, engine: 'running', baseline: false,
  custom: false, optional: false, trans: '', note: '', prompts: [], undos: [], setup: '', setup_rest_s: 0, teardown: '',
  pre: '', pre_s: 0, mark: '', fast: '', sim: '', sim_undo: '', sim_pre: '',
};

export const STEP_FIELDS = new Set(['name', 'prompt', ...Object.keys(STEP_DEFAULTS)]);
const TIME_FIELDS = ['settle_s', 'hold_s', 'undo_settle_s', 'rest_s', 'setup_rest_s', 'pre_s'] as const;
const STR_FIELDS = ['undo', 'note', 'setup', 'teardown', 'pre', 'mark', 'fast', 'trans', 'sim', 'sim_undo', 'sim_pre'];
export const SIM_ACTIONS = new Set(['', 'ac_on', 'ac_off', 'brake_on', 'brake_off', 'rev', 'rev_hold', 'rev_release',
  'key_off', 'key_on', 'stall', 'start', 'load_on', 'load_off']);
export const ENGINE_OFF_BANNER = 'ENGINE OFF STEP: the engine will be stopped';
export const FLAG_ADDRS = [0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0xa4];

export class StepError extends Error {}

const S = (name: string, prompt: string, over: Partial<Step> = {}): Step => ({ ...STEP_DEFAULTS, name, prompt, ...over });

export const DEFAULT_STEPS: Step[] = [
  S('baseline', 'Engine warm and idling, in P / neutral, parking brake on. A/C off, lights off, blower off, rear '
    + 'defogger off, wheel straight, feet off the pedals. Hands off.', { settle_s: 0, hold_s: 20, rest_s: 0, baseline: true }),
  S('ac', 'Turn the A/C ON (A/C button lit; switch the blower on if it is off).',
    { settle_s: 5, hold_s: 20, undo: 'Turn the A/C OFF (and the blower back off).', undo_settle_s: 5, rest_s: 15, sim: 'ac_on', sim_undo: 'ac_off' }),
  S('headlights', 'Turn the HEADLIGHTS ON (low beam).', { settle_s: 3, hold_s: 15, undo: 'Turn the headlights OFF.', undo_settle_s: 3, rest_s: 10 }),
  S('defogger', 'Turn the REAR DEFOGGER ON (button lit).', { settle_s: 3, hold_s: 15, undo: 'Turn the rear defogger OFF.', undo_settle_s: 3, rest_s: 10 }),
  S('blower', 'Turn the BLOWER to MAXIMUM (A/C stays OFF).', { settle_s: 3, hold_s: 15, undo: 'Turn the blower OFF.', undo_settle_s: 3, rest_s: 10 }),
  S('elec_load', 'ALL AT ONCE: HIGH BEAMS on + REAR DEFOGGER on + BLOWER max. Press Enter the moment the last one is on.', {
    settle_s: 1, hold_s: 10, undo: 'ALL OFF: high beams, defogger, blower.', undo_settle_s: 1, rest_s: 20, repeat: 5, fast: '0x00-0x1F',
    setup: 'High beams, rear defogger and blower all OFF. This step reads only 0x00-0x1F, as fast as the line allows (for the 0x0C b0 blink).',
    setup_rest_s: 20, sim: 'load_on', sim_undo: 'load_off' }),
  S('brake', 'Press the BRAKE PEDAL and HOLD it down.',
    { settle_s: 0, hold_s: 10, undo: 'Let go of the brake pedal.', undo_settle_s: 0, rest_s: 10, repeat: 2, sim: 'brake_on', sim_undo: 'brake_off' }),
  S('steering', 'Turn the steering wheel to FULL LOCK and hold it there.', {
    prompts: ['Turn the steering wheel to FULL LOCK LEFT and hold it there.', 'Turn the steering wheel to FULL LOCK RIGHT and hold it there.'],
    settle_s: 0, hold_s: 5, undo: 'Back to CENTRE, let go of the wheel.', undo_settle_s: 0, rest_s: 10, repeat: 6,
    note: 'Power steering pressure switch (0x08 b2): left, centre, right, centre, x3.' }),
  S('gear_auto', 'Shift the selector.', {
    prompts: ['Foot on the brake: shift to D.', 'Foot on the brake: shift to R.'], undos: ['Shift to N.', 'Shift to P.'],
    settle_s: 1, hold_s: 5, undo_settle_s: 1, rest_s: 8, repeat: 2, trans: 'auto',
    setup: 'Parking brake ON, foot on the BRAKE pedal, selector in P. Keep your foot on the brake for the whole step.',
    setup_rest_s: 10, teardown: 'Selector in P. You can take your foot off the brake.', note: 'Gear inputs (0x08 b4-b7, 0x0A b4).' }),
  S('gear_manual', 'Push the CLUTCH pedal all the way down and hold it.', {
    settle_s: 1, hold_s: 5, undo: 'Let the clutch pedal up.', undo_settle_s: 1, rest_s: 8, repeat: 3, trans: 'manual',
    setup: 'Parking brake ON, gearbox in NEUTRAL, feet off the pedals.', setup_rest_s: 10, note: 'Clutch switch (0x08 b4-b7, 0x0A b4).' }),
  S('blip', 'In P / neutral: when you press Enter, BLIP the throttle once to about 2000 rpm and let go.',
    { settle_s: 0, hold_s: 6, rest_s: 10, repeat: 3, sim: 'rev' }),
  S('rpm3000', 'In P / neutral, parking brake on: raise the engine to about 3000 rpm and HOLD it steady.',
    { settle_s: 3, hold_s: 10, undo: 'Let go of the throttle.', undo_settle_s: 5, rest_s: 15, sim: 'rev_hold', sim_undo: 'rev_release' }),
  S('keyon', 'Turn the key to ON (II) WITHOUT starting. Press Enter the instant it is on.', {
    pre: 'KEY OFF: turn the key to OFF (the engine stops). The ECU goes silent; that is expected. Press Enter once the key is off.',
    pre_s: 10, settle_s: 0, hold_s: 30, mark: 'Press Enter the moment the MIL (check engine light) goes out',
    undo: 'START the engine and let it idle. Press Enter once it runs.', undo_settle_s: 20, rest_s: 20, engine: 'off',
    note: 'Key-on, engine off: 0x0A b7, MIL 0x0B b5, fuel pump prime 0x0B b0.', sim_pre: 'key_off', sim: 'key_on', sim_undo: 'start' }),
  S('engine_stop', 'ENGINE STOP, KEY STAYS ON: parking brake ON, foot on the brake, 1st gear, let the clutch out slowly until the '
    + 'engine stalls. LEAVE THE KEY ON. Press Enter once it has stalled.', {
    settle_s: 2, hold_s: 15, undo: 'Clutch down, NEUTRAL, then START the engine. Press Enter once it runs.', undo_settle_s: 20, rest_s: 20,
    engine: 'off', optional: true, trans: 'manual',
    note: '0x0B b6 with the key on. Manual gearbox only: an automatic cannot be stalled safely, so this step is skipped there.',
    sim: 'stall', sim_undo: 'start' }),
  S('scs', 'Turn the key to ON (II) WITHOUT starting. Press Enter as soon as it is on.', {
    pre: 'KEY OFF (the engine stops). Fit the SCS service-check jumper in the 2-pin service check connector. Press Enter when it is in.',
    pre_s: 3, settle_s: 2, hold_s: 10, undo: 'Key OFF, REMOVE the jumper, then START the engine. Press Enter once it runs.',
    undo_settle_s: 20, rest_s: 20, engine: 'off', optional: true, note: 'SCS (0x09 b3). Only if you have the service-check jumper.',
    sim_pre: 'key_off', sim: 'key_on', sim_undo: 'start' }),
  S('custom', '(asked when the step comes up)', { settle_s: 3, hold_s: 15, undo: 'Undo it.', undo_settle_s: 3, rest_s: 10, custom: true, optional: true }),
];

export const promptFor = (s: Step, rep: number) => (s.prompts.length ? s.prompts[(rep - 1) % s.prompts.length]! : s.prompt);
export const undoFor = (s: Step, rep: number) => (s.undos.length ? s.undos[(rep - 1) % s.undos.length]! : s.undo);
export const hasUndo = (s: Step) => !!(s.undo || s.undos.length);

export function durationS(s: Step): number {
  let one = s.pre_s + s.settle_s + s.hold_s + s.rest_s;
  if (hasUndo(s)) one += s.undo_settle_s;
  return s.setup_rest_s + one * s.repeat;
}

export function promptCount(s: Step): number {
  const per = 1 + (s.pre ? 1 : 0) + (hasUndo(s) ? 1 : 0);
  return per * s.repeat + (s.setup ? 1 : 0) + (s.teardown ? 1 : 0);
}

export function scaled(s: Step, k: number): Step {
  const out = { ...s };
  for (const f of TIME_FIELDS) out[f] = s[f] * k;
  return out;
}

const NAME_RE = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;
const FAST_RE = /^\s*0x([0-9A-Fa-f]{1,2})\s*-\s*0x([0-9A-Fa-f]{1,2})\s*$/;

/** '0x00-0x1F' -> [[0x00, 16], [0x10, 16]]; '' -> the full sweep. */
export function fastBlocks(spec: string, maxLen = 16): Block[] {
  if (!spec) return FULL_BLOCKS;
  const m = FAST_RE.exec(spec);
  if (!m) throw new StepError(`fast must look like "0x00-0x1F" (got ${JSON.stringify(spec)})`);
  const lo = parseInt(m[1]!, 16), hi = parseInt(m[2]!, 16);
  if (hi < lo) throw new StepError(`fast range ${JSON.stringify(spec)} runs backwards`);
  const out: Block[] = [];
  for (let a = lo; a <= hi;) { const n = Math.min(maxLen, hi - a + 1); out.push([a, n]); a += n; }
  return out;
}

export function checkStep(d: Record<string, unknown>, where: string): Step {
  const unknown = Object.keys(d).filter((k) => !STEP_FIELDS.has(k)).sort();
  if (unknown.length) throw new StepError(`${where}: unknown key(s) ${unknown.join(', ')}`);
  const name = d.name;
  if (typeof name !== 'string' || !NAME_RE.test(name)) throw new StepError(`${where}: name must be letters, digits, _ or - (got ${JSON.stringify(name)})`);
  where = `${where} (${name})`;
  if (typeof d.prompt !== 'string' || !d.prompt.trim()) throw new StepError(`${where}: prompt is required`);
  for (const k of TIME_FIELDS) {
    if (!(k in d)) continue;
    const v = d[k];
    if (typeof v !== 'number' || v < 0 || v > 600) throw new StepError(`${where}: ${k} must be a number of seconds 0-600`);
  }
  if (((d.hold_s as number | undefined) ?? 1) <= 0) throw new StepError(`${where}: hold_s must be > 0`);
  const rep = d.repeat ?? 1;
  if (typeof rep !== 'number' || !Number.isInteger(rep) || rep < 1 || rep > 20) throw new StepError(`${where}: repeat must be an integer 1-20`);
  if (!['running', 'off'].includes((d.engine as string) ?? 'running')) throw new StepError(`${where}: engine must be "running" or "off"`);
  for (const k of STR_FIELDS) if (typeof (d[k] ?? '') !== 'string') throw new StepError(`${where}: ${k} must be a string`);
  for (const k of ['prompts', 'undos']) {
    const v = d[k] ?? [];
    if (!Array.isArray(v) || !v.every((s) => typeof s === 'string' && s.trim())) throw new StepError(`${where}: ${k} must be a list of non-empty strings`);
  }
  for (const k of ['baseline', 'custom', 'optional']) if (typeof (d[k] ?? false) !== 'boolean') throw new StepError(`${where}: ${k} must be true or false`);
  if (!['', 'auto', 'manual'].includes((d.trans as string) ?? '')) throw new StepError(`${where}: trans must be "auto", "manual" or left out`);
  for (const k of ['sim', 'sim_undo', 'sim_pre']) {
    if (!SIM_ACTIONS.has((d[k] as string) ?? '')) throw new StepError(`${where}: ${k} must be one of ${[...SIM_ACTIONS].filter(Boolean).sort().join(', ')}`);
  }
  fastBlocks((d.fast as string) ?? '');
  const undo = !!(d.undo || (Array.isArray(d.undos) && d.undos.length));
  if (d.engine === 'off' && !undo) throw new StepError(`${where}: an engine-off step needs an undo (how to start the engine again)`);
  if (d.baseline && undo) throw new StepError(`${where}: a baseline step has nothing to undo`);
  if (d.setup_rest_s && !d.setup) throw new StepError(`${where}: setup_rest_s needs a setup prompt`);
  if (d.pre_s && !d.pre) throw new StepError(`${where}: pre_s needs a pre prompt`);
  return { ...STEP_DEFAULTS, ...(d as Partial<Step>), name, prompt: d.prompt } as Step;
}

/** Parse a steps TOML ([[step]] entries; include_defaults = true keeps the defaults). */
export function parseSteps(text: string, base: Step[] = DEFAULT_STEPS): Step[] {
  let doc: Record<string, unknown>;
  try {
    doc = parseToml(text) as Record<string, unknown>;
  } catch (e) {
    throw new StepError(`not valid TOML: ${(e as Error).message.split('\n')[0]}`);
  }
  const extra = Object.keys(doc).filter((k) => k !== 'step' && k !== 'include_defaults').sort();
  if (extra.length) throw new StepError(`unknown top-level key(s) ${extra.join(', ')}; use [[step]]`);
  const entries = (doc.step ?? []) as unknown[];
  if (!Array.isArray(entries) || !entries.every((e) => typeof e === 'object' && e !== null)) throw new StepError('[[step]] entries expected');
  const keep = doc.include_defaults ?? false;
  if (typeof keep !== 'boolean') throw new StepError('include_defaults must be true or false');
  if (!entries.length && !keep) throw new StepError('no [[step]] entries');
  const byName = new Map(base.map((s) => [s.name, s]));
  const out: Step[] = keep ? [...base] : [];
  const seen = new Set<string>();
  entries.forEach((raw, i) => {
    const e = raw as Record<string, unknown>;
    const start = typeof e.name === 'string' && byName.has(e.name) ? { ...byName.get(e.name)! } : {};
    const step = checkStep({ ...start, ...e }, `step ${i + 1}`);
    if (seen.has(step.name)) throw new StepError(`step ${i + 1}: name ${JSON.stringify(step.name)} used twice`);
    seen.add(step.name);
    const idx = out.findIndex((s) => s.name === step.name);
    if (idx >= 0) out[idx] = step; else out.push(step);
  });
  return out;
}

export function selectSteps(steps: Step[], names: string | null): Step[] {
  if (!names) return [...steps];
  const want = names.split(',').map((n) => n.trim()).filter(Boolean);
  const byName = new Map(steps.map((s) => [s.name, s]));
  const missing = want.filter((n) => !byName.has(n));
  if (missing.length) throw new StepError(`unknown step(s) ${missing.join(', ')}; have ${[...byName.keys()].join(', ')}`);
  return want.map((n) => byName.get(n)!);
}

/** Drop the steps for the other gearbox ('' keeps all). */
export function applicable(steps: Step[], trans: string): Step[] {
  return steps.filter((s) => !s.trans || !trans || s.trans === trans);
}

export function totals(steps: Step[]): { required: number; optional: number; prompts: number } {
  let required = 0, optional = 0, prompts = 0;
  for (const s of steps) {
    if (s.optional) optional += durationS(s);
    else { required += durationS(s); prompts += promptCount(s); }
  }
  return { required, optional, prompts };
}
