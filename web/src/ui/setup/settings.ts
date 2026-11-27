/**
 * Cable and port settings of F8 Setup (theme and units live in the shared
 * session, src/core/session.ts). Kept in localStorage; the link module reads
 * them through `cableSettings()` / `useCable` when it connects.
 */

import { create } from 'zustand';

export type Protocol = 'auto' | 'honda' | 'obd2';
export interface CableSettings {
  /** PortInfo.id of the KKL cable to the ECU ('' = ask / first FTDI). */
  ecuPort: string;
  /** PortInfo.id of the CYD's USB-C. */
  cydPort: string;
  /** 0 = what the address map says ([protocol] baud: 10400 on the P2P). */
  baud: 0 | 9600 | 10400;
  protocol: Protocol;
  /** Start the built-in simulator when no cable is picked. */
  simWhenNoCable: boolean;
}

const KEY = 'hondalog.cable';
export const DEFAULT_CABLE: CableSettings = { ecuPort: '', cydPort: '', baud: 0, protocol: 'auto', simWhenNoCable: true };

function load(): CableSettings {
  try {
    const raw = JSON.parse(globalThis.localStorage?.getItem(KEY) ?? '{}') as Partial<CableSettings>;
    return { ...DEFAULT_CABLE, ...raw };
  } catch {
    return { ...DEFAULT_CABLE };
  }
}

interface CableStore extends CableSettings { set(patch: Partial<CableSettings>): void; reset(): void }

export const useCable = create<CableStore>()((set, get) => ({
  ...load(),
  set: (patch) => {
    set(patch);
    const { set: _s, reset: _r, ...data } = { ...get(), ...patch };
    try { globalThis.localStorage?.setItem(KEY, JSON.stringify(data)); } catch { /* no storage */ }
  },
  reset: () => get().set({ ...DEFAULT_CABLE }),
}));

export function cableSettings(): CableSettings {
  const { set: _s, reset: _r, ...data } = useCable.getState();
  return data;
}
