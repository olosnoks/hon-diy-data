# Integration notes (integrator's checklist, from the agents' final reports)

## Finishing pass, 2026-11-27 (second integrator pass): the open items closed

DONE
- [x] Phone / head-unit landscape. The stage publishes its design size (ui/shell/Stage.tsx StageSizeContext, COMPACT_BELOW = 720). 812x375 gives a 1386x640 stage, 1024x600 a 1140x668 stage (both compact), 1280x720 and 1366x768 the full 1366x768. F1 Live in compact mode: smaller tile metrics (value 19 px, sparkline 10 px, tighter padding, idle bar without its scale line) so 3 tile rows fit every group, and the tile grid scrolls inside its group as a last resort (it does not scroll at any of the four sizes). F2 big values shrink in compact mode; F4 Replay column widths come from the stage (vw/vh were window pixels, not design pixels), the THIS PULL panel is compact and scrolls if needed; F3/F5/F6 side panels use % of the stage instead of vw. The top bar tightens below a 1300 px stage (short stall text, less padding) so the 1140 px head-unit stage fits it. Checked in the browser with a script that flags anything outside the stage or cut by overflow:hidden on F1-F8 and every F7/F8 tab at 812x375, 1024x600, 1280x720, 1366x768: clean (the only report is F2's big digits, whose font line box is taller than the digits; nothing visible is cut).
- [x] Protocol "OBD2 only": Poller option obd2Only (with an isoFallback): no wake, no ECU-id read, no Honda probes; the 5-baud init starts at once and is retried every ISO_RETRY_S; mode stays 'obd2'; submitted Honda jobs are refused ("protocol is set to OBD2 only"). Controller passes obd2Only for protocol 'obd2' and a makeIsoFallback hook for tests.
- [x] Poke test pause / resume: Poller.pause()/resume() stop and restart the live block reads (submitted jobs still run, woken at once; no cycles while paused; no rate spike after). AppController.pauseLive()/resumeLive() (counted, survives a reconnect during the run, ECU detail says "live polling paused: poke test"); registerBytesDeps live = those. The runner's withLinePaused always resumes (stop or error included).
- [x] Event verdicts and notes in hondalog.db: core/store/eventnotes.ts writes the desktop app's rows (events kind 'event_note', key "kind@t" with t to 0.1 s as Python formats it, data = Python json.dumps of {note, verdict, kind, t, updated_unix}, key order and float repr as Python). Cross-checked both ways with the desktop's hondalog/analysis/eventnotes.py (web row read by Python, Python rows read by the web). DriveStore.loadEventNotes/saveEventNote; ui/events/notes.ts loads per day and writes through (the demo drives still use localStorage). Start-up moves any localStorage notes into the DB once (migrateLocalNotes; notes for drives the store does not have stay on the device).
- [x] Interrupted drive on reload: the app holds the "hondalog.db" Web Lock for the page's life; when it gets it (no other tab), start-up recovery marks every drive another session left "recording" as crashed at once (HondalogDb.recover(now, {foreign:true}), SqliteDriveStore.recoverInterrupted(), AppController.recovered). Another tab holding the lock: left to the heartbeat rule. hondalog.db is persisted on pagehide and on visibilitychange to hidden (wiring/index.ts flushOnHide).
- [x] Keep flag and imports: DriveInfo.keep (drives.keep), DriveStore.setKeep; drive picker Keep button (aria-pressed), "keep" in the notes column, deleting a keep drive asks twice and passes allowKeep. Import file… takes hondalog.db (importDb) or a CSV / MSL / MLG log (DriveStore.importFile: core/store/importlog.ts over the existing parseTextLog / readMlg and transfer.writeLog, status "imported", imported_from = file name) and selects the new drive. The picker's message line now shows over the empty-list text.
- [x] Bundle: main.tsx imports src/wiring (and the service worker registration) dynamically after the first paint. Main chunk 700.4 kB -> 195.8 kB (63.9 kB gzip). wiring chunk 352 kB (store + sql.js glue, link, cyd, expr, record, addressmap), sql-wasm 688 kB loaded by it; leaflet (settings-*.js, 171 kB) only with F4/F6, uPlot (58 kB) with F4/F5, esptool-js + pako (lib-*.js, 75 kB) only when Setup flashes the CYD.
- [x] Flaky tests made deterministic: replay "opens the newest drive" (waits for the lanes' uPlots, made in passive effects after the drive name renders; mock counter reset per test); obd2/clock "RealClock hits absolute deadlines" was the unidentified timing test (now on a controlled performance.now/setTimeout; a real-timer test only checks "never early"); ByteQueue timeouts on fake timers; LOD speed test checks the bound, not wall time; sim/real-clock poller, background writer and tile fetcher tests wait for their condition instead of a fixed sleep; wiring tests have longer condition timeouts. Full suite 3 runs in a row: 67 files, 1393 passed, 1 skipped, 0 failed each.
- [x] Replay: a remembered drive the store does not have (the demo drive shown before the store opened, now that the store loads in its own chunk) falls back to the newest; a drive opened from the demo source is replaced when the real store arrives.
- [x] Screenshots (simulator) in docs/screenshots/: 1366x768 F1-F8, 1280x720 F1/F2/F4, 1024x600 F1/F2/F4, 812x375 F1-F4 (old 1366-*.jpg removed).

STILL OPEN
- Not checked on hardware: KKL cable, CH340 CYD companion/relay, HL session on a real CYD, firmware flash, OBD2 fallback and OBD2-only on a real ECU.
- poller.obd2(fn) switch-back (Python) still not ported; OBD2-only is a start mode, not a runtime switch.
- Web event kinds differ from the desktop's for harsh driving ('harsh' cornering vs harsh_accel / harsh_brake), so those notes do not match across apps; stall / near_stall / dropout / key_off / vtec / mark keys match when both apps find the event at the same 0.1 s.
- No Web Locks API (old web views): start-up recovery assumes this page is the only one.
- Open from the lists below: pyproject 0.2.0 bump, KML/stall GeoJSON in store, report.py, Drive pinning, CYD-only theme work.

## Integrator pass, 2026-11-27: status

Wiring lives in `src/wiring/` (not `src/app/`: Windows would clash with `App.tsx`): maps.ts (MapStore), recordermap.ts,
pipeline.ts (MathEngine + AlarmEngine), obd2fallback.ts (IsoFallback), controller.ts (AppController), cydclient.ts
(Setup CydClient over HlClient), index.ts (startApp, deps registration, first connection), app.test.ts (5 tests).

DONE
- [x] startDemo() gone from main.tsx: startApp() + registerServiceWorker(). demo.ts stays only as a test fixture.
- [x] One FileStore interface (core/store/filestore.ts: list/remove required, description/persistent optional); platform/files.ts implements it; SqliteDriveStore.open(getFileStore()).
- [x] Address map: first run writes DEFAULT_MAP_TEXT to "addressmap.toml"; a stored map that fails falls back to the shipped one with a message; Setup > Files / F7 Bytes saveMap -> MapStore.save (validated), reconnects.
- [x] RecorderMap from #2's AddressMap (Channel.convert = #3 compiled formula); LivePipeline = #3 MathEngine + AlarmEngine from [[math]]/[[alarm]].
- [x] Poller on the picker's transport or createSimTransport({preset:'p2p-car'}) ("Simulator" button; auto when no granted cable and Setup > Cable > No cable is ticked). SIMULATOR badge replaces "demo data".
- [x] LiveSession fed from Poller events (onRead -> onBlock with t + latency, see below; onCycle -> decode -> onSample; onError; onPid). Session store: channels with min/max/history, raw bytes, ecu state/rate/errors/detail, alarms (engine active()), stall catcher status, recording state + drive row, marks, Esc acknowledge -> engine.
- [x] Recording starts when the link first comes up; REC / Ctrl+R toggle; Space marks go into the drive; captures -> stall.lastFile/lastT; drivesChanged() on start/stop/capture/import/delete.
- [x] CYD companion on a granted CH340 (auto, never the ECU port): GPS fixes -> gps stream (source handheld), handheld events -> LiveSession.onCydEvent; Relay started with the poller, active only when HELLO map hash = map hash; CYD chip status from the companion. Setup's CYD tab pauses the companion while it holds the port.
- [x] OBD2 fallback: #6 Iso9141Link + Obd2Session behind Poller.isoFallback (protocol auto/obd2; "honda" = off). ECU detail says "keep hondalog in front" during the 5-baud init and names a hidden page when the init failed.
- [x] setDriveSource(store) (#9); nav fields replayRequest/gpsFocus/powerFocus/replayDrive + openInReplay/showOnMap/openInPower moved into core/session.ts; ui/replay/nav.ts deleted, imports updated (events, replay, power, gps, tests).
- [x] registerBytesDeps (MemLink over poller.submit readMem/raw; live pause/resume no-ops: the poller interleaves) and registerSetupDeps (alarm source, HlCydClient, store, picker, map, platform capabilities, ConnectionControl). Registries moved to ui/bytes/deps.tsx and ui/setup/deps.ts so registering does not load the lazy screens.
- [x] Setup > Cable: new Connection card (Connect the KKL cable / Simulator / Disconnect, state line, OBD2 hint).
- [x] setExprCompiler(compileExpr) at start-up (#7).
- [x] format.ts units through core/units (US / Metric presets; MAP stays kPa).
- [x] Enter on F1/F2 opens the last caught stall in Replay at its trigger. Theme tab swatches are buttons (touch) - checked in the browser.
- [x] Unknown bytes seeded with the 2026-11-27 findings (0x0B.6, 0x0C.0, 0x0D, 0x0E, 0x16, 0x27, 0x2A, 0x2F, 0x32, 0x33, 0x35, 0x36, 0x37, 0x38, 0x3A.0, 0x3C, 0x80-0x8C, 0x86, 0x8F, 0x94-0x99, 0xA4.0): 44 entries.
- [x] Contracts: AlarmSpec extends AlarmRuleInit, MathSpec extends MathChannelInit (display fields required); DriveInfo.captureKind (set by driveInfo for captures); DriveStore.deleteDrive(name, {allowKeep}); Transport.kind drops 'tauri'; UsbSerialTransport.kind = 'webusb', usbLatencyS 0.001 on the FTDI driver; WebSerialTransport.port public (esptool-js).
- [x] @tauri-apps/api, @tauri-apps/cli and the "tauri" script removed from package.json (no npm install run: node_modules still holds them until the next install).
- [x] Planners merged: core/link/planner.ts re-exports #2's addressmap/plan (spans, DP, bus load, predicted rates); only the positional CostModel and BlockPlanner stay. Link golden tests pass.
- [x] Pull rule (both apps, decided 2026-11-27): detectPulls defaults throttle >= 50 % for >= 1.5 s in one gear, rpm rising; wot when >= 80 % for >= 1.0 s; ends at the throttle lift or where smoothed rpm stops rising (no 0.8 s tail); pull acceleration = local linear fit over 1.5 s inside the run; Pull.wot/label/key ("pull@t0"); gear tolerance 5 % + 0.5/vss, minKmh 5. Power screen classification uses pull.wot. Tests for the 23:03:07-shaped pull and 1st gear from 5 km/h.
- [x] index.html: manifest, icon-192, apple-touch-icon (base-relative after build; checked with HONDALOG_BASE=/hon-diy-data/).

BUG FIXED IN WIRING
- Poller's EcuRead.t is already "t minus latency"; LiveSession.onBlock subtracts the latency again. The controller passes t + latencyS so the DB gets the ECU sample time once.

NOT DONE / OPEN (as of the first pass; see the finishing pass above for what was closed)
- Not checked on hardware: KKL cable, CH340 CYD companion/relay, HL session on a real CYD, firmware flash, OBD2 fallback on a real ECU (all unit-tested against fakes only).
- ~~Protocol "obd2 only" behaves as "auto"~~ done.
- ~~Poke test live pause/resume are no-ops~~ done.
- ~~Events verdicts/notes in localStorage; no keep flag or CSV/MSL/MLG import~~ done.
- ~~Phone landscape (812x375): F1 Live clips the bottom tile row~~ done.
- ~~A drive left "recording" by a reload is marked crashed only once its heartbeat is stale~~ done.
- ~~Main JS chunk 700 kB~~ 196 kB.
- ~~Flaky tests~~ done.
- Open from the lists below: pyproject 0.2.0 bump, KML/stall GeoJSON in store, report.py, Drive pinning, CYD-only theme work.

## #12 platform (done)
- Android = Trusted Web Activity (Bubblewrap) over the Pages site; serial on Android via WebUSB JS drivers (FTDI FT232R, CH340). No Tauri, no Rust/NDK. Desktop browsers use Web Serial.
- Files: src/platform/{bytequeue,webserial,picker,files,pwa,index}.ts, src/platform/webusb/{driver,ftdi,ch340,transport}.ts, public/manifest.webmanifest, public/sw.js, public/icons/, android-twa/ (twa-manifest.json, assetlinks.template.json, build-apk.ps1 → builds in %USERPROFILE%\.hondalog-build\twa).
- FileStore interface defined in src/platform/files.ts (OPFS + memory). Reconcile with #4's store if it defines one.
- TODO integrator: index.html head: manifest link, icon-192, apple-touch-icon; src/main.tsx: registerServiceWorker(); drop @tauri-apps/api and @tauri-apps/cli from package.json; optionally add "vite/client" to tsconfig types; consider 'webusb' in Transport.kind (UsbSerialTransport currently reports 'webserial').
- Build was blocked by syntax errors in src/core/addressmap/pyfmt.ts (#2) at the time.
- Owner steps (held): sdkmanager --licenses + platform-tools/platforms;android-34/build-tools;34.0.0 (cmdline-tools at %LOCALAPPDATA%\Android\Sdk\cmdline-tools\latest); keytool keystore at %USERPROFILE%\.hondalog-keys\hondalog-release.jks; assetlinks.json at https://olosnoks.github.io/.well-known/assetlinks.json (needs the olosnoks.github.io repo); deploy Pages first; Bubblewrap may want JDK 17 (JDK 21 installed).
- Leftover: Rust installed on Windows before the scope change (rustup self uninstall if unwanted).
- Desktop Web Serial can't change the FTDI latency timer (Windows default 16 ms) — note for the UI.

## #8 shell, Live, Drive (done) — 55 tests
- session store src/core/session.ts (zustand): screen, theme, units, panel, ecu, cyd, stall, channels, history, raw, alarms, recording, marks, demo; actions setScreen/toggleTheme/setUnits/openPanel/closePanel/setEcu/setCyd/setStall/updateChannels/clearChannels/setRaw/setAlarms/acknowledgeAlarms/startRecording/stopRecording/setDrive/mark/escape/setDemo. Prefs in localStorage hondalog.prefs.
- main.tsx calls startDemo(): replace with real link/sim/recorder/CYD/alarms feeding the store.
- Placeholder index.tsx created for events/replay/power/gps/bytes/setup (owners overwrite).
- TODO: units via core/units (#3); theme toggle control in Setup for touch (#11); Enter → open caught stall in Replay (#9); Drive pinning not ported; launch margin uses an assumed 400 rpm stall line.
- Project-wide tsc blocked by src/core/addressmap/pyfmt.ts syntax errors (#2).

## Contract changes applied by the integrator (2026-11-27)
- Transport.kind gains 'webusb' (#12: set UsbSerialTransport.kind = 'webusb').
- Transport.read documented as read_some (resolve on first bytes) (#6).
- Transport.usbLatencyS?: number (#6; #12's FTDI WebUSB driver should report 0.001).

## #5 CYD (done) — 160 tests; HANDHELD.MAP byte-identical to Python on 6 maps; 25 frame types round-trip
- src/core/cyd/{bytes,format,serial_proto,expr,hhmap,bytecode,mapfile,hl_client,hl_proto,link,live,companion,relay,pushmap,drivefile,importer,sync,flash,fake_cyd}.ts.
- Platform must expose: PortSource {list(); open(info, baud|null) → new TransportPort(picker.open(info), baud ?? 921600, baud != null)}; FlashDevice = the W3C SerialPort (Web Serial) for esptool-js (close the CYD Transport first, flash(esptoolLoaderFactory(device))); open the CH340 with DTR and RTS both off (or both on) — RTS on + DTR off resets the board (#12's WebSerialTransport opens DTR+RTS on: OK, both on; WebUSB CH340 driver: check); MirrorStore for sync.
- TODO integrator: ImportStore/ImportDrive over #4's store; decode(channel, raw) from #3; decodePid from #6; CydLink raw reads → samples via the RecorderMap; Setup's CydClient over hl_client (#11); export mapFolderFiles for #11.
- Overlap: cyd/expr.ts + hhmap.ts duplicate #2/#3 for byte-exactness (keep; cross-check hashes).

## #4 triggers, recorder, store (done) — 88 tests; DB round-trips with Python verified both ways
- store/{schema,db,codec,zlib,writer,reader,library,transfer,drivestore,filestore,wasm.d.ts}.ts, export/ (MLG/CSV/MSL/GPX byte-identical to Python); record/{engine,presets,condition,recorder,live,testing}.ts.
- Two FileStore interfaces exist: store/filestore.ts (#4) and platform/files.ts (#12) — unify (platform implements store's; atomic replace). Then SqliteDriveStore.open(fileStore); optional setSqlJsWasm.
- TODO integrator: build RecorderMap from #2 AddressMap + #3 compileExpr(formula,{names:['x'],arrays:['b']}) (see record/testing.ts); wire LiveSession to Poller events (onBlock/onSample/onLinkError/onPid/feedGps/onCydEvent); OBD2 feedPid via #6 (obd2Stream()).
- Optional types.ts: DriveInfo.captureKind?: string (overtemp, lowbatt...). deleteDrive(name, {allowKeep}).
- Not ported: KML export (in gps/ by #10), redecode, logtool CLI, stall GeoJSON (gps/ by #10). Whole-file persist every 10 s (OPFS VFS later for big DBs).

## #11 Bytes + Setup (done) — 70 tests (jsdom only, NOT checked in a browser yet)
- F7 tabs: Scanner, Unknown bytes (38 seeded), Address map (form/TOML, comment-keeping edits, diff, hash), Verify (7 scripts), Rank, Poke test (poke/). F8 tabs: Alarms, CYD black box, Units, Cable, Files, Theme, About.
- TODO integrator: registerBytesDeps({link: MemLink over #1 poller, live: pause/resume, mapText, saveMap, scanner?}); registerSetupDeps({alarms, cyd: CydClient over #5 HL client (setup/cyd/client.ts), store, picker, mapText, saveMap, platform: capabilities()}).
- TODO: switch bytes/model/mapdoc.ts + formula.ts decoding to #2 addressmap / #3 expr (keep the comment-keeping editor); core/cyd export mapFolderFiles; seed Unknown bytes with the 2026-11-27 findings (index-bytes, output-bytes, analog-bytes, idle-810).
- Check both screens in a browser at 1366×768 and phone landscape.
- Project-wide tsc had ~268 errors in other modules (core/cyd, core/record) at that time — those agents still running.

## #10 Power + GPS (done) — 64 tests, checked in a browser; offline tile cache verified
- src/ui/power/ (adapter over #7's detectPulls/pullCurve/REFERENCE_D16Y8; Curve/Pull trace/Time in cell 2D), src/ui/gps/ (geo ports, GPX/KML/GeoJSON exporters golden-matched, tiles.ts Cache Storage LRU 300 MB, max 2 connections, offline switch).
- Depends on #9's replay/source.ts useDriveSource and replay/nav.ts useReplayNav: update imports if moved into session.ts.
- TODO: gps/save.ts uses Blob anchor download (fine for web/TWA). detectPulls trims ~1.3 s off WOT pull ends (#7) — check minimum trim. Confirm DriveInfo.startedUnix = wall time of t=0 (#4).
- Bug noted by #9 (Leaflet clearRect after hot reload) — dev-only? verify at integration.

## #9 Replay + Events (done) — 39 tests, checked in a browser
- src/ui/replay/{index,Lanes,ReplayMap,DrivePicker,GG,calc,channels,fmt,nav,source,fixture}.tsx/ts; src/ui/events/{index,detect,Timeline,notes}.
- Uses #7's core/analysis/replay.ts (own copy deleted) and #10's gps/MapView.
- TODO integrator: setDriveSource(store) in src/ui/replay/source.ts once #4's store is open; drivesChanged() after record/delete/import.
- TODO: move nav fields to core/session.ts: replayRequest, gpsFocus, powerFocus, replayDrive (#10 F5/F6 should read gpsFocus/powerFocus); verdicts+notes to the DB events table (now localStorage hondalog.events.notes); DriveStore lacks keep flag and CSV/MSL/MLG import; units via core/units.
- Bug for #10: Leaflet canvas "clearRect of undefined" after hot reload in MapView.

## #13 release scripts (done)
- scripts/release/{deploy-pages,build-all,publish-release,make-release-notes,create-keystore,sign-apk}.ps1, build-firmware-wsl.sh, wsl-sync.sh, README.md; packaging/linux/{hondalog.spec,build-linux.sh}.
- Verified: Pages dry run (local gh-pages branch with 2 unpushed dry-run commits: `git branch -D gh-pages` before the real deploy or leave it; real deploy starts from origin/gh-pages), firmware build reproducible, Linux tar.gz 171 MB (runs in WSL), Windows zip, web zip.
- Integrator done: release/ and firmware-out/ in root .gitignore; webusb transport cast fixed; twa-manifest path fixed.
- TODO: bump pyproject.toml to 0.2.0 (desktop + web versions match) when merging; tsc errors remaining in src/core/cyd/bytecode.ts (#5 still running).
- Owner one-time: WSL `sudo apt-get install -y libpng-dev libxcb-cursor0 libfuse2t64`; create-keystore.ps1; Android sdkmanager licences + packages; deploy-pages.ps1 -ConfigurePages after the first deploy; assetlinks.json on olosnoks.github.io.

## #1 link + sim (done) — 126 tests, golden vs Python (frames, sim RNG, whole Link and Poller runs)
- src/core/link/{protocol,clock,timing,planner,scheduler,link,poller,testmap}.ts; src/core/sim/{pyrandom,model,transport}.ts. createSimTransport({preset:'p2p'|'p2t'|'iso'|'flaky'|'p2p-car', seed, clock}) for the demo.
- Poller events: onRead, onLink, onError, onCycle, onPid; submit(fn), burst, setExpertUnlock.
- TODO: wire #6's ISO session into poller IsoFallback/IsoLiveSession; poller.obd2(fn) switch-back not ported; unify planner with #2's plan.ts via opts.planner (BlockPlanner); usbLatencyS/read_some contract now applied (#1 can drop its cast).
- Integration: main.tsx should feed the session store from Poller (sim or real transport) instead of startDemo().

## #3 expr, math, alarms, units (done) — 348 tests, golden vs Python
- Math channels in src/core/expr/mathch.ts (MathEngine). Alarms: src/core/alarms (AlarmEngine, snake_case AlarmRule). Units: src/core/units (US preset keeps MAP in kPa like Python; inHg = one line in units/display.ts).
- TODO integrator: AlarmSpec = AlarmRuleInit from @core/alarms and MathSpec = MathChannelInit (both #2 and #3 suggest it); #8 switch format.ts unit conversion to core/units (formatValue ported too).
- Edge: alarm templates print TOML ints as floats ("105.0").

## #2 address map (done) — 256 tests (+1 live-map test, env HONDALOG_LIVE_MAP)
- versionHash matches Python: shipped P2P 071de5244d2b, P2T 56fc65a6cba5, P28 d58d503473f1, owner's live map 8f3b481ce1dd.
- src/core/addressmap/{addressmap,plan,framing,groups,default-map,pyfmt}.ts, mapping/{status,find}.ts. Uses #3's src/core/expr.
- TODO integrator: widen AlarmSpec in types.ts to the Python alarm fields (severity warn/critical, low/high/rate, gate, hysteresis, actions) — coordinate with #3's alarms engine; AddressMap.protocol is a MapFraming superset; status lower case.
- TODO: #1 link can use loadDefault() in place of src/core/link/testmap.ts; first run writes DEFAULT_MAP_TEXT to storage (platform) then loads(text, variant, path).

## #6 OBD2 + diag (done) — 95 tests
- src/core/obd2/{iso9141,obd2,dtc,clock}.ts, testing/fakeEcu.ts, golden/decoders.json; src/core/diag/{firstcontact,ecuprobe,trace,probe,adapter,honda,report}.ts.
- Clear codes: UI must show codes + CLEAR_WARNING, require typing CLEAR, call clearDtcs(text); never allowEngineRunning (#11).
- 5-baud init fails in a hidden/minimised tab: UI must say "keep the app in front while connecting".
- firstcontact takes makeLink: swap SimpleHondaLink for #1's link at integration.
- Browser can't read the FTDI latency or clone check: report says so.

## #7 analysis (done) — 43 tests
- src/core/analysis/{series,lod,cursors,histogram,scatter,filters,compare,replay,gear,dynamics,power,grade,events,fixtures,index}.ts
- TODO integrator: setExprCompiler((text) => ({deps, mask(env,t)})) from src/core/expr at startup (mask false where no value).
- Event kinds expected: marker, trigger, alarm. Channel names assumed: rpm, vss, tps, map, iat, inj, brake, eld, vtec, idle_target?, bb_ig1?
- Not ported: report.py (HTML capture report). Trigger alignment uses trigger events only (no meta in LoadedDrive).

## #14 CYD firmware Dracula (done)
- Image: firmware-out/dracula/hondalog-cyd-dracula-merged.bin (893,568 B, sha256 2E120BE3…676A), flash at 0x0. Not flashed. firmware-out/ is untracked (don't commit; add to .gitignore).
- Changed: firmware/handheld/main/ui/{ui_theme.h,ui_theme.c,ui.c,ui.h,ui_input.h,ui_map.c,ui_perf.c}, sim/sim_main.c, docs/screenshots (46 renamed dracula-*/darker-*), docs/handheld.md, README.md (CYD screenshot path), HARDWARE.md.
- Theme saved in NVS key `night` (0 → Dracula, 1 → Darker); hold 3 s toggles. Water colour #55809F added (map). Borders #6272A4 on the CYD (TN panel), not #44475A.
- Owner decision (2026-11-27): YES, use CYD-only on_alarm #191A21 (5.5:1) on the CYD. DONE: ui_theme.c, docs/handheld.md, 4 screenshots; image rebuilt: firmware-out/dracula/hondalog-cyd-dracula-merged.bin, 893,568 B, sha256 f0c58b0e9bd1ac316c70ba14513b9c79836fc5b98804844106db95445b0a2293 (not flashed; uncommitted firmware changes in the worktree).
- WSL ~/hh-build now on web-port with UI files copied, sim deleted there; prior local state in git stash "pre-dracula local state".

## Byte findings to fold into the map (both apps), 2026-11-27 reports
- 0x27 = ignition correction, 1/4° counts (knock control under load); 0x3C = knock-sensor level (noisy, rises with rpm). index-bytes.md.
- 0x2F = purge ramp / enable timer (810 rpm purge idle follows when it completes); 0x2A = learned idle air; 0x0B b6 = O2 heater; 0x0C b0 = alternator control (ALTC); 0x16/0x0D/0x0E dead pending a cold start. output-bytes.md, idle-810.md.
- 0x32/0x37 = fixed ~0.88 V analog input; 0x33 = purge ECT threshold (67.5 °C); 0x3A b0 = post-start / O2-active flag?; 0x80-0x99 monitor status. analog-bytes.md.
- The DB keeps raw block bytes in samples.raw (there is no ecu_reads table) — check the web store matches.
