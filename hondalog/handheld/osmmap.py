"""The handheld's offline road map: the ``MAP/`` folder on the SD card.

Two files, both little-endian (layout: firmware/handheld/MAPFORMAT.md):

    MAP/MAP.IDX   header (projection, bbox, levels, build and source dates,
                  attribution, CRC) and, per level, a dense grid of
                  (offset, length) pairs: tile lookup is one seek.
    MAP/MAP.DAT   a short header, then the tiles. Each tile carries its own
                  CRC-32 and holds int16 coordinates relative to its centre.

This module writes and reads the format; :mod:`osmbuild` makes the content
from OpenStreetMap extracts. The firmware's reader is
components/hhmap (C), tested against files this module writes.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

DIR_NAME = "MAP"
IDX_NAME = "MAP.IDX"
DAT_NAME = "MAP.DAT"
README_NAME = "README.TXT"

IDX_MAGIC = b"HHMAPIDX"
DAT_MAGIC = b"HHMAPDAT"
TILE_MAGIC = b"HT"
VERSION = 1
ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"
Q = 32768                      # coordinate units per tile side: unit = tile_m / Q, tile spans -16384..16384

# Header: fixed part, then the level table, then a CRC-32 of everything before it.
HDR = struct.Struct("<8sHHBBH9dqI20s96s48s")   # 264 bytes
LEVEL = struct.Struct("<IHHddIIfI")     # tile_m, cols, rows, origin_x, origin_y, index_off, n_tiles, tol_m, data_bytes
DAT_HDR = struct.Struct("<8sHHI")       # magic, version, header length, build id
TILE_HDR = struct.Struct("<2sBBHHII")   # magic, level, version, n_features, n_strings, body_len, crc of the
                                        # first 12 header bytes and the body
INDEX = struct.Struct("<II")            # offset into MAP.DAT, length (header + body); 0, 0 = no tile
MAX_LEVELS = 4

# geometry, the low two bits of a feature's flags
G_LINE, G_AREA, G_POINT = 0, 1, 2
F_NAMED, F_RANKED, F_REF = 4, 8, 16       # F_REF: the name is a route number ("I-55")

# Class codes. The order of LAYER is the drawing order.
C_MOTORWAY, C_TRUNK, C_PRIMARY, C_SECONDARY, C_TERTIARY, C_RESIDENTIAL, C_SERVICE = 1, 2, 3, 4, 5, 6, 7
C_LINK_MAJOR, C_LINK = 8, 9                 # motorway/trunk links; primary/secondary links
C_RAIL = 16
C_RIVER, C_CANAL = 20, 21
C_WATER = 24                                # lakes, reservoirs, river areas
C_CITY, C_TOWN, C_VILLAGE, C_HAMLET, C_SUBURB = 32, 33, 34, 35, 36
C_FUEL = 40

CLASS_NAMES = {
    C_MOTORWAY: "motorway", C_TRUNK: "trunk", C_PRIMARY: "primary", C_SECONDARY: "secondary",
    C_TERTIARY: "tertiary", C_RESIDENTIAL: "residential", C_SERVICE: "service", C_LINK_MAJOR: "link_major",
    C_LINK: "link", C_RAIL: "rail", C_RIVER: "river", C_CANAL: "canal", C_WATER: "water",
    C_CITY: "city", C_TOWN: "town", C_VILLAGE: "village", C_HAMLET: "hamlet", C_SUBURB: "suburb", C_FUEL: "fuel",
}
LAYER = [C_WATER, C_RIVER, C_CANAL, C_RAIL, C_SERVICE, C_RESIDENTIAL, C_TERTIARY, C_LINK, C_LINK_MAJOR,
         C_SECONDARY, C_PRIMARY, C_TRUNK, C_MOTORWAY, C_SUBURB, C_HAMLET, C_VILLAGE, C_TOWN, C_CITY, C_FUEL]
LAYER_OF = {c: i for i, c in enumerate(LAYER)}


def geom_of(cls: int) -> int:
    return G_POINT if cls >= 32 else G_AREA if cls >= 24 else G_LINE


def crc32(data: bytes, crc: int = 0) -> int:
    return zlib.crc32(data, crc) & 0xFFFFFFFF


def _fit(text: str, n: int) -> bytes:
    """UTF-8, cut on a character boundary to leave room for a NUL."""
    raw = text.encode("utf-8")
    while len(raw) > n - 1:
        text = text[:-1]
        raw = text.encode("utf-8")
    return raw.ljust(n, b"\0")


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


# ---- projection -----------------------------------------------------------------

def metres_per_degree(lat: float) -> tuple[float, float]:
    """(per degree of longitude, per degree of latitude) on WGS84 at ``lat``."""
    p = math.radians(lat)
    ky = 111132.954 - 559.822 * math.cos(2 * p) + 1.175 * math.cos(4 * p)
    kx = 111412.84 * math.cos(p) - 93.5 * math.cos(3 * p) + 0.118 * math.cos(5 * p)
    return kx, ky


@dataclass
class Projection:
    """Equirectangular about the centre: x east, y north, metres. Over a
    100-mile radius the east-west scale is off by under 2 % at the north and
    south edges; positions are exact, since the handheld projects GPS fixes
    with the very same constants."""
    lat0: float
    lon0: float
    kx: float = 0.0
    ky: float = 0.0

    def __post_init__(self) -> None:
        if not self.kx:
            self.kx, self.ky = metres_per_degree(self.lat0)

    def fwd(self, lat: float, lon: float) -> tuple[float, float]:
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def inv(self, x: float, y: float) -> tuple[float, float]:
        return self.lat0 + y / self.ky, self.lon0 + x / self.kx


# ---- tiles ------------------------------------------------------------------------

@dataclass
class Feature:
    cls: int
    rings: list[list[tuple[int, int]]]       # line: one ring; area: outer and holes; point: one point
    name: str = ""
    rank: int = -1                            # places: 16 * log10(population + 1); -1 = none
    ref: bool = False                         # the name is a route number: drawn as a shield

    @property
    def geom(self) -> int:
        return geom_of(self.cls)


@dataclass
class Tile:
    level: int
    features: list[Feature] = field(default_factory=list)


def pack_tile(level: int, items) -> bytes:
    """Header, string table, features. ``items``: (cls, name, rank, rings, ref)
    in drawing order; a ring is a numpy int16 (n, 2) array or a list of (x, y)."""
    items = list(items)
    strings: dict[str, int] = {}
    for _, name, _, _, _ in items:
        if name and name not in strings:
            strings[name] = len(strings)
    if len(items) > 0xFFFF or len(strings) > 0xFFFF:
        raise ValueError("too many features in a tile")
    body = bytearray()
    for s in strings:
        raw = s.encode("utf-8")[:255]
        body += bytes([len(raw)]) + raw
    for cls, name, rank, rings, ref in items:
        geom = geom_of(cls)
        npts = sum(len(r) for r in rings)
        if not npts or npts > 0xFFFF:
            raise ValueError(f"feature with {npts} points")
        flags = geom | (F_NAMED if name else 0) | (F_RANKED if rank >= 0 else 0) | (F_REF if name and ref else 0)
        body += struct.pack("<BBH", cls, flags, npts)
        if name:
            body += struct.pack("<H", strings[name])
        if rank >= 0:
            body += bytes([min(255, rank)])
        if geom == G_AREA:
            body += struct.pack(f"<H{len(rings)}H", len(rings), *(len(r) for r in rings))
        for r in rings:
            if hasattr(r, "tobytes"):
                body += r.astype("<i2").tobytes()
            else:
                try:
                    body += struct.pack(f"<{2 * len(r)}h", *(c for p in r for c in p))
                except struct.error as exc:
                    raise ValueError(f"coordinate outside int16: {exc}") from exc
    head = TILE_HDR.pack(TILE_MAGIC, level, VERSION, len(items), len(strings), len(body), 0)[:12]
    return head + struct.pack("<I", crc32(bytes(body), crc32(head))) + body


def encode_tile(tile: Tile) -> bytes:
    """A Tile's bytes, features sorted into drawing order. Raises ValueError
    on coordinates outside int16 or counts that do not fit."""
    feats = sorted(tile.features, key=lambda f: LAYER_OF.get(f.cls, 255))
    return pack_tile(tile.level, ((f.cls, f.name, f.rank, [r for r in f.rings if r], f.ref) for f in feats))


def decode_tile(data: bytes) -> Tile:
    """Inverse of :func:`encode_tile`. Raises ValueError on a bad tile."""
    if len(data) < TILE_HDR.size:
        raise ValueError("tile is cut short")
    magic, level, version, n_feat, n_str, body_len, crc = TILE_HDR.unpack_from(data)
    if magic != TILE_MAGIC or version != VERSION:
        raise ValueError("not a map tile")
    body = data[TILE_HDR.size:TILE_HDR.size + body_len]
    if len(body) != body_len:
        raise ValueError("tile is cut short")
    if crc32(body, crc32(data[:12])) != crc:
        raise ValueError("tile checksum is wrong")
    pos = 0
    strings = []
    for _ in range(n_str):
        n = body[pos]
        strings.append(body[pos + 1:pos + 1 + n].decode("utf-8"))
        pos += 1 + n
    tile = Tile(level)
    for _ in range(n_feat):
        cls, flags, npts = struct.unpack_from("<BBH", body, pos)
        pos += 4
        name, rank = "", -1
        if flags & F_NAMED:
            name = strings[struct.unpack_from("<H", body, pos)[0]]
            pos += 2
        if flags & F_RANKED:
            rank = body[pos]
            pos += 1
        lens = [npts]
        if flags & 3 == G_AREA:
            (nr,) = struct.unpack_from("<H", body, pos)
            lens = list(struct.unpack_from(f"<{nr}H", body, pos + 2))
            pos += 2 + 2 * nr
        rings = []
        for n in lens:
            v = struct.unpack_from(f"<{2 * n}h", body, pos)
            pos += 4 * n
            rings.append(list(zip(v[0::2], v[1::2])))
        tile.features.append(Feature(cls, rings, name, rank, bool(flags & F_REF)))
    if pos != body_len:
        raise ValueError("tile body has trailing bytes")
    return tile


# ---- header and index ----------------------------------------------------------------

@dataclass
class Level:
    tile_m: int
    cols: int
    rows: int
    origin_x: float
    origin_y: float
    tol_m: float
    index_off: int = 0
    n_tiles: int = 0
    data_bytes: int = 0

    def tile_centre(self, col: int, row: int) -> tuple[float, float]:
        return self.origin_x + (col + 0.5) * self.tile_m, self.origin_y + (row + 0.5) * self.tile_m


@dataclass
class Header:
    proj: Projection
    radius_m: float
    bbox: tuple[float, float, float, float]          # min_lat, min_lon, max_lat, max_lon
    build_unix: int
    build_date: str
    sources: str
    levels: list[Level]
    attribution: str = ATTRIBUTION
    build_id: int = 0
    version: int = VERSION

    def size(self) -> int:
        return HDR.size + LEVEL.size * MAX_LEVELS + 4


def pack_header(h: Header) -> bytes:
    levels = list(h.levels) + [Level(0, 0, 0, 0.0, 0.0, 0.0)] * (MAX_LEVELS - len(h.levels))
    raw = HDR.pack(IDX_MAGIC, h.version, h.size(), len(h.levels), 0, 0, h.proj.lat0, h.proj.lon0, h.proj.kx,
                   h.proj.ky, h.radius_m, *h.bbox, h.build_unix, h.build_id, _fit(h.build_date, 20),
                   _fit(h.sources, 96), _fit(h.attribution, 48))
    raw += b"".join(LEVEL.pack(lv.tile_m, lv.cols, lv.rows, lv.origin_x, lv.origin_y, lv.index_off, lv.n_tiles,
                               lv.tol_m, lv.data_bytes) for lv in levels)
    return raw + struct.pack("<I", crc32(raw))


def unpack_header(data: bytes) -> Header:
    if len(data) < HDR.size or data[:8] != IDX_MAGIC:
        raise ValueError("not a map index (MAP.IDX)")
    v = HDR.unpack_from(data)
    version, hdr_len, n_levels = v[1], v[2], v[3]
    if version != VERSION:
        raise ValueError(f"map format version {version}; this hondalog reads {VERSION}")
    if hdr_len != HDR.size + LEVEL.size * MAX_LEVELS + 4 or len(data) < hdr_len or not 1 <= n_levels <= MAX_LEVELS:
        raise ValueError("map index header is damaged")
    if crc32(data[:hdr_len - 4]) != struct.unpack_from("<I", data, hdr_len - 4)[0]:
        raise ValueError("map index header checksum is wrong")
    levels = []
    for i in range(n_levels):
        tile_m, cols, rows, ox, oy, off, n, tol, nbytes = LEVEL.unpack_from(data, HDR.size + i * LEVEL.size)
        levels.append(Level(tile_m, cols, rows, ox, oy, tol, off, n, nbytes))
    return Header(Projection(v[6], v[7], v[8], v[9]), v[10], (v[11], v[12], v[13], v[14]), v[15],
                  _cstr(v[17]), _cstr(v[18]), levels, _cstr(v[19]), v[16], version)


def write_map(folder: str | Path, header: Header, tiles: dict[tuple[int, int, int], bytes]) -> tuple[Path, Path]:
    """Write MAP.IDX and MAP.DAT into ``folder`` (the MAP directory).
    ``tiles``: (level, col, row) -> encoded tile. Fills in the levels' index
    offsets, tile counts and the build id. Temporary names, then renamed."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    header.build_id = crc32(f"{header.build_unix}|{header.sources}|{len(tiles)}".encode()) or 1
    off = header.size()
    for lv in header.levels:
        lv.index_off = off
        off += lv.cols * lv.rows * INDEX.size
        lv.n_tiles = lv.data_bytes = 0
    dat = folder / "MAP.DTM"
    idx_entries = [bytearray(lv.cols * lv.rows * INDEX.size) for lv in header.levels]
    with open(dat, "wb") as f:
        f.write(DAT_HDR.pack(DAT_MAGIC, VERSION, DAT_HDR.size, header.build_id))
        pos = DAT_HDR.size
        for (level, col, row) in sorted(tiles, key=lambda k: (k[0], k[2], k[1])):
            data = tiles[(level, col, row)]
            lv = header.levels[level]
            INDEX.pack_into(idx_entries[level], (row * lv.cols + col) * INDEX.size, pos, len(data))
            f.write(data)
            pos += len(data)
            lv.n_tiles += 1
            lv.data_bytes += len(data)
    idx = folder / "MAP.IXT"
    with open(idx, "wb") as f:
        f.write(pack_header(header))
        for e in idx_entries:
            f.write(e)
    dat.replace(folder / DAT_NAME)
    idx.replace(folder / IDX_NAME)
    return folder / IDX_NAME, folder / DAT_NAME


class MapReader:
    """Read a MAP folder: the header, and tiles by (level, col, row)."""

    def __init__(self, folder: str | Path):
        folder = Path(folder)
        self.idx = (folder / IDX_NAME).read_bytes()
        self.dat = (folder / DAT_NAME).read_bytes()
        self.header = unpack_header(self.idx)
        if len(self.dat) < DAT_HDR.size:
            raise ValueError("MAP.DAT is cut short")
        magic, version, _, build_id = DAT_HDR.unpack_from(self.dat)
        if magic != DAT_MAGIC or version != VERSION:
            raise ValueError("not a map data file (MAP.DAT)")
        if build_id != self.header.build_id:
            raise ValueError("MAP.IDX and MAP.DAT are from different builds")

    def entry(self, level: int, col: int, row: int) -> tuple[int, int]:
        lv = self.header.levels[level]
        if not (0 <= col < lv.cols and 0 <= row < lv.rows):
            return 0, 0
        return INDEX.unpack_from(self.idx, lv.index_off + (row * lv.cols + col) * INDEX.size)

    def raw(self, level: int, col: int, row: int) -> bytes | None:
        off, n = self.entry(level, col, row)
        return self.dat[off:off + n] if n else None

    def tile(self, level: int, col: int, row: int) -> Tile | None:
        raw = self.raw(level, col, row)
        return decode_tile(raw) if raw is not None else None

    def tiles(self, level: int):
        """(col, row, Tile) for every tile of a level."""
        lv = self.header.levels[level]
        for row in range(lv.rows):
            for col in range(lv.cols):
                t = self.tile(level, col, row)
                if t is not None:
                    yield col, row, t

    def to_metres(self, level: int, col: int, row: int, q: tuple[int, int]) -> tuple[float, float]:
        lv = self.header.levels[level]
        cx, cy = lv.tile_centre(col, row)
        return cx + q[0] * lv.tile_m / Q, cy + q[1] * lv.tile_m / Q


README_TEXT = """\
hondalog handheld road map
==========================

Built {date} by `hondalog-handheld map-build` from:
    {sources}
Centre {lat:.4f}, {lon:.4f}; radius {radius_mi:g} miles.

Map data {attribution}. This folder is a Produced Work of the
OpenStreetMap database, available under the Open Database License:
https://www.openstreetmap.org/copyright

Copy this MAP folder to the root of the handheld's SD card.
Format: firmware/handheld/MAPFORMAT.md in the hondalog sources.
"""
