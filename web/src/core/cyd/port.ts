/**
 * The CYD's serial port as the Python code saw it (serial_link.SerialPort): open,
 * close, set_baud, write, read(timeout) -> bytes. ``TransportPort`` puts that over the
 * shared ``Transport`` contract (Web Serial, Tauri); tests use a fake handheld that
 * implements ``CydPort`` directly on a virtual clock.
 *
 * Times are seconds, as in the Python.
 */

import type { Transport } from '../types';

export class CydError extends Error {}

/** The file is still being written by the handheld. */
export class CydBusy extends CydError {}

/** Monotonic seconds and a way to wait. Tests drive a ``VirtualClock``. */
export interface Clock {
  now(): number;
  sleep(seconds: number): Promise<void>;
}

export const realClock: Clock = {
  now: () => (typeof performance !== 'undefined' ? performance.now() : Date.now()) / 1000,
  sleep: (s) => new Promise((resolve) => setTimeout(resolve, Math.max(0, s * 1000))),
};

/** A clock that only moves when something waits on it (tests). */
export class VirtualClock implements Clock {
  t = 0;
  now(): number { return this.t; }
  async sleep(s: number): Promise<void> {
    this.t += Math.max(0, s);
    // a turn of the event loop, not just a microtask: a waiting loop must not starve everything else
    await new Promise<void>((resolve) => {
      const g = globalThis as { setImmediate?: (fn: () => void) => void };
      if (g.setImmediate) g.setImmediate(resolve);
      else setTimeout(resolve, 0);
    });
  }
  advance(s: number): void { this.t += Math.max(0, s); }
}

export interface CydPort {
  readonly name: string;
  /** The speed it is set to. */
  readonly baud: number;
  /** A speed named by the user (``cyd:COM22@460800``): never hunted. */
  readonly fixedBaud?: boolean;
  readonly usb?: { vendorId: number; productId: number };
  open(): Promise<void>;
  close(): Promise<void>;
  setBaud(baud: number): Promise<void>;
  write(data: Uint8Array): Promise<void>;
  /** What arrived within ``timeoutS`` (possibly nothing). Throws CydError if the port is gone. */
  read(timeoutS: number): Promise<Uint8Array>;
}

/** CH340 behind the CYD's USB-C socket. */
export const CH340_ID = { vendorId: 0x1a86, productId: 0x7523 } as const;

/**
 * ``CydPort`` over the shared ``Transport``. Opening must not reset the board: the
 * CH340's RTS and DTR drive EN and GPIO0, and RTS on with DTR off is a reset (the
 * platform's open() must leave both alone or raise both; see the porting notes).
 */
export class TransportPort implements CydPort {
  private isOpen = false;

  constructor(private readonly transport: Transport, public baud = 921600, readonly fixedBaud = false) {}

  get name(): string { return this.transport.label; }
  get usb() { return this.transport.usb; }

  async open(): Promise<void> {
    try {
      await this.transport.open({ baud: this.baud });
      this.isOpen = true;
    } catch (e) {
      throw new CydError(String((e as Error)?.message ?? e));
    }
  }

  async close(): Promise<void> {
    if (!this.isOpen) return;
    this.isOpen = false;
    try {
      await this.transport.close();
    } catch {
      /* gone already */
    }
  }

  async setBaud(baud: number): Promise<void> {
    this.baud = baud;
    if (!this.isOpen) return;
    try {
      await this.transport.setBaud(baud);
      await this.transport.drain();
    } catch (e) {
      throw new CydError(String((e as Error)?.message ?? e));
    }
  }

  async write(data: Uint8Array): Promise<void> {
    if (!this.isOpen) throw new CydError('not connected');
    try {
      await this.transport.write(data);
    } catch (e) {
      throw new CydError(String((e as Error)?.message ?? e));
    }
  }

  async read(timeoutS: number): Promise<Uint8Array> {
    if (!this.isOpen) throw new CydError('not connected');
    try {
      return await this.transport.read(8192, Math.max(0, timeoutS * 1000));
    } catch (e) {
      throw new CydError(String((e as Error)?.message ?? e));
    }
  }
}

/** ``cyd:COM22@460800`` -> { device: 'COM22', baud: 460800 }; ``cyd:`` -> { device: '' }. */
export function parsePortSpec(spec: string): { device: string; baud: number | null; sim: boolean } {
  const s = spec.startsWith('cyd:') ? spec.slice(4) : spec;
  if (s === 'sim' || s.startsWith('sim:')) return { device: s, baud: null, sim: true };
  const at = s.indexOf('@');
  const device = at < 0 ? s : s.slice(0, at);
  const baud = at < 0 ? null : parseInt(s.slice(at + 1), 10) || null;
  return { device, baud, sim: false };
}
