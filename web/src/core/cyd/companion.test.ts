import { describe, expect, it } from 'vitest';
import type { PortInfo } from '../types';
import { type Fix, HandheldCompanion, type PortSource, findHandheld, normPort, samePort, specPort } from './companion';
import { FakeCyd, PEARL, until } from './fake_cyd';
import { VirtualClock } from './port';
import * as P from './serial_proto';

const ECU: PortInfo = { id: 'COM21', label: 'COM21', vendorId: 0x0403, productId: 0x6001, guess: 'ecu' };
const CYD: PortInfo = { id: 'COM22', label: 'COM22', vendorId: 0x1a86, productId: 0x7523, guess: 'cyd' };

function source(fake: FakeCyd, ports: PortInfo[] = [ECU, CYD]): PortSource & { opened: string[] } {
  const opened: string[] = [];
  return {
    opened,
    list: async () => ports,
    open: (p) => {
      opened.push(p.id);
      return fake;
    },
  };
}

describe('the handheld next to the KKL cable (test_hh_companion)', () => {
  it('same port and spec', () => {
    expect(samePort('cyd:COM22@460800', 'com22')).toBe(true);
    expect(samePort('COM22', 'COM21')).toBe(false);
    expect(samePort('', '')).toBe(false);
    expect(samePort('\\\\.\\COM22', 'COM22')).toBe(true);
    expect(normPort('cyd:/dev/ttyUSB0@921600')).toBe('/dev/ttyUSB0');
    expect(specPort('COM22')).toBe('cyd:COM22');
    expect(specPort('cyd:COM22')).toBe('cyd:COM22');
  });

  it('auto never takes the ECU port', async () => {
    const fake = new FakeCyd();
    expect((await findHandheld(source(fake), 'COM22'))).toBeNull();
    expect((await findHandheld(source(fake), 'COM21'))!.id).toBe('COM22');
    expect((await findHandheld(source(fake, [ECU, { ...CYD, guess: 'unknown' }]), 'COM21'))!.id).toBe('COM22'); // by its USB id
  });

  it('a named port that is the ECU port is refused and nothing opened', async () => {
    const fake = new FakeCyd();
    const src = source(fake);
    const hh = new HandheldCompanion(null, { spec: 'COM21', exclude: 'cyd:COM21@460800', ports: src, clock: fake.clock }).start();
    await until(() => fake.clock.now() > 0 || hh.readyDone, fake.clock).catch(() => undefined);
    await hh.ready;
    expect(hh.statusText()).toBe("handheld: COM21 is the ECU's port");
    expect(src.opened).toEqual([]);
    await hh.stop();
  });

  it('auto finds the CH340 and reads its GPS on the ECU poller time axis', async () => {
    const fake = new FakeCyd({ gpsS: 0.2 });
    const src = source(fake);
    const fixes: Array<[number, Fix]> = [];
    fake.clock.advance(100);
    const hh = new HandheldCompanion(null, { spec: 'auto', exclude: 'COM21', ports: src, clock: fake.clock, t0: 95,
      onFix: (t, f) => fixes.push([t, f]) }).start();
    await until(() => fixes.length >= 5, fake.clock);
    expect(src.opened).toEqual(['COM22']);
    expect(hh.found).toBe('COM22');
    expect(hh.statusText()).toBe('handheld HH-1373A0 fw 0.1.0');
    expect(hh.gpsOk).toBe(true);
    expect(hh.gpsText()).toBe('GPS: handheld (9 sats)');
    const [t, f] = fixes[fixes.length - 1]!;
    expect(t).toBeGreaterThan(5); // seconds on t0 = 95
    expect(t).toBeLessThan(fake.clock.now() - 95 + 1e-6);
    expect(f.gps_lat).toBeCloseTo(PEARL[0], 2);
    expect(f.gps_fix).toBe(1);
    // the heartbeat that shows "PC" and the clock exchange
    await until(() => hh.clockSync.count >= 5, fake.clock);
    expect(fake.sent(P.SC_TIME_REQ).length).toBeGreaterThanOrEqual(5);
    expect(hh.clockSync.valid).toBe(true);
    expect(hh.linkText()).toMatch(/^CYD HH-1373A0 fw 0.1.0 · two-way/);
    // a fix older than GPS_FRESH_S no longer counts
    await hh.stop();
    fake.clock.advance(6);
    expect(hh.gpsOk).toBe(false);
  });

  it('a record without a fix is not passed on (sats still counted)', async () => {
    const fake = new FakeCyd({ fix: false, sats: 3 });
    const fixes: Fix[] = [];
    const hh = new HandheldCompanion(fake, { clock: fake.clock, onFix: (_t, f) => fixes.push(f) }).start();
    await until(() => fake.gpsSent > 10, fake.clock);
    expect(fixes).toEqual([]);
    expect(hh.sats).toBe(3);
    expect(hh.gpsText()).toBe('');
    await hh.stop();
  });

  it('no handheld: auto keeps looking and says so', async () => {
    const clock = new VirtualClock();
    const fake = new FakeCyd({ clock });
    const src = source(fake, [ECU]);
    const hh = new HandheldCompanion(null, { spec: 'auto', exclude: 'COM21', ports: src, clock }).start();
    await hh.ready;
    expect(hh.statusText()).toBe('handheld: no handheld found');
    expect(src.opened).toEqual([]);
    await hh.stop();
  });

  it('off opens nothing', async () => {
    const fake = new FakeCyd();
    const src = source(fake);
    const hh = new HandheldCompanion(null, { spec: 'off', ports: src, clock: fake.clock }).start();
    await hh.ready;
    expect(src.opened).toEqual([]);
    expect(hh.statusText()).toBe('');
    await hh.stop();
  });
});
