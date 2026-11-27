/**
 * Read-only ECU probe: what else does this ECU answer on the K-line? Port of
 * hondalog/diag/ecuprobe.py. About 30-60 s, two stages:
 *
 *  A. One ISO 9141-2 session (5-baud init at 0x33, kept alive). Standard OBD2
 *     read services (Mode 01 bitmaps, 05 O2 tests, 06, 09 vehicle info, 02
 *     freeze frame), then manufacturer-range READ services (0x21, 0x22, 0x23,
 *     0x1A), each with a short timeout and an early stop after repeated
 *     silence. 0x7F negative replies are recorded with their NRC.
 *  B. The Honda request variants in ``HONDA_CANDIDATES``, at their own baud
 *     rate, with or without the wake-up bytes. By default B runs first, on a
 *     line nothing has talked on since key on; otherwise after the ISO
 *     session has timed out (> 5.5 s of silence).
 *
 * Nothing is sent that is not on the whitelist (``checkIso``, ``checkHonda``):
 * every frame passes through ``guardedIso`` or ``guardedHonda`` first.
 */

import type { Transport } from '../types';
import { ascii, concat, fmtHex, fromHex, hex2, sum } from '../obd2/bytes';
import { REAL, type Clock } from '../obd2/clock';
import { initText, Iso9141Error, Iso9141Link, split9141, splitKwp } from '../obd2/iso9141';
import { stripEcho } from './honda';
import { cell, fileStamp, fmtWhen, type ReportFiles } from './report';
import { TracingTransport } from './trace';

// ---- Honda candidate table (edit here, no code changes needed) -------------

export interface HondaCandidate {
  name: string;
  baud: number;
  /** quiet time before this entry, s */
  preDelayS: number;
  /** wake-up bytes sent first, or null */
  wake: Uint8Array | null;
  /** request bytes (checksum excluded if addChecksum) */
  request: Uint8Array;
  addChecksum?: boolean;
  replyLen?: number | null;
  /** MUST be true or the entry is refused */
  readOnly?: boolean;
  wakeDelayS?: number;
}

export const WAKE = fromHex('686af5afbfb3b2c1dbb3e9');

const READS: [string, string, number][] = [
  ['Honda read 00/2', '20050002', 5],
  ['Honda read row 00', '20050010', 19],
  ['Honda read row 10', '20051010', 19],
  ['Honda read row 20', '20052010', 19],
  ['Honda read ECU id 76', '2005760a', 13],
  ['Honda read batt 17', '20051701', 4],
];

/** Honda RAM read, kerpz/ArduinoHondaOBD: cmd 20, 05, addr, count, two's
 *  complement checksum; reply count+3 bytes. Only cmd 20 (read) is allowed:
 *  cmd 21 resets the ECU. Same order as the Python table. */
export const HONDA_CANDIDATES: readonly HondaCandidate[] = [9600, 10400].flatMap((baud) =>
  [null, WAKE].flatMap((wake) =>
    READS.map(([name, hex, replyLen]) => ({
      name: `${name} @${baud}${wake ? ' + wake' : ''}`,
      baud,
      preDelayS: 0.05,
      wake,
      request: fromHex(hex),
      replyLen,
      readOnly: true,
    })),
  ),
);

// ---- whitelist ---------------------------------------------------------------

/** ISO / KWP services the probe may send (all reads) */
export const ISO_ALLOWED: ReadonlySet<number> = new Set([0x01, 0x02, 0x03, 0x05, 0x06, 0x07, 0x09, 0x1a, 0x21, 0x22, 0x23]);
/** refused outright, named here so the test can check each one */
export const ISO_DENIED: ReadonlySet<number> = new Set([0x04, 0x08, 0x11, 0x14, 0x27, 0x2e, 0x2f, 0x30, 0x31, 0x34, 0x35, 0x36, 0x37, 0x3b, 0x3d]);
export const DEFAULT_SESSION = 0x81;

/** A request that is not on the read-only whitelist. */
export class ProbeBlocked extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ProbeBlocked';
  }
}

/** Throw ProbeBlocked unless ``payload`` (service byte first) is a read. */
export function checkIso(payload: Uint8Array): void {
  if (!payload.length) throw new ProbeBlocked('empty request');
  const sid = payload[0]!;
  if (sid === 0x10) {
    if (payload.length === 2 && payload[1] === DEFAULT_SESSION) return;
    throw new ProbeBlocked(`blocked ${fmtHex(payload)}: 0x10 only with the default session`);
  }
  if (ISO_DENIED.has(sid) || !ISO_ALLOWED.has(sid)) {
    throw new ProbeBlocked(`blocked ${fmtHex(payload)}: service ${hex2(sid)} is not a whitelisted read`);
  }
}

export function hondaFrame(c: HondaCandidate): Uint8Array {
  return c.addChecksum === false ? Uint8Array.from(c.request) : concat(c.request, [-sum(c.request) & 0xff]);
}

export function checkHonda(c: HondaCandidate): void {
  if (!c.readOnly) throw new ProbeBlocked(`blocked table entry '${c.name}': not marked read_only`);
  if (!c.request.length) throw new ProbeBlocked(`blocked table entry '${c.name}': empty request`);
  if (c.request[0] !== 0x20) {
    // 0x20 is the Honda memory read; 0x21 resets the ECU and clears its
    // learned values, and nothing else is known to be a read
    throw new ProbeBlocked(`blocked table entry '${c.name}': only Honda read (20) commands are sent`);
  }
}

// ---- results -----------------------------------------------------------------

export const NRC: Record<number, string> = {
  0x10: 'generalReject', 0x11: 'serviceNotSupported', 0x12: 'subFunctionNotSupported',
  0x13: 'incorrectMessageLength', 0x21: 'busyRepeatRequest', 0x22: 'conditionsNotCorrect',
  0x31: 'requestOutOfRange', 0x33: 'securityAccessDenied', 0x78: 'responsePending',
  0x7e: 'subFunctionNotSupportedInActiveSession', 0x80: 'serviceNotSupportedInActiveSession',
};

export const MANUFACTURER: ReadonlySet<number> = new Set([0x1a, 0x21, 0x22, 0x23]);

export const POSITIVE = 'positive';
export const OTHER = 'unparsed bytes';
export const NEGATIVE = 'negative';
export const SILENT = 'silent';
export const BLOCKED = 'blocked';
export const SKIPPED = 'skipped';
export type ExchangeKind = typeof POSITIVE | typeof OTHER | typeof NEGATIVE | typeof SILENT | typeof BLOCKED | typeof SKIPPED;

export interface Exchange {
  stage: string;
  service: string;
  request: Uint8Array;
  kind: ExchangeKind;
  /** what came back after our echo */
  raw: Uint8Array;
  replies: Uint8Array[];
  nrc: number | null;
  meaning: string;
  tMs: number;
}

function exchange(stage: string, service: string, request: Uint8Array, kind: ExchangeKind, extra: Partial<Exchange> = {}): Exchange {
  return { stage, service, request, kind, raw: new Uint8Array(0), replies: [], nrc: null, meaning: '', tMs: 0, ...extra };
}

export function rank(e: Exchange): number {
  if (e.kind === POSITIVE && (e.stage.startsWith('B') || (e.request.length && MANUFACTURER.has(e.request[0]!)))) return 0;
  return { [OTHER]: 1, [POSITIVE]: 2, [NEGATIVE]: 3, [SILENT]: 4, [BLOCKED]: 5, [SKIPPED]: 6 }[e.kind];
}

export class ProbeReport {
  exchanges: Exchange[] = [];
  notes: string[] = [];
  isoInit = '';
  elapsedS = 0;
  trace = '';

  constructor(readonly port: string, readonly started: Date = new Date()) {}

  hits(): Exchange[] {
    return this.exchanges.filter((e) => e.kind === POSITIVE || e.kind === OTHER);
  }

  toMarkdown(): string {
    const n = (k: ExchangeKind) => this.exchanges.filter((e) => e.kind === k).length;
    const out = [
      '# hondalog ECU probe', '',
      `- when: ${fmtWhen(this.started)}`,
      `- port: \`${this.port}\``,
      `- ISO 9141-2 init: ${this.isoInit || 'failed'}`,
      `- run time: ${this.elapsedS.toFixed(1)} s`,
      `- requests: ${this.exchanges.length}; positive ${n(POSITIVE)}, unparsed bytes ${n(OTHER)}, ` +
        `negative (7F) ${n(NEGATIVE)}, silent ${n(SILENT)}, blocked ${n(BLOCKED)}, skipped ${n(SKIPPED)}`,
      '', 'Read-only: only whitelisted read services and read-only Honda table entries were sent.', '',
    ];
    if (this.notes.length) out.push('## Notes', '', ...this.notes.map((t) => `- ${t}`), '');
    out.push('## Summary (interesting hits first)', '', '| stage | service | request | reply | meaning |', '|---|---|---|---|---|');
    // a stable sort, as Python's sorted()
    const sorted = this.exchanges.map((e, i) => [e, i] as const).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]);
    for (const [e] of sorted) {
      const reply = e.replies.length ? e.replies.map(fmtHex).join(' / ') : e.raw.length ? 'raw ' + fmtHex(e.raw) : '-';
      out.push(`| ${e.stage} | ${e.service} | \`${fmtHex(e.request)}\` | \`${reply}\` | ${cell(e.meaning || e.kind)} |`);
    }
    out.push(
      '', '## Byte trace', '',
      'Times in ms from the first event. TX = sent, RX = received (our own echo included), ' +
        'BREAK = line held low / released, BAUD = speed change.',
      '', '```', this.trace.trimEnd(), '```', '',
    );
    return out.join('\n');
  }

  files(): ReportFiles {
    const stamp = fileStamp(this.started);
    return {
      mdName: `ecuprobe-${stamp}.md`,
      md: this.toMarkdown(),
      hexName: `ecuprobe-${stamp}.hex.log`,
      hex: `# hondalog ECU probe, ${fmtWhen(this.started)}, port ${this.port}\n# ms from first event, direction, bytes\n` + this.trace,
    };
  }
}

// ---- decoding ----------------------------------------------------------------

function bitmap(base: number, data: Uint8Array): string {
  const pids: number[] = [];
  for (let i = 0; i < 32; i++) if (((data[i >> 3] ?? 0) >> (7 - (i & 7))) & 1) pids.push(base + i + 1);
  return 'supported: ' + (pids.map(hex2).join(' ') || 'none');
}

export function describeReply(payload: Uint8Array, replies: Uint8Array[]): string {
  const sid = payload[0]!;
  const first = replies[0]!;
  if (sid === 0x01 && payload.length === 2 && payload[1]! % 0x20 === 0 && first.length >= 6) {
    return 'Mode 01 ' + bitmap(payload[1]!, first.slice(2));
  }
  if ((sid === 0x05 || sid === 0x06 || sid === 0x09) && payload.length >= 2 && payload[1] === 0 && first.length >= 6) {
    return `Mode ${hex2(sid)} ` + bitmap(0, first.slice(-4));
  }
  if (sid === 0x09 && payload.length === 2 && (payload[1] === 0x02 || payload[1] === 0x04)) {
    const text = replies.map((r) => ascii(r.slice(3))).join('');
    return (payload[1] === 0x02 ? 'VIN ' : 'CALID ') + `"${text}"`;
  }
  const data = concat(...replies.map((r) => r.slice(1)));
  const text = ascii(data);
  const printable = [...text].filter((c) => c !== '.').length;
  return `${data.length} data bytes` + (printable >= 4 ? `, text "${text}"` : '');
}

// ---- the probe ---------------------------------------------------------------

export const ISO_TIMEOUT_S = 0.25; // first-byte wait for standard services
export const MFR_TIMEOUT_S = 0.2; // first-byte wait for manufacturer services
export const SILENT_STOP = 4; // consecutive silent replies that end a manufacturer stage
export const NRC11_STOP = 3; // consecutive serviceNotSupported that end a stage
export const KEEPALIVE_AFTER_S = 3.0;
export const SESSION_GAP_S = 5.6; // silence that ends the ISO session before stage B

export type IsoPlan = [string, Uint8Array[], boolean][];

/** [stage name, payloads, stop early on silence] */
export function isoPlan(): IsoPlan {
  const b = (...v: number[]) => Uint8Array.from(v);
  const m05 = [b(0x05, 0x00), b(0x05, 0x00, 0x01)];
  for (const s of [0x01, 0x02]) for (let tid = 0x01; tid < 0x0b; tid++) m05.push(b(0x05, tid, s));
  const m23: Uint8Array[] = [];
  for (const addr of [0x00, 0x10, 0x20]) {
    m23.push(
      b(0x23, 0x00, 0x00, addr, 0x04), // KWP2000: 3-byte addr, len
      b(0x23, 0x00, addr, 0x04), // 2-byte addr, len
      b(0x23, addr, 0x04), // 1-byte addr, len
      b(0x23, 0x12, 0x00, addr, 0x04), // UDS ALFID 12: 2-byte addr, 1-byte len
    );
  }
  return [
    ['A1 Mode 01 bitmaps', [b(1, 0), b(1, 0x20), b(1, 0x40)], false],
    ['A2 Mode 05 O2 tests', m05, false],
    ['A3 Mode 06', [b(6, 0), b(6, 1), b(6, 2), b(6, 3), b(6, 5), b(6, 0x21)], false],
    ['A4 Mode 09', [b(9, 0), b(9, 2), b(9, 4)], false],
    ['A5 Mode 02 freeze frame', [b(2, 2, 0), b(2, 0x0c, 0)], false],
    ['A6 0x1A read ECU id', [b(0x1a, 0x80), b(0x1a, 0x90)], true],
    ['A7 0x21 local id', Array.from({ length: 16 }, (_, i) => b(0x21, i)), true],
    ['A8 0x22 data id', [b(0x22, 0, 0), b(0x22, 1, 0), b(0x22, 0xf1, 0x90)], true],
    ['A9 0x23 read memory', m23, true],
  ];
}

class Prober {
  iso: Iso9141Link | null = null;
  lastOk = 0;

  constructor(
    readonly tr: TracingTransport,
    readonly clock: Clock,
    readonly report: ProbeReport,
    readonly deadline: number,
    readonly say: (text: string) => void,
    readonly plan: IsoPlan,
    readonly candidates: readonly HondaCandidate[],
  ) {}

  // -- ISO stage --

  /** The only path that puts an ISO frame on the wire. */
  async guardedIso(payload: Uint8Array, timeoutS: number): Promise<[Uint8Array, Uint8Array[]]> {
    checkIso(payload);
    const iso = this.iso!;
    const wait = iso.nextTx - this.clock.now();
    if (wait > 0) await this.clock.sleep(wait);
    const frame = iso.frame(payload);
    await this.tr.drain();
    await this.tr.write(frame);
    const buf = await iso.collect(frame.length, timeoutS);
    let [rest] = stripEcho(buf, frame);
    const split = (x: Uint8Array) => (iso.kwp ? splitKwp(x) : split9141(x));
    let replies = split(rest);
    // 7F xx 78 = response pending: keep listening up to 1 s
    let extra = 0;
    for (;;) {
      const last = replies[replies.length - 1];
      if (!last || last[0] !== 0x7f || last[2] !== 0x78 || extra >= 4) break;
      extra += 1;
      const more = await iso.collect(0, 0.25);
      if (!more.length) break;
      rest = concat(rest, more);
      replies = split(rest);
    }
    const now = this.clock.now();
    iso.nextTx = now;
    if (replies.length) {
      iso.lastActivity = now;
      this.lastOk = now;
    }
    return [rest, replies];
  }

  async keepalive(): Promise<boolean> {
    if (this.clock.now() - this.lastOk < KEEPALIVE_AFTER_S) return true;
    this.tr.note('keepalive 01 00');
    const [, replies] = await this.guardedIso(Uint8Array.of(0x01, 0x00), ISO_TIMEOUT_S);
    if (replies.some((r) => r[0] === 0x41)) return true;
    this.tr.note('keepalive got no reply; re-initialising');
    return this.init();
  }

  async init(): Promise<boolean> {
    this.iso = new Iso9141Link(this.tr, { clock: this.clock, trace: (s) => this.tr.note(s), usbLatencyS: this.tr.usbLatencyS });
    try {
      const init = await this.iso.slowInit();
      this.report.isoInit = this.report.isoInit || initText(init);
    } catch (e) {
      if (!(e instanceof Iso9141Error)) throw e;
      this.report.notes.push(`ISO 9141-2 5-baud init failed: ${e.message}`);
      return false;
    }
    this.lastOk = this.clock.now();
    return true;
  }

  async stageA(): Promise<void> {
    this.say('ISO 9141-2 5-baud init (about 3 s)');
    this.tr.note('stage A: ISO 9141-2 init');
    if (!(await this.init())) return;
    let alive = true;
    for (const [name, payloads, early] of this.plan) {
      this.say(name);
      this.tr.note(name);
      let silent = 0;
      let nrc11 = 0;
      for (let i = 0; i < payloads.length; i++) {
        const payload = payloads[i]!;
        const svc = hex2(payload[0] ?? 0);
        if (!alive || this.clock.now() > this.deadline) {
          const why = !alive ? 'ISO session lost' : 'time budget';
          this.report.exchanges.push(exchange(name, svc, payload, SKIPPED, { meaning: `skipped: ${why}` }));
          continue;
        }
        if (early && (silent >= SILENT_STOP || nrc11 >= NRC11_STOP)) {
          this.report.notes.push(`${name}: stopped after ${i} requests (` +
            (silent >= SILENT_STOP ? 'repeated silence' : 'repeated serviceNotSupported') + ')');
          break;
        }
        if (!(await this.keepalive())) {
          alive = false;
          this.report.notes.push(`${name}: ISO session lost, rest skipped`);
          this.report.exchanges.push(exchange(name, svc, payload, SKIPPED, { meaning: 'skipped: ISO session lost' }));
          continue;
        }
        const ex = await this.oneIso(name, payload, early ? MFR_TIMEOUT_S : ISO_TIMEOUT_S);
        this.report.exchanges.push(ex);
        silent = ex.kind === SILENT ? silent + 1 : 0;
        nrc11 = ex.nrc === 0x11 ? nrc11 + 1 : 0;
      }
    }
    if (this.iso) await this.iso.close();
  }

  async oneIso(stage: string, payload: Uint8Array, timeoutS: number): Promise<Exchange> {
    const svc = hex2(payload[0] ?? 0);
    const t0 = this.clock.now();
    let raw: Uint8Array;
    let replies: Uint8Array[];
    try {
      [raw, replies] = await this.guardedIso(payload, timeoutS);
    } catch (e) {
      if (e instanceof ProbeBlocked) return exchange(stage, svc, payload, BLOCKED, { meaning: e.message });
      throw e;
    }
    const ex = exchange(stage, svc, payload, SILENT, { raw, replies, tMs: 1000 * (this.clock.now() - t0) });
    const want = (payload[0]! + 0x40) & 0xff;
    const good = replies.filter((r) => r[0] === want);
    const neg = replies.filter((r) => r[0] === 0x7f && r[2] !== 0x78);
    if (good.length) {
      ex.kind = POSITIVE;
      ex.replies = good;
      ex.meaning = describeReply(payload, good);
    } else if (neg.length) {
      ex.kind = NEGATIVE;
      ex.nrc = neg[0]!.length > 2 ? neg[0]![2]! : null;
      ex.meaning = ex.nrc !== null ? `7F NRC ${hex2(ex.nrc)} ${NRC[ex.nrc] ?? 'unknown'}` : '7F (no NRC byte)';
    } else if (replies.length || raw.length) {
      ex.kind = OTHER;
      ex.meaning = replies.length ? 'valid frame(s), unexpected service byte' : `${raw.length} bytes that are not a valid ISO frame`;
    } else {
      ex.meaning = 'no reply';
    }
    return ex;
  }

  // -- Honda stage --

  /** The only path that puts a Honda table entry on the wire. */
  async guardedHonda(c: HondaCandidate): Promise<Uint8Array> {
    checkHonda(c);
    const { tr, clock } = this;
    if (tr.baud !== c.baud) await tr.setBaud(c.baud);
    await clock.sleep(c.preDelayS);
    if (c.wake) {
      await tr.drain();
      await tr.write(c.wake);
      await clock.sleep(c.wakeDelayS ?? 0.3);
    }
    const frame = hondaFrame(c);
    await tr.drain();
    await tr.write(frame);
    const start = clock.now();
    let last = start + (10 * frame.length) / c.baud;
    let buf: Uint8Array = new Uint8Array(0);
    for (;;) {
      const now = clock.now();
      if (now - start > 0.5 || (now > last && now - last > 0.08)) break;
      const chunk = await tr.read(64, 20);
      if (chunk.length) {
        buf = concat(buf, chunk);
        last = clock.now();
      }
    }
    return stripEcho(buf, frame)[0];
  }

  async stageB(first = false): Promise<void> {
    if (first) {
      // a fresh line: the ECU may lock to whichever protocol talks first after
      // key on, so the Honda reads go before any ISO init
      this.tr.note('stage B first: Honda reads before any ISO 9141-2 init');
      await this.clock.sleep(0.5);
    } else {
      this.say(`waiting ${SESSION_GAP_S} s for the ISO session to time out`);
      this.tr.note(`stage B: ${SESSION_GAP_S} s silence so the ISO session ends`);
      await this.clock.sleep(SESSION_GAP_S);
    }
    for (const c of this.candidates) {
      const name = 'B Honda';
      if (this.clock.now() > this.deadline + 10) {
        this.report.exchanges.push(exchange(name, c.name, hondaFrame(c), SKIPPED, { meaning: 'skipped: time budget' }));
        continue;
      }
      this.say(c.name);
      this.tr.note(c.name);
      const t0 = this.clock.now();
      let raw: Uint8Array;
      try {
        raw = await this.guardedHonda(c);
      } catch (e) {
        if (e instanceof ProbeBlocked) {
          this.report.exchanges.push(exchange(name, c.name, hondaFrame(c), BLOCKED, { meaning: e.message }));
          continue;
        }
        throw e;
      }
      const ex = exchange(name, c.name, hondaFrame(c), SILENT, { raw, tMs: 1000 * (this.clock.now() - t0) });
      if (raw.length) {
        const ok = (sum(raw) & 0xff) === 0;
        ex.kind = ok ? POSITIVE : OTHER;
        ex.meaning = `${raw.length} bytes` + (ok ? ', checksum OK' : ', checksum does not sum to 0') +
          (c.replyLen ? `, expected ${c.replyLen}` : '');
      } else {
        ex.meaning = 'no reply after the echo';
      }
      this.report.exchanges.push(ex);
    }
  }
}

export interface ProbeOptions {
  transport: Transport;
  clock?: Clock;
  budgetS?: number;
  progress?: (text: string) => void;
  /** run stage B (the Honda variants) */
  honda?: boolean;
  /** stage B before stage A (default) */
  hondaFirst?: boolean;
  /** baud the port is opened at (stage A switches to 10400 itself) */
  openBaud?: number;
  alreadyOpen?: boolean;
  /** test hooks: replace the ISO plan or the Honda table (still guarded) */
  plan?: IsoPlan;
  candidates?: readonly HondaCandidate[];
}

/** Run both stages. Never throws for link problems. */
export async function runEcuProbe(o: ProbeOptions): Promise<ProbeReport> {
  const clock = o.clock ?? REAL;
  const report = new ProbeReport(o.transport.label);
  const say = (text: string) => o.progress?.(text);
  const tr = new TracingTransport(o.transport, clock);
  const t0 = clock.now();
  try {
    if (!o.alreadyOpen) await tr.open({ baud: o.openBaud ?? 10400 });
    else if (!tr.baud) tr.baud = o.openBaud ?? 10400;
  } catch (e) {
    report.notes.push(`could not open ${report.port}: ${e instanceof Error ? e.message : String(e)}`);
    report.trace = tr.dump();
    return report;
  }
  // stage A must leave room for the 5.6 s gap and the Honda table
  const prober = new Prober(tr, clock, report, t0 + (o.budgetS ?? 85) - 15, say, o.plan ?? isoPlan(), o.candidates ?? HONDA_CANDIDATES);
  const honda = o.honda ?? true;
  try {
    if (honda && (o.hondaFirst ?? true)) {
      await prober.stageB(true);
      await prober.stageA();
    } else {
      await prober.stageA();
      if (honda) await prober.stageB();
    }
  } catch (e) {
    report.notes.push(`port failed during the probe: ${e instanceof Error ? e.message : String(e)}`);
  } finally {
    if (!o.alreadyOpen) {
      try {
        await tr.close();
      } catch {
        // already gone
      }
    }
  }
  report.elapsedS = clock.now() - t0;
  report.trace = tr.dump();
  return report;
}
