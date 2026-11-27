import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { utf8 } from './bytes';
import { FakeCyd } from './fake_cyd';
import { HlClient } from './hl_client';
import { resolveMap } from './hhmap';
import { CydClient } from './link';
import { DEFAULT_CFG, readMap } from './mapfile';
import { pushMap, pushText, setTime, setTimeText } from './pushmap';

const FIX = join(dirname(fileURLToPath(import.meta.url)), '__fixtures__');
const TOML = readFileSync(join(FIX, 'addressmap.toml'), 'utf-8');
const P2T = new Uint8Array(readFileSync(join(FIX, 'p2t.map')));

describe('push-map and set-time over both sessions', () => {
  it('binary: the very bytes the Python builds, and the config', async () => {
    const fake = new FakeCyd({ gpsTime: false });
    const cyd = new CydClient(fake, { clock: fake.clock });
    await cyd.open();
    const r = await pushMap(cyd, resolveMap(TOML, 'P2T'), { cfg: utf8(DEFAULT_CFG + 'mystery = 2\n') });
    expect(fake.sd!.get('HANDHELD.MAP')).toEqual(P2T);
    expect(r.cfgSkipped).toBe(1);
    expect(pushText(r)).toBe(`HANDHELD.MAP: map #${r.mapHash} sent (${P2T.length} bytes); the handheld uses it from its next ` +
      "power-up\nHANDHELD.CFG sent; the handheld will skip 1 line(s) it does not know");
    expect(await setTime(cyd, 1_700_000_000_000)).toBe(true);
    expect(setTimeText(false)).toMatch(/GPS has already set/);
  });

  it('HL: the same file through HL PUT', async () => {
    const fake = new FakeCyd({ gpsTime: true });
    await fake.open();
    const hl = new HlClient(fake, { clock: fake.clock });
    await hl.connect();
    const r = await pushMap(hl, resolveMap(TOML, 'P2T'), { cfg: utf8(DEFAULT_CFG) });
    expect(readMap(fake.sd!.get('HANDHELD.MAP')!).mapHash).toBe(r.mapHash);
    expect(fake.sd!.get('HANDHELD.MAP')).toEqual(P2T);
    expect(r.cfgSkipped).toBe(0);
    expect(await setTime(hl)).toBe(false);
  });
});
