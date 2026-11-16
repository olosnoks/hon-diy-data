#!/usr/bin/env python3
"""Save every screen in both themes at exactly 1366x768 to docs/screenshots/,
then three states the plain screens do not show (replay-compare, rawtools-rank,
tune-rom).

Uses the demo data (the mockup's simulated car) frozen at one moment, so the
pictures are repeatable and can be compared with docs/mockups/.

    xvfb-run -a -s "-screen 0 1366x768x24" python3 tools/screenshots.py   # with OpenGL
    QT_QPA_PLATFORM=offscreen python3 tools/screenshots.py                # 2D fallback
"""

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="hondalog-shots-"))
os.environ["XDG_CONFIG_HOME"] = str(TMP / "config")
os.environ["HONDALOG_DIR"] = str(TMP / "logs")

from hondalog import ui  # noqa: E402,F401
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from hondalog.ui import scene3d, theme  # noqa: E402
from hondalog.ui.main_window import Shell  # noqa: E402
from hondalog.ui.session import build_session  # noqa: E402

NAMES = ("sensors", "drive", "map3d", "replay", "rawtools", "tune", "alarms")


def main(out: Path) -> int:
    scene3d.default_surface_format()
    app = QApplication([])
    app.setStyle("Fusion")
    theme.apply(app)
    clock = [100.0]
    session = build_session(demo=True, demo_clock=lambda: clock[0])
    win = Shell(session, QSettings(str(TMP / "ui.ini"), QSettings.IniFormat))
    win.resize(1366, 768)
    win.show()
    for src in (session.ecu, session.blackbox):
        src.prefill(session.bus)
    session.recording._t0 -= 30.0                       # "REC 00:30", buffer full
    session.mark()
    session.mark()
    session.recording._last -= 18.0
    session.ecu.mark_t = session.blackbox.mark_t = -1e9

    def settle(frames=5):
        for _ in range(frames):
            app.processEvents()
            time.sleep(0.03)
            win.frame()
        app.processEvents()

    settle()
    mapper = session.mapper                              # some change heat for the hex grid
    mapper.sweeping = True
    for k in range(8):
        mapper.fill(60.0 + k * 0.9)
    mapper.revision += 1
    win.raw.grid.select(0x14)

    out.mkdir(parents=True, exist_ok=True)
    saved = []

    def shot(name):
        settle()
        image = win.grab()
        assert (image.width(), image.height()) == (1366, 768), image.size()
        path = out / f"{name}.png"
        image.save(str(path))
        saved.append(path.name)

    for name in ("daylight", "night"):
        theme.T.set(name)
        for i, screen in enumerate(NAMES):
            win.show_screen(i)
            shot(f"{screen}-{name}")
            if screen == "map3d":
                for mode in ("hist", "stall"):
                    win.map3d.view_seg.set_current(mode)
                    win.map3d._set_mode(mode)
                    shot(f"map3d-{mode}-{name}")
                win.map3d.view_seg.set_current("surface")
                win.map3d._set_mode("surface")
    extras(win, session, shot, settle, clock)
    gl_ok, reason = scene3d.gl_status()
    print(f"{len(saved)} screenshots in {out}  (3D: {'OpenGL, ' + reason if gl_ok else '2D fallback, ' + reason})")
    win.close()
    return 0


def extras(win, session, shot, settle, clock) -> None:
    """States the seven plain screens do not show: two cursors and a second
    drive in Replay, the Rank addresses panel, Tune with a ROM open."""
    import numpy as np

    from hondalog.tuning.demo import build_demo_rom
    from hondalog.ui import backends, wired

    # Replay: cursor B dropped at the first loss, A on the stall, an earlier stall laid over in grey
    replay = win.replay
    other = backends.demo_stall_view()
    other.title = "stall_20261109_081455"
    for cid in other.data:                          # the same failure, 2.5 s later
        if cid != "fuel_pump":
            other.data[cid] = np.roll(other.data[cid], 25)
    win.show_screen(3)
    replay.set_overlay(other)
    replay.block.set_cursor(replay.forensics.loss[0].t)
    replay.toggle_cursor_b()
    replay.block.set_cursor(0.0)

    # Raw tools: what followed the throttle in the last 40 sweeps
    mapper = session.mapper
    for k in range(40):
        mapper.fill(70.0 + k * 0.9)
    mapper.revision += 1
    win.raw.show_rank(True)
    win.raw.rank.set_reference("tps")
    win.raw.rank.rank()

    # Tune: the demo ROM opened from inside the app, one cell edited, the engine moving
    tables = wired.RomTableModel(backends.DemoTableModel(writable=False), store_dir=TMP / "roms")
    rom = TMP / "p28_demo.bin"
    rom.write_bytes(build_demo_rom())
    session.tables = win.tune.tm = tables
    win.tune.open_rom(rom)
    win.show_screen(5)
    for _ in range(45):                             # 9 s of driving: a trail on the table and the surface
        clock[0] += 0.2
        settle(1)
    win.tune.grid.cur = win.tune.grid.anchor = (3, 4)
    win.tune.bump(3)

    for name in ("daylight", "night"):
        theme.T.set(name)
        for index, screen in ((3, "replay-compare"), (4, "rawtools-rank"), (5, "tune-rom")):
            win.show_screen(index)
            shot(f"{screen}-{name}")


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"))
