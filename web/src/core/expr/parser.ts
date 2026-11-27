/**
 * Tokenizer and recursive-descent parser. Text in, AST out, nothing executed.
 *
 * Grammar (lowest precedence first)::
 *
 *     expr     := ifelse [ '?' expr ':' expr ]           a ? b : c
 *     ifelse   := or [ 'if' or 'else' ifelse ]           b if a else c
 *     or       := and ( ('||' | 'or') and )*
 *     and      := not ( ('&&' | 'and') not )*
 *     not      := 'not' not | compare
 *     compare  := bitor ( ('<'|'<='|'>'|'>='|'=='|'!=') bitor )*
 *     bitor    := bitxor ( '|' bitxor )*
 *     bitxor   := bitand ( '^' bitand )*
 *     bitand   := shift ( '&' shift )*
 *     shift    := sum ( ('<<' | '>>') sum )*
 *     sum      := term ( ('+' | '-') term )*
 *     term     := unary ( ('*' | '/' | '//' | '%') unary )*
 *     unary    := ('-' | '+' | '~' | '!') unary | power
 *     power    := atom [ '**' unary ]
 *     atom     := NUMBER | NAME | NAME '(' args ')' | NAME '[' expr ']'
 *               | '[' NAME [ '-' INT ] ']' | '(' expr ')'
 *
 * ``[rpm]`` and a bare ``rpm`` mean the same thing; ``[rpm-1]`` is the value one
 * sample ago. ``^`` is XOR as in Python, power is ``**``. ``!`` binds tightly
 * as in C; ``not`` binds loosely as in Python.
 */

import { ExprError, children, type Node, type Num } from './nodes';
import { pyRepr } from './pynum';

export const MAX_LENGTH = 2000;
export const MAX_DEPTH = 32;
export const MAX_TREE_DEPTH = 200;

const TOKEN = new RegExp(
  String.raw`(?<ws>\s+)` +
  String.raw`|(?<num>0[xX][0-9a-fA-F]+|0[bB][01]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)` +
  String.raw`|(?<name>[A-Za-z_][A-Za-z0-9_]*)` +
  String.raw`|(?<op>\*\*|\/\/|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^~<>!?:(),\[\]])`,
  'y',
);

const KEYWORDS = new Set(['and', 'or', 'not', 'if', 'else']);
const NAMED_NUMBERS: Record<string, number> = { true: 1, True: 1, false: 0, False: 0 };

export interface Token { kind: 'num' | 'name' | 'op' | 'end'; text: string; pos: number }

export function tokenize(text: string): Token[] {
  if (text.length > MAX_LENGTH) {
    throw new ExprError(`expression is longer than ${MAX_LENGTH} characters`, text.slice(0, 40) + '...');
  }
  const out: Token[] = [];
  let pos = 0;
  while (pos < text.length) {
    TOKEN.lastIndex = pos;
    const m = TOKEN.exec(text);
    if (m === null) {
      const ch = String.fromCodePoint(text.codePointAt(pos)!);
      let hint = '';
      if (ch === "'" || ch === '"') hint = ' (text is not allowed, only numbers and names)';
      else if (ch === '.') hint = ' (attribute access is not allowed)';
      else if (ch === '=') hint = ' (use == to compare)';
      throw new ExprError(`unexpected character ${pyRepr(ch)}${hint}`, text, pos);
    }
    const g = m.groups!;
    const kind = g.ws !== undefined ? 'ws' : g.num !== undefined ? 'num' : g.name !== undefined ? 'name' : 'op';
    if (kind !== 'ws') out.push({ kind, text: m[0], pos });
    pos += m[0].length;
  }
  out.push({ kind: 'end', text: '', pos: text.length });
  return out;
}

function number(tok: Token): Num {
  const s = tok.text;
  const p2 = s.slice(0, 2);
  if (p2 === '0x' || p2 === '0X') return { type: 'num', value: Number.parseInt(s.slice(2), 16), pos: tok.pos };
  if (p2 === '0b' || p2 === '0B') return { type: 'num', value: Number.parseInt(s.slice(2), 2), pos: tok.pos };
  return { type: 'num', value: Number(s.endsWith('.') ? s.slice(0, -1) : s), pos: tok.pos };
}

const LEVELS: string[][] = [['|'], ['^'], ['&'], ['<<', '>>'], ['+', '-'], ['*', '/', '//', '%']];
const COMPARE_OPS = ['<', '<=', '>', '>=', '==', '!='];

class Parser {
  private readonly toks: Token[];
  private i = 0;
  private depth = 0;

  constructor(private readonly text: string) {
    this.toks = tokenize(text);
  }

  private atEnd(): boolean {
    return this.tok.kind === 'end';
  }

  private get tok(): Token {
    return this.toks[this.i]!;
  }

  private is(...ops: string[]): boolean {
    const t = this.tok;
    return (t.kind === 'op' && ops.includes(t.text))
      || (t.kind === 'name' && ops.includes(t.text) && KEYWORDS.has(t.text));
  }

  private take(): Token {
    const t = this.tok;
    this.i += 1;
    return t;
  }

  private expect(op: string): Token {
    if (!this.is(op)) throw this.error(`expected ${pyRepr(op)}`);
    return this.take();
  }

  private error(what: string): ExprError {
    const t = this.tok;
    const got = t.kind === 'end' ? 'the end' : pyRepr(t.text);
    return new ExprError(`${what}, got ${got}`, this.text, t.pos);
  }

  private enter(): void {
    this.depth += 1;
    if (this.depth > MAX_DEPTH) throw new ExprError('expression is nested too deeply', this.text, this.tok.pos);
  }

  parse(): Node {
    if (this.tok.kind === 'end') throw new ExprError('empty expression', this.text, 0);
    const node = this.expr();
    if (!this.atEnd()) throw this.error('expected an operator or the end');
    return node;
  }

  private expr(): Node {
    this.enter();
    let node = this.ifelse();
    if (this.is('?')) {
      const pos = this.take().pos;
      const then = this.expr();
      this.expect(':');
      const other = this.expr();
      node = { type: 'cond', test: node, then, other, pos };
    }
    this.depth -= 1;
    return node;
  }

  private ifelse(): Node {
    let node = this.or();
    if (this.is('if')) {
      const pos = this.take().pos;
      const test = this.or();
      this.expect('else');
      const other = this.ifelse();
      node = { type: 'cond', test, then: node, other, pos };
    }
    return node;
  }

  private or(): Node {
    const node = this.and();
    if (!this.is('||', 'or')) return node;
    const pos = this.tok.pos;
    const values = [node];
    while (this.is('||', 'or')) {
      this.take();
      values.push(this.and());
    }
    return { type: 'bool', op: 'or', values, pos };
  }

  private and(): Node {
    const node = this.not();
    if (!this.is('&&', 'and')) return node;
    const pos = this.tok.pos;
    const values = [node];
    while (this.is('&&', 'and')) {
      this.take();
      values.push(this.not());
    }
    return { type: 'bool', op: 'and', values, pos };
  }

  private not(): Node {
    if (this.is('not')) {
      this.enter();
      const pos = this.take().pos;
      const node: Node = { type: 'unary', op: 'not', operand: this.not(), pos };
      this.depth -= 1;
      return node;
    }
    return this.compare();
  }

  private compare(): Node {
    const node = this.binary(0);
    if (!this.is(...COMPARE_OPS)) return node;
    const pos = this.tok.pos;
    const ops: string[] = [];
    const rights: Node[] = [];
    while (this.is(...COMPARE_OPS)) {
      ops.push(this.take().text);
      rights.push(this.binary(0));
    }
    return { type: 'compare', left: node, ops, rights, pos };
  }

  private binary(level: number): Node {
    if (level === LEVELS.length) return this.unary();
    const ops = LEVELS[level]!;
    let node = this.binary(level + 1);
    while (this.tok.kind === 'op' && ops.includes(this.tok.text)) {
      const tok = this.take();
      node = { type: 'binary', op: tok.text, left: node, right: this.binary(level + 1), pos: tok.pos };
    }
    return node;
  }

  private unary(): Node {
    const t = this.tok;
    if (t.kind === 'op' && (t.text === '-' || t.text === '+' || t.text === '~' || t.text === '!')) {
      this.enter();
      const tok = this.take();
      const op = tok.text === '!' ? 'not' : (tok.text as '-' | '+' | '~');
      const node: Node = { type: 'unary', op, operand: this.unary(), pos: tok.pos };
      this.depth -= 1;
      return node;
    }
    return this.power();
  }

  private power(): Node {
    let node = this.atom();
    if (this.is('**')) {
      this.enter();
      const pos = this.take().pos;
      node = { type: 'binary', op: '**', left: node, right: this.unary(), pos };
      this.depth -= 1;
    }
    return node;
  }

  private atom(): Node {
    const tok = this.tok;
    if (tok.kind === 'num') {
      this.take();
      return number(tok);
    }
    if (tok.kind === 'name') {
      if (KEYWORDS.has(tok.text)) throw this.error('expected a value');
      this.take();
      if (Object.prototype.hasOwnProperty.call(NAMED_NUMBERS, tok.text)) {
        return { type: 'num', value: NAMED_NUMBERS[tok.text]!, pos: tok.pos };
      }
      if (this.is('(')) return this.call(tok);
      if (this.is('[')) {
        this.take();
        const index = this.expr();
        this.expect(']');
        return { type: 'index', name: tok.text, index, pos: tok.pos };
      }
      return { type: 'var', name: tok.text, lag: 0, pos: tok.pos };
    }
    if (this.is('(')) {
      this.take();
      const node = this.expr();
      this.expect(')');
      return node;
    }
    if (this.is('[')) return this.channelRef();
    throw this.error('expected a value');
  }

  private call(name: Token): Node {
    this.expect('(');
    const args: Node[] = [];
    if (!this.is(')')) {
      args.push(this.expr());
      while (this.is(',')) {
        this.take();
        args.push(this.expr());
      }
    }
    this.expect(')');
    return { type: 'call', func: name.text, args, pos: name.pos };
  }

  private channelRef(): Node {
    const pos = this.expect('[').pos;
    const tok = this.tok;
    if (tok.kind !== 'name' || KEYWORDS.has(tok.text)) throw this.error("expected a channel name after '['");
    this.take();
    let lag = 0;
    if (this.is('-')) {
      this.take();
      const num = this.tok;
      if (num.kind !== 'num' || !/^\d+$/.test(num.text)) {
        throw this.error("expected a whole number of samples after '-'");
      }
      this.take();
      lag = Number.parseInt(num.text, 10);
      if (lag > 10000) throw new ExprError('cannot look back more than 10000 samples', this.text, num.pos);
    }
    this.expect(']');
    return { type: 'var', name: tok.text, lag, pos };
  }
}

/** Parse expression text into an AST. Throws ExprError. */
export function parse(text: unknown): Node {
  if (typeof text !== 'string') {
    const kind = text === null || text === undefined ? 'NoneType' : typeof text === 'number' ? 'float' : typeof text;
    throw new ExprError(`expression must be text, not ${kind}`);
  }
  const stripped = text.trim();
  let node: Node;
  try {
    node = new Parser(stripped).parse();
  } catch (exc) {
    if (exc instanceof RangeError) throw new ExprError('expression is nested too deeply', stripped);
    throw exc;
  }
  if (depthOf(node) > MAX_TREE_DEPTH) throw new ExprError('expression is too long a chain of operators', stripped);
  return node;
}

function depthOf(node: Node): number {
  let best = 0;
  const stack: [Node, number][] = [[node, 1]];
  while (stack.length) {
    const [cur, d] = stack.pop()!;
    if (d > best) best = d;
    for (const c of children(cur)) stack.push([c, d + 1]);
  }
  return best;
}
