/**
 * zlib (RFC 1950/1951) without a dependency and without waiting: the ``d1``
 * chunk codec is applied inside a database transaction, so it has to be
 * synchronous (CompressionStream is not).
 *
 * ``inflate`` reads anything zlib writes (stored, fixed and dynamic Huffman
 * blocks): Python's ``zlib.compress``. ``deflate`` writes stored blocks only
 * (valid zlib, no compression): the d1 transforms already do most of the
 * shrinking for black box rows, and Python reads these blocks as they are.
 */

export function adler32(data: Uint8Array): number {
  let a = 1;
  let b = 0;
  const n = data.length;
  let i = 0;
  while (i < n) {
    const end = Math.min(n, i + 3800);
    for (; i < end; i++) {
      a += data[i]!;
      b += a;
    }
    a %= 65521;
    b %= 65521;
  }
  return ((b << 16) | a) >>> 0;
}

/** zlib stream made of stored blocks. */
export function deflate(data: Uint8Array): Uint8Array {
  const blocks = Math.max(1, Math.ceil(data.length / 65535));
  const out = new Uint8Array(2 + data.length + blocks * 5 + 4);
  out[0] = 0x78;
  out[1] = 0x01;                      // CMF/FLG: deflate, 32K window, fastest; (0x7801 % 31 == 0)
  let at = 2;
  for (let k = 0; k < blocks; k++) {
    const lo = k * 65535;
    const len = Math.min(65535, data.length - lo);
    out[at++] = k === blocks - 1 ? 1 : 0;
    out[at++] = len & 0xff;
    out[at++] = len >>> 8;
    out[at++] = ~len & 0xff;
    out[at++] = (~len >>> 8) & 0xff;
    out.set(data.subarray(lo, lo + len), at);
    at += len;
  }
  const sum = adler32(data);
  out[at++] = sum >>> 24;
  out[at++] = (sum >>> 16) & 0xff;
  out[at++] = (sum >>> 8) & 0xff;
  out[at++] = sum & 0xff;
  return out;
}

// ---- inflate ----------------------------------------------------------------

interface Tree {
  counts: Uint16Array;     // number of codes of each length
  symbols: Uint16Array;    // symbols ordered by code
}

const LEN_BASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258];
const LEN_EXTRA = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0];
const DIST_BASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073,
  4097, 6145, 8193, 12289, 16385, 24577];
const DIST_EXTRA = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13];
const CL_ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15];

function buildTree(lengths: ArrayLike<number>, n: number): Tree {
  const counts = new Uint16Array(16);
  const symbols = new Uint16Array(n);
  for (let i = 0; i < n; i++) counts[lengths[i]!]!++;
  counts[0] = 0;
  const offs = new Uint16Array(16);
  for (let i = 1; i < 16; i++) offs[i] = offs[i - 1]! + counts[i - 1]!;
  for (let i = 0; i < n; i++) if (lengths[i]) symbols[offs[lengths[i]!]!++] = i;
  return { counts, symbols };
}

let fixedLit: Tree | null = null;
let fixedDist: Tree | null = null;

function fixedTrees(): [Tree, Tree] {
  if (!fixedLit || !fixedDist) {
    const l = new Uint8Array(288);
    l.fill(8, 0, 144);
    l.fill(9, 144, 256);
    l.fill(7, 256, 280);
    l.fill(8, 280, 288);
    fixedLit = buildTree(l, 288);
    fixedDist = buildTree(new Uint8Array(30).fill(5), 30);
  }
  return [fixedLit, fixedDist];
}

class BitReader {
  pos = 0;
  bit = 0;
  bits = 0;
  constructor(readonly src: Uint8Array) {}

  need(n: number): number {
    while (this.bits < n) {
      if (this.pos >= this.src.length) throw new Error('inflate: unexpected end of data');
      this.bit |= this.src[this.pos++]! << this.bits;
      this.bits += 8;
    }
    const v = this.bit & ((1 << n) - 1);
    this.bit >>>= n;
    this.bits -= n;
    return v;
  }

  decode(t: Tree): number {
    let code = 0;
    let first = 0;
    let index = 0;
    for (let len = 1; len < 16; len++) {
      code |= this.need(1);
      const count = t.counts[len]!;
      if (code - count < first) return t.symbols[index + (code - first)]!;
      index += count;
      first += count;
      first <<= 1;
      code <<= 1;
    }
    throw new Error('inflate: bad Huffman code');
  }

  align(): void {
    this.bit = 0;
    this.bits = 0;
  }
}

class Output {
  buf: Uint8Array;
  n = 0;
  constructor(size: number) {
    this.buf = new Uint8Array(Math.max(size, 1024));
  }
  ensure(extra: number): void {
    if (this.n + extra <= this.buf.length) return;
    const next = new Uint8Array(Math.max(this.buf.length * 2, this.n + extra));
    next.set(this.buf.subarray(0, this.n));
    this.buf = next;
  }
}

/** Decompress a zlib stream (``zlib.decompress``). */
export function inflate(data: Uint8Array, sizeHint = 0): Uint8Array {
  if (data.length < 2 || (data[0]! & 0x0f) !== 8 || ((data[0]! << 8) | data[1]!) % 31 !== 0) {
    throw new Error('inflate: not a zlib stream');
  }
  if (data[1]! & 0x20) throw new Error('inflate: preset dictionaries are not supported');
  const r = new BitReader(data);
  r.pos = 2;
  const out = new Output(sizeHint || data.length * 4);
  let last = 0;
  while (!last) {
    last = r.need(1);
    const type = r.need(2);
    if (type === 0) {
      r.align();
      if (r.pos + 4 > data.length) throw new Error('inflate: unexpected end of data');
      const len = data[r.pos]! | (data[r.pos + 1]! << 8);
      const nlen = data[r.pos + 2]! | (data[r.pos + 3]! << 8);
      if ((len ^ 0xffff) !== nlen) throw new Error('inflate: damaged stored block');
      r.pos += 4;
      if (r.pos + len > data.length) throw new Error('inflate: unexpected end of data');
      out.ensure(len);
      out.buf.set(data.subarray(r.pos, r.pos + len), out.n);
      out.n += len;
      r.pos += len;
      continue;
    }
    let lit: Tree;
    let dist: Tree;
    if (type === 1) {
      [lit, dist] = fixedTrees();
    } else if (type === 2) {
      const hlit = r.need(5) + 257;
      const hdist = r.need(5) + 1;
      const hclen = r.need(4) + 4;
      const cl = new Uint8Array(19);
      for (let i = 0; i < hclen; i++) cl[CL_ORDER[i]!] = r.need(3);
      const clTree = buildTree(cl, 19);
      const lengths = new Uint8Array(hlit + hdist);
      for (let i = 0; i < hlit + hdist;) {
        const sym = r.decode(clTree);
        if (sym < 16) {
          lengths[i++] = sym;
        } else {
          let prev = 0;
          let rep: number;
          if (sym === 16) {
            if (i === 0) throw new Error('inflate: bad code lengths');
            prev = lengths[i - 1]!;
            rep = 3 + r.need(2);
          } else if (sym === 17) {
            rep = 3 + r.need(3);
          } else {
            rep = 11 + r.need(7);
          }
          if (i + rep > hlit + hdist) throw new Error('inflate: bad code lengths');
          lengths.fill(prev, i, i + rep);
          i += rep;
        }
      }
      lit = buildTree(lengths.subarray(0, hlit), hlit);
      dist = buildTree(lengths.subarray(hlit), hdist);
    } else {
      throw new Error('inflate: bad block type');
    }
    for (;;) {
      const sym = r.decode(lit);
      if (sym < 256) {
        out.ensure(1);
        out.buf[out.n++] = sym;
      } else if (sym === 256) {
        break;
      } else {
        const li = sym - 257;
        if (li >= 29) throw new Error('inflate: bad length code');
        const len = LEN_BASE[li]! + r.need(LEN_EXTRA[li]!);
        const di = r.decode(dist);
        if (di >= 30) throw new Error('inflate: bad distance code');
        const d = DIST_BASE[di]! + r.need(DIST_EXTRA[di]!);
        if (d > out.n) throw new Error('inflate: distance too far back');
        out.ensure(len);
        const b = out.buf;
        for (let k = 0; k < len; k++) {
          b[out.n] = b[out.n - d]!;
          out.n++;
        }
      }
    }
  }
  const result = out.buf.slice(0, out.n);
  r.align();
  if (r.pos + 4 <= data.length) {
    const want = ((data[r.pos]! << 24) | (data[r.pos + 1]! << 16) | (data[r.pos + 2]! << 8) | data[r.pos + 3]!) >>> 0;
    if (want !== adler32(result)) throw new Error('inflate: checksum mismatch');
  }
  return result;
}
