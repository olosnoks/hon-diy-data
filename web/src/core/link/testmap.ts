/** The default address map's channels (hondalog/data/addressmap.toml, P2P),
 *  enough of an AddressMap for the link tests. Not used by the app. */
import type { AddressMap, ChannelSpec, RateClass } from '../types';

const RAW: [string, number, number, RateClass, string, 'value' | 'flag'][] = [
  ['rpm', 0, 2, 'fast', 'x / 4', 'value'], ['vss', 2, 1, 'medium', 'x', 'value'],
  ['ect', 16, 1, 'slow', 'poly', 'value'], ['iat', 17, 1, 'slow', 'poly', 'value'],
  ['map', 18, 1, 'fast', 'x * 0.716 - 5', 'value'], ['baro', 19, 1, 'slow', 'x * 0.716 - 5', 'value'],
  ['tps', 20, 1, 'fast', 'x / 2.55', 'value'], ['o2', 21, 1, 'medium', 'x / 51.3', 'value'],
  ['batt', 23, 1, 'slow', 'x / 10.45', 'value'], ['alt_fr', 24, 1, 'slow', 'x / 2.55', 'value'],
  ['eld', 25, 1, 'slow', '77.06 - x / 2.5371', 'value'], ['stft', 32, 1, 'medium', '(x / 128 - 1) * 100', 'value'],
  ['ltft', 34, 1, 'medium', '(x / 128 - 1) * 100', 'value'], ['inj', 36, 2, 'fast', 'x / 250', 'value'],
  ['ign', 38, 1, 'fast', '(x - 24) / 4', 'value'], ['ign_limit', 39, 1, 'slow', '(x - 24) / 4', 'value'],
  ['iacv', 40, 1, 'slow', 'x / 2.55', 'value'], ['knock', 60, 1, 'slow', 'x / 51', 'value'],
  ['ac_sw', 8, 1, 'slow', 'bit(x, 1)', 'flag'], ['vtec', 10, 1, 'slow', 'bit(x, 2)', 'flag'],
  ['mil', 11, 1, 'slow', 'bit(x, 5)', 'flag'], ['closed_loop', 15, 1, 'slow', 'bit(x, 0)', 'flag'],
  ['fuel_pump', 11, 1, 'slow', 'bit(x, 0)', 'flag'], ['fan', 12, 1, 'slow', 'bit(x, 1)', 'flag'],
  ['ac_clutch', 11, 1, 'slow', 'bit(x, 1)', 'flag'], ['brake', 8, 1, 'slow', 'bit(x, 3)', 'flag'],
  ['starter', 8, 1, 'slow', 'bit(x, 0)', 'flag'],
];

export function testMap(protocol: AddressMap['protocol'] = { baud: 10400 }): AddressMap {
  const channels: ChannelSpec[] = RAW.map(([name, addr, length, rate, formula, kind]) => ({
    name, label: name, addr, length, formula, units: '', min: 0, max: 255, decimals: 0,
    kind, status: 'verified', rate, endian: 'big',
  }));
  return {
    version: 'test', variant: 'P2P', channels, math: [], alarms: [], constants: {},
    protocol, ecuId: { addr: 0x76, length: 10 }, versionHash: '000000000000', text: '',
  };
}

export const NAMES = RAW.map((r) => r[0]);
