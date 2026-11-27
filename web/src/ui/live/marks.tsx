/**
 * Small marks shared by the tiles (paint.py marker / shape): the verified /
 * unverified box, the warn triangle and alarm octagon (shape + colour, never
 * colour alone), and the sparkline.
 */
import type { CSSProperties } from 'react';
import type { History } from '../../core/session';

export function VerifiedMark({ verified, size = 9, ink, okColor, unvColor }: {
  verified: boolean; size?: number; ink?: string; okColor: string; unvColor: string;
}) {
  const base: CSSProperties = { flexShrink: 0, display: 'inline-block' };
  if (verified) {
    return <span role="img" aria-label="verified" title="address verified" data-mark="verified"
      style={{ ...base, width: size, height: size, background: ink ?? okColor }} />;
  }
  const s = Math.round(size * 0.78);
  return <span role="img" aria-label="unverified" title="address unverified" data-mark="unverified"
    style={{ ...base, width: s, height: s, border: `1px solid ${ink ?? unvColor}` }} />;
}

/** Warn = triangle, alarm = octagon. */
export function LevelIcon({ level, size = 12, color, blink }: { level: 1 | 2; size?: number; color: string; blink?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" role="img" aria-label={level === 2 ? 'alarm' : 'warning'}
      className={blink ? 'hl-blink' : undefined} style={{ flexShrink: 0 }}>
      {level === 2
        ? <polygon points="5,0 11,0 16,5 16,11 11,16 5,16 0,11 0,5" fill={color} />
        : <polygon points="8,0 16,15 0,15" fill={color} />}
    </svg>
  );
}

/** Last 60 s as a polyline; gaps (null) hold the previous point. */
export function Sparkline({ history, width = 100, height = 14, color, flag = false }: {
  history: History | undefined; width?: number; height?: number; color: string; flag?: boolean;
}) {
  const v = history?.v ?? [];
  const good = v.filter((x): x is number => x !== null && Number.isFinite(x));
  if (good.length < 2) return <svg width="100%" height={height} aria-hidden />;
  let lo = Math.min(...good);
  let hi = Math.max(...good);
  if (flag) { lo = 0; hi = 1; }
  const span = hi - lo || 1;
  const n = Math.max(v.length - 1, 1);
  let last = good[0]!;
  const pts = v.map((x, i) => {
    if (x !== null && Number.isFinite(x)) last = x;
    const y = height - 2 - ((last - lo) / span) * (height - 4);
    return `${((i / n) * width).toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden
      style={{ display: 'block', overflow: 'visible' }}>
      <polyline points={pts.join(' ')} fill="none" stroke={color} strokeWidth={1.5}
        strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** 45 degree hatch for stale / no data (paint.py hatch_brush). */
export function hatch(panel: string, sunk: string): string {
  return `repeating-linear-gradient(135deg, ${panel} 0 8px, ${sunk} 8px 16px)`;
}
