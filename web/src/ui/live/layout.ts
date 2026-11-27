/**
 * F1 Live layout (design canvas "F1 Live: grouped by system"): six groups,
 * the tiles in each, their one-line notes, and the switches strip. Channels
 * are matched by name; a channel the map does not have shows as "not in
 * address map". Channels whose spec.group names a group but are not listed
 * here are appended to that group, so a new map entry shows up on its own.
 */
import type { SessionData } from '../../core/session';
import { formatBound } from '../shell/format';

export type Ctx = SessionData;

export interface TileDef {
  /** Channel name; for a derived tile (no channel) a unique key. */
  name: string;
  /** Overrides spec.label (also the label when the channel is missing). */
  label?: string;
  note?: string | ((s: Ctx) => string);
  /** Draw the note in the alarm colour (a known problem, not an alarm). */
  noteWarn?: boolean | ((s: Ctx) => boolean);
  /** A derived tile or a value shown as words (gear N). */
  display?: (s: Ctx) => { text: string; unit?: string; verified?: boolean } | null;
}

export interface GroupDef {
  id: string;
  title: string;
  sub: (s: Ctx) => string;
  tiles: TileDef[];
  /** The RPM-vs-idle-target bar with the stall zone. */
  idleBar?: boolean;
}

const v = (s: Ctx, name: string): number | null => {
  const st = s.channels[name];
  return st && st.fresh ? st.value : null;
};

const bound = (s: Ctx, name: string, which: 'min' | 'max'): string => {
  const st = s.channels[name];
  return st ? formatBound(st[which], st.spec, s.units) : '---';
};

export const GEAR_WORD = ['N', '1', '2', '3', '4', '5'];

export const GROUPS: GroupDef[] = [
  {
    id: 'engine', title: 'ENGINE',
    sub: (s) => {
      const rpm = v(s, 'rpm'), vss = v(s, 'vss'), ect = v(s, 'ect');
      if (rpm === null) return 'no data';
      if (rpm < 50) return 'engine off';
      if (vss !== null && vss > 1) return 'driving';
      return `${ect !== null && ect < 60 ? 'cold' : 'warm'} idle, stopped`;
    },
    tiles: [
      { name: 'rpm', note: (s) => `min ${bound(s, 'rpm', 'min')} · max ${bound(s, 'rpm', 'max')}` },
      { name: 'map', note: (s) => { const b = v(s, 'baro'); return b === null ? '' : `baro ${Math.round(b)}`; } },
      { name: 'tps', label: 'Throttle', note: 'opening, closed = 0' },
      { name: 'load', label: 'Load', note: 'ECU calc' },
      { name: 'ect', label: 'Coolant', note: 'reads ~14 °C low?', noteWarn: true },
      { name: 'iat', label: 'Intake air' },
    ],
  },
  {
    id: 'idle', title: 'IDLE CONTROL', sub: () => 'what the ECU asks for vs gets', idleBar: true,
    tiles: [
      { name: 'idle_target', label: 'Idle target', note: 'set by coolant' },
      { name: 'iac_cmd', label: 'IAC command', note: 'moves first' },
      { name: 'iacv', label: 'Idle valve', note: (s) => ((v(s, 'iacv') ?? 99) < 10 ? 'near floor' : ''), noteWarn: true },
      { name: 'iac_learn', label: 'IAC learn', note: (s) => ((v(s, 'ect') ?? 99) < 80 ? 'frozen: coolant low' : '') },
      { name: 'rpm_map_idx', label: 'RPM×MAP idx' },
      { name: 'vss', label: 'Speed', note: (s) => { const g = v(s, 'gear'); return g === null ? '' : `gear ${GEAR_WORD[Math.round(g)] ?? g}`; } },
    ],
  },
  {
    id: 'fuel', title: 'FUEL',
    sub: (s) => { const cl = v(s, 'closed_loop'); return cl === null ? '' : cl >= 0.5 ? 'closed loop' : 'open loop'; },
    tiles: [
      { name: 'stft', label: 'Short trim' },
      {
        name: 'ltft', label: 'Long trim',
        note: (s) => {
          const a = v(s, 'stft'), b = v(s, 'ltft');
          if (a === null || b === null) return '';
          const t = Math.round(a + b);
          return `total ${t > 0 ? '+' : t < 0 ? '−' : ''}${Math.abs(t)} %`;
        },
        noteWarn: (s) => Math.abs((v(s, 'stft') ?? 0) + (v(s, 'ltft') ?? 0)) >= 15,
      },
      { name: 'o2', label: 'O2 front', note: 'switching 0.1–0.9' },
      { name: 'o2_post', label: 'O2 rear', note: 'post-cat' },
      { name: 'inj', label: 'Injector' },
      { name: 'purge', label: 'Purge duty?' },
    ],
  },
  {
    id: 'ignition', title: 'IGNITION', sub: () => 'formula fixed 11-26',
    tiles: [
      { name: 'ign', label: 'Timing', note: 'BTDC' },
      { name: 'vtec', label: 'VTEC', note: (s) => ((s.channels.vtec?.max ?? 0) >= 0.5 ? `seen at ${bound(s, 'rpm', 'max')}` : 'not seen this drive') },
      { name: 'vtec_psw', label: 'VTEC press. sw' },
      { name: 'rpm_idx_3c', label: 'RPM idx 0x3C' },
    ],
  },
  {
    id: 'electrical', title: 'ELECTRICAL',
    sub: (s) => {
      const b = v(s, 'batt');
      if (b === null) return '';
      return b >= 13.2 ? 'charging normal' : b >= 12.4 ? 'charging low' : 'not charging';
    },
    tiles: [
      { name: 'batt', label: 'Battery', note: (s) => `min ${bound(s, 'batt', 'min')} at dips` },
      { name: 'alt_fr', label: 'Alt field' },
      { name: 'eld', label: 'Elec load', note: 'fan +7.5 A' },
    ],
  },
  {
    id: 'cyd', title: 'CYD · BLACK BOX', sub: () => 'GPS, its own SD log and stall catcher',
    tiles: [
      { name: 'gps_speed', label: 'GPS speed', note: (s) => (s.cyd.sats !== undefined ? `${s.cyd.sats} sats · fix ${s.cyd.fix || 'none'}` : '') },
      {
        name: 'gear', label: 'Gear', note: 'from rpm ÷ speed',
        display: (s) => { const g = v(s, 'gear'); return g === null ? null : { text: GEAR_WORD[Math.round(g)] ?? String(Math.round(g)) }; },
      },
      { name: 'trip', label: 'Trip' },
      {
        name: 'cyd_sd', label: 'CYD SD log',
        display: (s) => (s.cyd.state === 'off' ? null : { text: s.cyd.sdLogging ? 'REC' : 'OFF', verified: true }),
        note: (s) => [s.cyd.sdDrive !== undefined ? `drive ${s.cyd.sdDrive}` : '', s.cyd.sdFreeGb !== undefined ? `${s.cyd.sdFreeGb} GB free` : ''].filter(Boolean).join(' · '),
      },
      {
        name: 'cyd_link', label: 'CYD link',
        display: (s) => (s.cyd.state === 'off' ? null : { text: s.cyd.state === 'ok' ? s.cyd.mode.toUpperCase() : s.cyd.state.toUpperCase(), verified: true }),
        note: (s) => (s.cyd.mapHash ? `map #${s.cyd.mapHash} ${s.cyd.mapMatch === false ? '✗ mismatch' : s.cyd.mapMatch ? '✓' : ''}`.trim() : ''),
        noteWarn: (s) => s.cyd.mapMatch === false,
      },
      {
        name: 'cyd_stalls', label: 'CYD stalls',
        display: (s) => (s.cyd.state === 'off' ? null : { text: String(s.stall.count), verified: true }),
        note: (s) => (s.cyd.state === 'off' ? '' : s.stall.state),
      },
    ],
  },
];

/** The switches strip, in the canvas order. */
export const SWITCHES: { name: string; label: string }[] = [
  { name: 'starter', label: 'Starter' },
  { name: 'brake', label: 'Brake' },
  { name: 'psp', label: 'Steering load' },
  { name: 'ac_sw', label: 'A/C switch' },
  { name: 'ac_clutch', label: 'A/C clutch' },
  { name: 'fuel_pump', label: 'Fuel pump' },
  { name: 'closed_loop', label: 'Closed loop' },
  { name: 'o2_heater', label: 'O2 heater?' },
  { name: 'mil', label: 'MIL' },
];

export const MAX_TILES = 9;

/** The group's tiles plus unlisted channels whose spec.group is this group. */
export function tilesFor(group: GroupDef, channels: Ctx['channels']): TileDef[] {
  const listed = new Set(group.tiles.map((t) => t.name));
  const extra = Object.values(channels)
    .filter((st) => 'group' in st.spec && st.spec.group === group.id && !listed.has(st.spec.name))
    .map((st) => ({ name: st.spec.name }));
  return [...group.tiles, ...extra].slice(0, MAX_TILES);
}

export function extraSwitches(channels: Ctx['channels']): { name: string; label: string }[] {
  const listed = new Set(SWITCHES.map((s) => s.name));
  return Object.values(channels)
    .filter((st) => 'group' in st.spec && st.spec.group === 'switches' && !listed.has(st.spec.name))
    .map((st) => ({ name: st.spec.name, label: st.spec.label }));
}
