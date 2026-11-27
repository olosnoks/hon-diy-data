/**
 * The live path's recording side (port of the recording parts of
 * hondalog/live.py LiveSession): readings in, math channels and alarms
 * worked out (by the pipeline agent #3 provides), everything recorded and
 * watched by the triggers.
 *
 *     Poller --onBlock--> LiveSession --feedRead--> Recorder (hondalog.db, raw bytes)
 *            --onSample->   pipeline (math + alarms) -> Recorder ("math" stream, alarm events, captures)
 *     CYD / black box ---> same timebase -----------> Recorder ("blackbox/live", "gps"; key on keeps the drive open)
 *
 * No timers and no I/O of its own: the link layer (#1), the CYD layer (#5)
 * and the UI (#8) call these methods.
 */

import type { HondalogDb } from '../store/db';
import type { Column } from '../store/model';
import { gpsColumns, GPS_STREAM } from '../store/export/track';
import type { Fired } from './engine';
import { MANUAL, alarmRule } from './presets';
import { type PidInfo, Recorder, type RecorderMap, type RecorderOptions, columnFor } from './recorder';

export const MATH_STREAM = 'math';
export const BLACKBOX_STREAM = 'blackbox/live';
/** a rail above this is "on" for the purpose of keeping a drive open (volts) */
export const KEY_ON_V = 6.0;

/**
 * True while the black box sees the car switched on: IG1 (its own debounced
 * flag, or the rail itself), the ECU's supply, or the engine turning.
 */
export function blackboxKeyOn(values: Record<string, number | null | undefined>): boolean {
  const above = (name: string, level: number) => {
    const v = values[name];
    return v !== null && v !== undefined && v === v && v >= level;
  };
  return above('bb_ig1_on', 0.5) || above('bb_ig1', KEY_ON_V) || above('bb_ecub', KEY_ON_V) || above('bb_running', 0.5) || above('bb_rpm_tach', 1.0);
}

export interface BlockRead { t: number; addr: number; data: Uint8Array; latencyS: number }
export interface LinkErrorInfo { t: number; addr: number; length: number; kind: string; attempt: number; message: string }
export interface PollSample { t: number; link: boolean; values: Record<string, number | null>; fresh?: Iterable<string> | null }

/** An alarm event as the alarm engine (agent #3) reports it. */
export interface AlarmEventLike {
  t: number;
  name: string;
  kind: string;                 // raised, cleared, acknowledged
  severity: string;
  value: number | null;
  channel: string;
  unverified: boolean;
  markerText: string;
  message: string;
  actions: readonly string[];   // "marker", "capture", ...
}

/** Math channels + alarms per sample (agent #3's LivePipeline). */
export interface Pipeline {
  readonly mathNames: readonly string[];
  readonly mathChannels: readonly { name: string; label?: string; units?: string; kind?: string; decimals?: number; min?: number; max?: number }[];
  process(values: Record<string, number | null>, t: number, link: boolean, changed?: Iterable<string> | null): { values: Record<string, number | null>; events: AlarmEventLike[] };
  acknowledge?(): AlarmEventLike[];
  reset?(): void;
}

export interface LiveFrame {
  t: number;
  link: boolean;
  values: Record<string, number | null>;
  events: AlarmEventLike[];
  sample: PollSample | null;
}

/** One event from the handheld (agent #5's decoder). */
export interface CydEventLike {
  t: number;
  code: number;
  name: string;
  src: number;
  aux: number;
  value: number;
  tDevUs: number;
  text: string;
  isStall: boolean;
  isMarker: boolean;
  isDtc: boolean;
}

export interface BlackboxEventLike { t: number; text: string; code: number; name: string; eventNo: number; tDevUs: number; startedFile: boolean }

export interface LiveSessionOptions {
  pipeline?: Pipeline | null;
  /** options for every Recorder this session starts */
  recorder?: RecorderOptions;
  /** what the link knows that belongs in a drive header (link counters, read plan, ...) */
  logMeta?: () => Record<string, unknown>;
  /** monotonic seconds (for the handheld's marker echo); default performance.now() */
  monotonic?: () => number;
  /** session seconds now (laptop GPS fixes) */
  now?: () => number;
  maxFrames?: number;
  /** the black box live columns (agent #5's channel list); default: the keys of the first frame */
  blackboxColumns?: Column[];
}

export class LiveSession {
  recorder: Recorder | null = null;
  readonly frames: LiveFrame[] = [];
  onFrame: ((f: LiveFrame) => void) | null = null;
  onRead: ((r: BlockRead) => void) | null = null;
  onGps: ((t: number, fix: Record<string, number | null>) => void) | null = null;
  /** a capture opened on the laptop: have the box / handheld save its side too */
  onTriggerOut: ((f: Fired) => void) | null = null;
  /** a numbered marker: send it to the two-way handheld / black box too; return true if the handheld got it */
  onMarkOut: ((n: number) => boolean) | null = null;
  lastError = '';
  private mathColumns: Column[] | null = null;
  private gpsCols: Column[] | null = null;
  private gpsSource = 'gps';
  private cydMarks: number[] = [];
  private bbAskedAt = -Infinity;
  private readonly monotonic: () => number;

  constructor(readonly hdb: HondalogDb, readonly amap: RecorderMap, readonly opts: LiveSessionOptions = {}) {
    this.monotonic = opts.monotonic ?? (() => performance.now() / 1000);
  }

  get recording(): boolean {
    return this.recorder !== null;
  }

  /** Begin a drive in the database. */
  startRecording(opts: RecorderOptions = {}): Recorder {
    if (this.recorder) return this.recorder;
    const recorder = new Recorder(this.hdb, this.amap, { ...(this.opts.recorder ?? {}), ...opts, meta: { ...(this.opts.recorder?.meta ?? {}), ...(this.opts.logMeta?.() ?? {}), ...(opts.meta ?? {}) } });
    recorder.onFired = (f) => this.fired(f);
    this.recorder = recorder;
    return recorder;
  }

  /** End the drive (its row gets the final link counters). Returns the recorder (drives, rows, events). */
  async stopRecording(): Promise<Recorder | null> {
    const recorder = this.recorder;
    this.recorder = null;
    if (recorder) {
      recorder.setMeta(this.opts.logMeta?.() ?? {});
      await recorder.stop();
    }
    return recorder;
  }

  /** Drop a marker. Returns its number (0 when not recording). */
  mark(text?: string): number {
    const r = this.recorder;
    if (!r) return 0;
    const n = r.markers + 1;
    r.mark(text ?? `MARK ${n}`);
    if (text === undefined) {
      let sent = false;
      try {
        sent = !!this.onMarkOut?.(n);
      } catch (err) {
        this.lastError = `handheld mark: ${(err as Error).message}`;
      }
      if (sent) this.cydMarks.push(this.monotonic());     // the handheld will echo it back
    }
    return n;
  }

  /** Save a capture now (a button). */
  trigger(): void {
    this.recorder?.trigger(MANUAL);
  }

  acknowledge(): AlarmEventLike[] {
    const events = this.opts.pipeline?.acknowledge?.() ?? [];
    this.recordAlarms(events);
    return events;
  }

  // ---- the poller ----

  /** One K-line block read: recorded at the time the ECU sampled it (completion minus its age). */
  onBlock(read: BlockRead): void {
    this.recorder?.feedRead(read.t - read.latencyS, read.addr, read.data, read.latencyS);
    this.onRead?.(read);
  }

  onLinkError(err: LinkErrorInfo): void {
    this.recorder?.feedLinkError(err.t, err.addr, err.length, err.kind, err.attempt, err.message);
  }

  /** One OBD2 Mode 01 reply (OBD2-only live mode). */
  onPid(t: number, data: Uint8Array, pid: PidInfo): void {
    this.recorder?.feedPid(t, data, pid);
  }

  /** One poller sweep: math channels and alarms, the "math" stream, alarm events, a dead link. */
  onSample(sample: PollSample): LiveFrame {
    const pipeline = this.opts.pipeline ?? null;
    let values: Record<string, number | null> = { ...sample.values };
    let events: AlarmEventLike[] = [];
    if (pipeline) {
      const out = pipeline.process(sample.values, sample.t, sample.link, sample.fresh ?? null);
      values = out.values;
      events = out.events;
    }
    const frame: LiveFrame = { t: sample.t, link: sample.link, values, events, sample };
    const r = this.recorder;
    if (r) {
      if (!frame.link) r.feedLink(frame.t, false);
      else if (pipeline && pipeline.mathNames.length) {
        if (!this.mathColumns) this.mathColumns = pipeline.mathChannels.map(columnFor);
        const math: Record<string, number | null> = {};
        for (const n of pipeline.mathNames) math[n] = frame.values[n] ?? null;
        r.feedValues(MATH_STREAM, frame.t, math, { source: 'math', columns: this.mathColumns });
      }
      this.recordAlarms(frame.events);
    }
    this.frames.push(frame);
    const max = this.opts.maxFrames ?? 2000;
    if (this.frames.length > max) this.frames.splice(0, this.frames.length - max);
    this.onFrame?.(frame);
    return frame;
  }

  /** Alarm events go into the log; "marker" drops a marker, "capture" opens a capture window. */
  private recordAlarms(events: AlarmEventLike[]): void {
    const r = this.recorder;
    if (!r) return;
    for (const e of events) {
      r.event('alarm', e.markerText, { alarm: e.name, kind: e.kind, severity: e.severity, value: e.value, channel: e.channel, unverified: e.unverified }, e.t);
      if (e.kind !== 'raised') continue;
      if (e.actions.includes('marker')) r.mark(e.markerText);
      if (e.actions.includes('capture')) r.trigger(alarmRule(e.name, e.message, e.severity, r.rules), e.t);
    }
  }

  private fired(f: Fired): void {
    if (!f.rule.actions.includes('capture') || f.rule.name === 'external') return;
    this.bbAskedAt = this.monotonic();
    try {
      this.onTriggerOut?.(f);
    } catch (err) {
      this.lastError = `black box trigger: ${(err as Error).message}`;
    }
  }

  // ---- GPS, the handheld, the black box ----

  /** A GPS fix (session seconds, the "gps" stream's columns): to onGps and, while recording, into the "gps" stream. */
  feedGps(t: number, fix: Record<string, number | null>, source = 'gps', columns?: Column[]): void {
    try {
      this.onGps?.(t, fix);
    } catch (err) {
      this.lastError = `gps: ${(err as Error).message}`;
    }
    const r = this.recorder;
    if (!r) return;
    if (!this.gpsCols || this.gpsSource !== source) {
      this.gpsCols = columns ?? gpsColumns();
      this.gpsSource = source;
    }
    r.feedValues(GPS_STREAM, t, fix, { source, columns: this.gpsCols, evaluate: false });
  }

  /** The handheld's own events: its button's markers, its stall trigger, trouble codes. */
  onCydEvent(e: CydEventLike): void {
    const r = this.recorder;
    if (!r) return;
    const data = { code: e.code, name: e.name, src: e.src, aux: e.aux, value: e.value, handheld_us: e.tDevUs };
    if (e.isMarker) {
      const now = this.monotonic();
      while (this.cydMarks.length && now - this.cydMarks[0]! >= 3.0) this.cydMarks.shift();   // sent, never echoed
      if (this.cydMarks.length) {
        this.cydMarks.shift();                       // the echo of a marker sent from here
        return;
      }
      r.mark(`MARK (handheld button ${e.value})`);
    } else if (e.isDtc) {
      r.event('dtc', e.text, data, e.t);
    } else {
      r.event('handheld', e.text, data, e.t);
    }
    if (e.isStall) r.externalTrigger(`handheld: ${e.text}`, e.t);
  }

  /** A black box frame: recorded with alive = key on, so the drive stays open while the ECU is dead. */
  onBlackboxSample(sample: { t: number; link: boolean; values: Record<string, number | null> }): void {
    const r = this.recorder;
    if (!r || !sample.link) return;
    r.feedValues(BLACKBOX_STREAM, sample.t, sample.values, {
      source: 'blackbox', columns: this.opts.blackboxColumns, alive: blackboxKeyOn(sample.values),
    });
  }

  onBlackboxEvent(e: BlackboxEventLike): void {
    const r = this.recorder;
    if (!r) return;
    r.event('blackbox', e.text, { code: e.code, name: e.name, event_no: e.eventNo, box_us: e.tDevUs }, e.t);
    if (e.startedFile && this.monotonic() - this.bbAskedAt > 2.0) r.externalTrigger(`black box: ${e.name}`, e.t);
  }
}
