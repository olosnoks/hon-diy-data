/**
 * Simulated Honda ECU (port of hondalog/sim.py, minus the pty server).
 *
 * EcuModel is the engine + RAM + protocol logic, no I/O. SimTransport
 * (transport.ts) puts it behind the Transport interface with true byte timing.
 *
 * What it models:
 * - Honda RAM reads (20 05 addr len cksum) as the stock P2P answers them
 *   (2000 Civic EX, 2026-11-25): at 10400 baud only, and only after the wake
 *   frame 68 6A F5 AF BF B3 B2 C1 DB B3 E9. Stays awake until it loses power.
 *   `new EcuModel({ framing: P2T_FRAMING })` is kerpz's P2T: 9600 baud, no wake.
 * - ISO 9141-2 at 10400 baud: 5-baud init at 0x33 with W1-W5 and P2/P3
 *   timing, optional fast init, Mode 01/02/03/04/07. While an ISO session is
 *   alive (until 5 s of silence) Honda requests go unanswered.
 * - Line faults (Faults): dropped bytes, bad checksums, junk, slow and
 *   missing replies, damaged echo, garbled requests, a deaf ECU, cable
 *   unplug. Seeded with Python's own generator, so a run repeats and matches
 *   the Python simulator draw for draw.
 */

import {
  DEFAULT_FRAMING, buildReply, concat, indexOf, verify, withOverrides, initBytes,
  parseHex, type FullFraming,
} from '../link/protocol';
import { PyRandom } from './pyrandom';

const ECT_POLY = [155.04149, -3.0414878, 0.03952185, -0.00029383913, 0.0000010792568, -0.0000000015618437];
const TEMP_TABLE: number[] = Array.from({ length: 256 }, (_, x) => {
  let s = 0;
  ECT_POLY.forEach((c, i) => { s += c * x ** i; });
  return s;
});

export const ISO_BAUD = 10400;
export const ISO_ADDRESS = 0x33;
export const BIT_5BAUD_S = 0.2;
export const P3_MIN_S = 0.055;    // ECU ignores a request sooner than this after its reply
export const P3_MAX_S = 5.0;      // ISO session dies after this much silence
export const W5_S = 0.3;          // line must be idle this long before a 5-baud init
export const KWP_START = parseHex('c133f18166');
export const KWP_START_REPLY = parseHex('83f110c1e98fbd');
/** The stock P2P: Honda reads at 10400 baud, after the wake frame. */
export const P2P_FRAMING: Readonly<FullFraming> = Object.freeze(withOverrides(DEFAULT_FRAMING, { baud: ISO_BAUD }));
/** kerpz's P2T test ECU: Honda reads at 9600 baud. */
export const P2T_FRAMING: Readonly<FullFraming> = DEFAULT_FRAMING;

/** Python's round(): halves go to the even neighbour. */
export function pyRound(x: number): number {
  const f = Math.floor(x);
  const d = x - f;
  if (d < 0.5) return f;
  if (d > 0.5) return f + 1;
  return f % 2 === 0 ? f : f + 1;
}

const tempCache = new Map<number, number>();
/** Sensor byte whose decoded temperature is closest (1 degree steps). */
export function tempRaw(degC: number): number {
  const whole = pyRound(degC);
  let hit = tempCache.get(whole);
  if (hit === undefined) {
    hit = 0;
    let best = Infinity;
    for (let x = 0; x < 256; x++) {
      const d = Math.abs(TEMP_TABLE[x]! - whole);
      if (d < best) { best = d; hit = x; }
    }
    tempCache.set(whole, hit);
  }
  return hit;
}

function u8(v: number): number {
  return Math.max(0, Math.min(255, Math.trunc(pyRound(v))));
}

function be16(v: number): [number, number] {
  return [(v >> 8) & 0xff, v & 0xff];
}

// ---- wire-level replies and faults ------------------------------------------

export interface Chunk { data: Uint8Array; gapS: number }   // gapS: silence before this chunk
/** Bytes the ECU puts on the wire, delayS after the event that caused them. */
export interface Reply { chunks: Chunk[]; delayS: number; baud: number }

const chunk = (data: ArrayLike<number>, gapS = 0): Chunk => ({ data: Uint8Array.from(data), gapS });

export type FaultName = 'dropByte' | 'badChecksum' | 'junk' | 'noReply' | 'slowReply' | 'dropEcho' | 'garble' | 'deaf';

/** Line fault probabilities, each per reply (or per echoed request). */
export class Faults {
  dropByte = 0;      // one reply byte goes missing
  badChecksum = 0;   // one bit of the reply flips
  junk = 0;          // 1-4 random bytes in front of the reply
  noReply = 0;       // the reply never comes
  slowReply = 0;     // the reply comes slowReplyS late
  slowReplyS = 0.15;
  dropEcho = 0;      // one byte of our own echo goes missing
  garble = 0;        // our request is damaged on the wire: bad echo, no reply
  deaf = 0;          // the ECU stops listening for deafS (per request)
  deafS = 0.3;
  seed = 1;

  constructor(init: Partial<Faults> = {}) {
    Object.assign(this, init);
  }

  private static readonly ALIASES: Record<string, string> = {
    drop: 'drop_byte', cksum: 'bad_checksum', slow: 'slow_reply', silent: 'no_reply', echo: 'drop_echo',
  };
  private static readonly KEYS: Record<string, keyof Faults> = {
    drop_byte: 'dropByte', bad_checksum: 'badChecksum', junk: 'junk', no_reply: 'noReply',
    slow_reply: 'slowReply', slow_reply_s: 'slowReplyS', drop_echo: 'dropEcho', garble: 'garble',
    deaf: 'deaf', deaf_s: 'deafS', seed: 'seed',
  };

  /** 'drop=0.01,cksum=0.01,junk=0.02,slow=0.005,seed=7' -> Faults. */
  static parse(text: string): Faults {
    const values: Record<string, number> = {};
    for (const item of text.split(',').map((s) => s.trim()).filter(Boolean)) {
      const eq = item.indexOf('=');
      let key = (eq < 0 ? item : item.slice(0, eq)).trim();
      key = Faults.ALIASES[key] ?? key;
      const field = Faults.KEYS[key];
      if (eq < 0 || !field) {
        throw new Error(`bad fault setting '${item}'; known: ${Object.keys(Faults.KEYS).sort().join(', ')}`);
      }
      const raw = item.slice(eq + 1).trim();
      const v = field === 'seed' ? parseInt(raw, 10) : parseFloat(raw);
      if (!Number.isFinite(v)) throw new Error(`bad fault setting '${item}'`);
      values[field] = v;
    }
    return new Faults(values as Partial<Faults>);
  }

  /** 'flaky', 'p2p', or a spec for parse. */
  static named(text: string, seed = 1): Faults {
    if (text === 'flaky') return Faults.flaky(seed);
    if (text === 'p2p') return Faults.p2p(seed);
    return Faults.parse(text);
  }

  /** The stock P2P on the KKL 409.1 cable at idle (drive 2026-11-26 20:45):
   *  1.7 % of requests come back with a damaged echo and no reply, 0.4 % are
   *  ignored, 0.15 % start ~0.3 s in which the ECU ignores everything. */
  static p2p(seed = 1): Faults {
    return new Faults({ garble: 0.017, noReply: 0.004, deaf: 0.0015, deafS: 0.3, seed });
  }

  /** A poor cable: about 3% of exchanges go wrong one way or another. */
  static flaky(seed = 1): Faults {
    return new Faults({ dropByte: 0.008, badChecksum: 0.008, junk: 0.006, noReply: 0.004,
      slowReply: 0.002, dropEcho: 0.002, seed });
  }

  any(): boolean {
    return Boolean(this.dropByte || this.badChecksum || this.junk || this.noReply
      || this.slowReply || this.dropEcho || this.garble || this.deaf);
  }
}

export interface FreezeFrame {
  dtc: string; rpm: number; ect: number; mapKpa: number; vss: number;
  load: number; stft: number; ltft: number; fuelStatus: number;
}

function freezeFrame(dtc = 'P0135'): FreezeFrame {
  return { dtc, rpm: 812, ect: 84, mapKpa: 34, vss: 0, load: 22, stft: 1.6, ltft: 2.3, fuelStatus: 2 };
}

/** 'P0135' -> [0x01, 0x35]. */
export function dtcToBytes(code: string): Uint8Array {
  const system = 'PCBU'.indexOf(code[0]!.toUpperCase());
  const h = (i: number) => parseInt(code[i]!, 16);
  return Uint8Array.from([((system << 6) | (h(1) << 4) | h(2)) & 0xff, ((h(3) << 4) | h(4)) & 0xff]);
}

export interface EcuModelOptions {
  framing?: FullFraming;
  warmupS?: number;
  revEveryS?: number;
  stallAfterS?: number | null;
  stallMode?: 'rpm' | 'power';
  restartAfterS?: number | null;
  echo?: boolean;
  seed?: number;
  clock?: () => number;
  faults?: Faults | null;
  turnaroundS?: number;
  maxReadLen?: number | null;
  /** false: answers ISO 9141-2 only */
  hondaProtocol?: boolean;
  iso?: boolean;
  isoFastInit?: boolean;
  dtcs?: string[];
  pendingDtcs?: string[];
  /** default: a 10400 baud ECU wants the wake frame first, like the P2P */
  needWake?: boolean;
}

const nowSeconds = () => performance.now() / 1000;

/** A warm-ish D16 that idles, revs now and then, and can be made to die. */
export class EcuModel {
  framing: FullFraming;
  needWake: boolean;
  woken = false;
  wakes = 0;
  hondaIgnored = 0;           // Honda reads dropped: not woken yet
  faults: Faults;
  faultRng: PyRandom;
  faultCounts: Record<string, number> = {};
  turnaroundS: number;
  maxReadLen: number | null;
  hondaProtocol: boolean;
  iso: boolean;
  isoFastInit: boolean;
  cableDead = false;          // true: no echo, nothing (no 12 V at the plug)
  dtcs: string[];
  pendingDtcs: string[];
  freeze: FreezeFrame | null;
  monitorsIncomplete = false; // true after a Mode 04 clear
  live = { mapKpa: 101, o2: 0.05, stft: 0, ltft: 2, ign: 0, iat: 28, closedLoop: false };
  isoInits = 0;
  isoRequests = 0;
  initToleranceS = 0.015;     // how sloppy the 5-baud bit edges may be
  warmupS: number;
  revEveryS: number;
  stallAfterS: number | null;
  stallMode: 'rpm' | 'power';
  restartAfterS: number | null;
  echo: boolean;
  clock: () => number;
  rng: PyRandom;
  ram: Uint8Array;
  t0: number;
  last: number;
  running = true;
  powered = true;             // false = ECU lost power: no replies at all
  rpm = 1500;
  ect = 20;
  tps = 0;
  acOn = false;
  brakeOn = false;
  revStarted: number | null = null;
  nextRev: number;
  stoppedAt: number | null = null;
  requests = 0;

  private unpluggedUntil = 0;
  private unplugWindow: [number, number] = [0, 0];
  private rxbuf: number[] = [];
  private garbled = false;
  /** exposed for tests (Python: model._deaf_until) */
  deafUntil = -Infinity;
  private lastRx = -Infinity;
  private lastActivity = -Infinity;
  private lows: [number, number][] = [];
  private isoState: 'off' | 'await_kb' | 'active' = 'off';
  private isoKwp = false;
  private isoLast = 0;
  private isoBusyUntil = 0;
  private fastArmedUntil = -Infinity;
  private readonly wakeSeq: Uint8Array;

  constructor(o: EcuModelOptions = {}) {
    this.framing = o.framing ?? P2P_FRAMING;
    this.needWake = o.needWake ?? this.framing.baud === ISO_BAUD;
    this.faults = o.faults ?? new Faults();
    this.faultRng = new PyRandom(this.faults.seed);
    this.turnaroundS = o.turnaroundS ?? 0.002;
    this.maxReadLen = o.maxReadLen ?? null;
    this.hondaProtocol = o.hondaProtocol ?? true;
    this.iso = o.iso ?? true;
    this.isoFastInit = o.isoFastInit ?? false;
    this.dtcs = [...(o.dtcs ?? [])];
    this.pendingDtcs = [...(o.pendingDtcs ?? [])];
    this.freeze = this.dtcs.length ? freezeFrame(this.dtcs[0]) : null;
    this.warmupS = o.warmupS ?? 120;
    this.revEveryS = o.revEveryS ?? 20;
    this.stallAfterS = o.stallAfterS ?? null;
    this.stallMode = o.stallMode ?? 'rpm';
    this.restartAfterS = o.restartAfterS === undefined ? 15 : o.restartAfterS;
    this.echo = o.echo ?? true;
    this.clock = o.clock ?? nowSeconds;
    this.rng = new PyRandom(o.seed ?? 1);
    this.ram = new Uint8Array(256);
    for (let i = 0; i < 256; i++) this.ram[i] = this.rng.randrange(256);
    this.ram.set(new TextEncoder().encode('SIM-P2P\x00\x00\x01'), 0x76);   // fake ECU id
    this.t0 = this.last = this.clock();
    this.nextRev = this.revEveryS;
    this.wakeSeq = initBytes(this.framing);
    this.step();
  }

  // -- controls --

  /** Engine dies, ECU stays alive (RPM reads 0). */
  stall(): void {
    this.running = false;
    this.stoppedAt = this.clock() - this.t0;
  }

  /** ECU loses power: engine dies and the K-line goes silent. */
  powerCut(): void {
    this.stall();
    this.powered = false;
    this.woken = false;   // the wake frame has to be sent again
  }

  restart(): void {
    this.running = this.powered = true;
    this.stoppedAt = null;
    this.rpm = Math.max(this.rpm, 300);
  }

  rev(): void {
    if (this.running && this.revStarted === null) this.revStarted = this.clock() - this.t0;
  }

  /** Press the A/C button (toggle, or set with `on`). */
  ac(on?: boolean): void {
    this.acOn = on === undefined ? !this.acOn : on;
  }

  // -- engine --

  step(): void {
    const now = this.clock();
    const t = now - this.t0;
    const dt = Math.min(1.0, Math.max(0.0, now - this.last));
    this.last = now;

    if (this.stallAfterS !== null && this.running && t >= this.stallAfterS) {
      this.stallAfterS = null;
      if (this.stallMode === 'power') this.powerCut();
      else this.stall();
    }
    if (!this.running && this.restartAfterS !== null && this.stoppedAt !== null
      && t - this.stoppedAt >= this.restartAfterS) {
      this.restart();
    }
    if (this.revEveryS && this.running && t >= this.nextRev) {
      this.nextRev = t + this.revEveryS;
      this.rev();
    }

    this.ect = 20.0 + 68.0 * (1 - Math.exp((-3.0 * t) / Math.max(1.0, this.warmupS)));
    const cold = Math.max(0.0, (70.0 - this.ect) / 50.0);
    const clutch = this.acOn && this.running;
    const idle = 750.0 + 750.0 * cold + (clutch ? 300.0 : 0.0);
    this.tps = 0.0;
    let target = idle;
    if (this.revStarted !== null) {
      const phase = (t - this.revStarted) / 3.0;
      if (phase >= 1.0 || !this.running) {
        this.revStarted = null;
      } else {
        this.tps = 55.0 * Math.sin(Math.PI * phase) ** 2;
        target = idle + 3000.0 * Math.sin(Math.PI * phase);
      }
    }
    if (!this.running) target = 0.0;
    const rate = this.running ? 6.0 : 9.0;
    this.rpm += (target - this.rpm) * Math.min(1.0, rate * dt);
    if (!this.running && this.rpm < 60.0) this.rpm = 0.0;
    const rpm = this.rpm + (this.rpm > 100 ? this.rng.uniform(-12, 12) : 0.0);

    const turning = this.rpm > 100;
    const cranking = this.running && turning && this.rpm < 500;
    const closedLoop = turning && !cranking && this.ect > 60.0 && this.tps < 10.0;
    const mapKpa = !turning ? 101.0 : 30.0 + 0.9 * this.tps + 6.0 * cold;
    const o2 = closedLoop ? 0.45 + 0.4 * Math.sin(t * 5.0) : turning ? 0.8 : 0.05;
    const batt = turning ? 14.2 + this.rng.uniform(-0.05, 0.05) : 12.3;
    const stft = closedLoop ? 4.0 * Math.sin(t * 0.7) : 0.0;
    const inj = !turning ? 0.0 : 2.4 + 0.05 * this.tps + 1.2 * cold;
    const ign = !turning ? 0.0 : 16.0 + Math.min(20.0, (this.rpm - 750) / 150.0);
    const iacv = !turning ? 0.0 : 30.0 + 25.0 * cold;
    const live = this.live;
    live.mapKpa = mapKpa; live.o2 = o2; live.stft = stft; live.ign = ign;
    live.closedLoop = closedLoop;

    const r = this.ram;
    const rpmRaw = Math.max(0, Math.min(0xffff, Math.trunc(rpm * 4)));
    [r[0x00], r[0x01]] = be16(rpmRaw);
    r[0x02] = 0;                                        // VSS: parked
    // Flag bytes as read on the 2000 Civic EX P2P.
    const stoppedS = this.stoppedAt === null ? null : t - this.stoppedAt;
    const pump = this.running || (stoppedS !== null && stoppedS < 2.0);   // 2 s prime
    const mil = !this.running || this.dtcs.length > 0;                    // bulb check at key on
    r[0x08] = 0x80 | 0x04 | (this.acOn ? 0x02 : 0) | (this.brakeOn ? 0x08 : 0) | (cranking ? 0x01 : 0);
    r[0x0a] = 0x10 | (turning ? 0x80 : 0) | (this.rpm > 5500 ? 0x04 : 0);
    r[0x0b] = (turning ? 0x40 : 0) | (mil ? 0x20 : 0) | (clutch ? 0x02 : 0) | (pump ? 0x01 : 0);
    r[0x0c] = this.ect > 98.0 ? 0x02 : 0x00;
    r[0x0f] = closedLoop ? 0x01 : 0x00;
    r[0x10] = tempRaw(this.ect);
    r[0x11] = tempRaw(28.0);
    r[0x12] = u8((mapKpa + 5) / 0.716);
    r[0x13] = u8((101.0 + 5) / 0.716);
    r[0x14] = u8(this.tps * 2.55);                      // = the OBD2 PID 11 byte
    r[0x15] = u8(o2 * 51.3);
    r[0x17] = u8(batt * 10.45);
    r[0x18] = u8(40 * 2.55);
    r[0x19] = u8((77.06 - 22.0) * 2.5371);
    r[0x20] = u8((stft / 100 + 1) * 128);
    r[0x21] = r[0x22] = u8((2.0 / 100 + 1) * 128);
    [r[0x24], r[0x25]] = be16(Math.trunc(inj * 250));
    r[0x26] = r[0x27] = u8(ign * 4 + 24);
    r[0x28] = u8(iacv * 2.55);
    r[0x3c] = 0;
    // Some life elsewhere so the memory scanner has something to find.
    r[0x80] = (r[0x80]! + 1) & 0xff;
    r[0x90] = r[0x00]!;
    r[0x91] = r[0x01]!;
    r[0xa0] = u8(this.tps * 2.55);
  }

  // -- cable --

  /** Pull the USB cable. forS: plug it back in after that long. */
  unplug(forS?: number | null): void {
    this.unpluggedUntil = forS == null ? Infinity : this.clock() + forS;
  }

  replug(): void {
    this.unpluggedUntil = 0;
    this.unplugWindow = [0, 0];
  }

  /** Pull the cable at clock time `at` for `forS` seconds. */
  scheduleUnplug(at: number, forS: number): void {
    this.unplugWindow = [at, at + forS];
  }

  unplugged(now?: number): boolean {
    const t = now ?? this.clock();
    return t < this.unpluggedUntil || (this.unplugWindow[0] <= t && t < this.unplugWindow[1]);
  }

  // -- faults --

  private faultP(name: FaultName): number {
    return this.faults[name];
  }

  /** Roll the dice for one fault (no draw at all when its probability is 0). */
  hit(name: FaultName): boolean {
    const p = this.faultP(name);
    if (p <= 0 || this.faultRng.random() >= p) return false;
    const key = PY_FAULT[name];
    this.faultCounts[key] = (this.faultCounts[key] ?? 0) + 1;
    return true;
  }

  /** What the tester hears of its own transmission. */
  echoOf(data: Uint8Array): Uint8Array {
    this.garbled = false;
    if (data.length === 5 && this.hit('garble')) {
      // a bit flipped on the wire: the tester hears it, the ECU ignores the frame
      const rng = this.faultRng;
      const i = rng.randrange(data.length);
      this.garbled = true;
      const out = Uint8Array.from(data);
      out[i] = data[i]! ^ (1 << rng.randrange(8));
      return out;
    }
    if (data.length > 1 && this.hit('dropEcho')) {
      const i = this.faultRng.randrange(data.length);
      return concat(data.subarray(0, i), data.subarray(i + 1));
    }
    return data;
  }

  /** Apply the fault dice to one reply. Returns [bytes, extra delay]. */
  damage(frame: Uint8Array): [Uint8Array, number] {
    if (!this.faults.any() || !frame.length) return [frame, 0];
    const rng = this.faultRng;
    const out = Array.from(frame);
    let extra = 0;
    if (this.hit('noReply')) return [new Uint8Array(0), 0];
    if (this.hit('dropByte')) out.splice(rng.randrange(out.length), 1);
    if (this.hit('badChecksum')) {
      const i = rng.randrange(out.length);
      out[i] = out[i]! ^ (1 << rng.randrange(8));
    }
    if (this.hit('junk')) {
      const n = rng.randint(1, 4);
      const junk: number[] = [];
      for (let k = 0; k < n; k++) junk.push(rng.randrange(256));
      out.unshift(...junk);
    }
    if (this.hit('slowReply')) extra = this.faults.slowReplyS;
    return [Uint8Array.from(out), extra];
  }

  // -- Honda protocol --

  /** Reply to one checksum-valid request frame (empty = stay silent). */
  handle(frame: Uint8Array): Uint8Array {
    this.requests += 1;
    this.step();
    if (!this.powered || !this.hondaProtocol || frame[0] !== this.framing.cmdRead || frame.length !== 5) {
      return new Uint8Array(0);
    }
    if (this.needWake && !this.woken) {
      this.hondaIgnored += 1;
      return new Uint8Array(0);
    }
    const addr = frame[2]!, length = frame[3]!;
    if (length === 0 || (this.maxReadLen !== null && length > this.maxReadLen)) return new Uint8Array(0);
    const data = new Uint8Array(length);
    for (let i = 0; i < length; i++) data[i] = this.ram[(addr + i) & 0xff]!;
    return buildReply(data, this.framing);
  }

  private frames(buf: number[]): Uint8Array[] {
    const out: Uint8Array[] = [];
    while (buf.length >= 2) {
      const size = buf[1]!;
      if (!(size >= 3 && size <= 16)) {
        buf.shift();
        continue;
      }
      if (buf.length < size) break;
      const frame = Uint8Array.from(buf.slice(0, size));
      if (verify(frame)) {
        const reply = this.handle(frame);
        if (reply.length) out.push(reply);
        buf.splice(0, size);
      } else {
        buf.shift();
      }
    }
    return out;
  }

  /** Consume request bytes from `buf` (in place); return reply bytes. No
   *  faults and no timing here; see onBytes for the wire-level view. */
  feed(buf: number[]): Uint8Array {
    this.wakeIn(buf);
    return concat(...this.frames(buf));
  }

  /** Take the wake frame out of `buf`; a powered ECU is awake after it. */
  private wakeIn(buf: number[]): void {
    const seq = this.wakeSeq;
    if (!seq.length) return;
    let at = indexOf(buf, seq);
    while (at >= 0) {
      buf.splice(at, seq.length);
      this.step();   // power state is due now (key on)
      if (this.powered) {
        this.woken = true;
        this.wakes += 1;
      }
      at = indexOf(buf, seq);
    }
  }

  // -- the wire --

  private isoAlive(now: number): boolean {
    if (this.isoState !== 'off' && now - this.isoLast > P3_MAX_S) this.isoState = 'off';
    return this.isoState !== 'off';
  }

  /** Bytes from the tester, complete on the wire at `now`. */
  onBytes(data: Uint8Array, baud: number, now: number): Reply[] {
    const gap = now - this.lastRx;
    this.lastRx = this.lastActivity = now;
    const isoOk = baud === ISO_BAUD && this.powered && this.iso;
    const fastStart = isoOk && this.isoFastInit && eq(data, KWP_START) && now <= this.fastArmedUntil + 0.1;
    if (this.isoAlive(now) || fastStart || baud !== this.framing.baud) {
      this.rxbuf.length = 0;
      if (!isoOk) return [];   // wrong speed: framing errors, ignored
      return this.isoBytes(data, now);
    }
    if (gap > 0.05) this.rxbuf.length = 0;   // stale partial request
    if (this.garbled) {
      this.garbled = false;
      return [];   // the ECU got a damaged frame
    }
    if (this.faults.deaf && data.length === 5 && data[0] === this.framing.cmdRead) {
      if (now < this.deafUntil) return [];
      if (this.hit('deaf')) {
        this.deafUntil = now + this.faults.deafS;
        return [];
      }
    }
    for (const b of data) this.rxbuf.push(b);
    this.wakeIn(this.rxbuf);
    const chunks: Chunk[] = [];
    let delay = this.turnaroundS;
    for (const frame of this.frames(this.rxbuf)) {
      const [damaged, extra] = this.damage(frame);
      if (!damaged.length) continue;
      if (chunks.length) {
        chunks.push(chunk(damaged, this.turnaroundS + extra));
      } else {
        delay += extra;
        chunks.push(chunk(damaged));
      }
    }
    return chunks.length ? [{ chunks, delayS: delay, baud: this.framing.baud }] : [];
  }

  /** The tester held the line low from `start` to `end`. */
  onBreak(start: number, end: number): Reply[] {
    const idleBefore = start - this.lastActivity;
    this.lastActivity = end;
    if (!this.powered || !this.iso) return [];
    if (this.lows.length && start - this.lows[0]![0] > 10 * BIT_5BAUD_S + 0.1) this.lows.length = 0;
    if (!this.lows.length) {
      if (end - start >= 0.020 && end - start <= 0.030) {   // fast init wake-up pattern
        this.fastArmedUntil = end + 0.1;
        return [];
      }
      if (idleBefore < W5_S) return [];   // bus was not idle: ignored
    }
    this.lows.push([start, end]);
    const t0 = this.lows[0]![0];
    const tol = this.initToleranceS;
    for (const [s, e] of this.lows) {
      for (const edge of [s, e]) {
        const k = (edge - t0) / BIT_5BAUD_S;
        if (Math.abs(k - pyRound(k)) * BIT_5BAUD_S > tol) {
          this.lows.length = 0;
          return [];
        }
      }
    }
    const bits: number[] = [];
    for (let k = 0; k < 10; k++) {
      const centre = t0 + (k + 0.5) * BIT_5BAUD_S;
      bits.push(this.lows.some(([s, e]) => s <= centre && centre < e) ? 0 : 1);
    }
    const want = [0, ...Array.from({ length: 8 }, (_, i) => (ISO_ADDRESS >> i) & 1), 1];
    if (!eq(bits, want)) return [];   // not (yet) address 0x33
    this.lows.length = 0;
    this.isoInits += 1;
    this.isoState = 'await_kb';
    this.isoKwp = false;
    this.isoLast = t0 + 10 * BIT_5BAUD_S;
    const w1 = 0.1;
    this.isoBusyUntil = this.isoLast + w1;
    return [{ chunks: [chunk([0x55]), chunk([0x08], 0.01), chunk([0x08], 0.01)],
      delayS: this.isoLast + w1 - end, baud: ISO_BAUD }];
  }

  // -- ISO 9141-2 / KWP2000 --

  private isoBytes(data: Uint8Array, now: number): Reply[] {
    if (this.isoState === 'await_kb') {
      if (data.length === 1 && data[0] === 0xf7) {   // inverted key byte 2
        this.isoState = 'active';
        this.isoLast = now;
        this.isoBusyUntil = now + 0.03 + 10.0 / ISO_BAUD;
        return [{ chunks: [chunk([~ISO_ADDRESS & 0xff])], delayS: 0.03, baud: ISO_BAUD }];
      }
      this.isoState = 'off';
      return [];
    }
    if (this.isoState === 'off') {
      if (this.isoFastInit && now <= this.fastArmedUntil + 0.1 && eq(data, KWP_START)) {
        this.isoState = 'active';
        this.isoKwp = true;
        this.isoLast = now;
        this.isoInits += 1;
        return [{ chunks: [chunk(KWP_START_REPLY)], delayS: 0.03, baud: ISO_BAUD }];
      }
      return [];
    }
    if (now < this.isoBusyUntil) return [];   // sooner than P3min: ignored
    const payload = this.isoPayload(data);
    if (payload === null) return [];
    this.isoLast = now;
    this.isoRequests += 1;
    this.step();
    const chunks: Chunk[] = [];
    let delay = 0.03, total = 0;
    for (const message of this.obd(payload)) {
      const [framed, extra] = this.damage(this.isoFrame(message));
      if (!framed.length) continue;
      const gap = chunks.length ? 0.03 + extra : 0.0;
      if (!chunks.length) delay += extra;
      chunks.push(chunk(framed, gap));
      total += gap + (framed.length * 10.0) / ISO_BAUD;
    }
    if (!chunks.length) return [];
    this.isoBusyUntil = now + delay + total + P3_MIN_S;
    return [{ chunks, delayS: delay, baud: ISO_BAUD }];
  }

  private isoPayload(data: Uint8Array): Uint8Array | null {
    if (data.length < 5) return null;
    let s = 0;
    for (let i = 0; i < data.length - 1; i++) s += data[i]!;
    if ((s & 0xff) !== data[data.length - 1]) return null;
    if (this.isoKwp) {
      const n = data[0]! & 0x3f;
      if ((data[0]! & 0xc0) !== 0xc0 || data[1] !== 0x33 || data.length !== n + 4) return null;
      return data.slice(3, -1);
    }
    if (!(data[0] === 0x68 && data[1] === 0x6a && data[2] === 0xf1)) return null;
    return data.slice(3, -1);
  }

  private isoFrame(payload: Uint8Array): Uint8Array {
    const body = this.isoKwp ? concat([0x80 | payload.length, 0xf1, 0x10], payload) : concat([0x48, 0x6b, 0x10], payload);
    let s = 0;
    for (const b of body) s += b;
    return concat(body, [s & 0xff]);
  }

  /** Mode 01 (or freeze frame) data bytes for one PID. */
  private pid(pid: number, frozen = false): Uint8Array | null {
    const live = this.live, ff = this.freeze;
    let rpm: number, ect: number, mapKpa: number, vss: number, stft: number, ltft: number, load: number, fuel: number;
    if (frozen) {
      if (ff === null) return null;
      ({ rpm, ect, mapKpa, vss, stft, ltft, load } = ff);
      fuel = ff.fuelStatus;
    } else {
      rpm = this.rpm; ect = this.ect; mapKpa = live.mapKpa; vss = 0;
      stft = live.stft; ltft = live.ltft;
      load = this.rpm > 100 ? (100.0 * mapKpa) / 101.0 : 0.0;
      fuel = live.closedLoop ? 2 : 1;
    }
    const trim = (x: number) => u8((x / 100 + 1) * 128);
    const table = new Map<number, number[]>([
      [0x03, [fuel, 0]],
      [0x04, [u8(load * 2.55)]],
      [0x05, [u8(ect + 40)]],
      [0x06, [trim(stft)]],
      [0x07, [trim(ltft)]],
      [0x0b, [u8(mapKpa)]],
      [0x0c, be16(Math.max(0, Math.min(0xffff, Math.trunc(rpm * 4))))],
      [0x0d, [u8(vss)]],
    ]);
    if (frozen) {
      table.set(0x02, ff !== null ? Array.from(dtcToBytes(ff.dtc)) : [0, 0]);
    } else {
      const monitors = 0x65;   // catalyst, evap, O2 sensor, O2 heater
      table.set(0x01, [(this.dtcs.length ? 0x80 : 0) | Math.min(0x7f, this.dtcs.length), 0x07,
        monitors, this.monitorsIncomplete ? monitors : 0x00]);
      table.set(0x0e, [u8((live.ign + 64) * 2)]);
      table.set(0x0f, [u8(live.iat + 40)]);
      table.set(0x11, [u8(this.tps * 2.55)]);
      table.set(0x13, [0x03]);
      table.set(0x14, [u8(live.o2 / 0.005), trim(stft)]);
      table.set(0x15, [u8(0.6 / 0.005), 0xff]);
      table.set(0x1c, [0x01]);
    }
    if (pid === 0x00) {
      let bitmap = 0;
      for (const p of table.keys()) bitmap += 2 ** (32 - p);
      return Uint8Array.from([(bitmap / 2 ** 24) & 0xff, (bitmap / 2 ** 16) & 0xff, (bitmap / 2 ** 8) & 0xff, bitmap & 0xff]);
    }
    const v = table.get(pid);
    return v ? Uint8Array.from(v) : null;
  }

  /** OBD2 service handler: request payload -> response payloads. */
  private obd(payload: Uint8Array): Uint8Array[] {
    const mode = payload[0]!;
    const dtcMessages = (codes: string[]): Uint8Array[] => {
      let raw = concat(...codes.map(dtcToBytes));
      raw = raw.length ? concat(raw, new Uint8Array((6 - (raw.length % 6)) % 6)) : new Uint8Array(6);
      const out: Uint8Array[] = [];
      for (let i = 0; i < raw.length; i += 6) out.push(concat([mode + 0x40], raw.subarray(i, i + 6)));
      return out;
    };
    if (mode === 0x01 && payload.length === 2) {
      const data = this.pid(payload[1]!);
      return data !== null ? [concat([0x41, payload[1]!], data)] : [];
    }
    if (mode === 0x02 && payload.length === 3) {
      const data = this.pid(payload[1]!, true);
      if (data === null || payload[2] !== 0) return [];
      return [concat([0x42, payload[1]!, payload[2]!], data)];
    }
    if (mode === 0x03 && payload.length === 1) return dtcMessages(this.dtcs);
    if (mode === 0x07 && payload.length === 1) return dtcMessages(this.pendingDtcs);
    if (mode === 0x04 && payload.length === 1) {
      this.dtcs.length = 0;
      this.pendingDtcs.length = 0;
      this.freeze = null;
      this.monitorsIncomplete = true;
      return [Uint8Array.of(0x44)];
    }
    return [];
  }
}

/** Fault names as the Python simulator counts them (fault_counts keys). */
const PY_FAULT: Record<FaultName, string> = {
  dropByte: 'drop_byte', badChecksum: 'bad_checksum', junk: 'junk', noReply: 'no_reply',
  slowReply: 'slow_reply', dropEcho: 'drop_echo', garble: 'garble', deaf: 'deaf',
};

function eq(a: ArrayLike<number>, b: ArrayLike<number>): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}
