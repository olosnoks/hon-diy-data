/** Hex helpers for the console and the grid (hondalog/protocol.py). */

export const HEX = Array.from({ length: 256 }, (_, i) => i.toString(16).toUpperCase().padStart(2, '0'));

export function hex2(v: number): string {
  return v >= 0 && v <= 255 ? HEX[v]! : '--';
}

export function addrText(a: number): string {
  return `0x${hex2(a)}`;
}

/** '20 05 00 10', '20,05,0x00,10' or '20050010' -> bytes. Throws on bad hex. */
export function parseHex(text: string): Uint8Array {
  const cleaned = text.replace(/,/g, ' ').replace(/0x/g, ' ').replace(/0X/g, ' ');
  const out: number[] = [];
  for (let token of cleaned.split(/\s+/).filter(Boolean)) {
    if (token.length % 2) token = '0' + token;
    if (!/^[0-9a-fA-F]+$/.test(token)) throw new Error(`not hex: ${token}`);
    for (let i = 0; i < token.length; i += 2) out.push(parseInt(token.slice(i, i + 2), 16));
  }
  return Uint8Array.from(out);
}

export function fmtHex(data: ArrayLike<number>): string {
  return Array.from(data, (b) => hex2(b)).join(' ');
}

/** Byte that makes data + checksum sum to 0 modulo 256. */
export function checksum(data: ArrayLike<number>): number {
  let s = 0;
  for (let i = 0; i < data.length; i++) s += data[i]!;
  return (-s) & 0xff;
}

export function withChecksum(data: ArrayLike<number>): Uint8Array {
  return Uint8Array.from([...Array.from(data), checksum(data)]);
}

export function verifyFrame(frame: ArrayLike<number>): boolean {
  let s = 0;
  for (let i = 0; i < frame.length; i++) s += frame[i]!;
  return frame.length > 0 && (s & 0xff) === 0;
}

/** A checksum-valid frame of unknown length (protocol.find_any_frame). */
export function findAnyFrame(rest: ArrayLike<number>, replyHeader = 0x00, replyLenExtra = 3): Uint8Array | null {
  for (let i = 0; i < rest.length - 2; i++) {
    if (replyHeader >= 0 && rest[i] !== replyHeader) continue;
    const size = rest[i + 1]! - replyLenExtra + 3;
    if (size >= 3 && i + size <= rest.length) {
      const f = Array.from(rest).slice(i, i + size);
      if (verifyFrame(f)) return Uint8Array.from(f);
    }
  }
  return null;
}

/** The read-only guard (W1): with the expert unlock off, only Honda read
 *  requests (cmd 0x20, length byte 0x05, addr, len, checksum) go out. */
export function isReadRequest(frame: ArrayLike<number>, cmdRead = 0x20): boolean {
  if (frame.length !== 5 && frame.length !== 4) return false;
  if (frame[0] !== cmdRead || frame[1] !== 0x05) return false;
  const len = frame[3]!;
  return len >= 1 && len <= 16;
}
