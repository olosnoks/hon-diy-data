# Competitor survey for hondalog

Scope: tools a 2000 Civic EX owner could buy or copy ideas from, plus the general logging, standalone-ECU and scope tools that set the bar for features and UX.

How to read this file:

- Plain text is taken from a page fetched in this session (URLs at the bottom).
- A dagger (†) marks a claim from background knowledge that was not re-checked in this session. Treat those as "probably right, verify before relying on it". Prices marked † are rough.
- "Applies to stock P2P?" says whether the product itself can talk to the stock OBD2B ECU. Almost none of the Honda tuning tools can: they all assume a chipped OBD1 ECU.

## 1. Honda-specific

| Product | Platform | Price | Applies to stock P2P? | Standout features worth copying |
|---|---|---|---|---|
| Hondata s300 + SManager | Windows, USB (Bluetooth on v3) | $595 for the module, ECU not included | No. Daughterboard inside an OBD1 P28/P30/P72 etc. | 2 MB onboard log memory, about 20–60 min. Real-time ECU update. Lambda overlay on the fuel table, sensor overlay and "sensor tracing" on tables. Datalog-to-laptop with configurable trigger conditions. 24×20 low and high cam tables. Graph templates, datalog comments, export datalog, export movie, Compare Calibrations, Error Codes window with Clear DTCs. |
| Hondata KPro + KManager | Windows, USB | about $1,000+ † | No (K-series) | Log is a sequence of "frames", each a full sensor snapshot. Onboard logging 5–50 Hz selectable (100 Hz on KPro4), 2 MB flash, 10–110 min, up to 20 logs. Start trigger = RPM and throttle; stop trigger = RPM and speed; or a manual switch. 8 analog inputs. Serial "datalogger output": one 22-byte packet, about 20 Hz at 9600 baud and about 30 Hz at 115200. |
| Hondata FlashPro + FlashProManager | Windows, USB to OBD2 port | $795 (ILX example) | No (2006+ CAN cars) | Reflash through the OBD2 port, about 20 hours onboard logging, live tuning, DTC read and clear. Shows the modern expectation: one box that tunes, logs standalone and acts as a scan tool. |
| Crome / Crome Pro / Dealer | Windows | Free; Pro and Dealer are paid licences | No. Chipped OBD1 only | Datalog over serial (QD2/QD3 protocol) with a sampling-priority setting. Target Lambda, Lambda Readings and Lambda Differential (% error) views on F10/F11/F12. Auto-Adjust Maps and Live Auto Adjustment with thresholds. "Smart Tracking" cell modes (quad cell, row, column). Lock cells, map smoothing, knock recording, live plotter, two telemetry graphs with a playback slider, timestamped auto-saved logs, CSV export, real-time programming with Ostrich/ROMulator, plugin manager. |
| eCtune | Windows | Per-vehicle licence † | No. Chipped OBD1 only | Described by Moates as advanced but poorly maintained. Known for a clean live-tuning UI and a built-in dyno-style plot †. |
| Neptune RTP (+ Demon) | Windows | Per-vehicle licence, bundled with Demon hardware † | No. Chipped OBD1 only | Moates calls it the most supported option for serious OBD1 setups. Demon adds onboard logging and emulation in one board †. |
| BRE, TurboEdit, Uberdata, FreeLog | Windows | Free | No | BRE and TurboEdit target OBD0 (PR3/PW0/PM6). Uberdata is an older OBD1 editor. FreeLog is a free logger that works with Crome ROMs. Useful mainly as proof that the OBD1 datalog protocol is simple and well understood. |
| Honda Tuning Suite (HTS) | Windows † | Free | No. OBD1 | "Fast datalogging of 90 parameters". Three wideband inputs with direct Innovate support. Row/column interpolation, smoothing, snapshots for rollback, "Map Trails" (where the ECU has been on the table), Auto Adjust from wideband versus target AFR, live AFR and boost graphs, graph templates, 2D and 3D table graphs. |
| Moates Ostrich 2.0 | USB hardware; used by Crome, eCtune, BRE, TunerPro RT | about $175 † | With the P28 only | Emulates 27C32–27C512, 27C010, 29F040. 921,600 baud. Upload, download or verify in under 2 s for most ROMs. 64 KB banks. ROM trace and window trace (which addresses the ECU reads); Moates itself calls trace imprecise and a last resort. Access time 65–80 ns. |
| Moates HULOG / Hondalog cable | USB serial to CN2 header | about $40–60 † | With the P28 only | Just a level-shifted USB serial link to the ECU's CN2 port. Needs a datalog-capable ROM and a CN2 header soldered in. |
| TunerPro / TunerPro RT | Windows | Free download, optional registration | With the P28 only, and only with a matching XDF | XDF definitions (tables, scalars, flags, axis linking, 32-bit types). ADX datastream definitions with configurable math. Compare the current bin against up to 4 others, difference list. 3D surface with multi-point drag, rotate, zoom, smoothing with user alpha. RT adds emulator support (Ostrich, Ostrich 2.0, Demon, others), dashboards, histograms, proportional "bubble" trace on tables, record and playback, side-scrolling history charts. Quality depends entirely on the definition file. |
| Honda HDS / i-HDS (PGM Tester successor) | Windows + Honda VCI | $45/day, $200/30 days, $1,800/year | Yes for diagnostics † (coverage of a 2000 car under current i-HDS not checked) | DTC read and clear, Data List, snapshots (data capture), function tests, reprogramming. The reference for what the factory considers "complete" diagnostics. |
| kerpz ArduinoHondaOBD (and forks) | Arduino | Free, open source | Tested on P2T (OBD2) and chipped P30, not P2P | The origin of hondalog's address map. Reads RPM, speed, timing, O2, ECT, IAT, IACV, knock, injector duration, flag bits; has a fault-code page and an ECU reset. Emulates an ELM327 so Torque can connect. |

## 2. General logging and analysis

| Product | Platform | Price | Standout features worth copying |
|---|---|---|---|
| MegaLogViewer MS / HD (EFI Analytics) | Windows, macOS, Linux (Java) † | MS $49.95, HD $69.95 list ($34.95 / $49.95 on sale) | The de facto log viewer. HD loads any delimited ASCII log. Histogram / table generator: any field on X, Y and Z, adjustable table size, a second Z field for deltas (e.g. AFR minus target), toggled filters with instant re-render. Custom fields: `[Field]` references, `[Field-1]` for earlier records, `smoothBasic`, conditionals with `? :`, built-in HP and drag functions. Scatter plots, VE Analyze from logs, live file trailing. |
| TunerStudio MS / Ultra | Windows, macOS, Linux (Java) † | MS $89.95, Ultra $129.95 list | Dashboard designer with gauges, shift lights and warning indicators. VE Analyze Live auto-tune. Ultra adds an integrated log viewer, log playback, dyno and tuning views, custom channels, event-triggered actions. Tooth and composite loggers, injector/coil/relay output tests. TS Dash is a dashboard-only build for permanent installs. |
| HP Tuners VCM Scanner | Windows + MPVI interface | Interface about $400 + credits † | Layouts made of charts, gauges, tables and histograms †. Math parameters: up to 10 user-defined, `[channel]` references, `avg(ms)` and `slope(ms)` time-window modifiers, relational and logical operators; negative windows look forward during playback. |
| EcuTek ProECU | Windows + dongle, phone app | Licence per car † | Custom maps logged alongside OEM parameters, map-switching, phone dashboard † (not researched in depth; no stock-Honda relevance). |
| COBB Accessport | Handheld | about $700 † | The "one-glance" benchmark: six configurable live gauges on a handheld, shift light, logging to internal memory, DTC read/clear † (not researched in depth). |
| Torque Pro | Android + ELM327 | about $5 † | Themeable dashboards, custom PIDs with formulas, fault codes, CSV logging with GPS †. kerpz's project exists partly to feed it. |
| OBDLink app / OBDwiz | Android, iOS, Windows + OBDLink adapter | Free with adapter | Customisable dashboards with templates, DTC read/clear, freeze frame with a saved report, readiness monitors, CSV logs, GPS track coloured by a chosen parameter, custom PIDs, graph of up to four live PIDs. |
| ScanXL, AutoEnginuity | Windows | about $90–300 † | Generic OBD2 plus paid enhanced-manufacturer packs, with bidirectional tests † (not researched in depth). |
| AiM Race Studio 3 | Windows; phone apps | Free with AiM hardware † | Lap-based analysis: track map, lap overlay, time-compare, math channels, histograms and scatter † (the page fetched only confirmed platform details). |
| MoTeC i2 Standard / Pro | Windows | Standard free with MoTeC hardware; Pro by licence | The analysis ceiling. Standard: up to 5 graphs with 10 channels, dual cursors, 2 histograms, 2 scatter plots, 1 mixture map, CSV and Matlab export, workbooks and worksheets. Pro: unlimited everything, multi-lap overlays with alignment, data gating, linked cursors and zoom, FFT, rainbow track maps, equation editor, automatic unit conversion, video overlay. |

## 3. Standalone ECU software

| Product | Platform | Price | Standout features worth copying |
|---|---|---|---|
| Haltech NSP / ESP | Windows | Free with ECU | Onboard logging that is continuous or conditional on a channel or switch, plus laptop logging. Page-based workspace layouts †. |
| Link G4X / PCLink | Windows | Free with ECU | Internal logging up to 1000 Hz, PC logging around 40 Hz (forum figures). Users report the live rate falling to 20–25 Hz while PC logging and lower with busy time plots: a real-world reminder that UI load limits the stream. |
| AEM Infinity (InfinityTuner, AEMdata) | Windows | Free with ECU | AEMdata is a separate analysis package; not researched in depth †. |
| Speeduino | Open source, TunerStudio front end | Free | Simple, documented serial protocol worth copying for Track B: one ASCII command returns a fixed realtime packet (`A` = 75 bytes, `n` = 119 bytes with a type and length header), `r` reads any offset and length. 115200 8N1. |
| rusEFI / FOME | Open source, TunerStudio front end | Free | Logging profiles; triggered logging (manual, simple threshold, expression such as RPM > 6000 and TPS > 90); MLG logs with the tune embedded; 100 Hz default PC rate and up to 500 Hz; SD logging at 20 Hz by default (under about 100 MB per hour) or 50 Hz; high-speed trigger log mode; bench tests for coils, injectors, pump, fan; Lua scripting; Innovate serial and AEM CAN wideband input. |

## 4. Scopes and stall-hunting

| Product | Platform | Price | Standout features worth copying |
|---|---|---|---|
| PicoScope 4225A / 4425A + PicoScope 7 Automotive | Windows, macOS, Linux † | $1,057 (2 ch), $1,691 (4 ch), scope only | 20 MHz, up to 400 MS/s, 12-bit, 250 M sample buffer, ±50 mV to ±200 V. Waveform buffer of up to 10,000 captures. Triggers: edge, window, interval, pulse width, level dropout, window dropout, rise/fall time, logic. Masks auto-generated from a good waveform. Alarms run actions on capture, buffer-full or mask-fail: for example "save all buffers" then "recapture", for unattended long tests (with a gap while files are written). Math channels including duty and frequency over time. Serial decode including UART and CAN. |
| Snap-on scan tools (VERUS, ZEUS, TRITON) | Dedicated tablet | Several thousand dollars † | PID Trigger: set a minimum and maximum on any PID, arm it, drive; when the value leaves the band the tool saves a recording, keeps a configurable number of frames after the event, and marks the trigger on the graph. Scope keeps a rolling frame buffer; pause and scrub back; peak detect raises the sample rate to catch fast glitches. |
| Autel MaxiScope and similar | Tablet add-on | about $500–700 † | Budget 4-channel scope with presets per sensor † (not researched). |
| K-line / CAN sniffers | Various | — | For this car the "sniffer" is the KKL cable itself in listen-only mode. Useful once: record what a real scan tool sends. |

## 5. What the survey says, in short

1. Every commercial Honda tool for this engine family assumes a chipped OBD1 ECU. None of them logs a stock P2P. Track A is not a clone of anything; the closest prior art is kerpz's Arduino code and the factory tester.
2. On the analysis side the bar is clear and shared across products: time graphs with cursors, a table histogram (X, Y, Z with a delta option and filters), scatter plots, user math channels, two-log overlay. MegaLogViewer HD does all of this for $70 and reads plain CSV and MLG, so hondalog should export to it on day one and only then build its own versions.
3. Trigger-and-save is the standard answer to intermittent faults everywhere: Hondata (RPM and throttle), rusEFI (threshold or expression), Snap-on (PID out of band), Pico (dropout trigger plus alarm actions). hondalog's stall catcher is the same idea with one hard-coded trigger; it needs to become a general trigger engine.
4. Stream rates worth knowing: Hondata's own 9600-baud stream is about 20 Hz; PC logging on a Link is about 40 Hz; rusEFI defaults to 100 Hz. A polled 9600-baud K-line lands at 10–20 Hz. That is the same class as Hondata's serial output and slower than everything on USB or CAN.
5. Lab scopes win on electrical faults by three to four orders of magnitude in sample rate. Track B does not need to match that; it needs dropout detection of about 1 ms on four rails, always on, with a pre-trigger buffer, which no scan tool offers.

## Sources

Fetched in this session:

- Hondata s300 product page: https://hondata.com/s300
- SManager help, s300 feature page: https://hondata.mobi/help/smanager/s300.htm
- SManager help, contents: https://hondata.mobi/help/smanager/smanager_content_static.html
- KManager help, datalogging intro: https://www.hondata.mobi/help/kmanager/introductiontodatalogging.htm
- KManager help, onboard datalogging: https://www.hondata.mobi/help/kmanager/onboarddatalogging.htm
- KManager help, onboard datalogging parameters: https://www.hondata.mobi/help/kmanager/dataloggingparameters.htm
- KManager help, features: https://www.hondata.mobi/help/kmanager/introductionfeatures.htm
- KManager help, serial output: https://www.hondata.mobi/help/kmanager/serialoutput.htm
- Hondata FlashPro ILX: https://www.hondata.com/products/flashpro/flashpro-ilx
- Crome 1.6.x Pro/Dealer manual (first 100,000 characters read): https://ej9.ru/data/book/crome-v1.6.x-manual-pro-dealer.pdf
- Moates Honda overview: https://shop.moates.net/pages/honda-overview-read-me
- Moates Ostrich 2.0: https://shop.moates.net/pages/ostrich-2-0
- Moates on the Data Trace feature: https://shop.moates.net/pages/about-the-data-trace-feature-of-our-chip-emulators
- Honda Tuning Suite features: https://hondatuningsuite.com/features/
- TunerPro features: https://tunerpro.net/features.htm
- TunerPro v5 what's new: https://tunerpro.net/v5whatsnew.htm
- HP Academy on TunerPro and TunerPro RT: https://www.hpacademy.com/blog/what-is-tunerpro-and-tunerpro-rt/
- Honda service tools (i-HDS): https://techinfo.honda.com/Rjanisis/pubs/web/RJAAI001_TOOLS1.htm
- kerpz ArduinoHondaOBD: https://github.com/kerpz/ArduinoHondaOBD
- kerpz ArduinoHondaUNI (mirror): https://github.imc.re/kerpz/ArduinoHondaUNI
- mr-sneezy fork (S2000, extra channels): https://github.com/mr-sneezy/ArduinoHondaOBD1_to_OBD2_BT
- MegaLogViewer HD histograms: https://efianalytics.com/MegaLogViewerHD/histograms.html
- MegaLogViewer HD formulas: https://www.efianalytics.com/MegaLogViewerHD/formulas.html
- EFI Analytics product and price list: https://www.efianalytics.com/register/browseProducts.jsp
- DIYAutoTune TunerStudio guide: https://diyautotune.com/blogs/tech-corner/tunerstudio-complete-guide
- HP Tuners math parameters: https://www.hptuners.eu/help/VCM-Scanner/math-parameters.html
- OBDLink app: https://www.obdlink.com/obd-apps/obdlink-app/
- MoTeC i2: https://www.motec.com.au/i2/
- AiM software page (platform details only): https://aimshop.com/pages/race-studio-3-analysis-math-channel
- Haltech datalog overview: https://support.haltech.com/portal/en/kb/haltech/elite-software-programmer/esp-main-setup/datalog-and-devices
- Link forum, runtime sample rate: https://forums.linkecu.com/topic/14789-runtime-sample-rate/
- Speeduino secondary serial interface: https://gitea.interbiznw.com/speeduino/wiki.js/src/branch/master/Secondary_Serial_IO_interface.md
- rusEFI-family wiki, data logging: https://0594faa0.wiki-7d7.pages.dev/Basic-Features/Data-Logging
- rusEFI feature status (page says it is partly out of date): https://wiki.rusefi.com/Dev-Status
- PicoScope 4000 series automotive specifications: https://www.picoauto.com/de/products/automotive-oscilloscope-kit/picoscope-4000-series-specifications
- PicoScope alarm function walkthrough: https://hongtronics.com/en/?p=4027
- PicoScope 7.1.50 release note (Test & Measurement edition): https://www.cambridgenetwork.co.uk/news/new-stable-release-picoscope-7150-brings-advanced-tools-and-enhanced-performance
- Snap-on PID Trigger: https://www.snapon.com/diagnostics/us/News-Center/PID-Trigger.htm
- Snap-on TRITON D10 scope, capturing data: https://www.snapon.com/DiagnosticsManuals/TritonD10 AU/Content/TritonD10/D10Scope/Capturing_Data.htm

Not reachable or not read (so nothing here relies on them): tunerstudio.com and efianalytics.com product home pages, picoauto.com software page, hptuners.com VCM Scanner page, the kerpz `.ino` source file, support.moates.net (redirects to the shop), a Vehicle Service Pros article on PID triggers (robots.txt). EcuTek, COBB, ScanXL, AutoEnginuity, AEM, Autel and Torque Pro were not fetched at all; their rows are background knowledge only.
