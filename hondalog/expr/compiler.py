"""Compile an AST into nested closures. No ``eval``, no ``exec``, no codegen.

Every node becomes one small Python function ``f(values, t)``. Constant
sub-trees are folded at compile time. Stateful helpers (``avg``, ``[x-1]``
...) are hoisted: they run once per ``Expr.eval`` call, before the main tree,
so their windows advance on every sample even inside an untaken branch.

An evaluation problem (missing value, divide by zero, math domain error) never
escapes ``Expr.eval``: the result is ``None``, which the app shows as invalid.
"""

from __future__ import annotations

import difflib
import math
import operator
from typing import Any, Callable, Collection, Mapping

import numpy as np

from . import funcs
from .nodes import (Binary, Bool, Call, Compare, Cond, ExprError, Index, Node, Num,
                    Unary, Var, walk)
from .parser import parse

Values = Mapping[str, Any]
Fn = Callable[[Values, float], Any]

_NC: Any = object()          # "not a constant" marker
_INT_LIMIT = 1 << 62


class Invalid(Exception):
    """Raised inside a closure when an input has no value right now."""


class EvalError(Exception):
    """Raised by ``Expr.eval_strict`` when the expression has no value."""


# ---- operator closures ------------------------------------------------------

def _add(lf: Fn, lc: Any, r: Fn, rc: Any) -> Fn:
    if rc is not _NC:
        return lambda v, t: lf(v, t) + rc
    if lc is not _NC:
        return lambda v, t: lc + r(v, t)
    return lambda v, t: lf(v, t) + r(v, t)


def _sub(lf: Fn, lc: Any, r: Fn, rc: Any) -> Fn:
    if rc is not _NC:
        return lambda v, t: lf(v, t) - rc
    if lc is not _NC:
        return lambda v, t: lc - r(v, t)
    return lambda v, t: lf(v, t) - r(v, t)


def _mul(lf: Fn, lc: Any, r: Fn, rc: Any) -> Fn:
    if rc is not _NC:
        return lambda v, t: lf(v, t) * rc
    if lc is not _NC:
        return lambda v, t: lc * r(v, t)
    return lambda v, t: lf(v, t) * r(v, t)


def _div(lf: Fn, lc: Any, r: Fn, rc: Any) -> Fn:
    if rc is not _NC and rc != 0:
        return lambda v, t: lf(v, t) / rc
    if lc is not _NC:
        return lambda v, t: lc / r(v, t)
    return lambda v, t: lf(v, t) / r(v, t)


def _int_result(x: int) -> int:
    if not -_INT_LIMIT < x < _INT_LIMIT:
        raise Invalid("integer overflow")
    return x


def _int(a: Any) -> int:
    """Whole-number view of a value for the bit operators."""
    return _int_result(int(a))


def _lshift(a: Any, b: Any) -> int:
    n = int(b)
    if not 0 <= n < 64:
        raise Invalid("shift count out of range")
    return _int_result(_int(a) << n)


def _rshift(a: Any, b: Any) -> int:
    n = int(b)
    if not 0 <= n < 64:
        raise Invalid("shift count out of range")
    return _int(a) >> n


def _generic(op: Callable[[Any, Any], Any]) -> Callable[[Fn, Any, Fn, Any], Fn]:
    def make(lf: Fn, lc: Any, r: Fn, rc: Any) -> Fn:
        return lambda v, t: op(lf(v, t), r(v, t))
    return make


_BINARY: dict[str, Callable[[Fn, Any, Fn, Any], Fn]] = {
    "+": _add, "-": _sub, "*": _mul, "/": _div,
    "//": _generic(operator.floordiv),
    "%": _generic(operator.mod),
    "**": _generic(math.pow),
    "&": _generic(lambda a, b: _int(a) & _int(b)),
    "|": _generic(lambda a, b: _int(a) | _int(b)),
    "^": _generic(lambda a, b: _int(a) ^ _int(b)),
    "<<": _generic(_lshift),
    ">>": _generic(_rshift),
}

_COMPARE: dict[str, Callable[[Any, Any], bool]] = {
    "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
    "==": operator.eq, "!=": operator.ne,
}


def _cmp1(op: str, lf: Fn, r: Fn, rc: Any) -> Fn:
    """One comparison, written out for the common operators (alarm limits)."""
    if rc is not _NC:
        if op == ">":
            return lambda v, t: lf(v, t) > rc
        if op == "<":
            return lambda v, t: lf(v, t) < rc
        if op == ">=":
            return lambda v, t: lf(v, t) >= rc
        if op == "<=":
            return lambda v, t: lf(v, t) <= rc
    fn = _COMPARE[op]
    return lambda v, t: fn(lf(v, t), r(v, t))


# ---- the compiler -----------------------------------------------------------

class _Compiler:
    def __init__(self, text: str, names: Collection[str] | None,
                 constants: Mapping[str, float], arrays: Collection[str]):
        self.text = text
        self.names = names
        self.constants = constants
        self.arrays = arrays
        self.deps: set[str] = set()
        self.functions: set[str] = set()
        self.steps: list[Fn] = []
        self.states: list[funcs.State] = []
        self.slots: list[list[Any]] = []
        self.uses_time = False

    def error(self, message: str, node: Node) -> ExprError:
        return ExprError(message, self.text, node.pos)

    def compile(self, node: Node) -> tuple[Fn, Any]:
        if isinstance(node, Num):
            return self._const(node.value)
        if isinstance(node, Var):
            return self._var(node)
        if isinstance(node, Index):
            return self._index(node)
        if isinstance(node, Unary):
            return self._unary(node)
        if isinstance(node, Binary):
            return self._binary(node)
        if isinstance(node, Compare):
            return self._compare(node)
        if isinstance(node, Bool):
            return self._bool(node)
        if isinstance(node, Cond):
            return self._cond(node)
        return self._call(node)

    @staticmethod
    def _const(value: Any) -> tuple[Fn, Any]:
        return (lambda v, t: value), value

    def _fold(self, fn: Fn, *consts: Any) -> tuple[Fn, Any]:
        """If every input was constant, run the node now and keep the answer."""
        if any(c is _NC for c in consts):
            return fn, _NC
        try:
            value = fn({}, 0.0)
        except Exception:
            return fn, _NC          # e.g. 1/0: stays a runtime 'invalid'
        return self._const(value)

    # -- leaves --

    def _unknown(self, node: Var | Index) -> ExprError:
        known = sorted(set(self.names or ()) | set(self.constants))
        near = difflib.get_close_matches(node.name, known, n=3)
        hint = f" (did you mean {', '.join(near)}?)" if near else ""
        return self.error(f"unknown name {node.name!r}{hint}", node)

    def _var(self, node: Var) -> tuple[Fn, Any]:
        name = node.name
        if name in self.arrays:
            raise self.error(f"{name!r} is a list of bytes: write {name}[0], {name}[1] ...", node)
        if name in self.constants and (self.names is None or name not in self.names):
            if node.lag:
                raise self.error(f"{name!r} is a constant, it has no previous sample", node)
            return self._const(self.constants[name])
        if self.names is not None and name not in self.names:
            raise self._unknown(node)
        self.deps.add(name)

        def var(v: Values, t: float) -> Any:
            x = v[name]
            if x is None or x != x:
                raise Invalid(name)
            return x
        if node.lag:
            return self._hoist(funcs.Lag(node.lag), var), _NC
        return var, _NC

    def _index(self, node: Index) -> tuple[Fn, Any]:
        name = node.name
        if name not in self.arrays:
            if self.names is not None and name not in self.names and name not in self.constants:
                raise self._unknown(node)
            raise self.error(f"{name!r} cannot be indexed", node)
        idx, idx_c = self.compile(node.index)
        if idx_c is not _NC:
            i = int(idx_c)
            return (lambda v, t: v[name][i]), _NC
        return (lambda v, t: v[name][int(idx(v, t))]), _NC

    # -- operators --

    def _unary(self, node: Unary) -> tuple[Fn, Any]:
        f, c = self.compile(node.operand)
        fn: Fn
        if node.op == "-":
            fn = lambda v, t: -f(v, t)                      # noqa: E731
        elif node.op == "+":
            fn = f
        elif node.op == "~":
            fn = lambda v, t: ~_int(f(v, t))                # noqa: E731
        else:
            fn = lambda v, t: 0 if f(v, t) else 1           # noqa: E731
        return self._fold(fn, c)

    def _binary(self, node: Binary) -> tuple[Fn, Any]:
        lf, lc = self.compile(node.left)
        rf, rc = self.compile(node.right)
        return self._fold(_BINARY[node.op](lf, lc, rf, rc), lc, rc)

    def _compare(self, node: Compare) -> tuple[Fn, Any]:
        lf, lc = self.compile(node.left)
        rights = [self.compile(r) for r in node.rights]
        fn: Fn
        if len(rights) == 1:
            fn = _cmp1(node.ops[0], lf, *rights[0])
        else:
            links = [(_COMPARE[op], rf) for op, (rf, _) in zip(node.ops, rights)]

            def fn(v: Values, t: float) -> Any:
                a = lf(v, t)
                for op, rf in links:
                    b = rf(v, t)
                    if not op(a, b):
                        return False
                    a = b
                return True
        return self._fold(fn, lc, *(c for _, c in rights))

    def _bool(self, node: Bool) -> tuple[Fn, Any]:
        parts = [self.compile(v) for v in node.values]
        fs = [f for f, _ in parts]
        fn: Fn
        if len(fs) == 2:
            a, b = fs
            if node.op == "and":
                fn = lambda v, t: 1 if a(v, t) and b(v, t) else 0       # noqa: E731
            else:
                fn = lambda v, t: 1 if a(v, t) or b(v, t) else 0        # noqa: E731
        elif node.op == "and":
            def fn(v: Values, t: float) -> Any:
                for f in fs:
                    if not f(v, t):
                        return 0
                return 1
        else:
            def fn(v: Values, t: float) -> Any:
                for f in fs:
                    if f(v, t):
                        return 1
                return 0
        return self._fold(fn, *(c for _, c in parts))

    def _cond(self, node: Cond) -> tuple[Fn, Any]:
        test, tc = self.compile(node.test)
        then, hc = self.compile(node.then)
        other, oc = self.compile(node.other)
        if tc is not _NC:                    # constant test: keep one branch
            return (then, hc) if tc else (other, oc)
        return (lambda v, t: then(v, t) if test(v, t) else other(v, t)), _NC

    # -- calls --

    def _call(self, node: Call) -> tuple[Fn, Any]:
        name = node.func
        self.functions.add(name)
        if name in funcs.PURE:
            return self._pure(node, funcs.PURE[name])
        if name in funcs.STATEFUL:
            return self._stateful(node, funcs.STATEFUL[name])
        if name in funcs.SPECIAL:
            return self._special(node)
        known = sorted({*funcs.PURE, *funcs.STATEFUL, *funcs.SPECIAL})
        near = difflib.get_close_matches(name, known, n=3)
        hint = f" (did you mean {', '.join(near)}?)" if near else ""
        raise self.error(f"unknown function {name!r}{hint}", node)

    def _arity(self, node: Call, lo: int, hi: int | None) -> None:
        n = len(node.args)
        if n < lo or (hi is not None and n > hi):
            want = f"{lo}" if hi == lo else f"{lo} or more" if hi is None else f"{lo} to {hi}"
            raise self.error(f"{node.func}() takes {want} arguments, got {n}", node)

    def _constant_args(self, node: Call, start: int) -> list[float]:
        out = []
        for i, arg in enumerate(node.args[start:], start):
            _, c = self.compile(arg)
            if c is _NC:
                raise self.error(f"argument {i + 1} of {node.func}() must be a constant", arg)
            out.append(c)
        return out

    def _pure(self, node: Call, spec: funcs.Pure) -> tuple[Fn, Any]:
        self._arity(node, spec.min_args, spec.max_args)
        scalar = spec.scalar
        n_dyn = len(node.args) if spec.const_from is None else spec.const_from
        tail = () if spec.const_from is None else tuple(self._constant_args(node, n_dyn))
        if spec.pairs:
            _check_pairs(self, node, tail)
        parts = [self.compile(a) for a in node.args[:n_dyn]]
        # Trailing constants (poly coefficients, clamp limits) are bound once.
        while parts and parts[-1][1] is not _NC and len(parts) > 1:
            tail = (parts.pop()[1], *tail)
        fs = [f for f, _ in parts]
        fn: Fn
        if len(fs) == 1:
            a = fs[0]
            if tail:
                fn = lambda v, t: scalar(a(v, t), *tail)            # noqa: E731
            else:
                fn = lambda v, t: scalar(a(v, t))                   # noqa: E731
        elif len(fs) == 2 and not tail:
            a, b = fs
            fn = lambda v, t: scalar(a(v, t), b(v, t))              # noqa: E731
        else:
            fn = lambda v, t: scalar(*[f(v, t) for f in fs], *tail)  # noqa: E731
        return self._fold(fn, *(c for _, c in parts))

    def _stateful(self, node: Call, spec: funcs.Stateful) -> tuple[Fn, Any]:
        self._arity(node, 1 + spec.params - spec.optional, 1 + spec.params)
        params = self._constant_args(node, 1)
        try:
            state = spec.factory(*params)
        except (ValueError, TypeError) as exc:
            raise self.error(f"{node.func}(): {exc}", node) from None
        arg, _ = self.compile(node.args[0])
        self.uses_time = True
        return self._hoist(state, arg), _NC

    def _hoist(self, state: funcs.State, arg: Fn) -> Fn:
        slot: list[Any] = [None]
        update = state.update

        def step(v: Values, t: float) -> None:
            try:
                slot[0] = update(t, arg(v, t))
            except Exception:
                slot[0] = None

        def read(v: Values, t: float) -> Any:
            x = slot[0]
            if x is None:
                raise Invalid("no value yet")
            return x
        self.steps.append(step)
        self.states.append(state)
        self.slots.append(slot)
        return read

    def _special(self, node: Call) -> tuple[Fn, Any]:
        if node.func == "time":
            self._arity(node, 0, 0)
            self.uses_time = True
            return (lambda v, t: t), _NC
        if node.func == "valid":
            self._arity(node, 1, 1)
            f, _ = self.compile(node.args[0])

            def valid(v: Values, t: float) -> Any:
                try:
                    r = f(v, t)
                except Exception:
                    return 0
                return 1 if r == r else 0
            return valid, _NC
        self._arity(node, 2, None)
        fs = [self.compile(a)[0] for a in node.args]

        def coalesce(v: Values, t: float) -> Any:
            for f in fs:
                try:
                    r = f(v, t)
                except Exception:
                    continue
                if r == r:
                    return r
            raise Invalid("nothing has a value")
        return coalesce, _NC


def _check_pairs(comp: _Compiler, node: Call, tail: tuple[float, ...]) -> None:
    if len(tail) % 2:
        raise comp.error(f"{node.func}() needs x, y pairs after the input", node)
    xs = tail[0::2]
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise comp.error(f"{node.func}(): the x values must go up", node)


# ---- public -----------------------------------------------------------------

class Expr:
    """A compiled expression. Build with :func:`compile_expr`.

    ``eval(values, t)`` is the hot path: ``values`` maps names to numbers (or
    ``None`` for "no value"), ``t`` is the sample time in seconds. It returns
    a float, or ``None`` when the expression has no value right now.
    """

    __slots__ = ("text", "node", "deps", "functions", "stateful", "uses_time", "constant",
                 "eval", "_fn", "_steps", "_states", "_slots", "_constants", "_vector")

    def __init__(self, text: str, node: Node, comp: _Compiler, fn: Fn, const: Any):
        self.text = text
        self.node = node
        self.deps: frozenset[str] = frozenset(comp.deps)
        self.functions: frozenset[str] = frozenset(comp.functions)
        self.stateful = bool(comp.steps)
        self.uses_time = comp.uses_time
        self.constant: float | None = None if const is _NC else float(const)
        self._fn = fn
        self._steps = tuple(comp.steps)
        self._states = tuple(comp.states)
        self._slots = tuple(comp.slots)
        self._constants = {k: v for k, v in comp.constants.items()
                           if comp.names is None or k not in comp.names}
        self._vector: Any = None
        self.eval: Callable[..., float | None] = _make_eval(fn, self._steps)

    def __repr__(self) -> str:
        return f"Expr({self.text!r})"

    def eval_strict(self, values: Values, t: float = 0.0) -> float:
        """Like ``eval`` but raises EvalError instead of returning None."""
        try:
            for step in self._steps:
                step(values, t)
            return self._fn(values, t)          # type: ignore[no-any-return]
        except Exception as exc:
            raise EvalError(f"{self.text}: {type(exc).__name__}: {exc}") from exc

    def truth(self, values: Values, t: float = 0.0) -> bool | None:
        """The expression as a condition: True, False, or None for 'unknown'."""
        r = self.eval(values, t)
        return None if r is None else r != 0.0

    def reset(self) -> None:
        """Forget all window history (new session, new log, seek in replay)."""
        for state in self._states:
            state.reset()
        for slot in self._slots:
            slot[0] = None

    def eval_array(self, columns: Mapping[str, Any], t: Any = None) -> np.ndarray:
        """Evaluate over whole columns at once. NaN means 'no value'.

        ``columns`` maps names to equal-length arrays (NaN = missing), ``t`` is
        the time column in seconds. Gives the same numbers as calling ``eval``
        once per row on a fresh expression, without touching live state.
        """
        from . import vector
        if self._vector is None:
            self._vector = vector.compile_vector(self.node, self.text, self._constants)
        return vector.run(self._vector, self, columns, t)


def _make_eval(fn: Fn, steps: tuple[Fn, ...]) -> Callable[..., float | None]:
    isfinite = math.isfinite
    if not steps:
        def eval_plain(values: Values, t: float = 0.0) -> float | None:
            try:
                r = float(fn(values, t))
            except Exception:
                return None
            return r if isfinite(r) else None
        return eval_plain

    def eval_stateful(values: Values, t: float = 0.0) -> float | None:
        for step in steps:
            step(values, t)
        try:
            r = float(fn(values, t))
        except Exception:
            return None
        return r if isfinite(r) else None
    return eval_stateful


def compile_expr(text: str, names: Collection[str] | None = None,
                 constants: Mapping[str, float] | None = None,
                 arrays: Collection[str] = ()) -> Expr:
    """Parse, check and compile ``text``. Raises ExprError on any problem.

    names      the variables the expression may use; ``None`` allows any name
               (it is then looked up at evaluation time).
    constants  named numbers folded in at compile time (injector size ...).
               A variable of the same name wins over a constant.
    arrays     variables that are sequences and must be indexed (``b[0]``).
    """
    node = parse(text)
    comp = _Compiler(text.strip(), names, constants or {}, arrays)
    fn, const = comp.compile(node)
    return Expr(text.strip(), node, comp, fn, const)


def references(text: str) -> set[str]:
    """Names an expression mentions, without compiling it."""
    return {n.name for n in walk(parse(text)) if isinstance(n, (Var, Index))}
