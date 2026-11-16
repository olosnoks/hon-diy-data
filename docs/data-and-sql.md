# Data and SQL

Everything hondalog records goes into one SQLite database. This page covers
where it is, what is in it, how to query it, and the `hondalog-log` command
that lists, exports, compares and deletes drives.

- [Where the data is](#where-the-data-is)
- [What a drive is](#what-a-drive-is)
- [hondalog-log](#hondalog-log)
- [Naming a drive](#naming-a-drive)
- [Exports](#exports)
- [GPS: tracks and where the stalls happen](#gps-tracks-and-where-the-stalls-happen)
- [Imports](#imports)
- [Redecode](#redecode)
- [Deleting and keeping](#deleting-and-keeping)
- [Retention](#retention)
- [Querying with SQL](#querying-with-sql)
- [Crash safety](#crash-safety)
- [Backups](#backups)

## Where the data is

| What | Where |
|---|---|
| Every drive and capture | `~/hondalog/hondalog.db` |
| Its write-ahead files, while it is open | `hondalog.db-wal`, `hondalog.db-shm` beside it |
| Loose files: exports you did not give a path, first-contact reports, fetched black box captures (`.bbx`) | `~/hondalog/logs/` |
| Verification evidence | `~/hondalog/verification/` |

`HONDALOG_DB=/path/file.db` uses another database. `HONDALOG_DIR=/path`
puts the database (`/path/hondalog.db`) and the loose files in one folder.
`hondalog-log --db FILE` picks a database for one command.

The database is ordinary SQLite (WAL mode). Nothing else is a recording
format: the `.hlog` container of early development versions is gone.

## What a drive is

A **drive** is one stretch of recording: from the ECU's first answer to key
off. Its name is `drive_YYYY_MM_DD_HHMM`, for example
`drive_2026_11_16_0712`. It holds:

- every K-line read with its own timestamp, **the raw bytes the ECU sent**,
  the reply latency, and the decoded values;
- the math channels;
- the black box's live frames, if the box was connected;
- the handheld's GPS fixes (the `gps` stream), when the drive came from the
  handheld: imported from its SD card, or recorded live over `cyd:`;
- markers, trigger firings and alarms;
- a header: ECU id bytes, port, the address map's version and hash and its
  full text, link statistics, whether the expert unlock was used and which
  frames were sent with it.

A **capture** is a window inside a drive: 30 seconds before a trigger and
10 after, named `stall_YYYYMMDD_HHMMSS`. It is one row pointing into the
drive, not a copy of the data.

Times (`t`) are seconds since the app connected, on one clock for every
table of a drive. Units are the address map's (°C, kPa, km/h), whatever the
screen shows.

## hondalog-log

```sh
hondalog-log [--db DB] COMMAND ...
```

| Command | Does |
|---|---|
| `record --port PORT [--bb PORT] [--seconds N]` | Record without the window, until Ctrl-C |
| `list [-q WORDS]` | Drives and captures; `-q` searches names, notes and marker texts |
| `info [DRIVE] [--events N]` | Header, streams with their rates, events. Default: the newest drive |
| `export DRIVE OUT` | Write `.mlg`, `.msl`, `.csv`, `.npz` or `.db`; the GPS track as `.gpx` or `.kml` |
| `stalls [--geojson OUT]` | Every stall with where it happened, grouped by place; `--geojson` writes them as a file |
| `compare A B` | Two drives or captures, channel by channel |
| `import FILE...` | Read CSV / MSL / MLG logs in as new drives |
| `report DRIVE [-o OUT.html]` | A one-page HTML report |
| `redecode DRIVE [OUT_DB] [--map TOML]` | Decode the raw bytes again with another map, as a new drive |
| `prune` | Delete old drives by age, size or count |
| `rm NAME...` | Delete one drive or one capture |
| `keep DRIVE [--off]` | Protect a drive from `prune` |
| `vacuum [--full]` | Give freed space back to the disk and check the file |
| `sql ["QUERY"] [--csv]` | Run a query, or open the `sqlite3` shell |

### record

```sh
hondalog-log record --port /dev/hondalog                         # until Ctrl-C
hondalog-log record --port /dev/hondalog --bb /dev/hondalog-bb   # with the black box
hondalog-log record --port sim: --bb bbsim: --seconds 30         # simulated, 30 s
```

The same recorder, trigger engine and stall catcher as the app, with a
progress line every two seconds. `--map FILE` uses another address map,
`-o` (or `--db`) another database, `-q` is quiet.

### list and info

```
$ hondalog-log list
  stall_20261115_215339          2026-11-15T21:53:39     23.8 s      2584 rows    2 marks  black box: ecub_glitch
drive_2026_11_15_2153            2026-11-15T21:53:26     45.1 s      5344 rows    2 marks
```

Captures are indented above the drive they belong to. `info` prints the
header, then one line per stream:

```
    ecu/00+3              684 rows     15.2 Hz, 3 raw bytes/row  [rpm, vss]
    ecu/08+10              39 rows      0.9 Hz, 10 raw bytes/row  [ect, iat, ac_sw, vtec, mil, closed_loop]
    math                  682 rows     15.2 Hz  [inj_duty, fuel_flow, ...]
    blackbox/live        2255 rows     50.0 Hz  [bb_vbat, bb_ecub, ...]
```

A stream named `ecu/AA+N` is one K-line read: N bytes at address 0xAA.

### compare

```sh
hondalog-log compare drive_2026_11_16_0712 drive_2026_11_17_0705
hondalog-log compare stall_A stall_B --align trigger --channels rpm,map,batt
```

Prints, per channel, the mean of each, the mean difference, the RMS
difference and the largest difference. `--align` chooses the moment laid on
top of each other: `start` (default), `marker` (with `--text WORDS` to pick
one), `trigger`, or `crossing` (RPM rising through 400). Either side may be
in another database: `other.db#drive_...`.

### report

```sh
hondalog-log report stall_20261116_071455 -o ~/hondalog/logs/stall.html
```

Without `-o` the file is written to the current directory as `NAME.html`.

## Naming a drive

Wherever a command takes DRIVE:

| Form | Means |
|---|---|
| `drive_2026_11_16_0712` | A drive |
| `stall_20261116_071455` | A capture: the drive cut to its window |
| `last` | The newest drive |
| `other.db#drive_...` | A drive in another database |
| `old_log.csv`, `.msl`, `.mlg` | A log file, read directly |

## Exports

```sh
hondalog-log export last drive.mlg                 # MegaLogViewer binary (MLG v2)
hondalog-log export last drive.msl                 # MegaLogViewer text
hondalog-log export last drive.csv
hondalog-log export last drive.npz                 # NumPy
hondalog-log export last drive.db                  # a database holding just this drive
hondalog-log export last idle.csv --channels rpm,map,stft --filter "ect > 80" --hz 10
```

| Option | Does |
|---|---|
| `--channels a,b,c` | Only these columns |
| `--hz N` | Resample to a fixed rate |
| `--filter EXPR` | Keep rows where this [expression](mapping-the-p2p.md#expression-reference) is true. Repeatable |
| `--start S`, `--end S` | Cut to a time range, in drive seconds |
| `--mlg-version 1` or `2` | MLG format version (default 2) |
| `--compact` | MLG: 16-bit scaled fields where they fit |

The format follows the output file's extension.

- **MLG** follows EFI Analytics' published MLG layout and is checked byte by
  byte in the tests. **Nobody on this project has opened one in
  MegaLogViewer yet.**
- **CSV** starts with `#` header lines (map version, units, ECU id).
- **`.db`** is the way to hand one drive to someone: it is a complete
  hondalog database with the raw bytes, so they can redecode it.
- Parquet is not offered: Debian trixie has no `python3-pyarrow`.

## GPS: tracks and where the stalls happen

The GPS comes from the handheld (its own receiver): a drive imported from
its SD card, or recorded live over `cyd:`, has a `gps` stream with a fix
about once a second. A K-line-only drive has none, and the GPS commands say
so. The [Map screen (F8)](user-guide.md#f8-map) draws the same data.

```sh
hondalog-log export last drive.gpx                       # GPX 1.1: the track with times, waypoints
hondalog-log export last drive --format kml              # KML, coloured by speed (drive.kml)
hondalog-log export last drive.kml --channel rpm         # ... coloured by another channel
hondalog-log export stall_20261116_071455 stall.gpx      # a capture: its window only
hondalog-log stalls                                      # every stall, where, how fast, which group
hondalog-log stalls --geojson stalls.geojson             # the same as a GeoJSON file
hondalog-log stalls --since 2026-11-01 --reason rpm --geojson -   # filtered, to stdout
```

- **GPX** has one track segment per stretch without a gap (no fix for 5 s
  breaks the line), each point with its UTC time, and speed and heading in
  a `hondalog:` extension (`--channel` adds that channel's value too).
  Markers, alarms and captures are waypoints (`<type>marker</type>`,
  `alarm`, `stall`, `capture`). A drive whose wall clock is not known gets
  no times.
- **KML** draws the track in the Map screen's colours (the cividis-like
  ramp; blue to orange around zero for fuel trims), the range in the
  document's description, and pins for markers, alarms and captures. Opens
  in Google Earth and most map tools.
- **`hondalog-log stalls`** lists every stall (`--all`: every capture) with
  the GPS fix nearest its trigger, the speed there and the fastest in the
  30 s before, and puts stalls within 150 m of each other (`--radius`) in a
  numbered group. The last line names the biggest group:
  `4 stalls. 3 of them within 150 m of one another, near 32.26977, -90.09992, ...`
- **GeoJSON** is a FeatureCollection of Points (`[lon, lat]`, as RFC 7946
  has it); each feature's properties are the capture's name, drive, time,
  reason, speeds and its group (`cluster`, `cluster_size`). Stalls with no
  fix within a minute of the trigger are left out and counted in
  `unlocated`.
- Speeds in exports are km/h (the GPS's unit); `speed_mph` in SQL.

## Imports

```sh
hondalog-log import old_log.csv another.msl
```

Reads hondalog 0.1.0 CSV logs and MegaLogViewer `.msl` / `.mlg` files into
the database as new drives (status `imported`). In the app: Replay, O,
**Import file…**.

Imported drives hold decoded values only. They have no raw bytes, so they
cannot be redecoded.

Drives recorded by the handheld come in with
`hondalog-handheld import` ([Handheld](handheld.md#importing-drives)); those
do keep the raw bytes.

## Redecode

Every recorded K-line read is stored with the bytes the ECU sent. When the
address map turns out to be wrong, fix the map and decode the old drive
again:

```sh
hondalog-log redecode drive_2026_11_16_0712                 # with your current map
hondalog-log redecode drive_2026_11_16_0712 --map fixed.toml
```

The result is a **new** drive (`..._2`, `..._3`); the original is not
changed. A channel moved to an address that was never read in that drive
cannot be recovered: only the blocks the planner asked for are stored.

## Deleting and keeping

```sh
hondalog-log rm drive_2026_11_16_0712         # asks first
hondalog-log rm -y stall_20261116_071455      # no question
hondalog-log keep drive_2026_11_16_0712       # protect it
hondalog-log keep drive_2026_11_16_0712 --off
```

- `rm` on a **drive** removes everything recorded in it, its captures
  included, and gives the space back to the disk.
- `rm` on a **capture** removes only the capture's entry. The drive and its
  data stay.
- A drive being recorded is refused.
- A drive marked **keep** asks you to type its name. `--force` skips that
  question; `--yes` alone does not.
- `--full` rewrites the file compactly afterwards.

The drive picker in the app (Replay, O) has the same Delete… and Keep
buttons.

## Retention

Nothing is deleted automatically. `prune` is the tool:

```sh
hondalog-log prune --days 90              # a dry run: lists what would go
hondalog-log prune --days 90 --yes        # delete drives older than 90 days
hondalog-log prune --mb 2000 --yes        # oldest first until the file is under 2 GB
hondalog-log prune --drives 200 --yes     # keep at most 200 drives
```

Without `--yes` it only prints the plan. `prune` never deletes:

- the newest drive;
- a drive that is being recorded;
- a drive marked keep, or with notes or tags;
- a drive with a capture in it (a stall), unless you add `--captures`.

How big it gets: one synthetic hour (K-line reads plus black box frames at
50 Hz) is about 13.5 MB.

`hondalog-log vacuum` releases free pages and runs SQLite's integrity check.
`vacuum --full` rewrites the whole file; not while recording.

## Querying with SQL

```sh
hondalog-log sql "SELECT drive, minutes, stalls FROM drive_summary"
hondalog-log sql --csv "SELECT ..." > out.csv
hondalog-log sql                      # the sqlite3 shell (needs the sqlite3 package)
```

The `sqlite3` shell and DB Browser for SQLite open the file too, also while
the app is recording. **Open it read-only** (`sqlite3 -readonly`), and write
to it only through hondalog.

### Views: what to query

| View | One row is |
|---|---|
| `drive_summary` | A drive: `drive`, `status`, `end_reason`, `started`, `ended`, `minutes`, `rows`, `stalls`, `captures`, `markers`, `alarms`, `channels`, `ecu_id`, `map_version`, `notes`, `tags`, `approx_mb` |
| `stalls` | A stall capture: `name`, `drive`, `wall_time`, `reason`, `message`, `t_trigger`, `t_start`, `t_end`, `complete`, `rows` |
| `drive_YYYY_MM_DD_HHMM` | **One view per drive.** A row per RPM read, every channel as a column: `t`, `wall_time`, `rpm`, `map`, ... Slower channels hold their last value (for up to 5 s); black box channels show their one-second mean |
| `drive_..._blackbox` | The same drive's black box streams, a row per second: `<channel>_min`, `<channel>_mean`, `<channel>_max` |
| `readings` | Every K-line and math value in long form: `drive`, `channel`, `units`, `t`, `unix_time`, `value` |
| `blackbox_1s` | Every black box channel per second: `drive`, `channel`, `t`, `samples`, `min`, `mean`, `max` |
| `ecu_reads` | The raw bytes of every K-line read: `drive`, `t`, `addr`, `block`, `raw`, `raw_hex`, `latency_ms` |
| `markers` | Markers, trigger firings and alarms: `drive`, `t`, `wall_time`, `kind`, `text` |
| `gps_track` | Every GPS fix of every drive: `drive`, `t`, `unix_time`, `lat`, `lon`, `speed` (km/h), `speed_mph`, `heading`, `fix` (1 = a position), `sats`, `hdop` |
| `stall_locations` | Every capture with the GPS fix nearest its trigger (within 60 s): `name`, `kind`, `reason`, `message`, `drive`, `wall_time`, `t_trigger`, `gps_t`, `gps_dt` (fix minus trigger, s), `lat`, `lon`, `speed`, `speed_mph`, `heading`, `max_speed_30s` (km/h). No fix: `lat` is NULL |

Every shared view also has `drive_id`, for joins.

### Tables underneath

`drives`, `address_maps`, `streams`, `channels`, `samples`, `chunks`,
`chunk_stats`, `events`, `alarms`, `captures`, `schema_version`. They are
described in [Architecture](ARCHITECTURE.md#the-database). Two things to
know:

- `samples` is wide: one row per K-line read with the raw bytes and up to
  four decoded values. Use the `readings` view to ask for a channel by name.
- The black box rows are packed into `chunks` BLOBs. SQL sees them through
  the per-second statistics (`blackbox_1s`). For every sample, export the
  drive or read it in Python:
  `hondalog.data.open_log("drive_...").series("bb_ecub")`.

### Examples

Each of these runs as written against a database recorded from the
simulator. Replace the drive name with one of yours
(`hondalog-log list`).

Every drive, newest first:

```sql
SELECT drive, started, minutes, stalls, markers, status, notes
FROM drive_summary
ORDER BY started DESC;
```

Every stall ever caught:

```sql
SELECT name, drive, wall_time, reason, message, round(t_trigger, 1) AS t_trigger
FROM stalls
ORDER BY wall_time;
```

RPM in the last 5 seconds before each stall:

```sql
SELECT s.name AS stall, round(r.t - s.t_trigger, 2) AS seconds, r.value AS rpm
FROM stalls s
JOIN readings r ON r.drive_id = s.drive_id AND r.channel = 'rpm'
WHERE r.t BETWEEN s.t_trigger - 5 AND s.t_trigger
ORDER BY 1, 2;
```

ECU +B from the black box around each stall (the lowest and the mean value
of each second):

```sql
SELECT s.name AS stall, b.t - s.t_trigger AS seconds, b.min AS ecub_min, b.mean AS ecub_mean
FROM stalls s
JOIN blackbox_1s b ON b.drive_id = s.drive_id AND b.channel = 'bb_ecub'
WHERE b.t BETWEEN s.t_trigger - 5 AND s.t_trigger + 2
ORDER BY 1, 2;
```

One drive as a table: where was it lean at a warm idle?

```sql
SELECT t, wall_time, rpm, map, o2, stft
FROM drive_2026_11_16_0712
WHERE ect > 80 AND rpm < 1000 AND o2 < 0.2;
```

The seconds in which any rail seen by the black box dipped under 11 V:

```sql
SELECT t, bb_vbat_min, bb_ecub_min, bb_pump_min, bb_ig1_min, bb_rpm_tach_mean
FROM drive_2026_11_16_0712_blackbox
WHERE bb_ecub_min < 11
ORDER BY t;
```

Lowest battery voltage the ECU reported, per drive:

```sql
SELECT drive, min(value) AS lowest_batt, count(*) AS reads
FROM readings
WHERE channel = 'batt'
GROUP BY drive;
```

Markers and trigger firings of every drive:

```sql
SELECT drive, round(t, 2) AS t, kind, text
FROM markers
ORDER BY drive_id, t;
```

The raw K-line bytes in the second before each trigger:

```sql
SELECT m.text, round(e.t, 3) AS t, e.block, e.raw_hex, e.latency_ms
FROM markers m
JOIN ecu_reads e ON e.drive_id = m.drive_id
WHERE m.kind = 'trigger' AND e.t BETWEEN m.t - 1 AND m.t;
```

Link quality per drive (reply latency):

```sql
SELECT drive, round(avg(latency_ms), 2) AS mean_ms, round(max(latency_ms), 2) AS worst_ms,
       count(*) AS reads
FROM ecu_reads
GROUP BY drive;
```

Which map each drive was decoded with, and whether anything but reads was
sent:

```sql
SELECT name, status, end_reason, map_tag, expert_unlock, keep
FROM drives;
```

### GPS and where the stalls happen

Run against a database with GPS drives (the handheld's) and stall
captures. `fix = 1` leaves out the rows the receiver sent without a
position.

One drive's track, in mph:

```sql
SELECT t, lat, lon, round(speed_mph, 1) AS mph, heading, sats
FROM gps_track
WHERE drive = 'drive_2026_11_02_0712' AND fix = 1
ORDER BY t;
```

Every drive with GPS: fixes, top speed and how far north and south it went:

```sql
SELECT drive, count(*) AS fixes, round(max(speed_mph)) AS top_mph,
       round(min(lat), 4) AS south, round(max(lat), 4) AS north
FROM gps_track
WHERE fix = 1
GROUP BY drive;
```

Every stall, where it happened, and how fast the car was going (at the
nearest fix and at most in the 30 s before):

```sql
SELECT name, drive, wall_time, reason, round(lat, 5) AS lat, round(lon, 5) AS lon,
       round(speed_mph) AS mph, round(max_speed_30s * 0.621371) AS mph_before, round(gps_dt, 1) AS fix_dt
FROM stall_locations
WHERE kind = 'stall'
ORDER BY wall_time;
```

Stalls within about 150 m of another stall (a box test: 111 000 m per
degree of latitude, 94 000 m per degree of longitude at Pearl's 32° N):

```sql
SELECT a.name AS stall, b.name AS also_here,
       round(max(abs(a.lat - b.lat) * 111000, abs(a.lon - b.lon) * 94000)) AS metres_apart
FROM stall_locations a
JOIN stall_locations b ON b.capture_id > a.capture_id
WHERE a.kind = 'stall' AND b.kind = 'stall'
  AND abs(a.lat - b.lat) * 111000 < 150 AND abs(a.lon - b.lon) * 94000 < 150;
```

Stalls counted per square of about 100 m (three decimals of a degree):

```sql
SELECT round(lat, 3) AS lat, round(lon, 3) AS lon, count(*) AS stalls, group_concat(name, ' ') AS which
FROM stall_locations
WHERE kind = 'stall' AND lat IS NOT NULL
GROUP BY 1, 2
ORDER BY stalls DESC;
```

The car's speed and position in the 30 s before each stall:

```sql
SELECT s.name AS stall, round(g.t - s.t_trigger) AS seconds, round(g.speed_mph) AS mph,
       round(g.lat, 5) AS lat, round(g.lon, 5) AS lon
FROM stall_locations s
JOIN gps_track g ON g.drive_id = s.drive_id
WHERE s.kind = 'stall' AND g.t BETWEEN s.t_trigger - 30 AND s.t_trigger
ORDER BY s.name, g.t;
```

Stalls with no GPS fix near them (K-line-only drives, a cold receiver):

```sql
SELECT name, drive, wall_time, reason
FROM stall_locations
WHERE lat IS NULL;
```

### From Python

```python
from hondalog.data import open_log

log = open_log("last")                    # or a drive or capture name
rpm = log.series("rpm")                  # .t = times in seconds, .v = values
print(log.meta["address_map_tag"], len(rpm), rpm.v.max())

from hondalog.geo import Track, stalls

track = Track.from_log(log)              # the GPS fixes: .t .lat .lon .speed, .at(t), .nearest(x, y)
pins = stalls.stall_pins("~/hondalog/hondalog.db")       # every stall with its place
print(stalls.summary(pins))
```

## Crash safety

- The recorder buffers in memory and commits every **0.5 s** on its own
  thread. The UI and the poller never wait for the disk.
- After a crash, a kill or a dead laptop battery, the database opens cleanly
  and misses at most the last commit interval.
- The next thing to open the database (the app, any `hondalog-log` command)
  finds the drive still marked `recording` with no live writer and finishes
  it: status **`crashed`**, end time from the last row on disk.
  `hondalog-log info` says "the recorder died: everything up to the last
  commit is here".
- A commit is not forced to the platter each time (`synchronous=NORMAL`);
  the write-ahead log is checkpointed every 10 s. That bounds what a sudden
  power cut, as opposed to a crash, can take. A laptop has a battery, so in
  practice this is the crash case.
- A file from a newer hondalog is refused with a message instead of being
  half-read.

Tested with SIGKILL in the test suite. Not tested: pulling the battery out
of the T430.

## Backups

The database is one file, but while it is open its newest data may still be
in `hondalog.db-wal`. So:

**With hondalog closed**, copy the file:

```sh
cp ~/hondalog/hondalog.db /media/usb/hondalog-$(date +%F).db
```

**While it may be open**, let SQLite make a consistent copy:

```sh
sqlite3 ~/hondalog/hondalog.db ".backup '/media/usb/hondalog-backup.db'"
```

**One drive** for someone else:

```sh
hondalog-log export drive_2026_11_16_0712 drive_0712.db
```

Also worth copying: `~/.config/hondalog/addressmap.toml` (your verified
channels), `~/hondalog/logs/` and `~/hondalog/verification/`.

To look inside a backup without touching your own data:

```sh
hondalog-log --db /media/usb/hondalog-backup.db list
hondalog --open "/media/usb/hondalog-backup.db#drive_2026_11_16_0712"
```
