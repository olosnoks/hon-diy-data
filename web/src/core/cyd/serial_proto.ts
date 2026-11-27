/**
 * The handheld's serial link (firmware/handheld/SERIAL.md): frame types and payload
 * layouts, ported from hondalog/handheld/serial_proto.py (which mirrors hh_serial.h
 * byte for byte), with the black box's COBS framing (hondalog/blackbox/format.py).
 *
 * A frame is ``COBS(ver type seq payload crc16) 0x00``.
 */

import type { CydHello } from '../types';
import { ByteBuf, Struct, ascii, concat, crc16, cstr, fit } from './bytes';
import * as F from './format';

export { crc16 };

export const PROTO_VERSION = 1;
export const REV = 1;
export const MAX_PAYLOAD = 512;
export const CHUNK = 480;
export const HELLO_S = 2.0;
export const HOST_S = 3.0;
export const MAGIC = Uint8Array.of(0x48, 0x48, 0x53, 0x4c); // "HHSL"
/** Tried in this order when the port stays unreadable. */
export const BAUDS = [921600, 460800, 115200] as const;

// handheld -> laptop
export const SF_HELLO = 0x40, SF_READ = 0x41, SF_TICK = 0x42, SF_LINK = 0x43, SF_GPS = 0x44, SF_EVENT = 0x45,
  SF_STATS = 0x46, SF_LOG = 0x47, SF_ISO = 0x48, SF_TIME_RSP = 0x49, SF_ACK = 0x4a, SF_FILE_INFO = 0x4b,
  SF_FILE_DATA = 0x4c, SF_MAPUP = 0x4d;
// laptop -> handheld (two-way only)
export const SC_GET_HELLO = 0x60, SC_TIME_REQ = 0x61, SC_TIME_SET = 0x62, SC_MARK = 0x63, SC_ACK_ALARM = 0x64,
  SC_DRIVE = 0x65, SC_LIST_FILES = 0x66, SC_READ_FILE = 0x67, SC_PUT_BEGIN = 0x68, SC_PUT_DATA = 0x69,
  SC_PUT_END = 0x6a, SC_DTC = 0x6b, SC_MAPUP_OPEN = 0x6c, SC_MAPUP_WRITE = 0x6d, SC_MAPUP_STATUS = 0x6e,
  SC_MAPUP_COMMIT = 0x6f;
// laptop -> handheld: the laptop's own ECU reads relayed (two-way, HELLO capability CAP_RELAY)
export const SR_RELAY_READ = 0x50, SR_RELAY_ISO = 0x51, SR_RELAY_TICK = 0x52, SR_RELAY_LINK = 0x53;
export const FRAME_NAMES: Record<number, string> = {
  [SF_HELLO]: 'hello', [SF_READ]: 'read', [SF_TICK]: 'tick', [SF_LINK]: 'link', [SF_GPS]: 'gps',
  [SF_EVENT]: 'event', [SF_STATS]: 'stats', [SF_LOG]: 'log', [SF_ISO]: 'iso', [SF_TIME_RSP]: 'time_rsp',
  [SF_ACK]: 'ack', [SF_FILE_INFO]: 'file_info', [SF_FILE_DATA]: 'file_data', [SF_MAPUP]: 'mapup',
};

export const VARIANT_DEFAULT = 0, VARIANT_GPS_P1 = 1;
export const VARIANTS: Record<number, string> = { 0: 'default', 1: 'gps-p1' };
export const CAP_RX = 1, CAP_FILES = 2, CAP_PUT = 4, CAP_LOG = 8, CAP_MAPUP = 0x10, CAP_HL = 0x20, CAP_RELAY = 0x40;
export const F_DRIVE = 1, F_CAPTURE = 2, F_MAP = 3, F_CFG = 4;
export const FILE_KINDS: Record<number, string> = { 1: 'drive', 2: 'capture', 3: 'map', 4: 'cfg' };
export const OK = 0, IGNORED = 1, E_ARG = -1, E_IO = -2, E_CRC = -3, E_STATE = -4, E_REFUSED = -5, E_BUSY = -6,
  E_UNSUPPORTED = -7, E_OFFSET = -8;
export const STATUS_TEXT: Record<number, string> = {
  [OK]: 'ok', [IGNORED]: 'ignored', [E_ARG]: 'bad argument', [E_IO]: 'card error', [E_CRC]: 'checksum mismatch',
  [E_STATE]: 'not expected now', [E_REFUSED]: 'refused: the handheld could not load it', [E_BUSY]: 'busy',
  [E_UNSUPPORTED]: 'not supported (no card, or an older firmware)', [E_OFFSET]: 'wrong offset',
};
export const LINK_OFF = 0, LINK_CONNECTING = 1, LINK_OK = 2, LINK_ISO_ONLY = 3, LINK_ERROR = 4, LINK_RELAY = 5;
export const LINK_NAMES: Record<number, string> = {
  0: 'no reply from the ECU', 1: 'connecting', 2: 'ok', 3: 'OBD2 only', 4: 'K-line cable not found', 5: 'via laptop',
};
export const TS_NONE = 0, TS_NMEA = 1, TS_PPS = 2, TS_HOST = 3;
export const TS_NAMES: Record<number, string> = { 0: 'none', 1: 'GPS sentences', 2: 'GPS PPS', 3: 'set by the laptop' };
export const ST_SD_OK = 1, ST_RECORDING = 2, ST_GPS_FIX = 4, ST_TIME = 8, ST_ARMED = 16, ST_CAPTURING = 32,
  ST_HOST = 64, ST_BUILTIN = 128;
export const FI_OPEN = 1, FI_MORE = 2;
export const FD_EOF = 1, FD_BUSY = 2;
export const NO_FILE = 0xffffffff;
export const MAPUP_CHUNK = MAX_PAYLOAD - 8;
export const MAPUP_FINAL = 1, MAPUP_ABORT = 2;
export const MAPUP_IDLE_S = 10.0;
export const MAPUP_SIZE_MAX = 128 << 20;
/** HELLO flags bit: the handheld has an SD card (HH_SER_HF_SD). */
export const HF_SD = 8;

export const HELLO = new Struct('<4sBBH12s12s12s8s10sBBBBBBIIQqHH'); // 96
export const READ = new Struct('<QBBH'); // 12, then len bytes
export const TICK = new Struct('<QHHI'); // 16
export const LINK = new Struct('<QBBBB'); // 12
export const ISO = new Struct('<QBBBB'); // 12, then len bytes
export const STATS = new Struct('<Qq6IHBB7I6I'); // 96
export const STATS_EXT = new Struct('<B3xI'); // 8
export const TIME_RSP = new Struct('<qQQ'); // 24
export const ACK = new Struct('<BBhI'); // 8
export const FILE_INFO = new Struct('<BBHII'); // 12
export const FILE_DATA = new Struct('<BBHIII'); // 16, then data
export const LIST = new Struct('<B3xI'); // 8
export const READ_FILE = new Struct('<BxHII'); // 12
export const PUT_BEGIN = new Struct('<B3xII'); // 12
export const MAPUP_OPEN = new Struct('<BBxxII'); // 12, then the name
export const MAPUP_WRITE = new Struct('<II'); // 8, then the data
export const MAPUP_COMMIT = new Struct('<B3x'); // 4
export const MAPUP_INFO = new Struct('<BxxxIII16s'); // 32
export const RELAY_READ = new Struct('<QBBH'); // 12, then len raw ECU bytes
export const RELAY_ISO = new Struct('<QBBBB'); // 12, then len bytes
export const RELAY_TICK = new Struct('<QHHI'); // 16
export const RELAY_LINK = new Struct('<QBBBB'); // 12, then up to 10 bytes ECU id
export const TIME_REQ = new Struct('<q');
export const U32 = new Struct('<I');
export const RELAY_LEN_MAX = 64;
export const MU_NONE = 0, MU_OPEN = 1, MU_PAUSED = 2;

// ---------------------------------------------------------------- framing

export function cobsEncode(data: Uint8Array): Uint8Array {
  const out = new ByteBuf();
  const block: number[] = [];
  for (const b of data) {
    if (b === 0) {
      out.pushByte(block.length + 1);
      out.push(Uint8Array.from(block));
      block.length = 0;
    } else {
      block.push(b);
      if (block.length === 254) {
        out.pushByte(0xff);
        out.push(Uint8Array.from(block));
        block.length = 0;
      }
    }
  }
  out.pushByte(block.length + 1);
  out.push(Uint8Array.from(block));
  return out.take();
}

/** null if malformed. */
export function cobsDecode(data: Uint8Array): Uint8Array | null {
  const out = new ByteBuf();
  let i = 0;
  const n = data.length;
  while (i < n) {
    const code = data[i]!;
    if (code === 0 || i + code > n) return null;
    out.push(data.subarray(i + 1, i + code));
    i += code;
    if (code !== 0xff && i < n) out.pushByte(0);
  }
  return out.take();
}

export function frameEncode(ftype: number, payload: Uint8Array = new Uint8Array(0), seq = 0): Uint8Array {
  if (payload.length > MAX_PAYLOAD) throw new Error('payload too long');
  const raw = new Uint8Array(3 + payload.length + 2);
  raw[0] = PROTO_VERSION;
  raw[1] = ftype & 0xff;
  raw[2] = seq & 0xff;
  raw.set(payload, 3);
  const c = crc16(raw.subarray(0, raw.length - 2));
  raw[raw.length - 2] = c & 0xff;
  raw[raw.length - 1] = c >> 8;
  return concat(cobsEncode(raw), Uint8Array.of(0));
}

export type Frame = [type: number, seq: number, payload: Uint8Array];

/** Check a decoded frame: ver, length, CRC. Returns the frame or null. */
export function frameDecode(raw: Uint8Array | null): Frame | null {
  if (raw === null || raw.length < 5 || raw[0] !== PROTO_VERSION) return null;
  const got = raw[raw.length - 2]! | (raw[raw.length - 1]! << 8);
  if (got !== crc16(raw.subarray(0, raw.length - 2))) return null;
  return [raw[1]!, raw[2]!, raw.slice(3, raw.length - 2)];
}

/** Feed bytes, get frames. Bad frames are counted. */
export class Deframer {
  private buf = new ByteBuf();
  bad = 0;

  feed(data: Uint8Array): Frame[] {
    const frames: Frame[] = [];
    this.buf.push(data);
    for (;;) {
      const view = this.buf.view();
      const end = view.indexOf(0);
      if (end < 0) {
        if (this.buf.length > 2 * MAX_PAYLOAD) {
          this.buf.clear();
          this.bad += 1;
        }
        return frames;
      }
      const chunk = this.buf.shift(end + 1).subarray(0, end);
      if (!chunk.length) continue;
      const f = frameDecode(cobsDecode(chunk));
      if (f === null) {
        this.bad += 1;
        continue;
      }
      frames.push(f);
    }
  }
}

// ---------------------------------------------------------------- HELLO

export interface Hello {
  rev: number;
  variant: number;
  caps: number;
  fw: string;
  device: string;
  mapHash: string;
  mapSha: Uint8Array;
  ecuId: Uint8Array;
  linkState: number;
  backend: number;
  linkMode: number;
  nCh: number;
  flags: number;
  bootNo: number;
  baud: number;
  tUs: number;
  unixUs: number;
  helloMs: number;
  maxPayload: number;
}

export const twoWay = (h: Hello): boolean => Boolean(h.caps & CAP_RX);
export const variantName = (h: Hello): string => VARIANTS[h.variant] ?? `variant ${h.variant}`;
export const builtinMap = (h: Hello): boolean => Boolean(h.flags & 1);
/** The handheld's wall clock (unixUs) is held to the GPS pulse. */
export const clockFromPps = (h: Hello): boolean => Boolean(h.flags & 16);

export function helloText(h: Hello): string {
  const way = twoWay(h) ? 'two-way' : 'tx-only';
  return `handheld ${h.device || '?'} fw ${h.fw}, ${variantName(h)}, ${way}, ` +
    `map #${h.mapHash}${builtinMap(h) ? ' (built in)' : ''}, boot ${h.bootNo}`;
}

/** The shared contract's view of a hello. */
export function toCydHello(h: Hello): CydHello {
  return {
    id: h.device, fw: h.fw, model: 'cyd-4827s043n', variant: h.variant === VARIANT_GPS_P1 ? 'gps-p1' : 'default',
    mapHash: h.mapHash, twoWay: twoWay(h), caps: h.caps, boot: h.bootNo,
  };
}

/** A map hash as the firmware keeps it: all 12 characters, no terminator. */
export function hash12(text: string): Uint8Array {
  const out = new Uint8Array(12);
  out.set(ascii(text).subarray(0, 12));
  return out;
}

function padTo(b: Uint8Array, n: number): Uint8Array {
  const out = new Uint8Array(n);
  out.set(b.subarray(0, n));
  return out;
}

export function packHello(h: Hello): Uint8Array {
  return HELLO.pack(MAGIC, h.rev, h.variant, h.caps, fit(h.fw, 12), fit(h.device, 12), hash12(h.mapHash),
    padTo(h.mapSha, 8), padTo(h.ecuId, 10), Math.min(h.ecuId.length, 10), h.linkState, h.backend, h.linkMode,
    h.nCh, h.flags, h.bootNo, h.baud, h.tUs, h.unixUs, h.helloMs, h.maxPayload);
}

/** null if this is not a handheld's hello. */
export function parseHello(p: Uint8Array): Hello | null {
  if (p.length < HELLO.size || p[0] !== MAGIC[0] || p[1] !== MAGIC[1] || p[2] !== MAGIC[2] || p[3] !== MAGIC[3]) return null;
  const [, rev, variant, caps, fw, device, mapHash, sha, ecu, ecuLen, state, backend, mode, nCh, flags, boot,
    baud, tUs, unixUs, helloMs, maxPayload] = HELLO.unpack(p);
  return {
    rev, variant, caps, fw: cstr(fw), device: cstr(device), mapHash: cstr(mapHash), mapSha: sha,
    ecuId: (ecu as Uint8Array).slice(0, Math.min(ecuLen, 10)), linkState: state, backend, linkMode: mode, nCh,
    flags, bootNo: boot, baud, tUs, unixUs, helloMs, maxPayload,
  };
}

export function makeHello(fields: Partial<Hello> = {}): Hello {
  return {
    rev: REV, variant: VARIANT_DEFAULT, caps: CAP_RX, fw: '?', device: '?', mapHash: '', mapSha: new Uint8Array(0),
    ecuId: new Uint8Array(0), linkState: 0, backend: 0, linkMode: 0, nCh: 0, flags: 0, bootNo: 0, baud: 0, tUs: 0,
    unixUs: 0, helloMs: 0, maxPayload: 0, ...fields,
  };
}

// ---------------------------------------------------------------- relay

function u16(x: number): number {
  return Math.max(0, Math.min(0xffff, Math.round(x)));
}

function u64(x: number | bigint): bigint {
  const v = typeof x === 'bigint' ? x : BigInt(Math.trunc(Math.max(0, Number.isFinite(x) ? x : 0)));
  return BigInt.asUintN(64, v < 0n ? 0n : v);
}

/** RELAY_READ: one Honda block read on the laptop (``hostUs`` when the request went out). */
export function packRelayRead(hostUs: number | bigint, addr: number, data: Uint8Array, latencyUs: number): Uint8Array {
  return concat(RELAY_READ.pack(u64(hostUs), addr & 0xff, data.length, u16(latencyUs)), data);
}

/** RELAY_ISO: one OBD2 reply (Mode 01 = 0x41) in OBD2-only mode. */
export function packRelayIso(hostUs: number | bigint, pid: number, data: Uint8Array, mode = 0x41, flags = 0): Uint8Array {
  return concat(RELAY_ISO.pack(u64(hostUs), mode & 0xff, pid & 0xff, data.length, flags & 0xff), data);
}

/** RELAY_TICK: one laptop sample done; ``hz`` is the sample rate (sent x 100). */
export function packRelayTick(hostUs: number | bigint, latencyUs: number, hz: number, n: number): Uint8Array {
  return RELAY_TICK.pack(u64(hostUs), u16(latencyUs), u16(hz * 100), BigInt.asUintN(32, BigInt(Math.trunc(n))));
}

/** RELAY_LINK: the laptop's link state (LINK_OFF / CONNECTING / OK / ISO_ONLY). */
export function packRelayLink(hostUs: number | bigint, state: number, up: boolean, iso: boolean,
  ecuId: Uint8Array = new Uint8Array(0)): Uint8Array {
  const ecu = ecuId.subarray(0, 10);
  return concat(RELAY_LINK.pack(u64(hostUs), state & 0xff, up ? 1 : 0, iso ? 1 : 0, ecu.length), ecu);
}

// ---------------------------------------------------------------- STATS

export const STATS_FIELDS = ['t_us', 'unix_us', 'reads', 'errors', 'timeouts', 'checksum_failures', 'retries',
  'reconnects', 'hz_c', 'link_state', 'flags', 'drive_no', 'log_bytes', 'free_mb', 'markers', 'stall_count',
  'nmea_good', 'nmea_bad', 'tx_frames', 'tx_dropped', 'tx_bytes', 'rx_frames', 'rx_bad', 'ring_peak'] as const;

export type Stats = Record<string, number>;

/** The STATS fields; a frame from older firmware (96 bytes) has time_source 0 and time_err_us 0. */
export function parseStats(p: Uint8Array): Stats | null {
  if (p.length < STATS.size) return null;
  const vals = STATS.unpack(p);
  const out: Stats = {};
  STATS_FIELDS.forEach((name, i) => { out[name] = vals[i]; });
  const ext = p.length >= STATS.size + STATS_EXT.size ? STATS_EXT.unpack(p, STATS.size) : [0, 0];
  out.time_source = ext[0];
  out.time_err_us = ext[1];
  return out;
}

/** A STATS payload; the appended time fields only when time_source or time_err_us is given. */
export function packStats(fields: Stats): Uint8Array {
  const body = STATS.pack(...STATS_FIELDS.map((n) => Math.trunc(fields[n] ?? 0)));
  if ('time_source' in fields || 'time_err_us' in fields) {
    return concat(body, STATS_EXT.pack(Math.trunc(fields.time_source ?? 0), Math.trunc(fields.time_err_us ?? 0)));
  }
  return body;
}

/** The Link screen's wording: "PPS ±20µs", "GPS ±1s", "PC", "none". */
export function timeText(source: number, errUs: number): string {
  if (source === TS_PPS) return errUs >= 1000 ? `PPS ±${pyG(errUs / 1000)}ms` : `PPS ±${errUs}µs`;
  return source === TS_NMEA ? 'GPS ±1s' : source === TS_HOST ? 'PC' : 'none';
}

/** Python's ``:g`` for the small numbers it is used on here. */
export function pyG(x: number): string {
  if (x === 0) return '0';
  const p = Number(x.toPrecision(6));
  const e = Math.floor(Math.log10(Math.abs(p)));
  if (e < -4 || e >= 6) {
    const [m = '', ex = '0'] = p.toExponential(5).split('e');
    const mant = m.replace(/\.?0+$/, '');
    const n = parseInt(ex, 10);
    return `${mant}e${n < 0 ? '-' : '+'}${String(Math.abs(n)).padStart(2, '0')}`;
  }
  return String(p);
}

const MAPUP_NAME = /^MAP\/[A-Z0-9_~$-]{1,8}(\.[A-Z0-9_~$-]{0,3})?$/;

/** A name the handheld takes for a MAP/ upload: ``MAP/`` and an 8.3 name in capitals. */
export function mapupNameOk(name: string): boolean {
  return MAPUP_NAME.test(name) && name !== 'MAP/UPLOAD.TMP';
}

export function statusText(status: number): string {
  return STATUS_TEXT[status] ?? `error ${status}`;
}

// ---------------------------------------------------------------- live records

export interface EventRecord { tUs: number; code: number; src: number; aux: number; value: number }

export function parseEvent(p: Uint8Array): EventRecord | null {
  if (p.length < F.REC_EVENT.size) return null;
  const [tUs, code, src, aux, raw] = F.REC_EVENT.unpack(p);
  const value = F.FLOAT_EVENTS.has(code) ? new DataView(p.buffer, p.byteOffset + 12, 4).getFloat32(0, true) : raw;
  return { tUs, code, src, aux, value };
}

export interface GpsRecord {
  t_us: number;
  gps_lat: number | null;
  gps_lon: number | null;
  gps_speed: number | null;
  gps_heading: number | null;
  gps_sats: number;
  gps_fix: 0 | 1;
  gps_hdop: number | null;
  gps_utc: number | null;
}

export function parseGps(p: Uint8Array): GpsRecord | null {
  if (p.length < F.REC_GPS.size) return null;
  const [tUs, lat, lon, utc, speed, course, fix, sats, hdop] = F.REC_GPS.unpack(p);
  const has = Boolean(fix);
  return {
    t_us: tUs, gps_lat: has ? lat / 1e7 : null, gps_lon: has ? lon / 1e7 : null,
    gps_speed: has ? speed * 0.036 : null, gps_heading: has ? course / 100 : null, gps_sats: sats,
    gps_fix: has ? 1 : 0, gps_hdop: hdop ? hdop / 100 : null, gps_utc: utc ? utc : null,
  };
}

/** An EVENT as text for the UI (serial_link.CydEvent.text). */
export function eventText(ev: EventRecord): string {
  if (ev.code === F.EV_STALL) return `stall: ${F.STALL_MESSAGES[ev.src] ?? 'trigger'}`;
  if (ev.code === F.EV_MARKER) return `MARK ${ev.value} (handheld button)`;
  if (ev.code === F.EV_DTC) return `trouble code ${F.dtcText(Math.trunc(ev.value))}`;
  if (ev.code === F.EV_DTC_NONE) return 'no stored trouble codes';
  return (F.EVENT_NAMES[ev.code] ?? `event_${ev.code}`).replace(/_/g, ' ');
}
