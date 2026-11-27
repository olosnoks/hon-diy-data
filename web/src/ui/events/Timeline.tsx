/**
 * The small timeline of one event: RPM with the idle target, throttle and MAP
 * from 10 s before to 5 s after it (design canvas "F3 Events").
 */
import type { LoadedDrive } from '../../core/types';
import { valueAt } from '../../core/analysis/cursors';
import { searchSorted } from '../../core/analysis/series';
import { FONTS, type Theme } from '../theme';
import { num } from '../replay/channels';
import type { DriveEvent } from './detect';

export const BEFORE_S = 10;
export const AFTER_S = 5;
const X0 = 90, X1 = 750;
const TICKS = [-10, -6, -3, 0, 3];

type S = { t: Float64Array; v: Float64Array };

/** Samples of s in [a, b], at most `max` of them (every k-th, keeping the extremes of each stride). */
export function windowPoints(s: S | undefined, a: number, b: number, max = 400): [number, number][] {
  if (!s) return [];
  const i0 = Math.max(0, searchSorted(s.t, a) - 1), i1 = Math.min(s.t.length, searchSorted(s.t, b, 'right') + 1);
  const n = i1 - i0;
  const stride = Math.max(1, Math.ceil(n / max));
  const out: [number, number][] = [];
  for (let i = i0; i < i1; i += stride) {
    let lo = i, hi = i;
    for (let j = i; j < Math.min(i + stride, i1); j++) {
      if (s.v[j]! < s.v[lo]!) lo = j;
      if (s.v[j]! > s.v[hi]!) hi = j;
    }
    const pick = lo === hi ? [lo] : lo < hi ? [lo, hi] : [hi, lo];
    for (const j of pick) if (Number.isFinite(s.v[j]!)) out.push([s.t[j]!, s.v[j]!]);
  }
  return out;
}

function extent(pts: [number, number][], extra: number[] = []): [number, number] {
  let lo = Infinity, hi = -Infinity;
  for (const [, v] of pts) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  for (const v of extra) if (Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  return Number.isFinite(lo) ? [lo, hi] : [0, 1];
}

export function Timeline({ drive, event: e, theme: c }: { drive: LoadedDrive; event: DriveEvent; theme: Theme }) {
  const a = e.t - BEFORE_S, b = e.t + AFTER_S;
  const x = (tt: number) => X0 + ((tt - a) / (b - a)) * (X1 - X0);
  const s = drive.series;
  const rpm = windowPoints(s.rpm, a, b), tps = windowPoints(s.tps, a, b), map = windowPoints(s.map, a, b);
  const target = s.idle_target ? valueAt(s.idle_target, e.t) : NaN;
  const [rlo0, rhi0] = extent(rpm, [target]);
  const rlo = Math.max(0, rlo0 - 100), rhi = rhi0 + 100;
  const yr = (v: number) => 140 - ((v - rlo) / (rhi - rlo || 1)) * 120;
  const tmax = Math.max(20, extent(tps)[1]);
  const yt = (v: number) => 200 - (v / tmax) * 40;
  const [mlo, mhi] = extent(map);
  const ym = (v: number) => 290 - ((v - (mlo - 5)) / (mhi - mlo + 10 || 1)) * 60;
  const line = (pts: [number, number][], y: (v: number) => number) =>
    pts.filter(([tt]) => tt >= a && tt <= b).map(([tt, v]) => `${x(tt).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const danger = [Math.max(rlo, 400), Math.min(rhi, 600)];
  const low = rpm.reduce<[number, number] | null>((m, p) => (p[0] >= e.t - 1.5 && p[0] <= e.t + 1.5 && (!m || p[1] < m[1]) ? p : m), null);

  return (
    <svg viewBox="0 0 760 330" width="100%" height="100%" preserveAspectRatio="none" role="img"
      aria-label={`RPM, idle target, throttle and MAP from ${BEFORE_S} s before to ${AFTER_S} s after the event`}>
      <g stroke={c.line} strokeWidth="1">
        {TICKS.map((k) => <line key={k} x1={x(e.t + k)} y1="10" x2={x(e.t + k)} y2="300" />)}
      </g>
      {e.t1 > e.t + 0.2 && e.kind === 'dropout' && (
        <rect data-testid="silence" x={x(e.t)} y="10" width={Math.max(2, x(Math.min(b, e.t1)) - x(e.t))} height="290" fill={c.series[2]} opacity="0.15" />
      )}
      <line x1={x(e.t)} y1="10" x2={x(e.t)} y2="300" stroke={c.alarm} strokeWidth="2" />
      <g fontFamily="Inter" fontSize="12" fontWeight="700" fill={c.fg}>
        <text x="8" y="40">RPM</text><text x="8" y="175">Throttle</text><text x="8" y="255">MAP</text>
      </g>
      {danger[1]! > danger[0]! && <rect x={X0} y={yr(danger[1]!)} width={X1 - X0} height={yr(danger[0]!) - yr(danger[1]!)} fill={c.warn} opacity="0.3" />}
      {Number.isFinite(target) && (
        <>
          <line x1={X0} y1={yr(target)} x2={X1} y2={yr(target)} stroke={c.fg} strokeWidth="1.5" strokeDasharray="6 4" />
          <text x={X1 - 2} y={yr(target) - 6} textAnchor="end" fontFamily="JetBrains Mono" fontSize="11" fill={c.fg}>target {num(target)}</text>
        </>
      )}
      <polyline data-testid="tl-rpm" points={line(rpm, yr)} fill="none" stroke={c.series[0]} strokeWidth="3" />
      {low && (e.kind === 'near_stall' || e.kind === 'stall') && (
        <text x={x(low[0]) + 6} y={yr(low[1]) + 18} fontFamily="JetBrains Mono" fontSize="11" fontWeight="700" fill={c.alarm}>{num(low[1])}</text>
      )}
      <line x1={X0} y1="200" x2={X1} y2="200" stroke={c.rule} strokeWidth="1" />
      <polyline data-testid="tl-tps" points={line(tps, yt)} fill="none" stroke={c.series[1]} strokeWidth="3" />
      <line x1={X0} y1="290" x2={X1} y2="290" stroke={c.rule} strokeWidth="1" />
      <polyline data-testid="tl-map" points={line(map, ym)} fill="none" stroke={c.series[2]} strokeWidth="3" />
      <g fontFamily={FONTS.mono} fontSize="11" fill={c.fg}>
        {TICKS.map((k) => <text key={k} x={x(e.t + k)} y="318" textAnchor="middle">{k === -10 ? '−10 s' : k < 0 ? `−${-k}` : k > 0 ? `+${k}` : '0'}</text>)}
      </g>
      {!rpm.length && <text x="420" y="90" textAnchor="middle" fontFamily="Inter" fontSize="13" fill={c.fg2}>no ECU data around this moment</text>}
    </svg>
  );
}
