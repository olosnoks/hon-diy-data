/* hondalog service worker: offline app shell + OpenStreetMap tile cache.
 *
 * - Navigations: network first (so a new deploy shows up), cached index.html
 *   when offline.
 * - Same-origin GET (hashed Vite assets, fonts, wasm, maps): cache first,
 *   filled on first use. Vite hashes file names, so a cached asset never goes
 *   stale; old ones are dropped when a new worker activates.
 * - tile.openstreetmap.org: cache first, capped, so the GPS map works offline
 *   for areas already seen. Nothing else is fetched cross-origin.
 * No analytics, no other network.
 */
const VERSION = 'hondalog-v1';
const SHELL = `${VERSION}-shell`;
const ASSETS = `${VERSION}-assets`;
const TILES = 'hondalog-tiles'; // survives app updates
const MAX_TILES = 4000;

const scope = self.registration.scope; // ends with "/" (/ or /hon-diy-data/)
const SHELL_FILES = ['', 'index.html', 'manifest.webmanifest', 'icons/icon-192.png', 'icons/icon-512.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL);
      await Promise.all(
        SHELL_FILES.map(async (f) => {
          try {
            const r = await fetch(new URL(f, scope), { cache: 'no-cache' });
            if (r.ok) await cache.put(new URL(f, scope), r);
          } catch {
            /* offline install: filled later */
          }
        }),
      );
      // Precache the assets index.html references, so the first offline start works.
      try {
        const html = await (await cache.match(new URL('index.html', scope)))?.text();
        if (html) {
          const refs = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map((m) => m[1]).filter((u) => !/^(https?:|data:)/.test(u));
          const assets = await caches.open(ASSETS);
          await Promise.all(refs.map((u) => assets.add(new URL(u, scope)).catch(() => undefined)));
        }
      } catch {
        /* best effort */
      }
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keep = new Set([SHELL, ASSETS, TILES]);
      for (const k of await caches.keys()) if (!keep.has(k)) await caches.delete(k);
      await self.clients.claim();
    })(),
  );
});

async function trimTiles(cache) {
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - MAX_TILES; i++) await cache.delete(keys[i]);
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  if (url.hostname.endsWith('tile.openstreetmap.org')) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(TILES);
        const hit = await cache.match(req);
        if (hit) return hit;
        const r = await fetch(req);
        if (r.ok || r.type === 'opaque') {
          await cache.put(req, r.clone());
          trimTiles(cache);
        }
        return r;
      })(),
    );
    return;
  }

  if (url.origin !== self.location.origin || !req.url.startsWith(scope)) return;

  if (req.mode === 'navigate') {
    event.respondWith(
      (async () => {
        try {
          const r = await fetch(req);
          if (r.ok) (await caches.open(SHELL)).put(new URL('index.html', scope), r.clone());
          return r;
        } catch {
          const cache = await caches.open(SHELL);
          return (await cache.match(new URL('index.html', scope))) || (await cache.match(new URL('', scope))) || Response.error();
        }
      })(),
    );
    return;
  }

  event.respondWith(
    (async () => {
      const hit = (await caches.match(req, { cacheName: ASSETS })) || (await caches.match(req, { cacheName: SHELL }));
      if (hit) return hit;
      const r = await fetch(req);
      if (r.ok && r.type === 'basic') (await caches.open(ASSETS)).put(req, r.clone());
      return r;
    })(),
  );
});
