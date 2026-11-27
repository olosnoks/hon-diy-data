/**
 * Relay: the laptop's own ECU reads to the handheld (hondalog/handheld/relay.py,
 * SERIAL.md "Relay: the laptop's ECU data to the handheld").
 *
 *     onBlock(read)          -> RELAY_READ  0x50   (host_us = request out, latency_us)
 *     onPid(t, pid, data)    -> RELAY_ISO   0x51   (mode 0x41, OBD2-only mode)
 *     onSample(sample)       -> RELAY_TICK  0x52   (link up samples only)
 *     poller state           -> RELAY_LINK  0x53   (on every change and once a second)
 *
 * The hooks only pack a frame into a bounded queue (the oldest is dropped and counted
 * when it is full): a slow or stalled USB link never holds up the K-line. An async
 * writer sends. Relaying only happens to a two-way handheld whose HELLO sets CAP_RELAY
 * and runs the laptop's map (HELLO map hash = the map's version hash); ``force`` skips
 * those two checks for testing. ``status`` says why not otherwise.
 */

import type { Clock } from './port';
import { realClock } from './port';
import * as P from './serial_proto';

export const LINK_EVERY_S = 1.0;
export const QUEUE_MAX = 1024;

export const TOO_OLD = 'handheld: firmware too old for relay';
export const OWN_KLINE = 'handheld: reads the ECU on its own cable (no relay)';
export const TX_ONLY = 'handheld: TX-only (GPS on P1), no relay';
export const MAP_DIFFERS = 'handheld map differs: push the map to the handheld';

/** What the relay needs of the companion. */
export interface RelayTarget {
  readonly hello: P.Hello | null;
  send(ftype: number, payload: Uint8Array): Promise<void>;
}

/** What the relay reads off the laptop's poller (hondalog Poller / the K-line agent's poller). */
export interface RelayPoller {
  /** 'ok' | 'connecting' | anything else = off (Python: OK, CONNECTING, NO_REPLY, PORT_LOST, STOPPED). */
  readonly state: string;
  readonly mode?: 'honda' | 'obd2';
  /** Monotonic seconds of session second 0. */
  readonly t0: number;
  readonly hz?: number;
  readonly sweeps?: number;
  readonly ecuId?: Uint8Array | null;
  /** The poller's monotonic clock (seconds). */
  now(): number;
}

/** (LINK state, up, iso) of a laptop poller. */
export function linkFields(poller: { state: string; mode?: string }): [number, boolean, boolean] {
  const state = String(poller.state).toLowerCase();
  const iso = poller.mode === 'obd2' || state === 'iso_only';
  if (state === 'ok' || state === 'iso_only') return [iso ? P.LINK_ISO_ONLY : P.LINK_OK, true, iso];
  if (state === 'connecting') return [P.LINK_CONNECTING, false, iso];
  return [P.LINK_OFF, false, iso];
}

export interface BlockReadLike { mono: number; latencyS: number; addr: number; data: Uint8Array }
export interface SampleLike { link: boolean; t: number; mono?: number | null; latency: number }

export class Relay {
  queued = 0;
  sent = 0;
  dropped = 0;
  failed = 0;
  lastError = '';
  private q: Array<[number, Uint8Array]> = [];
  private linkLast = '';
  private linkNext = 0;
  private wake: (() => void) | null = null;
  private stopFlag = false;
  private loop: Promise<void> | null = null;
  private readonly clock: Clock;

  constructor(readonly companion: RelayTarget, readonly poller: RelayPoller, public mapHash: string,
    readonly force = false, readonly maxQueue = QUEUE_MAX, clock?: Clock) {
    this.clock = clock ?? realClock;
  }

  // -- whether to relay --

  /** '' while relaying (or waiting for the hello), else why not. */
  get status(): string {
    const h = this.companion.hello;
    if (h === null) return '';
    if (!P.twoWay(h)) return TX_ONLY;
    if (this.force) return '';
    if (P.ownKline(h)) return OWN_KLINE;
    if (!(h.caps & P.CAP_RELAY)) return TOO_OLD;
    if (h.mapHash !== this.mapHash) return MAP_DIFFERS;
    return '';
  }

  get active(): boolean {
    return this.companion.hello !== null && !this.status;
  }

  /** For the info bar. */
  text(): string {
    if (this.status) return this.status;
    if (!this.active) return '';
    return `relay to handheld: ${this.sent} sent` + (this.dropped ? `, ${this.dropped} dropped` : '');
  }

  // -- the poller's hooks: pack and queue, nothing else --

  private put(ftype: number, payload: Uint8Array): void {
    if (this.q.length >= this.maxQueue) {
      this.q.shift();
      this.dropped += 1;
    }
    this.q.push([ftype, payload]);
    this.queued += 1;
    this.wake?.();
  }

  private clockUs(): number {
    return Math.trunc(this.poller.now() * 1e6);
  }

  onBlock(read: BlockReadLike): void {
    if (!this.active) return;
    this.put(P.SR_RELAY_READ, P.packRelayRead(Math.trunc((read.mono - read.latencyS) * 1e6), read.addr, read.data,
      read.latencyS * 1e6));
  }

  onPid(t: number, pid: number, data: Uint8Array): void {
    if (!this.active) return;
    this.put(P.SR_RELAY_ISO, P.packRelayIso(Math.trunc((this.poller.t0 + t) * 1e6), pid, data, 0x41));
  }

  onSample(sample: SampleLike): void {
    if (!this.active) return;
    this.checkLink();
    if (sample.link) {
      const mono = sample.mono || this.poller.t0 + sample.t;
      this.put(P.SR_RELAY_TICK, P.packRelayTick(Math.trunc(mono * 1e6), sample.latency * 1e6, this.poller.hz ?? 0,
        this.poller.sweeps ?? 0));
    }
  }

  /** RELAY_LINK on a change of the poller's state, and once a second. */
  checkLink(force = false): void {
    if (!this.active) return;
    const fields = linkFields(this.poller);
    const ecu = (this.poller.ecuId ?? new Uint8Array(0)).subarray(0, 10);
    const key = `${fields.join(',')}|${Array.from(ecu).join(',')}`;
    const now = this.clock.now();
    if (!force && key === this.linkLast && now < this.linkNext) return;
    this.linkLast = key;
    this.linkNext = now + LINK_EVERY_S;
    this.put(P.SR_RELAY_LINK, P.packRelayLink(this.clockUs(), fields[0], fields[1], fields[2], ecu));
  }

  // -- the writer --

  start(): this {
    if (!this.loop) this.loop = this.run();
    return this;
  }

  async stop(): Promise<void> {
    this.stopFlag = true;
    this.wake?.();
    await this.loop;
  }

  /** Send what is queued now (the writer's step; tests call it directly). */
  async flush(): Promise<void> {
    while (!this.stopFlag && this.q.length) {
      const [ftype, payload] = this.q.shift()!;
      try {
        await this.companion.send(ftype, payload);
        this.sent += 1;
      } catch (e) { // port gone, handheld restarting: the next ones may go
        this.failed += 1;
        this.lastError = String((e as Error)?.message ?? e);
      }
    }
  }

  private async run(): Promise<void> {
    while (!this.stopFlag) {
      await new Promise<void>((resolve) => {
        this.wake = resolve;
        const timer = setTimeout(resolve, 250);
        const done = () => { clearTimeout(timer); resolve(); };
        this.wake = done;
      });
      this.wake = null;
      this.checkLink();
      await this.flush();
    }
  }
}
