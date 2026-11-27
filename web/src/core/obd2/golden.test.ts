/** Golden values from the Python decoders (hondalog/obd2.py), generated with
 *  the Python app into golden/decoders.json. */

import { describe, expect, it } from 'vitest';
import golden from './golden/decoders.json';
import { decodeDtc, decodePid, pidText } from './obd2';

const G = golden as unknown as Record<string, (number | string | null)[]>;

describe('decoders match Python byte for byte', () => {
  for (const pid of ['04', '05', '06', '07', '0B', '0E', '0F', '11', '14']) {
    it(`PID ${pid}`, () => {
      const got = Array.from({ length: 256 }, (_, a) => decodePid(parseInt(pid, 16), Uint8Array.of(a, 0)).value);
      expect(got).toEqual(G[pid]);
    });
  }

  it('PID 0C', () => {
    const got: (number | null)[] = [];
    for (let a = 0; a < 256; a += 7) for (let b = 0; b < 256; b += 13) got.push(decodePid(0x0c, Uint8Array.of(a, b)).value);
    expect(got).toEqual(G['0C']);
  });

  it('DTC codes', () => {
    const got: string[] = [];
    for (let h = 0; h < 256; h += 5) for (let l = 0; l < 256; l += 11) got.push(decodeDtc(h, l));
    expect(got).toEqual(G['dtc']);
  });

  it('PID text', () => {
    const got: string[] = [];
    for (const p of [3, 0x13, 0x1c, 0x0c, 0x14, 0x77]) for (const a of [0, 1, 2, 3, 4, 8, 16, 0x7f]) got.push(pidText(decodePid(p, Uint8Array.of(a, 0))));
    expect(got).toEqual(G['text']);
  });
});
