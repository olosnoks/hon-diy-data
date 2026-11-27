/**
 * Link counters and the adaptive timeouts (port of hondalog/link/timing.py).
 *
 * responseTimeout   wait after the request has left the wire for the first
 *                   reply byte; tracks the measured per-request overhead with
 *                   a smoothed mean and deviation (RFC 6298). Doubles on a
 *                   timeout. Between minReplyTimeoutS and replyTimeoutS.
 * interByteTimeout  once a reply has started, how long a silence ends it.
 *                   Doubles when a reply is cut short.
 * gap               idle time between frames. Grows only when errors come in
 *                   clusters, shrinks back once the line is clean.
 * lateGuard         after a request that got no answer at all, how long the
 *                   line is watched before the next request goes out (a late
 *                   reply carries no address and could pass for the wrong one).
 */

import type { FullFraming } from './protocol';

const ERR_CLUSTER = 0.2;   // smoothed failure share above which the gap grows
const ERR_CLEAN = 0.05;    // ... and below which it shrinks again

/** Counters since connect (Python LinkStats, snake_case -> camelCase). */
export class LinkCounters {
  requests = 0;          // exchanges started (retries included)
  ok = 0;                // exchanges that returned a valid frame
  timeouts = 0;          // no reply, or a reply that stopped short
  shortReplies = 0;      // the "stopped short" part of timeouts
  checksumFailures = 0;  // full-length reply, wrong checksum
  badFrames = 0;         // bytes arrived, none of them a reply
  echoMismatches = 0;    // our own bytes came back damaged or not at all
  junkBytes = 0;         // extra bytes skipped around an otherwise good frame
  resyncs = 0;           // times the line was drained back to idle
  discardedBytes = 0;    // bytes thrown away while draining
  retries = 0;           // second and later attempts
  wakes = 0;             // init sequences sent
  reconnects = 0;        // port reopened after it was lost

  get errors(): number {
    return this.timeouts + this.checksumFailures + this.badFrames;
  }

  asDict(): Record<string, number> {
    return {
      requests: this.requests, ok: this.ok, timeouts: this.timeouts,
      shortReplies: this.shortReplies, checksumFailures: this.checksumFailures,
      badFrames: this.badFrames, echoMismatches: this.echoMismatches,
      junkBytes: this.junkBytes, resyncs: this.resyncs, discardedBytes: this.discardedBytes,
      retries: this.retries, wakes: this.wakes, reconnects: this.reconnects, errors: this.errors,
    };
  }
}

export class AdaptiveTiming {
  readonly byteS: number;
  readonly adaptive: boolean;
  readonly usbLatencyS: number;
  readonly timeoutCeiling: number;
  readonly timeoutFloor: number;
  readonly ibtCeiling: number;
  readonly gapFloor: number;
  readonly gapCeiling: number;
  srtt: number | null = null;
  rttvar = 0;
  peakChunkGap = 0;
  responseTimeout: number;
  interByteTimeout: number;
  gap: number;
  errorShare = 0;

  constructor(framing: FullFraming, usbLatencyS = 0.001) {
    this.byteS = 10.0 / framing.baud;
    this.adaptive = framing.adaptiveTiming;
    this.usbLatencyS = Math.max(0, usbLatencyS);
    this.timeoutCeiling = framing.replyTimeoutS;
    this.timeoutFloor = Math.min(framing.minReplyTimeoutS, framing.replyTimeoutS);
    this.ibtCeiling = Math.max(framing.interByteTimeoutS, 2 * this.byteS);
    this.gapFloor = Math.max(0, framing.interRequestDelayS);
    this.gapCeiling = Math.max(this.gapFloor, framing.maxInterRequestDelayS);
    this.responseTimeout = this.timeoutCeiling;
    this.interByteTimeout = this.ibtCeiling;
    this.gap = this.gapFloor;
  }

  /** Forget what was measured (after a reconnect or a protocol switch). */
  reset(): void {
    this.srtt = null;
    this.rttvar = 0;
    this.peakChunkGap = 0;
    this.responseTimeout = this.timeoutCeiling;
    this.interByteTimeout = this.ibtCeiling;
    this.gap = this.gapFloor;
    this.errorShare = 0;
  }

  /** The ECU is back after an outage: drop the backed-off gap. */
  relax(): void {
    this.errorShare = 0;
    this.gap = this.gapFloor;
  }

  /** overheadS: exchange time minus the pure byte time on the wire.
   *  chunkGapS: longest silence inside the reply. */
  onSuccess(overheadS: number, chunkGapS: number): void {
    const r = Math.max(0, overheadS);
    if (this.srtt === null) {
      this.srtt = r;
      this.rttvar = r / 2;
    } else {
      this.rttvar = 0.75 * this.rttvar + 0.25 * Math.abs(this.srtt - r);
      this.srtt = 0.875 * this.srtt + 0.125 * r;
    }
    this.peakChunkGap = Math.max(chunkGapS, 0.95 * this.peakChunkGap);
    this.errorShare *= 0.9;
    if (!this.adaptive) return;
    const want = 2 * this.srtt + 4 * this.rttvar + 2 * this.byteS + 2 * this.usbLatencyS;
    this.responseTimeout = Math.min(this.timeoutCeiling, Math.max(this.timeoutFloor, want));
    const ibt = 4 * this.peakChunkGap + 2 * this.byteS + 2 * this.usbLatencyS;
    this.interByteTimeout = Math.min(this.ibtCeiling, Math.max(this.ibtFloor, ibt));
    if (this.errorShare < ERR_CLEAN && this.gap > this.gapFloor) {
      this.gap = Math.max(this.gapFloor, this.gap * 0.8);
    }
  }

  /** A request failed. short: the reply started, then stopped. silent:
   *  nothing but (maybe) the echo came back. */
  onFailure(opts: { short?: boolean; silent?: boolean } = {}): void {
    this.errorShare = 0.9 * this.errorShare + 0.1;
    if (!this.adaptive) return;
    if (opts.silent) this.responseTimeout = Math.min(this.timeoutCeiling, this.responseTimeout * 2);
    if (opts.short) this.interByteTimeout = Math.min(this.ibtCeiling, this.interByteTimeout * 2);
    if (this.errorShare > ERR_CLUSTER) this.gap = Math.min(this.gapCeiling, Math.max(this.gap * 2, 0.002));
  }

  get ibtFloor(): number {
    return Math.min(this.ibtCeiling, 4 * this.byteS + 2 * this.usbLatencyS);
  }

  /** Silence that counts as "the line is idle" when resynchronising. */
  get idleGap(): number {
    return 4 * this.byteS + 2 * this.usbLatencyS;
  }

  /** How long after an unanswered request a late reply is still expected. */
  get lateGuard(): number {
    return this.timeoutCeiling;
  }

  /** Measured per-request overhead, or 0 before the first good exchange. */
  get overheadS(): number {
    return this.srtt || 0;
  }

  asDict(): Record<string, number> {
    return {
      overheadMs: 1000 * this.overheadS,
      overheadDevMs: 1000 * this.rttvar,
      responseTimeoutMs: 1000 * this.responseTimeout,
      interByteTimeoutMs: 1000 * this.interByteTimeout,
      gapMs: 1000 * this.gap,
    };
  }
}
