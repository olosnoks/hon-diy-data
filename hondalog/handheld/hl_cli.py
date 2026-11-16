"""``hondalog-handheld ls|get|put|rm|cfg|stat|mark|time|reboot|flash``: thin wrappers on
``hl_client.HlClient``, the same client the app's Handheld tab uses."""

from __future__ import annotations

import argparse
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import hl_proto as H
from .hl_client import HlClient, HlError, MapFolderError, reason_text

PORT_HELP = "the handheld: cyd: (found by itself), cyd:/dev/ttyUSB0, cyd:/dev/ttyUSB0@460800"


def _mb(n: float) -> str:
    return f"{n / 1048576:.1f}"


class Bar:
    """A one-line progress readout: percent, MB, rate, ETA."""

    def __init__(self, label: str, out=sys.stderr):
        self.label, self.out, self.t0, self.last = label, out, time.monotonic(), 0.0

    def __call__(self, done: int, total: int) -> None:
        now = time.monotonic()
        if done < total and now - self.last < 0.25:
            return
        self.last = now
        el = max(now - self.t0, 1e-6)
        pct = 100.0 * done / total if total else 100.0
        eta = f" ETA {int((total - done) / (done / el))} s" if done and done < total and el > 2 else ""
        print(f"\r{self.label}: {pct:5.1f}%  {_mb(done)} / {_mb(total)} MB  {done / el / 1024:.0f} kB/s{eta}   ",
              end="" if done < total else "\n", file=self.out, flush=True)


@contextmanager
def session(args: argparse.Namespace) -> Iterator[HlClient]:
    from .serial_link import make_port
    port = make_port(args.port)
    port.open()
    client = HlClient(port, on_console=lambda t: print(f"  [{t}]", file=sys.stderr) if args.verbose else None)
    try:
        client.connect()
        yield client
    finally:
        client.close()
        port.close()


def _guard(fn):
    def run(args: argparse.Namespace) -> int:
        try:
            return fn(args) or 0
        except HlError as exc:
            print(f"hondalog-handheld: {reason_text(exc)}", file=sys.stderr)
            return 1
        except MapFolderError as exc:
            print(f"hondalog-handheld: {exc}", file=sys.stderr)
            return 1
    return run


@_guard
def _ls(args) -> int:
    with session(args) as c:
        files, dirs = c.ls(args.path)
        for d in dirs:
            print(f"{'<dir>':>12}  {'':19}  {d}/")
        for name, size, mtime in files:
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)) if mtime else "-"
            print(f"{size:>12}  {when:19}  {name}")
    return 0


@_guard
def _get(args) -> int:
    with session(args) as c:
        data = c.get(args.path, Bar(args.path) if sys.stderr.isatty() else None)
    out = Path(args.output or Path(args.path).name)
    out.write_bytes(data)
    print(f"{args.path}: {len(data)} bytes -> {out}")
    return 0


@_guard
def _put(args) -> int:
    data = Path(args.file).read_bytes()
    with session(args) as c:
        note = c.put(args.path, data, Bar(args.path) if sys.stderr.isatty() else None)
    print(f"{args.file} -> {args.path}: {len(data)} bytes{' (' + note + ')' if note else ''}")
    return 0


@_guard
def _rm(args) -> int:
    with session(args) as c:
        c.rm(args.path)
    print(f"{args.path} removed")
    return 0


@_guard
def _cfg(args) -> int:
    with session(args) as c:
        if args.action == "get":
            data = c.cfg_get()
            if args.file:
                Path(args.file).write_bytes(data)
                print(f"HANDHELD.CFG -> {args.file}")
            else:
                sys.stdout.write(data.decode("utf-8", "replace"))
        else:
            if not args.file:
                print("cfg put wants a FILE", file=sys.stderr)
                return 2
            note = c.cfg_put(Path(args.file).read_bytes())
            print(f"HANDHELD.CFG written ({note}); the handheld uses it from the next power-up")
    return 0


@_guard
def _stat(args) -> int:
    import json
    with session(args) as c:
        st = c.stat()
    if args.json:
        print(json.dumps(st, indent=2))
        return 0
    link, gps, sd, drive, tm = st["link"], st["gps"], st["sd"], st["drive"], st["time"]
    print(f"ECU link   {link['name']}  {link['hz']} Hz  {link['reads']} reads  {link['errors']} errors  ECU id {st['ecu']['id'] or '-'}")
    print(f"GPS        {'fix' if gps['fix'] else 'no fix'}  {gps['sats']} sats  {gps['lat']:.5f}, {gps['lon']:.5f}  {gps['speed_kmh']} km/h")
    print(f"SD card    {'ok' if sd['ok'] else 'none'}  {sd['free_mb']} MB free")
    print(f"drive      {'recording D%06d, %d bytes' % (drive['no'], drive['bytes']) if drive['recording'] else 'not recording'}"
          f"  {drive['markers']} markers  {drive['stalls']} stalls")
    print(f"time       {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(tm['unix_ms'] / 1000)) if tm['unix_ms'] else 'unknown'}"
          f" UTC  (from {tm['source']})")
    return 0


@_guard
def _mark(args) -> int:
    with session(args) as c:
        c.mark()
    print("marker set")
    return 0


@_guard
def _time(args) -> int:
    with session(args) as c:
        print("clock set" if c.set_time() == "ok" else "the handheld keeps GPS time (not changed)")
    return 0


@_guard
def _reboot(args) -> int:
    with session(args) as c:
        c.reboot()
    print("the handheld is restarting")
    return 0


def _flash(args) -> int:
    from . import flasher
    from .serial_link import find_device
    port = args.port[4:] if args.port.startswith("cyd:") else args.port
    port = port.partition("@")[0] or find_device() or ""
    if not port:
        print("flash: no handheld found; name the port: --port /dev/ttyUSB0", file=sys.stderr)
        return 1
    last = {"stage": ""}

    def show(p: flasher.Progress) -> None:
        if p.pct is not None:
            eta = f"  ETA {int(p.eta)} s" if p.eta else ""
            print(f"\r{p.stage}: {p.pct:5.1f}%  {p.written / 1048576:.2f} / {p.total / 1048576:.2f} MB{eta}   ",
                  end="", file=sys.stderr, flush=True)
        elif p.stage != last["stage"]:
            last["stage"] = p.stage
            print(f"\n{p.stage}", file=sys.stderr)

    try:
        flasher.flash(args.image, port, args.baud, show)
    except flasher.FlashError as exc:
        print(f"\nflash: {exc}", file=sys.stderr)
        return 1
    print("\nFlashed. The board restarts into the new firmware.")
    return 0


def register(sub, port_help: str = PORT_HELP) -> None:
    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--port", default="cyd:", help=port_help)
        p.add_argument("-v", "--verbose", action="store_true", help="show the handheld's log lines")

    p = sub.add_parser("ls", help="list files on the handheld's SD card (HL session)")
    p.add_argument("path", nargs="?", default="/")
    common(p)
    p.set_defaults(fn=_ls)
    p = sub.add_parser("get", help="download a file from the card")
    p.add_argument("path")
    p.add_argument("-o", "--output")
    common(p)
    p.set_defaults(fn=_get)
    p = sub.add_parser("put", help="upload a file to MAP/, or as HANDHELD.CFG / HANDHELD.MAP")
    p.add_argument("file")
    p.add_argument("path", help="/MAP/MAP.DAT, /HANDHELD.CFG, ...")
    common(p)
    p.set_defaults(fn=_put)
    p = sub.add_parser("rm", help="delete a file in MAP/ or DRIVES/ (or the settings files)")
    p.add_argument("path")
    common(p)
    p.set_defaults(fn=_rm)
    p = sub.add_parser("cfg", help="read or write HANDHELD.CFG")
    p.add_argument("action", choices=("get", "put"))
    p.add_argument("file", nargs="?")
    common(p)
    p.set_defaults(fn=_cfg)
    p = sub.add_parser("stat", help="the handheld's status: ECU link, GPS, card, drive, clock")
    p.add_argument("--json", action="store_true")
    common(p)
    p.set_defaults(fn=_stat)
    for name, fn, text in (("mark", _mark, "set a marker in the drive being recorded"),
                           ("time", _time, "set the handheld's clock (unless it has GPS time)"),
                           ("reboot", _reboot, "restart the handheld")):
        p = sub.add_parser(name, help=text)
        common(p)
        p.set_defaults(fn=fn)
    p = sub.add_parser("flash", help="install a merged firmware image on the handheld (esptool)")
    p.add_argument("image", help="the merged .bin, written at 0x0")
    p.add_argument("--port", default="cyd:", help=port_help)
    p.add_argument("--baud", type=int, default=460800, help="flashing speed (default 460800; 115200 if it fails)")
    p.set_defaults(fn=_flash)
