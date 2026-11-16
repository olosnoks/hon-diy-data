"""GPX, KML and GeoJSON: written, parsed back and checked; the CLI that makes
them; the SQL examples of the GPS section in docs/data-and-sql.md."""

import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from hondalog import logtool, ramps
from hondalog.data import open_log
from hondalog.data.synth import write_gps_drives, write_synthetic
from hondalog.export import FORMATS, export_log
from hondalog.export.geo import KML_BINS, export_geo, place
from hondalog.geo import stalls as gs
from hondalog.geo.track import Track

GPX = "{http://www.topografix.com/GPX/1/1}"
KML = "{http://www.opengis.net/kml/2.2}"
HL = "{https://github.com/olosnoks/hon-diy-data/gpx/1}"
DOCS = Path(__file__).parent.parent / "docs" / "data-and-sql.md"


@pytest.fixture(scope="module")
def gps_db(tmp_path_factory):
    folder = tmp_path_factory.mktemp("gpsx")
    path = folder / "hondalog.db"
    refs = write_gps_drives(path, drives=4, duration_s=600, started_unix=datetime(2026, 11, 2, 7, 12).timestamp())
    gappy = write_synthetic(path, 400, fast_hz=4, slow_hz=1, blackbox_hz=0, markers=1, seed=42, gps_hz=1.0,
                            started_unix=datetime(2026, 11, 20, 17, 30).timestamp())
    # cut a hole in the GPS of the last drive: 40 s without a fix (a tunnel, the handheld unplugged)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE samples SET v0 = NULL, v1 = NULL WHERE drive_id = (SELECT id FROM drives WHERE name = ?) "
                 "AND stream_id = (SELECT s.id FROM streams s JOIN drives d ON d.id = s.drive_id "
                 "WHERE d.name = ? AND s.name = 'gps') AND t BETWEEN 200 AND 240", (gappy.name, gappy.name))
    conn.commit()
    conn.close()
    # and a K-line-only drive with a stall: in stall_locations without a place
    write_synthetic(path, 200, fast_hz=4, slow_hz=1, blackbox_hz=0, markers=0, seed=43, stalls=(100.0,),
                    started_unix=datetime(2026, 10, 30, 8, 0).timestamp())
    return path, refs, gappy


def test_gpx_parses_back_with_times_segments_and_waypoints(gps_db, tmp_path):
    path, refs, gappy = gps_db
    out = tmp_path / "drive.gpx"
    with open_log(gappy) as log:
        export_geo(log, out)
        epoch = float(log.meta["epoch_unix"])
        lat_series = log.series("gps_lat")
    root = ET.parse(out).getroot()
    assert root.tag == f"{GPX}gpx" and root.get("version") == "1.1" and root.get("creator").startswith("hondalog")
    # the schema's order: metadata, wpt, trk
    order = [child.tag.replace(GPX, "") for child in root]
    assert order[0] == "metadata" and order[-1] == "trk" and set(order[1:-1]) <= {"wpt"}
    segs = root.findall(f"{GPX}trk/{GPX}trkseg")
    assert len(segs) == 2                                   # the 40 s hole breaks it
    pts = [pt for seg in segs for pt in seg.findall(f"{GPX}trkpt")]
    fixes = int(np.isfinite(lat_series.v).sum())
    assert len(pts) == fixes == 400 - 40                   # fixes at .31 s: 40 in the hole
    times = [datetime.fromisoformat(pt.find(f"{GPX}time").text.replace("Z", "+00:00")) for pt in pts]
    assert all(b > a for a, b in zip(times, times[1:]))
    first_t = lat_series.t[np.isfinite(lat_series.v)][0]
    assert times[0] == datetime.fromtimestamp(epoch + first_t, timezone.utc).replace(microsecond=times[0].microsecond)
    assert abs(times[0].timestamp() - (epoch + first_t)) < 0.002
    lats = np.array([float(pt.get("lat")) for pt in pts])
    lons = np.array([float(pt.get("lon")) for pt in pts])
    assert 32.25 < lats.min() and lats.max() < 32.29 and -90.15 < lons.min() and lons.max() < -90.09
    speed = [pt.find(f"{GPX}extensions/{HL}speed_kmh") for pt in pts]
    assert all(s is not None for s in speed) and max(float(s.text) for s in speed) > 30
    wpts = root.findall(f"{GPX}wpt")
    kinds = sorted(w.find(f"{GPX}type").text for w in wpts)
    assert kinds == ["marker"]
    meta = root.find(f"{GPX}metadata/{GPX}bounds")
    assert float(meta.get("minlat")) == pytest.approx(lats.min(), abs=1e-6)


def test_gpx_waypoints_for_stalls_and_captures(gps_db, tmp_path):
    path, refs, gappy = gps_db
    out = tmp_path / "second.gpx"
    with open_log(refs[1]) as log:
        export_geo(log, out, channel="rpm")
        track = Track.from_log(log)
    root = ET.parse(out).getroot()
    wpts = {w.find(f"{GPX}name").text: w for w in root.findall(f"{GPX}wpt")}
    stall = next(w for name, w in wpts.items() if name.startswith("stall_"))
    assert stall.find(f"{GPX}type").text == "stall" and stall.find(f"{GPX}desc").text == "rpm dropped to 0"
    assert any(w.find(f"{GPX}type").text == "capture" for w in wpts.values())
    assert sum(w.find(f"{GPX}type").text == "marker" for w in wpts.values()) == 2
    t_stall = sqlite3.connect(path).execute("SELECT t_trigger FROM captures WHERE name = ?",
                                            (stall.find(f"{GPX}name").text,)).fetchone()[0]
    lat, lon = place(track, t_stall)
    assert (float(stall.get("lat")), float(stall.get("lon"))) == (pytest.approx(lat, abs=1e-7),
                                                                  pytest.approx(lon, abs=1e-7))
    first = root.find(f"{GPX}trk/{GPX}trkseg/{GPX}trkpt/{GPX}extensions")
    assert first.find(f"{HL}rpm") is not None               # the chosen channel rides along


def test_kml_is_coloured_by_the_channel(gps_db, tmp_path):
    path, refs, gappy = gps_db
    out = tmp_path / "drive.kml"
    with open_log(refs[0]) as log:
        export_log(log, out, channels=["rpm"])              # the export menu's path: by extension
        track = Track.from_log(log)
        rpm = log.series("rpm")
        values = track.sample(rpm.t, rpm.v)
    root = ET.parse(out).getroot()
    doc = root.find(f"{KML}Document")
    styles = {s.get("id"): s.find(f"{KML}LineStyle/{KML}color").text for s in doc.findall(f"{KML}Style")
              if s.find(f"{KML}LineStyle") is not None}
    assert len(styles) == KML_BINS + 1
    lo, hi, div = ramps.value_range("rpm", values)
    colours = ramps.bin_colours(KML_BINS, div)
    r, g, b = (int(round(float(c) * 255)) for c in colours[0])
    assert styles["bin0"] == f"ff{b:02x}{g:02x}{r:02x}"     # aabbggrr, the Map screen's ramp
    assert all(re.fullmatch(r"ff[0-9a-f]{6}", c) for c in styles.values())
    assert "rpm" in doc.find(f"{KML}description").text
    lines = doc.findall(f".//{KML}Placemark/{KML}LineString/{KML}coordinates")
    assert len(lines) > 10
    used = {pm.find(f"{KML}styleUrl").text for pm in doc.findall(f".//{KML}Placemark")
            if pm.find(f"{KML}LineString") is not None}
    assert len(used) > 4                                    # it really changes colour along the way
    coords = [tuple(map(float, c.split(","))) for line in lines for c in line.text.split()]
    assert all(-90.15 < lon < -90.09 and 32.25 < lat < 32.29 for lon, lat in coords)
    # every fix is in a piece (pieces share their joining point)
    assert len(coords) >= len(track)
    pins = [pm for pm in doc.findall(f".//{KML}Placemark") if pm.find(f"{KML}Point") is not None]
    assert any(pm.find(f"{KML}styleUrl").text == "#pin_stall" for pm in pins)


def test_no_gps_no_export(gps_db, tmp_path):
    path = gps_db[0].parent / "plain.db"
    ref = write_synthetic(path, 60, fast_hz=2, slow_hz=1, blackbox_hz=0, markers=0)
    with open_log(ref) as log, pytest.raises(ValueError, match="no GPS"):
        export_geo(log, tmp_path / "x.gpx")
    assert ".gpx" in FORMATS and ".kml" in FORMATS


def test_geojson_of_the_stalls(gps_db, tmp_path):
    path, refs, gappy = gps_db
    pins = gs.stall_pins(path)
    out = gs.write_geojson(pins, tmp_path / "stalls.geojson")
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["type"] == "FeatureCollection" and len(doc["features"]) == 4 and doc["unlocated"] == 1
    for f in doc["features"]:
        lon, lat = f["geometry"]["coordinates"]              # RFC 7946: longitude first
        assert f["geometry"]["type"] == "Point" and -91 < lon < -90 and 32 < lat < 33
        assert f["properties"]["name"].startswith("stall_") and f["properties"]["kind"] == "stall"
    sizes = sorted(f["properties"]["cluster_size"] for f in doc["features"])
    assert sizes == [1, 3, 3, 3]


def test_cli_export_and_stalls(gps_db, tmp_path, capsys):
    path, refs, gappy = gps_db
    db = str(path)
    assert logtool.main(["--db", db, "export", refs[0].name, str(tmp_path / "a"), "--format", "gpx"]) == 0
    assert ET.parse(tmp_path / "a.gpx").getroot().tag == f"{GPX}gpx"
    assert logtool.main(["--db", db, "export", refs[0].name, str(tmp_path / "b.kml"), "--channel", "map"]) == 0
    assert "map" in ET.parse(tmp_path / "b.kml").getroot().find(f"{KML}Document/{KML}description").text
    assert logtool.main(["--db", db, "export", refs[0].name, str(tmp_path / "c.out"), "--format", "kml"]) == 0
    assert ET.parse(tmp_path / "c.out").getroot().tag == f"{KML}kml"
    capsys.readouterr()
    assert logtool.main(["--db", db, "stalls", "--geojson", str(tmp_path / "s.geojson")]) == 0
    text = capsys.readouterr().out
    assert "wrote" in text and "group 1" in text and "3 of them within" in text
    assert len(json.loads((tmp_path / "s.geojson").read_text())["features"]) == 4
    assert logtool.main(["--db", db, "stalls", "--geojson", "-", "--all"]) == 0
    assert len(json.loads(capsys.readouterr().out)["features"]) == 5      # and the manual capture (not the unplaced)
    assert logtool.main(["--db", db, "stalls", "-q", "--since", "2026-11-06", "--geojson", "-"]) == 0
    late = json.loads(capsys.readouterr().out)["features"]
    assert {f["properties"]["drive"] for f in late} == {refs[2].name, refs[3].name}
    assert logtool.main(["--db", db, "stalls", "--reason", "link"]) == 0
    assert "No captures match" in capsys.readouterr().out


def test_docs_gps_sql_examples_run(gps_db):
    """Every query of the GPS section of docs/data-and-sql.md runs against a
    database with GPS drives and stalls and returns rows."""
    path, refs, gappy = gps_db
    text = DOCS.read_text(encoding="utf-8")
    start = text.index("### GPS and where the stalls happen")
    section = text[start:text.index("\n### ", start + 10)]
    queries = re.findall(r"```sql\n(.*?)```", section, re.S)
    assert len(queries) >= 5
    conn = sqlite3.connect(path)
    for query in queries:
        rows = conn.execute(query.replace("drive_2026_11_02_0712", refs[0].name)).fetchall()
        assert rows, query
    assert re.search(r"^\| `gps_track` \|", text, re.M) and re.search(r"^\| `stall_locations` \|", text, re.M)
