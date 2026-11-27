/**
 * The integrator's wiring: everything between the core modules and the
 * session store the screens read.
 *
 *   FileStore (platform) --> SqliteDriveStore (#4) ---------------> Replay / Events / Power / GPS (setDriveSource)
 *                        --> MapStore --> AddressMap (#2) --> RecorderMap + LivePipeline (#3 math, alarms)
 *   Transport (picker or simulator) --> Poller (#1, OBD2 fallback #6) --> LiveSession (#4) --> Recorder (hondalog.db)
 *                                                                    \--> session store (channels, raw, ecu, alarms, stall)
 *   CH340 (CYD) --> HandheldCompanion (#5): GPS + events into the LiveSession; Relay when the map hash matches
 *   or, with no KKL cable: CH340 (CYD) --> CydLink as the ECU source (the CYD polls the ECU on its own
 *   K-line cable; its raw READs are decoded with our map, TICK makes the samples) --> LiveSession
 *
 * Session store -> here: recording on/off (REC, Ctrl+R), marks (Space), alarm acknowledge (Esc).
 * No timers of the core modules are touched; the status timer only reads.
 */
import { loads, type AddressMap } from '../core/addressmap';
import { HandheldCompanion, findHandheld, isHandheldPort, type PortSource } from '../core/cyd/companion';
import * as CF from '../core/cyd/format';
import { SILENT_S } from '../core/cyd/link';
import type { CydEvent } from '../core/cyd/live';
import { TransportPort } from '../core/cyd/port';
import { Relay } from '../core/cyd/relay';
import * as CP from '../core/cyd/serial_proto';
import { REAL, type Clock } from '../core/link/clock';
import { Poller, framingOf, type IsoFallback, type LinkErrorEvent } from '../core/link/poller';
import { PID_CHANNEL, channelValue, decodePid } from '../core/obd2/obd2';
import { LiveSession, type LiveFrame } from '../core/record/live';
import { obd2Stream, type Recorder } from '../core/record/recorder';
import { useSession, type ActiveAlarm, type CydStatus, type EcuStatus } from '../core/session';
import { createSimTransport } from '../core/sim/transport';
import { SqliteDriveStore, driveInfo } from '../core/store/drivestore';
import type { FileStore } from '../core/store/filestore';
import { refName, type StoreOptions } from '../core/store/writer';
import type { ChannelSpec, ChannelState, EcuRead, LinkState, LinkStats, MathSpec, PortInfo, Transport, TransportPicker } from '../core/types';
import { drivesChanged, setDriveSource } from '../ui/replay/source';
import { DEMO_DRIVE, withDemoDrive } from '../ui/replay/demodrive';
import { MapStore, type LoadedMap } from './maps';
import { obd2Fallback, type Obd2FallbackHooks } from './obd2fallback';
import { LivePipeline } from './pipeline';
import { recorderMapOf } from './recordermap';

/** sim: the built-in simulator (tests, ?sim); port: the laptop's KKL cable; cyd: the CYD reads
 *  the ECU on its own K-line cable and streams it over its USB-C (desktop ``cyd:`` ports). */
export type Source = { kind: 'sim' } | { kind: 'port'; port: PortInfo } | { kind: 'cyd'; port: PortInfo };

export interface ControllerOptions {
  files: FileStore;
  picker: TransportPicker | null;
  /** The simulator's transport (default: createSimTransport({ preset: 'p2p-car' })). */
  makeSim?: () => Transport;
  clock?: Clock;
  /** Wall time, unix seconds. */
  wallNow?: () => number;
  storeOptions?: StoreOptions;
  /** Honda reads only, Honda then OBD2 (default), or OBD2 (ISO 9141-2) only: no Honda reads. */
  protocol?: () => 'auto' | 'honda' | 'obd2';
  /** The OBD2 fallback (tests; default obd2Fallback = #6's Iso9141Link + Obd2Session). */
  makeIsoFallback?: (hooks: Obd2FallbackHooks) => IsoFallback;
  /** Baud override (0 = the map's). */
  baud?: () => number;
  /** Start recording when the link comes up (default true). */
  autoRecord?: boolean;
  /** Look for the CYD on a granted CH340 port (default true). */
  companion?: boolean;
  /** Pick the ECU source by itself at start-up, on replug and every 3 s (default true). */
  autoSource?: boolean;
  /** The KKL cable's port id Setup > Cable remembers ('' = the first FTDI). */
  ecuPort?: () => string;
  /** Publish to the session store at most this often (Hz, default 10). */
  publishHz?: number;
  /** True when no other tab has hondalog.db open (default: a Web Lock held for the page's life). */
  soleOwner?: () => Promise<boolean>;
}

/** Hold the "hondalog.db" Web Lock while the page lives; false if another tab holds it.
 *  No Web Locks API (old web views, tests): true. */
export function holdDbLock(name = 'hondalog.db'): Promise<boolean> {
  const locks = typeof navigator !== 'undefined' ? (navigator as Navigator & { locks?: LockManager }).locks : undefined;
  if (!locks?.request) return Promise.resolve(true);
  return new Promise((resolve) => {
    locks.request(name, { ifAvailable: true }, (lock) => {
      if (!lock) {
        resolve(false);
        return undefined;
      }
      resolve(true);
      return new Promise<void>(() => undefined);     // held until the page goes
    }).catch(() => resolve(true));
  });
}

/** "STALL 23:03:07" and the cause, worded as the desktop's (session.py, chrome.py StallBanner). */
export function stallBanner(drive: string, e: { reason: string; message: string; t: number }, epochUnix: number) {
  const wall = new Date((epochUnix + e.t) * 1000);
  const hms = [wall.getHours(), wall.getMinutes(), wall.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':');
  const cause = ({ rpm: 'RPM dropped to 0.', link: 'ECU went silent.' } as Record<string, string>)[e.reason]
    ?? (e.message ? e.message.charAt(0).toUpperCase() + e.message.slice(1).replace(/\.$/, '') + '.' : 'Capture saved.');
  return { title: `STALL ${hms}`, cause: `${cause} Saved ${drive}`, drive, t: e.t };
}

const ECU_DETAIL: Partial<Record<LinkState, string>> = { connecting: 'connecting', error: 'no reply' };

export class AppController {
  readonly maps: MapStore;
  store: SqliteDriveStore | null = null;
  loaded: LoadedMap | null = null;
  map: AddressMap | null = null;
  pipeline: LivePipeline | null = null;
  live: LiveSession | null = null;
  poller: Poller | null = null;
  transport: Transport | null = null;
  source: Source | null = null;
  companion: HandheldCompanion | null = null;
  relay: Relay | null = null;
  /** The CYD as the ECU source (Source kind 'cyd'): it polls the K-line, we decode. Never
   *  together with ``poller`` or ``companion``: one program on the CH340, one on the K-line. */
  cydLink: HandheldCompanion | null = null;
  /** A source the owner chose in Setup (or Disconnect): the 3 s watchdog leaves it alone
   *  until the next plug event. */
  autoHold = false;
  /** The last thing that went wrong (for Setup and the tests). */
  lastError = '';
  /** Drives start-up crash recovery marked crashed. */
  recovered: string[] = [];

  private readonly clock: Clock;
  private readonly wallNow: () => number;
  private mem = new Map<number, number>();
  private changed = new Set<string>();
  private byAddr = new Map<number, string[]>();
  private obdValues: Record<string, number | null> = {};
  private bounds: Record<string, { min: number; max: number }> = {};
  private specs: (ChannelSpec | MathSpec)[] = [];
  private lastPublish = -Infinity;
  private pendingFrame: LiveFrame | null = null;
  private alarmKey = '';
  private timer: ReturnType<typeof setInterval> | null = null;
  private unsub: (() => void)[] = [];
  private driveRefreshAt = 0;
  private everUp = false;
  private companionPaused = false;
  private livePauses = 0;
  /** Wall time (unix s) of drive time 0 of the recording, for the stall banner's clock. */
  private recEpoch = 0;
  /** The CYD source's last LINK state code (SERIAL.md: 0 no reply, 1 connecting, 2 ok, 3 OBD2, 4 no cable). */
  private cydCode: number | null = null;
  private cydWarned = '';
  private autoRun: Promise<void> | null = null;
  /** The CYD was the ECU source when Setup's CYD tab took the port: give it back as that. */
  private pausedCyd: PortInfo | null = null;

  constructor(readonly opts: ControllerOptions) {
    this.maps = new MapStore(opts.files);
    this.clock = opts.clock ?? REAL;
    this.wallNow = opts.wallNow ?? (() => Date.now() / 1000);
  }

  // ------------------------------------------------------------ start-up

  /** Open hondalog.db and the address map, hand the store to the screens, start listening to the session. */
  async init(): Promise<void> {
    this.store = await SqliteDriveStore.open(this.opts.files, {
      parseMap: (text) => { try { return loads(text); } catch { return null; } },
    });
    // A drive a reload interrupted is "crashed" in the list at once (not after its heartbeat goes stale).
    try {
      if (await (this.opts.soleOwner ?? holdDbLock)()) this.recovered = await this.store.recoverInterrupted();
    } catch (e) {
      this.lastError = `crash recovery: ${(e as Error).message}`;
    }
    setDriveSource(this.store);
    await this.useMap(await this.maps.load());
    this.subscribe();
    this.timer = setInterval(() => this.statusTick(), 500);
  }

  private async useMap(loaded: LoadedMap): Promise<void> {
    this.loaded = loaded;
    this.map = loaded.map;
    if (loaded.error) this.lastError = loaded.error;
    this.pipeline = new LivePipeline(loaded.map);
    if (this.pipeline.errors.length) this.lastError = this.pipeline.errors.join('; ');
    this.live = new LiveSession(this.store!.hdb, recorderMapOf(loaded.map), {
      pipeline: this.pipeline,
      recorder: this.opts.storeOptions ?? {},
      logMeta: () => this.logMeta(),
      monotonic: () => this.clock.now(),
      now: () => { const t0 = this.t0; return t0 === null ? 0 : this.clock.now() - t0; },
    });
    this.byAddr.clear();
    for (const c of loaded.map.channels) {
      for (let a = c.addr; a < c.addr + c.length; a++) {
        const list = this.byAddr.get(a) ?? [];
        list.push(c.name);
        this.byAddr.set(a, list);
      }
    }
    this.specs = [...loaded.map.channelSpecs(), ...loaded.map.math.filter((m) => m.enabled)];
    this.bounds = {};
    useSession.getState().clearChannels();
    if (this.companion) this.companion.mapHash = loaded.map.versionHash;
    if (this.relay) this.relay.mapHash = loaded.map.versionHash;
    if (this.cydLink) this.cydLink.mapHash = loaded.map.versionHash;
  }

  /** Session time 0 (monotonic s) of the ECU source: the poller's, or the CYD link's. */
  private get t0(): number | null {
    return this.poller?.t0 ?? this.cydLink?.t0 ?? null;
  }

  /** Facts for the drive header (desktop live.py _log_meta): the CYD source marks the drive "cyd". */
  private logMeta(): Record<string, unknown> {
    if (this.poller) return { link: this.poller.linkReport() };
    const c = this.cydLink;
    if (!c) return {};
    const h = c.hello;
    const cyd: Record<string, unknown> = { port: this.cydPortName(), frames: c.frames, lost: c.lost, damaged: c.damaged, map_mismatch: c.mapMismatch };
    if (h) Object.assign(cyd, { device: h.device, firmware: h.fw, variant: CP.variantName(h), link: CP.twoWay(h) ? 'two-way' : 'tx-only', boot: h.bootNo, map_hash: h.mapHash });
    return { source: 'cyd', cyd, ...(c.warning ? { cyd_warning: c.warning } : {}) };
  }

  private cydPortName(): string {
    return this.source?.kind === 'cyd' ? `cyd:${this.source.port.label}` : '';
  }

  /** The map text in use (F7 Bytes, Setup). */
  get mapText(): string {
    return this.loaded?.text ?? '';
  }

  /** Check, store and use a new address map (Setup > Files, F7 Bytes > Address map). Reconnects when connected. */
  async saveMap(text: string): Promise<void> {
    await this.maps.save(text);
    const source = this.source;
    if (source) await this.disconnect();
    await this.useMap(await this.maps.load());
    if (source) await this.connect(source);
  }

  // ------------------------------------------------------------ connection

  /** Poll the ECU through a port the user picked, or the built-in simulator. */
  async connect(source: Source): Promise<void> {
    if (!this.map || !this.live) throw new Error('not initialised');
    await this.teardown();
    if (source.kind === 'cyd') return this.connectCyd(source.port);
    const transport = source.kind === 'sim'
      ? (this.opts.makeSim ?? (() => createSimTransport({ preset: 'p2p-car' })))()
      : this.opts.picker!.open(source.port);
    const sim = transport.kind === 'sim';
    const protocol = this.opts.protocol?.() ?? 'auto';
    const baud = this.opts.baud?.() ?? 0;
    const framing = baud ? { ...framingOf(this.map), baud } : undefined;
    this.source = source;
    this.transport = transport;
    this.mem.clear();
    this.obdValues = {};
    this.everUp = false;
    const s = useSession.getState();
    s.clearChannels();
    s.setDemo(false);
    s.setEcu({ state: 'connecting', rateHz: 0, errors: 0, simulated: sim, obd2: false, detail: (sim ? 'simulator' : transport.label) + (protocol === 'obd2' ? ' (OBD2 only)' : '') });
    const poller = new Poller(transport, this.map, {
      clock: this.clock,
      ...(framing ? { framing } : {}),
      ...(transport.usbLatencyS !== undefined ? { usbLatencyS: transport.usbLatencyS } : {}),
      isoFallback: protocol === 'honda' ? null : (this.opts.makeIsoFallback ?? obd2Fallback)({
        onInit: (phase, detail, hidden) => this.obd2Init(phase, detail, hidden),
      }),
      obd2Only: protocol === 'obd2',
      onRead: (r) => this.onRead(r),
      onCycle: (t, latency) => this.onCycle(t, latency),
      onLink: (state, stats) => this.onLink(state, stats),
      onError: (e) => this.onLinkError(e),
      onPid: (t, pid, raw) => this.onPid(t, pid, raw),
    });
    this.poller = poller;
    this.useDemoDrive(sim);
    if (this.livePauses > 0) poller.pause();      // reconnected during a poke test
    void poller.start().catch((e: unknown) => { this.lastError = `poller: ${String(e)}`; });
    // The CYD may already be linked on its own: restart it so it relays this ECU link.
    if (this.opts.companion !== false && !sim) void this.restartCompanion();
  }

  async disconnect(): Promise<void> {
    if (!(await this.teardown())) return;
    // The CYD stays linked without the ECU (GPS, its own log and stall catcher).
    if (this.opts.companion !== false) void this.startCompanion();
  }

  /** Stop the ECU source (poller or CYD link), the recording and the companion. False if none ran. */
  private async teardown(): Promise<boolean> {
    const poller = this.poller;
    const link = this.cydLink;
    if (!poller && !link) return false;
    if (useSession.getState().recording.active) useSession.getState().stopRecording();
    await this.stopRecorder();
    this.poller = null;
    this.cydLink = null;
    await this.stopCompanion();
    await poller?.stop().catch(() => undefined);
    if (link) {
      await link.stop().catch(() => undefined);
      useSession.getState().setCyd({ state: 'off', mode: 'off', mapMatch: null, detail: '' });
    }
    this.transport = null;
    this.source = null;
    useSession.getState().setEcu({ state: 'stopped', rateHz: 0, simulated: false, obd2: false, detail: '' });
    this.useDemoDrive(false);
    this.markStale();
    return true;
  }

  /** The CYD as the ECU source: it reads the ECU on its own K-line cable, we decode its raw
   *  READs with our map (desktop CydPoller). No companion and no relay meanwhile. */
  private async connectCyd(port: PortInfo): Promise<void> {
    const picker = this.opts.picker;
    if (!picker) throw new Error('no serial access');
    if (this.companionPaused) throw new Error("Setup's CYD black box tab holds the port");
    this.source = { kind: 'cyd', port };
    await this.stopCompanion();          // the same CH340: let go of it before it is opened again
    if (this.source?.kind !== 'cyd' || this.source.port !== port) return;    // another connect came first
    if (this.companionPaused) {           // Setup's CYD tab took the port meanwhile
      this.source = null;
      throw new Error("Setup's CYD black box tab holds the port");
    }
    this.mem.clear();
    this.obdValues = {};
    this.changed.clear();
    this.everUp = false;
    this.cydCode = null;
    this.cydWarned = '';
    const s = useSession.getState();
    s.clearChannels();
    s.setDemo(false);
    s.setEcu({ state: 'connecting', rateHz: 0, errors: 0, simulated: false, obd2: false, detail: 'waiting for the CYD' });
    s.setCyd({ state: 'connecting', mode: 'off', detail: port.label });
    const link = new HandheldCompanion(new TransportPort(picker.open(port), 921600, false), {
      clock: this.clock,
      mapHash: this.map!.versionHash,
      t0: this.clock.now(),
      onHello: () => this.onCydHello(),
      onRead: (t, _mono, addr, data, latencyS) => this.onCydRead(t, addr, data, latencyS),
      onTick: (t) => this.onCydTick(t),
      onLink: (up, state) => this.onCydLink(up, state),
      onIso: (t, mode, pid, data) => { if (mode === 0x41) this.onPid(t, pid, data); },
      onFix: (t, fix) => this.live?.feedGps(t, gpsValues(fix)),       // the source's own GPS: stream source "gps", as the desktop
      onEvent: (ev) => this.live?.onCydEvent(cydEvent(ev)),
    });
    this.cydLink = link;
    this.useDemoDrive(false);
    link.start();
  }

  /** The simulator comes with a recorded demo drive (GPS track, a 3rd-gear WOT
   *  pull) so Replay, Power and GPS have something to show offline. */
  private useDemoDrive(on: boolean): void {
    if (!this.store) return;
    setDriveSource(on ? withDemoDrive(this.store) : this.store);
    const s = useSession.getState();
    if (on && !s.replayDrive) useSession.setState({ replayDrive: DEMO_DRIVE });
    if (!on && s.replayDrive === DEMO_DRIVE) useSession.setState({ replayDrive: null });
  }

  // ------------------------------------------------------------ the poke test

  /** F7 Bytes' poke test borrows the line: the poller stops its live block
   *  reads (only the scanner's submitted reads run) until resumeLive(). */
  async pauseLive(): Promise<void> {
    this.livePauses += 1;
    const p = this.poller;
    if (!p) return;
    p.pause();
    useSession.getState().setEcu({ detail: 'live polling paused: poke test' });
  }

  /** Live polling back on (the poke test always calls it, a stop or an error included). */
  async resumeLive(): Promise<void> {
    this.livePauses = Math.max(0, this.livePauses - 1);
    if (this.livePauses > 0) return;
    const p = this.poller;
    if (!p?.paused) return;
    p.resume();
    const s = useSession.getState();
    if (s.ecu.detail === 'live polling paused: poke test') s.setEcu({ detail: '' });
  }

  /** Stop everything (page unload, tests). */
  async close(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    for (const u of this.unsub) u();
    this.unsub = [];
    await this.disconnect();
    await this.store?.close().catch(() => undefined);
  }

  // ------------------------------------------------------------ the poller

  private onRead(r: EcuRead): void {
    for (let i = 0; i < r.data.length; i++) {
      const a = r.addr + i;
      this.mem.set(a, r.data[i]!);
      for (const n of this.byAddr.get(a) ?? []) this.changed.add(n);
    }
    // EcuRead.t is already the moment the ECU sampled it; LiveSession.onBlock takes the completion time.
    this.live?.onBlock({ t: r.t + r.latencyS, addr: r.addr, data: r.data, latencyS: r.latencyS });
    if (this.poller) this.relay?.onBlock({ mono: this.poller.t0 + r.t + r.latencyS, latencyS: r.latencyS, addr: r.addr, data: r.data });
  }

  private onCycle(t: number, latency: number): void {
    if (!this.map || !this.live) return;
    const obd2 = this.poller?.mode === 'obd2';
    const values = obd2 ? { ...this.obdValues } : this.map.decode(this.mem);
    const fresh = obd2 ? Object.keys(this.obdValues) : [...this.changed];
    this.changed.clear();
    const frame = this.live.onSample({ t, link: true, values, fresh });
    if (this.poller) this.relay?.onSample({ link: true, t, mono: this.poller.t0 + t, latency });
    this.publish(frame);
  }

  private onPid(t: number, pid: number, raw: Uint8Array): void {
    const name = PID_CHANNEL[pid];
    if (name) this.obdValues[name] = channelValue(pid, raw);
    const pv = decodePid(pid, raw);
    const ch = name ? recorderChannel(this.map, name) : null;
    this.live?.onPid(t, raw, { ...obd2Stream(pid, pv, ch), value: name ? this.obdValues[name] ?? null : pv.value });
    this.relay?.onPid(t, pid, raw);
  }

  private onLink(state: LinkState, stats: LinkStats): void {
    const s = useSession.getState();
    const obd2 = state === 'iso_only';
    s.setEcu({
      state, rateHz: stats.rateHz, errors: this.poller?.errors ?? s.ecu.errors, obd2,
      detail: state === 'ok' || obd2 ? (obd2 ? this.poller?.isoLink ?? '' : '') : this.poller?.lastError || ECU_DETAIL[state] || '',
    });
    if (state === 'ok' || obd2) {
      if (!this.everUp) {
        this.everUp = true;
        if (this.opts.autoRecord !== false && !s.recording.active) s.startRecording();
      }
    } else if (state === 'error' && this.live && this.poller) {
      const frame = this.live.onSample({ t: this.clock.now() - this.poller.t0, link: false, values: {} });
      this.publish(frame, true);
    }
  }

  private onLinkError(e: LinkErrorEvent): void {
    this.live?.onLinkError(e);
  }

  private obd2Init(phase: 'start' | 'ok' | 'failed', detail: string, hidden: boolean): void {
    const keep = 'keep hondalog in front while connecting';
    const text = phase === 'start' ? `OBD2 5-baud init: ${keep}`
      : phase === 'ok' ? detail
        : hidden ? `OBD2 init failed: the page was hidden (${keep})` : `OBD2 init failed: ${detail}`;
    useSession.getState().setEcu({ detail: text });
  }

  // ------------------------------------------------------------ the CYD as the ECU source

  private onCydHello(): void {
    const c = this.cydLink;
    if (!c) return;
    useSession.getState().setCyd(this.cydStatus(c));
    // Map mismatch (desktop: the warning in the status bar): its plan may not read every channel of ours.
    if (c.warning && c.warning !== this.cydWarned) {
      this.cydWarned = c.warning;
      useSession.getState().say(`CYD: ${c.warning}`);
    }
  }

  private onCydRead(t: number, addr: number, data: Uint8Array, latencyS: number): void {
    for (let i = 0; i < data.length; i++) {
      const a = addr + i;
      this.mem.set(a, data[i]!);
      for (const n of this.byAddr.get(a) ?? []) this.changed.add(n);
    }
    // t is when the reply's last byte was in (the CYD's clock on ours), as LiveSession.onBlock wants.
    this.live?.onBlock({ t, addr, data, latencyS });
  }

  /** A TICK: one sweep of the CYD's read plan done: one sample, as the poller's onCycle. */
  private onCydTick(t: number): void {
    const c = this.cydLink;
    if (!this.map || !this.live || !c) return;
    const obd2 = c.mode === 'obd2';
    const values = obd2 ? { ...this.obdValues } : this.map.decode(this.mem);
    const fresh = obd2 ? Object.keys(this.obdValues) : [...this.changed];
    this.changed.clear();
    this.publish(this.live.onSample({ t, link: true, values, fresh }));
  }

  private onCydLink(up: boolean, state: number): void {
    const c = this.cydLink;
    if (!c) return;
    this.cydCode = state;
    const s = useSession.getState();
    s.setEcu(this.cydEcuStatus());
    if (up) {
      if (!this.everUp) {
        this.everUp = true;
        if (this.opts.autoRecord !== false && !s.recording.active) s.startRecording();
      }
      return;
    }
    // Down (no cable, no ECU reply): what we had is not data any more (desktop _unknown + _emit_down).
    this.mem.clear();
    this.obdValues = {};
    this.changed.clear();
    if (this.live && this.everUp) this.publish(this.live.onSample({ t: this.clock.now() - c.t0, link: false, values: {} }), true);
    this.markStale();
  }

  /** The ECU chip while the CYD is the source: its link state, worded so "no cable" never looks like data. */
  private cydEcuStatus(): Partial<EcuStatus> {
    const c = this.cydLink!;
    const base = { rateHz: c.hz, errors: c.errors, simulated: false };
    if (c.state === 'port_lost') return { ...base, state: 'error', obd2: false, detail: 'CYD port lost' };
    if (!c.hello) {
      return { ...base, state: 'connecting', obd2: false, detail: c.state === 'no_reply' ? 'CYD: no data (HANDHELD.CFG serial = 1?)' : 'waiting for the CYD' };
    }
    if (c.silentS > SILENT_S) return { ...base, state: 'error', obd2: false, detail: 'CYD silent' };
    if (c.state === 'ok') {
      const obd2 = c.mode === 'obd2' || this.cydCode === CP.LINK_ISO_ONLY;
      return { ...base, state: obd2 ? 'iso_only' : 'ok', obd2, detail: 'through the CYD' };
    }
    const code = this.cydCode ?? c.hello.linkState;
    if (code === CP.LINK_ERROR) return { ...base, state: 'error', obd2: false, detail: 'CYD: no K-line cable' };
    if (code === CP.LINK_OFF) return { ...base, state: 'error', obd2: false, detail: 'CYD: no reply from the ECU' };
    return { ...base, state: 'connecting', obd2: false, detail: `CYD: ${CP.LINK_NAMES[code] ?? 'connecting'}` };
  }

  // ------------------------------------------------------------ the session store

  /** Channels, raw bytes and alarms into the store, at most publishHz. */
  private publish(frame: LiveFrame, force = false): void {
    this.pendingFrame = frame;
    const now = this.clock.now();
    if (!force && now - this.lastPublish < 1 / (this.opts.publishHz ?? 10)) return;
    this.lastPublish = now;
    this.flushPublish();
  }

  private flushPublish(): void {
    const frame = this.pendingFrame;
    if (!frame || !this.pipeline) return;
    this.pendingFrame = null;
    const states: ChannelState[] = [];
    for (const spec of this.specs) {
      const v = frame.values[spec.name];
      const value = v === undefined || v === null || Number.isNaN(v) ? null : v;
      const fresh = frame.link && value !== null;
      let b = this.bounds[spec.name];
      if (fresh) {
        if (!b) b = this.bounds[spec.name] = { min: value!, max: value! };
        else { b.min = Math.min(b.min, value!); b.max = Math.max(b.max, value!); }
      }
      states.push({ spec, value: fresh ? value : null, t: frame.t, min: b?.min ?? null, max: b?.max ?? null, fresh, alarm: this.pipeline.level(spec.name) });
    }
    const s = useSession.getState();
    s.updateChannels(states);
    if (this.mem.size) s.setRaw(Object.fromEntries(this.mem));
    this.setAlarms(this.pipeline.active());
  }

  private setAlarms(alarms: ActiveAlarm[]): void {
    const key = JSON.stringify(alarms);
    if (key === this.alarmKey) return;
    this.alarmKey = key;
    useSession.getState().setAlarms(alarms);
  }

  private markStale(): void {
    const s = useSession.getState();
    const states = Object.values(s.channels).map((c) => ({ ...c, fresh: false, value: null }));
    if (states.length) s.updateChannels(states);
  }

  private subscribe(): void {
    this.unsub.push(useSession.subscribe((s, prev) => {
      if (s.recording.active !== prev.recording.active) {
        if (s.recording.active) this.startRecorder();
        else void this.stopRecorder();
      }
      if (s.marks.length > prev.marks.length && this.live?.recording) {
        const m = s.marks[s.marks.length - 1]!;
        this.live.mark(m.text);
      }
      if (prev.alarms.some((a) => !a.acked) && s.alarms.length && s.alarms.every((a) => a.acked) && s.alarms !== prev.alarms) {
        const changedOnlyAck = s.alarms.length === prev.alarms.length && s.alarms.every((a, i) => a.name === prev.alarms[i]?.name);
        if (changedOnlyAck) this.live?.acknowledge();
      }
    }));
  }

  private startRecorder(): void {
    const live = this.live;
    if (!live || live.recording) return;
    const poller = this.poller;
    const cyd = this.cydLink;
    const t0 = this.t0;
    const epochUnix = t0 !== null ? this.wallNow() - (this.clock.now() - t0) : this.wallNow();
    let recorder: Recorder;
    try {
      recorder = live.startRecording({
        epochUnix,
        port: cyd ? this.cydPortName() : this.transport?.label ?? '',
        ...(poller?.ecuId ? { ecuId: poller.ecuIdText } : cyd?.ecuId ? { ecuId: cyd.ecuIdText } : {}),
      });
    } catch (e) {
      this.lastError = `recorder: ${(e as Error).message}`;
      useSession.getState().stopRecording();
      return;
    }
    this.recEpoch = epochUnix;
    recorder.onCapture = (e) => {
      const s = useSession.getState();
      const name = refName(e.ref);
      s.setStall({ count: s.stall.count + 1, lastFile: name, lastT: e.t, state: 'triggered' });
      s.showStallBanner(stallBanner(name, e, this.recEpoch));
      drivesChanged();
    };
    useSession.getState().say(`Recording to ${recorder.ref?.drive ?? 'hondalog.db'}`);
    this.refreshDrive(true);
    drivesChanged();
  }

  private async stopRecorder(): Promise<void> {
    const live = this.live;
    if (!live?.recording) return;
    await live.stopRecording();
    useSession.getState().setStall({ state: 'off', bufferS: 0, postLeftS: 0 });
    useSession.getState().say('Recording stopped');
    drivesChanged();
  }

  /** The drive being written, for F2 Drive and the status bar. */
  private refreshDrive(force = false): void {
    const r = this.live?.recorder;
    if (!r || !this.store) return;
    const now = this.clock.now();
    if (!force && now < this.driveRefreshAt) return;
    this.driveRefreshAt = now + 5;
    const name = r.ref?.drive;
    const entry = name ? this.store.entries().find((e) => e.name === name) : undefined;
    if (entry && useSession.getState().recording.active) useSession.getState().setDrive(driveInfo(entry));
  }

  /** Twice a second: rate, error count, stall catcher, the drive row, the CYD. */
  private statusTick(): void {
    if (this.pendingFrame) this.flushPublish();
    const s = useSession.getState();
    const p = this.poller;
    if (p) s.setEcu({ rateHz: p.hz, errors: p.errors });
    else if (this.cydLink) s.setEcu(this.cydEcuStatus());     // also catches a silent or lost CYD
    const r = this.live?.recorder;
    if (r) {
      const st = r.status();
      const t0 = this.t0;
      const t = t0 !== null ? this.clock.now() - t0 : 0;
      const state = st.capturing ? 'saving' : r.parked ? 'standby' : st.armed ? 'armed' : 'standby';
      if (!(s.stall.state === 'triggered' && state !== 'saving' && s.stall.lastT !== undefined && s.stall.lastT !== null && t - s.stall.lastT < 5)) {
        s.setStall({ state, bufferS: st.bufferS, preS: st.preS, postS: st.postS, postLeftS: st.capturing ? Math.max(0, st.captureEndsT - t) : 0 });
      }
      this.refreshDrive();
    }
    const shown = this.cydLink ?? this.companion;
    if (shown) s.setCyd(this.cydStatus(shown));
    s.setLinkInfo(this.linkInfo());
    this.watchdog();
  }

  private nextCydTry = 0;
  private cydStarting = false;

  /** Every 3 s: pick the ECU source (a KKL cable that appeared wins over the CYD), then, next to a
   *  KKL cable, link the companion if none is linked (or its port was lost). */
  private watchdog(): void {
    if (this.cydStarting || this.transport?.kind === 'sim') return;
    const now = this.clock.now();
    if (now < this.nextCydTry) return;
    this.nextCydTry = now + 3;
    this.cydStarting = true;
    void this.autoSource().catch(() => undefined)
      .then(() => (this.companionWanted() && (!this.companion || this.companion.state === 'port_lost') ? this.restartCompanion() : false))
      .catch(() => false)
      .finally(() => { this.cydStarting = false; });
  }

  /** The companion may hold the CH340: not while the CYD is the ECU source or Setup's CYD tab has it. */
  private companionWanted(): boolean {
    return this.opts.companion !== false && !this.companionPaused && !this.cydLink && this.source?.kind !== 'cyd' && this.transport?.kind !== 'sim';
  }

  /** The bottom strip's link summary (desktop main_window.py _link_info). */
  linkInfo(): string {
    const map = this.map;
    if (!map) return '';
    const port = this.source?.kind === 'cyd' ? `CYD ${this.source.port.label}`
      : this.transport ? (this.transport.kind === 'sim' ? 'simulator' : this.transport.label) : 'no port';
    const unverified = map.channels.filter((c) => !map.isVerified(c.name)).length;
    return `${port} · map ${map.version.split(' ')[0]} · ${map.channels.length} ch · ${unverified} unverified`;
  }

  // ------------------------------------------------------------ choosing the ECU source

  /** Which source is active, for Setup > Cable. */
  get sourceKind(): 'kkl' | 'cyd' | 'sim' | '' {
    const k = this.source?.kind;
    return k === 'port' ? 'kkl' : k === 'cyd' ? 'cyd' : k === 'sim' ? 'sim' : '';
  }

  /** Setup's CYD black box tab holds the CH340 (HL session). */
  get cydHeld(): boolean {
    return this.companionPaused;
  }

  /** Pick the ECU source with no click (start-up, replug, the 3 s watchdog): a granted KKL cable
   *  wins (the CYD next to it as companion + relay); with none, a granted CYD reads the ECU itself.
   *  Never both on the K-line. One run at a time. */
  autoSource(): Promise<void> {
    if (!this.autoRun) this.autoRun = this.pickSource().finally(() => { this.autoRun = null; });
    return this.autoRun;
  }

  private async pickSource(): Promise<void> {
    const picker = this.opts.picker;
    if (!picker || !this.map || this.opts.autoSource === false || this.autoHold || this.source?.kind === 'sim') return;
    let ports: PortInfo[];
    try { ports = await picker.list(); } catch { return; }
    if (this.autoHold || this.transport?.kind === 'sim') return;      // changed while listing
    const src = this.source;
    const listed = (p: PortInfo) => ports.some((x) => x.id === p.id);
    if (src?.kind === 'port' && listed(src.port)) return;        // the cable in use is still there
    const kkl = this.kklPort(ports);
    if (kkl) {
      await this.connect({ kind: 'port', port: kkl });
      return;
    }
    if (src?.kind === 'cyd' || this.companionPaused) return;    // the CYD link reopens a lost port itself
    const cyd = ports.find(isHandheldPort);
    if (cyd) await this.connect({ kind: 'cyd', port: cyd });
  }

  /** The KKL cable among granted ports: the one Setup > Cable remembers, else the first FTDI. */
  private kklPort(ports: PortInfo[]): PortInfo | undefined {
    const want = this.opts.ecuPort?.() ?? '';
    return ports.find((p) => want !== '' && p.id === want && !isHandheldPort(p)) ?? ports.find((p) => p.guess === 'ecu');
  }

  /** Setup > Cable "Connect through the CYD" (``hold``: the owner's choice sticks until the next
   *  plug event) and the top bar's chip: a granted CYD, else the browser's chooser (from the click). */
  async connectThroughCyd(hold = true): Promise<boolean> {
    const picker = this.opts.picker;
    if (!picker || this.companionPaused) return false;
    let port: PortInfo | null = null;
    try { port = (await picker.list()).find(isHandheldPort) ?? null; } catch { port = null; }
    if (!port && picker.request) port = await picker.request([]);
    if (!port || this.companionPaused) return false;
    if (hold) this.autoHold = true;
    await this.connect({ kind: 'cyd', port });
    return true;
  }

  /** Setup > Cable's buttons: the owner's choice holds the watchdog off until the next plug event. */
  async userConnect(source: Source): Promise<void> {
    this.autoHold = true;
    await this.connect(source);
  }

  async userDisconnect(): Promise<void> {
    this.autoHold = true;
    await this.disconnect();
  }

  // ------------------------------------------------------------ the CYD as companion

  private portSource(): PortSource | null {
    const picker = this.opts.picker;
    if (!picker) return null;
    return {
      list: () => picker.list(),
      open: (info, baud) => new TransportPort(picker.open(info), baud ?? 921600, baud !== null),
    };
  }

  /** The handheld on a granted CH340 port: its GPS and events go into the drive; relay when its map matches. */
  async startCompanion(): Promise<boolean> {
    if (this.companion || !this.companionWanted()) return false;
    const ports = this.portSource();
    if (!ports || !this.map) return false;
    let found: PortInfo | null = null;
    try {
      found = await findHandheld(ports, this.transport?.label ?? '');
    } catch {
      found = null;
    }
    // Checked again after the wait: the CYD may have become the ECU source meanwhile.
    if (!found || this.companion || !this.companionWanted()) return false;
    const companion = new HandheldCompanion(ports.open(found, null), {
      mapHash: this.map.versionHash,
      t0: this.poller?.t0 ?? this.clock.now(),
      onFix: (t, fix) => this.live?.feedGps(t, gpsValues(fix), 'handheld'),
      onEvent: (ev) => this.live?.onCydEvent(cydEvent(ev)),
    });
    this.companion = companion;
    companion.start();
    if (this.poller) {
      const poller = this.poller;
      const clock = this.clock;
      this.relay = new Relay(companion, {
        get state() { return poller.linkState; },
        get mode() { return poller.mode; },
        t0: poller.t0,
        get hz() { return poller.hz; },
        get sweeps() { return poller.cycles; },
        get ecuId() { return poller.ecuId; },
        now: () => clock.now(),
      }, this.map.versionHash).start();
    }
    useSession.getState().setCyd({ state: 'connecting', mode: 'off', detail: found.label });
    return true;
  }

  async stopCompanion(): Promise<void> {
    const relay = this.relay;
    const companion = this.companion;
    this.relay = null;
    this.companion = null;
    await relay?.stop().catch(() => undefined);
    await companion?.stop().catch(() => undefined);
    if (companion) useSession.getState().setCyd({ state: 'off', mode: 'off', mapMatch: null, detail: '' });
  }

  /** Setup's CYD tab takes the port for its HL session: let go of it meanwhile (the CYD as
   *  the ECU source too: given back as that afterwards). */
  async pauseCompanion(): Promise<void> {
    this.companionPaused = true;
    await this.stopCompanion();
    if (this.source?.kind === 'cyd') {
      this.pausedCyd = this.source.port;
      await this.disconnect();
    }
  }

  async resumeCompanion(): Promise<void> {
    this.companionPaused = false;
    const cyd = this.pausedCyd;
    this.pausedCyd = null;
    if (cyd && !this.source) {
      await this.connect({ kind: 'cyd', port: cyd }).catch((e: unknown) => { this.lastError = `CYD: ${String(e)}`; });
      return;
    }
    if (!this.source) await this.autoSource();
    await this.startCompanion();
  }

  async restartCompanion(): Promise<boolean> {
    await this.stopCompanion();
    return this.startCompanion();
  }

  /** The top bar's CYD chip, clicked while off: with no KKL cable, connect the CYD as the ECU
   *  source; next to a KKL cable link it as companion. With no port given yet, show the
   *  browser's chooser first (this runs from the click). */
  async connectCydNow(): Promise<boolean> {
    if (this.companionPaused) return false;     // Setup's CYD tab holds the port
    const picker = this.opts.picker;
    if (this.source?.kind !== 'port') {
      let ports: PortInfo[] = [];
      try { ports = picker ? await picker.list() : []; } catch { ports = []; }
      const kkl = this.kklPort(ports);
      if (kkl) {
        await this.connect({ kind: 'port', port: kkl });      // the KKL cable wins; the CYD comes as companion
        return true;
      }
      return this.connectThroughCyd(false);
    }
    const ports = this.portSource();
    let known: PortInfo | null = null;
    try { known = ports ? await findHandheld(ports, this.transport?.label ?? '') : null; } catch { known = null; }
    if (!known && picker?.request) {
      const picked = await picker.request([]);
      if (!picked) return false;
    }
    this.nextCydTry = this.clock.now() + 3;
    return this.restartCompanion();
  }

  /** A serial port was plugged in (or the CYD came back): pick the source again (a KKL cable
   *  plugged in takes over from the CYD) and link a CYD this browser was given before, with no
   *  click. A lost companion link is dropped and opened again. */
  async portPlugged(): Promise<boolean> {
    if (this.transport?.kind === 'sim') return false;
    this.autoHold = false;
    await this.autoSource().catch(() => undefined);
    if (!this.companionWanted()) return false;
    if (this.companion && this.companion.state !== 'port_lost') return false;
    return this.restartCompanion();
  }

  /** A serial port went away: if it was the CYD's, show it off (not "lost" forever); if it was
   *  the KKL cable's, the CYD may take over as the ECU source. */
  async portUnplugged(): Promise<void> {
    if (this.companion) {
      const ports = this.portSource();
      let still: PortInfo | null = null;
      try { still = ports ? await findHandheld(ports, this.transport?.label ?? '') : null; } catch { still = null; }
      if (!still) await this.stopCompanion();
    }
    await this.autoSource().catch(() => undefined);
  }

  private cydStatus(c: HandheldCompanion): Partial<CydStatus> {
    const h = c.hello;
    const relaying = !!this.relay?.active;
    // "no_reply" is also the CYD's ECU link being down: only a CYD that sends nothing is silent.
    const state: CydStatus['state'] = c.state === 'port_lost' ? 'lost' : h ? (c.silentS > SILENT_S ? 'silent' : 'ok') : 'connecting';
    return {
      state,
      mode: h ? (relaying ? 'relay' : 'standalone') : 'off',
      mapMatch: h ? h.mapHash === this.map?.versionHash : null,
      ...(h ? {
        mapHash: h.mapHash,
        hello: {
          id: h.device, fw: h.fw, model: h.device, variant: h.variant ? 'gps-p1' : 'default', mapHash: h.mapHash,
          twoWay: CP.twoWay(h), caps: h.caps, boot: h.bootNo,
        },
      } : {}),
      sats: c.sats,
      fix: c.gpsOk ? '3D' : '',
      detail: c === this.cydLink ? (c.warning || 'reads the ECU: ' + (c.linkText() || c.statusText())) : this.relay?.text() || c.statusText(),
    };
  }
}

function gpsValues(fix: CP.GpsRecord): Record<string, number | null> {
  return {
    gps_lat: fix.gps_lat, gps_lon: fix.gps_lon, gps_speed: fix.gps_speed, gps_heading: fix.gps_heading,
    gps_sats: fix.gps_sats, gps_fix: fix.gps_fix, gps_hdop: fix.gps_hdop, gps_utc: fix.gps_utc,
  };
}

/** A CYD EVENT for the drive: its button's markers, its stall trigger, trouble codes it read. */
function cydEvent(ev: CydEvent) {
  return {
    t: ev.t, code: ev.code, name: ev.name, src: ev.src, aux: ev.aux, value: ev.value, tDevUs: ev.tUs, text: ev.text,
    isStall: ev.isStall, isMarker: ev.code === CF.EV_MARKER, isDtc: ev.code === CF.EV_DTC || ev.code === CF.EV_DTC_NONE,
  };
}

function recorderChannel(map: AddressMap | null, name: string) {
  const c = map?.channel(name);
  if (!c) return null;
  return {
    name: c.name, label: c.label, units: c.units, kind: c.kind, decimals: c.decimals, min: c.min, max: c.max,
    addr: c.addr, length: c.length, verified: c.verified, convert: () => null,
  };
}
