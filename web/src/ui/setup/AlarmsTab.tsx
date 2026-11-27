/**
 * Setup > Alarms (hondalog/ui/screens/alarms.py): the rules of the address
 * map, the live state of every rule, the alarm list (newest first) and
 * acknowledge. The engine is agent #3's AlarmEngine (src/core/alarms); the
 * tab reads it through AlarmSource so a test or the session can stand in.
 */

import { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import type { AlarmEngine, AlarmEvent } from '../../core/alarms';
import { useSession } from '../../core/session';
import { Button, Card, mono, Note, useTheme } from '../bytes/controls';
import { tryParse } from '../bytes/MapView';
import type { MapAlarm } from '../bytes/model/mapdoc';

export interface AlarmRow {
  name: string; label: string; severity: 'warn' | 'critical'; state: 'idle' | 'pending' | 'active' | 'latched';
  acknowledged: boolean; message: string; value: number | null; raisedAt: number | null; count: number; unverified: boolean; channel: string;
}
export interface AlarmLogRow { t: number; kind: 'raised' | 'cleared' | 'acknowledged'; name: string; severity: 'warn' | 'critical'; message: string }

export interface AlarmSource {
  rows(): AlarmRow[];
  log(): AlarmLogRow[];
  acknowledge(name?: string | null): void;
  subscribe(fn: () => void): () => void;
  revision(): number;
  now(): number;
}

/** AlarmSource over agent #3's AlarmEngine: statuses as rows, events into a log (newest first, 50 kept). */
export function alarmSourceFromEngine(engine: AlarmEngine, clock: () => number): AlarmSource {
  const log: AlarmLogRow[] = [];
  const listeners = new Set<() => void>();
  let rev = 0;
  const bump = () => { rev++; listeners.forEach((f) => f()); };
  engine.subscribe((e: AlarmEvent) => {
    log.unshift({ t: e.t, kind: e.kind, name: e.name, severity: e.severity, message: e.message });
    log.length = Math.min(log.length, 50);
    bump();
  });
  return {
    rows: () => engine.statuses().map((s) => ({
      name: s.name, label: s.rule.label || s.name, severity: s.severity, state: s.state, acknowledged: s.acknowledged, message: s.message,
      value: s.value, raisedAt: s.raisedAt, count: s.count, unverified: s.unverified, channel: s.rule.channel,
    })),
    log: () => log,
    acknowledge: (name = null) => { engine.acknowledge(name ?? null, clock()); bump(); },
    subscribe: (fn) => { listeners.add(fn); return () => listeners.delete(fn); },
    revision: () => rev,
    now: clock,
  };
}

/** The condition of a rule in words. */
export function ruleText(a: MapAlarm): string {
  const parts: string[] = [];
  const ch = (a.channel as string) || '';
  if (a.low !== undefined && a.low !== null) parts.push(`${ch} < ${a.low}`);
  if (a.high !== undefined && a.high !== null) parts.push(`${ch} > ${a.high}`);
  if (a.rate_low !== undefined && a.rate_low !== null) parts.push(`${ch} falls faster than ${a.rate_low}/s`);
  if (a.rate_high !== undefined && a.rate_high !== null) parts.push(`${ch} rises faster than ${a.rate_high}/s`);
  if (a.when) parts.push(String(a.when));
  let text = parts.join(' or ') || '(no condition)';
  if (a.hold_s) text += ` for ${a.hold_s} s`;
  if (a.gate) text += ` while ${a.gate}`;
  return text;
}

const SHAPES: [string, string][] = [
  ['■', 'filled square: link up, data flowing'], ['□', 'hollow square: off or not connected'], ['◐', 'half circle: working on it'],
  ['○', 'ring: stall catcher armed'], ['▲', 'triangle + yellow band: warning limit'], ['⯃', 'octagon + filled tile: alarm limit'],
  ['┆', 'dashed box: address is a guess'],
];

export function AlarmsTab(props: { source: AlarmSource | null; mapText: string }) {
  const t = useTheme();
  const src = props.source;
  useSyncExternalStore((fn) => (src ? src.subscribe(fn) : () => undefined), () => (src ? src.revision() : 0));
  const sessionAlarms = useSession((s) => s.alarms);
  const ackSession = useSession((s) => s.acknowledgeAlarms);
  const rules = useMemo(() => tryParse(props.mapText).map?.alarms ?? [], [props.mapText]);
  const [, tick] = useState(0);
  useEffect(() => { const id = setInterval(() => tick((n) => n + 1), 1000); return () => clearInterval(id); }, []);

  const rows: AlarmRow[] = src ? src.rows() : sessionAlarms.map((a) => ({
    name: a.name, label: a.name, severity: a.level === 'alarm' ? 'critical' : 'warn', state: 'active', acknowledged: a.acked, message: a.message,
    value: null, raisedAt: null, count: 1, unverified: false, channel: a.channel ?? '',
  }));
  const active = rows.filter((r) => r.state === 'active' || r.state === 'latched')
    .sort((a, b) => Number(b.severity === 'critical') - Number(a.severity === 'critical') || (b.raisedAt ?? 0) - (a.raisedAt ?? 0));
  const log = src?.log() ?? [];
  const now = src?.now() ?? 0;
  const ack = () => { if (src) src.acknowledge(null); ackSession(); };
  const unacked = active.some((r) => !r.acknowledged);
  const age = (secs: number) => (secs < 90 ? `${secs.toFixed(0)} s ago` : `${(secs / 60).toFixed(0)} min ago`);

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 380px', gap: 8, alignItems: 'start' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}>
        <Card title="Alarm list · live, newest first" right={<Button kind={unacked ? 'primary' : 'normal'} disabled={!unacked} onClick={ack}>Acknowledge (Esc)</Button>}>
          {active.length === 0 && log.length === 0 && <Note>Nothing yet. Warnings and alarms appear here as they happen.</Note>}
          {active.map((r) => {
            const alarm = r.severity === 'critical';
            return (
              <div key={`a-${r.name}`} data-testid="alarm-active" style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '6px 10px', marginBottom: 2,
                background: alarm ? t.alarm : t.warn, color: alarm ? t.onAlarm : t.onWarn, fontWeight: 700 }}>
                <span style={{ fontSize: 16 }}>{alarm ? '⯃' : '▲'}</span>
                <span style={{ fontWeight: 800, width: 70 }}>{alarm ? 'ALARM' : 'WARN'}</span>
                <span style={{ flex: 1 }}>{r.message || r.label}{r.unverified ? ' (unverified input)' : ''}</span>
                <span style={mono}>{r.state === 'latched' ? 'latched' : 'active'}{r.acknowledged ? ' · ack' : ''}</span>
              </div>
            );
          })}
          {log.map((e, i) => (
            <div key={`l-${i}`} style={{ display: 'flex', gap: 12, padding: '5px 10px', borderBottom: `2px solid ${t.sunk}` }}>
              <span style={{ fontWeight: 800, width: 120 }}>{e.kind.toUpperCase()}</span>
              <span style={{ flex: 1 }}>{e.name}: {e.message}</span>
              <span style={mono}>{age(Math.max(0, now - e.t))}</span>
            </div>
          ))}
        </Card>
        <Card title={`Rules · from the address map (${rules.length})`}>
          {rules.length === 0 ? <Note>The map has no [[alarm]] rules{tryParse(props.mapText).map ? '' : ' (it does not load)'}.</Note> : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead><tr style={{ ...mono, fontSize: 12, textAlign: 'left' }}><th>RULE</th><th>CONDITION</th><th>LEVEL</th><th>LATCH</th><th>STATE</th></tr></thead>
              <tbody>
                {rules.map((a) => {
                  const st = rows.find((r) => r.name === a.name);
                  return (
                    <tr key={a.name} style={{ borderBottom: `1px solid ${t.line}`, opacity: a.enabled === false ? 0.5 : 1 }}>
                      <td style={{ ...mono, fontWeight: 700, padding: '5px 4px' }}>{a.name}</td>
                      <td style={mono}>{ruleText(a)}</td>
                      <td>{(a.severity as string) === 'critical' ? 'alarm' : 'warn'}</td>
                      <td>{a.latching ? 'yes' : ''}</td>
                      <td style={mono}>{a.enabled === false ? 'off' : st ? `${st.state}${st.count ? ` · ${st.count}×` : ''}` : '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          <Note>Rules live in the address map ([[alarm]]): edit them on F7 Bytes &gt; Address map. A rule that reads an unverified address says so.</Note>
        </Card>
      </div>
      <Card title="Shape key">
        {SHAPES.map(([icon, text]) => (
          <div key={text} style={{ display: 'flex', gap: 10, alignItems: 'center', fontWeight: 700, padding: '3px 0' }}>
            <span style={{ ...mono, width: 20, textAlign: 'center' }}>{icon}</span>{text}
          </div>
        ))}
      </Card>
    </div>
  );
}
