/**
 * The integrator's wiring end to end on the built-in simulator (real clock,
 * byte timing): poller -> live session -> session store and hondalog.db.
 */
import { afterEach, describe, expect, it } from 'vitest';
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
    await until(() => (useSession.getState().recording.drive?.name ?? '') !== '', 8000);
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
