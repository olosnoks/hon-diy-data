/**
 * Map status: every channel, its state and how it can be checked
 * (hondalog/mapping/verify.py coverage + scripts_for, and the
 * `python -m hondalog.mapping status` text). Pure functions.
 *
 * Only the catalogue of the guided verification scripts is here (which
 * script checks which channel, and whether that check can prove the
 * address); running a session is not part of the address map.
 */

import type { AddressMap, Evidence } from '../addressmap';

export interface ScriptCheck {
  channel: string;
  /** Plausibility only: cannot prove the address. */
  weak: boolean;
}

export interface ScriptInfo {
  id: string;
  title: string;
  checks: ScriptCheck[];
}

const c = (channel: string, weak = false): ScriptCheck => ({ channel, weak });

function switchScript(channel: string, scriptId: string, title: string): ScriptInfo {
  return { id: scriptId, title, checks: [c(channel), c(channel), c(channel)] };
}

/** verify.SCRIPTS, in the same order, checks in the same order. */
export const SCRIPTS: readonly ScriptInfo[] = [
  {
    id: 'koeo', title: 'Key on, engine off',
    checks: [c('rpm', true), c('vss', true), c('tps', true), c('map'), c('map', true), c('baro', true),
      c('batt'), c('batt', true), c('ect'), c('ect'), c('iat'), c('mil', true), c('closed_loop', true)],
  },
  {
    id: 'tps_sweep', title: 'Throttle sweep (engine off)',
    checks: [c('tps'), c('tps'), c('tps'), c('tps'), c('rpm', true)],
  },
  {
    id: 'idle', title: 'Warm idle',
    checks: [c('rpm'), c('rpm', true), c('rpm', true), c('map', true), c('batt'), c('batt', true),
      c('tps', true), c('vss', true), c('ect', true), c('inj', true), c('ign', true), c('o2'),
      c('closed_loop', true), c('stft', true), c('ltft', true), c('iacv', true)],
  },
  {
    id: 'steady_3000', title: 'Steady 3000 rpm',
    checks: [c('rpm'), c('rpm'), c('rpm', true), c('tps', true), c('ign', true), c('batt', true), c('vss', true)],
  },
  {
    id: 'warmup', title: 'Cold start to warm',
    checks: [c('ect'), c('ect'), c('ect'), c('iat'), c('iat', true), c('rpm', true), c('closed_loop')],
  },
  {
    id: 'drive', title: 'Steady road speed',
    checks: [c('vss'), c('vss', true), c('rpm', true)],
  },
  switchScript('ac_sw', 'ac_switch', 'A/C switch'),
];

/** Script.channels: the channels a script checks, first mention order. */
export function scriptChannels(script: ScriptInfo): string[] {
  return [...new Set(script.checks.map((k) => k.channel))];
}

/** Scripts that check a channel, strongest evidence first. */
export function scriptsFor(channel: string): ScriptInfo[] {
  const strength = (s: ScriptInfo): number => -s.checks.filter((k) => k.channel === channel && !k.weak).length;
  const found = SCRIPTS.filter((s) => scriptChannels(s).includes(channel));
  return found.map((s, i) => [s, i] as const).sort((a, b) => (strength(a[0]) - strength(b[0])) || (a[1] - b[1])).map((p) => p[0]);
}

export interface CoverageRow {
  channel: string;
  label: string;
  status: string;
  evidence: Evidence | null;
  /** Scripts that can prove the address. */
  strong: string[];
  /** Scripts that only show the value is plausible. */
  weak: string[];
}

/**
 * For a map overview: every channel, its state, and how it can be checked.
 * A channel with neither strong nor weak scripts needs the correlation
 * finder or an ISO cross-check.
 */
export function coverage(amap: AddressMap): CoverageRow[] {
  return amap.channels.map((ch) => {
    const strong = SCRIPTS.filter((s) => s.checks.some((k) => k.channel === ch.name && !k.weak)).map((s) => s.id);
    const weak = SCRIPTS.filter((s) => scriptChannels(s).includes(ch.name) && !strong.includes(s.id)).map((s) => s.id);
    return { channel: ch.name, label: ch.label, status: ch.status, evidence: ch.evidence, strong, weak };
  });
}

/** How many measured channels are verified. */
export function verifiedCount(amap: AddressMap): { verified: number; total: number } {
  return { verified: amap.channels.filter((ch) => ch.verified).length, total: amap.channels.length };
}

/** The lines `python -m hondalog.mapping status` prints. */
export function statusLines(amap: AddressMap): string[] {
  const lines = [`${amap.path}\n${amap.versionTag}   ECU variant: ${amap.variant || '-'}   schema ${amap.schema}`];
  for (const row of coverage(amap)) {
    const ev = row.evidence;
    const how = ev ? `${ev.date.slice(0, 10)} ${ev.method}`
      : 'check with: ' + (row.strong.join(', ') || 'finder / ISO cross-check');
    lines.push(`  ${row.channel.padEnd(12)} ${row.status.padEnd(10)} ${how}`);
  }
  const { verified, total } = verifiedCount(amap);
  lines.push(`${verified} of ${total} channels verified`);
  return lines;
}
