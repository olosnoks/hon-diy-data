/**
 * The uPlot charts of F5: the curve (torque and hp vs rpm, the band, the
 * dashed D16Y8 reference, the VTEC zone, the logged maximum, a pull or a
 * baseline to compare with) and the pull trace (rpm, speed, TPS, MAP in time).
 */
import type uPlot from 'uplot';
import { FONTS, type Theme } from '../theme';
import type { ChartOptions } from './UPlotChart';
import { referenceHp, referenceTorque, thousands, VTEC_ZONE, type DynamicsGrid, type PowerResult } from './analysis';

export interface Overlay { label: string; points: Array<{ rpm: number; nm: number; hp: number }>; colour: string }

export const RPM_RANGE: [number, number] = [1000, 7000];
export const NM_RANGE: [number, number] = [0, 160];
export const HP_RANGE: [number, number] = [0, 140];

function alpha(hex: string, a: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function axis(t: Theme, extra: Partial<uPlot.Axis>): uPlot.Axis {
  return {
    stroke: t.fg, font: `500 11px ${FONTS.mono}`, labelFont: `700 12px ${FONTS.sans}`,
    grid: { stroke: t.line, width: 1 }, ticks: { stroke: t.line, width: 1 }, ...extra,
  };
}

/** x: every 250 rpm of the reference plus the measured bins, sorted. */
export function curveData(res: PowerResult | null, overlays: Overlay[]): uPlot.AlignedData {
  const xs = new Set<number>();
  for (let r = RPM_RANGE[0]; r <= RPM_RANGE[1]; r += 250) xs.add(r);
  res?.points.forEach((p) => xs.add(Math.round(p.rpm)));
  overlays.forEach((o) => o.points.forEach((p) => xs.add(Math.round(p.rpm))));
  const x = [...xs].sort((a, b) => a - b);
  const at = <T,>(pts: T[], rpm: (p: T) => number, v: (p: T) => number) => {
    const m = new Map(pts.map((p) => [Math.round(rpm(p)), v(p)]));
    return x.map((r) => (m.has(r) ? m.get(r)! : null));
  };
  const pts = res?.points ?? [];
  const cols: (number | null)[][] = [
    at(pts, (p) => p.rpm, (p) => p.nm),
    at(pts, (p) => p.rpm, (p) => p.hp),
    x.map((r) => referenceTorque(r)),
    x.map((r) => referenceHp(r)),
    at(pts, (p) => p.rpm, (p) => p.nmLo),
    at(pts, (p) => p.rpm, (p) => p.nmHi),
  ];
  for (const o of overlays) {
    cols.push(at(o.points, (p) => p.rpm, (p) => p.nm));
    cols.push(at(o.points, (p) => p.rpm, (p) => p.hp));
  }
  return [x, ...cols] as uPlot.AlignedData;
}

export function curveOptions(t: Theme, overlays: Overlay[], rpmMax: number | null, title: string): ChartOptions {
  const series: uPlot.Series[] = [
    {},
    { label: 'torque N·m', scale: 'nm', stroke: t.series[0], width: 3.5, spanGaps: true, points: { show: false } },
    { label: 'hp', scale: 'hp', stroke: t.series[1], width: 3.5, spanGaps: true, points: { show: false } },
    { label: 'D16Y8 torque (approx.)', scale: 'nm', stroke: t.comment, width: 2, dash: [6, 4], points: { show: false } },
    { label: 'D16Y8 hp (approx.)', scale: 'hp', stroke: t.comment, width: 2, dash: [6, 4], points: { show: false } },
    { label: 'band low', scale: 'nm', stroke: 'transparent', width: 0, spanGaps: true, points: { show: false } },
    { label: 'band high', scale: 'nm', stroke: 'transparent', width: 0, spanGaps: true, points: { show: false } },
  ];
  for (const o of overlays) {
    series.push({ label: `${o.label} torque`, scale: 'nm', stroke: o.colour, width: 2, spanGaps: true, points: { show: false } });
    series.push({ label: `${o.label} hp`, scale: 'hp', stroke: o.colour, width: 2, dash: [2, 3], spanGaps: true, points: { show: false } });
  }
  const draw = (u: uPlot) => {
    const ctx = u.ctx;
    const { left, top, width, height } = u.bbox;
    const px = (r: number) => u.valToPos(r, 'x', true);
    const dpr = typeof devicePixelRatio === 'number' ? devicePixelRatio : 1;
    ctx.save();
    // the VTEC zone
    const x0 = Math.max(left, px(VTEC_ZONE[0])), x1 = Math.min(left + width, px(VTEC_ZONE[1]));
    ctx.fillStyle = alpha(t.warn, 0.25);
    ctx.fillRect(x0, top, x1 - x0, height);
    ctx.fillStyle = t.fg;
    ctx.font = `700 ${12 * dpr}px ${FONTS.sans}`;
    ctx.textAlign = 'center';
    ctx.fillText('VTEC', (x0 + x1) / 2, top + 16 * dpr);
    // the highest rpm logged
    if (rpmMax != null && rpmMax > RPM_RANGE[0] && rpmMax < RPM_RANGE[1]) {
      const x = px(rpmMax);
      ctx.strokeStyle = t.alarm;
      ctx.lineWidth = 1.5 * dpr;
      ctx.setLineDash([4 * dpr, 3 * dpr]);
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + height); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = t.alarm;
      ctx.font = `700 ${11 * dpr}px ${FONTS.sans}`;
      ctx.textAlign = x > left + width - 120 * dpr ? 'right' : 'left';
      ctx.fillText(`logged max ${thousands(rpmMax)}`, x + (ctx.textAlign === 'right' ? -4 : 4) * dpr, top + height - 8 * dpr);
    }
    ctx.restore();
  };
  return {
    title,
    legend: { show: false },
    cursor: { drag: { x: false, y: false }, points: { show: false } },
    scales: { x: { time: false, range: RPM_RANGE }, nm: { range: NM_RANGE }, hp: { range: HP_RANGE } },
    axes: [
      axis(t, { label: 'engine rpm', values: (_u, vals) => vals.map((v) => (v % 1000 === 0 ? `${v / 1000}k` : '')) }),
      axis(t, { scale: 'nm', label: 'torque N·m', labelFont: `700 12px ${FONTS.sans}`, stroke: t.fg }),
      axis(t, { scale: 'hp', side: 1, label: 'hp', grid: { show: false } }),
    ],
    series,
    bands: [{ series: [6, 5], fill: alpha(t.series[0], 0.12) }],
    hooks: { drawClear: [draw] },
  };
}

// ---- the pull trace ----------------------------------------------------------------------

export const TRACE_PAD_S = 3;

export function traceData(grid: DynamicsGrid, t0: number, t1: number): uPlot.AlignedData {
  const x: number[] = [], rpm: (number | null)[] = [], kmh: (number | null)[] = [], tps: (number | null)[] = [], map: (number | null)[] = [];
  const v = (a: Float64Array, i: number) => (Number.isFinite(a[i]) ? a[i]! : null);
  for (let i = 0; i < grid.t.length; i++) {
    const tt = grid.t[i]!;
    if (tt < t0 - TRACE_PAD_S || tt > t1 + TRACE_PAD_S) continue;
    x.push(+(tt - t0).toFixed(3));
    rpm.push(v(grid.rpm, i)); kmh.push(v(grid.vss, i)); tps.push(v(grid.tps, i)); map.push(v(grid.map, i));
  }
  return [x, rpm, kmh, tps, map];
}

export function traceOptions(t: Theme, duration: number): ChartOptions {
  const draw = (u: uPlot) => {
    const { top, height, left, width } = u.bbox;
    const a = Math.max(left, u.valToPos(0, 'x', true)), b = Math.min(left + width, u.valToPos(duration, 'x', true));
    u.ctx.save();
    u.ctx.fillStyle = alpha(t.accent, 0.12);
    u.ctx.fillRect(a, top, b - a, height);
    u.ctx.restore();
  };
  return {
    legend: { show: true, live: true },
    cursor: { drag: { x: false, y: false } },
    scales: { x: { time: false }, rpm: { range: [0, 7000] }, pct: { range: [0, 120] } },
    axes: [
      axis(t, { label: 's from the start of the pull' }),
      axis(t, { scale: 'rpm', label: 'rpm' }),
      axis(t, { scale: 'pct', side: 1, label: 'km/h · TPS % · MAP kPa', grid: { show: false } }),
    ],
    series: [
      {},
      { label: 'rpm', scale: 'rpm', stroke: t.series[0], width: 2.5, points: { show: false } },
      { label: 'km/h', scale: 'pct', stroke: t.series[2], width: 2, points: { show: false } },
      { label: 'TPS %', scale: 'pct', stroke: t.series[4], width: 2, points: { show: false } },
      { label: 'MAP kPa', scale: 'pct', stroke: t.series[3], width: 2, points: { show: false } },
    ],
    hooks: { drawClear: [draw] },
  };
}
