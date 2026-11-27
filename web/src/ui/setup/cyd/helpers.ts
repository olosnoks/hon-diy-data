/**
 * Pure helpers of the CYD black box tab (hondalog/ui/screens/handheld.py
 * fmt_bytes, fmt_dur, progress_text, status_rows, upload_target) and
 * HANDHELD.CFG as text (hondalog/handheld/cfgtext.py).
 */

import { canRemove, canWrite, splitPath, WRITE_FILES } from '../../../core/cyd/hl_proto';
import { DEFAULT_CFG } from '../../../core/cyd/mapfile';

export { canRemove, canWrite, splitPath, DEFAULT_CFG };

export function fmtBytes(n: number): string {
  if (n < 1024) return `${Math.trunc(n)} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

export function fmtDur(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const p2 = (v: number) => String(v).padStart(2, '0');
  return s < 3600 ? `${Math.floor(s / 60)}:${p2(s % 60)}` : `${Math.floor(s / 3600)}:${p2(Math.floor((s % 3600) / 60))}:${p2(s % 60)}`;
}

/** [percent, 'x.x of y.y MB · elapsed · ~eta left'] for a transfer. */
export function progressText(done: number, total: number, elapsed: number): [number, string] {
  const pct = total ? (100 * done) / total : 100;
  let text = `${(done / 1048576).toFixed(1)} of ${(total / 1048576).toFixed(1)} MB · ${fmtDur(elapsed)}`;
  if (done && done < total && elapsed > 2) text += ` · ~${fmtDur((elapsed * (total - done)) / done)} left`;
  return [pct, text];
}

export type StatusRow = [section: string, label: string, value: string, good: boolean | null];

/** The HL STAT snapshot as rows. The marks (OK / NO / --) carry the meaning, colour only backs them up. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function statusRows(st: Record<string, any>): StatusRow[] {
  const g = (k: string) => (st[k] ?? {}) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  const link = g('link'), ecu = g('ecu'), gps = g('gps'), sd = g('sd'), drive = g('drive'), tm = g('time'), ser = g('serial');
  const fix = !!gps.fix;
  const src = ({ gps: 'GPS', host: 'set from this PC', none: 'not set' } as Record<string, string>)[tm.source ?? 'none'] ?? '?';
  const ms = Number(tm.unix_ms ?? 0);
  const clock = ms ? `${new Date(ms).toISOString().slice(0, 19).replace('T', ' ')} UTC` : 'not known';
  const n = (v: unknown) => Number(v ?? 0);
  return [
    ['ECU link', 'link', `${link.state === 2 ? 'OK' : 'NO'}  ${link.name ?? '?'}`, link.state === 2],
    ['ECU link', 'read rate', `${n(link.hz).toFixed(1)} Hz fast cycle`, null],
    ['ECU link', 'reads / errors', `${n(link.reads)} / ${n(link.errors)}`, null],
    ['ECU link', 'ECU id', ecu.id || 'not read yet', null],
    ['GPS', 'fix', `${fix ? 'OK' : 'NO'}  ${fix ? 'fix' : 'no fix'}, ${n(gps.sats)} satellites`, fix],
    ['GPS', 'position', fix ? `${n(gps.lat).toFixed(5)}, ${n(gps.lon).toFixed(5)}` : '--', null],
    ['GPS', 'speed / course', fix ? `${n(gps.speed_kmh).toFixed(0)} km/h, ${n(gps.course).toFixed(0)} deg` : '--', null],
    ['GPS', 'sentences good / bad', `${n(gps.nmea_good)} / ${n(gps.nmea_bad)}`, null],
    ['SD card', 'card', `${sd.ok ? 'OK' : 'NO'}  ${sd.ok ? 'present' : 'missing'}`, !!sd.ok],
    ['SD card', 'free', sd.ok ? `${n(sd.free_mb)} MB` : '--', null],
    ['Drive', 'recording', drive.recording ? `OK  D${String(n(drive.no)).padStart(6, '0')}, ${fmtBytes(n(drive.bytes))}` : '--  not recording', drive.recording ? true : null],
    ['Drive', 'markers / stalls', `${n(drive.markers)} / ${n(drive.stalls)}`, null],
    ['Drive', 'stall catcher', drive.armed ? 'armed' : 'not armed', null],
    ['Clock', 'time', clock, !!ms],
    ['Clock', 'source', src, tm.source === 'gps'],
    ['Link to this PC', 'speed', `${n(ser.baud)} baud`, null],
    ['Link to this PC', 'frames sent / dropped', `${n(ser.tx_frames)} / ${n(ser.tx_dropped)}`, null],
  ];
}

/** Where a local file goes on the card: [path, why not]. HANDHELD.CFG / HANDHELD.MAP at the
 *  root, anything else into MAP/ (the only folder the handheld lets you write to). */
export function uploadTarget(localName: string): [string | null, string] {
  const base = localName.split(/[\\/]/).pop() ?? localName;
  const name = base.toUpperCase();
  if (WRITE_FILES.includes(name)) return [`/${name}`, ''];
  const path = `/MAP/${name}`;
  if (splitPath(path) === null) return [null, `${base}: the card wants 8.3 names (letters, digits, _ and . up to 12 characters)`];
  return [path, ''];
}

// ---- HANDHELD.CFG text ----------------------------------------------------------

const LINE = /^(\s*#?\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^#]*?)\s*(?:#\s*(.*))?$/;

export interface CfgEntry { key: string; value: string; comment: string; enabled: boolean; line: number }

export function cfgParse(text: string): CfgEntry[] {
  const out: CfgEntry[] = [];
  text.split(/\r?\n/).forEach((raw, n) => {
    const m = LINE.exec(raw);
    if (!m) return;
    const lead = m[1]!;
    const enabled = !lead.includes('#');
    if (!enabled && !/^\s*#\s*$/.test(lead)) return;
    out.push({ key: m[2]!, value: m[3]!.trim(), comment: (m[4] ?? '').trim(), enabled, line: n });
  });
  return out;
}

function lines(text: string): string[] {
  const l = text.split(/\r?\n/);
  if (l.length && l[l.length - 1] === '') l.pop();      // splitlines() drops the empty tail
  return l;
}

/** The text with `key` set to `value` (its line kept, with its comment); added at the end if there is none. */
export function cfgSetValue(text: string, key: string, value: string, enable = true): string {
  const ls = lines(text);
  for (const e of cfgParse(text)) {
    if (e.key === key) {
      const comment = e.comment ? `   # ${e.comment}` : '';
      ls[e.line] = `${enable ? '' : '# '}${key} = ${value}${comment}`;
      return ls.join('\n') + '\n';
    }
  }
  ls.push(`${key} = ${value}`);
  return ls.join('\n') + '\n';
}

/** The settings in force (not commented out), last one wins. */
export function cfgValues(text: string): Record<string, string> {
  return Object.fromEntries(cfgParse(text).filter((e) => e.enabled).map((e) => [e.key, e.value]));
}

/** Lines the handheld will skip: a key it does not know, or a line that is not `key = value`. */
export function cfgProblems(text: string, known: Set<string>): string[] {
  const out: string[] = [];
  lines(text).forEach((raw, i) => {
    const s = raw.split('#')[0]!.trim();
    if (!s) return;
    const m = /^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\S/.exec(s);
    if (!m) out.push(`line ${i + 1}: not 'key = value'`);
    else if (!known.has(m[1]!)) out.push(`line ${i + 1}: unknown setting '${m[1]}'`);
  });
  return out;
}

export const KNOWN_KEYS = new Set(cfgParse(DEFAULT_CFG).map((e) => e.key));
