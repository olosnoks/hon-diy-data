import { describe, expect, it } from 'vitest';
import { deriveChannels, gearOf, pullAt, torqueNow, withDerived } from './calc';
import { FAKE_ORIGIN, fixtureDrives, FixtureStore, SCRIPT_B } from './fixture';
import { valueAt } from '../../core/analysis/cursors';
import { channelMeta, compass, num, valueText } from './channels';

describe('calculated channels', () => {
  it('gear from the rpm/vss clusters, NaN when stopped or between gears', () => {
    expect(gearOf(2485, 50)).toBe(3);
    expect(gearOf(2592, 72)).toBe(4);
    expect(gearOf(800, 0)).toBeNaN();
    expect(gearOf(3000, 50)).toBeNaN();            // clutch in / shifting
  });
  it('torque from the model: none when the engine is off, more with more MAP', () => {
    expect(torqueNow(0, 30, 25, 2)).toBeNaN();
    expect(torqueNow(3000, 90, 25, 6)).toBeGreaterThan(torqueNow(3000, 50, 25, 4));
    expect(torqueNow(3000, 90, 25, 6)).toBeGreaterThan(80);
  });
  it('a drive gets every calculated channel', () => {
    const d = fixtureDrives()[0]!;
    const all = deriveChannels(d);
    for (const ch of ['gps_speed', 'gps_heading', 'lat_g', 'accel_g', 'gear', 'grade', 'torque', 'power', 'fuel_lph', 'rpm_err']) {
      expect(all[ch]?.t.length, ch).toBeGreaterThan(10);
    }
    // the hard stop at ~91 s brakes at about 0.5 g
    expect(valueAt(all.accel_g!, 91.5)).toBeLessThan(-0.35);
    // idle: about 1 L/h, rpm a little under the 864 target
    expect(valueAt(all.fuel_lph!, 10)).toBeGreaterThan(0.3);
    expect(valueAt(all.fuel_lph!, 10)).toBeLessThan(2.5);
    expect(valueAt(all.rpm_err!, 10)).toBeLessThan(0);
  });
  it('the pull the cursor is in', () => {
    const d = withDerived(fixtureDrives()[0]!);
    const p = pullAt(d.series, 118);                // the WOT 2nd-gear pull
    expect(p).not.toBeNull();
    expect(p!.tpsMean).toBeGreaterThan(70);           // the 30 % launch, then WOT
    expect(p!.rpm1).toBeGreaterThan(p!.rpm0);
    expect(pullAt(d.series, 5)).toBeNull();         // idling
  });
});

describe('fixture', () => {
  it('has no real place in it: the track starts at the made-up point', () => {
    const d = fixtureDrives()[0]!;
    expect(d.gps.lat[0]!).toBeCloseTo(FAKE_ORIGIN.lat, 1);
    expect(d.gps.lon[0]!).toBeCloseTo(FAKE_ORIGIN.lon, 1);
    expect(d.info.name).toBe(SCRIPT_B.name);
  });
  it('a store over the drives, newest first, with a capture', async () => {
    const s = new FixtureStore();
    const list = await s.listDrives();
    expect(list.map((d) => d.kind)).toContain('dropout');
    expect(list[0]!.startedUnix).toBeGreaterThanOrEqual(list[list.length - 1]!.startedUnix);
    const cap = await s.loadDrive('dropout_2026_11_26_2253');
    expect(cap.series.rpm!.t[0]!).toBeGreaterThanOrEqual(140);
    await s.deleteDrive('dropout_2026_11_26_2253');
    expect((await s.listDrives()).map((d) => d.name)).not.toContain('dropout_2026_11_26_2253');
    await expect(s.loadDrive('nope')).rejects.toThrow();
  });
});

describe('channel text', () => {
  it('numbers with thousands and a real minus', () => {
    expect(num(2214)).toBe('2,214');
    expect(num(-1350, 0, true)).toBe('−1,350');
    expect(num(1350, 0, true)).toBe('+1,350');
    expect(num(-0.001, 1)).toBe('0.0');
    expect(num(NaN)).toBe('—');
  });
  it('values the way the sensor list shows them', () => {
    expect(valueText(channelMeta('map'), 51)).toBe('51 kPa');
    expect(valueText(channelMeta('rpm'), 2214)).toBe('2,214');
    expect(valueText(channelMeta('ign'), 24.5)).toBe('24.5°');
    expect(valueText(channelMeta('vtec'), 0)).toBe('off');
    expect(valueText(channelMeta('closed_loop'), 1)).toBe('closed');
    expect(valueText(channelMeta('stft'), 4.7)).toBe('+4.7 %');
    expect(valueText(channelMeta('gear'), 2)).toBe('2nd');
    expect(valueText(channelMeta('gear'), NaN)).toBe('—');
    expect(compass(44)).toBe('NE');
    expect(channelMeta('mystery_byte').group).toBe('other');
  });
});
