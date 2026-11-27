/**
 * Request and answer over the handheld's binary link (hondalog/handheld/serial_link.py
 * ``CydClient``): list and fetch files with resume, push HANDHELD.MAP / HANDHELD.CFG,
 * upload files into the card's MAP/ folder, set the clock, mark, start/stop the drive.
 * Two-way firmware only. Plus the pieces the live link shares: the baud hunt
 * (``Opener``), the clock exchange (``ClockSync``), the one-way clock, ``UsedLength``.
 *
 * One rule keeps the tx-only variant safe: **nothing is written to the port until a
 * HELLO has said the handheld listens.**
 */

import { concat, crc32 } from './bytes';
import * as F from './format';
import type { HhMap } from './hhmap';
import { mapFolderFiles } from './hl_client';
import { buildMap } from './mapfile';
import { type Clock, CydBusy, CydError, type CydPort, realClock } from './port';
import * as P from './serial_proto';

export { CydBusy, CydError };

export const PREFIX = 'cyd:';
/** No frame for this long: the stream has stopped. */
export const SILENT_S = 1.5;
/** No good frame for this long after opening: try the next baud. */
export const HUNT_S = 3.0;

/** Opening, and finding the baud rate: if nothing readable arrives for HUNT_S the next of BAUDS is tried. */
export class Opener {
  readonly bauds: number[];
  private i = 0;
  tChange = 0;
  /** A good frame arrived at this speed. */
  locked = false;

  constructor(readonly port: CydPort, private readonly clock: Clock = realClock) {
    const first = port.baud || P.BAUDS[0];
    this.bauds = port.fixedBaud ? [first] : [first, ...P.BAUDS.filter((b) => b !== first)];
  }

  async open(): Promise<void> {
    await this.port.open();
    this.tChange = this.clock.now();
  }

  good(): void {
    this.locked = true;
  }

  /** Call while nothing good has arrived. True if the baud was changed. */
  async hunt(now: number): Promise<boolean> {
    if (this.locked || this.bauds.length < 2 || now - this.tChange < HUNT_S) return false;
    this.i = (this.i + 1) % this.bauds.length;
    await this.port.setBaud(this.bauds[this.i]!);
    this.tChange = now;
    return true;
  }
}

/**
 * Offset between the handheld's clock and the laptop's (blackbox/reader.py ClockSync):
 * the exchange with the shortest round trip in the recent window is used.
 * ``host_us = device_us + offset_us``.
 */
export class ClockSync {
  private recent: Array<[number, number]> = [];
  offsetUs: number | null = null;
  rttUs: number | null = null;
  count = 0;

  constructor(private readonly window = 16) {}

  get valid(): boolean { return this.offsetUs !== null; }

  add(hostTxUs: number, devRxUs: number, devTxUs: number, hostRxUs: number): void {
    const rtt = (hostRxUs - hostTxUs) - (devTxUs - devRxUs);
    if (rtt < 0) return;
    const offset = (hostTxUs + hostRxUs) / 2 - (devRxUs + devTxUs) / 2;
    this.recent.push([rtt, offset]);
    if (this.recent.length > this.window) this.recent.shift();
    let best = this.recent[0]!;
    for (const r of this.recent) if (r[0] < best[0] || (r[0] === best[0] && r[1] < best[1])) best = r;
    [this.rttUs, this.offsetUs] = best;
    this.count += 1;
  }

  toHostUs(devUs: number): number {
    return devUs + (this.offsetUs ?? 0);
  }
}

/** Device time -> laptop time when nothing can be asked: the smallest ``arrival - stamp`` of recent frames. */
export class OneWayClock {
  private recent: number[] = [];
  offset: number | null = null;

  constructor(private readonly window = 64) {}

  add(hostS: number, devUs: number): void {
    this.recent.push(hostS - devUs / 1e6);
    if (this.recent.length > this.window) this.recent.shift();
    this.offset = Math.min(...this.recent);
  }

  reset(): void {
    this.recent = [];
    this.offset = null;
  }
}

/**
 * Follows a drive or capture file as it arrives and says where its last good block
 * ends, once ``GARBAGE`` bytes past it have shown no other (the preallocated tail of a
 * drive that power cut short). A clean END stops it at once.
 */
export class UsedLength {
  static readonly GARBAGE = 256 * 1024;
  private buf: Uint8Array = new Uint8Array(0);
  nonce: number | null = null;
  pos = 0;
  used = 0;
  /** Not a drive file: never cut. */
  off = false;

  feed(data: Uint8Array): number | null {
    if (this.off) return null;
    this.buf = concat(this.buf, data);
    const buf = this.buf;
    if (this.nonce === null) {
      if (buf.length < F.FILE_HDR.size) return null;
      const fields = F.FILE_HDR.unpack(buf);
      if (fields[0] !== F.FILE_MAGIC) {
        this.off = true;
        return null;
      }
      this.nonce = fields[9];
      this.pos = this.used = fields[2];
    }
    const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
    while (this.pos + F.BLOCK_HDR.size <= buf.length) {
      const p = this.pos;
      const [magic, kind, , , length] = F.BLOCK_HDR.unpack(buf, p);
      if (magic === F.BLOCK_MAGIC && length <= F.MAX_PAYLOAD) {
        const end = p + F.BLOCK_HDR.size + length + 4;
        if (end > buf.length) break; // the rest of it is on the way
        if (dv.getUint32(end - 4, true) === F.blockCrc(this.nonce!, buf.subarray(p, end - 4))) {
          this.pos = this.used = end;
          if (kind === F.B_END) return end;
          continue;
        }
      }
      const nxt = indexOfMagic(buf, p + 1);
      this.pos = nxt >= 0 ? nxt : Math.max(p + 1, buf.length - 3);
    }
    return buf.length - this.used >= UsedLength.GARBAGE ? this.used : null;
  }
}

function indexOfMagic(buf: Uint8Array, from: number): number {
  const m = F.BLOCK_MAGIC_BYTES;
  for (let i = from; i + 4 <= buf.length; i++) {
    if (buf[i] === m[0] && buf[i + 1] === m[1] && buf[i + 2] === m[2] && buf[i + 3] === m[3]) return i;
  }
  return -1;
}

export interface FileEntry {
  kind: number;
  fileNo: number;
  size: number;
  open: boolean;
}

export function entryName(e: { kind: number; fileNo: number }): string {
  const n = String(e.fileNo).padStart(6, '0');
  switch (e.kind) {
    case P.F_DRIVE: return `DRIVES/D${n}.HHD`;
    case P.F_CAPTURE: return `CAPTURES/C${n}.HHD`;
    case P.F_MAP: return 'HANDHELD.MAP';
    case P.F_CFG: return 'HANDHELD.CFG';
    default: return `kind${e.kind}/${e.fileNo}`;
  }
}

/** Where fetched bytes go (sync's mirror; a ``.part`` file while it arrives). */
export interface PartSink {
  /** What an earlier, interrupted transfer left (empty if none). */
  existing(): Promise<Uint8Array>;
  /** Start over (empty it). */
  reset(): Promise<void>;
  append(data: Uint8Array): Promise<void>;
  truncate(length: number): Promise<void>;
  /** The transfer is whole: make it the real file. */
  finish(): Promise<void>;
}

export class MemorySink implements PartSink {
  data: Uint8Array = new Uint8Array(0);
  finished = false;
  constructor(initial?: Uint8Array) { if (initial) this.data = initial.slice(); }
  async existing() { return this.data; }
  async reset() { this.data = new Uint8Array(0); }
  async append(d: Uint8Array) { this.data = concat(this.data, d); }
  async truncate(n: number) { this.data = this.data.slice(0, n); }
  async finish() { this.finished = true; }
}

type Accept<T> = (type: number, seq: number, payload: Uint8Array, sentSeq: number) => T | null | undefined;

/** One command at a time on a port this object owns (the app must not be connected to it meanwhile). */
export class CydClient {
  hello: P.Hello | null = null;
  stats: P.Stats = {};
  frames = 0;
  private deframer = new P.Deframer();
  private seq = 0;
  private pending: P.Frame[] = [];
  private opener: Opener | null = null;
  readonly clock: Clock;

  constructor(readonly port: CydPort, opts: { timeout?: number; retries?: number; clock?: Clock } = {},
  ) {
    this.timeout = opts.timeout ?? 1.5;
    this.retries = opts.retries ?? 4;
    this.clock = opts.clock ?? realClock;
  }

  timeout: number;
  retries: number;

  get damaged(): number { return this.deframer.bad; }

  /** Open the port and wait for the handheld's hello (one every 2 s; the baud rates are tried in turn). */
  async open(waitS = 12.0): Promise<P.Hello> {
    this.opener = new Opener(this.port, this.clock);
    await this.opener.open();
    const end = this.clock.now() + waitS;
    while (this.clock.now() < end) {
      for (const [ftype, , payload] of await this.readFrames(0.1)) {
        if (ftype === P.SF_HELLO) {
          const hello = P.parseHello(payload);
          if (hello !== null) {
            this.hello = hello;
            return hello;
          }
        }
      }
      if (await this.opener.hunt(this.clock.now())) this.deframer = new P.Deframer();
    }
    await this.close();
    throw new CydError(`no hello from a handheld on ${this.port.name} (is it running, and is the link on? ` +
      'HANDHELD.CFG: serial = 1)');
  }

  async close(): Promise<void> {
    await this.port.close();
  }

  private async readFrames(timeout: number): Promise<P.Frame[]> {
    let out = this.pending;
    this.pending = [];
    if (!out.length) out = this.deframer.feed(await this.port.read(timeout));
    for (const [ftype, , payload] of out) {
      this.frames += 1;
      this.opener?.good();
      if (ftype === P.SF_STATS) this.stats = P.parseStats(payload) ?? this.stats;
    }
    return out;
  }

  needTwoWay(): void {
    if (this.hello === null) throw new CydError('not open');
    if (!P.twoWay(this.hello)) {
      throw new CydError(`this handheld is tx-only (${P.variantName(this.hello)}: the GPS has the port's receive ` +
        'pin), so it cannot take commands. Move the SD card to the laptop instead');
    }
  }

  /** Send a command; feed every frame to ``accept`` until it returns something. Sent again on a timeout. */
  async request<T>(ftype: number, payload: Uint8Array, accept: Accept<T>, timeout?: number, retries?: number): Promise<T> {
    this.needTwoWay();
    const t = timeout ?? this.timeout;
    const n = 1 + (retries ?? this.retries);
    for (let attempt = 0; attempt < n; attempt++) {
      const seq = this.seq;
      this.seq = (this.seq + 1) & 0xff;
      await this.port.write(P.frameEncode(ftype, payload, seq));
      const end = this.clock.now() + t;
      while (this.clock.now() < end) {
        const frames = await this.readFrames(Math.min(0.05, Math.max(0.001, end - this.clock.now())));
        for (let i = 0; i < frames.length; i++) {
          const [rtype, rseq, rpayload] = frames[i]!;
          const result = accept(rtype, rseq, rpayload, seq);
          if (result !== null && result !== undefined) {
            this.pending.push(...frames.slice(i + 1));
            return result;
          }
        }
      }
    }
    throw new CydError('the handheld did not answer');
  }

  /** A command answered by an ACK: [status, value]. */
  async command(ftype: number, payload: Uint8Array = new Uint8Array(0), retries?: number, timeout?: number):
    Promise<[number, number]> {
    return this.request(ftype, payload, (rtype, _rseq, p, seq) => {
      if (rtype !== P.SF_ACK || p.length < P.ACK.size) return null;
      const [cmd, cseq, status, value] = P.ACK.unpack(p);
      return cmd === ftype && cseq === seq ? [status, value] as [number, number] : null;
    }, timeout, retries);
  }

  // -- engine --

  /** Not sent twice: a second marker would be a lie. */
  async mark(): Promise<void> { await this.command(P.SC_MARK, undefined, 0); }
  async ackAlarm(): Promise<void> { await this.command(P.SC_ACK_ALARM); }
  async drive(on: boolean): Promise<void> { await this.command(P.SC_DRIVE, Uint8Array.of(on ? 1 : 0)); }
  async requestDtc(): Promise<void> { await this.command(P.SC_DTC, undefined, 0); }

  /** Set the handheld's clock from this computer's. False: it keeps its own (its GPS has set it). */
  async setTime(unixS?: number): Promise<boolean> {
    const us = BigInt(Math.trunc((unixS ?? Date.now() / 1000) * 1e6));
    const [status] = await this.command(P.SC_TIME_SET, P.TIME_REQ.pack(us));
    if (status !== P.OK && status !== P.IGNORED) throw new CydError(`set-time: ${P.statusText(status)}`);
    return status === P.OK;
  }

  // -- files --

  async listFiles(kind: number): Promise<FileEntry[]> {
    const out: FileEntry[] = [];
    let after = 0;
    for (;;) {
      let page: FileEntry[] = [];
      const accept: Accept<[boolean, number]> = (rtype, _rseq, p, seq) => {
        if (rtype === P.SF_ACK && p.length >= P.ACK.size) {
          const [cmd, cseq, status] = P.ACK.unpack(p);
          if (cmd === P.SC_LIST_FILES && cseq === seq) throw new CydError(`list files: ${P.statusText(status)}`);
          return null;
        }
        if (rtype !== P.SF_FILE_INFO || p.length < P.FILE_INFO.size) return null;
        const [fkind, flags, , no, size] = P.FILE_INFO.unpack(p);
        if (fkind !== kind) return null;
        if (no === P.NO_FILE) return [Boolean(flags & P.FI_MORE), size];
        if (no > after && page.every((e) => e.fileNo !== no)) page.push({ kind: fkind, fileNo: no, size, open: Boolean(flags & P.FI_OPEN) });
        return null;
      };
      let [more, count] = await this.request(P.SC_LIST_FILES, P.LIST.pack(kind, after), accept);
      if (page.length !== count) { // a FILE_INFO frame was lost on the way: ask for the page again
        page = [];
        [more, count] = await this.request(P.SC_LIST_FILES, P.LIST.pack(kind, after), accept);
        if (page.length !== count) throw new CydError('the file list arrived incomplete twice');
      }
      out.push(...page);
      if (!more || !page.length) return out;
      after = page[page.length - 1]!.fileNo;
    }
  }

  /** [bytes, end of file]. Each chunk carries its own CRC32; a damaged one is asked for again. */
  async readChunk(kind: number, fileNo: number, offset: number, length = P.CHUNK): Promise<[Uint8Array, boolean]> {
    return this.request(P.SC_READ_FILE, P.READ_FILE.pack(kind, length, fileNo, offset), (rtype, _rseq, p, seq) => {
      if (rtype === P.SF_ACK && p.length >= P.ACK.size) {
        const [cmd, cseq, status] = P.ACK.unpack(p);
        if (cmd === P.SC_READ_FILE && cseq === seq) throw new CydError(`read file: ${P.statusText(status)}`);
        return null;
      }
      if (rtype !== P.SF_FILE_DATA || p.length < P.FILE_DATA.size) return null;
      const [fkind, flags, n, no, off, crc] = P.FILE_DATA.unpack(p);
      if (fkind !== kind || no !== fileNo) return null;
      if (off === P.NO_FILE) {
        if (flags & P.FD_BUSY) throw new CydBusy(`${entryName({ kind, fileNo })} is still being written`);
        throw new CydError(`${entryName({ kind, fileNo })}: no such file on the handheld`);
      }
      const data = p.slice(P.FILE_DATA.size, P.FILE_DATA.size + n);
      if (off !== offset || data.length !== n || crc32(data) !== crc) return null; // older or damaged: ask again
      return [data, Boolean(flags & P.FD_EOF)] as [Uint8Array, boolean];
    });
  }

  /**
   * Download a file into ``sink``. If the sink holds the start of an interrupted transfer (and it still
   * starts like the handheld's file) the download carries on after it. ``cut`` sees the file as it
   * arrives and may return a length: the rest is not wanted (``UsedLength``). Returns the final length.
   */
  async fetch(entry: FileEntry, sink: PartSink, progress?: (done: number, total: number) => void,
    cut?: UsedLength): Promise<number> {
    let offset = 0;
    const have = await sink.existing();
    if (have.length) {
      const headLen = Math.min(have.length, P.CHUNK);
      if (have.length <= entry.size && headLen) {
        const [first] = await this.readChunk(entry.kind, entry.fileNo, 0, headLen);
        if (first.length === headLen && first.every((b, i) => b === have[i])) offset = have.length;
      }
    }
    let stop: number | null = null;
    if (cut && offset) stop = cut.feed(have);
    if (!offset) await sink.reset();
    let done = offset >= entry.size && entry.size > 0;
    while (!done && stop === null) {
      const [data, eof] = await this.readChunk(entry.kind, entry.fileNo, offset);
      done = eof;
      await sink.append(data);
      offset += data.length;
      progress?.(offset, entry.size);
      if (!data.length && !done) throw new CydError(`${entryName(entry)}: the handheld sent an empty chunk before the end`);
      if (cut) stop = cut.feed(data);
    }
    if (stop !== null && stop < offset) {
      await sink.truncate(stop);
      offset = stop;
    }
    await sink.finish();
    return offset;
  }

  /** Send HANDHELD.MAP (F_MAP) or HANDHELD.CFG (F_CFG). The handheld checks it before it replaces the old
   *  file; an interrupted push carries on. Returns the handheld's note (a CFG: lines it will skip). */
  async push(kind: number, data: Uint8Array, progress?: (done: number, total: number) => void): Promise<number> {
    const crc = crc32(data);
    let [status, offset] = await this.command(P.SC_PUT_BEGIN, P.PUT_BEGIN.pack(kind, data.length, crc));
    if (status !== P.OK) throw new CydError(`push: ${P.statusText(status)}`);
    const step = 400;
    while (offset < data.length) {
      const chunk = data.subarray(offset, offset + step);
      const [st, value] = await this.command(P.SC_PUT_DATA, concat(P.U32.pack(offset), chunk));
      if (st === P.E_OFFSET) {
        offset = value; // it has more (or less) than we thought: go from there
        continue;
      }
      if (st !== P.OK) throw new CydError(`push: ${P.statusText(st)} at byte ${offset}`);
      offset = value;
      progress?.(offset, data.length);
    }
    let value: number;
    [status, value] = await this.command(P.SC_PUT_END, undefined, undefined, 5.0);
    if (status !== P.OK) throw new CydError(`push: ${P.statusText(status)}`);
    return value;
  }

  // -- files into the card's MAP/ folder --

  needMapUpload(): void {
    this.needTwoWay();
    if (!(this.hello!.flags & P.HF_SD)) throw new CydError('the handheld has no SD card');
    if (!(this.hello!.caps & P.CAP_MAPUP)) {
      throw new CydError("this handheld's firmware cannot take map files over the cable (it is older than the " +
        'MAP upload): flash the current firmware, or copy the MAP folder to the SD card');
    }
  }

  /** One file into the card's MAP/ (temporary file, checked whole, renamed). Resumes a cut-off transfer. */
  async putMapFile(name: string, data: Uint8Array, progress?: (done: number, total: number) => void,
    final = false, commitTimeout = 120.0): Promise<void> {
    const full = name.startsWith('MAP/') ? name : 'MAP/' + name;
    if (!P.mapupNameOk(full)) throw new CydError(`${full}: the handheld takes MAP/ and an 8.3 name in capitals`);
    if (!(data.length > 0 && data.length <= P.MAPUP_SIZE_MAX)) {
      throw new CydError(`${full}: ${data.length} bytes; the handheld takes 1 to ${P.MAPUP_SIZE_MAX}`);
    }
    this.needMapUpload();
    const crc = crc32(data);
    const wire = new TextEncoder().encode(full);
    let status = 0, offset = 0;
    for (let attempt = 0; attempt < 6; attempt++) {
      [status, offset] = await this.command(P.SC_MAPUP_OPEN, concat(P.MAPUP_OPEN.pack(0, wire.length, data.length, crc), wire));
      if (status === P.E_BUSY && attempt < 5) { // the map screen is still drawing: try again
        await this.clock.sleep(0.5);
        continue;
      }
      break;
    }
    if (status !== P.OK) throw new CydError(`${full}: ${P.statusText(status)}`);
    if (progress && offset) progress(offset, data.length);
    let badRun = 0;
    while (offset < data.length) {
      const chunk = data.subarray(offset, offset + P.MAPUP_CHUNK);
      const [st, value] = await this.command(P.SC_MAPUP_WRITE, concat(P.MAPUP_WRITE.pack(offset, crc32(chunk)), chunk));
      if (st === P.E_CRC) { // damaged on the way: send it again
        badRun += 1;
        if (badRun > 8) {
          throw new CydError(`${full}: chunks keep arriving damaged at byte ${offset} (a long cable? try ` +
            'cyd:...@460800)');
        }
        continue;
      }
      if (st === P.E_IO || st === P.E_BUSY) {
        badRun += 1;
        if (badRun > 8) throw new CydError(`${full}: ${P.statusText(st)} keeps happening at byte ${offset}`);
        await this.clock.sleep(0.3 * badRun);
        continue;
      }
      if (st === P.E_STATE) {
        throw new CydError(`${full}: the handheld dropped the upload at byte ${offset} (it was silent for ` +
          `${P.MAPUP_IDLE_S} s or restarted); run the command again`);
      }
      if (st !== P.OK && st !== P.E_OFFSET) throw new CydError(`${full}: ${P.statusText(st)} at byte ${offset}`);
      badRun = 0;
      offset = value;
      progress?.(offset, data.length);
    }
    // not sent twice on a timeout: the first one may have been done
    const [st] = await this.command(P.SC_MAPUP_COMMIT, P.MAPUP_COMMIT.pack(final ? P.MAPUP_FINAL : 0), 0, commitTimeout);
    if (st === P.E_CRC) {
      throw new CydError(`${full}: the file on the card does not match its checksum (a bad SD card?); it was not ` +
        'used and the old one is as it was');
    }
    if (st !== P.OK) throw new CydError(`${full}: ${P.statusText(st)} at the commit`);
  }

  /** The upload the handheld holds, if any. */
  async mapUploadInfo(): Promise<{ name: string; size: number; crc32: number; got: number; paused: boolean } | null> {
    const found: Array<{ name: string; size: number; crc32: number; got: number; paused: boolean }> = [];
    await this.request(P.SC_MAPUP_STATUS, new Uint8Array(0), (rtype, _rseq, p, seq) => {
      if (rtype === P.SF_MAPUP && p.length >= P.MAPUP_INFO.size) {
        const [state, got, size, crc, name] = P.MAPUP_INFO.unpack(p);
        if (state !== P.MU_NONE) found.push({ name: F.cstr(name), size, crc32: crc, got, paused: state === P.MU_PAUSED });
        return null;
      }
      if (rtype !== P.SF_ACK || p.length < P.ACK.size) return null;
      const [cmd, cseq, status, value] = P.ACK.unpack(p);
      return cmd === P.SC_MAPUP_STATUS && cseq === seq ? [status, value] : null;
    });
    return found[found.length - 1] ?? null;
  }

  async mapUploadStatus(): Promise<[boolean, number]> {
    const info = await this.mapUploadInfo();
    return info ? [true, info.got] : [false, 0];
  }

  async abortMapUpload(): Promise<void> {
    await this.command(P.SC_MAPUP_COMMIT, P.MAPUP_COMMIT.pack(P.MAPUP_ABORT));
  }

  /** A MAP folder (MAP.IDX, MAP.DAT, README.TXT; checked first) into the handheld's MAP/: README, MAP.DAT,
   *  then MAP.IDX last, so the pair that must match is only ever half replaced for the small index's transfer.
   *  A set cut off in an earlier run carries on from the file it was in. Returns the names sent. */
  async putMap(found: Record<string, Uint8Array>,
    progress?: (name: string, done: number, total: number, filesDone: number, all: number) => void): Promise<string[]> {
    const checked = new Map(mapFolderFiles(found));
    const files = ['README.TXT', 'MAP.DAT', 'MAP.IDX'].filter((n) => checked.has(n)).map((n) => [n, checked.get(n)!] as const);
    const total = files.reduce((s, [, d]) => s + d.length, 0);
    this.needMapUpload();
    let skip = 0;
    const info = await this.mapUploadInfo();
    if (info) {
      files.forEach(([n, d], i) => {
        if (info.name === 'MAP/' + n && info.size === d.length && info.crc32 === crc32(d)) skip = i;
      });
    }
    let done = files.slice(0, skip).reduce((s, [, d]) => s + d.length, 0);
    for (let i = skip; i < files.length; i++) {
      const [name, data] = files[i]!;
      const base = done;
      await this.putMapFile(name, data, (got, size) => progress?.(name, got, size, base + got, total), i === files.length - 1);
      done += data.length;
    }
    return files.map(([n]) => n);
  }
}

/** ``push-map``: send ``map`` as HANDHELD.MAP through an open CydClient; returns its size.
 *  The handheld uses it from its next power-up. (The app's push for relaying uses this too.) */
export async function pushMapTo(cyd: CydClient, map: HhMap, progress?: (done: number, total: number) => void): Promise<number> {
  const data = buildMap(map, map.text);
  await cyd.push(P.F_MAP, data, progress);
  return data.length;
}
