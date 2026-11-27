import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { resetSession, useSession } from '../../core/session';
import type { ChannelState } from '../../core/types';
import { DEMO_SPECS, demoFrame } from '../shell/demo';
import { resetLaunch } from './Bars';
import { bigValuePx } from './BigTile';
import DriveScreen, { DRIVE_SLOTS } from './index';
import { LAUNCH0, stepLaunch, type LaunchState } from './launch';
import { markLine } from './MarkPad';
import { stallSentence, STATE_WORD } from './StallCard';

function feed(t: number) {
  const f = demoFrame(t);
  const states: ChannelState[] = DEMO_SPECS.map((spec) => ({
    spec, value: f[spec.name]!, t, min: 440, max: 5411, fresh: true, alarm: 0,
  }));
  useSession.getState().updateChannels(states);
}

beforeEach(() => {
  localStorage.clear();
  resetSession();
  resetLaunch();
});
afterEach(cleanup);

describe('launch tracker', () => {
  const run = (rows: [number, number, number][], target = 824): LaunchState =>
    rows.reduce((s, [t, rpm, vss]) => stepLaunch(s, { t, rpm, vss, target }), LAUNCH0);

  it('records the low of a launch where the clutch bites at a standstill', () => {
    const s = run([[0, 812, 0], [1, 810, 0], [1.2, 700, 0], [1.5, 546, 0], [1.8, 590, 2], [2.5, 900, 8], [3, 1500, 16]]);
    expect(s.active).toBe(false);
    expect(s.last).toBe(546);
    expect(s.count).toBe(1);
  });

  it('a stall during the launch is 0', () => {
    const s = run([[0, 812, 0], [1, 600, 1], [1.4, 0, 0]]);
    expect(s.last).toBe(0);
  });

  it('a dip that recovers at a standstill closes after 2 s', () => {
    const s = run([[0, 812, 0], [1, 700, 0], [2, 650, 0], [3.5, 820, 0]]);
    expect(s.last).toBe(650);
  });

  it('ignores rolling starts that never stopped', () => {
    const s = run([[0, 2000, 30], [1, 1500, 20], [2, 1200, 10]]);
    expect(s.last).toBeNull();
  });

  it('times out after 6 s', () => {
    const s = run([[0, 812, 0], [1, 700, 1], [8, 900, 5]]);
    expect(s.active).toBe(false);
    expect(s.last).toBe(700);
  });
});

describe('drive helpers', () => {
  it('big value font shrinks with length (tiles.py steps)', () => {
    expect(bigValuePx('812')).toBe(104);
    expect(bigValuePx('5,411')).toBe(88);
    expect(bigValuePx('−123.5')).toBe(75);
    expect(bigValuePx('1234567')).toBe(62);
  });

  it('mark line (MarkPad.sync)', () => {
    expect(markLine(false, 3, 0, 0)).toBe('not recording · Ctrl+R');
    expect(markLine(true, 0, null, 0)).toBe('0 marks');
    expect(markLine(true, 1, 0, 12_500)).toBe('1 mark · last 12 s ago');
    expect(markLine(true, 4, 0, 125_000)).toBe('4 marks · last 2 min ago');
  });

  it('stall card sentences (StallCard.paint_body)', () => {
    const base = useSession.getState().stall;
    expect(STATE_WORD.armed).toBe('ARMED');
    expect(stallSentence({ ...base, state: 'armed' })).toBe('Fires on RPM = 0 after running, or ECU silent 1 s. Saves 20 s before and 5 s after.');
    expect(stallSentence({ ...base, state: 'off' })).toBe('Off: the catcher runs while recording. Ctrl+R starts a recording.');
    expect(stallSentence({ ...base, state: 'triggered', lastFile: 'stall_2026_11_27_1402' }))
      .toBe('Saved stall_2026_11_27_1402. Enter opens it in Replay, Esc dismisses.');
  });
});

describe('DriveScreen', () => {
  it('shows six big readouts, both bars, the stall card and MARK', () => {
    useSession.getState().startRecording();
    useSession.getState().setStall({ state: 'armed', bufferS: 12.5, count: 1 });
    feed(5);
    render(<DriveScreen />);
    for (const n of DRIVE_SLOTS) expect(screen.getByTestId(`big-${n}`)).toBeTruthy();
    expect(screen.getByTestId('bigvalue-vss').textContent).toBe('0');
    expect(screen.getByTestId('bigvalue-batt').textContent).toMatch(/^1[34]\.\d$/);
    expect(screen.getByText('RPM vs idle target')).toBeTruthy();
    expect(screen.getByText('Launch margin')).toBeTruthy();
    // rpm − idle target, signed
    expect(screen.getByTestId('launch-margin-readout').textContent).toMatch(/^[+−±]\d+ rpm$/);
    expect(screen.getByTestId('launch-low').textContent).toBe('no launch yet');
    expect(screen.getByText('ARMED')).toBeTruthy();
    expect(screen.getByText('12.5 / 20 s')).toBeTruthy();
    expect(screen.getByTestId('stall-count').textContent).toBe('1');
    expect(screen.getByTestId('mark-line').textContent).toBe('0 marks');
    fireEvent.click(screen.getByTestId('mark-pad'));
    expect(useSession.getState().marks.length).toBe(1);
    expect(screen.getByTestId('mark-line').textContent).toBe('1 mark · last 0 s ago');
  });

  it('follows a launch and shows its low', () => {
    render(<DriveScreen />);
    act(() => { for (let t = 15; t <= 24; t += 0.1) feed(t); });
    expect(screen.getByTestId('launch-low').textContent).toMatch(/^last launch low 5\d\d rpm$/);
  });

  it('keeps tracking while F2 is not shown', () => {
    const { unmount } = render(<DriveScreen />);
    unmount();
    act(() => { for (let t = 15; t <= 24; t += 0.1) feed(t); });
    render(<DriveScreen />);
    expect(screen.getByTestId('launch-low').textContent).toMatch(/^last launch low 5/);
  });

  it('a triggered stall turns the card red', () => {
    useSession.getState().setStall({ state: 'triggered', lastFile: 'stall_x' });
    render(<DriveScreen />);
    expect(screen.getByText('TRIGGERED')).toBeTruthy();
    expect(screen.getByTestId('stall-card').style.background).toBe('rgb(255, 85, 85)');
  });
});
