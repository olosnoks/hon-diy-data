/**
 * The raw bytes drawer on F1: every polled address the address map does not
 * decode, as hex and decimal, and every channel the Live group table does not
 * place (desktop sensors.py). Opens from "Raw bytes (n) ▸", Esc closes.
 * The full tools (verify, sweep, poke) are on F7 Bytes.
 */
import { useShallow } from 'zustand/react/shallow';
import { useSession, type SessionData } from '../../core/session';
import { FONTS } from '../theme';
import { formatValue, unitsOf } from '../shell/format';
import { useTheme } from '../shell/useTheme';
import { unplacedChannels } from './layout';

export const RAW_PANEL = 'raw';

/** Addresses with a byte but no channel decoding them, ascending. */
export function unmappedAddrs(s: Pick<SessionData, 'raw' | 'channels'>): number[] {
  const mapped = new Set<number>();
  for (const st of Object.values(s.channels)) {
    const spec = st.spec;
    if ('addr' in spec && spec.kind !== 'math' && spec.source === undefined) {
      for (let i = 0; i < spec.length; i++) mapped.add(spec.addr + i);
    }
  }
  return Object.keys(s.raw).map(Number).filter((a) => !mapped.has(a)).sort((a, b) => a - b);
}

const hex = (n: number) => n.toString(16).toUpperCase().padStart(2, '0');

export function RawDrawer() {
  const t = useTheme();
  const addrs = useSession(useShallow(unmappedAddrs));
  const raw = useSession((s) => s.raw);
  const close = useSession((s) => s.closePanel);
  const others = useSession(useShallow((s) => unplacedChannels(s.channels)));
  const channels = useSession((s) => s.channels);
  const units = useSession((s) => s.units);
  return (
    <aside
      role="dialog"
      aria-label="Raw bytes"
      style={{
        position: 'absolute', top: 0, right: 0, bottom: 0, width: 440, zIndex: 5,
        background: t.panel, borderLeft: `2px solid ${t.rule}`, display: 'flex', flexDirection: 'column',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, padding: '8px 10px', borderBottom: `1px solid ${t.rule}` }}>
        <h2 style={{ margin: 0, fontSize: 13, fontWeight: 800, letterSpacing: '0.06em' }}>RAW BYTES</h2>
        <span style={{ fontSize: 12, color: t.fg2, fontWeight: 600, flexGrow: 1 }}>{addrs.length} not in the address map · F7 has the tools</span>
        <button type="button" onClick={close} aria-label="Close raw bytes"
          style={{ background: 'transparent', border: `2px solid ${t.rule}`, color: t.fg, padding: '2px 8px', minHeight: 28, cursor: 'pointer', fontWeight: 700, fontSize: 12 }}>
          Esc ✕
        </button>
      </div>
      {others.length > 0 && (
        <div data-testid="raw-channels" style={{ padding: '6px 10px', borderBottom: `1px solid ${t.rule}`, display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', columnGap: 12, rowGap: 2, maxHeight: '40%', overflow: 'auto', flexShrink: 0 }}>
          <div style={{ gridColumn: '1 / -1', fontSize: 11, fontWeight: 800, letterSpacing: '0.06em', color: t.accent }}>OTHER CHANNELS</div>
          {others.map((n) => {
            const st = channels[n]!;
            return (
              <div key={n} style={{ display: 'flex', justifyContent: 'space-between', gap: 6, fontSize: 12, borderBottom: `1px solid ${t.sunk}` }}>
                <span style={{ color: t.fg2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{st.spec.label || n}</span>
                <span style={{ fontFamily: FONTS.mono, fontWeight: 700, whiteSpace: 'nowrap' }}>{formatValue(st, units)} {unitsOf(st.spec, units)}</span>
              </div>
            );
          })}
        </div>
      )}
      <div style={{ overflow: 'auto', padding: '8px 10px', display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 6, alignContent: 'start' }}>
        {addrs.length === 0 && <div style={{ gridColumn: '1 / -1', color: t.fg2, fontSize: 13 }}>No unmapped bytes polled yet.</div>}
        {addrs.map((a) => {
          const b = raw[a] ?? 0;
          return (
            <div key={a} data-testid={`raw-${a}`} style={{ border: `1px solid ${t.line}`, padding: '4px 6px', fontFamily: FONTS.mono }}>
              <div style={{ fontSize: 11, color: t.fg2, fontWeight: 700 }}>0x{hex(a)}</div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                <span style={{ fontSize: 18, fontWeight: 700 }}>{hex(b)}</span>
                <span style={{ fontSize: 11, color: t.fg2 }}>{b}</span>
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
