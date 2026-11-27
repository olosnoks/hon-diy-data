# PyInstaller spec: hondalog for Linux, one folder (dist/hondalog/).
# Mirrors packaging/windows/hondalog.spec.
#
#   packaging/linux/build-linux.sh      (sets up build/linuxvenv and runs this)
#
# The app (hondalog) and the command-line tools share one folder and one copy
# of Python, Qt and the data files.
# -*- mode: python -*-

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
# generated launchers go under build/ (git-ignored), not next to this spec
ENTRY = os.environ.get("HONDALOG_ENTRY_DIR") or os.path.join(ROOT, "build", "linux-entry")

# (program name, entry module:function, console)
PROGRAMS = [
    ("hondalog", "hondalog.__main__:main", False),
    ("hondalog-log", "hondalog.logtool:main", True),
    ("hondalog-handheld", "hondalog.handheld.cli:main", True),
    ("hondalog-firstcontact", "hondalog.diag.firstcontact:main", True),
    ("hondalog-probe", "hondalog.diag.ecuprobe:main", True),
    ("hondalog-map", "hondalog.mapping.__main__:main", True),
    ("hondalog-sim", "hondalog.sim:main", True),
    ("hondalog-tune", "hondalog.tuning.cli:main", True),
]

os.makedirs(ENTRY, exist_ok=True)
for name, target, _ in PROGRAMS:
    mod, func = target.split(":")
    with open(os.path.join(ENTRY, "run_" + name.replace("-", "_") + ".py"), "w", encoding="utf-8") as f:
        f.write(f"import sys\nfrom {mod} import {func}\nsys.exit({func}())\n")

datas = (
    collect_data_files("hondalog", includes=["data/*", "data/fonts/*", "tuning/data/*"])
    + collect_data_files("esptool")          # flasher stubs, for the Handheld tab's firmware install
    + collect_data_files("pyqtgraph")
)
hidden = collect_submodules("hondalog") + collect_submodules("esptool") + ["serial.tools.list_ports_linux"]

parts = []
for name, _, console in PROGRAMS:
    a = Analysis(
        [os.path.join(ENTRY, "run_" + name.replace("-", "_") + ".py")],
        pathex=[ROOT],
        datas=datas,
        hiddenimports=hidden,
        excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore"],
        noarchive=False,
    )
    pyz = PYZ(a.pure)
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=console,
              upx=False, debug=False, strip=False)
    parts += [exe, a.binaries, a.datas]

COLLECT(*parts, name="hondalog", upx=False, strip=False)
