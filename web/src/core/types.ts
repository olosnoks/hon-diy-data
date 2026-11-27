/**
 * Shared contracts for the hondalog web port. Every module builds against
 * these; change them only through the integrator (see web/PORTING.md).
 *
 * Units follow the Python app: time in seconds (monotonic, since connect)
 * unless a name says _ms/_us/_unix; storage is metric.
 */

// ---------------------------------------------------------------- transport

/** One serial line (KKL cable to the ECU, or the CYD's USB-C). Implemented by
 *  WebSerialTransport (browser), TauriSerialTransport (Windows/Linux/Android),
 *  SimTransport (built-in simulator). Byte-exact, no framing. */
export interface Transport {
  readonly kind: 'webserial' | 'tauri' | 'sim';
  /** A human label: "COM21", "/dev/ttyUSB0", "FTDI 0403:6001", "sim:". */
  readonly label: string;
  open(opts: { baud: number }): Promise<void>;
  close(): Promise<void>;
  setBaud(baud: number): Promise<void>;
  /** Hold the line low (true) / release (false): ISO 9141 5-baud init, wake-up. */
  setBreak(on: boolean): Promise<void>;
  write(bytes: Uint8Array): Promise<void>;
  /** Read up to `max` bytes, resolving with what arrived within `timeoutMs`
   *  (possibly empty). Never throws on timeout. */
  read(max: number, timeoutMs: number): Promise<Uint8Array>;
  /** Discard anything buffered on the receive side. */
  drain(): Promise<void>;
  /** USB identity when known (FTDI 0403:6001, CH340 1a86:7523). */
  readonly usb?: { vendorId: number; productId: number };
}

export interface TransportPicker {
  /** Ask the user to pick a port (browser) or list ports (native). */
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

export interface MathSpec { name: string; label: string; expr: string; units: string; min: number; max: number; decimals: number }
export interface AlarmSpec { name: string; when: string; level: 'warn' | 'alarm'; message: string; hold_s?: number; rearm_when?: string; channel?: string }

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
}

/** Store backed by SQLite (sql.js in the browser, persisted to OPFS; the same
 *  schema as the Python hondalog.db so files move between apps). */
export interface DriveStore {
  listDrives(): Promise<DriveInfo[]>;
  /** Columns of a drive as typed arrays, time-aligned per stream. */
  loadDrive(name: string): Promise<LoadedDrive>;
  deleteDrive(name: string): Promise<void>;
  importDb(file: Uint8Array): Promise<number>;
  exportDb(): Promise<Uint8Array>;
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
