/**
 * The F1 sensor tile (tiles.py, sized to the design canvas): verified /
 * unverified mark and label, value and units, sparkline with session
 * min / max, one-line note. Alarm states use shape and colour: warn = yellow
 * header band, ring and triangle; alarm = red tile and octagon (blinks until
 * acknowledged with Esc); stale or missing = hatched, "---".
 */
import { memo } from 'react';
import { useCompact } from './compact';
import { useShallow } from 'zustand/react/shallow';
import { alarmLevel, alarmUnacked, useSession } from '../../core/session';
import { FONTS } from '../theme';
import { formatBound, formatValue, isFlag, isVerified, unitsOf } from '../shell/format';
import { useTheme } from '../shell/useTheme';
import type { TileDef } from './layout';
import { hatch, LevelIcon, Sparkline, VerifiedMark } from './marks';

export const Tile = memo(function Tile({ def }: { def: TileDef }) {
  const t = useTheme();
  const st = useSession((s) => s.channels[def.name]);
  const history = useSession((s) => s.history[def.name]);
  const units = useSession((s) => s.units);
  const level = useSession((s) => alarmLevel(s, def.name));
  const unacked = useSession((s) => alarmUnacked(s, def.name));
  const note = useSession((s) => (typeof def.note === 'function' ? def.note(s) : def.note ?? ''));
  const noteWarn = useSession((s) => (typeof def.noteWarn === 'function' ? def.noteWarn(s) : !!def.noteWarn));
  const shown = useSession(useShallow((s) => (def.display ? def.display(s) : undefined)));
  const compact = useCompact();

  const derived = def.display !== undefined;
  const missing = derived ? shown === null : !st;
  const stale = missing || (!derived && (!st!.fresh || st!.value === null));
  const label = def.label ?? st?.spec.label ?? def.name;
  const verified = shown?.verified ?? (st ? isVerified(st.spec) : false);
  const text = shown ? shown.text : derived ? '---' : formatValue(st, units);
  const unit = shown ? shown.unit ?? '' : st && !isFlag(st.spec) ? unitsOf(st.spec, units) : '';
  const alarm = level === 2;
  const warn = level === 1;
  const ink = alarm ? t.onAlarm : t.fg;
  const ink2 = alarm ? t.onAlarm : t.fg2;
  const showMinMax = !derived && st && !isFlag(st.spec) && !stale;

  return (
    <div
      data-testid={`tile-${def.name}`}
      data-level={level}
      data-stale={stale || undefined}
      style={{
        border: `1px solid ${alarm ? t.alarm : warn ? t.warn : t.line}`,
        boxShadow: warn ? `inset 0 0 0 2px ${t.warn}` : undefined,
        background: alarm ? t.alarm : stale ? hatch(t.panel, t.sunk) : 'transparent',
        color: ink, padding: compact ? '3px 6px' : '6px 8px', display: 'flex', flexDirection: 'column', gap: compact ? 0 : 2, minWidth: 0,
      }}
    >
      <div
        style={{
          display: 'flex', alignItems: 'center', gap: 5, fontSize: 11.5, fontWeight: 700,
          whiteSpace: 'nowrap', overflow: 'hidden',
          ...(warn ? { background: t.warn, color: t.onWarn, margin: compact ? '-3px -6px 0' : '-6px -8px 0', padding: compact ? '2px 6px' : '3px 8px' } : {}),
        }}
      >
        <VerifiedMark verified={verified} okColor={t.ok} unvColor={t.unverified}
          ink={alarm ? t.onAlarm : warn ? t.onWarn : undefined} />
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', flexGrow: 1 }}>{label}</span>
        {level > 0 && <LevelIcon level={level as 1 | 2} color={alarm ? t.onAlarm : t.onWarn} blink={alarm && unacked} />}
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 4, minWidth: 0 }}>
        <span data-testid={`value-${def.name}`}
          style={{ fontFamily: FONTS.mono, fontSize: compact ? 19 : 24, fontWeight: 700, lineHeight: compact ? 1.05 : 1.1, whiteSpace: 'nowrap' }}>
          {text}
        </span>
        {unit && !stale && <span style={{ fontSize: 11.5, fontWeight: 600, color: ink2 }}>{unit}</span>}
      </div>
      {!derived && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, height: compact ? 10 : 14 }}>
          <div style={{ flexGrow: 1, minWidth: 0 }}>
            {!stale && <Sparkline history={history} height={compact ? 10 : 14} color={alarm ? t.onAlarm : t.accent} flag={st ? isFlag(st.spec) : false} />}
          </div>
          {showMinMax && (
            <span style={{ fontFamily: FONTS.mono, fontSize: 10, fontWeight: 500, color: ink2, whiteSpace: 'nowrap' }}
              title="session min / max">
              ▼{formatBound(st.min, st.spec, units)} ▲{formatBound(st.max, st.spec, units)}
            </span>
          )}
        </div>
      )}
      <div style={{ fontSize: compact ? 10.5 : 11, lineHeight: compact ? 1.2 : undefined, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        color: alarm ? t.onAlarm : noteWarn ? t.alarm : t.fg2 }}>
        {missing ? 'not in address map' : note || ' '}
      </div>
    </div>
  );
});
