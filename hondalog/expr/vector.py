"""Whole-column evaluation with numpy, for replay, filters and histograms.

Same language and the same answers as the closure compiler, but one numpy
operation per node instead of one Python call per node per row. NaN stands for
"no value". Stateful helpers run their scalar state object down the column, so
the two back ends cannot drift apart.
"""

from __future__ import annotations

import operator
from typing import TYPE_CHECKING, Any, Callable, Mapping

import numpy as np

from . import funcs
from .nodes import (Binary, Bool, Call, Compare, Cond, ExprError, Index, Node, Num,
                    Unary, Var)

if TYPE_CHECKING:
    from .compiler import Expr

Arr = Any
VFn = Callable[[Mapping[str, Any], np.ndarray], Arr]

_nan = np.nan
_INT_LIMIT = 2.0 ** 62


def _truth(a: Arr) -> Arr:
    return np.where(np.isnan(a), _nan, np.not_equal(a, 0))


def _div(op: Callable[[Arr, Arr], Arr]) -> Callable[[Arr, Arr], Arr]:
    def run(a: Arr, b: Arr) -> Arr:
        return funcs._finite(np.where(np.equal(b, 0), _nan, op(a, np.where(np.equal(b, 0), 1, b))))
    return run


def _bitop(op: Callable[[Any, Any], Any], shift: int = 0) -> Callable[[Arr, Arr], Arr]:
    def run(a: Arr, b: Arr) -> Arr:
        ai, bad_a = funcs._to_int(a)
        bi, bad_b = funcs._to_int(b)
        bad = bad_a | bad_b
        if shift:
            bad = bad | (bi < 0) | (bi >= 64)
            bi = np.where(bad, 0, bi)
            if shift > 0:
                bad = bad | (np.abs(ai.astype(float)) * np.exp2(bi.astype(float)) >= _INT_LIMIT)
                bi = np.where(bad, 0, bi)
        return np.where(bad, _nan, op(ai, bi).astype(float))
    return run


_BINARY: dict[str, Callable[[Arr, Arr], Arr]] = {
    "+": operator.add, "-": operator.sub, "*": operator.mul,
    "/": _div(np.true_divide), "//": _div(np.floor_divide), "%": _div(np.mod),
    "**": lambda a, b: funcs._finite(funcs._v_pow(a, b)),
    "&": _bitop(operator.and_), "|": _bitop(operator.or_), "^": _bitop(operator.xor),
    "<<": _bitop(operator.lshift, 1), ">>": _bitop(operator.rshift, -1),
}

_COMPARE: dict[str, Callable[[Arr, Arr], Arr]] = {
    "<": np.less, "<=": np.less_equal, ">": np.greater, ">=": np.greater_equal,
    "==": np.equal, "!=": np.not_equal,
}


def _cmp(op: Callable[[Arr, Arr], Arr], a: Arr, b: Arr) -> Arr:
    return np.where(np.isnan(a) | np.isnan(b), _nan, op(a, b))


class _Vector:
    def __init__(self, text: str, constants: Mapping[str, float]):
        self.text = text
        self.constants = constants

    def compile(self, node: Node) -> VFn:
        if isinstance(node, Num):
            value = np.float64(node.value)
            return lambda cols, t: value
        if isinstance(node, Var):
            return self._var(node)
        if isinstance(node, Index):
            raise ExprError(f"{node.name}[...] only works in channel formulas", self.text, node.pos)
        if isinstance(node, Unary):
            return self._unary(node)
        if isinstance(node, Binary):
            lf, rf, op = self.compile(node.left), self.compile(node.right), _BINARY[node.op]
            return lambda cols, t: op(lf(cols, t), rf(cols, t))
        if isinstance(node, Compare):
            return self._compare(node)
        if isinstance(node, Bool):
            return self._bool(node)
        if isinstance(node, Cond):
            test, then, other = (self.compile(n) for n in (node.test, node.then, node.other))

            def cond(cols: Mapping[str, Any], t: np.ndarray) -> Arr:
                c = test(cols, t)
                return np.where(np.isnan(c), _nan,
                                np.where(np.not_equal(c, 0), then(cols, t), other(cols, t)))
            return cond
        return self._call(node)

    def _var(self, node: Var) -> VFn:
        name = node.name
        if name in self.constants:
            value = np.float64(self.constants[name])
            return lambda cols, t: value

        def column(cols: Mapping[str, Any], t: np.ndarray) -> Arr:
            try:
                raw = cols[name]
            except KeyError:
                raise ExprError(f"no column named {name!r}", self.text, node.pos) from None
            col = np.asarray(raw, dtype=float)
            if col.shape != t.shape:
                raise ExprError(f"column {name!r} has {col.size} rows, expected {t.size}",
                                self.text, node.pos)
            return col
        if node.lag:
            lag = node.lag
            return lambda cols, t: funcs.run_state(funcs.Lag(lag), t.tolist(), column(cols, t))
        return column

    def _unary(self, node: Unary) -> VFn:
        f = self.compile(node.operand)
        if node.op == "-":
            return lambda cols, t: -f(cols, t)
        if node.op == "+":
            return f
        if node.op == "~":
            def invert(cols: Mapping[str, Any], t: np.ndarray) -> Arr:
                ai, bad = funcs._to_int(f(cols, t))
                return np.where(bad, _nan, (~ai).astype(float))
            return invert
        return lambda cols, t: _cmp(np.equal, f(cols, t), 0.0)

    def _compare(self, node: Compare) -> VFn:
        left = self.compile(node.left)
        links = [(_COMPARE[op], self.compile(r)) for op, r in zip(node.ops, node.rights)]

        def run(cols: Mapping[str, Any], t: np.ndarray) -> Arr:
            a = left(cols, t)
            out: Arr = None
            for op, rf in links:
                b = rf(cols, t)
                link = _cmp(op, a, b)
                out = link if out is None else np.where(np.isnan(out), _nan,
                                                        np.where(out == 0, 0.0, link))
                a = b
            return out
        return run

    def _bool(self, node: Bool) -> VFn:
        fs = [self.compile(v) for v in node.values]
        stop = 0.0 if node.op == "and" else 1.0       # the value that ends the chain early

        def run(cols: Mapping[str, Any], t: np.ndarray) -> Arr:
            out = _truth(fs[0](cols, t))
            for f in fs[1:]:
                out = np.where(np.isnan(out), _nan,
                               np.where(out == stop, stop, _truth(f(cols, t))))
            return out
        return run

    def _call(self, node: Call) -> VFn:
        name = node.func
        args = [self.compile(a) for a in node.args]
        if name in funcs.PURE:
            vec = funcs.PURE[name].vector
            return lambda cols, t: funcs._finite(vec(*[a(cols, t) for a in args]))
        if name in funcs.STATEFUL:
            spec = funcs.STATEFUL[name]
            zero = np.zeros(0)
            params = [float(a({}, zero)) for a in args[1:]]      # constants (checked earlier)
            x = args[0]

            def stateful(cols: Mapping[str, Any], t: np.ndarray) -> Arr:
                col = np.broadcast_to(np.asarray(x(cols, t), dtype=float), t.shape)
                return funcs.run_state(spec.factory(*params), t.tolist(), col)
            return stateful
        if name == "time":
            return lambda cols, t: t
        if name == "valid":
            x = args[0]
            return lambda cols, t: (~np.isnan(x(cols, t))).astype(float)

        def coalesce(cols: Mapping[str, Any], t: np.ndarray) -> Arr:
            out = args[0](cols, t)
            for a in args[1:]:
                out = np.where(np.isnan(out), a(cols, t), out)
            return out
        return coalesce


def compile_vector(node: Node, text: str, constants: Mapping[str, float]) -> VFn:
    return _Vector(text, constants).compile(node)


def run(fn: VFn, expr: "Expr", columns: Mapping[str, Any], t: Any = None) -> np.ndarray:
    if t is None:
        if expr.uses_time:
            raise ExprError("this expression looks at time: pass the time column", expr.text)
        n = next((np.size(columns[name]) for name in expr.deps if name in columns), 1)
        t_arr = np.zeros(n)
    else:
        t_arr = np.asarray(t, dtype=float)
    with np.errstate(all="ignore"):
        out = np.asarray(fn(columns, t_arr), dtype=float)
        out = np.where(np.isfinite(out), out, _nan)
    return np.array(np.broadcast_to(out, t_arr.shape))
