"""Shared bits for the tuning tests."""

from pathlib import Path

from hondalog.tuning import RomImage, RomSession
from hondalog.tuning.demo import build_demo_rom, demo_definition

FIXTURES = Path(__file__).parent / "fixtures" / "tuning"
SAMPLE_XDF = FIXTURES / "sample.xdf"


def demo_session(tmp_path) -> RomSession:
    rom = RomImage.from_bytes(build_demo_rom())
    return RomSession(rom, demo_definition(), store_dir=tmp_path / "store", name="demo")
