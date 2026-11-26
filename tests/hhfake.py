"""A fake handheld on the USB serial link, as a port object (open/close/
set_baud/read/write): it sends HELLO every ``hello_s`` and a GPS record every
``gps_s`` (fw 0.1.0 by default: two-way, no relay capability), answers
TIME_REQ with TIME_RSP, and keeps every frame the laptop wrote, decoded."""

from __future__ import annotations

import struct
import threading
import time

from hondalog.handheld import format as F
from hondalog.handheld import serial_proto as P

PEARL = (32.2746, -90.1320)


class FakeHandheld:
    def __init__(self, name="COM22", caps=P.CAP_RX | P.CAP_FILES | P.CAP_PUT | P.CAP_LOG | P.CAP_MAPUP | P.CAP_HL,
                 fw="0.1.0", map_hash="44979f9238b1", fix=True, sats=9, hello_s=0.5, gps_s=0.1,
                 write_delay=0.0):
        self.name = name
        self.baud = 921600
        self.fixed_baud = True
        self.caps = caps
        self.fw = fw
        self.map_hash = map_hash
        self.fix = fix
        self.sats = sats
        self.hello_s = hello_s
        self.gps_s = gps_s
        self.write_delay = write_delay      # seconds each write blocks (a stalled USB link)
        self.opened = 0
        self.is_open = False
        self.frames: list[tuple[int, int, bytes]] = []      # (type, seq, payload) the laptop sent
        self.bad = 0
        self._deframer = P.Deframer()
        self._out = bytearray()
        self._seq = 0
        self._t0 = time.monotonic()
        self._next_hello = 0.0
        self._next_gps = 0.0
        self._lock = threading.Lock()
        self.gps_sent = 0

    # -- what the laptop sees --

    def hello(self) -> P.Hello:
        t_us = int((time.monotonic() - self._t0) * 1e6)
        return P.Hello(P.REV, P.VARIANT_DEFAULT, self.caps, self.fw, "HH-1373A0", self.map_hash, b"\0" * 8, b"",
                       P.LINK_ERROR, 0, 0, 40, 1 | 2 | 4 | 8 | 16, 7, self.baud, t_us, 0, 2000, 512)

    def _frame(self, ftype: int, payload: bytes) -> None:
        self._out += b"\0" + P.frame_encode(ftype, payload, self._seq)
        self._seq = (self._seq + 1) & 0xFF

    def _gps(self) -> bytes:
        t = time.monotonic() - self._t0
        lat, lon = PEARL[0] + t * 1e-5, PEARL[1]
        return F.REC_GPS.pack(int(t * 1e6), int(lat * 1e7), int(lon * 1e7), int(1.7e9 + t), 1000, 9000,
                              1 if self.fix else 0, self.sats, 90)

    def open(self) -> None:
        self.opened += 1
        self.is_open = True
        self._next_hello = self._next_gps = 0.0

    def close(self) -> None:
        self.is_open = False

    def set_baud(self, baud: int) -> None:
        self.baud = baud

    def read(self, timeout: float) -> bytes:
        end = time.monotonic() + timeout
        while True:
            now = time.monotonic()
            with self._lock:
                if now >= self._next_hello:
                    self._next_hello = now + self.hello_s
                    self._frame(P.SF_HELLO, self.hello().pack())
                if self.gps_s and now >= self._next_gps:
                    self._next_gps = now + self.gps_s
                    self._frame(P.SF_GPS, self._gps())
                    self.gps_sent += 1
                if self._out:
                    data, self._out = bytes(self._out), bytearray()
                    return data
            if now >= end:
                return b""
            time.sleep(min(0.005, max(0.0, end - now)))

    def write(self, data: bytes) -> None:
        if self.write_delay:
            time.sleep(self.write_delay)
        with self._lock:
            before = self._deframer.bad
            for ftype, seq, payload in self._deframer.feed(data):
                self.frames.append((ftype, seq, payload))
                if ftype == P.SC_TIME_REQ and len(payload) >= 8:
                    host_tx = struct.unpack("<q", payload[:8])[0]
                    dev = int((time.monotonic() - self._t0) * 1e6)
                    self._frame(P.SF_TIME_RSP, P.TIME_RSP.pack(host_tx, dev, dev + 20))
            self.bad += self._deframer.bad - before

    def sent(self, ftype: int) -> list[bytes]:
        with self._lock:
            return [p for t, _, p in self.frames if t == ftype]
