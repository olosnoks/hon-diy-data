"""Write the golden files the TypeScript expression, math, alarm and unit tests
compare against. Run from the worktree root, read-only on the Python side:

    PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 ../hon-diy-data/.venv/Scripts/python.exe \
        web/src/core/expr/__golden__/gen_golden.py

Everything goes to JSON next to this file. Floats are written with repr, so
they read back bit for bit; None (no value) is null.
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

import numpy as np

from hondalog import addressmap as am
from hondalog import units as hu
from hondalog.alarms import AlarmEngine, AlarmRule
from hondalog.alarms.engine import _fill
from hondalog.expr import ExprError, compile_expr, check_units, parse
from hondalog.expr import funcs
from hondalog.mathch import MathChannel, MathEngine

OUT = Path(__file__).resolve().parent


def num(x):
    if x is None:
        return None
    x = float(x)
    if x != x or math.isinf(x):
        return None
    return x


def dump(name, obj):
    path = OUT / name
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8")
    print(f"wrote {path} ({path.stat().st_size} bytes)")


# ---- 1. every channel formula of the shipped map, all variants ---------------

def formulas():
    seen = {}
    base = am.load_default()
    maps = [base] + [am.load(am.DEFAULT_MAP, v) for v in sorted(base.variants) if v != base.variant]
    for amap in maps:
        for ch in amap.channels:
            key = (ch.formula, ch.length, ch.endian)
            if key not in seen:
                seen[key] = ch
    rnd = random.Random(20261127)
    out = []
    for (formula, length, endian), ch in seen.items():
        if length == 1:
            raws = [bytes([i]) for i in range(256)]
        else:
            special = [0, 1, 0x7F, 0x80, 0xFF, 0x100, 0x7FFF, 0x8000, 0xFFFF,
                       (1 << (8 * length)) - 1, 1 << (8 * length - 1)]
            vals = sorted({v for v in special if v < (1 << (8 * length))}
                          | {rnd.randrange(1 << (8 * length)) for _ in range(400)})
            raws = [v.to_bytes(length, "big") for v in vals]
        cases = [[list(r), num(ch.convert(r))] for r in raws]
        out.append({"name": ch.name, "formula": formula, "length": length, "endian": endian,
                    "cases": cases})
    return out


# ---- 2. random expressions through the closure evaluator --------------------

_LEAVES = ["a", "b", "c", "[a-1]", "2", "0.5", "3", "0", "-1", "100"]
_BIN = ["+", "-", "*", "/", "//", "%", "<", "<=", ">", ">=", "==", "!=", "&&", "||", "&", "|",
        "^", "<<", ">>", "**"]
_F1 = ["abs", "int", "floor", "ceil", "sqrt", "sign", "delta", "rate", "integ", "valid", "!", "-",
       "~", "round", "signed", "ln", "exp", "log10", "sin", "cos", "float"]
_F2 = ["avg(%s, 250)", "slope(%s, 350)", "minw(%s, 250)", "maxw(%s, 250)", "smooth(%s, 3)",
       "median(%s, 3)", "ema(%s, 200)", "stdev(%s, 250)", "held(%s, 200)", "prev(%s, 2)",
       "min(%s, 2)", "max(%s, a)", "clamp(%s, 0, 5)", "bit(%s, 1)", "poly(%s, 1, 2, 3)",
       "table(%s, 0, 0, 5, 10, 9, 2)", "coalesce(%s, b)", "between(%s, 0, c)", "pow(%s, 2)",
       "round(%s, 2)", "signed(%s, 4)", "thermistor(%s, 0.001, 0.0002, 0.0000001)"]


def _random_expr(rnd, depth):
    if depth == 0 or rnd.random() < 0.15:
        return rnd.choice(_LEAVES)
    kind = rnd.random()
    sub = lambda: _random_expr(rnd, depth - 1)                       # noqa: E731
    if kind < 0.45:
        return f"({sub()} {rnd.choice(_BIN)} {sub()})"
    if kind < 0.6:
        return f"{rnd.choice(_F1)}({sub()})"
    if kind < 0.8:
        return rnd.choice(_F2) % sub()
    if kind < 0.9:
        return f"({sub()} ? {sub()} : {sub()})"
    return f"({sub()} < {sub()} <= {sub()})"


def random_exprs():
    rnd = random.Random(20261127)
    n = 40
    t = [float(x) for x in np.cumsum([rnd.uniform(0.03, 0.12) for _ in range(n)])]
    cols = {}
    for name in "abc":
        vals = [rnd.choice([0, 1, 2, 3, -2, 0.5, 7, 250]) if rnd.random() < 0.7
                else rnd.uniform(-5, 9) for _ in range(n)]
        cols[name] = [None if rnd.random() < 0.12 else float(v) for v in vals]
    rows = [{k: v[i] for k, v in cols.items()} for i in range(n)]
    exprs = []
    for _ in range(500):
        text = _random_expr(rnd, 3)
        e = compile_expr(text, names={"a", "b", "c"})
        exprs.append({"text": text, "out": [num(e.eval(row, ti)) for row, ti in zip(rows, t)]})
    # fixed cases that exercise Python number semantics
    fixed = ["-7 % 3", "7 % -3", "-7 // 2", "7.5 // -2", "-0.5 % 1", "5 % 2.5", "1e308 * 10 - 1e308",
             "round(2.675, 2)", "round(0.125, 2)", "round(0.375, 2)", "round(-2.5)", "round(3.5)",
             "round(1234.5678, -2)", "round(-0.004, 2)", "round(1e300, 2)", "round(x, 3)",
             "2 ** 0.5", "pow(-8, 1/3)", "(-8) ** 3", "0 ** 0", "1 ** (1e308*10 - 1e308*10)",
             "abs(-0.0)", "sign(0)", "int(-0.9)", "floor(-0.5)", "ceil(-0.5)", "~0", "-1 >> 1",
             "-9 >> 2", "1 << 61", "1 << 62", "(1 << 61) * 2", "bit(-1, 63)", "bit(-2, 0)",
             "signed(0x80)", "signed(0x7F)", "signed(0x8000, 16)", "signed(-1, 64)",
             "signed(0x1FF, 9)", "signed(300)", "0x7fffffff & 0xff", "5 ^ 3", "5 | 3",
             "table(2.5, 0, 0, 5, 10, 9, 2)", "table(7, 0, 0, 5, 10, 9, 2)",
             "poly(1.5, 0.5, -2, 0.25)", "thermistor(128, 0.001129, 0.000234, 0.0000000876)",
             "thermistor(x, 0.001129, 0.000234, 0.0000000876, 1023)",
             "clamp(x, 0, 1)", "between(x, 0, 1)", "min(x, 1, -1)", "max(x, 1, 3)",
             "sqrt(x)", "ln(x)", "log(x)", "log10(x)", "exp(x)", "sin(x)", "cos(x)",
             "x ? 1 : 2", "not x", "!x", "x and 1", "x or 0", "x == 0.1 + 0.2",
             "1 < x < 3", "1 < x > 0", "x // 0.1", "x % 0.1", "x / 3", "x * 1e-300 * 1e-300"]
    xs = [0.0, -0.0, 0.3, 0.30000000000000004, 1.0, 2.0, 2.5, -2.5, 10.0, 1234.5678,
          255.0, -1.5, 1e-7, 123456789.123, 7.0]
    fixed_out = []
    for text in fixed:
        e = compile_expr(text)
        fixed_out.append({"text": text, "xs": xs, "out": [num(compile_expr(text).eval({"x": x}))
                                                           for x in xs],
                          "constant": num(e.constant)})
    return {"t": t, "cols": cols, "exprs": exprs, "fixed": fixed_out}


# ---- 3. error messages ------------------------------------------------------

BAD = ["", "1 +", "(1", "1 1", "a = 1", "'x'", "a.b", "a @ b", "avg(x)", "avg(x, y)",
       "avg(x, 0)", "smooth(x, 0)", "min(1)", "round(1, x)", "table(x, 0, 1, 2, 3, 4)",
       "table(x, 5, 1, 2, 3)", "[x-]", "[x-1.5]", "[3]", "x[0]", "if", "time(1)", "[x-99999]",
       "x ? 1", "1 if x", "1 + $", "__import__('os').system('true')", "().__class__.__bases__[0]",
       "open('/etc/passwd')", "eval('1')", "lambda: 1", "[c for c in x]", "x.__class__",
       "globals()", "getattr(x, 'real')", "{1: 2}", "x; y", "x\ny", "f'{x}'", "x := 5", "*x",
       "**x", "x[::1]", "yield", "import os", "median(x, 2000)", "ema(x, -1)", "held(x, -5)",
       "prev(x, 0)", "prev(x, 1, 2)", "delta(x, 1)", "signed(x, y)", "clamp(x, 1)",
       "between(1, 2, 3, 4)", "bit(1)", "poly(x)", "thermistor(1, 2)", "abs()", "pow(1)",
       "foo(1)", "averge(rpm, 5)", "smoth(x, 3)", "sqr(4)", "1 +* 2", "((1)", "1)",
       "[x-1", "[if]", "a ? b", "not", "1 and", "x[", "\"q\"", "1.2.3", "(" * 40 + "1" + ")" * 40,
       "-" * 40 + "1", "+".join(["x"] * 250), "1+" * 1001 + "1", "a b c", "1 ,2", "x !",
       "x < ", "0x", "0b2", "x if 1 else"]

NAMED = [("[rmp] + 1", ["rpm", "map"], {}), ("[k-1]", [], {"k": 1}), ("ectt > 5", ["ect", "iat"], {}),
         ("batt_lo + 1", ["batt", "batt_low", "baro"], {"bat": 1}), ("b + 1", ["x"], {}),
         ("b[0] + x", ["x"], {}), ("q[1]", ["x"], {}), ("tps2", ["tps", "rpm", "tps_v"], {}),
         ("[inj_dutyy]", ["inj_duty", "inj", "duty"], {}), ("zz", ["a"], {})]


def errors():
    out = []
    for text in BAD:
        try:
            compile_expr(text, names=None if "x" not in text else {"x", "y", "rpm"})
            out.append({"text": text, "error": None})
        except ExprError as exc:
            out.append({"text": text, "error": str(exc), "message": exc.message, "pos": exc.pos})
    for text, names, consts in NAMED:
        arrays = ("b",) if "b[" in text or text.startswith("b ") else ()
        try:
            compile_expr(text, names=names, constants=consts, arrays=arrays)
            out.append({"text": text, "names": names, "constants": consts, "arrays": list(arrays),
                        "error": None})
        except ExprError as exc:
            out.append({"text": text, "names": names, "constants": consts, "arrays": list(arrays),
                        "error": str(exc), "message": exc.message, "pos": exc.pos})
    return out


# ---- 4. number formatting ---------------------------------------------------

def formatting():
    values = [0.0, -0.0, 0.5, 1.5, 2.5, -2.5, 0.125, 0.375, 2.675, 1.005, 11.456, 105.0, 106.0,
              -1000.0, 12.04999, 9.9999, 99.95, 1e-5, 0.0001234, 123456.0, 123456789.0, 1e16,
              1e20, 3.14159, 1 / 3, 2 / 3, 14.696, 0.1 + 0.2, 6.912, 790.5, -0.004, 1e300,
              5e-324, 230.0, 88.0, 0.6213712, 21.5]
    specs = ["", ".0f", ".1f", ".2f", ".3f", "g", ".4g", "+.4g", ".2", ".1", "5", "05", ">8.1f",
             "<8.2f", "^9.1f", "+.1f", " .2f", "%", ".1%", "8", ".3g", "+g", "d", ".0g"]
    fmt = []
    for v in values:
        for spec in specs:
            try:
                s = format(v, spec)
            except (ValueError, TypeError):
                s = None
            fmt.append([v, spec, s])
    strs = [["abc", ">6"], ["abc", "<6"], ["abc", "^6"], ["abc", "^7"], ["abc", ".2"],
            ["abc", "8.2"], ["abc", ""], ["abc", "f"], ["abc", "+"]]
    for s, spec in strs:
        try:
            fmt.append([s, spec, format(s, spec)])
        except (ValueError, TypeError):
            fmt.append([s, spec, None])
    rounds = []
    for v in values + [1234.5678, -1234.5678, 15.0, 25.0, -35.0, 0.45, 0.55, 1.45, 2.345]:
        for d in [-3, -2, -1, 0, 1, 2, 3, 6]:
            if d == 0:
                r = float(round(v))
            else:
                r = round(v, d)
            rounds.append([v, d, r])
    reprs = [[v, repr(v)] for v in values + [1e22, 1.5e-7, 123.0, 1e15, 1e-4, -2.5e-5]]
    fills = []
    fields = {"value": 11.456, "label": "Battery low", "units": "V", "rpm": 800.0, "high": 105,
              "low": None, "name": "batt_low", "rate": -1234.5678, "o2": 0.123, "tps": 72.4}
    for tpl in ["{label}: {value:.1f} {units} at {rpm:.0f} rpm ({nope}) {value.real}",
                "{value}", "{value:g}", "{high}", "{high:.1f}", "{low}", "{low:.1f}",
                "{rate:+.0f}", "{rate:.4g}", "{name:>12}", "{value:bad}", "{value:.1z}",
                "O2 {o2:.2f} V at {tps:.0f}% throttle, {rpm:.0f} rpm", "{{value}}", "{value:%}",
                "{value:10.3f}|", "{label:.3}", "{units:d}", "{rpm:d}", "{value:d}"]:
        fills.append([tpl, _fill(tpl, fields)])
    return {"format": fmt, "round": rounds, "repr": reprs, "fill": {"fields": fields, "cases": fills}}


# ---- 5. shipped math channels and alarms ------------------------------------

def shipped():
    amap = am.load_default()
    math_defs = [{k: getattr(m, k) for k in ("name", "expr", "label", "units", "min", "max",
                                            "decimals", "kind", "enabled")} for m in amap.math]
    alarm_defs = []
    for r in amap.alarms:
        d = {k: getattr(r, k) for k in ("name", "channel", "low", "high", "rate_low", "rate_high",
                                       "rate_window_ms", "when", "clear_when", "gate", "hold_s",
                                       "clear_hold_s", "hysteresis", "severity", "priority",
                                       "latching", "actions", "message", "label", "enabled")}
        d["actions"] = list(d["actions"])
        alarm_defs.append(d)
    names = amap.names + amap.obd2_names
    units = {c.name: c.units for c in amap.channels}
    units.update({c.name: c.units for c in amap.obd2})
    verified = {c.name: c.verified for c in amap.channels}
    labels = {c.name: [c.label, c.units] for c in amap.channels}
    labels.update({m.name: [m.label, m.units] for m in amap.math})
    engine = amap.math_engine()
    alarms = amap.alarm_engine()

    # a synthetic drive: idle, rev, hot coolant, sagging battery, lean pulls, stall
    rnd = random.Random(7)
    n = 1200
    t, rows = [], []
    tt = 0.0
    for i in range(n):
        tt += rnd.uniform(0.04, 0.07)
        phase = i / n
        rpm = 780 + 20 * math.sin(i / 7)
        if 0.2 < phase < 0.3:
            rpm = 780 + 6500 * math.sin((phase - 0.2) * math.pi / 0.1)
        if 0.85 < phase:
            rpm = max(0.0, 780 - (phase - 0.85) * 40000)
        row = {c: 0.0 for c in amap.names}
        row.update(rpm=rpm, ect=88 + max(0, (phase - 0.4) * 120) - max(0, (phase - 0.6) * 240),
                   batt=14.2 - (2.8 if 0.65 < phase < 0.75 else 0) + rnd.uniform(-0.05, 0.05),
                   tps=80.0 if 0.22 < phase < 0.28 else 2.0,
                   o2=0.1 if 0.22 < phase < 0.28 else 0.45 + 0.4 * math.sin(i / 3),
                   map=30 + (60 if 0.22 < phase < 0.28 else 0) + rnd.uniform(-1, 1),
                   baro=101.0, iat=27.0 + phase * 10, inj=2.4 + (4 if 0.22 < phase < 0.28 else 0),
                   stft=rnd.uniform(-3, 3), ltft=-1.0)
        if rnd.random() < 0.03:                      # dropouts
            for k in ("rpm", "ect", "batt", "inj"):
                if rnd.random() < 0.5:
                    row[k] = None
        t.append(tt)
        rows.append(row)
    math_out = []
    events = []
    for ti, row in zip(t, rows):
        m = engine.update(row, ti)
        math_out.append({k: num(v) for k, v in m.items()})
        for ev in alarms.update({**row, **m}, ti):
            events.append({"t": ev.t, "kind": ev.kind, "name": ev.name, "severity": ev.severity,
                           "priority": ev.priority, "value": num(ev.value), "message": ev.message,
                           "actions": list(ev.actions), "channel": ev.channel,
                           "unverified": ev.unverified})
    USED = ("rpm", "ect", "batt", "tps", "o2", "map", "baro", "iat", "inj", "stft", "ltft")
    acks = [{"kind": e.kind, "name": e.name, "t": e.t} for e in alarms.acknowledge(t=999.0)]
    return {"constants": amap.constants, "names": names, "units": units, "verified": verified,
            "labels": labels, "math": math_defs, "alarms": alarm_defs,
            "math_order": engine.names, "math_warnings": engine.warnings,
            "base_inputs": {k: sorted(engine.base_inputs(k)) for k in engine.names},
            "all_names": amap.all_names,
            "t": t, "rows": [{k: r[k] for k in USED} for r in rows], "math_out": math_out, "events": events, "acks": acks}


# ---- 6. unit checks and unit conversion -------------------------------------

def unit_cases():
    units = {"ect": "°C", "iat": "°C", "map": "kPa", "baro": "kPa", "batt": "V", "rpm": "rpm",
             "vss": "km/h", "o2": "V", "inj": "ms"}
    texts = ["[ect] - [iat]", "[map] / [baro] * 100", "[ect] * 1.8 + 32", "avg([map], 500) - [baro]",
             "[ect] + [map]", "[ect] > [batt]", "max([map], [batt])", "[rpm] > 1000 ? [ect] : [batt]",
             "slope([rpm], 500)", "minw([batt], 5000) < 12", "integ([vss])", "rate([ect]) + [ect]",
             "between([ect], [map], 5)", "clamp([ect], 0, [batt])", "coalesce([map], [ect])",
             "-[ect] + [iat]", "~[ect] + [map]", "[ect] < [iat] < [map]", "2 * [ect] - [iat]",
             "[inj] * [rpm] / 1200", "abs([ect]) - [map]", "[ect] && [map] > [batt]"]
    declared = [None, "°C", "kPa", "%", "rpm/s", "°c", "K"]
    out = []
    for text in texts:
        for d in declared:
            out.append([text, d, check_units(parse(text), units, d)])
    conv = []
    for q, table in hu._TABLE.items():
        for a in table:
            for b in table:
                for v in [-40.0, 0.0, 37.5, 101.325, 1.0, 88.0, 250.0]:
                    conv.append([v, a, b, hu.convert(v, a, b), hu.convert_span(v, a, b),
                                 list(hu.factors(a, b))])
    return {"units": units, "check": out, "convert": conv}


def main() -> int:
    dump("formulas.json", formulas())
    dump("random_exprs.json", random_exprs())
    dump("errors.json", errors())
    dump("formatting.json", formatting())
    dump("shipped.json", shipped())
    dump("units.json", unit_cases())
    helps = funcs.function_help()
    dump("function_help.json", helps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
