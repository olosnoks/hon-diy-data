"""Find unknown ECU bytes by stimulus: one owner action at a time, every RAM
address 0x00-0xFF swept before, during and after, and a report of which bytes
and bits answered.

    python tools/poke_test.py list                       # the steps and how long they take
    python tools/poke_test.py run --dry-run --auto       # simulator, no cable, no prompts
    python tools/poke_test.py run --port COM21           # the car (takes the cable, gives it back)
    python tools/poke_test.py analyse poke-runs/20261126-221500

Read-only: the map's wake frame and Honda read requests (20 05 addr len cksum)
through hondalog's own Link with its read-only guard on. Never the expert
unlock, never an OBD2 service.

On a real port the tool takes the cable from the app the way
docs/analysis/2026-11-26/work-sweep/take-cable.ps1 does (tools/.watch-pause,
then a graceful close of the app window) and always gives it back (deletes the
pause file) on the way out, Ctrl-C included. It refuses to start if the pause
file is already there or another hondalog-log / probe / sweep process runs.

Run folder poke-runs/<timestamp>/:
    reads.csv      every request: wall, t, sweep, addr, len, ok, error, hex, attempts
    wide.csv       one line per sweep: wall, t, t_end, sweep, mode, b00..bFF (blank = not read / no reply)
    markers.json   the steps, every phase (pre, wait, settle, hold, undo, rest) and owner marks
    meta.json      port, framing, address labels, link stats, cable take/give times
    report.md      the analysis (again any time: poke_test.py analyse <folder>)
    findings.json  the same, machine-readable
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import tomllib
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

ROOT = Path(__file__).resolve().parent.parent
PAUSE_FILE = ROOT / "tools" / ".watch-pause"
RUNS_DIR = ROOT / "poke-runs"
FORBIDDEN_PORTS = {"COM23"}            # the CYD handheld: never touched
SIM_PREFIX = "sim:"
FULL_BLOCKS: tuple[tuple[int, int], ...] = tuple((a, 16) for a in range(0x00, 0x100, 16))
FLAG_ADDRS = (0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0xA4)

# ---------------------------------------------------------------- steps --


@dataclass
class Step:
    """One owner action. Timeline:

    [setup prompt -> setup_rest_s (measured rest, once)]
    repeat x: [pre prompt -> pre_s] -> prompt (Enter) -> settle_s -> hold_s (measured)
              -> [undo prompt (Enter) -> undo_settle_s] -> rest_s (hands off, measured)
    [teardown prompt (once)]
    """

    name: str
    prompt: str
    settle_s: float = 3.0
    hold_s: float = 15.0
    undo: str = ""                 # what puts it back; "" = nothing to undo
    undo_settle_s: float = 3.0
    rest_s: float = 10.0
    repeat: int = 1
    engine: str = "running"        # engine state during the hold: running | off
    baseline: bool = False         # the hold is itself a hands-off baseline window
    custom: bool = False           # prompt asked for at run time
    optional: bool = False         # asked whether to do it
    trans: str = ""                # "", "auto" or "manual": only for that gearbox
    note: str = ""                 # shown with the step (and when it is skipped)
    prompts: list[str] = field(default_factory=list)   # per repetition, cycling (overrides prompt)
    undos: list[str] = field(default_factory=list)     # per repetition, cycling (overrides undo)
    setup: str = ""                # once before the first repetition
    setup_rest_s: float = 0.0      # measured rest after the setup (the step's own baseline)
    teardown: str = ""             # once after the last repetition
    pre: str = ""                  # before every repetition (e.g. key off), then pre_s
    pre_s: float = 0.0
    mark: str = ""                 # during the hold the owner presses Enter when this happens
    fast: str = ""                 # "0x00-0x1F": read only that range, back to back, in this step
    sim: str = ""                  # built-in simulator actions (sim: ports only)
    sim_undo: str = ""
    sim_pre: str = ""

    def prompt_for(self, rep: int) -> str:
        return self.prompts[(rep - 1) % len(self.prompts)] if self.prompts else self.prompt

    def undo_for(self, rep: int) -> str:
        return self.undos[(rep - 1) % len(self.undos)] if self.undos else self.undo

    def duration_s(self) -> float:
        one = self.pre_s + self.settle_s + self.hold_s + self.rest_s
        if self.undo or self.undos:
            one += self.undo_settle_s
        return self.setup_rest_s + one * self.repeat

    def prompt_count(self) -> int:
        per = 1 + (1 if self.pre else 0) + (1 if (self.undo or self.undos) else 0)
        return per * self.repeat + (1 if self.setup else 0) + (1 if self.teardown else 0)

    def scaled(self, k: float) -> "Step":
        d = asdict(self)
        for key in ("settle_s", "hold_s", "undo_settle_s", "rest_s", "setup_rest_s", "pre_s"):
            d[key] = d[key] * k
        return Step(**d)


STEP_FIELDS = {f.name for f in fields(Step)}
TIME_FIELDS = ("settle_s", "hold_s", "undo_settle_s", "rest_s", "setup_rest_s", "pre_s")
STR_FIELDS = ("undo", "note", "setup", "teardown", "pre", "mark", "fast", "trans",
              "sim", "sim_undo", "sim_pre")
SIM_ACTIONS = {"", "ac_on", "ac_off", "brake_on", "brake_off", "rev", "rev_hold",
               "rev_release", "key_off", "key_on", "stall", "start", "load_on", "load_off"}

ENGINE_OFF_BANNER = "*** ENGINE OFF STEP: the engine will be stopped ***"

DEFAULT_STEPS: list[Step] = [
    Step("baseline",
         "Engine warm and idling, in P / neutral, parking brake on. A/C off, lights off, "
         "blower off, rear defogger off, wheel straight, feet off the pedals. Hands off.",
         settle_s=0, hold_s=20, rest_s=0, baseline=True),
    Step("ac", "Turn the A/C ON (A/C button lit; switch the blower on if it is off).",
         settle_s=5, hold_s=20, undo="Turn the A/C OFF (and the blower back off).",
         undo_settle_s=5, rest_s=15, sim="ac_on", sim_undo="ac_off"),
    Step("headlights", "Turn the HEADLIGHTS ON (low beam).",
         settle_s=3, hold_s=15, undo="Turn the headlights OFF.", undo_settle_s=3, rest_s=10),
    Step("defogger", "Turn the REAR DEFOGGER ON (button lit).",
         settle_s=3, hold_s=15, undo="Turn the rear defogger OFF.", undo_settle_s=3, rest_s=10),
    Step("blower", "Turn the BLOWER to MAXIMUM (A/C stays OFF).",
         settle_s=3, hold_s=15, undo="Turn the blower OFF.", undo_settle_s=3, rest_s=10),
    Step("elec_load", "ALL AT ONCE: HIGH BEAMS on + REAR DEFOGGER on + BLOWER max. "
         "Press Enter the moment the last one is on.",
         settle_s=1, hold_s=10, undo="ALL OFF: high beams, defogger, blower.",
         undo_settle_s=1, rest_s=20, repeat=5, fast="0x00-0x1F",
         setup="High beams, rear defogger and blower all OFF. This step reads only "
               "0x00-0x1F, as fast as the line allows (for the 0x0C b0 blink).",
         setup_rest_s=20, sim="load_on", sim_undo="load_off"),
    Step("brake", "Press the BRAKE PEDAL and HOLD it down.",
         settle_s=0, hold_s=10, undo="Let go of the brake pedal.", undo_settle_s=0, rest_s=10,
         repeat=2, sim="brake_on", sim_undo="brake_off"),
    Step("steering", "Turn the steering wheel to FULL LOCK and hold it there.",
         prompts=["Turn the steering wheel to FULL LOCK LEFT and hold it there.",
                  "Turn the steering wheel to FULL LOCK RIGHT and hold it there."],
         settle_s=0, hold_s=5, undo="Back to CENTRE, let go of the wheel.", undo_settle_s=0,
         rest_s=10, repeat=6,
         note="Power steering pressure switch (0x08 b2): left, centre, right, centre, x3."),
    Step("gear_auto", "Shift the selector.",
         prompts=["Foot on the brake: shift to D.", "Foot on the brake: shift to R."],
         undos=["Shift to N.", "Shift to P."],
         settle_s=1, hold_s=5, undo_settle_s=1, rest_s=8, repeat=2, trans="auto",
         setup="Parking brake ON, foot on the BRAKE pedal, selector in P. Keep your foot on "
               "the brake for the whole step.",
         setup_rest_s=10, teardown="Selector in P. You can take your foot off the brake.",
         note="Gear inputs (0x08 b4-b7, 0x0A b4)."),
    Step("gear_manual", "Push the CLUTCH pedal all the way down and hold it.",
         settle_s=1, hold_s=5, undo="Let the clutch pedal up.", undo_settle_s=1, rest_s=8,
         repeat=3, trans="manual",
         setup="Parking brake ON, gearbox in NEUTRAL, feet off the pedals.", setup_rest_s=10,
         note="Clutch switch (0x08 b4-b7, 0x0A b4)."),
    Step("blip", "In P / neutral: when you press Enter, BLIP the throttle once to about "
         "2000 rpm and let go.",
         settle_s=0, hold_s=6, rest_s=10, repeat=3, sim="rev"),
    Step("rpm3000", "In P / neutral, parking brake on: raise the engine to about 3000 rpm "
         "and HOLD it steady.",
         settle_s=3, hold_s=10, undo="Let go of the throttle.", undo_settle_s=5, rest_s=15,
         sim="rev_hold", sim_undo="rev_release"),
    Step("keyon", "Turn the key to ON (II) WITHOUT starting. Press Enter the instant it is on.",
         pre="KEY OFF: turn the key to OFF (the engine stops). The ECU goes silent; that is "
             "expected. Press Enter once the key is off.",
         pre_s=10, settle_s=0, hold_s=30,
         mark="Press Enter the moment the MIL (check engine light) goes out",
         undo="START the engine and let it idle. Press Enter once it runs.",
         undo_settle_s=20, rest_s=20, engine="off",
         note="Key-on, engine off: 0x0A b7, MIL 0x0B b5, fuel pump prime 0x0B b0.",
         sim_pre="key_off", sim="key_on", sim_undo="start"),
    Step("engine_stop", "ENGINE STOP, KEY STAYS ON: parking brake ON, foot on the brake, "
         "1st gear, let the clutch out slowly until the engine stalls. LEAVE THE KEY ON. "
         "Press Enter once it has stalled.",
         settle_s=2, hold_s=15,
         undo="Clutch down, NEUTRAL, then START the engine. Press Enter once it runs.",
         undo_settle_s=20, rest_s=20, engine="off", optional=True, trans="manual",
         note="0x0B b6 with the key on. Manual gearbox only: an automatic cannot be stalled "
              "safely, so this step is skipped there.",
         sim="stall", sim_undo="start"),
    Step("scs", "Turn the key to ON (II) WITHOUT starting. Press Enter as soon as it is on.",
         pre="KEY OFF (the engine stops). Fit the SCS service-check jumper in the 2-pin "
             "service check connector. Press Enter when it is in.",
         pre_s=3, settle_s=2, hold_s=10,
         undo="Key OFF, REMOVE the jumper, then START the engine. Press Enter once it runs.",
         undo_settle_s=20, rest_s=20, engine="off", optional=True,
         note="SCS (0x09 b3). Only if you have the service-check jumper.",
         sim_pre="key_off", sim="key_on", sim_undo="start"),
    Step("custom", "(asked when the step comes up)",
         settle_s=3, hold_s=15, undo="Undo it.", undo_settle_s=3, rest_s=10, custom=True,
         optional=True),
]

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_FAST_RE = re.compile(r"^\s*0x([0-9A-Fa-f]{1,2})\s*-\s*0x([0-9A-Fa-f]{1,2})\s*$")


class StepError(ValueError):
    pass


def fast_blocks(spec: str, max_len: int = 16) -> tuple[tuple[int, int], ...]:
    """'0x00-0x1F' -> ((0x00, 16), (0x10, 16)). '' -> the full sweep."""
    if not spec:
        return FULL_BLOCKS
    m = _FAST_RE.match(spec)
    if not m:
        raise StepError(f"fast must look like \"0x00-0x1F\" (got {spec!r})")
    lo, hi = int(m.group(1), 16), int(m.group(2), 16)
    if hi < lo:
        raise StepError(f"fast range {spec!r} runs backwards")
    out = []
    a = lo
    while a <= hi:
        n = min(max_len, hi - a + 1)
        out.append((a, n))
        a += n
    return tuple(out)


def _check_step(d: dict[str, Any], where: str) -> Step:
    unknown = set(d) - STEP_FIELDS
    if unknown:
        raise StepError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    name = d.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise StepError(f"{where}: name must be letters, digits, _ or - (got {name!r})")
    where = f"{where} ({name})"
    if not isinstance(d.get("prompt"), str) or not d["prompt"].strip():
        raise StepError(f"{where}: prompt is required")
    for key in TIME_FIELDS:
        if key not in d:
            continue
        v = d[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v > 600:
            raise StepError(f"{where}: {key} must be a number of seconds 0-600")
        d[key] = float(v)
    if d.get("hold_s", 1) <= 0:
        raise StepError(f"{where}: hold_s must be > 0")
    rep = d.get("repeat", 1)
    if isinstance(rep, bool) or not isinstance(rep, int) or not 1 <= rep <= 20:
        raise StepError(f"{where}: repeat must be an integer 1-20")
    if d.get("engine", "running") not in ("running", "off"):
        raise StepError(f"{where}: engine must be \"running\" or \"off\"")
    for key in STR_FIELDS:
        if not isinstance(d.get(key, ""), str):
            raise StepError(f"{where}: {key} must be a string")
    for key in ("prompts", "undos"):
        v = d.get(key, [])
        if not isinstance(v, list) or not all(isinstance(s, str) and s.strip() for s in v):
            raise StepError(f"{where}: {key} must be a list of non-empty strings")
    for key in ("baseline", "custom", "optional"):
        if not isinstance(d.get(key, False), bool):
            raise StepError(f"{where}: {key} must be true or false")
    if d.get("trans", "") not in ("", "auto", "manual"):
        raise StepError(f"{where}: trans must be \"auto\", \"manual\" or left out")
    for key in ("sim", "sim_undo", "sim_pre"):
        if d.get(key, "") not in SIM_ACTIONS:
            raise StepError(f"{where}: {key} must be one of "
                            f"{', '.join(sorted(a for a in SIM_ACTIONS if a))}")
    fast_blocks(d.get("fast", ""))
    has_undo = bool(d.get("undo") or d.get("undos"))
    if d.get("engine") == "off" and not has_undo:
        raise StepError(f"{where}: an engine-off step needs an undo "
                        "(how to start the engine again)")
    if d.get("baseline") and has_undo:
        raise StepError(f"{where}: a baseline step has nothing to undo")
    if d.get("setup_rest_s") and not d.get("setup"):
        raise StepError(f"{where}: setup_rest_s needs a setup prompt")
    if d.get("pre_s") and not d.get("pre"):
        raise StepError(f"{where}: pre_s needs a pre prompt")
    return Step(**d)


def parse_steps(text: str, base: Sequence[Step] = DEFAULT_STEPS) -> list[Step]:
    """Parse a steps TOML. The [[step]] entries replace the default list; an
    entry whose name matches a default step starts from that step and
    overrides only the keys it gives. ``include_defaults = true`` keeps the
    default list and applies the entries as overrides / additions instead."""
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise StepError(f"not valid TOML: {exc}") from None
    extra = set(doc) - {"step", "include_defaults"}
    if extra:
        raise StepError(f"unknown top-level key(s) {', '.join(sorted(extra))}; use [[step]]")
    entries = doc.get("step", [])
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise StepError("[[step]] entries expected")
    keep = doc.get("include_defaults", False)
    if not isinstance(keep, bool):
        raise StepError("include_defaults must be true or false")
    if not entries and not keep:
        raise StepError("no [[step]] entries")
    by_name = {s.name: s for s in base}
    out: list[Step] = list(base) if keep else []
    seen: set[str] = set()
    for i, e in enumerate(entries, 1):
        start = asdict(by_name[e["name"]]) if e.get("name") in by_name else {}
        step = _check_step({**start, **e}, f"step {i}")
        if step.name in seen:
            raise StepError(f"step {i}: name {step.name!r} used twice")
        seen.add(step.name)
        idx = next((j for j, s in enumerate(out) if s.name == step.name), None)
        if idx is not None:
            out[idx] = step
        else:
            out.append(step)
    return out


def select_steps(steps: Sequence[Step], names: str | None) -> list[Step]:
    if not names:
        return list(steps)
    want = [n.strip() for n in names.split(",") if n.strip()]
    by_name = {s.name: s for s in steps}
    missing = [n for n in want if n not in by_name]
    if missing:
        raise StepError(f"unknown step(s) {', '.join(missing)}; have {', '.join(by_name)}")
    return [by_name[n] for n in want]


def applicable(steps: Sequence[Step], trans: str) -> list[Step]:
    """Drop the steps for the other gearbox (trans '' keeps all)."""
    return [s for s in steps if not s.trans or not trans or s.trans == trans]


def describe_steps(steps: Sequence[Step]) -> str:
    lines = [f"{'#':>2}  {'step':<12} {'engine':<7} {'reps':>4} {'setup':>5} {'pre':>4} "
             f"{'settle':>6} {'hold':>5} {'undo':>5} {'rest':>5} {'total':>6}  notes"]
    total = opt = 0.0
    prompts = 0
    for i, s in enumerate(steps, 1):
        tags = [t for t in ("optional" if s.optional else "",
                            f"{s.trans} gearbox only" if s.trans else "",
                            f"fast {s.fast}" if s.fast else "",
                            f"mark: {s.mark}" if s.mark else "") if t]
        if s.optional:
            opt += s.duration_s()
        else:
            total += s.duration_s()
            prompts += s.prompt_count()
        undo = s.undo_settle_s if (s.undo or s.undos) else 0
        lines.append(f"{i:>2}  {s.name:<12} {('OFF' if s.engine == 'off' else 'running'):<7} "
                     f"{s.repeat:>4} {s.setup_rest_s:>5.0f} {s.pre_s:>4.0f} {s.settle_s:>6.0f} "
                     f"{s.hold_s:>5.0f} {undo:>5.0f} {s.rest_s:>5.0f} {s.duration_s():>6.0f}  "
                     f"{'; '.join(tags)}")
    lines.append(f"timed total {total:.0f} s ({total / 60:.1f} min) for the required steps, "
                 f"+ {opt:.0f} s if every optional step is done; + {prompts} Enter prompts "
                 f"(about {prompts * 5 / 60:.0f}-{prompts * 10 / 60:.0f} min of owner time)")
    return "\n".join(lines)


# --------------------------------------------------------------- labels --

KNOWN_NOTES = {   # docs/analysis/2026-11-26 findings not (or not fully) in the map
    0x1C: "copy of ELD (0x19)",
    0x27: "rpm band above idle",
    0x29: "IAC command",
    0x2A: "slow learned value",
    0x2F: "~33 s counter after ECT snap-back",
    0x76: "idle target [0]",
    0x77: "idle target [1]",
    0x78: "ECU id / constants",
    0x86: "small signed table (follows STFT)",
    0x9A: "fuel system status",
    0x9C: "calculated load",
    0xA0: "second O2?",
}
KNOWN_BITS = {   # flag-bits.md candidates the map does not name yet
    (0x08, 0): "starter", (0x08, 2): "psp_sw?", (0x08, 7): "gear input?",
    (0x09, 3): "scs?", (0x0A, 4): "gear input?", (0x0A, 7): "ecu_ready?",
    (0x0B, 6): "engine_turning?", (0x0C, 0): "elec load / alt ctrl?", (0xA4, 0): "idle state?",
}


def labels_from_map(amap: Any) -> dict[str, Any]:
    """{'addresses': {'0x08': {'byte': '', 'bits': {'1': 'ac_sw'}}}, 'drivers': {rpm, ect}}."""
    out: dict[str, dict[str, Any]] = {}

    def slot(a: int) -> dict[str, Any]:
        return out.setdefault(f"0x{a:02X}", {"byte": "", "bits": {}})

    for a, note in KNOWN_NOTES.items():
        slot(a)["byte"] = note
    for (a, b), note in KNOWN_BITS.items():
        slot(a)["bits"][str(b)] = note
    drivers: dict[str, Any] = {}
    for ch in getattr(amap, "channels", []):
        m = re.fullmatch(r"\s*bit\(\s*x\s*,\s*(\d)\s*\)\s*", ch.formula or "")
        if m and ch.length == 1:
            slot(ch.addr)["bits"][m.group(1)] = ch.name
            continue
        if ch.name.startswith("raw_"):
            continue
        if ch.length == 1:
            slot(ch.addr)["byte"] = ch.name
        else:
            for i in range(ch.length):
                slot(ch.addr + i)["byte"] = f"{ch.name} [{i}]"
        if ch.name in ("rpm", "ect"):
            drivers[ch.name] = {"addr": ch.addr, "length": ch.length}
    drivers.setdefault("rpm", {"addr": 0x00, "length": 2})
    drivers.setdefault("ect", {"addr": 0x10, "length": 1})
    return {"addresses": out, "drivers": drivers}


DEFAULT_LABELS = {"addresses": {}, "drivers": {"rpm": {"addr": 0, "length": 2},
                                               "ect": {"addr": 0x10, "length": 1}}}


def address_name(labels: dict[str, Any], addr: int, bit: int | None = None) -> str:
    entry = labels.get("addresses", {}).get(f"0x{addr:02X}")
    if not entry:
        return ""
    if bit is not None:
        return entry.get("bits", {}).get(str(bit), "")
    names = [entry.get("byte", "")] + [f"b{k}={v}" for k, v in sorted(entry.get("bits", {}).items())]
    return ", ".join(n for n in names if n)


# ------------------------------------------------------------ recording --


class Recorder:
    """Writes reads.csv, wide.csv, markers.json as they happen."""

    def __init__(self, folder: Path, t0: float) -> None:
        self.folder = folder
        self.t0 = t0
        folder.mkdir(parents=True, exist_ok=True)
        self._reads_f = open(folder / "reads.csv", "w", newline="", encoding="utf-8")
        self._wide_f = open(folder / "wide.csv", "w", newline="", encoding="utf-8")
        self.reads = csv.writer(self._reads_f)
        self.wide = csv.writer(self._wide_f)
        self.reads.writerow(["wall", "t", "sweep", "addr", "len", "ok", "error", "hex", "attempts"])
        self.wide.writerow(["wall", "t", "t_end", "sweep", "mode"] + [f"b{i:02X}" for i in range(256)])
        self.phases: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []
        self.info: dict[str, Any] = {}
        self.lock = threading.Lock()

    def now(self) -> float:
        return time.monotonic() - self.t0

    def phase(self, step: str, rep: int, phase: str, t0: float, t1: float, **extra: Any) -> None:
        with self.lock:
            self.phases.append({"step": step, "rep": rep, "phase": phase,
                                "t0": round(t0, 4), "t1": round(t1, 4),
                                "wall": datetime.now().isoformat(timespec="milliseconds"),
                                **extra})
        self.save_markers()

    def event(self, step: str, rep: int, label: str, t: float) -> None:
        with self.lock:
            self.events.append({"step": step, "rep": rep, "label": label, "t": round(t, 4),
                                "wall": datetime.now().isoformat(timespec="milliseconds")})
        self.save_markers()

    def save_markers(self) -> None:
        with self.lock:
            data = {"steps": self.steps, "phases": self.phases, "events": self.events,
                    **self.info}
        tmp = self.folder / "markers.json.tmp"
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.folder / "markers.json")

    def flush(self) -> None:
        self._reads_f.flush()
        self._wide_f.flush()

    def close(self) -> None:
        for f in (self._reads_f, self._wide_f):
            try:
                f.close()
            except Exception:
                pass
        self.save_markers()


class SimDriver:
    """Plays the owner's actions on the built-in simulator (sim: ports only).
    Only what the simulated ECU models moves; everything else stays put."""

    def __init__(self, model: Any) -> None:
        self.model = model
        self.rev_hold = False
        model.rev_every_s = 0              # no spontaneous revs: they would look like responses
        model.restart_after_s = None       # the engine stays off until a step starts it
        model.next_rev = float("inf")

    def act(self, action: str) -> None:
        m = self.model
        if action == "ac_on":
            m.ac(True)
        elif action == "ac_off":
            m.ac(False)
        elif action == "brake_on":
            m.brake_on = True
        elif action == "brake_off":
            m.brake_on = False
        elif action == "rev":
            m.rev()
        elif action == "rev_hold":
            self.rev_hold = True
            m.rev()
        elif action == "rev_release":
            self.rev_hold = False
        elif action == "key_off":
            m.power_cut()                  # the ECU goes silent, wants the wake frame again
        elif action == "key_on":
            m.powered = True               # key on, engine off; the pump primes for 2 s
            m.running = False
            m.stopped_at = m.clock() - m.t0
        elif action == "stall":
            m.stall()
        elif action == "start":
            m.restart()
        # load_on / load_off: the simulated ECU has no electrical-load input

    def tick(self) -> None:
        if self.rev_hold:
            self.model.rev()


class Sweeper(threading.Thread):
    """Reads ``blocks`` (the full 0x00-0xFF table by default) once per ``period``.

    When the ECU stops answering (key off) it does not give up: it drops to a
    probe (wake frame + one read of 0x00) about twice a second until the ECU
    answers again, then carries on. The cable stays open throughout."""

    SILENT_AFTER = 4

    def __init__(self, link: Any, transport: Any, rec: Recorder, period: float = 1.0,
                 sim: SimDriver | None = None, protocol_error: type = Exception,
                 transport_error: type = Exception, rpm_addr: int = 0) -> None:
        super().__init__(name="sweeper", daemon=True)
        self.link, self.transport, self.rec = link, transport, rec
        self.full_period = period
        self.mode: tuple[str, tuple[tuple[int, int], ...], float] = ("full", FULL_BLOCKS, period)
        self.sim = sim
        self.protocol_error = protocol_error
        self.transport_error = transport_error
        self.rpm_addr = rpm_addr
        self.stop_event = threading.Event()
        self.sweeps = 0
        self.errors: dict[str, int] = {}
        self.reads_ok = 0
        self.reads_failed = 0
        self.silent = False
        self.silent_spells = 0
        self.last_ok_rows = 0
        self.last_rows = 0
        self.last_rpm: float | None = None
        self.crash: BaseException | None = None

    def set_fast(self, blocks: tuple[tuple[int, int], ...] | None) -> None:
        """Read only ``blocks`` back to back (None: back to the full sweep)."""
        self.mode = ("full", FULL_BLOCKS, self.full_period) if blocks is None else \
            ("fast", blocks, 0.0)

    def stop(self) -> None:
        self.stop_event.set()

    def _err(self, name: str) -> None:
        self.errors[name] = self.errors.get(name, 0) + 1

    def run(self) -> None:
        try:
            self._loop()
        except BaseException as exc:      # recorded; the main thread reports it
            self.crash = exc

    def _read(self, addr: int, length: int, vals: list[str], retries: int | None = None) -> bool:
        rec = self.rec
        t = rec.now()
        wall = datetime.now().isoformat(timespec="milliseconds")
        try:
            res = self.link.read_block(addr, length, retries=retries)
        except self.protocol_error as exc:
            self._err(type(exc).__name__)
            self.reads_failed += 1
            rec.reads.writerow([wall, f"{t:.4f}", self.sweeps, f"0x{addr:02X}", length, 0,
                                f"{type(exc).__name__}: {exc}", "", ""])
            return False
        self.reads_ok += 1
        rec.reads.writerow([wall, f"{t:.4f}", self.sweeps, f"0x{addr:02X}", length, 1, "",
                            res.data.hex(" "), res.attempts])
        for i, v in enumerate(res.data):
            vals[addr + i] = str(v)
        return True

    def _loop(self) -> None:
        rec = self.rec
        consecutive = 0
        while not self.stop_event.is_set():
            mode, blocks, period = self.mode
            ts = rec.now()
            wall = datetime.now().isoformat(timespec="milliseconds")
            if self.sim:
                self.sim.tick()
            vals: list[str] = [""] * 256
            ok_rows = rows = 0
            try:
                if self.silent:
                    mode, period = "probe", 0.5
                    try:
                        self.link.reset()
                        self.link.wake()
                    except self.transport_error:
                        raise
                    rows = 1
                    if self._read(blocks[0][0], blocks[0][1], vals, retries=0):
                        ok_rows = 1
                        self.silent = False
                        consecutive = 0
                else:
                    for addr, length in blocks:
                        if self.stop_event.is_set():
                            break
                        rows += 1
                        if self._read(addr, length, vals):
                            ok_rows += 1
                            consecutive = 0
                        else:
                            consecutive += 1
                            if consecutive >= self.SILENT_AFTER:
                                self.silent = True        # key off / ECU asleep: probe instead
                                self.silent_spells += 1
                                consecutive = 0
                                break
            except self.transport_error as exc:
                self._err(type(exc).__name__)
                self._reopen()
            te = rec.now()
            rec.wide.writerow([wall, f"{ts:.4f}", f"{te:.4f}", self.sweeps, mode] + vals)
            rec.flush()
            self.last_ok_rows, self.last_rows = ok_rows, rows
            a = self.rpm_addr
            self.last_rpm = ((int(vals[a]) * 256 + int(vals[a + 1])) / 4
                             if vals[a] and vals[a + 1] else None)
            self.sweeps += 1
            rest = period - (rec.now() - ts)
            if rest > 0:
                self.stop_event.wait(rest)

    def _reopen(self) -> None:
        """The serial port itself failed (USB hiccup): reopen it, never give it up."""
        while not self.stop_event.wait(1.0):
            try:
                self.transport.close()
            except Exception:
                pass
            try:
                self.transport.open()
                self.link.reset()
                self.link.wake()
                return
            except Exception as exc:
                self._err(type(exc).__name__)


# --------------------------------------------------------- cable handling --


def _powershell(script: str, timeout: float = 60) -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=timeout)
    return out.stdout.strip()


def list_processes() -> list[dict[str, Any]]:
    """Every python / hondalog* / powershell process: ProcessId, Name, CommandLine."""
    if os.name != "nt":
        return []
    text = _powershell(
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -match "
        "'^(python|pythonw|hondalog.*|powershell|pwsh)\\.exe$' } | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress")
    if not text:
        return []
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


PORT_USER_RE = re.compile(
    r"hondalog-(log|probe|firstcontact|map|tune)(\.exe)?\b|hondalog\.(logtool|diag|mapping)"
    r"|sweep\.py|take-cable|crosscheck_car|poke_test|cyd_debug", re.IGNORECASE)


def is_app(p: dict[str, Any]) -> bool:
    """The hondalog app, counted the way watch-and-run.ps1 does."""
    name, cmd = (p.get("Name") or "").lower(), p.get("CommandLine") or ""
    return name == "hondalog.exe" or (name == "python.exe" and re.search(r"hondalog\.exe", cmd, re.I)
                                      is not None and "-m " not in cmd)


def other_port_users(procs: Iterable[dict[str, Any]], own: set[int]) -> list[dict[str, Any]]:
    """Processes other than the app (and this one) that may hold the cable."""
    return [p for p in procs if p.get("ProcessId") not in own and not is_app(p)
            and PORT_USER_RE.search(p.get("CommandLine") or "")]


def own_pids() -> set[int]:
    return {os.getpid(), os.getppid()}       # the venv launcher is our parent


def close_app(say: Callable[[str], None], wait_s: int = 30) -> None:
    """CloseMainWindow on the app (as take-cable.ps1), wait, stop it only if it hangs."""
    if os.name != "nt":
        return
    _powershell("Get-Process | Where-Object { $_.MainWindowTitle -match '^hondalog' -and "
                "$_.Path -notlike '*dist*' } | ForEach-Object { [void]$_.CloseMainWindow() }")
    for i in range(wait_s):
        if not [p for p in list_processes() if is_app(p)]:
            say(f"app closed after {i} s")
            return
        time.sleep(1)
    left = [p for p in list_processes() if is_app(p)]
    if left:
        say(f"app still running after {wait_s} s; stopping it")
        for p in left:
            _powershell(f"Stop-Process -Id {int(p['ProcessId'])} -Force -ErrorAction SilentlyContinue")


_console_handler = None


def _guard_console_close(pause: Path) -> None:
    """Closing the console window kills Python without running finally blocks:
    delete the pause file from a console control handler too."""
    global _console_handler
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    def handler(event: int) -> bool:
        if event in (2, 5, 6):            # CLOSE, LOGOFF, SHUTDOWN
            try:
                pause.unlink()
            except OSError:
                pass
        return False                      # Python's own handler (Ctrl-C) still runs

    _console_handler = handler
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)


@contextmanager
def take_cable(port: str, say: Callable[[str], None], pause: Path = PAUSE_FILE,
               procs: Callable[[], list[dict[str, Any]]] = list_processes,
               closer: Callable[[Callable[[str], None]], None] = close_app,
               guard_console: bool = True) -> Iterator[dict[str, str]]:
    """Take the cable from the app; ALWAYS give it back (delete the pause file)."""
    times: dict[str, str] = {}
    if pause.exists():
        raise SystemExit(f"refusing: {pause} already exists (another tool has the cable, or a "
                         "previous run did not finish). Check, delete it by hand, try again.")
    others = other_port_users(procs(), own_pids())
    if others:
        listing = "\n".join(f"  pid {p.get('ProcessId')}: {p.get('CommandLine')}" for p in others)
        raise SystemExit(f"refusing: another program may hold {port}:\n{listing}")
    created = False
    try:
        with open(pause, "x", encoding="utf-8") as f:     # exclusive create
            f.write(f"poke_test pid {os.getpid()} {datetime.now().isoformat()}\n")
        created = True
        times["take"] = datetime.now().isoformat(timespec="milliseconds")
        say(f"took the cable: {pause.name} created; closing the app")
        if guard_console:
            _guard_console_close(pause)
        closer(say)
        yield times
    finally:
        if created:
            try:
                pause.unlink()
            except FileNotFoundError:
                pass
            times["give"] = datetime.now().isoformat(timespec="milliseconds")
            say(f"gave the cable back: {pause.name} deleted (the watcher restarts the app)")


@contextmanager
def _no_cable() -> Iterator[dict[str, str]]:
    yield {}


# ------------------------------------------------------------ console UI --


class Console:
    def __init__(self, auto: bool, beep: bool, status: Callable[[], str]) -> None:
        self.auto = auto
        self.beep_on = beep
        self.status = status

    def beep(self, long: bool = False) -> None:
        if not self.beep_on:
            return
        try:
            import winsound
            winsound.Beep(1200 if long else 880, 400 if long else 150)
        except Exception:
            sys.stdout.write("\a")
            sys.stdout.flush()

    def ask(self, text: str, choices: str = "") -> str:
        """Show a prompt and wait for Enter. Returns what was typed ('' = just Enter)."""
        print(f"\n  >>> {text}")
        if self.auto:
            return ""
        self._drain()
        hint = "Enter when done" + (f"; {choices}" if choices else "")
        try:
            return input(f"      [{hint}] ").strip().lower()
        except EOFError:
            raise KeyboardInterrupt from None

    def yes_no(self, text: str, default: bool = False) -> bool:
        if self.auto:
            return default
        try:
            ans = input(f"  {text} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        except EOFError:
            raise KeyboardInterrupt from None
        return default if not ans else ans.startswith("y")

    @staticmethod
    def _drain() -> None:
        if os.name == "nt":
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getwch()

    @staticmethod
    def _enter_pressed() -> bool:
        if os.name == "nt":
            import msvcrt
            hit = False
            while msvcrt.kbhit():
                if msvcrt.getwch() in ("\r", "\n"):
                    hit = True
            return hit
        import select
        if select.select([sys.stdin], [], [], 0)[0]:
            sys.stdin.readline()
            return True
        return False

    def countdown(self, label: str, seconds: float, alive: Callable[[], None],
                  on_mark: Callable[[], None] | None = None, mark_text: str = "") -> None:
        end = time.monotonic() + seconds
        if on_mark and not self.auto:
            self._drain()
            print(f"      ** {mark_text} **")
        marked = False
        while True:
            alive()
            left = end - time.monotonic()
            if left <= 0:
                break
            if on_mark and not marked and not self.auto and self._enter_pressed():
                on_mark()
                marked = True
                sys.stdout.write("\n      (marked)\n")
            if not self.auto:
                sys.stdout.write(f"\r      {label:<30} {left:4.0f} s   {self.status():<40}")
                sys.stdout.flush()
            time.sleep(min(0.05 if on_mark else 0.25, left))
        if not self.auto:
            sys.stdout.write("\r" + " " * 95 + "\r")
        print(f"      {label}: done")


def run_steps(steps: Sequence[Step], rec: Recorder, ui: Console, sim: SimDriver | None,
              alive: Callable[[], None], sweeper: Sweeper | None = None,
              optional: str = "ask", trans: str = "") -> None:
    rec.steps = [asdict(s) for s in steps]
    rec.save_markers()
    for n, s in enumerate(steps, 1):
        print("\n" + "=" * 78)
        title = f"Step {n}/{len(steps)}: {s.name}" + ("  (optional)" if s.optional else "")
        print(title)
        if s.engine == "off":
            print(f"  {ENGINE_OFF_BANNER}")
        if s.note:
            print(f"  {s.note}")
        if s.trans and trans and s.trans != trans:
            print(f"  skipped: {s.trans} gearbox only")
            rec.phase(s.name, 0, "skipped", rec.now(), rec.now(), why=f"{s.trans} only")
            continue
        if s.optional and not s.custom:
            do = {"yes": True, "no": False}.get(optional)
            if do is None:
                do = ui.yes_no(f"Optional step '{s.name}': do it?")
            if not do:
                print("  skipped")
                rec.phase(s.name, 0, "skipped", rec.now(), rec.now(), why="optional")
                continue
        undo_text = s.undo
        if s.custom:
            if ui.auto or optional == "no":
                print("  custom step skipped")
                rec.phase(s.name, 0, "skipped", rec.now(), rec.now(), why="custom")
                continue
            text = input("  Custom action (what will you do?; blank = skip): ").strip()
            if not text:
                rec.phase(s.name, 0, "skipped", rec.now(), rec.now(), why="custom")
                continue
            undo_text = input("  How is it undone? (blank = nothing to undo): ").strip()
            s = Step(**{**asdict(s), "prompt": text, "undo": undo_text})
            rec.steps[n - 1].update({"prompt": text, "undo": undo_text})
            rec.save_markers()
        print(f"  {s.repeat} x ("
              + (f"pre {s.pre_s:.0f} s, " if s.pre else "")
              + f"settle {s.settle_s:.0f} s, hold {s.hold_s:.0f} s"
              + (f", undo settle {s.undo_settle_s:.0f} s" if (s.undo or s.undos) else "")
              + f", rest {s.rest_s:.0f} s)")
        if s.fast and sweeper is not None:
            sweeper.set_fast(fast_blocks(s.fast))
        try:
            if _run_one(s, rec, ui, sim, alive) == "quit":
                return
        finally:
            if s.fast and sweeper is not None:
                sweeper.set_fast(None)


def _run_one(s: Step, rec: Recorder, ui: Console, sim: SimDriver | None,
             alive: Callable[[], None]) -> str:
    def timed(rep: int, phase: str, secs: float, label: str, **kw: Any) -> None:
        if secs <= 0:
            return
        t = rec.now()
        ui.countdown(label, secs, alive, **kw)
        rec.phase(s.name, rep, phase, t, rec.now())

    if s.setup:
        t = rec.now()
        ans = ui.ask(s.setup, "s = skip step, q = stop the test")
        if ans in ("q", "s"):
            rec.phase(s.name, 0, "quit" if ans == "q" else "skipped", t, rec.now())
            return "quit" if ans == "q" else "skip"
        rec.phase(s.name, 0, "wait", t, rec.now())
        timed(0, "rest", s.setup_rest_s, "rest - hands off (before)")
    for rep in range(1, s.repeat + 1):
        if s.repeat > 1:
            print(f"  -- {rep}/{s.repeat}")
        if s.pre:
            t = rec.now()
            ans = ui.ask(s.pre, "s = skip step, q = stop the test")
            if ans in ("q", "s"):
                rec.phase(s.name, rep, "quit" if ans == "q" else "skipped", t, rec.now())
                return "quit" if ans == "q" else "skip"
            if sim and s.sim_pre:
                sim.act(s.sim_pre)
            rec.phase(s.name, rep, "wait_pre", t, rec.now())
            timed(rep, "pre", s.pre_s, "wait")
        t = rec.now()
        ans = ui.ask(s.prompt_for(rep), "" if s.pre else "s = skip step, q = stop the test")
        if ans == "q":
            rec.phase(s.name, rep, "quit", t, rec.now())
            return "quit"
        if ans == "s" and not s.pre:
            rec.phase(s.name, rep, "skipped", t, rec.now())
            return "skip"
        if sim and s.sim:
            sim.act(s.sim)
        rec.phase(s.name, rep, "wait", t, rec.now(), prompt=s.prompt_for(rep))
        timed(rep, "settle", s.settle_s, "settle (let it change)")
        mark = (lambda r=rep: rec.event(s.name, r, s.mark, rec.now())) if s.mark else None
        timed(rep, "hold", s.hold_s, "HOLD - keep it like this", on_mark=mark, mark_text=s.mark)
        ui.beep(long=True)
        undo = s.undo_for(rep)
        if undo:
            t = rec.now()
            ui.ask(undo)
            if sim and s.sim_undo:
                sim.act(s.sim_undo)
            rec.phase(s.name, rep, "undo_wait", t, rec.now())
            timed(rep, "undo_settle", s.undo_settle_s, "settle after undo")
        timed(rep, "rest", s.rest_s, "rest - hands off")
        ui.beep()
    if s.teardown:
        t = rec.now()
        ui.ask(s.teardown)
        rec.phase(s.name, 0, "wait", t, rec.now())
    return "ok"


def cmd_run(a: argparse.Namespace) -> int:
    from hondalog import protocol
    from hondalog.addressmap import load_user_map
    from hondalog.transport import Link, TransportError, make_transport

    if a.dry_run and not (a.port or "").startswith(SIM_PREFIX):
        a.port = SIM_PREFIX
    if not a.port:
        raise SystemExit("--port is required (COM21 for the car, sim: for the simulator, "
                         "or --dry-run)")
    if a.port.upper() in FORBIDDEN_PORTS:
        raise SystemExit(f"refusing {a.port}: that is the handheld")
    is_sim = a.port.startswith(SIM_PREFIX)
    optional = a.optional or ("no" if a.auto else "ask")
    steps = load_steps(a)
    trans = a.trans or ""
    if not trans and any(s.trans for s in steps):
        if a.auto:
            trans = "auto"
        else:
            while trans not in ("auto", "manual"):
                ans = input("Gearbox: automatic or manual? [a/m] ").strip().lower()
                trans = {"a": "auto", "m": "manual"}.get(ans[:1], "")
    steps = applicable(steps, trans)
    if a.time_scale != 1.0:
        steps = [s.scaled(a.time_scale) for s in steps]
    print(describe_steps(steps))

    amap = load_user_map()
    framing = amap.framing
    labels = labels_from_map(amap)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = Path(a.out) if a.out else RUNS_DIR / stamp
    if folder.exists() and any(folder.iterdir()):
        raise SystemExit(f"{folder} exists and is not empty")
    meta: dict[str, Any] = {
        "tool": "poke_test", "port": a.port, "dry_run": a.dry_run, "auto": a.auto,
        "trans": trans, "optional": optional, "time_scale": a.time_scale, "period_s": a.period,
        "start_wall": datetime.now().isoformat(timespec="milliseconds"),
        "framing": {"baud": framing.baud, "init": framing.init_sequence.hex(" "),
                    "max_read_len": framing.max_read_len},
        "map_version": getattr(amap, "version", ""),
        "labels": labels,
    }

    def say(text: str) -> None:
        print(f"[{datetime.now():%H:%M:%S}] {text}")

    def write_meta() -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    cable = _no_cable() if is_sim else take_cable(a.port, say)
    with cable as cable_times:
        meta["cable"] = cable_times          # the same dict: 'give' lands after the with
        t0 = time.monotonic()
        rec = Recorder(folder, t0)
        rec.info = {"trans": trans}
        tr = make_transport(a.port, framing)
        link = Link(tr, framing)
        if not link.read_only:
            raise SystemExit("the Link is not read-only: refusing")
        sim = SimDriver(tr.model) if is_sim and hasattr(tr, "model") else None
        sweeper = Sweeper(link, tr, rec, period=a.period, sim=sim,
                          protocol_error=protocol.ProtocolError, transport_error=TransportError,
                          rpm_addr=labels["drivers"]["rpm"]["addr"])
        interrupted = False
        try:
            for attempt in range(3):
                try:
                    tr.open()
                    break
                except Exception as exc:
                    if attempt == 2:
                        raise SystemExit(f"cannot open {a.port}: {exc}")
                    say(f"open {a.port} failed ({exc}); retrying")
                    time.sleep(1.5)
            link.reset()
            link.wake()
            say(f"sweeping 0x00-0xFF on {a.port} every {a.period:.1f} s -> {folder}")
            sweeper.start()

            def status() -> str:
                if sweeper.silent:
                    return "ECU silent: probing"
                rpm = sweeper.last_rpm
                return (f"reads {sweeper.last_ok_rows}/{sweeper.last_rows}  rpm "
                        + (f"{rpm:.0f}" if rpm is not None else "--")
                        + f"  sweeps {sweeper.sweeps}")

            def alive() -> None:
                if sweeper.crash is not None:
                    raise RuntimeError(f"sweeper stopped: {sweeper.crash!r}")

            ui = Console(a.auto, not a.no_beep, status)
            while sweeper.sweeps < 2:             # a couple of sweeps before the first prompt
                alive()
                time.sleep(0.1)
            run_steps(steps, rec, ui, sim, alive, sweeper, optional=optional, trans=trans)
        except KeyboardInterrupt:
            interrupted = True
            print("\nCtrl-C: stopping (the run so far is kept)")
        finally:
            sweeper.stop()
            if sweeper.is_alive():
                sweeper.join(timeout=10)
            try:
                tr.close()
            except Exception:
                pass
            rec.close()
            st = link.stats
            meta.update({
                "end_wall": datetime.now().isoformat(timespec="milliseconds"),
                "seconds": round(time.monotonic() - t0, 2),
                "sweeps": sweeper.sweeps, "reads_ok": sweeper.reads_ok,
                "reads_failed": sweeper.reads_failed, "errors": sweeper.errors,
                "silent_spells": sweeper.silent_spells, "interrupted": interrupted,
                "link_stats": {k: getattr(st, k) for k in dir(st) if not k.startswith("_")
                               and isinstance(getattr(st, k), (int, float))},
            })
            if sweeper.crash is not None:
                meta["sweeper_crash"] = repr(sweeper.crash)
            write_meta()
    write_meta()                                  # now with the give-back time
    print(f"\nrun saved in {folder}: {meta['sweeps']} sweeps, {meta['reads_ok']} reads ok, "
          f"{meta['reads_failed']} failed")
    report = analyse_folder(folder, alpha=a.alpha)
    print(f"report: {report}")
    return 0


def load_steps(a: argparse.Namespace) -> list[Step]:
    steps = DEFAULT_STEPS
    if getattr(a, "steps_file", None):
        steps = parse_steps(Path(a.steps_file).read_text(encoding="utf-8"))
    return select_steps(steps, getattr(a, "steps", None))


# -------------------------------------------------------------- analysis --


@dataclass
class Sweep:
    t0: float
    t1: float
    vals: list[int | None]


def _ranks(xs: Sequence[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        r = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def mann_whitney_p(a: Sequence[float], b: Sequence[float]) -> float:
    """Two-sided Mann-Whitney U test, normal approximation with tie correction."""
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return 1.0
    allv = list(a) + list(b)
    r = _ranks(allv)
    u1 = sum(r[:n1]) - n1 * (n1 + 1) / 2
    n = n1 + n2
    counts: dict[float, int] = {}
    for v in allv:
        counts[v] = counts.get(v, 0) + 1
    tie = sum(t ** 3 - t for t in counts.values())
    var = n1 * n2 / 12 * ((n + 1) - tie / (n * (n - 1))) if n > 1 else 0.0
    if var <= 0:
        return 1.0
    z = max(0.0, abs(u1 - n1 * n2 / 2) - 0.5) / math.sqrt(var)
    return math.erfc(z / math.sqrt(2))


def _log_comb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_p(k1: int, n1: int, k2: int, n2: int) -> float:
    """Two-sided Fisher exact test on [[k1, n1-k1], [k2, n2-k2]]."""
    K, N = k1 + k2, n1 + n2
    if K == 0 or K == N or n1 == 0 or n2 == 0:
        return 1.0
    lo, hi = max(0, K - n2), min(K, n1)
    base = _log_comb(N, K)

    def lp(x: int) -> float:
        return _log_comb(n1, x) + _log_comb(n2, K - x) - base

    obs = lp(k1)
    total = sum(math.exp(v) for v in (lp(x) for x in range(lo, hi + 1)) if v <= obs + 1e-7)
    return min(1.0, total)


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) < 5 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return sxy / (sx * sy) if sx and sy else None


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _sd(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


@dataclass
class Finding:
    step: str
    addr: int
    bit: int | None              # None = the byte as a number
    kind: str                    # shift | excursion | edge
    base: str
    during: str
    effect: str
    score: float                 # 0..1, for ranking
    p: float
    n_hold: int
    n_base: int
    indirect: str = ""           # e.g. "rpm r=+0.91": follows a driver that moved in this step
    drift: str = ""              # the rest before and after the step disagree
    name: str = ""

    @property
    def where(self) -> str:
        return f"0x{self.addr:02X}" + (f" b{self.bit}" if self.bit is not None else "")


@dataclass
class StepResult:
    name: str
    engine: str
    prompt: str
    reps_done: int
    n_hold: int
    n_base: int
    drivers: dict[str, str] = field(default_factory=dict)
    driver_moved: dict[str, bool] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    edges: list[Finding] = field(default_factory=list)
    bitfrac: dict[str, tuple[float, float]] = field(default_factory=dict)  # "0x08.2": (rest, hold)
    timeline: list[str] = field(default_factory=list)
    marks: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class Settings:
    alpha: float = 1e-3          # p threshold (256 bytes + 2048 bits are tested per step)
    min_d: float = 1.0           # byte shift: |standardised mean difference|
    min_dp: float = 0.5          # bit shift: change in the share of sweeps with the bit set
    min_exc_frac: float = 0.2    # excursion: share of hold sweeps outside anything seen at rest
    min_exc_n: int = 2           # ... and at least this many sweeps
    exc_p: float = 0.01          # ... and never with p above this
    min_base: int = 15           # rest sweeps wanted (widened to further rest windows)
    sd_floor: float = 0.5        # a byte constant at rest still gets a finite effect size
    indirect_r: float = 0.6


EDGE_PHASES = ("wait", "settle", "undo_wait", "undo_settle")


def _inside(sw: Sweep, ph: dict[str, Any]) -> bool:
    return sw.t0 >= ph["t0"] - 1e-6 and sw.t1 <= ph["t1"] + 1e-6


def _driver_series(sweeps: list[Sweep], drv: dict[str, Any]) -> list[float | None]:
    a, n = drv["addr"], drv["length"]
    out: list[float | None] = []
    for sw in sweeps:
        bs = sw.vals[a:a + n]
        out.append(None if any(b is None for b in bs) else float(int.from_bytes(bytes(bs), "big")))
    return out


def _bit(v: int | None, b: int) -> float | None:
    return None if v is None else float((v >> b) & 1)


def analyse(sweeps: list[Sweep], markers: dict[str, Any], labels: dict[str, Any] | None = None,
            settings: Settings | None = None) -> list[StepResult]:
    """Per step (and per prompt variant), every byte and bit whose values in the
    hold windows differ from the rest windows around the step."""
    cfg = settings or Settings()
    labels = labels or DEFAULT_LABELS
    phases = markers.get("phases", [])
    steps_meta = {s["name"]: s for s in markers.get("steps", [])}
    rest_idx = [i for i, ph in enumerate(phases)
                if ph["phase"] == "rest"
                or (ph["phase"] == "hold" and steps_meta.get(ph["step"], {}).get("baseline"))]
    drivers = labels.get("drivers", DEFAULT_LABELS["drivers"])
    drv_series = {k: _driver_series(sweeps, v) for k, v in drivers.items()}
    drv_addrs = {k: set(range(v["addr"], v["addr"] + v["length"])) for k, v in drivers.items()}
    phase_of = [next((i for i, ph in enumerate(phases) if _inside(sw, ph)), None) for sw in sweeps]
    sweeps_of: dict[int, list[int]] = {}
    for k, p in enumerate(phase_of):
        if p is not None:
            sweeps_of.setdefault(p, []).append(k)

    def sweeps_in(idxs: Iterable[int]) -> list[int]:
        return sorted(k for i in set(idxs) for k in sweeps_of.get(i, []))

    results: list[StepResult] = []
    for st in markers.get("steps", []):
        name = st["name"]
        if st.get("baseline"):
            continue
        mine = [i for i, ph in enumerate(phases) if ph["step"] == name]
        holds = [i for i in mine if phases[i]["phase"] == "hold"]
        variants = st.get("prompts") or []
        groups: list[tuple[str, str, list[int]]] = [(name, st.get("prompt", ""), holds)]
        if len(set(variants)) > 1:
            for v, text in enumerate(variants):
                sel = [i for i in holds if (phases[i]["rep"] - 1) % len(variants) == v]
                groups.append((f"{name}:{v + 1}", text, sel))
        if not holds:
            results.append(StepResult(name=name, engine=st.get("engine", "running"),
                                      prompt=st.get("prompt", ""), reps_done=0, n_hold=0,
                                      n_base=0, note="skipped or not reached"))
            continue
        # rest windows: inside the step (setup rest, between repetitions) and, unless the step
        # has its own setup or fast read, the one right before and right after; widened
        # outwards until there are min_base sweeps
        first, last = min(mine), max(mine)
        inside = [i for i in rest_idx if first <= i <= last]
        before = [i for i in rest_idx if i < first]
        after = [i for i in rest_idx if i > last]
        own_only = bool(st.get("setup") or st.get("fast")) and len(sweeps_in(inside)) >= cfg.min_base
        chosen = list(inside) if own_only else inside + before[-1:] + after[:1]
        pool_b, pool_a = before[:-1][::-1], after[1:]
        while not own_only and len(sweeps_in(chosen)) < cfg.min_base and (pool_b or pool_a):
            if pool_b:
                chosen.append(pool_b.pop(0))
            if pool_a and len(sweeps_in(chosen)) < cfg.min_base:
                chosen.append(pool_a.pop(0))
        base_sw = sweeps_in(chosen)
        pre_sw = sweeps_in([i for i in chosen if i < first] or
                           [i for i in chosen if phases[i]["rep"] == 0])
        post_sw = sweeps_in([i for i in chosen if i > last] or
                            [i for i in chosen if phases[i]["rep"] == max(1, st.get("repeat", 1))])
        excluded = set(sweeps_in(i for i in mine if phases[i]["phase"] != "rest"))
        outside = [k for k in range(len(sweeps)) if k not in excluded]
        edge_sw = sweeps_in(i for i in mine if phases[i]["phase"] in EDGE_PHASES)
        for gname, gprompt, gholds in groups:
            res = StepResult(name=gname, engine=st.get("engine", "running"), prompt=gprompt,
                             reps_done=len({phases[i]["rep"] for i in gholds}), n_hold=0, n_base=0)
            results.append(res)
            _analyse_group(res, sweeps, sweeps_in(gholds), base_sw, pre_sw, post_sw, outside,
                           edge_sw if gname == name else [], cfg, labels, drv_series, drv_addrs,
                           drivers)
            if gname == name:
                _timeline(res, st, sweeps, phases, mine, markers.get("events", []))
    return results


def _analyse_group(res: StepResult, sweeps: list[Sweep], hold_sw: list[int], base_sw: list[int],
                   pre_sw: list[int], post_sw: list[int], outside: list[int], edge_sw: list[int],
                   cfg: Settings, labels: dict[str, Any], drv_series: dict[str, list],
                   drv_addrs: dict[str, set[int]], drivers: dict[str, Any]) -> None:
    res.n_hold, res.n_base = len(hold_sw), len(base_sw)
    if len(base_sw) < 5:
        res.note = f"too few rest sweeps ({len(base_sw)})"
        return
    if len(hold_sw) < 3:
        res.note = f"too few hold sweeps ({len(hold_sw)})"
        return
    for dname, series in drv_series.items():
        h = [series[k] for k in hold_sw if series[k] is not None]
        b = [series[k] for k in base_sw if series[k] is not None]
        if len(b) < 3:
            res.drivers[dname], res.driver_moved[dname] = "no data", False
            continue
        if len(h) < 3:                                  # silent ECU: count it as moved
            res.drivers[dname], res.driver_moved[dname] = "no reply", True
            continue
        scale = 0.25 if dname == "rpm" and drivers[dname]["length"] == 2 else 1.0
        d = (_mean(h) - _mean(b)) / max(math.sqrt((_sd(h) ** 2 + _sd(b) ** 2) / 2), cfg.sd_floor)
        res.drivers[dname] = f"{_mean(b) * scale:.0f} -> {_mean(h) * scale:.0f}"
        res.driver_moved[dname] = abs(d) >= cfg.min_d and mann_whitney_p(h, b) < cfg.alpha

    def indirect_of(get: Callable[[int], float | None], addr: int) -> str:
        notes = []
        for dname, moved in res.driver_moved.items():
            if not moved:
                continue
            if addr in drv_addrs.get(dname, ()):
                notes.append(f"is {dname}")
                continue
            series = drv_series[dname]
            out_d = [series[k] for k in outside if series[k] is not None]
            h_d = [series[k] for k in hold_sw if series[k] is not None]
            b_d = [series[k] for k in base_sw if series[k] is not None]
            jump = abs(_mean(h_d) - _mean(b_d)) if h_d and b_d else 0.0
            # the driver must vary outside the step for the test to mean anything; if it
            # does not, fall back to every sweep (this step included) and say so
            pool, tag = outside, ""
            if not out_d or max(out_d) - min(out_d) < jump / 2:
                pool, tag = range(len(sweeps)), " (this step only)"
            xs, ys = [], []
            for k in pool:
                v, dv = get(k), series[k]
                if v is not None and dv is not None:
                    xs.append(v)
                    ys.append(dv)
            r = spearman(xs, ys)
            if r is not None and abs(r) >= cfg.indirect_r:
                notes.append(f"{dname} r={r:+.2f}{tag}")
        return "; ".join(notes)

    def column(idx: list[int], addr: int) -> list[int]:
        return [v for v in (sweeps[k].vals[addr] for k in idx) if v is not None]

    for addr in range(256):
        h, b = column(hold_sw, addr), column(base_sw, addr)
        if len(h) < 3 or len(b) < 5:
            continue
        pre, post = column(pre_sw, addr), column(post_sw, addr)
        for bit in range(8):
            if addr in FLAG_ADDRS:
                res.bitfrac[f"0x{addr:02X}.{bit}"] = (sum((v >> bit) & 1 for v in b) / len(b),
                                                      sum((v >> bit) & 1 for v in h) / len(h))
        mh, mb = _mean(h), _mean(b)
        d = (mh - mb) / max(math.sqrt((_sd(h) ** 2 + _sd(b) ** 2) / 2), cfg.sd_floor)
        p = mann_whitney_p(h, b)
        lo, hi = min(b), max(b)
        exc = sum(1 for v in h if v < lo or v > hi)
        byte_hit = None
        if abs(d) >= cfg.min_d and p < cfg.alpha:
            byte_hit = ("shift", min(1.0, abs(d) / 5))
        elif exc >= cfg.min_exc_n and exc / len(h) >= cfg.min_exc_frac and p < cfg.exc_p:
            byte_hit = ("excursion", 0.9 * exc / len(h))

        bit_hits: list[Finding] = []
        for bit in range(8):
            kh = sum((v >> bit) & 1 for v in h)
            kb = sum((v >> bit) & 1 for v in b)
            ph_, pb_ = kh / len(h), kb / len(b)
            dp = ph_ - pb_
            pf = fisher_p(kh, len(h), kb, len(b))
            kind = None
            if abs(dp) >= cfg.min_dp and pf < cfg.alpha:
                kind, score = "shift", abs(dp)
            elif pb_ in (0.0, 1.0):
                odd = kh if pb_ == 0.0 else len(h) - kh
                if odd >= cfg.min_exc_n and pf < cfg.exc_p and (odd / len(h) >= cfg.min_exc_frac
                                                                 or pf < cfg.alpha):
                    kind, score = "excursion", 0.9 * odd / len(h)
            if kind is None:
                continue
            drift = ""
            if len(pre) >= 3 and len(post) >= 3:
                fa = sum((v >> bit) & 1 for v in pre) / len(pre)
                fb = sum((v >> bit) & 1 for v in post) / len(post)
                if abs(fa - fb) >= max(0.5, abs(dp) / 2):
                    drift = f"set {fa:.0%} before, {fb:.0%} after"
            bit_hits.append(Finding(
                step=res.name, addr=addr, bit=bit, kind=kind, base=f"{pb_:.0%} set",
                during=f"{ph_:.0%} set", effect=f"{dp:+.0%}", score=score, p=pf,
                n_hold=len(h), n_base=len(b),
                indirect=indirect_of(lambda k, a=addr, bt=bit: _bit(sweeps[k].vals[a], bt), addr),
                drift=drift, name=address_name(labels, addr, bit)))

        # the byte as a number, unless it is just one bit flipping
        if byte_hit and not (len(bit_hits) == 1 and len(set(h) | set(b)) <= 2):
            drift = ""
            if len(pre) >= 3 and len(post) >= 3:
                ma, mp = _mean(pre), _mean(post)
                if abs(ma - mp) >= max(abs(mh - mb) / 2, 2 * cfg.sd_floor):
                    drift = f"mean {ma:.1f} before, {mp:.1f} after"
            res.findings.append(Finding(
                step=res.name, addr=addr, bit=None, kind=byte_hit[0], base=_summ(b),
                during=_summ(h),
                effect=f"d={d:+.1f}" if byte_hit[0] == "shift" else f"{exc}/{len(h)} outside {lo}-{hi}",
                score=byte_hit[1], p=p, n_hold=len(h), n_base=len(b),
                indirect=indirect_of(lambda k, a=addr: None if sweeps[k].vals[a] is None
                                     else float(sweeps[k].vals[a]), addr),
                drift=drift, name=address_name(labels, addr)))
        res.findings.extend(bit_hits)

        # switching moments (prompt, settle, undo): bits in a state never seen at rest
        e = column(edge_sw, addr)
        if len(e) >= cfg.min_exc_n:
            for bit in range(8):
                kb = sum((v >> bit) & 1 for v in b)
                if kb not in (0, len(b)):
                    continue
                state = 0 if kb else 1                  # the state never seen at rest
                odd = sum(1 for v in e if (v >> bit) & 1 == state)
                if odd < cfg.min_exc_n:
                    continue
                ke = sum((v >> bit) & 1 for v in e)
                pf = fisher_p(ke, len(e), kb, len(b))
                if pf >= cfg.alpha and odd / len(e) < cfg.min_exc_frac:
                    continue
                if any(f.addr == addr and f.bit == bit for f in bit_hits):
                    continue
                res.edges.append(Finding(
                    step=res.name, addr=addr, bit=bit, kind="edge",
                    base=f"always {1 - state}", during=f"{state} in {odd}/{len(e)}",
                    effect=f"{odd} sweeps", score=odd / len(e), p=pf, n_hold=len(e),
                    n_base=len(b), name=address_name(labels, addr, bit),
                    indirect=indirect_of(lambda k, a=addr, bt=bit: _bit(sweeps[k].vals[a], bt),
                                         addr)))
    key = (lambda f: (bool(f.indirect), -f.score, f.p, f.addr, -1 if f.bit is None else f.bit))
    res.findings.sort(key=key)
    res.edges.sort(key=key)


def _timeline(res: StepResult, st: dict[str, Any], sweeps: list[Sweep],
              phases: list[dict[str, Any]], mine: list[int], events: list[dict[str, Any]]) -> None:
    """Engine-off steps and owner marks: when each flag bit changed, relative to the hold."""
    for ev in events:
        if ev["step"] != res.name:
            continue
        hold = next((phases[i] for i in mine if phases[i]["phase"] == "hold"
                     and phases[i]["rep"] == ev["rep"]), None)
        rel = f"{ev['t'] - hold['t0']:+.1f} s into the hold" if hold else ""
        before = [sw for sw in sweeps if sw.t1 <= ev["t"]][-3:]
        after = [sw for sw in sweeps if sw.t0 >= ev["t"]][:3]
        changed = []
        for addr in range(256):
            bv = [sw.vals[addr] for sw in before]
            av = [sw.vals[addr] for sw in after]
            if None in bv or None in av or not bv or not av:
                continue
            for bit in range(8):
                sb = {(v >> bit) & 1 for v in bv}
                sa = {(v >> bit) & 1 for v in av}
                if len(sb) == 1 and len(sa) == 1 and sb != sa:
                    changed.append(f"0x{addr:02X} b{bit} {sb.pop()}->{sa.pop()}")
        res.marks.append(f"rep {ev['rep']}: \"{ev['label']}\" {rel}; bits steady for 3 sweeps "
                         f"before and after that changed: {', '.join(changed) or 'none'}")
    if st.get("engine") != "off" and not st.get("pre"):
        return
    for rep in sorted({phases[i]["rep"] for i in mine if phases[i]["rep"] > 0}):
        ph = [phases[i] for i in mine if phases[i]["rep"] == rep]
        hold = next((p for p in ph if p["phase"] == "hold"), None)
        if not hold or not ph:
            continue
        t_lo, t_hi = min(p["t0"] for p in ph), max(p["t1"] for p in ph)
        prev: dict[int, int | None] = {}
        silent_from = None
        for sw in sweeps:
            if sw.t0 < t_lo or sw.t1 > t_hi:
                continue
            rel = sw.t0 - hold["t0"]
            phase = next((p["phase"] for p in ph if _inside(sw, p)), "")
            got = [a for a in FLAG_ADDRS if sw.vals[a] is not None]
            if not got:
                if silent_from is None:
                    silent_from = rel
                continue
            if silent_from is not None:
                res.timeline.append(f"rep {rep} {silent_from:+6.1f} s: ECU silent until {rel:+.1f} s")
                silent_from = None
            for a in got:
                v = sw.vals[a]
                old = prev.get(a)
                if old is not None and old != v:
                    flips = [f"b{b} {(old >> b) & 1}->{(v >> b) & 1}" for b in range(8)
                             if ((old ^ v) >> b) & 1]
                    res.timeline.append(f"rep {rep} {rel:+6.1f} s ({phase}): 0x{a:02X} "
                                        + ", ".join(flips))
                prev[a] = v
        if silent_from is not None:
            res.timeline.append(f"rep {rep} {silent_from:+6.1f} s: ECU silent to the end")


def _summ(xs: Sequence[int]) -> str:
    lo, hi = min(xs), max(xs)
    return f"{lo}" if lo == hi else f"{_mean(xs):.1f} ({lo}-{hi})"


# ------------------------------------------------------------ load + report --


def load_run(folder: Path) -> tuple[list[Sweep], dict[str, Any], dict[str, Any]]:
    sweeps: list[Sweep] = []
    with open(folder / "wide.csv", newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        head = next(rd)
        i_b, i_t, i_te = head.index("b00"), head.index("t"), head.index("t_end")
        for row in rd:
            if len(row) < i_b + 256:
                continue        # a line cut short by a crash
            vals = [int(v) if v != "" else None for v in row[i_b:i_b + 256]]
            sweeps.append(Sweep(float(row[i_t]), float(row[i_te]), vals))
    markers = json.loads((folder / "markers.json").read_text(encoding="utf-8"))
    meta_path = folder / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return sweeps, markers, meta


def _fmt_p(p: float) -> str:
    return "<1e-12" if p < 1e-12 else f"{p:.0e}" if p < 1e-3 else f"{p:.3f}"


def _finding_rows(items: list[Finding], top: int) -> list[str]:
    L = ["| # | addr | known as | change | rest | during | effect | p | note |",
         "|---|---|---|---|---|---|---|---|---|"]
    for i, f in enumerate(items[:top], 1):
        notes = "; ".join(x for x in (f"indirect: {f.indirect}" if f.indirect else "",
                                      f"rest drift: {f.drift}" if f.drift else "") if x)
        L.append(f"| {i} | {f.where} | {f.name or '?'} | {f.kind} | {f.base} | {f.during} | "
                 f"{f.effect} | {_fmt_p(f.p)} | {notes} |")
    if len(items) > top:
        L.append(f"| | | | ... {len(items) - top} more (findings.json) | | | | | |")
    return L


def render_report(results: list[StepResult], meta: dict[str, Any], markers: dict[str, Any],
                  n_sweeps: int, settings: Settings, top: int = 25) -> str:
    L: list[str] = []
    labels = meta.get("labels") or DEFAULT_LABELS
    run = meta.get("start_wall", "?")
    L.append(f"# Poke test, {run[:19].replace('T', ' ')}")
    L.append("")
    sim_note = " (built-in simulator)" if str(meta.get("port", "")).startswith(SIM_PREFIX) else ""
    L.append(f"- Port `{meta.get('port', '?')}`{sim_note}; map `{meta.get('map_version', '?')}`; "
             f"gearbox `{meta.get('trans') or '?'}`")
    L.append(f"- {n_sweeps} sweeps in {meta.get('seconds', '?')} s (full-sweep period "
             f"{meta.get('period_s', '?')} s); reads ok {meta.get('reads_ok', '?')}, failed "
             f"{meta.get('reads_failed', '?')} {meta.get('errors') or ''}; ECU-silent spells "
             f"{meta.get('silent_spells', '?')}")
    if meta.get("cable"):
        L.append(f"- Cable taken {meta['cable'].get('take', '?')}, given back "
                 f"{meta['cable'].get('give', '?')}")
    if meta.get("interrupted"):
        L.append("- **Stopped early (Ctrl-C)**: the steps after that point are missing.")
    if meta.get("time_scale", 1.0) != 1.0:
        L.append(f"- Times scaled by {meta['time_scale']} (test run)")
    L.append(f"- A row is listed when, in the hold windows against the rest windows around the "
             f"step, p < {settings.alpha:g} and |d| >= {settings.min_d} (byte; d = difference of "
             f"means / pooled SD) or the share of sweeps with the bit set changed by >= "
             f"{settings.min_dp:.0%} (bit); or on an excursion: at least {settings.min_exc_n} "
             f"hold sweeps (and {settings.min_exc_frac:.0%} of them, or p < {settings.alpha:g}) "
             "outside anything seen at rest. **indirect** = the address also follows rpm or ECT "
             f"outside this step (|Spearman r| >= {settings.indirect_r}) and rpm or ECT moved in "
             "this step. **edge** rows: bits in a state never seen at rest during the switching "
             "moments (prompt, settle, undo), not in the hold.")
    L.append("")
    L.append("## Steps")
    L.append("")
    L.append("| step | engine | reps | hold sweeps | rest sweeps | rpm rest -> hold | "
             "ECT raw rest -> hold | direct | indirect | edge |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        direct = sum(1 for f in r.findings if not f.indirect)
        L.append(f"| {r.name} | {'**OFF**' if r.engine == 'off' else 'running'} | {r.reps_done} | "
                 f"{r.n_hold} | {r.n_base} | {r.drivers.get('rpm', '-')} | "
                 f"{r.drivers.get('ect', '-')} | {direct} | {len(r.findings) - direct} | "
                 f"{len(r.edges)} |" + (f" {r.note}" if r.note else ""))
    L.append("")
    L.append("## Flag bytes 0x08-0x0F and 0xA4, bit by bit")
    L.append("")
    L.append("Share of sweeps with the bit set, rest -> hold (percent). `0` / `1`: that value in "
             "every rest and hold sweep. **Bold**: listed as a response. `e`: an edge response. "
             "`-`: not read in that step.")
    L.append("")
    cols = [r for r in results if not r.note]
    L.append("| bit | known as | " + " | ".join(r.name for r in cols) + " |")
    L.append("|---|---|" + "---|" * len(cols))
    for a in FLAG_ADDRS:
        for b in range(8):
            key = f"0x{a:02X}.{b}"
            cells = []
            for r in cols:
                if key not in r.bitfrac:
                    cells.append("-")
                    continue
                pb, ph = r.bitfrac[key]
                txt = (str(int(pb)) if pb == ph and pb in (0.0, 1.0)
                       else f"{pb * 100:.0f}->{ph * 100:.0f}")
                if any(f.addr == a and f.bit == b for f in r.findings):
                    txt = f"**{txt}**"
                if any(f.addr == a and f.bit == b for f in r.edges):
                    txt += " e"
                cells.append(txt)
            L.append(f"| 0x{a:02X} b{b} | {address_name(labels, a, b) or ''} | "
                     + " | ".join(cells) + " |")
    L.append("")
    for r in results:
        L.append(f"## {r.name}" + (" (ENGINE OFF)" if r.engine == "off" else ""))
        L.append("")
        L.append(f"> {r.prompt}")
        L.append("")
        if r.note:
            L.append(f"_{r.note}_")
            L.append("")
            continue
        if r.engine == "off":
            L.append("Engine-off step: everything that needs the engine running moves too; "
                     "the direct rows and the timeline are the interesting part.")
            L.append("")
        if r.findings:
            L.extend(_finding_rows(r.findings, top))
        else:
            L.append("Nothing responded in the hold.")
        L.append("")
        if r.edges:
            L.append("At the switching moments:")
            L.append("")
            L.extend(_finding_rows(r.edges, top))
            L.append("")
        if r.marks:
            L.append("Owner marks:")
            L.append("")
            L.extend(f"- {m}" for m in r.marks)
            L.append("")
        if r.timeline:
            L.append("Flag-bit timeline (seconds from the start of the hold):")
            L.append("")
            L.append("```")
            L.extend(r.timeline[:80])
            if len(r.timeline) > 80:
                L.append(f"... {len(r.timeline) - 80} more")
            L.append("```")
            L.append("")
    by_addr: dict[int, list[Finding]] = {}
    for r in results:
        for f in r.findings + r.edges:
            by_addr.setdefault(f.addr, []).append(f)
    L.append("## Per address")
    L.append("")
    L.append("Which steps moved each address. **Bold**: a direct response; `(i)` indirect; "
             "`(e)` at the switching moments only.")
    L.append("")
    L.append("| addr | known as | steps |")
    L.append("|---|---|---|")
    for addr in sorted(by_addr):
        items: dict[str, list[str]] = {}
        for f in by_addr[addr]:
            tag = (f"b{f.bit}" if f.bit is not None else "byte") + \
                (" (i)" if f.indirect else "") + (" (e)" if f.kind == "edge" else "")
            items.setdefault(f.step, []).append(tag)
        cells = []
        for step, tags in items.items():
            txt = f"{step}: {', '.join(tags)}"
            cells.append(f"**{txt}**" if any("(" not in t for t in tags) else txt)
        L.append(f"| 0x{addr:02X} | {address_name(labels, addr) or '?'} | {'; '.join(cells)} |")
    L.append("")
    unknown = sorted({(f.addr, -1 if f.bit is None else f.bit, f.step) for fs in by_addr.values()
                      for f in fs if not f.indirect and not f.name and f.kind != "edge"})
    L.append("## Unnamed addresses that responded directly")
    L.append("")
    if unknown:
        for addr, bit, step in unknown:
            L.append(f"- 0x{addr:02X}" + (f" b{bit}" if bit >= 0 else "") + f": {step}")
    else:
        L.append("None.")
    L.append("")
    return "\n".join(L)


def analyse_folder(folder: Path, alpha: float = 1e-3) -> Path:
    sweeps, markers, meta = load_run(folder)
    settings = Settings(alpha=alpha)
    results = analyse(sweeps, markers, meta.get("labels"), settings)
    out = folder / "report.md"
    out.write_text(render_report(results, meta, markers, len(sweeps), settings), encoding="utf-8")
    summary = [{"step": r.name, "note": r.note, "drivers": r.drivers,
                "findings": [asdict(f) for f in r.findings],
                "edges": [asdict(f) for f in r.edges], "marks": r.marks,
                "timeline": r.timeline} for r in results]
    (folder / "findings.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return out


# ------------------------------------------------------------------ main --


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the steps and sweep (takes the cable on a COM port)")
    r.add_argument("--port", help="COM21 for the car, sim: for the built-in simulator")
    r.add_argument("--dry-run", action="store_true",
                   help="take nothing: built-in simulator (sim:), no pause file, app untouched")
    r.add_argument("--steps", help="comma-separated step names to run (default: all)")
    r.add_argument("--steps-file", help="TOML file with [[step]] entries")
    r.add_argument("--trans", choices=("auto", "manual"),
                   help="gearbox (asked at the start when left out)")
    r.add_argument("--optional", choices=("ask", "yes", "no"),
                   help="optional steps: ask (default), do all, skip all (--auto: no)")
    r.add_argument("--period", type=float, default=1.0, help="full-sweep period in s")
    r.add_argument("--auto", action="store_true",
                   help="do not wait for Enter (simulator / tests)")
    r.add_argument("--time-scale", type=float, default=1.0,
                   help="multiply every timed phase (tests only)")
    r.add_argument("--no-beep", action="store_true")
    r.add_argument("--alpha", type=float, default=1e-3)
    r.add_argument("--out", help="run folder (default poke-runs/<timestamp>)")
    an = sub.add_parser("analyse", aliases=["analyze"], help="(re)analyse a saved run folder")
    an.add_argument("folder")
    an.add_argument("--alpha", type=float, default=1e-3)
    ls = sub.add_parser("list", help="print the step list and the run time")
    ls.add_argument("--steps")
    ls.add_argument("--steps-file")
    ls.add_argument("--trans", choices=("auto", "manual"))
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            print(describe_steps(applicable(load_steps(a), a.trans or "")))
            return 0
        if a.cmd in ("analyse", "analyze"):
            print(analyse_folder(Path(a.folder), alpha=a.alpha))
            return 0
        if a.time_scale <= 0:
            raise SystemExit("--time-scale must be > 0")
        return cmd_run(a)
    except StepError as exc:
        raise SystemExit(f"steps: {exc}") from None


if __name__ == "__main__":
    raise SystemExit(main())
