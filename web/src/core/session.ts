/**
 * The shared session store (zustand). Owner: agent #8 (app shell). Every
 * screen reads it; the core modules (link, recorder, CYD, alarms) write it
 * through the actions below. New fields go through the integrator.
 *
 * Kept deliberately small: what the always-visible chrome and the live
 * screens need. Heavy data (drives, reads, maps) stays in its own module.
 *
 *   import { useSession } from '../../core/session';
 *   const rpm = useSession((s) => s.channels.rpm);        // React
 *   useSession.getState().mark();                          // anywhere
 *   useSession.subscribe((s) => s.marks, ...)              // listeners
 */
import { create } from 'zustand';
import type { ChannelState, CydHello, DriveInfo, LinkState } from './types';

// ---------------------------------------------------------------- screens

export type ScreenId = 'live' | 'drive' | 'events' | 'replay' | 'power' | 'gps' | 'bytes' | 'setup';

/** F1-F8 in order (design canvas "Navigation"). */
export const SCREENS: readonly { id: ScreenId; key: string; name: string }[] = [
  { id: 'live', key: 'F1', name: 'Live' },
  { id: 'drive', key: 'F2', name: 'Drive' },
  { id: 'events', key: 'F3', name: 'Events' },
  { id: 'replay', key: 'F4', name: 'Replay' },
  { id: 'power', key: 'F5', name: 'Power' },
  { id: 'gps', key: 'F6', name: 'GPS' },
  { id: 'bytes', key: 'F7', name: 'Bytes' },
  { id: 'setup', key: 'F8', name: 'Setup' },
];

export type ThemeName = 'dracula' | 'darker';
export type UnitSystem = 'metric' | 'imperial';

// ---------------------------------------------------------------- status

/** The ECU link as the status bar shows it. */
export interface EcuStatus {
  state: LinkState;
  rateHz: number;
  errors: number;
  /** Data comes from the built-in simulator. */
  simulated: boolean;
  /** OBD2 (ISO 9141-2 PIDs) instead of Honda reads. */
  obd2: boolean;
  /** Short reason for connecting / error ("5-baud init", "port lost"). */
  detail?: string;
}

/** The CYD handheld, which is the black box (GPS, own SD log, stall catcher). */
export interface CydStatus {
  state: 'off' | 'connecting' | 'ok' | 'silent' | 'lost';
  /** relay = the CYD forwards ECU frames to us; standalone = it polls itself. */
  mode: 'off' | 'relay' | 'standalone';
  /** Its SD log is recording. */
  sdLogging: boolean;
  /** Its HANDHELD.MAP hash matches our address map (null = unknown). */
  mapMatch: boolean | null;
  mapHash?: string;
  sdFreeGb?: number;
  sdDrive?: number;
  sats?: number;
  fix?: '' | '2D' | '3D';
  hello?: CydHello;
  detail?: string;
}

/** The stall catcher (triggers module). */
export interface StallStatus {
  state: 'off' | 'standby' | 'armed' | 'saving' | 'triggered';
  bufferS: number;
  preS: number;
  postS: number;
  postLeftS: number;
  /** Stalls caught this drive. */
  count: number;
  lastFile: string | null;
}

export interface RecordingState {
  active: boolean;
  /** Date.now() when the recording started, null when stopped. */
  startedUnixMs: number | null;
  /** The drive being written (recorder fills it), null when stopped. */
  drive: DriveInfo | null;
}

/** One mark (Space / MARK button). The recorder subscribes to `marks`. */
export interface Mark {
  unixMs: number;
  /** Optional note typed later. */
  text?: string;
}

/** An alarm that is currently active (alarms module fills these). */
export interface ActiveAlarm {
  name: string;
  /** Channel the alarm is about (its tile turns warn / alarm). */
  channel?: string;
  level: 'warn' | 'alarm';
  message: string;
  acked: boolean;
}

/** Last 60 s of a channel for sparklines, sampled at 2 Hz. */
export interface History {
  lastT: number;
  v: (number | null)[];
}

export const HISTORY_N = 120;
export const HISTORY_DT = 0.5;

// ---------------------------------------------------------------- state

export interface SessionData {
  /** Selected screen (F1-F8). */
  screen: ScreenId;
  theme: ThemeName;
  units: UnitSystem;
  /** The open panel or drawer ("raw", "connect", ...); Esc closes it. */
  panel: string | null;
  ecu: EcuStatus;
  cyd: CydStatus;
  stall: StallStatus;
  /** Newest value of every channel by name (ECU, math, CYD). */
  channels: Record<string, ChannelState>;
  history: Record<string, History>;
  /** Latest byte at every polled address (0x00-0xFF), for the raw drawer. */
  raw: Record<number, number>;
  alarms: ActiveAlarm[];
  recording: RecordingState;
  marks: Mark[];
  /** The demo data source is feeding the store (no hardware). */
  demo: boolean;
}

export interface SessionActions {
  setScreen(screen: ScreenId): void;
  setTheme(theme: ThemeName): void;
  toggleTheme(): void;
  setUnits(units: UnitSystem): void;
  openPanel(panel: string): void;
  /** Toggle a panel: open it, or close it if it is the open one. */
  togglePanel(panel: string): void;
  closePanel(): void;
  setEcu(patch: Partial<EcuStatus>): void;
  setCyd(patch: Partial<CydStatus>): void;
  setStall(patch: Partial<StallStatus>): void;
  /** Merge new channel states (by spec.name); feeds the 2 Hz history too. */
  updateChannels(states: ChannelState[]): void;
  /** Forget every channel (map reloaded, disconnected). */
  clearChannels(): void;
  /** Merge raw bytes: addr -> byte. */
  setRaw(bytes: Record<number, number>): void;
  setAlarms(alarms: ActiveAlarm[]): void;
  acknowledgeAlarms(): void;
  startRecording(drive?: DriveInfo | null): void;
  stopRecording(): void;
  setDrive(drive: DriveInfo | null): void;
  /** Space / MARK: add a mark (recorder picks it up). Returns the mark count. */
  mark(text?: string): number;
  /** Esc: close the open panel, otherwise acknowledge alarms. */
  escape(): void;
  setDemo(on: boolean): void;
}

export type Session = SessionData & SessionActions;

const PREFS_KEY = 'hondalog.prefs';

function loadPrefs(): Partial<Pick<SessionData, 'theme' | 'units' | 'screen'>> {
  try {
    const raw = globalThis.localStorage?.getItem(PREFS_KEY);
    if (!raw) return {};
    const p = JSON.parse(raw) as Record<string, unknown>;
    const out: Partial<Pick<SessionData, 'theme' | 'units' | 'screen'>> = {};
    if (p.theme === 'dracula' || p.theme === 'darker') out.theme = p.theme;
    if (p.units === 'metric' || p.units === 'imperial') out.units = p.units;
    if (SCREENS.some((s) => s.id === p.screen)) out.screen = p.screen as ScreenId;
    return out;
  } catch {
    return {};
  }
}

function savePrefs(s: SessionData): void {
  try {
    globalThis.localStorage?.setItem(PREFS_KEY, JSON.stringify({ theme: s.theme, units: s.units, screen: s.screen }));
  } catch {
    /* private mode / no storage: preferences just don't persist */
  }
}

/** The state of a fresh session (no link, nothing recorded). */
export function initialSession(): SessionData {
  return {
    screen: 'live',
    theme: 'dracula',
    units: 'metric',
    panel: null,
    ecu: { state: 'stopped', rateHz: 0, errors: 0, simulated: false, obd2: false },
    cyd: { state: 'off', mode: 'off', sdLogging: false, mapMatch: null },
    stall: { state: 'off', bufferS: 0, preS: 20, postS: 5, postLeftS: 0, count: 0, lastFile: null },
    channels: {},
    history: {},
    raw: {},
    alarms: [],
    recording: { active: false, startedUnixMs: null, drive: null },
    marks: [],
    demo: false,
  };
}

export const useSession = create<Session>()((set, get) => ({
  ...initialSession(),
  ...loadPrefs(),

  setScreen: (screen) => set({ screen }),
  setTheme: (theme) => set({ theme }),
  toggleTheme: () => set({ theme: get().theme === 'dracula' ? 'darker' : 'dracula' }),
  setUnits: (units) => set({ units }),
  openPanel: (panel) => set({ panel }),
  togglePanel: (panel) => set({ panel: get().panel === panel ? null : panel }),
  closePanel: () => set({ panel: null }),
  setEcu: (patch) => set({ ecu: { ...get().ecu, ...patch } }),
  setCyd: (patch) => set({ cyd: { ...get().cyd, ...patch } }),
  setStall: (patch) => set({ stall: { ...get().stall, ...patch } }),

  updateChannels: (states) => {
    if (!states.length) return;
    const { channels, history } = get();
    const nextC = { ...channels };
    let nextH = history;
    for (const st of states) {
      const name = st.spec.name;
      nextC[name] = st;
      const h = history[name];
      if (!h || st.t - h.lastT >= HISTORY_DT || st.t < h.lastT) {
        if (nextH === history) nextH = { ...history };
        const v = h && st.t >= h.lastT ? h.v.slice(h.v.length >= HISTORY_N ? 1 : 0) : [];
        v.push(st.fresh ? st.value : null);
        nextH[name] = { lastT: st.t, v };
      }
    }
    set({ channels: nextC, history: nextH });
  },
  clearChannels: () => set({ channels: {}, history: {}, raw: {} }),
  setRaw: (bytes) => set({ raw: { ...get().raw, ...bytes } }),
  setAlarms: (alarms) => set({ alarms }),
  acknowledgeAlarms: () => {
    const { alarms } = get();
    if (alarms.some((a) => !a.acked)) set({ alarms: alarms.map((a) => ({ ...a, acked: true })) });
  },
  startRecording: (drive = null) =>
    set({ recording: { active: true, startedUnixMs: Date.now(), drive }, stall: { ...get().stall, count: 0 } }),
  stopRecording: () => set({ recording: { active: false, startedUnixMs: null, drive: null } }),
  setDrive: (drive) => set({ recording: { ...get().recording, drive } }),
  mark: (text) => {
    const marks = [...get().marks, text ? { unixMs: Date.now(), text } : { unixMs: Date.now() }];
    set({ marks });
    return marks.length;
  },
  escape: () => {
    if (get().panel !== null) set({ panel: null });
    else get().acknowledgeAlarms();
  },
  setDemo: (demo) => set({ demo }),
}));

// Theme, units and the last screen survive a reload.
useSession.subscribe((s, prev) => {
  if (s.theme !== prev.theme || s.units !== prev.units || s.screen !== prev.screen) savePrefs(s);
});

/** Back to a fresh session (tests, disconnect). Keeps nothing. */
export function resetSession(): void {
  useSession.setState(initialSession());
}

/** The alarm level a channel's tile shows: 0 none, 1 warn, 2 alarm. */
export function alarmLevel(s: Pick<SessionData, 'alarms' | 'channels'>, channel: string): 0 | 1 | 2 {
  let level: 0 | 1 | 2 = s.channels[channel]?.alarm ?? 0;
  for (const a of s.alarms) {
    if (a.channel === channel) level = Math.max(level, a.level === 'alarm' ? 2 : 1) as 0 | 1 | 2;
  }
  return level;
}

/** An unacknowledged alarm on this channel (its icon blinks). */
export function alarmUnacked(s: Pick<SessionData, 'alarms'>, channel: string): boolean {
  return s.alarms.some((a) => a.channel === channel && !a.acked);
}
