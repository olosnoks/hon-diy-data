/**
 * The laptop's side of the handheld's ``HL`` text session (hondalog/handheld/hl_client.py):
 * list, read, write and delete files on the SD card, edit HANDHELD.CFG, upload the road
 * map, read a status snapshot, mark, set the clock, restart.
 *
 * Writing to a handheld that cannot listen (the GPS-on-P1 build) is refused before a
 * byte goes out: ``connect()`` waits for the binary hello and reads the variant first.
 * Uploads go in a window of ``win`` chunks, one ACK per chunk, whole-file CRC-32 checked
 * by the handheld before the new file replaces the old one; an interrupted upload
 * carries on from what the handheld already holds (``HL STATUS``).
 */

import { ascii, concat, crc32, hex8, b64, unb64 } from './bytes';
import { HlError, StreamSplitter, TIMEOUT_S } from './hl_proto';
import { type Clock, type CydPort, realClock } from './port';
import * as P from './serial_proto';

export { HlError };

export type Progress = (done: number, total: number) => void;

export const REASONS: Record<string, string> = {
  badcmd: 'the handheld did not understand the command (older firmware?)',
  args: 'bad arguments', path: 'that path is not allowed', denied: 'that file cannot be changed over USB',
  nosd: 'no SD card in the handheld', nofile: 'no such file',
  busy: 'the file is being written by the handheld: end the drive first', io: 'SD card error',
  crc: 'checksum mismatch', size: 'wrong size or too big', refused: 'the handheld could not load it',
  state: 'not expected now', offset: 'out of step', b64: 'a damaged data line',
  nosession: 'no session open', timeout: 'the handheld stopped answering',
  disconnected: 'the cable was disconnected', cancelled: 'cancelled',
  nohello: 'no hello from a handheld', txonly: 'this handheld cannot listen (GPS on P1 build)',
};
export const FINAL = new Set(['cancelled', 'disconnected', 'denied', 'path', 'nosd', 'busy', 'refused', 'args', 'txonly', 'badcmd']);

export function reasonText(e: unknown): string {
  const r = (e as HlError)?.reason;
  return r ? (REASONS[r] ?? String((e as Error).message)) : String((e as Error)?.message ?? e);
}

export class HlTxOnly extends HlError {
  constructor() { super('txonly'); }
}

export class Cancelled extends HlError {
  constructor() { super('cancelled'); }
}

export interface HlClientOptions {
  onConsole?: (text: string) => void;
  onState?: (open: boolean) => void;
  idleS?: number;
  chunk?: number;
  window?: number;
  clock?: Clock;
}

export class HlClient {
  readonly onConsole: (text: string) => void;
  readonly onState: (open: boolean) => void;
  readonly idleS: number;
  readonly clock: Clock;
  private readonly chunkOverride?: number;
  private readonly windowOverride?: number;
  private split = new StreamSplitter();
  private q: string[] = [];
  helloFrame: P.Hello | null = null;
  info: Record<string, string> = {};
  open = false;
  /** When the handheld will end an idle session (our estimate). */
  deadline = 0;
  cancelFlag = false;
  /** Bytes/s of the last transfer. */
  rate = 0;
  lastStats: P.Stats | null = null;

  constructor(readonly port: CydPort, opts: HlClientOptions = {}) {
    this.onConsole = opts.onConsole ?? (() => undefined);
    this.onState = opts.onState ?? (() => undefined);
    this.idleS = opts.idleS ?? 20.0;
    this.chunkOverride = opts.chunk;
    this.windowOverride = opts.window;
    this.clock = opts.clock ?? realClock;
  }

  // ---- the wire ----

  private frame(ftype: number, payload: Uint8Array): void {
    if (ftype === P.SF_HELLO) this.helloFrame = P.parseHello(payload) ?? this.helloFrame;
    else if (ftype === P.SF_LOG) this.onConsole(new TextDecoder().decode(payload).replace(/[\r\n]+$/, ''));
    else if (ftype === P.SF_STATS) this.lastStats = P.parseStats(payload);
  }

  async pump(wait = 0.02): Promise<void> {
    for (const piece of this.split.feed(await this.port.read(wait))) {
      if (piece.kind === 'frame') this.frame(piece.frame[0], piece.frame[2]);
      else if (piece.text === 'HL TIMEOUT') {
        this.setOpen(false);
        this.q.push(piece.text);
      } else if (piece.text.startsWith('HL ') || piece.text === 'HL') this.q.push(piece.text);
      else this.onConsole(piece.text);
    }
  }

  private setOpen(value: boolean): void {
    if (this.open !== value) {
      this.open = value;
      this.onState(value);
    }
  }

  private async send(text: string): Promise<void> {
    await this.port.write(concat(ascii(text), Uint8Array.of(0x0a)));
  }

  private async next(idle: number): Promise<string> {
    const end = this.clock.now() + idle;
    while (!this.q.length) {
      if (this.cancelFlag) throw new Cancelled();
      if (this.clock.now() > end) throw new HlError('timeout');
      await this.pump(0.02);
    }
    const line = this.q.shift()!;
    if (line === 'HL TIMEOUT') throw new HlError('timeout');
    return line;
  }

  /** Send; the HL lines up to and including the first one that starts with ``until``. */
  private async expect(command: string, until: string, idle = 8.0): Promise<string[]> {
    await this.ensure();
    this.q = [];
    await this.send(command);
    const out: string[] = [];
    for (;;) {
      const line = await this.next(idle);
      if (line.startsWith('HL ERR')) throw new HlError(line.slice(7).trim() || 'state');
      out.push(line);
      if (line.startsWith(until)) {
        this.deadline = this.clock.now() + TIMEOUT_S;
        return out;
      }
    }
  }

  /** Throw away what is still arriving (the tail of a failed transfer). */
  async drain(quiet = 0.3): Promise<void> {
    let end = this.clock.now() + quiet;
    while (this.clock.now() < end) {
      const before = this.q.length;
      await this.pump(0.02);
      if (this.q.length !== before) end = this.clock.now() + quiet;
    }
    this.q = [];
  }

  // ---- connection ----

  /** Find the speed the handheld talks at (its binary hello says who it is) and refuse a TX-only one.
   *  Nothing is written until the hello is in. */
  async connect(bauds: readonly number[] = P.BAUDS, listenS = 3.5): Promise<P.Hello> {
    for (const baud of bauds) {
      try {
        await this.port.setBaud(baud);
      } catch {
        /* a port that has not opened yet */
      }
      this.helloFrame = null;
      const end = this.clock.now() + listenS;
      while (this.clock.now() < end && this.helloFrame === null) {
        await this.pump(0.05);
        if (this.q.some((l) => l.startsWith('HL '))) break;
      }
      if (this.helloFrame !== null) {
        if (!P.twoWay(this.helloFrame)) throw new HlTxOnly();
        return this.helloFrame;
      }
      if (this.q.length) { // an HL session left open by an earlier program: it is two-way
        this.helloFrame = null;
        return P.makeHello({ caps: P.CAP_RX, baud });
      }
    }
    throw new HlError('nohello');
  }

  /** Open (or re-open) the session. Asked again every second: the handheld may be mid-frame. */
  async hello(total = 8.0): Promise<Record<string, string>> {
    const end = this.clock.now() + total;
    let nxt = 0;
    this.q = [];
    for (;;) {
      const now = this.clock.now();
      if (this.cancelFlag) throw new Cancelled();
      if (now > end) throw new HlError('nohello');
      if (now >= nxt) {
        nxt = now + 1.0;
        await this.port.write(ascii('\nHL HELLO\n')); // the LF drops a half-received line
      }
      await this.pump(0.05);
      while (this.q.length) {
        const line = this.q.shift()!;
        if (line.startsWith('HL HELLO')) {
          this.info = {};
          for (const t of line.split(/\s+/).slice(2)) {
            const i = t.indexOf('=');
            if (i >= 0) this.info[t.slice(0, i)] = t.slice(i + 1);
          }
          this.setOpen(true);
          this.deadline = this.clock.now() + TIMEOUT_S;
          return this.info;
        }
      }
    }
  }

  private async ensure(): Promise<void> {
    if (this.open && this.clock.now() > this.deadline) this.setOpen(false); // the handheld has ended it by now
    if (!this.open) await this.hello();
  }

  async bye(): Promise<void> {
    if (!this.open) return;
    try {
      await this.expect('HL BYE', 'HL OK', 2.0);
    } catch (e) {
      if (!(e instanceof HlError)) throw e;
    }
    this.setOpen(false);
  }

  async close(): Promise<void> {
    try {
      await this.bye();
    } catch {
      /* the port is gone */
    }
  }

  /** A command that costs nothing, to keep the session from timing out. */
  async keepalive(): Promise<void> {
    await this.expect('HL STAT', 'HL STAT ');
  }

  get chunk(): number { return this.chunkOverride || parseInt(this.info.chunk ?? '768', 10); }
  get window(): number { return this.windowOverride || parseInt(this.info.win ?? '2', 10); }

  // ---- commands ----

  /** (files as [path, size, mtime], folders) */
  async ls(path = '/'): Promise<[Array<[string, number, number]>, string[]]> {
    const files: Array<[string, number, number]> = [];
    const dirs: string[] = [];
    for (const line of (await this.expect('HL LS ' + path, 'HL END', 15.0)).slice(0, -1)) {
      if (line.startsWith('HL F ')) {
        const rest = line.slice(5);
        const a = rest.indexOf(' ');
        const b = rest.indexOf(' ', a + 1);
        files.push([rest.slice(b + 1), parseInt(rest.slice(0, a), 10), parseInt(rest.slice(a + 1, b), 10)]);
      } else if (line.startsWith('HL D ')) dirs.push(line.slice(5).trim());
    }
    return [files, dirs];
  }

  private async getRaw(command: string, progress?: Progress, idle = 20.0): Promise<Uint8Array> {
    await this.ensure();
    this.q = [];
    await this.send(command);
    let size = -1;
    let got = 0;
    const chunks: Uint8Array[] = [];
    const t0 = this.clock.now();
    for (;;) {
      const line = await this.next(idle);
      if (line.startsWith('HL BEGIN ')) size = parseInt(line.slice(9), 10);
      else if (size < 0 && (line.startsWith('HL DATA ') || line.startsWith('HL END'))) continue; // an earlier transfer's tail
      else if (line.startsWith('HL DATA ')) {
        const data = unb64(line.slice(8).trim());
        if (data === null) throw new HlError('b64');
        chunks.push(data);
        got += data.length;
        progress?.(got, size);
      } else if (line.startsWith('HL END ')) {
        const out = concat(...chunks);
        if (size !== out.length) throw new HlError('size');
        if (crc32(out) !== parseInt(line.slice(7).trim(), 16)) throw new HlError('crc');
        this.deadline = this.clock.now() + TIMEOUT_S;
        this.rate = out.length / Math.max(this.clock.now() - t0, 1e-6);
        return out;
      } else if (line.startsWith('HL ERR')) throw new HlError(line.slice(7).trim() || 'state');
    }
  }

  /** The file's bytes (one retry for a damaged line). */
  async get(path: string, progress?: Progress): Promise<Uint8Array> {
    try {
      return await this.getRaw('HL GET ' + path, progress);
    } catch (e) {
      if (!(e instanceof HlError) || FINAL.has(e.reason)) throw e;
      await this.drain();
      return this.getRaw('HL GET ' + path, progress);
    }
  }

  async cfgGet(progress?: Progress): Promise<Uint8Array> {
    return this.getRaw('HL CFG GET', progress);
  }

  async rm(path: string): Promise<void> {
    await this.expect('HL RM ' + path, 'HL OK');
  }

  async stat(): Promise<Record<string, any>> {
    const lines = await this.expect('HL STAT', 'HL STAT ');
    return JSON.parse(lines[lines.length - 1]!.slice(8));
  }

  async mark(): Promise<void> {
    await this.expect('HL MARK', 'HL OK');
  }

  /** The handheld's clock to ``unixMs`` (now). Answer: ``ok``, or ``ignored`` when it has GPS time. */
  async setTime(unixMs?: number): Promise<string> {
    const ms = Math.trunc(unixMs ?? Date.now());
    const lines = await this.expect(`HL TIME ${ms}`, 'HL OK');
    return lines[lines.length - 1]!.slice(5).trim() || 'ok';
  }

  async reboot(): Promise<void> {
    try {
      await this.expect('HL REBOOT', 'HL OK', 3.0);
    } catch (e) {
      if (!(e instanceof HlError) || e.reason !== 'timeout') throw e;
    }
    this.setOpen(false);
  }

  /** What the handheld holds of an unfinished upload: [held bytes, size, crc32]; held 0 = nothing. */
  async partial(path: string): Promise<[number, number, number]> {
    const lines = await this.expect('HL STATUS ' + path, 'HL PART', 5.0);
    const f = lines[lines.length - 1]!.split(/\s+/);
    return [parseInt(f[2] ?? '0', 10), parseInt(f[3] ?? '0', 10), parseInt(f[4] ?? '0', 16)];
  }

  // ---- uploads ----

  private async putOnce(command: string, data: Uint8Array, start: number, progress?: Progress): Promise<string> {
    await this.ensure();
    this.q = [];
    const crc = crc32(data);
    const n = data.length;
    await this.send(`${command} ${n} ${hex8(crc)}` + (start ? ` ${start}` : ''));
    let line = await this.next(10.0);
    if (line.startsWith('HL ERR')) throw new HlError(line.slice(7).trim() || 'state');
    if (!line.startsWith('HL READY')) throw new HlError('state');
    const f = line.split(/\s+/);
    let pos = f.length > 2 ? parseInt(f[2]!, 10) : 0;
    let acked = pos;
    const t0 = this.clock.now();
    const base = pos;
    let inflight: number[] = [];
    const chunk = this.chunk;
    const window = this.window;
    progress?.(acked, n);
    let sentEmpty = false;
    for (;;) {
      while (pos < n && inflight.length < window) {
        if (this.cancelFlag) throw new Cancelled();
        const part = data.subarray(pos, pos + chunk);
        await this.port.write(ascii(`HL DATA @${pos} ${b64(part)}\n`));
        pos += part.length;
        inflight.push(pos);
      }
      if (n === 0 && !sentEmpty) {
        sentEmpty = true;
        await this.send('HL DATA ');
      }
      line = await this.next(this.idleS);
      if (line.startsWith('HL ACK ')) {
        acked = parseInt(line.slice(7), 10);
        inflight = inflight.filter((e) => e > acked);
        progress?.(acked, n);
      } else if (line.startsWith('HL OK')) {
        this.deadline = this.clock.now() + TIMEOUT_S;
        this.rate = (n - base) / Math.max(this.clock.now() - t0, 1e-6);
        progress?.(n, n);
        return line.slice(5).trim();
      } else if (line.startsWith('HL ERR')) throw new HlError(line.slice(7).trim() || 'state');
    }
  }

  private async putWithResume(command: string, path: string, data: Uint8Array, progress: Progress | undefined,
    attempts: number, onRetry?: (attempt: number, e: Error) => void): Promise<string> {
    const crc = crc32(data);
    let last: Error | null = null;
    for (let attempt = 0; attempt < attempts; attempt++) {
      try {
        let held = 0, size = 0, pcrc = 0;
        try {
          [held, size, pcrc] = await this.partial(path);
        } catch (e) {
          if (!(e instanceof HlError) || FINAL.has(e.reason)) throw e;
        }
        const start = size === data.length && pcrc === crc && held > 0 && held < data.length ? held : 0;
        return await this.putOnce(command, data, start, progress);
      } catch (e) {
        if (!(e instanceof HlError)) throw e;
        last = e;
        if (FINAL.has(e.reason) || (e.reason === 'crc' && attempt >= 1)) throw e;
        onRetry?.(attempt + 1, e);
        this.setOpen(false);
        try {
          await this.drain();
        } catch {
          /* the port is gone: the next attempt says so */
        }
      }
    }
    throw last!;
  }

  /** Write ``data`` to ``path`` (MAP/*, HANDHELD.CFG, HANDHELD.MAP). Resumes after an interruption.
   *  Returns the handheld's note (``lines=.. skipped=..`` for a config). */
  async put(path: string, data: Uint8Array, progress?: Progress, attempts = 4,
    onRetry?: (attempt: number, e: Error) => void): Promise<string> {
    return this.putWithResume('HL PUT ' + path, path, data, progress, attempts, onRetry);
  }

  async cfgPut(data: Uint8Array, progress?: Progress, attempts = 4): Promise<string> {
    return this.putWithResume('HL CFG PUT', '/HANDHELD.CFG', data, progress, attempts);
  }

  /** Upload a checked MAP folder's files (``mapFolderFiles``) into /MAP. */
  async putMapFiles(files: Array<[string, Uint8Array]>,
    progress?: (name: string, held: number, size: number, done: number, total: number) => void,
    onRetry?: (attempt: number, e: Error) => void): Promise<Array<[string, number]>> {
    const total = files.reduce((s, [, d]) => s + d.length, 0);
    let done = 0;
    const out: Array<[string, number]> = [];
    for (const [name, data] of files) {
      const base = done;
      const cb = progress ? (held: number, size: number) => progress(name, held, size, base + held, total) : undefined;
      await this.putWithResume('HL PUT /MAP/' + name, '/MAP/' + name, data, cb, 4, onRetry);
      out.push([name, data.length]);
      done += data.length;
    }
    return out;
  }
}

export const MAP_FILES = ['MAP.DAT', 'MAP.IDX', 'README.TXT'];

export class MapFolderError extends Error {}

/** Check a MAP folder's files (name -> bytes): [name, bytes] in upload order (MAP.DAT first). */
export function mapFolderFiles(found: Record<string, Uint8Array>): Array<[string, Uint8Array]> {
  const up: Record<string, Uint8Array> = {};
  for (const [k, v] of Object.entries(found)) up[k.toUpperCase()] = v;
  for (const need of ['MAP.IDX', 'MAP.DAT']) {
    if (!(need in up)) throw new MapFolderError(`no ${need}: pick the MAP folder made by \`hondalog-handheld map-build\``);
  }
  const idx = up['MAP.IDX']!, dat = up['MAP.DAT']!;
  const magic = (b: Uint8Array, m: string) => new TextDecoder().decode(b.subarray(0, 8)) === m;
  if (!magic(idx, 'HHMAPIDX') || !magic(dat, 'HHMAPDAT') || idx.length < 428 || dat.length < 16) {
    throw new MapFolderError('MAP.IDX / MAP.DAT are not hondalog map files');
  }
  if (crc32(idx.subarray(0, 424)) !== new DataView(idx.buffer, idx.byteOffset + 424, 4).getUint32(0, true)) {
    throw new MapFolderError('MAP.IDX is damaged (header checksum)');
  }
  for (let i = 0; i < 4; i++) {
    if (idx[96 + i] !== dat[12 + i]) throw new MapFolderError('MAP.IDX and MAP.DAT are from different builds');
  }
  const out: Array<[string, Uint8Array]> = [['MAP.DAT', dat], ['MAP.IDX', idx]];
  if (up['README.TXT']) out.push(['README.TXT', up['README.TXT']]);
  return out;
}
