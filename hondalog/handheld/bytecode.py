"""Compile a ``hondalog.expr`` syntax tree into the handheld's postfix byte
code (firmware: hh_expr.c).

The handheld has no parser. The tree that the laptop evaluates is walked
here and written out, so there is one grammar. Only what can be evaluated
from the present sample is accepted: a function with history (``avg``,
``slope``, ``[rpm-1]`` ...) raises ``Unsupported`` and the caller leaves that
math channel or alarm out.
"""

from __future__ import annotations

from typing import Collection, Mapping, Sequence

from ..expr import parse
from ..expr import funcs
from ..expr.nodes import Binary, Bool, Call, Compare, Cond, Index, Node, Num, Unary, Var
from . import format as F


class Unsupported(ValueError):
    """The expression needs something the handheld's machine does not have."""


class _Absent(Unsupported):
    """A name the handheld never has a value for (an OBD2-only channel)."""


class Pool:
    """Constants shared by every program of a map file."""

    def __init__(self) -> None:
        self.values: list[float] = []
        self._index: dict[bytes, int] = {}

    def add(self, value: float) -> int:
        import struct
        key = struct.pack("<d", float(value))
        if key not in self._index:
            if len(self.values) >= F.MAX_CONST:
                raise Unsupported(f"more than {F.MAX_CONST} different constants")
            self._index[key] = len(self.values)
            self.values.append(float(value))
        return self._index[key]


class Compiler:
    """names: channel name -> index (``[rpm]``); constants: folded in;
    formula=True allows ``x`` and ``b[i]`` of a ``raw_len``-byte channel."""

    def __init__(self, pool: Pool, names: Mapping[str, int] | None = None,
                 constants: Mapping[str, float] | None = None, formula: bool = False, raw_len: int = 0,
                 absent: Collection[str] = ()):
        self.pool = pool
        #: names that never have a value here: a coalesce() argument using one is left out
        self.absent = frozenset(absent)
        self.names = dict(names or {})
        self.constants = dict(constants or {})
        self.formula = formula
        self.raw_len = raw_len
        self.deps: set[str] = set()

    def compile(self, text: str) -> bytes:
        out = bytearray()
        depth = self._emit(parse(text), out, 0)
        assert depth == 1
        out.append(F.OP_END)
        return bytes(out)

    # every _emit returns the stack depth after the node; ``depth`` is the depth before it
    def _push(self, depth: int) -> int:
        if depth + 1 > F.EXPR_STACK:
            raise Unsupported(f"expression needs more than {F.EXPR_STACK} stack slots")
        return depth + 1

    def _const(self, value: float, out: bytearray, depth: int) -> int:
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, int) and 0 <= value <= 255:
            out += bytes([F.OP_IMM, value])
        else:
            index = self.pool.add(float(value))
            out += bytes([F.OP_CONST, index & 0xFF, index >> 8])
        return self._push(depth)

    def _emit(self, node: Node, out: bytearray, depth: int) -> int:
        if isinstance(node, Num):
            return self._const(node.value, out, depth)
        if isinstance(node, Var):
            return self._var(node, out, depth)
        if isinstance(node, Index):
            return self._index(node, out, depth)
        if isinstance(node, Unary):
            depth = self._emit(node.operand, out, depth)
            if node.op != "+":
                out.append({"-": F.OP_NEG, "~": F.OP_BITNOT}.get(node.op, F.OP_NOT))
            return depth
        if isinstance(node, Binary):
            depth = self._emit(node.left, out, depth)
            depth = self._emit(node.right, out, depth)
            out.append(F.OP_BINARY[node.op])
            return depth - 1
        if isinstance(node, Compare):
            # a < b <= c  is  (a < b) and (b <= c); the middle terms are pure, so twice is fine
            left = node.left
            for i, (op, right) in enumerate(zip(node.ops, node.rights)):
                d = self._emit(left, out, depth)
                d = self._emit(right, out, d)
                out.append(F.OP_COMPARE[op])
                depth = d - 1
                if i:
                    out.append(F.OP_AND)
                    depth -= 1
                left = right
            return depth
        if isinstance(node, Bool):
            depth = self._emit(node.values[0], out, depth)
            for value in node.values[1:]:
                depth = self._emit(value, out, depth)
                out.append(F.OP_AND if node.op == "and" else F.OP_OR)
                depth -= 1
            if len(node.values) == 1:           # cannot happen from the parser
                raise Unsupported("empty boolean")
            return depth
        if isinstance(node, Cond):
            depth = self._emit(node.test, out, depth)
            depth = self._emit(node.then, out, depth)
            depth = self._emit(node.other, out, depth)
            out.append(F.OP_SELECT)
            return depth - 2
        return self._call(node, out, depth)

    def _var(self, node: Var, out: bytearray, depth: int) -> int:
        name = node.name
        if node.lag:
            raise Unsupported(f"[{name}-{node.lag}] needs history")
        if self.formula and name == "x":
            out.append(F.OP_X)
            return self._push(depth)
        if self.formula and name == "b":
            raise Unsupported("'b' is a list of bytes: write b[0], b[1] ...")
        if name in self.names:
            self.deps.add(name)
            out += bytes([F.OP_CH, self.names[name]])
            return self._push(depth)
        if name in self.constants:
            return self._const(self.constants[name], out, depth)
        if name in self.absent:
            raise _Absent(f"{name!r} is OBD2-only: the handheld has no value for it")
        raise Unsupported(f"unknown name {name!r}")

    def _index(self, node: Index, out: bytearray, depth: int) -> int:
        if not (self.formula and node.name == "b"):
            raise Unsupported(f"{node.name!r} cannot be indexed")
        index = node.index
        negative = isinstance(index, Unary) and index.op == "-"
        if negative:
            index = index.operand                   # b[-1]: the last byte
        if not isinstance(index, Num):
            raise Unsupported("b[...] needs a constant index")
        i = -int(index.value) if negative else int(index.value)
        if i < 0:
            i += self.raw_len
        if not 0 <= i < 255:
            raise Unsupported("b[...] index out of range")
        out += bytes([F.OP_BYTE, i])
        return self._push(depth)

    def _call(self, node: Call, out: bytearray, depth: int) -> int:
        name = node.func
        args: Sequence[Node] = node.args
        if name == "time":
            out.append(F.OP_TIME)
            return self._push(depth)
        if name == "valid":
            depth = self._emit(args[0], out, depth)
            out.append(F.OP_VALID)
            return depth
        if name == "coalesce":
            kept = []
            for arg in args:                    # an argument that can never have a value adds nothing
                try:
                    self._emit(arg, bytearray(), depth)
                except _Absent:
                    continue
                kept.append(arg)
            if not kept:
                raise Unsupported("coalesce(): no argument has a value on the handheld")
            args = kept
            depth = self._emit(args[0], out, depth)
            for arg in args[1:]:
                depth = self._emit(arg, out, depth)
                out.append(F.OP_COALESCE)
                depth -= 1
            return depth
        if name in funcs.STATEFUL:
            raise Unsupported(f"{name}() needs history")
        if name not in F.FN:
            raise Unsupported(f"{name}() is not available on the handheld")
        spec = funcs.PURE[name]
        n = len(args)
        if n < spec.min_args or (spec.max_args is not None and n > spec.max_args) or n > 255:
            raise Unsupported(f"{name}() takes another number of arguments")
        start = depth
        for arg in args:
            depth = self._emit(arg, out, depth)
        out += bytes([F.OP_CALL, F.FN[name], n])
        return start + 1


def compile_formula(text: str, pool: Pool, constants: Mapping[str, float], raw_len: int) -> bytes:
    """A channel formula: ``x`` is the raw number, ``b[i]`` its bytes."""
    return Compiler(pool, constants=constants, formula=True, raw_len=raw_len).compile(text)


def compile_condition(text: str, pool: Pool, names: Mapping[str, int],
                      constants: Mapping[str, float],
                      absent: Collection[str] = ()) -> tuple[bytes, set[str]]:
    """A math channel or alarm condition over channel values. Returns the
    program and the channel names it reads. ``absent`` names channels the
    handheld never has (OBD2-only): ``coalesce()`` leaves them out."""
    comp = Compiler(pool, names=names, constants=constants, absent=absent)
    return comp.compile(text), comp.deps
