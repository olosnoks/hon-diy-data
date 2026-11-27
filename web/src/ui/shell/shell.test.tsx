import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { App } from '../../App';
import { resetSession, useSession } from '../../core/session';
import { DARKER, DRACULA } from '../theme';
import { demoFrame, DEMO_SPECS, startDemo } from './demo';
import { formatNumber, formatValue, mmss, rate } from './format';
import { stageFit } from './Stage';
import { cydChip, ecuChip, stallText, TopBar } from './TopBar';

beforeEach(() => {
  localStorage.clear();
  resetSession();
});
afterEach(cleanup);

describe('stageFit', () => {
  it('is 1:1 at the design size', () => {
    expect(stageFit(1366, 768)).toEqual({ scale: 1, width: 1366, height: 768 });
  });
  it('scales up on a big monitor and fills a wider window', () => {
    const f = stageFit(2732, 1536);
    expect(f.scale).toBe(2);
    const w = stageFit(2560, 1080);
    expect(w.height).toBeCloseTo(768);
    expect(w.width).toBeGreaterThan(1366);
  });
  it('relaxes to 1140 x 640 on a landscape phone', () => {
    const f = stageFit(800, 360);
    expect(f.scale).toBeCloseTo(0.5625);
    expect(f.height).toBeCloseTo(640);
    expect(f.width).toBeCloseTo(1422.2, 0);
  });
});

describe('format', () => {
  it('numbers', () => {
    expect(formatNumber(812.4, 0)).toBe('812');
    expect(formatNumber(-12, 0)).toBe('−12');
    expect(formatNumber(9.4, 1, true)).toBe('+9.4');
    expect(formatNumber(-0.01, 1)).toBe('0.0');
    expect(formatNumber(12345, 0)).toBe('12,345');
    expect(mmss(862)).toBe('14:22');
    expect(rate(12.1)).toBe('12.1 Hz');
    expect(rate(250)).toBe('250 Hz');
  });
  it('values: stale is ---, flags ON/OFF, imperial converts', () => {
    const vss = DEMO_SPECS.find((s) => s.name === 'vss')!;
    const flag = DEMO_SPECS.find((s) => s.name === 'mil')!;
    expect(formatValue({ spec: vss, value: 100, t: 0, min: 0, max: 0, fresh: true, alarm: 0 }, 'imperial')).toBe('62');
    expect(formatValue({ spec: vss, value: 100, t: 0, min: 0, max: 0, fresh: false, alarm: 0 }, 'metric')).toBe('---');
    expect(formatValue({ spec: flag, value: 1, t: 0, min: 0, max: 0, fresh: true, alarm: 0 }, 'metric')).toBe('ON');
    expect(formatValue(undefined, 'metric')).toBe('---');
  });
});

describe('status chips', () => {
  it('ECU', () => {
    const s = useSession.getState();
    expect(ecuChip(s.ecu, DRACULA).text).toBe('ECU off');
    expect(ecuChip({ ...s.ecu, state: 'ok', rateHz: 12.1 }, DRACULA)).toMatchObject({ text: 'ECU 12.1 Hz', color: DRACULA.ok });
    expect(ecuChip({ ...s.ecu, state: 'error', detail: 'port lost' }, DRACULA)).toMatchObject({ text: 'ECU port lost', color: DRACULA.alarm });
  });
  it('stall and CYD', () => {
    const s = useSession.getState();
    expect(stallText({ ...s.stall, state: 'armed' }).text).toBe('Stall catcher armed');
    expect(stallText({ ...s.stall, state: 'triggered' }).hot).toBe(true);
    expect(cydChip({ ...s.cyd, state: 'ok', mapMatch: true }, DRACULA).text).toBe('CYD black box');
    expect(cydChip({ ...s.cyd, state: 'ok', mapMatch: false }, DRACULA)).toMatchObject({ text: 'CYD map mismatch', color: DRACULA.warn });
  });
});

describe('TopBar', () => {
  it('shows F1-F8 with Live selected; tapping a key switches screen', () => {
    render(<TopBar />);
    const tabs = screen.getAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual(['F1Live', 'F2Drive', 'F3Events', 'F4Replay', 'F5Power', 'F6GPS', 'F7Bytes', 'F8Setup']);
    expect(tabs[0]!.getAttribute('aria-selected')).toBe('true');
    fireEvent.click(tabs[1]!);
    expect(useSession.getState().screen).toBe('drive');
    expect(screen.getAllByRole('tab')[1]!.getAttribute('aria-selected')).toBe('true');
  });

  it('shows the status as the canvas words it', () => {
    const s = useSession.getState();
    s.setEcu({ state: 'ok', rateHz: 12.1 });
    s.setStall({ state: 'armed' });
    s.setCyd({ state: 'ok', mapMatch: true });
    render(<TopBar />);
    expect(screen.getByTestId('status-ecu').textContent).toBe('ECU 12.1 Hz');
    expect(screen.getByTestId('status-rec').textContent).toBe('REC off');
    expect(screen.getByTestId('status-stall').textContent).toBe('Stall catcher armed');
    expect(screen.getByTestId('status-cyd').textContent).toBe('CYD black box');
    fireEvent.click(screen.getByTestId('status-rec'));
    expect(useSession.getState().recording.active).toBe(true);
    expect(screen.getByTestId('status-rec').textContent).toBe('REC 00:00');
    fireEvent.click(screen.getByText('MARK'));
    expect(useSession.getState().marks.length).toBe(1);
  });
});

describe('App', () => {
  it('renders Live, switches with F2, toggles theme with F9', () => {
    render(<App />);
    expect(screen.getByRole('region', { name: 'ENGINE' })).toBeTruthy();
    act(() => { fireEvent.keyDown(window, { key: 'F2' }); });
    expect(screen.getByTestId('stall-card')).toBeTruthy();
    expect(document.querySelector('[data-theme]')!.getAttribute('data-theme')).toBe('dracula');
    act(() => { fireEvent.keyDown(window, { key: 'F9' }); });
    expect(document.querySelector('[data-theme]')!.getAttribute('data-theme')).toBe('darker');
    expect(document.body.style.background).toBe('rgb(25, 26, 33)'); // DARKER.bg
    expect(DARKER.bg).toBe('#191A21');
  });

  it('loads a lazy screen (placeholder or real) without crashing', async () => {
    render(<App />);
    act(() => { fireEvent.keyDown(window, { key: 'F8' }); });
    expect(await screen.findByText(/Setup/, {}, { timeout: 4000 })).toBeTruthy();
  });
});

describe('demo', () => {
  it('produces the canvas idle values', () => {
    const f = demoFrame(5);
    expect(f.rpm).toBeGreaterThan(780);
    expect(f.rpm).toBeLessThan(840);
    expect(f.idle_target).toBe(824);
    expect(f.vss).toBe(0);
    expect(f.closed_loop).toBe(1);
  });
  it('dips toward the stall zone at the launch, then drives', () => {
    expect(demoFrame(20.5).rpm).toBeLessThan(600);
    expect(demoFrame(30).vss).toBeGreaterThan(20);
  });
  it('feeds the store and stops', () => {
    const stop = startDemo(10);
    const s = useSession.getState();
    expect(s.demo).toBe(true);
    expect(s.ecu).toMatchObject({ state: 'ok', simulated: true });
    expect(Object.keys(s.channels).length).toBe(DEMO_SPECS.length);
    expect(s.recording.active).toBe(true);
    stop();
    expect(useSession.getState().demo).toBe(false);
  });
});
