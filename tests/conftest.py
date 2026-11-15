"""Shared fixtures. The UI tests run offscreen; nothing touches the real
~/.config or ~/hondalog."""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="hondalog-test-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")
os.environ["HONDALOG_DIR"] = os.path.join(_TMP, "logs")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def app():
    """One QApplication for the whole run (pytest-qt's qapp if it is there)."""
    from hondalog import ui  # noqa: F401
    from PySide6.QtWidgets import QApplication
    from hondalog.ui import scene3d, theme
    inst = QApplication.instance()
    if inst is None:
        scene3d.default_surface_format()
        inst = QApplication([])
    inst.setStyle("Fusion")
    theme.apply(inst)
    return inst


@pytest.fixture
def settings(tmp_path):
    from PySide6.QtCore import QSettings
    return QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat)


@pytest.fixture
def shell(app, settings):
    """The whole app on frozen demo data at 1366x768."""
    from hondalog.ui import theme
    from hondalog.ui.main_window import Shell
    from hondalog.ui.session import build_session
    session = build_session(demo=True, demo_clock=lambda: 100.0)
    win = Shell(session, settings)
    win.resize(1366, 768)
    win.show()
    for src in (session.ecu, session.blackbox):
        src.prefill(session.bus)
    spin(app, win, 3)
    yield win
    win.close()
    win.deleteLater()
    app.processEvents()
    theme.T.set("daylight")


def spin(app, win, frames=2):
    for _ in range(frames):
        app.processEvents()
        win.frame()
    app.processEvents()
