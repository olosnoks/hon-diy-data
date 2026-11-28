/**
 * The top bar (design canvas, every artboard): HONDALOG wordmark, the F1-F8
 * keys (tappable on touch), then the always-on status: ECU link and read
 * rate, REC, stall catcher, CYD black box, the theme toggle (◐ F9) and a
 * MARK button (Space).
 */
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { useTouchOnly } from './touch';
import { SCREENS, useSession, type CydStatus, type EcuStatus, type StallStatus } from '../../core/session';
import type { Theme } from '../theme';
import { FONTS } from '../theme';
import { cydConnectAction } from './actions';
import { mmss, rate } from './format';
import { useStageSize } from './Stage';
import { useTheme } from './useTheme';

/** Below this stage width (design px) the bar tightens: a 1024 x 600 head unit gets a 1140 px wide stage. */
export const NARROW_BELOW = 1300;

export const TOPBAR_H = 40;

/**
 * How much of the status is left out to make the bar fit (desktop TopBar._fit:
 * the figures go first, the REC clock last of them, then whole items from the
 * right): 1 short stall text, 2 no ECU rate / errors, 3 no REC clock, 4 no CYD
 * chip, 5 no stall text.
 */
export const FIT_LEVELS = 5;

type Lamp = 'square' | 'dot' | 'ring' | 'hollow';

function LampIcon({ shape, color }: { shape: Lamp; color: string }) {
  const base: CSSProperties = { width: 9, height: 9, display: 'inline-block', flexShrink: 0 };
  if (shape === 'square') return <span aria-hidden style={{ ...base, background: color }} />;
  if (shape === 'dot') return <span aria-hidden style={{ ...base, background: color, borderRadius: '50%' }} />;
  if (shape === 'ring') return <span aria-hidden style={{ ...base, border: `2px solid ${color}`, borderRadius: '50%' }} />;
  return <span aria-hidden style={{ ...base, border: `1px solid ${color}` }} />;
}

/** A status chip: lamp + text (+ mono figures). `bad` = filled with the alarm colour (desktop chrome.py "bad"). */
export interface Chip { text: string; color: string; shape: Lamp; figures?: string[]; bad?: boolean }

export function ecuChip(ecu: EcuStatus, t: Theme): Chip {
  const sim = ecu.simulated ? ' SIM' : '';
  // "N err" once the poller has counted errors (desktop source_chip)
  const err = ecu.errors > 0 ? [`${ecu.errors} err`] : [];
  switch (ecu.state) {
    case 'ok':
      return { text: `ECU${sim}${ecu.obd2 ? ' OBD2' : ''}`, figures: [rate(ecu.rateHz), ...err], color: t.ok, shape: 'square' };
    case 'iso_only':
      return { text: `ECU${sim} OBD2 only`, figures: err, color: t.warn, shape: 'square' };
    case 'connecting':
      return { text: `ECU${sim} ${ecu.detail || 'connecting'}`, color: t.warn, shape: 'square' };
    case 'error':
      // silent / port lost: the alarm-filled chip
      return { text: `ECU ${ecu.detail || 'no reply'}`, figures: err, color: t.onAlarm, shape: 'square', bad: true };
    default:
      return { text: 'ECU off', color: t.comment, shape: 'hollow' };
  }
}

export function stallText(st: StallStatus, short = false): { text: string; hot: boolean } {
  switch (st.state) {
    case 'armed': return { text: short ? 'Stall armed' : 'Stall catcher armed', hot: false };
    case 'standby': return { text: short ? 'Stall waits' : 'Stall catcher waits for engine', hot: false };
    case 'saving': return { text: short ? `STALL +${Math.round(st.postLeftS)} s` : `STALL CAUGHT, saving +${Math.round(st.postLeftS)} s`, hot: true };
    case 'triggered': return { text: 'STALL CAUGHT', hot: true };
    default: return { text: short ? 'Stall off' : 'Stall catcher off', hot: false };
  }
}

export function cydChip(cyd: CydStatus, t: Theme): Chip & { title: string } {
  const bits = [
    cyd.mode !== 'off' ? `link ${cyd.mode}` : '',
    cyd.sdLogging ? 'SD log on' : '',
    cyd.mapMatch === true ? 'map matches' : cyd.mapMatch === false ? 'MAP MISMATCH' : '',
  ].filter(Boolean);
  const title = `CYD black box: ${cyd.state}${bits.length ? ' · ' + bits.join(' · ') : ''}`;
  if (cyd.state === 'ok') {
    const bad = cyd.mapMatch === false;
    return { text: bad ? 'CYD map mismatch' : 'CYD black box', color: bad ? t.warn : t.ok, shape: 'square', title };
  }
  if (cyd.state === 'connecting') return { text: 'CYD connecting', color: t.warn, shape: 'square', title };
  if (cyd.state === 'silent' || cyd.state === 'lost')
    return { text: cyd.state === 'lost' ? 'CYD port lost' : 'CYD silent', color: t.onAlarm, shape: 'square', title, bad: true };
  return { text: 'CYD off', color: t.comment, shape: 'hollow', title };
}

function useSecondTick(on: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [on]);
  return now;
}

const chipBtn: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 6, background: 'transparent', border: 0,
  padding: 0, cursor: 'pointer', fontSize: 12.5, fontWeight: 600, whiteSpace: 'nowrap', height: '100%',
};

/** The chip's body; a bad chip is filled with the alarm colour, 6 px past either end (desktop draw_chip). */
function ChipBody({ c, t }: { c: Chip; t: Theme }) {
  return (
    <span data-bad={c.bad ? 'true' : undefined}
      style={{ display: 'flex', alignItems: 'center', gap: 6, color: c.bad ? t.onAlarm : t.fg, fontWeight: c.bad ? 800 : 600,
        ...(c.bad ? { background: t.alarm, padding: '3px 6px' } : {}) }}>
      <LampIcon shape={c.shape} color={c.color} />
      {c.text}
      {c.figures?.map((f) => <span key={f} style={{ fontFamily: FONTS.mono, fontWeight: 700 }}>{' '}{f}</span>)}
    </span>
  );
}

/** ◐ F9: the theme toggle, a tap target for the head unit (desktop BarButton "half"). */
function ThemeButton({ t, narrow }: { t: Theme; narrow: boolean }) {
  const name = t.name === 'dracula' ? 'Dracula' : 'Darker';
  return (
    <button type="button" data-testid="theme-toggle" aria-label={`Theme: ${name}. F9 switches Dracula / Darker`}
      title={`Theme: ${name}. F9 switches Dracula / Darker`} onClick={() => useSession.getState().toggleTheme()}
      style={{ display: 'flex', alignItems: 'center', gap: 6, border: 0, borderLeft: `2px solid ${t.rule}`, background: t.bg, color: t.fg,
        padding: narrow ? '0 7px' : '0 10px', cursor: 'pointer', flexShrink: 0, minWidth: 44, justifyContent: 'center' }}>
      <span aria-hidden style={{ fontSize: 14, lineHeight: 1 }}>◐</span>
      <span style={{ fontFamily: FONTS.mono, fontSize: 11, fontWeight: 700, border: `1.5px solid ${t.fg}`, padding: '0 4px' }}>F9</span>
    </button>
  );
}

export function TopBar() {
  const t = useTheme();
  const screen = useSession((s) => s.screen);
  const setScreen = useSession((s) => s.setScreen);
  const ecu = useSession((s) => s.ecu);
  const cyd = useSession((s) => s.cyd);
  const stall = useSession((s) => s.stall);
  const rec = useSession((s) => s.recording);
  const marks = useSession((s) => s.marks.length);
  const now = useSecondTick(rec.active);
  const stageW = useStageSize().width;
  const narrow = stageW < NARROW_BELOW;
  const touch = useTouchOnly();
  // fit: start from everything whenever what is shown changes, then drop until it fits
  const statusRef = useRef<HTMLDivElement>(null);
  const fitKey = `${ecu.state}|${ecu.errors > 0}|${ecu.detail ?? ''}|${stall.state}|${cyd.state}|${cyd.mapMatch}|${rec.active}|${Math.round(stageW)}`;
  const [fitState, setFitState] = useState({ key: fitKey, level: 0 });
  const fit = fitState.key === fitKey ? fitState.level : 0;
  useLayoutEffect(() => {
    const el = statusRef.current;
    if (el && fit < FIT_LEVELS && el.scrollWidth > el.clientWidth + 1) setFitState({ key: fitKey, level: fit + 1 });
  });

  const e0 = ecuChip(ecu, t);
  const e = fit >= 2 ? { ...e0, figures: [] } : e0;
  const c = cydChip(cyd, t);
  const st = stallText(stall, narrow || fit >= 1);
  const elapsed = rec.active && rec.startedUnixMs !== null ? (now - rec.startedUnixMs) / 1000 : 0;

  return (
    <header
      style={{
        display: 'flex', alignItems: 'stretch', height: TOPBAR_H, flexShrink: 0,
        borderBottom: `2px solid ${t.rule}`, background: t.bg, color: t.fg, fontFamily: FONTS.sans,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', padding: narrow ? '0 8px' : '0 14px', fontWeight: 800, fontSize: narrow ? 13 : 15, borderRight: `2px solid ${t.rule}` }}>
        HONDALOG
      </div>
      <nav role="tablist" aria-label="Screens" style={{ display: 'flex', alignItems: 'stretch' }}>
        {SCREENS.map((s) => {
          const on = s.id === screen;
          return (
            <button
              key={s.id}
              type="button"
              role="tab"
              aria-selected={on}
              onClick={() => setScreen(s.id)}
              style={{
                display: 'flex', alignItems: 'center', gap: narrow ? 3 : 5, padding: narrow ? '0 6px' : '0 10px', border: 0,
                borderRight: `1px solid ${t.line}`, cursor: 'pointer', minWidth: 44,
                background: on ? t.accent : 'transparent', color: on ? t.onAccent : t.fg,
              }}
            >
              <span style={{ fontFamily: FONTS.mono, fontSize: 11, fontWeight: 700 }}>{s.key}</span>
              <span style={{ fontSize: 13, fontWeight: 700 }}>{s.name}</span>
            </button>
          );
        })}
      </nav>
      <div style={{ flexGrow: 1 }} />
      <div ref={statusRef} data-fit={fit} style={{ display: 'flex', alignItems: 'center', gap: narrow ? 9 : 14, padding: narrow ? '0 8px' : '0 14px', borderLeft: `2px solid ${t.rule}`, minWidth: 0, overflow: 'hidden' }}>
        <button type="button" title="Connection (F8 Setup)" onClick={() => setScreen('setup')} style={chipBtn} data-testid="status-ecu">
          <ChipBody c={e} t={t} />
        </button>
        <button
          type="button"
          title={rec.active ? 'Stop recording (Ctrl+R)' : 'Start recording (Ctrl+R)'}
          onClick={() => (rec.active ? useSession.getState().stopRecording() : useSession.getState().startRecording())}
          style={chipBtn}
          data-testid="status-rec"
        >
          <LampIcon shape={rec.active ? 'dot' : 'ring'} color={rec.active ? t.alarm : t.comment} />
          {rec.active ? (
            fit >= 3 ? 'REC' : <>REC <span style={{ fontFamily: FONTS.mono, fontWeight: 700 }}>{mmss(elapsed)}</span></>
          ) : 'REC off'}
        </button>
        {fit < 5 && <span
          data-testid="status-stall"
          style={{
            fontSize: 12.5, fontWeight: st.hot ? 800 : 600, whiteSpace: 'nowrap',
            ...(st.hot ? { background: t.alarm, color: t.onAlarm, padding: '3px 6px' } : {}),
          }}
        >
          {st.text}
        </span>}
        {fit < 4 && (
          <button type="button" title={cyd.state === 'ok' || cyd.state === 'connecting' || !cydConnectAction() ? c.title : `${c.title} · click to connect`}
            onClick={() => {
              const go = cydConnectAction();
              if (go && cyd.state !== 'ok' && cyd.state !== 'connecting') void go().then((ok) => { if (!ok) setScreen('setup'); });
              else setScreen('setup');
            }} style={chipBtn} data-testid="status-cyd">
            <ChipBody c={c} t={t} />
          </button>
        )}
      </div>
      <ThemeButton t={t} narrow={narrow} />
      <button
        type="button"
        title={`Mark this moment (Space) · ${marks} mark${marks === 1 ? '' : 's'}`}
        onClick={() => useSession.getState().mark()}
        style={{
          display: 'flex', alignItems: 'center', gap: 6, border: 0, borderLeft: `2px solid ${t.rule}`,
          background: t.accent, color: t.onAccent, padding: narrow ? '0 8px' : '0 12px', cursor: 'pointer', fontSize: 13, fontWeight: 800,
        }}
      >
        MARK
        {/* no key on a touch screen, nor room for one on the head unit's narrow bar */}
        {!narrow && !touch && <span style={{ fontFamily: FONTS.mono, fontSize: 11, fontWeight: 700, border: `1.5px solid ${t.onAccent}`, padding: '0 4px' }}>Space</span>}
      </button>
    </header>
  );
}
