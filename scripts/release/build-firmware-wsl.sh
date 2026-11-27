#!/usr/bin/env bash
# Build the CYD black-box firmware (firmware/handheld) and merge it into one image
# that is written at 0x0. Runs in WSL or on Linux; build-all.ps1 calls it with
#   wsl -d Ubuntu -- bash /mnt/c/.../scripts/release/build-firmware-wsl.sh --out /mnt/c/.../release/<ver> --version <ver>
#
#   build-firmware-wsl.sh --out DIR --version X.Y.Z [--src DIR] [--work DIR]
#                         [--no-sim] [--strict-tests] [--clean]
#
#   --out DIR        where hondalog-handheld-<version>.bin and its flash notes go
#   --version V      version in the file name
#   --work DIR       native build copy (default ~/hondalog-release/fw-src); a
#                    checkout on /mnt/c is copied there first (rsync)
#   --no-sim         skip the PC simulator checks (they need libpng-dev and fetch LVGL);
#                    automatic when libpng-dev is missing
#   --strict-tests   fail when a host test fails (default: report it, keep the image)
#   --clean          rebuild from scratch (wipes .pio in the copy)
#   --rev TEXT       source revision written into FLASH-handheld.txt
#
# The build itself is scripts/build-firmware.sh handheld (PlatformIO in
# ~/.hondalog-pio, first run downloads ~1.5 GB). The merge uses esptool 5:
#   esptool --chip esp32s3 merge-bin --flash-mode keep --flash-freq keep --flash-size keep
#           0x0 bootloader.bin 0x8000 partitions.bin 0x10000 firmware.bin
# esptool 5 comes from ~/.hondalog-esptool (a venv this script makes with pip; no
# sudo) unless HONDALOG_ESPTOOL points at one.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
. "$HERE/wsl-sync.sh"

OUT="" VERSION="" SRC="$ROOT" WORK="$HOME/hondalog-release/fw-src" NOSIM=0 STRICT=0 CLEAN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="${2:?--out needs a folder}"; shift ;;
    --version) VERSION="${2:?--version needs a value}"; shift ;;
    --src) SRC="${2:?}"; shift ;;
    --work) WORK="${2:?}"; shift ;;
    --no-sim) NOSIM=1 ;;
    --strict-tests) STRICT=1 ;;
    --clean) CLEAN=1 ;;
    --rev) HONDALOG_REV="${2:?}"; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$OUT" ] && [ -n "$VERSION" ] || { echo "usage: $0 --out DIR --version X.Y.Z (see --help)" >&2; exit 2; }
mkdir -p "$OUT" && OUT=$(cd "$OUT" && pwd)

say() { printf '\n== %s\n' "$*"; }
die() { printf 'firmware: %s\n' "$*" >&2; exit 1; }

say "firmware: prerequisites"
missing=()
for p in python3 git make gcc rsync; do command -v "$p" >/dev/null || missing+=("$p"); done
python3 -c 'import ensurepip, venv' 2>/dev/null || missing+=(python3-venv)
if [ ${#missing[@]} -gt 0 ]; then
  echo "missing: ${missing[*]}"
  echo "the owner installs them once:  sudo apt-get install -y build-essential git rsync python3-venv"
  exit 2
fi
if [ $NOSIM -eq 0 ] && ! echo '#include <png.h>' | cc -E -x c - >/dev/null 2>&1; then
  echo "   libpng-dev not installed: simulator checks skipped (sudo apt-get install -y libpng-dev to run them)"
  NOSIM=1
fi

# build in a native copy: scripts/build-firmware.sh would otherwise try to apt-get libpng (sudo)
B="$SRC"
if on_windows_drive "$SRC" || [ "$WORK" != "$HOME/hondalog-release/fw-src" ]; then
  say "firmware: copying the checkout to $WORK"
  sync_tree "$SRC" "$WORK" || die "rsync failed"
  normalize_eol "$WORK" "$SRC"
  B="$WORK"
fi
if [ $NOSIM -eq 1 ]; then
  [ "$B" = "$SRC" ] && die "--no-sim needs a build copy (use --work DIR) so the checkout is not changed"
  rm -rf "$B/firmware/handheld/sim"
  echo "   simulator removed from the build copy"
fi
FW="$B/firmware/handheld"
[ -f "$FW/platformio.ini" ] || die "$FW/platformio.ini missing: not a hondalog checkout"
[ $CLEAN -eq 1 ] && rm -rf "$FW/.pio"

say "firmware: scripts/build-firmware.sh handheld"
marker=$(mktemp); trap 'rm -f "$marker"' EXIT
QT_QPA_PLATFORM=offscreen bash "$B/scripts/build-firmware.sh" handheld
fwstatus=$?
P="$FW/.pio/build/esp32s3"
for f in bootloader.bin partitions.bin firmware.bin; do
  [ -f "$P/$f" ] || die "$P/$f not built (logs: $B/build-logs/)"
done
blog="$B/build-logs/handheld-build.log"
[ "$blog" -nt "$marker" ] && grep -q '\[SUCCESS\]' "$blog" || die "the PlatformIO build did not succeed in this run (log: $blog)"
tests="passed"
if [ $fwstatus -ne 0 ]; then
  tests="FAILED (see $B/build-logs/; the image was built)"
  [ $STRICT -eq 1 ] && die "host tests failed and --strict-tests is set"
  echo "   WARN host tests failed; the image is still made (pass --strict-tests to stop here)"
fi

say "firmware: esptool 5"
ESPTOOL="${HONDALOG_ESPTOOL:-}"
if [ -z "$ESPTOOL" ]; then
  EV="$HOME/.hondalog-esptool"
  if [ ! -x "$EV/bin/esptool" ]; then
    echo "   making $EV (pip install 'esptool>=5')"
    python3 -m venv "$EV" && "$EV/bin/pip" install -q --upgrade pip 'esptool>=5' || die "could not install esptool 5 into $EV"
  fi
  ESPTOOL="$EV/bin/esptool"
fi
ever=$("$ESPTOOL" version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)
case "$ever" in
  ""|[0-4].*) die "$ESPTOOL is esptool ${ever:-?}; merge-bin with 'keep' needs esptool 5 (pip install 'esptool>=5')" ;;
esac
echo "   esptool $ever"

say "firmware: merge"
IMG="hondalog-handheld-$VERSION.bin"
"$ESPTOOL" --chip esp32s3 merge-bin -o "$OUT/$IMG" \
  --flash-mode keep --flash-freq keep --flash-size keep \
  0x0 "$P/bootloader.bin" 0x8000 "$P/partitions.bin" 0x10000 "$P/firmware.bin" || die "merge-bin failed"
size=$(stat -c %s "$OUT/$IMG")
echo "   $IMG  $size bytes"

cat > "$OUT/FLASH-handheld.txt" <<EOF
hondalog CYD black box firmware $VERSION (Sunton ESP32-4827S043N, ESP32-S3 N16R8)
source:  ${HONDALOG_REV:-unknown revision}, PlatformIO env esp32s3
built:   $(date -u +%Y-%m-%dT%H:%MZ)
tests:   $tests
image:   $IMG ($size bytes), bootloader 0x0 + partitions 0x8000 + app 0x10000, written at 0x0

Flash (esptool 5; COM22 on Windows, /dev/ttyUSB0 or /dev/ttyACM0 on Linux):

  python -m esptool --chip esp32s3 --port COM22 --baud 460800 --before default-reset --after hard-reset write-flash --flash-mode keep --flash-freq keep --flash-size keep 0x0 $IMG

or from the desktop app:  hondalog-handheld flash --port COM22 $IMG

If it does not connect: hold BOOT, tap RESET, release BOOT, run it again; or --baud 115200.
EOF
echo "   notes: FLASH-handheld.txt"
echo "firmware: done, tests $tests"
