/**
 * Time in cell (the old Map 3D, drawn flat): the RPM x MAP grid, each cell
 * coloured by the samples logged there (log scale) or by the mean injector
 * time, ignition or fuel trim. Grey = no samples in that cell yet.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { FONTS, type Theme } from '../theme';
import { DIV_RAMP, SEQ_RAMP, div, rgbHex, seq } from '../gps/geo/ramps';
import { CELL_KEYS, cellValue, coverage, type CellKey, type CellMap } from './cells';
import { Tabs } from './widgets';

export function cellColour(cells: CellMap, key: CellKey, j: number, i: number, maxHits: number): string | null {
  const v = cellValue(cells, key, j, i);
  if (!Number.isFinite(v) || (key === 'hits' && v <= 0)) return null;
  if (key === 'hits') return rgbHex(seq(Math.log(v) / Math.log(Math.max(2, maxHits))));
  const spec = CELL_KEYS.find((k) => k.key === key)!;
  const [lo, hi] = spec.range!;
  const f = (v - lo) / (hi - lo);
  return rgbHex(key === 'trim' ? div(f) : seq(f));
}

export default function CellView({ t, cells, title }: { t: Theme; cells: CellMap; title: string }) {
  const [key, setKey] = useState<CellKey>('hits');
  const [hover, setHover] = useState<[number, number] | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 400 });
  const maxHits = useMemo(() => Math.max(1, ...cells.hits.flat()), [cells]);
  const cov = useMemo(() => coverage(cells), [cells]);
  const ny = cells.mapAxis.length, nx = cells.rpmAxis.length;
  const pad = { l: 56, r: 12, t: 10, b: 40 };

  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth || 800, h: el.clientHeight || 400 }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const c = canvas.current;
    const ctx = c?.getContext?.('2d');
    if (!c || !ctx) return;
    const dpr = typeof devicePixelRatio === 'number' ? devicePixelRatio : 1;
    c.width = size.w * dpr; c.height = size.h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.w, size.h);
    const cw = (size.w - pad.l - pad.r) / nx, ch = (size.h - pad.t - pad.b) / ny;
    for (let j = 0; j < ny; j++) {
      for (let i = 0; i < nx; i++) {
        const x = pad.l + i * cw, y = pad.t + (ny - 1 - j) * ch;
        ctx.fillStyle = cellColour(cells, key, j, i, maxHits) ?? t.sunk;
        ctx.fillRect(x + 1, y + 1, cw - 2, ch - 2);
        if (hover && hover[0] === j && hover[1] === i) {
          ctx.strokeStyle = t.livePoint; ctx.lineWidth = 3;
          ctx.strokeRect(x + 1.5, y + 1.5, cw - 3, ch - 3);
        }
      }
    }
    ctx.fillStyle = t.fg;
    ctx.font = `500 11px ${FONTS.mono}`;
    ctx.textAlign = 'center';
    cells.rpmAxis.forEach((r, i) => { if (i % 2 === 0) ctx.fillText(r >= 1000 ? `${(r / 1000).toFixed(1)}k` : String(r), pad.l + (i + 0.5) * cw, size.h - pad.b + 16); });
    ctx.textAlign = 'right';
    cells.mapAxis.forEach((m, j) => ctx.fillText(String(m), pad.l - 8, pad.t + (ny - 1 - j + 0.5) * ch + 4));
    ctx.font = `700 12px ${FONTS.sans}`;
    ctx.textAlign = 'center';
    ctx.fillText('engine rpm', pad.l + (size.w - pad.l - pad.r) / 2, size.h - 6);
    ctx.save();
    ctx.translate(14, pad.t + (size.h - pad.t - pad.b) / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText('MAP kPa', 0, 0);
    ctx.restore();
  }, [cells, key, hover, size, t, maxHits, nx, ny, pad.b, pad.l, pad.r, pad.t]);

  const onMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const cw = (size.w - pad.l - pad.r) / nx, ch = (size.h - pad.t - pad.b) / ny;
    const i = Math.floor((e.clientX - r.left - pad.l) / cw);
    const j = ny - 1 - Math.floor((e.clientY - r.top - pad.t) / ch);
    setHover(i >= 0 && i < nx && j >= 0 && j < ny ? [j, i] : null);
  };

  const spec = CELL_KEYS.find((k) => k.key === key)!;
  const ramp = key === 'trim' ? DIV_RAMP : SEQ_RAMP;
  const legend = key === 'hits' ? ['1', String(Math.round(Math.sqrt(maxHits))), String(maxHits)]
    : key === 'trim' ? ['−12 % rich', '0', '+12 % lean'] : [String(spec.range![0]), String((spec.range![0] + spec.range![1]) / 2), String(spec.range![1])];
  const hv = hover ? cellValue(cells, key, hover[0], hover[1]) : NaN;
  const hits = hover ? cells.hits[hover[0]]![hover[1]]! : 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, height: '100%', minHeight: 0 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <Tabs t={t} label="Colour cells by" items={CELL_KEYS.map((k) => [k.key, k.label] as [CellKey, string])} value={key} onChange={setKey} />
        <div style={{ flexGrow: 1 }} />
        <span style={{ fontSize: 12.5, fontWeight: 600 }} data-testid="cell-coverage">Cells with data <b style={{ fontFamily: FONTS.mono }}>{cov.seen} / {cov.total}</b></span>
      </div>
      <figure ref={box} style={{ margin: 0, flexGrow: 1, minHeight: 0, border: `1px solid ${t.rule}`, position: 'relative' }}>
        <canvas ref={canvas} role="img" aria-label={`${title}: ${spec.label} per RPM × MAP cell`} onMouseMove={onMove} onMouseLeave={() => setHover(null)}
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />
      </figure>
      <div style={{ display: 'flex', gap: 16, alignItems: 'center', fontSize: 12.5, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, width: 260 }}>
          <div style={{ height: 14, background: `linear-gradient(90deg, ${ramp.join(', ')})`, border: `1px solid ${t.rule}` }} />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: FONTS.mono, fontSize: 11.5 }}>{legend.map((l, k) => <span key={k}>{l}</span>)}</div>
        </div>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span style={{ width: 14, height: 14, background: t.sunk, border: `1px solid ${t.rule}` }} />grey = no samples logged in that cell yet</span>
        <div style={{ flexGrow: 1 }} />
        <span style={{ fontFamily: FONTS.mono, fontWeight: 700 }} aria-live="polite">
          {hover ? `${cells.rpmAxis[hover[1]]} rpm · ${cells.mapAxis[hover[0]]} kPa · ${hits} samples${key !== 'hits' ? ` · ${Number.isFinite(hv) ? hv.toFixed(spec.decimals) : '---'} ${spec.units}` : ''}` : 'point at a cell'}
        </span>
      </div>
    </div>
  );
}
