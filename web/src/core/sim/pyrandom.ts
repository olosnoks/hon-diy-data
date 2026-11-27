/**
 * Python's `random.Random` (MT19937), bit-exact for the calls the simulator
 * makes: seed(int), random(), getrandbits(k <= 32), randrange, randint,
 * uniform. A seeded simulator run therefore draws the same engine noise and
 * the same line faults as the Python one.
 */

const N = 624;
const M = 397;

export class PyRandom {
  private mt = new Uint32Array(N);
  private mti = N + 1;

  constructor(seed = 1) {
    this.seed(seed);
  }

  private initGenrand(s: number): void {
    const mt = this.mt;
    mt[0] = s >>> 0;
    for (let i = 1; i < N; i++) {
      const prev = mt[i - 1]! ^ (mt[i - 1]! >>> 30);
      // 1812433253 * prev + i, mod 2^32, without losing precision
      mt[i] = (Math.imul(1812433253, prev) + i) >>> 0;
    }
    this.mti = N;
  }

  /** Python's seed(n) for an int: init_by_array over |n| in 32-bit words. */
  seed(n: number): void {
    let v = BigInt(Math.abs(Math.trunc(n)));
    const key: number[] = [];
    if (v === 0n) key.push(0);
    while (v > 0n) {
      key.push(Number(v & 0xffffffffn));
      v >>= 32n;
    }
    this.initGenrand(19650218);
    const mt = this.mt;
    let i = 1, j = 0;
    const len = key.length;
    for (let k = Math.max(N, len); k > 0; k--) {
      const prev = mt[i - 1]! ^ (mt[i - 1]! >>> 30);
      mt[i] = ((mt[i]! ^ Math.imul(prev, 1664525)) + key[j]! + j) >>> 0;
      i++; j++;
      if (i >= N) { mt[0] = mt[N - 1]!; i = 1; }
      if (j >= len) j = 0;
    }
    for (let k = N - 1; k > 0; k--) {
      const prev = mt[i - 1]! ^ (mt[i - 1]! >>> 30);
      mt[i] = ((mt[i]! ^ Math.imul(prev, 1566083941)) - i) >>> 0;
      i++;
      if (i >= N) { mt[0] = mt[N - 1]!; i = 1; }
    }
    mt[0] = 0x80000000;
  }

  private genrand(): number {
    const mt = this.mt;
    if (this.mti >= N) {
      let kk = 0;
      const mag = (y: number) => (y & 1 ? 0x9908b0df : 0);
      for (; kk < N - M; kk++) {
        const y = (mt[kk]! & 0x80000000) | (mt[kk + 1]! & 0x7fffffff);
        mt[kk] = mt[kk + M]! ^ (y >>> 1) ^ mag(y);
      }
      for (; kk < N - 1; kk++) {
        const y = (mt[kk]! & 0x80000000) | (mt[kk + 1]! & 0x7fffffff);
        mt[kk] = mt[kk + (M - N)]! ^ (y >>> 1) ^ mag(y);
      }
      const y = (mt[N - 1]! & 0x80000000) | (mt[0]! & 0x7fffffff);
      mt[N - 1] = mt[M - 1]! ^ (y >>> 1) ^ mag(y);
      this.mti = 0;
    }
    let y = mt[this.mti++]!;
    y ^= y >>> 11;
    y ^= (y << 7) & 0x9d2c5680;
    y ^= (y << 15) & 0xefc60000;
    y ^= y >>> 18;
    return y >>> 0;
  }

  /** Float in [0, 1) with 53 random bits. */
  random(): number {
    const a = this.genrand() >>> 5;
    const b = this.genrand() >>> 6;
    return (a * 67108864 + b) / 9007199254740992;
  }

  getrandbits(k: number): number {
    if (k <= 0) return 0;
    if (k > 32) throw new RangeError('getrandbits: k <= 32 only');
    return this.genrand() >>> (32 - k);
  }

  private randbelow(n: number): number {
    if (n <= 0) throw new RangeError('empty range');
    const k = 32 - Math.clz32(n);   // n.bit_length(), n < 2**32
    let r = this.getrandbits(k);
    while (r >= n) r = this.getrandbits(k);
    return r;
  }

  /** randrange(stop) or randrange(start, stop). */
  randrange(start: number, stop?: number): number {
    if (stop === undefined) return this.randbelow(start);
    return start + this.randbelow(stop - start);
  }

  randint(a: number, b: number): number {
    return this.randrange(a, b + 1);
  }

  uniform(a: number, b: number): number {
    return a + (b - a) * this.random();
  }
}
