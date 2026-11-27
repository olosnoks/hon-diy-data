# hondalog web port: guide for the porting agents

One React + TypeScript app (Vite) is the whole of hondalog on every platform:

- **web**: GitHub Pages, serial over **Web Serial** (Chrome/Edge desktop).
- **Windows, Linux, Android**: the same build inside **Tauri v2** (system web view),
  serial through a Tauri serial plugin behind the same `Transport` interface.
  **No Electron.**

All four must be functionally and visually identical: same screens, same
keys, same theme, same files. The only differences allowed are how a serial
port is opened and where files are stored, both hidden behind `src/platform/`.

## Ground rules (every agent)

1. Work only in your own folders (table below). Read anything.
2. **Do not edit** `package.json`, `package-lock.json`, `tsconfig.json`,
   `vite.config.ts`, `src/core/types.ts`, `src/ui/theme.ts`. Need a dependency
   or a contract change? Say so in your final message with the exact change;
   the integrator applies it. Never run `npm install`.
3. Port behaviour from the Python sources named for you, including edge
   cases and their tests: port the relevant `tests/*.py` cases to Vitest
   (`*.test.ts` next to the code). Run only your own tests:
   `npx vitest run src/core/<yours>` from `web/`.
4. Byte-for-byte compatibility where the Python app or the CYD reads the
   result: the K-line frames, `version_hash`, the HANDHELD.MAP binary, the
   CYD serial/HL protocols, the SQLite schema. Write golden tests against
   output from the Python code (run it with `..\..\hon-diy-data\.venv\Scripts\python.exe`
   from this worktree, read-only) where you can.
5. Theme: only `src/ui/theme.ts` tokens (Dracula / Darker). Never white or
   light surfaces. Fonts: Inter and JetBrains Mono (bundled, no network).
6. Layout target 1366 x 768; must also work on a phone in landscape
   (Android) and scale up on large monitors.
7. No network at runtime except OpenStreetMap tiles on the GPS map
   (cached) and nothing else. No analytics. No GPS data leaves the device.
8. Do not commit, push or switch branches. Do not touch the running app's
   checkout (`..\hon-diy-data`), the serial ports, or `~/.config/hondalog`.
9. Final message: what you built, what passes, what's left, and any
   contract/dependency change you need.

## Who owns what

| # | Area | Folder(s) | Port from (Python) |
|---|---|---|---|
| 1 | K-line link, framing, poller, simulator | `src/core/link/`, `src/core/sim/` | `hondalog/link/`, `protocol.py`, `poller.py`, `transport.py`, `sim.py` |
| 2 | Address map, block planner, version hash | `src/core/addressmap/` | `addressmap.py`, `hondalog/data/addressmap.toml`, `mapping/` (status, find) |
| 3 | Expression engine, math channels, alarms, units | `src/core/expr/`, `src/core/alarms/`, `src/core/units/` | `hondalog/expr/`, `mathch/`, `alarms/`, `ui/units.py`, `units.py` |
| 4 | Triggers, recorder, drive store (SQLite) | `src/core/record/`, `src/core/store/` | `triggers/`, `logger.py`, `live.py`, `hondalog/data/` (schema, views, export) |
| 5 | CYD black box: serial proto, HL session, relay, map compile, flash | `src/core/cyd/` | `hondalog/handheld/` (serial_proto, hl_client, relay, mapfile, format, companion), esptool-js |
| 6 | OBD2 ISO 9141-2, first contact, probe | `src/core/obd2/`, `src/core/diag/` | `hondalog/diag/`, ISO parts of `link/` |
| 7 | Analysis: events, power curve, g-forces, gear, fuel, replay model | `src/core/analysis/` | `analysis/`, `ui/replaymodel.py`, docs/analysis/2026-11-26 methods (vehicle-dynamics, drive-idle-control, drive-timeline) |
| 8 | App shell, theme, nav F1-F8, status bar, keys, Live (F1), Drive (F2) | `src/ui/shell/`, `src/ui/live/`, `src/ui/drive/`, `src/main.tsx`, `src/App.tsx`, `index.html` | `ui/main_window.py`, `chrome.py`, `tiles.py`, `screens/sensors.py`, `screens/drive.py`, design canvas |
| 9 | Replay (F4), Events (F3) | `src/ui/replay/`, `src/ui/events/` | `screens/replay.py`, `drivepicker.py`, design canvas |
| 10 | Power (F5), GPS map (F6) | `src/ui/power/`, `src/ui/gps/` | `screens/map3d.py`, `screens/gpsmap.py`, `mapview.py`, `geo/` |
| 11 | Bytes (F7), Setup (F8) incl. CYD tab and alarms | `src/ui/bytes/`, `src/ui/setup/` | `screens/rawtools.py`, `screens/alarms.py`, `screens/handheld.py` |
| 12 | Platform: Web Serial, Tauri v2 shell + serial plugin (desktop + Android), storage, PWA | `src/platform/`, `src-tauri/`, `public/manifest*` | `transport.py`, new |
| 13 | Release + deploy scripts (no GitHub Actions) | `../scripts/release/` | `scripts/`, `tools/build-handheld-image.sh` |
| 14 | CYD firmware Dracula theme | `../firmware/handheld/main/ui/` | firmware UI |

The design (screens, layout, theme) is the canvas
"hondalog app layout redesign" (Dracula draft 2): F1 Live, F2 Drive,
F3 Events, F4 Replay, F5 Power, F6 GPS map, F7 Bytes, F8 Setup; F9 theme;
Space = mark. The CYD is the black box (no separate ESP32-S3 black box).

Shared state lives in a zustand store `src/core/session.ts` (owner: #8 creates it,
everyone may read; new fields go through the integrator).
