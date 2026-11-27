/**
 * A thin layer over a sql.js Database with the calls the Python code makes on
 * a sqlite3 connection: execute, fetchone, fetchall, executemany, and
 * splitting a script into statements without an implicit commit.
 *
 * Prepared statements are never kept between calls: ``Database.export``
 * (saving the file) frees every statement of the database.
 */

import type { Database } from './sqljs';

export type SqlValue = number | string | Uint8Array | null;
export type Row = SqlValue[];

function bindable(v: unknown): SqlValue {
  if (v === undefined || v === null) return null;
  if (typeof v === 'boolean') return v ? 1 : 0;
  if (typeof v === 'number') return v !== v ? null : v;   // NaN is "no value", like Python's sqlite3
  if (typeof v === 'bigint') return Number(v);
  if (typeof v === 'string') return v;
  if (ArrayBuffer.isView(v)) return new Uint8Array(v.buffer, v.byteOffset, v.byteLength);
  return String(v);
}

export class Conn {
  constructor(public db: Database) {}

  run(sql: string, params: readonly unknown[] = []): void {
    if (params.length === 0) {
      this.db.run(sql);
      return;
    }
    this.db.run(sql, params.map(bindable));
  }

  all(sql: string, params: readonly unknown[] = []): Row[] {
    const stmt = this.db.prepare(sql);
    try {
      if (params.length) stmt.bind(params.map(bindable));
      const out: Row[] = [];
      while (stmt.step()) out.push(stmt.get() as Row);
      return out;
    } finally {
      stmt.free();
    }
  }

  /** Rows as objects keyed by column name. */
  objects(sql: string, params: readonly unknown[] = []): Record<string, SqlValue>[] {
    const stmt = this.db.prepare(sql);
    try {
      if (params.length) stmt.bind(params.map(bindable));
      const out: Record<string, SqlValue>[] = [];
      while (stmt.step()) out.push(stmt.getAsObject() as Record<string, SqlValue>);
      return out;
    } finally {
      stmt.free();
    }
  }

  columns(sql: string, params: readonly unknown[] = []): { names: string[]; rows: Row[] } {
    const stmt = this.db.prepare(sql);
    try {
      if (params.length) stmt.bind(params.map(bindable));
      const rows: Row[] = [];
      while (stmt.step()) rows.push(stmt.get() as Row);
      return { names: stmt.getColumnNames(), rows };
    } finally {
      stmt.free();
    }
  }

  get(sql: string, params: readonly unknown[] = []): Row | undefined {
    const stmt = this.db.prepare(sql);
    try {
      if (params.length) stmt.bind(params.map(bindable));
      return stmt.step() ? (stmt.get() as Row) : undefined;
    } finally {
      stmt.free();
    }
  }

  many(sql: string, rows: readonly (readonly unknown[])[]): void {
    if (!rows.length) return;
    const stmt = this.db.prepare(sql);
    try {
      for (const r of rows) {
        stmt.run(r.map(bindable));
      }
    } finally {
      stmt.free();
    }
  }

  /** The id of the last inserted row. */
  lastId(): number {
    return Number(this.get('SELECT last_insert_rowid()')![0]);
  }

  /** Execute several statements one by one (``schema.run_script``). */
  script(text: string): void {
    for (const stmt of splitStatements(text)) this.db.run(stmt);
  }

  /** ``BEGIN IMMEDIATE`` ... ``COMMIT``, rolled back on any error. */
  tx<T>(fn: () => T): T {
    this.db.run('BEGIN IMMEDIATE');
    try {
      const out = fn();
      this.db.run('COMMIT');
      return out;
    } catch (err) {
      try {
        this.db.run('ROLLBACK');
      } catch {
        /* already rolled back */
      }
      throw err;
    }
  }

}

/** Split SQL text at top-level semicolons (outside quotes and comments). */
export function splitStatements(text: string): string[] {
  const out: string[] = [];
  let cur = '';
  let i = 0;
  let depth = 0;                       // BEGIN ... END in triggers
  while (i < text.length) {
    const ch = text[i]!;
    if (ch === "'" || ch === '"' || ch === '`' || ch === '[') {
      const close = ch === '[' ? ']' : ch;
      let j = i + 1;
      while (j < text.length) {
        if (text[j] === close) {
          if (close !== ']' && text[j + 1] === close) { j += 2; continue; }
          break;
        }
        j++;
      }
      cur += text.slice(i, j + 1);
      i = j + 1;
      continue;
    }
    if (ch === '-' && text[i + 1] === '-') {
      const j = text.indexOf('\n', i);
      const end = j < 0 ? text.length : j;
      cur += text.slice(i, end);
      i = end;
      continue;
    }
    if (ch === '/' && text[i + 1] === '*') {
      const j = text.indexOf('*/', i + 2);
      const end = j < 0 ? text.length : j + 2;
      cur += text.slice(i, end);
      i = end;
      continue;
    }
    if (/[A-Za-z_]/.test(ch) && (i === 0 || !/[A-Za-z0-9_]/.test(text[i - 1]!))) {
      const m = /^[A-Za-z_][A-Za-z0-9_]*/.exec(text.slice(i, i + 32))!;
      const word = m[0].toUpperCase();
      if (word === 'BEGIN' && /CREATE\s+(TEMP\s+|TEMPORARY\s+)?TRIGGER/i.test(cur)) depth++;
      else if (word === 'END' && depth > 0) depth--;
      cur += m[0];
      i += m[0].length;
      continue;
    }
    if (ch === ';' && depth === 0) {
      if (cur.trim()) out.push(cur.trim());
      cur = '';
      i++;
      continue;
    }
    cur += ch;
    i++;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}
