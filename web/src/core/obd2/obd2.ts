/**
 * OBD2 services on an initialised ``Iso9141Link`` (port of the services half
 * of hondalog/obd2.py): Mode 01 live PIDs (only the supported ones), Mode 02
 * freeze frame, Mode 03 stored and Mode 07 pending DTCs, Mode 05 O2 sensor
 * tests, Mode 06 on-board test results, Mode 04 clear (needs a typed
 * confirmation and a stopped engine).
 */

import { describe as describeDtc } from './dtc';
import { hex2, hexLower } from './bytes';
import {
  ConfirmationRequired,
  initText,
  Iso9141Error,
  NoResponse,
  type Iso9141Link,
} from './iso9141';

// ---------------------------------------------------------------- clearing

/** What the user must type, exactly, before Mode 04 is sent. */
export const CLEAR_CONFIRM = 'CLEAR';
export const CLEAR_WARNING =
  'Clearing trouble codes (OBD2 Mode 04) also erases the freeze frame and resets ' +
  "every readiness monitor to 'not ready'. The car will not pass an emissions " +
  'inspection until the monitors have run again, which takes several days of ' +
  'normal driving. The ECU may also forget its fuel trims and idle learning. ' +
  'Do this with the key ON and the engine OFF, and write the codes down first.';

/** True only for the exact confirmation text (case and spaces matter). */
export function clearConfirmed(typed: string): boolean {
  return typed === CLEAR_CONFIRM;
}

// ---------------------------------------------------------------- rounding

/** Python's round(x, nd): exact ties go to the even digit. */
export function pyRound(x: number, nd = 0): number {
  const p = 10 ** nd;
  // An exact decimal tie x = m / (2 * 10^nd) with m odd needs 5^nd | m, and
  // then x = (m / 5^nd) / 2^(nd+1) is exact in binary. Anything else is not a
  // tie, and toFixed rounds the exact binary value correctly.
  const m = Math.round(x * p * 2);
  const f = 5 ** nd;
  if (Math.abs(m) % 2 === 1 && m % f === 0 && m / f / 2 ** (nd + 1) === x) {
    const fl = Math.floor(x * p);
    return (fl % 2 === 0 ? fl : fl + 1) / p;
  }
  return Number(x.toFixed(nd));
}

// ---------------------------------------------------------------- PIDs

export interface PidValue {
  pid: number;
  name: string;
  value: number | null;
  units: string;
  raw: Uint8Array;
  text: string;
}

export function pidText(v: PidValue): string {
  if (v.text) return `${v.name}: ${v.text}`;
  if (v.value === null) return `${v.name}: ${hexLower(v.raw)}`;
  return `${v.name}: ${fmtG(v.value)} ${v.units}`.trimEnd();
}

/** Python's '%g' for the values PIDs produce. */
export function fmtG(x: number): string {
  if (Number.isInteger(x) && Math.abs(x) < 1e6) return String(x);
  const s = x.toPrecision(6);
  return s.includes('e') ? s : s.replace(/\.?0+$/, '');
}

const FUEL_STATUS: Record<number, string> = {
  0: 'not reported', 1: 'open loop (not yet warm)', 2: 'closed loop',
  4: 'open loop (load or decel fuel cut)', 8: 'open loop (system fault)',
  16: 'closed loop, but a fault in an O2 sensor',
};
const OBD_STD: Record<number, string> = {
  1: 'OBD-II (CARB)', 2: 'OBD (EPA)', 3: 'OBD and OBD-II', 4: 'OBD-I',
  5: 'not OBD compliant', 6: 'EOBD', 7: 'EOBD and OBD-II',
};

const trim = (a: number): number => pyRound(((a - 128) * 100) / 128, 1);
const need = (d: Uint8Array, n: number): void => {
  if (d.length < n) throw new RangeError('short');
};
const b0 = (d: Uint8Array): number => (need(d, 1), d[0]!);
const w0 = (d: Uint8Array): number => (need(d, 2), d[0]! * 256 + d[1]!);

type Decoder = (d: Uint8Array) => number | string;
/** pid -> [name, units, decoder] */
export const PIDS: Record<number, [string, string, Decoder]> = {
  0x03: ['Fuel system status', '', (d) => FUEL_STATUS[b0(d)] ?? `0x${hex2(d[0]!)}`],
  0x04: ['Calculated load', '%', (d) => pyRound((b0(d) * 100) / 255, 1)],
  0x05: ['Coolant temperature', '°C', (d) => b0(d) - 40],
  0x06: ['Short term fuel trim B1', '%', (d) => trim(b0(d))],
  0x07: ['Long term fuel trim B1', '%', (d) => trim(b0(d))],
  0x08: ['Short term fuel trim B2', '%', (d) => trim(b0(d))],
  0x09: ['Long term fuel trim B2', '%', (d) => trim(b0(d))],
  0x0a: ['Fuel pressure', 'kPa', (d) => b0(d) * 3],
  0x0b: ['Intake manifold pressure', 'kPa', (d) => b0(d)],
  0x0c: ['Engine speed', 'rpm', (d) => w0(d) / 4],
  0x0d: ['Vehicle speed', 'km/h', (d) => b0(d)],
  0x0e: ['Ignition advance', '° BTDC', (d) => b0(d) / 2 - 64],
  0x0f: ['Intake air temperature', '°C', (d) => b0(d) - 40],
  0x10: ['Mass air flow', 'g/s', (d) => w0(d) / 100],
  0x11: ['Throttle position', '%', (d) => pyRound((b0(d) * 100) / 255, 1)],
  0x13: [
    'O2 sensors present',
    '',
    (d) => {
      const a = b0(d);
      const names: string[] = [];
      for (let i = 0; i < 8; i++) if ((a >> i) & 1) names.push(`B${1 + (i >> 2)}S${1 + (i % 4)}`);
      return names.join(' ') || 'none';
    },
  ],
  0x1c: ['OBD standard', '', (d) => OBD_STD[b0(d)] ?? `0x${hex2(d[0]!)}`],
  0x1f: ['Run time since start', 's', (d) => w0(d)],
  0x21: ['Distance with MIL on', 'km', (d) => w0(d)],
};
for (let i = 0; i < 8; i++) {
  PIDS[0x14 + i] = [`O2 sensor B${1 + (i >> 2)}S${1 + (i % 4)}`, 'V', (d) => pyRound(b0(d) / 200, 3)];
}

export function decodePid(pid: number, data: Uint8Array): PidValue {
  const raw = Uint8Array.from(data);
  const entry = PIDS[pid];
  if (!entry) return { pid, name: `PID ${hex2(pid)}`, value: null, units: '', raw, text: '' };
  const [name, units, fn] = entry;
  let value: number | string;
  try {
    value = fn(raw);
  } catch {
    return { pid, name, value: null, units, raw, text: 'short reply' };
  }
  if (typeof value === 'string') return { pid, name, value: null, units, raw, text: value };
  return { pid, name, value, units, raw, text: '' };
}

/** Two DTC bytes -> 'P0135'. */
export function decodeDtc(hi: number, lo: number): string {
  return `${'PCBU'[hi >> 6]}${(hi >> 4) & 3}${(hi & 0xf).toString(16).toUpperCase()}` +
    `${(lo >> 4).toString(16).toUpperCase()}${(lo & 0xf).toString(16).toUpperCase()}`;
}

/** 'P0135' -> [0x01, 0x35] (the inverse of decodeDtc). */
export function dtcToBytes(code: string): [number, number] {
  const system = 'PCBU'.indexOf(code[0]!.toUpperCase());
  const h = (i: number) => parseInt(code[i]!, 16);
  return [(system << 6) | (h(1) << 4) | h(2), (h(3) << 4) | h(4)];
}

// ---------------------------------------------------------------- readiness

const MONITORS_B: [number, string][] = [[0, 'misfire'], [1, 'fuel system'], [2, 'comprehensive components']];
const MONITORS_CD: [number, string][] = [
  [0, 'catalyst'], [1, 'heated catalyst'], [2, 'evaporative system'], [3, 'secondary air'],
  [4, 'A/C refrigerant'], [5, 'O2 sensor'], [6, 'O2 sensor heater'], [7, 'EGR'],
];

export interface Readiness {
  milOn: boolean;
  dtcCount: number;
  /** supported monitors -> complete? (insertion order as on the wire) */
  monitors: Record<string, boolean>;
  raw: Uint8Array;
}

export const readinessReady = (r: Readiness): boolean => Object.values(r.monitors).every(Boolean);
export const readinessIncomplete = (r: Readiness): string[] =>
  Object.entries(r.monitors).filter(([, done]) => !done).map(([n]) => n);

export function readinessText(r: Readiness): string {
  const state = readinessReady(r) ? 'all monitors ready' : 'not ready: ' + readinessIncomplete(r).join(', ');
  return `MIL ${r.milOn ? 'ON' : 'off'}, ${r.dtcCount} stored DTC(s), ${state}`;
}

/** Mode 01 PID 01: MIL, DTC count and monitor readiness. */
export function decodeReadiness(data: Uint8Array): Readiness {
  if (data.length < 4) throw new NoResponse('PID 01 reply too short');
  const [a, b, c, d] = [data[0]!, data[1]!, data[2]!, data[3]!];
  const monitors: Record<string, boolean> = {};
  for (const [bit, name] of MONITORS_B) if ((b >> bit) & 1) monitors[name] = !((b >> (bit + 4)) & 1);
  for (const [bit, name] of MONITORS_CD) if ((c >> bit) & 1) monitors[name] = !((d >> bit) & 1);
  return { milOn: Boolean(a & 0x80), dtcCount: a & 0x7f, monitors, raw: data.slice(0, 4) };
}

// ---------------------------------------------------------------- channels

/** Mode 01 PIDs that are the same quantity as an address map channel. */
export const PID_CHANNEL: Record<number, string> = {
  0x0c: 'rpm', 0x0d: 'vss', 0x0b: 'map', 0x11: 'tps', 0x0e: 'ign', 0x14: 'o2', 0x06: 'stft',
  0x07: 'ltft', 0x05: 'ect', 0x0f: 'iat', 0x01: 'mil', 0x03: 'closed_loop',
  0x04: 'calc_load', 0x15: 'o2_post',
};

/** PIDs whose channel value is a 0/1 flag rather than decodePid().value. */
export const PID_DECODE: Record<number, (d: Uint8Array) => number> = {
  /** PID 01 byte A bit 7: the check engine lamp is commanded on. */
  0x01: (d) => (b0(d) >> 7) & 1,
  /** PID 03: closed loop (0x02) or closed loop with an O2 fault (0x10) -> 1. */
  0x03: (d) => (b0(d) === 0x02 || b0(d) === 0x10 ? 1 : 0),
};

/** The number a PID puts on its PID_CHANNEL channel (null: short reply or no number). */
export function channelValue(pid: number, data: Uint8Array): number | null {
  const fn = PID_DECODE[pid];
  if (!fn) return decodePid(pid, data).value;
  try {
    return fn(data);
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------- DTCs etc.

export interface Dtc {
  code: string;
  description: string;
  status: 'stored' | 'pending';
}

export const dtcText = (d: Dtc): string =>
  `${d.code}  ${d.description}` + (d.status === 'pending' ? '  (pending)' : '');

export interface FreezeFrame {
  dtc: Dtc | null;
  values: Map<number, PidValue>;
}

/** Mode 05 test id -> [name, units, scale]. SAE J1979 non-CAN O2 tests. */
export const O2_TESTS: Record<number, [string, string, number]> = {
  0x01: ['Rich to lean sensor threshold voltage', 'V', 0.005],
  0x02: ['Lean to rich sensor threshold voltage', 'V', 0.005],
  0x03: ['Low sensor voltage for switch time', 'V', 0.005],
  0x04: ['High sensor voltage for switch time', 'V', 0.005],
  0x05: ['Rich to lean sensor switch time', 's', 0.004],
  0x06: ['Lean to rich sensor switch time', 's', 0.004],
  0x07: ['Minimum sensor voltage for test cycle', 'V', 0.005],
  0x08: ['Maximum sensor voltage for test cycle', 'V', 0.005],
  0x09: ['Time between sensor transitions', 's', 0.04],
  0x0a: ['Sensor period', 's', 0.04],
};

export interface O2TestResult {
  tid: number;
  /** O2 sensor bit (0x01 = B1S1, 0x02 = B1S2 ...) */
  sensor: number;
  name: string;
  value: number | null;
  units: string;
  /** limits when the ECU sends them (min, max), scaled like the value */
  min: number | null;
  max: number | null;
  raw: Uint8Array;
}

/** Mode 06 (non-CAN): one on-board monitoring test result. Values are
 *  manufacturer scaled; only pass/fail against the limit is generic. */
export interface Mode06Result {
  tid: number;
  componentId: number;
  value: number;
  limit: number;
  limitKind: 'max' | 'min';
  pass: boolean;
  raw: Uint8Array;
}

export function sensorName(bit: number): string {
  for (let i = 0; i < 8; i++) if (bit === 1 << i) return `B${1 + (i >> 2)}S${1 + (i % 4)}`;
  return `sensor ${hex2(bit)}`;
}

export interface Obd2Snapshot {
  init: string;
  readiness: Readiness | null;
  stored: Dtc[];
  pending: Dtc[];
  freezeFrame: FreezeFrame | null;
  supportedPids: number[];
  values: Map<number, PidValue>;
  errors: string[];
}

export function emptySnapshot(): Obd2Snapshot {
  return { init: '', readiness: null, stored: [], pending: [], freezeFrame: null, supportedPids: [], values: new Map(), errors: [] };
}

/** Plain text, as hondalog's Obd2Snapshot.to_text (goes in the reports). */
export function snapshotText(s: Obd2Snapshot): string {
  const lines = ['OBD2 snapshot', `  link: ${s.init || 'unknown'}`];
  if (s.readiness) {
    lines.push(`  status: ${readinessText(s.readiness)}`);
    for (const [name, done] of Object.entries(s.readiness.monitors)) {
      lines.push(`    ${name.padEnd(26)} ${done ? 'ready' : 'NOT READY'}`);
    }
  }
  for (const [title, codes] of [['stored codes', s.stored], ['pending codes', s.pending]] as const) {
    lines.push(`  ${title}: ` + (codes.length ? '' : 'none'));
    for (const d of codes) lines.push(`    ${d.code}  ${d.description}`);
  }
  if (s.freezeFrame?.dtc) {
    lines.push(`  freeze frame for ${s.freezeFrame.dtc.code}:`);
    for (const v of s.freezeFrame.values.values()) lines.push(`    ${pidText(v)}`);
  }
  if (s.values.size) {
    lines.push('  live values (supported PIDs: ' + s.supportedPids.map(hex2).join(' ') + '):');
    for (const v of s.values.values()) lines.push(`    ${pidText(v)}`);
  }
  for (const e of s.errors) lines.push(`  ! ${e}`);
  return lines.join('\n') + '\n';
}

/** Markdown of a snapshot for the UI (show and save). */
export function snapshotMarkdown(s: Obd2Snapshot): string {
  const out = ['# OBD2 snapshot', '', `- link: ${s.init || 'unknown'}`];
  if (s.readiness) out.push(`- status: ${readinessText(s.readiness)}`);
  out.push('');
  if (s.readiness && Object.keys(s.readiness.monitors).length) {
    out.push('## Readiness monitors', '', '| monitor | state |', '|---|---|');
    for (const [name, done] of Object.entries(s.readiness.monitors)) out.push(`| ${name} | ${done ? 'ready' : 'NOT READY'} |`);
    out.push('');
  }
  for (const [title, codes] of [['Stored codes', s.stored], ['Pending codes', s.pending]] as const) {
    out.push(`## ${title}`, '');
    if (!codes.length) out.push('none');
    else {
      out.push('| code | description |', '|---|---|');
      for (const d of codes) out.push(`| ${d.code} | ${d.description.replace(/\|/g, '\\|')} |`);
    }
    out.push('');
  }
  if (s.freezeFrame?.dtc) {
    out.push(`## Freeze frame for ${s.freezeFrame.dtc.code}`, '', '| PID | value |', '|---|---|');
    for (const v of s.freezeFrame.values.values()) out.push(`| ${hex2(v.pid)} | ${pidText(v)} |`);
    out.push('');
  }
  if (s.values.size) {
    out.push('## Live values', '', `Supported PIDs: ${s.supportedPids.map(hex2).join(' ')}`, '', '| PID | value |', '|---|---|');
    for (const v of s.values.values()) out.push(`| ${hex2(v.pid)} | ${pidText(v)} |`);
    out.push('');
  }
  if (s.errors.length) out.push('## Errors', '', ...s.errors.map((e) => `- ${e}`), '');
  return out.join('\n');
}

function bitmapPids(base: number, data: Uint8Array): number[] {
  const out: number[] = [];
  for (let n = 1; n <= 32; n++) {
    const byte = data[(n - 1) >> 3] ?? 0;
    if ((byte >> (7 - ((n - 1) & 7))) & 1) out.push(base + n);
  }
  return out;
}

export class Obd2Session {
  static readonly CLEAR_CONFIRM = CLEAR_CONFIRM;
  static readonly CLEAR_WARNING = CLEAR_WARNING;
  private supported = new Map<number, number[]>();

  constructor(readonly link: Iso9141Link) {}

  // -- Mode 01 / 02 --

  /** PIDs the ECU says it supports (walks the 00, 20, 40.. bitmaps). */
  async supportedPids(mode = 1): Promise<number[]> {
    const cached = this.supported.get(mode);
    if (cached) return [...cached];
    const found: number[] = [];
    for (let base = 0; base <= 0xc0; base += 0x20) {
      let data: Uint8Array;
      try {
        data = await this.pidData(mode, base);
      } catch (e) {
        if (e instanceof NoResponse) break;
        throw e;
      }
      const bits = Uint8Array.from({ length: 4 }, (_, i) => data[i] ?? 0);
      found.push(...bitmapPids(base, bits));
      if (!(bits[3]! & 1)) break;
    }
    this.supported.set(mode, found);
    return [...found];
  }

  private async pidData(mode: number, pid: number, frame = 0): Promise<Uint8Array> {
    const request = mode === 2 ? Uint8Array.of(mode, pid, frame) : Uint8Array.of(mode, pid);
    const skip = request.length;
    for (const reply of await this.link.request(request)) {
      let match = true;
      for (let i = 1; i < skip; i++) if (reply[i] !== request[i]) match = false;
      if (match) return reply.slice(skip);
    }
    throw new NoResponse(`reply to mode ${hex2(mode)} PID ${hex2(pid)} did not match the request`);
  }

  /** One PID, decoded. Throws NoResponse if the ECU does not answer it. */
  async readPid(pid: number, mode = 1, frame = 0): Promise<PidValue> {
    return decodePid(pid, await this.pidData(mode, pid, frame));
  }

  /** Raw data bytes of one Mode 01 PID (for the live poller and channelValue). */
  async readPidRaw(pid: number): Promise<Uint8Array> {
    return this.pidData(1, pid);
  }

  /** Every supported live PID (or the given ones). Skips the bitmaps, the
   *  monitor status and the freeze-frame DTC, which have their own calls. */
  async readPids(pids?: number[]): Promise<Map<number, PidValue>> {
    const list = pids ?? (await this.supportedPids()).filter((p) => p !== 0x01 && p !== 0x02 && p % 0x20);
    const out = new Map<number, PidValue>();
    for (const pid of list) {
      try {
        out.set(pid, await this.readPid(pid));
      } catch (e) {
        if (!(e instanceof NoResponse)) throw e;
      }
    }
    return out;
  }

  /** MIL state, number of stored codes, readiness monitors (PID 01). */
  async readiness(): Promise<Readiness> {
    return decodeReadiness(await this.pidData(1, 0x01));
  }

  /** Mode 02: the snapshot stored with the first emissions DTC, or null. */
  async readFreezeFrame(frame = 0): Promise<FreezeFrame | null> {
    let data: Uint8Array;
    try {
      data = await this.pidData(2, 0x02, frame);
    } catch (e) {
      if (e instanceof NoResponse) return null;
      throw e;
    }
    if (data.length < 2 || (data[0] === 0 && data[1] === 0)) return null;
    const out: FreezeFrame = { dtc: Obd2Session.dtc(data[0]!, data[1]!, 'stored'), values: new Map() };
    for (const pid of await this.supportedPids(2)) {
      if (pid === 0x01 || pid === 0x02 || pid % 0x20 === 0) continue;
      try {
        out.values.set(pid, await this.readPid(pid, 2, frame));
      } catch (e) {
        if (!(e instanceof NoResponse)) throw e;
      }
    }
    return out;
  }

  // -- Mode 03 / 07 / 04 --

  static dtc(hi: number, lo: number, status: Dtc['status']): Dtc {
    const code = decodeDtc(hi, lo);
    return { code, description: describeDtc(code), status };
  }

  private async dtcs(mode: number, status: Dtc['status']): Promise<Dtc[]> {
    let replies: Uint8Array[];
    try {
      replies = await this.link.request(Uint8Array.of(mode));
    } catch (e) {
      if (e instanceof NoResponse) return []; // many ECUs stay silent when there is nothing to report
      throw e;
    }
    const out: Dtc[] = [];
    for (const reply of replies) {
      const body = reply.slice(1);
      for (let i = 0; i + 1 < body.length; i += 2) {
        if (body[i] || body[i + 1]) {
          const d = Obd2Session.dtc(body[i]!, body[i + 1]!, status);
          if (!out.some((x) => x.code === d.code)) out.push(d);
        }
      }
    }
    return out;
  }

  /** Mode 03: stored (confirmed) trouble codes. */
  readDtcs(): Promise<Dtc[]> {
    return this.dtcs(0x03, 'stored');
  }

  /** Mode 07: codes seen once, not yet confirmed. */
  readPendingDtcs(): Promise<Dtc[]> {
    return this.dtcs(0x07, 'pending');
  }

  /**
   * Mode 04: erase codes, freeze frame and readiness. Destructive.
   *
   * Does nothing unless ``confirm === CLEAR_CONFIRM`` ("CLEAR", typed by the
   * user after reading CLEAR_WARNING). Refuses while the engine is turning
   * (PID 0C > 0) unless ``allowEngineRunning``. Resolves true when the ECU
   * acknowledged.
   */
  async clearDtcs(confirm = '', opts: { allowEngineRunning?: boolean } = {}): Promise<boolean> {
    if (!clearConfirmed(confirm)) {
      throw new ConfirmationRequired(`clearDtcs needs confirm='${CLEAR_CONFIRM}'. ` + CLEAR_WARNING);
    }
    if (!opts.allowEngineRunning) {
      const rpm = await this.engineRpm();
      if (rpm > 0) {
        throw new Iso9141Error(`engine is running (${rpm.toFixed(0)} rpm): clear codes with the key ON and the engine OFF`);
      }
    }
    try {
      await this.link.request(Uint8Array.of(0x04), 0);
    } catch (e) {
      if (e instanceof NoResponse) return false;
      throw e;
    }
    return true;
  }

  /** PID 0C, 0 when the ECU does not answer it (engine-off check). */
  async engineRpm(): Promise<number> {
    try {
      return (await this.readPid(0x0c)).value ?? 0;
    } catch (e) {
      if (e instanceof NoResponse) return 0;
      throw e;
    }
  }

  // -- Mode 05 / 06 --

  /** Mode 05 test ids the ECU supports (05 00 [01] bitmap), [] if none. */
  async supportedO2Tests(): Promise<number[]> {
    for (const req of [Uint8Array.of(0x05, 0x00, 0x01), Uint8Array.of(0x05, 0x00)]) {
      try {
        const replies = await this.link.request(req);
        const first = replies[0]!;
        if (first.length >= 6) return bitmapPids(0, first.slice(-4));
      } catch (e) {
        if (!(e instanceof NoResponse)) throw e;
      }
    }
    return [];
  }

  /** Mode 05: one O2 sensor monitoring test (sensor bit 0x01 = B1S1). */
  async readO2Test(tid: number, sensor = 0x01): Promise<O2TestResult> {
    const replies = await this.link.request(Uint8Array.of(0x05, tid, sensor));
    const r = replies.find((x) => x[1] === tid) ?? replies[0]!;
    const data = r.slice(3);
    const [name, units, scale] = O2_TESTS[tid] ?? [`Test ${hex2(tid)}`, '', 0];
    const sc = (v: number | undefined) => (v === undefined || !scale ? null : pyRound(v * scale, 3));
    return {
      tid, sensor, name, units, raw: r,
      value: scale ? sc(data[0]) : (data[0] ?? null),
      min: sc(data[1]), max: sc(data[2]),
    };
  }

  /** Mode 06 test ids the ECU supports (06 00 bitmap), [] if none. */
  async supportedMode06(): Promise<number[]> {
    try {
      const first = (await this.link.request(Uint8Array.of(0x06, 0x00)))[0]!;
      return first.length >= 6 ? bitmapPids(0, first.slice(-4)) : [];
    } catch (e) {
      if (e instanceof NoResponse) return [];
      throw e;
    }
  }

  /** Mode 06 (non-CAN): results for one test id. 46 TID CID/limtype VA VB LA LB */
  async readMode06(tid: number): Promise<Mode06Result[]> {
    const out: Mode06Result[] = [];
    for (const r of await this.link.request(Uint8Array.of(0x06, tid))) {
      if (r.length < 7) continue;
      const lim = r[2]!;
      const value = r[3]! * 256 + r[4]!;
      const limit = r[5]! * 256 + r[6]!;
      const limitKind = lim & 0x80 ? 'min' : 'max';
      out.push({
        tid: r[1]!, componentId: lim & 0x7f, value, limit, limitKind,
        pass: limitKind === 'max' ? value <= limit : value >= limit, raw: r,
      });
    }
    return out;
  }

  // -- everything at once --

  /** Readiness, codes, freeze frame and (optionally) every live PID. */
  async snapshot(live = true): Promise<Obd2Snapshot> {
    const snap = emptySnapshot();
    snap.init = this.link.init ? initText(this.link.init) : '';
    const steps: [string, () => Promise<void>][] = [
      ['supported PIDs', async () => void (snap.supportedPids = await this.supportedPids())],
      ['readiness', async () => void (snap.readiness = await this.readiness())],
      ['stored codes', async () => void (snap.stored = await this.readDtcs())],
      ['pending codes', async () => void (snap.pending = await this.readPendingDtcs())],
      ['freeze frame', async () => void (snap.freezeFrame = await this.readFreezeFrame())],
    ];
    if (live) steps.push(['live values', async () => void (snap.values = await this.readPids())]);
    for (const [name, step] of steps) {
      try {
        await step();
      } catch (e) {
        if (!(e instanceof Iso9141Error)) throw e;
        snap.errors.push(`${name}: ${e.message}`);
      }
    }
    return snap;
  }
}
