"""Command line for the black box: ``hondalog-bb`` / ``python -m hondalog.blackbox``."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def _connect(port: str):
    from .reader import BlackboxReader
    reader = BlackboxReader(port)
    reader.start()
    reader.ready.wait(4)
    if reader.hello is None:
        reader.stop()
        raise SystemExit(f"no black box on {port}: {reader.last_error or 'no answer'}")
    return reader


def cmd_info(args) -> int:
    from .diagnose import diagnose
    from .eventfile import read_event
    for path in args.files:
        ev = read_event(path)
        print(f"{path}: event {ev.event_no}, {ev.reason_name} ({ev.reason_text})")
        print(f"  box {ev.device} fw {ev.fw}; {len(ev.t_us)} ms of data, {len(ev.edge_t_us)} edges, "
              f"{'complete' if ev.complete else 'INCOMPLETE'}")
        if ev.trigger_wall:
            print(f"  trigger at {ev.trigger_wall.isoformat(timespec='milliseconds')}")
        for r in ev.events:
            print(f"  {(r.t_us - ev.trigger_t_us) / 1000:+10.1f} ms  {r.name:<12} value {r.value}")
        for w in ev.warnings:
            print(f"  warning: {w}")
        print()
        print(diagnose(ev).text())
    return 0


def cmd_csv(args) -> int:
    from .eventfile import export_csv, read_event
    out = Path(args.out) if args.out else Path(args.file).with_suffix(".csv")
    print(export_csv(read_event(args.file), out))
    return 0


def cmd_live(args) -> int:
    reader = _connect(args.port)
    print(reader.device_text)
    try:
        end = None if args.seconds is None else time.monotonic() + args.seconds
        while end is None or time.monotonic() < end:
            time.sleep(0.25)
            while not reader.events.empty():
                ev = reader.events.get_nowait()
                print(f"\n[{ev.name}] {ev.text}" + (f" -> event file {ev.event_no}" if ev.started_file else ""))
            if reader.samples and reader.samples[-1].link:
                v = reader.samples[-1].values
                print(f"\rbatt {v['bb_vbat']:5.2f}  ecu+B {v['bb_ecub']:5.2f}  pump {v['bb_pump']:5.2f}  "
                      f"ig1 {v['bb_ig1']:5.2f}  rpm {v['bb_rpm_tach']:5.0f}  inj {v['bb_inj_pw']:5.2f} ms  "
                      f"{reader.hz:4.0f} Hz ", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        reader.stop()
        print()
    return 0


def cmd_list(args) -> int:
    reader = _connect(args.port)
    try:
        for info in reader.list_events():
            print(f"event {info.event_no:6d}  {info.size:8d} bytes  {info.reason_name:<12} "
                  f"{'' if info.complete else 'incomplete'}")
    finally:
        reader.stop()
    return 0


def cmd_fetch(args) -> int:
    from ..logger import log_dir
    reader = _connect(args.port)
    try:
        time.sleep(0.5)             # let the clock exchange settle first
        numbers = args.numbers or [i.event_no for i in reader.list_events()]
        for no in numbers:
            print(reader.fetch_event(no, args.dest or log_dir()))
    finally:
        reader.stop()
    return 0


def cmd_config(args) -> int:
    reader = _connect(args.port)
    try:
        for item in args.set or []:
            key, sep, value = item.partition("=")
            if not sep:
                raise SystemExit(f"--set wants key=value, got {item!r}")
            reader.set_config(key.strip(), value.strip())
        if args.save:
            reader.save_config()
        print(reader.get_config(), end="")
    finally:
        reader.stop()
    return 0


def cmd_selftest(args) -> int:
    reader = _connect(args.port)
    try:
        text = reader.selftest()
        print(text, end="")
        return 0 if "selftest: PASS" in text else 1
    finally:
        reader.stop()


def cmd_cal(args) -> int:
    """Two-point calibration: scale and offset from two meter readings."""
    (m1, r1), (m2, r2) = (args.low, args.high)
    if abs(r2 - r1) < 0.5:
        raise SystemExit("the two points are too close together; use about 8 V and 15 V")
    gain = (m2 - m1) / (r2 - r1)
    scale = round(args.scale * gain)
    offset = round((m1 - gain * r1) * 1000 + gain * args.offset)
    print(f"{args.rail}_scale = {scale}\n{args.rail}_offset_mv = {offset}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hondalog-bb", description="hondalog black box tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    port = dict(default="/dev/hondalog-bb", help="serial port, or bbsim: (default /dev/hondalog-bb)")

    p = sub.add_parser("info", help="show a capture: events, order, verdict")
    p.add_argument("files", nargs="+")
    p.set_defaults(fn=cmd_info)
    p = sub.add_parser("csv", help="export a capture to CSV")
    p.add_argument("file")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_csv)
    p = sub.add_parser("live", help="print live values")
    p.add_argument("--port", **port)
    p.add_argument("--seconds", type=float)
    p.set_defaults(fn=cmd_live)
    p = sub.add_parser("list", help="list captures on the SD card")
    p.add_argument("--port", **port)
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("fetch", help="download captures over USB")
    p.add_argument("numbers", nargs="*", type=int, help="event numbers (default: all listed)")
    p.add_argument("--port", **port)
    p.add_argument("--dest", help="folder (default: the hondalog log folder)")
    p.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("config", help="show or change the box's settings")
    p.add_argument("--port", **port)
    p.add_argument("--set", action="append", metavar="KEY=VALUE")
    p.add_argument("--save", action="store_true", help="write the settings to the SD card")
    p.set_defaults(fn=cmd_config)
    p = sub.add_parser("selftest", help="run the box's self-test")
    p.add_argument("--port", **port)
    p.set_defaults(fn=cmd_selftest)
    p = sub.add_parser("cal", help="work out scale/offset from two meter readings")
    p.add_argument("rail", choices=("vbat", "ecub", "pump", "ig1", "alt"))
    p.add_argument("--low", nargs=2, type=float, required=True, metavar=("METER_V", "BOX_V"))
    p.add_argument("--high", nargs=2, type=float, required=True, metavar=("METER_V", "BOX_V"))
    p.add_argument("--scale", type=int, default=7912, help="scale currently in the box")
    p.add_argument("--offset", type=int, default=0, help="offset_mv currently in the box")
    p.set_defaults(fn=cmd_cal)
    p = sub.add_parser("sim", help="run the simulator on a pty (same as hondalog-bbsim)")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(fn=lambda a: __import__("hondalog.blackbox.sim", fromlist=["main"]).main(a.rest))

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
