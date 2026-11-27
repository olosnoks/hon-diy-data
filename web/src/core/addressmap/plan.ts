/**
 * Block-read planner (hondalog/link/planner.py) and the rate classes
 * (hondalog/link/scheduler.py: DEFAULT_CLASS, rate_class_of, target_hz).
 *
 * A request costs the same fixed overhead however much it reads (5 echoed
 * request bytes, 3 reply frame bytes, ECU turnaround, the gap before the next
 * request), then one byte time per data byte. A block is read at the rate of
 * its most demanding channel, so the planner minimises
 *
 *     sum over blocks of  rate(block) * time(block)
 *
 * exactly, by dynamic programming over the address-sorted spans.
 */

import type { RateClass } from '../types';
import { DEFAULT_FRAMING, REPLY_OVERHEAD, REQUEST_BYTES, type MapFraming } from './framing';

export const RATE_CLASSES: readonly RateClass[] = ['fast', 'medium', 'slow'];

/** A channel's rate class when the map gives none. Unknown names are slow. */
export const DEFAULT_CLASS: Readonly<Record<string, RateClass>> = Object.freeze({
  rpm: 'fast', map: 'fast', tps: 'fast', inj: 'fast', ign: 'fast',
  o2: 'medium', stft: 'medium', ltft: 'medium', vss: 'medium',
  ect: 'slow', iat: 'slow', batt: 'slow', baro: 'slow',
});

export function defaultClass(name: string): RateClass {
  return Object.prototype.hasOwnProperty.call(DEFAULT_CLASS, name) ? DEFAULT_CLASS[name]! : 'slow';
}

export interface ChannelLike {
  name: string;
  addr: number;
  length: number;
  rate?: string;
}

/** The map's rate field, else the name's default class. */
export function rateClassOf(channel: ChannelLike): RateClass {
  const explicit = String(channel.rate ?? '').trim().toLowerCase();
  if (explicit) {
    if (!(RATE_CLASSES as readonly string[]).includes(explicit)) {
      throw new Error(`${channel.name}: rate must be one of ('fast', 'medium', 'slow'), not '${explicit}'`);
    }
    return explicit as RateClass;
  }
  return defaultClass(channel.name);
}

/** Reads per second a rate class aims for. */
export function targetHz(rateClass: RateClass, framing: Pick<MapFraming, 'rateFastHz' | 'rateMediumHz' | 'rateSlowHz'>): number {
  return { fast: framing.rateFastHz, medium: framing.rateMediumHz, slow: framing.rateSlowHz }[rateClass];
}

/** Time one read request takes on the wire. */
export class CostModel {
  readonly byteS: number;
  readonly echoBytes: number;
  readonly frameBytes: number;
  readonly turnaroundS: number;
  readonly gapS: number;

  constructor(opts: { byteS?: number; echoBytes?: number; frameBytes?: number; turnaroundS?: number; gapS?: number } = {}) {
    this.byteS = opts.byteS ?? 10.0 / 9600;
    this.echoBytes = opts.echoBytes ?? REQUEST_BYTES;
    this.frameBytes = opts.frameBytes ?? REPLY_OVERHEAD;
    this.turnaroundS = opts.turnaroundS ?? 0.002;
    this.gapS = opts.gapS ?? 0.002;
  }

  static fromFraming(framing: Pick<MapFraming, 'baud' | 'turnaroundS' | 'interRequestDelayS'> = DEFAULT_FRAMING,
                     turnaroundS?: number, gapS?: number): CostModel {
    return new CostModel({
      byteS: 10.0 / framing.baud,
      turnaroundS: turnaroundS ?? framing.turnaroundS,
      gapS: gapS ?? framing.interRequestDelayS,
    });
  }

  /** Fixed cost of a request, whatever its length. */
  get overheadS(): number {
    return (this.echoBytes + this.frameBytes) * this.byteS + this.turnaroundS + this.gapS;
  }

  /** The fixed cost expressed in data bytes: the break-even gap. */
  get overheadBytes(): number {
    return this.overheadS / this.byteS;
  }

  requestS(length: number): number {
    return this.overheadS + length * this.byteS;
  }
}

/** A run of bytes that must arrive in one reply (one or more channels). */
export interface Span {
  addr: number;
  length: number;
  rateHz: number;
  names: string[];
}

/** One read request and what it is for. */
export interface Block {
  addr: number;
  length: number;
  rateHz: number;
  channels: string[];
}

export function span(addr: number, length: number, rateHz = 1.0, names: string[] = []): Span {
  return { addr, length, rateHz, names };
}

export function blockEnd(b: { addr: number; length: number }): number {
  return b.addr + b.length;
}

/**
 * Channel byte ranges, overlapping ones merged, sorted by address. A
 * multi-byte channel is one span, so it is never split across two requests.
 */
export function spansFromChannels(channels: Iterable<ChannelLike>, rateOf?: (c: ChannelLike) => number): Span[] {
  const raw = Array.from(channels, (c) => [c.addr, c.addr + c.length, rateOf ? rateOf(c) : 1.0, c.name] as const);
  raw.sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));     // stable, like Python's sort
  const spans: Span[] = [];
  for (const [lo, hi, rate, name] of raw) {
    const last = spans[spans.length - 1];
    if (last && lo < last.addr + last.length) {
      spans[spans.length - 1] = {
        addr: last.addr,
        length: Math.max(last.addr + last.length, hi) - last.addr,
        rateHz: Math.max(last.rateHz, rate),
        names: [...last.names, name],
      };
    } else {
      spans.push({ addr: lo, length: hi - lo, rateHz: rate, names: [name] });
    }
  }
  return spans;
}

/**
 * Cheapest set of reads that covers every span. Exact (dynamic programme).
 * `spans` must be sorted and non-overlapping (spansFromChannels). A span
 * longer than maxLen is read in maxLen pieces.
 */
export function planBlocks(spans: readonly Span[], maxLen = 16, cost: CostModel = new CostModel()): Block[] {
  if (maxLen < 1) throw new Error('max_len must be at least 1');
  const n = spans.length;
  const best: number[] = [0, ...Array<number>(n).fill(Infinity)];
  const cut: number[] = Array<number>(n + 1).fill(0);
  for (let k = 1; k <= n; k++) {
    const end = blockEnd(spans[k - 1]!);
    let rate = 0;
    for (let i = k - 1; i >= 0; i--) {
      const length = end - spans[i]!.addr;
      if (length > maxLen && i < k - 1) break;
      rate = Math.max(rate, spans[i]!.rateHz);
      const pieces = Math.ceil(length / maxLen);
      const c = best[i]! + rate * (pieces * cost.overheadS + length * cost.byteS);
      if (c < best[k]!) {
        best[k] = c;
        cut[k] = i;
      }
    }
  }
  const blocks: Block[] = [];
  let k = n;
  while (k > 0) {
    const i = cut[k]!;
    const addr = spans[i]!.addr;
    const end = blockEnd(spans[k - 1]!);
    const group = spans.slice(i, k);
    const rate = Math.max(...group.map((s) => s.rateHz));
    const names = group.flatMap((s) => s.names);
    const parts: [number, number][] = [];
    for (let a = addr; a < end; a += maxLen) parts.push([a, Math.min(maxLen, end - a)]);
    for (const [a, length] of parts.reverse()) blocks.push({ addr: a, length, rateHz: rate, channels: names });
    k = i;
  }
  return blocks.reverse();
}

/** Fraction of wire time the plan needs to hit every target. > 1 = it cannot. */
export function busLoad(blocks: readonly Block[], cost: CostModel): number {
  return blocks.reduce((sum, b) => sum + b.rateHz * cost.requestS(b.length), 0);
}

/**
 * Reads per second each block should get from the scheduler. The top-rate
 * blocks share whatever wire time the slower ones leave; if the slower ones
 * alone overload the wire, everything scales down together.
 */
export function predictRates(blocks: readonly Block[], cost: CostModel): number[] {
  if (!blocks.length) return [];
  const top = Math.max(...blocks.map((b) => b.rateHz));
  let lower = 0;
  let topCycle = 0;
  for (const b of blocks) {
    if (b.rateHz < top) lower += b.rateHz * cost.requestS(b.length);
    else topCycle += cost.requestS(b.length);
  }
  if (lower >= 1.0 || topCycle <= 0) {
    const load = busLoad(blocks, cost);
    return blocks.map((b) => b.rateHz / load);
  }
  const topHz = (1.0 - lower) / topCycle;
  return blocks.map((b) => (b.rateHz >= top ? topHz : b.rateHz));
}

/**
 * addressmap.plan_blocks: the old greedy sweep, fewest (addr, length) reads
 * that cover every channel at one rate. Start at the lowest needed address,
 * take as many needed bytes as fit in one request, repeat. A multi-byte
 * channel is never split. AddressMap.blocks() uses this.
 */
export function greedyBlocks(channels: Iterable<{ addr: number; length: number }>, maxLen = 16): [number, number][] {
  const seen = new Set<string>();
  const spans: [number, number][] = [];
  for (const c of channels) {
    const key = `${c.addr},${c.addr + c.length}`;
    if (!seen.has(key)) {
      seen.add(key);
      spans.push([c.addr, c.addr + c.length]);
    }
  }
  spans.sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
  const blocks: [number, number][] = [];
  let start = -1;
  let end = -1;
  for (const [lo, hi] of spans) {
    if (start >= 0 && hi - start <= maxLen) {
      end = Math.max(end, hi);
      continue;
    }
    if (start >= 0) blocks.push([start, end - start]);
    start = lo;
    end = hi;
  }
  if (start >= 0) blocks.push([start, end - start]);
  return blocks;
}

/** The poller's plan for a map: spans at their class targets, cost from the framing. */
export function planForMap(channels: readonly ChannelLike[], framing: MapFraming, cost?: CostModel): Block[] {
  const spans = spansFromChannels(channels, (c) => targetHz(rateClassOf(c), framing));
  return planBlocks(spans, framing.maxReadLen, cost ?? CostModel.fromFraming(framing));
}
