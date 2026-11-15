"""Loads the firmware's portable core (bbcore) as a shared library, so tests
can run the real C against the Python mirror. Needs a C compiler; tests that
use it are skipped without one."""

from __future__ import annotations

import ctypes as C
import shutil
import subprocess
from pathlib import Path

import numpy as np

from hondalog.blackbox import format as F

HOST_DIR = Path(__file__).resolve().parent.parent / "firmware" / "blackbox" / "test" / "host"
_lib = None


class Status(C.Structure):
    _fields_ = [("t_us", C.c_uint64), ("now", C.c_uint8 * 28), ("event_count", C.c_uint32),
                ("samples", C.c_uint32), ("edges", C.c_uint32), ("overruns", C.c_uint32),
                ("io_errors", C.c_uint32), ("state", C.c_uint8), ("last_reason", C.c_uint8),
                ("capturing", C.c_uint8), ("sd_ok", C.c_uint8), ("captures_this_boot", C.c_uint32),
                ("ig1_off_ms", C.c_uint32), ("unix_us_at_t0", C.c_int64)]


def lib(build_dir: Path):
    """Build (once per session) and load libbbhost.so. None if no compiler."""
    global _lib
    if _lib is not None:
        return _lib
    if not (shutil.which("cc") and shutil.which("make")):
        return None
    out = Path(build_dir)
    subprocess.run(["make", "-s", "-C", str(HOST_DIR), f"OUT={out}"], check=True)
    L = C.CDLL(str(out / "libbbhost.so"))
    L.bbhost_new.restype = C.c_void_p
    L.bbhost_new.argtypes = [C.c_char_p, C.c_char_p, C.c_uint32, C.c_uint32]
    L.bbhost_free.argtypes = [C.c_void_p]
    L.bbhost_set_now.argtypes = [C.c_void_p, C.c_uint64]
    L.bbhost_set_write_budget.argtypes = [C.c_void_p, C.c_long]
    L.bbhost_syncs.argtypes = [C.c_void_p]
    L.bbhost_edge.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_uint64]
    L.bbhost_button.argtypes = [C.c_void_p, C.c_int]
    L.bbhost_adc.argtypes = [C.c_void_p, C.c_uint64, C.c_void_p, C.c_void_p, C.c_size_t]
    L.bbhost_storage.argtypes = [C.c_void_p, C.c_int]
    L.bbhost_rx.argtypes = [C.c_void_p, C.c_char_p, C.c_size_t]
    L.bbhost_link_poll.argtypes = [C.c_void_p]
    L.bbhost_tx_take.restype = C.c_size_t
    L.bbhost_tx_take.argtypes = [C.c_void_p, C.c_void_p, C.c_size_t]
    L.bbhost_status.argtypes = [C.c_void_p, C.POINTER(Status)]
    L.bbhost_gps.argtypes = [C.c_void_p, C.c_void_p]
    L.bbhost_pre_s.argtypes = [C.c_void_p]
    L.bbhost_trig_new.restype = C.c_void_p
    L.bbhost_trig_new.argtypes = [C.c_char_p]
    L.bbhost_trig_free.argtypes = [C.c_void_p]
    L.bbhost_trig_step.argtypes = [C.c_void_p, C.c_uint64, C.POINTER(C.c_int32),
                                   C.POINTER(C.c_int32), C.POINTER(C.c_uint8)]
    L.bbhost_config_dump.restype = C.c_size_t
    L.bbhost_config_dump.argtypes = [C.c_char_p, C.c_char_p, C.c_size_t, C.POINTER(C.c_int),
                                     C.POINTER(C.c_int)]
    L.bbhost_nmea.argtypes = [C.c_char_p, C.c_void_p]
    L.bbhost_sizes.restype = C.c_size_t
    L.bbhost_sizes.argtypes = [C.POINTER(C.c_uint32)]
    L.bb_crc32.restype = C.c_uint32
    L.bb_crc32.argtypes = [C.c_uint32, C.c_char_p, C.c_size_t]
    L.bb_crc16.restype = C.c_uint16
    L.bb_crc16.argtypes = [C.c_char_p, C.c_size_t]
    L.bb_cobs_encode.restype = C.c_size_t
    L.bb_cobs_encode.argtypes = [C.c_char_p, C.c_size_t, C.c_char_p]
    L.bb_frame_encode.restype = C.c_size_t
    L.bb_frame_encode.argtypes = [C.c_uint8, C.c_uint8, C.c_char_p, C.c_size_t, C.c_char_p]
    L.bb_extend32.restype = C.c_uint64
    L.bb_extend32.argtypes = [C.c_uint64, C.c_uint32]
    L.bb_led_pattern.argtypes = [C.c_int, C.c_uint32]
    _lib = L
    return L


class Box:
    """The C engine with a directory for an SD card."""

    CONV_HZ = 25000             # 5 channels x 5 kHz

    def __init__(self, L, root: Path, cfg_text: str = "", last_event_no: int = 0):
        self.L, self.root = L, Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # the simulator speaks car-side millivolts, so undo nothing
        text = "".join(f"{n}_scale = 1000\n" for n in F.ANALOG) + cfg_text
        self.h = L.bbhost_new(str(self.root).encode(), text.encode(), self.CONV_HZ, last_event_no)
        assert self.h, "config rejected"
        self._deframer = F.Deframer()
        self._ch, self._mv = [], []

    def close(self):
        if self.h:
            self.L.bbhost_free(self.h)
            self.h = None

    def feed(self, tick, boot_us: int = 0, chunk_ms: int = 10, storage: bool = True):
        """One simulator tick: edges now, ADC readings in chunks like the DMA driver."""
        for te, ch, level in tick.edges:
            self.L.bbhost_edge(self.h, ch, 0 if level else 1, boot_us + te)     # opto inverts
        self.L.bbhost_button(self.h, tick.button)
        for row in tick.sub:
            self._ch += [0, 1, 2, 3, 4]
            self._mv += row
        if tick.n % chunk_ms == 0:
            self.flush(boot_us + tick.n * 1000, storage)

    def flush(self, t_us: int, storage: bool = True):
        if self._ch:
            ch = np.array(self._ch, dtype=np.uint8)
            mv = np.array(self._mv, dtype=np.uint16)
            self.L.bbhost_adc(self.h, t_us, ch.ctypes.data, mv.ctypes.data, len(ch))
            self._ch, self._mv = [], []
        self.L.bbhost_set_now(self.h, t_us)
        if storage:
            self.L.bbhost_storage(self.h, 10000)

    def status(self) -> Status:
        st = Status()
        self.L.bbhost_status(self.h, C.byref(st))
        return st

    def send(self, ftype: int, payload: bytes = b""):
        data = F.frame_encode(ftype, payload)
        self.L.bbhost_rx(self.h, data, len(data))

    def frames(self):
        """Poll the link and return what the box sent: [(type, payload)]."""
        self.L.bbhost_link_poll(self.h)
        buf = C.create_string_buffer(1 << 20)
        n = self.L.bbhost_tx_take(self.h, buf, len(buf))
        return [(t, p) for t, _s, p in self._deframer.feed(buf.raw[:n])]

    def event_file(self, no: int, ext: str = "BBX") -> Path:
        return self.root / "EVENTS" / f"EV{no:06d}.{ext}"
