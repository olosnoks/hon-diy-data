"""Tokenizer and recursive-descent parser. Text in, AST out, nothing executed.

Grammar (lowest precedence first)::

    expr     := ifelse [ '?' expr ':' expr ]           a ? b : c
    ifelse   := or [ 'if' or 'else' ifelse ]           b if a else c
    or       := and ( ('||' | 'or') and )*
    and      := not ( ('&&' | 'and') not )*
    not      := 'not' not | compare
    compare  := bitor ( ('<'|'<='|'>'|'>='|'=='|'!=') bitor )*
    bitor    := bitxor ( '|' bitxor )*
    bitxor   := bitand ( '^' bitand )*
    bitand   := shift ( '&' shift )*
    shift    := sum ( ('<<' | '>>') sum )*
    sum      := term ( ('+' | '-') term )*
    term     := unary ( ('*' | '/' | '//' | '%') unary )*
    unary    := ('-' | '+' | '~' | '!') unary | power
    power    := atom [ '**' unary ]
    atom     := NUMBER | NAME | NAME '(' args ')' | NAME '[' expr ']'
              | '[' NAME [ '-' INT ] ']' | '(' expr ')'

``[rpm]`` and a bare ``rpm`` mean the same thing; ``[rpm-1]`` is the value one
sample ago (MegaLogViewer style). ``^`` is XOR as in Python, power is ``**``.
``!`` binds tightly as in C (``!a == b`` is ``(!a) == b``); ``not`` binds
loosely as in Python (``not a == b`` is ``not (a == b)``).
"""

from __future__ import annotations

import re
from typing import NamedTuple

from .nodes import (Binary, Bool, Call, Compare, Cond, ExprError, Index, Node, Num,
                    Unary, Var, children)

MAX_LENGTH = 2000        # characters; a formula longer than this is a mistake
MAX_DEPTH = 32           # bracket nesting; keeps a hostile map file off the stack limit
MAX_TREE_DEPTH = 200     # operator chains: 1 + 1 + 1 + ... builds a deep tree too

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>0[xX][0-9a-fA-F]+ | 0[bB][01]+
         | (?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\*\*|//|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^~<>!?:(),\[\]])
""", re.VERBOSE)

_KEYWORDS = {"and", "or", "not", "if", "else"}
_NAMED_NUMBERS = {"true": 1, "True": 1, "false": 0, "False": 0}


class Token(NamedTuple):
    kind: str            # 'num' | 'name' | 'op' | 'end'
    text: str
    pos: int


def tokenize(text: str) -> list[Token]:
    if len(text) > MAX_LENGTH:
        raise ExprError(f"expression is longer than {MAX_LENGTH} characters", text[:40] + "...")
    out: list[Token] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None:
            ch = text[pos]
            hint = ""
            if ch in "'\"":
                hint = " (text is not allowed, only numbers and names)"
            elif ch == ".":
                hint = " (attribute access is not allowed)"
            elif ch == "=":
                hint = " (use == to compare)"
            raise ExprError(f"unexpected character {ch!r}{hint}", text, pos)
        kind = m.lastgroup or ""
        if kind != "ws":
            out.append(Token(kind, m.group(), pos))
        pos = m.end()
    out.append(Token("end", "", len(text)))
    return out


def _number(tok: Token, text: str) -> Num:
    s = tok.text
    try:
        if s[:2] in ("0x", "0X"):
            return Num(int(s, 16), tok.pos)
        if s[:2] in ("0b", "0B"):
            return Num(int(s, 2), tok.pos)
        if re.fullmatch(r"\d+", s):
            return Num(int(s), tok.pos)
        return Num(float(s), tok.pos)
    except ValueError as exc:                               # pragma: no cover
        raise ExprError(f"bad number {s!r}", text, tok.pos) from exc


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.toks = tokenize(text)
        self.i = 0
        self.depth = 0

    # -- token helpers --

    @property
    def tok(self) -> Token:
        return self.toks[self.i]

    def _is(self, *ops: str) -> bool:
        t = self.tok
        return (t.kind == "op" and t.text in ops) or (t.kind == "name" and t.text in ops
                                                       and t.text in _KEYWORDS)

    def _take(self) -> Token:
        t = self.tok
        self.i += 1
        return t

    def _expect(self, op: str) -> Token:
        if not self._is(op):
            raise self._error(f"expected {op!r}")
        return self._take()

    def _error(self, what: str) -> ExprError:
        t = self.tok
        got = "the end" if t.kind == "end" else repr(t.text)
        return ExprError(f"{what}, got {got}", self.text, t.pos)

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExprError("expression is nested too deeply", self.text, self.tok.pos)

    # -- grammar --

    def parse(self) -> Node:
        if self.tok.kind == "end":
            raise ExprError("empty expression", self.text, 0)
        node = self.expr()
        if self.tok.kind != "end":
            raise self._error("expected an operator or the end")
        return node

    def expr(self) -> Node:
        self._enter()
        node = self.ifelse()
        if self._is("?"):
            pos = self._take().pos
            then = self.expr()
            self._expect(":")
            other = self.expr()
            node = Cond(node, then, other, pos)
        self.depth -= 1
        return node

    def ifelse(self) -> Node:
        node = self.or_()
        if self._is("if"):
            pos = self._take().pos
            test = self.or_()
            self._expect("else")
            other = self.ifelse()
            node = Cond(test, node, other, pos)
        return node

    def or_(self) -> Node:
        node = self.and_()
        if not self._is("||", "or"):
            return node
        pos, values = self.tok.pos, [node]
        while self._is("||", "or"):
            self._take()
            values.append(self.and_())
        return Bool("or", tuple(values), pos)

    def and_(self) -> Node:
        node = self.not_()
        if not self._is("&&", "and"):
            return node
        pos, values = self.tok.pos, [node]
        while self._is("&&", "and"):
            self._take()
            values.append(self.not_())
        return Bool("and", tuple(values), pos)

    def not_(self) -> Node:
        if self._is("not"):
            self._enter()
            pos = self._take().pos
            node: Node = Unary("not", self.not_(), pos)
            self.depth -= 1
            return node
        return self.compare()

    def compare(self) -> Node:
        node = self.binary(0)
        if not self._is("<", "<=", ">", ">=", "==", "!="):
            return node
        pos = self.tok.pos
        ops: list[str] = []
        rights: list[Node] = []
        while self._is("<", "<=", ">", ">=", "==", "!="):
            ops.append(self._take().text)
            rights.append(self.binary(0))
        return Compare(node, tuple(ops), tuple(rights), pos)

    _LEVELS: tuple[tuple[str, ...], ...] = (
        ("|",), ("^",), ("&",), ("<<", ">>"), ("+", "-"), ("*", "/", "//", "%"))

    def binary(self, level: int) -> Node:
        if level == len(self._LEVELS):
            return self.unary()
        ops = self._LEVELS[level]
        node = self.binary(level + 1)
        while self.tok.kind == "op" and self.tok.text in ops:
            tok = self._take()
            node = Binary(tok.text, node, self.binary(level + 1), tok.pos)
        return node

    def unary(self) -> Node:
        if self.tok.kind == "op" and self.tok.text in ("-", "+", "~", "!"):
            self._enter()
            tok = self._take()
            node: Node = Unary("not" if tok.text == "!" else tok.text, self.unary(), tok.pos)
            self.depth -= 1
            return node
        return self.power()

    def power(self) -> Node:
        node = self.atom()
        if self._is("**"):
            self._enter()
            pos = self._take().pos
            node = Binary("**", node, self.unary(), pos)       # right-assoc, binds past unary
            self.depth -= 1
        return node

    def atom(self) -> Node:
        tok = self.tok
        if tok.kind == "num":
            self._take()
            return _number(tok, self.text)
        if tok.kind == "name":
            if tok.text in _KEYWORDS:
                raise self._error("expected a value")
            self._take()
            if tok.text in _NAMED_NUMBERS:
                return Num(_NAMED_NUMBERS[tok.text], tok.pos)
            if self._is("("):
                return self.call(tok)
            if self._is("["):
                self._take()
                index = self.expr()
                self._expect("]")
                return Index(tok.text, index, tok.pos)
            return Var(tok.text, 0, tok.pos)
        if self._is("("):
            self._take()
            node = self.expr()
            self._expect(")")
            return node
        if self._is("["):
            return self.channel_ref()
        raise self._error("expected a value")

    def call(self, name: Token) -> Node:
        self._expect("(")
        args: list[Node] = []
        if not self._is(")"):
            args.append(self.expr())
            while self._is(","):
                self._take()
                args.append(self.expr())
        self._expect(")")
        return Call(name.text, tuple(args), name.pos)

    def channel_ref(self) -> Node:
        pos = self._expect("[").pos
        tok = self.tok
        if tok.kind != "name" or tok.text in _KEYWORDS:
            raise self._error("expected a channel name after '['")
        self._take()
        lag = 0
        if self._is("-"):
            self._take()
            num = self.tok
            if num.kind != "num" or not num.text.isdigit():
                raise self._error("expected a whole number of samples after '-'")
            self._take()
            lag = int(num.text)
            if lag > 10000:
                raise ExprError("cannot look back more than 10000 samples", self.text, num.pos)
        self._expect("]")
        return Var(tok.text, lag, pos)


def parse(text: str) -> Node:
    """Parse expression text into an AST. Raises ExprError."""
    if not isinstance(text, str):
        raise ExprError(f"expression must be text, not {type(text).__name__}")
    text = text.strip()
    try:
        node = _Parser(text).parse()
    except RecursionError:
        raise ExprError("expression is nested too deeply", text) from None
    if _depth(node) > MAX_TREE_DEPTH:
        raise ExprError("expression is too long a chain of operators", text)
    return node


def _depth(node: Node) -> int:
    """Height of the tree, without recursion."""
    best, stack = 0, [(node, 1)]
    while stack:
        cur, d = stack.pop()
        best = max(best, d)
        stack.extend((c, d + 1) for c in children(cur))
    return best
