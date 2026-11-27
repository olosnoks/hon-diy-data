/**
 * The recorder's view of an address map (#4's RecorderMap) built from #2's
 * AddressMap. Each channel's formula is #3's compiled expression
 * (compileExpr(formula, {names: ['x'], arrays: ['b']}), done once by the
 * AddressMap's Channel), so the bytes in the drive decode exactly as live.
 */
import type { AddressMap } from '../core/addressmap';
import type { RecorderMap } from '../core/record/recorder';

export function recorderMapOf(map: AddressMap): RecorderMap {
  return {
    ecu: map.ecu,
    version: map.version,
    versionTag: map.versionTag,
    versionHash: map.versionHash,
    variant: map.variant,
    path: map.path,
    text: map.text,
    channels: map.channels.map((c) => ({
      name: c.name, label: c.label, units: c.units, kind: c.kind, decimals: c.decimals, min: c.min, max: c.max,
      addr: c.addr, length: c.length, verified: c.verified,
      convert: (raw: Uint8Array) => {
        try {
          return c.convert(raw);
        } catch {
          return null;
        }
      },
    })),
  };
}
