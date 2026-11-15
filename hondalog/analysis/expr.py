"""Safe expressions over channels, for filters and trigger conditions.

    ect > 80 and closed_loop and abs(rate(tps)) < 5
    [rpm] > 3000 && [map] < 40                (MegaLogViewer style also works)

The same expression runs on whole numpy columns (filters, histograms) and on
single live values (triggers). There is no ``eval``: the text is parsed with
``ast`` and walked by a small interpreter that knows a fixed list of
operators and functions.

A name that has no value (channel not read yet, link down) counts as NaN, and
any comparison with NaN is false. So ``rpm < 50`` is false while the ECU is
silent, which is what a stall rule needs.

W3 owns the evaluator for math channels (W3-06). This module is deliberately
small and has the same surface (compile once, call with a mapping), so the
two can be swapped or merged without touching the callers.
"""

from __future__ import annotations

import ast
import math
import re
from typing import Any, Callable, Mapping

import numpy as np


class ExprError(ValueError):
    """The expression text is not allowed or does not parse."""


def _rate(env: Mapping[str, Any], x: Any) -> Any:
    """Change per second (needs the time column, so table mode only)."""
    t = env.get("t")
    if t is None or np.ndim(x) == 0:
        raise ExprError("rate() needs a whole log, it cannot run on one live sample")
    if len(x) < 2:
        return np.zeros(len(x))
    return np.gradient(x, t)


def _smooth(env: Mapping[str, Any], x: Any, n: Any = 5) -> Any:
    """Centred moving average over n samples, NaN-aware."""
    if np.ndim(x) == 0:
        return x
    n = max(1, int(n))
    ok = ~np.isnan(x)
    kernel = np.ones(n)
    total = np.convolve(np.where(ok, x, 0.0), kernel, "same")
    count = np.convolve(ok.astype(float), kernel, "same")
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0, total / count, np.nan)


def _prev(env: Mapping[str, Any], x: Any, n: Any = 1) -> Any:
    """The value n samples earlier."""
    if np.ndim(x) == 0:
        raise ExprError("prev() needs a whole log, it cannot run on one live sample")
    n = int(n)
    out = np.full(len(x), np.nan)
    if 0 < n < len(x):
        out[n:] = x[:-n]
    elif n == 0:
        out[:] = x
    return out


def _between(env: Mapping[str, Any], x: Any, lo: Any, hi: Any) -> Any:
    return np.logical_and(x >= lo, x <= hi)


def _plain(fn: Callable[..., Any]) -> Callable[..., Any]:
    return lambda env, *args: fn(*args)


#: name -> (function(env, *args), minimum args, maximum args)
FUNCTIONS: dict[str, tuple[Callable[..., Any], int, int]] = {
    "abs": (_plain(np.abs), 1, 1),
    "min": (_plain(np.minimum), 2, 2),
    "max": (_plain(np.maximum), 2, 2),
    "sqrt": (_plain(np.sqrt), 1, 1),
    "log": (_plain(np.log), 1, 1),
    "exp": (_plain(np.exp), 1, 1),
    "round": (_plain(np.round), 1, 1),
    "isnan": (_plain(np.isnan), 1, 1),
    "between": (_between, 3, 3),
    "rate": (_rate, 1, 1),
    "smooth": (_smooth, 1, 2),
    "prev": (_prev, 1, 2),
}

_BIN = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide,
        ast.Mod: np.mod, ast.Pow: np.power, ast.FloorDiv: np.floor_divide}
_CMP = {ast.Lt: np.less, ast.LtE: np.less_equal, ast.Gt: np.greater, ast.GtE: np.greater_equal,
        ast.Eq: np.equal, ast.NotEq: np.not_equal}
_CONSTANTS = {"nan": math.nan, "pi": math.pi, "true": 1.0, "false": 0.0}


def _normalise(text: str) -> str:
    text = re.sub(r"(?<![\w\])])\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]", r"\1", text)
    text = text.replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"!(?!=)", " not ", text)
    return text.strip()


class Expr:
    """A compiled expression. Call it with a mapping of name -> value or array."""

    def __init__(self, text: str):
        self.text = text
        source = _normalise(text)
        if not source:
            raise ExprError("empty expression")
        try:
            self._tree = ast.parse(source, mode="eval").body
        except SyntaxError as exc:
            raise ExprError(f"bad expression {text!r}: {exc.msg}") from None
        self.names: set[str] = set()
        self._check(self._tree)

    def __repr__(self) -> str:
        return f"Expr({self.text!r})"

    def _check(self, node: ast.AST) -> None:
        bad = ExprError(f"bad expression {self.text!r}: {type(node).__name__} is not allowed")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ExprError(f"bad expression {self.text!r}: only numbers are allowed")
        elif isinstance(node, ast.Name):
            if node.id.lower() not in _CONSTANTS:
                self.names.add(node.id)
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in _BIN:
                raise bad
            self._check(node.left)
            self._check(node.right)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
                raise bad
            self._check(node.operand)
        elif isinstance(node, ast.BoolOp):
            for value in node.values:
                self._check(value)
        elif isinstance(node, ast.Compare):
            if any(type(op) not in _CMP for op in node.ops):
                raise bad
            for value in (node.left, *node.comparators):
                self._check(value)
        elif isinstance(node, ast.IfExp):
            for value in (node.test, node.body, node.orelse):
                self._check(value)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.keywords:
                raise bad
            spec = FUNCTIONS.get(node.func.id)
            if spec is None:
                raise ExprError(f"bad expression {self.text!r}: unknown function "
                                f"{node.func.id!r} (known: {', '.join(sorted(FUNCTIONS))})")
            if not spec[1] <= len(node.args) <= spec[2]:
                raise ExprError(f"bad expression {self.text!r}: {node.func.id}() takes "
                                f"{spec[1]}" + (f" to {spec[2]}" if spec[2] != spec[1] else "")
                                + " argument(s)")
            for arg in node.args:
                self._check(arg)
        else:
            raise bad

    def __call__(self, env: Mapping[str, Any]) -> Any:
        with np.errstate(all="ignore"):
            return self._eval(self._tree, env)

    def missing(self, env: Mapping[str, Any]) -> list[str]:
        """Names the expression uses that ``env`` does not have."""
        return sorted(n for n in self.names if n not in env)

    def mask(self, env: Mapping[str, Any], length: int | None = None) -> np.ndarray:
        """Evaluate as a boolean column. Raises ExprError for unknown names."""
        missing = self.missing(env)
        if missing:
            raise ExprError(f"{self.text!r}: no channel named {', '.join(missing)}")
        result = self(env)
        if np.ndim(result) == 0:
            if length is None:
                length = len(env["t"]) if "t" in env else 1
            return np.full(length, bool(_truth(result)))
        return _truth(result)

    def test(self, env: Mapping[str, Any]) -> bool:
        """Evaluate on single values. Unknown or missing values make it false."""
        try:
            return bool(_truth(self(env)))
        except (ExprError, TypeError, ZeroDivisionError):
            return False

    def _eval(self, node: ast.AST, env: Mapping[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return float(node.value)        # type: ignore[arg-type]  # checked in _check
        if isinstance(node, ast.Name):
            value = env.get(node.id)
            if value is None:
                return _CONSTANTS.get(node.id.lower(), math.nan) if node.id not in env else math.nan
            return value
        if isinstance(node, ast.BinOp):
            return _BIN[type(node.op)](self._eval(node.left, env), self._eval(node.right, env))
        if isinstance(node, ast.UnaryOp):
            value = self._eval(node.operand, env)
            if isinstance(node.op, ast.Not):
                return np.logical_not(_truth(value))
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BoolOp):
            combine = np.logical_and if isinstance(node.op, ast.And) else np.logical_or
            truth = _truth(self._eval(node.values[0], env))
            for value in node.values[1:]:
                truth = combine(truth, _truth(self._eval(value, env)))
            return truth
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, env)
            result: Any = True
            for cmp, comparator in zip(node.ops, node.comparators):
                right = self._eval(comparator, env)
                result = np.logical_and(result, _CMP[type(cmp)](left, right))
                left = right
            return result
        if isinstance(node, ast.IfExp):
            return np.where(_truth(self._eval(node.test, env)), self._eval(node.body, env),
                            self._eval(node.orelse, env))
        if isinstance(node, ast.Call):
            fn = FUNCTIONS[node.func.id][0]     # type: ignore[attr-defined]
            return fn(env, *[self._eval(arg, env) for arg in node.args])
        raise ExprError(f"cannot evaluate {type(node).__name__}")


def _truth(value: Any) -> Any:
    """Truth of a number or array: non-zero and not NaN."""
    if isinstance(value, np.ndarray) and value.dtype == bool:
        return value
    if isinstance(value, (bool, np.bool_)):
        return value
    return np.logical_and(np.not_equal(value, 0), np.logical_not(np.isnan(value)))


def compile_expr(text: str) -> Expr:
    return Expr(text)
