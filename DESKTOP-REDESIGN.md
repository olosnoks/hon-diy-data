# Desktop redesign (Python / PySide6): guide for the agents

The Windows and Linux desktop app **stays the Python app** (PySide6). This
branch (`desktop-redesign`, worktree `..\hon-diy-data-desktop`) gives it the
new layout and the Dracula theme so it looks and works the same as the web
app being written in TypeScript in `..\hon-diy-data-web\web\`.

## The design (approved)
Canvas artboards (HTML, read them for layout, sizes, wording, colours):
`C:\Users\skons\AppData\Local\Temp\claude\148e8e79e81f0bfd\b9aac4ce-4dd1-4bbe-9c80-40c46e10ec46\scratchpad\design\project\`
Main (navigation), Live, LiveDarker, Power, Events, Replay (`*.dc.html`).

- Keys: **F1 Live, F2 Drive, F3 Events, F4 Replay, F5 Power, F6 GPS map,
  F7 Bytes, F8 Setup**; F9 toggles **Dracula ↔ Darker**; Space = mark; Esc
  closes panels. Tune is hidden unless a ROM is opened (`--rom`).
- Map 3D becomes the "Time in cell" tab of Power. Raw tools + address map →
  Bytes. Alarms + Handheld + settings → Setup.
- **The CYD is the black box.** The separate ESP32-S3 black box ("Black box ·
  USB" group, bb sources) is hidden from the UI (keep its code and tests;
  just don't show it unless `--bb` is given).
- Theme tokens (only these; never white or light surfaces):
  Dracula bg #282A36, panel #21222C, sunk #343746, line #44475A, rule #6272A4,
  fg #F8F8F2, fg2 #C5C8D6, accent #BD93F9 (on-accent #282A36), warn #F1FA8C,
  alarm #FF5555, ok #50FA7B, unverified #FFB86C, series #BD93F9 #FF79C6
  #8BE9FD #50FA7B #FFB86C, live point #FF79C6. Darker: bg #191A21,
  panel #121318, sunk #21222C (rest the same). Same as `web/src/ui/theme.ts`
  in the web worktree.

## Rules
1. Keep the working Python code working: the full test suite must pass as
   before (`..\hon-diy-data\.venv\Scripts\python.exe -m pytest -q` from this
   worktree; ~35 tests fail on Windows regardless: pty, Linux paths, minipro,
   firstcontact/FTDI, DB location — compare with a run on unmodified main).
   Update tests that encode the old layout/theme; add tests for new screens.
2. Work only in your files (table). Shared files go through agent D1.
3. No new runtime dependencies without saying so. Don't commit, push or
   switch branches; don't touch `..\hon-diy-data` (the running app) or the
   serial ports or `~/.config/hondalog`.
4. Run the app only with `--demo` (and `-platform offscreen` / QT_QPA_PLATFORM
   for screenshots) to check your screen; save screenshots under
   `docs/screenshots/redesign/` (local check only).
5. Final message: what changed, test results before/after, screenshots,
   anything left.

## Who owns what
| Agent | Files |
|---|---|
| D1 shell + theme | `hondalog/ui/theme.py`, `chrome.py`, `main_window.py`, `paint.py`, `controls.py`, `bus.py`, `session.py` (UI parts), `__main__.py` UI flags; wires the new screen order |
| D2 Live + Drive | `hondalog/ui/screens/sensors.py` (→ Live, grouped), `screens/drive.py`, `tiles.py` |
| D3 Replay + Events | `screens/replay.py`, `replaymodel.py`, `drivepicker.py`, new `screens/events.py`, new analysis in `hondalog/analysis/` (`power.py`, `motion.py` for gear / accel / lateral g, `events.py`) |
| D4 Power + Bytes + Setup | new `screens/power.py` (uses D3's `hondalog/analysis/power.py`; Time-in-cell tab hosts `screens/map3d.py`), `screens/rawtools.py` (→ Bytes), new `screens/setup.py` hosting `screens/alarms.py` + `screens/handheld.py` + settings |
