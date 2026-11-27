/**
 * Standard OBD2 over the K-line: ISO 9141-2, with an ISO 14230 (KWP2000)
 * fast-init fallback. Port of the link half of hondalog/obd2.py.
 *
 * 5-baud init, done by break toggling (an FT232 cannot run at 5 baud):
 *
 *     idle >= 300 ms (W5)
 *     address 0x33 at 5 bit/s: 200 ms per bit, start bit low, 8 data bits
 *         LSB first, stop bit high                                    (2.0 s)
 *     ECU: 0x55 sync at 10400 baud after 60-300 ms (W1)
 *     ECU: key byte 1, key byte 2, each within 20 ms (W2, W3)
 *     us:  inverted key byte 2, 25-50 ms later (W4)
 *     ECU: inverted address 0xCC, 25-50 ms later
 *     then requests  68 6A F1 <mode> <pid..> <sum>
 *     and replies    48 6B <ecu> <mode+0x40> <data..> <sum>
 *
 * Timing rules kept: at least 55 ms between the end of a reply and the next
 * request (P3min), and something sent at least every 5 s (P3max) or the ECU
 * drops the session; ``request`` re-runs the init if the session has gone
 * stale and ``keepalive`` exists for callers that sit idle.
 *
 * Browser timing of the bit-banged address: see ``clock.ts``.
 *
 * Use from one task at a time; nothing else may use the port meanwhile.
 */

import type { Transport } from '../types';
import { ByteBuf, concat, hex2, hexLower, startsWith, sum } from './bytes';
import { REAL, usbLatencyS, type Clock } from './clock';

export const ISO_BAUD = 10400;
export const INIT_ADDRESS = 0x33;
export const BIT_S = 0.2; // one bit at 5 baud
export const W5_S = 0.3; // bus idle before an init
export const P3_MIN_S = 0.055; // end of reply -> next request
export const P3_MAX_S = 5.0; // session timeout
export const KEEPALIVE_S = 4.0;
export const HEADER_9141 = Uint8Array.of(0x68, 0x6a, 0xf1);
export const KWP_START = Uint8Array.of(0xc1, 0x33, 0xf1, 0x81, 0x66);
/** Edge error (s) above which slowInit adds a timing note to the result. */
export const EDGE_WARN_S = 0.01;

export class Iso9141Error extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}
/** The wake-up handshake did not complete. */
export class InitError extends Iso9141Error {}
/** The ECU did not answer a request (it may not support it). */
export class NoResponse extends Iso9141Error {}
/** KWP2000 negative response (7F ...). */
export class NegativeResponse extends Iso9141Error {}
/** A destructive service was called without its confirmation. */
export class ConfirmationRequired extends Iso9141Error {}

export interface InitResult {
  method: '5-baud' | 'fast';
  protocol: 'ISO 9141-2' | 'ISO 14230-4 (KWP2000)';
  keyBytes: [number, number];
  /** measured: end of address -> sync byte */
  w1S: number;
  /** stray bytes skipped before the sync */
  garbage: number;
  /** worst measured error of a 5-baud bit edge against the 200 ms grid */
  edgeErrorS: number;
  notes: string[];
}

export function initText(r: InitResult): string {
  return `${r.protocol} via ${r.method} init, key bytes ${hex2(r.keyBytes[0])} ${hex2(r.keyBytes[1])}`;
}

/** ISO 9141-2 / KWP2000 checksum: plain sum modulo 256. */
export function checksum(data: ArrayLike<number>): number {
  return sum(data) & 0xff;
}

/** Mode 01 PID -> number of data bytes (for splitting replies that carry no length). */
const PID_LEN: Record<number, number> = {
  0x00: 4, 0x01: 4, 0x02: 2, 0x03: 2, 0x04: 1, 0x05: 1, 0x06: 1, 0x07: 1, 0x08: 1,
  0x09: 1, 0x0a: 1, 0x0b: 1, 0x0c: 2, 0x0d: 1, 0x0e: 1, 0x0f: 1, 0x10: 2, 0x11: 1,
  0x12: 1, 0x13: 1, 0x14: 2, 0x15: 2, 0x16: 2, 0x17: 2, 0x18: 2, 0x19: 2, 0x1a: 2,
  0x1b: 2, 0x1c: 1, 0x1d: 1, 0x1e: 1, 0x1f: 2, 0x20: 4, 0x21: 2, 0x40: 4,
};

function likelyLen(buf: Uint8Array, i: number): number | null {
  if (i + 4 > buf.length) return null;
  const mode = buf[i + 3]!;
  if (mode === 0x43 || mode === 0x47) return 11;
  if (mode === 0x44) return 5;
  if (mode === 0x41 && i + 5 <= buf.length && buf[i + 4]! in PID_LEN) return 3 + 2 + PID_LEN[buf[i + 4]!]! + 1;
  if (mode === 0x42 && i + 5 <= buf.length && buf[i + 4]! in PID_LEN) return 3 + 3 + PID_LEN[buf[i + 4]!]! + 1;
  return null;
}

function lengthsFor(buf: Uint8Array, i: number): number[] {
  const likely = likelyLen(buf, i);
  const rest = [5, 6, 7, 8, 9, 10, 11].filter((n) => n !== likely);
  return likely ? [likely, ...rest] : rest;
}

/**
 * Cut a run of ISO 9141-2 replies (48 6B xx ... sum) into payloads.
 *
 * The messages carry no length, so the cuts are found by checksum, trying the
 * length the mode and PID imply first. Returns the payloads (mode byte
 * onward) of every message that checks out; damaged bytes are skipped.
 */
export function split9141(buf: Uint8Array): Uint8Array[] {
  const walk = (i: number): Uint8Array[] | null => {
    if (i === buf.length) return [];
    if (buf[i] !== 0x48 || i + 5 > buf.length || buf[i + 1] !== 0x6b) return null;
    for (const n of lengthsFor(buf, i)) {
      if (i + n <= buf.length && (sum(buf, i, i + n - 1) & 0xff) === buf[i + n - 1]) {
        const rest = walk(i + n);
        if (rest !== null) return [buf.slice(i + 3, i + n - 1), ...rest];
      }
    }
    return null;
  };
  const whole = walk(0);
  if (whole !== null) return whole;
  const out: Uint8Array[] = []; // salvage what checks out
  let i = 0;
  while (i + 5 <= buf.length) {
    if (buf[i] === 0x48 && buf[i + 1] === 0x6b) {
      for (const n of lengthsFor(buf, i)) {
        if (i + n <= buf.length && (sum(buf, i, i + n - 1) & 0xff) === buf[i + n - 1]) {
          out.push(buf.slice(i + 3, i + n - 1));
          i += n - 1;
          break;
        }
      }
    }
    i += 1;
  }
  return out;
}

/** Cut a run of KWP2000 replies (fmt tgt src data sum) into payloads. */
export function splitKwp(buf: Uint8Array): Uint8Array[] {
  const out: Uint8Array[] = [];
  let i = 0;
  while (i + 4 <= buf.length) {
    const fmt = buf[i]!;
    let n = fmt & 0x3f;
    let head = fmt & 0x80 ? 3 : 1;
    if (n === 0) {
      if (i + head >= buf.length) break;
      n = buf[i + head]!;
      head += 1;
    }
    const end = i + head + n;
    if (fmt & 0x80 && n && end < buf.length && (sum(buf, i, end) & 0xff) === buf[end]) {
      out.push(buf.slice(i + head, end));
      i = end + 1;
    } else {
      i += 1;
    }
  }
  return out;
}

export interface Iso9141Options {
  clock?: Clock;
  /** Baud rate ``close()`` puts the port back to (the Honda protocol's). */
  hondaBaud?: number;
  trace?: (text: string) => void;
  /** USB latency of the adapter; default from the transport (see clock.ts). */
  usbLatencyS?: number;
}

/** One tester on the K-line speaking ISO 9141-2 (or KWP2000 after a fast init). */
export class Iso9141Link {
  readonly transport: Transport;
  readonly clock: Clock;
  hondaBaud: number;
  trace?: (text: string) => void;
  readonly usbLatencyS: number;
  init: InitResult | null = null;
  kwp = false;
  requests = 0;
  reinits = 0;
  lastActivity = -Infinity;
  nextTx = 0;

  constructor(transport: Transport, opts: Iso9141Options = {}) {
    this.transport = transport;
    this.clock = opts.clock ?? REAL;
    this.hondaBaud = opts.hondaBaud ?? 9600;
    this.trace = opts.trace;
    this.usbLatencyS = opts.usbLatencyS ?? usbLatencyS(transport as Transport & { usbLatencyS?: number });
  }

  private note(text: string): void {
    this.trace?.(text);
  }

  private async readByte(timeoutS: number): Promise<number | null> {
    const data = await this.transport.read(1, Math.max(0, timeoutS) * 1000);
    return data.length ? data[0]! : null;
  }

  get connected(): boolean {
    return this.init !== null && this.clock.now() - this.lastActivity < P3_MAX_S;
  }

  // -- wake-up --

  /** 5-baud init; if that fails and ``allowFast``, try the fast init. */
  async connect(allowFast = true): Promise<InitResult> {
    try {
      return await this.slowInit();
    } catch (slow) {
      if (!(slow instanceof InitError) || !allowFast) throw slow;
      this.note(`5-baud init failed (${slow.message}); trying fast init`);
      try {
        return await this.fastInit();
      } catch (fast) {
        if (!(fast instanceof InitError)) throw fast;
        throw new InitError(`5-baud init: ${slow.message}; fast init: ${fast.message}`);
      }
    }
  }

  /** ISO 9141-2 5-baud init by break toggling. Takes about 2.6 s. */
  async slowInit(address = INIT_ADDRESS): Promise<InitResult> {
    const t = this.transport;
    const clock = this.clock;
    this.init = null;
    await t.setBreak(false);
    await t.setBaud(ISO_BAUD);
    await clock.sleep(W5_S + 0.05); // bus idle
    await t.drain();
    const bits = [0];
    for (let i = 0; i < 8; i++) bits.push((address >> i) & 1);
    bits.push(1);
    this.note(`5-baud init, address ${hex2(address)}: ` + bits.join(''));
    const t0 = clock.now();
    let low = false;
    let edgeError = 0;
    try {
      for (let k = 0; k < bits.length; k++) {
        const bit = bits[k]!;
        if ((bit === 0) !== low) {
          const due = t0 + k * BIT_S;
          await clock.sleepUntil(due);
          low = bit === 0;
          const before = clock.now();
          await t.setBreak(low);
          // the edge happened somewhere in [before, after]: count the worse end
          const after = clock.now();
          edgeError = Math.max(edgeError, Math.abs(before - due), Math.abs(after - due));
        }
      }
    } finally {
      if (low) await t.setBreak(false); // never leave the line held low
    }
    const tEnd = t0 + bits.length * BIT_S;
    // The breaks come back as framing garbage. Drop it late in the stop bit.
    await clock.sleepUntil(tEnd - 0.03);
    await t.drain();

    const timingNote = (): string =>
      edgeError > EDGE_WARN_S
        ? ` (bit edges were up to ${(1000 * edgeError).toFixed(0)} ms off the 200 ms grid: ` +
          'keep the app in front and the machine idle during the init)'
        : '';
    let garbage = 0;
    let deadline = tEnd + 0.3 + 0.2; // W1 max + margin
    for (;;) {
      const b = await this.readByte(deadline - clock.now());
      if (b === null) {
        throw new InitError('no sync byte (0x55) after the address: the ECU did not answer the 5-baud init' + timingNote());
      }
      if (b === 0x55) break;
      garbage += 1;
      if (garbage > 16) {
        throw new InitError('only garbage after the address, no 0x55 sync (wrong baud rate, or a cable that cannot do 10400)');
      }
    }
    const w1 = clock.now() - tEnd;
    const kb1 = await this.readByte(0.02 + 0.1);
    const kb2 = kb1 !== null ? await this.readByte(0.02 + 0.1) : null;
    if (kb1 === null || kb2 === null) throw new InitError('sync byte seen but the key bytes did not follow');
    await clock.sleep(0.03); // W4: 25-50 ms
    await t.write(Uint8Array.of(~kb2 & 0xff));
    const want = ~address & 0xff;
    deadline = clock.now() + 0.05 + 0.15;
    const seen: number[] = [];
    while (seen[seen.length - 1] !== want) {
      const b = await this.readByte(deadline - clock.now());
      if (b === null) {
        throw new InitError(
          `key bytes ${hex2(kb1)} ${hex2(kb2)} seen, but the ECU did not confirm with ${hex2(want)} ` +
            `(got ${hexLower(seen) || 'nothing'})`,
        );
      }
      seen.push(b);
    }
    this.kwp = kb2 === 0x8f;
    const result: InitResult = {
      method: '5-baud',
      protocol: this.kwp ? 'ISO 14230-4 (KWP2000)' : 'ISO 9141-2',
      keyBytes: [kb1, kb2],
      w1S: w1,
      garbage,
      edgeErrorS: edgeError,
      notes: [],
    };
    const usual = (kb1 === 0x08 && kb2 === 0x08) || (kb1 === 0x94 && kb2 === 0x94);
    if (!usual && !this.kwp) result.notes.push(`unusual key bytes ${hex2(kb1)} ${hex2(kb2)}; treated as ISO 9141-2`);
    if (edgeError > EDGE_WARN_S) result.notes.push(`bit edges up to ${(1000 * edgeError).toFixed(1)} ms off the grid`);
    return this.established(result);
  }

  /** ISO 14230 fast init: 25 ms low, 25 ms high, StartCommunication. */
  async fastInit(): Promise<InitResult> {
    const t = this.transport;
    const clock = this.clock;
    this.init = null;
    await t.setBreak(false);
    await t.setBaud(ISO_BAUD);
    await clock.sleep(W5_S + 0.05);
    await t.drain();
    this.note('fast init: 25 ms low, 25 ms high, C1 33 F1 81 66');
    const t0 = clock.now();
    await t.setBreak(true);
    try {
      await clock.sleepUntil(t0 + 0.025);
    } finally {
      await t.setBreak(false);
    }
    await clock.sleepUntil(t0 + 0.05);
    await t.drain();
    await t.write(KWP_START);
    let buf = await this.collect(KWP_START.length, 0.3);
    if (startsWith(buf, KWP_START)) buf = buf.slice(KWP_START.length);
    const replies = splitKwp(buf).filter((p) => p[0] === 0xc1);
    if (!replies.length) {
      throw new InitError('no StartCommunication response (this ECU is probably ISO 9141-2 only, which needs the 5-baud init)');
    }
    const first = replies[0]!;
    this.kwp = true;
    return this.established({
      method: 'fast',
      protocol: 'ISO 14230-4 (KWP2000)',
      keyBytes: [first[1] ?? 0, first[2] ?? 0],
      w1S: 0,
      garbage: 0,
      edgeErrorS: 0,
      notes: [],
    });
  }

  private established(result: InitResult): InitResult {
    const now = this.clock.now();
    this.init = result;
    this.lastActivity = now;
    this.nextTx = now + P3_MIN_S + 2 * this.usbLatencyS;
    this.note(`session up: ${initText(result)}`);
    return result;
  }

  /** Leave the ISO session and put the port back to the Honda baud rate.
   *  There is no "stop" message in ISO 9141-2: the ECU ends the session by
   *  itself after 5 s without a request. */
  async close(): Promise<void> {
    this.init = null;
    try {
      await this.transport.setBreak(false);
    } catch {
      // port may be gone already
    }
    await this.transport.setBaud(this.hondaBaud);
    await this.transport.drain();
  }

  // -- messages --

  frame(payload: Uint8Array): Uint8Array {
    const body = this.kwp ? concat([0xc0 | payload.length, 0x33, 0xf1], payload) : concat(HEADER_9141, payload);
    return concat(body, [checksum(body)]);
  }

  /** Read until the ECU has been quiet for longer than P2max. */
  async collect(echoLen: number, firstTimeoutS: number): Promise<Uint8Array> {
    const clock = this.clock;
    const quiet = 0.06 + 2 * this.usbLatencyS;
    const buf = new ByteBuf();
    let deadline = clock.now() + (echoLen * 10.0) / ISO_BAUD + firstTimeoutS;
    for (;;) {
      const now = clock.now();
      if (now >= deadline) break;
      const chunk = await this.transport.read(64, (deadline - now) * 1000);
      if (chunk.length) {
        buf.push(chunk);
        if (buf.length > echoLen) deadline = clock.now() + quiet;
      }
    }
    return buf.bytes();
  }

  /** Send one service request; return the reply payloads (mode byte first).
   *  Throws NoResponse if nothing valid comes back. Re-runs the init first if
   *  the session has been idle for too long to still be alive. */
  async request(payload: Uint8Array, retries = 1): Promise<Uint8Array[]> {
    if (this.init === null) throw new Iso9141Error('no ISO session: call connect() first');
    const clock = this.clock;
    let attempt = 0;
    for (;;) {
      attempt += 1;
      if (clock.now() - this.lastActivity > P3_MAX_S - 0.5) {
        this.note('session idle too long, re-initialising');
        this.reinits += 1;
        await this.connect(this.init === null || this.init.method === 'fast');
      }
      const wait = this.nextTx - clock.now();
      if (wait > 0) await clock.sleep(wait);
      const frame = this.frame(payload);
      await this.transport.drain();
      await this.transport.write(frame);
      this.requests += 1;
      let buf = await this.collect(frame.length, 0.25);
      const now = clock.now();
      this.nextTx = now; // the quiet wait in collect already covers P3min
      if (startsWith(buf, frame)) buf = buf.slice(frame.length);
      const replies = this.kwp ? splitKwp(buf) : split9141(buf);
      if (replies.length && replies[0]![0] === 0x7f) {
        this.lastActivity = now;
        throw new NegativeResponse(`negative response ${hexLower(replies[0]!)} to ${hexLower(payload)}`);
      }
      const want = (payload[0]! + 0x40) & 0xff;
      const good = replies.filter((r) => r.length && r[0] === want);
      if (good.length) {
        this.lastActivity = now;
        return good;
      }
      if (attempt > retries) {
        throw new NoResponse(`no reply to ${hexLower(payload)}` + (buf.length ? ` (got ${hexLower(buf)})` : ''));
      }
    }
  }

  /** Keep the session open: sends Mode 01 PID 00 if it has been idle for 4 s.
   *  Returns true if a message was sent. */
  async keepalive(): Promise<boolean> {
    if (this.init === null || this.clock.now() - this.lastActivity < KEEPALIVE_S) return false;
    await this.request(Uint8Array.of(0x01, 0x00));
    return true;
  }
}

