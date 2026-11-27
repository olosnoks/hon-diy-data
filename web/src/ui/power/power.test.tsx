import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DRACULA } from '../theme';
import { synthDrive } from '../gps/fixtures';

// uPlot needs a real canvas; the chart's options and data are checked here instead.
vi.mock('./UPlotChart', () => ({
  UPlotChart: ({ label, data, options }: { label: string; data: unknown[][]; options: { series: { label?: string }[] } }) => (
    <div data-testid="uplot" aria-label={label} data-columns={data.length} data-x={data[0]?.length ?? 0}
      data-series={options.series.map((s) => s.label ?? '').join('|')} />
  ),
}));

import PowerScreen, { PowerView } from './index';

beforeEach(() => {
  localStorage.clear();
  // jsdom has no 2D canvas: the cell map draws nothing (and says nothing) here
  HTMLCanvasElement.prototype.getContext = (() => null) as unknown as HTMLCanvasElement['getContext'];
});
afterEach(cleanup);

const drive = synthDrive();

function cards() {
  const res = screen.getByRole('complementary', { name: 'Result' });
  return res.textContent ?? '';
}

describe('F5 Power', () => {
  it('lists the pulls found, best first, with their quality', () => {
    render(<PowerView t={DRACULA} drive={drive} />);
    const list = screen.getByRole('listbox', { name: 'Pulls' });
    const opts = within(list).getAllByRole('option');
    expect(opts.length).toBeGreaterThanOrEqual(2);
    expect(opts[0]!.textContent).toContain('FULL THROTTLE');
    expect(opts[0]!.textContent).toContain('2nd');
    expect(opts[0]!.getAttribute('aria-selected')).toBe('true');
    expect(opts[1]!.textContent).toContain('PART');
    expect(opts[1]!.textContent).toContain('3rd');
  });

  it('shows the curve with the reference, the band and the result', () => {
    render(<PowerView t={DRACULA} drive={drive} />);
    const chart = screen.getByTestId('uplot');
    expect(chart.getAttribute('aria-label')).toMatch(/Torque and horsepower against rpm for the \d\d:\d\d:\d\d pull, with the published D16Y8 curve/);
    expect(chart.dataset.series).toContain('D16Y8 torque (approx.)');
    expect(chart.dataset.series).toContain('band low');
    expect(Number(chart.dataset.columns)).toBe(7);
    const text = cards();
    expect(text).toMatch(/Peak torque\d+ N·m/);
    expect(text).toMatch(/Power\d+ hp/);
    expect(text).toMatch(/vs D16Y8≈ (8[6-9]|9[0-5]) %/);
    expect(text).toMatch(/Uncertainty± \d+ %grade unknown/);
    expect(text).toContain('2nd ✓');
    expect(text).toContain('22 °C');
    expect(text).toContain('100.3 kPa');
    expect(screen.getByText(/D16Y8 published \(approx\.\)/)).toBeTruthy();
  });

  it('recomputes when an input changes', () => {
    render(<PowerView t={DRACULA} drive={drive} />);
    const peak = () => Number(/Peak torque(\d+)/.exec(cards())![1]);
    const before = peak();
    fireEvent.change(screen.getByLabelText('Car + driver, kg'), { target: { value: '1500' } });
    expect(peak()).toBeGreaterThan(before);
    fireEvent.change(screen.getByLabelText('Road grade, % (empty = unknown)'), { target: { value: '0' } });
    expect(cards()).toContain('grade given');
    fireEvent.change(screen.getByLabelText('Car + driver, kg'), { target: { value: 'abc' } });
    expect(screen.getByRole('status').textContent).toMatch(/Check the inputs/);
    expect(JSON.parse(localStorage.getItem('hondalog.power.inputs')!).massKg).toBe(1500);
  });

  it('compares with another pull', () => {
    render(<PowerView t={DRACULA} drive={drive} />);
    fireEvent.click(screen.getByRole('button', { name: 'Compare with another pull' }));
    expect(screen.getByText('Pick the pull to compare with.')).toBeTruthy();
    const opts = within(screen.getByRole('listbox', { name: 'Pulls' })).getAllByRole('option');
    fireEvent.click(opts[1]!);
    expect(opts[1]!.textContent).toContain('compared');
    const chart = screen.getByTestId('uplot');
    expect(Number(chart.dataset.columns)).toBe(9);
    expect(screen.getByRole('button', { name: 'Stop comparing' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Stop comparing' }));
    expect(Number(screen.getByTestId('uplot').dataset.columns)).toBe(7);
  });

  it('saves a baseline and shows it', () => {
    render(<PowerView t={DRACULA} drive={drive} />);
    fireEvent.click(screen.getByRole('button', { name: 'Save as baseline' }));
    expect(localStorage.getItem('hondalog.power.baseline')).toBeTruthy();
    const box = screen.getByRole('checkbox', { name: /Show baseline/ });
    fireEvent.click(box);
    expect(screen.getByTestId('uplot').dataset.series).toContain('baseline');
  });

  it('exports the curve as CSV on the device', () => {
    const save = vi.fn();
    render(<PowerView t={DRACULA} drive={drive} save={save} />);
    fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }));
    expect(save).toHaveBeenCalledTimes(1);
    const [name, mime, text] = save.mock.calls[0]!;
    expect(name).toMatch(/^drive_2026_11_26_2251_power_\d{6}\.csv$/);
    expect(mime).toBe('text/csv');
    expect(text).toContain('rpm,torque_nm');
  });

  it('has the pull trace and time in cell tabs', () => {
    render(<PowerView t={DRACULA} drive={drive} />);
    fireEvent.click(screen.getByRole('tab', { name: 'Pull trace' }));
    const chart = screen.getByTestId('uplot');
    expect(chart.getAttribute('aria-label')).toMatch(/rpm, speed, throttle and MAP/);
    expect(chart.dataset.series).toBe('|rpm|km/h|TPS %|MAP kPa');
    fireEvent.click(screen.getByRole('tab', { name: 'Time in cell' }));
    expect(screen.getByTestId('cell-coverage').textContent).toMatch(/\d+ \/ 200/);
    fireEvent.click(screen.getByRole('tab', { name: 'Fuel trim %' }));
    expect(screen.getByText('−12 % rich')).toBeTruthy();
  });

  it('selects the pull Replay asked for', () => {
    const { rerender } = render(<PowerView t={DRACULA} drive={drive} />);
    rerender(<PowerView t={DRACULA} drive={drive} focus={{ t0: drive.marks.partT0 + 1, t1: drive.marks.partT0 + 2, seq: 1 }} />);
    const sel = within(screen.getByRole('listbox', { name: 'Pulls' })).getAllByRole('option').find((o) => o.getAttribute('aria-selected') === 'true')!;
    expect(sel.textContent).toContain('3rd');
  });

  it('says so when there is no drive', () => {
    render(<PowerView t={DRACULA} drive={null} />);
    expect(screen.getByText('No drive open')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Export CSV' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('opens the newest drive of the shared drive source', async () => {
    await act(async () => { render(<PowerScreen />); });
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Drive' })).toBeTruthy());
    await waitFor(() => expect((screen.getByRole('combobox', { name: 'Drive' }) as HTMLSelectElement).value).not.toBe(''));
  });
});
