// Simulator: Python-exact random numbers and RAM, the P2P protocol, faults,
// ISO 9141-2 5-baud init. Ports the sim parts of tests/test_link_sim.py and
// tests/test_link_robust.py (test_sim_pty.py is pty-only: skipped).
import { describe, expect, it } from 'vitest';
import golden from './golden.json';
import { PyRandom } from './pyrandom';
import { EcuModel, Faults, ISO_ADDRESS, P2P_FRAMING, P2T_FRAMING, pyRound } from './model';
import { SimTransport, createSimTransport } from './transport';
import { VirtualClock } from '../link/clock';
import { Link } from '../link/link';
import { DEFAULT_FRAMING, NoReply, buildReadRequest, buildReply, concat, initBytes, parseReply, verify, withOverrides } from '../link/protocol';

const toHex = (b: ArrayLike<number>) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
const WAKE = initBytes(P2P_FRAMING);
const rpmOf = (ram: Uint8Array) => (ram[0]! * 256 + ram[1]!) / 4;

describe('PyRandom is bit-exact with CPython random.Random', () => {
  for (const [seed, want] of Object.entries(golden.random)) {
    it(`seed ${seed}`, () => {
      const g = new PyRandom(Number(seed));
      expect(Array.from({ length: 5 }, () => g.random())).toEqual(want.random);
      expect(Array.from({ length: 10 }, () => g.randrange(256))).toEqual(want.randrange256);
      expect(Array.from({ length: 10 }, () => g.randint(1, 4))).toEqual(want.randint14);
      expect(Array.from({ length: 3 }, () => g.uniform(-12, 12))).toEqual(want.uniform);
      expect(Array.from({ length: 10 }, () => g.randrange(5))).toEqual(want.randrange5);
    });
  }
  it('round halves to even like Python', () => {
    expect([0.5, 1.5, 2.5, -0.5, -1.5, 2.4999, 2.5001].map((x) => pyRound(x) + 0)).toEqual([0, 2, 2, 0, -2, 2, 3]);
  });
});

describe('EcuModel RAM matches the Python simulator', () => {
  it.each([['1'], ['7']] as const)('seed %s, stepped every 0.25 s', (seed) => {
    const c = new VirtualClock();
    const m = new EcuModel({ clock: c.now, revEveryS: 0, seed: Number(seed) });
    const rows = [toHex(m.ram)];
    const want = golden.ram[seed];
    for (let i = 1; i < want.length; i++) {
      c.advance(0.25);
      m.step();
      rows.push(toHex(m.ram));
    }
    expect(rows).toEqual(want);
  });
  it('with revs (warm-up, rev, trims)', () => {
    const c = new VirtualClock();
    const m = new EcuModel({ clock: c.now, revEveryS: 5, seed: 3 });
    const rows: string[] = [];
    for (let i = 0; i < 100; i++) {
      c.advance(0.1);
      m.step();
      rows.push(toHex(m.ram.slice(0, 0x30)));
    }
    expect(rows).toEqual(golden.ram_rev);
  });
});

describe('P2P protocol', () => {
  it('echoes then replies (after the wake frame)', async () => {
    const t = new SimTransport(new EcuModel({ revEveryS: 0 }));   // a stock P2P: 10400 baud
    expect(t.baud).toBe(10400);
    const req = buildReadRequest(0, 2);
    await t.write(req);
    expect(toHex(await t.read(64, 100))).toBe(toHex(req));   // not woken: the echo only
    await t.write(WAKE);
    expect(toHex(await t.read(64, 100))).toBe(toHex(WAKE));
    await t.write(req);
    const got = await t.read(64, 100);
    expect(toHex(got.slice(0, 5))).toBe(toHex(req));
    expect(parseReply(got, req, 2).length).toBe(2);
  });

  it('answers Honda only at 10400 after the wake frame', async () => {
    const m = new EcuModel({ revEveryS: 0 });
    const t = new SimTransport(m);
    const req = buildReadRequest(0x76, 3);
    for (const baud of [9600, 10400]) {
      await t.setBaud(baud);
      await t.write(req);
      expect(toHex(await t.read(64, 100))).toBe(toHex(req));
    }
    await t.setBaud(9600);
    await t.write(WAKE);   // a wake frame at 9600 is noise to it
    await t.write(req);
    await t.read(64, 100);
    expect(m.woken).toBe(false);
    expect(m.hondaIgnored).toBe(1);
    await t.setBaud(10400);
    await t.write(WAKE);
    await t.read(64, 100);
    expect(m.woken).toBe(true);
    expect(m.wakes).toBe(1);
    await t.write(req);
    expect(toHex((await t.read(64, 100)).slice(5, 10))).toBe(toHex(concat([0, 6], new TextEncoder().encode('SIM'))));
    await t.setBaud(9600);
    await t.write(req);
    expect(toHex(await t.read(64, 100))).toBe(toHex(req));   // woken, still deaf at 9600
    m.powerCut();
    m.restart();
    await t.setBaud(10400);
    await t.write(req);
    expect(toHex(await t.read(64, 100))).toBe(toHex(req));   // key cycled: wake it again
    const link = new Link(t, P2P_FRAMING);
    await link.wake();
    expect(new TextDecoder().decode(await link.readMem(0x76, 3))).toBe('SIM');
    const p2t = new SimTransport(new EcuModel({ framing: P2T_FRAMING, revEveryS: 0 }));
    await p2t.write(req);   // kerpz's P2T: 9600, no wake needed
    expect(p2t.baud).toBe(9600);
    expect(toHex((await p2t.read(64, 100)).slice(5, 10))).toBe('0006' + toHex(new TextEncoder().encode('SIM')));
  });

  it('ignores bad checksums and resyncs', () => {
    const m = new EcuModel({ revEveryS: 0 });
    expect(m.feed(Array.from(buildReadRequest(0x10, 1))).length).toBe(0);   // asleep
    expect(m.feed(Array.from(WAKE)).length).toBe(0);
    expect(m.woken).toBe(true);
    const buf = [0x20, 0x05, 0x00, 0x02, 0x00];   // wrong checksum
    expect(m.feed(buf).length).toBe(0);
    buf.push(...buildReadRequest(0x10, 1));
    expect(verify(m.feed(buf))).toBe(true);
  });

  it('idles, revs and stalls', () => {
    const c = new VirtualClock();
    const m = new EcuModel({ clock: c.now, warmupS: 1, revEveryS: 0, restartAfterS: null });
    const rpm = () => { m.step(); return rpmOf(m.ram); };
    let idle = 0;
    for (let i = 0; i < 100; i++) { c.advance(0.1); idle = rpm(); }
    expect(idle).toBeGreaterThan(700);
    expect(idle).toBeLessThan(800);
    m.rev();
    let peak = 0;
    for (let i = 0; i < 30; i++) { c.advance(0.1); peak = Math.max(peak, rpm()); }
    expect(peak).toBeGreaterThan(2500);
    m.stall();
    let dead = -1;
    for (let i = 0; i < 20; i++) { c.advance(0.1); dead = rpm(); }
    expect(dead).toBe(0);
    m.powerCut();
    expect(m.feed(Array.from(buildReadRequest(0, 2))).length).toBe(0);
  });

  it('true byte timing: bytes arrive one at a time, echo first', async () => {
    const clock = new VirtualClock();
    const BYTE = 10 / 9600;
    const t = new SimTransport(new EcuModel({ framing: P2T_FRAMING, clock: clock.now, revEveryS: 0 }),
      { clock, byteTiming: true, usbLatencyS: 0 });
    const req = buildReadRequest(0x76, 4);
    await t.write(req);
    const seen: [number, number][] = [];
    while (seen.length < 12) {
      const chunk = await t.read(64, 100);
      expect(chunk.length).toBe(1);
      seen.push([clock.now(), chunk[0]!]);
    }
    expect(toHex(seen.slice(0, 5).map((x) => x[1]))).toBe(toHex(req));
    expect(seen[0]![0]).toBeCloseTo(BYTE, 9);
    expect(seen[4]![0]).toBeCloseTo(5 * BYTE, 9);
    expect(seen[5]![0]).toBeCloseTo(5 * BYTE + 0.002 + BYTE, 9);
    expect(seen[11]![0]).toBeCloseTo(12 * BYTE + 0.002, 9);
  });

  it('cable unplug and cable_dead', async () => {
    const clock = new VirtualClock();
    const m = new EcuModel({ framing: P2T_FRAMING, clock: clock.now, revEveryS: 0 });
    const t = new SimTransport(m, { clock, byteTiming: true });
    m.scheduleUnplug(1, 1);
    await t.write(buildReadRequest(0, 1));
    clock.advanceTo(1.5);
    await expect(t.read(10, 10)).rejects.toThrow(/unplugged/);
    await expect(t.open({ baud: 9600 })).rejects.toThrow(/unplugged/);
    clock.advanceTo(2.1);
    await t.open({ baud: 9600 });
    m.cableDead = true;
    await t.write(buildReadRequest(0, 1));
    expect((await t.read(64, 50)).length).toBe(0);
  });
});

describe('faults', () => {
  it('parse and presets', () => {
    const f = Faults.parse('drop=0.01, cksum=0.02,junk=0.03,slow=0.04,silent=0.05,echo=0.06,seed=9');
    expect([f.dropByte, f.badChecksum, f.junk, f.slowReply, f.noReply, f.dropEcho, f.seed]).toEqual([0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 9]);
    expect(Faults.parse('').any()).toBe(false);
    expect(() => Faults.parse('gremlins=1')).toThrow();
    expect(Faults.named('p2p', 3)).toMatchObject({ garble: 0.017, noReply: 0.004, deaf: 0.0015, deafS: 0.3, seed: 3 });
    expect(Faults.named('flaky').any()).toBe(true);
    expect(Faults.parse('garble=0.5,deaf=0.1,deaf_s=0.2')).toMatchObject({ garble: 0.5, deaf: 0.1, deafS: 0.2 });
  });

  it('a garbled request: damaged echo, no reply', async () => {
    const m = new EcuModel({ framing: P2T_FRAMING, revEveryS: 0, faults: new Faults({ garble: 1 }) });
    const t = new SimTransport(m);
    const req = buildReadRequest(0x76, 3);
    await t.write(req);
    const got = await t.read(64, 100);
    expect(got.length).toBe(5);
    expect(toHex(got)).not.toBe(toHex(req));
    expect(m.faultCounts.garble).toBe(1);
  });

  it('a deaf ECU ignores everything for deafS', async () => {
    const clock = new VirtualClock();
    const m = new EcuModel({ framing: P2T_FRAMING, clock: clock.now, revEveryS: 0, faults: new Faults({ deaf: 1e-12, deafS: 0 }) });
    const t = new SimTransport(m, { clock });
    m.deafUntil = 0.3;
    const req = buildReadRequest(0x76, 3);
    await t.write(req);
    expect((await t.read(64, 10)).length).toBe(5);   // echo only
    clock.advanceTo(0.31);
    await t.write(req);
    expect((await t.read(64, 10)).length).toBe(5 + 6);
  });
});

describe('createSimTransport presets (make_transport)', () => {
  it('sim:, sim:flaky, sim:p2t, sim:iso', async () => {
    const t = createSimTransport({ clock: new VirtualClock() });
    expect(t.byteTiming).toBe(true);
    expect(t.model.faults.any()).toBe(false);
    expect(t.label).toBe('sim:');
    const flaky = createSimTransport({ preset: 'flaky' });
    expect(flaky.model.faults.any()).toBe(true);
    expect(flaky.label).toBe('sim:flaky');
    expect(createSimTransport({ preset: 'p2p-car' }).model.faults.garble).toBe(0.017);
    expect(createSimTransport({ faultSpec: 'drop=0.5' }).model.faults.dropByte).toBe(0.5);

    const clock = new VirtualClock();
    const p2p = createSimTransport({ clock });
    expect(p2p.model.framing.baud).toBe(10400);
    expect(p2p.model.needWake).toBe(true);
    await p2p.open({ baud: 10400 });
    const f = withOverrides(P2P_FRAMING, { retries: 0 });
    const link = new Link(p2p, f, clock);
    await link.wake();
    expect(new TextDecoder().decode(await link.readMem(0x76, 3))).toBe('SIM');

    const clock2 = new VirtualClock();
    const old = createSimTransport({ clock: clock2 });   // a map still at 9600
    await old.open({ baud: 9600 });
    expect(old.baud).toBe(9600);
    const l2 = new Link(old, withOverrides(DEFAULT_FRAMING, { retries: 0 }), clock2);
    await l2.wake();
    await expect(l2.readMem(0x76, 3)).rejects.toThrow(NoReply);   // like the car: no Honda at 9600

    const clock3 = new VirtualClock();
    const p2t = createSimTransport({ preset: 'p2t', clock: clock3 });
    expect(p2t.model.framing.baud).toBe(9600);
    expect(p2t.model.needWake).toBe(false);
    expect(new TextDecoder().decode(await new Link(p2t, DEFAULT_FRAMING, clock3).readMem(0x76, 3))).toBe('SIM');

    const iso = createSimTransport({ preset: 'iso', clock: new VirtualClock() });
    expect(iso.model.hondaProtocol).toBe(false);
  });
});

describe('ISO 9141-2 (5-baud init at 0x33, Mode 01)', () => {
  async function fiveBaudInit(t: SimTransport, clock: VirtualClock) {
    clock.advance(0.5);   // W5: bus idle
    const bits = [0, ...Array.from({ length: 8 }, (_, i) => (ISO_ADDRESS >> i) & 1), 1];
    // send runs of low bits as breaks, high bits as idle
    let k = 0;
    while (k < 10) {
      const level = bits[k]!;
      let n = 1;
      while (k + n < 10 && bits[k + n] === level) n++;
      if (level === 0) {
        await t.setBreak(true);
        clock.advance(0.2 * n);
        await t.setBreak(false);
      } else {
        clock.advance(0.2 * n);
      }
      k += n;
    }
  }

  it('init, key bytes, PID 0C', async () => {
    const clock = new VirtualClock();
    const t = createSimTransport({ preset: 'iso', clock, revEveryS: 0 });
    await t.open({ baud: 10400 });
    await fiveBaudInit(t, clock);
    const sync = await t.read(3, 1000);
    expect(toHex(sync)).toBe('55');
    const kb = concat(await t.read(3, 1000), await t.read(3, 1000));
    expect(toHex(kb)).toBe('0808');
    clock.advance(0.03);
    await t.write(Uint8Array.of(0xf7));
    await t.read(1, 100);   // our echo
    const inv = await t.read(1, 100);
    expect(inv[0]).toBe(~ISO_ADDRESS & 0xff);
    clock.advance(0.06);
    const body = [0x68, 0x6a, 0xf1, 0x01, 0x0c];
    const req = Uint8Array.from([...body, body.reduce((a, b) => a + b, 0) & 0xff]);
    await t.write(req);
    let got: Uint8Array = new Uint8Array(0);
    for (let i = 0; i < 20 && got.length < 6 + 8; i++) got = concat(got, await t.read(64, 100));
    expect(toHex(got.slice(0, 6))).toBe(toHex(req));
    expect(toHex(got.slice(6, 11))).toBe('486b10410c');
    expect(t.model.isoInits).toBe(1);
    expect(t.model.isoRequests).toBe(1);
    // Honda reads go unanswered on an ISO-only ECU
    expect(t.model.hondaProtocol).toBe(false);
  });

  it('buildReply framing is what the ECU sends', () => {
    const m = new EcuModel({ framing: P2T_FRAMING, revEveryS: 0 });
    expect(toHex(m.feed(Array.from(buildReadRequest(0x76, 3))))).toBe(toHex(buildReply(new TextEncoder().encode('SIM'))));
  });
});

describe('live demo on the real clock (no cable)', () => {
  it('a Poller on createSimTransport() reads the ECU in real time', async () => {
    const { Poller } = await import('../link/poller');
    const { testMap } = await import('../link/testmap');
    const t = createSimTransport({ revEveryS: 0 });   // real clock, true byte timing
    let reads = 0;
    const poller = new Poller(t, testMap(), { onRead: () => { reads++; } });
    await poller.start();
    await new Promise((r) => setTimeout(r, 1200));
    expect(poller.linkState).toBe('ok');
    expect(new TextDecoder().decode(poller.ecuId!)).toBe('SIM-P2P\x00\x00\x01');
    expect(reads).toBeGreaterThan(10);
    await poller.stop();
    expect(poller.linkState).toBe('stopped');
  }, 10000);
});
