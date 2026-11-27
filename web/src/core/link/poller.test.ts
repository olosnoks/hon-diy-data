// Poller: schedule, timestamps, recovery. Port of the poller parts of
// tests/test_link_robust.py and test_link_sim.py, plus golden runs against
// the Python poller (every block read, link-down moment and link error equal).
import { describe, expect, it } from 'vitest';
import golden from './golden_poller.json';
import { VirtualClock } from './clock';
import { Poller, type IsoFallback, type LinkErrorEvent } from './poller';
import { DEFAULT_FRAMING, withOverrides } from './protocol';
import { TransportError } from './link';
import { testMap, NAMES } from './testmap';
import { EcuModel, Faults, type EcuModelOptions } from '../sim/model';
import { SimTransport } from '../sim/transport';
import type { EcuRead, LinkState } from '../types';

const toHex = (b: ArrayLike<number>) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
const text = (b: Uint8Array) => new TextDecoder().decode(b);

function startPoller(model: EcuModelOptions = {}, protocol = { baud: 10400 }) {
  const clock = new VirtualClock();
  const m = new EcuModel({ clock: clock.now, revEveryS: 0, ...model });
  const transport = new SimTransport(m, { clock, byteTiming: true });
  const reads: EcuRead[] = [];
  const links: [number, LinkState, string, string][] = [];
  const errors: LinkErrorEvent[] = [];
  const cycles: number[] = [];
  const poller: Poller = new Poller(transport, testMap(protocol), {
    clock,
    onRead: (r) => reads.push(r),
    onLink: (s) => links.push([clock.now() - poller.t0, s, poller.state, poller.lastError]),
    onError: (e) => errors.push(e),
    onCycle: (t) => cycles.push(t),
  });
  return { clock, model: m, poller, reads, links, errors, cycles, transport };
}

const downs = (links: [number, LinkState, string, string][]) => links.filter((l) => l[1] === 'error').map((l) => l[0]);

describe('poller', () => {
  it('rates per channel and shortfall', async () => {
    const { poller } = startPoller();
    await poller.runUntil(20);
    await poller.stop();
    const rates = poller.channelRates();
    expect(Object.keys(rates).sort()).toEqual([...NAMES].sort());
    for (const name of ['rpm', 'map', 'tps', 'inj', 'ign', 'o2']) {
      expect(rates[name]!.achievedHz, name).toBeGreaterThan(15);
      expect(rates[name]!.worstGapS).toBeLessThan(0.12);
    }
    for (const name of ['stft', 'ltft']) expect(Math.abs(rates[name]!.achievedHz - 5) / 5).toBeLessThan(0.15);
    for (const name of ['ect', 'iat', 'batt', 'mil', 'knock']) expect(Math.abs(rates[name]!.achievedHz - 1)).toBeLessThan(0.15);
    expect(poller.busLoad).toBeGreaterThan(1);
    const short = poller.shortfalls();
    expect(short.rpm!.shortfallHz).toBeGreaterThan(0);
    expect(short.rpm!.shortfallHz).toBeLessThan(5);
    expect(short.ect).toBeUndefined();
    const classes = poller.classRates();
    expect(classes.fast![0]).toBe(20);
    expect(classes.fast![1]).toBeGreaterThan(15);
    expect(classes.fast![1]).toBeLessThan(20);
    const predicted = poller.predictedRates();
    expect(Math.abs(rates.rpm!.achievedHz - predicted.rpm!) / predicted.rpm!).toBeLessThan(0.08);
    expect(poller.errors).toBe(0);
    expect(poller.hz).toBeGreaterThan(15);
    const report = poller.linkReport();
    expect(report.timeouts).toBe(0);
    expect(report.ok).toBe(poller.reads);
    expect(poller.stats.rateHz).toBe(poller.hz);
    expect(poller.linkState).toBe('stopped');
  });

  it('reads carry their data age; cycles only once every block was read', async () => {
    const { poller, reads, cycles, links } = startPoller();
    await poller.start();
    expect(text(poller.ecuId!)).toBe('SIM-P2P\x00\x00\x01');
    expect(poller.ecuIdText).toContain('"SIM-P2P...');
    await poller.stop();
    const p2 = startPoller();
    await p2.poller.runUntil(5);
    await p2.poller.stop();
    const r = p2.reads;
    expect(p2.cycles.length).toBeGreaterThan(60);
    // the first cycle comes after every planned block was read once
    const firstCycle = p2.cycles[0]!;
    const before = new Set(r.filter((x) => x.t + x.latencyS <= firstCycle + 1e-12).map((x) => x.addr));
    expect(before.size).toBe(p2.poller.plan.length);
    for (const x of r.filter((y) => [0, 18, 36].includes(y.addr))) {   // the fast blocks that close cycles
      expect(x.latencyS).toBeGreaterThan(0.003);
      expect(x.latencyS).toBeLessThan(0.012);
    }
    for (let i = 1; i < r.length; i++) expect(r[i]!.t).toBeGreaterThan(r[i - 1]!.t);
    const rpm = r.filter((x) => x.addr === 0);
    expect(rpm.length).toBeGreaterThan(60);
    const last = rpm[rpm.length - 1]!.data;
    const v = (last[0]! * 256 + last[1]!) / 4;
    expect(v).toBeGreaterThan(600);
    expect(v).toBeLessThan(1700);
    expect(p2.links[0]![1]).toBe('connecting');
    expect(p2.links.some((l) => l[1] === 'ok')).toBe(true);
    void cycles; void reads; void links;
  });

  it('key off then key on recovers without restart', async () => {
    const { poller, links, reads, cycles } = startPoller({ stallAfterS: 3, stallMode: 'power', restartAfterS: 4 });
    await poller.runUntil(12);
    await poller.stop();
    const down = downs(links);
    expect(down.length).toBeGreaterThan(0);
    // two block reads must fail (each retried) and the line stay silent 0.6 s
    expect(down[0]!).toBeGreaterThanOrEqual(3.0);
    expect(down[0]!).toBeLessThan(4.1);
    for (let i = 1; i < down.length; i++) expect(down[i]! - down[i - 1]!).toBeLessThan(0.9);
    const back = cycles.filter((t) => t > 7);
    expect(back.length).toBeGreaterThan(0);
    expect(back[0]! - 7).toBeLessThan(2);
    expect(down.filter((t) => t > back[0]!)).toEqual([]);
    expect(poller.link.stats.wakes).toBeGreaterThanOrEqual(2);
    expect(poller.channelRates().rpm!.achievedHz).toBeGreaterThan(15);
    void reads;
  });

  it('cable unplug and replug recovers without restart', async () => {
    const { poller, model, links, cycles, clock } = startPoller();
    model.scheduleUnplug(2, 3);   // out at 2 s, back in at 5 s
    await poller.runUntil(10);
    const lost = links.filter((l) => l[2] === 'port lost');
    expect(lost.length).toBeGreaterThan(0);
    expect(lost[0]![0]).toBeGreaterThanOrEqual(2.0);
    expect(lost[0]![0]).toBeLessThan(2.2);
    expect(lost[0]![3]).toMatch(/unplugged/);
    const back = cycles.filter((t) => t > 5);
    expect(back.length).toBeGreaterThan(0);
    expect(back[0]! - 5).toBeLessThan(3);
    expect(downs(links).filter((t) => t > back[0]!)).toEqual([]);
    expect(poller.counters.reconnects).toBe(1);
    expect(poller.channelRates().rpm!.achievedHz).toBeGreaterThan(15);
    const job = poller.submit((link) => link.readMem(0x76, 3));
    await poller.runUntil(clock.now() + 0.1);
    expect(text(await job)).toBe('SIM');
    await poller.stop();
  });

  it('records why the port was lost', async () => {
    const { poller, model, errors } = startPoller();
    model.scheduleUnplug(1, 1);
    await poller.runUntil(1.5);
    expect(poller.state).toBe('port lost');
    expect(poller.lastError).toMatch(/unplugged/);
    expect(errors.some((e) => e.kind === 'PortLost' && /unplugged/.test(e.message))).toBe(true);
    await poller.stop();
  });

  it('jobs fail fast while the port is gone', async () => {
    const { poller, model, clock } = startPoller();
    await poller.runUntil(1);
    model.unplug();
    await poller.runUntil(clock.now() + 2);
    expect(poller.state).toBe('port lost');
    const job = poller.submit((link) => link.readMem(0, 1));
    await poller.runUntil(clock.now() + 0.5);
    await expect(job).rejects.toThrow(TransportError);
    await poller.stop();
    await expect(poller.submit(() => 1)).rejects.toThrow(TransportError);
  });

  it('survives a flaky line', async () => {
    const { poller, links } = startPoller({ faults: Faults.flaky(4) });
    await poller.runUntil(30);
    const s = poller.counters;
    expect(poller.state).toBe('ok');
    expect(s.retries).toBeGreaterThan(5);
    expect(s.resyncs).toBeGreaterThan(5);
    expect(s.ok).toBeGreaterThan(1200);
    expect(s.errors).toBeLessThan(0.06 * s.requests);
    expect(poller.channelRates().rpm!.achievedHz).toBeGreaterThan(13);
    expect(downs(links)).toEqual([]);   // never dropped to no reply
    await poller.stop();
  });

  it('re-plans when the ECU is slower than assumed', async () => {
    const { poller } = startPoller({ turnaroundS: 0.012 });
    const before = poller.blocks;
    await poller.runUntil(15);
    await poller.stop();
    expect(Math.abs(poller.cost.turnaroundS - 0.0125)).toBeLessThan(0.001);
    expect(poller.blocks.length).toBeLessThan(before.length);
  });

  it('burst mode reads one channel fast', async () => {
    const { poller, reads, clock } = startPoller();
    await poller.runUntil(2);
    const job = poller.burst(['rpm'], 2.0);
    const t0 = clock.now();
    await poller.runUntil(t0 + 1.9);
    expect(await job).toEqual([[0, 2]]);
    const n = reads.filter((r) => r.addr === 0 && r.data.length === 2 && r.t > t0).length;
    expect(n / 1.9).toBeGreaterThan(45);
    await poller.runUntil(t0 + 5);
    expect(poller.channelRates().ect!.achievedHz).toBeGreaterThan(0.7);
    await poller.stop();
  });

  it('short ECU deafness does not drop the link', async () => {
    const clock = new VirtualClock();
    const model = new EcuModel({ clock: clock.now, revEveryS: 0, faults: new Faults({ deaf: 1e-12, deafS: 0 }) });
    const links: LinkState[] = [];
    const readT: number[] = [];
    const deaf: { t?: number; wakes?: number } = {};
    const poller: Poller = new Poller(new SimTransport(model, { clock, byteTiming: true }), testMap(), {
      clock,
      onLink: (s) => links.push(s),
      onRead: (r) => {
        const t = r.t + r.latencyS;
        readT.push(t);
        if (t > 3 && deaf.t === undefined) {
          deaf.t = t;
          deaf.wakes = poller.link.stats.wakes;
          model.deafUntil = clock.now() + 0.4;
        }
      },
    });
    await poller.runUntil(8);
    await poller.stop();
    expect(links.filter((s) => s === 'error')).toEqual([]);
    expect(poller.link.stats.wakes).toBe(deaf.wakes);
    expect(poller.link.stats.timeouts).toBeGreaterThanOrEqual(2);
    const back = Math.min(...readT.filter((t) => t > deaf.t!));
    expect(back - deaf.t!).toBeGreaterThan(0.4);
    expect(back - deaf.t!).toBeLessThan(0.9);
    expect(poller.errorKinds.NoReply).toBeGreaterThanOrEqual(2);
  });

  it('the p2p fault preset keeps the link up', async () => {
    const { poller, model, links } = startPoller({ faults: Faults.p2p(3) });
    await poller.runUntil(120);
    await poller.stop();
    const s = poller.counters;
    expect(s.badFrames).toBeGreaterThan(20);
    expect(s.echoMismatches).toBeGreaterThanOrEqual(s.badFrames);
    expect(s.checksumFailures).toBeLessThanOrEqual(2);
    expect(model.faultCounts.deaf ?? 0).toBeGreaterThanOrEqual(3);
    let drops = 0;
    for (let i = 1; i < links.length; i++) if (links[i - 1]![1] === 'ok' && links[i]![1] === 'error') drops++;
    expect(drops).toBeLessThanOrEqual(1);
  }, 30000);

  it('reports a silent ECU and recovers by itself', async () => {
    const FAST = withOverrides(DEFAULT_FRAMING, { reply_timeout_s: 0.03, init_sequence: '', inter_request_delay_s: 0,
      retries: 0, inter_byte_timeout_s: 0.03 });
    const clock = new VirtualClock();
    const model = new EcuModel({ framing: FAST, clock: clock.now, revEveryS: 0, restartAfterS: null });
    model.powerCut();
    // (the Python test runs on the real clock; on virtual time the wire needs byte timing to take time)
    const poller = new Poller(new SimTransport(model, { clock, byteTiming: true }), testMap(), { clock, framing: FAST });
    await poller.runUntil(1);
    expect(poller.state).toBe('no reply');
    expect(poller.linkState).toBe('error');
    expect(poller.ecuId).toBeNull();
    expect(poller.lastError).toMatch(/echo only/);
    model.restart();
    await poller.runUntil(2);
    expect(poller.state).toBe('ok');
    expect(poller.ecuId).not.toBeNull();   // read once the ECU answers
    await poller.stop();
  });

  it('only read requests and the wake frame go on the wire', async () => {
    const { poller, transport } = startPoller({ faults: Faults.p2p(1), stallAfterS: 2, stallMode: 'power', restartAfterS: 2 });
    const sent: Uint8Array[] = [];
    const write = transport.write.bind(transport);
    transport.write = (d: Uint8Array) => { sent.push(Uint8Array.from(d)); return write(d); };
    await poller.runUntil(8);
    await poller.stop();
    expect(sent.length).toBeGreaterThan(100);
    for (const frame of sent) expect(poller.link.allowed(frame)).toBe(true);
  });

  it('falls back to OBD2-only when Honda reads never work', async () => {
    const clock = new VirtualClock();
    const model = new EcuModel({ clock: clock.now, revEveryS: 0, hondaProtocol: false });
    const opened: number[] = [];
    let pids = 0;
    const fallback: IsoFallback = {
      async open(_t, c, baud) {
        opened.push(c.now());
        expect(baud).toBe(10400);
        return {
          label: 'ISO 9141-2 (test)',
          async next() {
            await c.sleep(0.1);
            pids++;
            return { pid: 0x0c, raw: Uint8Array.of(0x0b, 0xb8), emit: pids % 2 === 0 };
          },
          async close() {},
        };
      },
    };
    const pidSeen: number[] = [];
    const states: LinkState[] = [];
    const poller = new Poller(new SimTransport(model, { clock, byteTiming: true }), testMap(), {
      clock, isoFallback: fallback, onPid: (_t, pid) => pidSeen.push(pid), onLink: (s) => states.push(s),
    });
    await poller.runUntil(15);
    await poller.stop();
    expect(opened.length).toBe(1);
    expect(opened[0]!).toBeGreaterThanOrEqual(12);
    expect(states).toContain('iso_only');
    expect(poller.isoLink).toBe('ISO 9141-2 (test)');
    expect(pidSeen.length).toBeGreaterThan(10);
    expect(poller.cycles).toBeGreaterThan(5);
  });

  it('OBD2 only: no Honda reads, the 5-baud init at once, retried when nobody answers', async () => {
    const clock = new VirtualClock();
    // A Honda-speaking ECU: OBD2-only must still not send a single Honda read.
    const model = new EcuModel({ clock: clock.now, revEveryS: 0 });
    const transport = new SimTransport(model, { clock, byteTiming: true });
    const sent: Uint8Array[] = [];
    const write = transport.write.bind(transport);
    transport.write = (d: Uint8Array) => { sent.push(Uint8Array.from(d)); return write(d); };
    const opened: number[] = [];
    let pids = 0;
    const fallback: IsoFallback = {
      async open(_t, c) {
        opened.push(c.now());
        await c.sleep(2.5);                                   // the 5-baud init takes its time
        if (opened.length === 1) throw new Error('no 0x55 sync');
        return {
          label: 'ISO 9141-2 (test)',
          async next() {
            await c.sleep(0.1);
            pids++;
            return { pid: 0x0d, raw: Uint8Array.of(40), emit: true };
          },
          async close() {},
        };
      },
    };
    const states: LinkState[] = [];
    const reads: number[] = [];
    const poller = new Poller(transport, testMap(), {
      clock, isoFallback: fallback, obd2Only: true, onLink: (s) => states.push(s), onRead: (r) => reads.push(r.addr),
    });
    expect(poller.mode).toBe('obd2');
    await expect(Promise.race([poller.submit((l) => l.readMem(0, 1)), poller.runUntil(1).then(() => 'ran')])).rejects.toThrow(/OBD2 only/);
    await poller.runUntil(12);
    await poller.stop();
    expect(opened[0]!).toBeLessThan(0.5);                     // straight away, not after 12 s of Honda reads
    expect(opened.length).toBe(2);                           // first init failed, retried ISO_RETRY_S later
    expect(opened[1]! - opened[0]!).toBeGreaterThanOrEqual(5);
    expect(states).toContain('iso_only');
    expect(poller.mode).toBe('obd2');
    expect(reads).toEqual([]);
    expect(sent).toEqual([]);                                 // the fake fallback owns the wire: nothing from the Honda link
    expect(pids).toBeGreaterThan(10);
  });

  it('pause stops the live reads, jobs still run, resume carries on', async () => {
    const clock = new VirtualClock();
    const model = new EcuModel({ clock: clock.now, revEveryS: 0 });
    const reads: number[] = [];
    let cycles = 0;
    const poller = new Poller(new SimTransport(model, { clock, byteTiming: true }), testMap(), {
      clock, onRead: (r) => reads.push(r.t), onCycle: () => cycles++,
    });
    await poller.runUntil(1);
    expect(reads.length).toBeGreaterThan(5);
    poller.pause();
    expect(poller.paused).toBe(true);
    const before = reads.length;
    const cyclesBefore = cycles;
    const job = poller.submit((link) => link.readMem(0x00, 4));
    await poller.runUntil(3);
    expect((await job).length).toBe(4);
    expect(reads.length).toBe(before);
    expect(cycles).toBe(cyclesBefore);
    expect(poller.linkState).toBe('ok');
    poller.resume();
    await poller.runUntil(4);
    await poller.stop();
    expect(reads.length).toBeGreaterThan(before + 5);
    expect(cycles).toBeGreaterThan(cyclesBefore);
  });

  it('obd2Only without a fallback is ignored (Honda reads)', async () => {
    const clock = new VirtualClock();
    const model = new EcuModel({ clock: clock.now, revEveryS: 0 });
    const poller = new Poller(new SimTransport(model, { clock, byteTiming: true }), testMap(), { clock, obd2Only: true });
    expect(poller.obd2Only).toBe(false);
    await poller.runUntil(1);
    await poller.stop();
    expect(poller.reads).toBeGreaterThan(5);
  });
});

// ---- golden: the same block reads as the Python poller ----

type G = { reads: [number, number, number, string][]; down: number[]; errors: [number, number, string, number][]; ecu_id: string | null; wakes: number; reconnects: number };

async function replay(want: G, until: number, model: EcuModelOptions, unplug?: [number, number]) {
  const { poller, reads, links, errors, model: m } = startPoller(model);
  if (unplug) m.scheduleUnplug(unplug[0], unplug[1]);
  await poller.runUntil(until + 0.5);
  await poller.stop();
  const got = reads.map((r) => [r.addr, r.data.length, r.t + r.latencyS, toHex(r.data)] as const).filter((r) => r[2] <= until);
  expect(got.length).toBe(want.reads.length);
  got.forEach((r, i) => {
    const w = want.reads[i]!;
    expect([r[0], r[1], r[3]], `read ${i}`).toEqual([w[0], w[1], w[3]]);
    expect(Math.abs(r[2] - w[2]), `read ${i} time`).toBeLessThan(1e-9);
  });
  const down = downs(links).filter((t) => t <= until);
  expect(down.length).toBe(want.down.length);
  down.forEach((t, i) => expect(Math.abs(t - want.down[i]!)).toBeLessThan(1e-9));
  const errs = errors.filter((e) => e.kind !== 'PortLost' && e.t <= until).map((e) => [e.t, e.addr, e.kind, e.attempt] as const);
  expect(errs.length).toBe(want.errors.length);
  errs.forEach((e, i) => {
    const w = want.errors[i]!;
    expect([e[1], e[2], e[3]]).toEqual([w[1], w[2], w[3]]);
    expect(Math.abs(e[0] - w[0])).toBeLessThan(1e-9);
  });
  expect(poller.ecuId ? toHex(poller.ecuId) : null).toBe(want.ecu_id);
  expect(poller.counters.wakes).toBe(want.wakes);
  expect(poller.counters.reconnects).toBe(want.reconnects);
}

describe('golden runs against the Python poller', () => {
  it('clean, 3 s', () => replay(golden.clean as unknown as G, 3, {}));
  it('key off at 3 s, key on at 7 s', () => replay(golden.keyoff as unknown as G, 9, { stallAfterS: 3, stallMode: 'power', restartAfterS: 4 }));
  it('P2P faults on the car (seed 3), 20 s', () => replay(golden.p2p as unknown as G, 20, { faults: Faults.p2p(3) }));
  it('cable out at 2 s for 3 s', () => replay(golden.unplug as unknown as G, 8, {}, [2, 3]));
});
