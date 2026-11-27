/**
 * Byte helpers for the CYD link: a little-endian subset of Python's ``struct``
 * (so the layouts below read like the Python they were ported from), CRC-32
 * (zlib), CRC-16/CCITT (binascii.crc_hqx), strict base64, UTF-8 fields.
 */

export type StructValue = number | bigint | Uint8Array;

interface Item { code: string; count: number; size: number }

const SIZES: Record<string, number> = { x: 1, B: 1, b: 1, H: 2, h: 2, I: 4, i: 4, Q: 8, q: 8, f: 4, d: 8, s: 1 };

/** ``struct.Struct("<...")`` for the codes x B b H h I i Q q f d s. Always little-endian, packed. */
export class Struct {
  readonly size: number;
  private readonly items: Item[] = [];

  constructor(readonly format: string) {
    const fmt = format.replace(/^[<=]/, '');
    const re = /(\d*)([xBbHhIiQqfds])/g;
    let m: RegExpExecArray | null;
    let size = 0;
    let used = 0;
    while ((m = re.exec(fmt)) !== null) {
      used += m[0].length;
      const count = m[1] ? parseInt(m[1], 10) : 1;
      const code = m[2]!;
      if (code === 's' || code === 'x') {
        this.items.push({ code, count, size: count });
        size += count;
      } else {
        for (let i = 0; i < count; i++) this.items.push({ code, count: 1, size: SIZES[code]! });
        size += SIZES[code]! * count;
      }
    }
    if (used !== fmt.length) throw new Error(`bad struct format ${format}`);
    this.size = size;
  }

  /** Values for every code but ``x``; ``Ns`` takes bytes (cut or NUL padded). Integers wrap like C. */
  pack(...values: StructValue[]): Uint8Array {
    const out = new Uint8Array(this.size);
    this.packInto(out, 0, ...values);
    return out;
  }

  packInto(out: Uint8Array, offset: number, ...values: StructValue[]): void {
    const dv = new DataView(out.buffer, out.byteOffset, out.byteLength);
    let pos = offset;
    let vi = 0;
    for (const it of this.items) {
      if (it.code === 'x') { pos += it.size; continue; }
      const v = values[vi++];
      if (v === undefined) throw new Error(`struct ${this.format}: too few values`);
      switch (it.code) {
        case 's': {
          const b = v as Uint8Array;
          out.set(b.subarray(0, it.count), pos);
          break;
        }
        case 'B': dv.setUint8(pos, Number(v) & 0xff); break;
        case 'b': dv.setInt8(pos, Number(v)); break;
        case 'H': dv.setUint16(pos, Number(v) & 0xffff, true); break;
        case 'h': dv.setInt16(pos, Number(v), true); break;
        case 'I': dv.setUint32(pos, Number(BigInt.asUintN(32, toBig(v))), true); break;
        case 'i': dv.setInt32(pos, Number(BigInt.asIntN(32, toBig(v))), true); break;
        case 'Q': dv.setBigUint64(pos, BigInt.asUintN(64, toBig(v)), true); break;
        case 'q': dv.setBigInt64(pos, BigInt.asIntN(64, toBig(v)), true); break;
        case 'f': dv.setFloat32(pos, Number(v), true); break;
        case 'd': dv.setFloat64(pos, Number(v), true); break;
      }
      pos += it.size;
    }
    if (vi !== values.length) throw new Error(`struct ${this.format}: ${values.length} values for ${vi} fields`);
  }

  /** Numbers (Q/q as numbers: exact below 2**53) and byte strings for ``s``. */
  unpack(buf: Uint8Array, offset = 0): any[] {
    if (buf.length - offset < this.size) throw new Error(`struct ${this.format}: needs ${this.size} bytes`);
    const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
    const out: any[] = [];
    let pos = offset;
    for (const it of this.items) {
      switch (it.code) {
        case 'x': break;
        case 's': out.push(buf.slice(pos, pos + it.count)); break;
        case 'B': out.push(dv.getUint8(pos)); break;
        case 'b': out.push(dv.getInt8(pos)); break;
        case 'H': out.push(dv.getUint16(pos, true)); break;
        case 'h': out.push(dv.getInt16(pos, true)); break;
        case 'I': out.push(dv.getUint32(pos, true)); break;
        case 'i': out.push(dv.getInt32(pos, true)); break;
        case 'Q': out.push(Number(dv.getBigUint64(pos, true))); break;
        case 'q': out.push(Number(dv.getBigInt64(pos, true))); break;
        case 'f': out.push(dv.getFloat32(pos, true)); break;
        case 'd': out.push(dv.getFloat64(pos, true)); break;
      }
      pos += it.size;
    }
    return out;
  }

  /** ``iter_unpack`` over whole records only. */
  *iter(buf: Uint8Array): Generator<any[]> {
    for (let pos = 0; pos + this.size <= buf.length; pos += this.size) yield this.unpack(buf, pos);
  }
}

function toBig(v: StructValue): bigint {
  if (typeof v === 'bigint') return v;
  const n = Number(v);
  return BigInt(Number.isFinite(n) ? Math.trunc(n) : 0);
}

// ---------------------------------------------------------------- CRCs

const CRC32_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

/** zlib.crc32(data, crc) & 0xFFFFFFFF */
export function crc32(data: Uint8Array, crc = 0): number {
  let c = (crc ^ 0xffffffff) >>> 0;
  for (let i = 0; i < data.length; i++) c = CRC32_TABLE[(c ^ data[i]!) & 0xff]! ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

const CRC16_TABLE = (() => {
  const t = new Uint16Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n << 8;
    for (let k = 0; k < 8; k++) c = c & 0x8000 ? ((c << 1) ^ 0x1021) & 0xffff : (c << 1) & 0xffff;
    t[n] = c;
  }
  return t;
})();

/** binascii.crc_hqx(data, 0xFFFF): CRC-16/CCITT-FALSE. */
export function crc16(data: Uint8Array, crc = 0xffff): number {
  let c = crc & 0xffff;
  for (let i = 0; i < data.length; i++) c = ((c << 8) & 0xffff) ^ CRC16_TABLE[((c >> 8) ^ data[i]!) & 0xff]!;
  return c;
}

// ---------------------------------------------------------------- text and bytes

const enc = new TextEncoder();
const dec = new TextDecoder('utf-8');

export function utf8(text: string): Uint8Array {
  return enc.encode(text);
}

export function fromUtf8(raw: Uint8Array): string {
  return dec.decode(raw);
}

/** ASCII with '?' for anything else (Python ``encode("ascii", "replace")``). */
export function ascii(text: string): Uint8Array {
  const out = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    out[i] = c < 0x80 ? c : 0x3f;
  }
  return out;
}

/** A C string field: up to the first NUL, UTF-8 with replacement. */
export function cstr(raw: Uint8Array): string {
  const end = raw.indexOf(0);
  return dec.decode(end < 0 ? raw : raw.subarray(0, end));
}

/** UTF-8 for a char[size] field: cut on a character boundary, NUL padded (format.fit). */
export function fit(text: string, size: number): Uint8Array {
  let chars = Array.from(text);
  let raw = enc.encode(text);
  while (raw.length > size - 1) {
    chars = chars.slice(0, -1);
    raw = enc.encode(chars.join(''));
  }
  const out = new Uint8Array(size);
  out.set(raw);
  return out;
}

export function concat(...parts: Uint8Array[]): Uint8Array {
  let n = 0;
  for (const p of parts) n += p.length;
  const out = new Uint8Array(n);
  let pos = 0;
  for (const p of parts) { out.set(p, pos); pos += p.length; }
  return out;
}

export function equalBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/** bytes.find(needle, start) */
export function findBytes(hay: Uint8Array, needle: Uint8Array, start = 0): number {
  if (needle.length === 0) return start <= hay.length ? start : -1;
  const first = needle[0];
  const last = hay.length - needle.length;
  outer: for (let i = Math.max(0, start); i <= last; i++) {
    if (hay[i] !== first) continue;
    for (let j = 1; j < needle.length; j++) if (hay[i + j] !== needle[j]) continue outer;
    return i;
  }
  return -1;
}

export function rfindBytes(hay: Uint8Array, needle: Uint8Array): number {
  for (let i = hay.length - needle.length; i >= 0; i--) {
    let ok = true;
    for (let j = 0; j < needle.length; j++) if (hay[i + j] !== needle[j]) { ok = false; break; }
    if (ok) return i;
  }
  return -1;
}

export function hex(data: Uint8Array): string {
  let s = '';
  for (const b of data) s += b.toString(16).padStart(2, '0');
  return s;
}

export function fromHex(text: string): Uint8Array {
  const clean = text.replace(/\s+/g, '');
  const out = new Uint8Array(clean.length >> 1);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(clean.substr(i * 2, 2), 16);
  return out;
}

/** A growable byte buffer (Python ``bytearray`` as a queue). */
export class ByteBuf {
  private buf = new Uint8Array(256);
  private start = 0;
  private end = 0;

  get length(): number { return this.end - this.start; }

  push(data: Uint8Array): void {
    if (this.end + data.length > this.buf.length) {
      const live = this.end - this.start;
      const cap = Math.max(this.buf.length, (live + data.length) * 2);
      const nb = new Uint8Array(cap);
      nb.set(this.buf.subarray(this.start, this.end));
      this.buf = nb;
      this.start = 0;
      this.end = live;
    }
    this.buf.set(data, this.end);
    this.end += data.length;
  }

  pushByte(b: number): void {
    this.push(Uint8Array.of(b));
  }

  /** Take everything. */
  take(): Uint8Array {
    const out = this.buf.slice(this.start, this.end);
    this.start = this.end = 0;
    return out;
  }

  /** Take up to n bytes from the front. */
  shift(n: number): Uint8Array {
    const k = Math.min(n, this.length);
    const out = this.buf.slice(this.start, this.start + k);
    this.start += k;
    if (this.start === this.end) this.start = this.end = 0;
    return out;
  }

  view(): Uint8Array {
    return this.buf.subarray(this.start, this.end);
  }

  clear(): void {
    this.start = this.end = 0;
  }
}

// ---------------------------------------------------------------- base64

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
const B64_INV = (() => {
  const t = new Int16Array(256).fill(-1);
  for (let i = 0; i < 64; i++) t[B64.charCodeAt(i)] = i;
  return t;
})();

export function b64(data: Uint8Array): string {
  let out = '';
  let i = 0;
  for (; i + 2 < data.length; i += 3) {
    const n = (data[i]! << 16) | (data[i + 1]! << 8) | data[i + 2]!;
    out += B64[n >> 18]! + B64[(n >> 12) & 63]! + B64[(n >> 6) & 63]! + B64[n & 63]!;
  }
  if (i < data.length) {
    const n = (data[i]! << 16) | ((data[i + 1] ?? 0) << 8);
    out += B64[n >> 18]! + B64[(n >> 12) & 63]! + (i + 1 < data.length ? B64[(n >> 6) & 63]! : '=') + '=';
  }
  return out;
}

/** Strict base64 (hl_proto.unb64): null for anything but whole, valid quads. */
export function unb64(text: string): Uint8Array | null {
  if (text.length % 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(text)) return null;
  const pad = text.endsWith('==') ? 2 : text.endsWith('=') ? 1 : 0;
  const n = (text.length / 4) * 3 - pad;
  const out = new Uint8Array(n);
  let o = 0;
  for (let i = 0; i < text.length; i += 4) {
    const a = B64_INV[text.charCodeAt(i)]!, b = B64_INV[text.charCodeAt(i + 1)]!;
    const c = text[i + 2] === '=' ? 0 : B64_INV[text.charCodeAt(i + 2)]!;
    const d = text[i + 3] === '=' ? 0 : B64_INV[text.charCodeAt(i + 3)]!;
    if (a < 0 || b < 0 || c < 0 || d < 0) return null;
    if (text[i + 2] === '=' && text[i + 3] !== '=') return null;
    if ((text[i + 2] === '=' || text[i + 3] === '=') && i + 4 !== text.length) return null;
    const v = (a << 18) | (b << 12) | (c << 6) | d;
    if (o < n) out[o++] = v >> 16;
    if (o < n) out[o++] = (v >> 8) & 0xff;
    if (o < n) out[o++] = v & 0xff;
  }
  return out;
}

export function hex8(n: number): string {
  return (n >>> 0).toString(16).toUpperCase().padStart(8, '0');
}
