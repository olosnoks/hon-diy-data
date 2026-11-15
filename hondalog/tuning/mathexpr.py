"""Conversion equations: raw ROM number <-> engineering value.

The syntax is the one TunerPro uses in XDF ``<MATH equation="...">``:
arithmetic on named variables (``X`` is the cell), ``^`` for power, ``;`` or
``,`` between function arguments, names not case-sensitive. Everything is
evaluated with numpy so a whole table converts in one call.

Supported functions: ABS SQRT EXP LN LOG LOG10 POW SIN COS TAN ASIN ACOS ATAN
ROUND FLOOR CEIL INT MIN MAX IF AND OR NOT ROW COL.
``CELL()``, ``THIS()``, ``THAT()`` and ``ADDRESS()`` are not supported; an
equation using them raises :class:`MathError` and the item is shown raw.

No ``eval`` of user text happens without the AST whitelist below.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping

import numpy as np


class MathError(Exception):
    """The equation cannot be parsed, evaluated or inverted."""


_TOKEN = re.compile(r"""
    (?P<num>0[xX][0-9A-Fa-f]+ | (?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\*\*|<<|>>|<=|>=|==|!=|<>|&&|\|\||[-+*/%^()<>,;&|!=])
  | (?P<ws>\s+)
""", re.X)


def _round_half_away(x):
    return np.sign(x) * np.floor(np.abs(x) + 0.5)


def _if(cond, a, b):
    return np.where(np.asarray(cond) != 0, a, b)


def _and(*args):
    out = np.asarray(args[0]) != 0
    for a in args[1:]:
        out = out & (np.asarray(a) != 0)
    return out.astype(float)


def _or(*args):
    out = np.asarray(args[0]) != 0
    for a in args[1:]:
        out = out | (np.asarray(a) != 0)
    return out.astype(float)


def _min(*args):
    out = np.asarray(args[0], dtype=float)
    for a in args[1:]:
        out = np.minimum(out, a)
    return out


def _max(*args):
    out = np.asarray(args[0], dtype=float)
    for a in args[1:]:
        out = np.maximum(out, a)
    return out


_FUNCS = {
    "ABS": np.abs, "SQRT": np.sqrt, "EXP": np.exp, "LN": np.log, "LOG": np.log,
    "LOG10": np.log10, "POW": np.power, "SIN": np.sin, "COS": np.cos,
    "TAN": np.tan, "ASIN": np.arcsin, "ACOS": np.arccos, "ATAN": np.arctan,
    "ROUND": _round_half_away, "FLOOR": np.floor, "CEIL": np.ceil,
    "INT": np.trunc, "TRUNC": np.trunc, "MIN": _min, "MAX": _max, "IF": _if,
    "AND": _and, "OR": _or, "NOT": lambda a: (np.asarray(a) == 0).astype(float),
}
_POSITION = ("ROW", "COL")
_UNSUPPORTED = ("CELL", "THIS", "THAT", "ADDRESS")

_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name,
            ast.Load, ast.Call, ast.Compare, ast.Add, ast.Sub, ast.Mult,
            ast.Div, ast.Mod, ast.Pow, ast.USub, ast.UAdd, ast.Lt, ast.LtE,
            ast.Gt, ast.GtE, ast.Eq, ast.NotEq, ast.BitAnd, ast.BitOr,
            ast.LShift, ast.RShift)


def _translate(text: str) -> tuple[str, set[str], bool]:
    """XDF equation -> (python source, variable names, uses ROW/COL)."""
    out: list[str] = []
    names: set[str] = set()
    uses_pos = False
    pos = 0
    tokens: list[tuple[str, str]] = []
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise MathError(f"bad equation {text!r}: unexpected {text[pos]!r}")
        pos = m.end()
        if m.lastgroup != "ws":
            tokens.append((m.lastgroup or "", m.group()))
    for i, (kind, tok) in enumerate(tokens):
        if kind == "num":
            out.append(str(int(tok, 16)) if tok[:2].lower() == "0x" else tok)
        elif kind == "name":
            up = tok.upper()
            is_call = i + 1 < len(tokens) and tokens[i + 1][1] == "("
            if is_call:
                if up in _UNSUPPORTED:
                    raise MathError(f"equation {text!r}: {up}() is not supported")
                if up in _POSITION:
                    uses_pos = True
                elif up not in _FUNCS:
                    raise MathError(f"equation {text!r}: unknown function {tok}()")
                out.append(f"_f_{up}")
            else:
                names.add(up)
                out.append(f"_v_{up}")
        else:
            out.append({"^": "**", ";": ",", "<>": "!=", "&&": "&", "||": "|",
                        "=": "=="}.get(tok, tok))
            if tok == "!":
                raise MathError(f"equation {text!r}: use NOT() instead of '!'")
    return " ".join(out), names, uses_pos


class Expression:
    """A compiled equation. ``expr(x, row=..., col=..., NAME=value)``."""

    def __init__(self, text: str, cell_var: str = "X"):
        self.text = (text or "").strip() or cell_var
        self.cell_var = cell_var.upper()
        source, names, self.uses_position = _translate(self.text)
        try:
            tree = ast.parse(source, mode="eval")
        except SyntaxError as exc:
            raise MathError(f"bad equation {self.text!r}: {exc.msg}") from exc
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED):
                raise MathError(
                    f"bad equation {self.text!r}: {type(node).__name__} not allowed")
            if isinstance(node, ast.Call) and (
                    not isinstance(node.func, ast.Name) or node.keywords):
                raise MathError(f"bad equation {self.text!r}: bad call")
            if isinstance(node, ast.Constant) and (
                    isinstance(node.value, bool)
                    or not isinstance(node.value, (int, float))):
                raise MathError(f"bad equation {self.text!r}: only numbers allowed")
        self.variables = frozenset(names)
        self.extra_variables = frozenset(names - {self.cell_var})
        self._code = compile(tree, "<equation>", "eval")
        self.is_identity = re.sub(r"\s", "", self.text).upper() == self.cell_var

    def __call__(self, x, *, row=0, col=0, **variables):
        env: dict[str, object] = {"__builtins__": {}}
        env.update({f"_f_{k}": v for k, v in _FUNCS.items()})
        env["_f_ROW"] = lambda: np.asarray(row, dtype=float)
        env["_f_COL"] = lambda: np.asarray(col, dtype=float)
        given = {k.upper(): v for k, v in variables.items()}
        for name in self.variables:
            if name == self.cell_var:
                env[f"_v_{name}"] = np.asarray(x, dtype=float)
            elif name in given:
                env[f"_v_{name}"] = np.asarray(given[name], dtype=float)
            else:
                raise MathError(f"equation {self.text!r}: no value for {name}")
        try:
            with np.errstate(all="ignore"):
                result = eval(self._code, env)     # AST is whitelisted above
        except MathError:
            raise
        except Exception as exc:                   # noqa: BLE001 - user equation
            raise MathError(f"equation {self.text!r} failed: {exc}") from exc
        result = np.asarray(result, dtype=float)
        shape = np.broadcast(np.asarray(x), np.asarray(row), np.asarray(col)).shape
        return np.broadcast_to(result, shape).copy() if result.shape != shape else result

    def __repr__(self) -> str:
        return f"Expression({self.text!r})"


def raw_limits(bits: int, signed: bool, is_float: bool) -> tuple[float, float]:
    if is_float:
        return (-np.inf, np.inf)
    if signed:
        return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1)
    return (0, (1 << bits) - 1)


class Conversion:
    """Raw <-> real for one item, with the inverse worked out numerically.

    The inverse is exact for affine equations (``a*X + b``, the usual case).
    For anything else on 8- or 16-bit data it searches every possible raw value
    for the closest result, so no algebra is needed.
    """

    def __init__(self, equation: str = "X", *, bits: int = 8, signed: bool = False,
                 is_float: bool = False, cell_var: str = "X",
                 variables: Mapping[str, float] | None = None):
        self.expr = Expression(equation, cell_var)
        self.bits, self.signed, self.is_float = bits, signed, is_float
        self.variables = {k.upper(): float(v) for k, v in (variables or {}).items()}
        self.lo, self.hi = raw_limits(bits, signed, is_float)

    @property
    def equation(self) -> str:
        return self.expr.text

    def to_real(self, raw, row=0, col=0) -> np.ndarray:
        return self.expr(raw, row=row, col=col, **self.variables)

    def _affine(self, row, col, shape) -> tuple[np.ndarray, np.ndarray] | None:
        """(a, b) per cell when the equation is a*X + b, else None."""
        lo = 0.0 if self.is_float else float(self.lo)
        hi = 1000.0 if self.is_float else float(self.hi)
        span = hi - lo

        def f(x: float) -> np.ndarray:
            return self.expr(np.full(shape, x), row=row, col=col, **self.variables)

        b0, b1 = f(lo), f(lo + 1.0)
        with np.errstate(all="ignore"):
            a = b1 - b0
            b = b0 - a * lo
        if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b))):
            return None
        for frac in (0.25, 0.5, 0.77, 1.0):
            x = lo + np.floor(span * frac)
            got, want = f(x), a * x + b
            if not np.all(np.isfinite(got)):
                return None
            tol = 1e-9 * np.maximum(1.0, np.maximum(np.abs(got), np.abs(want)))
            if np.any(np.abs(got - want) > tol):
                return None
        return a, b

    def to_raw(self, real, row=0, col=0) -> tuple[np.ndarray, np.ndarray]:
        """Real values -> (raw, clipped). ``clipped`` marks cells that hit a limit."""
        real = np.asarray(real, dtype=float)
        shape = np.broadcast(real, np.asarray(row), np.asarray(col)).shape
        real = np.broadcast_to(real, shape)
        row_b = np.broadcast_to(np.asarray(row), shape)
        col_b = np.broadcast_to(np.asarray(col), shape)
        if not np.all(np.isfinite(real)):
            raise MathError("cannot store a value that is not a finite number")
        affine = self._affine(row_b, col_b, shape)
        if affine is not None:
            a, b = affine
            if np.any(a == 0):
                raise MathError(
                    f"equation {self.equation!r} does not depend on the cell value")
            raw = (real - b) / a
        elif not self.is_float and self.bits <= 16:
            raw = self._search(real, row_b, col_b)
        else:
            raise MathError(
                f"equation {self.equation!r} cannot be inverted for "
                f"{'float' if self.is_float else f'{self.bits}-bit'} data")
        if self.is_float:
            return raw.astype(float), np.zeros(shape, dtype=bool)
        raw = _round_half_away(raw)
        clipped = (raw < self.lo) | (raw > self.hi)
        return np.clip(raw, self.lo, self.hi).astype(np.int64), clipped

    def _search(self, real: np.ndarray, row: np.ndarray, col: np.ndarray) -> np.ndarray:
        candidates = np.arange(int(self.lo), int(self.hi) + 1, dtype=float)
        out = np.empty(real.shape, dtype=float)

        def nearest(table: np.ndarray, wanted: np.ndarray) -> np.ndarray:
            ok = np.isfinite(table)
            if not ok.any():
                raise MathError(f"equation {self.equation!r} gives no usable values")
            vals, cands = table[ok], candidates[ok]
            order = np.argsort(vals, kind="stable")
            vals, cands = vals[order], cands[order]
            idx = np.clip(np.searchsorted(vals, wanted), 1, len(vals) - 1) \
                if len(vals) > 1 else np.zeros(wanted.shape, dtype=int)
            if len(vals) > 1:
                left = np.abs(wanted - vals[idx - 1]) <= np.abs(vals[idx] - wanted)
                idx = np.where(left, idx - 1, idx)
            return cands[idx]

        if not self.expr.uses_position:
            table = self.expr(candidates, **self.variables)
            return nearest(table, real)
        flat_r, flat_c = row.ravel(), col.ravel()
        flat_v, flat_o = real.ravel(), out.ravel()
        for i in range(flat_v.size):
            table = self.expr(candidates, row=float(flat_r[i]), col=float(flat_c[i]),
                              **self.variables)
            flat_o[i] = nearest(table, flat_v[i:i + 1])[0]
        return flat_o.reshape(real.shape)
