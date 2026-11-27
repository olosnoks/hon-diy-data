// Port of the pure parts of tests/test_mapping_finder.py (correlation finder,
// typed targets, what-changed) and the map status overview.
import { describe, expect, it } from 'vitest';
import { Channel, loadDefault, loads, MapError } from '../index';
import { groupOf } from '../groups';
import {
  bitChangeText, bitFormula, bitHistory, candidateFields, candidateLabel, changedBits, changedBytes,
  flagChannelTable, findValue, fromRows, matchFormula, matchText, rank, rankAgainstChannel, rankShape,
  rankSteps, SweepRecording, valueMatchText, type Mark,
} from './find';
import { coverage, scriptsFor, statusLines, verifiedCount } from './status';

const AMAP = loadDefault();

// ---- tests/mapping_helpers.py ----------------------------------------------------

const ECT_POLY = [155.04149, -3.0414878, 0.03952185, -0.00029383913, 0.0000010792568, -0.0000000015618437];
const TEMP_TABLE = Array.from({ length: 256 }, (_, x) => ECT_POLY.reduce((s, c, i) => s + c * x ** i, 0));
const pyRound = (v: number) => {
  const f = Math.floor(v);
  const d = v - f;
  return d > 0.5 ? f + 1 : d < 0.5 ? f : f % 2 === 0 ? f : f + 1;
};
function tempRaw(deg: number): number {
  const target = pyRound(deg);
  let best = 0;
  for (let x = 1; x < 256; x++) if (Math.abs(TEMP_TABLE[x]! - target) < Math.abs(TEMP_TABLE[best]! - target)) best = x;
  return best;
}

interface Ram {
  rpm?: number; tps?: number; map_kpa?: number; baro?: number; ect?: number; iat?: number; batt?: number;
  o2?: number; inj?: number; ign?: number; vss?: number; stft?: number; ltft?: number; iacv?: number;
  mil?: number; closed_loop?: number; ac?: number; noise?: number; brake?: number; starter?: number;
  ac_clutch?: boolean; fuel_pump?: boolean;
}

function ram(o: Ram = {}): Int16Array {
  const { rpm = 0, tps = 0, map_kpa = 101, baro = 101, ect = 20, iat = 20, batt = 12.4, o2 = 0.05, inj = 0,
    ign = 0, vss = 0, stft = 0, ltft = 0, iacv = 0, mil = 0, closed_loop = 0, ac = 0, noise = 0, brake = 0,
    starter = 0 } = o;
  const r = new Int16Array(256).fill(0x55);
  const u8 = (v: number) => Math.max(0, Math.min(255, Math.trunc(pyRound(v))));
  const x = Math.max(0, Math.min(0xffff, Math.trunc(pyRound(rpm * 4))));
  r[0x00] = x >> 8;
  r[0x01] = x & 0xff;
  r[0x02] = u8(vss);
  const running = rpm > 100;
  const acClutch = o.ac_clutch ?? (Boolean(ac) && running);
  const fuelPump = o.fuel_pump ?? running;
  r[0x08] = 0x84 | (ac ? 0x02 : 0) | (brake ? 0x08 : 0) | (starter ? 0x01 : 0);
  r[0x0a] = 0x10 | (running ? 0x80 : 0);
  r[0x0b] = (running ? 0x40 : 0) | (mil ? 0x20 : 0) | (acClutch ? 0x02 : 0) | (fuelPump ? 0x01 : 0);
  r[0x0f] = closed_loop ? 0x01 : 0x00;
  r[0x10] = tempRaw(ect);
  r[0x11] = tempRaw(iat);
  r[0x12] = u8((map_kpa + 5) / 0.716);
  r[0x13] = u8((baro + 5) / 0.716);
  r[0x14] = u8(tps * 2.55);
  r[0x15] = u8(o2 * 51.3);
  r[0x17] = u8(batt * 10.45);
  r[0x18] = u8(40 * 2.55);
  r[0x19] = u8((77.06 - 22.0) * 2.5371);
  r[0x20] = u8((stft / 100 + 1) * 128);
  r[0x21] = r[0x22] = u8((ltft / 100 + 1) * 128);
  const y = Math.trunc(pyRound(inj * 250));
  r[0x24] = y >> 8;
  r[0x25] = y & 0xff;
  r[0x26] = r[0x27] = u8(ign * 4 + 24);
  r[0x28] = u8(iacv * 2.55);
  r[0x3c] = 0;
  r[0x80] = noise & 0xff;
  r[0x90] = x & 0xff;
  r[0x91] = x >> 8;
  r[0xa0] = u8(tps * 2 + 24);
  r[0xb0] = 0x80 | (ac ? 0x10 : 0);
  return r;
}

function recording(rows: Int16Array[], dt = 0.2, t0 = 0, marks: Mark[] = []): SweepRecording {
  const rec = new SweepRecording();
  rows.forEach((row, i) => rec.append(row, new Float64Array(256).fill(t0 + i * dt)));
  rec.marks.push(...marks);
  return rec;
}

function driving(n = 60): SweepRecording {
  const rows: Int16Array[] = [];
  for (let i = 0; i < n; i++) {
    const tps = 40 * Math.abs(Math.sin(i / 7.0));
    const rpm = 800 + 2500 * Math.abs(Math.sin(i / 4.3 + 1));
    rows.push(ram({ rpm, tps, map_kpa: 30 + rpm / 200, ect: 20 + i, batt: 14.0 + 0.3 * Math.sin(i / 3.0),
      inj: 2.4 + rpm / 1000, noise: i }));
  }
  return recording(rows);
}

const sameCand = (c: { addr: number; width: number; endian: string; inverse: boolean }, addr: number, width = 1, endian = 'big', inverse = false) =>
  c.addr === addr && c.width === width && c.endian === endian && c.inverse === inverse;

// ---- recording ----------------------------------------------------------------

describe('sweep recording', () => {
  it('decode, window and marks', () => {
    const rec = driving(20);
    rec.marks.push({ label: 'blip', t0: 1.0, t1: 2.0 });
    expect(rec.length).toBe(20);
    expect(rec.t[3]).toBeCloseTo(0.6, 9);
    expect(rec.readAddresses()).toEqual(Array.from({ length: 256 }, (_, a) => a));
    const d = rec.decode(AMAP);
    expect(Math.abs(d.rpm![0]! - (800 + 2500 * Math.sin(1)))).toBeLessThan(0.3);
    expect(d.tps![0]).toBe(0);
    expect(d['rpm:t']![5]).toBeCloseTo(1.0, 9);
    expect(rec.memAt(0).get(0x13)).toBe(pyRound(106 / 0.716));
    expect(rec.window(1.0, 2.0).length).toBe(6);
    expect(rec.marked('blip').length).toBe(6);
    expect(rec.markMask('blip').flat().filter(Boolean).length).toBe(6 * 256);
  });

  it('from plain rows', () => {
    const rec = fromRows([new Map([[0, 1]]), [1, 2, 3]], [0.0, 1.0]);
    expect(rec.length).toBe(2);
    expect(rec.mem[0]![1]).toBe(-1);
    expect(rec.mem[1]![2]).toBe(3);
  });
});

// ---- correlation ----------------------------------------------------------------

describe('correlation finder', () => {
  it('finds the hidden copies and gets width and byte order right', () => {
    const rec = driving();
    const top = rankAgainstChannel(rec, AMAP, 'rpm', { top: 3 });
    const best = top[0]!;
    expect(sameCand(best.candidate, 0x90, 2, 'little')).toBe(true);
    expect(best.score).toBeGreaterThan(0.999);
    expect(candidateLabel(best.candidate)).toBe('0x90 u16le');
    expect(best.slope).toBeCloseTo(0.25, 3);
    expect(Math.abs(best.offset)).toBeLessThan(2);
    expect(matchFormula(best).startsWith('x * 0.25')).toBe(true);
    expect(candidateFields(best.candidate)).toEqual({ addr: 0x90, length: 2, endian: 'little' });
    expect(matchText(best)).toContain('score');

    const tps = rankAgainstChannel(rec, AMAP, 'tps', { top: 5 });
    expect(sameCand(tps[0]!.candidate, 0xa0)).toBe(true);
    expect(Math.abs(tps[0]!.slope - 0.5) / 0.5).toBeLessThan(2e-2);
    expect(Math.abs(tps[0]!.offset + 12)).toBeLessThan(1);
    const wide = tps.filter((m) => [0x9f, 0xa0].includes(m.candidate.addr) && m.candidate.width === 2);
    expect(wide.length).toBeGreaterThan(0);
    expect(wide.every((m) => m.score < tps[0]!.score && m.notes.length > 0)).toBe(true);
  });

  it('excludes own bytes and ignores dead bytes', () => {
    const rec = driving();
    for (const m of rankAgainstChannel(rec, AMAP, 'rpm', { top: 0 })) {
      expect([0x00, 0x01]).not.toContain(m.candidate.addr);
      expect(m.candidate.width === 2 && m.candidate.addr === 0xff).toBe(false);
      expect(m.span).toBeGreaterThan(0);
    }
    expect(() => rankAgainstChannel(rec, AMAP, 'nope')).toThrow(MapError);
  });

  it('takes an external reference at its own sample rate', () => {
    const rec = driving();
    const end = rec.t[rec.t.length - 1]!;
    const t = Array.from({ length: 500 }, (_, i) => (end * i) / 499);
    const volts = t.map((ti) => 14.0 + 0.3 * Math.sin(ti / 0.2 / 3.0));
    const top = rank(rec, t, volts, { top: 1 })[0]!;
    expect(sameCand(top.candidate, 0x17)).toBe(true);
    expect(Math.abs(top.slope - 1 / 10.45) / (1 / 10.45)).toBeLessThan(0.05);
    expect(top.rmse).toBeLessThan(0.1);
    expect(rank(rec, [0.0], [1.0])).toEqual([]);
    expect(rank(new SweepRecording(), t, volts)).toEqual([]);
  });

  it('Spearman follows a curved sensor', () => {
    const rec = driving();
    const refV = rec.t.map((_, i) => 20 + i);
    const found = Object.fromEntries(rank(rec, rec.t, refV, { method: 'spearman', top: 3 }).map((m) => [candidateLabel(m.candidate), m]));
    expect(Math.abs(found['0x10 u8']!.rho)).toBeGreaterThan(0.99);
    expect(Math.abs(found['0x10 u8']!.r)).toBeLessThan(0.995);
    const end = rec.t[rec.t.length - 1]!;
    const shape = Object.fromEntries(rankShape(rec, 'rise', 0, end, { top: 2 }).map((m) => [m.candidate.addr, m]));
    expect(new Set(Object.keys(shape).map(Number))).toEqual(new Set([0x10, 0x80]));
    expect(shape[0x10]!.rho).toBeLessThan(-0.99);
    expect(shape[0x80]!.rho).toBeGreaterThan(0.99);
    expect([0x10, 0x80]).toContain(rankShape(rec, 'fall', 0, end, { top: 2 })[0]!.candidate.addr);
    expect(() => rankShape(rec, 'wobble' as 'rise', 0, 1)).toThrow();
  });

  it('inverse candidates for period-style values', () => {
    const rows: Int16Array[] = [];
    const rpm: number[] = [];
    for (let i = 0; i < 40; i++) {
      const speed = 1000 + 100 * i;
      const period = Math.trunc(1875000 / speed);
      const r = ram({ noise: i * 37 });
      r[0xc0] = period >> 8;
      r[0xc1] = period & 0xff;
      rows.push(r);
      rpm.push(speed);
    }
    const rec = recording(rows);
    const best = rank(rec, rec.t, rpm, { inverse: true, top: 1 })[0]!;
    expect(sameCand(best.candidate, 0xc0, 2, 'big', true)).toBe(true);
    expect(Math.abs(best.slope - 1875000) / 1875000).toBeLessThan(0.01);
    expect(matchFormula(best)).toContain(' / x');
    const plain = rank(rec, rec.t, rpm, { top: 1 })[0]!;
    expect(Math.abs(plain.r)).toBeLessThan(Math.abs(best.r));
  });

  it('scores jitter down', () => {
    const rows: Int16Array[] = [];
    for (let i = 0; i < 40; i++) {
      const r = ram({ tps: i, noise: 0 });
      r[0xd0] = 100 + (i > 20 ? 1 : 0);
      rows.push(r);
    }
    const rec = recording(rows);
    const ms = Object.fromEntries(rank(rec, rec.t, rows.map((_, i) => i), { top: 0 }).map((m) => [candidateLabel(m.candidate), m]));
    expect(ms['0xD0 u8']!.score).toBeLessThan(0.5);
    expect(ms['0xD0 u8']!.notes[0]).toContain('barely moves');
    expect(ms['0x14 u8']!.score).toBeGreaterThan(0.99);
  });
});

// ---- typed targets ----------------------------------------------------------------

describe('typed targets', () => {
  it('find value for a typed reading', () => {
    const rec = recording(Array.from({ length: 8 }, (_, i) => ram({ rpm: 3000, batt: 13.97, noise: i })));
    const hits = findValue(rec, 3000, { formula: 'x / 4', relTol: 0.01 });
    expect(new Set(hits.map((h) => `${h.candidate.addr},${h.candidate.width},${h.candidate.endian}`)))
      .toEqual(new Set(['0,2,big', '144,2,little']));
    expect(hits[0]!.error).toBe(0);
    expect(valueMatchText(hits[0]!)).toContain('reads 3000');
    const volts = findValue(rec, 13.9, { formula: 'x / 10.45', tol: 0.1, relTol: 0, widths: [1] });
    expect(volts.map((h) => h.candidate.addr)).toEqual([0x17]);
    expect(() => findValue(rec, 1, { formula: 'x +' })).toThrow(MapError);
  });
});

// ---- bits ----------------------------------------------------------------

function acRecording(): SweepRecording {
  const rows = Array.from({ length: 30 }, (_, i) => ram({ rpm: 800, ac: i >= 10 && i < 20 ? 1 : 0, noise: i % 4 }));
  return recording(rows, 0.2, 0, [{ label: 'ac_on', t0: 2.0, t1: 3.9 }]);
}

describe('what changed', () => {
  it('finds the bits that follow the action', () => {
    const rec = acRecording();
    const found = changedBits(rec, 'ac_on');
    expect(found.map((c) => [c.addr, c.bit, c.outside, c.inside])).toEqual([[0x08, 1, 0, 1], [0x0b, 1, 0, 1], [0xb0, 4, 0, 1]]);
    expect(found[0]!.purity).toBe(1);
    expect(bitFormula(found[0]!)).toBe('bit(x, 1)');
    expect(bitChangeText(found[0]!)).toContain('0x08 bit 1: 0 -> 1');
    expect(changedBytes(rec, 'ac_on')).toEqual([0x08, 0x0b, 0xb0]);
    const table = flagChannelTable('ac2', found[2]!, 'A/C (copy)');
    const entry = { ...table, addr: BigInt(table.addr as number), length: BigInt(table.length as number) };
    expect(new Channel(entry).convert([0x90])).toBe(1);
    expect(changedBits(rec, 'no such mark')).toEqual([]);
    expect(changedBits(new SweepRecording(), 'x')).toEqual([]);
  });

  it('ignores free-running bits and tolerates sloppy timing', () => {
    const rows = Array.from({ length: 30 }, (_, i) => ram({ ac: i >= 11 && i < 21 ? 1 : 0, noise: i }));
    const rec = recording(rows, 0.2, 0, [{ label: 'ac_on', t0: 2.0, t1: 3.9 }]);
    expect(changedBits(rec, 'ac_on', 0.99)).toEqual([]);
    expect(changedBits(rec, 'ac_on', 0.9, 0.25).map((c) => [c.addr, c.bit])).toEqual([[0x08, 1], [0xb0, 4]]);
    const inverted = recording(Array.from({ length: 30 }, (_, i) => ram({ mil: i >= 10 && i < 20 ? 0 : 1 })), 0.2, 0,
      [{ label: 'm', t0: 2.0, t1: 3.9 }]);
    const all = changedBits(inverted, 'm');
    expect(all.length).toBe(1);
    expect([all[0]!.inside, bitFormula(all[0]!)]).toEqual([0, '1 - bit(x, 5)']);
    const steps = rankSteps(acRecording(), 'ac_on', { minSpan: 0, top: 3 });
    expect(new Set(steps.map((m) => m.candidate.addr))).toEqual(new Set([0x08, 0x0b, 0xb0]));
  });

  it('bit history', () => {
    const { t, bits } = bitHistory(acRecording(), 0x08);
    expect(bits.length).toBe(30);
    expect(bits.map((b) => b[1])).toEqual([...Array(10).fill(0), ...Array(10).fill(1), ...Array(10).fill(0)]);
    expect(bits.map((b) => b[2])).toEqual(Array(30).fill(1));
    expect(t[0]).toBe(0);
    expect(bits.reduce((s, b) => s + b[0]!, 0)).toBe(0);
  });
});

// ---- status ----------------------------------------------------------------

describe('map status', () => {
  it('coverage lists strong and weak scripts per channel', () => {
    const rows = Object.fromEntries(coverage(AMAP).map((r) => [r.channel, r]));
    // python: {r['channel']: [r['strong'], r['weak']] for r in verify.coverage(load_default())}
    const golden: Record<string, [string[], string[]]> = JSON.parse('{"rpm": [["idle", "steady_3000"], ["koeo", "tps_sweep", "warmup", "drive"]], "vss": [["drive"], ["koeo", "idle", "steady_3000"]], "ect": [["koeo", "warmup"], ["idle"]], "iat": [["koeo", "warmup"], []], "map": [["koeo"], ["idle"]], "baro": [[], ["koeo"]], "tps": [["tps_sweep"], ["koeo", "idle", "steady_3000"]], "o2": [["idle"], []], "batt": [["koeo", "idle"], ["steady_3000"]], "alt_fr": [[], []], "eld": [[], []], "stft": [[], ["idle"]], "ltft": [[], ["idle"]], "inj": [[], ["idle"]], "ign": [[], ["idle", "steady_3000"]], "ign_limit": [[], []], "iacv": [[], ["idle"]], "knock": [[], []], "ac_sw": [["ac_switch"], []], "vtec": [[], []], "mil": [[], ["koeo"]], "closed_loop": [["warmup"], ["koeo", "idle"]], "fuel_pump": [[], []], "fan": [[], []], "ac_clutch": [[], []], "brake": [[], []], "starter": [[], []]}');
    expect(Object.fromEntries(Object.entries(rows).map(([k, r]) => [k, [r.strong, r.weak]]))).toEqual(golden);
    expect(rows.rpm!.evidence?.result).toBe('pass');
  });

  it('scripts_for puts the strongest first', () => {
    expect(scriptsFor('tps').map((s) => s.id)).toEqual(['tps_sweep', 'koeo', 'idle', 'steady_3000']);
    expect(scriptsFor('nothing')).toEqual([]);
  });

  it('status text', () => {
    const lines = statusLines(AMAP);
    expect(lines[0]).toContain(`${AMAP.versionTag}   ECU variant: P2P   schema 2`);
    expect(lines.some((l) => l.startsWith('  knock        unverified check with: finder / ISO cross-check'))).toBe(true);
    expect(lines.some((l) => l.startsWith('  baro         unverified check with: koeo'))).toBe(false);
    const { verified, total } = verifiedCount(AMAP);
    expect(lines[lines.length - 1]).toBe(`${verified} of ${total} channels verified`);
    expect(verified).toBe(14);
    const m = loads("[[channel]]\nname='map'\naddr=1\n");
    expect(statusLines(m)[1]).toBe('  map          unverified check with: koeo');
  });
});

describe('Live groups', () => {
  it.each([
    ['idle_target', 'idle'], ['iac_cmd', 'idle'], ['iacv', 'idle'], ['iac_learn', 'idle'],
    ['stft', 'fuel'], ['ltft', 'fuel'], ['o2', 'fuel'], ['o2_2', 'fuel'], ['inj', 'fuel'], ['purge_duty', 'fuel'],
    ['fuel_status', 'fuel'], ['rpm', 'engine'], ['ect', 'engine'], ['load_ecu', 'engine'], ['ign', 'ignition'],
    ['knock', 'ignition'], ['batt', 'electrical'], ['eld', 'electrical'], ['alt_fr', 'electrical'],
    ['brake', 'switches'], ['psp', 'switches'], ['vtec_psw', 'switches'], ['raw_2b', 'raw'], ['raw_03', 'raw'],
    ['gps_speed', 'cyd'], ['mystery', 'raw'],
  ])('%s -> %s', (name, group) => {
    expect(groupOf(name)).toBe(group);
  });

  it('an unknown flag is a switch', () => {
    expect(groupOf('door', 'flag')).toBe('switches');
  });
});
