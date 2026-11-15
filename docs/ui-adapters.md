# UI adapters: what the integration step has to wire

The UI (`hondalog/ui/`) never imports a backend module. It talks to seven
small Protocol classes in `hondalog/ui/adapters.py`. Today each one is
implemented in `hondalog/ui/backends.py` against what is on main (the 0.1.0
poller, CSV logger and simulator), with a stopgap where main has nothing.

**To integrate W1-W5, edit one function:** `build_session()` at the bottom of
`hondalog/ui/session.py`. Replace the object passed for each role. No screen
changes. `tests/test_ui_backends.py::test_implementations_satisfy_the_protocols`
shows the `isinstance` check each replacement must pass.

```
ECU poller ----.                                      .-- Sensors, Drive (tiles)
black box ------+--> ChannelBus --(read on the 33 ms --+-- Map 3D, Tune (live point)
math channels --'    (any thread)   frame timer)       '-- Replay live lanes, alarms
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

## The seven roles

| Role | Protocol | Today (`backends.py`) | Replace with | Status today |
|---|---|---|---|---|
| ECU link | `ChannelSource` + `Connectable` | `PollerSource` over `hondalog.poller.Poller` | W1 link/scheduler | real |
| Black box | `ChannelSource` | `SimBlackBox`: derives 8 channels from a simulated ECU; "not found" on a real port | W2 `hondalog/blackbox/` reader | stopgap |
| Alarms | `AlarmSource` | `LimitAlarms`: spec limits, 2 s latch, acknowledge | W3 alarms | stopgap |
| Recording, stall catcher | `Recording` | `CsvRecording` over `hondalog.logger.Recorder` | W4 native log + trigger engine | real, reads two private fields |
| Logs, forensics | `LogReader` | `CsvLogReader` + `order_of_loss()` | W4 reader + analysis | reader real, analysis stopgap |
| Observed map | `ObservedMap` | `HistogramMap`: this session only, in memory | W4 histogram | stopgap |
| Raw tools, wizard | `MapperSession` | `PollerMapper`: sweep and console are real; wizard is a small Pearson ranker | W3 mapping wizard, W1 command guard | part real |
| Tables | `TableModel` | `DemoTableModel`: sample 20 x 10 tables, read-only | W5 `hondalog/tuning/` | stopgap |

Math channels (W3) need no adapter of their own: implement `ChannelSource`
with `name = "MATH"`, register specs with `source="MATH"`, publish. The
Sensors screen adds a "Math channels" group and, past 32 tiles, switches to
the compact 112 px rows by itself.

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

- `Session.pump` currently looks for `ecu.poller` (to notice a dead thread)
  and `ecu.amap` (map name in the tab bar, mapper lookups). Keep those two
  attributes or change the three places that use `getattr(self.ecu, ...)`.
- `PollerSource.set_verified` only marks the channel for this session.
  The real one should write `status = "verified"` to the address map.
- `PollerSource.sink` is how the recorder taps samples. With W4's log format
  the recorder should subscribe to the bus or to the link directly.

W2: the eight black-box ids the layouts assume are `bbatt, ecub, fpv, ig1,
tach, igt, injp, bbmark` (`BLACKBOX_SPECS`). The default Drive view and the
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
event it returns with severity `alarm`, the shell beeps twice and drops an
auto-MARK through `Recording.mark`. The limits in `backends.DEFAULT_LIMITS`
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

`CsvRecording.stall()` reads `Recorder._stall._armed`, `._ring` and
`._trigger` because 0.1.0 has no public status. `standby` (recording, engine
not seen running yet) is a state the handoff does not have; drop it if the
trigger engine arms differently.

### LogReader (W4)

```python
def read(self, path) -> LogView       # raises OSError / ValueError with a user message
def analyze(self, view) -> Forensics  # order of loss, "held up", reading, banner summary
def recent(self, stalls_only=False) -> Sequence[Path]   # newest first
```

`LogView` holds per-channel time and value arrays (`t[id]`, `data[id]`), so
1 kHz black-box channels and 10 Hz ECU channels sit in one view at their own
rates. Times are seconds relative to the stall trigger (`trigger_t == 0.0`)
or to the start of the file (`trigger_t is None`). Set `align_residual_ms`
and the Replay header shows it when it exceeds 5 ms.

`order_of_loss()` is the stopgap analysis: band = what the channel did in the
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
the newest `stall_*.csv` files through the `LogReader`.

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
fills a `WizardView`. `PollerMapper.send` goes straight to `link.raw`: route
it through W1's command guard.

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
