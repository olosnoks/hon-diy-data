"""Synthetic drives for tests and benchmarks: a plausible drive, no car needed."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Sequence

import numpy as np

from .db import DriveRef
from .model import Column
from .store import Store

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
                    seed: int = 1, started_unix: float = 1794729600.0, gps_hz: float = 0.0,
                    gps_start_m: float = 0.0, stalls: Sequence[float] = (), stall_dead_s: float = 25.0,
                    captures: Sequence[tuple[float, str, str]] = ()) -> DriveRef:
    """Write a multi-rate drive into the database ``path``: three fast K-line
    blocks, one slow block with raw bytes, and black box rail frames, all on
    one timebase. The rails are in millivolt steps, as the box reports them.

    ``gps_hz``: also a ``gps`` stream, the handheld's fixes, driving the
    Pearl, MS loop (``hondalog.geo.route``) from ``gps_start_m`` metres along it.
    ``stalls``: session seconds at which the engine dies for ``stall_dead_s``
    (RPM reads 0, the car rolls to a halt), each with a stall capture (30 s
    before, 10 s after) as the stall catcher saves them. ``captures``: more
    captures, (t, kind, reason) such as (300, "capture", "manual")."""
    rng = np.random.default_rng(seed)

    def dead(t: np.ndarray) -> np.ndarray:
        out = np.zeros(len(t), dtype=bool)
        for ts in stalls:
            out |= (t >= ts) & (t < ts + stall_dead_s)
        return out
    meta = {"format": "hondalog synthetic", "ecu": "P2P (synthetic)", "ecu_id": "SYNTH",
            "address_map_version": "synthetic-1", "started_unix": started_unix, "epoch_unix": started_unix}
    with Store(Path(path), background=False, fsync=False) as store:
        writer = store.begin_drive(meta)
        n_fast = int(duration_s * fast_hz)
        base = np.arange(n_fast) / fast_hz + rng.uniform(0, 0.004, n_fast)
        for k, (name, addr, length, channels) in enumerate(FAST):
            t = base + k * 0.018                # each read has its own timestamp
            values = drive(t, rng)
            if stalls:
                off = dead(t)
                values["rpm"] = np.where(off, 0.0, values["rpm"])
                values["inj"] = np.where(off, 0.0, values["inj"])
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
                frame[f"{rail}_mean"] = np.round(mean, 3)
                frame[f"{rail}_min"] = np.round(mean - np.abs(rng.normal(0, 0.05, len(t))), 3)
                frame[f"{rail}_max"] = np.round(mean + np.abs(rng.normal(0, 0.05, len(t))), 3)
            writer.append_columns("blackbox/rails", t, frame)
        if gps_hz:
            from ..geo import route
            from ..geo.track import GPS_STREAM, gps_columns
            t, lat, lon, kmh, heading = route.drive(route.PEARL_LOOP, duration_s, hz=gps_hz, seed=seed,
                                                    start_m=gps_start_m,
                                                    stops_at={ts: stall_dead_s for ts in stalls})
            t = t + 0.31                        # the receiver's own phase
            writer.add_stream(GPS_STREAM, gps_columns(), source="gps", storage="rows")
            sats = np.clip(np.round(9 + 2 * np.sin(t / 97.0) + rng.normal(0, 0.6, len(t))), 4, 14)
            writer.append_columns(GPS_STREAM, t, {
                "gps_lat": lat, "gps_lon": lon, "gps_speed": np.round(kmh, 1), "gps_heading": np.round(heading),
                "gps_sats": sats, "gps_fix": np.ones(len(t)), "gps_hdop": np.round(0.8 + 0.4 * (12 - sats) / 8, 1),
                "gps_utc": np.floor(started_unix + t)})
        for i in range(markers):
            writer.event(duration_s * (i + 0.5) / markers, "marker", f"MARK {i + 1}")
        for ts in stalls:
            writer.event(ts, "trigger", "STALL (rpm 0)")
            writer.capture("stall", ts, max(0.0, ts - 30.0), min(duration_s, ts + 10.0), rule="stall",
                           reason="rpm", label="STALL (rpm 0)", message="rpm dropped to 0", severity="critical",
                           wall=datetime.fromtimestamp(started_unix + ts),
                           meta={"trigger_time_s": f"{ts:.3f}", "stall_time_s": f"{ts:.3f}",
                                 "trigger_reason": "rpm", "stall_trigger": "rpm dropped to 0"})
        for ts, kind, reason in captures:
            writer.event(ts, "trigger", kind.upper())
            writer.capture(kind, ts, max(0.0, ts - 30.0), min(duration_s, ts + 10.0), rule=reason,
                           reason=reason, label=kind.upper(), message=f"{reason} capture",
                           wall=datetime.fromtimestamp(started_unix + ts),
                           meta={"trigger_time_s": f"{ts:.3f}", "trigger_reason": reason})
        writer.end()
    return writer.ref


#: where the fixture's stalls happen: (stretch of the Pearl loop, fraction along it)
FIXTURE_STALLS = (("rough stretch", 0.35), ("rough stretch", 0.6), ("us 80", 0.5), ("rough stretch", 0.45))


def write_gps_drives(path: str | os.PathLike, drives: int = 4, duration_s: float = 900.0, *,
                     gps_hz: float = 1.0, fast_hz: float = 4.0, slow_hz: float = 1.0, blackbox_hz: float = 0.0,
                     started_unix: float = 1794729600.0, seed: int = 11) -> list[DriveRef]:
    """Several drives around the Pearl, MS loop with GPS, each with one stall
    capture: three of the four on the same rough stretch of road (the kind of
    cluster that points at a loose connection shaken by bumps), one on the
    US 80-like road. The second drive also has a manual capture. For tests,
    the screenshots and the UI benchmark."""
    from ..geo import route
    refs = []
    loop = route.PEARL_LOOP
    for k in range(drives):
        where, frac = FIXTURE_STALLS[k % len(FIXTURE_STALLS)]
        a, b = loop.stretches[where]
        start = (k * 1700.0) % loop.length_m
        target = a + (b - a) * frac
        target += loop.length_m if target < start + 200.0 else 0.0
        t_stall = route.time_at(loop, target, seed=seed + k, hz=gps_hz, start_m=start)
        t_stall = min(t_stall, duration_s - 60.0)
        extra = ((min(duration_s - 20.0, 120.0), "capture", "manual"),) if k == 1 else ()
        refs.append(write_synthetic(path, duration_s, fast_hz=fast_hz, slow_hz=slow_hz, blackbox_hz=blackbox_hz,
                                    markers=2, seed=seed + k, started_unix=started_unix + k * 86400.0 * 3,
                                    gps_hz=gps_hz, gps_start_m=start, stalls=(round(t_stall, 2),),
                                    captures=extra))
    return refs
