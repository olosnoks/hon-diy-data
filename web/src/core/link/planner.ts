/**
 * Block-read planner: which (addr, length) requests to send. Port of
 * hondalog/link/planner.py.
 *
 * A request costs the same fixed overhead however much it reads (5 echoed
 * request bytes, 3 bytes of reply framing, turnaround, gap) plus one byte
 * time per data byte. A block is read at the rate of its most demanding
 * channel, so the planner minimises  sum(rate(block) * time(block))  over
 * all ways of cutting the address-sorted channel list into blocks (exact,
 * dynamic programme).
 */

import { REPLY_OVERHEAD, REQUEST_BYTES, type FullFraming } from './protocol';

export interface ChannelLike {
  name: string;
  addr: number;
  length: number;
  rate?: string;
}

/** Time one read request takes on the wire. */
export class CostModel {
  constructor(
    readonly byteS = 10.0 / 9600,
    readonly turnaroundS = 0.002,
    readonly gapS = 0.002,
    readonly echoBytes = REQUEST_BYTES,
    readonly frameBytes = REPLY_OVERHEAD,
  ) {}

  static fromFraming(framing: FullFraming, turnaroundS?: number, gapS?: number): CostModel {
    return new CostModel(10.0 / framing.baud,
      turnaroundS ?? framing.turnaroundS,
      gapS ?? framing.interRequestDelayS);
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
export interface Span { addr: number; length: number; rateHz: number; names: string[] }

/** One read request and what it is for. */
export interface Block { addr: number; length: number; rateHz: number; channels: string[] }

/** Channel byte ranges, overlapping ones merged, sorted by address. A
 *  multi-byte channel is one span, never split across two requests. */
export function spansFromChannels<C extends ChannelLike>(channels: Iterable<C>,
  rateOf?: (c: C) => number): Span[] {
  const raw = [...channels].map((c) => ({ lo: c.addr, hi: c.addr + c.length, rate: rateOf ? rateOf(c) : 1.0, name: c.name }));
  raw.sort((a, b) => a.lo - b.lo || a.hi - b.hi);   // stable, like Python's sorted
  const spans: Span[] = [];
  for (const { lo, hi, rate, name } of raw) {
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

/** Cheapest set of reads that covers every span. Exact (dynamic programme).
 *  `spans` must be sorted and non-overlapping (spansFromChannels). A span
 *  longer than maxLen is read in maxLen pieces. */
export function planBlocks(spans: readonly Span[], maxLen = 16, cost: CostModel = new CostModel()): Block[] {
  if (maxLen < 1) throw new RangeError('maxLen must be at least 1');
  const n = spans.length;
  const best = [0, ...Array<number>(n).fill(Infinity)];
  const cut = Array<number>(n + 1).fill(0);
  for (let k = 1; k <= n; k++) {
    const end = spans[k - 1]!.addr + spans[k - 1]!.length;
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
    const end = spans[k - 1]!.addr + spans[k - 1]!.length;
    const group = spans.slice(i, k);
    const rate = Math.max(...group.map((s) => s.rateHz));
    const names = group.flatMap((s) => s.names);
    const parts: [number, number][] = [];
    for (let a = addr; a < end; a += maxLen) parts.push([a, Math.min(maxLen, end - a)]);
    for (const [a, length] of parts.reverse()) blocks.push({ addr: a, length, rateHz: rate, channels: names });
    k = i;
  }
  blocks.reverse();
  return blocks;
}

/** Fraction of wire time the plan needs to hit every target. > 1 = it cannot. */
export function busLoad(blocks: readonly Block[], cost: CostModel): number {
  let s = 0;
  for (const b of blocks) s += b.rateHz * cost.requestS(b.length);
  return s;
}

/** Reads per second each block should get from the scheduler (it never
 *  idles: the top-rate blocks share whatever wire time is left). */
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

/** The planner the Poller uses. Swap in another one (e.g. the address-map
 *  agent's `plan`) by passing an object with this shape. */
export interface BlockPlanner {
  plan(channels: readonly ChannelLike[], rateOf: (c: ChannelLike) => number, maxLen: number, cost: CostModel): Block[];
}

export const defaultPlanner: BlockPlanner = {
  plan: (channels, rateOf, maxLen, cost) => planBlocks(spansFromChannels(channels, rateOf), maxLen, cost),
};
