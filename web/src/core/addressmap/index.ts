/**
 * The address map: TOML parsing, variants, validation, decoding, the read
 * planner and the version hash (byte-identical to Python's).
 *
 *     import { loads, loadDefault } from '@core/addressmap';
 *     const map = loads(text, 'P2P');      // throws MapError
 *     map.versionHash                       // '8f3b481ce1dd'
 *     map.plan()                            // cost-model block plan for the poller
 *     map.decode(mem)                       // { rpm: 812, ... }
 */

import { AddressMap, loads } from './addressmap';
import { DEFAULT_MAP_TEXT } from './default-map';

export * from './addressmap';
export * from './plan';
export * from './groups';
export { DEFAULT_FRAMING, PROTOCOL_KEYS, REPLY_OVERHEAD, REQUEST_BYTES, parseHex, toHex, withOverrides } from './framing';
export type { MapFraming } from './framing';
export { DEFAULT_MAP_TEXT } from './default-map';
export * as status from './mapping/status';
export * as find from './mapping/find';

/** Path label used in messages for the built-in map. */
export const DEFAULT_MAP_PATH = 'hondalog/data/addressmap.toml';

/** The shipped default map (optionally another of its ECU variants). */
export function loadDefault(variant?: string | null): AddressMap {
  return loads(DEFAULT_MAP_TEXT, variant, DEFAULT_MAP_PATH);
}
