"""Read-only ECU probe: what else does this ECU answer on the K-line?

One run (``hondalog-probe``), about 30-60 s, two stages:

 A. One ISO 9141-2 session (5-baud init at 0x33, kept alive). Standard OBD2
    read services (Mode 01 bitmaps, 05 O2 tests, 06, 09 vehicle info, 02
    freeze frame), then manufacturer-range READ services (0x21, 0x22, 0x23,
    0x1A), each with a short timeout and an early stop after repeated
    silence. 0x7F negative replies are recorded with their NRC.
 B. After the session has timed out (>5.5 s of silence): the Honda request
    variants in ``HONDA_CANDIDATES`` below, at their own baud rate, with or
    without the wake-up bytes.

Nothing is sent that is not on the whitelist (``check_iso`` and
``check_honda``): every frame passes through ``_guarded_iso`` or
``_guarded_honda`` first. The report is markdown with a summary table
(interesting hits first) and the full byte trace; a ``.hex.log`` goes next
to it.

To add Honda sequences from research: append a ``HondaCandidate`` to
``HONDA_CANDIDATES``. Only entries with ``read_only=True`` are ever sent.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from ..link.clock import REAL, Clock
from ..protocol import fmt_hex, strip_echo
from ..transport import Transport, TransportError, make_transport
from .trace import TracingTransport


# ---- Honda candidate table (edit here, no code changes needed) -------------

@dataclass(frozen=True)
class HondaCandidate:
    name: str
    baud: int                       # 9600 or 10400 typically
    pre_delay_s: float              # quiet time before this entry
    wake: bytes | None              # wake-up bytes sent first, or None
    request: bytes                  # request bytes (checksum excluded if add_checksum)
    add_checksum: bool = True       # append two's complement of the byte sum
    reply_len: int | None = None    # expected reply length after the echo, if known
    read_only: bool = False         # MUST be True or the entry is refused
    wake_delay_s: float = 0.3       # wait after the wake bytes


WAKE = bytes.fromhex("686af5afbfb3b2c1dbb3e9")

HONDA_CANDIDATES: tuple[HondaCandidate, ...] = (
    # Honda RAM read (cmd 20, len 05, addr 00, count 02) -> expected 00 05+? reply
    HondaCandidate("Honda read 00/2 @9600", 9600, 0.0, None, bytes.fromhex("20050002"),
                   reply_len=5, read_only=True),
    HondaCandidate("Honda read 00/2 @9600 + wake", 9600, 0.1, WAKE, bytes.fromhex("20050002"),
                   reply_len=5, read_only=True),
    HondaCandidate("Honda read 00/2 @10400", 10400, 0.1, None, bytes.fromhex("20050002"),
                   reply_len=5, read_only=True),
    HondaCandidate("Honda read 00/2 @10400 + wake", 10400, 0.1, WAKE, bytes.fromhex("20050002"),
                   reply_len=5, read_only=True),
    # Honda HDS-style K-line (motorcycle/later cars): "72 05 00 F0" = ECU id
    # query / start, "72 05 71 00" = read data table 0. Both are reads.
    HondaCandidate("HDS 72 05 00 F0 @10400", 10400, 0.1, None, bytes.fromhex("720500f0"),
                   read_only=True),
    HondaCandidate("HDS 72 05 71 00 @10400", 10400, 0.06, None, bytes.fromhex("72057100"),
                   read_only=True),
    HondaCandidate("HDS 72 05 00 F0 @9600", 9600, 0.1, None, bytes.fromhex("720500f0"),
                   read_only=True),
)


# ---- whitelist ---------------------------------------------------------------

#: ISO / KWP services the probe may send (all reads)
ISO_ALLOWED = frozenset({0x01, 0x02, 0x03, 0x05, 0x06, 0x07, 0x09, 0x1A, 0x21, 0x22, 0x23})
#: refused outright, named here so the test can check each one
ISO_DENIED = frozenset({0x04, 0x08, 0x11, 0x14, 0x27, 0x2E, 0x2F, 0x30, 0x31,
                        0x34, 0x35, 0x36, 0x37, 0x3B, 0x3D})
DEFAULT_SESSION = 0x81


class ProbeBlocked(Exception):
    """A request that is not on the read-only whitelist."""


def check_iso(payload: bytes) -> None:
    """Raise ProbeBlocked unless ``payload`` (service byte first) is a read."""
    payload = bytes(payload)
    if not payload:
        raise ProbeBlocked("empty request")
    sid = payload[0]
    if sid == 0x10:
        if payload == bytes([0x10, DEFAULT_SESSION]):
            return
        raise ProbeBlocked(f"blocked {fmt_hex(payload)}: 0x10 only with the default session")
    if sid in ISO_DENIED or sid not in ISO_ALLOWED:
        raise ProbeBlocked(f"blocked {fmt_hex(payload)}: service {sid:02X} is not a "
                           "whitelisted read")


def honda_frame(c: HondaCandidate) -> bytes:
    req = bytes(c.request)
    return req + bytes([(-sum(req)) & 0xFF]) if c.add_checksum else req


def check_honda(c: HondaCandidate) -> None:
    if not c.read_only:
        raise ProbeBlocked(f"blocked table entry {c.name!r}: not marked read_only")
    if not c.request:
        raise ProbeBlocked(f"blocked table entry {c.name!r}: empty request")


# ---- results -----------------------------------------------------------------

NRC = {0x10: "generalReject", 0x11: "serviceNotSupported", 0x12: "subFunctionNotSupported",
       0x13: "incorrectMessageLength", 0x21: "busyRepeatRequest", 0x22: "conditionsNotCorrect",
       0x31: "requestOutOfRange", 0x33: "securityAccessDenied", 0x78: "responsePending",
       0x7E: "subFunctionNotSupportedInActiveSession",
       0x80: "serviceNotSupportedInActiveSession"}

MANUFACTURER = frozenset({0x1A, 0x21, 0x22, 0x23})

POSITIVE, OTHER, NEGATIVE, SILENT, BLOCKED, SKIPPED = (
    "positive", "unparsed bytes", "negative", "silent", "blocked", "skipped")


@dataclass
class Exchange:
    stage: str
    service: str
    request: bytes
    kind: str
    raw: bytes = b""                 # what came back after our echo
    replies: list[bytes] = field(default_factory=list)
    nrc: int | None = None
    meaning: str = ""
    t_ms: float = 0.0

    @property
    def rank(self) -> int:
        if self.kind == POSITIVE and (self.stage.startswith("B") or
                                      (self.request[:1] and self.request[0] in MANUFACTURER)):
            return 0
        return {OTHER: 1, POSITIVE: 2, NEGATIVE: 3, SILENT: 4, BLOCKED: 5, SKIPPED: 6}[self.kind]


@dataclass
class ProbeReport:
    port: str
    started: datetime = field(default_factory=datetime.now)
    exchanges: list[Exchange] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    iso_init: str = ""
    elapsed_s: float = 0.0
    trace: str = ""

    def hits(self) -> list[Exchange]:
        return [e for e in self.exchanges if e.kind in (POSITIVE, OTHER)]

    def to_markdown(self) -> str:
        n = {k: sum(e.kind == k for e in self.exchanges)
             for k in (POSITIVE, OTHER, NEGATIVE, SILENT, BLOCKED, SKIPPED)}
        out = ["# hondalog ECU probe", "",
               f"- when: {self.started:%Y-%m-%d %H:%M:%S}",
               f"- port: `{self.port}`",
               f"- ISO 9141-2 init: {self.iso_init or 'failed'}",
               f"- run time: {self.elapsed_s:.1f} s",
               f"- requests: {len(self.exchanges)}; positive {n[POSITIVE]}, "
               f"unparsed bytes {n[OTHER]}, negative (7F) {n[NEGATIVE]}, silent {n[SILENT]}, "
               f"blocked {n[BLOCKED]}, skipped {n[SKIPPED]}",
               "", "Read-only: only whitelisted read services and read-only Honda table "
               "entries were sent.", ""]
        if self.notes:
            out += ["## Notes", "", *[f"- {t}" for t in self.notes], ""]
        out += ["## Summary (interesting hits first)", "",
                "| stage | service | request | reply | meaning |", "|---|---|---|---|---|"]
        for e in sorted(self.exchanges, key=lambda e: e.rank):
            if e.replies:
                reply = " / ".join(fmt_hex(r) for r in e.replies)
            elif e.raw:
                reply = "raw " + fmt_hex(e.raw)
            else:
                reply = "-"
            meaning = (e.meaning or e.kind).replace("|", "\\|")
            out.append(f"| {e.stage} | {e.service} | `{fmt_hex(e.request)}` | `{reply}` "
                       f"| {meaning} |")
        out += ["", "## Byte trace", "",
                "Times in ms from the first event. TX = sent, RX = received (our own echo "
                "included), BREAK = line held low / released, BAUD = speed change.",
                "", "```", self.trace.rstrip(), "```", ""]
        return "\n".join(out)


# ---- decoding ----------------------------------------------------------------

def _ascii(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def _bitmap(base: int, data: bytes) -> str:
    bits = int.from_bytes(data[:4].ljust(4, b"\0"), "big")
    pids = [base + i + 1 for i in range(32) if bits & (1 << (31 - i))]
    return "supported: " + (" ".join(f"{p:02X}" for p in pids) or "none")


def describe(payload: bytes, replies: list[bytes]) -> str:
    sid = payload[0]
    first = replies[0]
    if sid == 0x01 and len(payload) == 2 and payload[1] % 0x20 == 0 and len(first) >= 6:
        return "Mode 01 " + _bitmap(payload[1], first[2:])
    if sid in (0x05, 0x06, 0x09) and len(payload) >= 2 and payload[1] == 0 and len(first) >= 6:
        return f"Mode {sid:02X} " + _bitmap(0, first[-4:])
    if sid == 0x09 and len(payload) == 2 and payload[1] in (0x02, 0x04):
        text = "".join(_ascii(r[3:]) for r in replies)
        return ("VIN " if payload[1] == 0x02 else "CALID ") + f'"{text}"'
    data = b"".join(r[1:] for r in replies)
    text = _ascii(data)
    printable = sum(c != "." for c in text)
    return (f"{len(data)} data bytes" + (f', text "{text}"' if printable >= 4 else ""))


# ---- the probe ---------------------------------------------------------------

ISO_TIMEOUT_S = 0.25          # first-byte wait for standard services
MFR_TIMEOUT_S = 0.20          # first-byte wait for manufacturer services
SILENT_STOP = 4               # consecutive silent replies that end a manufacturer stage
NRC11_STOP = 3                # consecutive serviceNotSupported that end a stage
KEEPALIVE_AFTER_S = 3.0
SESSION_GAP_S = 5.6           # silence that ends the ISO session before stage B


def iso_plan() -> list[tuple[str, list[bytes], bool]]:
    """(stage name, payloads, stop early on silence)"""
    m05 = [b"\x05\x00", b"\x05\x00\x01"]
    m05 += [bytes([0x05, tid, s]) for s in (0x01, 0x02) for tid in range(0x01, 0x0B)]
    m23 = []
    for addr in (0x00, 0x10, 0x20):
        m23 += [bytes([0x23, 0x00, 0x00, addr, 0x04]),        # KWP2000: 3-byte addr, len
                bytes([0x23, 0x00, addr, 0x04]),              # 2-byte addr, len
                bytes([0x23, addr, 0x04]),                    # 1-byte addr, len
                bytes([0x23, 0x12, 0x00, addr, 0x04])]        # UDS ALFID 12: 2-byte addr, 1-byte len
    return [
        ("A1 Mode 01 bitmaps", [b"\x01\x00", b"\x01\x20", b"\x01\x40"], False),
        ("A2 Mode 05 O2 tests", m05, False),
        ("A3 Mode 06", [b"\x06\x00", b"\x06\x01", b"\x06\x02", b"\x06\x03", b"\x06\x05",
                        b"\x06\x21"], False),
        ("A4 Mode 09", [b"\x09\x00", b"\x09\x02", b"\x09\x04"], False),
        ("A5 Mode 02 freeze frame", [b"\x02\x02\x00", b"\x02\x0c\x00"], False),
        ("A6 0x1A read ECU id", [b"\x1a\x80", b"\x1a\x90"], True),
        ("A7 0x21 local id", [bytes([0x21, i]) for i in range(0x10)], True),
        ("A8 0x22 data id", [b"\x22\x00\x00", b"\x22\x01\x00", b"\x22\xf1\x90"], True),
        ("A9 0x23 read memory", m23, True),
    ]


class Prober:
    def __init__(self, tr: TracingTransport, clock: Clock, report: ProbeReport,
                 deadline: float, say: Callable[[str], None]) -> None:
        self.tr = tr
        self.clock = clock
        self.report = report
        self.deadline = deadline
        self.say = say
        self.iso = None
        self.last_ok = 0.0

    # -- ISO stage --

    def _guarded_iso(self, payload: bytes, timeout: float) -> tuple[bytes, list[bytes]]:
        """The only path that puts an ISO frame on the wire."""
        check_iso(payload)
        from ..obd2 import split_9141, split_kwp
        iso = self.iso
        wait = iso._next_tx - self.clock.now()
        if wait > 0:
            self.clock.sleep(wait)
        frame = iso._frame(bytes(payload))
        self.tr.flush_input()
        self.tr.write(frame)
        buf = iso._collect(len(frame), first_timeout=timeout)
        rest, _ = strip_echo(buf, frame)
        replies = split_kwp(rest) if iso.kwp else split_9141(rest)
        # 7F xx 78 = response pending: keep listening up to 1 s
        extra = 0
        while replies and replies[-1][:1] == b"\x7f" and replies[-1][2:3] == b"\x78" and extra < 4:
            extra += 1
            more = iso._collect(0, first_timeout=0.25)
            if not more:
                break
            rest += more
            replies = split_kwp(rest) if iso.kwp else split_9141(rest)
        now = self.clock.now()
        iso._next_tx = now
        if replies:
            iso._last_activity = now
            self.last_ok = now
        return rest, replies

    def _keepalive(self) -> bool:
        if self.clock.now() - self.last_ok < KEEPALIVE_AFTER_S:
            return True
        self.tr.note("keepalive 01 00")
        _, replies = self._guarded_iso(b"\x01\x00", ISO_TIMEOUT_S)
        if any(r[:1] == b"\x41" for r in replies):
            return True
        self.tr.note("keepalive got no reply; re-initialising")
        return self._init()

    def _init(self) -> bool:
        from ..obd2 import Iso9141Error, Iso9141Link
        self.iso = Iso9141Link(self.tr, self.clock, trace=self.tr.note)
        try:
            init = self.iso.slow_init()
        except Iso9141Error as exc:
            self.report.notes.append(f"ISO 9141-2 5-baud init failed: {exc}")
            return False
        self.report.iso_init = self.report.iso_init or str(init)
        self.last_ok = self.clock.now()
        return True

    def stage_a(self) -> None:
        self.say("ISO 9141-2 5-baud init (about 3 s)")
        self.tr.note("stage A: ISO 9141-2 init")
        if not self._init():
            return
        alive = True
        for name, payloads, early in iso_plan():
            self.say(name)
            self.tr.note(name)
            silent = nrc11 = 0
            for i, payload in enumerate(payloads):
                svc = f"{payload[0]:02X}"
                if not alive or self.clock.now() > self.deadline:
                    why = "ISO session lost" if not alive else "time budget"
                    self.report.exchanges.append(Exchange(name, svc, payload, SKIPPED,
                                                          meaning=f"skipped: {why}"))
                    continue
                if early and (silent >= SILENT_STOP or nrc11 >= NRC11_STOP):
                    self.report.notes.append(
                        f"{name}: stopped after {i} requests ("
                        + ("repeated silence" if silent >= SILENT_STOP else
                           "repeated serviceNotSupported") + ")")
                    break
                if not self._keepalive():
                    alive = False
                    self.report.notes.append(f"{name}: ISO session lost, rest skipped")
                    self.report.exchanges.append(Exchange(name, svc, payload, SKIPPED,
                                                          meaning="skipped: ISO session lost"))
                    continue
                self.report.exchanges.append(
                    self._one_iso(name, payload, MFR_TIMEOUT_S if early else ISO_TIMEOUT_S))
                kind = self.report.exchanges[-1]
                silent = silent + 1 if kind.kind == SILENT else 0
                nrc11 = nrc11 + 1 if kind.nrc == 0x11 else 0
        if self.iso is not None:
            self.iso.close()

    def _one_iso(self, stage: str, payload: bytes, timeout: float) -> Exchange:
        svc = f"{payload[0]:02X}"
        t0 = self.clock.now()
        try:
            raw, replies = self._guarded_iso(payload, timeout)
        except ProbeBlocked as exc:
            return Exchange(stage, svc, payload, BLOCKED, meaning=str(exc))
        ex = Exchange(stage, svc, payload, SILENT, raw=raw, replies=replies,
                      t_ms=1000 * (self.clock.now() - t0))
        want = (payload[0] + 0x40) & 0xFF
        good = [r for r in replies if r[:1] == bytes([want])]
        neg = [r for r in replies if r[:1] == b"\x7f" and r[2:3] != b"\x78"]
        if good:
            ex.kind, ex.replies = POSITIVE, good
            ex.meaning = describe(payload, good)
        elif neg:
            ex.kind = NEGATIVE
            ex.nrc = neg[0][2] if len(neg[0]) > 2 else None
            ex.meaning = (f"7F NRC {ex.nrc:02X} {NRC.get(ex.nrc, 'unknown')}"
                          if ex.nrc is not None else "7F (no NRC byte)")
        elif replies or raw:
            ex.kind = OTHER
            ex.meaning = ("valid frame(s), unexpected service byte" if replies
                          else f"{len(raw)} bytes that are not a valid ISO frame")
        else:
            ex.meaning = "no reply"
        return ex

    # -- Honda stage --

    def _guarded_honda(self, c: HondaCandidate) -> bytes:
        """The only path that puts a Honda table entry on the wire."""
        check_honda(c)
        tr, clock = self.tr, self.clock
        if tr.baud != c.baud:
            tr.set_baud(c.baud)
        clock.sleep(c.pre_delay_s)
        if c.wake:
            tr.flush_input()
            tr.write(c.wake)
            clock.sleep(c.wake_delay_s)
        frame = honda_frame(c)
        tr.flush_input()
        tr.write(frame)
        start = clock.now()
        last = start + 10.0 * len(frame) / c.baud
        buf = bytearray()
        while True:
            now = clock.now()
            if now - start > 0.5 or (now > last and now - last > 0.08):
                break
            chunk = tr.read_some(64, 0.02)
            if chunk:
                buf += chunk
                last = clock.now()
        rest, _ = strip_echo(bytes(buf), frame)
        return rest

    def stage_b(self) -> None:
        self.say(f"waiting {SESSION_GAP_S:g} s for the ISO session to time out")
        self.tr.note(f"stage B: {SESSION_GAP_S:g} s silence so the ISO session ends")
        self.clock.sleep(SESSION_GAP_S)
        for c in HONDA_CANDIDATES:
            name = "B Honda"
            if self.clock.now() > self.deadline + 10:
                self.report.exchanges.append(Exchange(name, c.name, honda_frame(c), SKIPPED,
                                                      meaning="skipped: time budget"))
                continue
            self.say(c.name)
            self.tr.note(c.name)
            t0 = self.clock.now()
            try:
                raw = self._guarded_honda(c)
            except ProbeBlocked as exc:
                self.report.exchanges.append(Exchange(name, c.name, honda_frame(c), BLOCKED,
                                                      meaning=str(exc)))
                continue
            ex = Exchange(name, c.name, honda_frame(c), SILENT, raw=raw,
                          t_ms=1000 * (self.clock.now() - t0))
            if raw:
                ok = sum(raw) & 0xFF == 0
                ex.kind = POSITIVE if ok else OTHER
                ex.meaning = (f"{len(raw)} bytes"
                              + (", checksum OK" if ok else ", checksum does not sum to 0")
                              + (f", expected {c.reply_len}" if c.reply_len else ""))
            else:
                ex.meaning = "no reply after the echo"
            self.report.exchanges.append(ex)


def run(port: str, transport: Transport | None = None, clock: Clock = REAL,
        budget_s: float = 85.0, progress: Callable[[str], None] | None = None,
        honda: bool = True) -> ProbeReport:
    """Run both stages. Never raises for link problems."""
    report = ProbeReport(port)

    def say(text: str) -> None:
        if progress is not None:
            progress(text)

    tr = TracingTransport(transport or make_transport(port), clock)
    t0 = clock.now()
    try:
        tr.open()
    except TransportError as exc:
        report.notes.append(f"could not open {port}: {exc}")
        report.trace = tr.dump()
        return report
    # stage A must leave room for the 5.6 s gap and the Honda table
    prober = Prober(tr, clock, report, t0 + budget_s - 15.0, say)
    try:
        prober.stage_a()
        if honda:
            prober.stage_b()
    except TransportError as exc:
        report.notes.append(f"port failed during the probe: {exc}")
    finally:
        try:
            tr.close()
        except TransportError:
            pass
    report.elapsed_s = clock.now() - t0
    report.trace = tr.dump()
    return report


def default_report_path(when: datetime) -> str:
    from ..logger import log_dir
    return os.path.join(str(log_dir()), f"ecuprobe-{when:%Y%m%d-%H%M%S}.md")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="hondalog-probe",
        description="Read-only probe of what the ECU answers on the K-line (ISO 9141-2 "
                    "services and Honda request variants). About 30-60 s. Close the main "
                    "app first.")
    ap.add_argument("--port", default=None,
                    help="serial port (COM21, /dev/ttyUSB0); 'sim:' or 'sim:iso' for the "
                         "simulator (default: the first FTDI adapter, else /dev/hondalog)")
    ap.add_argument("-o", "--out", default=None, help="report file (.md); '-' = stdout")
    ap.add_argument("--no-honda", action="store_true", help="skip stage B (Honda variants)")
    args = ap.parse_args(argv)
    port = args.port
    if port is None:
        from ..transport import list_ports
        ftdis = [p.device for p in list_ports() if p.is_ftdi]
        port = ftdis[0] if ftdis else "/dev/hondalog"

    def progress(text: str) -> None:
        print(f"  .. {text}", file=sys.stderr, flush=True)

    print(f"hondalog ECU probe on {port}", file=sys.stderr)
    t0 = time.monotonic()
    report = run(port, progress=progress, honda=not args.no_honda)
    text = report.to_markdown()
    print(f"\ndone in {time.monotonic() - t0:.1f} s: {len(report.hits())} replies worth a "
          f"look out of {len(report.exchanges)} requests", file=sys.stderr)
    if args.out == "-":
        sys.stdout.write(text)
    else:
        path = args.out or default_report_path(report.started)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        hex_path = os.path.splitext(path)[0] + ".hex.log"
        with open(hex_path, "w", encoding="utf-8") as fh:
            fh.write(f"# hondalog ECU probe, {report.started:%Y-%m-%d %H:%M:%S}, "
                     f"port {report.port}\n# ms from first event, direction, bytes\n"
                     + report.trace)
        print(f"report: {path}\nraw hex log: {hex_path}", file=sys.stderr)
    return 0 if report.iso_init else 1


if __name__ == "__main__":
    sys.exit(main())
