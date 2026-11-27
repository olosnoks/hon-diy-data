/**
 * Recording (port of hondalog/logger.py): the drive being written to the
 * database, the triggers, and the captures they save.
 *
 * One ``Recorder`` is one recording session. Triggers (the stall rules by
 * default) are evaluated on every feed; a capture window that closes is
 * entered in ``captures`` as a time range of the drive (no data is copied).
 *
 * A session is one or more drives. A drive ends when the recorder stops, or
 * when nothing has shown the car to be switched on for ``driveGapS`` (key
 * off). "Switched on" is the ECU answering, or another source saying so (the
 * black box feeds its frames with ``alive: true`` while it sees the key on):
 * a stall with the key on and the ECU dead stays in one drive. The next sign
 * of life starts a new drive on the same clock. Between drives nothing is
 * recorded.
 *
 *     feedRead(t, addr, data)     one K-line read; raw bytes stored next to the values
 *     feedLink(t, up)             the ECU stopped or started answering
 *     feedValues(stream, t, {})   math channels, black box frames, GPS fixes
 *     feed(sample)                one whole row of decoded values (imports, tests)
 *     feedLinkError(...)          a failed exchange while the link is up ("link_error" event)
 */

import { type HondalogDb } from '../store/db';
import { type RetentionPolicy, applyRetention } from '../store/library';
import { type Column, column } from '../store/model';
import { localIso, pyG } from '../store/pyfmt';
import { APP_FORMAT } from '../store/version';
import { type DriveRef, type DriveWriter, Store, type StoreOptions } from '../store/writer';
import { type Capture, type FeedValues, type Fired, type TriggerRule, TriggerEngine, ruleToDict } from './engine';
import { MANUAL, type StallOptions, defaultRules, externalRule } from './presets';

export const LINK = 'link';
export const ECU_STREAM = 'ecu';
export const LINK_STREAM = 'ecu/link';

/** An address map channel as the recorder needs it; ``convert`` decodes its bytes (agent #2/#3's formula). */
export interface RecorderChannel {
  name: string;
  label: string;
  units: string;
  kind: string;
  decimals: number;
  min: number;
  max: number;
  addr: number;
  length: number;
  verified?: boolean;
  convert(raw: Uint8Array): number | null;
}

export interface RecorderMap {
  ecu: string;
  version: string;
  versionTag?: string;
  versionHash?: string;
  variant?: string;
  /** file name the map came from, if any */
  path?: string;
  /** the TOML text (stored once per map with every drive) */
  text?: string;
  channels: RecorderChannel[];
}

export interface Sample {
  t: number;
  /** wall time of this sample (unix s), if the source knows it */
  wallUnix?: number | null;
  link: boolean;
  values: Record<string, number | null | undefined>;
}

/** A capture was saved. ``reason`` is the rule's short code: "rpm" and "link" for the stall rules. */
export interface CaptureEvent {
  ref: DriveRef;
  reason: string;
  t: number;
  rows: number;
  rule: string;
  label: string;
  message: string;
}

/** What the stall catcher is doing right now, for a status chip. */
export interface TriggerStatus {
  armed: boolean;
  capturing: boolean;
  preS: number;
  postS: number;
  /** seconds of log a trigger could reach back over now */
  bufferS: number;
  captureEndsT: number;
  fired: number;
}

/** One OBD2 Mode 01 reply, decoded by agent #6's code (see ``obd2Stream``). */
export interface PidInfo {
  stream: string;
  column: Column;
  attrs: Record<string, unknown>;
  value: number | null;
}

/** An address map channel, math channel or black box channel as a log column. */
export function columnFor(ch: { name: string; units?: string; label?: string; kind?: string; decimals?: number; min?: number; max?: number }): Column {
  return column(ch.name, {
    dtype: 'f4', units: ch.units ?? '', label: ch.label && ch.label !== ch.name ? ch.label : '', kind: ch.kind === 'flag' ? 'flag' : 'value',
    decimals: ch.decimals ?? 2, lo: ch.min ?? null, hi: ch.max ?? null,
  });
}

/**
 * Stream name, column and attrs for one Mode 01 PID: a PID that is the same
 * quantity as a map channel gets that channel's name with the PID's units.
 */
export function obd2Stream(pid: number, pv: { name: string; units: string }, ch?: RecorderChannel | null): Omit<PidInfo, 'value'> {
  const hex = pid.toString(16).padStart(2, '0');
  const col = ch ? column(ch.name, { dtype: 'f4', units: pv.units || ch.units, label: ch.label !== ch.name ? ch.label : '', kind: ch.kind === 'flag' ? 'flag' : 'value', decimals: ch.decimals, lo: ch.min, hi: ch.max })
    : column(`pid_${hex}`, { dtype: 'f4', units: pv.units, label: pv.name });
  return { stream: `obd2/${hex.toUpperCase()}`, column: col, attrs: { mode: 1, pid, name: pv.name } };
}

export interface RecorderOptions extends StoreOptions {
  ecuId?: string;
  port?: string;
  rules?: TriggerRule[];
  /** seconds without a sign of life that end a drive (120); null: one drive for the whole session */
  driveGapS?: number | null;
  retention?: RetentionPolicy | null;
  /** wall time (unix s) of session time 0, when the clock knows it */
  epochUnix?: number | null;
  meta?: Record<string, unknown>;
  stall?: StallOptions;
}

export class Recorder {
  readonly header: Record<string, unknown>;
  readonly startedUnix: number;
  /** every drive of this session, oldest first */
  readonly drives: DriveRef[] = [];
  readonly events: CaptureEvent[] = [];
  readonly notices: Fired[] = [];
  /** called with every saved capture */
  onCapture: ((e: CaptureEvent) => void) | null = null;
  /** called with every rule firing (a banner or a beep before the capture is saved) */
  onNotice: ((f: Fired) => void) | null = null;
  /** called with every Fired (the live session tells the black box) */
  onFired: ((f: Fired) => void) | null = null;
  markers = 0;
  /** sweeps or reads fed so far */
  rows = 0;
  active = true;

  readonly driveGapS: number | null;
  readonly retention: RetentionPolicy | null;
  private readonly store: Store;
  private log: DriveWriter | null = null;
  private engine: TriggerEngine;
  private underReview = new Map<string, DriveWriter>();
  private pendingMarkers: string[] = [];
  private epoch: number | null;
  private lastT = 0;
  private tFirst: number | null = null;
  private driveT0: number | null = null;
  private downSince: number | null = null;
  private keyOffSeen = false;
  private link: boolean | null = null;
  private readonly names: string[];
  private readonly unknown: FeedValues;
  private blockChannels = new Map<string, RecorderChannel[]>();
  private custom = new Map<string, string[]>();

  constructor(readonly hdb: HondalogDb, readonly amap: RecorderMap, opts: RecorderOptions = {}) {
    this.startedUnix = hdb.now();
    this.driveGapS = opts.driveGapS === undefined ? 120.0 : opts.driveGapS;
    this.retention = opts.retention ?? null;
    const rules = opts.rules ?? defaultRules(opts.stall);
    this.header = {
      format: APP_FORMAT,
      started: localIso(this.startedUnix),
      started_unix: this.startedUnix,
      timebase: 'seconds since connect (monotonic)',
      ecu: amap.ecu,
      ecu_id: opts.ecuId || '(not read)',
      port: opts.port ?? '',
      address_map_version: amap.version,
      address_map_tag: amap.versionTag ?? amap.version,
      address_map_hash: amap.versionHash ?? '',
      ecu_variant: amap.variant ?? '',
      address_map_file: amap.path ?? '',
      address_map_text: amap.text ?? '',
      unverified_channels: amap.channels.filter((c) => c.verified === false).map((c) => c.name).join(' ') || 'none',
      units: amap.channels.map((c) => `${c.name}=${c.units || '-'}`).join(' '),
      triggers: rules.map(ruleToDict),
      ...(opts.meta ?? {}),
    };
    this.epoch = opts.epochUnix ?? null;
    if (this.epoch !== null) this.header.epoch_unix = this.epoch;
    this.names = amap.channels.map((c) => c.name);
    this.unknown = Object.fromEntries(this.names.map((n) => [n, null]));
    this.store = new Store(hdb, opts);
    this.openDrive(this.startedUnix);
    this.engine = new TriggerEngine(rules);
    this.engine.onRevoked = (f, t) => this.revoked(f, t);
  }

  // ---- properties ----

  /** The drive being written now (the last one of the session). */
  get ref(): DriveRef {
    return this.drives[this.drives.length - 1]!;
  }

  /** Between drives: for driveGapS the ECU has been silent and nothing else has shown the key on. */
  get parked(): boolean {
    return this.log === null;
  }

  get rules(): TriggerRule[] {
    return this.engine.rules;
  }

  get capturing(): boolean {
    return this.engine.capturing;
  }

  /** The last database error, if the log could not be written. */
  get writeError(): Error | null {
    return this.store.error;
  }

  status(): TriggerStatus {
    const engine = this.engine;
    let stall = engine.rules.filter((r) => r.enabled && r.kind === 'stall');
    if (!stall.length) stall = engine.rules.filter((r) => r.enabled);
    const out: TriggerStatus = { armed: false, capturing: false, preS: 30, postS: 10, bufferS: 0, captureEndsT: 0, fired: 0 };
    if (stall.length) {
      out.preS = stall[0]!.preS;
      out.postS = stall[0]!.postS;
      out.armed = stall.some((r) => engine.armed(r.name));
    }
    out.fired = engine.rules.reduce((s, r) => s + engine.firedCount(r.name), 0);
    if (this.driveT0 !== null && this.log !== null) out.bufferS = Math.max(0, Math.min(out.preS, this.lastT - this.driveT0));
    if (engine.capture) {
      out.capturing = true;
      out.captureEndsT = engine.capture.tEnd;
    }
    return out;
  }

  // ---- UI side ----

  /** Drop a marker. It gets the time of the next thing fed. */
  mark(text = 'MARK'): void {
    this.pendingMarkers.push(text);
    this.markers++;
  }

  /** A free-text note at the current time. */
  note(text: string): void {
    if (this.active) this.drive().event(this.lastT, 'note', text);
  }

  /** Add facts to the drive's header: DTCs, a freeze frame, expert-unlock, notes, tags. */
  setMeta(items: Record<string, unknown>): void {
    if (!this.active) return;
    Object.assign(this.header, items);
    this.log?.setMeta(items);
  }

  /** Record an event of any kind ("alarm", "dtc", "link", ...). */
  event(kind: string, text = '', data: Record<string, unknown> | null = null, t: number | null = null): void {
    if (this.active) this.drive().event(t ?? this.lastT, kind, text, data);
  }

  /** One failed K-line exchange while the link was up: a "link_error" event (only while a drive is open). */
  feedLinkError(t: number, addr: number, length: number, kind: string, attempt: number, message = ''): void {
    if (!this.active || !this.log) return;
    const hex = addr.toString(16).toUpperCase().padStart(2, '0');
    this.log.event(t, 'link_error', `${kind} ${hex}+${length} #${attempt}`, { addr, length, kind, attempt, message: message.slice(0, 120) });
  }

  /** Start a capture by hand, from an alarm, or from a black box trigger. */
  trigger(rule: TriggerRule | string = MANUAL, t: number | null = null): void {
    if (this.active) this.handleFired([this.engine.fire(rule, t ?? this.lastT)]);
  }

  /** Another source (the black box, the handheld) reported a stall: a capture around now through the same engine. */
  externalTrigger(why: string, t: number | null = null): void {
    if (this.active) this.handleFired([this.engine.fire(externalRule(why, this.engine.rules), t ?? this.lastT)]);
  }

  /** Commit what has been fed so far. */
  flush(): void {
    this.store.write();
  }

  async stop(): Promise<void> {
    if (!this.active) return;
    this.active = false;
    const capture = this.engine.finish();
    if (capture) this.saveCapture(capture);
    try {
      this.endDrive('stopped');
      await this.store.close();
    } catch {
      /* the error stays in writeError */
    }
    if (this.retention) {
      applyRetention(this.hdb, this.retention);
      await this.hdb.persist().catch(() => undefined);
    }
  }

  // ---- feeding ----

  /** One row of decoded values for every channel (no raw bytes: such a log cannot be decoded again). */
  feed(sample: Sample): void {
    if (!this.active || !this.begin(sample.t, sample.wallUnix ?? null, !!sample.link)) return;
    this.after(sample.t, this.sweep(sample));
  }

  /** One K-line read: ``data`` is what the ECU returned for ``addr``; ``latency`` its estimated age (s). */
  feedRead(t: number, addr: number, data: Uint8Array, latency: number | null = null): void {
    if (!this.active) return;
    this.begin(t, null, true);
    const changed = this.read(t, addr, data, latency);
    Object.assign(changed, this.setLink(t, true));
    this.after(t, changed);
  }

  /** One OBD2 Mode 01 reply (OBD2-only live mode), stored raw in an ``obd2/XX`` stream. */
  feedPid(t: number, data: Uint8Array, pid: PidInfo): void {
    if (!this.active) return;
    this.begin(t, null, true);
    const log = this.drive();
    if (!log.stream(pid.stream)) log.addStream(pid.stream, [pid.column], { source: 'obd2', rawLen: data.length, attrs: pid.attrs });
    try {
      log.append(pid.stream, t, [pid.value], data);
    } catch {
      /* another length than the first reply: keep the stream consistent */
    }
    const changed: FeedValues = { [pid.column.name]: pid.value, ...this.setLink(t, true) };
    this.after(t, changed);
  }

  /** The link went up or down (no reply, port lost). */
  feedLink(t: number, up: boolean): void {
    if (!this.active || !this.begin(t, null, up)) return;
    this.after(t, this.setLink(t, up));
  }

  /**
   * One row of any other stream on the session timebase. Its columns are
   * fixed by the first call (``columns``, or the keys of ``values``).
   * ``alive``: this source sees the key on (true) / off (false); undefined:
   * it cannot tell, and its rows are dropped between drives.
   */
  feedValues(stream: string, t: number, values: Record<string, number | null | undefined>,
    opts: { source?: string; columns?: Column[]; evaluate?: boolean; alive?: boolean | null } = {}): void {
    if (!this.active || !this.begin(t, null, null, opts.alive ?? null)) return;
    const log = this.drive();
    let names = this.custom.get(stream);
    if (!names) {
      const cols = opts.columns ?? Object.keys(values).map((n) => column(n));
      log.addStream(stream, cols, { source: opts.source || stream.split('/')[0] });
      names = cols.map((c) => c.name);
      this.custom.set(stream, names);
    }
    log.append(stream, t, names.map((n) => values[n] ?? null));
    if (opts.evaluate ?? true) this.after(t, values);
  }

  // ---- internals ----

  private openDrive(startedUnix: number): DriveWriter {
    const meta: Record<string, unknown> = { ...this.header, drive_of_session: this.drives.length + 1 };
    meta.started = localIso(startedUnix);
    meta.started_unix = startedUnix;
    if (this.drives.length) meta.previous_drive = this.drives[this.drives.length - 1]!.drive;
    this.log = this.store.beginDrive(meta, { startedUnix });
    this.drives.push(this.log.ref);
    this.custom = new Map();
    this.driveT0 = null;
    this.link = null;
    return this.log;
  }

  /** The drive being written; starts one if the recorder was parked. */
  private drive(): DriveWriter {
    if (!this.log) this.openDrive(this.wall(this.lastT));
    return this.log!;
  }

  private endDrive(reason: string): void {
    const log = this.log;
    this.log = null;
    if (log) log.end('complete', reason);
  }

  private begin(t: number, wall: number | null, link: boolean | null, alive: boolean | null = null): boolean {
    if (this.epoch === null) {
      // wall time of session time 0: what lines this log up with a black box capture or another drive
      this.epoch = (wall ?? this.hdb.now()) - t;
      this.header.epoch_unix = this.epoch;
      this.log?.setMeta({ epoch_unix: this.epoch });
    }
    if (link || alive) {
      this.downSince = null;                     // a sign of life: the ECU, or the key seen on
      this.keyOffSeen = false;
    } else {
      if (alive === false) this.keyOffSeen = true;
      if (this.downSince === null && ((link === false && this.link !== null) || alive === false)) this.downSince = t;
    }
    if (this.log && this.downSince !== null && this.driveGapS !== null && t - this.downSince >= this.driveGapS && !this.engine.capturing) {
      // "key_off" when a second witness saw the key off; "link_lost" when all that is known is the ECU's silence
      this.endDrive(this.keyOffSeen ? 'key_off' : 'link_lost');
    }
    if (!this.log) {
      if (!(link || alive)) {
        this.lastT = t;
        return false;
      }
      const log = this.openDrive(this.wall(t));
      if (!link) log.setMeta({ started_by: 'key on seen by another source, ECU silent' });
    }
    if (this.tFirst === null) this.tFirst = t;
    if (this.driveT0 === null) this.driveT0 = t;
    if (this.pendingMarkers.length) {
      for (const text of this.pendingMarkers) this.drive().event(t, 'marker', text);
      this.pendingMarkers = [];
    }
    this.lastT = t;
    this.rows++;
    return true;
  }

  private sweep(sample: Sample): FeedValues {
    const log = this.drive();
    if (!log.stream(ECU_STREAM)) {
      const cols = [column(LINK, { dtype: 'u1', kind: 'flag', decimals: 0 }), ...this.amap.channels.map(columnFor)];
      log.addStream(ECU_STREAM, cols, { source: 'ecu', attrs: { mode: 'sweep' } });
    }
    const values = sample.values;
    log.append(ECU_STREAM, sample.t, [sample.link ? 1 : 0, ...this.names.map((n) => values[n] ?? null)]);
    this.link = !!sample.link;
    if (sample.link) return { ...values, [LINK]: 1 };
    return { ...this.unknown, [LINK]: 0 };
  }

  private read(t: number, addr: number, data: Uint8Array, latency: number | null): FeedValues {
    const key = `${addr}+${data.length}`;
    let channels = this.blockChannels.get(key);
    if (!channels) {
      channels = this.amap.channels.filter((c) => addr <= c.addr && c.addr + c.length <= addr + data.length);
      this.blockChannels.set(key, channels);
    }
    const name = `ecu/${addr.toString(16).toUpperCase().padStart(2, '0')}+${data.length}`;
    const log = this.drive();
    if (!log.stream(name)) {
      log.addStream(name, channels.map(columnFor), { source: 'ecu', rawLen: data.length, attrs: { addr, length: data.length, mode: 'read' } });
    }
    const values = channels.map((c) => {
      try {
        const v = c.convert(data.subarray(c.addr - addr, c.addr - addr + c.length));
        return v === null || v === undefined || !Number.isFinite(v) ? null : v;
      } catch {
        return null;
      }
    });
    log.append(name, t, values, data, latency);
    return Object.fromEntries(channels.map((c, i) => [c.name, values[i]!]));
  }

  /** Log a link change. Returns what the trigger engine has to hear. */
  private setLink(t: number, up: boolean): FeedValues {
    const log = this.drive();
    if (!log.stream(LINK_STREAM)) {
      log.addStream(LINK_STREAM, [column(LINK, { dtype: 'u1', kind: 'flag', decimals: 0 })], { source: 'ecu' });
      this.link = null;
    }
    if (up !== this.link || !up) log.append(LINK_STREAM, t, [up ? 1 : 0]);
    const changed = up !== this.link;
    this.link = up;
    if (up) return { [LINK]: 1 };
    // every value is unknown from here on; say so once, not on every probe
    return changed ? { ...this.unknown, [LINK]: 0 } : { [LINK]: 0 };
  }

  private after(t: number, values: FeedValues | null): void {
    const [fired, done] = this.engine.feed(t, values);
    this.handleFired(fired);
    if (done) this.saveCapture(done);
  }

  private handleFired(fired: Fired[]): void {
    for (const f of fired) {
      const rule = f.rule;
      try {
        this.onFired?.(f);
      } catch {
        /* a listener must not stop the recording */
      }
      if (rule.actions.includes('mark')) {
        this.drive().event(f.t, 'trigger', rule.label, { rule: rule.name, reason: rule.reason, message: rule.message, severity: rule.severity });
      }
      if (rule.actions.includes('notify')) {
        this.notices.push(f);
        try {
          this.onNotice?.(f);
        } catch {
          /* a listener must not stop the recording */
        }
      }
    }
  }

  /** Review found a firing to be a false alarm. */
  private revoked(f: Fired, t: number): void {
    const rule = f.rule;
    this.log?.event(t, 'trigger', rule.cancelLabel || `${rule.label} (cancelled)`, {
      rule: rule.name, reason: rule.cancelKind || 'cancelled', message: rule.cancelMessage,
      revokes_t: Math.round(f.t * 1000) / 1000, severity: 'info',
    });
    const key = `${rule.name}|${f.t}`;
    const log = this.underReview.get(key);
    this.underReview.delete(key);
    if (log && rule.cancelKind) {                   // already saved as a stall: re-file it
      try {
        log.relabelCapture(f.t, rule.name, {
          kind: rule.cancelKind, reason: rule.cancelKind, label: rule.cancelLabel || `${rule.label} (cancelled)`,
          message: rule.cancelMessage || rule.message, severity: 'info',
          meta: { cancelled: true, revoked_by: rule.cancelWhen, revoked_at_s: t.toFixed(3) },
        });
      } catch (err) {
        this.store.error = err as Error;
      }
    }
  }

  private wall(t: number): number {
    return (this.epoch ?? this.startedUnix) + t;
  }

  private saveCapture(capture: Capture): void {
    const first = capture.first;
    const rule = first.rule;
    let { kind, reason, label, message, severity } = rule;
    if (capture.cancelled) {
      if (!rule.cancelKind) return;                 // a false alarm, and the rule wants nothing kept
      kind = reason = rule.cancelKind;
      label = rule.cancelLabel || `${rule.label} (cancelled)`;
      message = rule.cancelMessage || message;
      severity = 'info';
    }
    const wall = this.wall(first.t);
    const log = this.drive();
    const tStart = this.driveT0 === null ? capture.tStart : Math.max(capture.tStart, this.driveT0);
    const fired = capture.fired.map((f) => ({ rule: f.rule.name, t: Math.round(f.t * 1000) / 1000 }));
    const meta: Record<string, unknown> = {
      trigger: rule.name,
      trigger_reason: rule.reason,
      trigger_message: rule.message,
      trigger_time_s: first.t.toFixed(3),
      trigger_wall_time: localIso(wall, 'milliseconds'),
      triggers_fired: fired,
      window: `${pyG(rule.preS)} s before, ${pyG(rule.postS)} s after`,
      window_complete: capture.complete,
      // the 0.1.0 names, which the replay tab and old scripts look for
      stall_trigger: rule.message,
      stall_time_s: first.t.toFixed(3),
      stall_wall_time: localIso(wall, 'milliseconds'),
    };
    if (capture.cancelled) {
      meta.cancelled = true;
      meta.revoked_by = rule.cancelWhen;
    }
    let saved: { ref: DriveRef; rows: number };
    try {
      saved = log.capture(kind, first.t, tStart, capture.tEnd, {
        rule: rule.name, reason, label, message, severity, wallUnix: wall, complete: capture.complete, fired, meta,
      });
    } catch (err) {
      this.store.error = err as Error;              // the capture is lost, the recording goes on
      return;
    }
    const event: CaptureEvent = { ref: saved.ref, reason, t: first.t, rows: saved.rows, rule: rule.name, label, message };
    this.events.push(event);
    try {
      this.onCapture?.(event);
    } catch {
      /* a listener must not stop the recording */
    }
    if (rule.cancelWhen && !capture.cancelled && this.engine.reviewing()) {
      while (this.underReview.size >= 16) this.underReview.delete(this.underReview.keys().next().value!);  // verdicts that never came
      this.underReview.set(`${rule.name}|${first.t}`, log);
    }
  }
}
