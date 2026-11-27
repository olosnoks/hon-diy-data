/**
 * The user-editable address map: which RAM bytes mean what
 * (port of hondalog/addressmap.py, schema 1 and 2).
 *
 *     schema = 2
 *     ecu_variant = "P2P"                 which [variants] entry to load
 *     [variants.P2T]                      known ECUs; description, constants, protocol
 *     [constants]                         named numbers for expressions
 *     [[channel]]  rate, endian, source, notes
 *     [channel.evidence]                  why a channel is marked verified
 *     [channel.variants.P28]              per-ECU overrides of a channel
 *     [[math]]                            derived channels
 *     [[alarm]]                           alarm rules
 *     [[obd2]]                            OBD2-only channels (Mode 01 PID, no RAM address)
 *
 * Errors are MapError with the Python messages. TOML integers are read as
 * bigint so that int/float checks match Python's; values are plain numbers
 * once a channel is built.
 */

import { parse as parseToml } from 'smol-toml';
import { sha256 } from '@noble/hashes/sha256';
import { bytesToHex } from '@noble/hashes/utils';
import type {
  AddressMap as AddressMapContract, AlarmSpec, ChannelKind, ChannelSpec, ChannelStatus, MathSpec, RateClass,
} from '../types';
import { compileExpr, ExprError, references, type Expr as Compiled } from '../expr';
import { DEFAULT_FRAMING, withOverrides, type MapFraming } from './framing';
import { groupOf } from './groups';
import { defaultClass, greedyBlocks, planForMap, RATE_CLASSES, type Block, type CostModel } from './plan';
import { PyFloat, pyFixed, pyJsonDumps, pyRepr, pySorted, pyStr } from './pyfmt';

export const SCHEMA = 2;
export const RATES: readonly RateClass[] = RATE_CLASSES;
export const UNVERIFIED = 'unverified';
export const VERIFIED = 'verified';
export const SUSPECT = 'suspect';
export const STATUSES: readonly ChannelStatus[] = [UNVERIFIED, VERIFIED, SUSPECT];
const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

export class MapError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'MapError';
  }
}

type Table = Record<string, unknown>;

function isTable(v: unknown): v is Table {
  return v !== null && typeof v === 'object' && !Array.isArray(v) && !(v instanceof Date);
}

/** A Python int (TOML integers arrive as bigint). Booleans are not. */
function isInt(v: unknown): v is bigint {
  return typeof v === 'bigint';
}

/** A Python int or float, not a bool. */
function isNumber(v: unknown): v is bigint | number {
  return typeof v === 'bigint' || typeof v === 'number';
}

function num(v: unknown, fallback: number): number {
  if (v === undefined || v === null) return fallback;
  if (typeof v === 'bigint') return Number(v);
  if (typeof v === 'number') return v;
  if (typeof v === 'boolean') return v ? 1 : 0;
  return Number(v);
}

function bool(v: unknown, fallback: boolean): boolean {
  if (v === undefined || v === null) return fallback;
  if (typeof v === 'bigint') return v !== 0n;
  if (typeof v === 'number') return v !== 0;
  if (typeof v === 'string') return v.length > 0;
  return Boolean(v);
}

function str(v: unknown, fallback = ''): string {
  if (v === undefined) return fallback;
  return pyStr(v);
}

/** Python truthiness for `x or y`. */
function truthy(v: unknown): boolean {
  if (v === undefined || v === null || v === false || v === '' || v === 0 || v === 0n) return false;
  if (Array.isArray(v)) return v.length > 0;
  if (isTable(v)) return Object.keys(v).length > 0;
  return true;
}

/** Python's TypeError for Klass(**entry) with a bad keyword set. */
function checkKeywords(klass: string, entry: Table, fields: readonly string[], required: readonly string[]): string | null {
  for (const key of Object.keys(entry)) {
    if (!fields.includes(key)) return `${klass}.__init__() got an unexpected keyword argument ${pyRepr(key)}`;
  }
  const missing = required.filter((f) => !(f in entry));
  if (missing.length === 1) return `${klass}.__init__() missing 1 required positional argument: ${pyRepr(missing[0])}`;
  if (missing.length > 1) {
    const names = missing.map((m) => pyRepr(m));
    const list = names.length === 2 ? names.join(' and ') : `${names.slice(0, -1).join(', ')}, and ${names[names.length - 1]}`;
    return `${klass}.__init__() missing ${missing.length} required positional arguments: ${list}`;
  }
  return null;
}

function asEntries(v: unknown): Table[] {
  if (v === undefined) return [];
  if (!Array.isArray(v)) throw new TypeError(`argument after ** must be a mapping, not ${typeof v === 'string' ? 'str' : 'dict'}`);
  return v.map((e) => {
    if (!isTable(e)) throw new TypeError(`argument after ** must be a mapping, not ${typeof e === 'string' ? 'str' : typeof e}`);
    return e;
  });
}

// ---------------------------------------------------------------- formulas

/** Check a channel formula. Formulas see x (the bytes as one number) and b[i]. */
export function formulaExpr(text: string): Compiled {
  let expr: Compiled;
  try {
    expr = compileExpr(text, { names: ['x'], arrays: ['b'] });
  } catch (exc) {
    if (exc instanceof ExprError) throw new MapError(`bad formula ${pyRepr(text)}: ${exc.exprMessage}`);
    throw exc;
  }
  if (expr.stateful || expr.usesTime) {
    const used = pySorted(expr.functions);
    throw new MapError(`bad formula ${pyRepr(text)}: a formula decodes one reading, it cannot use `
      + `history (${used.join(', ')}); make a [[math]] channel for that`);
  }
  return expr;
}

/**
 * Compile a formula into f(x, b) -> number. Only arithmetic is allowed.
 * The function throws if the formula cannot be evaluated for that input.
 */
export function compileFormula(text: string): (x: number, b: ArrayLike<number>) => number {
  const expr = formulaExpr(text);
  return (x, b) => expr.evalStrict({ x, b });
}

// ---------------------------------------------------------------- evidence

export const EVIDENCE_FIELDS = ['date', 'method', 'result', 'summary', 'log', 'map_version', 'map_hash', 'reference'] as const;
export type Evidence = Record<(typeof EVIDENCE_FIELDS)[number], string>;

export function makeEvidence(fields: Partial<Evidence> = {}): Evidence {
  const out = {} as Evidence;
  for (const f of EVIDENCE_FIELDS) out[f] = fields[f] ?? '';
  return out;
}

/** Evidence.from_table */
export function evidenceFromTable(table: unknown, who: string): Evidence {
  if (!isTable(table)) throw new MapError(`${who}: evidence must be a table`);
  const extra = pySorted(Object.keys(table).filter((k) => !(EVIDENCE_FIELDS as readonly string[]).includes(k)));
  if (extra.length) throw new MapError(`${who}: unknown evidence field ${pyRepr(extra[0])}`);
  const out = makeEvidence();
  for (const [k, v] of Object.entries(table)) out[k as keyof Evidence] = pyStr(v);
  return out;
}

/** Evidence.to_table: only the fields that are set. */
export function evidenceToTable(ev: Evidence): Partial<Evidence> {
  const out: Partial<Evidence> = {};
  for (const f of EVIDENCE_FIELDS) if (ev[f]) out[f] = ev[f];
  return out;
}

// ---------------------------------------------------------------- channels

/** Channel fields a [channel.variants.X] table may override. */
export const VARIANT_KEYS = ['addr', 'length', 'formula', 'endian', 'label', 'units', 'min', 'max',
  'decimals', 'rate', 'status', 'evidence', 'source', 'notes', 'absent'] as const;

const CHANNEL_FIELDS = ['name', 'addr', 'length', 'formula', 'label', 'units', 'min', 'max', 'decimals',
  'kind', 'gauge', 'plot', 'status', 'rate', 'endian', 'source', 'notes', 'evidence', 'variants', '_expr'] as const;

function formatValue(value: number | null | undefined, decimals: number): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  return pyFixed(value, decimals);
}

/** One measured channel. Implements the contract's ChannelSpec. */
export class Channel implements ChannelSpec {
  name!: string;
  addr!: number;
  length!: number;
  formula!: string;
  label!: string;
  units!: string;
  min!: number;
  max!: number;
  decimals!: number;
  kind!: 'value' | 'flag';
  gauge!: boolean;
  plot!: boolean;
  /** Lower case; Python keeps the file's case but only compares lower case. */
  status!: ChannelStatus;
  rate!: RateClass;
  endian!: 'big' | 'little';
  source!: string;
  notes!: string;
  evidence!: Evidence | null;
  variants!: Record<string, Table>;
  group!: string;
  private expr!: Compiled;

  /**
   * Python's Channel(**entry): `entry` holds TOML values (ints as bigint).
   * Throws TypeError for unknown or missing keys, MapError for bad values.
   */
  constructor(entry: Table) {
    const bad = checkKeywords('Channel', entry, CHANNEL_FIELDS, ['name', 'addr']);
    if (bad) throw new TypeError(bad);
    const name = entry.name;
    if (typeof name !== 'string' || !NAME.test(name)) throw new MapError(`bad channel name ${pyRepr(name)}`);
    const who = name;
    const addr = entry.addr;
    if (!isInt(addr) || addr < 0n || addr > 0xffn) throw new MapError(`${who}: addr out of range`);
    const length = entry.length ?? 1n;
    if (!isInt(length) || length < 1n || length > 4n || addr + length > 0x100n) {
      throw new MapError(`${who}: length must be 1-4 and stay below 0x100`);
    }
    const kind = entry.kind ?? 'value';
    if (kind !== 'value' && kind !== 'flag') throw new MapError(`${who}: kind must be 'value' or 'flag'`);
    let rate = entry.rate ?? '';
    if (typeof rate !== 'string') throw new MapError(`${who}: rate must be one of ${RATES.join(', ')}`);
    rate = rate.trim().toLowerCase() || defaultClass(name);
    if (!(RATES as readonly string[]).includes(rate as string)) throw new MapError(`${who}: rate must be one of ${RATES.join(', ')}`);
    const endian = entry.endian ?? 'big';
    if (endian !== 'big' && endian !== 'little') throw new MapError(`${who}: endian must be 'big' or 'little'`);
    const status = entry.status ?? UNVERIFIED;
    if (typeof status !== 'string' || !(STATUSES as readonly string[]).includes(status.toLowerCase())) {
      throw new MapError(`${who}: status must be one of ${STATUSES.join(', ')}`);
    }
    const formula = entry.formula ?? 'x';
    if (typeof formula !== 'string') throw new MapError(`${who}: formula must be text`);
    let evidence: Evidence | null = null;
    if (entry.evidence !== undefined && entry.evidence !== null) evidence = evidenceFromTable(entry.evidence, who);
    const variants = entry.variants ?? {};
    if (!isTable(variants)) throw new MapError(`${who}: variants must be a table`);
    for (const [variant, table] of Object.entries(variants)) {
      if (!isTable(table)) throw new MapError(`${who}: variants.${variant} must be a table`);
      const extra = pySorted(Object.keys(table).filter((k) => !(VARIANT_KEYS as readonly string[]).includes(k)));
      if (extra.length) throw new MapError(`${who}: variants.${variant} cannot set ${pyRepr(extra[0])}`);
    }
    this.name = name;
    this.addr = Number(addr);
    this.length = Number(length);
    this.formula = formula;
    this.label = truthy(entry.label) ? str(entry.label) : name;
    this.units = str(entry.units);
    this.min = num(entry.min, 0);
    this.max = num(entry.max, 255);
    this.decimals = num(entry.decimals, 0);
    this.kind = kind;
    this.gauge = bool(entry.gauge, false);
    this.plot = bool(entry.plot, false);
    this.status = status.toLowerCase() as ChannelStatus;
    this.rate = rate as RateClass;
    this.endian = endian;
    this.source = str(entry.source);
    this.notes = str(entry.notes);
    this.evidence = evidence;
    this.variants = variants as Record<string, Table>;
    this.group = groupOf(name, kind);
    this.expr = formulaExpr(formula);
    if (kind === 'flag') {
      this.min = 0;
      this.max = 1;
      this.decimals = 0;
    }
  }

  get verified(): boolean {
    return this.status === VERIFIED;
  }

  /** The raw bytes as one unsigned number in this channel's byte order. */
  rawValue(raw: ArrayLike<number>): number {
    let x = 0;
    const n = raw.length;
    for (let i = 0; i < n; i++) {
      const byte = this.endian === 'big' ? raw[i]! : raw[n - 1 - i]!;
      x = x * 256 + (byte & 0xff);
    }
    return x;
  }

  /** Raw bytes -> engineering value. null if the formula blows up. */
  convert(raw: ArrayLike<number>): number | null {
    return this.expr.eval({ x: this.rawValue(raw), b: raw });
  }

  format(value: number | null | undefined): string {
    return formatValue(value, this.decimals);
  }

  /** The fields as Python's dataclass would pass them to replace(). */
  private asEntry(): Table {
    return {
      name: this.name, addr: BigInt(this.addr), length: BigInt(this.length), formula: this.formula,
      label: this.label, units: this.units, min: this.min, max: this.max, decimals: this.decimals,
      kind: this.kind, gauge: this.gauge, plot: this.plot, status: this.status, rate: this.rate,
      endian: this.endian, source: this.source, notes: this.notes,
      evidence: this.evidence ? { ...this.evidence } : null, variants: this.variants,
    };
  }

  /**
   * This channel as it applies to one ECU variant (null = not present). The
   * plain fields describe `baseVariant`. Another variant inherits address and
   * formula unless it overrides them, but never the verified state.
   */
  forVariant(variant: string, baseVariant: string): Channel | null {
    if (!variant || variant === baseVariant) return this;
    const table: Table = { ...(this.variants[variant] ?? {}) };
    const absent = table.absent;
    delete table.absent;
    if (truthy(absent)) return null;
    if (!('status' in table)) table.status = UNVERIFIED;
    if (!('evidence' in table)) table.evidence = null;
    try {
      return new Channel({ ...this.asEntry(), ...table });
    } catch (exc) {
      if (exc instanceof TypeError) throw new MapError(`${this.name}: variants.${variant}: ${exc.message}`);
      throw exc;
    }
  }
}

const OBD2_FIELDS = ['name', 'pid', 'label', 'units', 'min', 'max', 'decimals', 'kind', 'gauge', 'plot', 'rate', 'source', 'notes'] as const;

/**
 * A channel only standard OBD2 can fill: a Mode 01 PID the Honda protocol has
 * no RAM address for. Never read in Honda mode. Always counts as verified.
 */
export class Obd2Channel {
  name!: string;
  pid!: number;
  label!: string;
  units!: string;
  min!: number;
  max!: number;
  decimals!: number;
  kind!: 'value' | 'flag';
  gauge!: boolean;
  plot!: boolean;
  rate!: RateClass;
  source!: string;
  notes!: string;
  readonly verified = true;
  readonly status: ChannelStatus = VERIFIED;
  group!: string;

  constructor(entry: Table) {
    const bad = checkKeywords('Obd2Channel', entry, OBD2_FIELDS, ['name', 'pid']);
    if (bad) throw new TypeError(bad);
    const name = entry.name;
    if (typeof name !== 'string' || !NAME.test(name)) throw new MapError(`bad obd2 channel name ${pyRepr(name)}`);
    const pid = entry.pid;
    if (!isInt(pid) || !(pid > 0n && pid <= 0xffn)) throw new MapError(`${name}: pid must be a Mode 01 PID, 0x01-0xFF`);
    const kind = entry.kind ?? 'value';
    if (kind !== 'value' && kind !== 'flag') throw new MapError(`${name}: kind must be 'value' or 'flag'`);
    const rate = str(entry.rate, 'slow').trim().toLowerCase() || 'slow';
    if (!(RATES as readonly string[]).includes(rate)) throw new MapError(`${name}: rate must be one of ${RATES.join(', ')}`);
    this.name = name;
    this.pid = Number(pid);
    this.label = truthy(entry.label) ? str(entry.label) : name;
    this.units = str(entry.units);
    this.min = num(entry.min, 0);
    this.max = num(entry.max, 255);
    this.decimals = num(entry.decimals, 0);
    this.kind = kind;
    this.gauge = bool(entry.gauge, false);
    this.plot = bool(entry.plot, false);
    this.rate = rate as RateClass;
    this.source = str(entry.source, 'SAE J1979');
    this.notes = str(entry.notes);
    this.group = groupOf(name, kind);
    if (kind === 'flag') {
      this.min = 0;
      this.max = 1;
      this.decimals = 0;
    }
  }

  format(value: number | null | undefined): string {
    return formatValue(value, this.decimals);
  }
}

// ---------------------------------------------------------------- math channels

export class MathError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'MathError';
  }
}

const MATH_FIELDS = ['name', 'expr', 'label', 'units', 'min', 'max', 'decimals', 'gauge', 'plot', 'kind', 'description', 'enabled'] as const;

/** One derived channel (hondalog/mathch MathChannel). Implements MathSpec. */
export class MathChannel implements MathSpec {
  name!: string;
  expr!: string;
  label!: string;
  units!: string;
  min!: number;
  max!: number;
  decimals!: number;
  gauge!: boolean;
  plot!: boolean;
  kind!: 'value' | 'flag';
  description!: string;
  enabled!: boolean;
  group!: string;

  constructor(entry: Table) {
    const bad = checkKeywords('MathChannel', entry, MATH_FIELDS, ['name', 'expr']);
    if (bad) throw new TypeError(bad);
    const name = entry.name;
    if (typeof name !== 'string' || !NAME.test(name)) throw new MathError(`bad math channel name ${pyRepr(name)}`);
    const expr = entry.expr;
    if (typeof expr !== 'string' || !expr.trim()) throw new MathError(`${name}: no expression`);
    const kind = entry.kind ?? 'value';
    if (kind !== 'value' && kind !== 'flag') throw new MathError(`${name}: kind must be 'value' or 'flag'`);
    this.name = name;
    this.expr = expr;
    this.label = truthy(entry.label) ? str(entry.label) : name;
    this.units = str(entry.units);
    this.min = num(entry.min, 0);
    this.max = num(entry.max, 100);
    this.decimals = num(entry.decimals, 1);
    this.gauge = bool(entry.gauge, false);
    this.plot = bool(entry.plot, false);
    this.kind = kind;
    this.description = str(entry.description);
    this.enabled = bool(entry.enabled, true);
    this.group = groupOf(name, kind);
    if (kind === 'flag') {
      this.min = 0;
      this.max = 1;
      this.decimals = 0;
    }
  }

  format(value: number | null | undefined): string {
    return formatValue(value, this.decimals);
  }
}

export interface MathCheck {
  /** Enabled math channels in dependency order. */
  order: MathChannel[];
  /** Measured channels each math channel is built from, through other math channels. */
  baseInputs: Map<string, Set<string>>;
  compiled: Map<string, Compiled>;
}

/**
 * The checks MathEngine's constructor makes: duplicates, name clashes,
 * loops, unknown names. Evaluating math channels is agent #3's engine.
 */
export function checkMath(channels: readonly MathChannel[], inputs: Iterable<string>, constants: Record<string, number>): MathCheck {
  const inputSet = new Set(inputs);
  const byName = new Map<string, MathChannel>();
  for (const ch of channels.filter((c) => c.enabled)) {
    if (byName.has(ch.name)) throw new MathError(`duplicate math channel ${pyRepr(ch.name)}`);
    if (inputSet.has(ch.name)) throw new MathError(`math channel ${pyRepr(ch.name)} has the same name as a measured channel`);
    if (Object.prototype.hasOwnProperty.call(constants, ch.name)) {
      throw new MathError(`math channel ${pyRepr(ch.name)} has the same name as a constant`);
    }
    byName.set(ch.name, ch);
  }
  // topological order; a loop is an error naming it
  const deps = new Map<string, string[]>();
  for (const [name, ch] of byName) {
    let refs: Set<string>;
    try {
      refs = references(ch.expr);
    } catch (exc) {
      if (exc instanceof ExprError) throw new MathError(`math channel ${pyRepr(name)}: ${exc.message}`);
      throw exc;
    }
    deps.set(name, pySorted([...refs].filter((r) => byName.has(r))));
  }
  const order: MathChannel[] = [];
  const state = new Map<string, number>();
  const visit = (name: string, path: string[]): void => {
    if (state.get(name) === 2) return;
    if (state.get(name) === 1) {
      const loop = [...path.slice(path.indexOf(name)), name];
      throw new MathError('math channels refer to each other in a loop: ' + loop.join(' -> '));
    }
    state.set(name, 1);
    for (const dep of deps.get(name)!) visit(dep, [...path, name]);
    state.set(name, 2);
    order.push(byName.get(name)!);
  };
  for (const name of byName.keys()) visit(name, []);

  const known = new Set([...inputSet, ...byName.keys()]);
  const baseInputs = new Map<string, Set<string>>();
  const compiled = new Map<string, Compiled>();
  for (const ch of order) {
    let expr: Compiled;
    try {
      expr = compileExpr(ch.expr, { names: known, constants });
    } catch (exc) {
      if (exc instanceof ExprError) throw new MathError(`math channel ${pyRepr(ch.name)}: ${exc.message}`);
      throw exc;
    }
    const base = new Set<string>();
    for (const dep of expr.deps) for (const n of baseInputs.get(dep) ?? [dep]) base.add(n);
    baseInputs.set(ch.name, base);
    compiled.set(ch.name, expr);
  }
  return { order, baseInputs, compiled };
}

// ---------------------------------------------------------------- alarms

export class AlarmError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AlarmError';
  }
}

export const ALARM_ACTIONS = ['tile', 'banner', 'beep', 'marker', 'capture'] as const;
const DEFAULT_PRIORITY: Record<string, number> = { warn: 50, critical: 100 };
const ALARM_FIELDS = ['name', 'channel', 'low', 'high', 'rate_low', 'rate_high', 'rate_window_ms', 'when',
  'clear_when', 'gate', 'hold_s', 'clear_hold_s', 'hysteresis', 'severity', 'priority', 'latching',
  'actions', 'message', 'label', 'enabled'] as const;

/**
 * One alarm rule (hondalog/alarms AlarmRule), field names as in Python.
 * Also fills the contract's AlarmSpec: level ('alarm' for critical) and
 * rearm_when (clear_when). `when` keeps the Python meaning: "" for a rule
 * that only has low/high/rate limits.
 */
export class AlarmRule implements AlarmSpec {
  name!: string;
  channel!: string;
  low!: number | null;
  high!: number | null;
  rate_low!: number | null;
  rate_high!: number | null;
  rate_window_ms!: number;
  when!: string;
  clear_when!: string;
  gate!: string;
  hold_s!: number;
  clear_hold_s!: number;
  hysteresis!: number;
  severity!: 'warn' | 'critical';
  priority!: number;
  latching!: boolean;
  actions!: string[];
  message!: string;
  label!: string;
  enabled!: boolean;

  constructor(entry: Table) {
    const bad = checkKeywords('AlarmRule', entry, ALARM_FIELDS, ['name']);
    if (bad) throw new TypeError(bad);
    const name = entry.name;
    if (typeof name !== 'string' || !NAME.test(name)) throw new AlarmError(`bad alarm name ${pyRepr(name)}`);
    const who = `alarm ${pyRepr(name)}`;
    const severity = entry.severity ?? 'warn';
    if (severity !== 'warn' && severity !== 'critical') throw new AlarmError(`${who}: severity must be 'warn' or 'critical'`);
    const actions = (entry.actions as unknown[] | undefined) ?? ['tile', 'banner'];
    const badAction = actions.find((a) => !(ALARM_ACTIONS as readonly unknown[]).includes(a));
    if (badAction !== undefined) throw new AlarmError(`${who}: unknown action ${pyRepr(badAction)} (use ${ALARM_ACTIONS.join(', ')})`);
    const limitKeys = ['low', 'high', 'rate_low', 'rate_high'];
    for (const key of [...limitKeys, 'hold_s', 'clear_hold_s', 'hysteresis', 'rate_window_ms']) {
      const v = entry[key];
      if (v !== undefined && v !== null && !isNumber(v)) throw new AlarmError(`${who}: limits and times must be numbers`);
    }
    const opt = (k: string): number | null => (entry[k] === undefined || entry[k] === null ? null : num(entry[k], 0));
    this.name = name;
    this.channel = str(entry.channel);
    this.low = opt('low');
    this.high = opt('high');
    this.rate_low = opt('rate_low');
    this.rate_high = opt('rate_high');
    this.rate_window_ms = num(entry.rate_window_ms, 500);
    this.when = str(entry.when);
    this.clear_when = str(entry.clear_when);
    this.gate = str(entry.gate);
    this.hold_s = num(entry.hold_s, 0);
    this.clear_hold_s = num(entry.clear_hold_s, 0);
    this.hysteresis = num(entry.hysteresis, 0);
    this.severity = severity;
    this.priority = entry.priority === undefined || entry.priority === null ? DEFAULT_PRIORITY[severity]! : num(entry.priority, 0);
    this.latching = bool(entry.latching, false);
    this.actions = actions.map((a) => String(a));
    this.message = str(entry.message);
    this.label = truthy(entry.label) ? str(entry.label) : name.replace(/_/g, ' ');
    this.enabled = bool(entry.enabled, true);
    const limits = [this.low, this.high, this.rate_low, this.rate_high];
    if (limits.some((v) => v !== null) && !this.channel) throw new AlarmError(`${who}: low/high/rate limits need a 'channel'`);
    if (limits.every((v) => v === null) && !this.when.trim()) {
      throw new AlarmError(`${who}: needs a limit (low, high, rate_low, rate_high) or 'when'`);
    }
    if (this.low !== null && this.high !== null && this.low >= this.high) throw new AlarmError(`${who}: low must be below high`);
    if (this.hold_s < 0 || this.clear_hold_s < 0 || this.hysteresis < 0) {
      throw new AlarmError(`${who}: hold times and hysteresis cannot be negative`);
    }
    if (this.rate_window_ms <= 0) throw new AlarmError(`${who}: rate_window_ms must be more than 0`);
    if (this.clear_when.trim() && !this.when.trim()) throw new AlarmError(`${who}: 'clear_when' only makes sense with 'when'`);
  }

  get level(): 'warn' | 'alarm' {
    return this.severity === 'critical' ? 'alarm' : 'warn';
  }

  get rearm_when(): string | undefined {
    return this.clear_when || undefined;
  }

  get usesRate(): boolean {
    return this.rate_low !== null || this.rate_high !== null;
  }
}

function makeAlarm(entry: Table): AlarmRule {
  const table = { ...entry };
  if ('actions' in table && !Array.isArray(table.actions)) {
    throw new AlarmError(`alarm ${pyRepr(table.name)}: actions must be a list`);
  }
  return new AlarmRule(table);
}

/** The checks AlarmEngine's constructor makes. */
export function checkAlarms(rules: readonly AlarmRule[], names: Iterable<string>, constants: Record<string, number>): void {
  const known = new Set(names);
  const seen = new Set<string>();
  for (const rule of rules) {
    if (!rule.enabled) continue;
    if (seen.has(rule.name)) throw new AlarmError(`duplicate alarm ${pyRepr(rule.name)}`);
    seen.add(rule.name);
    if (rule.channel && !known.has(rule.channel)) {
      throw new AlarmError(`alarm ${pyRepr(rule.name)}: unknown channel ${pyRepr(rule.channel)}`);
    }
    for (const attr of ['when', 'clear_when', 'gate'] as const) {
      const text = rule[attr].trim();
      if (!text) continue;
      try {
        compileExpr(text, { names: known, constants });
      } catch (exc) {
        if (exc instanceof ExprError) throw new AlarmError(`alarm ${pyRepr(rule.name)}, ${attr}: ${exc.message}`);
        throw exc;
      }
    }
  }
}

// ---------------------------------------------------------------- the map

/** Memory read so far: address -> byte. Missing, null or negative = not read. */
export type Memory = Map<number, number> | ArrayLike<number | null | undefined> | Record<number, number>;

function memGet(mem: Memory, addr: number): number | undefined {
  const v = mem instanceof Map ? mem.get(addr) : (mem as Record<number, number | null | undefined>)[addr];
  return v === null || v === undefined || v < 0 ? undefined : v;
}

export interface DecodeSpec {
  variant: string;
  channels: [string, number, number, string, string, string, string][];
  constants: [string, PyFloat][];
  math: [string, string, string][];
  obd2?: [string, number, string, string][];
}

export interface AddressMapInit {
  version: string;
  ecu: string;
  channels: Channel[];
  framing?: MapFraming;
  ecuIdAddr?: number | null;
  ecuIdLen?: number;
  path?: string;
  schema?: number;
  variant?: string;
  baseVariant?: string;
  variants?: Record<string, Table>;
  constants?: Record<string, number>;
  math?: MathChannel[];
  alarms?: AlarmRule[];
  baseChannels?: Channel[];
  obd2?: Obd2Channel[];
  text?: string;
}

/** A loaded address map. Implements the contract's AddressMap. */
export class AddressMap implements AddressMapContract {
  version: string;
  ecu: string;
  channels: Channel[];
  framing: MapFraming;
  ecuIdAddr: number | null;
  ecuIdLen: number;
  path: string;
  schema: number;
  variant: string;
  baseVariant: string;
  variants: Record<string, Table>;
  constants: Record<string, number>;
  math: MathChannel[];
  alarms: AlarmRule[];
  baseChannels: Channel[];
  obd2: Obd2Channel[];
  text: string;
  versionHash: string;

  constructor(init: AddressMapInit) {
    this.version = init.version;
    this.ecu = init.ecu;
    this.channels = init.channels;
    this.framing = init.framing ?? { ...DEFAULT_FRAMING };
    this.ecuIdAddr = init.ecuIdAddr === undefined ? 0x76 : init.ecuIdAddr;
    this.ecuIdLen = init.ecuIdLen ?? 10;
    this.path = init.path ?? '';
    this.schema = init.schema ?? 1;
    this.variant = init.variant ?? '';
    this.baseVariant = init.baseVariant ?? '';
    this.variants = init.variants ?? {};
    this.constants = init.constants ?? {};
    this.math = init.math ?? [];
    this.alarms = init.alarms ?? [];
    this.baseChannels = init.baseChannels?.length ? init.baseChannels : [...this.channels];
    this.obd2 = init.obd2 ?? [];
    this.text = init.text ?? '';
    this.versionHash = this.computeHash();
  }

  // -- contract views --

  /** The resolved framing (the contract calls it protocol). */
  get protocol(): MapFraming {
    return this.framing;
  }

  get ecuId(): { addr: number | null; length: number } {
    return { addr: this.ecuIdAddr, length: this.ecuIdLen };
  }

  // -- lookups --

  get names(): string[] {
    return this.channels.map((c) => c.name);
  }

  /** OBD2-only channels (no RAM address): filled in OBD2-only live mode. */
  get obd2Names(): string[] {
    return this.obd2.map((c) => c.name);
  }

  get mathNames(): string[] {
    return this.math.filter((m) => m.enabled).map((m) => m.name);
  }

  /** Measured channels, then math channels. */
  get allNames(): string[] {
    return [...this.names, ...this.mathNames];
  }

  channel(name: string): Channel | undefined {
    return this.channels.find((c) => c.name === name);
  }

  obd2Channel(name: string): Obd2Channel | undefined {
    return this.obd2.find((c) => c.name === name);
  }

  mathChannel(name: string): MathChannel | undefined {
    return this.math.find((m) => m.name === name);
  }

  /** Greedy uniform-rate reads (addressmap.AddressMap.blocks). */
  blocks(): [number, number][] {
    return greedyBlocks(this.channels, this.framing.maxReadLen);
  }

  /** The poller's cost-model plan: blocks at their rate class targets. */
  plan(cost?: CostModel): Block[] {
    return planForMap(this.channels, this.framing, cost);
  }

  /** mem maps address -> byte for everything read this sweep. */
  decode(mem: Memory): Record<string, number | null> {
    const out: Record<string, number | null> = {};
    for (const ch of this.channels) {
      const raw: number[] = [];
      let missing = false;
      for (let a = ch.addr; a < ch.addr + ch.length; a++) {
        const v = memGet(mem, a);
        if (v === undefined) {
          missing = true;
          break;
        }
        raw.push(v);
      }
      out[ch.name] = missing ? null : ch.convert(raw);
    }
    return out;
  }

  // -- schema 2 helpers --

  /** Base unit text for every measured, OBD2 and math channel. */
  units(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const c of this.channels) out[c.name] = c.units;
    for (const c of this.obd2) out[c.name] = c.units;
    for (const m of this.math) out[m.name] = m.units;
    return out;
  }

  /** Channels grouped by poll rate class. */
  byRate(): Record<RateClass, Channel[]> {
    const out = { fast: [], medium: [], slow: [] } as Record<RateClass, Channel[]>;
    for (const rate of RATES) out[rate] = this.channels.filter((c) => c.rate === rate);
    return out;
  }

  /** The load-time math check (names, loops) with base inputs per math channel. */
  mathCheck(): MathCheck {
    return checkMath(this.math, [...this.names, ...this.obd2Names], this.constants);
  }

  /** Measured channels a math channel is built from (empty for unknown names). */
  mathBaseInputs(name: string): Set<string> {
    return this.mathCheck().baseInputs.get(name) ?? new Set();
  }

  /**
   * A measured channel's own state; a math channel is verified when every
   * measured channel it is built from is.
   */
  isVerified(name: string): boolean {
    const ch = this.channel(name);
    if (ch) return ch.verified;
    const inputs = this.mathCheck().baseInputs.get(name);
    if (!inputs) return false;
    return [...inputs].every((n) => this.channel(n)?.verified === true);
  }

  /** verified per channel name, math channels from their inputs (AlarmEngine's view). */
  verifiedMap(): Record<string, boolean> {
    const out: Record<string, boolean> = {};
    for (const c of this.channels) out[c.name] = c.verified;
    const check = this.mathCheck();
    for (const m of check.order) out[m.name] = [...check.baseInputs.get(m.name)!].every((n) => out[n] ?? false);
    return out;
  }

  /** Everything that decides what number a log row holds, as plain data. */
  decodeSpec(): DecodeSpec {
    const spec: DecodeSpec = {
      variant: this.variant,
      channels: this.channels.map((c) => [c.name, c.addr, c.length, c.endian, c.formula, c.units, c.kind]),
      constants: pySorted(Object.keys(this.constants)).map((k) => [k, new PyFloat(this.constants[k]!)]),
      math: this.math.filter((m) => m.enabled).map((m) => [m.name, m.expr, m.units]),
    };
    if (this.obd2.length) spec.obd2 = this.obd2.map((c) => [c.name, c.pid, c.units, c.kind]);
    return spec;
  }

  /** The exact text Python hashes (json.dumps, sort_keys, compact, ensure_ascii=False). */
  decodeSpecJson(): string {
    return pyJsonDumps(this.decodeSpec());
  }

  /**
   * 12 hex digits that change whenever decoding changes: addresses, lengths,
   * byte order, formulas, units, constants, math expressions. Labels, ranges,
   * alarm rules and verified marks are left out.
   */
  private computeHash(): string {
    return bytesToHex(sha256(new TextEncoder().encode(this.decodeSpecJson()))).slice(0, 12);
  }

  /** Version text plus hash, for log headers: "p2p-guess-2 #1a2b3c4d5e6f". */
  get versionTag(): string {
    return `${this.version} #${this.versionHash}`;
  }

  /** Every measured channel as a plain ChannelSpec (for stores and workers). */
  channelSpecs(): ChannelSpec[] {
    return this.channels.map((c) => ({
      name: c.name, label: c.label, addr: c.addr, length: c.length, formula: c.formula, units: c.units,
      min: c.min, max: c.max, decimals: c.decimals, kind: c.kind as ChannelKind, status: c.status,
      rate: c.rate, endian: c.endian, source: c.source, notes: c.notes, group: c.group,
    }));
  }
}

// ---------------------------------------------------------------- parse

const TOP_KEYS = ['schema', 'version', 'ecu', 'ecu_variant', 'base_variant', 'variants', 'ecu_id',
  'protocol', 'constants', 'channel', 'math', 'alarm', 'obd2'];

function parseConstants(table: unknown, where: string): Record<string, number> {
  if (!isTable(table)) throw new MapError(`${where} must be a table of name = number`);
  const out: Record<string, number> = {};
  for (const [key, value] of Object.entries(table)) {
    if (!NAME.test(key)) throw new MapError(`${where}: bad name ${pyRepr(key)}`);
    if (!isNumber(value)) throw new MapError(`${where}: ${key} must be a number`);
    out[key] = Number(value);
  }
  return out;
}

function get(doc: Table, key: string, fallback: unknown): unknown {
  return Object.prototype.hasOwnProperty.call(doc, key) ? doc[key] : fallback;
}

/** Build an AddressMap from a parsed TOML document (ints as bigint). Throws MapError. */
export function parseDoc(doc: Table, path = '', variant?: string | null, text = ''): AddressMap {
  const where = path || 'address map';
  const schema = get(doc, 'schema', 1n);
  if (!isInt(schema) || schema < 1n) throw new MapError(`${where}: schema must be a whole number`);
  if (schema > BigInt(SCHEMA)) {
    throw new MapError(`${where}: written for map schema ${schema}, this hondalog reads up to `
      + `${SCHEMA}. Update hondalog or use an older map.`);
  }
  if (schema >= 2n) {
    const extra = pySorted(Object.keys(doc).filter((k) => !TOP_KEYS.includes(k)));
    if (extra.length) {
      throw new MapError(`${where}: unknown section or key ${pyRepr(extra[0])} (known: ${pySorted(TOP_KEYS).join(', ')})`);
    }
  }
  let base: Channel[];
  try {
    base = asEntries(doc.channel).map((e) => new Channel(e));
  } catch (exc) {
    if (exc instanceof TypeError) throw new MapError(`${where}: ${exc.message}`);
    throw exc;
  }
  if (!base.length) throw new MapError(`${where}: no [[channel]] entries`);
  const names = base.map((c) => c.name);
  const dupes = new Set(names.filter((n, i) => names.indexOf(n) !== i));
  if (dupes.size) throw new MapError(`${where}: duplicate channel names ${pyRepr(pySorted(dupes))}`);

  const variants = get(doc, 'variants', {});
  if (!isTable(variants) || !Object.values(variants).every(isTable)) {
    throw new MapError(`${where}: [variants] must hold one table per ECU`);
  }
  const variantNames = Object.keys(variants);
  const baseVariant = str(truthy(doc.base_variant) ? doc.base_variant : (variantNames[0] ?? ''));
  const active = str(truthy(variant) ? variant : truthy(doc.ecu_variant) ? doc.ecu_variant : baseVariant);
  const used = new Set(base.flatMap((c) => Object.keys(c.variants)));
  if (variantNames.length) {
    const unknown = pySorted([...new Set([...used, active, baseVariant])].filter((v) => !variantNames.includes(v)));
    if (unknown.length) {
      throw new MapError(`${where}: ECU variant ${pyRepr(unknown[0])} is not listed under [variants] (known: ${variantNames.join(', ')})`);
    }
  } else if (active && !used.has(active) && active !== baseVariant) {
    throw new MapError(`${where}: no ECU variant ${pyRepr(active)} in this map`);
  }
  const info: Table = active !== baseVariant ? { ...((variants as Record<string, Table>)[active] ?? {}) } : {};
  const channels = base.map((ch) => ch.forVariant(active, baseVariant)).filter((c): c is Channel => c !== null);
  if (!channels.length) throw new MapError(`${where}: variant ${pyRepr(active)} has no channels`);

  let framing: MapFraming;
  try {
    framing = withOverrides(DEFAULT_FRAMING, get(doc, 'protocol', {}));
    framing = withOverrides(framing, get(info, 'protocol', {}));
  } catch (exc) {
    throw new MapError(`${where}: [protocol] ${(exc as Error).message}`);
  }
  const constants = parseConstants(get(doc, 'constants', {}), `${where}: [constants]`);
  Object.assign(constants, parseConstants(get(info, 'constants', {}), `${where}: [variants.${active}.constants]`));
  let clash = pySorted(Object.keys(constants).filter((k) => names.includes(k)));
  if (clash.length) throw new MapError(`${where}: constant ${pyRepr(clash[0])} has the same name as a channel`);

  let obd2: Obd2Channel[];
  try {
    obd2 = asEntries(doc.obd2).map((e) => new Obd2Channel(e));
  } catch (exc) {
    if (exc instanceof TypeError) throw new MapError(`${where}: [[obd2]] ${exc.message}`);
    throw exc;
  }
  clash = pySorted(obd2.map((c) => c.name).filter((n) => names.includes(n)));
  if (clash.length) throw new MapError(`${where}: obd2 channel ${pyRepr(clash[0])} has the same name as a channel`);
  let math: MathChannel[];
  let alarms: AlarmRule[];
  try {
    math = asEntries(doc.math).map((e) => new MathChannel(e));
    alarms = asEntries(doc.alarm).map((e) => makeAlarm(e));
  } catch (exc) {
    if (exc instanceof TypeError || exc instanceof MathError || exc instanceof AlarmError) {
      throw new MapError(`${where}: ${exc.message}`);
    }
    throw exc;
  }
  const ecuId = get(doc, 'ecu_id', {});
  const ecuTable: Table = isTable(ecuId) ? ecuId : {};
  const ecuAddr = ecuTable.addr;
  const amap = new AddressMap({
    version: str(get(doc, 'version', 'unversioned')),
    ecu: str(truthy(info.description) ? info.description : get(doc, 'ecu', '')),
    channels,
    framing,
    ecuIdAddr: ecuAddr === undefined || ecuAddr === null ? null : Number(ecuAddr),
    ecuIdLen: Math.trunc(num(get(ecuTable, 'length', 10n), 10)),
    path,
    schema: Number(schema),
    variant: active,
    baseVariant,
    variants: Object.fromEntries(Object.entries(variants).map(([k, v]) => [k, { ...(v as Table) }])),
    constants,
    math,
    alarms,
    baseChannels: base,
    obd2,
    text,
  });
  try {               // check everything now so a bad map fails at load
    amap.mathCheck();
    checkAlarms(alarms, amap.allNames, constants);
  } catch (exc) {
    if (exc instanceof MathError || exc instanceof AlarmError) throw new MapError(`${where}: ${exc.message}`);
    throw exc;
  }
  return amap;
}

/** Parse TOML text the way the map loader wants it (ints as bigint). */
export function parseTomlText(text: string, path = ''): Table {
  try {
    return parseToml(text, { integersAsBigInt: true }) as unknown as Table;
  } catch (exc) {
    throw new MapError(`${path || 'address map'}: ${(exc as Error).message}`);
  }
}

/**
 * Load a map from TOML text. `variant` picks an ECU variant other than the
 * file's own ecu_variant. The text is kept on the map (stored with drives).
 */
export function loads(text: string, variant?: string | null, path = ''): AddressMap {
  return parseDoc(parseTomlText(text, path), path, variant, text);
}
