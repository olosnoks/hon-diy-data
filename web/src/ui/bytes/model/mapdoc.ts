/**
 * The address map as the F7 Bytes editor sees it: TOML text in, a checked
 * model out, plus the comment-preserving edits the editor makes (status
 * marks with evidence, channel fields, version bump), a diff and the hash.
 *
 * Local fallback for agent #2's src/core/addressmap: when that module exists
 * the integrator passes its parser through BytesDeps.parseMap. The hash here
 * follows hondalog/addressmap.py AddressMap.version_hash (decode_spec as
 * sorted, compact JSON, sha256, 12 hex digits) and is checked against the
 * Python output for the shipped map in mapdoc.test.ts.
 */

import { parse as parseToml } from 'smol-toml';
import { sha256 } from '@noble/hashes/sha256';
import { bytesToHex } from '@noble/hashes/utils';
import { compileFormula } from './formula';

export type Status = 'verified' | 'unverified' | 'suspect';
export const STATUSES: Status[] = ['unverified', 'verified', 'suspect'];
const RATES = ['fast', 'medium', 'slow'];
const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
const TOP_KEYS = new Set(['schema', 'version', 'ecu', 'ecu_variant', 'base_variant', 'variants', 'ecu_id',
  'protocol', 'constants', 'channel', 'math', 'alarm', 'obd2']);
const VARIANT_KEYS = new Set(['addr', 'length', 'formula', 'endian', 'label', 'units', 'min', 'max',
  'decimals', 'rate', 'status', 'evidence', 'source', 'notes', 'absent']);
const DEFAULT_CLASS: Record<string, string> = { rpm: 'fast', map: 'fast', tps: 'fast', inj: 'fast', ign: 'fast',
  o2: 'medium', stft: 'medium', ltft: 'medium', vss: 'medium' };

export class MapError extends Error {}

export interface Evidence {
  date?: string; method?: string; result?: string; summary?: string; log?: string;
  map_version?: string; map_hash?: string; reference?: string;
}

export interface MapChannel {
  name: string; label: string; addr: number; length: number; formula: string; units: string;
  min: number; max: number; decimals: number; kind: 'value' | 'flag'; status: Status; rate: string;
  endian: 'big' | 'little'; source: string; notes: string; evidence: Evidence | null;
}

export interface MapMath { name: string; expr: string; units: string; label: string; enabled: boolean }
export interface MapAlarm { [key: string]: unknown; name: string }
export interface MapObd2 { name: string; pid: number; units: string; kind: string }

export interface ParsedMap {
  version: string;
  ecu: string;
  variant: string;
  baseVariant: string;
  variants: string[];
  channels: MapChannel[];
  math: MapMath[];
  alarms: MapAlarm[];
  obd2: MapObd2[];
  constants: Record<string, number>;
  ecuId: { addr: number | null; length: number };
  hash: string;
  text: string;
}

type Table = Record<string, unknown>;
const isTable = (v: unknown): v is Table => typeof v === 'object' && v !== null && !Array.isArray(v) && !(v instanceof Date);
const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v);

function num(v: unknown, dflt: number): number {
  return typeof v === 'number' ? v : typeof v === 'bigint' ? Number(v) : dflt;
}

function channelFrom(entry: Table): MapChannel {
  const name = entry.name;
  if (typeof name !== 'string' || !NAME.test(name)) throw new MapError(`bad channel name ${JSON.stringify(name)}`);
  const known = new Set(['name', 'addr', 'length', 'formula', 'label', 'units', 'min', 'max', 'decimals', 'kind', 'gauge',
    'plot', 'status', 'rate', 'endian', 'source', 'notes', 'evidence', 'variants', 'group']);
  const extra = Object.keys(entry).filter((k) => !known.has(k)).sort();
  if (extra.length) throw new MapError(`${name}: unexpected field ${JSON.stringify(extra[0])}`);
  const addr = entry.addr;
  if (!isInt(addr) || addr < 0 || addr > 0xff) throw new MapError(`${name}: addr out of range`);
  const length = entry.length ?? 1;
  if (!isInt(length) || length < 1 || length > 4 || addr + length > 0x100) {
    throw new MapError(`${name}: length must be 1-4 and stay below 0x100`);
  }
  const kind = entry.kind ?? 'value';
  if (kind !== 'value' && kind !== 'flag') throw new MapError(`${name}: kind must be 'value' or 'flag'`);
  let rate = typeof entry.rate === 'string' ? entry.rate.trim().toLowerCase() : entry.rate === undefined ? '' : null;
  if (rate === null) throw new MapError(`${name}: rate must be one of ${RATES.join(', ')}`);
  rate = rate || DEFAULT_CLASS[name] || 'slow';
  if (!RATES.includes(rate)) throw new MapError(`${name}: rate must be one of ${RATES.join(', ')}`);
  const endian = entry.endian ?? 'big';
  if (endian !== 'big' && endian !== 'little') throw new MapError(`${name}: endian must be 'big' or 'little'`);
  const status = typeof entry.status === 'string' ? entry.status.toLowerCase() : entry.status === undefined ? 'unverified' : '';
  if (!STATUSES.includes(status as Status)) throw new MapError(`${name}: status must be one of ${STATUSES.join(', ')}`);
  const formula = entry.formula ?? 'x';
  if (typeof formula !== 'string') throw new MapError(`${name}: formula must be text`);
  try {
    compileFormula(formula);
  } catch (e) {
    // named constants are only known to the full map; report everything else
    const msg = (e as Error).message;
    if (!/unknown name/.test(msg)) throw new MapError(`${name}: ${msg}`);
  }
  let evidence: Evidence | null = null;
  if (entry.evidence !== undefined) {
    if (!isTable(entry.evidence)) throw new MapError(`${name}: evidence must be a table`);
    evidence = Object.fromEntries(Object.entries(entry.evidence).map(([k, v]) => [k, String(v)])) as Evidence;
  }
  if (entry.variants !== undefined) {
    if (!isTable(entry.variants)) throw new MapError(`${name}: variants must be a table`);
    for (const [v, t] of Object.entries(entry.variants)) {
      if (!isTable(t)) throw new MapError(`${name}: variants.${v} must be a table`);
      const bad = Object.keys(t).filter((k) => !VARIANT_KEYS.has(k)).sort();
      if (bad.length) throw new MapError(`${name}: variants.${v} cannot set ${JSON.stringify(bad[0])}`);
    }
  }
  const flag = kind === 'flag';
  return {
    name, label: (entry.label as string) || name, addr, length, formula,
    units: typeof entry.units === 'string' ? entry.units : '',
    min: flag ? 0 : num(entry.min, 0), max: flag ? 1 : num(entry.max, 255), decimals: flag ? 0 : num(entry.decimals, 0),
    kind, status: status as Status, rate, endian, source: (entry.source as string) ?? '', notes: (entry.notes as string) ?? '',
    evidence,
  };
}

/** Parse and check a map (one ECU variant). Throws MapError. */
export function parseMapText(text: string, variant?: string): ParsedMap {
  let doc: Table;
  try {
    doc = parseToml(text) as Table;
  } catch (e) {
    throw new MapError(`not valid TOML: ${(e as Error).message.split('\n')[0]}`);
  }
  const schema = doc.schema ?? 1;
  if (!isInt(schema) || schema < 1) throw new MapError('schema must be a whole number');
  if (schema > 2) throw new MapError(`written for map schema ${schema}, this hondalog reads up to 2`);
  if (schema >= 2) {
    const extra = Object.keys(doc).filter((k) => !TOP_KEYS.has(k)).sort();
    if (extra.length) throw new MapError(`unknown section or key ${JSON.stringify(extra[0])}`);
  }
  const entries = (doc.channel ?? []) as unknown[];
  if (!Array.isArray(entries)) throw new MapError('[[channel]] entries expected');
  const rawBase = entries.map((e) => {
    if (!isTable(e)) throw new MapError('[[channel]] entries expected');
    return e;
  });
  const base = rawBase.map(channelFrom);
  if (!base.length) throw new MapError('no [[channel]] entries');
  const names = base.map((c) => c.name);
  const dupes = [...new Set(names.filter((n, i) => names.indexOf(n) !== i))].sort();
  if (dupes.length) throw new MapError(`duplicate channel names ${dupes.join(', ')}`);

  const variants = (doc.variants ?? {}) as Table;
  if (!isTable(variants) || !Object.values(variants).every(isTable)) {
    throw new MapError('[variants] must hold one table per ECU');
  }
  const vnames = Object.keys(variants);
  const baseVariant = String(doc.base_variant || vnames[0] || '');
  const active = String(variant || doc.ecu_variant || baseVariant);
  const used = new Set<string>();
  rawBase.forEach((e) => { if (isTable(e.variants)) Object.keys(e.variants).forEach((v) => used.add(v)); });
  if (vnames.length) {
    const unknown = [...new Set([...used, active, baseVariant])].filter((v) => v && !vnames.includes(v)).sort();
    if (unknown.length) throw new MapError(`ECU variant ${JSON.stringify(unknown[0])} is not listed under [variants]`);
  } else if (active && !used.has(active) && active !== baseVariant) {
    throw new MapError(`no ECU variant ${JSON.stringify(active)} in this map`);
  }
  const channels: MapChannel[] = [];
  base.forEach((ch, i) => {
    if (!active || active === baseVariant) { channels.push(ch); return; }
    const over = { ...((rawBase[i]!.variants as Table | undefined)?.[active] as Table | undefined ?? {}) };
    if (over.absent) return;
    const table: Table = { ...rawBase[i]!, status: 'unverified', ...over };
    delete table.variants;
    delete table.absent;
    if (!('evidence' in over)) delete table.evidence;
    const merged = channelFrom(table);
    channels.push(merged);
  });
  if (!channels.length) throw new MapError(`variant ${JSON.stringify(active)} has no channels`);

  const info = active !== baseVariant && isTable(variants[active]) ? variants[active] as Table : {};
  const constants: Record<string, number> = {};
  for (const [where, t] of [['[constants]', doc.constants], [`[variants.${active}.constants]`, info.constants]] as const) {
    if (t === undefined) continue;
    if (!isTable(t)) throw new MapError(`${where} must be a table`);
    for (const [k, v] of Object.entries(t)) {
      if (!NAME.test(k)) throw new MapError(`${where}: bad name ${JSON.stringify(k)}`);
      if (typeof v !== 'number' && typeof v !== 'bigint') throw new MapError(`${where}: ${k} must be a number`);
      constants[k] = Number(v);
    }
  }
  const clash = Object.keys(constants).filter((k) => names.includes(k)).sort();
  if (clash.length) throw new MapError(`constant ${JSON.stringify(clash[0])} has the same name as a channel`);
  for (const ch of channels) {
    try { compileFormula(ch.formula, constants); } catch (e) { throw new MapError(`${ch.name}: ${(e as Error).message}`); }
  }
  const obd2 = ((doc.obd2 ?? []) as Table[]).map((e) => ({
    name: String(e.name), pid: num(e.pid, 0), units: (e.units as string) ?? '', kind: (e.kind as string) ?? 'value',
  }));
  const obClash = obd2.map((c) => c.name).filter((n) => names.includes(n));
  if (obClash.length) throw new MapError(`obd2 channel ${JSON.stringify(obClash[0])} has the same name as a channel`);
  const math = ((doc.math ?? []) as Table[]).map((e) => {
    if (typeof e.name !== 'string' || !NAME.test(e.name)) throw new MapError(`bad math channel name ${JSON.stringify(e.name)}`);
    if (typeof e.expr !== 'string') throw new MapError(`math ${e.name}: expr must be text`);
    return { name: e.name, expr: e.expr, units: (e.units as string) ?? '', label: (e.label as string) || e.name, enabled: e.enabled !== false };
  });
  const alarms = ((doc.alarm ?? []) as Table[]).map((e) => {
    if (typeof e.name !== 'string' || !NAME.test(e.name)) throw new MapError(`bad alarm name ${JSON.stringify(e.name)}`);
    const sev = e.severity ?? 'warn';
    if (sev !== 'warn' && sev !== 'critical') throw new MapError(`alarm ${JSON.stringify(e.name)}: severity must be 'warn' or 'critical'`);
    return { ...e, name: e.name } as MapAlarm;
  });
  const ecuId = isTable(doc.ecu_id) ? doc.ecu_id : {};
  const map: Omit<ParsedMap, 'hash'> = {
    version: String(doc.version ?? 'unversioned'),
    ecu: String((info.description as string) || doc.ecu || ''),
    variant: active, baseVariant, variants: vnames, channels, math, alarms, obd2, constants,
    ecuId: { addr: isInt(ecuId.addr) ? ecuId.addr : null, length: num(ecuId.length, 10) },
    text,
  };
  return { ...map, hash: versionHash(map) };
}

/** Problems with a map text, as lines (empty = fine). */
export function validateMap(text: string, variant?: string): string[] {
  try {
    const m = parseMapText(text, variant);
    const out: string[] = [];
    // overlapping channels are allowed (a u16 and its bytes), but say so
    const owner = new Map<number, string>();
    for (const c of m.channels) {
      for (let a = c.addr; a < c.addr + c.length; a++) {
        const prev = owner.get(a);
        if (prev && c.kind !== 'flag') out.push(`note: 0x${a.toString(16).toUpperCase().padStart(2, '0')} is read by ${prev} and ${c.name}`);
        if (!prev) owner.set(a, c.name);
      }
    }
    return out.filter((l) => !l.startsWith('note:')).concat(out.filter((l) => l.startsWith('note:')));
  } catch (e) {
    return [(e as Error).message];
  }
}

// ---- hash ---------------------------------------------------------------

/** Python's repr() for a float (json.dumps of a float). */
export function pyFloat(v: number): string {
  if (!Number.isFinite(v)) return v > 0 ? 'Infinity' : v < 0 ? '-Infinity' : 'NaN';
  if (Number.isInteger(v) && Math.abs(v) < 1e16) return `${v}.0`;
  let s = String(v);
  // JS: 1e-7 / 1.5e+21 ; Python: 1e-07 / 1.5e+21
  const m = /^(-?[\d.]+)e([+-])(\d+)$/.exec(s);
  if (m) s = `${m[1]}e${m[2]}${m[3]!.padStart(2, '0')}`;
  else if (Math.abs(v) < 1e-4 && v !== 0) {
    const e = v.toExponential();
    const k = /^(-?[\d.]+)e([+-])(\d+)$/.exec(e)!;
    s = `${k[1]}e${k[2]}${k[3]!.padStart(2, '0')}`;
  }
  return s;
}

/** json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",", ":")). */
function pyJson(v: unknown, floats: boolean): string {
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'string') return JSON.stringify(v);
  if (typeof v === 'number') return floats ? pyFloat(v) : String(v);
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (Array.isArray(v)) return `[${v.map((x) => pyJson(x, floats)).join(',')}]`;
  const t = v as Table;
  return `{${Object.keys(t).sort().map((k) => `${JSON.stringify(k)}:${pyJson(t[k], floats)}`).join(',')}}`;
}

export function decodeSpecJson(map: Omit<ParsedMap, 'hash'>): string {
  const parts: string[] = [];
  const channels = map.channels.map((c) => pyJson([c.name, c.addr, c.length, c.endian, c.formula, c.units, c.kind], false));
  const constants = Object.keys(map.constants).sort().map((k) => `[${JSON.stringify(k)},${pyFloat(map.constants[k]!)}]`);
  const math = map.math.filter((m) => m.enabled).map((m) => pyJson([m.name, m.expr, m.units], false));
  parts.push(`"channels":[${channels.join(',')}]`);
  parts.push(`"constants":[${constants.join(',')}]`);
  parts.push(`"math":[${math.join(',')}]`);
  if (map.obd2.length) parts.push(`"obd2":[${map.obd2.map((c) => pyJson([c.name, c.pid, c.units, c.kind], false)).join(',')}]`);
  parts.push(`"variant":${JSON.stringify(map.variant)}`);
  return `{${parts.join(',')}}`;
}

export function versionHash(map: Omit<ParsedMap, 'hash'>): string {
  return bytesToHex(sha256(new TextEncoder().encode(decodeSpecJson(map)))).slice(0, 12);
}

// ---- edits that keep the file's comments -------------------------------------

/** 'p2p-guess-2 (kerpz)' -> 'p2p-guess-3 (kerpz)'; no number -> append ' r2'. */
export function nextVersion(version: string): string {
  const cut = version.indexOf('(');
  const head = cut < 0 ? version : version.slice(0, cut);
  const tail = cut < 0 ? '' : version.slice(cut);
  const nums = [...head.matchAll(/\d+/g)];
  const last = nums[nums.length - 1];
  if (last && last.index !== undefined) {
    const after = head.slice(last.index + last[0].length).replace(/^[ \-_.]+|[ \-_.]+$/g, '');
    if (!(after.length > 0 && /^[A-Za-z]+$/.test(after))) {
      return `${head.slice(0, last.index)}${Number(last[0]) + 1}${head.slice(last.index + last[0].length)}${tail}`;
    }
  }
  return version ? `${version} r2` : 'r2';
}

export function tomlValue(v: unknown): string {
  if (typeof v === 'string') return JSON.stringify(v);
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(v);
  throw new Error(`cannot write ${typeof v}`);
}

function fmtKeyValue(key: string, v: unknown): string {
  if (key === 'addr' && typeof v === 'number') return `${key} = 0x${v.toString(16).toUpperCase().padStart(2, '0')}`;
  return `${key} = ${tomlValue(v)}`;
}

const HEADER = /^\s*\[\[?\s*([A-Za-z0-9_.]+)\s*\]\]?/;

interface Span { start: number; end: number }

/** Lines [start, end) of the [[kind]] item called `name`, sub-tables included. */
function itemSpan(lines: string[], kind: string, name: string): Span | null {
  for (let i = 0; i < lines.length; i++) {
    if (!new RegExp(`^\\s*\\[\\[\\s*${kind}\\s*\\]\\]`).test(lines[i]!)) continue;
    let end = i + 1;
    while (end < lines.length) {
      const m = HEADER.exec(lines[end]!);
      if (m && !m[1]!.startsWith(`${kind}.`)) break;
      if (m && /^\s*\[\[/.test(lines[end]!) && m[1] === kind) break;
      end++;
    }
    for (let j = i + 1; j < end; j++) {
      if (HEADER.test(lines[j]!)) break;
      const m = /^\s*name\s*=\s*"([^"]*)"/.exec(lines[j]!);
      if (m) {
        if (m[1] === name) return { start: i, end };
        break;
      }
    }
    i = end - 1;
  }
  return null;
}

function splitComment(rest: string): [string, string] {
  let inStr = false;
  for (let i = 0; i < rest.length; i++) {
    const c = rest[i];
    if (c === '"' && rest[i - 1] !== '\\') inStr = !inStr;
    if (c === '#' && !inStr) return [rest.slice(0, i).trimEnd(), rest.slice(i)];
  }
  return [rest.trimEnd(), ''];
}

/** Set keys in lines [start, end) of one table body; missing keys are added
 *  after the last key line. Returns the new end. */
function setKeys(lines: string[], start: number, end: number, updates: Record<string, unknown>): number {
  let lastKey = start;
  const left = new Map(Object.entries(updates));
  for (let i = start; i < end; i++) {
    if (HEADER.test(lines[i]!)) break;
    const m = /^(\s*)([A-Za-z0-9_]+)(\s*=\s*)(.*)$/.exec(lines[i]!);
    if (!m) continue;
    lastKey = i + 1;
    const key = m[2]!;
    if (!left.has(key)) continue;
    const v = left.get(key);
    left.delete(key);
    const [, comment] = splitComment(m[4]!);
    if (v === undefined || v === null) {
      lines.splice(i, 1);
      i--; end--; lastKey--;
      continue;
    }
    const pad = comment ? '   ' : '';
    lines[i] = `${m[1]}${fmtKeyValue(key, v)}${pad}${comment}`;
  }
  const add = [...left.entries()].filter(([, v]) => v !== undefined && v !== null).map(([k, v]) => fmtKeyValue(k, v));
  lines.splice(lastKey, 0, ...add);
  return end + add.length;
}

/** The text with one channel's fields changed (addr, formula, status, ...).
 *  `null` removes a key. Comments and the rest of the file are kept. */
export function setChannel(text: string, name: string, changes: Record<string, unknown>): string {
  const lines = text.split(/\r?\n/);
  const span = itemSpan(lines, 'channel', name);
  if (!span) throw new MapError(`no channel named ${JSON.stringify(name)}`);
  setKeys(lines, span.start + 1, span.end, changes);
  return lines.join('\n');
}

/** Set the evidence sub-table of a channel (replacing an old one). */
export function setEvidence(text: string, name: string, evidence: Evidence | null): string {
  const lines = text.split(/\r?\n/);
  const span = itemSpan(lines, 'channel', name);
  if (!span) throw new MapError(`no channel named ${JSON.stringify(name)}`);
  // drop an existing [channel.evidence]
  for (let i = span.start + 1; i < span.end; i++) {
    if (/^\s*\[\s*channel\.evidence\s*\]/.test(lines[i]!)) {
      let j = i + 1;
      while (j < span.end && !HEADER.test(lines[j]!)) j++;
      while (j > i + 1 && lines[j - 1]!.trim() === '') j--;
      lines.splice(i, j - i);
      span.end -= j - i;
      break;
    }
  }
  if (evidence) {
    const body = Object.entries(evidence).filter(([, v]) => v).map(([k, v]) => `${k} = ${tomlValue(String(v))}`);
    // insert before trailing blank lines of the item
    let at = span.end;
    while (at > span.start + 1 && lines[at - 1]!.trim() === '') at--;
    lines.splice(at, 0, '[channel.evidence]', ...body);
  }
  return lines.join('\n');
}

/** Record a verification result: status + evidence (the hash does not change). */
export function markChannel(text: string, name: string, status: Status, evidence?: Evidence): string {
  let out = setChannel(text, name, { status });
  if (evidence) out = setEvidence(out, name, evidence);
  return out;
}

/** Point a channel at another address: the verified mark goes (a new address
 *  proves nothing yet) and the map's version moves on. */
export function assignAddress(text: string, name: string, addr: number, length = 1, endian: 'big' | 'little' = 'big'): string {
  let out = setChannel(text, name, { addr, length, endian: endian === 'big' ? null : endian, status: 'unverified' });
  out = setEvidence(out, name, null);
  return bumpVersion(out);
}

export function setTopKey(text: string, key: string, value: unknown): string {
  const lines = text.split(/\r?\n/);
  let first = lines.findIndex((l) => HEADER.test(l));
  if (first < 0) first = lines.length;
  setKeys(lines, 0, first, { [key]: value });
  return lines.join('\n');
}

export function bumpVersion(text: string): string {
  let version = 'unversioned';
  try {
    const doc = parseToml(text) as Table;
    version = String(doc.version ?? 'unversioned');
  } catch { /* the edit will fail validation anyway */ }
  return setTopKey(text, 'version', nextVersion(version));
}

// ---- diff -------------------------------------------------------------------

export interface MapChange { section: string; name: string; field: string; old: unknown; new: unknown }

export function diffMaps(a: ParsedMap, b: ParsedMap): { changes: MapChange[]; decodeChanged: boolean } {
  const out: MapChange[] = [];
  for (const f of ['version', 'ecu', 'variant'] as const) if (a[f] !== b[f]) out.push({ section: 'map', name: '', field: f, old: a[f], new: b[f] });
  for (const k of [...new Set([...Object.keys(a.constants), ...Object.keys(b.constants)])].sort()) {
    if (a.constants[k] !== b.constants[k]) out.push({ section: 'constant', name: k, field: 'value', old: a.constants[k], new: b.constants[k] });
  }
  const items = <T extends { name: string }>(section: string, x: T[], y: T[]) => {
    const mx = new Map(x.map((i) => [i.name, i])), my = new Map(y.map((i) => [i.name, i]));
    for (const n of [...new Set([...mx.keys(), ...my.keys()])].sort()) {
      const p = mx.get(n), q = my.get(n);
      if (!p) { out.push({ section, name: n, field: '', old: undefined, new: 'added' }); continue; }
      if (!q) { out.push({ section, name: n, field: '', old: 'removed', new: undefined }); continue; }
      for (const f of [...new Set([...Object.keys(p), ...Object.keys(q)])].sort()) {
        const u = (p as Table)[f], v = (q as Table)[f];
        if (JSON.stringify(u) !== JSON.stringify(v)) out.push({ section, name: n, field: f, old: u, new: v });
      }
    }
  };
  items('channel', a.channels, b.channels);
  items('math', a.math, b.math);
  items('alarm', a.alarms, b.alarms);
  return { changes: out, decodeChanged: a.hash !== b.hash };
}

export function changeText(c: MapChange): string {
  const show = (v: unknown) => (v === undefined ? '—' : typeof v === 'number' ? String(v) : JSON.stringify(v));
  if (!c.field) return `${c.section} ${c.name}: ${c.new === 'added' ? 'added' : 'removed'}`;
  return `${c.section}${c.name ? ' ' + c.name : ''}.${c.field}: ${show(c.old)} → ${show(c.new)}`;
}

/** Decode one sweep ({addr: byte}) with the map's formulas (null = not read / formula failed). */
export function decodeWith(map: ParsedMap, mem: Map<number, number> | Record<number, number>): Record<string, number | null> {
  const get = (a: number) => (mem instanceof Map ? mem.get(a) : (mem as Record<number, number>)[a]);
  const out: Record<string, number | null> = {};
  for (const ch of map.channels) {
    const raw: number[] = [];
    for (let a = ch.addr; a < ch.addr + ch.length; a++) {
      const v = get(a);
      if (v === undefined || v < 0) break;
      raw.push(v);
    }
    if (raw.length !== ch.length) { out[ch.name] = null; continue; }
    const ordered = ch.endian === 'big' ? raw : [...raw].reverse();
    const x = ordered.reduce((acc, b) => acc * 256 + b, 0);
    try {
      const v = compileCached(ch.formula, map.constants)(x, raw);
      out[ch.name] = Number.isFinite(v) ? v : null;
    } catch {
      out[ch.name] = null;
    }
  }
  return out;
}

const cache = new Map<string, ReturnType<typeof compileFormula>>();
function compileCached(formula: string, constants: Record<string, number>) {
  const key = formula + '\u0000' + JSON.stringify(constants);
  let f = cache.get(key);
  if (!f) { f = compileFormula(formula, constants); cache.set(key, f); }
  return f;
}
