# hondalog feature list

Prioritised feature list for the six implementation workstreams. It is derived from the survey in `competitors.md` and from reading the existing code (v0.1.0).

## How to read it

**Priority**

- **P0**: build first. Without it the tool is not trustworthy or not usable in the car.
- **P1**: needed to stand next to the commercial tools.
- **P2**: nice to have.

**★** marks the 5–8 features per workstream that make it "industry standard". Everything without a star is support work or polish.

**Feasible on** says where the feature can work:

- **Stock**: stock P2P over the K-line at 9600 baud (Track A).
- **Stock (ISO)**: needs the standard OBD2 session (ISO 9141-2, 10400 baud), not the Honda RAM-read protocol.
- **Track B**: needs the black box.
- **P28**: needs the chipped P28, and where noted the Ostrich or a wideband.
- **Offline**: works on saved logs or ROM files, no car needed.

**†** marks a fact from background knowledge that was not re-checked in this session. Verify before coding against it.

## What exists today (v0.1.0)

Honda `20 05 addr len cksum` reads with echo stripping and checksum checks; a poller thread that sweeps the address map's blocks (about 10 Hz in the simulator); a TOML address map with formulas and a verified/unverified flag; CSV logging with `#` header lines and markers; a stall catcher (30 s before, 10 s after, on RPM = 0 or 1 s of link loss); live plots (up to 8 channels); a replay tab with cursor; a hex console and memory scanner; an ECU simulator; one dark theme. `iso9141.py` is an empty stub. There is no DTC code, no math channels, no alarms, no analysis views, no tuning code and no Track B code.

Nothing has been tested on the real car yet. Three things to keep in mind across all workstreams:

1. **The Honda protocol may not answer on OBD2 pin 7 of a US-market P2P.** kerpz tested through the Honda 3-pin DLC on a P2T. ISO 9141-2 is the only link that is certain to work on this car, so it is P0 and not a cross-check.
2. **The hex console can send any bytes.** kerpz's code has an ECU reset command (`21 04 01` †). Writes and resets need a guard before anyone pokes at a running engine.
3. **A sweep takes about 100 ms and every channel in a row gets the same timestamp.** Per-read timestamps are a prerequisite for honest analysis and for lining up with Track B.

---

## W1. ECU link and protocol performance

| ID | Pri | ★ | Feature and acceptance criteria | Who has it | Feasible on |
|---|---|---|---|---|---|
| W1-01 | P0 | ★ | **First-contact wizard.** One button runs, in order: port and chip check; echo test (send a byte, expect it back within 20 ms); passive listen 2 s; Honda read of `0x00` at 9600 with and without the wake bytes; ISO 9141-2 5-baud init at 10400. Ends with a plain verdict ("Honda protocol OK", "ISO only", "cable dead", "ECU silent") and saves a text report with every byte sent and received. Passes against the simulator in all four states. | HDS auto-detect; OBDLink protocol auto-search | Stock |
| W1-02 | P0 | ★ | **ISO 9141-2 session.** 5-baud init of address `0x33` by break toggling (200 ms per bit), sync `0x55`, key bytes, inverted-key handshake, 10400 8N1, `68 6A F1` headers, additive checksum, keepalive at most every 4 s. Mode 01 PID `0x00/0x20/0x40` support bitmaps are read and only supported PIDs are offered. Simulator gains an ISO mode so this is testable off the car. | Every OBD2 scan tool | Stock (ISO) |
| W1-03 | P0 | ★ | **DTC read and clear.** ISO mode 03 (stored), 07 (pending), 04 (clear, behind a confirm dialog that states readiness monitors will reset). Codes shown with text from a bundled P-code table plus Honda-specific P1xxx. If the Honda protocol is live, also decode the DTC bytes kerpz reads at `0x40–0x4F` † and show both views side by side. | SManager Error Codes window, FlashPro, HDS, OBDLink | Stock (ISO); Honda bytes need map verification |
| W1-04 | P0 | ★ | **Freeze frame and readiness.** Mode 02 freeze frame for the stored DTC, mode 01 PID 01 MIL status and monitor readiness. Saved into the log header and exportable as a one-page text report. | OBDLink (saved report), HDS | Stock (ISO) |
| W1-05 | P0 | ★ | **Tiered poll scheduler.** Each channel gets a rate class (fast, medium, slow). The scheduler builds the smallest set of read requests per class, merging neighbours when a gap costs less than a new request (break-even is about 8 bytes plus turnaround). Acceptance: with RPM, MAP, TPS, O2, injector and ignition as "fast", the simulator shows at least 15 Hz on those and at least 1 Hz on everything else; the achieved rate per channel is displayed. | Crome "sampling rate priority"; rusEFI logging profiles | Stock |
| W1-06 | P0 | ★ | **Per-read timestamps.** Every decoded value carries the monotonic time at which the last byte of its reply arrived, not the sweep time. Logged per channel group. Acceptance: in a replay, RPM and MAP from different blocks show their true offsets (tens of ms), and unit tests cover it. | MoTeC, AiM (per-channel rates) | Stock |
| W1-07 | P0 | | **Link timing probe.** Measures ECU turnaround (last request byte to first reply byte) and reports median, p95 and maximum over 200 reads; then tunes `reply_timeout_s` and `inter_request_delay_s` automatically within safe limits. Sets and verifies the FTDI latency timer at 1 ms and warns if it could not. | None directly; equivalent to OBDLink adaptive timing | Stock |
| W1-08 | P0 | | **Block-length and address-space probe.** Tries lengths 1, 2, 4, 8, 16, 32, 48, 64 at `0x00` and records the largest that returns a valid checksum; stores the result in the map as `max_read_len`. Then reads `0x00–0xFF` once and marks which addresses answer. Read-only. | — | Stock |
| W1-09 | P0 | | **Command guard.** Only command `0x20` (read) is allowed on the wire by default, including from the hex console. Anything else needs an "expert unlock" tick box per session and is written to the log header. | HDS (function tests behind confirmations) | Stock |
| W1-10 | P1 | ★ | **Link health statistics.** Rolling counters for checksum errors, short replies, timeouts and retries, plus achieved Hz per rate class, shown in the status bar and logged once per second. A reply that fails its checksum is retried once before the block is dropped. | TunerStudio comms stats † | Stock |
| W1-11 | P1 | | **Burst mode.** One or two addresses read as fast as the line allows (about 45–65 Hz for one 2-byte channel) for a fixed time, for scoping a single signal such as RPM or O2 switching. | PicoScope-style single-signal focus | Stock |
| W1-12 | P1 | | **Dual-protocol capture plan.** The two protocols cannot share the line. Provide a scripted sequence: ISO session for DTCs, freeze frame and a PID snapshot, then drop to the Honda protocol for logging; note the switch in the log. | — | Stock |
| W1-13 | P1 | | **Listen-only sniffer.** Record everything on the K-line with microsecond timestamps to a file, at a chosen baud, without transmitting. For capturing a real scan tool's conversation with a Y-cable. | K-line sniffers | Stock |
| W1-14 | P2 | | **OBD1 datalog protocol for the P28.** A second link class for the CN2 header using the Crome QD2-style stream † (38400 baud †), behind the same `Link` interface so the rest of the app does not care which ECU is attached. | Crome, FreeLog, HTS | P28 |
| W1-15 | P2 | | **ELM327-compatible bridge.** Expose the poller as a pty that answers ELM327 AT commands, so Torque-class apps can read hondalog's channels. | kerpz does this on the Arduino | Stock |

## W2. Track B black box: firmware and host integration

| ID | Pri | ★ | Feature and acceptance criteria | Who has it | Feasible on |
|---|---|---|---|---|---|
| W2-01 | P0 | ★ | **Protected, calibrated front end.** Four rails (battery, ECU +B, fuel pump feed, IG1) through dividers with series resistance and clamps that survive ±100 V transients and reverse polarity; three pulse inputs (tach, ignition trigger, one injector) through clamp-and-comparator or opto stages rated for injector flyback (about 60–80 V †). Two-point calibration per rail stored in flash. Acceptance: readings within ±0.1 V of a bench meter from 6 V to 16 V. | PicoScope input ranges to ±200 V | Track B |
| W2-02 | P0 | ★ | **Rail sampling with glitch capture.** Each rail sampled at 5 kHz or faster; stored at 1 kHz as minimum, maximum and mean per millisecond. Acceptance: a 1 ms dropout to 0 V injected on the bench is visible in the stored record with its depth within 0.3 V. | PicoScope dropout trigger, Snap-on peak detect | Track B |
| W2-03 | P0 | ★ | **Edge timestamping.** Tach, ignition trigger and injector edges captured by hardware timer (ESP32 RMT/MCPWM capture or RP2040 PIO) with 10 µs resolution or better. Derived per event: period, RPM, injector pulse width, missing-pulse flag. Acceptance: at a 7,000 rpm bench signal no edge is lost over 10 minutes. | rusEFI trigger log; TunerStudio tooth and composite loggers | Track B |
| W2-04 | P0 | ★ | **30 s pre-trigger ring buffer that survives power loss.** Runs whenever the box has power. On a trigger it keeps 30 s before and 10 s after and writes the capture to flash or SD. A supercapacitor or small cell holds the MCU up for at least 2 s after input power disappears so the write completes. Acceptance: pull the supply on the bench mid-run; the capture is on storage and passes its CRC. | Hondata onboard logging; PicoScope alarm "save all buffers" | Track B |
| W2-05 | P0 | ★ | **Trigger set.** Any of: a rail below a threshold for longer than N ms; a rail falling faster than X V/ms; no ignition edges for N ms while IG1 is high; no injector edges while tach edges continue; manual button. Each trigger records its cause. Thresholds are set from the host and stored. | Pico trigger types; Snap-on PID Trigger; rusEFI expression triggers | Track B |
| W2-06 | P0 | ★ | **Simple documented host protocol.** USB CDC serial. Speeduino-style: single-byte commands, fixed binary frames with type, length and CRC. Commands: identity and firmware version, live frame stream (50 Hz summary), list captures, download capture, set config, set clock. Acceptance: a protocol document and a Python reference client with tests; the firmware has a simulator so the host can be developed without hardware. | Speeduino `A`/`n`/`r` commands | Track B |
| W2-07 | P0 | | **Time alignment with the K-line log.** Host sends its monotonic clock to the box every second and stores the offset. Captures carry box time and the mapped host time. Acceptance: a bench event fed to both paths lines up within 20 ms in replay. | MoTeC and AiM multi-source alignment | Track B + Stock |
| W2-08 | P1 | ★ | **Standalone operation.** The box captures and stores with no laptop attached; an LED shows armed, triggered and "capture waiting". Captures are downloaded later. Storage holds at least 50 captures. | KPro (up to 20 onboard logs), FlashPro, Haltech | Track B |
| W2-09 | P1 | ★ | **Host integration.** Track B channels appear in hondalog as ordinary channels (plots, alarms, logs). A Track B trigger also fires the K-line stall capture and the two are saved as one event bundle. | PicoScope plus scan-tool data on one screen (Pico "PicoDiagnostics") † | Track B + Stock |
| W2-10 | P1 | | **Capture viewer with verdict helper.** For each capture show the order of events with millisecond deltas, for example "ECU +B fell 38 ms before IG1; fuel pump feed followed 4 ms later". A fixed table maps the order to likely causes (main relay, ignition switch, ground, ECU). | Pico guided tests | Offline |
| W2-11 | P1 | | **Spare analog inputs.** Two 0–5 V inputs for a wideband controller output and one spare, sampled at 100 Hz, with a linear calibration (volts to lambda) set from the host. | KPro (8 analog inputs), HTS (3 wideband inputs) | Track B |
| W2-12 | P2 | | **Wireless download.** ESP32-S3 only: Wi-Fi access point for pulling captures without a cable. | Hondata Bluetooth, TS Dash | Track B |
| W2-13 | P2 | | **Accelerometer channel.** A 3-axis sensor logged at 100 Hz to tie dropouts to bumps or vibration. | Shadow Dash, AiM | Track B |

Sizing note for W2-04: four rails at 1 kHz with min, max and mean in 12 bits each is about 18 bytes per ms, so 30 s is about 540 KB. That fits an ESP32-S3 with PSRAM. It does not fit an RP2040's 264 KB of RAM; on that chip either store min and max only at 500 Hz (about 120 KB) or use an external FRAM/PSRAM. Edge events are small: about 500 edges per second at 3,000 rpm, 5 bytes each, about 75 KB for 30 s.

## W3. Address mapping and verification toolkit, math channels, alarms

| ID | Pri | ★ | Feature and acceptance criteria | Who has it | Feasible on |
|---|---|---|---|---|---|
| W3-01 | P0 | ★ | **Guided verification per channel.** Each channel has a scripted check: for example TPS "key on, engine off, press the pedal slowly to the floor" expects a monotonic rise from under 5% to over 90%; ECT expects agreement with IAT within 5 °C on a cold engine; battery expects agreement with Track B or a typed-in meter reading within 0.3 V. Pass marks the channel verified and stores date, evidence log name and map version. | HDS data list is the reference; nothing automates this | Stock |
| W3-02 | P0 | ★ | **Cross-check against ISO PIDs.** Capture a PID snapshot (W1-12), then the Honda RAM values under the same steady conditions, and report per channel: agree, scaled (suggest slope and offset by least squares), or no match. Covers RPM, ECT, IAT, MAP, TPS, VSS, STFT, LTFT, O2, timing advance. | — (this is the main trust-builder for a guessed map) | Stock + Stock (ISO) |
| W3-03 | P0 | ★ | **Correlation finder in the memory scanner.** While the scanner sweeps, the user picks a reference (a verified channel, a Track B channel or a typed target) and the tool ranks all 256 addresses, 8- and 16-bit, by correlation with it. Also a "what changed" filter for a marked action (A/C on, brake pressed) that lists bits that flipped only during the action. | TunerPro bin compare ideas; game-style memory scanners | Stock |
| W3-04 | P0 | ★ | **Math channels.** User-defined expressions over any channels, stored in the address map, evaluated live at the rate of their fastest input and again on replay. Syntax follows MegaLogViewer where practical: `[rpm]`, `[rpm-1]` for the previous sample, plus time-window helpers `avg(x, ms)`, `slope(x, ms)`, `min/max(x, ms)`, `smooth(x, n)` and `a ? b : c`. A math channel can use another math channel; cycles are rejected with a clear message. Ships with duty cycle, fuel flow estimate, total trim (STFT + LTFT) and RPM rate of change. | MLV HD custom fields; VCM Scanner math parameters (10 user slots, `avg`/`slope`); TunerPro ADX math; MoTeC equation editor | Stock |
| W3-05 | P0 | ★ | **Alarms.** Per channel or math channel: low and high limits, a hold time, a re-arm hysteresis, a severity (warn or critical) and actions (tile colour, banner, beep, marker in the log, start a capture). Evaluated every sample. Acceptance: coolant above 105 °C for 2 s raises a critical banner within one poll cycle and writes a marker. | TunerStudio indicators and warning lights; COBB shift/warn lights; Snap-on PID limits | Stock |
| W3-06 | P0 | | **Safe expression evaluator.** One parser for formulas, math channels, alarm and trigger conditions. No `eval`; a whitelist of functions; units checked where declared; evaluation errors show the channel as invalid, never crash polling. | — | Stock |
| W3-07 | P1 | ★ | **Map versioning and provenance.** Each map change gets a version hash that is written into every log; the app can show a diff between two maps and re-decode an old log's raw bytes with a newer map (needs W4-02 raw capture). | TunerPro XDF/ADX as shareable definitions | Stock / Offline |
| W3-08 | P1 | ★ | **Calibration helpers.** Fit a linear or polynomial formula from pairs of (raw byte, reference value) collected live, show the residual, and write the formula into the map. Thermistor curves can be fitted from three temperature points. | TunerStudio sensor calibration tools | Stock |
| W3-09 | P1 | | **Bit-field explorer.** For flag bytes, show eight lamps per byte with history strips, and let the user name a bit and promote it to a flag channel. | KManager input/output bitfields | Stock |
| W3-10 | P1 | | **Unit system.** Each channel declares a base unit; display unit is a per-user choice (°C/°F, kPa/psi/inHg, km/h/mph, lambda/AFR). Logs store base units and name the unit in the header. | MoTeC automatic unit conversion; all commercial tools | Stock |
| W3-11 | P2 | | **Shareable map packs.** Export a map with its verification evidence as one file, so a second P2P owner can start from verified addresses. | TunerPro definition ecosystem | Offline |
| W3-12 | P2 | | **ADX import.** Read a TunerPro ADX datastream definition into the channel model, where one exists for the OBD1 datalog stream. | TunerPro RT | P28 |

## W4. Data layer: log format, replay, analysis, exports

| ID | Pri | ★ | Feature and acceptance criteria | Who has it | Feasible on |
|---|---|---|---|---|---|
| W4-01 | P0 | ★ | **MegaLogViewer-compatible export.** (a) Tab-delimited `.msl`-style text: one header row of names, one row of units, numeric rows, markers as `MARK` lines †. (b) Binary MLG v2 exactly per the EFI Analytics specification: `MLVLG\0` header, big-endian, 89-byte field definitions, type-0 data blocks with a 10 µs timestamp, type-1 marker blocks with a 50-byte message. Acceptance: files open in MegaLogViewer HD with correct names, units and markers; a round-trip reader test proves the binary layout. | TunerStudio, rusEFI and Speeduino write MLG; MLV HD reads any delimited ASCII | Offline |
| W4-02 | P0 | ★ | **Native log format with raw bytes.** A single-file container (SQLite or Parquet plus a JSON header) holding: decoded channels with per-read timestamps, the raw RAM bytes of every read, the address map text, link statistics, markers, alarms, DTC and freeze-frame snapshot, Track B frames. Append-safe: after a hard power cut the file opens and loses at most 1 s. CSV stays as an export. | KManager `.kdl` frames; rusEFI embeds the tune in the log | Stock |
| W4-03 | P0 | ★ | **Time-series replay with dual cursors.** Stacked graphs, linked zoom and pan, two cursors with a delta readout (time, and each channel's difference, minimum, maximum and mean between cursors), marker and alarm list that jumps on click, keyboard stepping one sample at a time. Loads a 1-hour log in under 2 s. | MoTeC i2 (dual cursors, linked zoom), MLV, VCM Scanner | Offline |
| W4-04 | P0 | ★ | **Table histogram.** Any channel on X and Y with user-set bins (presets matching the fuel table's RPM and MAP breakpoints), any channel on Z, cell statistic selectable (mean, minimum, maximum, count, standard deviation), an optional second Z for a delta (for example lambda minus target), a minimum-hits threshold and colour scale. Clicking a cell lists its samples and jumps to them in the time view. | MLV HD histogram/table generator; VCM Scanner histograms; TunerPro RT; MoTeC mixture map | Offline; live on Stock |
| W4-05 | P0 | ★ | **Filters.** Named boolean expressions (same evaluator as W3-06) applied to histograms, scatter plots and exports, for example "ECT > 80 and closed loop and abs(TPS rate) < 5 %/s". Toggling a filter re-renders in under 200 ms on a 1-hour log. | MLV HD filters; MoTeC data gating | Offline |
| W4-06 | P1 | ★ | **Scatter plot.** X against Y with an optional colour channel, filters applied, a fitted line or polynomial on request, and brushing: selecting points highlights them in the time view. | MLV, MoTeC i2 | Offline |
| W4-07 | P1 | ★ | **Two-log compare.** Overlay a second log on the same graphs with an adjustable time offset, alignment by a chosen event (first marker, first time RPM crosses a value, stall trigger), and a difference trace. Histograms can show log A minus log B per cell. | MoTeC i2 Pro overlays with alignment; MLV overlay †; TunerPro bin compare for ROMs | Offline |
| W4-08 | P1 | ★ | **General trigger engine.** Replace the hard-coded stall catcher with rules built from expressions: pre-trigger and post-trigger times, re-arm condition, action (save capture, marker, beep). Ships with stall, link loss, low battery, lean spike and over-temperature rules. | Hondata (RPM/TPS start, RPM/speed stop), rusEFI (threshold and expression), Snap-on PID Trigger, Pico alarms | Stock |
| W4-09 | P1 | | **Session and event library.** A browsable list of logs and captures with date, duration, map version, notes, tags and thumbnails of RPM; full-text search over notes and markers. | KManager datalog comments; MoTeC projects and workbooks | Offline |
| W4-10 | P1 | | **Stall report.** For a capture, a one-page HTML or PDF: the last 30 s of key channels, Track B rail timeline, order of events, DTCs and freeze frame at the time, link statistics. | OBDLink saved diagnostic report; HDS snapshots | Offline |
| W4-11 | P1 | | **Importers.** Read MLG, MSL and generic CSV back in, so logs from other tools (and old hondalog CSVs) open in the same views. | MLV HD (any ASCII log) | Offline |
| W4-12 | P2 | | **GPS channel and map.** Optional USB GPS logged as channels; a track view coloured by any channel. | OBDLink, AiM, MoTeC rainbow maps, Shadow Dash | Stock |
| W4-13 | P2 | | **Virtual dyno.** Estimated wheel power from speed, acceleration and vehicle mass, as MLV's `accelHp` does. | MLV HD, Crome Dyno Tools plugin, eCtune † | Offline |
| W4-14 | P2 | | **Other exports.** MoTeC `.ld` † and Matlab/NumPy dumps for people who already own those tools. | MoTeC exports CSV and Matlab | Offline |

## W5. Tuning suite for the P28

Everything here needs the chipped P28. ROM-file features can be built and tested now against ROM images; live features need the Ostrich and, for fuel work, a wideband.

One risk to settle first: table locations on a Honda OBD1 ROM depend on the base ROM (stock P28, or a Crome-style rewritten base with datalogging). XDF files for these are scarce compared with the Crome ecosystem †. W5-01 should decide the base ROM and where its definitions come from before any editor work starts.

| ID | Pri | ★ | Feature and acceptance criteria | Who has it | Feasible on |
|---|---|---|---|---|---|
| W5-01 | P0 | ★ | **Definition layer.** Parse TunerPro XDF (XML): tables with X, Y, Z axes, address, element size, signedness, byte order, row/column order and math; constants; flags; linked axes. Plus a native TOML definition for anything XDF cannot express. Acceptance: a reference XDF loads, every table renders, and writing values back reproduces the original bytes exactly when nothing was changed. | TunerPro (XDF is the common format); Crome and eCtune use their own | Offline |
| W5-02 | P0 | ★ | **Table editor.** Spreadsheet-style grid with axis labels in engineering units; select, type, add, multiply, percent change; interpolate across rows, columns or a selection; smooth with an adjustable strength; copy and paste to and from spreadsheets; unlimited undo; cells changed since load are marked. | SManager, Crome, HTS (interpolate, smooth, snapshots), TunerPro, TunerStudio | Offline |
| W5-03 | P0 | ★ | **ROM safety.** Never edit the original: every session works on a copy, with automatic snapshots on each save. Size and signature checks on load (32 KB image, known vector pattern). Checksum handling per the definition: recompute, or confirm the checksum routine is disabled in the base ROM as Crome does. | HTS snapshots; Crome (checksum routine disabled); TunerStudio restore points † | Offline |
| W5-04 | P0 | ★ | **ROM diff.** Compare two ROMs: a byte-level difference list grouped by defined table or "undefined region", and a per-table cell difference view (absolute and percent). Compare against up to four at once is the TunerPro bar; two is the minimum. | TunerPro (up to 4 bins), SManager Compare Calibrations | Offline |
| W5-05 | P0 | ★ | **Live cursor and trail on tables.** The current operating cell is highlighted on the fuel and ignition tables at the poll rate, with interpolation weights shown across the four surrounding cells and a fading trail of where the engine has been. | SManager sensor tracing, HTS Map Trails, Crome MAP line trace, TunerPro bubble trace, TunerStudio | P28 (table view), and Stock for a read-only "virtual table" using RPM and MAP |
| W5-06 | P1 | ★ | **3D surface view.** Rotatable, zoomable surface with height colouring, the live cursor drawn on it, and point or region drag to edit. Holds 30 fps on HD 4000 for a 24×20 table. | TunerPro (multi-point drag, rotate, zoom), HTS, TunerStudio | Offline |
| W5-07 | P1 | ★ | **Lambda overlay and fuel correction.** Per fuel cell: measured lambda (mean, count), target lambda, and the percent difference, as three switchable overlays. "Apply correction" scales selected cells by the error, limited to a maximum step (default ±10%), only for cells above a minimum hit count, with locked cells skipped and neighbours optionally blended. Works from a log or live. | SManager lambda overlay; Crome F10/F11/F12 with Auto-Adjust and cell locks; HTS Auto Adjust; MLV VE Analyze; TunerStudio VE Analyze Live | P28 + wideband. Offline analysis also works on Stock using STFT + LTFT as the error signal |
| W5-08 | P1 | ★ | **Ostrich 2.0 driver.** Native Linux serial driver at 921,600 baud: identify, upload, download, verify, bank select, and single-byte or small-block live writes so a cell change reaches the engine without a full upload. Acceptance: full 32 KB upload and verify in under 3 s; a table edit is live in under 100 ms; verified against a hardware loopback or a protocol simulator built from the Moates command documentation †. | Crome, eCtune, BRE, TunerPro RT | P28 + Ostrich |
| W5-09 | P1 | | **Burner integration.** Drive a TL866II+ or T48 through the open-source `minipro` tool † : pick device SST27SF512, place the 32 KB image at the correct offset for a 64 KB chip (commonly `0x8000` †), erase, write, read back and compare, and record the result with the ROM's hash. Check first that the installed `minipro` supports the programmer and this chip. | Crome ("R" hotkey to burn), Moates BURN2 | P28 |
| W5-10 | P1 | | **Knock and ignition view.** Knock channel over the ignition table as a count per cell, with the time graph linked. | Crome knock recordings, KManager knock count, MLV (spark or AFR tables to see where knock occurs) | P28; analysis-only on Stock if the knock address verifies |
| W5-11 | P1 | | **Wideband input.** Serial drivers for common controllers (Innovate MTS †, AEM serial †) and the 0–5 V route through Track B (W2-11). Lambda is the stored unit; AFR is a display choice. | HTS (Innovate direct), rusEFI (Innovate serial, AEM CAN), SManager analog wideband | P28; logging also works on Stock |
| W5-12 | P2 | | **Emulator trace view.** Show Ostrich address-trace hits over the ROM map to find which tables the code actually reads. Moates warns the trace misses accesses and is a last resort, so label it as approximate. | TunerPro RT, RenoVelo Domino | P28 + Ostrich |
| W5-13 | P2 | | **Closed-loop live auto-tune.** W5-07 applied continuously while driving, with steady-state gating (ECT, TPS rate, RPM rate), lag compensation for the wideband, and a hard authority limit. | TunerStudio VE Analyze Live, Crome Live Auto Adjustment | P28 + Ostrich + wideband |
| W5-14 | P2 | | **Scripts and patches.** Apply named, reversible ROM patches (for example enable datalogging) from a definition, as Crome's plugins and XDF patches do. | Crome plugin manager, TunerPro XDF patches † | Offline |

## W6. UI/UX shell

Target display: 1366×768 TN panel, low brightness, narrow vertical viewing angle, Intel HD 4000. Design for a glance of under one second and for keyboard use with the laptop on the passenger seat.

| ID | Pri | ★ | Feature and acceptance criteria | Who has it | Feasible on |
|---|---|---|---|---|---|
| W6-01 | P0 | ★ | **All-sensors glance screen.** Every channel on one screen without scrolling: a grid of tiles (about 6×4 at 1366×768), each with name, value, unit, a min/max bar for the session and a state colour (normal, warn, critical, stale, unverified). Flags as a lamp strip. Values update at their poll rate; a stale value greys out after 3 missed updates. | SManager and KManager sensor window, HDS data list, Crome live data | Stock |
| W6-02 | P0 | ★ | **Driving view.** Four to six very large readouts (digits at least 90 px tall), an RPM bar, link state, recording state and the last alarm. No controls needing a pointer. Space adds a marker; one key cycles pages. Readable from 1 m at an angle. | COBB Accessport gauges, Torque dashboards, TS Dash, Crome Minimal View | Stock |
| W6-03 | P0 | ★ | **Sunlight theme and night theme.** "Sun": black on white, no mid-greys, line widths of 2 px or more, contrast ratio of at least 15:1 for values, state shown by shape or text as well as colour (TN panels shift colour with angle). "Night": dim amber or red on black. One key toggles; all widgets and plots follow. | MoTeC and AiM dash colour schemes; phone apps with day/night modes | Stock |
| W6-04 | P0 | ★ | **Performance budget.** With 8 plotted channels and 24 tiles: UI at 30 fps, under 50% of one core on the T430, no dropped serial reads (the poller never waits on the UI), memory under 400 MB after one hour, start to first data in under 3 s. A built-in overlay shows frame time, poll rate and queue depth, and a CI test runs the simulator for 10 minutes offscreen and fails on a regression. | Link forum shows what happens without a budget: live rate halves when logging and plotting | Stock |
| W6-05 | P0 | ★ | **Alarm and event presentation.** A full-width banner with large text and a distinct sound for critical alarms and stall captures; warn-level alarms colour the tile only. Acknowledge with one key. Alarm history list. | TunerStudio warning indicators, COBB warnings | Stock |
| W6-06 | P1 | ★ | **Dashboard designer.** Drag-and-drop pages built from widgets: numeric, bar, round gauge, lamp, sparkline, strip chart, table cursor. Per-widget channel, range, warn bands and size. Layouts saved as files, several pages per layout, with shipped presets (warm-up, cruise, tuning, stall hunt). | TunerStudio dashboard designer, TunerPro RT dashboards, Torque, OBDLink, VCM Scanner layouts | Stock |
| W6-07 | P1 | ★ | **Workspace layout.** Dockable panels and named workspaces (Drive, Diagnose, Map, Analyse, Tune) switched with F-keys; the app restores the last workspace and window state. | MoTeC workbooks and worksheets, Haltech pages, VCM Scanner layouts | Stock |
| W6-08 | P1 | ★ | **Graph quality.** Per-channel axis scaling with shared or separate axes, pause-and-scroll-back while still recording, cursor readout, channel list with search, colours that stay distinct in the Sun theme, graph templates saved by name. | SManager graph templates, MLV, PicoScope | Stock |
| W6-09 | P1 | | **3D views.** Shared OpenGL surface widget for W5-06 and for histogram results (W4-04). Falls back to a 2D heat map if OpenGL 2.1 is not available. | TunerPro, HTS, TunerStudio | Offline |
| W6-10 | P1 | | **Keyboard-first operation.** Every action has a shortcut; a searchable command palette; shortcut hints shown on buttons. Touchpad not needed for any driving task. | Crome (F-key driven), TunerStudio hotkeys | Stock |
| W6-11 | P1 | | **Trust indicators everywhere.** Unverified channels carry the amber `?` in tiles, plots, logs and exports; a banner states how many plotted channels are unverified. | — (specific to a guessed map) | Stock |
| W6-12 | P2 | | **Audio readout.** Optional spoken alarms and a rising tone tied to one channel, so nothing needs to be looked at while driving. | Shift-light tones in COBB/TunerStudio † | Stock |
| W6-13 | P2 | | **Second-screen dash.** Serve the driving view over the laptop's Wi-Fi to a phone on the dash. | TS Dash Echo, Hondata Mobile, TuneLogs | Stock |

---

## Performance bar

### What the commercial tools hit

| Tool | Figure | Source |
|---|---|---|
| Hondata KPro serial output | 22-byte packet, about 20 Hz at 9600 baud, about 30 Hz at 115200 | KManager help |
| Hondata KPro onboard logging | 5–50 Hz selectable, 100 Hz on KPro4; 2 MB flash; 10–110 min; up to 20 logs | KManager help |
| Hondata s300 onboard logging | 2 MB, about 20–60 min | SManager help |
| Hondata FlashPro | about 20 hours of onboard logging | Hondata product page |
| Link G4X | 1000 Hz internal; about 40 Hz PC logging; live view drops to 20–25 Hz while logging and lower with busy plots | Link forum thread |
| rusEFI-family firmware | 100 Hz default PC rate, up to 500 Hz; SD at 20 Hz default (under about 100 MB per hour) or 50 Hz | Project wiki |
| Speeduino | 119-byte realtime frame at 115200 baud (wire limit about 90 frames/s) | Speeduino wiki; rate is arithmetic |
| OBD2 over CAN (for scale) | 75–160 standard PIDs/s measured on a 2006 Civic with OBDLink adapters | scithings OBD rate paper |
| OBD2 over ISO 9141-2 | about 4–8 PIDs/s † (each exchange costs the ECU's reply delay plus a mandatory 55 ms idle gap †) | Background, not re-checked |
| Ostrich 2.0 | 921,600 baud; upload, download or verify in under 2 s for most ROMs | Moates |
| PicoScope 4425A | 400 MS/s, 250 M sample buffer, 10,000-waveform buffer | Pico specifications |
| MoTeC i2 Standard | 5 graphs × 10 channels, 2 histograms, 2 scatter plots | MoTeC |

No vendor publishes UI frame rates. The Link thread is the only data point: 30–60 Hz screen updates on ordinary laptops, falling when plots get busy.

### The 9600-baud K-line limit, worked through

9600 baud 8N1 moves 960 bytes per second, 1.04 ms per byte. A Honda read costs 5 request bytes plus `len + 3` reply bytes on the wire. The request's echo does not add time: it is the same bytes on the same wire. On top of wire time there is a fixed cost per request: ECU turnaround, USB and FTDI latency (1 ms timer set), and hondalog's 2 ms inter-request delay. Nobody has measured the P2P's turnaround, so the table uses 5 ms (good case) to 12 ms (poor case) for that fixed cost.

| Read | Wire bytes | Wire time | Per request | Rate |
|---|---|---|---|---|
| 1 byte | 9 | 9.4 ms | 14–21 ms | 47–70 reads/s |
| 2 bytes (RPM) | 10 | 10.4 ms | 15–22 ms | 45–65 reads/s |
| 8 bytes | 16 | 16.7 ms | 22–29 ms | 35–46 reads/s |
| 16 bytes (current block size) | 24 | 25.0 ms | 30–37 ms | 27–33 blocks/s |
| 48 bytes in one request, if the ECU allows it (untested) | 56 | 58.3 ms | 63–70 ms | 14–16 Hz |

What that means for real schedules:

- **Full sweep `0x00–0x2F` as three 16-byte blocks** (what v0.1.0 does): 90–111 ms, so **9–11 Hz**. This matches the simulator's 10 Hz. Adding a fourth block for the knock byte at `0x3C` drops it to about 7–8 Hz.
- **Hard ceiling for all 48 bytes:** about 17 Hz, reached only with zero overhead and one long read. No scheduler beats this.
- **Tiered schedule (W1-05):** fast set = RPM (`0x00`, 2 bytes) + MAP to O2 (`0x12–0x15`, 4 bytes) + injector and ignition (`0x24–0x26`, 3 bytes). That is 33 wire bytes and three requests: 49–70 ms, so **14–20 Hz** if nothing else is read. Interleaving one 16-byte slow block every fourth cycle gives **13–18 Hz on the six fast channels and about 1–1.5 Hz on each slow block** (ECT, IAT, battery, trims, flags).
- **Burst on one channel:** 45–65 Hz for RPM alone.

These numbers assume the guessed addresses. If the real P2P keeps fast channels further apart, the fast set costs another request (about 15–22 ms) per extra cluster.

For comparison, the fallback ISO 9141-2 path gives roughly 4–8 values per second in total †, so six channels would update about once a second. That is enough for diagnostics and for cross-checking the map, not for tuning.

### Realistic targets for this hardware

| Area | Target | Notes |
|---|---|---|
| K-line, full frame | 10 Hz | Already met in the simulator; confirm on the car |
| K-line, six fast channels | 15 Hz minimum, 20 Hz stretch | Needs W1-05 and W1-07 |
| K-line, slow channels | 1 Hz or better | |
| K-line, single-channel burst | 45 Hz or better | |
| Timestamp accuracy | ±2 ms per read | USB frame timing is the floor |
| Link errors | under 0.5% of reads on a healthy cable | Logged, not hidden |
| Stall capture (K-line) | trigger within 2 poll cycles; 30 s before, 10 s after | Exists; move to the trigger engine |
| Track B rails | 5 kHz sampling, 1 kHz min/max/mean stored, 1 ms dropout visible | Three orders below a PicoScope, enough for relay and switch faults |
| Track B edges | 10 µs resolution, no lost edges to 7,000 rpm | |
| Track B capture | 30 s before, 10 s after, written within 2 s of power loss | |
| Track B to K-line alignment | within 20 ms | |
| UI frame rate | 30 fps steady with 8 plots and 24 tiles; never below 20 fps | HD 4000 at 1366×768 |
| UI latency | under 100 ms from serial byte to pixel | One poll cycle is already 50–100 ms, so do not add more |
| CPU and memory | under 50% of one core; under 400 MB after 1 hour | |
| 3D table | 30 fps for 24×20 with live cursor | Trivial load for the GPU; keep the 2D fallback |
| Log size, native | about 2–6 MB per hour at 10–15 Hz with raw bytes | CSV export is about 5 MB per hour at 10 Hz × 25 channels |
| Log size, MLG export | about 1.5–2 MB per hour | 45-byte records at 10 Hz |
| Replay | open a 1-hour log in under 2 s; filter toggle under 200 ms | A 1-hour log is only 36,000–72,000 rows |
| Ostrich | 32 KB upload and verify under 3 s; cell edit live under 100 ms | Moates quotes under 2 s for most ROMs |

Two honest limits to state in the README:

1. On the stock ECU hondalog will be a 10–20 Hz logger. That equals Hondata's own 9600-baud stream and is well below anything on USB or CAN. It is fine for warm-up, trims, idle and stall context. It is too slow for transient fuelling or knock work; those belong to the P28 with a faster datalog link.
2. A K-line log cannot catch a millisecond power dropout: the ECU simply stops answering. Only Track B can show which rail fell first.

## Sources

Fetched in this session:

- kerpz ArduinoHondaOBD (protocol origin, tested ECUs): https://github.com/kerpz/ArduinoHondaOBD
- mr-sneezy fork (channel list on an S2000): https://github.com/mr-sneezy/ArduinoHondaOBD1_to_OBD2_BT
- ISO 9141-2 and KWP2000 on the K-line without an ELM327 (init sequence, framing, keepalive): https://matt2005.github.io/2019-12-28-repost-reading-obd2-data-without-elm327-part2
- OBD PID rate measurements on CAN: https://scithings.id.au/OBD_rate.pdf
- MLG binary log format 2.0: https://www.efianalytics.com/TunerStudio/docs/MLG_Binary_LogFormat_2.0.pdf
- MegaLogViewer HD histograms: https://efianalytics.com/MegaLogViewerHD/histograms.html
- MegaLogViewer HD formulas: https://www.efianalytics.com/MegaLogViewerHD/formulas.html
- EFI Analytics products and prices: https://www.efianalytics.com/register/browseProducts.jsp
- DIYAutoTune TunerStudio guide: https://diyautotune.com/blogs/tech-corner/tunerstudio-complete-guide
- HP Tuners math parameters: https://www.hptuners.eu/help/VCM-Scanner/math-parameters.html
- MoTeC i2: https://www.motec.com.au/i2/
- SManager s300 features: https://hondata.mobi/help/smanager/s300.htm
- SManager help contents: https://hondata.mobi/help/smanager/smanager_content_static.html
- KManager onboard datalogging: https://www.hondata.mobi/help/kmanager/onboarddatalogging.htm
- KManager datalogging parameters: https://www.hondata.mobi/help/kmanager/dataloggingparameters.htm
- KManager serial output: https://www.hondata.mobi/help/kmanager/serialoutput.htm
- KManager features: https://www.hondata.mobi/help/kmanager/introductionfeatures.htm
- Hondata FlashPro ILX: https://www.hondata.com/products/flashpro/flashpro-ilx
- Crome 1.6.x Pro/Dealer manual: https://ej9.ru/data/book/crome-v1.6.x-manual-pro-dealer.pdf
- Honda Tuning Suite features: https://hondatuningsuite.com/features/
- Moates Honda overview: https://shop.moates.net/pages/honda-overview-read-me
- Moates Ostrich 2.0: https://shop.moates.net/pages/ostrich-2-0
- Moates Data Trace: https://shop.moates.net/pages/about-the-data-trace-feature-of-our-chip-emulators
- TunerPro features: https://tunerpro.net/features.htm
- TunerPro v5 what's new: https://tunerpro.net/v5whatsnew.htm
- HP Academy on TunerPro: https://www.hpacademy.com/blog/what-is-tunerpro-and-tunerpro-rt/
- Honda i-HDS: https://techinfo.honda.com/Rjanisis/pubs/web/RJAAI001_TOOLS1.htm
- OBDLink app: https://www.obdlink.com/obd-apps/obdlink-app/
- Speeduino secondary serial interface: https://gitea.interbiznw.com/speeduino/wiki.js/src/branch/master/Secondary_Serial_IO_interface.md
- rusEFI-family wiki, data logging: https://0594faa0.wiki-7d7.pages.dev/Basic-Features/Data-Logging
- rusEFI feature status: https://wiki.rusefi.com/Dev-Status
- Link forum, runtime sample rate: https://forums.linkecu.com/topic/14789-runtime-sample-rate/
- Haltech datalog overview: https://support.haltech.com/portal/en/kb/haltech/elite-software-programmer/esp-main-setup/datalog-and-devices
- PicoScope 4000 series specifications: https://www.picoauto.com/de/products/automotive-oscilloscope-kit/picoscope-4000-series-specifications
- PicoScope alarm function walkthrough: https://hongtronics.com/en/?p=4027
- Snap-on PID Trigger: https://www.snapon.com/diagnostics/us/News-Center/PID-Trigger.htm
- Snap-on TRITON D10 scope capture: https://www.snapon.com/DiagnosticsManuals/TritonD10 AU/Content/TritonD10/D10Scope/Capturing_Data.htm

Not verified in this session (marked † above), each needs a check before code depends on it:

- Honda DTC bytes at `0x40–0x4F` and the `21 04 01` reset command. These are remembered from the kerpz `.ino` source, which could not be fetched.
- Whether a US-market P2P answers the Honda protocol on OBD2 pin 7.
- ISO 9141-2 timing limits and the resulting 4–8 PIDs/s.
- Crome QD2 datalog protocol details and its 38400 baud rate.
- Ostrich serial command set (Moates publishes it; the document was not fetched).
- `minipro` support for TL866II+/T48 with the SST27SF512, and the `0x8000` image offset.
- MSL text layout details and marker lines.
- Injector flyback voltage for the front-end rating.
- Innovate and AEM serial wideband protocols.
- Java-based EFI Analytics tools running on Linux.
