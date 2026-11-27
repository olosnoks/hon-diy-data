import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  assignAddress, bumpVersion, decodeWith, diffMaps, markChannel, nextVersion, parseMapText, pyFloat, setChannel,
  validateMap,
} from './mapdoc';

const SHIPPED = readFileSync(resolve(__dirname, 'fixtures/addressmap.toml'), 'utf-8');

const SMALL = `schema = 2
version = "test-1"

[[channel]]
name = "rpm"
addr = 0x00
length = 2
formula = "x / 4"   # quarter rpm
status = "verified"

[[channel]]
name = "tps"
addr = 0x14
formula = "x / 2.55"

[constants]
k = 2
`;

describe('address map document', () => {
  it('hashes the shipped map exactly like Python (golden)', () => {
    // hondalog.addressmap.load(...).version_hash, run 2026-11-27
    expect(parseMapText(SHIPPED).hash).toBe('071de5244d2b');
    expect(parseMapText(SHIPPED, 'P2T').hash).toBe('56fc65a6cba5');
    expect(parseMapText(SHIPPED, 'P28').hash).toBe('d58d503473f1');
    expect(parseMapText(SHIPPED).channels).toHaveLength(27);
  });

  it('next_version matches mapedit.next_version', () => {
    expect(nextVersion('p2p-guess-2 (kerpz)')).toBe('p2p-guess-3 (kerpz)');
    expect(nextVersion('p2p')).toBe('p2p r2');
    expect(nextVersion('p2p-guess-6')).toBe('p2p-guess-7');
    expect(nextVersion('v1.2 beta')).toBe('v1.2 beta r2');
    expect(nextVersion('')).toBe('r2');
  });

  it('assigning an address resets verification and bumps the version (as update_channel)', () => {
    const out = assignAddress(SHIPPED, 'tps', 0x15);
    const m = parseMapText(out);
    expect(m.version).toBe('p2p-guess-3 (kerpz P2T map)');
    const tps = m.channels.find((c) => c.name === 'tps')!;
    expect(tps.addr).toBe(0x15);
    expect(tps.status).toBe('unverified');
    expect(m.hash).toBe('eb97322e38a6');      // Python, same edit
  });

  it('marks keep comments and leave the hash alone', () => {
    const before = parseMapText(SMALL);
    const out = markChannel(SMALL, 'tps', 'suspect', { method: 'manual', summary: 'reads 0 at WOT' });
    expect(out).toContain('# quarter rpm');
    const after = parseMapText(out);
    expect(after.channels[1]!.status).toBe('suspect');
    expect(after.channels[1]!.evidence?.summary).toBe('reads 0 at WOT');
    expect(after.hash).toBe(before.hash);
    // marking again replaces the evidence, not appends
    const again = parseMapText(markChannel(out, 'tps', 'verified', { method: 'guided:tps_sweep' }));
    expect(again.channels[1]!.evidence).toEqual({ method: 'guided:tps_sweep' });
  });

  it('validates', () => {
    expect(validateMap(SMALL)).toEqual([]);
    expect(validateMap(SMALL.replace('addr = 0x14', 'addr = 0x140'))[0]).toMatch(/addr out of range/);
    expect(validateMap(SMALL.replace('name = "tps"', 'name = "rpm"'))[0]).toMatch(/duplicate/);
    expect(validateMap(SMALL.replace('"x / 2.55"', '"x / "'))[0]).toMatch(/bad formula/);
    expect(validateMap('version = [')[0]).toMatch(/not valid TOML/);
    expect(validateMap('bogus = 1\n' + SMALL)[0]).toMatch(/unknown section or key/);
  });

  it('diffs two maps', () => {
    const a = parseMapText(SMALL);
    const b = parseMapText(bumpVersion(setChannel(SMALL, 'tps', { formula: 'x / 2' })));
    const d = diffMaps(a, b);
    expect(d.decodeChanged).toBe(true);
    expect(d.changes.map((c) => `${c.section}.${c.name}.${c.field}`)).toEqual(['map..version', 'channel.tps.formula']);
  });

  it('decodes a sweep', () => {
    const m = parseMapText(SMALL);
    expect(decodeWith(m, { 0: 0x0b, 1: 0xb8, 0x14: 255 })).toEqual({ rpm: 750, tps: 100 });
    expect(decodeWith(m, { 0: 1 }).rpm).toBeNull();
  });

  it('writes floats like Python', () => {
    expect(pyFloat(4)).toBe('4.0');
    expect(pyFloat(1.59)).toBe('1.59');
    expect(pyFloat(1e-7)).toBe('1e-07');
  });
});
