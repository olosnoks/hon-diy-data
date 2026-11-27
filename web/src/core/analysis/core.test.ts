/** Ports of tests/test_analysis.py: LOD, cursors, histogram, scatter, filters, compare. */
import { describe, expect, it } from 'vitest';
import { compareDrives, Comparison, eventTime, firstCrossing, overlayDiff, overlaySummary } from './compare';
import { spanStats, stepSample, valueAt } from './cursors';
import { Filter, FilterSet, PRESET_FILTERS, type CompiledExpr, type ExprCompiler } from './filters';
import { arange, makeDrive, normal, rng } from './fixtures';
import { Bins, holdWeights, MAP_KPA_10, PRESETS, RPM_500, TableHistogram } from './histogram';
import { decimatePoints, envelope, LodPyramid, polyline } from './lod';
import { ScatterModel } from './scatter';
import { align, savgol, type Series, type Table } from './series';

const S = (t: number[], v: number[], name = 'x', units = ''): Series => ({ name, t: Float64Array.from(t), v: Float64Array.from(v), units });

// ---------------------------------------------------------------- level of detail

describe('envelope', () => {
  it('keeps spikes', () => {
    const t = arange(0, 1000, 0.01);
    const v = t.map(Math.sin);
    v[43_210] = 50;
    for (let i = 60_000; i < 60_050; i++) v[i] = NaN;
    const env = envelope(t, v, undefined, undefined, 800);
    expect(env.reduced).toBe(true);
    expect(env.t.length).toBeLessThanOrEqual(800);
    expect(Math.max(...env.hi)).toBe(50);
    expect(Math.min(...env.lo)).toBeGreaterThanOrEqual(-1);
    for (let i = 0; i < env.t.length; i++) expect(env.lo[i]!).toBeLessThanOrEqual(env.hi[i]!);
    for (let i = 1; i < env.t.length; i++) expect(env.t[i]!).toBeGreaterThan(env.t[i - 1]!);
    const [x, y] = polyline(env);
    expect(x.length).toBe(2 * env.t.length);
    expect(Math.max(...y)).toBe(50);
    const zoom = envelope(t, v, 432.0, 432.2, 800);
    expect(zoom.reduced).toBe(false);
    expect(Math.max(...zoom.hi)).toBe(50);
    expect(zoom.t.length).toBeLessThanOrEqual(23);
    expect(polyline(zoom)[0]).toBe(zoom.t);
    expect(envelope(new Float64Array(0), new Float64Array(0)).t.length).toBe(0);
    const hole = envelope(t, v.map((x, i) => (t[i]! > 300 && t[i]! < 500 ? NaN : x)), undefined, undefined, 100);
    expect(hole.lo.some(Number.isNaN)).toBe(true);
  });

  it('skips empty columns', () => {
    const t = Float64Array.from([...arange(0, 10, 0.001), ...arange(90, 100, 0.001)]);
    const env = envelope(t, Float64Array.from(t), undefined, undefined, 100);
    expect(env.reduced).toBe(true);
    expect(env.t.length).toBe(20);
    expect(env.lo[10]!).toBeGreaterThanOrEqual(90);
    expect(env.hi[9]!).toBeLessThan(10);
  });

  it('pyramid matches the direct envelope', () => {
    const r = rng(5);
    const n = 1_000_000;
    const t = Float64Array.from({ length: n }, (_, i) => i / 1000);
    const v = Float64Array.from({ length: n }, () => normal(r));
    v[777_777] = 99; v[123_456] = -99;
    const p = new LodPyramid(t, v);
    expect(p.levels.length).toBeGreaterThanOrEqual(2);
    const full = p.envelope(undefined, undefined, 1000);
    expect(full.reduced).toBe(true);
    expect(Math.max(...full.hi)).toBe(99);
    expect(Math.min(...full.lo)).toBe(-99);
    expect(full.t.length).toBeLessThanOrEqual(1000);
    const direct = envelope(t, v, undefined, undefined, 1000);
    expect(full.t.length).toBe(direct.t.length);
    for (let i = 0; i < full.t.length; i++) {
      const prev = i ? i - 1 : full.t.length - 1;
      expect(Math.max(full.hi[i]!, full.hi[prev]!)).toBeGreaterThanOrEqual(direct.hi[i]!);
      expect(Math.min(full.lo[i]!, full.lo[prev]!)).toBeLessThanOrEqual(direct.lo[i]!);
    }
    const part = p.envelope(700, 800, 500);
    expect(Math.max(...part.hi)).toBe(99);
    expect(part.t[0]!).toBeGreaterThanOrEqual(700);
    expect(part.t[part.t.length - 1]!).toBeLessThanOrEqual(800);
    const close = p.envelope(777.7, 777.9, 500);
    expect(close.reduced).toBe(false);
    expect(Math.max(...close.hi)).toBe(99);
    // fast because it is bounded: a window of the big series comes back at about the
    // asked width, whatever its length (no wall-clock budget: that flakes under load)
    for (let i = 0; i < 20; i++) expect(p.envelope(i * 10, i * 10 + 600, 1366).t.length).toBeLessThanOrEqual(2 * 1366 + 2);
    expect(new LodPyramid(new Float64Array(0), new Float64Array(0)).envelope().t.length).toBe(0);
  });

  it('decimates points evenly', () => {
    expect([...decimatePoints(5, 10)]).toEqual([0, 1, 2, 3, 4]);
    const d = decimatePoints(1000, 10);
    expect(d.length).toBe(10);
    expect(d[0]).toBe(0);
    expect(d[9]).toBe(999);
  });
});

// ---------------------------------------------------------------- cursors

describe('cursors', () => {
  it('values and span stats', () => {
    const s = S([0, 1, 2, 4], [100, 200, 400, 0], 'rpm', 'rpm');
    expect(valueAt(s, 1.5)).toBe(200);
    expect(valueAt(s, 1.5, 'linear')).toBe(300);
    expect(valueAt(s, 1.6, 'nearest')).toBe(400);
    expect(valueAt(s, -1)).toBeNaN();
    expect(valueAt(s, -1, 'nearest')).toBe(100);
    expect(valueAt(s, 99)).toBe(0);
    const st = spanStats(s, 0.5, 3.0);
    expect([st.a, st.b, st.delta, st.n]).toEqual([100, 400, 300, 2]);
    expect([st.min, st.max]).toEqual([200, 400]);
    expect(st.rate).toBeCloseTo(120);
    expect(st.mean).toBeCloseTo((200 * 1 + 400 * 1) / 2);
    const back = spanStats(s, 3.0, 0.5);
    expect(back.delta).toBe(-300);
    expect(back.min).toBe(200);
    const gap = spanStats(S([0, 1, 2, 4], [NaN, 1, NaN, 3]), 0, 4);
    expect([gap.n, gap.min, gap.max]).toEqual([2, 1, 3]);
    const none = spanStats(s, 2.2, 2.4);
    expect(none.n).toBe(0);
    expect(none.mean).toBeNaN();
    expect(none.a).toBe(400);
    expect(stepSample(s, 1.0, +1)).toBe(2);
    expect(stepSample(s, 1.0, -1)).toBe(0);
    expect(stepSample(s, 1.5, -1)).toBe(1);
    expect(stepSample(s, 4.0, +1)).toBe(4);
    expect(stepSample(S([], []), 3, 1)).toBe(3);
    expect(valueAt(S([], []), 1)).toBeNaN();
    const quick = spanStats(s, 0.5, 3.0, false);
    expect(quick.min).toBeNaN();
    expect(quick.n).toBe(0);
    expect(quick.delta).toBe(300);
  });
});

// ---------------------------------------------------------------- histogram

describe('histogram', () => {
  it('bins', () => {
    const b = Bins.fromBreakpoints([1000, 2000, 3000]);
    expect(b.length).toBe(3);
    expect([...b.centres]).toEqual([1000, 2000, 3000]);
    expect([...b.index([0, 1499, 1500, 2600, 9000, NaN])]).toEqual([0, 0, 1, 2, 2, -1]);
    const open = Bins.fromBreakpoints([1000, 2000, 3000], false);
    expect([...open.edges]).toEqual([500, 1500, 2500, 3500]);
    expect([...open.index([499, 500, 3500, 3501])]).toEqual([-1, 0, 2, -1]);
    const lin = Bins.linear(0, 100, 4);
    expect([...lin.centres]).toEqual([12.5, 37.5, 62.5, 87.5]);
    expect(lin.index([100])[0]).toBe(3);
    for (const bad of [[1], [1, 1], [2, 1]]) {
      expect(() => Bins.fromEdges(bad)).toThrow();
      expect(() => Bins.fromBreakpoints(bad)).toThrow();
    }
    expect(RPM_500.length).toBe(15);
    expect(Object.keys(PRESETS).length).toBe(5);
    // ROM axes
    expect([...Bins.fromAxis({ values: [500, 1000, 2000] }).centres]).toEqual([500, 1000, 2000]);
    expect(() => Bins.fromAxis({ values: [0, 1], numeric: false })).toThrow(/numeric/);
  });

  it('statistics match a slow loop', () => {
    const r = rng(3);
    const n = 5000;
    const x = Float64Array.from({ length: n }, () => 400 + r() * 6600);
    const y = Float64Array.from({ length: n }, () => 15 + r() * 90);
    const z = Float64Array.from({ length: n }, (_, i) => (i % 97 === 0 ? NaN : normal(r) * 5));
    const xb = Bins.fromBreakpoints([1000, 2000, 3000, 4000, 5000, 6000]);
    const yb = Bins.linear(20, 100, 4);
    const mask = Uint8Array.from({ length: n }, () => (r() > 0.2 ? 1 : 0));
    const t = Float64Array.from({ length: n }, (_, i) => i * 0.1);
    const h = new TableHistogram(x, y, z, xb, yb, { mask, t });
    expect(h.mean.length).toBe(24);
    const ix = xb.index(x); const iy = yb.index(y);
    let total = 0;
    for (let cx = 0; cx < 6; cx++) for (let cy = 0; cy < 4; cy++) {
      const sel: number[] = [];
      for (let i = 0; i < n; i++) if (ix[i] === cx && iy[i] === cy && mask[i] && !Number.isNaN(z[i]!)) sel.push(i);
      const c = h.cell(cx, cy);
      expect(h.count[c]).toBe(sel.length);
      expect(h.rows(cx, cy)).toEqual(sel);
      total += sel.length;
      if (sel.length) {
        const zs = sel.map((i) => z[i]!);
        const m = zs.reduce((a, b) => a + b, 0) / zs.length;
        expect(h.mean[c]!).toBeCloseTo(m, 9);
        expect(h.min[c]).toBe(Math.min(...zs));
        expect(h.max[c]).toBe(Math.max(...zs));
        expect(h.std[c]!).toBeCloseTo(Math.sqrt(zs.reduce((a, b) => a + (b - m) ** 2, 0) / zs.length), 9);
        expect(h.time[c]!).toBeCloseTo(sel.length * 0.1, 9);      // time-in-cell
      }
    }
    expect(h.samples).toBe(total);
    const times = h.times(2, 1);
    h.rows(2, 1).forEach((row, k) => expect(times[k]!).toBeCloseTo(row * 0.1));
    const counts = [...h.count].sort((a, b) => a - b);
    const hits = counts[counts.length >> 1]!;
    const gated = h.stat('mean', hits + 1);
    expect(gated.filter(Number.isNaN).length).toBe([...h.count].filter((c) => c <= hits).length);
    expect(Math.min(...h.stat('count', hits + 1))).toBe(0);
    const [lo, hi] = h.colorRange('mean');
    expect(lo).toBe(Math.min(...[...h.mean].filter((v) => !Number.isNaN(v))));
    expect(hi).toBe(Math.max(...[...h.mean].filter((v) => !Number.isNaN(v))));
    expect(h.colorRange('count')[0]).toBe(0);
    expect(() => h.stat('median' as never)).toThrow();
    expect(() => new TableHistogram(x, y, z, xb, yb).times(0, 0)).toThrow();
  });

  it('empty and delta', () => {
    const b = Bins.linear(0, 10, 2);
    const e = new Float64Array(0);
    const empty = new TableHistogram(e, e, e, b, b);
    expect(empty.samples).toBe(0);
    expect([...empty.mean].every(Number.isNaN)).toBe(true);
    expect(empty.colorRange()).toEqual([0, 0]);
    const a = new TableHistogram([1, 1, 6], [1, 1, 6], [10, 20, 5], b, b);
    const c = new TableHistogram([1, 6], [1, 1], [12, 1], b, b);
    const d = a.delta(c);
    expect(d[a.cell(0, 0)]).toBe(3);
    expect(d[a.cell(1, 0)]).toBeNaN();
    expect(d[a.cell(1, 1)]).toBeNaN();
    expect([...a.delta(c, 'mean', 2)].every(Number.isNaN)).toBe(true);
    expect(() => a.delta(new TableHistogram(e, e, e, b, Bins.linear(0, 10, 3)))).toThrow();
  });

  it('time in cell skips gaps', () => {
    const t = Float64Array.from([0, 0.1, 0.2, 0.3, 10.3, 10.4]);
    const w = holdWeights(t);
    expect(w[3]!).toBeCloseTo(0.3);            // capped at 3 median periods, not 10 s
    const h = new TableHistogram([1000, 1000, 1000, 1000, 1000, 1000], [30, 30, 30, 30, 30, 30], [1, 1, 1, 1, 1, 1],
      RPM_500, MAP_KPA_10, { t });
    expect(h.stat('time').reduce((a, b) => a + b, 0)).toBeCloseTo(0.1 * 3 + 0.3 + 0.1 + 0.1);
  });
});

// ---------------------------------------------------------------- scatter

describe('scatter', () => {
  it('fit and brush', () => {
    const t = arange(0, 20, 0.1);
    const x = t.map((_, i) => (10 * i) / 199);
    const table: Table = { t, columns: { x, y: x.map((v) => 3 * v + 2), c: Float64Array.from(t), q: x.map((v) => 1 + 2 * v + 0.5 * v * v) } };
    table.columns.y![5] = NaN;
    const s = new ScatterModel(table, 'x', 'y', 'c', x.map((v) => (v < 9 ? 1 : 0)));
    expect(s.length).toBe(x.filter((v) => v < 9).length - 1);
    expect([...s.rows]).not.toContain(5);
    const fit = s.fit()!;
    expect(fit.coeffs[0]!).toBeCloseTo(2, 9);
    expect(fit.coeffs[1]!).toBeCloseTo(3, 9);
    expect(fit.r2).toBeCloseTo(1, 9);
    expect(fit.describe()).toContain('y = 2 + 3 x');
    expect(fit.at(1)).toBeCloseTo(5);
    const quad = new ScatterModel(table, 'x', 'q').fit(2)!;
    expect(quad.coeffs.map((c) => Number(c.toFixed(6)))).toEqual([1, 2, 0.5]);
    const picked = s.brush(2, 1, 100, 0);
    expect(picked.length).toBeGreaterThan(0);
    expect(Math.min(...[...picked].map((i) => s.x[i]!))).toBeGreaterThanOrEqual(1);
    expect(Math.max(...[...picked].map((i) => s.x[i]!))).toBeLessThanOrEqual(2);
    expect([...s.brushTimes(1, 2, 0, 100)]).toEqual([...picked].map((i) => s.t[i]!));
    const pts = s.points(50);
    expect(pts.x.length).toBeLessThanOrEqual(50);
    expect(pts.color!.length).toBe(pts.x.length);
    expect(pts.pick[0]).toBe(0);
    expect(pts.pick[pts.pick.length - 1]).toBe(s.length - 1);
    expect(new ScatterModel(table, 'x', 'y').points().color).toBeNull();
    expect(new ScatterModel({ t, columns: { x: t.map(() => 1), y: x } }, 'x', 'y').fit()).toBeNull();
    const spans = new ScatterModel(table, 'x', 'q').brushSpans(0, 1, 0, 1000, 0.5);
    expect(spans.length).toBe(1);
    expect(spans[0]![0]).toBe(0);
    expect(spans[0]![1]).toBeCloseTo(1.9);
    const two = new ScatterModel({ t, columns: { x: t.map(Math.sin), y: Float64Array.from(t) } }, 'x', 'y').brushSpans(0.9, 1.1, 0, 100);
    expect(two.length).toBeGreaterThanOrEqual(2);
    for (const [a, b] of two) expect(a).toBeLessThanOrEqual(b);
  });
});

// ---------------------------------------------------------------- filters

/** A tiny stand-in for the expression engine: `a > n`, `a < n`, bare `a`, joined by `and`. */
const fake: ExprCompiler = (text: string): CompiledExpr => {
  const parts = text.split(/\s+and\s+/).map((p) => p.trim());
  const tests = parts.map((p) => {
    const m = /^(\w+)\s*([<>])\s*(-?[\d.]+)$/.exec(p);
    if (m) { const [, c, op, n] = m; return { c: c!, f: (v: number) => (op === '>' ? v > Number(n) : v < Number(n)) }; }
    if (/^\w+$/.test(p)) return { c: p, f: (v: number) => v > 0 };
    throw new Error(`bad expression: ${text}`);
  });
  return {
    deps: tests.map((x) => x.c),
    mask: (env, t) => Uint8Array.from({ length: t.length }, (_, i) => (tests.every((x) => {
      const v = env[x.c]?.[i] ?? NaN;
      return !Number.isNaN(v) && x.f(v);
    }) ? 1 : 0)),
  };
};

describe('filters', () => {
  it('combine and cache', () => {
    const t = arange(0, 100, 0.5);
    const table: Table = { t, columns: { ect: t.map((x) => 60 + x / 2), closed_loop: t.map((x) => (x > 30 ? 1 : 0)), tps: t.map((x) => x % 7) } };
    const fs = new FilterSet([new Filter('warm', 'ect > 80', true, fake), new Filter('closed loop', 'closed_loop', true, fake)], fake);
    fs.add('steady', 'tps < 5', false);
    expect(fs.channels()).toEqual(new Set(['ect', 'closed_loop']));
    const both = fs.mask(table);
    const sum = (m: Uint8Array) => m.reduce((a, b) => a + b, 0);
    expect(sum(both)).toBeGreaterThan(0);
    expect(sum(both)).toBeLessThan(t.length);
    both.forEach((m, i) => expect(m).toBe(table.columns.ect![i]! > 80 && table.columns.closed_loop![i]! > 0 ? 1 : 0));
    fs.enable('steady');
    expect(sum(fs.mask(table))).toBeLessThan(sum(both));
    expect(fs.channels()).toEqual(new Set(['ect', 'closed_loop', 'tps']));
    fs.enable('warm', false);
    fs.remove('closed loop');
    expect(fs.active.map((f) => f.name)).toEqual(['steady']);
    expect(fs.get('warm')!.enabled).toBe(false);
    const again = FilterSet.fromJSON(fs.toJSON(), fake);
    expect([...again.mask(table)]).toEqual([...fs.mask(table)]);
    expect(new FilterSet([], fake).mask(table).every((m) => m === 1)).toBe(true);
    expect(() => new Filter('bad', 'ect >', true, fake)).toThrow();
    expect(() => new Filter('x', 'ect > 1')).toThrow(/setExprCompiler/);
    expect(PRESET_FILTERS.length).toBe(5);
  });
});

// ---------------------------------------------------------------- compare

function twoRuns() {
  const t = arange(0, 60, 0.1);
  const a = makeDrive({ rpm: { t, v: t.map((x) => 800 + 50 * x) }, stft: { t, v: t.map(() => 2) }, map: { t, v: t.map((x) => 30 + x) } },
    [{ t: 20, kind: 'marker', text: 'pull start' }, { t: 40, kind: 'trigger', text: 'STALL (rpm 0)' }]);
  const tb = arange(0, 50, 0.1);
  const b = makeDrive({ rpm: { t: tb, v: tb.map((x) => 800 + 50 * (x + 7)) }, stft: { t: tb, v: tb.map(() => -1) }, map: { t: tb, v: tb.map((x) => 30 + x + 7) } },
    [{ t: 13, kind: 'marker', text: 'pull start' }, { t: 33, kind: 'trigger', text: 'STALL (link lost)' }]);
  return [a, b] as const;
}

describe('compare', () => {
  it('alignment and difference', () => {
    const [a, b] = twoRuns();
    const cmp = new Comparison(a, b);
    expect(cmp.commonChannels()).toEqual(['rpm', 'stft', 'map']);
    expect(Math.abs(overlaySummary(cmp.overlay('rpm')).mean)).toBeCloseTo(350, -1);
    expect(cmp.align('marker')).toBeCloseTo(7);
    const over = cmp.overlay('rpm');
    expect(overlaySummary(over).maxAbs).toBeLessThan(1e-6);
    expect(overlayDiff(over).slice(0, 60).every(Number.isNaN)).toBe(true);
    expect(cmp.shifted('rpm').t[0]).toBeCloseTo(7);
    expect(cmp.align('trigger')).toBeCloseTo(7);
    expect(cmp.align('crossing', { channel: 'rpm', level: 2000 })).toBeCloseTo(7, 6);
    expect(cmp.align('start')).toBe(0);
    expect(overlaySummary(cmp.overlay('stft')).mean).toBeCloseTo(3);
    expect(() => cmp.align('marker', { text: 'no such marker' })).toThrow(/neither log/);
    expect(() => cmp.align('crossing', { channel: 'rpm', level: 3700 })).toThrow(/second log/);
    expect(() => eventTime(a, 'sideways' as never)).toThrow();
    expect(eventTime(a, 'marker', { text: 'PULL' })).toBe(20);
    const empty = overlaySummary(new Comparison(a, makeDrive({ rpm: { t: [], v: [] } })).overlay('rpm'));
    expect(empty.n).toBe(0);
    expect(empty.rms).toBeNaN();
  });

  it('first crossing', () => {
    const s = S([0, 1, 2, 3, 4], [0, 2, 4, 2, 0]);
    expect(firstCrossing(s, 3)).toBeCloseTo(1.5);
    expect(firstCrossing(s, 3, false)).toBeCloseTo(2.5);
    expect(firstCrossing(s, 1, true, 2)).toBeNull();
    expect(firstCrossing(s, 9)).toBeNull();
    expect(firstCrossing(S([0], [0]), 0.5)).toBeNull();
  });

  it('histogram delta between drives', () => {
    const [a, b] = twoRuns();
    const xb = Bins.linear(800, 3800, 3); const yb = Bins.linear(30, 90, 2);
    const { delta, a: ha, b: hb } = new Comparison(a, b).histogramDelta('rpm', 'map', 'stft', xb, yb);
    expect(delta.length).toBe(6);
    for (const d of delta) if (!Number.isNaN(d)) expect(Math.abs(d - 3)).toBeLessThan(1e-9);
    expect(delta.filter(Number.isNaN).length).toBe(ha.mean.filter((m, i) => Number.isNaN(m + hb.mean[i]!)).length);
    const fs = new FilterSet([new Filter('low', 'rpm < 1800', true, fake)], fake);
    const f = new Comparison(a, b).histogramDelta('rpm', 'map', 'stft', xb, yb, 'mean', 1, fs);
    expect(f.a.samples).toBeLessThan(ha.samples);
    for (let iy = 0; iy < 2; iy++) for (let ix = 1; ix < 3; ix++) expect(f.delta[iy * 3 + ix]).toBeNaN();
  });

  it('compareDrives with fallback and channel sets', () => {
    const [a, b] = twoRuns();
    const r = compareDrives(a, b, undefined, 'marker', { text: 'pull' });
    expect(r.offset).toBeCloseTo(7);
    expect(r.alignedOn).toBe('marker');
    const rows = Object.fromEntries(r.rows.map((x) => [x.name, x]));
    expect(Object.keys(rows)).toEqual(['rpm', 'stft', 'map']);
    expect(rows.rpm!.n).toBe(500);
    expect(rows.stft!.mean).toBeCloseTo(3);
    expect(rows.stft!.meanA).toBe(2);
    expect(rows.stft!.meanB).toBe(-1);
    const fb = compareDrives(a, b, ['stft', 'nope'], 'crossing', { channel: 'rpm', level: 1e9 });
    expect(fb.alignedOn).toBe('start');
    expect(fb.offset).toBe(0);
    expect(fb.rows.map((x) => x.name)).toEqual(['stft']);
    const extra = makeDrive({ rpm: { t: [0, 1], v: [800, 900] }, knock: { t: [0, 1], v: [0, 1] } });
    const e = compareDrives(a, extra);
    expect(e.rows.map((x) => x.name)).toEqual(['rpm']);
    expect(e.onlyA).toEqual(['stft', 'map']);
    expect(e.onlyB).toEqual(['knock']);
  });
});

// ---------------------------------------------------------------- series helpers

describe('series helpers', () => {
  it('align holds, interpolates and gaps a dropout', () => {
    const s = S([0, 1, 2, 10, 11], [0, 10, 20, 100, 110]);
    const g = Float64Array.from([0.5, 1.5, 5.5, 10.5, 12]);
    const hold = align(s, g);
    expect([hold[0], hold[1], hold[3]]).toEqual([0, 10, 100]);
    expect(hold[2]).toBeNaN();            // older than 3 periods (3 s): a gap
    const lin = align(s, g, 'linear');
    expect(lin[1]).toBeCloseTo(15);
    expect(lin[4]).toBeNaN();             // never past the last read
  });

  it('savgol matches the exact slope of a quadratic, edges included', () => {
    const t = arange(0, 5, 0.1);
    const v = t.map((x) => 3 + 2 * x + 0.5 * x * x);
    const d = savgol(v, 15, 2, 1, 0.1);
    t.forEach((x, i) => expect(d[i]!).toBeCloseTo(2 + x, 9));
    const sm = savgol(v, 15, 2, 0, 0.1);
    v.forEach((x, i) => expect(sm[i]!).toBeCloseTo(x, 9));
  });
});
