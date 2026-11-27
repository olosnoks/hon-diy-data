/**
 * Byte layouts shared with the handheld's firmware (components/hhcore/include/hhcore.h),
 * ported from hondalog/handheld/format.py. Everything is little-endian and packed.
 * firmware/handheld/FORMAT.md is the description.
 */

import { Struct, crc32, cstr, fit } from './bytes';

export { crc32, cstr, fit };

// ---- HANDHELD.MAP -----------------------------------------------------------

export const MAP_MAGIC = 0x504d4848; // "HHMP"
export const MAP_SCHEMA = 1;
export const MAP_MAX_CORE = 32768;
export const MAP_HDR = new Struct('<IHHII12s32sI'); // 64
export const MAP_SEC = new Struct('<BBHI'); // 8
export const MAP_INFO = new Struct('<48s12shBB'); // 64
export const MAP_PROTO = new Struct('<IBBhBBBBB16s3s10d'); // 112
export const MAP_CHAN = new Struct('<16s20s8s6fBBBBBBBBHH'); // 80
export const MAP_ALARM = new Struct('<16s24shBBB3s8f6H'); // 92
export const MAP_STALL = new Struct('<5fhH'); // 24
export const MS_INFO = 1, MS_PROTO = 2, MS_CONST = 3, MS_CHAN = 4, MS_PROG = 5, MS_ALARM = 6, MS_STALL = 7;

export const MAX_CH = 64;
export const MAX_ALARMS = 24;
export const MAX_CONST = 256;
export const MAX_SLOPES = 4;
export const MAX_READ = 64;
export const EXPR_STACK = 32;

export const KIND_VALUE = 0, KIND_FLAG = 1, KIND_MATH = 2;
export const RATE_CLASS: Record<string, number> = { fast: 0, medium: 1, slow: 2 };

export const AF_LATCHING = 0x01, AF_LOW = 0x02, AF_HIGH = 0x04, AF_RATE_LOW = 0x08, AF_RATE_HIGH = 0x10;
export const AF_MARKER = 0x20, AF_CAPTURE = 0x40, AF_TILE = 0x80;

// ---- expression byte code ---------------------------------------------------

export const OP_END = 0x00, OP_CONST = 0x01, OP_IMM = 0x02, OP_X = 0x03, OP_BYTE = 0x04, OP_CH = 0x05, OP_TIME = 0x06;
export const OP_NEG = 0x10, OP_NOT = 0x11, OP_BITNOT = 0x12, OP_VALID = 0x13;
export const OP_BINARY: Record<string, number> = {
  '+': 0x20, '-': 0x21, '*': 0x22, '/': 0x23, '//': 0x24, '%': 0x25, '**': 0x26,
  '&': 0x27, '|': 0x28, '^': 0x29, '<<': 0x2a, '>>': 0x2b,
};
export const OP_COMPARE: Record<string, number> = { '<': 0x30, '<=': 0x31, '>': 0x32, '>=': 0x33, '==': 0x34, '!=': 0x35 };
export const OP_AND = 0x38, OP_OR = 0x39, OP_SELECT = 0x3a, OP_COALESCE = 0x3b, OP_CALL = 0x40;
export const FUNCTIONS = ['abs', 'min', 'max', 'round', 'int', 'float', 'floor', 'ceil', 'sqrt', 'exp', 'ln',
  'log10', 'pow', 'sin', 'cos', 'sign', 'clamp', 'between', 'bit', 'signed', 'poly', 'table', 'thermistor'];
export const FN: Record<string, number> = Object.fromEntries(FUNCTIONS.map((n, i) => [n, i]));
FN.log = FN.ln!;

// ---- drive and capture files ------------------------------------------------

export const FILE_MAGIC = 0x46444848; // "HHDF"
export const BLOCK_MAGIC = 0x4b424848; // "HHBK"
export const BLOCK_MAGIC_BYTES = Uint8Array.of(0x48, 0x48, 0x42, 0x4b);
export const FILE_VERSION = 1;
export const FILE_HDR = new Struct('<IHHIIBBBBIQq12s8s12s12s10sBBIB3sQI8sI'); // 128
export const BLOCK_HDR = new Struct('<IBBHIIQ'); // 24
export const FK_DRIVE = 1, FK_CAPTURE = 2;
export const HF_TIME_VALID = 1, HF_TIME_GPS = 2, HF_PREALLOC = 4, HF_BUILTIN_MAP = 8;
export const TIME_GPS = 1, TIME_CLOCK = 2, TIME_HOST = 3, TIME_PPS = 4;
export const TIME_FROM_GPS = [TIME_GPS, TIME_PPS];

export const B_READS = 1, B_GPS = 2, B_EVENTS = 3, B_CONFIG = 4, B_ISO = 5, B_NAMES = 6, B_CAPTURE = 7, B_TIME = 8, B_END = 0x7f;
export const BLOCK_NAMES: Record<number, string> = {
  [B_READS]: 'reads', [B_GPS]: 'gps', [B_EVENTS]: 'events', [B_CONFIG]: 'config', [B_ISO]: 'iso',
  [B_NAMES]: 'names', [B_CAPTURE]: 'capture', [B_TIME]: 'time', [B_END]: 'end',
};
export const MAX_PAYLOAD = 4096;

export const REC_READ = new Struct('<IBBH');
export const REC_ISO = new Struct('<IBBBB');
export const REC_GPS = new Struct('<QiiIHHBBH'); // 28
export const REC_EVENT = new Struct('<QBBHi'); // 16
export const REC_CAPTURE = new Struct('<QQQBBHI'); // 32
export const REC_TIME = new Struct('<QqB7s'); // 24
export const REC_END = new Struct('<IIIIQ'); // 24

export const EV_MARKER = 1, EV_LINK_UP = 2, EV_LINK_DOWN = 3, EV_ALARM_RAISED = 4, EV_ALARM_CLEARED = 5,
  EV_ALARM_ACK = 6, EV_STALL = 7, EV_PORT_LOST = 8, EV_PORT_BACK = 9, EV_ISO_SESSION = 10, EV_DTC = 11,
  EV_LOG_DROPPED = 12, EV_DRIVE_START = 13, EV_DTC_NONE = 14;
export const EVENT_NAMES: Record<number, string> = {
  1: 'marker', 2: 'link_up', 3: 'link_down', 4: 'alarm_raised', 5: 'alarm_cleared', 6: 'alarm_ack', 7: 'stall',
  8: 'port_lost', 9: 'port_back', 10: 'iso_session', 11: 'dtc', 12: 'log_dropped', 13: 'drive_start', 14: 'dtc_none',
};
export const FLOAT_EVENTS = new Set([EV_ALARM_RAISED, EV_ALARM_CLEARED, EV_ALARM_ACK, EV_STALL]);
export const STALL_REASONS: Record<number, string> = { 1: 'rpm', 2: 'link', 3: 'alarm' };
export const STALL_MESSAGES: Record<number, string> = { 1: 'rpm dropped to 0', 2: 'link lost (no reply from ECU)', 3: 'alarm' };
export const END_REASONS: Record<number, string> = { 1: 'link_lost', 2: 'stopped', 3: 'sleep' };
export const BACKENDS: Record<number, string> = { 0: 'usb', 1: 'uart' };

/** A block's CRC covers the file's nonce, then the header and the payload. */
export function blockCrc(nonce: number, headerAndPayload: Uint8Array): number {
  const n = new Uint8Array(4);
  new DataView(n.buffer).setUint32(0, nonce >>> 0, true);
  return crc32(headerAndPayload, crc32(n));
}

/** Raw two bytes of an OBD2 trouble code -> "P0301". */
export function dtcText(code: number): string {
  return 'PCBU'[(code >> 14) & 3] + String((code >> 12) & 3) + (code & 0xfff).toString(16).toUpperCase().padStart(3, '0');
}

/** The f32 view of an i32 (an EVENT's last word for the float codes). */
export function i32AsF32(v: number): number {
  const b = new DataView(new ArrayBuffer(4));
  b.setInt32(0, v, true);
  return b.getFloat32(0, true);
}
