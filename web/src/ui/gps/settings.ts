/**
 * Map tile settings (the Python app's map/online, map/tile_url,
 * map/tile_attribution, map/cache_mb), kept on the device.
 */
import { DEFAULT_CAP_MB } from './tiles';

export interface MapSettings {
  /** download tiles at all; off = never touch the network, cached tiles only */
  online: boolean;
  /** '' = OpenStreetMap */
  tileUrl: string;
  attribution: string;
  cacheMb: number;
}

export const DEFAULT_MAP_SETTINGS: MapSettings = { online: true, tileUrl: '', attribution: '', cacheMb: DEFAULT_CAP_MB };
const KEY = 'hondalog.map.settings';

export function loadMapSettings(): MapSettings {
  try {
    const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(KEY) : null;
    if (!raw) return { ...DEFAULT_MAP_SETTINGS };
    const v = JSON.parse(raw) as Partial<MapSettings>;
    return {
      online: typeof v.online === 'boolean' ? v.online : DEFAULT_MAP_SETTINGS.online,
      tileUrl: typeof v.tileUrl === 'string' ? v.tileUrl : '',
      attribution: typeof v.attribution === 'string' ? v.attribution : '',
      cacheMb: typeof v.cacheMb === 'number' && v.cacheMb > 0 ? v.cacheMb : DEFAULT_CAP_MB,
    };
  } catch {
    return { ...DEFAULT_MAP_SETTINGS };
  }
}

export function saveMapSettings(s: MapSettings): void {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* storage full or blocked */ }
}
