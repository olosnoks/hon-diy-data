/**
 * Which stalls happened at the same place (port of hondalog/geo/stalls.py,
 * without the SQL: the pins come from the drive store).
 */
import { GENERATOR } from './exporters';
import { haversineM } from './proj';

export const CLUSTER_RADIUS_M = 150;

export interface StallPin {
  name: string;
  kind: string;            // stall, nearstall, dropout, capture...
  reason: string;
  message: string;
  drive: string;
  wallUnix: number | null;
  tTrigger: number;
  lat: number | null;
  lon: number | null;
  speedKmh: number | null;
  maxSpeed30s: number | null;
}

export const located = (p: StallPin) => p.lat != null && p.lon != null;

/** Groups of pins (indices) within radiusM of another pin of the group
 *  (single linkage), biggest first. Pins without a position are left out. */
export function clusters(pins: StallPin[], radiusM = CLUSTER_RADIUS_M): number[][] {
  const idx = pins.map((p, i) => (located(p) ? i : -1)).filter((i) => i >= 0);
  if (!idx.length) return [];
  const parent = idx.map((_, i) => i);
  const root = (a: number): number => {
    while (parent[a] !== a) { parent[a] = parent[parent[a]!]!; a = parent[a]!; }
    return a;
  };
  for (let a = 0; a < idx.length; a++) {
    for (let b = a + 1; b < idx.length; b++) {
      const p = pins[idx[a]!]!, q = pins[idx[b]!]!;
      if (haversineM(p.lat!, p.lon!, q.lat!, q.lon!) <= radiusM) parent[root(a)] = root(b);
    }
  }
  const groups = new Map<number, number[]>();
  idx.forEach((pi, a) => {
    const r = root(a);
    if (!groups.has(r)) groups.set(r, []);
    groups.get(r)!.push(pi);
  });
  return [...groups.values()].sort((g, h) => h.length - g.length || g[0]! - h[0]!);
}

export function centre(pins: StallPin[], group: number[]): [number, number] {
  const lat = group.reduce((s, i) => s + pins[i]!.lat!, 0) / group.length;
  const lon = group.reduce((s, i) => s + pins[i]!.lon!, 0) / group.length;
  return [lat, lon];
}

export function spreadM(pins: StallPin[], group: number[]): number {
  const [lat, lon] = centre(pins, group);
  return Math.max(...group.map((i) => haversineM(lat, lon, pins[i]!.lat!, pins[i]!.lon!)));
}

function median(a: number[]): number {
  const s = [...a].sort((p, q) => p - q);
  const m = s.length >> 1;
  return s.length % 2 ? s[m]! : (s[m - 1]! + s[m]!) / 2;
}

/** One or two sentences: how many, how many have a place, the biggest group. */
export function summary(
  pins: StallPin[], radiusM = CLUSTER_RADIUS_M,
  speed: (kmh: number) => string = (k) => `${k.toFixed(0)} km/h`,
  distance: (m: number) => string = (m) => `${m.toFixed(0)} m`,
): string {
  if (!pins.length) return 'No captures match: nothing to compare yet.';
  const placed = pins.filter(located);
  const what = pins.every((p) => p.kind === 'stall') ? 'stall' : 'capture';
  let head = `${pins.length} ${what}${pins.length !== 1 ? 's' : ''}`;
  if (!placed.length) return head + ', none with a GPS fix near its trigger.';
  if (placed.length < pins.length) head += `, ${placed.length} with a place`;
  const groups = clusters(pins, radiusM);
  const big = groups[0]!;
  if (big.length < 2) return head + `. No two within ${distance(radiusM)} of each other: no one place stands out.`;
  const [lat, lon] = centre(pins, big);
  const moving = big.map((i) => pins[i]!.maxSpeed30s).filter((v): v is number => v != null);
  const fast = moving.length ? `, moving at up to ${speed(median(moving))} before` : '';
  return head + `. ${big.length} of them within ${distance(Math.max(1, 2 * spreadM(pins, big)))} of one another, `
    + `near ${lat.toFixed(5)}, ${lon.toFixed(5)}${fast}.`;
}

/** A FeatureCollection: one Point per pin with a place ([lon, lat]). */
export function stallsGeojson(pins: StallPin[], radiusM = CLUSTER_RADIUS_M): Record<string, unknown> {
  const groups = clusters(pins, radiusM);
  const groupOf = new Map<number, number>();
  groups.forEach((g, gi) => g.forEach((i) => groupOf.set(i, gi)));
  const features = pins.flatMap((p, i) => {
    if (!located(p)) return [];
    const { lat, lon, ...rest } = p;
    const gi = groupOf.get(i);
    return [{
      type: 'Feature', geometry: { type: 'Point', coordinates: [lon, lat] },
      properties: { ...rest, cluster: gi ?? null, cluster_size: gi != null ? groups[gi]!.length : 1 },
    }];
  });
  return { type: 'FeatureCollection', features, generator: GENERATOR, unlocated: pins.filter((p) => !located(p)).length };
}
