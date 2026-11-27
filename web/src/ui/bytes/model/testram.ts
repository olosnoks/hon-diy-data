/** A scriptable fake ECU RAM image for the Bytes tests (tests/mapping_helpers.py). */

import { SweepRecording, type Mark } from './sweeps';

export interface RamOpts {
  rpm?: number; tps?: number; batt?: number; ect?: number; ac?: number; mil?: number; noise?: number;
}

export function ram(o: RamOpts = {}): Int16Array {
  const { rpm = 0, tps = 0, batt = 12.4, ect = 20, ac = 0, mil = 0, noise = 0 } = o;
  const r = new Int16Array(256).fill(0x55);
  const u8 = (v: number) => Math.max(0, Math.min(255, Math.round(v)));
  const x = Math.max(0, Math.min(0xffff, Math.round(rpm * 4)));
  r[0x00] = x >> 8; r[0x01] = x & 0xff;
  r[0x08] = 0x84 | (ac ? 0x02 : 0);
  r[0x0b] = (rpm > 100 ? 0x40 : 0) | (mil ? 0x20 : 0) | (ac && rpm > 100 ? 0x02 : 0);
  r[0x10] = u8(200 - ect * 2);            // a thermistor: count falls as it warms
  r[0x14] = u8(tps * 2.55);
  r[0x17] = u8(batt * 10.45);
  r[0x80] = noise & 0xff;                  // a free-running counter
  r[0x90] = x & 0xff; r[0x91] = x >> 8;    // rpm again, little-endian
  r[0xa0] = u8(tps * 2 + 24);              // throttle on another scale
  r[0xb0] = 0x80 | (ac ? 0x10 : 0);        // A/C again, bit 4
  return r;
}

export function recording(rows: Int16Array[], dt = 0.2, t0 = 0, marks: Mark[] = []): SweepRecording {
  const rec = new SweepRecording();
  rows.forEach((row, i) => rec.append(row, t0 + i * dt));
  rec.marks.push(...marks);
  return rec;
}

export const TEST_MAP = `schema = 2
version = "test-1"

[[channel]]
name = "rpm"
addr = 0x00
length = 2
formula = "x / 4"
status = "verified"

[[channel]]
name = "tps"
addr = 0x14
formula = "x / 2.55"

[[channel]]
name = "batt"
addr = 0x17
formula = "x / 10.45"

[[channel]]
name = "ac_sw"
addr = 0x08
formula = "bit(x, 1)"
kind = "flag"
`;

export function driving(n = 60): SweepRecording {
  const rows: Int16Array[] = [];
  for (let i = 0; i < n; i++) {
    const tps = 40 * Math.abs(Math.sin(i / 7));
    const rpm = 800 + 2500 * Math.abs(Math.sin(i / 4.3 + 1));
    rows.push(ram({ rpm, tps, ect: 20 + i, batt: 14 + 0.3 * Math.sin(i / 3), noise: i }));
  }
  return recording(rows);
}
