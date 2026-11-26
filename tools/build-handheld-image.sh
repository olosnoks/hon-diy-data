#!/usr/bin/env bash
# Build the handheld firmware from a checkout and package it for flashing.
#
#   tools/build-handheld-image.sh [REPO_DIR] [OUT_DIR] [--port COM22] [--no-tests] [--gps-p1]
#
#   REPO_DIR  checkout to build (default: the one this script is in)
#   OUT_DIR   where the images go (default: REPO_DIR/firmware-out)
#
# Uses a local ESP-IDF (HH_IDF_ENV, default ~/esp/hh-idf-env.sh: ESP-IDF 5.3.1 export.sh
# plus HH_LVGL_DIR and IDF_COMPONENT_MANAGER=0), not PlatformIO. Writes to OUT_DIR:
#   bootloader.bin, partition-table.bin, hondalog_handheld.bin (+ ota_data_initial.bin if
#   the partition table has one), hondalog-handheld-merged.bin (written at 0x0),
#   SHA256SUMS and FLASH.txt (the esptool commands).
# Host tests (scripts/build-firmware.sh handheld) run first unless --no-tests; a test
# failure is reported in FLASH.txt but does not stop the image being made.
set -uo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
REPO="" OUT="" PORT="COM22" TESTS=1 GPS_P1=0
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="${2:?--port needs a value}"; shift ;;
    --no-tests) TESTS=0 ;;
    --gps-p1) GPS_P1=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) if [ -z "$REPO" ]; then REPO="$1"; elif [ -z "$OUT" ]; then OUT="$1"; else echo "too many arguments" >&2; exit 2; fi ;;
  esac
  shift
done
REPO=$(cd "${REPO:-$HERE}" && pwd) || exit 2
OUT="${OUT:-$REPO/firmware-out}"
FW="$REPO/firmware/handheld"
ENV_SH="${HH_IDF_ENV:-$HOME/esp/hh-idf-env.sh}"
[ -f "$FW/CMakeLists.txt" ] || { echo "$FW/CMakeLists.txt: not a hondalog checkout" >&2; exit 2; }
[ -r "$ENV_SH" ] || { echo "$ENV_SH: no ESP-IDF env script (set HH_IDF_ENV)" >&2; exit 2; }
mkdir -p "$OUT" "$REPO/build-logs"
OUT=$(cd "$OUT" && pwd)

BDIR="$FW/build" extra=() variant=esp32s3
if [ $GPS_P1 -eq 1 ]; then
  BDIR="$FW/build-gps-p1" variant=esp32s3-gps-p1
  extra=(-DHH_GPS_P1=1 "-DSDKCONFIG=$BDIR/sdkconfig" "-DSDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.gps_p1")
fi

tests="not run (--no-tests)"
if [ $TESTS -eq 1 ]; then
  targs=(handheld); [ $GPS_P1 -eq 1 ] && targs+=(--gps-p1)
  if HH_IDF_ENV="$ENV_SH" QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" "$REPO/scripts/build-firmware.sh" "${targs[@]}"; then
    tests="passed (scripts/build-firmware.sh handheld)"
  else
    tests="FAILED, see $REPO/build-logs/ (image built anyway)"
  fi
fi

log="$REPO/build-logs/handheld-image-build.log"
echo "== building $variant in $BDIR"
start=$(date +%s)
if ! ( . "$ENV_SH" && lv=$HH_LVGL_DIR && idf.py -C "$FW" -B "$BDIR" "-DHH_LVGL_DIR=$lv" "${extra[@]}" build ) >"$log" 2>&1; then
  grep -nE "error:|Error|undefined reference" "$log" | head -20
  echo "build failed (log: $log)" >&2; exit 1
fi
echo "   built in $(( $(date +%s) - start ))s"

# flasher_args.json: flash settings and the offset of each file
eval "$(python3 - "$BDIR/flasher_args.json" <<'PY'
import json, sys, shlex
d = json.load(open(sys.argv[1]))
s = d["flash_settings"]
print(f"MODE={s['flash_mode']} FREQ={s['flash_freq']} SIZE={s['flash_size']}")
pairs = sorted(d["flash_files"].items(), key=lambda kv: int(kv[0], 16))
print("PAIRS=(" + " ".join(shlex.quote(f"{o}={f}") for o, f in pairs) + ")")
PY
)" || { echo "cannot read $BDIR/flasher_args.json" >&2; exit 1; }

rm -f "$OUT"/*.bin "$OUT/SHA256SUMS" "$OUT/FLASH.txt"
args=() split=""
for p in "${PAIRS[@]}"; do
  off=${p%%=*} f=${p#*=} name=$(basename "$f")
  cp "$BDIR/$f" "$OUT/$name"
  args+=("$off" "$OUT/$name")
  split+=" $off $name"
done
MERGED=hondalog-handheld-merged.bin
( . "$ENV_SH" && esptool.py --chip esp32s3 merge_bin -o "$OUT/$MERGED" \
    --flash_mode "$MODE" --flash_freq "$FREQ" --flash_size "$SIZE" "${args[@]}" ) >>"$log" 2>&1 \
  || { echo "merge_bin failed (log: $log)" >&2; exit 1; }
(cd "$OUT" && sha256sum *.bin > SHA256SUMS)

rev=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo "?")
branch=$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")
dirty=$(git -C "$REPO" status --porcelain -- firmware 2>/dev/null | grep -q . && echo " (firmware/ has uncommitted changes)")
idfv=$( ( . "$ENV_SH" && idf.py --version ) 2>/dev/null | tail -1)
cat > "$OUT/FLASH.txt" <<EOF
hondalog handheld firmware ($variant), Sunton ESP32-4827S043N (ESP32-S3 N16R8)
source: $REPO  $branch @ $rev$dirty
built:  $(date -u +%Y-%m-%dT%H:%MZ) with $idfv, LVGL 9.2.2
tests:  $tests
flash:  mode $MODE, freq $FREQ, size $SIZE

Merged image (bootloader + partition table + app), written at 0x0. On Windows, esptool 5:

  python -m esptool --chip esp32s3 --port $PORT --baud 460800 --before default-reset --after hard-reset write-flash --flash-mode keep --flash-freq keep --flash-size keep 0x0 $MERGED

or the laptop app's CLI (same thing):

  hondalog-handheld flash --port $PORT $MERGED

Separate files instead (same result):

  python -m esptool --chip esp32s3 --port $PORT --baud 460800 --before default-reset --after hard-reset write-flash --flash-mode $MODE --flash-freq $FREQ --flash-size $SIZE$split

esptool 4 spells it write_flash / --flash_mode / default_reset / hard_reset.
If it does not connect: hold BOOT, tap RESET (or plug in), release BOOT, run it again;
or try --baud 115200.

SHA-256:
$(sed 's/^/  /' "$OUT/SHA256SUMS")
EOF
echo "== $OUT"
ls -l "$OUT"
echo "tests: $tests"
