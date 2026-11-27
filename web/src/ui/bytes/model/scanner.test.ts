import { describe, expect, it } from 'vitest';
import { checksum, fmtHex, isReadRequest, parseHex, withChecksum } from './hex';
import { DemoMemLink, MemoryScanner, type MemLink } from './scanner';

class Clock { t = 0; now = () => this.t; }

function fakeLink(values: () => number[], fail = new Set<number>()): MemLink {
  return {
    available: () => true,
    readMem: async (addr, len) => {
      if (fail.has(addr)) throw new Error('timeout');
      const v = values();
      return Uint8Array.from({ length: len }, (_, i) => v[addr + i] ?? 0);
    },
  };
}

describe('memory scanner', () => {
  it('tracks values, change heat, min/max and history', async () => {
    let n = 0;
    const clock = new Clock();
    const sc = new MemoryScanner(fakeLink(() => Array.from({ length: 256 }, (_, a) => (a === 0x14 ? n : 7))), clock.now);
    await sc.sweepOnce();
    expect(sc.cur[0x14]).toBe(0);
    expect(sc.fresh[0x14]).toBe(0);
    n = 5; clock.t = 1;
    await sc.sweepOnce();
    expect(sc.fresh[0x14]).toBe(1);
    expect(sc.changes[0x14]).toBe(1);
    expect(sc.changes[0x15]).toBe(0);
    expect([sc.minv[0x14], sc.maxv[0x14]]).toEqual([0, 5]);
    expect(sc.history(0x14)).toEqual([0, 5]);
    expect(sc.sweeps).toBe(2);
    expect(sc.recording().length).toBe(2);
    sc.clearChanges();
    expect(sc.changes[0x14]).toBe(0);
    expect([sc.minv[0x14], sc.maxv[0x14]]).toEqual([5, 5]);
    expect(sc.sweeps).toBe(0);
    expect(sc.exportText(new Date(2026, 10, 27, 1, 2, 3))).toContain('0x14 05 5 5 0');
  });

  it('skips a failed block and stops when every block fails', async () => {
    const sc = new MemoryScanner(fakeLink(() => Array(256).fill(1), new Set([0x10])));
    await sc.sweepOnce();
    expect(sc.cur[0x10]).toBe(-1);
    expect(sc.cur[0x20]).toBe(1);
    const dead = new MemoryScanner(fakeLink(() => [], new Set(Array.from({ length: 16 }, (_, i) => i * 16))));
    dead.sweeping = true;
    await dead.sweepOnce();
    expect(dead.sweeping).toBe(false);
    expect(dead.console()).toMatch(/sweep stopped: timeout/);
  });

  it('runs the console with the read-only guard', async () => {
    const sc = new MemoryScanner(new DemoMemLink(() => 0));
    expect(await sc.send(parseHex('20 05 14 01'), true)).toMatch(/checksum OK/);
    expect(await sc.send(parseHex('21 05 14 01'), true)).toMatch(/refused/);
    sc.setExpert(true);
    expect(await sc.send(parseHex('21 05 14 01'), true)).toMatch(/no reply/);
    expect(sc.expertLog).toHaveLength(1);
    const off = new MemoryScanner(null);
    expect(await off.send(parseHex('20'), true)).toBe('not connected');
  });

  it('hex helpers', () => {
    expect(fmtHex(parseHex('20,05,0x14,1'))).toBe('20 05 14 01');
    expect(checksum([0x20, 0x05, 0x14, 0x01])).toBe(0xc6);
    expect(isReadRequest(withChecksum([0x20, 0x05, 0x14, 0x01]))).toBe(true);
    expect(() => parseHex('zz')).toThrow();
  });
});
