/**
 * The integrator's wiring end to end on the built-in simulator (real clock,
 * byte timing): poller -> live session -> session store and hondalog.db.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_MAP_TEXT } from '../core/addressmap';
import { resetSession, useSession } from '../core/session';
import { MemoryFileStore } from '../core/store/filestore';
import { useDriveSource } from '../ui/replay/source';
import { AppController } from './controller';
import { MAP_FILE, MapStore } from './maps';
import { LivePipeline } from './pipeline';
import { recorderMapOf } from './recordermap';
import { loadDefault } from '../core/addressmap';
import { livePids, obdClock } from './obd2fallback';
import { VirtualClock } from '../core/link/clock';

async function until(pred: () => boolean, ms = 12000): Promise<void> {
  const end = Date.now() + ms;
  while (!pred()) {
    if (Date.now() > end) throw new Error('timed out');
    await new Promise((r) => setTimeout(r, 50));
  }
}

let app: AppController | null = null;
afterEach(async () => {
  await app?.close();
  app = null;
  resetSession();
});

describe('map store', () => {
  it('writes the shipped map on first run and keeps the owner map', async () => {
    const files = new MemoryFileStore();
    const maps = new MapStore(files);
    const first = await maps.load();
    expect(first.error).toBe('');
    expect(new TextDecoder().decode((await files.read(MAP_FILE))!)).toBe(DEFAULT_MAP_TEXT);
    const own = DEFAULT_MAP_TEXT.replace(/^version = "([^"]*)"/m, 'version = "$1-owner"');
    await maps.save(own);
    expect((await maps.load()).map.version).toMatch(/-owner$/);
    await expect(maps.save('this is not toml [')).rejects.toThrow();
    await files.write(MAP_FILE, new TextEncoder().encode('[[channel]]\nname = 1'));
    const bad = await maps.load();
    expect(bad.error).toMatch(/does not load/);
    expect(bad.text).toBe(DEFAULT_MAP_TEXT);
  });
});

describe('recorder map and pipeline', () => {
  it('decodes with the address map formulas and runs math and alarms', () => {
    const map = loadDefault();
    const rmap = recorderMapOf(map);
    expect(rmap.versionHash).toBe(map.versionHash);
    expect(rmap.channels.length).toBe(map.channels.length);
    const rpm = map.channel('rpm')!;
    const raw = Uint8Array.from({ length: rpm.length }, (_, i) => (i === 0 ? 0x10 : 0x00));
    expect(rmap.channels.find((c) => c.name === 'rpm')!.convert(raw)).toBe(rpm.convert(raw));
    const p = new LivePipeline(map);
    expect(p.errors).toEqual([]);
    const values = Object.fromEntries(map.names.map((n) => [n, 1]));
    const out = p.process(values, 1, true);
    for (const n of p.mathNames) expect(n in out.values).toBe(true);
  });

  it('OBD2 helpers', () => {
    expect(livePids([0x01, 0x0c, 0x0d, 0x42])).toEqual([0x0c, 0x0d]);
    expect(livePids([0x42])).toEqual([0x0c, 0x0d, 0x05]);
    const c = new VirtualClock(1);
    const oc = obdClock(c);
    void oc.sleepUntil(3);
    expect(oc.now()).toBe(3);
  });
});

describe('AppController on the simulator', () => {
  it('polls, fills the session, records a drive the store lists, and marks', async () => {
    const files = new MemoryFileStore();
    app = new AppController({ files, picker: null, storeOptions: { flushIntervalS: 0.2, checkpointS: 0 } });
    await app.init();
    expect(useDriveSource.getState().demo).toBe(false);
    await app.connect({ kind: 'sim' });
    expect(useSession.getState().ecu.simulated).toBe(true);
    await until(() => useSession.getState().ecu.state === 'ok' && (useSession.getState().channels.rpm?.fresh ?? false));
    const s = useSession.getState();
    expect(s.channels.rpm!.value).toBeGreaterThan(300);
    expect(Object.keys(s.raw).length).toBeGreaterThan(10);
    expect(s.recording.active).toBe(true);                     // auto-record when the link comes up
    useSession.getState().mark('test mark');
    await until(() => (useSession.getState().recording.drive?.name ?? '') !== '', 15000);
    await new Promise((r) => setTimeout(r, 600));
    useSession.getState().stopRecording();
    await until(() => !app!.live!.recording);
    const drives = await app.store!.listDrives();
    expect(drives.length).toBeGreaterThanOrEqual(1);
    const d = await app.store!.loadDrive(drives[0]!.name);
    expect(d.series.rpm!.t.length).toBeGreaterThan(5);
    expect(d.events.some((e) => e.text.includes('test mark'))).toBe(true);
    await app.disconnect();
    expect(useSession.getState().ecu.state).toBe('stopped');
  }, 30000);
});

describe('AppController protocol "OBD2 only"', () => {
  it('skips Honda reads and starts in the ISO 9141 path', async () => {
    const files = new MemoryFileStore();
    let opens = 0;
    let hondaWrites = 0;
    const { createSimTransport } = await import('../core/sim/transport');
    app = new AppController({
      files, picker: null, protocol: () => 'obd2', autoRecord: false, companion: false,
      makeSim: () => {
        const t = createSimTransport({ preset: 'p2p-car' });
        const write = t.write.bind(t);
        t.write = (d: Uint8Array) => { hondaWrites++; return write(d); };
        return t;
      },
      makeIsoFallback: (hooks) => ({
        async open(_t, c) {
          opens++;
          hooks.onInit?.('start', 'OBD2 5-baud init', false);
          await c.sleep(0.05);
          hooks.onInit?.('ok', 'ISO 9141-2 (fake)', false);
          return {
            label: 'ISO 9141-2 (fake)',
            async next() { await c.sleep(0.02); return { pid: 0x0c, raw: Uint8Array.of(0x0b, 0xb8), emit: true }; },
            async close() {},
          };
        },
      }),
    });
    await app.init();
    await app.connect({ kind: 'sim' });
    expect(app.poller!.obd2Only).toBe(true);
    await until(() => useSession.getState().ecu.obd2 && useSession.getState().ecu.state === 'iso_only', 12000);
    expect(opens).toBe(1);
    expect(hondaWrites).toBe(0);
    expect(app.poller!.reads).toBeGreaterThan(0);
    expect(app.poller!.ecuId).toBeNull();
  }, 30000);
});

describe('poke test live pause / resume', () => {
  it('stops the live poller around the run and always resumes it', async () => {
    const { withLinePaused } = await import('../ui/bytes/poke/runner');
    const { memLinkOf } = await import('./index');
    const files = new MemoryFileStore();
    app = new AppController({ files, picker: null, autoRecord: false, companion: false });
    await app.init();
    await app.connect({ kind: 'sim' });
    await until(() => useSession.getState().ecu.state === 'ok', 15000);
    const live = { pause: () => app!.pauseLive(), resume: () => app!.resumeLive() };
    const mem = memLinkOf(app);
    let cyclesDuring = -1;
    const got = await withLinePaused(live, async () => {
      expect(app!.poller!.paused).toBe(true);
      expect(useSession.getState().ecu.detail).toMatch(/paused/);
      const c0 = app!.poller!.cycles;
      const bytes = await mem.readMem(0x00, 8);          // the scanner's reads still go through
      await new Promise((r) => setTimeout(r, 300));
      cyclesDuring = app!.poller!.cycles - c0;
      return bytes;
    });
    expect(got.length).toBe(8);
    expect(cyclesDuring).toBe(0);
    expect(app.poller!.paused).toBe(false);
    const c1 = app.poller!.cycles;
    await until(() => app!.poller!.cycles > c1 + 2, 15000);
    // an error inside the run still resumes
    await expect(withLinePaused(live, async () => { throw new Error('boom'); })).rejects.toThrow('boom');
    expect(app.poller!.paused).toBe(false);
  }, 40000);
});

describe('a drive interrupted by a reload', () => {
  async function leftRecording(files: MemoryFileStore): Promise<string> {
    const { HondalogDb, SESSION_PID } = await import('../core/store/db');
    const { Store } = await import('../core/store/writer');
    const { column } = await import('../core/store/model');
    const hdb = await HondalogDb.openFile(files);
    const store = new Store(hdb, { background: false, checkpointS: 0 });
    const d = store.beginDrive({ started_unix: Date.now() / 1000 - 60 });
    d.addStream('s', [column('rpm')]);
    for (let i = 0; i < 10; i++) d.append('s', i, [800]);
    store.flush();
    hdb.conn.run('UPDATE drives SET writer_pid=?, heartbeat_at=?', [SESSION_PID + 7, Date.now() / 1000 - 2]);   // the page before the reload
    await hdb.persist();
    return d.name;
  }

  it('is crashed in the list as soon as the app starts', async () => {
    const files = new MemoryFileStore();
    const name = await leftRecording(files);
    app = new AppController({ files, picker: null, soleOwner: async () => true });
    await app.init();
    expect(app.recovered).toEqual([name]);
    const d = (await useDriveSource.getState().source.listDrives()).find((x) => x.name === name)!;
    expect(d.status).toBe('crashed');
  });

  it('is left alone while another tab holds the database', async () => {
    const files = new MemoryFileStore();
    const name = await leftRecording(files);
    app = new AppController({ files, picker: null, soleOwner: async () => false });
    await app.init();
    expect(app.recovered).toEqual([]);
    expect((await app.store!.listDrives()).find((x) => x.name === name)!.status).toBe('recording');
  });

  it('the database is written when the page is hidden or goes away', async () => {
    const { flushOnHide } = await import('./index');
    const files = new MemoryFileStore();
    app = new AppController({ files, picker: null });
    await app.init();
    const persist = vi.spyOn(app.store!, 'persist');
    const off = flushOnHide(app, window, document);
    window.dispatchEvent(new Event('pagehide'));
    expect(persist).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
    expect(persist).toHaveBeenCalledTimes(2);
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
    expect(persist).toHaveBeenCalledTimes(2);
    off();
    window.dispatchEvent(new Event('pagehide'));
    expect(persist).toHaveBeenCalledTimes(2);
  });
});

describe('Setup CYD client over the HL session', () => {
  it('connects, lists the card, pushes the address map and lets the companion back', async () => {
    const { FakeCyd } = await import('../core/cyd/fake_cyd');
    const { HlCydClient } = await import('./cydclient');
    const fake = new FakeCyd({ sd: new Map([['DRIVES/D000001.HHD', new Uint8Array(100)]]) });
    const calls: string[] = [];
    const client = new HlCydClient({
      listPorts: async () => [{ id: 'a', label: 'FTDI', guess: 'ecu' }, { id: 'b', label: 'CH340', guess: 'cyd' }],
      openPort: () => fake,
      beforeConnect: async () => { calls.push('pause'); },
      afterDisconnect: async () => { calls.push('resume'); },
      clock: fake.clock,
    });
    expect((await client.ports())[0]!.id).toBe('b');
    const hello = await client.connect('b');
    expect(hello.fw).toBeTruthy();
    const [files, dirs] = await client.ls('/');
    expect(files.length + dirs.length).toBeGreaterThan(0);
    const msg = await client.pushAddressMap(DEFAULT_MAP_TEXT);
    expect(msg).toContain(loadDefault().versionHash);
    expect(fake.sd!.get('HANDHELD.MAP')!.length).toBeGreaterThan(100);
    await client.disconnect();
    expect(calls).toEqual(['pause', 'resume']);
    await expect(client.flash(new Uint8Array(10), 460800, () => undefined)).rejects.toThrow(/Web Serial/);
  });
});
