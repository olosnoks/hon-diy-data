/**
 * Time source for the ISO 9141-2 link and the diagnostic tools (port of
 * hondalog/link/clock.py, made async).
 *
 * Times are seconds. ``RealClock`` is the browser's monotonic clock;
 * ``VirtualClock`` moves only when someone sleeps, so the fake ECU, the link
 * and the tools run on simulated time and a 3 s 5-baud init finishes in
 * microseconds in a test.
 *
 * Timing feasibility of the 5-baud init in a browser
 * -------------------------------------------------
 * The address byte is bit-banged with ``Transport.setBreak`` (Web Serial
 * ``SerialPort.setSignals({ break })``): 10 bits of 200 ms, but only the
 * level *changes* need a call (0x33 = 6 edges in 2 s). What matters is the
 * error of each edge against the ideal grid t0 + k * 200 ms:
 *
 * - ``setTimeout`` in a foreground tab fires 1-4 ms late (Chrome clamps
 *   nested timers to >= 4 ms after 5 levels; ours are not nested that deep).
 *   ``RealClock.sleepUntil`` sleeps to 4 ms before the edge and then spins on
 *   ``performance.now()``, which brings that to well under 1 ms. The spin
 *   blocks the main thread for at most ``spinS`` per edge (6 x 4 ms).
 * - Edges are scheduled against absolute deadlines, never "sleep 200 ms
 *   after the last one", so errors do not add up over the byte.
 * - ``setSignals`` is a USB control transfer (FTDI SET_BREAK): about
 *   1-3 ms, with ~1 ms jitter. The constant part shifts every edge equally
 *   and does not matter; only the jitter does.
 * - A hidden or minimised tab is throttled (timers aligned to 1 s, more
 *   after 5 minutes): the init WILL fail there. Keep the tab in front while
 *   connecting; the UI should say so. Tauri's web view is not throttled
 *   while the window is shown.
 *
 * Tolerance: ISO 9141-2 asks the tester for 5 baud +-5 % (10 ms per bit);
 * real ECUs sample the middle of each bit and accept far worse. The fake ECU
 * in ``testing/fakeEcu.ts`` rejects any edge more than 15 ms off the grid
 * (as the Python simulator does), and ``Iso9141Link.slowInit`` measures the
 * real edge error and puts it in ``InitResult.notes`` when it exceeds 10 ms,
 * so a failed init on a busy machine reads as a timing problem in the report.
 */

export interface Clock {
  readonly virtual: boolean;
  /** Seconds, monotonic. */
  now(): number;
  /** Wait ``seconds`` (returns at once for <= 0). */
  sleep(seconds: number): Promise<void>;
  /** Wait until ``now() >= t`` as precisely as the platform allows. */
  sleepUntil(t: number): Promise<void>;
}

/** The browser's monotonic clock, with a precise ``sleepUntil``. */
export class RealClock implements Clock {
  readonly virtual = false;

  /** Spin (busy-wait) for the last ``spinS`` before a deadline. */
  constructor(public spinS = 0.004) {}

  now(): number {
    return performance.now() / 1000;
  }

  async sleep(seconds: number): Promise<void> {
    if (seconds > 0) await this.sleepUntil(this.now() + seconds);
  }

  async sleepUntil(t: number): Promise<void> {
    const coarse = t - this.spinS - this.now();
    if (coarse > 0) await new Promise<void>((resolve) => setTimeout(resolve, coarse * 1000));
    while (this.now() < t) {
      // busy wait: setTimeout cannot hit a 200 ms bit edge to the millisecond
    }
  }
}

/** Simulated time: ``sleep`` returns at once and moves the clock. */
export class VirtualClock implements Clock {
  readonly virtual = true;

  constructor(public t = 0) {}

  now(): number {
    return this.t;
  }

  async sleep(seconds: number): Promise<void> {
    if (seconds > 0) this.t += seconds;
  }

  async sleepUntil(t: number): Promise<void> {
    if (t > this.t) this.t = t;
  }

  advanceTo(t: number): void {
    if (t > this.t) this.t = t;
  }
}

export const REAL: Clock = new RealClock();

/** The USB adapter's latency (FTDI latency timer), when the transport tells. */
export function usbLatencyS(t: { kind: string; usbLatencyS?: number }): number {
  if (typeof t.usbLatencyS === 'number') return t.usbLatencyS;
  // Web Serial / Tauri cannot read the FTDI latency timer: assume the
  // driver default (16 ms) so timeouts are never too short.
  return t.kind === 'sim' ? 0.001 : 0.016;
}
