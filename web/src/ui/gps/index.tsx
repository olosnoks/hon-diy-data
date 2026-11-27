/**
 * F6 GPS map (port of hondalog/ui/screens/gpsmap.py).
 *
 * Two views:
 * - Drive: the track of the chosen drive, coloured by speed, RPM, MAP, AFR or
 *   any other channel, every stall, near-stall, link dropout, alarm and mark
 *   pinned. Clicking the track or a pin puts the cursor there.
 * - All stalls: every stall capture where it happened, the biggest group in
 *   one place named. Clicking a pin opens that capture in Replay.
 *
 * Offline first: map tiles come from the device's cache; with "Download map
 * tiles" on, missing ones are asked of OpenStreetMap (two at a time, only
 * what the screen shows). No position ever leaves the device: the only
 * requests are tile URLs, and those can be switched off.
 */
import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { LoadedDrive } from '../../core/types';
import { useSession, type UnitSystem } from '../../core/session';
import { useTheme } from '../shell/useTheme';
import { FONTS, type Theme } from '../theme';
import { useDriveSource, type DriveSource } from '../replay/source';
import { openInReplay, useReplayNav } from '../replay/nav';
import { Button, DriveSelect, Empty, H2, Row, Section, Tabs } from '../power/widgets';
import { useDrive } from '../power/useDrive';
import { exportDrive, speedChannel, channelSeries, channelUnits, type GeoFormat } from './geo/exporters';
import { DIV_RAMP, SEQ_RAMP, valueRange } from './geo/ramps';
import { stallsGeojson, summary, type StallPin } from './geo/stalls';
import { Track, type Position } from './geo/track';
import { driveWaypoints, markLabel, type Waypoint } from './geo/waypoints';
import { NODATA, type MapPin } from './MapView';
import { pinSvg, PIN_LABEL, type PinKind } from './pins';
import { saveText, type SaveFn } from './save';
import { loadMapSettings, saveMapSettings, type MapSettings } from './settings';
import { loadStallPins } from './stallpins';
import { defaultStore, isValid, sourceFor, TileFetcher } from './tiles';

const MapView = lazy(() => import('./MapView'));

/** (preset, label, the channels that can stand for it, first with data wins) */
export const PRESETS: Array<[string, string, string[]]> = [
  ['speed', 'Speed', ['vss', 'gps_speed']], ['rpm', 'RPM', ['rpm']], ['map', 'MAP', ['map']], ['afr', 'AFR', ['afr_est', 'afr']],
];
export const SKIP_CHANNELS = new Set(['gps_lat', 'gps_lon', 'gps_utc', 'gps_fix', 'gps_hdop', 'gps_sats', 'gps_heading', 'bbmark', 'link']);

export function channelsOf(drive: LoadedDrive | null): string[] {
  if (!drive) return [];
  const out = Object.keys(drive.series ?? {}).filter((n) => !SKIP_CHANNELS.has(n) && drive.series[n]!.v.some((v) => Number.isFinite(v)));
  if (drive.gps?.t?.length && !out.includes('gps_speed')) out.push('gps_speed');
  return out;
}

/** a preset (or a channel name) -> the channel of this drive that shows it, '' = none */
export function resolveColour(drive: LoadedDrive | null, colour: string): string {
  const have = channelsOf(drive);
  const preset = PRESETS.find(([k]) => k === colour);
  if (preset) {
    if (colour === 'speed' && drive) { const s = speedChannel(drive); return have.includes(s) ? s : ''; }
    return preset[2].find((c) => have.includes(c)) ?? '';
  }
  return have.includes(colour) ? colour : '';
}

export function labelOf(drive: LoadedDrive | null, cid: string): string {
  if (cid === 'gps_speed') return 'GPS speed';
  const spec = drive?.map?.channels.find((c) => c.name === cid) ?? drive?.map?.math.find((m) => m.name === cid);
  return spec?.label ?? cid;
}

function speedText(kmh: number | null | undefined, units: UnitSystem): string {
  if (kmh == null || !Number.isFinite(kmh)) return '---';
  return units === 'imperial' ? `${(kmh / 1.609344).toFixed(0)} mph` : `${kmh.toFixed(0)} km/h`;
}

export function distanceText(m: number, units: UnitSystem): string {
  if (units === 'imperial') {
    const mi = m / 1609.344;
    return mi >= 0.1 ? `${mi.toFixed(1)} mi` : `${(m / 0.3048).toFixed(0)} ft`;
  }
  return m >= 100 ? `${(m / 1000).toFixed(1)} km` : `${m.toFixed(0)} m`;
}

function num(v: number, decimals = 0): string {
  return Number.isFinite(v) ? v.toFixed(decimals).replace('-', '−') : '---';
}

function signed(t: number): string {
  const sign = t < -0.05 ? '−' : t > 0.05 ? '+' : '';
  return `${sign}${Math.abs(t).toFixed(1)}`;
}

function PinIcon({ kind, t, label = '', size = 18 }: { kind: PinKind; t: Theme; label?: string; size?: number }) {
  return <span aria-hidden style={{ display: 'inline-flex', width: size, height: size }} dangerouslySetInnerHTML={{ __html: pinSvg(kind, t, size, label) }} />;
}

export interface Tiles { fetcher: TileFetcher | null; attribution: string }

export interface GpsViewProps {
  t: Theme;
  drive: LoadedDrive | null;
  header?: ReactNode;
  tiles: Tiles;
  settings: MapSettings;
  onSettings(s: MapSettings): void;
  onClearCache?(): Promise<void>;
  cacheBytes?: number;
  units?: UnitSystem;
  /** a moment to show (Events -> "Show on GPS map") */
  focusT?: { t: number; seq: number } | null;
  stallPins?: (kinds: 'stalls' | 'all') => Promise<StallPin[]>;
  onOpenInReplay?(drive: string, t: number): void;
  save?: SaveFn;
}

export function GpsView(props: GpsViewProps) {
  const { t, drive, header, tiles, settings, onSettings, units = 'metric', focusT, save = saveText } = props;
  const [mode, setMode] = useState<'drive' | 'stalls'>('drive');
  const [colour, setColour] = useState('speed');
  const [cursorT, setCursorT] = useState<number | null>(null);
  const [note, setNote] = useState('');
  const [pins, setPins] = useState<StallPin[]>([]);
  const [kinds, setKinds] = useState<'stalls' | 'all'>('stalls');
  const [selected, setSelected] = useState(-1);
  const [loadingPins, setLoadingPins] = useState(false);

  const track = useMemo(() => Track.fromDrive(drive), [drive]);
  const waypoints = useMemo(() => driveWaypoints(drive), [drive]);
  const cid = useMemo(() => resolveColour(drive, colour), [drive, colour]);
  const colouring = useMemo(() => {
    if (!drive || track.empty || !cid) return null;
    const s = channelSeries(drive, cid);
    if (!s) return null;
    const values = track.sample(s.t, s.v);
    const [lo, hi, diverging] = valueRange(cid, values);
    return { values, lo, hi, diverging, units: channelUnits(drive, cid) };
  }, [drive, track, cid]);

  useEffect(() => { setCursorT(null); }, [drive]);
  useEffect(() => { if (focusT) { setMode('drive'); setCursorT(focusT.t); } }, [focusT?.seq]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (mode !== 'stalls' || !props.stallPins) return;
    let live = true;
    setLoadingPins(true);
    props.stallPins(kinds).then((p) => { if (live) { setPins(p); setSelected(-1); } })
      .catch((e: unknown) => live && setNote(`Could not read the captures: ${String(e)}`))
      .finally(() => live && setLoadingPins(false));
    return () => { live = false; };
  }, [mode, kinds, props.stallPins]); // eslint-disable-line react-hooks/exhaustive-deps

  const cursor: Position | null = cursorT != null ? track.at(cursorT) : null;

  const mapPins: MapPin[] = useMemo(() => {
    if (mode === 'stalls') {
      return pins.flatMap((p, i) => (p.lat != null && p.lon != null ? [{
        lat: p.lat, lon: p.lon, kind: (p.kind === 'stall' ? 'stall' : p.kind === 'dropout' ? 'dropout' : p.kind === 'nearstall' ? 'nearstall' : 'capture') as PinKind,
        title: `${p.name}${p.message ? `: ${p.message}` : ''}`, id: i, selected: i === selected, t: p.tTrigger,
      }] : []));
    }
    if (track.empty) return [];
    const out: MapPin[] = [{ lat: track.lat[0]!, lon: track.lon[0]!, kind: 'start', title: 'start of the track', t: track.t[0]! }];
    waypoints.forEach((w, i) => {
      const pos = track.at(w.t);
      let lat = pos?.lat, lon = pos?.lon;
      if (lat == null) {
        let best = -1, bd = Infinity;
        for (let k = 0; k < track.length; k++) { const d = Math.abs(track.t[k]! - w.t); if (d < bd) { bd = d; best = k; } }
        if (best >= 0 && bd <= 60) { lat = track.lat[best]; lon = track.lon[best]; }
      }
      if (lat == null || lon == null) return;
      out.push({ lat, lon, kind: w.kind, label: w.kind === 'marker' ? markLabel(w.name) : '', title: `${PIN_LABEL[w.kind]}: ${w.name}${w.text && w.text !== w.name ? ` · ${w.text}` : ''}`, t: w.t, id: i });
    });
    return out;
  }, [mode, pins, selected, track, waypoints]);

  const fitKey = mode === 'stalls' ? `stalls:${kinds}:${pins.length}` : `drive:${drive?.info.name ?? ''}:${track.length}`;

  const onPin = (p: MapPin) => {
    if (mode === 'stalls') {
      const sp = pins[Number(p.id)];
      setSelected(Number(p.id));
      if (sp) props.onOpenInReplay?.(sp.drive, sp.tTrigger);
      return;
    }
    if (p.t != null) setCursorT(p.t);
  };

  const doExport = (fmt: GeoFormat) => {
    try {
      if (mode === 'stalls') {
        const placed = pins.filter((p) => p.lat != null).length;
        if (!pins.length) { setNote('No stalls to export.'); return; }
        save('stalls.geojson', 'application/geo+json', JSON.stringify(stallsGeojson(pins), null, 1));
        setNote(`Saved stalls.geojson (${placed} with a place) on this device.`);
        return;
      }
      if (!drive) { setNote('Open a drive with GPS first.'); return; }
      const out = exportDrive(drive, fmt, cid || null);
      save(out.filename, out.mime, out.text);
      setNote(`Saved ${out.filename} on this device.`);
    } catch (e) {
      setNote(`Export failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  // ---- texts ----
  const presetLabel = PRESETS.find(([k]) => k === colour)?.[1] ?? labelOf(drive, colour);
  const caption = mode === 'stalls'
    ? (pins.length ? `${pins.filter((p) => p.lat != null).length} of ${pins.length} placed. Octagon = stall, square = dropout, diamond = other capture. Click a pin to open it in Replay.` : loadingPins ? 'Reading the captures…' : 'None match yet.')
    : !drive ? 'Pick a drive; its GPS track shows here.'
      : track.empty ? 'No GPS in this drive: the GPS comes from the handheld (its SD card, or live over the CYD link).'
        : `Coloured by ${cid ? labelOf(drive, cid) : 'nothing (not in this drive)'}. Click the track to put the cursor there.`;
  const channels = channelsOf(drive);
  const cursorValue = (() => {
    if (!cursor || !drive) return ['Value', '---'];
    if (!cid || cid === 'gps_speed') {
      const i = Math.floor(cursor.index), f = cursor.index - i;
      const d = track.dist[i]! + (i + 1 < track.length ? (track.dist[i + 1]! - track.dist[i]!) * f : 0);
      return ['From start', distanceText(d, units)];
    }
    const s = channelSeries(drive, cid);
    if (!s) return [labelOf(drive, cid), '---'];
    let k = -1;
    for (let j = 0; j < s.t.length && s.t[j]! <= cursor.t; j++) k = j;
    return [labelOf(drive, cid), k >= 0 ? `${num(s.v[k]!, Math.abs(s.v[k]!) < 20 ? 1 : 0)}${colouring?.units ? ` ${colouring.units}` : ''}` : '---'];
  })();

  return (
    <div style={{ height: '100%', minHeight: 0, display: 'flex', background: t.bg, color: t.fg, fontFamily: FONTS.sans }}>
      <section aria-label="GPS map" style={{ flex: '1 1 0', minWidth: 0, display: 'flex', flexDirection: 'column', padding: '10px 14px', gap: 8 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <Tabs t={t} label="View" value={mode} onChange={setMode} items={[['drive', 'Drive'], ['stalls', 'All stalls']]} />
          <span style={{ fontSize: 12.5, color: t.fg2, flex: 1, minWidth: 200 }}>{caption}</span>
        </div>
        <figure style={{ margin: 0, flexGrow: 1, minHeight: 0, border: `1px solid ${t.rule}`, position: 'relative' }}>
          <Suspense fallback={<Empty t={t} title="Loading the map…" />}>
            <MapView theme={t} fetcher={tiles.fetcher} attribution={tiles.attribution}
              track={mode === 'drive' ? track : null} values={colouring?.values ?? null} lo={colouring?.lo} hi={colouring?.hi}
              diverging={colouring?.diverging} pins={mapPins} cursor={mode === 'drive' ? cursor : null} fitKey={fitKey}
              onTrackClick={(tt) => setCursorT(tt)} onPinClick={onPin} />
          </Suspense>
          {!settings.online && <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 500, background: t.panel, border: `1px solid ${t.rule}`, padding: '4px 8px', fontSize: 12, fontWeight: 700 }}>offline: cached tiles only</div>}
        </figure>
      </section>

      <aside aria-label="Map panel" style={{ width: 'clamp(250px, 26vw, 360px)', flexShrink: 0, borderLeft: `2px solid ${t.rule}`, display: 'flex', flexDirection: 'column', overflowY: 'auto' }}>
        <Section t={t}>{header}</Section>
        {mode === 'drive' ? <>
          <Section t={t}>
            <H2>COLOUR</H2>
            <Tabs t={t} label="Colour the track by" value={PRESETS.some(([k]) => k === colour) ? colour : ('' as string)} onChange={setColour}
              items={PRESETS.map(([k, l]) => [k, l] as [string, string])} />
            <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12.5, fontWeight: 600 }}>
              Other channel
              <select aria-label="Other channel" value={PRESETS.some(([k]) => k === colour) ? '' : colour} onChange={(e) => e.target.value && setColour(e.target.value)}
                style={{ flex: 1, minWidth: 0, fontFamily: FONTS.mono, fontSize: 12.5, color: t.fg, background: t.sunk, border: `1px solid ${t.line}`, minHeight: 26 }}>
                <option value="">…</option>
                {channels.map((c) => <option key={c} value={c}>{labelOf(drive, c)}</option>)}
              </select>
            </label>
            <div aria-label="Colour legend">
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>{colouring ? `${labelOf(drive, cid)}${colouring.units ? ` (${colouring.units})` : ''}` : `${presetLabel}: not in this drive`}</div>
              <div style={{ height: 16, border: `1px solid ${t.rule}`, background: colouring ? `linear-gradient(90deg, ${(colouring.diverging ? DIV_RAMP : SEQ_RAMP).join(', ')})` : NODATA }} />
              <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: FONTS.mono, fontSize: 12, fontWeight: 700 }}>
                {colouring ? [colouring.lo, (colouring.lo + colouring.hi) / 2, colouring.hi].map((v, i) => <span key={i}>{num(v, Math.abs(colouring.hi - colouring.lo) < 20 ? 1 : 0)}</span>) : <span>&nbsp;</span>}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, marginTop: 4 }}>
                <span style={{ width: 18, height: 10, background: NODATA, border: `1px solid ${t.rule}` }} /> grey = no value there
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 10px', marginTop: 6, fontSize: 12, fontWeight: 700 }}>
                {(['stall', 'nearstall', 'dropout', 'capture', 'alarm', 'marker'] as PinKind[]).map((k) => (
                  <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><PinIcon kind={k} t={t} label={k === 'marker' ? '1' : ''} size={16} />{PIN_LABEL[k]}</span>
                ))}
              </div>
            </div>
          </Section>
          <Section t={t}>
            <H2>AT THE CURSOR</H2>
            {cursor ? <>
              <Row label="Time">{signed(cursor.t)} s</Row>
              <Row label="GPS speed">{speedText(cursor.speedKmh, units)}</Row>
              <Row label={cursorValue[0]}>{cursorValue[1]}</Row>
              <Row label="Heading">{Number.isFinite(cursor.heading) ? `${cursor.heading.toFixed(0)}°` : '---'}</Row>
              <div style={{ fontFamily: FONTS.mono, fontSize: 12, fontWeight: 700 }}>{cursor.lat.toFixed(6)}, {cursor.lon.toFixed(6)}</div>
              {props.onOpenInReplay && drive && <Button t={t} onClick={() => props.onOpenInReplay!(drive.info.name, cursor.t)}>Open in Replay</Button>}
            </> : <div style={{ fontSize: 12.5, color: t.fg2 }}>{cursorT != null ? 'No GPS fix at that moment.' : 'Click the track or a pin.'}</div>}
          </Section>
          <Section t={t}>
            <H2>TRACK</H2>
            <Row label="Distance">{track.empty ? '---' : distanceText(track.lengthM, units)}</Row>
            <Row label="Time">{track.empty ? '---' : track.duration >= 120 ? `${(track.duration / 60).toFixed(0)} min` : `${track.duration.toFixed(0)} s`}</Row>
            <div style={{ fontFamily: FONTS.mono, fontSize: 12, fontWeight: 700 }}>{track.empty ? 'no GPS fixes' : `${track.length} fixes · ${track.gaps} gap${track.gaps !== 1 ? 's' : ''} over 5 s`}</div>
          </Section>
          <Section t={t}>
            <H2>PINS · {waypoints.length}</H2>
            <div role="list" aria-label="Pins" style={{ display: 'flex', flexDirection: 'column', maxHeight: 180, overflowY: 'auto' }}>
              {waypoints.map((w: Waypoint, i) => (
                <button key={i} type="button" role="listitem" onClick={() => setCursorT(w.t)}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 2px', background: cursorT === w.t ? t.sunk : 'transparent', color: t.fg, border: 'none', borderBottom: `1px solid ${t.line}`, cursor: 'pointer', textAlign: 'left', fontFamily: FONTS.sans }}>
                  <PinIcon kind={w.kind} t={t} label={w.kind === 'marker' ? markLabel(w.name) : ''} />
                  <span style={{ fontFamily: FONTS.mono, fontSize: 12, fontWeight: 700, width: 64 }}>{w.t.toFixed(1)} s</span>
                  <span style={{ fontSize: 12.5, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{w.name}</span>
                </button>
              ))}
              {!waypoints.length && <span style={{ fontSize: 12.5, color: t.fg2 }}>No stalls, dropouts or marks in this drive.</span>}
            </div>
          </Section>
          <Section t={t}>
            <H2>EXPORT</H2>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 }}>
              <Button t={t} onClick={() => doExport('gpx')} disabled={track.empty}>GPX</Button>
              <Button t={t} onClick={() => doExport('kml')} disabled={track.empty}>KML</Button>
              <Button t={t} onClick={() => doExport('geojson')} disabled={track.empty}>GeoJSON</Button>
            </div>
          </Section>
        </> : <>
          <Section t={t}>
            <H2>WHERE THEY HAPPEN</H2>
            <Tabs t={t} label="Captures" value={kinds} onChange={setKinds} items={[['stalls', 'Stalls'], ['all', 'Every capture']]} />
            <div style={{ fontSize: 13, fontWeight: 700, lineHeight: 1.45 }}>{loadingPins ? 'Reading the captures…' : summary(pins, undefined, (k) => speedText(k, units), (m) => distanceText(m, units))}</div>
          </Section>
          <Section t={t}>
            <H2>CAPTURES · FASTEST IN THE 30 S BEFORE</H2>
            <div role="list" aria-label="Captures" style={{ display: 'flex', flexDirection: 'column', maxHeight: 300, overflowY: 'auto' }}>
              {pins.map((p, i) => (
                <button key={p.name} type="button" role="listitem" onClick={() => setSelected(i)} onDoubleClick={() => props.onOpenInReplay?.(p.drive, p.tTrigger)}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 2px', background: i === selected ? t.fg : i % 2 ? t.sunk : 'transparent', color: i === selected ? t.bg : t.fg, border: 'none', cursor: 'pointer', textAlign: 'left', fontFamily: FONTS.sans }}>
                  <PinIcon kind={p.kind === 'stall' ? 'stall' : p.kind === 'dropout' ? 'dropout' : 'capture'} t={t} />
                  <span style={{ fontFamily: FONTS.mono, fontSize: 12, fontWeight: 700, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
                  <span style={{ fontSize: 12, fontWeight: 700 }}>{p.reason || p.kind}</span>
                  <span style={{ fontFamily: FONTS.mono, fontSize: 12, fontWeight: 700, width: 64, textAlign: 'right' }}>{p.lat != null ? speedText(p.maxSpeed30s, units) : 'no fix'}</span>
                </button>
              ))}
              {!pins.length && !loadingPins && <span style={{ fontSize: 12.5, color: t.fg2 }}>None yet.</span>}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
              <Button t={t} onClick={() => doExport('geojson')} disabled={!pins.length}>GeoJSON</Button>
              <Button t={t} onClick={() => { const p = pins[selected]; if (p) props.onOpenInReplay?.(p.drive, p.tTrigger); }} disabled={selected < 0}>Open in Replay</Button>
            </div>
          </Section>
        </>}
        <Section t={t} last>
          <H2>MAP TILES</H2>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12.5, fontWeight: 700 }}>
            <input type="checkbox" checked={settings.online} onChange={(e) => onSettings({ ...settings, online: e.target.checked })} />
            Download map tiles (OpenStreetMap)
          </label>
          <div style={{ fontSize: 11.5, color: t.fg2, lineHeight: 1.4 }}>
            Tiles you have seen are kept on this device for offline use. A tile request tells the tile server which area you are looking at; nothing else ever leaves the device. Off: cached tiles only, no network.
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
            <span style={{ fontFamily: FONTS.mono, fontWeight: 700 }}>{props.cacheBytes != null ? `${props.cacheBytes < 1e6 ? `${Math.round(props.cacheBytes / 1e3)} kB` : `${(props.cacheBytes / 1e6).toFixed(1)} MB`} cached` : ''}</span>
            <div style={{ flexGrow: 1 }} />
            {props.onClearCache && <Button t={t} onClick={() => { void props.onClearCache!().then(() => setNote('Cached tiles deleted.')); }}>Clear cached tiles</Button>}
          </div>
          <div role="status" style={{ fontSize: 12, color: t.fg2, minHeight: 16 }}>{note}</div>
        </Section>
      </aside>
    </div>
  );
}

/** The tile fetcher for the settings, rebuilt when the source changes. */
export function useTiles(settings: MapSettings): Tiles & { bytes: number; clear(): Promise<void> } {
  const source = useMemo(() => sourceFor(settings.tileUrl, settings.attribution), [settings.tileUrl, settings.attribution]);
  const fetcher = useMemo(() => (isValid(source) ? new TileFetcher(defaultStore(source, settings.cacheMb), source, { online: settings.online }) : null),
    [source, settings.cacheMb]); // eslint-disable-line react-hooks/exhaustive-deps
  const [bytes, setBytes] = useState(0);
  useEffect(() => { fetcher?.setOnline(settings.online); }, [fetcher, settings.online]);
  const prev = useRef<TileFetcher | null>(null);
  useEffect(() => {
    // a new source: the old fetcher stops (StrictMode re-runs effects, so not in a cleanup)
    if (prev.current && prev.current !== fetcher) prev.current.close();
    prev.current = fetcher;
    if (!fetcher) return;
    setBytes(fetcher.store.sizeBytes());
    return fetcher.onResult((r) => { if (r.state === 'fetched') setBytes(fetcher.store.sizeBytes()); });
  }, [fetcher]);
  return {
    fetcher, attribution: source.attribution, bytes,
    clear: async () => { if (fetcher) { await fetcher.store.clear(); setBytes(0); } },
  };
}

/** F6 GPS map: the drive from the shared drive source, the moment from Events' "Show on GPS map". */
export default function GpsScreen() {
  const t = useTheme();
  const units = useSession((s) => s.units);
  const focus = useReplayNav((s) => s.gpsFocus);
  const ds = useDrive(focus?.drive ?? null);
  const source = useDriveSource((s) => s.source);
  const [settings, setSettings] = useState<MapSettings>(loadMapSettings);
  const tiles = useTiles(settings);
  const onSettings = (s: MapSettings) => { setSettings(s); saveMapSettings(s); };
  const stallPins = useMemo(() => (kinds: 'stalls' | 'all') => loadStallPins(source as DriveSource, kinds), [source]);
  const header = (
    <div>
      <DriveSelect t={t} drives={ds.drives} value={ds.name} onChange={ds.open} />
      {ds.loading && <div style={{ fontSize: 11.5, color: t.fg2 }}>Loading…</div>}
      {ds.error && <div style={{ fontSize: 11.5, color: t.alarm }}>{ds.error}</div>}
    </div>
  );
  return (
    <GpsView t={t} drive={ds.drive} header={header} tiles={tiles} settings={settings} onSettings={onSettings}
      onClearCache={tiles.clear} cacheBytes={tiles.bytes} units={units}
      focusT={focus && focus.drive === ds.name ? focus : null} stallPins={stallPins} onOpenInReplay={openInReplay} />
  );
}
