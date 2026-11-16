"""Loads the handheld firmware's portable core (hhcore) as a shared library,
so tests can run the real C against the Python it was ported from. Needs a C
compiler; tests that use it are skipped without one.

``Handheld`` is the whole engine on a simulated card and a simulated K-line:
the Python ``EcuModel`` behind a ``LoopbackTransport`` on a ``VirtualClock``,
with exact 9600-baud byte timing, the cable's echo and the simulator's faults.
"""

from __future__ import annotations

import ctypes as C
import shutil
import subprocess
from pathlib import Path

from hondalog.link.clock import VirtualClock
from hondalog.sim import EcuModel
from hondalog.transport import LoopbackTransport, TransportError

HOST_DIR = Path(__file__).resolve().parent.parent / "firmware" / "handheld" / "test" / "host"
_lib = None

U8P, U16P, DP, FP, IP = (C.POINTER(C.c_uint8), C.POINTER(C.c_uint16), C.POINTER(C.c_double),
                         C.POINTER(C.c_float), C.POINTER(C.c_int))


class Callbacks(C.Structure):
    _fields_ = [("open", C.CFUNCTYPE(C.c_int)), ("close", C.CFUNCTYPE(None)),
                ("write", C.CFUNCTYPE(C.c_int, U8P, C.c_size_t)),
                ("read_some", C.CFUNCTYPE(C.c_int, U8P, C.c_size_t, C.c_double)),
                ("flush_input", C.CFUNCTYPE(None)), ("set_baud", C.CFUNCTYPE(C.c_int, C.c_uint32)),
                ("set_break", C.CFUNCTYPE(C.c_int, C.c_int)), ("now", C.CFUNCTYPE(C.c_double)),
                ("sleep", C.CFUNCTYPE(None, C.c_double)), ("usb_latency_s", C.c_double)]


def lib(build_dir: Path):
    """Build (once per session) and load libhhhost.so. None if no compiler."""
    global _lib
    if _lib is not None:
        return _lib
    if not (shutil.which("cc") and shutil.which("make")):
        return None
    out = Path(build_dir)
    subprocess.run(["make", "-s", "-C", str(HOST_DIR), "lib", f"OUT={out}"], check=True)
    L = C.CDLL(str(out / "libhhhost.so"))
    L.hhhost_new.restype = C.c_void_p
    L.hhhost_new.argtypes = [C.c_char_p, C.c_char_p, C.c_size_t, C.c_char_p, C.POINTER(Callbacks),
                             C.c_uint32, C.c_uint32]
    L.hhhost_free.argtypes = [C.c_void_p]
    L.hhhost_run.argtypes = [C.c_void_p, C.c_int]
    L.hhhost_run_until.argtypes = [C.c_void_p, C.c_double]
    L.hhhost_gps.argtypes = [C.c_void_p, C.c_char_p]
    L.hhhost_pps_edge.argtypes = [C.c_void_p, C.c_int64]
    L.hhhost_gps_at.argtypes = [C.c_void_p, C.c_char_p, C.c_int64]
    L.hhhost_set_write_budget.argtypes = [C.c_void_p, C.c_long]
    L.hhhost_set_stale.argtypes = [C.c_void_p, C.c_char_p]
    L.hhhost_time_set.restype = C.c_int64
    L.hhhost_time_set.argtypes = [C.c_void_p]
    L.hhhost_get.restype = C.c_double
    L.hhhost_get.argtypes = [C.c_void_p, C.c_char_p, C.c_int]
    L.hhhost_text.argtypes = [C.c_void_p, C.c_char_p, C.c_char_p, C.c_size_t]
    for name in ("hhhost_mark", "hhhost_ack", "hhhost_request_dtc"):
        getattr(L, name).argtypes = [C.c_void_p]
    L.hhhost_mark_at.argtypes = [C.c_void_p, C.c_int64]
    L.hhhost_plan.argtypes = [C.c_int, U8P, U8P, DP, C.c_int, C.c_uint32, C.c_double, C.c_double,
                              U16P, U16P, DP, U8P, U8P, U8P, DP]
    L.hhhost_sched_new.restype = C.c_void_p
    L.hhhost_sched_new.argtypes = [C.c_int, DP]
    L.hhhost_sched_free.argtypes = [C.c_void_p]
    L.hh_sched_next.argtypes = [C.c_void_p, C.c_double]
    L.hh_sched_done.argtypes = [C.c_void_p, C.c_int, C.c_double]
    L.hh_sched_failed.argtypes = [C.c_void_p, C.c_int, C.c_double]
    L.hh_sched_restart.argtypes = [C.c_void_p]
    L.hhhost_eval.restype = C.c_double
    L.hhhost_eval.argtypes = [C.c_char_p, C.c_size_t, DP, C.c_uint16, FP, C.c_uint16, C.c_char_p, C.c_uint8,
                              C.c_double, C.c_double]
    L.hh_expr_check.argtypes = [C.c_char_p, C.c_size_t, C.c_uint16, C.c_uint16]
    L.hhhost_map_check.argtypes = [C.c_char_p, C.c_size_t]
    L.hhhost_map_decode.restype = C.c_double
    L.hhhost_map_decode.argtypes = [C.c_char_p, C.c_size_t, C.c_int, C.c_char_p]
    L.hhhost_map_math.restype = C.c_double
    L.hhhost_map_math.argtypes = [C.c_char_p, C.c_size_t, C.c_int, FP, C.c_double]
    L.hhhost_alarms_new.restype = C.c_void_p
    L.hhhost_alarms_new.argtypes = [C.c_char_p, C.c_size_t]
    L.hhhost_alarms_free.argtypes = [C.c_void_p]
    L.hhhost_alarms_update.argtypes = [C.c_void_p, FP, C.c_int, C.c_double, IP, C.c_int]
    L.hhhost_alarms_ack.argtypes = [C.c_void_p, C.c_double, IP, C.c_int]
    L.hhhost_alarms_state.argtypes = [C.c_void_p, C.c_int]
    L.hhhost_alarms_top.argtypes = [C.c_void_p]
    L.hh_checksum.restype = C.c_uint8
    L.hh_checksum.argtypes = [C.c_char_p, C.c_size_t]
    L.hh_frame_allowed.argtypes = [C.c_void_p, C.c_char_p, C.c_size_t]
    L.hh_iso_allowed.argtypes = [C.c_char_p, C.c_size_t]
    L.hhhost_serial.restype = C.c_void_p
    L.hhhost_serial.argtypes = [C.c_void_p, C.c_int, C.c_uint32]
    L.hhhost_serial_poll.restype = C.c_size_t
    L.hhhost_serial_poll.argtypes = [C.c_void_p, U8P, C.c_size_t]
    L.hhhost_serial_rx.argtypes = [C.c_void_p, C.c_char_p, C.c_size_t]
    L.hhhost_serial_tick.argtypes = [C.c_void_p]
    L.hhhost_serial_stat.restype = C.c_uint32
    L.hhhost_serial_stat.argtypes = [C.c_void_p, C.c_char_p]
    _lib = L
    return L


def plan(L, channels, max_len=16, baud=9600, turnaround_s=0.002, gap_s=0.002):
    """C planner over (addr, length, rate_hz) channels. Returns
    ([(addr, length, rate, (channel indexes...))], bus load)."""
    n = len(channels)
    addr = (C.c_uint8 * n)(*[c[0] for c in channels])
    length = (C.c_uint8 * n)(*[c[1] for c in channels])
    rate = (C.c_double * n)(*[c[2] for c in channels])
    b_addr, b_len = (C.c_uint16 * 64)(), (C.c_uint16 * 64)()
    b_rate, b_first, b_count = (C.c_double * 64)(), (C.c_uint8 * 64)(), (C.c_uint8 * 64)()
    order, load = (C.c_uint8 * 48)(), C.c_double()
    nb = L.hhhost_plan(n, addr, length, rate, max_len, baud, turnaround_s, gap_s, b_addr, b_len, b_rate,
                       b_first, b_count, order, C.byref(load))
    assert nb >= 0
    return ([(b_addr[i], b_len[i], b_rate[i], tuple(order[b_first[i]:b_first[i] + b_count[i]]))
             for i in range(nb)], load.value)


class Handheld:
    """The engine against the Python ECU simulator, on virtual time."""

    def __init__(self, L, sd: Path, map_bytes: bytes | None = None, cfg: str = "", boot_no: int = 1,
                 last_drive_no: int = 0, usb_latency_s: float = 0.001, **model):
        self.L = L
        self.sd = Path(sd)
        self.clock = VirtualClock()
        model.setdefault("restart_after_s", None)
        model.setdefault("rev_every_s", 1e9)
        self.model = EcuModel(clock=self.clock.now, **model)
        self.transport = LoopbackTransport(self.model, self.clock, byte_timing=True, usb_latency_s=usb_latency_s)
        self.sent: list[tuple[int, bytes]] = []         # (baud, bytes) of everything written
        tr = self.transport

        def guard(fn, ok=0):
            def run(*args):
                try:
                    out = fn(*args)
                except TransportError:
                    return -1
                return ok if out is None else out
            return run

        def write(data, n):
            payload = bytes(data[:n])
            self.sent.append((tr.baud, payload))
            tr.write(payload)

        def read_some(buf, size, timeout):
            chunk = tr.read_some(size, timeout)
            C.memmove(buf, chunk, len(chunk))
            return len(chunk)

        def flush():
            try:
                tr.flush_input()
            except TransportError:
                pass

        self._cb = Callbacks(
            Callbacks._fields_[0][1](guard(tr.open)), Callbacks._fields_[1][1](tr.close),
            Callbacks._fields_[2][1](guard(write)), Callbacks._fields_[3][1](guard(read_some)),
            Callbacks._fields_[4][1](flush), Callbacks._fields_[5][1](guard(tr.set_baud)),
            Callbacks._fields_[6][1](guard(lambda on: tr.set_break(bool(on)))),
            Callbacks._fields_[7][1](self.clock.now), Callbacks._fields_[8][1](self.clock.sleep), usb_latency_s)
        self.h = L.hhhost_new(str(self.sd).encode(), map_bytes, len(map_bytes) if map_bytes else 0,
                              cfg.encode() if cfg else None, C.byref(self._cb), boot_no, last_drive_no)
        assert self.h, "the map did not load"

    def run_until(self, t: float) -> None:
        self.L.hhhost_run_until(self.h, t)

    def run_for(self, seconds: float) -> None:
        self.run_until(self.clock.now() + seconds)

    def get(self, what: str, index: int = 0) -> float:
        return self.L.hhhost_get(self.h, what.encode(), index)

    def text(self, what: str) -> str:
        buf = C.create_string_buffer(64)
        self.L.hhhost_text(self.h, what.encode(), buf, 64)
        return buf.value.decode("utf-8", "replace")

    def gps(self, sentence: str) -> None:
        body = sentence.strip().lstrip("$").split("*")[0]
        check = 0
        for ch in body.encode():
            check ^= ch
        self.L.hhhost_gps(self.h, f"${body}*{check:02X}\r\n".encode())

    def pps_edge(self, t_us: int) -> None:
        """A rising edge of the GPS pulse, device time (esp_timer) in microseconds."""
        self.L.hhhost_pps_edge(self.h, int(t_us))

    def gps_at(self, sentence: str, t_us: int) -> None:
        """Like gps(), with the device time the bytes were read at (what the pulse pairing uses)."""
        body = sentence.strip().lstrip("$").split("*")[0]
        check = 0
        for ch in body.encode():
            check ^= ch
        self.L.hhhost_gps_at(self.h, f"${body}*{check:02X}\r\n".encode(), int(t_us))

    def mark(self) -> None:
        self.L.hhhost_mark(self.h)

    def mark_at(self, t_us: int) -> None:
        self.L.hhhost_mark_at(self.h, t_us)

    def ack(self) -> None:
        self.L.hhhost_ack(self.h)

    def request_dtc(self) -> None:
        self.L.hhhost_request_dtc(self.h)

    def power_cut_after(self, nbytes: int) -> None:
        self.L.hhhost_set_write_budget(self.h, nbytes)

    def stale(self, path: Path) -> None:
        self.L.hhhost_set_stale(self.h, str(path).encode())

    @property
    def time_set(self) -> int:
        return self.L.hhhost_time_set(self.h)

    # -- the laptop link (hh_serial.c) on this engine --

    def serial(self, tx_only: bool = False, ring_size: int = 0) -> None:
        """Attach the link, as the default build (two-way) or GPS-on-P1 build makes it."""
        assert self.L.hhhost_serial(self.h, 1 if tx_only else 0, ring_size)

    def serial_poll(self) -> bytes:
        """Everything the UART would send now."""
        out = bytearray()
        buf = (C.c_uint8 * 4096)()
        while True:
            n = self.L.hhhost_serial_poll(self.h, buf, 4096)
            if not n:
                return bytes(out)
            out += bytes(buf[:n])

    def serial_rx(self, data: bytes) -> None:
        """Bytes from the laptop (the card is serviced after them)."""
        self.L.hhhost_serial_rx(self.h, data, len(data))

    def serial_tick(self) -> None:
        self.L.hhhost_serial_tick(self.h)

    def serial_stat(self, what: str) -> int:
        return self.L.hhhost_serial_stat(self.h, what.encode())

    def close(self) -> None:
        if self.h:
            self.L.hhhost_free(self.h)
            self.h = None

    def drive(self, no: int) -> Path:
        return self.sd / "DRIVES" / f"D{no:06d}.HHD"

    def capture(self, no: int) -> Path:
        return self.sd / "CAPTURES" / f"C{no:06d}.HHD"
