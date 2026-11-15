"""Black box constants and binary layouts, shared by the file parser, the USB
protocol and the simulator.

This mirrors ``firmware/blackbox/components/bbcore/include/bbcore.h`` byte for
byte (little-endian, packed). ``tests/test_bb_core.py`` checks the sizes
against the compiled C. The written-out specs are FORMAT.md and PROTOCOL.md
next to the firmware.
"""

from __future__ import annotations

import binascii
import struct
import zlib

import numpy as np

FILE_VERSION = 1
PROTO_VERSION = 1
SAMPLE_HZ = 1000
TICK_US = 1000

# ---- channels ---------------------------------------------------------------

ANALOG = ("vbat", "ecub", "pump", "ig1", "alt")
RAILS = ANALOG[:4]                  # these also carry min/max per millisecond
EDGE = ("tach", "ign", "inj")
A_VBAT, A_ECUB, A_PUMP, A_IG1, A_ALT = range(5)
E_TACH, E_IGN, E_INJ = range(3)

DIG_TACH, DIG_IGN, DIG_INJ, DIG_BUTTON = 0x01, 0x02, 0x04, 0x08
DIG_IG1_ON, DIG_RUNNING, DIG_CAPTURING, DIG_GAP = 0x10, 0x20, 0x40, 0x80

# ---- reason codes -----------------------------------------------------------

R_TACH_STOP, R_IGN_STOP, R_IG1_DROP, R_ECUB_DROP, R_PUMP_DROP = 1, 2, 3, 4, 5
R_VBAT_DIP, R_BUTTON, R_HOST, R_BROWNOUT, R_INJ_STOP, R_ECUB_GLITCH = 6, 7, 8, 9, 10, 11
R_MAX_TRIGGER = 31
EV_HOST_MARK, EV_TIME_SYNC, EV_RUNNING, EV_IG1_ON, EV_IG1_OFF, EV_OVERRUN = 32, 33, 34, 35, 36, 37

REASONS = {
    0: ("none", "no trigger"),
    R_TACH_STOP: ("tach_stop", "tach pulses stopped with IG1 on"),
    R_IGN_STOP: ("ign_stop", "ignition trigger pulses stopped with IG1 on"),
    R_IG1_DROP: ("ig1_drop", "IG1 dropped while the engine was running"),
    R_ECUB_DROP: ("ecub_drop", "ECU +B dropped with IG1 on (main relay signature)"),
    R_PUMP_DROP: ("pump_drop", "fuel pump feed dropped while running"),
    R_VBAT_DIP: ("vbat_dip", "battery voltage dipped while running"),
    R_BUTTON: ("button", "marker button"),
    R_HOST: ("host", "trigger from the laptop"),
    R_BROWNOUT: ("brownout", "logger supply collapsing"),
    R_INJ_STOP: ("inj_stop", "injector pulses stopped while the tach kept going"),
    R_ECUB_GLITCH: ("ecub_glitch", "ECU +B fell faster than the set slope"),
    EV_HOST_MARK: ("host_mark", "marker from the laptop"),
    EV_TIME_SYNC: ("time_sync", "clock set"),
    EV_RUNNING: ("running", "engine running"),
    EV_IG1_ON: ("ig1_on", "IG1 on"),
    EV_IG1_OFF: ("ig1_off", "IG1 off"),
    EV_OVERRUN: ("overrun", "ring buffer overrun, data lost"),
}


def reason_name(code: int) -> str:
    return REASONS[code][0] if code in REASONS else f"rule{code}"


def reason_text(code: int) -> str:
    return REASONS[code][1] if code in REASONS else f"user rule {code}"


# ---- records ----------------------------------------------------------------

SAMPLE_DTYPE = np.dtype([
    ("mv", "<u2", (5,)), ("min_dv", "u1", (4,)), ("max_dv", "u1", (4,)),
    ("rpm_tach", "<u2"), ("rpm_ign", "<u2"), ("inj_pw_us", "<u2"),
    ("inj_duty", "u1"), ("dig", "u1"), ("rsv", "<u2")])
EVENT_DTYPE = np.dtype([("t_us", "<u8"), ("code", "u1"), ("src", "u1"), ("rsv", "<u2"),
                        ("value", "<i4")])
GPS_DTYPE = np.dtype([("t_us", "<u8"), ("lat_e7", "<i4"), ("lon_e7", "<i4"), ("utc", "<u4"),
                      ("speed_cms", "<u2"), ("course_cdeg", "<u2"), ("fix", "u1"),
                      ("sats", "u1"), ("rsv", "<u2")])
INDEX_DTYPE = np.dtype([("offset", "<u4"), ("type", "u1"), ("flags", "u1"), ("count", "<u2"),
                        ("t0_us", "<u8")])
assert SAMPLE_DTYPE.itemsize == 28 and EVENT_DTYPE.itemsize == 16
assert GPS_DTYPE.itemsize == 28 and INDEX_DTYPE.itemsize == 16

EDGE_T_MASK = (1 << 56) - 1


def edge_pack(t_us: int, ch: int, level: int) -> int:
    return (t_us & EDGE_T_MASK) | (ch << 56) | ((1 if level else 0) << 60)


# ---- event file -------------------------------------------------------------

FILE_MAGIC = 0x58424248         # b"HBBX"
BLOCK_MAGIC = 0x4B4C4242        # b"BBLK"
B_SAMPLES, B_EDGES, B_EVENTS, B_CONFIG, B_GPS, B_INDEX, B_END = 1, 2, 3, 4, 5, 0x7E, 0x7F
HF_TIME_VALID, HF_TIME_GPS = 1, 2
END_COMPLETE = 1

FILE_HDR = struct.Struct("<IHHIIBBBBIIIQqi12s12sBBHI")
BLOCK_HDR = struct.Struct("<IBBHIIQ")
END_REC = struct.Struct("<IIII")
assert FILE_HDR.size == 84 and BLOCK_HDR.size == 24

BLOCK_SAMPLES = 256
BLOCK_EDGES = 512

# ---- USB frames -------------------------------------------------------------

F_HELLO, F_LIVE, F_EVENT, F_TIME_RSP, F_TEXT, F_FILE_INFO, F_FILE_DATA = 1, 2, 3, 5, 0x0A, 0x0B, 0x0C
(F_GET_HELLO, F_TIME_REQ, F_TIME_SET, F_MARK, F_TRIGGER, F_SELFTEST, F_GET_CONFIG, F_SET_CONFIG,
 F_SAVE_CONFIG, F_LIST_FILES, F_READ_FILE, F_LIVE_RATE) = range(0x10, 0x1C)

HELLO = struct.Struct("<BB12s12sHHIIBB")
LIVE = struct.Struct("<Q28s5HIBBH")
EVENT_MSG = struct.Struct("<QBBHiIB")
TIME_RSP = struct.Struct("<qQQ")
FILE_INFO = struct.Struct("<IIBB")
READ_FILE = struct.Struct("<IIH")
FILE_CHUNK = 480
MAX_PAYLOAD = 512
assert LIVE.size == 54 and HELLO.size == 40

ST_SD_OK, ST_CAPTURING, ST_TIME_VALID, ST_RUNNING = 0x01, 0x02, 0x04, 0x08
ST_IG1_ON, ST_GPS_FIX, ST_SELFTEST_FAIL = 0x10, 0x20, 0x40


def crc32(data: bytes, crc: int = 0) -> int:
    return zlib.crc32(data, crc) & 0xFFFFFFFF


def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


def cobs_encode(data: bytes) -> bytes:
    out = bytearray()
    block = bytearray()
    for b in data:
        if b == 0:
            out.append(len(block) + 1)
            out += block
            block.clear()
        else:
            block.append(b)
            if len(block) == 254:
                out.append(0xFF)
                out += block
                block.clear()
    out.append(len(block) + 1)
    out += block
    return bytes(out)


def cobs_decode(data: bytes) -> bytes | None:
    """None if malformed."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        code = data[i]
        if code == 0 or i + code > n:
            return None
        out += data[i + 1:i + code]
        i += code
        if code != 0xFF and i < n:
            out.append(0)
    return bytes(out)


def frame_encode(ftype: int, payload: bytes = b"", seq: int = 0) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too long")
    raw = bytes((PROTO_VERSION, ftype, seq & 0xFF)) + payload
    raw += struct.pack("<H", crc16(raw))
    return cobs_encode(raw) + b"\x00"


class Deframer:
    """Feed bytes, get (type, seq, payload) tuples. Bad frames are counted."""

    def __init__(self):
        self._buf = bytearray()
        self.bad = 0

    def feed(self, data: bytes) -> list[tuple[int, int, bytes]]:
        frames = []
        self._buf += data
        while True:
            end = self._buf.find(0)
            if end < 0:
                if len(self._buf) > 2 * MAX_PAYLOAD:
                    self._buf.clear()
                    self.bad += 1
                return frames
            chunk = bytes(self._buf[:end])
            del self._buf[:end + 1]
            if not chunk:
                continue
            raw = cobs_decode(chunk)
            if (raw is None or len(raw) < 5 or raw[0] != PROTO_VERSION
                    or struct.unpack("<H", raw[-2:])[0] != crc16(raw[:-2])):
                self.bad += 1
                continue
            frames.append((raw[1], raw[2], raw[3:-2]))
