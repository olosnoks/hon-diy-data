/** F3 Events screen. */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import EventsScreen from './index';
import { useEventNotes } from './notes';
import { FixtureStore } from '../replay/fixture';
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
});
