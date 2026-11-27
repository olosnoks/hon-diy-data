import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.setConfig({ testTimeout: 20000 });
import BytesScreen from './index';
import { DemoMemLink, MemoryScanner } from './model/scanner';
import { TEST_MAP } from './model/testram';
import type { NotesStore } from './model/unknown';

afterEach(cleanup);

function memStore(): NotesStore {
  let data = {};
  return { load: () => JSON.parse(JSON.stringify(data)), save: (d) => { data = d; } };
}

function setup(extra: Record<string, unknown> = {}) {
  now = 0;
  const link = new DemoMemLink(() => now);
  const scanner = new MemoryScanner(link, () => now);
  current = scanner;
  const saved: string[] = [];
  const deps = { link, scanner, demo: true, live: null, mapText: TEST_MAP, notes: memStore(), saveMap: async (t: string) => { saved.push(t); }, ...extra };
  const utils = render(<BytesScreen deps={deps} />);
  return { ...utils, link, saved };
}

let current: MemoryScanner;
let now = 0;

/** Step the scanner by hand (the sweep loop would keep act() busy forever). */
async function sweep(times = 1, between?: () => void) {
  for (let i = 0; i < times; i++) {
    between?.();
    now += 1;
    await act(async () => { await current.sweepOnce(); });
  }
}

describe('F7 Bytes screen', () => {
  it('sweeps into the hex grid and inspects a byte bit by bit', async () => {
    const { container } = setup();
    expect(screen.getByTestId('scanner-status').textContent).toMatch(/sweep 0/);
    await sweep(3);
    expect(screen.getByTestId('scanner-status').textContent).not.toMatch(/sweep 0 /);
    const cell = container.querySelector('[data-addr="8"]')!;
    expect(cell.textContent).toBe('84');
    fireEvent.click(cell);
    expect(screen.getByTestId('inspector-hex').textContent).toBe('84');
    const bits = screen.getByLabelText('bits 7 to 0');
    expect(within(bits).getAllByText('1')).toHaveLength(2);       // 0x84 = b7 and b2
    expect(screen.getByText(/Address map: ac_sw · unverified guess/)).toBeTruthy();
  });

  it('moves the selection with the arrow keys', async () => {
    setup();
    const root = screen.getByTestId('bytes-screen');
    fireEvent.keyDown(root, { key: 'ArrowDown' });
    fireEvent.keyDown(root, { key: 'ArrowRight' });
    expect(screen.getByText('0x25')).toBeTruthy();
  });

  it('refuses non-read frames in the console unless Expert is on', async () => {
    setup();
    const tx = screen.getByLabelText('frame to send');
    fireEvent.change(tx, { target: { value: '21 05 14 01' } });
    await act(async () => { fireEvent.click(screen.getByTestId('send')); });
    expect(screen.getByTestId('console-rx').textContent).toMatch(/refused/);
    fireEvent.change(tx, { target: { value: '20 05 14 01' } });
    await act(async () => { fireEvent.click(screen.getByTestId('send')); });
    expect(screen.getByTestId('console-rx').textContent).toMatch(/checksum OK/);
  });

  it('lists the unknown bytes and keeps the owner\'s notes', () => {
    setup();
    fireEvent.click(screen.getByRole('tab', { name: 'Unknown bytes' }));
    fireEvent.click(screen.getByText('purge_duty?'));
    fireEvent.change(screen.getByLabelText('notes'), { target: { value: 'pinch the purge line next time' } });
    expect(screen.getByText(/pinch the purge line next time/, { selector: 'em' })).toBeTruthy();
    fireEvent.change(screen.getByLabelText('confidence filter'), { target: { value: 'high' } });
    expect(screen.queryByText('purge_duty?', { selector: 'td' })).toBeNull();
  });

  it('edits the map: mark, validate, bump, diff and save', async () => {
    const { saved } = setup();
    fireEvent.click(screen.getByRole('tab', { name: 'Address map' }));
    expect(screen.getByTestId('map-validation').textContent).toMatch(/OK/);
    fireEvent.click(screen.getByText('tps', { selector: 'td' }));
    fireEvent.click(screen.getByRole('button', { name: 'Mark suspect' }));
    expect(screen.getByText(/channel tps.status: "unverified" → "suspect"/)).toBeTruthy();
    expect(screen.getByText(/decoding unchanged/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText('formula'), { target: { value: 'x / 2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply to the text' }));
    expect(screen.getByText(/bump it, the version goes into every log/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Bump version' }));
    expect(screen.getByText(/test-2 · #/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save map' })); });
    expect(saved).toHaveLength(1);
    expect(saved[0]).toContain('version = "test-2"');
    fireEvent.click(screen.getByRole('button', { name: 'TOML' }));
    fireEvent.change(screen.getByLabelText('address map TOML'), { target: { value: 'version = [' } });
    expect(screen.getByTestId('map-validation').textContent).toMatch(/not valid TOML/);
  });

  it('ranks addresses against a shape and assigns one to a channel', async () => {
    const { link } = setup();
    await sweep(8, () => { link.state.ect += 3; });   // the thermistor byte runs down as it warms
    fireEvent.click(screen.getByRole('tab', { name: 'Rank' }));
    fireEvent.change(screen.getByLabelText('reference'), { target: { value: 'shape:fall' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Rank now/ })); });
    const first = screen.getAllByRole('row')[1]!;
    expect(first.textContent).toMatch(/^0x10 u8/);
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.change(screen.getByLabelText('assign to channel'), { target: { value: 'batt' } });
    fireEvent.click(screen.getByRole('button', { name: /Assign to channel/ }));
    expect(screen.getByText(/batt now reads 0x10 u8/)).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: 'Address map' }));
    expect(screen.getByText(/channel batt.addr: 23 → 16/)).toBeTruthy();
  });

  it('shows the verification scripts and the poke test steps', () => {
    setup();
    fireEvent.click(screen.getByRole('tab', { name: 'Verify' }));
    fireEvent.click(screen.getByText('Throttle sweep (engine off)'));
    expect(screen.getByRole('button', { name: 'Start' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    expect(screen.getByTestId('verify-phase').textContent).toBe('Foot off');
    fireEvent.click(screen.getByRole('tab', { name: 'Poke test' }));
    expect(screen.getByText('elec_load')).toBeTruthy();
    expect(screen.getByText(/Timed total/)).toBeTruthy();
  });
});
