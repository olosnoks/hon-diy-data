/** F3 Events screen. */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import EventsScreen from './index';
import { harshKindAt, LOCAL_KEY, migrateLocalNotes, useEventNotes } from './notes';
import { eventsOf, harshWords } from './detect';
import { FixtureStore, fixtureDrives } from '../replay/fixture';
import { demoDrive } from '../replay/demodrive';
import type { EventNote, LoadedDrive } from '../../core/types';
import { eventKey } from '../../core/store/eventnotes';
import { setDriveSource } from '../replay/source';
import { resetSession, useSession } from '../../core/session';

beforeEach(() => {
  resetSession();
  localStorage.clear();
  useEventNotes.setState({ verdicts: {}, notes: {} });
  useSession.setState({ replayRequest: null, gpsFocus: null, powerFocus: null, replayDrive: null });
  setDriveSource(new FixtureStore(), true);
});
afterEach(cleanup);

async function open() {
  render(<EventsScreen />);
  const list = await screen.findByRole('listbox', { name: 'Events' });
  await within(list).findAllByRole('option');
  return list;
}
const rows = (list: HTMLElement) => within(list).getAllByRole('option');
const nearRow = (list: HTMLElement, rpm: RegExp) => rows(list).find((r) => r.getAttribute('data-event') === 'near_stall' && rpm.test(r.textContent!))!;

describe('F3 Events', () => {
  it("lists the day's events from both drives, newest first, with filters", async () => {
    const list = await open();
    expect(screen.getByTestId('summary').textContent).toMatch(/2 drives · \d+ min/);
    const all = rows(list);
    expect(all.length).toBeGreaterThanOrEqual(12);
    expect(all[0]!.getAttribute('data-event')).toBe('key_off');            // drive B ends with a key-off
    expect(all[0]!.getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('button', { name: `All ${all.length}` })).toBeTruthy();
    const nearChip = screen.getByRole('button', { name: 'Near-stall 3' });
    fireEvent.click(nearChip);
    expect(rows(list).every((r) => r.getAttribute('data-event') === 'near_stall')).toBe(true);
    expect(rows(list)).toHaveLength(3);
    fireEvent.click(nearChip);                                              // again: all
    expect(rows(list)).toHaveLength(all.length);
    expect(screen.getByRole('button', { name: 'Engine died 0' })).toBeTruthy();
  });

  it('the selected near-stall: verdict, why, timeline', async () => {
    const list = await open();
    fireEvent.click(nearRow(list, /44\d rpm/));
    expect(screen.getByTestId('title').textContent).toMatch(/^Near-stall · 44\d rpm$/);
    expect(screen.getByTestId('verdict').textContent).toBe('closest call of the night');
    expect(screen.getByTestId('why').textContent).toContain('Throttle blipped');
    expect(screen.getByTestId('tl-rpm').getAttribute('points')!.split(' ').length).toBeGreaterThan(50);
    expect(screen.getByTestId('tl-tps').getAttribute('points')).not.toBe('');
  });

  it('Open in Replay and Show on GPS map hand the moment over', async () => {
    const list = await open();
    fireEvent.click(nearRow(list, /44\d rpm/));
    fireEvent.click(screen.getByText('Open in Replay (F4)'));
    const req = useSession.getState().replayRequest!;
    expect(req.drive).toBe('drive_2026_11_26_2251');
    expect(req.t).toBeGreaterThan(228);
    expect(req.t).toBeLessThan(231);
    expect(useSession.getState().screen).toBe('replay');
    fireEvent.click(screen.getByText('Show on GPS map'));
    expect(useSession.getState().gpsFocus!.t).toBeCloseTo(req.t);
    expect(useSession.getState().screen).toBe('gps');
  });

  it('a note and a verdict of your own, kept on the device', async () => {
    const list = await open();
    const row = nearRow(list, /5[34]\d rpm/);
    fireEvent.click(row);
    fireEvent.click(screen.getByText('Add a note'));
    fireEvent.change(screen.getByLabelText('Note'), { target: { value: 'clutch bite, my fault' } });
    fireEvent.click(screen.getByText('Save'));
    expect(screen.getByTestId('note').textContent).toContain('clutch bite, my fault');
    expect(localStorage.getItem('hondalog.events.notes')).toContain('clutch bite, my fault');
    expect(row.textContent).toContain('✎ clutch bite, my fault');

    fireEvent.click(screen.getByText('Change verdict'));
    fireEvent.click(within(screen.getByRole('menu')).getByText('driver'));
    expect(screen.getByTestId('verdict').textContent).toBe('driver (yours)');
    fireEvent.click(screen.getByText('Change verdict'));
    fireEvent.click(within(screen.getByRole('menu')).getByText(/^automatic:/));
    expect(screen.getByTestId('verdict').textContent).not.toContain('(yours)');
  });

  it('with a drive store: notes and verdicts go into its events table (desktop keys) and come back', async () => {
    const rowsDb = new Map<string, { note: string; verdict: string; kind: string; t: number | null }>();
    const calls: string[] = [];
    class NotesStore extends FixtureStore {
      async loadEventNotes(drive: string) {
        const out: Record<string, { note: string; verdict: string; kind: string; t: number | null }> = {};
        for (const [k, v] of rowsDb) if (k.startsWith(drive + '|')) out[k.slice(drive.length + 1)] = v;
        return out;
      }
      async saveEventNote(drive: string, key: string, c: { note?: string; verdict?: string; kind?: string; t?: number }) {
        calls.push(`${drive}|${key}`);
        const old = rowsDb.get(`${drive}|${key}`) ?? { note: '', verdict: '', kind: c.kind ?? '', t: c.t ?? null };
        rowsDb.set(`${drive}|${key}`, { ...old, ...(c.note !== undefined ? { note: c.note } : {}), ...(c.verdict !== undefined ? { verdict: c.verdict } : {}) });
      }
    }
    setDriveSource(new NotesStore(), false);
    const list = await open();
    fireEvent.click(nearRow(list, /5[34]\d rpm/));
    fireEvent.click(screen.getByText('Add a note'));
    fireEvent.change(screen.getByLabelText('Note'), { target: { value: 'clutch bite, my fault' } });
    fireEvent.click(screen.getByText('Save'));
    fireEvent.click(screen.getByText('Change verdict'));
    fireEvent.click(within(screen.getByRole('menu')).getByText('driver'));
    await act(async () => { await Promise.resolve(); });
    expect(localStorage.getItem('hondalog.events.notes')).toBeNull();          // nothing on the device
    expect(calls.length).toBe(2);
    expect(calls[0]).toMatch(/^drive_2026_11_26_\d{4}\|near_stall@\d+\.\d$/);
    const stored = rowsDb.get(calls[0]!)!;
    expect(stored).toMatchObject({ note: 'clutch bite, my fault', verdict: 'driver', kind: 'near_stall' });
    // a fresh screen reads them back from the store
    cleanup();
    useEventNotes.setState({ verdicts: {}, notes: {} });
    const again = await open();
    await act(async () => { await Promise.resolve(); });
    const row = nearRow(again, /5[34]\d rpm/);
    expect(row.textContent).toContain('✎ clutch bite, my fault');
    fireEvent.click(row);
    expect(screen.getByTestId('verdict').textContent).toBe('driver (yours)');
  });

  it('device notes move into the drive database once', async () => {
    const { SqliteDriveStore } = await import('../../core/store/drivestore');
    const { MemoryFileStore } = await import('../../core/store/filestore');
    const { newDrive } = await import('../../core/store/writer');
    const { column } = await import('../../core/store/model');
    const { migrateLocalNotes, LOCAL_KEY } = await import('./notes');
    const store = await SqliteDriveStore.open(new MemoryFileStore());
    const drive = newDrive(store.hdb, { started_unix: 1794812400.0 }, (d) => { d.addStream('s', [column('rpm')]); d.append('s', 0, [800]); }).drive;
    localStorage.setItem(LOCAL_KEY, JSON.stringify({
      verdicts: { [`${drive}@near_stall@229.46`]: 'driver', 'drive_gone@stall@1.00': 'engine died' },
      notes: { [`${drive}@near_stall@229.46`]: 'clutch bite', [`${drive}@mark@12.00`]: 'pothole' },
    }));
    expect(await migrateLocalNotes(store)).toBe(2);
    const notes = await store.loadEventNotes(drive);
    expect(notes['near_stall@229.5']).toMatchObject({ note: 'clutch bite', verdict: 'driver', kind: 'near_stall' });
    expect(notes['mark@12.0']).toMatchObject({ note: 'pothole', verdict: '' });
    // the drive that is not in the store stays on the device; nothing moves twice
    expect(JSON.parse(localStorage.getItem(LOCAL_KEY)!)).toEqual({ verdicts: { 'drive_gone@stall@1.00': 'engine died' }, notes: {} });
    expect(await migrateLocalNotes(store)).toBe(0);
    expect(Object.keys(await store.loadEventNotes(drive))).toHaveLength(2);
  });

  it('↑ ↓ move through the list, Enter opens in Replay', async () => {
    const list = await open();
    act(() => { fireEvent.keyDown(window, { key: 'ArrowDown' }); });
    expect(rows(list)[1]!.getAttribute('aria-selected')).toBe('true');
    act(() => { fireEvent.keyDown(window, { key: 'ArrowUp' }); fireEvent.keyDown(window, { key: 'ArrowUp' }); });
    expect(rows(list)[0]!.getAttribute('aria-selected')).toBe('true');
    act(() => { fireEvent.keyDown(window, { key: 'Enter' }); });
    expect(useSession.getState().replayRequest!.drive).toBe('drive_2026_11_26_2251');
  });

  it('says so when nothing is recorded', async () => {
    setDriveSource({ listDrives: async () => [], loadDrive: async () => { throw new Error('none'); } });
    render(<EventsScreen />);
    expect(await screen.findAllByText('No drives recorded yet.')).not.toHaveLength(0);
  });

  it('harsh events read as the desktop words them', () => {
    expect(harshWords('harsh_brake', 0.38, 55.2, 21.4)).toMatchObject({ title: 'Harsh brake · 0.38 g', text: '0.38 g · 55→21 km/h' });
    expect(harshWords('harsh_accel', 0.31, 4, 38).why).toBe('Acceleration of 0.31 g (3.0 m/s², from vss over 1.5 s) between 4 and 38 km/h.');
    const brakes = eventsOf(fixtureDrives()[0]!).filter((e) => e.kind === 'harsh_brake');
    expect(brakes.length).toBeGreaterThan(0);
    for (const e of brakes) expect(e.text).toMatch(/^\d\.\d\d g · \d+→\d+ km\/h$/);
  });

  it('◀ ▶ and the dropdown pick the day (PgUp / PgDn too)', async () => {
    const older: LoadedDrive = { ...demoDrive(), info: { ...demoDrive().info, name: 'drive_2026_11_25_2300', startedUnix: new Date(2026, 10, 25, 23, 0, 0).getTime() / 1000 } };
    const store = new FixtureStore();
    setDriveSource({
      listDrives: async () => [...await store.listDrives(), older.info],
      loadDrive: async (n: string) => (n === older.info.name ? older : store.loadDrive(n)),
    });
    await open();
    const daySel = screen.getByRole('combobox', { name: 'Day' }) as HTMLSelectElement;
    expect(daySel.value).toBe('2026-11-26');
    expect((screen.getByRole('button', { name: 'Next day' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Previous day' }));
    await screen.findByText(/1 drive · \d+ min/);
    expect(daySel.value).toBe('2026-11-25');
    expect((screen.getByRole('button', { name: 'Previous day' }) as HTMLButtonElement).disabled).toBe(true);
    act(() => { fireEvent.keyDown(window, { key: 'PageDown' }); });
    await screen.findByText(/2 drives · \d+ min/);
    expect(daySel.value).toBe('2026-11-26');
  });

  it("old 'harsh' notes come back under harsh_accel / harsh_brake (device and database)", async () => {
    const d = fixtureDrives()[0]!;
    const brake = eventsOf(d).find((e) => e.kind === 'harsh_brake')!;
    expect(harshKindAt(d, brake.t)).toBe('harsh_brake');
    // on the device (the demo drives): the old id moves to the new one
    localStorage.setItem(LOCAL_KEY, JSON.stringify({ verdicts: {}, notes: { [`${d.info.name}@harsh@${brake.t.toFixed(2)}`]: 'late on the brakes' } }));
    await useEventNotes.getState().load(null, [brake]);
    expect(useEventNotes.getState().notes[brake.id]).toBe('late on the brakes');
    expect(JSON.parse(localStorage.getItem(LOCAL_KEY)!).notes).toEqual({ [brake.id]: 'late on the brakes' });
    // in the database under the old key: found, rewritten under the new kind, the old row cleared
    const rows: Record<string, EventNote> = { [`harsh@${brake.t.toFixed(1)}`]: { note: 'db note', verdict: 'driver', kind: 'harsh', t: brake.t } };
    const writes: [string, Record<string, unknown>][] = [];
    const src = {
      loadEventNotes: async () => ({ ...rows }),
      saveEventNote: async (_d: string, key: string, change: Record<string, unknown>) => { writes.push([key, change]); },
    };
    await useEventNotes.getState().load(src, [brake]);
    expect(useEventNotes.getState().verdicts[brake.id]).toBe('driver');
    expect(writes.map(([k]) => k)).toEqual([`harsh_brake@${brake.t.toFixed(1)}`, `harsh@${brake.t.toFixed(1)}`]);
    expect(writes[1]![1]).toEqual({ note: '', verdict: '' });
  });

  it("start-up migration files old 'harsh' notes by the sign of the speed change", async () => {
    const d = fixtureDrives()[0]!;
    const brake = eventsOf(d).find((e) => e.kind === 'harsh_brake')!;
    localStorage.setItem(LOCAL_KEY, JSON.stringify({ verdicts: { [`${d.info.name}@harsh@${brake.t.toFixed(2)}`]: 'driver' }, notes: {} }));
    const saved: [string, string, Record<string, unknown>][] = [];
    const store = { loadEventNotes: async () => ({}), saveEventNote: async (dr: string, k: string, c: Record<string, unknown>) => { saved.push([dr, k, c]); } };
    expect(await migrateLocalNotes(store, localStorage, async (_dr, t) => harshKindAt(d, t))).toBe(1);
    expect(saved[0]![1]).toBe(eventKey('harsh_brake', Number(brake.t.toFixed(2))));
    expect(saved[0]![2]).toMatchObject({ verdict: 'driver', kind: 'harsh_brake' });
    // without a way to tell, it stays on the device
    localStorage.setItem(LOCAL_KEY, JSON.stringify({ verdicts: { 'x@harsh@1.00': 'driver' }, notes: {} }));
    expect(await migrateLocalNotes(store, localStorage)).toBe(0);
    expect(localStorage.getItem(LOCAL_KEY)).toContain('x@harsh@1.00');
  });
});
