/**
 * Small controls shared by F7 Bytes and F8 Setup: buttons, cards, tabs,
 * inputs and the progress block, drawn only from src/ui/theme.ts tokens
 * (2 px square borders, Dracula / Darker, no light surfaces).
 *
 * The theme comes from ThemeContext; the shell (agent #8) can wrap the
 * screens in <ThemeContext.Provider value={theme}> or pass `theme` to the
 * screen components, which provide it themselves.
 */

import { createContext, useContext, type CSSProperties, type ReactNode } from 'react';
import { DRACULA, FONTS, type Theme } from '../theme';

export const ThemeContext = createContext<Theme>(DRACULA);
export const useTheme = () => useContext(ThemeContext);

export const mono: CSSProperties = { fontFamily: FONTS.mono };

export function Button(props: {
  children: ReactNode; onClick?: () => void; kind?: 'normal' | 'primary' | 'danger'; disabled?: boolean;
  hotkey?: string; title?: string; active?: boolean; style?: CSSProperties; type?: 'button' | 'submit'; 'aria-label'?: string; testId?: string;
}) {
  const t = useTheme();
  const { kind = 'normal', disabled, active } = props;
  const fill = kind === 'primary' ? t.accent : kind === 'danger' ? t.alarm : active ? t.fg : t.panel;
  const ink = kind === 'primary' ? t.onAccent : kind === 'danger' ? t.onAlarm : active ? t.bg : t.fg;
  return (
    <button
      type={props.type ?? 'button'}
      onClick={props.onClick}
      disabled={disabled}
      title={props.title}
      aria-label={props['aria-label']}
      aria-pressed={active}
      data-testid={props.testId}
      style={{
        background: fill, color: ink, border: `2px solid ${kind === 'normal' ? t.rule : fill}`, borderRadius: 0,
        padding: '0 12px', minHeight: 36, fontFamily: FONTS.sans, fontWeight: 700, fontSize: 14, cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.45 : 1, display: 'inline-flex', alignItems: 'center', gap: 8, whiteSpace: 'nowrap', ...props.style,
      }}
    >
      {props.hotkey && <span style={{ ...mono, fontSize: 12, border: `1px solid ${ink}`, padding: '0 4px' }}>{props.hotkey}</span>}
      {props.children}
    </button>
  );
}

export function Card(props: { title?: ReactNode; right?: ReactNode; children: ReactNode; style?: CSSProperties; bodyStyle?: CSSProperties }) {
  const t = useTheme();
  return (
    <section style={{ background: t.panel, border: `2px solid ${t.rule}`, display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, ...props.style }}>
      {(props.title || props.right) && (
        <header style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', borderBottom: `2px solid ${t.line}` }}>
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 800, flex: 1, minWidth: 0 }}>{props.title}</h3>
          {props.right}
        </header>
      )}
      <div style={{ padding: 10, minHeight: 0, flex: 1, overflow: 'auto', ...props.bodyStyle }}>{props.children}</div>
    </section>
  );
}

export function Tabs<K extends string>(props: { items: readonly (readonly [K, string])[]; value: K; onChange: (k: K) => void; label: string }) {
  const t = useTheme();
  return (
    <div role="tablist" aria-label={props.label} style={{ display: 'flex', flexWrap: 'wrap', borderBottom: `2px solid ${t.rule}` }}>
      {props.items.map(([k, text]) => {
        const on = k === props.value;
        return (
          <button
            key={k}
            role="tab"
            aria-selected={on}
            onClick={() => props.onChange(k)}
            style={{
              background: on ? t.accent : 'transparent', color: on ? t.onAccent : t.fg, border: 'none', borderRight: `1px solid ${t.line}`,
              padding: '8px 14px', fontFamily: FONTS.sans, fontWeight: 700, fontSize: 14, cursor: 'pointer', borderRadius: 0,
            }}
          >
            {text}
          </button>
        );
      })}
    </div>
  );
}

export function inputStyle(t: Theme, extra: CSSProperties = {}): CSSProperties {
  return {
    background: t.bg, color: t.fg, border: `2px solid ${t.rule}`, borderRadius: 0, padding: '6px 8px', fontSize: 14,
    fontFamily: FONTS.sans, minHeight: 36, boxSizing: 'border-box', ...extra,
  };
}

export function TextInput(props: { value: string; onChange: (v: string) => void; placeholder?: string; monospace?: boolean;
  style?: CSSProperties; onEnter?: () => void; 'aria-label'?: string; disabled?: boolean; type?: string }) {
  const t = useTheme();
  return (
    <input
      type={props.type ?? 'text'}
      value={props.value}
      disabled={props.disabled}
      aria-label={props['aria-label']}
      placeholder={props.placeholder}
      onChange={(e) => props.onChange(e.target.value)}
      onKeyDown={(e) => { if (e.key === 'Enter') props.onEnter?.(); e.stopPropagation(); }}
      style={inputStyle(t, { ...(props.monospace ? mono : {}), ...props.style })}
    />
  );
}

export function Select<V extends string>(props: { value: V; options: readonly (readonly [V, string])[]; onChange: (v: V) => void;
  'aria-label'?: string; style?: CSSProperties; disabled?: boolean }) {
  const t = useTheme();
  return (
    <select
      value={props.value}
      disabled={props.disabled}
      aria-label={props['aria-label']}
      onChange={(e) => props.onChange(e.target.value as V)}
      style={inputStyle(t, props.style)}
    >
      {props.options.map(([v, text]) => <option key={v} value={v} style={{ background: t.bg, color: t.fg }}>{text}</option>)}
    </select>
  );
}

export function TextArea(props: { value: string; onChange?: (v: string) => void; rows?: number; readOnly?: boolean;
  style?: CSSProperties; 'aria-label'?: string; placeholder?: string }) {
  const t = useTheme();
  return (
    <textarea
      value={props.value}
      readOnly={props.readOnly}
      rows={props.rows ?? 8}
      aria-label={props['aria-label']}
      placeholder={props.placeholder}
      spellCheck={false}
      onChange={(e) => props.onChange?.(e.target.value)}
      onKeyDown={(e) => e.stopPropagation()}
      style={inputStyle(t, { ...mono, fontSize: 13, width: '100%', resize: 'vertical', lineHeight: 1.4, ...props.style })}
    />
  );
}

export function Check(props: { checked: boolean; onChange: (v: boolean) => void; children: ReactNode; disabled?: boolean }) {
  const t = useTheme();
  return (
    <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontWeight: 600 }}>
      <input
        type="checkbox"
        checked={props.checked}
        disabled={props.disabled}
        onChange={(e) => props.onChange(e.target.checked)}
        style={{ width: 18, height: 18, accentColor: t.accent }}
      />
      {props.children}
    </label>
  );
}

/** Percentage, stage, a bar and a detail line; kind '' working, 'ok' done, 'err' failed (the words say it too). */
export function ProgressBlock(props: { stage: string; pct: number | null; detail?: string; kind?: '' | 'ok' | 'err' }) {
  const t = useTheme();
  const color = props.kind === 'ok' ? t.ok : props.kind === 'err' ? t.alarm : t.accent;
  return (
    <div role="status" style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 8 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
        <span style={{ fontSize: 26, fontWeight: 800 }}>{props.pct === null ? '' : `${Math.round(props.pct)}%`}</span>
        <span style={{ fontWeight: 700 }}>{props.stage}</span>
      </div>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={props.pct ?? undefined}
        style={{ height: 18, border: `2px solid ${t.rule}`, background: t.bg, position: 'relative' }}
      >
        <div style={{ position: 'absolute', inset: 0, width: props.pct === null ? '30%' : `${Math.max(0, Math.min(100, props.pct))}%`, background: color }} />
      </div>
      {props.detail && <span style={{ color: t.fg2, fontSize: 13 }}>{props.detail}</span>}
    </div>
  );
}

/** A status mark that carries its meaning in the shape and word, colour second. */
export function StatusMark(props: { status: string }) {
  const t = useTheme();
  const s = props.status;
  const style: CSSProperties = { ...mono, fontSize: 12, fontWeight: 700, padding: '1px 6px', display: 'inline-block' };
  if (s === 'verified') return <span style={{ ...style, background: t.ok, color: t.bg }}>✓ verified</span>;
  if (s === 'suspect') return <span style={{ ...style, background: t.alarm, color: t.onAlarm }}>✗ suspect</span>;
  return <span style={{ ...style, border: `2px dashed ${t.unverified}`, color: t.unverified }}>? unverified</span>;
}

export function Note(props: { children: ReactNode; style?: CSSProperties }) {
  const t = useTheme();
  return <p style={{ color: t.fg2, fontSize: 13, margin: '6px 0', lineHeight: 1.45, ...props.style }}>{props.children}</p>;
}

/** Save text or bytes as a file (browser download; the Tauri shell may replace this). */
export function downloadFile(name: string, data: string | Uint8Array, type = 'application/octet-stream'): void {
  const blob = new Blob([typeof data === 'string' ? data : new Uint8Array(data)], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function readFileAsBytes(file: Blob): Promise<Uint8Array> {
  if (typeof file.arrayBuffer === 'function') return file.arrayBuffer().then((b) => new Uint8Array(b));
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(new Uint8Array(r.result as ArrayBuffer));
    r.onerror = () => reject(r.error);
    r.readAsArrayBuffer(file);
  });
}
