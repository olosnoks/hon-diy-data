/**
 * The big drive tile (tiles.py DRIVE metrics): label and units in a 40 px
 * header with the verified mark, a huge right-aligned value, a gauge with
 * the session min / max ticks, the last 60 s, and min / max in the footer.
 * Same alarm language as F1: warn = yellow band + triangle, alarm = red +
 * octagon, stale = hatched "---".
 */
import { memo } from 'react';
import { alarmLevel, alarmUnacked, useSession } from '../../core/session';
import { FONTS } from '../theme';
import { formatBound, formatValue, isFlag, isVerified, unitsOf } from '../shell/format';
import { COMPACT_BELOW, useStageSize } from '../shell/Stage';
import { useTheme } from '../shell/useTheme';
import { hatch, LevelIcon, Sparkline, VerifiedMark } from '../live/marks';

/** Font size that fits `text` in a big tile (tiles.py _fit_value steps). */
export function bigValuePx(text: string, base = 104): number {
  const n = text.length;
  const scale = n <= 4 ? 1 : n === 5 ? 0.85 : n === 6 ? 0.72 : 0.6;
  return Math.round(base * scale);
}

export const BigTile = memo(function BigTile({ name }: { name: string }) {
  const compact = useStageSize().height < COMPACT_BELOW;   // a phone in landscape: the number fits its box
  const t = useTheme();
  const st = useSession((s) => s.channels[name]);
  const history = useSession((s) => s.history[name]);
  const units = useSession((s) => s.units);
  const level = useSession((s) => alarmLevel(s, name));
  const unacked = useSession((s) => alarmUnacked(s, name));

  const stale = !st || !st.fresh || st.value === null;
  const flag = st ? isFlag(st.spec) : false;
  const text = formatValue(st, units, 1);
  const unit = st && !flag ? unitsOf(st.spec, units) : '';
  const alarm = level === 2;
  const warn = level === 1;
  const ink = alarm ? t.onAlarm : t.fg;
  const span = st ? st.spec.max - st.spec.min || 1 : 1;
  const frac = (v: number | null) => (v === null || !st ? -1 : Math.min(1, Math.max(0, (v - st.spec.min) / span)));

  return (
    <div
      data-testid={`big-${name}`}
      data-level={level}
      data-stale={stale || undefined}
      style={{
        minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column', color: ink,
        border: `2px solid ${alarm ? t.alarm : t.rule}`, boxShadow: warn ? `inset 0 0 0 3px ${t.warn}` : undefined,
        background: alarm ? t.alarm : stale ? hatch(t.panel, t.sunk) : t.panel,
      }}
    >
      <div style={{
        height: 40, flexShrink: 0, display: 'flex', alignItems: 'center', gap: 8, padding: '0 10px 0 12px',
        ...(warn ? { background: t.warn, color: t.onWarn } : {}),
      }}>
        {level > 0 && <LevelIcon level={level as 1 | 2} size={24} color={alarm ? t.onAlarm : t.onWarn} blink={alarm && unacked} />}
        <span style={{ fontSize: 22, fontWeight: 800, letterSpacing: '0.04em', flexGrow: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {(st?.spec.label ?? name).toUpperCase()}
        </span>
        {unit && <span style={{ fontSize: 22, fontWeight: 700 }}>{unit}</span>}
        <VerifiedMark verified={st ? isVerified(st.spec) : false} size={18} okColor={t.ok} unvColor={t.unverified}
          ink={alarm ? t.onAlarm : warn ? t.onWarn : undefined} />
      </div>
      <div style={{ flexGrow: 1, minHeight: 0, display: 'flex', alignItems: 'center', justifyContent: 'flex-end', padding: '0 14px', overflow: 'hidden' }}>
        <span data-testid={`bigvalue-${name}`} style={{ fontFamily: FONTS.mono, display: 'block', fontSize: bigValuePx(text, compact ? 84 : 104), fontWeight: 700, lineHeight: compact ? 0.9 : 1, letterSpacing: '-0.03em', whiteSpace: 'nowrap' }}>
          {text}
        </span>
      </div>
      {!flag && (
        <div style={{ position: 'relative', height: 22, margin: '6px 14px 10px', background: alarm ? t.alarm : t.sunk, border: `2px solid ${alarm ? ink : t.rule}`, flexShrink: 0 }}>
          {!stale && <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${frac(st!.value) * 100}%`, background: ink }} />}
          {st && !stale && [st.min, st.max].map((v, i) => frac(v) >= 0 && (
            <div key={i} style={{ position: 'absolute', left: `calc(${frac(v) * 100}% - 1.5px)`, top: -7, bottom: -7, width: 3, background: alarm ? ink : t.accent }} />
          ))}
        </div>
      )}
      <div style={{ height: 44, padding: '0 2px', flexShrink: 0 }}>
        {!stale && <Sparkline history={history} height={44} width={300} color={ink} flag={flag} />}
      </div>
      <div style={{ height: 30, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 12px', fontFamily: FONTS.mono, fontSize: 18, fontWeight: 700 }}>
        {!st ? <span>not in address map</span> : flag ? <span>{text}</span> : (
          <>
            <span>▼ {formatBound(st.min, st.spec, units, 1)}</span>
            <span style={{ fontSize: 14 }}>{st.spec && 'source' in st.spec && st.spec.source === 'BB' ? 'BLACK BOX' : ''}</span>
            <span>▲ {formatBound(st.max, st.spec, units, 1)}</span>
          </>
        )}
      </div>
    </div>
  );
});
