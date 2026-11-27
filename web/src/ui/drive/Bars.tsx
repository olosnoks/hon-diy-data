/**
 * F2 bars: rpm vs idle target, and the launch margin (how far the rpm is
 * above the stall line, with the low of the last launch).
 */
import { useSyncExternalStore, type ReactNode } from 'react';
import { useSession } from '../../core/session';
import { FONTS, type Theme } from '../theme';
import { formatNumber } from '../shell/format';
import { useTheme } from '../shell/useTheme';
import { idleFrac, IDLE_HI, IDLE_LO, STALL_ZONE } from '../live/IdleBar';
import { DANGER, LAUNCH0, STALL_LINE, stepLaunch, type LaunchState } from './launch';

const fresh = (s: ReturnType<typeof useSession.getState>, n: string) => (s.channels[n]?.fresh ? s.channels[n]!.value : null);

function Frame({ t, title, right, children, foot, testid }: {
  t: Theme; title: string; right: string; children: ReactNode; foot: ReactNode; testid: string;
}) {
  return (
    <section data-testid={testid} style={{ flex: '1 1 0', minWidth: 0, border: `2px solid ${t.rule}`, background: t.panel, padding: '8px 14px', display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 15, fontWeight: 800, letterSpacing: '0.04em' }}>{title}</span>
        <span style={{ fontFamily: FONTS.mono, fontSize: 20, fontWeight: 700 }}>{right}</span>
      </div>
      <div style={{ position: 'relative', height: 26, background: t.sunk, border: `2px solid ${t.rule}` }}>{children}</div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: FONTS.mono, fontSize: 12, color: t.fg2, fontWeight: 500 }}>{foot}</div>
    </section>
  );
}

export function IdleTargetBar() {
  const t = useTheme();
  const rpm = useSession((s) => fresh(s, 'rpm'));
  const target = useSession((s) => fresh(s, 'idle_target'));
  const diff = rpm !== null && target !== null ? Math.round(rpm) - Math.round(target) : null;
  const right = `${rpm === null ? '---' : Math.round(rpm)} / ${target === null ? '---' : Math.round(target)}${diff === null ? '' : ` · ${formatNumber(diff, 0, true)}`}`;
  return (
    <Frame t={t} testid="drive-idle-bar" title="RPM VS IDLE TARGET" right={right}
      foot={<><span>{IDLE_LO}</span><span>yellow = stall zone</span><span>{IDLE_HI}</span></>}>
      <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${idleFrac(STALL_ZONE) * 100}%`, background: t.warn }} />
      {target !== null && <div style={{ position: 'absolute', left: `calc(${idleFrac(target) * 100}% - 2px)`, top: -5, bottom: -5, width: 4, background: t.fg2 }} />}
      {rpm !== null && <div style={{ position: 'absolute', left: `calc(${idleFrac(rpm) * 100}% - 7px)`, top: 4, width: 14, height: 14, background: t.accent }} />}
    </Frame>
  );
}

const M_LO = 300;
const M_HI = 1300;
const mFrac = (rpm: number) => Math.min(1, Math.max(0, (rpm - M_LO) / (M_HI - M_LO)));

/*
 * The launch tracker runs from the first time F2 is shown and keeps running
 * while other screens are up, so a launch made on F1 still shows its low.
 */
let tracker: LaunchState = LAUNCH0;
let unsubscribe: (() => void) | null = null;
const listeners = new Set<() => void>();

function feed(s: ReturnType<typeof useSession.getState>): void {
  const rpm = s.channels.rpm;
  if (!rpm) return;
  const next = stepLaunch(tracker, { t: rpm.t, rpm: fresh(s, 'rpm'), vss: fresh(s, 'vss'), target: fresh(s, 'idle_target') });
  if (next === tracker) return;
  const visible = next.last !== tracker.last || next.active !== tracker.active;
  tracker = next;
  if (visible) listeners.forEach((l) => l());
}

function subscribeLaunch(listener: () => void): () => void {
  if (!unsubscribe) {
    feed(useSession.getState());
    unsubscribe = useSession.subscribe((s, prev) => { if (s.channels !== prev.channels) feed(s); });
  }
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Forget the launches seen (tests, a new drive). */
export function resetLaunch(): void {
  unsubscribe?.();
  unsubscribe = null;
  tracker = LAUNCH0;
}

/** The launch tracker's state; re-renders when a launch starts or ends. */
export function useLaunch(): LaunchState {
  return useSyncExternalStore(subscribeLaunch, () => tracker);
}

export function LaunchMarginBar() {
  const t = useTheme();
  const rpm = useSession((s) => fresh(s, 'rpm'));
  const launch = useLaunch();
  const margin = rpm === null ? null : Math.round(rpm) - STALL_LINE;
  const right = margin === null ? '---' : `${formatNumber(margin, 0, true)} rpm`;
  const low = launch.last;
  return (
    <Frame t={t} testid="launch-margin" title="LAUNCH MARGIN" right={right}
      foot={<>
        <span>stall line {STALL_LINE} (assumed)</span>
        <span data-testid="launch-low">{low === null ? 'no launch yet' : low === 0 ? 'last launch: STALLED' : `last launch low ${Math.round(low)} (${formatNumber(Math.round(low) - STALL_LINE, 0, true)})`}</span>
      </>}>
      <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${mFrac(STALL_LINE) * 100}%`, background: t.alarm }} />
      <div style={{ position: 'absolute', left: `${mFrac(STALL_LINE) * 100}%`, top: 0, bottom: 0, width: `${(mFrac(DANGER) - mFrac(STALL_LINE)) * 100}%`, background: t.warn }} />
      {low !== null && low > 0 && (
        <div title="last launch low" style={{ position: 'absolute', left: `calc(${mFrac(low) * 100}% - 2px)`, top: -5, bottom: -5, width: 4, background: t.livePoint }} />
      )}
      {rpm !== null && <div style={{ position: 'absolute', left: `calc(${mFrac(rpm) * 100}% - 7px)`, top: 4, width: 14, height: 14, background: t.accent }} />}
    </Frame>
  );
}
