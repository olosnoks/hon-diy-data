import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { DriveInfo } from '../../core/types';
import { DRACULA } from '../theme';
import { DrivePicker, deleteQuestion, matches, pickerRows } from './DrivePicker';
import { FixtureStore } from './fixture';

afterEach(cleanup);

const info = (name: string, kind: DriveInfo['kind'], startedUnix: number, durationS: number, reason?: string): DriveInfo =>
  ({ id: 0, name, kind, startedUnix, durationS, rows: 1, status: 'ok', mapVersion: '', ecuId: '', reason });

describe('picker rows (drivepicker.py)', () => {
  const drives = [
    info('drive_a', 'drive', 1000, 600),
    info('stall_a', 'stall', 1300, 30, 'rpm dropped to 0'),
    info('drive_b', 'drive', 5000, 2100),
    info('dropout_b', 'dropout', 5200, 25),
    info('orphan', 'capture', 99999, 10),
  ];
  it('newest drive first, each followed by its captures, orphans last', () => {
    const rows = pickerRows(drives);
    expect(rows.map((r) => [r.info.name, r.nested])).toEqual([
      ['drive_b', false], ['dropout_b', true], ['drive_a', false], ['stall_a', true], ['orphan', false],
    ]);
    expect(rows[2]!.stalls).toBe(1);
    expect(rows[2]!.captures).toBe(1);
  });
  it('search: every word must match; a drive matches what its captures say', () => {
    const rows = pickerRows(drives);
    expect(rows.filter((r) => matches(r, 'stall')).map((r) => r.info.name)).toEqual(['drive_a', 'stall_a']);
    expect(rows.filter((r) => matches(r, 'rpm dropped')).map((r) => r.info.name)).toEqual(['drive_a', 'stall_a']);
    expect(rows.filter((r) => matches(r, '')).length).toBe(5);
  });
  it('the delete question says exactly what goes', () => {
    const rows = pickerRows(drives);
    const a = deleteQuestion(rows.find((r) => r.info.name === 'drive_a')!);
    expect(a).toContain('Delete drive_a?');
    expect(a).toContain('1 capture, 1 of them stall');
    expect(a).toContain('its captures included');
    expect(a).toContain('cannot be undone');
    const c = deleteQuestion(rows.find((r) => r.info.name === 'stall_a')!);
    expect(c).toContain('Only the capture');
  });
});

describe('<DrivePicker>', () => {
  it('lists, searches, moves with the arrows and opens with Enter', async () => {
    const onOpen = vi.fn();
    render(<DrivePicker source={new FixtureStore()} theme={DRACULA} title="Open a drive" onOpen={onOpen} onClose={() => {}} />);
    const list = await screen.findByRole('listbox');
    await within(list).findByText('drive_2026_11_26_2251');
    const opts = within(list).getAllByRole('option');
    expect(opts.map((o) => o.getAttribute('data-name'))).toEqual(['drive_2026_11_26_2251', 'dropout_2026_11_26_2253', 'drive_2026_11_26_2224']);
    const search = screen.getByLabelText('Search drives');
    fireEvent.keyDown(search, { key: 'ArrowDown' });
    fireEvent.keyDown(search, { key: 'ArrowDown' });
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(onOpen).toHaveBeenCalledWith('drive_2026_11_26_2224');
    fireEvent.change(search, { target: { value: 'zzz' } });
    expect(screen.getByText('Nothing matches.')).toBeTruthy();
  });

  it('deletes after the question, and only then', async () => {
    const store = new FixtureStore();
    const del = vi.spyOn(store, 'deleteDrive');
    render(<DrivePicker source={store} theme={DRACULA} title="Open a drive" onOpen={() => {}} onClose={() => {}} />);
    await screen.findByText('dropout_2026_11_26_2253');
    fireEvent.click(screen.getByText('dropout_2026_11_26_2253'));
    fireEvent.click(screen.getByText('Delete…'));
    const ask = screen.getByRole('alertdialog');
    expect(ask.textContent).toContain('Only the capture');
    fireEvent.click(within(ask).getByText('Cancel'));
    expect(del).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Delete…'));
    await act(async () => { fireEvent.click(within(screen.getByRole('alertdialog')).getByText('Delete')); });
    expect(del).toHaveBeenCalledWith('dropout_2026_11_26_2253');
    expect(await screen.findByText('Deleted dropout_2026_11_26_2253.')).toBeTruthy();
    expect(screen.queryByRole('option', { name: /dropout_2026_11_26_2253/ })).toBeNull();
  });

  it('Keep marks a drive; deleting a keep drive asks twice and passes allowKeep', async () => {
    const { SqliteDriveStore } = await import('../../core/store/drivestore');
    const { MemoryFileStore } = await import('../../core/store/filestore');
    const { newDrive } = await import('../../core/store/writer');
    const { column } = await import('../../core/store/model');
    const store = await SqliteDriveStore.open(new MemoryFileStore());
    const name = newDrive(store.hdb, { started_unix: 1794812400.0 }, (d) => { d.addStream('s', [column('rpm')]); d.append('s', 0, [800]); d.append('s', 5, [810]); }).drive;
    const del = vi.spyOn(store, 'deleteDrive');
    render(<DrivePicker source={store} theme={DRACULA} title="Open a drive" onOpen={() => {}} onClose={() => {}} />);
    await screen.findByText(name);
    const keep = screen.getByRole('button', { name: 'Keep' });
    expect(keep.getAttribute('aria-pressed')).toBe('false');
    await act(async () => { fireEvent.click(keep); });
    expect(await screen.findByText(/is marked keep/)).toBeTruthy();
    expect((await store.listDrives())[0]!.keep).toBe(true);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Keep' }).getAttribute('aria-pressed')).toBe('true'));
    expect(screen.getByRole('option').textContent).toContain('keep');
    fireEvent.click(screen.getByText('Delete…'));
    fireEvent.click(within(screen.getByRole('alertdialog')).getByText('Delete'));
    const second = screen.getByRole('alertdialog', { name: 'Marked keep' });
    expect(second.textContent).toContain('is marked keep');
    fireEvent.click(within(second).getByText('Cancel'));
    expect(del).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Delete…'));
    fireEvent.click(within(screen.getByRole('alertdialog')).getByText('Delete'));
    await act(async () => { fireEvent.click(within(screen.getByRole('alertdialog', { name: 'Marked keep' })).getByText('Delete')); });
    expect(del).toHaveBeenCalledWith(name, { allowKeep: true });
    expect(await screen.findByText(`Deleted ${name}.`)).toBeTruthy();
    expect(await store.listDrives()).toEqual([]);
  });

  it('Import file… takes a CSV log as a new drive and chooses it', async () => {
    const { SqliteDriveStore } = await import('../../core/store/drivestore');
    const { MemoryFileStore } = await import('../../core/store/filestore');
    const store = await SqliteDriveStore.open(new MemoryFileStore());
    render(<DrivePicker source={store} theme={DRACULA} title="Open a drive" onOpen={() => {}} onClose={() => {}} />);
    expect(await screen.findByText(/or a CSV, MSL or MLG log/)).toBeTruthy();
    const csv = 'time_s,rpm,map\n0.0,800,30\n0.1,820,31\n0.2,850,32\n';
    const f = new File([csv], 'old_log.csv', { type: 'text/csv' });
    Object.defineProperty(f, 'arrayBuffer', { value: async () => new TextEncoder().encode(csv).buffer });   // jsdom's File has none
    await act(async () => { fireEvent.change(screen.getByLabelText('Import file'), { target: { files: [f] } }); });
    const msg = await screen.findByText(/^Imported old_log\.csv as /);
    const name = msg.textContent!.replace(/^Imported old_log\.csv as /, '').replace(/\.$/, '');
    expect((await store.listDrives()).map((d) => d.name)).toEqual([name]);
    await waitFor(() => expect(screen.getByRole('option', { selected: true }).getAttribute('data-name')).toBe(name));
    const d = await store.loadDrive(name);
    expect(Array.from(d.series.rpm!.v)).toEqual([800, 820, 850]);
  });

  it('says so when there is nothing recorded', async () => {
    const empty = { listDrives: async () => [], loadDrive: async () => { throw new Error('none'); } };
    render(<DrivePicker source={empty} theme={DRACULA} title="Open a drive" onOpen={() => {}} onClose={() => {}} />);
    expect(await screen.findByText(/No drives recorded yet/)).toBeTruthy();
  });

  it('Esc closes', async () => {
    const onClose = vi.fn();
    render(<DrivePicker source={new FixtureStore()} theme={DRACULA} title="Open a drive" onOpen={() => {}} onClose={onClose} />);
    fireEvent.keyDown(screen.getByLabelText('Search drives'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});
