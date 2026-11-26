#!/usr/bin/env bash
# Build and test the ESP32 firmware locally (Linux or WSL). No GitHub Actions needed.
#
#   scripts/build-firmware.sh                 # host tests + build every firmware
#   scripts/build-firmware.sh blackbox        # just one (blackbox | handheld)
#   scripts/build-firmware.sh handheld --flash /dev/ttyUSB0   # build, then flash
#   scripts/build-firmware.sh handheld --gps-p1   # GPS on header P1 (next to USB-C), console off
#   scripts/build-firmware.sh --clean         # wipe build dirs first
#
# First run installs PlatformIO into ~/.hondalog-pio and downloads the ESP32-S3
# toolchain and ESP-IDF 5.3 (~1.5 GB, several minutes). Later runs are fast.
#
# With an ESP-IDF already on this machine, point HH_IDF_ENV at the script that
# sets it up and PlatformIO is not used (nor installed):
#
#   HH_IDF_ENV=~/esp/esp-idf/export.sh scripts/build-firmware.sh blackbox
#
# Each firmware is then built with ". $HH_IDF_ENV && idf.py -B build build".
# If that script sets HH_LVGL_DIR (a checkout of LVGL), the handheld build and
# its simulator use it instead of fetching LVGL.
#
# The handheld's PC simulator checks (firmware/handheld/sim: logic, contrast,
# layout) run as part of its host tests when that folder is in the checkout.
# Writes a log for each step to build-logs/; paste the failing one if something breaks.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VENV="$HOME/.hondalog-pio"
LOGS="$ROOT/build-logs"
TARGETS=()
FLASH_PORT=""
CLEAN=0
GPS_P1=0

while [ $# -gt 0 ]; do
  case "$1" in
    blackbox|handheld) TARGETS+=("$1") ;;
    --flash) FLASH_PORT="${2:?--flash needs a port, e.g. /dev/ttyUSB0}"; shift ;;
    --clean) CLEAN=1 ;;
    --gps-p1) GPS_P1=1 ;;   # handheld: GPS on header P1 (UART0) instead of P4; serial console off
    -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[ ${#TARGETS[@]} -eq 0 ] && TARGETS=(blackbox handheld)
mkdir -p "$LOGS"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '   \033[32mOK\033[0m  %s\n' "$*"; }
bad()  { printf '   \033[31mFAIL\033[0m %s  (log: %s)\n' "$1" "$2"; FAILED+=("$1"); }
FAILED=()

IDF_ENV="${HH_IDF_ENV:-}"
if [ -n "$IDF_ENV" ] && [ ! -r "$IDF_ENV" ]; then
  echo "HH_IDF_ENV=$IDF_ENV: no such file (it must be the script that sets up ESP-IDF)" >&2
  exit 2
fi
want() { local t; for t in "${TARGETS[@]}"; do [ "$t" = "$1" ] && return 0; done; return 1; }
SIM="$ROOT/firmware/handheld/sim"

say "Prerequisites"
missing=()
packages=()
for p in python3 git make gcc; do command -v "$p" >/dev/null || missing+=("$p"); done
[ ${#missing[@]} -gt 0 ] && packages+=(python3 python3-pip git make gcc build-essential)
if [ -z "$IDF_ENV" ] && ! python3 -c 'import venv' 2>/dev/null; then
  missing+=(python3-venv); packages+=(python3-venv)
fi
# the handheld simulator writes PNGs: it links libpng
if want handheld && [ -f "$SIM/Makefile" ] && ! echo '#include <png.h>' | ${CC:-cc} -E -x c - >/dev/null 2>&1; then
  missing+=(libpng-dev); packages+=(libpng-dev)
fi
if [ ${#missing[@]} -gt 0 ]; then
  echo "   installing: ${missing[*]} (needs sudo)"
  sudo apt-get update -qq && sudo apt-get install -y -qq "${packages[@]}" \
    || { echo "could not install ${missing[*]}" >&2; exit 1; }
fi
if [ -n "$IDF_ENV" ]; then
  # a local ESP-IDF: no PlatformIO. The env script is sourced in a subshell for each idf.py call.
  PY=python3
  idf() { ( . "$IDF_ENV" >/dev/null && idf.py "$@" ); }
  version=$( ( . "$IDF_ENV" >/dev/null && idf.py --version ) 2>/dev/null | tail -1)
  [ -n "$version" ] || { echo "idf.py does not run after '. $IDF_ENV'" >&2; exit 1; }
  ok "$version from $IDF_ENV"
  LVGL=$( ( . "$IDF_ENV" >/dev/null && printf '%s' "${HH_LVGL_DIR:-}" ) 2>/dev/null)
else
  if [ ! -x "$VENV/bin/pio" ]; then
    echo "   creating $VENV and installing PlatformIO"
    python3 -m venv "$VENV" && "$VENV/bin/pip" install -q --upgrade pip platformio pytest numpy pyserial \
      || { echo "PlatformIO install failed" >&2; exit 1; }
  fi
  PY="$VENV/bin/python"
  PIO="$VENV/bin/pio"
  ok "PlatformIO $("$PIO" --version | awk '{print $NF}')"
  LVGL="${HH_LVGL_DIR:-}"
fi

for t in "${TARGETS[@]}"; do
  dir="$ROOT/firmware/$t"
  project="platformio.ini"
  [ -n "$IDF_ENV" ] && project="CMakeLists.txt"
  if [ ! -f "$dir/$project" ]; then
    say "$t: skipped (firmware/$t/$project not in this checkout)"
    continue
  fi

  say "$t: host tests"
  if [ -f "$dir/test/host/Makefile" ]; then
    log="$LOGS/$t-host-c.log"
    if make -C "$dir/test/host" >"$log" 2>&1; then ok "C core tests"; else bad "$t C core tests" "$log"; fi
  fi
  case "$t" in
    blackbox) pat="tests/test_bb_*.py" ;;
    handheld) pat="tests/test_hh_*.py tests/test_handheld*.py" ;;
  esac
  pytests=$(cd "$ROOT" && ls $pat 2>/dev/null)
  if [ -n "$pytests" ]; then
    log="$LOGS/$t-pytest.log"
    if ! "$PY" -c 'import pytest' 2>/dev/null; then
      echo "   Python tests not run: $PY has no pytest (pip install pytest numpy pyserial)"
    elif (cd "$ROOT" && "$PY" -m pytest -q $pytests) >"$log" 2>&1; then
      ok "Python tests ($(tail -1 "$log"))"
    else bad "$t Python tests" "$log"; fi
  fi
  if [ "$t" = handheld ]; then
    if [ -f "$SIM/Makefile" ]; then
      # the UI on the PC: LVGL into memory, then the logic, contrast and layout checks
      log="$LOGS/$t-sim.log"
      [ $CLEAN -eq 1 ] && make -C "$SIM" clean >/dev/null 2>&1
      # two make runs: the Makefile lists LVGL's sources when it starts, so a fresh
      # clone made by 'make lvgl test' in one run is not seen and the link fails
      if make -C "$SIM" ${LVGL:+LVGL_DIR="$LVGL"} lvgl >"$log" 2>&1 &&
         make -C "$SIM" ${LVGL:+LVGL_DIR="$LVGL"} test >>"$log" 2>&1; then
        ok "simulator checks ($(grep -v '^make' "$log" | tail -1))"
      else
        bad "$t simulator checks" "$log"
        grep -nE "error:|FAIL|fatal" "$log" | head -15 | sed 's/^/        /'
      fi
    else
      echo "   simulator checks skipped (firmware/handheld/sim not in this checkout)"
    fi
  fi

  say "$t: firmware build"
  log="$LOGS/$t-build.log"
  if [ -n "$IDF_ENV" ]; then
    bdir="$dir/build"
    extra=()
    [ "$t" = handheld ] && [ -n "$LVGL" ] && extra+=("-DHH_LVGL_DIR=$LVGL")
    if [ "$t" = handheld ] && [ $GPS_P1 -eq 1 ]; then
      bdir="$dir/build-gps-p1"
      extra+=("-DHH_GPS_P1=1" "-DSDKCONFIG=$bdir/sdkconfig" "-DSDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.gps_p1")
    fi
    [ $CLEAN -eq 1 ] && rm -rf "$bdir"
    if idf -C "$dir" -B "$bdir" "${extra[@]}" build >"$log" 2>&1; then
      bin=$(ls -S "$bdir"/*.bin 2>/dev/null | head -1)
      ok "built $(du -h "$bin" | cut -f1) -> ${bin#$ROOT/}"
      grep -E "binary size .* bytes" "$log" | tail -1 | sed 's/^/        /'
      if [ -n "$FLASH_PORT" ]; then
        log="$LOGS/$t-flash.log"
        if idf -C "$dir" -B "$bdir" -p "$FLASH_PORT" flash >"$log" 2>&1; then ok "flashed to $FLASH_PORT"
        else bad "$t flash" "$log"; fi
      fi
    else
      bad "$t build" "$log"
      grep -nE "error:|Error|undefined reference" "$log" | head -15 | sed 's/^/        /'
    fi
    continue
  fi
  [ $CLEAN -eq 1 ] && rm -rf "$dir/.pio"
  penv=()
  [ "$t" = handheld ] && [ $GPS_P1 -eq 1 ] && penv=(-e esp32s3-gps-p1)
  if "$PIO" run -d "$dir" "${penv[@]}" >"$log" 2>&1; then
    bin=$(ls "$dir"/.pio/build/*/firmware.bin 2>/dev/null | head -1)
    ok "built $(du -h "$bin" | cut -f1) -> ${bin#$ROOT/}"
    grep -E "^(RAM|Flash):" "$log" | sed 's/^/        /'
    if [ -n "$FLASH_PORT" ]; then
      log="$LOGS/$t-flash.log"
      if "$PIO" run -d "$dir" "${penv[@]}" -t upload --upload-port "$FLASH_PORT" >"$log" 2>&1; then ok "flashed to $FLASH_PORT"
      else bad "$t flash" "$log"; fi
    fi
  else
    bad "$t build" "$log"
    grep -nE "error:|Error|undefined reference" "$log" | head -15 | sed 's/^/        /'
  fi
done

say "Summary"
if [ ${#FAILED[@]} -eq 0 ]; then echo "   everything passed"; exit 0; fi
printf '   failed: %s\n' "${FAILED[@]}"
echo "   logs are in ${LOGS#$ROOT/}/"
exit 1
