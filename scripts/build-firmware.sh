#!/usr/bin/env bash
# Build and test the ESP32 firmware locally (Linux or WSL). No GitHub Actions needed.
#
#   scripts/build-firmware.sh                 # host tests + build every firmware
#   scripts/build-firmware.sh blackbox        # just one (blackbox | handheld)
#   scripts/build-firmware.sh handheld --flash /dev/ttyUSB0   # build, then flash
#   scripts/build-firmware.sh --clean         # wipe build dirs first
#
# First run installs PlatformIO into ~/.hondalog-pio and downloads the ESP32-S3
# toolchain and ESP-IDF 5.3 (~1.5 GB, several minutes). Later runs are fast.
# Writes a log for each step to build-logs/; paste the failing one if something breaks.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VENV="$HOME/.hondalog-pio"
LOGS="$ROOT/build-logs"
TARGETS=()
FLASH_PORT=""
CLEAN=0

while [ $# -gt 0 ]; do
  case "$1" in
    blackbox|handheld) TARGETS+=("$1") ;;
    --flash) FLASH_PORT="${2:?--flash needs a port, e.g. /dev/ttyUSB0}"; shift ;;
    --clean) CLEAN=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
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

say "Prerequisites"
missing=()
for p in python3 git make gcc; do command -v "$p" >/dev/null || missing+=("$p"); done
python3 -c 'import venv' 2>/dev/null || missing+=(python3-venv)
if [ ${#missing[@]} -gt 0 ]; then
  echo "   installing: ${missing[*]} (needs sudo)"
  sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-venv python3-pip git make gcc build-essential
fi
if [ ! -x "$VENV/bin/pio" ]; then
  echo "   creating $VENV and installing PlatformIO"
  python3 -m venv "$VENV" && "$VENV/bin/pip" install -q --upgrade pip platformio pytest numpy pyserial \
    || { echo "PlatformIO install failed" >&2; exit 1; }
fi
PIO="$VENV/bin/pio"
ok "PlatformIO $("$PIO" --version | awk '{print $NF}')"

for t in "${TARGETS[@]}"; do
  dir="$ROOT/firmware/$t"
  if [ ! -f "$dir/platformio.ini" ]; then
    say "$t: skipped (firmware/$t/platformio.ini not in this checkout)"
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
    if (cd "$ROOT" && "$VENV/bin/python" -m pytest -q $pytests) >"$log" 2>&1; then
      ok "Python tests ($(tail -1 "$log"))"
    else bad "$t Python tests" "$log"; fi
  fi

  say "$t: firmware build"
  [ $CLEAN -eq 1 ] && rm -rf "$dir/.pio"
  log="$LOGS/$t-build.log"
  if "$PIO" run -d "$dir" >"$log" 2>&1; then
    bin=$(ls "$dir"/.pio/build/*/firmware.bin 2>/dev/null | head -1)
    ok "built $(du -h "$bin" | cut -f1) -> ${bin#$ROOT/}"
    grep -E "^(RAM|Flash):" "$log" | sed 's/^/        /'
    if [ -n "$FLASH_PORT" ]; then
      log="$LOGS/$t-flash.log"
      if "$PIO" run -d "$dir" -t upload --upload-port "$FLASH_PORT" >"$log" 2>&1; then ok "flashed to $FLASH_PORT"
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
