"""The seams between the UI and everything behind it.

The UI talks to backends only through the Protocol classes in this file.
``backends.py`` implements each one against what exists on main today (the
poller, the CSV logger, the simulator) plus small stopgaps where main has
nothing yet. The integration step replaces those implementations; the screens
do not change. See docs/ui-adapters.md.

No Qt in here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

import numpy as np

from .bus import ChannelBus, ChannelSpec

# ---- live data --------------------------------------------------------------

# SourceStatus.state values
OFF, CONNECTING, OK, SILENT, LOST = "off", "connecting", "ok", "silent", "lost"


@dataclass
class SourceStatus:
    state: str = OFF            # OFF / CONNECTING / OK / SILENT / LOST
    rate_hz: float = 0.0        # samples per second the source delivers
    errors: int = 0
    detail: str = ""            # short text for the chip: "no port", "waking K-line"
    silent_s: float = 0.0       # how long the device has not answered
    queue: int = 0              # samples waiting somewhere (profile overlay)
    simulated: bool = False


@runtime_checkable
class ChannelSource(Protocol):
    """Anything that produces channel values: ECU poller, black box, math."""

    name: str                   # ChannelSpec.source of its channels: "ECU", "BB", ...

    def specs(self) -> Sequence[ChannelSpec]:
        """Channels this source can deliver. May change after connect()."""

    def start(self, bus: ChannelBus) -> None:
        """Begin publishing into the bus (from any thread). Idempotent."""

    def stop(self) -> None:
        """Stop publishing and release the device. Idempotent."""

    def status(self) -> SourceStatus:
        """Cheap; called at a few Hz from the UI thread."""


@runtime_checkable
class Connectable(Protocol):
    """A ChannelSource the user points at a port (the K-line cable)."""

    port: str

    def ports(self) -> Sequence[tuple[str, str]]:
        """(device, label) choices for the connection menu."""

    def connect(self, port: str) -> None: ...

    def disconnect(self) -> None: ...

    def set_verified(self, channel_id: str, verified: bool) -> str:
        """Mark a channel's address verified. Returns a message for the user."""


# ---- alarms -----------------------------------------------------------------

@dataclass
class AlarmEvent:
    channel_id: str
    source: str                 # "ECU" / "BLACK BOX"
    label: str
    severity: str               # "warn" or "alarm"
    value_text: str
    limit_text: str             # "above 45 °C"
    t_start: float              # bus time
    t_end: float | None = None  # None while active
    acked: bool = False


@dataclass
class LimitRow:
    name: str
    warn: str                   # "<12.8", ">100", "—"
    alarm: str


@runtime_checkable
class AlarmSource(Protocol):
    def evaluate(self, bus: ChannelBus, now: float) -> Sequence[AlarmEvent]:
        """Update states from the bus. Returns alarms that started just now
        (the shell beeps and auto-marks for severity 'alarm')."""

    def state(self, channel_id: str) -> str:
        """'' / 'warn' / 'alarm' for a tile."""

    def unacked(self, channel_id: str) -> bool:
        """True while an alarm has not been acknowledged (the icon blinks)."""

    def active(self) -> Sequence[AlarmEvent]:
        """Alarms first, then warnings, newest first."""

    def history(self) -> Sequence[AlarmEvent]:
        """Cleared events, newest first."""

    def acknowledge(self) -> None: ...

    def limits(self) -> Sequence[LimitRow]: ...

    @property
    def revision(self) -> int:
        """Bumps whenever active() or history() would return something new."""


# ---- recording and the stall catcher ----------------------------------------

@dataclass
class StallStatus:
    state: str = "off"          # off / standby / armed / saving / triggered
    buffer_s: float = 0.0       # pre-trigger seconds held right now
    pre_s: float = 30.0
    post_s: float = 10.0
    post_left_s: float = 0.0    # while saving
    count: int = 0              # stalls this drive
    last_file: str = ""


@dataclass
class StallCapture:
    path: Path
    reason: str                 # "rpm" or "link"
    wall: object                # datetime of the trigger
    t: float = 0.0


@runtime_checkable
class Recording(Protocol):
    active: bool
    path: Path | None
    marks: int

    def start(self) -> str:
        """Start a log. Returns '' or an error message."""

    def stop(self) -> None: ...

    def mark(self, text: str | None = None) -> int:
        """Drop a marker. Returns its number, or 0 when not recording."""

    def elapsed_s(self) -> float: ...

    def last_mark_age_s(self) -> float | None: ...

    def stall(self) -> StallStatus: ...

    def poll(self) -> Sequence[StallCapture]:
        """Stall captures finished since the last call."""


# ---- logs and forensics -----------------------------------------------------

@dataclass
class LogView:
    """One log or stall capture, ready to plot."""
    path: Path
    title: str = ""
    note: str = ""                              # "simulated capture"
    meta: dict = field(default_factory=dict)
    specs: dict = field(default_factory=dict)   # id -> ChannelSpec
    t: dict = field(default_factory=dict)       # id -> time array (s, own rate)
    data: dict = field(default_factory=dict)    # id -> value array
    markers: list = field(default_factory=list)  # (t, text), MARKs only
    trigger_t: float | None = None              # stall time, None for plain logs
    align_residual_ms: float | None = None      # ECU vs black-box alignment
    live: bool = False

    def span(self) -> tuple[float, float]:
        lo = min((float(t[0]) for t in self.t.values() if len(t)), default=0.0)
        hi = max((float(t[-1]) for t in self.t.values() if len(t)), default=1.0)
        return lo, max(hi, lo + 1e-3)

    def value_at(self, channel_id: str, t: float) -> float:
        tt = self.t.get(channel_id)
        if tt is None or not len(tt):
            return float("nan")
        i = int(np.searchsorted(tt, t, side="right")) - 1
        return float(self.data[channel_id][max(0, min(i, len(tt) - 1))])


@dataclass
class LossRow:
    t: float                    # seconds relative to the trigger (negative = before)
    channel_id: str
    signal: str                 # "FP FEED · black box"
    what: str                   # "fell below 11 V"


@dataclass
class Forensics:
    loss: list = field(default_factory=list)    # LossRow, first to fall first
    held: str = ""              # "Held up through the stall" sentence
    reading: str = ""           # one-sentence reading
    summary: str = ""           # for the stall banner: "FP FEED fell first, 2.1 s earlier."


@runtime_checkable
class LogReader(Protocol):
    def read(self, path) -> LogView:
        """Raises OSError / ValueError with a message fit for the user."""

    def analyze(self, view: LogView) -> Forensics:
        """Order of loss etc. Computed from the data, never typed."""

    def recent(self, stalls_only: bool = False) -> Sequence[Path]:
        """Newest first."""


# ---- observed map (Map 3D) --------------------------------------------------

@runtime_checkable
class ObservedMap(Protocol):
    """RPM x MAP cells accumulated from logged data."""

    rpm_axis: np.ndarray        # NX break points
    map_axis: np.ndarray        # NY break points
    revision: int               # bumps when cells change

    def update(self, bus: ChannelBus) -> None:
        """Fold new live samples in. Called once per UI frame; must be cheap."""

    def hits(self) -> np.ndarray:
        """(NY, NX) sample counts."""

    def mean(self, key: str) -> np.ndarray:
        """(NY, NX) mean of 'inj', 'ign' or 'trim'. NaN where hits == 0."""

    def clear(self) -> None: ...


# ---- raw tools / mapper -----------------------------------------------------

@dataclass
class Candidate:
    addr: int
    score: float                # 0..1, how closely the byte followed the stimulus
    lo: int
    hi: int


@dataclass
class WizardView:
    steps: tuple = ("Target", "Baseline", "Stimulus", "Candidates", "Confirm")
    step: int = 0
    target: str = ""            # "Throttle (TPS)"
    title: str = ""
    body: str = ""
    column: str = "FOLLOWS STIMULUS"
    candidates: list = field(default_factory=list)
    targets: list = field(default_factory=list)     # step 0: things to look for
    target_index: int = 0
    primary: str = "Next"       # label of the primary button
    can_back: bool = False
    busy: float | None = None   # 0..1 progress while a timed step runs


@runtime_checkable
class MapperSession(Protocol):
    """Memory sweep, hex console and the guided mapping wizard.

    Arrays are length 256 and owned by the session; the UI only reads them.
    """

    cur: np.ndarray             # int16, -1 = not read yet
    fresh: np.ndarray           # bool: changed in the latest sweep
    changes: np.ndarray         # int32: changes since clear
    minv: np.ndarray
    maxv: np.ndarray
    sweeps: int
    sweep_s: float
    sweeping: bool
    revision: int               # bumps when any of the above changed

    def available(self) -> bool:
        """False when there is no link to sweep."""

    def set_sweeping(self, on: bool) -> None: ...

    def clear_changes(self) -> None: ...

    def poll(self) -> None:
        """Harvest finished reads. Called once per UI frame."""

    def history(self, addr: int) -> np.ndarray:
        """Recent values of one byte, oldest first (for the inspector)."""

    def mapped(self, addr: int) -> tuple[str, bool] | None:
        """(channel name, verified) when the address map uses this byte."""

    def send(self, data: bytes, add_checksum: bool) -> None: ...

    def console(self) -> str:
        """Latest RX line for the hex console."""

    def export(self, path) -> None: ...

    def wizard(self) -> WizardView: ...

    def wizard_pick_target(self, index: int) -> None: ...

    def wizard_next(self, selected_addr: int | None = None) -> None: ...

    def wizard_back(self) -> None: ...

    def wizard_retry(self) -> None: ...


# ---- tuning tables ----------------------------------------------------------

@dataclass
class TableInfo:
    id: str
    name: str                   # "Fuel · low cam"
    units: str                  # "ms"
    z_label: str                # "INJ ms"
    decimals: int
    step: float                 # one key press
    lo: float                   # display range
    hi: float


@runtime_checkable
class TableModel(Protocol):
    """The ROM tables being edited. Arrays are (n_rpm, n_map)."""

    rpm_axis: np.ndarray
    map_axis: np.ndarray
    writable: bool
    read_only_reason: str
    revision: int               # bumps on every change

    def tables(self) -> Sequence[TableInfo]: ...

    def values(self, table_id: str) -> np.ndarray: ...

    def base(self, table_id: str) -> np.ndarray:
        """Values as loaded, for the changed markers and the delta."""

    def adjust(self, table_id: str, cells: Sequence[tuple[int, int]], steps: float) -> None: ...

    def interpolate(self, table_id: str, cells: Sequence[tuple[int, int]]) -> None: ...

    def smooth(self, table_id: str, cells: Sequence[tuple[int, int]]) -> None: ...

    def undo(self) -> bool: ...

    def save(self, path) -> None: ...

    def can_write_chip(self) -> bool: ...
