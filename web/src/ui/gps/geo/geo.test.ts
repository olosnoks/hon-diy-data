// Port of tests/test_geo.py (projection, tracks, waypoints, stall groups) and
// tests/test_geo_export.py (GPX, KML, GeoJSON) on a synthetic track.
import { describe, expect, it } from 'vitest';
import * as proj from './proj';
import { GAP_S, hold, place, Track } from './track';
import { binColours, bins, isDiverging, valueRange } from './ramps';
import { exportDrive, KML_BINS, kmlColour, speedChannel, writeGeoJson, writeGpx, writeKml, g4 } from './exporters';
import { classify, driveWaypoints, markLabel } from './waypoints';
import { clusters, stallsGeojson, summary, type StallPin } from './stalls';
import { synthDrive, SYNTH_ORIGIN } from '../fixtures';

const ORIGIN = SYNTH_ORIGIN;

function straight(n = 600, hz = 1, kmh = 36, gapAt: number | null = null, gapS = 20): Track {
  const t = Array.from({ length: n }, (_, i) => i / hz).map((x) => (gapAt != null && x >= gapAt ? x + gapS : x));
  const lat = t.map(() => ORIGIN[0]);
  const lon = t.map((x) => ORIGIN[1] + ((x * kmh) / 3.6 / (proj.MEAN_R * Math.cos((ORIGIN[0] * Math.PI) / 180))) * (180 / Math.PI));
  return new Track(t, lat, lon, t.map(() => kmh), t.map(() => 90));
}

describe('projection', () => {
  it('round-trips unit coordinates and finds the slippy-map tile', () => {
    const PEARL: [number, number] = [32.273, -90.132]; // the Python test's check value of the formula
    const [x, y] = proj.toUnit(PEARL[1], PEARL[0]);
    const [lon, lat] = proj.fromUnit(x, y);
    expect(lon).toBeCloseTo(PEARL[1], 9);
    expect(lat).toBeCloseTo(PEARL[0], 9);
    const n = 2 ** 15;
    const wiki = [Math.trunc(((PEARL[1] + 180) / 360) * n),
      Math.trunc(((1 - Math.asinh(Math.tan((PEARL[0] * Math.PI) / 180)) / Math.PI) / 2) * n)];
    expect([Math.trunc(x * n), Math.trunc(y * n)]).toEqual(wiki);
    expect(wiki).toEqual([8179, 13277]);
    expect(proj.metresPerPx(0, 0)).toBeCloseTo(156543.03, 1);
  });

  it('measures distances and makes a scale bar', () => {
    expect(proj.haversineM(32, -90, 33, -90) / 111195).toBeCloseTo(1, 3);
    expect(proj.bearingDeg(32, -90, 32, -89)).toBeCloseTo(90, 0);
    let [px, label] = proj.scaleBar(10, 150);
    expect(label).toBe('1 km');
    expect(px).toBeCloseTo(100);
    [px, label] = proj.scaleBar(10, 150, true);
    expect(label).toBe('0.5 mi');
    expect(px).toBeCloseTo(80.4672, 3);
    expect(proj.scaleBar(0.5, 150, true)[1]).toBe('200 ft');
    const [la, lo] = proj.offset(32.27, -90.13, 1000, 0);
    expect(proj.haversineM(32.27, -90.13, la, lo) / 1000).toBeCloseTo(1, 3);
  });

  it('fits a box inside the view', () => {
    const [x0, y0, x1, y1] = straight(600).bbox();
    const z = proj.fitZoom(x0, y0, x1, y1, 960, 630);
    expect((x1 - x0) * proj.worldPx(z)).toBeLessThanOrEqual(960 - 80);
    expect((x1 - x0) * proj.worldPx(z + 1)).toBeGreaterThan(960 - 80);
  });
});

describe('tracks', () => {
  it('drops no-fix rows and sorts', () => {
    const tr = new Track([3, 1, 2, 4, 5], [32.27, 32.27, NaN, 0, 32.28], [-90.13, -90.13, -90.13, 0, -90.12]);
    expect([...tr.t]).toEqual([1, 3, 5]);
    expect(tr.lat[tr.length - 1]).toBe(32.28);
  });

  it('breaks the line at gaps, with no position inside', () => {
    const tr = straight(300, 1, 36, 100, 30);
    expect(tr.breaks[0]).toBe(1);
    expect(tr.gaps).toBe(1);
    const i = [...tr.breaks].findIndex((b, k) => k > 0 && b === 1);
    expect(tr.t[i]! - tr.t[i - 1]!).toBeGreaterThan(GAP_S);
    expect(tr.at(tr.t[i - 1]! + 2)).not.toBeNull();
    expect(tr.at(tr.t[i - 1]! + 15)).toBeNull();
    expect(tr.lengthM / (10 * 298)).toBeCloseTo(1, 2);
  });

  it('goes from time to place and back', () => {
    const tr = straight(600);
    const pos = tr.at(100.5)!;
    expect(pos.index).toBeCloseTo(100.5);
    expect(pos.lon).toBeCloseTo((tr.lon[100]! + tr.lon[101]!) / 2, 12);
    expect(tr.at(-1)).toBeNull();
    expect(tr.at(1e6)).toBeNull();
    const [t, d] = tr.nearest(pos.x, pos.y + 1e-9)!;
    expect(t).toBeCloseTo(100.5, 3);
    expect(d).toBeLessThan(1e-8);
    expect(tr.nearest(pos.x, pos.y + 1e-3, 1e-6)).toBeNull();
  });

  it('holds a channel at every fix', () => {
    const tr = straight(10);
    const v = tr.sample([0, 2, 2.5, 8], [1, 2, 3, 4], 1);
    expect([...v.slice(0, 4)]).toEqual([1, 1, 2, 3]);
    expect(Number.isNaN(v[4])).toBe(true);
    expect(v[8]).toBe(4);
    expect(Number.isNaN(hold([5], [1], [1])[0])).toBe(true);
  });

  it('decimates by the length in pixels and keeps both ends of a gap', () => {
    const tr = straight(3600, 10, 50);
    for (const z of [12, 14, 16]) {
      const idx = tr.lod(z);
      expect(idx[0]).toBe(0);
      expect(idx[idx.length - 1]).toBe(tr.length - 1);
      const lengthPx = tr.lengthM / proj.metresPerPx(ORIGIN[0], z);
      expect(idx.length).toBeLessThanOrEqual(lengthPx / 1.5 + 10 * (1 + tr.gaps));
      expect(tr.lod(z)).toBe(idx); // cached
    }
    expect(tr.lod(12).length).toBeLessThan(tr.lod(16).length);
    const g = straight(400, 10, 36, 20, 40);
    const keep = new Set(g.lod(8));
    const i = [...g.breaks].findIndex((b, k) => k > 0 && b === 1);
    for (const k of [0, i - 1, i, g.length - 1]) expect(keep.has(k)).toBe(true);
  });

  it('places a moment on the track, else the nearest fix within a minute', () => {
    const tr = straight(100, 1, 36, 50, 100);
    expect(place(tr, 10)![1]).toBeCloseTo(tr.at(10)!.lon, 12);
    expect(place(tr, 100)).not.toBeNull(); // in the gap, 50 s from a fix
    expect(place(tr, 1e5)).toBeNull();
  });
});

describe('ramps', () => {
  it('spreads values like the Python app', () => {
    expect(isDiverging('total_trim')).toBe(true);
    expect(isDiverging('rpm')).toBe(false);
    const vals = Array.from({ length: 101 }, (_, i) => i);
    const [lo, hi] = valueRange('rpm', vals);
    expect(lo).toBeCloseTo(2);
    expect(hi).toBeCloseTo(98);
    expect(valueRange('vss', [10, 20, 30])[0]).toBe(0);
    expect(valueRange('stft', [-3, 5])).toEqual([-4.84, 4.84, true]);
    expect([...bins([NaN, 0, 50, 100], 0, 100, 4)]).toEqual([-1, 0, 2, 3]);
    // the first KML bin colour of the sequential ramp, as numpy computes it
    // golden: hondalog.ramps.bin_colours(16, ...) through export.geo._kml_colour
    expect(binColours(KML_BINS, false).map(kmlColour)).toEqual(['ff532708', 'ff5c3219', 'ff653d29', 'ff6c483a', 'ff6e5449', 'ff6f5f58', 'ff706a67', 'ff727676', 'ff758184', 'ff778d93', 'ff729aa3', 'ff6da7b3', 'ff68b4c3', 'ff5cc3d4', 'ff4ed2e5', 'ff3fe1f6']);
    expect(binColours(KML_BINS, true).map(kmlColour)).toEqual(['ff6a3709', 'ff7d4410', 'ff905217', 'ffa35f1e', 'ffb5783c', 'ffc89c71', 'ffdbc1a7', 'ffeee5dc', 'ffdbe8f4', 'ffa2cbee', 'ff69aee9', 'ff3091e3', 'ff1379d4', 'ff1067bc', 'ff0d56a3', 'ff0a448b']);
    expect(g4(12345.6)).toBe('1.235e+04');
    expect(g4(0.5)).toBe('0.5');
  });
});

describe('waypoints', () => {
  it('names the kinds of events as pins', () => {
    expect(classify('marker', 'MARK 2')).toBe('marker');
    expect(classify('stall', 'rpm dropped to 0')).toBe('stall');
    expect(classify('trigger', 'STALL rpm')).toBe('stall');
    expect(classify('trigger', 'capture requested')).toBe('capture');
    expect(classify('dropout', 'LINK DROPOUT')).toBe('dropout');
    expect(classify('near_stall', '')).toBe('nearstall');
    expect(classify('connect', '')).toBeNull();
    expect(markLabel('MARK 12')).toBe('12');
  });

  it('pins every stall, near-stall and dropout of a drive', () => {
    const d = synthDrive();
    const w = driveWaypoints(d);
    const kinds = w.map((x) => x.kind);
    expect(kinds).toContain('stall');
    expect(kinds).toContain('dropout');
    expect(kinds).toContain('marker');
    const ns = w.filter((x) => x.kind === 'nearstall');
    expect(ns.length).toBe(1);
    expect(Math.abs(ns[0]!.t - d.marks.nearStallT)).toBeLessThan(1);
    expect(w.filter((x) => x.kind === 'stall').length).toBe(1); // found and recorded: one pin
    expect(w.map((x) => x.t)).toEqual([...w.map((x) => x.t)].sort((a, b) => a - b));
  });
});

describe('exports', () => {
  const drive = synthDrive();
  const parse = (s: string) => new DOMParser().parseFromString(s, 'application/xml');

  it('writes a GPX that parses back with segments, times and waypoints', () => {
    const { text, filename } = exportDrive(drive, 'gpx');
    expect(filename).toBe('drive_2026_11_26_2251.gpx');
    const doc = parse(text);
    expect(doc.getElementsByTagName('parsererror').length).toBe(0);
    const root = doc.documentElement;
    expect(root.localName).toBe('gpx');
    expect(root.getAttribute('version')).toBe('1.1');
    const order = [...root.children].map((c) => c.localName);
    expect(order[0]).toBe('metadata');
    expect(order[order.length - 1]).toBe('trk');
    expect(new Set(order.slice(1, -1))).toEqual(new Set(['wpt']));
    const segs = root.getElementsByTagName('trkseg');
    expect(segs.length).toBe(2); // the GPS gap breaks it
    const pts = root.getElementsByTagName('trkpt');
    expect(pts.length).toBe(drive.gps.t.length);
    const times = [...pts].map((p) => Date.parse(p.getElementsByTagName('time')[0]!.textContent!));
    for (let i = 1; i < times.length; i++) expect(times[i]!).toBeGreaterThan(times[i - 1]!);
    expect(times[0]! / 1000).toBeCloseTo(drive.info.startedUnix + drive.gps.t[0]!, 2);
    const types = [...root.getElementsByTagName('wpt')].map((w) => w.getElementsByTagName('type')[0]!.textContent);
    expect(types).toEqual(expect.arrayContaining(['stall', 'nearstall', 'dropout', 'marker']));
    // the chosen channel (vss, the ECU's speed) rides along
    expect(pts[0]!.getElementsByTagName('hondalog:vss').length).toBe(1);
    const b = root.getElementsByTagName('bounds')[0]!;
    expect(Number(b.getAttribute('minlat'))).toBeCloseTo(Math.min(...drive.gps.lat), 6);
  });

  it('writes a KML coloured by the channel', () => {
    const { text } = exportDrive(drive, 'kml', 'rpm');
    const doc = parse(text).documentElement;
    const styles = [...doc.getElementsByTagName('Style')].filter((s) => s.getElementsByTagName('LineStyle').length);
    expect(styles.length).toBe(KML_BINS + 1);
    for (const s of styles) expect(s.getElementsByTagName('color')[0]!.textContent).toMatch(/^ff[0-9a-f]{6}$/);
    expect(doc.getElementsByTagName('description')[0]!.textContent).toContain('rpm');
    const lines = [...doc.getElementsByTagName('Placemark')].filter((p) => p.getElementsByTagName('LineString').length);
    expect(lines.length).toBeGreaterThan(4);
    const used = new Set(lines.map((p) => p.getElementsByTagName('styleUrl')[0]!.textContent));
    expect(used.size).toBeGreaterThan(3);
    const coords = lines.flatMap((p) => p.getElementsByTagName('coordinates')[0]!.textContent!.split(' '));
    expect(coords.length).toBeGreaterThanOrEqual(drive.gps.t.length - 2);
    const pins = [...doc.getElementsByTagName('Placemark')].filter((p) => p.getElementsByTagName('Point').length);
    expect(pins.some((p) => p.getElementsByTagName('styleUrl')[0]!.textContent === '#pin_stall')).toBe(true);
  });

  it('writes GeoJSON with [lon, lat] order', () => {
    const { text } = exportDrive(drive, 'geojson');
    const doc = JSON.parse(text);
    expect(doc.type).toBe('FeatureCollection');
    const lines = doc.features.filter((f: { geometry: { type: string } }) => f.geometry.type === 'LineString');
    expect(lines.length).toBe(2);
    const [lon, lat] = lines[0].geometry.coordinates[0];
    expect(lon).toBeCloseTo(drive.gps.lon[0]!, 6);
    expect(lat).toBeCloseTo(drive.gps.lat[0]!, 6);
    expect(lines[0].properties.vss.length).toBe(lines[0].geometry.coordinates.length);
  });

  it('refuses a drive without GPS, and knows what speed means', () => {
    const plain = { ...drive, gps: { t: new Float64Array(), lat: new Float64Array(), lon: new Float64Array(), speedKmh: new Float64Array(), heading: new Float64Array() } };
    expect(() => exportDrive(plain, 'gpx')).toThrow(/no GPS/);
    expect(speedChannel(drive)).toBe('vss');
    expect(speedChannel({ series: {} })).toBe('gps_speed');
  });

  it('writes without times when the wall clock is unknown', () => {
    const tr = straight(20);
    const gpx = writeGpx(tr, [], {});
    expect(gpx).not.toContain('<time>');
    expect(gpx).toContain('no times');
    expect(writeKml(tr, null)).toContain('#nodata');
    expect(JSON.parse(writeGeoJson(tr)).features[0].properties.times[0]).toBe(0);
  });
});

describe('stall groups', () => {
  const pin = (name: string, east: number, north: number, located = true): StallPin => {
    const [lat, lon] = proj.offset(ORIGIN[0], ORIGIN[1], east, north);
    return { name, kind: 'stall', reason: 'rpm', message: '', drive: 'd', wallUnix: 0, tTrigger: 0,
      lat: located ? lat : null, lon: located ? lon : null, speedKmh: 0, maxSpeed30s: 30 };
  };
  it('groups stalls at the same place, biggest first', () => {
    const pins = [pin('a', 0, 0), pin('b', 100, 0), pin('c', 5000, 0), pin('d', 180, 50), pin('e', 0, 0, false)];
    const g = clusters(pins);
    expect(g[0]).toEqual([0, 1, 3]);
    expect(g[1]).toEqual([2]);
    expect(summary(pins)).toMatch(/^5 stalls, 4 with a place\. 3 of them within \d+ m of one another/);
    expect(summary([])).toContain('nothing to compare');
    const doc = stallsGeojson(pins) as { features: Array<{ properties: { cluster_size: number } }>; unlocated: number };
    expect(doc.features.length).toBe(4);
    expect(doc.unlocated).toBe(1);
    expect(doc.features[0]!.properties.cluster_size).toBe(3);
  });
});
