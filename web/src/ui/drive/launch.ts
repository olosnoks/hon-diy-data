/**
 * Launch tracking for the F2 launch-margin bar. A launch window opens when
 * the car leaves a standstill, or when the rpm sags 80 below the idle target
 * at a standstill (the clutch biting before the car rolls); it closes at
 * 15 km/h, after 6 s, or once the rpm is back at target. The lowest rpm in
 * the window is the launch low.
 *
 * Thresholds from docs/analysis/2026-11-26/drive-idle-control.md: every
 * near-stall went below 600 rpm; 400 rpm is taken as the stall line (an
 * assumption made there, kept here and said so on screen).
 */

export const STALL_LINE = 400;
export const DANGER = 600;

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
