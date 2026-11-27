/**
 * Install firmware on the handheld (the CYD's ESP32-S3) with esptool-js: the merged image
 * (bootloader, partition table, app; ``idf.py merge_bin``) goes in at 0x0, the equivalent
 * of ``esptool --chip esp32s3 --baud 460800 write-flash -z 0x0 image.bin`` that
 * ``hondalog-handheld flash`` runs (hondalog/handheld/flasher.py).
 *
 * The chip is checked before anything is written: another chip is refused. 460800 baud,
 * and 115200 again from the start when the fast speed fails. Progress goes to a callback
 * (stage, percentage, bytes of the image written, total).
 *
 * esptool-js needs the raw serial device, not the byte stream: the platform supplies a
 * ``FlashDevice`` (a W3C ``SerialPort``, or a Tauri object that behaves like one: open,
 * close, readable, writable, setSignals, getInfo). The caller closes the CYD's normal
 * link before and opens it again after.
 */

import { md5 } from '@noble/hashes/legacy';
import { hex } from './bytes';

export const CHIP = 'esp32s3';
export const CHIP_NAME = 'ESP32-S3';
export const DEFAULT_BAUD = 460800;
export const FALLBACK_BAUD = 115200;
export const ROM_BAUD = 115200;
export const BAUDS = [115200, 230400, 460800, 921600];

export class FlashError extends Error {}

export interface FlashProgress {
  stage: string;
  /** null while there is none */
  pct: number | null;
  written: number;
  total: number;
  text: string;
  elapsed: number;
}

export function eta(p: FlashProgress): number | null {
  if (p.pct === null || p.pct <= 0 || p.pct >= 100 || p.elapsed < 2) return null;
  return (p.elapsed * (100 - p.pct)) / p.pct;
}

const progress = (stage: string, pct: number | null = null, written = 0, total = 0, text = '', elapsed = 0): FlashProgress =>
  ({ stage, pct, written, total, text, elapsed });

/** The image's bytes, if it looks like a merged image for this chip. */
export function checkImage(data: Uint8Array, name = 'image'): Uint8Array {
  if (data.length < 0x9000 || data[0] !== 0xe9) throw new FlashError(`${name} is not an ESP32 firmware image`);
  if (data[0x8000] !== 0xaa || data[0x8001] !== 0x50) {
    throw new FlashError(`${name} has no partition table at 0x8000: it is an app-only image. Use the merged image ` +
      '(scripts/build-firmware.sh handheld, or idf.py merge_bin), which is written at 0x0.');
  }
  if (data.length > 16 * 1024 * 1024) throw new FlashError(`${name} is ${Math.floor(data.length / 1048576)} MB: more than the board's flash`);
  return data;
}

// esptool 5: "Writing at 0x00010000 ━━━━ 35.1% 0:05 286.7kB/815.1kB"; esptool 4 and esptool-js:
// "Writing at 0x10000... (35 %)" / "(35%)".
const PROGRESS_RE = /Writing at 0x([0-9a-fA-F]+)/;
const PCT_RE = /(\d+(?:\.\d+)?)\s*%/;
const SIZES_RE = /(\d+(?:\.\d+)?)\s*(kB|KB|MB|GB|B)?\s*\/\s*(\d+(?:\.\d+)?)\s*(kB|KB|MB|GB|B|bytes)?/;
const UNITS: Record<string, number> = { B: 1, bytes: 1, kB: 1024, KB: 1024, MB: 1048576, GB: 1073741824 };
const STAGES: Array<[RegExp, string]> = [
  [/Connecting/, 'Connecting to the board'],
  [/Chip is|Chip type|Detecting chip/, 'Chip found'],
  [/Uploading stub|Running stub|Stub running/, 'Starting the flasher'],
  [/Changing baud ?rate|Changed\b/, 'Changing speed'],
  [/Configuring flash size|Flash will be erased|Flash params set/, 'Preparing the flash'],
  [/Compressed \d+ bytes/, 'Erasing and writing'],
  [/Hash of data verified/, 'Verified'],
  [/Hard resetting|Staying in the bootloader|Leaving/, 'Resetting the board'],
];
const FATAL_RE = /(A fatal error occurred: .*)/;

export interface ParseState { total?: number; fatal?: string; chip?: string }

/** One line of esptool's (or esptool-js's) output -> a progress update, or null. */
export function parseLine(line: string, state: ParseState): FlashProgress | null {
  line = line.trim();
  if (!line) return null;
  if (PROGRESS_RE.test(line)) {
    const pm = PCT_RE.exec(line);
    if (pm) {
      const pct = Math.min(100, parseFloat(pm[1]!));
      let total = state.total ?? 0; // the image's own size: esptool's byte counts are rounded
      let written = Math.trunc((total * pct) / 100);
      if (!total) {
        const sm = SIZES_RE.exec(line.slice(pm.index + pm[0].length));
        if (sm) {
          const u2 = UNITS[sm[4] || sm[2] || 'B'] ?? 1;
          const u1 = UNITS[sm[2] || sm[4] || 'B'] ?? 1;
          written = Math.trunc(parseFloat(sm[1]!) * u1);
          total = Math.trunc(parseFloat(sm[3]!) * u2);
          state.total = total;
        }
      }
      return progress('Writing firmware', pct, written, total, line);
    }
    return progress('Writing firmware', null, 0, state.total ?? 0, line);
  }
  const f = FATAL_RE.exec(line);
  if (f) {
    state.fatal = f[1];
    return progress('Failed', null, 0, 0, f[1]);
  }
  const c = /Chip is ([A-Za-z0-9-]+)/.exec(line) ?? /Chip type:\s+([A-Za-z0-9-]+)/.exec(line);
  if (c) state.chip = c[1];
  for (const [rx, stage] of STAGES) if (rx.test(line)) return progress(stage, null, 0, state.total ?? 0, line);
  return null;
}

// ---------------------------------------------------------------- the esptool-js adapter

/** What flash() uses of esptool-js's ESPLoader. */
export interface FlashLoader {
  chip: { CHIP_NAME: string };
  main(mode?: string): Promise<string>;
  writeFlash(options: {
    fileArray: { data: string; address: number }[]; flashSize: string; flashMode: string; flashFreq: string;
    eraseAll: boolean; compress: boolean; reportProgress?: (fileIndex: number, written: number, total: number) => void;
    calculateMD5Hash?: (image: string) => string;
  }): Promise<void>;
  hardReset(): Promise<void>;
}

export interface FlashTerminal { clean(): void; writeLine(data: string): void; write(data: string): void }

/** Makes a loader at a baud rate, and lets the port go afterwards. */
export type LoaderFactory = (baud: number, terminal: FlashTerminal) => Promise<{ loader: FlashLoader; disconnect(): Promise<void> }>;

/**
 * The raw serial device esptool-js drives (Web Serial's ``SerialPort``; the Tauri platform
 * provides an object with the same members). Typed loosely so this file needs no DOM types.
 */
export type FlashDevice = unknown;

/** The real thing: esptool-js over a FlashDevice (loaded on demand). */
export function esptoolLoaderFactory(device: FlashDevice): LoaderFactory {
  return async (baud, terminal) => {
    const esp = await import('esptool-js');
    const transport = new esp.Transport(device as any, false);
    const loader = new esp.ESPLoader({ transport, baudrate: baud, romBaudrate: ROM_BAUD, terminal });
    return {
      loader: loader as unknown as FlashLoader,
      disconnect: async () => {
        try {
          await transport.disconnect();
        } catch {
          /* already closed */
        }
      },
    };
  };
}

/** Uint8Array -> the binary string esptool-js takes. */
export function toBinaryString(data: Uint8Array): string {
  let out = '';
  for (let i = 0; i < data.length; i += 0x8000) out += String.fromCharCode(...data.subarray(i, i + 0x8000));
  return out;
}

function binaryStringBytes(s: string): Uint8Array {
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i) & 0xff;
  return out;
}

export interface FlashOptions {
  baud?: number;
  /** Try 115200 when the first speed fails (default true). */
  fallback?: boolean;
  address?: number;
  onProgress?: (p: FlashProgress) => void;
  onLine?: (line: string) => void;
  /** Checked between stages and blocks: true = stop. */
  cancelled?: () => boolean;
  now?: () => number;
}

/**
 * Write the merged image at ``address`` (0x0). Throws FlashError with something a person can act on.
 * Returns the loader's output lines.
 */
export async function flash(image: Uint8Array, makeLoader: LoaderFactory, opts: FlashOptions = {}): Promise<string[]> {
  const data = checkImage(image);
  const now = opts.now ?? (() => Date.now() / 1000);
  const emit = opts.onProgress ?? (() => undefined);
  const address = opts.address ?? 0;
  const first = opts.baud ?? DEFAULT_BAUD;
  const bauds = opts.fallback === false || first === FALLBACK_BAUD ? [first] : [first, FALLBACK_BAUD];
  const t0 = now();
  const lines: string[] = [];
  emit(progress('Starting esptool', null, 0, data.length));
  let lastError: unknown = null;
  for (let attempt = 0; attempt < bauds.length; attempt++) {
    const baud = bauds[attempt]!;
    const state: ParseState = { total: data.length };
    let partial = '';
    const handle = (text: string) => {
      lines.push(text);
      if (opts.onLine && !PROGRESS_RE.test(text)) opts.onLine(text.trimEnd());
      const p = parseLine(text, state);
      if (p !== null) {
        p.elapsed = now() - t0;
        emit(p);
      }
    };
    const terminal: FlashTerminal = {
      clean: () => undefined,
      write: (s) => {
        partial += s;
        const parts = partial.split(/\r\n|\r|\n/);
        partial = parts.pop() ?? '';
        for (const ln of parts) if (ln) handle(ln);
      },
      writeLine: (s) => {
        const text = partial + s;
        partial = '';
        for (const ln of text.split(/\r\n|\r|\n/)) if (ln) handle(ln);
      },
    };
    const cancelled = () => {
      if (opts.cancelled?.()) throw new FlashError('cancelled: the board may have a half-written image. Flash it again.');
    };
    let made: Awaited<ReturnType<LoaderFactory>> | null = null;
    let wrote = false;
    try {
      cancelled();
      made = await makeLoader(baud, terminal);
      const loader = made.loader;
      emit(progress('Connecting to the board', null, 0, data.length, `${baud} baud`, now() - t0));
      await loader.main('default_reset');
      const chip = loader.chip?.CHIP_NAME ?? state.chip ?? '';
      if (chip !== CHIP_NAME) {
        throw new FlashError(`This chip is ${chip || 'unknown'}, not ${CHIP_NAME}. This firmware is for the CYD's ` +
          `${CHIP_NAME}: nothing was written.`);
      }
      cancelled();
      wrote = true;
      await loader.writeFlash({
        fileArray: [{ data: toBinaryString(data), address }], flashSize: 'keep', flashMode: 'keep', flashFreq: 'keep',
        eraseAll: false, compress: true,
        reportProgress: (_i, sent, total) => {
          cancelled();
          const pct = total ? Math.min(100, (100 * sent) / total) : 0;
          emit(progress('Writing firmware', pct, Math.trunc((data.length * pct) / 100), data.length, '', now() - t0));
        },
        calculateMD5Hash: (img) => hex(md5(binaryStringBytes(img))),
      });
      emit(progress('Verified', 100, data.length, data.length, '', now() - t0));
      emit(progress('Resetting the board', null, data.length, data.length, '', now() - t0));
      await loader.hardReset();
      emit(progress('Done', 100, data.length, data.length, 'The board is running the new firmware.', now() - t0));
      return lines;
    } catch (e) {
      lastError = e;
      if (e instanceof FlashError) throw e; // wrong chip, cancelled: never tried again
      // a failure once writing began is not a speed problem the slow speed would fix only if it was the link:
      // try the fallback speed once (esptool's advice for a flaky cable) unless this was it
      if (attempt + 1 < bauds.length) {
        emit(progress('Changing speed', null, 0, data.length,
          `${baud} baud failed${wrote ? ' while writing' : ''}: trying ${bauds[attempt + 1]}`, now() - t0));
      }
    } finally {
      await made?.disconnect();
    }
  }
  throw friendly(lastError, lines);
}

function friendly(e: unknown, lines: string[]): FlashError {
  const msg = String((e as Error)?.message ?? e ?? '');
  const all = lines.join(' ') + ' ' + msg;
  if (/Failed to connect|No serial data received|Wrong boot mode|Couldn't sync|not in download mode/i.test(all)) {
    return new FlashError('the board did not enter its bootloader. Hold BOOT, tap RESET, release BOOT, and try again.' +
      (msg ? ` (${msg})` : ''));
  }
  if (/Could not open|Permission denied|Resource busy|busy|already open|NetworkError/i.test(all)) {
    return new FlashError('the serial port is in use or not allowed: close other programs that have it open. ' + msg);
  }
  return new FlashError(msg || 'esptool failed');
}
