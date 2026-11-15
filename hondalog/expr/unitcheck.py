"""Light unit checking: catch 'kPa + °C' and 'ect > batt', nothing clever.

Units are the free-text strings channels declare. Adding, subtracting or
comparing two values whose declared units differ gives a warning. Scaling by a
plain number keeps the unit; multiplying two channels gives "unknown", which
is compatible with anything. Warnings never stop an expression from running.
"""

from __future__ import annotations

from typing import Mapping

from .nodes import Binary, Bool, Call, Compare, Cond, Index, Node, Num, Unary, Var

_SAME_AS_FIRST = {"abs", "avg", "minw", "maxw", "smooth", "median", "ema", "prev", "round",
                  "floor", "ceil", "int", "float", "delta", "stdev"}
_ALL_ALIKE = {"min", "max", "clamp", "between", "coalesce"}


def _norm(unit: str | None) -> str | None:
    unit = (unit or "").strip()
    return unit.casefold().replace(" ", "") or None


class _Checker:
    def __init__(self, units: Mapping[str, str]):
        self.units = units
        self.warnings: list[str] = []
        self.names: dict[str, str] = {}          # normalised -> as first written

    def _known(self, unit: str | None) -> str | None:
        key = _norm(unit)
        if key is not None:
            self.names.setdefault(key, (unit or "").strip())
        return key

    def _show(self, key: str) -> str:
        return self.names.get(key, key)

    def _same(self, a: str | None, b: str | None, what: str) -> str | None:
        if a is not None and b is not None and a != b:
            self.warnings.append(f"{what} mixes {self._show(a)} and {self._show(b)}")
            return None
        return a if a is not None else b

    def unit(self, node: Node) -> str | None:
        if isinstance(node, Num):
            return None
        if isinstance(node, Var):
            return self._known(self.units.get(node.name))
        if isinstance(node, Index):
            return None
        if isinstance(node, Unary):
            inner = self.unit(node.operand)
            return inner if node.op in "+-" else None
        if isinstance(node, Binary):
            left, right = self.unit(node.left), self.unit(node.right)
            if node.op in ("+", "-"):
                return self._same(left, right, f"'{node.op}'")
            if node.op == "*":
                if isinstance(node.right, Num):
                    return left
                return right if isinstance(node.left, Num) else None
            if node.op == "/" and isinstance(node.right, Num):
                return left
            return None
        if isinstance(node, Compare):
            prev = self.unit(node.left)
            for op, rnode in zip(node.ops, node.rights):
                cur = self.unit(rnode)
                self._same(prev, cur, f"comparison '{op}'")
                prev = cur
            return None
        if isinstance(node, Bool):
            for value in node.values:
                self.unit(value)
            return None
        if isinstance(node, Cond):
            self.unit(node.test)
            return self._same(self.unit(node.then), self.unit(node.other), "'? :'")
        return self._call(node)

    def _call(self, node: Call) -> str | None:
        units = [self.unit(a) for a in node.args]
        first = units[0] if units else None
        if node.func in _SAME_AS_FIRST:
            return first
        if node.func in _ALL_ALIKE:
            out = first
            for other in units[1:]:
                out = self._same(out, other, f"{node.func}()")
            return None if node.func == "between" else out
        if node.func in ("slope", "rate") and first is not None:
            return self._known(f"{self._show(first)}/s")
        if node.func == "integ" and first is not None:
            return self._known(f"{self._show(first)}·s")
        return None


def check_units(node: Node, units: Mapping[str, str],
                declared: str | None = None) -> list[str]:
    """Warnings about mixed units in an expression (an empty list is good).

    ``units`` maps names to unit text; ``declared`` is the unit the result is
    supposed to have, checked when the expression's own unit can be worked out.
    """
    checker = _Checker(units)
    result = checker.unit(node)
    want = checker._known(declared)
    if result is not None and want is not None and result != want:
        checker.warnings.append(
            f"result is in {checker._show(result)} but declared as {checker._show(want)}")
    return checker.warnings
