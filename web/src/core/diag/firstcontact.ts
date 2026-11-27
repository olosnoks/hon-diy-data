/**
 * First contact with the car: which protocol answers, and how well. Port of
 * hondalog/diag/firstcontact.py.
 *
 * ``runFirstContact`` does, in order:
 *
 *  1. adapter check (what the browser can tell: USB id, chip)
 *  2. open the port
 *  3. echo test: one byte out, expect it back within 20 ms (+ USB latency).
 *     No echo at all means no 12 V at the OBD2 plug or a broken cable.
 *  4. passive listen: is anything else talking on the K-line?
 *  5. Honda read of 0x00 without the wake-up bytes, then with them
 *  6. if no answer: the same read parsed with alternate reply framings
 *  7. if Honda answers: ECU id, block-length probe, timing probe, and a
 *     0x00-0xFF sweep
 *  8. ISO 9141-2 5-baud init at 0x33 (fast init as fallback), then readiness,
 *     stored and pending codes, freeze frame and the supported Mode 01 PIDs
 *
 * Everything sent is a read request, the wake-up bytes, an init, or an OBD2
 * read service: nothing writes, resets or clears. The report (markdown) has a
 * plain verdict, every finding, and the full byte trace with timestamps.
 */

import type { AddressMap, Transport } from '../types';
import { ascii, fmtHex } from '../obd2/bytes';
import { REAL, type Clock } from '../obd2/clock';
import { initText, Iso9141Error, Iso9141Link } from '../obd2/iso9141';
import { Obd2Session, snapshotText } from '../obd2/obd2';
import {
  detectAdapter,
  detectPlatform,
  udevInstallHint,
  usbId,
  WINDOWS_LATENCY_HINT,
  type AdapterInfo,
  type HostPlatform,
} from './adapter';
import {
  buildReadRequest,
  findReply,
  framingFromMap,
  ProtocolError,
  SimpleHondaLink,
  type HondaFraming,
  type HondaLink,
} from './honda';
import {
  blockSummary,
  probeBlockLength,
  probeTiming,
  sweep,
  timingOk,
  timingSummary,
  type BlockLengthProbe,
  type Sweep,
  type TimingProbe,
} from './probe';
import { cell, fileStamp, fmtWhen, type ReportFiles } from './report';
import { TracingTransport } from './trace';

export const HONDA_OK = 'Honda protocol OK';
export const ISO_ONLY = 'ISO only';
export const CABLE_DEAD = 'cable dead';
export const ECU_SILENT = 'ECU silent';
export const NO_PORT = 'no port';
export type Verdict = typeof HONDA_OK | typeof ISO_ONLY | typeof CABLE_DEAD | typeof ECU_SILENT | typeof NO_PORT;

export const NEXT_STEPS: Record<Verdict, readonly string[]> = {
  [HONDA_OK]: [
    'Copy the suggested `[protocol]` settings below into the address map (Setup > Address map).',
    'Connect on F1 Live. Check RPM, TPS and battery against reality first.',
    'Use the RAM sweep below and F7 Bytes to verify the address map.',
  ],
  [ISO_ONLY]: [
    'Use the OBD2 functions (codes, freeze frame, readiness, PIDs); they work now.',
    'For fast logging try the Honda protocol on the 3-pin service connector instead of OBD2 pin 7, and run this again.',
    'Send this report and the .hex.log along if you ask for help.',
  ],
  [CABLE_DEAD]: [
    'Check the plug is fully seated and the car battery is connected.',
    'Measure 12 V between OBD2 pin 16 and pin 4/5 (fuse if missing).',
    'Try the cable on another car or another cable on this car.',
  ],
  [ECU_SILENT]: [
    'Key to ON (II), not ACC. Try again with the engine running.',
    "Check that the cable's K-line is on OBD2 pin 7 and that pin 7 is populated in the car's plug.",
    'Look at the byte trace: any RX bytes after the echo are a reply in an unknown format; send the .hex.log along if you ask for help.',
  ],
  [NO_PORT]: [
    'lsusb | grep 0403 and sudo dmesg | tail: does Linux see the cable?',
    'groups: are you in dialout? (sudo usermod -aG dialout $USER, then log out and in)',
    'If dmesg mentions brltty: sudo apt remove brltty, then replug.',
  ],
};

/** NO_PORT advice on Windows (no udev, no dialout group there). */
export const NO_PORT_WINDOWS: readonly string[] = [
  "Device Manager > Ports (COM & LPT): is the cable listed (e.g. USB Serial Port (COM21))? If it shows under Other devices instead, install the FTDI VCP driver.",
  'Close any other program that may hold the port (a terminal, another hondalog, a vendor scan tool), then run this again.',
  'Pick the port again in the port chooser.',
];

/** NO_PORT advice in a browser or on Android. */
export const NO_PORT_WEB: readonly string[] = [
  'Use Chrome or Edge on a desktop: Web Serial is not available in other browsers.',
  'Close any other tab or program that may hold the port, unplug and replug the cable, then pick it again in the port chooser.',
  'On Android use the hondalog app (USB OTG adapter), not the browser.',
];

export function nextSteps(verdict: Verdict, platform: HostPlatform = detectPlatform()): readonly string[] {
  if (verdict === NO_PORT) {
    if (platform === 'windows') return NO_PORT_WINDOWS;
    if (platform !== 'linux') return NO_PORT_WEB;
  }
  return NEXT_STEPS[verdict] ?? [];
}

export const ECHO_BYTE = 0x55; // not a Honda command byte; an ECU ignores it
export const ECHO_WAIT_S = 0.02;

/** reply framings tried when the default gets no valid reply: [label, TOML overrides] */
export const ALT_FRAMINGS: readonly [string, Record<string, number>][] = [
  ['any reply header byte', { reply_header: -1 }],
  ['reply length byte = data length', { reply_header: -1, reply_len_extra: 0 }],
  ['reply length byte = data length + 2', { reply_header: -1, reply_len_extra: 2 }],
  ['reply length byte = data length + 4', { reply_header: -1, reply_len_extra: 4 }],
];

function applyOverrides(f: HondaFraming, o: Record<string, number>): HondaFraming {
  return {
    ...f,
    replyHeader: o['reply_header'] ?? f.replyHeader,
    replyLenExtra: o['reply_len_extra'] ?? f.replyLenExtra,
  };
}

export interface Step {
  name: string;
  /** null = skipped / not applicable */
  ok: boolean | null;
  detail: string;
  tMs: number;
}

export class FirstContactReport {
  verdict: Verdict = ECU_SILENT;
  summary = '';
  steps: Step[] = [];
  adapter: AdapterInfo | null = null;
  echo = false;
  echoMs: number | null = null;
  busChatter: Uint8Array = new Uint8Array(0);
  hondaNoInit = false;
  hondaWithInit = false;
  hondaReply: Uint8Array = new Uint8Array(0);
  hondaFraming = '';
  /** what came back to the default request */
  rawReply: Uint8Array = new Uint8Array(0);
  ecuId: Uint8Array = new Uint8Array(0);
  blockProbe: BlockLengthProbe | null = null;
  timing: TimingProbe | null = null;
  sweep: Sweep | null = null;
  isoInit = '';
  isoOk = false;
  obd2Text = '';
  /** suggested [protocol] settings, snake_case TOML keys */
  recommended: Record<string, number | string> = {};
  trace = '';

  constructor(readonly port: string, readonly started: Date = new Date(), readonly platform: HostPlatform = detectPlatform()) {}

  step(name: string, ok: boolean | null, detail = '', tMs = 0): Step {
    const s = { name, ok, detail, tMs };
    this.steps.push(s);
    return s;
  }

  toMarkdown(): string {
    const out: string[] = [
      '# hondalog first contact',
      '',
      `- when: ${fmtWhen(this.started)}`,
      `- port: \`${this.port}\``,
      `- **verdict: ${this.verdict}**`,
      '',
      this.summary,
      '',
      '## Next steps',
      '',
      ...nextSteps(this.verdict, this.platform).map((t, i) => `${i + 1}. ${t}`),
      '',
      '## Steps',
      '',
      '| # | step | result | detail |',
      '|---|---|---|---|',
    ];
    this.steps.forEach((s, i) => {
      const mark = s.ok === null ? 'skipped' : s.ok ? 'OK' : 'FAIL';
      out.push(`| ${i + 1} | ${s.name} | ${mark} | ${cell(s.detail)} |`);
    });
    const a = this.adapter;
    if (a) {
      out.push(
        '', '## Adapter', '',
        `- kind: ${a.kind}, driver: ${a.driver || '-'}, USB id: ${usbId(a)}`,
        `- chip: ${a.chip || '-'}`,
        `- verdict: ${a.verdict}`,
        `- latency timer: ${a.latencyMs ?? '-'} ms`,
        ...a.notes.map((n) => `- ${n}`),
      );
      if (a.isFtdi && (a.latencyMs === null || a.latencyMs > 1)) {
        if (this.platform === 'windows') {
          out.push('', 'Set the latency timer to 1 ms (once; Windows keeps it):', '', WINDOWS_LATENCY_HINT + '.');
        } else if (this.platform === 'linux') {
          out.push('', 'Make the 1 ms latency timer permanent:', '', '```sh', udevInstallHint(), '```');
        }
      }
    }
    if (this.ecuId.length) out.push('', '## ECU id', '', `\`${fmtHex(this.ecuId)}\`  "${ascii(this.ecuId)}"`);
    if (this.rawReply.length && !this.hondaReply.length) {
      out.push('', '## Raw reply to the default read request', '', `\`${fmtHex(this.rawReply)}\``);
    }
    if (this.blockProbe) out.push('', '## Block length', '', blockSummary(this.blockProbe));
    if (this.timing) {
      out.push('', '## Link timing', '', timingSummary(this.timing), ...this.timing.notes.map((n) => `- ${n}`));
      if (this.timing.gapErrors.size) {
        out.push('- errors per inter-request gap: ' + [...this.timing.gapErrors].map(([g, e]) => `${+(1000 * g).toFixed(6)} ms: ${e}`).join(', '));
      }
    }
    const rec = Object.entries(this.recommended);
    if (rec.length) {
      out.push('', '## Suggested `[protocol]` settings for the address map', '', '```toml', '[protocol]');
      for (const [k, v] of rec) out.push(typeof v === 'string' ? `${k} = "${v}"` : `${k} = ${v}`);
      out.push('```');
    }
    if (this.sweep) {
      out.push('', '## RAM sweep 0x00-0xFF', '', `${this.sweep.answered} of 256 addresses answered.`, '', '```', this.sweep.hexdump(), '```');
      const runs = this.sweep.asciiRuns();
      if (runs.length) {
        out.push('', 'Readable text (ECU id or part number?): ' + runs.map(([ad, t]) => `${ad.toString(16).toUpperCase().padStart(2, '0')}: "${t}"`).join(', '));
      }
    }
    if (this.isoInit || this.obd2Text) {
      out.push('', '## ISO 9141-2', '', `init: ${this.isoInit || 'failed'}`, '');
      if (this.obd2Text) out.push('```', this.obd2Text.trimEnd(), '```');
    }
    out.push(
      '', '## Byte trace', '',
      'Times in ms from the first event. TX = sent, RX = received (our own echo included), ' +
        'BREAK = line held low / released, BAUD = speed change.',
      '', '```', this.trace.trimEnd(), '```', '',
    );
    return out.join('\n');
  }

  /** The .md and .hex.log the UI saves (Python writes the same two files). */
  files(): ReportFiles {
    const stamp = fileStamp(this.started);
    return {
      mdName: `firstcontact-${stamp}.md`,
      md: this.toMarkdown(),
      hexName: `firstcontact-${stamp}.hex.log`,
      hex:
        `# hondalog first contact, ${fmtWhen(this.started)}, port ${this.port}, verdict: ${this.verdict}\n` +
        '# ms from first event, direction, bytes\n' + this.trace,
    };
  }
}

/** Send one byte; true if it comes back. Returns [echo, ms until it did]. */
export async function echoTest(t: TracingTransport, clock: Clock = REAL, waitS = ECHO_WAIT_S): Promise<[boolean, number | null]> {
  await t.drain();
  const t0 = clock.now();
  await t.write(Uint8Array.of(ECHO_BYTE));
  const deadline = t0 + 10 / Math.max(1, t.baud) + waitS + 2 * t.usbLatencyS;
  const got: number[] = [];
  while (clock.now() < deadline) {
    const chunk = await t.read(16, (deadline - clock.now()) * 1000);
    if (chunk.length) {
      got.push(...chunk);
      if (got.includes(ECHO_BYTE)) return [true, 1000 * (clock.now() - t0)];
    }
  }
  return [false, null];
}

/** Record whatever is on the line without sending anything. */
export async function listen(t: Transport, seconds: number, clock: Clock = REAL): Promise<Uint8Array> {
  const end = clock.now() + seconds;
  const got: number[] = [];
  while (clock.now() < end) got.push(...(await t.read(256, (end - clock.now()) * 1000)));
  return Uint8Array.from(got);
}

async function tryRead(link: HondaLink, addr: number, length: number, tries = 2): Promise<Uint8Array | null> {
  for (let i = 0; i < tries; i++) {
    try {
      return (await link.readBlock(addr, length, 0)).data;
    } catch (e) {
      if (!(e instanceof ProtocolError)) throw e;
    }
  }
  return null;
}

export interface FirstContactOptions {
  transport: Transport;
  /** Address map: its [protocol] and [ecu_id]. Default: the stock P2P. */
  map?: Pick<AddressMap, 'protocol' | 'ecuId'> | null;
  /** Framing override (wins over the map's). */
  framing?: HondaFraming;
  clock?: Clock;
  iso?: boolean;
  quick?: boolean;
  listenS?: number;
  progress?: (text: string) => void;
  platform?: HostPlatform;
  /** Honda link to use (default SimpleHondaLink). */
  makeLink?: (t: Transport, f: HondaFraming, clock: Clock) => HondaLink;
  /** The port is open already: skip open() (and leave it open afterwards). */
  alreadyOpen?: boolean;
}

/** Run the whole first-contact sequence. Never throws for link problems;
 *  everything ends up in the report. */
export async function runFirstContact(o: FirstContactOptions): Promise<FirstContactReport> {
  const clock = o.clock ?? REAL;
  const framing = o.framing ?? framingFromMap(o.map);
  const ecuId = o.map?.ecuId ?? { addr: 0x76, length: 10 };
  const platform = o.platform ?? detectPlatform();
  const report = new FirstContactReport(o.transport.label, new Date(), platform);
  const say = (text: string) => o.progress?.(text);
  const ms = (t0: number) => 1000 * (clock.now() - t0);

  say('checking the adapter');
  let t = clock.now();
  const a = (report.adapter = detectAdapter(o.transport));
  report.step('adapter', ['usb-serial', 'serial', 'simulator'].includes(a.kind), `${a.chip || a.kind}, ${a.verdict}`, ms(t));

  const tr = new TracingTransport(o.transport, clock);
  say('opening the port');
  t = clock.now();
  try {
    if (!o.alreadyOpen) await tr.open({ baud: framing.baud });
    else if (!tr.baud) tr.baud = framing.baud;
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    report.step('open port', false, msg, ms(t));
    report.verdict = NO_PORT;
    report.summary =
      `Could not open ${report.port}: ${msg}. Is the cable plugged in, and is ` +
      (platform === 'linux' ? 'your user in the dialout group?' : 'the port free (no other program using it)?');
    report.trace = tr.dump();
    return report;
  }
  if (a.isFtdi) {
    report.step('latency timer 1 ms', null, 'latency_timer cannot be read from here; set it to 1 ms, see Adapter below');
  }
  report.step('open port', true, `USB latency ${+(1000 * tr.usbLatencyS).toFixed(3)} ms (assumed)`, ms(t));

  try {
    await runOpen(report, tr, framing, ecuId, clock, o, say);
  } catch (e) {
    // Transport errors (cable pulled, port lost) end the run but not the report.
    const msg = e instanceof Error ? e.message : String(e);
    report.step('port', false, `port failed during the test: ${msg}`);
    report.verdict = NO_PORT;
    report.summary = `The port stopped working during the test (${msg}). Cable pulled?`;
  } finally {
    if (!o.alreadyOpen) {
      try {
        await tr.close();
      } catch {
        // already gone
      }
    }
  }
  report.trace = tr.dump();
  return report;
}

async function runOpen(
  report: FirstContactReport,
  tr: TracingTransport,
  framing: HondaFraming,
  ecuId: { addr: number | null; length: number },
  clock: Clock,
  o: FirstContactOptions,
  say: (text: string) => void,
): Promise<void> {
  const listenS = o.listenS ?? 2.0;
  const makeLink = o.makeLink ?? ((t, f, c) => new SimpleHondaLink(t, f, c));

  // 3. echo
  say('echo test');
  tr.note('echo test');
  [report.echo, report.echoMs] = await echoTest(tr, clock);
  report.step('echo test', report.echo,
    report.echoMs !== null ? `echo after ${report.echoMs.toFixed(1)} ms` : 'no echo: no 12 V at the OBD2 plug, or a broken cable');

  // 4. passive listen
  if (listenS > 0) {
    say(`listening for ${listenS} s`);
    tr.note('passive listen');
    await clock.sleep(0.05);
    await tr.drain();
    report.busChatter = await listen(tr, listenS, clock);
    const quiet = !report.busChatter.length;
    report.step('passive listen', quiet, quiet ? `line quiet for ${listenS} s`
      : `${report.busChatter.length} bytes heard: something else is talking (another tool on the line?)`);
  }

  // 5. Honda read without, then with, the wake-up bytes
  say('Honda read of 0x00');
  tr.note('Honda read 0x00, no wake-up bytes');
  const link = makeLink(tr, framing, clock);
  let data = await tryRead(link, 0x00, 2);
  report.hondaNoInit = data !== null;
  report.step('Honda read 0x00 (no wake-up)', report.hondaNoInit, data ? fmtHex(data) : 'no valid reply');
  if (data === null && framing.initSequence.length) {
    tr.note('wake-up bytes, then Honda read 0x00');
    await link.wake();
    data = await tryRead(link, 0x00, 2);
    report.hondaWithInit = data !== null;
    report.step('Honda read 0x00 (after wake-up)', report.hondaWithInit, data ? fmtHex(data) : 'no valid reply');
  }
  if (data) {
    report.hondaReply = data;
    report.hondaFraming = 'default';
  }

  // 6. alternate framings
  if (data === null && report.echo) {
    say('trying alternate reply framings');
    const request = buildReadRequest(0x00, 2, framing);
    tr.note('default read, raw capture');
    report.rawReply = (await link.raw(request, 0.08, 0.5))[1];
    let found = '';
    for (const [label, overrides] of ALT_FRAMINGS) {
      const hit = report.rawReply.length ? findReply(report.rawReply, 2, applyOverrides(framing, overrides)) : null;
      if (hit) {
        found = label;
        report.hondaReply = hit;
        report.hondaFraming = label;
        Object.assign(report.recommended, overrides);
        break;
      }
    }
    report.step('alternate framings', report.rawReply.length ? Boolean(found) : false,
      found ? `valid with: ${found}`
        : report.rawReply.length ? `raw reply ${fmtHex(report.rawReply)} fits no framing tried` : 'nothing came back to parse');
  }

  if (report.hondaReply.length && report.hondaFraming === 'default') {
    await hondaDetails(report, tr, link, framing, ecuId, o.quick ?? false, say);
  }

  // 8. ISO 9141-2
  const iso = o.iso ?? true;
  if (iso && report.echo) await isoStep(report, tr, framing, clock, say);
  else if (iso) report.step('ISO 9141-2 init', null, 'skipped: no echo, the line is dead');

  verdict(report);
}

async function hondaDetails(
  report: FirstContactReport,
  tr: TracingTransport,
  link: HondaLink,
  framing: HondaFraming,
  ecuId: { addr: number | null; length: number },
  quick: boolean,
  say: (text: string) => void,
): Promise<void> {
  if (ecuId.addr !== null) {
    say('reading the ECU id');
    tr.note('ECU id');
    const ident = await tryRead(link, ecuId.addr, ecuId.length);
    report.ecuId = ident ?? new Uint8Array(0);
    report.step('ECU id', ident !== null, ident ? fmtHex(ident) : `no reply at ${ecuId.addr.toString(16).toUpperCase().padStart(2, '0')}`);
  }
  say('probing the longest block read');
  tr.note('block-length probe');
  const bp = (report.blockProbe = await probeBlockLength(link));
  report.step('block-length probe', bp.maxReadLen > 0, blockSummary(bp));
  if (bp.maxReadLen && bp.maxReadLen !== framing.maxReadLen) report.recommended['max_read_len'] = bp.maxReadLen;
  if (quick) {
    report.step('timing probe', null, 'skipped (quick)');
    report.step('RAM sweep', null, 'skipped (quick)');
    return;
  }
  say('measuring ECU turnaround (200 reads)');
  tr.note('timing probe');
  const tp = (report.timing = await probeTiming(link));
  report.step('timing probe', timingOk(tp), timingSummary(tp));
  for (const key of ['reply_timeout_s', 'inter_request_delay_s']) {
    const v = tp.recommended[key];
    if (v !== undefined) report.recommended[key] = v;
  }
  say('sweeping 0x00-0xFF');
  tr.note('RAM sweep 0x00-0xFF');
  report.sweep = await sweep(link, 0, 0xff, Math.min(16, bp.maxReadLen || 1));
  report.step('RAM sweep', report.sweep.answered > 0, `${report.sweep.answered} of 256 addresses answered`);
}

async function isoStep(
  report: FirstContactReport,
  tr: TracingTransport,
  framing: HondaFraming,
  clock: Clock,
  say: (text: string) => void,
): Promise<void> {
  say('ISO 9141-2 5-baud init (about 3 s)');
  tr.note('ISO 9141-2 init');
  const iso = new Iso9141Link(tr, { clock, hondaBaud: framing.baud, trace: (s) => tr.note(s), usbLatencyS: tr.usbLatencyS });
  let init;
  try {
    init = await iso.connect(true);
  } catch (e) {
    if (!(e instanceof Iso9141Error)) throw e;
    report.step('ISO 9141-2 init', false, e.message);
    await iso.close();
    return;
  }
  report.isoOk = true;
  report.isoInit = initText(init);
  report.step('ISO 9141-2 init', true, `${initText(init)}, sync after ${(1000 * init.w1S).toFixed(0)} ms`
    + (init.notes.length ? `; ${init.notes.join('; ')}` : ''));
  say('reading codes, freeze frame and PIDs');
  try {
    const snap = await new Obd2Session(iso).snapshot(true);
    report.obd2Text = snapshotText(snap);
    const codes = snap.stored.map((d) => d.code).join(', ') || 'none';
    report.step('OBD2 snapshot', !snap.errors.length,
      `stored codes: ${codes}; ${snap.supportedPids.length} PIDs supported` + (snap.errors.length ? `; ${snap.errors.length} errors` : ''));
  } finally {
    await iso.close();
  }
}

function verdict(report: FirstContactReport): void {
  if (report.hondaReply.length) {
    report.verdict = HONDA_OK;
    const how = report.hondaFraming === 'default' ? 'with the default framing' : `with an alternate framing (${report.hondaFraming})`;
    const init = report.hondaNoInit ? 'without' : 'only after';
    report.summary = `The ECU answers Honda RAM reads on this pin ${how}, ${init} the wake-up bytes. hondalog can log at full speed.`;
    if (report.isoOk) report.summary += ' ISO 9141-2 works too, for trouble codes and readiness.';
  } else if (report.isoOk) {
    report.verdict = ISO_ONLY;
    report.summary =
      'The ECU answers standard OBD2 (ISO 9141-2) but not Honda RAM reads on this pin. Codes, freeze frame and ' +
      'readiness work; live data is limited to about 4-8 PIDs per second. Honda reads may need the 3-pin service ' +
      'connector instead of OBD2 pin 7.';
  } else if (!report.echo) {
    report.verdict = CABLE_DEAD;
    report.summary =
      'Nothing came back, not even our own transmission. The K-line has no pull-up: check 12 V on OBD2 pin 16 ' +
      '(fuse), ground on pins 4/5, and the cable.';
  } else {
    report.verdict = ECU_SILENT;
    report.summary =
      'The cable works (echo OK) but the ECU answered neither the Honda protocol nor ISO 9141-2. Is the key in ' +
      'ON (II)? Is the cable on pin 7?';
  }
}
