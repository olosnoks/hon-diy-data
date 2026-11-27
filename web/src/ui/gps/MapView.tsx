/**
 * The map canvas of F6 (port of hondalog/ui/mapview.py on Leaflet): cached
 * OSM tiles in the night look, the track coloured by a channel (one colour
 * bin per run, grey where there is no value), pins, the cursor dot.
 * Clicking the track gives the time there; clicking a pin gives the pin.
 */
import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { Theme } from '../theme';
import { FONTS } from '../theme';
import { createOfflineTileLayer } from './TileLayer';
import { OSM_COPYRIGHT_URL, type TileFetcher } from './tiles';
import { binColours, bins, rgbHex } from './geo/ramps';
import { worldPx } from './geo/proj';
import type { Position, Track } from './geo/track';
import { pinSvg, type PinKind } from './pins';

export const COLOUR_BINS = 24;
export const NODATA = '#8C8C8C';

export interface MapPin {
  lat: number;
  lon: number;
  kind: PinKind;
  label?: string;
  title?: string;
  t?: number;
  id?: string | number;
  selected?: boolean;
}

export interface MapViewProps {
  theme: Theme;
  fetcher: TileFetcher | null;
  attribution: string;
  track: Track | null;
  values?: ArrayLike<number> | null;
  lo?: number;
  hi?: number;
  diverging?: boolean;
  pins: MapPin[];
  cursor?: Position | null;
  /** fit the view to the track (or the pins) whenever this changes */
  fitKey?: string;
  onTrackClick?: (t: number) => void;
  onPinClick?: (pin: MapPin) => void;
}

/** Runs of fixes with the same colour: [i0, i1 inclusive, colour]. Runs share
 *  their joining fix so the line has no holes. Gaps break runs. */
export function colourRuns(track: Track, values: ArrayLike<number> | null | undefined, lo: number, hi: number,
  diverging: boolean, fallback: string): Array<[number, number, string]> {
  const out: Array<[number, number, string]> = [];
  const palette = binColours(COLOUR_BINS, diverging).map(rgbHex);
  const b = values ? bins(values, lo, hi, COLOUR_BINS) : null;
  const colourOf = (i: number) => (b ? (b[i]! >= 0 ? palette[b[i]!]! : NODATA) : fallback);
  for (const [s0, s1] of track.segments()) {
    if (s1 - s0 < 2) continue;
    let start = s0;
    for (let j = s0 + 1; j <= s1; j++) {
      if (j === s1 || colourOf(j) !== colourOf(start)) {
        out.push([start, Math.min(j, s1 - 1), colourOf(start)]);
        start = j;
      }
    }
  }
  return out.filter(([a, z]) => z > a);
}

export default function MapView(props: MapViewProps) {
  const { theme, fetcher, attribution, track, values, lo = 0, hi = 1, diverging = false, pins, cursor, fitKey } = props;
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const trackLayer = useRef<L.LayerGroup | null>(null);
  const pinLayer = useRef<L.LayerGroup | null>(null);
  const cursorLayer = useRef<L.CircleMarker | null>(null);
  const handlers = useRef(props);
  handlers.current = props;

  // the map itself, once
  useEffect(() => {
    if (!host.current) return;
    const m = L.map(host.current, {
      zoomControl: true, attributionControl: true, preferCanvas: true, worldCopyJump: false,
      minZoom: 2, maxZoom: 19, zoomSnap: 1,
    }).setView([20, 0], 2);
    m.attributionControl.setPrefix(false);
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(m);
    trackLayer.current = L.layerGroup().addTo(m);
    pinLayer.current = L.layerGroup().addTo(m);
    m.on('click', (e: L.LeafletMouseEvent) => {
      const tr = handlers.current.track;
      if (!tr || tr.empty || !handlers.current.onTrackClick) return;
      const lon = e.latlng.lng, lat = e.latlng.lat;
      const x = (lon + 180) / 360;
      const s = Math.sin((lat * Math.PI) / 180);
      const y = 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
      const hit = tr.nearest(x, y, 14 / worldPx(m.getZoom()));
      if (hit) handlers.current.onTrackClick(hit[0]);
    });
    map.current = m;
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => m.invalidateSize()) : null;
    ro?.observe(host.current);
    return () => {
      ro?.disconnect();
      m.remove();
      map.current = null;
    };
  }, []);

  // fit (before the tiles are added, so the world view's tiles are never asked for)
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    let pts: L.LatLngTuple[] = [];
    if (track && !track.empty) {
      const [x0, y0, x1, y1] = track.bbox();
      const lonOf = (x: number) => x * 360 - 180;
      const latOf = (y: number) => (Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180) / Math.PI;
      pts = [[latOf(y1), lonOf(x0)], [latOf(y0), lonOf(x1)]];
    } else if (pins.length) {
      pts = pins.map((p) => [p.lat, p.lon]);
    }
    if (pts.length) {
      try { m.fitBounds(L.latLngBounds(pts), { padding: [40, 40], maxZoom: 17, animate: false }); } catch { /* zero-size map (hidden) */ }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey]);

  // tiles
  useEffect(() => {
    const m = map.current;
    if (!m || !fetcher) return;
    const layer = createOfflineTileLayer(fetcher, {
      attribution: attribution ? `<a href="${OSM_COPYRIGHT_URL}" target="_blank" rel="noopener noreferrer">${attribution}</a>` : '',
    });
    layer.addTo(m);
    return () => { layer.remove(); };
  }, [fetcher, attribution]);

  // the track
  useEffect(() => {
    const g = trackLayer.current;
    if (!g) return;
    g.clearLayers();
    if (!track || track.empty) return;
    const ll = (i: number): L.LatLngTuple => [track.lat[i]!, track.lon[i]!];
    for (const [a, b] of track.segments()) {
      if (b - a < 2) continue;
      const pts: L.LatLngTuple[] = [];
      for (let i = a; i < b; i++) pts.push(ll(i));
      L.polyline(pts, { color: theme.bg, weight: 8, opacity: 0.9, interactive: false }).addTo(g);
    }
    for (const [a, b, colour] of colourRuns(track, values, lo, hi, diverging, theme.accent)) {
      const pts: L.LatLngTuple[] = [];
      for (let i = a; i <= b; i++) pts.push(ll(i));
      L.polyline(pts, { color: colour, weight: 5, opacity: 1, interactive: false }).addTo(g);
    }
  }, [track, values, lo, hi, diverging, theme]);

  // pins
  useEffect(() => {
    const g = pinLayer.current;
    if (!g) return;
    g.clearLayers();
    for (const p of [...pins].sort((a, b) => Number(!!a.selected) - Number(!!b.selected))) {
      const size = p.kind === 'start' ? 20 : 22;
      const icon = L.divIcon({ html: pinSvg(p.kind, theme, size, p.label ?? '', !!p.selected), className: 'hl-pin', iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
      const mk = L.marker([p.lat, p.lon], { icon, title: p.title ?? '', keyboard: true, riseOnHover: true, zIndexOffset: p.selected ? 1000 : 0 });
      mk.on('click', () => handlers.current.onPinClick?.(p));
      mk.addTo(g);
    }
  }, [pins, theme]);

  // the cursor dot
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    cursorLayer.current?.remove();
    cursorLayer.current = null;
    if (!cursor) return;
    cursorLayer.current = L.circleMarker([cursor.lat, cursor.lon], {
      radius: 8, color: theme.fg, weight: 3, fillColor: theme.livePoint, fillOpacity: 1, interactive: false,
    }).addTo(m);
  }, [cursor, theme]);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', minHeight: 0 }}>
      <style>{`
        .hl-map .leaflet-container { background: ${theme.sunk}; font-family: ${FONTS.sans}; }
        .hl-map .hl-tile[data-state="cached"], .hl-map .hl-tile[data-state="fetched"] {
          filter: invert(1) hue-rotate(180deg) brightness(0.58) contrast(1.05); }
        .hl-map .hl-tile-missing { background-color: ${theme.sunk};
          background-image: linear-gradient(${theme.line} 1px, transparent 1px), linear-gradient(90deg, ${theme.line} 1px, transparent 1px);
          background-size: 64px 64px !important; }
        .hl-map .hl-pin { background: none; border: none; }
        .hl-map .leaflet-control-attribution { background: ${theme.panel}; color: ${theme.fg2}; font-size: 11.5px; }
        .hl-map .leaflet-control-attribution a { color: ${theme.accent}; }
        .hl-map .leaflet-control-scale-line { background: ${theme.panel}; color: ${theme.fg}; border-color: ${theme.fg}; font-family: ${FONTS.mono}; }
        .hl-map .leaflet-bar a { background: ${theme.panel}; color: ${theme.fg}; border-bottom-color: ${theme.line}; }
        .hl-map .leaflet-bar a:hover { background: ${theme.sunk}; }
      `}</style>
      <div className="hl-map" style={{ position: 'absolute', inset: 0 }}>
        <div ref={host} data-testid="leaflet-host" style={{ width: '100%', height: '100%' }} />
      </div>
    </div>
  );
}
