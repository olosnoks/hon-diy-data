import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { resetSession, useSession } from '../../core/session';
import type { ChannelSpec, ChannelState } from '../../core/types';
import { DEMO_SPECS, demoFrame } from '../shell/demo';
import { handleKey } from '../shell/keys';
import { idleFrac } from './IdleBar';
import LiveScreen from './index';
import { GROUPS, SWITCHES, tilesFor, unplacedChannels } from './layout';
import { unmappedAddrs } from './RawDrawer';

function feed(t = 5, omit: string[] = [], stale: string[] = []) {
  const f = demoFrame(t);
  const states: ChannelState[] = DEMO_SPECS.filter((s) => !omit.includes(s.name)).map((spec) => ({
    spec, value: f[spec.name]!, t, min: f[spec.name]! - 1, max: f[spec.name]! + 1, fresh: !stale.includes(spec.name), alarm: 0,
  }));
  useSession.getState().updateChannels(states);
}

beforeEach(() => {
  localStorage.clear();
  resetSession();
});
afterEach(cleanup);

describe('F1 Live layout', () => {
  it('has the six canvas groups in order', () => {
    expect(GROUPS.map((g) => g.title)).toEqual(['ENGINE', 'IDLE CONTROL', 'FUEL', 'IGNITION', 'ELECTRICAL', 'CYD · BLACK BOX']);
  });

  it('uses the desktop group table: map channels only, second choices, unplaced to the drawer', () => {
    const st = (name: string, group = '') => ({ spec: { ...DEMO_SPECS[0]!, name, label: name, group } as ChannelSpec, value: 0, t: 0, min: 0, max: 0, fresh: true, alarm: 0 as const });
    const ch = Object.fromEntries(['ign', 'knock', 'load', 'o2_post', 'o2_2', 'extra'].map((n) => [n, st(n, 'ignition')]));
    expect(GROUPS.map((g) => g.tiles.map((d) => d.name))).toEqual([
      ['rpm', 'map', 'tps', 'load_ecu', 'load', 'ect', 'iat'],
      ['idle_target', 'iac_cmd', 'iacv', 'iac_learn', 'ign_limit', 'vss'],
      ['stft', 'ltft', 'o2', 'o2_2', 'o2_post', 'inj', 'purge_duty', 'fuel_status'],
      ['ign', 'vtec', 'vtec_psw', 'knock'],
      ['batt', 'alt_fr', 'eld'],
      ['gps_speed', 'gear', 'trip', 'cyd_sd', 'cyd_link', 'cyd_stalls'],
    ]);
    expect(tilesFor(GROUPS[3]!, ch).map((d) => d.name)).toEqual(['ign', 'knock']);
    expect(tilesFor(GROUPS[0]!, ch).map((d) => d.name)).toEqual(['load']);          // load_ecu is not in the map
    expect(tilesFor(GROUPS[2]!, ch).map((d) => d.name)).toEqual(['o2_2']);          // o2_post only without o2_2
    expect(tilesFor(GROUPS[5]!, ch)).toHaveLength(6);                                // the CYD's worked-out tiles always
    expect(tilesFor(GROUPS[0]!, {}).map((d) => d.name)).toEqual(['rpm', 'map', 'tps', 'load_ecu', 'ect', 'iat']);
    expect(unplacedChannels(ch)).toEqual(['extra']);
    expect(SWITCHES.map((x) => x.name)).toEqual(['starter', 'brake', 'psp', 'ac_sw', 'ac_clutch', 'fan', 'fuel_pump', 'closed_loop', 'o2_heater', 'mil']);
  });

  it('idle bar maps 500-1200 rpm', () => {
    expect(idleFrac(500)).toBe(0);
    expect(idleFrac(1200)).toBe(1);
    expect(idleFrac(850)).toBeCloseTo(0.5);
    expect(idleFrac(0)).toBe(0);
  });
});

describe('LiveScreen', () => {
  it('renders groups, tiles with values, marks, notes and the idle bar', () => {
    feed();
    useSession.getState().setCyd({ state: 'ok', mode: 'relay', sdLogging: true, mapMatch: true, mapHash: '8f3b48', sats: 12, fix: '3D', sdDrive: 41, sdFreeGb: 1.2 });
    render(<LiveScreen />);
    const engine = screen.getByRole('region', { name: 'ENGINE' });
    expect(within(engine).getByText('warm idle, stopped')).toBeTruthy();
    expect(screen.getByTestId('value-idle_target').textContent).toBe('824');
    expect(screen.getByTestId('value-stft').textContent).toMatch(/^\+\d+\.\d$/);
    // verified vs unverified marks
    expect(within(screen.getByTestId('tile-rpm')).getByRole('img', { name: 'verified' })).toBeTruthy();
    expect(within(screen.getByTestId('tile-load')).getByRole('img', { name: 'unverified' })).toBeTruthy();
    // notes
    expect(within(screen.getByTestId('tile-map')).getByText('baro 100')).toBeTruthy();
    expect(within(screen.getByTestId('tile-ltft')).getByText(/^total \+\d+ %$/)).toBeTruthy();
    expect(within(screen.getByTestId('tile-vss')).getByText('gear N')).toBeTruthy();
    // derived CYD tiles
    expect(screen.getByTestId('value-gear').textContent).toBe('N');
    expect(screen.getByTestId('value-cyd_sd').textContent).toBe('REC');
    expect(screen.getByTestId('value-cyd_link').textContent).toBe('RELAY');
    expect(within(screen.getByTestId('tile-cyd_link')).getByText('map #8f3b48 ✓')).toBeTruthy();
    expect(within(screen.getByTestId('tile-gps_speed')).getByText('12 sats · fix 3D')).toBeTruthy();
    // idle bar
    expect(within(screen.getByTestId('idle-bar')).getByText(/^\d+ \/ 824 · /)).toBeTruthy();
    expect(screen.getByText('yellow = stall zone')).toBeTruthy();
  });

  it('stale is hatched with ---, a channel the map lacks is not shown', () => {
    feed(5, ['iat'], ['map']);
    render(<LiveScreen />);
    expect(screen.getByTestId('value-map').textContent).toBe('---');
    expect(screen.getByTestId('tile-map').getAttribute('data-stale')).toBe('true');
    expect(screen.queryByTestId('tile-iat')).toBeNull();
  });

  it('before any map, the table shows as placeholders', () => {
    render(<LiveScreen />);
    expect(within(screen.getByTestId('tile-iat')).getByText('not in address map')).toBeTruthy();
  });

  it('alarm states: warn and alarm by level, with shapes', () => {
    feed();
    useSession.getState().setAlarms([
      { name: 'w', channel: 'ltft', level: 'warn', message: '', acked: true },
      { name: 'a', channel: 'rpm', level: 'alarm', message: '', acked: false },
    ]);
    render(<LiveScreen />);
    expect(screen.getByTestId('tile-ltft').getAttribute('data-level')).toBe('1');
    expect(within(screen.getByTestId('tile-ltft')).getByRole('img', { name: 'warning' })).toBeTruthy();
    const rpm = screen.getByTestId('tile-rpm');
    expect(rpm.getAttribute('data-level')).toBe('2');
    const oct = within(rpm).getByRole('img', { name: 'alarm' });
    expect(oct.getAttribute('class')).toBe('hl-blink');
    act(() => { handleKey({ key: 'Escape' }); });
    expect(within(screen.getByTestId('tile-rpm')).getByRole('img', { name: 'alarm' }).getAttribute('class')).toBeNull();
  });

  it('switches strip shows on / off', () => {
    feed();
    render(<LiveScreen />);
    expect(screen.getByTestId('switch-fuel_pump').getAttribute('data-on')).toBe('true');
    expect(screen.getByTestId('switch-mil').getAttribute('data-on')).toBeNull();
    expect(screen.getByTestId('switch-closed_loop').textContent).toBe('Closed loop');
  });

  it('raw bytes drawer counts unmapped bytes, opens, Esc closes', () => {
    feed();
    const mapped = DEMO_SPECS.find((s) => s.name === 'rpm')!.addr;
    useSession.getState().setRaw({ [mapped]: 1, 0xa0: 0x3c, 0xa1: 255 });
    expect(unmappedAddrs(useSession.getState())).toEqual([0xa0, 0xa1]);
    render(<LiveScreen />);
    const btn = screen.getByRole('button', { name: /Raw bytes \(2\)/ });
    fireEvent.click(btn);
    const drawer = screen.getByRole('dialog', { name: 'Raw bytes' });
    expect(within(drawer).getByTestId('raw-160').textContent).toContain('3C');
    act(() => { handleKey({ key: 'Escape' }); });
    expect(screen.queryByRole('dialog', { name: 'Raw bytes' })).toBeNull();
  });
});
