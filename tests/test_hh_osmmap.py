"""The handheld's offline road map: the format, the builder, the CLI, and the
fixture the firmware's C reader is tested against (test/host/hmtest.c).

Regenerate that fixture after a deliberate format or builder change:

    HH_UPDATE_FIXTURES=1 python3 -m pytest tests/test_hh_osmmap.py
"""

import math
import os
import shutil
import zipfile
from pathlib import Path

import numpy as np
import pytest

from hondalog.handheld import cli
from hondalog.handheld import osmmap as M

osmium = pytest.importorskip("osmium")
from hondalog.handheld import osmbuild as B  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TINY = REPO / "tests" / "fixtures" / "osmmap" / "tiny.osm"
C_FIXTURE = REPO / "firmware" / "handheld" / "test" / "host" / "fixtures" / "tinymap"
CENTER = (32.30, -90.18)
BUILD_UNIX = 1700000000


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    out = tmp_path_factory.mktemp("tiny")
    report = B.build([TINY], out, *CENTER, 2.0, build_unix=BUILD_UNIX)
    return report, M.MapReader(out / "MAP")


def all_features(reader, level):
    return [(c, r, f) for c, r, t in reader.tiles(level) for f in t.features]


def lat_lon(reader, level, col, row, q):
    x, y = reader.to_metres(level, col, row, q)
    return reader.header.proj.inv(x, y)


# ---- the format -----------------------------------------------------------------------

def test_tile_round_trip_and_checksum():
    tile = M.Tile(2, [
        M.Feature(M.C_MOTORWAY, [[(-16384, 0), (0, 5), (16384, -7)]], "I-55", ref=True),
        M.Feature(M.C_WATER, [[(0, 0), (100, 0), (100, 100), (0, 100)], [(10, 10), (20, 10), (20, 20)]]),
        M.Feature(M.C_CITY, [[(5, -5)]], "Jackson", 83),
        M.Feature(M.C_RESIDENTIAL, [[(1, 1), (2, 2)]], "Pond Rd"),
        M.Feature(M.C_PRIMARY, [[(3, 3), (4, 4)]], "I-55"),        # one string, two features
    ])
    raw = M.encode_tile(tile)
    back = M.decode_tile(raw)
    assert back.level == 2
    # drawing order: water first, roads by class, places last
    assert [f.cls for f in back.features] == [M.C_WATER, M.C_RESIDENTIAL, M.C_PRIMARY, M.C_MOTORWAY, M.C_CITY]
    by_cls = {f.cls: f for f in back.features}
    assert by_cls[M.C_MOTORWAY].rings == [[(-16384, 0), (0, 5), (16384, -7)]] and by_cls[M.C_MOTORWAY].ref
    assert by_cls[M.C_WATER].rings[1] == [(10, 10), (20, 10), (20, 20)]
    assert (by_cls[M.C_CITY].name, by_cls[M.C_CITY].rank) == ("Jackson", 83)
    assert not by_cls[M.C_PRIMARY].ref and by_cls[M.C_PRIMARY].name == "I-55"
    assert raw.count(b"I-55") == 1                 # the tile's string table holds it once
    bad = bytearray(raw)
    bad[-1] ^= 1
    with pytest.raises(ValueError, match="checksum"):
        M.decode_tile(bytes(bad))
    with pytest.raises(ValueError):
        M.encode_tile(M.Tile(0, [M.Feature(M.C_RAIL, [[(0, 40000), (0, 0)]])]))     # outside int16


def test_header_round_trip_and_damage():
    proj = M.Projection(32.2988, -90.1848)
    h = M.Header(proj, 160934.4, (30.8, -91.9, 33.7, -88.4), BUILD_UNIX, "2023-11-14T22:13Z",
                 "Geofabrik mississippi 2026-11-15", [M.Level(1024, 315, 315, -160934.4, -160934.4, 2.0)])
    raw = M.pack_header(h)
    assert len(raw) == h.size() == 428
    back = M.unpack_header(raw)
    assert (back.proj.lat0, back.proj.kx, back.radius_m, back.build_date) == (32.2988, proj.kx, 160934.4, h.build_date)
    assert back.attribution == "© OpenStreetMap contributors (ODbL)" and back.levels[0].cols == 315
    bad = bytearray(raw)
    bad[60] ^= 1
    with pytest.raises(ValueError, match="checksum"):
        M.unpack_header(bytes(bad))


def test_projection_matches_wgs84_scale():
    # against the WGS84 ellipsoid's radii of curvature
    a, e2, phi = 6378137.0, 0.00669437999014, math.radians(32.3)
    w = 1 - e2 * math.sin(phi) ** 2
    kx, ky = M.metres_per_degree(32.3)
    assert kx == pytest.approx(math.pi / 180 * a * math.cos(phi) / math.sqrt(w), abs=1)
    assert ky == pytest.approx(math.pi / 180 * a * (1 - e2) / w ** 1.5, abs=1)
    p = M.Projection(32.3, -90.18)
    assert p.inv(*p.fwd(32.4, -90.0)) == pytest.approx((32.4, -90.0))


# ---- geometry ----------------------------------------------------------------------------

def test_simplify_keeps_corners_and_drops_wiggles():
    line = np.array([[0, 0], [10, 0.5], [20, -0.4], [30, 0], [30, 30]], float)
    assert B.simplify(line, 1.0).tolist() == [[0, 0], [30, 0], [30, 30]]
    assert len(B.simplify(line, 0.1)) == 5


def test_clip_line_splits_where_it_leaves_and_comes_back():
    zig = np.array([[-5, 5], [5, 5], [5, 15], [8, 15], [8, 5], [15, 5]], float)
    pieces = B.clip_line(zig, 0, 0, 10, 10)
    assert [p.tolist() for p in pieces] == [[[0, 5], [5, 5], [5, 10]], [[8, 10], [8, 5], [10, 5]]]
    assert B.clip_line(np.array([[20, 20], [30, 30]], float), 0, 0, 10, 10) == []


def test_clip_ring_is_the_intersection():
    square = np.array([[-5, -5], [5, -5], [5, 5], [-5, 5]], float)
    clipped = B.clip_ring(square, 0, 0, 10, 10)
    assert abs(B.ring_area(clipped)) == pytest.approx(25)
    assert len(B.clip_ring(square, 20, 20, 30, 30)) == 0


def test_names():
    assert B.road_ref("I 55;I 20") == "I-55" and B.road_ref("US 49") == "US 49" and B.road_ref("I-220") == "I-220"
    assert B.short_name("North State Street") == "N State St" and B.short_name("Lakeland Drive") == "Lakeland Dr"
    assert B.place_rank(M.C_CITY, "153,701") == round(16 * math.log10(153702))
    assert B.place_rank(M.C_VILLAGE, None) == round(16 * math.log10(501))


# ---- the builder on a hand-made extract ----------------------------------------------------

def test_levels_carry_what_their_zoom_needs(tiny):
    _, mr = tiny
    classes = [{f.cls for _, _, f in all_features(mr, lv)} for lv in range(4)]
    assert {M.C_SERVICE, M.C_RESIDENTIAL, M.C_TERTIARY, M.C_RAIL, M.C_FUEL, M.C_HAMLET} <= classes[0]
    assert M.C_SERVICE not in classes[1] and M.C_RESIDENTIAL in classes[1]
    assert M.C_RESIDENTIAL not in classes[2] and M.C_TERTIARY in classes[2] and M.C_FUEL not in classes[2]
    assert classes[3] == {M.C_MOTORWAY, M.C_PRIMARY, M.C_RIVER, M.C_CITY}       # the lakes are too small for it
    assert M.C_WATER in classes[2]


def test_labels(tiny):
    _, mr = tiny
    named = {(lv, f.name, f.ref) for lv in range(4) for _, _, f in all_features(mr, lv) if f.name}
    assert (0, "I-55", True) in named and (3, "I-55", True) in named          # first ref of "I 55;I 20"
    assert (3, "US 49", True) in named
    assert (0, "W Elm Ave", False) in named and not any(n == "W Elm Ave" for lv, n, _ in named if lv > 0)
    assert not any(n in ("Test Street", "Test River") for _, n, _ in named)  # no minor road or river names
    places = {f.name: f.rank for lv in range(4) for _, _, f in all_features(mr, lv) if f.cls >= 32 and f.name}
    assert places == {"Testville": 65, "Smallton": 43, "Tiny Corner": 27}


def test_geometry_lands_where_it_was(tiny):
    _, mr = tiny
    # every motorway vertex is within a few metres of the drawn I-55 (lon -90.1745 .. -90.1752)
    for lv in range(4):
        lons = [lat_lon(mr, lv, c, r, q)[1] for c, r, f in all_features(mr, lv) if f.cls == M.C_MOTORWAY
                for q in f.rings[0]]
        assert lons and all(-90.1753 - 0.0003 * lv <= x <= -90.1744 + 0.0003 * lv for x in lons), lv
    city = [lat_lon(mr, 0, c, r, f.rings[0][0]) for c, r, f in all_features(mr, 0) if f.name == "Testville"]
    assert city[0] == pytest.approx(CENTER, abs=1e-6)


def test_lake_with_island_and_clipped_water_fill(tiny):
    _, mr = tiny
    # level 2 is one tile: the reservoir arrives whole, with its island as a second ring
    water = [f for _, _, f in all_features(mr, 2) if f.cls == M.C_WATER]
    assert sorted(len(f.rings) for f in water) == [1, 2]
    # on level 0 the pieces of the reservoir add up to its area, island excluded
    total = 0.0
    for c, r, f in all_features(mr, 0):
        if f.cls == M.C_WATER:
            for k, ring in enumerate(f.rings):
                pts = np.array([mr.to_metres(0, c, r, q) for q in ring])
                total += abs(B.ring_area(pts))
    kx, ky = M.metres_per_degree(CENTER[0])
    lake = (2 * 0.00135 * ky) * (2 * 0.00212 * kx)
    reservoir = (2 * 0.0027 * ky) * (2 * 0.0032 * kx)
    island = (2 * 0.00045 * ky) * (2 * 0.00053 * kx)
    # outer and island are filled even-odd, so area of the rings = outer + island
    assert total == pytest.approx(lake + reservoir + island, rel=0.02)


def test_what_is_left_out(tiny):
    report, mr = tiny
    names = {f.name for lv in range(4) for _, _, f in all_features(mr, lv)}
    assert "Faraway" not in names and "MS 99" not in names                  # outside the radius
    assert report.features[M.C_WATER] == 2                                   # the tiny pond is not
    lv0 = mr.header.levels[0]
    assert lv0.cols == lv0.rows == 7 and lv0.n_tiles < 49
    for col, row in ((0, 0), (6, 0), (0, 6), (6, 6)):                       # corners are outside the circle
        assert mr.entry(0, col, row) == (0, 0)


def test_header_and_readme(tiny):
    report, mr = tiny
    h = mr.header
    assert (h.proj.lat0, h.proj.lon0) == CENTER and h.radius_m == pytest.approx(2 * B.MILE)
    assert h.attribution == M.ATTRIBUTION == "© OpenStreetMap contributors (ODbL)"
    assert h.sources == "Geofabrik tiny undated" and h.build_date == "2023-11-14T22:13Z"
    assert [lv.tile_m for lv in h.levels] == [1024, 4096, 8192, 32768]
    assert h.bbox[0] < CENTER[0] < h.bbox[2] and h.bbox[1] < CENTER[1] < h.bbox[3]
    readme = (report.folder / M.README_NAME).read_text(encoding="utf-8")
    assert "OpenStreetMap contributors" in readme and "Open Database License" in readme
    assert report.total_bytes == sum(p.stat().st_size for p in report.folder.iterdir() if p.suffix != ".TXT")


def test_two_extracts_with_the_same_objects_give_the_same_map(tmp_path, tiny):
    report, mr = tiny
    twice = B.build([TINY, TINY], tmp_path, *CENTER, 2.0, build_unix=BUILD_UNIX)
    assert twice.duplicates >= 15
    again = M.MapReader(tmp_path / "MAP")
    for lv in range(4):
        assert [(c, r, M.encode_tile(t)) for c, r, t in again.tiles(lv)] == \
               [(c, r, M.encode_tile(t)) for c, r, t in mr.tiles(lv)]


def test_reader_refuses_a_mismatched_pair(tmp_path, tiny):
    report, _ = tiny
    a = tmp_path / "a"
    shutil.copytree(report.folder, a)
    B.build([TINY], tmp_path / "b", *CENTER, 2.5, build_unix=BUILD_UNIX + 1)
    shutil.copy(tmp_path / "b" / "MAP" / "MAP.DAT", a / "MAP.DAT")
    with pytest.raises(ValueError, match="different builds"):
        M.MapReader(a)


def test_cli_map_build_and_info(tmp_path, capsys):
    card = tmp_path / "card"
    rc = cli.main(["map-build", "--pbf", str(TINY), "--center", "32.30,-90.18", "--radius-mi", "2",
                   "--out", str(card), "--zip", str(tmp_path / "map.zip"), "--quiet"])
    out = capsys.readouterr().out
    assert rc == 0 and "level 0:" in out and "OpenStreetMap contributors" in out
    assert (card / "MAP" / "MAP.IDX").exists() and (card / "MAP" / "MAP.DAT").exists()
    with zipfile.ZipFile(tmp_path / "map.zip") as z:
        assert sorted(z.namelist()) == ["MAP/MAP.DAT", "MAP/MAP.IDX", "MAP/README.TXT"]
    assert cli.main(["info", str(card)]) == 0
    assert "all tiles check out" in capsys.readouterr().out
    dat = card / "MAP" / "MAP.DAT"
    raw = bytearray(dat.read_bytes())
    raw[200] ^= 0xFF
    dat.write_bytes(bytes(raw))
    assert cli.main(["info", str(card / "MAP")]) == 1
    assert cli.main(["map-build", "--pbf", str(TINY), "--center", "x", "--out", str(card)]) == 2


# ---- the C reader's fixture ----------------------------------------------------------------

def expectations(reader: M.MapReader) -> str:
    """What hmtest.c checks the C reader against, one fact per line."""
    h = reader.header
    out = [f"header {h.proj.lat0!r} {h.proj.lon0!r} {h.proj.kx!r} {h.proj.ky!r} {h.radius_m!r} {h.build_id} "
           f"{len(h.levels)} {h.build_date} {h.attribution.encode().hex()}"]
    for i, lv in enumerate(h.levels):
        out.append(f"level {i} {lv.tile_m} {lv.cols} {lv.rows} {lv.n_tiles}")
    for i in range(len(h.levels)):
        for c, r, t in reader.tiles(i):
            out.append(f"tile {i} {c} {r} {len(t.features)}")
            for f in t.features:
                x, y = f.rings[0][0]
                xl, yl = f.rings[-1][-1]
                out.append(f"f {f.cls} {sum(len(g) for g in f.rings)} {len(f.rings)} {f.rank} {int(f.ref)} "
                           f"{f.name.replace(' ', '_') or '-'} {x} {y} {xl} {yl}")
    return "\n".join(out) + "\n"


def test_c_fixture_is_current(tmp_path):
    """The bytes under test/host/fixtures/tinymap are what this builder makes
    from tiny.osm (the build is deterministic for a given build time)."""
    B.build([TINY], tmp_path, *CENTER, 2.0, build_unix=BUILD_UNIX)
    fresh = M.MapReader(tmp_path / "MAP")
    expect = expectations(fresh)
    if os.environ.get("HH_UPDATE_FIXTURES"):
        (C_FIXTURE / "MAP").mkdir(parents=True, exist_ok=True)
        for name in (M.IDX_NAME, M.DAT_NAME):
            shutil.copy(tmp_path / "MAP" / name, C_FIXTURE / "MAP" / name)
        (C_FIXTURE / "expect.txt").write_text(expect)
    for name in (M.IDX_NAME, M.DAT_NAME):
        assert (C_FIXTURE / "MAP" / name).read_bytes() == (tmp_path / "MAP" / name).read_bytes(), \
            f"{name} is stale: HH_UPDATE_FIXTURES=1 python3 -m pytest {Path(__file__).name}"
    assert (C_FIXTURE / "expect.txt").read_text() == expect
