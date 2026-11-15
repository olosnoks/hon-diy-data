"""The safe expression evaluator: grammar, safety, state, numpy back end."""

import math
import random

import numpy as np
import pytest

from hondalog import expr as ex
from hondalog.expr import ExprError, compile_expr
from hondalog.expr import compiler, funcs, parser


def ev(text, t=0.0, **values):
    return compile_expr(text).eval(values, t)


# ---- grammar and arithmetic -------------------------------------------------

@pytest.mark.parametrize("text, want", [
    ("1 + 2 * 3", 7), ("(1 + 2) * 3", 9), ("7 // 2", 3), ("7 % 3", 1), ("-7 % 3", 2),
    ("2 ** 3 ** 2", 512), ("-2 ** 2", -4), ("2 ** -1", 0.5), ("10 / 4", 2.5),
    ("0x10 + 0b101 + 1e1 + .5", 31.5), ("1 << 4 | 1", 17), ("6 & 3 ^ 1", 3), ("~5", -6),
    ("255 >> 4", 15), ("3 > 2", 1), ("3 > 2 > 1", 1), ("3 > 2 > 5", 0), ("1 == 1.0", 1),
    ("2 != 2", 0), ("!0", 1), ("not 3", 0), ("1 && 0", 0), ("1 and 2", 1), ("0 || 0", 0),
    ("0 or 7", 1), ("1 ? 2 : 3", 2), ("0 ? 2 : 3", 3), ("2 if 1 else 3", 2),
    ("0 ? 1 : 0 ? 2 : 3", 3), ("1 if 0 else 2 if 0 else 3", 3), ("true + false", 1),
    ("abs(-3)", 3), ("min(3, 1, 2)", 1), ("max(3, 1, 2)", 3), ("round(2.567, 1)", 2.6),
    ("round(2.5)", 2), ("int(-2.7)", -2), ("floor(-2.5)", -3), ("ceil(2.1)", 3),
    ("sqrt(16)", 4), ("pow(2, 10)", 1024), ("log10(1000)", 3), ("exp(0)", 1), ("ln(1)", 0),
    ("sign(-4)", -1), ("clamp(12, 0, 10)", 10), ("between(5, 1, 9)", 1),
    ("bit(0b1000, 3)", 1), ("signed(0xFF)", -1), ("signed(0x8000, 16)", -32768),
    ("poly(2, 1, 2, 3)", 17), ("table(5, 0, 0, 10, 100)", 50), ("table(-1, 0, 3, 10, 100)", 3),
    ("table(99, 0, 3, 10, 100)", 100),
])
def test_constants_evaluate(text, want):
    e = compile_expr(text)
    assert e.eval({}) == pytest.approx(want)
    assert e.constant == pytest.approx(want)        # folded at compile time
    assert e.eval_array({}, np.zeros(3)) == pytest.approx([want] * 3)


def test_valid_and_coalesce():
    for text, want in [("coalesce(1/0, 4)", 4), ("valid(1/0)", 0), ("valid(3)", 1),
                       ("coalesce(x, 1/0, 9)", 9)]:
        e = compile_expr(text)
        assert e.eval({"x": None}) == want
        assert e.eval_array({"x": [np.nan]}, [0.0]) == pytest.approx([want])
    assert compile_expr("coalesce(x, x)").eval({"x": None}) is None


def test_channel_references_and_names():
    assert ev("[rpm] / 4", rpm=3000) == 750
    assert ev("rpm / 4", rpm=3000) == 750
    e = compile_expr("[inj] * [rpm] / 1200 + k", constants={"k": 1})
    assert e.deps == {"inj", "rpm"}
    assert e.eval({"inj": 2.4, "rpm": 3000}) == pytest.approx(7.0)
    assert ex.references("[a] + b[0] + avg(c, 5)") == {"a", "b", "c"}


def test_variable_shadows_constant_of_same_name():
    e = compile_expr("k * 2", names={"k"}, constants={"k": 100})
    assert e.eval({"k": 3}) == 6
    assert e.eval_array({"k": [3.0]}, [0.0]) == pytest.approx([6])


def test_unknown_names_are_rejected_with_a_hint():
    with pytest.raises(ExprError, match="unknown name 'rmp'.*did you mean rpm"):
        compile_expr("[rmp] + 1", names={"rpm", "map"})
    with pytest.raises(ExprError, match="unknown function 'averge'.*avg"):
        compile_expr("averge(rpm, 5)", names={"rpm"})
    with pytest.raises(ExprError, match="constant"):
        compile_expr("[k-1]", names=set(), constants={"k": 1})


def test_any_name_allowed_when_names_is_none():
    assert ev("anything + 1", anything=2) == 3
    assert ev("anything + 1") is None               # looked up at run time


@pytest.mark.parametrize("bad, why", [
    ("", "empty"), ("1 +", "expected a value"), ("(1", r"expected '\)'"), ("1 1", "operator"),
    ("a = 1", "=="), ("'x'", "text is not allowed"), ("a.b", "attribute"), ("a @ b", "unexpected"),
    ("avg(x)", "takes 2"), ("avg(x, y)", "must be a constant"), ("avg(x, 0)", "more than 0"),
    ("smooth(x, 0)", "1 to"), ("min(1)", "2 or more"), ("round(1, x)", "constant"),
    ("table(x, 0, 1, 2, 3, 4)", "pairs"), ("table(x, 5, 1, 2, 3)", "go up"), ("[x-]", "whole number"),
    ("[x-1.5]", "whole number"), ("[3]", "channel name"), ("x[0]", "cannot be indexed"),
    ("if", "expected a value"), ("time(1)", "takes 0"), ("[x-99999]", "10000"),
    ("x ? 1", "expected ':'"), ("1 if x", "expected 'else'"),
])
def test_syntax_errors_name_the_problem(bad, why):
    with pytest.raises(ExprError, match=why):
        compile_expr(bad)


def test_error_carries_position():
    with pytest.raises(ExprError) as info:
        compile_expr("1 + $")
    assert info.value.pos == 4 and "column 5" in str(info.value)


# ---- safety -----------------------------------------------------------------

@pytest.mark.parametrize("attack", [
    "__import__('os').system('true')", "().__class__.__bases__[0]", "open('/etc/passwd')",
    "eval('1')", "exec('x=1')", "lambda: 1", "[c for c in x]", "x.__class__", "globals()",
    "getattr(x, 'real')", "print(1)", "compile('1','','eval')", "{1: 2}", "x; y", "x\ny",
    "f'{x}'", "b'\\x00'", "1 if x else __builtins__", "type(x)", "breakpoint()",
    "x := 5", "*x", "**x", "x[::1]", "yield", "await x", "import os",
])
def test_code_injection_is_a_syntax_error(attack):
    with pytest.raises(ExprError):
        compile_expr(attack, names={"x", "y"})


def test_only_whitelisted_functions_exist():
    for name in ("eval", "exec", "open", "getattr", "type", "len", "str", "map", "print"):
        assert name not in funcs.PURE and name not in funcs.STATEFUL
        with pytest.raises(ExprError):
            compile_expr(f"{name}(1)")


def test_no_eval_or_exec_in_the_evaluator_source():
    import ast
    import inspect
    from hondalog import addressmap
    from hondalog.alarms import engine as alarm_engine
    from hondalog.mathch import engine as math_engine
    banned = {"eval", "exec", "compile", "__import__"}
    for mod in (compiler, parser, funcs, ex.unitcheck, ex.vector, ex.nodes, addressmap,
                math_engine, alarm_engine):
        for node in ast.walk(ast.parse(inspect.getsource(mod))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in banned, (mod.__name__, node.func.id, node.lineno)


def test_resource_limits():
    with pytest.raises(ExprError, match="longer"):
        compile_expr("1+" * 2000 + "1")
    with pytest.raises(ExprError, match="nested too deeply"):
        compile_expr("(" * 200 + "1" + ")" * 200)
    with pytest.raises(ExprError, match="nested too deeply|chain"):
        compile_expr("-" * 500 + "1")
    with pytest.raises(ExprError, match="nested too deeply|chain"):
        compile_expr("2**" * 300 + "2")
    with pytest.raises(ExprError, match="chain"):
        compile_expr("+".join(["x"] * 400))
    assert compile_expr("+".join(["1"] * 150)).eval({}) == 150
    # numbers cannot be made to eat memory or time
    assert compile_expr("1 << 4000").eval({}) is None
    assert compile_expr("9 ** 9 ** 9").eval({}) is None
    assert compile_expr("1e308 * 10").eval({}) is None


# ---- invalid values never raise ---------------------------------------------

@pytest.mark.parametrize("text", [
    "1 / x", "x / 0", "7 % 0", "7 // 0", "sqrt(-1)", "ln(0)", "(-8) ** 0.5", "0 ** -1",
    "exp(9999)", "y + 1", "x > y", "y && x", "!y", "y ? 1 : 2", "bit(1, 99)", "1 << -1",
    "min(x, y)", "thermistor(0, 1, 1, 1)", "int(1e400)", "missing * 2",
])
def test_bad_maths_and_missing_inputs_give_none(text):
    e = compile_expr(text)
    assert e.eval({"x": 0, "y": None}) is None
    assert e.truth({"x": 0, "y": None}) is None
    with pytest.raises(ex.EvalError):
        e.eval_strict({"x": 0, "y": None})


def test_nan_input_counts_as_missing():
    assert ev("x > 1", x=float("nan")) is None
    assert ev("x + 1", x=float("nan")) is None


def test_short_circuit_skips_invalid_branches():
    assert ev("x > 0 && y > 0", x=0, y=None) == 0
    assert ev("x > 0 || y > 0", x=1, y=None) == 1
    assert ev("x > 0 ? y : 5", x=0, y=None) == 5
    assert ev("coalesce(y, x)", x=7, y=None) == 7
    assert ev("valid(y) ? y : -1", y=None) == -1


def test_truth():
    e = compile_expr("[ect] > 105 && [rpm] > 400")
    assert e.truth({"ect": 110, "rpm": 800}) is True
    assert e.truth({"ect": 90, "rpm": 800}) is False
    assert e.truth({"ect": None, "rpm": 800}) is None


# ---- stateful helpers -------------------------------------------------------

def feed(text, xs, dt=0.1):
    e = compile_expr(text)
    return [e.eval({"x": x}, i * dt) for i, x in enumerate(xs)]


def test_previous_sample():
    assert feed("[x-1]", [1, 2, 3]) == [None, 1, 2]
    assert feed("[x-2]", [1, 2, 3, 4]) == [None, None, 1, 2]
    assert feed("prev(x)", [1, 2, 3]) == [None, 1, 2]
    assert feed("prev(x, 2)", [1, 2, 3]) == [None, None, 1]
    assert feed("x - [x-1]", [1, 4, 9]) == [None, 3, 5]
    assert feed("delta(x)", [1, 4, 9]) == [None, 3, 5]


def test_rate_and_slope_are_per_second():
    assert feed("rate(x)", [0, 10, 30]) == pytest.approx([None, 100, 200])
    out = feed("slope(x, 1000)", [5 * i for i in range(20)])
    assert out[0] is None and out[1:] == pytest.approx([50.0] * 19)
    # least squares, so one noisy sample does not swing it like rate() does
    noisy = feed("slope(x, 1000)", [0, 1, 2, 3, 40, 5, 6, 7, 8, 9, 10])
    assert abs(noisy[-1] - 10) < abs(feed("rate(x)", [0, 1, 2, 3, 40, 5])[-1] - 10)


def test_time_windows():
    xs = [1, 2, 3, 4, 5, 6]
    assert feed("avg(x, 250)", xs) == pytest.approx([1, 1.5, 2, 3, 4, 5])
    assert feed("minw(x, 250)", [5, 3, 4, 6, 7, 8]) == [5, 3, 3, 3, 4, 6]
    assert feed("maxw(x, 250)", [5, 8, 4, 3, 2, 1]) == [5, 8, 8, 8, 4, 3]
    assert feed("stdev(x, 150)", [1, 3, 3, 7]) == pytest.approx([0, 1, 0, 2])
    assert feed("smooth(x, 3)", xs) == pytest.approx([1, 1.5, 2, 3, 4, 5])
    assert feed("median(x, 3)", [1, 100, 2, 3, 3]) == [1, 50.5, 2, 3, 3]
    assert feed("integ(x)", [2, 2, 4]) == pytest.approx([0, 0.2, 0.5])


def test_ema_settles_on_a_step():
    out = feed("ema(x, 200)", [0] + [10] * 30)
    assert out[1] == pytest.approx(10 * (1 - math.exp(-0.5)))
    assert out[-1] == pytest.approx(10, abs=1e-3)
    assert all(b >= a for a, b in zip(out, out[1:]))


def test_held_needs_an_unbroken_run():
    out = feed("held(x > 5, 200)", [9, 9, 9, 0, 9, 9, 9])
    assert out == [0, 0, 1, 0, 0, 0, 1]


def test_windows_skip_invalid_samples_and_advance_in_untaken_branches():
    assert feed("avg(x, 1000)", [2, None, 4]) == [2, None, 3]
    e = compile_expr("go ? avg(x, 1000) : -1")
    seq = [(0, 10), (0, 20), (1, 30)]
    out = [e.eval({"go": g, "x": x}, i * 0.1) for i, (g, x) in enumerate(seq)]
    assert out == [-1, -1, 20]                      # the window saw 10 and 20 too


def test_reset_and_time_going_backwards():
    e = compile_expr("avg(x, 1000) + [x-1]")
    assert e.eval({"x": 4}, 0.0) is None
    assert e.eval({"x": 6}, 0.1) == 9
    e.reset()
    assert e.eval({"x": 100}, 0.2) is None
    assert e.eval({"x": 100}, 0.3) == 200
    w = compile_expr("maxw(x, 1000) + minw(x, 1000) + avg(x, 1000)")
    w.eval({"x": 50}, 10.0)
    assert w.eval({"x": 1}, 1.0) == 3               # replay jumped back: old data dropped


def test_long_runs_do_not_drift():
    e = compile_expr("avg(x, 300) + slope(x, 300) * 0 + smooth(x, 4) * 0 + stdev(x, 300) * 0")
    rnd = random.Random(3)
    last = []
    for i in range(20000):
        x = 1e6 + rnd.random()
        last = (last + [x])[-4:]
        got = e.eval({"x": x}, i * 0.1)
    assert got == pytest.approx(sum(last) / 4, rel=1e-12)
    s = compile_expr("slope(x, 500)")
    for i in range(20000):
        got = s.eval({"x": 3.0 * (i * 0.05) + 7}, i * 0.05)
    assert got == pytest.approx(3.0, rel=1e-6)


def test_flags_and_time():
    e = compile_expr("time() > 1 ? 1 : 0")
    assert e.uses_time and not e.stateful and e.eval({}, 0.5) == 0 and e.eval({}, 2) == 1
    assert compile_expr("avg(x, 5)").stateful and compile_expr("[x-1]").stateful
    assert not compile_expr("x + 1").stateful
    assert compile_expr("avg(x, 5) + bit(x, 1)").functions == {"avg", "bit"}
    assert any("avg(x, ms)" in line for line in ex.function_help())


# ---- numpy back end ---------------------------------------------------------

def test_eval_array_basics():
    t = np.arange(4) * 0.1
    cols = {"rpm": np.array([800.0, 3000, np.nan, 6000]), "inj": np.array([2.0, 4, 6, 0])}
    out = compile_expr("[inj] * [rpm] / 1200").eval_array(cols, t)
    assert out[:2] == pytest.approx([800 * 2 / 1200, 10]) and np.isnan(out[2]) and out[3] == 0
    assert np.isnan(compile_expr("1 / [inj]").eval_array(cols, t)[3])
    flags = compile_expr("[rpm] > 2000 && [inj] > 3").eval_array(cols, t)
    assert flags[0] == 0 and flags[1] == 1 and np.isnan(flags[2]) and flags[3] == 0
    assert compile_expr("[rpm] > 0").eval_array(cols).shape == (4,)      # t optional when pure
    with pytest.raises(ExprError, match="time column"):
        compile_expr("avg([rpm], 100)").eval_array(cols)
    with pytest.raises(ExprError, match="no column"):
        compile_expr("[nope] + 1").eval_array(cols, t)
    with pytest.raises(ExprError, match="rows"):
        compile_expr("[rpm] + 1").eval_array({"rpm": [1.0, 2.0]}, t)
    with pytest.raises(ExprError, match="formulas"):
        compile_expr("b[0]", arrays={"b"}).eval_array({}, t)


def test_eval_array_does_not_touch_live_state():
    e = compile_expr("avg(x, 1000)")
    assert e.eval({"x": 10}, 0.0) == 10
    e.eval_array({"x": np.array([1000.0, 2000.0])}, np.array([0.0, 0.1]))
    assert e.eval({"x": 20}, 0.1) == 15


_LEAVES = ["a", "b", "c", "[a-1]", "2", "0.5", "3", "0", "-1", "100"]
_BIN = ["+", "-", "*", "/", "//", "%", "<", "<=", ">", ">=", "==", "!=", "&&", "||", "&", "|",
        "^", "<<", ">>", "**"]
_F1 = ["abs", "int", "floor", "ceil", "sqrt", "sign", "delta", "rate", "integ", "valid", "!", "-",
       "~", "round", "signed", "ln", "exp"]
_F2 = ["avg(%s, 250)", "slope(%s, 350)", "minw(%s, 250)", "maxw(%s, 250)", "smooth(%s, 3)",
       "median(%s, 3)", "ema(%s, 200)", "stdev(%s, 250)", "held(%s, 200)", "prev(%s, 2)",
       "min(%s, 2)", "max(%s, a)", "clamp(%s, 0, 5)", "bit(%s, 1)", "poly(%s, 1, 2, 3)",
       "table(%s, 0, 0, 5, 10, 9, 2)", "coalesce(%s, b)", "between(%s, 0, c)", "pow(%s, 2)"]


def _random_expr(rnd, depth):
    if depth == 0 or rnd.random() < 0.15:
        return rnd.choice(_LEAVES)
    kind = rnd.random()
    sub = lambda: _random_expr(rnd, depth - 1)                       # noqa: E731
    if kind < 0.45:
        return f"({sub()} {rnd.choice(_BIN)} {sub()})"
    if kind < 0.6:
        f = rnd.choice(_F1)
        return f"{f}({sub()})"
    if kind < 0.8:
        return rnd.choice(_F2) % sub()
    if kind < 0.9:
        return f"({sub()} ? {sub()} : {sub()})"
    return f"({sub()} < {sub()} <= {sub()})"


def test_numpy_back_end_matches_the_closure_back_end():
    """600 random expressions over data with gaps: both back ends must agree."""
    rnd = random.Random(20261115)
    n = 40
    t = np.cumsum([rnd.uniform(0.03, 0.12) for _ in range(n)])
    cols = {}
    for name in "abc":
        vals = [rnd.choice([0, 1, 2, 3, -2, 0.5, 7, 250]) if rnd.random() < 0.7
                else rnd.uniform(-5, 9) for _ in range(n)]
        cols[name] = np.array([np.nan if rnd.random() < 0.12 else v for v in vals])
    rows = [{k: (None if np.isnan(v[i]) else float(v[i])) for k, v in cols.items()}
            for i in range(n)]
    checked = 0
    for _ in range(600):
        text = _random_expr(rnd, 3)
        e = compile_expr(text, names={"a", "b", "c"})
        scalar = [e.eval(row, float(ti)) for row, ti in zip(rows, t)]
        vector = compile_expr(text, names={"a", "b", "c"}).eval_array(cols, t)
        for i, (s, v) in enumerate(zip(scalar, vector)):
            if s is None:
                assert np.isnan(v), (text, i, s, v)
            else:
                assert v == pytest.approx(s, rel=1e-9, abs=1e-9), (text, i, s, v)
        checked += sum(s is not None for s in scalar)
    assert checked > 5000          # the comparison saw plenty of real numbers


# ---- units ------------------------------------------------------------------

UNITS = {"ect": "°C", "iat": "°C", "map": "kPa", "baro": "kPa", "batt": "V", "rpm": "rpm"}


@pytest.mark.parametrize("text, declared, problem", [
    ("[ect] - [iat]", "°C", None),
    ("[map] / [baro] * 100", "%", None),
    ("[ect] * 1.8 + 32", None, None),
    ("avg([map], 500) - [baro]", "kPa", None),
    ("[ect] + [map]", None, "mixes °C and kPa"),
    ("[ect] > [batt]", None, "mixes °C and V"),
    ("max([map], [batt])", None, "mixes kPa and V"),
    ("[rpm] > 1000 ? [ect] : [batt]", None, "mixes °C and V"),
    ("[ect] - [iat]", "kPa", "declared as kPa"),
    ("slope([rpm], 500)", "rpm/s", None),
    ("slope([rpm], 500)", "rpm", "declared as rpm"),
    ("minw([batt], 5000) < 12", None, None),
])
def test_unit_check(text, declared, problem):
    warnings = ex.check_units(ex.parse(text), UNITS, declared)
    if problem is None:
        assert warnings == []
    else:
        assert any(problem in w for w in warnings), warnings
