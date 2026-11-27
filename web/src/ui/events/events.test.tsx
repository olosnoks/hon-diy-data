/** F3 Events screen. */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import EventsScreen from './index';
import { useEventNotes } from './notes';
import { FixtureStore } from '../replay/fixture';
import { setDriveSource } from '../replay/source';
import { useReplayNav } from '../replay/nav';
import { resetSession, useSession } from '../../core/session';

beforeEach(() => {
  resetSession();
  localStorage.clear();
  useEventNotes.setState({ verdicts: {}, notes: {} });
  useReplayNav.setState({ replayRequest: null, gpsFocus: null, powerFocus: null, replayDrive: null });
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
    const req = useReplayNav.getState().replayRequest!;
    expect(req.drive).toBe('drive_2026_11_26_2251');
    expect(req.t).toBeGreaterThan(228);
    expect(req.t).toBeLessThan(231);
    expect(useSession.getState().screen).toBe('replay');
    fireEvent.click(screen.getByText('Show on GPS map'));
    expect(useReplayNav.getState().gpsFocus!.t).toBeCloseTo(req.t);
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

  it('↑ ↓ move through the list, Enter opens in Replay', async () => {
    const list = await open();
    act(() => { fireEvent.keyDown(window, { key: 'ArrowDown' }); });
    expect(rows(list)[1]!.getAttribute('aria-selected')).toBe('true');
    act(() => { fireEvent.keyDown(window, { key: 'ArrowUp' }); fireEvent.keyDown(window, { key: 'ArrowUp' }); });
    expect(rows(list)[0]!.getAttribute('aria-selected')).toBe('true');
    act(() => { fireEvent.keyDown(window, { key: 'Enter' }); });
    expect(useReplayNav.getState().replayRequest!.drive).toBe('drive_2026_11_26_2251');
  });

  it('says so when nothing is recorded', async () => {
    setDriveSource({ listDrives: async () => [], loadDrive: async () => { throw new Error('none'); } });
    render(<EventsScreen />);
    expect(await screen.findAllByText('No drives recorded yet.')).not.toHaveLength(0);
  });
});
