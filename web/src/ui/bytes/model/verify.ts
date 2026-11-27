/**
 * Guided verification: prove (or disprove) one channel at a time.
 * Port of hondalog/mapping/verify.py: the Script model, the eight check
 * kinds, judge(), VerificationSession and the shipped scripts (koeo,
 * tps_sweep, idle, steady_3000, warmup, drive, ac_switch).
 *
 * Nothing here marks a channel verified by itself: `confirmEdit` returns the
 * edited map text, and only the user's click applies it.
 */

import { changedBits, findValue, rankAgainstChannel, rankShape } from './correlate';
import { decodeWith, markChannel, type Evidence, type ParsedMap } from './mapdoc';
import { SweepRecording, type Mark } from './sweeps';

export const PASS = 'pass', PLAUSIBLE = 'plausible', FAIL = 'fail', SKIP = 'skip';
export type CheckStatus = typeof PASS | typeof FAIL | typeof SKIP;
export type Verdict = typeof PASS | typeof PLAUSIBLE | typeof FAIL | typeof SKIP;
const MIN_SAMPLES = 3;

export interface Phase { id: string; title: string; instruction: string; minS: number }
export interface Reference { name: string; prompt: string; units: string; default?: number }
export interface Question { name: string; prompt: string }
export interface Check {
  channel: string; kind: string; phase: string; text: string;
  params: Record<string, number | string>; weak: boolean; requires: string;
}
export interface Script {
  id: string; title: string; intro: string; phases: Phase[]; checks: Check[];
  references: Reference[]; questions: Question[];
}

export const scriptChannels = (s: Script) => [...new Set(s.checks.map((c) => c.channel))];

export interface CheckResult { check: Check; status: CheckStatus; measured: string; stats: Record<string, number> }
export interface ChannelVerdict { channel: string; status: Verdict; results: CheckResult[] }

export function verdictSummary(v: ChannelVerdict): string {
  return v.results.filter((r) => r.status !== SKIP).map((r) => `${r.check.text}: ${r.measured}`).join('; ').slice(0, 400);
}

export function resultLine(r: CheckResult): string {
  const mark = r.status === PASS ? 'ok  ' : r.status === FAIL ? 'FAIL' : 'skip';
  const weak = r.check.weak && r.status === PASS ? ' (plausibility only)' : '';
  return `${mark} ${r.check.text}: ${r.measured}${weak}`;
}

// ---- numbers ------------------------------------------------------------------

export function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const n = s.length;
  return n % 2 ? s[(n - 1) / 2]! : (s[n / 2 - 1]! + s[n / 2]!) / 2;
}

/** numpy.percentile, linear interpolation. */
export function percentile(xs: number[], q: number): number {
  const s = [...xs].sort((a, b) => a - b);
  const pos = ((s.length - 1) * q) / 100;
  const lo = Math.floor(pos), hi = Math.ceil(pos);
  return s[lo]! + (s[hi]! - s[lo]!) * (pos - lo);
}

/** Python's "{:.4g}" (no trailing zeros, exponent for big/small). */
export function g(v: number, digits = 4): string {
  if (!Number.isFinite(v)) return String(v);
  if (v === 0) return '0';
  const exp = Math.floor(Math.log10(Math.abs(v)));
  if (exp < -4 || exp >= digits) {
    const [m, e] = v.toExponential(digits - 1).split('e');
    const mant = m!.includes('.') ? m!.replace(/0+$/, '').replace(/\.$/, '') : m!;
    const n = Number(e);
    return `${mant}e${n < 0 ? '-' : '+'}${String(Math.abs(n)).padStart(2, '0')}`;
  }
  const s = v.toFixed(Math.max(0, digits - 1 - exp));
  return s.includes('.') ? s.replace(/0+$/, '').replace(/\.$/, '') : s;
}
const pct = (v: number) => `${Math.round(v * 100)}%`;

// ---- checks -----------------------------------------------------------------------

interface Data { series(channel: string, phase: string): number[]; session: VerificationSession }

function need(v: number[], n = MIN_SAMPLES): Omit<CheckResult, 'check'> | null {
  return v.length >= n ? null : { status: SKIP, measured: `only ${v.length} readings`, stats: {} };
}

type CheckFn = (c: Check, d: Data) => Omit<CheckResult, 'check'>;
const P = (c: Check, k: string) => c.params[k] as number;
const has = (c: Check, k: string) => c.params[k] !== undefined;

const range: CheckFn = (c, d) => {
  const v = d.series(c.channel, c.phase);
  const short = need(v); if (short) return short;
  const lo = P(c, 'lo'), hi = P(c, 'hi'), frac = has(c, 'frac') ? P(c, 'frac') : 0.9;
  const inside = v.filter((x) => x >= lo && x <= hi).length / v.length;
  const mn = Math.min(...v), mx = Math.max(...v), md = median(v);
  return { status: inside >= frac ? PASS : FAIL,
    measured: `${g(mn)} to ${g(mx)} (median ${g(md)}), ${pct(inside)} inside ${g(lo, 6)}..${g(hi, 6)}`,
    stats: { min: mn, max: mx, median: md, inside } };
};

const rise: CheckFn = (c, d) => {
  const v = d.series(c.channel, c.phase);
  const short = need(v, 6); if (short) return short;
  const falling = c.params.direction === 'fall';
  const w = falling ? v.map((x) => -x) : v;
  const k = Math.max(1, Math.floor(w.length / 20));
  const start = median(v.slice(0, k)), end = median(v.slice(-k));
  const swing = Math.max(...w) - Math.min(...w);
  let run = -Infinity, worst = 0;
  for (const x of w) { run = Math.max(run, x); worst = Math.max(worst, run - x); }
  const back = swing > 0 ? worst / swing : 1;
  const okStart = !has(c, 'start') || (falling ? start >= P(c, 'start') : start <= P(c, 'start'));
  const okEnd = !has(c, 'end') || (falling ? end <= P(c, 'end') : end >= P(c, 'end'));
  const okMove = swing >= (has(c, 'min_swing') ? P(c, 'min_swing') : 0);
  const ok = okStart && okEnd && okMove && back <= (has(c, 'max_back') ? P(c, 'max_back') : 0.2);
  return { status: ok ? PASS : FAIL, measured: `went ${g(start)} -> ${g(end)}, largest reversal ${pct(back)} of the swing`,
    stats: { start, end, swing, reversal: back } };
};

const steady: CheckFn = (c, d) => {
  const v = d.series(c.channel, c.phase);
  const short = need(v); if (short) return short;
  const span = percentile(v, 95) - percentile(v, 5);
  return { status: span <= P(c, 'max_span') ? PASS : FAIL,
    measured: `moved ${g(span)} (5th to 95th percentile), limit ${g(P(c, 'max_span'), 6)}`, stats: { span, median: median(v) } };
};

const nearChannel: CheckFn = (c, d) => {
  const a = d.series(c.channel, c.phase), b = d.series(String(c.params.other), c.phase);
  const short = need(a) ?? need(b); if (short) return short;
  const ma = median(a), mb = median(b);
  return { status: Math.abs(ma - mb) <= P(c, 'tol') ? PASS : FAIL,
    measured: `${g(ma)} vs ${c.params.other} ${g(mb)} (apart ${g(Math.abs(ma - mb), 3)}, allowed ${g(P(c, 'tol'), 6)})`,
    stats: { value: ma, other: mb, difference: ma - mb } };
};

const nearReference: CheckFn = (c, d) => {
  const name = String(c.params.ref);
  const ref = d.session.references[name];
  if (ref === undefined) return { status: SKIP, measured: `no ${name} reading given`, stats: {} };
  const v = d.series(c.channel, c.phase);
  const short = need(v); if (short) return short;
  const mid = median(v);
  const tol = Math.max(has(c, 'tol') ? P(c, 'tol') : 0, (has(c, 'rel_tol') ? P(c, 'rel_tol') : 0) * Math.abs(ref));
  const source = d.session.referenceSources[name] ?? name;
  return { status: Math.abs(mid - ref) <= tol ? PASS : FAIL,
    measured: `${g(mid)} vs ${source} ${g(ref, 6)} (apart ${g(Math.abs(mid - ref), 3)}, allowed ${g(tol, 3)})`,
    stats: { value: mid, reference: ref, difference: mid - ref } };
};

const switching: CheckFn = (c, d) => {
  const v = d.series(c.channel, c.phase);
  const short = need(v, 8); if (short) return short;
  const mid = P(c, 'mid');
  let crossings = 0;
  for (let i = 1; i < v.length; i++) if ((v[i]! > mid) !== (v[i - 1]! > mid)) crossings++;
  const mn = Math.min(...v), mx = Math.max(...v);
  const ok = crossings >= P(c, 'min_crossings') && mn <= P(c, 'lo') && mx >= P(c, 'hi');
  return { status: ok ? PASS : FAIL, measured: `crossed ${g(mid, 6)} ${crossings} times, range ${g(mn, 3)} to ${g(mx, 3)}`,
    stats: { crossings, min: mn, max: mx } };
};

const flag: CheckFn = (c, d) => {
  const v = d.series(c.channel, c.phase);
  const short = need(v); if (short) return short;
  const want = P(c, 'value');
  const share = v.filter((x) => Math.round(x) === want).length / v.length;
  return { status: share >= (has(c, 'frac') ? P(c, 'frac') : 0.9) ? PASS : FAIL,
    measured: `read ${want} in ${pct(share)} of readings`, stats: { share } };
};

const versusPhase: CheckFn = (c, d) => {
  const a = d.series(c.channel, c.phase), b = d.series(c.channel, String(c.params.other_phase));
  const short = need(a) ?? need(b); if (short) return short;
  const ma = median(a), mb = median(b), diff = ma - mb, margin = P(c, 'by');
  const ok = margin >= 0 ? diff >= margin : diff <= margin;
  const sign = diff >= 0 ? '+' : '';
  return { status: ok ? PASS : FAIL,
    measured: `${g(ma)} vs ${g(mb)} in '${c.params.other_phase}' (${sign}${g(diff)}, wanted at least ${g(Math.abs(margin), 6)} ${margin >= 0 ? 'above' : 'below'})`,
    stats: { value: ma, other: mb, difference: diff } };
};

const CHECKS: Record<string, CheckFn> = { range, rise, steady, near_channel: nearChannel, near_reference: nearReference,
  switching, flag, versus_phase: versusPhase };

export function runCheck(check: Check, data: Data): CheckResult {
  if (check.requires && !data.session.answers[check.requires]) {
    return { check, status: SKIP, measured: `only applies when: ${check.requires.replace(/_/g, ' ')}`, stats: {} };
  }
  try {
    const fn = CHECKS[check.kind];
    if (!fn) throw new Error(`unknown check ${check.kind}`);
    return { ...fn(check, data), check };
  } catch (e) {
    return { check, status: SKIP, measured: `could not be evaluated: ${(e as Error).message}`, stats: {} };
  }
}

export function judge(channel: string, results: CheckResult[]): ChannelVerdict {
  const done = results.filter((r) => r.status !== SKIP);
  let status: Verdict;
  if (!done.length) status = SKIP;
  else if (done.some((r) => r.status === FAIL)) status = FAIL;
  else if (done.some((r) => !r.check.weak)) status = PASS;
  else status = PLAUSIBLE;
  return { channel, status, results };
}

// ---- the session -----------------------------------------------------------------

export interface SessionReport {
  script: string; title: string; started: string; durationS: number; mapVersion: string; mapHash: string; variant: string;
  verdicts: Record<string, ChannelVerdict>; references: Record<string, number>; referenceSources: Record<string, string>;
  answers: Record<string, boolean>; phases: { id: string; t0: number; t1: number | null }[]; samples: number; sweeps: number;
}

export class VerificationSession {
  state: 'ready' | 'running' | 'done' = 'ready';
  references: Record<string, number> = {};
  referenceSources: Record<string, string> = {};
  answers: Record<string, boolean> = {};
  recording = new SweepRecording();
  report: SessionReport | null = null;
  readonly missingChannels: string[];
  private samples: Record<string, [number, Record<string, number | null>][]> = {};
  private index = -1;
  private t0 = 0;
  private phaseT0 = 0;
  private started = '';
  private mark: Mark | null = null;
  private log: { id: string; t0: number; t1: number | null }[] = [];

  constructor(readonly script: Script, public map: ParsedMap, readonly clock: () => number = () => performance.now() / 1000,
    readonly decodeSweeps = true) {
    for (const r of script.references) if (r.default !== undefined) this.references[r.name] = r.default;
    const names = new Set([...map.channels.map((c) => c.name), ...map.math.map((m) => m.name)]);
    this.missingChannels = scriptChannels(script).filter((c) => !names.has(c));
    this.recording.meta = { script: script.id };
  }

  now(): number { return this.clock() - this.t0; }
  get phase(): Phase | null { return this.state === 'running' ? this.script.phases[this.index]! : null; }
  get phaseIndex(): number { return this.index; }
  get phaseElapsed(): number { return this.state === 'running' ? this.now() - this.phaseT0 : 0; }
  get canAdvance(): boolean { const ph = this.phase; return ph !== null && this.phaseElapsed >= ph.minS; }

  start(): Phase {
    if (this.state !== 'ready') throw new Error('session already started');
    this.t0 = this.clock();
    this.started = new Date().toISOString().slice(0, 19);
    this.state = 'running';
    this.index = -1;
    return this.enter(0)!;
  }

  private enter(index: number): Phase | null {
    const now = this.now();
    if (this.mark) { this.mark.t1 = now; this.log[this.log.length - 1]!.t1 = now; }
    if (index >= this.script.phases.length) { this.mark = null; return null; }
    this.index = index;
    this.phaseT0 = now;
    const phase = this.script.phases[index]!;
    this.mark = { label: phase.id, t0: now, t1: null };
    this.recording.marks.push(this.mark);
    this.samples[phase.id] ??= [];
    this.log.push({ id: phase.id, t0: now, t1: null });
    return phase;
  }

  nextPhase(): Phase | null {
    if (this.state !== 'running') throw new Error('session is not running');
    return this.enter(this.index + 1);
  }

  setReference(name: string, value: number | null, source = ''): void {
    if (value === null || Number.isNaN(value)) { delete this.references[name]; delete this.referenceSources[name]; return; }
    this.references[name] = value;
    if (source) this.referenceSources[name] = source;
  }

  answer(question: string, yes: boolean): void { this.answers[question] = yes; }

  addSample(values: Record<string, number | null>, t?: number): void {
    const ph = this.phase;
    if (!ph || !this.mark) return;
    this.samples[ph.id]!.push([t ?? this.now(), values]);
  }

  /** One raw sweep: 256 values (-1 = unread), stamped now unless times are given. */
  addSweep(mem: ArrayLike<number>, times?: ArrayLike<number>): void {
    if (!this.phase) return;
    const now = this.now();
    const stamp = times ? Float64Array.from(times) : new Float64Array(256).fill(now);
    this.recording.append(mem, stamp);
    if (this.decodeSweeps) {
      const m = new Map<number, number>();
      for (let a = 0; a < 256; a++) if (mem[a]! >= 0) m.set(a, mem[a]!);
      this.addSample(decodeWith(this.map, m), Math.max(...stamp));
    }
  }

  series(channel: string, phase: string): number[] {
    return (this.samples[phase] ?? []).map(([, v]) => v[channel]).filter((v): v is number => v !== null && v !== undefined);
  }

  evaluate(): Record<string, ChannelVerdict> {
    const data: Data = { series: (c, p) => this.series(c, p), session: this };
    const grouped: Record<string, CheckResult[]> = {};
    for (const check of this.script.checks) {
      const result = this.missingChannels.includes(check.channel)
        ? { check, status: SKIP as CheckStatus, measured: 'the map has no such channel', stats: {} } : runCheck(check, data);
      (grouped[check.channel] ??= []).push(result);
    }
    return Object.fromEntries(Object.entries(grouped).map(([n, r]) => [n, judge(n, r)]));
  }

  finish(): SessionReport {
    if (this.state === 'ready') throw new Error('session was never started');
    if (this.state === 'running') { this.enter(this.script.phases.length); this.state = 'done'; }
    const last = this.log[this.log.length - 1];
    this.report = {
      script: this.script.id, title: this.script.title, started: this.started,
      durationS: Math.round((last?.t1 ?? this.now()) * 1000) / 1000,
      mapVersion: this.map.version, mapHash: this.map.hash, variant: this.map.variant, verdicts: this.evaluate(),
      references: { ...this.references }, referenceSources: { ...this.referenceSources }, answers: { ...this.answers },
      phases: this.log.map((p) => ({ ...p })), samples: Object.values(this.samples).reduce((a, s) => a + s.length, 0),
      sweeps: this.recording.length,
    };
    return this.report;
  }

  /** Other places this channel might live, from the recorded sweeps (labels, best first). */
  suggest(channel: string, top = 5): string[] {
    if (!this.recording.length) return [];
    const ch = this.map.channels.find((c) => c.name === channel);
    const order: Record<string, number> = { rise: 0, near_reference: 1, flag: 2, near_channel: 3 };
    const checks = this.script.checks.filter((c) => c.channel === channel && c.kind in order).sort((a, b) => order[a.kind]! - order[b.kind]!);
    const hex = (a: number) => `0x${a.toString(16).toUpperCase().padStart(2, '0')}`;
    for (const check of checks) {
      const mark = this.recording.marks.find((m) => m.label === check.phase);
      if (!mark) continue;
      const t1 = mark.t1 ?? this.now();
      const win = this.recording.window(mark.t0, t1);
      let found: string[] = [];
      if (check.kind === 'rise') {
        found = rankShape(win, (check.params.direction as 'rise' | 'fall') ?? 'rise', mark.t0, t1, { top })
          .map((m) => `${hex(m.candidate.addr)}${m.candidate.width === 2 ? ' u16' + (m.candidate.endian === 'big' ? 'be' : 'le') : ''} (score ${m.score.toFixed(2)})`);
      } else if (check.kind === 'near_reference') {
        const ref = this.references[String(check.params.ref)];
        if (ref !== undefined) {
          const tol = Math.max(has(check, 'tol') ? P(check, 'tol') : 0, (has(check, 'rel_tol') ? P(check, 'rel_tol') : 0) * Math.abs(ref));
          found = findValue(win, ref, ch?.formula ?? 'x', tol, 0, [1, 2], top).map((m) => `${hex(m.candidate.addr)} reads ${g(m.decoded)}`);
        }
      } else if (check.kind === 'flag') {
        const on = checks.find((c) => c.kind === 'flag' && c.params.value === 1);
        if (on) found = changedBits(this.recording, on.phase).slice(0, top).map((b) => `${hex(b.addr)} bit ${b.bit}`);
      } else {
        try {
          found = rankAgainstChannel(win, this.map, String(check.params.other), { top }).map((m) => `${hex(m.candidate.addr)} (score ${m.score.toFixed(2)})`);
        } catch { /* the other channel is not in the map */ }
      }
      if (found.length) return found;
    }
    return [];
  }

  evidence(channel: string, note = ''): Evidence {
    const report = this.report ?? this.finish();
    const verdict = report.verdicts[channel]!;
    const refs = [...new Set(verdict.results.filter((r) => r.check.kind === 'near_reference' && r.status !== SKIP).map((r) => String(r.check.params.ref)))].sort();
    return {
      date: report.started, method: `guided:${this.script.id}`, result: verdict.status,
      summary: verdictSummary(verdict) + (note ? ` | note: ${note}` : ''), map_version: report.mapVersion, map_hash: report.mapHash,
      reference: refs.map((r) => this.referenceSources[r] ?? r).join(', '),
    };
  }

  /** The user accepts a channel: the map text with it marked verified and the
   *  evidence attached. Refused unless it passed (force accepts plausible). */
  confirmEdit(channel: string, mapText: string, note = '', force = false): string {
    const report = this.report ?? this.finish();
    const v = report.verdicts[channel];
    if (!v) throw new Error(`script '${this.script.id}' does not check ${JSON.stringify(channel)}`);
    if (v.status === FAIL) throw new Error(`${channel} failed its check and cannot be marked verified`);
    if (v.status === SKIP) throw new Error(`${channel} was not checked (no data or no reference given)`);
    if (v.status === PLAUSIBLE && !force) {
      throw new Error(`${channel} only passed plausibility checks; that does not prove the address. Confirm anyway if you accept it.`);
    }
    return markChannel(mapText, channel, 'verified', this.evidence(channel, note));
  }

  /** Mark a channel suspect, with what was measured. */
  rejectEdit(channel: string, mapText: string, note = ''): string {
    const report = this.report ?? this.finish();
    if (!report.verdicts[channel]) throw new Error(`script '${this.script.id}' does not check ${JSON.stringify(channel)}`);
    return markChannel(mapText, channel, 'suspect', this.evidence(channel, note));
  }
}

// ---- the shipped scripts ---------------------------------------------------------

function c(channel: string, kind: string, phase: string, text: string, opts: { weak?: boolean; requires?: string } & Record<string, unknown> = {}): Check {
  const { weak = false, requires = '', ...params } = opts;
  return { channel, kind, phase, text, params: params as Record<string, number | string>, weak, requires };
}
const ph = (id: string, title: string, instruction: string, minS = 5): Phase => ({ id, title, instruction, minS });

const METER: Reference = { name: 'batt_meter', prompt: 'Battery voltage on a multimeter at the battery posts', units: 'V' };
const TACH: Reference = { name: 'tach_rpm', prompt: 'RPM shown on the dash tachometer', units: 'rpm' };
const AMBIENT: Reference = { name: 'ambient_c', prompt: 'Air temperature where the car is parked', units: '°C' };
const COLD: Question = { name: 'cold_engine', prompt: 'Has the engine been off for at least 6 hours?' };

export const KOEO: Script = {
  id: 'koeo', title: 'Key on, engine off',
  intro: 'Engine stopped, key in ON (II). Checks the readings that have a known value with the engine off.',
  phases: [ph('rest', 'Key on, engine off', 'Key to ON (II). Do not start the engine. Foot off the pedals. Everything electrical off.', 8)],
  checks: [
    c('rpm', 'range', 'rest', 'RPM reads zero with the engine off', { weak: true, lo: 0, hi: 50 }),
    c('vss', 'range', 'rest', 'speed reads zero parked', { weak: true, lo: 0, hi: 1 }),
    c('tps', 'range', 'rest', 'throttle reads closed', { weak: true, lo: -3, hi: 5 }),
    c('map', 'near_channel', 'rest', 'MAP equals barometric pressure with the engine off', { other: 'baro', tol: 4 }),
    c('map', 'range', 'rest', 'MAP is atmospheric', { weak: true, lo: 70, hi: 106 }),
    c('baro', 'range', 'rest', 'baro is atmospheric', { weak: true, lo: 70, hi: 106 }),
    c('batt', 'near_reference', 'rest', 'battery agrees with the meter', { ref: 'batt_meter', tol: 0.3 }),
    c('batt', 'range', 'rest', 'battery is a resting 12 V battery', { weak: true, lo: 11.0, hi: 13.3 }),
    c('ect', 'near_channel', 'rest', 'coolant equals intake air on a cold engine', { requires: 'cold_engine', other: 'iat', tol: 5 }),
    c('ect', 'near_reference', 'rest', 'coolant equals the air temperature on a cold engine', { requires: 'cold_engine', ref: 'ambient_c', tol: 5 }),
    c('iat', 'near_reference', 'rest', 'intake air equals the air temperature', { requires: 'cold_engine', ref: 'ambient_c', tol: 6 }),
    c('mil', 'flag', 'rest', 'MIL is lit for the bulb check', { weak: true, value: 1 }),
    c('closed_loop', 'flag', 'rest', 'not in closed loop', { weak: true, value: 0 }),
  ],
  references: [METER, AMBIENT], questions: [COLD],
};

export const TPS_SWEEP: Script = {
  id: 'tps_sweep', title: 'Throttle sweep (engine off)',
  intro: 'Engine stopped, key ON. The pedal is pressed slowly so the throttle reading can be watched from closed to wide open.',
  phases: [
    ph('released', 'Foot off', 'Key ON, engine off, foot off the accelerator.', 4),
    ph('sweep', 'Press slowly', 'Press the accelerator slowly and evenly to the floor, taking about 10 seconds. Do not pump it.', 8),
    ph('floor', 'Hold on the floor', 'Hold the pedal on the floor.', 3),
    ph('release', 'Let go', 'Release the pedal.', 3),
  ],
  checks: [
    c('tps', 'range', 'released', 'throttle under 5% with the pedal released', { lo: -3, hi: 5 }),
    c('tps', 'rise', 'sweep', 'throttle rises steadily from under 5% to over 90%', { start: 5, end: 90, max_back: 0.15 }),
    c('tps', 'range', 'floor', 'throttle over 90% on the floor', { lo: 90, hi: 106 }),
    c('tps', 'range', 'release', 'throttle returns to closed', { lo: -3, hi: 5, frac: 0.5 }),
    c('rpm', 'range', 'sweep', 'RPM stays zero', { weak: true, lo: 0, hi: 50 }),
  ],
  references: [], questions: [],
};

export const IDLE: Script = {
  id: 'idle', title: 'Warm idle',
  intro: 'Engine fully warm and idling in neutral with everything switched off.',
  phases: [ph('idle', 'Warm idle', 'Engine fully warm (radiator fan has run at least once), idling in neutral, A/C, lights and rear demister off. Hands off.', 30)],
  checks: [
    c('rpm', 'near_reference', 'idle', 'RPM agrees with the tachometer', { ref: 'tach_rpm', rel_tol: 0.1, tol: 60 }),
    c('rpm', 'range', 'idle', 'RPM is a normal idle', { weak: true, lo: 550, hi: 950 }),
    c('rpm', 'steady', 'idle', 'RPM is steady', { weak: true, max_span: 150 }),
    c('map', 'range', 'idle', 'MAP shows idle vacuum', { weak: true, lo: 20, hi: 48 }),
    c('batt', 'near_reference', 'idle', 'battery agrees with the meter', { ref: 'batt_meter', tol: 0.3 }),
    c('batt', 'range', 'idle', 'battery shows charging voltage', { weak: true, lo: 13.2, hi: 14.9 }),
    c('tps', 'range', 'idle', 'throttle reads closed', { weak: true, lo: -3, hi: 5 }),
    c('vss', 'range', 'idle', 'speed reads zero', { weak: true, lo: 0, hi: 1 }),
    c('ect', 'range', 'idle', 'coolant at operating temperature', { weak: true, lo: 75, hi: 105 }),
    c('inj', 'range', 'idle', 'injector pulse is an idle pulse', { weak: true, lo: 1.0, hi: 4.5 }),
    c('ign', 'range', 'idle', 'ignition advance is an idle value', { weak: true, lo: 5, hi: 25 }),
    c('o2', 'switching', 'idle', 'O2 sensor switches rich/lean in closed loop', { mid: 0.45, lo: 0.3, hi: 0.6, min_crossings: 4 }),
    c('closed_loop', 'flag', 'idle', 'closed loop is on', { weak: true, value: 1 }),
    c('stft', 'range', 'idle', 'short trim is near zero', { weak: true, lo: -15, hi: 15 }),
    c('ltft', 'range', 'idle', 'long trim is near zero', { weak: true, lo: -15, hi: 15 }),
    c('iacv', 'range', 'idle', 'idle valve is partly open', { weak: true, lo: 1, hi: 80 }),
  ],
  references: [TACH, METER], questions: [],
};

export const STEADY_3000: Script = {
  id: 'steady_3000', title: 'Steady 3000 rpm',
  intro: 'Idle, then hold 3000 rpm on the tachometer in neutral, then back to idle.',
  phases: [
    ph('idle', 'Idle', 'Warm engine idling in neutral.', 8),
    ph('hold', 'Hold 3000 rpm', 'Raise the engine speed to 3000 rpm on the tachometer and hold it as steady as you can.', 12),
    ph('back', 'Back to idle', 'Release the pedal and let it settle.', 5),
  ],
  checks: [
    c('rpm', 'near_reference', 'hold', 'RPM agrees with the tachometer', { ref: 'tach_rpm', rel_tol: 0.08 }),
    c('rpm', 'versus_phase', 'hold', 'RPM is well above idle', { other_phase: 'idle', by: 1500 }),
    c('rpm', 'steady', 'hold', 'RPM is steady while held', { weak: true, max_span: 400 }),
    c('tps', 'versus_phase', 'hold', 'throttle is open further than at idle', { weak: true, other_phase: 'idle', by: 1 }),
    c('ign', 'versus_phase', 'hold', 'ignition is more advanced than at idle', { weak: true, other_phase: 'idle', by: 3 }),
    c('batt', 'range', 'hold', 'battery shows charging voltage', { weak: true, lo: 13.2, hi: 14.9 }),
    c('vss', 'range', 'hold', 'speed reads zero', { weak: true, lo: 0, hi: 1 }),
  ],
  references: [{ name: 'tach_rpm', prompt: 'RPM held on the dash tachometer', units: 'rpm', default: 3000 }], questions: [],
};

export const WARMUP: Script = {
  id: 'warmup', title: 'Cold start to warm',
  intro: 'Starts with a cold engine and follows it to operating temperature.',
  phases: [
    ph('cold', 'Cold, engine off', 'Engine cold (off for 6+ hours). Key ON, engine off.', 8),
    ph('warming', 'Warm up', 'Start the engine and let it idle until the radiator fan comes on, or for 10 minutes. Leave the pedal alone.', 240),
  ],
  checks: [
    c('ect', 'near_channel', 'cold', 'coolant equals intake air before starting', { other: 'iat', tol: 5 }),
    c('ect', 'near_reference', 'cold', 'coolant equals the air temperature before starting', { ref: 'ambient_c', tol: 5 }),
    c('ect', 'rise', 'warming', 'coolant climbs steadily to over 70 °C', { end: 70, min_swing: 30, max_back: 0.1 }),
    c('iat', 'near_reference', 'cold', 'intake air equals the air temperature', { ref: 'ambient_c', tol: 6 }),
    c('iat', 'steady', 'warming', 'intake air changes far less than coolant', { weak: true, max_span: 30 }),
    c('rpm', 'rise', 'warming', 'fast idle comes down as it warms', { weak: true, direction: 'fall', min_swing: 150, max_back: 0.5 }),
    c('closed_loop', 'rise', 'warming', 'closed loop switches on once warm', { start: 0.2, end: 0.8, max_back: 1.0 }),
  ],
  references: [AMBIENT], questions: [],
};

export const DRIVE: Script = {
  id: 'drive', title: 'Steady road speed',
  intro: 'A passenger works the laptop. Hold a steady speed on a level road.',
  phases: [ph('cruise', 'Hold 60 km/h', 'Hold a steady 60 km/h (37 mph) by the speedometer, or type in the speed you are holding.', 10)],
  checks: [
    c('vss', 'near_reference', 'cruise', 'speed agrees with the speedometer', { ref: 'speedo_kmh', tol: 5, rel_tol: 0.08 }),
    c('vss', 'steady', 'cruise', 'speed is steady', { weak: true, max_span: 8 }),
    c('rpm', 'range', 'cruise', 'engine is running', { weak: true, lo: 1000, hi: 5000 }),
  ],
  references: [{ name: 'speedo_kmh', prompt: 'Speed held on the speedometer', units: 'km/h', default: 60 }], questions: [],
};

/** A script for any on/off input: off, on, off again. */
export function switchScript(channel: string, action: string, scriptId = '', title = ''): Script {
  const cap = action.slice(0, 1).toUpperCase() + action.slice(1);
  return {
    id: scriptId || `switch_${channel}`, title: title || `Switch test: ${action}`,
    intro: `Engine idling (or key ON). ${cap} is switched on and off while everything else is left alone.`,
    phases: [
      ph('off', 'Off', `Make sure ${action} is OFF. Touch nothing else.`, 5),
      ph('on', 'On', `Switch ON ${action} and leave it on.`, 6),
      ph('off_again', 'Off again', `Switch ${action} OFF again.`, 5),
    ],
    checks: [
      c(channel, 'flag', 'off', 'reads 0 while off', { value: 0 }),
      c(channel, 'flag', 'on', 'reads 1 while on', { value: 1, frac: 0.8 }),
      c(channel, 'flag', 'off_again', 'reads 0 again', { value: 0, frac: 0.8 }),
    ],
    references: [], questions: [],
  };
}

export const SCRIPTS: Record<string, Script> = Object.fromEntries(
  [KOEO, TPS_SWEEP, IDLE, STEADY_3000, WARMUP, DRIVE, switchScript('ac_sw', 'the A/C', 'ac_switch', 'A/C switch')].map((s) => [s.id, s]),
);

/** Scripts that check a channel, strongest evidence first. */
export function scriptsFor(channel: string): Script[] {
  const strength = (s: Script) => -s.checks.filter((c) => c.channel === channel && !c.weak).length;
  return Object.values(SCRIPTS).filter((s) => scriptChannels(s).includes(channel)).sort((a, b) => strength(a) - strength(b));
}

/** Every channel, its state, and how it can be checked. */
export function coverage(map: ParsedMap): { channel: string; label: string; status: string; strong: string[]; weak: string[] }[] {
  return map.channels.map((ch) => {
    const strong = Object.values(SCRIPTS).filter((s) => s.checks.some((c) => c.channel === ch.name && !c.weak)).map((s) => s.id);
    const weak = Object.values(SCRIPTS).filter((s) => scriptChannels(s).includes(ch.name) && !strong.includes(s.id)).map((s) => s.id);
    return { channel: ch.name, label: ch.label, status: ch.status, strong, weak };
  });
}
