/**
 * The integrator's wiring: everything between the core modules and the
 * session store the screens read.
 *
 *   FileStore (platform) --> SqliteDriveStore (#4) ---------------> Replay / Events / Power / GPS (setDriveSource)
 *                        --> MapStore --> AddressMap (#2) --> RecorderMap + LivePipeline (#3 math, alarms)
 *   Transport (picker or simulator) --> Poller (#1, OBD2 fallback #6) --> LiveSession (#4) --> Recorder (hondalog.db)
 *                                                                    \--> session store (channels, raw, ecu, alarms, stall)
 *   CH340 (CYD) --> HandheldCompanion (#5): GPS + events into the LiveSession; Relay when the map hash matches
 *
 * Session store -> here: recording on/off (REC, Ctrl+R), marks (Space), alarm acknowledge (Esc).
 * No timers of the core modules are touched; the status timer only reads.
 */
import { loads, type AddressMap } from '../core/addressmap';
import { HandheldCompanion, findHandheld, type PortSource } from '../core/cyd/companion';
import { TransportPort } from '../core/cyd/port';
import { Relay } from '../core/cyd/relay';
import * as CP from '../core/cyd/serial_proto';
import { REAL, type Clock } from '../core/link/clock';
import { Poller, framingOf, type IsoFallback, type LinkErrorEvent } from '../core/link/poller';
import { PID_CHANNEL, channelValue, decodePid } from '../core/obd2/obd2';
import { LiveSession, type LiveFrame } from '../core/record/live';
import { obd2Stream, type Recorder } from '../core/record/recorder';
import { useSession, type ActiveAlarm, type CydStatus } from '../core/session';
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

export type Source = { kind: 'sim' } | { kind: 'port'; port: PortInfo };

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
      logMeta: () => (this.poller ? { link: this.poller.linkReport() } : {}),
      monotonic: () => this.clock.now(),
      now: () => (this.poller ? this.clock.now() - this.poller.t0 : 0),
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
    await this.disconnect();
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
    const poller = this.poller;
    if (!poller) return;
    if (useSession.getState().recording.active) useSession.getState().stopRecording();
    await this.stopRecorder();
    this.poller = null;
    await this.stopCompanion();
    await poller.stop().catch(() => undefined);
    this.transport = null;
    this.source = null;
    // The CYD stays linked without the ECU (GPS, its own log and stall catcher).
    if (this.opts.companion !== false) void this.startCompanion();
    useSession.getState().setEcu({ state: 'stopped', rateHz: 0, simulated: false, obd2: false, detail: '' });
    this.useDemoDrive(false);
    this.markStale();
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
    const epochUnix = poller ? this.wallNow() - (this.clock.now() - poller.t0) : this.wallNow();
    let recorder: Recorder;
    try {
      recorder = live.startRecording({
        epochUnix,
        port: this.transport?.label ?? '',
        ...(poller?.ecuId ? { ecuId: poller.ecuIdText } : {}),
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
    const r = this.live?.recorder;
    if (r) {
      const st = r.status();
      const t = p ? this.clock.now() - p.t0 : 0;
      const state = st.capturing ? 'saving' : r.parked ? 'standby' : st.armed ? 'armed' : 'standby';
      if (!(s.stall.state === 'triggered' && state !== 'saving' && s.stall.lastT !== undefined && s.stall.lastT !== null && t - s.stall.lastT < 5)) {
        s.setStall({ state, bufferS: st.bufferS, preS: st.preS, postS: st.postS, postLeftS: st.capturing ? Math.max(0, st.captureEndsT - t) : 0 });
      }
      this.refreshDrive();
    }
    if (this.companion) s.setCyd(this.cydStatus());
    s.setLinkInfo(this.linkInfo());
  }

  /** The bottom strip's link summary (desktop main_window.py _link_info). */
  linkInfo(): string {
    const map = this.map;
    if (!map) return '';
    const port = this.transport ? (this.transport.kind === 'sim' ? 'simulator' : this.transport.label) : 'no port';
    const unverified = map.channels.filter((c) => !map.isVerified(c.name)).length;
    return `${port} · map ${map.version.split(' ')[0]} · ${map.channels.length} ch · ${unverified} unverified`;
  }

  // ------------------------------------------------------------ the CYD

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
    if (this.companion || this.companionPaused) return false;
    const ports = this.portSource();
    if (!ports || !this.map) return false;
    let found: PortInfo | null = null;
    try {
      found = await findHandheld(ports, this.transport?.label ?? '');
    } catch {
      found = null;
    }
    if (!found || this.companion) return false;
    const companion = new HandheldCompanion(ports.open(found, null), {
      mapHash: this.map.versionHash,
      t0: this.poller?.t0 ?? this.clock.now(),
      onFix: (t, fix) => this.live?.feedGps(t, {
        gps_lat: fix.gps_lat, gps_lon: fix.gps_lon, gps_speed: fix.gps_speed, gps_heading: fix.gps_heading,
        gps_sats: fix.gps_sats, gps_fix: fix.gps_fix, gps_hdop: fix.gps_hdop, gps_utc: fix.gps_utc,
      }, 'handheld'),
      onEvent: (ev) => this.live?.onCydEvent({
        t: ev.t, code: ev.code, name: ev.name, src: ev.src, aux: ev.aux, value: ev.value, tDevUs: ev.tUs, text: ev.text,
        isStall: ev.isStall, isMarker: ev.code === 1, isDtc: ev.code === 11,
      }),
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

  /** Setup's CYD tab takes the port for its HL session: let go of it meanwhile. */
  async pauseCompanion(): Promise<void> {
    this.companionPaused = true;
    await this.stopCompanion();
  }

  async resumeCompanion(): Promise<void> {
    this.companionPaused = false;
    if (this.transport?.kind !== 'sim') await this.startCompanion();
  }

  async restartCompanion(): Promise<boolean> {
    await this.stopCompanion();
    return this.startCompanion();
  }

  /** A serial port was plugged in (or the CYD came back): link a CYD this browser
   *  was given before, with no click. A lost link is dropped and opened again. */
  async portPlugged(): Promise<boolean> {
    if (this.opts.companion === false || this.companionPaused || this.transport?.kind === 'sim') return false;
    if (this.companion && this.companion.state !== 'port_lost') return false;
    return this.restartCompanion();
  }

  /** A serial port went away: if it was the CYD's, show it off (not "lost" forever). */
  async portUnplugged(): Promise<void> {
    if (!this.companion) return;
    const ports = this.portSource();
    let still: PortInfo | null = null;
    try { still = ports ? await findHandheld(ports, this.transport?.label ?? '') : null; } catch { still = null; }
    if (!still) await this.stopCompanion();
  }

  private cydStatus(): Partial<CydStatus> {
    const c = this.companion!;
    const h = c.hello;
    const relaying = !!this.relay?.active;
    const state: CydStatus['state'] = c.state === 'port_lost' ? 'lost' : h ? (c.state === 'no_reply' ? 'silent' : 'ok') : 'connecting';
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
      detail: this.relay?.text() || c.statusText(),
    };
  }
}

function recorderChannel(map: AddressMap | null, name: string) {
  const c = map?.channel(name);
  if (!c) return null;
  return {
    name: c.name, label: c.label, units: c.units, kind: c.kind, decimals: c.decimals, min: c.min, max: c.max,
    addr: c.addr, length: c.length, verified: c.verified, convert: () => null,
  };
}
