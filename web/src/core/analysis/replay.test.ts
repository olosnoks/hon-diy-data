/** Ports of tests/test_analysis.py (replay session) and tests/test_ui_replay.py (the model, no window). */
import { describe, expect, it } from 'vitest';
import { valueAt } from './cursors';
import { arange, ch, makeDrive } from './fixtures';
import { MIN_WINDOW_S, ReplayModel, ReplaySession } from './replay';

function synth() {
  const fast = arange(0, 600, 0.025);
  const slow = arange(0.37, 600, 1.0);
  return makeDrive({
    rpm: ch(fast, (t) => 800 + 400 * Math.sin(t / 7) + (t % 1)),
    ect: ch(slow, (t) => 60 + t / 6),
    vbatt_mean: ch(slow, () => 14.1),
  }, [{ t: 25, kind: 'marker', text: 'MARK 1' }, { t: 75, kind: 'marker', text: 'MARK 2' }]);
}

function twoRuns() {
  const t = arange(0, 60, 0.1);
  const a = makeDrive({ rpm: ch(t, (x) => 800 + 50 * x) }, [{ t: 20, kind: 'marker', text: 'pull start' }, { t: 40, kind: 'trigger', text: 'STALL' }]);
  const tb = arange(0, 50, 0.1);
  const b = makeDrive({ rpm: ch(tb, (x) => 800 + 50 * (x + 7)) }, [{ t: 13, kind: 'marker', text: 'pull start' }, { t: 33, kind: 'trigger', text: 'STALL' }]);
  return [a, b] as const;
}

describe('ReplaySession', () => {
  it('view, cursors, readouts, steps, events and bookmarks', () => {
    const d = synth();
    const s = new ReplaySession(d);
    expect(s.view).toEqual([s.t0, s.t1]);
    const env = s.trace('rpm', 1366);
    expect(env.reduced).toBe(true);
    expect(env.t.length).toBeLessThanOrEqual(1366);
    s.zoom(0.01, 300);
    expect(s.view[1] - s.view[0]).toBeCloseTo((s.t1 - s.t0) * 0.01);
    expect(s.trace('rpm').reduced).toBe(false);
    const lo = s.view[0];
    s.pan(0.5);
    expect(s.view[0]).toBeGreaterThan(lo);
    s.setView(5, 1);
    s.showAll();
    expect(s.view).toEqual([s.t0, s.t1]);

    s.setCursor(100);
    s.setCursor(160, 'b');
    expect(s.cursorDelta).toBe(60);
    const rows = Object.fromEntries(s.readouts(['rpm', 'ect', 'vbatt_mean']).map((r) => [r.name, r]));
    const rpm = d.series.rpm!;
    expect(rows.rpm!.value).toBe(valueAt(rpm, 100));
    expect(rows.rpm!.stats!.n).toBe([...rpm.t].filter((t) => t >= 100 && t <= 160).length);
    expect(rows.ect!.stats!.delta).toBeCloseTo(10, 0);
    const quick = s.readouts(['ect'], false)[0]!.stats!;
    expect([quick.a, quick.b, quick.delta, quick.rate]).toEqual([rows.ect!.stats!.a, rows.ect!.stats!.b, rows.ect!.stats!.delta, rows.ect!.stats!.rate]);
    expect(quick.min).toBeNaN();
    expect(quick.n).toBe(0);
    s.clearCursorB();
    expect(s.readouts(['rpm'])[0]!.stats).toBeNull();
    expect(s.cursorDelta).toBeNull();
    expect(s.readouts().length).toBe(3);
    s.setCursor(1e9);
    expect(s.cursorA).toBe(s.t1);

    s.setCursor(100);
    const one = s.step(+1, 'ect');
    expect(one - 100).toBeGreaterThan(0);
    expect(one - 100).toBeLessThan(1);
    expect([...d.series.ect!.t]).toContain(one);
    expect(s.step(-1, 'ect')).toBeLessThan(one);
    s.setCursor(100);
    const any = s.step(+1);
    expect(any).toBeGreaterThan(100);
    expect(any).toBeLessThan(100.03);
    expect(s.step(-1)).toBeLessThanOrEqual(100);

    expect(s.eventList().map((e) => e.text).slice(0, 2)).toEqual(['MARK 1', 'MARK 2']);
    s.setCursor(0);
    s.setView(0, 10);
    expect(s.jumpEvent(+1)).toBeCloseTo(25);
    expect(s.view[0]).toBeLessThanOrEqual(25);
    expect(s.view[1]).toBeGreaterThanOrEqual(25);
    expect(s.jumpEvent(-1)).toBeNull();
    expect(s.jumpEvent(+1)).toBeCloseTo(75);

    s.addEvent(50, 'loss', 'FP FEED');
    s.addEvent(60, 'note', 'look here');
    expect(s.eventList().map((e) => e.text).slice(0, 4)).toEqual(['MARK 1', 'FP FEED', 'look here', 'MARK 2']);
    expect(s.eventList('loss').map((e) => e.text)).toEqual(['FP FEED']);
    expect(s.eventList('marker').map((e) => e.text).slice(0, 2)).toEqual(['MARK 1', 'MARK 2']);
    s.setCursor(25);
    expect(s.jumpEvent(+1)).toBeCloseTo(50);
    expect(s.jumpEvent(+1, 'marker')).toBeCloseTo(75);
    s.clearEvents('note');
    expect(s.bookmarks.map((e) => e.kind)).toEqual(['loss']);
    s.clearEvents();
    expect(s.bookmarks).toEqual([]);
    expect(s.eventList()[1]!.text).toBe('MARK 2');
  });

  it('overlay and pyramid', () => {
    const [a, b] = twoRuns();
    const s = new ReplaySession(a);
    expect(s.overlayTrace('rpm')).toBeNull();
    expect(s.setOverlay(b, 'marker')).toBeCloseTo(7);
    expect(s.overlayTrace('rpm')!.t[0]!).toBeGreaterThanOrEqual(7 - 0.2);
    expect(s.overlayTrace('nope')).toBeNull();
    expect(s.setOverlay(b, 'crossing', { channel: 'rpm', level: 1e9 })).toBe(0);
    expect(s.alignOverlay('marker')).toBeCloseTo(7);
    expect(() => s.alignOverlay('crossing', { channel: 'rpm', level: 1e9 })).toThrow();
    expect(s.overlay!.offset).toBeCloseTo(7);
    expect(s.shiftOverlay(-0.5)).toBeCloseTo(6.5);
    expect(s.overlayTrace('rpm')!.t[0]!).toBeGreaterThanOrEqual(6.5 - 0.2);
    expect(s.setOverlay(null)).toBe(0);
    expect(s.overlay).toBeNull();
    expect(s.alignOverlay('marker')).toBe(0);
    expect(s.shiftOverlay(1)).toBe(0);
    const t = arange(0, 300, 0.001);
    const long = new ReplaySession(makeDrive({ rail: ch(t, Math.sin) }));
    const env = long.trace('rail', 1000);
    expect(env.reduced).toBe(true);
    expect(env.t.length).toBeLessThanOrEqual(1000);
  });
});

/** A stall capture: 12 Hz ECU rpm, a 1 kHz black box stream; pump dies at `pumpDies`. */
function capture(pumpDies: number, name: string) {
  const ecu = arange(0, 50, 1 / 12);
  const bb = arange(0, 50, 0.001);
  const pump = bb.map((t) => (t < pumpDies ? 13.6 : 0.2));
  pump[20_000] = 3.0;
  return makeDrive({
    rpm: ch(ecu, (t) => (t < 40 ? 800 + 10 * Math.sin(t) : 0)),
    bb_pump: { t: bb, v: pump },
    bb_rpm_tach: ch(bb, (t) => (t < 40 ? 810 : 0)),
  }, [{ t: 12, kind: 'marker', text: 'MARK 1' }, { t: 40, kind: 'trigger', text: 'STALL' }], { name, kind: 'stall' });
}

const NAMES: Record<string, [string, number]> = { tach: ['bb_rpm_tach', 1 / 30], fpv: ['bb_pump', 1] };

describe('ReplayModel', () => {
  it('speaks view time and UI ids', () => {
    const m = new ReplayModel(capture(38, 'stall_a'), { names: NAMES, span: [-30, 10] });
    expect(m.origin).toBe(40);
    expect(m.span[0]).toBeCloseTo(-30);
    expect(m.span[1]).toBeCloseTo(10, 1);
    expect(m.window[0]).toBeCloseTo(m.span[0]);

    let tr = m.trace('fpv', 800);
    expect(tr.reduced).toBe(true);
    expect(tr.x.length).toBeLessThanOrEqual(1600);
    expect(tr.x[0]!).toBeGreaterThanOrEqual(-30);
    expect(tr.x[tr.x.length - 1]!).toBeLessThanOrEqual(10);
    expect(Math.min(...tr.y)).toBeCloseTo(0.2);
    expect([...tr.y].map((y) => Math.round(y * 1000) / 1000)).toContain(3);
    expect(Math.max(...m.trace('tach', 800).y)).toBeCloseTo(27);
    tr = m.trace('rpm', 800);
    expect(tr.reduced).toBe(false);
    expect(Math.abs(tr.x.length - 480)).toBeLessThanOrEqual(3);

    const near = (a: [number, number], b: [number, number], tol = 0.06) => {
      expect(Math.abs(a[0] - b[0])).toBeLessThanOrEqual(tol);
      expect(Math.abs(a[1] - b[1])).toBeLessThanOrEqual(tol);
    };
    near(m.setWindow(-100, 500), m.span, 1e-9);
    near(m.zoom(0.5, 0), [-15, 5]);
    near(m.pan(0.25), [-10, 10]);
    near(m.pan(1), [-10, 10]);
    const [t0, t1] = m.zoom(1e-6, -2);
    expect(t1 - t0).toBeCloseTo(MIN_WINDOW_S);
    expect(t0).toBeLessThanOrEqual(-2);
    expect(t1).toBeGreaterThanOrEqual(-2);
    tr = m.trace('fpv', 800);
    expect(tr.reduced).toBe(false);
    expect(tr.x.length).toBeLessThan(300);
    near(m.showAll(), m.span, 1e-9);
    expect(m.reveal(-2)).toBe(false);
    m.zoom(0.1, -20);
    expect(m.reveal(5)).toBe(true);
    expect(m.window[0]).toBeLessThanOrEqual(5);
    expect(m.window[1]).toBeGreaterThanOrEqual(5);

    expect(m.setCursor(-5)).toBeCloseTo(-5);
    expect(m.delta).toBeNull();
    expect(Object.keys(m.readouts(['fpv', 'tach', 'nope'])).sort()).toEqual(['fpv', 'tach']);
    expect(m.readouts(['fpv']).fpv!.value).toBeCloseTo(13.6);
    expect(m.readouts(['fpv']).fpv!.stats).toBeNull();
    m.setCursor(0, 'b');
    const row = m.readouts(['fpv', 'tach']);
    expect(m.delta).toBeCloseTo(5);
    expect(row.fpv!.stats!.delta).toBeCloseTo(0.2 - 13.6);
    expect(row.tach!.stats!.a).toBeCloseTo(27);
    expect(row.tach!.stats!.delta).toBeCloseTo(-27);
    expect(row.fpv!.stats!.min).toBeCloseTo(0.2);
    expect(row.fpv!.stats!.n).toBe(5001);
    m.clearCursorB();
    expect(m.cursorB).toBeNull();
    expect(m.setCursor(99)).toBeCloseTo(m.span[1]);
    const back = m.step(-1, 'rpm');
    expect(back).toBeGreaterThan(-6);
    expect(back).toBeLessThan(m.span[1]);

    expect(m.events().map(([t, k]) => [Math.round(t * 10) / 10, k])).toEqual([[-28, 'marker'], [0, 'trigger']]);
    m.addEvent(-2, 'loss', 'FP FEED');
    m.setCursor(-30);
    const jumps = [m.jump(+1), m.jump(+1), m.jump(+1), m.jump(+1)];
    expect(jumps[0]).toBeCloseTo(-28);
    expect(jumps[1]).toBeCloseTo(-2);
    expect(jumps[2]).toBeCloseTo(0);
    expect(jumps[3]).toBeNull();
    expect(m.jump(-1)).toBeCloseTo(-2);
    expect(m.window[0]).toBeLessThanOrEqual(-2);
    expect(m.window[1]).toBeGreaterThanOrEqual(-2);
  });

  it('lays a second drive over the first', () => {
    const m = new ReplayModel(capture(38, 'stall_a'), { names: NAMES, span: [-30, 10] });
    expect(m.overlayTrace('fpv', 800)).toBeNull();
    expect(m.nudgeOverlay(1)).toBe(0);
    const other = new ReplayModel(capture(39.5, 'stall_b'), { names: NAMES });
    expect(m.setOverlay(other, 'trigger')).toBe('trigger');
    expect(m.overlayName).toBe('stall_b');
    expect(m.overlayOffset).toBeCloseTo(0);
    let o = m.overlayTrace('fpv', 800)!;
    const died = (tr: { x: Float64Array; y: Float64Array }) => tr.x[tr.y.findIndex((y) => y < 1)]!;
    expect(died(o)).toBeCloseTo(-0.5, 1);
    expect(died(m.trace('fpv', 800))).toBeCloseTo(-2, 1);
    expect(Math.max(...m.overlayTrace('tach', 800)!.y)).toBeCloseTo(27);
    expect(m.nudgeOverlay(-1.5)).toBeCloseTo(-1.5);
    o = m.overlayTrace('fpv', 800)!;
    expect(died(o)).toBeCloseTo(-2, 1);
    expect(m.alignOverlay('marker')).toBe('marker');
    expect(m.overlayOffset).toBeCloseTo(0);
    expect(m.alignOverlay('crossing')).toBe('start');     // starts above 400 rpm: no crossing
    expect(m.setOverlay(null)).toBe('');
    expect(m.overlayTrace('fpv', 800)).toBeNull();
    expect(m.overlayName).toBe('');
  });

  it('a whole drive gets origin 0', () => {
    const m = new ReplayModel(makeDrive({ rpm: { t: [0, 1, 2], v: [1, 2, 3] } }));
    expect(m.origin).toBe(0);
    expect(m.span).toEqual([0, 2]);
  });
});
