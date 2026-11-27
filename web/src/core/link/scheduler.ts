/**
 * Priority scheduler: which block to read next, and how well it is doing.
 * Port of hondalog/link/scheduler.py.
 *
 * Rate classes: fast (RPM, MAP, TPS, injector, ignition), medium (O2, trims,
 * VSS), slow (temperatures, battery, flags). "Most overdue first", never
 * idle: spare wire time goes to the fast class. `report` gives the achieved
 * rate per channel and the shortfall against target.
 */

import type { RateClass } from '../types';
import type { Block, ChannelLike } from './planner';
import type { FullFraming } from './protocol';

export const RATE_CLASSES: readonly RateClass[] = ['fast', 'medium', 'slow'];

export const DEFAULT_CLASS: Record<string, RateClass> = {
  rpm: 'fast', map: 'fast', tps: 'fast', inj: 'fast', ign: 'fast',
  o2: 'medium', stft: 'medium', ltft: 'medium', vss: 'medium',
  ect: 'slow', iat: 'slow', batt: 'slow', baro: 'slow',
};

const WINDOW_S = 5.0;     // achieved rates are averaged over this long
const TOLERANCE = 0.05;   // within 5% of target counts as met
const STAMPS_MAX = 1024;

export function rateClassOf(channel: ChannelLike): RateClass {
  const explicit = String(channel.rate ?? '').trim().toLowerCase();
  if (explicit) {
    if (!(RATE_CLASSES as readonly string[]).includes(explicit)) {
      throw new Error(`${channel.name}: rate must be one of fast, medium, slow, not '${explicit}'`);
    }
    return explicit as RateClass;
  }
  return DEFAULT_CLASS[channel.name] ?? 'slow';
}

export function targetHz(rateClass: RateClass, framing: FullFraming): number {
  return rateClass === 'fast' ? framing.rateFastHz : rateClass === 'medium' ? framing.rateMediumHz : framing.rateSlowHz;
}

export class ChannelRate {
  constructor(
    readonly name: string,
    readonly rateClass: RateClass,
    readonly targetHz: number,
    readonly achievedHz: number,
    readonly worstGapS: number,   // longest time between two reads in the window
  ) {}
  get ok(): boolean {
    return this.achievedHz >= this.targetHz * (1 - TOLERANCE);
  }
  /** Reads per second missing against target; 0 when within 5% of it. */
  get shortfallHz(): number {
    return this.ok ? 0 : this.targetHz - this.achievedHz;
  }
}

export class Scheduler {
  readonly blocks: Block[];
  readonly channelClass: Record<string, RateClass>;
  readonly channelTarget: Record<string, number>;
  private readonly rate: number[];
  private last: number[];
  private stamps: number[][];
  readonly reads: number[];
  readonly failures: number[];
  private lead = 0;
  private lowLast = false;
  private prevDone = -Infinity;
  /** blocks at the highest rate: one pass over these is one "cycle" */
  readonly top: ReadonlySet<number>;

  constructor(blocks: readonly Block[], channelClass: Record<string, RateClass> = {},
    channelTarget: Record<string, number> = {}) {
    this.blocks = [...blocks];
    this.channelClass = channelClass;
    this.channelTarget = channelTarget;
    this.rate = this.blocks.map((b) => Math.max(b.rateHz, 1e-6));
    this.last = this.blocks.map(() => -Infinity);
    this.stamps = this.blocks.map(() => []);
    this.reads = this.blocks.map(() => 0);
    this.failures = this.blocks.map(() => 0);
    const top = this.rate.length ? Math.max(...this.rate) : 0;
    this.top = new Set(this.rate.flatMap((r, i) => (r >= top ? [i] : [])));
  }

  /** Index of the block to read now.
   *  1. A never-read block, fastest class first.
   *  2. A slower-class block that is due, but never two in a row.
   *  3. The most overdue block, if anything is due.
   *  4. Otherwise the spare wire time goes to the fastest class. */
  next(now: number): number {
    let best = 0, bestU = -Infinity;
    let top = -1, topU = -Infinity;
    let low = -1, lowU = -Infinity;
    const { last, rate, top: topSet, lead } = this;
    for (let i = 0; i < rate.length; i++) {
      const u = (now - last[i]! + lead) * rate[i]!;
      if (u > bestU || (u === bestU && rate[i]! > rate[best]!)) {
        best = i;
        bestU = u;
      }
      if (topSet.has(i)) {
        if (u > topU) { top = i; topU = u; }
      } else if (u > lowU) {
        low = i;
        lowU = u;
      }
    }
    let pick: number;
    if (bestU === Infinity) pick = best;
    else if (lowU >= 1.0 && !this.lowLast) pick = low;
    else if (bestU >= 1.0 || top < 0) pick = (topSet.has(best) || !this.lowLast || top < 0) ? best : top;
    else pick = top;
    this.lowLast = !topSet.has(pick);
    return pick;
  }

  done(index: number, tDone: number): void {
    // A block picked now is finished one request later, so "due" looks
    // ahead by the typical request time.
    const step = tDone - this.prevDone;
    if (step > 0 && step < 0.2) this.lead = this.lead === 0 ? step : 0.9 * this.lead + 0.1 * step;
    this.prevDone = tDone;
    this.last[index] = tDone;
    const s = this.stamps[index]!;
    s.push(tDone);
    if (s.length > STAMPS_MAX) s.shift();
    this.reads[index]! += 1;
  }

  /** Push a failed block back one period so it cannot hog the wire. */
  failed(index: number, now: number): void {
    this.last[index] = now;
    this.failures[index]! += 1;
  }

  /** Forget read times (after the link was down). */
  restart(): void {
    this.last = this.blocks.map(() => -Infinity);
    for (const s of this.stamps) s.length = 0;
  }

  /** [reads per second, worst gap] for one block over the last window. */
  blockHz(index: number, now: number): [number, number] {
    const stamps = this.stamps[index]!.filter((t) => t >= now - WINDOW_S);
    if (stamps.length < 2) return [0, stamps.length ? now - stamps[0]! : Infinity];
    let worst = now - stamps[stamps.length - 1]!;
    for (let i = 1; i < stamps.length; i++) worst = Math.max(worst, stamps[i]! - stamps[i - 1]!);
    const span = stamps[stamps.length - 1]! - stamps[0]!;
    return [span > 0 ? (stamps.length - 1) / span : 0, worst];
  }

  /** Achieved rate and shortfall for every channel, by channel name. */
  report(now: number): Record<string, ChannelRate> {
    const out: Record<string, ChannelRate> = {};
    this.blocks.forEach((block, i) => {
      const [hz, worst] = this.blockHz(i, now);
      for (const name of block.channels) {
        out[name] = new ChannelRate(name, this.channelClass[name] ?? 'slow',
          this.channelTarget[name] ?? block.rateHz, hz, worst);
      }
    });
    return out;
  }

  shortfalls(now: number): Record<string, ChannelRate> {
    return Object.fromEntries(Object.entries(this.report(now)).filter(([, r]) => !r.ok));
  }
}
