/**
 * F3 Events: one list of stalls, dropouts, near-stalls, key-offs, VTEC,
 * harsh driving and marks across the day's drives (design canvas "F3 Events").
 * Filter chips; the selected event gets its verdict, a "why" from the signals
 * around it and a small timeline; Open in Replay (Enter), Show on GPS map,
 * a note and the owner's own verdict. ↑ / ↓ move through the list.
 */
import { useEffect, useMemo, useState } from 'react';
import type { LoadedDrive } from '../../core/types';
import { useTheme } from '../shell/useTheme';
import { FONTS, type Theme } from '../theme';
import { useDriveSource } from '../replay/source';
import { openInReplay, showOnMap } from '../../core/session';
import { clock, day, length } from '../replay/fmt';
import { autoVerdict, eventsOf, KIND_FILTER, KIND_LABEL, KIND_ORDER, VERDICTS, type DriveEvent, type EventKind } from './detect';
import { useEventNotes } from './notes';
import { Timeline } from './Timeline';

function badge(t: Theme, kind: EventKind) {
  const base = { fontSize: 11.5, fontWeight: 800, padding: '3px 7px', width: 96, textAlign: 'center' as const, flexShrink: 0, boxSizing: 'border-box' as const };
  const filled = (bg: string, fg: string) => ({ ...base, background: bg, color: fg, border: `2px solid ${bg}` });
  const outline = (c: string, fg = c) => ({ ...base, border: `2px solid ${c}`, color: fg });
  switch (kind) {
    case 'stall': return filled(t.alarm, t.onAlarm);
    case 'near_stall': return filled(t.warn, t.onWarn);
    case 'vtec': return filled(t.ok, t.bg);
    case 'dropout': return outline(t.accent);
    case 'harsh': return outline(t.unverified);
    case 'mark': return outline(t.fg);
    case 'key_off': return outline(t.line, t.fg2);
  }
}

interface Loaded { drives: LoadedDrive[]; events: DriveEvent[] }

export default function EventsScreen() {
  const t = useTheme();
  const { source, version } = useDriveSource();
  const [days, setDays] = useState<string[]>([]);
  const [dayKey, setDayKey] = useState<string | null>(null);
  const [data, setData] = useState<Loaded | null>(null);
  const [status, setStatus] = useState('Loading…');
  const [filter, setFilter] = useState<EventKind | 'all'>('all');
  const [selId, setSelId] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [verdictMenu, setVerdictMenu] = useState(false);
  const { verdicts, notes, setVerdict, setNote } = useEventNotes();

  // which days have drives; default the newest
  useEffect(() => {
    let live = true;
    source.listDrives().then((all) => {
      if (!live) return;
      const ds = [...new Set(all.filter((d) => d.kind === 'drive').sort((a, b) => b.startedUnix - a.startedUnix).map((d) => day(d.startedUnix)))];
      setDays(ds);
      setDayKey((k) => (k && ds.includes(k) ? k : ds[0] ?? null));
      if (!ds.length) { setData({ drives: [], events: [] }); setStatus('No drives recorded yet.'); }
    }, (e: unknown) => setStatus(`Cannot list drives: ${String(e)}`));
    return () => { live = false; };
  }, [source, version]);

  // that day's drives and their events
  useEffect(() => {
    if (!dayKey) return;
    let live = true;
    setStatus('Loading…');
    (async () => {
      const all = await source.listDrives();
      const names = all.filter((d) => d.kind === 'drive' && day(d.startedUnix) === dayKey).map((d) => d.name);
      const drives: LoadedDrive[] = [];
      for (const n of names) {
        try { drives.push(await source.loadDrive(n)); } catch (e) { console.error('cannot load', n, e); }
      }
      const events = drives.flatMap(eventsOf).sort((a, b) => b.unix - a.unix);
      if (live) { setData({ drives, events }); setStatus(''); }
    })().catch((e: unknown) => live && setStatus(`Cannot load drives: ${String(e)}`));
    return () => { live = false; };
  }, [dayKey, source, version]);

  const events = data?.events ?? [];
  const shown = useMemo(() => (filter === 'all' ? events : events.filter((e) => e.kind === filter)), [events, filter]);
  const sel = shown.find((e) => e.id === selId) ?? shown[0] ?? null;
  const drive = sel ? data?.drives.find((d) => d.info.name === sel.drive) ?? null : null;
  const counts = useMemo(() => {
    const c = {} as Record<EventKind, number>;
    for (const k of KIND_ORDER) c[k] = 0;
    for (const e of events) c[e.kind]++;
    return c;
  }, [events]);

  // keys: ↑ ↓ through the list, Enter opens in Replay
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT')) return;
      if (e.altKey || e.ctrlKey || e.metaKey || !shown.length) return;
      const i = sel ? shown.indexOf(sel) : -1;
      if (e.key === 'ArrowDown') { setSelId(shown[Math.min(shown.length - 1, i + 1)]!.id); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { setSelId(shown[Math.max(0, i - 1)]!.id); e.preventDefault(); }
      else if (e.key === 'Enter' && sel) { openInReplay(sel.drive, sel.t); e.preventDefault(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [shown, sel]);

  const totalS = (data?.drives ?? []).reduce((s, d) => s + d.info.durationS, 0);
  const chip = (on: boolean) => ({
    fontSize: 12, fontWeight: 700, padding: '5px 10px', cursor: 'pointer', fontFamily: FONTS.sans,
    background: on ? t.accent : 'transparent', color: on ? t.onAccent : t.fg2, border: `2px solid ${on ? t.rule : t.line}`,
  });
  const button = (primary = false) => ({
    fontFamily: FONTS.sans, fontSize: 13, fontWeight: 700, minHeight: 36, padding: '0 14px', cursor: 'pointer',
    background: primary ? t.accent : t.bg, color: primary ? t.onAccent : t.fg, border: `2px solid ${primary ? t.accent : t.rule}`,
  });
  const verdict = sel ? verdicts[sel.id] ?? autoVerdict(sel, events) : '';
  const own = sel ? !!verdicts[sel.id] : false;
  const hot = sel && !own && (sel.kind === 'stall' || verdict === 'closest call of the night');

  return (
    <div style={{ flexGrow: 1, minHeight: 0, display: 'flex', background: t.bg, color: t.fg, fontFamily: FONTS.sans }}>
      <section style={{ width: 'clamp(320px, 41vw, 560px)', flexShrink: 0, borderRight: `2px solid ${t.rule}`, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px 0', fontSize: 12.5, fontWeight: 600, color: t.fg2 }}>
          {days.length > 1 ? (
            <select aria-label="Day" value={dayKey ?? ''} onChange={(e) => { setDayKey(e.target.value); setSelId(null); }}
              style={{ background: t.bg, color: t.fg, border: `1px solid ${t.rule}`, fontFamily: FONTS.sans, fontSize: 12.5, fontWeight: 600 }}>
              {days.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          ) : <span>{dayKey}</span>}
          <span data-testid="summary">· {data?.drives.length ?? 0} drive{data?.drives.length === 1 ? '' : 's'} · {Math.round(totalS / 60)} min</span>
        </div>
        <div role="group" aria-label="Filter" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '10px 12px', borderBottom: `1px solid ${t.rule}` }}>
          <button type="button" aria-pressed={filter === 'all'} style={chip(filter === 'all')} onClick={() => setFilter('all')}>All {events.length}</button>
          {KIND_ORDER.map((k) => (
            <button key={k} type="button" aria-pressed={filter === k} style={chip(filter === k)} onClick={() => setFilter(filter === k ? 'all' : k)}>
              {KIND_FILTER[k]} {counts[k]}
            </button>
          ))}
        </div>
        <div role="listbox" aria-label="Events" style={{ flexGrow: 1, minHeight: 0, overflowY: 'auto' }}>
          {!shown.length && <div style={{ padding: 16, color: t.fg2, fontSize: 14 }}>{status || (events.length ? 'None of this kind.' : 'No events in these drives.')}</div>}
          {shown.map((e) => {
            const on = e === sel;
            return (
              <div key={e.id} role="option" aria-selected={on} data-event={e.kind}
                onClick={() => { setSelId(e.id); setEditing(null); setVerdictMenu(false); }} onDoubleClick={() => openInReplay(e.drive, e.t)}
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', borderBottom: `1px solid ${t.line}`, cursor: 'pointer',
                  borderLeft: `5px solid ${on ? t.accent : 'transparent'}`, background: on ? t.sunk : 'transparent' }}>
                <span style={{ fontFamily: FONTS.mono, fontSize: 13, fontWeight: 700, width: 70, flexShrink: 0 }}>{clock(e.unix)}</span>
                <span style={badge(t, e.kind)}>{KIND_LABEL[e.kind]}</span>
                <span style={{ fontSize: 13, color: t.fg2, flex: '1 1 0', minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {e.text}{notes[e.id] ? ` · ✎ ${notes[e.id]}` : ''}
                </span>
              </div>
            );
          })}
        </div>
      </section>

      <section style={{ flex: '1 1 0', minWidth: 0, display: 'flex', flexDirection: 'column', padding: '12px 16px', gap: 10, minHeight: 0 }}>
        {sel && drive ? (
          <>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
              <span style={{ fontFamily: FONTS.mono, fontSize: 20, fontWeight: 700 }}>{clock(sel.unix)}</span>
              <span data-testid="title" style={{ fontSize: 20, fontWeight: 800 }}>{sel.title}</span>
              <span data-testid="verdict" style={{ fontSize: 13, fontWeight: 700, color: hot ? t.alarm : own ? t.accent : t.fg2 }}>{verdict}{own ? ' (yours)' : ''}</span>
              <span style={{ fontSize: 12, color: t.fg2, marginLeft: 'auto' }}>{sel.drive} · {length(drive.info.durationS)}</span>
            </div>
            {sel.why && (
              <div data-testid="why" style={{ background: t.sunk, padding: '10px 12px', fontSize: 14, lineHeight: 1.45 }}>
                <strong>Why:</strong> {sel.why}
              </div>
            )}
            {notes[sel.id] && editing !== sel.id && (
              <div data-testid="note" style={{ borderLeft: `4px solid ${t.accent}`, padding: '4px 10px', fontSize: 14 }}><strong>Note:</strong> {notes[sel.id]}</div>
            )}
            {editing === sel.id && (
              <NoteEditor theme={t} initial={notes[sel.id] ?? ''} onCancel={() => setEditing(null)}
                onSave={(text) => { setNote(sel.id, text); setEditing(null); }} />
            )}
            <figure style={{ margin: 0, flexGrow: 1, minHeight: 120, border: `1px solid ${t.rule}` }}>
              <Timeline drive={drive} event={sel} theme={t} />
            </figure>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', position: 'relative' }}>
              <button type="button" style={button(true)} onClick={() => openInReplay(sel.drive, sel.t)}>Open in Replay (F4)</button>
              <button type="button" style={button()} onClick={() => showOnMap(sel.drive, sel.t)} disabled={!drive.gps.t.length}
                title={drive.gps.t.length ? '' : 'No GPS in this drive'}>Show on GPS map</button>
              <button type="button" style={button()} onClick={() => { setEditing(sel.id); setVerdictMenu(false); }}>{notes[sel.id] ? 'Edit the note' : 'Add a note'}</button>
              <button type="button" style={button()} aria-expanded={verdictMenu} onClick={() => setVerdictMenu((v) => !v)}>Change verdict</button>
              {verdictMenu && (
                <div role="menu" aria-label="Verdict" style={{ position: 'absolute', bottom: 44, right: 0, background: t.panel, border: `2px solid ${t.rule}`, display: 'flex', flexDirection: 'column', minWidth: 200, zIndex: 10 }}>
                  {VERDICTS.map((v) => (
                    <button key={v} type="button" role="menuitemradio" aria-checked={verdicts[sel.id] === v}
                      onClick={() => { setVerdict(sel.id, v); setVerdictMenu(false); }}
                      style={{ textAlign: 'left', padding: '8px 12px', background: verdicts[sel.id] === v ? t.accent : 'transparent', color: verdicts[sel.id] === v ? t.onAccent : t.fg,
                        border: 0, borderBottom: `1px solid ${t.line}`, fontFamily: FONTS.sans, fontSize: 13, fontWeight: 700, cursor: 'pointer' }}>{v}</button>
                  ))}
                  <button type="button" role="menuitemradio" aria-checked={!own} onClick={() => { setVerdict(sel.id, null); setVerdictMenu(false); }}
                    style={{ textAlign: 'left', padding: '8px 12px', background: 'transparent', color: t.fg2, border: 0, fontFamily: FONTS.sans, fontSize: 13, fontWeight: 700, cursor: 'pointer' }}>
                    automatic: {autoVerdict(sel, events)}
                  </button>
                </div>
              )}
            </div>
          </>
        ) : (
          <div style={{ color: t.fg2, fontSize: 15, fontWeight: 600 }}>{status || 'Pick an event on the left.'}</div>
        )}
      </section>
    </div>
  );
}

function NoteEditor({ theme: t, initial, onSave, onCancel }: { theme: Theme; initial: string; onSave(text: string): void; onCancel(): void }) {
  const [text, setText] = useState(initial);
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
      <textarea aria-label="Note" value={text} autoFocus rows={2} onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) onSave(text); if (e.key === 'Escape') { e.stopPropagation(); onCancel(); } }}
        style={{ flexGrow: 1, background: t.bg, color: t.fg, border: `2px solid ${t.accent}`, fontFamily: FONTS.sans, fontSize: 14, padding: 6, resize: 'vertical' }} />
      <button type="button" onClick={() => onSave(text)} style={{ fontFamily: FONTS.sans, fontSize: 13, fontWeight: 700, minHeight: 36, padding: '0 14px', background: t.accent, color: t.onAccent, border: `2px solid ${t.accent}`, cursor: 'pointer' }}>Save</button>
      <button type="button" onClick={onCancel} style={{ fontFamily: FONTS.sans, fontSize: 13, fontWeight: 700, minHeight: 36, padding: '0 14px', background: t.bg, color: t.fg, border: `2px solid ${t.rule}`, cursor: 'pointer' }}>Cancel</button>
    </div>
  );
}
