/**
 * The address map as the handheld compiler needs it, resolved from the map's TOML
 * text the way hondalog/addressmap.py ``parse`` does: the active ECU variant's
 * channels, the [protocol] framing with every field (request_len, retries, rates ...),
 * constants, math channels in the math engine's order with their base inputs, alarm
 * rules with their defaults, OBD2-only names, and the version hash.
 *
 * The shared ``AddressMap`` contract (src/core/types.ts) is thinner than this; its
 * ``text`` and ``variant`` are enough to rebuild it here (``fromContract``). The
 * version hash computed here is checked against the contract's in the tests.
 */

import { parse as parseToml } from 'smol-toml';
import { sha256 } from '@noble/hashes/sha256';
import type { AddressMap } from '../types';
import { hex, utf8 } from './bytes';
import { parse as parseExpr, references, walk } from './expr';

export class MapError extends Error {}

export type Rate = 'fast' | 'medium' | 'slow';
export const RATES: Rate[] = ['fast', 'medium', 'slow'];

/** link/scheduler.py DEFAULT_CLASS */
export const DEFAULT_CLASS: Record<string, Rate> = {
  rpm: 'fast', map: 'fast', tps: 'fast', inj: 'fast', ign: 'fast',
  o2: 'medium', stft: 'medium', ltft: 'medium', vss: 'medium',
  ect: 'slow', iat: 'slow', batt: 'slow', baro: 'slow',
};

export interface HhChannel {
  name: string; addr: number; length: number; formula: string; label: string; units: string;
  min: number; max: number; decimals: number; kind: 'value' | 'flag'; status: string; rate: Rate;
  endian: 'big' | 'little'; verified: boolean;
}

export interface HhFraming {
  baud: number; cmdRead: number; requestLen: number; replyHeader: number; replyLenExtra: number;
  maxReadLen: number; initSequence: Uint8Array; initDelayS: number; replyTimeoutS: number;
  interRequestDelayS: number; adaptiveTiming: boolean; minReplyTimeoutS: number; interByteTimeoutS: number;
  maxInterRequestDelayS: number; retries: number; turnaroundS: number; rateFastHz: number;
  rateMediumHz: number; rateSlowHz: number;
}

export interface HhMath {
  name: string; expr: string; label: string; units: string; min: number; max: number; decimals: number;
  kind: 'value' | 'flag'; enabled: boolean;
}

export interface HhAlarm {
  name: string; channel: string; low: number | null; high: number | null; rateLow: number | null;
  rateHigh: number | null; rateWindowMs: number; when: string; clearWhen: string; gate: string; holdS: number;
  clearHoldS: number; hysteresis: number; severity: 'warn' | 'critical'; priority: number; latching: boolean;
  actions: string[]; message: string; label: string; enabled: boolean;
}

export interface HhObd2 { name: string; pid: number; units: string; kind: 'value' | 'flag' }

export interface HhMap {
  version: string;
  ecu: string;
  variant: string;
  baseVariant: string;
  variants: string[];
  channels: HhChannel[];
  framing: HhFraming;
  ecuIdAddr: number | null;
  ecuIdLen: number;
  constants: Map<string, number>;
  math: HhMath[];
  /** Enabled math channels, inputs first (MathEngine.names). */
  mathOrder: string[];
  /** Measured (and OBD2-only) channels each math channel is built from. */
  baseInputs: Map<string, Set<string>>;
  alarms: HhAlarm[];
  obd2: HhObd2[];
  versionHash: string;
  text: string;
}

export const DEFAULT_FRAMING: HhFraming = {
  baud: 9600, cmdRead: 0x20, requestLen: 5, replyHeader: 0x00, replyLenExtra: 3, maxReadLen: 16,
  initSequence: Uint8Array.of(0x68, 0x6a, 0xf5, 0xaf, 0xbf, 0xb3, 0xb2, 0xc1, 0xdb, 0xb3, 0xe9),
  initDelayS: 0.3, replyTimeoutS: 0.2, interRequestDelayS: 0.002, adaptiveTiming: true, minReplyTimeoutS: 0.02,
  interByteTimeoutS: 0.05, maxInterRequestDelayS: 0.05, retries: 1, turnaroundS: 0.002, rateFastHz: 20.0,
  rateMediumHz: 5.0, rateSlowHz: 1.0,
};

const FRAMING_KEYS: Record<string, keyof HhFraming> = {
  baud: 'baud', cmd_read: 'cmdRead', request_len: 'requestLen', reply_header: 'replyHeader',
  reply_len_extra: 'replyLenExtra', max_read_len: 'maxReadLen', init_sequence: 'initSequence',
  init_delay_s: 'initDelayS', reply_timeout_s: 'replyTimeoutS', inter_request_delay_s: 'interRequestDelayS',
  adaptive_timing: 'adaptiveTiming', min_reply_timeout_s: 'minReplyTimeoutS',
  inter_byte_timeout_s: 'interByteTimeoutS', max_inter_request_delay_s: 'maxInterRequestDelayS',
  retries: 'retries', turnaround_s: 'turnaroundS', rate_fast_hz: 'rateFastHz', rate_medium_hz: 'rateMediumHz',
  rate_slow_hz: 'rateSlowHz',
};

const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
const VARIANT_KEYS = new Set(['addr', 'length', 'formula', 'endian', 'label', 'units', 'min', 'max', 'decimals',
  'rate', 'status', 'evidence', 'source', 'notes', 'absent']);

/** protocol.parse_hex: '20 05 00 10', '20,05,0x00,10' or '20050010'. */
export function parseHex(text: string): Uint8Array {
  const cleaned = text.replace(/,/g, ' ').replace(/0x/g, ' ').replace(/0X/g, ' ');
  const out: number[] = [];
  for (let token of cleaned.split(/\s+/).filter(Boolean)) {
    if (token.length % 2) token = '0' + token;
    if (!/^[0-9a-fA-F]*$/.test(token)) throw new MapError(`bad hex ${token}`);
    for (let i = 0; i < token.length; i += 2) out.push(parseInt(token.slice(i, i + 2), 16));
  }
  return Uint8Array.from(out);
}

type Table = Record<string, any>;

function isTable(v: unknown): v is Table {
  return typeof v === 'object' && v !== null && !Array.isArray(v) && !(v instanceof Date);
}

function str(v: unknown, dflt = ''): string {
  return v === undefined || v === null ? dflt : String(v);
}

function makeChannel(entry: Table): HhChannel {
  const name = entry.name;
  if (typeof name !== 'string' || !NAME.test(name)) throw new MapError(`bad channel name ${JSON.stringify(name)}`);
  const addr = entry.addr;
  if (typeof addr !== 'number' || !Number.isInteger(addr) || addr < 0 || addr > 0xff) throw new MapError(`${name}: addr out of range`);
  const length = entry.length ?? 1;
  if (!Number.isInteger(length) || length < 1 || length > 4 || addr + length > 0x100) {
    throw new MapError(`${name}: length must be 1-4 and stay below 0x100`);
  }
  const kind = entry.kind ?? 'value';
  if (kind !== 'value' && kind !== 'flag') throw new MapError(`${name}: kind must be 'value' or 'flag'`);
  const rate = (str(entry.rate).trim().toLowerCase() || DEFAULT_CLASS[name] || 'slow') as Rate;
  if (!RATES.includes(rate)) throw new MapError(`${name}: rate must be one of ${RATES.join(', ')}`);
  const endian = entry.endian ?? 'big';
  if (endian !== 'big' && endian !== 'little') throw new MapError(`${name}: endian must be 'big' or 'little'`);
  const status = str(entry.status, 'unverified');
  if (!['unverified', 'verified', 'suspect'].includes(status.toLowerCase())) {
    throw new MapError(`${name}: status must be one of unverified, verified, suspect`);
  }
  const formula = entry.formula ?? 'x';
  if (typeof formula !== 'string') throw new MapError(`${name}: formula must be text`);
  if (entry.variants !== undefined) {
    if (!isTable(entry.variants)) throw new MapError(`${name}: variants must be a table`);
    for (const [v, t] of Object.entries(entry.variants)) {
      if (!isTable(t)) throw new MapError(`${name}: variants.${v} must be a table`);
      const extra = Object.keys(t).filter((k) => !VARIANT_KEYS.has(k)).sort();
      if (extra.length) throw new MapError(`${name}: variants.${v} cannot set '${extra[0]}'`);
    }
  }
  parseExpr(formula);
  const ch: HhChannel = {
    name, addr, length, formula, label: str(entry.label) || name, units: str(entry.units),
    min: Number(entry.min ?? 0), max: Number(entry.max ?? 255), decimals: Number(entry.decimals ?? 0), kind,
    status, rate, endian, verified: status.toLowerCase() === 'verified',
  };
  if (kind === 'flag') {
    ch.min = 0;
    ch.max = 1;
    ch.decimals = 0;
  }
  return ch;
}

function forVariant(entry: Table, base: HhChannel, variant: string, baseVariant: string): HhChannel | null {
  if (!variant || variant === baseVariant) return base;
  const table: Table = { ...((entry.variants ?? {})[variant] ?? {}) };
  if (table.absent) return null;
  delete table.absent;
  if (!('status' in table)) table.status = 'unverified';
  delete table.evidence;
  // dataclasses.replace: the fields as they are after __post_init__, with the overrides on top
  return makeChannel({
    name: base.name, addr: base.addr, length: base.length, formula: base.formula, label: base.label,
    units: base.units, min: base.min, max: base.max, decimals: base.decimals, kind: base.kind, status: base.status,
    rate: base.rate, endian: base.endian, ...table,
  });
}

function framingWith(f: HhFraming, table: unknown): HhFraming {
  if (table === undefined) return f;
  if (!isTable(table)) throw new MapError('[protocol] must be a table');
  const out: HhFraming = { ...f };
  for (const [key, value] of Object.entries(table)) {
    const k = FRAMING_KEYS[key];
    if (!k) throw new MapError(`[protocol] unknown protocol setting: ${key}`);
    if (k === 'initSequence') (out as any)[k] = typeof value === 'string' ? parseHex(value) : Uint8Array.from(value as number[]);
    else (out as any)[k] = value;
  }
  return out;
}

function constantsOf(table: unknown, where: string): Map<string, number> {
  const out = new Map<string, number>();
  if (table === undefined) return out;
  if (!isTable(table)) throw new MapError(`${where} must be a table of name = number`);
  for (const [k, v] of Object.entries(table)) {
    if (!NAME.test(k)) throw new MapError(`${where}: bad name '${k}'`);
    if (typeof v !== 'number') throw new MapError(`${where}: ${k} must be a number`);
    out.set(k, v);
  }
  return out;
}

function makeMath(e: Table): HhMath {
  if (typeof e.name !== 'string' || !NAME.test(e.name)) throw new MapError(`bad math channel name ${JSON.stringify(e.name)}`);
  if (typeof e.expr !== 'string' || !e.expr.trim()) throw new MapError(`${e.name}: no expression`);
  const kind = e.kind ?? 'value';
  const m: HhMath = {
    name: e.name, expr: e.expr, label: str(e.label) || e.name, units: str(e.units), min: Number(e.min ?? 0),
    max: Number(e.max ?? 100), decimals: Number(e.decimals ?? 1), kind, enabled: e.enabled ?? true,
  };
  if (kind === 'flag') {
    m.min = 0;
    m.max = 1;
    m.decimals = 0;
  }
  return m;
}

function numOrNull(v: unknown): number | null {
  return v === undefined || v === null ? null : Number(v);
}

function makeAlarm(e: Table): HhAlarm {
  if (typeof e.name !== 'string' || !NAME.test(e.name)) throw new MapError(`bad alarm name ${JSON.stringify(e.name)}`);
  const severity = e.severity ?? 'warn';
  if (severity !== 'warn' && severity !== 'critical') throw new MapError(`alarm '${e.name}': severity must be 'warn' or 'critical'`);
  const a: HhAlarm = {
    name: e.name, channel: str(e.channel), low: numOrNull(e.low), high: numOrNull(e.high),
    rateLow: numOrNull(e.rate_low), rateHigh: numOrNull(e.rate_high), rateWindowMs: Number(e.rate_window_ms ?? 500),
    when: str(e.when), clearWhen: str(e.clear_when), gate: str(e.gate), holdS: Number(e.hold_s ?? 0),
    clearHoldS: Number(e.clear_hold_s ?? 0), hysteresis: Number(e.hysteresis ?? 0), severity,
    priority: e.priority === undefined || e.priority === null ? (severity === 'critical' ? 100 : 50) : Number(e.priority),
    latching: Boolean(e.latching ?? false), actions: e.actions === undefined ? ['tile', 'banner'] : [...e.actions],
    message: str(e.message), label: str(e.label) || e.name.replace(/_/g, ' '), enabled: e.enabled ?? true,
  };
  const limits = [a.low, a.high, a.rateLow, a.rateHigh];
  if (limits.some((v) => v !== null) && !a.channel) throw new MapError(`alarm '${a.name}': low/high/rate limits need a 'channel'`);
  if (limits.every((v) => v === null) && !a.when.trim()) {
    throw new MapError(`alarm '${a.name}': needs a limit (low, high, rate_low, rate_high) or 'when'`);
  }
  return a;
}

export function usesRate(a: HhAlarm): boolean {
  return a.rateLow !== null || a.rateHigh !== null;
}

/** The math engine's order (mathch/engine.py _sort) and each channel's base inputs. */
function mathEngine(math: HhMath[], inputs: Set<string>, constants: Map<string, number>):
  { order: string[]; base: Map<string, Set<string>> } {
  const byName = new Map<string, HhMath>();
  for (const m of math) {
    if (!m.enabled) continue;
    if (byName.has(m.name)) throw new MapError(`duplicate math channel '${m.name}'`);
    if (inputs.has(m.name)) throw new MapError(`math channel '${m.name}' has the same name as a measured channel`);
    if (constants.has(m.name)) throw new MapError(`math channel '${m.name}' has the same name as a constant`);
    byName.set(m.name, m);
  }
  const deps = new Map<string, string[]>();
  for (const [name, m] of byName) deps.set(name, [...references(m.expr)].filter((r) => byName.has(r)).sort());
  const order: string[] = [];
  const state = new Map<string, number>();
  const visit = (name: string, path: string[]): void => {
    if (state.get(name) === 2) return;
    if (state.get(name) === 1) {
      const loop = [...path.slice(path.indexOf(name)), name];
      throw new MapError('math channels refer to each other in a loop: ' + loop.join(' -> '));
    }
    state.set(name, 1);
    for (const d of deps.get(name)!) visit(d, [...path, name]);
    state.set(name, 2);
    order.push(name);
  };
  for (const name of byName.keys()) visit(name, []);
  const known = new Set([...inputs, ...byName.keys()]);
  const base = new Map<string, Set<string>>();
  for (const name of order) {
    const used = new Set<string>();
    for (const n of walk(parseExpr(byName.get(name)!.expr))) {
      if (n.t !== 'var') continue;
      if (constants.has(n.name) && !known.has(n.name)) continue;
      if (!known.has(n.name)) throw new MapError(`math channel '${name}': unknown name '${n.name}'`);
      used.add(n.name);
    }
    const all = new Set<string>();
    for (const d of used) for (const x of base.get(d) ?? new Set([d])) all.add(x);
    base.set(name, all);
  }
  return { order, base };
}

const TOP_KEYS = new Set(['schema', 'version', 'ecu', 'ecu_variant', 'base_variant', 'variants', 'ecu_id',
  'protocol', 'constants', 'channel', 'math', 'alarm', 'obd2']);

/** Resolve a map from its TOML text (addressmap.loads). ``variant`` picks another ECU variant. */
export function resolveMap(text: string, variant?: string | null): HhMap {
  let doc: Table;
  try {
    doc = parseToml(text) as Table;
  } catch (e) {
    throw new MapError(`address map: ${(e as Error).message}`);
  }
  const schema = doc.schema ?? 1;
  if (!Number.isInteger(schema) || schema < 1) throw new MapError('address map: schema must be a whole number');
  if (schema > 2) throw new MapError(`address map: written for map schema ${schema}, this hondalog reads up to 2.`);
  if (schema >= 2) {
    const extra = Object.keys(doc).filter((k) => !TOP_KEYS.has(k)).sort();
    if (extra.length) throw new MapError(`address map: unknown section or key '${extra[0]}'`);
  }
  const entries: Table[] = doc.channel ?? [];
  const base = entries.map(makeChannel);
  if (!base.length) throw new MapError('address map: no [[channel]] entries');
  const names = base.map((c) => c.name);
  const dupes = [...new Set(names.filter((n, i) => names.indexOf(n) !== i))];
  if (dupes.length) throw new MapError(`address map: duplicate channel names ${dupes.sort().join(', ')}`);

  const variants: Table = doc.variants ?? {};
  if (!isTable(variants)) throw new MapError('address map: [variants] must hold one table per ECU');
  const variantNames = Object.keys(variants);
  const baseVariant = str(doc.base_variant) || (variantNames[0] ?? '');
  const active = str(variant || doc.ecu_variant || baseVariant);
  const used = new Set<string>();
  for (const e of entries) for (const v of Object.keys(e.variants ?? {})) used.add(v);
  if (variantNames.length) {
    const unknown = [...new Set([...used, active, baseVariant])].filter((v) => !(v in variants)).sort();
    if (unknown.length) throw new MapError(`address map: ECU variant '${unknown[0]}' is not listed under [variants]`);
  } else if (active && !used.has(active) && active !== baseVariant) {
    throw new MapError(`address map: no ECU variant '${active}' in this map`);
  }
  const info: Table = active !== baseVariant ? { ...(variants[active] ?? {}) } : {};
  const channels: HhChannel[] = [];
  entries.forEach((e, i) => {
    const c = forVariant(e, base[i]!, active, baseVariant);
    if (c) channels.push(c);
  });
  if (!channels.length) throw new MapError(`address map: variant '${active}' has no channels`);

  const framing = framingWith(framingWith(DEFAULT_FRAMING, doc.protocol), info.protocol);
  const constants = constantsOf(doc.constants, 'address map: [constants]');
  for (const [k, v] of constantsOf(info.constants, `address map: [variants.${active}.constants]`)) constants.set(k, v);
  const chNames = new Set(names);
  for (const k of constants.keys()) if (chNames.has(k)) throw new MapError(`address map: constant '${k}' has the same name as a channel`);

  const obd2: HhObd2[] = ((doc.obd2 ?? []) as Table[]).map((e) => {
    if (typeof e.name !== 'string' || !NAME.test(e.name)) throw new MapError(`bad obd2 channel name ${JSON.stringify(e.name)}`);
    if (!Number.isInteger(e.pid) || e.pid <= 0 || e.pid > 0xff) throw new MapError(`${e.name}: pid must be a Mode 01 PID, 0x01-0xFF`);
    return { name: e.name, pid: e.pid, units: str(e.units), kind: e.kind ?? 'value' };
  });
  for (const o of obd2) if (chNames.has(o.name)) throw new MapError(`address map: obd2 channel '${o.name}' has the same name as a channel`);
  const math = ((doc.math ?? []) as Table[]).map(makeMath);
  const alarms = ((doc.alarm ?? []) as Table[]).map(makeAlarm);
  const ecuId: Table = doc.ecu_id ?? {};
  const engine = mathEngine(math, new Set([...channels.map((c) => c.name), ...obd2.map((o) => o.name)]), constants);

  const map: HhMap = {
    version: str(doc.version ?? 'unversioned'),
    ecu: str(info.description || doc.ecu || ''),
    variant: active,
    baseVariant,
    variants: variantNames,
    channels,
    framing,
    ecuIdAddr: ecuId.addr === undefined || ecuId.addr === null ? null : Number(ecuId.addr),
    ecuIdLen: Math.trunc(Number(ecuId.length ?? 10)),
    constants,
    math,
    mathOrder: engine.order,
    baseInputs: engine.base,
    alarms,
    obd2,
    versionHash: '',
    text,
  };
  map.versionHash = versionHash(map);
  return map;
}

/** Is a channel (or a math channel, through its inputs) verified? (AddressMap.is_verified) */
export function isVerified(map: HhMap, name: string): boolean {
  const ch = map.channels.find((c) => c.name === name);
  if (ch) return ch.verified;
  const inputs = map.baseInputs.get(name);
  if (!inputs) return false;
  for (const n of inputs) {
    const c = map.channels.find((x) => x.name === n);
    if (!c || !c.verified) return false;
  }
  return true;
}

// ---------------------------------------------------------------- version hash

/** Python's float repr (what json.dumps writes for a float). */
export function pyFloatRepr(x: number): string {
  if (Number.isNaN(x)) return 'NaN';
  if (!Number.isFinite(x)) return x > 0 ? 'Infinity' : '-Infinity';
  if (x === 0) return Object.is(x, -0) ? '-0.0' : '0.0';
  const [mant = '', expText = '0'] = x.toExponential().split('e');
  const e = parseInt(expText, 10);
  const neg = mant.startsWith('-');
  const digits = mant.replace('-', '').replace('.', '');
  const decpt = e + 1;
  let body: string;
  if (decpt <= -4 || decpt > 16) {
    const m = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits;
    body = `${m}e${e < 0 ? '-' : '+'}${String(Math.abs(e)).padStart(2, '0')}`;
  } else if (decpt <= 0) {
    body = `0.${'0'.repeat(-decpt)}${digits}`;
  } else if (decpt >= digits.length) {
    body = `${digits}${'0'.repeat(decpt - digits.length)}.0`;
  } else {
    body = `${digits.slice(0, decpt)}.${digits.slice(decpt)}`;
  }
  return (neg ? '-' : '') + body;
}

/** Strings as json.dumps(ensure_ascii=False) writes them (the same escapes as JSON.stringify). */
function jstr(s: string): string {
  return JSON.stringify(s);
}

/** AddressMap.version_hash: sha256 of the decode spec as sorted, compact JSON; 12 hex digits. */
export function versionHash(map: HhMap): string {
  const parts: string[] = [];
  const channels = map.channels.map((c) =>
    `[${jstr(c.name)},${c.addr},${c.length},${jstr(c.endian)},${jstr(c.formula)},${jstr(c.units)},${jstr(c.kind)}]`);
  parts.push(`"channels":[${channels.join(',')}]`);
  const consts = [...map.constants.entries()].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
    .map(([k, v]) => `[${jstr(k)},${pyFloatRepr(v)}]`);
  parts.push(`"constants":[${consts.join(',')}]`);
  const math = map.math.filter((m) => m.enabled).map((m) => `[${jstr(m.name)},${jstr(m.expr)},${jstr(m.units)}]`);
  parts.push(`"math":[${math.join(',')}]`);
  if (map.obd2.length) {
    const o = map.obd2.map((c) => `[${jstr(c.name)},${c.pid},${jstr(c.units)},${jstr(c.kind)}]`);
    parts.push(`"obd2":[${o.join(',')}]`);
  }
  parts.push(`"variant":${jstr(map.variant)}`);
  return hex(sha256(utf8(`{${parts.join(',')}}`))).slice(0, 12);
}

/** The map behind the shared contract: rebuilt from its text and variant, hash checked. */
export function fromContract(amap: AddressMap): HhMap {
  const map = resolveMap(amap.text, amap.variant || null);
  if (amap.versionHash && amap.versionHash !== map.versionHash) {
    throw new MapError(`version hash mismatch: the address map module says #${amap.versionHash}, ` +
      `the handheld compiler #${map.versionHash}`);
  }
  return map;
}
