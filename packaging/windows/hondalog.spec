# PyInstaller spec: hondalog for Windows, one folder (dist\hondalog\).
#
#   packaging\windows\build-windows.ps1      (sets up build\winvenv and runs this)
#
# The app (hondalog.exe, no console window) and the command-line tools share
# one folder and one copy of Python, Qt and the data files.
# -*- mode: python -*-

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
ENTRY = os.path.join(SPECPATH, "entry")

# (exe name, entry module:function, console window)
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
hidden = collect_submodules("hondalog") + collect_submodules("esptool") + ["serial.tools.list_ports_windows"]
icon = os.path.join(SPECPATH, "hondalog.ico")
icon = icon if os.path.exists(icon) else None

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
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=console, icon=icon,
              upx=False, debug=False)
    parts += [exe, a.binaries, a.datas]

COLLECT(*parts, name="hondalog", upx=False)
