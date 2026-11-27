/**
 * Loading sql.js (SQLite compiled to WebAssembly) once per page.
 *
 * In the browser and in the Tauri web view the .wasm file comes from the
 * bundle (Vite's ``?url`` import, no network). Under Node (the tests) sql.js
 * finds its .wasm next to itself. The platform layer may hand over the bytes
 * itself with ``setSqlJsWasm``.
 */

import initSqlJs from 'sql.js';
import type { Database, SqlJsStatic } from 'sql.js';
import wasmUrl from 'sql.js/dist/sql-wasm.wasm?url';

export type { Database, SqlJsStatic };

let loading: Promise<SqlJsStatic> | null = null;
let wasmBinary: ArrayBuffer | null = null;

/** Give sql.js its .wasm bytes (call before the first ``loadSqlJs``). */
export function setSqlJsWasm(bytes: ArrayBuffer | Uint8Array): void {
  wasmBinary = bytes instanceof Uint8Array ? bytes.slice().buffer : bytes;
  loading = null;
}

function isNode(): boolean {
  const p = (globalThis as { process?: { versions?: { node?: string } } }).process;
  // Node, also under jsdom in the tests: sql.js reads its .wasm with fs from next to itself
  return !!p?.versions?.node;
}

export function loadSqlJs(): Promise<SqlJsStatic> {
  if (!loading) {
    const config: Record<string, unknown> = {};
    if (wasmBinary) config.wasmBinary = wasmBinary;
    else if (!isNode()) config.locateFile = () => wasmUrl;
    loading = initSqlJs(config).catch((err: unknown) => {
      loading = null;
      throw err;
    });
  }
  return loading;
}
