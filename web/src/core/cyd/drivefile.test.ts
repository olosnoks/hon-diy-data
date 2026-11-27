import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { hex } from './bytes';
import { driveId, epochUnix, parseDriveFile, tLastUs } from './drivefile';
import * as F from './format';
import { type ImportDrive, type ImportStore, importCard, importFile, resultText } from './importer';

const FIX = join(dirname(fileURLToPath(import.meta.url)), '__fixtures__');
const bin = (n: string) => new Uint8Array(readFileSync(join(FIX, n)));
const WANT = JSON.parse(readFileSync(join(FIX, 'drives.json'), 'utf-8'));

describe('drive files read as the Python reads them', () => {
  for (const { file, parse: want } of WANT) {
    it(file, () => {
      const df = parseDriveFile(bin(file), file);
      expect([df.kind, df.fileNo, df.bootNo, df.flags, df.backend, df.linkMode, df.nonce, df.t0Us, df.unixUsAtT0])
        .toEqual([want.kind, want.file_no, want.boot_no, want.flags, want.backend, want.link_mode, want.nonce, want.t0_us, want.unix_us_at_t0]);
      expect([df.mapHash, df.fw, df.device, hex(df.ecuId), df.driveNo, df.reason, df.triggerTUs, df.prealloc])
        .toEqual([want.map_hash, want.fw, want.device, want.ecu_id, want.drive_no, want.reason, want.trigger_t_us, want.prealloc]);
      expect(df.reads.length).toBe(want.reads);
      expect(df.reads.slice(0, 5).map((r) => [r.tUs, r.addr, hex(r.data), r.latencyUs])).toEqual(want.first_reads);
      const last = df.reads[df.reads.length - 1]!;
      expect([last.tUs, last.addr, hex(last.data)]).toEqual(want.last_read);
      expect(df.iso.length).toBe(want.iso);
      expect(df.gps.length).toBe(want.n_gps);
      df.gps.slice(0, 5).forEach((g, i) => {
        const w = want.gps[i];
        expect([g.tUs, g.utc, g.fix, g.sats]).toEqual([w[0], w[3], w[6], w[7]]);
        expect(g.lat).toBeCloseTo(w[1], 9);
        expect(g.lon).toBeCloseTo(w[2], 9);
        expect(g.speedKmh).toBeCloseTo(w[4], 9);
        expect(g.heading).toBeCloseTo(w[5], 9);
        if (w[8] === null) expect(g.hdop).toBeNull();
        else expect(g.hdop).toBeCloseTo(w[8], 9);
      });
      expect(df.events.map((e) => [e.tUs, e.code, e.src, e.aux, Number.isNaN(e.value) ? 'nan' : e.value])).toEqual(want.events);
      expect(df.captures.map((c) => [c.tTriggerUs, c.tStartUs, c.tEndUs, c.reason, c.complete, c.nFired])).toEqual(want.captures);
      expect(df.times).toEqual(want.times);
      expect(df.config.length).toBe(want.config_len);
      expect(Object.fromEntries(df.alarmNames)).toEqual(want.alarm_names);
      expect(df.end).toEqual(want.end);
      expect([df.blocks, df.badBlocks, df.missingBlocks, df.usedBytes, df.size])
        .toEqual([want.blocks, want.bad_blocks, want.missing_blocks, want.used_bytes, want.size]);
      expect(driveId(df)).toBe(want.id);
      expect(epochUnix(df)).toBeCloseTo(want.epoch_unix, 6);
      expect(tLastUs(df)).toBe(want.t_last_us);
    });
  }

  it('refuses what is not a drive file', () => {
    expect(() => parseDriveFile(new Uint8Array(10))).toThrow(/too short/);
    expect(() => parseDriveFile(new Uint8Array(200))).toThrow(/not a handheld drive file/);
    const d = bin('D_D000001.HHD');
    d[20]! ^= 1;
    expect(() => parseDriveFile(d)).toThrow(/checksum/);
  });

  it('a damaged block in the middle is skipped and counted', () => {
    const d = bin('D_D000001.HHD');
    d[3000]! ^= 0xff;
    const df = parseDriveFile(d);
    expect(df.badBlocks).toBe(1);
    expect(df.end).not.toBeNull();
    expect(df.reads.length).toBeLessThan(WANT[0].parse.reads);
  });
});

class FakeStore implements ImportStore {
  drives: Array<{ name: string; meta: Record<string, unknown>; started: number; streams: Map<string, number>;
    events: Array<[number, string, string]>; captures: number; status: string }> = [];
  async alreadyImported(id: string) {
    return this.drives.find((d) => d.meta.handheld_id === id)?.name ?? null;
  }
  async deleteDrive(name: string) {
    this.drives = this.drives.filter((d) => d.name !== name);
  }
  async beginDrive(meta: Record<string, unknown>, started: number): Promise<ImportDrive> {
    const rec = { name: `drive_${this.drives.length + 1}`, meta, started, streams: new Map<string, number>(),
      events: [] as Array<[number, string, string]>, captures: 0, status: 'recording' };
    this.drives.push(rec);
    return {
      name: rec.name,
      addStream: (n) => { rec.streams.set(n, 0); },
      append: (n) => { rec.streams.set(n, (rec.streams.get(n) ?? 0) + 1); },
      event: (t, kind, text) => { rec.events.push([t, kind, text]); },
      capture: () => { rec.captures += 1; },
      end: (status) => { rec.status = status; },
    };
  }
}

describe('import (test_hh_import)', () => {
  const card = () => new Map([['HANDHELD.MAP', bin('sim.HANDHELD.MAP')], ['DRIVES/D000001.HHD', bin('D_D000001.HHD')]]);

  it('makes a drive like a laptop recording, with the map from the card', async () => {
    const store = new FakeStore();
    const res = (await importCard(card(), store))[0]!;
    expect(res.status).toBe('imported');
    expect(res.reads).toBe(WANT[0].parse.reads);
    expect(resultText(res)).toMatch(/imported as drive_1 \(936 reads, 14 GPS fixes, 1 captures\)/);
    const d = store.drives[0]!;
    expect(d.meta.address_map_hash).toBe(WANT[0].parse.map_hash);
    expect(d.meta.address_map_file).toBe('HANDHELD.MAP');
    expect(d.meta.handheld_id).toBe(WANT[0].parse.id);
    // started: the GPS epoch of file second 0, moved back to the first read if that came earlier
    expect(d.started).toBeLessThanOrEqual(WANT[0].parse.epoch_unix);
    expect(WANT[0].parse.epoch_unix - d.started).toBeLessThan(0.1);
    expect(d.streams.get('gps')).toBe(14);
    expect(d.streams.get('ecu/link')).toBe(1);
    expect([...d.streams.keys()].some((k) => /^ecu\/[0-9A-F]{2}\+\d+$/.test(k))).toBe(true);
    expect(d.events.map((e) => e[1])).toEqual(['marker', 'trigger']);
    expect(d.events[0]![2]).toBe('MARK 1');
    expect(d.captures).toBe(1); // the stall whose window the file never closed
    expect(d.status).toBe('imported');
  });

  it('is idempotent; force imports again', async () => {
    const store = new FakeStore();
    await importCard(card(), store);
    const again = (await importCard(card(), store))[0]!;
    expect(again.status).toBe('skipped');
    expect(again.reason).toBe('already imported as drive_1');
    const forced = (await importCard(card(), store, { force: true }))[0]!;
    expect(forced.status).toBe('imported');
    expect(store.drives.length).toBe(1);
  });

  it('a power-cut file with stale space imports cleanly', async () => {
    const store = new FakeStore();
    const res = await importFile('D000001.HHD', bin('torn_D_D000001.HHD'), store, { cardMap: bin('sim.HANDHELD.MAP') });
    expect(res.status).toBe('imported');
    expect(res.notes).toContain('no end marker (power was cut)');
    expect(res.notes.join(' ')).not.toMatch(/damaged/);
  });

  it('a drive recorded with another map needs that map', async () => {
    const store = new FakeStore();
    const res = await importFile('D000001.HHD', bin('D_D000001.HHD'), store);
    expect(res.status).toBe('failed');
    expect(res.reason).toMatch(/address map #/);
  });

  it('a file with nothing in it is skipped', async () => {
    const d = bin('D_D000001.HHD');
    const head = d.slice(0, F.FILE_HDR.size);
    const res = await importFile('E.HHD', head, new FakeStore(), {});
    expect(res.status).toBe('skipped');
  });
});
