/**
 * A Leaflet grid layer that gets its tiles through the offline-first
 * TileFetcher instead of letting <img> elements hit the server: the cache
 * comes first, at most two requests run at once, and only the tiles Leaflet
 * currently shows are wanted (a tile scrolled away leaves the wish list).
 * A tile that is neither cached nor reachable is drawn as a plain grid cell.
 */
import L from 'leaflet';
import { keyStr, type TileFetcher, type TileKey, type TileResult } from './tiles';

interface Live { el: HTMLElement; done?: L.DoneCallback; url?: string; key: TileKey }

export function createOfflineTileLayer(fetcher: TileFetcher, options: L.GridLayerOptions = {}): L.GridLayer {
  const live = new Map<string, Live>();
  let queued = false;

  const rewant = () => {
    if (queued) return;
    queued = true;
    queueMicrotask(() => {
      queued = false;
      fetcher.want([...live.values()].filter((t) => t.done).map((t) => t.key));
    });
  };

  const show = (r: TileResult) => {
    const t = live.get(keyStr(r.key));
    if (!t) return;
    if (r.data) {
      if (t.url) URL.revokeObjectURL(t.url);
      t.url = URL.createObjectURL(r.data);
      t.el.style.backgroundImage = `url("${t.url}")`;
      t.el.classList.remove('hl-tile-missing');
      t.el.dataset.state = r.state;
    } else if (!t.url) {
      t.el.classList.add('hl-tile-missing');
      t.el.dataset.state = 'missing';
    }
    if (t.done) {
      const done = t.done;
      t.done = undefined;
      done(undefined, t.el);
    }
  };

  const Layer = L.GridLayer.extend({
    createTile(this: L.GridLayer, coords: L.Coords, done: L.DoneCallback): HTMLElement {
      const el = L.DomUtil.create('div', 'hl-tile');
      const size = this.getTileSize();
      el.style.width = `${size.x}px`;
      el.style.height = `${size.y}px`;
      el.style.backgroundSize = '100% 100%';
      const key: TileKey = [coords.z, coords.x, coords.y];
      live.set(keyStr(key), { el, done, key });
      rewant();
      return el;
    },
  });

  const Ctor = Layer as unknown as new (o: L.GridLayerOptions) => L.GridLayer;
  const layer = new Ctor({ maxZoom: fetcher.source.maxZoom, minZoom: fetcher.source.minZoom, ...options });
  const off = fetcher.onResult(show);
  layer.on('tileunload', (e: L.TileEvent) => {
    const c = e.coords;
    const k = keyStr([c.z, c.x, c.y]);
    const t = live.get(k);
    if (t?.url) URL.revokeObjectURL(t.url);
    live.delete(k);
    fetcher.forget([c.z, c.x, c.y]);
    rewant();
  });
  layer.on('remove', () => {
    off();
    for (const t of live.values()) if (t.url) URL.revokeObjectURL(t.url);
    live.clear();
    fetcher.want([]);
  });
  return layer;
}
