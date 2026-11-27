/**
 * The memory scanner behind F7 Bytes: sweeps 0x00-0xFF through the link in
 * 16-byte reads, keeps the current value, change heat, min/max and the last
 * 120 sweeps of every byte, and runs the hex console with the read-only
 * guard. Port of hondalog/ui/backends.py PollerMapper (sweep, console,
 * export, rank on the sweep history).
 */

import { checksum, findAnyFrame, fmtHex, isReadRequest, withChecksum } from './hex';
import { SweepRecording } from './sweeps';

/** What the scanner needs from the K-line session (agent #1's poller). Every
 *  read is submitted through the poller so it shares the line with polling. */
export interface MemLink {
  available(): boolean;
  /** The link runs standard OBD2 PIDs: raw Honda reads are refused. */
  obd2Only?(): boolean;
  readMem(addr: number, len: number): Promise<Uint8Array>;
  /** Send a frame as typed, return the echo and whatever came back. */
  raw?(frame: Uint8Array): Promise<{ echo: Uint8Array; rest: Uint8Array }>;
}

export const OBD2_ONLY = 'OBD2 only: this ECU answers standard OBD2 (ISO 9141-2), not Honda reads, so the '
  + 'sweep and the console are off. Live values, codes and readiness still work.';

export type Block = [number, number];
export const FULL_BLOCKS: Block[] = Array.from({ length: 16 }, (_, i) => [i * 16, 16] as Block);

export type SweepListener = (mem: Int16Array, times: Float64Array, t: number) => void;

export class MemoryScanner {
  static HIST = 120;
  cur = new Int16Array(256).fill(-1);
  fresh = new Uint8Array(256);
  changes = new Int32Array(256);
  minv = new Int16Array(256).fill(255);
  maxv = new Int16Array(256);
  sweeps = 0;
  sweepS = 0;
  sweeping = false;
  revision = 0;
  expert = false;
  /** Every frame sent with the expert unlock (for the log header). */
  expertLog: string[] = [];
  private hist: Int16Array[] = [];
  private histT: number[] = [];
  private consoleText = '';
  private blocks: Block[] = FULL_BLOCKS;
  private loop: Promise<void> | null = null;
  private listeners = new Set<() => void>();
  private sweepListeners = new Set<SweepListener>();

  constructor(public link: MemLink | null, readonly clock: () => number = () => performance.now() / 1000) {}

  available(): boolean {
    return !!this.link && this.link.available() && !this.obd2Only();
  }

  obd2Only(): boolean {
    return !!this.link?.obd2Only?.();
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  onSweep(fn: SweepListener): () => void {
    this.sweepListeners.add(fn);
    return () => this.sweepListeners.delete(fn);
  }

  private bump(): void {
    this.revision++;
    for (const fn of this.listeners) fn();
  }

  /** Read only these blocks (the poke test's fast steps); null = the full sweep. */
  setBlocks(blocks: Block[] | null): void {
    this.blocks = blocks ?? FULL_BLOCKS;
  }

  setSweeping(on: boolean): void {
    this.sweeping = on && this.available();
    if (on && this.obd2Only()) this.consoleText = OBD2_ONLY;
    this.bump();
    if (this.sweeping && !this.loop) {
      this.loop = this.run().finally(() => { this.loop = null; });
    }
  }

  /** Resolves when the sweep loop has stopped (after setSweeping(false)). */
  async stopped(): Promise<void> {
    await this.loop;
  }

  private async run(): Promise<void> {
    while (this.sweeping && this.available()) {
      await this.sweepOnce();
      // let the UI (and timers) run between sweeps even when reads resolve at once
      await new Promise((res) => setTimeout(res, 0));
    }
    if (this.sweeping) { this.sweeping = false; this.bump(); }
  }

  /** One pass over the blocks. Public so tests and the poke test can step it. */
  async sweepOnce(): Promise<void> {
    const link = this.link;
    if (!link) return;
    const t0 = this.clock();
    const times = new Float64Array(256).fill(NaN);
    const row = new Int16Array(256).fill(-1);
    this.fresh.fill(0);
    let failed = 0;
    let lastErr = '';
    for (const [addr, len] of this.blocks) {
      if (!this.available()) break;
      let data: Uint8Array;
      try {
        data = await link.readMem(addr, len);
      } catch (e) {
        failed++;
        lastErr = (e as Error).message;
        continue;
      }
      const t = this.clock();
      for (let i = 0; i < data.length && addr + i < 256; i++) {
        const a = addr + i, v = data[i]!;
        const old = this.cur[a]!;
        if (old >= 0 && old !== v) { this.fresh[a] = 1; this.changes[a]!++; }
        this.minv[a] = Math.min(this.minv[a]!, v);
        this.maxv[a] = Math.max(this.maxv[a]!, v);
        this.cur[a] = v;
        row[a] = v;
        times[a] = t;
      }
    }
    if (failed === this.blocks.length) {
      this.sweeping = false;
      this.consoleText = `sweep stopped: ${lastErr || 'no reply'}`;
      this.bump();
      return;
    }
    const now = this.clock();
    this.sweeps++;
    this.sweepS = now - t0;
    // the history keeps the newest value of every byte, read this sweep or not
    this.hist.push(Int16Array.from(this.cur));
    this.histT.push(now);
    if (this.hist.length > MemoryScanner.HIST) { this.hist.shift(); this.histT.shift(); }
    for (const fn of this.sweepListeners) fn(row, times, now);
    this.bump();
  }

  clearChanges(): void {
    this.changes.fill(0);
    this.fresh.fill(0);
    for (let a = 0; a < 256; a++) {
      const v = this.cur[a]!;
      this.minv[a] = v >= 0 ? v : 255;
      this.maxv[a] = v >= 0 ? v : 0;
    }
    this.hist = [];
    this.histT = [];
    this.sweeps = 0;
    this.bump();
  }

  /** Recent values of one byte, oldest first (the inspector's trace). */
  history(addr: number): number[] {
    return this.hist.map((r) => r[addr]!).filter((v) => v >= 0);
  }

  /** The sweep history as a recording for the correlation finder. */
  recording(): SweepRecording {
    const rec = new SweepRecording();
    this.hist.forEach((row, i) => { if (row.some((v) => v >= 0)) rec.append(row, this.histT[i]!); });
    return rec;
  }

  console(): string {
    return this.consoleText;
  }

  setConsole(text: string): void {
    this.consoleText = text;
    this.bump();
  }

  setExpert(on: boolean): void {
    this.expert = on;
    this.bump();
  }

  /** The hex console: refused frames never leave the app. */
  async send(data: Uint8Array, addChecksum: boolean): Promise<string> {
    const link = this.link;
    if (!link || !this.available() || !data.length || !link.raw) {
      this.setConsole(this.obd2Only() ? OBD2_ONLY : 'not connected');
      return this.consoleText;
    }
    const frame = addChecksum ? withChecksum(data) : data;
    if (!this.expert && !isReadRequest(frame)) {
      this.setConsole('!! refused: only read requests (20 05 addr len) go out; switch Expert on to send anything else');
      return this.consoleText;
    }
    if (this.expert && !isReadRequest(frame)) this.expertLog.push(`${new Date().toISOString()} ${fmtHex(frame)}`);
    const t0 = this.clock();
    try {
      const { echo, rest } = await link.raw(frame);
      const ms = (this.clock() - t0) * 1000;
      let note: string;
      if (!rest.length) note = 'no reply' + (echo.length ? '' : ', no echo: check cable and 12 V');
      else if (findAnyFrame(rest)) note = 'checksum OK';
      else note = 'no valid frame';
      this.setConsole(`${fmtHex(rest) || '(nothing)'}  ·  ${note} · ${ms.toFixed(0)} ms`);
    } catch (e) {
      this.setConsole(`!! ${(e as Error).message}`);
    }
    return this.consoleText;
  }

  /** The sweep as the Python export text (addr value min max changes). */
  exportText(now = new Date()): string {
    const pad = (n: number) => String(n).padStart(2, '0');
    const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
    const lines = [`# hondalog memory sweep, ${stamp}, ${this.sweeps} sweeps`, '# addr value min max changes'];
    for (let a = 0; a < 256; a++) {
      const v = this.cur[a]!;
      if (v >= 0) lines.push(`0x${a.toString(16).toUpperCase().padStart(2, '0')} ${v.toString(16).toUpperCase().padStart(2, '0')} ${this.minv[a]} ${this.maxv[a]} ${this.changes[a]}`);
    }
    return lines.join('\n') + '\n';
  }
}

/** The reply a Honda ECU gives a read: 00 len+3 data.. cksum. */
export function replyFrame(data: ArrayLike<number>): Uint8Array {
  const body = [0x00, (data.length + 3) & 0xff, ...Array.from(data)];
  return Uint8Array.from([...body, checksum(body)]);
}

/**
 * Demo / simulator RAM that follows a made-up idling car (backends.py
 * DemoMapper). Used when there is no link, by the tests, and by the poke
 * test's dry run (`act` takes the poke test's sim actions).
 */
export class DemoMemLink implements MemLink {
  state = { rpm: 780, ac: false, brake: false, load: false, key: true, running: true, rev: 0, ect: 82 };
  private t0: number;
  constructor(readonly clock: () => number = () => performance.now() / 1000, public delayMs = 0) {
    this.t0 = clock();
  }
  available(): boolean { return true; }

  act(action: string): void {
    const s = this.state;
    switch (action) {
      case 'ac_on': s.ac = true; break;
      case 'ac_off': s.ac = false; break;
      case 'brake_on': s.brake = true; break;
      case 'brake_off': s.brake = false; break;
      case 'load_on': s.load = true; break;
      case 'load_off': s.load = false; break;
      case 'rev': s.rev = this.clock(); break;
      case 'rev_hold': s.rpm = 3000; break;
      case 'rev_release': s.rpm = 780; break;
      case 'key_off': s.key = false; s.running = false; break;
      case 'key_on': s.key = true; break;
      case 'stall': s.running = false; break;
      case 'start': s.key = true; s.running = true; s.rpm = 780; break;
      default: break;
    }
  }

  /** The 256 bytes right now. */
  ram(): Int16Array {
    const s = this.state, t = this.clock() - this.t0;
    const r = new Int16Array(256).fill(0);
    let rpm = s.running ? s.rpm + 15 * Math.sin(t * 1.3) + (s.ac ? 40 : 0) : 0;
    if (s.running && s.rev && this.clock() - s.rev < 2) rpm = 2000;
    const x = Math.round(rpm * 4);
    r[0x00] = (x >> 8) & 0xff; r[0x01] = x & 0xff;
    r[0x08] = 0x84 | (s.ac ? 0x02 : 0) | (s.brake ? 0x08 : 0);
    r[0x0a] = 0x10 | (s.running ? 0x80 : 0);
    r[0x0b] = (s.running ? 0x41 : 0) | (s.key && !s.running ? 0x20 : 0) | (s.ac && s.running ? 0x02 : 0);
    r[0x0c] = s.load ? 0x00 : 0x01;
    r[0x0f] = s.running ? 0x01 : 0;
    r[0x10] = Math.round(200 - s.ect * 1.5);
    r[0x12] = s.running ? Math.round((30 + 5 + Math.sin(t)) / 0.716) : 147;
    r[0x13] = 147;
    r[0x14] = rpm > 1500 ? 40 : 0;
    r[0x15] = s.running ? Math.round(51.3 * (0.45 + 0.4 * Math.sin(t * 3))) : 0;
    r[0x17] = Math.round((s.running ? 14.1 - (s.load ? 0.3 : 0) : 12.4) * 10.45);
    r[0x19] = Math.round((s.load ? 40 : 20) * 2.5371);
    r[0x1c] = r[0x19]!;
    r[0x20] = 128 + Math.round(10 * Math.sin(t * 2));
    r[0x27] = rpm < 845 ? 0 : Math.min(4, Math.floor((rpm - 845) / 50) + 1);
    r[0x28] = s.running ? 10 + (s.ac ? 20 : 0) + (s.load ? 8 : 0) : 0;
    r[0x29] = s.running ? 40 + (s.ac ? 25 : 0) + (s.load ? 10 : 0) : 0;
    r[0x2f] = Math.floor(t / 33) & 0xff;
    r[0x33] = 55; r[0x35] = 45; r[0x36] = 7; r[0x38] = 25; r[0x39] = 25; r[0x3a] = 25;
    r[0x32] = 45 - (Math.floor(t * 7) % 17 === 0 ? 1 : 0);
    r[0x37] = 45;
    r[0x76] = 0x0c; r[0x77] = 0xa8;
    r[0x9a] = s.running ? 2 : 1;
    r[0x9c] = s.running ? 60 : 0;
    r[0xa0] = s.running ? 48 : 0;
    r[0xa4] = s.running ? 1 : 0;
    return r;
  }

  async readMem(addr: number, len: number): Promise<Uint8Array> {
    if (this.delayMs) await new Promise((res) => setTimeout(res, this.delayMs));
    if (!this.state.key) throw new Error('no reply');
    const r = this.ram();
    return Uint8Array.from({ length: Math.min(len, 256 - addr) }, (_, i) => r[addr + i]!);
  }

  async raw(frame: Uint8Array): Promise<{ echo: Uint8Array; rest: Uint8Array }> {
    if (isReadRequest(frame)) {
      const data = await this.readMem(frame[2]!, frame[3]!);
      return { echo: frame, rest: replyFrame(data) };
    }
    return { echo: frame, rest: new Uint8Array() };
  }
}
