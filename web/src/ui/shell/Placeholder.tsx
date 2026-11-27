/** Shown by the placeholder index files of screens that have not landed yet. */
import { useTheme } from './useTheme';

export function Placeholder({ fkey, name, what }: { fkey: string; name: string; what: string }) {
  const t = useTheme();
  return (
    <div style={{ padding: '28px 32px', display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <span style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: 13, background: t.accent, color: t.onAccent, padding: '1px 6px' }}>{fkey}</span>
        <span style={{ fontSize: 28, fontWeight: 800 }}>{name}</span>
      </div>
      <p style={{ margin: 0, fontSize: 15, color: t.fg2, maxWidth: 640, lineHeight: 1.45 }}>{what}</p>
      <p style={{ margin: 0, fontSize: 13, color: t.comment }}>This screen is still being ported.</p>
    </div>
  );
}
