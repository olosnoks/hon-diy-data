/**
 * The lanes (port of the LaneBlock of hondalog/ui/screens/replay.py on uPlot):
 * one stacked plot per channel on one time axis, each drawn from the replay
 * model's min/max envelope for the window at the plot's pixel width, a second
 * drive in grey under it, the rpm lane with the idle target dashed. Cursor A
 * solid, cursor B dashed; each lane shows its value at A and, with B, A − B.
 *
 * Mouse: click / drag = cursor A, Shift+click = cursor B, wheel = zoom around
 * the pointer, Shift+wheel = pan. A lane's × removes it.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent, type WheelEvent } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import type { LoadedDrive } from '../../core/types';
import type { ReplayModel } from '../../core/analysis/replay';
import { FONTS, type Theme } from '../theme';
import { channelMeta, num, valueText } from './channels';
import type { DriveEvent } from '../events/detect';
import { clock, signed } from './fmt';

export interface LaneDef { id: string; extra?: string }

export const DEFAULT_LANES: LaneDef[] = [
  { id: 'rpm', extra: 'idle_target' }, { id: 'vss' }, { id: 'tps' }, { id: 'map' }, { id: 'lat_g' },
];

export const AXIS_H = 22;

type Trace = { x: Float64Array; y: Float64Array };

/** y of a trace at each x (last sample at or before x; NaN outside). */
export function resample(x: ArrayLike<number>, tr: Trace | null): (number | null)[] {
  const out: (number | null)[] = new Array(x.length).fill(null);
  if (!tr || !tr.x.length) return out;
  let j = 0;
  for (let i = 0; i < x.length; i++) {
    const xi = x[i]!;
    while (j + 1 < tr.x.length && tr.x[j + 1]! <= xi) j++;
    if (tr.x[j]! > xi || xi > tr.x[tr.x.length - 1]! + 5) continue;
    const v = tr.y[j]!;
    out[i] = Number.isFinite(v) ? v : null;
  }
  return out;
}

/** Lane y range: the data's min/max padded (replay.py set_view), flags 0..1. */
export function laneRange(drive: LoadedDrive, id: string, extra?: string): [number, number] {
  const meta = channelMeta(id, drive);
  if (meta.flag) return [-0.15, 1.15];
  let lo = Infinity, hi = -Infinity;
  for (const name of [id, extra]) {
    const s = name ? drive.series[name] : undefined;
    if (!s) continue;
    for (let i = 0; i < s.v.length; i++) { const v = s.v[i]!; if (Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }
  }
  if (!Number.isFinite(lo)) return [meta.min ?? 0, meta.max ?? 1];
  const nominal = (meta.max ?? hi) - (meta.min ?? lo);
  const pad = Math.max(0.1 * (hi - lo), 0.04 * (Number.isFinite(nominal) ? nominal : 0), 1e-6);
  return [lo - pad, hi + pad];
}

/** Time axis step: the first that leaves 64 px between labels (replay.py). */
export function axisStep(span: number, width: number): number {
  return [0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 300, 600].find((s) => (s / span) * width >= 64) ?? 600;
}

export function laneColour(theme: Theme, index: number): string {
  const order = [0, 3, 4, 2, 1];
  return theme.series[order[index % order.length]!]!;
}

interface PlotProps {
  model: ReplayModel; id: string; extra?: string; flag: boolean;
  width: number; height: number; colour: string; theme: Theme;
  range: [number, number]; window: [number, number]; overlayKey: string;
}

function LanePlot({ model, id, extra, flag, width, height, colour, theme, range, window: win, overlayKey }: PlotProps) {
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const scales = useRef({ x: win, y: range });
  scales.current = { x: win, y: range };

  const data = useMemo(() => {
    const columns = Math.max(10, Math.floor(width));
    const main = model.trace(id, columns);
    const x = Array.from(main.x);
    const y = Array.from(main.y, (v) => (Number.isFinite(v) ? v : null));
    const over = resample(main.x, model.overlayTrace(id, columns));
    const ex = extra ? resample(main.x, model.has(extra) ? model.trace(extra, columns) : null) : x.map(() => null);
    return [x, y, over, ex] as uPlot.AlignedData;
    // overlayKey: the second drive or its offset changed
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model, id, extra, width, win[0], win[1], overlayKey]);

  useEffect(() => {
    if (!host.current || width < 10 || height < 10) return;
    const stepped = flag && uPlot.paths?.stepped ? uPlot.paths.stepped({ align: 1 }) : undefined;
    let u: uPlot | null = null;
    try {
      u = new uPlot({
        width: Math.floor(width), height: Math.floor(height), pxAlign: 0,
        legend: { show: false }, cursor: { show: false }, select: { show: false, left: 0, top: 0, width: 0, height: 0 },
        scales: {
          x: { time: false, auto: false, range: () => [scales.current.x[0], scales.current.x[1]] },
          y: { auto: false, range: () => [scales.current.y[0], scales.current.y[1]] },
        },
        axes: [{ show: false }, { show: false }],
        series: [
          {},
          { stroke: colour, width: 2, paths: stepped, spanGaps: false },
          { stroke: theme.rule, width: 2, paths: stepped, spanGaps: false },
          { stroke: theme.fg2, width: 1.5, dash: [5, 4], spanGaps: true },
        ],
      }, data, host.current);
    } catch (err) {
      console.error('uPlot failed', err);
    }
    plot.current = u;
    return () => { u?.destroy(); plot.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [width, height, colour, theme, flag]);

  useEffect(() => {
    const u = plot.current;
    if (!u) return;
    u.batch(() => {
      u.setData(data, false);
      u.setScale('x', { min: win[0], max: win[1] });
      u.setScale('y', { min: range[0], max: range[1] });
    });
  }, [data, win, range]);

  return <div ref={host} data-lane-plot={id} style={{ position: 'absolute', inset: 0 }} />;
}

export interface LanesProps {
  model: ReplayModel;
  drive: LoadedDrive;
  lanes: LaneDef[];
  events: DriveEvent[];
  theme: Theme;
  window: [number, number];
  cursorA: number;
  cursorB: number | null;
  overlayKey: string;
  onCursor(t: number, which: 'a' | 'b'): void;
  onZoom(factor: number, around: number): void;
  onPan(fraction: number): void;
  onRemove(id: string): void;
}

export function Lanes(props: LanesProps) {
  const { model, drive, lanes, events, theme: t, window: win, cursorA, cursorB, overlayKey } = props;
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 600, h: 400 });
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => { if (el.clientWidth > 0 && el.clientHeight > 0) setSize({ w: el.clientWidth, h: el.clientHeight }); };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const laneH = Math.max(24, (size.h - AXIS_H) / Math.max(1, lanes.length));
  const span = win[1] - win[0] || 1;
  const xOf = (tt: number) => ((tt - win[0]) / span) * size.w;
  const tOf = (x: number) => win[0] + (x / size.w) * span;
  const unix0 = drive.info.startedUnix + model.origin;

  const ranges = useMemo(() => Object.fromEntries(lanes.map((l) => [l.id, laneRange(drive, l.id, l.extra)])), [drive, lanes]);
  const readouts = model.readouts(lanes.flatMap((l) => (l.extra ? [l.id, l.extra] : [l.id])), false);

  const drag = useRef(false);
  const pointT = (e: PointerEvent<HTMLDivElement> | WheelEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return tOf(r.width > 0 ? ((e.clientX - r.left) / r.width) * size.w : 0);
  };
  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    drag.current = true;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    props.onCursor(pointT(e), e.shiftKey ? 'b' : 'a');
  };
  const onMove = (e: PointerEvent<HTMLDivElement>) => { if (drag.current && e.buttons & 1) props.onCursor(pointT(e), e.shiftKey ? 'b' : 'a'); };
  const onUp = () => { drag.current = false; };
  const onWheel = (e: WheelEvent<HTMLDivElement>) => {
    const d = e.deltaY || e.deltaX;
    if (!d) return;
    if (e.shiftKey) props.onPan(d < 0 ? -0.15 : 0.15);
    else props.onZoom(d < 0 ? 0.8 : 1.25, pointT(e));
  };

  const step = axisStep(span, size.w);
  const ticks: number[] = [];
  for (let k = Math.ceil(win[0] / step - 1e-9); k * step <= win[1] + 1e-9 && ticks.length < 60; k++) ticks.push(k * step);

  const evColour = (e: DriveEvent) => ({ stall: t.alarm, near_stall: t.warn, dropout: t.series[2], key_off: t.comment, vtec: t.ok, harsh_accel: t.unverified, harsh_brake: t.unverified, corner: t.unverified, knock: t.unverified, mark: t.fg }[e.kind]);
  const inWin = events.filter((e) => e.t1 + 0.5 >= win[0] + model.origin && e.t0 - 0.5 <= win[1] + model.origin);

  return (
    <div ref={box} style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden', background: t.panel }}>
      {lanes.map((l, i) => {
        const meta = channelMeta(l.id, drive);
        const r = readouts[l.id];
        const ex = l.extra ? readouts[l.extra] : undefined;
        const has = model.has(l.id);
        const delta = r?.stats && !meta.flag ? `Δ ${signed(-r.stats.delta, meta.decimals)}` : '';
        return (
          <div key={l.id} data-lane={l.id} style={{ position: 'absolute', left: 0, right: 0, top: i * laneH, height: laneH, borderBottom: `1px solid ${t.line}` }}>
            {has ? (
              <LanePlot model={model} id={l.id} extra={l.extra} flag={!!meta.flag} width={size.w} height={laneH - 1}
                colour={laneColour(t, i)} theme={t} range={ranges[l.id] ?? [0, 1]} window={win} overlayKey={overlayKey} />
            ) : (
              <div style={{ position: 'absolute', inset: 0, background: `repeating-linear-gradient(45deg, transparent 0 6px, ${t.sunk} 6px 9px)` }} />
            )}
            <div style={{ position: 'absolute', left: 6, top: 3, display: 'flex', gap: 8, alignItems: 'baseline', pointerEvents: 'none', zIndex: 2,
              background: t.panel + 'CC', padding: '0 4px' }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: t.fg }}>
                {meta.label}{meta.units && meta.units !== 'rpm' && !meta.label.endsWith(` ${meta.units}`) ? ` ${meta.units}` : ''}{l.extra ? ` · ${l.extra === 'idle_target' ? 'target' : channelMeta(l.extra, drive).label}` : ''}
              </span>
              <span data-value={l.id} style={{ fontFamily: FONTS.mono, fontSize: 13, fontWeight: 700, color: t.fg }}>
                {has ? valueText({ ...meta, units: '' }, r?.value ?? NaN) : 'no data'}
                {ex && Number.isFinite(ex.value) ? ` / ${num(ex.value)}` : ''}
              </span>
              {delta && <span style={{ fontFamily: FONTS.mono, fontSize: 12, fontWeight: 700, color: t.accent }}>{delta}</span>}
            </div>
            {lanes.length > 1 && (
              <button type="button" aria-label={`Remove lane ${meta.label}`} onClick={() => props.onRemove(l.id)}
                style={{ position: 'absolute', right: 4, top: 2, zIndex: 3, background: 'transparent', color: t.fg2, border: 0, fontSize: 14, fontWeight: 700, cursor: 'pointer', padding: '0 4px' }}>×</button>
            )}
          </div>
        );
      })}

      {/* events, cursors, pointer handling */}
      <div data-testid="lanes-overlay" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onWheel={onWheel}
        style={{ position: 'absolute', left: 0, right: 0, top: 0, height: size.h - AXIS_H, cursor: 'ew-resize', zIndex: 1 }}>
        {inWin.map((e) => {
          const x0 = xOf(e.t0 - model.origin), x1 = xOf(e.t1 - model.origin);
          if (e.kind === 'near_stall' || e.kind === 'stall') {
            return (
              <div key={e.id} style={{ position: 'absolute', left: Math.min(x0, x1) - 3, width: Math.max(6, Math.abs(x1 - x0) + 6), top: 0, height: laneH, background: evColour(e), opacity: 0.18 }} />
            );
          }
          return <div key={e.id} title={e.title} style={{ position: 'absolute', left: xOf(e.t - model.origin) - 1, width: 2, top: 0, bottom: 0, background: evColour(e), opacity: 0.6 }} />;
        })}
        {inWin.filter((e) => e.kind === 'near_stall' || e.kind === 'stall').map((e) => (
          <span key={e.id + 'l'} style={{ position: 'absolute', left: xOf(e.t0 - model.origin) - 4, top: laneH + 2, transform: 'translateX(-100%)', fontSize: 10.5, fontWeight: 700, color: evColour(e), whiteSpace: 'nowrap' }}>
            {e.kind === 'stall' ? 'stall' : `dip ${num(e.value)}`}
          </span>
        ))}
        {cursorB !== null && cursorB >= win[0] && cursorB <= win[1] && (
          <div data-testid="cursor-b" style={{ position: 'absolute', left: xOf(cursorB) - 1, top: 0, bottom: 0, width: 0, borderLeft: `2px dashed ${t.accent}` }}>
            <span style={{ position: 'absolute', bottom: 0, left: -8, width: 16, textAlign: 'center', background: t.accent, color: t.onAccent, fontSize: 11, fontWeight: 800 }}>B</span>
          </div>
        )}
        {cursorA >= win[0] && cursorA <= win[1] && (
          <div data-testid="cursor-a" style={{ position: 'absolute', left: xOf(cursorA) - 1, top: 0, bottom: 0, width: 2, background: t.livePoint }}>
            {cursorB !== null && <span style={{ position: 'absolute', bottom: 0, left: -7, width: 16, textAlign: 'center', background: t.livePoint, color: t.bg, fontSize: 11, fontWeight: 800 }}>A</span>}
          </div>
        )}
      </div>

      <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: AXIS_H, borderTop: `1px solid ${t.line}`, background: t.panel }}>
        {ticks.map((tt) => (
          <span key={tt} style={{ position: 'absolute', left: Math.min(Math.max(2, xOf(tt) - 30), size.w - 62), width: 60, textAlign: 'center', top: 3,
            fontFamily: FONTS.mono, fontSize: 11, color: t.fg }}>
            {step < 1 ? clock(unix0 + tt, 1).slice(3) : clock(unix0 + tt)}
          </span>
        ))}
      </div>
    </div>
  );
}
