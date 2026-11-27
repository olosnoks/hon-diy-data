/**
 * What Replay and Events need to know about a channel: label, unit, decimals,
 * group (the same groups as F1 Live). Taken from the drive's address map when
 * it has one, else from this table (the names of hondalog/data/addressmap.toml),
 * else made up from the name.
 */
import type { LoadedDrive } from '../../core/types';

export interface ChannelMeta {
  name: string;
  label: string;
  units: string;
  decimals: number;
  group: GroupKey;
  flag?: boolean;
  /** nominal range: lanes pad around the data, flags draw 0..1 */
  min?: number;
  max?: number;
}

export type GroupKey = 'engine' | 'idle' | 'fuel' | 'ignition' | 'electrical' | 'switches' | 'cyd' | 'calc' | 'other';

export const GROUPS: { key: GroupKey; title: string }[] = [
  { key: 'engine', title: 'ENGINE' },
  { key: 'idle', title: 'IDLE CONTROL' },
  { key: 'fuel', title: 'FUEL' },
  { key: 'ignition', title: 'IGNITION' },
  { key: 'electrical', title: 'ELECTRICAL' },
  { key: 'switches', title: 'SWITCHES' },
  { key: 'cyd', title: 'CYD · BLACK BOX' },
  { key: 'calc', title: 'CALCULATED' },
  { key: 'other', title: 'OTHER' },
];

const m = (name: string, label: string, units: string, decimals: number, group: GroupKey, extra: Partial<ChannelMeta> = {}): ChannelMeta =>
  ({ name, label, units, decimals, group, ...extra });

const KNOWN: ChannelMeta[] = [
  m('rpm', 'RPM', 'rpm', 0, 'engine', { min: 0, max: 7200 }),
  m('vss', 'Speed', 'km/h', 0, 'engine', { min: 0, max: 180 }),
  m('map', 'MAP', 'kPa', 0, 'engine', { min: 15, max: 105 }),
  m('tps', 'Throttle', '%', 0, 'engine', { min: 0, max: 100 }),
  m('calc_load', 'Load', '%', 0, 'engine', { min: 0, max: 100 }),
  m('load', 'Load', '%', 0, 'engine', { min: 0, max: 100 }),
  m('ect', 'Coolant', '°C', 0, 'engine', { min: -20, max: 120 }),
  m('iat', 'Intake air', '°C', 0, 'engine', { min: -20, max: 80 }),
  m('baro', 'Baro', 'kPa', 0, 'engine'),
  m('idle_target', 'Idle target', 'rpm', 0, 'idle', { min: 0, max: 1500 }),
  m('iac_cmd', 'IAC cmd', '', 0, 'idle'),
  m('iacv', 'Idle valve', '%', 0, 'idle', { min: 0, max: 100 }),
  m('iac_learn', 'IAC learn', '', 0, 'idle'),
  m('stft', 'Short trim', '%', 1, 'fuel'),
  m('ltft', 'Long trim', '%', 1, 'fuel'),
  m('o2', 'O2 front', 'V', 2, 'fuel', { min: 0, max: 1 }),
  m('o2_post', 'O2 rear', 'V', 2, 'fuel', { min: 0, max: 1 }),
  m('inj', 'Injector', 'ms', 1, 'fuel'),
  m('fuel_flow', 'Fuel flow', 'L/h', 1, 'fuel'),
  m('closed_loop', 'Fuel system', '', 0, 'fuel', { flag: true }),
  m('ign', 'Timing', '°', 1, 'ignition'),
  m('knock', 'Knock', 'V', 2, 'ignition'),
  m('vtec', 'VTEC', '', 0, 'ignition', { flag: true }),
  m('batt', 'Battery', 'V', 1, 'electrical', { min: 10, max: 15.5 }),
  m('alt_fr', 'Alt field', '%', 0, 'electrical'),
  m('eld', 'Elec load', 'A', 0, 'electrical'),
  m('brake', 'Brake', '', 0, 'switches', { flag: true }),
  m('psp', 'Steering', '', 0, 'switches', { flag: true }),
  m('ac_sw', 'A/C', '', 0, 'switches', { flag: true }),
  m('ac_clutch', 'A/C clutch', '', 0, 'switches', { flag: true }),
  m('fuel_pump', 'Pump', '', 0, 'switches', { flag: true }),
  m('fan', 'Fan', '', 0, 'switches', { flag: true }),
  m('starter', 'Starter', '', 0, 'switches', { flag: true }),
  m('mil', 'MIL', '', 0, 'switches', { flag: true }),
  m('gps_speed', 'GPS speed', 'km/h', 0, 'cyd', { min: 0, max: 180 }),
  m('gps_heading', 'Heading', '°', 0, 'cyd', { min: 0, max: 360 }),
  // calculated (calc.ts)
  m('power', 'Power now', 'hp', 0, 'calc', { min: 0, max: 130 }),
  m('torque', 'Torque now', 'N·m', 0, 'calc', { min: 0, max: 160 }),
  m('gear', 'Gear', '', 0, 'calc', { min: 0, max: 5 }),
  m('accel_g', 'Accel', 'g', 2, 'calc', { min: -0.6, max: 0.6 }),
  m('lat_g', 'Lateral g', 'g', 2, 'calc', { min: -0.6, max: 0.6 }),
  m('grade', 'Grade (est.)', '%', 1, 'calc', { min: -10, max: 10 }),
  m('fuel_lph', 'Fuel flow', 'L/h', 1, 'calc', { min: 0, max: 30 }),
  m('rpm_err', 'RPM − target', 'rpm', 0, 'calc'),
];

const BY_NAME = new Map(KNOWN.map((c) => [c.name, c]));
const MAP_GROUPS: Record<string, GroupKey> = {
  engine: 'engine', idle: 'idle', fuel: 'fuel', ignition: 'ignition', electrical: 'electrical',
  switches: 'switches', cyd: 'cyd',
};

/** Meta for one channel of a drive (map first, then the table, then the name). */
export function channelMeta(name: string, drive?: LoadedDrive | null): ChannelMeta {
  const known = BY_NAME.get(name);
  const spec = drive?.map?.channels.find((c) => c.name === name);
  if (spec) {
    return {
      name, label: spec.label || known?.label || name, units: spec.units, decimals: spec.decimals,
      group: (spec.group && MAP_GROUPS[spec.group]) || known?.group || 'other',
      flag: spec.kind === 'flag', min: spec.min, max: spec.max,
    };
  }
  const math = drive?.map?.math.find((c) => c.name === name);
  if (math) return { name, label: math.label, units: math.units, decimals: math.decimals, group: known?.group ?? 'other', min: math.min, max: math.max };
  return known ?? { name, label: name.replace(/_/g, ' '), units: '', decimals: 2, group: 'other' };
}

const thousands = (s: string) => s.replace(/\B(?=(\d{3})+(?!\d))/g, ',');

/** A number as the screens show it: thousands separated, U+2212 minus. */
export function num(v: number, decimals = 0, signed = false): string {
  if (!Number.isFinite(v)) return '—';
  const fixed = Math.abs(v).toFixed(decimals);
  const [int, frac] = fixed.split('.');
  const body = thousands(int ?? '0') + (frac ? '.' + frac : '');
  const zero = Number(fixed) === 0;
  if (v < 0 && !zero) return '−' + body;
  return signed && !zero ? '+' + body : body;
}

/** "2,214", "51 kPa", "on", "+4.7 %". */
export function valueText(meta: ChannelMeta, v: number): string {
  if (!Number.isFinite(v)) return '—';
  if (meta.name === 'gear') return v >= 1 ? ordinal(Math.round(v)) : v === 0 ? 'N' : '—';
  if (meta.name === 'closed_loop') return v >= 0.5 ? 'closed' : 'open';
  if (meta.flag) return v >= 0.5 ? 'on' : 'off';
  if (meta.name === 'gps_heading') return compass(v);
  const signed = meta.name === 'stft' || meta.name === 'ltft' || meta.name === 'grade' || meta.name === 'accel_g' || meta.name === 'rpm_err';
  const text = num(v, meta.decimals, signed);
  if (!meta.units || meta.units === 'rpm' || meta.units === meta.label) return text;
  return meta.units === '°' ? text + meta.units : `${text} ${meta.units}`;
}

export function ordinal(n: number): string {
  return n === 1 ? '1st' : n === 2 ? '2nd' : n === 3 ? '3rd' : `${n}th`;
}

export function compass(deg: number): string {
  const names = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  return names[Math.round((((deg % 360) + 360) % 360) / 45) % 8]!;
}
