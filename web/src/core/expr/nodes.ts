/** AST node types for the expression language. Plain data, no behaviour. */

import { pyRepr } from './pynum';

/** A problem in an expression's text: syntax, unknown name, bad call. */
export class ExprError extends Error {
  readonly exprMessage: string;
  readonly text: string;
  readonly pos: number | null;

  constructor(message: string, text = '', pos: number | null = null) {
    super(ExprError.render(message, text, pos));
    this.name = 'ExprError';
    this.exprMessage = message;
    this.text = text;
    this.pos = pos;
  }

  static render(message: string, text: string, pos: number | null): string {
    if (!text) return message;
    if (pos === null) return `${message} in ${pyRepr(text)}`;
    return `${message} at column ${pos + 1} of ${pyRepr(text)}`;
  }
}

export interface Num { type: 'num'; value: number; pos: number }
/** A channel, constant or formula variable. ``lag`` > 0 is ``[name-N]``. */
export interface Var { type: 'var'; name: string; lag: number; pos: number }
/** ``b[0]``: one element of an array variable (formulas only). */
export interface Index { type: 'index'; name: string; index: Node; pos: number }
export interface Unary { type: 'unary'; op: '-' | '+' | '~' | 'not'; operand: Node; pos: number }
export interface Binary { type: 'binary'; op: string; left: Node; right: Node; pos: number }
/** ``a < b <= c`` chains like Python: every link must hold. */
export interface Compare { type: 'compare'; left: Node; ops: string[]; rights: Node[]; pos: number }
/** 'and' | 'or'; the result is always 0 or 1. */
export interface Bool { type: 'bool'; op: 'and' | 'or'; values: Node[]; pos: number }
export interface Cond { type: 'cond'; test: Node; then: Node; other: Node; pos: number }
export interface Call { type: 'call'; func: string; args: Node[]; pos: number }

export type Node = Num | Var | Index | Unary | Binary | Compare | Bool | Cond | Call;

export function children(node: Node): Node[] {
  switch (node.type) {
    case 'num':
    case 'var':
      return [];
    case 'index':
      return [node.index];
    case 'unary':
      return [node.operand];
    case 'binary':
      return [node.left, node.right];
    case 'compare':
      return [node.left, ...node.rights];
    case 'bool':
      return node.values;
    case 'cond':
      return [node.test, node.then, node.other];
    case 'call':
      return node.args;
  }
}

/** Every node of the tree, parents before children. */
export function* walk(node: Node): Generator<Node> {
  const stack: Node[] = [node];
  while (stack.length) {
    const cur = stack.pop()!;
    yield cur;
    const kids = children(cur);
    for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]!);
  }
}
