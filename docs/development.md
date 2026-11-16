# Development

For anyone changing hondalog's code: how to run the tests and tools, where
things live, how to add a channel, a math channel or an alarm, and the
conventions the code keeps.

Firmware has its own page: [Firmware development](firmware-dev.md).

- [Setup](#setup)
- [Running the tests](#running-the-tests)
- [Tools](#tools)
- [mypy scope](#mypy-scope)
- [Architecture pointers](#architecture-pointers)
- [Adding a channel, a math channel, an alarm](#adding-a-channel-a-math-channel-an-alarm)
- [Coding conventions](#coding-conventions)
- [Documentation conventions](#documentation-conventions)
- [GitHub Actions are manual-only](#github-actions-are-manual-only)

## Setup

Work from a clone; there is nothing to build for the Python side.

```sh
git clone https://github.com/olosnoks/hon-diy-data
cd hon-diy-data
sudo apt install python3-numpy python3-serial python3-pyqtgraph \
     python3-pyside6.qtcore python3-pyside6.qtgui python3-pyside6.qtwidgets \
     python3-pyside6.qtopenglwidgets python3-pyside6.qtopengl python3-opengl \
     python3-pytest gcc make
```

(or `pip install PySide6 pyqtgraph pyserial numpy PyOpenGL pytest` in a
virtual environment; Python 3.11 or newer).

Run the commands from the clone with `bin/hondalog`, `bin/hondalog-log` and
so on: each launcher runs the package next to it, not the installed copy in
`/opt/hondalog`. `./install.sh` copies the clone over the installed one.

The tests and tools never touch your real data: they set `HONDALOG_DIR` and
`XDG_CONFIG_HOME` to temporary folders. Do the same when experimenting by
hand:

```sh
export HONDALOG_DIR=/tmp/hl XDG_CONFIG_HOME=/tmp/hl/config
bin/hondalog-log record --port sim: --bb bbsim: --seconds 20
```

## Running the tests

```sh
QT_QPA_PLATFORM=offscreen python3 -m pytest -q   # everything: 996 tests, about two minutes
python3 -m pytest -q tests/test_protocol.py tests/test_planner.py
python3 -m pytest -q -k stall
```

- **Offscreen.** `tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen` if it
  is not set, so the UI tests need no display. To see a test's window, set
  `QT_QPA_PLATFORM=xcb` yourself.
- **The C cores.** `tests/test_bb_*.py` and `tests/test_hh_*.py` build the
  firmware cores with `make` and `gcc` and load them. Without a compiler
  those tests are skipped.
- **Skips.** On the last full offscreen run (2026-11-16): 994 passed, 2
  skipped. One is the OpenGL scene test; others depend on what is installed
  (`sqlite3` shell, `pytest-qt`, a C compiler).
- **OpenGL.** The scene test needs a real OpenGL context and is skipped
  offscreen. To include it:
  `xvfb-run -a -s "-screen 0 1366x768x24" python3 -m pytest tests/test_ui_scene.py`.
- Nothing needs the car, the cable, or the network.

What covers what:

| Tests | Cover |
|---|---|
| `test_protocol`, `test_link_sim`, `test_link_robust`, `test_planner`, `test_sim_pty`, `test_obd2`, `test_firstcontact` | Framing, the link against the simulator at true byte timing, faults and recovery, the read planner, ISO 9141-2, first contact |
| `test_addressmap*`, `test_expr`, `test_mathch`, `test_alarms`, `test_units`, `test_mapping_*`, `test_mapedit` | The map, the expression engine, math channels, alarms, verification and the finder |
| `test_database`, `test_recorder`, `test_triggers`, `test_analysis`, `test_export`, `test_live_cli` | The database (crash recovery, migrations, views through plain `sqlite3`), recording, triggers, analysis, exports, `hondalog-log` |
| `test_bb_core`, `test_bb_host` | The black box C core against its Python model; the reader, alignment, verdicts |
| `test_hh_core`, `test_hh_map`, `test_hh_import` | The handheld C core against the Python it was ported from; map export; importing drives |
| `test_tuning_*` | Definitions, XDF, ROM images, the table editor, diff, auto-tune, burner, Ostrich simulator, `hondalog-tune` |
| `test_ui_*` | Theme, bus, units, the shell and every screen, the wired backends |
| `test_integration` | The cross-package path: simulator, link, pipeline, recorder, capture, reopen, redecode, histogram, MLG |
| `test_perf_budget`, `test_bench_expr` | CPU budgets |

Before committing: the whole suite, then the smoke test.

## Tools

All run from the repository root.

### `tools/smoke.py`

```sh
QT_QPA_PLATFORM=offscreen python3 tools/smoke.py
```

The end-to-end run against `hondalog-sim` on a real pseudo-terminal:

1. No window: link, pipeline and recorder produce a drive with raw bytes
   and math channels, and a stall capture (RPM to 0) that plain `sqlite3`
   can see.
2. The same with the ECU going silent (a power cut).
3. The window, offscreen at 1366x768, against a second simulator run: a
   mark, a memory sweep, the stall banner, the capture in Replay.

It also **rewrites `docs/screenshots/sim-*.png`**. Check `git status`
afterwards and commit or restore them.

### `tools/bench_ui.py`

```sh
QT_QPA_PLATFORM=offscreen python3 tools/bench_ui.py              # no display
xvfb-run -a -s "-screen 0 1366x768x24" python3 tools/bench_ui.py   # with (software) OpenGL
python3 tools/bench_ui.py --onscreen --seconds 20                  # on the real display
```

Runs the real app against `sim:` and measures, per screen: frame rate,
frame time, CPU as a share of one core, and latency from the last serial
byte to the painted pixel.

| Option | Does |
|---|---|
| `--seconds N` | Per scenario (default 8) |
| `--onscreen` | Use the real display |
| `--theme daylight` or `night` | |
| `--only NAME ...` | Just these scenarios: `sensors`, `drive`, `map3d`, `map3d-orbit`, `replay-live`, `replay-play`, `rawtools-sweep`, `tune`, `alarms`, `sensors-again` |
| `--json FILE` | Also write the results as JSON |
| `--check` | Exit 1 if a target is missed |
| `--no-blackbox` | Leave the simulated black box out |

Targets and the last measured numbers: [UI performance](ui-performance.md).
Numbers from a desktop or server CPU say little about the T430; the last
form above, or `hondalog --profile`, is the one that counts.

### `tools/screenshots.py`

```sh
xvfb-run -a -s "-screen 0 1366x768x24" python3 tools/screenshots.py   # with OpenGL
QT_QPA_PLATFORM=offscreen python3 tools/screenshots.py                # 2D fallback
```

Saves every screen in both themes at exactly 1366x768 to
`docs/screenshots/`, then three states the plain screens do not show
(`replay-compare`, `rawtools-rank`, `tune-rom`). It uses the demo data
frozen at one moment, so the pictures repeat exactly and can be compared
with `docs/mockups/`. Run it after changing how a screen looks, under
`xvfb-run` so that Map 3D and Tune are drawn in 3D, and commit the result.

### Others

| Tool | Does |
|---|---|
| `python3 tools/bench_link.py` | Per-channel read rate against the simulated ECU at true 9600 baud, the 0.1.0 strategy against the current planner and scheduler. `--realtime`, `--turnaround-ms`, `--usb-latency-ms`, `--faults flaky`, `--json` |
| `python3 bench/bench_data.py` | Database write, open and histogram timings, and size per hour. `--hours N` |
| `python3 bench/bench_layout.py` | The comparison of storage layouts quoted in the architecture page |
| `python3 -m hondalog.bench.expr_bench` | CPU cost of math channels and alarms against the budget. `--json` |
| `tools/mockup-render/shoot.py` | Photographs the design mockup pages (`*.dc.html`, made by `docs/design/mockup-src/build.py`) at 1366x768 into PNGs like those in `docs/mockups/`. Needs Playwright and Chrome; reads `pages/` and writes `out/` in the folder it runs from |

## mypy scope

mypy is run on the packages that are kept clean, not on everything:

```sh
python3 -m mypy --ignore-missing-imports --follow-imports=silent \
  hondalog/link hondalog/diag hondalog/expr hondalog/mathch hondalog/alarms \
  hondalog/mapping hondalog/data hondalog/analysis hondalog/export hondalog/triggers \
  hondalog/tuning hondalog/addressmap.py hondalog/protocol.py hondalog/transport.py \
  hondalog/poller.py hondalog/obd2.py hondalog/sim.py hondalog/live.py \
  hondalog/logger.py hondalog/logtool.py hondalog/units.py
```

**Not in scope yet:** `hondalog/ui/`, `hondalog/blackbox/` and
`hondalog/handheld/`. New code in the packages above should stay clean;
moving a package into the list is welcome.

## Architecture pointers

Read [Architecture](ARCHITECTURE.md) first. The short version:

| If you are changing | Start in | And read |
|---|---|---|
| The wire: framing, timing, recovery | `hondalog/protocol.py`, `transport.py`, `link/`, `poller.py` | Architecture, "The live path" |
| What a byte means | `hondalog/addressmap.py`, `hondalog/data/addressmap.toml` | [Mapping the P2P](mapping-the-p2p.md) |
| Expressions | `hondalog/expr/` (parser, compiler, `funcs.py`) | One engine for formulas, math, alarms, filters, triggers |
| What is recorded, and when a drive starts and ends | `hondalog/live.py`, `logger.py`, `triggers/` | Architecture, "Rules that hold along it" |
| The database | `hondalog/data/schema.py` (migrations, views), `store.py`, `db.py` | Architecture, "The database" |
| Replay, compare, histograms | `hondalog/analysis/` | |
| A screen | `hondalog/ui/screens/`, and the adapter it talks to in `ui/adapters.py` | [UI adapters](ui-adapters.md), [Design handoff](design/HANDOFF.md) |
| What the UI is bound to | `hondalog/ui/wired.py` (real), `ui/backends.py` (demo and shared) | [UI adapters](ui-adapters.md) |
| The black box | `firmware/blackbox/components/bbcore/` and its Python mirror `hondalog/blackbox/` | [PROTOCOL.md](../firmware/blackbox/PROTOCOL.md), [FORMAT.md](../firmware/blackbox/FORMAT.md) |
| The handheld | `firmware/handheld/components/hhcore/`, `hondalog/handheld/` | [FORMAT.md](../firmware/handheld/FORMAT.md) |

Three rules that hold everywhere:

1. **Nothing outside `hondalog/ui/` imports Qt.** Everything else runs
   headless and is tested that way.
2. **There is one live path**, `hondalog.live.LiveSession`. The app,
   `hondalog-log record` and the smoke test all use it. Do not build a
   second.
3. **The UI talks to the rest only through the Protocol classes in
   `ui/adapters.py`**, and sources publish into the `ChannelBus` from their
   own threads; one 33 ms timer reads it. No per-sample Qt signals.

In the source and in older documents, `W1` to `W6` are the six workstreams
the code was built in: W1 link, W2 black box, W3 mapping and expressions,
W4 data, W5 tuning, W6 UI.

## Adding a channel, a math channel, an alarm

All three are data, not code: entries in the address map. Full field
reference: [Mapping the P2P](mapping-the-p2p.md#the-map-file-schema-2).

### A channel

In `hondalog/data/addressmap.toml` (the shipped default) or your own copy:

```toml
[[channel]]
name = "fan"
label = "Rad fan"
addr = 0x09
length = 1
formula = "bit(x, 4)"
kind = "flag"
status = "unverified"
rate = "slow"
source = "found with the finder, 2026-11-20"
```

Then:

1. Bump the map's `version`.
2. A new channel is always `status = "unverified"`. It becomes verified on
   the car, never in a commit.
3. The read planner picks the new address up by itself. Check what it did
   to the rates: `python3 tools/bench_link.py`.
4. If the shipped map changed, run the suite: several tests pin the shipped
   map's channel list and its hash, and the handheld's built-in copy
   (`firmware/handheld/components/hhcore/port/hh_default_map.inc`) is
   generated from it with
   `bin/hondalog-handheld export-map --c-array firmware/handheld/components/hhcore/port/hh_default_map.inc`.
5. No UI code changes: the Sensors screen builds its tiles from the map.
   (`fan` and `fuel_pump` are the two names the placeholder tiles wait for.)

### A math channel

```toml
[[math]]
name = "vacuum"
label = "Manifold vacuum"
expr = "[baro] - [map]"
units = "kPa"
min = 0
max = 100
decimals = 0
```

Check its cost if it uses history functions:
`python3 -m hondalog.bench.expr_bench`. If it must also run on the handheld,
it cannot use the functions over time; `hondalog-handheld export-map` lists
what was left out.

### An alarm

```toml
[[alarm]]
name = "iat_high"
label = "Intake air hot"
channel = "iat"
high = 60
hold_s = 5
hysteresis = 3
severity = "warn"
actions = ["tile", "banner"]
message = "Intake air {value:.0f} °C"
```

Limits are in the map's units.

### A new expression function

This one is code: add it to `PURE` or `STATEFUL` in
`hondalog/expr/funcs.py` with a scalar and a numpy implementation, add tests
in `tests/test_expr.py`, and document it in
[the expression reference](mapping-the-p2p.md#expression-reference). If the
handheld should have it too, it also needs an opcode in
`hondalog/handheld/bytecode.py` and `firmware/handheld/components/hhcore/hh_expr.c`.

### A database change

Add a `Migration` to `MIGRATIONS` in `hondalog/data/schema.py`. Never edit
an existing migration: files in the field have already run it.
`tests/test_database.py` opens old-version files.

## Coding conventions

Taken from how the code is written today.

- **Python 3.11+**, `from __future__ import annotations`, type hints on
  public functions. Lines up to about 110 characters.
- **Standard library first.** The runtime dependencies are PySide6,
  pyqtgraph, pyserial and numpy, all packaged in Debian trixie. Adding one
  means it has to exist as a Debian package too.
- **Module docstrings say what the module is and what it is based on**,
  including what was *not* verified. Keep that honest when you change
  behaviour.
- **Comments say why**, not what.
- **No `eval`, no `exec`.** Anything a user can type (formulas, filters,
  trigger conditions) goes through `hondalog.expr`.
- **The link is read-only by default.** Anything that could send a
  non-read frame goes through the guard in `transport.Link`.
- **Threads.** The poller owns the port; the store owns the disk; the UI
  thread touches neither.
- **Units** are metric in the map, the database and every export. Only
  `hondalog/ui/units.py` converts, for display.
- **Simulators are part of the product.** A feature that cannot be
  exercised against `sim:` or `bbsim:` cannot be tested here; extend the
  simulator with it.
- **Tests go with the change**, and run without hardware.
- **C** (the firmware cores): C11, `-Wall -Wextra -Werror`, no ESP-IDF
  headers in the portable cores, and behaviour identical to the Python
  mirror, which the tests check.

## Documentation conventions

- Say what was verified and on what. Mark the rest `[unverified]`.
- A command, flag, path, key or number in the docs must match the code.
  Check commands against `--help`.
- Relative links. After editing, check that links and image paths resolve.
- Screenshots come from `tools/screenshots.py`, `tools/smoke.py` and the
  handheld simulator. Do not hand-edit them.
- User-visible changes go in [CHANGELOG.md](../CHANGELOG.md).

## GitHub Actions are manual-only

The repository has two workflows, `.github/workflows/tests.yml` and
`blackbox.yml`. Both are triggered **only by hand** (`workflow_dispatch`).
Nothing runs on push or on a pull request. That is a deliberate choice:

- **Runs cost Actions minutes**, and the account's allowance is limited. A
  push-triggered suite plus a firmware build burns it quickly.
- **Everything they do can be done locally**, faster, with the logs on your
  own disk.
- **Local is the truth anyway.** Nothing here is verified by a hosted
  runner: the things that matter are the simulators on your machine and,
  eventually, the car.

So the rule for contributors is: **build and test locally; do not add push
or pull-request triggers, and do not rely on a hosted run.**

| The workflow would run | Do this instead |
|---|---|
| The whole test suite | `python3 -m pytest -q` |
| The smoke test | `QT_QPA_PLATFORM=offscreen python3 tools/smoke.py` |
| mypy | the command under [mypy scope](#mypy-scope) |
| Black box host tests and firmware build | `scripts/build-firmware.sh blackbox` |
| (no workflow builds the handheld) | `scripts/build-firmware.sh handheld` |

The workflow files stay in the tree as a record of the exact commands. They
can still be started from the repository's Actions tab if someone chooses
to spend the minutes.
