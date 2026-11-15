"""Simulated Honda ECU.

``EcuModel`` is the engine + RAM + request parser (no I/O, used by the tests
and by the built-in "sim:" port). ``main`` serves it on a pseudo-terminal so
the app can talk to it exactly as it talks to the KKL cable, echo included.

The RAM layout follows the same kerpz P2T guess as the default address map.
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

from . import protocol
from .protocol import Framing
from .transport import SIM_SYMLINK

_ECT_POLY = (155.04149, -3.0414878, 0.03952185, -0.00029383913,
             0.0000010792568, -0.0000000015618437)
_TEMP_TABLE = [sum(c * x ** i for i, c in enumerate(_ECT_POLY)) for x in range(256)]


@functools.lru_cache(maxsize=512)
def _temp_raw_whole(deg_c: int) -> int:
    return min(range(256), key=lambda x: abs(_TEMP_TABLE[x] - deg_c))


def _temp_raw(deg_c: float) -> int:
    """Sensor byte whose decoded temperature is closest (1 degree steps)."""
    return _temp_raw_whole(round(deg_c))


def _u8(value: float) -> int:
    return max(0, min(255, int(round(value))))


class EcuModel:
    """A warm-ish D16 that idles, revs now and then, and can be made to die."""

    def __init__(self, framing: Framing = protocol.DEFAULT, warmup_s: float = 120.0,
                 rev_every_s: float = 20.0, stall_after_s: float | None = None,
                 stall_mode: str = "rpm", restart_after_s: float | None = 15.0,
                 echo: bool = True, seed: int = 1, clock=time.monotonic):
        self.framing = framing
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

    def restart(self) -> None:
        self.running = self.powered = True
        self.stopped_at = None
        self.rpm = max(self.rpm, 300.0)

    def rev(self) -> None:
        if self.running and self.rev_started is None:
            self.rev_started = self.clock() - self.t0

    # -- engine --

    def step(self) -> None:
        now = self.clock()
        t, dt = now - self.t0, min(1.0, max(0.0, now - self.last))
        self.last = now

        if self.stall_after_s is not None and self.running and t >= self.stall_after_s:
            self.stall_after_s = None
            self.power_cut() if self.stall_mode == "power" else self.stall()
        if (not self.running and self.restart_after_s is not None
                and t - self.stopped_at >= self.restart_after_s):
            self.restart()
        if self.rev_every_s and self.running and t >= self.next_rev:
            self.next_rev = t + self.rev_every_s
            self.rev()

        self.ect = 20.0 + 68.0 * (1 - math.exp(-3.0 * t / max(1.0, self.warmup_s)))
        cold = max(0.0, (70.0 - self.ect) / 50.0)
        idle = 750.0 + 750.0 * cold
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
        closed_loop = turning and self.ect > 60.0
        map_kpa = 101.0 if not turning else 30.0 + 0.9 * self.tps + 6.0 * cold
        o2 = 0.45 + 0.4 * math.sin(t * 5.0) if closed_loop else (0.8 if turning else 0.05)
        batt = 14.2 + self.rng.uniform(-0.05, 0.05) if turning else 12.3
        stft = 4.0 * math.sin(t * 0.7) if closed_loop else 0.0
        inj = 0.0 if not turning else 2.4 + 0.05 * self.tps + 1.2 * cold
        ign = 0.0 if not turning else 16.0 + min(20.0, (self.rpm - 750) / 150.0)
        iacv = 0.0 if not turning else 30.0 + 25.0 * cold

        r = self.ram
        r[0x00:0x02] = max(0, min(0xFFFF, int(rpm * 4))).to_bytes(2, "big")
        r[0x02] = 0                                         # VSS: parked
        r[0x08] &= ~0x04 & 0xFF                             # A/C off
        r[0x0A] = (r[0x0A] & ~0x08 & 0xFF) | (0x08 if self.rpm > 5500 else 0)
        r[0x0B] &= ~0x20 & 0xFF                             # MIL off
        r[0x0F] = (r[0x0F] & ~0x01 & 0xFF) | (1 if closed_loop else 0)
        r[0x10] = _temp_raw(self.ect)
        r[0x11] = _temp_raw(28.0)
        r[0x12] = _u8((map_kpa + 5) / 0.716)
        r[0x13] = _u8((101.0 + 5) / 0.716)
        r[0x14] = _u8(self.tps * 2 + 24)
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
        r[0xA0] = _u8(self.tps * 2.55)

    # -- protocol --

    def handle(self, frame: bytes) -> bytes:
        """Reply to one checksum-valid request frame (b'' = stay silent)."""
        self.requests += 1
        self.step()
        if not self.powered or frame[0] != self.framing.cmd_read or len(frame) != 5:
            return b""
        addr, length = frame[2], frame[3]
        if length == 0:
            return b""
        data = bytes(self.ram[(addr + i) & 0xFF] for i in range(length))
        return protocol.build_reply(data, self.framing)

    def feed(self, buf: bytearray) -> bytes:
        """Consume request bytes from ``buf`` (in place); return reply bytes.

        Like the real thing it never complains: frames with a bad length or
        checksum are dropped a byte at a time until it finds one it likes.
        """
        out = bytearray()
        while len(buf) >= 2:
            size = buf[1]
            if not 3 <= size <= 16:
                del buf[0]
                continue
            if len(buf) < size:
                break
            if protocol.verify(buf[:size]):
                out += self.handle(bytes(buf[:size]))
                del buf[:size]
            else:
                del buf[0]
        return bytes(out)


# ---- pty server -------------------------------------------------------------

HELP = "keys:  s = stall (RPM to 0)   p = power cut (ECU silent)   r = restart   v = rev   q = quit"


def serve(model: EcuModel, link_path: str = SIM_SYMLINK, realtime: bool = True,
          keys: bool = True, run_for: float | None = None, out=sys.stdout) -> None:
    import termios
    import tty

    master, slave = os.openpty()
    tty.setraw(slave)                 # no line discipline echo or translation
    pts = os.ttyname(slave)
    try:
        if os.path.islink(link_path) or os.path.exists(link_path):
            os.unlink(link_path)
        os.symlink(pts, link_path)
    except OSError as exc:
        print(f"could not create {link_path}: {exc}", file=out)
        link_path = pts
    print(f"simulated ECU on {link_path} -> {pts}", file=out)

    stdin_fd, old_term = None, None
    if keys and sys.stdin.isatty():
        stdin_fd = sys.stdin.fileno()
        old_term = termios.tcgetattr(stdin_fd)
        tty.setcbreak(stdin_fd)
        print(HELP, file=out)
    out.flush()

    byte_s = 10.0 / model.framing.baud if realtime else 0.0
    buf = bytearray()
    last_rx = last_status = time.monotonic()
    end = None if run_for is None else time.monotonic() + run_for
    try:
        while end is None or time.monotonic() < end:
            fds = [master] + ([stdin_fd] if stdin_fd is not None else [])
            ready, _, _ = select.select(fds, [], [], 0.1)
            now = time.monotonic()
            if stdin_fd in ready:
                key = os.read(stdin_fd, 1).decode(errors="ignore").lower()
                if key == "q":
                    break
                action = {"s": model.stall, "p": model.power_cut,
                          "r": model.restart, "v": model.rev}.get(key)
                if action:
                    action()
                    print(f"\n[{action.__name__}]", file=out)
            if master in ready:
                data = os.read(master, 256)
                if now - last_rx > 0.05:
                    buf.clear()                       # stale partial request
                last_rx = now
                if model.echo:
                    os.write(master, data)            # K-line: you hear yourself
                buf += data
                reply = model.feed(buf)
                if reply:
                    time.sleep(0.002 + byte_s * (len(data) + len(reply)))
                    os.write(master, reply)
            model.step()
            if stdin_fd is not None and now - last_status > 0.5:
                last_status = now
                state = "RUN " if model.running else ("DEAD" if not model.powered else "STALL")
                print(f"\r{state} rpm {model.rpm:5.0f}  ect {model.ect:3.0f} C  "
                      f"requests {model.requests}   ", end="", file=out, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if old_term is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_term)
        try:
            if os.path.islink(link_path):
                os.unlink(link_path)
        except OSError:
            pass
        os.close(master)
        os.close(slave)
        print("\nsimulator stopped", file=out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hondalog-sim", description="Simulated Honda ECU on a pty.")
    ap.add_argument("--link", default=SIM_SYMLINK, help=f"symlink to create (default {SIM_SYMLINK})")
    ap.add_argument("--warmup", type=float, default=120.0, help="seconds to reach operating temperature")
    ap.add_argument("--rev-every", type=float, default=20.0, help="seconds between fake revs, 0 = never")
    ap.add_argument("--stall-after", type=float, default=None, metavar="SEC", help="stall once after SEC seconds")
    ap.add_argument("--stall-mode", choices=("rpm", "power"), default="rpm",
                    help="rpm: RPM drops to 0, ECU keeps answering. power: ECU goes silent")
    ap.add_argument("--restart-after", type=float, default=15.0, help="seconds a stall lasts, 0 = until 'r'")
    ap.add_argument("--no-echo", action="store_true", help="do not echo requests (not like a real K-line)")
    ap.add_argument("--fast", action="store_true", help="skip the 9600 baud timing")
    ap.add_argument("--run-for", type=float, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    model = EcuModel(warmup_s=args.warmup, rev_every_s=args.rev_every,
                     stall_after_s=args.stall_after, stall_mode=args.stall_mode,
                     restart_after_s=args.restart_after or None, echo=not args.no_echo)
    serve(model, args.link, realtime=not args.fast, run_for=args.run_for)
    return 0


if __name__ == "__main__":
    sys.exit(main())
