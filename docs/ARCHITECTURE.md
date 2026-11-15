# hondalog: how the pieces fit

One Python package, `hondalog/`. Nothing below `ui/` imports Qt, so every
part runs headless and is tested that way.

## Packages

| Package | What it is | Came from |
|---|---|---|
| `protocol.py`, `transport.py` | Honda K-line framing; serial port, simulator loopback, the request/reply `Link` with its read-only guard | W1 |
| `link/` | read planner, tiered scheduler (fast / medium / slow), adaptive timing, clocks | W1 |
| `poller.py` | the thread that owns the port: `on_block` (every read), `on_sample` (one per fast cycle), `submit`, `obd2` | W1 |
| `obd2.py`, `diag/` | ISO 9141-2 / OBD2; first contact, probes, adapter check | W1 |
| `addressmap.py`, `data/addressmap.toml` | the map: channels, variants, evidence, `[[math]]`, `[[alarm]]`, `[protocol]`; `version_tag` = version + decode hash | W3 (+ W1's rate classes and protocol keys) |
| `expr/` | the one expression engine: formulas, math channels, alarms, filters, triggers | W3 |
| `mathch/`, `alarms/` | math channel engine; alarm state machine | W3 |
| `mapping/` | guided verification, sweep recorder, correlation finder, map editing | W3 |
| `live.py` | `LivePipeline` (math + alarms per sample) and `LiveSession` (the one live path) | W3 + integration |
| `logger.py` | `Recorder`: the running log, the trigger engine, captures | W4 |
| `data/` | the `.hlog` container, reader, library, retention, redecode | W4 |
| `triggers/` | trigger rules and engine (the stall catcher is two rules) | W4 |
| `analysis/` | filters, table histogram, scatter, cursors, compare, replay session, report | W4 |
| `export/` | MLG / MSL / CSV / NPZ writers and importers | W4 |
| `blackbox/` | Track B laptop side: reader, clock sync, simulator, capture files, alignment, verdicts | W2 |
| `tuning/` | ROM definitions, ROM session, table editor, tracer, auto-tune, burner, emulator | W5 |
| `ui/` | the Qt app: shell, seven screens, `ChannelBus`, adapter protocols | W6 |
| `firmware/blackbox/` (not Python) | the ESP32-S3 firmware; its core is the C that the host tests run | W2 |

## The live path

There is one, `hondalog.live.LiveSession`. The app, `hondalog-log record`
and `tools/smoke.py` all use it.

```
            K-line                                     USB
              |                                         |
   transport.Link  (read-only guard,           blackbox.BlackboxReader
     adaptive timing)                            (own clock sync, t0 = poller.t0)
              |                                         |
        poller.Poller                                   |
        |            |                                  |
   on_block      on_sample                          on_sample / on_event
 BlockRead(t,   Sample(values, stamps,                  |
 addr, bytes,   fresh, latency)                         |
 values)             |                                  |
        |      live.LivePipeline                        |
        |      (mathch + alarms)                        |
        |            |                                  |
        +------ live.LiveSession ----------------------+
                     |
     +---------------+------------------+---------------------------+
     |                                  |                           |
 logger.Recorder                    frames / on_read / on_frame   Recorder.on_fired
   feed_read  -> "ecu/AA+N" streams   (the UI publishes them      -> reader.trigger()
                 (own t, raw bytes)    into its ChannelBus)          (box saves its side)
   feed_values -> "math", "blackbox/live"
   feed_link   -> "ecu/link"
   event / mark / trigger / external_trigger
     |
 triggers.TriggerEngine  (rules in hondalog.expr; stall = two rules)
     |
 capture: the window is cut out of the running log, every stream included
     |
 ~/hondalog/logs/log_*.hlog, stall_*.hlog, alarm_*.hlog
```

Rules that hold along it:

- **Time.** Session seconds count from the poller's `t0`. A read is stamped
  when its reply completed minus its estimated age (`BlockRead.t -
  latency_s`, the same instant as `Sample.stamps`). The black box reader is
  given the same `t0` and maps box microseconds onto it with its own NTP-style
  `ClockSync`. The log header's `epoch_unix` is the wall time of second 0.
- **Raw bytes.** Every K-line read is logged with the bytes the ECU sent,
  so `data.redecode` can decode an old log with a corrected map.
- **One trigger path.** Stall rules, user rules, alarms with the `capture`
  action (`triggers.alarm_rule`), the manual button and a black box trigger
  (`Recorder.external_trigger`) all open a window in the same engine; firings
  inside one window share one capture file.
- **Header.** `address_map_tag` / `address_map_hash`, `ecu_variant`, `link`
  (counters and measured timing), `expert_unlock`, `unlocked_frames`,
  `read_plan`, `triggers`, `epoch_unix`, `blackbox`.

## After the drive

```
data.open_log(.hlog | .csv | .msl | .mlg) -> Log (streams at their own rates)
    .series(name) / .table(names, base=, hz=)      analysis.FilterSet (hondalog.expr)
    analysis.TableHistogram                        bins: Bins.from_breakpoints, or
                                                   table_bins(tuning.TableEditor)
    analysis.ReplaySession                         envelopes, two cursors, readouts
    export.export_log -> .mlg .msl .csv .npz
    data.redecode(log, amap)
blackbox.open_event(.bbx) -> the box's 1 kHz capture merged with the ECU log
                             (aligned by epoch_unix, else markers, else RPM)
tuning.analyze_log(table columns, TableEditor) -> auto-tune suggestions
```

## The UI

`ui/` talks to the rest only through the Protocol classes in
`ui/adapters.py`; `ui/session.py:build_session()` picks the implementations
(`ui/wired.py` for real use, `ui/backends.py` for `--demo`). Sources publish
into `ui/bus.ChannelBus` from their own threads; the shell reads the bus on
one 33 ms timer. Details: `docs/ui-adapters.md`.

## Commands

| Command | Module | Does |
|---|---|---|
| `hondalog` | `hondalog.__main__` | the app (`--port`, `--connect`, `--bb`, `--rom`, `--definition`, `--open`, `--demo`, `--no-gl`, `--profile`) |
| `hondalog-sim` | `hondalog.sim` | fake ECU on a pty, with faults |
| `hondalog-firstcontact` | `hondalog.diag.firstcontact` | first time on the car: adapter, echo, protocol, block size, timing, ISO |
| `hondalog-map` | `hondalog.mapping.__main__` | status, guided verification, address finder, map diff / hash / upgrade |
| `hondalog-log` | `hondalog.logtool` | record (headless), info, import, export, repair, report, redecode, list, prune |
| `hondalog-bb` | `hondalog.blackbox.__main__` | black box: live, list, fetch, info, csv, config, selftest, cal |
| `hondalog-bbsim` | `hondalog.blackbox.sim` | fake black box on a pty |
| `hondalog-tune` | `hondalog.tuning.cli` | ROM files: info, list, show, diff, convert, burn-image, burn |

## Tests and tools

`python3 -m pytest` runs everything; `tests/test_integration.py` is the
cross-track path (simulator -> link -> pipeline -> recorder -> capture ->
reopen -> redecode -> histogram on ROM axes -> MLG, and the black box
simulator on the same timebase); `tests/test_ui_wired.py` is the same
backends seen through the UI's adapters. `tools/smoke.py` is the end-to-end
run with the window. `.github/workflows/tests.yml` runs the suite, the smoke
test and mypy on the typed packages; `blackbox.yml` builds the firmware.
