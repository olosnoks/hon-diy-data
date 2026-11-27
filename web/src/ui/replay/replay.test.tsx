/** F4 Replay screen (port of the screen half of tests/test_ui_replay.py). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const plots = vi.hoisted(() => ({ made: 0, data: [] as unknown[] }));
vi.mock('uplot', () => ({
  default: class {
    static paths = {};
    constructor(_o: unknown, data: unknown) { plots.made++; plots.data.push(data); }
    setData(d: unknown) { plots.data.push(d); }
    setScale() {}
    batch(f: () => void) { f(); }
    destroy() {}
  },
}));
// Leaflet and the tile cache are F6's; here the map is a stand-in that shows what it was given
vi.mock('../gps/MapView', () => ({
  COLOUR_BINS: 24,
  default: (p: { pins: unknown[]; cursor: { lat: number } | null; values: ArrayLike<number> | null }) => (
    <div data-testid="map" data-pins={p.pins.length} data-cursor={p.cursor ? p.cursor.lat.toFixed(5) : ''} data-values={p.values ? p.values.length : 0} />
  ),
}));

import ReplayScreen from './index';
import { FixtureStore, SCRIPT_A, SCRIPT_B } from './fixture';
import { setDriveSource } from './source';
import { openInReplay } from '../../core/session';
import { resetSession, useSession } from '../../core/session';
import { clock } from './fmt';

const B0 = SCRIPT_B.started.getTime() / 1000;
const A0 = SCRIPT_A.started.getTime() / 1000;

let frames: FrameRequestCallback[] = [];
beforeEach(() => {
  resetSession();
  useSession.setState({ replayRequest: null, gpsFocus: null, powerFocus: null, replayDrive: null });
  setDriveSource(new FixtureStore(), true);
  plots.made = 0;
  plots.data = [];
  frames = [];
  vi.stubGlobal('requestAnimationFrame', (f: FrameRequestCallback) => { frames.push(f); return frames.length; });
  vi.stubGlobal('cancelAnimationFrame', () => {});
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const key = (k: string, extra: Partial<KeyboardEventInit> = {}) => act(() => { fireEvent.keyDown(window, { key: k, ...extra }); });
const clockText = () => screen.getByTestId('clock').textContent!;
/** seconds after midnight on the clock ("A " prefix allowed) */
const near = (got: number, want: number) => expect(Math.abs(got - want)).toBeLessThanOrEqual(0.1001);
const secs = (text: string) => { const [h, m, s] = text.replace('A ', '').split(':').map(Number); return h! * 3600 + m! * 60 + s!; };

async function open() {
  render(<ReplayScreen />);
  await screen.findByTestId('drive-name');
  // the drive arrives outside act (a resolved load): its lanes make their uPlots in passive
  // effects, which may run after the name is in the DOM. Wait for them, not for the clock.
  await waitFor(() => expect(document.querySelectorAll('[data-lane-plot]').length).toBeGreaterThan(0));
  await act(async () => { await Promise.resolve(); });
}

describe('F4 Replay', () => {
  it('opens the newest drive with everything on one cursor', async () => {
    await open();
    expect(screen.getByTestId('drive-name').textContent).toBe('drive_2026_11_26_2251');
    expect(clockText()).toBe(clock(B0, 0) + '.0');
    // the scrubber has a tick per event; near-stalls are there
    const ticks = screen.getByTestId('scrubber').querySelectorAll('[data-tick]');
    expect(ticks.length).toBeGreaterThan(8);
    expect([...ticks].filter((t) => t.getAttribute('data-tick') === 'near_stall')).toHaveLength(2);
    // the default lanes, drawn with uPlot
    for (const id of ['rpm', 'vss', 'tps', 'map', 'lat_g']) expect(document.querySelector(`[data-lane="${id}"]`)).toBeTruthy();
    await waitFor(() => expect(plots.made).toBeGreaterThanOrEqual(5));
    // eight calculated readouts, sensors grouped like Live
    expect(screen.getByTestId('calc').querySelectorAll('[data-calc]')).toHaveLength(8);
    for (const g of ['engine', 'idle', 'fuel', 'ignition', 'electrical', 'switches', 'cyd']) expect(document.querySelector(`[data-group="${g}"]`), g).toBeTruthy();
    // the map got the track, the pins and the position
    const map = screen.getByTestId('map');
    expect(Number(map.getAttribute('data-values'))).toBeGreaterThan(100);
    expect(Number(map.getAttribute('data-pins'))).toBeGreaterThan(3);
    expect(map.getAttribute('data-cursor')).not.toBe('');
  });

  it('a remembered drive the store does not have (the demo drive before the store opened) falls back to the newest', async () => {
    useSession.setState({ replayDrive: 'drive_from_the_demo' });
    await open();
    expect(screen.getByTestId('drive-name').textContent).toBe('drive_2026_11_26_2251');
  });

  it('← → step 0.1 s, Shift 1 s; [ ] walk the events; Home / End', async () => {
    await open();
    const t0 = secs(clockText());
    await key('ArrowRight'); await key('ArrowRight'); await key('ArrowRight');
    near(secs(clockText()) - t0, 0.3);
    await key('ArrowRight', { shiftKey: true });
    near(secs(clockText()) - t0, 1.3);
    await key('ArrowLeft');
    near(secs(clockText()) - t0, 1.2);
    await key(']');                                  // the first near-stall, ~21.5 s in
    const first = secs(clockText()) - secs(clock(B0));
    expect(first).toBeGreaterThan(20);
    expect(first).toBeLessThan(23);
    await key(']');
    expect(secs(clockText()) - secs(clock(B0))).toBeGreaterThan(first);
    await key('[');
    expect(secs(clockText()) - secs(clock(B0))).toBeCloseTo(first, 0);
    await key('End');
    expect(secs(clockText()) - secs(clock(B0))).toBeGreaterThan(250);
    await key('Home');
    expect(clockText()).toBe(clock(B0, 0) + '.0');
  });

  it('D drops cursor B; the readouts show A − B', async () => {
    await open();
    expect(screen.queryByTestId('delta')).toBeNull();
    await key('d');
    expect(screen.getByTestId('delta').textContent).toBe('A−B 0.00 s');
    await key('ArrowRight', { shiftKey: true });
    expect(screen.getByTestId('delta').textContent).toBe('A−B +1.00 s');
    expect(clockText().startsWith('A ')).toBe(true);
    expect(screen.getByTestId('cursor-b')).toBeTruthy();
    await key('d');
    expect(screen.queryByTestId('delta')).toBeNull();
  });

  it('a sensor click adds its lane; × removes it', async () => {
    await open();
    fireEvent.click(document.querySelector('[data-sensor="batt"]')!);
    expect(document.querySelector('[data-lane="batt"]')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Remove lane Battery'));
    expect(document.querySelector('[data-lane="batt"]')).toBeNull();
    fireEvent.click(screen.getByText('Gear').closest('button')!);      // a calculated readout too
    expect(document.querySelector('[data-lane="gear"]')).toBeTruthy();
  });

  it('plays at 0.5×, 1× and 4×, and stops at the end', async () => {
    await open();
    const t0 = secs(clockText());
    fireEvent.click(screen.getByLabelText('Play'));
    expect(screen.getByLabelText('Pause')).toBeTruthy();
    const run = (ms: number) => act(() => { const f = frames.pop()!; frames = []; f(ms); });
    await run(1000); await run(2000);                // 1 s of wall time at 1×
    expect(secs(clockText()) - t0).toBeCloseTo(1, 1);
    fireEvent.click(screen.getByText('4×'));
    await run(0); await run(1000);                   // 1 s at 4×
    expect(secs(clockText()) - t0).toBeCloseTo(5, 1);
    fireEvent.click(screen.getByText('0.5×'));
    await run(0); await run(2000);
    expect(secs(clockText()) - t0).toBeCloseTo(6, 1);
    await run(3000); await run(1_000_000);           // past the end: stops there
    expect(screen.getByLabelText('Play')).toBeTruthy();
    expect(secs(clockText()) - secs(clock(B0))).toBeGreaterThan(250);
  });

  it('lays a second drive over this one (C), slides it (. ,), removes it (Shift+C)', async () => {
    await open();
    await key('c');
    const dialog = await screen.findByRole('dialog');
    const other = await within(dialog).findByText('drive_2026_11_26_2224');
    expect(within(dialog).queryByText('drive_2026_11_26_2251')).toBeNull();     // not itself
    await act(async () => { fireEvent.doubleClick(other); });
    const note = await screen.findByTestId('overlay-note');
    expect(note.textContent).toMatch(/^drive_2026_11_26_2224 [−+]?0\.0\d s on start$/);
    await key('.'); await key('.');
    expect(screen.getByTestId('overlay-note').textContent).toMatch(/\+0\.[12]\d s on start/);
    await key('a');                                  // next alignment: the first marker (A has none -> start)
    expect(screen.getByText(/lined up on the first sample/)).toBeTruthy();
    await key('C');
    expect(screen.queryByTestId('overlay-note')).toBeNull();
  });

  it('O opens the drive picker; Esc closes it; opening a drive replaces this one', async () => {
    await open();
    await key('o');
    const dialog = await screen.findByRole('dialog');
    fireEvent.keyDown(within(dialog).getByLabelText('Search drives'), { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    await key('o');
    const d2 = await screen.findByRole('dialog');
    await act(async () => { fireEvent.doubleClick(await within(d2).findByText('drive_2026_11_26_2224')); });
    expect((await screen.findByText('drive_2026_11_26_2224', { selector: '[data-testid="drive-name"]' })).textContent).toBe('drive_2026_11_26_2224');
  });

  it('opens at the moment Events asked for', async () => {
    await open();
    await act(async () => { openInReplay('drive_2026_11_26_2224', 47.1); });
    await screen.findByText('drive_2026_11_26_2224', { selector: '[data-testid="drive-name"]' });
    expect(secs(clockText())).toBeCloseTo(secs(clock(A0)) + 47.1, 0);
    expect(useSession.getState().screen).toBe('replay');
  });
});
