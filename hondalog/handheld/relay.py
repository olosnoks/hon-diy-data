"""Relay: the laptop's own ECU reads to the handheld (firmware/handheld/SERIAL.md,
"Relay: the laptop's ECU data to the handheld").

``Relay`` listens to the laptop's ``Poller`` through ``LiveSession``'s listener
hooks (it never replaces the poller's callbacks) and forwards over the
``HandheldCompanion``'s link:

    on_block(BlockRead)   -> RELAY_READ  0x50   (host_us = request out, latency_us)
    on_pid(t, pid, data)  -> RELAY_ISO   0x51   (mode 0x41, OBD2-only mode)
    on_sample(Sample)     -> RELAY_TICK  0x52   (link up samples only)
    poller state          -> RELAY_LINK  0x53   (on every change and once a second)

``host_us`` is the poller's own monotonic clock in microseconds.

The hooks run on the poller thread and only pack a frame into a bounded queue
(the oldest is dropped and counted when it is full): a slow or stalled USB
link never holds up the K-line. A writer thread sends.

Relaying only happens to a two-way handheld whose HELLO sets CAP_RELAY (64)
and runs the laptop's map (HELLO map hash = ``amap.version_hash``); ``force``
skips those two checks for testing. ``status`` says why not otherwise.
"""

from __future__ import annotations

import collections
import threading
import time
from typing import Any

from .. import poller as pl
from . import serial_proto as P

LINK_EVERY_S = 1.0
QUEUE_MAX = 1024

TOO_OLD = "handheld: firmware too old for relay"
TX_ONLY = "handheld: TX-only (GPS on P1), no relay"
MAP_DIFFERS = "handheld map differs: hondalog-handheld push-map"


def link_fields(poller: Any) -> tuple[int, bool, bool]:
    """(LINK state, up, iso) of a laptop poller."""
    state, mode = getattr(poller, "state", pl.STOPPED), getattr(poller, "mode", pl.HONDA)
    iso = mode == pl.OBD2
    if state == pl.OK:
        return (P.LINK_ISO_ONLY if iso else P.LINK_OK), True, iso
    if state == pl.CONNECTING:
        return P.LINK_CONNECTING, False, iso
    return P.LINK_OFF, False, iso


class Relay:
    def __init__(self, companion: Any, poller: Any, amap: Any, force: bool = False, max_queue: int = QUEUE_MAX):
        self.companion = companion
        self.poller = poller
        self.amap = amap
        self.force = force
        self.max_queue = max_queue
        self.queued = self.sent = self.dropped = self.failed = 0
        self.last_error = ""
        self._q: collections.deque[tuple[int, bytes]] = collections.deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._link_last: tuple | None = None
        self._link_next = 0.0
        self._thread = threading.Thread(target=self._run, name="hondalog-relay", daemon=True)

    # -- whether to relay --

    @property
    def status(self) -> str:
        """'' while relaying (or waiting for the hello), else why not."""
        h = self.companion.hello
        if h is None:
            return ""
        if not h.two_way:
            return TX_ONLY
        if self.force:
            return ""
        if not h.caps & P.CAP_RELAY:
            return TOO_OLD
        if h.map_hash != self.amap.version_hash:
            return MAP_DIFFERS
        return ""

    @property
    def active(self) -> bool:
        return self.companion.hello is not None and not self.status

    def text(self) -> str:
        """For the info bar."""
        if self.status:
            return self.status
        if not self.active:
            return ""
        return f"relay to handheld: {self.sent} sent" + (f", {self.dropped} dropped" if self.dropped else "")

    # -- the poller thread: pack and queue, nothing else --

    def _put(self, ftype: int, payload: bytes) -> None:
        with self._lock:
            if len(self._q) >= self.max_queue:
                self._q.popleft()
                self.dropped += 1
            self._q.append((ftype, payload))
            self.queued += 1
        self._wake.set()

    def _clock_us(self) -> int:
        return int(self.poller.clock.now() * 1e6)

    def on_block(self, read: Any) -> None:
        if not self.active:
            return
        self._put(P.SR_RELAY_READ, P.pack_relay_read(int((read.mono - read.latency_s) * 1e6), read.addr,
                                                     read.data, read.latency_s * 1e6))

    def on_pid(self, t: float, pid: int, data: bytes) -> None:
        if not self.active:
            return
        self._put(P.SR_RELAY_ISO, P.pack_relay_iso(int((self.poller.t0 + t) * 1e6), pid, data, 0x41))

    def on_sample(self, sample: Any) -> None:
        if not self.active:
            return
        self.check_link()
        if sample.link:
            mono = sample.mono or (self.poller.t0 + sample.t)
            self._put(P.SR_RELAY_TICK, P.pack_relay_tick(int(mono * 1e6), sample.latency * 1e6,
                                                         getattr(self.poller, "hz", 0.0),
                                                         getattr(self.poller, "sweeps", 0)))

    def check_link(self, force: bool = False) -> None:
        """RELAY_LINK on a change of the poller's state, and once a second."""
        if not self.active:
            return
        fields = link_fields(self.poller)
        ecu = bytes(getattr(self.poller, "ecu_id", None) or b"")[:10]
        key = (*fields, ecu)
        now = time.monotonic()
        if not force and key == self._link_last and now < self._link_next:
            return
        self._link_last, self._link_next = key, now + LINK_EVERY_S
        self._put(P.SR_RELAY_LINK, P.pack_relay_link(self._clock_us(), fields[0], fields[1], fields[2], ecu))

    # -- the writer --

    def start(self) -> "Relay":
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.25)
            self._wake.clear()
            self.check_link()
            while not self._stop.is_set():
                with self._lock:
                    if not self._q:
                        break
                    ftype, payload = self._q.popleft()
                try:
                    self.companion.send(ftype, payload)
                    self.sent += 1
                except Exception as exc:            # port gone, handheld restarting: the next ones may go
                    self.failed += 1
                    self.last_error = str(exc)
