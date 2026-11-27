/**
 * Runs the guided poke test (tools/poke_test.py run_steps / _run_one),
 * recording the phases and owner marks while the memory scanner sweeps.
 *
 * The cable: the Python tool took the KKL cable from the app with a pause
 * file and a window close. Here the test lives inside the app, so it asks
 * the live session to pause its polling (LiveControl.pause) and always
 * resumes it on the way out, a stop or an error included. The scanner's
 * reads go through the same link meanwhile.
 */

import type { Block } from '../model/scanner';
import type { EventMark, Markers, PhaseMark, StepMeta, Sweep } from './analyse';
import { fastBlocks, hasUndo, promptFor, undoFor, type Step } from './steps';

/** What the runner asks the owner (implemented by the screen, or by `autoUi`). */
export interface PokeUi {
  /** Show text and wait for Enter; resolves '' (Enter), 's' (skip step) or 'q' (stop the test). */
  ask(text: string, choices: string): Promise<string>;
  yesNo(text: string): Promise<boolean>;
  /** A timed phase. `onMark` is set during holds with a mark: call it when the owner presses Enter. */
  countdown(label: string, seconds: number, opts?: { onMark?: () => void; markText?: string }): Promise<void>;
  /** The custom step: what will you do, and how is it undone (null = skip). */
  custom(): Promise<{ prompt: string; undo: string } | null>;
  beep?(long?: boolean): void;
  /** Step banner: n of total, the step and whether it is skipped. */
  step?(n: number, total: number, step: Step, info: string): void;
  readonly auto?: boolean;
}

/** The live session the test borrows the line from. */
export interface LiveControl {
  pause(): Promise<void>;
  resume(): Promise<void>;
}

export class Recorder {
  phases: PhaseMark[] = [];
  events: EventMark[] = [];
  steps: StepMeta[] = [];
  sweeps: Sweep[] = [];
  constructor(readonly clock: () => number, readonly t0 = clock()) {}
  now(): number { return this.clock() - this.t0; }
  phase(step: string, rep: number, phase: string, t0: number, t1: number, extra: Record<string, unknown> = {}): void {
    this.phases.push({ step, rep, phase, t0, t1, ...extra });
  }
  event(step: string, rep: number, label: string, t: number): void {
    this.events.push({ step, rep, label, t });
  }
  /** A finished sweep from the scanner: the bytes and when each was read (scanner clock). */
  addSweep(mem: ArrayLike<number>, times: ArrayLike<number>): void {
    let lo = Infinity, hi = -Infinity;
    const vals: (number | null)[] = [];
    for (let a = 0; a < 256; a++) {
      const v = mem[a]!;
      vals.push(v >= 0 ? v : null);
      const t = times[a]!;
      if (v >= 0 && Number.isFinite(t)) { lo = Math.min(lo, t); hi = Math.max(hi, t); }
    }
    if (!Number.isFinite(lo)) { lo = hi = this.clock(); }
    this.sweeps.push({ t0: lo - this.t0, t1: hi - this.t0, vals });
  }
  markers(): Markers {
    return { steps: this.steps, phases: this.phases, events: this.events };
  }
}

export interface RunOptions {
  trans?: '' | 'auto' | 'manual';
  optional?: 'ask' | 'yes' | 'no';
  /** Simulator actions (the dry run). */
  sim?: (action: string) => void;
  /** Read only some blocks during a step (the scanner's setBlocks). */
  setFast?: (blocks: Block[] | null) => void;
  /** Checked between phases: true stops the test. */
  stopped?: () => boolean;
}

export async function runSteps(steps: Step[], rec: Recorder, ui: PokeUi, opts: RunOptions = {}): Promise<'ok' | 'quit'> {
  const { trans = '', optional = 'ask', sim, setFast } = opts;
  rec.steps = steps.map((s) => ({ ...s }));
  for (let n = 1; n <= steps.length; n++) {
    if (opts.stopped?.()) return 'quit';
    let s = steps[n - 1]!;
    if (s.trans && trans && s.trans !== trans) {
      ui.step?.(n, steps.length, s, `skipped: ${s.trans} gearbox only`);
      rec.phase(s.name, 0, 'skipped', rec.now(), rec.now(), { why: `${s.trans} only` });
      continue;
    }
    ui.step?.(n, steps.length, s, '');
    if (s.optional && !s.custom) {
      let doIt = optional === 'yes' ? true : optional === 'no' ? false : null;
      if (doIt === null) doIt = await ui.yesNo(`Optional step '${s.name}': do it?`);
      if (!doIt) { rec.phase(s.name, 0, 'skipped', rec.now(), rec.now(), { why: 'optional' }); continue; }
    }
    if (s.custom) {
      const c = ui.auto || optional === 'no' ? null : await ui.custom();
      if (!c || !c.prompt.trim()) { rec.phase(s.name, 0, 'skipped', rec.now(), rec.now(), { why: 'custom' }); continue; }
      s = { ...s, prompt: c.prompt.trim(), undo: c.undo.trim() };
      rec.steps[n - 1] = { ...rec.steps[n - 1]!, prompt: s.prompt, undo: s.undo };
    }
    if (s.fast) setFast?.(fastBlocks(s.fast));
    try {
      if ((await runOne(s, rec, ui, sim, opts.stopped)) === 'quit') return 'quit';
    } finally {
      if (s.fast) setFast?.(null);
    }
  }
  return 'ok';
}

async function runOne(s: Step, rec: Recorder, ui: PokeUi, sim?: (a: string) => void, stopped?: () => boolean): Promise<'ok' | 'skip' | 'quit'> {
  const timed = async (rep: number, phase: string, secs: number, label: string, kw: { onMark?: () => void; markText?: string } = {}) => {
    if (secs <= 0) return;
    const t = rec.now();
    await ui.countdown(label, secs, kw);
    rec.phase(s.name, rep, phase, t, rec.now());
  };
  if (s.setup) {
    const t = rec.now();
    const ans = await ui.ask(s.setup, 's = skip step, q = stop the test');
    if (ans === 'q' || ans === 's') { rec.phase(s.name, 0, ans === 'q' ? 'quit' : 'skipped', t, rec.now()); return ans === 'q' ? 'quit' : 'skip'; }
    rec.phase(s.name, 0, 'wait', t, rec.now());
    await timed(0, 'rest', s.setup_rest_s, 'rest - hands off (before)');
  }
  for (let rep = 1; rep <= s.repeat; rep++) {
    if (stopped?.()) return 'quit';
    if (s.pre) {
      const t = rec.now();
      const ans = await ui.ask(s.pre, 's = skip step, q = stop the test');
      if (ans === 'q' || ans === 's') { rec.phase(s.name, rep, ans === 'q' ? 'quit' : 'skipped', t, rec.now()); return ans === 'q' ? 'quit' : 'skip'; }
      if (sim && s.sim_pre) sim(s.sim_pre);
      rec.phase(s.name, rep, 'wait_pre', t, rec.now());
      await timed(rep, 'pre', s.pre_s, 'wait');
    }
    let t = rec.now();
    const ans = await ui.ask(promptFor(s, rep), s.pre ? '' : 's = skip step, q = stop the test');
    if (ans === 'q') { rec.phase(s.name, rep, 'quit', t, rec.now()); return 'quit'; }
    if (ans === 's' && !s.pre) { rec.phase(s.name, rep, 'skipped', t, rec.now()); return 'skip'; }
    if (sim && s.sim) sim(s.sim);
    rec.phase(s.name, rep, 'wait', t, rec.now(), { prompt: promptFor(s, rep) });
    await timed(rep, 'settle', s.settle_s, 'settle (let it change)');
    const onMark = s.mark ? () => rec.event(s.name, rep, s.mark, rec.now()) : undefined;
    await timed(rep, 'hold', s.hold_s, 'HOLD - keep it like this', { onMark, markText: s.mark });
    ui.beep?.(true);
    const undo = undoFor(s, rep);
    if (undo) {
      t = rec.now();
      await ui.ask(undo, '');
      if (sim && s.sim_undo) sim(s.sim_undo);
      rec.phase(s.name, rep, 'undo_wait', t, rec.now());
      await timed(rep, 'undo_settle', s.undo_settle_s, 'settle after undo');
    }
    await timed(rep, 'rest', s.rest_s, 'rest - hands off');
    ui.beep?.();
  }
  if (s.teardown) {
    const t = rec.now();
    await ui.ask(s.teardown, '');
    rec.phase(s.name, 0, 'wait', t, rec.now());
  }
  return 'ok';
}

/** Borrow the line from the live session for `fn`, and always give it back. */
export async function withLinePaused<T>(live: LiveControl | null, fn: () => Promise<T>): Promise<T> {
  if (live) await live.pause();
  try {
    return await fn();
  } finally {
    if (live) await live.resume();
  }
}

/** An owner who answers every prompt with Enter at once (the dry run / tests).
 *  `advance(seconds)` is called for every timed phase so a fake clock and
 *  the simulator can move on. */
export function autoUi(advance: (seconds: number) => Promise<void> | void, answers: Record<string, string> = {}): PokeUi {
  return {
    auto: true,
    ask: async (text) => answers[text] ?? '',
    yesNo: async () => false,
    countdown: async (_label, seconds, opts) => {
      if (opts?.onMark) { await advance(seconds / 2); opts.onMark(); await advance(seconds / 2); } else await advance(seconds);
    },
    custom: async () => null,
  };
}

export { hasUndo };
