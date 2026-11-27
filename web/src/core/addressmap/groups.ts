/**
 * Which group a channel shows under on F1 Live. Not part of the TOML (the
 * Python app would refuse an unknown channel key), so it is decided here by
 * name: the table first, then a few name patterns, then the channel's kind.
 */

export const GROUPS = ['engine', 'idle', 'fuel', 'ignition', 'electrical', 'switches', 'cyd', 'raw'] as const;
export type ChannelGroup = (typeof GROUPS)[number];

export const GROUP_LABELS: Readonly<Record<ChannelGroup, string>> = Object.freeze({
  engine: 'Engine',
  idle: 'Idle control',
  fuel: 'Fuel',
  ignition: 'Ignition',
  electrical: 'Electrical',
  switches: 'Switches',
  cyd: 'CYD',
  raw: 'Raw bytes',
});

/** Known channel names (shipped map, the owner's map, OBD2 and math channels). */
export const GROUP_OF: Readonly<Record<string, ChannelGroup>> = Object.freeze({
  // engine
  rpm: 'engine', vss: 'engine', map: 'engine', baro: 'engine', tps: 'engine',
  ect: 'engine', iat: 'engine', load_ecu: 'engine', calc_load: 'engine', load: 'engine',
  rpm_rate: 'engine', rpm_smooth: 'engine', map_avg: 'engine',
  // idle
  idle_target: 'idle', iac_cmd: 'idle', iacv: 'idle', iac_learn: 'idle',
  // fuel
  stft: 'fuel', ltft: 'fuel', o2: 'fuel', o2_2: 'fuel', o2_post: 'fuel', inj: 'fuel',
  purge_duty: 'fuel', fuel_status: 'fuel', closed_loop: 'fuel', o2_heater: 'fuel',
  inj_duty: 'fuel', fuel_flow: 'fuel', total_trim: 'fuel', afr_est: 'fuel',
  // ignition
  ign: 'ignition', ign_limit: 'ignition', knock: 'ignition',
  // electrical
  batt: 'electrical', alt_fr: 'electrical', eld: 'electrical', batt_min_5s: 'electrical',
  // switches and outputs
  ac_sw: 'switches', ac_clutch: 'switches', brake: 'switches', starter: 'switches',
  psp: 'switches', mil: 'switches', fan: 'switches', fuel_pump: 'switches',
  vtec: 'switches', vtec_psw: 'switches',
});

/**
 * The Live group for a channel name. `kind` breaks the tie for names the
 * table does not know: a flag is a switch, anything else is raw.
 */
export function groupOf(name: string, kind?: string): ChannelGroup {
  if (Object.prototype.hasOwnProperty.call(GROUP_OF, name)) return GROUP_OF[name]!;
  if (/^raw_[0-9a-f]{2}$/i.test(name) || /^b[0-9a-f]{2}$/i.test(name)) return 'raw';
  if (/^(cyd|gps|imu|hh)_/.test(name)) return 'cyd';
  if (/^(iac|idle)/.test(name)) return 'idle';
  if (/^(o2|stft|ltft|inj|fuel|purge|afr)/.test(name)) return 'fuel';
  if (/^(ign|knock|spark)/.test(name)) return 'ignition';
  if (/^(batt|alt|eld|volt)/.test(name)) return 'electrical';
  if (kind === 'flag') return 'switches';
  return 'raw';
}
