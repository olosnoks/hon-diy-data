"""hondalog entry point."""

from __future__ import annotations

import argparse
import json
import signal
import sys

from . import __version__


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hondalog", description="Honda K-line data logger")
    ap.add_argument("--port", help="serial port, e.g. /dev/hondalog, /tmp/hondalog-sim or sim:")
    ap.add_argument("--connect", action="store_true", help="connect on start")
    ap.add_argument("--open", metavar="DRIVE", help="open a drive or capture by name (see hondalog-log "
                                                    "list), a log file (.csv .msl .mlg) or a black box "
                                                    "capture (.bbx) in the replay screen")
    ap.add_argument("--bb", metavar="PORT", help="black box port (default: /dev/hondalog-bb if "
                                                 "present; the simulator next to a simulated ECU)")
    ap.add_argument("--rom", metavar="BIN", help="ROM image to edit on the Tune screen")
    ap.add_argument("--definition", metavar="FILE",
                    help="ROM definition for --rom (.toml or TunerPro .xdf)")
    ap.add_argument("--demo", action="store_true",
                    help="simulated car and black box, no cable needed (nothing is recorded)")
    ap.add_argument("--theme", choices=("daylight", "night"), help="start in this theme")
    ap.add_argument("--profile", action="store_true",
                    help="show frame time, CPU and latency in the bottom bar and print a "
                         "summary on exit (run this on the T430)")
    ap.add_argument("--no-gl", action="store_true",
                    help="never create an OpenGL view: Map 3D and Tune use the 2D heat map")
    ap.add_argument("--version", action="version", version=f"hondalog {__version__}")
    args = ap.parse_args(argv)

    if args.no_gl:
        import os
        os.environ["HONDALOG_GL"] = "0"
    from . import ui  # noqa: F401  (selects PySide6 for pyqtgraph)
    from PySide6.QtWidgets import QApplication
    from .ui import scene3d, theme
    from .ui.main_window import Shell
    from .ui.session import build_session

    scene3d.default_surface_format()        # must precede QApplication
    app = QApplication(sys.argv[:1])
    app.setApplicationName("hondalog")
    app.setDesktopFileName("hondalog")
    app.setStyle("Fusion")
    theme.apply(app)
    signal.signal(signal.SIGINT, signal.SIG_DFL)      # Ctrl+C in the terminal quits

    session = build_session(demo=args.demo, rom=args.rom, definition=args.definition,
                            blackbox_port=args.bb)
    problem = getattr(session.tables, "problem", "")
    if problem:
        print(f"hondalog: {problem}; showing the sample tables", file=sys.stderr)
    win = Shell(session, profile=args.profile)
    if args.theme:
        theme.T.set(args.theme)
    screen = app.primaryScreen().availableGeometry()
    win.resize(min(1366, screen.width()), min(768, screen.height()))
    win.show()
    if args.demo:
        for src in (session.ecu, session.blackbox):
            src.prefill(session.bus)
    elif args.port and args.connect:
        win.connect_port(args.port)
    elif args.port:
        win.settings.setValue("port/last", args.port)
    if args.open:
        win.open_in_replay(args.open)
    code = app.exec()
    if win.perf is not None:
        summary = win.perf.summary()
        summary["scene"] = win.map3d.scene.stats()
        print(json.dumps(summary, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
