# Changelog

All notable changes, grouped by area. Dates are commit dates. The version
number in `pyproject.toml` is still 0.1.0; everything after the first
commit is listed under "Unreleased".

Almost nothing here has been run on the car yet. "Tested" below means
against the simulators and the host builds of the firmware cores.

## Unreleased

### Version 0.2.1

- Setup > **Downloads** (web app, desktop browsers and the Android app): direct links to the newest GitHub release for
  Android, Windows, Linux (tar.gz and AppImage), the CYD firmware and the offline web app, with sizes, release
  notes, SHA256SUMS and the CYD flashing steps. The build for this device is marked, and an update is flagged when
  the release is newer than what's running. It asks api.github.com only when the tab opens and keeps the last
  answer for offline. On the radio, Download opens the browser and the APK installs over the running version.
- Head-unit screen (a 1280×720 touch radio): in the Android app the layout is picked from the panel's real pixels,
  so a 1280×720 radio gets the full 1366-wide layout at any density setting. At 240 dpi it used to drop into the
  cramped phone layout, which cut values off. Touch screens and the app show no keyboard hints (bottom strip,
  "Space" on MARK). The app also has no tap flash, no long-press selection and no overscroll.
- 0.2.1 everywhere (web, desktop, drive headers, TWA manifest). The Android versionCode is 201, so the standalone APK
  installs over the 0.2.0 TWA from the v0.2.0 release when both are signed with the release key.

### Standalone Android app for the head unit

- `android/`: the web app built into the APK and run in the app's own WebView, replacing the Chrome TWA for the radio
  (`web/android-twa` stays as a fallback). No Chrome and no internet needed. Logging, ECU polling and the stall
  catcher keep running behind other apps (foreground service, wake lock, page kept "visible"). The app opens and
  connects by itself when the KKL cable (FTDI) or the CYD (CH340) is plugged in. Optional start at boot. Drives
  live in app storage (atomic writes). Exports go to `Download/hondalog`. Warns when the WebView is older than 100.
- USB: a `navigator.usb` shim over Android's USB host API (`assets/hondalog-android.js`, `UsbBridge.java`), so the
  web app's FTDI/CH340 WebUSB drivers run unchanged. The web app uses its `NativeFileStore`
  (`web/src/platform/native.ts`) and the USB bridge when `window.HondalogAndroid` is there, and no service worker.
- Build: `android\build.ps1` on Windows (SDK build-tools, signs with the release key; `build-all.ps1 -Only android`
  uses it) or `android/build.sh` from Debian/Ubuntu packages. versionCode = major·10000 + minor·100 + patch.
- Install plan and first-start steps for the radio: `docs/android.md`.
- Tested: the shim in front of the P2P simulator with the app's own link, transport and FTDI driver (wakes at 10400,
  reads RAM). In headless Chrome at 1024×600, the APK's own assets with a faked bridge connect by themselves, poll at
  about 13 Hz and record. Not yet run on a real radio or on the car.

### Handheld shows US units

- The CYD's screen shows °F, mph, mi (ft on the map scale bar), inHg and gal/h by default; `units = metric` in
  `HANDHELD.CFG` brings back the address map's own units. Display only (`main/ui/ui_units.c`): tile ranges, bars
  and alarm-banner limits convert with the value; the engine, alarms, drive files, serial stream and relay keep
  the map's units. Factors and decimals as the laptop's `hondalog/units.py`. Tested: a host C test of the
  conversions (`hhunitstest`), the config parser, the simulator's layout and contrast checks in both unit sets.

### Handheld tab in the laptop app, and the HL text session

- A **Handheld** tab (F10) in the PySide6 app: port picker with CH340 detection and one Connect that stays open
  across the sub-views Status, SD card (tree, download, upload, delete), Config (`HANDHELD.CFG` as a form or
  text, with the lines the handheld will skip), Map (folder picker, percent, MB, ETA, resume; sent over the binary
  map-upload commands), Console (live log, send a line, record to a file) and Install firmware (esptool 5, merged
  image check, progress, the port released before flashing and reopened after). Serial work runs on a worker
  thread. Needs the `esptool` package (now a dependency).
- `hondalog-handheld ls`, `get`, `put`, `rm`, `cfg get|put`, `stat`, `mark`, `time`, `reboot` and `flash`: thin
  wrappers on `hondalog/handheld/hl_client.py`, the same client the tab uses. `put-map` stays the one map command.
- Firmware: the HL text session (`components/hhcore/hh_hl.c`, SERIAL.md "The HL session"): hello, stat, ls, get,
  put with resume, cfg, rm, mark, time, reboot, a 30 s idle timeout, base64 lines in a window of three, writes
  limited to `MAP/` and the two settings files, deletes to those and `DRIVES/`, the drive being recorded refused.
  It pauses the binary stream while open and is not built into the GPS-on-P1 firmware. The UART receive buffer is
  now 16 KB. `port/hh_serfs.c` gained path-based list, read and remove.
- `hondalog-cydsim` speaks HL (`--wire-baud`, `--sd-kbps` model the cable and the card; `--no-logs`). Tested: a host
  C test for line parsing, base64, CRC32, PUT with resume and a bad checksum, the path rules and the timeout
  (`hhhltest`); the laptop client against the firmware C (`tests/test_hh_hl.py`); the client against the simulator
  over in-process and pty ports; the screen offscreen against the simulator. Not run on the board yet.

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

- `stall_locations` finds the nearest GPS fix with `min(abs())` instead of a
  correlated `ORDER BY`, which SQLite 3.45.0/3.45.1 rejected ("no such column:
  c.t_trigger"); ties now pick the earlier fix.
- Every drive goes into one crash-safe SQLite database,
  `~/hondalog/hondalog.db`, committed every half second, with the raw bytes
  of every K-line read. A short-lived `.hlog` file format came first and was
  replaced by the database.
- A view per drive with a column per channel, and the views
  `drive_summary`, `stalls`, `readings`, `blackbox_1s`, `ecu_reads`,
  `markers`, `gps_track` and `stall_locations` for plain SQL.
- `hondalog-log`: `record`, `list`, `info`, `import`, `export`, `compare`,
  `report`, `redecode`, `prune`, `rm`, `keep`, `vacuum`, `sql`, `stalls`.
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

- Fixed a crash at start on Debian trixie (`QListView.update() takes exactly one
  argument`): repainting every child after a theme or unit change now calls
  `QWidget.update(w)`, since Debian's PySide6 binds item views' `update(index)`.
- A new UI designed at 1366x768 for the ThinkPad T430, replacing the 0.1.0
  window: Sensors, Drive, Map 3D, Replay, Raw tools, Tune, Alarms and Map
  on F1 to F8, Daylight and Night themes (F9), every action on a key, state
  shown by shape as well as colour.
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

### GPS and the map

- The Map screen (F8): the GPS track of the drive Replay has open, coloured
  by speed, RPM, MAP, AFR or any channel, with shape-coded pins for marks,
  alarms, stalls and other captures; a click on the track moves Replay's
  cursor there, and Replay's cursor is a dot on the map. While Replay is
  live: the car now and the last ten minutes. "All stalls": every stall of
  the database on one map, filtered by date and trigger reason, with the
  biggest group in one place named; a pin opens its capture in Replay.
- OpenStreetMap raster tiles under the OSM tile usage policy (a User-Agent
  naming hondalog, only the tiles on screen, two connections, the server's
  cache headers, the attribution on the map), offline first: a disk cache
  in `~/.cache/hondalog/tiles` with a size cap, a lower zoom's tile or a
  plain grid where nothing is cached, the track, scale bar and north arrow
  without any tiles. Tiles inverted and dimmed at night. The server is a
  setting (Settings > Map tiles). No new packages.
- An hour of driving with a fix every 0.1 s (36 000 points) draws and pans
  at 30 fps in `tools/bench_ui.py`: decimated per zoom, drawn once per zoom
  into an image (a slice per frame after a zoom) that panning moves.
- Exports: GPX (track with times, waypoints for marks, alarms and captures)
  and KML (coloured by a channel), from the screen and with
  `hondalog-log export DRIVE OUT --format gpx|kml [--channel C]`;
  `hondalog-log stalls [--geojson OUT]` lists every stall with its place and
  groups them.
- SQL views `gps_track` (every fix of every drive) and `stall_locations`
  (every capture with the fix nearest its trigger); schema version 2.
- The handheld's live GPS reaches the app's bus (source GPS); the CYD
  simulator and the demo drive a loop of streets in Pearl, MS, and the car
  stops where the engine stalls. `hondalog.data.synth.write_gps_drives`
  makes test drives with stalls on one rough stretch;
  `tools/fixture_tiles.py` draws stand-in tiles so no test or tool asks a
  tile server for anything.

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

- No deep sleep while the laptop is talking: the ECU-silent sleep
  (`sleep_after_s` + `drive_gap_s`, 7 min by default) waits until the USB-C
  link has been quiet for 2 min. On the bench with no car it used to unmount
  the card and drop the link 7 min after boot, in the middle of a map upload.
- Map upload survives a failed card write: the temporary file is kept and the
  upload stays active, the handheld logs the step and errno (`serfs` warning),
  and `put-map` / `cyd_debug.py --put-map` retry the chunk (up to 8 times)
  instead of throwing the file away and starting again.
- A `sys:` log line every 10 s with the last reset reason (PANIC, TASK_WDT,
  BROWNOUT, ...), uptime and free internal/PSRAM heap, to explain reboots.
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
- GPS on header P3: data on GPIO18 and the PPS output on GPIO17 (replacing the
  optional second button in the normal build). PPS rising edges are
  time-stamped by an ISR, the firmware swaps the UART to the PPS pin at run
  time if P3 is wired the other way round, and the `gps diag` line now reports
  both pins.
- Map screen: an offline road map from `MAP/` on the SD card, north up and
  centred on the GPS fix, in the press cycle between GPS trip and Link.
  Roads by class (black on white, white on black at night), water, dashed
  railways, towns by population, fuel stations; route shields (`I-55`,
  `US 49`), street and place names placed around each other; a heading arrow,
  the drive's trail as breadcrumbs, octagon pins for stalls and diamonds for
  marks on this drive; a scale bar in miles; "© OpenStreetMap contributors"
  always on screen. Four zoom levels follow the speed (under 30, 60, 90
  km/h and over) with hysteresis; the optional second button steps the zoom.
  `NO GPS FIX` banner with the last fix, or the last known position kept in
  NVS at the end of each drive (Jackson before any fix). Without `MAP/` the
  screen says so and gives the `map-build` command.
- The map is drawn by `components/hhmap` (portable C: reader, LRU tile cache,
  anti-aliased renderer) in a low-priority task on core 0, into PSRAM
  canvases the UI swaps in; the UI never waits on the card, and redraws the
  map only when the car has moved 24 px, the zoom or theme changes, or a pin
  is added. The SD mount allows 6 open files (was 4).
- `hondalog-handheld map-build --pbf FILE... --center LAT,LON --radius-mi N
  --out SDCARD [--zip FILE]`: builds `MAP/` from Geofabrik OpenStreetMap
  extracts with pyosmium: 4 detail levels with Douglas-Peucker
  simplification each, tiles clipped exactly at their edges, int16
  coordinates, a CRC per tile, an index for one-seek lookup. 100 miles
  around Jackson from the Mississippi and Louisiana extracts: 17.3 MB,
  about 2 minutes. Format: `firmware/handheld/MAPFORMAT.md`.
  `hondalog-handheld info CARD` checks a `MAP/` folder tile by tile.
- Simulator: Map scenes on real Jackson roads (downtown on State Street,
  I-55 at 70 mph, no fix, no map) from a committed 12-mile map
  (`sim/testmap`, `make testmap` rebuilds it), with layout, 3:1 contrast
  checks for the map's graphics, and the render budget.
- Fonts: the © sign added to the text fonts.
- Map upload over the USB-C cable (two-way build): four new link commands
  (`MAPUP_OPEN`, `MAPUP_WRITE`, `MAPUP_STATUS`, `MAPUP_COMMIT`, capability
  bit 16, SERIAL.md "Map upload") put files into the card's `MAP/` folder:
  names `MAP/` plus an 8.3 name only (no path tricks, refused in the
  `--gps-p1` build), a CRC32 on every chunk and on the whole file, a status
  query for resuming, and a commit that reads the temporary file back from
  the card, checks length and CRC32 and only then renames it over the real
  file. The Map screen's files are closed for the length of an upload
  (`MAP BEING UPDATED`) and reopened after the last commit, an abort, or 10 s
  of silence (the upload then waits, resumable). `hondalog-handheld put-map
  DIR` (progress bar, resume, verification) and `tools/cyd_debug.py PORT
  --put-map DIR` (Python and pyserial only, a progress line every 5 %) send
  `README.TXT`, `MAP.DAT` and `MAP.IDX`; `cydsim` keeps the files in its
  `--sd` folder. About 40-55 kB/s expected on the cable (5-7 minutes for the
  17 MB map; not yet measured on the board). Host C tests cover chunking,
  resume, bad chunk and whole-file CRCs, refused names and the
  temporary-file-then-rename sequence.
- The firmware runs its code and read-only data from PSRAM
  (`CONFIG_SPIRAM_XIP_FROM_PSRAM`, ESP-IDF's advice for RGB panels with
  frame buffers in PSRAM): a flash write such as an NVS commit no longer
  stops the LCD interrupt's reads of the frames.
- Wall clock from the GPS pulse: each PPS edge is paired with the UTC second of
  the GGA/RMC that follows within 900 ms, and an alpha-beta filter keeps the
  device clock's offset and drift, so `unix_us` is good to tens of microseconds
  while the pulse is there (portable `hh_pps.c`, host tests with simulated
  pulses, jitter, missing and noise pulses, a lost fix and a clock step). Noise
  pulses (not 1 s +- 1 ms) and pairs far from the prediction are ignored. With
  no pulse or no fix it falls back to the sentences' time as before. The
  snapshot has `time_source` (none / GPS sentences / PPS / laptop) and the error
  estimate; the Link screen shows `TIME PPS +-20us`; drive files get a TIME
  record (new source 4, with the error and drift) when the source changes and
  every minute; the serial link's hello has a PPS flag and STATS gains
  `time_source` and `time_err_us` (104 bytes, the first 96 unchanged), and its
  `t_us` and `unix_us` are now read at the same instant. The `gps time` log
  line shows the offset, drift and jitter. The laptop reads the new codes
  (`hondalog-handheld status`, the importer's epoch) and still reads files and
  frames from older firmware. The Link screen's STALL row is shortened to make
  room for TIME.

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
