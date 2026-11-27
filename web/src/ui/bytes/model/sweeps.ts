/** Sweep recordings: every sweep's 256 bytes with the time each was read
 *  (hondalog/mapping/sweeps.py SweepRecording, Mark, from_rows, chunks). */

export const UNREAD = -1;

export interface Mark { label: string; t0: number; t1: number | null }

export function inMark(m: Mark, t: number): boolean {
  return t >= m.t0 && t <= (m.t1 ?? Infinity);
}

export class SweepRecording {
  mem: Int16Array[] = [];
  times: Float64Array[] = [];
  marks: Mark[] = [];
  meta: Record<string, unknown> = {};

  get length(): number {
    return this.mem.length;
  }

  append(mem: ArrayLike<number>, times: ArrayLike<number> | number): void {
    this.mem.push(Int16Array.from(mem));
    this.times.push(typeof times === 'number' ? new Float64Array(256).fill(times) : Float64Array.from(times));
  }

  /** One time per sweep: when its last chunk arrived. */
  get t(): number[] {
    return this.times.map((row) => row.reduce((a, b) => Math.max(a, b), -Infinity));
  }

  readAddresses(): number[] {
    const out: number[] = [];
    for (let a = 0; a < 256; a++) if (this.mem.some((row) => row[a]! >= 0)) out.push(a);
    return out;
  }

  memAt(i: number): Map<number, number> {
    const row = this.mem[i]!;
    const out = new Map<number, number>();
    for (let a = 0; a < 256; a++) if (row[a]! >= 0) out.set(a, row[a]!);
    return out;
  }

  window(t0: number, t1: number): SweepRecording {
    const out = new SweepRecording();
    const ts = this.t;
    ts.forEach((t, i) => {
      if (t >= t0 && t <= t1) { out.mem.push(this.mem[i]!); out.times.push(this.times[i]!); }
    });
    out.marks = [...this.marks];
    out.meta = { ...this.meta };
    return out;
  }

  /** (N x 256) which readings were inside marks with this label. */
  markMask(label: string): boolean[][] {
    return this.times.map((row) => Array.from(row, (t) => this.marks.some((m) => m.label === label && inMark(m, t))));
  }
}

export function fromRows(rows: (ArrayLike<number> | Record<number, number>)[], t: number[]): SweepRecording {
  const rec = new SweepRecording();
  rows.forEach((row, i) => {
    const mem = new Int16Array(256).fill(UNREAD);
    if (typeof (row as ArrayLike<number>).length === 'number') {
      const r = row as ArrayLike<number>;
      for (let a = 0; a < Math.min(256, r.length); a++) mem[a] = r[a]!;
    } else {
      for (const [a, v] of Object.entries(row)) mem[Number(a)] = v;
    }
    rec.append(mem, t[i]!);
  });
  return rec;
}

/** (address, length) reads covering lo..hi inclusive. */
export function chunks(lo = 0x00, hi = 0xff, size = 16): [number, number][] {
  [lo, hi] = [lo & 0xff, hi & 0xff].sort((a, b) => a - b) as [number, number];
  size = Math.max(1, size);
  const out: [number, number][] = [];
  for (let a = lo; a <= hi; a += size) out.push([a, Math.min(size, hi + 1 - a)]);
  return out;
}
