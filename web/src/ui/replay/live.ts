/**
 * Replay's Live mode (L, as the desktop's go_live): the lanes follow the live
 * session instead of a file, the last 40 s with the cursor at "now". Built
 * from the session store's 2 Hz history (the last 60 s of every channel), as
 * a drive whose time is seconds from now (newest sample at 0).
 */
import type { LoadedDrive } from '../../core/types';
import { HISTORY_DT, type History } from '../../core/session';

export const LIVE_NAME = 'live';
export const LIVE_WINDOW_S = 40;

const EMPTY_GPS = () => ({ t: new Float64Array(0), lat: new Float64Array(0), lon: new Float64Array(0), speedKmh: new Float64Array(0), heading: new Float64Array(0) });

/** The live history as a drive: t = seconds from the newest sample (≤ 0). */
export function liveDrive(history: Record<string, History>, nowUnix = Date.now() / 1000): LoadedDrive {
  let newest = -Infinity;
  for (const h of Object.values(history)) newest = Math.max(newest, h.lastT);
  const series: LoadedDrive['series'] = {};
  let rows = 0;
  if (Number.isFinite(newest)) {
    for (const [name, h] of Object.entries(history)) {
      const t: number[] = [], v: number[] = [];
      const n = h.v.length;
      h.v.forEach((x, i) => {
        if (x === null || !Number.isFinite(x)) return;
        t.push(h.lastT - newest - (n - 1 - i) * HISTORY_DT);
        v.push(x);
      });
      if (t.length) { series[name] = { t: Float64Array.from(t), v: Float64Array.from(v) }; rows = Math.max(rows, t.length); }
    }
  }
  return {
    info: { id: 0, name: LIVE_NAME, kind: 'drive', startedUnix: nowUnix, durationS: 0, rows, status: 'recording', mapVersion: '', ecuId: '' },
    map: null, series, reads: [], events: [], gps: EMPTY_GPS(),
  };
}
