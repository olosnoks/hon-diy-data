/**
 * Read a handheld drive or capture file (``Dnnnnnn.HHD`` / ``Cnnnnnn.HHD``), ported from
 * hondalog/handheld/drivefile.py. FORMAT.md is the layout.
 *
 * The reader takes what checks out and nothing else. A file whose power was cut has no
 * END block and may end in half a block; a file whose space was taken in advance carries
 * whatever the card held after its last block. Every block CRC includes the file's own
 * nonce, and block numbers only go up, so neither a torn block nor an old file's
 * leftovers get in.
 */

import { cstr, fromUtf8 } from './bytes';
import * as F from './format';

export interface Read { tUs: number; addr: number; data: Uint8Array; latencyUs: number }
export interface IsoRead { tUs: number; mode: number; pid: number; data: Uint8Array }
export interface GpsFix {
  tUs: number; lat: number; lon: number; utc: number; speedKmh: number; heading: number; fix: number; sats: number;
  hdop: number | null;
}
export interface HhEvent { tUs: number; code: number; src: number; aux: number; value: number }
export interface CaptureRecord { tTriggerUs: number; tStartUs: number; tEndUs: number; reason: number; complete: boolean; nFired: number }
export interface EndRecord { n_blocks: number; n_reads: number; status: number; t_end_us: number; reason: string }

export interface DriveFile {
  name: string;
  kind: number;
  fileNo: number;
  bootNo: number;
  flags: number;
  backend: number;
  linkMode: number;
  nonce: number;
  t0Us: number;
  unixUsAtT0: number;
  mapHash: string;
  mapSha: Uint8Array;
  fw: string;
  device: string;
  ecuId: Uint8Array;
  driveNo: number;
  reason: number;
  triggerTUs: number;
  prealloc: number;
  reads: Read[];
  iso: IsoRead[];
  gps: GpsFix[];
  events: HhEvent[];
  captures: CaptureRecord[];
  /** [t_us, unix_us, source] */
  times: Array<[number, number, number]>;
  config: string;
  alarmNames: Map<number, string>;
  /** The END block, null = power was cut. */
  end: EndRecord | null;
  blocks: number;
  badBlocks: number;
  missingBlocks: number;
  usedBytes: number;
  size: number;
}

export const isComplete = (df: DriveFile): boolean => df.end !== null;

/** Stable identity of this recording: what the importer remembers. */
export function driveId(df: DriveFile): string {
  return `handheld:${df.device || '?'}:${df.bootNo}:${df.kind === F.FK_DRIVE ? 'D' : 'C'}${df.fileNo}:` +
    (df.nonce >>> 0).toString(16).padStart(8, '0');
}

/** Device time -> seconds of this file (0 = its start). */
export function seconds(df: DriveFile, tUs: number): number {
  return (tUs - df.t0Us) / 1e6;
}

/** Wall clock (unix seconds, UTC) of file second 0: the newest GPS time record, else the header. */
export function epochUnix(df: DriveFile): number | null {
  let best: [number, number, number] | null = null;
  const gps = (s: number) => (F.TIME_FROM_GPS.includes(s) ? 1 : 0);
  for (const rec of df.times) if (best === null || gps(rec[2]) >= gps(best[2])) best = rec;
  if (best !== null) return (best[1] - (best[0] - df.t0Us)) / 1e6;
  if (df.unixUsAtT0 > 0 && df.flags & F.HF_TIME_VALID) return df.unixUsAtT0 / 1e6;
  return null;
}

/** Time of the last thing recorded. */
export function tLastUs(df: DriveFile): number {
  let last = df.t0Us;
  for (const seq of [df.reads, df.iso, df.gps, df.events] as Array<Array<{ tUs: number }>>) {
    for (const item of seq.slice(-64)) last = Math.max(last, item.tUs);
  }
  return last;
}

export function ecuIdText(df: DriveFile): string {
  if (!df.ecuId.length) return '(not read)';
  const text = Array.from(df.ecuId, (b) => (b >= 32 && b < 127 ? String.fromCharCode(b) : '.')).join('');
  return Array.from(df.ecuId, (b) => b.toString(16).toUpperCase().padStart(2, '0')).join(' ') + `  "${text}"`;
}

function records(df: DriveFile, kind: number, t0Us: number, body: Uint8Array): void {
  if (kind === F.B_READS) {
    let pos = 0;
    while (pos + F.REC_READ.size <= body.length) {
      const [dt, addr, n, latency] = F.REC_READ.unpack(body, pos);
      pos += F.REC_READ.size;
      if (pos + n > body.length) break;
      df.reads.push({ tUs: t0Us + dt, addr, data: body.slice(pos, pos + n), latencyUs: latency });
      pos += n;
    }
  } else if (kind === F.B_ISO) {
    let pos = 0;
    while (pos + F.REC_ISO.size <= body.length) {
      const [dt, mode, pid, n] = F.REC_ISO.unpack(body, pos);
      pos += F.REC_ISO.size;
      if (pos + n > body.length) break;
      df.iso.push({ tUs: t0Us + dt, mode, pid, data: body.slice(pos, pos + n) });
      pos += n;
    }
  } else if (kind === F.B_GPS) {
    for (const [tUs, lat, lon, utc, speed, course, fix, sats, hdop] of F.REC_GPS.iter(body)) {
      df.gps.push({ tUs, lat: lat / 1e7, lon: lon / 1e7, utc, speedKmh: speed * 0.036, heading: course / 100, fix, sats,
        hdop: hdop ? hdop / 100 : null });
    }
  } else if (kind === F.B_EVENTS) {
    for (const [tUs, code, src, aux, value] of F.REC_EVENT.iter(body)) {
      df.events.push({ tUs, code, src, aux, value: F.FLOAT_EVENTS.has(code) ? F.i32AsF32(value) : value });
    }
  } else if (kind === F.B_CAPTURE) {
    for (const r of F.REC_CAPTURE.iter(body)) {
      df.captures.push({ tTriggerUs: r[0], tStartUs: r[1], tEndUs: r[2], reason: r[3], complete: Boolean(r[4]), nFired: r[6] });
    }
  } else if (kind === F.B_TIME) {
    for (const [tUs, unixUs, source] of F.REC_TIME.iter(body)) df.times.push([tUs, unixUs, source]);
  } else if (kind === F.B_CONFIG) {
    df.config = fromUtf8(body);
  } else if (kind === F.B_NAMES) {
    for (const line of fromUtf8(body).split(/\r\n|\r|\n/)) {
      const parts = splitN(line, ' ', 2);
      if (parts.length === 3 && parts[0] === 'A' && /^\d+$/.test(parts[1]!)) df.alarmNames.set(parseInt(parts[1]!, 10), parts[2]!);
    }
  } else if (kind === F.B_END && body.length >= F.REC_END.size) {
    const [nBlocks, nReads, status, reason, tEnd] = F.REC_END.unpack(body);
    df.end = { n_blocks: nBlocks, n_reads: nReads, status, t_end_us: tEnd, reason: F.END_REASONS[reason] ?? String(reason) };
  }
}

/** Python ``str.split(sep, n)``. */
function splitN(s: string, sep: string, n: number): string[] {
  const out: string[] = [];
  let rest = s;
  while (out.length < n) {
    const i = rest.indexOf(sep);
    if (i < 0) break;
    out.push(rest.slice(0, i));
    rest = rest.slice(i + sep.length);
  }
  out.push(rest);
  return out;
}

function isMagic(data: Uint8Array, pos: number): boolean {
  const m = F.BLOCK_MAGIC_BYTES;
  return data[pos] === m[0] && data[pos + 1] === m[1] && data[pos + 2] === m[2] && data[pos + 3] === m[3];
}

function findMagic(data: Uint8Array, from: number): number {
  for (let i = from; i + 4 <= data.length; i++) if (isMagic(data, i)) return i;
  return -1;
}

/** Parse the bytes of a drive or capture file. Throws if the file header is not one (nothing else is fatal). */
export function parseDriveFile(data: Uint8Array, name = 'file'): DriveFile {
  if (data.length < F.FILE_HDR.size) throw new Error(`${name}: too short for a handheld file`);
  const h = F.FILE_HDR.unpack(data);
  if (h[0] !== F.FILE_MAGIC || h[2] !== F.FILE_HDR.size) throw new Error(`${name}: not a handheld drive file`);
  if (h[1] !== F.FILE_VERSION) throw new Error(`${name}: file version ${h[1]}; this hondalog reads ${F.FILE_VERSION}`);
  if (F.crc32(data.subarray(0, F.FILE_HDR.size - 4)) !== h[h.length - 1]) throw new Error(`${name}: the file header's checksum is wrong`);
  const df: DriveFile = {
    name, kind: h[5], fileNo: h[3], bootNo: h[4], flags: h[6], backend: h[7], linkMode: h[8], nonce: h[9], t0Us: h[10],
    unixUsAtT0: h[11], mapHash: cstr(h[12]), mapSha: h[13], fw: cstr(h[14]), device: cstr(h[15]),
    ecuId: (h[16] as Uint8Array).slice(0, h[17]), driveNo: h[19], reason: h[20], triggerTUs: h[22], prealloc: h[23],
    reads: [], iso: [], gps: [], events: [], captures: [], times: [], config: '', alarmNames: new Map(), end: null,
    blocks: 0, badBlocks: 0, missingBlocks: 0, usedBytes: F.FILE_HDR.size, size: data.length,
  };
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  let pos = F.FILE_HDR.size;
  let lastSeq = -1;
  let lost = false; // the walk is off a block boundary: looking for the next one
  while (pos + F.BLOCK_HDR.size + 4 <= data.length) {
    let ok = false;
    let kind = 0, seq = 0, t0Us = 0, end = 0;
    if (isMagic(data, pos)) {
      const b = F.BLOCK_HDR.unpack(data, pos);
      kind = b[1];
      const length = b[4];
      seq = b[5];
      t0Us = b[6];
      end = pos + F.BLOCK_HDR.size + length;
      if (length <= F.MAX_PAYLOAD && end + 4 <= data.length && seq > lastSeq
        && dv.getUint32(end, true) === F.blockCrc(df.nonce, data.subarray(pos, end))) ok = true;
    }
    if (!ok) {
      if (!lost) {
        df.badBlocks += 1;
        lost = true;
      }
      const nxt = findMagic(data, pos + 1);
      if (nxt < 0) break;
      pos = nxt;
      continue;
    }
    lost = false;
    df.missingBlocks += seq - lastSeq - 1;
    lastSeq = seq;
    df.blocks += 1;
    records(df, kind, t0Us, data.subarray(pos + F.BLOCK_HDR.size, end));
    pos = df.usedBytes = end + 4;
    if (kind === F.B_END) break;
  }
  // what lies after the last good block of a file cut by power is not a "bad block"
  if (df.end === null && lost) df.badBlocks -= 1;
  df.reads.sort((a, b) => a.tUs - b.tUs);
  df.events.sort((a, b) => a.tUs - b.tUs);
  return df;
}
