/**
 * Shared contracts for the hondalog web port. Every module builds against
 * these; change them only through the integrator (see web/PORTING.md).
 *
 * Units follow the Python app: time in seconds (monotonic, since connect)
 * unless a name says _ms/_us/_unix; storage is metric.
 */

import type { AlarmRuleInit } from './alarms/engine';
import type { MathChannelInit } from './expr/mathch';

// ---------------------------------------------------------------- transport

/** One serial line (KKL cable to the ECU, or the CYD's USB-C). Implemented by
 *  WebSerialTransport (desktop Chrome/Edge), UsbSerialTransport (WebUSB JS
 *  drivers: Android and the TWA), SimTransport (built-in simulator).
 *  Byte-exact, no framing. */
export interface Transport {
  readonly kind: 'webserial' | 'webusb' | 'sim';
  /** A human label: "COM21", "/dev/ttyUSB0", "FTDI 0403:6001", "sim:". */
  readonly label: string;
  open(opts: { baud: number }): Promise<void>;
  close(): Promise<void>;
  setBaud(baud: number): Promise<void>;
  /** Hold the line low (true) / release (false): ISO 9141 5-baud init, wake-up. */
  setBreak(on: boolean): Promise<void>;
  write(bytes: Uint8Array): Promise<void>;
  /** Read up to `max` bytes. Resolves AS SOON AS at least one byte is
   *  available (with everything buffered, up to `max`), like pyserial
   *  read_some; resolves empty only when `timeoutMs` passes with nothing.
   *  Never throws on timeout (may throw if the device is gone). */
  read(max: number, timeoutMs: number): Promise<Uint8Array>;
  /** Discard anything buffered on the receive side. */
  drain(): Promise<void>;
  /** USB identity when known (FTDI 0403:6001, CH340 1a86:7523). */
  readonly usb?: { vendorId: number; productId: number };
  /** The adapter's receive latency in seconds when known (FTDI latency
   *  timer: 0.001 when set by the WebUSB driver; unknown on desktop Web
   *  Serial, where Windows defaults to 16 ms). */
  readonly usbLatencyS?: number;
}

export interface TransportPicker {
  /** Ports the user already granted (no prompt). */
  list(): Promise<PortInfo[]>;
  request?(filters?: { vendorId: number; productId?: number }[]): Promise<PortInfo | null>;
  open(port: PortInfo): Transport;
}

export interface PortInfo {
  id: string;
  label: string;
  vendorId?: number;
  productId?: number;
  /** "ecu" = FTDI KKL, "cyd" = CH340 handheld, "unknown". */
  guess: 'ecu' | 'cyd' | 'unknown';
}

// ---------------------------------------------------------------- address map

export type RateClass = 'fast' | 'medium' | 'slow';
export type ChannelKind = 'value' | 'flag' | 'math';
export type ChannelStatus = 'verified' | 'unverified' | 'suspect';

export interface ChannelSpec {
  name: string;
  label: string;
  addr: number;          // 0x00-0xFF
  length: number;        // 1-4
  formula: string;       // expression in the hondalog expression language
  units: string;
  min: number;
  max: number;
  decimals: number;
  kind: ChannelKind;
  status: ChannelStatus;
  rate: RateClass;
  endian: 'big' | 'little';
  source?: string;
  notes?: string;
  /** UI grouping on F1 Live (engine, idle, fuel, ignition, electrical, switches, cyd, raw). */
  group?: string;
}

/** A math channel as the map defines it: #3's MathChannelInit, with the
 *  display fields every screen needs made required. */
export interface MathSpec extends MathChannelInit { label: string; units: string; min: number; max: number; decimals: number }
/** An alarm rule with the Python fields (#3's AlarmRuleInit: severity
 *  warn/critical, low/high/rate limits, gate, hysteresis, actions ...).
 *  level / rearm_when are the old short names (#2's AlarmRule fills them). */
export interface AlarmSpec extends AlarmRuleInit { level?: 'warn' | 'alarm'; rearm_when?: string }

export interface AddressMap {
  version: string;
  variant: string;
  channels: ChannelSpec[];
  math: MathSpec[];
  alarms: AlarmSpec[];
  constants: Record<string, number>;
  protocol: Partial<Framing>;
  ecuId: { addr: number | null; length: number };
  /** 12 hex digits; MUST equal the Python AddressMap.version_hash for the same
   *  TOML (the CYD checks it before taking relay frames). */
  versionHash: string;
  /** The TOML text it was parsed from (stored with every drive). */
  text: string;
}

/** Honda diagnostic protocol framing (hondalog/protocol.py Framing). */
export interface Framing {
  baud: number;               // 9600 or 10400 (P2P: 10400 with the wake frame)
  cmdRead: number;            // 0x20
  replyHeader: number;        // 0x00, -1 = any
  initSequence: string;       // hex wake frame, "" = none
  interRequestDelayS: number;
  minReplyTimeoutS: number;
  interByteTimeoutS: number;
  maxInterRequestDelayS: number;
  turnaroundS: number;
}

// ---------------------------------------------------------------- live data

/** One block read from the ECU (the unit stored in ecu_reads). */
export interface EcuRead {
  t: number;              // s since connect, corrected for data age
  addr: number;
  data: Uint8Array;
  latencyS: number;
}

export type LinkState = 'stopped' | 'connecting' | 'ok' | 'iso_only' | 'error';

export interface LinkStats {
  requests: number; ok: number; timeouts: number; shortReplies: number;
  checksumFailures: number; badFrames: number; retries: number; resyncs: number;
  rateHz: number;
}

/** The newest decoded value of every channel, plus history for sparklines/lanes. */
export interface ChannelState {
  spec: ChannelSpec | MathSpec;
  value: number | null;       // null = no value / stale
  t: number;
  min: number | null;
  max: number | null;
  fresh: boolean;
  alarm: 0 | 1 | 2;
}

// ---------------------------------------------------------------- recording

export interface DriveInfo {
  id: number;
  name: string;                 // drive_YYYY_MM_DD_HHMM, stall_..., dropout_...
  kind: 'drive' | 'stall' | 'dropout' | 'capture' | 'alarm';
  startedUnix: number;
  durationS: number;
  rows: number;
  status: 'recording' | 'ok' | 'crashed';
  mapVersion: string;
  ecuId: string;
  reason?: string;
  /** What kind of capture a capture row is (stall, dropout, overtemp, lowbatt, manual ...). */
  captureKind?: string;
  /** drives.keep: never pruned; deleting it asks twice (a drive row only). */
  keep?: boolean;
}

/** A person's note and verdict on one event (desktop eventnotes.py, events rows of kind 'event_note'). */
export interface EventNote {
  note: string;
  verdict: string;
  kind: string;
  t: number | null;
}

/** Store backed by SQLite (sql.js in the browser, persisted to OPFS; the same
 *  schema as the Python hondalog.db so files move between apps). */
export interface DriveStore {
  listDrives(): Promise<DriveInfo[]>;
  /** Columns of a drive as typed arrays, time-aligned per stream. */
  loadDrive(name: string): Promise<LoadedDrive>;
  deleteDrive(name: string, opts?: { allowKeep?: boolean }): Promise<void>;
  importDb(file: Uint8Array): Promise<number>;
  exportDb(): Promise<Uint8Array>;
  /** Mark a drive keep (or not). */
  setKeep?(name: string, keep: boolean): Promise<void>;
  /** A CSV / MSL / MLG log file as a new drive; resolves the drive's name. */
  importFile?(fileName: string, bytes: Uint8Array): Promise<string>;
  /** Notes and verdicts of one drive's events, by event key ("near_stall@3210.4"). */
  loadEventNotes?(drive: string): Promise<Record<string, EventNote>>;
  /** Set (undefined: leave, '': clear) the note and/or verdict of one event. */
  saveEventNote?(drive: string, key: string, change: { note?: string; verdict?: string; kind?: string; t?: number }): Promise<void>;
}

export interface LoadedDrive {
  info: DriveInfo;
  map: AddressMap | null;
  /** channel name -> samples */
  series: Record<string, { t: Float64Array; v: Float64Array }>;
  reads: EcuRead[];
  events: { t: number; kind: string; text: string }[];
  gps: { t: Float64Array; lat: Float64Array; lon: Float64Array; speedKmh: Float64Array; heading: Float64Array };
}

// ---------------------------------------------------------------- CYD (black box)

export interface CydHello {
  id: string; fw: string; model: string; variant: 'default' | 'gps-p1';
  mapHash: string; twoWay: boolean; caps: number; boot: number;
}
