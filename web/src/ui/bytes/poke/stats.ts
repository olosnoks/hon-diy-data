/** The poke test's statistics (tools/poke_test.py): average ranks, the
 *  two-sided Mann-Whitney U test (normal approximation, tie correction),
 *  Fisher's exact test and Spearman's rho. */

export function ranks(xs: number[]): number[] {
  const order = xs.map((_, i) => i).sort((a, b) => xs[a]! - xs[b]! || a - b);
  const out = new Array<number>(xs.length).fill(0);
  let i = 0;
  while (i < order.length) {
    let j = i;
    while (j + 1 < order.length && xs[order[j + 1]!] === xs[order[i]!]) j++;
    const r = (i + j) / 2 + 1;
    for (let k = i; k <= j; k++) out[order[k]!] = r;
    i = j + 1;
  }
  return out;
}

/** erfc: Taylor-series erf below 0.5, Numerical Recipes erfcc above (relative error < 1.2e-7,
 *  far finer than the p thresholds it is compared with). */
export function erfc(x: number): number {
  const z = Math.abs(x);
  let r: number;
  if (z < 0.5) {
    r = 1 - erf(z);
  } else {
    const t = 1 / (1 + 0.5 * z);
    const y = t * Math.exp(-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418
      + t * (-0.18628806 + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))));
    r = y;
  }
  return x >= 0 ? r : 2 - r;
}

/** erf by its Taylor series (small |x|). */
export function erf(x: number): number {
  let sum = x, term = x;
  for (let n = 1; n < 60; n++) {
    term *= (-x * x) / n;
    const add = term / (2 * n + 1);
    sum += add;
    if (Math.abs(add) < 1e-17 * Math.abs(sum)) break;
  }
  return (2 / Math.sqrt(Math.PI)) * sum;
}

export function mannWhitneyP(a: number[], b: number[]): number {
  const n1 = a.length, n2 = b.length;
  if (!n1 || !n2) return 1;
  const all = [...a, ...b];
  const r = ranks(all);
  let u1 = -n1 * (n1 + 1) / 2;
  for (let i = 0; i < n1; i++) u1 += r[i]!;
  const n = n1 + n2;
  const counts = new Map<number, number>();
  for (const v of all) counts.set(v, (counts.get(v) ?? 0) + 1);
  let tie = 0;
  for (const t of counts.values()) tie += t ** 3 - t;
  const v = n > 1 ? (n1 * n2) / 12 * ((n + 1) - tie / (n * (n - 1))) : 0;
  if (v <= 0) return 1;
  const z = Math.max(0, Math.abs(u1 - (n1 * n2) / 2) - 0.5) / Math.sqrt(v);
  return erfc(z / Math.SQRT2);
}

const LOG_FACT: number[] = [0];
function logFact(n: number): number {
  for (let k = LOG_FACT.length; k <= n; k++) LOG_FACT.push(LOG_FACT[k - 1]! + Math.log(k));
  return LOG_FACT[n]!;
}
const logComb = (n: number, k: number) => logFact(n) - logFact(k) - logFact(n - k);

/** Two-sided Fisher exact test on [[k1, n1-k1], [k2, n2-k2]]. */
export function fisherP(k1: number, n1: number, k2: number, n2: number): number {
  const K = k1 + k2, N = n1 + n2;
  if (K === 0 || K === N || n1 === 0 || n2 === 0) return 1;
  const lo = Math.max(0, K - n2), hi = Math.min(K, n1);
  const base = logComb(N, K);
  const lp = (x: number) => logComb(n1, x) + logComb(n2, K - x) - base;
  const obs = lp(k1);
  let total = 0;
  for (let x = lo; x <= hi; x++) { const v = lp(x); if (v <= obs + 1e-7) total += Math.exp(v); }
  return Math.min(1, total);
}

export function spearman(x: number[], y: number[]): number | null {
  if (x.length < 5 || new Set(x).size < 2 || new Set(y).size < 2) return null;
  const rx = ranks(x), ry = ranks(y);
  const mx = rx.reduce((a, b) => a + b, 0) / rx.length, my = ry.reduce((a, b) => a + b, 0) / ry.length;
  let sxy = 0, sx = 0, sy = 0;
  for (let i = 0; i < rx.length; i++) {
    sxy += (rx[i]! - mx) * (ry[i]! - my); sx += (rx[i]! - mx) ** 2; sy += (ry[i]! - my) ** 2;
  }
  return sx && sy ? sxy / (Math.sqrt(sx) * Math.sqrt(sy)) : null;
}

export const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;

export function sd(xs: number[]): number {
  if (xs.length < 2) return 0;
  const m = mean(xs);
  return Math.sqrt(xs.reduce((a, x) => a + (x - m) ** 2, 0) / (xs.length - 1));
}
