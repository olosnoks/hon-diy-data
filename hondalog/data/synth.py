"""Synthetic logs for tests and benchmarks: a plausible drive, no car needed."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from .container import LogWriter
from .model import Column

FAST = (("ecu/00+2", 0x00, 2, ("rpm",)),
        ("ecu/12+4", 0x12, 4, ("map", "baro", "tps", "o2")),
        ("ecu/24+3", 0x24, 3, ("inj", "ign")))
SLOW = ("ecu/08+16", 0x08, 16, ("ect", "iat", "batt", "closed_loop"))
UNITS = {"rpm": "rpm", "map": "kPa", "baro": "kPa", "tps": "%", "o2": "V", "inj": "ms",
         "ign": "° BTDC", "ect": "°C", "iat": "°C", "batt": "V"}
RAILS = ("vbatt", "ecu_b", "pump", "ig1")


def drive(t: np.ndarray, rng: np.random.Generator) -> dict[str, np.ndarray]:
    """Channel values for the times in ``t``: warm-up, idle, pulls, cruise."""
    phase = t / 37.0
    tps = np.clip(18 + 30 * np.sin(phase) + 25 * np.sin(phase * 2.7 + 1) + rng.normal(0, 1.5, len(t)), 0, 100)
    rpm = np.clip(800 + 55 * tps + 400 * np.sin(t / 11.0) + rng.normal(0, 25, len(t)), 650, 7200)
    map_ = np.clip(28 + 0.72 * tps + rng.normal(0, 1.2, len(t)), 18, 101)
    ect = np.clip(20 + t / 6.0, None, 88) + rng.normal(0, 0.3, len(t))
    return {
        "rpm": rpm, "map": map_, "baro": np.full(len(t), 99.5), "tps": tps,
        "o2": np.clip(0.45 + 0.4 * np.sign(np.sin(t * 3.1)) + rng.normal(0, 0.03, len(t)), 0, 1),
        "inj": 2.2 + map_ * 0.05 + rng.normal(0, 0.05, len(t)),
        "ign": 14 + rpm / 400.0 - map_ * 0.08,
        "ect": ect, "iat": 24 + rng.normal(0, 0.2, len(t)),
        "batt": 13.9 + rng.normal(0, 0.05, len(t)),
        "closed_loop": (ect > 60).astype(float),
    }


def write_synthetic(path: str | os.PathLike, duration_s: float = 3600.0, *, fast_hz: float = 16.0,
                    slow_hz: float = 1.3, blackbox_hz: float = 50.0, markers: int = 12,
                    seed: int = 1) -> Path:
    """Write a multi-rate log: three fast K-line blocks, one slow block with
    raw bytes, and black box rail frames, all on one timebase."""
    rng = np.random.default_rng(seed)
    path = Path(path)
    meta = {"format": "hondalog synthetic", "started": "2026-11-15T08:00:00", "ecu": "P2P (synthetic)",
            "ecu_id": "SYNTH", "address_map_version": "synthetic-1", "started_unix": 1794729600.0}
    with LogWriter(path, meta, background=False) as writer:
        n_fast = int(duration_s * fast_hz)
        base = np.arange(n_fast) / fast_hz + rng.uniform(0, 0.004, n_fast)
        for k, (name, addr, length, channels) in enumerate(FAST):
            t = base + k * 0.018                # each read has its own timestamp
            values = drive(t, rng)
            cols = [Column(c, "f4", UNITS.get(c, "")) for c in channels]
            writer.add_stream(name, cols, source="ecu", raw_len=length, attrs={"addr": addr, "length": length})
            writer.append_columns(name, t, {c: values[c] for c in channels},
                                  rng.integers(0, 256, (len(t), length), dtype=np.uint8))
        name, addr, length, channels = SLOW
        t = np.arange(int(duration_s * slow_hz)) / slow_hz + 0.05
        values = drive(t, rng)
        cols = [Column(c, "f4", UNITS.get(c, ""), kind="flag" if c == "closed_loop" else "value")
                for c in channels]
        writer.add_stream(name, cols, source="ecu", raw_len=length, attrs={"addr": addr, "length": length})
        writer.append_columns(name, t, {c: values[c] for c in channels},
                              rng.integers(0, 256, (len(t), length), dtype=np.uint8))
        if blackbox_hz:
            t = np.arange(int(duration_s * blackbox_hz)) / blackbox_hz
            cols = [Column(f"{rail}_{stat}", "f4", "V") for rail in RAILS for stat in ("min", "max", "mean")]
            writer.add_stream("blackbox/rails", cols, source="blackbox")
            frame = {}
            for rail in RAILS:
                mean = 13.8 + rng.normal(0, 0.04, len(t))
                frame[f"{rail}_mean"] = mean
                frame[f"{rail}_min"] = mean - np.abs(rng.normal(0, 0.05, len(t)))
                frame[f"{rail}_max"] = mean + np.abs(rng.normal(0, 0.05, len(t)))
            writer.append_columns("blackbox/rails", t, frame)
        for i in range(markers):
            writer.event(duration_s * (i + 0.5) / markers, "marker", f"MARK {i + 1}")
    return path
