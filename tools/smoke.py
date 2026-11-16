#!/usr/bin/env python3
"""End-to-end smoke test against hondalog-sim on a real pty. No car needed.

Part 1 (no GUI): LiveSession (poller -> pipeline -> recorder) on the sim pty:
                 a drive in the database with raw bytes, math channels, and a
                 stall capture (RPM to 0) that plain sqlite3 can see.
Part 1b:         the same with the ECU going silent (power cut).
Part 1c:         the handheld's USB serial stream (hondalog-cydsim on a pty,
                 port "cyd:"): a recorded drive with the handheld's stall and
                 its GPS (gps_track, stall_locations, GPX / KML, hondalog-log
                 stalls --geojson), then "hondalog-handheld sync" pulling its
                 SD drives into the database.
Part 2 (offscreen GUI): main window at 1366x768 against a second sim run,
        marker, memory sweep, stall banner, replay, the Map screen (F8:
        the capture, All stalls with the handheld's); saves screenshots to
        docs/screenshots/sim-*.png. No map tile server is asked for anything.

    QT_QPA_PLATFORM=offscreen python3 tools/smoke.py
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="hondalog-smoke-"))
os.environ["HONDALOG_DIR"] = str(TMP / "logs")
os.environ["XDG_CONFIG_HOME"] = str(TMP / "config")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["HONDALOG_TILES"] = "offline"                 # the Map screen: cached tiles only
os.environ["HONDALOG_TILE_CACHE"] = str(TMP / "tiles")
LINK = str(TMP / "sim-pty")

from hondalog import addressmap, protocol                    # noqa: E402
from hondalog.data import open_log                            # noqa: E402
from hondalog.live import LiveSession                         # noqa: E402
from hondalog.logger import read_log                          # noqa: E402
from hondalog.poller import OK                                # noqa: E402


def start_sim(*args):
    proc = subprocess.Popen([sys.executable, "-m", "hondalog.sim", "--link", LINK, *args],
                            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
    print("  sim:", proc.stdout.readline().strip())
    assert os.path.exists(LINK), "simulator did not create its pty link"
    return proc


def check(cond, what):
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        raise SystemExit(1)


def headless():
    print("Part 1: headless poller against the sim pty")
    sim = start_sim("--warmup", "6", "--rev-every", "5", "--stall-after", "13", "--restart-after", "0")
    amap = addressmap.load_user_map()
    live = LiveSession(amap)                # the same path the app and hondalog-log record use
    poller = live.connect(LINK)
    rec = None
    try:
        check(poller.ready.wait(5), "port opened")
        check(poller.ecu_id is not None and b"SIM" in poller.ecu_id,
              f"ECU id read: {poller.ecu_id_text}")
        check(poller.link.echo_seen, "K-line echo seen and stripped")
        rec = live.start_recording()
        time.sleep(4)
        live.mark("smoke marker")
        check(poller.state == OK and poller.errors == 0,
              f"polling: {poller.reads} reads, {poller.errors} errors, {poller.hz:.1f} Hz")
        end = time.monotonic() + 30
        while rec.events.empty() and time.monotonic() < end:
            time.sleep(0.1)
        check(not rec.events.empty(), "stall capture saved")
        event = rec.events.get()
    finally:
        live.close()
        sim.terminate()
        sim.wait()
    log, stall = read_log(rec.path), read_log(event.path)
    check(rec.path.db == TMP / "logs" / "hondalog.db" and rec.path.db.is_file(),
          f"recorded into {rec.path.db.name} as {rec.path.name}")
    check(len(log.t) > 100 and log.meta["address_map_tag"] == amap.version_tag,
          f"drive {rec.path.name}: {len(log.t)} rows, header has map version and hash")
    with open_log(rec.path) as native:
        raw = [s for s in native.streams.values() if s.info.raw_len]
        check(len(raw) >= 5 and all(s.rows > 3 for s in raw) and "math" in native.streams,
              f"{len(raw)} read streams with raw bytes, math channels logged")
        check(native.meta["link"]["ok"] > 100 and native.meta["unlocked_frames"] == [],
              "link counters and the (empty) unlocked-frame list are in the header")
    check(any("smoke marker" in m for _, m in log.markers), "marker is in the log")
    check(float(log.data["rpm"].max()) > 2500, f"fake rev seen (max {log.data['rpm'].max():.0f} rpm)")
    st = float(stall.meta["stall_time_s"])
    check(event.reason == "rpm" and stall.data["rpm"][-1] == 0,
          f"{event.path.name}: trigger '{stall.meta['stall_trigger']}' at {st:.1f} s")
    with open_log(event.path) as cap:       # every stream has its own times: ask the capture
        before, after = event.t - cap.t0, cap.t1 - event.t
    check(after >= 10.0 and before > 8.0,
          f"stall window: {before:.1f} s before, {after:.1f} s after")
    # the capture landed in the database: ask it with nothing but sqlite3
    conn = sqlite3.connect(rec.path.db)
    row = conn.execute("SELECT name, drive, reason, rows, t_trigger - t_start, t_end - t_trigger FROM stalls").fetchone()
    check(row is not None and row[:3] == (event.path.name, rec.path.name, "rpm") and row[3] == event.rows,
          f"stalls view: {row[0]} in {row[1]}, {row[3]} rows, {row[4]:.1f} s before, {row[5]:.1f} s after")
    last = conn.execute(f"SELECT count(*), max(rpm), min(rpm) FROM {rec.path.name} WHERE t > ?", (event.t - 5,)).fetchone()
    check(last[0] > 20 and last[2] == 0, f"view {rec.path.name}: {last[0]} rows in the stall's last 5 s and after")
    status, ended, stalls = conn.execute("SELECT status, ended, stalls FROM drive_summary").fetchone()
    check(status == "complete" and ended and stalls == 1, f"drive_summary: {status}, ended {ended}, {stalls} stall")
    conn.close()
    return event.path


def headless_power_cut():
    print("Part 1b: ECU loses power (K-line goes silent) while recording")
    sim = start_sim("--warmup", "4", "--rev-every", "0", "--stall-after", "5",
                    "--stall-mode", "power", "--restart-after", "0")
    amap = addressmap.load_user_map()
    live = LiveSession(amap)
    poller = live.connect(LINK)
    rec = None
    try:
        check(poller.ready.wait(5) and poller.ecu_id is not None, "connected")
        rec = live.start_recording()
        end = time.monotonic() + 30
        while rec.events.empty() and time.monotonic() < end:
            time.sleep(0.1)
        check(not rec.events.empty(), "stall capture saved")
        event = rec.events.get()
        check(poller.state == "no reply" and "echo only" in poller.last_error,
              f"link state '{poller.state}', last error: {poller.last_error}")
    finally:
        live.close()
        sim.terminate()
        sim.wait()
    stall = read_log(event.path)
    st = float(stall.meta["stall_time_s"])
    check(event.reason == "link" and stall.data["link"][-1] == 0,
          f"{event.path.name}: trigger '{stall.meta['stall_trigger']}' at {st:.1f} s")
    with open_log(event.path) as cap:
        after = cap.t1 - event.t
    check(after >= 10.0, f"kept logging {after:.1f} s after the drop")
    # delete that drive again the way the owner would: hondalog-log rm
    from hondalog import logtool
    before = sqlite3.connect(event.path.db).execute("SELECT count(*) FROM drives").fetchone()[0]
    code = logtool.main(["rm", "--yes", event.path.drive])
    left = [r[0] for r in sqlite3.connect(event.path.db).execute("SELECT name FROM drives")]
    check(code == 0 and len(left) == before - 1 and event.path.drive not in left,
          f"hondalog-log rm removed {event.path.drive}; {len(left)} drive(s) left")


def headless_cyd():
    print("Part 1c: the handheld (CYD) over its USB serial port, hondalog-cydsim on a pty")
    amap = addressmap.load_user_map()
    link, sd = str(TMP / "cyd-pty"), TMP / "cyd-sd"
    proc = subprocess.Popen([sys.executable, "-m", "hondalog.handheld.cydsim", "--link", link, "--sd", str(sd),
                             "--map", str(addressmap.user_map_path()), "--stall-after", "6", "--restart-after", "0"],
                            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)
    print("  cydsim:", proc.stdout.readline().strip())
    live = LiveSession(amap)
    try:
        poller = live.connect("cyd:" + link)
        check(poller.ready.wait(5) and poller.hello is not None and poller.hello.two_way,
              f"hello: {poller.hello.text if poller.hello else '(none)'}")
        check(not poller.map_mismatch, f"the handheld runs this map (#{amap.version_hash})")
        rec = live.start_recording()
        end = time.monotonic() + 30
        while rec.events.empty() and time.monotonic() < end:
            time.sleep(0.1)
        check(not rec.events.empty(), "stall capture saved from the handheld's stream")
        check(poller.lost == 0 and poller._deframer.bad == 0, f"link: {poller.link_text()}")
    finally:
        live.close()
    with open_log(rec.path) as native:
        check(native.meta.get("source") == "cyd" and "gps" in native.streams,
              f"drive {rec.path.name}: source cyd, {len(native.streams)} streams with GPS")
    gps_checks(rec.path)
    from hondalog.handheld import cli
    try:
        code = cli.main(["sync", "--port", "cyd:" + link, "--end-drive"])
    finally:
        proc.terminate()
        proc.wait()
    conn = sqlite3.connect(rec.path.db)
    n = conn.execute("SELECT count(*) FROM drives WHERE meta LIKE '%\"source\": \"handheld\"%'").fetchone()[0]
    conn.close()
    check(code == 0 and n >= 1, f"hondalog-handheld sync: {n} drive(s) from the handheld's SD card")


def gps_checks(ref):
    """The handheld's GPS of the drive just recorded: the views, the exports."""
    import json
    import xml.etree.ElementTree as ET

    from hondalog import logtool
    from hondalog.geo import route
    conn = sqlite3.connect(ref.db)
    n, lat0, lat1, lon0, lon1 = conn.execute(
        "SELECT count(*), min(lat), max(lat), min(lon), max(lon) FROM gps_track WHERE drive = ? AND fix = 1",
        (ref.name,)).fetchone()
    loop = route.PEARL_LOOP
    check(n >= 5 and loop.lat.min() - 0.002 < lat0 <= lat1 < loop.lat.max() + 0.002
          and loop.lon.min() - 0.002 < lon0 <= lon1 < loop.lon.max() + 0.002,
          f"gps_track: {n} fixes on the Pearl loop")
    row = conn.execute("SELECT name, lat, lon, gps_dt FROM stall_locations WHERE drive = ? AND kind = 'stall'",
                       (ref.name,)).fetchone()
    conn.close()
    check(row is not None and row[1] is not None and abs(row[3]) <= 1.5,
          f"stall_locations: {row[0] if row else '-'} at {row[1]:.5f}, {row[2]:.5f}" if row and row[1] else
          "stall_locations: the handheld's stall has a place")
    gpx, kml, geo = TMP / "cyd.gpx", TMP / "cyd.kml", TMP / "stalls.geojson"
    codes = [logtool.main(["export", ref.name, str(gpx)]), logtool.main(["export", ref.name, str(kml), "--channel", "rpm"]),
             logtool.main(["stalls", "-q", "--geojson", str(geo)])]
    pts = ET.parse(gpx).getroot().findall(".//{http://www.topografix.com/GPX/1/1}trkpt")
    features = json.loads(geo.read_text())["features"]
    check(codes == [0, 0, 0] and len(pts) == n and ET.parse(kml).getroot().tag.endswith("kml") and features,
          f"GPX ({len(pts)} points), KML by rpm, GeoJSON ({len(features)} stall(s)) written and read back")


def gui(docs: Path):
    print("Part 2: offscreen GUI at 1366x768")
    from hondalog import ui  # noqa: F401
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from hondalog.ui import adapters, scene3d, theme
    from hondalog.ui.main_window import MAP, RAW, REPLAY, SENSORS, Shell
    from hondalog.ui.session import build_session

    scene3d.default_surface_format()
    app = QApplication([])
    app.setStyle("Fusion")
    theme.apply(app)
    session = build_session()
    win = Shell(session, QSettings(str(TMP / "ui.ini"), QSettings.IniFormat))
    win.resize(1366, 768)
    win.show()
    shots = docs / "screenshots"
    shots.mkdir(exist_ok=True)

    def spin(seconds, until=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if until and until():
                return True
            time.sleep(0.005)
        return until is None

    sim = start_sim("--warmup", "8", "--rev-every", "6", "--stall-after", "16", "--restart-after", "0")
    try:
        win.connect_port(LINK)
        check(spin(5, lambda: session.ecu_status.state == adapters.OK), "GUI connected to the sim pty")
        check(spin(2, lambda: session.recording.active), "auto-record started")
        spin(4)
        win.mark()
        spin(3)
        win.mark()
        check(session.recording.marks == 2, "two markers dropped")
        n_tiles = len(win.sensors.tiles)
        check(n_tiles >= 32, f"{n_tiles} sensor tiles on one screen")
        check(len(session.bus.channels("MATH")) >= 5 and session.bus.channel("inj_duty").gen > 3,
              "math channels are live as their own group")
        check("fast" in session.ecu.link_text(), f"link: {session.ecu.link_text()}")
        spin(1.2)
        win.grab().save(str(shots / "sim-sensors.png"))

        win.show_screen(RAW)
        mapper = session.mapper
        mapper.send(protocol.parse_hex("20 05 76 0A"), True)
        mapper.set_sweeping(True)
        check(spin(12, lambda: mapper.sweeps >= 3), "three memory sweeps done")
        check(spin(25, lambda: mapper.sweeps >= 8), "eight sweeps for the correlation finder")
        mapper.set_sweeping(False)
        moved = int((mapper.changes > 0).sum())
        check(moved > 5 and (mapper.cur >= 0).all(), f"mapper saw {moved} bytes change, all 256 read")
        check("checksum OK" in mapper.console(), "hex console round trip")
        mapper.send(protocol.parse_hex("21 04 00"), True)
        check(spin(3, lambda: "blocked" in mapper.console()), "a frame that is not a read is refused")
        win.raw.show_rank(True)
        win.raw.rank.set_reference("rpm")
        win.raw.rank.rank()
        ranked = win.raw.rank.rows
        check(win.raw.rank.ranked and (ranked or mapper.console()),
              "rank addresses against rpm: " + (", ".join(f"{r.label} {r.score:.2f}" for r in ranked[:3])
                                                or mapper.console()))
        win.raw.show_rank(False)
        mapper.export(TMP / "scan.txt")
        check((TMP / "scan.txt").stat().st_size > 1000, "sweep export")
        spin(0.3)
        win.grab().save(str(shots / "sim-rawtools.png"))

        win.show_screen(SENSORS)
        check(spin(40, lambda: win.banner.isVisible()), "stall banner shown")
        spin(0.5)
        check(win.grab().save(str(shots / "sim-stall-banner.png")), "stall banner screenshot saved")
        stall_path = session.banner.path
        rate = session.ecu_status.rate_hz
        win.open_stall()
        view = win.replay.view
        check(win.current() == REPLAY and view is not None and view.trigger_t is not None
              and len(view.markers) >= 1, "stall capture opens in replay with markers and a trigger")
        model = win.replay.model
        check(model is not None and model.session is view.replay and win.replay.block.traces >= 1,
              "replay lanes are drawn from the drive's ReplaySession "
              f"({len(model.events())} events to step through)")
        check(len(win.replay.forensics.loss) >= 1, "order of loss computed: "
              + "; ".join(f"{r.signal} {r.t:+.1f}s" for r in win.replay.forensics.loss[:3]))
        spin(0.5)
        win.grab().save(str(shots / "sim-replay.png"))
        win.show_screen(MAP)
        spin(0.3)
        check(win.map.track is not None and win.map.track.empty
              and "No GPS" in win.map.canvas.caption, "Map (F8): a K-line-only capture says it has no GPS")
        win.map.set_mode("stalls")
        spin(0.3)
        placed = [p for p in win.map.stall_pins if p.located]
        check(len(win.map.stall_pins) >= 2 and placed and len(win.map.canvas.pins) == len(placed),
              f"Map, All stalls: {len(win.map.stall_pins)} stalls, {len(placed)} with a place (the handheld's)")
        win.map.canvas.pinClicked.emit(win.map.stall_pins.index(placed[0]))
        spin(0.3)
        check(win.current() == REPLAY and win.replay.view.title == placed[0].name,
              f"a pin opens {placed[0].name} in Replay")
        win.show_screen(MAP)
        win.map.set_mode("drive")
        spin(0.3)
        tr = win.map.track
        check(tr is not None and not tr.empty and win.map.canvas._timg is not None,
              f"Map, Drive: the capture's track, {len(tr)} fixes, drawn without tiles")
        c = win.map.canvas
        sx, sy = c.screen_of(tr.x[len(tr) // 2], tr.y[len(tr) // 2])
        c.clicked.emit(*c.unit_at(sx, sy))
        check(abs(win.replay.block.cursor - tr.t[len(tr) // 2]) < 1.0,
              f"a click on the track moves Replay's cursor to {win.replay.block.cursor:+.1f} s")
        session.disconnect()
        print(f"  window {win.width()}x{win.height()}, last poll rate {rate:.1f} Hz, "
              f"stall capture {stall_path.name} in {stall_path.db.name}")
    finally:
        win.close()
        sim.terminate()
        sim.wait()


if __name__ == "__main__":
    docs = ROOT / "docs"
    if not os.access(docs if docs.exists() else ROOT, os.W_OK):
        docs = TMP                      # installed copy in /opt is read-only
    docs.mkdir(exist_ok=True)
    print(f"screenshots go to {docs}")
    headless()
    headless_power_cut()
    headless_cyd()
    gui(docs)
    print(f"ALL OK  (scratch files in {TMP})")
