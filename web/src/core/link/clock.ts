/**
 * Time source for the link (port of hondalog/link/clock.py).
 *
 * Everything on the link side asks a Clock for the time and to wait, never
 * `performance.now()` / `setTimeout` directly. With VirtualClock the
 * simulator, the link and the poller run on simulated time: a 60 second run
 * at exact byte timing finishes in well under a second and gives the same
 * numbers every run.
 */

export interface Clock {
  readonly virtual: boolean;
  /** Seconds, monotonic. */
  now(): number;
  /** Wait this long (virtual: moves the clock and resolves at once). */
  sleep(seconds: number): Promise<void>;
}

/** The real monotonic clock (performance.now, setTimeout). */
export class RealClock implements Clock {
  readonly virtual = false;
  now(): number {
    return performance.now() / 1000;
  }
  sleep(seconds: number): Promise<void> {
    if (!(seconds > 0)) return Promise.resolve();
    return new Promise((resolve) => setTimeout(resolve, seconds * 1000));
  }
}

/** Simulated time. `sleep` returns at once and moves the clock. */
export class VirtualClock implements Clock {
  readonly virtual = true;
  t: number;
  constructor(start = 0) {
    this.t = start;
  }
  now = (): number => this.t;
  sleep(seconds: number): Promise<void> {
    if (seconds > 0) this.t += seconds;
    return Promise.resolve();
  }
  /** Synchronous sleep, for the simulator's own bookkeeping. */
  advance(seconds: number): void {
    if (seconds > 0) this.t += seconds;
  }
  advanceTo(t: number): void {
    if (t > this.t) this.t = t;
  }
}

export const REAL: Clock = new RealClock();
