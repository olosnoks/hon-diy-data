import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { concat, crc32, rfindBytes, utf8 } from './bytes';
import { FakeCyd } from './fake_cyd';
import * as F from './format';
import { resolveMap } from './hhmap';
import { ClockSync, CydClient, CydError, MemorySink, UsedLength, entryName, pushMapTo } from './link';
import { DEFAULT_CFG, readMap } from './mapfile';
import * as P from './serial_proto';
import { MemoryMirror, mirrorFiles, mirrorName, sync } from './sync';

const FIX = join(dirname(fileURLToPath(import.meta.url)), '__fixtures__');
const bin = (n: string) => new Uint8Array(readFileSync(join(FIX, n)));
const DRIVE = bin('D_D000001.HHD');
const MAP = bin('sim.HANDHELD.MAP');
const TOML = readFileSync(join(FIX, 'addressmap.toml'), 'utf-8');

async function client(opts: ConstructorParameters<typeof FakeCyd>[0] = {}) {
  const sd = new Map<string, Uint8Array>([['HANDHELD.MAP', MAP], ['DRIVES/D000001.HHD', DRIVE]]);
  const fake = new FakeCyd({ sd, ...opts });
  const cyd = new CydClient(fake, { clock: fake.clock, timeout: 0.3, retries: 1 });
  await cyd.open();
  return { fake, cyd, sd };
}

describe('request and answer (test_hh_serial)', () => {
  it('opens on the hello and finds the baud', async () => {
    const fake = new FakeCyd({ deviceBaud: 921600, baud: 460800 });
    const cyd = new CydClient(fake, { clock: fake.clock });
    const hello = await cyd.open(8);
    expect(hello.fw).toBe('0.1.0');
    expect(fake.baud).toBe(921600);
    expect(fake.frames.length).toBe(0); // nothing written before the hello
  });

  it('no hello: an error a person can act on', async () => {
    const fake = new FakeCyd({ deviceBaud: 9600, baud: 921600, fixedBaud: true });
    const cyd = new CydClient(fake, { clock: fake.clock });
    await expect(cyd.open(2)).rejects.toThrow(/serial = 1/);
  });

  it('mark, drive, set-time', async () => {
    const { fake, cyd } = await client({ gpsTime: false });
    await cyd.mark();
    expect(fake.markers).toBe(1);
    expect(await cyd.setTime(1_700_000_000)).toBe(true);
    expect(fake.timeSet).toBeGreaterThan(1.6e15);
    fake.gpsTime = true;
    expect(await cyd.setTime()).toBe(false);
    fake.busy = 'DRIVES/D000001.HHD';
    await cyd.drive(false);
    expect(fake.busy).toBeNull();
  });

  it('lists and fetches files, chunk by chunk', async () => {
    const { cyd } = await client();
    const drives = await cyd.listFiles(P.F_DRIVE);
    expect(drives).toEqual([{ kind: P.F_DRIVE, fileNo: 1, size: DRIVE.length, open: false }]);
    expect(entryName(drives[0]!)).toBe('DRIVES/D000001.HHD');
    const sink = new MemorySink();
    const seen: number[] = [];
    expect(await cyd.fetch(drives[0]!, sink, (d) => seen.push(d))).toBe(DRIVE.length);
    expect(sink.data).toEqual(DRIVE);
    expect(sink.finished).toBe(true);
    expect(seen.length).toBe(Math.ceil(DRIVE.length / P.CHUNK));
    expect(await cyd.listFiles(P.F_CAPTURE)).toEqual([]);
    const maps = await cyd.listFiles(P.F_MAP);
    expect(maps.map(entryName)).toEqual(['HANDHELD.MAP']);
  });

  it('pages of 24 files', async () => {
    const sd = new Map<string, Uint8Array>();
    for (let i = 1; i <= 30; i++) sd.set(`DRIVES/D${String(i).padStart(6, '0')}.HHD`, Uint8Array.of(i));
    const fake = new FakeCyd({ sd });
    const cyd = new CydClient(fake, { clock: fake.clock });
    await cyd.open();
    const all = await cyd.listFiles(P.F_DRIVE);
    expect(all.map((e) => e.fileNo)).toEqual(Array.from({ length: 30 }, (_, i) => i + 1));
  });

  it('a file being written is busy', async () => {
    const { fake, cyd } = await client();
    fake.busy = 'DRIVES/D000001.HHD';
    const e = (await cyd.listFiles(P.F_DRIVE))[0]!;
    expect(e.open).toBe(true);
    await expect(cyd.readChunk(P.F_DRIVE, 1, 0)).rejects.toThrow(/still being written/);
    await expect(cyd.readChunk(P.F_DRIVE, 9, 0)).rejects.toThrow(/no such file/);
  });

  it('push-map: the handheld checks it and keeps the old one as .MBK', async () => {
    const { cyd, sd } = await client();
    const map = resolveMap(TOML, 'P2T');
    const size = await pushMapTo(cyd, map);
    const mf = readMap(sd.get('HANDHELD.MAP')!);
    expect(mf.mapHash).toBe(map.versionHash);
    expect(sd.get('HANDHELD.MAP')!.length).toBe(size);
    expect(sd.get('HANDHELD.MBK')).toEqual(MAP);
    const skipped = await cyd.push(P.F_CFG, utf8(DEFAULT_CFG + 'mystery = 1\n'));
    expect(skipped).toBe(1);
    await expect(cyd.push(P.F_MAP, utf8('not a map'))).rejects.toThrow(/could not load it/);
  });

  it('a tx-only handheld cannot take commands', async () => {
    const fake = new FakeCyd({ txOnly: true });
    const cyd = new CydClient(fake, { clock: fake.clock });
    await cyd.open();
    await expect(cyd.mark()).rejects.toThrow(/tx-only/);
    expect(fake.rxIgnored).toBe(0);
  });
});

describe('MAP upload (test_hh_mapup)', () => {
  const big = (n: number, seed: number) => Uint8Array.from({ length: n }, (_, i) => (i * 13 + seed) & 0xff);

  it('a file goes in whole and is renamed into place', async () => {
    const { fake, cyd, sd } = await client();
    const data = big(5000, 1);
    await cyd.putMapFile('MAP.DAT', data, undefined, true);
    expect(sd.get('MAP/MAP.DAT')).toEqual(data);
    expect(fake.mapCommits).toEqual(['MAP/MAP.DAT']);
  });

  it('a damaged chunk is sent again', async () => {
    const { fake, cyd, sd } = await client();
    fake.damageMapup = 2;
    const data = big(3000, 2);
    await cyd.putMapFile('MAP/MAP.IDX', data);
    expect(sd.get('MAP/MAP.IDX')).toEqual(data);
  });

  it('an interrupted upload resumes from what the handheld holds', async () => {
    const { fake, cyd, sd } = await client();
    const data = big(20000, 3);
    fake.mapupAnswersLeft = 10;
    await expect(cyd.putMapFile('MAP.DAT', data)).rejects.toThrow(CydError);
    const info = (await cyd.mapUploadInfo())!;
    expect(info.name).toBe('MAP/MAP.DAT');
    expect(info.got).toBe(10 * P.MAPUP_CHUNK);
    fake.mapupAnswersLeft = null;
    const seen: number[] = [];
    await cyd.putMapFile('MAP.DAT', data, (d) => seen.push(d));
    expect(seen[0]).toBe(10 * P.MAPUP_CHUNK);
    expect(sd.get('MAP/MAP.DAT')).toEqual(data);
  });

  it('other names are refused; an empty file too', async () => {
    const { cyd } = await client();
    for (const name of ['MAP/UPLOAD.TMP', 'map.dat', '../X', 'MAP/A/B']) {
      await expect(cyd.putMapFile(name, Uint8Array.of(1))).rejects.toThrow(/8.3 name/);
    }
    await expect(cyd.putMapFile('MAP.DAT', new Uint8Array(0))).rejects.toThrow(/1 to/);
  });

  it('needs a two-way handheld with a card and new firmware', async () => {
    const nosd = new FakeCyd({ sd: null });
    const c1 = new CydClient(nosd, { clock: nosd.clock });
    await c1.open();
    await expect(c1.putMapFile('MAP.DAT', Uint8Array.of(1))).rejects.toThrow(/no SD card/);
    const old = new FakeCyd({ caps: P.CAP_RX | P.CAP_FILES });
    const c2 = new CydClient(old, { clock: old.clock });
    await c2.open();
    await expect(c2.putMapFile('MAP.DAT', Uint8Array.of(1))).rejects.toThrow(/older than the MAP upload/);
  });
});

describe('sync (test_hh_serial)', () => {
  it('resumes after an interrupted transfer, then has everything', async () => {
    const { fake, cyd } = await client();
    const store = new MemoryMirror();
    const mapChunks = Math.ceil(MAP.length / P.CHUNK);
    fake.fileAnswersLeft = mapChunks + 4; // the cable is pulled 4 chunks into the drive
    await expect(sync(cyd, store)).rejects.toThrow(CydError);
    const part = store.files.get('DRIVES/D000001.HHD.part')!;
    expect(part.length).toBe(4 * P.CHUNK);
    fake.fileAnswersLeft = null;
    const report = await sync(cyd, store);
    expect(report.fetched).toEqual(['DRIVES/D000001.HHD']);
    expect(report.present).toEqual(['HANDHELD.MAP']);
    expect(report.bytes).toBe(DRIVE.length - 4 * P.CHUNK); // only the rest was sent again
    expect(store.files.get('DRIVES/D000001.HHD')).toEqual(DRIVE);
    expect(store.files.has('DRIVES/D000001.HHD.part')).toBe(false);
    const again = await sync(cyd, store);
    expect(again.present).toEqual(['HANDHELD.MAP', 'DRIVES/D000001.HHD']);
    expect(again.fetched).toEqual([]);
    const files = await mirrorFiles(store);
    expect([...files.keys()].sort()).toEqual(['DRIVES/D000001.HHD', 'HANDHELD.MAP']);
    expect(mirrorName('HH-1373A0')).toBe('HH-1373A0');
    expect(mirrorName('a b/c')).toBe('a_b_c');
  });

  it('skips the preallocated tail of a drive that power cut short', async () => {
    const end = rfindBytes(DRIVE, F.BLOCK_MAGIC_BYTES);
    const real = DRIVE.slice(0, end); // no END block
    const junk = Uint8Array.from({ length: 700_000 }, (_, i) => (i * 131 + 7) & 0xff);
    const { cyd } = await client();
    const fake = cyd.port as FakeCyd;
    fake.sd!.set('DRIVES/D000001.HHD', concat(real, junk));
    const store = new MemoryMirror();
    const report = await sync(cyd, store);
    expect(store.files.get('DRIVES/D000001.HHD')).toEqual(real);
    expect(report.bytes).toBeLessThan(MAP.length + real.length + UsedLength.GARBAGE + 50_000);
    const again = await sync(cyd, store);
    expect(again.present).toEqual(['HANDHELD.MAP', 'DRIVES/D000001.HHD']);
  });

  it('a drive still being written is not copied', async () => {
    const { fake, cyd } = await client();
    fake.busy = 'DRIVES/D000001.HHD';
    const report = await sync(cyd, new MemoryMirror());
    expect(report.open).toEqual(['DRIVES/D000001.HHD']);
    expect(report.fetched).toEqual(['HANDHELD.MAP']);
  });
});

describe('UsedLength and the clock', () => {
  it('follows blocks; a clean END is where it stops', () => {
    const watch = new UsedLength();
    let got: number | null = null;
    for (let i = 0; i < DRIVE.length; i += 97) {
      got = watch.feed(DRIVE.subarray(i, i + 97));
      if (got !== null) break;
    }
    expect(got).toBe(DRIVE.length);
    const other = new UsedLength();
    expect(other.feed(utf8('not a drive file at all'.repeat(10)))).toBeNull();
    expect(other.off).toBe(true);
  });

  it('the clock exchange takes the shortest round trip', () => {
    const cs = new ClockSync();
    expect(cs.valid).toBe(false);
    cs.add(1000, 50, 60, 1200); // rtt 190
    cs.add(2000, 1050, 1060, 2050); // rtt 40, offset 970
    cs.add(3000, 2000, 2000, 2990); // negative: ignored
    expect(cs.rttUs).toBe(40);
    expect(cs.offsetUs).toBe(970);
    expect(cs.toHostUs(100)).toBe(1070);
    expect(crc32(utf8('x'))).toBeGreaterThan(0);
  });
});
