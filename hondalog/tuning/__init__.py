"""ROM tuning for the chipped P28: definitions, ROM files, table editing,
ROM compare, live table cursor, wideband auto-tune, chip burning, emulator.

No Qt in here. The UI imports from this package; start with
:class:`RomSession` and work outward:

    from hondalog import tuning
    definition = tuning.load_definition("p28.xdf")        # or a native .toml
    session = tuning.RomSession.open("stock.bin", definition)
    fuel = session.table("fuel")                          # a TableEditor
    fuel.percent((0, 0, 3, 3), +5)
    session.undo()
    session.save("tuned.bin")
    image = session.burn_image()                          # refuses a bad checksum
    tuning.Minipro().burn(image, dry_run=True)
"""

from .autotune import (AutoTuneResult, AutoTuneSettings, afr_to_lambda, analyze,
                       analyze_log, lambda_to_afr, trim_ratio)
from .binning import AxisError, cell_stat, knock_map
from .burner import BurnerError, BurnResult, Minipro, find_minipro, read_burn_log
from .definition import (Axis, Category, ChecksumSpec, Definition, DefinitionError, Flag,
                         Patch, PatchEntry, Scalar, Signature, Storage, Table,
                         load_definition, loads_definition)
from .diff import RomDiff, TableDiff, byte_runs, compare_many, diff_roms, table_stack
from .mathexpr import Conversion, Expression, MathError
from .ostrich import EmulatorError, Ostrich, OstrichSim
from .rom import (CHIP_27SF512_SIZE, P28_ROM_SIZE, BurnImage, BurnRefused, ChecksumReport,
                  Layout, RomError, RomImage, build_burn_image, checksum_fix,
                  checksum_report, chip_image, detect_layout)
from .session import RomSession, SaveResult, rom_dir
from .table import AxisValues, EditResult, TableEditor, TableError, parse_clipboard
from .trace import CursorPosition, TableTracer
from .xdf import dumps_xdf, load_xdf, loads_xdf, save_xdf

__all__ = [
    "AutoTuneResult", "AutoTuneSettings", "Axis", "AxisError", "AxisValues", "BurnImage",
    "BurnRefused", "BurnResult", "BurnerError", "CHIP_27SF512_SIZE", "Category",
    "ChecksumReport", "ChecksumSpec", "Conversion", "CursorPosition", "Definition",
    "DefinitionError", "EditResult", "EmulatorError", "Expression", "Flag", "Layout",
    "MathError", "Minipro", "Ostrich", "OstrichSim", "P28_ROM_SIZE", "Patch",
    "PatchEntry", "RomDiff", "RomError", "RomImage", "RomSession", "SaveResult",
    "Scalar", "Signature", "Storage", "Table", "TableDiff", "TableEditor", "TableError",
    "TableTracer", "afr_to_lambda", "analyze", "analyze_log", "build_burn_image",
    "byte_runs", "cell_stat", "checksum_fix", "checksum_report", "chip_image",
    "compare_many", "detect_layout", "diff_roms", "dumps_xdf", "find_minipro",
    "knock_map", "lambda_to_afr", "load_definition", "load_xdf", "loads_definition",
    "loads_xdf", "parse_clipboard", "read_burn_log", "rom_dir", "save_xdf",
    "table_stack", "trim_ratio",
]
