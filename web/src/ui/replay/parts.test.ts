import { describe, expect, it, vi } from 'vitest';

// uPlot needs a canvas and matchMedia, which jsdom does not have
vi.mock('uplot', () => ({ default: class { static paths = {}; } }));
import { axisStep, laneRange, resample } from './Lanes';
import { ggPoints } from './GG';
import { withDerived } from './calc';
import { fixtureDrives } from './fixture';
import { clock, length, pickerLength, signed } from './fmt';
import { windowPoints } from '../events/Timeline';

describe('lane helpers', () => {
  it('resample holds the last sample and leaves gaps outside', () => {
    const tr = { x: Float64Array.from([1, 2, 3]), y: Float64Array.from([10, NaN, 30]) };
    expect(resample([0, 1, 1.5, 2.5, 3, 10], tr)).toEqual([null, 10, 10, null, 30, null]);
    expect(resample([1, 2], null)).toEqual([null, null]);
  });
  it('lane range pads the data; flags are 0..1', () => {
    const d = withDerived(fixtureDrives()[0]!);
    const [lo, hi] = laneRange(d, 'rpm', 'idle_target');
    expect(lo).toBeLessThan(440);
    expect(hi).toBeGreaterThan(5200);
    expect(laneRange(d, 'vtec')).toEqual([-0.15, 1.15]);
  });
  it('axis step leaves 64 px between labels', () => {
    expect(axisStep(60, 800)).toBe(5);
    expect(axisStep(1, 800)).toBe(0.1);
    expect(axisStep(5000, 400)).toBe(600);
  });
});

describe('g-g', () => {
  it('a 5 s trail at 10 Hz ending at the cursor', () => {
    const d = withDerived(fixtureDrives()[0]!);
    const pts = ggPoints(d, 92);                    // in the hard stop
    expect(pts.length).toBeGreaterThan(40);
    expect(pts[pts.length - 1]![1]).toBeLessThan(-0.3);
  });
});

describe('formatting', () => {
  it('clock, signed, lengths', () => {
    const at = new Date(2026, 10, 26, 23, 12, 41).getTime() / 1000;
    expect(clock(at)).toBe('23:12:41');
    expect(clock(at + 0.06, 1)).toBe('23:12:41.0');
    expect(clock(at + 0.96, 1)).toBe('23:12:41.9');
    expect(signed(-0.4, 2)).toBe('−0.40');
    expect(signed(0.001, 2)).toBe('0.00');
    expect(length(2148)).toBe('35:48');
    expect(pickerLength(45)).toBe('45 s');
    expect(pickerLength(2148)).toBe('36 min');
    expect(pickerLength(0)).toBe('');
  });
});

describe('timeline points', () => {
  it('thins long windows but keeps the extremes', () => {
    const t = Float64Array.from({ length: 5000 }, (_, i) => i / 100);
    const v = Float64Array.from(t, () => 800);
    v[2500] = 440;
    const pts = windowPoints({ t, v }, 0, 50, 100);
    expect(pts.length).toBeLessThanOrEqual(220);
    expect(Math.min(...pts.map((p) => p[1]))).toBe(440);
  });
});
