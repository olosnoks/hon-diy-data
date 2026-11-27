/**
 * Raster map tiles from an OpenStreetMap-style server, cached on the device
 * (port of hondalog/geo/tiles.py).
 *
 * Offline first: in the car there is usually no internet. A cached tile is
 * used whatever its age; a stale one is shown at once and asked for again
 * afterwards when the server can be reached. Tiles that are neither cached
 * nor reachable are missing and the map draws a plain grid there.
 *
 * OpenStreetMap tile usage policy (https://operations.osmfoundation.org/policies/tiles/):
 * - only the tiles the screen shows are asked for, two connections at most,
 *   and a wish list that is replaced, not appended to, when the view moves:
 *   nothing is prefetched or bulk downloaded;
 * - tiles are kept as long as Cache-Control max-age / Expires allow (7 days
 *   when the server says nothing); no-cache is never sent;
 * - the attribution is shown on the map.
 * A browser does not let a page set User-Agent; it sends its own and the
 * Referer of the app's origin, which is what the policy asks of web apps.
 *
 * Privacy: a tile URL reveals the area being viewed (inherent to online
 * tiles). Nothing else is ever sent, and `online: false` (the setting
 * "Download map tiles") never touches the network.
 */

export const OSM_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
export const OSM_ATTRIBUTION = '© OpenStreetMap contributors';
export const OSM_COPYRIGHT_URL = 'https://www.openstreetmap.org/copyright';
export const DEFAULT_TTL_S = 7 * 24 * 3600;
export const MIN_TTL_S = 60;
export const DEFAULT_CAP_MB = 300;
export const MAX_CONNECTIONS = 2;
export const OFFLINE_BACKOFF_S = 60;
export const TOUCH_S = 3600;

export type TileKey = readonly [number, number, number]; // z, x, y
export const keyStr = (k: TileKey) => `${k[0]}/${k[1]}/${k[2]}`;
export function parseKey(s: string): TileKey {
  const [z, x, y] = s.split('/').map(Number);
  return [z!, x!, y!];
}

export interface TileSource {
  url: string;
  attribution: string;
  minZoom: number;
  maxZoom: number;
}

export function tileUrl(src: TileSource, z: number, x: number, y: number): string {
  return src.url.replace('{s}', 'a').replace('{z}', String(z)).replace('{x}', String(x)).replace('{y}', String(y));
}

function hostOf(url: string): string {
  try { return new URL(url.replace(/[{}]/g, '')).hostname; } catch { return ''; }
}

export function isOsm(src: TileSource): boolean {
  return ['tile.openstreetmap.org', 'a.tile.openstreetmap.org', 'b.tile.openstreetmap.org', 'c.tile.openstreetmap.org']
    .includes(hostOf(src.url));
}

export function isValid(src: TileSource): boolean {
  if (!['{z}', '{x}', '{y}'].every((k) => src.url.includes(k))) return false;
  return /^(https?|file):/.test(src.url);
}

/** a short stable name for the cache of one source */
export function slug(src: TileSource): string {
  const host = (hostOf(src.url) || 'local').replace(/[^A-Za-z0-9.-]+/g, '_').slice(0, 48);
  let h = 0x811c9dc5;
  for (let i = 0; i < src.url.length; i++) { h ^= src.url.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return `${host}-${h.toString(16).padStart(8, '0')}`;
}

/** The source the settings describe. An empty URL is OpenStreetMap. */
export function sourceFor(url?: string | null, attribution?: string | null): TileSource {
  const u = (url ?? '').trim() || OSM_URL;
  let a = attribution;
  if (a == null || !a.trim()) a = u === OSM_URL || u.includes('openstreetmap') ? OSM_ATTRIBUTION : '';
  return { url: u, attribution: a.trim(), minZoom: 0, maxZoom: 19 };
}

type HeaderBag = Record<string, string> | { get(name: string): string | null };
function header(h: HeaderBag, name: string): string {
  if (typeof (h as { get?: unknown }).get === 'function') return (h as { get(n: string): string | null }).get(name) ?? '';
  const rec = h as Record<string, string>;
  const k = Object.keys(rec).find((x) => x.toLowerCase() === name.toLowerCase());
  return k ? rec[k] ?? '' : '';
}

/** When a response may be used until without asking again (unix seconds). */
export function expiry(headers: HeaderBag, now: number): number {
  const cc = header(headers, 'Cache-Control').toLowerCase();
  if (cc.includes('no-store') || cc.includes('no-cache')) return now;
  const m = /(?:^|[,\s])max-age\s*=\s*(\d+)/.exec(cc);
  if (m) {
    const age = Number(header(headers, 'Age')) || 0;
    return now + Math.max(0, Number(m[1]) - age);
  }
  const exp = header(headers, 'Expires');
  if (exp) {
    const when = Date.parse(exp);
    if (!Number.isFinite(when) || !/[a-z]/i.test(exp)) return now;
    const date = Date.parse(header(headers, 'Date'));
    return Number.isFinite(date) ? now + (when - date) / 1000 : when / 1000;
  }
  return now + DEFAULT_TTL_S;
}

// ---- the cache ----------------------------------------------------------------------

export interface CachedTile { data: Blob; expires: number; etag: string; lastModified: string }

interface IndexEntry { size: number; used: number }

/** Where tile bytes live. The LRU index and size cap are shared (TileStore). */
export interface TileBackend {
  read(key: string): Promise<CachedTile | null>;
  write(key: string, tile: CachedTile): Promise<void>;
  remove(key: string): Promise<void>;
  loadIndex(): Record<string, IndexEntry>;
  saveIndex(index: Record<string, IndexEntry>): void;
}

export class TileStore {
  private index: Record<string, IndexEntry> | null = null;
  constructor(readonly backend: TileBackend, public cap = DEFAULT_CAP_MB * 1e6, private now: () => number = () => Date.now() / 1000) {}

  private idx(): Record<string, IndexEntry> {
    if (!this.index) this.index = this.backend.loadIndex();
    return this.index;
  }

  async get(key: TileKey): Promise<CachedTile | null> {
    const k = keyStr(key);
    const tile = await this.backend.read(k);
    if (!tile) return null;
    const e = this.idx()[k];
    const now = this.now();
    if (!e) { this.idx()[k] = { size: tile.data.size, used: now }; this.backend.saveIndex(this.idx()); }
    else if (now - e.used > TOUCH_S) { e.used = now; this.backend.saveIndex(this.idx()); }
    return tile;
  }

  async put(key: TileKey, tile: CachedTile): Promise<void> {
    const k = keyStr(key);
    try { await this.backend.write(k, tile); } catch { return; }
    this.idx()[k] = { size: tile.data.size, used: this.now() };
    this.backend.saveIndex(this.idx());
    await this.trim();
  }

  /** a 304 Not Modified: the tile is good until expires */
  async refresh(key: TileKey, expires: number, etag = '', lastModified = ''): Promise<void> {
    const old = await this.backend.read(keyStr(key));
    if (!old) return;
    await this.backend.write(keyStr(key), { ...old, expires, etag: etag || old.etag, lastModified: lastModified || old.lastModified });
    const e = this.idx()[keyStr(key)];
    if (e) { e.used = this.now(); this.backend.saveIndex(this.idx()); }
  }

  sizeBytes(): number {
    return Object.values(this.idx()).reduce((s, e) => s + e.size, 0);
  }

  /** Delete the least recently used tiles until under 90 % of the cap. */
  async trim(): Promise<number> {
    if (this.sizeBytes() <= this.cap) return 0;
    const entries = Object.entries(this.idx()).sort((a, b) => a[1].used - b[1].used);
    let total = this.sizeBytes();
    const goal = Math.floor(this.cap * 0.9);
    let gone = 0;
    for (const [k, e] of entries) {
      if (total <= goal) break;
      await this.backend.remove(k).catch(() => undefined);
      delete this.idx()[k];
      total -= e.size;
      gone++;
    }
    this.backend.saveIndex(this.idx());
    return gone;
  }

  async clear(): Promise<void> {
    for (const k of Object.keys(this.idx())) await this.backend.remove(k).catch(() => undefined);
    this.index = {};
    this.backend.saveIndex(this.index);
  }

  /** test hook: pretend a tile was last used at `used` */
  setUsed(key: TileKey, used: number): void {
    const e = this.idx()[keyStr(key)];
    if (e) e.used = used;
  }
}

/** Tiles in memory only (tests, and where nothing persistent is available). */
export class MemoryBackend implements TileBackend {
  readonly tiles = new Map<string, CachedTile>();
  private index: Record<string, IndexEntry> = {};
  async read(key: string) { return this.tiles.get(key) ?? null; }
  async write(key: string, tile: CachedTile) { this.tiles.set(key, tile); }
  async remove(key: string) { this.tiles.delete(key); }
  loadIndex() { return { ...this.index }; }
  saveIndex(index: Record<string, IndexEntry>) { this.index = { ...index }; }
}

/** Tiles in the browser's Cache Storage (also in the Tauri web view), the
 *  LRU index in localStorage. Survives restarts: the map works offline. */
export class CacheStorageBackend implements TileBackend {
  constructor(private name: string) {}
  private open() { return caches.open(`hondalog-tiles-${this.name}`); }
  private req(key: string) { return new Request(`https://hondalog.invalid/tiles/${key}.png`); }
  async read(key: string): Promise<CachedTile | null> {
    const res = await (await this.open()).match(this.req(key));
    if (!res) return null;
    return {
      data: await res.blob(),
      expires: Number(res.headers.get('x-hl-expires')) || 0,
      etag: res.headers.get('x-hl-etag') ?? '',
      lastModified: res.headers.get('x-hl-last-modified') ?? '',
    };
  }
  async write(key: string, t: CachedTile): Promise<void> {
    const headers = new Headers({ 'content-type': t.data.type || 'image/png', 'x-hl-expires': String(t.expires) });
    if (t.etag) headers.set('x-hl-etag', t.etag);
    if (t.lastModified) headers.set('x-hl-last-modified', t.lastModified);
    await (await this.open()).put(this.req(key), new Response(t.data, { headers }));
  }
  async remove(key: string): Promise<void> { await (await this.open()).delete(this.req(key)); }
  loadIndex(): Record<string, IndexEntry> {
    try { return JSON.parse(localStorage.getItem(`hondalog.tiles.${this.name}.index`) ?? '{}'); } catch { return {}; }
  }
  saveIndex(index: Record<string, IndexEntry>): void {
    try { localStorage.setItem(`hondalog.tiles.${this.name}.index`, JSON.stringify(index)); } catch { /* full */ }
  }
}

/** The persistent store for a source when the platform has one, else memory. */
export function defaultStore(src: TileSource, capMb = DEFAULT_CAP_MB): TileStore {
  const persistent = typeof caches !== 'undefined' && typeof localStorage !== 'undefined';
  return new TileStore(persistent ? new CacheStorageBackend(slug(src)) : new MemoryBackend(), capMb * 1e6);
}

// ---- the network -------------------------------------------------------------------

export interface TileResponse { status: number; headers: HeaderBag; body: Blob | null }
/** One GET. A 304 or 4xx / 5xx comes back as a response; a connection
 *  failure throws. */
export type GetFn = (url: string, headers: Record<string, string>) => Promise<TileResponse>;

/** fetch() in the web view. Conditional headers are left to the browser's own
 *  HTTP cache (setting them by hand would need a CORS preflight per tile). */
export const browserGet: GetFn = async (url) => {
  const res = await fetch(url, { credentials: 'omit', mode: 'cors', referrerPolicy: 'strict-origin-when-cross-origin' });
  return { status: res.status, headers: res.headers, body: res.status === 200 ? await res.blob() : null };
};

export type TileState = 'cached' | 'fetched' | 'missing';
export interface TileResult { key: TileKey; state: TileState; data: Blob | null; stale: boolean }

export interface FetcherOptions {
  get?: GetFn;
  online?: boolean;
  threads?: number;
  /** unix seconds (cache expiry) */
  now?: () => number;
  /** seconds, monotonic (back-off) */
  clock?: () => number;
}

/** Brings tiles to the map without blocking it. want(keys) says which tiles
 *  the screen shows now (it replaces the last wish list); listeners get the
 *  results. */
export class TileFetcher {
  readonly source: TileSource;
  online: boolean;
  requests = 0;
  errors = 0;
  lastError = '';
  private get: GetFn;
  private now: () => number;
  private clock: () => number;
  private threads: number;
  private wanted = new Map<string, TileKey>();
  private busy = new Set<string>();
  private known = new Map<string, TileState>();
  private revalidated = new Set<string>();
  private offlineUntil = 0;
  private running = 0;
  private stopped = false;
  private listeners = new Set<(r: TileResult) => void>();
  private idleWaiters: Array<() => void> = [];
  maxBusy = 0;

  constructor(readonly store: TileStore, source: TileSource, opts: FetcherOptions = {}) {
    this.source = source;
    this.get = opts.get ?? browserGet;
    this.online = opts.online ?? true;
    this.threads = Math.max(1, Math.min(opts.threads ?? MAX_CONNECTIONS, MAX_CONNECTIONS));
    this.now = opts.now ?? (() => Date.now() / 1000);
    this.clock = opts.clock ?? (() => (typeof performance !== 'undefined' ? performance.now() : Date.now()) / 1000);
  }

  onResult(fn: (r: TileResult) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** The tiles on screen, most important first. Replaces the last list. */
  want(keys: Iterable<TileKey>): void {
    this.wanted = new Map();
    for (const k of keys) {
      const s = keyStr(k);
      if (!this.busy.has(s) && !this.known.has(s)) this.wanted.set(s, k);
    }
    this.pump();
  }

  /** Ask for this tile again (the map dropped its copy). */
  forget(key: TileKey): void {
    this.known.delete(keyStr(key));
  }

  get pending(): number { return this.wanted.size + this.busy.size; }
  get offline(): boolean { return !this.online || this.clock() < this.offlineUntil; }

  setOnline(on: boolean): void {
    this.online = on;
    this.offlineUntil = 0;
    if (on) for (const [k, v] of [...this.known]) if (v === 'missing') this.known.delete(k);
  }

  close(): void {
    this.stopped = true;
    this.wanted.clear();
    this.listeners.clear();
  }

  /** resolves when nothing is wanted or in flight (tests) */
  idle(): Promise<void> {
    if (this.running === 0 && this.wanted.size === 0) return Promise.resolve();
    return new Promise((r) => this.idleWaiters.push(r));
  }

  private pump(): void {
    while (!this.stopped && this.running < this.threads && this.wanted.size) {
      this.running++;
      void this.worker();
    }
  }

  private async worker(): Promise<void> {
    try {
      while (!this.stopped && this.wanted.size) {
        const [s, key] = this.wanted.entries().next().value as [string, TileKey];
        this.wanted.delete(s);
        this.busy.add(s);
        this.maxBusy = Math.max(this.maxBusy, this.busy.size);
        try {
          await this.one(key);
        } catch (exc) {
          this.errors++;
          this.lastError = String(exc);
          this.deliver(key, 'missing', null);
        } finally {
          this.busy.delete(s);
        }
      }
    } finally {
      this.running--;
      if (this.running === 0 && this.wanted.size === 0) {
        const w = this.idleWaiters.splice(0);
        w.forEach((r) => r());
      }
    }
  }

  private deliver(key: TileKey, state: TileState, data: Blob | null, stale = false): void {
    const s = keyStr(key);
    if (state !== 'cached' || !stale) this.known.set(s, state);
    else if (!this.known.has(s)) this.known.set(s, 'cached');
    const r: TileResult = { key, state, data, stale };
    for (const fn of this.listeners) fn(r);
  }

  private netOk(): boolean {
    return this.online && this.clock() >= this.offlineUntil;
  }

  private async one(key: TileKey): Promise<void> {
    const [z, x, y] = key;
    const s = keyStr(key);
    if (z < this.source.minZoom || z > this.source.maxZoom || x < 0 || x >= 2 ** z || y < 0 || y >= 2 ** z) {
      this.deliver(key, 'missing', null);
      return;
    }
    const cached = await this.store.get(key);
    if (cached) {
      const stale = !(this.now() < cached.expires);
      this.deliver(key, 'cached', cached.data, stale);
      if (!stale || !this.netOk() || this.revalidated.has(s)) return;
      this.revalidated.add(s);
    } else if (!this.netOk()) {
      this.deliver(key, 'missing', null);
      return;
    }
    const headers: Record<string, string> = { Accept: 'image/png,image/*;q=0.8' };
    if (cached?.etag) headers['If-None-Match'] = cached.etag;
    if (cached?.lastModified) headers['If-Modified-Since'] = cached.lastModified;
    const url = tileUrl(this.source, z, x, y);
    let resp: TileResponse;
    try {
      this.requests++;
      resp = await this.get(url, headers);
    } catch (exc) {
      this.errors++;
      this.lastError = String(exc);
      this.offlineUntil = this.clock() + OFFLINE_BACKOFF_S;
      if (!cached) this.deliver(key, 'missing', null);
      return;
    }
    const now = this.now();
    const ok = resp.status === 200 || resp.status === 304;
    const expires = ok ? Math.max(now + MIN_TTL_S, expiry(resp.headers, now)) : now;
    const etag = header(resp.headers, 'ETag');
    const modified = header(resp.headers, 'Last-Modified');
    if (resp.status === 304 && cached) {
      await this.store.refresh(key, expires, etag, modified);
      this.known.set(s, 'cached');
      return;
    }
    if (resp.status === 200 && resp.body && resp.body.size > 0) {
      const type = header(resp.headers, 'Content-Type');
      if (type && !type.startsWith('image/')) {
        this.errors++;
        this.lastError = `${url}: not an image`;
        if (!cached) this.deliver(key, 'missing', null);
        return;
      }
      await this.store.put(key, { data: resp.body, expires, etag, lastModified: modified });
      this.deliver(key, 'fetched', resp.body);
      return;
    }
    this.errors++;
    this.lastError = `${url}: HTTP ${resp.status}`;
    if (resp.status === 429 || resp.status >= 500) this.offlineUntil = this.clock() + OFFLINE_BACKOFF_S;
    if (!cached) this.deliver(key, 'missing', null);
  }
}
