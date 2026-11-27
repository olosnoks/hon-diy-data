/**
 * How the rows of a high-rate stream are packed into one ``chunks.data`` BLOB
 * (port of hondalog/data/codec.py; read that module's text for the layout).
 *
 * ``raw``  the stream's packed little-endian rows as they are.
 * ``d1``   lossless column by column, then zlib: time as the second
 *          difference of the doubles' bit patterns, scaled-integer floats as
 *          integers (offset or zigzag delta), every column byte-shuffled.
 *          Per field one header ``<Bdq`` (mode, scale, base), then the payloads.
 *
 * Decoding is byte-exact with the Python codec. Encoding makes the same
 * transforms; the zlib layer is written as stored blocks (see zlib.ts).
 */

import { DTYPES, type Dtype, type StreamData, type StreamInfo, emptyStreamData, isFloatDtype, packRows, readValue, unpackRows } from './model';
import { deflate, inflate } from './zlib';

export const RAW = 'raw';
export const PACKED = 'd1';
export const CODECS = [RAW, PACKED];

const AS_IS = 0, DELTA32 = 1, DELTA64 = 2, TIME = 3, OFFSET32 = 4;
const SCALES = [1.0, 2.0, 10.0, 100.0, 1000.0, 10000.0];
const HEAD = 17;                         // struct "<Bdq"
const I32_MAX = 2 ** 31 - 1;
const M64 = (1n << 64n) - 1n;

function shuffle(bytes: Uint8Array, size: number, n: number): Uint8Array {
  if (size === 1 || n === 0) return bytes.slice();
  const out = new Uint8Array(n * size);
  for (let i = 0; i < n; i++) for (let b = 0; b < size; b++) out[b * n + i] = bytes[i * size + b]!;
  return out;
}

function unshuffle(data: Uint8Array, size: number, n: number): Uint8Array {
  if (size === 1 || n === 0) return data.slice(0, n * size);
  const out = new Uint8Array(n * size);
  for (let i = 0; i < n; i++) for (let b = 0; b < size; b++) out[i * size + b] = data[b * n + i]!;
  return out;
}

function zigzag(x: bigint): bigint {
  return BigInt.asUintN(64, (x << 1n) ^ (x >> 63n));
}

function unzigzag(u: bigint): bigint {
  return BigInt.asIntN(64, (u >> 1n) ^ -(u & 1n));
}

/** numpy ``rint``: round half to even. */
function rint(x: number): number {
  const r = Math.round(x);
  if (Math.abs(x - Math.trunc(x)) === 0.5) return 2 * Math.round(x / 2);
  return r;
}

function u32bytes(values: ArrayLike<number>): Uint8Array {
  const out = new Uint8Array(values.length * 4);
  const view = new DataView(out.buffer);
  for (let i = 0; i < values.length; i++) view.setUint32(i * 4, values[i]! >>> 0, true);
  return out;
}

function u64bytes(values: readonly bigint[]): Uint8Array {
  const out = new Uint8Array(values.length * 8);
  const view = new DataView(out.buffer);
  for (let i = 0; i < values.length; i++) view.setBigUint64(i * 8, values[i]! & M64, true);
  return out;
}

/** (mode, base, payload) for an integer column (values exact as doubles). */
function encodeInts(ints: readonly number[]): [number, number, Uint8Array] {
  const n = ints.length;
  let base = Infinity;
  for (const v of ints) if (v < base) base = v;
  const delta = ints.map((v, i) => (i === 0 ? v : v - ints[i - 1]!));
  let big = 0, offMax = 0, offSum = 0, dSum = 0;
  for (let i = 0; i < n; i++) {
    big = Math.max(big, Math.abs(delta[i]!));
    const off = ints[i]! - base;
    offMax = Math.max(offMax, off);
    offSum += off;
    if (i > 0) dSum += Math.abs(delta[i]!);
  }
  if (offMax <= I32_MAX && offSum < 2 * dSum) {
    return [OFFSET32, base, shuffle(u32bytes(ints.map((v) => v - base)), 4, n)];
  }
  if (big < 2 ** 30) {
    return [DELTA32, 0, shuffle(u32bytes(delta.map((d) => Number(zigzag(BigInt(d)) & 0xffffffffn))), 4, n)];
  }
  return [DELTA64, 0, shuffle(u64bytes(delta.map((d) => zigzag(BigInt(d)))), 8, n)];
}

function fieldBytes(data: StreamData, name: string, dtype: Dtype): Uint8Array {
  const n = data.t.length;
  const size = DTYPES[dtype];
  const out = new Uint8Array(n * size);
  const view = new DataView(out.buffer);
  const values = name === 't' ? data.t : data.columns[name]!;
  for (let i = 0; i < n; i++) {
    let v = values[i]!;
    switch (dtype) {
      case 'f4': view.setFloat32(i * size, v, true); break;
      case 'f8': view.setFloat64(i * size, v, true); break;
      default: {
        if (v !== v) v = 0;
        const tmp = new DataView(new ArrayBuffer(8));
        switch (dtype) {
          case 'u1': tmp.setUint8(0, v); break;
          case 'i1': tmp.setInt8(0, v); break;
          case 'u2': tmp.setUint16(0, v, true); break;
          case 'i2': tmp.setInt16(0, v, true); break;
          case 'u4': tmp.setUint32(0, v, true); break;
          case 'i4': tmp.setInt32(0, v, true); break;
          case 'u8': tmp.setBigUint64(0, BigInt(Math.trunc(v)), true); break;
          case 'i8': tmp.setBigInt64(0, BigInt(Math.trunc(v)), true); break;
        }
        out.set(new Uint8Array(tmp.buffer, 0, size), i * size);
      }
    }
  }
  return out;
}

function encodeField(data: StreamData, name: string, dtype: Dtype, raw: boolean): [number, number, number, Uint8Array] {
  const n = data.t.length;
  const w = data.info.rawLen;
  if (raw) {                                      // (rows, raw_len) -> transposed bytes
    const out = new Uint8Array(n * w);
    for (let i = 0; i < n; i++) for (let b = 0; b < w; b++) out[b * n + i] = data.raw[i * w + b]!;
    return [AS_IS, 0, 0, out];
  }
  if (n === 0) return [AS_IS, 0, 0, new Uint8Array(0)];
  if (name === 't') {
    const bits = new DataView(fieldBytes(data, 't', 'f8').buffer);
    const second: bigint[] = [];
    let prevBits = 0n, prevD = 0n;
    for (let i = 0; i < n; i++) {
      const b = bits.getBigInt64(i * 8, true);
      const d = BigInt.asIntN(64, b - prevBits);
      second.push(zigzag(BigInt.asIntN(64, d - prevD)));
      prevBits = b;
      prevD = d;
    }
    return [TIME, 0, 0, shuffle(u64bytes(second), 8, n)];
  }
  const col = data.columns[name]!;
  const size = DTYPES[dtype];
  if (!isFloatDtype(dtype) && size < 8) {
    const ints = Array.from(col, (v) => (v !== v ? 0 : Math.trunc(v)));
    const [mode, base, payload] = encodeInts(ints);
    return [mode, 1.0, base, payload];
  }
  if (isFloatDtype(dtype)) {
    let finite = true;
    for (let i = 0; i < n; i++) if (!Number.isFinite(col[i]!)) { finite = false; break; }
    if (finite) {
      for (const scale of SCALES) {
        const q = Array.from(col, (v) => rint(v * scale));
        let maxAbs = 0;
        for (const x of q) maxAbs = Math.max(maxAbs, Math.abs(x));
        if (maxAbs >= 2 ** 52) break;
        let equal = true;
        for (let i = 0; i < n; i++) {
          const back = dtype === 'f4' ? Math.fround(q[i]! / scale) : q[i]! / scale;
          if (back !== col[i]) { equal = false; break; }
        }
        if (equal) {
          const [mode, base, payload] = encodeInts(q);
          return [mode, scale, base, payload];
        }
      }
    }
  }
  return [AS_IS, 0, 0, shuffle(fieldBytes(data, name, dtype), size, n)];
}

/** Pack rows (column-wise data, sorted by time) with ``codec``. */
export function encode(data: StreamData, codec: string = PACKED): Uint8Array {
  if (codec === RAW) return packRows(data);
  if (codec !== PACKED) throw new Error(`unknown chunk codec ${JSON.stringify(codec)}`);
  const fields = data.info.fields;
  const heads = new Uint8Array(HEAD * fields.length);
  const hv = new DataView(heads.buffer);
  const payloads: Uint8Array[] = [];
  fields.forEach((f, k) => {
    const [mode, scale, base, payload] = encodeField(data, f.name, f.dtype, !!f.raw);
    hv.setUint8(k * HEAD, mode);
    hv.setFloat64(k * HEAD + 1, scale, true);
    hv.setBigInt64(k * HEAD + 9, BigInt(base), true);
    payloads.push(payload);
  });
  const total = heads.length + payloads.reduce((s, p) => s + p.length, 0);
  const blob = new Uint8Array(total);
  blob.set(heads, 0);
  let at = heads.length;
  for (const p of payloads) {
    blob.set(p, at);
    at += p.length;
  }
  return deflate(blob);
}

/** The rows of one chunk. */
export function decode(codec: string, data: Uint8Array, info: StreamInfo, nRows: number): StreamData {
  if (codec === RAW) return unpackRows(info, data, nRows);
  if (codec !== PACKED) throw new Error(`unknown chunk codec ${JSON.stringify(codec)}; this log was written by a newer hondalog`);
  const blob = inflate(data);
  const bv = new DataView(blob.buffer, blob.byteOffset, blob.byteLength);
  const out = emptyStreamData(info, nRows);
  const fields = info.fields;
  let at = HEAD * fields.length;
  const n = nRows;
  fields.forEach((f, k) => {
    const mode = bv.getUint8(k * HEAD);
    const scale = bv.getFloat64(k * HEAD + 1, true);
    const base = bv.getBigInt64(k * HEAD + 9, true);
    if (f.raw) {
      const w = f.size;
      for (let i = 0; i < n; i++) for (let b = 0; b < w; b++) out.raw[i * w + b] = blob[at + b * n + i]!;
      at += n * w;
      return;
    }
    const target = f.name === 't' ? out.t : out.columns[f.name]!;
    const size = DTYPES[f.dtype];
    if (mode === AS_IS) {
      const bytes = unshuffle(blob.subarray(at, at + n * size), size, n);
      const view = new DataView(bytes.buffer);
      for (let i = 0; i < n; i++) target[i] = readValue(view, i * size, f.dtype);
      at += n * size;
    } else if (mode === TIME) {
      const bytes = unshuffle(blob.subarray(at, at + n * 8), 8, n);
      const view = new DataView(bytes.buffer);
      const tv = new DataView(new ArrayBuffer(8));
      let d = 0n, bits = 0n;
      for (let i = 0; i < n; i++) {
        d = BigInt.asIntN(64, d + unzigzag(view.getBigUint64(i * 8, true)));
        bits = BigInt.asIntN(64, bits + d);
        tv.setBigInt64(0, bits, true);
        target[i] = tv.getFloat64(0, true);
      }
      at += n * 8;
    } else if (mode === DELTA32 || mode === DELTA64 || mode === OFFSET32) {
      const item = mode === DELTA64 ? 8 : 4;
      const bytes = unshuffle(blob.subarray(at, at + n * item), item, n);
      const view = new DataView(bytes.buffer);
      let sum = 0n;
      for (let i = 0; i < n; i++) {
        const stored = item === 8 ? view.getBigUint64(i * 8, true) : BigInt(view.getUint32(i * 4, true));
        let v: bigint;
        if (mode === OFFSET32) v = stored + base;
        else {
          sum = BigInt.asIntN(64, sum + unzigzag(stored));
          v = sum;
        }
        const num = Number(v);
        if (isFloatDtype(f.dtype)) target[i] = f.dtype === 'f4' ? Math.fround(num / scale) : num / scale;
        else target[i] = num;
      }
      at += n * item;
    } else {
      throw new Error(`damaged chunk: unknown column mode ${mode}`);
    }
  });
  return out;
}
