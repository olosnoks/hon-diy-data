/**
 * The Replay screen's brain, without any UI (port of
 * hondalog/analysis/replay.py `ReplaySession` and hondalog/ui/replaymodel.py
 * `ReplayModel`).
 *
 * ReplaySession works in drive seconds and channel names: the view range,
 * one min/max envelope per channel and pixel width, two cursors, readouts,
 * event jumps, a second drive laid over the first.
 *
 * ReplayModel is the screen's view of it: time counted from an origin (the
 * stall trigger of a capture), UI lane ids mapped to channel names with a
 * scale (the black box tach is pulses per second on screen, rpm in the log),
 * the window clamped to the drive and never narrower than MIN_WINDOW_S.
 */
import type { LoadedDrive } from '../types';
import { Comparison, type AlignHow, type AlignOptions } from './compare';
import { spanStats, stepSample, valueAt, type SpanStats } from './cursors';
import { envelope, LodPyramid, polyline, type Envelope } from './lod';
import { driveSpan, getSeries, searchSorted, type Series } from './series';

export const PYRAMID_FROM = 200_000;
export interface DriveEvent { t: number; kind: string; text: string }
export interface Readout { name: string; units: string; value: number; stats: SpanStats | null }

export class ReplaySession {
  view: [number, number];
  cursorA: number;
  cursorB: number | null = null;
  overlay: Comparison | null = null;
  /** moments the viewer worked out itself, listed and jumped to with the drive's own */
  bookmarks: DriveEvent[] = [];
  readonly t0: number;
  readonly t1: number;
  private pyramids = new Map<string, LodPyramid>();

  constructor(readonly drive: LoadedDrive) {
    [this.t0, this.t1] = driveSpan(drive);
    this.view = [this.t0, Math.max(this.t1, this.t0 + 1e-3)];
    this.cursorA = this.t0;
  }

  get channels(): string[] { return Object.keys(this.drive.series); }

  // -- view --
  setView(t0: number, t1: number): void { if (t1 > t0) this.view = [t0, t1]; }
  showAll(): void { this.view = [this.t0, Math.max(this.t1, this.t0 + 1e-3)]; }
  /** factor < 1 zooms in; the time `around` stays where it is on screen. */
  zoom(factor: number, around?: number): void {
    const [t0, t1] = this.view;
    const c = around ?? (t0 + t1) / 2;
    this.setView(c - (c - t0) * factor, c + (t1 - c) * factor);
  }
  pan(fraction: number): void {
    const [t0, t1] = this.view;
    const s = (t1 - t0) * fraction;
    this.view = [t0 + s, t1 + s];
  }

  // -- drawing --
  private env(series: Series, columns: number, key: number): Envelope {
    if (series.t.length >= PYRAMID_FROM) {
      const k = `${key}:${series.name}`;
      let p = this.pyramids.get(k);
      if (!p) { p = new LodPyramid(series.t, series.v); this.pyramids.set(k, p); }
      return p.envelope(this.view[0], this.view[1], columns);
    }
    return envelope(series.t, series.v, this.view[0], this.view[1], columns);
  }

  /** What to draw for one channel in the current view. */
  trace(name: string, columns = 1000): Envelope { return this.env(getSeries(this.drive, name), columns, 0); }

  overlayTrace(name: string, columns = 1000): Envelope | null {
    if (!this.overlay || !(name in this.overlay.b.series)) return null;
    return this.env(this.overlay.shifted(name), columns, 1);
  }

  private dropOverlayPyramids(): void {
    for (const k of [...this.pyramids.keys()]) if (k.startsWith('1:')) this.pyramids.delete(k);
  }

  /** Lay a second drive over this one; returns the offset applied. */
  setOverlay(other: LoadedDrive | null, how: AlignHow = 'start', opts: AlignOptions = {}): number {
    this.dropOverlayPyramids();
    if (!other) { this.overlay = null; return 0; }
    this.overlay = new Comparison(this.drive, other);
    try { return this.overlay.align(how, opts); } catch { return this.overlay.align('start'); }
  }

  /** Line the overlay up on another moment; throws (changing nothing) if a drive lacks it. */
  alignOverlay(how: AlignHow = 'start', opts: AlignOptions = {}): number {
    if (!this.overlay) return 0;
    const off = this.overlay.align(how, opts);
    this.dropOverlayPyramids();
    return off;
  }

  shiftOverlay(seconds: number): number {
    if (!this.overlay) return 0;
    this.overlay.offset += seconds;
    this.dropOverlayPyramids();
    return this.overlay.offset;
  }

  // -- cursors --
  setCursor(t: number, which: 'a' | 'b' = 'a'): void {
    const c = Math.min(Math.max(t, this.t0), this.t1);
    if (which === 'a') this.cursorA = c; else this.cursorB = c;
  }
  clearCursorB(): void { this.cursorB = null; }

  /** Move a cursor one sample (of one channel, or of any stream so no read is skipped). */
  step(direction: number, channel?: string | null, which: 'a' | 'b' = 'a'): number {
    const now = which === 'a' || this.cursorB === null ? this.cursorA : this.cursorB;
    let target: number;
    if (channel) target = stepSample(getSeries(this.drive, channel), now, direction);
    else {
      let best: number | null = null;
      for (const s of Object.values(this.drive.series)) {
        const t = s.t;
        if (!t.length) continue;
        const i = direction > 0 ? searchSorted(t, now, 'right') : searchSorted(t, now, 'left') - 1;
        if (i >= 0 && i < t.length) {
          const c = t[i]!;
          if (best === null || (direction > 0 ? c < best : c > best)) best = c;
        }
      }
      target = best ?? now;
    }
    this.setCursor(target, which);
    return target;
  }

  readouts(names?: string[], full = true): Readout[] {
    return (names ?? this.channels).map((name) => {
      const s = getSeries(this.drive, name);
      return {
        name, units: s.units ?? '', value: valueAt(s, this.cursorA),
        stats: this.cursorB !== null ? spanStats(s, this.cursorA, this.cursorB, full) : null,
      };
    });
  }

  get cursorDelta(): number | null { return this.cursorB === null ? null : this.cursorB - this.cursorA; }

  // -- events --
  addEvent(t: number, kind: string, text = ''): DriveEvent {
    const e = { t, kind, text };
    this.bookmarks.push(e);
    return e;
  }
  clearEvents(kind?: string): void { this.bookmarks = kind === undefined ? [] : this.bookmarks.filter((e) => e.kind !== kind); }

  /** Markers, triggers and alarms in time order, bookmarks among them; `kinds` narrows both. */
  eventList(...kinds: string[]): DriveEvent[] {
    const want = kinds.length ? kinds : ['marker', 'trigger', 'alarm'];
    const own = this.drive.events.filter((e) => want.includes(e.kind));
    const marks = this.bookmarks.filter((e) => !kinds.length || kinds.includes(e.kind));
    return [...own, ...marks].sort((a, b) => a.t - b.t);
  }

  /** Cursor A to the next/previous event; the view follows. null if none. */
  jumpEvent(direction: number, ...kinds: string[]): number | null {
    const times = this.eventList(...kinds).map((e) => e.t);
    let target: number | null = null;
    if (direction > 0) target = times.find((t) => t > this.cursorA + 1e-6) ?? null;
    else { const earlier = times.filter((t) => t < this.cursorA - 1e-6); target = earlier.length ? earlier[earlier.length - 1]! : null; }
    if (target !== null) {
      this.setCursor(target);
      const span = this.view[1] - this.view[0];
      if (!(this.view[0] <= target && target <= this.view[1])) this.view = [target - span / 2, target + span / 2];
    }
    return target;
  }
}

// ---------------------------------------------------------------- the screen's model

export const MIN_WINDOW_S = 0.25;
/** how a second drive can be laid over the first: [key, menu text] */
export const ALIGNMENTS: [AlignHow, string][] = [
  ['start', 'the first sample'], ['marker', 'the first marker'],
  ['trigger', 'the stall (trigger)'], ['crossing', 'RPM rising through 400'],
];
export const ALIGN_SHORT: Record<AlignHow, string> = { start: 'start', marker: 'marker', trigger: 'stall', crossing: 'rpm 400' };
const ALIGN_OPTIONS: Partial<Record<AlignHow, AlignOptions>> = { crossing: { channel: 'rpm', level: 400, rising: true } };

export interface LaneReadout { value: number; stats: SpanStats | null }
export interface Trace { x: Float64Array; y: Float64Array; reduced: boolean }

export interface ReplayModelOptions {
  /** session seconds that read 0 on screen; default: the trigger of a capture, else 0 */
  origin?: number;
  /** UI lane id -> [channel name, scale] */
  names?: Record<string, [string, number]>;
  /** view-time span to allow; default the whole drive */
  span?: [number, number];
  title?: string;
}

/** Where a drive's screen time starts: a capture's trigger, else 0. */
export function defaultOrigin(drive: LoadedDrive): number {
  if (drive.info.kind === 'drive') return 0;
  const trig = drive.events.filter((e) => e.kind === 'trigger').sort((a, b) => a.t - b.t)[0];
  return trig ? trig.t : 0;
}

export class ReplayModel {
  readonly session: ReplaySession;
  readonly origin: number;
  readonly names: Record<string, [string, number]>;
  readonly span: [number, number];
  readonly title: string;
  overlayName = '';
  align: AlignHow | '' = '';

  constructor(readonly drive: LoadedDrive, opts: ReplayModelOptions = {}) {
    this.session = new ReplaySession(drive);
    this.origin = opts.origin ?? defaultOrigin(drive);
    this.names = { ...(opts.names ?? {}) };
    this.title = opts.title ?? drive.info.name;
    const s = this.session;
    let [lo, hi] = opts.span ?? [s.t0 - this.origin, s.t1 - this.origin];
    if (s.t1 > s.t0 || Object.keys(drive.series).length) {
      const [l2, h2] = [Math.max(lo, s.t0 - this.origin), Math.min(hi, s.t1 - this.origin)];
      if (h2 > l2) [lo, hi] = [l2, h2];
    }
    this.span = [lo, hi];
    this.setWindow(lo, hi);
    this.session.setCursor(lo + this.origin);
  }

  private nameOf(cid: string): [string, number] { return this.names[cid] ?? [cid, 1]; }
  has(cid: string): boolean { return (this.drive.series[this.nameOf(cid)[0]]?.t.length ?? 0) > 0; }

  // ---- window ----
  get window(): [number, number] {
    const [t0, t1] = this.session.view;
    return [t0 - this.origin, t1 - this.origin];
  }

  /** Show [t0, t1], kept inside the drive and no narrower than MIN_WINDOW_S. */
  setWindow(t0: number, t1: number): [number, number] {
    const [lo, hi] = this.span;
    const width = Math.min(Math.max(t1 - t0, MIN_WINDOW_S), hi - lo);
    const a = Math.min(Math.max(lo, t0), hi - width);
    this.session.setView(a + this.origin, a + width + this.origin);
    return this.window;
  }

  zoom(factor: number, around?: number): [number, number] {
    const [t0, t1] = this.window;
    const [lo, hi] = this.span;
    const f = Math.min(Math.max(factor, MIN_WINDOW_S / (t1 - t0)), (hi - lo) / (t1 - t0));
    this.session.zoom(f, around === undefined ? undefined : around + this.origin);
    return this.setWindow(...this.window);
  }

  pan(fraction: number): [number, number] {
    const [t0, t1] = this.window;
    const s = (t1 - t0) * fraction;
    return this.setWindow(t0 + s, t1 + s);
  }

  showAll(): [number, number] { return this.setWindow(...this.span); }

  /** Scroll so `t` is in the window; true if the window moved. */
  reveal(t: number): boolean {
    const [t0, t1] = this.window;
    if (t0 <= t && t <= t1) return false;
    const w = t1 - t0;
    this.setWindow(t - w / 2, t + w / 2);
    return true;
  }

  // ---- drawing ----
  private line(env: Envelope, scale: number): Trace {
    const [x, y] = polyline(env);
    return { x: x.map((v) => v - this.origin), y: y.map((v) => v * scale), reduced: env.reduced };
  }

  /** One lane in the current window: raw samples when they fit, else one
   *  min/max pair per pixel column (a dropout one sample long is still drawn). */
  trace(cid: string, columns: number): Trace {
    const [name, scale] = this.nameOf(cid);
    return this.line(this.session.trace(name, columns), scale);
  }

  overlayTrace(cid: string, columns: number): Trace | null {
    const [name, scale] = this.nameOf(cid);
    const env = this.session.overlayTrace(name, columns);
    return !env || !env.t.length ? null : this.line(env, scale);
  }

  // ---- cursors ----
  get cursorA(): number { return this.session.cursorA - this.origin; }
  get cursorB(): number | null { const b = this.session.cursorB; return b === null ? null : b - this.origin; }
  setCursor(t: number, which: 'a' | 'b' = 'a'): number {
    this.session.setCursor(t + this.origin, which);
    return which === 'a' ? this.cursorA : (this.cursorB as number);
  }
  clearCursorB(): void { this.session.clearCursorB(); }
  get delta(): number | null { return this.session.cursorDelta; }
  step(direction: number, cid?: string): number {
    return this.session.step(direction, cid ? this.nameOf(cid)[0] : null) - this.origin;
  }

  /** Per lane: value at cursor A, A-to-B statistics when B is set, in the lane's unit. */
  readouts(cids: string[], full = true): Record<string, LaneReadout> {
    const known = cids.filter((c) => this.has(c));
    const rows = this.session.readouts(known.map((c) => this.nameOf(c)[0]), full);
    const out: Record<string, LaneReadout> = {};
    known.forEach((cid, i) => {
      const scale = this.nameOf(cid)[1];
      const row = rows[i]!;
      let stats = row.stats;
      if (stats && scale !== 1) {
        stats = { ...stats, a: stats.a * scale, b: stats.b * scale, delta: stats.delta * scale, min: stats.min * scale,
          max: stats.max * scale, mean: stats.mean * scale, rate: stats.rate * scale };
      }
      out[cid] = { value: row.value * scale, stats };
    });
    return out;
  }

  // ---- events ----
  addEvent(t: number, kind: string, text = ''): void { this.session.addEvent(t + this.origin, kind, text); }
  events(): [number, string, string][] {
    return this.session.eventList().map((e) => [e.t - this.origin, e.kind, e.text || e.kind]);
  }
  jump(direction: number): number | null {
    const target = this.session.jumpEvent(direction);
    if (target === null) return null;
    this.setWindow(...this.window);
    return target - this.origin;
  }

  // ---- a second drive ----
  /** Lay another drive over this one, lined up on `how`; returns what it was
   *  really aligned on ("start" when a drive lacks the moment). null removes it. */
  setOverlay(other: ReplayModel | LoadedDrive | null, how: AlignHow = 'start'): AlignHow | '' {
    if (!other) { this.session.setOverlay(null); this.overlayName = ''; this.align = ''; return ''; }
    const model = other instanceof ReplayModel ? other : new ReplayModel(other);
    this.session.setOverlay(model.drive, 'start');
    this.overlayName = model.title;
    return this.alignOverlay(how);
  }

  alignOverlay(how: AlignHow): AlignHow | '' {
    if (!this.session.overlay) return '';
    try { this.session.alignOverlay(how, ALIGN_OPTIONS[how] ?? {}); this.align = how; }
    catch { this.session.alignOverlay('start'); this.align = 'start'; }
    return this.align;
  }

  get overlayOffset(): number { return this.session.overlay?.offset ?? 0; }
  nudgeOverlay(seconds: number): number { return this.session.shiftOverlay(seconds); }
}
