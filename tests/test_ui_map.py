"""The Map screen (F6 GPS map) offscreen: the track of Replay's drive, click to move
Replay's cursor and the dot that follows it, colouring, the All stalls
view on a real database, exports, the live position, tiles (fixture tiles,
both dark themes, the grid without any) and the paint budget for an hour."""

import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from conftest import spin
from hondalog.data.synth import write_gps_drives
from hondalog.geo import proj, route
from hondalog.geo.track import Track
from hondalog.ui import main_window as mw
from hondalog.ui import theme

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
import fixture_tiles  # noqa: E402


def show_map(shell, app):
    # these tests are written around the demo stall capture; the demo Replay opens on a simulated drive
    view = shell.replay.view
    if view is not None and view.title.startswith("demo_drive"):
        from hondalog.ui.backends import demo_stall_view
        shell.replay.show_view(demo_stall_view())
    shell.show_screen(mw.MAP)
    spin(app, shell, 3)
    return shell.map


def test_f8_shows_the_track_of_replays_capture(shell, app):
    m = show_map(shell, app)
    assert shell.tabs.current == mw.MAP and shell.current() == mw.MAP
    assert m.track is not None and len(m.track) == 41                      # the demo capture: -30 s .. +10 s
    kinds = sorted(p.kind for p in m.canvas.pins)
    assert kinds == ["marker", "marker", "stall", "start"]
    assert m.canvas.title == shell.replay.view.title
    st = m.stats()
    assert st["grid"] > 0 and st["tiles"] == 0                              # no tiles offline: the plain grid
    assert m.canvas._timg is not None and st["runs"] > 0                    # ... and the track all the same
    assert "OpenStreetMap contributors" in m.canvas._texts["attr"]
    assert m.colour_cid == "gps_speed" and m.legend.title.startswith("Colour: GPS speed")
    assert not any(row.channel_id.startswith("gps_") for row in shell.replay.forensics.loss)


def test_click_on_the_track_moves_replays_cursor(shell, app):
    m = show_map(shell, app)
    tr, c = m.track, m.canvas
    i = 12
    sx, sy = c.screen_of(tr.x[i], tr.y[i])
    QTest.mouseClick(c, Qt.LeftButton, Qt.NoModifier, QPoint(round(sx) + 3, round(sy)))
    spin(app, shell)
    hit = tr.nearest(*c.unit_at(round(sx) + 3, round(sy)))
    assert shell.replay.block.cursor == pytest.approx(hit[0], abs=1e-6)
    assert abs(shell.replay.block.cursor - tr.t[i]) < 1.0                   # a fix is a second apart
    pos = tr.at(shell.replay.block.cursor)
    assert c.cursor == (pytest.approx(pos.x), pytest.approx(pos.y))
    # a click away from the track changes nothing
    before = shell.replay.block.cursor
    QTest.mouseClick(c, Qt.LeftButton, Qt.NoModifier, QPoint(c.width() - 60, 80))
    spin(app, shell)
    assert shell.replay.block.cursor == before
    # a drag pans and does not move the cursor
    cx = c.cx
    QTest.mousePress(c, Qt.LeftButton, Qt.NoModifier, QPoint(300, 300))
    for k in range(1, 6):
        QTest.mouseMove(c, QPoint(300 + 20 * k, 300))
    QTest.mouseRelease(c, Qt.LeftButton, Qt.NoModifier, QPoint(400, 300))
    assert c.cx < cx and shell.replay.block.cursor == before


def test_replays_cursor_is_the_dot(shell, app):
    m = show_map(shell, app)
    shell.replay.block.set_cursor(-5.0)
    spin(app, shell)
    pos = m.track.at(-5.0)
    assert m.canvas.cursor == (pytest.approx(pos.x), pytest.approx(pos.y))
    assert m.at.cells[0] == ("Time", "−5.0 s")
    shell.replay.block.set_cursor(9.9)
    spin(app, shell)
    assert m.canvas.cursor == (pytest.approx(m.track.at(9.9).x), pytest.approx(m.track.at(9.9).y))
    # a stall pin moves the cursor to the stall
    stall = next(p for p in m.canvas.pins if p.kind == "stall")
    m._pin_clicked(stall.payload)
    assert shell.replay.block.cursor == pytest.approx(0.0)
    # [ and ] step through the pins
    m.step_waypoint(-1)
    assert shell.replay.block.cursor == pytest.approx(-9.0)                 # MARK 2


def test_colour_by_presets_and_any_channel(shell, app):
    m = show_map(shell, app)
    m.set_colour("rpm")
    assert m.colour_cid == "rpm" and "RPM" in m.legend.title and m.col_seg.current == "rpm"
    assert np.isfinite(m.values).sum() == len(m.track)
    m.set_colour("ecub")                                                    # a black box rail
    assert m.colour_cid == "ecub" and m.col_seg.current is None and m.chan_btn.isChecked()
    assert "ECU +B" in m.chan_btn.text()
    m.set_colour("afr")                                                     # not in the demo capture
    assert m.colour_cid == "" and "not in this drive" in m.legend.title
    m.cycle_colour()
    assert m.colour == "speed" and m.colour_cid == "gps_speed"
    renders = m.canvas.renders
    m.set_colour("map")
    assert m.canvas.renders == renders + 1                                  # recoloured once


@pytest.fixture
def stall_db(tmp_path):
    folder = tmp_path / "logs"
    now = time.time()
    refs = write_gps_drives(folder / "hondalog.db", drives=4, duration_s=600, started_unix=now - 20 * 86400)
    # make the last two drives recent (2 and 1 days ago) for the date filter
    import sqlite3
    conn = sqlite3.connect(folder / "hondalog.db")
    for ref, days in ((refs[2], 2), (refs[3], 1)):
        conn.execute("UPDATE captures SET wall_unix = ?, wall_iso = datetime(?, 'unixepoch', 'localtime') "
                     "WHERE drive_id = (SELECT id FROM drives WHERE name = ?)",
                     (now - days * 86400, now - days * 86400, ref.name))
    conn.commit()
    conn.close()
    return folder, refs


def wire_logs(shell, folder):
    from hondalog.ui import wired
    from hondalog.ui.session import _lookup
    shell.session.logs = wired.DriveReader(_lookup(shell.session), folder)


def test_all_stalls_on_a_database(shell, app, stall_db):
    folder, refs = stall_db
    wire_logs(shell, folder)
    m = show_map(shell, app)
    m.set_mode("stalls")
    spin(app, shell)
    assert len(m.stall_pins) == 4 and len(m.canvas.pins) == 4 and m.track is not None
    assert m.canvas._timg is None                                           # the drive's track is not drawn here
    assert "4 stalls. 3 of them within" in m.reading._text
    assert all(p.kind == "stall" for p in m.canvas.pins)
    assert m.canvas.title == "All stalls · 4 of 4 placed"
    m.since_seg.set_current("7")
    m.load_stalls()
    assert {p.drive for p in m.stall_pins} == {refs[2].name, refs[3].name}
    m.since_seg.set_current("all")
    m.set_reason((None, None), "Every capture")
    assert len(m.stall_pins) == 5 and any(p.kind == "capture" for p in m.canvas.pins)
    m.set_reason(("manual", ("capture",)), "capture: manual  (1)")
    assert [p.reason for p in m.stall_pins] == ["manual"] and m.reason_btn.text() == "capture: manual"
    m.set_reason((None, ("stall",)), "Stalls, any reason")
    # a pin click opens that capture in Replay
    first = m.stall_pins[0]
    m.canvas.pinClicked.emit(0)
    spin(app, shell)
    assert shell.current() == mw.REPLAY and shell.replay.view.title == first.name
    assert shell.replay.view.trigger_t == 0.0
    # back on F8, the Drive view shows that capture's 40 s around its pin
    m.set_mode("drive")
    show_map(shell, app)
    assert m.track is not None and 35 <= len(m.track) <= 41
    pos = m.track.at(0.0)
    assert float(proj.haversine_m(pos.lat, pos.lon, first.lat, first.lon)) < 30.0


def test_exports_from_the_screen(shell, app, stall_db, tmp_path):
    folder, refs = stall_db
    wire_logs(shell, folder)
    assert shell.replay.open(refs[1])
    m = show_map(shell, app)
    assert len(m.track) == 600 and m.gpx_btn.isEnabled()
    m.set_colour("rpm")
    text = m.export("gpx", tmp_path / "d.gpx")
    assert text.startswith("Wrote") and ET.parse(tmp_path / "d.gpx").getroot().tag.endswith("gpx")
    m.export("kml", tmp_path / "d.kml")
    desc = ET.parse(tmp_path / "d.kml").getroot()[0].find("{http://www.opengis.net/kml/2.2}description").text
    assert "rpm" in desc
    # the drive as GeoJSON (the web app's three formats)
    assert m.drive_json_btn.isVisible() and m.export("geojson", tmp_path / "d.geojson").startswith("Wrote")
    doc = json.loads((tmp_path / "d.geojson").read_text(encoding="utf-8"))
    kinds = [f["properties"]["kind"] for f in doc["features"]]
    assert doc["type"] == "FeatureCollection" and kinds[0] == "track"
    assert doc["features"][0]["geometry"]["type"] == "LineString" and "rpm" in doc["features"][0]["properties"]
    m.set_mode("stalls")
    assert not m.drive_json_btn.isVisible()
    assert "4 stalls" in m.export("geojson", tmp_path / "s.geojson")
    assert (tmp_path / "s.geojson").stat().st_size > 200


def test_live_position_and_trail(shell, app):
    gps = shell.session.extra_sources[0]
    gps.prefill(shell.session.bus, 300.0)
    shell.replay.go_live()
    m = show_map(shell, app)
    c = m.canvas
    assert c.live is not None and c._trail is not None and c._trail.size() >= 290
    lat = shell.session.bus.channel("gps_lat").value
    lon = shell.session.bus.channel("gps_lon").value
    x, y = proj.to_unit(lon, lat)
    assert c.live == (pytest.approx(float(x)), pytest.approx(float(y)))
    assert c.on_screen(*c.live)                                             # it follows the car
    assert c.title.startswith("Live: handheld GPS") and m.at.cells[0][0] == "Speed"
    # a drag stops the following; F starts it again
    c.pan_px(2000, 0)
    m._user_moved()
    assert not c.on_screen(*c.live)
    m.fit()
    assert c.on_screen(*c.live)


def wait_tiles(app, shell, layer, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        spin(app, shell, 1)
        if not layer.fetcher.pending and not layer.fetcher._done and shell.map.stats()["tiles"]:
            break
        time.sleep(0.01)
    spin(app, shell, 1)


def test_fixture_tiles_dark_in_both_themes(shell, app, tmp_path):
    m = show_map(shell, app)
    fixture_tiles.requests.clear()
    layer = fixture_tiles.fixture_layer(tmp_path / "tiles")
    m.use_tiles(layer)
    wait_tiles(app, shell, layer)
    spin(app, shell, 2)
    st = m.stats()
    assert st["tiles"] > 0 and st["grid"] == 0
    assert fixture_tiles.requests and all(h["User-Agent"].startswith("hondalog/") for _, h in fixture_tiles.requests)
    assert "fixture tiles" in m.canvas._texts["attr"]
    key = next(iter(layer._raw))
    dracula = layer.pixmap(key, "dracula").toImage().pixelColor(5, 5)
    darker = layer.pixmap(key, "darker").toImage().pixelColor(5, 5)
    assert dracula.lightness() < 80 and darker.lightness() < dracula.lightness()     # never a light map
    theme.T.set("darker")
    spin(app, shell)
    assert m.stats()["tiles"] > 0
    # the disk cache answers the next time, without the "server"
    asked = len(fixture_tiles.requests)
    again = fixture_tiles.fixture_layer(tmp_path / "tiles")
    m.use_tiles(again)
    wait_tiles(app, shell, again)
    assert m.stats()["tiles"] > 0 and len(fixture_tiles.requests) == asked


def test_an_hour_pans_without_redrawing_the_track(app):
    from hondalog.ui.mapview import MapCanvas
    t, lat, lon, kmh, heading = route.drive(route.PEARL_LOOP, 3600, hz=10, seed=7)
    tr = Track(t, lat, lon, kmh, heading)
    c = MapCanvas(None)
    c.resize(960, 632)
    c.show()
    c.set_track(tr, kmh, 0.0, 75.0)
    start = time.perf_counter()
    c.fit(tr.bbox())
    first = time.perf_counter() - start
    app.processEvents()
    assert c.stats()["points"] < 4000 and len(tr) == 36000
    renders = c.renders
    times = []
    for k in range(60):
        t0 = time.perf_counter()
        c.pan_px(6.0, 3.0 if k % 2 else -3.0)
        c.repaint()
        times.append(time.perf_counter() - t0)
    assert c.renders == renders                                             # panning only moved the image
    assert np.median(times) < 0.010 and first < 0.5
    # a zoom shows the old image scaled at once and draws the new one a slice per frame
    t0 = time.perf_counter()
    c.zoom_by(2)
    c.repaint()
    assert time.perf_counter() - t0 < 0.05 and c.renders == renders and c.stats()["pending"]
    assert c._timg_target.width() == pytest.approx(c._timg.width() * 4)
    slices = 0
    while c.stats()["pending"]:
        t0 = time.perf_counter()
        c.refresh()
        assert time.perf_counter() - t0 < 0.030                             # a slice is a few ms
        slices += 1
    assert c.renders == renders + 1 and c._timg_target.width() == pytest.approx(c._timg.width())
    c.close()


def test_scale_bar_and_grid_without_tiles(app):
    from hondalog.ui import units
    from hondalog.ui.mapview import MapCanvas
    c = MapCanvas(None)
    c.resize(800, 600)
    c.set_pins([])
    units.U.preset("US")
    c.miles = True
    c.center_on(*proj.to_unit(-90.13, 32.27), zoom=15)
    assert c._scale[1].endswith(("ft", "mi")) and 0 < c._scale[0] <= 150
    c.miles = False
    c._layout()
    assert c._scale[1].endswith(("m", "km"))
    assert c._grid_on and not c._tiles_on and c._north[0].size() == 3
    image = c.grab().toImage()
    assert image.width() == 800
