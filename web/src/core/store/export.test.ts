// @vitest-environment node
/**
 * Exports and importers (port of tests/test_export.py and the GPX part of
 * test_geo_export.py), with golden files the Python exporter wrote from
 * fixtures/python-sim.db: the MLG files must match byte for byte.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { HondalogDb } from './db';
import { exportLog } from './export';
import { MlgError, asciiText, parseMlg, readMlg, writeMlg, U08, S16, F32, STYLE_ON_OFF } from './export/mlg';
import { parseTextLog } from './export/textlog';
import { gpsColumns } from './export/track';
import { MemoryFileStore } from './filestore';
import { column } from './model';
import { type Log, type Table, readDrive } from './reader';
import { SqliteDriveStore } from './drivestore';
import { writeLog } from './transfer';
import { newDrive } from './writer';

const here = resolve(__dirname);
const fx = (name: string) => new Uint8Array(readFileSync(join(here, 'fixtures', name)));
const near = (a: number, b: number, tol = 1e-9) => expect(Math.abs(a - b)).toBeLessThanOrEqual(tol);
const dec = new TextDecoder();

async function pythonSim(): Promise<Log> {
  const hdb = await HondalogDb.open(fx('python-sim.db'));
  return readDrive(hdb.conn, '');
}

/** The header lines that are Python dict/list reprs (floats lost in JSON) or name the exporting app's version. */
const unstable = (line: string) => /^# (hondalog |link: |triggers: |read_plan: |unlocked_frames: )/.test(line) || line.startsWith('"hondalog ');

describe('golden files from the Python exporter', () => {
  it('MLG v2, v1 and compact are byte for byte the same', async () => {
    const log = await pythonSim();
    const mine = await exportLog(log, 'mlg');
    const theirs = fx('python-sim.mlg');
    expect(mine.length).toBe(theirs.length);
    expect(Buffer.from(mine).equals(Buffer.from(theirs))).toBe(true);
    expect(Buffer.from(await exportLog(log, 'mlg', { mlgVersion: 1 })).equals(Buffer.from(fx('python-sim.v1.mlg')))).toBe(true);
    const compact = await exportLog(log, 'mlg', { channels: ['rpm', 'map', 'tps', 'vtec', 'ect'], compact: true });
    expect(Buffer.from(compact).equals(Buffer.from(fx('python-sim.compact.mlg')))).toBe(true);
  });

  it('CSV and MSL: every row the same, the header the same but for Python reprs', async () => {
    const log = await pythonSim();
    const cmp = (mine: string, theirs: string) => {
      // line endings differ between checkouts (git normalises the fixture); compare lines
      const a = mine.split(/\r?\n/), b = theirs.split(/\r?\n/);
      expect(a.length).toBe(b.length);
      a.forEach((line, i) => { if (!unstable(line) || !unstable(b[i]!)) expect(line).toBe(b[i]); });
    };
    cmp(dec.decode(await exportLog(log, 'csv')), dec.decode(fx('python-sim.csv')));
    cmp(dec.decode(await exportLog(log, 'msl', { channels: ['rpm', 'map', 'ect', 'bb_vbat'] })), dec.decode(fx('python-sim.msl')));
    cmp(dec.decode(await exportLog(log, 'csv', { channels: ['rpm', 'bb_vbat', 'link'], hz: 10, t0: 1.0, t1: 2.5 })), dec.decode(fx('python-sim.hz.csv')));
  });
});

async function small(): Promise<Log> {
  const hdb = await HondalogDb.open(null);
  const ref = newDrive(hdb, { ecu: 'P2P', ecu_id: 'SIM', started_unix: 1794729600.0, address_map_version: 'v7', address_map_text: 'x'.repeat(5000) }, (w) => {
    w.addStream('ecu', [column('link', { dtype: 'u1', kind: 'flag', decimals: 0 }), column('rpm', { units: 'rpm', decimals: 0 }),
      column('ect', { units: '°C', decimals: 1 }), column('ign', { units: '° BTDC', decimals: 1 })], { source: 'ecu' });
    for (let i = 0; i < 400; i++) {
      const down = i >= 200 && i < 210;
      w.append('ecu', i * 0.1, [down ? 0 : 1, down ? null : 800 + i, down ? null : 80 + i * 0.01, down ? null : -3.5 + i * 0.05]);
    }
    w.event(5.03, 'marker', 'MARK 1');
    w.event(30.0, 'trigger', 'STALL (rpm 0)');
    w.event(31.0, 'note', 'not a marker');
  });
  return readDrive(hdb.conn, ref.drive);
}

const table = (t: number[], cols: Record<string, number[]>, units: Record<string, string> = {}): Table => ({
  t: Float64Array.from(t), columns: Object.fromEntries(Object.entries(cols).map(([k, v]) => [k, Float64Array.from(v)])), units,
  names: Object.keys(cols),
});

describe('MLG', () => {
  it('v2 layout matches the spec', async () => {
    const blob = await exportLog(await small(), 'mlg');
    const v = new DataView(blob.buffer);
    expect(dec.decode(blob.subarray(0, 6))).toBe('MLVLG\u0000');
    const [version, stamp, infoStart, dataBegin, recLen, nfields] = [v.getUint16(6), v.getUint32(8), v.getUint32(12), v.getUint32(16), v.getUint16(20), v.getUint16(22)];
    expect([version, stamp, nfields]).toEqual([2, 1794729600, 5]);
    expect(infoStart).toBe(24 + 89 * nfields);
    expect(blob[dataBegin - 1]).toBe(0);
    expect(dec.decode(blob.subarray(infoStart, dataBegin - 1)).startsWith('ecu=P2P; ecu_id=SIM; address_map_version=v7; started=2026-11-1')).toBe(true);
    const parsed = parseMlg(blob);
    expect(parsed.fields[0]).toMatchObject({ type: F32, name: 'Time', units: 's', style: 0, scale: 1, transform: 0, digits: 3 });
    expect(parsed.fields[1]).toMatchObject({ type: U08, name: 'link', style: STYLE_ON_OFF, digits: 0 });
    expect(parsed.fields[3]!.units).toBe('degC');
    expect(parsed.fields[4]!.units).toBe('deg BTDC');
    expect(recLen).toBe(4 + 1 + 4 * 3);
    let pos = dataBegin;
    const counters: number[] = [];
    const markers: [number, string][] = [];
    let records = 0;
    while (pos < blob.length) {
      counters.push(blob[pos + 1]!);
      if (blob[pos] === 0) { records++; pos += 5 + recLen; } else { markers.push([records, parsed.markers[markers.length]![1]]); pos += 54; }
    }
    expect(records).toBe(400);
    expect(counters).toEqual(Array.from({ length: 402 }, (_, i) => i % 256));
    expect(markers).toEqual([[51, 'MARK 1'], [300, 'STALL (rpm 0)']]);
    expect(parsed.crcErrors).toBe(0);
    near(parsed.data.Time![399]!, 39.9, 1e-5);
    expect(parsed.data.rpm![399]).toBe(1199);
    expect(parsed.data.rpm![205]).toBe(999);                      // link gap holds the last value
  });

  it('v1 layout and the round trip', async () => {
    const log = await small();
    const blob = await exportLog(log, 'mlg', { mlgVersion: 1 });
    const v = new DataView(blob.buffer);
    expect([v.getUint16(6), v.getUint16(20), v.getUint16(18)]).toEqual([1, 5, 17]);
    expect(v.getUint16(12)).toBe(22 + 55 * 5);
    const parsed = parseMlg(blob);
    expect(parsed.version).toBe(1);
    expect(parsed.crcErrors).toBe(0);
    expect(parsed.info.startsWith('ecu=P2P')).toBe(true);
    for (const version of [1, 2] as const) {
      const back = readMlg(await exportLog(log, 'mlg', { mlgVersion: version }));
      expect(back.channels).toEqual(['link', 'rpm', 'ect', 'ign']);
      expect(back.rows).toBe(400);
      back.series('rpm').t.forEach((t, i) => near(t, i * 0.1, 1e-5));
      expect(Array.from(back.series('rpm').v)).toEqual(Array.from({ length: 400 }, (_, i) => (i >= 200 && i < 210 ? 999 : 800 + i)));
      expect(back.units('ect')).toBe('degC');
      expect(back.meta.started_unix).toBe(1794729600);
      expect(back.markers.map((m) => m[1])).toEqual(['MARK 1', 'STALL (rpm 0)']);
      near(back.markers[0]![0], 5.1, 1e-5);
    }
  });

  it('compact fields', () => {
    const n = 1000;
    const t = table(Array.from({ length: n }, (_, i) => i * 0.1), {
      rpm: Array.from({ length: n }, (_, i) => 800 + i), ect: Array.from({ length: n }, (_, i) => 80 + i * 0.01),
      vtec: Array.from({ length: n }, (_, i) => i % 2), big: Array.from({ length: n }, (_, i) => i * 1000), gap: Array.from({ length: n }, (_, i) => (i === 5 ? NaN : 1)),
    }, { rpm: 'rpm' });
    const cols = { rpm: column('rpm', { decimals: 0 }), ect: column('ect', { decimals: 1 }), vtec: column('vtec', { kind: 'flag' }), big: column('big', { decimals: 0 }), gap: column('gap') };
    const plain = writeMlg(t, [], { columns: cols });
    const small_ = writeMlg(t, [], { columns: cols, compact: true, fill: 'keep' });
    const parsed = parseMlg(small_);
    const kinds = Object.fromEntries(parsed.fields.map((f) => [f.name, [f.type, f.scale, f.style]]));
    expect(kinds.rpm![0]).toBe(S16);
    expect(kinds.ect![0]).toBe(S16);
    near(kinds.ect![1]!, 0.1, 1e-7);
    expect(kinds.vtec).toEqual([U08, 1, STYLE_ON_OFF]);
    expect(kinds.big![0]).toBe(F32);
    expect(kinds.gap![0]).toBe(F32);
    expect(Array.from(parsed.data.rpm!)).toEqual(Array.from(t.columns.rpm!));
    parsed.data.ect!.forEach((x, i) => near(x, t.columns.ect![i]!, 0.05 + 1e-6 + 1e-7 * Math.abs(t.columns.ect![i]!)));   // assert_allclose: atol + rtol * |desired|
    expect(Number.isNaN(parsed.data.gap![5]!)).toBe(true);
    expect(parsed.crcErrors).toBe(0);
    expect(small_.length).toBeLessThan(plain.length);
    expect(parsed.recordLength).toBe(4 + 2 + 2 + 1 + 4 + 4);
  });

  it('reader edge cases', () => {
    const enc = new TextEncoder();
    expect(() => parseMlg(enc.encode('not an mlg file at all......'))).toThrow(MlgError);
    expect(() => parseMlg(Uint8Array.from([...enc.encode('MLVLG\u0000'), 0, 9, ...new Uint8Array(40)]))).toThrow(MlgError);
    expect(() => writeMlg(table([], {}), [], { version: 3 as never })).toThrow(MlgError);
    const blob = writeMlg(table([0, 0.5, 1, 1.5], { a: [1, 2, 3, 4] }), [[9.0, 'late'], [0.0, 'x'.repeat(80)]]);
    const parsed = parseMlg(blob);
    expect(parsed.markers).toEqual([[0.0, 'x'.repeat(49)], [1.5, 'late']]);
    const cut = parseMlg(blob.subarray(0, blob.length - 3));      // torn last block
    expect(cut.t.length).toBe(4);
    expect(cut.markers).toHaveLength(1);
    // a file without a Time field: time from the wrapping 10 us stamps
    const head = new Uint8Array(24 + 89 + 6 * 6);
    const hv = new DataView(head.buffer);
    head.set(enc.encode('MLVLG\u0000'));
    hv.setUint16(6, 2); hv.setUint32(16, 24 + 89); hv.setUint16(20, 1); hv.setUint16(22, 1);
    head[24] = 0; head[25] = 'a'.charCodeAt(0); hv.setFloat32(24 + 46, 2.0); hv.setFloat32(24 + 50, 1.0);
    for (let i = 0; i < 6; i++) {
      const b = 24 + 89 + i * 6;
      head[b] = 0; head[b + 1] = i; hv.setUint16(b + 2, (i * 30000) % 65536); head[b + 4] = i; head[b + 5] = i;
    }
    const bare = parseMlg(head);
    bare.t.forEach((t, i) => near(t, i * 0.3, 1e-9));
    expect(Array.from(bare.data.a!)).toEqual([2, 4, 6, 8, 10, 12]);
    expect(parseMlg(writeMlg(table([], { a: [] }), [[1.0, 'm']])).markers).toEqual([[0.0, 'm']]);
    expect(Array.from(asciiText('λ = 1.0 °', 10))).toEqual(Array.from(enc.encode('lambda = \u0000')));
  });
});

describe('text logs', () => {
  it('MSL export and import', async () => {
    const lines = dec.decode(await exportLog(await small(), 'msl')).split('\n').filter(Boolean);
    expect(lines[0]!.startsWith('"hondalog')).toBe(true);
    expect(lines[1]!.startsWith('"Capture Date: ')).toBe(true);
    expect(lines[2]).toBe('Time\tlink\trpm\tect\tign');
    expect(lines[3]).toBe('s\t\trpm\tdegC\tdeg BTDC');
    expect(lines[4]!.split('\t').slice(0, 3)).toEqual(['0.000', '1', '800']);
    expect(lines).toContain('MARK 001 - MARK 1');
    expect(lines).toContain('MARK 002 - STALL (rpm 0)');
    const back = parseTextLog(lines.join('\n') + '\n');
    expect(back.channels).toEqual(['link', 'rpm', 'ect', 'ign']);
    expect(back.rows).toBe(400);
    expect(back.units('ign')).toBe('deg BTDC');
    expect(String(back.meta.title)).toContain('hondalog');
    expect(back.markers.map(([t, x]) => [Math.round(t * 10) / 10, x])).toEqual([[5.1, 'MARK 1'], [30.0, 'STALL (rpm 0)']]);
    expect(back.series('rpm').v[205]).toBe(999);
    expect(back.series('link').v[205]).toBe(0);
  });

  it('CSV export and import', async () => {
    const text = dec.decode(await exportLog(await small(), 'csv'));
    expect(text.startsWith('# hondalog ')).toBe(true);
    expect(text).toContain('# units: link=- rpm=rpm ect=°C ign=° BTDC');
    expect(text).not.toContain('address_map_text');
    expect(text).toContain('time_s,link,rpm,ect,ign,marker');
    expect(text).toContain('20.5000,0,,,,');                       // gaps stay empty
    const back = parseTextLog(text);
    expect(back.meta.address_map_version).toBe('v7');
    expect(back.units('ect')).toBe('°C');
    expect(Number.isNaN(back.series('rpm').v[205]!)).toBe(true);
    expect(back.series('rpm').v[399]).toBe(1199);
    expect(back.column('link')!.kind).toBe('flag');
    expect(back.events.map((e) => e.kind)).toEqual(['marker', 'trigger']);
  });

  it('an old 0.1.0 CSV imports and becomes a drive', async () => {
    const OLD = '# hondalog 0.1.0\n# started: 2026-11-10T07:58:01\n# ecu: P2P expected, not confirmed\n' +
      '# ecu_id: 53 49 4D 2D 50 32 50 00 00 01  "SIM-P2P..."\n# address_map_version: p2p-guess-1 (kerpz P2T map)\n' +
      '# units: rpm=rpm ect=°C batt=V vtec=-\ntime_s,wall_time,link,rpm,ect,batt,vtec,marker\n' +
      '0.103,2026-11-10T07:58:01.201,1,752,81.5,13.9,0,\n0.204,2026-11-10T07:58:01.302,1,760,81.5,13.9,0,MARK 1\n' +
      '0.305,2026-11-10T07:58:01.403,0,,,,,\n0.406,2026-11-10T07:58:01.504,1,0,81.6,12.1,0,it stumbled | STALL (rpm 0)\n';
    const log = parseTextLog(OLD, 'log_20261110_075801.csv');
    expect(log.channels).toEqual(['link', 'rpm', 'ect', 'batt', 'vtec']);
    expect(String(log.meta.address_map_version).startsWith('p2p-guess-1')).toBe(true);
    expect(String(log.meta.ecu_id)).toContain('SIM-P2P');
    expect(log.units('ect')).toBe('°C');
    expect(log.units('vtec')).toBe('');
    const rpm = log.series('rpm');
    expect(Array.from(rpm.t)).toEqual([0.103, 0.204, 0.305, 0.406]);
    expect(rpm.v[0]).toBe(752);
    expect(Number.isNaN(rpm.v[2]!)).toBe(true);
    expect(Array.from(log.series('link').v)).toEqual([1, 1, 0, 1]);
    expect(log.events.map((e) => [e.t, e.kind, e.text])).toEqual([[0.204, 'marker', 'MARK 1'], [0.406, 'marker', 'it stumbled'], [0.406, 'trigger', 'STALL (rpm 0)']]);
    const hdb = await HondalogDb.open(null);
    const native = readDrive(hdb.conn, writeLog(hdb, log, { imported_from: 'log_20261110_075801.csv' }).drive);
    expect(native.status).toBe('imported');
    expect(native.rows).toBe(4);
    expect(native.name).toBe('drive_2026_11_10_0758');
    expect(native.meta.started).toBe('2026-11-10T07:58:01');
    expect(native.markers).toEqual(log.markers);
    near(native.series('batt').v[3]!, 12.1, 1e-12);
  });

  it('generic text logs', () => {
    const semi = parseTextLog('Time;AFR;Note\n0;14.7;ok\n1;15,1;lean\n2;13.9;\n');
    expect(semi.channels).toEqual(['AFR']);
    expect(Number.isNaN(semi.series('AFR').v[1]!)).toBe(true);
    const noTime = parseTextLog('a,b\n1,2\n3,4\n');
    expect(Array.from(noTime.series('a').t)).toEqual([0, 1]);
    expect(String(noTime.meta.time_axis)).toContain('row number');
    const odd = parseTextLog('time,t,rpm,rpm\n2,9,3,4\n1,8,1,2\n\nMARK 7\n');
    expect(odd.channels).toEqual(['t_', 'rpm', 'rpm_2']);
    expect(Array.from(odd.series('rpm').t)).toEqual([1, 2]);
    expect(() => parseTextLog('# only a comment\n')).toThrow();
    const headOnly = parseTextLog('time_s,rpm,marker\n');
    expect(headOnly.rows).toBe(0);
    expect(headOnly.channels).toEqual(['rpm']);
  });

  it('a cut of a drive as its own small database', async () => {
    const log = await pythonSim();
    const bytes = await exportLog(log, 'db', { t0: 1.0, t1: 2.0 });
    const cut = await HondalogDb.open(bytes);
    const back = readDrive(cut.conn, '');
    expect(back.t0).toBeGreaterThanOrEqual(1.0);
    expect(back.t1).toBeLessThanOrEqual(2.0);
    expect(back.streams.get('ecu/00+3')!.info.rawLen).toBe(3);
    expect(String(back.meta.exported_from)).toBe(log.name);
    await expect(exportLog(log, 'parquet')).rejects.toThrow(/unknown export format/);
  });
});

// ---- GPX ----

async function gpsDrive() {
  const files = new MemoryFileStore();
  const store = await SqliteDriveStore.open(files);
  const epoch = 1795765862.5;
  const ref = newDrive(store.hdb, { epoch_unix: epoch, started_unix: epoch }, (w) => {
    w.addStream('gps', gpsColumns(), { source: 'handheld' });
    w.addStream('ecu/00+3', [column('rpm', { units: 'rpm', decimals: 0 }), column('vss', { units: 'km/h', decimals: 0 })], { source: 'ecu', rawLen: 3, attrs: { addr: 0, length: 3 } });
    for (let i = 0; i < 120; i++) {
      const t = i * 1.0 + (i >= 60 ? 10 : 0);              // a 10 s gap: two segments
      w.append('gps', t, [52.1 + i * 1e-4, 4.3 + i * 2e-4, 30 + (i % 7) * 1.25, (90 + i) % 360, 8, 1, 0.9, 1795765862 + t]);
    }
    for (let i = 0; i < 1300; i++) {
      const t = i * 0.1;
      w.append('ecu/00+3', t, [800 + 10 * (i % 50), 30], new Uint8Array([0, 0, 30]));
    }
    w.event(12.0, 'marker', 'MARK 1');
    w.event(40.0, 'alarm', 'ECT high', { state: 'raised' });
    w.capture('stall', 50.0, 20.0, 60.0, { rule: 'stall', reason: 'rpm', message: 'rpm dropped to 0', wallUnix: epoch + 50 });
  });
  return { store, ref, files };
}

describe('GPX', () => {
  it('parses back with times, segments and waypoints', async () => {
    const { store, ref } = await gpsDrive();
    const log = store.readLog(ref.drive);
    const gpx = dec.decode(await exportLog(log, 'gpx', { captures: store.captures(log) }));
    expect(gpx.startsWith("<?xml version='1.0' encoding='UTF-8'?>\n<gpx xmlns=\"http://www.topografix.com/GPX/1/1\"")).toBe(true);
    expect((gpx.match(/<trkseg>/g) ?? []).length).toBe(2);
    expect((gpx.match(/<trkpt /g) ?? []).length).toBe(120);
    expect(gpx).toContain('<time>2026-11-27T07:51:02.500Z</time>');
    expect(gpx).toContain('<name>MARK 1</name>');
    expect(gpx).toContain('<sym>Danger Area</sym>');
    expect(gpx).toContain('<type>stall</type>');
    expect(gpx).toContain('<desc>rpm dropped to 0</desc>');
    expect(gpx).toContain('<hondalog:vss>30</hondalog:vss>');
    expect(gpx).toContain('<hondalog:speed_kmh>31.2</hondalog:speed_kmh>');   // 31.25: half to even, as Python
    const rpm = dec.decode(await exportLog(log, 'gpx', { channels: ['rpm'] }));
    expect(rpm).toContain('<hondalog:rpm>');
    const loaded = await store.loadDrive(ref.drive);
    expect(loaded.gps.t.length).toBe(120);
    near(loaded.gps.lat[0]!, 52.1, 1e-12);
    expect(loaded.reads[0]!.addr).toBe(0);
  });

  it('no GPS, no export', async () => {
    await expect(exportLog(await small(), 'gpx')).rejects.toThrow(/no GPS fixes/);
  });

  const PY = resolve(here, '../../../../../hon-diy-data/.venv/Scripts/hondalog-log.exe');
  it.skipIf(!existsSync(PY))('is byte for byte what the Python exporter writes for the same drive', async () => {
    const { store, ref } = await gpsDrive();
    const dir = mkdtempSync(join(tmpdir(), 'hondalog-web-gpx-'));
    const path = join(dir, 'gps.db');
    writeFileSync(path, await store.exportDb());
    const out = join(dir, 'py.gpx');
    const env = { ...process.env, HONDALOG_DIR: dir, HONDALOG_DB: path, HONDALOG_HANDHELD: 'off' };
    execFileSync(PY, ['--db', path, 'export', ref.drive, out], { env, encoding: 'utf8' });
    const theirs = readFileSync(out, 'utf8').replace(/\r\n/g, '\n');     // ElementTree writes in text mode: CRLF on Windows
    const log = store.readLog(ref.drive);
    const mine = dec.decode(await exportLog(log, 'gpx', { captures: store.captures(log) }));
    expect(mine.replace(/creator="hondalog [^"]*"/, '')).toBe(theirs.replace(/creator="hondalog [^"]*"/, ''));
  }, 60_000);
});
