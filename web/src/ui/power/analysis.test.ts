import { beforeEach, describe, expect, it } from 'vitest';
import { synthDrive, SYNTH_TORQUE_K } from '../gps/fixtures';
import {
  analyseDrive, computeResult, curveCsv, DEFAULT_INPUTS, loadBaseline, pullDetail, referenceHp, referenceTorque, REFERENCE_D16Y8,
  saveBaseline, tyreRadius, validInputs, vehicleOf,
} from './analysis';
import { buildCells, cellValue, coverage, MAP_AXIS, RPM_AXIS } from './cells';

describe('reference curve (core/analysis)', () => {
  it('goes through the two rated points', () => {
    expect(referenceTorque(REFERENCE_D16Y8.ratedNmRpm)).toBeCloseTo(145, 5);
    expect(referenceHp(REFERENCE_D16Y8.ratedHpRpm)).toBeCloseTo(127, 0);
    expect(REFERENCE_D16Y8.approximate).toBe(true);
  });
});

describe('inputs', () => {
  it('works out the rolling radius of the stock tyre', () => {
    expect(tyreRadius('185/65R14')).toBeCloseTo(0.287, 3);
    expect(tyreRadius('195/55 R15')).toBeGreaterThan(0.28);
    expect(tyreRadius('nonsense')).toBeNull();
  });
  it('maps the inputs onto the vehicle model', () => {
    const p = vehicleOf({ ...DEFAULT_INPUTS, drivetrainLossPct: 15, gradePct: 2 });
    expect(p.eta).toBeCloseTo(0.85);
    expect(p.grade).toBeCloseTo(0.02);
    expect(p.tyreRadiusM).toBeCloseTo(0.287, 3);
    expect(validInputs(DEFAULT_INPUTS)).toBeNull();
    expect(validInputs({ ...DEFAULT_INPUTS, massKg: 50 })).toMatch(/mass/);
    expect(validInputs({ ...DEFAULT_INPUTS, tyre: 'x' })).toMatch(/tyre/);
  });
});

describe('pulls and the curve', () => {
  const drive = synthDrive();
  const a = analyseDrive(drive)!;

  it('finds the WOT pull in 2nd first, then the part pull in 3rd', () => {
    expect(a).not.toBeNull();
    const [full, part] = a.pulls;
    expect(full!.quality).toBe('full');
    expect(full!.pull.gear).toBe(2);
    expect(Math.abs(full!.pull.t0 - drive.marks.wotT0)).toBeLessThan(0.5);
    expect(full!.tpsMean).toBeCloseTo(84, 0);
    expect(pullDetail(full!)).toMatch(/^\d\.\d s · TPS 84 % · MAP 98 kPa$/);
    expect(part!.quality).toBe('part');
    expect(part!.pull.gear).toBe(3);
    for (const p of a.pulls.slice(2)) expect(p.quality).toBe('short');
    expect(a.rpmMax).toBeGreaterThan(5400);
  });

  it('measures the engine torque the drive was made with (0.91 × reference)', () => {
    const res = computeResult(drive, a, a.pulls[0]!, DEFAULT_INPUTS)!;
    const mid = res.points.filter((p) => p.rpm > 3200 && p.rpm < 4500);
    expect(mid.length).toBeGreaterThan(3);
    for (const p of mid) expect(Math.abs(p.nm / p.refNm - SYNTH_TORQUE_K)).toBeLessThan(0.04);
    expect(res.vsRefPct).toBeGreaterThan(86);
    expect(res.vsRefPct).toBeLessThan(95);
    expect(res.uncertaintyPct).toBeGreaterThan(5);
    expect(res.uncertaintyPct).toBeLessThan(15);
    for (const p of res.points) {
      expect(p.nmLo).toBeLessThan(p.nm);
      expect(p.nmHi).toBeGreaterThan(p.nm);
    }
    expect(res.peakHpAtEnd).toBe(true);
    expect(res.iat).toBeCloseTo(22);
    expect(res.baro).toBeCloseTo(100.3);
  });

  it('follows the inputs', () => {
    const base = computeResult(drive, a, a.pulls[0]!, DEFAULT_INPUTS)!;
    const lossy = computeResult(drive, a, a.pulls[0]!, { ...DEFAULT_INPUTS, drivetrainLossPct: 15 })!;
    expect(lossy.peakNm).toBeGreaterThan(base.peakNm);
    const known = computeResult(drive, a, a.pulls[0]!, { ...DEFAULT_INPUTS, gradePct: 0 })!;
    expect(known.uncertaintyPct).toBeLessThan(base.uncertaintyPct);
    const heavy = computeResult(drive, a, a.pulls[0]!, { ...DEFAULT_INPUTS, massKg: 1400 })!;
    expect(heavy.peakNm).toBeGreaterThan(base.peakNm);
  });

  it('writes a CSV with the inputs and one row per bin', () => {
    const res = computeResult(drive, a, a.pulls[0]!, DEFAULT_INPUTS)!;
    const csv = curveCsv(res, DEFAULT_INPUTS, 'drive');
    const rows = csv.trim().split('\n').filter((l) => !l.startsWith('#'));
    expect(rows[0]).toBe('rpm,torque_nm,torque_lo_nm,torque_hi_nm,power_hp,power_lo_hp,power_hi_hp,ref_torque_nm,ref_power_hp,samples');
    expect(rows.length).toBe(res.points.length + 1);
    expect(csv).toContain('# mass 1195 kg');
    expect(csv).toContain('approximate');
  });

  it('has nothing to say without rpm, speed and throttle', () => {
    expect(analyseDrive(null)).toBeNull();
    expect(analyseDrive({ ...drive, series: { rpm: drive.series.rpm! } })).toBeNull();
  });
});

describe('baseline', () => {
  beforeEach(() => localStorage.clear());
  it('is kept on the device', () => {
    const drive = synthDrive();
    const a = analyseDrive(drive)!;
    const res = computeResult(drive, a, a.pulls[0]!, DEFAULT_INPUTS)!;
    expect(loadBaseline()).toBeNull();
    saveBaseline(res, 'stock');
    const b = loadBaseline()!;
    expect(b.label).toBe('stock');
    expect(b.points.length).toBe(res.points.length);
  });
});

describe('time in cell', () => {
  it('bins rpm × MAP on the default 20 × 10 grid', () => {
    expect(RPM_AXIS.length).toBe(20);
    expect(RPM_AXIS[0]).toBe(500);
    expect(RPM_AXIS[19]).toBe(7500);
    expect(MAP_AXIS).toEqual([17, 26, 36, 45, 54, 64, 73, 82, 92, 101]);
    const cells = buildCells(synthDrive());
    let best = [0, 0, 0];
    cells.hits.forEach((row, j) => row.forEach((h, i) => { if (h > best[0]!) best = [h, j, i]; }));
    expect(RPM_AXIS[best[2]!]).toBeLessThan(1300); // the engine lives at idle
    expect(MAP_AXIS[best[1]!]).toBeLessThan(40);
    const cov = coverage(cells);
    expect(cov.total).toBe(200);
    expect(cov.seen).toBeGreaterThan(5);
    expect(Number.isNaN(cellValue(cells, 'inj', 0, 19))).toBe(true); // no samples there
    expect(cellValue(cells, 'trim', best[1]!, best[2]!)).toBeGreaterThan(-12);
    const pull = buildCells(synthDrive(), 38, 44);
    expect(pull.total).toBeLessThan(cells.total);
  });
});
