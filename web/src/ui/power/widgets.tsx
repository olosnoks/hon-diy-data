/**
 * Small building blocks of F5 Power and F6 GPS, drawn from the design canvas
 * (Power.dc.html): section headings, tabs, buttons, result cards, input rows,
 * a drive select. Theme tokens only (Dracula / Darker).
 */
import type { CSSProperties, ReactNode } from 'react';
import type { DriveInfo } from '../../core/types';
import { FONTS, type Theme } from '../theme';

export function H2({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <h2 style={{ margin: 0, fontSize: 13, fontWeight: 800, letterSpacing: '0.06em', ...style }}>{children}</h2>;
}

export function Section({ t, children, last, style }: { t: Theme; children: ReactNode; last?: boolean; style?: CSSProperties }) {
  return (
    <div style={{ padding: '10px 12px', borderBottom: last ? undefined : `1px solid ${t.rule}`, display: 'flex', flexDirection: 'column', gap: 6, ...style }}>
      {children}
    </div>
  );
}

export function Tabs<K extends string>({ t, items, value, onChange, label }: {
  t: Theme; items: Array<[NoInfer<K>, string]>; value: K; onChange(k: NoInfer<K>): void; label: string;
}) {
  return (
    <div role="tablist" aria-label={label} style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
      {items.map(([k, text]) => {
        const on = k === value;
        return (
          <button key={k} type="button" role="tab" aria-selected={on} onClick={() => onChange(k)}
            style={{
              fontFamily: FONTS.sans, fontSize: 13, fontWeight: 700, padding: '5px 12px', cursor: 'pointer',
              background: on ? t.accent : 'transparent', color: on ? t.onAccent : t.fg,
              border: `1px solid ${on ? t.accent : t.line}`, minHeight: 30,
            }}>{text}</button>
        );
      })}
    </div>
  );
}

export function Button({ t, children, onClick, primary, disabled, title, pressed }: {
  t: Theme; children: ReactNode; onClick?(): void; primary?: boolean; disabled?: boolean; title?: string; pressed?: boolean;
}) {
  const fill = primary || pressed;
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title} aria-pressed={pressed}
      style={{
        fontFamily: FONTS.sans, fontSize: 13, fontWeight: 700, minHeight: 36, padding: '0 10px', cursor: disabled ? 'default' : 'pointer',
        background: fill ? t.accent : t.bg, color: fill ? t.onAccent : t.fg,
        border: `2px solid ${fill ? t.accent : t.rule}`, opacity: disabled ? 0.5 : 1,
      }}>{children}</button>
  );
}

export function ResultCard({ t, label, value, note }: { t: Theme; label: string; value: string; note: string }) {
  return (
    <div style={{ border: `1px solid ${t.line}`, padding: '6px 8px', minWidth: 0 }}>
      <div style={{ fontSize: 11.5, fontWeight: 700 }}>{label}</div>
      <div style={{ fontFamily: FONTS.mono, fontSize: 20, fontWeight: 700, whiteSpace: 'nowrap' }}>{value}</div>
      <div style={{ fontSize: 11, color: t.fg2 }}>{note}</div>
    </div>
  );
}

export function Row({ label, children, color }: { label: ReactNode; children: ReactNode; color?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, fontSize: 12.5 }}>
      <span style={{ fontWeight: 600 }}>{label}</span>
      <span style={{ fontFamily: FONTS.mono, fontWeight: 700, color }}>{children}</span>
    </div>
  );
}

export function inputStyle(t: Theme, width = 86): CSSProperties {
  return {
    width, fontFamily: FONTS.mono, fontSize: 12.5, fontWeight: 700, color: t.fg, background: t.sunk,
    border: `1px solid ${t.line}`, padding: '3px 6px', textAlign: 'right', minHeight: 26, boxSizing: 'border-box',
  };
}

export function DriveSelect({ t, drives, value, onChange, label = 'Drive' }: {
  t: Theme; drives: DriveInfo[]; value: string | null; onChange(name: string): void; label?: string;
}) {
  const sorted = [...drives].sort((a, b) => b.startedUnix - a.startedUnix);
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, fontWeight: 600 }}>
      <span>{label}</span>
      <select aria-label={label} value={value ?? ''} onChange={(e) => onChange(e.target.value)}
        style={{ ...inputStyle(t, 0), flex: 1, minWidth: 0, textAlign: 'left', fontFamily: FONTS.mono }}>
        {!value && <option value="">(none)</option>}
        {sorted.map((d) => <option key={d.name} value={d.name}>{d.name}{d.kind !== 'drive' ? ` · ${d.kind}` : ''}</option>)}
      </select>
    </label>
  );
}

export function Empty({ t, title, children }: { t: Theme; title: string; children?: ReactNode }) {
  return (
    <div style={{ padding: 24, color: t.fg2, fontSize: 14, lineHeight: 1.5 }}>
      <div style={{ color: t.fg, fontWeight: 800, fontSize: 15, marginBottom: 6 }}>{title}</div>
      {children}
    </div>
  );
}

export function Swatch({ color, dashed, label }: { color: string; dashed?: boolean; label: string }) {
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, fontWeight: 600 }}>
      <span style={dashed
        ? { width: 18, height: 0, borderTop: `2px dashed ${color}`, display: 'inline-block' }
        : { width: 18, height: 3, background: color, display: 'inline-block' }} />
      {label}
    </span>
  );
}
