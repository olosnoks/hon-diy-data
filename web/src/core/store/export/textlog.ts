/**
 * Text logs (port of hondalog/export/textlog.py): CSV and MegaLogViewer
 * ``.msl`` writers, and one tolerant reader for hondalog 0.1.0 CSV, MSL and
 * plain CSV from other tools.
 */

import { type Column, type LogEvent, StreamInfo, column, emptyStreamData, searchsorted, sortByTime } from '../model';
import { pyFixed, pyFloat, pyG } from '../pyfmt';
import { Log, type Table } from '../reader';
import { APP_FORMAT } from '../version';
import { fill } from './mlg';

export const TIME_NAMES = ['time_s', 'time', 't', 'timestamp', 'time (s)', 'time(s)', 'seconds'];
const SKIP_COLUMNS = ['wall_time'];
const MARKER_COLUMN = 'marker';

function number(value: number, decimals: number | null = null): string {
  if (value !== value) return '';
  if (decimals !== null) return pyFixed(value, decimals);
  return Math.abs(value) < 1e6 ? pyG(value, 6) : pyFixed(value, 0);
}

/** Row index -> marker text: each marker lands on the first row at or after it. */
function markerRows(t: Float64Array, markers: [number, string][]): Map<number, string> {
  const out = new Map<number, string>();
  if (!t.length) return out;
  for (const [when, text] of markers) {
    const row = Math.min(searchsorted(t, when, 'left'), t.length - 1);
    out.set(row, out.has(row) ? `${out.get(row)} | ${text}` : text);
  }
  return out;
}

/** ``str(value)`` the way Python prints header values. */
export function pyStr(value: unknown): string {
  if (value === null || value === undefined) return 'None';
  if (typeof value === 'boolean') return value ? 'True' : 'False';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : pyFloat(value);
  if (typeof value === 'string') return value;
  const repr = (v: unknown): string => {
    if (typeof v === 'string') return `'${v.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
    if (Array.isArray(v)) return '[' + v.map(repr).join(', ') + ']';
    if (v && typeof v === 'object') return '{' + Object.entries(v).map(([k, x]) => `${repr(k)}: ${repr(x)}`).join(', ') + '}';
    return pyStr(v);
  };
  return repr(value);
}

function csvField(s: string): string {
  return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

/** CSV in the hondalog layout: ``#`` header lines, then ``time_s``, the channels and a ``marker`` column. */
export function writeCsv(table: Table, markers: [number, string][] = [], meta: Record<string, unknown> = {}, columns: Record<string, Column> = {}): string {
  const marks = markerRows(table.t, markers);
  const names = table.names;
  const decimals = names.map((n) => (!(n in columns) ? null : columns[n]!.kind === 'flag' ? 0 : Math.max(columns[n]!.decimals, 0) + 2));
  let out = `# ${APP_FORMAT}\n`;
  for (const [key, value] of Object.entries(meta)) {
    const text = pyStr(value).replace(/\n/g, ' ');
    if (text.length <= 400) out += `# ${key}: ${text}\n`;
  }
  if (!('units' in meta)) out += `# units: ${names.map((n) => `${n}=${table.units[n] || '-'}`).join(' ')}\n`;
  const rows: string[] = [['time_s', ...names, MARKER_COLUMN].map(csvField).join(',')];
  const cols = names.map((n) => table.columns[n]!);
  for (let i = 0; i < table.t.length; i++) {
    rows.push([pyFixed(table.t[i]!, 4), ...cols.map((c, k) => number(c[i]!, decimals[k]!)), marks.get(i) ?? ''].map(csvField).join(','));
  }
  return out + rows.map((r) => r + '\r\n').join('');
}

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function ctime(unix: number): string {
  const d = new Date(unix * 1000);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${DAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())} ${d.getFullYear()}`;
}

function ascii(s: string): string {
  return s.replace(/[^\x00-\x7f]/g, '?');
}

/** A MegaLogViewer text log: title lines, names, units, tab-separated rows (gaps filled). */
export function writeMsl(table: Table, markers: [number, string][] = [], opts: { title?: string; fill?: 'hold' | 'zero' | 'keep'; started?: number | null } = {}): string {
  const names = table.names;
  const marks = markerRows(table.t, markers);
  const t0 = table.t.length ? table.t[0]! : 0;
  const how = opts.fill ?? 'hold';
  const cols = names.map((n) => fill(table.columns[n]!, how === 'keep' ? 'zero' : how));
  const when = ctime(opts.started ? opts.started : Date.now() / 1000);
  let out = `"${APP_FORMAT}${opts.title ? ': ' + opts.title : ''}"\n`;
  out += `"Capture Date: ${when}"\n`;
  out += ['Time', ...names].join('\t') + '\n';
  out += ['s', ...names.map((n) => (table.units[n] || '').replace(/°/g, 'deg'))].join('\t') + '\n';
  let count = 0;
  for (let i = 0; i < table.t.length; i++) {
    if (marks.has(i)) {
      count++;
      out += `MARK ${String(count).padStart(3, '0')} - ${marks.get(i)}\n`;
    }
    out += [pyFixed(table.t[i]! - t0, 3), ...cols.map((c) => number(c[i]!))].join('\t') + '\n';
  }
  return ascii(out);
}

// ---- reading -------------------------------------------------------------------

function toFloat(cell: string): number {
  const s = cell.trim();
  if (!s) return NaN;
  const low = s.toLowerCase();
  if (low === 'nan') return NaN;
  if (low === 'inf' || low === '+inf' || low === 'infinity') return Infinity;
  if (low === '-inf' || low === '-infinity') return -Infinity;
  if (!/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(s.replace(/_/g, ''))) return NaN;
  return Number(s.replace(/_/g, ''));
}

const isNumber = (cell: string) => {
  const s = cell.trim().toLowerCase();
  return toFloat(cell) === toFloat(cell) || s === 'nan';
};

/** A minimal ``csv.reader`` (quotes, doubled quotes, any one-character delimiter). */
export function csvRows(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  let i = 0;
  let any = false;
  while (i < text.length) {
    const ch = text[i]!;
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') { cell += '"'; i += 2; continue; }
        quoted = false;
        i++;
        continue;
      }
      cell += ch;
      i++;
      continue;
    }
    if (ch === '"' && cell === '') { quoted = true; any = true; i++; continue; }
    if (ch === delimiter) { row.push(cell); cell = ''; any = true; i++; continue; }
    if (ch === '\r' || ch === '\n') {
      if (any || cell !== '') row.push(cell);
      rows.push(row);
      row = [];
      cell = '';
      any = false;
      if (ch === '\r' && text[i + 1] === '\n') i++;
      i++;
      continue;
    }
    cell += ch;
    any = true;
    i++;
  }
  if (any || cell !== '') {
    row.push(cell);
    rows.push(row);
  }
  return rows;
}

function safeName(name: string, taken: Set<string>): string {
  let n = name.trim() || 'column';
  if (n === 't' || n === 'raw') n += '_';
  const base = n;
  let k = 2;
  while (taken.has(n)) n = `${base}_${k++}`;
  taken.add(n);
  return n;
}

/** Import a CSV or MSL text as a log with one stream. */
export function parseTextLog(text: string, path: string | null = null): Log {
  const meta: Record<string, unknown> = {};
  const body: string[] = [];
  let marks: [number, string][] = [];
  let header: string | null = null;
  for (const line of text.split(/\r\n|\r|\n/)) {
    const stripped = line.trim();
    if (!stripped) continue;
    if (stripped.startsWith('#')) {
      const rest = stripped.slice(1);
      const at = rest.indexOf(':');
      if (at >= 0) meta[rest.slice(0, at).trim()] = rest.slice(at + 1).trim();
      else if (!('format' in meta)) meta.format = rest.trim();
    } else if (header === null && stripped.startsWith('"') && !stripped.includes('\t') && !stripped.replace(/^"+|"+$/g, '').includes(',')) {
      if (!('title' in meta)) meta.title = stripped.replace(/^"+|"+$/g, '');
    } else if (header === null && stripped.startsWith('"') && stripped.endsWith('"') && (stripped.match(/"/g) ?? []).length === 2) {
      if (!('title' in meta)) meta.title = stripped.replace(/^"+|"+$/g, '');
    } else if (header === null) {
      header = line;
    } else if (stripped.toUpperCase().startsWith('MARK')) {
      marks.push([body.length, stripped.replace(/^MARK\s*\d*\s*-?\s*/i, '') || stripped]);
    } else {
      body.push(line);
    }
  }
  if (header === null) throw new Error(`${path ?? 'text log'}: no header row`);
  const delimiter = header.includes('\t') ? '\t' : (header.split(';').length > header.split(',').length ? ';' : ',');
  const names = csvRows(header, delimiter)[0] ?? [];
  let rows = csvRows(body.join('\n'), delimiter).filter((r) => r.length);
  let unitsRow: string[] | null = null;
  if (rows.length && !rows[0]!.some((c) => c.trim() && isNumber(c))) {
    unitsRow = rows[0]!;
    rows = rows.slice(1);
    marks = marks.map(([n, t]) => [Math.max(0, n - 1), t]);
  }
  const width = names.length;
  rows = rows.map((r) => [...r.slice(0, width), ...Array(Math.max(0, width - r.length)).fill('')]);
  const lower = names.map((n) => n.trim().toLowerCase());
  const timeName = TIME_NAMES.find((n) => lower.includes(n));
  const timeI = timeName !== undefined ? lower.indexOf(timeName) : null;
  const markerI = lower.includes(MARKER_COLUMN) ? lower.indexOf(MARKER_COLUMN) : null;
  const cell = (r: number, c: number) => rows[r]![c] ?? '';
  let t: Float64Array;
  if (timeI !== null) t = Float64Array.from(rows, (_, r) => toFloat(cell(r, timeI)));
  else {
    t = Float64Array.from(rows, (_, r) => r);
    meta.time_axis = 'row number (the file has no time column)';
  }
  const keep: number[] = [];
  t.forEach((x, i) => { if (x === x) keep.push(i); });
  const units: Record<string, string> = {};
  for (const pair of String(meta.units ?? '').split(/\s+/).filter(Boolean)) {
    const at = pair.indexOf('=');
    if (at >= 0 && pair.slice(at + 1) !== '-') units[pair.slice(0, at)] = pair.slice(at + 1);
  }
  const taken = new Set<string>();
  const columns: Column[] = [];
  const data: Float64Array[] = [];
  names.forEach((name, i) => {
    if (i === timeI || i === markerI || SKIP_COLUMNS.includes(lower[i]!)) return;
    const values = Float64Array.from(keep, (r) => toFloat(cell(r, i)));
    if (rows.length && values.every((v) => v !== v) && rows.some((_, r) => cell(r, i).trim())) return;   // a text column
    const clean = safeName(name, taken);
    const unit = units[name.trim()] ?? (unitsRow && i < unitsRow.length ? unitsRow[i]!.trim() : '');
    const flag = lower[i] === 'link';
    columns.push(column(clean, { dtype: 'f8', units: unit, kind: flag ? 'flag' : 'value', decimals: flag ? 0 : 2 }));
    data.push(values);
  });
  const info = new StreamInfo(delimiter !== '\t' ? 'csv' : 'msl', columns, 'import');
  let out = emptyStreamData(info, keep.length);
  out.t.set(Float64Array.from(keep, (i) => t[i]!));
  columns.forEach((c, k) => out.columns[c.name]!.set(data[k]!));
  out = sortByTime(out);
  const events: LogEvent[] = [];
  if (markerI !== null && rows.length) {
    rows.forEach((_, r) => {
      const when = t[r]!;
      const text = cell(r, markerI);
      if (text && when === when) {
        for (const part of text.split(' | ')) events.push({ t: when, kind: part.startsWith('STALL') ? 'trigger' : 'marker', text: part, data: {} });
      }
    });
  }
  for (const [seen, text_] of marks) {
    if (keep.length) events.push({ t: t[keep[Math.min(seen, keep.length - 1)]!]!, kind: 'marker', text: text_, data: {} });
  }
  if (path !== null) meta.imported_from = path;
  return new Log(meta, [out], events);
}
