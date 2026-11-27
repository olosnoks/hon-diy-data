/**
 * Block-read planner: which (addr, length) requests to send. Port of
 * hondalog/link/planner.py.
 *
 * One implementation for the whole app: the planning itself (spans, the
 * exact dynamic programme, bus load, predicted rates) lives in
 * src/core/addressmap/plan.ts (#2) and is re-exported here; the link keeps
 * only its positional CostModel constructor and the BlockPlanner hook the
 * Poller takes.
 *
 * A request costs the same fixed overhead however much it reads (5 echoed
 * request bytes, 3 bytes of reply framing, turnaround, gap) plus one byte
 * time per data byte. A block is read at the rate of its most demanding
 * channel, so the planner minimises  sum(rate(block) * time(block))  over
 * all ways of cutting the address-sorted channel list into blocks.
 */

import * as plan from '../addressmap/plan';
import { REPLY_OVERHEAD, REQUEST_BYTES, type FullFraming } from './protocol';

export type { Block, ChannelLike, Span } from '../addressmap/plan';
export { busLoad, planBlocks, predictRates, span, spansFromChannels } from '../addressmap/plan';

/** Time one read request takes on the wire (#2's CostModel, positional arguments). */
export class CostModel extends plan.CostModel {
  constructor(byteS = 10.0 / 9600, turnaroundS = 0.002, gapS = 0.002, echoBytes = REQUEST_BYTES, frameBytes = REPLY_OVERHEAD) {
    super({ byteS, turnaroundS, gapS, echoBytes, frameBytes });
  }

  static override fromFraming(framing: Pick<FullFraming, 'baud' | 'turnaroundS' | 'interRequestDelayS'>, turnaroundS?: number, gapS?: number): CostModel {
    return new CostModel(10.0 / framing.baud, turnaroundS ?? framing.turnaroundS, gapS ?? framing.interRequestDelayS);
  }
}

/** The planner the Poller uses. Swap in another one by passing an object with this shape. */
export interface BlockPlanner {
  plan(channels: readonly plan.ChannelLike[], rateOf: (c: plan.ChannelLike) => number, maxLen: number, cost: plan.CostModel): plan.Block[];
}

export const defaultPlanner: BlockPlanner = {
  plan: (channels, rateOf, maxLen, cost) => plan.planBlocks(plan.spansFromChannels(channels, rateOf), maxLen, cost),
};
