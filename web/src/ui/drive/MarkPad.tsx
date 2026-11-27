/** The MARK pad (screens/drive.py MarkPad): one press, no aiming. */
import { useEffect, useState } from 'react';
import { useSession } from '../../core/session';
import { FONTS } from '../theme';
import { useTheme } from '../shell/useTheme';

export function markLine(active: boolean, marks: number, lastMarkMs: number | null, nowMs: number): string {
  if (!active) return 'not recording · Ctrl+R';
  if (lastMarkMs === null || marks === 0) return `${marks} marks`;
  const age = Math.max(0, (nowMs - lastMarkMs) / 1000);
  const ago = age < 60 ? `${Math.floor(age)} s ago` : `${Math.floor(age / 60)} min ago`;
  return `${marks} mark${marks !== 1 ? 's' : ''} · last ${ago}`;
}

export function MarkPad() {
  const t = useTheme();
  const active = useSession((s) => s.recording.active);
  const marks = useSession((s) => s.marks.length);
  const last = useSession((s) => s.marks[s.marks.length - 1]?.unixMs ?? null);
  const [now, setNow] = useState(() => Date.now());
  const [down, setDown] = useState(false);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const fill = down ? t.livePoint : t.accent;
  const ink = t.onAccent;
  return (
    <button
      type="button"
      data-testid="mark-pad"
      onPointerDown={() => setDown(true)}
      onPointerUp={() => setDown(false)}
      onPointerLeave={() => setDown(false)}
      onClick={() => { useSession.getState().mark(); setNow(Date.now()); }}
      style={{
        flexGrow: 1, minHeight: 0, border: 0, background: fill, color: ink, cursor: 'pointer',
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10,
        touchAction: 'manipulation',
      }}
    >
      <span style={{ fontSize: 104, fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1 }}>MARK</span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 18, fontWeight: 700 }}>
        <span style={{ fontFamily: FONTS.mono, fontSize: 16, border: `2px solid ${ink}`, padding: '1px 6px' }}>Space</span>
        it just stumbled
      </span>
      <span style={{ fontFamily: FONTS.mono, fontSize: 16, fontWeight: 700 }} data-testid="mark-line">
        {markLine(active, marks, last, now)}
      </span>
    </button>
  );
}
