/**
 * The hondalog expression language's parser (hondalog/expr/parser.py), text in,
 * syntax tree out. Kept here, a faithful port, because the handheld's byte code
 * compiler walks this exact tree and HANDHELD.MAP must come out byte-identical to
 * the Python's. ``Num.isInt`` keeps Python's int/float distinction (an integer
 * 0-255 compiles to an immediate, anything else to the constant pool).
 *
 * The evaluator lives in src/core/expr/ (agent #3); this module only parses.
 */

export type Node =
  | { t: 'num'; value: number; isInt: boolean; pos: number }
  | { t: 'var'; name: string; lag: number; pos: number }
  | { t: 'index'; name: string; index: Node; pos: number }
  | { t: 'unary'; op: string; operand: Node; pos: number }
  | { t: 'binary'; op: string; left: Node; right: Node; pos: number }
  | { t: 'compare'; left: Node; ops: string[]; rights: Node[]; pos: number }
  | { t: 'bool'; op: 'and' | 'or'; values: Node[]; pos: number }
  | { t: 'cond'; test: Node; then: Node; other: Node; pos: number }
  | { t: 'call'; func: string; args: Node[]; pos: number };

export class ExprError extends Error {
  constructor(readonly msg: string, readonly text = '', readonly pos: number | null = null) {
    super(!text ? msg : pos === null ? `${msg} in ${pyRepr(text)}` : `${msg} at column ${pos + 1} of ${pyRepr(text)}`);
  }
}

/** Python's repr() of a str, for the messages. */
export function pyRepr(s: string): string {
  const q = s.includes("'") && !s.includes('"') ? '"' : "'";
  let out = q;
  for (const ch of s) {
    if (ch === '\\') out += '\\\\';
    else if (ch === q) out += '\\' + q;
    else if (ch === '\n') out += '\\n';
    else if (ch === '\r') out += '\\r';
    else if (ch === '\t') out += '\\t';
    else if (ch < ' ' || ch === '\x7f') out += '\\x' + ch.charCodeAt(0).toString(16).padStart(2, '0');
    else out += ch;
  }
  return out + q;
}

export const MAX_LENGTH = 2000;
export const MAX_DEPTH = 32;
export const MAX_TREE_DEPTH = 200;

const TOKEN = /(\s+)|(0[xX][0-9a-fA-F]+|0[bB][01]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(\*\*|\/\/|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^~<>!?:(),[\]])/y;
const KEYWORDS = new Set(['and', 'or', 'not', 'if', 'else']);
const NAMED_NUMBERS: Record<string, number> = { true: 1, True: 1, false: 0, False: 0 };

interface Token { kind: 'num' | 'name' | 'op' | 'end'; text: string; pos: number }

export function tokenize(text: string): Token[] {
  if (text.length > MAX_LENGTH) throw new ExprError(`expression is longer than ${MAX_LENGTH} characters`, text.slice(0, 40) + '...');
  const out: Token[] = [];
  let pos = 0;
  while (pos < text.length) {
    TOKEN.lastIndex = pos;
    const m = TOKEN.exec(text);
    if (m === null) {
      const ch = text[pos];
      let hint = '';
      if (ch === "'" || ch === '"') hint = ' (text is not allowed, only numbers and names)';
      else if (ch === '.') hint = ' (attribute access is not allowed)';
      else if (ch === '=') hint = ' (use == to compare)';
      throw new ExprError(`unexpected character ${pyRepr(ch ?? '')}${hint}`, text, pos);
    }
    if (m[2] !== undefined) out.push({ kind: 'num', text: m[2], pos });
    else if (m[3] !== undefined) out.push({ kind: 'name', text: m[3], pos });
    else if (m[4] !== undefined) out.push({ kind: 'op', text: m[4], pos });
    pos = TOKEN.lastIndex;
  }
  out.push({ kind: 'end', text: '', pos: text.length });
  return out;
}

function number(tok: Token): Node {
  const s = tok.text;
  if (s.startsWith('0x') || s.startsWith('0X')) return { t: 'num', value: parseInt(s.slice(2), 16), isInt: true, pos: tok.pos };
  if (s.startsWith('0b') || s.startsWith('0B')) return { t: 'num', value: parseInt(s.slice(2), 2), isInt: true, pos: tok.pos };
  if (/^\d+$/.test(s)) return { t: 'num', value: parseInt(s, 10), isInt: true, pos: tok.pos };
  return { t: 'num', value: parseFloat(s), isInt: false, pos: tok.pos };
}

const LEVELS: string[][] = [['|'], ['^'], ['&'], ['<<', '>>'], ['+', '-'], ['*', '/', '//', '%']];

class Parser {
  private readonly toks: Token[];
  private i = 0;
  private depth = 0;

  constructor(private readonly text: string) {
    this.toks = tokenize(text);
  }

  private get tok(): Token { return this.toks[this.i]!; }

  private is(...ops: string[]): boolean {
    const t = this.tok;
    return (t.kind === 'op' && ops.includes(t.text)) || (t.kind === 'name' && ops.includes(t.text) && KEYWORDS.has(t.text));
  }

  private take(): Token { return this.toks[this.i++]!; }

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

  private atEnd(): boolean { return this.tok.kind === 'end'; }

  parse(): Node {
    if (this.tok.kind === 'end') throw new ExprError('empty expression', this.text, 0);
    const node = this.expr();
    if (!this.atEnd()) throw this.error('expected an operator or the end');
    return node;
  }

  expr(): Node {
    this.enter();
    let node = this.ifelse();
    if (this.is('?')) {
      const pos = this.take().pos;
      const then = this.expr();
      this.expect(':');
      const other = this.expr();
      node = { t: 'cond', test: node, then, other, pos };
    }
    this.depth -= 1;
    return node;
  }

  ifelse(): Node {
    let node = this.or();
    if (this.is('if')) {
      const pos = this.take().pos;
      const test = this.or();
      this.expect('else');
      const other = this.ifelse();
      node = { t: 'cond', test, then: node, other, pos };
    }
    return node;
  }

  or(): Node {
    const node = this.and();
    if (!this.is('||', 'or')) return node;
    const pos = this.tok.pos;
    const values = [node];
    while (this.is('||', 'or')) {
      this.take();
      values.push(this.and());
    }
    return { t: 'bool', op: 'or', values, pos };
  }

  and(): Node {
    const node = this.not();
    if (!this.is('&&', 'and')) return node;
    const pos = this.tok.pos;
    const values = [node];
    while (this.is('&&', 'and')) {
      this.take();
      values.push(this.not());
    }
    return { t: 'bool', op: 'and', values, pos };
  }

  not(): Node {
    if (this.is('not')) {
      this.enter();
      const pos = this.take().pos;
      const node: Node = { t: 'unary', op: 'not', operand: this.not(), pos };
      this.depth -= 1;
      return node;
    }
    return this.compare();
  }

  compare(): Node {
    const node = this.binary(0);
    const cmp = ['<', '<=', '>', '>=', '==', '!='];
    if (!this.is(...cmp)) return node;
    const pos = this.tok.pos;
    const ops: string[] = [];
    const rights: Node[] = [];
    while (this.is(...cmp)) {
      ops.push(this.take().text);
      rights.push(this.binary(0));
    }
    return { t: 'compare', left: node, ops, rights, pos };
  }

  binary(level: number): Node {
    if (level === LEVELS.length) return this.unary();
    const ops = LEVELS[level]!;
    let node = this.binary(level + 1);
    while (this.tok.kind === 'op' && ops.includes(this.tok.text)) {
      const tok = this.take();
      node = { t: 'binary', op: tok.text, left: node, right: this.binary(level + 1), pos: tok.pos };
    }
    return node;
  }

  unary(): Node {
    if (this.tok.kind === 'op' && ['-', '+', '~', '!'].includes(this.tok.text)) {
      this.enter();
      const tok = this.take();
      const node: Node = { t: 'unary', op: tok.text === '!' ? 'not' : tok.text, operand: this.unary(), pos: tok.pos };
      this.depth -= 1;
      return node;
    }
    return this.power();
  }

  power(): Node {
    let node = this.atom();
    if (this.is('**')) {
      this.enter();
      const pos = this.take().pos;
      node = { t: 'binary', op: '**', left: node, right: this.unary(), pos };
      this.depth -= 1;
    }
    return node;
  }

  atom(): Node {
    const tok = this.tok;
    if (tok.kind === 'num') {
      this.take();
      return number(tok);
    }
    if (tok.kind === 'name') {
      if (KEYWORDS.has(tok.text)) throw this.error('expected a value');
      this.take();
      if (tok.text in NAMED_NUMBERS) return { t: 'num', value: NAMED_NUMBERS[tok.text]!, isInt: true, pos: tok.pos };
      if (this.is('(')) return this.call(tok);
      if (this.is('[')) {
        this.take();
        const index = this.expr();
        this.expect(']');
        return { t: 'index', name: tok.text, index, pos: tok.pos };
      }
      return { t: 'var', name: tok.text, lag: 0, pos: tok.pos };
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

  call(name: Token): Node {
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
    return { t: 'call', func: name.text, args, pos: name.pos };
  }

  channelRef(): Node {
    const pos = this.expect('[').pos;
    const tok = this.tok;
    if (tok.kind !== 'name' || KEYWORDS.has(tok.text)) throw this.error("expected a channel name after '['");
    this.take();
    let lag = 0;
    if (this.is('-')) {
      this.take();
      const num = this.tok;
      if (num.kind !== 'num' || !/^\d+$/.test(num.text)) throw this.error("expected a whole number of samples after '-'");
      this.take();
      lag = parseInt(num.text, 10);
      if (lag > 10000) throw new ExprError('cannot look back more than 10000 samples', this.text, num.pos);
    }
    this.expect(']');
    return { t: 'var', name: tok.text, lag, pos };
  }
}

export function children(node: Node): Node[] {
  switch (node.t) {
    case 'num': case 'var': return [];
    case 'index': return [node.index];
    case 'unary': return [node.operand];
    case 'binary': return [node.left, node.right];
    case 'compare': return [node.left, ...node.rights];
    case 'bool': return node.values;
    case 'cond': return [node.test, node.then, node.other];
    case 'call': return node.args;
  }
}

/** Every node, parents before children. */
export function* walk(node: Node): Generator<Node> {
  const stack = [node];
  while (stack.length) {
    const cur = stack.pop()!;
    yield cur;
    const ch = children(cur);
    for (let i = ch.length - 1; i >= 0; i--) stack.push(ch[i]!);
  }
}

function treeDepth(node: Node): number {
  let best = 0;
  const stack: [Node, number][] = [[node, 1]];
  while (stack.length) {
    const [cur, d] = stack.pop()!;
    best = Math.max(best, d);
    for (const c of children(cur)) stack.push([c, d + 1]);
  }
  return best;
}

/** Python ``str.strip()`` (whitespace as Python counts it, close enough for map files). */
export function pyStrip(s: string): string {
  return s.replace(/^[\s\x1c-\x1f\x85]+|[\s\x1c-\x1f\x85]+$/g, '');
}

/** Parse expression text into a tree. Throws ExprError. */
export function parse(text: string): Node {
  if (typeof text !== 'string') throw new ExprError(`expression must be text, not ${typeof text}`);
  const t = pyStrip(text);
  let node: Node;
  try {
    node = new Parser(t).parse();
  } catch (e) {
    if (e instanceof RangeError) throw new ExprError('expression is nested too deeply', t);
    throw e;
  }
  if (treeDepth(node) > MAX_TREE_DEPTH) throw new ExprError('expression is too long a chain of operators', t);
  return node;
}

/** Names an expression mentions (hondalog.expr.references). */
export function references(text: string): Set<string> {
  const out = new Set<string>();
  for (const n of walk(parse(text))) if (n.t === 'var' || n.t === 'index') out.add(n.name);
  return out;
}
