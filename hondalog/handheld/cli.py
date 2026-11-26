"""``hondalog-handheld``: the laptop's side of the handheld.

    hondalog-handheld export-map SDCARD [--map TOML] [--variant X]
        compile your address map (channels, formulas, alarm limits, link
        timing) into SDCARD/HANDHELD.MAP; write HANDHELD.CFG if there is none
    hondalog-handheld import SDCARD [--db FILE] [--map TOML] [--force]
        every drive on the card into the database (skips what is already in)
    hondalog-handheld info FILE...
        what is in a drive file, a capture file, a HANDHELD.MAP or a MAP folder
    hondalog-handheld map-build --pbf FILE [--pbf FILE...] --center LAT,LON --radius-mi 100 --out SDCARD
        the offline road map for the Map screen, from OpenStreetMap extracts
        (Geofabrik .osm.pbf), into SDCARD/MAP (MAPFORMAT.md)

Over the handheld's USB-C cable (firmware/handheld/SERIAL.md; PORT is
``cyd:`` = found by itself, or ``cyd:/dev/ttyUSB0``):

    hondalog-handheld status [--port PORT]
        who is there: firmware, variant, map, the link's counters
    hondalog-handheld sync [--port PORT] [--db FILE] [--end-drive]
        copy the card's drives to the laptop and import them (resumable)
    hondalog-handheld push-map [--port PORT] [--map TOML] [--cfg FILE]
        send HANDHELD.MAP (and HANDHELD.CFG); used from the next power-up
    hondalog-handheld put-map DIR [--port PORT]
        copy a MAP folder (map-build's output) into the card's MAP/ over the cable (resumable, verified)
    hondalog-handheld set-time [--port PORT]
        set the handheld's clock from this computer's (when it has no GPS time)
    hondalog-handheld command mark|ack-alarm|dtc|drive-stop|drive-start [--port PORT]
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


def _info_map(path: Path) -> int:
    """A MAP folder (or the card holding one): its header, and every tile checked."""
    from . import osmmap as OM
    folder = path / OM.DIR_NAME if (path / OM.DIR_NAME / OM.IDX_NAME).exists() else path
    try:
        mr = OM.MapReader(folder)
    except (OSError, ValueError) as exc:
        print(f"{folder}: {exc}", file=sys.stderr)
        return 1
    h = mr.header
    print(f"{folder}: road map built {h.build_date}, centre {h.proj.lat0:.4f}, {h.proj.lon0:.4f}, "
          f"radius {h.radius_m / 1609.344:.0f} mi")
    print(f"  {h.sources}; {h.attribution}")
    bad = 0
    for i, lv in enumerate(h.levels):
        for col, row in ((c, r) for r in range(lv.rows) for c in range(lv.cols)):
            raw = mr.raw(i, col, row)
            if raw is None:
                continue
            try:
                OM.decode_tile(raw)
            except ValueError as exc:
                bad += 1
                print(f"  level {i} tile {col},{row}: {exc}")
        print(f"  level {i}: {lv.n_tiles} tiles of {lv.tile_m / 1000:g} km, {lv.data_bytes / 1e6:.1f} MB")
    print(f"  {'all tiles check out' if not bad else f'{bad} damaged tiles'}")
    return 1 if bad else 0


def _info(args: argparse.Namespace) -> int:
    code = 0
    for name in args.files:
        path = Path(name)
        if path.is_dir():
            code |= _info_map(path)
            continue
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


# ---- over the serial link ----------------------------------------------------

def _client(args: argparse.Namespace):
    from .serial_link import PREFIX, CydClient
    port = args.port if args.port.startswith(PREFIX) else PREFIX + args.port
    cyd = CydClient(port)
    hello = cyd.open()
    print(hello.text)
    return cyd


def _status(args: argparse.Namespace) -> int:
    from . import serial_proto as P
    cyd = _client(args)
    try:
        cyd._frames(1.2)                    # a STATS frame comes once a second
        h, st = cyd.hello, cyd.stats
        assert h is not None
        print(f"  link: {'two-way' if h.two_way else 'TX only (the GPS has the receive pin)'}, {h.baud} baud, "
              f"link revision {h.rev}")
        print(f"  ECU link: {P.LINK_NAMES.get(h.link_state, h.link_state)}; ECU id {h.ecu_id.hex(' ') or '-'}")
        print(f"  clock: {'set' if h.flags & 2 else 'not set'}{' (GPS)' if h.flags & 4 else ''}"
              f"{' (pulse: PPS)' if h.clock_from_pps else ''}; SD card: {'ok' if h.flags & 8 else 'none'}")
        if st and st.get("time_source"):
            print(f"  time: {P.time_text(st['time_source'], st.get('time_err_us', 0))} "
                  f"({P.TS_NAMES.get(st['time_source'], '?')})")
        if st:
            print(f"  K-line: {st['reads']} reads, {st['errors']} errors, {st['timeouts']} timeouts, "
                  f"{st['hz_c'] / 100:.1f} Hz")
            print(f"  this link: {st['tx_frames']} frames sent, {st['tx_dropped']} dropped, "
                  f"{st['rx_frames']} commands heard, {st['rx_bad']} damaged")
        print(f"  frames damaged on the way here: {cyd.damaged}")
    finally:
        cyd.close()
    return 0


def _sync(args: argparse.Namespace) -> int:
    from .sync import sync

    last = [""]

    def progress(name: str, done: int, total: int) -> None:
        if sys.stdout.isatty():
            print(f"\r  {name}  {done}/{total} bytes", end="", flush=True)
            last[0] = name
    cyd = _client(args)
    try:
        report = sync(database=args.db, mirror=args.mirror, captures=not args.no_captures,
                      end_drive=args.end_drive, do_import=not args.no_import, progress=progress, client=cyd)
    finally:
        cyd.close()
    if last[0]:
        print()
    print(f"{report.mirror}: {len(report.fetched)} file(s) copied ({report.bytes} bytes), "
          f"{len(report.present)} already here")
    for name in report.open:
        print(f"  {name}: still being written, not copied (--end-drive ends it first)")
    for res in report.imported:
        print(res)
    return 1 if any(r.status == "failed" for r in report.imported) else 0


def push_map_to(cyd, amap) -> int:
    """Send ``amap`` as HANDHELD.MAP through an open ``CydClient``; returns its size. The
    handheld uses it from its next power-up. (The app's "push map" for relaying uses this too.)"""
    from . import serial_proto as P
    from .mapfile import build_map
    data = build_map(amap)
    cyd.push(P.F_MAP, data)
    return len(data)


def _push_map(args: argparse.Namespace) -> int:
    from . import serial_proto as P
    amap = _load_map(args)
    cyd = _client(args)
    try:
        size = push_map_to(cyd, amap)
        print(f"HANDHELD.MAP: map #{amap.version_hash} sent ({size} bytes); the handheld uses it from its "
              "next power-up")
        if args.cfg:
            skipped = cyd.push(P.F_CFG, Path(args.cfg).read_bytes())
            print("HANDHELD.CFG sent" + (f"; the handheld will skip {skipped} line(s) it does not know"
                                         if skipped else ""))
    finally:
        cyd.close()
    return 0


def _put_map(args: argparse.Namespace) -> int:
    import time as _time
    t0 = _time.monotonic()
    state = {"pct": -1, "name": ""}
    tty = sys.stdout.isatty()

    def progress(name: str, done: int, size: int, all_done: int, all_total: int) -> None:
        pct = 100 * all_done // max(1, all_total)
        if tty:
            width = 30
            fill = width * all_done // max(1, all_total)
            rate = all_done / max(0.001, _time.monotonic() - t0) / 1000
            print(f"\r  {name:<10} [{'#' * fill}{'.' * (width - fill)}] {pct:3d}%  {rate:5.1f} kB/s", end="", flush=True)
        elif name != state["name"] or pct >= state["pct"] + 10:
            state.update(pct=pct, name=name)
            print(f"  {name}: {pct}% of the set", flush=True)

    cyd = _client(args)
    try:
        names = cyd.put_map(args.dir, progress)
    finally:
        cyd.close()
    if tty:
        print()
    secs = _time.monotonic() - t0
    print(f"MAP/ on the handheld: {', '.join(names)} sent and verified in {secs:.0f} s; the Map screen uses it "
          "straight away")
    return 0


def _set_time(args: argparse.Namespace) -> int:
    cyd = _client(args)
    try:
        taken = cyd.set_time()
    finally:
        cyd.close()
    print("clock set" if taken else "not set: the handheld's GPS has already set its clock")
    return 0


def _command(args: argparse.Namespace) -> int:
    import time as _time

    from . import format as F
    from . import serial_proto as P
    from .serial_link import parse_event
    cyd = _client(args)
    try:
        if args.what == "mark":
            cyd.mark()
        elif args.what == "ack-alarm":
            cyd.ack_alarm()
        elif args.what in ("drive-stop", "drive-start"):
            cyd.drive(args.what == "drive-start")
        elif args.what == "dtc":
            cyd.request_dtc()
            print("reading trouble codes (live data pauses for up to 10 s) ...")
            end = _time.monotonic() + 15.0
            while _time.monotonic() < end:
                for ftype, _seq, payload in cyd._frames(0.1):
                    ev = parse_event(payload) if ftype == P.SF_EVENT else None
                    if ev is None:
                        continue
                    if ev[1] == F.EV_DTC:
                        print(f"  {F.dtc_text(int(ev[4]))}")
                        if ev[2] + 1 >= ev[3]:
                            return 0
                    elif ev[1] == F.EV_DTC_NONE:
                        print("  no stored trouble codes")
                        return 0
            print("no answer: the ECU may not speak OBD2 (pre-1996 Hondas do not)")
            return 1
        print(f"{args.what}: ok")
    finally:
        cyd.close()
    return 0


def _map_build(args: argparse.Namespace) -> int:
    try:
        from . import osmbuild
    except ImportError as exc:          # pragma: no cover - numpy missing
        print(f"map-build: {exc}", file=sys.stderr)
        return 1
    try:
        import osmium  # noqa: F401
    except ImportError:
        print("map-build needs pyosmium: pip install osmium  (Debian/Ubuntu: apt install python3-pyosmium)",
              file=sys.stderr)
        return 1
    try:
        lat, lon = (float(v) for v in args.center.split(","))
    except ValueError:
        print(f"map-build: --center wants LAT,LON, got {args.center!r}", file=sys.stderr)
        return 2
    if not (-85 < lat < 85 and -180 <= lon <= 180) or not 0 < args.radius_mi <= 250:
        print("map-build: centre or radius out of range (radius 0 to 250 miles)", file=sys.stderr)
        return 2
    for p in args.pbf:
        if not Path(p).is_file():
            print(f"map-build: {p}: no such file", file=sys.stderr)
            return 1
    say = (lambda msg: None) if args.quiet else (lambda msg: print(msg, flush=True))
    report = osmbuild.build(args.pbf, args.out, lat, lon, args.radius_mi, rail=not args.no_rail,
                            fuel=not args.no_fuel, zip_path=args.zip, progress=say)
    for line in report.lines():
        print(line)
    print(f"copy {report.folder} to the root of the handheld's SD card; Map data {osmbuild.M.ATTRIBUTION}")
    return 0


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

    p = sub.add_parser("map-build", help="build the offline road map (SDCARD/MAP) from OpenStreetMap extracts")
    p.add_argument("--pbf", action="append", required=True, metavar="FILE",
                   help="an OSM extract (.osm.pbf, or .osm); repeat for several (e.g. two states)")
    p.add_argument("--center", required=True, metavar="LAT,LON", help="centre of the map, e.g. 32.2988,-90.1848")
    p.add_argument("--radius-mi", dest="radius_mi", type=float, default=100.0, help="radius in miles (default 100)")
    p.add_argument("--out", required=True, metavar="SDCARD", help="the SD card (MAP/ is written into it)")
    p.add_argument("--zip", metavar="FILE", help="also write a zip holding the MAP folder")
    p.add_argument("--no-rail", dest="no_rail", action="store_true", help="leave the railways out")
    p.add_argument("--no-fuel", dest="no_fuel", action="store_true", help="leave the fuel stations out")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(fn=_map_build)

    port_help = "the handheld: cyd: (found by itself), cyd:/dev/ttyUSB0, cyd:/dev/ttyUSB0@460800"
    p = sub.add_parser("status", help="ask the handheld who it is, over its USB-C cable")
    p.add_argument("--port", default="cyd:", help=port_help)
    p.set_defaults(fn=_status)

    p = sub.add_parser("sync", help="copy the handheld's drives over its USB-C cable and import them")
    p.add_argument("--port", default="cyd:", help=port_help)
    p.add_argument("--db", help="database file or its folder (default: ~/hondalog/hondalog.db)")
    p.add_argument("--mirror", help="where the copied files go (default: ~/hondalog/handheld/<device>)")
    p.add_argument("--end-drive", dest="end_drive", action="store_true",
                   help="end the drive being recorded first, so it is copied too")
    p.add_argument("--no-captures", dest="no_captures", action="store_true", help="drives only")
    p.add_argument("--no-import", dest="no_import", action="store_true", help="copy only")
    p.set_defaults(fn=_sync)

    p = sub.add_parser("push-map", help="send your address map (and HANDHELD.CFG) to the handheld")
    p.add_argument("--port", default="cyd:", help=port_help)
    p.add_argument("--map", help="address map TOML (default: your map)")
    p.add_argument("--variant")
    p.add_argument("--cfg", help="also send this file as HANDHELD.CFG")
    p.set_defaults(fn=_push_map)

    p = sub.add_parser("put-map", help="copy a MAP folder into the handheld's SD card over its USB-C cable")
    p.add_argument("dir", help="the MAP folder (MAP.IDX, MAP.DAT, README.TXT) or the card folder that holds it")
    p.add_argument("--port", default="cyd:", help=port_help)
    p.set_defaults(fn=_put_map)

    p = sub.add_parser("set-time", help="set the handheld's clock from this computer's")
    p.add_argument("--port", default="cyd:", help=port_help)
    p.set_defaults(fn=_set_time)

    p = sub.add_parser("command", help="press a button on the handheld from here")
    p.add_argument("what", choices=("mark", "ack-alarm", "dtc", "drive-stop", "drive-start"))
    p.add_argument("--port", default="cyd:", help=port_help)
    p.set_defaults(fn=_command)

    from . import hl_cli
    hl_cli.register(sub, port_help)

    args = parser.parse_args(argv)
    try:
        return int(args.fn(args))
    except addressmap.MapError as exc:
        print(f"hondalog-handheld: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        from .serial_link import CydError
        if isinstance(exc, (CydError, OSError)):
            print(f"hondalog-handheld: {exc}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
