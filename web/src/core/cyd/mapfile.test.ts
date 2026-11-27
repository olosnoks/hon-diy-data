import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { sha256 } from '@noble/hashes/sha256';
import { equalBytes, hex, utf8 } from './bytes';
import { Pool, Unsupported, compileCondition, compileFormula } from './bytecode';
import * as F from './format';
import { pyFloatRepr, resolveMap, fromContract } from './hhmap';
import { DEFAULT_CFG, MapError, buildMap, buildMapFromContract, mapOfFile, newReport, readMap } from './mapfile';

const FIX = join(dirname(fileURLToPath(import.meta.url)), '__fixtures__');
const bin = (name: string) => new Uint8Array(readFileSync(join(FIX, name)));
const text = (name: string) => readFileSync(join(FIX, name), 'utf-8');
const MAPS = JSON.parse(text('maps.json'));
const TOML = text('addressmap.toml');

function firstDiff(a: Uint8Array, b: Uint8Array): number {
  for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return i;
  return a.length === b.length ? -1 : Math.min(a.length, b.length);
}

describe('HANDHELD.MAP is byte-identical to the Python', () => {
  for (const name of ['default', 'p2t', 'p28', 'proto', 'unverified_vss_p2t', 'stall_opts']) {
    it(name, () => {
      const c = MAPS[name];
      const src = text(c.toml);
      const map = resolveMap(src, c.variant);
      expect(map.versionHash).toBe(c.hash);
      const report = newReport();
      const data = buildMap(map, src, c.stall, report);
      const want = bin(`${name}.map`);
      expect(firstDiff(data, want)).toBe(-1);
      expect(report.channels).toEqual(c.channels);
      expect(report.math).toEqual(c.math);
      expect(report.alarms).toEqual(c.alarms);
      expect(report.skipped).toEqual(c.skipped);
      expect([report.coreBytes, report.totalBytes]).toEqual([c.core_bytes, c.total_bytes]);
    });
  }

  it('matches `hondalog-handheld export-map` of the shipped map', () => {
    expect(MAPS.export_map_matches_build_map).toBe(true);
    const data = buildMap(resolveMap(TOML), TOML);
    expect(equalBytes(data, bin('export-map.HANDHELD.MAP'))).toBe(true);
    expect(MAPS.export_map_cfg).toBe(DEFAULT_CFG);
  });

  it('from the shared AddressMap contract', () => {
    const map = resolveMap(TOML);
    const amap = {
      version: map.version, variant: map.variant, channels: [], math: [], alarms: [], constants: {}, protocol: {},
      ecuId: { addr: 0x76, length: 10 }, versionHash: MAPS.default.hash, text: TOML,
    };
    expect(equalBytes(buildMapFromContract(amap), bin('default.map'))).toBe(true);
    expect(() => fromContract({ ...amap, versionHash: '000000000000' })).toThrow(/mismatch/);
  });
});

describe('reading a map (test_hh_map)', () => {
  const data = bin('default.map');
  const mf = readMap(data);
  const map = resolveMap(TOML);

  it('carries the map, its hash and its text', () => {
    expect(mf.mapHash).toBe(map.versionHash);
    expect(mf.version).toBe(map.version);
    expect(mf.variant).toBe('P2P');
    expect(mf.source).toBe(TOML);
    expect(hex(mf.mapSha256)).toBe(hex(sha256(utf8(TOML))));
    expect(mapOfFile(mf)!.versionHash).toBe(map.versionHash);
    expect(mf.core.length).toBeLessThan(F.MAP_MAX_CORE);
    expect([mf.ecuIdAddr, mf.ecuIdLen]).toEqual([0x76, 10]);
    expect(mf.protocol.baud).toBe(10400);
    expect(mf.protocol.rate_fast_hz).toBe(20);
    expect(mf.protocol.max_read_len).toBe(16);
  });

  it('channels, limits and what is left out', () => {
    const by: Record<string, any> = Object.fromEntries(mf.channels.map((c) => [c.name, c]));
    expect(mf.channels.slice(0, map.channels.length).map((c) => c.name)).toEqual(map.channels.map((c) => c.name));
    const { rpm, ect, batt, vtec } = by;
    expect([rpm.addr, rpm.length, rpm.rate_class, rpm.kind]).toEqual([0, 2, 0, F.KIND_VALUE]);
    expect([rpm.lo, rpm.hi, rpm.units, rpm.label]).toEqual([0, 7200, 'rpm', 'RPM']);
    expect(rpm.warn_hi).toBe(6800);
    expect(Number.isNaN(rpm.alarm_hi) && Number.isNaN(rpm.warn_lo)).toBe(true);
    expect(ect.alarm_hi).toBe(105);
    expect(ect.units).toBe('°C');
    expect(batt.warn_lo).toBe(12);
    expect(batt.warn_hi).toBeCloseTo(15.2, 5);
    expect(vtec.kind).toBe(F.KIND_FLAG);
    expect(by.inj_duty.kind).toBe(F.KIND_MATH);
    expect(by.ign.units).toBe('° BTDC');
    expect(by.total_trim.verified).toBe(true);
    expect(by.inj_duty.verified).toBe(false);
    const alarms: Record<string, any> = Object.fromEntries(mf.alarms.map((a) => [a.name, a]));
    expect(alarms.ect_high.channel).toBe(map.channels.findIndex((c) => c.name === 'ect'));
    expect(alarms.ect_high.flags & F.AF_LATCHING).toBeTruthy();
    expect(alarms.lean_under_load.channel).toBe(-1);
    expect(alarms.idle_collapse.rate_low).toBe(-400);
    expect(mf.stall).toEqual({ rpm_run: 400, rpm_zero: 50, link_down_s: 1, pre_s: 30, post_s: 10, rpm_channel: 0 });
  });

  it('refuses damaged files; a damaged text costs only the text', () => {
    expect(() => readMap(data.slice(0, 40))).toThrow();
    expect(() => readMap(new Uint8Array(200))).toThrow();
    const broken = data.slice();
    broken[300]! ^= 1;
    expect(() => readMap(broken)).toThrow(/checksum/);
    const t = data.slice();
    t[mf.core.length + 50]! ^= 1;
    const back = readMap(t);
    expect(back.source).toBe('');
    expect(back.channels.length).toBe(mf.channels.length);
  });

  it('a formula the handheld cannot run stops the export', () => {
    const bad = TOML.replace('formula = "x / 51.3"', 'formula = "b[x]"');
    expect(() => buildMap(resolveMap(bad), bad)).toThrow(MapError);
    expect(() => buildMap(resolveMap(bad), bad)).toThrow(/o2/);
  });
});

describe('byte code (test_hh_core)', () => {
  it('what needs history is refused', () => {
    const pool = new Pool();
    for (const t of ['slope([rpm], 500)', '[rpm-1]', 'avg(rpm, 100)', 'nosuch(1)']) {
      expect(() => compileCondition(t, pool, new Map([['rpm', 0]]), new Map())).toThrow(Unsupported);
    }
    expect(() => compileFormula('b', pool, new Map(), 1)).toThrow(Unsupported);
  });

  it('small integers are immediates, the rest pooled', () => {
    const pool = new Pool();
    expect(Array.from(compileFormula('x * 2 + 1.0', pool, new Map(), 1)))
      .toEqual([F.OP_X, F.OP_IMM, 2, F.OP_BINARY['*'], F.OP_CONST, 0, 0, F.OP_BINARY['+'], F.OP_END]);
    expect(pool.values).toEqual([1]);
    expect(Array.from(compileFormula('b[-1]', pool, new Map(), 2))).toEqual([F.OP_BYTE, 1, F.OP_END]);
    expect(Array.from(compileFormula('1 < x <= 3', pool, new Map(), 1))).toEqual([
      F.OP_IMM, 1, F.OP_X, F.OP_COMPARE['<'], F.OP_X, F.OP_IMM, 3, F.OP_COMPARE['<='], F.OP_AND, F.OP_END]);
  });

  it('coalesce drops OBD2-only arguments', () => {
    const [prog] = compileCondition('coalesce([calc_load], [map])', new Pool(), new Map([['map', 4]]), new Map(), ['calc_load']);
    expect(Array.from(prog)).toEqual([F.OP_CH, 4, F.OP_END]);
    expect(() => compileCondition('coalesce([calc_load])', new Pool(), new Map(), new Map(), ['calc_load'])).toThrow(Unsupported);
  });
});

describe('python float repr for the version hash', () => {
  it.each([[4, '4.0'], [1.59, '1.59'], [0.745, '0.745'], [1e16, '1e+16'], [1e15, '1000000000000000.0'],
    [0.0001, '0.0001'], [0.00001, '1e-05'], [-2.5e-7, '-2.5e-07'], [123456789.125, '123456789.125'], [0, '0.0']])(
    '%s', (x, want) => expect(pyFloatRepr(x as number)).toBe(want));
});
