"""Named filters: boolean expressions that gate histograms, scatter plots and exports.

The text is a ``hondalog.expr`` expression over the log's channels, the same
language as math channels, alarms and triggers: ``ect > 80 and closed_loop``,
``abs(rate(tps)) < 5``, ``[rpm] > 3000 && [map] < 40``. A row where the
expression has no value (a channel is NaN there) does not pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import numpy as np

from ..data.model import BoolArray, Table
from ..expr import Expr, compile_expr


@dataclass
class Filter:
    """One named condition, for example ``Filter("warm", "ect > 80")``."""

    name: str
    expr: str
    enabled: bool = True
    _compiled: Expr | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._compiled = compile_expr(self.expr)     # raises ExprError on bad text

    @property
    def compiled(self) -> Expr:
        assert self._compiled is not None
        return self._compiled

    def mask(self, table: Table) -> BoolArray:
        return self.compiled.mask(table.env(), table.t)

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "expr": self.expr, "enabled": self.enabled}

    @classmethod
    def from_json(cls, doc: Mapping[str, Any]) -> "Filter":
        return cls(str(doc["name"]), str(doc["expr"]), bool(doc.get("enabled", True)))


#: Starting points. They use the default map's channel names.
PRESETS = (
    Filter("warm", "ect > 80", enabled=False),
    Filter("closed loop", "closed_loop", enabled=False),
    Filter("steady throttle", "abs(rate(tps)) < 5", enabled=False),
    Filter("engine running", "rpm > 400", enabled=False),
    Filter("link up", "link", enabled=False),
)


class FilterSet:
    """A list of filters applied together (logical AND of the enabled ones).

    Each filter's mask is cached per table, so toggling one filter only costs
    one AND over the cached masks.
    """

    def __init__(self, filters: Iterable[Filter] = ()):
        self.filters: list[Filter] = list(filters)
        self._cache: dict[tuple[int, str], BoolArray] = {}
        self._table_id = 0

    def add(self, name: str, expr: str, enabled: bool = True) -> Filter:
        flt = Filter(name, expr, enabled)
        self.filters = [f for f in self.filters if f.name != name] + [flt]
        return flt

    def remove(self, name: str) -> None:
        self.filters = [f for f in self.filters if f.name != name]

    def enable(self, name: str, on: bool = True) -> None:
        for flt in self.filters:
            if flt.name == name:
                flt.enabled = on

    def get(self, name: str) -> Filter | None:
        return next((f for f in self.filters if f.name == name), None)

    @property
    def active(self) -> list[Filter]:
        return [f for f in self.filters if f.enabled]

    def channels(self) -> set[str]:
        """Channel names the enabled filters need in the table."""
        names: set[str] = set()
        for flt in self.active:
            names |= flt.compiled.deps
        names.discard("t")
        return names

    def mask(self, table: Table) -> BoolArray:
        """True for rows that pass every enabled filter."""
        if id(table) != self._table_id:
            self._cache.clear()
            self._table_id = id(table)
        out = np.ones(len(table), dtype=bool)
        for flt in self.active:
            key = (id(table), flt.expr)
            mask = self._cache.get(key)
            if mask is None:
                mask = self._cache[key] = flt.mask(table)
            out &= mask
        return out

    def to_json(self) -> list[dict[str, Any]]:
        return [f.to_json() for f in self.filters]

    @classmethod
    def from_json(cls, docs: Iterable[Mapping[str, Any]]) -> "FilterSet":
        return cls(Filter.from_json(d) for d in docs)
