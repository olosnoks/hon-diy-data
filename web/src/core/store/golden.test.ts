// @vitest-environment node
/**
 * Golden tests against the Python app: fixtures/python-sim.db was recorded by
 * ``hondalog-log record --port sim: --bb bbsim: --seconds 3`` (Python), and
 * python-sim.expected.json is what the Python reader made of it. A database
 * written here is read back with the Python ``hondalog-log`` when the venv is
 * there (..\..\hon-diy-data\.venv), never touching ~/hondalog.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { HondalogDb, MemoryFileStore, SqliteDriveStore, readDrive } from './index';
import { Recorder } from '../record/recorder';
import { recorderMapFromToml } from '../record/testing';

const here = resolve(__dirname);
const fixture = (name: string) => new Uint8Array(readFileSync(join(here, 'fixtures', name)));
const expected = JSON.parse(readFileSync(join(here, 'fixtures', 'python-sim.expected.json'), 'utf8'));

function same(a: ArrayLike<number>, b: (number | null)[]) {
  expect(a.length).toBe(b.length);
  for (let i = 0; i < b.length; i++) {
    const want = b[i];
    if (want === null) expect(Number.isNaN(a[i])).toBe(true);
    else expect(a[i]).toBe(want);
  }
}

describe('a database made by the Python recorder', () => {
  it('opens, and every value reads back exactly as Python reads it', async () => {
    const hdb = await HondalogDb.open(fixture('python-sim.db'), { fileName: 'python-sim.db' });
    const log = readDrive(hdb.conn, '');
    expect(log.name).toBe(expected.drive);
    expect(log.status).toBe(expected.status);
    expect(log.rows).toBe(expected.rows);
    expect(Object.fromEntries([...log.streams].map(([n, s]) => [n, s.t.length]))).toEqual(expected.streams);
    for (const [name, s] of Object.entries(expected.series) as [string, { t: (number | null)[]; v: (number | null)[] }][]) {
      const got = log.series(name);
      same(got.t, s.t);
      same(got.v, s.v);                 // bb_* come out of the d1 codec: byte-exact
    }
    expect(log.events.map((e) => [e.t, e.kind, e.text])).toEqual(expected.events);
    const raw = log.streams.get('ecu/00+3')!;
    expect(Array.from({ length: 5 }, (_, i) => Array.from(raw.raw.subarray(i * 3, i * 3 + 3)))).toEqual(expected.raw00);
    same(raw.latency.subarray(0, 5), expected.lat00);
    expect(Object.keys(log.meta).sort()).toEqual(expected.meta_keys);
    expect(log.meta.ecu_id).toBe(expected.ecu_id);
    expect(log.meta.address_map_hash).toBe(expected.map_hash);

    const summary = hdb.conn.objects('SELECT * FROM drive_summary')[0];
    expect(summary).toEqual(expected.drive_summary);
    const view = hdb.conn.columns(`SELECT * FROM ${expected.drive} LIMIT 3`);
    expect(view.names).toEqual(expected.view_cols);
    expect(view.rows).toEqual(expected.view_rows);
    expect(hdb.conn.get('SELECT count(*) FROM readings')![0]).toBe(expected.readings_count);
    expect(hdb.conn.get('SELECT count(*) FROM ecu_reads')![0]).toBe(expected.ecu_reads_count);
    expect(hdb.conn.all("SELECT channel, t, samples, min, mean, max FROM blackbox_1s WHERE channel='bb_vbat' ORDER BY t")).toEqual(expected.blackbox_1s);
    hdb.close();
  });

  it('is listed and loaded through the DriveStore contract', async () => {
    const files = new MemoryFileStore();
    await files.write('hondalog.db', fixture('python-sim.db'));
    const store = await SqliteDriveStore.open(files);
    const list = await store.listDrives();
    expect(list).toHaveLength(1);
    expect(list[0]).toMatchObject({ name: expected.drive, kind: 'drive', status: 'ok', rows: expected.rows, reason: 'stopped' });
    const loaded = await store.loadDrive(expected.drive);
    expect(loaded.series.rpm!.v.length).toBe(expected.series.rpm.v.length);
    expect(loaded.reads.length).toBe(expected.ecu_reads_count);
    expect(loaded.reads[0]!.data).toBeInstanceOf(Uint8Array);
    expect(loaded.events.map((e) => e.kind)).toEqual(['blackbox', 'blackbox', 'blackbox']);
    expect(loaded.gps.t.length).toBe(0);
    // export and import again into an empty store: one drive, the same rows
    const bytes = await store.exportDb();
    const other = await SqliteDriveStore.open(new MemoryFileStore());
    expect(await other.importDb(bytes)).toBe(1);
    expect(await other.importDb(bytes)).toBe(0);           // already there
    const again = other.readLog(expected.drive);
    expect(again.rows).toBe(expected.rows);
    same(again.series('bb_vbat').v, expected.series.bb_vbat.v);
    await store.close();
    await other.close();
  });
});

const PY = resolve(here, '../../../../../hon-diy-data/.venv/Scripts/hondalog-log.exe');
const PY_EXE = resolve(here, '../../../../../hon-diy-data/.venv/Scripts/python.exe');

describe.skipIf(!existsSync(PY))('a database made here, read by the Python app', () => {
  it('hondalog-log info and the SQL views read it', async () => {
    const text = readFileSync(join(here, 'fixtures', 'addressmap.toml'), 'utf8');
    const amap = recorderMapFromToml(text);
    const files = new MemoryFileStore();
    const hdb = await HondalogDb.openFile(files);
    const rec = new Recorder(hdb, amap, { ecuId: 'SIM-P2P', port: 'sim:', background: false, epochUnix: 1795765862.5, stall: { preS: 5, postS: 2 } });
    for (let i = 0; i < 100; i++) {
      const t = i * 0.06;
      const rpm = i < 80 ? 3000 : 0;
      rec.feedRead(t, 0x00, new Uint8Array([(rpm * 4) >> 8, (rpm * 4) & 0xff, 40]), 0.0035);
      rec.feedRead(t + 0.02, 0x12, new Uint8Array([60, 141, 24 + 2 * (i % 50), 23]));
      rec.feedValues('blackbox/live', t + 0.01, { bb_vbat: 13.8, bb_ig1: 12.5 }, { source: 'blackbox', alive: true });
      if (i % 10 === 0) rec.feedValues('gps', t, { gps_lat: 52.1 + i * 1e-5, gps_lon: 4.3 + i * 1e-5, gps_speed: 30, gps_heading: 90, gps_sats: 8, gps_fix: 1, gps_hdop: 0.9, gps_utc: 0 },
        { source: 'handheld', columns: (await import('./export/track')).gpsColumns(), evaluate: false });
    }
    rec.mark('MARK 1');
    rec.feedLink(6.1, false);
    await rec.stop();
    expect(rec.events.map((e) => e.reason)).toEqual(['rpm']);
    const dir = mkdtempSync(join(tmpdir(), 'hondalog-web-'));
    const path = join(dir, 'web.db');
    writeFileSync(path, files.files.get('hondalog.db')!);
    const env = { ...process.env, HONDALOG_DIR: dir, HONDALOG_DB: path, HONDALOG_HANDHELD: 'off' };
    const info = execFileSync(PY, ['--db', path, 'info'], { env, encoding: 'utf8' });
    expect(info).toContain(rec.ref.drive);
    expect(info).toContain('status: complete');
    expect(info).toMatch(/ecu\/00\+3\s+100 rows/);
    expect(info).toContain('3 raw bytes/row');
    expect(info).toMatch(/blackbox\/live\s+100 rows/);
    const sql = execFileSync(PY, ['--db', path, 'sql', 'SELECT name, reason, rows FROM stalls'], { env, encoding: 'utf8' });
    expect(sql).toContain('stall_');
    expect(sql).toMatch(/\trpm\t/);
    // the Python reader: values, raw bytes, the decoded black box block, gps_track
    const script = [
      'import sys, json, sqlite3',
      'from hondalog.data import open_log',
      'log = open_log(sys.argv[1])',
      'c = sqlite3.connect(sys.argv[1])',
      'print(json.dumps({"status": log.status, "rpm0": float(log.series("rpm").v[0]), "raw0": list(map(int, log.streams["ecu/00+3"].raw[0])),',
      ' "lat": float(log.streams["ecu/00+3"].latency[0]), "vbat": float(log.series("bb_vbat").v[-1]), "n_bb": len(log.series("bb_vbat")),',
      ' "markers": log.markers, "gps": c.execute("select count(*), max(lat) from gps_track").fetchone(),',
      ' "bb1s": c.execute("select count(*) from blackbox_1s").fetchone()[0], "integrity": c.execute("pragma integrity_check").fetchone()[0],',
      ' "app": c.execute("pragma application_id").fetchone()[0], "uv": c.execute("pragma user_version").fetchone()[0]}))',
    ].join('\n');
    const out = JSON.parse(execFileSync(PY_EXE, ['-I', '-c', script, path], { env, encoding: 'utf8', cwd: resolve(here, '../../../..') }));
    expect(out.status).toBe('complete');
    expect(out.rpm0).toBe(3000);
    expect(out.raw0).toEqual([0x2e, 0xe0, 40]);
    expect(out.lat).toBeCloseTo(0.0035, 9);
    expect(out.vbat).toBeCloseTo(13.8, 5);
    expect(out.n_bb).toBe(100);
    expect(out.markers.map((m: [number, string]) => m[1])).toEqual(['STALL (rpm 0)', 'MARK 1']);       // the stall at 4.8 s, the marker with the next feed (6.1 s)
    expect(out.gps[0]).toBe(10);
    expect(out.gps[1]).toBeCloseTo(52.1 + 90e-5, 9);
    expect(out.bb1s).toBeGreaterThan(0);
    expect(out.integrity).toBe('ok');
    expect(out.app).toBe(0x48444c47);
    expect(out.uv).toBe(2);
  }, 60_000);
});
