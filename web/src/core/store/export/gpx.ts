/**
 * GPX 1.1 of a drive's GPS track (port of the GPX half of
 * hondalog/export/geo.py): one ``trk`` with a ``trkseg`` per stretch without
 * a gap, each ``trkpt`` with its UTC time and speed / heading (and one
 * chosen channel) in a ``hondalog:`` extension; ``wpt`` for every marker,
 * raised alarm and capture. Laid out the way Python's ElementTree writes it.
 *
 * Times need the drive's wall clock (``epoch_unix``); without one the GPX has
 * no times (the positions are still right). Nothing here leaves the device.
 */

import { pyFixed, pyG } from '../pyfmt';
import type { Log } from '../reader';
import { APP_FORMAT } from '../version';
import { type CaptureRow, Track, type Waypoint, logWaypoints } from './track';

export const GPX_NS = 'http://www.topografix.com/GPX/1/1';
export const HL_NS = 'https://github.com/olosnoks/hon-diy-data/gpx/1';
/** how far in time a waypoint may be from a fix and still be placed on it */
export const PLACE_WITHIN_S = 60.0;
/** waypoint kind -> GPX sym */
const SYM: Record<string, string> = { stall: 'Crossing', capture: 'Flag, Blue', alarm: 'Danger Area', marker: 'Flag, Red' };

/** Wall time (unix) of session second 0 of a log, if known. */
export function epochOf(log: Log): number | null {
  for (const key of ['epoch_unix', 'started_unix']) {
    const v = Number(log.meta[key]);
    if (Number.isFinite(v) && v > 0) return v;
  }
  return null;
}

/** What "speed" means for this log: the ECU's vehicle speed if it has readings, else the GPS's. */
export function speedChannel(log: Log): string {
  for (const name of ['vss', 'gps_speed']) {
    if (!log.column(name)) continue;
    const s = log.series(name);
    const finite = Array.from(s.v).filter(Number.isFinite);
    if (finite.length && (name === 'gps_speed' || Math.max(...finite) > 0)) return name;
  }
  return 'gps_speed';
}

/** (lat, lon) of a moment: on the track, else the nearest fix in time. */
export function place(track: Track, t: number): [number, number] | null {
  const pos = track.at(t);
  if (pos) return pos;
  if (track.empty) return null;
  let best = 0;
  for (let i = 1; i < track.length; i++) if (Math.abs(track.t[i]! - t) < Math.abs(track.t[best]! - t)) best = i;
  return Math.abs(track.t[best]! - t) <= PLACE_WITHIN_S ? [track.lat[best]!, track.lon[best]!] : null;
}

/** ``datetime.fromtimestamp(unix, timezone.utc).isoformat(timespec="milliseconds")`` with Z. */
export function isoUtc(unix: number): string {
  return new Date(Math.floor(Math.round(unix * 1e6) / 1000)).toISOString();
}

function esc(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function attr(text: string): string {
  return esc(text).replace(/"/g, '&quot;').replace(/\n/g, '&#10;');
}

function el(tag: string, text: string): string {
  return `<${tag}>${esc(text)}</${tag}>`;
}

/** Index runs of the track without a gap. */
export function segments(track: Track): number[][] {
  const out: number[][] = [];
  let cur: number[] = [];
  for (let i = 0; i < track.length; i++) {
    if (track.breaks[i] && cur.length) {
      out.push(cur);
      cur = [];
    }
    cur.push(i);
  }
  if (cur.length) out.push(cur);
  return out;
}

const f7 = (x: number) => pyFixed(x, 7);

/** ``extra``: (channel, units, value at every fix) written into each trkpt's extension. */
export function writeGpx(track: Track, waypoints: Waypoint[] = [], opts: { name?: string; epoch?: number | null; extra?: [string, string, Float64Array] | null } = {}): string {
  const name = opts.name || 'hondalog drive';
  const epoch = opts.epoch ?? null;
  const parts: string[] = [];
  parts.push("<?xml version='1.0' encoding='UTF-8'?>\n");
  parts.push(`<gpx xmlns="${GPX_NS}" xmlns:hondalog="${HL_NS}" version="1.1" creator="${attr(APP_FORMAT)}">`);
  parts.push('<metadata>' + el('name', name));
  parts.push(el('desc', epoch !== null ? 'Recorded by hondalog (GPS from the handheld). Times are UTC.'
    : "Recorded by hondalog. The drive's wall clock is not known: no times."));
  if (epoch !== null && track.length) parts.push(el('time', isoUtc(epoch + track.t[0]!)));
  if (track.length) {
    const mn = (a: Float64Array) => Math.min(...a), mx = (a: Float64Array) => Math.max(...a);
    parts.push(`<bounds minlat="${f7(mn(track.lat))}" minlon="${f7(mn(track.lon))}" maxlat="${f7(mx(track.lat))}" maxlon="${f7(mx(track.lon))}" />`);
  }
  parts.push('</metadata>');
  for (const w of waypoints) {
    const where = place(track, w.t);
    if (!where) continue;
    let s = `<wpt lat="${f7(where[0])}" lon="${f7(where[1])}">`;
    if (epoch !== null) s += el('time', isoUtc(epoch + w.t));
    s += el('name', w.name);
    if (w.text && w.text !== w.name) s += el('desc', w.text);
    s += el('sym', SYM[w.kind] ?? SYM.marker!) + el('type', w.kind) + '</wpt>';
    parts.push(s);
  }
  parts.push('<trk>' + el('name', name) + el('type', 'driving'));
  for (const seg of segments(track)) {
    parts.push('<trkseg>');
    for (const i of seg) {
      let s = `<trkpt lat="${f7(track.lat[i]!)}" lon="${f7(track.lon[i]!)}">`;
      if (epoch !== null) s += el('time', isoUtc(epoch + track.t[i]!));
      const items: [string, string][] = [];
      if (Number.isFinite(track.speed[i]!)) items.push(['speed_kmh', pyFixed(track.speed[i]!, 1)]);
      if (Number.isFinite(track.heading[i]!)) items.push(['heading', pyFixed(track.heading[i]!, 0)]);
      if (opts.extra && Number.isFinite(opts.extra[2][i]!)) items.push([opts.extra[0], pyG(opts.extra[2][i]!, 4)]);
      if (items.length) s += '<extensions>' + items.map(([k, v]) => `<hondalog:${k}>${esc(v)}</hondalog:${k}>`).join('') + '</extensions>';
      parts.push(s + '</trkpt>');
    }
    parts.push('</trkseg>');
  }
  parts.push('</trk></gpx>');
  return parts.join('');
}

/**
 * A log's GPS track as GPX text. ``channel`` is added to every point
 * (default: speed). Throws when the log has no GPS fixes.
 */
export function exportGpx(log: Log, opts: { channel?: string | null; t0?: number | null; t1?: number | null; captures?: CaptureRow[] | null } = {}): string {
  const track = Track.fromLog(log).cut(opts.t0, opts.t1);
  if (track.empty) throw new Error(`${log.name || 'this log'}: no GPS fixes to export (the GPS comes from the handheld)`);
  const epoch = epochOf(log);
  const lo = track.t[0]!, hi = track.t[track.length - 1]!;
  const pins = logWaypoints(log, opts.captures ?? null).filter((w) => lo - PLACE_WITHIN_S <= w.t && w.t <= hi + PLACE_WITHIN_S);
  const name = log.name || 'hondalog drive';
  let channel = opts.channel ?? null;
  if (channel === null) channel = speedChannel(log);
  else if (!log.column(channel)) throw new Error(`no channel ${JSON.stringify(channel)} in ${name}`);
  let extra: [string, string, Float64Array] | null = null;
  if (channel !== 'gps_speed' && log.column(channel)) {
    const s = log.series(channel);
    extra = [channel, log.units(channel), track.sample(s.t, s.v)];
  }
  return writeGpx(track, pins, { name, epoch, extra });
}
