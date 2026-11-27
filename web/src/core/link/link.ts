/**
 * Request/reply exchanges on a Transport. Port of the `Link` half of
 * hondalog/transport.py: Honda read requests with adaptive timeouts, echo
 * checking, resync, retry with backoff, counters and frame timestamps.
 * Refuses to send anything but read commands (and the wake frame) until the
 * expert unlock is on.
 *
 * Transport.read(max, timeoutMs) is used as Python's `read_some`: it should
 * resolve as soon as any bytes are there (the sim and the serial transports
 * do). A transport that always waits the full timeout still works, only
 * slower. The link never changes the port's settings per byte (the
 * 2026-11-26 Windows fix lives in the platform transports: one timeout per
 * call, no reconfiguration while our request is being shifted out).
 */

import type { Transport } from '../types';
import { REAL, type Clock } from './clock';
import {
  BadChecksum, DEFAULT_FRAMING, NoReply, ProtocolError, REPLY_OVERHEAD, ShortReply,
  buildReadRequest, concat, equal, findReply, fmtHex, indexOf, initBytes, parseReply,
  stripEcho, verify, type FullFraming,
} from './protocol';
import { AdaptiveTiming, LinkCounters } from './timing';

/** The port itself failed (unplugged, permission denied, ...). */
export class TransportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'TransportError';
  }
}

/** A frame that is not a read was stopped by the read-only guard. */
export class CommandBlocked extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'CommandBlocked';
  }
}

export const DEFAULT_USB_LATENCY_S = 0.016;   // FTDI default latency_timer
export const LOW_USB_LATENCY_S = 0.001;

/** USB receive latency of a transport: its own `usbLatencyS` if it has one
 *  (SimTransport does), else the FTDI default (Web Serial cannot lower it). */
export function usbLatencyOf(t: Transport): number {
  const v = (t as { usbLatencyS?: number }).usbLatencyS;
  return typeof v === 'number' ? v : DEFAULT_USB_LATENCY_S;
}

/** One successful block read. Times are clock seconds. */
export interface ReadResult {
  addr: number;
  data: Uint8Array;
  tRequest: number;      // just before the request was written
  tDone: number;         // when the last reply byte was in hand
  latencyS: number;      // estimated age of the data at tDone
  turnaroundS: number;   // last request byte to first reply byte, as seen here
  attempts: number;
}

export type LinkErrorHook = (addr: number, length: number, error: ProtocolError, attempt: number, tRequest: number) => void;

export class Link {
  static readonly BACKOFF_BASE_S = 0.005;
  static readonly BACKOFF_MAX_S = 0.08;
  static readonly RESYNC_MAX_S = 0.25;

  readonly transport: Transport;
  readonly framing: FullFraming;
  readonly clock: Clock;
  readonly usbLatencyS: number;
  readonly stats = new LinkCounters();
  timing: AdaptiveTiming;
  retries: number;
  echoSeen = false;
  /** read-only guard: raw() sends only read requests and the init bytes */
  readOnly = true;
  /** every non-read frame sent after an unlock, for the log header */
  readonly unlockedFrames: Uint8Array[] = [];
  /** called for every failed attempt, retries included. Must not throw. */
  onError: LinkErrorHook | null = null;

  private readonly init: Uint8Array;
  private echoMode: boolean | null = null;   // null = not known yet
  private nextTx = 0;
  private lastTxEnd = 0;
  private guardUntil = 0;
  private readonly requests = new Map<number, Uint8Array>();

  /** `clock` defaults to the transport's own (SimTransport has one), else the real clock. */
  constructor(transport: Transport, framing: FullFraming = DEFAULT_FRAMING, clock?: Clock,
    usbLatencyS?: number) {
    this.transport = transport;
    this.framing = framing;
    this.clock = clock ?? (transport as { clock?: Clock }).clock ?? REAL;
    this.usbLatencyS = usbLatencyS ?? usbLatencyOf(transport);
    this.timing = new AdaptiveTiming(framing, this.usbLatencyS);
    this.retries = Math.max(0, Math.trunc(framing.retries));
    this.init = initBytes(framing);
  }

  // -- housekeeping --

  /** Start measuring again (after a reconnect or a protocol switch). */
  reset(): void {
    this.timing = new AdaptiveTiming(this.framing, this.usbLatencyS);
    this.echoMode = null;
    this.nextTx = 0;
    this.guardUntil = 0;
  }

  /** Expert unlock: let raw() send frames that are not reads. */
  unlock(on = true): void {
    this.readOnly = !on;
  }

  /** True if the read-only guard lets these bytes onto the wire. */
  allowed(data: Uint8Array): boolean {
    const f = this.framing;
    if (!data.length) return true;
    if (this.init.length && equal(data, this.init)) return true;
    return data.length === f.requestLen && data[0] === f.cmdRead && data[1] === f.requestLen && verify(data);
  }

  private readSome(size: number, timeoutS: number): Promise<Uint8Array> {
    return this.transport.read(size, Math.max(0, timeoutS) * 1000);
  }

  /** Up to `size` bytes; waits up to `timeoutS` for all of them. */
  async readFor(size: number, timeoutS: number): Promise<Uint8Array> {
    const parts: Uint8Array[] = [];
    let n = 0;
    const deadline = this.clock.now() + Math.max(0, timeoutS);
    while (n < size) {
      const remaining = deadline - this.clock.now();
      const chunk = await this.readSome(size - n, Math.max(0, remaining));
      parts.push(chunk);
      n += chunk.length;
      if (!chunk.length && remaining <= 0) break;
    }
    return concat(...parts);
  }

  /** Send the wake-up sequence and throw away whatever comes back. */
  async wake(): Promise<void> {
    const seq = this.init;
    if (!seq.length) return;
    this.stats.wakes += 1;
    if (this.guardUntil) await this.settle();
    await this.transport.drain();
    await this.transport.write(seq);
    await this.clock.sleep(this.framing.initDelayS);
    await this.readFor(256, 0.05);
    this.nextTx = this.clock.now() + this.timing.gap;
  }

  private txTime(nbytes: number): number {
    return (nbytes * 10.0) / this.framing.baud;
  }

  /** Drain the line until it has been silent for idleGap. After a request
   *  that got no answer, pass quietUntil: the line is then watched until that
   *  time for a reply that is merely late, and the wait ends early once such
   *  a reply has come and gone. Returns the number of bytes discarded. */
  async resync(quietUntil = 0): Promise<number> {
    const clock = this.clock;
    const idle = this.timing.idleGap;
    const end = Math.max(clock.now() + Link.RESYNC_MAX_S, quietUntil);
    let dropped = 0;
    for (;;) {
      const now = clock.now();
      if (now >= end) break;
      const wait = dropped || now + idle >= quietUntil ? idle : quietUntil - now;
      const chunk = await this.readSome(256, wait);
      if (chunk.length) dropped += chunk.length;
      else if (dropped || clock.now() >= quietUntil) break;
    }
    this.stats.resyncs += 1;
    this.stats.discardedBytes += dropped;
    this.nextTx = clock.now() + this.timing.gap;
    return dropped;
  }

  /** Finish the late-reply watch left behind by an unanswered request. */
  private async settle(): Promise<void> {
    const until = this.guardUntil;
    this.guardUntil = 0;
    await this.resync(until);
  }

  // -- reads --

  /** Read `length` RAM bytes at `addr`. Throws ProtocolError. */
  async readMem(addr: number, length: number): Promise<Uint8Array> {
    return (await this.readBlock(addr, length)).data;
  }

  /** Read with timestamps. Retries with backoff, resyncing in between;
   *  throws the last ProtocolError if every attempt fails. */
  async readBlock(addr: number, length: number, retries?: number | null): Promise<ReadResult> {
    const key = addr * 256 + length;
    let request = this.requests.get(key);
    if (!request) {
      request = buildReadRequest(addr, length, this.framing);
      this.requests.set(key, request);
    }
    const attempts = 1 + (retries ?? this.retries);
    let attempt = 0;
    for (;;) {
      attempt += 1;
      let error: ProtocolError;
      try {
        return await this.exchange(addr, length, request, attempt);
      } catch (exc) {
        if (exc instanceof NoReply) {
          // Nothing came back. The caller hears about it now, but the line
          // is watched for a late reply before anything is sent again.
          error = exc;
          this.guardUntil = this.lastTxEnd + this.timing.lateGuard;
        } else if (exc instanceof ProtocolError) {
          error = exc;
          await this.resync();
        } else {
          throw exc;
        }
      }
      if (this.onError) {
        try {
          this.onError(addr, length, error, attempt, this.lastTxEnd);
        } catch {
          /* a hook must not break the link */
        }
      }
      if (attempt >= attempts) throw error;
      this.stats.retries += 1;
      await this.clock.sleep(Math.min(Link.BACKOFF_MAX_S, Link.BACKOFF_BASE_S * 2 ** (attempt - 1)));
    }
  }

  private async exchange(addr: number, length: number, request: Uint8Array, attempt: number): Promise<ReadResult> {
    const f = this.framing, clock = this.clock, tm = this.timing, stats = this.stats;
    const byteS = tm.byteS;
    const nreq = this.echoMode !== false ? request.length : 0;
    const size = length + REPLY_OVERHEAD;
    const want = nreq + size;
    const wantLen = (length + f.replyLenExtra) & 0xff;
    const header = f.replyHeader;

    if (this.guardUntil) await this.settle();
    const wait = this.nextTx - clock.now();
    if (wait > 0) await clock.sleep(wait);
    const tReq = clock.now();
    await this.transport.write(request);
    stats.requests += 1;

    const txEnd = (this.lastTxEnd = tReq + request.length * byteS);
    let deadline = txEnd + byteS + tm.responseTimeout;
    const hard = tReq + (request.length + size) * byteS + tm.responseTimeout + tm.interByteTimeout;
    const ibt = tm.interByteTimeout;
    let buf = new Uint8Array(Math.max(64, want * 2));
    let n = 0;
    let data: Uint8Array | null = null;
    let started = false;
    let tFirst = 0, lastRx = 0, maxGap = 0;
    let now = tReq;
    const prefixIsEcho = (k: number): boolean => {
      for (let i = 0; i < k; i++) if (buf[i] !== request[i]) return false;
      return true;
    };
    for (;;) {
      now = clock.now();
      const remaining = deadline - now;
      if (remaining <= 0) break;
      const chunk = await this.readSome(Math.max(1, want - n), remaining);
      if (!chunk.length) continue;
      now = clock.now();
      if (n + chunk.length > buf.length) {
        const bigger = new Uint8Array((n + chunk.length) * 2);
        bigger.set(buf.subarray(0, n));
        buf = bigger;
      }
      buf.set(chunk, n);
      n += chunk.length;
      if (started) {
        if (now - lastRx > maxGap) maxGap = now - lastRx;
      } else if (n > nreq || !prefixIsEcho(n)) {
        started = true;   // past the echo, or not an echo at all
        tFirst = now;
      }
      lastRx = now;
      if (started) deadline = Math.min(hard, now + ibt);
      if (n >= want) {
        if (prefixIsEcho(nreq)) {   // the lean, normal path
          const frame = buf.subarray(nreq, nreq + size);
          let s = 0;
          for (let i = 0; i < frame.length; i++) s += frame[i]!;
          if ((header < 0 || frame[0] === header) && frame[1] === wantLen && !(s & 0xff)) {
            data = frame.slice(2, -1);
            break;
          }
        }
        data = findReply(stripEcho(buf.subarray(0, n), request)[0], length, f);
        if (data !== null) break;
      } else if (n >= size && !prefixIsEcho(Math.min(n, nreq))) {
        data = findReply(stripEcho(buf.subarray(0, n), request)[0], length, f);
        if (data !== null) break;
      }
    }

    const got = buf.slice(0, n);
    const echoHere = indexOf(got, request) >= 0;
    if (data === null) {
      this.nextTx = now + tm.gap;
      if (this.echoMode && n && !echoHere) stats.echoMismatches += 1;
      try {
        data = parseReply(got, request, length, f);
      } catch (exc) {
        if (exc instanceof NoReply) {
          stats.timeouts += 1;
          tm.onFailure({ silent: true });
        } else if (exc instanceof ShortReply) {
          stats.timeouts += 1;
          stats.shortReplies += 1;
          tm.onFailure({ short: true });
        } else if (exc instanceof BadChecksum) {
          stats.checksumFailures += 1;
          tm.onFailure();
        } else if (exc instanceof ProtocolError) {
          stats.badFrames += 1;
          tm.onFailure();
        }
        throw exc;
      }
    }

    if (echoHere) {
      this.echoMode = this.echoSeen = true;
    } else {
      if (this.echoMode) stats.echoMismatches += 1;   // the frame survived, the echo did not
      else this.echoMode = false;
      this.echoSeen = false;
    }
    const onWire = (request.length + size) * byteS;
    const extra = n - (size + (echoHere ? request.length : 0));
    if (extra > 0) stats.junkBytes += extra;
    tm.onSuccess(now - tReq - onWire, maxGap);
    stats.ok += 1;
    this.nextTx = now + tm.gap;
    return {
      addr, data, tRequest: tReq, tDone: now,
      latencyS: size * byteS + tm.usbLatencyS / 2,
      turnaroundS: Math.max(0, tFirst - txEnd - byteS),
      attempts: attempt,
    };
  }

  // -- raw access (hex console, diagnostics) --

  /** Send arbitrary bytes; collect what comes back until the line goes
   *  quiet. Returns [echoSeen, bytesAfterEcho]. Read-only by default:
   *  anything that is not a well-formed read request (or the init sequence)
   *  throws CommandBlocked until unlock(). */
  async raw(data: Uint8Array, quietS = 0.08, maxS = 0.6): Promise<[boolean, Uint8Array]> {
    data = Uint8Array.from(data);
    if (!this.allowed(data)) {
      if (this.readOnly) {
        const f = this.framing;
        const h = (v: number) => v.toString(16).toUpperCase().padStart(2, '0');
        throw new CommandBlocked(`blocked ${fmtHex(data)}: only read requests (${h(f.cmdRead)} ${h(f.requestLen)} addr len cksum) are sent unless the expert unlock is on`);
      }
      this.unlockedFrames.push(data);
    }
    const clock = this.clock;
    if (this.guardUntil) await this.settle();
    const wait = this.nextTx - clock.now();
    if (wait > 0) await clock.sleep(wait);
    await this.transport.drain();
    await this.transport.write(data);
    const start = clock.now();
    let lastRx = start + this.txTime(data.length);
    const parts: Uint8Array[] = [];
    for (;;) {
      const now = clock.now();
      if (now - start > maxS || (now - lastRx > quietS && now > lastRx)) break;
      const chunk = await this.readSome(64, 0.02);
      if (chunk.length) {
        parts.push(chunk);
        lastRx = clock.now();
      }
    }
    this.nextTx = clock.now() + this.timing.gap;
    const [rest, echo] = stripEcho(concat(...parts), data);
    return [echo, rest];
  }
}
