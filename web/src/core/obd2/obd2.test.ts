/** ISO 9141-2: init, PIDs, DTCs, freeze frame, clear, Mode 05/06.
 *  Port of tests/test_obd2.py (the poller/Honda-switching cases belong to
 *  the link agent) against the fake ECU on virtual time. */

import { describe, expect, it } from 'vitest';
import type { Transport } from '../types';
import { fromHex } from './bytes';
import { VirtualClock } from './clock';
import * as dtc from './dtc';
import {
  checksum,
  ConfirmationRequired,
  InitError,
  Iso9141Error,
  Iso9141Link,
  NoResponse,
  split9141,
} from './iso9141';
import {
  channelValue,
  clearConfirmed,
  decodeDtc,
  decodePid,
  decodeReadiness,
  dtcToBytes,
  Obd2Session,
  pidText,
  pyRound,
  readinessIncomplete,
  readinessReady,
  readinessText,
  snapshotMarkdown,
  snapshotText,
} from './obd2';
import { FakeEcu, LoopbackTransport, type FakeEcuOptions } from './testing/fakeEcu';

function rig(o: FakeEcuOptions = {}) {
  const clock = new VirtualClock();
  const ecu = new FakeEcu(o);
  const transport = new LoopbackTransport(ecu, clock);
  const link = new Iso9141Link(transport, { clock, hondaBaud: ecu.framing.baud });
  return { clock, ecu, transport, link };
}

type Ev = [number, 'break' | 'baud' | 'tx', boolean | number | Uint8Array];

/** Wraps a transport and notes break edges, baud changes and writes. */
class Recorder implements Transport {
  readonly kind = 'sim' as const;
  readonly label = 'rec';
  events: Ev[] = [];
  usbLatencyS: number;
  constructor(private inner: LoopbackTransport, private clock: VirtualClock) {
    this.usbLatencyS = inner.usbLatencyS;
  }
  open(o: { baud: number }) { return this.inner.open(o); }
  close() { return this.inner.close(); }
  async setBaud(b: number) { this.events.push([this.clock.now(), 'baud', b]); await this.inner.setBaud(b); }
  async setBreak(on: boolean) { this.events.push([this.clock.now(), 'break', on]); await this.inner.setBreak(on); }
  async write(d: Uint8Array) { this.events.push([this.clock.now(), 'tx', Uint8Array.from(d)]); await this.inner.write(d); }
  read(n: number, ms: number) { return this.inner.read(n, ms); }
  drain() { return this.inner.drain(); }
}

const r6 = (x: number) => Math.round(x * 1e6) / 1e6;

// ---- init ----

describe('init', () => {
  it('5-baud init waveform and handshake', async () => {
    const { clock, ecu, transport } = rig();
    const rec = new Recorder(transport, clock);
    const link = new Iso9141Link(rec, { clock });
    const result = await link.slowInit();
    expect(result.method).toBe('5-baud');
    expect(result.protocol).toBe('ISO 9141-2');
    expect(result.keyBytes).toEqual([0x08, 0x08]);
    expect(Math.abs(result.w1S - 0.1)).toBeLessThan(0.005);
    expect(result.edgeErrorS).toBe(0);
    const edges = rec.events.filter((e) => e[1] === 'break').map((e) => [e[0], e[2]] as [number, boolean]);
    const t0 = edges.find(([, on]) => on)![0];
    // 0x33 LSB first is 1 1 0 0 1 1 0 0: low for the start bit, bits 2-3 and bits 6-7
    const lows = edges.filter(([, on]) => on).map(([t]) => r6(t - t0));
    const highs = edges.filter(([t, on]) => !on && t >= t0).map(([t]) => r6(t - t0));
    expect(lows).toEqual([0.0, 0.6, 1.4]);
    expect(highs).toEqual([0.2, 1.0, 1.8]);
    expect(rec.events[1]!.slice(1)).toEqual(['baud', 10400]);
    expect(t0 - rec.events[1]![0]).toBeGreaterThanOrEqual(0.3); // W5
    const tx = rec.events.filter((e) => e[1] === 'tx');
    expect(Array.from(tx[0]![2] as Uint8Array)).toEqual([0xf7]); // inverted key byte 2
    const syncT = t0 + 2.0 + 0.1;
    const kb2T = syncT + (3 * 10) / 10400 + 0.02;
    expect(tx[0]![0] - kb2T).toBeGreaterThanOrEqual(0.025); // W4
    expect(tx[0]![0] - kb2T).toBeLessThanOrEqual(0.05);
    expect(ecu.isoInits).toBe(1);
    expect(link.connected).toBe(true);
  });

  it('fails cleanly when the ECU is off, and tries the fast init', async () => {
    const { ecu, link } = rig();
    ecu.powerCut();
    await expect(link.connect()).rejects.toThrow(/5-baud init.*fast init/);
    await expect(link.connect()).rejects.toBeInstanceOf(InitError);
    expect(link.connected).toBe(false);
    await expect(link.request(fromHex('0100'))).rejects.toBeInstanceOf(Iso9141Error);
  });

  it('falls back to the fast init when the ECU only does KWP', async () => {
    const { ecu, link } = rig({ isoFastInit: true });
    ecu.initToleranceS = -1; // make every 5-baud init fail
    const result = await link.connect();
    expect(result.method).toBe('fast');
    expect(result.protocol).toContain('KWP2000');
    expect(link.kwp).toBe(true);
    const session = new Obd2Session(link);
    expect(Math.abs((await session.readPid(0x0c)).value! - ecu.rpm)).toBeLessThanOrEqual(30);
    expect(await session.supportedPids()).toContain(0x0c);
  });

  it('fast init does not work on a 9141-only ECU', async () => {
    const { link } = rig();
    await expect(link.fastInit()).rejects.toThrow(/StartCommunication/);
  });

  it('sloppy bit timing is rejected by the fake ECU', async () => {
    const { clock, ecu, transport } = rig();
    await clock.sleep(1.0);
    for (const [start, end] of [[0.0, 0.23], [0.6, 1.0], [1.4, 1.8]] as const) {
      clock.advanceTo(1.0 + start);
      await transport.setBreak(true);
      clock.advanceTo(1.0 + end);
      await transport.setBreak(false);
    }
    expect(ecu.isoInits).toBe(0);
  });

  it('measures edge error and notes it when setBreak is slow', async () => {
    const { clock, transport } = rig();
    // a transport whose setBreak takes 12 ms (a busy machine): the init still
    // works on a tolerant ECU and the result says why it might not on the car
    const slow = new Recorder(transport, clock);
    const orig = slow.setBreak.bind(slow);
    slow.setBreak = async (on: boolean) => {
      await clock.sleep(0.012);
      await orig(on);
    };
    transport.ecu.initToleranceS = 0.05;
    const link = new Iso9141Link(slow, { clock });
    const r = await link.slowInit();
    expect(r.edgeErrorS).toBeCloseTo(0.012, 6);
    expect(r.notes.join(' ')).toMatch(/off the grid/);
  });
});

// ---- messages ----

describe('messages', () => {
  const msg = (payload: string) => {
    const body = fromHex('486b10' + payload);
    return Uint8Array.from([...body, checksum(body)]);
  };
  const hx = (a: Uint8Array[]) => a.map((x) => Buffer.from(x).toString('hex'));

  it('splits ISO 9141-2 messages without length bytes', () => {
    const a = msg('410c1af8');
    const b = msg('43013504200000');
    const c = msg('44');
    expect(hx(split9141(Uint8Array.from([...a, ...b, ...c])))).toEqual(['410c1af8', '43013504200000', '44']);
    const bad = Uint8Array.from([...a.slice(0, -1), 0, ...b]);
    expect(hx(split9141(bad))).toEqual(['43013504200000']); // bad sum skipped
    expect(split9141(new Uint8Array(0))).toEqual([]);
    expect(split9141(fromHex('486b'))).toEqual([]);
    const tricky = msg('4100486b1041'); // data that looks like a header inside a message
    expect(hx(split9141(Uint8Array.from([...tricky, ...a])))).toEqual(['4100486b1041', '410c1af8']);
  });

  it('keeps P3min between requests and parses replies', async () => {
    const { clock, ecu, transport } = rig();
    const rec = new Recorder(transport, clock);
    const link = new Iso9141Link(rec, { clock });
    await link.connect();
    const session = new Obd2Session(link);
    for (let i = 0; i < 5; i++) await session.readPid(0x0c);
    expect(ecu.isoRequests).toBe(5); // none ignored as "too soon"
    const tx = rec.events
      .filter((e) => e[1] === 'tx' && (e[2] as Uint8Array)[0] === 0x68 && (e[2] as Uint8Array)[1] === 0x6a)
      .map((e) => e[0]);
    const gaps = tx.slice(1).map((t, i) => t - tx[i]!);
    // request 6 bytes + P2 30 ms + reply 7 bytes + the quiet wait that doubles as P3min
    expect(gaps.every((g) => g > 0.09 && g < 0.13)).toBe(true);
  });

  it('re-initialises after five idle seconds; keepalive prevents it', async () => {
    const { clock, ecu, link } = rig();
    await link.connect();
    const session = new Obd2Session(link);
    await session.readPid(0x05);
    for (let i = 0; i < 4; i++) {
      await clock.sleep(3.0);
      expect(await link.keepalive()).toBe(false); // only 3 s idle: nothing sent
      await clock.sleep(1.2);
      expect(await link.keepalive()).toBe(true); // 4.2 s idle: PID 00 sent
    }
    expect(ecu.isoInits).toBe(1);
    expect(link.reinits).toBe(0);
    await clock.sleep(6.0); // the ECU has dropped us
    expect(link.connected).toBe(false);
    expect(Math.abs((await session.readPid(0x05)).value! - ecu.ect)).toBeLessThanOrEqual(1);
    expect(ecu.isoInits).toBe(2);
    expect(link.reinits).toBe(1);
  });
});

// ---- services ----

describe('services', () => {
  it('discovers supported PIDs and reads live values', async () => {
    const { ecu, link } = rig();
    await link.connect();
    const session = new Obd2Session(link);
    const pids = await session.supportedPids();
    expect(pids).toEqual([0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x11, 0x13, 0x14, 0x15, 0x1c]);
    const before = ecu.isoRequests;
    expect(await session.supportedPids()).toEqual(pids);
    expect(ecu.isoRequests).toBe(before); // cached
    const values = await session.readPids();
    expect([...values.keys()].sort((a, b) => a - b)).toEqual(pids.filter((p) => p !== 0x01));
    const v = (pid: number) => values.get(pid)!;
    expect(Math.abs(v(0x0c).value! - ecu.rpm)).toBeLessThanOrEqual(30);
    expect(v(0x0c).units).toBe('rpm');
    expect(Math.abs(v(0x05).value! - ecu.ect)).toBeLessThanOrEqual(1);
    expect(Math.abs(v(0x0b).value! - ecu.live.mapKpa)).toBeLessThanOrEqual(1);
    expect(Math.abs(v(0x11).value! - ecu.tps)).toBeLessThanOrEqual(1);
    expect(Math.abs(v(0x0e).value! - ecu.live.ign)).toBeLessThanOrEqual(0.5);
    expect(v(0x03).text).toMatch(/^(closed loop|open loop)/);
    expect(v(0x13).text).toBe('B1S1 B1S2');
    expect(v(0x1c).text).toBe('OBD-II (CARB)');
    expect(pidText(v(0x0c))).toContain('rpm');
    await expect(session.readPid(0x10)).rejects.toBeInstanceOf(NoResponse); // no MAF
  });

  it('reads DTCs, freeze frame and readiness', async () => {
    const codes = ['P0135', 'P1259', 'P0505', 'P1456']; // 4 codes: two reply messages
    const { link } = rig({ dtcs: codes, pendingDtcs: ['P0420'] });
    await link.connect();
    const session = new Obd2Session(link);
    const stored = await session.readDtcs();
    expect(stored.map((d) => d.code)).toEqual(codes);
    expect(stored[0]!.description).toContain('heater');
    expect(stored[1]!.description).toContain('VTEC');
    const pending = await session.readPendingDtcs();
    expect(pending.map((d) => d.code)).toEqual(['P0420']);
    expect(pending[0]!.status).toBe('pending');
    const ready = await session.readiness();
    expect(ready.milOn).toBe(true);
    expect(ready.dtcCount).toBe(4);
    expect(readinessReady(ready)).toBe(true);
    expect(new Set(Object.keys(ready.monitors))).toEqual(
      new Set(['misfire', 'fuel system', 'comprehensive components', 'catalyst', 'evaporative system', 'O2 sensor', 'O2 sensor heater']),
    );
    const ff = (await session.readFreezeFrame())!;
    expect(ff.dtc!.code).toBe('P0135');
    expect(ff.values.get(0x0c)!.value).toBe(812);
    expect(ff.values.get(0x05)!.value).toBe(84);
    expect(Math.abs(ff.values.get(0x06)!.value! - 1.6)).toBeLessThanOrEqual(0.5);
    const snap = await session.snapshot();
    const text = snapshotText(snap);
    expect(text).toContain('P1259');
    expect(text).toContain('freeze frame for P0135');
    expect(text).toContain('Engine speed');
    expect(snap.errors).toEqual([]);
    const md = snapshotMarkdown(snap);
    expect(md).toContain('## Stored codes');
    expect(md).toContain('| P1259 |');
    expect(md).toContain('## Freeze frame for P0135');
  });

  it('no codes and no freeze frame', async () => {
    const { link } = rig();
    await link.connect();
    const session = new Obd2Session(link);
    expect(await session.readDtcs()).toEqual([]);
    expect(await session.readPendingDtcs()).toEqual([]);
    expect(await session.readFreezeFrame()).toBeNull();
    const ready = await session.readiness();
    expect(ready.milOn).toBe(false);
    expect(ready.dtcCount).toBe(0);
    expect(readinessText(ready)).toContain('all monitors ready');
  });

  it('clear needs the typed confirmation and a stopped engine', async () => {
    const { clock, ecu, link } = rig({ dtcs: ['P0135'] });
    await link.connect();
    const session = new Obd2Session(link);
    for (const bad of ['', 'yes', 'clear', ' CLEAR', 'CLEAR ']) {
      expect(clearConfirmed(bad)).toBe(false);
      await expect(session.clearDtcs(bad)).rejects.toBeInstanceOf(ConfirmationRequired);
      await expect(session.clearDtcs(bad)).rejects.toThrow(/readiness/);
    }
    await expect(session.clearDtcs(Obd2Session.CLEAR_CONFIRM)).rejects.toThrow(/engine is running/);
    expect(ecu.dtcs).toEqual(['P0135']); // nothing was sent
    ecu.stall(); // key on, engine off
    await clock.sleep(1.0);
    await session.readPid(0x0c);
    await clock.sleep(1.0);
    expect(await session.clearDtcs(Obd2Session.CLEAR_CONFIRM)).toBe(true);
    expect(ecu.dtcs).toEqual([]);
    expect(await session.readDtcs()).toEqual([]);
    const ready = await session.readiness();
    expect(ready.milOn).toBe(false);
    expect(readinessReady(ready)).toBe(false);
    expect(readinessIncomplete(ready)).toContain('catalyst'); // monitors reset, as warned
    expect(await session.readFreezeFrame()).toBeNull();
    expect(Obd2Session.CLEAR_WARNING).toContain('readiness');
  });

  it('survives line faults and never returns a wrong value', async () => {
    const { ecu, link } = rig({ dtcs: ['P0135', 'P0171'] });
    await link.connect();
    ecu.setFaults({ badChecksum: 0.15, dropByte: 0.1, noReply: 0.05, seed: 2 });
    const session = new Obd2Session(link);
    let ok = 0;
    for (let i = 0; i < 60; i++) {
      let v;
      try {
        v = await session.readPid(0x05);
      } catch (e) {
        if (e instanceof NoResponse) continue;
        throw e;
      }
      expect(Math.abs(v.value! - ecu.ect)).toBeLessThanOrEqual(1);
      ok++;
    }
    expect(ok).toBeGreaterThan(45);
  });

  it('reads Mode 05 O2 tests and Mode 06 results', async () => {
    const { link } = rig();
    await link.connect();
    const session = new Obd2Session(link);
    expect(await session.supportedO2Tests()).toEqual([1, 2, 3, 4, 5]);
    const t = await session.readO2Test(0x01, 0x01);
    expect(t.name).toMatch(/Rich to lean/);
    expect(t.value).toBeCloseTo(0.45, 3);
    expect(t.units).toBe('V');
    expect(await session.supportedMode06()).toEqual([2, 3]);
    const [r2] = await session.readMode06(0x02);
    expect(r2).toMatchObject({ tid: 2, componentId: 1, value: 0x30, limit: 0x80, limitKind: 'max', pass: true });
    const [r3] = await session.readMode06(0x03);
    expect(r3).toMatchObject({ componentId: 1, limitKind: 'min', pass: false });
  });
});

// ---- decoders ----

describe('decoders', () => {
  it('decode DTCs, PIDs and readiness', () => {
    expect(decodeDtc(...dtcToBytes('P0135'))).toBe('P0135');
    expect(decodeDtc(...dtcToBytes('P1456'))).toBe('P1456');
    expect(decodeDtc(0xc1, 0x00)).toBe('U0100');
    expect(decodeDtc(0x41, 0x23)).toBe('C0123');
    expect(decodePid(0x0c, fromHex('1af8')).value).toBe(1726);
    expect(decodePid(0x05, fromHex('7b')).value).toBe(83);
    expect(decodePid(0x06, fromHex('80')).value).toBe(0);
    expect(decodePid(0x07, fromHex('90')).value).toBe(12.5);
    expect(decodePid(0x0e, fromHex('a0')).value).toBe(16);
    expect(decodePid(0x14, fromHex('5a80')).value).toBe(0.45);
    expect(decodePid(0x77, fromHex('01')).value).toBeNull();
    expect(decodePid(0x0c, fromHex('1a')).text).toBe('short reply');
    const r = decodeReadiness(Uint8Array.of(0x82, 0x07, 0x65, 0x04));
    expect(r.milOn).toBe(true);
    expect(r.dtcCount).toBe(2);
    expect(readinessIncomplete(r)).toEqual(['evaporative system']);
    expect(checksum(fromHex('686af1010c'))).toBe(0xd0);
  });

  it('rounds like Python', () => {
    expect(pyRound(6.25, 1)).toBe(6.2); // exact tie: to even
    expect(pyRound(6.35, 1)).toBe(6.3); // 6.35 is 6.3499.. in binary
    expect(decodePid(0x06, Uint8Array.of(136)).value).toBe(6.2); // (136-128)*100/128 = 6.25
    expect(decodePid(0x04, Uint8Array.of(0xff)).value).toBe(100);
  });

  it('channel values turn status bytes into flags', () => {
    for (const [status, flag] of [[0x01, 0], [0x02, 1], [0x04, 0], [0x08, 0], [0x10, 1], [0x00, 0]] as const) {
      expect(channelValue(0x03, Uint8Array.of(status, 0))).toBe(flag);
    }
    expect(channelValue(0x01, Uint8Array.of(0x81, 7, 0x65, 0))).toBe(1);
    expect(channelValue(0x01, Uint8Array.of(0x7f, 7, 0x65, 0))).toBe(0);
    expect(channelValue(0x04, Uint8Array.of(0xff))).toBe(100);
    expect(channelValue(0x15, Uint8Array.of(120, 0xff))).toBeCloseTo(0.6);
    expect(channelValue(0x03, new Uint8Array(0))).toBeNull(); // short reply: unknown
  });

  it('DTC table', () => {
    expect(dtc.describe('P1259')).toContain('VTEC');
    expect(dtc.describe('p1298 ')).toContain('ELD');
    expect(dtc.describe('P0505')).toContain('Idle');
    expect(dtc.describe('P0301')).toContain('misfire');
    expect(dtc.describe('P1999')).toContain('Honda-specific');
    expect(dtc.describe('P0999')).toContain('generic');
    expect(dtc.describe('XYZ')).toBe('(not a valid OBD2 code)');
    expect(dtc.isStallRelated('P1381')).toBe(true);
    expect(dtc.isStallRelated('P0420')).toBe(false);
    for (const code of [...Object.keys(dtc.HONDA), ...Object.keys(dtc.GENERIC)]) {
      expect(code.length).toBe(5);
      expect(code[0]).toBe('P');
      expect(decodeDtc(...dtcToBytes(code))).toBe(code);
    }
    expect(Object.keys(dtc.HONDA).every((c) => c[1] === '1')).toBe(true);
    expect(Object.keys(dtc.GENERIC).every((c) => c[1] === '0')).toBe(true);
  });
});
