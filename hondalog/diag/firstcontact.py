"""First contact with the car: which protocol answers, and how well.

One call (``run``) or one command (``hondalog-firstcontact``) does, in order:

 1. adapter check: FTDI genuine or clone, latency timer (set to 1 ms), udev rule
 2. open the port
 3. echo test: one byte out, expect it back within 20 ms (+ USB latency).
    No echo at all means no 12 V at the OBD2 plug or a broken cable.
 4. passive listen for 2 s: is anything else talking on the K-line?
 5. Honda read of 0x00 without the wake-up bytes, then with them
 6. if no answer: the same read parsed with alternate reply framings
 7. if Honda answers: ECU id, block-length probe, timing probe, and a
    0x00-0xFF sweep
 8. ISO 9141-2 5-baud init at 0x33 (fast init as fallback), then readiness,
    stored and pending codes, freeze frame and the supported Mode 01 PIDs

Everything sent is a read request, the wake-up bytes, an init, or an OBD2
read service: nothing writes, resets or clears. The report (markdown) has a
plain verdict, every finding, and the full byte trace with timestamps.

Verdicts: ``Honda protocol OK``, ``ISO only``, ``cable dead``, ``ECU silent``,
``no port``.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime

from .. import protocol
from ..addressmap import AddressMap, load_default
from ..link.clock import REAL, Clock
from ..protocol import Framing, ProtocolError, fmt_hex
from ..transport import Link, Transport, TransportError, make_transport
from . import ftdi
from .probe import BlockLengthProbe, Sweep, TimingProbe, probe_block_length, probe_timing, sweep
from .trace import TracingTransport

HONDA_OK = "Honda protocol OK"
ISO_ONLY = "ISO only"
CABLE_DEAD = "cable dead"
ECU_SILENT = "ECU silent"
NO_PORT = "no port"

NEXT_STEPS: dict[str, tuple[str, ...]] = {
    HONDA_OK: (
        "Copy the suggested `[protocol]` settings below into ~/.config/hondalog/addressmap.toml.",
        "Start hondalog and connect. Check RPM, TPS and battery against reality first.",
        "Use the RAM sweep below and Raw tools > Memory scanner to verify the address map.",
    ),
    ISO_ONLY: (
        "Use the OBD2 functions (codes, freeze frame, readiness, PIDs); they work now.",
        "For fast logging try the Honda protocol on the 3-pin service connector instead "
        "of OBD2 pin 7, and run this again.",
        "Send this report and the .hex.log along if you ask for help.",
    ),
    CABLE_DEAD: (
        "Check the plug is fully seated and the car battery is connected.",
        "Measure 12 V between OBD2 pin 16 and pin 4/5 (fuse if missing).",
        "Try the cable on another car or another cable on this car.",
    ),
    ECU_SILENT: (
        "Key to ON (II), not ACC. Try again with the engine running.",
        "Check that the cable's K-line is on OBD2 pin 7 and that pin 7 is populated in the car's plug.",
        "Look at the byte trace: any RX bytes after the echo are a reply in an unknown "
        "format; send the .hex.log along if you ask for help.",
    ),
    NO_PORT: (
        "lsusb | grep 0403 and sudo dmesg | tail: does Linux see the cable?",
        "groups: are you in dialout? (sudo usermod -aG dialout $USER, then log out and in)",
        "If dmesg mentions brltty: sudo apt remove brltty, then replug.",
    ),
}

ECHO_BYTE = 0x55               # not a Honda command byte; an ECU ignores it
ECHO_WAIT_S = 0.020

#: reply framings tried when the default gets no valid reply
ALT_FRAMINGS: tuple[tuple[str, dict[str, int]], ...] = (
    ("any reply header byte", {"reply_header": -1}),
    ("reply length byte = data length", {"reply_header": -1, "reply_len_extra": 0}),
    ("reply length byte = data length + 2", {"reply_header": -1, "reply_len_extra": 2}),
    ("reply length byte = data length + 4", {"reply_header": -1, "reply_len_extra": 4}),
)


@dataclass
class Step:
    name: str
    ok: bool | None             # None = skipped / not applicable
    detail: str = ""
    t_ms: float = 0.0           # how long it took


@dataclass
class FirstContactReport:
    port: str
    started: datetime = field(default_factory=datetime.now)
    verdict: str = ECU_SILENT
    summary: str = ""
    steps: list[Step] = field(default_factory=list)
    adapter: ftdi.AdapterInfo | None = None
    echo: bool = False
    echo_ms: float | None = None
    bus_chatter: bytes = b""
    honda_no_init: bool = False
    honda_with_init: bool = False
    honda_reply: bytes = b""
    honda_framing: str = ""
    raw_reply: bytes = b""               # what came back to the default request
    ecu_id: bytes = b""
    block_probe: BlockLengthProbe | None = None
    timing: TimingProbe | None = None
    sweep: Sweep | None = None
    iso_init: str = ""
    iso_ok: bool = False
    obd2_text: str = ""
    recommended: dict[str, object] = field(default_factory=dict)
    trace: str = ""

    def step(self, name: str, ok: bool | None, detail: str = "", t_ms: float = 0.0) -> Step:
        s = Step(name, ok, detail, t_ms)
        self.steps.append(s)
        return s

    # -- output --

    def to_markdown(self) -> str:
        out: list[str] = [
            "# hondalog first contact",
            "",
            f"- when: {self.started:%Y-%m-%d %H:%M:%S}",
            f"- port: `{self.port}`",
            f"- **verdict: {self.verdict}**",
            "",
            self.summary,
            "",
            "## Next steps",
            "",
            *[f"{i}. {text}" for i, text in enumerate(NEXT_STEPS.get(self.verdict, ()), 1)],
            "",
            "## Steps",
            "",
            "| # | step | result | detail |",
            "|---|---|---|---|",
        ]
        for i, s in enumerate(self.steps, 1):
            mark = "skipped" if s.ok is None else ("OK" if s.ok else "FAIL")
            detail = s.detail.replace("|", "\\|").replace("\n", " ")
            out.append(f"| {i} | {s.name} | {mark} | {detail} |")
        if self.adapter is not None:
            a = self.adapter
            out += ["", "## Adapter", "",
                    f"- kind: {a.kind}, driver: {a.driver or '-'}, USB id: {a.usb_id}",
                    f"- chip: {a.chip or '-'}, manufacturer: {a.manufacturer or '-'}, "
                    f"product: {a.product or '-'}, serial: {a.serial or '-'}",
                    f"- verdict: {a.verdict}",
                    f"- latency timer: {a.latency_ms if a.latency_ms is not None else '-'} ms"]
            out += [f"- {n}" for n in a.notes]
            if a.is_ftdi and (a.latency_ms or 0) > 1:
                out += ["", "Make the 1 ms latency timer permanent:", "", "```sh",
                        ftdi.udev_install_hint(), "```"]
        if self.ecu_id:
            text = "".join(chr(b) if 32 <= b < 127 else "." for b in self.ecu_id)
            out += ["", "## ECU id", "", f"`{fmt_hex(self.ecu_id)}`  \"{text}\""]
        if self.raw_reply and not self.honda_reply:
            out += ["", "## Raw reply to the default read request", "",
                    f"`{fmt_hex(self.raw_reply)}`"]
        if self.block_probe is not None:
            out += ["", "## Block length", "", self.block_probe.summary()]
        if self.timing is not None:
            out += ["", "## Link timing", "", self.timing.summary()]
            out += [f"- {n}" for n in self.timing.notes]
            if self.timing.gap_errors:
                out.append("- errors per inter-request gap: " + ", ".join(
                    f"{1000 * g:g} ms: {e}" for g, e in self.timing.gap_errors.items()))
        if self.recommended:
            out += ["", "## Suggested `[protocol]` settings for the address map", "",
                    "```toml", "[protocol]"]
            for key, value in self.recommended.items():
                out.append(f"{key} = {value!r}" if not isinstance(value, str)
                           else f'{key} = "{value}"')
            out.append("```")
        if self.sweep is not None:
            out += ["", "## RAM sweep 0x00-0xFF", "",
                    f"{self.sweep.answered} of 256 addresses answered.", "", "```",
                    self.sweep.hexdump(), "```"]
            runs = self.sweep.ascii_runs()
            if runs:
                out += ["", "Readable text (ECU id or part number?): "
                        + ", ".join(f"{a:02X}: \"{t}\"" for a, t in runs)]
        if self.iso_init or self.obd2_text:
            out += ["", "## ISO 9141-2", "", f"init: {self.iso_init or 'failed'}", ""]
            if self.obd2_text:
                out += ["```", self.obd2_text.rstrip(), "```"]
        out += ["", "## Byte trace", "",
                "Times in ms from the first event. TX = sent, RX = received (our own "
                "echo included), BREAK = line held low / released, BAUD = speed change.",
                "", "```", self.trace.rstrip(), "```", ""]
        return "\n".join(out)


def _ms(t0: float, clock: Clock) -> float:
    return 1000 * (clock.now() - t0)


def echo_test(transport: Transport, clock: Clock = REAL,
              wait_s: float = ECHO_WAIT_S) -> tuple[bool, float | None]:
    """Send one byte; True if it comes back. Returns (echo, ms until it did)."""
    transport.flush_input()
    t0 = clock.now()
    transport.write(bytes([ECHO_BYTE]))
    deadline = t0 + 10.0 / max(1, transport.baud) + wait_s + 2 * transport.usb_latency_s
    got = bytearray()
    while clock.now() < deadline:
        chunk = transport.read_some(16, deadline - clock.now())
        if chunk:
            got += chunk
            if ECHO_BYTE in got:
                return True, 1000 * (clock.now() - t0)
    return False, None


def listen(transport: Transport, seconds: float, clock: Clock = REAL) -> bytes:
    """Record whatever is on the line without sending anything."""
    end = clock.now() + seconds
    got = bytearray()
    while clock.now() < end:
        got += transport.read_some(256, end - clock.now())
    return bytes(got)


def _try_read(link: Link, addr: int, length: int, tries: int = 2) -> bytes | None:
    for _ in range(tries):
        try:
            return link.read_block(addr, length, retries=0).data
        except ProtocolError:
            continue
    return None


def run(port: str, amap: AddressMap | None = None, transport: Transport | None = None,
        clock: Clock = REAL, iso: bool = True, quick: bool = False,
        listen_s: float = 2.0, progress: Callable[[str], None] | None = None,
        sysfs: str = "/sys") -> FirstContactReport:
    """Run the whole first-contact sequence. Never raises for link problems;
    everything ends up in the report."""
    amap = amap or load_default()
    framing = amap.framing
    report = FirstContactReport(port)

    def say(text: str) -> None:
        if progress is not None:
            progress(text)

    # 1. adapter
    say("checking the adapter")
    t = clock.now()
    report.adapter = ftdi.detect(port, sysfs)
    a = report.adapter
    report.step("adapter", a.kind in ("usb-serial", "pty", "simulator"),
                f"{a.chip or a.kind}, {a.verdict}", _ms(t, clock))

    inner = transport or make_transport(port, framing)
    tr = TracingTransport(inner, clock)

    # 2. open
    say("opening the port")
    t = clock.now()
    try:
        tr.open()
    except TransportError as exc:
        report.step("open port", False, str(exc), _ms(t, clock))
        report.verdict = NO_PORT
        report.summary = (f"Could not open {port}: {exc}. Is the cable plugged in, and is "
                          "your user in the dialout group?")
        report.trace = tr.dump()
        return report
    lat = 1000 * tr.usb_latency_s
    if a.is_ftdi:
        a.latency_ms = ftdi.read_latency_ms(port, sysfs)
        lat_ok = a.latency_ms is not None and a.latency_ms <= 1
        report.step("latency timer 1 ms", lat_ok,
                    f"latency_timer = {a.latency_ms} ms" + ("" if lat_ok else
                                                            "; see the udev rule below"))
    report.step("open port", True, f"USB latency {lat:g} ms", _ms(t, clock))

    try:
        _run_open(report, tr, amap, framing, clock, iso, quick, listen_s, say)
    except TransportError as exc:
        report.step("port", False, f"port failed during the test: {exc}")
        report.verdict = NO_PORT
        report.summary = f"The port stopped working during the test ({exc}). Cable pulled?"
    finally:
        try:
            tr.close()
        except TransportError:
            pass
    report.trace = tr.dump()
    return report


def _run_open(report: FirstContactReport, tr: TracingTransport, amap: AddressMap,
              framing: Framing, clock: Clock, iso: bool, quick: bool, listen_s: float,
              say: Callable[[str], None]) -> None:
    # 3. echo
    say("echo test")
    tr.note("echo test")
    report.echo, report.echo_ms = echo_test(tr, clock)
    report.step("echo test", report.echo,
                f"echo after {report.echo_ms:.1f} ms" if report.echo_ms is not None
                else "no echo: no 12 V at the OBD2 plug, or a broken cable")

    # 4. passive listen
    if listen_s > 0:
        say(f"listening for {listen_s:g} s")
        tr.note("passive listen")
        clock.sleep(0.05)
        tr.flush_input()
        report.bus_chatter = listen(tr, listen_s, clock)
        quiet = not report.bus_chatter
        report.step("passive listen", quiet,
                    f"line quiet for {listen_s:g} s" if quiet else
                    f"{len(report.bus_chatter)} bytes heard: something else is talking "
                    "(another tool on the line?)")

    # 5. Honda read without, then with, the wake-up bytes
    say("Honda read of 0x00")
    tr.note("Honda read 0x00, no wake-up bytes")
    plain = replace(framing, adaptive_timing=False)
    link = Link(tr, plain, clock)
    data = _try_read(link, 0x00, 2)
    report.honda_no_init = data is not None
    report.step("Honda read 0x00 (no wake-up)", report.honda_no_init,
                fmt_hex(data) if data is not None else "no valid reply")
    if data is None and framing.init_sequence:
        tr.note("wake-up bytes, then Honda read 0x00")
        link.wake()
        data = _try_read(link, 0x00, 2)
        report.honda_with_init = data is not None
        report.step("Honda read 0x00 (after wake-up)", report.honda_with_init,
                    fmt_hex(data) if data is not None else "no valid reply")
    if data is not None:
        report.honda_reply = data
        report.honda_framing = "default"

    # 6. alternate framings
    if data is None and report.echo:
        say("trying alternate reply framings")
        request = protocol.build_read_request(0x00, 2, framing)
        tr.note("default read, raw capture")
        _, report.raw_reply = link.raw(request, quiet_s=0.08, max_s=0.5)
        found = ""
        for label, overrides in ALT_FRAMINGS:
            alt = replace(plain, **overrides)       # type: ignore[arg-type]
            rest = report.raw_reply
            hit = protocol.find_reply(rest, 2, alt) if rest else None
            if hit is not None:
                found = label
                report.honda_reply = hit
                report.honda_framing = label
                report.recommended.update(overrides)
                break
        report.step("alternate framings", bool(found) if report.raw_reply else False,
                    (f"valid with: {found}" if found else
                     (f"raw reply {fmt_hex(report.raw_reply)} fits no framing tried"
                      if report.raw_reply else "nothing came back to parse")))

    honda = bool(report.honda_reply)
    if honda and report.honda_framing == "default":
        _honda_details(report, link, amap, framing, clock, quick, say)

    # 8. ISO 9141-2
    if iso and report.echo:
        _iso(report, tr, amap, clock, say)
    elif iso:
        report.step("ISO 9141-2 init", None, "skipped: no echo, the line is dead")

    _verdict(report)


def _honda_details(report: FirstContactReport, link: Link, amap: AddressMap,
                   framing: Framing, clock: Clock, quick: bool,
                   say: Callable[[str], None]) -> None:
    tr = link.transport
    note = tr.note if isinstance(tr, TracingTransport) else (lambda text: None)
    adaptive = Link(tr, framing, clock)
    adaptive.echo_seen = link.echo_seen
    # ECU id
    if amap.ecu_id_addr is not None:
        say("reading the ECU id")
        note("ECU id")
        ident = _try_read(adaptive, amap.ecu_id_addr, amap.ecu_id_len)
        report.ecu_id = ident or b""
        report.step("ECU id", ident is not None,
                    fmt_hex(ident) if ident else f"no reply at {amap.ecu_id_addr:02X}")
    # block length
    say("probing the longest block read")
    note("block-length probe")
    report.block_probe = probe_block_length(adaptive)
    report.step("block-length probe", report.block_probe.max_read_len > 0,
                report.block_probe.summary())
    if report.block_probe.max_read_len and report.block_probe.max_read_len != framing.max_read_len:
        report.recommended["max_read_len"] = report.block_probe.max_read_len
    if quick:
        report.step("timing probe", None, "skipped (--quick)")
        report.step("RAM sweep", None, "skipped (--quick)")
        return
    # timing
    say("measuring ECU turnaround (200 reads)")
    note("timing probe")
    report.timing = probe_timing(adaptive)
    report.step("timing probe", report.timing.ok, report.timing.summary())
    for key in ("reply_timeout_s", "inter_request_delay_s"):
        if key in report.timing.recommended:
            report.recommended[key] = report.timing.recommended[key]
    # sweep
    say("sweeping 0x00-0xFF")
    note("RAM sweep 0x00-0xFF")
    chunk = min(16, report.block_probe.max_read_len or 1)
    report.sweep = sweep(adaptive, chunk=chunk)
    report.step("RAM sweep", report.sweep.answered > 0,
                f"{report.sweep.answered} of 256 addresses answered")


def _iso(report: FirstContactReport, tr: TracingTransport, amap: AddressMap, clock: Clock,
         say: Callable[[str], None]) -> None:
    from ..obd2 import Iso9141Error, Iso9141Link, Obd2Session
    say("ISO 9141-2 5-baud init (about 3 s)")
    tr.note("ISO 9141-2 init")
    iso = Iso9141Link(tr, clock, honda_baud=amap.framing.baud, trace=tr.note)
    try:
        init = iso.connect(allow_fast=True)
    except Iso9141Error as exc:
        report.step("ISO 9141-2 init", False, str(exc))
        iso.close()
        return
    report.iso_ok = True
    report.iso_init = str(init)
    report.step("ISO 9141-2 init", True, f"{init}, sync after {1000 * init.w1_s:.0f} ms")
    say("reading codes, freeze frame and PIDs")
    try:
        snap = Obd2Session(iso).snapshot(live=True)
        report.obd2_text = snap.to_text()
        codes = ", ".join(d.code for d in snap.stored) or "none"
        report.step("OBD2 snapshot", not snap.errors,
                    f"stored codes: {codes}; {len(snap.supported_pids)} PIDs supported"
                    + (f"; {len(snap.errors)} errors" if snap.errors else ""))
    finally:
        iso.close()


def _verdict(report: FirstContactReport) -> None:
    if report.honda_reply:
        report.verdict = HONDA_OK
        how = ("with the default framing" if report.honda_framing == "default"
               else f"with an alternate framing ({report.honda_framing})")
        init = "without" if report.honda_no_init else "only after"
        report.summary = (f"The ECU answers Honda RAM reads on this pin {how}, {init} the "
                          "wake-up bytes. hondalog can log at full speed.")
        if report.iso_ok:
            report.summary += " ISO 9141-2 works too, for trouble codes and readiness."
    elif report.iso_ok:
        report.verdict = ISO_ONLY
        report.summary = ("The ECU answers standard OBD2 (ISO 9141-2) but not Honda RAM reads "
                          "on this pin. Codes, freeze frame and readiness work; live data is "
                          "limited to about 4-8 PIDs per second. Honda reads may need the "
                          "3-pin service connector instead of OBD2 pin 7.")
    elif not report.echo:
        report.verdict = CABLE_DEAD
        report.summary = ("Nothing came back, not even our own transmission. The K-line has no "
                          "pull-up: check 12 V on OBD2 pin 16 (fuse), ground on pins 4/5, and "
                          "the cable.")
    else:
        report.verdict = ECU_SILENT
        report.summary = ("The cable works (echo OK) but the ECU answered neither the Honda "
                          "protocol nor ISO 9141-2. Is the key in ON (II)? Is the cable on "
                          "pin 7?")


def default_report_path(when: datetime | None = None) -> str:
    from ..logger import log_dir
    when = when or datetime.now()
    return os.path.join(str(log_dir()), f"firstcontact-{when:%Y%m%d-%H%M%S}.md")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="hondalog-firstcontact",
        description="Find out which protocol the ECU answers and write a report. Read-only: "
                    "sends read requests and OBD2 read services, never writes or clears.")
    ap.add_argument("--port", default=None,
                    help="serial port (default: the first FTDI adapter, else /dev/hondalog); "
                         "'sim:' for the built-in simulator")
    ap.add_argument("-o", "--out", default=None, help="report file (.md); '-' = stdout")
    ap.add_argument("--no-iso", action="store_true", help="skip the ISO 9141-2 init")
    ap.add_argument("--quick", action="store_true", help="skip the timing probe and RAM sweep")
    ap.add_argument("--listen", type=float, default=2.0, metavar="SEC",
                    help="passive listen time (default 2)")
    ap.add_argument("--map", default=None, help="address map TOML (default: the built-in one)")
    ap.add_argument("--udev", action="store_true",
                    help="print the udev rule (stable name + 1 ms latency) and exit")
    args = ap.parse_args(argv)

    if args.udev:
        print(ftdi.udev_rule(), end="")
        print("\n# install with:\n" + "\n".join("#   " + line for line in
                                                ftdi.udev_install_hint().splitlines()))
        return 0
    from ..addressmap import load
    from ..transport import list_ports
    amap = load(args.map) if args.map else load_default()
    port = args.port
    if port is None:
        ftdis = [p.device for p in list_ports() if p.is_ftdi]
        port = ftdis[0] if ftdis else "/dev/hondalog"

    def progress(text: str) -> None:
        print(f"  .. {text}", file=sys.stderr, flush=True)

    print(f"hondalog first contact on {port}", file=sys.stderr)
    t0 = time.monotonic()
    report = run(port, amap, iso=not args.no_iso, quick=args.quick, listen_s=args.listen,
                 progress=progress)
    text = report.to_markdown()
    print(f"\nverdict: {report.verdict}  ({time.monotonic() - t0:.1f} s)\n{report.summary}",
          file=sys.stderr)
    if args.out == "-":
        sys.stdout.write(text)
    else:
        path = args.out or default_report_path(report.started)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        hex_path = os.path.splitext(path)[0] + ".hex.log"
        with open(hex_path, "w", encoding="utf-8") as fh:
            fh.write(f"# hondalog first contact, {report.started:%Y-%m-%d %H:%M:%S}, "
                     f"port {report.port}, verdict: {report.verdict}\n"
                     "# ms from first event, direction, bytes\n" + report.trace)
        print(f"report: {path}\nraw hex log: {hex_path}", file=sys.stderr)
    return 0 if report.verdict in (HONDA_OK, ISO_ONLY) else 1


if __name__ == "__main__":
    sys.exit(main())
