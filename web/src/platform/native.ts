/**
 * The standalone Android app (android/): the web build runs inside the app's
 * own WebView and talks to the phone through window.HondalogAndroid, a Java
 * object the app adds before the page loads. USB goes through a navigator.usb
 * shim the app injects (android/assets/hondalog-android.js), so the WebUSB
 * drivers here work unchanged. This file only adds what the shim cannot: the
 * file store in the app's own storage (not the WebView's OPFS, so drives
 * survive a WebView update and can be exported as a real file).
 */
import type { FileStore } from '../core/store/filestore';

/** The Java side (android/src/.../Bridge.java). Base64 strings, sync calls; a
 *  failure is returned as "\u0001" + message, never thrown. */
export interface NativeFiles {
  fileRead(name: string): string | null;
  fileList(): string;
  fileRemove(name: string): string;
  fileWriteBegin(name: string): string;
  fileWriteChunk(token: string, b64: string): string;
  fileWriteCommit(token: string): string;
  fileWriteAbort(token: string): string;
  appVersion(): string;
}

const ERR = '\u0001';

/** The value of a bridge call, or throw its error. */
export function checked<T extends string | null | undefined>(v: T): T {
  if (typeof v === 'string' && v.startsWith(ERR)) throw new Error(v.slice(1) || 'app storage error');
  return v;
}

function bridge(): NativeFiles | null {
  if (typeof window === 'undefined') return null;
  const b = (window as unknown as { HondalogAndroid?: NativeFiles }).HondalogAndroid;
  return b && typeof b.fileRead === 'function' ? b : null;
}

/** Running inside the standalone Android app. */
export function isNativeApp(): boolean {
  return bridge() !== null;
}

/** The app's version (versionName), or null outside the app. */
export function nativeAppVersion(): string | null {
  const b = bridge();
  try {
    return b ? b.appVersion() : null;
  } catch {
    return null;
  }
}

const CHUNK = 768 * 1024; // bytes per bridge call (a multiple of 3: no padding mid-stream)

export function toBase64(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    out += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000) as unknown as number[]);
  }
  return btoa(out);
}

export function fromBase64(b64: string): Uint8Array {
  const s = atob(b64);
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

function checkName(name: string): string {
  const parts = name.split('/').filter((p) => p.length > 0);
  if (parts.length === 0 || parts.some((p) => p === '.' || p === '..')) throw new Error(`bad file name: ${name}`);
  return parts.join('/');
}

/** Files in the app's private storage on the phone (written atomically: temp file, then rename). */
export class NativeFileStore implements FileStore {
  readonly description = 'App storage on this device';
  readonly persistent = true;

  constructor(private readonly b: NativeFiles = bridge()!) {
    if (!b) throw new Error('not running inside the hondalog app');
  }

  async read(name: string): Promise<Uint8Array | null> {
    const s = checked(this.b.fileRead(checkName(name)));
    return s === null || s === undefined ? null : fromBase64(s);
  }

  async write(name: string, bytes: Uint8Array): Promise<void> {
    const token = checked(this.b.fileWriteBegin(checkName(name)));
    try {
      for (let i = 0; i < bytes.length; i += CHUNK) checked(this.b.fileWriteChunk(token, toBase64(bytes.subarray(i, i + CHUNK))));
      checked(this.b.fileWriteCommit(token));
    } catch (e) {
      try {
        this.b.fileWriteAbort(token);
      } catch {
        /* already gone */
      }
      throw e;
    }
  }

  async list(): Promise<string[]> {
    return (JSON.parse(this.b.fileList()) as string[]).sort();
  }

  async remove(name: string): Promise<void> {
    checked(this.b.fileRemove(checkName(name)));
  }
}
