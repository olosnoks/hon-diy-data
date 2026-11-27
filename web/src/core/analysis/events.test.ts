/** Events and their verdicts, on synthetic drives shaped like the 2026-11-25/26 cases. */
import { describe, expect, it } from 'vitest';
import { findEvents, findHarsh, findLinkEvents, findMarks, findNearStalls, findVtec, nearStallVerdict } from './events';
import { arange, makeDrive } from './fixtures';

const HZ = 12;

/** Idle at a standstill with one dip at t=20 s; the knobs shape it. */
function idleDip(o: { min?: number; tpsFall?: number; tpsMin?: number; mapRise?: number; eldStep?: number; peak?: number; vss?: number } = {}) {
  const t = arange(0, 40, 1 / HZ);
  const min = o.min ?? 520; const peak = o.peak ?? 830;
  const rpm = t.map((x) => {
    if (x < 19) return 820;
    if (x < 19.5) return 820 + ((peak - 820) * (x - 19)) / 0.5;         // rise to the peak
    if (x < 20) return peak - ((peak - min) * (x - 19.5)) / 0.5;         // the fall
    if (x < 21) return min + ((820 - min) * (x - 20)) / 1;               // recovery
    return 820;
  });
  const tps = t.map((x) => (x >= 19.4 && x < 19.6 ? (o.tpsFall ?? 0) : x >= 19.6 && x <= 20.2 ? (o.tpsMin ?? 0) : 0));
  const map = t.map((x) => (x >= 19.6 && x <= 20.5 ? 25 + (o.mapRise ?? 13) : 25));
  const slow = arange(0.3, 40, 1.04);
  const eld = slow.map((x) => (x >= 17 && x <= 22 ? 15 + (o.eldStep ?? 0) : 15));
  return makeDrive({ rpm: { t, v: rpm }, vss: { t, v: t.map(() => o.vss ?? 0) }, tps: { t, v: tps }, map: { t, v: map }, eld: { t: slow, v: eld } });
}

describe('near stalls', () => {
  it('throttle closed at clutch bite', () => {
    const ev = findNearStalls(idleDip());
    expect(ev.length).toBe(1);
    expect(ev[0]!.kind).toBe('near_stall');
    expect(ev[0]!.verdict).toBe('throttle closed at clutch bite');
    expect(ev[0]!.data.minRpm).toBeCloseTo(520, -1);
    expect(ev[0]!.data.fallRpmS).toBeLessThan(-400);
    expect(ev[0]!.severity).toBe('warn');
    expect(findNearStalls(idleDip({ min: 460 }))[0]!.severity).toBe('alarm');
  });

  it('open throttle, blip, load step, overshoot', () => {
    expect(findNearStalls(idleDip({ tpsFall: 15, tpsMin: 12 }))[0]!.verdict).toBe('clutch bite with the throttle open');
    expect(findNearStalls(idleDip({ tpsFall: 13, tpsMin: 0 }))[0]!.verdict).toBe('throttle blip released as the clutch bit');
    expect(findNearStalls(idleDip({ mapRise: 2, eldStep: 19 }))[0]!.verdict).toBe('electrical load step (+19 A)');
    expect(findNearStalls(idleDip({ peak: 1210 }))[0]!.verdict).toBe('throttle closed at clutch bite, rpm already falling from above target');
    expect(nearStallVerdict({ tpsAtFall: 0, tpsMinFall: 0, mapRise: 0, overshoot: 0, eldStep: 0 })).toBe('rpm sag at standstill');
  });

  it('not at a standstill, or not low enough', () => {
    expect(findNearStalls(idleDip({ vss: 20 })).length).toBe(0);
    expect(findNearStalls(idleDip({ min: 650 })).length).toBe(0);
  });
});

function rpmWithGap(before: number, after: number, gapS: number, opts: { vssBefore?: number; vssAfter?: number; end?: number } = {}) {
  const a = arange(0, 30, 0.1);
  const b = arange(30 + gapS, (opts.end ?? 60) + gapS, 0.1);
  const t = [...a, ...b];
  const rpm = [...a.map(() => before), ...b.map(() => after)];
  const vss = [...a.map(() => opts.vssBefore ?? 0), ...b.map(() => opts.vssAfter ?? 0)];
  return makeDrive({ rpm: { t, v: rpm }, vss: { t, v: vss } });
}

describe('link events', () => {
  it('short dropout with the same idle on both sides (11-26 21:10:54)', () => {
    const ev = findLinkEvents(rpmWithGap(726, 729, 1.5)).filter((e) => e.kind !== 'keyoff' || e.label !== 'LOG ENDS (engine running)');
    expect(ev.length).toBe(1);
    expect(ev[0]!.kind).toBe('dropout');
    expect(ev[0]!.verdict).toContain('engine running');
    expect(ev[0]!.data.silenceS).toBeCloseTo(1.6, 1);
  });

  it('a silence while driving is a dropout even if the revs changed (11-26 23:16)', () => {
    const ev = findLinkEvents(rpmWithGap(2066, 2887, 8, { vssBefore: 41, vssAfter: 59 }));
    expect(ev[0]!.kind).toBe('dropout');
    expect(ev[0]!.verdict).toContain('while driving');
  });

  it('back at rpm 0 or in an after-start flare is an engine-off', () => {
    const off = findLinkEvents(rpmWithGap(820, 202, 180));
    expect(off[0]!.kind).toBe('keyoff');
    expect(off[0]!.verdict).toContain('key-off or ECU power loss');
    expect(off[0]!.verdict).toContain('key-on / cranking');
    const flare = findLinkEvents(rpmWithGap(670, 1446, 24));
    expect(flare[0]!.kind).toBe('keyoff');
    expect(flare[0]!.verdict).toContain('after-start flare');
  });

  it('IG1 from the CYD tells key-off from power loss', () => {
    const d = rpmWithGap(820, 0, 180);
    const tig = arange(0, 240, 0.5);
    d.series.bb_ig1 = { t: tig, v: tig.map((x) => (x > 30 && x < 210 ? 0.2 : 13.9)) };
    expect(findLinkEvents(d)[0]!.verdict).toContain('key-off (IG1 dropped)');
    d.series.bb_ig1 = { t: tig, v: tig.map(() => 13.9) };
    expect(findLinkEvents(d)[0]!.verdict).toContain('ECU power loss with IG1 on');
  });

  it('a stall with the ECU answering, and the log ending with the engine running', () => {
    const t = arange(0, 20, 0.1);
    const d = makeDrive({ rpm: { t, v: t.map((x) => (x < 10 ? 780 : x < 11 ? 780 - 780 * (x - 10) : 0)) } });
    const ev = findLinkEvents(d);
    expect(ev.map((e) => e.kind)).toEqual(['stall']);
    expect(ev[0]!.verdict).toContain('ECU answering');
    const end = findLinkEvents(makeDrive({ rpm: { t, v: t.map(() => 800) } }));
    expect(end.length).toBe(1);
    expect(end[0]!.label).toBe('LOG ENDS (engine running)');
    expect(end[0]!.severity).toBe('info');
  });

  it('not armed below 400 rpm', () => {
    expect(findLinkEvents(rpmWithGap(300, 0, 10)).length).toBe(0);
  });
});

describe('vtec, harsh driving, marks', () => {
  it('finds each with a verdict', () => {
    const t = arange(0, 30, 0.1);
    const vss = t.map((x) => (x < 10 ? 60 : x < 13 ? 60 - ((x - 10) * 4 * 3.6) / 1.011 : 60 - (12 * 3.6) / 1.011));
    const d = makeDrive({
      rpm: { t, v: vss.map((v) => v * 36) },
      vss: { t, v: vss },
      vtec: { t, v: t.map((x) => (x >= 20 && x < 21 ? 1 : 0)) },
    }, [{ t: 5, kind: 'marker', text: 'MARK 1' }]);
    const v = findVtec(d);
    expect(v.length).toBe(1);
    expect(v[0]!.verdict).toMatch(/^VTEC on at [\d,]+ rpm for 1\.0 s$/);
    const h = findHarsh(d);
    expect(h.map((e) => e.kind)).toEqual(['harsh_brake']);
    expect(h[0]!.verdict).toMatch(/^braking 0\.4\d g from 6\d km\/h$/);
    const m = findMarks(d);
    expect(m[0]!.verdict).toBe('mark at 60 km/h, 2,160 rpm');
    const all = findEvents(d);
    for (let i = 1; i < all.length; i++) expect(all[i]!.t).toBeGreaterThanOrEqual(all[i - 1]!.t);
    expect(all.map((e) => e.kind)).toContain('mark');
  });
});
