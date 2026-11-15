"""Command line tool for log files: ``hondalog-log`` (or ``python3 -m hondalog.logtool``).

    hondalog-log record  --port PORT [options]   record without the GUI (the app's live path)
    hondalog-log info    FILE                    what is in a log
    hondalog-log import  FILE... [-o DIR]        old CSV / MSL / MLG -> native .hlog
    hondalog-log export  FILE OUT [options]      .mlg .msl .csv .npz .hlog
    hondalog-log repair  FILE...                 finish logs cut off by a crash
    hondalog-log report  FILE [-o OUT.html]      one-page capture report
    hondalog-log redecode FILE OUT [--map TOML]  decode the raw bytes with another map
    hondalog-log list    [DIR] [-q TEXT]         the session library
    hondalog-log prune   [DIR] --days N --mb N [--yes]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import addressmap
from .analysis.filters import FilterSet
from .analysis.report import write_report
from .data import EXTENSION, open_log, repair, write_log
from .data.library import Library
from .data.redecode import redecode
from .data.retention import RetentionPolicy
from .export import FORMATS, export_log
from .logger import log_dir


def _record(args: argparse.Namespace) -> int:
    """Headless recording through ``hondalog.live.LiveSession``: the same
    path the app uses (per-read raw bytes, math channels, alarms, triggers,
    optional black box)."""
    from .live import LiveSession

    amap = addressmap.load(args.map) if args.map else addressmap.load_user_map()
    session = LiveSession(amap, out_dir=args.out)
    poller = session.connect(args.port)
    recorder = None
    try:
        poller.ready.wait(10)
        if poller.ecu_id is None:
            print(f"no reply from the ECU on {args.port} yet ({poller.last_error or 'key on?'}); "
                  "recording anyway", file=sys.stderr)
        if args.bb:
            reader = session.open_blackbox(args.bb)
            reader.ready.wait(4)
            if reader.hello is None:
                print(f"black box on {args.bb}: {reader.last_error or 'no hello'}", file=sys.stderr)
        recorder = session.start_recording()
        print(f"recording to {recorder.path}  (Ctrl-C to stop)")
        end = time.monotonic() + args.seconds if args.seconds else None
        shown = 0.0
        while poller.is_alive() and (end is None or time.monotonic() < end):
            time.sleep(0.1)
            while session.frames:
                frame = session.frames.popleft()
                for event in frame.events:
                    print(f"{frame.t:8.1f} s  {event.marker_text}")
            while not recorder.notices.empty():
                fired = recorder.notices.get_nowait()
                print(f"{fired.t:8.1f} s  TRIGGER {fired.rule.label}: {fired.rule.message}")
            while not recorder.events.empty():
                print(f"            saved {recorder.events.get_nowait().path}")
            if not args.quiet and time.monotonic() - shown >= 2.0:
                shown = time.monotonic()
                print(f"{session.now():8.1f} s  {poller.state:9s} {poller.hz:5.1f} Hz  "
                      f"{recorder.rows} reads, {poller.errors} errors", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
    if recorder is not None:
        while not recorder.events.empty():
            print(f"            saved {recorder.events.get_nowait().path}")
        if recorder.write_error is not None:
            print(f"hondalog-log: the log is incomplete: {recorder.write_error}", file=sys.stderr)
            return 1
        print(f"saved {recorder.path}  ({recorder.rows} reads, {recorder.markers} markers)")
    return 0


def _info(args: argparse.Namespace) -> int:
    with open_log(args.file) as log:
        print(f"{args.file}")
        state = "" if log.indexed else "   (no index: cut off, run 'repair')"
        print(f"  {log.duration:.1f} s, {log.rows} rows, {len(log.events)} events{state}")
        for key, value in log.meta.items():
            if key != "address_map_text":
                text = str(value)
                print(f"  {key}: {text if len(text) < 100 else text[:97] + '...'}")
        print("  streams:")
        for stream in log.streams.values():
            rate = stream.rows / (stream.t1 - stream.t0) if stream.rows > 1 and stream.t1 > stream.t0 else 0
            raw = f", {stream.info.raw_len} raw bytes/row" if stream.info.raw_len else ""
            print(f"    {stream.name:<16} {stream.rows:>8} rows  {rate:7.1f} Hz{raw}  "
                  f"[{', '.join(stream.info.column_names)}]")
        for event in log.events[:args.events]:
            print(f"  {event.t:10.3f} s  {event.kind:<8} {event.text}")
        if len(log.events) > args.events:
            print(f"  ... {len(log.events) - args.events} more events")
    return 0


def _import(args: argparse.Namespace) -> int:
    for name in args.files:
        src = Path(name)
        out = (Path(args.out) if args.out else src.parent) / (src.stem + EXTENSION)
        if out.exists() and not args.force:
            print(f"skip {src}: {out} exists (use --force)")
            continue
        with open_log(src) as log:
            write_log(log, out, {"imported_from": str(src)})
            print(f"{src} -> {out}  ({log.rows} rows, {len(log.events)} events)")
    return 0


def _export(args: argparse.Namespace) -> int:
    filters = None
    if args.filter:
        filters = FilterSet()
        for i, text in enumerate(args.filter):
            filters.add(f"f{i}", text)
    with open_log(args.file) as log:
        out = export_log(log, args.out, channels=args.channels.split(",") if args.channels else None,
                         hz=args.hz, filters=filters, t0=args.start, t1=args.end,
                         mlg_version=args.mlg_version, compact=args.compact)
    print(f"wrote {out}  ({out.stat().st_size} bytes)")
    return 0


def _repair(args: argparse.Namespace) -> int:
    for name in args.files:
        info = repair(name)
        what = "already complete" if info["was_indexed"] else f"index written, {info['junk_bytes']} bytes dropped"
        print(f"{name}: {info['rows']} rows, {what}")
    return 0


def _report(args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else Path(args.file).with_suffix(".html")
    with open_log(args.file) as log:
        write_report(log, out)
    print(f"wrote {out}")
    return 0


def _redecode(args: argparse.Namespace) -> int:
    amap = addressmap.load(args.map) if args.map else addressmap.load_user_map()
    with open_log(args.file) as log:
        new = redecode(log, amap)
        if not new.meta["redecoded_streams"]:
            print("this log has no raw bytes (it was recorded per sweep); nothing to decode again")
            return 1
        write_log(new, args.out)
    print(f"wrote {args.out} with map {amap.version!r}")
    return 0


def _list(args: argparse.Namespace) -> int:
    library = Library(args.dir or log_dir())
    library.refresh()
    for entry in library.search(args.query or ""):
        extra = entry.error or entry.trigger or entry.notes
        print(f"{entry.name:<36} {entry.duration:8.1f} s  {entry.rows:>8} rows  "
              f"{len(entry.markers):>3} marks  {extra}")
    return 0


def _prune(args: argparse.Namespace) -> int:
    policy = RetentionPolicy(args.days, args.mb, args.files, keep_captures=not args.captures)
    folder = args.dir or log_dir()
    if not policy.active:
        print("nothing to do: give --days, --mb or --files")
        return 1
    doomed = policy.plan(folder)
    for path in doomed:
        print(("delete " if args.yes else "would delete ") + str(path))
    if args.yes:
        policy.apply(folder)
    elif doomed:
        print("dry run; add --yes to delete")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hondalog-log", description="hondalog log file tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("record", help="record from the ECU without the GUI")
    p.add_argument("--port", required=True, help="serial device, or sim: for the built-in simulator")
    p.add_argument("--bb", help="black box port (bbsim: for the built-in simulator)")
    p.add_argument("--seconds", type=float, help="stop after this long (default: until Ctrl-C)")
    p.add_argument("--map", help="address map TOML (default: your map)")
    p.add_argument("-o", "--out", help="folder for the log and captures (default: ~/hondalog/logs)")
    p.add_argument("-q", "--quiet", action="store_true", help="no progress lines")
    p.set_defaults(fn=_record)

    p = sub.add_parser("info", help="show what is in a log")
    p.add_argument("file")
    p.add_argument("--events", type=int, default=20)
    p.set_defaults(fn=_info)

    p = sub.add_parser("import", help="convert CSV/MSL/MLG logs to native .hlog")
    p.add_argument("files", nargs="+")
    p.add_argument("-o", "--out", help="output folder (default: next to the source)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=_import)

    p = sub.add_parser("export", help="write another format: " + " ".join(FORMATS))
    p.add_argument("file")
    p.add_argument("out")
    p.add_argument("--channels", help="comma-separated; default all")
    p.add_argument("--hz", type=float, help="resample to a fixed rate")
    p.add_argument("--filter", action="append", help="keep rows where this is true (repeatable)")
    p.add_argument("--start", type=float)
    p.add_argument("--end", type=float)
    p.add_argument("--mlg-version", type=int, default=2, choices=(1, 2))
    p.add_argument("--compact", action="store_true", help="MLG: 16-bit scaled fields where they fit")
    p.set_defaults(fn=_export)

    p = sub.add_parser("repair", help="finish logs that were cut off")
    p.add_argument("files", nargs="+")
    p.set_defaults(fn=_repair)

    p = sub.add_parser("report", help="one-page HTML report")
    p.add_argument("file")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=_report)

    p = sub.add_parser("redecode", help="decode the raw bytes with another address map")
    p.add_argument("file")
    p.add_argument("out")
    p.add_argument("--map", help="address map TOML (default: your map)")
    p.set_defaults(fn=_redecode)

    p = sub.add_parser("list", help="list logs and captures")
    p.add_argument("dir", nargs="?")
    p.add_argument("-q", "--query")
    p.set_defaults(fn=_list)

    p = sub.add_parser("prune", help="delete old session logs")
    p.add_argument("dir", nargs="?")
    p.add_argument("--days", type=float)
    p.add_argument("--mb", type=float)
    p.add_argument("--files", type=int)
    p.add_argument("--captures", action="store_true", help="captures may be deleted too")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=_prune)

    args = parser.parse_args(argv)
    try:
        return int(args.fn(args))
    except (OSError, ValueError, KeyError) as exc:
        print(f"hondalog-log: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
