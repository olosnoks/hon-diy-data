#!/usr/bin/env python3
"""UI benchmark: the real app against the built-in simulator ("sim:").

Measures, per screen:
  fps        frame timer rate actually achieved (target 30, never below 20)
  frame ms   work per frame: refresh + paint, flushed inside the frame
  cpu %      whole process (UI thread + poller thread + simulator + black-box sim)
             as a share of ONE core (target under 50)
  latency    last serial byte of a poll sweep -> pixels painted (target under 100 ms)

    QT_QPA_PLATFORM=offscreen python3 tools/bench_ui.py              # CI / no display
    xvfb-run -a -s "-screen 0 1366x768x24" python3 tools/bench_ui.py   # with (software) OpenGL
    python3 tools/bench_ui.py --onscreen --seconds 20                  # on the T430 itself

Numbers from a server CPU say nothing certain about an HD 4000 laptop: run
the last form, or `hondalog --profile`, on the T430.

The Map screen (F8) runs on a one-hour drive with a fix every 0.1 s (36 000
points, ``write_synthetic(gps_hz=10)``) and the tiles of tools/fixture_tiles.py
cached on disk: "map-pan" drags the map around while zooming in and out,
"map-play" plays Replay with the dot moving on the map.
"""

import argparse
import json
import math
import os
import resource
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="hondalog-bench-"))
os.environ["XDG_CONFIG_HOME"] = str(TMP / "config")
os.environ["HONDALOG_DIR"] = str(TMP / "logs")
os.environ["HONDALOG_TILES"] = "offline"                 # never the real OSM servers
os.environ["HONDALOG_TILE_CACHE"] = str(TMP / "tiles")

TARGETS = {"fps_min": 20.0, "fps": 28.5, "cpu_pct": 50.0, "latency_p95": 100.0,
           # the screen that is up all day, with the simulated black box running
           "sensors_cpu_pct": 20.0}
SCENARIOS = ("sensors", "drive", "map3d", "map3d-orbit", "replay-live", "replay-play",
             "rawtools-sweep", "tune", "alarms", "map-pan", "map-play", "sensors-again")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seconds", type=float, default=8.0, help="per scenario (default 8)")
    ap.add_argument("--onscreen", action="store_true", help="use the real display")
    ap.add_argument("--theme", choices=("dracula", "darker", "daylight", "night"), default="dracula")
    ap.add_argument("--only", nargs="*", choices=SCENARIOS, help="run just these")
    ap.add_argument("--json", metavar="FILE", help="also write the results as JSON")
    ap.add_argument("--check", action="store_true", help="exit 1 if a target is missed")
    ap.add_argument("--no-blackbox", action="store_true",
                    help="leave the simulated black box out: its 1 kHz model runs in this "
                         "process and costs about 3 %% of a core that the real box does not")
    args = ap.parse_args(argv)
    if not args.onscreen:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from hondalog import ui  # noqa: F401
    from PySide6.QtCore import QEventLoop, QSettings, QTimer
    from PySide6.QtWidgets import QApplication

    from hondalog.ui import adapters, backends, scene3d, theme
    from hondalog.ui import main_window as mw
    from hondalog.ui.session import build_session

    scene3d.default_surface_format()
    app = QApplication([])
    app.setStyle("Fusion")
    theme.apply(app)
    # an hour around the Pearl loop with GPS at 10 Hz, and fixture tiles for it on disk
    import fixture_tiles
    from hondalog.data.synth import write_synthetic
    from hondalog.geo import route as georoute
    hour = write_synthetic(TMP / "logs" / "hondalog.db", duration_s=3600, fast_hz=8.0, blackbox_hz=0.0,
                           gps_hz=10.0, stalls=(1500.0,), seed=7)
    loop = georoute.PEARL_LOOP
    from hondalog.geo import proj as geoproj
    bx, by = geoproj.to_unit(loop.lon, loop.lat)
    pad = 0.00003
    fixture_tiles.prefill(TMP / "fixture-tiles", (bx.min() - pad, by.min() - pad, bx.max() + pad, by.max() + pad),
                          range(13, 18))
    session = build_session(blackbox_port="off" if args.no_blackbox else None)
    win = mw.Shell(session, QSettings(str(TMP / "ui.ini"), QSettings.IniFormat), profile=True)
    theme.T.set(args.theme)
    win.resize(1366, 768)
    win.show()

    def run(seconds):
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    win.connect_port("sim:")
    deadline = time.monotonic() + 5
    while session.ecu_status.state != adapters.OK and time.monotonic() < deadline:
        run(0.05)
    if session.ecu_status.state != adapters.OK:
        print("simulator did not connect", file=sys.stderr)
        return 2

    # Latency is byte-to-pixel: the link stamps each read with the moment its
    # last reply byte arrived (BlockRead.mono) and publishes that as the wire time.
    run(2.0)                                # let sparklines, history and the map fill a little

    gl_ok, gl_reason = scene3d.gl_status()
    orbit = QTimer()
    orbit.timeout.connect(lambda: win.map3d.scene.orbit(math.radians(1.5), 0.0))
    win.map.use_tiles(fixture_tiles.fixture_layer(TMP / "fixture-tiles", online=False))
    pan = QTimer()
    pan_state = {"k": 0}

    def pan_step():
        # a drag across the map, 6 px a step, turning every 2 s, a zoom level up or down every 3 s
        k = pan_state["k"] = pan_state["k"] + 1
        angle = (k // 120) * 2.1
        win.map.canvas.pan_px(6 * math.cos(angle), 6 * math.sin(angle))
        if k % 180 == 0:
            canvas = win.map.canvas
            canvas.zoom_by(1 if (k // 180) % 4 < 2 else -1)
    pan.timeout.connect(pan_step)

    def setup(name):
        orbit.stop()
        pan.stop()
        if win.replay.playing:
            win.replay.toggle_play()
        session.mapper.set_sweeping(False)
        if name.startswith("sensors"):
            win.show_screen(mw.SENSORS)
        elif name == "drive":
            win.show_screen(mw.DRIVE)
        elif name.startswith("map3d"):
            win.show_screen(mw.MAP3D)
            if name.endswith("orbit"):
                orbit.start(16)
        elif name == "replay-live":
            win.show_screen(mw.REPLAY)
            win.replay.go_live()
        elif name == "replay-play":
            win.show_screen(mw.REPLAY)
            win.replay.show_view(backends.demo_stall_view())
            win.replay.block.set_cursor(-30.0)
            win.replay.toggle_play()
        elif name == "rawtools-sweep":
            win.show_screen(mw.RAW)
            session.mapper.set_sweeping(True)
        elif name == "tune":
            win.show_screen(mw.TUNE)
        elif name == "alarms":
            win.show_screen(mw.ALARMS)
        elif name.startswith("map-"):
            if win.replay.view is None or getattr(win.replay.view, "path", None) != hour:
                win.replay.open(hour)
            win.show_screen(mw.MAP)
            win.map.set_mode("drive")
            win.map.set_colour("speed")
            win.map.fit()
            if name == "map-pan":
                pan.start(16)
            else:
                win.replay.block.set_cursor(win.replay.block.span[0] + 1300.0)
                win.replay.toggle_play()

    results = {}
    names = args.only or SCENARIOS
    print(f"hondalog UI bench: {app.platformName()} platform, {args.theme} theme, "
          f"3D = {'OpenGL (' + gl_reason + ')' if gl_ok else '2D fallback (' + gl_reason + ')'}, "
          f"{args.seconds:g} s per screen, poll {session.ecu_status.rate_hz:.1f} Hz")
    print(f"{'screen':<16}{'fps':>6}{'min':>6}{'frame p50':>11}{'p95':>7}{'max':>7}"
          f"{'cpu %':>7}{'lat p50':>9}{'p95':>6}{'max':>6}")
    for name in names:
        setup(name)
        run(0.5)
        win.perf.reset()
        run(args.seconds)
        s = win.perf.summary()
        if name.startswith("map3d"):
            s["scene"] = win.map3d.scene.stats()
        elif name.startswith("map-"):
            s["map"] = win.map.stats()
        elif name == "tune":
            s["scene"] = win.tune.scene.stats()
        results[name] = s
        f, lat = s["frame_ms"], s["latency_ms"]
        print(f"{name:<16}{s['fps']:>6.1f}{s['fps_min']:>6.1f}{f['p50']:>11.2f}{f['p95']:>7.2f}"
              f"{f['max']:>7.1f}{s['cpu_pct']:>7.1f}{lat['p50']:>9.1f}{lat['p95']:>6.1f}"
              f"{lat['max']:>6.1f}")
    orbit.stop()
    poll = session.ecu_status
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    scene = results.get("map3d-orbit", results.get("map3d", {})).get("scene")
    print(f"poll rate {poll.rate_hz:.1f} Hz, {poll.errors} link errors, peak RSS {rss:.0f} MB, "
          f"{len(win.sensors.tiles)} tiles")
    for name in ("map-pan", "map-play"):
        m = results.get(name, {}).get("map")
        if m:
            print(f"{name}: {m['track_points']} track points, {m['points']} kept at zoom {m['zoom']} "
                  f"({m['runs']} runs in the track image, last drawn in {m['render_ms']} ms in slices, "
                  f"{m['renders']} images), {m['tiles']} tiles; map paint p50 {m['paint_p50']} ms, "
                  f"p95 {m['paint_p95']} ms, max {m['paint_max']} ms over {m['paints']} paints")
    if scene:
        print(f"3D scene: {scene['draw_calls']} draw calls ({scene['geometry']} geometry + "
              f"{scene['labels']} labels), {scene['triangles']} triangles, "
              f"paintGL {scene['frame_ms']:.2f} ms, MSAA {scene['msaa']}x"
              if scene["gl"] else "3D scene: 2D fallback in use, GL path not measured")

    missed = []
    for name, s in results.items():
        if s["fps"] < TARGETS["fps"]:
            missed.append(f"{name}: {s['fps']} fps")
        if s["fps_min"] < TARGETS["fps_min"]:
            missed.append(f"{name}: slowest 1% of frames at {s['fps_min']} fps")
        if s["cpu_pct"] > TARGETS["cpu_pct"]:
            missed.append(f"{name}: {s['cpu_pct']}% CPU")
        elif name.startswith("sensors") and s["cpu_pct"] > TARGETS["sensors_cpu_pct"]:
            missed.append(f"{name}: {s['cpu_pct']}% CPU (all sensors: {TARGETS['sensors_cpu_pct']:g}%)")
        if s["latency_ms"]["p95"] > TARGETS["latency_p95"]:
            missed.append(f"{name}: latency p95 {s['latency_ms']['p95']} ms")
    print("targets (30 fps, never below 20, under 50% of a core and 20% on all sensors, under 100 ms): "
          + ("all met" if not missed else "MISSED: " + "; ".join(missed)))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"platform": app.platformName(), "gl": gl_ok, "gl_reason": gl_reason,
                       "seconds": args.seconds, "poll_hz": poll.rate_hz, "rss_mb": rss,
                       "results": results}, fh, indent=2)
    win.close()
    return 1 if (missed and args.check) else 0


if __name__ == "__main__":
    sys.exit(main())
