"""Raster map tiles from an OpenStreetMap-style server, cached on disk.

Offline first: in the car there is usually no internet. A tile on disk is
used whatever its age; a stale one is shown at once and checked with the
server afterwards (a conditional request: ETag / Last-Modified), when the
server can be reached. Tiles that are neither on disk nor reachable are
simply missing and the map draws a plain grid there.

The OpenStreetMap tile usage policy (https://operations.osmfoundation.org/policies/tiles/)
is followed for the default server:

- a User-Agent that names the program: ``hondalog/<version> (+<repo url>)``;
- only tiles the screen shows are asked for, two connections at most, and a
  wish list that is replaced, not appended to, when the view moves: nothing
  is prefetched or bulk downloaded;
- tiles are kept as long as the server's ``Cache-Control: max-age`` /
  ``Expires`` allow (7 days when it says nothing) and revalidated with
  ``If-None-Match`` / ``If-Modified-Since``; no ``no-cache`` is ever sent;
- the attribution ("© OpenStreetMap contributors") is the source's
  ``attribution`` and the map draws it.

The cache lives in ``~/.cache/hondalog/tiles`` (``$XDG_CACHE_HOME``,
``$HONDALOG_TILE_CACHE``), one folder per server, with a size cap: the
least recently used tiles go first.

No Qt in here. ``TileFetcher`` takes a ``decode`` function that runs on its
worker threads, so the UI can have the PNG turned into an image off its
own thread.
"""

from __future__ import annotations

import collections
import email.utils
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .. import __version__

REPO_URL = "https://github.com/olosnoks/hon-diy-data"
OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"
DEFAULT_TTL_S = 7 * 24 * 3600.0
MIN_TTL_S = 60.0
DEFAULT_CAP_MB = 300.0
MAX_CONNECTIONS = 2
TIMEOUT_S = 10.0
#: after a failed connection, wait this long before asking the network again
OFFLINE_BACKOFF_S = 60.0

TileKey = tuple[int, int, int]              # (z, x, y)


def user_agent() -> str:
    return f"hondalog/{__version__} (+{REPO_URL})"


def cache_root() -> Path:
    env = os.environ.get("HONDALOG_TILE_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "hondalog" / "tiles"


@dataclass(frozen=True)
class TileSource:
    """A tile server: a URL template with {z} {x} {y} (and optionally {s},
    filled with "a"), the attribution its terms ask for, its zoom range."""

    url: str = OSM_URL
    attribution: str = OSM_ATTRIBUTION
    min_zoom: int = 0
    max_zoom: int = 19

    def tile_url(self, z: int, x: int, y: int) -> str:
        return self.url.replace("{s}", "a").replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))

    @property
    def is_osm(self) -> bool:
        return urllib.parse.urlsplit(self.url).hostname in ("tile.openstreetmap.org", "a.tile.openstreetmap.org",
                                                           "b.tile.openstreetmap.org", "c.tile.openstreetmap.org")

    @property
    def slug(self) -> str:
        """The cache folder's name: the host plus a hash of the template."""
        parts = urllib.parse.urlsplit(self.url)
        host = re.sub(r"[^A-Za-z0-9.-]+", "_", parts.hostname or parts.scheme or "local")[:48]
        return f"{host}-{hashlib.sha1(self.url.encode()).hexdigest()[:8]}"

    @property
    def valid(self) -> bool:
        return all(k in self.url for k in ("{z}", "{x}", "{y}")) and urllib.parse.urlsplit(self.url).scheme in (
            "http", "https", "file")


def source_for(url: str | None, attribution: str | None = None) -> TileSource:
    """The source the settings describe. An empty URL is OpenStreetMap; a
    server of your own without an attribution gets the OSM one when its URL
    looks like OSM's, else none is assumed (set it in the settings)."""
    url = (url or "").strip() or OSM_URL
    if attribution is None or not attribution.strip():
        attribution = OSM_ATTRIBUTION if url == OSM_URL or "openstreetmap" in url else ""
    return TileSource(url, attribution.strip())


def expiry(headers: Any, now: float) -> float:
    """When a response may be used until without asking again (unix time)."""
    get = headers.get if hasattr(headers, "get") else (lambda k, d=None: d)
    cc = str(get("Cache-Control", "") or get("cache-control", "") or "").lower()
    if "no-store" in cc or "no-cache" in cc:
        return now                          # keep it for offline use, but always revalidate
    m = re.search(r"(?:^|[,\s])max-age\s*=\s*(\d+)", cc)
    if m:
        age = 0.0
        try:
            age = float(get("Age", 0) or 0)
        except (TypeError, ValueError):
            pass
        return now + max(0.0, float(m.group(1)) - age)
    exp = get("Expires") or get("expires")
    if exp:
        try:
            when = email.utils.parsedate_to_datetime(str(exp)).timestamp()
            date = get("Date") or get("date")
            if date:                        # the server's clock, not ours
                when = now + (when - email.utils.parsedate_to_datetime(str(date)).timestamp())
            return when
        except (TypeError, ValueError, IndexError, OverflowError):
            return now                      # "Expires: 0" and the like: already stale
    return now + DEFAULT_TTL_S


@dataclass
class CachedTile:
    data: bytes
    expires: float
    etag: str = ""
    last_modified: str = ""

    def fresh(self, now: float) -> bool:
        return now < self.expires


class TileCache:
    """Tiles of one source on disk: ``<root>/<source slug>/<z>/<x>/<y>.png``
    with a ``.json`` beside it (expiry, ETag, Last-Modified). Thread safe."""

    def __init__(self, source: TileSource, root: str | os.PathLike | None = None,
                 cap_mb: float = DEFAULT_CAP_MB):
        self.source = source
        self.dir = Path(root if root is not None else cache_root()) / source.slug
        self.cap = int(cap_mb * 1e6)
        self._lock = threading.Lock()
        self._size: int | None = None
        self._touched: set[TileKey] = set()

    def _paths(self, key: TileKey) -> tuple[Path, Path]:
        z, x, y = key
        base = self.dir / str(z) / str(x)
        return base / f"{y}.png", base / f"{y}.json"

    def get(self, key: TileKey) -> CachedTile | None:
        png, meta = self._paths(key)
        try:
            data = png.read_bytes()
        except OSError:
            return None
        info: dict[str, Any] = {}
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if key not in self._touched:        # least recently *used*: a hit counts, once a session
            self._touched.add(key)
            try:
                os.utime(png)
            except OSError:
                pass
        return CachedTile(data, float(info.get("expires", 0.0)), str(info.get("etag", "")),
                          str(info.get("last_modified", "")))

    def put(self, key: TileKey, tile: CachedTile) -> None:
        png, meta = self._paths(key)
        try:
            png.parent.mkdir(parents=True, exist_ok=True)
            old = png.stat().st_size if png.exists() else 0
            tmp = png.with_suffix(".tmp")
            tmp.write_bytes(tile.data)
            os.replace(tmp, png)
            self._write_meta(meta, tile)
        except OSError:
            return
        with self._lock:
            if self._size is not None:
                self._size += len(tile.data) - old
        self._touched.add(key)
        self.trim()

    def refresh(self, key: TileKey, expires: float, etag: str = "", last_modified: str = "") -> None:
        """A 304 Not Modified: the tile on disk is good until ``expires``."""
        png, meta = self._paths(key)
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        info["expires"] = expires
        if etag:
            info["etag"] = etag
        if last_modified:
            info["last_modified"] = last_modified
        info["checked"] = time.time()
        try:
            meta.write_text(json.dumps(info), encoding="utf-8")
            os.utime(png)
        except OSError:
            pass

    def _write_meta(self, meta: Path, tile: CachedTile) -> None:
        meta.write_text(json.dumps({"expires": tile.expires, "etag": tile.etag,
                                    "last_modified": tile.last_modified, "fetched": time.time(),
                                    "url": self.source.url}), encoding="utf-8")

    def _scan(self) -> list[tuple[float, int, Path]]:
        out = []
        if not self.dir.is_dir():
            return out
        for path in self.dir.rglob("*.png"):
            try:
                st = path.stat()
            except OSError:
                continue
            out.append((st.st_mtime, st.st_size, path))
        return out

    def size_bytes(self) -> int:
        with self._lock:
            if self._size is None:
                self._size = sum(size for _, size, _ in self._scan())
            return self._size

    def trim(self) -> int:
        """Delete the least recently used tiles until the cache is under 90 %
        of its cap. Returns how many were deleted."""
        if self.size_bytes() <= self.cap:
            return 0
        files = sorted(self._scan())
        total = sum(size for _, size, _ in files)
        goal = int(self.cap * 0.9)
        gone = 0
        for _, size, path in files:
            if total <= goal:
                break
            for p in (path, path.with_suffix(".json")):
                try:
                    p.unlink()
                except OSError:
                    pass
            total -= size
            gone += 1
        with self._lock:
            self._size = total
        return gone

    def clear(self) -> None:
        for _, _, path in self._scan():
            for p in (path, path.with_suffix(".json")):
                try:
                    p.unlink()
                except OSError:
                    pass
        with self._lock:
            self._size = 0
            self._touched.clear()


# ---- the network ------------------------------------------------------------

@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes = b""


def http_get(url: str, headers: dict[str, str], timeout: float = TIMEOUT_S) -> Response:
    """One GET with urllib (proxies from the environment, as urllib does).
    A 304 or 4xx / 5xx comes back as a Response, not an exception; a
    connection failure raises OSError."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or 200         # file: URLs have none
            return Response(int(status), {k: v for k, v in resp.headers.items()}, resp.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, {k: v for k, v in (exc.headers or {}).items()}, b"")
    except urllib.error.URLError as exc:
        raise OSError(str(exc.reason)) from None


# Result.state
CACHED, FETCHED, MISSING = "cached", "fetched", "missing"


@dataclass
class Result:
    key: TileKey
    state: str                  # CACHED (from disk, maybe stale), FETCHED (new from the server), MISSING
    image: Any = None           # what ``decode`` made of the bytes (the bytes without one)
    stale: bool = False         # from disk and past its expiry


class TileFetcher:
    """Brings tiles to the UI without blocking it.

    ``want(keys)`` says which tiles the screen shows now (it replaces the
    last wish list: tiles scrolled away are not fetched). Worker threads look
    in the cache, then ask the server. ``poll()`` hands back what is ready.
    ``online=False`` never touches the network (the setting "download map
    tiles" off, or tests).
    """

    def __init__(self, cache: TileCache, *, decode: Callable[[bytes], Any] | None = None,
                 get: Callable[[str, dict[str, str]], Response] | None = None,
                 online: bool = True, threads: int = MAX_CONNECTIONS):
        self.cache = cache
        self.source = cache.source
        self.decode = decode
        self.get = get or http_get
        self.online = online
        self._cv = threading.Condition()
        self._wanted: collections.OrderedDict[TileKey, None] = collections.OrderedDict()
        self._busy: set[TileKey] = set()
        self._done: collections.deque[Result] = collections.deque()
        self._known: dict[TileKey, str] = {}        # settled this session: CACHED / FETCHED / MISSING
        self._revalidated: set[TileKey] = set()
        self._offline_until = 0.0
        self.requests = 0                           # network requests made
        self.errors = 0
        self.last_error = ""
        self._stop = False
        self._threads = [threading.Thread(target=self._run, name=f"hondalog-tiles-{i}", daemon=True)
                         for i in range(max(1, min(threads, MAX_CONNECTIONS)))]
        for th in self._threads:
            th.start()

    # ---- UI side ----

    def want(self, keys: Iterable[TileKey]) -> None:
        """The tiles on screen, most important first. Replaces the last list."""
        with self._cv:
            self._wanted = collections.OrderedDict((k, None) for k in keys
                                                   if k not in self._busy and k not in self._known)
            if self._wanted:
                self._cv.notify_all()

    def forget(self, key: TileKey) -> None:
        """Ask for this tile again (the UI dropped its copy)."""
        with self._cv:
            self._known.pop(key, None)

    def poll(self, limit: int = 64) -> list[Result]:
        out = []
        while self._done and len(out) < limit:
            out.append(self._done.popleft())
        return out

    @property
    def pending(self) -> int:
        return len(self._wanted) + len(self._busy)

    @property
    def offline(self) -> bool:
        return not self.online or time.monotonic() < self._offline_until

    def set_online(self, on: bool) -> None:
        with self._cv:
            self.online = on
            self._offline_until = 0.0
            if on:      # tiles that were missing may be there now
                self._known = {k: v for k, v in self._known.items() if v != MISSING}
            self._cv.notify_all()

    def close(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        for th in self._threads:
            th.join(timeout=TIMEOUT_S + 1)

    # ---- workers ----

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._stop and not self._wanted:
                    self._cv.wait()
                if self._stop:
                    return
                key, _ = self._wanted.popitem(last=False)
                self._busy.add(key)
            try:
                self._one(key)
            except Exception as exc:        # a worker never dies
                self.errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._deliver(key, MISSING, None)
            finally:
                with self._cv:
                    self._busy.discard(key)

    def _decode(self, data: bytes) -> Any:
        return self.decode(data) if self.decode is not None else data

    def _deliver(self, key: TileKey, state: str, data: bytes | None, stale: bool = False,
                 image: Any = None) -> None:
        if image is None and data is not None:
            image = self._decode(data)
            if image is None:               # not an image after all
                state = MISSING
        with self._cv:
            if state != CACHED or not stale:
                self._known[key] = state
            else:
                self._known.setdefault(key, CACHED)
        self._done.append(Result(key, state, image, stale))

    def _net_ok(self) -> bool:
        return self.online and time.monotonic() >= self._offline_until

    def _one(self, key: TileKey) -> None:
        now = time.time()
        z, x, y = key
        if not (self.source.min_zoom <= z <= self.source.max_zoom) or not 0 <= x < 2 ** z or not 0 <= y < 2 ** z:
            self._deliver(key, MISSING, None)
            return
        cached = self.cache.get(key)
        if cached is not None:
            stale = not cached.fresh(now)
            self._deliver(key, CACHED, cached.data, stale)
            if not stale or not self._net_ok() or key in self._revalidated:
                return
            self._revalidated.add(key)
        elif not self._net_ok():
            self._deliver(key, MISSING, None)
            return
        headers = {"User-Agent": user_agent(), "Accept": "image/png,image/*;q=0.8"}
        if cached is not None:
            if cached.etag:
                headers["If-None-Match"] = cached.etag
            if cached.last_modified:
                headers["If-Modified-Since"] = cached.last_modified
        url = self.source.tile_url(z, x, y)
        try:
            self.requests += 1
            resp = self.get(url, headers)
        except OSError as exc:
            self.errors += 1
            self.last_error = str(exc)
            self._offline_until = time.monotonic() + OFFLINE_BACKOFF_S
            if cached is None:
                self._deliver(key, MISSING, None)
            return
        now = time.time()
        expires = max(now + MIN_TTL_S, expiry(resp.headers, now)) if resp.status in (200, 304) else now
        etag = resp.headers.get("ETag") or resp.headers.get("etag") or ""
        modified = resp.headers.get("Last-Modified") or resp.headers.get("last-modified") or ""
        if resp.status == 304 and cached is not None:
            self.cache.refresh(key, expires, etag, modified)
            with self._cv:
                self._known[key] = CACHED
            return
        if resp.status == 200 and resp.body:
            image = self._decode(resp.body)
            if image is None:
                self.errors += 1
                self.last_error = f"{url}: not an image"
                if cached is None:
                    self._deliver(key, MISSING, None)
                return
            self.cache.put(key, CachedTile(resp.body, expires, etag, modified))
            self._deliver(key, FETCHED, resp.body, image=image)
            return
        self.errors += 1
        self.last_error = f"{url}: HTTP {resp.status}"
        if resp.status == 429 or resp.status >= 500:    # the server asks us to back off
            self._offline_until = time.monotonic() + OFFLINE_BACKOFF_S
        if cached is None:
            self._deliver(key, MISSING, None)


__all__ = ["CACHED", "DEFAULT_CAP_MB", "FETCHED", "MISSING", "OSM_ATTRIBUTION", "OSM_URL", "REPO_URL",
           "CachedTile", "Response", "Result", "TileCache", "TileFetcher", "TileKey", "TileSource",
           "cache_root", "expiry", "http_get", "source_for", "user_agent"]
