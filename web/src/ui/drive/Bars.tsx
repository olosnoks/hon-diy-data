/**
 * F2 bars: rpm vs idle target, and the launch margin: rpm − idle target on a
 * −300…+700 rpm scale, red below −150, yellow −150…0 (desktop tiles.py IdleBar
 * "launch"), with the low of the last launch under it.
 */
import { useSyncExternalStore, type ReactNode } from 'react';
import { useSession } from '../../core/session';
import { FONTS, type Theme } from '../theme';
import { formatNumber } from '../shell/format';
import { useTheme } from '../shell/useTheme';
import { idleFrac, IDLE_HI, IDLE_LO, STALL_ZONE } from '../live/IdleBar';
import { LAUNCH0, LAUNCH_HI, LAUNCH_LO, LAUNCH_RED, signedRpm, stepLaunch, type LaunchState } from './launch';

const fresh = (s: ReturnType<typeof useSession.getState>, n: string) => (s.channels[n]?.fresh ? s.channels[n]!.value : null);

function Frame({ t, title, right, rightColor, children, foot, under, testid }: {
  t: Theme; title: string; right: string; rightColor?: string; children: ReactNode; foot: ReactNode; under?: ReactNode; testid: string;
}) {
  return (
    <section data-testid={testid} style={{ flex: '1 1 0', minWidth: 0, border: `2px solid ${t.rule}`, background: t.panel, padding: '8px 14px', display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
        <span data-testid={`${testid}-title`} style={{ fontSize: 15, fontWeight: 800 }}>{title}</span>
        <span data-testid={`${testid}-readout`} style={{ fontFamily: FONTS.mono, fontSize: 20, fontWeight: 700, color: rightColor ?? t.fg }}>{right}</span>
      </div>
      <div style={{ position: 'relative', height: 26, background: t.sunk, border: `2px solid ${t.rule}` }}>{children}</div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: FONTS.mono, fontSize: 12, color: t.fg2, fontWeight: 500 }}>{foot}</div>
      {under}
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
    <Frame t={t} testid="drive-idle-bar" title="RPM vs idle target" right={right}
      foot={<><span>{IDLE_LO}</span><span>yellow = stall zone</span><span>{IDLE_HI}</span></>}>
      <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${idleFrac(STALL_ZONE) * 100}%`, background: t.warn }} />
      {target !== null && <div style={{ position: 'absolute', left: `calc(${idleFrac(target) * 100}% - 2px)`, top: -5, bottom: -5, width: 4, background: t.fg2 }} />}
      {rpm !== null && <div style={{ position: 'absolute', left: `calc(${idleFrac(rpm) * 100}% - 7px)`, top: 4, width: 14, height: 14, background: t.accent }} />}
    </Frame>
  );
}

/** Where a margin (rpm − idle target) sits on the −300…+700 scale, 0..1. */
export const mFrac = (m: number) => Math.min(1, Math.max(0, (m - LAUNCH_LO) / (LAUNCH_HI - LAUNCH_LO)));

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

/** "last launch low 462 rpm" (or the stall, or none yet). */
export function launchLowText(low: number | null): string {
  if (low === null) return 'no launch yet';
  if (low === 0) return 'last launch: STALLED';
  return `last launch low ${Math.round(low)} rpm`;
}

export function LaunchMarginBar() {
  const t = useTheme();
  const rpm = useSession((s) => fresh(s, 'rpm'));
  const target = useSession((s) => fresh(s, 'idle_target'));
  const launch = useLaunch();
  const margin = rpm !== null && target !== null ? Math.round(rpm) - Math.round(target) : null;
  const zone = margin === null ? '' : margin < LAUNCH_RED ? 'alarm' : margin < 0 ? 'warn' : '';
  const right = margin === null ? '---' : `${signedRpm(margin)} rpm`;
  const low = launch.last;
  const lowMargin = low !== null && low > 0 && target !== null ? low - target : null;
  return (
    <Frame t={t} testid="launch-margin" title="Launch margin" right={right}
      rightColor={zone === 'alarm' ? t.alarm : zone === 'warn' ? t.warn : undefined}
      foot={<><span>{signedRpm(LAUNCH_LO)}</span><span>target</span><span>{signedRpm(LAUNCH_HI)}</span></>}
      under={<div data-testid="launch-low" style={{ fontFamily: FONTS.mono, fontSize: 13, fontWeight: 700, color: low === 0 ? t.alarm : t.fg }}>{launchLowText(low)}</div>}>
      <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${mFrac(LAUNCH_RED) * 100}%`, background: t.alarm }} />
      <div style={{ position: 'absolute', left: `${mFrac(LAUNCH_RED) * 100}%`, top: 0, bottom: 0, width: `${(mFrac(0) - mFrac(LAUNCH_RED)) * 100}%`, background: t.warn }} />
      <div style={{ position: 'absolute', left: `calc(${mFrac(0) * 100}% - 2px)`, top: -5, bottom: -5, width: 4, background: t.fg2 }} />
      {lowMargin !== null && (
        <div title="last launch low" style={{ position: 'absolute', left: `calc(${mFrac(lowMargin) * 100}% - 2px)`, top: -5, bottom: -5, width: 4, background: t.livePoint }} />
      )}
      {margin !== null && (
        <div data-testid="launch-marker" style={{ position: 'absolute', left: `calc(${mFrac(margin) * 100}% - 7px)`, top: 4, width: 14, height: 14, boxSizing: 'border-box',
          background: margin < 0 ? t.bg : t.accent, border: margin < 0 ? `2px solid ${t.fg}` : 0 }} />
      )}
    </Frame>
  );
}
