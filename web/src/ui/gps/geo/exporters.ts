/**
 * GPS exports (port of hondalog/export/geo.py and geojson() of
 * hondalog/geo/stalls.py): GPX 1.1, KML 2.2 and GeoJSON (RFC 7946) as text.
 *
 * GPX: one trk with a trkseg per stretch without a gap, each trkpt with its
 * UTC time, speed and heading (and the chosen channel) in a hondalog:
 * extension; wpt for every pin, in the schema's order (metadata, wpt, trk).
 * KML: the track cut into pieces of one colour each, the map's ramp and range.
 * Nothing here touches the network: the text is saved on the device.
 */
import type { LoadedDrive } from '../../../core/types';
import { binColours, bins, valueRange, type RGB } from './ramps';
import { place, PLACE_WITHIN_S, Track } from './track';
import { driveWaypoints, type Waypoint } from './waypoints';

export const GENERATOR = 'hondalog web';
export const GPX_NS = 'http://www.topografix.com/GPX/1/1';
export const KML_NS = 'http://www.opengis.net/kml/2.2';
export const HL_NS = 'https://github.com/olosnoks/hon-diy-data/gpx/1';
export const KML_BINS = 16;
/** waypoint kind -> [KML icon colour aabbggrr, GPX sym] */
export const PIN_STYLE: Record<string, [string, string]> = {
  stall: ['ff2000b0', 'Crossing'],
  nearstall: ['ff00a5ff', 'Bell'],
  dropout: ['ffa0a0a0', 'Navaid, White'],
  capture: ['ffa83f0a', 'Flag, Blue'],
  alarm: ['ff00d0ff', 'Danger Area'],
  marker: ['ff000000', 'Flag, Red'],
};

export function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export function iso(unix: number): string {
  return new Date(Math.round(unix * 1000)).toISOString();
}

const f7 = (v: number) => v.toFixed(7);

/** Python's "{:.4g}" */
export function g4(v: number): string {
  if (v === 0) return '0';
  const e = Math.floor(Math.log10(Math.abs(v)));
  if (e < -5 || e >= 4) {
    const [m, x] = v.toExponential(3).split('e');
    const mant = String(Number(m));
    const exp = Number(x);
    return `${mant}e${exp < 0 ? '-' : '+'}${String(Math.abs(exp)).padStart(2, '0')}`;
  }
  return String(Number(v.toPrecision(4)));
}

export interface GpxOptions {
  name?: string;
  epoch?: number | null;
  /** [channel, units, value at every fix] written into each trkpt's extension */
  extra?: [string, string, ArrayLike<number>] | null;
}

export function writeGpx(track: Track, waypoints: Waypoint[] = [], opts: GpxOptions = {}): string {
  const name = opts.name || 'hondalog drive';
  const epoch = opts.epoch ?? null;
  const L: string[] = [];
  L.push(`<?xml version="1.0" encoding="UTF-8"?>`);
  L.push(`<gpx xmlns="${GPX_NS}" xmlns:hondalog="${HL_NS}" version="1.1" creator="${GENERATOR}">`);
  L.push('<metadata>');
  L.push(`<name>${esc(name)}</name>`);
  L.push(`<desc>${epoch != null ? 'Recorded by hondalog (GPS from the handheld). Times are UTC.'
    : 'Recorded by hondalog. The drive\'s wall clock is not known: no times.'}</desc>`);
  if (epoch != null && track.length) L.push(`<time>${iso(epoch + track.t[0]!)}</time>`);
  if (track.length) {
    let a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
    for (let i = 0; i < track.length; i++) {
      a = Math.min(a, track.lat[i]!); b = Math.min(b, track.lon[i]!);
      c = Math.max(c, track.lat[i]!); d = Math.max(d, track.lon[i]!);
    }
    L.push(`<bounds minlat="${f7(a)}" minlon="${f7(b)}" maxlat="${f7(c)}" maxlon="${f7(d)}"/>`);
  }
  L.push('</metadata>');
  for (const w of waypoints) {
    const where = place(track, w.t);
    if (!where) continue;
    L.push(`<wpt lat="${f7(where[0])}" lon="${f7(where[1])}">`);
    if (epoch != null) L.push(`<time>${iso(epoch + w.t)}</time>`);
    L.push(`<name>${esc(w.name)}</name>`);
    if (w.text && w.text !== w.name) L.push(`<desc>${esc(w.text)}</desc>`);
    L.push(`<sym>${esc((PIN_STYLE[w.kind] ?? PIN_STYLE.marker!)[1])}</sym>`);
    L.push(`<type>${w.kind}</type>`);
    L.push('</wpt>');
  }
  L.push('<trk>');
  L.push(`<name>${esc(name)}</name>`);
  L.push('<type>driving</type>');
  for (const [a, b] of track.segments()) {
    L.push('<trkseg>');
    for (let i = a; i < b; i++) {
      let pt = `<trkpt lat="${f7(track.lat[i]!)}" lon="${f7(track.lon[i]!)}">`;
      if (epoch != null) pt += `<time>${iso(epoch + track.t[i]!)}</time>`;
      const items: [string, string][] = [];
      if (Number.isFinite(track.speed[i])) items.push(['speed_kmh', track.speed[i]!.toFixed(1)]);
      if (Number.isFinite(track.heading[i])) items.push(['heading', track.heading[i]!.toFixed(0)]);
      const ex = opts.extra;
      if (ex && Number.isFinite(ex[2][i])) items.push([ex[0], g4(ex[2][i]!)]);
      if (items.length) {
        pt += '<extensions>' + items.map(([k, v]) => `<hondalog:${k}>${esc(v)}</hondalog:${k}>`).join('') + '</extensions>';
      }
      L.push(pt + '</trkpt>');
    }
    L.push('</trkseg>');
  }
  L.push('</trk>');
  L.push('</gpx>');
  return L.join('\n') + '\n';
}

export function kmlColour(rgb: RGB): string {
  const [r, g, b] = rgb.map((c) => Math.round(c * 255).toString(16).padStart(2, '0'));
  return `ff${b}${g}${r}`;
}

export interface KmlOptions {
  name?: string; channel?: string; units?: string;
  lo?: number | null; hi?: number | null;
  waypoints?: Waypoint[]; epoch?: number | null; width?: number;
}

export function writeKml(track: Track, values: ArrayLike<number> | null, opts: KmlOptions = {}): string {
  const name = opts.name || 'hondalog drive';
  const channel = opts.channel || '';
  const width = String(opts.width ?? 5);
  const vals = values ?? new Float64Array(track.length).fill(NaN);
  const [lo, hi, diverging] = valueRange(channel, vals, opts.lo, opts.hi);
  const colours = binColours(KML_BINS, diverging);
  const epoch = opts.epoch ?? null;
  const when = epoch != null && track.length ? ` Started ${new Date((epoch + track.t[0]!) * 1000).toISOString().slice(0, 16).replace('T', ' ')} UTC.` : '';
  const L: string[] = [];
  L.push(`<?xml version="1.0" encoding="UTF-8"?>`);
  L.push(`<kml xmlns="${KML_NS}">`);
  L.push('<Document>');
  L.push(`<name>${esc(name)}</name>`);
  L.push(`<description>${esc(`Coloured by ${channel || 'nothing'}${opts.units ? ` (${opts.units})` : ''}: `
    + `${diverging ? 'blue' : 'dark blue'} ${g4(lo)} to ${diverging ? 'orange' : 'yellow'} ${g4(hi)}`
    + `${diverging ? ' (0 in the middle)' : ''}; grey = no value.${when} Exported by ${GENERATOR}.`)}</description>`);
  for (let k = 0; k < KML_BINS; k++) {
    L.push(`<Style id="bin${k}"><LineStyle><color>${kmlColour(colours[k]!)}</color><width>${width}</width></LineStyle></Style>`);
  }
  L.push(`<Style id="nodata"><LineStyle><color>ff8c8c8c</color><width>${width}</width></LineStyle></Style>`);
  for (const [kind, [colour]] of Object.entries(PIN_STYLE)) {
    L.push(`<Style id="pin_${kind}"><IconStyle><color>${colour}</color></IconStyle></Style>`);
  }
  L.push('<Folder>');
  L.push(`<name>${esc(channel ? `Track (${channel})` : 'Track')}</name>`);
  const b = bins(vals, lo, hi, KML_BINS);
  for (const [s0, s1] of track.segments()) {
    const seg = s1 - s0;
    if (seg < 2) continue;
    let start = 0;
    for (let j = 1; j <= seg; j++) {
      if (j === seg || b[s0 + j] !== b[s0 + start] || j - start >= 500) {
        const end = Math.min(j + 1, seg);
        if (end - start >= 2) {
          const k = b[s0 + start]!;
          const coords: string[] = [];
          for (let i = s0 + start; i < s0 + end; i++) coords.push(`${f7(track.lon[i]!)},${f7(track.lat[i]!)}`);
          L.push(`<Placemark><styleUrl>${k >= 0 ? `#bin${k}` : '#nodata'}</styleUrl><LineString><tessellate>1</tessellate><coordinates>${coords.join(' ')}</coordinates></LineString></Placemark>`);
        }
        start = j;
      }
    }
  }
  L.push('</Folder>');
  const wps = opts.waypoints ?? [];
  if (wps.length) {
    L.push('<Folder>');
    L.push('<name>Markers, alarms and captures</name>');
    for (const w of wps) {
      const where = place(track, w.t);
      if (!where) continue;
      let pm = `<Placemark><name>${esc(w.name)}</name>`;
      if (w.text) pm += `<description>${esc(w.text)}</description>`;
      if (epoch != null) pm += `<TimeStamp><when>${iso(epoch + w.t)}</when></TimeStamp>`;
      pm += `<styleUrl>#pin_${w.kind in PIN_STYLE ? w.kind : 'marker'}</styleUrl>`;
      pm += `<Point><coordinates>${f7(where[1])},${f7(where[0])}</coordinates></Point></Placemark>`;
      L.push(pm);
    }
    L.push('</Folder>');
  }
  L.push('</Document>');
  L.push('</kml>');
  return L.join('\n') + '\n';
}

export interface GeoJsonOptions { name?: string; epoch?: number | null; channel?: string; values?: ArrayLike<number> | null }

/** The drive as GeoJSON: one LineString per stretch without a gap, one Point per pin. */
export function writeGeoJson(track: Track, waypoints: Waypoint[] = [], opts: GeoJsonOptions = {}): string {
  const features: unknown[] = [];
  for (const [a, b] of track.segments()) {
    const coords: number[][] = [];
    const times: (string | number)[] = [];
    const vals: (number | null)[] = [];
    for (let i = a; i < b; i++) {
      coords.push([round7(track.lon[i]!), round7(track.lat[i]!)]);
      times.push(opts.epoch != null ? iso(opts.epoch + track.t[i]!) : round3(track.t[i]!));
      if (opts.values) vals.push(Number.isFinite(opts.values[i]) ? Number(g4(opts.values[i]!)) : null);
    }
    const properties: Record<string, unknown> = { kind: 'track', name: opts.name || 'hondalog drive', times };
    if (opts.values && opts.channel) properties[opts.channel] = vals;
    features.push({ type: 'Feature', geometry: coords.length > 1 ? { type: 'LineString', coordinates: coords } : { type: 'Point', coordinates: coords[0] }, properties });
  }
  for (const w of waypoints) {
    const where = place(track, w.t);
    if (!where) continue;
    features.push({
      type: 'Feature', geometry: { type: 'Point', coordinates: [round7(where[1]), round7(where[0])] },
      properties: { kind: w.kind, name: w.name, text: w.text, t: round3(w.t), ...(opts.epoch != null ? { time: iso(opts.epoch + w.t) } : {}) },
    });
  }
  return JSON.stringify({ type: 'FeatureCollection', generator: GENERATOR, features }, null, 1);
}

const round7 = (v: number) => Math.round(v * 1e7) / 1e7;
const round3 = (v: number) => Math.round(v * 1e3) / 1e3;

// ---- a whole drive ---------------------------------------------------------------

export type GeoFormat = 'gpx' | 'kml' | 'geojson';

/** What "speed" means for this drive: the ECU's vehicle speed if it has
 *  readings, else the GPS's. */
export function speedChannel(drive: Pick<LoadedDrive, 'series'>): string {
  const vss = drive.series?.vss;
  if (vss && vss.v.length) {
    let max = -Infinity;
    for (const v of vss.v) if (Number.isFinite(v)) max = Math.max(max, v);
    if (max > 0) return 'vss';
  }
  return 'gps_speed';
}

/** A channel's (t, v) in a drive, the GPS speed included. */
export function channelSeries(drive: LoadedDrive, channel: string): { t: ArrayLike<number>; v: ArrayLike<number> } | null {
  if (channel === 'gps_speed') return drive.gps?.t?.length ? { t: drive.gps.t, v: drive.gps.speedKmh } : null;
  if (channel === 'gps_heading') return drive.gps?.t?.length ? { t: drive.gps.t, v: drive.gps.heading } : null;
  return drive.series?.[channel] ?? null;
}

export function channelUnits(drive: LoadedDrive, channel: string): string {
  if (channel === 'gps_speed') return 'km/h';
  const spec = drive.map?.channels.find((c) => c.name === channel) ?? drive.map?.math.find((m) => m.name === channel);
  return spec?.units ?? '';
}

/** Wall time of the drive's second 0 (the store's startedUnix), when known. */
export function epochOf(drive: LoadedDrive): number | null {
  const v = drive.info?.startedUnix;
  return typeof v === 'number' && v > 0 ? v : null;
}

/** The drive's track as text in fmt. Throws when the drive has no GPS fixes. */
export function exportDrive(drive: LoadedDrive, fmt: GeoFormat, channel?: string | null): { text: string; mime: string; filename: string } {
  const track = Track.fromDrive(drive);
  const name = drive.info?.name || 'hondalog drive';
  if (track.empty) throw new Error(`${name}: no GPS fixes to export (the GPS comes from the handheld)`);
  const epoch = epochOf(drive);
  const lo = track.t[0]! - PLACE_WITHIN_S, hi = track.t[track.length - 1]! + PLACE_WITHIN_S;
  const pins = driveWaypoints(drive).filter((w) => w.t >= lo && w.t <= hi);
  const ch = channel || speedChannel(drive);
  const series = channelSeries(drive, ch);
  if (!series) throw new Error(`no channel ${ch} in ${name}`);
  const values = track.sample(series.t, series.v);
  const units = channelUnits(drive, ch);
  if (fmt === 'gpx') {
    const extra: [string, string, Float64Array] | null = ch !== 'gps_speed' ? [ch, units, values] : null;
    return { text: writeGpx(track, pins, { name, epoch, extra }), mime: 'application/gpx+xml', filename: `${name}.gpx` };
  }
  if (fmt === 'kml') {
    return { text: writeKml(track, values, { name, channel: ch, units, waypoints: pins, epoch }), mime: 'application/vnd.google-earth.kml+xml', filename: `${name}.kml` };
  }
  return { text: writeGeoJson(track, pins, { name, epoch, channel: ch, values }), mime: 'application/geo+json', filename: `${name}.geojson` };
}
