/**
 * Plain data types of a log (port of hondalog/data/model.py): a column, a
 * stream definition and its packed little-endian row layout (the one the
 * ``chunks`` BLOBs use), an event.
 */

export type Dtype = 'f4' | 'f8' | 'u1' | 'i1' | 'u2' | 'i2' | 'u4' | 'i4' | 'u8' | 'i8';

export const DTYPES: Record<Dtype, number> = { f4: 4, f8: 8, u1: 1, i1: 1, u2: 2, i2: 2, u4: 4, i4: 4, u8: 8, i8: 8 };

/** event kinds that the replay view shows as a marker line */
export const MARKER_KINDS = ['marker', 'trigger', 'alarm'];

export interface Column {
  readonly name: string;
  readonly dtype: Dtype;
  readonly units: string;
  readonly label: string;
  readonly kind: string;          // "value" or "flag"
  readonly decimals: number;
  readonly lo: number | null;
  readonly hi: number | null;
}

export function column(name: string, opts: Partial<Omit<Column, 'name'>> | Dtype = {}): Column {
  const o = typeof opts === 'string' ? { dtype: opts } : opts;
  const dtype = (o.dtype ?? 'f4') as Dtype;
  if (!(dtype in DTYPES)) throw new Error(`column ${JSON.stringify(name)}: unsupported dtype ${JSON.stringify(dtype)}`);
  if (!name || name === 't' || name === 'raw') throw new Error(`bad column name ${JSON.stringify(name)}`);
  return {
    name, dtype, units: o.units ?? '', label: o.label ?? '', kind: o.kind ?? 'value',
    decimals: o.decimals ?? 2, lo: o.lo ?? null, hi: o.hi ?? null,
  };
}

export function isFloatDtype(d: Dtype): boolean {
  return d === 'f4' || d === 'f8';
}

export interface Field {
  name: string;               // "t", a column, or "raw"
  dtype: Dtype;               // raw: u1
  offset: number;
  size: number;               // bytes (raw: raw_len)
  raw?: boolean;
}

export class StreamInfo {
  readonly name: string;
  readonly columns: readonly Column[];
  readonly source: string;
  readonly rawLen: number;
  readonly attrs: Record<string, unknown>;
  /** t (f8), the columns, then the raw bytes: the packed row */
  readonly fields: Field[];
  readonly itemsize: number;

  constructor(name: string, columns: readonly Column[], source = '', rawLen = 0, attrs: Record<string, unknown> = {}) {
    const names = columns.map((c) => c.name);
    if (new Set(names).size !== names.length) throw new Error(`stream ${JSON.stringify(name)}: duplicate column names`);
    if (!(rawLen >= 0 && rawLen <= 65535)) throw new Error(`stream ${JSON.stringify(name)}: raw_len out of range`);
    this.name = name;
    this.columns = columns;
    this.source = source;
    this.rawLen = rawLen;
    this.attrs = attrs;
    const fields: Field[] = [{ name: 't', dtype: 'f8', offset: 0, size: 8 }];
    let at = 8;
    for (const c of columns) {
      fields.push({ name: c.name, dtype: c.dtype, offset: at, size: DTYPES[c.dtype] });
      at += DTYPES[c.dtype];
    }
    if (rawLen) {
      fields.push({ name: 'raw', dtype: 'u1', offset: at, size: rawLen, raw: true });
      at += rawLen;
    }
    this.fields = fields;
    this.itemsize = at;
  }

  get columnNames(): string[] {
    return this.columns.map((c) => c.name);
  }

  column(name: string): Column | undefined {
    return this.columns.find((c) => c.name === name);
  }

  /** Same row layout (numpy: equal dtype). */
  sameLayout(other: StreamInfo): boolean {
    if (this.rawLen !== other.rawLen || this.columns.length !== other.columns.length) return false;
    return this.columns.every((c, i) => c.name === other.columns[i]!.name && c.dtype === other.columns[i]!.dtype);
  }
}

export interface LogEvent {
  t: number;
  kind: string;
  text: string;
  data: Record<string, any>;
}

/** Rows of one stream, column-wise: what the readers and exporters work on. */
export interface StreamData {
  info: StreamInfo;
  t: Float64Array;
  /** column name -> values as float64 (NaN = no value) */
  columns: Record<string, Float64Array>;
  /** rows x rawLen, row-major */
  raw: Uint8Array;
  /** seconds, NaN where unknown */
  latency: Float64Array;
}

export function emptyStreamData(info: StreamInfo, n = 0): StreamData {
  const columns: Record<string, Float64Array> = {};
  for (const c of info.columns) columns[c.name] = new Float64Array(n).fill(isFloatDtype(c.dtype) ? NaN : 0);
  return { info, t: new Float64Array(n), columns, raw: new Uint8Array(n * info.rawLen), latency: new Float64Array(n).fill(NaN) };
}

// ---- packed rows ----------------------------------------------------------

function writeValue(view: DataView, at: number, dtype: Dtype, v: number): void {
  switch (dtype) {
    case 'f4': view.setFloat32(at, v, true); return;
    case 'f8': view.setFloat64(at, v, true); return;
    case 'u1': view.setUint8(at, v); return;
    case 'i1': view.setInt8(at, v); return;
    case 'u2': view.setUint16(at, v, true); return;
    case 'i2': view.setInt16(at, v, true); return;
    case 'u4': view.setUint32(at, v, true); return;
    case 'i4': view.setInt32(at, v, true); return;
    case 'u8': view.setBigUint64(at, BigInt(Math.trunc(v)), true); return;
    case 'i8': view.setBigInt64(at, BigInt(Math.trunc(v)), true); return;
  }
}

export function readValue(view: DataView, at: number, dtype: Dtype): number {
  switch (dtype) {
    case 'f4': return view.getFloat32(at, true);
    case 'f8': return view.getFloat64(at, true);
    case 'u1': return view.getUint8(at);
    case 'i1': return view.getInt8(at);
    case 'u2': return view.getUint16(at, true);
    case 'i2': return view.getInt16(at, true);
    case 'u4': return view.getUint32(at, true);
    case 'i4': return view.getInt32(at, true);
    case 'u8': return Number(view.getBigUint64(at, true));
    case 'i8': return Number(view.getBigInt64(at, true));
  }
}

/** One row in the stream's packed layout (``struct.pack``). None/NaN of an integer column is 0. */
export function packRow(info: StreamInfo, t: number, values: readonly (number | null | undefined)[], raw?: Uint8Array): Uint8Array {
  const out = new Uint8Array(info.itemsize);
  const view = new DataView(out.buffer);
  view.setFloat64(0, t, true);
  info.columns.forEach((c, i) => {
    let v = values[i];
    if (v === null || v === undefined || v !== v) v = isFloatDtype(c.dtype) ? NaN : 0;
    writeValue(view, info.fields[i + 1]!.offset, c.dtype, v as number);
  });
  if (info.rawLen && raw) out.set(raw.subarray(0, info.rawLen), info.itemsize - info.rawLen);
  return out;
}

/** Packed rows -> column-wise data. */
export function unpackRows(info: StreamInfo, bytes: Uint8Array, n: number): StreamData {
  const out = emptyStreamData(info, n);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const size = info.itemsize;
  for (let i = 0; i < n; i++) {
    const base = i * size;
    out.t[i] = view.getFloat64(base, true);
    for (let k = 0; k < info.columns.length; k++) {
      const f = info.fields[k + 1]!;
      out.columns[f.name]![i] = readValue(view, base + f.offset, f.dtype);
    }
    if (info.rawLen) out.raw.set(bytes.subarray(base + size - info.rawLen, base + size), i * info.rawLen);
  }
  return out;
}

/** Column-wise data -> packed rows (``rows.tobytes()``). */
export function packRows(data: StreamData, lo = 0, hi = data.t.length): Uint8Array {
  const info = data.info;
  const n = hi - lo;
  const out = new Uint8Array(n * info.itemsize);
  const view = new DataView(out.buffer);
  for (let i = 0; i < n; i++) {
    const base = i * info.itemsize;
    view.setFloat64(base, data.t[lo + i]!, true);
    for (let k = 0; k < info.columns.length; k++) {
      const f = info.fields[k + 1]!;
      let v = data.columns[f.name]![lo + i]!;
      if (v !== v && !isFloatDtype(f.dtype)) v = 0;
      writeValue(view, base + f.offset, f.dtype, v);
    }
    if (info.rawLen) out.set(data.raw.subarray((lo + i) * info.rawLen, (lo + i + 1) * info.rawLen), base + info.itemsize - info.rawLen);
  }
  return out;
}

/** Round a value the way storing it in the column's dtype does. */
export function castTo(dtype: Dtype, v: number): number {
  if (dtype === 'f4') return Math.fround(v);
  if (dtype === 'f8') return v;
  if (v !== v) return 0;
  const buf = new DataView(new ArrayBuffer(8));
  writeValue(buf, 0, dtype, Math.trunc(v));
  return readValue(buf, 0, dtype);
}

/** Stable sort of column-wise data by time; returns the same object if already sorted. */
export function sortByTime(data: StreamData): StreamData {
  const n = data.t.length;
  let sorted = true;
  for (let i = 1; i < n; i++) if (data.t[i]! < data.t[i - 1]!) { sorted = false; break; }
  if (sorted) return data;
  const order = Array.from({ length: n }, (_, i) => i).sort((a, b) => data.t[a]! - data.t[b]! || a - b);
  return takeRows(data, order);
}

export function takeRows(data: StreamData, idx: ArrayLike<number>): StreamData {
  const n = idx.length;
  const out = emptyStreamData(data.info, n);
  const w = data.info.rawLen;
  for (let j = 0; j < n; j++) {
    const i = idx[j]!;
    out.t[j] = data.t[i]!;
    out.latency[j] = data.latency[i]!;
    for (const c of data.info.columns) out.columns[c.name]![j] = data.columns[c.name]![i]!;
    if (w) out.raw.set(data.raw.subarray(i * w, (i + 1) * w), j * w);
  }
  return out;
}

export function concatStreamData(parts: StreamData[]): StreamData {
  if (parts.length === 1) return parts[0]!;
  const info = parts[0]!.info;
  const n = parts.reduce((s, p) => s + p.t.length, 0);
  const out = emptyStreamData(info, n);
  let at = 0;
  for (const p of parts) {
    out.t.set(p.t, at);
    out.latency.set(p.latency, at);
    for (const c of info.columns) out.columns[c.name]!.set(p.columns[c.name]!, at);
    out.raw.set(p.raw, at * info.rawLen);
    at += p.t.length;
  }
  return out;
}

/** Index of the first element >= x (side left) or > x (side right) in a sorted array. */
export function searchsorted(a: ArrayLike<number>, x: number, side: 'left' | 'right' = 'left'): number {
  let lo = 0;
  let hi = a.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    const v = a[mid]!;
    if (side === 'left' ? v < x : v <= x) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}
