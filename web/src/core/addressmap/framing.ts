/**
 * The protocol settings an address map can override (hondalog/protocol.py
 * Framing): defaults, [protocol] overrides, and the hex wake frame parser.
 * Field names are camelCase versions of the Python ones.
 */

import type { Framing } from '../types';

/** Everything in Python's Framing. A superset of the contract's Framing. */
export interface MapFraming extends Framing {
  requestLen: number;
  replyLenExtra: number;
  maxReadLen: number;
  initDelayS: number;
  replyTimeoutS: number;
  adaptiveTiming: boolean;
  retries: number;
  rateFastHz: number;
  rateMediumHz: number;
  rateSlowHz: number;
}

export const DEFAULT_FRAMING: Readonly<MapFraming> = Object.freeze({
  baud: 9600,
  cmdRead: 0x20,
  requestLen: 5,
  replyHeader: 0x00,
  replyLenExtra: 3,
  maxReadLen: 16,
  initSequence: '686af5afbfb3b2c1dbb3e9',
  initDelayS: 0.3,
  replyTimeoutS: 0.2,
  interRequestDelayS: 0.002,
  adaptiveTiming: true,
  minReplyTimeoutS: 0.02,
  interByteTimeoutS: 0.05,
  maxInterRequestDelayS: 0.05,
  retries: 1,
  turnaroundS: 0.002,
  rateFastHz: 20.0,
  rateMediumHz: 5.0,
  rateSlowHz: 1.0,
});

/** 20 05 addr len cksum: echoed back on the one-wire K-line. */
export const REQUEST_BYTES = 5;
/** header + length byte + checksum around the data. */
export const REPLY_OVERHEAD = 3;

/** Python [protocol] key -> MapFraming field, in Framing's field order. */
export const PROTOCOL_KEYS: Readonly<Record<string, keyof MapFraming>> = Object.freeze({
  baud: 'baud',
  cmd_read: 'cmdRead',
  request_len: 'requestLen',
  reply_header: 'replyHeader',
  reply_len_extra: 'replyLenExtra',
  max_read_len: 'maxReadLen',
  init_sequence: 'initSequence',
  init_delay_s: 'initDelayS',
  reply_timeout_s: 'replyTimeoutS',
  inter_request_delay_s: 'interRequestDelayS',
  adaptive_timing: 'adaptiveTiming',
  min_reply_timeout_s: 'minReplyTimeoutS',
  inter_byte_timeout_s: 'interByteTimeoutS',
  max_inter_request_delay_s: 'maxInterRequestDelayS',
  retries: 'retries',
  turnaround_s: 'turnaroundS',
  rate_fast_hz: 'rateFastHz',
  rate_medium_hz: 'rateMediumHz',
  rate_slow_hz: 'rateSlowHz',
});

/** protocol.parse_hex: '20 05 00 10', '20,05,0x00,10' or '20050010' -> bytes. */
export function parseHex(text: string): Uint8Array {
  const cleaned = text.replace(/,/g, ' ').replace(/0x/g, ' ').replace(/0X/g, ' ');
  const out: number[] = [];
  for (let token of cleaned.split(/\s+/).filter(Boolean)) {
    if (token.length % 2) token = '0' + token;
    for (let i = 0; i < token.length; i += 2) {
      const pair = token.slice(i, i + 2);
      if (!/^[0-9a-fA-F]{2}$/.test(pair)) {
        throw new Error(`non-hexadecimal number found in fromhex() arg at position ${i + (/^[0-9a-fA-F]/.test(pair) ? 1 : 0)}`);
      }
      out.push(parseInt(pair, 16));
    }
  }
  return Uint8Array.from(out);
}

export function toHex(bytes: ArrayLike<number>): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

/** Framing.with_overrides: a copy with values from a [protocol] table. */
export function withOverrides(base: Readonly<MapFraming>, table: unknown): MapFraming {
  if (table === null || typeof table !== 'object' || Array.isArray(table)) {
    throw new Error(`'${Array.isArray(table) ? 'list' : typeof table === 'string' ? 'str' : typeof table}' object has no attribute 'items'`);
  }
  const out: MapFraming = { ...base };
  for (const [key, raw] of Object.entries(table as Record<string, unknown>)) {
    const field = Object.prototype.hasOwnProperty.call(PROTOCOL_KEYS, key) ? PROTOCOL_KEYS[key] : undefined;
    if (!field) throw new Error(`unknown protocol setting: ${key}`);
    let value: unknown = typeof raw === 'bigint' ? Number(raw) : raw;
    if (field === 'initSequence') {
      if (typeof value === 'string') value = toHex(parseHex(value));
      else if (Array.isArray(value)) value = toHex(value.map((b) => Number(b) & 0xff));
      else throw new Error(`cannot convert '${typeof value}' object to bytes`);
    }
    (out as unknown as Record<string, unknown>)[field] = value;
  }
  return out;
}
