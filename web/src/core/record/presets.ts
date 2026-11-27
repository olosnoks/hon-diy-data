/**
 * Built-in trigger rules, and user rules from TOML (port of
 * hondalog/triggers/presets.py). The two stall rules are on by default; the
 * others stay off until the channels they use are verified on the car.
 * Channel names are the default address map's.
 */

import { parse as parseToml } from 'smol-toml';
import { pyG } from '../store/pyfmt';
import { type TriggerRule, ruleFromDict, triggerRule } from './engine';

export interface StallOptions {
  rpmChannel?: string;
  preS?: number;
  postS?: number;
  rpmRun?: number;
  rpmZero?: number;
  /** no valid reply for this long is a link loss (3 s since 2026-11-26; was 1 s) */
  linkDownS?: number;
  /** longest wait for the review verdict after a link loss */
  reviewS?: number;
  /** road speed channel for the moving-car rule; "" leaves the rule out */
  vssChannel?: string;
  movingKmh?: number;
}

/**
 * The stall catcher as two rules, both armed once RPM has been above rpmRun:
 *   * "stall":     RPM reads below rpmZero (ECU still answering)
 *   * "link_loss": no valid reply for linkDownS (ECU lost power, cable out)
 *
 * The link-loss rule is reviewed when the ECU answers again: if the first
 * RPM after the silence is above rpmRun and close to the last one before
 * (below 1.25 x that + 100), or the car was moving before and still is (both
 * speeds above movingKmh), the engine never stopped: the capture is filed as
 * kind "dropout" ("LINK DROPOUT"), not as a stall. With no speed channel
 * (never read before the silence) the moving test is false and the RPM test
 * decides.
 */
export function stallRules(o: StallOptions = {}): TriggerRule[] {
  const rpm = o.rpmChannel ?? 'rpm';
  const preS = o.preS ?? 30, postS = o.postS ?? 10;
  const rpmRun = o.rpmRun ?? 400, rpmZero = o.rpmZero ?? 50;
  const linkDownS = o.linkDownS ?? 3.0, reviewS = o.reviewS ?? 60.0;
  const vss = o.vssChannel ?? 'vss', moving = o.movingKmh ?? 5.0;
  const arm = `${rpm} > ${pyG(rpmRun)}`;
  const movingTest = vss ? ` or (before_${vss} > ${pyG(moving)} and ${vss} > ${pyG(moving)})` : '';
  return [
    triggerRule({
      name: 'stall', when: `${rpm} < ${pyG(rpmZero)}`, armWhen: arm, preS, postS, label: 'STALL (rpm 0)',
      message: 'rpm dropped to 0', kind: 'stall', reason: 'rpm', group: 'stall',
    }),
    triggerRule({
      name: 'link_loss', when: 'link == 0', armWhen: arm, holdS: linkDownS, preS: preS + linkDownS, postS,
      label: 'STALL (link lost)', message: 'link lost (no reply from ECU)', kind: 'stall', reason: 'link', group: 'stall',
      reviewS, cancelKind: 'dropout',
      cancelWhen: `${arm} and (${rpm} < before_${rpm} * 1.25 + 100${movingTest})`,
      cancelLabel: 'LINK DROPOUT (engine running)',
      cancelMessage: 'ECU silent, engine still running when it answered again',
    }),
  ];
}

/** Rules from the feature list that ship disabled. */
export function extraRules(): TriggerRule[] {
  return [
    triggerRule({ name: 'low_battery', when: 'batt < 11.0', rearmWhen: 'batt > 12.0', holdS: 0.5, preS: 30, postS: 10,
      label: 'LOW BATTERY', severity: 'warn', message: 'battery below 11.0 V for 0.5 s', kind: 'lowbatt', enabled: false }),
    triggerRule({ name: 'lean_spike', when: 'o2 < 0.1 and tps > 50 and rpm > 2500', holdS: 0.3, preS: 10, postS: 5,
      label: 'LEAN', severity: 'critical', message: 'O2 lean under load for 0.3 s', kind: 'lean', enabled: false }),
    triggerRule({ name: 'overtemp', when: 'ect > 105', rearmWhen: 'ect < 100', holdS: 2.0, preS: 60, postS: 10,
      label: 'OVERTEMP', severity: 'critical', message: 'coolant above 105 °C for 2 s', kind: 'overtemp', enabled: false }),
  ];
}

export function defaultRules(stall: StallOptions = {}): TriggerRule[] {
  return [...stallRules(stall), ...extraRules()];
}

export const MANUAL: TriggerRule = triggerRule({ name: 'manual', when: '0', preS: 30, postS: 10, label: 'CAPTURE', message: 'capture requested', kind: 'capture' });

/** The one-off rule for a trigger from outside (the black box, the handheld): a stall capture with the stall rules' window. */
export function externalRule(why: string, rules: Iterable<TriggerRule> = []): TriggerRule {
  const stall = [...rules].find((r) => r.kind === 'stall');
  return triggerRule({ name: 'external', when: '0', preS: stall?.preS ?? 30, postS: stall?.postS ?? 10, label: `STALL (${why})`,
    message: why, kind: 'stall', reason: 'external', group: 'stall' });
}

/** The one-off rule for an alarm whose actions include "capture"; a trigger rule of the same name sets the window. */
export function alarmRule(name: string, message = '', severity = 'critical', rules: Iterable<TriggerRule> = []): TriggerRule {
  const own = [...rules].find((r) => r.name === name);
  return triggerRule({ name: `alarm_${name}`, when: '0', preS: own?.preS ?? 30, postS: own?.postS ?? 10, label: `ALARM ${name}`,
    message: message || `alarm ${name}`, kind: 'alarm', reason: 'alarm', severity });
}

/**
 * Rules from TOML text (``[[trigger]]`` tables with the Python field names).
 * Throws with the rule's name on a bad entry.
 */
export function loadRules(text: string, source = 'triggers.toml'): TriggerRule[] {
  const doc = parseToml(text) as { trigger?: Record<string, unknown>[] };
  const out: TriggerRule[] = [];
  for (const entry of doc.trigger ?? []) {
    if (typeof entry.when !== 'string' || !entry.when) {
      throw new Error(`${source}: trigger '${String(entry.name ?? '?')}': missing 'when'`);
    }
    out.push(ruleFromDict(entry));
  }
  return out;
}

/** ``base`` with same-named rules replaced by, and new rules added from, ``override``. */
export function mergeRules(base: Iterable<TriggerRule>, override: Iterable<TriggerRule>): TriggerRule[] {
  const merged = new Map<string, TriggerRule>();
  for (const r of base) merged.set(r.name, r);
  for (const r of override) merged.set(r.name, r);
  return [...merged.values()];
}
