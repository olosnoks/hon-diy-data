/**
 * The one safe expression evaluator: formulas, math channels, alarm, filter
 * and trigger conditions all go through here (port of hondalog/expr).
 *
 *     import { compileExpr } from '@core/expr';
 *
 *     const duty = compileExpr('[inj] * [rpm] / 1200', { names: ['inj', 'rpm'] });
 *     duty.eval({ inj: 2.4, rpm: 3000 }, 12.5);       // 6
 *     duty.eval({ inj: null, rpm: 3000 }, 12.6);      // null (invalid)
 *     duty.evalArray({ inj: injCol, rpm: rpmCol }, tCol);   // Float64Array, NaN = invalid
 *
 * Channel formulas from the address map compile with
 * ``compileExpr(formula, { names: ['x'], arrays: ['b'] })`` and must not be
 * ``stateful`` or ``usesTime``; evaluate with ``{ x: rawUnsigned, b: rawBytes }``.
 *
 * Text is tokenized and parsed by hand into an AST of nine node types, then
 * compiled once into closures. There is no eval / Function anywhere, only the
 * functions in ``funcs`` can be called, and attribute access, strings,
 * lambdas and comprehensions do not exist in the grammar.
 */

export { EvalError, Expr, compileExpr, references } from './compiler';
export type { Column, CompileOptions, Value, Values } from './compiler';
export { functionHelp } from './funcs';
export { ExprError } from './nodes';
export type { Node } from './nodes';
export { parse } from './parser';
export { checkUnits } from './unitcheck';
export { MathChannel, MathEngine, MathError } from './mathch';
export type { MathChannelInit, MathValues } from './mathch';
export { pyFixed, pyFormat, pyG, pyRepr, pyRound, reprFloat } from './pynum';
