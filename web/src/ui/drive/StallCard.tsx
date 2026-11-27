/** The stall catcher card (screens/drive.py StallCard). */
import { useSession, type StallStatus } from '../../core/session';
import { FONTS, type Theme } from '../theme';
import { useTheme } from '../shell/useTheme';

export const STATE_WORD: Record<StallStatus['state'], string> = {
  off: 'OFF', standby: 'STANDBY', armed: 'ARMED', saving: 'SAVING', triggered: 'TRIGGERED',
};

export function stallSentence(st: StallStatus): string {
  const g = (n: number) => String(Number(n.toFixed(2)));
  if (st.state === 'triggered' && st.lastFile) return `Saved ${st.lastFile}. Enter opens it in Replay, Esc dismisses.`;
  if (st.state === 'off') return 'Off: the catcher runs while recording. Ctrl+R starts a recording.';
  if (st.state === 'standby')
    return `Recording. Arms once the engine runs, then fires on RPM = 0 or ECU silent 1 s. Saves ${g(st.preS)} s before and ${g(st.postS)} s after.`;
  return `Fires on RPM = 0 after running, or ECU silent 1 s. Saves ${g(st.preS)} s before and ${g(st.postS)} s after.`;
}

function StateIcon({ state, ink }: { state: StallStatus['state']; ink: string }) {
  return (
    <svg width={44} height={44} viewBox="0 0 44 44" aria-hidden style={{ flexShrink: 0 }}>
      {state === 'armed' && <circle cx={22} cy={22} r={15} fill="none" stroke={ink} strokeWidth={8} />}
      {state === 'standby' && (
        <>
          <circle cx={22} cy={22} r={17} fill="none" stroke={ink} strokeWidth={5} />
          <path d="M22 3 A19 19 0 0 0 22 41 Z" fill={ink} />
        </>
      )}
      {(state === 'saving' || state === 'triggered') && <polygon points="13,0 31,0 44,13 44,31 31,44 13,44 0,31 0,13" fill={ink} />}
      {state === 'off' && <rect x={6} y={6} width={32} height={32} fill="none" stroke={ink} strokeWidth={7} />}
    </svg>
  );
}

export function StallCard() {
  const t: Theme = useTheme();
  const st = useSession((s) => s.stall);
  const hot = st.state === 'triggered' || st.state === 'saving';
  const ink = hot ? t.onAlarm : t.fg;
  const saving = st.state === 'saving';
  const frac = st.preS && st.postS ? (saving ? (st.postS - st.postLeftS) / st.postS : st.bufferS / st.preS) : 0;
  const right = saving
    ? `+${(st.postS - st.postLeftS).toFixed(1)} / ${st.postS} s`
    : `${st.bufferS.toFixed(1)} / ${st.preS} s`;
  const word = STATE_WORD[st.state];
  return (
    <section
      data-testid="stall-card"
      aria-label="Stall catcher"
      style={{ height: 268, flexShrink: 0, background: hot ? t.alarm : t.panel, color: ink, border: `2px solid ${hot ? t.alarm : t.rule}`, padding: '10px 14px', display: 'flex', flexDirection: 'column' }}
    >
      <div style={{ fontSize: 13, fontWeight: 800, letterSpacing: '0.06em', color: hot ? ink : t.fg2 }}>STALL CATCHER</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, height: 62 }}>
        <StateIcon state={st.state} ink={ink} />
        <span style={{ fontSize: word.length > 7 ? 38 : 52, fontWeight: 800, letterSpacing: '-0.02em' }}>{word}</span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14, fontWeight: 700, marginTop: 2 }}>
        <span>{saving ? 'Saving after trigger' : 'Pre-trigger buffer'}</span>
        <span style={{ fontFamily: FONTS.mono }}>{right}</span>
      </div>
      <div style={{ position: 'relative', height: 18, marginTop: 6, background: hot ? t.alarm : t.sunk, border: `2px solid ${hot ? ink : t.rule}` }}>
        <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${Math.min(1, Math.max(0, frac)) * 100}%`, background: ink }} />
      </div>
      <p style={{ margin: '10px 0 0', fontSize: 14, fontWeight: 600, lineHeight: '20px', flexGrow: 1, overflow: 'hidden' }}>{stallSentence(st)}</p>
      <div style={{ borderTop: `2px solid ${hot ? ink : t.line}`, paddingTop: 6, display: 'flex', justifyContent: 'space-between', fontSize: 14, fontWeight: 700 }}>
        <span>Stalls this drive</span>
        <span style={{ fontFamily: FONTS.mono }} data-testid="stall-count">{st.count}</span>
      </div>
    </section>
  );
}
