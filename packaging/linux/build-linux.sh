#!/usr/bin/env bash
# Build hondalog (the PySide6 desktop app) for Linux x86-64: a folder with the
# app and the command-line tools, a tar.gz of it and, when appimagetool is on
# PATH, an AppImage. Linux or WSL. Mirrors packaging/windows/build-windows.ps1.
#
#   packaging/linux/build-linux.sh [--out DIR] [--version X.Y.Z] [--rev TEXT]
#                                  [--work DIR] [--clean] [--no-appimage]
#
#   --out DIR      copy the archives there too (build-all.ps1 passes release/<ver>)
#   --version V    version in the file names (default: the Python package's)
#   --rev TEXT     source revision for BUILD.txt (default: git rev-parse, if it works)
#   --work DIR     native build copy (default ~/hondalog-release/linux-src); a
#                  checkout on /mnt/c is copied there first (rsync), a native
#                  checkout builds in place
#   --clean        new venv and PyInstaller work folder
#   --no-appimage  only the tar.gz
#
# Output (in the build tree): dist/hondalog/ (run ./hondalog),
#   dist/hondalog-<ver>-linux-x64.tar.gz, dist/hondalog-<ver>-x86_64.AppImage
# Uses its own venv in build/linuxvenv (pip installs PySide6 and PyInstaller there).
# Never calls sudo: missing apt packages are listed for you to install.
#
# AppImage: needs appimagetool (https://github.com/AppImage/appimagetool/releases)
# on PATH or in $APPIMAGETOOL. Without FUSE (WSL) it runs with
# APPIMAGE_EXTRACT_AND_RUN=1.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SRC=$(cd "$HERE/../.." && pwd)
. "$SRC/scripts/release/wsl-sync.sh"

OUT="" VERSION="" REV="" WORK="$HOME/hondalog-release/linux-src" CLEAN=0 APPIMAGE=1
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="${2:?--out needs a folder}"; shift ;;
    --version) VERSION="${2:?}"; shift ;;
    --rev) REV="${2:?}"; shift ;;
    --work) WORK="${2:?}"; shift ;;
    --clean) CLEAN=1 ;;
    --no-appimage) APPIMAGE=0 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '\n== %s\n' "$*"; }
die() { printf 'linux build: %s\n' "$*" >&2; exit 1; }

say "prerequisites"
need=()      # without these the build cannot run
qt=()        # without these PyInstaller warns and the app will not start on this machine
command -v python3 >/dev/null || need+=(python3)
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null \
  || die "python3 is $(python3 -V 2>&1); hondalog needs 3.11 or newer"
python3 -c 'import ensurepip, venv' 2>/dev/null || need+=(python3-venv)
command -v objdump >/dev/null || need+=(binutils)        # PyInstaller reads ELF headers with it
on_windows_drive "$SRC" && ! command -v rsync >/dev/null && need+=(rsync)
have_pkg() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"; }
if command -v dpkg-query >/dev/null; then
  for p in libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
           libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
           libfontconfig1 libdbus-1-3; do
    have_pkg "$p" || qt+=("$p")
  done
fi
if [ ${#need[@]} -gt 0 ]; then
  echo "missing: ${need[*]}"
  echo "install once (owner, with sudo):  sudo apt-get install -y ${need[*]} ${qt[*]}"
  exit 2
fi
if [ ${#qt[@]} -gt 0 ]; then
  echo "   WARN Qt runtime libraries missing: ${qt[*]}"
  echo "        the build goes on (they are not bundled); to run or smoke-test the app here:"
  echo "        sudo apt-get install -y ${qt[*]}"
fi

B="$SRC"
if on_windows_drive "$SRC"; then
  say "copying the checkout to $WORK"
  [ -z "$REV" ] && REV=$(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || true)
  sync_tree "$SRC" "$WORK" --exclude '/firmware' || die "rsync failed"
  normalize_eol "$WORK" "$SRC"
  B="$WORK"
fi
cd "$B" || die "cannot cd to $B"
[ -z "$REV" ] && REV=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

VENV="$B/build/linuxvenv"
PY="$VENV/bin/python"
[ $CLEAN -eq 1 ] && rm -rf "$VENV" "$B/build/pyinstaller" "$B/build/linux-entry" "$B/dist/hondalog"
if [ ! -x "$PY" ]; then
  say "creating $VENV"
  python3 -m venv "$VENV" || die "venv failed"
fi
say "installing hondalog and PyInstaller"
"$PY" -m pip install -q --upgrade pip || die "pip upgrade failed"
"$PY" -m pip install -q ".[gl]" pyinstaller || die "pip install failed"

[ -z "$VERSION" ] && VERSION=$("$PY" -c "import importlib.metadata as m; print(m.version('hondalog'))")
ICON="$B/build/hondalog.png"
if [ ! -f "$ICON" ]; then
  say "making hondalog.png"
  QT_QPA_PLATFORM=offscreen "$PY" - "$B/hondalog/data/hondalog.svg" "$ICON" <<'PY' || echo "   WARN no icon (Qt could not render the SVG)"
import sys
from PySide6.QtGui import QGuiApplication, QIcon
app = QGuiApplication(sys.argv[:1])
QIcon(sys.argv[1]).pixmap(256, 256).toImage().save(sys.argv[2], "PNG")
PY
fi

say "PyInstaller"
"$PY" -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller \
  packaging/linux/hondalog.spec || die "PyInstaller failed"
APP="$B/dist/hondalog"
[ -x "$APP/hondalog" ] || die "dist/hondalog/hondalog was not made"

echo "hondalog $VERSION ($REV), built $(date '+%Y-%m-%d %H:%M') on $(. /etc/os-release && echo "$PRETTY_NAME")" > "$APP/BUILD.txt"
cp packaging/99-hondalog.rules "$APP/" 2>/dev/null
[ -f "$ICON" ] && cp "$ICON" "$APP/hondalog.png"
sed -e 's|^Exec=.*|Exec=hondalog|' packaging/hondalog.desktop > "$APP/hondalog.desktop"
cat > "$APP/INSTALL.txt" <<'EOF'
hondalog for Linux x86-64

Run:  ./hondalog          (the command-line tools are next to it: hondalog-log, hondalog-handheld, ...)

Serial port access (the KKL cable and the CYD over USB) needs your user in the
dialout group, once:   sudo usermod -aG dialout $USER   (then log out and in)
Optional udev rule for the cable:  sudo cp 99-hondalog.rules /etc/udev/rules.d/

Menu entry: edit Exec= in hondalog.desktop to the full path of ./hondalog and copy it
to ~/.local/share/applications/ (and hondalog.png to ~/.local/share/icons/).

Qt needs these on a minimal system (Debian/Ubuntu names):
  libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0
  libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libfontconfig1 libdbus-1-3
EOF

TAR="hondalog-$VERSION-linux-x64.tar.gz"
say "packing $TAR"
rm -f "dist/$TAR"
tar -C dist -czf "dist/$TAR" hondalog || die "tar failed"
made=("dist/$TAR")

if [ $APPIMAGE -eq 1 ]; then
  TOOL="${APPIMAGETOOL:-$(command -v appimagetool || command -v appimagetool-x86_64.AppImage || true)}"
  if [ -z "$TOOL" ]; then
    echo "   AppImage skipped: appimagetool not found (put it on PATH or set APPIMAGETOOL)"
  else
    AI="hondalog-$VERSION-x86_64.AppImage"
    say "AppImage $AI"
    AD="$B/build/AppDir"
    rm -rf "$AD" && mkdir -p "$AD/usr/lib"
    cp -a "$APP" "$AD/usr/lib/hondalog"
    cp "$APP/hondalog.desktop" "$AD/hondalog.desktop"
    if [ -f "$ICON" ]; then cp "$ICON" "$AD/hondalog.png"; else : > "$AD/hondalog.png"; fi
    cat > "$AD/AppRun" <<'EOF'
#!/bin/sh
HERE=$(dirname "$(readlink -f "$0")")
exec "$HERE/usr/lib/hondalog/hondalog" "$@"
EOF
    chmod +x "$AD/AppRun"
    rm -f "dist/$AI"
    fuse=0; [ -e /dev/fuse ] && { ldconfig -p 2>/dev/null | grep -q libfuse.so.2; } && fuse=1
    if ( [ $fuse -eq 1 ] || export APPIMAGE_EXTRACT_AND_RUN=1; ARCH=x86_64 "$TOOL" "$AD" "dist/$AI" ) >"$B/build/appimage.log" 2>&1; then
      made+=("dist/$AI")
    else
      echo "   WARN appimagetool failed (log: $B/build/appimage.log); the tar.gz is still there"
    fi
  fi
fi

if [ -n "$OUT" ]; then
  mkdir -p "$OUT" || die "cannot make $OUT"
  cp "${made[@]}" "$OUT/" || die "copy to $OUT failed"
fi
say "done"
for f in "${made[@]}"; do echo "   $B/$f  ($(du -h "$f" | cut -f1))"; done
[ -n "$OUT" ] && echo "   copied to $OUT"
exit 0
