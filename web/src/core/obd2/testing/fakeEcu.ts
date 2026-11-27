/**
 * A simulated ECU on the K-line for the obd2/ and diag/ tests: the protocol
 * parts of hondalog/sim.py (EcuModel) and transport.py (LoopbackTransport
 * with byte timing) on a VirtualClock.
 *
 * - Honda RAM reads (20 05 addr len cksum) at ``framing.baud``; a 10400 baud
 *   ECU (the P2P) answers only after the wake frame.
 * - ISO 9141-2 at 10400 baud: 5-baud init at 0x33 with W1-W5 and a bit-edge
 *   tolerance, key bytes 08 08, P3min/P3max, Mode 01/02/03/04/07.
 * - Optional KWP2000 fast init.
 * - Faults: bad checksum, dropped byte, no reply (seeded).
 *
 * Engine values are fixed numbers (no engine model): the tests compare what
 * the link decodes with these fields.
 */

import type { Transport } from '../../types';
import { concat, equal, fromHex, indexOf, sum } from '../bytes';
import type { VirtualClock } from '../clock';
import { dtcToBytes } from '../obd2';

export const ISO_BAUD = 10400;
const ISO_ADDRESS = 0x33;
const BIT_5BAUD_S = 0.2;
const P3_MIN_S = 0.055;
const P3_MAX_S = 5.0;
const W5_S = 0.3;
const KWP_START = fromHex('c133f18166');
const KWP_START_REPLY = fromHex('83f110c1e98fbd');
export const WAKE = fromHex('686af5afbfb3b2c1dbb3e9');

export interface SimFraming {
  baud: number;
  cmdRead: number;
  replyHeader: number;
  replyLenExtra: number;
  initSequence: Uint8Array;
}
export const P2P_FRAMING: SimFraming = { baud: 10400, cmdRead: 0x20, replyHeader: 0, replyLenExtra: 3, initSequence: WAKE };
export const P2T_FRAMING: SimFraming = { ...P2P_FRAMING, baud: 9600 };

export interface Faults {
  dropByte?: number;
  badChecksum?: number;
  noReply?: number;
  seed?: number;
}

interface Chunk { data: Uint8Array; gapS: number }
interface Reply { chunks: Chunk[]; delayS: number; baud: number }

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const u8c = (v: number) => Math.max(0, Math.min(255, Math.round(v)));

export interface FakeEcuOptions {
  framing?: SimFraming;
  needWake?: boolean;
  hondaProtocol?: boolean;
  iso?: boolean;
  isoFastInit?: boolean;
  dtcs?: string[];
  pendingDtcs?: string[];
  turnaroundS?: number;
  maxReadLen?: number | null;
  faults?: Faults;
}

export class FakeEcu {
  framing: SimFraming;
  needWake: boolean;
  woken = false;
  hondaProtocol: boolean;
  iso: boolean;
  isoFastInit: boolean;
  cableDead = false;
  powered = true;
  running = true;
  echo = true;
  turnaroundS: number;
  maxReadLen: number | null;
  dtcs: string[];
  pendingDtcs: string[];
  freeze: { dtc: string; rpm: number; ect: number; mapKpa: number; vss: number; load: number; stft: number; ltft: number; fuel: number } | null;
  monitorsIncomplete = false;
  isoInits = 0;
  isoRequests = 0;
  requests = 0;
  initToleranceS = 0.015;
  faults: Faults;
  private rng: () => number;
  ram: Uint8Array;
  /** Honda read handler override (tests: dead addresses). Return null = silent. */
  hondaHook: ((addr: number, length: number) => Uint8Array | null | undefined) | null = null;

  rpm = 750;
  ect = 85;
  tps = 0;
  live = { mapKpa: 34, o2: 0.45, stft: 1.6, ltft: 2.3, ign: 12, iat: 28, closedLoop: true };

  private rxbuf: number[] = [];
  private lastRx = -Infinity;
  private lastActivity = -Infinity;
  private lows: [number, number][] = [];
  private isoState: 'off' | 'await_kb' | 'active' = 'off';
  private isoKwp = false;
  private isoLast = 0;
  private isoBusyUntil = 0;
  private fastArmedUntil = -Infinity;

  constructor(o: FakeEcuOptions = {}) {
    this.framing = o.framing ?? P2P_FRAMING;
    this.needWake = o.needWake ?? this.framing.baud === ISO_BAUD;
    this.hondaProtocol = o.hondaProtocol ?? true;
    this.iso = o.iso ?? true;
    this.isoFastInit = o.isoFastInit ?? false;
    this.dtcs = [...(o.dtcs ?? [])];
    this.pendingDtcs = [...(o.pendingDtcs ?? [])];
    this.turnaroundS = o.turnaroundS ?? 0.002;
    this.maxReadLen = o.maxReadLen ?? null;
    this.faults = o.faults ?? {};
    this.rng = mulberry32(this.faults.seed ?? 1);
    this.freeze = this.dtcs.length
      ? { dtc: this.dtcs[0]!, rpm: 812, ect: 84, mapKpa: 34, vss: 0, load: 22, stft: 1.6, ltft: 2.3, fuel: 2 }
      : null;
    const r = mulberry32(7);
    this.ram = Uint8Array.from({ length: 256 }, () => Math.floor(r() * 256));
    this.ram.set(new TextEncoder().encode('SIM-P2P'), 0x76);
    this.ram.set([0, 0, 1], 0x7d);
  }

  setFaults(f: Faults): void {
    this.faults = f;
    this.rng = mulberry32(f.seed ?? 1);
  }

  /** Engine dies, ECU stays alive (RPM reads 0). */
  stall(): void {
    this.running = false;
    this.rpm = 0;
  }

  /** ECU loses power: engine dies and the K-line goes silent. */
  powerCut(): void {
    this.stall();
    this.powered = false;
    this.woken = false;
  }

  private hit(p: number | undefined): boolean {
    return !!p && this.rng() < p;
  }

  private damage(frame: Uint8Array): Uint8Array {
    const f = this.faults;
    if (!(f.dropByte || f.badChecksum || f.noReply)) return frame;
    if (this.hit(f.noReply)) return new Uint8Array(0);
    const out = Array.from(frame);
    if (this.hit(f.dropByte)) out.splice(Math.floor(this.rng() * out.length), 1);
    if (this.hit(f.badChecksum)) {
      const i = Math.floor(this.rng() * out.length);
      out[i] = out[i]! ^ (1 << Math.floor(this.rng() * 8));
    }
    return Uint8Array.from(out);
  }

  // -- Honda --

  private buildReply(data: Uint8Array): Uint8Array {
    const f = this.framing;
    const body = concat([f.replyHeader >= 0 ? f.replyHeader : 0, (data.length + f.replyLenExtra) & 0xff], data);
    return concat(body, [-sum(body) & 0xff]);
  }

  private handle(frame: number[]): Uint8Array | null {
    this.requests += 1;
    if (!this.powered || !this.hondaProtocol || frame[0] !== this.framing.cmdRead || frame.length !== 5) return null;
    if (this.needWake && !this.woken) return null;
    const addr = frame[2]!;
    const length = frame[3]!;
    if (this.hondaHook) {
      const r = this.hondaHook(addr, length);
      if (r === null) return null;
      if (r) return this.buildReply(r);
    }
    if (length === 0 || (this.maxReadLen !== null && length > this.maxReadLen)) return null;
    const data = Uint8Array.from({ length }, (_, i) => this.ram[(addr + i) & 0xff]!);
    return this.buildReply(data);
  }

  private wakeIn(): void {
    const seq = this.framing.initSequence;
    if (!seq.length) return;
    let at = indexOf(this.rxbuf, seq);
    while (at >= 0) {
      this.rxbuf.splice(at, seq.length);
      if (this.powered) this.woken = true;
      at = indexOf(this.rxbuf, seq);
    }
  }

  private frames(): Uint8Array[] {
    const out: Uint8Array[] = [];
    const buf = this.rxbuf;
    while (buf.length >= 2) {
      const size = buf[1]!;
      if (size < 3 || size > 16) {
        buf.shift();
        continue;
      }
      if (buf.length < size) break;
      if ((sum(buf, 0, size) & 0xff) === 0) {
        const reply = this.handle(buf.slice(0, size));
        if (reply) out.push(reply);
        buf.splice(0, size);
      } else {
        buf.shift();
      }
    }
    return out;
  }

  // -- the wire --

  private isoAlive(now: number): boolean {
    if (this.isoState !== 'off' && now - this.isoLast > P3_MAX_S) this.isoState = 'off';
    return this.isoState !== 'off';
  }

  /** Bytes from the tester, complete on the wire at ``now``. */
  onBytes(data: Uint8Array, baud: number, now: number): Reply[] {
    const gap = now - this.lastRx;
    this.lastRx = this.lastActivity = now;
    const isoOk = baud === ISO_BAUD && this.powered && this.iso;
    const fastStart = isoOk && this.isoFastInit && equal(data, KWP_START) && now <= this.fastArmedUntil + 0.1;
    if (this.isoAlive(now) || fastStart || baud !== this.framing.baud) {
      this.rxbuf = [];
      if (!isoOk) return [];
      return this.isoBytes(data, now);
    }
    if (gap > 0.05) this.rxbuf = [];
    this.rxbuf.push(...data);
    this.wakeIn();
    const chunks: Chunk[] = [];
    for (const frame of this.frames()) {
      const d = this.damage(frame);
      if (!d.length) continue;
      chunks.push({ data: d, gapS: chunks.length ? this.turnaroundS : 0 });
    }
    return chunks.length ? [{ chunks, delayS: this.turnaroundS, baud: this.framing.baud }] : [];
  }

  /** The tester held the line low from ``start`` to ``end``. */
  onBreak(start: number, end: number): Reply[] {
    const idleBefore = start - this.lastActivity;
    this.lastActivity = end;
    if (!this.powered || !this.iso) return [];
    if (this.lows.length && start - this.lows[0]![0] > 10 * BIT_5BAUD_S + 0.1) this.lows = [];
    if (!this.lows.length) {
      if (end - start >= 0.02 && end - start <= 0.03) {
        this.fastArmedUntil = end + 0.1; // fast init wake-up pattern
        return [];
      }
      if (idleBefore < W5_S) return []; // bus was not idle: ignored
    }
    this.lows.push([start, end]);
    const t0 = this.lows[0]![0];
    for (const [s, e] of this.lows) {
      for (const edge of [s, e]) {
        const k = (edge - t0) / BIT_5BAUD_S;
        if (Math.abs(k - Math.round(k)) * BIT_5BAUD_S > this.initToleranceS) {
          this.lows = [];
          return [];
        }
      }
    }
    const bits: number[] = [];
    for (let k = 0; k < 10; k++) {
      const centre = t0 + (k + 0.5) * BIT_5BAUD_S;
      bits.push(this.lows.some(([s, e]) => s <= centre && centre < e) ? 0 : 1);
    }
    const want = [0];
    for (let i = 0; i < 8; i++) want.push((ISO_ADDRESS >> i) & 1);
    want.push(1);
    if (!equal(bits, want)) return [];
    this.lows = [];
    this.isoInits += 1;
    this.isoState = 'await_kb';
    this.isoKwp = false;
    this.isoLast = t0 + 10 * BIT_5BAUD_S;
    const w1 = 0.1;
    this.isoBusyUntil = this.isoLast + w1;
    return [{
      chunks: [{ data: Uint8Array.of(0x55), gapS: 0 }, { data: Uint8Array.of(0x08), gapS: 0.01 }, { data: Uint8Array.of(0x08), gapS: 0.01 }],
      delayS: this.isoLast + w1 - end,
      baud: ISO_BAUD,
    }];
  }

  private isoBytes(data: Uint8Array, now: number): Reply[] {
    if (this.isoState === 'await_kb') {
      if (data.length === 1 && data[0] === 0xf7) {
        this.isoState = 'active';
        this.isoLast = now;
        this.isoBusyUntil = now + 0.03 + 10 / ISO_BAUD;
        return [{ chunks: [{ data: Uint8Array.of(~ISO_ADDRESS & 0xff), gapS: 0 }], delayS: 0.03, baud: ISO_BAUD }];
      }
      this.isoState = 'off';
      return [];
    }
    if (this.isoState === 'off') {
      if (this.isoFastInit && now <= this.fastArmedUntil + 0.1 && equal(data, KWP_START)) {
        this.isoState = 'active';
        this.isoKwp = true;
        this.isoLast = now;
        this.isoInits += 1;
        return [{ chunks: [{ data: KWP_START_REPLY, gapS: 0 }], delayS: 0.03, baud: ISO_BAUD }];
      }
      return [];
    }
    if (now < this.isoBusyUntil) return []; // sooner than P3min: ignored
    const payload = this.isoPayload(data);
    if (!payload) return [];
    this.isoLast = now;
    this.isoRequests += 1;
    const chunks: Chunk[] = [];
    const delay = 0.03;
    let total = 0;
    for (const message of this.obd(payload)) {
      const framed = this.damage(this.isoFrame(message));
      if (!framed.length) continue;
      const gap = chunks.length ? 0.03 : 0;
      chunks.push({ data: framed, gapS: gap });
      total += gap + (framed.length * 10) / ISO_BAUD;
    }
    if (!chunks.length) return [];
    this.isoBusyUntil = now + delay + total + P3_MIN_S;
    return [{ chunks, delayS: delay, baud: ISO_BAUD }];
  }

  private isoPayload(data: Uint8Array): Uint8Array | null {
    if (data.length < 5 || (sum(data, 0, data.length - 1) & 0xff) !== data[data.length - 1]) return null;
    if (this.isoKwp) {
      const n = data[0]! & 0x3f;
      if ((data[0]! & 0xc0) !== 0xc0 || data[1] !== 0x33 || data.length !== n + 4) return null;
      return data.slice(3, -1);
    }
    if (data[0] !== 0x68 || data[1] !== 0x6a || data[2] !== 0xf1) return null;
    return data.slice(3, -1);
  }

  private isoFrame(payload: Uint8Array): Uint8Array {
    const body = this.isoKwp ? concat([0x80 | payload.length, 0xf1, 0x10], payload) : concat([0x48, 0x6b, 0x10], payload);
    return concat(body, [sum(body) & 0xff]);
  }

  private pid(pid: number, frozen = false): Uint8Array | null {
    const live = this.live;
    const ff = this.freeze;
    let rpm: number, ect: number, mapKpa: number, vss: number, stft: number, ltft: number, load: number, fuel: number;
    if (frozen) {
      if (!ff) return null;
      ({ rpm, ect, mapKpa, vss, stft, ltft, load, fuel } = ff);
    } else {
      rpm = this.rpm; ect = this.ect; mapKpa = live.mapKpa; vss = 0;
      stft = live.stft; ltft = live.ltft;
      load = this.rpm > 100 ? (100 * mapKpa) / 101 : 0;
      fuel = live.closedLoop ? 2 : 1;
    }
    const trim = (x: number) => u8c((x / 100 + 1) * 128);
    const rpm4 = Math.max(0, Math.min(0xffff, Math.trunc(rpm * 4)));
    const table = new Map<number, Uint8Array>([
      [0x03, Uint8Array.of(fuel, 0)],
      [0x04, Uint8Array.of(u8c(load * 2.55))],
      [0x05, Uint8Array.of(u8c(ect + 40))],
      [0x06, Uint8Array.of(trim(stft))],
      [0x07, Uint8Array.of(trim(ltft))],
      [0x0b, Uint8Array.of(u8c(mapKpa))],
      [0x0c, Uint8Array.of(rpm4 >> 8, rpm4 & 0xff)],
      [0x0d, Uint8Array.of(u8c(vss))],
    ]);
    if (frozen) {
      table.set(0x02, ff ? Uint8Array.from(dtcToBytes(ff.dtc)) : Uint8Array.of(0, 0));
    } else {
      const monitors = 0x65; // catalyst, evap, O2 sensor, O2 heater
      table.set(0x01, Uint8Array.of((this.dtcs.length ? 0x80 : 0) | Math.min(0x7f, this.dtcs.length), 0x07, monitors, this.monitorsIncomplete ? monitors : 0));
      table.set(0x0e, Uint8Array.of(u8c((live.ign + 64) * 2)));
      table.set(0x0f, Uint8Array.of(u8c(live.iat + 40)));
      table.set(0x11, Uint8Array.of(u8c(this.tps * 2.55)));
      table.set(0x13, Uint8Array.of(0x03));
      table.set(0x14, Uint8Array.of(u8c(live.o2 / 0.005), trim(stft)));
      table.set(0x15, Uint8Array.of(u8c(0.6 / 0.005), 0xff));
      table.set(0x1c, Uint8Array.of(0x01));
    }
    if (pid === 0) {
      let bitmap = 0;
      for (const p of table.keys()) bitmap += 2 ** (32 - p);
      return Uint8Array.of((bitmap / 2 ** 24) & 0xff, (bitmap / 2 ** 16) & 0xff, (bitmap / 2 ** 8) & 0xff, bitmap & 0xff);
    }
    return table.get(pid) ?? null;
  }

  private obd(payload: Uint8Array): Uint8Array[] {
    const mode = payload[0]!;
    const dtcMessages = (codes: string[]): Uint8Array[] => {
      let raw: number[] = codes.flatMap((c) => dtcToBytes(c));
      raw = raw.length ? [...raw, ...new Array((6 - (raw.length % 6)) % 6).fill(0)] : new Array(6).fill(0);
      const out: Uint8Array[] = [];
      for (let i = 0; i < raw.length; i += 6) out.push(Uint8Array.from([mode + 0x40, ...raw.slice(i, i + 6)]));
      return out;
    };
    if (mode === 0x01 && payload.length === 2) {
      const data = this.pid(payload[1]!);
      return data ? [concat([0x41, payload[1]!], data)] : [];
    }
    if (mode === 0x02 && payload.length === 3) {
      const data = this.pid(payload[1]!, true);
      if (!data || payload[2] !== 0) return [];
      return [concat([0x42, payload[1]!, payload[2]!], data)];
    }
    if (mode === 0x03 && payload.length === 1) return dtcMessages(this.dtcs);
    if (mode === 0x07 && payload.length === 1) return dtcMessages(this.pendingDtcs);
    if (mode === 0x04 && payload.length === 1) {
      this.dtcs = [];
      this.pendingDtcs = [];
      this.freeze = null;
      this.monitorsIncomplete = true;
      return [Uint8Array.of(0x44)];
    }
    if (mode === 0x05 && payload.length === 3) {
      if (payload[1] === 0) return [Uint8Array.of(0x45, 0x00, payload[2]!, 0xf8, 0x00, 0x00, 0x00)]; // TIDs 01-05
      if (payload[1]! >= 1 && payload[1]! <= 5 && payload[2] === 1) return [Uint8Array.of(0x45, payload[1]!, 0x01, 0x5a)];
      return [];
    }
    if (mode === 0x06 && payload.length === 2) {
      if (payload[1] === 0) return [Uint8Array.of(0x46, 0x00, 0x60, 0x00, 0x00, 0x00)]; // TIDs 02, 03
      if (payload[1] === 0x02) return [Uint8Array.of(0x46, 0x02, 0x01, 0x00, 0x30, 0x00, 0x80)];
      if (payload[1] === 0x03) return [Uint8Array.of(0x46, 0x03, 0x81, 0x00, 0x10, 0x00, 0x20)];
      return [];
    }
    return [];
  }
}

// ---------------------------------------------------------------- transport

export class TransportError extends Error {}

/** The fake ECU in the same process, with a model of the wire at true byte
 *  timing on a VirtualClock. Bytes become readable 10 bit times after the one
 *  before them, rounded up to ``usbLatencyS`` ticks like an FTDI. ``read``
 *  resolves as soon as at least one byte is there (pyserial read_some). */
export class LoopbackTransport implements Transport {
  readonly kind = 'sim' as const;
  readonly label = 'sim:';
  usbLatencyS: number;
  baud: number;
  isOpen = true;
  /** Make open() fail (no such device). */
  failOpen = false;
  private rx: [number, number][] = [];
  private wireFree = 0;
  private breakSince: number | null = null;

  constructor(readonly ecu: FakeEcu, readonly clock: VirtualClock, opts: { usbLatencyS?: number; baud?: number } = {}) {
    this.usbLatencyS = opts.usbLatencyS ?? 0.001;
    this.baud = opts.baud ?? ecu.framing.baud;
  }

  private check(): void {
    if (!this.isOpen) throw new TransportError('port is not open');
  }

  private deliver(t: number): number {
    const tick = this.usbLatencyS;
    if (tick <= 0) return t;
    return (Math.trunc(t / tick - 1e-9) + 1) * tick;
  }

  private queue(start: number, data: Uint8Array, byteS: number): number {
    let t = start;
    for (const b of data) {
      t += byteS;
      this.rx.push([this.deliver(t), b]);
    }
    return t;
  }

  private sortRx(): void {
    this.rx.sort((a, b) => a[0] - b[0]);
  }

  async open(opts: { baud: number }): Promise<void> {
    if (this.failOpen) throw new TransportError('no such device');
    this.rx = [];
    this.wireFree = 0;
    this.breakSince = null;
    this.isOpen = true;
    this.baud = opts.baud;
  }

  async close(): Promise<void> {
    this.isOpen = false;
  }

  async setBaud(baud: number): Promise<void> {
    this.check();
    this.baud = baud;
  }

  async write(data: Uint8Array): Promise<void> {
    this.check();
    const now = this.clock.now();
    const byteS = 10 / this.baud;
    const start = Math.max(now, this.wireFree);
    const end = start + byteS * data.length;
    this.wireFree = end;
    const ecu = this.ecu;
    if (ecu.cableDead) return;
    if (ecu.echo) this.queue(start, data, byteS);
    for (const reply of ecu.onBytes(Uint8Array.from(data), this.baud, end)) {
      const rb = 10 / reply.baud;
      let t = end + reply.delayS;
      for (const chunk of reply.chunks) t = this.queue(t + chunk.gapS, chunk.data, rb);
    }
    this.sortRx();
  }

  async read(max: number, timeoutMs: number): Promise<Uint8Array> {
    this.check();
    const clock = this.clock;
    let now = clock.now();
    const timeout = Math.max(0, timeoutMs / 1000);
    const rx = this.rx;
    if (!rx.length || rx[0]![0] > now) {
      const wake = rx.length ? rx[0]![0] : Infinity;
      if (wake > now + timeout) {
        await clock.sleep(timeout);
        return new Uint8Array(0);
      }
      await clock.sleepUntil(wake);
      now = Math.max(clock.now(), wake);
    }
    let n = 0;
    while (n < rx.length && n < max && rx[n]![0] <= now) n++;
    const out = Uint8Array.from(rx.slice(0, n), ([, b]) => b);
    rx.splice(0, n);
    return out;
  }

  async drain(): Promise<void> {
    this.check();
    const now = this.clock.now();
    this.rx = this.rx.filter(([t]) => t > now);
  }

  async setBreak(on: boolean): Promise<void> {
    this.check();
    const now = this.clock.now();
    if (on && this.breakSince === null) {
      this.breakSince = now;
    } else if (!on && this.breakSince !== null) {
      const start = this.breakSince;
      this.breakSince = null;
      if (this.ecu.cableDead) return;
      for (const reply of this.ecu.onBreak(start, now)) {
        const rb = 10 / reply.baud;
        let t = now + reply.delayS;
        for (const chunk of reply.chunks) t = this.queue(t + chunk.gapS, chunk.data, rb);
      }
      this.sortRx();
    }
  }
}
