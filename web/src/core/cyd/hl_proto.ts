/**
 * The handheld's ``HL`` text session (firmware/handheld/SERIAL.md, "The HL session"),
 * ported from hondalog/handheld/hl_proto.py: the byte-stream splitter (frames and text
 * lines on one cable), the path rules, and constants. ``HL HELLO`` pauses the binary
 * stream; lines follow until ``HL BYE`` or 30 s without a command:
 *
 *     HL HELLO                 -> HL HELLO fw= model= proto=1 id= map= variant= chunk= win=
 *     HL LS <dir>              -> HL F <size> <mtime> <path> | HL D <path> ... HL END
 *     HL GET <path>            -> HL BEGIN <size>, HL DATA <b64>..., HL END <crc32>
 *     HL PUT <path> <n> <crc32> [<offset>]
 *                              -> HL READY <offset>; HL DATA <b64> -> HL ACK <held> ...; HL OK
 *     HL STATUS <path>         -> HL PART <held> <size> <crc32>
 *     HL CFG GET | HL CFG PUT <n> <crc32>
 *     HL RM <path>   HL STAT   HL MARK   HL TIME <unix_ms>   HL REBOOT   HL BYE
 *     errors: HL ERR <reason>; idle: HL TIMEOUT
 */

import { ByteBuf, b64, crc16, fromUtf8, hex8, unb64 } from './bytes';
import { type Frame, E_ARG, E_BUSY, E_CRC, E_IO, E_REFUSED, cobsDecode } from './serial_proto';

export { b64, unb64, hex8 };

export const TIMEOUT_S = 30.0;
export const CHUNK = 2048;
export const WINDOW = 3;
export const MODEL = 'cyd-4827s043n';
export const MAX_LINE = 8192;
export const PROTO = 1;

const SEG = /^[A-Za-z0-9_.-]{1,12}$/;

export const WRITE_FILES = ['HANDHELD.CFG', 'HANDHELD.MAP'];
export const MAX_SIZE: Record<string, number> = { 'HANDHELD.CFG': 8 * 1024, 'HANDHELD.MAP': 256 * 1024 };
export const MAX_MAP_FILE = 512 * 1024 * 1024;

/** ``/MAP/MAP.DAT`` -> ``['MAP', 'MAP.DAT']`` (upper case); ``/`` -> ``[]``; null if not a card path. */
export function splitPath(path: string): string[] | null {
  if (!path.startsWith('/') || path.length > 64 || path.includes('\\')) return null;
  const parts = path.split('/').slice(1);
  if (parts.length === 1 && parts[0] === '') return [];
  if (parts.length && parts[parts.length - 1] === '') parts.pop();
  for (const p of parts) {
    if (p === '.' || p === '..' || !SEG.test(p) || p.replace(/^\.+|\.+$/g, '') === '') return null;
  }
  return parts.map((p) => p.toUpperCase());
}

/** PUT: the two settings files at the root and files directly in MAP/. */
export function canWrite(parts: string[]): boolean {
  if (parts.length === 1) return WRITE_FILES.includes(parts[0]!);
  return parts.length === 2 && parts[0] === 'MAP';
}

/** RM: what can be put, and the drive files in DRIVES/. */
export function canRemove(parts: string[]): boolean {
  return canWrite(parts) || (parts.length === 2 && parts[0] === 'DRIVES');
}

export function maxSize(parts: string[]): number {
  return parts.length === 1 ? (MAX_SIZE[parts[parts.length - 1]!] ?? MAX_MAP_FILE) : MAX_MAP_FILE;
}

export function reason(status: number): string {
  return ({ [E_IO]: 'io', [E_CRC]: 'crc', [E_ARG]: 'args', [E_BUSY]: 'busy', [E_REFUSED]: 'refused' } as Record<number, string>)[status] ?? 'state';
}

export type Piece = { kind: 'line'; text: string } | { kind: 'frame'; frame: Frame };

/**
 * Frames and text lines out of one byte stream. The first COBS byte is a length code
 * and the second is ``ver`` (0x01), which no line has in its second place, so a
 * chunk's second byte says which it is (the firmware's rule too).
 */
export class StreamSplitter {
  private buf = new ByteBuf();
  private skip = false;
  bad = 0;

  feed(data: Uint8Array): Piece[] {
    const out: Piece[] = [];
    for (const b of data) {
      if (b === 0) {
        if (this.buf.length && !this.skip) this.frame(out);
        this.buf.clear();
        this.skip = false;
        continue;
      }
      if (this.skip) continue;
      const v = this.buf.view();
      if (b === 0x0a && v.length && (v.length === 1 || v[1] !== 1)) {
        const text = fromUtf8(this.buf.take()).replace(/^[\r\n]+/, '').replace(/\r+$/, '');
        if (text) out.push({ kind: 'line', text });
        continue;
      }
      if (v.length >= MAX_LINE) {
        this.skip = true;
        this.buf.clear();
        this.bad += 1;
        continue;
      }
      this.buf.pushByte(b);
    }
    return out;
  }

  private frame(out: Piece[]): void {
    const buf = this.buf.view();
    if (buf.length === 1 && (buf[0] === 0x0a || buf[0] === 0x0d)) return;
    const raw = buf.length >= 2 && buf[1] !== 1 ? null : cobsDecode(buf.slice());
    if (raw === null || raw.length < 5 || raw[0] !== 1
      || (raw[raw.length - 2]! | (raw[raw.length - 1]! << 8)) !== crc16(raw.subarray(0, raw.length - 2))) {
      this.bad += 1;
      return;
    }
    out.push({ kind: 'frame', frame: [raw[1]!, raw[2]!, raw.slice(3, raw.length - 2)] });
  }
}

export class HlError extends Error {
  constructor(readonly reason: string) {
    super(reason);
  }
}
