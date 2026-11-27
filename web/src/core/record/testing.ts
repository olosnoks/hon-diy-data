/**
 * Test support: the shipped address map (P2P variant) as a RecorderMap,
 * decoded with the built-in formula compiler. Not used by the app.
 */

import { parse } from 'smol-toml';
import { compileValue } from './condition';
import type { RecorderChannel, RecorderMap } from './recorder';

export function recorderMapFromToml(text: string, variant?: string): RecorderMap {
  const doc = parse(text) as Record<string, any>;
  const v = variant ?? String(doc.ecu_variant ?? '');
  const channels: RecorderChannel[] = [];
  for (const raw of doc.channel as Record<string, any>[]) {
    let ch = { ...raw };
    const over = raw.variants?.[v];
    if (over) {
      if (over.absent) continue;
      ch = { ...ch, ...over, status: over.status ?? 'unverified' };
    }
    const formula = compileValue(String(ch.formula));
    const endian = ch.endian === 'little' ? 'little' : 'big';
    const flag = ch.kind === 'flag';
    channels.push({
      name: ch.name, label: ch.label ?? ch.name, units: ch.units ?? '', kind: flag ? 'flag' : 'value',
      decimals: flag ? 0 : ch.decimals ?? 2, min: flag ? 0 : Number(ch.min ?? 0), max: flag ? 1 : Number(ch.max ?? 100),
      addr: Number(ch.addr), length: Number(ch.length ?? 1), verified: String(ch.status ?? '').toLowerCase() === 'verified',
      convert(bytes: Uint8Array): number | null {
        let x = 0;
        const b = endian === 'big' ? Array.from(bytes) : Array.from(bytes).reverse();
        for (const byte of b) x = x * 256 + byte;
        return formula.eval({ x });
      },
    });
  }
  return { ecu: String(doc.ecu ?? ''), version: String(doc.version ?? ''), versionHash: '', variant: v, path: 'addressmap.toml', text, channels };
}
