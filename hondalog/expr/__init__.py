"""The one safe expression evaluator: formulas, math channels, alarm, filter
and trigger conditions all go through here.

    from hondalog.expr import compile_expr

    duty = compile_expr("[inj] * [rpm] / 1200", names={"inj", "rpm"})
    duty.eval({"inj": 2.4, "rpm": 3000}, t=12.5)      # -> 6.0
    duty.eval({"inj": None, "rpm": 3000}, t=12.6)     # -> None (invalid)
    duty.eval_array({"inj": inj_col, "rpm": rpm_col}, t_col)   # numpy, NaN = invalid

Text is tokenized and parsed by hand into an AST of nine node types, then
compiled once into closures. There is no ``eval``/``exec`` anywhere, only the
functions in ``funcs`` can be called, and attribute access, strings, lambdas
and comprehensions do not exist in the grammar.
"""

from .compiler import EvalError, Expr, compile_expr, references
from .funcs import function_help
from .nodes import ExprError
from .parser import parse
from .unitcheck import check_units

__all__ = ["EvalError", "Expr", "ExprError", "check_units", "compile_expr",
           "function_help", "parse", "references"]
