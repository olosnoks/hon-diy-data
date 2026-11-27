/**
 * A fake handheld on the USB serial link, as a ``CydPort`` on a virtual clock (tests/hhfake.py
 * with the parts of the simulator's command handling (cydsim.py) and HL session (cydsim_hl.py)
 * the laptop side talks to). It sends HELLO every ``helloS`` and a GPS record every ``gpsS``,
 * answers TIME_REQ, keeps every frame the laptop wrote, serves an in-memory SD card over
 * the binary commands (list/read/put/MAP upload) and over HL, and records RELAY_* frames.
 *
 * Used by this folder's tests; the UI's tests may use it too (no real port, no timers).
 */

import { b64, concat, crc32, fit, hex8, unb64 } from './bytes';
import * as F from './format';
import * as H from './hl_proto';
import { DEFAULT_CFG, readMap } from './mapfile';
import { CydError, type CydPort, VirtualClock } from './port';
import * as P from './serial_proto';

export const PEARL = [32.2746, -90.132] as const;

export interface FakeOptions {
  name?: string;
  caps?: number;
  fw?: string;
  mapHash?: string;
  fix?: boolean;
  sats?: number;
  helloS?: number;
  gpsS?: number;
  /** The speed the handheld talks at; a port at another speed reads noise. */
  deviceBaud?: number;
  /** The port's starting speed (default: the device's). */
  baud?: number;
  fixedBaud?: boolean;
  txOnly?: boolean;
  /** The card: path (``DRIVES/D000001.HHD``) -> bytes; null = no card. */
  sd?: Map<string, Uint8Array> | null;
  /** The GPS has set the clock (TIME_SET / HL TIME are ignored). */
  gpsTime?: boolean;
  device?: string;
  logs?: boolean;
  clock?: VirtualClock;
  usb?: { vendorId: number; productId: number };
}

interface Part { parts: string[]; n: number; crc: number; held: number; data: Uint8Array; lines: number }

export class FakeCyd implements CydPort {
  readonly name: string;
  baud: number;
  readonly fixedBaud: boolean;
  readonly usb?: { vendorId: number; productId: number };
  readonly clock: VirtualClock;
  caps: number;
  fw: string;
  mapHash: string;
  fix: boolean;
  sats: number;
  helloS: number;
  gpsS: number;
  deviceBaud: number;
  txOnly: boolean;
  sd: Map<string, Uint8Array> | null;
  gpsTime: boolean;
  device: string;
  logs: boolean;
  bootNo = 7;
  opened = 0;
  isOpen = false;
  /** (type, seq, payload) of every frame the laptop sent. */
  frames: P.Frame[] = [];
  bad = 0;
  rxIgnored = 0;
  gpsSent = 0;
  markers = 0;
  timeSet: number | null = null;
  /** The drive being recorded (READ_FILE says busy, HL GET/RM says busy). */
  busy: string | null = null;
  /** Stop answering READ_FILE after this many more answers (the cable is pulled). */
  fileAnswersLeft: number | null = null;
  /** Stop answering MAPUP_WRITE after this many more answers. */
  mapupAnswersLeft: number | null = null;
  /** Answer ``HL ERR io`` after that many DATA lines of an upload. */
  hlFailAfter: number | null = null;
  /** Damage the next N MAPUP_WRITE chunks (CRC fails). */
  damageMapup = 0;
  hlCommands: string[] = [];
  mapCommits: string[] = [];
  /** Fail the next ``failWrites`` port writes with a CydError (the cable). */
  failWrites = 0;
  private out: Uint8Array[] = [];
  private seq = 0;
  private t0: number;
  private nextHello = 0;
  private nextGps = 0;
  private deframer = new P.Deframer();
  private split = new H.StreamSplitter();
  private hlOpen = false;
  private hlDeadline = 0;
  private hlSkipData = false;
  private part: Part | null = null;
  private put: { kind: number; size: number; crc: number; data: Uint8Array } | null = null;
  private mapup: { name: string; size: number; crc: number; got: number; data: Uint8Array } | null = null;

  constructor(opts: FakeOptions = {}) {
    this.clock = opts.clock ?? new VirtualClock();
    this.name = opts.name ?? 'COM22';
    this.txOnly = opts.txOnly ?? false;
    this.caps = opts.caps ?? (this.txOnly ? P.CAP_LOG
      : P.CAP_RX | P.CAP_FILES | P.CAP_PUT | P.CAP_LOG | P.CAP_MAPUP | P.CAP_HL);
    this.fw = opts.fw ?? '0.1.0';
    this.mapHash = opts.mapHash ?? '44979f9238b1';
    this.fix = opts.fix ?? true;
    this.sats = opts.sats ?? 9;
    this.helloS = opts.helloS ?? 0.5;
    this.gpsS = opts.gpsS ?? 0.1;
    this.deviceBaud = opts.deviceBaud ?? 921600;
    this.baud = opts.baud ?? this.deviceBaud;
    this.fixedBaud = opts.fixedBaud ?? false;
    this.sd = opts.sd === undefined ? new Map() : opts.sd;
    this.gpsTime = opts.gpsTime ?? true;
    this.device = opts.device ?? 'HH-1373A0';
    this.logs = opts.logs ?? false;
    this.usb = opts.usb;
    this.t0 = this.clock.now();
  }

  // ---- what the laptop sees ----

  hello(): P.Hello {
    const tUs = Math.trunc((this.clock.now() - this.t0) * 1e6);
    return P.makeHello({
      rev: P.REV, variant: this.txOnly ? P.VARIANT_GPS_P1 : P.VARIANT_DEFAULT, caps: this.caps, fw: this.fw,
      device: this.device, mapHash: this.mapHash, mapSha: new Uint8Array(8), ecuId: new Uint8Array(0),
      linkState: P.LINK_ERROR, backend: 0, linkMode: 0, nCh: 40, flags: 1 | 2 | 4 | (this.sd ? 8 : 0) | 16,
      bootNo: this.bootNo, baud: this.deviceBaud, tUs, unixUs: 0, helloMs: 2000, maxPayload: 512,
    });
  }

  private devUs(): number {
    return Math.trunc((this.clock.now() - this.t0) * 1e6);
  }

  frame(ftype: number, payload: Uint8Array): void {
    this.out.push(concat(Uint8Array.of(0), P.frameEncode(ftype, payload, this.seq)));
    this.seq = (this.seq + 1) & 0xff;
  }

  private text(line: string): void {
    this.out.push(new TextEncoder().encode(line + '\n'));
  }

  private gps(): Uint8Array {
    const t = this.clock.now() - this.t0;
    const lat = PEARL[0] + t * 1e-5;
    return F.REC_GPS.pack(Math.trunc(t * 1e6), Math.trunc(lat * 1e7), Math.trunc(PEARL[1] * 1e7),
      Math.trunc(1.7e9 + t), 1000, 9000, this.fix ? 1 : 0, this.sats, 90);
  }

  private tick(): void {
    const now = this.clock.now();
    if (this.hlOpen && now > this.hlDeadline) {
      this.text('HL TIMEOUT');
      this.hlEnd();
    }
    if (this.hlOpen) return; // the binary stream waits while a session is open
    if (now >= this.nextHello) {
      this.nextHello = now + this.helloS;
      this.frame(P.SF_HELLO, P.packHello(this.hello()));
    }
    if (this.gpsS && now >= this.nextGps) {
      this.nextGps = now + this.gpsS;
      this.frame(P.SF_GPS, this.gps());
      this.gpsSent += 1;
    }
  }

  // ---- the port ----

  async open(): Promise<void> {
    this.opened += 1;
    this.isOpen = true;
    this.nextHello = this.nextGps = 0;
  }

  async close(): Promise<void> {
    this.isOpen = false;
  }

  async setBaud(baud: number): Promise<void> {
    this.baud = baud;
  }

  async read(timeoutS: number): Promise<Uint8Array> {
    if (!this.isOpen) throw new CydError('not open');
    await macrotask(); // a reading loop must never starve the test (or anything else) of a turn
    if (!this.isOpen) throw new CydError('not open');
    const end = this.clock.now() + timeoutS;
    for (;;) {
      this.tick();
      if (this.out.length) {
        const data = concat(...this.out);
        this.out = [];
        if (this.baud !== this.deviceBaud) return data.map((b) => (b * 37 + 11) | 1); // noise, never a 0x00
        return data;
      }
      if (this.clock.now() >= end) return new Uint8Array(0);
      // step to ``end`` exactly at the last step: adding a remainder can fall short by a rounding error forever
      this.clock.t = end - this.clock.now() <= 0.005 ? end : this.clock.now() + 0.005;
    }
  }

  async write(data: Uint8Array): Promise<void> {
    if (!this.isOpen) throw new CydError('not open');
    if (this.failWrites > 0) {
      this.failWrites -= 1;
      throw new CydError('write failed');
    }
    if (this.txOnly || this.baud !== this.deviceBaud) {
      this.rxIgnored += data.length;
      return;
    }
    const before = this.deframer.bad;
    for (const piece of this.split.feed(data)) {
      if (piece.kind === 'frame') {
        this.frames.push(piece.frame);
        if (!this.hlOpen) this.command(...piece.frame);
      } else if (piece.text.startsWith('HL')) {
        this.hlLine(piece.text);
      }
    }
    this.bad += this.deframer.bad - before;
  }

  sent(ftype: number): Uint8Array[] {
    return this.frames.filter((f) => f[0] === ftype).map((f) => f[2]);
  }

  // ---- binary commands (cydsim._command) ----

  private ack(ftype: number, seq: number, status = P.OK, value = 0): void {
    this.frame(P.SF_ACK, P.ACK.pack(ftype, seq, status, value));
  }

  private command(ftype: number, seq: number, p: Uint8Array): void {
    if (ftype >= P.SR_RELAY_READ && ftype <= P.SR_RELAY_LINK) return; // kept in ``frames``
    if (ftype === P.SC_GET_HELLO) this.frame(P.SF_HELLO, P.packHello(this.hello()));
    else if (ftype === P.SC_TIME_REQ && p.length >= 8) {
      const hostTx = P.TIME_REQ.unpack(p)[0];
      const dev = this.devUs();
      this.frame(P.SF_TIME_RSP, P.TIME_RSP.pack(BigInt(hostTx), dev, dev));
    } else if (ftype === P.SC_TIME_SET && p.length >= 8) {
      if (this.gpsTime) this.ack(ftype, seq, P.IGNORED);
      else {
        this.timeSet = P.TIME_REQ.unpack(p)[0] - this.devUs();
        this.ack(ftype, seq);
      }
    } else if (ftype === P.SC_MARK) {
      this.markers += 1;
      this.ack(ftype, seq);
    } else if (ftype === P.SC_ACK_ALARM || ftype === P.SC_DTC) this.ack(ftype, seq);
    else if (ftype === P.SC_DRIVE && p.length >= 1) {
      if (!p[0]) this.busy = null;
      this.ack(ftype, seq, P.OK, p[0] ? 1 : 0);
    } else if (ftype === P.SC_LIST_FILES && p.length >= P.LIST.size) {
      const [kind, after] = P.LIST.unpack(p);
      this.list(seq, kind, after);
    } else if (ftype === P.SC_READ_FILE && p.length >= P.READ_FILE.size) {
      const [kind, want, no, offset] = P.READ_FILE.unpack(p);
      this.readFile(seq, kind, want, no, offset);
    } else if (ftype === P.SC_PUT_BEGIN || ftype === P.SC_PUT_DATA || ftype === P.SC_PUT_END) this.putCmd(ftype, seq, p);
    else if (ftype >= P.SC_MAPUP_OPEN && ftype <= P.SC_MAPUP_COMMIT) this.mapupCmd(ftype, seq, p);
    else this.ack(ftype, seq, P.E_UNSUPPORTED);
  }

  private path(kind: number, no: number): string | null {
    if (kind === P.F_MAP) return 'HANDHELD.MAP';
    if (kind === P.F_CFG) return 'HANDHELD.CFG';
    if (kind === P.F_DRIVE) return `DRIVES/D${String(no).padStart(6, '0')}.HHD`;
    if (kind === P.F_CAPTURE) return `CAPTURES/C${String(no).padStart(6, '0')}.HHD`;
    return null;
  }

  private list(seq: number, kind: number, after: number): void {
    if (this.sd === null) return this.ack(P.SC_LIST_FILES, seq, P.E_UNSUPPORTED);
    const entries: Array<[number, number]> = [];
    if (kind === P.F_MAP || kind === P.F_CFG) {
      const f = this.sd.get(this.path(kind, 0)!);
      if (after < 1 && f) entries.push([1, f.length]);
    } else if (kind === P.F_DRIVE || kind === P.F_CAPTURE) {
      const re = kind === P.F_DRIVE ? /^DRIVES\/D(\d+)\.HHD$/ : /^CAPTURES\/C(\d+)\.HHD$/;
      for (const [k, v] of this.sd) {
        const m = re.exec(k);
        if (m && parseInt(m[1]!, 10) > after) entries.push([parseInt(m[1]!, 10), v.length]);
      }
    } else return this.ack(P.SC_LIST_FILES, seq, P.E_ARG);
    entries.sort((a, b) => a[0] - b[0]);
    const page = entries.slice(0, 24);
    for (const [no, size] of page) {
      this.frame(P.SF_FILE_INFO, P.FILE_INFO.pack(kind, this.path(kind, no) === this.busy ? P.FI_OPEN : 0, 0, no, size));
    }
    this.frame(P.SF_FILE_INFO, P.FILE_INFO.pack(kind, entries.length > page.length ? P.FI_MORE : 0, 0, P.NO_FILE, page.length));
  }

  private readFile(seq: number, kind: number, want: number, no: number, offset: number): void {
    if (this.sd === null) return this.ack(P.SC_READ_FILE, seq, P.E_UNSUPPORTED);
    if (this.fileAnswersLeft !== null) {
      if (this.fileAnswersLeft <= 0) return; // the cable is out: no answer
      this.fileAnswersLeft -= 1;
    }
    const path = this.path(kind, no);
    const data = path ? this.sd.get(path) : undefined;
    if (!data || path === this.busy) {
      this.frame(P.SF_FILE_DATA, P.FILE_DATA.pack(kind, path !== null && path === this.busy ? P.FD_BUSY : 0, 0, no, P.NO_FILE, 0));
      return;
    }
    const chunk = data.subarray(offset, offset + Math.min(want, P.CHUNK));
    const eof = offset + chunk.length >= data.length ? P.FD_EOF : 0;
    this.frame(P.SF_FILE_DATA, concat(P.FILE_DATA.pack(kind, eof, chunk.length, no, offset, crc32(chunk)), chunk));
  }

  private putCmd(ftype: number, seq: number, p: Uint8Array): void {
    if (this.sd === null) return this.ack(ftype, seq, P.E_UNSUPPORTED);
    const put = this.put;
    if (ftype === P.SC_PUT_BEGIN) {
      if (p.length < P.PUT_BEGIN.size) return this.ack(ftype, seq, P.E_ARG);
      const [kind, size, crc] = P.PUT_BEGIN.unpack(p);
      if ((kind !== P.F_MAP && kind !== P.F_CFG) || !size || size > (kind === P.F_MAP ? 256 << 10 : 8 << 10)) {
        return this.ack(ftype, seq, P.E_ARG);
      }
      if (put && put.kind === kind && put.size === size && put.crc === crc) return this.ack(ftype, seq, P.OK, put.data.length);
      this.put = { kind, size, crc, data: new Uint8Array(0) };
      this.ack(ftype, seq);
    } else if (ftype === P.SC_PUT_DATA) {
      if (put === null || p.length < 4) return this.ack(ftype, seq, put === null ? P.E_STATE : P.E_ARG);
      const offset = P.U32.unpack(p)[0];
      const data = p.subarray(4);
      const got = put.data.length;
      if (offset + data.length <= got) this.ack(ftype, seq, P.OK, got);
      else if (offset !== got) this.ack(ftype, seq, P.E_OFFSET, got);
      else if (got + data.length > put.size) this.ack(ftype, seq, P.E_ARG, got);
      else {
        put.data = concat(put.data, data);
        this.ack(ftype, seq, P.OK, put.data.length);
      }
    } else {
      if (put === null || put.data.length !== put.size) return this.ack(ftype, seq, P.E_STATE, put ? put.data.length : 0);
      this.put = null;
      if (crc32(put.data) !== put.crc) return this.ack(ftype, seq, P.E_CRC);
      let value = 0;
      const final = this.path(put.kind, 0)!;
      if (put.kind === P.F_MAP) {
        try {
          readMap(put.data);
        } catch {
          return this.ack(ftype, seq, P.E_REFUSED, 1);
        }
      } else value = skippedCfgLines(put.data)[1];
      const old = this.sd.get(final);
      if (old) this.sd.set(final.replace(/\.(MAP|CFG)$/, put.kind === P.F_MAP ? '.MBK' : '.CBK'), old);
      this.sd.set(final, put.data);
      this.ack(ftype, seq, P.OK, value);
    }
  }

  private mapupCmd(ftype: number, seq: number, p: Uint8Array): void {
    if (this.sd === null) return this.ack(ftype, seq, P.E_UNSUPPORTED);
    const up = this.mapup;
    if (ftype === P.SC_MAPUP_OPEN) {
      if (p.length < P.MAPUP_OPEN.size) return this.ack(ftype, seq, P.E_ARG);
      const [, nameLen, size, crc] = P.MAPUP_OPEN.unpack(p);
      const raw = p.subarray(P.MAPUP_OPEN.size);
      const name = new TextDecoder().decode(raw);
      if (raw.length !== nameLen || !P.mapupNameOk(name) || !(size > 0 && size <= P.MAPUP_SIZE_MAX)) return this.ack(ftype, seq, P.E_REFUSED);
      if (up && up.name === name && up.size === size && up.crc === crc) return this.ack(ftype, seq, P.OK, up.got);
      this.mapup = { name, size, crc, got: 0, data: new Uint8Array(0) };
      this.ack(ftype, seq);
    } else if (ftype === P.SC_MAPUP_WRITE) {
      if (p.length < P.MAPUP_WRITE.size + 1) return this.ack(ftype, seq, P.E_ARG);
      if (up === null) return this.ack(ftype, seq, P.E_STATE);
      if (this.mapupAnswersLeft !== null) {
        if (this.mapupAnswersLeft <= 0) return;
        this.mapupAnswersLeft -= 1;
      }
      const [offset, crc] = P.MAPUP_WRITE.unpack(p);
      const data = p.subarray(P.MAPUP_WRITE.size);
      const got = up.got;
      const damaged = this.damageMapup > 0;
      if (damaged) this.damageMapup -= 1;
      if (damaged || crc32(data) !== crc) this.ack(ftype, seq, P.E_CRC, got);
      else if (offset + data.length <= got) this.ack(ftype, seq, P.OK, got);
      else if (offset !== got) this.ack(ftype, seq, P.E_OFFSET, got);
      else if (got + data.length > up.size) this.ack(ftype, seq, P.E_ARG, got);
      else {
        up.data = concat(up.data, data);
        up.got = got + data.length;
        this.ack(ftype, seq, P.OK, up.got);
      }
    } else if (ftype === P.SC_MAPUP_STATUS) {
      this.frame(P.SF_MAPUP, up ? P.MAPUP_INFO.pack(P.MU_OPEN, up.got, up.size, up.crc, fit(up.name, 16))
        : P.MAPUP_INFO.pack(P.MU_NONE, 0, 0, 0, new Uint8Array(16)));
      this.ack(ftype, seq, up ? P.OK : P.E_STATE, up ? up.got : 0);
    } else {
      const flags = p[0] ?? 0;
      if (flags & P.MAPUP_ABORT) {
        this.mapup = null;
        return this.ack(ftype, seq);
      }
      if (up === null) return this.ack(ftype, seq, P.E_STATE);
      if (up.got !== up.size) return this.ack(ftype, seq, P.E_STATE, up.got);
      this.mapup = null;
      if (up.data.length !== up.size || crc32(up.data) !== up.crc) return this.ack(ftype, seq, P.E_CRC, 0);
      this.sd.set(up.name, up.data);
      this.mapCommits.push(up.name);
      this.ack(ftype, seq, P.OK, up.size);
    }
  }

  // ---- the HL session (cydsim_hl) ----

  private hlEnd(): void {
    this.hlOpen = false;
    this.hlSkipData = false;
    this.out.push(Uint8Array.of(0));
    this.nextHello = this.clock.now();
  }

  private hlLine(text: string): void {
    const body = text.slice(2).trim();
    const sp = body.indexOf(' ');
    const cmd = (sp < 0 ? body : body.slice(0, sp)).toUpperCase();
    const rest = sp < 0 ? '' : body.slice(sp + 1);
    if (cmd === 'DATA') return this.hlData(rest);
    this.hlCommands.push(cmd);
    if (this.hlOpen && cmd !== 'STAT') this.hlDeadline = this.clock.now() + H.TIMEOUT_S;
    this.hlSkipData = false;
    if (cmd === 'HELLO') {
      if (!this.hlOpen) {
        this.hlOpen = true;
        this.out.push(Uint8Array.of(0));
      }
      this.hlDeadline = this.clock.now() + H.TIMEOUT_S;
      this.text(`HL HELLO fw=${this.fw} model=${H.MODEL} proto=${H.PROTO} id=${this.device} map=${this.mapHash} ` +
        `variant=${this.txOnly ? 'gps-p1' : 'default'} chunk=${H.CHUNK} win=${H.WINDOW}`);
      if (this.logs) this.text('I (100) hhserial: session open');
      return;
    }
    if (!this.hlOpen) return this.text('HL ERR nosession');
    if (this.sd === null && ['LS', 'GET', 'PUT', 'STATUS', 'CFG', 'RM'].includes(cmd)) return this.text('HL ERR nosd');
    const arg = rest.trim();
    switch (cmd) {
      case 'BYE': this.text('HL OK bye'); this.hlEnd(); return;
      case 'MARK': this.markers += 1; this.text('HL OK'); return;
      case 'TIME': {
        const ms = /^-?\d+$/.test(arg) ? parseInt(arg, 10) : NaN;
        if (!(ms > 0)) this.text('HL ERR args');
        else if (this.gpsTime) this.text('HL OK ignored');
        else {
          this.timeSet = ms * 1000 - this.devUs();
          this.text('HL OK');
        }
        return;
      }
      case 'REBOOT': this.text('HL OK reboot'); this.part = null; this.bootNo += 1; this.hlEnd(); return;
      case 'STAT':
        this.text('HL STAT ' + JSON.stringify({ t_ms: Math.trunc((this.clock.now() - this.t0) * 1000), fw: this.fw,
          boot: this.bootNo, drive: { markers: this.markers }, sd: { ok: this.sd ? 1 : 0 } }));
        return;
      case 'LS': return this.hlLs(arg || '/');
      case 'GET': return this.hlGet(arg);
      case 'CFG': {
        const [sub = '', ...a] = arg.split(' ');
        if (sub.toUpperCase() === 'GET') return this.hlStartGet('HANDHELD.CFG');
        if (sub.toUpperCase() === 'PUT') return this.hlPut('/HANDHELD.CFG', a.join(' '));
        return this.text('HL ERR args');
      }
      case 'RM': return this.hlRm(arg);
      case 'STATUS': {
        const parts = H.splitPath(arg);
        if (parts === null) this.text('HL ERR path');
        else if (this.part && this.part.parts.join('/') === parts.join('/')) {
          this.text(`HL PART ${this.part.held} ${this.part.n} ${hex8(this.part.crc)}`);
        } else this.text('HL PART 0 0 00000000');
        return;
      }
      case 'PUT': {
        const i = rest.indexOf(' ');
        return this.hlPut(i < 0 ? rest : rest.slice(0, i), i < 0 ? '' : rest.slice(i + 1));
      }
      default: this.text('HL ERR badcmd');
    }
  }

  private hlLs(path: string): void {
    const parts = H.splitPath(path);
    if (parts === null) return this.text('HL ERR path');
    const prefix = parts.length ? parts.join('/') + '/' : '';
    const files: Array<[string, number]> = [];
    const dirs = new Set<string>();
    for (const [k, v] of this.sd!) {
      if (!k.startsWith(prefix)) continue;
      const rest = k.slice(prefix.length);
      const slash = rest.indexOf('/');
      if (slash < 0) files.push([rest, v.length]);
      else dirs.add(rest.slice(0, slash));
    }
    if (parts.length && !files.length && !dirs.size) return this.text('HL ERR nofile');
    const base = parts.length ? '/' + parts.join('/') : '';
    const names = [...[...dirs].map((d) => [d, -1] as [string, number]), ...files].sort((a, b) => (a[0] < b[0] ? -1 : 1));
    for (const [n, size] of names) {
      if (size < 0) this.text(`HL D ${base}/${n}`);
      else this.text(`HL F ${size} 1700000000 ${base}/${n}`);
    }
    this.text('HL END');
  }

  private hlGet(path: string): void {
    const parts = H.splitPath(path);
    if (parts === null) return this.text('HL ERR path');
    this.hlStartGet(parts.join('/'));
  }

  private hlStartGet(key: string): void {
    const data = this.sd!.get(key);
    if (!data) return this.text('HL ERR nofile');
    if (key === this.busy) return this.text('HL ERR busy');
    this.text(`HL BEGIN ${data.length}`);
    for (let i = 0; i < data.length; i += H.CHUNK) this.text('HL DATA ' + b64(data.subarray(i, i + H.CHUNK)));
    this.text(`HL END ${hex8(crc32(data))}`);
  }

  private hlRm(path: string): void {
    const parts = H.splitPath(path);
    if (parts === null) return this.text('HL ERR path');
    const key = parts.join('/');
    if (!H.canRemove(parts)) this.text('HL ERR denied');
    else if (!this.sd!.has(key)) this.text('HL ERR nofile');
    else if (key === this.busy) this.text('HL ERR busy');
    else {
      this.sd!.delete(key);
      this.text('HL OK');
    }
  }

  private hlPut(path: string, args: string): void {
    const parts = H.splitPath(path);
    if (parts === null) return this.text('HL ERR path');
    if (!H.canWrite(parts)) return this.text('HL ERR denied');
    const f = args.split(/\s+/).filter(Boolean);
    const n = /^\d+$/.test(f[0] ?? '') ? parseInt(f[0]!, 10) : NaN;
    const crc = /^[0-9a-fA-F]+$/.test(f[1] ?? '') ? parseInt(f[1]!, 16) : NaN;
    const offset = f.length > 2 ? (/^\d+$/.test(f[2]!) ? parseInt(f[2]!, 10) : NaN) : 0;
    if (Number.isNaN(n) || Number.isNaN(crc) || Number.isNaN(offset) || (f.length !== 2 && f.length !== 3)) return this.text('HL ERR args');
    if (n > H.maxSize(parts)) return this.text('HL ERR size');
    const part = this.part;
    if (part && part.parts.join('/') === parts.join('/') && part.n === n && part.crc === crc && offset > 0 && offset <= part.held) {
      part.data = part.data.slice(0, offset);
      part.held = offset;
    } else {
      this.part = { parts, n, crc, held: 0, data: new Uint8Array(0), lines: 0 };
    }
    this.hlSkipData = false;
    this.text(`HL READY ${this.part!.held}`);
  }

  private hlData(rest: string): void {
    const part = this.part;
    if (this.hlOpen) this.hlDeadline = this.clock.now() + H.TIMEOUT_S;
    if (!this.hlOpen || part === null || this.hlSkipData) {
      if (!this.hlSkipData) {
        this.text(this.hlOpen ? 'HL ERR state' : 'HL ERR nosession');
        this.hlSkipData = true;
      }
      return;
    }
    let at: number | null = null;
    if (rest.startsWith('@')) {
      const i = rest.indexOf(' ');
      const head = i < 0 ? rest : rest.slice(0, i);
      rest = i < 0 ? '' : rest.slice(i + 1);
      const v = parseInt(head.slice(1), 10);
      if (!Number.isNaN(v)) at = v;
    }
    const data = unb64(rest.trim());
    part.lines += 1;
    const fail = (reason: string) => {
      this.hlSkipData = true;
      this.text('HL ERR ' + reason);
    };
    if (data === null) return fail('b64');
    if (this.hlFailAfter !== null && part.lines > this.hlFailAfter) {
      this.hlFailAfter = null;
      return fail('io');
    }
    if (at !== null && at !== part.held) return fail('offset');
    if (part.held + data.length > part.n) return fail('size');
    part.data = concat(part.data, data);
    part.held += data.length;
    this.text(`HL ACK ${part.held}`);
    if (part.held === part.n) this.hlFinish(part);
  }

  private hlFinish(part: Part): void {
    if (crc32(part.data) !== part.crc) {
      this.part = null;
      return this.text('HL ERR crc');
    }
    let info = '';
    const key = part.parts.join('/');
    if (part.parts.length === 1) {
      if (part.parts[0] === 'HANDHELD.MAP') {
        try {
          readMap(part.data);
        } catch {
          this.part = null;
          return this.text('HL ERR refused');
        }
      } else {
        const [lines, skipped] = skippedCfgLines(part.data);
        info = ` lines=${lines} skipped=${skipped}`;
      }
      const old = this.sd!.get(key);
      if (old) this.sd!.set(key.replace(/\.(MAP|CFG)$/, part.parts[0] === 'HANDHELD.MAP' ? '.MBK' : '.CBK'), old);
    }
    this.sd!.set(key, part.data);
    this.part = null;
    this.text('HL OK' + info);
  }
}

/** [lines, lines with a key the firmware does not know] of a HANDHELD.CFG. */
export function skippedCfgLines(data: Uint8Array): [number, number] {
  const keys = new Set(DEFAULT_CFG.split('\n').filter((l) => l.includes('=') && !l.startsWith('#'))
    .map((l) => (l.split('=')[0] ?? '').trim()));
  let lines = 0, skipped = 0;
  for (const raw of new TextDecoder().decode(data).split(/\r?\n/)) {
    const line = (raw.split('#')[0] ?? '').trim();
    if (!line) continue;
    lines += 1;
    if (!keys.has((line.split('=')[0] ?? '').trim())) skipped += 1;
  }
  return [lines, skipped];
}

const macrotask = (): Promise<void> => new Promise((resolve) => {
  const g = globalThis as { setImmediate?: (fn: () => void) => void };
  if (g.setImmediate) g.setImmediate(resolve);
  else setTimeout(resolve, 0);
});

/** Wait (yielding turns) until ``cond`` holds; the fake's reads move the virtual clock. */
export async function until(cond: () => boolean, clock: VirtualClock, maxS = 30): Promise<void> {
  const end = clock.now() + maxS;
  let spins = 0;
  while (!cond()) {
    if (clock.now() > end || ++spins > 200_000) throw new Error('until: timed out');
    await macrotask();
  }
}
