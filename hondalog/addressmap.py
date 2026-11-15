"""The user-editable address map: which RAM bytes mean what."""

from __future__ import annotations

import ast
import math
import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import protocol
from .protocol import Framing

DEFAULT_MAP = Path(__file__).parent / "data" / "addressmap.toml"


def user_map_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "hondalog" / "addressmap.toml"


class MapError(Exception):
    pass


# ---- formulas ---------------------------------------------------------------

def _bit(x, n):
    return (int(x) >> int(n)) & 1


def _signed(x, bits=8):
    x, bits = int(x), int(bits)
    return x - (1 << bits) if x & (1 << (bits - 1)) else x


def _poly(x, *coeffs):
    return sum(c * x ** i for i, c in enumerate(coeffs))


_FUNCS = {"bit": _bit, "signed": _signed, "poly": _poly, "abs": abs, "min": min,
          "max": max, "round": round, "int": int, "float": float}

_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name,
            ast.Load, ast.Call, ast.Subscript, ast.IfExp, ast.Compare,
            ast.BoolOp, ast.And, ast.Or, ast.Not, ast.Add, ast.Sub, ast.Mult,
            ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd,
            ast.BitAnd, ast.BitOr, ast.BitXor, ast.LShift, ast.RShift,
            ast.Invert, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


def compile_formula(text: str):
    """Compile a formula into f(x, b) -> number. Only arithmetic is allowed."""
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as exc:
        raise MapError(f"bad formula {text!r}: {exc.msg}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise MapError(f"bad formula {text!r}: {type(node).__name__} not allowed")
        if isinstance(node, ast.Name) and node.id not in ("x", "b", *_FUNCS):
            raise MapError(f"bad formula {text!r}: unknown name {node.id!r}")
        if isinstance(node, ast.Call) and not (
                isinstance(node.func, ast.Name) and node.func.id in _FUNCS
                and not node.keywords):
            raise MapError(f"bad formula {text!r}: only {sorted(_FUNCS)} can be called")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise MapError(f"bad formula {text!r}: only numbers allowed")
    code = compile(tree, "<formula>", "eval")
    env = {"__builtins__": {}, **_FUNCS}

    def run(x, b):
        return eval(code, env, {"x": x, "b": b})   # AST is whitelisted above
    return run


# ---- channels ---------------------------------------------------------------

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
    status: str = "unverified"
    _fn: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.name or ""):
            raise MapError(f"bad channel name {self.name!r}")
        if not 0 <= self.addr <= 0xFF:
            raise MapError(f"{self.name}: addr out of range")
        if not 1 <= self.length <= 4 or self.addr + self.length > 0x100:
            raise MapError(f"{self.name}: length must be 1-4 and stay below 0x100")
        if self.kind not in ("value", "flag"):
            raise MapError(f"{self.name}: kind must be 'value' or 'flag'")
        self.label = self.label or self.name
        self._fn = compile_formula(self.formula)
        if self.kind == "flag":
            self.min, self.max, self.decimals = 0.0, 1.0, 0

    @property
    def verified(self) -> bool:
        return self.status.lower() == "verified"

    def convert(self, raw: bytes) -> float | None:
        """Raw bytes -> engineering value. None if the formula blows up."""
        try:
            value = float(self._fn(int.from_bytes(raw, "big"), list(raw)))
        except Exception:
            return None
        return value if math.isfinite(value) else None

    def format(self, value) -> str:
        if value is None or value != value:
            return "--"
        return f"{value:.{self.decimals}f}"


@dataclass
class AddressMap:
    version: str
    ecu: str
    channels: list[Channel]
    framing: Framing = protocol.DEFAULT
    ecu_id_addr: int | None = 0x76
    ecu_id_len: int = 10
    path: str = ""

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.channels]

    def channel(self, name: str) -> Channel | None:
        return next((c for c in self.channels if c.name == name), None)

    def blocks(self) -> list[tuple[int, int]]:
        return plan_blocks(self.channels, self.framing.max_read_len)

    def decode(self, mem: dict[int, int]) -> dict[str, float | None]:
        """mem maps address -> byte for everything read this sweep."""
        out = {}
        for ch in self.channels:
            try:
                raw = bytes(mem[a] for a in range(ch.addr, ch.addr + ch.length))
            except KeyError:
                out[ch.name] = None
                continue
            out[ch.name] = ch.convert(raw)
        return out


def plan_blocks(channels, max_len: int = 16) -> list[tuple[int, int]]:
    """Fewest (addr, length) reads that cover every channel.

    Greedy: start at the lowest needed address, take as many needed bytes as
    fit in one request, repeat. A multi-byte channel is never split.
    """
    spans = sorted({(c.addr, c.addr + c.length) for c in channels})
    blocks: list[tuple[int, int]] = []
    start = end = None
    for lo, hi in spans:
        if start is not None and hi - start <= max_len:
            end = max(end, hi)
            continue
        if start is not None:
            blocks.append((start, end - start))
        start, end = lo, hi
    if start is not None:
        blocks.append((start, end - start))
    return blocks


def load(path) -> AddressMap:
    path = Path(path)
    try:
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise MapError(f"{path}: {exc}") from exc
    try:
        channels = [Channel(**entry) for entry in doc.get("channel", [])]
    except TypeError as exc:
        raise MapError(f"{path}: {exc}") from exc
    if not channels:
        raise MapError(f"{path}: no [[channel]] entries")
    names = [c.name for c in channels]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise MapError(f"{path}: duplicate channel names {sorted(dupes)}")
    try:
        framing = protocol.DEFAULT.with_overrides(doc.get("protocol", {}))
    except (ValueError, TypeError) as exc:
        raise MapError(f"{path}: [protocol] {exc}") from exc
    ecu_id = doc.get("ecu_id", {})
    return AddressMap(
        version=str(doc.get("version", "unversioned")),
        ecu=str(doc.get("ecu", "")),
        channels=channels,
        framing=framing,
        ecu_id_addr=ecu_id.get("addr"),
        ecu_id_len=int(ecu_id.get("length", 10)),
        path=str(path),
    )


def load_default() -> AddressMap:
    return load(DEFAULT_MAP)


def load_user_map() -> AddressMap:
    """Load the user's map, creating it from the default on first run."""
    path = user_map_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_MAP, path)
    return load(path)
