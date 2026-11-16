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
| `logger.py` | `Recorder`: the drive being recorded, the trigger engine, captures | W4 |
| `data/` | the SQLite database (`schema`, `db`, `store`, `codec`), reader, library, retention, redecode | W4 |
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
                 (own t, raw bytes,    into its ChannelBus)          (box saves its side)
                  latency)
   feed_values -> "math", "blackbox/live"
   feed_link   -> "ecu/link"
   event / mark / trigger / external_trigger
     |                                  |
 triggers.TriggerEngine               data.Store  (buffers in memory; its own thread
   (rules in hondalog.expr;             commits every 0.5 s: WAL, synchronous=NORMAL,
    stall = two rules)                  executemany on prepared statements)
     |                                  |
 capture: one row in `captures`      ~/hondalog/hondalog.db
   (drive, t_start..t_end, what        drives, samples, chunks, events, alarms, captures
    fired); no data is copied          + one view per drive: drive_2026_11_16_0712
```

Rules that hold along it:

- **Time.** Session seconds count from the poller's `t0`. A read is stamped
  when its reply completed minus its estimated age (`BlockRead.t -
  latency_s`, the same instant as `Sample.stamps`). The black box reader is
  given the same `t0` and maps box microseconds onto it with its own NTP-style
  `ClockSync`. The drive's `epoch_unix` is the wall time of second 0.
- **Raw bytes.** Every K-line read is stored with the bytes the ECU sent,
  so `data.redecode` can decode an old drive with a corrected map.
- **Drives.** A recording session is one or more drives on one clock. A
  drive ends when recording stops or when nothing has shown the car to be
  switched on for `drive_gap_s` (120 s): the ECU silent, and the black box
  (if there is one) not seeing the key on. While the box sees IG1, ECU +B or
  the engine turning (`live.blackbox_key_on`) the drive stays open and its
  frames are recorded although the ECU is dead; that stall is one drive, not
  an ECU drive and a "black box only" one cut at the 120 s mark. The next
  sign of life starts a new drive: the ECU answering, or the key coming on
  with the ECU still silent (`started_by` in the drive's meta). Between
  drives nothing is recorded. `end_reason` is `key_off` when the box saw the
  key go off, `link_lost` when only the ECU's silence is known. A drive does
  not end while a capture window is open.
- **One trigger path.** Stall rules, user rules, alarms with the `capture`
  action (`triggers.alarm_rule`), the manual button and a black box trigger
  (`Recorder.external_trigger`) all open a window in the same engine; firings
  inside one window share one capture.
- **Header.** `address_map_tag` / `address_map_hash`, `ecu_variant`, `link`
  (counters and measured timing), `expert_unlock`, `unlocked_frames`,
  `read_plan`, `triggers`, `epoch_unix`, `blackbox`: columns of the drive's
  row where SQL should see them, the rest in its `meta` JSON. `Log.meta`
  puts them back into one dictionary.

## The database

One file, `~/hondalog/hondalog.db` (`HONDALOG_DB`, or `hondalog.db` in
`HONDALOG_DIR`). SQLite in WAL mode with `synchronous=NORMAL` and
incremental auto-vacuum; `PRAGMA application_id` is "HDLG". Nothing else is
a recording format: the `.hlog` container is gone.

| Table | One row is | Key |
|---|---|---|
| `schema_version` | an applied migration (version, when, description) | version |
| `drives` | a drive: name, status (`recording` / `complete` / `crashed` / `imported`), end reason, started / ended (unix and ISO), duration, `epoch_unix`, first and last session second, rows, vehicle, ECU id, port, map version / tag / hash (`map_id` -> `address_maps`), link stats (JSON), expert unlock, unlocked frames, read plan, notes, tags, keep, stall and capture counts, RPM thumbnail, everything else as `meta` JSON, writer pid and heartbeat | id; name is unique |
| `address_maps` | an address map text, stored once however many drives used it | sha256 |
| `streams` | a stream of a drive: a K-line block (`ecu/12+4`), `math`, `blackbox/live`; source, storage (`rows` or `chunks`), raw length, address, rows, first and last time | (drive_id, id) |
| `channels` | a channel of a stream: name, units, label, kind, dtype, decimals, range, and where its values are: `part`, `slot` | (drive_id, stream_id, position) |
| `samples` | one read or one sample of a `rows` stream: `t`, `raw` (the ECU's bytes), `latency_us`, decoded values `v0..v3` | (drive_id, stream_id, part, t, n), clustered |
| `chunks` | a block of rows of a `chunks` stream: `t_start`, `t_end`, `n_rows`, `codec`, `data` | id; index (drive_id, stream_id, t_start) |
| `chunk_stats` | one second of one channel of a `chunks` stream: n, min, mean, max | (drive_id, stream_id, position, t), clustered |
| `events` | a marker, trigger firing, note, link or black box event: t, kind, text, data (JSON) | id; index (drive_id, t) |
| `alarms` | an alarm state change: t, name, state, severity, channel, value, text | id; index (drive_id, t) |
| `captures` | a stall or trigger capture: drive, name (`stall_20261116_071455`), kind, rule, reason, message, `t_trigger`, `t_start`, `t_end`, wall time, complete, rows, what fired (JSON), meta | id; name is unique |

Views: one per drive, named like the drive (`drive_2026_11_16_0712`: a row
per read of the stream that carries RPM, every channel as a column, slower
streams held for up to 5 s, black box channels as their one-second mean)
and `<drive>_blackbox`; shared: `drive_summary`, `stalls`, `readings`,
`blackbox_1s`, `ecu_reads`, `markers`. The README has example queries.

**Storage layout of the decoded values.** `samples` is wide: one row per
K-line read holds the raw bytes, the latency and the values decoded from
them; a channel is (stream, part, slot) and slot k is column `vk` (streams
with more than four columns continue in `part` 1, 2, ...). The alternative,
one row per value clustered on (drive, channel, t), was measured with
`bench/bench_layout.py` on one hour of reads (177 480 reads, 421 920
values, 11 channels):

| | long / narrow | wide per read |
|---|---|---|
| file size | 16.9 MB | 9.1 MB |
| write the hour in 0.5 s commits | 2.1 s | 0.9 s |
| writer CPU while recording | 0.06 % | 0.03 % |
| load every channel and the raw bytes | 230 ms | 139 ms |
| load one fast channel | 22 ms | 22 ms |
| every channel, one 60 s window | 1.7 ms | 2.4 ms |

Each value costs a key in the narrow layout and each read costs a second
row for its raw bytes; the wide row pays for the key once. A range read of
one channel is the same index search either way. What the wide layout
loses, `WHERE channel = 'rpm'`, the `readings` view gives back.

**High-rate streams** (`storage = "chunks"`, the default for
`source = "blackbox"`) are not stored row by row. Each commit appends the
new rows as one `raw` BLOB (the packed records, no CPU); once a minute the
store replaces those with one `d1` block (`data/codec.py`: lossless, per
column; millivolt-like floats become integers, times become second
differences, then byte shuffle and zlib) and writes `chunk_stats`. One
million 1 kHz rows of twelve millivolt channels (56 MB as packed records)
take 13 MB including the statistics.

**Durability.** `Store` buffers in memory and commits every 0.5 s on its own
thread (`hondalog-store`); the feeding threads and the UI thread never touch
the disk. After a SIGKILL the database opens cleanly and misses at most the
last commit interval; the first opener (app, CLI, `open_log`) finds the
drive still marked `recording` with no live writer (pid gone, or no
heartbeat for 30 s) and finishes its row: status `crashed`, `ended_at` from
the last row on disk, the black box tail packed, the view built
(`db.recover`). A drive's totals (`rows`, `duration_s`, `t_last`, the
thumbnail) are written when it ends; while it is `recording` the reader
counts for itself. In `synchronous=NORMAL` a commit is not synced; the store
checkpoints the write-ahead log every 10 s, which bounds what a power cut
(as opposed to a crash) can take.

**Migrations.** `data/schema.py` holds `MIGRATIONS`, a list of
`Migration(version, description, apply)`. Every open runs the ones the file
has not seen, each in its own transaction together with its
`schema_version` row (and `PRAGMA user_version`), then rebuilds the views. A
file from a newer hondalog is refused with a message, not half-read.

**Size.** One synthetic hour (three fast K-line blocks at 16 Hz, one slow
block, twelve black box columns at 50 Hz: the streams the `.hlog` figure of
13.8 MB was measured on) is 13.5 MB: `samples` 9.1, `chunks` 2.3,
`chunk_stats` 2.0. A K-line row costs about 50 to 70 bytes here against
about 20 to 30 packed, because every value is an 8-byte REAL that SQL can
read; the black box rows shrink to about a quarter. `bench/bench_data.py`
prints the numbers.

## After the drive

```
data.open_log(DriveRef | "db#name" | name | .csv | .msl | .mlg) -> Log
    .series(name) / .table(names, base=, hz=)      analysis.FilterSet (hondalog.expr)
    .streams[...].raw / .latency / .window(t0, t1)
    analysis.TableHistogram                        bins: Bins.from_breakpoints, or
                                                   table_bins(tuning.TableEditor)
    analysis.ReplaySession                         envelopes, two cursors, readouts
    analysis.Comparison / compare_logs             two drives, same database or two
    export.export_log -> .mlg .msl .csv .npz .db   export.export_capture(capture, file)
    data.redecode(log, amap) -> data.write_log(log, db) -> a new drive
data.Library(db)            drives and captures from SQL, search, notes and tags
data.RetentionPolicy        delete old drives, then incremental vacuum
data.retention.delete       one drive or one capture by name (hondalog-log rm, the drive picker)
blackbox.open_event(.bbx) -> the box's 1 kHz capture merged with the drive
                             that was being recorded (found by wall time in
                             `drives`; aligned by epoch_unix, else markers, else RPM)
tuning.analyze_log(table columns, TableEditor) -> auto-tune suggestions
```

A `DriveRef` (database, drive name, optional capture name) stands where a
log file path used to: `Recorder.path`, `CaptureEvent.path`, `Log.path`.
A capture opens as the drive cut to its window, with the trigger facts
added to `meta`.

## The UI

`ui/` talks to the rest only through the Protocol classes in
`ui/adapters.py`; `ui/session.py:build_session()` picks the implementations
(`ui/wired.py` for real use, `ui/backends.py` for `--demo`). Sources publish
into `ui/bus.ChannelBus` from their own threads; the shell reads the bus on
one 33 ms timer. Details: `docs/ui-adapters.md`.

## Commands

| Command | Module | Does |
|---|---|---|
| `hondalog` | `hondalog.__main__` | the app (`--port`, `--connect`, `--bb`, `--rom`, `--definition`, `--open DRIVE`, `--demo`, `--no-gl`, `--profile`) |
| `hondalog-sim` | `hondalog.sim` | fake ECU on a pty, with faults |
| `hondalog-firstcontact` | `hondalog.diag.firstcontact` | first time on the car: adapter, echo, protocol, block size, timing, ISO |
| `hondalog-map` | `hondalog.mapping.__main__` | status, guided verification, address finder, map diff / hash / upgrade |
| `hondalog-log` | `hondalog.logtool` | record (headless), list, info, export, compare, import, report, redecode, prune, rm, keep, vacuum, sql |
| `hondalog-bb` | `hondalog.blackbox.__main__` | black box: live, list, fetch, info, csv, config, selftest, cal |
| `hondalog-bbsim` | `hondalog.blackbox.sim` | fake black box on a pty |
| `hondalog-tune` | `hondalog.tuning.cli` | ROM files: info, list, show, diff, convert, burn-image, burn |

## Tests and tools

`python3 -m pytest` runs everything; `tests/test_integration.py` is the
cross-track path (simulator -> link -> pipeline -> recorder -> capture ->
reopen -> redecode -> histogram on ROM axes -> MLG, and the black box
simulator on the same timebase); `tests/test_database.py` is the database
(crash recovery, migrations, the views through plain `sqlite3`, chunks,
reading while recording); `tests/test_ui_wired.py` is the same
backends seen through the UI's adapters. `tools/smoke.py` is the end-to-end
run with the window. `.github/workflows/tests.yml` runs the suite, the smoke
test and mypy on the typed packages; `blackbox.yml` builds the firmware.
