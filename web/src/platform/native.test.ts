import { afterEach, describe, expect, it } from 'vitest';
import { _resetPlatform, capabilities, getFileStore, platformName, serialPath } from './index';
import { fromBase64, isNativeApp, NativeFileStore, toBase64 } from './native';

/** The Java FileBridge, in memory: chunks land in a temp entry until commit. */
function fakeBridge() {
  const files = new Map<string, string>();
  const temps = new Map<string, { name: string; parts: string[] }>();
  let next = 0;
  const calls = { chunks: 0 };
  return {
    files,
    calls,
    fileRead: (n: string) => files.get(n) ?? null,
    fileList: () => JSON.stringify([...files.keys()]),
    fileRemove: (n: string) => { files.delete(n); return ''; },
    fileWriteBegin: (n: string) => { const t = `t${next++}`; temps.set(t, { name: n, parts: [] }); return t; },
    fileWriteChunk: (t: string, b64: string) => { calls.chunks++; temps.get(t)!.parts.push(b64); return ''; },
    fileWriteCommit: (t: string) => {
      const e = temps.get(t)!;
      const bytes = e.parts.map(fromBase64);
      const all = new Uint8Array(bytes.reduce((a, b) => a + b.length, 0));
      let o = 0;
      for (const b of bytes) { all.set(b, o); o += b.length; }
      files.set(e.name, toBase64(all));
      temps.delete(t);
      return '';
    },
    fileWriteAbort: (t: string) => { temps.delete(t); return ''; },
    appVersion: () => '0.2.0',
  };
}

afterEach(() => {
  delete (window as unknown as { HondalogAndroid?: unknown }).HondalogAndroid;
  _resetPlatform();
});

describe('native app file store', () => {
  it('base64 round-trips every byte value and large buffers', () => {
    const b = new Uint8Array(200_000);
    for (let i = 0; i < b.length; i++) b[i] = (i * 31 + 7) & 0xff;
    expect(fromBase64(toBase64(b))).toEqual(b);
    expect(toBase64(new Uint8Array(0))).toBe('');
  });

  it('writes in chunks, reads back, lists and removes', async () => {
    const fb = fakeBridge();
    const s = new NativeFileStore(fb);
    const big = new Uint8Array(1024 * 1024 + 5).map((_, i) => i & 0xff);
    await s.write('hondalog.db', big);
    await s.write('maps/p2p.toml', new TextEncoder().encode('x = 1'));
    expect(fb.calls.chunks).toBeGreaterThan(1);
    expect(await s.read('hondalog.db')).toEqual(big);
    expect(await s.read('missing.db')).toBeNull();
    expect(await s.list()).toEqual(['hondalog.db', 'maps/p2p.toml']);
    await s.remove('hondalog.db');
    expect(await s.list()).toEqual(['maps/p2p.toml']);
    await expect(s.write('../x', new Uint8Array(1))).rejects.toThrow(/bad file name/);
  });

  it('a Java-side failure becomes an Error and the temp write is aborted', async () => {
    const fb = fakeBridge();
    let aborted = 0;
    const s = new NativeFileStore({ ...fb, fileWriteCommit: () => '\u0001disk full', fileWriteAbort: () => { aborted++; return ''; } });
    await expect(s.write('hondalog.db', new Uint8Array(10))).rejects.toThrow('disk full');
    expect(aborted).toBe(1);
    const r = new NativeFileStore({ ...fb, fileRead: () => '\u0001bad file name: x' });
    await expect(r.read('x')).rejects.toThrow('bad file name');
  });

  it('an empty file still commits', async () => {
    const fb = fakeBridge();
    const s = new NativeFileStore(fb);
    await s.write('empty', new Uint8Array(0));
    expect(await s.read('empty')).toEqual(new Uint8Array(0));
  });

  it('the platform picks the app store and reports the app inside the Android app', () => {
    expect(isNativeApp()).toBe(false);
    (window as unknown as { HondalogAndroid: unknown }).HondalogAndroid = fakeBridge();
    _resetPlatform();
    expect(isNativeApp()).toBe(true);
    expect(getFileStore()).toBeInstanceOf(NativeFileStore);
    expect(platformName()).toBe('android-app');
    expect(capabilities().files).toBe('app');
    expect(capabilities().offline).toBe(true);
    // the app's USB bridge wins over Web Serial, even on a non-Android user agent
    Object.defineProperty(navigator, 'usb', { value: new EventTarget(), configurable: true });
    Object.defineProperty(navigator, 'serial', { value: new EventTarget(), configurable: true });
    _resetPlatform();
    expect(serialPath()).toBe('webusb');
    delete (navigator as unknown as { usb?: unknown }).usb;
    delete (navigator as unknown as { serial?: unknown }).serial;
  });
});
