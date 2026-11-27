/**
 * The rest of the frame every screen shares (desktop chrome.py): the stall
 * banner laid over the top of the screen under the top bar, and the bottom
 * strip with the worst alarm and warning, a passing message or the link
 * summary, the yellow SIMULATOR badge, and the key hints (hidden on touch and
 * compact sizes).
 */
import { useEffect, useState, type CSSProperties } from 'react';
import { useSession, type ActiveAlarm } from '../../core/session';
import { FONTS } from '../theme';
import { COMPACT_BELOW, useStageSize } from './Stage';
import { TOPBAR_H } from './TopBar';
import { useTheme } from './useTheme';

export const STRIP_H = 32;
export const STRIP_H_COMPACT = 26;
export const BANNER_H = 44;
export const KEY_HINTS = 'Space mark · F1–F8 screens · F9 theme · Esc acknowledge';

const OCTAGON = 'M7,1 L17,1 L23,7 L23,17 L17,23 L7,23 L1,17 L1,7 Z';
const TRIANGLE = 'M12,2 L23,22 L1,22 Z';

function Shape({ d, color, size }: { d: string; color: string; size: number }) {
  return (
    <svg aria-hidden width={size} height={size} viewBox="0 0 24 24" style={{ flexShrink: 0 }}>
      <path d={d} fill={color} />
    </svg>
  );
}

function Keycap({ text, color }: { text: string; color: string }) {
  return <span style={{ fontFamily: FONTS.mono, fontSize: 11, fontWeight: 700, border: `1.5px solid ${color}`, padding: '0 4px', lineHeight: '16px' }}>{text}</span>;
}

/** Touch screens get no key hints (the Android head unit, a phone). */
function useCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.('(pointer: coarse)').matches);
  useEffect(() => {
    const mq = typeof window !== 'undefined' ? window.matchMedia?.('(pointer: coarse)') : undefined;
    if (!mq) return;
    const on = () => setCoarse(mq.matches);
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);
  return coarse;
}

/** 44 px alarm bar over the top of the screen (content does not move): "STALL 23:03:07", the cause, Open in Replay (Enter), Dismiss (Esc). */
export function StallBannerBar() {
  const t = useTheme();
  const banner = useSession((s) => s.stallBanner);
  if (!banner) return null;
  const btn: CSSProperties = {
    display: 'flex', alignItems: 'center', gap: 8, height: 36, padding: '0 12px', cursor: 'pointer', flexShrink: 0,
    fontFamily: FONTS.sans, fontSize: 14, fontWeight: 700, whiteSpace: 'nowrap',
  };
  return (
    <div role="alert" data-testid="stall-banner"
      style={{ position: 'absolute', left: 0, right: 0, top: TOPBAR_H, height: BANNER_H, zIndex: 30, display: 'flex', alignItems: 'center', gap: 12,
        padding: '0 8px 0 12px', background: t.alarm, color: t.onAlarm, fontFamily: FONTS.sans, boxSizing: 'border-box' }}>
      <Shape d={OCTAGON} color={t.onAlarm} size={22} />
      <span style={{ fontSize: 18, fontWeight: 800, whiteSpace: 'nowrap' }}>{banner.title}</span>
      <span data-testid="stall-cause" style={{ fontSize: 14, fontWeight: 700, flex: '1 1 0', minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{banner.cause}</span>
      <button type="button" onClick={() => useSession.getState().openStallBanner()}
        style={{ ...btn, background: t.panel, color: t.alarm, border: `2px solid ${t.panel}` }}>
        Open in Replay <Keycap text="Enter" color={t.alarm} />
      </button>
      <button type="button" onClick={() => useSession.getState().dismissStallBanner()}
        style={{ ...btn, background: 'transparent', color: t.onAlarm, border: `2px solid ${t.onAlarm}` }}>
        Dismiss <Keycap text="Esc" color={t.onAlarm} />
      </button>
    </div>
  );
}

/** "{message} (+N)" for the first of a severity (desktop AlarmStrip.sync). */
export function worst(alarms: ActiveAlarm[], level: ActiveAlarm['level']): string {
  const rows = alarms.filter((a) => a.level === level);
  if (!rows.length) return '';
  return rows[0]!.message + (rows.length > 1 ? `  (+${rows.length - 1})` : '');
}

function useNow(until: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (until === null) return;
    const left = until - Date.now();
    if (left <= 0) return;
    const id = setTimeout(() => setNow(Date.now()), left + 20);
    return () => clearTimeout(id);
  }, [until]);
  return until === null ? now : Math.max(now, Date.now());
}

/** The bottom strip (desktop AlarmStrip). */
export function BottomStrip() {
  const t = useTheme();
  const stage = useStageSize();
  const compact = stage.height < COMPACT_BELOW;
  const coarse = useCoarsePointer();
  const alarms = useSession((s) => s.alarms);
  const message = useSession((s) => s.message);
  const info = useSession((s) => s.linkInfo);
  const demo = useSession((s) => s.demo);
  const simulated = useSession((s) => s.ecu.simulated && s.ecu.state !== 'stopped');
  const now = useNow(message?.untilMs ?? null);
  const h = compact ? STRIP_H_COMPACT : STRIP_H;
  const alarm = worst(alarms, 'alarm');
  const warn = worst(alarms, 'warn');
  const said = message && message.untilMs > now ? message.text : '';
  const hints = !compact && !coarse;
  const fill = (bg: string, fg: string): CSSProperties => ({
    display: 'flex', alignItems: 'center', gap: 8, height: '100%', padding: '0 12px', background: bg, color: fg,
    fontSize: compact ? 12.5 : 14, fontWeight: 800, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0, flexShrink: 1,
  });
  return (
    <footer data-testid="bottom-strip"
      style={{ height: h, flexShrink: 0, display: 'flex', alignItems: 'stretch', background: t.panel, borderTop: `2px solid ${t.rule}`,
        color: t.fg, fontFamily: FONTS.sans, boxSizing: 'border-box', minWidth: 0 }}>
      {alarm && (
        <span data-testid="strip-alarm" style={fill(t.alarm, t.onAlarm)}>
          <Shape d={OCTAGON} color={t.onAlarm} size={compact ? 13 : 16} />
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{alarm}</span>
        </span>
      )}
      {warn && (
        <span data-testid="strip-warn" style={fill(t.warn, t.onWarn)}>
          <Shape d={TRIANGLE} color={t.onWarn} size={compact ? 13 : 16} />
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{warn}</span>
        </span>
      )}
      {!alarm && !warn && (
        <span data-testid="strip-ok" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0 12px', fontSize: compact ? 12.5 : 14, fontWeight: 800, whiteSpace: 'nowrap', flexShrink: 0 }}>
          <span aria-hidden style={{ width: 9, height: 9, background: t.ok, display: 'inline-block' }} />
          No active alarms
        </span>
      )}
      <span data-testid={said ? 'strip-message' : 'strip-info'}
        style={{ flex: '1 1 0', minWidth: 0, alignSelf: 'center', padding: '0 12px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          ...(said ? { fontSize: compact ? 12.5 : 14, fontWeight: 600, color: t.fg } : { fontFamily: FONTS.mono, fontSize: compact ? 11.5 : 13, fontWeight: 500, color: t.fg2, textAlign: 'right' }) }}>
        {said || info}
      </span>
      {(simulated || demo) && (
        <span data-testid="sim-badge" title="Data from the built-in simulator, not a car"
          style={{ alignSelf: 'center', margin: '0 8px', padding: '1px 6px', fontFamily: FONTS.mono, fontSize: 11, fontWeight: 800, letterSpacing: 1,
            color: t.onWarn, background: t.warn, whiteSpace: 'nowrap', flexShrink: 0 }}>
          {simulated ? 'SIMULATOR' : 'DEMO DATA'}
        </span>
      )}
      {hints && (
        <span data-testid="key-hints" style={{ alignSelf: 'center', padding: '0 10px', borderLeft: `1px solid ${t.line}`, fontFamily: FONTS.mono, fontSize: 13, fontWeight: 500, whiteSpace: 'nowrap', flexShrink: 0 }}>
          {KEY_HINTS}
        </span>
      )}
    </footer>
  );
}

