/**
 * Launch tracking for the F2 launch-margin bar. A launch window opens when
 * the car leaves a standstill, or when the rpm sags 80 below the idle target
 * at a standstill (the clutch biting before the car rolls); it closes at
 * 15 km/h, after 6 s, or once the rpm is back at target. The lowest rpm in
 * the window is the launch low.
 *
 * Thresholds from docs/analysis/2026-11-26/drive-idle-control.md: every
 * near-stall went below 600 rpm.
 *
 * The bar shows rpm − idle target (both apps, PARITY-DECISIONS row 1): a
 * −300…+700 rpm scale, red below −150, yellow from −150 to the target.
 */

export const LAUNCH_LO = -300;
export const LAUNCH_HI = 700;
export const LAUNCH_RED = -150;

/** +124, −12, ±0 with a true minus sign (desktop tiles.py signed_rpm). */
export function signedRpm(v: number): string {
  const text = Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 });
  return (v <= -0.5 ? '−' : v >= 0.5 ? '+' : '±') + text;
}

export interface LaunchState {
  active: boolean;
  t0: number;
  min: number;
  /** Lowest rpm of the last finished launch (0 = it stalled), null = none yet. */
  last: number | null;
  wasStopped: boolean;
  /** Launches seen. */
  count: number;
}

export const LAUNCH0: LaunchState = { active: false, t0: 0, min: 0, last: null, wasStopped: false, count: 0 };

export function stepLaunch(
  s: LaunchState,
  x: { t: number; rpm: number | null; vss: number | null; target: number | null },
): LaunchState {
  if (x.rpm === null || x.vss === null) return s;
  const stopped = x.vss < 0.5;
  const target = x.target ?? 800;
  if (!s.active) {
    const sag = stopped && x.rpm < target - 80;
    if (s.wasStopped && x.rpm > 50 && (!stopped || sag)) {
      return { ...s, active: true, t0: x.t, min: x.rpm, wasStopped: stopped };
    }
    return s.wasStopped === stopped ? s : { ...s, wasStopped: stopped };
  }
  if (x.rpm < 50) return { ...s, active: false, last: 0, count: s.count + 1, wasStopped: stopped };
  const min = Math.min(s.min, x.rpm);
  const dt = x.t - s.t0;
  const done = x.vss >= 15 || dt > 6 || (stopped && dt > 2 && x.rpm >= target - 20);
  if (done) return { ...s, active: false, min, last: min, count: s.count + 1, wasStopped: stopped };
  return { ...s, min, wasStopped: stopped };
}
