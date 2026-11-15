"""Command-line front end for the mapping toolkit.

    python3 -m hondalog.mapping status
    python3 -m hondalog.mapping scripts
    python3 -m hondalog.mapping verify tps_sweep --port /dev/hondalog
    python3 -m hondalog.mapping find --port sim: --channel rpm --seconds 20
    python3 -m hondalog.mapping find --port sim: --value 12.4 --formula "x / 10.45"
    python3 -m hondalog.mapping diff old.toml new.toml
    python3 -m hondalog.mapping hash
    python3 -m hondalog.mapping upgrade

Works over ssh and without a display. ``--port sim:`` uses the built-in fake ECU.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, Sequence

from .. import addressmap
from ..addressmap import MapError
from . import correlate, mapedit, verify
from .sweeps import SweepRecorder


def evidence_dir() -> Path:
    from ..logger import log_dir
    return log_dir().parent / "verification"


def _load(args: argparse.Namespace) -> addressmap.AddressMap:
    if args.map:
        return addressmap.load(args.map, args.variant)
    return addressmap.load_user_map(args.variant)


def _map_path(args: argparse.Namespace) -> Path:
    return Path(args.map) if args.map else addressmap.user_map_path()


def _open_poller(amap: addressmap.AddressMap, port: str):
    from ..poller import Poller
    from ..transport import make_transport
    poller = Poller(make_transport(port, amap.framing), amap)
    poller.start()
    if not poller.ready.wait(8):
        poller.stop()
        raise MapError(f"{port}: port did not open")
    return poller


def cmd_status(args: argparse.Namespace, say: Callable[[str], None]) -> int:
    amap = _load(args)
    say(f"{amap.path}\n{amap.version_tag}   ECU variant: {amap.variant or '-'}   "
        f"schema {amap.schema}")
    for row in verify.coverage(amap):
        ev = row["evidence"]
        how = (f"{ev.date[:10]} {ev.method}" if ev else
               "check with: " + (", ".join(row["strong"]) or "finder / ISO cross-check"))
        say(f"  {row['channel']:12} {row['status']:10} {how}")
    done = sum(1 for c in amap.channels if c.verified)
    say(f"{done} of {len(amap.channels)} channels verified")
    return 0


def cmd_scripts(args: argparse.Namespace, say: Callable[[str], None]) -> int:
    for script in verify.SCRIPTS.values():
        say(f"{script.id:12} {script.title}: {', '.join(script.channels)}")
        say(f"{'':12} {script.intro}")
    return 0


def cmd_verify(args: argparse.Namespace, say: Callable[[str], None],
               ask: Callable[[str], str]) -> int:
    script = verify.SCRIPTS.get(args.script)
    if script is None:
        say(f"no script {args.script!r}; try: {', '.join(verify.SCRIPTS)}")
        return 2
    amap = _load(args)
    session = verify.VerificationSession(script, amap)
    say(f"{script.title}\n{script.intro}\n")
    given = dict(item.split("=", 1) for item in args.ref)
    for ref in script.references:
        text = given.get(ref.name)
        if text is None and not args.auto:
            default = "" if ref.default is None else f" [{ref.default:g}]"
            text = ask(f"{ref.prompt} ({ref.units}){default}, Enter to skip: ").strip()
        if text:
            session.set_reference(ref.name, float(text), "typed in")
    for q in script.questions:
        yes = q.name in args.yes
        if not yes and not args.auto:
            yes = ask(f"{q.prompt} [y/N] ").strip().lower().startswith("y")
        session.answer(q.name, yes)

    poller = _open_poller(amap, args.port)
    try:
        phase: verify.Phase | None = session.start()
        recorder = session.recorder(poller.submit, 0x00, args.hi)
        recorder.start()
        while phase is not None:
            say(f"\n== {phase.title} ==\n{phase.instruction}")
            hold = phase.min_s * args.speed
            if args.auto:
                end = time.monotonic() + hold
                while time.monotonic() < end and recorder.active:
                    session.take(recorder)
                    time.sleep(0.02)
            else:
                ask(f"Press Enter when done (at least {phase.min_s:.0f} s) ... ")
                while not session.can_advance and recorder.active:
                    session.take(recorder)
                    time.sleep(0.05)
            session.take(recorder)
            if recorder.error:
                say(f"link lost: {recorder.error}")
                break
            phase = session.next_phase()
        recorder.stop()
    finally:
        poller.stop()
    report = session.finish()
    path = session.save(args.evidence or evidence_dir())
    say("\n" + report.text())
    say(f"\nevidence saved: {path}")
    for name in report.by_status(verify.FAIL):
        hints = session.suggest(name)
        if hints:
            say(f"\n{name} failed. It may live here instead:")
            for hint in hints:
                say(f"  {hint}")
    wanted = [c for c in args.confirm.split(",") if c] if args.confirm else []
    if not wanted and not args.auto:
        for name in report.by_status(verify.PASS):
            if ask(f"Mark {name} verified in the map? [y/N] ").strip().lower().startswith("y"):
                wanted.append(name)
    for name in wanted:
        try:
            session.confirm(name, _map_path(args), force=args.force)
            say(f"{name}: marked verified in {_map_path(args)}")
        except MapError as exc:
            say(f"{name}: not marked: {exc}")
    return 0 if not report.by_status(verify.FAIL) else 1


def cmd_find(args: argparse.Namespace, say: Callable[[str], None]) -> int:
    amap = _load(args)
    poller = _open_poller(amap, args.port)
    try:
        recorder = SweepRecorder(poller.submit, 0x00, args.hi)
        say(f"sweeping 0x00-0x{args.hi:02X} for {args.seconds:g} s ...")
        rec = recorder.run(seconds=args.seconds)
    finally:
        poller.stop()
    say(f"{len(rec)} sweeps, {recorder.failed_reads} failed reads")
    if args.value is not None:
        formula = args.formula
        if formula is None:
            ch = amap.channel(args.channel) if args.channel else None
            formula = ch.formula if ch else "x"
        hits = correlate.find_value(rec, args.value, formula, rel_tol=args.tol, top=args.top)
        say(f"addresses that read {args.value:g} through '{formula}':")
        for hit in hits:
            say(f"  {hit}")
        return 0 if hits else 1
    if not args.channel:
        say("give --channel NAME (rank against it) or --value NUMBER")
        return 2
    matches = correlate.rank_against_channel(rec, amap, args.channel, inverse=args.inverse,
                                             top=args.top)
    say(f"addresses that move with {args.channel}:")
    for match in matches:
        say(f"  {match}")
    return 0 if matches else 1


def cmd_diff(args: argparse.Namespace, say: Callable[[str], None]) -> int:
    old, new = addressmap.load(args.old, args.variant), addressmap.load(args.new, args.variant)
    diff = mapedit.diff_maps(old, new)
    say(diff.text())
    return 1 if diff else 0


def cmd_hash(args: argparse.Namespace, say: Callable[[str], None]) -> int:
    amap = _load(args)
    say(f"{amap.version_hash}  {amap.version}  ({amap.path})")
    return 0


def cmd_upgrade(args: argparse.Namespace, say: Callable[[str], None]) -> int:
    path = _map_path(args)
    if not path.exists():
        addressmap.load_user_map()
    amap, skipped = mapedit.upgrade_to_v2(path)
    say(f"{path}: schema {amap.schema}, {len(amap.math)} math channels, "
        f"{len(amap.alarms)} alarms" + (f"; left out: {', '.join(skipped)}" if skipped else ""))
    return 0


def main(argv: Sequence[str] | None = None, say: Callable[[str], None] = print,
         ask: Callable[[str], str] = input) -> int:
    ap = argparse.ArgumentParser(prog="hondalog-map", description=__doc__.splitlines()[0])
    ap.add_argument("--map", help="map file (default: your ~/.config/hondalog/addressmap.toml)")
    ap.add_argument("--variant", help="ECU variant to load, e.g. P2P")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="channels, verified state and how to check each")
    sub.add_parser("scripts", help="list the verification scripts")
    v = sub.add_parser("verify", help="run a guided verification script")
    v.add_argument("script")
    v.add_argument("--port", required=True)
    v.add_argument("--ref", action="append", default=[], metavar="NAME=VALUE",
                   help="a reference reading, e.g. batt_meter=12.4")
    v.add_argument("--yes", action="append", default=[], metavar="QUESTION",
                   help="answer yes to a condition, e.g. cold_engine")
    v.add_argument("--auto", action="store_true", help="no prompts: each phase runs its minimum time")
    v.add_argument("--speed", type=float, default=1.0, help="with --auto: scale the phase times")
    v.add_argument("--confirm", default="", metavar="CH,CH", help="mark these verified if they pass")
    v.add_argument("--force", action="store_true", help="also confirm plausibility-only results")
    v.add_argument("--hi", type=lambda s: int(s, 0), default=0x3F,
                   help="highest address to sweep (default 0x3F; 0xFF for the finder's sake)")
    v.add_argument("--evidence", help="folder for evidence files")
    f = sub.add_parser("find", help="rank addresses against a channel or a typed value")
    f.add_argument("--port", required=True)
    f.add_argument("--channel")
    f.add_argument("--value", type=float)
    f.add_argument("--formula")
    f.add_argument("--tol", type=float, default=0.05, help="relative tolerance for --value")
    f.add_argument("--seconds", type=float, default=15.0)
    f.add_argument("--inverse", action="store_true", help="also try 1/x")
    f.add_argument("--top", type=int, default=10)
    f.add_argument("--hi", type=lambda s: int(s, 0), default=0xFF)
    d = sub.add_parser("diff", help="what changed between two map files")
    d.add_argument("old")
    d.add_argument("new")
    sub.add_parser("hash", help="print the map's version hash")
    sub.add_parser("upgrade", help="bring a schema 1 map up to schema 2 in place")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "verify":
            return cmd_verify(args, say, ask)
        handler = {"status": cmd_status, "scripts": cmd_scripts, "find": cmd_find,
                   "diff": cmd_diff, "hash": cmd_hash, "upgrade": cmd_upgrade}[args.cmd]
        return handler(args, say)
    except (MapError, ValueError, OSError) as exc:
        say(f"error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
