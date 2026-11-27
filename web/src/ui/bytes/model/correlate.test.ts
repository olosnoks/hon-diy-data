import { describe, expect, it } from 'vitest';
import {
  bitFormula, bitHistory, candLabel, changedBits, findValue, matchFormula, rank, rankAgainstChannel, rankShape,
} from './correlate';
import { parseMapText } from './mapdoc';
import { SweepRecording } from './sweeps';
import { driving, ram, recording, TEST_MAP } from './testram';

const MAP = parseMapText(TEST_MAP);

describe('correlation finder (tests/test_mapping_finder.py)', () => {
  it('finds the hidden copies and gets width and byte order right', () => {
    const rec = driving();
    const top = rankAgainstChannel(rec, MAP, 'rpm', { top: 3 });
    const best = top[0]!;
    expect(best.candidate).toEqual({ addr: 0x90, width: 2, endian: 'little', inverse: false });
    expect(best.score).toBeGreaterThan(0.999);
    expect(candLabel(best.candidate)).toBe('0x90 u16le');
    expect(best.slope).toBeCloseTo(0.25, 3);
    expect(Math.abs(best.offset)).toBeLessThan(2);
    expect(matchFormula(best).startsWith('x * 0.25')).toBe(true);

    const tps = rankAgainstChannel(rec, MAP, 'tps', { top: 5 });
    expect(tps[0]!.candidate).toEqual({ addr: 0xa0, width: 1, endian: 'big', inverse: false });
    expect(tps[0]!.slope).toBeGreaterThan(0.49);
    expect(tps[0]!.slope).toBeLessThan(0.51);
    const wide = tps.filter((m) => [0x9f, 0xa0].includes(m.candidate.addr) && m.candidate.width === 2);
    expect(wide.every((m) => m.score < tps[0]!.score && m.notes.length > 0)).toBe(true);
  });

  it('leaves the reference\'s own bytes out and refuses unknown channels', () => {
    const rec = driving();
    for (const m of rankAgainstChannel(rec, MAP, 'rpm', { top: 0 })) {
      expect([0x00, 0x01]).not.toContain(m.candidate.addr);
      expect(m.span).toBeGreaterThan(0);
    }
    expect(() => rankAgainstChannel(rec, MAP, 'nope')).toThrow();
  });

  it('takes an external reference at its own sample rate', () => {
    const rec = driving();
    const end = rec.t[rec.length - 1]!;
    const t = Array.from({ length: 500 }, (_, i) => (i * end) / 499);
    const volts = t.map((x) => 14 + 0.3 * Math.sin(x / 0.2 / 3));
    const top = rank(rec, t, volts, { top: 1 })[0]!;
    expect(top.candidate.addr).toBe(0x17);
    expect(top.rmse).toBeLessThan(0.1);
    expect(rank(rec, [0], [1])).toEqual([]);
    expect(rank(new SweepRecording(), t, volts)).toEqual([]);
  });

  it('ranks against a shape (thermistor runs down, counter up)', () => {
    const rec = driving();
    const end = rec.t[rec.length - 1]!;
    const shape = new Map(rankShape(rec, 'rise', 0, end, { top: 2 }).map((m) => [m.candidate.addr, m]));
    expect([...shape.keys()].sort((a, b) => a - b)).toEqual([0x10, 0x80]);
    expect(shape.get(0x10)!.rho).toBeLessThan(-0.99);
    expect(shape.get(0x80)!.rho).toBeGreaterThan(0.99);
  });

  it('scores jitter down', () => {
    const rows = Array.from({ length: 40 }, (_, i) => {
      const r = ram({ tps: i });
      r[0xd0] = 100 + (i > 20 ? 1 : 0);
      return r;
    });
    const rec = recording(rows);
    const ms = new Map(rank(rec, rec.t, rows.map((_, i) => i), { top: 0 }).map((m) => [candLabel(m.candidate), m]));
    expect(ms.get('0xD0 u8')!.score).toBeLessThan(0.5);
    expect(ms.get('0xD0 u8')!.notes[0]).toMatch(/barely moves/);
    expect(ms.get('0x14 u8')!.score).toBeGreaterThan(0.99);
  });

  it('finds a typed value', () => {
    const rec = recording(Array.from({ length: 8 }, (_, i) => ram({ rpm: 3000, batt: 13.97, noise: i })));
    const hits = findValue(rec, 3000, 'x / 4', 0, 0.01);
    expect(new Set(hits.map((h) => `${h.candidate.addr}/${h.candidate.width}/${h.candidate.endian}`)))
      .toEqual(new Set(['0/2/big', '144/2/little']));
    expect(hits[0]!.error).toBe(0);
    const volts = findValue(rec, 13.9, 'x / 10.45', 0.1, 0, [1]);
    expect(volts.map((h) => h.candidate.addr)).toEqual([0x17]);
    expect(() => findValue(rec, 1, 'x +')).toThrow();
  });

  it('finds the bits that follow a marked action', () => {
    const rows = Array.from({ length: 30 }, (_, i) => ram({ rpm: 800, ac: i >= 10 && i < 20 ? 1 : 0, noise: i % 4 }));
    const rec = recording(rows, 0.2, 0, [{ label: 'ac_on', t0: 2.0, t1: 3.9 }]);
    const found = changedBits(rec, 'ac_on');
    expect(found.map((c) => [c.addr, c.bit, c.outside, c.inside])).toEqual([[0x08, 1, 0, 1], [0x0b, 1, 0, 1], [0xb0, 4, 0, 1]]);
    expect(found[0]!.purity).toBe(1);
    expect(bitFormula(found[0]!)).toBe('bit(x, 1)');
    expect(changedBits(rec, 'nothing')).toEqual([]);
    const { bits } = bitHistory(rec, 0x08);
    expect(bits).toHaveLength(30);
    expect(bits[12]![1]).toBe(1);
    expect(bits[2]![1]).toBe(0);
  });
});
