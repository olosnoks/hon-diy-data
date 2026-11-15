"""MathChannel definition and the engine that evaluates a set of them."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Collection, Iterable, Mapping, Sequence

import numpy as np

from ..expr import Expr, ExprError, check_units, compile_expr, references

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class MathError(ValueError):
    """A math channel definition is wrong (bad expression, unknown input, cycle)."""


@dataclass
class MathChannel:
    """One derived channel. Field names match ``addressmap.Channel`` where they
    mean the same thing, so the UI can treat both alike."""
    name: str
    expr: str
    label: str = ""
    units: str = ""
    min: float = 0.0
    max: float = 100.0
    decimals: int = 1
    gauge: bool = False
    plot: bool = False
    kind: str = "value"          # "value" or "flag"
    description: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise MathError(f"bad math channel name {self.name!r}")
        if not isinstance(self.expr, str) or not self.expr.strip():
            raise MathError(f"{self.name}: no expression")
        if self.kind not in ("value", "flag"):
            raise MathError(f"{self.name}: kind must be 'value' or 'flag'")
        self.label = self.label or self.name
        if self.kind == "flag":
            self.min, self.max, self.decimals = 0.0, 1.0, 0

    def format(self, value: float | None) -> str:
        if value is None or value != value:
            return "--"
        return f"{value:.{self.decimals}f}"


@dataclass
class _Compiled:
    channel: MathChannel
    expr: Expr
    base_inputs: frozenset[str] = field(default_factory=frozenset)


class MathEngine:
    """Evaluates math channels in dependency order.

    channels   the definitions (disabled ones are skipped)
    inputs     names of the measured channels expressions may read
    constants  named numbers (``[constants]`` in the address map)
    units      unit text per input name, for the mixed-unit warnings

    Raises MathError for a bad expression, an unknown name, a name clash or a
    cycle. ``warnings`` lists unit mix-ups; they do not stop anything.
    """

    def __init__(self, channels: Sequence[MathChannel], inputs: Iterable[str],
                 constants: Mapping[str, float] | None = None,
                 units: Mapping[str, str] | None = None):
        self.inputs = frozenset(inputs)
        self.constants = dict(constants or {})
        active = [c for c in channels if c.enabled]
        by_name: dict[str, MathChannel] = {}
        for ch in active:
            if ch.name in by_name:
                raise MathError(f"duplicate math channel {ch.name!r}")
            if ch.name in self.inputs:
                raise MathError(f"math channel {ch.name!r} has the same name as a measured channel")
            if ch.name in self.constants:
                raise MathError(f"math channel {ch.name!r} has the same name as a constant")
            by_name[ch.name] = ch
        self.channels: list[MathChannel] = _sort(by_name)
        known = self.inputs | set(by_name)
        unit_map = dict(units or {})
        unit_map.update({c.name: c.units for c in active if c.units})
        self.warnings: list[str] = []
        self._compiled: list[_Compiled] = []
        base: dict[str, frozenset[str]] = {}
        for ch in self.channels:
            try:
                expr = compile_expr(ch.expr, names=known, constants=self.constants)
            except ExprError as exc:
                raise MathError(f"math channel {ch.name!r}: {exc}") from exc
            self.warnings += [f"{ch.name}: {w}" for w in
                              check_units(expr.node, unit_map, ch.units or None)]
            deps: set[str] = set()
            for dep in expr.deps:
                deps |= base.get(dep, frozenset({dep}))
            base[ch.name] = frozenset(deps)
            self._compiled.append(_Compiled(ch, expr, base[ch.name]))
        self.names: list[str] = [c.name for c in self.channels]
        self._plan = [(c.channel.name, c.expr.eval) for c in self._compiled]
        self._last: dict[str, float | None] = dict.fromkeys(self.names)

    def __len__(self) -> int:
        return len(self._compiled)

    def channel(self, name: str) -> MathChannel | None:
        return next((c for c in self.channels if c.name == name), None)

    def base_inputs(self, name: str) -> frozenset[str]:
        """Measured channels a math channel depends on, through other math
        channels too. The poll scheduler can use this to pick its rate."""
        return next(c.base_inputs for c in self._compiled if c.channel.name == name)

    def expr(self, name: str) -> Expr:
        return next(c.expr for c in self._compiled if c.channel.name == name)

    # -- live --

    def update(self, values: Mapping[str, float | None], t: float,
               changed: Collection[str] | None = None) -> dict[str, float | None]:
        """Evaluate for one sample; returns ``{math name: value or None}``.

        ``changed`` names the inputs that are new in this sample. When given,
        only math channels fed by one of them are re-evaluated (so each runs
        at the rate of its fastest input); the rest keep their last value.
        """
        scope: dict[str, Any] = dict(values)
        out: dict[str, float | None] = {}
        if changed is None:
            for name, ev in self._plan:
                scope[name] = out[name] = ev(scope, t)
        else:
            fresh = set(changed)
            last = self._last
            for comp in self._compiled:
                name = comp.channel.name
                if fresh.isdisjoint(comp.base_inputs) and comp.base_inputs:
                    scope[name] = out[name] = last[name]
                else:
                    scope[name] = out[name] = comp.expr.eval(scope, t)
        self._last = out
        return out

    def extend(self, values: Mapping[str, float | None], t: float) -> dict[str, float | None]:
        """``values`` plus the math channels, as one new dict."""
        merged = dict(values)
        merged.update(self.update(values, t))
        return merged

    def reset(self) -> None:
        """Forget window history (new connection, new log, seek)."""
        for comp in self._compiled:
            comp.expr.reset()
        self._last = dict.fromkeys(self.names)

    # -- replay --

    def compute_series(self, t: Any, columns: Mapping[str, Any]) -> dict[str, np.ndarray]:
        """Evaluate every math channel over a whole log.

        ``t`` is the time column in seconds, ``columns`` maps measured channel
        names to equal-length arrays with NaN for missing values. Returns one
        array per math channel, NaN where it has no value. Live state is not
        touched, so this is safe while polling.
        """
        t_arr = np.asarray(t, dtype=float)
        scope: dict[str, Any] = {k: np.asarray(v, dtype=float) for k, v in columns.items()}
        missing = self.inputs - set(scope)
        blank = np.full(t_arr.shape, np.nan)
        for name in missing:
            scope[name] = blank
        out: dict[str, np.ndarray] = {}
        for comp in self._compiled:
            scope[comp.channel.name] = out[comp.channel.name] = comp.expr.eval_array(scope, t_arr)
        return out


def _sort(by_name: Mapping[str, MathChannel]) -> list[MathChannel]:
    """Topological order; raises MathError naming the loop if there is one."""
    deps: dict[str, list[str]] = {}
    for name, ch in by_name.items():
        try:
            refs = references(ch.expr)
        except ExprError as exc:
            raise MathError(f"math channel {name!r}: {exc}") from exc
        deps[name] = sorted(r for r in refs if r in by_name)
    order: list[MathChannel] = []
    state: dict[str, int] = {}                 # 1 = on the current path, 2 = done

    def visit(name: str, path: list[str]) -> None:
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            loop = path[path.index(name):] + [name]
            raise MathError("math channels refer to each other in a loop: " + " -> ".join(loop))
        state[name] = 1
        for dep in deps[name]:
            visit(dep, path + [name])
        state[name] = 2
        order.append(by_name[name])

    for name in by_name:
        visit(name, [])
    return order
