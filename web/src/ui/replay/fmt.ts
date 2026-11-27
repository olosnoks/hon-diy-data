/** Time formatting for Replay and Events (local wall clock, U+2212 minus). */

const pad = (n: number, w = 2) => String(Math.trunc(n)).padStart(w, '0');

/** "23:12:41" or, with decimals, "23:12:41.0" (local time of a unix second). */
export function clock(unix: number, decimals = 0): string {
  if (!Number.isFinite(unix)) return '--:--:--';
  const d = new Date(Math.floor(unix) * 1000);
  const base = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  if (!decimals) return base;
  const frac = unix - Math.floor(unix);
  return base + '.' + pad(Math.min(Math.floor(frac * 10 ** decimals), 10 ** decimals - 1), decimals);
}

/** "2026-11-26" (local). */
export function day(unix: number): string {
  const d = new Date(unix * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** "+1.25", "−0.40", "0.00" (seconds and other differences). */
export function signed(v: number, decimals = 1): string {
  if (!Number.isFinite(v)) return '—';
  const text = Math.abs(v).toFixed(decimals);
  if (Number(text) === 0) return text;
  return (v < 0 ? '−' : '+') + text;
}

/** "35:48" for a length in seconds; "1:02:03" past an hour. */
export function length(seconds: number): string {
  if (!(seconds > 0)) return '';
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}:${pad(m)}:${pad(r)}` : `${m}:${pad(r)}`;
}

/** The drive picker's length column (drivepicker.py _length): "45 s", "36 min". */
export function pickerLength(seconds: number): string {
  if (seconds <= 0) return '';
  return seconds < 120 ? `${seconds.toFixed(0)} s` : `${(seconds / 60).toFixed(0)} min`;
}
