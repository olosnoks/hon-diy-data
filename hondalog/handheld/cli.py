"""``hondalog-handheld``: the laptop's side of the handheld.

    hondalog-handheld export-map SDCARD [--map TOML] [--variant X]
        compile your address map (channels, formulas, alarm limits, link
        timing) into SDCARD/HANDHELD.MAP; write HANDHELD.CFG if there is none
    hondalog-handheld import SDCARD [--db FILE] [--map TOML] [--force]
        every drive on the card into the database (skips what is already in)
    hondalog-handheld info FILE...
        what is in a drive file, a capture file or a HANDHELD.MAP
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .. import addressmap
from . import format as F
from .drivefile import read_drive_file
from .importer import import_card, import_file
from .mapfile import MAP_NAME, c_array, export_map, read_map


def _load_map(args: argparse.Namespace) -> addressmap.AddressMap:
    variant = getattr(args, "variant", None)
    if args.map:
        return addressmap.load(args.map, variant)
    return addressmap.load_user_map(variant)


def _export(args: argparse.Namespace) -> int:
    amap = _load_map(args)
    stall = {k: v for k, v in (("pre_s", args.pre), ("post_s", args.post), ("rpm_run", args.rpm_run),
                               ("rpm_zero", args.rpm_zero)) if v is not None}
    if args.c_array:
        Path(args.c_array).write_text(c_array(amap), encoding="utf-8", newline="\n")
        print(f"wrote {args.c_array} (map #{amap.version_hash})")
        if not args.folder:
            return 0
    if not args.folder:
        print("export-map: give the SD card folder", file=sys.stderr)
        return 2
    report = export_map(amap, args.folder, stall=stall or None)
    print(f"{Path(args.folder) / MAP_NAME}: map {amap.version_tag}")
    print(f"  {len(report.channels)} channels, {len(report.math)} math channels, {len(report.alarms)} alarms; "
          f"{report.core_bytes} bytes for the handheld, {report.total_bytes} with the map text")
    for line in report.skipped:
        print(f"  left out: {line}")
    unverified = [c.name for c in amap.channels if not c.verified]
    if unverified:
        print(f"  {len(unverified)} channel(s) not verified on the car: the handheld marks them")
    return 0


def _import(args: argparse.Namespace) -> int:
    amap = addressmap.load(args.map, args.variant) if args.map else None
    target = Path(args.card)
    if target.is_file():
        results = [import_file(target, args.db, amap=amap, force=args.force, allow_other_map=args.other_map)]
    elif target.is_dir():
        results = import_card(target, args.db, amap=amap, force=args.force, allow_other_map=args.other_map)
    else:
        print(f"{target}: no such folder or file", file=sys.stderr)
        return 2
    if not results:
        print(f"{target}: no drive files (looked in DRIVES/ and CAPTURES/)")
        return 1
    for res in results:
        print(res)
    return 1 if any(r.status == "failed" for r in results) else 0


def _info(args: argparse.Namespace) -> int:
    code = 0
    for name in args.files:
        path = Path(name)
        try:
            head = path.read_bytes()[:4]
            if head == F.MAP_MAGIC.to_bytes(4, "little"):
                mf = read_map(path)
                print(f"{path}: handheld map #{mf.map_hash}, {mf.version!r}, variant {mf.variant or '-'}")
                print(f"  {len(mf.channels)} channels, {len(mf.alarms)} alarms, {len(mf.core)} bytes core, "
                      f"map text {'inside' if mf.source else 'not inside'}")
                for c in mf.channels:
                    where = "math" if c["kind"] == F.KIND_MATH else f"{c['addr']:02X}+{c['length']}"
                    print(f"    {c['name']:<14} {where:<6} {'fast medium slow'.split()[c['rate_class']]:<6} "
                          f"{c['units']:<7} {'' if c['verified'] else 'unverified'}")
                continue
            df = read_drive_file(path)
        except (OSError, ValueError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            code = 1
            continue
        kind = "drive" if df.kind == F.FK_DRIVE else "capture"
        epoch = df.epoch_unix
        when = datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if epoch else \
            "time not known (no GPS fix)"
        print(f"{path}: {kind} {df.file_no}, boot {df.boot_no}, device {df.device or '?'}, firmware {df.fw}")
        print(f"  started {when}; {df.seconds(df.t_last_us):.1f} s; map #{df.map_hash}; "
              f"{F.BACKENDS.get(df.backend, '?')}{' ISO 9141-2' if df.link_mode else ''}; ECU id {df.ecu_id_text}")
        print(f"  {len(df.reads)} reads, {len(df.iso)} OBD2 reads, {len(df.gps)} GPS fixes, {len(df.events)} events, "
              f"{len(df.captures)} captures")
        end = f"ended: {df.end['reason']}" if df.end else "no end marker (power was cut)"
        print(f"  {df.blocks} blocks, {df.used_bytes} of {df.size} bytes used, {df.bad_blocks} damaged, "
              f"{df.missing_blocks} missing; {end}")
        for e in df.events[:args.events]:
            print(f"    {df.seconds(e.t_us):9.3f}  {e.name}  src={e.src} aux={e.aux} value={e.value}")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hondalog-handheld", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("export-map", help="compile the address map for the handheld onto an SD card")
    p.add_argument("folder", nargs="?", help="the SD card (or any folder)")
    p.add_argument("--map", help="address map TOML (default: your map)")
    p.add_argument("--variant", help="ECU variant (default: the map's own)")
    p.add_argument("--pre", type=float, help="seconds kept before a stall (default 30)")
    p.add_argument("--post", type=float, help="seconds after a stall (default 10)")
    p.add_argument("--rpm-run", dest="rpm_run", type=float, help="the stall trigger arms above this (default 400)")
    p.add_argument("--rpm-zero", dest="rpm_zero", type=float, help="... and fires below this (default 50)")
    p.add_argument("--c-array", metavar="FILE", help="also write the map as the firmware's built-in default")
    p.set_defaults(fn=_export)

    p = sub.add_parser("import", help="import the card's drives into the database")
    p.add_argument("card", help="the SD card folder, or one .HHD file")
    p.add_argument("--db", help="database file or its folder (default: ~/hondalog/hondalog.db)")
    p.add_argument("--map", help="address map TOML the drives were recorded with (default: found by hash)")
    p.add_argument("--variant")
    p.add_argument("--force", action="store_true", help="import again what the database already has")
    p.add_argument("--other-map", dest="other_map", action="store_true",
                   help="decode with --map even if its hash is not the one in the file")
    p.set_defaults(fn=_import)

    p = sub.add_parser("info", help="show what is in a drive file or a HANDHELD.MAP")
    p.add_argument("files", nargs="+")
    p.add_argument("--events", type=int, default=20)
    p.set_defaults(fn=_info)

    args = parser.parse_args(argv)
    try:
        return int(args.fn(args))
    except addressmap.MapError as exc:
        print(f"hondalog-handheld: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
