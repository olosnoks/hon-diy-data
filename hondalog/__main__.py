"""hondalog entry point."""

from __future__ import annotations

import argparse
import signal
import sys

from . import __version__


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hondalog", description="Honda K-line data logger")
    ap.add_argument("--port", help="serial port to preselect, e.g. /dev/hondalog, "
                                   "/tmp/hondalog-sim or sim:")
    ap.add_argument("--connect", action="store_true", help="connect on start")
    ap.add_argument("--open", metavar="CSV", help="open a log in the replay tab")
    ap.add_argument("--version", action="version", version=f"hondalog {__version__}")
    args = ap.parse_args(argv)

    from . import ui  # noqa: F401  (selects PySide6 for pyqtgraph)
    from PySide6.QtWidgets import QApplication
    from .ui import theme
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("hondalog")
    app.setDesktopFileName("hondalog")
    theme.apply(app)
    signal.signal(signal.SIGINT, signal.SIG_DFL)      # Ctrl+C in the terminal quits

    win = MainWindow()
    screen = app.primaryScreen().availableGeometry()
    win.resize(min(1340, screen.width()), min(720, screen.height()))
    win.show()
    if args.port:
        win.port_combo.setEditText(args.port)
    if args.connect:
        win.connect_port(args.port)
    if args.open:
        win.open_in_replay(args.open)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
