/** Gear, acceleration, lateral g, g-g, power from a pull, fuel, airflow, grade. Synthetic data only. */
import { describe, expect, it } from 'vitest';
import { ggTrail, headingDiff, lateralAccel, longAccelFromArrays, longitudinalAccel } from './dynamics';
import { arange, ch, makeDrive } from './fixtures';
import { estimateGear, gearFromArrays, GearState, gearOfRatio, gearShare, GEAR_RPM_PER_KMH } from './gear';
import { estimateGrade } from './grade';
import {
  airflowGs, airflowHp, DEFAULT_VEHICLE, drivePulls, effectiveMass, fuelFlowGs, fuelUsed, gsToLph, overallRatio,
  REFERENCE_D16Y8, referenceHp, referenceTorque, roadLoad, torqueFromAccel, torqueModel, torqueToHp, torqueWithBand,
} from './power';

describe('gear', () => {
  it('clusters and coupling', () => {
    expect(gearOfRatio(76.8 * 1.03)).toBe(2);
    expect(gearOfRatio(60)).toBe(0);
    expect(gearOfRatio(NaN)).toBe(0);
    const t = arange(0, 14, 0.1);
    // 0-5 s 2nd at 40 km/h; 5-7 s clutch in (rpm 900); 7-12 s 3rd at 45 km/h; 12-13 s crawl; 13+ stopped
    const vss = t.map((x) => (x < 7 ? 40 : x < 12 ? 45 : x < 13 ? 4 : 0));
    const rpm = t.map((x, i) => (x < 5 ? 40 * 76.8 : x < 7 ? 900 : x < 12 ? 45 * 49.7 : 800) + (i % 3));
    rpm[20] = NaN;                                   // a missing read does not break coupling
    const g = gearFromArrays(t, rpm, vss);
    const at = (x: number) => Math.round(x * 10);
    expect(g.gear[at(2.5)]).toBe(2);
    expect(g.state[at(2.5)]).toBe(GearState.inGear);
    expect(g.gear[at(2.2)]).toBe(2);              // the missing read at 2.0 s is inside its window
    expect(g.state[20]).toBe(GearState.unknown);
    expect(g.gear[at(4.7)]).toBe(0);                 // within 0.5 s of the clutch: not coupled
    expect(g.state[at(4.7)]).toBe(GearState.decoupled);
    expect(g.state[at(6)]).toBe(GearState.decoupled);
    expect(g.gear[at(9)]).toBe(3);
    expect(g.state[at(12.5)]).toBe(GearState.crawl);
    expect(g.state[at(13.5)]).toBe(GearState.stopped);
    const share = gearShare(g);
    expect(share.gears[1]).toBeCloseTo(4.4, 1);
    expect(share.stopped).toBeCloseTo(1, 1);
    const d = makeDrive({ rpm: { t, v: rpm }, vss: { t, v: vss } });
    expect(estimateGear(d).gear[at(9)]).toBe(3);
  });
});

describe('longitudinal acceleration', () => {
  it('constant acceleration through 1 km/h steps, gaps bridged or kept', () => {
    const t = arange(0, 20, 1 / 11.3);
    const vss = t.map((x) => Math.floor((10 + 2 * x * 3.6) / 1.011));   // 2 m/s² true, quantised
    const d = makeDrive({ vss: { t, v: vss } });
    const la = longitudinalAccel(d);
    const mid = la.a.slice(30, 170);
    const mean = mid.reduce((a, b) => a + b, 0) / mid.length;
    expect(mean).toBeCloseTo(2, 1);
    expect(la.v[100]!).toBeCloseTo((10 / 3.6) + 2 * la.t[100]!, 0);
    expect(la.s[la.s.length - 1]!).toBeGreaterThan(400);
    // a 2 s hole is bridged, a 10 s hole is not
    const g = arange(0, 30, 0.1);
    const v = g.map((x) => ((x > 5 && x < 7) || (x > 15 && x < 25) ? NaN : 50));
    const r = longAccelFromArrays(g, v);
    expect(r.a[60]!).toBeCloseTo(0, 6);
    expect(r.a[200]).toBeNaN();
  });
});

describe('lateral g from GPS', () => {
  const R = 100; const V = 20;                     // m, m/s: 4 m/s², 0.408 g
  const LAT0 = 45; const M_PER_DEG = 111_195;
  function circle(withHeading: boolean, gapFrom = -1, gapTo = -1) {
    const tg: number[] = []; const lat: number[] = []; const lon: number[] = []; const hd: number[] = []; const sp: number[] = [];
    for (let k = 0; k < 60; k++) {
      if (k >= gapFrom && k < gapTo) continue;
      const ang = (V / R) * k;                     // clockwise from north, viewed from above
      const x = R * Math.sin(ang); const y = R * Math.cos(ang);
      // position on a circle centred at the origin, moving clockwise: heading = ang + 90°
      tg.push(k + 1.7);                            // fixes arrive 1.7 s late
      lat.push(LAT0 + y / M_PER_DEG);
      lon.push(x / (M_PER_DEG * Math.cos((LAT0 * Math.PI) / 180)));
      hd.push(withHeading ? (((ang * 180) / Math.PI + 90) % 360) : NaN);
      sp.push(V * 3.6);
    }
    const te = arange(0, 62, 0.1);
    return makeDrive({ vss: ch(te, () => (V * 3.6) / 1.011) }, [], {
      gps: { t: Float64Array.from(tg), lat: Float64Array.from(lat), lon: Float64Array.from(lon), heading: Float64Array.from(hd), speedKmh: Float64Array.from(sp) },
    });
  }

  it('turn rate × speed, lag removed', () => {
    const lat = lateralAccel(circle(true));
    expect(lat.t[0]).toBeCloseTo(0);
    expect(lat.yawDegS[10]!).toBeCloseTo((V / R) * 180 / Math.PI, 6);
    expect(lat.g[10]!).toBeCloseTo(4 / 9.81, 6);
    expect(headingDiff(350, 10)).toBe(20);
    expect(headingDiff(10, 350)).toBe(-20);
  });

  it('bearing from positions when no heading, gaps and corrupt fixes dropped', () => {
    const d = circle(false, 30, 40);
    d.gps.lat[5] = 10;                              // thousands of km off
    const lat = lateralAccel(d);
    expect(lat.g[15]!).toBeCloseTo(4 / 9.81, 2);
    const i29 = [...d.gps.t].indexOf(29 + 1.7);
    expect(lat.g[i29]).toBeNaN();                   // next fix is 11 s away: no rate across the gap
    expect(lat.g[5]).toBeNaN();
    const pts = ggTrail(d, 10, 20);
    expect(pts.length).toBeGreaterThan(50);
    for (const p of pts) { expect(p.lat).toBeCloseTo(0.408, 1); expect(Math.abs(p.lon)).toBeLessThan(0.05); }
    expect(ggTrail(d, 31, 38).length).toBe(0);       // nothing inside the gap
  });

  it('slow speed gives no lateral', () => {
    const d = circle(true);
    d.series.vss!.v.fill(3);
    expect([...lateralAccel(d).g].every(Number.isNaN)).toBe(true);
  });
});

describe('power from a pull', () => {
  it('golden numbers from vehicle-dynamics.md §1.3', () => {
    expect(effectiveMass(2) - effectiveMass(0)).toBeCloseTo(110, -1);
    expect(effectiveMass(0) - 1195).toBeCloseTo(31.6, 1);
    expect(overallRatio(2)).toBeCloseTo(8.40, 2);
    const v = (3125 / 76.8) * 1.011 / 3.6;
    const tq = torqueFromAccel(2.25, v, 2);
    expect(tq).toBeCloseTo(123, 0);                  // table: 123 N·m at 3,000-3,250 rpm
    expect(torqueToHp(tq, 3125)).toBeCloseTo(54, 0); // and 54 hp
    expect(roadLoad(100 / 3.6)).toBeCloseTo(475, -1);
    const band = torqueWithBand(2.25, v, 2);
    expect(band.lo).toBeLessThan(tq);
    expect(band.hi).toBeGreaterThan(tq);
    expect(band.parts.grade!).toBeCloseTo(8.9, 0);   // ±2 % grade ≈ ±7 %
    expect((band.hi - band.lo) / 2 / tq).toBeGreaterThan(0.06);
    expect((band.hi - band.lo) / 2 / tq).toBeLessThan(0.14);
  });

  it('reference D16Y8 curve is marked approximate and hits the rated points', () => {
    expect(REFERENCE_D16Y8.approximate).toBe(true);
    expect(referenceTorque(5500)).toBe(145);
    expect(referenceHp(6600)).toBeCloseTo(127, 0);
    expect(Math.max(...REFERENCE_D16Y8.nm)).toBe(145);
  });

  /** A drive with one full-throttle pull in 2nd at a constant 120 N·m. */
  function pullDrive(torque = 120, tpsPull = 90) {
    const hz = 11.3; const dt = 1 / hz;
    const p = DEFAULT_VEHICLE;
    const t: number[] = []; const vss: number[] = []; const rpm: number[] = []; const tps: number[] = []; const map: number[] = [];
    let v = (2770 / 76.8) * 1.011 / 3.6;
    for (let k = 0; k < 30 * hz; k++) {
      const tt = k * dt;
      const pulling = tt >= 10 && tt < 15;
      t.push(tt);
      const vEcu = (v * 3.6) / 1.011;
      vss.push(vEcu); rpm.push(vEcu * 76.8); tps.push(pulling ? tpsPull : 15); map.push(pulling ? 98 : 40);
      if (pulling) {
        const f = (torque * overallRatio(2, p) * p.eta) / p.tyreRadiusM;
        v += ((f - roadLoad(v, p)) / effectiveMass(2, p)) * dt;
      }
    }
    return makeDrive({ t: { t, v: t }, vss: { t, v: vss }, rpm: { t, v: rpm }, tps: { t, v: tps }, map: { t, v: map } });
  }

  it('detects the pull and recovers the torque', () => {
    const curves = drivePulls(pullDrive());
    expect(curves.length).toBe(1);
    const c = curves[0]!;
    expect(c.pull.gear).toBe(2);
    expect(c.pull.fullThrottle).toBe(true);
    expect(c.pull.t0).toBeGreaterThanOrEqual(9.9);
    expect(c.pull.t1).toBeLessThan(15);
    const mid = c.points.filter((q) => q.t > 11 && q.t < 13.5);
    for (const q of mid) expect(q.torque).toBeCloseTo(120, -1);
    const meanMid = mid.reduce((a, q) => a + q.torque, 0) / mid.length;
    expect(Math.abs(meanMid - 120)).toBeLessThan(3);
    expect(c.bins.length).toBeGreaterThan(2);
    expect(c.peakHp.hp).toBeCloseTo(torqueToHp(120, c.peakHp.rpm), -1);
    expect(c.ofReference).toBeGreaterThan(0.8);
    expect(c.ofReference).toBeLessThan(1.05);
    expect(c.uncertainty.relative).toBeGreaterThan(0.05);
    expect(c.points[5]!.airGs).toBeGreaterThan(20);
    expect(c.label).toBe('measured + modelled');
    // editable inputs: more mass -> more torque for the same acceleration
    const heavy = drivePulls(pullDrive(), { ...DEFAULT_VEHICLE, massKg: 1400 })[0]!;
    expect(heavy.points[10]!.torque).toBeGreaterThan(c.points[10]!.torque);
  });

  it('part throttle is not a full-throttle pull unless asked', () => {
    expect(drivePulls(pullDrive(100, 60)).length).toBe(0);
    const part = drivePulls(pullDrive(100, 60), DEFAULT_VEHICLE, { minTps: 50 });
    expect(part.length).toBe(1);
    expect(part[0]!.pull.fullThrottle).toBe(false);
  });

  it('airflow, torque model and fuel flow', () => {
    const air = airflowGs(5370, 98, 20);
    expect(air).toBeGreaterThan(65);
    expect(air).toBeLessThan(80);
    const hp = airflowHp(air);
    expect(hp.lo).toBeCloseTo(air * 1.25);
    expect(hp.hi).toBeCloseTo(air * 1.35);
    expect(torqueModel(4600, 98, 20)).toBeGreaterThan(120);
    expect(torqueModel(4600, 98, 20)).toBeLessThan(160);
    expect(fuelFlowGs(3.0, 800)).toBeCloseTo(4 * (240 / 60000) * 2.0 * (800 / 120) * 0.745, 9);
    expect(fuelFlowGs(0, 3000)).toBe(0);              // fuel cut
    expect(fuelFlowGs(0.9, 800)).toBe(0);             // below the dead time
    expect(gsToLph(0.113)).toBeCloseTo(0.546, 2);      // idle: 0.113 g/s = 0.55 L/h
    const t = arange(0, 3600, 1);
    const d = makeDrive({ inj: ch(t, () => 3), rpm: ch(t, () => 800), vss: ch(t, () => 0) });
    const f = fuelUsed(d);
    expect(f.litres).toBeCloseTo(gsToLph(fuelFlowGs(3, 800)), 2);
    expect(f.idleLitres).toBeCloseTo(f.litres, 6);
  });
});

describe('grade estimate', () => {
  it('recovers a known climb (self-consistent, modelled)', () => {
    const p = DEFAULT_VEHICLE;
    const hz = 10; const dt = 1 / hz;
    const t: number[] = []; const vss: number[] = []; const rpm: number[] = []; const map: number[] = []; const brake: number[] = []; const inj: number[] = [];
    let v = 60 / 3.6;
    for (let k = 0; k < 120 * hz; k++) {
      const tt = k * dt;
      const grade = tt < 60 ? 0 : 0.03;
      const vEcu = (v * 3.6) / 1.011;
      const r = vEcu * GEAR_RPM_PER_KMH[3]!;
      t.push(tt); vss.push(vEcu); rpm.push(r); map.push(55); brake.push(0); inj.push(3);
      const fEng = (0.91 * torqueModel(r, 55, 25) * overallRatio(4, p) * p.eta) / p.tyreRadiusM;
      const sin = Math.sin(Math.atan(grade));
      v += ((fEng - roadLoad(v, p) - p.massKg * 9.81 * sin) / effectiveMass(4, p)) * dt;
    }
    const d = makeDrive({ vss: { t, v: vss }, rpm: { t, v: rpm }, map: { t, v: map }, brake: { t, v: brake }, inj: { t, v: inj } });
    const g = estimateGrade(d);
    expect(g.modelled).toBe(true);
    expect(g.brakeKnown).toBe(true);
    const at = (x: number) => g.raw[Math.round(x * hz)]!;
    expect(at(30)).toBeCloseTo(0, 2);
    expect(at(90)).toBeCloseTo(0.03, 2);
    expect(g.elevation[g.elevation.length - 1]!).toBeGreaterThan(10);
    // braking blanks the estimate
    d.series.brake!.v.fill(1);
    expect([...estimateGrade(d).raw].every(Number.isNaN)).toBe(true);
  });
});
