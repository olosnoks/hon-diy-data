# UI adapters: how the UI is bound to the backends

The UI (`hondalog/ui/`) talks to everything behind it through the Protocol
classes in `hondalog/ui/adapters.py`. `build_session()` at the bottom of
`hondalog/ui/session.py` picks one implementation per role:

- real use: `hondalog/ui/wired.py`, bound to the tracks' backends;
- `--demo`: `hondalog/ui/backends.py`, the simulated car (no port, no log).

`tests/test_ui_wired.py` exercises every wired adapter against the built-in
simulators; `test_wired_implementations_satisfy_the_protocols` is the
`isinstance` check a replacement must pass.

```
W1 poller ------ on_block ---.                              .-- Sensors, Drive (tiles)
W3 math -- (LiveSession) ----+--> ChannelBus --(read on the -+-- Map 3D, Tune (live point)
W2 black box reader ---------'    (any thread)  33 ms timer) '-- Replay live lanes, alarms
```

## The rule for live data

Sources call `bus.publish()` / `bus.publish_block()` from their own thread.
The UI reads the bus on one 33 ms timer (`Shell.frame`). There are no Qt
signals per sample, so a source can never flood the event loop and the poller
never waits on the UI. Do not add signals; publish into the bus.

- `bus.publish(values, t=None, wire=None, source=None)`: one row.
  `values` maps channel id to a number (`None` / NaN are skipped). `t` is bus
  time (`bus.now()`, or `bus.bus_time(time.monotonic())`). `wire` is the
  `time.monotonic()` at which the bytes arrived; the latency overlay uses it.
- `bus.publish_block(t_array, {id: array}, ...)`: many rows at once. Use this
  for the 1 kHz black box (send blocks of 10-100 rows, not single rows).
- `bus.register(ChannelSpec)`: declares a channel. Registering an id again
  replaces the spec and keeps the data (address map reload).
- `bus.set_timeout(source, seconds)`: how long without an update before the
  source's tiles go stale (hatched). Use about 3 poll periods.

`ChannelSpec` (in `hondalog/ui/bus.py`) is everything the UI knows about a
channel: `id, label, units, decimals, lo, hi, kind ("value"/"flag"), source
("ECU"/"BB"/"MATH"), verified, warn_lo, warn_hi, alarm_lo, alarm_hi, signed,
mapped`. Labels must fit a 163 px tile (about 12 capitals).

## The roles

| Role | Protocol | Wired (`wired.py`) | Backend | Demo (`backends.py`) |
|---|---|---|---|---|
| ECU link | `ChannelSource` + `Connectable` | `LinkSource` | `hondalog.live.LiveSession` over the W1 `Poller` | `DemoSource` |
| Math channels | `ChannelSource` ("MATH") | `MathSource` | W3 `LivePipeline.math` | none |
| Black box | `ChannelSource` | `BlackboxSource` | W2 `BlackboxReader` on the poller's `t0` | `DemoSource` |
| Alarms | `AlarmSource` | `PipelineAlarms` | W3 `LivePipeline.alarms`; plain limits for the black box rails | `LimitAlarms` |
| Recording, stall catcher | `Recording` | `LiveRecording` | W4 `Recorder` + trigger engine (`Recorder.status()`) | `NullRecording` |
| Logs, forensics | `LogReader` | `DriveReader` | W4 `open_log` / `ReplaySession` / `Library` on the drive database; W2 `open_event` for `.bbx` | `CsvLogReader` |
| Observed map | `ObservedMap` | `LogHistogramMap` | W4 `TableHistogram`, bins from the W5 table when a ROM is open | `HistogramMap` |
| Raw tools, wizard | `MapperSession` | `MappingSession` | W3 `VerificationSession` / `correlate` / `mapedit`; W1 read-only guard | `DemoMapper` |
| Tables | `TableModel` | `RomTableModel` | W5 `RomSession` / `TableEditor`; sample tables when no ROM is loaded | `DemoTableModel` |

Things the wired adapters add that the screens reach with `getattr` (so the
demo objects need not have them): `ecu.link_text()`, `ecu.channel_rates()`,
`ecu.first_contact(port)`, `ecu.set_expert_unlock(on)`, `mapper.set_expert(on)`,
`mapper.rank_against(channel)`, `alarms.marks_itself`, `tables.open(rom,
definition)`, `tables.editor`.

What is not bound yet:

- `ReplaySession` rides on `LogView.replay`, but the Replay screen still
  draws from the `LogView` arrays with its own decimation and cursor code.
- `mapper.rank_against()` (the correlation finder on the sweep history) has
  no button.
- The Tune screen reads its table list once at start: a ROM is opened with
  `--rom` / `--definition`, not from inside the app. `TableTracer` is not
  used; the screen's own live cursor does that job.
- Captures on the box's SD card are fetched with `hondalog-bb fetch`.

### ChannelSource / Connectable (W1, W2)

```python
name: str                                  # "ECU", "BB", "MATH"
def specs(self) -> Sequence[ChannelSpec]
def start(self, bus: ChannelBus) -> None   # register specs, begin publishing
def stop(self) -> None
def status(self) -> SourceStatus           # state, rate_hz, errors, detail, silent_s, queue, simulated

# Connectable (the ECU only)
port: str
def ports(self) -> Sequence[tuple[str, str]]        # (device, label)
def connect(self, port: str) -> None
def disconnect(self) -> None
def set_verified(self, channel_id, verified) -> str  # returns a message for the user
```

`SourceStatus.state` is one of `off / connecting / ok / silent / lost`; the
chips in the status bar are drawn from it. Notes for W1:

- `Session.pump` looks for `ecu.poller` (to notice a dead thread) and
  `ecu.amap` (map name in the tab bar, mapper lookups).
- `LinkSource` publishes every block read (`Poller.on_block`), so a channel's
  tile moves at its own rate; `t` is when the ECU sampled it and `wire` is
  `BlockRead.mono`, the moment the last reply byte arrived.
- `LinkSource.set_verified` writes `status` and a "manual" evidence record to
  the address map file (`mapping.mapedit.mark_channel`) and reloads the map.
- The recorder is fed by the `LiveSession`, not by the bus.
- Rad fan and Fuel pump are real tiles as soon as the map has channels named
  `fan` and `fuel_pump`; until then they are the unmapped placeholders.

W2: the eight black-box ids the layouts assume are `bbatt, ecub, fpv, ig1,
tach, igt, injp, bbmark` (`BLACKBOX_SPECS`). `wired.BB_TO_UI` maps the reader's
names onto them (`bb_vbat, bb_ecub, bb_pump, bb_ig1, bb_rpm_tach / 30,
bb_rpm_ign / 30, bb_inj_pw, bb_button`); logs keep W2's names and
`DriveReader` applies the same mapping. The default Drive view and the
Replay lanes reference `fpv`, `bbatt`, `ecub`, `ig1`, `tach`, `bbmark` by id;
keep the ids or edit `DRIVE_DEFAULT` (session.py) and `GROUPS` (replay.py).

### AlarmSource (W3)

```python
def evaluate(self, bus, now) -> Sequence[AlarmEvent]   # alarms that started just now
def state(self, channel_id) -> str                     # "" / "warn" / "alarm"
def unacked(self, channel_id) -> bool                  # icon blinks while True
def active(self) -> Sequence[AlarmEvent]               # alarms first, newest first
def history(self) -> Sequence[AlarmEvent]
def acknowledge(self) -> None
def limits(self) -> Sequence[LimitRow]
revision: int                                          # bump when active()/history() change
```

`evaluate` runs once per frame on the UI thread and must be cheap. For every
event it returns with severity `alarm` the shell beeps twice. With the plain
limit checker it also drops an auto-MARK through `Recording.mark`; W3's rules
say themselves whether they want a marker (`actions`), and the live path
drops it (`marks_itself`). The limits in `backends.DEFAULT_LIMITS`
are the placeholders from HANDOFF section 8; `spec_from_channel` already
prefers `warn_lo / warn_hi / alarm_lo / alarm_hi` on the address-map channel
when those fields exist.

### Recording (W4)

```python
active: bool; path: Path | None; marks: int
def start(self) -> str            # "" or an error message
def stop(self) -> None
def mark(self, text=None) -> int  # marker number, 0 when not recording
def elapsed_s(self) -> float
def last_mark_age_s(self) -> float | None
def stall(self) -> StallStatus    # state: off / standby / armed / saving / triggered
def poll(self) -> Sequence[StallCapture]   # captures finished since the last call
```

`LiveRecording.stall()` reads `Recorder.status()` (armed, capturing, pre/post
seconds, buffer held, when the open window closes). `standby` is "recording,
no stall rule armed yet" (the engine has not been seen running).
`StallCapture.reason` is the rule's code (`rpm`, `link`, `external` for a
black box trigger, `alarm`, `manual`) and `message` its own words.

### LogReader (W4)

```python
def read(self, path) -> LogView       # raises OSError / ValueError with a user message
def analyze(self, view) -> Forensics  # order of loss, "held up", reading, banner summary
def recent(self, stalls_only=False) -> Sequence          # newest first
def drives(self) -> Sequence[DriveRow]                   # the drive picker's list
def import_file(self, path)                              # CSV / MSL / MLG -> a new drive
```

There are no log files to browse any more: recordings are drives in
`hondalog.db`. `read` takes what `recent` and `drives` hand out (a
`hondalog.data.DriveRef` for a drive or a capture, a `Path` for a `.bbx`
file), a drive or capture name, or the path of a file to read as it is.
`Recording.path` and `StallCapture.path` are `DriveRef`s too; all of them
have `.name` (`drive_2026_11_16_0712`, `stall_20261116_071455`).

Replay's Open button shows `ui/drivepicker.DrivePicker`: `drives()` as a
list (each drive followed by its captures, then `.bbx` files), a search box
over `DriveRow.text`, and "Import file", which calls `import_file` and opens
what it returns.

`LogView` holds per-channel time and value arrays (`t[id]`, `data[id]`), so
1 kHz black-box channels and 10 Hz ECU channels sit in one view at their own
rates. Times are seconds relative to the stall trigger (`trigger_t == 0.0`)
or to the start of the file (`trigger_t is None`). Set `align_residual_ms`
and the Replay header shows it when it exceeds 5 ms.

`order_of_loss()` is the reading for any `LogView` (for a `.bbx` the box's own
verdict from `blackbox.diagnose` leads the summary): band = what the channel did in the
first two thirds of the 30 s window, widened by 8 % of its range; first exit
that lasts 0.2 s; exits within 0.15 s of the trigger count as effects, not
causes.

### ObservedMap (W4)

```python
rpm_axis, map_axis: np.ndarray; revision: int
def update(self, bus) -> None      # once per frame, cheap
def hits(self) -> np.ndarray       # (n_map, n_rpm) sample counts
def mean(self, key) -> np.ndarray  # key in "inj", "ign", "trim"; NaN where hits == 0
def clear(self) -> None
```

Stall paths for the 3D view come from `Session.stall_paths()`, which reads
the newest `stall_*` captures through the `LogReader`.

### MapperSession (W3, W1)

The screen only draws what the session reports. Arrays are length 256 and
owned by the session: `cur` (int16, -1 = unread), `fresh`, `changes`, `minv`,
`maxv`; plus `sweeps`, `sweep_s`, `sweeping`, `revision` (bump when memory or
the console line changed).

```python
def available(self) -> bool
def set_sweeping(self, on) -> None
def clear_changes(self) -> None
def poll(self) -> None                     # once per frame: harvest finished reads
def history(self, addr) -> np.ndarray
def mapped(self, addr) -> tuple[str, bool] | None    # (channel, verified)
def send(self, data: bytes, add_checksum: bool) -> None
def console(self) -> str                   # latest RX line
def export(self, path) -> None
def wizard(self) -> WizardView             # steps, step, title, body, targets, candidates, primary, busy
def wizard_pick_target(self, index) -> None
def wizard_next(self, selected_addr=None) -> None
def wizard_back(self) -> None
def wizard_retry(self) -> None
```

`WizardCard` is a host: it renders `WizardView` (five numbered steps, one
instruction box, a target list or a candidate table, three buttons) and calls
the four `wizard_*` methods. W3's wizard needs no UI change as long as it
fills a `WizardView`. `send` goes to `link.raw`, which is W1's guard: a
frame that is not a read comes back as "blocked" until `set_expert(True)`.

### TableModel (W5)

```python
rpm_axis, map_axis: np.ndarray; writable: bool; read_only_reason: str; revision: int
def tables(self) -> Sequence[TableInfo]    # id, name, units, z_label, decimals, step, lo, hi
def values(self, table_id) -> np.ndarray   # (n_rpm, n_map), read by the grid and the surface
def base(self, table_id) -> np.ndarray     # as loaded: changed markers and the delta
def adjust(self, table_id, cells, steps) -> None
def interpolate(self, table_id, cells) -> None
def smooth(self, table_id, cells) -> None
def undo(self) -> bool
def save(self, path) -> None
def can_write_chip(self) -> bool
```

`cells` is a list of `(rpm_index, map_index)`. Bump `revision` on every
change; the grid and the 3D surface reload on the next frame. Axis sizes are
read from the model, so a real P28 table that is not 20 x 10 works without a
UI change (rows are 24 px: more than 20 RPM rows needs a scroll or a smaller
row, which is not built).

## Demo mode

`hondalog --demo` wires `DemoSource` (the mockup's simulated car) for both
ECU and black box, `NullRecording`, `DemoMapper` and writable sample tables.
It exists for looking at the UI and for `tools/screenshots.py`. Nothing in
demo mode touches a port or writes a log. Remove or keep at integration; the
screens do not depend on it, but `tests/conftest.py` builds its `shell`
fixture from it.

## Tools

- `tools/bench_ui.py`: the performance measurement (see its docstring).
- `tools/screenshots.py`: every screen in both themes at 1366x768.
- `tools/smoke.py`: end to end against `hondalog-sim` on a pty, GUI included.
- `hondalog --profile`: frame time, CPU and latency in the bottom bar.
