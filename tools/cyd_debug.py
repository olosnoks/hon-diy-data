#!/usr/bin/env python3
"""Watch a CYD handheld over its USB-C serial link: no hondalog install needed.

Needs only Python 3 and pyserial, so it runs on Windows (PlatformIO's Python
already has pyserial), macOS or Linux:

    python cyd_debug.py COM17                  # everything, readable
    python cyd_debug.py /dev/ttyUSB0 --gps     # GPS fixes and GPS diagnostics only
    python cyd_debug.py COM17 --seconds 30     # stop after 30 s (default: until Ctrl+C)
    python cyd_debug.py COM17 --reset          # reset the board first, to see its boot messages
    python cyd_debug.py COM17 --raw            # also print frame types/sizes as they arrive

It decodes the frames in firmware/handheld/SERIAL.md: hello, stats, GPS,
link changes, events and the handheld's own log lines (including the
"gps diag" line every 2 s). Opening the port does not reset the board unless
--reset is given. Pass @460800 after the port if the handheld runs slower.
"""
from __future__ import annotations

import argparse
import struct
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is missing: pip install pyserial (PlatformIO's Python already has it)")

T_HELLO, T_READ, T_TICK, T_LINK, T_GPS, T_EVENT, T_STATS, T_LOG = 0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47
NAMES = {0x40: "hello", 0x41: "read", 0x42: "tick", 0x43: "link", 0x44: "gps", 0x45: "event", 0x46: "stats",
         0x47: "log", 0x48: "iso", 0x49: "time", 0x4A: "ack", 0x4B: "file-info", 0x4C: "file-data"}
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", help="COM17, /dev/ttyUSB0, /dev/hondalog-cyd ... optionally @baud")
    ap.add_argument("--gps", action="store_true", help="GPS fixes, GPS counters and GPS log lines only")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long (0 = until Ctrl+C)")
    ap.add_argument("--reset", action="store_true", help="reset the board first (shows its boot messages)")
    ap.add_argument("--raw", action="store_true", help="also show frame types as they arrive")
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
