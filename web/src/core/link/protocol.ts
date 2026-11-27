/**
 * Honda diagnostic (K-line) framing. Pure functions, no I/O.
 * Port of hondalog/protocol.py.
 *
 *     request:  20 05 ADDR LEN CKSUM          (05 = total request length)
 *     reply:    00 LEN+3 DATA*LEN CKSUM       (LEN+3 = total reply length)
 *
 * In both directions the checksum makes every byte of the frame sum to 0x00
 * modulo 256. The K-line is one wire, so a KKL cable hears its own
 * transmission: the request comes back as an echo before the ECU's reply.
 */

import type { Framing } from '../types';

/** Everything the Python Framing dataclass has. `Framing` (types.ts) is the
 *  shared subset; the link needs the rest too. */
export interface FullFraming extends Framing {
  requestLen: number;          // 2nd request byte: total request length
  replyLenExtra: number;       // 2nd reply byte = data length + this
  maxReadLen: number;          // largest LEN to ask for in one request
  initDelayS: number;
  replyTimeoutS: number;       // ceiling (and start) of the adaptive response timeout
  adaptiveTiming: boolean;
  retries: number;             // extra attempts per read
  rateFastHz: number;
  rateMediumHz: number;
  rateSlowHz: number;
}

export const DEFAULT_FRAMING: Readonly<FullFraming> = Object.freeze({
  baud: 9600,
  cmdRead: 0x20,
  requestLen: 5,
  replyHeader: 0x00,
  replyLenExtra: 3,
  maxReadLen: 16,
  initSequence: '68 6a f5 af bf b3 b2 c1 db b3 e9',
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

const KNOWN = new Set(Object.keys(DEFAULT_FRAMING));

function camel(key: string): string {
  return key.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
}

/** A copy with values replaced from a [protocol] table. Takes the TOML's
 *  snake_case keys (`reply_timeout_s`) or camelCase (`replyTimeoutS`).
 *  `init_sequence` may be a hex string or bytes. Unknown keys throw. */
export function withOverrides(base: Readonly<FullFraming>, table: Record<string, unknown> | Partial<FullFraming> | undefined): FullFraming {
  const out: FullFraming = { ...base };
  if (!table) return out;
  for (const [rawKey, value] of Object.entries(table)) {
    if (value === undefined) continue;
    const key = camel(rawKey);
    if (!KNOWN.has(key)) throw new Error(`unknown protocol setting: ${rawKey}`);
    if (key === 'initSequence') {
      out.initSequence = typeof value === 'string' ? fmtHex(parseHex(value)) : fmtHex(Uint8Array.from(value as ArrayLike<number>));
    } else if (key === 'adaptiveTiming') {
      out.adaptiveTiming = Boolean(value);
    } else {
      (out as unknown as Record<string, number>)[key] = Number(value);
    }
  }
  return out;
}

/** The init (wake) bytes of a framing; empty = send none. */
export function initBytes(f: Pick<Framing, 'initSequence'>): Uint8Array {
  return parseHex(f.initSequence ?? '');
}

export const REQUEST_BYTES = 5;   // 20 05 addr len cksum: echoed back on the one-wire K-line
export const REPLY_OVERHEAD = 3;  // header + length byte + checksum around the data

// ---- errors -----------------------------------------------------------------

export type ProtocolErrorKind = 'NoReply' | 'ShortReply' | 'BadChecksum' | 'BadFrame' | 'ProtocolError';

/** Base class for reply problems. `kind` is the Python class name (stable
 *  under minification, unlike `constructor.name`). */
export class ProtocolError extends Error {
  readonly kind: ProtocolErrorKind = 'ProtocolError';
  constructor(message: string) {
    super(message);
    this.name = 'ProtocolError';
  }
}
/** Nothing came back after the echo (or nothing at all). */
export class NoReply extends ProtocolError {
  override readonly kind = 'NoReply' as const;
  constructor(m: string) { super(m); this.name = 'NoReply'; }
}
/** A reply started but did not finish before the timeout. */
export class ShortReply extends ProtocolError {
  override readonly kind = 'ShortReply' as const;
  constructor(m: string) { super(m); this.name = 'ShortReply'; }
}
/** A full-length reply arrived but its checksum is wrong. */
export class BadChecksum extends ProtocolError {
  override readonly kind = 'BadChecksum' as const;
  constructor(m: string) { super(m); this.name = 'BadChecksum'; }
}
/** Bytes arrived but nothing in them looks like a reply. */
export class BadFrame extends ProtocolError {
  override readonly kind = 'BadFrame' as const;
  constructor(m: string) { super(m); this.name = 'BadFrame'; }
}

// ---- byte helpers -----------------------------------------------------------

export function bytes(...values: number[]): Uint8Array {
  return Uint8Array.from(values);
}

export function concat(...parts: ArrayLike<number>[]): Uint8Array {
  let n = 0;
  for (const p of parts) n += p.length;
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

export function equal(a: ArrayLike<number>, b: ArrayLike<number>): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/** Python's bytes.find. */
export function indexOf(hay: ArrayLike<number>, needle: ArrayLike<number>, from = 0): number {
  const n = needle.length;
  if (n === 0) return from <= hay.length ? from : -1;
  outer: for (let i = from; i + n <= hay.length; i++) {
    for (let j = 0; j < n; j++) if (hay[i + j] !== needle[j]) continue outer;
    return i;
  }
  return -1;
}

function sum(data: ArrayLike<number>): number {
  let s = 0;
  for (let i = 0; i < data.length; i++) s += data[i]!;
  return s;
}

// ---- checksum ---------------------------------------------------------------

/** Byte that makes `data + checksum` sum to 0 modulo 256. */
export function checksum(data: ArrayLike<number>): number {
  return (-sum(data)) & 0xff;
}

export function withChecksum(data: ArrayLike<number>): Uint8Array {
  return concat(data, [checksum(data)]);
}

/** True if a complete frame (checksum included) sums to 0 modulo 256. */
export function verify(frame: ArrayLike<number>): boolean {
  return frame.length > 0 && (sum(frame) & 0xff) === 0;
}

// ---- requests ---------------------------------------------------------------

type ReqFraming = Pick<FullFraming, 'cmdRead' | 'requestLen'>;
type ReplyFraming = Pick<FullFraming, 'replyHeader' | 'replyLenExtra'>;

export function buildReadRequest(addr: number, length: number, framing: ReqFraming = DEFAULT_FRAMING): Uint8Array {
  if (!(Number.isInteger(addr) && addr >= 0 && addr <= 0xff)) throw new RangeError(`address out of range: ${addr}`);
  if (!(Number.isInteger(length) && length >= 1 && length <= 0xff)) throw new RangeError(`length out of range: ${length}`);
  return withChecksum([framing.cmdRead, framing.requestLen, addr, length]);
}

/** Total reply bytes expected for a read of `length` data bytes. */
export function replySize(length: number): number {
  return length + REPLY_OVERHEAD;
}

/** Frame data the way the ECU does. Used by the simulator and the tests. */
export function buildReply(data: ArrayLike<number>, framing: ReplyFraming = DEFAULT_FRAMING): Uint8Array {
  const header = framing.replyHeader >= 0 ? framing.replyHeader : 0x00;
  const lengthByte = (data.length + framing.replyLenExtra) & 0xff;
  return withChecksum(concat([header, lengthByte], data));
}

// ---- replies ----------------------------------------------------------------

/** Drop our own request from the front of what was received. Returns
 *  [rest, echoSeen]. Junk in front of the echo goes with it. If the echo is
 *  missing (a cable that does not echo) the buffer comes back whole. */
export function stripEcho(buf: Uint8Array, request: Uint8Array): [Uint8Array, boolean] {
  const pos = request.length ? indexOf(buf, request) : -1;
  if (pos < 0) return [buf.slice(), false];
  return [buf.slice(pos + request.length), true];
}

function* candidates(rest: Uint8Array, length: number, framing: ReplyFraming): Generator<number> {
  const wantLen = (length + framing.replyLenExtra) & 0xff;
  for (let i = 0; i < rest.length; i++) {
    if (framing.replyHeader >= 0 && rest[i] !== framing.replyHeader) continue;
    if (i + 1 < rest.length && rest[i + 1] !== wantLen) continue;
    yield i;
  }
}

/** Scan for a complete, checksum-valid reply. Returns the data bytes. */
export function findReply(rest: Uint8Array, length: number, framing: ReplyFraming = DEFAULT_FRAMING): Uint8Array | null {
  const size = replySize(length);
  for (const i of candidates(rest, length, framing)) {
    const frame = rest.subarray(i, i + size);
    if (frame.length === size && verify(frame)) return frame.slice(2, -1);
  }
  return null;
}

/** Extract the data bytes of a read reply from raw received bytes. Skips the
 *  echo and any junk before the frame. Throws a ProtocolError subclass that
 *  says what went wrong. */
export function parseReply(buf: Uint8Array, request: Uint8Array, length: number,
  framing: ReplyFraming = DEFAULT_FRAMING): Uint8Array {
  const [rest, echoSeen] = stripEcho(buf, request);
  const data = findReply(rest, length, framing);
  if (data !== null) return data;

  if (!buf.length) {
    throw new NoReply('no bytes at all, not even the echo (cable unplugged, or no 12 V on the OBD2 plug)');
  }
  if (!rest.length) throw new NoReply('echo only, the ECU did not answer');

  const size = replySize(length);
  const all = [...candidates(rest, length, framing)];
  const full = all.filter((i) => rest.length - i >= size);
  if (full.length) {
    const frame = rest.subarray(full[0]!, full[0]! + size);
    throw new BadChecksum(`bad checksum in reply ${fmtHex(frame)}`);
  }
  if (all.length) throw new ShortReply(`reply cut short: got ${fmtHex(rest)}, wanted ${size} bytes`);
  const note = echoSeen ? '' : ' (no echo seen)';
  throw new BadFrame(`no reply frame in ${fmtHex(rest)}${note}`);
}

/** Find a checksum-valid frame of unknown length (for the hex console). */
export function findAnyFrame(rest: Uint8Array, framing: ReplyFraming = DEFAULT_FRAMING): Uint8Array | null {
  for (let i = 0; i < rest.length - 2; i++) {
    if (framing.replyHeader >= 0 && rest[i] !== framing.replyHeader) continue;
    const size = rest[i + 1]! - framing.replyLenExtra + 3;
    if (size >= 3 && i + size <= rest.length && verify(rest.subarray(i, i + size))) {
      return rest.slice(i, i + size);
    }
  }
  return null;
}

// ---- hex helpers ------------------------------------------------------------

/** Parse '20 05 00 10', '20,05,0x00,10' or '20050010' into bytes. */
export function parseHex(text: string): Uint8Array {
  const cleaned = text.replace(/,/g, ' ').replace(/0x/g, ' ').replace(/0X/g, ' ');
  const out: number[] = [];
  for (let token of cleaned.split(/\s+/).filter(Boolean)) {
    if (token.length % 2) token = '0' + token;
    if (!/^[0-9a-fA-F]+$/.test(token)) throw new Error(`bad hex: ${token}`);
    for (let i = 0; i < token.length; i += 2) out.push(parseInt(token.slice(i, i + 2), 16));
  }
  return Uint8Array.from(out);
}

export function fmtHex(data: ArrayLike<number>): string {
  return Array.from(data, (b) => b.toString(16).toUpperCase().padStart(2, '0')).join(' ');
}
