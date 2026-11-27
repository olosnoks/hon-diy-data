/**
 * HANDHELD.MAP: the address map compiled for the handheld (hondalog/handheld/mapfile.py).
 *
 * The handheld reads the *core* (header and sections: channels with their formulas as
 * byte code, alarm rules, link timing). After the core comes the map's TOML text, which
 * the handheld skips and the importer uses. Layout: firmware/handheld/FORMAT.md.
 * ``buildMap`` is byte-identical to the Python ``build_map`` (golden tests).
 */

import { sha256 } from '@noble/hashes/sha256';
import type { AddressMap } from '../types';
import { Struct, concat, cstr, crc32, equalBytes, findBytes, fit, utf8, fromUtf8 } from './bytes';
import { Pool, Unsupported, compileCondition, compileFormula } from './bytecode';
import * as F from './format';
import { type HhAlarm, type HhMap, MapError, fromContract, isVerified, resolveMap, usesRate } from './hhmap';

export { MapError };

export const MAP_NAME = 'HANDHELD.MAP';
export const CFG_NAME = 'HANDHELD.CFG';

export const DEFAULT_CFG = `# hondalog handheld settings. Edit, save, restart the handheld.
# Lines that are missing or wrong fall back to the value shown here.
backend = auto          # usb: KKL cable on the USB socket.  uart: L9637D on the UART.  auto: usb
gps_baud = 9600         # 0 = no GPS
iso_fallback = 1        # try plain OBD2 (ISO 9141-2) when Honda reads stay unanswered
iso_after_s = 12        # ... for this long (an ISO session left open lasts 5 s)
drive_gap_s = 120       # ECU silent this long ends the drive
sync_ms = 1000          # the card is synced this often: the most a power cut can take
block_ms = 250          # a log block is closed after this long
prealloc_mb = 16        # space taken for a drive file at its start (about 3 hours); 0 = none
stall_pre_s = -1        # seconds kept before a stall; -1 = what the address map says (30)
stall_post_s = -1       # seconds after; -1 = the map's (10)
capture_files = 1       # also write CAPTURES/Cnnnnnn.HHD at a stall
sleep_after_s = 300     # sleep this long after the drive ended; 0 = never (use with a switched supply)
sleep_probe_s = 20      # asleep: wake this often and ask the ECU
usb_latency_ms = 1      # FTDI latency timer
utc_offset_min = 0      # clock on the screen only; files are UTC
serial = 1              # the link to the laptop on the USB-C port; 0 = off (plain log console)
serial_baud = 921600    # its speed; try 460800 if the laptop sees damaged frames
# rotate = 180          # display: 0 or 180 (upside down); unset = the firmware's built-in default
units = us              # screen: us (F, mph, mi, inHg, gal/h) or metric (C, km/h, kPa, L/h)
`;

export interface ExportReport {
  channels: string[];
  math: string[];
  alarms: string[];
  /** "name: why", one line each */
  skipped: string[];
  coreBytes: number;
  totalBytes: number;
  mapHash: string;
}

export function newReport(): ExportReport {
  return { channels: [], math: [], alarms: [], skipped: [], coreBytes: 0, totalBytes: 0, mapHash: '' };
}

export interface StallOptions { rpm_run?: number; rpm_zero?: number; link_down_s?: number; pre_s?: number; post_s?: number; rpm_channel?: string }

function section(kind: number, count: number, body: Uint8Array): Uint8Array {
  return concat(F.MAP_SEC.pack(kind, 0, count, body.length), body);
}

const f32 = (v: number | null): number => (v === null ? NaN : v);

/** (warn_lo, warn_hi, alarm_lo, alarm_hi) of the rules on one channel: the limit a value crosses first. */
function limits(rules: HhAlarm[], channel: string): [number, number, number, number] {
  let warnLo = NaN, warnHi = NaN, alarmLo = NaN, alarmHi = NaN;
  for (const r of rules) {
    if (r.channel !== channel) continue;
    const critical = r.severity === 'critical';
    if (r.low !== null) {
      if (critical) alarmLo = Number.isNaN(alarmLo) ? r.low : Math.max(alarmLo, r.low);
      else warnLo = Number.isNaN(warnLo) ? r.low : Math.max(warnLo, r.low);
    }
    if (r.high !== null) {
      if (critical) alarmHi = Number.isNaN(alarmHi) ? r.high : Math.min(alarmHi, r.high);
      else warnHi = Number.isNaN(warnHi) ? r.high : Math.min(warnHi, r.high);
    }
  }
  return [warnLo, warnHi, alarmLo, alarmHi];
}

const clampInt = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, Math.trunc(v)));

/**
 * Compile ``map`` into the bytes of a HANDHELD.MAP. ``source``: the TOML text appended
 * (default: the map's own text). Throws MapError if a channel formula cannot be compiled.
 */
export function buildMap(map: HhMap, source?: string | null, stall?: StallOptions | null,
  report: ExportReport = newReport()): Uint8Array {
  const src = source ?? map.text ?? '';
  const pool = new Pool();
  let programs: Uint8Array = new Uint8Array(0);
  const constants = map.constants;

  const add = (program: Uint8Array): [number, number] => {
    let at = findBytes(programs, program); // identical formulas share their bytes
    if (at < 0) {
      at = programs.length;
      programs = concat(programs, program);
    }
    if (at + program.length > 0xffff) throw new MapError('the formulas do not fit a handheld map (64 KB of programs)');
    return [at, program.length];
  };

  const measured = map.channels;
  if (measured.length > F.MAX_CH) throw new MapError(`the handheld takes ${F.MAX_CH} channels, the map has ${measured.length}`);
  const records: [string, string, string, ...number[]][] = [];
  const index = new Map<string, number>();
  const enabled = map.alarms.filter((r) => r.enabled);
  for (const ch of measured) {
    let program: Uint8Array;
    try {
      program = compileFormula(ch.formula, pool, constants, ch.length);
    } catch (e) {
      if (e instanceof Unsupported) {
        throw new MapError(`${ch.name}: formula '${ch.formula}' cannot run on the handheld: ${e.message}`);
      }
      throw e;
    }
    const [off, len] = add(program);
    index.set(ch.name, records.length);
    records.push([ch.name, ch.label, ch.units, ch.min, ch.max, ...limits(enabled, ch.name),
      clampInt(ch.decimals, 0, 9), ch.verified ? 1 : 0, F.RATE_CLASS[ch.rate]!,
      ch.kind === 'flag' ? F.KIND_FLAG : F.KIND_VALUE, ch.addr, ch.length, ch.endian === 'little' ? 1 : 0, 0, off, len]);
    report.channels.push(ch.name);
  }

  // math channels, inputs first; one that needs history is left out, and so is everything built on it
  const absent = map.obd2.map((o) => o.name);
  for (const name of map.mathOrder) {
    const m = map.math.find((x) => x.name === name)!;
    let program: Uint8Array;
    try {
      if (records.length >= F.MAX_CH) throw new Unsupported(`no room: the handheld takes ${F.MAX_CH} channels`);
      [program] = compileCondition(m.expr, pool, index, constants, absent);
    } catch (e) {
      if (e instanceof Unsupported) {
        report.skipped.push(`math ${name}: ${e.message}`);
        continue;
      }
      throw e;
    }
    const [off, len] = add(program);
    index.set(name, records.length);
    const inputs = map.baseInputs.get(name) ?? new Set<string>();
    let slowest = -1;
    for (const c of measured) if (inputs.has(c.name)) slowest = Math.max(slowest, F.RATE_CLASS[c.rate]!);
    if (slowest < 0) slowest = 2;
    records.push([name, m.label, m.units, m.min, m.max, ...limits(enabled, name), clampInt(m.decimals, 0, 9),
      isVerified(map, name) ? 1 : 0, slowest, F.KIND_MATH, 0, 0, 0, 0, off, len]);
    report.math.push(name);
  }

  const alarmRecords: Uint8Array[] = [];
  let slopes = 0;
  for (const rule of enabled) {
    const spans: number[] = [];
    try {
      if (alarmRecords.length >= F.MAX_ALARMS) throw new Unsupported(`no room: the handheld takes ${F.MAX_ALARMS} alarms`);
      if (rule.channel && !index.has(rule.channel)) throw new Unsupported(`its channel '${rule.channel}' is not on the handheld`);
      if (usesRate(rule) && slopes >= F.MAX_SLOPES) throw new Unsupported(`no room: the handheld takes ${F.MAX_SLOPES} rate alarms`);
      for (const text of [rule.when, rule.clearWhen, rule.gate]) {
        if (text.trim()) {
          const [program] = compileCondition(text, pool, index, constants, absent);
          spans.push(...add(program));
        } else {
          spans.push(0, 0);
        }
      }
    } catch (e) {
      if (e instanceof Unsupported) {
        report.skipped.push(`alarm ${rule.name}: ${e.message}`);
        continue;
      }
      throw e;
    }
    slopes += usesRate(rule) ? 1 : 0;
    const flags = (rule.latching ? F.AF_LATCHING : 0) | (rule.low !== null ? F.AF_LOW : 0)
      | (rule.high !== null ? F.AF_HIGH : 0) | (rule.rateLow !== null ? F.AF_RATE_LOW : 0)
      | (rule.rateHigh !== null ? F.AF_RATE_HIGH : 0) | (rule.actions.includes('marker') ? F.AF_MARKER : 0)
      | (rule.actions.includes('capture') ? F.AF_CAPTURE : 0) | (rule.actions.includes('tile') ? F.AF_TILE : 0);
    alarmRecords.push(F.MAP_ALARM.pack(fit(rule.name, 16), fit(rule.label, 24),
      rule.channel ? (index.get(rule.channel) ?? -1) : -1, rule.severity === 'critical' ? 2 : 1, flags,
      clampInt(rule.priority || 0, 0, 255), new Uint8Array(3), f32(rule.low), f32(rule.high), rule.hysteresis,
      rule.holdS, rule.clearHoldS, f32(rule.rateLow), f32(rule.rateHigh), rule.rateWindowMs, ...spans));
    report.alarms.push(rule.name);
  }

  const fr = map.framing;
  if (fr.requestLen !== 5) throw new MapError('the handheld only sends 5-byte read requests (protocol.request_len)');
  if (fr.initSequence.length > 16) throw new MapError('protocol.init_sequence is longer than 16 bytes');
  const init = new Uint8Array(16);
  init.set(fr.initSequence);
  const proto = F.MAP_PROTO.pack(fr.baud, fr.cmdRead, fr.requestLen, fr.replyHeader, fr.replyLenExtra,
    Math.min(fr.maxReadLen, F.MAX_READ), fr.adaptiveTiming ? 1 : 0, clampInt(fr.retries, 0, 5), fr.initSequence.length,
    init, new Uint8Array(3), fr.initDelayS, fr.replyTimeoutS, fr.interRequestDelayS, fr.minReplyTimeoutS,
    fr.interByteTimeoutS, fr.maxInterRequestDelayS, fr.turnaroundS, fr.rateFastHz, fr.rateMediumHz, fr.rateSlowHz);
  const opts = { rpm_run: 400.0, rpm_zero: 50.0, link_down_s: 1.0, pre_s: 30.0, post_s: 10.0, rpm_channel: 'rpm', ...(stall ?? {}) };
  const stallBody = F.MAP_STALL.pack(opts.rpm_run, opts.rpm_zero, opts.link_down_s, opts.pre_s, opts.post_s,
    index.get(String(opts.rpm_channel)) ?? -1, 0);
  const info = F.MAP_INFO.pack(fit(map.version, 48), fit(map.variant, 12), map.ecuIdAddr === null ? -1 : map.ecuIdAddr,
    map.ecuIdAddr !== null ? Math.min(10, map.ecuIdLen) : 0, 0);
  const chanBody = concat(...records.map((r) => F.MAP_CHAN.pack(fit(r[0], 16), fit(r[1], 20), fit(r[2], 8),
    ...(r.slice(3) as number[]))));
  const constBody = new Struct(`<${pool.values.length}d`).pack(...pool.values);
  const body = concat(section(F.MS_INFO, 1, info), section(F.MS_PROTO, 1, proto),
    section(F.MS_CONST, pool.values.length, constBody), section(F.MS_CHAN, records.length, chanBody),
    section(F.MS_PROG, 0, programs), section(F.MS_ALARM, alarmRecords.length, concat(...alarmRecords)),
    section(F.MS_STALL, 1, stallBody));
  const coreLen = F.MAP_HDR.size + body.length;
  if (coreLen > F.MAP_MAX_CORE) throw new MapError(`the compiled map is ${coreLen} bytes; the handheld reads ${F.MAP_MAX_CORE}`);
  const srcBytes = utf8(src);
  const sha = srcBytes.length ? sha256(srcBytes) : new Uint8Array(32);
  const mapHash = new Uint8Array(12);
  mapHash.set(utf8(map.versionHash).subarray(0, 12));
  const header = (crc: number): Uint8Array =>
    F.MAP_HDR.pack(F.MAP_MAGIC, F.MAP_SCHEMA, F.MAP_HDR.size, coreLen, crc, mapHash, sha, srcBytes.length);
  const core = concat(header(crc32(concat(header(0), body))), body);
  report.coreBytes = core.length;
  report.mapHash = map.versionHash;
  const out = srcBytes.length ? concat(core, srcBytes, new Struct('<I').pack(crc32(srcBytes))) : core;
  report.totalBytes = out.length;
  return out;
}

/** HANDHELD.MAP for the shared AddressMap contract (rebuilt from its text; the hashes must agree). */
export function buildMapFromContract(amap: AddressMap, stall?: StallOptions | null, report?: ExportReport): Uint8Array {
  return buildMap(fromContract(amap), amap.text, stall, report);
}

/** HANDHELD.MAP straight from TOML text. */
export function buildMapFromToml(text: string, variant?: string | null, stall?: StallOptions | null,
  report?: ExportReport): Uint8Array {
  return buildMap(resolveMap(text, variant), text, stall, report);
}

// ---------------------------------------------------------------- reading

export interface MapFileChannel {
  name: string; label: string; units: string; lo: number; hi: number; warn_lo: number; warn_hi: number;
  alarm_lo: number; alarm_hi: number; decimals: number; verified: boolean; rate_class: number; kind: number;
  addr: number; length: number; little_endian: boolean; prog_off: number; prog_len: number;
}

export interface MapFileAlarm {
  name: string; label: string; channel: number; severity: number; flags: number; priority: number; low: number;
  high: number; hysteresis: number; hold_s: number; clear_hold_s: number; rate_low: number; rate_high: number;
  rate_window_ms: number; when: [number, number]; clear_when: [number, number]; gate: [number, number];
}

export interface MapFile {
  mapHash: string;
  mapSha256: Uint8Array;
  version: string;
  variant: string;
  core: Uint8Array;
  /** The TOML text, "" if the file carries none (or it is damaged). */
  source: string;
  channels: MapFileChannel[];
  alarms: MapFileAlarm[];
  stall: Record<string, number>;
  protocol: Record<string, number | boolean | Uint8Array>;
  constants: number[];
  programs: Uint8Array;
  ecuIdAddr: number | null;
  ecuIdLen: number;
}

/** Parse a HANDHELD.MAP. Throws Error (ValueError in the Python). */
export function readMap(data: Uint8Array): MapFile {
  if (data.length < F.MAP_HDR.size) throw new Error('not a handheld map: too short');
  const [magic, schema, hdrLen, coreLen, crc, mapHash, sha, srcLen] = F.MAP_HDR.unpack(data);
  if (magic !== F.MAP_MAGIC || hdrLen !== F.MAP_HDR.size) throw new Error('not a handheld map');
  if (schema !== F.MAP_SCHEMA) throw new Error(`handheld map schema ${schema}; this hondalog reads ${F.MAP_SCHEMA}`);
  if (coreLen > data.length || coreLen < hdrLen) throw new Error('handheld map is cut short');
  const zeroed = data.slice(0, coreLen);
  zeroed.fill(0, 12, 16);
  if (crc32(zeroed) !== crc) throw new Error('handheld map: checksum is wrong');
  const out: MapFile = {
    mapHash: cstr(mapHash), mapSha256: sha, version: '', variant: '', core: data.slice(0, coreLen), source: '',
    channels: [], alarms: [], stall: {}, protocol: {}, constants: [], programs: new Uint8Array(0), ecuIdAddr: null,
    ecuIdLen: 0,
  };
  let pos = hdrLen;
  while (pos + F.MAP_SEC.size <= coreLen) {
    const [kind, , count, length] = F.MAP_SEC.unpack(data, pos);
    pos += F.MAP_SEC.size;
    const body = data.slice(pos, pos + length);
    pos += length;
    if (kind === F.MS_INFO) {
      const [version, variant, idAddr, idLen] = F.MAP_INFO.unpack(body);
      out.version = cstr(version);
      out.variant = cstr(variant);
      out.ecuIdAddr = idAddr < 0 ? null : idAddr;
      out.ecuIdLen = idLen;
    } else if (kind === F.MS_PROTO) {
      const v = F.MAP_PROTO.unpack(body);
      out.protocol = {
        baud: v[0], cmd_read: v[1], request_len: v[2], reply_header: v[3], reply_len_extra: v[4], max_read_len: v[5],
        adaptive_timing: Boolean(v[6]), retries: v[7], init_sequence: (v[9] as Uint8Array).slice(0, v[8]),
        init_delay_s: v[11], reply_timeout_s: v[12], inter_request_delay_s: v[13], min_reply_timeout_s: v[14],
        inter_byte_timeout_s: v[15], max_inter_request_delay_s: v[16], turnaround_s: v[17], rate_fast_hz: v[18],
        rate_medium_hz: v[19], rate_slow_hz: v[20],
      };
    } else if (kind === F.MS_CONST) {
      out.constants = new Struct(`<${count}d`).unpack(body);
    } else if (kind === F.MS_CHAN) {
      for (let i = 0; i < count; i++) {
        const v = F.MAP_CHAN.unpack(body, i * F.MAP_CHAN.size);
        out.channels.push({
          name: cstr(v[0]), label: cstr(v[1]), units: cstr(v[2]), lo: v[3], hi: v[4], warn_lo: v[5], warn_hi: v[6],
          alarm_lo: v[7], alarm_hi: v[8], decimals: v[9], verified: Boolean(v[10]), rate_class: v[11], kind: v[12],
          addr: v[13], length: v[14], little_endian: Boolean(v[15]), prog_off: v[17], prog_len: v[18],
        });
      }
    } else if (kind === F.MS_PROG) {
      out.programs = body;
    } else if (kind === F.MS_ALARM) {
      for (let i = 0; i < count; i++) {
        const v = F.MAP_ALARM.unpack(body, i * F.MAP_ALARM.size);
        out.alarms.push({
          name: cstr(v[0]), label: cstr(v[1]), channel: v[2], severity: v[3], flags: v[4], priority: v[5], low: v[7],
          high: v[8], hysteresis: v[9], hold_s: v[10], clear_hold_s: v[11], rate_low: v[12], rate_high: v[13],
          rate_window_ms: v[14], when: [v[15], v[16]], clear_when: [v[17], v[18]], gate: [v[19], v[20]],
        });
      }
    } else if (kind === F.MS_STALL) {
      const v = F.MAP_STALL.unpack(body);
      out.stall = { rpm_run: v[0], rpm_zero: v[1], link_down_s: v[2], pre_s: v[3], post_s: v[4], rpm_channel: v[5] };
    }
  }
  if (srcLen) {
    const srcBytes = data.subarray(coreLen, coreLen + srcLen);
    const tail = data.subarray(coreLen + srcLen, coreLen + srcLen + 4);
    if (srcBytes.length === srcLen && tail.length === 4
      && new DataView(tail.buffer, tail.byteOffset, 4).getUint32(0, true) === crc32(srcBytes)
      && equalBytes(sha256(srcBytes), sha)) {
      out.source = fromUtf8(srcBytes);
    }
  }
  return out;
}

/** The map a HANDHELD.MAP was made from, if its text is inside. */
export function mapOfFile(mf: MapFile): HhMap | null {
  return mf.source ? resolveMap(mf.source, mf.variant || null) : null;
}

/** The core of a map as a C initialiser (the firmware's built-in map, hh_default_map.inc). */
export function cArray(map: HhMap, source?: string | null): string {
  const data = buildMap(map, source);
  const coreLen = F.MAP_HDR.unpack(data)[3] as number;
  const core = data.subarray(0, coreLen);
  const lines = ['/* Generated by `hondalog-handheld export-map --c-array` from hondalog/data/addressmap.toml.',
    ` * Map hash ${map.versionHash}. Do not edit: tests/test_hh_map.py checks it is current. */`];
  for (let i = 0; i < core.length; i += 16) {
    lines.push('    ' + Array.from(core.subarray(i, i + 16), (b) => `0x${b.toString(16).padStart(2, '0')},`).join(' '));
  }
  return lines.join('\n') + '\n';
}

/** What ``export-map`` writes onto a card: the map, a default HANDHELD.CFG (unless one exists). */
export function exportFiles(map: HhMap, opts: { stall?: StallOptions | null; haveCfg?: boolean } = {}):
  { files: [string, Uint8Array][]; report: ExportReport } {
  const report = newReport();
  const files: [string, Uint8Array][] = [[MAP_NAME, buildMap(map, map.text, opts.stall, report)]];
  if (!opts.haveCfg) files.push([CFG_NAME, utf8(DEFAULT_CFG)]);
  return { files, report };
}
