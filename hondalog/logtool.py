"""Command line tool for the drive database: ``hondalog-log`` (or ``python3 -m hondalog.logtool``).

    hondalog-log record  --port PORT [options]   record without the GUI (the app's live path)
    hondalog-log list    [-q TEXT]               drives and captures, newest first
    hondalog-log info    [DRIVE]                 what is in a drive (default: the newest)
    hondalog-log export  DRIVE OUT [options]     .mlg .msl .csv .npz, or .db (a copy to hand on)
    hondalog-log compare A B [options]           two drives, channel by channel
    hondalog-log import  FILE...                 old CSV / MSL / MLG -> new drives
    hondalog-log report  DRIVE [-o OUT.html]     one-page capture report
    hondalog-log redecode DRIVE [--map TOML]     decode the raw bytes again -> a new drive
    hondalog-log prune   --days N --mb N [--yes] delete old drives
    hondalog-log vacuum  [--full]                give freed space back to the disk
    hondalog-log sql     [QUERY]                 run SQL (no query: the sqlite3 shell)

DRIVE is a drive name (``drive_2026_11_16_0712``), a capture name
(``stall_20261116_071455``), a drive id, ``last``, ``other.db#name`` for a
drive in another database, or a CSV / MSL / MLG file. ``--db FILE`` (before
the command) picks the database; the default is ``~/hondalog/hondalog.db``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from . import addressmap
from .analysis.compare import compare_logs
from .analysis.filters import FilterSet
from .analysis.report import write_report
from .data import db, open_log, write_log
from .data.library import Library
from .data.reader import Log
from .data.redecode import redecode
from .data.retention import RetentionPolicy
from .export import FORMATS, export_log


def _open(args: argparse.Namespace, spec: str | None) -> Log:
    """A DRIVE argument as an open log."""
    text = spec or "last"
    if os.path.exists(text):
        return open_log(text)                       # a file to import, or another database's newest drive
    ref = db.parse_ref(text, args.db)
    if ref is None:
        where = text.rsplit("#", 1)[0] if "#" in text else db.db_path(args.db)
        if not db.is_database(where):
            raise FileNotFoundError(f"{where}: no database there (nothing recorded yet?)")
        raise KeyError(f"no drive, capture or file {text!r} (see 'hondalog-log list')")
    return open_log(ref)


def _record(args: argparse.Namespace) -> int:
    """Headless recording through ``hondalog.live.LiveSession``: the same
    path the app uses (per-read raw bytes, math channels, alarms, triggers,
    optional black box)."""
    from .live import LiveSession

    amap = addressmap.load(args.map) if args.map else addressmap.load_user_map()
    session = LiveSession(amap, out_dir=args.out or args.db)
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
        print(f"recording {recorder.path.name} into {recorder.db}  (Ctrl-C to stop)")
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
                print(f"            saved {recorder.events.get_nowait().path.name}")
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
            print(f"            saved {recorder.events.get_nowait().path.name}")
        if recorder.write_error is not None:
            print(f"hondalog-log: the log is incomplete: {recorder.write_error}", file=sys.stderr)
            return 1
        names = ", ".join(ref.name for ref in recorder.drives)
        print(f"saved {names} in {recorder.db}  ({recorder.rows} reads, {recorder.markers} markers)")
    return 0


def _info(args: argparse.Namespace) -> int:
    with _open(args, args.drive) as log:
        print(f"{log.path}")
        state = {"crashed": "   (the recorder died: everything up to the last commit is here)",
                 "recording": "   (being recorded now)"}.get(log.status, "")
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
    target = db.db_path(args.db)
    for name in args.files:
        src = Path(name)
        with open_log(src) as log:
            ref = write_log(log, target, {"imported_from": str(src)})
            print(f"{src} -> {ref.name}  ({log.rows} rows, {len(log.events)} events)")
    return 0


def _export(args: argparse.Namespace) -> int:
    filters = None
    if args.filter:
        filters = FilterSet()
        for i, text in enumerate(args.filter):
            filters.add(f"f{i}", text)
    with _open(args, args.drive) as log:
        out = export_log(log, args.out, channels=args.channels.split(",") if args.channels else None,
                         hz=args.hz, filters=filters, t0=args.start, t1=args.end,
                         mlg_version=args.mlg_version, compact=args.compact)
    print(f"wrote {out}  ({out.stat().st_size} bytes)")
    return 0


def _compare(args: argparse.Namespace) -> int:
    with _open(args, args.a) as a, _open(args, args.b) as b:
        channels = args.channels.split(",") if args.channels else None
        result = compare_logs(a, b, channels, align=args.align, text=args.text or "")
        print(f"A  {a.path}   {a.duration:.1f} s")
        print(f"B  {b.path}   {b.duration:.1f} s, laid over A with {result.offset:+.3f} s "
              f"(aligned on {result.aligned_on})")
        print(f"{'channel':<14} {'units':<8} {'mean A':>10} {'mean B':>10} {'A-B mean':>10} "
              f"{'rms':>10} {'max |A-B|':>10} {'samples':>8}")
        for row in result.rows:
            print(f"{row.name:<14} {row.units:<8} {row.mean_a:>10.4g} {row.mean_b:>10.4g} {row.mean:>10.4g} "
                  f"{row.rms:>10.4g} {row.max_abs:>10.4g} {row.n:>8}")
        if result.only_a or result.only_b:
            print(f"only in A: {', '.join(result.only_a) or '-'}; only in B: {', '.join(result.only_b) or '-'}")
    return 0


def _report(args: argparse.Namespace) -> int:
    with _open(args, args.drive) as log:
        out = Path(args.out) if args.out else Path(f"{log.name or Path(args.drive).stem}.html")
        write_report(log, out)
    print(f"wrote {out}")
    return 0


def _redecode(args: argparse.Namespace) -> int:
    amap = addressmap.load(args.map) if args.map else addressmap.load_user_map()
    with _open(args, args.drive) as log:
        new = redecode(log, amap)
        if not new.meta["redecoded_streams"]:
            print("this log has no raw bytes (it was recorded per sweep or imported); nothing to decode again")
            return 1
        ref = write_log(new, args.out or db.db_path(args.db), {"redecoded_drive": log.name})
    print(f"wrote {ref.name} in {ref.db} with map {amap.version!r}")
    return 0


def _list(args: argparse.Namespace) -> int:
    library = Library(args.db)
    library.refresh()
    for entry in library.search(args.query or ""):
        indent = "" if entry.kind == "drive" else "  "
        flag = "" if entry.status in ("complete", "imported") or entry.kind != "drive" else f"[{entry.status}] "
        extra = flag + (entry.trigger or entry.notes)
        print(f"{indent + entry.name:<32} {entry.started[:19]:<19} {entry.duration:8.1f} s  "
              f"{entry.rows:>8} rows  {len(entry.markers):>3} marks  {extra}")
    return 0


def _prune(args: argparse.Namespace) -> int:
    policy = RetentionPolicy(args.days, args.mb, args.drives, keep_captures=not args.captures,
                             full_vacuum=args.full)
    target = db.db_path(args.db)
    if not policy.active:
        print("nothing to do: give --days, --mb or --drives")
        return 1
    doomed = policy.plan(target)
    for ref in doomed:
        print(("delete " if args.yes else "would delete ") + ref.name)
    if args.yes:
        policy.apply(target)
    elif doomed:
        print("dry run; add --yes to delete")
    return 0


def _vacuum(args: argparse.Namespace) -> int:
    target = db.db_path(args.db)
    info = db.vacuum(target, full=args.full)
    what = "rewritten" if args.full else f"{info['free_pages']} free pages released"
    print(f"{target}: {info['before'] / 1e6:.2f} MB -> {info['after'] / 1e6:.2f} MB ({what}; check: {info['check']})")
    return 0 if info["check"] == "ok" else 1


def _sql(args: argparse.Namespace) -> int:
    target = db.db_path(args.db)
    if not args.query:
        if not target.is_file():
            raise FileNotFoundError(f"{target}: no database there (nothing recorded yet?)")
        shell = shutil.which("sqlite3")
        if shell is None:
            print("the sqlite3 shell is not installed (apt install sqlite3); "
                  "give the query on the command line instead", file=sys.stderr)
            return 1
        os.execv(shell, [shell, "-header", "-column", "-readonly", str(target)])
    try:
        names, rows = db.query(target, " ".join(args.query))
    except sqlite3.Error as exc:
        print(f"hondalog-log: {exc}", file=sys.stderr)
        return 2
    sep = "," if args.csv else "\t"
    if names:
        print(sep.join(names))
    for row in rows:
        print(sep.join("" if v is None else v.hex() if isinstance(v, bytes) else str(v) for v in row))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hondalog-log", description="hondalog drive database tool")
    parser.add_argument("--db", help="database file or its folder (default: ~/hondalog/hondalog.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("record", help="record from the ECU without the GUI")
    p.add_argument("--port", required=True, help="serial device, or sim: for the built-in simulator")
    p.add_argument("--bb", help="black box port (bbsim: for the built-in simulator)")
    p.add_argument("--seconds", type=float, help="stop after this long (default: until Ctrl-C)")
    p.add_argument("--map", help="address map TOML (default: your map)")
    p.add_argument("-o", "--out", help="database file or folder to record into (same as --db)")
    p.add_argument("-q", "--quiet", action="store_true", help="no progress lines")
    p.set_defaults(fn=_record)

    p = sub.add_parser("list", help="list drives and captures")
    p.add_argument("-q", "--query")
    p.set_defaults(fn=_list)

    p = sub.add_parser("info", help="show what is in a drive")
    p.add_argument("drive", nargs="?")
    p.add_argument("--events", type=int, default=20)
    p.set_defaults(fn=_info)

    p = sub.add_parser("import", help="read CSV/MSL/MLG logs into the database as new drives")
    p.add_argument("files", nargs="+")
    p.set_defaults(fn=_import)

    p = sub.add_parser("export", help="write another format: " + " ".join(FORMATS))
    p.add_argument("drive")
    p.add_argument("out")
    p.add_argument("--channels", help="comma-separated; default all")
    p.add_argument("--hz", type=float, help="resample to a fixed rate")
    p.add_argument("--filter", action="append", help="keep rows where this is true (repeatable)")
    p.add_argument("--start", type=float)
    p.add_argument("--end", type=float)
    p.add_argument("--mlg-version", type=int, default=2, choices=(1, 2))
    p.add_argument("--compact", action="store_true", help="MLG: 16-bit scaled fields where they fit")
    p.set_defaults(fn=_export)

    p = sub.add_parser("compare", help="two drives (or captures), channel by channel")
    p.add_argument("a")
    p.add_argument("b", help="may be in another database: other.db#drive_...")
    p.add_argument("--channels", help="comma-separated; default: every channel both have")
    p.add_argument("--align", default="start", choices=("start", "marker", "trigger", "crossing"),
                   help="the moment that is laid on top of each other (default: the first sample)")
    p.add_argument("--text", help="with --align marker: the marker whose text contains this")
    p.set_defaults(fn=_compare)

    p = sub.add_parser("report", help="one-page HTML report")
    p.add_argument("drive")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=_report)

    p = sub.add_parser("redecode", help="decode the raw bytes with another address map, as a new drive")
    p.add_argument("drive")
    p.add_argument("out", nargs="?", help="database to write the new drive into (default: the same one)")
    p.add_argument("--map", help="address map TOML (default: your map)")
    p.set_defaults(fn=_redecode)

    p = sub.add_parser("prune", help="delete old drives")
    p.add_argument("--days", type=float)
    p.add_argument("--mb", type=float)
    p.add_argument("--drives", type=int)
    p.add_argument("--captures", action="store_true", help="drives with captures may be deleted too")
    p.add_argument("--full", action="store_true", help="rewrite the file compactly afterwards")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=_prune)

    p = sub.add_parser("vacuum", help="give freed space back to the disk and check the file")
    p.add_argument("--full", action="store_true", help="rewrite the whole file (not while recording)")
    p.set_defaults(fn=_vacuum)

    p = sub.add_parser("sql", help="run one SQL query, or open the sqlite3 shell on the database")
    p.add_argument("query", nargs="*")
    p.add_argument("--csv", action="store_true", help="comma-separated output")
    p.set_defaults(fn=_sql)

    args = parser.parse_args(argv)
    try:
        return int(args.fn(args))
    except (OSError, ValueError, KeyError) as exc:
        text = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        print(f"hondalog-log: {text}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
