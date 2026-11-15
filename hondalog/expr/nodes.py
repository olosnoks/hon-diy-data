"""AST node types for the expression language. Plain data, no behaviour."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Union


class ExprError(ValueError):
    """A problem in an expression's text: syntax, unknown name, bad call."""

    def __init__(self, message: str, text: str = "", pos: int | None = None):
        self.message = message
        self.text = text
        self.pos = pos
        super().__init__(self._render())

    def _render(self) -> str:
        if not self.text:
            return self.message
        if self.pos is None:
            return f"{self.message} in {self.text!r}"
        return f"{self.message} at column {self.pos + 1} of {self.text!r}"


@dataclass(frozen=True, slots=True)
class Num:
    value: float | int
    pos: int = 0


@dataclass(frozen=True, slots=True)
class Var:
    """A channel, constant or formula variable. ``lag`` > 0 is ``[name-N]``."""
    name: str
    lag: int = 0
    pos: int = 0


@dataclass(frozen=True, slots=True)
class Index:
    """``b[0]``: one element of an array variable (formulas only)."""
    name: str
    index: "Node"
    pos: int = 0


@dataclass(frozen=True, slots=True)
class Unary:
    op: str                  # '-', '+', '~', 'not'
    operand: "Node"
    pos: int = 0


@dataclass(frozen=True, slots=True)
class Binary:
    op: str                  # + - * / // % ** & | ^ << >>
    left: "Node"
    right: "Node"
    pos: int = 0


@dataclass(frozen=True, slots=True)
class Compare:
    """``a < b <= c`` chains like Python: every link must hold."""
    left: "Node"
    ops: tuple[str, ...]
    rights: tuple["Node", ...]
    pos: int = 0


@dataclass(frozen=True, slots=True)
class Bool:
    op: str                  # 'and' | 'or'; the result is always 0 or 1
    values: tuple["Node", ...]
    pos: int = 0


@dataclass(frozen=True, slots=True)
class Cond:
    test: "Node"
    then: "Node"
    other: "Node"
    pos: int = 0


@dataclass(frozen=True, slots=True)
class Call:
    func: str
    args: tuple["Node", ...]
    pos: int = 0


Node = Union[Num, Var, Index, Unary, Binary, Compare, Bool, Cond, Call]


def children(node: Node) -> tuple[Node, ...]:
    if isinstance(node, (Num, Var)):
        return ()
    if isinstance(node, Index):
        return (node.index,)
    if isinstance(node, Unary):
        return (node.operand,)
    if isinstance(node, Binary):
        return (node.left, node.right)
    if isinstance(node, Compare):
        return (node.left, *node.rights)
    if isinstance(node, Bool):
        return node.values
    if isinstance(node, Cond):
        return (node.test, node.then, node.other)
    return node.args


def walk(node: Node) -> Iterator[Node]:
    """Every node of the tree, parents before children."""
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(reversed(children(cur)))
