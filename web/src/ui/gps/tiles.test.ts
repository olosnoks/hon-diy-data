// Port of tests/test_geo_tiles.py: the OSM policy defaults, cache headers,
// offline first, revalidation, back-off, the wish list, the size cap.
// Nothing here touches the network: every request goes to a fake server.
import { describe, expect, it } from 'vitest';
import {
  DEFAULT_TTL_S, expiry, isOsm, isValid, MAX_CONNECTIONS, MemoryBackend, OSM_ATTRIBUTION, OSM_URL, slug, sourceFor,
  TileFetcher, tileUrl, TileStore, type GetFn, type TileKey, type TileResult, type TileSource,
} from './tiles';

const PNG = () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3])], { type: 'image/png' });
const source: TileSource = { url: 'https://tiles.test/{z}/{x}/{y}.png', attribution: '© test contributors', minZoom: 0, maxZoom: 19 };

class Server {
  calls: Array<[string, Record<string, string>]> = [];
  inFlight = 0;
  most = 0;
  constructor(private opts: { status?: number; headers?: Record<string, string>; fail?: boolean; gate?: Promise<void> } = {}) {}
  get: GetFn = async (url, headers) => {
    this.calls.push([url, headers]);
    this.inFlight++;
    this.most = Math.max(this.most, this.inFlight);
    try {
      if (this.opts.gate) await this.opts.gate;
      await Promise.resolve();
      if (this.opts.fail) throw new TypeError('Failed to fetch');
      const status = this.opts.status ?? 200;
      const etag = this.opts.headers?.ETag;
      if (etag && headers['If-None-Match'] === etag) return { status: 304, headers: { ...this.opts.headers }, body: null };
      return { status, headers: { 'Content-Type': 'image/png', ...this.opts.headers }, body: status === 200 ? PNG() : null };
    } finally {
      this.inFlight--;
    }
  };
}

function collect(f: TileFetcher): TileResult[] {
  const out: TileResult[] = [];
  f.onResult((r) => out.push(r));
  return out;
}

describe('source and policy', () => {
  it('defaults to OpenStreetMap with its attribution', () => {
    const src = sourceFor('');
    expect(src.url).toBe(OSM_URL);
    expect(OSM_URL).toBe('https://tile.openstreetmap.org/{z}/{x}/{y}.png');
    expect(src.attribution).toBe(OSM_ATTRIBUTION);
    expect(isOsm(src)).toBe(true);
    expect(src.maxZoom).toBe(19);
    const own = sourceFor('https://maps.example/{z}/{x}/{y}.png', '© me');
    expect(own.attribution).toBe('© me');
    expect(isOsm(own)).toBe(false);
    expect(slug(own)).not.toBe(slug(src));
    expect(sourceFor('https://maps.example/{z}/{x}/{y}.png').attribution).toBe('');
    expect(isValid({ ...src, url: 'https://maps.example/tiles.png' })).toBe(false);
    expect(tileUrl({ ...src, url: 'https://a.example/{s}/{z}/{x}/{y}.png' }, 3, 1, 2)).toBe('https://a.example/a/3/1/2.png');
    expect(MAX_CONNECTIONS).toBeLessThanOrEqual(2);
  });

  it('reads cache headers', () => {
    const now = 1_800_000_000;
    expect(expiry({ 'Cache-Control': 'public, max-age=600' }, now)).toBe(now + 600);
    expect(expiry({ 'Cache-Control': 'max-age=600', Age: '100' }, now)).toBe(now + 500);
    expect(expiry({ 'Cache-Control': 'no-cache' }, now)).toBe(now);
    expect(expiry({}, now)).toBe(now + DEFAULT_TTL_S);
    const date = new Date((now - 50) * 1000).toUTCString();
    const exp = new Date((now - 50 + 3600) * 1000).toUTCString();
    expect(expiry({ Expires: exp, Date: date }, now)).toBeCloseTo(now + 3600);
    expect(expiry({ Expires: '0' }, now)).toBe(now);
  });
});

describe('fetcher', () => {
  it('fetches, caches, and then serves from the cache without a request', async () => {
    const server = new Server();
    const store = new TileStore(new MemoryBackend());
    const f = new TileFetcher(store, source, { get: server.get });
    const res = collect(f);
    const key: TileKey = [15, 8179, 13277];
    f.want([key]);
    await f.idle();
    expect(res.map((r) => r.state)).toEqual(['fetched']);
    const [url, headers] = server.calls[0]!;
    expect(url).toBe('https://tiles.test/15/8179/13277.png');
    expect(Object.keys(headers).some((k) => ['cache-control', 'pragma'].includes(k.toLowerCase()))).toBe(false);
    expect(headers['If-None-Match']).toBeUndefined();
    expect(await store.get(key)).not.toBeNull();
    f.forget(key);
    f.want([key]);
    await f.idle();
    expect(res.map((r) => r.state)).toEqual(['fetched', 'cached']);
    expect(server.calls.length).toBe(1);
  });

  it('shows a stale tile at once, then revalidates it', async () => {
    const server = new Server({ headers: { 'Cache-Control': 'max-age=3600', ETag: '"v1"', 'Last-Modified': 'Mon, 02 Nov 2026 07:12:00 GMT' } });
    const store = new TileStore(new MemoryBackend());
    const now = () => Date.now() / 1000;
    await store.put([14, 1, 2], { data: PNG(), expires: now() - 10, etag: '"v1"', lastModified: 'Mon, 02 Nov 2026 07:12:00 GMT' });
    const f = new TileFetcher(store, source, { get: server.get });
    const res = collect(f);
    f.want([[14, 1, 2]]);
    await f.idle();
    expect(res.length).toBe(1);
    expect(res[0]!.state).toBe('cached');
    expect(res[0]!.stale).toBe(true);
    expect(server.calls.length).toBe(1);
    const h = server.calls[0]![1];
    expect(h['If-None-Match']).toBe('"v1"');
    expect(h['If-Modified-Since']).toMatch(/^Mon, 02 Nov 2026/);
    const after = await store.get([14, 1, 2]);
    expect(after!.expires).toBeGreaterThan(now()); // the 304 moved the expiry on
  });

  it('shows the cache when offline and backs off', async () => {
    const server = new Server({ fail: true });
    const store = new TileStore(new MemoryBackend());
    await store.put([14, 5, 5], { data: PNG(), expires: Date.now() / 1000 - 999, etag: '', lastModified: '' });
    let clock = 1000;
    const f = new TileFetcher(store, source, { get: server.get, clock: () => clock });
    const res = collect(f);
    f.want([[14, 5, 5], [14, 5, 6]]);
    await f.idle();
    const by = new Map(res.map((r) => [r.key.join('/'), r.state]));
    expect(by.get('14/5/5')).toBe('cached');
    expect(by.get('14/5/6')).toBe('missing');
    expect(f.offline).toBe(true);
    expect(f.errors).toBeGreaterThanOrEqual(1);
    const calls = server.calls.length;
    f.want([[14, 7, 7], [14, 7, 8]]);
    await f.idle();
    expect(res.slice(-2).map((r) => r.state)).toEqual(['missing', 'missing']);
    expect(server.calls.length).toBe(calls);
    clock += 61; // the back-off is over
    expect(f.offline).toBe(false);
  });

  it('never touches the network with downloads switched off', async () => {
    const server = new Server();
    const store = new TileStore(new MemoryBackend());
    await store.put([14, 5, 5], { data: PNG(), expires: 0, etag: '', lastModified: '' });
    const f = new TileFetcher(store, source, { get: server.get, online: false });
    const res = collect(f);
    f.want([[14, 5, 5], [14, 9, 9]]);
    await f.idle();
    expect(res.map((r) => r.state).sort()).toEqual(['cached', 'missing']);
    expect(server.calls).toEqual([]);
  });

  it('treats errors and tiles off the world as missing', async () => {
    const server = new Server({ status: 404 });
    const f = new TileFetcher(new TileStore(new MemoryBackend()), source, { get: server.get });
    const res = collect(f);
    f.want([[14, 3, 3], [20, 0, 0], [3, 9, 0]]);
    await f.idle();
    expect(res.map((r) => r.state)).toEqual(['missing', 'missing', 'missing']);
    expect(server.calls.map((c) => c[0])).toEqual(['https://tiles.test/14/3/3.png']);
    expect(f.lastError).toContain('HTTP 404');
  });

  it('fetches only what is wanted now, two at a time', async () => {
    let open!: () => void;
    const gate = new Promise<void>((r) => { open = r; });
    const server = new Server({ gate });
    const f = new TileFetcher(new TileStore(new MemoryBackend()), source, { get: server.get });
    f.want(Array.from({ length: 40 }, (_, x) => [16, x, 100] as TileKey));
    await new Promise((r) => setTimeout(r, 10)); // two are in flight now
    f.want([[16, 500, 500]]); // the view moved on
    open();
    await f.idle();
    const asked = server.calls.map((c) => c[0]);
    expect(asked.length).toBeLessThanOrEqual(3);
    expect(asked).toContain('https://tiles.test/16/500/500.png');
    expect(server.most).toBeLessThanOrEqual(MAX_CONNECTIONS);
  });
});

describe('cache cap', () => {
  it('drops the least recently used tiles', async () => {
    let now = 1_800_000_000;
    const store = new TileStore(new MemoryBackend(), 10_000, () => now);
    const blob = () => new Blob([new Uint8Array(3000)]);
    for (let i = 0; i < 3; i++) {
      await store.put([10, i, 0], { data: blob(), expires: 4e9, etag: '', lastModified: '' });
      store.setUsed([10, i, 0], now - 2 * 86400 + i);
    }
    expect(await store.get([10, 0, 0])).not.toBeNull(); // used now: the newest
    await store.put([10, 3, 0], { data: blob(), expires: 4e9, etag: '', lastModified: '' }); // over the cap
    expect(store.sizeBytes()).toBeLessThanOrEqual(10_000 * 0.9 + 1);
    const kept: number[] = [];
    for (let i = 0; i < 4; i++) if (await store.get([10, i, 0])) kept.push(i);
    expect(kept).toEqual([0, 2, 3]);
    await store.clear();
    expect(store.sizeBytes()).toBe(0);
    expect(await store.get([10, 3, 0])).toBeNull();
    now++;
  });
});
