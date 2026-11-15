"""``hondalog-tune``: the tuning tools from a terminal.

    hondalog-tune info rom.bin -d p28.toml
    hondalog-tune list -d p28.xdf
    hondalog-tune show rom.bin -d p28.toml fuel
    hondalog-tune diff stock.bin tuned.bin -d p28.toml
    hondalog-tune convert p28.xdf p28.toml
    hondalog-tune checksum rom.bin -d p28.toml --fix fixed.bin
    hondalog-tune burn-image rom.bin -d p28.toml chip.bin
    hondalog-tune burn rom.bin -d p28.toml --dry-run
    hondalog-tune demo-rom demo.bin        (pairs with the built-in demo definition)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .burner import BurnerError, Minipro
from .definition import Definition, DefinitionError, Table, load_definition
from .demo import DEMO_DEFINITION, build_demo_rom
from .diff import byte_runs, diff_roms
from .mathexpr import MathError
from .rom import (BurnRefused, RomError, RomImage, build_burn_image, checksum_fix,
                  checksum_report)
from .session import RomSession, rom_dir
from .table import TableError
from .xdf import save_xdf


def _definition(args) -> Definition:
    path = args.definition
    if path in (None, "demo"):
        if path is None:
            raise DefinitionError("give a definition with -d FILE (.xdf or .toml), "
                                  "or -d demo for the built-in demo")
        path = DEMO_DEFINITION
    return load_definition(path)


def _load(path: str, definition: Definition | None, half: str | None) -> RomImage:
    size = definition.rom_size if definition else 0x8000
    return RomImage.load(path, size, half)


def cmd_info(args) -> int:
    definition = _definition(args) if args.definition else None
    rom = _load(args.rom, definition, args.half)
    print(f"file:     {args.rom}")
    print(f"layout:   {rom.layout.describe()}")
    print(f"sha256:   {rom.sha256}  (the {rom.size // 1024} KB image)")
    for w in rom.warnings:
        print(f"warning:  {w}")
    if definition:
        print(f"definition: {definition.title or '(untitled)'}: "
              f"{len(definition.tables)} tables, {len(definition.scalars)} scalars, "
              f"{len(definition.flags)} flags, {len(definition.patches)} patches")
        for w in rom.check_signatures(definition):
            print(f"warning:  {w}")
        report = checksum_report(rom.data, definition.checksum)
        rule = "" if report.verified_rule or report.status == "none" else " (rule unverified)"
        print(f"checksum: {report.status}: {report.message}{rule}")
        return 1 if report.status == "bad" else 0
    return 0


def cmd_list(args) -> int:
    definition = _definition(args)
    for w in definition.warnings:
        print(f"warning: {w}", file=sys.stderr)
    for category, items in sorted(definition.by_category().items()):
        print(f"[{category}]")
        for item in items:
            where = ", ".join(f"0x{s:04X}-0x{e - 1:04X}" for s, e in item.ranges()[:2])
            shape = f" {item.rows}x{item.cols}" if isinstance(item, Table) else ""
            print(f"  {item.kind:<7}{item.id:<28}{item.title}{shape}  ({where})")
    return 0


def cmd_show(args) -> int:
    definition = _definition(args)
    session = RomSession(_load(args.rom, definition, args.half), definition,
                         store_dir=rom_dir())
    editor = session.table(args.table)
    view = "raw" if args.raw or not editor.real_available else "real"
    if editor.math_error and not args.raw:
        print(f"note: {editor.math_error}; showing raw values", file=sys.stderr)
    xa, ya = editor.axis("x"), editor.axis("y")
    print(f"{editor.title}  [{editor.units or view}]   "
          f"columns: {xa.units or 'index'}, rows: {ya.units or 'index'}")
    text = editor.copy(None, view, with_axes=True).rstrip("\n").split("\n")
    cells = [line.split("\t") for line in text]
    width = max(len(c) for line in cells for c in line) + 1
    for line in cells:
        print("".join(c.rjust(width) for c in line))
    return 0


def cmd_diff(args) -> int:
    definition = _definition(args) if args.definition else None
    a, b = _load(args.a, definition, args.half), _load(args.b, definition, args.half)
    result = diff_roms(a.data, b.data, definition)
    for line in result.summary():
        print(line)
    if args.bytes:
        for start, end in byte_runs(a.data, b.data):
            print(f"  0x{start:04X}-0x{end - 1:04X}  {end - start} bytes  "
                  f"{bytes(a.data[start:end])[:8].hex()} -> {bytes(b.data[start:end])[:8].hex()}"
                  + (" ..." if end - start > 8 else ""))
    if args.table:
        t = result.table(args.table)
        if t is None:
            print(f"{args.table}: no difference")
        else:
            for r in range(t.delta.shape[0]):
                print(" ".join(f"{v:+8.3g}" if c else "       ."
                               for v, c in zip(t.delta[r], t.changed[r])))
    return 0 if result.identical else 1


def cmd_convert(args) -> int:
    definition = load_definition(args.source)
    for w in definition.warnings:
        print(f"warning: {w}", file=sys.stderr)
    target = Path(args.target)
    if target.exists() and not args.force:
        raise RomError(f"{target} already exists (use --force)")
    if target.suffix.lower() == ".xdf":
        save_xdf(definition, target)
    else:
        definition.save(target)
    print(f"wrote {target}: {len(definition.tables)} tables, "
          f"{len(definition.scalars)} scalars, {len(definition.flags)} flags")
    return 0


def cmd_checksum(args) -> int:
    definition = _definition(args)
    rom = _load(args.rom, definition, args.half)
    report = checksum_report(rom.data, definition.checksum)
    print(f"{report.status}: {report.message}")
    if args.fix:
        for offset, blob in checksum_fix(rom.data, definition.checksum):
            rom.data[offset:offset + len(blob)] = blob
        rom.save(args.fix, overwrite=args.force)
        print(f"wrote {args.fix}: {checksum_report(rom.data, definition.checksum).message}")
        return 0
    return 1 if report.status == "bad" else 0


def _burn_image(args):
    definition = _definition(args)
    rom = _load(args.rom, definition, args.half)
    image = build_burn_image(rom.data, definition, layout=args.layout,
                             logical_size=definition.rom_size,
                             allow_signature_mismatch=args.ignore_signature)
    for w in image.warnings:
        print(f"warning: {w}", file=sys.stderr)
    return image


def cmd_burn_image(args) -> int:
    image = _burn_image(args)
    target = Path(args.output)
    if target.exists() and not args.force:
        raise RomError(f"{target} already exists (use --force)")
    target.write_bytes(image.data)
    print(f"wrote {target}: {len(image.data)} bytes, {image.layout}, "
          f"sha256 {image.image_sha256}")
    return 0


def cmd_burn(args) -> int:
    image = _burn_image(args)
    burner = Minipro(args.minipro, args.device, ignore_id_error=args.ignore_id)
    if not args.dry_run:
        print(f"programmer: {burner.programmer()}")
    result = burner.burn(image, dry_run=args.dry_run, erase=not args.no_erase,
                         log_path=None if args.dry_run else rom_dir() / "burns.jsonl",
                         progress=lambda name, i, n: print(f"[{i + 1}/{n}] {name}")
                         if name not in ("done", "failed") else None)
    if args.dry_run:
        for line in result.commands():
            print(line)
    print(result.message)
    print(f"image sha256: {result.image_sha256}")
    return 0 if result.ok else 1


def cmd_demo_rom(args) -> int:
    target = Path(args.output)
    if target.exists() and not args.force:
        raise RomError(f"{target} already exists (use --force)")
    target.write_bytes(build_demo_rom())
    print(f"wrote {target} (synthetic; use with -d demo; never burn it)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hondalog-tune", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, func, help_text, rom=True, definition=True):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=func)
        if rom:
            p.add_argument("rom", help="ROM file (.bin), 32 KB or 64 KB")
            p.add_argument("--half", choices=["lower", "upper"],
                           help="which half of a 64 KB file with two different halves")
        if definition:
            p.add_argument("-d", "--definition", help=".xdf, .toml, or 'demo'")
        p.add_argument("--force", action="store_true", help="overwrite output files")
        return p

    add("info", cmd_info, "size, layout, hash and checksum of a ROM")
    add("list", cmd_list, "tables and scalars in a definition", rom=False)
    p = add("show", cmd_show, "print one table")
    p.add_argument("table")
    p.add_argument("--raw", action="store_true", help="stored numbers, not units")
    p = add("diff", cmd_diff, "compare two ROMs", rom=False)
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--half", choices=["lower", "upper"])
    p.add_argument("--bytes", action="store_true", help="list every changed byte range")
    p.add_argument("--table", help="print the cell differences of one table")
    p = add("convert", cmd_convert, "XDF <-> native TOML definition", rom=False,
            definition=False)
    p.add_argument("source")
    p.add_argument("target")
    p = add("checksum", cmd_checksum, "check or fix the ROM checksum")
    p.add_argument("--fix", metavar="OUT", help="write a corrected copy here")
    for name, func, text in (("burn-image", cmd_burn_image, "write a chip-ready 64 KB file"),
                             ("burn", cmd_burn, "burn an SST27SF512 with minipro")):
        p = add(name, func, text)
        p.add_argument("--layout", choices=["doubled", "upper"], default="doubled",
                       help="doubled: image twice (default); upper: at 0x8000, 0xFF below")
        p.add_argument("--ignore-signature", action="store_true",
                       help="allow a ROM whose signature bytes do not match")
        if name == "burn-image":
            p.add_argument("output")
        else:
            p.add_argument("--dry-run", action="store_true",
                           help="show the minipro commands, run nothing")
            p.add_argument("--minipro", help="path to the minipro binary")
            p.add_argument("--device", default="SST27SF512")
            p.add_argument("--no-erase", action="store_true")
            p.add_argument("--ignore-id", action="store_true",
                           help="pass -y: carry on if the chip ID does not match")
    p = add("demo-rom", cmd_demo_rom, "write the synthetic demo ROM", rom=False,
            definition=False)
    p.add_argument("output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except BurnRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except (RomError, DefinitionError, BurnerError, TableError, MathError, KeyError,
            OSError) as exc:
        text = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        print(f"error: {text}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
