"""hondalog entry point."""

from __future__ import annotations

import argparse
import json
import signal
import sys

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="hondalog", description="Honda K-line data logger")
    ap.add_argument("--port", help="serial port, e.g. /dev/hondalog, COM21, auto, cyd: (the handheld reads "
                                   "the ECU), /tmp/hondalog-sim or sim:. A plain cable name with --connect is "
                                   "the KKL cable to prefer: without it the CYD is used (see --auto)")
    ap.add_argument("--connect", action="store_true", help="connect on start")
    ap.add_argument("--auto", choices=("on", "off"), default="on",
                    help="pick the ECU source by itself, at start-up and on replug: the KKL cable "
                         "(FTDI 0403:6001), else the CYD (CH340 1a86:7523) as cyd:, else nothing; "
                         "a KKL cable that appears wins over the CYD. Never a simulator. Used without "
                         "--port, and with --port <cable> --connect or --port auto (default on)")
    ap.add_argument("--open", metavar="DRIVE", help="open a drive or capture by name (see hondalog-log "
                                                    "list), a log file (.csv .msl .mlg) or a black box "
                                                    "capture (.bbx) in the replay screen")
    ap.add_argument("--bb", metavar="PORT", help="the separate ESP32 black box: its port, and show it in "
                                                 "the app (hidden without --bb: the CYD is the black box)")
    ap.add_argument("--handheld", metavar="PORT|auto|off",
                    help="the handheld (CYD) on USB next to the laptop's own K-line cable: its GPS "
                         "beats the laptop's location. auto (default) finds a CH340 1a86:7523 "
                         "that is not the ECU's port")
    ap.add_argument("--relay", choices=("on", "off", "force"), default="on",
                    help="send the laptop's ECU reads to that handheld (its screen, alarms, stall "
                         "rules and SD log): on (if its firmware takes them and its map is yours), "
                         "off, force (skip those checks: testing)")
    ap.add_argument("--rom", metavar="BIN", help="ROM image to edit on the Tune screen")
    ap.add_argument("--definition", metavar="FILE",
                    help="ROM definition for --rom (.toml or TunerPro .xdf)")
    ap.add_argument("--demo", action="store_true",
                    help="simulated car and black box, no cable needed (nothing is recorded)")
    ap.add_argument("--theme", choices=("dracula", "darker", "daylight", "night"), metavar="dracula|darker",
                    help="start in this theme (F9 toggles; daylight / night are old names for them)")
    ap.add_argument("--profile", action="store_true",
                    help="show frame time, CPU and latency in the bottom bar and print a "
                         "summary on exit (run this on the T430)")
    ap.add_argument("--no-gl", action="store_true",
                    help="never create an OpenGL view: Map 3D and Tune use the 2D heat map")
    ap.add_argument("--version", action="version", version=f"hondalog {__version__}")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

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
                            blackbox_port=args.bb, handheld=args.handheld, relay=args.relay)
    problem = getattr(session.tables, "problem", "")
    if problem:
        print(f"hondalog: {problem}; showing the sample tables", file=sys.stderr)
    win = Shell(session, profile=args.profile, show_bb=args.bb is not None)
    if args.theme:
        theme.T.set(args.theme)
    screen = app.primaryScreen().availableGeometry()
    win.resize(min(1366, screen.width()), min(768, screen.height()))
    win.show()
    from .ui.autoconnect import is_explicit
    port = args.port or ""
    if args.demo:
        for src in (session.ecu, session.blackbox):
            src.prefill(session.bus)
    elif port and port != "auto" and (is_explicit(port) or args.auto == "off"):
        if args.connect:                # exactly this port (a simulator only ever this way)
            win.connect_port(port)
        else:
            win.settings.setValue("port/last", port)
    elif args.auto == "on" and (not port or port == "auto" or args.connect):
        win.start_auto(preferred="" if port == "auto" else port)
    elif port:
        win.settings.setValue("port/last", port)
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
