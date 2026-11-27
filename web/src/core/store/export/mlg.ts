/**
 * MegaLogViewer binary logs (``.mlg``, "MLVLG"), writer and reader: a port of
 * hondalog/export/mlg.py, byte for byte. All big-endian:
 *
 *     header v2 (24 bytes)  "MLVLG\0" | u16 version | u32 unix time | u32 info data start
 *                           | u32 data begin index | u16 record length | u16 field count
 *     header v1 (22 bytes)  the same, but info data start is u16
 *     field (v2: 89 bytes)  u8 type | 34s name | 10s units | u8 display style
 *                           | f32 scale | f32 transform | i8 digits | 34s category
 *     field (v1: 55 bytes)  the same without the category
 *     data block            u8 0 | u8 counter | u16 timestamp (10 us units, wraps) | record | u8 sum of the record bytes
 *     marker block          u8 1 | u8 counter | u16 timestamp | 50s message
 *
 *     display value = (raw + transform) * scale
 */

import { type Column, type LogEvent, StreamInfo, column, emptyStreamData, searchsorted } from '../model';
import { Log, type Table } from '../reader';

export const MAGIC = new Uint8Array([0x4d, 0x4c, 0x56, 0x4c, 0x47, 0x00]);
const HEADER_V2 = 24, HEADER_V1 = 22, FIELD_V2 = 89, FIELD_V1 = 55;
const MARKER_LEN = 54, MARKER_TEXT = 50;
export const U08 = 0, S08 = 1, U16 = 2, S16 = 3, U32 = 4, S32 = 5, S64 = 6, F32 = 7;
export const STYLE_FLOAT = 0, STYLE_ON_OFF = 4;
/** type code -> byte size */
const SIZES: Record<number, number> = { 0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 8, 7: 4, 10: 1, 11: 2, 12: 4 };
const INT_RANGE: Record<number, [number, number]> = { [U08]: [0, 255], [S16]: [-32768, 32767], [U16]: [0, 65535] };

export class MlgError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'MlgError';
  }
}

export interface MlgField {
  name: string;
  units: string;
  type: number;
  style: number;
  scale: number;
  transform: number;
  digits: number;
  category: string;
}

const field = (name: string, f: Partial<MlgField> = {}): MlgField => ({
  name, units: '', type: F32, style: STYLE_FLOAT, scale: 1.0, transform: 0.0, digits: 2, category: '', ...f,
});

/** Null-padded ASCII of at most ``limit - 1`` characters. */
export function asciiText(text: string, limit: number): Uint8Array {
  const t = text.replace(/°/g, 'deg').replace(/λ/g, 'lambda').replace(/µ/g, 'u');
  const out = new Uint8Array(limit);
  let k = 0;
  for (const ch of t) {
    if (k >= limit - 1) break;
    const code = ch.codePointAt(0)!;
    out[k++] = code < 128 ? code : 0x3f;
  }
  return out;
}

function cstr(bytes: Uint8Array): string {
  let end = bytes.indexOf(0);
  if (end < 0) end = bytes.length;
  return Array.from(bytes.subarray(0, end), (b) => (b < 128 ? String.fromCharCode(b) : '�')).join('');
}

/** numpy ``round``: half to even. */
function npRound(x: number): number {
  const r = Math.round(x);
  return Math.abs(x - Math.trunc(x)) === 0.5 ? 2 * Math.round(x / 2) : r;
}

/** Replace NaN: "hold" repeats the last good value (0 before the first), "zero" writes 0. */
export function fill(values: Float64Array, how: 'hold' | 'zero' | 'keep'): Float64Array {
  if (how === 'keep' || !values.some((v) => v !== v)) return values;
  const out = new Float64Array(values.length);
  let last = 0;
  let seen = false;
  for (let i = 0; i < values.length; i++) {
    const v = values[i]!;
    if (v === v) { out[i] = v; last = v; seen = true; }
    else out[i] = how === 'zero' ? 0 : seen ? last : 0;
  }
  return out;
}

/** A field type per channel: float32 (lossless), on/off flags as u8, ``compact``: scaled 16-bit integers. */
export function planFields(table: Table, columns: Record<string, Column> = {}, compact = false, category = ''): MlgField[] {
  const fields = [field('Time', { units: 's', digits: 3, category })];
  for (const name of table.names) {
    const col = columns[name] ?? column(name);
    const values = table.columns[name]!;
    const f = field(name, { units: table.units[name] ?? col.units, digits: col.decimals, category });
    const clean = !values.some((v) => v !== v);
    let mn = Infinity, mx = -Infinity;
    for (const v of values) { if (v < mn) mn = v; if (v > mx) mx = v; }
    if (col.kind === 'flag' && clean && values.length && mn >= 0 && mx <= 1) {
      f.type = U08; f.style = STYLE_ON_OFF; f.digits = 0;
    } else if (compact && clean && values.length) {
      const scale = 10.0 ** -Math.max(0, col.decimals);
      const raw = Array.from(values, (v) => npRound(v / scale));
      const lo = Math.min(...raw), hi = Math.max(...raw);
      for (const code of [S16, U16]) {
        const [a, b] = INT_RANGE[code]!;
        if (lo >= a && hi <= b) { f.type = code; f.scale = scale; break; }
      }
    }
    fields.push(f);
  }
  return fields;
}

function setValue(view: DataView, at: number, type: number, v: number): void {
  switch (type) {
    case 0: case 10: view.setUint8(at, v); break;
    case 1: view.setInt8(at, v); break;
    case 2: case 11: view.setUint16(at, v); break;
    case 3: view.setInt16(at, v); break;
    case 4: case 12: view.setUint32(at, v); break;
    case 5: view.setInt32(at, v); break;
    case 6: view.setBigInt64(at, BigInt(Math.trunc(v))); break;
    case 7: view.setFloat32(at, v); break;
  }
}

function getValue(view: DataView, at: number, type: number): number {
  switch (type) {
    case 0: case 10: return view.getUint8(at);
    case 1: return view.getInt8(at);
    case 2: case 11: return view.getUint16(at);
    case 3: return view.getInt16(at);
    case 4: case 12: return view.getUint32(at);
    case 5: return view.getInt32(at);
    case 6: return Number(view.getBigInt64(at));
    default: return view.getFloat32(at);
  }
}

const pyMod = (a: number, m: number) => ((a % m) + m) % m;

/** Python's ``round(x)`` (half to even) for the marker stamp. */
const pyRoundInt = npRound;

export interface MlgWriteOptions {
  version?: 1 | 2;
  columns?: Record<string, Column>;
  info?: string;
  /** unix seconds for the header (default now) */
  timestamp?: number | null;
  compact?: boolean;
  fill?: 'hold' | 'zero' | 'keep';
  category?: string;
}

/** A table (one row per record) and its markers as an MLG file. */
export function writeMlg(table: Table, markers: [number, string][] = [], opts: MlgWriteOptions = {}): Uint8Array {
  const version = opts.version ?? 2;
  if (version !== 1 && version !== 2) throw new MlgError('MLG version must be 1 or 2');
  const fields = planFields(table, opts.columns ?? {}, opts.compact ?? false, opts.category ?? '');
  const n = table.t.length;
  const offsets: number[] = [];
  let recLen = 0;
  for (const f of fields) { offsets.push(recLen); recLen += SIZES[f.type]!; }
  const blockLen = recLen + 5;
  const t0 = n ? table.t[0]! : 0;
  const blocks = new Uint8Array(n * blockLen);
  const view = new DataView(blocks.buffer);
  const filled = fields.slice(1).map((f) => fill(table.columns[f.name]!, opts.fill ?? 'hold'));
  for (let i = 0; i < n; i++) {
    const b = i * blockLen;
    const t = table.t[i]! - t0;
    view.setUint8(b, 0);
    view.setUint16(b + 2, pyMod(npRound(t * 1e5), 65536));
    setValue(view, b + 4 + offsets[0]!, F32, t);
    fields.forEach((f, k) => {
      if (k === 0) return;
      let v = filled[k - 1]![i]!;
      if (f.type !== F32) v = npRound(v / f.scale - f.transform);
      setValue(view, b + 4 + offsets[k]!, f.type, v);
    });
    let sum = 0;
    for (let j = b + 4; j < b + blockLen - 1; j++) sum += blocks[j]!;
    blocks[b + blockLen - 1] = sum & 0xff;
  }
  // markers go in front of the first record at or after their time
  const slots = markers.map(([mt]) => (n ? searchsorted(table.t, mt, 'left') : 0));
  const order = slots.map((_, j) => j).sort((a, b) => slots[a]! - slots[b]! || a - b);
  const total = n + markers.length;
  const isMarker = new Array<boolean>(total).fill(false);
  const markerPos = order.map((j, k) => slots[j]! + k);
  for (const p of markerPos) isMarker[p] = true;
  let r = 0;
  for (let c = 0; c < total; c++) if (!isMarker[c]) view.setUint8(r++ * blockLen + 1, c % 256);

  const enc = new TextEncoder();
  const info = opts.info ? Uint8Array.from([...enc.encode(opts.info.replace(/[^\x00-\x7f]/g, '?')), 0]) : new Uint8Array(0);
  const fieldSize = version === 2 ? FIELD_V2 : FIELD_V1;
  const headSize = version === 2 ? HEADER_V2 : HEADER_V1;
  const infoStart = info.length ? headSize + fieldSize * fields.length : 0;
  const dataBegin = headSize + fieldSize * fields.length + info.length;
  if (version === 1 && infoStart > 0xffff) throw new MlgError('too many fields for an MLG v1 info block; use version 2');
  const stamp = Math.trunc(opts.timestamp ?? Date.now() / 1000) >>> 0;

  const out = new Uint8Array(dataBegin + blocks.length + markers.length * MARKER_LEN);
  const ov = new DataView(out.buffer);
  out.set(MAGIC, 0);
  ov.setUint16(6, version);
  ov.setUint32(8, stamp);
  let at = 12;
  if (version === 2) { ov.setUint32(at, infoStart); at += 4; } else { ov.setUint16(at, infoStart); at += 2; }
  ov.setUint32(at, dataBegin); at += 4;
  ov.setUint16(at, recLen); at += 2;
  ov.setUint16(at, fields.length); at += 2;
  for (const f of fields) {
    ov.setUint8(at, f.type);
    out.set(asciiText(f.name, 34), at + 1);
    out.set(asciiText(f.units, 10), at + 35);
    ov.setUint8(at + 45, f.style);
    ov.setFloat32(at + 46, f.scale);
    ov.setFloat32(at + 50, f.transform);
    ov.setInt8(at + 54, f.digits);
    if (version === 2) out.set(asciiText(f.category, 34), at + 55);
    at += fieldSize;
  }
  out.set(info, at);
  at += info.length;
  let done = 0;
  order.forEach((j, k) => {
    const slot = slots[j]!;
    out.set(blocks.subarray(done * blockLen, slot * blockLen), at);
    at += (slot - done) * blockLen;
    done = slot;
    const [mt, text] = markers[j]!;
    out[at] = 1;
    out[at + 1] = markerPos[k]! % 256;
    ov.setUint16(at + 2, pyMod(pyRoundInt((mt - t0) * 1e5), 65536));
    out.set(asciiText(text, MARKER_TEXT), at + 4);
    at += MARKER_LEN;
  });
  out.set(blocks.subarray(done * blockLen), at);
  return out;
}

export interface MlgFile {
  version: number;
  timestamp: number;
  info: string;
  fields: MlgField[];
  t: Float64Array;
  data: Record<string, Float64Array>;
  markers: [number, string][];
  crcErrors: number;
  recordLength: number;
  dataBegin: number;
}

/** Decode an MLG file held in memory. Throws MlgError if it is not one. */
export function parseMlg(blob: Uint8Array): MlgFile {
  if (blob.length < HEADER_V1 || !MAGIC.every((b, i) => blob[i] === b)) throw new MlgError('not an MLG file (no MLVLG header)');
  const v = new DataView(blob.buffer, blob.byteOffset, blob.byteLength);
  const version = v.getUint16(6);
  let stamp: number, infoStart: number, dataBegin: number, recLen: number, count: number, head: number, fsize: number;
  if (version === 1) {
    stamp = v.getUint32(8); infoStart = v.getUint16(12); dataBegin = v.getUint32(14); recLen = v.getUint16(18); count = v.getUint16(20);
    head = HEADER_V1; fsize = FIELD_V1;
  } else if (version === 2) {
    if (blob.length < HEADER_V2) throw new MlgError('not an MLG file (no MLVLG header)');
    stamp = v.getUint32(8); infoStart = v.getUint32(12); dataBegin = v.getUint32(16); recLen = v.getUint16(20); count = v.getUint16(22);
    head = HEADER_V2; fsize = FIELD_V2;
  } else {
    throw new MlgError(`unsupported MLG version ${version}`);
  }
  if (head + fsize * count > blob.length || dataBegin > blob.length) throw new MlgError('MLG header points past the end of the file');
  const fields: MlgField[] = [];
  for (let i = 0; i < count; i++) {
    const at = head + i * fsize;
    const type = v.getUint8(at);
    if (!(type in SIZES)) throw new MlgError(`field ${i}: unknown type code ${type}`);
    const name = cstr(blob.subarray(at + 1, at + 35));
    const units = cstr(blob.subarray(at + 35, at + 45));
    const style = v.getUint8(at + 45);
    const category = version === 2 ? cstr(blob.subarray(at + 55, at + 89)) : '';
    if (type >= 10) fields.push(field(name || `bits${i}`, { units, type, style, scale: 1.0, transform: 0.0, digits: 0, category }));
    else fields.push(field(name, { units, type, style, scale: v.getFloat32(at + 46), transform: v.getFloat32(at + 50), digits: v.getInt8(at + 54), category }));
  }
  const offsets: number[] = [];
  let size = 0;
  for (const f of fields) { offsets.push(size); size += SIZES[f.type]!; }
  if (size !== recLen) throw new MlgError(`record length ${recLen} does not match the fields (${size})`);
  const info = infoStart ? cstr(blob.subarray(infoStart, dataBegin)) : '';
  const blockLen = recLen + 5;
  const starts: number[] = [];
  const markerAt: [number, string][] = [];
  let pos = dataBegin;
  while (pos < blob.length) {
    const kind = blob[pos]!;
    if (kind === 0 && pos + blockLen <= blob.length) { starts.push(pos); pos += blockLen; }
    else if (kind === 1 && pos + MARKER_LEN <= blob.length) { markerAt.push([starts.length, cstr(blob.subarray(pos + 4, pos + MARKER_LEN))]); pos += MARKER_LEN; }
    else break;
  }
  const n = starts.length;
  let crcErrors = 0;
  const data: Record<string, Float64Array> = {};
  for (const f of fields) data[f.name] = new Float64Array(n);
  const stamps = new Float64Array(n);
  starts.forEach((b, i) => {
    let sum = 0;
    for (let j = b + 4; j < b + blockLen - 1; j++) sum += blob[j]!;
    if ((sum & 0xff) !== blob[b + blockLen - 1]) crcErrors++;
    stamps[i] = v.getUint16(b + 2);
    fields.forEach((f, k) => {
      data[f.name]![i] = (getValue(v, b + 4 + offsets[k]!, f.type) + f.transform) * f.scale;
    });
  });
  let t: Float64Array;
  if ('Time' in data && n) t = data.Time!.slice();
  else {
    t = new Float64Array(n);
    let acc = 0;
    for (let i = 0; i < n; i++) {
      let step = i === 0 ? 0 : stamps[i]! - stamps[i - 1]!;
      if (step < 0) step += 65536;
      acc += step;
      t[i] = acc * 1e-5;
    }
  }
  const markers: [number, string][] = markerAt.map(([seen, text]) => [n ? t[Math.min(seen, n - 1)]! : 0, text]);
  return { version, timestamp: stamp, info, fields, t, data, markers, crcErrors, recordLength: recLen, dataBegin };
}

/** Import an MLG file as a log with one stream. */
export function readMlg(blob: Uint8Array, path: string | null = null): Log {
  const mlg = parseMlg(blob);
  const fields = mlg.fields.filter((f) => f.name !== 'Time');
  const columns = fields.map((f) => column(f.name, {
    dtype: f.type === U32 || f.type === S32 || f.type === S64 ? 'f8' : 'f4', units: f.units,
    kind: f.style >= 4 ? 'flag' : 'value', decimals: Math.max(0, f.digits),
  }));
  const info = new StreamInfo('mlg', columns, 'import');
  const rows = emptyStreamData(info, mlg.t.length);
  rows.t.set(mlg.t);
  columns.forEach((c, k) => {
    const src = mlg.data[fields[k]!.name]!;
    const dst = rows.columns[c.name]!;
    for (let i = 0; i < src.length; i++) dst[i] = c.dtype === 'f4' ? Math.fround(src[i]!) : src[i]!;
  });
  const meta: Record<string, unknown> = { format: `MLG v${mlg.version}`, imported_from: path ?? '' };
  if (mlg.info) meta.info = mlg.info;
  if (mlg.timestamp) meta.started_unix = mlg.timestamp;
  const events: LogEvent[] = mlg.markers.map(([t, text]) => ({ t, kind: 'marker', text, data: {} }));
  return new Log(meta, [rows], events);
}

