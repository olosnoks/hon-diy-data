/**
 * The handheld's live stream (hondalog/handheld/serial_link.py ``CydPoller``): one async
 * loop that owns the port, hunts the baud rate, keeps the clock exchange and the "PC"
 * heartbeat going, counts lost and damaged frames, and hands out what arrives:
 * hello, raw ECU reads (READ frames: the ECU's bytes as the handheld read them), ticks,
 * link changes, OBD2 replies, GPS records, events, STATS and log lines.
 *
 * Decoding the raw reads into channel values is left to the caller (the expression
 * engine, src/core/expr/): ``onRead`` gives the block as read, on the session's time
 * axis (``t0``). ``HandheldCompanion`` (companion.ts) is this link next to the laptop's
 * own KKL cable, for the handheld's GPS.
 *
 * Nothing is written until a HELLO has said the handheld listens.
 */

import { type Clock, CydError, type CydPort, realClock } from './port';
import { ClockSync, HUNT_S, OneWayClock, Opener, SILENT_S } from './link';
import * as F from './format';
import * as P from './serial_proto';

export type LinkStateName = 'connecting' | 'ok' | 'no_reply' | 'port_lost' | 'stopped';

export interface CydEvent extends P.EventRecord {
  /** Session seconds (on t0). */
  t: number;
  name: string;
  text: string;
  isStall: boolean;
}

export interface LinkHooks {
  onHello?: (hello: P.Hello) => void;
  /** A READ frame: session seconds of the reply's end, laptop-monotonic seconds, address, bytes, latency s. */
  onRead?: (t: number, mono: number, addr: number, data: Uint8Array, latencyS: number) => void;
  onTick?: (t: number, latencyS: number, hz: number) => void;
  onLink?: (up: boolean, state: number) => void;
  onIso?: (t: number, mode: number, pid: number, data: Uint8Array) => void;
  onGps?: (t: number, fix: P.GpsRecord) => void;
  onEvent?: (ev: CydEvent) => void;
  onLog?: (text: string) => void;
  onStats?: (stats: P.Stats) => void;
}

export interface CydLinkOptions extends LinkHooks {
  /** The laptop map's version hash (map mismatch warning). */
  mapHash?: string;
  t0?: number;
  clock?: Clock;
}

export class CydLink {
  port: CydPort | null;
  readonly clock: Clock;
  hooks: LinkHooks;
  mapHash: string;
  t0: number;
  state: LinkStateName = 'connecting';
  mode: 'honda' | 'obd2' = 'honda';
  reads = 0;
  errors = 0;
  sweeps = 0;
  hz = 0;
  lastError = '';
  ecuId: Uint8Array | null = null;
  hello: P.Hello | null = null;
  /** The last STATS frame. */
  device: P.Stats = {};
  lost = 0;
  frames = 0;
  bytesIn = 0;
  bytesPerS = 0;
  mapMismatch = false;
  warning = '';
  clockSync = new ClockSync();
  logLines: string[] = [];
  protected oneWay = new OneWayClock();
  protected deframer = new P.Deframer();
  protected opener: Opener | null = null;
  protected tOpen = 0;
  private stopFlag = false;
  private seqOut = 0;
  private seqIn: number | null = null;
  private lastGood = 0;
  private nextReport = 0;
  private nextBeat = 0;
  private rateMark: [number, number] = [0, 0];
  private ecuUp: boolean | null = null;
  private lastT = 0;
  private writeChain: Promise<void> = Promise.resolve();
  private readyResolve: (() => void) | null = null;
  private loop: Promise<void> | null = null;
  /** Resolves once the handheld said hello (or did not, for HUNT_S). */
  ready: Promise<void>;
  readyDone = false;

  constructor(port: CydPort | null, opts: CydLinkOptions = {}) {
    this.port = port;
    this.clock = opts.clock ?? realClock;
    this.hooks = opts;
    this.mapHash = opts.mapHash ?? '';
    this.t0 = opts.t0 ?? this.clock.now();
    this.ready = new Promise((resolve) => { this.readyResolve = resolve; });
  }

  get damaged(): number { return this.deframer.bad; }
  get twoWay(): boolean { return this.hello !== null && P.twoWay(this.hello); }

  protected setReady(): void {
    if (!this.readyDone) {
      this.readyDone = true;
      this.readyResolve?.();
    }
  }

  start(): this {
    if (!this.loop) this.loop = this.run();
    return this;
  }

  async stop(): Promise<void> {
    this.stopFlag = true;
    await this.loop;
  }

  get stopped(): boolean { return this.stopFlag; }

  // -- commands (two-way firmware) --

  /** One frame to the handheld. Refused until a hello has said it listens. */
  async send(ftype: number, payload: Uint8Array = new Uint8Array(0)): Promise<void> {
    if (!this.twoWay) {
      throw new CydError(this.hello !== null ? 'the handheld is tx-only (GPS on P1)' : 'no hello from the handheld yet');
    }
    const job = this.writeChain.then(async () => {
      if (this.state === 'port_lost' || this.state === 'stopped' || this.port === null) {
        throw new CydError(this.lastError || 'not connected');
      }
      const seq = this.seqOut;
      this.seqOut = (this.seqOut + 1) & 0xff;
      await this.port.write(P.frameEncode(ftype, payload, seq));
    });
    this.writeChain = job.catch(() => undefined);
    return job;
  }

  mark(): Promise<void> { return this.send(P.SC_MARK); }
  ackAlarm(): Promise<void> { return this.send(P.SC_ACK_ALARM); }
  /** The handheld's own ISO 9141-2 Mode 03 read (about 8 s without live data); codes arrive as events. */
  requestDtc(): Promise<void> { return this.send(P.SC_DTC); }
  /** Stop (and hold) or start the handheld's own drive file. */
  drive(on: boolean): Promise<void> { return this.send(P.SC_DRIVE, Uint8Array.of(on ? 1 : 0)); }

  // -- the loop --

  private async run(): Promise<void> {
    try {
      await this.open();
      while (!this.stopFlag) {
        if (this.state === 'port_lost') {
          await this.clock.sleep(0.5);
          if (this.stopFlag) break;
          await this.open();
          continue;
        }
        try {
          const data = await this.port!.read(0.05);
          const now = this.clock.now();
          if (data.length) {
            this.bytesIn += data.length;
            const before = this.deframer.bad;
            for (const [ftype, seq, payload] of this.deframer.feed(data)) this.frame(ftype, seq, payload, now);
            this.errors += this.deframer.bad - before;
          }
          await this.housekeeping(now);
        } catch (e) {
          if (e instanceof CydError) this.portLost(e);
          else throw e;
        }
      }
    } finally {
      this.setReady();
      this.state = 'stopped';
      if (this.port !== null) await this.port.close();
    }
  }

  /** The port to use (``auto`` looks for one: companion). null = none now. */
  protected async acquirePort(): Promise<CydPort | null> {
    return this.port;
  }

  protected async open(): Promise<void> {
    this.tOpen = this.clock.now();
    try {
      const port = await this.acquirePort();
      if (port === null) {
        if (this.state !== 'port_lost') this.state = 'port_lost';
        this.setReady();
        return;
      }
      this.port = port;
      this.opener = new Opener(port, this.clock);
      await this.opener.open();
    } catch (e) {
      if (!(e instanceof CydError)) throw e;
      this.portLost(e);
      this.setReady();
      return;
    }
    this.state = 'connecting';
    this.hello = null;
    this.seqIn = null;
    this.deframer = new P.Deframer();
    this.oneWay.reset();
    this.clockSync = new ClockSync();
    this.lastGood = this.tOpen;
    this.ecuUp = null;
  }

  protected portLost(e: Error): void {
    this.errors += 1;
    this.lastError = `port: ${e.message}`;
    this.state = 'port_lost';
    this.hz = 0;
    void this.port?.close();
  }

  private async housekeeping(now: number): Promise<void> {
    const opener = this.opener;
    if (this.twoWay && now >= this.nextBeat) {
      // the heartbeat that shows "PC" on the handheld, and the clock exchange
      this.nextBeat = now + (this.clockSync.count < 5 ? 0.2 : 1.0);
      await this.send(P.SC_TIME_REQ, P.TIME_REQ.pack(BigInt(Math.trunc(now * 1e6))));
    }
    if (opener !== null && !opener.locked) {
      if (await opener.hunt(now)) {
        this.deframer = new P.Deframer();
        this.lastError = `nothing readable: trying ${this.port!.baud} baud`;
      }
      if (now - this.tOpen > HUNT_S && !this.readyDone) {
        this.state = 'no_reply';
        if (!this.lastError) this.lastError = 'no data from the handheld (is the link on? HANDHELD.CFG serial = 1)';
        this.setReady();
      }
    }
    if (now - this.lastGood > SILENT_S && (this.state === 'ok' || this.state === 'connecting') && this.frames) {
      this.state = 'no_reply';
      this.hz = 0;
      this.lastError = `no data from the handheld for ${SILENT_S.toFixed(1)} s`;
    }
    if (now >= this.nextReport) {
      this.nextReport = now + 0.5;
      const [tPrev, bytesPrev] = this.rateMark;
      if (tPrev && now > tPrev) {
        const inst = (this.bytesIn - bytesPrev) / (now - tPrev);
        this.bytesPerS = this.bytesPerS === 0 ? inst : 0.7 * this.bytesPerS + 0.3 * inst;
      }
      this.rateMark = [now, this.bytesIn];
    }
  }

  // -- time --

  /** Laptop monotonic seconds of a device timestamp. */
  host(devUs: number, now: number): number {
    let host: number;
    if (this.clockSync.valid) host = this.clockSync.toHostUs(devUs) / 1e6;
    else if (this.oneWay.offset !== null) host = devUs / 1e6 + this.oneWay.offset;
    else host = now;
    return Math.min(host, now);
  }

  session(devUs: number, now: number): number {
    return this.host(devUs, now) - this.t0;
  }

  // -- frames --

  protected frame(ftype: number, seq: number, p: Uint8Array, now: number): void {
    this.frames += 1;
    this.lastGood = now;
    this.opener?.good();
    if (this.seqIn !== null) {
      const missing = (seq - this.seqIn - 1) & 0xff;
      if (missing) {
        this.lost += missing;
        this.errors += missing;
      }
    }
    this.seqIn = seq;
    const h = this.hooks;
    if (ftype === P.SF_READ && p.length >= P.READ.size) {
      const [tUs, addr, length, latUs] = P.READ.unpack(p);
      const data = p.slice(P.READ.size, P.READ.size + length);
      if (data.length !== length) {
        this.errors += 1;
        return;
      }
      if (!this.clockSync.valid) this.oneWay.add(now, tUs + latUs);
      const mono = this.host(tUs + latUs, now);
      const t = Math.max(mono - this.t0, this.lastT);
      this.lastT = t;
      this.reads += 1;
      if (this.ecuUp !== true) this.link(true, P.LINK_OK);
      this.call(() => h.onRead?.(t, mono, addr, data, latUs / 1e6));
    } else if (ftype === P.SF_TICK && p.length >= P.TICK.size) {
      const [tUs, latUs, hzC] = P.TICK.unpack(p);
      this.oneWay.add(now, tUs);
      this.hz = hzC / 100;
      if (this.ecuUp === true) {
        const t = Math.max(this.host(tUs, now) - this.t0, this.lastT);
        this.sweeps += 1;
        this.call(() => h.onTick?.(t, latUs / 1e6, this.hz));
      }
    } else if (ftype === P.SF_LINK && p.length >= P.LINK.size) {
      const [tUs, state, up, , iso] = P.LINK.unpack(p);
      this.oneWay.add(now, tUs);
      this.mode = iso ? 'obd2' : 'honda';
      this.link(Boolean(up), state);
    } else if (ftype === P.SF_HELLO) {
      const hello = P.parseHello(p);
      if (hello !== null) this.onHello(hello, now);
    } else if (ftype === P.SF_STATS) {
      const stats = P.parseStats(p);
      if (stats !== null) {
        this.oneWay.add(now, stats.t_us ?? 0);
        this.device = stats;
        this.call(() => h.onStats?.(stats));
      }
    } else if (ftype === P.SF_GPS) {
      const fix = P.parseGps(p);
      if (fix !== null) this.gps(this.session(fix.t_us, now), fix);
    } else if (ftype === P.SF_EVENT) {
      const ev = P.parseEvent(p);
      if (ev !== null) {
        const full: CydEvent = { ...ev, t: this.session(ev.tUs, now), name: F.EVENT_NAMES[ev.code] ?? `event_${ev.code}`,
          text: P.eventText(ev), isStall: ev.code === F.EV_STALL };
        this.call(() => h.onEvent?.(full));
      }
    } else if (ftype === P.SF_ISO && p.length >= P.ISO.size) {
      const [tUs, mode, pid, length] = P.ISO.unpack(p);
      const data = p.slice(P.ISO.size, P.ISO.size + length);
      this.reads += 1;
      this.mode = 'obd2';
      if (mode === 0x41 && this.ecuUp !== true) this.link(true, P.LINK_ISO_ONLY);
      this.call(() => h.onIso?.(this.session(tUs, now), mode, pid, data));
    } else if (ftype === P.SF_TIME_RSP && p.length >= P.TIME_RSP.size) {
      const [hostTx, devRx, devTx] = P.TIME_RSP.unpack(p);
      this.clockSync.add(hostTx, devRx, devTx, Math.trunc(now * 1e6));
    } else if (ftype === P.SF_LOG) {
      const text = new TextDecoder().decode(p).replace(/\s+$/, '');
      this.logLines.push(text);
      if (this.logLines.length > 200) this.logLines.shift();
      this.call(() => h.onLog?.(text));
    }
  }

  /** GPS records go here (the companion overrides it). */
  protected gps(t: number, fix: P.GpsRecord): void {
    this.call(() => this.hooks.onGps?.(t, fix));
  }

  private call(fn: () => void): void {
    try {
      fn();
    } catch (e) { // a listener's problem must not stop the stream
      this.lastError = `callback: ${String(e)}`;
    }
  }

  private onHello(hello: P.Hello, now: number): void {
    const first = this.hello === null;
    const restarted = !first && this.hello !== null && (hello.bootNo !== this.hello.bootNo || hello.tUs < this.hello.tUs);
    if (restarted) { // the handheld restarted: its clock starts over
      this.oneWay.reset();
      this.clockSync = new ClockSync();
    }
    this.hello = hello;
    this.oneWay.add(now, hello.tUs);
    if (hello.ecuId.length) this.ecuId = hello.ecuId;
    if (first || restarted) {
      this.mapMismatch = Boolean(this.mapHash) && hello.mapHash !== this.mapHash;
      this.warning = '';
      if (this.mapMismatch) {
        this.warning = `the handheld runs address map #${hello.mapHash}, yours is #${this.mapHash}: its raw reads are ` +
          'decoded with yours. Channels its plan does not read stay empty (push the map)';
      }
      if (hello.rev !== P.REV) {
        this.warning = (this.warning ? this.warning + ' ' : '') +
          `the handheld speaks link revision ${hello.rev}, this program ${P.REV}`;
      }
      this.nextBeat = now;
    }
    if (this.state === 'connecting' && hello.linkState !== P.LINK_OK && hello.linkState !== P.LINK_ISO_ONLY) {
      this.link(false, hello.linkState);
    }
    this.call(() => this.hooks.onHello?.(hello));
    this.setReady();
  }

  private link(up: boolean, state: number): void {
    if (up) {
      if (this.ecuUp !== true) {
        this.ecuUp = true;
        this.lastError = '';
      }
      this.state = 'ok';
    } else {
      this.ecuUp = false;
      this.hz = 0;
      this.state = state !== P.LINK_CONNECTING || this.state !== 'connecting' ? 'no_reply' : 'connecting';
      this.lastError = `handheld: ${P.LINK_NAMES[state] ?? 'ECU link down'}`;
    }
    this.call(() => this.hooks.onLink?.(up, state));
  }

  /** One line for the connection menu and the status bar. */
  linkText(): string {
    const h = this.hello;
    if (h === null) return '';
    const d = this.device;
    const parts = [`CYD ${h.device} fw ${h.fw}`, P.twoWay(h) ? 'two-way' : 'TX-only (GPS on P1)', `${this.hz.toFixed(0)} Hz`,
      `${(this.bytesPerS / 1000).toFixed(1)} kB/s at ${this.port?.baud ?? h.baud} baud`,
      `lost ${this.lost} damaged ${this.deframer.bad} dropped ${d.tx_dropped ?? 0}`];
    if (Object.keys(d).length) {
      const flags = d.flags ?? 0;
      parts.push(flags & P.ST_RECORDING ? `SD rec #${d.drive_no ?? 0}` : flags & P.ST_SD_OK ? 'SD ok' : 'no SD');
      parts.push(`K-line ${d.timeouts ?? 0} t/o ${d.checksum_failures ?? 0} cks`);
      if (d.time_source) parts.push('TIME ' + P.timeText(d.time_source, d.time_err_us ?? 0));
    }
    parts.push(this.mapMismatch ? 'MAP MISMATCH #' + h.mapHash : 'map #' + h.mapHash);
    return parts.join(' · ');
  }

  get ecuIdText(): string {
    if (this.ecuId === null) return '(no reply)';
    const text = Array.from(this.ecuId, (b) => (b >= 32 && b < 127 ? String.fromCharCode(b) : '.')).join('');
    return `${Array.from(this.ecuId, (b) => b.toString(16).toUpperCase().padStart(2, '0')).join(' ')}  "${text}"`;
  }
}
