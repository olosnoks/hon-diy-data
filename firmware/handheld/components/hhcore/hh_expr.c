/* hhcore: runs the postfix programs that `hondalog-handheld export-map`
 * compiles from the address map's formulas, math channels and alarm
 * conditions. See hhcore.h for why the device has no parser.
 *
 * The rules are hondalog/expr's:
 *   - NaN is "no value". An operand without a value gives no value, except
 *     where Python would not have looked at it: `0 and x`, `1 or x`, the
 *     branch of `a ? b : c` not taken, valid(x), coalesce(...).
 *   - anything Python raises on (divide by zero, sqrt of a negative, a shift
 *     by 64, ...) is no value;
 *   - the final result must be finite.
 * Arithmetic is double, as on the laptop. */
#include <math.h>
#include <string.h>

#include "hhcore.h"

#define INT_LIMIT 4611686018427387904.0     /* 2^62, the laptop's limit for bit operators */

static int to_int(double a, int64_t *out)
{
    if (!(a > -INT_LIMIT && a < INT_LIMIT)) return 0;   /* also catches NaN */
    *out = (int64_t)a;                                  /* toward zero, like int() */
    return 1;
}

static double py_mod(double a, double b)
{
    if (b == 0.0) return NAN;
    double r = fmod(a, b);
    if (r != 0.0 && ((r < 0.0) != (b < 0.0))) r += b;
    return r;
}

static double truth(double v) { return v != 0.0 ? 1.0 : 0.0; }

static double call(uint8_t fn, const double *a, int n)
{
    int64_t i, k;
    for (int j = 0; j < n; j++) if (isnan(a[j])) return NAN;
    switch (fn) {
    case HH_FN_ABS: return n == 1 ? fabs(a[0]) : NAN;
    case HH_FN_MIN:
    case HH_FN_MAX: {
        if (n < 2) return NAN;
        double m = a[0];
        for (int j = 1; j < n; j++) if (fn == HH_FN_MIN ? a[j] < m : a[j] > m) m = a[j];
        return m;
    }
    case HH_FN_ROUND: {         /* nearbyint rounds halves to even, as Python's round() */
        if (n == 1) return nearbyint(a[0]);
        if (n != 2) return NAN;
        double p = pow(10.0, (double)(int)a[1]);
        return a[1] == 0.0 ? nearbyint(a[0]) : nearbyint(a[0] * p) / p;
    }
    case HH_FN_INT: return n == 1 ? trunc(a[0]) : NAN;
    case HH_FN_FLOAT: return n == 1 ? a[0] : NAN;
    case HH_FN_FLOOR: return n == 1 ? floor(a[0]) : NAN;
    case HH_FN_CEIL: return n == 1 ? ceil(a[0]) : NAN;
    case HH_FN_SQRT: return n == 1 && a[0] >= 0.0 ? sqrt(a[0]) : NAN;
    case HH_FN_EXP: return n == 1 ? exp(a[0]) : NAN;
    case HH_FN_LN: return n == 1 && a[0] > 0.0 ? log(a[0]) : NAN;
    case HH_FN_LOG10: return n == 1 && a[0] > 0.0 ? log10(a[0]) : NAN;
    case HH_FN_POW: return n == 2 ? pow(a[0], a[1]) : NAN;
    case HH_FN_SIN: return n == 1 ? sin(a[0]) : NAN;
    case HH_FN_COS: return n == 1 ? cos(a[0]) : NAN;
    case HH_FN_SIGN: return n == 1 ? (double)((a[0] > 0) - (a[0] < 0)) : NAN;
    case HH_FN_CLAMP: return n == 3 ? (a[0] < a[1] ? a[1] : a[0] > a[2] ? a[2] : a[0]) : NAN;
    case HH_FN_BETWEEN: return n == 3 ? (double)(a[1] <= a[0] && a[0] <= a[2]) : NAN;
    case HH_FN_BIT:
        if (n != 2 || !to_int(a[0], &i) || !to_int(a[1], &k) || k < 0 || k >= 64) return NAN;
        return (double)((i >> k) & 1);
    case HH_FN_SIGNED: {
        int64_t bits = 8;
        if (n < 1 || n > 2 || !to_int(a[0], &i) || (n == 2 && !to_int(a[1], &bits))) return NAN;
        if (bits < 1 || bits > 62) return NAN;
        i &= ((int64_t)1 << bits) - 1;
        return (double)((i & ((int64_t)1 << (bits - 1))) ? i - ((int64_t)1 << bits) : i);
    }
    case HH_FN_POLY: {          /* c0 + c1*x + c2*x^2 ... (Horner, highest first) */
        if (n < 2) return NAN;
        double acc = 0.0;
        for (int j = n - 1; j >= 1; j--) acc = acc * a[0] + a[j];
        return acc;
    }
    case HH_FN_TABLE: {         /* table(x, x0, y0, x1, y1, ...), ends held */
        int np = (n - 1) / 2;
        if (n < 5 || !(n & 1)) return NAN;
        const double *p = a + 1;
        if (a[0] <= p[0]) return p[1];
        for (int j = 1; j < np; j++) {
            double x1 = p[2 * j];
            if (a[0] <= x1) {
                double x0 = p[2 * j - 2], y0 = p[2 * j - 1], y1 = p[2 * j + 1];
                return x1 == x0 ? y1 : y0 + (y1 - y0) * (a[0] - x0) / (x1 - x0);
            }
        }
        return p[2 * np - 1];
    }
    case HH_FN_THERMISTOR: {    /* Steinhart-Hart on an ADC count */
        if (n < 4 || n > 5) return NAN;
        double full = n == 5 ? a[4] : 255.0;
        if (full - a[0] == 0.0) return NAN;
        double r = a[0] / (full - a[0]);
        if (!(r > 0.0)) return NAN;
        double ln_r = log(r);
        double den = a[1] + a[2] * ln_r + a[3] * pow(ln_r, 3.0);
        return den == 0.0 ? NAN : 1.0 / den - 273.15;
    }
    default: return NAN;
    }
}

/* Operands each opcode takes from the stack; -1 = not an opcode. */
static int arity(uint8_t op)
{
    if (op == HH_OP_CONST || op == HH_OP_IMM || op == HH_OP_X || op == HH_OP_BYTE || op == HH_OP_CH
        || op == HH_OP_TIME) return 0;
    if (op >= HH_OP_NEG && op <= HH_OP_VALID) return 1;
    if ((op >= HH_OP_ADD && op <= HH_OP_SHR) || (op >= HH_OP_LT && op <= HH_OP_NE) || op == HH_OP_AND
        || op == HH_OP_OR || op == HH_OP_COALESCE) return 2;
    if (op == HH_OP_SELECT) return 3;
    return -1;
}

static int operand_bytes(uint8_t op)
{
    if (op == HH_OP_CONST || op == HH_OP_CALL) return 2;
    if (op == HH_OP_IMM || op == HH_OP_BYTE || op == HH_OP_CH) return 1;
    return 0;
}

int hh_expr_check(const uint8_t *prog, size_t len, uint16_t n_const, uint16_t n_ch)
{
    int depth = 0;
    for (size_t pc = 0; pc < len;) {
        uint8_t op = prog[pc++];
        if (op == HH_OP_END) return depth == 1 && pc == len ? 0 : -1;
        int extra = operand_bytes(op);
        if (pc + (size_t)extra > len) return -1;
        if (op == HH_OP_CALL) {
            int argc = prog[pc + 1];
            if (prog[pc] >= HH_FN_COUNT || argc < 1 || argc > depth) return -1;
            depth -= argc - 1;
        } else {
            int take = arity(op);
            if (take < 0 || take > depth) return -1;
            if (op == HH_OP_CONST && (prog[pc] | (prog[pc + 1] << 8)) >= n_const) return -1;
            if (op == HH_OP_CH && prog[pc] >= n_ch) return -1;
            depth += 1 - take;
        }
        if (depth > HH_EXPR_STACK) return -1;
        pc += (size_t)extra;
    }
    return -1;      /* ran off the end without END */
}

double hh_expr_eval(const uint8_t *prog, size_t len, const hh_expr_env_t *env)
{
    double st[HH_EXPR_STACK];
    int sp = 0;
    int64_t i, k;

    for (size_t pc = 0; pc < len;) {
        uint8_t op = prog[pc++];
        double a, b, r;
        if (op == HH_OP_END) {
            if (sp != 1) return NAN;
            return isfinite(st[0]) ? st[0] : NAN;
        }
        if (pc + (size_t)operand_bytes(op) > len) return NAN;
        switch (op) {
        case HH_OP_CONST: {
            unsigned idx = prog[pc] | ((unsigned)prog[pc + 1] << 8);
            pc += 2;
            if (idx >= env->n_const || sp >= HH_EXPR_STACK) return NAN;
            memcpy(&st[sp++], (const uint8_t *)env->consts + idx * sizeof(double), sizeof(double));
            continue;
        }
        case HH_OP_IMM: if (sp >= HH_EXPR_STACK) return NAN; st[sp++] = prog[pc++]; continue;
        case HH_OP_X: if (sp >= HH_EXPR_STACK) return NAN; st[sp++] = env->x; continue;
        case HH_OP_TIME: if (sp >= HH_EXPR_STACK) return NAN; st[sp++] = env->t; continue;
        case HH_OP_BYTE: {
            uint8_t idx = prog[pc++];
            if (sp >= HH_EXPR_STACK) return NAN;
            st[sp++] = env->raw && idx < env->raw_len ? (double)env->raw[idx] : NAN;
            continue;
        }
        case HH_OP_CH: {
            uint8_t idx = prog[pc++];
            if (sp >= HH_EXPR_STACK) return NAN;
            st[sp++] = env->ch && idx < env->n_ch ? (double)env->ch[idx] : NAN;
            continue;
        }
        case HH_OP_CALL: {
            uint8_t fn = prog[pc], argc = prog[pc + 1];
            pc += 2;
            if (argc < 1 || argc > sp) return NAN;
            r = call(fn, &st[sp - argc], argc);
            sp -= argc;
            st[sp++] = isfinite(r) ? r : NAN;
            continue;
        }
        default: break;
        }
        int take = arity(op);
        if (take < 0 || take > sp) return NAN;
        if (take == 1) {
            a = st[sp - 1];
            if (op == HH_OP_VALID) r = isnan(a) ? 0.0 : 1.0;
            else if (isnan(a)) r = NAN;
            else if (op == HH_OP_NEG) r = -a;
            else if (op == HH_OP_NOT) r = a != 0.0 ? 0.0 : 1.0;
            else r = to_int(a, &i) ? (double)~i : NAN;
            st[sp - 1] = r;
            continue;
        }
        if (take == 3) {
            double test = st[sp - 3];
            r = isnan(test) ? NAN : test != 0.0 ? st[sp - 2] : st[sp - 1];
            sp -= 2;
            st[sp - 1] = r;
            continue;
        }
        a = st[sp - 2]; b = st[sp - 1];
        sp--;
        if (op == HH_OP_AND) {              /* Python's short circuit, seen from the results */
            st[sp - 1] = isnan(a) ? NAN : a == 0.0 ? 0.0 : isnan(b) ? NAN : truth(b);
            continue;
        }
        if (op == HH_OP_OR) {
            st[sp - 1] = isnan(a) ? NAN : a != 0.0 ? 1.0 : isnan(b) ? NAN : truth(b);
            continue;
        }
        if (op == HH_OP_COALESCE) { st[sp - 1] = isnan(a) ? b : a; continue; }
        if (isnan(a) || isnan(b)) { st[sp - 1] = NAN; continue; }
        switch (op) {
        case HH_OP_ADD: r = a + b; break;
        case HH_OP_SUB: r = a - b; break;
        case HH_OP_MUL: r = a * b; break;
        case HH_OP_DIV: r = b == 0.0 ? NAN : a / b; break;
        case HH_OP_FLOORDIV: r = b == 0.0 ? NAN : floor(a / b); break;
        case HH_OP_MOD: r = py_mod(a, b); break;
        case HH_OP_POW: r = pow(a, b); break;
        case HH_OP_BAND: r = to_int(a, &i) && to_int(b, &k) ? (double)(i & k) : NAN; break;
        case HH_OP_BOR: r = to_int(a, &i) && to_int(b, &k) ? (double)(i | k) : NAN; break;
        case HH_OP_BXOR: r = to_int(a, &i) && to_int(b, &k) ? (double)(i ^ k) : NAN; break;
        case HH_OP_SHL:
            if (!to_int(a, &i) || !to_int(b, &k) || k < 0 || k >= 64) { r = NAN; break; }
            if (k >= 62 ? i != 0 : (i < 0 ? -i : i) >= ((int64_t)1 << (62 - k))) { r = NAN; break; }
            r = (double)(i * ((int64_t)1 << (k >= 62 ? 0 : k)));
            break;
        case HH_OP_SHR:
            if (!to_int(a, &i) || !to_int(b, &k) || k < 0 || k >= 64) { r = NAN; break; }
            r = (double)(i < 0 ? ~(~i >> k) : i >> k);
            break;
        case HH_OP_LT: r = a < b; break;
        case HH_OP_LE: r = a <= b; break;
        case HH_OP_GT: r = a > b; break;
        case HH_OP_GE: r = a >= b; break;
        case HH_OP_EQ: r = a == b; break;
        case HH_OP_NE: r = a != b; break;
        default: return NAN;
        }
        st[sp - 1] = isfinite(r) ? r : NAN;
    }
    return NAN;     /* no END */
}
