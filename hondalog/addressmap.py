"""The user-editable address map: which RAM bytes mean what.

Schema 2 adds to the original (schema 1) file, all optional:

    schema = 2
    ecu_variant = "P2P"                 which [variants] entry to load
    [variants.P2T]                      known ECUs; description, constants, protocol
    [constants]                         named numbers for expressions
    [[channel]]  rate, endian, source, notes
    [channel.evidence]                  why a channel is marked verified
    [channel.variants.P28]              per-ECU overrides of a channel
    [[math]]                            derived channels (hondalog.mathch)
    [[alarm]]                           alarm rules (hondalog.alarms)

A schema 1 file loads unchanged. Formulas go through ``hondalog.expr``: text
is parsed into a small AST and compiled to closures, nothing is eval'd.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tomllib
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import protocol
from .alarms import AlarmEngine, AlarmError, AlarmRule
from .expr import Expr, ExprError, compile_expr
from .link.scheduler import DEFAULT_CLASS
from .mathch import MathChannel, MathEngine, MathError
from .protocol import Framing

DEFAULT_MAP = Path(__file__).parent / "data" / "addressmap.toml"
SCHEMA = 2
RATES = ("fast", "medium", "slow")
UNVERIFIED, VERIFIED, SUSPECT = "unverified", "verified", "suspect"
STATUSES = (UNVERIFIED, VERIFIED, SUSPECT)
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def user_map_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "hondalog" / "addressmap.toml"


class MapError(Exception):
    pass


# ---- formulas ---------------------------------------------------------------

def _formula_expr(text: str) -> Expr:
    try:
        expr = compile_expr(text, names=("x",), arrays=("b",))
    except ExprError as exc:
        raise MapError(f"bad formula {text!r}: {exc.message}") from exc
    if expr.stateful or expr.uses_time:
        used = sorted(expr.functions)
        raise MapError(f"bad formula {text!r}: a formula decodes one reading, it cannot use "
                       f"history ({', '.join(used)}); make a [[math]] channel for that")
    return expr


def compile_formula(text: str) -> Callable[[int, Sequence[int]], Any]:
    """Compile a formula into f(x, b) -> number. Only arithmetic is allowed.

    ``x`` is the raw bytes as one unsigned number, ``b`` the single bytes.
    The function raises if the formula cannot be evaluated for that input.
    """
    expr = _formula_expr(text)
    strict = expr.eval_strict

    def run(x: int, b: Sequence[int]) -> Any:
        return strict({"x": x, "b": b})
    return run


# ---- channels ---------------------------------------------------------------

@dataclass
class Evidence:
    """Why a channel is marked verified (or suspect). All text, all optional."""
    date: str = ""            # ISO date-time of the check
    method: str = ""          # "guided:idle", "crosscheck:iso", "manual" ...
    result: str = ""          # "pass" | "fail"
    summary: str = ""         # one line a person can read
    log: str = ""             # evidence file name (session record)
    map_version: str = ""     # the map's version text at the time
    map_hash: str = ""        # the map's version hash at the time
    reference: str = ""       # what it was compared against (meter, ISO PID ...)

    @classmethod
    def from_table(cls, table: Any, who: str) -> "Evidence":
        if not isinstance(table, Mapping):
            raise MapError(f"{who}: evidence must be a table")
        known = {f.name for f in fields(cls)}
        extra = sorted(set(table) - known)
        if extra:
            raise MapError(f"{who}: unknown evidence field {extra[0]!r}")
        return cls(**{k: str(v) for k, v in table.items()})

    def to_table(self) -> dict[str, str]:
        return {k: v for k, v in asdict(self).items() if v}


# Channel fields a [channel.variants.X] table may override.
_VARIANT_KEYS = {"addr", "length", "formula", "endian", "label", "units", "min", "max",
                 "decimals", "rate", "status", "evidence", "source", "notes", "absent"}


@dataclass
class Channel:
    name: str
    addr: int
    length: int = 1
    formula: str = "x"
    label: str = ""
    units: str = ""
    min: float = 0.0
    max: float = 255.0
    decimals: int = 0
    kind: str = "value"        # "value" or "flag"
    gauge: bool = False
    plot: bool = False
    status: str = UNVERIFIED   # "unverified" | "verified" | "suspect"
    # ---- schema 2 ----
    # Poll rate class: "fast" | "medium" | "slow". Left out, the channel's name
    # decides (link/scheduler.py DEFAULT_CLASS); after loading it is always set.
    rate: str = ""
    endian: str = "big"        # byte order of x for multi-byte channels
    source: str = ""           # provenance: where the address and formula came from
    notes: str = ""
    evidence: Evidence | None = None
    variants: dict[str, dict[str, Any]] = field(default_factory=dict)
    _expr: Expr | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name or ""):
            raise MapError(f"bad channel name {self.name!r}")
        who = self.name
        if not isinstance(self.addr, int) or isinstance(self.addr, bool) \
                or not 0 <= self.addr <= 0xFF:
            raise MapError(f"{who}: addr out of range")
        if not isinstance(self.length, int) or not 1 <= self.length <= 4 \
                or self.addr + self.length > 0x100:
            raise MapError(f"{who}: length must be 1-4 and stay below 0x100")
        if self.kind not in ("value", "flag"):
            raise MapError(f"{who}: kind must be 'value' or 'flag'")
        if not isinstance(self.rate, str):
            raise MapError(f"{who}: rate must be one of {', '.join(RATES)}")
        self.rate = self.rate.strip().lower() or DEFAULT_CLASS.get(self.name, "slow")
        if self.rate not in RATES:
            raise MapError(f"{who}: rate must be one of {', '.join(RATES)}")
        if self.endian not in ("big", "little"):
            raise MapError(f"{who}: endian must be 'big' or 'little'")
        if not isinstance(self.status, str) or self.status.lower() not in STATUSES:
            raise MapError(f"{who}: status must be one of {', '.join(STATUSES)}")
        if not isinstance(self.formula, str):
            raise MapError(f"{who}: formula must be text")
        if self.evidence is not None and not isinstance(self.evidence, Evidence):
            self.evidence = Evidence.from_table(self.evidence, who)
        if not isinstance(self.variants, Mapping):
            raise MapError(f"{who}: variants must be a table")
        for variant, table in self.variants.items():
            if not isinstance(table, Mapping):
                raise MapError(f"{who}: variants.{variant} must be a table")
            extra = sorted(set(table) - _VARIANT_KEYS)
            if extra:
                raise MapError(f"{who}: variants.{variant} cannot set {extra[0]!r}")
        self.label = self.label or self.name
        self._expr = _formula_expr(self.formula)
        if self.kind == "flag":
            self.min, self.max, self.decimals = 0.0, 1.0, 0

    @property
    def verified(self) -> bool:
        return self.status.lower() == VERIFIED

    def convert(self, raw: bytes) -> float | None:
        """Raw bytes -> engineering value. None if the formula blows up."""
        assert self._expr is not None
        return self._expr.eval({"x": int.from_bytes(raw, self.endian), "b": raw})  # type: ignore[arg-type]

    def format(self, value: float | None) -> str:
        if value is None or value != value:
            return "--"
        return f"{value:.{self.decimals}f}"

    def for_variant(self, variant: str, base_variant: str) -> "Channel | None":
        """This channel as it applies to one ECU variant (None = not present).

        The plain fields describe ``base_variant``. Another variant inherits
        address and formula unless it overrides them, but never the verified
        state: a check on one ECU says nothing about another.
        """
        if not variant or variant == base_variant:
            return self
        table = dict(self.variants.get(variant, {}))
        if table.pop("absent", False):
            return None
        table.setdefault("status", UNVERIFIED)
        table.setdefault("evidence", None)
        try:
            return replace(self, **table)
        except TypeError as exc:
            raise MapError(f"{self.name}: variants.{variant}: {exc}") from exc


@dataclass
class AddressMap:
    version: str
    ecu: str
    channels: list[Channel]
    framing: Framing = protocol.DEFAULT
    ecu_id_addr: int | None = 0x76
    ecu_id_len: int = 10
    path: str = ""
    # ---- schema 2 ----
    schema: int = 1
    variant: str = ""                         # the ECU variant these channels are for
    base_variant: str = ""                    # the variant the plain channel fields describe
    variants: dict[str, dict[str, Any]] = field(default_factory=dict)
    constants: dict[str, float] = field(default_factory=dict)
    math: list[MathChannel] = field(default_factory=list)
    alarms: list[AlarmRule] = field(default_factory=list)
    base_channels: list[Channel] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if not self.base_channels:
            self.base_channels = list(self.channels)

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.channels]

    @property
    def math_names(self) -> list[str]:
        return [m.name for m in self.math if m.enabled]

    @property
    def all_names(self) -> list[str]:
        """Measured channels, then math channels."""
        return self.names + self.math_names

    def channel(self, name: str) -> Channel | None:
        return next((c for c in self.channels if c.name == name), None)

    def math_channel(self, name: str) -> MathChannel | None:
        return next((m for m in self.math if m.name == name), None)

    def blocks(self) -> list[tuple[int, int]]:
        return plan_blocks(self.channels, self.framing.max_read_len)

    def decode(self, mem: Mapping[int, int]) -> dict[str, float | None]:
        """mem maps address -> byte for everything read this sweep."""
        out: dict[str, float | None] = {}
        for ch in self.channels:
            try:
                raw = bytes(mem[a] for a in range(ch.addr, ch.addr + ch.length))
            except KeyError:
                out[ch.name] = None
                continue
            out[ch.name] = ch.convert(raw)
        return out

    # -- schema 2 helpers --

    def units(self) -> dict[str, str]:
        """Base unit text for every measured and math channel."""
        out = {c.name: c.units for c in self.channels}
        out.update({m.name: m.units for m in self.math})
        return out

    def by_rate(self) -> dict[str, list[Channel]]:
        """Channels grouped by poll rate class, for the tiered scheduler."""
        return {rate: [c for c in self.channels if c.rate == rate] for rate in RATES}

    def math_engine(self) -> MathEngine:
        """A fresh evaluator for this map's math channels."""
        return MathEngine(self.math, self.names, self.constants,
                          {c.name: c.units for c in self.channels})

    def is_verified(self, name: str) -> bool:
        """A measured channel's own state; a math channel is verified when
        every measured channel it is built from is."""
        ch = self.channel(name)
        if ch is not None:
            return ch.verified
        engine = self.math_engine()
        if name not in engine.names:
            return False
        inputs = engine.base_inputs(name)
        return all((c := self.channel(n)) is not None and c.verified for n in inputs)

    def alarm_engine(self) -> AlarmEngine:
        """A fresh alarm engine over measured and math channels."""
        engine = self.math_engine()
        verified = {c.name: c.verified for c in self.channels}
        for name in engine.names:
            verified[name] = all(verified.get(n, False) for n in engine.base_inputs(name))
        labels = {c.name: (c.label, c.units) for c in self.channels}
        labels.update({m.name: (m.label, m.units) for m in self.math})
        return AlarmEngine(self.alarms, self.all_names, self.constants, verified, labels)

    def decode_spec(self) -> dict[str, Any]:
        """Everything that decides what number a log row holds, as plain data."""
        return {
            "variant": self.variant,
            "channels": [[c.name, c.addr, c.length, c.endian, c.formula, c.units, c.kind]
                         for c in self.channels],
            "constants": sorted(self.constants.items()),
            "math": [[m.name, m.expr, m.units] for m in self.math if m.enabled],
        }

    @property
    def version_hash(self) -> str:
        """12 hex digits that change whenever decoding changes.

        Covers addresses, lengths, byte order, formulas, units, constants and
        math expressions. Labels, ranges, alarm rules and the verified marks
        are left out: marking a channel verified must not change the hash that
        its own evidence refers to.
        """
        text = json.dumps(self.decode_spec(), sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]

    @property
    def version_tag(self) -> str:
        """Version text plus hash, for log headers: "p2p-guess-2 #1a2b3c4d5e6f"."""
        return f"{self.version} #{self.version_hash}"


def plan_blocks(channels: Iterable[Channel], max_len: int = 16) -> list[tuple[int, int]]:
    """Fewest (addr, length) reads that cover every channel.

    Greedy: start at the lowest needed address, take as many needed bytes as
    fit in one request, repeat. A multi-byte channel is never split.
    """
    spans = sorted({(c.addr, c.addr + c.length) for c in channels})
    blocks: list[tuple[int, int]] = []
    start = end = -1
    for lo, hi in spans:
        if start >= 0 and hi - start <= max_len:
            end = max(end, hi)
            continue
        if start >= 0:
            blocks.append((start, end - start))
        start, end = lo, hi
    if start >= 0:
        blocks.append((start, end - start))
    return blocks


_TOP_KEYS = {"schema", "version", "ecu", "ecu_variant", "base_variant", "variants", "ecu_id",
             "protocol", "constants", "channel", "math", "alarm"}


def _constants(table: Any, where: str) -> dict[str, float]:
    if not isinstance(table, Mapping):
        raise MapError(f"{where} must be a table of name = number")
    out: dict[str, float] = {}
    for key, value in table.items():
        if not _NAME.fullmatch(key):
            raise MapError(f"{where}: bad name {key!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MapError(f"{where}: {key} must be a number")
        out[key] = float(value)
    return out


def parse(doc: Mapping[str, Any], path: str = "", variant: str | None = None) -> AddressMap:
    """Build an AddressMap from a parsed TOML document. Raises MapError."""
    where = path or "address map"
    schema = doc.get("schema", 1)
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        raise MapError(f"{where}: schema must be a whole number")
    if schema > SCHEMA:
        raise MapError(f"{where}: written for map schema {schema}, this hondalog reads up to "
                       f"{SCHEMA}. Update hondalog or use an older map.")
    if schema >= 2:           # schema 1 files were never checked for stray keys
        extra = sorted(set(doc) - _TOP_KEYS)
        if extra:
            raise MapError(f"{where}: unknown section or key {extra[0]!r} "
                           f"(known: {', '.join(sorted(_TOP_KEYS))})")
    try:
        base = [Channel(**entry) for entry in doc.get("channel", [])]
    except TypeError as exc:
        raise MapError(f"{where}: {exc}") from exc
    if not base:
        raise MapError(f"{where}: no [[channel]] entries")
    names = [c.name for c in base]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise MapError(f"{where}: duplicate channel names {sorted(dupes)}")

    variants = doc.get("variants", {})
    if not isinstance(variants, Mapping) or not all(isinstance(v, Mapping)
                                                    for v in variants.values()):
        raise MapError(f"{where}: [variants] must hold one table per ECU")
    base_variant = str(doc.get("base_variant") or next(iter(variants), ""))
    active = str(variant or doc.get("ecu_variant") or base_variant)
    used = {v for c in base for v in c.variants}
    if variants:
        unknown = sorted((used | {active, base_variant}) - set(variants))
        if unknown:
            raise MapError(f"{where}: ECU variant {unknown[0]!r} is not listed under "
                           f"[variants] (known: {', '.join(variants)})")
    elif active and active not in used | {base_variant}:
        raise MapError(f"{where}: no ECU variant {active!r} in this map")
    info = dict(variants.get(active, {})) if active != base_variant else {}
    channels = [c for c in (ch.for_variant(active, base_variant) for ch in base) if c is not None]
    if not channels:
        raise MapError(f"{where}: variant {active!r} has no channels")

    try:
        framing = protocol.DEFAULT.with_overrides(doc.get("protocol", {}))
        framing = framing.with_overrides(info.get("protocol", {}))
    except (ValueError, TypeError, AttributeError) as exc:
        raise MapError(f"{where}: [protocol] {exc}") from exc
    constants = _constants(doc.get("constants", {}), f"{where}: [constants]")
    constants.update(_constants(info.get("constants", {}),
                                f"{where}: [variants.{active}.constants]"))
    clash = sorted(set(constants) & set(names))
    if clash:
        raise MapError(f"{where}: constant {clash[0]!r} has the same name as a channel")

    try:
        math = [MathChannel(**entry) for entry in doc.get("math", [])]
        alarms = [_alarm(entry) for entry in doc.get("alarm", [])]
    except (TypeError, MathError, AlarmError) as exc:
        raise MapError(f"{where}: {exc}") from exc
    ecu_id = doc.get("ecu_id", {})
    amap = AddressMap(
        version=str(doc.get("version", "unversioned")),
        ecu=str(info.get("description") or doc.get("ecu", "")),
        channels=channels,
        framing=framing,
        ecu_id_addr=ecu_id.get("addr"),
        ecu_id_len=int(ecu_id.get("length", 10)),
        path=str(path),
        schema=schema,
        variant=active,
        base_variant=base_variant,
        variants={k: dict(v) for k, v in variants.items()},
        constants=constants,
        math=math,
        alarms=alarms,
        base_channels=base,
    )
    try:                      # compile everything now so a bad map fails at load
        amap.math_engine()
        amap.alarm_engine()
    except (MathError, AlarmError) as exc:
        raise MapError(f"{where}: {exc}") from exc
    return amap


def _alarm(entry: Mapping[str, Any]) -> AlarmRule:
    table = dict(entry)
    if "actions" in table:
        if not isinstance(table["actions"], (list, tuple)):
            raise AlarmError(f"alarm {table.get('name')!r}: actions must be a list")
        table["actions"] = tuple(table["actions"])
    return AlarmRule(**table)


def load(path: str | Path, variant: str | None = None) -> AddressMap:
    """Load a map file. ``variant`` picks an ECU variant other than the file's
    own ``ecu_variant``."""
    path = Path(path)
    try:
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise MapError(f"{path}: {exc}") from exc
    return parse(doc, str(path), variant)


def loads(text: str, variant: str | None = None, path: str = "") -> AddressMap:
    """Load a map from TOML text."""
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise MapError(f"{path or 'address map'}: {exc}") from exc
    return parse(doc, path, variant)


def load_default() -> AddressMap:
    return load(DEFAULT_MAP)


def load_user_map(variant: str | None = None) -> AddressMap:
    """Load the user's map, creating it from the default on first run."""
    path = user_map_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_MAP, path)
    return load(path, variant)
