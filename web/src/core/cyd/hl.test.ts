import { describe, expect, it } from 'vitest';
import { concat, crc32, utf8, unb64 } from './bytes';
import { FakeCyd } from './fake_cyd';
import * as H from './hl_proto';
import { Cancelled, HlClient, HlError, HlTxOnly, MapFolderError, mapFolderFiles } from './hl_client';
import { DEFAULT_CFG, buildMapFromToml } from './mapfile';
import * as P from './serial_proto';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const FIX = join(dirname(fileURLToPath(import.meta.url)), '__fixtures__');
const MAP = new Uint8Array(readFileSync(join(FIX, 'default.map')));

async function rig(opts: ConstructorParameters<typeof FakeCyd>[0] = {}) {
  const sd = new Map<string, Uint8Array>([
    ['HANDHELD.MAP', MAP],
    ['HANDHELD.CFG', utf8(DEFAULT_CFG)],
    ['DRIVES/D000001.HHD', bytesOf(5000, 1)],
    ['DRIVES/D000002.HHD', bytesOf(300, 2)],
  ]);
  const fake = new FakeCyd({ sd, logs: true, ...opts });
  await fake.open();
  const consoles: string[] = [];
  const client = new HlClient(fake, { onConsole: (t) => consoles.push(t), clock: fake.clock });
  await client.connect();
  return { client, fake, consoles, sd };
}

function bytesOf(n: number, seed: number): Uint8Array {
  const out = new Uint8Array(n);
  for (let i = 0; i < n; i++) out[i] = (i * 31 + seed * 7) & 0xff;
  return out;
}

function fakeMap(build = 7, idxOk = true, size = 70000): [Uint8Array, Uint8Array] {
  const idx = new Uint8Array(428);
  idx.set(utf8('HHMAPIDX'));
  const dv = new DataView(idx.buffer);
  dv.setUint16(8, 1, true);
  dv.setUint16(10, 428, true);
  dv.setUint32(96, build, true);
  dv.setUint32(424, idxOk ? crc32(idx.subarray(0, 424)) : 1, true);
  const head = new Uint8Array(16);
  head.set(utf8('HHMAPDAT'));
  const hv = new DataView(head.buffer);
  hv.setUint16(8, 1, true);
  hv.setUint16(10, 16, true);
  hv.setUint32(12, build, true);
  return [idx, concat(head, bytesOf(size, 3))];
}

describe('the byte stream (test_hl_client)', () => {
  it('the splitter separates frames and lines', () => {
    const frame = P.frameEncode(P.SF_LOG, utf8('hello\nworld'), 5);
    const other = P.frameEncode(P.SF_ACK, P.ACK.pack(1, 2, 0, 0), 6);
    const stream = concat(Uint8Array.of(0), frame, utf8('I (12) tag: text\r\n'), other, utf8('HL OK\n'), utf8('x\n'), utf8('HL END\n'));
    for (const step of [stream.length, 1, 7]) {
      const sp = new H.StreamSplitter();
      const got: H.Piece[] = [];
      for (let i = 0; i < stream.length; i += step) got.push(...sp.feed(stream.subarray(i, i + step)));
      expect(got.map((g) => g.kind)).toEqual(['frame', 'line', 'frame', 'line', 'line', 'line']);
      const f = got[0] as Extract<H.Piece, { kind: 'frame' }>;
      expect([f.frame[0], f.frame[1], new TextDecoder().decode(f.frame[2])]).toEqual([P.SF_LOG, 5, 'hello\nworld']);
      expect(got.filter((g) => g.kind === 'line').map((g) => (g as any).text)).toEqual(['I (12) tag: text', 'HL OK', 'x', 'HL END']);
      expect(sp.bad).toBe(0);
    }
  });

  it('the splitter counts damage and survives it', () => {
    const frame = P.frameEncode(P.SF_LOG, utf8('abc'), 1);
    frame[4]! ^= 0x10;
    const good = P.frameEncode(P.SF_LOG, utf8('ok'), 2);
    const sp = new H.StreamSplitter();
    const got = sp.feed(concat(frame, good, utf8('HL OK\n')));
    expect(got.map((g) => g.kind)).toEqual(['frame', 'line']);
    expect(new TextDecoder().decode((got[0] as any).frame[2])).toBe('ok');
    expect(sp.bad).toBe(1);
  });

  it.each([
    ['/', []], ['/MAP/map.dat', ['MAP', 'MAP.DAT']], ['/HANDHELD.CFG', ['HANDHELD.CFG']], ['/MAP/', ['MAP']],
    ['MAP/MAP.DAT', null], ['/../x', null], ['/MAP/../HANDHELD.MAP', null], ['/A//B', null], ['/MAP\\X', null],
    ['/MAP/a b', null], ['/' + 'A'.repeat(13), null], ['/.', null], ['/MAP/..', null], ['/' + 'A/'.repeat(40), null],
    ['/MAP/MAP.DAT\n', null], ['', null],
  ])('path rules %s', (path, parts) => expect(H.splitPath(path as string)).toEqual(parts));

  it.each([
    ['/MAP/MAP.DAT', true, true], ['/MAP/README.TXT', true, true], ['/HANDHELD.CFG', true, true],
    ['/HANDHELD.MAP', true, true], ['/DRIVES/D000001.HHD', false, true], ['/CAPTURES/C000001.HHD', false, false],
    ['/DRIVES', false, false], ['/MAP', false, false], ['/MAP/X/Y', false, false], ['/OTHER.TXT', false, false],
    ['/', false, false],
  ])('write and remove allowlists %s', (path, write, remove) => {
    const parts = H.splitPath(path as string)!;
    expect(H.canWrite(parts)).toBe(write);
    expect(H.canRemove(parts)).toBe(remove);
  });

  it('base64 is strict', () => {
    expect(Array.from(unb64('AAEC')!)).toEqual([0, 1, 2]);
    expect(unb64('')!.length).toBe(0);
    for (const bad of ['AAE', 'AA!C', 'AAEC=', 'A===', 'AA\nC']) expect(unb64(bad)).toBeNull();
  });
});

describe('the session (test_hl_client, test_hh_hl)', () => {
  it('hello pauses the stream and bye resumes it', async () => {
    const { client, fake } = await rig();
    const info = await client.hello();
    expect(info.model).toBe('cyd-4827s043n');
    expect(info.proto).toBe('1');
    expect(info.variant).toBe('default');
    expect(info.id).toBe('HH-1373A0');
    expect(Number(info.chunk)).toBeGreaterThanOrEqual(512);
    const before = fake.gpsSent;
    await client.stat();
    await client.pump(1.0);
    expect(fake.gpsSent).toBe(before); // paused
    await client.bye();
    expect(client.open).toBe(false);
    client.helloFrame = null;
    for (let i = 0; i < 40 && client.helloFrame === null; i++) await client.pump(0.05);
    expect(client.helloFrame).not.toBeNull();
    expect(fake.gpsSent).toBeGreaterThan(before);
  });

  it('stat, mark, time and the console', async () => {
    const { client, fake, consoles } = await rig({ gpsTime: false });
    const st = await client.stat();
    expect(st.fw).toBe('0.1.0');
    await client.mark();
    expect(fake.markers).toBe(1);
    expect(await client.setTime(1_700_000_000_000)).toBe('ok');
    expect(fake.timeSet).not.toBeNull();
    expect(consoles.some((c) => c.includes('session open'))).toBe(true);
    fake.gpsTime = true;
    expect(await client.setTime()).toBe('ignored');
  });

  it('ls, get, put, rm', async () => {
    const { client, sd } = await rig();
    const [files, dirs] = await client.ls('/');
    expect(dirs).toEqual(['/DRIVES']);
    expect(files.map((f) => f[0])).toEqual(['/HANDHELD.CFG', '/HANDHELD.MAP']);
    const [drives] = await client.ls('/DRIVES');
    expect(drives.map((f) => [f[0], f[1]])).toEqual([['/DRIVES/D000001.HHD', 5000], ['/DRIVES/D000002.HHD', 300]]);
    const seen: number[] = [];
    const got = await client.get('/DRIVES/D000001.HHD', (d) => seen.push(d));
    expect(got).toEqual(sd.get('DRIVES/D000001.HHD'));
    expect(seen[seen.length - 1]).toBe(5000);
    const data = bytesOf(9000, 5);
    const note = await client.put('/MAP/README.TXT', data);
    expect(note).toBe('');
    expect(sd.get('MAP/README.TXT')).toEqual(data);
    await client.rm('/MAP/README.TXT');
    expect(sd.has('MAP/README.TXT')).toBe(false);
    await expect(client.rm('/CAPTURES/C000001.HHD')).rejects.toMatchObject({ reason: 'denied' });
    await expect(client.get('/NOPE.TXT')).rejects.toMatchObject({ reason: 'nofile' });
  });

  it('an empty file', async () => {
    const { client, sd } = await rig();
    expect(await client.put('/MAP/EMPTY.TXT', new Uint8Array(0))).toBe('');
    expect(sd.get('MAP/EMPTY.TXT')!.length).toBe(0);
    expect((await client.get('/MAP/EMPTY.TXT')).length).toBe(0);
  });

  it.each([['put', '/DRIVES/D000009.HHD', 'denied'], ['put', '/../X', 'path'], ['get', '/A//B', 'path'],
    ['rm', '/OTHER.TXT', 'denied']])('paths are policed: %s %s', async (op, path, reason) => {
    const { client } = await rig();
    const p = op === 'put' ? client.put(path, utf8('x')) : op === 'get' ? client.get(path) : client.rm(path);
    await expect(p).rejects.toMatchObject({ reason });
  });

  it('the drive being written is busy, other drives can be removed', async () => {
    const { client, fake, sd } = await rig();
    fake.busy = 'DRIVES/D000002.HHD';
    await expect(client.get('/DRIVES/D000002.HHD')).rejects.toMatchObject({ reason: 'busy' });
    await expect(client.rm('/DRIVES/D000002.HHD')).rejects.toMatchObject({ reason: 'busy' });
    await client.rm('/DRIVES/D000001.HHD');
    expect(sd.has('DRIVES/D000001.HHD')).toBe(false);
  });

  it('a config is checked; its skipped lines are counted and the old one kept', async () => {
    const { client, sd } = await rig();
    const note = await client.cfgPut(utf8('gps_baud = 4800\nnot_a_key = 1\n# comment\n'));
    expect(note).toBe('lines=2 skipped=1');
    expect(new TextDecoder().decode(await client.cfgGet())).toContain('gps_baud = 4800');
    expect(sd.get('HANDHELD.CBK')).toEqual(utf8(DEFAULT_CFG));
  });

  it('a map that does not load is refused, a good one replaces the old', async () => {
    const { client, sd } = await rig();
    await expect(client.put('/HANDHELD.MAP', utf8('not a map at all'))).rejects.toMatchObject({ reason: 'refused' });
    expect(sd.get('HANDHELD.MAP')).toEqual(MAP);
    const text = readFileSync(join(FIX, 'addressmap.toml'), 'utf-8');
    const p2t = buildMapFromToml(text, 'P2T');
    await client.put('/HANDHELD.MAP', p2t);
    expect(sd.get('HANDHELD.MAP')).toEqual(p2t);
    expect(sd.get('HANDHELD.MBK')).toEqual(MAP);
  });

  it('an upload resumes after a failure on the card', async () => {
    const { client, fake, sd } = await rig();
    fake.hlFailAfter = 3;
    const data = bytesOf(30000, 9);
    const retries: string[] = [];
    await client.put('/MAP/MAP.DAT', data, undefined, 4, (_n, e) => retries.push((e as HlError).reason));
    expect(retries).toEqual(['io']);
    expect(sd.get('MAP/MAP.DAT')).toEqual(data);
    const puts = fake.hlCommands.filter((c) => c === 'PUT');
    expect(puts.length).toBe(2);
  });

  it('a cancelled upload carries on where it stopped', async () => {
    const { client, sd } = await rig();
    const data = bytesOf(40000, 4);
    let first = 0;
    await expect(client.put('/MAP/MAP.DAT', data, (d) => {
      if (d > 8000 && !client.cancelFlag) {
        first = d;
        client.cancelFlag = true;
      }
    })).rejects.toBeInstanceOf(Cancelled);
    client.cancelFlag = false;
    const [held, size] = await client.partial('/MAP/MAP.DAT');
    expect(held).toBeGreaterThanOrEqual(first);
    expect(size).toBe(data.length);
    const seen: number[] = [];
    await client.drain();
    await client.put('/MAP/MAP.DAT', data, (d) => seen.push(d));
    expect(seen[0]).toBe(held); // started from what the handheld held
    expect(sd.get('MAP/MAP.DAT')).toEqual(data);
  });

  it('a damaged data line fails the chunk, then the client resumes', async () => {
    const { client, fake, sd } = await rig();
    const data = bytesOf(20000, 6);
    const orig = fake.write.bind(fake);
    let damaged = false;
    fake.write = async (b: Uint8Array) => {
      const text = new TextDecoder().decode(b);
      if (!damaged && text.startsWith('HL DATA @4096')) {
        damaged = true;
        return orig(utf8(text.slice(0, -5) + '!!!!\n'));
      }
      return orig(b);
    };
    const retries: string[] = [];
    await client.put('/MAP/MAP.DAT', data, undefined, 4, (_n, e) => retries.push((e as HlError).reason));
    expect(retries).toEqual(['b64']);
    expect(sd.get('MAP/MAP.DAT')).toEqual(data);
  });

  it('a bad CRC replaces nothing', async () => {
    const { client, sd } = await rig();
    sd.set('MAP/MAP.DAT', utf8('old'));
    // a PUT whose announced CRC is not the data's:
    (client as any).putOnce = async (cmd: string, data: Uint8Array) => {
      const n = data.length;
      await (client as any).ensure();
      (client as any).q = [];
      await (client as any).send(`${cmd} ${n} 00000000`);
      await (client as any).next(5);
      await fakeSend(client, data);
      for (;;) {
        const line: string = await (client as any).next(5);
        if (line.startsWith('HL ERR')) throw new HlError(line.slice(7));
        if (line.startsWith('HL OK')) return '';
      }
    };
    await expect(client.put('/MAP/MAP.DAT', bytesOf(3000, 1))).rejects.toMatchObject({ reason: 'crc' });
    expect(sd.get('MAP/MAP.DAT')).toEqual(utf8('old'));
  });

  it('the session times out after 30 seconds and is opened again', async () => {
    const { client, fake } = await rig();
    await client.hello();
    fake.clock.advance(31);
    await client.pump(0.1);
    expect(client.open).toBe(false);
    const st = await client.stat(); // opens it again by itself
    expect(st.fw).toBe('0.1.0');
    expect(fake.hlCommands.filter((c) => c === 'HELLO').length).toBe(2);
  });

  it('commands before hello are refused by the handheld', async () => {
    const fake = new FakeCyd();
    await fake.open();
    await fake.write(utf8('HL STAT\n'));
    expect(new TextDecoder().decode(await fake.read(0.01))).toContain('HL ERR nosession');
  });

  it('the map folder is checked, then uploaded with progress', async () => {
    const { client, sd } = await rig();
    const [idx, dat] = fakeMap();
    const files = mapFolderFiles({ 'map.idx': idx, 'MAP.DAT': dat, 'README.TXT': utf8('hello') });
    expect(files.map((f) => f[0])).toEqual(['MAP.DAT', 'MAP.IDX', 'README.TXT']);
    const seen: Array<[string, number]> = [];
    const out = await client.putMapFiles(files, (name, _h, _s, done) => seen.push([name, done]));
    expect(out).toEqual([['MAP.DAT', dat.length], ['MAP.IDX', 428], ['README.TXT', 5]]);
    expect(seen[seen.length - 1]).toEqual(['README.TXT', dat.length + 428 + 5]);
    expect(sd.get('MAP/MAP.IDX')).toEqual(idx);
    expect(() => mapFolderFiles({ 'MAP.IDX': idx })).toThrow(MapFolderError);
    expect(() => mapFolderFiles({ 'MAP.IDX': fakeMap(7, false)[0], 'MAP.DAT': dat })).toThrow(/damaged/);
    expect(() => mapFolderFiles({ 'MAP.IDX': idx, 'MAP.DAT': fakeMap(8)[1] })).toThrow(/different builds/);
  });
});

describe('who may be written to', () => {
  it('a tx-only handheld is never written to', async () => {
    const fake = new FakeCyd({ txOnly: true });
    await fake.open();
    const client = new HlClient(fake, { clock: fake.clock });
    await expect(client.connect()).rejects.toBeInstanceOf(HlTxOnly);
    expect(fake.rxIgnored).toBe(0);
  });

  it('the wrong speed finds nothing and writes nothing', async () => {
    const fake = new FakeCyd({ deviceBaud: 460800, baud: 921600 });
    await fake.open();
    const client = new HlClient(fake, { clock: fake.clock });
    await expect(client.connect([921600, 115200], 1.0)).rejects.toMatchObject({ reason: 'nohello' });
    expect(fake.rxIgnored).toBe(0);
    expect(fake.frames.length).toBe(0);
    expect((await client.connect([460800], 1.0)).fw).toBe('0.1.0');
  });
});

async function fakeSend(client: HlClient, data: Uint8Array): Promise<void> {
  const { b64 } = await import('./bytes');
  for (let i = 0; i < data.length; i += 2048) {
    await client.port.write(utf8(`HL DATA @${i} ${b64(data.subarray(i, i + 2048))}\n`));
  }
}
