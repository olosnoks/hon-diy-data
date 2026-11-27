/**
 * The owner's verdicts and notes on events, kept on the device
 * (localStorage, like the session prefs). TODO(integrator): move to the drive
 * store's events table (#4) so they travel with hondalog.db.
 */
import { create } from 'zustand';

const KEY = 'hondalog.events.notes';

interface Saved { verdicts: Record<string, string>; notes: Record<string, string> }
interface NotesState extends Saved {
  setVerdict(id: string, verdict: string | null): void;
  setNote(id: string, note: string): void;
}

function load(): Saved {
  try {
    const raw = globalThis.localStorage?.getItem(KEY);
    if (raw) {
      const v = JSON.parse(raw) as Partial<Saved>;
      return { verdicts: { ...(v.verdicts ?? {}) }, notes: { ...(v.notes ?? {}) } };
    }
  } catch { /* unreadable: start empty */ }
  return { verdicts: {}, notes: {} };
}

function save(s: Saved): void {
  try { globalThis.localStorage?.setItem(KEY, JSON.stringify({ verdicts: s.verdicts, notes: s.notes })); } catch { /* blocked */ }
}

export const useEventNotes = create<NotesState>()((set, get) => ({
  ...load(),
  setVerdict(id, verdict) {
    const verdicts = { ...get().verdicts };
    if (verdict) verdicts[id] = verdict; else delete verdicts[id];
    set({ verdicts });
    save(get());
  },
  setNote(id, note) {
    const notes = { ...get().notes };
    if (note.trim()) notes[id] = note.trim(); else delete notes[id];
    set({ notes });
    save(get());
  },
}));
