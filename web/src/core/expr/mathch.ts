/**
 * Math channels: values worked out from other channels, live and on replay
 * (hondalog/mathch).
 *
 *     const engine = new MathEngine([new MathChannel({ name: 'duty', expr: '[inj] * [rpm] / 1200', units: '%' })],
 *                                   ['inj', 'rpm']);
 *     engine.update({ inj: 2.4, rpm: 3000 }, 12.5);      // { duty: 6 }
 *
 * Expressions are compiled once. A math channel may use another math channel;
 * the engine sorts them so inputs come first and refuses cycles. A channel
 * whose inputs are missing reads null for that sample; nothing in here throws
 * while polling.
 */

import { compileExpr, references, type Column, type Expr, type Values } from './compiler';
import { ExprError } from './nodes';
import { pyFixed, pyRepr } from './pynum';
import { checkUnits } from './unitcheck';

const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** A math channel definition is wrong (bad expression, unknown input, cycle). */
export class MathError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'MathError';
  }
}

export interface MathChannelInit {
  name: string;
  expr: string;
  label?: string;
  units?: string;
  min?: number;
  max?: number;
  decimals?: number;
  gauge?: boolean;
  plot?: boolean;
  kind?: 'value' | 'flag' | string;
  description?: string;
  enabled?: boolean;
}

/** One derived channel. Field names match the measured channels where they
 *  mean the same thing, so the UI can treat both alike. */
export class MathChannel {
  name: string;
  expr: string;
  label: string;
  units: string;
  min: number;
  max: number;
  decimals: number;
  gauge: boolean;
  plot: boolean;
  kind: 'value' | 'flag';
  description: string;
  enabled: boolean;

  constructor(init: MathChannelInit) {
    const { name, expr } = init;
    if (typeof name !== 'string' || !NAME.test(name)) throw new MathError(`bad math channel name ${typeof name === 'string' ? pyRepr(name) : String(name)}`);
    if (typeof expr !== 'string' || !expr.trim()) throw new MathError(`${name}: no expression`);
    const kind = init.kind ?? 'value';
    if (kind !== 'value' && kind !== 'flag') throw new MathError(`${name}: kind must be 'value' or 'flag'`);
    this.name = name;
    this.expr = expr;
    this.label = init.label || name;
    this.units = init.units ?? '';
    this.min = init.min ?? 0.0;
    this.max = init.max ?? 100.0;
    this.decimals = init.decimals ?? 1;
    this.gauge = init.gauge ?? false;
    this.plot = init.plot ?? false;
    this.kind = kind;
    this.description = init.description ?? '';
    this.enabled = init.enabled ?? true;
    if (this.kind === 'flag') {
      this.min = 0.0;
      this.max = 1.0;
      this.decimals = 0;
    }
  }

  format(value: number | null | undefined): string {
    if (value === null || value === undefined || value !== value) return '--';
    return pyFixed(value, this.decimals);
  }
}

interface Compiled {
  channel: MathChannel;
  expr: Expr;
  baseInputs: ReadonlySet<string>;
}

export type MathValues = Record<string, number | null>;

/**
 * Evaluates math channels in dependency order.
 *
 * channels   the definitions (disabled ones are skipped)
 * inputs     names of the measured channels expressions may read
 * constants  named numbers (``[constants]`` in the address map)
 * units      unit text per input name, for the mixed-unit warnings
 *
 * Throws MathError for a bad expression, an unknown name, a name clash or a
 * cycle. ``warnings`` lists unit mix-ups; they do not stop anything.
 */
export class MathEngine {
  readonly inputs: ReadonlySet<string>;
  readonly constants: Readonly<Record<string, number>>;
  readonly channels: MathChannel[];
  readonly warnings: string[] = [];
  readonly names: string[];
  private readonly compiled: Compiled[] = [];
  private last: MathValues;

  constructor(channels: readonly MathChannel[], inputs: Iterable<string>,
    constants: Readonly<Record<string, number>> | null = null,
    units: Readonly<Record<string, string>> | null = null) {
    this.inputs = new Set(inputs);
    this.constants = { ...(constants ?? {}) };
    const active = channels.filter((c) => c.enabled);
    const byName = new Map<string, MathChannel>();
    for (const ch of active) {
      if (byName.has(ch.name)) throw new MathError(`duplicate math channel ${pyRepr(ch.name)}`);
      if (this.inputs.has(ch.name)) throw new MathError(`math channel ${pyRepr(ch.name)} has the same name as a measured channel`);
      if (Object.prototype.hasOwnProperty.call(this.constants, ch.name)) {
        throw new MathError(`math channel ${pyRepr(ch.name)} has the same name as a constant`);
      }
      byName.set(ch.name, ch);
    }
    this.channels = sortChannels(byName);
    const known = new Set([...this.inputs, ...byName.keys()]);
    const unitMap: Record<string, string> = { ...(units ?? {}) };
    for (const c of active) if (c.units) unitMap[c.name] = c.units;
    const base = new Map<string, ReadonlySet<string>>();
    for (const ch of this.channels) {
      let expr: Expr;
      try {
        expr = compileExpr(ch.expr, { names: known, constants: this.constants });
      } catch (exc) {
        if (exc instanceof ExprError) throw new MathError(`math channel ${pyRepr(ch.name)}: ${exc.message}`);
        throw exc;
      }
      for (const w of checkUnits(expr.node, unitMap, ch.units || null)) this.warnings.push(`${ch.name}: ${w}`);
      const deps = new Set<string>();
      for (const dep of expr.deps) for (const d of base.get(dep) ?? [dep]) deps.add(d);
      base.set(ch.name, deps);
      this.compiled.push({ channel: ch, expr, baseInputs: deps });
    }
    this.names = this.channels.map((c) => c.name);
    this.last = Object.fromEntries(this.names.map((n) => [n, null]));
  }

  get length(): number {
    return this.compiled.length;
  }

  channel(name: string): MathChannel | null {
    return this.channels.find((c) => c.name === name) ?? null;
  }

  /** Measured channels a math channel depends on, through other math
   *  channels too. The poll scheduler can use this to pick its rate. */
  baseInputs(name: string): ReadonlySet<string> {
    const c = this.compiled.find((x) => x.channel.name === name);
    if (!c) throw new Error(`no math channel ${pyRepr(name)}`);
    return c.baseInputs;
  }

  expr(name: string): Expr {
    const c = this.compiled.find((x) => x.channel.name === name);
    if (!c) throw new Error(`no math channel ${pyRepr(name)}`);
    return c.expr;
  }

  // -- live --

  /**
   * Evaluate for one sample; returns ``{math name: value or null}``.
   *
   * ``changed`` names the inputs that are new in this sample. When given,
   * only math channels fed by one of them are re-evaluated (so each runs at
   * the rate of its fastest input); the rest keep their last value.
   */
  update(values: Values, t: number, changed?: Iterable<string> | null): MathValues {
    const scope: Record<string, Values[string]> = { ...values };
    const out: MathValues = {};
    if (changed === null || changed === undefined) {
      for (const c of this.compiled) {
        const v = c.expr.eval(scope, t);
        scope[c.channel.name] = v;
        out[c.channel.name] = v;
      }
    } else {
      const fresh = new Set(changed);
      const last = this.last;
      for (const c of this.compiled) {
        const name = c.channel.name;
        let v: number | null;
        if (c.baseInputs.size && ![...c.baseInputs].some((n) => fresh.has(n))) v = last[name] ?? null;
        else v = c.expr.eval(scope, t);
        scope[name] = v;
        out[name] = v;
      }
    }
    this.last = out;
    return out;
  }

  /** ``values`` plus the math channels, as one new object. */
  extend(values: Values, t: number): Record<string, Values[string]> {
    return { ...values, ...this.update(values, t) };
  }

  /** Forget window history (new connection, new log, seek). */
  reset(): void {
    for (const c of this.compiled) c.expr.reset();
    this.last = Object.fromEntries(this.names.map((n) => [n, null]));
  }

  // -- replay --

  /**
   * Evaluate every math channel over a whole log.
   *
   * ``t`` is the time column in seconds, ``columns`` maps measured channel
   * names to equal-length arrays with NaN (or null) for missing values.
   * Returns one array per math channel, NaN where it has no value. Live state
   * is not touched, so this is safe while polling.
   */
  computeSeries(t: ArrayLike<number>, columns: Readonly<Record<string, Column>>): Record<string, Float64Array> {
    const scope: Record<string, Column> = { ...columns };
    const blank = new Float64Array(t.length).fill(NaN);
    for (const name of this.inputs) if (!Object.prototype.hasOwnProperty.call(scope, name)) scope[name] = blank;
    const out: Record<string, Float64Array> = {};
    for (const c of this.compiled) {
      const series = c.expr.evalArray(scope, t);
      scope[c.channel.name] = series;
      out[c.channel.name] = series;
    }
    return out;
  }
}

/** Topological order; throws MathError naming the loop if there is one. */
function sortChannels(byName: ReadonlyMap<string, MathChannel>): MathChannel[] {
  const deps = new Map<string, string[]>();
  for (const [name, ch] of byName) {
    let refs: Set<string>;
    try {
      refs = references(ch.expr);
    } catch (exc) {
      if (exc instanceof ExprError) throw new MathError(`math channel ${pyRepr(name)}: ${exc.message}`);
      throw exc;
    }
    deps.set(name, [...refs].filter((r) => byName.has(r)).sort());
  }
  const order: MathChannel[] = [];
  const state = new Map<string, number>();         // 1 = on the current path, 2 = done
  const visit = (name: string, path: string[]): void => {
    if (state.get(name) === 2) return;
    if (state.get(name) === 1) {
      const loop = [...path.slice(path.indexOf(name)), name];
      throw new MathError(`math channels refer to each other in a loop: ${loop.join(' -> ')}`);
    }
    state.set(name, 1);
    for (const dep of deps.get(name)!) visit(dep, [...path, name]);
    state.set(name, 2);
    order.push(byName.get(name)!);
  };
  for (const name of byName.keys()) visit(name, []);
  return order;
}
