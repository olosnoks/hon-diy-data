"""Worker thread that owns the serial link.

It sweeps the address map's read blocks in a loop and turns each sweep into a
Sample. Nothing else touches the port: the raw tools hand their work to this
thread with ``submit``. No Qt in here, so it runs headless too.
"""

from __future__ import annotations

import collections
import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime

from .addressmap import AddressMap
from .protocol import ProtocolError, fmt_hex
from .transport import Link, Transport, TransportError

# Link states
CONNECTING, OK, NO_REPLY, PORT_LOST, STOPPED = "connecting", "ok", "no reply", "port lost", "stopped"


@dataclass
class Sample:
    t: float                        # seconds since connect
    wall: datetime
    link: bool                      # False = nothing valid came back this sweep
    values: dict = field(default_factory=dict)   # channel name -> float | None


class Poller(threading.Thread):
    def __init__(self, transport: Transport, amap: AddressMap, on_sample=None,
                 max_queue: int = 2000):
        super().__init__(name="hondalog-poller", daemon=True)
        self.transport = transport
        self.amap = amap
        self.link = Link(transport, amap.framing)
        self.on_sample = on_sample                  # called on this thread
        self.samples: collections.deque[Sample] = collections.deque(maxlen=max_queue)
        self.blocks = amap.blocks()
        self._jobs: queue.Queue = queue.Queue()
        self._stop_evt = threading.Event()
        self.ready = threading.Event()              # set once ECU id was tried
        self.t0 = time.monotonic()
        # stats (plain attribute reads from the UI thread are fine)
        self.state = CONNECTING
        self.reads = 0
        self.errors = 0
        self.sweeps = 0
        self.hz = 0.0
        self.last_error = ""
        self.ecu_id: bytes | None = None

    # -- API for other threads --

    def submit(self, fn) -> Future:
        """Run fn(link) on the poller thread between polls."""
        fut: Future = Future()
        self._jobs.put((fn, fut))
        return fut

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
            while not self._stop_evt.is_set():
                self._run_jobs(limit=1)
                if self.state == PORT_LOST:
                    self._emit({}, link=False)
                    self._stop_evt.wait(0.25)
                    self._reopen()
                else:
                    self._sweep()
        finally:
            self.ready.set()
            self.state = STOPPED
            self.transport.close()
            self._fail_jobs("disconnected")

    def _connect(self) -> None:
        try:
            self.transport.open()
            self.link.wake()
            if self.amap.ecu_id_addr is not None:
                for _ in range(2):
                    try:
                        self.ecu_id = self._read(self.amap.ecu_id_addr, self.amap.ecu_id_len)
                        break
                    except ProtocolError:
                        pass
        except TransportError as exc:
            self._port_lost(exc)

    def _read(self, addr: int, length: int) -> bytes:
        """One counted read. Raises ProtocolError / TransportError."""
        try:
            data = self.link.read_mem(addr, length)
        except ProtocolError as exc:
            self.errors += 1
            self.last_error = f"{addr:02X}+{length}: {exc}"
            raise
        self.reads += 1
        time.sleep(self.amap.framing.inter_request_delay_s)
        return data

    def _sweep(self) -> None:
        mem: dict[int, int] = {}
        # While the ECU is silent, probe with one block so samples keep coming.
        blocks = self.blocks[:1] if self.state == NO_REPLY else self.blocks
        try:
            if self.state == NO_REPLY and self.sweeps % 8 == 0:
                self.link.wake()            # ECU may have just been powered up
            for addr, length in blocks:
                try:
                    data = self._read(addr, length)
                except ProtocolError:
                    continue
                mem.update((addr + i, b) for i, b in enumerate(data))
                self._run_jobs(limit=1)     # raw tools share the line 1:1
        except TransportError as exc:
            self._port_lost(exc)
            return
        self.sweeps += 1
        if mem:
            self.state = OK
        elif self.state in (OK, CONNECTING):
            self.state = NO_REPLY
        self._emit(self.amap.decode(mem) if mem else {}, link=bool(mem))

    def _emit(self, values: dict, link: bool) -> None:
        now = time.monotonic()
        sample = Sample(now - self.t0, datetime.now(), link, values)
        last = getattr(self, "_last_emit", None)
        if last is not None and link and now > last:
            inst = 1.0 / (now - last)
            self.hz = inst if self.hz == 0 else 0.8 * self.hz + 0.2 * inst
        elif not link:
            self.hz = 0.0
        self._last_emit = now
        self.samples.append(sample)
        if self.on_sample is not None:
            try:
                self.on_sample(sample)
            except Exception as exc:        # a logging problem must not kill polling
                self.last_error = f"on_sample: {exc!r}"

    def _port_lost(self, exc: Exception) -> None:
        self.errors += 1
        self.last_error = f"port: {exc}"
        self.state = PORT_LOST
        self.transport.close()
        self._fail_jobs(f"port lost: {exc}")

    def _reopen(self) -> None:
        try:
            self.transport.open()
        except TransportError as exc:
            self.last_error = f"port: {exc}"
            return
        self.state = NO_REPLY

    def _run_jobs(self, limit: int = 1) -> None:
        for _ in range(limit):
            try:
                fn, fut = self._jobs.get_nowait()
            except queue.Empty:
                return
            if not fut.set_running_or_notify_cancel():
                continue
            if self.state == PORT_LOST:
                fut.set_exception(TransportError(self.last_error))
                continue
            try:
                fut.set_result(fn(self.link))
            except TransportError as exc:
                fut.set_exception(exc)
                self._port_lost(exc)
            except Exception as exc:
                fut.set_exception(exc)

    def _fail_jobs(self, why: str) -> None:
        while True:
            try:
                _, fut = self._jobs.get_nowait()
            except queue.Empty:
                return
            if fut.set_running_or_notify_cancel():
                fut.set_exception(TransportError(why))
