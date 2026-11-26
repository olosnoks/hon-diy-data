"""Simulated Honda ECU.

``EcuModel`` is the engine + RAM + protocol logic (no I/O, used by the tests
and by the built-in "sim:" port). ``main`` serves it on a pseudo-terminal so
the app can talk to it exactly as it talks to the KKL cable, echo included.

What it models:

* Honda RAM reads (``20 05 addr len cksum``) as the stock P2P answers them
  (confirmed on a 2000 Civic EX, 2026-11-25): at 10400 baud only, and only
  after the wake frame ``68 6A F5 AF BF B3 B2 C1 DB B3 E9``. The ECU stays
  awake until it loses power. ``EcuModel(framing=protocol.DEFAULT)`` is
  instead kerpz's P2T: Honda reads at 9600 baud, no wake frame needed.
* ISO 9141-2 at 10400 baud: 5-baud init at address 0x33 with W1-W5 and
  P2/P3 timing, optional fast init (off by default, as on the real car),
  Mode 01 PIDs, Mode 02 freeze frame, Mode 03/07 DTCs, Mode 04 clear. On the
  P2P both protocols share 10400 baud, so the ECU tells them apart by
  content: a 5-baud init opens an ISO session, the wake frame and ``20 05``
  frames are Honda. The two exclude each other: while an ISO session is alive
  (until 5 s of silence) Honda requests go unanswered. That is a guess about
  the real ECU, chosen because it is the harder case for the app. ISO needs
  break and baud control, so it works on the in-process transport, not
  through the pty.
* Line faults (``Faults``): dropped bytes, bad checksums, junk, slow and
  missing replies, damaged echo, cable unplug. Seeded, so a run repeats.
* True byte timing: see ``LoopbackTransport(byte_timing=True)`` and ``serve``.

The RAM layout follows the default address map: kerpz's P2T addresses with
the P2P findings from the 2000 Civic EX (2026-11-25): TPS is the OBD2 PID 11
byte, and the flag bytes carry the bits seen on the car (starter, A/C switch,
PSP switch, brake in 0x08; VTEC in 0x0A; fuel pump, A/C clutch, MIL in 0x0B;
fan command in 0x0C; closed loop in 0x0F).
"""

from __future__ import annotations

import argparse
import functools
import math
import os
import random
import select
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from typing import IO, Any

from . import protocol
from .protocol import Framing

SIM_SYMLINK = "/tmp/hondalog-sim"      # same value as transport.SIM_SYMLINK

_ECT_POLY = (155.04149, -3.0414878, 0.03952185, -0.00029383913,
             0.0000010792568, -0.0000000015618437)
_TEMP_TABLE = [sum(c * x ** i for i, c in enumerate(_ECT_POLY)) for x in range(256)]

ISO_BAUD = 10400
ISO_ADDRESS = 0x33
BIT_5BAUD_S = 0.2
P3_MIN_S = 0.055           # ECU ignores a request sooner than this after its reply
P3_MAX_S = 5.0             # ISO session dies after this much silence
W5_S = 0.3                 # line must be idle this long before a 5-baud init
KWP_START = bytes.fromhex("c133f18166")
#: The stock P2P: Honda reads at 10400 baud, after the wake frame (the
#: init_sequence, which Framing has by default).
P2P_FRAMING = protocol.DEFAULT.with_overrides({"baud": ISO_BAUD})
#: kerpz's P2T test ECU: Honda reads at 9600 baud.
P2T_FRAMING = protocol.DEFAULT
KWP_START_REPLY = bytes.fromhex("83f110c1e98fbd")


@functools.lru_cache(maxsize=512)
def _temp_raw_whole(deg_c: int) -> int:
    return min(range(256), key=lambda x: abs(_TEMP_TABLE[x] - deg_c))


def _temp_raw(deg_c: float) -> int:
    """Sensor byte whose decoded temperature is closest (1 degree steps)."""
    return _temp_raw_whole(round(deg_c))


def _u8(value: float) -> int:
    return max(0, min(255, int(round(value))))


# ---- wire-level replies and faults ------------------------------------------

@dataclass(frozen=True)
class Chunk:
    data: bytes
    gap_s: float = 0.0          # silence on the wire before this chunk


@dataclass(frozen=True)
class Reply:
    """Bytes the ECU puts on the wire, ``delay_s`` after the event that caused them."""

    chunks: tuple[Chunk, ...]
    delay_s: float
    baud: int


@dataclass
class Faults:
    """Line fault probabilities, each per reply (or per echoed request)."""

    drop_byte: float = 0.0       # one reply byte goes missing
    bad_checksum: float = 0.0    # one bit of the reply flips
    junk: float = 0.0            # 1-4 random bytes in front of the reply
    no_reply: float = 0.0        # the reply never comes
    slow_reply: float = 0.0      # the reply comes slow_reply_s late
    slow_reply_s: float = 0.15
    drop_echo: float = 0.0       # one byte of our own echo goes missing
    seed: int = 1

    _ALIASES = {"drop": "drop_byte", "cksum": "bad_checksum", "slow": "slow_reply",
                "silent": "no_reply", "echo": "drop_echo"}

    @classmethod
    def parse(cls, text: str) -> "Faults":
        """'drop=0.01,cksum=0.01,junk=0.02,slow=0.005,seed=7' -> Faults."""
        known = {f.name: f.type for f in fields(cls)}
        values: dict[str, Any] = {}
        for item in filter(None, (part.strip() for part in text.split(","))):
            key, sep, value = item.partition("=")
            key = cls._ALIASES.get(key.strip(), key.strip())
            if not sep or key not in known:
                raise ValueError(f"bad fault setting {item!r}; known: {sorted(known)}")
            values[key] = int(value) if key == "seed" else float(value)
        return cls(**values)

    @classmethod
    def flaky(cls, seed: int = 1) -> "Faults":
        """A poor cable: about 3% of exchanges go wrong one way or another."""
        return cls(drop_byte=0.008, bad_checksum=0.008, junk=0.006, no_reply=0.004,
                   slow_reply=0.002, drop_echo=0.002, seed=seed)

    def any(self) -> bool:
        return any((self.drop_byte, self.bad_checksum, self.junk, self.no_reply,
                    self.slow_reply, self.drop_echo))


@dataclass
class FreezeFrame:
    dtc: str = "P0135"
    rpm: float = 812.0
    ect: float = 84.0
    map_kpa: float = 34.0
    vss: float = 0.0
    load: float = 22.0
    stft: float = 1.6
    ltft: float = 2.3
    fuel_status: int = 2


def dtc_to_bytes(code: str) -> bytes:
    """'P0135' -> b'\x01\x35'."""
    system = "PCBU".index(code[0].upper())
    return bytes([(system << 6) | (int(code[1], 16) << 4) | int(code[2], 16),
                  (int(code[3], 16) << 4) | int(code[4], 16)])


@dataclass
class _Live:
    """Engine values of the last step, for the OBD2 PIDs."""

    map_kpa: float = 101.0
    o2: float = 0.05
    stft: float = 0.0
    ltft: float = 2.0
    ign: float = 0.0
    iat: float = 28.0
    closed_loop: bool = False
    counts: dict[str, int] = field(default_factory=dict)


class EcuModel:
    """A warm-ish D16 that idles, revs now and then, and can be made to die."""

    def __init__(self, framing: Framing = P2P_FRAMING, warmup_s: float = 120.0,
                 rev_every_s: float = 20.0, stall_after_s: float | None = None,
                 stall_mode: str = "rpm", restart_after_s: float | None = 15.0,
                 echo: bool = True, seed: int = 1,
                 clock: Callable[[], float] = time.monotonic,
                 faults: Faults | None = None, turnaround_s: float = 0.002,
                 max_read_len: int | None = None, honda_protocol: bool = True,
                 iso: bool = True, iso_fast_init: bool = False,
                 dtcs: tuple[str, ...] = (), pending_dtcs: tuple[str, ...] = (),
                 need_wake: bool | None = None) -> None:
        self.framing = framing             # Honda reads are answered at framing.baud only
        # None: like the P2P, a 10400 baud ECU wants the wake frame first
        self.need_wake = (framing.baud == ISO_BAUD) if need_wake is None else need_wake
        self.woken = False
        self.wakes = 0
        self.honda_ignored = 0             # Honda reads dropped: not woken yet
        self.faults = faults or Faults()
        self.fault_rng = random.Random(self.faults.seed)
        self.fault_counts: dict[str, int] = {}
        self.turnaround_s = turnaround_s
        self.max_read_len = max_read_len
        self.honda_protocol = honda_protocol   # False: answers ISO 9141-2 only
        self.iso = iso
        self.iso_fast_init = iso_fast_init
        self.cable_dead = False                # True: no echo, nothing (no 12 V at the plug)
        self.dtcs = list(dtcs)
        self.pending_dtcs = list(pending_dtcs)
        self.freeze: FreezeFrame | None = FreezeFrame(dtc=dtcs[0]) if dtcs else None
        self.monitors_incomplete = False       # True after a Mode 04 clear
        self.live = _Live()
        self.iso_inits = 0
        self.iso_requests = 0
        self._unplugged_until = 0.0
        self._unplug_window = (0.0, 0.0)
        self._rxbuf = bytearray()
        self._last_rx = float("-inf")
        self._last_activity = float("-inf")
        self._lows: list[tuple[float, float]] = []
        self._iso_state = "off"                # off | await_kb | active
        self._iso_kwp = False
        self._iso_last = 0.0
        self._iso_busy_until = 0.0
        self._fast_armed_until = float("-inf")
        self.init_tolerance_s = 0.015          # how sloppy the 5-baud bit edges may be
        self.warmup_s = warmup_s
        self.rev_every_s = rev_every_s
        self.stall_after_s = stall_after_s
        self.stall_mode = stall_mode
        self.restart_after_s = restart_after_s
        self.echo = echo
        self.clock = clock
        self.rng = random.Random(seed)
        self.ram = bytearray(self.rng.randrange(256) for _ in range(256))
        self.ram[0x76:0x80] = b"SIM-P2P\x00\x00\x01"       # fake ECU id
        self.t0 = self.last = clock()
        self.running = True
        self.powered = True        # False = ECU lost power: no replies at all
        self.rpm = 1500.0
        self.ect = 20.0
        self.tps = 0.0
        self.ac_on = False         # A/C button: 08 b1, clutch 0B b1, idle-up
        self.brake_on = False
        self.rev_started: float | None = None
        self.next_rev = rev_every_s
        self.stopped_at: float | None = None
        self.requests = 0
        self.step()

    # -- controls --

    def stall(self) -> None:
        """Engine dies, ECU stays alive (RPM reads 0)."""
        self.running = False
        self.stopped_at = self.clock() - self.t0

    def power_cut(self) -> None:
        """ECU loses power: engine dies and the K-line goes silent."""
        self.stall()
        self.powered = False
        self.woken = False         # the wake frame has to be sent again

    def restart(self) -> None:
        self.running = self.powered = True
        self.stopped_at = None
        self.rpm = max(self.rpm, 300.0)

    def rev(self) -> None:
        if self.running and self.rev_started is None:
            self.rev_started = self.clock() - self.t0

    def ac(self, on: bool | None = None) -> None:
        """Press the A/C button (toggle, or set with ``on``)."""
        self.ac_on = (not self.ac_on) if on is None else on

    # -- engine --

    def step(self) -> None:
        now = self.clock()
        t, dt = now - self.t0, min(1.0, max(0.0, now - self.last))
        self.last = now

        if self.stall_after_s is not None and self.running and t >= self.stall_after_s:
            self.stall_after_s = None
            if self.stall_mode == "power":
                self.power_cut()
            else:
                self.stall()
        if (not self.running and self.restart_after_s is not None
                and self.stopped_at is not None
                and t - self.stopped_at >= self.restart_after_s):
            self.restart()
        if self.rev_every_s and self.running and t >= self.next_rev:
            self.next_rev = t + self.rev_every_s
            self.rev()

        self.ect = 20.0 + 68.0 * (1 - math.exp(-3.0 * t / max(1.0, self.warmup_s)))
        cold = max(0.0, (70.0 - self.ect) / 50.0)
        clutch = self.ac_on and self.running
        idle = 750.0 + 750.0 * cold + (300.0 if clutch else 0.0)    # A/C idle-up (~1050 warm)
        self.tps = 0.0
        target = idle
        if self.rev_started is not None:
            phase = (t - self.rev_started) / 3.0
            if phase >= 1.0 or not self.running:
                self.rev_started = None
            else:
                self.tps = 55.0 * math.sin(math.pi * phase) ** 2
                target = idle + 3000.0 * math.sin(math.pi * phase)
        if not self.running:
            target = 0.0
        rate = 6.0 if self.running else 9.0
        self.rpm += (target - self.rpm) * min(1.0, rate * dt)
        if not self.running and self.rpm < 60.0:
            self.rpm = 0.0
        rpm = self.rpm + (self.rng.uniform(-12, 12) if self.rpm > 100 else 0.0)

        turning = self.rpm > 100
        cranking = self.running and turning and self.rpm < 500
        closed_loop = turning and not cranking and self.ect > 60.0 and self.tps < 10.0
        map_kpa = 101.0 if not turning else 30.0 + 0.9 * self.tps + 6.0 * cold
        o2 = 0.45 + 0.4 * math.sin(t * 5.0) if closed_loop else (0.8 if turning else 0.05)
        batt = 14.2 + self.rng.uniform(-0.05, 0.05) if turning else 12.3
        stft = 4.0 * math.sin(t * 0.7) if closed_loop else 0.0
        inj = 0.0 if not turning else 2.4 + 0.05 * self.tps + 1.2 * cold
        ign = 0.0 if not turning else 16.0 + min(20.0, (self.rpm - 750) / 150.0)
        iacv = 0.0 if not turning else 30.0 + 25.0 * cold
        live = self.live
        live.map_kpa, live.o2, live.stft, live.ign = map_kpa, o2, stft, ign
        live.closed_loop = closed_loop

        r = self.ram
        r[0x00:0x02] = max(0, min(0xFFFF, int(rpm * 4))).to_bytes(2, "big")
        r[0x02] = 0                                         # VSS: parked
        # Flag bytes as read on the 2000 Civic EX P2P. Key on engine off:
        # 08=10000100 0A=00010000 0B=00100001; cranking 08=10000101; running
        # 08=10000100 0A=10010000 0B=01000001 0F=00000001.
        stopped_s = None if self.stopped_at is None else t - self.stopped_at
        pump = self.running or (stopped_s is not None and stopped_s < 2.0)   # 2 s prime
        mil = (not self.running) or bool(self.dtcs)         # bulb check at key on
        r[0x08] = (0x80 | 0x04                              # b2: PSP switch (closed at rest)
                   | (0x02 if self.ac_on else 0)            # b1: A/C switch
                   | (0x08 if self.brake_on else 0)         # b3: brake switch
                   | (0x01 if cranking else 0))             # b0: starter
        r[0x0A] = (0x10 | (0x80 if turning else 0)
                   | (0x04 if self.rpm > 5500 else 0))      # b2: VTEC solenoid
        r[0x0B] = ((0x40 if turning else 0) | (0x20 if mil else 0)
                   | (0x02 if clutch else 0)                # b1: A/C clutch
                   | (0x01 if pump else 0))                 # b0: fuel pump (main relay)
        r[0x0C] = 0x02 if self.ect > 98.0 else 0x00         # b1: ECM fan command
        r[0x0F] = 0x01 if closed_loop else 0x00             # b0: closed loop
        r[0x10] = _temp_raw(self.ect)
        r[0x11] = _temp_raw(28.0)
        r[0x12] = _u8((map_kpa + 5) / 0.716)
        r[0x13] = _u8((101.0 + 5) / 0.716)
        r[0x14] = _u8(self.tps * 2.55)                      # = the OBD2 PID 11 byte
        r[0x15] = _u8(o2 * 51.3)
        r[0x17] = _u8(batt * 10.45)
        r[0x18] = _u8(40 * 2.55)
        r[0x19] = _u8((77.06 - 22.0) * 2.5371)
        r[0x20] = _u8((stft / 100 + 1) * 128)
        r[0x21] = r[0x22] = _u8((2.0 / 100 + 1) * 128)
        r[0x24:0x26] = int(inj * 250).to_bytes(2, "big")
        r[0x26] = r[0x27] = _u8(ign * 4 + 24)
        r[0x28] = _u8(iacv * 2.55)
        r[0x3C] = 0
        # Some life elsewhere so the memory scanner has something to find.
        r[0x80] = (r[0x80] + 1) & 0xFF
        r[0x90:0x92] = r[0x00:0x02]
        r[0xA0] = _u8(self.tps * 2.55)                      # a copy of the throttle byte

    # -- cable --

    def unplug(self, for_s: float | None = None) -> None:
        """Pull the USB cable. ``for_s``: plug it back in after that long."""
        self._unplugged_until = float("inf") if for_s is None else self.clock() + for_s

    def replug(self) -> None:
        self._unplugged_until = 0.0
        self._unplug_window = (0.0, 0.0)

    def schedule_unplug(self, at: float, for_s: float) -> None:
        """Pull the cable at clock time ``at`` for ``for_s`` seconds."""
        self._unplug_window = (at, at + for_s)

    def unplugged(self, now: float | None = None) -> bool:
        t = self.clock() if now is None else now
        return t < self._unplugged_until or self._unplug_window[0] <= t < self._unplug_window[1]

    # -- faults --

    def _hit(self, name: str) -> bool:
        p = float(getattr(self.faults, name))
        if p <= 0.0 or self.fault_rng.random() >= p:
            return False
        self.fault_counts[name] = self.fault_counts.get(name, 0) + 1
        return True

    def echo_of(self, data: bytes) -> bytes:
        """What the tester hears of its own transmission."""
        if len(data) > 1 and self._hit("drop_echo"):
            i = self.fault_rng.randrange(len(data))
            return data[:i] + data[i + 1:]
        return data

    def _damage(self, frame: bytes) -> tuple[bytes, float]:
        """Apply the fault dice to one reply. Returns (bytes, extra delay)."""
        if not self.faults.any() or not frame:
            return frame, 0.0
        rng = self.fault_rng
        out, extra = bytearray(frame), 0.0
        if self._hit("no_reply"):
            return b"", 0.0
        if self._hit("drop_byte"):
            del out[rng.randrange(len(out))]
        if self._hit("bad_checksum"):
            out[rng.randrange(len(out))] ^= 1 << rng.randrange(8)
        if self._hit("junk"):
            out[:0] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 4)))
        if self._hit("slow_reply"):
            extra = self.faults.slow_reply_s
        return bytes(out), extra

    # -- Honda protocol --

    def handle(self, frame: bytes) -> bytes:
        """Reply to one checksum-valid request frame (b'' = stay silent)."""
        self.requests += 1
        self.step()
        if (not self.powered or not self.honda_protocol
                or frame[0] != self.framing.cmd_read or len(frame) != 5):
            return b""
        if self.need_wake and not self.woken:
            self.honda_ignored += 1
            return b""
        addr, length = frame[2], frame[3]
        if length == 0 or (self.max_read_len is not None and length > self.max_read_len):
            return b""
        data = bytes(self.ram[(addr + i) & 0xFF] for i in range(length))
        return protocol.build_reply(data, self.framing)

    def _frames(self, buf: bytearray) -> list[bytes]:
        out: list[bytes] = []
        while len(buf) >= 2:
            size = buf[1]
            if not 3 <= size <= 16:
                del buf[0]
                continue
            if len(buf) < size:
                break
            if protocol.verify(buf[:size]):
                reply = self.handle(bytes(buf[:size]))
                if reply:
                    out.append(reply)
                del buf[:size]
            else:
                del buf[0]
        return out

    def feed(self, buf: bytearray) -> bytes:
        """Consume request bytes from ``buf`` (in place); return reply bytes.

        Like the real thing it never complains: frames with a bad length or
        checksum are dropped a byte at a time until it finds one it likes.
        No faults and no timing here; see ``on_bytes`` for the wire-level view.
        """
        self._wake_in(buf)
        return b"".join(self._frames(buf))

    def _wake_in(self, buf: bytearray) -> None:
        """Take the wake frame out of ``buf``; a powered ECU is awake after it."""
        seq = self.framing.init_sequence
        if not seq:
            return
        at = buf.find(seq)
        while at >= 0:
            del buf[at:at + len(seq)]
            self.step()                     # power state is due now (key on)
            if self.powered:
                self.woken = True
                self.wakes += 1
            at = buf.find(seq)

    # -- the wire --

    def _iso_alive(self, now: float) -> bool:
        if self._iso_state != "off" and now - self._iso_last > P3_MAX_S:
            self._iso_state = "off"
        return self._iso_state != "off"

    def on_bytes(self, data: bytes, baud: int, now: float) -> list[Reply]:
        """Bytes from the tester, complete on the wire at ``now``."""
        gap = now - self._last_rx
        self._last_rx = self._last_activity = now
        # Told apart by content, not only by speed: on the P2P both protocols
        # run at 10400 baud. An ISO session (opened by a 5-baud init, or a
        # fast init) takes everything until it times out; otherwise bytes at
        # the Honda speed are Honda (wake frame, 20 05 reads).
        iso_ok = baud == ISO_BAUD and self.powered and self.iso
        fast_start = (iso_ok and self.iso_fast_init and data == KWP_START
                      and now <= self._fast_armed_until + 0.1)
        if self._iso_alive(now) or fast_start or baud != self.framing.baud:
            self._rxbuf.clear()
            if not iso_ok:
                return []                       # wrong speed: framing errors, ignored
            return self._iso_bytes(data, now)
        if gap > 0.05:
            self._rxbuf.clear()                 # stale partial request
        self._rxbuf += data
        self._wake_in(self._rxbuf)
        chunks: list[Chunk] = []
        delay = self.turnaround_s
        for frame in self._frames(self._rxbuf):
            damaged, extra = self._damage(frame)
            if not damaged:
                continue
            if chunks:
                chunks.append(Chunk(damaged, self.turnaround_s + extra))
            else:
                delay += extra
                chunks.append(Chunk(damaged))
        return [Reply(tuple(chunks), delay, self.framing.baud)] if chunks else []

    def on_break(self, start: float, end: float) -> list[Reply]:
        """The tester held the line low from ``start`` to ``end``."""
        idle_before = start - self._last_activity
        self._last_activity = end
        if not self.powered or not self.iso:
            return []
        if self._lows and start - self._lows[0][0] > 10 * BIT_5BAUD_S + 0.1:
            self._lows.clear()
        if not self._lows:
            if 0.020 <= end - start <= 0.030:               # fast init wake-up pattern
                self._fast_armed_until = end + 0.1
                return []
            if idle_before < W5_S:
                return []                                   # bus was not idle: ignored
        self._lows.append((start, end))
        t0 = self._lows[0][0]
        tol = self.init_tolerance_s
        for s, e in self._lows:
            for edge in (s, e):
                k = (edge - t0) / BIT_5BAUD_S
                if abs(k - round(k)) * BIT_5BAUD_S > tol:
                    self._lows.clear()
                    return []
        bits = []
        for k in range(10):
            centre = t0 + (k + 0.5) * BIT_5BAUD_S
            bits.append(0 if any(s <= centre < e for s, e in self._lows) else 1)
        want = [0] + [(ISO_ADDRESS >> i) & 1 for i in range(8)] + [1]
        if bits != want:
            return []                                       # not (yet) address 0x33
        self._lows.clear()
        self.iso_inits += 1
        self._iso_state, self._iso_kwp = "await_kb", False
        self._iso_last = t0 + 10 * BIT_5BAUD_S
        w1 = 0.1
        self._iso_busy_until = self._iso_last + w1
        return [Reply((Chunk(b"\x55"), Chunk(b"\x08", 0.01), Chunk(b"\x08", 0.01)),
                      self._iso_last + w1 - end, ISO_BAUD)]

    # -- ISO 9141-2 / KWP2000 --

    def _iso_bytes(self, data: bytes, now: float) -> list[Reply]:
        if self._iso_state == "await_kb":
            if data == b"\xf7":                             # inverted key byte 2
                self._iso_state, self._iso_last = "active", now
                self._iso_busy_until = now + 0.03 + 10.0 / ISO_BAUD
                return [Reply((Chunk(bytes([~ISO_ADDRESS & 0xFF])),), 0.03, ISO_BAUD)]
            self._iso_state = "off"
            return []
        if self._iso_state == "off":
            if (self.iso_fast_init and now <= self._fast_armed_until + 0.1
                    and data == KWP_START):
                self._iso_state, self._iso_kwp, self._iso_last = "active", True, now
                self.iso_inits += 1
                return [Reply((Chunk(KWP_START_REPLY),), 0.03, ISO_BAUD)]
            return []
        if now < self._iso_busy_until:
            return []                                       # sooner than P3min: ignored
        payload = self._iso_payload(data)
        if payload is None:
            return []
        self._iso_last = now
        self.iso_requests += 1
        self.step()
        chunks: list[Chunk] = []
        delay, total = 0.03, 0.0
        for message in self._obd(payload):
            framed, extra = self._damage(self._iso_frame(message))
            if not framed:
                continue
            gap = 0.03 + extra if chunks else 0.0
            if not chunks:
                delay += extra
            chunks.append(Chunk(framed, gap))
            total += gap + len(framed) * 10.0 / ISO_BAUD
        if not chunks:
            return []
        self._iso_busy_until = now + delay + total + P3_MIN_S
        return [Reply(tuple(chunks), delay, ISO_BAUD)]

    def _iso_payload(self, data: bytes) -> bytes | None:
        if len(data) < 5 or sum(data[:-1]) & 0xFF != data[-1]:
            return None
        if self._iso_kwp:
            n = data[0] & 0x3F
            if data[0] & 0xC0 != 0xC0 or data[1] != 0x33 or len(data) != n + 4:
                return None
            return data[3:-1]
        if data[:3] != b"\x68\x6a\xf1":
            return None
        return data[3:-1]

    def _iso_frame(self, payload: bytes) -> bytes:
        if self._iso_kwp:
            body = bytes([0x80 | len(payload), 0xF1, 0x10]) + payload
        else:
            body = b"\x48\x6b\x10" + payload
        return body + bytes([sum(body) & 0xFF])

    def _pid(self, pid: int, frozen: bool = False) -> bytes | None:
        """Mode 01 (or freeze frame) data bytes for one PID."""
        live, ff = self.live, self.freeze
        if frozen:
            if ff is None:
                return None
            rpm, ect, map_kpa, vss = ff.rpm, ff.ect, ff.map_kpa, ff.vss
            stft, ltft, load, fuel = ff.stft, ff.ltft, ff.load, ff.fuel_status
        else:
            rpm, ect, map_kpa, vss = self.rpm, self.ect, live.map_kpa, 0.0
            stft, ltft = live.stft, live.ltft
            load = 100.0 * map_kpa / 101.0 if self.rpm > 100 else 0.0
            fuel = 2 if live.closed_loop else 1

        def trim(x: float) -> int:
            return _u8((x / 100 + 1) * 128)

        table: dict[int, bytes] = {
            0x03: bytes([fuel, 0]),
            0x04: bytes([_u8(load * 2.55)]),
            0x05: bytes([_u8(ect + 40)]),
            0x06: bytes([trim(stft)]),
            0x07: bytes([trim(ltft)]),
            0x0B: bytes([_u8(map_kpa)]),
            0x0C: max(0, min(0xFFFF, int(rpm * 4))).to_bytes(2, "big"),
            0x0D: bytes([_u8(vss)]),
        }
        if frozen:
            table[0x02] = dtc_to_bytes(ff.dtc) if ff is not None else b"\x00\x00"
        else:
            monitors = 0x65                     # catalyst, evap, O2 sensor, O2 heater
            table.update({
                0x01: bytes([(0x80 if self.dtcs else 0) | min(0x7F, len(self.dtcs)), 0x07,
                             monitors, monitors if self.monitors_incomplete else 0x00]),
                0x0E: bytes([_u8((live.ign + 64) * 2)]),
                0x0F: bytes([_u8(live.iat + 40)]),
                0x11: bytes([_u8(self.tps * 2.55)]),
                0x13: b"\x03",
                0x14: bytes([_u8(live.o2 / 0.005), trim(stft)]),
                0x15: bytes([_u8(0.6 / 0.005), 0xFF]),
                0x1C: b"\x01",
            })
        if pid == 0x00:
            bitmap = 0
            for p in table:
                bitmap |= 1 << (32 - p)
            return bitmap.to_bytes(4, "big")
        return table.get(pid)

    def _obd(self, payload: bytes) -> list[bytes]:
        """OBD2 service handler: request payload -> response payloads."""
        mode = payload[0]

        def dtc_messages(codes: list[str]) -> list[bytes]:
            raw = b"".join(dtc_to_bytes(c) for c in codes)
            raw = raw + bytes(-len(raw) % 6) if raw else bytes(6)
            return [bytes([mode + 0x40]) + raw[i:i + 6] for i in range(0, len(raw), 6)]

        if mode == 0x01 and len(payload) == 2:
            data = self._pid(payload[1])
            return [bytes([0x41, payload[1]]) + data] if data is not None else []
        if mode == 0x02 and len(payload) == 3:
            data = self._pid(payload[1], frozen=True)
            if data is None or payload[2] != 0:
                return []
            return [bytes([0x42, payload[1], payload[2]]) + data]
        if mode == 0x03 and len(payload) == 1:
            return dtc_messages(self.dtcs)
        if mode == 0x07 and len(payload) == 1:
            return dtc_messages(self.pending_dtcs)
        if mode == 0x04 and len(payload) == 1:
            self.dtcs.clear()
            self.pending_dtcs.clear()
            self.freeze = None
            self.monitors_incomplete = True
            return [b"\x44"]
        return []


# ---- pty server -------------------------------------------------------------

HELP = ("keys:  s = stall (RPM to 0)   p = power cut (ECU silent)   r = restart   v = rev\n"
        "       u = unplug the cable for 3 s   q = quit")


class _Pty:
    """The pseudo-terminal pair and its symlink; can be pulled and replugged."""

    def __init__(self, link_path: str, out: IO[str]) -> None:
        self.link_path = link_path
        self.out = out
        self.master = self.slave = -1
        self.plug()

    def plug(self) -> None:
        import tty
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)            # no line discipline echo or translation
        pts = os.ttyname(self.slave)
        try:
            if os.path.islink(self.link_path) or os.path.exists(self.link_path):
                os.unlink(self.link_path)
            os.symlink(pts, self.link_path)
        except OSError as exc:
            print(f"could not create {self.link_path}: {exc}", file=self.out)
            self.link_path = pts
        print(f"simulated ECU on {self.link_path} -> {pts}", file=self.out)
        self.out.flush()

    def pull(self) -> None:
        try:
            if os.path.islink(self.link_path):
                os.unlink(self.link_path)
        except OSError:
            pass
        for fd in (self.master, self.slave):
            if fd >= 0:
                os.close(fd)
        self.master = self.slave = -1


def serve(model: EcuModel, link_path: str = SIM_SYMLINK, realtime: bool = True,
          keys: bool = True, run_for: float | None = None,
          out: IO[str] = sys.stdout) -> None:
    """Serve the model on a pty. ``realtime``: pace every byte, echo included,
    at the true baud rate; otherwise bytes go out as fast as the pty takes them."""
    import termios
    import tty

    pty = _Pty(link_path, out)
    stdin_fd: int | None = None
    old_term: Any = None
    if keys and sys.stdin.isatty():
        stdin_fd = sys.stdin.fileno()
        old_term = termios.tcgetattr(stdin_fd)
        tty.setcbreak(stdin_fd)
        print(HELP, file=out)
    out.flush()

    baud = model.framing.baud
    pending: list[tuple[float, int]] = []       # (due, byte)
    wire_free = 0.0
    last_status = time.monotonic()
    end_at = None if run_for is None else time.monotonic() + run_for

    def queue(start: float, data: bytes, byte_s: float) -> float:
        t = start
        for b in data:
            t += byte_s
            pending.append((t, b))
        return t

    try:
        while end_at is None or time.monotonic() < end_at:
            now = time.monotonic()
            if model.unplugged(now):
                if pty.master >= 0:
                    pty.pull()
                    pending.clear()
                    print("\n[cable unplugged]", file=out, flush=True)
            elif pty.master < 0:
                pty.plug()
            timeout = 0.1
            if pending:
                timeout = max(0.0, min(timeout, pending[0][0] - now))
            fds = ([pty.master] if pty.master >= 0 else []) + ([stdin_fd] if stdin_fd is not None else [])
            ready, _, _ = select.select(fds, [], [], timeout) if fds else ([], [], [])
            if not fds:
                time.sleep(timeout)
            now = time.monotonic()
            if stdin_fd is not None and stdin_fd in ready:
                key = os.read(stdin_fd, 1).decode(errors="ignore").lower()
                if key == "q":
                    break
                if key == "u":
                    model.unplug(3.0)
                action = {"s": model.stall, "p": model.power_cut,
                          "r": model.restart, "v": model.rev}.get(key)
                if action:
                    action()
                    print(f"\n[{action.__name__}]", file=out)
            if pty.master >= 0 and pty.master in ready:
                try:
                    data = os.read(pty.master, 256)
                except OSError:
                    data = b""
                if data and not model.cable_dead:
                    byte_s = 10.0 / baud if realtime else 0.0
                    start = max(now, wire_free)
                    tx_end = wire_free = start + byte_s * len(data)
                    if model.echo:                  # K-line: you hear yourself
                        queue(start, model.echo_of(data), byte_s)
                    for reply in model.on_bytes(data, baud, tx_end):
                        t = tx_end + reply.delay_s
                        for chunk in reply.chunks:
                            t = queue(t + chunk.gap_s, chunk.data,
                                      10.0 / reply.baud if realtime else 0.0)
                    pending.sort(key=lambda item: item[0])
            if pending and pty.master >= 0:
                n = 0
                while n < len(pending) and pending[n][0] <= now:
                    n += 1
                if n:
                    try:
                        os.write(pty.master, bytes(b for _, b in pending[:n]))
                    except OSError:
                        pass
                    del pending[:n]
            model.step()
            if stdin_fd is not None and now - last_status > 0.5:
                last_status = now
                state = "RUN " if model.running else ("DEAD" if not model.powered else "STALL")
                print(f"\r{state} rpm {model.rpm:5.0f}  ect {model.ect:3.0f} C  "
                      f"requests {model.requests}   ", end="", file=out, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if old_term is not None and stdin_fd is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_term)
        pty.pull()
        print("\nsimulator stopped", file=out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hondalog-sim", description="Simulated Honda ECU on a pty.")
    ap.add_argument("--link", default=SIM_SYMLINK, help=f"symlink to create (default {SIM_SYMLINK})")
    ap.add_argument("--warmup", type=float, default=120.0, help="seconds to reach operating temperature")
    ap.add_argument("--rev-every", type=float, default=20.0, help="seconds between fake revs, 0 = never")
    ap.add_argument("--stall-after", type=float, default=None, metavar="SEC", help="stall once after SEC seconds")
    ap.add_argument("--stall-mode", choices=("rpm", "power"), default="rpm",
                    help="rpm: RPM drops to 0, ECU keeps answering. power: ECU goes silent")
    ap.add_argument("--restart-after", type=float, default=15.0, help="seconds a stall lasts, 0 = until 'r'")
    ap.add_argument("--no-echo", action="store_true", help="do not echo requests (not like a real K-line)")
    ap.add_argument("--fast", action="store_true",
                    help="skip the true byte timing (bytes go out at once)")
    ap.add_argument("--faults", default="", metavar="SPEC",
                    help="line faults, e.g. 'drop=0.01,cksum=0.01,junk=0.01,slow=0.005,silent=0.005,"
                         "echo=0.005' or 'flaky'")
    ap.add_argument("--seed", type=int, default=1, help="seed for engine noise and faults (runs repeat)")
    ap.add_argument("--turnaround-ms", type=float, default=2.0,
                    help="ECU think time between request and reply (default 2)")
    ap.add_argument("--max-read-len", type=int, default=None, help="ECU ignores longer reads")
    ap.add_argument("--unplug-after", type=float, default=None, metavar="SEC",
                    help="pull the cable once after SEC seconds")
    ap.add_argument("--unplug-for", type=float, default=3.0, metavar="SEC",
                    help="how long the cable stays out (default 3)")
    ap.add_argument("--cable-dead", action="store_true", help="no echo, no replies (no 12 V at the plug)")
    ap.add_argument("--iso-only", action="store_true",
                    help="ECU ignores Honda reads (ISO needs the built-in sim: port, not the pty)")
    ap.add_argument("--ecu", choices=("P2P", "P2T"), default="P2P",
                    help="P2P (default): Honda reads at 10400 baud after the wake frame, as on "
                         "the 2000 Civic EX. P2T: kerpz's test ECU, Honda reads at 9600 baud")
    ap.add_argument("--run-for", type=float, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    faults = Faults.flaky(args.seed) if args.faults == "flaky" else Faults.parse(args.faults)
    faults.seed = args.seed
    model = EcuModel(framing=P2P_FRAMING if args.ecu == "P2P" else P2T_FRAMING,
                     warmup_s=args.warmup, rev_every_s=args.rev_every,
                     stall_after_s=args.stall_after, stall_mode=args.stall_mode,
                     restart_after_s=args.restart_after or None, echo=not args.no_echo,
                     seed=args.seed, faults=faults, turnaround_s=args.turnaround_ms / 1000.0,
                     max_read_len=args.max_read_len, honda_protocol=not args.iso_only)
    model.cable_dead = args.cable_dead
    if args.unplug_after is not None:
        model.schedule_unplug(time.monotonic() + args.unplug_after, args.unplug_for)
    serve(model, args.link, realtime=not args.fast, run_for=args.run_for)
    return 0


if __name__ == "__main__":
    sys.exit(main())
