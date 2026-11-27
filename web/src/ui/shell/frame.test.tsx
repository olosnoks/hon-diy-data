/** The frame: stall banner, bottom strip, theme toggle, messages (desktop chrome.py parity). */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetSession, useSession } from '../../core/session';
import { stallBanner } from '../../wiring/controller';
import { BottomStrip, KEY_HINTS, StallBannerBar } from './Frame';
import { handleKey } from './keys';
import { StageSizeContext } from './Stage';
import { TopBar } from './TopBar';

beforeEach(() => { localStorage.clear(); resetSession(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const BANNER = { title: 'STALL 23:03:07', cause: 'RPM dropped to 0. Saved stall_2026_11_26_2303', drive: 'stall_2026_11_26_2303', t: 41.5 };

describe('stall banner', () => {
  it('words a caught stall as the desktop does', () => {
    const b = stallBanner('stall_x', { reason: 'rpm', message: 'rpm fell to 0', t: 10 }, new Date(2026, 10, 26, 23, 2, 57).getTime() / 1000);
    expect(b).toEqual({ title: 'STALL 23:03:07', cause: 'RPM dropped to 0. Saved stall_x', drive: 'stall_x', t: 10 });
    expect(stallBanner('c', { reason: 'link', message: '', t: 0 }, 0).cause).toBe('ECU went silent. Saved c');
    expect(stallBanner('c', { reason: 'overtemp', message: 'coolant over 105 °C.', t: 0 }, 0).cause).toBe('Coolant over 105 °C. Saved c');
  });

  it('shows under the top bar with Open in Replay (Enter) and Dismiss (Esc)', () => {
    useSession.getState().showStallBanner(BANNER);
    render(<StallBannerBar />);
    expect(screen.getByTestId('stall-banner').textContent).toContain('STALL 23:03:07');
    expect(screen.getByTestId('stall-cause').textContent).toBe(BANNER.cause);
    expect(screen.getByRole('button', { name: /Open in Replay\s*Enter/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Dismiss\s*Esc/ }));
    expect(useSession.getState().stallBanner).toBeNull();
    expect(screen.queryByTestId('stall-banner')).toBeNull();
  });

  it('Enter opens the stall in Replay from any screen; Esc dismisses and acknowledges', () => {
    const s = useSession.getState();
    s.setScreen('power');
    s.showStallBanner(BANNER);
    expect(handleKey({ key: 'Enter' })).toBe(true);
    expect(useSession.getState().screen).toBe('replay');
    expect(useSession.getState().replayRequest).toMatchObject({ drive: BANNER.drive, t: 41.5 });
    expect(useSession.getState().stallBanner).toBeNull();
    s.showStallBanner(BANNER);
    s.setAlarms([{ name: 'a', level: 'alarm', message: 'x', acked: false }]);
    handleKey({ key: 'Escape' });
    expect(useSession.getState().stallBanner).toBeNull();
    expect(useSession.getState().alarms[0]!.acked).toBe(true);
  });
});

describe('bottom strip', () => {
  it('no alarms, the link summary and the key hints', () => {
    useSession.getState().setLinkInfo('simulator · map p2p-guess7 · 41 ch · 12 unverified');
    render(<BottomStrip />);
    expect(screen.getByTestId('strip-ok').textContent).toBe('No active alarms');
    expect(screen.getByTestId('strip-info').textContent).toBe('simulator · map p2p-guess7 · 41 ch · 12 unverified');
    expect(screen.getByTestId('key-hints').textContent).toBe(KEY_HINTS);
  });

  it('the worst alarm and warning, with how many more', () => {
    useSession.getState().setAlarms([
      { name: 'ect', level: 'alarm', message: 'ECU Coolant 108 °C — above 105', acked: false },
      { name: 'b', level: 'alarm', message: 'ECU Battery 11.2 V — below 11.5', acked: false },
      { name: 'w', level: 'warn', message: 'ECU Long trim +16 % — above 15', acked: false },
    ]);
    render(<BottomStrip />);
    expect(screen.getByTestId('strip-alarm').textContent).toBe('ECU Coolant 108 °C — above 105  (+1)');
    expect(screen.getByTestId('strip-warn').textContent).toBe('ECU Long trim +16 % — above 15');
    expect(screen.queryByTestId('strip-ok')).toBeNull();
  });

  it('a passing message replaces the summary for a while', () => {
    vi.useFakeTimers();
    useSession.getState().setLinkInfo('no port');
    render(<BottomStrip />);
    act(() => { useSession.getState().startRecording(); useSession.getState().mark(); });
    expect(screen.getByTestId('strip-message').textContent).toBe('Marker 1 dropped');
    act(() => { vi.advanceTimersByTime(6100); });
    expect(screen.getByTestId('strip-info').textContent).toBe('no port');
  });

  it('hides the key hints at compact (head unit, phone) sizes', () => {
    render(<StageSizeContext.Provider value={{ width: 1140, height: 668, scale: 0.9 }}><BottomStrip /></StageSizeContext.Provider>);
    expect(screen.queryByTestId('key-hints')).toBeNull();
  });

  it('a mark while not recording says so', () => {
    useSession.getState().mark();
    expect(useSession.getState().message!.text).toBe('Not recording: marker ignored');
  });
});

describe('top bar additions', () => {
  it('◐ F9 toggles the theme', () => {
    render(<TopBar />);
    const btn = screen.getByTestId('theme-toggle');
    expect(btn.textContent).toBe('◐F9');
    fireEvent.click(btn);
    expect(useSession.getState().theme).toBe('darker');
  });

  it('N err, and a lost ECU / CYD fills the chip with the alarm colour', () => {
    const s = useSession.getState();
    s.setEcu({ state: 'ok', rateHz: 12.1, errors: 4 });
    s.setCyd({ state: 'lost' });
    const { rerender } = render(<TopBar />);
    expect(screen.getByTestId('status-ecu').textContent).toBe('ECU 12.1 Hz 4 err');
    expect(screen.getByTestId('status-cyd').querySelector('[data-bad]')).toBeTruthy();
    act(() => { s.setEcu({ state: 'error', detail: 'silent' }); });
    rerender(<TopBar />);
    expect(screen.getByTestId('status-ecu').querySelector('[data-bad]')!.textContent).toBe('ECU silent 4 err');
  });
});
