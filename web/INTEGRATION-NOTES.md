# Integration notes (integrator's checklist, from the agents' final reports)

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
- Owner decision (2026-11-27): YES, use CYD-only on_alarm #191A21 (5.5:1) on the CYD; rebuild the image after.
- WSL ~/hh-build now on web-port with UI files copied, sim deleted there; prior local state in git stash "pre-dracula local state".

## Byte findings to fold into the map (both apps), 2026-11-27 reports
- 0x27 = ignition correction, 1/4° counts (knock control under load); 0x3C = knock-sensor level (noisy, rises with rpm). index-bytes.md.
- 0x2F = purge ramp / enable timer (810 rpm purge idle follows when it completes); 0x2A = learned idle air; 0x0B b6 = O2 heater; 0x0C b0 = alternator control (ALTC); 0x16/0x0D/0x0E dead pending a cold start. output-bytes.md, idle-810.md.
- 0x32/0x37 = fixed ~0.88 V analog input; 0x33 = purge ECT threshold (67.5 °C); 0x3A b0 = post-start / O2-active flag?; 0x80-0x99 monitor status. analog-bytes.md.
- The DB keeps raw block bytes in samples.raw (there is no ecu_reads table) — check the web store matches.
