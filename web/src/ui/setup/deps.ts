/**
 * What F8 Setup needs from the rest of the app, and where the integrator
 * registers it (src/app/index.ts). Kept apart from index.tsx so registering
 * does not load the screen.
 */
import type { DriveStore, PortInfo, TransportPicker } from '../../core/types';
import type { AlarmSource } from './AlarmsTab';
import type { CydClient } from './cyd/client';
import type { Confirm } from './cyd/CydTab';

export interface PlatformFacts { platform: string; serial: string; files: string; notes: string[] }

/** Connect / disconnect the ECU link (the integrator's AppController). */
export interface ConnectionControl {
  /** Poll the ECU through this port (a PortInfo from the picker). */
  connectPort(port: PortInfo): Promise<void>;
  /** The built-in simulator (shown as SIMULATOR). */
  connectSim(): Promise<void>;
  /** The CYD reads the ECU on its own K-line cable and streams it over its USB-C
   *  (the browser's port chooser when no CYD port is granted yet). */
  connectCyd?(): Promise<void>;
  /** Which source is active: the KKL cable, through the CYD, the simulator, '' = none. */
  source?(): 'kkl' | 'cyd' | 'sim' | '';
  disconnect(): Promise<void>;
  /** What is connected now: '' when nothing. */
  current(): string;
}

export interface SetupDeps {
  alarms: AlarmSource | null;
  cyd: CydClient | null;
  store: DriveStore | null;
  picker: TransportPicker | null;
  /** The address map in use and how to replace it. */
  mapText: string;
  saveMap: ((text: string) => Promise<void>) | null;
  /** Platform facts for About and Cable (src/platform capabilities()). */
  platform: () => PlatformFacts;
  versions: Record<string, string>;
  confirm: Confirm;
  /** Connect buttons on the Cable tab (null: no link module in this build). */
  connection: ConnectionControl | null;
}

let registered: Partial<SetupDeps> = {};
let version = 0;
const listeners = new Set<() => void>();
export function registerSetupDeps(deps: Partial<SetupDeps>): void {
  registered = { ...registered, ...deps };
  version++;
  for (const f of listeners) f();   // a Setup screen already open picks them up (start-up finishes after the first draw)
}

export function registeredSetupDeps(): Partial<SetupDeps> {
  return registered;
}

/** For useSyncExternalStore: re-read the deps when they are (re)registered. */
export function subscribeSetupDeps(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
export function setupDepsVersion(): number {
  return version;
}
