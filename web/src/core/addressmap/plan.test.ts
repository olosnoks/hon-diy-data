// Port of the planner half of tests/test_planner.py (the scheduler is the link's).
import { describe, expect, it } from 'vitest';
import {
  busLoad, CostModel, DEFAULT_FRAMING, greedyBlocks, loadDefault, loads, planBlocks, predictRates,
  rateClassOf, span, spansFromChannels, targetHz, withOverrides, type Block, type Span,
} from './index';

const COST = new CostModel();          // 9600 baud, 2 ms turnaround, 2 ms gap

const ch = (name: string, addr: number, length = 1, rate = '') => ({ name, addr, length, rate });

function total(blocks: Block[], cost = COST): number {
  return blocks.reduce((s, b) => s + b.rateHz * cost.requestS(b.length), 0);
}

const al = (blocks: Block[]) => blocks.map((b) => [b.addr, b.length]);

describe('block planner', () => {
  it('cost model numbers', () => {
    expect(COST.byteS).toBeCloseTo(1.0417e-3, 6);
    expect(COST.overheadS).toBeCloseTo(8 * COST.byteS + 0.004, 12);
    expect(Math.abs(COST.overheadBytes - 11.84)).toBeLessThan(0.01);
    expect(COST.requestS(16)).toBeCloseTo(COST.overheadS + 16 * COST.byteS, 12);
  });

  it('spans keep multibyte channels whole and merge overlaps', () => {
    const spans = spansFromChannels([ch('rpm', 0, 2), ch('rpm_lo', 1), ch('ect', 0x10)]);
    expect(spans.map((s) => [s.addr, s.length])).toEqual([[0, 2], [0x10, 1]]);
    expect(spans[0]!.names).toEqual(['rpm', 'rpm_lo']);
  });

  it('a gap shorter than the overhead is read through', () => {
    expect(al(planBlocks([span(0, 1), span(8, 1)], 16, COST))).toEqual([[0, 9]]);
  });

  it('a gap longer than the overhead splits', () => {
    expect(al(planBlocks([span(0, 1), span(14, 1)], 16, COST))).toEqual([[0, 1], [14, 1]]);
  });

  it('the break-even moves with the measured overhead', () => {
    const slowEcu = new CostModel({ turnaroundS: 0.010 });
    expect(al(planBlocks([span(0, 1), span(14, 1)], 16, slowEcu))).toEqual([[0, 15]]);
  });

  it('max_len is respected and channels never split', () => {
    const spans = spansFromChannels(Array.from({ length: 20 }, (_, i) => ch(`c${i}`, i * 2, 2)));
    const blocks = planBlocks(spans, 16, COST);
    expect(blocks.every((b) => b.length <= 16)).toBe(true);
    const covered = new Set(blocks.flatMap((b) => Array.from({ length: b.length }, (_, i) => b.addr + i)));
    for (let a = 0; a < 40; a++) expect(covered.has(a)).toBe(true);
    for (const s of spans) expect(blocks.some((b) => b.addr <= s.addr && s.addr + s.length <= b.addr + b.length)).toBe(true);
  });

  it('an oversize span is chunked', () => {
    expect(al(planBlocks([span(0, 40)], 16, COST))).toEqual([[0, 16], [16, 16], [32, 8]]);
  });

  it('the plan is optimal against brute force', () => {
    const spans: Span[] = ([[0, 2, 20], [2, 1, 5], [9, 1, 1], [16, 2, 1], [18, 1, 20], [21, 1, 5],
      [30, 1, 1], [33, 2, 20], [47, 1, 1]] as const).map(([a, n, r]) => span(a, n, r));
    let best = Infinity;
    for (let mask = 0; mask < 1 << (spans.length - 1); mask++) {
      const groups: Span[][] = [];
      let cur = [spans[0]!];
      for (let i = 1; i < spans.length; i++) {
        if (mask & (1 << (i - 1))) {
          groups.push(cur);
          cur = [spans[i]!];
        } else cur.push(spans[i]!);
      }
      groups.push(cur);
      if (groups.some((g) => g[g.length - 1]!.addr + g[g.length - 1]!.length - g[0]!.addr > 16)) continue;
      best = Math.min(best, groups.reduce((s, g) => s + Math.max(...g.map((x) => x.rateHz))
        * COST.requestS(g[g.length - 1]!.addr + g[g.length - 1]!.length - g[0]!.addr), 0));
    }
    expect(total(planBlocks(spans, 16, COST))).toBeCloseTo(best, 12);
  });

  it('riding along in a fast block depends on rate and distance', () => {
    const near = planBlocks([span(0x14, 1, 20, ['tps']), span(0x15, 1, 5, ['o2'])], 16, COST);
    expect(near.map((b) => [b.addr, b.length, b.rateHz])).toEqual([[0x14, 2, 20]]);
    const slow = planBlocks([span(0x12, 1, 20, ['map']), span(0x13, 1, 1, ['baro'])], 16, COST);
    expect(slow.map((b) => [b.addr, b.length, b.rateHz])).toEqual([[0x12, 1, 20], [0x13, 1, 1]]);
    const mid = planBlocks([span(0x12, 1, 20), span(0x13, 1, 1), span(0x14, 1, 20)], 16, COST);
    expect(al(mid)).toEqual([[0x12, 3]]);
    const far = planBlocks([span(0x12, 1, 20), span(0x1a, 1, 5)], 16, COST);
    expect(far.map((b) => [b.addr, b.length, b.rateHz])).toEqual([[0x12, 1, 20], [0x1a, 1, 5]]);
  });

  it('the default map plan', () => {
    const amap = loadDefault();
    const f = amap.framing;
    const blocks = amap.plan();
    const names = blocks.flatMap((b) => b.channels);
    expect([...names].sort()).toEqual([...amap.names].sort());
    const fast = blocks.filter((b) => b.rateHz === f.rateFastHz);
    const fastNames = new Set(fast.flatMap((b) => b.channels));
    for (const n of ['rpm', 'map', 'tps', 'inj', 'ign']) expect(fastNames.has(n)).toBe(true);
    expect(fast.length).toBe(3);
    expect(fast.reduce((s, b) => s + b.length, 0)).toBeLessThanOrEqual(12);
    const flat = planBlocks(spansFromChannels(amap.channels), f.maxReadLen, COST);
    const old = greedyBlocks(amap.channels, f.maxReadLen).map(([a, n]) => ({ addr: a, length: n, rateHz: 1, channels: [] }));
    expect(total(flat)).toBeLessThanOrEqual(total(old) + 1e-12);
  });

  it('rate classes', () => {
    expect(rateClassOf(ch('rpm', 0))).toBe('fast');
    expect(rateClassOf(ch('o2', 0))).toBe('medium');
    expect(rateClassOf(ch('ect', 0))).toBe('slow');
    expect(rateClassOf(ch('mystery', 0))).toBe('slow');
    expect(rateClassOf(ch('ect', 0, 1, 'FAST'))).toBe('fast');
    expect(() => rateClassOf(ch('ect', 0, 1, 'warp'))).toThrow();
    const f = withOverrides(DEFAULT_FRAMING, { rate_fast_hz: 30n });
    expect([targetHz('fast', f), targetHz('medium', f), targetHz('slow', f)]).toEqual([30, 5, 1]);
  });

  it('the address map accepts a rate field', () => {
    const amap = loads('version = "t"\n[[channel]]\nname = "x"\naddr = 0x30\nrate = "fast"\n[[channel]]\nname = "y"\naddr = 0x50\n');
    expect(amap.channels.map((c) => rateClassOf(c))).toEqual(['fast', 'slow']);
  });

  it('bus load and predicted rates', () => {
    const blocks: Block[] = [
      { addr: 0, length: 2, rateHz: 20, channels: ['rpm'] },
      { addr: 0x10, length: 2, rateHz: 5, channels: ['o2'] },
      { addr: 0x20, length: 8, rateHz: 1, channels: ['ect'] },
    ];
    expect(busLoad(blocks, COST)).toBeLessThan(1);
    const rates = predictRates(blocks, COST);
    expect(rates[1]).toBe(5);
    expect(rates[2]).toBe(1);
    expect(rates[0]!).toBeGreaterThan(40);
    const over = [0, 1, 2].map((i) => ({ addr: i * 16, length: 16, rateHz: 20, channels: [`f${i}`] }));
    expect(busLoad(over, COST)).toBeGreaterThan(1);
    const r = predictRates(over, COST);
    expect(r[0]!).toBeLessThan(20);
    expect(predictRates([], COST)).toEqual([]);
    expect(CostModel.fromFraming(loadDefault().framing).byteS).toBeCloseTo(10 / 10400, 12);
  });
});
