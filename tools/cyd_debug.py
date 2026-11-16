#!/usr/bin/env python3
"""Watch a CYD handheld over its USB-C serial link: no hondalog install needed.

Needs only Python 3 and pyserial, so it runs on Windows (PlatformIO's Python
already has pyserial), macOS or Linux:

    python cyd_debug.py COM17                  # everything, readable
    python cyd_debug.py /dev/ttyUSB0 --gps     # GPS fixes and GPS diagnostics only
    python cyd_debug.py COM17 --seconds 30     # stop after 30 s (default: until Ctrl+C)
    python cyd_debug.py COM17 --reset          # reset the board first, to see its boot messages
    python cyd_debug.py COM17 --raw            # also print frame types/sizes as they arrive
    python cyd_debug.py COM17 --put-map path\\to\\MAP   # copy a MAP folder into the handheld's SD card (below)

--put-map DIR sends MAP.IDX, MAP.DAT and README.TXT from DIR (the folder
`hondalog-handheld map-build` wrote, or the card folder that holds it) into
the card's MAP/ folder over the cable, then exits. Each file goes to a
temporary file on the card and replaces the old one only after the handheld
has read it back and checked its length and CRC32. If the cable is pulled or
the program stopped, run the same command again: it carries on from the last
byte the handheld holds. Progress is printed every 5 %. Two-way firmware
only (not the --gps-p1 build). About 40-55 kB/s at 921600 baud (estimated):
17 MB takes 5-7 minutes.

It decodes the frames in firmware/handheld/SERIAL.md: hello, stats, GPS,
link changes, events and the handheld's own log lines (including the
"gps diag" line every 2 s). Opening the port does not reset the board unless
--reset is given. Pass @460800 after the port if the handheld runs slower.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import time
import zlib

try:
    import serial
except ImportError:
    sys.exit("pyserial is missing: pip install pyserial (PlatformIO's Python already has it)")

T_HELLO, T_READ, T_TICK, T_LINK, T_GPS, T_EVENT, T_STATS, T_LOG = 0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47
NAMES = {0x40: "hello", 0x41: "read", 0x42: "tick", 0x43: "link", 0x44: "gps", 0x45: "event", 0x46: "stats",
         0x47: "log", 0x48: "iso", 0x49: "time", 0x4A: "ack", 0x4B: "file-info", 0x4C: "file-data", 0x4D: "mapup"}
HELLO = struct.Struct("<4sBBH12s12s12s8s10sBBBBBBIIQqHH")
STATS = struct.Struct("<Qq6IHBB7I6I")
STATS_FIELDS = ("t_us", "unix_us", "reads", "errors", "timeouts", "checksum_failures", "retries", "reconnects",
                "hz_c", "link_state", "flags", "drive_no", "log_bytes", "free_mb", "markers", "stall_count",
                "nmea_good", "nmea_bad", "tx_frames", "tx_dropped", "tx_bytes", "rx_frames", "rx_bad", "ring_peak")
GPS = struct.Struct("<QiiIHHBBH")
LINK = struct.Struct("<QBBBB")
EVENT = struct.Struct("<QBBHi")
LINK_STATES = {0: "no reply from the ECU", 1: "connecting", 2: "ok", 3: "OBD2 (ISO) only", 4: "cable/port gone"}
EVENTS = {1: "marker", 2: "link up", 3: "link down", 4: "alarm raised", 5: "alarm cleared", 6: "alarm acked",
          7: "STALL", 8: "port lost", 9: "port back", 10: "ISO session", 11: "DTC", 12: "log dropped",
          13: "drive start", 14: "no DTCs"}


def crc16(data: bytes) -> int:
    c = 0xFFFF
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def cobs_decode(b: bytes) -> bytes | None:
    out = bytearray()
    i = 0
    while i < len(b):
        n = b[i]
        if n == 0 or i + n > len(b) + 1:
            return None
        out += b[i + 1:i + n]
        i += n
        if n < 0xFF and i < len(b):
            out.append(0)
    return bytes(out)


def cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("ascii", "replace")


def stamp(t_us: int) -> str:
    return f"{t_us / 1e6:9.3f}s"


def show(ftype: int, p: bytes, args, state: dict) -> None:
    gps_only = args.gps
    if ftype == T_HELLO and len(p) >= HELLO.size and p[:4] == b"HHSL":
        f = HELLO.unpack(p[:HELLO.size])
        key = (cstr(f[5]), f[15])
        if state.get("hello") != key:            # print it once per boot, not every 2 s
            state["hello"] = key
            caps = "two-way" if f[3] & 1 else "tx-only"
            print(f"[hello] {cstr(f[5])} fw {cstr(f[4])} variant {'gps-p1' if f[2] else 'default'} {caps}, "
                  f"map #{cstr(f[6])}{' (built in)' if f[14] & 1 else ''}, boot {f[15]}, {f[16]} baud, "
                  f"ECU link: {LINK_STATES.get(f[10], f[10])}")
    elif ftype == T_STATS and len(p) >= STATS.size:
        s = dict(zip(STATS_FIELDS, STATS.unpack(p[:STATS.size])))
        if gps_only:
            print(f"[stats] {stamp(s['t_us'])} GPS sentences ok {s['nmea_good']} bad {s['nmea_bad']}"
                  f"  fix {'yes' if s['flags'] & 4 else 'no'}")
        else:
            print(f"[stats] {stamp(s['t_us'])} ECU {LINK_STATES.get(s['link_state'], s['link_state'])}, "
                  f"reads {s['reads']} errors {s['errors']} {s['hz_c'] / 100:.1f} Hz | "
                  f"GPS ok {s['nmea_good']} bad {s['nmea_bad']} fix {'yes' if s['flags'] & 4 else 'no'} | "
                  f"SD {'ok' if s['flags'] & 1 else 'NONE'} {s['free_mb']} MB free, drive {s['drive_no']}"
                  f"{' REC' if s['flags'] & 2 else ''} | link sent {s['tx_frames']} lost {s['tx_dropped']}")
    elif ftype == T_GPS and len(p) >= GPS.size:
        t, lat, lon, utc, speed, course, fix, sats, hdop = GPS.unpack(p[:GPS.size])
        if fix:
            print(f"[gps]   {stamp(t)} FIX {lat / 1e7:.6f}, {lon / 1e7:.6f}  {speed * 0.036 * 0.621371:.1f} mph "
                  f"heading {course / 100:.0f}  sats {sats} hdop {hdop / 100:.1f}")
        else:
            print(f"[gps]   {stamp(t)} no fix yet, sats {sats}")
    elif ftype == T_LOG:
        text = p.decode("utf-8", "replace").rstrip()
        if not gps_only or "gps" in text.lower():
            print(f"[log]   {text}")
    elif gps_only:
        return
    elif ftype == T_LINK and len(p) >= LINK.size:
        t, st, backend, mode, _ = LINK.unpack(p[:LINK.size])
        print(f"[link]  {stamp(t)} ECU {LINK_STATES.get(st, st)} via {'USB cable' if backend == 0 else 'UART'}")
    elif ftype == T_EVENT and len(p) >= EVENT.size:
        t, code, a, b, v = EVENT.unpack(p[:EVENT.size])
        print(f"[event] {stamp(t)} {EVENTS.get(code, code)} ({a}, {b}, {v})")
    elif ftype in (T_READ, T_TICK):
        state[ftype] = state.get(ftype, 0) + 1
    elif args.raw:
        print(f"[{NAMES.get(ftype, hex(ftype))}] {len(p)} bytes")


# ---- --put-map: files into the card's MAP/ folder (firmware/handheld/SERIAL.md, "Map upload") ----

SC_MAPUP_OPEN, SC_MAPUP_WRITE, SC_MAPUP_STATUS, SC_MAPUP_COMMIT = 0x6C, 0x6D, 0x6E, 0x6F
T_ACK, T_MAPUP = 0x4A, 0x4D
CAP_RX, CAP_MAPUP = 0x0001, 0x0010
OK, E_CRC, E_STATE, E_REFUSED, E_BUSY, E_UNSUPPORTED, E_OFFSET = 0, -3, -4, -5, -6, -7, -8
STATUS_TEXT = {0: "ok", -1: "bad argument", -2: "card error", -3: "checksum mismatch", -4: "not expected now",
               -5: "refused", -6: "busy", -7: "not supported", -8: "wrong offset"}
ACK = struct.Struct("<BBhI")
MAPUP_OPEN = struct.Struct("<BBxxII")
MAPUP_WRITE = struct.Struct("<II")
MAPUP_INFO = struct.Struct("<BxxxIII16s")
CHUNK = 504                      # 512 (the most a frame carries) less the 8-byte header
FINAL = 1
IDLE_S = 10.0


class PutError(Exception):
    pass


def cobs_encode(data: bytes) -> bytes:
    out = bytearray([0])
    code_at, code = 0, 1
    for b in data:
        if b == 0:
            out[code_at] = code
            code_at, code = len(out), 1
            out.append(0)
        else:
            out.append(b)
            code += 1
            if code == 0xFF:
                out[code_at] = code
                code_at, code = len(out), 1
                out.append(0)
    out[code_at] = code
    return bytes(out)


def make_frame(ftype: int, seq: int, payload: bytes) -> bytes:
    raw = bytes([1, ftype, seq]) + payload
    raw += crc16(raw).to_bytes(2, "little")
    return cobs_encode(raw) + b"\0"


class Link:
    """Commands with an ACK, over a pyserial port; the live stream that keeps arriving is skipped."""

    def __init__(self, ser, timeout: float = 1.5, retries: int = 4):
        self.ser, self.timeout, self.retries = ser, timeout, retries
        self.buf = bytearray()
        self.seq = 0
        self.hello = None
        self.info = None                      # the newest MAPUP frame

    def frames(self, wait: float):
        end = time.time() + wait
        while True:
            first = self.ser.read(1)                  # waits for a byte; read(4096) would wait out the whole timeout
            if first:
                self.buf += first + self.ser.read(self.ser.in_waiting)
            while True:
                i = self.buf.find(0)
                if i < 0:
                    break
                chunk, self.buf = bytes(self.buf[:i]), self.buf[i + 1:]
                f = cobs_decode(chunk) if chunk else None
                if f is None or len(f) < 5 or crc16(f[:-2]) != int.from_bytes(f[-2:], "little"):
                    continue
                yield f[1], f[2], f[3:-2]
            if time.time() >= end:
                return

    def wait_hello(self, seconds: float = 12.0) -> tuple:
        end = time.time() + seconds
        while time.time() < end:
            for ftype, _seq, p in self.frames(0.2):
                if ftype == T_HELLO and len(p) >= HELLO.size and p[:4] == b"HHSL":
                    self.hello = HELLO.unpack(p[:HELLO.size])
                    return self.hello
        raise PutError("no hello from the handheld: wrong port or baud, or its serial link is off "
                       "(HANDHELD.CFG serial = 0). Pass @460800 after the port if it runs slower.")

    def command(self, ftype: int, payload: bytes = b"", timeout: float | None = None,
                retries: int | None = None) -> tuple[int, int]:
        for _ in range(1 + (self.retries if retries is None else retries)):
            seq, self.seq = self.seq, (self.seq + 1) & 0xFF
            self.ser.write(make_frame(ftype, seq, payload))
            self.ser.flush()
            end = time.time() + (self.timeout if timeout is None else timeout)
            while time.time() < end:
                for rtype, _rseq, p in self.frames(0.05):
                    if rtype == T_MAPUP and len(p) >= MAPUP_INFO.size:
                        self.info = MAPUP_INFO.unpack(p[:MAPUP_INFO.size])
                    elif rtype == T_ACK and len(p) >= ACK.size:
                        cmd, cseq, status, value = ACK.unpack(p[:ACK.size])
                        if cmd == ftype and cseq == seq:
                            return status, value
        raise PutError("the handheld did not answer (cable pulled? the handheld restarted?). Run the same "
                       "command again: it carries on where it stopped.")


def say_status(status: int) -> str:
    return STATUS_TEXT.get(status, f"error {status}")


def put_file(link: Link, name: str, data: bytes, final: bool, progress) -> None:
    """One file into MAP/, resumed where the handheld holds it, committed only when whole and verified."""
    full = "MAP/" + name
    wire = full.encode("ascii")
    crc = zlib.crc32(data) & 0xFFFFFFFF
    for attempt in range(6):
        status, offset = link.command(SC_MAPUP_OPEN, MAPUP_OPEN.pack(0, len(wire), len(data), crc) + wire)
        if status == E_BUSY and attempt < 5:      # the map screen is still drawing
            time.sleep(0.5)
            continue
        break
    if status != OK:
        raise PutError(f"{full}: {say_status(status)}")
    progress(name, offset, len(data))
    bad_run = 0
    while offset < len(data):
        chunk = data[offset:offset + CHUNK]
        status, value = link.command(SC_MAPUP_WRITE, MAPUP_WRITE.pack(offset, zlib.crc32(chunk) & 0xFFFFFFFF) + chunk)
        if status == E_CRC:                       # damaged on the way: again
            bad_run += 1
            if bad_run > 8:
                raise PutError(f"{full}: chunks keep arriving damaged at byte {offset} (try the port@460800)")
            continue
        if status == E_STATE:
            raise PutError(f"{full}: the handheld dropped the upload at byte {offset}; run the command again")
        if status not in (OK, E_OFFSET):
            raise PutError(f"{full}: {say_status(status)} at byte {offset}")
        bad_run, offset = 0, value                # E_OFFSET: it holds more (or less) than we thought
        progress(name, offset, len(data))
    # once only: a second commit after one that went through would say "not expected now"
    status, _ = link.command(SC_MAPUP_COMMIT, bytes([FINAL if final else 0, 0, 0, 0]), timeout=120.0, retries=0)
    if status == E_CRC:
        raise PutError(f"{full}: what is on the card does not match its checksum (a bad SD card?); "
                       "it was not used and the old file is as it was")
    if status != OK:
        raise PutError(f"{full}: {say_status(status)} at the commit")


def put_map(ser, folder: str) -> int:
    folder = os.path.abspath(folder)
    if os.path.isfile(os.path.join(folder, "MAP", "MAP.IDX")):
        folder = os.path.join(folder, "MAP")      # the card folder that holds MAP/
    files = {}
    for name in ("README.TXT", "MAP.DAT", "MAP.IDX"):        # the index last: the pair is half-replaced briefly
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                files[name] = fh.read()
        elif name != "README.TXT":
            raise PutError(f"{folder}: no {name} (run hondalog-handheld map-build first)")
        else:
            print("  note: no README.TXT in the folder (it carries the OpenStreetMap attribution)")
    idx, dat = files["MAP.IDX"], files["MAP.DAT"]
    if idx[:8] != b"HHMAPIDX" or dat[:8] != b"HHMAPDAT":
        raise PutError(f"{folder}: MAP.IDX / MAP.DAT are not a hondalog map")
    if len(idx) < 100 or len(dat) < 16 or struct.unpack_from("<I", idx, 96)[0] != struct.unpack_from("<I", dat, 12)[0]:
        raise PutError(f"{folder}: MAP.IDX and MAP.DAT are from different builds")
    total = sum(len(d) for d in files.values())

    link = Link(ser)
    h = link.wait_hello()
    caps, flags = h[3], h[14]
    print(f"handheld {cstr(h[5])} fw {cstr(h[4])}, {'two-way' if caps & CAP_RX else 'tx-only'}")
    if not caps & CAP_RX:
        raise PutError("this handheld is the tx-only build (the GPS has the port's receive pin): it cannot take "
                       "files over the cable. Copy the MAP folder to the SD card instead.")
    if not flags & 8:
        raise PutError("the handheld has no SD card")
    if not caps & CAP_MAPUP:
        raise PutError("this handheld's firmware cannot take map files over the cable (it is older than the MAP "
                       "upload): flash the current firmware, or copy the MAP folder to the SD card")

    # an earlier run that was cut off: the file it was in the middle of goes on from its last byte
    names = list(files)
    skip = 0
    link.info = None
    status, _ = link.command(SC_MAPUP_STATUS)
    if status == OK and link.info and link.info[0]:
        _state, got, size, crc, raw = link.info
        for i, name in enumerate(names):
            if (cstr(raw), size, crc) == ("MAP/" + name, len(files[name]), zlib.crc32(files[name]) & 0xFFFFFFFF):
                skip = i
                print(f"  carrying on with {name} from byte {got}")
    done = sum(len(files[n]) for n in names[:skip])
    print(f"sending {', '.join(names[skip:])} ({(total - done) / 1e6:.2f} MB) into the card's MAP/ ...")
    t0 = time.time()
    state = {"next": 0, "base": done, "start": done}

    def progress(name: str, got: int, size: int) -> None:
        all_done = state["base"] + got
        pct = 100 * all_done // total
        if pct >= state["next"]:
            state["next"] = pct // 5 * 5 + 5
            rate = (all_done - state["start"]) / max(0.001, time.time() - t0) / 1000
            left = (total - all_done) / max(1.0, rate * 1000)
            print(f"  {pct:3d}%  {name} {got}/{size}  {rate:.1f} kB/s  about {left:.0f} s left", flush=True)

    for i, name in enumerate(names):
        if i < skip:
            continue
        state["base"] = sum(len(files[n]) for n in names[:i])
        put_file(link, name, files[name], i == len(names) - 1, progress)
        print(f"  {name}: sent, checked on the card and in place", flush=True)
    secs = time.time() - t0
    print(f"done in {secs:.0f} s ({(total - done) / 1000 / max(secs, 0.001):.1f} kB/s): MAP/ on the handheld is "
          "the new map; the Map screen uses it at once")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", help="COM17, /dev/ttyUSB0, /dev/hondalog-cyd ... optionally @baud")
    ap.add_argument("--gps", action="store_true", help="GPS fixes, GPS counters and GPS log lines only")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long (0 = until Ctrl+C)")
    ap.add_argument("--reset", action="store_true", help="reset the board first (shows its boot messages)")
    ap.add_argument("--raw", action="store_true", help="also show frame types as they arrive")
    ap.add_argument("--put-map", metavar="DIR", help="copy this MAP folder (MAP.IDX, MAP.DAT, README.TXT) into the "
                    "card's MAP/ over the cable, with resume and verification; then exit")
    args = ap.parse_args()

    port, _, baud = args.port.partition("@")
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, int(baud or 921600), 0.2
    s.rts = False                     # RTS before DTR: no pulse on the board's reset line
    s.dtr = False
    s.open()
    if args.reset:
        s.rts = True
        time.sleep(0.1)
        s.rts = False
    if args.put_map:
        try:
            return put_map(s, args.put_map)
        except PutError as exc:
            print(f"put-map: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("\nstopped; run the same command again to carry on", file=sys.stderr)
            return 130
        finally:
            s.close()
    print(f"listening on {port} at {s.baudrate} baud{' (board reset)' if args.reset else ''}; Ctrl+C to stop")
    state: dict = {}
    buf = bytearray()
    good = bad = 0
    t_end = time.time() + args.seconds if args.seconds else None
    last_summary = time.time()
    try:
        while t_end is None or time.time() < t_end:
            buf += s.read(4096)
            while True:
                i = buf.find(0)
                if i < 0:
                    break
                chunk, buf = bytes(buf[:i]), buf[i + 1:]
                if not chunk:
                    continue
                f = cobs_decode(chunk)
                if f is None or len(f) < 5 or crc16(f[:-2]) != int.from_bytes(f[-2:], "little"):
                    bad += 1
                    continue
                good += 1
                show(f[1], f[3:-2], args, state)
            if not args.gps and time.time() - last_summary >= 10:
                last_summary = time.time()
                print(f"[link]  frames ok {good} damaged {bad}; ECU reads seen {state.get(T_READ, 0)}")
    except KeyboardInterrupt:
        pass
    finally:
        s.close()
    print(f"done: {good} frames ok, {bad} damaged")
    if good == 0:
        print("no frames: wrong port or baud, or the handheld's serial link is off (HANDHELD.CFG serial = 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
