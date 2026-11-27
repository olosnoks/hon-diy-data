/**
 * The owner's verdicts and notes on events. With a drive store they live in
 * the drive's own database, as the desktop app keeps them (events rows of
 * kind 'event_note', key "kind@t" to 0.1 s: core/store/eventnotes.ts), so
 * they travel with hondalog.db between the apps. The demo drives (no store)
 * keep them on the device (localStorage).
 *
 * The maps here are by the screen's event id (drive@kind@t); `load` fills them
 * for the drives on screen, `setVerdict` / `setNote` write through.
 */
import { create } from 'zustand';
import { eventKey } from '../../core/store/eventnotes';
import type { DriveStore, EventNote, LoadedDrive } from '../../core/types';
import { valueAt } from '../../core/analysis/cursors';

/** Where notes lived before they moved into the database (and still do for the demo drives). */
export const LOCAL_KEY = 'hondalog.events.notes';

/** What notes need from the drive source. */
export type NotesSource = Partial<Pick<DriveStore, 'loadEventNotes' | 'saveEventNote'>>;

/** The event fields a note is filed under. */
export interface NoteTarget { id: string; drive: string; kind: string; t: number }

interface Saved { verdicts: Record<string, string>; notes: Record<string, string> }
interface NotesState extends Saved {
  source: NotesSource | null;
  /** the last write that failed ('' = fine) */
  error: string;
  /** Fill the maps for these events from the source's database (or the device for the demo). */
  load(source: NotesSource | null, events: NoteTarget[]): Promise<void>;
  setVerdict(e: NoteTarget, verdict: string | null): void;
  setNote(e: NoteTarget, note: string): void;
}

function readLocal(): Saved {
  try {
    const raw = globalThis.localStorage?.getItem(LOCAL_KEY);
    if (raw) {
      const v = JSON.parse(raw) as Partial<Saved>;
      return { verdicts: { ...(v.verdicts ?? {}) }, notes: { ...(v.notes ?? {}) } };
    }
  } catch { /* unreadable: start empty */ }
  return { verdicts: {}, notes: {} };
}

function writeLocal(s: Saved): void {
  try {
    if (!Object.keys(s.verdicts).length && !Object.keys(s.notes).length) globalThis.localStorage?.removeItem(LOCAL_KEY);
    else globalThis.localStorage?.setItem(LOCAL_KEY, JSON.stringify({ verdicts: s.verdicts, notes: s.notes }));
  } catch { /* blocked */ }
}

const inDb = (s: NotesSource | null): s is Required<NotesSource> => !!s?.loadEventNotes && !!s.saveEventNote;

/**
 * The web once filed hard accelerations, hard braking and hard corners under
 * one kind, 'harsh'; the desktop's kinds are harsh_accel / harsh_brake (and
 * corner). Notes kept under 'harsh' are found again by the events now there.
 */
export const OLD_HARSH = 'harsh';
const HARSH_NOW = new Set(['harsh_accel', 'harsh_brake', 'corner']);
const oldLocalId = (e: NoteTarget) => `${e.drive}@${OLD_HARSH}@${e.t.toFixed(2)}`;

/** Which kind an old 'harsh' note at t is now: by the sign of the speed change around t. */
export function harshKindAt(drive: LoadedDrive, t: number): 'harsh_accel' | 'harsh_brake' | null {
  const vss = drive.series.vss;
  if (!vss || !vss.t.length) return null;
  const dv = valueAt(vss, t + 0.75) - valueAt(vss, t - 0.75);
  if (!Number.isFinite(dv) || dv === 0) return null;
  return dv > 0 ? 'harsh_accel' : 'harsh_brake';
}

export const useEventNotes = create<NotesState>()((set, get) => ({
  verdicts: {},
  notes: {},
  source: null,
  error: '',
  async load(source, events) {
    if (!inDb(source)) {
      const saved = readLocal();
      let moved = false;
      for (const e of events) {
        if (!HARSH_NOW.has(e.kind)) continue;
        const old = oldLocalId(e);
        for (const m of [saved.verdicts, saved.notes]) {
          if (m[old] !== undefined) { if (m[e.id] === undefined) m[e.id] = m[old]!; delete m[old]; moved = true; }
        }
      }
      if (moved) writeLocal(saved);
      set({ ...saved, source });
      return;
    }
    const verdicts: Record<string, string> = {};
    const notes: Record<string, string> = {};
    const stored = new Map<string, Record<string, EventNote>>();
    for (const d of new Set(events.map((e) => e.drive))) {
      try { stored.set(d, await source.loadEventNotes(d)); } catch { stored.set(d, {}); }
    }
    for (const e of events) {
      const rows = stored.get(e.drive);
      let row = rows?.[eventKey(e.kind, e.t)];
      const oldKey = eventKey(OLD_HARSH, e.t);
      if (!row && HARSH_NOW.has(e.kind) && rows?.[oldKey]) {
        // a note filed under the old 'harsh' kind: move it to this event's kind
        row = rows[oldKey]!;
        delete rows[oldKey];
        const change = { note: row.note, verdict: row.verdict };
        try {
          await source.saveEventNote(e.drive, eventKey(e.kind, e.t), { ...change, kind: e.kind, t: e.t });
          await source.saveEventNote(e.drive, oldKey, { note: '', verdict: '' });
        } catch { /* stays under the old key; shown anyway */ }
      }
      if (!row) continue;
      if (row.verdict) verdicts[e.id] = row.verdict;
      if (row.note) notes[e.id] = row.note;
    }
    set({ verdicts, notes, source });
  },
  setVerdict(e, verdict) {
    const verdicts = { ...get().verdicts };
    if (verdict) verdicts[e.id] = verdict; else delete verdicts[e.id];
    set({ verdicts });
    persist(get, set, e, { verdict: verdict ?? '' });
  },
  setNote(e, note) {
    const notes = { ...get().notes };
    const text = note.trim();
    if (text) notes[e.id] = text; else delete notes[e.id];
    set({ notes });
    persist(get, set, e, { note: text });
  },
}));

function persist(get: () => NotesState, set: (p: Partial<NotesState>) => void, e: NoteTarget, change: { note?: string; verdict?: string }): void {
  const source = get().source;
  if (!inDb(source)) {
    writeLocal(get());
    return;
  }
  source.saveEventNote(e.drive, eventKey(e.kind, e.t), { ...change, kind: e.kind, t: e.t })
    .then(() => set({ error: '' }), (err: unknown) => set({ error: `Cannot save the note: ${String(err)}` }));
}

/** Old event ids: `${drive}@${kind}@${t.toFixed(2)}` (drive names hold no '@'). */
function parseOldId(id: string): { drive: string; kind: string; t: number } | null {
  const a = id.lastIndexOf('@');
  const b = a > 0 ? id.lastIndexOf('@', a - 1) : -1;
  if (b <= 0) return null;
  const t = Number(id.slice(a + 1));
  if (!Number.isFinite(t)) return null;
  return { drive: id.slice(0, b), kind: id.slice(b + 1, a), t };
}

/**
 * Move the notes and verdicts kept on the device (before they went into the
 * database) into the drive store, once: every entry whose drive is in the
 * store is written there and removed from the device; entries for drives the
 * store does not have stay where they are. Notes of the old kind 'harsh' are
 * filed as harsh_accel / harsh_brake by `harshKind` (the sign of the speed
 * change, harshKindAt); without it, or when it cannot tell, they stay on the
 * device. Returns how many moved.
 */
export async function migrateLocalNotes(store: Required<NotesSource>, storage: Storage | undefined = globalThis.localStorage,
  harshKind?: (drive: string, t: number) => Promise<string | null>): Promise<number> {
  let raw: string | null = null;
  try { raw = storage?.getItem(LOCAL_KEY) ?? null; } catch { raw = null; }
  if (!raw || !storage) return 0;
  let saved: Saved;
  try {
    const v = JSON.parse(raw) as Partial<Saved>;
    saved = { verdicts: { ...(v.verdicts ?? {}) }, notes: { ...(v.notes ?? {}) } };
  } catch {
    return 0;
  }
  const left: Saved = { verdicts: {}, notes: {} };
  let moved = 0;
  for (const id of new Set([...Object.keys(saved.verdicts), ...Object.keys(saved.notes)])) {
    const at = parseOldId(id);
    const change: { note?: string; verdict?: string } = {};
    if (saved.notes[id]) change.note = saved.notes[id];
    if (saved.verdicts[id]) change.verdict = saved.verdicts[id];
    try {
      if (!at) throw new Error('unknown id');
      // old 'harsh' notes go to harsh_accel / harsh_brake by the sign of the speed change
      const kind = at.kind === OLD_HARSH ? await harshKind?.(at.drive, at.t) : at.kind;
      if (!kind) throw new Error('cannot tell accel from brake');
      await store.saveEventNote(at.drive, eventKey(kind, at.t), { ...change, kind, t: at.t });
      moved++;
    } catch {
      if (saved.notes[id]) left.notes[id] = saved.notes[id]!;
      if (saved.verdicts[id]) left.verdicts[id] = saved.verdicts[id]!;
    }
  }
  try {
    if (!Object.keys(left.notes).length && !Object.keys(left.verdicts).length) storage.removeItem(LOCAL_KEY);
    else storage.setItem(LOCAL_KEY, JSON.stringify(left));
  } catch { /* blocked */ }
  return moved;
}
