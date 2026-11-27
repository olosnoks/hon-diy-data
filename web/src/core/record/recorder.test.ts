// @vitest-environment node
/** The recorder: drives in the database, captures, key-off; retention, library (port of tests/test_recorder.py). */
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { HondalogDb } from '../store/db';
import { KeepProtected, applyRetention, deleteEntry, describe as describeEntry, listEntries, lookup, retentionPlan, search } from '../store/library';
import { column } from '../store/model';
import { readDrive } from '../store/reader';
import { annotate, newDrive } from '../store/writer';
import { MemoryFileStore } from '../store/filestore';
import { triggerRule } from './engine';
import { type RecorderOptions, Recorder, type Sample } from './recorder';
import { recorderMapFromToml } from './testing';

const TOML = readFileSync(join(resolve(__dirname), '../store/fixtures/addressmap.toml'), 'utf8');
const amap = recorderMapFromToml(TOML);
const T0 = new Date(2026, 10, 15, 8, 0, 0).getTime() / 1000;

function sample(t: number, rpm = 800, link = true, more: Record<string, number> = {}): Sample {
  return { t, wallUnix: T0 + t, link, values: link ? { rpm, ect: 85.0, batt: 13.9, ...more } : {} };
}

async function db(): Promise<HondalogDb> {
  return HondalogDb.open(null);
}

function recorder(hdb: HondalogDb, opts: RecorderOptions = {}): Recorder {
  return new Recorder(hdb, amap, { ecuId: 'SIM-P2P', port: 'sim:', background: false, checkpointS: 0, ...opts });
}

/** numpy arange(t0, t1, step) */
function arange(t0: number, t1: number, step = 0.1): number[] {
  const n = Math.ceil((t1 - t0) / step);
  return Array.from({ length: Math.max(0, n) }, (_, i) => t0 + i * step);
}

function run(rec: Recorder, t0: number, t1: number, rpm = 800, link = true, step = 0.1): void {
  for (const t of arange(t0, t1, step)) rec.feed(sample(t, rpm, link));
}

const r1 = (x: number) => Math.round(x * 10) / 10;
const near = (a: number, b: number, tol: number) => expect(Math.abs(a - b)).toBeLessThanOrEqual(tol);

describe('recording', () => {
  it('records sweeps and a stall capture', async () => {
    const hdb = await db();
    const rec = recorder(hdb);
    expect(rec.ref.drive.startsWith('drive_')).toBe(true);
    expect(rec.drives).toEqual([rec.ref]);
    run(rec, 0, 50);
    rec.mark('MARK 1');
    rec.mark('it stumbled');
    run(rec, 50, 52);
    run(rec, 52, 63, 0);
    expect(rec.notices[0]!.reason).toBe('rpm');              // known at once, before the capture is saved
    const event = rec.events[0]!;
    expect(event.reason).toBe('rpm');
    expect(event.rule).toBe('stall');
    near(event.t, 52.0, 1e-9);
    expect(event.ref.capture).toBe('stall_20261115_080052');
    expect(event.ref.drive).toBe(rec.ref.drive);
    run(rec, 63, 64, 0);
    rec.note('towed home');
    await rec.stop();
    await rec.stop();
    expect(rec.events).toHaveLength(1);
    expect(rec.rows).toBe(640);
    expect(rec.markers).toBe(2);
    expect(rec.writeError).toBeNull();

    const log = readDrive(hdb.conn, rec.ref.drive);
    expect(log.status).toBe('complete');
    expect(log.meta.end_reason).toBe('stopped');
    expect(log.streams.get('ecu')!.t.length).toBe(640);
    expect(log.meta.address_map_version).toBe(amap.version);
    expect(log.meta.ecu_id).toBe('SIM-P2P');
    expect(log.meta.address_map_text).toContain('[[channel]]');           // the map travels with the log
    expect(log.meta.triggers.slice(0, 2).map((r: { name: string }) => r.name)).toEqual(['stall', 'link_loss']);
    expect(log.events.map((e) => [r1(e.t), e.kind, e.text])).toEqual([
      [50.0, 'marker', 'MARK 1'], [50.0, 'marker', 'it stumbled'], [52.0, 'trigger', 'STALL (rpm 0)'], [63.9, 'note', 'towed home']]);
    expect(log.events[2]!.data.message).toBe('rpm dropped to 0');
    expect(log.units('ect')).toBe('°C');
    expect(log.column('vtec')!.kind).toBe('flag');
    expect(log.series('map').v.every((v) => Number.isNaN(v))).toBe(true);    // not in these samples

    const cap = readDrive(hdb.conn, event.ref.capture);
    expect(cap.meta.trigger).toBe('stall');
    expect(cap.meta.stall_trigger).toBe('rpm dropped to 0');
    near(Number(cap.meta.stall_time_s), 52.0, 1e-9);
    expect(cap.meta.window_complete).toBe(true);
    expect(cap.meta.capture_of).toBe(rec.ref.drive);
    expect(String(cap.meta.stall_wall_time).startsWith('2026-11-15T08:00:52')).toBe(true);
    const rpm = cap.series('rpm');
    near(rpm.t[0]!, 22.0, 0.11);
    expect(rpm.t[rpm.t.length - 1]!).toBeGreaterThanOrEqual(62.0);
    expect(rpm.t[rpm.t.length - 1]!).toBeLessThanOrEqual(62.2);
    expect(rpm.v[0]).toBe(800);
    expect(rpm.v[rpm.v.length - 1]).toBe(0);
    expect(event.rows).toBe(cap.rows);
    expect(cap.events.map((e) => e.text)).toEqual(['MARK 1', 'it stumbled', 'STALL (rpm 0)']);

    // the capture is a row, not a copy; plain SQL sees the stall
    const c = hdb.conn;
    expect(c.get('SELECT count(*), count(DISTINCT drive_id) FROM samples WHERE part = 0')).toEqual([640, 1]);
    const stall = c.get('SELECT name, drive, reason, t_trigger, t_start, t_end, complete, rows FROM stalls')!;
    expect(stall.slice(0, 3)).toEqual(['stall_20261115_080052', rec.ref.drive, 'rpm']);
    near(stall[3] as number, 52, 1e-9);
    near(stall[4] as number, 22, 1e-9);
    near(stall[5] as number, 62, 1e-9);
    expect(stall.slice(6)).toEqual([1, event.rows]);
    expect(c.get('SELECT stall_count, capture_count, status FROM drives')).toEqual([1, 1, 'complete']);
    expect(c.all(`SELECT rpm, link FROM ${rec.ref.drive} WHERE t > 62.95`)).toEqual(Array(10).fill([0, 1]));
  });

  it('link-loss capture, stopped in the middle of it', async () => {
    const hdb = await db();
    const rec = recorder(hdb, { stall: { postS: 5, linkDownS: 1.0 } });
    run(rec, 0, 10);
    run(rec, 10, 12, 800, false);
    expect(rec.capturing).toBe(true);
    expect(rec.events).toHaveLength(0);
    await rec.stop();                                          // stopped before postS was over
    const event = rec.events[0]!;
    expect(event.reason).toBe('link');
    near(event.t, 11.0, 0.11);
    const cap = readDrive(hdb.conn, event.ref.capture);
    expect(cap.meta.stall_trigger).toContain('link lost');
    expect(cap.meta.window_complete).toBe(false);
    const link = cap.series('link').v;
    const rpm = cap.series('rpm').v;
    expect(link[link.length - 1]).toBe(0);
    expect(Number.isNaN(rpm[rpm.length - 1]!)).toBe(true);
    expect(rpm[0]).toBe(800);
  });

  it('custom rules, the manual trigger and header facts', async () => {
    const hdb = await db();
    const hot = triggerRule({ name: 'overtemp', when: 'ect > 105', holdS: 0.5, preS: 2, postS: 1, kind: 'overtemp', message: 'coolant above 105' });
    const quiet = triggerRule({ name: 'note_only', when: 'batt < 12', actions: ['mark'] });
    const rec = recorder(hdb, { rules: [hot, quiet] });
    expect(rec.rules.map((r) => r.name)).toEqual(['overtemp', 'note_only']);
    run(rec, 0, 5);
    for (const t of arange(5, 8)) rec.feed(sample(t, 800, true, { ect: 110, batt: 11.5 }));
    const event = rec.events[0]!;
    expect(event.reason).toBe('overtemp');
    expect(event.ref.capture.startsWith('overtemp_')).toBe(true);
    expect(event.label).toBe('OVERTEMP');
    expect(rec.notices).toHaveLength(1);                       // "note_only" marks but does not notify
    rec.setMeta({ dtc: ['P0135'], notes: 'hot day' });
    rec.event('alarm', 'ECT high', { limit: 105 });
    rec.trigger();                                             // the capture button
    run(rec, 8, 20);
    const manual = rec.events[1]!;
    expect(manual.reason).toBe('manual');
    expect(manual.ref.capture.startsWith('capture_')).toBe(true);
    expect(manual.ref.drive).toBe(event.ref.drive);
    await rec.stop();
    const log = readDrive(hdb.conn, rec.ref.drive);
    expect(log.meta.dtc).toEqual(['P0135']);
    expect(log.meta.notes).toBe('hot day');
    const kinds = log.events.map((e) => `${e.kind}|${e.text}`);
    for (const k of ['trigger|OVERTEMP', 'trigger|NOTE_ONLY', 'alarm|ECT high', 'trigger|CAPTURE']) expect(kinds).toContain(k);
    const cap = readDrive(hdb.conn, manual.ref.capture);
    expect(cap.meta.dtc).toEqual(['P0135']);
    near(cap.t0, 0, 0.01);
    rec.mark('late');
    rec.feed(sample(99));                                      // after stop: ignored, no error
    rec.feedRead(99, 0, new Uint8Array(2));
    rec.feedLink(99, false);
    rec.feedValues('x', 99, { a: 1 });
    rec.trigger();
    expect(rec.rows).toBe(200);
  });

  it('per-read recording keeps raw bytes and true times', async () => {
    const hdb = await db();
    const rec = recorder(hdb, { stall: { postS: 1 } });
    for (let i = 0; i < 100; i++) {
      const t = i * 0.06;
      const rpm = i < 80 ? 3000 : 0;
      rec.feedRead(t, 0x00, new Uint8Array([(rpm * 4) >> 8, (rpm * 4) & 0xff]), 0.0035);
      rec.feedRead(t + 0.02, 0x12, new Uint8Array([60, 141, 24 + 2 * (i % 50), 23]));
      if (i % 10 === 0) rec.feedRead(t + 0.04, 0x16, new Uint8Array([0, 145, 128, 0]));
    }
    rec.feedLink(6.1, false);
    await rec.stop();
    const event = rec.events[0]!;
    expect(event.reason).toBe('rpm');
    near(event.t, 4.8, 1e-9);
    const log = readDrive(hdb.conn, rec.ref.drive);
    expect(new Set(log.streams.keys())).toEqual(new Set(['ecu/00+2', 'ecu/12+4', 'ecu/16+4', 'ecu/link']));
    const fast = log.streams.get('ecu/00+2')!;
    expect(fast.info.rawLen).toBe(2);
    expect(fast.info.attrs.addr).toBe(0);
    expect(fast.t.length).toBe(100);
    expect(Array.from(fast.raw.subarray(0, 2))).toEqual([0x2e, 0xe0]);
    expect(log.series('rpm').v[0]).toBe(3000);
    for (const l of fast.latency) near(l, 0.0035, 1e-12);
    expect(log.streams.get('ecu/12+4')!.latency.every((v) => Number.isNaN(v))).toBe(true);   // not given: unknown, not zero
    near(log.series('map').v[0]!, 60 * 0.716 - 5, 1e-5);
    near(log.series('tps').v[1]!, 26 / 2.55, 1e-5);
    expect(log.streams.get('ecu/16+4')!.info.columnNames).toEqual(['batt', 'alt_fr', 'eld']);
    near(log.series('rpm').t[1]!, 0.06, 1e-12);
    near(log.series('tps').t[1]!, 0.08, 1e-12);
    near(log.series('batt').v[0]!, 145 / 10.45, 1e-5);
    expect(Array.from(log.series('link').v)).toEqual([1, 0]);
    const table = log.table(['rpm', 'tps', 'batt'], { base: 'rpm' });
    expect(table.t.length).toBe(100);
    expect(table.columns.tps![5]).toBe(log.series('tps').v[4]);   // read 20 ms after rpm
  });

  it('a black box stream shares the capture', async () => {
    const hdb = await db();
    const rec = recorder(hdb, { stall: { postS: 1, preS: 3, linkDownS: 1.0 } });
    const cols = [column('ecu_b_min', { units: 'V' }), column('ig1_min', { units: 'V' })];
    for (let i = 0; i < 1000; i++) {
      const t = i * 0.01;
      const volts = t < 7.5 ? 13.8 : 0.2;
      rec.feedValues('blackbox/rails', t, { ecu_b_min: volts, ig1_min: 13.7 }, { columns: cols });
      if (i % 10 === 0) rec.feed(sample(t, t < 7.5 ? 800 : 0, t < 7.5));
    }
    await rec.stop();
    const event = rec.events[0]!;
    expect(event.reason).toBe('link');
    const cap = readDrive(hdb.conn, event.ref.capture);
    expect(new Set(cap.streams.keys())).toEqual(new Set(['ecu', 'blackbox/rails']));
    const rail = cap.series('ecu_b_min');
    expect(rail.units).toBe('V');
    expect(cap.streams.get('blackbox/rails')!.info.source).toBe('blackbox');
    const drop = rail.t[rail.v.findIndex((v) => v < 6)]!;
    near(drop, 7.5, 0.011);                                     // the rail fell a full second before the trigger
    near(event.t - drop, 1.0, 0.11);
    expect(hdb.conn.get("SELECT DISTINCT codec FROM chunks")).toEqual(['d1']);   // packed at the end of the drive
  });

  it('black box values can trigger', async () => {
    const hdb = await db();
    const rule = triggerRule({ name: 'rail_drop', when: 'ecu_b_min < 6', preS: 1, postS: 0.5, kind: 'rail' });
    const rec = recorder(hdb, { rules: [rule] });
    for (let i = 0; i < 300; i++) rec.feedValues('blackbox/rails', i * 0.01, { ecu_b_min: i < 200 ? 13.8 : 1.0 });
    await rec.stop();
    near(rec.events[0]!.t, 2.0, 1e-9);
  });
});

describe('drives and key-off', () => {
  it('key off ends the drive and the next answer starts a new one', async () => {
    const hdb = await db();
    const rec = recorder(hdb, { driveGapS: 20.0, stall: { preS: 15, postS: 2, linkDownS: 1.0 } });
    const cols = [column('ecu_b_min', { units: 'V' })];
    const box = (t0: number, t1: number) => {
      for (const t of arange(t0, t1, 0.05)) rec.feedValues('blackbox/rails', t, { ecu_b_min: 12.5 }, { columns: cols, evaluate: false });
    };
    run(rec, 0, 30);
    box(0, 30);
    run(rec, 30, 33, 800, false);                               // key off: the link-loss capture opens
    box(30, 33);
    run(rec, 33, 49.9, 800, false);                             // not yet 20 s of silence
    const linkCap = rec.events[0]!;
    expect(linkCap.reason).toBe('link');
    expect(rec.parked).toBe(false);
    expect(rec.drives).toHaveLength(1);
    run(rec, 49.9, 55, 800, false);
    expect(rec.parked).toBe(true);
    expect(rec.drives).toHaveLength(1);
    expect(rec.status().bufferS).toBe(0);
    box(55, 100);                                              // the box keeps talking: not recorded
    rec.mark('pushed it home');                                // waits for the next drive
    const fed = rec.rows;
    run(rec, 100, 130);                                        // key on again
    expect(rec.parked).toBe(false);
    expect(rec.drives).toHaveLength(2);
    expect(rec.rows).toBe(fed + 300);
    run(rec, 130, 135, 0);                                     // a stall 30 s into the second drive
    await rec.stop();
    const stall = rec.events[1]!;
    const [first, second] = rec.drives as [typeof rec.ref, typeof rec.ref];
    expect(first.drive).not.toBe(second.drive);
    expect(stall.ref.drive).toBe(second.drive);
    expect(linkCap.ref.drive).toBe(first.drive);
    const a = readDrive(hdb.conn, first.drive);
    const b = readDrive(hdb.conn, second.drive);
    expect([a.status, a.meta.end_reason]).toEqual(['complete', 'link_lost']);
    expect([b.status, b.meta.end_reason]).toEqual(['complete', 'stopped']);
    expect(a.t0).toBe(0);
    near(a.t1, 50.0, 0.11);                                    // ends when the silence was long enough
    near(b.t0, 100.0, 1e-9);
    near(b.t1, 134.9, 1e-9);                                   // same clock, nothing in between
    expect(b.meta.previous_drive).toBe(first.drive);
    expect(b.meta.drive_of_session).toBe(2);
    expect(b.meta.epoch_unix).toBe(a.meta.epoch_unix);
    expect(b.meta.ecu_id).toBe('SIM-P2P');
    const ecuB = a.series('ecu_b_min');
    expect(ecuB.t[ecuB.t.length - 1]!).toBeLessThan(50.1);
    expect(b.channels).not.toContain('ecu_b_min');
    expect(b.eventsOf('marker').map((e) => e.text)).toEqual(['pushed it home']);
    const cap = readDrive(hdb.conn, stall.ref.capture);           // 15 s before the stall, all in the second drive
    const rpm = cap.series('rpm');
    near(rpm.t[0]!, 115.0, 0.11);
    expect(rpm.t[rpm.t.length - 1]!).toBeGreaterThanOrEqual(132.0);
    expect(rpm.t.every((x, i) => i === 0 || x > rpm.t[i - 1]!)).toBe(true);
    expect(hdb.conn.all('SELECT drive, stalls, captures FROM drive_summary ORDER BY drive_id')).toEqual([
      [first.drive, 1, 1], [second.drive, 1, 1]]);
    const started = hdb.conn.all('SELECT started_iso FROM drives ORDER BY id').map((r) => String(r[0]));
    expect(started[1]!.startsWith('2026-11-15T08:01:40')).toBe(true);   // the second drive began 100 s into the session
  });

  it('key on with a dead ECU stays in the drive', async () => {
    const hdb = await db();
    const rec = recorder(hdb, { driveGapS: 20.0, stall: { preS: 15, postS: 2, linkDownS: 1.0 } });
    const cols = [column('bb_ig1', { units: 'V' }), column('bb_ecub', { units: 'V' })];
    const box = (t0: number, t1: number, key = true, ecub = 12.3) => {
      for (const t of arange(t0, t1, 0.05)) {
        rec.feedValues('blackbox/live', t, { bb_ig1: key ? 12.4 : 0.0, bb_ecub: ecub }, { source: 'blackbox', columns: cols, evaluate: false, alive: key });
      }
    };
    const both = (t0: number, t1: number, link = true) => {
      for (let t = t0; t < t1; t++) {
        run(rec, t, t + 1, 800, link);
        box(t, t + 1, true, link ? 12.3 : 0.2);
      }
    };
    both(0, 30);
    both(30, 100, false);                                      // ECU dead for 70 s, key on all the while
    expect(rec.parked).toBe(false);
    expect(rec.drives).toHaveLength(1);
    expect(rec.events[0]!.reason).toBe('link');
    for (let t = 100; t < 130; t++) {                          // key off: the box says so, the ECU stays silent
      run(rec, t, t + 1, 800, false);
      box(t, t + 1, false, 0.0);
    }
    expect(rec.parked).toBe(true);
    box(130, 200, false, 0.0);                                 // parked: nothing is recorded
    run(rec, 130, 200, 800, false, 1.0);
    expect(rec.parked).toBe(true);
    box(200, 210, true, 0.2);                                  // key on again, the ECU does not answer
    expect(rec.parked).toBe(false);
    expect(rec.drives).toHaveLength(2);
    run(rec, 200, 210, 800, false, 0.5);
    both(210, 220);                                            // now it does
    await rec.stop();
    const [first, second] = rec.drives as [typeof rec.ref, typeof rec.ref];
    const a = readDrive(hdb.conn, first.drive);
    const b = readDrive(hdb.conn, second.drive);
    expect(a.meta.end_reason).toBe('key_off');
    expect('started_by' in a.meta).toBe(false);
    const ig1 = a.series('bb_ig1');
    expect(ig1.t[0]).toBe(0);
    near(ig1.t[ig1.t.length - 1]!, 120.0, 0.06);
    const dead = Array.from(ig1.t.keys()).filter((i) => ig1.t[i]! >= 30 && ig1.t[i]! < 100);
    expect(dead.length).toBe(1400);
    expect(dead.every((i) => ig1.v[i]! > 12)).toBe(true);
    const ecub = a.series('bb_ecub');
    expect(dead.every((i) => ecub.v[i]! < 1)).toBe(true);
    const rpm = a.series('rpm');
    expect(Array.from(rpm.t.keys()).filter((i) => rpm.t[i]! >= 30).every((i) => Number.isNaN(rpm.v[i]!))).toBe(true);
    const aLink = a.series('link').v;
    expect(aLink[aLink.length - 1]).toBe(0);
    expect(String(b.meta.started_by).startsWith('key on seen')).toBe(true);
    expect(b.meta.previous_drive).toBe(first.drive);
    near(b.t0, 200.0, 1e-9);
    near(b.series('bb_ig1').t[0]!, 200.0, 1e-9);
    const brpm = b.series('rpm');
    const firstAnswer = Array.from(brpm.t.keys()).find((i) => !Number.isNaN(brpm.v[i]!))!;
    near(brpm.t[firstAnswer]!, 210.0, 1e-9);
    expect(b.meta.end_reason).toBe('stopped');
    const bl = b.series('link').v;
    expect(bl[0]).toBe(0);
    expect(bl[bl.length - 1]).toBe(1);
    expect(hdb.conn.get('SELECT count(*) FROM drives')![0]).toBe(2);
  });

  it('a source that cannot see the key does not hold the drive open', async () => {
    const hdb = await db();
    const rec = recorder(hdb, { driveGapS: 5.0, stall: { linkDownS: 60.0 } });
    for (let t = 0; t < 30; t++) {
      run(rec, t, t + 1);
      rec.feedValues('blackbox/live', t + 0.5, { bb_ig1: 0.0 }, { source: 'blackbox', alive: false });
    }
    expect(rec.parked).toBe(false);                             // 30 s of "key off" from the box, the ECU answering
    for (let t = 30; t < 40; t++) {
      run(rec, t, t + 1, 800, false);
      rec.feedValues('blackbox/live', t + 0.5, { bb_ig1: 0.0 }, { source: 'blackbox', alive: false });
    }
    expect(rec.parked).toBe(true);
    await rec.stop();
    const log = readDrive(hdb.conn, rec.drives[0]!.drive);
    near(log.t1, 35.0, 0.6 + 35e-6);                           // pytest.approx(35.0, abs=0.6) with its rel=1e-6
    expect(log.meta.end_reason).toBe('key_off');
  });

  it('a capture near the start of a drive is cut at the drive', async () => {
    const hdb = await db();
    const rec = recorder(hdb, { driveGapS: 5.0, stall: { preS: 30, postS: 1, linkDownS: 60.0 } });
    run(rec, 0, 10);
    run(rec, 10, 16, 800, false);
    expect(rec.parked).toBe(true);
    run(rec, 40, 44);
    run(rec, 44, 46, 0);
    await rec.stop();
    const cap = readDrive(hdb.conn, rec.events[0]!.ref.capture);
    near(cap.t0, 40.0, 1e-9);
    expect(cap.meta.window).toBe('30 s before, 1 s after');
    const row = hdb.conn.get('SELECT t_start, t_trigger FROM captures')!;
    near(row[0] as number, 40, 1e-9);
    near(row[1] as number, 44, 1e-9);
  });

  it('the drive does not end while a capture is open', async () => {
    const hdb = await db();
    const rec = recorder(hdb, { driveGapS: 2.0, stall: { preS: 2, postS: 6, linkDownS: 1.0 } });
    run(rec, 0, 4);
    run(rec, 4, 9, 800, false);                                 // 2 s of silence pass while the window is open
    expect(rec.capturing).toBe(true);
    expect(rec.parked).toBe(false);
    run(rec, 9, 12, 800, false);
    expect(rec.capturing).toBe(false);
    expect(rec.parked).toBe(true);
    expect(rec.events[0]!.reason).toBe('link');
    await rec.stop();
    expect(rec.drives).toHaveLength(1);
    expect(readDrive(hdb.conn, rec.ref.drive).meta.end_reason).toBe('link_lost');
    const never = recorder(hdb, { driveGapS: null });           // null: one drive for the whole session
    run(never, 0, 2);
    run(never, 2, 500, 800, false, 5);
    expect(never.parked).toBe(false);
    await never.stop();
  });

  it('two recorders share one database', async () => {
    const hdb = await db();
    const a = recorder(hdb);
    const b = recorder(hdb);                                    // same minute: the names must differ
    expect(a.ref.drive).not.toBe(b.ref.drive);
    for (let i = 0; i < 600; i++) {
      a.feed(sample(i * 0.1));
      b.feed(sample(i * 0.1, 3000));
      if (i % 100 === 0) a.flush();
    }
    await a.stop();
    expect(readDrive(hdb.conn, b.ref.drive).status).toBe('recording');   // a's stop did not touch b's drive
    await b.stop();
    const one = readDrive(hdb.conn, a.ref.drive);
    const two = readDrive(hdb.conn, b.ref.drive);
    expect(one.rows).toBe(600);
    expect(two.rows).toBe(600);
    expect(Math.max(...one.series('rpm').v)).toBe(800);
    expect(Math.min(...two.series('rpm').v)).toBe(3000);
    expect(a.writeError).toBeNull();
    expect(b.writeError).toBeNull();
  });

  it('a write error does not stop the feeding', async () => {
    const hdb = await db();
    const rec = recorder(hdb);
    run(rec, 0, 1);
    rec.flush();
    const real = hdb.conn.tx.bind(hdb.conn);
    hdb.conn.tx = () => { throw new Error('database or disk is full'); };
    run(rec, 1, 2);
    rec.flush();
    run(rec, 2, 3);                                            // still accepts samples
    expect(rec.writeError?.message).toContain('full');
    hdb.conn.tx = real;
    await rec.stop().catch(() => undefined);
    const log = readDrive(hdb.conn, rec.ref.drive);
    expect(log.status).toBe('complete');
    expect(log.rows).toBeGreaterThanOrEqual(10);
    expect(log.rows).toBeLessThan(30);                         // what was fed while it failed is lost
  });

  it('link errors are logged while a drive is open', async () => {
    const hdb = await db();
    const rec = recorder(hdb, { driveGapS: 2.0 });
    run(rec, 1, 3);
    rec.feedLinkError(2.0, 0x12, 4, 'BadFrame', 2, 'no reply frame in 20 05');
    run(rec, 3, 6, 800, false);                                 // key off: parked after 2 s
    expect(rec.parked).toBe(true);
    rec.feedLinkError(5.9, 0x10, 4, 'NoReply', 1, 'echo only');   // parked: not logged
    await rec.stop();
    expect(hdb.conn.all("SELECT t, text FROM events WHERE kind='link_error'")).toEqual([[2.0, 'BadFrame 12+4 #2']]);
  });
});

describe('the 2026-11-26 dropout rules, end to end', () => {
  it('a dropout with the engine running is filed as a dropout', async () => {
    const hdb = await db();
    const rec = recorder(hdb, { stall: { preS: 5, postS: 2 } });
    run(rec, 0, 20, 700);
    run(rec, 20, 23.5, 700, false);                             // 3.5 s silence: fires at 23.0
    run(rec, 23.5, 30, 690);                                    // the engine never stopped
    await rec.stop();
    const cap = rec.events[0]!;
    expect(cap.reason).toBe('dropout');
    expect(cap.label.startsWith('LINK DROPOUT')).toBe(true);
    const c = hdb.conn;
    expect(c.all('SELECT kind, reason FROM captures')).toEqual([['dropout', 'dropout']]);
    expect(c.get('SELECT stall_count, capture_count FROM drives')).toEqual([0, 1]);
    expect(c.get('SELECT count(*) FROM stalls')).toEqual([0]);
    expect(c.all("SELECT text FROM events WHERE kind='trigger' ORDER BY t").map((r) => r[0])).toEqual(['STALL (link lost)', 'LINK DROPOUT (engine running)']);
  });

  it('a long dropout is re-filed after its capture was saved', async () => {
    // 2026-11-25 22:18: 18 s without a reply, engine running all along
    const hdb = await db();
    const rec = recorder(hdb, { stall: { preS: 5, postS: 2 } });
    run(rec, 0, 20, 700);
    run(rec, 20, 38, 700, false);                               // fires at 23, saved at 25
    expect(hdb.conn.all('SELECT kind FROM captures')).toEqual([['stall']]);
    run(rec, 38, 45, 700);
    await rec.stop();
    const row = hdb.conn.get('SELECT kind, label, meta FROM captures')!;
    expect(row[0]).toBe('dropout');
    expect(String(row[1]).startsWith('LINK DROPOUT')).toBe(true);
    expect(String(row[2])).toContain('"cancelled": true');
    expect(hdb.conn.get('SELECT stall_count, capture_count FROM drives')).toEqual([0, 1]);
  });

  it('a power loss stays a stall', async () => {
    const hdb = await db();
    const rec = recorder(hdb, { driveGapS: null, stall: { preS: 5, postS: 2 } });
    run(rec, 0, 20, 800);
    run(rec, 20, 40, 800, false);                               // ECU and engine dead
    run(rec, 40, 42, 0);                                        // key on: the ECU answers at rpm 0
    run(rec, 42, 50, 1300);                                     // restarted
    await rec.stop();
    expect(hdb.conn.all('SELECT kind, reason FROM captures')).toEqual([['stall', 'link']]);
    expect(hdb.conn.get('SELECT stall_count FROM drives')).toEqual([1]);
  });
});

// ---- retention and the library ----

const NOW = 1_800_000_000;

/** A finished drive that started ``days`` ago. */
function aged(hdb: HondalogDb, days: number, rows = 50, capture = false, meta: Record<string, unknown> = {}) {
  const started = NOW - days * 86400;
  return newDrive(hdb, { started_unix: started, epoch_unix: started, ...meta }, (w) => {
    w.addStream('s', [column('a'), column('rpm')], { rawLen: 8 });
    for (let i = 0; i < rows; i++) w.append('s', i, [i, 800 + i], new Uint8Array(8));
    if (capture) w.capture('stall', 5.0, 0.0, 10.0, { reason: 'rpm', message: 'rpm dropped to 0', wallUnix: started + 5 });
  });
}

describe('retention and deleting', () => {
  it('the retention policy', async () => {
    const hdb = await db();
    const old = aged(hdb, 40);
    aged(hdb, 39, 50, false, { notes: 'first verified run' });
    const stall = aged(hdb, 38, 50, true);
    aged(hdb, 37, 50, false, { tags: ['dyno'] });
    aged(hdb, 36, 50, false, { keep: true });
    const plain = aged(hdb, 20);
    const mid = aged(hdb, 10, 20_000);
    const fresh = aged(hdb, 1);
    const names = (refs: { drive: string }[]) => refs.map((r) => r.drive);
    expect(retentionPlan(hdb, {}, NOW)).toEqual([]);
    expect(names(retentionPlan(hdb, { maxAgeDays: 30 }, NOW))).toEqual([old.drive]);    // notes, tags, keep and captures protect
    expect(names(retentionPlan(hdb, { maxAgeDays: 30, keepCaptures: false }, NOW))).toEqual([old.drive, stall.drive]);
    expect(names(retentionPlan(hdb, { maxDrives: 5 }, NOW))).toEqual([old.drive, plain.drive, mid.drive]);
    expect(names(retentionPlan(hdb, { maxTotalMb: 0.5 }, NOW))).toEqual([old.drive, plain.drive, mid.drive]);
    expect(names(retentionPlan(hdb, { maxTotalMb: 50 }, NOW))).toEqual([]);
    expect(names(retentionPlan(hdb, { maxAgeDays: 0 }, NOW))).toEqual([old.drive, plain.drive, mid.drive]);   // the newest always stays
    const all = retentionPlan(hdb, { maxAgeDays: 0, keepNewest: 0 }, NOW);
    expect(all[all.length - 1]!.drive).toBe(fresh.drive);

    expect(names(applyRetention(hdb, { maxDrives: 5 }, NOW))).toEqual([old.drive, plain.drive, mid.drive]);
    const c = hdb.conn;
    expect(c.get('SELECT count(*) FROM drives')![0]).toBe(5);
    expect(c.get('SELECT count(DISTINCT drive_id), count(*) FROM samples')).toEqual([5, 5 * 50]);
    expect(c.get('PRAGMA freelist_count')![0]).toBe(0);         // the pages went back
    expect(c.get('PRAGMA integrity_check')![0]).toBe('ok');
    const views = new Set(c.all("SELECT name FROM sqlite_master WHERE type = 'view'").map((r) => String(r[0])));
    expect(views.has(old.drive) || views.has(mid.drive)).toBe(false);
    expect(views.has(fresh.drive)).toBe(true);
    expect(readDrive(c, 'last').rows).toBe(50);
    expect(applyRetention(hdb, { maxDrives: 5 }, NOW)).toEqual([]);
    expect(names(applyRetention(hdb, { maxDrives: 1, keepCaptures: false, fullVacuum: true }, NOW))).toEqual([stall.drive]);
    expect(c.get('SELECT count(*) FROM captures')![0]).toBe(0);
  });

  it('deletes one drive or one capture', async () => {
    const hdb = await db();
    const big = aged(hdb, 9, 20_000);
    const stall = aged(hdb, 8, 50, true);
    const flagged = aged(hdb, 7, 50, false, { keep: true });
    const capture = listEntries(hdb.conn).find((e) => e.kind === 'stall')!;
    expect(lookup(hdb.conn, 'last').name).toBe(flagged.drive);
    expect(capture.ref.drive).toBe(stall.drive);
    expect(describeEntry(capture)).toContain('The drive and its data stay');
    expect(describeEntry(lookup(hdb.conn, big.drive))).toContain('20000 rows');
    // a capture is only an entry: the drive keeps every row, its counts follow
    expect(deleteEntry(hdb, capture.name).name).toBe(capture.name);
    expect(hdb.conn.get('SELECT count(*) FROM captures')![0]).toBe(0);
    expect(hdb.conn.get('SELECT stall_count, capture_count FROM drives WHERE name=?', [stall.drive])).toEqual([0, 0]);
    expect(readDrive(hdb.conn, stall.drive).rows).toBe(50);
    expect(() => deleteEntry(hdb, capture.name)).toThrow(/no drive or capture/);
    // a drive goes with everything in it
    expect(deleteEntry(hdb, big.drive).name).toBe(big.drive);
    expect(hdb.conn.get('PRAGMA freelist_count')![0]).toBe(0);
    expect(hdb.conn.get('PRAGMA integrity_check')![0]).toBe('ok');
    // keep: only when the caller says the user confirmed that as well
    expect(() => deleteEntry(hdb, flagged.drive)).toThrow(KeepProtected);
    expect(deleteEntry(hdb, flagged.drive, { allowKeep: true }).keep).toBe(true);
    // the drive being recorded is refused, with or without keep
    const rec = recorder(hdb);
    run(rec, 0, 1);
    rec.flush();
    expect(() => deleteEntry(hdb, rec.ref.drive, { allowKeep: true })).toThrow(/being recorded/);
    await rec.stop();
    expect(deleteEntry(hdb, rec.ref.drive).name).toBe(rec.ref.drive);
    expect(listEntries(hdb.conn).map((e) => e.name)).toEqual([stall.drive]);
  });

  it('retention leaves a running drive alone, and the recorder applies it on stop', async () => {
    const hdb = await db();
    const old = aged(hdb, 5000);
    const rec = recorder(hdb);
    run(rec, 0, 1);
    rec.flush();
    expect(applyRetention(hdb, { maxAgeDays: 0, keepNewest: 0 }).map((r) => r.drive)).toEqual([old.drive]);
    run(rec, 1, 2);
    await rec.stop();
    expect(readDrive(hdb.conn, rec.ref.drive).rows).toBe(20);
    const old2 = aged(hdb, 5000);
    const rec2 = recorder(hdb, { retention: { maxAgeDays: 30 } });
    run(rec2, 0, 1);
    await rec2.stop();
    expect(listEntries(hdb.conn).some((e) => e.name === old2.drive)).toBe(false);
    expect(listEntries(hdb.conn).some((e) => e.name === rec2.ref.drive)).toBe(true);
  });
});

describe('the library', () => {
  it('lists, searches and annotates', async () => {
    const hdb = await db();
    const rec = recorder(hdb);
    run(rec, 0, 40);
    rec.mark('idle hunting');
    run(rec, 40, 42);
    run(rec, 42, 55, 0);
    await rec.stop();
    const entries = new Map(listEntries(hdb.conn).map((e) => [e.name, e]));
    expect(entries.size).toBe(2);
    const main = entries.get(rec.ref.drive)!;
    const stall = [...entries.values()].find((e) => e.kind === 'stall')!;
    expect(main.kind).toBe('drive');
    near(main.duration, 54.9, 1e-9);
    expect(main.rows).toBe(550);
    expect(main.status).toBe('complete');
    expect([main.stalls, main.captures]).toEqual([1, 1]);
    expect(main.mapVersion).toBe(amap.version);
    expect(main.markers).toEqual(['idle hunting', 'STALL (rpm 0)']);
    expect(stall.trigger).toBe('rpm dropped to 0');
    near(stall.duration, 40, 0.3);
    expect(stall.ref.capture).toBe(stall.name);
    expect(stall.ref.drive).toBe(main.name);
    expect(stall.markers).toEqual(main.markers);
    expect(main.thumbHi.length).toBeGreaterThan(0);
    expect(main.thumbHi.length).toBeLessThanOrEqual(64);
    expect(Math.max(...main.thumbHi)).toBe(800);
    expect(Math.min(...main.thumbLo)).toBe(0);
    const all = listEntries(hdb.conn);
    expect(new Set(search(all, 'hunting').map((e) => e.name))).toEqual(new Set([stall.name, main.name]));
    expect(search(all, 'hunting nonsense')).toEqual([]);
    expect(search(all, '', ['stall'])).toHaveLength(1);
    annotate(hdb, main.name, { notes: 'Fixed by cleaning the IACV', tags: ['idle', 'fixed'], keep: true });
    const found = search(listEntries(hdb.conn), 'iacv');
    expect(new Set(found.map((e) => e.name))).toEqual(new Set([stall.name, main.name]));
    expect(found.every((e) => e.keep && e.tags.join(',') === 'idle,fixed')).toBe(true);
    expect(search(listEntries(hdb.conn), '', [], 'fixed')).toHaveLength(2);
  });

  it('a drive whose recorder died is marked crashed on the next open', async () => {
    const files = new MemoryFileStore();
    const hdb = await HondalogDb.openFile(files);
    const rec = recorder(hdb, { checkpointS: 10 });
    run(rec, 0, 2);
    rec.flush();
    await hdb.persist();                                        // the tab is closed here
    const again = await HondalogDb.open(await files.read('hondalog.db'), { now: () => Date.now() / 1000 + 60 });
    const entry = listEntries(again.conn).find((e) => e.name === rec.ref.drive)!;
    expect(entry.status).toBe('crashed');
    expect(entry.rows).toBe(20);
    expect(readDrive(again.conn, rec.ref.drive).meta.end_reason).toBe('crash');
  });
});
