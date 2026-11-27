/**
 * The rows an export writes (hondalog/export/__init__.py export_table):
 * channels on one grid, filtered, cut to [t0, t1], and the markers in range.
 */

import type { Column } from '../model';
import type { Log, Table } from '../reader';

export interface ExportOptions {
  channels?: string[] | null;
  hz?: number | null;
  base?: string | null;
  t0?: number | null;
  t1?: number | null;
  /** extra channels a filter needs (looked up even when not exported) */
  filterChannels?: string[];
  /** keep row i of the full table (the channels and the filter's) */
  filter?: ((table: Table, i: number) => boolean) | null;
}

export function exportTable(log: Log, opts: ExportOptions = {}): { table: Table; markers: [number, string][] } {
  const names = opts.channels ? [...opts.channels] : log.channels;
  const wanted = [...names];
  for (const n of [...(opts.filterChannels ?? [])].sort()) if (!wanted.includes(n) && log.channels.includes(n)) wanted.push(n);
  const table = log.table(wanted, { hz: opts.hz ?? undefined, base: opts.base ?? undefined });
  const keep: number[] = [];
  for (let i = 0; i < table.t.length; i++) {
    if (opts.filter && !opts.filter(table, i)) continue;
    if (opts.t0 !== null && opts.t0 !== undefined && table.t[i]! < opts.t0) continue;
    if (opts.t1 !== null && opts.t1 !== undefined && table.t[i]! > opts.t1) continue;
    keep.push(i);
  }
  const pick = (a: Float64Array) => Float64Array.from(keep, (i) => a[i]!);
  const out: Table = { t: pick(table.t), columns: {}, units: {}, names };
  for (const n of names) {
    out.columns[n] = pick(table.columns[n]!);
    out.units[n] = table.units[n] ?? '';
  }
  const lo = opts.t0 ?? -Infinity, hi = opts.t1 ?? Infinity;
  return { table: out, markers: log.markers.filter(([t]) => lo <= t && t <= hi) };
}

/** Column definitions of the exported channels. */
export function exportColumns(log: Log, names: string[]): Record<string, Column> {
  const out: Record<string, Column> = {};
  for (const n of names) {
    const c = log.column(n);
    if (c) out[n] = c;
  }
  return out;
}

/** The log's meta without the map text (it stays in the database). */
export function smallMeta(log: Log): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(log.meta)) if (k !== 'address_map_text') out[k] = v;
  return out;
}
