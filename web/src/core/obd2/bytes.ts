/** Small byte helpers shared by obd2/ and diag/ (no I/O). */

export type Bytes = Uint8Array;

export function u8(...values: number[]): Uint8Array {
  return Uint8Array.from(values.map((v) => v & 0xff));
}

/** Parse '20 05 00 10', '20,05,0x00,10' or '20050010' (Python protocol.parse_hex). */
export function fromHex(text: string): Uint8Array {
  const cleaned = text.replace(/,/g, ' ').replace(/0x/gi, ' ');
  const out: number[] = [];
  for (let token of cleaned.split(/\s+/).filter(Boolean)) {
    if (token.length % 2) token = '0' + token;
    for (let i = 0; i < token.length; i += 2) {
      const v = parseInt(token.slice(i, i + 2), 16);
      if (Number.isNaN(v)) throw new Error(`not hex: ${token}`);
      out.push(v);
    }
  }
  return Uint8Array.from(out);
}

/** 'AB CD' upper case, space separated (Python protocol.fmt_hex). */
export function fmtHex(data: ArrayLike<number>): string {
  return Array.from(data, (b) => hex2(b)).join(' ');
}

/** 'ab cd' lower case (Python bytes.hex(' ')). */
export function hexLower(data: ArrayLike<number>): string {
  return Array.from(data, (b) => (b & 0xff).toString(16).padStart(2, '0')).join(' ');
}

export function hex2(b: number): string {
  return (b & 0xff).toString(16).toUpperCase().padStart(2, '0');
}

export function concat(...parts: ArrayLike<number>[]): Uint8Array {
  let n = 0;
  for (const p of parts) n += p.length;
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

export function equal(a: ArrayLike<number>, b: ArrayLike<number>): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

export function startsWith(buf: ArrayLike<number>, prefix: ArrayLike<number>): boolean {
  if (prefix.length > buf.length) return false;
  for (let i = 0; i < prefix.length; i++) if (buf[i] !== prefix[i]) return false;
  return true;
}

/** Index of ``needle`` in ``buf`` from ``from``, or -1 (bytes.find). */
export function indexOf(buf: ArrayLike<number>, needle: ArrayLike<number>, from = 0): number {
  if (needle.length === 0) return from <= buf.length ? from : -1;
  outer: for (let i = Math.max(0, from); i + needle.length <= buf.length; i++) {
    for (let j = 0; j < needle.length; j++) if (buf[i + j] !== needle[j]) continue outer;
    return i;
  }
  return -1;
}

export function sum(data: ArrayLike<number>, start = 0, end = data.length): number {
  let s = 0;
  for (let i = start; i < end; i++) s += data[i]!;
  return s;
}

/** Printable ASCII, other bytes as '.'. */
export function ascii(data: ArrayLike<number>): string {
  return Array.from(data, (b) => (b >= 32 && b < 127 ? String.fromCharCode(b) : '.')).join('');
}

/** Growable byte buffer (bytearray). */
export class ByteBuf {
  private buf = new Uint8Array(64);
  length = 0;

  push(data: ArrayLike<number>): void {
    if (this.length + data.length > this.buf.length) {
      const next = new Uint8Array(Math.max(this.buf.length * 2, this.length + data.length));
      next.set(this.buf.subarray(0, this.length));
      this.buf = next;
    }
    this.buf.set(data, this.length);
    this.length += data.length;
  }

  bytes(): Uint8Array {
    return this.buf.slice(0, this.length);
  }

  at(i: number): number {
    return this.buf[i]!;
  }
}
