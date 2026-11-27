import { describe, expect, it } from 'vitest';
import { analyseDrive } from '../power/analysis';
import { eventsOf } from '../events/detect';
import { DEMO_DRIVE, DEMO_PULL, demoDrive, withDemoDrive } from './demodrive';
import { FAKE_ORIGIN, FixtureStore } from './fixture';

describe('the simulator demo drive', () => {
  it('has a 3rd-gear WOT pull from 2,000 to 6,500 rpm that Power finds', () => {
    const a = analyseDrive(demoDrive())!;
    expect(a).not.toBeNull();
    const best = a.pulls[0]!;
    expect(best.quality).toBe('full');
    expect(best.pull.gear).toBe(3);
    expect(best.pull.t0).toBeGreaterThanOrEqual(DEMO_PULL.t0 - 0.5);
    expect(best.pull.t1).toBeLessThanOrEqual(DEMO_PULL.t1 + 0.5);
    expect(best.pull.rpm0).toBeLessThan(2300);
    expect(best.pull.rpm1).toBeGreaterThan(6200);
    expect(a.rpmMax).toBeLessThan(6700);
  });

  it('has a made-up GPS track (open water, no real place) and the usual events', () => {
    const d = demoDrive();
    expect(d.gps.t.length).toBeGreaterThan(150);
    expect(Math.abs(d.gps.lat[0]! - FAKE_ORIGIN.lat)).toBeLessThan(1e-6);
    expect(Math.abs(d.gps.lon[0]! - FAKE_ORIGIN.lon)).toBeLessThan(1e-6);
    const kinds = new Set(eventsOf(d).map((e) => e.kind));
    for (const k of ['near_stall', 'vtec', 'dropout', 'key_off', 'mark']) expect(kinds).toContain(k);
  });

  it('joins the drive list of the store without touching it', async () => {
    const store = new FixtureStore();
    const src = withDemoDrive(store);
    const names = (await src.listDrives()).map((d) => d.name);
    expect(names).toContain(DEMO_DRIVE);
    expect(names).toEqual(expect.arrayContaining((await store.listDrives()).map((d) => d.name)));
    expect((await src.loadDrive(DEMO_DRIVE)).info.name).toBe(DEMO_DRIVE);
    await expect(src.deleteDrive!(DEMO_DRIVE)).rejects.toThrow(/built in/);
  });

  it('keeps notes on the demo drive in memory', async () => {
    const saved: string[] = [];
    const store = Object.assign(new FixtureStore(), {
      loadEventNotes: async () => ({}),
      saveEventNote: async (d: string) => { saved.push(d); },
    });
    const src = withDemoDrive(store);
    await src.saveEventNote!(DEMO_DRIVE, 'vtec@41.0', { note: 'nice', kind: 'vtec', t: 41 });
    expect((await src.loadEventNotes!(DEMO_DRIVE))['vtec@41.0']).toMatchObject({ note: 'nice', verdict: '' });
    expect(saved).toEqual([]);
    await src.saveEventNote!('drive_x', 'vtec@1.0', { note: 'x' });
    expect(saved).toEqual(['drive_x']);
  });
});
