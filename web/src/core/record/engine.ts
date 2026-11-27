/**
 * The trigger engine (port of hondalog/triggers/engine.py): rules that watch
 * live values and open capture windows. Read that module's text for the full
 * story; in short:
 *
 *   * ``Fired``   a rule's condition has held for ``holdS``, right now
 *   * ``Capture`` the window around one or more firings is complete
 *                 (``preS`` before the first to ``postS`` after the last)
 *
 * Arming: with ``armWhen`` a rule is armed once that has been true and
 * disarmed by firing; without, armed from the start and re-armed by
 * ``rearmWhen`` (or by ``when`` going false). Rules of one ``group`` disarm
 * together. A value of null is "unknown"; a condition on an unknown value is
 * false, so ``rpm < 50`` cannot fire while the ECU is silent.
 *
 * Review (2026-11-26 rules): a rule with ``cancelWhen`` has its firing
 * checked the first time after the firing that every channel the condition
 * names has been read again; ``before_<name>`` is the last known value at the
 * firing. True: a false alarm ("revoked", ``onRevoked``), and a capture whose
 * firings were all revoked is ``cancelled``. A channel never seen before the
 * firing has no value (NaN): every comparison with it is false. No verdict
 * within ``reviewS``: the firing stands.
 *
 * A condition with history (rate(), avg() ...) is only stepped by a feed that
 * brings one of the channels it names. No I/O in here.
 */

import { type Condition, type Values, compile } from './condition';

export const ACTIONS = ['capture', 'mark', 'notify'] as const;
export type Action = (typeof ACTIONS)[number];

export interface TriggerRule {
  readonly name: string;
  /** fires when this has been true for holdS */
  readonly when: string;
  readonly armWhen: string;
  readonly rearmWhen: string;
  readonly holdS: number;
  readonly preS: number;
  readonly postS: number;
  readonly actions: readonly string[];
  /** "warn" or "critical" (or "info") */
  readonly severity: string;
  /** marker text, e.g. "STALL (rpm 0)" */
  readonly label: string;
  /** one line for people, e.g. "rpm dropped to 0" */
  readonly message: string;
  /** capture kind / name prefix, e.g. "stall" */
  readonly kind: string;
  /** short code for the UI (defaults to the name) */
  readonly reason: string;
  readonly group: string;
  readonly enabled: boolean;
  readonly cancelWhen: string;
  readonly reviewS: number;
  /** capture kind for a revoked firing ("" = drop it) */
  readonly cancelKind: string;
  readonly cancelLabel: string;
  readonly cancelMessage: string;
}

export type RuleInput = Partial<TriggerRule> & { name: string; when: string };

/** Make a rule: defaults filled in, every condition compiled now (an ExprError here, not in the car). */
export function triggerRule(input: RuleInput): TriggerRule {
  if (!input.name) throw new Error('a trigger rule needs a name');
  for (const text of [input.when, input.armWhen, input.rearmWhen, input.cancelWhen]) if (text) compile(text);
  if (!input.when) throw new Error(`rule ${JSON.stringify(input.name)}: 'when' is empty`);
  const actions = [...(input.actions ?? ACTIONS)];
  const unknown = actions.filter((a) => !(ACTIONS as readonly string[]).includes(a));
  if (unknown.length) throw new Error(`rule ${JSON.stringify(input.name)}: unknown action ${JSON.stringify(unknown[0])}`);
  const r: TriggerRule = {
    name: input.name, when: input.when, armWhen: input.armWhen ?? '', rearmWhen: input.rearmWhen ?? '',
    holdS: input.holdS ?? 0, preS: input.preS ?? 30, postS: input.postS ?? 10, actions,
    severity: input.severity ?? 'critical', label: input.label || input.name.toUpperCase(), message: input.message ?? '',
    kind: input.kind || input.name, reason: input.reason || input.name, group: input.group ?? '', enabled: input.enabled ?? true,
    cancelWhen: input.cancelWhen ?? '', reviewS: input.reviewS ?? 0, cancelKind: input.cancelKind ?? '',
    cancelLabel: input.cancelLabel ?? '', cancelMessage: input.cancelMessage ?? '',
  };
  if (r.holdS < 0 || r.preS < 0 || r.postS < 0 || r.reviewS < 0) throw new Error(`rule ${JSON.stringify(r.name)}: times cannot be negative`);
  return Object.freeze(r);
}

/** snake_case dict, as the Python app stores rules (drive header "triggers", triggers.toml). */
export function ruleToDict(r: TriggerRule): Record<string, unknown> {
  return {
    name: r.name, when: r.when, arm_when: r.armWhen, rearm_when: r.rearmWhen, hold_s: r.holdS, pre_s: r.preS, post_s: r.postS,
    actions: [...r.actions], severity: r.severity, label: r.label, message: r.message, kind: r.kind, reason: r.reason,
    group: r.group, enabled: r.enabled, cancel_when: r.cancelWhen, review_s: r.reviewS, cancel_kind: r.cancelKind,
    cancel_label: r.cancelLabel, cancel_message: r.cancelMessage,
  };
}

const FROM_DICT: Record<string, keyof TriggerRule> = {
  name: 'name', when: 'when', arm_when: 'armWhen', rearm_when: 'rearmWhen', hold_s: 'holdS', pre_s: 'preS', post_s: 'postS',
  actions: 'actions', severity: 'severity', label: 'label', message: 'message', kind: 'kind', reason: 'reason', group: 'group',
  enabled: 'enabled', cancel_when: 'cancelWhen', review_s: 'reviewS', cancel_kind: 'cancelKind', cancel_label: 'cancelLabel',
  cancel_message: 'cancelMessage',
};

/** A rule from a snake_case dict; unknown keys are ignored. */
export function ruleFromDict(doc: Record<string, unknown>): TriggerRule {
  const input: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(doc)) if (k in FROM_DICT) input[FROM_DICT[k]!] = v;
  return triggerRule(input as RuleInput);
}

export interface Fired {
  readonly rule: TriggerRule;
  readonly t: number;
  /** channel values at that moment */
  readonly values: Record<string, number | null | undefined>;
  readonly reason: string;
}

const fired = (rule: TriggerRule, t: number, values: Record<string, number | null | undefined>): Fired =>
  ({ rule, t, values, reason: rule.reason });

export class Capture {
  complete = false;
  revoked: Fired[] = [];
  constructor(public fired: Fired[], public tStart: number, public tEnd: number) {}

  /** Every firing in the window was revoked on review. */
  get cancelled(): boolean {
    return this.fired.length > 0 && this.fired.every((f) => this.revoked.includes(f));
  }

  get first(): Fired {
    return this.fired[0]!;
  }

  /** Time of the first firing. */
  get t(): number {
    return this.fired[0]!.t;
  }
}

/** One compiled condition and its last answer. */
class Cond {
  last = false;
  readonly expr: Condition;
  constructor(text: string) {
    this.expr = compile(text);
  }

  eval(env: Values, t: number, changed: ReadonlySet<string> | null): boolean {
    if (this.expr.stateful && (changed === null || !intersects(this.expr.deps, changed))) return this.last;
    this.last = this.expr.test(env, t);
    return this.last;
  }
}

function intersects(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  for (const x of a) if (b.has(x)) return true;
  return false;
}

/** A firing waiting for its cancelWhen verdict. */
class Review {
  readonly expr: Condition;
  readonly deadline: number;
  readonly before: Record<string, number>;
  constructor(readonly fired: Fired, known: Record<string, number>) {
    this.expr = compile(fired.rule.cancelWhen);
    this.deadline = fired.t + fired.rule.reviewS;
    this.before = {};
    for (const [k, v] of Object.entries(known)) this.before[`before_${k}`] = v;
  }

  /** true = false alarm, false = it stands, null = not known yet. */
  verdict(env: Values, t: number, changed: ReadonlySet<string> | null): boolean | null {
    const deps = this.expr.deps;
    if (t <= this.fired.t || changed === null || !intersects(deps, changed)) return null;
    const merged: Record<string, number | null | undefined> = { ...env, ...this.before };
    for (const name of deps) {
      const v = merged[name];
      if (v !== null && v !== undefined) continue;
      const channel = name.startsWith('before_') ? name.slice('before_'.length) : name;
      if (`before_${channel}` in this.before) return null;      // a channel we had: wait until it reads again
      merged[name] = NaN;                                         // never seen before the firing: no value
    }
    return this.expr.test(merged, t);
  }
}

class RuleState {
  readonly when: Cond;
  readonly arm: Cond | null;
  readonly rearm: Cond | null;
  armed: boolean;
  trueSince: number | null = null;
  fired = 0;
  constructor(readonly rule: TriggerRule) {
    this.when = new Cond(rule.when);
    this.arm = rule.armWhen ? new Cond(rule.armWhen) : null;
    this.rearm = rule.rearmWhen ? new Cond(rule.rearmWhen) : null;
    this.armed = this.arm === null;
  }
}

export type FeedValues = Record<string, number | null | undefined>;

/** Evaluates rules on live values. Feed it from one place. */
export class TriggerEngine {
  private states = new Map<string, RuleState>();
  readonly values: FeedValues = {};
  /** the window that is still open */
  capture: Capture | null = null;
  onFired: ((f: Fired) => void) | null = null;
  onCapture: ((c: Capture) => void) | null = null;
  /** a firing that review found to be a false alarm, and the time of the verdict */
  onRevoked: ((f: Fired, t: number) => void) | null = null;
  private reviews: Review[] = [];
  /** last value that was not null, per channel */
  private known: Record<string, number> = {};

  constructor(rules: Iterable<TriggerRule> = [], readonly maxCaptureS = 300.0) {
    for (const r of rules) this.add(r);
  }

  get rules(): TriggerRule[] {
    return [...this.states.values()].map((s) => s.rule);
  }

  /** Add a rule, or replace the one with the same name. */
  add(rule: TriggerRule): void {
    this.states.set(rule.name, new RuleState(rule));
  }

  remove(name: string): void {
    this.states.delete(name);
  }

  enable(name: string, on = true): void {
    const state = this.states.get(name);
    if (!state) throw new Error(`no rule ${JSON.stringify(name)}`);
    this.add(Object.freeze({ ...state.rule, enabled: on }));
  }

  armed(name: string): boolean {
    return this.states.get(name)!.armed;
  }

  firedCount(name: string): number {
    return this.states.get(name)!.fired;
  }

  get capturing(): boolean {
    return this.capture !== null;
  }

  /** A firing is still waiting for its verdict. */
  reviewing(): boolean {
    return this.reviews.length > 0;
  }

  /**
   * New values (null = unknown), every rule evaluated at time t. ``changed``
   * names the channels read just now (default: the keys of ``values``).
   * Returns the rules that fired now and the capture that completed now.
   */
  feed(t: number, values?: FeedValues | null, changed?: Iterable<string> | null): [Fired[], Capture | null] {
    let ch: ReadonlySet<string> | null = changed ? new Set(changed) : null;
    if (values && Object.keys(values).length) {
      Object.assign(this.values, values);
      if (!changed) ch = new Set(Object.keys(values));
      for (const [k, v] of Object.entries(values)) if (v !== null && v !== undefined) this.known[k] = v;
    }
    const env = this.values;
    if (this.reviews.length) this.review(env, t, ch);
    const out: Fired[] = [];
    for (const state of this.states.values()) {
      const rule = state.rule;
      if (!rule.enabled) continue;
      // every condition sees every feed, so its history has no holes
      const arm = state.arm ? state.arm.eval(env, t, ch) : false;
      const rearm = state.rearm ? state.rearm.eval(env, t, ch) : false;
      if (arm && !state.armed) state.armed = true;
      if (state.when.eval(env, t, ch)) {
        if (state.trueSince === null) state.trueSince = t;
        if (state.armed && t - state.trueSince >= rule.holdS) {
          state.fired++;
          out.push(fired(rule, t, { ...env }));
          for (const other of this.states.values()) {          // one stall, one capture
            if (other === state || (rule.group && other.rule.group === rule.group)) other.armed = false;
          }
        }
      } else {
        state.trueSince = null;
        if (state.arm === null && !state.armed && state.rearm === null) state.armed = true;
      }
      if (state.arm === null && !state.armed && rearm) state.armed = true;
    }
    for (const f of out) this.opened(f);
    return [out, this.closeIfDue(t)];
  }

  private review(env: Values, t: number, changed: ReadonlySet<string> | null): void {
    const keep: Review[] = [];
    for (const r of this.reviews) {
      if (t > r.deadline) continue;                   // no verdict in time: the firing stands
      const verdict = r.verdict(env, t, changed);
      if (verdict === null) {
        keep.push(r);
        continue;
      }
      if (verdict) {
        if (this.capture && this.capture.fired.includes(r.fired)) this.capture.revoked.push(r.fired);
        this.onRevoked?.(r.fired, t);
      }
    }
    this.reviews = keep;
  }

  /** Fire a rule by hand (a button, an alarm, a black box trigger): a known rule's name or a one-off rule. */
  fire(rule: TriggerRule | string, t: number): Fired {
    const r = typeof rule === 'string' ? this.states.get(rule)?.rule : rule;
    if (!r) throw new Error(`no rule ${JSON.stringify(rule)}`);
    const f = fired(r, t, { ...this.values });
    this.opened(f);
    return f;
  }

  /** Close the open window now (recording is stopping). */
  finish(): Capture | null {
    const capture = this.capture;
    this.capture = null;
    if (capture) this.onCapture?.(capture);
    return capture;
  }

  private opened(f: Fired): void {
    if (f.rule.cancelWhen) this.reviews.push(new Review(f, this.known));
    this.onFired?.(f);
    if (!f.rule.actions.includes('capture')) return;
    const start = f.t - f.rule.preS;
    const end = f.t + f.rule.postS;
    if (!this.capture) this.capture = new Capture([f], start, end);
    else {                                            // a second trigger inside the window: one bundle
      const cap = this.capture;
      cap.fired.push(f);
      cap.tStart = Math.min(cap.tStart, start);
      cap.tEnd = Math.min(Math.max(cap.tEnd, end), cap.t + this.maxCaptureS);
    }
  }

  private closeIfDue(t: number): Capture | null {
    const capture = this.capture;
    if (!capture || t < capture.tEnd) return null;
    capture.complete = true;
    capture.tEnd = t;                                 // keep the sample that closed it: "at least postS after"
    this.capture = null;
    this.onCapture?.(capture);
    return capture;
  }
}

/**
 * Run rules over a finished log (a table's arrays; NaN is unknown). Every row
 * counts as a new reading of every column. Returns every firing.
 */
export function scan(rules: TriggerRule[], t: ArrayLike<number>, columns: Record<string, ArrayLike<number>>): Fired[] {
  const engine = new TriggerEngine(rules);
  const names = Object.keys(columns);
  const out: Fired[] = [];
  for (let i = 0; i < t.length; i++) {
    const row: FeedValues = {};
    for (const n of names) {
      const x = columns[n]![i]!;
      row[n] = x !== x ? null : x;
    }
    out.push(...engine.feed(t[i]!, row)[0]);
  }
  return out;
}
