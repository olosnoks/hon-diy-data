import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { AlarmEngine, AlarmRule } from '../../core/alarms';
import { resetSession, useSession } from '../../core/session';
import { TEST_MAP } from '../bytes/model/testram';
import { alarmSourceFromEngine } from './AlarmsTab';
import type { CydClient, CydState, Progress } from './cyd/client';
import { DEFAULT_CFG } from './cyd/helpers';
import SetupScreen from './index';

afterEach(cleanup);
beforeEach(() => { resetSession(); localStorage.clear(); });

const MAP_WITH_ALARM = `${TEST_MAP}
[[alarm]]
name = "batt_low"
channel = "batt"
low = 11.5
hold_s = 2
severity = "critical"
latching = true
`;

/** A handheld with an SD card in memory (tests/hhfake.py in spirit). */
class FakeCyd implements CydClient {
  sd = new Map<string, Uint8Array>([['/HANDHELD.CFG', new TextEncoder().encode(DEFAULT_CFG)], ['/DRIVES/D000001.HHD', new Uint8Array(3000)]]);
  sent: string[] = [];
  pushed = '';
  console: ((l: string) => void)[] = [];
  state: ((s: CydState, d: string) => void)[] = [];
  async ports() { return [{ id: 'cyd:COM23', label: 'COM23 CH340 1a86:7523', guess: 'cyd' as const }, { id: 'COM21', label: 'COM21 FTDI', guess: 'ecu' as const }]; }
  async connect() { return { id: 'CYD-1', fw: '0.9.1', variant: 'default', map: '071de5244d2b' }; }
  async disconnect() { /* nothing */ }
  onConsole(fn: (l: string) => void) { this.console.push(fn); return () => undefined; }
  onState(fn: (s: CydState, d: string) => void) { this.state.push(fn); return () => undefined; }
  sendLine(text: string) { this.sent.push(text); this.console.forEach((f) => f(`< HL OK ${text}`)); }
  async stat() { return { link: { state: 2, name: 'K-line', hz: 11.2, reads: 10, errors: 0 }, sd: { ok: 1, free_mb: 7000 }, gps: { fix: 0, sats: 3 } }; }
  async ls(path: string): Promise<[Array<[string, number, number]>, string[]]> {
    const files: Array<[string, number, number]> = [];
    const dirs = new Set<string>();
    for (const [p, d] of this.sd) {
      const rest = p.slice(path === '/' ? 1 : path.length + 1);
      if (!p.startsWith(path === '/' ? '/' : `${path}/`)) continue;
      if (rest.includes('/')) dirs.add((path === '/' ? '' : path) + '/' + rest.split('/')[0]);
      else files.push([p, d.length, 1700000000]);
    }
    return [files, [...dirs]];
  }
  async get(path: string, progress?: Progress) { const d = this.sd.get(path)!; progress?.(d.length, d.length); return d; }
  async put(path: string, data: Uint8Array, progress?: Progress) { progress?.(data.length, data.length); this.sd.set(path, data); return 'crc ok'; }
  async rm(path: string) { this.sd.delete(path); }
  async cfgGet(progress?: Progress) { return this.get('/HANDHELD.CFG', progress); }
  async cfgPut(data: Uint8Array, progress?: Progress) { return this.put('/HANDHELD.CFG', data, progress); }
  async mark() { /* nothing */ }
  async setTime() { return 'ok'; }
  async reboot() { /* nothing */ }
  async pushAddressMap(text: string, progress?: Progress) { this.pushed = text; progress?.(100, 100); return 'map #test'; }
  async putMapFiles() { return []; }
  async flash() { /* nothing */ }
  cancel() { /* nothing */ }
}

async function connect() {
  fireEvent.click(screen.getByRole('tab', { name: 'CYD black box' }));
  await act(async () => { await Promise.resolve(); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Connect' })); });
  await waitFor(() => expect(screen.getByTestId('cyd-state').textContent).toMatch(/^Connected/));
}

describe('F8 Setup screen', () => {
  it('shows the map\'s alarm rules and live states, and acknowledges', async () => {
    let now = 0;
    const engine = new AlarmEngine([new AlarmRule({ name: 'batt_low', channel: 'batt', low: 11.5, severity: 'critical', latching: true })], ['batt']);
    const source = alarmSourceFromEngine(engine, () => now);
    render(<SetupScreen deps={{ alarms: source, mapText: MAP_WITH_ALARM, confirm: () => true }} />);
    expect(screen.getByText('batt < 11.5 for 2 s')).toBeTruthy();
    act(() => { engine.update({ batt: 10.9 }, 1); now = 1; });
    expect(screen.getByTestId('alarm-active').textContent).toMatch(/ALARM/);
    act(() => { engine.update({ batt: 12.6 }, 2); now = 2; });
    expect(screen.getByTestId('alarm-active')).toBeTruthy();      // latched: stays until acknowledged
    expect(engine.status('batt_low').state).toBe('latched');
    fireEvent.click(screen.getByRole('button', { name: /Acknowledge/ }));
    expect(screen.queryByTestId('alarm-active')).toBeNull();
    expect(screen.getAllByText(/RAISED|CLEARED|ACKNOWLEDGED/).length).toBeGreaterThanOrEqual(2);
  });

  it('connects the CYD, browses, downloads and deletes on the SD card', async () => {
    const cyd = new FakeCyd();
    render(<SetupScreen deps={{ cyd, mapText: TEST_MAP, confirm: () => true }} />);
    await connect();
    await waitFor(() => expect(screen.getByTestId('stat-link').textContent).toBe('OK  K-line'));
    fireEvent.click(screen.getByRole('button', { name: 'SD card' }));
    await waitFor(() => expect(screen.getByText('DRIVES/')).toBeTruthy());
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'open DRIVES/' })); });
    await waitFor(() => expect(screen.getByText('D000001.HHD')).toBeTruthy());
    fireEvent.click(screen.getByText('D000001.HHD'));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete' })); });
    expect(cyd.sd.has('/DRIVES/D000001.HHD')).toBe(false);
  });

  it('edits HANDHELD.CFG as a form and writes it', async () => {
    const cyd = new FakeCyd();
    render(<SetupScreen deps={{ cyd, mapText: TEST_MAP, confirm: () => true }} />);
    await connect();
    fireEvent.click(screen.getByRole('button', { name: 'Config' }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Read from handheld' })); });
    fireEvent.change(screen.getByLabelText('rotate'), { target: { value: '180' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Write to handheld' })); });
    expect(new TextDecoder().decode(cyd.sd.get('/HANDHELD.CFG'))).toContain('rotate = 180   # display');
    expect(screen.getByText('Done: HANDHELD.CFG written')).toBeTruthy();
  });

  it('pushes the address map and talks on the console', async () => {
    const cyd = new FakeCyd();
    render(<SetupScreen deps={{ cyd, mapText: TEST_MAP, confirm: () => true }} />);
    await connect();
    expect(useSession.getState().cyd.mapHash).toBe('071de5244d2b');
    fireEvent.click(screen.getByRole('button', { name: 'Map' }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Push the address map' })); });
    expect(cyd.pushed).toBe(TEST_MAP);
    expect(screen.getByText('Done: address map pushed')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Console' }));
    fireEvent.change(screen.getByLabelText('console line'), { target: { value: 'HL STAT' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(cyd.sent).toEqual(['HL STAT']);
    expect(screen.getByTestId('cyd-console').textContent).toMatch(/> HL STAT[\s\S]*< HL OK HL STAT/);
  });

  it('guards the firmware image', async () => {
    const cyd = new FakeCyd();
    render(<SetupScreen deps={{ cyd, mapText: TEST_MAP, confirm: () => true }} />);
    await connect();
    fireEvent.click(screen.getByRole('button', { name: 'Install firmware' }));
    const bad = new File([new Uint8Array(100)], 'nope.bin');
    await act(async () => { fireEvent.change(screen.getByLabelText('firmware image'), { target: { files: [bad] } }); });
    await waitFor(() => expect(screen.getByTestId('install-guard').textContent).toMatch(/^NO/));
    expect((screen.getByRole('button', { name: 'Install' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('sets units and theme in the session, and the cable settings', () => {
    render(<SetupScreen deps={{ mapText: TEST_MAP }} />);
    fireEvent.click(screen.getByRole('tab', { name: 'Units' }));
    fireEvent.click(screen.getByRole('button', { name: /^US/ }));
    expect(useSession.getState().units).toBe('imperial');
    fireEvent.click(screen.getByRole('tab', { name: 'Theme' }));
    fireEvent.click(screen.getByRole('button', { name: /Darker/ }));
    expect(useSession.getState().theme).toBe('darker');
    fireEvent.click(screen.getByRole('tab', { name: 'Cable' }));
    fireEvent.change(screen.getByLabelText('baud'), { target: { value: '9600' } });
    expect(JSON.parse(localStorage.getItem('hondalog.cable')!).baud).toBe(9600);
    fireEvent.click(screen.getByRole('tab', { name: 'Files' }));
    expect(screen.getByTestId('map-in-use').textContent).toMatch(/test-1 #/);
    fireEvent.click(screen.getByRole('tab', { name: 'About' }));
    expect(screen.getByText('hondalog (web)')).toBeTruthy();
  });

  it('says so when the CYD module is not wired', () => {
    render(<SetupScreen initialTab="cyd" deps={{ mapText: TEST_MAP }} />);
    expect(screen.getByText(/not part of this build yet/)).toBeTruthy();
  });
});

describe('Setup > Cable: the ECU source', () => {
  it('shows which source is active and connects through the CYD', async () => {
    let source: 'kkl' | 'cyd' | '' = '';
    let cydClicks = 0;
    const connection = {
      connectPort: async () => { source = 'kkl'; },
      connectSim: async () => undefined,
      connectCyd: async () => { cydClicks += 1; source = 'cyd'; useSession.getState().setEcu({ state: 'connecting', detail: 'waiting for the CYD' }); },
      disconnect: async () => { source = ''; },
      current: () => (source === 'cyd' ? 'CYD COM22' : source === 'kkl' ? 'COM21' : ''),
      source: () => source,
    };
    const picker = { list: async () => [], open: () => { throw new Error('no'); } };
    render(<SetupScreen initialTab="cable" deps={{ mapText: TEST_MAP, connection, picker }} />);
    expect(screen.getByTestId('connection-source').textContent).toBe('Source: not connected');
    fireEvent.click(screen.getByText('Connect through the CYD'));
    await waitFor(() => expect(screen.getByTestId('connection-source').textContent).toBe('Source: through the CYD'));
    expect(cydClicks).toBe(1);
    expect(screen.getByTestId('connection-state').textContent).toMatch(/^CYD COM22: connecting/);
  });
});
