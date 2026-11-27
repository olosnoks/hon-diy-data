/** RealClock.sleepUntil precision: the 5-baud init needs bit edges within
 *  ~10 ms of the 200 ms grid (see clock.ts). Real timers, so the bound is
 *  generous; a typical run is well under 1 ms. */

import { describe, expect, it } from 'vitest';
import { RealClock, VirtualClock } from './clock';

describe('clock', () => {
  it('RealClock hits absolute deadlines to the millisecond', async () => {
    const clock = new RealClock();
    const t0 = clock.now() + 0.01;
    let worst = 0;
    for (let k = 0; k < 5; k++) {
      const due = t0 + k * 0.04;
      await clock.sleepUntil(due);
      const err = clock.now() - due;
      expect(err).toBeGreaterThanOrEqual(0); // never early
      worst = Math.max(worst, err);
    }
    expect(worst).toBeLessThan(0.01);
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
