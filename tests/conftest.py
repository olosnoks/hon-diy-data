"""Shared fixtures. The UI tests run offscreen; nothing touches the real
~/.config or ~/hondalog."""

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="hondalog-test-")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")
os.environ["HONDALOG_DIR"] = os.path.join(_TMP, "logs")
# the Map screen never reaches a real tile server from the tests, and caches nothing in ~/.cache
os.environ["HONDALOG_TILES"] = "offline"
os.environ["HONDALOG_TILE_CACHE"] = os.path.join(_TMP, "tiles")
# no handheld is looked for next to a laptop cable unless a test asks (another
# process's hondalog-cydsim pty must not be picked up)
os.environ["HONDALOG_HANDHELD"] = "off"

sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402

from hondalog.data import open_log  # noqa: E402
from hondalog.data.synth import write_synthetic  # noqa: E402


@pytest.fixture(scope="session")
def synth_path(tmp_path_factory):
    """A 10-minute multi-rate drive shared by the read-only tests (a DriveRef)."""
    return write_synthetic(tmp_path_factory.mktemp("synth") / "synth.db", duration_s=600)


@pytest.fixture()
def synth(synth_path):
    with open_log(synth_path) as log:
        yield log


@pytest.fixture(autouse=True)
def _base_units():
    """Display units are one process-wide object (like the theme): a test
    that starts the app sets them to the user's. Put them back."""
    yield
    module = sys.modules.get("hondalog.ui.units")
    if module is not None:
        module.U.set(None)


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
    from hondalog.ui import theme, units
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
    units.U.set(None)                       # back to "as the address map has them"


def spin(app, win, frames=2):
    for _ in range(frames):
        app.processEvents()
        win.frame()
    app.processEvents()
