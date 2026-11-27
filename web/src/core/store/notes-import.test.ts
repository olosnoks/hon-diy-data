// @vitest-environment node
/**
 * Event notes in the events table (desktop eventnotes.py rows), the keep
 * flag, CSV / MSL / MLG import, and start-up recovery of a drive a reload
 * interrupted.
 */
import { describe, expect, it } from 'vitest';
import { HondalogDb, SESSION_PID } from './db';
import { SqliteDriveStore } from './drivestore';
import { EVENT_NOTE_KIND, dumpsNote, eventKey, loadEventNotes, saveEventNote } from './eventnotes';
import { writeCsv, writeMsl } from './export/textlog';
import { writeMlg } from './export/mlg';
import { MemoryFileStore } from './filestore';
import { KeepProtected } from './library';
import { column } from './model';
import { type Table, readDrive } from './reader';
import { Store, newDrive } from './writer';

const META = { ecu: 'P2P', started_unix: 1794812400.0, epoch_unix: 1794812400.0 };

function oneDrive(hdb: HondalogDb): string {
  return newDrive(hdb, META, (d) => {
    d.addStream('ecu/00+2', [column('rpm', { units: 'rpm', decimals: 0 })], { source: 'ecu' });
    for (let i = 0; i < 50; i++) d.append('ecu/00+2', i * 0.1, [800 + i]);
    d.event(1.0, 'marker', 'MARK 1');
  }).drive;
}

describe('event notes (desktop eventnotes.py format)', () => {
  it('keys are kind@t to 0.1 s, as Python formats them', () => {
    expect(eventKey('near_stall', 3210.44)).toBe('near_stall@3210.4');
    expect(eventKey('stall', 12)).toBe('stall@12.0');
    expect(eventKey('vtec', 0.25)).toBe('vtec@0.2');          // Python rounds the exact binary value: 0.25 -> 0.2
    expect(eventKey('vtec', 0.35)).toBe('vtec@0.3');          // 0.35 is 0.34999...
  });

  it('data is Python json.dumps of the dict', () => {
    expect(dumpsNote({ note: 'clutch bit early', kind: 'near_stall', t: 3210.4, updated_unix: 1794812400 }))
      .toBe('{"note": "clutch bit early", "kind": "near_stall", "t": 3210.4, "updated_unix": 1794812400.0}');
    expect(dumpsNote({ note: 'Kupplung zu früh' })).toBe('{"note": "Kupplung zu fr\\u00fch"}');
  });

  it('save, load, change, clear: one row per event, kind event_note', async () => {
    const hdb = await HondalogDb.open(null);
    const drive = oneDrive(hdb);
    const now = () => 1794812500.5;
    expect(loadEventNotes(hdb.conn, drive)).toEqual({});
    expect(saveEventNote(hdb, drive, 'near_stall@3.2', { note: 'clutch bit early', kind: 'near_stall', t: 3.2 }, now))
      .toEqual({ note: 'clutch bit early', verdict: '' });
    saveEventNote(hdb, drive, 'near_stall@3.2', { verdict: 'throttle late' }, now);
    const rows = hdb.conn.all('SELECT t, kind, text, data FROM events WHERE kind=?', [EVENT_NOTE_KIND]);   // key order as a Python dict keeps it
    expect(rows).toEqual([[3.2, 'event_note', 'near_stall@3.2',
      '{"note": "clutch bit early", "kind": "near_stall", "t": 3.2, "updated_unix": 1794812500.5, "verdict": "throttle late"}']]);
    expect(loadEventNotes(hdb.conn, drive)).toEqual({ 'near_stall@3.2': { note: 'clutch bit early', verdict: 'throttle late', kind: 'near_stall', t: 3.2 } });
    // notes never show up as markers
    expect(readDrive(hdb.conn, drive).markers).toEqual([[1.0, 'MARK 1']]);
    saveEventNote(hdb, drive, 'near_stall@3.2', { note: '' }, now);
    expect(loadEventNotes(hdb.conn, drive)['near_stall@3.2']!.note).toBe('');
    saveEventNote(hdb, drive, 'near_stall@3.2', { verdict: '' }, now);     // neither left: the row goes
    expect(hdb.conn.all('SELECT * FROM events WHERE kind=?', [EVENT_NOTE_KIND])).toEqual([]);
    // clearing what is not there writes nothing
    saveEventNote(hdb, drive, 'stall@9.0', { note: '' }, now);
    expect(hdb.conn.all('SELECT * FROM events WHERE kind=?', [EVENT_NOTE_KIND])).toEqual([]);
    expect(() => saveEventNote(hdb, 'drive_nope', 'k', { note: 'x' })).toThrow(/no drive/);
    expect(loadEventNotes(hdb.conn, 'drive_nope')).toEqual({});
  });

  it('reads a row the desktop app wrote', async () => {
    const hdb = await HondalogDb.open(null);
    const drive = oneDrive(hdb);
    const id = Number(hdb.conn.get('SELECT id FROM drives WHERE name=?', [drive])![0]);
    hdb.conn.run('INSERT INTO events(drive_id, t, kind, text, data) VALUES (?, ?, ?, ?, ?)',
      [id, 2.5, 'event_note', 'dropout@2.5', '{"note": "cable bumped", "verdict": "", "kind": "dropout", "t": 2.5, "updated_unix": 1794812600.123}']);
    const store = new SqliteDriveStore(hdb);
    expect(await store.loadEventNotes(drive)).toEqual({ 'dropout@2.5': { note: 'cable bumped', verdict: '', kind: 'dropout', t: 2.5 } });
    await store.saveEventNote(drive, 'dropout@2.5', { verdict: 'cable bumped' });
    expect((await store.loadEventNotes(drive))['dropout@2.5']).toEqual({ note: 'cable bumped', verdict: 'cable bumped', kind: 'dropout', t: 2.5 });
  });
});

describe('keep flag', () => {
  it('DriveInfo.keep, setKeep, and delete refuses a keep drive without allowKeep', async () => {
    const files = new MemoryFileStore();
    const store = await SqliteDriveStore.open(files);
    const drive = oneDrive(store.hdb);
    expect((await store.listDrives())[0]!.keep).toBe(false);
    await store.setKeep(drive, true);
    expect((await store.listDrives())[0]!.keep).toBe(true);
    expect(Number(store.hdb.conn.get('SELECT keep FROM drives')![0])).toBe(1);
    await expect(store.deleteDrive(drive)).rejects.toThrow(KeepProtected);
    // persisted: a fresh open sees it
    const again = await SqliteDriveStore.open(files);
    expect((await again.listDrives())[0]!.keep).toBe(true);
    await store.setKeep(drive, false);
    expect((await store.listDrives())[0]!.keep).toBe(false);
    await store.deleteDrive(drive);
    expect(await store.listDrives()).toEqual([]);
    await expect(store.setKeep('drive_nope', true)).rejects.toThrow();
  });
});

describe('CSV / MSL / MLG import', () => {
  const table: Table = {
    t: Float64Array.from({ length: 40 }, (_, i) => i * 0.1),
    columns: { rpm: Float64Array.from({ length: 40 }, (_, i) => 800 + 10 * i), map: Float64Array.from({ length: 40 }, (_, i) => 30 + i / 2) },
    units: { rpm: 'rpm', map: 'kPa' },
    names: ['rpm', 'map'],
  };
  const markers: [number, string][] = [[1.0, 'clutch in']];

  it.each([
    ['old.csv', () => new TextEncoder().encode(writeCsv(table, markers))],
    ['old.msl', () => new TextEncoder().encode(writeMsl(table, markers))],
    ['old.mlg', () => writeMlg(table, markers)],
  ])('%s becomes an imported drive', async (name, bytes) => {
    const store = await SqliteDriveStore.open(new MemoryFileStore());
    const drive = await store.importFile(name, bytes());
    const list = await store.listDrives();
    expect(list.map((d) => d.name)).toEqual([drive]);
    const loaded = await store.loadDrive(drive);
    expect(loaded.series.rpm!.v.length).toBe(40);
    expect(loaded.series.rpm!.v[39]).toBeCloseTo(1190, 3);
    expect(loaded.series.map!.v[10]).toBeCloseTo(35, 3);
    expect(loaded.events.some((e) => e.text.includes('clutch in'))).toBe(true);
    const log = store.readLog(drive);
    expect(log.status).toBe('imported');
    expect(log.meta.imported_from).toBe(name);
  });

  it('refuses a file it cannot read', async () => {
    const store = await SqliteDriveStore.open(new MemoryFileStore());
    await expect(store.importFile('empty.csv', new TextEncoder().encode('# only a comment\n'))).rejects.toThrow();
    await expect(store.importFile('bad.mlg', new Uint8Array([1, 2, 3]))).rejects.toThrow();
    expect(await store.listDrives()).toEqual([]);
  });
});

describe('start-up recovery of an interrupted drive', () => {
  it('a drive another session left recording is crashed at once (fresh heartbeat)', async () => {
    const files = new MemoryFileStore();
    const hdb = await HondalogDb.openFile(files);
    const store = new Store(hdb, { background: false, checkpointS: 0 });
    const drive = store.beginDrive(META);
    drive.addStream('s', [column('rpm')]);
    for (let i = 0; i < 20; i++) drive.append('s', i * 0.1, [800]);
    store.flush();
    // the page that wrote it is gone (another session's pid), its heartbeat a second old
    hdb.conn.run('UPDATE drives SET writer_pid=?, heartbeat_at=?', [SESSION_PID + 1, Date.now() / 1000 - 1]);
    await hdb.persist();
    const reopened = await SqliteDriveStore.open(files);
    expect((await reopened.listDrives())[0]!.status).toBe('recording');      // the plain open: heartbeat not stale yet
    expect(await reopened.recoverInterrupted()).toEqual([drive.name]);
    const d = (await reopened.listDrives())[0]!;
    expect(d.status).toBe('crashed');
    expect(d.rows).toBe(20);
    // persisted
    expect((await (await SqliteDriveStore.open(files)).listDrives())[0]!.status).toBe('crashed');
    // this session's own live drive is left alone
    const own = new Store(reopened.hdb, { background: false, checkpointS: 0 });
    const live = own.beginDrive(META);
    live.addStream('s', [column('rpm')]);
    live.append('s', 0, [1]);
    own.flush();
    expect(await reopened.recoverInterrupted()).toEqual([]);
    expect((await reopened.listDrives()).find((x) => x.name === live.name)!.status).toBe('recording');
  });
});
