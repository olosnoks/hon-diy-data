/** g-g diagram: lateral against longitudinal g at the cursor, with a 5 s trail. */
import type { LoadedDrive } from '../../core/types';
import { valueAt } from '../../core/analysis/cursors';
import type { Theme } from '../theme';

export const TRAIL_S = 5;
const R = 80;                 // px per g

/** [lat g, long g] every 0.1 s over the trail ending at t (drive time); NaN skipped. */
export function ggPoints(drive: LoadedDrive, t: number, trail = TRAIL_S): [number, number][] {
  const lat = drive.series.lat_g, lon = drive.series.accel_g;
  if (!lat || !lon) return [];
  const out: [number, number][] = [];
  for (let k = Math.round(trail * 10); k >= 0; k--) {
    const tt = t - k / 10;
    const x = valueAt(lat, tt, 'linear'), y = valueAt(lon, tt, 'linear');
    if (Number.isFinite(x) && Number.isFinite(y)) out.push([x, y]);
  }
  return out;
}

export function GG({ drive, t, theme: c }: { drive: LoadedDrive; t: number; theme: Theme }) {
  const pts = ggPoints(drive, t);
  const px = ([x, y]: [number, number]) => [100 + Math.max(-1.2, Math.min(1.2, x)) * R, 105 - Math.max(-1.2, Math.min(1.2, y)) * R];
  const last = pts.length ? px(pts[pts.length - 1]!) : null;
  return (
    <svg viewBox="0 0 200 200" width="100%" height="100%" role="img" aria-label="g-g diagram: lateral against longitudinal g, current point and the last 5 seconds">
      <circle cx="100" cy="105" r={R} fill="none" stroke={c.line} strokeWidth="1" />
      <circle cx="100" cy="105" r={R / 2} fill="none" stroke={c.line} strokeWidth="1" />
      <line x1="20" y1="105" x2="180" y2="105" stroke={c.line} />
      <line x1="100" y1="25" x2="100" y2="185" stroke={c.line} />
      {pts.length > 1 && <polyline data-testid="gg-trail" points={pts.map((p) => px(p).join(',')).join(' ')} fill="none" stroke={c.rule} strokeWidth="2" />}
      {last && <circle data-testid="gg-now" cx={last[0]} cy={last[1]} r="6" fill={c.livePoint} />}
      <g fontFamily="Inter" fontSize="10.5" fontWeight="700" fill={c.fg2}>
        <text x="6" y="16">g-g · 5 s trail</text>
        <text x="146" y="101">0.5 g</text>
        <text x="104" y="22">accel</text>
      </g>
      {!pts.length && <text x="100" y="150" textAnchor="middle" fontSize="10.5" fill={c.fg2} fontFamily="Inter">no accel / GPS here</text>}
    </svg>
  );
}
