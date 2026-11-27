/**
 * The loop that owns the ECU link (port of hondalog/poller.py).
 *
 * It reads the address map's blocks in priority order (scheduler.ts) and
 * hands out every block read raw (`onRead(EcuRead)`); decoding belongs to the
 * expression engine. Nothing else touches the port while it runs: the raw
 * tools hand their work to it with `submit(fn)` and it runs between reads.
 *
 * A "cycle" (`onCycle`) completes each time every fast block has been read
 * once, once every block has been read at least once since the link came up:
 * the moment the Python app emits a Sample.
 *
 * Recovery
 * - ECU stops answering (key off, stall with power loss): state 'no reply'
 *   (LinkState 'error'). The link is only declared down after
 *   FAILS_TO_NO_REPLY block reads failed in a row (each already retried once),
 *   or 2 failed with NoReply and no good read for SILENT_DOWN_S: a stock P2P
 *   at idle ignores a request or two now and then. Then it probes with one
 *   short read and a growing pause, re-sends the wake frame before every
 *   second probe (a P2P answers nothing after key-on until it has had it),
 *   but never in the first WAKE_GRACE_S of an outage of a running link, and
 *   carries on by itself when the ECU is back.
 * - Port dies (cable pulled): 'port lost' (LinkState 'error'); the port is
 *   reopened every 0.25-2 s and the same probing follows.
 * - OBD2-only ECU: with an `isoFallback`, when Honda reads have never worked
 *   on this connection and stay unanswered for ISO_AFTER_S, the fallback's
 *   5-baud init is tried; if it answers, LinkState becomes 'iso_only' and
 *   the poller steps the fallback's PID session instead (onPid).
 *
 * Read-only: the Link refuses anything but read requests and the wake frame
 * in raw() unless `setExpertUnlock(true)`; the poller itself only ever sends
 * reads and the wake frame.
 */

import type { AddressMap, ChannelSpec, EcuRead, LinkState, LinkStats, RateClass, Transport } from '../types';
import { REAL, type Clock } from './clock';
import { Link, TransportError } from './link';
import { CostModel, busLoad, defaultPlanner, predictRates, type Block, type BlockPlanner, type ChannelLike } from './planner';
import { DEFAULT_FRAMING, NoReply, ProtocolError, fmtHex, withOverrides, type FullFraming } from './protocol';
import { ChannelRate, RATE_CLASSES, Scheduler, rateClassOf, targetHz } from './scheduler';
import type { LinkCounters } from './timing';

/** Internal states, as the Python poller names them. */
export type PollerState = 'connecting' | 'ok' | 'no reply' | 'port lost' | 'stopped';

export const FAILS_TO_NO_REPLY = 3;   // failed block reads in a row (each already retried)
export const SILENT_DOWN_S = 0.6;     // ... or no good read this long, with 2 failed block reads
export const WAKE_GRACE_S = 1.0;      // no wake bytes this early in an outage of a running link
const PROBE_MIN_S = 0.05, PROBE_MAX_S = 0.25;
const REOPEN_MIN_S = 0.25, REOPEN_MAX_S = 2.0;
const REPORT_EVERY_S = 0.5;
const REPLAN_EVERY = 256;             // good reads between checks of the cost model
const ISO_AFTER_S = 12.0;             // Honda reads unanswered this long: try OBD2
const ISO_RETRY_S = 5.0;              // after an ISO init nobody answered
const ISO_REINIT_S = 1.0;             // after a live ISO session went quiet
const ISO_FAILS = 3;                  // PID requests in a row without a reply: session lost

/** One failed exchange, retries included (Python LinkError). kind: NoReply,
 *  ShortReply, BadChecksum, BadFrame; 'PortLost' for a port failure. */
export interface LinkErrorEvent {
  t: number;          // s since connect, when the request went out
  addr: number;
  length: number;
  kind: string;
  attempt: number;    // 1 = first try, 2 = the retry, ...
  message: string;
}

/** A live OBD2 session the poller steps in OBD2-only mode (owner: OBD2 agent). */
export interface IsoLiveSession {
  /** "ISO 9141-2 via 5-baud init, ..." */
  readonly label: string;
  /** One PID request. Throw on no/bad reply. `emit`: a cycle ends after it. */
  next(): Promise<{ pid: number; raw: Uint8Array; emit: boolean }>;
  /** Leave the session and put the line back to the Honda baud rate. */
  close(): Promise<void>;
}

export interface IsoFallback {
  /** 5-baud init (+ supported-PID query) on this transport. Throw if nobody
   *  answers; throw a TransportError if the port itself failed. */
  open(transport: Transport, clock: Clock, hondaBaud: number): Promise<IsoLiveSession>;
}

export interface PollerOptions {
  clock?: Clock;
  /** Overrides the framing built from `amap.protocol`. */
  framing?: FullFraming;
  planner?: BlockPlanner;
  isoFallback?: IsoFallback | null;
  /** Protocol "OBD2 only": no Honda reads at all; start straight in the
   *  isoFallback's 5-baud init (and retry it) instead of after ISO_AFTER_S. */
  obd2Only?: boolean;
  usbLatencyS?: number;
  onRead?: (read: EcuRead) => void;
  onLink?: (state: LinkState, stats: LinkStats) => void;
  onError?: (err: LinkErrorEvent) => void;
  onCycle?: (t: number, latencyS: number) => void;
  onPid?: (t: number, pid: number, raw: Uint8Array) => void;
}

interface Job { fn: (link: Link) => unknown; resolve: (v: unknown) => void; reject: (e: unknown) => void }

/** The full framing for a map: the defaults with the map's [protocol] table. */
export function framingOf(amap: Pick<AddressMap, 'protocol'>): FullFraming {
  return withOverrides(DEFAULT_FRAMING, amap.protocol as Record<string, unknown>);
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export class Poller {
  readonly transport: Transport;
  readonly amap: AddressMap;
  readonly framing: FullFraming;
  readonly clock: Clock;
  readonly link: Link;
  readonly planner: BlockPlanner;
  isoFallback: IsoFallback | null;
  /** No Honda reads: OBD2 (ISO 9141-2) from the start. */
  readonly obd2Only: boolean;

  onRead: ((read: EcuRead) => void) | null;
  onLink: ((state: LinkState, stats: LinkStats) => void) | null;
  onError: ((err: LinkErrorEvent) => void) | null;
  onCycle: ((t: number, latencyS: number) => void) | null;
  onPid: ((t: number, pid: number, raw: Uint8Array) => void) | null;

  /** failed exchanges by kind since connect */
  readonly errorKinds: Record<string, number> = {};
  readonly t0: number;
  state: PollerState = 'connecting';
  mode: 'honda' | 'obd2' = 'honda';
  /** OBD2-only mode: how the session came up */
  isoLink = '';
  reads = 0;
  errors = 0;
  cycles = 0;          // cycles completed with the link up (Python: sweeps)
  hz = 0;              // cycle rate
  lastError = '';
  ecuId: Uint8Array | null = null;
  /** achieved rate per channel, refreshed twice a second */
  rates: Record<string, ChannelRate> = {};
  cost: CostModel;
  plan: Block[] = [];
  /** resolves once the ECU id was tried */
  readonly ready: Promise<void>;

  private readonly channels: ChannelSpec[];
  private readonly klass: Record<string, RateClass>;
  private readonly target: Record<string, number>;
  private sched: Scheduler = new Scheduler([]);
  private burstSched: Scheduler | null = null;
  private burstUntil = 0;
  private unread = new Set<number>();
  private pending = new Set<number>();
  private fails = 0;
  private lastOk: number;
  private wakeAfter = 0;
  private probes = 0;
  private probeWait = PROBE_MIN_S;
  private reopenWait = REOPEN_MIN_S;
  private lastEmit: number | null = null;
  private nextReport = 0;
  private nextReplan = REPLAN_EVERY;
  private quietUntil = 0;
  private iso: IsoLiveSession | null = null;
  private isoFails = 0;
  private hondaLastOk: number;
  private hondaSeen = false;
  private nextIsoTry = 0;
  private jobs: Job[] = [];
  private jobWake: (() => void) | null = null;
  private pausedLive = false;
  private stopping = false;
  private stopWake: (() => void) | null = null;
  private stopSignal: Promise<void>;
  private connected = false;
  private running: Promise<void> | null = null;
  private finished = false;
  private readyResolve!: () => void;
  private lastPublic: LinkState | null = null;
  private lastYield = 0;

  constructor(transport: Transport, amap: AddressMap, opts: PollerOptions = {}) {
    this.transport = transport;
    this.amap = amap;
    this.clock = opts.clock ?? (transport as { clock?: Clock }).clock ?? REAL;
    this.framing = opts.framing ?? framingOf(amap);
    this.planner = opts.planner ?? defaultPlanner;
    this.isoFallback = opts.isoFallback ?? null;
    this.obd2Only = !!opts.obd2Only && this.isoFallback !== null;
    if (this.obd2Only) this.mode = 'obd2';
    this.link = new Link(transport, this.framing, this.clock, opts.usbLatencyS);
    this.link.onError = (addr, length, error, attempt, tReq) => this.linkError(addr, length, error, attempt, tReq);
    this.onRead = opts.onRead ?? null;
    this.onLink = opts.onLink ?? null;
    this.onError = opts.onError ?? null;
    this.onCycle = opts.onCycle ?? null;
    this.onPid = opts.onPid ?? null;
    this.t0 = this.clock.now();
    this.lastOk = this.hondaLastOk = this.t0;
    this.channels = amap.channels.filter((c) => c.kind !== 'math');
    this.klass = Object.fromEntries(this.channels.map((c) => [c.name, rateClassOf(c)]));
    this.target = Object.fromEntries(Object.entries(this.klass).map(([n, k]) => [n, targetHz(k, this.framing)]));
    this.cost = CostModel.fromFraming(this.framing);
    this.buildPlan();
    this.ready = new Promise((r) => { this.readyResolve = r; });
    this.stopSignal = new Promise((r) => { this.stopWake = r; });
  }

  // -- plan --

  private rateOf = (c: ChannelLike): number => this.target[c.name] ?? this.framing.rateSlowHz;

  private buildPlan(cost?: CostModel): void {
    if (cost) this.cost = cost;
    this.plan = this.planner.plan(this.channels, this.rateOf, this.framing.maxReadLen, this.cost);
    this.sched = new Scheduler(this.plan, this.klass, this.target);
    this.unread = new Set(this.plan.map((_, i) => i));
    this.pending = new Set(this.sched.top);
  }

  /** The planned reads as [addr, length], lowest address first. */
  get blocks(): [number, number][] {
    return this.plan.map((b) => [b.addr, b.length]);
  }

  /** Reads per second per channel that the cost model promises. */
  predictedRates(): Record<string, number> {
    const out: Record<string, number> = {};
    predictRates(this.plan, this.cost).forEach((hz, i) => {
      for (const name of this.plan[i]!.channels) out[name] = hz;
    });
    return out;
  }

  /** Share of the wire the targets need. Above 1.0 they cannot all be met. */
  get busLoad(): number {
    return busLoad(this.plan, this.cost);
  }

  channelRates(): Record<string, ChannelRate> {
    return { ...this.rates };
  }

  /** rate class -> [target Hz, worst achieved Hz among its channels]. */
  classRates(): Partial<Record<RateClass, [number, number]>> {
    const acc: Partial<Record<RateClass, [number, number]>> = {};
    for (const r of Object.values(this.rates)) {
      const [target, worst] = acc[r.rateClass] ?? [r.targetHz, Infinity];
      acc[r.rateClass] = [target, Math.min(worst, r.achievedHz)];
    }
    const out: Partial<Record<RateClass, [number, number]>> = {};
    for (const k of RATE_CLASSES) if (acc[k]) out[k] = acc[k];
    return out;
  }

  shortfalls(): Record<string, ChannelRate> {
    return Object.fromEntries(Object.entries(this.rates).filter(([, r]) => !r.ok));
  }

  get counters(): LinkCounters {
    return this.link.stats;
  }

  /** The shared LinkStats contract. */
  get stats(): LinkStats {
    const s = this.link.stats;
    return {
      requests: s.requests, ok: s.ok, timeouts: s.timeouts, shortReplies: s.shortReplies,
      checksumFailures: s.checksumFailures, badFrames: s.badFrames, retries: s.retries,
      resyncs: s.resyncs, rateHz: this.hz,
    };
  }

  /** Counters and measured timing in one flat dict (status bar, log header). */
  linkReport(): Record<string, number> {
    return {
      ...this.link.stats.asDict(),
      ...this.link.timing.asDict(),
      usbLatencyMs: 1000 * this.link.usbLatencyS,
      busLoad: this.busLoad,
      sampleHz: this.hz,
    };
  }

  /** The LinkState the UI shows. */
  get linkState(): LinkState {
    switch (this.state) {
      case 'ok': return this.iso ? 'iso_only' : 'ok';
      case 'connecting': return 'connecting';
      case 'stopped': return 'stopped';
      default: return 'error';
    }
  }

  get ecuIdText(): string {
    if (!this.ecuId) return '(no reply)';
    const text = Array.from(this.ecuId, (b) => (b >= 32 && b < 127 ? String.fromCharCode(b) : '.')).join('');
    return `${fmtHex(this.ecuId)}  "${text}"`;
  }

  /** Let the hex console send frames that are not reads (off by default). */
  setExpertUnlock(on: boolean): void {
    this.link.unlock(on);
  }

  // -- API --

  /** Run fn(link) between reads. Rejects with TransportError while the port is lost. */
  submit<T>(fn: (link: Link) => T | Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      if (this.finished) {
        reject(new TransportError('disconnected'));
        return;
      }
      this.jobs.push({ fn, resolve: resolve as (v: unknown) => void, reject });
      this.jobWake?.();
    });
  }

  /** Stop the live block reads (the poke test borrows the line): only
   *  submitted jobs run until resume(). The port stays open, the link state
   *  stays as it was; no cycles are emitted meanwhile. */
  pause(): void {
    this.pausedLive = true;
  }

  /** Back to live polling after pause(). */
  resume(): void {
    if (!this.pausedLive) return;
    this.pausedLive = false;
    this.lastEmit = null;          // no rate spike from the gap
    this.jobWake?.();
  }

  get paused(): boolean {
    return this.pausedLive;
  }

  /** Read only these channels, as fast as the line goes, for a while.
   *  Resolves with the burst's blocks as [addr, length]. */
  burst(names: string[], seconds: number): Promise<[number, number][]> {
    return this.submit(() => this.startBurst(names, seconds));
  }

  /** Start polling in the background. */
  start(): Promise<void> {
    if (!this.running) this.running = this.run();
    return this.ready;
  }

  /** Tests and benchmarks: drive the loop on this task until the clock
   *  reaches `t` (use with a VirtualClock; do not mix with start()). */
  async runUntil(t: number): Promise<void> {
    if (this.running) throw new Error('runUntil: the poller is already running in the background');
    if (!this.connected) await this.connect();
    let stuck = 0;
    while (!this.stopping && this.clock.now() < t) {
      const before = this.clock.now();
      await this.iterate();
      await this.maybeYield();
      stuck = this.clock.now() === before ? stuck + 1 : 0;
      if (stuck > 10000) throw new Error(`runUntil: the clock stopped at ${before} (state ${this.state})`);
    }
  }

  async stop(): Promise<void> {
    this.stopping = true;
    this.stopWake?.();
    if (this.running) await this.running;
    else await this.finish();
  }

  // -- loop --

  private async run(): Promise<void> {
    try {
      if (!this.connected) await this.connect();
      while (!this.stopping) {
        await this.iterate();
        await this.maybeYield();
      }
    } finally {
      await this.finish();
    }
  }

  /** Let the UI breathe: a macrotask now and then (always needed with a
   *  VirtualClock, where every await resolves at once). */
  private async maybeYield(): Promise<void> {
    const wall = performance.now();
    if (wall - this.lastYield >= 30) {
      this.lastYield = wall;
      await new Promise((r) => setTimeout(r, 0));
    }
  }

  private async iterate(): Promise<void> {
    if (this.jobs.length) await this.runJob();
    if (this.pausedLive && this.state !== 'port lost') {
      if (!this.jobs.length && !this.stopping) await this.waitForJob(0.25);
      return;
    }
    if (this.iso) await this.isoStep();
    else if (this.state === 'ok') await this.step();
    else if (this.state === 'port lost') {
      this.emitDown();
      await this.sleep(this.reopenWait);
      await this.reopen();
    } else if (this.isoDue()) await this.tryIso();
    else if (this.obd2Only) {
      // OBD2 only: never a Honda probe; wait for the next 5-baud init
      await this.sleep(Math.max(PROBE_MIN_S, Math.min(PROBE_MAX_S, this.nextIsoTry - this.clock.now())));
    } else await this.probe();
  }

  private async finish(): Promise<void> {
    if (this.finished) return;
    this.finished = true;
    this.readyResolve();
    this.state = 'stopped';
    try {
      await this.transport.close();
    } catch {
      /* already gone */
    }
    this.failJobs('disconnected');
    this.notify(true);
  }

  /** Paused: until a job arrives, resume() or stop(), at most `seconds`. */
  private async waitForJob(seconds: number): Promise<void> {
    if (this.clock.virtual) {
      await this.clock.sleep(seconds);
      return;
    }
    const woken = new Promise<void>((r) => { this.jobWake = r; });
    try {
      await Promise.race([this.clock.sleep(seconds), this.stopSignal, woken]);
    } finally {
      this.jobWake = null;
    }
  }

  private async sleep(seconds: number): Promise<void> {
    if (this.clock.virtual) await this.clock.sleep(seconds);
    else await Promise.race([this.clock.sleep(seconds), this.stopSignal]);
  }

  private setState(s: PollerState): void {
    this.state = s;
    this.notify();
  }

  /** onLink on every change of the public state (or when forced). */
  private notify(force = false): void {
    const pub = this.linkState;
    if (!force && pub === this.lastPublic) return;
    this.lastPublic = pub;
    if (this.onLink) {
      try {
        this.onLink(pub, this.stats);
      } catch (exc) {
        this.lastError = `onLink: ${errText(exc)}`;
      }
    }
  }

  private async connect(): Promise<void> {
    this.connected = true;
    this.notify(true);
    if (this.obd2Only) {
      try {
        await this.transport.open({ baud: this.framing.baud });
        this.link.reset();
      } catch (exc) {
        this.portLost(exc);
        this.readyResolve();
        return;
      }
      // 'connecting' until the first 5-baud init answers (tryIso -> 'ok')
      this.readyResolve();
      return;
    }
    try {
      await this.transport.open({ baud: this.framing.baud });
      this.link.reset();
      await this.link.wake();
      await this.readEcuId();
    } catch (exc) {
      if (exc instanceof ProtocolError) throw exc;
      this.portLost(exc);
      this.readyResolve();
      return;
    }
    if (this.ecuId) {
      this.setState('ok');
    } else {
      this.state = 'no reply';
      this.emitDown();
    }
    this.readyResolve();
  }

  private async readEcuId(): Promise<void> {
    const { addr, length } = this.amap.ecuId;
    if (addr === null || addr === undefined) return;
    for (let i = 0; i < 2; i++) {
      try {
        this.ecuId = (await this.read(addr, length)).data;
        return;
      } catch (exc) {
        if (!(exc instanceof ProtocolError)) throw exc;
      }
    }
  }

  /** One counted read. Throws ProtocolError / transport errors. */
  private async read(addr: number, length: number, retries: number | null = 0) {
    let result;
    try {
      result = await this.link.readBlock(addr, length, retries);
    } catch (exc) {
      if (exc instanceof ProtocolError) {
        this.errors += 1;
        this.lastError = `${hex2(addr)}+${length}: ${exc.message}`;
      }
      throw exc;
    }
    this.reads += 1;
    this.hondaLastOk = this.clock.now();
    this.hondaSeen = true;
    return result;
  }

  // -- the hot loop: one block per call --

  private async step(): Promise<void> {
    const clock = this.clock;
    let sched = this.burstSched ?? this.sched;
    let now = clock.now();
    if (this.burstSched && now >= this.burstUntil) {
      this.endBurst();
      sched = this.sched;
    }
    const index = sched.next(now);
    const block = sched.blocks[index]!;
    let result;
    try {
      result = await this.link.readBlock(block.addr, block.length);
    } catch (exc) {
      if (!(exc instanceof ProtocolError)) {
        this.portLost(exc);
        return;
      }
      this.errors += 1;
      this.lastError = `${hex2(block.addr)}+${block.length}: ${exc.message}`;
      now = clock.now();
      sched.failed(index, now);
      // One block that went unanswered twice is not a dead ECU. Gone means
      // several block reads in a row, or silence for SILENT_DOWN_S.
      this.fails += 1;
      const silent = exc instanceof NoReply && this.fails >= 2 && now - this.lastOk >= SILENT_DOWN_S;
      if (this.fails >= FAILS_TO_NO_REPLY || silent) {
        this.linkDown();
        this.wakeAfter = now + WAKE_GRACE_S;
        this.emitDown();
      }
      return;
    }
    this.reads += 1;
    this.fails = 0;
    const tDone = (this.lastOk = result.tDone);
    sched.done(index, tDone);

    const t = tDone - this.t0;
    if (this.onRead) {
      try {
        this.onRead({ t: t - result.latencyS, addr: block.addr, data: result.data, latencyS: result.latencyS });
      } catch (exc) {
        this.lastError = `onRead: ${errText(exc)}`;
      }
    }

    // a cycle per pass over the fast blocks, once every block has been seen
    this.unread.delete(index);
    this.pending.delete(index);
    if (!this.pending.size) {
      for (const i of sched.top) this.pending.add(i);
      if (!this.unread.size) this.emit(t, result.latencyS);
    }
    if (tDone >= this.nextReport) {
      this.nextReport = tDone + REPORT_EVERY_S;
      this.rates = this.sched.report(tDone);
      this.notify(true);
    }
    if (this.reads >= this.nextReplan) {
      this.nextReplan = this.reads + REPLAN_EVERY;
      this.maybeReplan();
    }
  }

  /** Re-plan if the measured per-request overhead is not what was assumed. */
  private maybeReplan(): void {
    const timing = this.link.timing;
    if (this.burstSched || timing.srtt === null) return;
    const measured = CostModel.fromFraming(this.framing, timing.overheadS, timing.gap);
    if (Math.abs(measured.overheadS - this.cost.overheadS) < 0.2 * this.cost.overheadS) return;
    const old = JSON.stringify(this.blocks);
    this.cost = measured;
    const next = this.planner.plan(this.channels, this.rateOf, this.framing.maxReadLen, measured);
    if (JSON.stringify(next.map((b) => [b.addr, b.length])) !== old) this.buildPlan(measured);
  }

  private emit(t: number, latency: number): void {
    const last = this.lastEmit;
    if (last !== null && t > last) {
      const inst = 1.0 / (t - last);
      this.hz = this.hz === 0 ? inst : 0.9 * this.hz + 0.1 * inst;
    }
    this.lastEmit = t;
    this.cycles += 1;
    if (this.onCycle) {
      try {
        this.onCycle(t, latency);
      } catch (exc) {
        this.lastError = `onCycle: ${errText(exc)}`;
      }
    }
  }

  /** The link is down: tell everyone now (repeated on every probe). */
  private emitDown(): void {
    this.hz = 0;
    this.lastEmit = null;
    this.notify(true);
  }

  // -- burst --

  private startBurst(names: string[], seconds: number): [number, number][] {
    const want = new Set(names);
    const channels = this.channels.filter((c) => want.has(c.name));
    if (!channels.length) throw new Error(`no such channels: ${names.join(', ')}`);
    const blocks = this.planner.plan(channels, () => 1.0, this.framing.maxReadLen, this.cost);
    this.burstSched = new Scheduler(blocks, this.klass, this.target);
    this.burstUntil = this.clock.now() + seconds;
    this.pending = new Set(this.burstSched.top);
    return blocks.map((b) => [b.addr, b.length]);
  }

  private endBurst(): void {
    this.burstSched = null;
    this.sched.restart();
    this.pending = new Set(this.sched.top);
  }

  // -- link down / recovery --

  private linkError(addr: number, length: number, error: ProtocolError, attempt: number, tReq: number): void {
    const kind = error.kind;
    this.errorKinds[kind] = (this.errorKinds[kind] ?? 0) + 1;
    if (this.onError && this.state === 'ok') {   // not the probes of an outage
      try {
        this.onError({ t: tReq - this.t0, addr, length, kind, attempt, message: error.message });
      } catch (exc) {
        this.lastError = `onError: ${errText(exc)}`;
      }
    }
  }

  private linkDown(): void {
    this.state = 'no reply';
    this.fails = 0;
    this.wakeAfter = 0;
    this.probes = 0;
    this.probeWait = PROBE_MIN_S;
    this.burstSched = null;
    this.rates = {};
  }

  private linkUp(): void {
    this.fails = 0;
    this.lastOk = this.clock.now();
    this.quietUntil = 0;
    this.link.timing.relax();
    this.sched.restart();
    this.unread = new Set(this.plan.map((_, i) => i));
    this.pending = new Set(this.sched.top);
    this.setState('ok');
  }

  /** The ECU is silent: ask for one block now and then until it answers. */
  private async probe(): Promise<void> {
    const block = this.plan[0] ?? { addr: 0, length: 1 };
    const quiet = this.clock.now() < this.quietUntil;
    try {
      this.probes += 1;
      if (this.probes % 2 === 0 && this.clock.now() >= this.wakeAfter) {
        await this.link.wake();   // just powered up? a P2P needs the wake frame
      }
      try {
        await this.read(block.addr, block.length);
      } catch (exc) {
        if (!(exc instanceof ProtocolError)) throw exc;
        if (!quiet) {
          if (this.state === 'connecting') this.state = 'no reply';
          this.emitDown();
        }
        await this.sleep(this.probeWait);
        this.probeWait = Math.min(PROBE_MAX_S, this.probeWait * 1.5);
        return;
      }
    } catch (exc) {
      this.portLost(exc);
      return;
    }
    this.linkUp();
    if (this.ecuId === null) {
      try {
        await this.readEcuId();
      } catch (exc) {
        this.portLost(exc);
      }
    }
  }

  private portLost(exc: unknown): void {
    const iso = this.iso;
    this.iso = null;
    this.mode = this.obd2Only ? 'obd2' : 'honda';
    this.errors += 1;
    this.lastError = `port: ${errText(exc)}`;
    this.linkDown();
    this.state = 'port lost';
    this.reopenWait = REOPEN_MIN_S;
    void iso?.close().catch(() => undefined);
    void this.transport.close().catch(() => undefined);
    this.failJobs(`port lost: ${errText(exc)}`);
    if (this.onError) {
      try {
        this.onError({ t: this.clock.now() - this.t0, addr: -1, length: 0, kind: 'PortLost', attempt: 1, message: errText(exc) });
      } catch {
        /* logging must not kill polling */
      }
    }
    // onLink('error') follows on the next loop turn (emitDown), as in Python
  }

  private async reopen(): Promise<void> {
    try {
      await this.transport.open({ baud: this.framing.baud });
    } catch (exc) {
      this.lastError = `port: ${errText(exc)}`;
      this.reopenWait = Math.min(REOPEN_MAX_S, this.reopenWait * 1.5);
      return;
    }
    this.link.stats.reconnects += 1;
    this.link.reset();
    this.setState('no reply');
    this.probes = 3;   // wake on the very next probe
  }

  // -- jobs --

  private async runJob(): Promise<void> {
    const job = this.jobs.shift();
    if (!job) return;
    if (this.state === 'port lost') {
      job.reject(new TransportError(this.lastError));
      return;
    }
    if (this.iso || this.obd2Only) {
      job.reject(new ProtocolError(this.obd2Only
        ? 'Honda reads are off: protocol is set to OBD2 only'
        : 'Honda reads are off: this ECU answers standard OBD2 only'));
      return;
    }
    try {
      job.resolve(await job.fn(this.link));
    } catch (exc) {
      job.reject(exc);
      if (exc instanceof TransportError) this.portLost(exc);
    }
  }

  private failJobs(why: string): void {
    const jobs = this.jobs;
    this.jobs = [];
    for (const j of jobs) j.reject(new TransportError(why));
  }

  // -- OBD2-only live mode --

  private isoDue(): boolean {
    if (!this.isoFallback) return false;
    const now = this.clock.now();
    if (this.obd2Only) return now >= this.nextIsoTry;
    if (this.hondaSeen) return false;
    return now >= this.quietUntil && now - this.hondaLastOk >= ISO_AFTER_S && now >= this.nextIsoTry;
  }

  private async tryIso(): Promise<void> {
    this.mode = 'obd2';
    let session: IsoLiveSession;
    try {
      session = await this.isoFallback!.open(this.transport, this.clock, this.framing.baud);
    } catch (exc) {
      if (exc instanceof TransportError) {
        this.portLost(exc);
        return;
      }
      this.lastError = `OBD2: ${errText(exc)}`;
      this.mode = this.obd2Only ? 'obd2' : 'honda';
      this.link.reset();
      if (this.obd2Only && this.state === 'connecting') this.setState('no reply');
      this.nextIsoTry = this.clock.now() + ISO_RETRY_S;
      return;
    }
    if (this.stopping) {
      await session.close().catch(() => undefined);
      return;
    }
    this.iso = session;
    this.isoFails = 0;
    this.isoLink = session.label;
    this.lastError = '';
    this.setState('ok');
  }

  private async isoStep(): Promise<void> {
    const session = this.iso!;
    let pv;
    try {
      pv = await session.next();
    } catch (exc) {
      if (exc instanceof TransportError) {
        this.portLost(exc);
        return;
      }
      this.errors += 1;
      this.lastError = `OBD2: ${errText(exc)}`;
      this.isoFails += 1;
      if (this.isoFails >= ISO_FAILS) await this.isoLeave(this.lastError);
      return;
    }
    this.isoFails = 0;
    this.reads += 1;
    const t = this.clock.now() - this.t0;
    if (this.onPid) {
      try {
        this.onPid(t, pv.pid, pv.raw);
      } catch (exc) {
        this.lastError = `onPid: ${errText(exc)}`;
      }
    }
    if (pv.emit) this.emit(t, 0);
  }

  /** The OBD2 session went quiet (key off, power lost): report the link
   *  down, go back to probing, and try the init again soon. */
  private async isoLeave(why: string): Promise<void> {
    const iso = this.iso;
    this.iso = null;
    this.mode = this.obd2Only ? 'obd2' : 'honda';
    this.lastError = `OBD2 session lost: ${why}`;
    this.linkDown();
    this.emitDown();
    if (iso) {
      try {
        await iso.close();
        this.link.reset();
      } catch (exc) {
        this.portLost(exc);
      }
    }
    if (this.state !== 'port lost') {
      this.state = 'no reply';
      this.probes = 3;
      this.nextIsoTry = this.clock.now() + ISO_REINIT_S;
    }
  }
}

function hex2(v: number): string {
  return v.toString(16).toUpperCase().padStart(2, '0');
}
