/**
 * What F7 Bytes needs from the rest of the app, as thin interfaces, with a
 * standalone default (the demo RAM and the shipped map) so the screen works
 * before the session is wired. The integrator passes the real ones:
 *
 *   link     MemLink over agent #1's poller (submit read_mem / raw)
 *   live     pause / resume the live session for the poke test
 *   mapText  the address map in use (TOML), saveMap writes it back
 */

import { createContext, useContext } from 'react';
import { DEFAULT_MAP_TEXT } from '../../core/addressmap/default-map';
import { DemoMemLink, type MemLink, type MemoryScanner } from './model/scanner';
import { localNotesStore, type NotesStore } from './model/unknown';
import type { LiveControl } from './poke/runner';

export interface BytesDeps {
  link: MemLink | null;
  /** True when `link` is the built-in demo RAM (the poke test's dry run can drive it). */
  demo: boolean;
  live: LiveControl | null;
  mapText: string;
  saveMap: ((text: string) => Promise<void>) | null;
  notes: NotesStore;
  clock: () => number;
  /** A scanner kept by the caller, so the sweep history survives leaving F7. */
  scanner?: MemoryScanner;
}

/** The shipped hondalog/data/addressmap.toml (agent #2's copy). */
export const SHIPPED_MAP: string = DEFAULT_MAP_TEXT;

export function defaultBytesDeps(): BytesDeps {
  return {
    link: new DemoMemLink(),
    demo: true,
    live: null,
    mapText: SHIPPED_MAP,
    saveMap: null,
    notes: localNotesStore,
    clock: () => performance.now() / 1000,
  };
}

/** Deps the integrator registers once (link, live control, map store); the
 *  router renders screens without props, so this is how they arrive. Kept
 *  here (not in index.tsx) so registering does not load the screen. */
let registered: Partial<BytesDeps> = {};
export function registerBytesDeps(deps: Partial<BytesDeps>): void {
  registered = { ...registered, ...deps };
}
export function registeredBytesDeps(): Partial<BytesDeps> {
  return registered;
}

export const BytesDepsContext = createContext<BytesDeps | null>(null);
export const useBytesDeps = (): BytesDeps => {
  const d = useContext(BytesDepsContext);
  if (!d) throw new Error('BytesDepsContext missing');
  return d;
};
