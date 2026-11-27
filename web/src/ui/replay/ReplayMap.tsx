/**
 * Replay's GPS map: F6's map canvas (src/ui/gps/MapView, cached OSM tiles in
 * the night look) with the drive's track coloured by a chosen channel, the
 * cursor's position and the events as pins. Without tiles (offline, nothing
 * cached, tiles switched off) the track still draws on a plain grid.
 */
import { useMemo, useState } from 'react';
import type { LoadedDrive } from '../../core/types';
import { valueAt } from '../../core/analysis/cursors';
import MapView, { COLOUR_BINS, type MapPin } from '../gps/MapView';
import { Track, place } from '../gps/geo/track';
import { binColours, rgbHex, valueRange } from '../gps/geo/ramps';
import { loadMapSettings } from '../gps/settings';
import { defaultStore, isOsm, OSM_ATTRIBUTION, sourceFor, TileFetcher } from '../gps/tiles';
import type { PinKind } from '../gps/pins';
import { FONTS, type Theme } from '../theme';
import { channelMeta, num } from './channels';
import type { DriveEvent, EventKind } from '../events/detect';

export const COLOUR_CHANNELS = ['vss', 'rpm', 'tps', 'map', 'lat_g', 'accel_g', 'gear', 'power'];

const PIN: Partial<Record<EventKind, PinKind>> = { stall: 'stall', near_stall: 'nearstall', dropout: 'dropout', mark: 'marker', harsh: 'alarm' };

let shared: { fetcher: TileFetcher | null; attribution: string } | null = null;

/** One tile fetcher for Replay (the same cache as F6). */
function tiles(): { fetcher: TileFetcher | null; attribution: string } {
  if (shared) return shared;
  try {
    const s = loadMapSettings();
    const src = sourceFor(s.tileUrl, s.attribution);
    shared = { fetcher: new TileFetcher(defaultStore(src, s.cacheMb), src, { online: s.online }),
      attribution: s.attribution || (isOsm(src) ? OSM_ATTRIBUTION : '') };
  } catch (err) {
    console.error('map tiles unavailable', err);
    shared = { fetcher: null, attribution: '' };
  }
  return shared;
}

interface Props {
  drive: LoadedDrive;
  theme: Theme;
  /** drive time of the cursor */
  cursorT: number;
  events: DriveEvent[];
  selected?: string | null;
  colourBy: string;
  onColourBy(ch: string): void;
  /** drive time picked on the track or a pin */
  onPick(t: number): void;
}

export function ReplayMap({ drive, theme: t, cursorT, events, selected, colourBy, onColourBy, onPick }: Props) {
  const [tilesOn, setTilesOn] = useState(true);
  const track = useMemo(() => Track.fromDrive(drive), [drive]);
  const series = drive.series[colourBy] ?? (colourBy === 'vss' ? drive.series.gps_speed : undefined);
  const values = useMemo(() => (series ? Float64Array.from(track.t, (tt) => valueAt(series, tt)) : null), [series, track]);
  const [lo, hi, diverging] = useMemo(() => (values ? valueRange(colourBy, values) : [0, 1, false] as [number, number, boolean]), [values, colourBy]);
  const pins = useMemo<MapPin[]>(() => events.flatMap((e) => {
    const kind = PIN[e.kind];
    const at = kind ? place(track, e.t) : null;
    return kind && at ? [{ lat: at[0], lon: at[1], kind, t: e.t, id: e.id, title: e.title, selected: e.id === selected }] : [];
  }), [events, track, selected]);
  const cursor = useMemo(() => {
    const pos = track.at(cursorT);
    if (pos || track.empty) return pos;
    // before the first fix or in a short GPS gap: the nearest fix within 5 s
    let best = -1, bd = 5;
    for (let i = 0; i < track.t.length; i++) { const d = Math.abs(track.t[i]! - cursorT); if (d <= bd) { bd = d; best = i; } }
    return best >= 0 ? track.at(track.t[best]!) : null;
  }, [track, cursorT]);
  const tileInfo = tilesOn ? tiles() : { fetcher: null, attribution: '' };
  const meta = channelMeta(colourBy, drive);
  const ramp = binColours(COLOUR_BINS, diverging).map(rgbHex);

  if (track.empty) {
    return (
      <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: t.fg2, fontSize: 14, fontWeight: 600, padding: 16, textAlign: 'center' }}>
        No GPS in this drive. The CYD logs positions when it has a fix.
      </div>
    );
  }
  return (
    <div style={{ position: 'relative', height: '100%' }}>
      <MapView theme={t} fetcher={tileInfo.fetcher} attribution={tileInfo.attribution} track={track} values={values}
        lo={lo} hi={hi} diverging={diverging} pins={pins} cursor={cursor} fitKey={drive.info.name}
        onTrackClick={onPick} onPinClick={(p) => p.t !== undefined && onPick(p.t)} />
      <div style={{ position: 'absolute', left: 48, top: 8, zIndex: 500, display: 'flex', gap: 6, alignItems: 'center', background: t.panel + 'E6', padding: '3px 6px', fontFamily: FONTS.sans }}>
        <label style={{ fontSize: 11, fontWeight: 700, color: t.fg }}>
          GPS · coloured by{' '}
          <select aria-label="Colour the track by" value={colourBy} onChange={(e) => onColourBy(e.target.value)}
            style={{ background: t.bg, color: t.fg, border: `1px solid ${t.rule}`, fontFamily: FONTS.sans, fontSize: 11, fontWeight: 700 }}>
            {COLOUR_CHANNELS.filter((c) => drive.series[c] || (c === 'vss' && drive.series.gps_speed)).map((c) => (
              <option key={c} value={c}>{channelMeta(c, drive).label.toLowerCase()}</option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => setTilesOn((v) => !v)} aria-pressed={tilesOn}
          style={{ fontSize: 11, fontWeight: 700, background: tilesOn ? t.sunk : t.bg, color: t.fg, border: `1px solid ${t.rule}`, cursor: 'pointer' }}>
          {tilesOn ? 'Tiles on' : 'Tiles off'}
        </button>
      </div>
      <div style={{ position: 'absolute', left: 8, bottom: 22, zIndex: 500, display: 'flex', gap: 6, alignItems: 'center', background: t.panel + 'E6', padding: '2px 6px', fontSize: 10.5, fontWeight: 600, color: t.fg2 }}>
        <span>{num(lo, meta.decimals)}</span>
        <span style={{ width: 90, height: 6, background: `linear-gradient(90deg, ${ramp.join(', ')})` }} />
        <span>{num(hi, meta.decimals)} {meta.units}</span>
        <span style={{ marginLeft: 6 }}>● near-stall ■ dropout</span>
      </div>
    </div>
  );
}
