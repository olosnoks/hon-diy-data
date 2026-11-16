# MAP/: the handheld's offline road map

The Map screen draws roads, water, railways, towns and fuel stations from a
`MAP` folder in the root of the SD card. The laptop makes it from
OpenStreetMap extracts:

    hondalog-handheld map-build --pbf mississippi-latest.osm.pbf --pbf louisiana-latest.osm.pbf \
        --center 32.2988,-90.1848 --radius-mi 100 --out /media/SDCARD

Writer and reader for the laptop: `hondalog/handheld/osmmap.py`. Builder:
`hondalog/handheld/osmbuild.py`. Reader and renderer on the handheld:
`components/hhmap` (`hhmap.h`). Version 1.

Map data © OpenStreetMap contributors, under the Open Database License. A
`MAP` folder is a Produced Work of the OpenStreetMap database: the
attribution is in its header, in `MAP/README.TXT`, and on the Map screen.

## Files

| File | What |
|---|---|
| `MAP/MAP.IDX` | Header (428 bytes), then per level a dense grid of tile index entries |
| `MAP/MAP.DAT` | A 16-byte header, then the tiles, one after another |
| `MAP/README.TXT` | Sources, date, attribution, licence (for people; the handheld does not read it) |

FAT 8.3 names, as the handheld mounts the card without long file names.
Everything is little-endian and packed. The builder writes both files under
temporary names and renames them at the end; a header's build id ties an
`IDX` to its `DAT`, so a half-copied pair is refused rather than misread.

## Projection and grid

Equirectangular about the centre, in metres:

    x = (lon - lon0) * kx        east
    y = (lat - lat0) * ky        north

`kx` and `ky` are the WGS84 metres per degree of longitude and latitude at
`lat0`, stored in the header so that the handheld projects its GPS fixes
with exactly the numbers the map was built with: a position lands on the
road it is on. Over a 100-mile radius the east-west scale is off by under
2 % at the northern and southern edges; positions are not.

Each level is a square grid of square tiles covering the circle's bounding
square: `origin_x = origin_y = -radius`, `cols = rows = ceil(2 * radius /
tile_m)`, tile `(col, row)` spans `x` from `origin_x + col * tile_m`, rows
counting northward. Tiles that do not touch the circle are left out.

## Levels

| Level | Tile | Simplified to | Drawn at | Zoom step | On it |
|---|---|---|---|---|---|
| 0 | 1.024 km | 2 m | 4 m/px (1.9 x 0.9 km on screen) | 1/4, under 30 km/h | everything: service roads, street names, hamlets, fuel |
| 1 | 4.096 km | 5 m | 10 m/px (4.8 x 2.2 km) | 2/4, 30 to 60 km/h | residential and up, rail, canals, fuel |
| 2 | 8.192 km | 12 m | 25 m/px (12 x 5.4 km) | 3/4, 60 to 90 km/h | tertiary and up, motorway links, villages |
| 3 | 32.768 km | 30 m | 60 m/px (29 x 13 km) | 4/4, over 90 km/h | secondary and up, rivers, towns and cities |

Douglas-Peucker simplification per level, to about half a pixel at the scale
it is drawn at. Lakes smaller than 1,000 / 10,000 / 60,000 / 500,000 m² are
left off levels 0 / 1 / 2 / 3.

## MAP.IDX

Header:

| Offset | Type | Field |
|---|---|---|
| 0 | char[8] | `HHMAPIDX` |
| 8 | u16 | version (1) |
| 10 | u16 | header length (428) |
| 12 | u8 | number of levels (1 to 4) |
| 13 | u8 | projection (0 = equirectangular, above) |
| 14 | u16 | 0 |
| 16 | f64 x 4 | lat0, lon0, kx, ky |
| 48 | f64 | radius, m |
| 56 | f64 x 4 | bounding box: min lat, min lon, max lat, max lon |
| 88 | i64 | build time, Unix seconds |
| 96 | u32 | build id (also in MAP.DAT) |
| 100 | char[20] | build date, `2026-11-16T09:30Z` |
| 120 | char[96] | sources and their dates: `Geofabrik mississippi 2026-11-15; louisiana 2026-11-15` |
| 216 | char[48] | attribution, UTF-8: `© OpenStreetMap contributors (ODbL)` |
| 264 | level x 4 | see below (unused ones are zero) |
| 424 | u32 | CRC-32 (zlib) of bytes 0 to 423 |

Level (40 bytes): u32 tile side m, u16 cols, u16 rows, f64 origin x, f64
origin y, u32 offset of its index in MAP.IDX, u32 tiles stored, f32
simplification tolerance m, u32 bytes of tiles.

Index: `cols * rows` entries of `{u32 offset in MAP.DAT, u32 length}`, row
by row from the south. `0, 0` = no tile. Finding a tile is one seek:
`index_off + (row * cols + col) * 8`.

## MAP.DAT

`char[8] "HHMAPDAT"`, u16 version, u16 header length (16), u32 build id;
then the tiles.

## Tiles

Header (16 bytes):

| Offset | Type | Field |
|---|---|---|
| 0 | char[2] | `HT` |
| 2 | u8 | level |
| 3 | u8 | version (1) |
| 4 | u16 | features |
| 6 | u16 | strings |
| 8 | u32 | body length |
| 12 | u32 | CRC-32 of header bytes 0 to 11, then the body |

Body: the string table, `strings` times `{u8 length, UTF-8 bytes}` (no
terminator; one copy of a name however many features use it), then the
features:

| Type | Field |
|---|---|
| u8 | class (below) |
| u8 | flags: bits 0-1 geometry (0 line, 1 area, 2 point); bit 2 named; bit 3 ranked; bit 4 the name is a route number |
| u16 | points |
| u16 | if named: string index |
| u8 | if ranked: rank, `round(16 * log10(population + 1))` |
| u16, u16 x n | if an area: rings, then the points in each (the first is the outline, the rest holes; filled even-odd) |
| (i16, i16) x points | coordinates |

Coordinates are relative to the tile's centre in units of `tile_m / 32768`
(3 cm on level 0, 1 m on level 3): a tile spans -16384 to +16384 on both
axes. Lines and areas are cut exactly at the tile's edge, so neighbouring
tiles meet without overlap; ring closing points are not repeated.

Features are stored in drawing order. The handheld still draws layer by
layer across all the tiles in view, so a minor road in one tile never paints
over a major one in the next.

The handheld checks a tile's CRC and walks all its features' bounds once,
when it is read from the card; a damaged tile is left blank and counted, the
rest of the map is drawn.

## Classes

| Code | Class | From OpenStreetMap | Levels | Width px (level 0..3) |
|---|---|---|---|---|
| 1 | motorway | highway=motorway | 0-3 | 7 6 5 4, with a light centre line at 6 and up |
| 2 | trunk | highway=trunk | 0-3 | 6 5 4 3 |
| 3 | primary | highway=primary | 0-3 | 5 4 4 3 |
| 4 | secondary | highway=secondary | 0-3 | 4 3 3 2 |
| 5 | tertiary | highway=tertiary, tertiary_link | 0-2 | 3 3 2 - |
| 6 | residential | highway=residential, unclassified, living_street, road | 0-1 | 2 2 - - |
| 7 | service | highway=service (not access=private/no) | 0 | 1 - - - |
| 8 | link_major | highway=motorway_link, trunk_link | 0-2 | 3 3 2 - |
| 9 | link | highway=primary_link, secondary_link | 0-1 | 3 2 - - |
| 16 | rail | railway=rail without a service=* tag | 0-2 | 2 2 2 -, dashed |
| 20 | river | waterway=river | 0-3 | 3 2 2 2, water colour |
| 21 | canal | waterway=canal | 0-1 | 2 2 - - |
| 24 | water | natural=water, landuse=reservoir, waterway=riverbank (areas, multipolygons too) | by size | filled |
| 32 | city | place=city | 0-3 | dot 8 px, label 20 px |
| 33 | town | place=town | 0-3 | dot 6 px |
| 34 | village | place=village | 0-2 | dot 4 px |
| 35 | hamlet | place=hamlet | 0-1 | dot 4 px |
| 36 | suburb | place=suburb, neighbourhood, quarter | 0-1 | label only |
| 40 | fuel | amenity=fuel (nodes, and the middle of closed ways) | 0-1 | a pump |

Names: a road carries its route number (`ref`, written as signed:
`I 55;I 20` -> `I-55`, `US 49`) on the levels in the table below, else its
name shortened (`North State Street` -> `N State St`) on the closest levels:

| Class | Route number on levels | Name on levels |
|---|---|---|
| motorway, trunk, primary | 0-3 | 0-1 |
| secondary | 0-2 | 0-1 |
| tertiary | 0 | 0 |

Residential streets, rivers and lakes carry no names: the screen is 480
pixels wide and read at arm's length.

## Size

The 100-mile radius around Jackson from the Mississippi and Louisiana
extracts (November 2026) is 17 MB (MAP.DAT 16.4 MB, MAP.IDX 0.9 MB); 69,000
tiles; about 2 minutes and 1 GB of RAM to build on a laptop. Level 0 is 60 %
of it, most of that service roads and residential streets.

The radius takes in a corner of south-east Arkansas (west of the Mississippi
River, north of 33° N) that neither extract covers; add the Arkansas extract
with another `--pbf` to fill it.
