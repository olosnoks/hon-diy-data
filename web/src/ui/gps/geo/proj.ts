/**
 * Web Mercator, the projection of every slippy-map tile server, and distances
 * (port of hondalog/geo/proj.py).
 *
 * Positions are kept as "unit" coordinates: x and y in 0..1 over the whole
 * world (x east from 180° W, y south from 85.05° N). Pixel coordinates at zoom
 * z are unit coordinates times TILE * 2**z.
 */

export const TILE = 256;
export const MAX_LAT = 85.0511287798066;
export const EARTH_R = 6378137.0;
export const MEAN_R = 6371008.8;
export const MIN_ZOOM = 2;
export const MAX_ZOOM = 19;

const RAD = Math.PI / 180;

export function toUnit(lon: number, lat: number): [number, number] {
  const la = Math.max(-MAX_LAT, Math.min(MAX_LAT, lat));
  const x = (lon + 180) / 360;
  const s = Math.sin(la * RAD);
  const y = 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
  return [x, y];
}

export function fromUnit(x: number, y: number): [number, number] {
  const lon = x * 360 - 180;
  const lat = Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) / RAD;
  return [lon, lat];
}

export function worldPx(zoom: number): number {
  return TILE * 2 ** zoom;
}

export function metresPerPx(lat: number, zoom: number): number {
  return (Math.cos(lat * RAD) * 2 * Math.PI * EARTH_R) / worldPx(zoom);
}

export function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = lat1 * RAD;
  const p2 = lat2 * RAD;
  const dp = p2 - p1;
  const dl = (lon2 - lon1) * RAD;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * MEAN_R * Math.asin(Math.sqrt(Math.max(0, Math.min(1, a))));
}

export function bearingDeg(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = lat1 * RAD;
  const p2 = lat2 * RAD;
  const dl = (lon2 - lon1) * RAD;
  const y = Math.sin(dl) * Math.cos(p2);
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return (((Math.atan2(y, x) / RAD) % 360) + 360) % 360;
}

/** The point east_m / north_m metres away (flat earth, fine for a town). */
export function offset(lat: number, lon: number, eastM: number, northM: number): [number, number] {
  const dlat = northM / MEAN_R;
  const dlon = eastM / (MEAN_R * Math.cos(lat * RAD));
  return [lat + dlat / RAD, lon + dlon / RAD];
}

const STEPS = [1, 2, 5];

function g(v: number): string {
  // Python's :g for the round values the scale bar uses
  return String(Number(v.toPrecision(6)));
}

/** The longest round length that fits in maxPx: [pixels, label]. */
export function scaleBar(mPerPx: number, maxPx: number, miles = false): [number, string] {
  if (mPerPx <= 0 || maxPx <= 0) return [0, ''];
  const limit = mPerPx * maxPx;
  let best: [number, string] = [0, ''];
  if (miles) {
    const ft = 0.3048;
    const mile = 1609.344;
    const values = [0.25, 0.5];
    for (let e = 0; e < 4; e++) for (const k of STEPS) values.push(k * 10 ** e);
    for (const value of values) {
      if (value * mile <= limit) best = [(value * mile) / mPerPx, `${g(value)} mi`];
    }
    if (best[1]) return best;
    for (let e = 0; e < 5; e++) {
      for (const k of STEPS) {
        const value = k * 10 ** e;
        if (value * ft <= limit) best = [(value * ft) / mPerPx, `${g(value)} ft`];
      }
    }
    return best;
  }
  for (let e = 0; e < 7; e++) {
    for (const k of STEPS) {
      const value = k * 10 ** e;
      if (value <= limit) best = [value / mPerPx, value >= 1000 ? `${g(value / 1000)} km` : `${g(value)} m`];
    }
  }
  return best;
}

/** The largest whole zoom at which the unit box fits the viewport. */
export function fitZoom(
  x0: number, y0: number, x1: number, y1: number, widthPx: number, heightPx: number,
  marginPx = 40, lo = MIN_ZOOM, hi = MAX_ZOOM,
): number {
  const w = Math.max(x1 - x0, 1e-12);
  const h = Math.max(y1 - y0, 1e-12);
  const roomW = Math.max(1, widthPx - 2 * marginPx);
  const roomH = Math.max(1, heightPx - 2 * marginPx);
  const z = Math.floor(Math.log2(Math.min(roomW / (w * TILE), roomH / (h * TILE))));
  return Math.min(hi, Math.max(lo, z));
}
