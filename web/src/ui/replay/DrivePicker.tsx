/**
 * The drive picker (port of hondalog/ui/drivepicker.py): every drive, newest
 * first, each followed by its captures (stalls, dropouts). Type to search
 * names, kinds and reasons; ↑/↓ choose, Enter or a double click opens, Esc
 * closes. "Delete…" (or Del) removes the chosen drive with everything in it,
 * or a capture's entry, after a question (twice for a drive marked keep).
 * "Keep" marks a drive so it is never pruned. "Import file…" reads a
 * hondalog.db from the Python app or another device, or an old CSV, MSL or
 * MLG log (a new drive).
 */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import type { DriveInfo } from '../../core/types';
import { FONTS, type Theme } from '../theme';
import { clock, day, pickerLength } from './fmt';
import { drivesChanged, type DriveSource } from './source';

/** What the import button takes (drivepicker.py IMPORT_FILTER, without the CYD's .bbx). */
export const IMPORT_ACCEPT = '.csv,.msl,.mlg,.txt,.db,.sqlite,.sqlite3,application/x-sqlite3,text/csv';

const isDatabaseFile = (name: string) => /\.(db|sqlite3?)$/i.test(name);

/** The second question for a drive marked keep (drivepicker.py delete_current). */
export function keepQuestion(name: string): string {
  return `${name} is marked keep.\n\nYou marked this drive so that it would not be deleted. Delete it anyway?`;
}

export interface PickerRow {
  info: DriveInfo;
  nested: boolean;
  captures: number;
  stalls: number;
  /** lower-case text the search matches against */
  text: string;
}

/** Drives newest first, each followed by the captures taken inside it. */
export function pickerRows(drives: DriveInfo[]): PickerRow[] {
  const whole = drives.filter((d) => d.kind === 'drive').sort((a, b) => b.startedUnix - a.startedUnix);
  const caps = drives.filter((d) => d.kind !== 'drive').sort((a, b) => a.startedUnix - b.startedUnix);
  const used = new Set<DriveInfo>();
  const rows: PickerRow[] = [];
  const text = (d: DriveInfo) => [d.name, d.kind, d.reason ?? '', d.status, day(d.startedUnix)].join(' ').toLowerCase();
  for (const d of whole) {
    const inside = caps.filter((c) => !used.has(c) && c.startedUnix >= d.startedUnix - 1 && c.startedUnix <= d.startedUnix + d.durationS + 1);
    inside.forEach((c) => used.add(c));
    rows.push({ info: d, nested: false, captures: inside.length, stalls: inside.filter((c) => c.kind === 'stall').length,
      text: text(d) + ' ' + inside.map(text).join(' ') });
    for (const c of inside) rows.push({ info: c, nested: true, captures: 0, stalls: 0, text: text(c) });
  }
  for (const c of caps) if (!used.has(c)) rows.push({ info: c, nested: false, captures: 0, stalls: 0, text: text(c) });
  return rows;
}

/** What the delete question says: exactly what goes (drivepicker.py delete_question). */
export function deleteQuestion(row: PickerRow): string {
  const d = row.info;
  if (d.kind !== 'drive') {
    return `Delete the ${d.kind} capture ${d.name}?\n\nOnly the capture's entry goes. The drive it was taken from, and all its data, stay.`;
  }
  const parts = [`${day(d.startedUnix)} ${clock(d.startedUnix)}`, pickerLength(d.durationS)];
  if (row.captures) {
    parts.push(`${row.captures} capture${row.captures !== 1 ? 's' : ''}` +
      (row.stalls ? `, ${row.stalls} of them stall${row.stalls !== 1 ? 's' : ''}` : ''));
  }
  let text = `Delete ${d.name}?\n\n${parts.filter(Boolean).join(' · ')}\n\nEverything recorded in this drive is removed from the database` +
    (row.captures ? ', its captures included' : '') + '. This cannot be undone.';
  if (d.reason) text += `\n\nNotes: ${d.reason.slice(0, 120)}`;
  return text;
}

export function matches(row: PickerRow, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return words.every((w) => row.text.includes(w));
}

interface Props {
  source: DriveSource;
  theme: Theme;
  title: string;
  onOpen(name: string): void;
  onClose(): void;
  /** a drive not to offer (the one already open, when picking a second) */
  exclude?: string;
}

export function DrivePicker({ source, theme: t, title, onOpen, onClose, exclude }: Props) {
  const [all, setAll] = useState<DriveInfo[] | null>(null);
  const [query, setQuery] = useState('');
  const [sel, setSel] = useState(0);
  const [message, setMessage] = useState('');
  const [asking, setAsking] = useState<PickerRow | null>(null);
  const [askingKeep, setAskingKeep] = useState(false);
  const [selectName, setSelectName] = useState<string | null>(null);
  const search = useRef<HTMLInputElement>(null);
  const file = useRef<HTMLInputElement>(null);

  const refresh = () => source.listDrives().then(setAll, (e: unknown) => { setAll([]); setMessage(`Cannot list drives: ${String(e)}`); });
  useEffect(() => { void refresh(); search.current?.focus(); }, [source]);   // eslint-disable-line react-hooks/exhaustive-deps

  const rows = useMemo(() => pickerRows((all ?? []).filter((d) => d.name !== exclude)).filter((r) => matches(r, query)), [all, query, exclude]);
  const current = rows[Math.min(sel, rows.length - 1)] ?? null;
  const canDelete = !!source.deleteDrive && !!current && current.info.status !== 'recording';
  const canKeep = !!source.setKeep && !!current && current.info.kind === 'drive';
  const canImport = !!source.importDb || !!source.importFile;

  // after an import: choose the new drive
  useEffect(() => {
    if (!selectName) return;
    const i = rows.findIndex((r) => r.info.name === selectName);
    if (i >= 0) { setSel(i); setSelectName(null); }
  }, [rows, selectName]);

  const closeAsk = () => { setAsking(null); setAskingKeep(false); };

  const doDelete = async (row: PickerRow) => {
    const keep = row.info.kind === 'drive' && !!row.info.keep;
    if (keep && !askingKeep) { setAskingKeep(true); return; }      // a drive marked keep: ask twice
    closeAsk();
    try {
      if (keep) await source.deleteDrive!(row.info.name, { allowKeep: true });
      else await source.deleteDrive!(row.info.name);
      setMessage(`Deleted ${row.info.name}.`);
      drivesChanged();
      await refresh();
    } catch (e) {
      setMessage(`Cannot delete ${row.info.name}: ${String(e)}`);
    }
  };

  const toggleKeep = async (row: PickerRow) => {
    const keep = !row.info.keep;
    try {
      await source.setKeep!(row.info.name, keep);
      setMessage(keep ? `${row.info.name} is marked keep: it is never pruned, and deleting it asks twice.` : `${row.info.name} is no longer marked keep.`);
      drivesChanged();
      await refresh();
    } catch (e) {
      setMessage(`Cannot change keep on ${row.info.name}: ${String(e)}`);
    }
  };

  const onImport = async (f: File | undefined) => {
    if (!f) return;
    try {
      const bytes = new Uint8Array(await f.arrayBuffer());
      if (isDatabaseFile(f.name) || !source.importFile) {
        if (!source.importDb) throw new Error('this list takes no database files');
        const n = await source.importDb(bytes);
        setMessage(`Imported ${n} drive${n !== 1 ? 's' : ''} from ${f.name}.`);
        setSel(0);
      } else {
        const name = await source.importFile(f.name, bytes);
        setMessage(`Imported ${f.name} as ${name}.`);
        setQuery('');
        setSelectName(name);
      }
      drivesChanged();
      await refresh();
    } catch (e) {
      setMessage(`Cannot import ${f.name}: ${String(e)}`);
    } finally {
      if (file.current) file.current.value = '';
    }
  };

  const onKey = (e: KeyboardEvent) => {
    e.stopPropagation();          // the screen's keys (← → [ ] …) stay out of the dialog
    if (asking) {
      if (e.key === 'Escape') { closeAsk(); e.preventDefault(); }
      return;
    }
    if (e.key === 'Escape') { onClose(); e.preventDefault(); }
    else if (e.key === 'ArrowDown') { setSel((s) => Math.min(rows.length - 1, s + 1)); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { setSel((s) => Math.max(0, s - 1)); e.preventDefault(); }
    else if (e.key === 'Enter' && current) { onOpen(current.info.name); e.preventDefault(); }
    else if (e.key === 'Delete' && e.target !== search.current && canDelete) { setAsking(current); e.preventDefault(); }
  };

  const btn = (primary = false) => ({
    fontFamily: FONTS.sans, fontSize: 14, fontWeight: 700, padding: '8px 18px', cursor: 'pointer',
    border: `2px solid ${primary ? t.accent : t.rule}`, background: primary ? t.accent : t.bg, color: primary ? t.onAccent : t.fg,
  });

  let empty = message;
  if (all && !rows.length && !message) {
    empty = all.length ? 'Nothing matches.' : 'No drives recorded yet. Record one (Ctrl+R on the Drive screen), or import a hondalog.db or a CSV, MSL or MLG log.';
  }

  return (
    <div role="dialog" aria-modal="true" aria-label={title} onKeyDown={onKey}
      style={{ position: 'absolute', inset: 0, zIndex: 1000, background: t.bg + 'E6', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ width: 'min(860px, 96%)', height: 'min(520px, 94%)', background: t.bg, border: `2px solid ${t.rule}`, padding: 16,
        display: 'flex', flexDirection: 'column', gap: 10, boxSizing: 'border-box', fontFamily: FONTS.sans, color: t.fg }}>
        <div style={{ fontSize: 15, fontWeight: 800, letterSpacing: '0.04em' }}>{title}</div>
        <input ref={search} value={query} placeholder="Search drives, notes, markers" aria-label="Search drives"
          onChange={(e) => { setQuery(e.target.value); setSel(0); }}
          style={{ fontFamily: FONTS.sans, fontSize: 15, fontWeight: 500, padding: '6px 10px', background: t.bg, color: t.fg, border: `2px solid ${t.accent}`, outline: 'none' }} />
        <div role="listbox" aria-label="Drives" style={{ flexGrow: 1, minHeight: 0, overflowY: 'auto', border: `2px solid ${t.rule}` }}>
          <div style={{ display: 'flex', position: 'sticky', top: 0, background: t.sunk, borderBottom: `2px solid ${t.rule}`, fontSize: 13, fontWeight: 700, padding: '4px 8px' }}>
            <span style={{ width: 300 }}>Drive</span><span style={{ width: 180 }}>Started</span><span style={{ width: 80 }}>Length</span><span>Notes</span>
          </div>
          {rows.map((r, i) => {
            const on = r === current;
            const d = r.info;
            const flags = d.kind === 'drive'
              ? [...(r.stalls ? [`${r.stalls} stall${r.stalls !== 1 ? 's' : ''}`] : []), ...(d.status !== 'ok' ? [d.status] : []),
                ...(d.keep ? ['keep'] : []), ...(d.reason ? [d.reason] : [])]
              : [d.reason ?? ''];
            return (
              <div key={d.name} role="option" aria-selected={on} data-name={d.name}
                onClick={() => setSel(i)} onDoubleClick={() => onOpen(d.name)}
                style={{ display: 'flex', alignItems: 'center', padding: '5px 8px', fontSize: 14, fontWeight: 500, cursor: 'pointer',
                  background: on ? t.accent : i % 2 ? t.sunk : t.bg, color: on ? t.onAccent : t.fg }}>
                <span style={{ width: 300, paddingLeft: r.nested ? 24 : 0, fontFamily: FONTS.mono, fontSize: 13,
                  color: on ? t.onAccent : d.kind === 'stall' ? t.alarm : undefined, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
                <span style={{ width: 180 }}>{day(d.startedUnix)} {clock(d.startedUnix)}</span>
                <span style={{ width: 80 }}>{pickerLength(d.durationS)}</span>
                <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{flags.filter(Boolean).join(' · ')}</span>
              </div>
            );
          })}
        </div>
        <div aria-live="polite" style={{ fontSize: 14, fontWeight: 500, minHeight: 20, color: t.fg2 }}>{all === null ? 'Loading…' : empty}</div>
        {asking ? (
          <div role="alertdialog" aria-label={askingKeep ? 'Marked keep' : 'Delete'} style={{ border: `2px solid ${t.alarm}`, padding: 12, display: 'flex', flexDirection: 'column', gap: 10, overflowY: 'auto' }}>
            <div style={{ whiteSpace: 'pre-wrap', fontSize: 14 }}>{askingKeep ? keepQuestion(asking.info.name) : deleteQuestion(asking)}</div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button type="button" style={btn()} onClick={closeAsk} autoFocus>Cancel</button>
              <button type="button" style={{ ...btn(), borderColor: t.alarm, background: t.alarm, color: t.onAlarm }} onClick={() => void doDelete(asking)}>Delete</button>
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {canImport && (
              <>
                <button type="button" style={btn()} onClick={() => file.current?.click()}
                  title="A hondalog.db from the desktop app or another device, or an old CSV, MSL or MLG log">Import file…</button>
                <input ref={file} type="file" accept={source.importFile ? IMPORT_ACCEPT : '.db,.sqlite,application/x-sqlite3'} hidden aria-label="Import file"
                  onChange={(e) => void onImport(e.target.files?.[0])} />
              </>
            )}
            {source.deleteDrive && <button type="button" style={btn()} disabled={!canDelete} onClick={() => current && setAsking(current)}>Delete…</button>}
            {source.setKeep && (
              <button type="button" style={btn(!!(canKeep && current?.info.keep))} disabled={!canKeep} aria-pressed={!!(canKeep && current?.info.keep)}
                title="Keep this drive: it is never pruned, and deleting it asks twice"
                onClick={() => current && void toggleKeep(current)}>Keep</button>
            )}
            <div style={{ flexGrow: 1 }} />
            <button type="button" style={btn()} onClick={onClose}>Cancel</button>
            <button type="button" style={btn(true)} disabled={!current} onClick={() => current && onOpen(current.info.name)}>Open</button>
          </div>
        )}
      </div>
    </div>
  );
}
