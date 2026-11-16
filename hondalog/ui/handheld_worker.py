"""The Handheld tab's link to the CYD, on a thread of its own: serial I/O never runs on the UI thread.

One ``HlWorker`` owns the port and the ``HlClient`` for as long as the tab is connected, whichever
sub-view is showing. Jobs (``submit``) run one after another on the thread; between jobs it reads the
port so console lines keep arriving. Everything comes back as Qt signals (queued to the UI thread).
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable

from PySide6.QtCore import QThread, Signal

from ..handheld import flasher
from ..handheld.hl_client import HlClient, HlError, reason_text


class HlWorker(QThread):
    state = Signal(str, str)            # "disconnected" | "connecting" | "connected" | "flashing", detail
    hello = Signal(dict)                # the HL HELLO fields, once a session is open
    console = Signal(str)               # a console line (ESP_LOG text, "< HL ..." for lines nobody asked for)
    progress = Signal(int, int, int, str)   # job id, done, total, what
    done = Signal(int, object, str)     # job id, result, error text ("" = ok)
    flash_progress = Signal(object)     # flasher.Progress

    def __init__(self, port_factory: Callable[[str], Any] | None = None, parent=None):
        super().__init__(parent)
        if port_factory is None:
            from ..handheld.serial_link import make_port
            port_factory = make_port
        self._factory = port_factory
        self._q: "queue.Queue[tuple[int, Callable[..., Any], bool]]" = queue.Queue()
        self._stop = threading.Event()
        self._next_id = 1
        self._lock = threading.Lock()
        self.client: HlClient | None = None
        self.port: Any = None
        self.port_name = ""
        self.connected = False
        self._flash_cancel = threading.Event()
        self.busy = 0                   # jobs queued or running

    # ---- from the UI thread ----

    def submit(self, fn: Callable[[HlClient, Callable[[int, int, str], None]], Any]) -> int:
        """Run ``fn(client, progress(done, total, what))`` on the thread. Returns the job id for ``done``/``progress``."""
        with self._lock:
            job = self._next_id
            self._next_id += 1
            self.busy += 1
        self._q.put((job, fn, True))
        return job

    def connect_port(self, name: str) -> int:
        """Open the port, find its speed, open a session. ``state`` says how it went."""
        with self._lock:
            job = self._next_id
            self._next_id += 1
            self.busy += 1
        self._q.put((job, lambda c, p, name=name: self._do_connect(name), False))
        return job

    def disconnect_port(self) -> int:
        with self._lock:
            job = self._next_id
            self._next_id += 1
            self.busy += 1
        self._q.put((job, lambda c, p: self._do_disconnect("disconnected"), False))
        return job

    def flash(self, image: str, baud: int) -> int:
        """Install a merged image: the port is released first and opened again after."""
        with self._lock:
            job = self._next_id
            self._next_id += 1
            self.busy += 1
        self._flash_cancel.clear()
        self._q.put((job, lambda c, p, image=image, baud=baud, job=job: self._do_flash(image, baud), False))
        return job

    def cancel(self) -> None:
        if self.client is not None:
            self.client.cancel_flag = True
        self._flash_cancel.set()

    def stop(self) -> None:
        self._stop.set()
        self.cancel()
        self.wait(3000)

    def send_line(self, text: str) -> None:
        """A line typed in the console, sent as it is (the replies come back as console lines)."""
        def go(c: HlClient, p: Callable[[int, int, str], None]) -> None:
            if not c.open:
                c.hello()
            c.port.write(text.encode("utf-8", "replace") + b"\n")
            c.deadline = time.monotonic() + 30.0
        self.submit(go)

    # ---- the thread ----

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                job, fn, needs_client = self._q.get(timeout=0.02 if self.client else 0.1)
            except queue.Empty:
                self._idle()
                continue
            err, result = "", None
            try:
                if needs_client and self.client is None:
                    raise HlError("disconnected")
                self.client and setattr(self.client, "cancel_flag", False)
                result = fn(self.client, lambda d, t, what="", job=job: self.progress.emit(job, d, t, what))  # type: ignore[arg-type]
            except HlError as exc:
                err = reason_text(exc)
                if exc.reason == "disconnected":
                    self._do_disconnect("the cable was disconnected")
            except OSError as exc:
                err = str(exc)
                self._do_disconnect(f"the port failed: {exc}")
            except Exception as exc:                # a port that went away raises CydError, ValueError ...
                err = str(exc) or type(exc).__name__
                if type(exc).__name__ == "CydError":
                    self._do_disconnect(f"the port failed: {exc}")
            finally:
                with self._lock:
                    self.busy -= 1
            self.done.emit(job, result, err)
        self._do_disconnect("")

    def _idle(self) -> None:
        c = self.client
        if c is None:
            return
        try:
            c.pump(0.03)
        except Exception as exc:
            self._do_disconnect(f"the port failed: {exc}")
            return
        while c.q:
            self.console.emit("< " + c.q.pop(0))

    def _do_connect(self, name: str) -> None:
        if self.client is not None:
            self._do_disconnect("")
        self.state.emit("connecting", name)
        port = None
        try:
            port = self._factory(name)
            port.open()
            client = HlClient(port, on_console=self.console.emit)
            client.connect()
            client.hello()
        except Exception as exc:
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass
            text = reason_text(exc)
            self.state.emit("disconnected", text)
            raise HlError(text) if not isinstance(exc, HlError) else exc
        self.client, self.port, self.port_name, self.connected = client, port, name, True
        self.state.emit("connected", name)
        self.hello.emit(dict(client.info))

    def _do_disconnect(self, why: str) -> None:
        c, p = self.client, self.port
        self.client = self.port = None
        was = self.connected
        self.connected = False
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
        if p is not None:
            try:
                p.close()
            except Exception:
                pass
        if was or why:
            self.state.emit("disconnected", why)

    def _do_flash(self, image: str, baud: int) -> None:
        name = self.port_name
        if not name:
            raise HlError("connect to the handheld first, so the tab knows its port")
        device = name[4:] if name.startswith("cyd:") else name
        device = device.partition("@")[0]
        self._do_disconnect("")             # the port is released: esptool opens it itself
        self.state.emit("flashing", name)
        try:
            flasher.flash(image, device, baud, self.flash_progress.emit, self.console.emit, self._flash_cancel)
        except flasher.FlashError as exc:
            self._reopen(name, wait=4.0)
            raise HlError(str(exc))
        self._reopen(name, wait=25.0)

    def _reopen(self, name: str, wait: float) -> None:
        """The board restarts after a flash (or a failed one): connect again, for up to ``wait`` seconds."""
        end = time.monotonic() + wait
        last = ""
        time.sleep(1.5)
        while time.monotonic() < end and not self._stop.is_set():
            try:
                self._do_connect(name)
                return
            except Exception as exc:
                last = str(exc)
                time.sleep(1.0)
        self.state.emit("disconnected", last or "the board did not answer after it restarted")
