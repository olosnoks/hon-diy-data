/**
 * The poke test analysis (tools/poke_test.py analyse, _analyse_group,
 * _timeline, labels_from_map, address_name): per step, every byte and bit
 * whose values in the hold windows differ from the rest windows around it,
 * edges at the switching moments, and the flag-bit timeline of engine-off
 * steps and owner marks.
 */

import type { ParsedMap } from '../model/mapdoc';
import { mannWhitneyP, fisherP, spearman, mean, sd } from './stats';
import { FLAG_ADDRS } from './steps';

export interface Sweep { t0: number; t1: number; vals: (number | null)[] }

export interface PhaseMark { step: string; rep: number; phase: string; t0: number; t1: number; [k: string]: unknown }
export interface EventMark { step: string; rep: number; label: string; t: number }
export interface StepMeta { name: string; prompt?: string; engine?: string; baseline?: boolean; repeat?: number;
  prompts?: string[]; setup?: string; fast?: string; pre?: string; [k: string]: unknown }
export interface Markers { steps: StepMeta[]; phases: PhaseMark[]; events?: EventMark[] }

export interface Finding {
  step: string; addr: number; bit: number | null; kind: 'shift' | 'excursion' | 'edge';
  base: string; during: string; effect: string; score: number; p: number; nHold: number; nBase: number;
  indirect: string; drift: string; name: string;
}

export const where = (f: Finding) => `0x${hx(f.addr)}${f.bit !== null ? ` b${f.bit}` : ''}`;
const hx = (a: number) => a.toString(16).toUpperCase().padStart(2, '0');

export interface StepResult {
  name: string; engine: string; prompt: string; repsDone: number; nHold: number; nBase: number;
  drivers: Record<string, string>; driverMoved: Record<string, boolean>;
  findings: Finding[]; edges: Finding[]; bitfrac: Record<string, [number, number]>;
  timeline: string[]; marks: string[]; note: string;
}

export interface Settings {
  alpha: number; minD: number; minDp: number; minExcFrac: number; minExcN: number; excP: number;
  minBase: number; sdFloor: number; indirectR: number;
}
export const DEFAULT_SETTINGS: Settings = { alpha: 1e-3, minD: 1.0, minDp: 0.5, minExcFrac: 0.2, minExcN: 2, excP: 0.01,
  minBase: 15, sdFloor: 0.5, indirectR: 0.6 };

export const EDGE_PHASES = ['wait', 'settle', 'undo_wait', 'undo_settle'];

// ---- labels -------------------------------------------------------------------------

export interface Labels {
  addresses: Record<string, { byte: string; bits: Record<string, string> }>;
  drivers: Record<string, { addr: number; length: number }>;
}

export const KNOWN_NOTES: Record<number, string> = {
  0x1c: 'copy of ELD (0x19)', 0x27: 'rpm band above idle', 0x29: 'IAC command', 0x2a: 'slow learned value',
  0x2f: '~33 s counter after ECT snap-back', 0x76: 'idle target [0]', 0x77: 'idle target [1]', 0x78: 'ECU id / constants',
  0x86: 'small signed table (follows STFT)', 0x9a: 'fuel system status', 0x9c: 'calculated load', 0xa0: 'second O2?',
};
export const KNOWN_BITS: [number, number, string][] = [
  [0x08, 0, 'starter'], [0x08, 2, 'psp_sw?'], [0x08, 7, 'gear input?'], [0x09, 3, 'scs?'], [0x0a, 4, 'gear input?'],
  [0x0a, 7, 'ecu_ready?'], [0x0b, 6, 'engine_turning?'], [0x0c, 0, 'elec load / alt ctrl?'], [0xa4, 0, 'idle state?'],
];

export const DEFAULT_LABELS: Labels = { addresses: {}, drivers: { rpm: { addr: 0, length: 2 }, ect: { addr: 0x10, length: 1 } } };

export function labelsFromMap(map: ParsedMap | null): Labels {
  const out: Labels['addresses'] = {};
  const slot = (a: number) => (out[`0x${hx(a)}`] ??= { byte: '', bits: {} });
  for (const [a, note] of Object.entries(KNOWN_NOTES)) slot(Number(a)).byte = note;
  for (const [a, b, note] of KNOWN_BITS) slot(a).bits[String(b)] = note;
  const drivers: Labels['drivers'] = {};
  for (const ch of map?.channels ?? []) {
    const m = /^\s*bit\(\s*x\s*,\s*(\d)\s*\)\s*$/.exec(ch.formula ?? '');
    if (m && ch.length === 1) { slot(ch.addr).bits[m[1]!] = ch.name; continue; }
    if (ch.name.startsWith('raw_')) continue;
    if (ch.length === 1) slot(ch.addr).byte = ch.name;
    else for (let i = 0; i < ch.length; i++) slot(ch.addr + i).byte = `${ch.name} [${i}]`;
    if (ch.name === 'rpm' || ch.name === 'ect') drivers[ch.name] = { addr: ch.addr, length: ch.length };
  }
  drivers.rpm ??= { addr: 0x00, length: 2 };
  drivers.ect ??= { addr: 0x10, length: 1 };
  return { addresses: out, drivers };
}

export function addressName(labels: Labels, addr: number, bit: number | null = null): string {
  const entry = labels.addresses[`0x${hx(addr)}`];
  if (!entry) return '';
  if (bit !== null) return entry.bits[String(bit)] ?? '';
  const names = [entry.byte, ...Object.keys(entry.bits).sort().map((k) => `b${k}=${entry.bits[k]}`)];
  return names.filter(Boolean).join(', ');
}

// ---- analysis ----------------------------------------------------------------------

const inside = (sw: Sweep, ph: PhaseMark) => sw.t0 >= ph.t0 - 1e-6 && sw.t1 <= ph.t1 + 1e-6;

function driverSeries(sweeps: Sweep[], drv: { addr: number; length: number }): (number | null)[] {
  return sweeps.map((sw) => {
    const bs = sw.vals.slice(drv.addr, drv.addr + drv.length);
    if (bs.some((b) => b === null || b === undefined)) return null;
    return (bs as number[]).reduce((a, b) => a * 256 + b, 0);
  });
}

const bitOf = (v: number | null | undefined, b: number) => (v === null || v === undefined ? null : (v >> b) & 1);
const pct0 = (v: number) => `${Math.round(v * 100)}%`;
const signedPct = (v: number) => `${v >= 0 ? '+' : '-'}${Math.round(Math.abs(v) * 100)}%`;

function summ(xs: number[]): string {
  const lo = Math.min(...xs), hi = Math.max(...xs);
  return lo === hi ? `${lo}` : `${mean(xs).toFixed(1)} (${lo}-${hi})`;
}

export function analyse(sweeps: Sweep[], markers: Markers, labels: Labels = DEFAULT_LABELS, settings: Partial<Settings> = {}): StepResult[] {
  const cfg = { ...DEFAULT_SETTINGS, ...settings };
  const phases = markers.phases ?? [];
  const stepsMeta = new Map((markers.steps ?? []).map((s) => [s.name, s]));
  const restIdx = phases.map((ph, i) => [ph, i] as const)
    .filter(([ph]) => ph.phase === 'rest' || (ph.phase === 'hold' && stepsMeta.get(ph.step)?.baseline)).map(([, i]) => i);
  const drivers = labels.drivers ?? DEFAULT_LABELS.drivers;
  const drvSeries: Record<string, (number | null)[]> = {};
  const drvAddrs: Record<string, Set<number>> = {};
  for (const [k, v] of Object.entries(drivers)) {
    drvSeries[k] = driverSeries(sweeps, v);
    drvAddrs[k] = new Set(Array.from({ length: v.length }, (_, i) => v.addr + i));
  }
  const sweepsOf = new Map<number, number[]>();
  sweeps.forEach((sw, k) => {
    const p = phases.findIndex((ph) => inside(sw, ph));
    if (p >= 0) { if (!sweepsOf.has(p)) sweepsOf.set(p, []); sweepsOf.get(p)!.push(k); }
  });
  const sweepsIn = (idxs: Iterable<number>) => [...new Set(idxs)].flatMap((i) => sweepsOf.get(i) ?? []).sort((a, b) => a - b);

  const results: StepResult[] = [];
  for (const st of markers.steps ?? []) {
    const name = st.name;
    if (st.baseline) continue;
    const mine = phases.map((ph, i) => [ph, i] as const).filter(([ph]) => ph.step === name).map(([, i]) => i);
    const holds = mine.filter((i) => phases[i]!.phase === 'hold');
    const variants = st.prompts ?? [];
    const groups: [string, string, number[]][] = [[name, st.prompt ?? '', holds]];
    if (new Set(variants).size > 1) {
      variants.forEach((text, v) => {
        groups.push([`${name}:${v + 1}`, text, holds.filter((i) => (phases[i]!.rep - 1) % variants.length === v)]);
      });
    }
    if (!holds.length) {
      results.push(emptyResult(name, st.engine ?? 'running', st.prompt ?? '', 'skipped or not reached'));
      continue;
    }
    const first = Math.min(...mine), last = Math.max(...mine);
    const ins = restIdx.filter((i) => i >= first && i <= last);
    const before = restIdx.filter((i) => i < first);
    const after = restIdx.filter((i) => i > last);
    const ownOnly = !!(st.setup || st.fast) && sweepsIn(ins).length >= cfg.minBase;
    const chosen = ownOnly ? [...ins] : [...ins, ...before.slice(-1), ...after.slice(0, 1)];
    const poolB = before.slice(0, -1).reverse(), poolA = after.slice(1);
    while (!ownOnly && sweepsIn(chosen).length < cfg.minBase && (poolB.length || poolA.length)) {
      if (poolB.length) chosen.push(poolB.shift()!);
      if (poolA.length && sweepsIn(chosen).length < cfg.minBase) chosen.push(poolA.shift()!);
    }
    const baseSw = sweepsIn(chosen);
    const preIdx = chosen.filter((i) => i < first);
    const preSw = sweepsIn(preIdx.length ? preIdx : chosen.filter((i) => phases[i]!.rep === 0));
    const postIdx = chosen.filter((i) => i > last);
    const postSw = sweepsIn(postIdx.length ? postIdx : chosen.filter((i) => phases[i]!.rep === Math.max(1, st.repeat ?? 1)));
    const excluded = new Set(sweepsIn(mine.filter((i) => phases[i]!.phase !== 'rest')));
    const outside = sweeps.map((_, k) => k).filter((k) => !excluded.has(k));
    const edgeSw = sweepsIn(mine.filter((i) => EDGE_PHASES.includes(phases[i]!.phase)));
    for (const [gname, gprompt, gholds] of groups) {
      const res = emptyResult(gname, st.engine ?? 'running', gprompt, '');
      res.repsDone = new Set(gholds.map((i) => phases[i]!.rep)).size;
      results.push(res);
      analyseGroup(res, sweeps, sweepsIn(gholds), baseSw, preSw, postSw, outside, gname === name ? edgeSw : [], cfg, labels,
        drvSeries, drvAddrs, drivers);
      if (gname === name) timeline(res, st, sweeps, phases, mine, markers.events ?? []);
    }
  }
  return results;
}

function emptyResult(name: string, engine: string, prompt: string, note: string): StepResult {
  return { name, engine, prompt, repsDone: 0, nHold: 0, nBase: 0, drivers: {}, driverMoved: {}, findings: [], edges: [],
    bitfrac: {}, timeline: [], marks: [], note };
}

function analyseGroup(res: StepResult, sweeps: Sweep[], holdSw: number[], baseSw: number[], preSw: number[], postSw: number[],
  outside: number[], edgeSw: number[], cfg: Settings, labels: Labels, drvSeries: Record<string, (number | null)[]>,
  drvAddrs: Record<string, Set<number>>, drivers: Labels['drivers']): void {
  res.nHold = holdSw.length;
  res.nBase = baseSw.length;
  if (baseSw.length < 5) { res.note = `too few rest sweeps (${baseSw.length})`; return; }
  if (holdSw.length < 3) { res.note = `too few hold sweeps (${holdSw.length})`; return; }
  const nn = (xs: (number | null)[]) => xs.filter((v): v is number => v !== null);
  for (const [dname, series] of Object.entries(drvSeries)) {
    const h = nn(holdSw.map((k) => series[k]!)), b = nn(baseSw.map((k) => series[k]!));
    if (b.length < 3) { res.drivers[dname] = 'no data'; res.driverMoved[dname] = false; continue; }
    if (h.length < 3) { res.drivers[dname] = 'no reply'; res.driverMoved[dname] = true; continue; }
    const scale = dname === 'rpm' && drivers[dname]!.length === 2 ? 0.25 : 1;
    const d = (mean(h) - mean(b)) / Math.max(Math.sqrt((sd(h) ** 2 + sd(b) ** 2) / 2), cfg.sdFloor);
    res.drivers[dname] = `${(mean(b) * scale).toFixed(0)} -> ${(mean(h) * scale).toFixed(0)}`;
    res.driverMoved[dname] = Math.abs(d) >= cfg.minD && mannWhitneyP(h, b) < cfg.alpha;
  }

  const indirectOf = (get: (k: number) => number | null, addr: number): string => {
    const notes: string[] = [];
    for (const [dname, moved] of Object.entries(res.driverMoved)) {
      if (!moved) continue;
      if (drvAddrs[dname]?.has(addr)) { notes.push(`is ${dname}`); continue; }
      const series = drvSeries[dname]!;
      const outD = nn(outside.map((k) => series[k]!));
      const hD = nn(holdSw.map((k) => series[k]!)), bD = nn(baseSw.map((k) => series[k]!));
      const jump = hD.length && bD.length ? Math.abs(mean(hD) - mean(bD)) : 0;
      let pool: number[] = outside, tag = '';
      if (!outD.length || Math.max(...outD) - Math.min(...outD) < jump / 2) { pool = sweeps.map((_, k) => k); tag = ' (this step only)'; }
      const xs: number[] = [], ys: number[] = [];
      for (const k of pool) {
        const v = get(k), dv = series[k]!;
        if (v !== null && dv !== null) { xs.push(v); ys.push(dv); }
      }
      const r = spearman(xs, ys);
      if (r !== null && Math.abs(r) >= cfg.indirectR) notes.push(`${dname} r=${r >= 0 ? '+' : ''}${r.toFixed(2)}${tag}`);
    }
    return notes.join('; ');
  };

  const column = (idx: number[], addr: number) => idx.map((k) => sweeps[k]!.vals[addr]).filter((v): v is number => v !== null && v !== undefined);

  for (let addr = 0; addr < 256; addr++) {
    const h = column(holdSw, addr), b = column(baseSw, addr);
    if (h.length < 3 || b.length < 5) continue;
    const pre = column(preSw, addr), post = column(postSw, addr);
    if (FLAG_ADDRS.includes(addr)) {
      for (let bit = 0; bit < 8; bit++) {
        res.bitfrac[`0x${hx(addr)}.${bit}`] = [b.filter((v) => (v >> bit) & 1).length / b.length, h.filter((v) => (v >> bit) & 1).length / h.length];
      }
    }
    const mh = mean(h), mb = mean(b);
    const d = (mh - mb) / Math.max(Math.sqrt((sd(h) ** 2 + sd(b) ** 2) / 2), cfg.sdFloor);
    const p = mannWhitneyP(h, b);
    const lo = Math.min(...b), hi = Math.max(...b);
    const exc = h.filter((v) => v < lo || v > hi).length;
    let byteHit: ['shift' | 'excursion', number] | null = null;
    if (Math.abs(d) >= cfg.minD && p < cfg.alpha) byteHit = ['shift', Math.min(1, Math.abs(d) / 5)];
    else if (exc >= cfg.minExcN && exc / h.length >= cfg.minExcFrac && p < cfg.excP) byteHit = ['excursion', (0.9 * exc) / h.length];

    const bitHits: Finding[] = [];
    for (let bit = 0; bit < 8; bit++) {
      const kh = h.filter((v) => (v >> bit) & 1).length, kb = b.filter((v) => (v >> bit) & 1).length;
      const ph = kh / h.length, pb = kb / b.length, dp = ph - pb;
      const pf = fisherP(kh, h.length, kb, b.length);
      let kind: 'shift' | 'excursion' | null = null, score = 0;
      if (Math.abs(dp) >= cfg.minDp && pf < cfg.alpha) { kind = 'shift'; score = Math.abs(dp); } else if (pb === 0 || pb === 1) {
        const odd = pb === 0 ? kh : h.length - kh;
        if (odd >= cfg.minExcN && pf < cfg.excP && (odd / h.length >= cfg.minExcFrac || pf < cfg.alpha)) { kind = 'excursion'; score = (0.9 * odd) / h.length; }
      }
      if (!kind) continue;
      let drift = '';
      if (pre.length >= 3 && post.length >= 3) {
        const fa = pre.filter((v) => (v >> bit) & 1).length / pre.length, fb = post.filter((v) => (v >> bit) & 1).length / post.length;
        if (Math.abs(fa - fb) >= Math.max(0.5, Math.abs(dp) / 2)) drift = `set ${pct0(fa)} before, ${pct0(fb)} after`;
      }
      bitHits.push({ step: res.name, addr, bit, kind, base: `${pct0(pb)} set`, during: `${pct0(ph)} set`, effect: signedPct(dp), score, p: pf,
        nHold: h.length, nBase: b.length, indirect: indirectOf((k) => bitOf(sweeps[k]!.vals[addr], bit), addr), drift,
        name: addressName(labels, addr, bit) });
    }

    if (byteHit && !(bitHits.length === 1 && new Set([...h, ...b]).size <= 2)) {
      let drift = '';
      if (pre.length >= 3 && post.length >= 3) {
        const ma = mean(pre), mp = mean(post);
        if (Math.abs(ma - mp) >= Math.max(Math.abs(mh - mb) / 2, 2 * cfg.sdFloor)) drift = `mean ${ma.toFixed(1)} before, ${mp.toFixed(1)} after`;
      }
      res.findings.push({ step: res.name, addr, bit: null, kind: byteHit[0], base: summ(b), during: summ(h),
        effect: byteHit[0] === 'shift' ? `d=${d >= 0 ? '+' : ''}${d.toFixed(1)}` : `${exc}/${h.length} outside ${lo}-${hi}`,
        score: byteHit[1], p, nHold: h.length, nBase: b.length,
        indirect: indirectOf((k) => sweeps[k]!.vals[addr] ?? null, addr), drift, name: addressName(labels, addr) });
    }
    res.findings.push(...bitHits);

    const e = column(edgeSw, addr);
    if (e.length >= cfg.minExcN) {
      for (let bit = 0; bit < 8; bit++) {
        const kb = b.filter((v) => (v >> bit) & 1).length;
        if (kb !== 0 && kb !== b.length) continue;
        const state = kb ? 0 : 1;
        const odd = e.filter((v) => ((v >> bit) & 1) === state).length;
        if (odd < cfg.minExcN) continue;
        const ke = e.filter((v) => (v >> bit) & 1).length;
        const pf = fisherP(ke, e.length, kb, b.length);
        if (pf >= cfg.alpha && odd / e.length < cfg.minExcFrac) continue;
        if (bitHits.some((f) => f.addr === addr && f.bit === bit)) continue;
        res.edges.push({ step: res.name, addr, bit, kind: 'edge', base: `always ${1 - state}`, during: `${state} in ${odd}/${e.length}`,
          effect: `${odd} sweeps`, score: odd / e.length, p: pf, nHold: e.length, nBase: b.length, name: addressName(labels, addr, bit),
          indirect: indirectOf((k) => bitOf(sweeps[k]!.vals[addr], bit), addr), drift: '' });
      }
    }
  }
  const order = (x: Finding, y: Finding) => (Number(!!x.indirect) - Number(!!y.indirect)) || (y.score - x.score) || (x.p - y.p)
    || (x.addr - y.addr) || ((x.bit ?? -1) - (y.bit ?? -1));
  res.findings.sort(order);
  res.edges.sort(order);
}

function fmtRel(v: number, width = 6): string {
  const s = `${v >= 0 ? '+' : '-'}${Math.abs(v).toFixed(1)}`;
  return s.padStart(width);
}

function timeline(res: StepResult, st: StepMeta, sweeps: Sweep[], phases: PhaseMark[], mine: number[], events: EventMark[]): void {
  for (const ev of events) {
    if (ev.step !== res.name) continue;
    const hold = mine.map((i) => phases[i]!).find((p) => p.phase === 'hold' && p.rep === ev.rep);
    const rel = hold ? `${fmtRel(ev.t - hold.t0, 0)} s into the hold` : '';
    const before = sweeps.filter((sw) => sw.t1 <= ev.t).slice(-3);
    const after = sweeps.filter((sw) => sw.t0 >= ev.t).slice(0, 3);
    const changed: string[] = [];
    for (let addr = 0; addr < 256; addr++) {
      const bv = before.map((sw) => sw.vals[addr]), av = after.map((sw) => sw.vals[addr]);
      if (!bv.length || !av.length || bv.some((v) => v === null || v === undefined) || av.some((v) => v === null || v === undefined)) continue;
      for (let bit = 0; bit < 8; bit++) {
        const sb = new Set((bv as number[]).map((v) => (v >> bit) & 1)), sa = new Set((av as number[]).map((v) => (v >> bit) & 1));
        if (sb.size === 1 && sa.size === 1 && [...sb][0] !== [...sa][0]) changed.push(`0x${hx(addr)} b${bit} ${[...sb][0]}->${[...sa][0]}`);
      }
    }
    res.marks.push(`rep ${ev.rep}: "${ev.label}" ${rel}; bits steady for 3 sweeps before and after that changed: ${changed.join(', ') || 'none'}`);
  }
  if (st.engine !== 'off' && !st.pre) return;
  const reps = [...new Set(mine.map((i) => phases[i]!.rep).filter((r) => r > 0))].sort((a, b) => a - b);
  for (const rep of reps) {
    const ph = mine.map((i) => phases[i]!).filter((p) => p.rep === rep);
    const hold = ph.find((p) => p.phase === 'hold');
    if (!hold || !ph.length) continue;
    const tLo = Math.min(...ph.map((p) => p.t0)), tHi = Math.max(...ph.map((p) => p.t1));
    const prev = new Map<number, number>();
    let silentFrom: number | null = null;
    for (const sw of sweeps) {
      if (sw.t0 < tLo || sw.t1 > tHi) continue;
      const rel = sw.t0 - hold.t0;
      const phase = ph.find((p) => inside(sw, p))?.phase ?? '';
      const got = FLAG_ADDRS.filter((a) => sw.vals[a] !== null && sw.vals[a] !== undefined);
      if (!got.length) { if (silentFrom === null) silentFrom = rel; continue; }
      if (silentFrom !== null) { res.timeline.push(`rep ${rep} ${fmtRel(silentFrom)} s: ECU silent until ${fmtRel(rel, 0)} s`); silentFrom = null; }
      for (const a of got) {
        const v = sw.vals[a] as number;
        const old = prev.get(a);
        if (old !== undefined && old !== v) {
          const flips: string[] = [];
          for (let b = 0; b < 8; b++) if (((old ^ v) >> b) & 1) flips.push(`b${b} ${(old >> b) & 1}->${(v >> b) & 1}`);
          res.timeline.push(`rep ${rep} ${fmtRel(rel)} s (${phase}): 0x${hx(a)} ${flips.join(', ')}`);
        }
        prev.set(a, v);
      }
    }
    if (silentFrom !== null) res.timeline.push(`rep ${rep} ${fmtRel(silentFrom)} s: ECU silent to the end`);
  }
}

// ---- report ----------------------------------------------------------------------------

const fmtP = (p: number) => (p < 1e-12 ? '<1e-12' : p < 1e-3 ? p.toExponential(0).replace(/e([+-])(\d)$/, 'e$10$2') : p.toFixed(3));

function findingRows(items: Finding[], top: number): string[] {
  const L = ['| # | addr | known as | change | rest | during | effect | p | note |', '|---|---|---|---|---|---|---|---|---|'];
  items.slice(0, top).forEach((f, i) => {
    const notes = [f.indirect ? `indirect: ${f.indirect}` : '', f.drift ? `rest drift: ${f.drift}` : ''].filter(Boolean).join('; ');
    L.push(`| ${i + 1} | ${where(f)} | ${f.name || '?'} | ${f.kind} | ${f.base} | ${f.during} | ${f.effect} | ${fmtP(f.p)} | ${notes} |`);
  });
  if (items.length > top) L.push(`| | | | ... ${items.length - top} more | | | | | |`);
  return L;
}

/** The report as Markdown (render_report, condensed to what the screen shows). */
export function renderReport(results: StepResult[], meta: { start?: string; port?: string; mapVersion?: string; trans?: string; labels?: Labels },
  nSweeps: number, cfg: Settings = DEFAULT_SETTINGS, top = 25): string {
  const labels = meta.labels ?? DEFAULT_LABELS;
  const L: string[] = [`# Poke test, ${(meta.start ?? '?').slice(0, 19).replace('T', ' ')}`, ''];
  L.push(`- Port \`${meta.port ?? '?'}\`; map \`${meta.mapVersion ?? '?'}\`; gearbox \`${meta.trans || '?'}\``);
  L.push(`- ${nSweeps} sweeps. A row is listed when, in the hold windows against the rest windows around the step, p < ${cfg.alpha} `
    + `and |d| >= ${cfg.minD} (byte) or the share of sweeps with the bit set changed by >= ${Math.round(cfg.minDp * 100)}% (bit); `
    + 'or on an excursion. **indirect** = the address also follows rpm or ECT outside this step. **edge** rows: bits in a state never '
    + 'seen at rest during the switching moments.', '', '## Steps', '',
  '| step | engine | reps | hold sweeps | rest sweeps | rpm rest -> hold | ECT raw rest -> hold | direct | indirect | edge |',
  '|---|---|---|---|---|---|---|---|---|---|');
  for (const r of results) {
    const direct = r.findings.filter((f) => !f.indirect).length;
    L.push(`| ${r.name} | ${r.engine === 'off' ? '**OFF**' : 'running'} | ${r.repsDone} | ${r.nHold} | ${r.nBase} | ${r.drivers.rpm ?? '-'} | `
      + `${r.drivers.ect ?? '-'} | ${direct} | ${r.findings.length - direct} | ${r.edges.length} |${r.note ? ` ${r.note}` : ''}`);
  }
  L.push('', '## Flag bytes 0x08-0x0F and 0xA4, bit by bit', '');
  const cols = results.filter((r) => !r.note);
  L.push(`| bit | known as | ${cols.map((r) => r.name).join(' | ')} |`, `|---|---|${'---|'.repeat(cols.length)}`);
  for (const a of FLAG_ADDRS) {
    for (let b = 0; b < 8; b++) {
      const key = `0x${hx(a)}.${b}`;
      const cells = cols.map((r) => {
        const fr = r.bitfrac[key];
        if (!fr) return '-';
        const [pb, ph] = fr;
        let txt = pb === ph && (pb === 0 || pb === 1) ? String(pb) : `${Math.round(pb * 100)}->${Math.round(ph * 100)}`;
        if (r.findings.some((f) => f.addr === a && f.bit === b)) txt = `**${txt}**`;
        if (r.edges.some((f) => f.addr === a && f.bit === b)) txt += ' e';
        return txt;
      });
      L.push(`| 0x${hx(a)} b${b} | ${addressName(labels, a, b)} | ${cells.join(' | ')} |`);
    }
  }
  L.push('');
  for (const r of results) {
    L.push(`## ${r.name}${r.engine === 'off' ? ' (ENGINE OFF)' : ''}`, '', `> ${r.prompt}`, '');
    if (r.note) { L.push(`_${r.note}_`, ''); continue; }
    if (r.findings.length) L.push(...findingRows(r.findings, top)); else L.push('Nothing responded in the hold.');
    L.push('');
    if (r.edges.length) L.push('At the switching moments:', '', ...findingRows(r.edges, top), '');
    if (r.marks.length) L.push('Owner marks:', '', ...r.marks.map((m) => `- ${m}`), '');
    if (r.timeline.length) L.push('Flag-bit timeline (seconds from the start of the hold):', '', '```', ...r.timeline.slice(0, 80), '```', '');
  }
  const byAddr = new Map<number, Finding[]>();
  for (const r of results) for (const f of [...r.findings, ...r.edges]) { if (!byAddr.has(f.addr)) byAddr.set(f.addr, []); byAddr.get(f.addr)!.push(f); }
  L.push('## Per address', '', '| addr | known as | steps |', '|---|---|---|');
  for (const addr of [...byAddr.keys()].sort((a, b) => a - b)) {
    const items = new Map<string, string[]>();
    for (const f of byAddr.get(addr)!) {
      const tag = (f.bit !== null ? `b${f.bit}` : 'byte') + (f.indirect ? ' (i)' : '') + (f.kind === 'edge' ? ' (e)' : '');
      if (!items.has(f.step)) items.set(f.step, []);
      items.get(f.step)!.push(tag);
    }
    const cells = [...items.entries()].map(([step, tags]) => {
      const txt = `${step}: ${tags.join(', ')}`;
      return tags.some((t) => !t.includes('(')) ? `**${txt}**` : txt;
    });
    L.push(`| 0x${hx(addr)} | ${addressName(labels, addr) || '?'} | ${cells.join('; ')} |`);
  }
  L.push('', '## Unnamed addresses that responded directly', '');
  const seen = new Set<string>();
  const unknown: [number, number, string][] = [];
  for (const fs of byAddr.values()) for (const f of fs) {
    if (f.indirect || f.name || f.kind === 'edge') continue;
    const k = `${f.addr}|${f.bit ?? -1}|${f.step}`;
    if (!seen.has(k)) { seen.add(k); unknown.push([f.addr, f.bit ?? -1, f.step]); }
  }
  unknown.sort((x, y) => x[0] - y[0] || x[1] - y[1] || x[2].localeCompare(y[2]));
  if (unknown.length) L.push(...unknown.map(([a, b, s]) => `- 0x${hx(a)}${b >= 0 ? ` b${b}` : ''}: ${s}`)); else L.push('None.');
  L.push('');
  return L.join('\n');
}
