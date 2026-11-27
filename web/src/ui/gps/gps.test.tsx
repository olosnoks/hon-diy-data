import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DRACULA } from '../theme';
import { synthDrive } from './fixtures';
import type { MapViewProps } from './MapView';
import { DEFAULT_MAP_SETTINGS, loadMapSettings, saveMapSettings } from './settings';
import { offset } from './geo/proj';
import type { StallPin } from './geo/stalls';

// Leaflet needs a laid-out page; the stub shows what the screen hands the map.
vi.mock('./MapView', async (orig) => {
  const real = await orig<typeof import('./MapView')>();
  return {
    ...real,
    default: (p: MapViewProps) => (
      <div data-testid="map" data-track={p.track?.length ?? 0} data-pins={p.pins.map((x) => x.kind).join(',')}
        data-coloured={p.values ? 'yes' : 'no'} data-cursor={p.cursor ? 'yes' : 'no'} data-attribution={p.attribution} data-fit={p.fitKey}>
        <button type="button" onClick={() => p.onTrackClick?.(p.track!.t[40]!)}>click the track</button>
        {p.pins.map((pin, i) => <button key={i} type="button" onClick={() => p.onPinClick?.(pin)}>{`pin ${pin.kind} ${i}`}</button>)}
      </div>
    ),
  };
});

import { GpsView, channelsOf, distanceText, resolveColour } from './index';

beforeEach(() => localStorage.clear());
afterEach(cleanup);

const drive = synthDrive();
const tiles = { fetcher: null, attribution: '© OpenStreetMap contributors' };

function view(extra: Partial<Parameters<typeof GpsView>[0]> = {}) {
  const onSettings = vi.fn();
  const save = vi.fn();
  const utils = render(<GpsView t={DRACULA} drive={drive} tiles={tiles} settings={DEFAULT_MAP_SETTINGS} onSettings={onSettings} save={save} {...extra} />);
  return { ...utils, onSettings, save };
}

async function map() {
  return await screen.findByTestId('map');
}

describe('F6 GPS map', () => {
  it('draws the track coloured by speed with every pin', async () => {
    view();
    const m = await map();
    expect(Number(m.dataset.track)).toBe(drive.gps.t.length);
    expect(m.dataset.coloured).toBe('yes');
    expect(m.dataset.attribution).toBe('© OpenStreetMap contributors');
    const kinds = m.dataset.pins!.split(',');
    expect(kinds[0]).toBe('start');
    for (const k of ['stall', 'nearstall', 'dropout', 'marker']) expect(kinds).toContain(k);
    const panel = screen.getByRole('complementary', { name: 'Map panel' });
    expect(panel.textContent).toMatch(/\d+ fixes · 1 gap over 5 s/);
    expect(within(screen.getByRole('list', { name: 'Pins' })).getAllByRole('listitem').length).toBe(kinds.length - 1);
  });

  it('puts the cursor where the track is clicked', async () => {
    view();
    expect(screen.getByText('Click the track or a pin.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'click the track' }));
    expect((await map()).dataset.cursor).toBe('yes');
    expect(screen.getByText('Heading')).toBeTruthy();
    expect(screen.getByText(/^\d+ km\/h$/)).toBeTruthy();
    expect(screen.getByText(/^-?\d+\.\d{6}, -?\d+\.\d{6}$/)).toBeTruthy();
  });

  it('moves the cursor to a pin', async () => {
    view();
    fireEvent.click(within(screen.getByRole('list', { name: 'Pins' })).getAllByRole('listitem')[0]!);
    expect((await map()).dataset.cursor).toBe('yes');
  });

  it('colours by another channel', async () => {
    view();
    fireEvent.click(screen.getByRole('tab', { name: 'RPM' }));
    expect(screen.getByLabelText('Colour legend').textContent).toContain('rpm');
    fireEvent.change(screen.getByRole('combobox', { name: 'Other channel' }), { target: { value: 'tps' } });
    expect(screen.getByLabelText('Colour legend').textContent).toContain('tps');
    fireEvent.click(screen.getByRole('tab', { name: 'AFR' }));
    expect(screen.getByLabelText('Colour legend').textContent).toContain('AFR: not in this drive');
    expect((await map()).dataset.coloured).toBe('no');
  });

  it('exports GPX, KML and GeoJSON on the device', () => {
    const { save } = view();
    fireEvent.click(screen.getByRole('button', { name: 'GPX' }));
    fireEvent.click(screen.getByRole('button', { name: 'KML' }));
    fireEvent.click(screen.getByRole('button', { name: 'GeoJSON' }));
    expect(save.mock.calls.map((c) => [c[0], c[1]])).toEqual([
      ['drive_2026_11_26_2251.gpx', 'application/gpx+xml'],
      ['drive_2026_11_26_2251.kml', 'application/vnd.google-earth.kml+xml'],
      ['drive_2026_11_26_2251.geojson', 'application/geo+json'],
    ]);
    expect(save.mock.calls[0]![2]).toContain('<gpx');
    expect(screen.getByRole('status').textContent).toContain('on this device');
  });

  it('can switch online tiles off', () => {
    const { onSettings, rerender } = view();
    const box = screen.getByRole('checkbox', { name: /Download map tiles/ });
    expect((box as HTMLInputElement).checked).toBe(true);
    fireEvent.click(box);
    expect(onSettings).toHaveBeenCalledWith({ ...DEFAULT_MAP_SETTINGS, online: false });
    rerender(<GpsView t={DRACULA} drive={drive} tiles={tiles} settings={{ ...DEFAULT_MAP_SETTINGS, online: false }} onSettings={onSettings} />);
    expect(screen.getByText('offline: cached tiles only')).toBeTruthy();
  });

  it('keeps the map settings on the device', () => {
    expect(loadMapSettings()).toEqual(DEFAULT_MAP_SETTINGS);
    saveMapSettings({ ...DEFAULT_MAP_SETTINGS, online: false });
    expect(loadMapSettings().online).toBe(false);
    localStorage.setItem('hondalog.map.settings', '{bad');
    expect(loadMapSettings()).toEqual(DEFAULT_MAP_SETTINGS);
  });

  it('says so for a drive without GPS', async () => {
    const plain = { ...drive, gps: { t: new Float64Array(), lat: new Float64Array(), lon: new Float64Array(), speedKmh: new Float64Array(), heading: new Float64Array() } };
    view({ drive: plain });
    expect(screen.getByText(/No GPS in this drive/)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'GPX' }) as HTMLButtonElement).disabled).toBe(true);
    expect((await map()).dataset.pins).toBe('');
  });

  it('shows the moment Events asked for', async () => {
    const { rerender } = view();
    rerender(<GpsView t={DRACULA} drive={drive} tiles={tiles} settings={DEFAULT_MAP_SETTINGS} onSettings={() => {}} focusT={{ t: 20, seq: 1 }} />);
    expect((await map()).dataset.cursor).toBe('yes');
  });

  it('shows every stall where it happened and opens one in Replay', async () => {
    const at = (e: number, n: number) => offset(40, -100, e, n);
    const pin = (name: string, e: number, n: number): StallPin => {
      const [lat, lon] = at(e, n);
      return { name, kind: 'stall', reason: 'rpm', message: 'rpm dropped to 0', drive: name, wallUnix: 1, tTrigger: 20, lat, lon, speedKmh: 0, maxSpeed30s: 25 };
    };
    const pins = [pin('stall_a', 0, 0), pin('stall_b', 60, 20), pin('stall_c', 3000, 0)];
    const stallPins = vi.fn(async () => pins);
    const open = vi.fn();
    view({ stallPins, onOpenInReplay: open });
    fireEvent.click(screen.getByRole('tab', { name: 'All stalls' }));
    await waitFor(() => expect(screen.getByText(/^3 stalls\. 2 of them within \d+ m of one another/)).toBeTruthy());
    expect(stallPins).toHaveBeenCalledWith('stalls');
    const m = await map();
    expect(m.dataset.pins).toBe('stall,stall,stall');
    expect(m.dataset.track).toBe('0');
    fireEvent.click(screen.getByRole('button', { name: 'pin stall 2' }));
    expect(open).toHaveBeenCalledWith('stall_c', 20);
    fireEvent.click(screen.getByRole('button', { name: 'GeoJSON' }));
  });
});

describe('helpers', () => {
  it('resolves colour presets against the drive', () => {
    expect(resolveColour(drive, 'speed')).toBe('vss');
    expect(resolveColour(drive, 'rpm')).toBe('rpm');
    expect(resolveColour(drive, 'afr')).toBe('');
    expect(resolveColour(drive, 'tps')).toBe('tps');
    expect(channelsOf(drive)).toContain('gps_speed');
    expect(channelsOf(null)).toEqual([]);
  });
  it('words distances in the chosen units', () => {
    expect(distanceText(12_345, 'metric')).toBe('12.3 km');
    expect(distanceText(50, 'metric')).toBe('50 m');
    expect(distanceText(16_093.44, 'imperial')).toBe('10.0 mi');
    expect(distanceText(30, 'imperial')).toBe('98 ft');
  });
});

describe('stall pins from the drive source', () => {
  it('places each capture on its own track', async () => {
    const { loadStallPins, pinOf } = await import('./stallpins');
    const d = synthDrive('stall_20261126_230000');
    const cap = { ...d, info: { ...d.info, kind: 'stall' as const, reason: 'rpm' } };
    const p = pinOf(cap);
    expect(p.kind).toBe('stall');
    expect(p.lat).not.toBeNull();
    const source = { listDrives: async () => [cap.info, drive.info], loadDrive: async () => cap };
    const all = await act(async () => loadStallPins(source));
    expect(all.length).toBe(1);
    expect(all[0]!.name).toBe('stall_20261126_230000');
  });
});
