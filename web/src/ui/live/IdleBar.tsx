/**
 * RPM vs idle target (design canvas, Idle control group): 500-1200 rpm, the
 * stall zone below 600 rpm in yellow, a line at the target and a square at
 * the rpm. Every near-stall in the 2026-11-26 drives went below 600 rpm
 * (docs/analysis/2026-11-26/drive-idle-control.md).
 */
import { useSession } from '../../core/session';
import { FONTS } from '../theme';
import { formatNumber } from '../shell/format';
import { useTheme } from '../shell/useTheme';

export const IDLE_LO = 500;
export const IDLE_HI = 1200;
export const STALL_ZONE = 600;

export function idleFrac(rpm: number): number {
  return Math.min(1, Math.max(0, (rpm - IDLE_LO) / (IDLE_HI - IDLE_LO)));
}

export function IdleBar({ compact = false }: { compact?: boolean }) {
  const t = useTheme();
  const rpm = useSession((s) => (s.channels.rpm?.fresh ? s.channels.rpm.value : null));
  const target = useSession((s) => (s.channels.idle_target?.fresh ? s.channels.idle_target.value : null));
  const diff = rpm !== null && target !== null ? Math.round(rpm) - Math.round(target) : null;
  const text = `${rpm === null ? '---' : Math.round(rpm)} / ${target === null ? '---' : Math.round(target)}`
    + (diff === null ? '' : ` · ${formatNumber(diff, 0, true)}`);
  const inZone = rpm !== null && rpm > 50 && rpm < STALL_ZONE;
  return (
    <div data-testid="idle-bar" style={{ padding: compact ? '4px 8px 0 8px' : '8px 10px 2px 10px', display: 'flex', flexDirection: 'column', gap: compact ? 2 : 4, flexShrink: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, fontWeight: 700 }}>
        <span>RPM vs idle target</span>
        <span style={{ fontFamily: FONTS.mono, ...(inZone ? { background: t.warn, color: t.onWarn, padding: '0 4px' } : {}) }}>{text}</span>
      </div>
      <div style={{ position: 'relative', height: compact ? 12 : 18, background: t.sunk, border: `1px solid ${t.rule}` }}>
        <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${idleFrac(STALL_ZONE) * 100}%`, background: t.warn }} />
        {target !== null && (
          <div title="idle target" style={{ position: 'absolute', left: `calc(${idleFrac(target) * 100}% - 1.5px)`, top: -3, bottom: -3, width: 3, background: t.line }} />
        )}
        {rpm !== null && (
          <div title="rpm" style={{ position: 'absolute', left: `calc(${idleFrac(rpm) * 100}% - 5px)`, top: compact ? 1 : 3, width: 10, height: compact ? 8 : 10, background: t.accent, outline: inZone ? `2px solid ${t.onWarn}` : undefined }} />
        )}
      </div>
      {!compact && (
        <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: FONTS.mono, fontSize: 11, color: t.fg2 }}>
          <span>{IDLE_LO}</span><span>yellow = stall zone</span><span>{IDLE_HI}</span>
        </div>
      )}
    </div>
  );
}
