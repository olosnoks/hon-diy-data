"""Worker thread that owns the serial link.

It reads the address map's blocks in priority order (link/scheduler.py),
decodes them and hands out Samples. Nothing else touches the port: the raw
tools and the OBD2 functions hand their work to this thread with ``submit``
and ``obd2``. No Qt in here, so it runs headless too.

What a Sample is now
--------------------
Blocks are read at different rates, so there is no "sweep" any more. A Sample
is emitted each time every fast block has been read once (one fast cycle,
about 20 a second). It carries the latest value of every channel; slow
channels simply repeat until their next read. ``Sample.stamps`` says when
each value was really taken, so nothing has to pretend that a row is one
instant. ``on_block`` delivers every single read for per-read logging.

Recovery
--------
* ECU stops answering (key off, stall with power loss): state NO_REPLY, the
  thread probes with one short read and a growing pause, re-sends the wake-up
  bytes now and then, and carries on by itself when the ECU is back.
* Port dies (cable pulled): state PORT_LOST, the thread reopens the port every
  0.25-2 s (the cable may come back under another ttyUSB number) and then goes
  through the same probing.
"""

from __future__ import annotations

import collections
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .addressmap import AddressMap
from .link.clock import REAL, Clock
from .link.planner import Block, CostModel, bus_load, plan_blocks, predict_rates, spans_from_channels
from .link.scheduler import RATE_CLASSES, ChannelRate, Scheduler, rate_class_of, target_hz
from .link.timing import LinkStats
from .protocol import NoReply, ProtocolError, fmt_hex
from .transport import Link, ReadResult, Transport, TransportError

if TYPE_CHECKING:
    from .obd2 import Obd2Session

# Link states
CONNECTING, OK, NO_REPLY, PORT_LOST, STOPPED = "connecting", "ok", "no reply", "port lost", "stopped"
# Poller.mode
HONDA, OBD2 = "honda", "obd2"

_FAILS_TO_NO_REPLY = 3         # failed block reads in a row (each already retried)
_PROBE_MIN_S, _PROBE_MAX_S = 0.05, 0.25
_REOPEN_MIN_S, _REOPEN_MAX_S = 0.25, 2.0
_REPORT_EVERY_S = 0.5
_REPLAN_EVERY = 256            # good reads between checks of the cost model
_SWITCH_BACK_S = 8.0           # grace for the ECU to drop an ISO session


@dataclass
class Sample:
    t: float                        # seconds since connect, at frame completion
    wall: datetime
    link: bool                      # False = the ECU is not answering
    values: dict[str, float | None] = field(default_factory=dict)
    #: channel name -> seconds since connect when that value was sampled by
    #: the ECU (frame completion minus the estimated latency)
    stamps: dict[str, float] = field(default_factory=dict)
    #: estimated age, at ``t``, of the data in the read that closed this sample
    latency: float = 0.0
    #: time.monotonic() (or the virtual clock) at ``t``
    mono: float = 0.0
    #: channels that changed hands in this sample (read since the last one)
    fresh: frozenset[str] = frozenset()


@dataclass(frozen=True)
class BlockRead:
    """One block read, for per-read logging (``Poller.on_block``)."""

    t: float                        # seconds since connect at frame completion
    mono: float
    addr: int
    data: bytes
    latency_s: float
    values: dict[str, float | None]


class Poller(threading.Thread):
    def __init__(self, transport: Transport, amap: AddressMap,
                 on_sample: Callable[[Sample], None] | None = None,
                 max_queue: int = 2000, clock: Clock = REAL) -> None:
        super().__init__(name="hondalog-poller", daemon=True)
        self.transport = transport
        self.amap = amap
        self.clock = clock
        self.link = Link(transport, amap.framing, clock)
        self.on_sample = on_sample                  # called on this thread
        self.on_block: Callable[[BlockRead], None] | None = None
        self.samples: collections.deque[Sample] = collections.deque(maxlen=max_queue)
        self._jobs: collections.deque[tuple[str, Callable[..., Any], Future[Any]]] = collections.deque()
        self._stop_evt = threading.Event()
        self.ready = threading.Event()              # set once ECU id was tried
        self.t0 = clock.now()
        self.port_name = transport.name
        # stats (plain attribute reads from the UI thread are fine)
        self.state = CONNECTING
        self.mode = HONDA
        self.reads = 0
        self.errors = 0
        self.sweeps = 0                             # samples emitted with link up
        self.hz = 0.0                               # sample (fast cycle) rate
        self.last_error = ""
        self.ecu_id: bytes | None = None
        #: achieved rate per channel, refreshed twice a second on this thread
        self.rates: dict[str, ChannelRate] = {}
        # plan
        self._class = {c.name: rate_class_of(c) for c in amap.channels}
        self._target = {n: target_hz(k, amap.framing) for n, k in self._class.items()}
        self.cost = CostModel.from_framing(amap.framing)
        self.plan: list[Block] = []
        self._sched = Scheduler([])
        self._decode: list[list[tuple[str, Any, int, int]]] = []
        self._burst: Scheduler | None = None
        self._burst_decode: list[list[tuple[str, Any, int, int]]] = []
        self._burst_until = 0.0
        self._build_plan()
        # running state
        self._values: dict[str, float | None] = {n: None for n in amap.names}
        self._stamps: dict[str, float] = {}
        self._fresh: set[str] = set()
        self._unread: set[int] = set(range(len(self.plan)))
        self._pending: set[int] = set(self._sched.top)
        self._fails = 0
        self._probes = 0
        self._probe_wait = _PROBE_MIN_S
        self._reopen_wait = _REOPEN_MIN_S
        self._last_emit: float | None = None
        self._next_report = 0.0
        self._next_replan = _REPLAN_EVERY
        self._quiet_until = 0.0

    # -- plan --

    def _build_plan(self, cost: CostModel | None = None) -> None:
        if cost is not None:
            self.cost = cost
        target = self._target
        spans = spans_from_channels(self.amap.channels, lambda c: target[c.name])
        self.plan = plan_blocks(spans, self.amap.framing.max_read_len, self.cost)
        self._sched = Scheduler(self.plan, self._class, target)
        by_name = {c.name: c for c in self.amap.channels}
        self._decode = [[(n, by_name[n], by_name[n].addr - b.addr, by_name[n].length)
                         for n in b.channels] for b in self.plan]
        self._unread = set(range(len(self.plan)))
        self._pending = set(self._sched.top)

    @property
    def blocks(self) -> list[tuple[int, int]]:
        """The planned reads as (addr, length), lowest address first."""
        return [(b.addr, b.length) for b in self.plan]

    def predicted_rates(self) -> dict[str, float]:
        """Reads per second per channel that the cost model promises."""
        out: dict[str, float] = {}
        for block, hz in zip(self.plan, predict_rates(self.plan, self.cost)):
            for name in block.channels:
                out[name] = hz
        return out

    @property
    def bus_load(self) -> float:
        """Share of the wire the targets need. Above 1.0 they cannot all be met."""
        return bus_load(self.plan, self.cost)

    # -- API for other threads --

    def submit(self, fn: Callable[[Link], Any]) -> Future[Any]:
        """Run fn(link) on the poller thread between reads."""
        fut: Future[Any] = Future()
        self._jobs.append(("link", fn, fut))
        return fut

    def obd2(self, fn: Callable[["Obd2Session"], Any]) -> Future[Any]:
        """Run fn(session) inside an ISO 9141-2 session on the poller thread.

        Honda polling pauses, the 5-baud init runs (about 2.5 s), fn gets an
        ``Obd2Session``, then the line goes back to 9600 baud and polling
        resumes once the ECU has let go of the ISO session (up to 5 s of
        silence; nothing is logged as a link loss meanwhile).
        """
        fut: Future[Any] = Future()
        self._jobs.append(("obd2", fn, fut))
        return fut

    def burst(self, names: list[str], seconds: float) -> Future[Any]:
        """Read only these channels, as fast as the line goes, for a while."""
        return self.submit(lambda link: self._start_burst(names, seconds))

    def channel_rates(self) -> dict[str, ChannelRate]:
        """Target and achieved reads per second for every channel."""
        return dict(self.rates)

    def class_rates(self) -> dict[str, tuple[float, float]]:
        """rate class -> (target Hz, worst achieved Hz among its channels)."""
        out: dict[str, tuple[float, float]] = {}
        for rate in self.rates.values():
            target, worst = out.get(rate.rate_class, (rate.target_hz, float("inf")))
            out[rate.rate_class] = (target, min(worst, rate.achieved_hz))
        return {k: out[k] for k in RATE_CLASSES if k in out}

    def shortfalls(self) -> dict[str, ChannelRate]:
        """Channels running below target."""
        return {n: r for n, r in self.rates.items() if not r.ok}

    @property
    def stats(self) -> LinkStats:
        """Link counters: timeouts, checksum failures, echo mismatches, resyncs..."""
        return self.link.stats

    def link_report(self) -> dict[str, float]:
        """Counters and measured timing in one flat dict (status bar, log header)."""
        out: dict[str, float] = dict(self.link.stats.as_dict())
        out.update(self.link.timing.as_dict())
        out["usb_latency_ms"] = 1000 * self.transport.usb_latency_s
        out["bus_load"] = self.bus_load
        out["sample_hz"] = self.hz
        return out

    def set_expert_unlock(self, on: bool) -> None:
        """Let the hex console send frames that are not reads (off by default)."""
        self.link.unlock(on)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_evt.set()
        if self.is_alive():
            self.join(timeout)

    @property
    def ecu_id_text(self) -> str:
        if self.ecu_id is None:
            return "(no reply)"
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in self.ecu_id)
        return f"{fmt_hex(self.ecu_id)}  \"{text}\""

    # -- thread body --

    def run(self) -> None:
        try:
            self._connect()
            self.ready.set()
            stop = self._stop_evt
            jobs = self._jobs
            while not stop.is_set():
                if jobs:
                    self._run_job()
                state = self.state
                if state == OK:
                    self._step()
                elif state == PORT_LOST:
                    self._emit_down()
                    self._sleep(self._reopen_wait)
                    self._reopen()
                else:
                    self._probe()
        finally:
            self.ready.set()
            self.state = STOPPED
            self.transport.close()
            self._fail_jobs("disconnected")

    def _sleep(self, seconds: float) -> None:
        if self.clock.virtual:
            self.clock.sleep(seconds)
        else:
            self._stop_evt.wait(seconds)

    def _connect(self) -> None:
        try:
            self.transport.open()
            self.link.reset()
            self.link.wake()
            self._read_ecu_id()
        except TransportError as exc:
            self._port_lost(exc)
            return
        if self.ecu_id is not None:
            self.state = OK
        else:
            self._emit_down()
            self.state = NO_REPLY

    def _read_ecu_id(self) -> None:
        if self.amap.ecu_id_addr is None:
            return
        for _ in range(2):
            try:
                self.ecu_id = self._read(self.amap.ecu_id_addr, self.amap.ecu_id_len).data
                return
            except ProtocolError:
                pass

    def _read(self, addr: int, length: int, retries: int | None = 0) -> ReadResult:
        """One counted read. Raises ProtocolError / TransportError."""
        try:
            result = self.link.read_block(addr, length, retries)
        except ProtocolError as exc:
            self.errors += 1
            self.last_error = f"{addr:02X}+{length}: {exc}"
            raise
        self.reads += 1
        return result

    # -- the hot loop: one block per call --

    def _step(self) -> None:
        clock = self.clock
        sched = self._burst or self._sched
        now = clock.now()
        if self._burst is not None and now >= self._burst_until:
            self._end_burst()
            sched = self._sched
        index = sched.next(now)
        block = sched.blocks[index]
        try:
            result = self.link.read_block(block.addr, block.length)
        except ProtocolError as exc:
            self.errors += 1
            self.last_error = f"{block.addr:02X}+{block.length}: {exc}"
            sched.failed(index, clock.now())
            # Silence that outlasted the retry means the ECU is gone; damaged
            # frames mean a bad line, which gets a few more chances.
            self._fails += _FAILS_TO_NO_REPLY if isinstance(exc, NoReply) else 1
            if self._fails >= _FAILS_TO_NO_REPLY:
                self._link_down()
                self._emit_down()           # tell the stall catcher now, not a probe later
            return
        except TransportError as exc:
            self._port_lost(exc)
            return
        self.reads += 1
        self._fails = 0
        t_done = result.t_done
        sched.done(index, t_done)

        # decode just this block
        data = result.data
        values, stamps, fresh = self._values, self._stamps, self._fresh
        t = t_done - self.t0
        t_sampled = t - result.latency_s
        decode = self._decode[index] if sched is self._sched else self._burst_decode[index]
        for name, channel, offset, length in decode:
            values[name] = channel.convert(data[offset:offset + length])
            stamps[name] = t_sampled
            fresh.add(name)
        if self.on_block is not None:
            try:
                self.on_block(BlockRead(t, t_done, block.addr, data, result.latency_s,
                                        {name: values[name] for name, _, _, _ in decode}))
            except Exception as exc:
                self.last_error = f"on_block: {exc!r}"

        # a sample per fast cycle, once every block has been seen
        self._unread.discard(index)
        pending = self._pending
        pending.discard(index)
        if not pending:
            pending.update(sched.top)
            if not self._unread:
                self._emit(t, t_done, result.latency_s)
        if t_done >= self._next_report:
            self._next_report = t_done + _REPORT_EVERY_S
            self.rates = self._sched.report(t_done)
        if self.reads >= self._next_replan:
            self._next_replan = self.reads + _REPLAN_EVERY
            self._maybe_replan()

    def _maybe_replan(self) -> None:
        """Re-plan if the measured per-request overhead is not what was assumed."""
        timing = self.link.timing
        if self._burst is not None or timing.srtt is None:
            return
        measured = CostModel.from_framing(self.amap.framing, turnaround_s=timing.overhead_s,
                                          gap_s=timing.gap)
        if abs(measured.overhead_s - self.cost.overhead_s) < 0.2 * self.cost.overhead_s:
            return
        old = self.blocks
        self.cost = measured
        spans = spans_from_channels(self.amap.channels, lambda c: self._target[c.name])
        if [(b.addr, b.length) for b in plan_blocks(
                spans, self.amap.framing.max_read_len, measured)] != old:
            self._build_plan(measured)

    def _emit(self, t: float, mono: float, latency: float) -> None:
        sample = Sample(t, datetime.now(), True, dict(self._values), dict(self._stamps),
                        latency, mono, frozenset(self._fresh))
        self._fresh.clear()
        last = self._last_emit
        if last is not None and t > last:
            inst = 1.0 / (t - last)
            self.hz = inst if self.hz == 0 else 0.9 * self.hz + 0.1 * inst
        self._last_emit = t
        self.sweeps += 1
        self._deliver(sample)

    def _emit_down(self) -> None:
        now = self.clock.now()
        self.hz = 0.0
        self._last_emit = None
        self._deliver(Sample(now - self.t0, datetime.now(), False, {}, {}, 0.0, now))

    def _deliver(self, sample: Sample) -> None:
        self.samples.append(sample)
        if self.on_sample is not None:
            try:
                self.on_sample(sample)
            except Exception as exc:        # a logging problem must not kill polling
                self.last_error = f"on_sample: {exc!r}"

    # -- burst --

    def _start_burst(self, names: list[str], seconds: float) -> list[tuple[int, int]]:
        channels = [c for c in self.amap.channels if c.name in set(names)]
        if not channels:
            raise ValueError(f"no such channels: {names}")
        blocks = plan_blocks(spans_from_channels(channels), self.amap.framing.max_read_len,
                             self.cost)
        self._burst = Scheduler(blocks, self._class, self._target)
        by_name = {c.name: c for c in channels}
        self._burst_decode = [[(n, by_name[n], by_name[n].addr - b.addr, by_name[n].length)
                               for n in b.channels] for b in blocks]
        self._burst_until = self.clock.now() + seconds
        self._pending = set(self._burst.top)
        return [(b.addr, b.length) for b in blocks]

    def _end_burst(self) -> None:
        self._burst = None
        self._sched.restart()
        self._pending = set(self._sched.top)

    # -- link down / recovery --

    def _link_down(self) -> None:
        self.state = NO_REPLY
        self._fails = 0
        self._probes = 0
        self._probe_wait = _PROBE_MIN_S
        self._burst = None
        self._values = {n: None for n in self.amap.names}
        self._stamps = {}
        self._fresh.clear()
        self.rates = {}

    def _link_up(self) -> None:
        self.state = OK
        self._fails = 0
        self._quiet_until = 0.0
        self.link.timing.relax()
        self._sched.restart()
        self._unread = set(range(len(self.plan)))
        self._pending = set(self._sched.top)
        if self.ecu_id is None:
            self._read_ecu_id()

    def _probe(self) -> None:
        """The ECU is silent: ask for one block now and then until it answers."""
        block = self.plan[0] if self.plan else Block(0, 1)
        quiet = self.clock.now() < self._quiet_until
        try:
            self._probes += 1
            if self._probes % 4 == 0:
                self.link.wake()            # the ECU may have just been powered up
            try:
                self._read(block.addr, block.length)
            except ProtocolError:
                if not quiet:
                    if self.state == CONNECTING:
                        self.state = NO_REPLY
                    self._emit_down()
                self._sleep(self._probe_wait)
                self._probe_wait = min(_PROBE_MAX_S, self._probe_wait * 1.5)
                return
        except TransportError as exc:
            self._port_lost(exc)
            return
        self._link_up()

    def _port_lost(self, exc: Exception) -> None:
        self.errors += 1
        self.last_error = f"port: {exc}"
        self._link_down()
        self.state = PORT_LOST
        self._reopen_wait = _REOPEN_MIN_S
        self.transport.close()
        self._fail_jobs(f"port lost: {exc}")

    def _reopen(self) -> None:
        try:
            self.transport.open()
        except TransportError as exc:
            self.last_error = f"port: {exc}"
            self._reopen_wait = min(_REOPEN_MAX_S, self._reopen_wait * 1.5)
            return
        self.link.stats.reconnects += 1
        self.link.reset()
        self.state = NO_REPLY
        self._probes = 3                    # wake on the very next probe

    # -- jobs --

    def _run_job(self) -> None:
        try:
            kind, fn, fut = self._jobs.popleft()
        except IndexError:
            return
        if not fut.set_running_or_notify_cancel():
            return
        if self.state == PORT_LOST:
            fut.set_exception(TransportError(self.last_error))
            return
        if kind == "obd2":
            self._run_obd2(fn, fut)
            return
        try:
            fut.set_result(fn(self.link))
        except TransportError as exc:
            fut.set_exception(exc)
            self._port_lost(exc)
        except Exception as exc:
            fut.set_exception(exc)

    def _run_obd2(self, fn: Callable[["Obd2Session"], Any], fut: Future[Any]) -> None:
        from .obd2 import Iso9141Link, Obd2Session
        self.mode = OBD2
        self.state = CONNECTING
        iso = Iso9141Link(self.transport, self.clock, honda_baud=self.amap.framing.baud)
        lost: TransportError | None = None
        try:
            iso.connect()
            fut.set_result(fn(Obd2Session(iso)))
        except TransportError as exc:
            lost = exc
            fut.set_exception(exc)
        except Exception as exc:
            fut.set_exception(exc)
        finally:
            try:
                iso.close()
            except TransportError as exc:
                lost = lost or exc
            self.mode = HONDA
        if lost is not None:
            self._port_lost(lost)
            return
        # Back to Honda reads. The ECU may hold the ISO session for up to 5 s
        # of silence; probe quietly instead of reporting a link loss.
        self.link.reset()
        self._link_down()
        self.state = CONNECTING
        self._probes = 3
        self._quiet_until = self.clock.now() + _SWITCH_BACK_S

    def _fail_jobs(self, why: str) -> None:
        while True:
            try:
                _, _, fut = self._jobs.popleft()
            except IndexError:
                return
            if fut.set_running_or_notify_cancel():
                fut.set_exception(TransportError(why))
