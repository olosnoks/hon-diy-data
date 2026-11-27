"""Map tiles for the Map screen: ``hondalog.geo.tiles`` plus Qt images.

The fetcher's worker threads decode each PNG and make both themed versions
of it there (QImage is safe off the GUI thread), so the UI thread only turns
an image into a pixmap:

- Dracula: inverted, hue turned back (water stays blue-ish, parks green-ish),
  dimmed and tinted toward the theme's ground, so the map is never a white
  sheet and the coloured track and pins stand out.
- Darker: the same, dimmed further for the deeper ground.

Settings (QSettings, the Map tiles menu): ``map/tile_url``,
``map/tile_attribution``, ``map/online`` (download tiles at all),
``map/cache_mb``. ``HONDALOG_TILES=offline`` in the environment keeps every
instance off the network (tests, screenshots).
"""

from __future__ import annotations

import collections
import os

import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPixmap

from ..geo import tiles as gt

#: decoded tile pairs kept in memory (about 0.5 MB each) and pixmaps of the current theme
RAW_KEEP = 96
PIX_KEEP = 96
#: how many zoom levels up a missing tile may borrow a scaled-up parent from
PARENT_LEVELS = 5

# invert, then rotate the hue by 180 degrees (the CSS hue-rotate matrix), then dim
_HUE180 = np.array([[-0.574, 1.430, 0.144],
                    [0.426, 0.430, 0.144],
                    [0.426, 1.430, -0.856]], dtype=np.float32)
#: (gain, lift as R, G, B) per theme: the lift is about the theme's ground colour
THEMED = {"dracula": (0.62, (30.0, 31.0, 42.0)), "darker": (0.52, (16.0, 17.0, 24.0))}
THEME_NAMES = tuple(THEMED)


def _pixels(img: QImage) -> np.ndarray:
    """(h, w, 4) uint8 view of a Format_RGB32 image: B, G, R, A in memory."""
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    buf = np.frombuffer(img.constBits(), dtype=np.uint8, count=img.sizeInBytes())
    return buf.reshape(h, bpl)[:, :w * 4].reshape(h, w, 4)


def _from_pixels(bgra: np.ndarray) -> QImage:
    h, w = bgra.shape[:2]
    data = np.ascontiguousarray(bgra)
    return QImage(data.tobytes(), w, h, w * 4, QImage.Format_RGB32).copy()


def themed(img: QImage) -> dict[str, QImage]:
    """{"dracula": ..., "darker": ...}: the tile inverted, hue turned back and
    dimmed onto the theme's ground (never a light map)."""
    rgb32 = img.convertToFormat(QImage.Format_RGB32)
    px = _pixels(rgb32).astype(np.float32)
    rgb = px[..., 2::-1]                                    # R, G, B
    inv = np.clip((255.0 - rgb) @ _HUE180.T, 0.0, 255.0)
    out = {}
    for name, (gain, lift) in THEMED.items():
        colour = inv * gain + np.array(lift, dtype=np.float32)
        bgra = np.empty(px.shape, dtype=np.uint8)
        bgra[..., 2::-1] = np.clip(colour, 0.0, 255.0).astype(np.uint8)
        bgra[..., 3] = 255
        out[name] = _from_pixels(bgra)
    return out


def decode(data: bytes):
    """PNG / JPEG bytes -> the two themed images, or None (runs on a worker)."""
    img = QImage.fromData(data)
    if img.isNull():
        return None
    return themed(img)


def env_offline() -> bool:
    return os.environ.get("HONDALOG_TILES", "").strip().lower() in ("offline", "off", "0", "no")


class TileLayer:
    """The tiles of one source for the map canvas."""

    def __init__(self, source: gt.TileSource, *, online: bool = True, cap_mb: float = gt.DEFAULT_CAP_MB,
                 root=None, get=None):
        self.source = source
        self.cache = gt.TileCache(source, root, cap_mb)
        # HONDALOG_TILES=offline keeps the network out; a ``get`` of the caller's own is not the network
        self._own_get = get is not None
        self.fetcher = gt.TileFetcher(self.cache, decode=decode, get=get,
                                      online=online and (self._own_get or not env_offline()))
        self._raw: collections.OrderedDict = collections.OrderedDict()     # key -> {"dracula": QImage, ...}
        self._pix: collections.OrderedDict = collections.OrderedDict()     # (key, theme) -> QPixmap
        self._missing: set = set()
        self.arrived = 0                    # tiles that came in (disk or network)

    # ---- settings ----

    @property
    def online(self) -> bool:
        return self.fetcher.online

    def set_online(self, on: bool) -> None:
        self.fetcher.set_online(on and (self._own_get or not env_offline()))
        if on:
            self._missing.clear()

    def close(self) -> None:
        self.fetcher.close()

    # ---- flow ----

    def want(self, keys) -> None:
        self.fetcher.want([k for k in keys if k not in self._raw and k not in self._missing])

    def poll(self) -> bool:
        """Take what the workers finished. True if anything changed on screen."""
        changed = False
        for res in self.fetcher.poll():
            changed = True
            if res.image is None:
                if res.key not in self._raw:
                    self._missing.add(res.key)
                continue
            self.arrived += 1
            self._missing.discard(res.key)
            self._raw[res.key] = res.image
            self._raw.move_to_end(res.key)
            for theme in THEME_NAMES:
                self._pix.pop((res.key, theme), None)
            while len(self._raw) > RAW_KEEP:
                old, _ = self._raw.popitem(last=False)
                self.fetcher.forget(old)
                for theme in THEME_NAMES:
                    self._pix.pop((old, theme), None)
        return changed

    def has(self, key) -> bool:
        return key in self._raw

    def missing(self, key) -> bool:
        return key in self._missing

    def pixmap(self, key, theme: str) -> QPixmap | None:
        pm = self._pix.get((key, theme))
        if pm is not None:
            self._pix.move_to_end((key, theme))
            return pm
        pair = self._raw.get(key)
        if pair is None:
            return None
        self._raw.move_to_end(key)
        pm = QPixmap.fromImage(pair.get(theme) or pair[THEME_NAMES[0]])
        self._pix[(key, theme)] = pm
        while len(self._pix) > PIX_KEEP:
            self._pix.popitem(last=False)
        return pm

    def fallback(self, key, theme: str) -> tuple[QPixmap, QRectF] | None:
        """A loaded tile of a lower zoom that covers ``key``, and the part of it that does."""
        z, x, y = key
        for up in range(1, PARENT_LEVELS + 1):
            if z - up < 0:
                break
            parent = (z - up, x >> up, y >> up)
            if parent in self._raw:
                pm = self.pixmap(parent, theme)
                if pm is None:
                    return None
                n = 1 << up
                size = pm.width() / n
                return pm, QRectF((x % n) * size, (y % n) * size, size, size)
        return None

    def status(self) -> str:
        """A few words for the map's attribution box."""
        f = self.fetcher
        if not f.online:
            return "tiles: cached only (downloads off)"
        if f.offline:
            return "offline: cached tiles only"
        if f.pending:
            return f"loading {f.pending} tile{'s' if f.pending != 1 else ''}"
        return ""

    def clear_cache(self) -> None:
        self.cache.clear()
        self._raw.clear()
        self._pix.clear()
        self._missing.clear()
        with self.fetcher._cv:
            self.fetcher._known.clear()


__all__ = ["TileLayer", "decode", "env_offline", "themed"]
