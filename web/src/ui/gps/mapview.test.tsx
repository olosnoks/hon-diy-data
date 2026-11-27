// The real Leaflet map in jsdom (sizes and a 2D context stubbed): tiles come
// through the offline-first fetcher (never <img> requests), the track is cut
// into colour runs, pins are placed, a click on the track gives its time.
import { act, cleanup, render } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { DRACULA } from '../theme';
import MapView, { colourRuns, NODATA } from './MapView';
import { MemoryBackend, TileFetcher, TileStore, type GetFn, type TileSource } from './tiles';
import { Track } from './geo/track';
import { synthDrive } from './fixtures';

const noop = () => undefined;
const ctx = new Proxy({}, { get: (_t, k) => (k === 'canvas' ? document.createElement('canvas') : k === 'measureText' ? () => ({ width: 10 }) : noop), set: () => true });
const saved: Array<[object, string, PropertyDescriptor | undefined]> = [];

beforeAll(() => {
  for (const [obj, key, value] of [
    [HTMLElement.prototype, 'clientWidth', 512], [HTMLElement.prototype, 'clientHeight', 384],
  ] as const) {
    saved.push([obj, key, Object.getOwnPropertyDescriptor(obj, key)]);
    Object.defineProperty(obj, key, { configurable: true, get: () => value });
  }
  HTMLCanvasElement.prototype.getContext = (() => ctx) as unknown as HTMLCanvasElement['getContext'];
  if (!URL.createObjectURL) URL.createObjectURL = () => 'blob:tile';
  if (!URL.revokeObjectURL) URL.revokeObjectURL = noop;
});
afterAll(() => {
  for (const [obj, key, desc] of saved) {
    if (desc) Object.defineProperty(obj, key, desc); else delete (obj as Record<string, unknown>)[key];
  }
});
afterEach(cleanup);

const source: TileSource = { url: 'https://tiles.test/{z}/{x}/{y}.png', attribution: '© test contributors', minZoom: 0, maxZoom: 19 };

describe('MapView with Leaflet', () => {
  it('asks the fetcher for the tiles on screen, at most two at a time', async () => {
    const calls: string[] = [];
    let inflight = 0, most = 0;
    const get: GetFn = async (url) => {
      calls.push(url);
      inflight++; most = Math.max(most, inflight);
      await new Promise((r) => setTimeout(r, 1));
      inflight--;
      return { status: 200, headers: { 'Content-Type': 'image/png' }, body: new Blob([new Uint8Array([1, 2, 3])], { type: 'image/png' }) };
    };
    const fetcher = new TileFetcher(new TileStore(new MemoryBackend()), source, { get });
    const track = Track.fromDrive(synthDrive());
    const { container } = render(<MapView theme={DRACULA} fetcher={fetcher} attribution="© test contributors" track={track} pins={[]} fitKey="a" />);
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); await fetcher.idle(); });
    expect(calls.length).toBeGreaterThan(0);
    expect(calls.length).toBeLessThan(40); // the screen's tiles only
    expect(calls.every((u) => u.startsWith('https://tiles.test/'))).toBe(true);
    expect(most).toBeLessThanOrEqual(2);
    expect(container.querySelectorAll('img.leaflet-tile').length).toBe(0); // no direct <img> requests
    expect(container.querySelector('.hl-tile[data-state="fetched"]')).not.toBeNull();
    expect(container.textContent).toContain('© test contributors');
  });

  it('never fetches with downloads off', async () => {
    const calls: string[] = [];
    const fetcher = new TileFetcher(new TileStore(new MemoryBackend()), source, { get: async (u) => { calls.push(u); throw new Error('no'); }, online: false });
    const { container } = render(<MapView theme={DRACULA} fetcher={fetcher} attribution="" track={Track.fromDrive(synthDrive())} pins={[]} fitKey="b" />);
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); await fetcher.idle(); });
    expect(calls).toEqual([]);
    expect(container.querySelector('.hl-tile-missing')).not.toBeNull();
  });

  it('places pins as markers', async () => {
    const track = Track.fromDrive(synthDrive());
    const pins = [{ lat: track.lat[0]!, lon: track.lon[0]!, kind: 'start' as const }, { lat: track.lat[9]!, lon: track.lon[9]!, kind: 'stall' as const, title: 'stall' }];
    const { container } = render(<MapView theme={DRACULA} fetcher={null} attribution="" track={track} pins={pins} fitKey="c" />);
    await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
    expect(container.querySelectorAll('.hl-pin').length).toBe(2);
  });
});

describe('colour runs', () => {
  it('cuts the track where the colour changes, shares the joining fix, breaks at gaps', () => {
    const tr = new Track([0, 1, 2, 3, 4, 20, 21, 22], [40, 40.001, 40.002, 40.003, 40.004, 40.01, 40.011, 40.012], [-100, -100, -100, -100, -100, -100, -100, -100]);
    const values = [0, 0, 100, 100, NaN, 0, 0, 0];
    const runs = colourRuns(tr, values, 0, 100, false, '#fff');
    expect(runs[0]!.slice(0, 2)).toEqual([0, 2]);
    expect(runs[1]!.slice(0, 2)).toEqual([2, 4]);
    expect(runs.some((r) => r[2] === NODATA)).toBe(false); // a NaN only at a run's end keeps the run's colour
    expect(runs[runs.length - 1]!.slice(0, 2)).toEqual([5, 7]); // after the gap
    expect(runs.every(([a, b]) => !(a < 5 && b >= 5))).toBe(true);
    expect(colourRuns(tr, null, 0, 1, false, '#abc').every((r) => r[2] === '#abc')).toBe(true);
  });
});
