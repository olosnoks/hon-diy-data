import { describe, expect, it } from 'vitest';
import { autoVerdict, eventsOf } from './detect';
import { fixtureDrives } from '../replay/fixture';

describe('events of a drive', () => {
  const b = fixtureDrives()[0]!;
  const ev = eventsOf(b);
  const of = (k: string) => ev.filter((e) => e.kind === k);

  it('finds what tonight-shaped drive B holds, newest first', () => {
    expect(ev.map((e) => e.unix)).toEqual([...ev.map((e) => e.unix)].sort((x, y) => y - x));
    expect(new Set(ev.map((e) => e.id)).size).toBe(ev.length);
    const near = of('near_stall');
    expect(near.map((e) => Math.round(e.value / 10) * 10).sort()).toEqual([440, 550]);
    expect(of('dropout')).toHaveLength(2);
    expect(of('dropout').every((e) => e.text.includes('engine ran') && e.text.includes('moving'))).toBe(true);
    expect(of('vtec')).toHaveLength(1);
    expect(of('key_off')).toHaveLength(1);
    expect(of('mark')).toHaveLength(1);
    expect(of('harsh_brake').some((e) => e.title.startsWith('Harsh brake'))).toBe(true);
    expect(of('corner').some((e) => e.title.startsWith('Hard left'))).toBe(true);
    expect(of('harsh')).toHaveLength(0);           // the desktop's kind names: notes keyed kind@t carry over
    expect(of('stall')).toHaveLength(0);           // nothing died tonight
  });

  it('explains a near-stall from the signals', () => {
    const blip = of('near_stall').find((e) => e.value < 500)!;
    expect(blip.title).toMatch(/^Near-stall · 4\d\d rpm$/);
    expect(blip.why).toContain('Throttle blipped');
    expect(blip.why).toMatch(/under the 864 target/);
    expect(blip.why).toContain('coolant');
  });

  it('verdicts: the lowest near-stall is the closest call', () => {
    const both = [...ev, ...eventsOf(fixtureDrives()[1]!)];
    const lowest = both.filter((e) => e.kind === 'near_stall').sort((x, y) => x.value - y.value)[0]!;
    expect(autoVerdict(lowest, both)).toBe('closest call of the night');
    expect(autoVerdict(of('dropout')[0]!, both)).toBe('link only, engine ran');
  });

  it('a drive with no rpm still lists its marks', () => {
    const empty = { ...b, series: {}, gps: { t: new Float64Array(0), lat: new Float64Array(0), lon: new Float64Array(0), speedKmh: new Float64Array(0), heading: new Float64Array(0) } };
    const e = eventsOf(empty);
    expect(e.map((x) => x.kind)).toEqual(['mark']);
  });
});
