#!/usr/bin/env python3
"""Save every screen in both themes at exactly 1366x768 to docs/screenshots/.

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
    gl_ok, reason = scene3d.gl_status()
    print(f"{len(saved)} screenshots in {out}  (3D: {'OpenGL, ' + reason if gl_ok else '2D fallback, ' + reason})")
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots"))
