/**
 * Named filters: boolean expressions that gate histograms, scatter plots and
 * exports (port of hondalog/analysis/filters.py).
 *
 * The text is a hondalog expression over the drive's channels (`ect > 80 and
 * closed_loop`). The evaluator lives in src/core/expr (agent #3); this module
 * takes it through `setExprCompiler` (or per filter) so analysis does not
 * depend on its internals. A row where the expression has no value does not pass.
 */
import { tableEnv, type Table } from './series';

export interface CompiledExpr {
  /** channel names the expression reads ("t" allowed) */
  readonly deps: Iterable<string>;
  /** truthy per row; NaN / no value must come back false */
  mask(env: Record<string, Float64Array>, t: Float64Array): ArrayLike<boolean | number>;
}
/** Compile expression text; throw on bad text. */
export type ExprCompiler = (text: string) => CompiledExpr;

let defaultCompiler: ExprCompiler | null = null;
/** Wire the expression engine in once (the integrator does this at start-up). */
export function setExprCompiler(compiler: ExprCompiler | null): void { defaultCompiler = compiler; }

export class Filter {
  readonly compiled: CompiledExpr;

  constructor(readonly name: string, readonly expr: string, public enabled = true, compiler?: ExprCompiler) {
    const c = compiler ?? defaultCompiler;
    if (!c) throw new Error('no expression compiler: call setExprCompiler first');
    this.compiled = c(expr);       // throws on bad text
  }

  mask(table: Table): Uint8Array {
    const m = this.compiled.mask(tableEnv(table), table.t);
    return Uint8Array.from({ length: table.t.length }, (_, i) => (m[i] ? 1 : 0));
  }

  toJSON(): { name: string; expr: string; enabled: boolean } {
    return { name: this.name, expr: this.expr, enabled: this.enabled };
  }

  static fromJSON(doc: { name: string; expr: string; enabled?: boolean }, compiler?: ExprCompiler): Filter {
    return new Filter(String(doc.name), String(doc.expr), doc.enabled ?? true, compiler);
  }
}

/** Starting points (default map channel names), all off. */
export const PRESET_FILTERS: readonly { name: string; expr: string; enabled: boolean }[] = [
  { name: 'warm', expr: 'ect > 80', enabled: false },
  { name: 'closed loop', expr: 'closed_loop', enabled: false },
  { name: 'steady throttle', expr: 'abs(rate(tps)) < 5', enabled: false },
  { name: 'engine running', expr: 'rpm > 400', enabled: false },
  { name: 'link up', expr: 'link', enabled: false },
];

/** Filters applied together (AND of the enabled ones). Each filter's mask is
 *  cached per table, so toggling one costs one AND over the cached masks. */
export class FilterSet {
  filters: Filter[];
  private cache = new Map<string, Uint8Array>();
  private cachedTable: Table | null = null;

  constructor(filters: Iterable<Filter> = [], private readonly compiler?: ExprCompiler) {
    this.filters = [...filters];
  }

  add(name: string, expr: string, enabled = true): Filter {
    const f = new Filter(name, expr, enabled, this.compiler);
    this.filters = [...this.filters.filter((x) => x.name !== name), f];
    return f;
  }

  remove(name: string): void { this.filters = this.filters.filter((f) => f.name !== name); }

  enable(name: string, on = true): void { for (const f of this.filters) if (f.name === name) f.enabled = on; }

  get(name: string): Filter | undefined { return this.filters.find((f) => f.name === name); }

  get active(): Filter[] { return this.filters.filter((f) => f.enabled); }

  /** Channel names the enabled filters need in the table. */
  channels(): Set<string> {
    const out = new Set<string>();
    for (const f of this.active) for (const d of f.compiled.deps) out.add(d);
    out.delete('t');
    return out;
  }

  /** 1 for rows that pass every enabled filter. */
  mask(table: Table): Uint8Array {
    if (table !== this.cachedTable) { this.cache.clear(); this.cachedTable = table; }
    const out = new Uint8Array(table.t.length).fill(1);
    for (const f of this.active) {
      let m = this.cache.get(f.expr);
      if (!m) { m = f.mask(table); this.cache.set(f.expr, m); }
      for (let i = 0; i < out.length; i++) out[i] = out[i]! & m[i]!;
    }
    return out;
  }

  toJSON(): ReturnType<Filter['toJSON']>[] { return this.filters.map((f) => f.toJSON()); }

  static fromJSON(docs: Iterable<{ name: string; expr: string; enabled?: boolean }>, compiler?: ExprCompiler): FilterSet {
    return new FilterSet([...docs].map((d) => Filter.fromJSON(d, compiler)), compiler);
  }
}
