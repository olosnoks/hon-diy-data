/** Shared bits of the diagnostic reports: dates and file names. */

const p2 = (n: number) => String(n).padStart(2, '0');

/** '2026-11-27 14:03:09' (local time), as the Python reports write it. */
export function fmtWhen(d: Date): string {
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`;
}

/** '20261127-140309' for file names. */
export function fileStamp(d: Date): string {
  return `${d.getFullYear()}${p2(d.getMonth() + 1)}${p2(d.getDate())}-${p2(d.getHours())}${p2(d.getMinutes())}${p2(d.getSeconds())}`;
}

/** A report as the UI saves it: the markdown and the raw .hex.log beside it. */
export interface ReportFiles {
  mdName: string;
  md: string;
  hexName: string;
  hex: string;
}

/** Escape a table cell (pipes and newlines). */
export function cell(text: string): string {
  return text.replace(/\|/g, '\\|').replace(/\n/g, ' ');
}
