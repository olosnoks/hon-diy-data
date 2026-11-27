/**
 * The Honda RAM-read protocol, as far as the diagnostic tools need it: the
 * framing helpers of hondalog/protocol.py and a small fixed-timing request /
 * reply link (the read-only subset of transport.Link).
 *
 *     request:  20 05 ADDR LEN CKSUM
 *     reply:    00 LEN+3 DATA*LEN CKSUM     (every frame sums to 0x00)
 *
 * The live poller uses the link agent's adaptive link (src/core/link/); the
 * first-contact run and the probes take anything that implements
 * ``HondaLink``, and default to ``SimpleHondaLink`` below so this folder has
 * no dependency on the poller.
 */

import type { AddressMap, Transport } from '../types';
import { concat, fmtHex, fromHex, indexOf, startsWith, sum } from '../obd2/bytes';
import { REAL, usbLatencyS, type Clock } from '../obd2/clock';

export interface HondaFraming {
  baud: number;
  cmdRead: number;
  requestLen: number;
  /** -1 = accept any first reply byte */
  replyHeader: number;
  replyLenExtra: number;
  maxReadLen: number;
  initSequence: Uint8Array;
  initDelayS: number;
  replyTimeoutS: number;
  interRequestDelayS: number;
  interByteTimeoutS: number;
  retries: number;
}

export const DEFAULT_FRAMING: HondaFraming = {
  baud: 9600,
  cmdRead: 0x20,
  requestLen: 5,
  replyHeader: 0x00,
  replyLenExtra: 3,
  maxReadLen: 16,
  initSequence: fromHex('686af5afbfb3b2c1dbb3e9'),
  initDelayS: 0.3,
  replyTimeoutS: 0.2,
  interRequestDelayS: 0.002,
  interByteTimeoutS: 0.05,
  retries: 1,
};

/** The stock P2P: Honda reads at 10400 baud after the wake frame (the
 *  built-in address map's [protocol]). */
export const P2P_FRAMING: HondaFraming = { ...DEFAULT_FRAMING, baud: 10400 };

/** Framing from an address map's [protocol] table (types.Framing fields plus
 *  the snake_case keys the TOML may carry). */
export function framingFromMap(map: Pick<AddressMap, 'protocol'> | null | undefined): HondaFraming {
  const p = (map?.protocol ?? {}) as Record<string, unknown>;
  const num = (camel: string, snake: string, dflt: number): number => {
    const v = p[camel] ?? p[snake];
    return typeof v === 'number' ? v : dflt;
  };
  const seq = p['initSequence'] ?? p['init_sequence'];
  return {
    baud: num('baud', 'baud', P2P_FRAMING.baud),
    cmdRead: num('cmdRead', 'cmd_read', DEFAULT_FRAMING.cmdRead),
    requestLen: num('requestLen', 'request_len', DEFAULT_FRAMING.requestLen),
    replyHeader: num('replyHeader', 'reply_header', DEFAULT_FRAMING.replyHeader),
    replyLenExtra: num('replyLenExtra', 'reply_len_extra', DEFAULT_FRAMING.replyLenExtra),
    maxReadLen: num('maxReadLen', 'max_read_len', DEFAULT_FRAMING.maxReadLen),
    initSequence: typeof seq === 'string' ? fromHex(seq) : DEFAULT_FRAMING.initSequence,
    initDelayS: num('initDelayS', 'init_delay_s', DEFAULT_FRAMING.initDelayS),
    replyTimeoutS: num('replyTimeoutS', 'reply_timeout_s', DEFAULT_FRAMING.replyTimeoutS),
    interRequestDelayS: num('interRequestDelayS', 'inter_request_delay_s', DEFAULT_FRAMING.interRequestDelayS),
    interByteTimeoutS: num('interByteTimeoutS', 'inter_byte_timeout_s', DEFAULT_FRAMING.interByteTimeoutS),
    retries: num('retries', 'retries', DEFAULT_FRAMING.retries),
  };
}

// ---------------------------------------------------------------- errors

export class ProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}
/** Nothing came back after the echo (or nothing at all). */
export class NoReply extends ProtocolError {}
/** A reply started but did not finish before the timeout. */
export class ShortReply extends ProtocolError {}
/** A full-length reply arrived but its checksum is wrong. */
export class BadChecksum extends ProtocolError {}
/** Bytes arrived but nothing in them looks like a reply. */
export class BadFrame extends ProtocolError {}
/** raw() refused bytes that are not a read request. */
export class CommandBlocked extends Error {}

// ---------------------------------------------------------------- framing

export const REPLY_OVERHEAD = 3;

/** Byte that makes ``data + checksum`` sum to 0 modulo 256. */
export function hondaChecksum(data: ArrayLike<number>): number {
  return -sum(data) & 0xff;
}

export function verify(frame: ArrayLike<number>): boolean {
  return frame.length > 0 && (sum(frame) & 0xff) === 0;
}

export function buildReadRequest(addr: number, length: number, f: HondaFraming = DEFAULT_FRAMING): Uint8Array {
  if (addr < 0 || addr > 0xff) throw new RangeError(`address out of range: ${addr}`);
  if (length < 1 || length > 0xff) throw new RangeError(`length out of range: ${length}`);
  const body = Uint8Array.of(f.cmdRead, f.requestLen, addr, length);
  return concat(body, [hondaChecksum(body)]);
}

/** Drop our own request from the front of what was received. Returns
 *  [rest, echoSeen]; junk in front of the echo goes with it. */
export function stripEcho(buf: Uint8Array, request: Uint8Array): [Uint8Array, boolean] {
  const pos = request.length ? indexOf(buf, request) : -1;
  if (pos < 0) return [buf, false];
  return [buf.slice(pos + request.length), true];
}

function* candidates(rest: Uint8Array, length: number, f: HondaFraming): Generator<number> {
  const wantLen = (length + f.replyLenExtra) & 0xff;
  for (let i = 0; i < rest.length; i++) {
    if (f.replyHeader >= 0 && rest[i] !== f.replyHeader) continue;
    if (i + 1 < rest.length && rest[i + 1] !== wantLen) continue;
    yield i;
  }
}

/** Scan for a complete, checksum-valid reply. Returns the data bytes. */
export function findReply(rest: Uint8Array, length: number, f: HondaFraming = DEFAULT_FRAMING): Uint8Array | null {
  const size = length + REPLY_OVERHEAD;
  for (const i of candidates(rest, length, f)) {
    const frame = rest.slice(i, i + size);
    if (frame.length === size && verify(frame)) return frame.slice(2, -1);
  }
  return null;
}

/** Data bytes of a read reply, or a ProtocolError that says what went wrong. */
export function parseReply(buf: Uint8Array, request: Uint8Array, length: number, f: HondaFraming = DEFAULT_FRAMING): Uint8Array {
  const [rest, echoSeen] = stripEcho(buf, request);
  const data = findReply(rest, length, f);
  if (data) return data;
  if (!buf.length) throw new NoReply('no bytes at all, not even the echo (cable unplugged, or no 12 V on the OBD2 plug)');
  if (!rest.length) throw new NoReply('echo only, the ECU did not answer');
  const size = length + REPLY_OVERHEAD;
  const all = [...candidates(rest, length, f)];
  const full = all.filter((i) => rest.length - i >= size);
  if (full.length) throw new BadChecksum(`bad checksum in reply ${fmtHex(rest.slice(full[0]!, full[0]! + size))}`);
  if (all.length) throw new ShortReply(`reply cut short: got ${fmtHex(rest)}, wanted ${size} bytes`);
  throw new BadFrame(`no reply frame in ${fmtHex(rest)}${echoSeen ? '' : ' (no echo seen)'}`);
}

// ---------------------------------------------------------------- link

export interface ReadResult {
  addr: number;
  data: Uint8Array;
  tRequest: number;
  tDone: number;
  /** last request byte to first reply byte, as seen here */
  turnaroundS: number;
  attempts: number;
}

/** What the diagnostic tools need from a Honda link. */
export interface HondaLink {
  readonly transport: Transport;
  readonly framing: HondaFraming;
  readonly usbLatencyS: number;
  /** seconds per byte at the framing's baud (8N1) */
  readonly byteS: number;
  /** idle time between frames, and its floor */
  gap: number;
  gapFloor: number;
  /** longest wait for a reply */
  replyTimeoutS: number;
  echoSeen: boolean;
  wake(): Promise<void>;
  readBlock(addr: number, length: number, retries?: number): Promise<ReadResult>;
  /** Send a read request (or the wake frame), collect until quiet:
   *  [echoSeen, bytes after the echo]. */
  raw(data: Uint8Array, quietS?: number, maxS?: number): Promise<[boolean, Uint8Array]>;
}

/** Fixed-timing Honda link: read requests and the wake frame only. */
export class SimpleHondaLink implements HondaLink {
  readonly usbLatencyS: number;
  readonly byteS: number;
  gap: number;
  gapFloor: number;
  replyTimeoutS: number;
  echoSeen = false;
  private nextTx = 0;

  constructor(readonly transport: Transport, readonly framing: HondaFraming, readonly clock: Clock = REAL) {
    this.usbLatencyS = usbLatencyS(transport as Transport & { usbLatencyS?: number });
    this.byteS = 10 / framing.baud;
    this.gap = this.gapFloor = framing.interRequestDelayS;
    this.replyTimeoutS = framing.replyTimeoutS;
  }

  /** True if the read-only guard lets these bytes onto the wire. */
  allowed(data: Uint8Array): boolean {
    const f = this.framing;
    if (!data.length) return true;
    if (f.initSequence.length && data.length === f.initSequence.length && startsWith(data, f.initSequence)) return true;
    return data.length === f.requestLen && data[0] === f.cmdRead && data[1] === f.requestLen && verify(data);
  }

  async wake(): Promise<void> {
    const seq = this.framing.initSequence;
    if (!seq.length) return;
    await this.transport.drain();
    await this.transport.write(seq);
    await this.clock.sleep(this.framing.initDelayS);
    await this.transport.read(256, 50);
    this.nextTx = this.clock.now() + this.gap;
  }

  /** Drain until the line has been quiet for 10 ms (or ``until``). */
  private async resync(until = 0): Promise<void> {
    const clock = this.clock;
    const end = Math.max(clock.now() + 0.25, until);
    let dropped = 0;
    for (;;) {
      const now = clock.now();
      if (now >= end) break;
      const idle = Math.max(0.01, 2 * this.usbLatencyS);
      const wait = dropped || now + idle >= until ? idle : until - now;
      const chunk = await this.transport.read(256, wait * 1000);
      if (chunk.length) dropped += chunk.length;
      else if (dropped || clock.now() >= until) break;
    }
    this.nextTx = clock.now() + this.gap;
  }

  async readBlock(addr: number, length: number, retries?: number): Promise<ReadResult> {
    const request = buildReadRequest(addr, length, this.framing);
    const attempts = 1 + (retries ?? this.framing.retries);
    for (let attempt = 1; ; attempt++) {
      try {
        return await this.exchange(addr, length, request, attempt);
      } catch (e) {
        if (!(e instanceof ProtocolError)) throw e;
        // watch for a late reply before anything is sent again, or it would
        // pass for the answer to the next request
        await this.resync(e instanceof NoReply ? this.lastTxEnd + this.replyTimeoutS : 0);
        if (attempt >= attempts) throw e;
        await this.clock.sleep(Math.min(0.08, 0.005 * 2 ** (attempt - 1)));
      }
    }
  }

  private lastTxEnd = 0;

  private async exchange(addr: number, length: number, request: Uint8Array, attempt: number): Promise<ReadResult> {
    const clock = this.clock;
    const byteS = this.byteS;
    const size = length + REPLY_OVERHEAD;
    const want = request.length + size;
    const wait = this.nextTx - clock.now();
    if (wait > 0) await clock.sleep(wait);
    const tReq = clock.now();
    await this.transport.write(request);
    const txEnd = (this.lastTxEnd = tReq + request.length * byteS);
    const latency = 2 * this.usbLatencyS;
    let deadline = txEnd + byteS + this.replyTimeoutS + latency;
    const hard = tReq + want * byteS + this.replyTimeoutS + this.framing.interByteTimeoutS + latency;
    const ibt = Math.max(this.framing.interByteTimeoutS, 2 * byteS) + latency;
    let buf: Uint8Array = new Uint8Array(0);
    let started = false;
    let tFirst = 0;
    let data: Uint8Array | null = null;
    let now = tReq;
    for (;;) {
      now = clock.now();
      const remaining = deadline - now;
      if (remaining <= 0) break;
      const chunk = await this.transport.read(Math.max(1, want - buf.length), remaining * 1000);
      if (!chunk.length) continue;
      now = clock.now();
      buf = concat(buf, chunk);
      if (!started) {
        const n = buf.length;
        if (n > request.length || !startsWith(request, buf)) {
          started = true; // past the echo, or not an echo at all
          tFirst = now;
        }
      }
      if (started) deadline = Math.min(hard, now + ibt);
      if (buf.length >= size) {
        data = findReply(stripEcho(buf, request)[0], length, this.framing);
        if (data) break;
      }
    }
    this.nextTx = now + this.gap;
    const echoHere = indexOf(buf, request) >= 0;
    if (!data) data = parseReply(buf, request, length, this.framing); // throws
    this.echoSeen = echoHere;
    return { addr, data, tRequest: tReq, tDone: now, turnaroundS: Math.max(0, tFirst - txEnd - byteS), attempts: attempt };
  }

  async raw(data: Uint8Array, quietS = 0.08, maxS = 0.6): Promise<[boolean, Uint8Array]> {
    if (!this.allowed(data)) {
      throw new CommandBlocked(
        `blocked ${fmtHex(data)}: only read requests (${fmtHex([this.framing.cmdRead, this.framing.requestLen])} addr len cksum) are sent`,
      );
    }
    const clock = this.clock;
    const wait = this.nextTx - clock.now();
    if (wait > 0) await clock.sleep(wait);
    await this.transport.drain();
    await this.transport.write(data);
    const start = clock.now();
    let lastRx = start + data.length * this.byteS;
    let buf: Uint8Array = new Uint8Array(0);
    for (;;) {
      const now = clock.now();
      if (now - start > maxS || (now - lastRx > quietS && now > lastRx)) break;
      const chunk = await this.transport.read(64, 20);
      if (chunk.length) {
        buf = concat(buf, chunk);
        lastRx = clock.now();
      }
    }
    this.nextTx = clock.now() + this.gap;
    const [rest, echoSeen] = stripEcho(buf, data);
    return [echoSeen, rest];
  }
}
