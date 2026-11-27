/**
 * Math channels and alarms per poller sweep (#4's LiveSession Pipeline),
 * over #3's MathEngine and AlarmEngine, from the address map's [[math]] and
 * [[alarm]] tables. A map whose math or alarms do not build still polls:
 * the part that failed is left out and `errors` says why.
 */
import type { AddressMap } from '../core/addressmap';
import { AlarmEngine, AlarmRule, type AlarmEvent } from '../core/alarms';
import { MathChannel, MathEngine } from '../core/expr';
import type { AlarmEventLike, Pipeline } from '../core/record/live';
import type { ActiveAlarm } from '../core/session';

function eventLike(e: AlarmEvent): AlarmEventLike {
  return {
    t: e.t, name: e.name, kind: e.kind, severity: e.severity, value: e.value, channel: e.channel,
    unverified: e.unverified, markerText: e.markerText, message: e.message, actions: e.actions,
  };
}

export class LivePipeline implements Pipeline {
  readonly math: MathEngine | null;
  readonly alarms: AlarmEngine | null;
  readonly errors: string[] = [];
  readonly mathNames: string[];
  readonly mathChannels: { name: string; label?: string; units?: string; kind?: string; decimals?: number; min?: number; max?: number }[];
  private lastT = 0;

  constructor(readonly map: AddressMap) {
    const inputs = [...map.names, ...map.obd2Names];
    let math: MathEngine | null = null;
    try {
      math = new MathEngine(map.math.filter((m) => m.enabled).map((m) => new MathChannel({
        name: m.name, expr: m.expr, label: m.label, units: m.units, min: m.min, max: m.max, decimals: m.decimals,
        gauge: m.gauge, plot: m.plot, kind: m.kind, description: m.description, enabled: m.enabled,
      })), inputs, map.constants, map.units());
    } catch (e) {
      this.errors.push(`math channels: ${(e as Error).message}`);
    }
    this.math = math;
    this.mathNames = math ? math.channels.map((c) => c.name) : [];
    this.mathChannels = math ? math.channels.map((c) => ({ name: c.name, label: c.label, units: c.units, kind: c.kind, decimals: c.decimals, min: c.min, max: c.max })) : [];
    let alarms: AlarmEngine | null = null;
    try {
      const labels: Record<string, [string, string]> = {};
      for (const c of map.channels) labels[c.name] = [c.label, c.units];
      for (const m of map.math) labels[m.name] = [m.label, m.units];
      alarms = new AlarmEngine(map.alarms.map((a) => new AlarmRule({
        name: a.name, channel: a.channel || undefined, low: a.low, high: a.high, rate_low: a.rate_low, rate_high: a.rate_high,
        rate_window_ms: a.rate_window_ms, when: a.when || undefined, clear_when: a.clear_when || undefined, gate: a.gate || undefined,
        hold_s: a.hold_s, clear_hold_s: a.clear_hold_s, hysteresis: a.hysteresis, severity: a.severity, priority: a.priority,
        latching: a.latching, actions: a.actions, message: a.message, label: a.label, enabled: a.enabled,
      })), [...inputs, ...this.mathNames], map.constants, map.verifiedMap(), labels);
    } catch (e) {
      this.errors.push(`alarms: ${(e as Error).message}`);
    }
    this.alarms = alarms;
  }

  process(values: Record<string, number | null>, t: number, link: boolean, changed?: Iterable<string> | null) {
    this.lastT = t;
    let all: Record<string, number | null> = values;
    if (this.math) {
      try {
        all = { ...values, ...this.math.update(values, t, changed ?? null) };
      } catch (e) {
        this.errors.push(`math: ${(e as Error).message}`);
      }
    }
    let events: AlarmEventLike[] = [];
    if (this.alarms && link) {
      try {
        events = this.alarms.update(all, t).map(eventLike);
      } catch (e) {
        this.errors.push(`alarms: ${(e as Error).message}`);
      }
    }
    return { values: all, events };
  }

  acknowledge(): AlarmEventLike[] {
    return this.alarms ? this.alarms.acknowledge(null, this.lastT).map(eventLike) : [];
  }

  reset(): void {
    this.math?.reset();
    this.alarms?.reset();
  }

  /** Alarm level per channel for the tiles: 0 none, 1 warn, 2 critical. */
  level(channel: string): 0 | 1 | 2 {
    const s = this.alarms?.severityFor(channel) ?? null;
    return s === 'critical' ? 2 : s === 'warn' ? 1 : 0;
  }

  /** The active alarms as the session store shows them. */
  active(): ActiveAlarm[] {
    if (!this.alarms) return [];
    return this.alarms.active().map((st) => ({
      name: st.name,
      ...(st.rule.channel ? { channel: st.rule.channel } : {}),
      level: st.severity === 'critical' ? 'alarm' as const : 'warn' as const,
      message: st.message || st.rule.label,
      acked: st.acknowledged,
    }));
  }
}
