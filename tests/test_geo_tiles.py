"""Map tiles against a fake server: the cache, the headers sent (User-Agent,
conditional requests, never no-cache), offline, the size cap, and no
prefetching. No test here, or anywhere, reaches a real tile server."""

import os
import threading
import time
from email.utils import formatdate

import pytest

from hondalog import __version__
from hondalog.geo import tiles as gt

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 200          # the fetcher does not decode without a decoder


class Server:
    """A fake tile server: records every request, answers from a script."""

    def __init__(self, status=200, headers=None, body=PNG, fail=False):
        self.calls: list[tuple[str, dict]] = []
        self.status, self.body, self.fail = status, body, fail
        self.headers = headers if headers is not None else {"Cache-Control": "max-age=3600", "ETag": '"v1"'}
        self.lock = threading.Lock()
        self.active = 0
        self.most = 0

    def __call__(self, url, headers):
        with self.lock:
            self.calls.append((url, dict(headers)))
            self.active += 1
            self.most = max(self.most, self.active)
        try:
            time.sleep(0.01)
            if self.fail:
                raise OSError("network is unreachable")
            if "If-None-Match" in headers and headers["If-None-Match"] == self.headers.get("ETag"):
                return gt.Response(304, {"Cache-Control": "max-age=7200"})
            return gt.Response(self.status, dict(self.headers), self.body if self.status == 200 else b"")
        finally:
            with self.lock:
                self.active -= 1


def drain(fetcher, n, timeout=5.0):
    """Wait for ``n`` results."""
    out = []
    end = time.monotonic() + timeout
    while len(out) < n and time.monotonic() < end:
        out += fetcher.poll()
        time.sleep(0.005)
    return out


def idle(fetcher, timeout=5.0):
    end = time.monotonic() + timeout
    while fetcher.pending and time.monotonic() < end:
        time.sleep(0.005)
    time.sleep(0.03)


@pytest.fixture
def source():
    return gt.TileSource("https://tiles.test/{z}/{x}/{y}.png", "© test contributors")


def test_defaults_follow_the_osm_policy():
    src = gt.source_for("")
    assert src.url == gt.OSM_URL == "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    assert src.attribution == "© OpenStreetMap contributors" and src.is_osm and src.max_zoom == 19
    assert gt.user_agent() == f"hondalog/{__version__} (+https://github.com/olosnoks/hon-diy-data)"
    own = gt.source_for("https://maps.example/{z}/{x}/{y}.png", "© me")
    assert own.attribution == "© me" and not own.is_osm and own.slug != src.slug
    assert gt.source_for("https://maps.example/{z}/{x}/{y}.png").attribution == ""
    assert not gt.TileSource("https://maps.example/tiles.png").valid
    assert gt.TileSource("https://a.example/{s}/{z}/{x}/{y}.png").tile_url(3, 1, 2) == "https://a.example/a/3/1/2.png"
    assert gt.MAX_CONNECTIONS <= 2


def test_cache_headers():
    now = 1_800_000_000.0
    assert gt.expiry({"Cache-Control": "public, max-age=600"}, now) == now + 600
    assert gt.expiry({"Cache-Control": "max-age=600", "Age": "100"}, now) == now + 500
    assert gt.expiry({"Cache-Control": "no-cache"}, now) == now
    assert gt.expiry({}, now) == now + gt.DEFAULT_TTL_S
    date = formatdate(now - 50, usegmt=True)                # the server's clock is 50 s behind ours
    exp = formatdate(now - 50 + 3600, usegmt=True)
    assert gt.expiry({"Expires": exp, "Date": date}, now) == pytest.approx(now + 3600)
    assert gt.expiry({"Expires": "0"}, now) == now


def test_fetch_sends_a_real_user_agent_and_caches(tmp_path, source):
    server = Server()
    cache = gt.TileCache(source, tmp_path)
    f = gt.TileFetcher(cache, get=server)
    try:
        f.want([(15, 8179, 13277)])
        res = drain(f, 1)
        assert [r.state for r in res] == [gt.FETCHED] and res[0].image == PNG
        url, headers = server.calls[0]
        assert url == "https://tiles.test/15/8179/13277.png"
        assert headers["User-Agent"] == gt.user_agent() and "hondalog/" in headers["User-Agent"]
        assert not any(k.lower() in ("cache-control", "pragma") for k in headers)      # never no-cache
        assert "If-None-Match" not in headers
        assert (tmp_path / source.slug / "15" / "8179" / "13277.png").read_bytes() == PNG
        # asked again (the UI dropped it): from disk, no request
        f.forget((15, 8179, 13277))
        f.want([(15, 8179, 13277)])
        assert [r.state for r in drain(f, 1)] == [gt.CACHED]
        idle(f)
        assert len(server.calls) == 1
    finally:
        f.close()


def test_stale_tiles_show_at_once_then_revalidate(tmp_path, source):
    server = Server(headers={"Cache-Control": "max-age=3600", "ETag": '"v1"', "Last-Modified":
                             "Mon, 02 Nov 2026 07:12:00 GMT"})
    cache = gt.TileCache(source, tmp_path)
    cache.put((14, 1, 2), gt.CachedTile(PNG, time.time() - 10, '"v1"', "Mon, 02 Nov 2026 07:12:00 GMT"))
    f = gt.TileFetcher(cache, get=server)
    try:
        f.want([(14, 1, 2)])
        res = drain(f, 1)
        assert res[0].state == gt.CACHED and res[0].stale                 # offline first: shown now
        idle(f)
        assert len(server.calls) == 1
        headers = server.calls[0][1]
        assert headers["If-None-Match"] == '"v1"' and headers["If-Modified-Since"].startswith("Mon, 02 Nov 2026")
        assert cache.get((14, 1, 2)).fresh(time.time())                    # the 304 moved the expiry on
        assert drain(f, 1, timeout=0.2) == []                              # nothing new to show
    finally:
        f.close()


def test_offline_shows_the_cache_and_backs_off(tmp_path, source):
    server = Server(fail=True)
    cache = gt.TileCache(source, tmp_path)
    cache.put((14, 5, 5), gt.CachedTile(PNG, time.time() - 999))           # stale, but all there is
    f = gt.TileFetcher(cache, get=server)
    try:
        f.want([(14, 5, 5), (14, 5, 6)])
        res = {r.key: r for r in drain(f, 2)}
        assert res[(14, 5, 5)].state == gt.CACHED and res[(14, 5, 6)].state == gt.MISSING
        idle(f)
        assert f.offline and f.errors >= 1
        calls = len(server.calls)
        f.want([(14, 7, 7), (14, 7, 8)])                                   # during the back-off: no network
        assert [r.state for r in drain(f, 2)] == [gt.MISSING, gt.MISSING]
        assert len(server.calls) == calls
    finally:
        f.close()
    # downloads switched off: the network is never touched
    server2 = Server()
    f = gt.TileFetcher(gt.TileCache(source, tmp_path), get=server2, online=False)
    try:
        f.want([(14, 5, 5), (14, 9, 9)])
        states = sorted(r.state for r in drain(f, 2))
        assert states == [gt.CACHED, gt.MISSING] and server2.calls == []
    finally:
        f.close()


def test_errors_and_out_of_range(tmp_path, source):
    server = Server(status=404)
    f = gt.TileFetcher(gt.TileCache(source, tmp_path), get=server)
    try:
        f.want([(14, 3, 3), (20, 0, 0), (3, 9, 0)])                       # 404, past max zoom, x off the world
        assert sorted(r.state for r in drain(f, 3)) == [gt.MISSING] * 3
        idle(f)
        assert [c[0] for c in server.calls] == ["https://tiles.test/14/3/3.png"]
        assert "HTTP 404" in f.last_error
    finally:
        f.close()


def test_only_what_is_wanted_now_is_fetched(tmp_path, source):
    """No prefetch, no bulk download: a new wish list replaces the old one."""
    gate = threading.Event()
    server = Server()

    def slow(url, headers):
        gate.wait(2.0)
        return server(url, headers)
    f = gt.TileFetcher(gt.TileCache(source, tmp_path), get=slow)
    try:
        first = [(16, x, 100) for x in range(40)]
        f.want(first)
        time.sleep(0.05)                                                   # two are in flight now
        f.want([(16, 500, 500)])                                           # the view moved on
        gate.set()
        idle(f)
        asked = [c[0] for c in server.calls]
        assert len(asked) <= 3 and "https://tiles.test/16/500/500.png" in asked
        assert server.most <= gt.MAX_CONNECTIONS
    finally:
        f.close()


def test_size_cap_drops_the_least_recently_used(tmp_path, source):
    cache = gt.TileCache(source, tmp_path, cap_mb=0.01)                    # 10 kB
    blob = b"p" * 3000
    for i in range(3):
        cache.put((10, i, 0), gt.CachedTile(blob, 4e9))
        old = time.time() - 2 * 86400 + i                                 # used two days ago
        os.utime(cache._paths((10, i, 0))[0], (old, old))
    assert cache.get((10, 0, 0)) is not None                               # used now: the newest
    cache.put((10, 3, 0), gt.CachedTile(blob, 4e9))                        # 12 kB: over the cap
    assert cache.size_bytes() <= 10_000 * 0.9 + 1
    kept = {i for i in range(4) if cache.get((10, i, 0)) is not None}
    assert kept == {0, 2, 3}                    # 1 was the least recently used; 0 was just read
    cache.clear()
    assert cache.size_bytes() == 0 and cache.get((10, 3, 0)) is None


def test_cache_root_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("HONDALOG_TILE_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert gt.cache_root() == tmp_path / "xdg" / "hondalog" / "tiles"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert str(gt.cache_root()).endswith(".cache/hondalog/tiles")
    monkeypatch.setenv("HONDALOG_TILE_CACHE", str(tmp_path / "t"))
    assert gt.cache_root() == tmp_path / "t"


def test_the_test_suite_is_kept_offline():
    assert os.environ.get("HONDALOG_TILES") == "offline"
