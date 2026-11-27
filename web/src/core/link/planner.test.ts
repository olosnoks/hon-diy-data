// Port of tests/test_planner.py (block planner and priority scheduler).
import { describe, expect, it } from 'vitest';
import { CostModel, busLoad, planBlocks, predictRates, spansFromChannels, type Block, type Span } from './planner';
import { Scheduler, rateClassOf, targetHz } from './scheduler';
import { DEFAULT_FRAMING, withOverrides } from './protocol';
import { framingOf } from './poller';
import { testMap } from './testmap';

const COST = new CostModel();   // 9600 baud, 2 ms turnaround, 2 ms gap
const span = (addr: number, length: number, rateHz = 1, names: string[] = []): Span => ({ addr, length, rateHz, names });
const block = (addr: number, length: number, rateHz = 1, channels: string[] = []): Block => ({ addr, length, rateHz, channels });
const total = (blocks: Block[], cost = COST) => blocks.reduce((s, b) => s + b.rateHz * cost.requestS(b.length), 0);
const al = (blocks: Block[]) => blocks.map((b) => [b.addr, b.length]);

describe('planner', () => {
  it('cost model numbers', () => {
    expect(COST.byteS).toBeCloseTo(1.0417e-3, 6);
    expect(COST.overheadS).toBeCloseTo(8 * COST.byteS + 0.004, 12);
    expect(COST.overheadBytes).toBeCloseTo(11.84, 2);
    expect(COST.requestS(16)).toBeCloseTo(COST.overheadS + 16 * COST.byteS, 12);
  });

  it('spans keep multi-byte channels whole and merge overlaps', () => {
    const spans = spansFromChannels([{ name: 'rpm', addr: 0, length: 2 }, { name: 'rpm_lo', addr: 1, length: 1 }, { name: 'ect', addr: 0x10, length: 1 }]);
    expect(spans.map((s) => [s.addr, s.length])).toEqual([[0, 2], [0x10, 1]]);
    expect(spans[0]!.names).toEqual(['rpm', 'rpm_lo']);
  });

  it('gap shorter than the overhead is read through, longer splits', () => {
    expect(al(planBlocks([span(0, 1), span(8, 1)], 16, COST))).toEqual([[0, 9]]);
    expect(al(planBlocks([span(0, 1), span(14, 1)], 16, COST))).toEqual([[0, 1], [14, 1]]);
  });

  it('break-even moves with the measured overhead', () => {
    expect(al(planBlocks([span(0, 1), span(14, 1)], 16, new CostModel(10 / 9600, 0.010)))).toEqual([[0, 15]]);
  });

  it('max len is respected and channels never split', () => {
    const spans = spansFromChannels(Array.from({ length: 20 }, (_, i) => ({ name: `c${i}`, addr: i * 2, length: 2 })));
    const blocks = planBlocks(spans, 16, COST);
    expect(blocks.every((b) => b.length <= 16)).toBe(true);
    const covered = new Set(blocks.flatMap((b) => Array.from({ length: b.length }, (_, k) => b.addr + k)));
    for (let a = 0; a < 40; a++) expect(covered.has(a)).toBe(true);
    for (const s of spans) expect(blocks.some((b) => b.addr <= s.addr && s.addr + s.length <= b.addr + b.length)).toBe(true);
  });

  it('oversize span is chunked', () => {
    expect(al(planBlocks([span(0, 40)], 16, COST))).toEqual([[0, 16], [16, 16], [32, 8]]);
  });

  it('plan is optimal against brute force', () => {
    const spans = ([[0, 2, 20], [2, 1, 5], [9, 1, 1], [16, 2, 1], [18, 1, 20], [21, 1, 5], [30, 1, 1], [33, 2, 20], [47, 1, 1]] as const)
      .map(([a, n, r]) => span(a, n, r));
    let best = Infinity;
    for (let mask = 0; mask < 1 << (spans.length - 1); mask++) {
      const groups: Span[][] = [[spans[0]!]];
      spans.slice(1).forEach((s, k) => {
        if (mask & (1 << k)) groups.push([s]);
        else groups[groups.length - 1]!.push(s);
      });
      if (groups.some((g) => g[g.length - 1]!.addr + g[g.length - 1]!.length - g[0]!.addr > 16)) continue;
      best = Math.min(best, groups.reduce((acc, g) => acc + Math.max(...g.map((s) => s.rateHz))
        * COST.requestS(g[g.length - 1]!.addr + g[g.length - 1]!.length - g[0]!.addr), 0));
    }
    expect(total(planBlocks(spans, 16, COST))).toBeCloseTo(best, 12);
  });

  it('riding along in a fast block depends on rate and distance', () => {
    const near = planBlocks([span(0x14, 1, 20, ['tps']), span(0x15, 1, 5, ['o2'])], 16, COST);
    expect(near.map((b) => [b.addr, b.length, b.rateHz])).toEqual([[0x14, 2, 20]]);
    const slow = planBlocks([span(0x12, 1, 20), span(0x13, 1, 1)], 16, COST);
    expect(slow.map((b) => [b.addr, b.length, b.rateHz])).toEqual([[0x12, 1, 20], [0x13, 1, 1]]);
    expect(al(planBlocks([span(0x12, 1, 20), span(0x13, 1, 1), span(0x14, 1, 20)], 16, COST))).toEqual([[0x12, 3]]);
    const far = planBlocks([span(0x12, 1, 20), span(0x1a, 1, 5)], 16, COST);
    expect(far.map((b) => [b.addr, b.length, b.rateHz])).toEqual([[0x12, 1, 20], [0x1a, 1, 5]]);
  });

  it('default map plan (same blocks as the Python poller)', () => {
    const amap = testMap();
    const f = framingOf(amap);
    const spans = spansFromChannels(amap.channels, (c) => targetHz(rateClassOf(c), f));
    const blocks = planBlocks(spans, f.maxReadLen, CostModel.fromFraming(f));
    expect(blocks.map((b) => [b.addr, b.length, b.rateHz])).toEqual(
      [[0, 3, 20], [8, 10, 1], [18, 4, 20], [23, 3, 1], [32, 3, 5], [36, 3, 20], [39, 2, 1], [60, 1, 1]]);
    expect(busLoad(blocks, CostModel.fromFraming(f))).toBeCloseTo(1.0288846153846154, 12);
    expect(blocks.flatMap((b) => b.channels).sort()).toEqual(amap.channels.map((c) => c.name).sort());
  });

  it('rate classes', () => {
    expect(rateClassOf({ name: 'rpm', addr: 0, length: 1 })).toBe('fast');
    expect(rateClassOf({ name: 'o2', addr: 0, length: 1 })).toBe('medium');
    expect(rateClassOf({ name: 'ect', addr: 0, length: 1 })).toBe('slow');
    expect(rateClassOf({ name: 'mystery', addr: 0, length: 1 })).toBe('slow');
    expect(rateClassOf({ name: 'ect', addr: 0, length: 1, rate: 'FAST' })).toBe('fast');
    expect(() => rateClassOf({ name: 'ect', addr: 0, length: 1, rate: 'warp' })).toThrow();
    const f = withOverrides(DEFAULT_FRAMING, { rate_fast_hz: 30 });
    expect([targetHz('fast', f), targetHz('medium', f), targetHz('slow', f)]).toEqual([30, 5, 1]);
  });
});

function runSchedule(blocks: Block[], cost: CostModel, seconds: number) {
  const sched = new Scheduler(blocks);
  let t = 0;
  const order: number[] = [];
  while (t < seconds) {
    const i = sched.next(t);
    t += cost.requestS(blocks[i]!.length);
    sched.done(i, t);
    order.push(i);
  }
  return { sched, t, order };
}

describe('scheduler', () => {
  it('reads every block first, fastest first', () => {
    const { order } = runSchedule([block(0, 1, 1, ['slow']), block(8, 1, 20, ['fast']), block(16, 1, 5, ['med'])], COST, 0.2);
    expect(order.slice(0, 3)).toEqual([1, 2, 0]);
  });

  it('meets targets when there is room', () => {
    const blocks = [block(0, 2, 20, ['rpm']), block(0x10, 2, 5, ['o2']), block(0x20, 8, 1, ['ect'])];
    expect(busLoad(blocks, COST)).toBeLessThan(1);
    const { sched, t } = runSchedule(blocks, COST, 20);
    const r = sched.report(t);
    expect(Object.values(r).every((x) => x.ok && x.shortfallHz === 0)).toBe(true);
    expect(Math.abs(r.o2!.achievedHz - 5) / 5).toBeLessThan(0.1);
    expect(Math.abs(r.ect!.achievedHz - 1)).toBeLessThan(0.1);
    expect(r.rpm!.achievedHz).toBeGreaterThan(40);
    const predicted = predictRates(blocks, COST)[0]!;
    expect(Math.abs(r.rpm!.achievedHz - predicted) / predicted).toBeLessThan(0.05);
  });

  it('reports shortfall when overloaded', () => {
    const blocks = [0, 1, 2].map((i) => block(i * 16, 16, 20, [`f${i}`])).concat([block(0x80, 4, 1, ['s'])]);
    expect(busLoad(blocks, COST)).toBeGreaterThan(1.5);
    const { sched, t } = runSchedule(blocks, COST, 20);
    const r = sched.report(t);
    expect(r.f0!.ok).toBe(false);
    expect(r.f0!.shortfallHz).toBeGreaterThan(5);
    expect(Object.keys(sched.shortfalls(t))).toEqual(expect.arrayContaining(['f0', 'f1', 'f2']));
    expect(r.s!.achievedHz).toBeGreaterThan(0.5);
    const rates = [0, 1, 2].map((i) => r[`f${i}`]!.achievedHz);
    expect(Math.max(...rates) - Math.min(...rates)).toBeLessThan(0.5);
    const p = predictRates(blocks, COST)[0]!;
    expect(Math.abs(r.f0!.achievedHz - p) / p).toBeLessThan(0.1);
  });

  it('a failed block does not hog the wire', () => {
    const sched = new Scheduler([block(0, 1, 20, ['a']), block(8, 1, 20, ['b'])]);
    let t = 0;
    const picks: number[] = [];
    for (let k = 0; k < 20; k++) {
      const i = sched.next(t);
      picks.push(i);
      t += 0.02;
      if (i === 0) sched.failed(0, t);
      else sched.done(1, t);
    }
    expect(picks.filter((x) => x === 1).length).toBeGreaterThanOrEqual(9);
    expect(sched.report(t).a!.achievedHz).toBe(0);
    expect(sched.failures[0]).toBeGreaterThan(0);
  });
});
