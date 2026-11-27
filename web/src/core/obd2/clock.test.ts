/** RealClock.sleepUntil precision: the 5-baud init needs bit edges within
 *  ~10 ms of the 200 ms grid (see clock.ts). The precision test runs on a
 *  controlled time source (a busy test machine delays real timers by tens of
 *  ms, which says nothing about the clock); the real-timer test only checks
 *  that a deadline is never met early. */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { RealClock, VirtualClock } from './clock';

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('clock', () => {
  it('RealClock hits absolute deadlines to the millisecond: coarse timer, then the spin', async () => {
    let ms = 1000;                                        // the controlled performance.now()
    vi.spyOn(performance, 'now').mockImplementation(() => (ms += 0.05));
    const asked: number[] = [];
    // a timer that wakes up 3 ms late (inside the 4 ms spin window) or exactly on time
    vi.stubGlobal('setTimeout', ((fn: () => void, delay: number) => {
      asked.push(delay);
      ms += delay + (asked.length % 2 ? 3 : 0);
      queueMicrotask(fn);
      return 0;
    }) as unknown as typeof setTimeout);
    const clock = new RealClock();
    const t0 = clock.now() + 0.01;
    let worst = 0;
    for (let k = 0; k < 5; k++) {
      const due = t0 + k * 0.2;
      await clock.sleepUntil(due);
      const err = clock.now() - due;
      expect(err).toBeGreaterThanOrEqual(0);            // never early
      worst = Math.max(worst, err);
    }
    expect(worst).toBeLessThan(0.001);
    // the coarse sleep stops spinS (4 ms) short of every deadline
    expect(asked.length).toBe(5);
    for (const d of asked.slice(1)) expect(d).toBeGreaterThan(190);
  });

  it('RealClock on real timers is never early', async () => {
    const clock = new RealClock();
    const t0 = clock.now() + 0.01;
    for (let k = 0; k < 3; k++) {
      const due = t0 + k * 0.02;
      await clock.sleepUntil(due);
      expect(clock.now() - due).toBeGreaterThanOrEqual(0);
    }
  });

  it('VirtualClock only moves when slept', async () => {
    const c = new VirtualClock(5);
    await c.sleep(-1);
    expect(c.now()).toBe(5);
    await c.sleepUntil(4);
    expect(c.now()).toBe(5);
    await c.sleep(0.5);
    expect(c.now()).toBe(5.5);
  });
});
