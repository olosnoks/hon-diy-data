# Firmware development

Two firmwares, both for the ESP32-S3, both built with ESP-IDF 5.3:

| Firmware | Folder | Board | Does |
|---|---|---|---|
| Black box | `firmware/blackbox/` | ESP32-S3 dev board (N16R8 or N8R8) | 1 kHz recorder for the stall hunt |
| Handheld | `firmware/handheld/` | Sunton ESP32-4827S043N ("CYD") | Live viewer and logger |

Everything builds and tests **on your own machine** with one script. No
hosted CI is involved; see
[Development](development.md#github-actions-are-manual-only) for why.

**Status.** Both firmwares compile with ESP-IDF 5.3.1 through
`HH_IDF_ENV` (the handheld in both variants, with a local LVGL), and both
cores pass their host tests. The PlatformIO path has no recorded complete
build: its first run needs a download that was not possible where this was
written (**[unverified]**). Neither firmware has a record of being run
through its bring-up list on a board.

- [The one script](#the-one-script)
- [PlatformIO or plain ESP-IDF](#platformio-or-plain-esp-idf)
- [The LVGL path](#the-lvgl-path)
- [Host C tests](#host-c-tests)
- [The handheld PC simulator](#the-handheld-pc-simulator)
- [Building by hand](#building-by-hand)
- [Repository layout](#repository-layout)
- [Porting notes](#porting-notes)

## The one script

```sh
scripts/build-firmware.sh                              # host tests, then build both firmwares
scripts/build-firmware.sh blackbox                     # just one: blackbox or handheld
scripts/build-firmware.sh handheld --flash /dev/ttyUSB0    # build, then flash
scripts/build-firmware.sh handheld --gps-p1            # the handheld with the GPS on header P1
scripts/build-firmware.sh --clean                      # wipe the build folders first
```

Runs on Linux and under WSL. For each firmware it does, in order:

1. **Prerequisites.** Installs what is missing with `sudo apt-get`. If any
   of `python3`, `git`, `make` or `gcc` is missing it installs `python3
   python3-pip git make gcc build-essential`; it adds `python3-venv` when
   PlatformIO is to be used and Python has no `venv`, and `libpng-dev` for
   the handheld simulator.
2. **Host C tests**: `make -C firmware/<name>/test/host`.
3. **Python tests** that load the C core: `tests/test_bb_*.py` for the black
   box, `tests/test_hh_*.py` (and any `tests/test_handheld*.py`) for the
   handheld. They run with the PlatformIO environment's Python (which the
   script gives pytest, numpy and pyserial) or, with `HH_IDF_ENV`, with
   `python3`; if that Python has no pytest the step is skipped with a note.
4. **Simulator checks** (handheld only): `make -C firmware/handheld/sim lvgl test`.
5. **The firmware build.**
6. **Flash**, if `--flash PORT` was given.

It ends with a summary and exits 1 if anything failed. Each step writes a
log to `build-logs/` (ignored by git): `blackbox-host-c.log`,
`blackbox-pytest.log`, `blackbox-build.log`, `handheld-sim.log` and so on.
When something breaks, the failing step's log is the thing to read.

| Option | Does |
|---|---|
| `blackbox`, `handheld` | Which firmware. Neither: both |
| `--flash PORT` | Flash after a successful build |
| `--gps-p1` | Handheld only: GPS on UART0 (header P1), serial console off |
| `--clean` | Remove the build folders first (`.pio/`, or `build/` / `build-gps-p1/` with `HH_IDF_ENV`, and the simulator's `build/`) |
| `-h`, `--help` | The usage text |

Ports: the black box flashes through its dev board's serial or USB socket
(`/dev/ttyACM0` or `/dev/ttyUSB0`); the CYD through its USB-C socket, a
CH340, `/dev/ttyUSB0`.

## PlatformIO or plain ESP-IDF

The script can build either way.

### PlatformIO (the default)

With `HH_IDF_ENV` unset, the first run creates a Python virtual environment
in `~/.hondalog-pio`, installs PlatformIO into it, and PlatformIO downloads
the ESP32-S3 toolchain and ESP-IDF 5.3: about 1.5 GB and several minutes,
once. It needs internet for that.

| | |
|---|---|
| Build command | `pio run -d firmware/<name>` (the `esp32s3` environment; `-e esp32s3-gps-p1` with `--gps-p1`) |
| Output | `firmware/<name>/.pio/build/esp32s3/firmware.bin`, `bootloader.bin`, `partitions.bin` |
| Handheld environments | `esp32s3` (default), `esp32s3-gps-p1`, `esp32s3-perf` (frame-rate overlay) |
| Platform | `espressif32@6.9.0` (handheld, pinned), `espressif32@^6.9.0` (black box) |

### An ESP-IDF you already have

Point `HH_IDF_ENV` at the script that sets ESP-IDF up, and PlatformIO is
neither used nor installed:

```sh
HH_IDF_ENV=~/esp/esp-idf/export.sh scripts/build-firmware.sh handheld
```

Each firmware is then built with `. $HH_IDF_ENV && idf.py -B build build`
in its folder.

| | |
|---|---|
| Output | `firmware/<name>/build/hondalog_blackbox.bin` or `hondalog_handheld.bin`, `build/bootloader/bootloader.bin`, `build/partition_table/partition-table.bin` |
| `--gps-p1` | Builds into `firmware/handheld/build-gps-p1/` with its own `sdkconfig`, so the two variants do not overwrite each other |
| `--flash PORT` | `idf.py -p PORT flash`, in the same build folder |

`HH_IDF_ENV` must name a readable file; the script stops with a message if
`idf.py` does not run after sourcing it.

## The LVGL path

The handheld's UI uses LVGL 9.2.2. Where it comes from depends on the build:

| Build | LVGL comes from |
|---|---|
| PlatformIO, or ESP-IDF with the component manager | Fetched by the component manager: `lvgl/lvgl == 9.2.2` in `firmware/handheld/main/idf_component.yml` |
| ESP-IDF, offline | A local checkout, named by `HH_LVGL_DIR` |
| The PC simulator | `firmware/handheld/sim/.lvgl/` (fetched by `make lvgl`), or `LVGL_DIR=...` |

For an offline build, clone LVGL once and export its path **from the same
script `HH_IDF_ENV` points at**:

```sh
git clone --depth 1 -b v9.2.2 https://github.com/lvgl/lvgl ~/esp/lvgl

cat > ~/esp/hondalog-env.sh <<'END'
. ~/esp/esp-idf/export.sh
export HH_LVGL_DIR=~/esp/lvgl
END

HH_IDF_ENV=~/esp/hondalog-env.sh scripts/build-firmware.sh handheld
```

The build script reads `HH_LVGL_DIR` after sourcing that file and passes it
both to the firmware build (`-DHH_LVGL_DIR=...`) and to the simulator
(`LVGL_DIR=...`). Without `HH_IDF_ENV` it takes `HH_LVGL_DIR` from the
environment for the simulator only.

The USB host drivers the handheld needs (`usb_host_cdc_acm` 2.4.1,
`usb_host_ftdi_vcp` 2.1.1) are copied into
`firmware/handheld/components/`, so they need no download. What was changed
against upstream is in
[README-vendored.md](../firmware/handheld/components/README-vendored.md).

## Host C tests

The logic of each firmware is a portable C core with no ESP-IDF in it. It
compiles for the PC and is tested there.

| Core | Source | Host build | What tests it |
|---|---|---|---|
| `bbcore` (black box) | `firmware/blackbox/components/bbcore/` | `make -C firmware/blackbox/test/host` builds `build/libbbhost.so` | `tests/test_bb_core.py`, `tests/test_bb_host.py` load it with ctypes and compare it with the Python model: triggers, capture files, the USB protocol, clock sync |
| `hhcore` (handheld) | `firmware/handheld/components/hhcore/` | `make -C firmware/handheld/test/host` builds `build/libhhhost.so` and runs the C unit tests (`build/hhtest`) | The C tests, plus `tests/test_hh_core.py`, `test_hh_map.py`, `test_hh_import.py` |
| `hhmap` (handheld road map) | `firmware/handheld/components/hhmap/` | the same `make` builds and runs `build/hmtest` | `hmtest.c` reads `test/host/fixtures/tinymap`, a MAP the Python builder made from `tests/fixtures/osmmap/tiny.osm`, and checks it against the builder's own `expect.txt`; then damage, the cache, rendering. `tests/test_hh_osmmap.py` checks the fixture is current (`HH_UPDATE_FIXTURES=1` rewrites it) |

```sh
make -C firmware/blackbox/test/host
make -C firmware/handheld/test/host          # builds, then runs the C unit tests
python3 -m pytest tests/test_bb_core.py tests/test_bb_host.py tests/test_hh_core.py tests/test_hh_map.py tests/test_hh_import.py \
    tests/test_hh_osmmap.py
```

Both build with `-Wall -Wextra -Werror`. The handheld core is also built
with `-ffp-contract=off`: its read planner and scheduler are compared with
the Python ones bit for bit, and a fused multiply-add would round
differently.

`hhcore` is a port of the laptop's link code: the same framing, planner,
scheduler, expression evaluation (as compiled bytecode), alarm state
machine and stall rules. The Python tests feed both the same inputs and
require the same outputs. When you change one side, the tests tell you to
change the other.

Without `gcc` and `make` the Python tests that need a C library are skipped.

## The handheld PC simulator

`firmware/handheld/sim/` builds the handheld's real UI sources
(`firmware/handheld/main/ui/`) for the PC, with LVGL drawing into memory
and a stub supplying fake data. There is no window: it runs checks and
writes PNG files.

```sh
cd firmware/handheld/sim
make lvgl             # fetch LVGL v9.2.2 into .lvgl/ (once), or pass LVGL_DIR=/path
make test             # logic, contrast and layout checks
make screenshots      # every screen in both themes -> ../docs/screenshots/
make budget           # pixels redrawn per frame, per screen
make testmap          # rebuild testmap/MAP from the OSM extracts in OSM_DIR (default ~/osm)
make clean
```

Needs `cc`, `make` and `libpng-dev`.

The Map screen reads `testmap/MAP`, a committed 12-mile map of Jackson
(1.4 MB, map data © OpenStreetMap contributors, ODbL), and the stub drives
real routes on it (`hh_stub_set_route`: State Street downtown, I-55). The
map worker runs in place in the simulator. `HH_SIM_MAP=/path/to/MAP
build/hhsim test` runs the checks against another map, such as a full
100-mile build.

| Target | Checks or makes |
|---|---|
| `test` | The UI's logic, text contrast in both themes (and 3:1 for the map's water, roads over it, rail and trail), layout, the Map screen (zoom by speed and by button, pins, trail, banner, no map, Night) |
| `screenshots` | The PNGs in [`firmware/handheld/docs/screenshots/`](../firmware/handheld/docs/screenshots/): each screen, each alarm state, the hold gestures, the stall screens, day and night |
| `budget` | How many pixels each screen redraws per frame, the number that decides the frame rate on the board; for the Map screen also how often a new picture arrives and the worker's time per picture |

The screenshots in [Handheld](handheld.md) come from `make screenshots`.
Run it again after changing the UI and commit the new pictures.

Fonts are pre-generated C files in `firmware/handheld/main/ui/fonts/`.
`firmware/handheld/tools/gen_fonts.py` regenerates them from Inter (it needs
fontTools, node and `lv_font_conv`); you only need it to change sizes or
glyphs.

To measure on the board itself, build the `esp32s3-perf` environment
(`-DHH_UI_PERF=1`): it overlays frame rate, render time and CPU.

## Building by hand

```sh
# PlatformIO
pip install platformio
cd firmware/blackbox && pio run -t upload
cd firmware/handheld && pio run -e esp32s3-gps-p1 -t upload --upload-port /dev/ttyUSB0

# ESP-IDF 5.3
. ~/esp/esp-idf/export.sh
cd firmware/handheld
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor

# ESP-IDF, GPS on P1
idf.py -B build-gps-p1 -DHH_GPS_P1=1 -DSDKCONFIG=build-gps-p1/sdkconfig \
       -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.gps_p1" build

# ESP-IDF, offline LVGL
IDF_COMPONENT_MANAGER=0 idf.py -DHH_LVGL_DIR=/path/to/lvgl build
```

Compile-time switches for the handheld:

| Define | Does |
|---|---|
| `HH_GPS_P1=1` | GPS on UART0 RX (GPIO44, header P1). Use with `sdkconfig.gps_p1`, which turns the console off |
| `HH_LCD_SWAP_RB=1` | Exchange the red and blue pin groups |
| `HH_UI_PERF=1` | Performance overlay |
| `HH_UI_AUTOCYCLE_S=N` | The sensor pages turn by themselves after N seconds without a press (default 0: never) |

Pin numbers are one `#define` each: `firmware/handheld/include/board_pins.h`
for the handheld, the table at the top of
`firmware/blackbox/main/hal/hal_esp32s3.c` for the black box.

The black box on an N8R8 module: set the flash size to 8MB in
`platformio.ini` and in `sdkconfig.defaults`.

## Repository layout

```
hon-diy-data/
├── README.md, CHANGELOG.md
├── install.sh, uninstall.sh       install the laptop app into /opt/hondalog
├── pyproject.toml
├── bin/                           launchers for the nine commands
├── hondalog/                      the Python package (map: docs/ARCHITECTURE.md)
│   ├── ui/                        the Qt app; nothing outside ui/ imports Qt
│   ├── data/                      the database, and the shipped addressmap.toml
│   ├── blackbox/                  laptop side of the black box
│   ├── handheld/                  laptop side of the handheld
│   └── tuning/                    ROM tools
├── tests/                         pytest; also drives the C cores
├── tools/                         smoke test, UI benchmark, screenshots, link benchmark
├── bench/                         data-layer benchmarks
├── scripts/build-firmware.sh      build and test both firmwares
├── packaging/                     udev rule, desktop launcher
├── docs/                          this documentation
│   ├── screenshots/               the app (tools/screenshots.py, tools/smoke.py)
│   ├── mockups/                   the design it was built from
│   ├── design/, research/         the UI specification and the feature survey
└── firmware/
    ├── blackbox/
    │   ├── HARDWARE.md, PROTOCOL.md, FORMAT.md
    │   ├── components/bbcore/     portable C core
    │   ├── main/                  ESP-IDF application; main/hal/ is the only chip-specific code
    │   └── test/host/             host build of the core
    └── handheld/
        ├── HARDWARE.md, FORMAT.md, SERIAL.md, MAPFORMAT.md
        ├── include/board_pins.h   every pin
        ├── components/hhcore/     portable C core (port/ holds the ESP-IDF glue)
        ├── components/hhmap/      the road map: reader, tile cache, renderer (portable C)
        ├── components/hhcore_stub/   fake data for UI work and the simulator
        ├── components/usb_host_*/ vendored USB host drivers
        ├── main/                  application; main/ui/ is the portable UI, main/port/ the board
        ├── sim/                   PC simulator; sim/testmap/ its 12-mile map
        ├── test/host/             host build and C unit tests of the core
        ├── tools/gen_fonts.py
        └── docs/screenshots/      simulator output
```

## Porting notes

- **Black box to another chip.** Only `main/hal/hal_esp32s3.c` knows the
  ESP32-S3. A port implements `main/hal/bb_hal.h`. The hardware page
  [sketches an RP2040 / Pico 2 port](../firmware/blackbox/HARDWARE.md#porting-rp2040--pico-2).
- **Handheld UI to another panel.** `main/ui/` is portable and calls three
  functions the port provides (`ui_port_theme_changed`, `ui_port_micros`,
  `ui_port_cpu`). The layout is fixed at 480x272.
- **Handheld K-line back ends.** USB host with the KKL cable
  (`port/hh_kline_usb.c`) and a UART with an L9637D (`port/hh_kline_uart.c`)
  sit behind one small interface; `backend` in `HANDHELD.CFG` picks.
