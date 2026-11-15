"""A synthetic ROM matching ``data/demo.toml``, for tests and for trying the
tools. It is not an ECU program and must never be burned."""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np

from .definition import Definition, load_definition
from .rom import Layout, RomImage, checksum_fix
from .session import RomSession

DEMO_DEFINITION = Path(__file__).parent / "data" / "demo.toml"

DEMO_RPM = [800, 1200, 1800, 2400, 3000, 3600, 4200, 5000, 5800, 6600]
DEMO_MAP_KPA = [20, 30, 40, 50, 60, 70, 85, 100]


def demo_definition() -> Definition:
    return load_definition(DEMO_DEFINITION)


def build_demo_rom(seed: int = 28) -> bytes:
    """32 KB of deterministic filler with the demo tables written into it."""
    definition = demo_definition()
    rng = random.Random(seed)
    data = bytearray(rng.randrange(256) for _ in range(definition.rom_size))
    data[0x10:0x16] = b"HLDEMO"
    data[0x100:0x102] = bytes.fromhex("c3f0")
    data[0x4000:0x4200] = bytes(0x200)
    rom = RomImage(data, Layout("plain", len(data), len(data)))
    session = RomSession(rom, definition, store_dir=Path("."))
    fuel = session.table("fuel")
    for i, point in enumerate(DEMO_RPM):
        fuel.set_axis_value("x", i, point)
    for i, kpa in enumerate(DEMO_MAP_KPA):
        fuel.set_axis_value("y", i, kpa)
    rpm = np.array(DEMO_RPM, dtype=float)[None, :]
    load = np.array(DEMO_MAP_KPA, dtype=float)[:, None] / 100.0
    torque = 1.0 - ((rpm - 4200.0) / 6000.0) ** 2
    fuel.set_values(1.2 + 9.0 * load * torque)
    session.table("ignition").set_values(
        np.clip(8.0 + 22.0 * (rpm / 6600.0) + 14.0 * (1.0 - load), -5.0, 31.0))
    session.table("target_lambda").set_values(
        np.where(load > 0.8, 0.86, np.where(load > 0.65, 0.93, 1.0)) + 0.0 * rpm)
    ect = session.table("ect_fuel")
    for i, temp in enumerate([-20, 0, 20, 40, 60, 80, 100, 120]):
        ect.set_axis_value("x", i, temp)
    ect.set_values(np.array([[1.45, 1.30, 1.18, 1.08, 1.02, 1.0, 1.0, 1.03]]))
    session.set_scalar("rev_limit", 6800)
    session.set_scalar("idle_target", 750)
    session.set_flag("closed_loop", True)
    session.set_flag("knock_retard", True)
    out = bytearray(session.data)
    for offset, blob in checksum_fix(out, definition.checksum):
        out[offset:offset + len(blob)] = blob
    return bytes(out)
