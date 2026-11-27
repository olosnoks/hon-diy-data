// version_hash and formula decoding against golden output of the Python code
// (fixtures/golden.json, made by running hondalog.addressmap on the same text).
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { loadDefault, loads, MapError } from './index';
import { formulaExpr } from './addressmap';
import { PyFloat, pyFloatRepr, pyJsonDumps, pyStrRepr } from './pyfmt';

interface Golden {
  maps: Record<string, { text: string; variant: string | null; hash: string; spec: string; decode: Record<string, number | null> }>;
  formulas: { f: string; values: (number | null)[]; two: number | null }[];
  bad: { f: string; error: string | null }[];
}

const GOLDEN = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'golden.json'), 'utf-8')) as Golden;
const INPUTS = [0, 1, 7, 100, 128, 200, 255];

describe('version hash golden (Python AddressMap.version_hash)', () => {
  // python -c "from hondalog import addressmap as a; print(a.load_default().version_hash)" etc.
  it.each([
    ['P2P', '071de5244d2b'],
    ['P2T', '56fc65a6cba5'],
    ['P28', 'd58d503473f1'],
  ])('shipped map, variant %s', (variant, hash) => {
    expect(loadDefault(variant).versionHash).toBe(hash);
  });

  it('the default variant of the shipped map is P2P', () => {
    expect(loadDefault().versionHash).toBe('071de5244d2b');
  });

  it.each(Object.entries(GOLDEN.maps))('synthetic map %s: spec text and hash', (_name, g) => {
    const m = loads(g.text, g.variant);
    expect(m.decodeSpecJson()).toBe(g.spec);
    expect(m.versionHash).toBe(g.hash);
  });

  it.each(Object.entries(GOLDEN.maps))('synthetic map %s: decode', (_name, g) => {
    const m = loads(g.text, g.variant);
    const mem = Array.from({ length: 256 }, (_, a) => (a * 37 + 11) & 0xff);
    const got = m.decode(mem);
    for (const [k, v] of Object.entries(g.decode)) {
      if (v === null) expect(got[k], k).toBeNull();
      else expect(got[k], k).toBeCloseTo(v, 9);
    }
  });

  // The owner's live map is not in the repo. To check it, write its text
  // (read through Python) to a file outside the repo and run with
  // HONDALOG_LIVE_MAP=<file> HONDALOG_LIVE_HASH=8f3b481ce1dd npx vitest run src/core/addressmap
  const live = typeof process !== 'undefined' ? process.env.HONDALOG_LIVE_MAP : undefined;
  it.skipIf(!live)('the live map (HONDALOG_LIVE_MAP) hashes like Python', () => {
    const text = readFileSync(live!, 'utf-8');
    const m = loads(text, 'P2P');
    expect(m.versionHash).toBe(process.env.HONDALOG_LIVE_HASH ?? '8f3b481ce1dd');
    // optional: {"addr pattern decode": ...} from Python, mem[a] = (a * 37 + 11) & 0xFF
    const decodeFile = process.env.HONDALOG_LIVE_DECODE;
    if (decodeFile) {
      const want = JSON.parse(readFileSync(decodeFile, 'utf-8')) as Record<string, number | null>;
      const got = m.decode(Array.from({ length: 256 }, (_, a) => (a * 37 + 11) & 0xff));
      for (const [k, v] of Object.entries(want)) {
        if (v === null) expect(got[k], k).toBeNull();
        else expect(got[k], k).toBeCloseTo(v, 9);
      }
    }
  });
});

describe('Python text helpers', () => {
  it.each([
    [1, '1.0'], [0.1, '0.1'], [1e-7, '1e-07'], [1e16, '1e+16'], [1234567890123456, '1234567890123456.0'],
    [-2.5, '-2.5'], [0.0001, '0.0001'], [0.00001, '1e-05'], [3e300, '3e+300'], [-0, '-0.0'], [0, '0.0'],
    [1.5e-300, '1.5e-300'], [123456789.125, '123456789.125'], [1 / 3, '0.3333333333333333'],
    [2 ** 60, '1.152921504606847e+18'], [Infinity, 'inf'], [NaN, 'nan'],
  ])('repr(%s) = %s', (x, want) => {
    expect(pyFloatRepr(x)).toBe(want);
  });

  it('json.dumps layout', () => {
    expect(pyJsonDumps({ b: [1, new PyFloat(2), 'é"\\\n\u0001'], a: null })).toBe('{"a":null,"b":[1,2.0,"é\\"\\\\\\n\\u0001"]}');
    expect(pyJsonDumps([new PyFloat(Infinity), new PyFloat(NaN)])).toBe('[Infinity,NaN]');
  });

  it('repr of text', () => {
    expect(pyStrRepr('x +')).toBe("'x +'");
    expect(pyStrRepr("it's")).toBe('"it\'s"');
    expect(pyStrRepr('a\\b\n')).toBe("'a\\\\b\\n'");
  });
});

describe('formulas decode like Python', () => {
  it.each(GOLDEN.formulas.map((g) => [g.f, g] as const))('%s', (_f, g) => {
    const expr = formulaExpr(g.f);
    INPUTS.forEach((x, i) => {
      const want = g.values[i]!;
      const got = expr.eval({ x, b: [x] });
      if (want === null) expect(got, `x=${x}`).toBeNull();
      else expect(got, `x=${x}`).toBeCloseTo(want, 9);
    });
    const two = expr.eval({ x: 0x1234, b: [0x12, 0x34] });
    if (g.two === null) expect(two).toBeNull();
    else expect(two).toBeCloseTo(g.two, 9);
  });

  it.each(GOLDEN.bad.map((g) => [g.f, g] as const))('bad formula %j', (f, g) => {
    let got: string | null = null;
    try {
      formulaExpr(f);
    } catch (exc) {
      expect(exc).toBeInstanceOf(MapError);
      got = (exc as Error).message;
    }
    expect(got).toBe(g.error);
  });
});
