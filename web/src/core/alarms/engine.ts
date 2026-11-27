/**
 * Alarm rules and the state machine that runs them once per sample
 * (port of hondalog/alarms/engine.py).
 *
 * A rule trips when any of its tests is true:
 *
 *     low / high          value below / above a limit
 *     rate_low/rate_high  value changing faster than a limit, in units per second
 *                         (least-squares slope over ``rate_window_ms``)
 *     when                any expression, e.g. "[o2] < 0.2 && [tps] > 60"
 *
 * and, if ``gate`` is set, only while the gate expression is true. Life cycle::
 *
 *     idle --trip--> pending --held for hold_s--> ACTIVE --clear for clear_hold_s--> idle
 *                                                   |                                 ^
 *                                                   +-- latching: stays up as "latched"
 *                                                       until acknowledge() ---------+
 *
 * Clearing a limit needs the value to come back inside by ``hysteresis``, so
 * a value sitting on the limit does not chatter. While an input has no value
 * the rule holds its state: a dropped link neither raises nor clears anything.
 *
 * Field names stay snake_case as in the address map's [[alarm]] tables, so a
 * table parsed from TOML can be passed straight to ``new AlarmRule(table)``.
 */

import { compileExpr, type Column, type Expr, type Values } from '../expr/compiler';
import { Slope } from '../expr/funcs';
import { ExprError } from '../expr/nodes';
import { pyFormat, pyG, pyRepr, pyStr } from '../expr/pynum';

export const WARN = 'warn';
export const CRITICAL = 'critical';
export type Severity = typeof WARN | typeof CRITICAL;
const DEFAULT_PRIORITY: Record<string, number> = { [WARN]: 50, [CRITICAL]: 100 };
export const ACTIONS = ['tile', 'banner', 'beep', 'marker', 'capture'] as const;
export type AlarmAction = (typeof ACTIONS)[number];
const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

export type AlarmState = 'idle' | 'pending' | 'active' | 'latched';
export type AlarmEventKind = 'raised' | 'cleared' | 'acknowledged';
const IDLE = 'idle';
const PENDING = 'pending';
const ACTIVE = 'active';
const LATCHED = 'latched';

/** An alarm rule is wrong (bad expression, unknown channel, bad limits). */
export class AlarmError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AlarmError';
  }
}

export interface AlarmRuleInit {
  name: string;
  channel?: string;                 // for low/high/rate tests; also colours its tile
  low?: number | null;
  high?: number | null;
  rate_low?: number | null;         // units per second, e.g. -1500 for "falling fast"
  rate_high?: number | null;
  rate_window_ms?: number;
  when?: string;                    // expression that trips the alarm
  clear_when?: string;              // optional expression that re-arms a ``when`` alarm
  gate?: string;                    // the rule only runs while this is true
  hold_s?: number;                  // must stay tripped this long before raising
  clear_hold_s?: number;            // must stay clear this long before clearing
  hysteresis?: number;              // re-arm band for low/high
  severity?: string;
  priority?: number | null;         // higher wins; default 50 warn, 100 critical
  latching?: boolean;               // stays up until acknowledged
  actions?: readonly string[];
  message?: string;                 // "{label} {value:.0f} {units}"
  label?: string;
  enabled?: boolean;
}

export class AlarmRule {
  readonly name: string;
  readonly channel: string;
  readonly low: number | null;
  readonly high: number | null;
  readonly rate_low: number | null;
  readonly rate_high: number | null;
  readonly rate_window_ms: number;
  readonly when: string;
  readonly clear_when: string;
  readonly gate: string;
  readonly hold_s: number;
  readonly clear_hold_s: number;
  readonly hysteresis: number;
  readonly severity: Severity;
  readonly priority: number;
  readonly latching: boolean;
  readonly actions: readonly AlarmAction[];
  readonly message: string;
  readonly label: string;
  readonly enabled: boolean;

  constructor(init: AlarmRuleInit) {
    const name = init.name;
    if (typeof name !== 'string' || !NAME.test(name)) {
      throw new AlarmError(`bad alarm name ${typeof name === 'string' ? pyRepr(name) : String(name)}`);
    }
    const who = `alarm ${pyRepr(name)}`;
    const severity = init.severity ?? WARN;
    if (!Object.prototype.hasOwnProperty.call(DEFAULT_PRIORITY, severity)) {
      throw new AlarmError(`${who}: severity must be 'warn' or 'critical'`);
    }
    const actions = [...(init.actions ?? ['tile', 'banner'])];
    const bad = actions.filter((a) => !(ACTIONS as readonly string[]).includes(a));
    if (bad.length) throw new AlarmError(`${who}: unknown action ${pyRepr(bad[0]!)} (use ${ACTIONS.join(', ')})`);
    const low = init.low ?? null;
    const high = init.high ?? null;
    const rateLow = init.rate_low ?? null;
    const rateHigh = init.rate_high ?? null;
    const holdS = init.hold_s ?? 0.0;
    const clearHoldS = init.clear_hold_s ?? 0.0;
    const hysteresis = init.hysteresis ?? 0.0;
    const window = init.rate_window_ms ?? 500.0;
    const limits = [low, high, rateLow, rateHigh];
    for (const value of [...limits, holdS, clearHoldS, hysteresis, window]) {
      if (value !== null && typeof value !== 'number') throw new AlarmError(`${who}: limits and times must be numbers`);
    }
    const channel = init.channel ?? '';
    const when = init.when ?? '';
    const clearWhen = init.clear_when ?? '';
    if (limits.some((v) => v !== null) && !channel) throw new AlarmError(`${who}: low/high/rate limits need a 'channel'`);
    if (limits.every((v) => v === null) && !when.trim()) {
      throw new AlarmError(`${who}: needs a limit (low, high, rate_low, rate_high) or 'when'`);
    }
    if (low !== null && high !== null && low >= high) throw new AlarmError(`${who}: low must be below high`);
    if (holdS < 0 || clearHoldS < 0 || hysteresis < 0) throw new AlarmError(`${who}: hold times and hysteresis cannot be negative`);
    if (window <= 0) throw new AlarmError(`${who}: rate_window_ms must be more than 0`);
    if (clearWhen.trim() && !when.trim()) throw new AlarmError(`${who}: 'clear_when' only makes sense with 'when'`);
    this.name = name;
    this.channel = channel;
    this.low = low;
    this.high = high;
    this.rate_low = rateLow;
    this.rate_high = rateHigh;
    this.rate_window_ms = window;
    this.when = when;
    this.clear_when = clearWhen;
    this.gate = init.gate ?? '';
    this.hold_s = holdS;
    this.clear_hold_s = clearHoldS;
    this.hysteresis = hysteresis;
    this.severity = severity as Severity;
    this.priority = init.priority ?? DEFAULT_PRIORITY[severity]!;
    this.latching = init.latching ?? false;
    this.actions = actions as AlarmAction[];
    this.message = init.message ?? '';
    this.label = init.label || name.replace(/_/g, ' ');
    this.enabled = init.enabled ?? true;
  }

  get usesRate(): boolean {
    return this.rate_low !== null || this.rate_high !== null;
  }
}

/** Something happened to an alarm. Returned by ``AlarmEngine.update``. */
export class AlarmEvent {
  constructor(
    readonly kind: AlarmEventKind,
    readonly name: string,
    readonly severity: Severity,
    readonly priority: number,
    readonly t: number,                       // sample time in seconds
    readonly value: number | null,            // the rule's channel at that moment, if it has one
    readonly message: string,
    readonly actions: readonly AlarmAction[],
    readonly channel = '',
    readonly unverified = false,              // an input comes from an unverified address
  ) {}

  /** Text for a log marker: "ALARM ect_high: Coolant 106 °C". */
  get markerText(): string {
    const word = { raised: 'ALARM', cleared: 'CLEAR', acknowledged: 'ACK' }[this.kind];
    return `${word} ${this.name}: ${this.message}`;
  }
}

/** Live state of one rule, for the UI. */
export class AlarmStatus {
  state: AlarmState = IDLE;
  since = 0.0;                    // when the current state began
  raisedAt: number | null = null;
  acknowledged = false;
  value: number | null = null;
  rate: number | null = null;
  message = '';
  count = 0;                      // times raised this session

  constructor(readonly rule: AlarmRule, public unverified = false) {}

  get name(): string {
    return this.rule.name;
  }

  /** True while the alarm should be shown (tripped, or latched). */
  get active(): boolean {
    return this.state === ACTIVE || this.state === LATCHED;
  }

  get severity(): Severity {
    return this.rule.severity;
  }

  get priority(): number {
    return this.rule.priority || 0;
  }
}

interface Runtime {
  status: AlarmStatus;
  when: Expr | null;
  clear_when: Expr | null;
  gate: Expr | null;
  slope: Slope | null;
  clearSince: number | null;
  inputs: ReadonlySet<string>;
}

export type AlarmListener = (event: AlarmEvent) => void;

/**
 * Runs a set of rules. Call ``update`` once per sample.
 *
 * rules      AlarmRule list (disabled rules are dropped)
 * names      every channel name the rules may use (measured and math)
 * constants  named numbers for expressions
 * verified   optional {channel: bool}; events and statuses of rules that read
 *            an unverified channel carry ``unverified = true``
 * labels     optional {channel: [label, units]} for messages
 */
export class AlarmEngine {
  private readonly args: [AlarmRule[], string[], Record<string, number>, Record<string, boolean> | null, Record<string, [string, string]>];
  private readonly labels: Record<string, [string, string]>;
  private readonly listeners: AlarmListener[] = [];
  private readonly rt: Runtime[] = [];
  private readonly byName = new Map<string, Runtime>();
  private readonly byChannel = new Map<string, Runtime[]>();
  private nActive = 0;

  constructor(rules: readonly AlarmRule[], names: Iterable<string>,
    constants: Readonly<Record<string, number>> | null = null,
    verified: Readonly<Record<string, boolean>> | null = null,
    labels: Readonly<Record<string, readonly [string, string]>> | null = null) {
    const known = new Set(names);
    this.labels = { ...(labels ?? {}) } as Record<string, [string, string]>;
    this.args = [[...rules], [...known], { ...(constants ?? {}) }, verified === null ? null : { ...verified }, this.labels];
    const seen = new Set<string>();
    for (const rule of rules) {
      if (!rule.enabled) continue;
      if (seen.has(rule.name)) throw new AlarmError(`duplicate alarm ${pyRepr(rule.name)}`);
      seen.add(rule.name);
      if (rule.channel && !known.has(rule.channel)) {
        throw new AlarmError(`alarm ${pyRepr(rule.name)}: unknown channel ${pyRepr(rule.channel)}`);
      }
      const rt: Runtime = {
        status: new AlarmStatus(rule), when: null, clear_when: null, gate: null, slope: null,
        clearSince: null, inputs: new Set(),
      };
      const inputs = new Set<string>(rule.channel ? [rule.channel] : []);
      for (const attr of ['when', 'clear_when', 'gate'] as const) {
        const text = rule[attr].trim();
        if (!text) continue;
        let expr: Expr;
        try {
          expr = compileExpr(text, { names: known, constants: constants ?? {} });
        } catch (exc) {
          if (exc instanceof ExprError) throw new AlarmError(`alarm ${pyRepr(rule.name)}, ${attr}: ${exc.message}`);
          throw exc;
        }
        rt[attr] = expr;
        for (const d of expr.deps) inputs.add(d);
      }
      if (rule.usesRate) rt.slope = new Slope(rule.rate_window_ms);
      rt.inputs = inputs;
      if (verified !== null) rt.status.unverified = [...inputs].some((n) => verified[n] === false);
      this.rt.push(rt);
    }
    for (const rt of this.rt) {
      this.byName.set(rt.status.name, rt);
      const ch = rt.status.rule.channel;
      if (ch) {
        const list = this.byChannel.get(ch);
        if (list) list.push(rt);
        else this.byChannel.set(ch, [rt]);
      }
    }
  }

  // -- queries --

  get length(): number {
    return this.rt.length;
  }

  get rules(): AlarmRule[] {
    return this.rt.map((rt) => rt.status.rule);
  }

  private get_(name: string): Runtime {
    const rt = this.byName.get(name);
    if (!rt) throw new Error(`no alarm ${pyRepr(name)}`);
    return rt;
  }

  /** Channels a rule reads. */
  inputs(name: string): ReadonlySet<string> {
    return this.get_(name).inputs;
  }

  status(name: string): AlarmStatus {
    return this.get_(name).status;
  }

  statuses(): AlarmStatus[] {
    return this.rt.map((rt) => rt.status);
  }

  /** Alarms to show now, most important first (priority, then oldest). */
  active(): AlarmStatus[] {
    if (!this.nActive) return [];
    const live = this.rt.map((rt) => rt.status).filter((s) => s.active);
    return live.sort((a, b) => (b.priority - a.priority) || ((a.raisedAt || 0) - (b.raisedAt || 0)));
  }

  /** The one alarm a banner should show, or null. */
  top(): AlarmStatus | null {
    return this.active()[0] ?? null;
  }

  /** Worst active severity among rules on this channel (tile colour). */
  severityFor(channel: string): Severity | null {
    let worst: Severity | null = null;
    for (const rt of this.byChannel.get(channel) ?? []) {
      if (rt.status.active && rt.status.rule.actions.includes('tile')) {
        if (rt.status.severity === CRITICAL) return CRITICAL;
        worst = WARN;
      }
    }
    return worst;
  }

  /** Call ``callback(event)`` for every event; returns an unsubscribe
   *  function. An exception in a callback is swallowed. */
  subscribe(callback: AlarmListener): () => void {
    this.listeners.push(callback);
    return () => {
      const i = this.listeners.indexOf(callback);
      if (i >= 0) this.listeners.splice(i, 1);
    };
  }

  // -- the per-sample step --

  /** Feed one sample (measured and math values). Returns new events. */
  update(values: Values, t: number): AlarmEvent[] {
    const events: AlarmEvent[] = [];
    for (const rt of this.rt) {
      const st = rt.status;
      const rule = st.rule;
      const tripped = this.tripped(rt, values, t);
      if (tripped === null) continue;                         // unknown: hold state
      let state = st.state;
      if (tripped) {
        rt.clearSince = null;
        if (state === IDLE) {
          st.state = PENDING;
          st.since = t;
          state = PENDING;
        }
        if (state === PENDING && t - st.since >= rule.hold_s) {
          this.raise(rt, t, values, events);
        } else if (state === LATCHED) {
          st.state = ACTIVE;                                   // came back before the ack
          st.since = t;
        }
      } else if (state === PENDING) {
        st.state = IDLE;
        st.since = t;
      } else if (state === ACTIVE) {
        if (rt.clearSince === null) rt.clearSince = t;
        if (t - rt.clearSince >= rule.clear_hold_s) {
          rt.clearSince = null;
          if (rule.latching && !st.acknowledged) {
            st.state = LATCHED;
            st.since = t;
          } else {
            this.clear(rt, t, events);
          }
        }
      }
    }
    if (events.length) this.publish(events);
    return events;
  }

  /** true = tripped, false = clear, null = cannot tell (missing input). */
  private tripped(rt: Runtime, values: Values, t: number): boolean | null {
    const st = rt.status;
    const rule = st.rule;
    if (rt.gate !== null) {
      const gate = rt.gate.truth(values, t);
      if (gate === null) return null;
      if (!gate) {
        rt.slope?.reset();
        return false;
      }
    }
    let trip = false;
    let clear = true;
    if (rule.channel) {
      const raw = values[rule.channel];
      if (raw === null || raw === undefined || typeof raw === 'object') return null;
      const value = typeof raw === 'boolean' ? (raw ? 1 : 0) : raw;
      if (value !== value) return null;
      st.value = value;
      const hyst = rule.hysteresis;
      if (rule.low !== null) {
        if (value < rule.low) trip = true;
        else if (value < rule.low + hyst) clear = false;
      }
      if (rule.high !== null) {
        if (value > rule.high) trip = true;
        else if (value > rule.high - hyst) clear = false;
      }
      if (rt.slope !== null) {
        const rate = rt.slope.update(t, value);
        st.rate = rate;
        if (rate !== null) {
          if (rule.rate_low !== null && rate < rule.rate_low) trip = true;
          if (rule.rate_high !== null && rate > rule.rate_high) trip = true;
        }
      }
    }
    if (rt.when !== null) {
      const cond = rt.when.truth(values, t);
      if (cond) {
        trip = true;
      } else if (cond === null) {
        if (!trip) return null;
      } else if (rt.clear_when !== null && !trip) {
        if (!rt.clear_when.truth(values, t)) clear = false;    // false or unknown: not yet
      }
    }
    if (trip) return true;
    if (clear) return false;
    return st.state === ACTIVE;                // in the re-arm band: an active alarm stays up
  }

  private raise(rt: Runtime, t: number, values: Values, events: AlarmEvent[]): void {
    const st = rt.status;
    st.state = ACTIVE;
    st.since = t;
    st.raisedAt = t;
    st.acknowledged = false;
    st.count += 1;
    st.message = this.format(rt, values);
    this.nActive += 1;
    events.push(this.event('raised', rt, t));
  }

  private clear(rt: Runtime, t: number, events: AlarmEvent[]): void {
    const st = rt.status;
    st.state = IDLE;
    st.since = t;
    st.acknowledged = false;
    this.nActive -= 1;
    events.push(this.event('cleared', rt, t));
  }

  private event(kind: AlarmEventKind, rt: Runtime, t: number): AlarmEvent {
    const st = rt.status;
    const rule = st.rule;
    return new AlarmEvent(kind, rule.name, rule.severity, rule.priority || 0, t, st.value, st.message,
      rule.actions, rule.channel, st.unverified);
  }

  private publish(events: AlarmEvent[]): void {
    for (const event of events) {
      for (const callback of [...this.listeners]) {
        try {
          callback(event);
        } catch {
          // a broken subscriber cannot stop polling
        }
      }
    }
  }

  private format(rt: Runtime, values: Values): string {
    const st = rt.status;
    const rule = st.rule;
    const [label, units] = this.labels[rule.channel] ?? [rule.channel, ''];
    const fields: Record<string, unknown> = {
      name: rule.name, label: rule.label, channel: rule.channel, channel_label: label, units,
      value: st.value, rate: st.rate, low: rule.low, high: rule.high, severity: rule.severity,
    };
    for (const [k, v] of Object.entries(values)) if (!(k in fields)) fields[k] = v;
    if (rule.message) return fillTemplate(rule.message, fields);
    if (rule.channel && st.value !== null) {
      let text = `${label || rule.channel} ${pyG(st.value, 4)}`;
      text += units ? ` ${units}` : '';
      if (rule.high !== null && st.value > rule.high) text += ` (limit ${pyG(rule.high)})`;
      else if (rule.low !== null && st.value < rule.low) text += ` (limit ${pyG(rule.low)})`;
      else if (st.rate !== null && rule.usesRate) text += `, changing ${pyFormat(st.rate, '+.4g')}${units ? ' ' + units : ''}/s`;
      return `${rule.label}: ${text}`;
    }
    return rule.label;
  }

  // -- operator actions --

  /**
   * Acknowledge one alarm, or all when ``name`` is null.
   *
   * A latched alarm whose condition is gone clears now. One that is still
   * tripped stays up but is marked acknowledged (the UI stops beeping) and
   * clears by itself when the condition goes away.
   */
  acknowledge(name: string | null = null, t: number | null = null): AlarmEvent[] {
    const events: AlarmEvent[] = [];
    const targets = name === null ? this.rt : [this.get_(name)];
    for (const rt of targets) {
      const st = rt.status;
      const when = t === null ? st.since : t;
      if (st.state === LATCHED) {
        events.push(this.event('acknowledged', rt, when));
        this.clear(rt, when, events);
      } else if (st.state === ACTIVE && !st.acknowledged) {
        st.acknowledged = true;
        events.push(this.event('acknowledged', rt, when));
      }
    }
    if (events.length) this.publish(events);
    return events;
  }

  /** Back to all-idle (new connection or new log). No events are sent. */
  reset(): void {
    for (const rt of this.rt) {
      rt.status = new AlarmStatus(rt.status.rule, rt.status.unverified);
      rt.clearSince = null;
      for (const expr of [rt.when, rt.clear_when, rt.gate]) expr?.reset();
      rt.slope?.reset();
    }
    this.nActive = 0;
  }

  // -- replay --

  /**
   * Run the rules over a whole log; returns every event in order.
   *
   * Works on a private copy of the engine, so live alarm state and
   * subscribers are not touched. NaN (or null) in a column means "no value".
   */
  replay(t: ArrayLike<number>, columns: Readonly<Record<string, Column>>): AlarmEvent[] {
    const [rules, names, constants, verified, labels] = this.args;
    const engine = new AlarmEngine(rules, names, constants, verified, labels);
    const needed = new Set<string>();
    for (const rt of engine.rt) for (const n of rt.inputs) needed.add(n);
    const namesIn = [...needed].sort().filter((n) => Object.prototype.hasOwnProperty.call(columns, n));
    const series = namesIn.map((n) => columns[n]!);
    const events: AlarmEvent[] = [];
    for (let i = 0; i < t.length; i++) {
      const row: Record<string, number | null> = {};
      namesIn.forEach((n, k) => {
        const x = series[k]![i];
        row[n] = x === null || x === undefined || x !== x ? null : x;
      });
      events.push(...engine.update(row, t[i]!));
    }
    return events;
  }
}

const FIELD = /\{([\p{L}\p{N}_]+)(?::([^{}]*))?\}/gu;
const SAFE_SPEC = /^[<>^+ ]?\d{0,3}(\.\d{1,2})?[dfg%]?$/;

/**
 * Replace {name} and {name:.1f}. Unknown names and bad specs are left as
 * written. Only plain names: no attribute or index access like str.format.
 * Numbers are formatted as Python floats (so ``{rpm:d}`` falls back to "800.0").
 */
export function fillTemplate(template: string, fields: Readonly<Record<string, unknown>>): string {
  return template.replace(FIELD, (whole: string, key: string, spec: string | undefined) => {
    if (!Object.prototype.hasOwnProperty.call(fields, key)) return whole;
    const value = fields[key];
    if (value === null || value === undefined) return '--';
    if (spec && SAFE_SPEC.test(spec)) {
      try {
        if (typeof value === 'number') return pyFormat(value, spec);
        if (typeof value === 'string') return pyFormat(value, spec);
      } catch {
        return pyStr(value);
      }
      return pyStr(value);
    }
    return typeof value === 'number' ? pyG(value) : pyStr(value);
  });
}
