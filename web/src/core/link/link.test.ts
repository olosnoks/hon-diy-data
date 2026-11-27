// Link timing, resync, retries, fault injection and the read-only guard.
// Port of the Link parts of tests/test_link_robust.py and test_link_sim.py,
// plus golden runs: whole exchange sequences (timestamps, data, errors,
// counters, fault draws) identical to the Python Link + simulator.
import { describe, expect, it } from 'vitest';
import golden from '../sim/golden.json';
import { VirtualClock } from './clock';
import { CommandBlocked, Link } from './link';
import {
  BadChecksum, BadFrame, DEFAULT_FRAMING, NoReply, ProtocolError, ShortReply, buildReadRequest,
  findAnyFrame, initBytes, withChecksum, withOverrides, type FullFraming,
} from './protocol';
import { AdaptiveTiming } from './timing';
import { EcuModel, Faults, P2P_FRAMING, type EcuModelOptions } from '../sim/model';
import { SimTransport } from '../sim/transport';
import type { Transport } from '../types';

const BYTE = 10 / 9600;
const text = (b: Uint8Array) => new TextDecoder().decode(b);
const toHex = (b: ArrayLike<number>) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');

/** Link mechanics at 9600 baud: kerpz's P2T (no wake frame). */
function rig(framing: FullFraming = DEFAULT_FRAMING, usbLatencyS = 0.001, model: EcuModelOptions = {}) {
  const clock = new VirtualClock();
  const m = new EcuModel({ framing, clock: clock.now, revEveryS: 0, ...model });
  const t = new SimTransport(m, { clock, byteTiming: true, usbLatencyS });
  return { clock, model: m, link: new Link(t, framing, clock), transport: t };
}

const near = (a: number, b: number, abs = 1e-6) => expect(Math.abs(a - b)).toBeLessThanOrEqual(abs);

describe('byte timing and timestamps', () => {
  it('true byte timing of one exchange', async () => {
    const { link } = rig(DEFAULT_FRAMING, 0);
    const r = await link.readBlock(0, 16);
    near(r.tDone - r.tRequest, 24 * BYTE + 0.002);
    near(r.turnaroundS, 0.002);
    near(r.latencyS, 19 * BYTE, 1e-12);
    const r2 = await link.readBlock(0, 2);
    near(r2.tRequest - r.tDone, 0.002, 1e-9);
    near(r2.tDone - r2.tRequest, 10 * BYTE + 0.002);
  });

  it('usb latency timer quantises delivery', async () => {
    for (const latency of [0.001, 0.016]) {
      const { link } = rig(DEFAULT_FRAMING, latency);
      const r = await link.readBlock(0, 16);
      const wire = 24 * BYTE + 0.002;
      expect(r.tDone - r.tRequest).toBeGreaterThanOrEqual(wire - 1e-9);
      expect(r.tDone - r.tRequest).toBeLessThanOrEqual(wire + latency + 1e-9);
      near(r.latencyS, 19 * BYTE + latency / 2, 1e-12);
    }
  });
});

describe('adaptive timing', () => {
  it('timeouts adapt to what is measured', async () => {
    const { clock, model, link } = rig();
    expect(link.timing.responseTimeout).toBe(0.2);
    for (let i = 0; i < 40; i++) await link.readBlock(0, 16);
    const tm = link.timing;
    near(tm.overheadS, 0.0025, 0.0006);
    near(tm.responseTimeout, 0.02, 1e-9);
    expect(tm.interByteTimeout).toBeLessThan(0.012);
    model.powerCut();
    const t0 = clock.now();
    await expect(link.readBlock(0, 16, 0)).rejects.toThrow(NoReply);
    expect(clock.now() - t0).toBeLessThan(0.06);
    near(link.timing.responseTimeout, 0.04, 1e-9);
    await expect(link.readBlock(0, 16, 0)).rejects.toThrow(NoReply);
    near(clock.now() - t0, 0.2 + 5 * BYTE + 0.002 + 0.04 + 6 * BYTE, 0.02);
  });

  it('a slow ECU is measured, not assumed', async () => {
    const { link } = rig(DEFAULT_FRAMING, 0.001, { turnaroundS: 0.03 });
    for (let i = 0; i < 40; i++) await link.readBlock(0, 8);
    near(link.timing.overheadS, 0.0305, 0.001);
    expect(link.timing.responseTimeout).toBeGreaterThan(0.06);
    expect(link.timing.responseTimeout).toBeLessThan(0.09);
    expect(link.stats.errors).toBe(0);
  });

  it('gap backs off on clustered errors and recovers', () => {
    const tm = new AdaptiveTiming(DEFAULT_FRAMING);
    expect(tm.gap).toBe(0.002);
    for (let i = 0; i < 200; i++) {
      if (i % 20 === 0) tm.onFailure();
      else tm.onSuccess(0.003, 0.001);
    }
    expect(tm.gap).toBe(0.002);
    for (let i = 0; i < 3; i++) tm.onFailure();
    expect(tm.gap).toBe(0.008);
    for (let i = 0; i < 10; i++) tm.onFailure();
    expect(tm.gap).toBe(0.05);
    for (let i = 0; i < 100; i++) tm.onSuccess(0.003, 0.001);
    expect(tm.gap).toBe(0.002);
  });

  it('fixed timing when adaptive is off', async () => {
    const { link } = rig(withOverrides(DEFAULT_FRAMING, { adaptive_timing: false }));
    for (let i = 0; i < 20; i++) await link.readBlock(0, 4);
    expect(link.timing.responseTimeout).toBe(0.2);
    expect(link.timing.interByteTimeout).toBe(0.05);
  });
});

describe('resync, retries, counters', () => {
  it('a late reply is drained and the next read gets the right data', async () => {
    const { clock, model, link } = rig();
    for (let i = 0; i < 30; i++) await link.readBlock(0, 4);
    model.faults = new Faults({ slowReply: 1, slowReplyS: 0.06 });
    const first = model.requests;
    await expect(link.readBlock(0x10, 4, 0)).rejects.toThrow(NoReply);
    const tFail = clock.now();
    expect(link.stats.timeouts).toBe(1);
    expect(model.requests).toBe(first + 1);
    model.faults = new Faults();
    const r = await link.readBlock(0x76, 3);
    expect(text(r.data)).toBe('SIM');
    expect(r.attempts).toBe(1);
    expect(link.stats.resyncs).toBe(1);
    expect(link.stats.discardedBytes).toBe(7);
    expect(r.tRequest - tFail).toBeLessThan(0.08);
  });

  it('retry with backoff hides a single bad frame', async () => {
    const { clock, model, link } = rig();
    model.faults = new Faults({ badChecksum: 1 });
    const real = model.damage.bind(model);
    let hits = 0;
    model.damage = (frame) => (hits++ === 0 ? real(frame) : [frame, 0]);
    const t0 = clock.now();
    const r = await link.readBlock(0x76, 10);
    expect(text(r.data)).toBe('SIM-P2P\x00\x00\x01');
    expect(r.attempts).toBe(2);
    const s = link.stats;
    expect([s.checksumFailures, s.retries, s.resyncs, s.ok]).toEqual([1, 1, 1, 1]);
    expect(clock.now() - t0).toBeGreaterThan(2 * 18 * BYTE + Link.BACKOFF_BASE_S);
  });

  it('retries exhausted throws the last error', async () => {
    const { model, link } = rig();
    model.faults = new Faults({ badChecksum: 1 });
    await expect(link.readBlock(0, 4, 2)).rejects.toThrow(BadChecksum);
    expect([link.stats.requests, link.stats.retries, link.stats.checksumFailures]).toEqual([3, 2, 3]);
  });

  it('junk before the reply is skipped and counted', async () => {
    const { model, link } = rig();
    model.faults = new Faults({ junk: 1, seed: 3 });
    model.faultRng = new (await import('../sim/pyrandom')).PyRandom(3);
    for (let addr = 0; addr < 64; addr += 4) {
      const r = await link.readBlock(addr, 4);
      expect(toHex(r.data)).toBe(toHex(model.ram.slice(addr, addr + 4)));
    }
    expect(link.stats.junkBytes).toBeGreaterThan(0);
    expect(link.stats.ok).toBe(16);
  });

  it('a dropped reply byte fails, then the link recovers', async () => {
    const { model, link } = rig();
    model.faults = new Faults({ dropByte: 1 });
    await expect(link.readBlock(0, 8, 0)).rejects.toThrow(ProtocolError);
    expect(link.stats.timeouts + link.stats.badFrames + link.stats.checksumFailures).toBe(1);
    model.faults = new Faults();
    expect((await link.readBlock(0, 8)).data.length).toBe(8);
  });

  it('a damaged echo is counted but the frame still counts', async () => {
    const { model, link } = rig();
    await link.readBlock(0, 4);
    expect(link.echoSeen).toBe(true);
    model.faults = new Faults({ dropEcho: 1 });
    expect(text((await link.readBlock(0x76, 3)).data)).toBe('SIM');
    expect(link.stats.echoMismatches).toBe(1);
  });

  it('a cable without echo is detected and costs no timeout', async () => {
    const { clock, link } = rig(DEFAULT_FRAMING, 0.001, { echo: false });
    await link.readBlock(0, 16);
    expect(link.echoSeen).toBe(false);
    const t0 = clock.now();
    const r = await link.readBlock(0, 16);
    expect(r.tDone - t0).toBeLessThan(24 * BYTE + 0.008);
    expect(link.stats.echoMismatches).toBe(0);
  });

  it.each([1, 2, 3])('fault soup never returns wrong data (seed %i)', async (seed) => {
    const { model, link } = rig();
    model.faults = new Faults({ dropByte: 0.05, badChecksum: 0.05, junk: 0.05, noReply: 0.03, slowReply: 0.03, dropEcho: 0.03, seed });
    model.faultRng = new (await import('../sim/pyrandom')).PyRandom(seed);
    const known = Uint8Array.from({ length: 0x40 }, (_, i) => 0x40 + i);
    // static, known contents: step() never writes 0x40-0x7F except 0x76.. (restored each read)
    let good = 0, bad = 0;
    for (let i = 0; i < 600; i++) {
      model.ram.set(known, 0x40);
      const addr = 0x40 + ((i * 7) % 48);
      try {
        const data = (await link.readBlock(addr, 8)).data;
        expect(toHex(data)).toBe(toHex(known.slice(addr - 0x40, addr - 0x40 + 8)));
        good++;
      } catch (exc) {
        if (!(exc instanceof ProtocolError)) throw exc;
        bad++;
      }
    }
    const s = link.stats;
    expect(good).toBeGreaterThan(540);
    expect(bad).toBeLessThan(60);
    expect(s.retries).toBeGreaterThan(20);
    expect(s.resyncs).toBeGreaterThanOrEqual(s.retries);
    expect(s.checksumFailures && s.timeouts && s.junkBytes).toBeTruthy();
    expect(s.ok).toBe(good);
  });

  it('faults are deterministic for a seed', async () => {
    const run = async (seed: number) => {
      const { clock, model, link } = rig(DEFAULT_FRAMING, 0.001, { faults: Faults.flaky(seed) });
      const out: string[] = [];
      for (let i = 0; i < 400; i++) {
        try {
          out.push(toHex((await link.readBlock(i % 64, 4)).data));
        } catch (exc) {
          out.push((exc as ProtocolError).kind);
        }
      }
      return JSON.stringify([out, link.stats.asDict(), model.faultCounts, clock.now()]);
    };
    expect(await run(5)).toBe(await run(5));
    expect(await run(5)).not.toBe(await run(6));
  });

  it('every failed attempt is reported', async () => {
    const { model, link } = rig();
    await link.readBlock(0, 4);
    const seen: [number, number, string, number][] = [];
    link.onError = (addr, n, exc, attempt) => seen.push([addr, n, exc.kind, attempt]);
    model.faults = new Faults({ noReply: 1 });
    await expect(link.readBlock(0x10, 4)).rejects.toThrow(NoReply);
    expect(seen).toEqual([[0x10, 4, 'NoReply', 1], [0x10, 4, 'NoReply', 2]]);
  });
});

describe('read-only guard', () => {
  it('raw blocks everything but reads until unlocked', async () => {
    const { model, link } = rig();
    const [echo, rest] = await link.raw(buildReadRequest(0x76, 3));
    expect(echo).toBe(true);
    expect(text(findAnyFrame(rest)!.slice(2, 5))).toBe('SIM');
    await link.raw(initBytes(DEFAULT_FRAMING));   // the wake bytes are allowed
    const reset = withChecksum([0x21, 0x04, 0x01]);
    const before = model.requests;
    for (const frame of [reset, Uint8Array.of(0x20, 5, 0, 0x10), Uint8Array.of(0x20, 5, 0, 0x10, 0), Uint8Array.of(0x27, 5, 0, 1, 0xd3)]) {
      await expect(link.raw(frame)).rejects.toThrow(CommandBlocked);
    }
    expect(model.requests).toBe(before);
    expect(link.unlockedFrames).toEqual([]);
    link.unlock();
    await link.raw(reset);
    expect(link.unlockedFrames.map(toHex)).toEqual([toHex(reset)]);
    link.unlock(false);
    await expect(link.raw(reset)).rejects.toThrow(CommandBlocked);
  });
});

// ---- tests/test_link_sim.py: Link against the instant (no byte timing) sim ----

const FAST = withOverrides(DEFAULT_FRAMING, { reply_timeout_s: 0.03, init_sequence: '', inter_request_delay_s: 0,
  retries: 0, inter_byte_timeout_s: 0.03 });

/** Replays canned bytes after each write. */
class Scripted implements Transport {
  readonly kind = 'sim' as const;
  readonly label = 'scripted';
  readonly clock = new VirtualClock();
  readonly usbLatencyS = 0;
  rx: number[] = [];
  sent: Uint8Array[] = [];
  constructor(private answers: Uint8Array[]) {}
  async open() {}
  async close() {}
  async setBaud() {}
  async setBreak() {}
  async write(data: Uint8Array) {
    this.sent.push(Uint8Array.from(data));
    const a = this.answers.shift();
    if (a) this.rx.push(...a);
  }
  async read(max: number, timeoutMs: number) {
    if (!this.rx.length) await this.clock.sleep(timeoutMs / 1000);
    return Uint8Array.from(this.rx.splice(0, max));
  }
  async drain() { this.rx = []; }
}

describe('Link against the instant simulator', () => {
  const vc = () => new VirtualClock();
  it('reads through the echo', async () => {
    const c = vc();
    const link = new Link(new SimTransport(new EcuModel({ framing: FAST, revEveryS: 0, clock: c.now }), { clock: c }), FAST);
    expect(text(await link.readMem(0x76, 10))).toBe('SIM-P2P\x00\x00\x01');
    expect(link.echoSeen).toBe(true);
  });
  it('works without echo', async () => {
    const c = vc();
    const link = new Link(new SimTransport(new EcuModel({ framing: FAST, revEveryS: 0, echo: false, clock: c.now }), { clock: c }), FAST);
    expect((await link.readMem(0, 16)).length).toBe(16);
    expect(link.echoSeen).toBe(false);
  });
  const req = buildReadRequest(0, 2);
  it.each([
    [new Uint8Array(0), NoReply],
    [req, NoReply],
    [Uint8Array.from([...req, 0, 5, 1]), ShortReply],
    [Uint8Array.from([...req, 0, 5, 1, 2, 3]), BadChecksum],
    [new Uint8Array(12).fill(0xff), BadFrame],
  ] as const)('malformed reply %#', async (answer, E) => {
    await expect(new Link(new Scripted([answer]), FAST).readMem(0, 2)).rejects.toThrow(E);
  });
  it('raw separates the echo', async () => {
    const c = vc();
    const link = new Link(new SimTransport(new EcuModel({ framing: FAST, revEveryS: 0, clock: c.now }), { clock: c }), FAST);
    const [echo, rest] = await link.raw(buildReadRequest(0x76, 3), 0.03, 0.3);
    expect(echo).toBe(true);
    expect(text(findAnyFrame(rest)!.slice(2, 5))).toBe('SIM');
  });
});

// ---- golden: the whole exchange sequence equals the Python run ----

type GoldenRun = { res: (string | number)[][]; stats: Record<string, number>; faults: Record<string, number>; now: number; timing: Record<string, number> };

const PY_STATS: Record<string, string> = {
  requests: 'requests', ok: 'ok', timeouts: 'timeouts', short_replies: 'shortReplies',
  checksum_failures: 'checksumFailures', bad_frames: 'badFrames', echo_mismatches: 'echoMismatches',
  junk_bytes: 'junkBytes', resyncs: 'resyncs', discarded_bytes: 'discardedBytes', retries: 'retries',
  wakes: 'wakes', reconnects: 'reconnects', errors: 'errors',
};

async function replay(want: GoldenRun, opts: { faults?: Faults; framing?: FullFraming; n: number; addrs?: number[]; length?: number }) {
  const framing = opts.framing ?? DEFAULT_FRAMING;
  const clock = new VirtualClock();
  const model = new EcuModel({ framing, clock: clock.now, revEveryS: 0, faults: opts.faults ?? null });
  const link = new Link(new SimTransport(model, { clock, byteTiming: true, usbLatencyS: 0.001 }), framing, clock);
  if (framing.baud === 10400) await link.wake();
  const res: (string | number)[][] = [];
  for (let i = 0; i < opts.n; i++) {
    const a = opts.addrs ? opts.addrs[i % opts.addrs.length]! : i % 64;
    try {
      const r = await link.readBlock(a, opts.length ?? 4);
      res.push(['ok', r.tRequest, r.tDone, toHex(r.data), r.attempts]);
    } catch (exc) {
      res.push([(exc as ProtocolError).kind, clock.now()]);
    }
  }
  expect(res.length).toBe(want.res.length);
  res.forEach((row, i) => {
    const w = want.res[i]!;
    expect(row.length, `row ${i}`).toBe(w.length);
    row.forEach((v, k) => {
      if (typeof v === 'number' && !Number.isInteger(v)) expect(Math.abs(v - (w[k] as number)), `row ${i}`).toBeLessThan(1e-9);
      else expect(v, `row ${i} col ${k}`).toEqual(w[k]);
    });
  });
  const stats = link.stats.asDict();
  for (const [py, ts] of Object.entries(PY_STATS)) expect(stats[ts], py).toBe(want.stats[py]);
  expect(model.faultCounts).toEqual(want.faults);
  expect(Math.abs(clock.now() - want.now)).toBeLessThan(1e-9);
  const tm = link.timing.asDict();
  expect(tm.responseTimeoutMs).toBeCloseTo(want.timing.response_timeout_ms!, 9);
  expect(tm.gapMs).toBeCloseTo(want.timing.gap_ms!, 9);
}

describe('golden runs against the Python Link + simulator', () => {
  it('clean line, 40 reads', () => replay(golden.link_clean as GoldenRun, { n: 40, addrs: [0x00, 0x10, 0x76] }));
  it('flaky cable, seed 5, 400 reads', () => replay(golden.link_flaky5 as GoldenRun, { faults: Faults.flaky(5), n: 400 }));
  it('fault soup, seed 2, 300 reads', () => replay(golden.link_soup as GoldenRun, {
    faults: new Faults({ dropByte: 0.05, badChecksum: 0.05, junk: 0.05, noReply: 0.03, slowReply: 0.03, dropEcho: 0.03, seed: 2 }), n: 300,
  }));
  it('P2P on the car (Faults.p2p seed 3) at 10400 after the wake frame, 600 reads', () =>
    replay(golden.link_p2p3 as GoldenRun, { faults: Faults.p2p(3), framing: P2P_FRAMING, n: 600 }));
});
