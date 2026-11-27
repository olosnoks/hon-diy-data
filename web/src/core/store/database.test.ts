// @vitest-environment node
/** The drive database: round trips, crash recovery, migrations, views, black box chunks (port of tests/test_database.py). */
import { describe, expect, it } from 'vitest';
import * as codec from './codec';
import { HondalogDb, deleteDrives, vacuum } from './db';
import { MemoryFileStore } from './filestore';
import { type StreamData, StreamInfo, column, emptyStreamData, packRows } from './model';
import { Log, align, readDrive } from './reader';
import { LogFormatError, MIGRATIONS, SLOTS, schemaVersion } from './schema';
import { mergeWindow, writeLog } from './transfer';
import { type DriveWriter, Store, newDrive } from './writer';
import { inflate, deflate } from './zlib';

const near = (a: number, b: number, tol = 1e-9) => expect(Math.abs(a - b)).toBeLessThanOrEqual(tol);
const be16 = (v: number) => new Uint8Array([(v >> 8) & 0xff, v & 0xff]);

function fill(drive: DriveWriter, rows = 200): void {
  drive.addStream('ecu/00+2', [column('rpm', { units: 'rpm', decimals: 0 })], { source: 'ecu', rawLen: 2, attrs: { addr: 0, length: 2 } });
  drive.addStream('ecu/10+3', [column('ect', { units: '°C' }), column('flag', { dtype: 'u1', kind: 'flag' })], { source: 'ecu' });
  for (let i = 0; i < rows; i++) {
    drive.append('ecu/00+2', i * 0.05, [800 + i], be16((800 + i) * 4), 0.004);
    if (i % 10 === 0) drive.append('ecu/10+3', i * 0.05 + 0.02, [80 + i / 100, i % 2]);
    if (i % 50 === 49) drive.store.write();
  }
  drive.event(1.0, 'marker', 'MARK 1');
  drive.event(2.5, 'trigger', 'STALL (rpm 0)', { rule: 'stall' });
  drive.event(3.0, 'note', 'smelled fuel');
  drive.event(3.5, 'alarm', 'ECT high', { alarm: 'ect_high', kind: 'raised', severity: 'warn', value: 106.0, channel: 'ect', unverified: true });
}

const META = { ecu: 'P2P', address_map_version: 'v1', started_unix: 1794812400.0, epoch_unix: 1794812400.0, address_map_text: "[[channel]]\nname = 'rpm'\n" };

function make(hdb: HondalogDb, rows = 200) {
  return newDrive(hdb, META, (d) => fill(d, rows));
}

describe('round trips', () => {
  it('a drive reads back as it was written', async () => {
    const hdb = await HondalogDb.open(null);
    const ref = make(hdb);
    expect(ref.drive.startsWith('drive_2026_11_')).toBe(true);
    const log = readDrive(hdb.conn, ref.drive);
    expect(log.status).toBe('complete');
    expect(log.meta.ecu).toBe('P2P');
    expect(log.meta.address_map_version).toBe('v1');
    expect(String(log.meta.address_map_text).startsWith('[[channel]]')).toBe(true);
    expect(log.meta.epoch_unix).toBe(1794812400.0);
    expect([...log.streams.keys()]).toEqual(['ecu/00+2', 'ecu/10+3']);
    expect(log.channels).toEqual(['rpm', 'ect', 'flag']);
    expect(log.rows).toBe(220);
    const rpm = log.series('rpm');
    expect(rpm.units).toBe('rpm');
    expect(Array.from(rpm.v)).toEqual(Array.from({ length: 200 }, (_, i) => 800 + i));
    rpm.t.forEach((t, i) => near(t, i * 0.05));
    const s = log.streams.get('ecu/00+2')!;
    expect(Array.from(s.raw.subarray(6, 8))).toEqual(Array.from(be16(803 * 4)));
    s.latency.forEach((l) => near(l, 0.004));
    expect(log.streams.get('ecu/10+3')!.latency.every((v) => Number.isNaN(v))).toBe(true);
    expect(s.info.attrs).toEqual({ addr: 0, length: 2 });
    expect(log.column('ect')!.units).toBe('°C');
    expect(log.column('nope')).toBeUndefined();
    expect(Array.from(log.series('flag').v.subarray(0, 3))).toEqual([0, 0, 0]);
    expect(log.markers).toEqual([[1.0, 'MARK 1'], [2.5, 'STALL (rpm 0)'], [3.5, 'ECT high']]);
    expect(log.events.map((e) => e.kind)).toEqual(['marker', 'trigger', 'note', 'alarm']);
    expect(log.events[1]!.data).toEqual({ rule: 'stall' });
    expect(log.events[3]!.data.value).toBe(106.0);
    expect(log.t0).toBe(0);
    near(log.t1, 9.95);
    expect(() => log.series('nope')).toThrow();
    for (const spec of [ref.drive, '1', 'last', '']) expect(readDrive(hdb.conn, spec).name).toBe(ref.drive);
    expect(() => readDrive(hdb.conn, 'drive_1999_01_01_0000')).toThrow();
  });

  it('null becomes NaN and bad rows are rejected', async () => {
    const hdb = await HondalogDb.open(null);
    let closed: DriveWriter | null = null;
    const ref = newDrive(hdb, {}, (w) => {
      closed = w;
      w.addStream('s', [column('a'), column('b')], { rawLen: 2 });
      w.append('s', 0.0, [null, 2.0], new Uint8Array([97, 98]));
      w.append('s', 0.5, [NaN, 3.5], new Uint8Array([99, 100]));
      expect(() => w.append('s', 0.1, [1.0], new Uint8Array(2))).toThrow();
      expect(() => w.append('s', 0.1, [1.0, 2.0], new Uint8Array(3))).toThrow();
      w.addStream('s', [column('a'), column('b')], { rawLen: 2 });          // same again is fine
      expect(() => w.addStream('s', [column('a')])).toThrow();
      expect(() => w.addStream('x', [column('a')], { storage: 'tape' })).toThrow();
    });
    const log = readDrive(hdb.conn, ref.drive);
    expect(log.series('a').v.every((v) => Number.isNaN(v))).toBe(true);
    expect(Array.from(log.series('b').v)).toEqual([2, 3.5]);
    expect(() => closed!.append('s', 1, [1, 2], new Uint8Array(2))).toThrow();   // closed
    expect(() => column('t')).toThrow();
    expect(() => column('x', 'f3' as never)).toThrow();
    expect(() => new StreamInfo('s', [column('a'), column('a')])).toThrow();
  });

  it('wide streams and equal timestamps', async () => {
    const hdb = await HondalogDb.open(null);
    const names = Array.from({ length: SLOTS * 2 + 3 }, (_, i) => `c${i}`);
    const cols = names.map((n) => column(n, 'f8'));
    const ref = newDrive(hdb, {}, (w) => {
      w.addStream('wide', cols, { rawLen: 1 });
      [0.0, 0.1, 0.1, 0.1, 0.05, 0.2].forEach((t, i) => w.append('wide', t, names.map((_, k) => i * 100 + k), new Uint8Array([i])));
      const info = new StreamInfo('wide', cols, '', 1);
      const rows = emptyStreamData(info, 3);
      rows.t.set([0.2, 0.3, 0.3]);
      for (const n of names) rows.columns[n]!.fill(0);
      rows.columns.c0!.set([7, 8, 9]);
      rows.columns[names[names.length - 1]!]!.set([70, 80, 90]);
      rows.raw.set([7, 8, 9]);
      w.appendData('wide', rows);
    });
    const log = readDrive(hdb.conn, ref.drive);
    const data = log.streams.get('wide')!;
    expect(data.t.length).toBe(9);
    expect([...data.raw].sort((a, b) => a - b)).toEqual([0, 1, 2, 3, 4, 5, 7, 8, 9]);
    const i3 = [...data.raw].indexOf(3);
    expect(data.columns.c0![i3]).toBe(300);
    expect(data.columns[names[names.length - 1]!]![i3]).toBe(300 + names.length - 1);   // parts line up
    const last = names[names.length - 1]!;
    expect(hdb.conn.get('SELECT count(DISTINCT part) FROM samples')![0]).toBe(3);
    expect(hdb.conn.get(`SELECT count(*), max(${last}) FROM ${ref.drive}`)).toEqual([9, 510]);
    expect(hdb.conn.all(`SELECT ${last} FROM ${ref.drive} WHERE c0 IN (100, 200, 300) ORDER BY c0`)).toEqual([[110], [210], [310]]);
  });

  it('a drive is readable while it is recorded', async () => {
    const hdb = await HondalogDb.open(null);
    const store = new Store(hdb, { background: false });
    const drive = store.beginDrive({ ecu: 'P2P' });
    fill(drive);
    drive.addStream('blackbox/rails', [column('ecu_b', { units: 'V' })], { source: 'blackbox' });
    for (let i = 0; i < 500; i++) drive.append('blackbox/rails', i * 0.01, [13.8]);
    store.flush();
    let log = readDrive(hdb.conn, drive.name);
    expect(log.status).toBe('recording');
    expect(log.rows).toBe(720);
    expect(log.events).toHaveLength(4);
    expect(log.streams.get('blackbox/rails')!.t.length).toBe(500);
    drive.append('ecu/00+2', 99.0, [1], new Uint8Array([0, 4]));
    store.flush();
    expect(readDrive(hdb.conn, drive.name).series('rpm').v.at(-1)).toBe(1);
    await store.close();                                         // ends the drive that was left open
    log = readDrive(hdb.conn, drive.name);
    expect(log.status).toBe('complete');
    expect(log.meta.end_reason).toBe('closed');
    expect(log.rows).toBe(721);
  });

  it('an unclosed drive is marked crashed on the next open', async () => {
    const files = new MemoryFileStore();
    const hdb = await HondalogDb.openFile(files);
    const store = new Store(hdb, { background: false, checkpointS: 0 });
    const drive = store.beginDrive({ ecu: 'P2P', epoch_unix: 1794812400.0 });
    fill(drive);
    drive.addStream('blackbox/rails', [column('ecu_b', { units: 'V' })], { source: 'blackbox' });
    for (let i = 0; i < 300; i++) drive.append('blackbox/rails', i * 0.01, [13.8 - i / 1000]);
    store.flush();
    await hdb.persist();
    drive.append('ecu/00+2', 50.0, [1], new Uint8Array([0, 4]));     // never committed
    expect(readDrive(hdb.conn, drive.name).status).toBe('recording');   // its writer is alive: left alone
    // the page "dies" here; the file is opened again by the next session
    const again = await HondalogDb.open(await files.read('hondalog.db'), { now: () => Date.now() / 1000 + 120 });
    const log = readDrive(again.conn, drive.name);
    expect(log.status).toBe('crashed');
    expect(log.meta.end_reason).toBe('crash');
    expect(log.rows).toBe(520);
    expect(log.series('rpm').v.at(-1)).toBe(999);
    near(log.series('ecu_b').v.at(-1)!, 13.501, 1e-5);
    const [status, ended, duration, rows, pid] = again.conn.get('SELECT status, ended_at, duration_s, rows, writer_pid FROM drives')!;
    expect([status, rows, pid]).toEqual(['crashed', 520, null]);
    near(duration as number, 9.95);
    near(ended as number, 1794812400.0 + 9.95, 1e-6);
    expect(again.conn.all('SELECT DISTINCT codec FROM chunks')).toEqual([['d1']]);   // the tail was packed
    expect(again.conn.get(`SELECT count(*) FROM ${drive.name}`)![0]).toBe(200);
    // recording goes on in the same file
    const s2 = new Store(again, { background: false });
    const second = s2.beginDrive();
    second.addStream('s', [column('a')]);
    second.append('s', 0.0, [1.0]);
    second.end();
    expect(again.conn.all('SELECT status FROM drives ORDER BY id').map((r) => r[0])).toEqual(['crashed', 'complete']);
  });
});

describe('schema and migrations', () => {
  it('a fresh database: settings and schema version', async () => {
    const hdb = await HondalogDb.open(null);
    make(hdb);
    const c = hdb.conn;
    expect(c.get('PRAGMA application_id')![0]).toBe(0x48444c47);
    expect(c.get('PRAGMA auto_vacuum')![0]).toBe(2);               // incremental
    const latest = MIGRATIONS[MIGRATIONS.length - 1]!.version;
    expect(c.get('PRAGMA user_version')![0]).toBe(latest);
    const rows = c.all('SELECT version, description FROM schema_version ORDER BY version');
    expect(rows.map((r) => r[0])).toEqual(MIGRATIONS.map((m) => m.version));
    expect(schemaVersion(c)).toBe(latest);
    const tables = new Set(c.all("SELECT name FROM sqlite_master WHERE type='table'").map((r) => r[0]));
    for (const t of ['drives', 'channels', 'streams', 'samples', 'chunks', 'chunk_stats', 'events', 'alarms', 'captures', 'address_maps', 'schema_version']) expect(tables.has(t)).toBe(true);
    // the exported file is a plain rollback-journal SQLite file the Python app opens (and turns to WAL itself)
    const bytes = hdb.export();
    expect(new TextDecoder().decode(bytes.subarray(0, 15))).toBe('SQLite format 3');
    expect([bytes[18], bytes[19]]).toEqual([1, 1]);
  });

  it('migrations run once, in order, and keep the data', async () => {
    const files = new MemoryFileStore();
    const hdb = await HondalogDb.openFile(files);
    const ref = make(hdb);
    await hdb.persist();
    const base = MIGRATIONS[MIGRATIONS.length - 1]!.version;
    const calls: string[] = [];
    const grown = [...MIGRATIONS,
      { version: base + 1, description: 'weather per drive', apply: (c: HondalogDb['conn']) => { calls.push('weather'); c.run("ALTER TABLE drives ADD COLUMN weather TEXT NOT NULL DEFAULT ''"); c.run("UPDATE drives SET weather = 'dry'"); } },
      { version: base + 2, description: 'fuel fills', apply: (c: HondalogDb['conn']) => { calls.push('fuel'); c.run('CREATE TABLE fills(id INTEGER PRIMARY KEY, drive_id INTEGER, litres REAL)'); } }];
    const bytes = (await files.read('hondalog.db'))!;
    const m = await HondalogDb.open(bytes, { migrations: grown });
    expect(calls).toEqual(['weather', 'fuel']);
    expect(m.conn.get('SELECT weather FROM drives')![0]).toBe('dry');
    expect(m.conn.get('PRAGMA user_version')![0]).toBe(base + 2);
    expect(m.conn.get(`SELECT count(*) FROM ${ref.drive}`)![0]).toBe(200);    // views were rebuilt
    const migrated = m.export();
    await HondalogDb.open(migrated, { migrations: grown });
    expect(calls).toEqual(['weather', 'fuel']);                                 // not again
    const broken = [...grown, { version: base + 3, description: 'breaks', apply: (c: HondalogDb['conn']) => { c.run('CREATE TABLE half_done(x)'); throw new Error('boom'); } }];
    await expect(HondalogDb.open(migrated, { migrations: broken })).rejects.toThrow(/boom/);
    await expect(HondalogDb.open(migrated)).rejects.toThrow(/newer/);           // an older program meets the newer file
  });

  it('not a database', async () => {
    const bad = new Uint8Array(72);
    bad.set(new TextEncoder().encode('HONDALOG'));
    await expect(HondalogDb.open(bad)).rejects.toThrow(LogFormatError);
    const SQL = await (await import('./sqljs')).loadSqlJs();
    const other = new SQL.Database();
    other.run('CREATE TABLE drives(id)');
    await expect(HondalogDb.open(other.export())).rejects.toThrow(/not a hondalog database/);
    const empty = await HondalogDb.open(null);
    expect(() => readDrive(empty.conn, '')).toThrow(/no drives/);
  });
});

describe('the views, with nothing but SQL', () => {
  it('per-drive views, stalls, readings, blackbox_1s, ecu_reads, markers, drive_summary', async () => {
    const hdb = await HondalogDb.open(null);
    const first = make(hdb);
    const second = newDrive(hdb, { ecu_id: 'SIM', started_unix: 1794900000.0, epoch_unix: 1794900000.0, notes: 'second drive' }, (d) => {
      d.addStream('ecu/00+2', [column('rpm', { units: 'rpm' })], { source: 'ecu', rawLen: 2, attrs: { addr: 0 } });
      d.addStream('ecu/08+1', [column('ect', { units: '°C' })], { source: 'ecu', rawLen: 1, attrs: { addr: 8 } });
      d.addStream('blackbox/live', [column('bb_ecub', { units: 'V' }), column('bb_vbat', { units: 'V' })], { source: 'blackbox' });
      for (let i = 0; i < 400; i++) {
        const t = i * 0.05;
        d.append('ecu/00+2', t, [t < 15 ? 3000 : 0], new Uint8Array([0x2e, 0xe0]), 0.0042);
        if (i % 20 === 0) d.append('ecu/08+1', t + 0.01, [80.0 + i / 100], new Uint8Array([0x50]));
      }
      for (let i = 0; i < 2000; i++) {
        const t = i * 0.01;
        d.append('blackbox/live', t, [t < 14.2 ? 13.8 : 0.3, 13.9]);
      }
      d.event(5.0, 'marker', 'MARK 1');
      d.capture('stall', 15.0, 5.0, 20.0, { rule: 'stall', reason: 'rpm', message: 'rpm dropped to 0' });
    });
    const c = hdb.conn;
    const views = new Set(c.all("SELECT name FROM sqlite_master WHERE type='view'").map((r) => String(r[0])));
    for (const v of [first.drive, second.drive, second.drive + '_blackbox', 'stalls', 'drive_summary', 'readings', 'ecu_reads', 'blackbox_1s', 'markers', 'gps_track', 'stall_locations']) expect(views.has(v)).toBe(true);
    expect([...views].every((n) => /^[A-Za-z0-9_]+$/.test(n))).toBe(true);
    const view = c.columns(`SELECT * FROM ${second.drive} LIMIT 1`);
    expect(view.names).toEqual(['t', 'wall_time', 'rpm', 'ect', 'bb_ecub', 'bb_vbat']);
    const rows = c.all(`SELECT t, wall_time, rpm, ect, bb_ecub FROM ${second.drive}`);
    expect(rows.length).toBe(400);
    expect(rows[0]![2]).toBe(3000);
    expect(rows.at(-1)![2]).toBe(0);
    expect(rows[0]![3]).toBeNull();
    near(rows[1]![3] as number, 80.0, 1e-5);
    near(rows[45]![3] as number, 80.4, 1e-5);
    near(rows[100]![4] as number, 13.8, 1e-5);
    near(rows.at(-1)![4] as number, 0.3, 1e-5);
    expect(String(rows[0]![1]).length).toBe(23);
    const bb = c.get(`SELECT t, bb_ecub_min, bb_ecub_mean, bb_ecub_max FROM ${second.drive}_blackbox WHERE t = 14`)!;
    expect(bb[0]).toBe(14);
    near(bb[1] as number, 0.3, 1e-6);
    near(bb[2] as number, 13.8 * 0.2 + 0.3 * 0.8, 1e-5);
    near(bb[3] as number, 13.8, 1e-5);
    expect(c.all('SELECT drive, status, rows, stalls, captures, markers, channels, notes FROM drive_summary ORDER BY drive_id')).toEqual([
      [first.drive, 'complete', 220, 0, 0, 1, 3, ''], [second.drive, 'complete', 2420, 1, 1, 1, 4, 'second drive']]);
    const stall = c.all('SELECT name, drive, reason, message, t_trigger, t_start, t_end, rows FROM stalls');
    expect(stall).toHaveLength(1);
    expect(stall[0]!.slice(1, 7)).toEqual([second.drive, 'rpm', 'rpm dropped to 0', 15, 5, 20]);
    const ecub = c.all(`SELECT s.name, b.t - s.t_trigger, b.min, b.mean, b.samples FROM stalls s JOIN blackbox_1s b ON b.drive_id = s.drive_id AND b.channel = 'bb_ecub'
        WHERE b.t BETWEEN s.t_trigger - 5 AND s.t_trigger ORDER BY b.t`);
    expect(ecub.map((r) => r[1])).toEqual([-5, -4, -3, -2, -1, 0]);
    expect(ecub[4]![4]).toBe(100);
    const reads = c.all('SELECT t, addr, block, raw, raw_hex, latency_ms FROM ecu_reads WHERE drive = ? ORDER BY t LIMIT 2', [second.drive]);
    expect(reads[0]!.slice(0, 3)).toEqual([0, 0, 'ecu/00+2']);
    expect(Array.from(reads[0]![3] as Uint8Array)).toEqual([0x2e, 0xe0]);
    expect(reads[0]!.slice(4)).toEqual(['2EE0', 4.2]);
    expect(reads[1]!.slice(1, 3)).toEqual([8, 'ecu/08+1']);
    expect(reads[1]![5]).toBeNull();
    expect(c.get('SELECT count(*) FROM ecu_reads')![0]).toBe(200 + 400 + 20);
    expect(c.all('SELECT drive, t, kind, text FROM markers WHERE drive = ?', [first.drive])).toEqual([
      [first.drive, 1.0, 'marker', 'MARK 1'], [first.drive, 2.5, 'trigger', 'STALL (rpm 0)'], [first.drive, 3.5, 'alarm', 'ECT high']]);
    expect(c.all('SELECT name, state, severity, channel, value, unverified FROM alarms')).toEqual([['ect_high', 'raised', 'warn', 'ect', 106.0, 1]]);
    expect(JSON.parse(String(c.get('SELECT attrs FROM streams WHERE drive_id = 1 AND id = 1')![0]))).toEqual({ addr: 0, length: 2 });
    expect(c.get('SELECT attrs FROM streams WHERE drive_id = 1 AND id = 1')![0]).toBe('{"addr": 0, "length": 2}');   // Python's json.dumps
    expect(c.get('SELECT count(*) FROM address_maps')![0]).toBe(1);
  });

  it('deleting a drive removes its rows and views', async () => {
    const hdb = await HondalogDb.open(null);
    const first = make(hdb);
    const second = make(hdb);
    expect(second.drive).toBe(first.drive + '_2');                  // same minute: made unique
    expect(deleteDrives(hdb.conn, [first.drive, 'nope'])).toEqual([first.drive]);
    const c = hdb.conn;
    expect(c.all('SELECT name FROM drives')).toEqual([[second.drive]]);
    expect(c.get('SELECT count(*), count(DISTINCT drive_id) FROM samples')).toEqual([220, 1]);
    expect(c.get('SELECT count(*) FROM events')![0]).toBe(3);
    expect(c.get('SELECT count(*) FROM address_maps')![0]).toBe(1);  // still used by the other
    const views = new Set(c.all("SELECT name FROM sqlite_master WHERE type='view'").map((r) => r[0]));
    expect(views.has(first.drive)).toBe(false);
    expect(views.has(second.drive)).toBe(true);
    expect(vacuum(c, true).check).toBe('ok');
    expect(readDrive(c, second.drive).rows).toBe(220);
  });
});

// ---- the black box in chunks ----

function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function bbRows(n: number, rate = 1000): StreamData {
  const r = rng(3);
  const info = new StreamInfo('blackbox/live', [column('bb_ecub', { units: 'V' }), column('bb_rpm', { units: 'rpm' }),
    column('bb_on', { dtype: 'u1', kind: 'flag' }), column('odd', 'f8')], 'blackbox', 2);
  const rows = emptyStreamData(info, n);
  for (let i = 0; i < n; i++) {
    rows.t[i] = 5.0 + i / rate + r() * 1e-5;
    rows.columns.bb_ecub![i] = Math.fround(Math.round((13.8 + (r() - 0.5) * 0.1) * 1000) / 1000);   // millivolts, like the box
    rows.columns.bb_rpm![i] = Math.fround(Math.round(3000 + 500 * Math.sin(i / 300)));
    rows.columns.bb_on![i] = r() < 0.5 ? 0 : 1;
    rows.columns.odd![i] = i % 97 === 0 ? NaN : (r() - 0.5) * 2;                 // fits no decimal scale
    rows.raw[2 * i] = Math.floor(r() * 256);
    rows.raw[2 * i + 1] = Math.floor(r() * 256);
  }
  return rows;
}

const bytesEqual = (a: Uint8Array, b: Uint8Array) => a.length === b.length && a.every((x, i) => x === b[i]);

describe('the black box in chunks', () => {
  it('zlib: our stored blocks and Python-compressed data', () => {
    const data = new Uint8Array(200_000).map((_, i) => (i * 7) & 0xff);
    expect(bytesEqual(inflate(deflate(data)), data)).toBe(true);
    expect(inflate(deflate(new Uint8Array(0)))).toHaveLength(0);
  });

  it('the codec is lossless and small', () => {
    const rows = bbRows(20_000);
    const info = rows.info;
    const blob = codec.encode(rows);
    const back = codec.decode(codec.PACKED, blob, info, 20_000);
    expect(bytesEqual(packRows(back), packRows(rows))).toBe(true);            // bit for bit, NaN included
    expect(bytesEqual(packRows(codec.decode(codec.RAW, codec.encode(rows, codec.RAW), info, 20_000)), packRows(rows))).toBe(true);
    for (const n of [1, 0]) {
      const part = { ...rows, t: rows.t.slice(0, n), latency: rows.latency.slice(0, n), raw: rows.raw.slice(0, 2 * n),
        columns: Object.fromEntries(Object.entries(rows.columns).map(([k, v]) => [k, v.slice(0, n)])) };
      expect(bytesEqual(packRows(codec.decode(codec.PACKED, codec.encode(part), info, n)), packRows(part))).toBe(true);
    }
    expect(() => codec.decode('zstd9', blob, info, 20_000)).toThrow();
    expect(() => codec.encode(rows, 'zstd9')).toThrow();
  });

  it('decodes the d1 block the Python app wrote', async () => {
    const { readFileSync } = await import('node:fs');
    const { join } = await import('node:path');
    const hdb = await HondalogDb.open(new Uint8Array(readFileSync(join(__dirname, 'fixtures', 'python-sim.db'))));
    const log = readDrive(hdb.conn, '');
    const s = log.streams.get('blackbox/live')!;
    // re-encode what we read with our codec, decode again: the same bytes
    const again = codec.decode(codec.PACKED, codec.encode(s), s.info, s.t.length);
    expect(bytesEqual(packRows(again), packRows(s))).toBe(true);
  });

  it('row by row (raw chunks, packed every compactS), whole arrays and windows', async () => {
    const rows = bbRows(90_000);
    const info = rows.info;
    const hdb = await HondalogDb.open(null);
    const store = new Store(hdb, { background: false, compactS: 30.0 });
    const drive = store.beginDrive({ epoch_unix: 1794812400.0 });
    drive.addStream(info.name, info.columns, { source: 'blackbox', rawLen: 2 });
    const names = info.columnNames;
    for (let start = 0; start < 90_000; start += 500) {         // a commit every 0.5 s of data
      for (let i = start; i < start + 500; i++) drive.append(info.name, rows.t[i]!, names.map((n) => rows.columns[n]![i]!), rows.raw.subarray(2 * i, 2 * i + 2));
      store.flush();
      if (start === 20_000) {
        expect(hdb.conn.all('SELECT DISTINCT codec FROM chunks')).toEqual([['raw']]);
        expect(hdb.conn.get('SELECT count(*) FROM chunks')![0]).toBe(41);
      }
    }
    const kinds = Object.fromEntries(hdb.conn.all('SELECT codec, count(*) FROM chunks GROUP BY codec'));
    expect(kinds.d1).toBe(2);
    expect(kinds.raw).toBeLessThan(60);
    expect(bytesEqual(packRows(readDrive(hdb.conn, drive.name).streams.get(info.name)!), packRows(rows))).toBe(true);   // both kinds as one stream
    drive.end();
    expect(hdb.conn.all('SELECT codec, count(*) FROM chunks GROUP BY codec')).toEqual([['d1', 3]]);
    const sum = hdb.conn.get('SELECT sum(n_rows), min(t_start), max(t_end) FROM chunks')!;
    expect(sum[0]).toBe(90_000);
    near(sum[1] as number, rows.t[0]!);
    near(sum[2] as number, rows.t[89_999]!);
    const log = readDrive(hdb.conn, drive.name);
    const stream = log.streams.get(info.name)!;
    expect(bytesEqual(packRows(stream), packRows(rows))).toBe(true);
    const cut = log.window(40.0, 41.5);
    const keep = Array.from(rows.t.keys()).filter((i) => rows.t[i]! >= 40.0 && rows.t[i]! <= 41.5);
    expect(cut.streams.get(info.name)!.t.length).toBe(keep.length);
    // one row per channel per second, also for seconds that straddle two packed blocks
    for (const second of [5, 34, 35, 64, 94]) {
      const mine = keepWhere(rows, second).map((i) => rows.columns.bb_ecub![i]!);
      const [n, lo, mean, hi] = hdb.conn.get("SELECT samples, min, mean, max FROM blackbox_1s WHERE channel = 'bb_ecub' AND t = ?", [second])! as number[];
      expect([n, lo, hi]).toEqual([mine.length, Math.min(...mine), Math.max(...mine)]);
      near(mean!, mine.reduce((a, b) => a + b, 0) / mine.length, 1e-9);
    }
    const odd = keepWhere(rows, 50).map((i) => rows.columns.odd![i]!);
    expect(hdb.conn.get("SELECT samples FROM blackbox_1s WHERE channel = 'odd' AND t = 50")![0]).toBe(odd.filter(Number.isFinite).length);
    expect(hdb.conn.get('SELECT count(*) FROM blackbox_1s')![0]).toBe(4 * 90);
    // whole arrays are packed directly; a capture window counts chunk rows exactly
    const copy = writeLog(hdb, log);
    const back = readDrive(hdb.conn, copy.drive);
    expect(back.status).toBe('imported');
    expect(bytesEqual(packRows(back.streams.get(info.name)!), packRows(rows))).toBe(true);
    let cap: { ref: { capture: string }; rows: number } | null = null;
    newDrive(hdb, {}, (w) => {
      w.addStream(info.name, info.columns, { source: 'blackbox', rawLen: 2 });
      const rev = { ...rows, t: rows.t.slice().reverse(), latency: rows.latency.slice().reverse(), raw: new Uint8Array(rows.raw.length),
        columns: Object.fromEntries(Object.entries(rows.columns).map(([k, v]) => [k, v.slice().reverse()])) };
      for (let i = 0; i < 90_000; i++) rev.raw.set(rows.raw.subarray(2 * (89_999 - i), 2 * (89_999 - i) + 2), 2 * i);
      w.appendData(info.name, rev);                               // out of order: sorted on the way in
      cap = w.capture('stall', 41.0, 40.0, 41.5);
    });
    expect(cap!.rows).toBe(keep.length);
    const c = readDrive(hdb.conn, cap!.ref.capture);
    expect(c.rows).toBe(keep.length);
    expect(c.t0).toBeGreaterThanOrEqual(40.0);
    expect(c.t1).toBeLessThanOrEqual(41.5);
  }, 60_000);

  it('the storage choice; low-rate streams stay rows', async () => {
    const hdb = await HondalogDb.open(null);
    const ref = newDrive(hdb, {}, (w) => {
      w.addStream('math', [column('afr')], { source: 'math' });
      w.addStream('blackbox/rails', [column('v')], { source: 'blackbox' });
      w.addStream('blackbox/slow', [column('temp')], { source: 'blackbox', storage: 'rows' });
      w.addStream('ecu/fast', [column('x')], { source: 'ecu', storage: 'chunks' });
      for (const n of ['math', 'blackbox/rails', 'blackbox/slow', 'ecu/fast']) w.append(n, 1.0, [2.0]);
    });
    expect(Object.fromEntries(hdb.conn.all('SELECT name, storage FROM streams'))).toEqual({
      math: 'rows', 'blackbox/rails': 'chunks', 'blackbox/slow': 'rows', 'ecu/fast': 'chunks' });
    expect(hdb.conn.get('SELECT count(*) FROM samples')![0]).toBe(2);
    const log = readDrive(hdb.conn, ref.drive);
    for (const n of ['afr', 'v', 'temp', 'x']) expect(Array.from(log.series(n).v)).toEqual([2.0]);
  });

  it('the background writer commits on its own and persists through the FileStore', async () => {
    const files = new MemoryFileStore();
    const hdb = await HondalogDb.openFile(files);
    const store = new Store(hdb, { flushIntervalS: 0.02, checkpointS: 0.05 });
    const drive = store.beginDrive();
    drive.addStream('s', [column('a')]);
    for (let i = 0; i < 50; i++) drive.append('s', i * 0.01, [i]);
    await new Promise((r) => setTimeout(r, 200));
    expect(readDrive(hdb.conn, drive.name).rows).toBe(50);        // without flush() or close()
    const saved = await HondalogDb.open(await files.read('hondalog.db'));
    expect(readDrive(saved.conn, drive.name).rows).toBe(50);       // and in the file
    await store.close();
    await store.close();                                           // twice is harmless
    expect(store.error).toBeNull();
    expect(drive.closed).toBe(true);
    expect(() => store.beginDrive()).toThrow();
  });
});

function keepWhere(rows: StreamData, second: number): number[] {
  return Array.from(rows.t.keys()).filter((i) => Math.floor(rows.t[i]!) === second);
}

describe('reading', () => {
  it('series merges a channel read in two streams', async () => {
    const hdb = await HondalogDb.open(null);
    const ref = newDrive(hdb, {}, (w) => {
      w.addStream('fast', [column('rpm')]);
      w.addStream('slow', [column('rpm'), column('ect')]);
      for (let i = 0; i < 10; i++) w.append('fast', i * 0.1, [i]);
      w.append('slow', 0.25, [100, 80]);
      w.append('slow', 0.75, [200, 81]);
    });
    const log = readDrive(hdb.conn, ref.drive);
    const s = log.series('rpm');
    expect(s.t.length).toBe(12);
    expect(s.v[3]).toBe(100);
    expect(s.v[9]).toBe(200);
    expect(log.streamsWith('rpm')).toHaveLength(2);
    expect(log.channels).toEqual(['rpm', 'ect']);
    expect(hdb.conn.columns(`SELECT * FROM ${ref.drive}`).names).toEqual(['t', 'wall_time', 'rpm', 'ect']);
  });

  it('align: gaps and modes', () => {
    const s = { t: Float64Array.from([0, 1, 2, 10]), v: Float64Array.from([0, 10, 20, 100]) };
    const grid = Float64Array.from([-1, 0.5, 1, 2.9, 6, 10]);
    const held = align(s, grid, 'hold', 1.5);
    expect(Number.isNaN(held[0]!)).toBe(true);
    expect(Array.from(held.subarray(1, 4))).toEqual([0, 10, 20]);
    expect(Number.isNaN(held[4]!)).toBe(true);
    expect(held[5]).toBe(100);
    const lin = align(s, grid, 'linear', 1.5);
    expect(lin[1]).toBe(5);
    expect(Number.isNaN(lin[3]!) && Number.isNaN(lin[4]!)).toBe(true);
    expect(lin[5]).toBe(100);
    expect(Array.from(align(s, grid).subarray(1, 4))).toEqual([0, 10, 20]);
    expect(align({ t: new Float64Array(0), v: new Float64Array(0) }, grid).every((v) => Number.isNaN(v))).toBe(true);
  });

  it('two drives of one session joined into one window', async () => {
    const hdb = await HondalogDb.open(null);
    const refs = [0.0, 10.0].map((t0) => newDrive(hdb, { session: 'one' }, (w) => {
      w.addStream('s', [column('a')]);
      for (let i = 0; i < 100; i++) w.append('s', t0 + i * 0.1, [t0 + i * 0.1]);
    }));
    const cut = mergeWindow(refs.map((r) => readDrive(hdb.conn, r.drive)), 8.0, 12.0);
    const s = cut.series('a');
    expect(s.t.length).toBe(41);
    near(s.t[0]!, 8.0, 1e-6);
    expect(s.t.every((x, i) => i === 0 || x > s.t[i - 1]!)).toBe(true);
  });

  it('an in-memory log, and write_log with odd names', async () => {
    const info = new StreamInfo('m', [column('x', 'f8')], 'math');
    const rows = emptyStreamData(info, 5);
    rows.t.set([0, 1, 2, 3, 4]);
    rows.columns.x!.set([0, 1, 4, 9, 16]);
    const log = new Log({ k: 1 }, [rows], [{ t: 2.0, kind: 'marker', text: 'm', data: {} }]);
    expect(log.rows).toBe(5);
    expect(log.series('x').v[3]).toBe(9);
    expect(log.status).toBe('memory');
    const hdb = await HondalogDb.open(null);
    const ref = writeLog(hdb, log, { extra: true }, { name: 'drive_test' });
    expect(ref.drive).toBe('drive_test');
    const odd = ['samples', 'stalls', '2nd try; DROP', 'drive_test'].map((n) => writeLog(hdb, log, {}, { name: n }).drive);
    expect(odd).toEqual(['samples_2', 'stalls_2', 'drive_2nd_try_DROP', 'drive_test_2']);
    for (const n of odd) expect(hdb.conn.get(`SELECT count(*) FROM ${n}`)![0]).toBe(5);
    expect(hdb.conn.get('SELECT count(*) FROM samples')![0]).toBe(25);
    const back = readDrive(hdb.conn, ref.drive);
    expect(back.meta.k).toBe(1);
    expect(back.meta.extra).toBe(true);
    expect(back.status).toBe('imported');
    expect(Array.from(back.series('x').v)).toEqual([0, 1, 4, 9, 16]);
    expect(back.streams.get('m')!.info.source).toBe('math');
    expect(back.markers).toEqual([[2.0, 'm']]);
    const empty = new Log();
    expect(empty.t0).toBe(0);
    expect(empty.duration).toBe(0);
    expect(empty.channels).toEqual([]);
    expect(empty.table().t.length).toBe(0);
  });
});
