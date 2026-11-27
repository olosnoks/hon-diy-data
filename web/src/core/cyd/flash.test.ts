import { describe, expect, it } from 'vitest';
import { type FlashLoader, type FlashProgress, FlashError, type LoaderFactory, checkImage, eta, flash, parseLine, toBinaryString } from './flash';

function image(ok = true): Uint8Array {
  const data = new Uint8Array(0x9000).fill(0xff);
  data[0] = 0xe9;
  if (ok) {
    data[0x8000] = 0xaa;
    data[0x8001] = 0x50;
  }
  return data;
}

interface Script { chip?: string; failMain?: (baud: number) => string | null; failWrite?: string }

function fakeEsptool(script: Script = {}) {
  const calls: Array<{ baud: number; wrote: number; reset: boolean; disconnected: boolean }> = [];
  const factory: LoaderFactory = async (baud, terminal) => {
    const call = { baud, wrote: 0, reset: false, disconnected: false };
    calls.push(call);
    const loader: FlashLoader = {
      chip: { CHIP_NAME: script.chip ?? 'ESP32-S3' },
      async main() {
        terminal.writeLine('esptool.js');
        terminal.write('Connecting...');
        terminal.writeLine('');
        const err = script.failMain?.(baud);
        if (err) throw new Error(err);
        terminal.writeLine(`Chip is ${script.chip ?? 'ESP32-S3'}`);
        terminal.writeLine('Uploading stub...');
        terminal.writeLine('Changing baudrate to ' + baud);
        return script.chip ?? 'ESP32-S3';
      },
      async writeFlash(o) {
        const file = o.fileArray[0]!;
        const bytes = file.data.length;
        expect(file.address).toBe(0);
        expect(o.compress).toBe(true);
        expect([o.flashSize, o.flashMode, o.flashFreq]).toEqual(['keep', 'keep', 'keep']);
        terminal.writeLine(`Compressed ${bytes} bytes to 100...`);
        for (const sent of [0, 25, 50, 100]) {
          if (script.failWrite && sent === 50) throw new Error(script.failWrite);
          terminal.writeLine(`Writing at 0x${(sent * 100).toString(16)}... (${sent}%)`);
          o.reportProgress?.(0, sent, 100);
        }
        call.wrote = bytes;
        expect(o.calculateMD5Hash!(file.data)).toMatch(/^[0-9a-f]{32}$/);
      },
      async hardReset() {
        call.reset = true;
      },
    };
    return { loader, disconnect: async () => { call.disconnected = true; } };
  };
  return { factory, calls };
}

describe('flashing the handheld (test_hl_flasher)', () => {
  it('checks the image', () => {
    expect(checkImage(image()).length).toBe(0x9000);
    expect(() => checkImage(image(false))).toThrow(/no partition table/);
    expect(() => checkImage(new TextEncoder().encode('hello'))).toThrow(FlashError);
  });

  it('parses esptool 5, esptool 4 and esptool-js lines', () => {
    const st = { total: 1_000_000 };
    let p = parseLine('Writing at 0x00010000 [=====>    ] 35.1% 0:05 286.7kB/815.1kB', st)!;
    expect(p.stage).toBe('Writing firmware');
    expect(p.pct).toBeCloseTo(35.1, 2);
    expect(p.written).toBe(351000);
    p = parseLine('Writing at 0x00010000... (12 %)', { total: 100 })!;
    expect(p.pct).toBe(12);
    p = parseLine('Writing at 0x1f000... (40%)', { total: 100 })!;
    expect(p.pct).toBe(40);
    const s2: { total?: number } = {};
    p = parseLine('Writing at 0x00010000 ━━━━ 50.0% 0:05 400.0kB/800.0kB', s2)!;
    expect([p.written, p.total, s2.total]).toEqual([409600, 819200, 819200]);
    const s3: { fatal?: string } = {};
    p = parseLine('ERROR: A fatal error occurred: Failed to connect', s3)!;
    expect(p.stage).toBe('Failed');
    expect(s3.fatal).toBeTruthy();
    const s4: { chip?: string } = {};
    expect(parseLine('Chip is ESP32-S3 (QFN56)', s4)!.stage).toBe('Chip found');
    expect(s4.chip).toBe('ESP32-S3');
    expect(parseLine('Uploading stub...', {})!.stage).toBe('Starting the flasher');
    expect(parseLine('', {})).toBeNull();
    expect(parseLine('nothing to see', {})).toBeNull();
  });

  it('writes the merged image at 0x0, verifies, resets; progress in bytes of the image', async () => {
    const { factory, calls } = fakeEsptool();
    const seen: FlashProgress[] = [];
    const lines: string[] = [];
    let t = 0;
    await flash(image(), factory, { onProgress: (p) => seen.push(p), onLine: (l) => lines.push(l), now: () => (t += 1) });
    expect(calls).toEqual([{ baud: 460800, wrote: 0x9000, reset: true, disconnected: true }]);
    const writing = seen.filter((p) => p.stage === 'Writing firmware' && p.pct !== null);
    expect(writing[writing.length - 1]!.written).toBe(0x9000);
    expect(writing.some((p) => p.pct === 50 && p.written === 0x9000 / 2)).toBe(true);
    expect(seen[seen.length - 1]).toMatchObject({ stage: 'Done', pct: 100 });
    expect(seen.map((p) => p.stage)).toContain('Chip found');
    expect(lines).toContain('Chip is ESP32-S3');
    expect(lines.some((l) => l.startsWith('Writing at'))).toBe(false);
    expect(eta({ stage: '', pct: 25, written: 0, total: 0, text: '', elapsed: 10 })).toBe(30);
  });

  it('another chip is refused before anything is written', async () => {
    const { factory, calls } = fakeEsptool({ chip: 'ESP32' });
    await expect(flash(image(), factory)).rejects.toThrow(/not ESP32-S3.*nothing was written/);
    expect(calls.length).toBe(1);
    expect(calls[0]!.wrote).toBe(0);
    expect(calls[0]!.disconnected).toBe(true);
  });

  it('falls back to 115200 when the fast speed fails', async () => {
    const { factory, calls } = fakeEsptool({ failMain: (b) => (b === 460800 ? 'Timeout' : null) });
    const seen: FlashProgress[] = [];
    await flash(image(), factory, { onProgress: (p) => seen.push(p) });
    expect(calls.map((c) => c.baud)).toEqual([460800, 115200]);
    expect(calls[1]!.wrote).toBe(0x9000);
    expect(seen.some((p) => p.stage === 'Changing speed' && p.text.includes('115200'))).toBe(true);
  });

  it('a failure is said in words a person can act on', async () => {
    const boot = fakeEsptool({ failMain: () => 'Failed to connect with the device' });
    await expect(flash(image(), boot.factory)).rejects.toThrow(/Hold BOOT, tap RESET/);
    const busy = fakeEsptool({ failMain: () => 'Failed to execute open on SerialPort: The port is already open.' });
    await expect(flash(image(), busy.factory, { fallback: false })).rejects.toThrow(/in use/);
  });

  it('cancel stops it and says the image may be half written', async () => {
    const { factory, calls } = fakeEsptool();
    let n = 0;
    await expect(flash(image(), factory, { onProgress: (p) => { if (p.stage === 'Writing firmware') n++; }, cancelled: () => n >= 2 }))
      .rejects.toThrow(/cancelled/);
    expect(calls.length).toBe(1);
    expect(calls[0]!.disconnected).toBe(true);
  });

  it('binary strings for esptool-js', () => {
    expect(toBinaryString(Uint8Array.of(0, 0xff, 0x41))).toBe('\u0000ÿA');
  });
});
