"""Build the handheld's offline road map (``MAP/``) from OpenStreetMap extracts.

    hondalog-handheld map-build --pbf mississippi-latest.osm.pbf --pbf louisiana-latest.osm.pbf \\
        --center 32.2988,-90.1848 --radius-mi 100 --out /media/SDCARD

Each extract is read once with pyosmium (node locations in memory, areas
assembled from closed ways and multipolygons). Every feature is projected,
simplified (Douglas-Peucker) for each of the four levels it appears on,
clipped to that level's tiles and quantised on the spot, so memory grows with
the output, not with the extract. Objects that are in two extracts (ways
across a state line) are taken once.

Map data (c) OpenStreetMap contributors, ODbL. The output is a Produced Work
and carries the attribution in its header and in MAP/README.TXT.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import osmmap as M

MILE = 1609.344

# (tile side m, Douglas-Peucker tolerance m, metres per screen pixel it is drawn at)
LEVELS = [(1024, 2.0, 4.0), (4096, 5.0, 10.0), (8192, 12.0, 25.0), (32768, 30.0, 60.0)]

# highest level (0 = closest) each class is on
MAX_LEVEL = {
    M.C_MOTORWAY: 3, M.C_TRUNK: 3, M.C_PRIMARY: 3, M.C_SECONDARY: 3, M.C_LINK_MAJOR: 2, M.C_TERTIARY: 2,
    M.C_LINK: 1, M.C_RESIDENTIAL: 1, M.C_SERVICE: 0, M.C_RAIL: 2, M.C_RIVER: 3, M.C_CANAL: 1, M.C_WATER: 3,
    M.C_CITY: 3, M.C_TOWN: 3, M.C_VILLAGE: 2, M.C_HAMLET: 1, M.C_SUBURB: 1, M.C_FUEL: 1,
}
WATER_MIN_M2 = [1_000.0, 10_000.0, 60_000.0, 500_000.0]      # smallest lake drawn on each level
# road labels: a ref ("I-55") on these levels; a name ("Lakeland Dr") only on the closer ones
REF_LEVEL = {M.C_MOTORWAY: 3, M.C_TRUNK: 3, M.C_PRIMARY: 3, M.C_SECONDARY: 2, M.C_TERTIARY: 0}
NAME_LEVEL = {M.C_MOTORWAY: 1, M.C_TRUNK: 1, M.C_PRIMARY: 1, M.C_SECONDARY: 1, M.C_TERTIARY: 0}
PLACE_POP = {M.C_CITY: 50_000, M.C_TOWN: 5_000, M.C_VILLAGE: 500, M.C_HAMLET: 50, M.C_SUBURB: 1_000}

HIGHWAY = {
    "motorway": M.C_MOTORWAY, "trunk": M.C_TRUNK, "primary": M.C_PRIMARY, "secondary": M.C_SECONDARY,
    "tertiary": M.C_TERTIARY, "tertiary_link": M.C_TERTIARY, "motorway_link": M.C_LINK_MAJOR,
    "trunk_link": M.C_LINK_MAJOR, "primary_link": M.C_LINK, "secondary_link": M.C_LINK,
    "residential": M.C_RESIDENTIAL, "unclassified": M.C_RESIDENTIAL, "living_street": M.C_RESIDENTIAL,
    "road": M.C_RESIDENTIAL, "service": M.C_SERVICE,
}
PLACE = {"city": M.C_CITY, "town": M.C_TOWN, "village": M.C_VILLAGE, "hamlet": M.C_HAMLET,
         "suburb": M.C_SUBURB, "neighbourhood": M.C_SUBURB, "quarter": M.C_SUBURB}
WATERWAY = {"river": M.C_RIVER, "canal": M.C_CANAL}

ABBREV = {"Street": "St", "Avenue": "Ave", "Road": "Rd", "Drive": "Dr", "Boulevard": "Blvd", "Parkway": "Pkwy",
          "Highway": "Hwy", "Lane": "Ln", "Place": "Pl", "Circle": "Cir", "Expressway": "Expy",
          "Frontage": "Frtg", "Court": "Ct", "Terrace": "Ter", "Trail": "Trl", "Memorial": "Mem"}
DIRS = {"North": "N", "South": "S", "East": "E", "West": "W", "Northeast": "NE", "Northwest": "NW",
        "Southeast": "SE", "Southwest": "SW"}


# ---- names ---------------------------------------------------------------------------

def road_ref(ref: str) -> str:
    """The first ref, as the sign shows it: "I 20;I 55" -> "I-20", "US 49" -> "US 49"."""
    first = ref.split(";")[0].strip()
    m = re.fullmatch(r"I[ -]?(\d+[A-Z]?)", first)
    return f"I-{m.group(1)}" if m else first


def short_name(name: str) -> str:
    """"North State Street" -> "N State St"."""
    words = name.split()
    if len(words) > 1 and words[0] in DIRS:
        words[0] = DIRS[words[0]]
    if len(words) > 1 and words[-1] in DIRS:
        words[-1] = DIRS[words[-1]]
    return " ".join(ABBREV.get(w, w) for w in words)


def place_rank(cls: int, population: str | None) -> int:
    pop = None
    if population:
        digits = re.sub(r"[^\d]", "", population.split(";")[0])
        pop = int(digits) if digits else None
    if pop is None:
        pop = PLACE_POP.get(cls, 0)
    return max(0, min(255, round(16 * math.log10(pop + 1))))


# ---- geometry -------------------------------------------------------------------------

def simplify(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker. A closed ring (first == last) keeps its closing point."""
    n = len(pts)
    if n < 3 or tol <= 0:
        return pts
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    tol2 = tol * tol
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = pts[b] - pts[a]
        d = pts[a + 1:b] - pts[a]
        l2 = float(seg @ seg)
        if l2 == 0.0:
            dist2 = (d * d).sum(1)
        else:
            cross = d[:, 0] * seg[1] - d[:, 1] * seg[0]
            dist2 = cross * cross / l2
        i = int(np.argmax(dist2))
        if dist2[i] > tol2:
            k = a + 1 + i
            keep[k] = True
            stack.append((a, k))
            stack.append((k, b))
    return pts[keep]


def clip_line(p: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> list[np.ndarray]:
    """Liang-Barsky on every segment at once; the visible parts joined back
    into polylines."""
    if len(p) < 2:
        return []
    a, b = p[:-1], p[1:]
    d = b - a
    t0 = np.zeros(len(a))
    t1 = np.ones(len(a))
    with np.errstate(divide="ignore", invalid="ignore"):
        for pp, qq in ((-d[:, 0], a[:, 0] - x0), (d[:, 0], x1 - a[:, 0]),
                       (-d[:, 1], a[:, 1] - y0), (d[:, 1], y1 - a[:, 1])):
            r = qq / pp
            t0 = np.where(pp < 0, np.maximum(t0, r), t0)
            t1 = np.where(pp > 0, np.minimum(t1, r), t1)
            t1 = np.where((pp == 0) & (qq < 0), -1.0, t1)
    vis = t1 - t0 > 1e-12
    if not vis.any():
        return []
    s = a + d * t0[:, None]
    e = a + d * t1[:, None]
    # snap what was cut to the exact boundary: neighbouring tiles then agree
    for arr, t in ((s, t0), (e, t1)):
        cut = (t > 0) & (t < 1)
        for col, lo, hi in ((0, x0, x1), (1, y0, y1)):
            v = arr[:, col]
            v[cut & (np.abs(v - lo) < 1e-6)] = lo
            v[cut & (np.abs(v - hi) < 1e-6)] = hi
    idx = np.flatnonzero(vis)
    joined = np.zeros(len(idx), bool)
    if len(idx) > 1:
        joined[1:] = (np.diff(idx) == 1) & (t1[idx[:-1]] >= 1.0) & (t0[idx[1:]] <= 0.0)
    out = []
    starts = np.flatnonzero(~joined)
    for k, st in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(idx)
        run = idx[st:end]
        out.append(np.vstack([s[run[0]][None, :], e[run]]))
    return out


def _clip_half(r: np.ndarray, axis: int, val: float, keep_ge: bool) -> np.ndarray:
    v = r[:, axis]
    ins = v >= val if keep_ge else v <= val
    if ins.all():
        return r
    if not ins.any():
        return r[:0]
    e = np.roll(r, -1, axis=0)
    ins_e = np.roll(ins, -1)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (val - v) / (e[:, axis] - v)
        cut = r + (e - r) * t[:, None]
    cut[:, axis] = val
    both, leave, enter = ins & ins_e, ins & ~ins_e, ~ins & ins_e
    count = both.astype(int) + leave + 2 * enter
    out = np.empty((int(count.sum()), 2))
    pos = np.cumsum(count) - count
    first = np.where(both[:, None], e, cut)
    m = count > 0
    out[pos[m]] = first[m]
    out[pos[enter] + 1] = e[enter]
    return out


def clip_ring(r: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    """Sutherland-Hodgman against a rectangle; ``r`` without its closing point."""
    for axis, val, ge in ((0, x0, True), (0, x1, False), (1, y0, True), (1, y1, False)):
        r = _clip_half(r, axis, val, ge)
        if len(r) < 3:
            return r[:0]
    return r


def ring_area(r: np.ndarray) -> float:
    x, y = r[:, 0], r[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


# ---- tiles ----------------------------------------------------------------------------

@dataclass
class Grid:
    level: int
    tile_m: int
    tol_m: float
    origin: float          # -R: the same for x and y
    n: int                 # cols = rows
    radius: float
    keep: np.ndarray = field(default=None)      # [row, col] -> the tile touches the circle

    def __post_init__(self) -> None:
        c = self.origin + (np.arange(self.n) + 0.5) * self.tile_m
        dx = np.maximum(np.abs(c) - self.tile_m / 2, 0)
        self.keep = (dx[:, None] ** 2 + dx[None, :] ** 2) <= self.radius ** 2

    def rect(self, c0: int, r0: int, c1: int, r1: int) -> tuple[float, float, float, float]:
        o, s = self.origin, self.tile_m
        return o + c0 * s, o + r0 * s, o + c1 * s, o + r1 * s

    def span(self, lo: float, hi: float) -> tuple[int, int]:
        a = int(math.floor((lo - self.origin) / self.tile_m))
        b = int(math.floor((hi - self.origin) / self.tile_m)) + 1
        return max(a, 0), min(b, self.n)


class TileSink:
    """Per-tile lists of features already quantised and packed."""

    def __init__(self, grids: list[Grid]):
        self.grids = grids
        self.tiles: dict[tuple[int, int, int], list[tuple]] = defaultdict(list)
        self.points = [0] * len(grids)

    def _quant(self, g: Grid, col: int, row: int, xy: np.ndarray) -> np.ndarray:
        cx, cy = g.origin + (col + 0.5) * g.tile_m, g.origin + (row + 0.5) * g.tile_m
        q = np.rint((xy - (cx, cy)) * (M.Q / g.tile_m))
        q = np.clip(q, -32768, 32767).astype("<i2")
        if len(q) > 1:
            keep = np.ones(len(q), bool)
            keep[1:] = np.any(q[1:] != q[:-1], axis=1)
            q = q[keep]
        return q

    def add(self, g: Grid, col: int, row: int, cls: int, rings: list[np.ndarray], name: str, rank: int,
            ref: bool = False) -> None:
        if not g.keep[row, col]:
            return
        geom = M.geom_of(cls)
        packed = []
        for ring in rings:
            q = self._quant(g, col, row, ring)
            if geom == M.G_AREA:
                if len(q) > 1 and (q[0] == q[-1]).all():
                    q = q[:-1]
                if len(q) < 3:
                    continue
            elif geom == M.G_LINE and len(q) < 2:
                continue
            packed.append(q)
        if not packed:
            return
        npts = sum(len(q) for q in packed)
        if npts > 0xFFFF:              # never on these levels; split rather than fail
            for q in packed:
                for i in range(0, len(q), 0xFFF0):
                    self.add(g, col, row, cls, [self._unquant(g, col, row, q[max(0, i - 1):i + 0xFFF0])], name, rank,
                             ref)
            return
        self.tiles[(g.level, col, row)].append((M.LAYER_OF[cls], cls, name, rank, packed, ref))
        self.points[g.level] += npts

    def _unquant(self, g: Grid, col: int, row: int, q: np.ndarray) -> np.ndarray:
        cx, cy = g.origin + (col + 0.5) * g.tile_m, g.origin + (row + 0.5) * g.tile_m
        return q.astype(float) * (g.tile_m / M.Q) + (cx, cy)

    def encode(self) -> dict[tuple[int, int, int], bytes]:
        out = {}
        for key, items in self.tiles.items():
            items.sort(key=lambda it: it[0])
            out[key] = M.pack_tile(key[0], [it[1:] for it in items])
        return out


def distribute_line(sink: TileSink, g: Grid, cls: int, pts: np.ndarray, name: str, ref: bool = False) -> None:
    lo, hi = pts.min(0), pts.max(0)
    c0, c1 = g.span(lo[0], hi[0])
    r0, r1 = g.span(lo[1], hi[1])
    if c0 >= c1 or r0 >= r1:
        return
    if c1 - c0 == 1 and r1 - r0 == 1 and _inside(g.rect(c0, r0, c1, r1), lo, hi):
        sink.add(g, c0, r0, cls, [pts], name, -1, ref)
        return
    _split_lines(sink, g, cls, clip_line(pts, *g.rect(c0, r0, c1, r1)), name, ref, c0, r0, c1, r1)


def _inside(rect, lo, hi) -> bool:
    return lo[0] >= rect[0] and lo[1] >= rect[1] and hi[0] <= rect[2] and hi[1] <= rect[3]


def _quarters(c0: int, r0: int, c1: int, r1: int):
    cm = (c0 + c1) // 2 if c1 - c0 > 1 else c1
    rm = (r0 + r1) // 2 if r1 - r0 > 1 else r1
    for a, b in ((c0, cm), (cm, c1)):
        for c, d in ((r0, rm), (rm, r1)):
            if a < b and c < d:
                yield a, c, b, d


def _split_lines(sink, g, cls, pieces, name, ref, c0, r0, c1, r1) -> None:
    if not pieces:
        return
    if c1 - c0 == 1 and r1 - r0 == 1:
        for p in pieces:
            sink.add(g, c0, r0, cls, [p], name, -1, ref)
        return
    for a, c, b, d in _quarters(c0, r0, c1, r1):
        if not g.keep[c:d, a:b].any():
            continue
        rect = g.rect(a, c, b, d)
        sub = []
        for p in pieces:
            lo, hi = p.min(0), p.max(0)
            if hi[0] < rect[0] or lo[0] > rect[2] or hi[1] < rect[1] or lo[1] > rect[3]:
                continue
            sub.extend([p] if _inside(rect, lo, hi) else clip_line(p, *rect))
        _split_lines(sink, g, cls, sub, name, ref, a, c, b, d)


def distribute_area(sink: TileSink, g: Grid, rings: list[np.ndarray]) -> None:
    lo = np.min([r.min(0) for r in rings], axis=0)
    hi = np.max([r.max(0) for r in rings], axis=0)
    c0, c1 = g.span(lo[0], hi[0])
    r0, r1 = g.span(lo[1], hi[1])
    if c0 >= c1 or r0 >= r1:
        return
    _split_area(sink, g, rings, c0, r0, c1, r1, first=True)


def _split_area(sink, g, rings, c0, r0, c1, r1, first=False) -> None:
    rect = g.rect(c0, r0, c1, r1)
    out = []
    for r in rings:
        lo, hi = r.min(0), r.max(0)
        if hi[0] <= rect[0] or lo[0] >= rect[2] or hi[1] <= rect[1] or lo[1] >= rect[3]:
            continue
        cr = r if _inside(rect, lo, hi) else clip_ring(r, *rect)
        if len(cr) >= 3 and abs(ring_area(cr)) > 1e-6:
            out.append(cr)
    if not out:
        return
    if c1 - c0 == 1 and r1 - r0 == 1:
        sink.add(g, c0, r0, M.C_WATER, out, "", -1)
        return
    for a, c, b, d in _quarters(c0, r0, c1, r1):
        if g.keep[c:d, a:b].any():
            _split_area(sink, g, out, a, c, b, d)


# ---- reading the extracts ----------------------------------------------------------------

def extract_date(path: str | Path) -> str:
    """The extract's replication timestamp ("2026-11-15"), else "undated"."""
    import osmium
    stamp = ""
    try:
        reader = osmium.io.Reader(str(path), osmium.osm.osm_entity_bits.NOTHING)
        stamp = reader.header().get("osmosis_replication_timestamp", "")
        reader.close()
    except Exception:          # pragma: no cover - a damaged header only costs the date
        stamp = ""
    return stamp[:10] if stamp else "undated"


def _source_name(path: Path) -> str:
    name = path.name
    for suffix in (".osm.pbf", ".pbf", ".osm.bz2", ".osm.gz", ".osm"):
        if name.endswith(suffix):
            return name[: -len(suffix)].removesuffix("-latest")
    return name


@dataclass
class BuildReport:
    folder: Path = Path()
    seconds: float = 0.0
    idx_bytes: int = 0
    dat_bytes: int = 0
    tiles: list[int] = field(default_factory=list)
    level_bytes: list[int] = field(default_factory=list)
    level_points: list[int] = field(default_factory=list)
    features: Counter = field(default_factory=Counter)
    duplicates: int = 0
    sources: str = ""
    zip_path: Path | None = None
    zip_bytes: int = 0

    @property
    def total_bytes(self) -> int:
        return self.idx_bytes + self.dat_bytes

    def lines(self) -> list[str]:
        out = [f"{self.folder}: {self.total_bytes / 1e6:.1f} MB (MAP.IDX {self.idx_bytes / 1e6:.2f} MB, "
               f"MAP.DAT {self.dat_bytes / 1e6:.1f} MB) in {self.seconds:.0f} s"]
        for i, (n, b, p) in enumerate(zip(self.tiles, self.level_bytes, self.level_points)):
            tile_m, tol, mpp = LEVELS[i]
            out.append(f"  level {i}: {n:6d} tiles of {tile_m / 1000:g} km, {b / 1e6:6.1f} MB, {p:9d} points "
                       f"(simplified to {tol:g} m; drawn at {mpp:g} m/px)")
        kinds = ", ".join(f"{M.CLASS_NAMES.get(c, c)} {n}" for c, n in sorted(self.features.items()))
        out.append(f"  features: {kinds}")
        if self.duplicates:
            out.append(f"  {self.duplicates} objects were in more than one extract and were taken once")
        out.append(f"  sources: {self.sources}")
        if self.zip_path:
            out.append(f"  zip: {self.zip_path} ({self.zip_bytes / 1e6:.1f} MB)")
        return out


class Builder:
    def __init__(self, lat: float, lon: float, radius_m: float, *, rail: bool = True, fuel: bool = True,
                 progress=None):
        self.proj = M.Projection(lat, lon)
        self.radius = radius_m
        n0 = math.ceil(2 * radius_m / LEVELS[0][0])
        self.grids = [Grid(i, s, tol, -radius_m, math.ceil(2 * radius_m / s), radius_m)
                      for i, (s, tol, _) in enumerate(LEVELS)]
        assert self.grids[0].n == n0
        self.sink = TileSink(self.grids)
        self.rail, self.fuel = rail, fuel
        self.seen: set[tuple[str, int]] = set()
        self.report = BuildReport()
        self.progress = progress or (lambda msg: None)

    # -- one feature -----------------------------------------------------------------
    def _project(self, coords: list[tuple[float, float]]) -> np.ndarray:
        a = np.asarray(coords, dtype=float)
        return np.column_stack(((a[:, 0] - self.proj.lon0) * self.proj.kx, (a[:, 1] - self.proj.lat0) * self.proj.ky))

    def _near(self, lo: np.ndarray, hi: np.ndarray) -> bool:
        r = self.radius
        dx = max(lo[0], 0.0, -hi[0])
        dy = max(lo[1], 0.0, -hi[1])
        return dx * dx + dy * dy <= r * r and hi[0] >= -r and lo[0] <= r and hi[1] >= -r and lo[1] <= r

    def add_line(self, cls: int, coords, ref: str = "", name: str = "") -> None:
        pts = self._project(coords)
        if len(pts) < 2 or not self._near(pts.min(0), pts.max(0)):
            return
        self.report.features[cls] += 1
        for g in self.grids:
            if g.level > MAX_LEVEL[cls]:
                break
            pts = simplify(pts, g.tol_m)
            label, is_ref = "", False
            if ref and g.level <= REF_LEVEL.get(cls, -1):
                label, is_ref = road_ref(ref), True
            elif name and g.level <= NAME_LEVEL.get(cls, -1):
                label = short_name(name)
            distribute_line(self.sink, g, cls, pts, label, is_ref)

    def add_area(self, rings_ll: list[list[tuple[float, float]]]) -> None:
        rings = [self._project(r) for r in rings_ll if len(r) >= 4]
        if not rings:
            return
        lo = np.min([r.min(0) for r in rings], axis=0)
        hi = np.max([r.max(0) for r in rings], axis=0)
        if not self._near(lo, hi):
            return
        area = abs(ring_area(rings[0])) - sum(abs(ring_area(r)) for r in rings[1:])
        if area < WATER_MIN_M2[0]:
            return
        self.report.features[M.C_WATER] += 1
        for g in self.grids:
            if area < WATER_MIN_M2[g.level]:
                break
            simple = []
            for r in rings:
                s = simplify(r, g.tol_m)
                if len(s) > 1 and (s[0] == s[-1]).all():
                    s = s[:-1]
                if len(s) >= 3:
                    simple.append(s)
            if simple and len(simple[0]) >= 3:
                distribute_area(self.sink, g, simple)

    def add_point(self, cls: int, lat: float, lon: float, name: str = "", rank: int = -1) -> None:
        x, y = self.proj.fwd(lat, lon)
        if x * x + y * y > self.radius ** 2:
            return
        self.report.features[cls] += 1
        p = np.array([[x, y]])
        for g in self.grids:
            if g.level > MAX_LEVEL[cls]:
                break
            col, row = int((x - g.origin) // g.tile_m), int((y - g.origin) // g.tile_m)
            if 0 <= col < g.n and 0 <= row < g.n:
                self.sink.add(g, col, row, cls, [p], name, rank)

    def _first(self, key: tuple[str, int]) -> bool:
        if key in self.seen:
            self.report.duplicates += 1
            return False
        self.seen.add(key)
        return True

    # -- one extract -----------------------------------------------------------------
    def read(self, path: str | Path) -> None:
        import osmium
        water = osmium.filter.TagFilter(("natural", "water"), ("waterway", "riverbank"), ("landuse", "reservoir"))
        keys = ["highway", "waterway", "place", "natural", "landuse"] + (["railway"] if self.rail else []) \
            + (["amenity"] if self.fuel else [])
        fp = (osmium.FileProcessor(str(path)).with_locations("flex_mem").with_areas(water)
              .with_filter(osmium.filter.KeyFilter(*keys)))
        n = 0
        for o in fp:
            n += 1
            if n % 200_000 == 0:
                self.progress(f"  {n} objects")
            try:
                if o.is_area():
                    self._area(o)
                elif o.is_way():
                    self._way(o)
                elif o.is_node():
                    self._node(o)
            except osmium.InvalidLocationError:
                continue

    def _way(self, o) -> None:
        t = o.tags
        hw = t.get("highway")
        cls = HIGHWAY.get(hw) if hw else None
        if cls is None and "waterway" in t:
            cls = WATERWAY.get(t.get("waterway"))
        if cls is None and self.rail and t.get("railway") == "rail" and "service" not in t:
            cls = M.C_RAIL
        if cls is None and self.fuel and t.get("amenity") == "fuel":
            if self._first(("w", o.id)):
                pts = [(nd.lon, nd.lat) for nd in o.nodes]
                if pts:
                    self.add_point(M.C_FUEL, sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))
            return
        if cls is None or t.get("area") == "yes" or (hw and t.get("access") in ("private", "no") and cls == M.C_SERVICE):
            return
        if not self._first(("w", o.id)):
            return
        self.add_line(cls, [(nd.lon, nd.lat) for nd in o.nodes], t.get("ref", ""), t.get("name", ""))

    def _area(self, o) -> None:
        t = o.tags
        if not (t.get("natural") == "water" or t.get("waterway") == "riverbank" or t.get("landuse") == "reservoir"):
            return
        if t.get("water") in ("wastewater",) or not self._first(("a", o.id)):
            return
        for outer in o.outer_rings():
            rings = [[(nd.lon, nd.lat) for nd in outer]]
            rings += [[(nd.lon, nd.lat) for nd in inner] for inner in o.inner_rings(outer)]
            self.add_area(rings)

    def _node(self, o) -> None:
        t = o.tags
        place = t.get("place")
        if place in PLACE and t.get("name"):
            if self._first(("n", o.id)):
                cls = PLACE[place]
                self.add_point(cls, o.location.lat, o.location.lon, t.get("name"), place_rank(cls, t.get("population")))
        elif self.fuel and t.get("amenity") == "fuel":
            if self._first(("n", o.id)):
                self.add_point(M.C_FUEL, o.location.lat, o.location.lon)

    # -- output ----------------------------------------------------------------------
    def header(self, build_unix: int, sources: str) -> M.Header:
        r = self.radius
        lat_lo, lon_lo = self.proj.inv(-r, -r)
        lat_hi, lon_hi = self.proj.inv(r, r)
        levels = [M.Level(g.tile_m, g.n, g.n, g.origin, g.origin, g.tol_m) for g in self.grids]
        date = dt.datetime.fromtimestamp(build_unix, dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        return M.Header(self.proj, r, (lat_lo, lon_lo, lat_hi, lon_hi), build_unix, date, sources, levels)


def build(pbfs: list[str | Path], out: str | Path, lat: float, lon: float, radius_mi: float, *,
          rail: bool = True, fuel: bool = True, build_unix: int | None = None, zip_path: str | Path | None = None,
          progress=None) -> BuildReport:
    """Build ``out``/MAP from the extracts. ``out`` is the SD card's root (or
    any folder that is to receive a MAP folder)."""
    t0 = time.monotonic()
    progress = progress or (lambda msg: None)
    b = Builder(lat, lon, radius_mi * MILE, rail=rail, fuel=fuel, progress=progress)
    names = []
    for p in pbfs:
        p = Path(p)
        names.append(f"{_source_name(p)} {extract_date(p)}")
        progress(f"reading {p}")
        b.read(p)
    sources = "Geofabrik " + "; ".join(names) if names else "-"
    progress("encoding tiles")
    tiles = b.sink.encode()
    if build_unix is None:
        build_unix = int(time.time())
    header = b.header(build_unix, sources)
    folder = Path(out) / M.DIR_NAME
    idx, dat = M.write_map(folder, header, tiles)
    (folder / M.README_NAME).write_text(M.README_TEXT.format(
        date=header.build_date, sources=sources, lat=lat, lon=lon, radius_mi=radius_mi,
        attribution=M.ATTRIBUTION), encoding="utf-8", newline="\r\n")
    rep = b.report
    rep.folder, rep.sources = folder, sources
    rep.idx_bytes, rep.dat_bytes = idx.stat().st_size, dat.stat().st_size
    rep.tiles = [lv.n_tiles for lv in header.levels]
    rep.level_bytes = [lv.data_bytes for lv in header.levels]
    rep.level_points = list(b.sink.points)
    if zip_path:
        zip_path = Path(zip_path)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for f in (M.IDX_NAME, M.DAT_NAME, M.README_NAME):
                z.write(folder / f, f"{M.DIR_NAME}/{f}")
        rep.zip_path, rep.zip_bytes = zip_path, zip_path.stat().st_size
    rep.seconds = time.monotonic() - t0
    return rep
