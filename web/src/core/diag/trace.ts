/**
 * A Transport wrapper that records every byte sent and received (port of
 * hondalog/diag/trace.py). The first-contact and probe reports show the
 * whole conversation with the ECU, so a failure on the real car can be read
 * off the report without running anything again.
 */

import type { Transport } from '../types';
import { fmtHex } from '../obd2/bytes';
import { REAL, usbLatencyS, type Clock } from '../obd2/clock';

export type TraceKind = 'TX' | 'RX' | 'BREAK' | 'BAUD' | 'NOTE';

export interface TraceEvent {
  t: number; // clock time, s
  kind: TraceKind;
  data: Uint8Array;
  text: string;
}

/** Pass-through to ``inner`` that logs TX, RX, break and baud changes. */
export class TracingTransport implements Transport {
  readonly kind: Transport['kind'];
  readonly label: string;
  readonly usb?: { vendorId: number; productId: number };
  readonly usbLatencyS: number;
  /** The baud rate last opened or set (Web Serial does not report it). */
  baud = 0;
  events: TraceEvent[] = [];

  constructor(readonly inner: Transport, readonly clock: Clock = REAL, readonly limit = 200_000) {
    this.kind = inner.kind;
    this.label = inner.label;
    this.usb = inner.usb;
    this.usbLatencyS = usbLatencyS(inner as Transport & { usbLatencyS?: number });
    const b = (inner as Transport & { baud?: number }).baud;
    if (typeof b === 'number') this.baud = b;
  }

  private log(kind: TraceKind, data: Uint8Array = new Uint8Array(0), text = ''): void {
    if (this.events.length < this.limit) this.events.push({ t: this.clock.now(), kind, data: Uint8Array.from(data), text });
  }

  note(text: string): void {
    this.log('NOTE', undefined, text);
  }

  async open(opts: { baud: number }): Promise<void> {
    await this.inner.open(opts);
    this.baud = opts.baud;
  }

  close(): Promise<void> {
    return this.inner.close();
  }

  async write(data: Uint8Array): Promise<void> {
    this.log('TX', data);
    await this.inner.write(data);
  }

  async read(max: number, timeoutMs: number): Promise<Uint8Array> {
    const data = await this.inner.read(max, timeoutMs);
    if (data.length) this.log('RX', data);
    return data;
  }

  drain(): Promise<void> {
    return this.inner.drain();
  }

  async setBaud(baud: number): Promise<void> {
    this.log('BAUD', undefined, String(baud));
    await this.inner.setBaud(baud);
    this.baud = baud;
  }

  async setBreak(on: boolean): Promise<void> {
    this.log('BREAK', undefined, on ? 'on (line low)' : 'off (line high)');
    await this.inner.setBreak(on);
  }

  // -- output --

  bytesSent(): number {
    return this.events.filter((e) => e.kind === 'TX').reduce((n, e) => n + e.data.length, 0);
  }

  bytesReceived(): number {
    return this.events.filter((e) => e.kind === 'RX').reduce((n, e) => n + e.data.length, 0);
  }

  /** One line per event, times in ms. RX chunks closer than ``mergeS`` are
   *  joined, so the echo and the reply each read as one line. */
  dump(t0?: number, mergeS = 0.003): string {
    if (!this.events.length) return '(nothing on the wire)\n';
    const base = t0 ?? this.events[0]!.t;
    const merged: TraceEvent[] = [];
    for (const e of this.events) {
      const last = merged[merged.length - 1];
      if (last && e.kind === 'RX' && last.kind === 'RX' && e.t - last.t <= mergeS) {
        const data = new Uint8Array(last.data.length + e.data.length);
        data.set(last.data);
        data.set(e.data, last.data.length);
        merged[merged.length - 1] = { t: e.t, kind: 'RX', data, text: '' };
        continue;
      }
      merged.push(e);
    }
    return merged
      .map((e) => `${(1000 * (e.t - base)).toFixed(1).padStart(10)}  ${e.kind.padEnd(5)} ${e.data.length ? fmtHex(e.data) : e.text}`)
      .join('\n') + '\n';
  }
}
