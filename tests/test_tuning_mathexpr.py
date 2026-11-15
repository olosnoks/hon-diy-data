import numpy as np
import pytest

from hondalog.tuning.mathexpr import Conversion, Expression, MathError, raw_limits


@pytest.mark.parametrize("text,x,want", [
    ("X", 7, 7), ("x/2^14", 16384, 1), ("0.7812*x - 40", 100, 38.12),
    ("(x/128)*100", 64, 50), ("-X^2", 3, -9), ("0x10 + X", 1, 17),
    ("IF(X>5; X*2; 0)", 9, 18), ("IF(X>5, X*2, 0)", 1, 0),
    ("MAX(X; 10)", 3, 10), ("MIN(X; 10; 2)", 3, 2), ("ABS(X-10)", 4, 6),
    ("ROUND(X/2)", 5, 3), ("INT(X/2)", 5, 2), ("SQRT(X)", 16, 4),
    ("X % 3", 8, 2), ("AND(X>1; X<5)", 3, 1), ("NOT(X)", 3, 0), ("OR(X=3; 0)", 3, 1),
    ("X <> 3", 3, 0), ("(X>2) && (X<4)", 3, 1),
])
def test_expression_values(text, x, want):
    assert float(Expression(text)(x)) == pytest.approx(want)


def test_expression_is_vectorised_and_case_blind():
    out = Expression("x*2 + Row()*10 + COL()")(
        np.ones((2, 3)), row=np.array([[0], [1]]), col=np.arange(3))
    assert out.tolist() == [[2, 3, 4], [12, 13, 14]]
    assert Expression("3")(np.zeros((2, 2))).shape == (2, 2)


def test_extra_variables():
    e = Expression("X*K + offset")
    assert e.extra_variables == {"K", "OFFSET"}
    assert float(e(2, k=3, OFFSET=1)) == 7
    with pytest.raises(MathError, match="no value for"):
        e(2, k=3)


@pytest.mark.parametrize("bad", [
    '__import__("os")', "x.y", "CELL(1;2;0)", "THIS()", "x +", "foo(x)", "x[0]",
    "lambda: 1", "!x", "x if x else 1", "'a'", "",
][:-1])
def test_bad_equations_are_refused(bad):
    with pytest.raises(MathError):
        Expression(bad)


def test_empty_equation_is_identity():
    assert Expression("").is_identity and Expression(" x ").is_identity


def test_runtime_failure_is_a_matherror():
    with pytest.raises(MathError):
        Expression("POW(X)")(2)


def test_raw_limits():
    assert raw_limits(8, False, False) == (0, 255)
    assert raw_limits(16, True, False) == (-32768, 32767)
    assert raw_limits(32, False, True)[1] == np.inf


@pytest.mark.parametrize("equation,bits,signed", [
    ("X", 8, False), ("0.7812*X-40", 8, False), ("X/10", 16, True),
    ("X*0.25-6", 8, True), ("1000/X", 8, False), ("X*X/100", 16, False),
    ("1875000/X", 16, False), ("X/2^14", 16, False),
])
def test_every_raw_value_survives_a_round_trip(equation, bits, signed):
    conv = Conversion(equation, bits=bits, signed=signed)
    raw = np.arange(conv.lo, conv.hi + 1, 1 if bits == 8 else 7)
    real = conv.to_real(raw)
    ok = np.isfinite(real)
    back, clipped = conv.to_raw(real[ok])
    assert np.array_equal(back, raw[ok]) and not clipped.any()


def test_to_raw_rounds_to_nearest_and_flags_clipping():
    conv = Conversion("X*0.5", bits=8)
    raw, clipped = conv.to_raw(np.array([10.2, 10.3, 500.0, -1.0]))
    assert raw.tolist() == [20, 21, 255, 0]
    assert clipped.tolist() == [False, False, True, True]


def test_position_dependent_inverse():
    conv = Conversion("X*(COL()+1)", bits=8)
    raw, _ = conv.to_raw(np.array([[10.0, 10.0, 10.0]]), row=0, col=np.arange(3))
    assert raw.tolist() == [[10, 5, 3]]
    conv = Conversion("X*X + ROW()", bits=8)
    raw, _ = conv.to_raw(np.array([[16.0], [17.0]]), row=np.array([[0], [1]]), col=0)
    assert raw.tolist() == [[4], [4]]


def test_inverse_failures():
    with pytest.raises(MathError, match="does not depend"):
        Conversion("5", bits=8).to_raw(np.array([1.0]))
    with pytest.raises(MathError, match="cannot be inverted"):
        Conversion("X*X", bits=32).to_raw(np.array([4.0]))
    with pytest.raises(MathError, match="finite"):
        Conversion("X", bits=8).to_raw(np.array([np.nan]))


def test_float_and_variable_conversion():
    conv = Conversion("X*K", bits=32, is_float=True, variables={"k": 2})
    raw, clipped = conv.to_raw(np.array([3.0]))
    assert raw[0] == pytest.approx(1.5) and not clipped.any()
    assert conv.equation == "X*K"
