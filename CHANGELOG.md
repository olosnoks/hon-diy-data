# Changelog

All notable changes, grouped by area. Dates are commit dates. The version
number in `pyproject.toml` is still 0.1.0; everything after the first
commit is listed under "Unreleased".

Almost nothing here has been run on the car yet. "Tested" below means
against the simulators and the host builds of the firmware cores.

## Unreleased

### Link and protocol (Track A)

- Read planner: merges nearby addresses into the fewest requests using a
  measured cost model, with fast / medium / slow rate classes (targets 20,
  5 and 1 reads per second) and a priority scheduler. About 17 Hz on the
  fast channels against 11.4 Hz in 0.1.0, at true 9600 baud byte timing.
- Robust exchanges: adaptive reply and inter-byte timeouts, one retry after
  draining the line, per-read timestamps corrected for data age, counters
  for timeouts, checksum failures, echo mismatches, resyncs and retries.
- Recovery from key off and on, or a pulled cable, within about 2 to 3
  seconds, without a restart.
- Read-only guard: only read requests and the wake-up bytes go out unless
  the expert unlock is on; frames sent that way are recorded.
- OBD2 over ISO 9141-2: 5-baud init with KWP2000 fast init as a fallback,
  supported PIDs, live PIDs, stored and pending codes with Honda P1xxx text,
  freeze frame, readiness, and clear codes (typed confirmation, engine off).
- `hondalog-firstcontact`: a read-only first run on the car that checks the
  adapter, the echo, the Honda read with and without wake-up bytes, other
  framings, the ECU id, block length, reply time, a RAM sweep and ISO
  9141-2, and writes a report and a byte trace. `--udev` prints the udev
  rule.
- Simulator: true byte timing, line faults (`--faults`, `flaky`), cable
  unplug, a dead cable, ISO-only ECU, repeatable runs (`--seed`), and the
  in-process ports `sim:` and `sim:flaky`.
- `tools/bench_link.py`: per-channel read rates before and after.

### Address map, mapping and expressions

- Address map schema 2: ECU variants, per-channel verification status and
  evidence, rate classes, math channels and alarms. `hondalog-map upgrade`
  converts a 0.1.0 map in place and keeps its comments.
- One safe expression engine (parsed, never `eval`) for formulas, math
  channels, alarms, filters and triggers, with functions over time
  (`avg`, `slope`, `minw`, `maxw`, `smooth` and more) and a unit system.
- Math channels (injector duty, fuel flow, total trim, RPM rate, load, an
  AFR estimate, filtered values) and alarms (coolant, battery low and high,
  over-rev, idle collapse, lean under load), with an alarm state machine.
- `hondalog-map`: guided verification scripts (`koeo`, `tps_sweep`, `idle`,
  `steady_3000`, `warmup`, `drive`, `ac_switch`) with pass / plausible /
  fail / skip verdicts and saved evidence; the correlation finder; the ISO
  PID cross-check; `status`, `diff`, `hash`.

### Data

- Every drive goes into one crash-safe SQLite database,
  `~/hondalog/hondalog.db`, committed every half second, with the raw bytes
  of every K-line read. A short-lived `.hlog` file format came first and was
  replaced by the database.
- A view per drive with a column per channel, and the views
  `drive_summary`, `stalls`, `readings`, `blackbox_1s`, `ecu_reads` and
  `markers` for plain SQL.
- `hondalog-log`: `record`, `list`, `info`, `import`, `export`, `compare`,
  `report`, `redecode`, `prune`, `rm`, `keep`, `vacuum`, `sql`.
- Exports to MegaLogViewer MLG (v1 and v2) and MSL, CSV, NPZ and a database
  copy; imports of 0.1.0 CSV, MSL and MLG.
- Analysis: filters, table histograms (with bins from a ROM's own axes),
  scatter, cursors, compare two drives, plot decimation.
- A general trigger engine; the stall catcher is two of its rules.
- One live path from the link to the database (`LiveSession`), used by the
  app, `hondalog-log record` and the smoke test.
- Delete one drive or one capture (`hondalog-log rm`, Delete in the drive
  picker), and mark drives keep.
- With the black box connected, a drive stays open while the box sees the
  key on, so a stall with the ECU dead is recorded in one drive.

### The app

- A new UI designed at 1366x768 for the ThinkPad T430, replacing the 0.1.0
  window: Sensors, Drive, Map 3D, Replay, Raw tools, Tune and Alarms on F1
  to F7, Daylight and Night themes (F9), every action on a key, state shown
  by shape as well as colour.
- Display units, US by default, chosen under Settings > Units; storage and
  exports stay metric.
- Replay: drive picker with search, stacked lanes drawn as min/max per
  pixel column, two cursors, compare a second drive in grey, stall
  forensics.
- Raw tools: continuous sweep, hex console, guided mapper, Rank addresses
  with Assign to channel.
- Tune: opens ROMs and definitions in the app, edits with undo, saves with
  the checksum fixed, draws the live cell and its trail on the table.
- `--demo`, `--profile`, `--no-gl`; `tools/bench_ui.py`,
  `tools/screenshots.py` and `tools/smoke.py`. All sensors use under 20% of
  a core with the simulated black box (measured on a desktop CPU, not the
  T430).

### Black box (Track B)

- Portable C core (`bbcore`) with a Python mirror and host tests: 1 kHz
  sampling of the battery, ECU +B, fuel pump, IG1, tach, ignition trigger
  and injector, trigger rules, a 30 s ring buffer and capture files.
- ESP32-S3 firmware (ESP-IDF, PlatformIO project), the USB protocol, the
  file format and the hardware plan with a bench test.
- Laptop side: `hondalog-bb` (live, list, fetch, info, csv, config,
  selftest, cal), `hondalog-bbsim`, alignment with the ECU log, verdicts on
  what fell first.

### Handheld

- Hardware plan and pin header for the Sunton ESP32-4827S043N (CYD), with
  two ways to reach the K-line (the KKL cable on USB host, or an L9637D on
  a UART).
- UI for the 480x272 panel driven by one button, with a PC simulator,
  contrast and layout checks and screenshots.
- `hhcore`, a portable C port of the laptop's link, planner, expressions,
  alarms and stall rules, tested against the Python it came from.
- ESP-IDF firmware: K-line over USB host (vendored `usb_host_cdc_acm`
  2.4.1 and `usb_host_ftdi_vcp` 2.1.1) or UART, power-cut-safe logging to
  SD, GPS, ISO 9141-2 fallback, deep sleep.
- Laptop side: `hondalog-handheld export-map`, `import`, `info`.
- Serial link to the laptop over the USB-C (CH340, COBS + CRC16 frames, 921600 baud
  with fallback): live ECU reads, GPS, alarms and stalls stream to the app, which
  records them as a normal drive (`--port cyd:`, "CYD (serial)" in the menu).
  Two-way with the GPS on GPIO18: `hondalog-handheld sync` (resumable SD download),
  `push-map`, `set-time`, `status`, `command`. One-way with `--gps-p1`.
  `hondalog-cydsim` simulates the handheld for testing.
- A build option for the GPS on header P1 (`--gps-p1`), and GPS sentence
  counters (`GPS OK/BAD`) on the Link screen.

### Tuning (Track C)

- ROM definitions in TunerPro XDF and a native TOML form, with conversion
  between them; ROM images, an editing session with undo, a table editor
  and ROM diff.
- Live table cursor, auto-tune from a wideband log, knock map.
- `minipro` burner wrapper and an Ostrich 2.0 driver with a simulator.
- `hondalog-tune` and a synthetic demo ROM. No P28 definition ships.

### Build, tests and documentation

- `scripts/build-firmware.sh`: host tests, simulator checks and the build
  for both firmwares, with PlatformIO or an ESP-IDF already installed.
- GitHub Actions workflows run only when started by hand.
- About a thousand tests, all runnable offscreen without hardware.
- Documentation: a user guide, hardware, cable day, mapping, stall
  hunting, data and SQL, handheld, tuning, troubleshooting, FAQ, glossary,
  development and firmware development pages under `docs/`; the
  architecture map; the UI design handoff and its mockups; the commercial
  tool survey and feature list.

## 0.1.0 (2026-11-15)

First version.

- Live data from the ECU over a KKL 409.1 cable with the Honda diagnostic
  protocol (`20 05 addr len cksum`, 9600 8N1).
- Logging to CSV.
- Stall catcher: RPM to 0, or the ECU going silent.
- Raw tools: memory sweep and hex console.
- Replay of a log.
- An address map (the kerpz P2T map as a starting guess) with a `?` on
  every channel until it is marked verified.
- A simulated ECU on a pseudo-terminal (`hondalog-sim`).
- `install.sh` for Debian: packages, `/opt/hondalog`, the `dialout` group,
  a udev rule for `/dev/hondalog`, a menu launcher.
